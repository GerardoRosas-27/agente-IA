from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from harness.paths import DOCS_DIR


ARCHITECTURE_HINTS = (
    "harness/",
    "api_endpoints/",
    "services/",
    "docs/architecture",
    "llm_api_client",
    "skill_registry",
    "orchestrator",
    "verifier",
    "daemon",
    "mcp",
)


@dataclass(frozen=True)
class ADRResult:
    created: bool
    path: Path
    reason: str


def should_create_adr(changed_files: Iterable[str], implementation_text: str = "") -> bool:
    files = list(changed_files)
    text = implementation_text.casefold()
    if any(any(hint in path for hint in ARCHITECTURE_HINTS) for path in files):
        return True
    return any(token in text for token in ("arquitectura", "boundary", "protocolo", "daemon", "mcp", "workflow"))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80] or "decision"


def create_adr_for_change(
    *,
    title: str,
    changed_files: Iterable[str],
    implementation_text: str,
    docs_dir: Path = DOCS_DIR,
) -> ADRResult:
    """Crea un ADR breve si el cambio toca arquitectura o límites de sistema."""
    files = list(changed_files)
    if not should_create_adr(files, implementation_text):
        return ADRResult(False, docs_dir / "adr", "cambio sin señales arquitectónicas")

    adr_dir = docs_dir / "adr"
    adr_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    path = adr_dir / f"{stamp}-{_slug(title)}.md"
    body = "\n".join(
        [
            f"# ADR: {title}",
            "",
            f"Fecha: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Contexto",
            "El cambio toca módulos o límites arquitectónicos del harness y requiere trazabilidad de decisión.",
            "",
            "## Decisión",
            "Registrar explícitamente el flujo, los archivos afectados y la evidencia de verificación en disco.",
            "",
            "## Archivos Afectados",
            *(f"- `{path_item}`" for path_item in files),
            "",
            "## Consecuencias",
            "- Facilita revisión futura de cambios estructurales.",
            "- Permite relacionar decisiones con pruebas y eventos del harness.",
            "",
            "## Evidencia",
            implementation_text[:1200] or "(sin extracto de implementación)",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")
    return ADRResult(True, path, "ADR creado para cambio arquitectónico")
