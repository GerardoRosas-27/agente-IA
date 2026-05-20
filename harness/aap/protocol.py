# harness/aap/protocol.py
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

from harness.paths import STATE_DB_PATH


class RiskLevel(IntEnum):
    GREEN = 1   # Seguro: solo lectura o pruebas no destructivas
    YELLOW = 2  # Moderado: cambios de código locales reversibles
    RED = 3     # Crítico: ejecución bash, envíos externos o despliegues


class AAPApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class AgentActionProtocol:
    """Protocolo de Acción del Agente (AAP) para gestionar niveles de riesgo e intercepciones (HITL)."""

    def __init__(self, db_path: Path = STATE_DB_PATH):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Inicializa la tabla de aprobaciones para el consentimiento humano."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS aap_approvals (
                    token TEXT PRIMARY KEY,
                    action_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def determine_risk_level(self, action: dict[str, Any]) -> RiskLevel:
        """Clasifica el nivel de riesgo de una acción según su tipo y parámetros."""
        kind = str(action.get("action") or "").strip()
        
        # Acciones de solo lectura o reversibles
        if kind in ("read", "search", "search_class", "search_method", "search_callers", "test", "rollback", "done"):
            return RiskLevel.GREEN

        # Acciones que modifican código fuente pero son reversibles mediante checkpoint/git
        if kind == "patch":
            apply = bool(action.get("apply"))
            if apply:
                return RiskLevel.YELLOW
            return RiskLevel.GREEN  # Un preview es GREEN

        # Acciones potencialmente peligrosas (comandos de consola arbitrarios)
        if kind == "command":
            return RiskLevel.RED

        return RiskLevel.RED  # Por defecto por seguridad (falla-seguro)

    def _canonical_action_json(self, action: dict[str, Any]) -> str:
        """Normaliza una acción para comparar aprobaciones sin depender del orden JSON."""
        comparable = dict(action)
        comparable.pop("approval_token", None)
        return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def request_approval(self, action: dict[str, Any]) -> str:
        """Registra una solicitud de aprobación para una acción RED (Nivel 3). Devuelve el token generado."""
        self.init_db()
        token = f"appr_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat(timespec="seconds")
        action_json = self._canonical_action_json(action)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO aap_approvals (token, action_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token, action_json, AAPApprovalStatus.PENDING.value, now, now),
            )
        return token

    def check_approval_status(self, token: str) -> AAPApprovalStatus:
        """Verifica el estado actual de una aprobación por su token."""
        self.init_db()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM aap_approvals WHERE token = ?",
                (token,),
            ).fetchone()
            if row:
                return AAPApprovalStatus(row["status"])
            return AAPApprovalStatus.DENIED

    def approval_matches_action(self, token: str, action: dict[str, Any]) -> bool:
        """Verifica que el token haya sido emitido para esta acción exacta."""
        self.init_db()
        expected = self._canonical_action_json(action)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT action_json FROM aap_approvals WHERE token = ?",
                (token,),
            ).fetchone()
            return bool(row and row["action_json"] == expected)

    def approve_action(self, token: str) -> bool:
        """Aprueba manualmente una acción pendiente."""
        self.init_db()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE aap_approvals SET status = ?, updated_at = ? WHERE token = ? AND status = ?",
                (AAPApprovalStatus.APPROVED.value, now, token, AAPApprovalStatus.PENDING.value),
            )
            return cursor.rowcount > 0

    def deny_action(self, token: str) -> bool:
        """Rechaza manualmente una acción pendiente."""
        self.init_db()
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE aap_approvals SET status = ?, updated_at = ? WHERE token = ? AND status = ?",
                (AAPApprovalStatus.DENIED.value, now, token, AAPApprovalStatus.PENDING.value),
            )
            return cursor.rowcount > 0


# Instancia única del protocolo de acción
aap_protocol = AgentActionProtocol()
