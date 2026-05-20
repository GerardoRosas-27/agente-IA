# tests/test_daemon_service.py
from __future__ import annotations

import asyncio
import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.daemon.service import AgentDaemon
from harness.goals.tree import GoalStatus


class TestDaemonService(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_harness_state.db"
        self.daemon = AgentDaemon(self.db_path)

    def tearDown(self) -> None:
        # Asegurar recolección de basura para cerrar descriptores de archivos de sqlite3 residuales
        gc.collect()
        try:
            self.tmp_dir.cleanup()
        except PermissionError:
            # Reintentar tras un breve sleep en Windows si es necesario
            import time
            time.sleep(0.5)
            try:
                self.tmp_dir.cleanup()
            except Exception:
                pass

    def test_daemon_db_initialization(self) -> None:
        self.daemon.init_db()
        self.assertTrue(self.db_path.is_file())

        # Verificar tablas creadas
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='daemon_tasks'"
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_submit_task_creates_goal_tree(self) -> None:
        tid = self.daemon.submit_task("Implementar nueva feature")
        self.assertTrue(tid.startswith("task_"))

        # Recuperar de la base de datos
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM daemon_tasks WHERE id = ?", (tid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["goal"], "Implementar nueva feature")
            self.assertEqual(row["status"], GoalStatus.PENDING.value)
            
            # Verificar deserialización del árbol jerárquico
            tree_data = row["goal_tree_json"]
            self.assertIn("analizar", tree_data)
            self.assertIn("implementar", tree_data)
            self.assertIn("verificar", tree_data)
        finally:
            conn.close()

    def test_submit_task_generates_unique_ids(self) -> None:
        first = self.daemon.submit_task("Primera tarea")
        second = self.daemon.submit_task("Segunda tarea")

        self.assertNotEqual(first, second)

        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM daemon_tasks").fetchone()[0]
            self.assertEqual(count, 2)
        finally:
            conn.close()

    async def test_daemon_loop_processes_task(self) -> None:
        # Registrar una tarea
        tid = self.daemon.submit_task("Ejecutar tarea daemon de prueba")

        # Arrancar el daemon
        await self.daemon.start()
        self.assertTrue(self.daemon._is_running)

        # Esperar un momento corto para que el bucle procese la tarea
        await asyncio.sleep(2.0)

        # Detener el daemon
        await self.daemon.stop()
        self.assertFalse(self.daemon._is_running)

        # Verificar que el estado de la tarea ha pasado a COMPLETADA
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM daemon_tasks WHERE id = ?", (tid,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], GoalStatus.COMPLETED.value)
        finally:
            conn.close()
