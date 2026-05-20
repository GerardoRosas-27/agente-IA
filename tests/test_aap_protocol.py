# tests/test_aap_protocol.py
from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.aap.protocol import AAPApprovalStatus, AgentActionProtocol, RiskLevel
from harness.agent_loop import execute_tool_action


class TestAAPProtocol(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_aap.db"
        self.protocol = AgentActionProtocol(self.db_path)

    def tearDown(self) -> None:
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    def test_determine_risk_level(self) -> None:
        # GREEN
        read_action = {"action": "read", "path": "file.txt"}
        self.assertEqual(self.protocol.determine_risk_level(read_action), RiskLevel.GREEN)

        search_action = {"action": "search", "query": "hello"}
        self.assertEqual(self.protocol.determine_risk_level(search_action), RiskLevel.GREEN)

        patch_preview_action = {"action": "patch", "patch": "diff", "apply": False}
        self.assertEqual(self.protocol.determine_risk_level(patch_preview_action), RiskLevel.GREEN)

        # YELLOW
        patch_apply_action = {"action": "patch", "patch": "diff", "apply": True}
        self.assertEqual(self.protocol.determine_risk_level(patch_apply_action), RiskLevel.YELLOW)

        # RED
        command_action = {"action": "command", "command": "python -m pytest"}
        self.assertEqual(self.protocol.determine_risk_level(command_action), RiskLevel.RED)

    def test_approval_lifecycle(self) -> None:
        action = {"action": "command", "command": "rm -rf /"}
        token = self.protocol.request_approval(action)
        self.assertTrue(token.startswith("appr_"))

        # El estado inicial debe ser PENDING
        self.assertEqual(self.protocol.check_approval_status(token), AAPApprovalStatus.PENDING)

        # Aprobar
        success = self.protocol.approve_action(token)
        self.assertTrue(success)
        self.assertEqual(self.protocol.check_approval_status(token), AAPApprovalStatus.APPROVED)

    def test_denial_lifecycle(self) -> None:
        action = {"action": "command", "command": "rm -rf /"}
        token = self.protocol.request_approval(action)

        # Rechazar
        success = self.protocol.deny_action(token)
        self.assertTrue(success)
        self.assertEqual(self.protocol.check_approval_status(token), AAPApprovalStatus.DENIED)

    def test_approval_is_bound_to_exact_action(self) -> None:
        original = {"action": "command", "command": "echo approved"}
        different = {"action": "command", "command": "echo different"}
        token = self.protocol.request_approval(original)

        self.assertTrue(self.protocol.approval_matches_action(token, original))
        self.assertFalse(self.protocol.approval_matches_action(token, different))


class TestAAPAgentLoopIntegration(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        # Patch local STATE_DB_PATH de harness.aap.protocol para aislarlo en las pruebas de integración
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_aap_integration.db"
        
        from harness.aap import protocol
        self.original_db_path = protocol.aap_protocol.db_path
        protocol.aap_protocol.db_path = self.db_path
        protocol.aap_protocol.init_db()

    def tearDown(self) -> None:
        from harness.aap import protocol
        protocol.aap_protocol.db_path = self.original_db_path
        
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except Exception:
            pass

    async def test_execute_tool_action_interception_flow(self) -> None:
        from harness.aap.protocol import aap_protocol
        
        # 1. Ejecutar una acción GREEN sin problemas
        # Crear archivo temporal dentro de la raíz del repo
        temp_file_path = Path("temp_test_aap.txt")
        temp_file_path.write_text("contenido", encoding="utf-8")

        try:
            read_action = {"action": "read", "path": "temp_test_aap.txt"}
            obs = execute_tool_action(read_action)
            self.assertTrue(obs.ok)
            self.assertEqual(obs.content, "contenido")
        finally:
            if temp_file_path.is_file():
                temp_file_path.unlink()

        # 2. Ejecutar acción RED (command) sin token. Debe bloquearse y devolver un token de aprobación.
        command_action = {"action": "command", "command": "echo 'Hello'"}
        obs_blocked = execute_tool_action(command_action)
        self.assertFalse(obs_blocked.ok)
        self.assertIn("BLOQUEADO POR SEGURIDAD", obs_blocked.content)
        self.assertIn("Token de aprobación generado:", obs_blocked.content)

        # Extraer token de aprobación
        # El token tiene formato appr_xxxxxxxx
        parts = obs_blocked.content.split("Token de aprobación generado: ")
        token = parts[1].split(".")[0]
        self.assertTrue(token.startswith("appr_"))

        # 3. Reintentar con el token sin haberlo aprobado. Debe bloquearse con estado 'pending'.
        command_action_with_pending_token = {
            "action": "command",
            "command": "echo 'Hello'",
            "approval_token": token
        }
        obs_still_blocked = execute_tool_action(command_action_with_pending_token)
        self.assertFalse(obs_still_blocked.ok)
        self.assertIn("está en estado 'pending'", obs_still_blocked.content)

        # 4. Aprobar la acción usando el protocolo
        aap_protocol.approve_action(token)

        # 5. Reintentar con el token aprobado. Debe ejecutarse con éxito!
        obs_approved = execute_tool_action(command_action_with_pending_token)
        self.assertTrue(obs_approved.ok)
        self.assertIn("Hello", obs_approved.content)

        # 6. El mismo token no debe poder reutilizarse para otra acción RED distinta.
        mismatched_action = {
            "action": "command",
            "command": "echo 'Different'",
            "approval_token": token
        }
        obs_mismatch = execute_tool_action(mismatched_action)
        self.assertFalse(obs_mismatch.ok)
        self.assertIn("no corresponde a esta acción", obs_mismatch.content)
