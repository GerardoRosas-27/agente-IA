"""Rutas raíz del repo para artefactos del harness."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURE_LIST_PATH = REPO_ROOT / "feature_list.json"
PROGRESS_DIR = REPO_ROOT / "progress"
DOCS_DIR = REPO_ROOT / "docs"
LOGS_DIR = REPO_ROOT / "logs"
AGENTS_MD = REPO_ROOT / "AGENTS.md"
CHECKPOINTS_MD = REPO_ROOT / "CHECKPOINTS.md"
