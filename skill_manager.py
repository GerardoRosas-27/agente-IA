"""
Gestor de skills/plugins declarativos.

Un skill es una carpeta en `skills/<nombre>/manifest.json` con metadatos,
permisos e instrucciones. Esta capa descubre skills y los ofrece al ciclo
agentico sin meter cada nueva capacidad directo al core.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str
    description: str
    risk: str
    status: str
    agent_enabled: bool
    agent_role: str
    triggers: list[str]
    permissions: list[str]
    entrypoint: str
    executor: str
    command: str
    cwd: str
    instructions: str
    path: str


class SkillManager:
    def __init__(self, skills_dir: str | Path | None = None) -> None:
        self.skills_dir = Path(skills_dir or Path(__file__).resolve().parent / "skills")
        self._cache_key: tuple[tuple[str, float], ...] = ()
        self._cache: list[SkillManifest] = []

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [part.strip() for part in value.split(",") if part.strip()]
        return []

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            t
            for t in re.findall(r"[a-záéíóúüñ0-9_./-]{3,}", text.lower())
            if t not in {"para", "como", "con", "que", "los", "las", "una", "uno", "por"}
        }

    @classmethod
    def _score(cls, query: str, skill: SkillManifest) -> float:
        q = cls._terms(query)
        text = "\n".join(
            [
                skill.name,
                skill.description,
                " ".join(skill.triggers),
                skill.entrypoint,
                skill.instructions,
            ]
        )
        d = cls._terms(text)
        if not q or not d:
            return 0.0
        overlap = len(q & d)
        if overlap <= 0:
            return 0.0
        return overlap / max(1.0, (len(q) * len(d)) ** 0.5)

    def load_skills(self) -> list[SkillManifest]:
        if not self.skills_dir.exists():
            return []
        manifest_paths = sorted(self.skills_dir.glob("*/manifest.json"))
        cache_key = tuple(
            (str(path), path.stat().st_mtime)
            for path in manifest_paths
            if path.exists()
        )
        if cache_key == self._cache_key:
            return list(self._cache)
        skills: list[SkillManifest] = []
        for manifest_path in manifest_paths:
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            name = str(data.get("name") or manifest_path.parent.name).strip()
            if not name:
                continue
            skills.append(
                SkillManifest(
                    name=name,
                    version=str(data.get("version") or "0.1.0").strip(),
                    description=str(data.get("description") or "").strip(),
                    risk=str(data.get("risk") or "low").strip().lower(),
                    status=str(data.get("status") or "experimental").strip().lower(),
                    agent_enabled=bool(data.get("agent_enabled", True)),
                    agent_role=str(data.get("agent_role") or f"AgenteSkill:{name}").strip(),
                    triggers=self._as_list(data.get("triggers")),
                    permissions=self._as_list(data.get("permissions")),
                    entrypoint=str(data.get("entrypoint") or "").strip(),
                    executor=str(data.get("executor") or "").strip(),
                    command=str(data.get("command") or "").strip(),
                    cwd=str(data.get("cwd") or ".").strip(),
                    instructions=str(data.get("instructions") or "").strip(),
                    path=str(manifest_path.parent),
                )
            )
        self._cache_key = cache_key
        self._cache = list(skills)
        return skills

    def invalidate_cache(self) -> None:
        self._cache_key = ()
        self._cache = []

    def repair_manifests(self) -> list[str]:
        """
        Normaliza manifests incompletos. No inventa ejecutores ni comandos;
        solo rellena metadatos seguros para que el skill pueda cargarse.
        """
        repaired: list[str] = []
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_dir in sorted(p for p in self.skills_dir.iterdir() if p.is_dir()):
            manifest_path = skill_dir / "manifest.json"
            data: dict[str, Any]
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError):
                data = {}
            before = json.dumps(data, sort_keys=True, ensure_ascii=False)
            name = str(data.get("name") or skill_dir.name).strip() or skill_dir.name
            risk = str(data.get("risk") or "low").strip().lower()
            if risk not in {"low", "moderate", "high", "dangerous"}:
                risk = "moderate"
            status = str(data.get("status") or "experimental").strip().lower()
            if status not in {"experimental", "validated", "deprecated", "disabled"}:
                status = "experimental"
            data.update(
                {
                    "name": self.slugify(name),
                    "version": str(data.get("version") or "0.1.0"),
                    "description": str(data.get("description") or f"Skill {name}").strip(),
                    "risk": risk,
                    "status": status,
                    "agent_enabled": bool(data.get("agent_enabled", True)),
                    "agent_role": str(data.get("agent_role") or f"AgenteSkill:{self.slugify(name)}"),
                    "triggers": self._as_list(data.get("triggers")),
                    "permissions": self._as_list(data.get("permissions")),
                    "entrypoint": str(data.get("entrypoint") or "").strip(),
                    "executor": str(data.get("executor") or "").strip(),
                    "command": str(data.get("command") or "").strip(),
                    "cwd": str(data.get("cwd") or ".").strip() or ".",
                    "instructions": str(data.get("instructions") or "").strip(),
                }
            )
            after = json.dumps(data, sort_keys=True, ensure_ascii=False)
            if before != after or not manifest_path.exists():
                manifest_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                repaired.append(str(manifest_path))
            readme = skill_dir / "README.md"
            if not readme.exists():
                readme.write_text(
                    f"# {data['name']}\n\n{data['description']}\n",
                    encoding="utf-8",
                )
                repaired.append(str(readme))
        if repaired:
            self.invalidate_cache()
        return repaired

    def search(self, query: str, *, limit: int = 5) -> list[tuple[float, SkillManifest]]:
        ranked = [(self._score(query, skill), skill) for skill in self.load_skills()]
        ranked = [item for item in ranked if item[0] > 0.0 or not query.strip()]
        ranked.sort(reverse=True, key=lambda item: item[0])
        return ranked[: max(1, int(limit))]

    def context(self, query: str, *, limit: int = 5, max_chars: int = 1800) -> str:
        matches = self.search(query, limit=limit)
        if not matches:
            return ""
        lines = ["Skills/plugins instalados:"]
        for score, skill in matches:
            lines.append(
                "- "
                f"{skill.name} v{skill.version} [{skill.status}, risk={skill.risk}] score={score:.2f}; "
                f"entrypoint={skill.entrypoint or 'manifest-only'}; "
                f"executor={skill.executor or 'context'}; "
                f"triggers={', '.join(skill.triggers) or '-'}; "
                f"permissions={', '.join(skill.permissions) or '-'}; "
                f"instrucciones={skill.instructions[:360]}"
            )
        return "\n".join(lines)[: max(300, int(max_chars))]

    @staticmethod
    def _tool_name(skill: SkillManifest) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", skill.name.strip().lower()).strip("-")
        return f"skill.{safe}"

    @staticmethod
    def slugify(name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(name).strip().lower()).strip("-")
        return safe or "learned-skill"

    @staticmethod
    def _agent_name(skill: SkillManifest) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", skill.agent_role.strip()).strip("-")
        return safe or f"AgenteSkill:{skill.name}"

    def register_executable_tools(
        self,
        registry: Any,
        *,
        run_terminal_fn: Callable[..., object],
        project_root: str,
    ) -> None:
        from tool_registry import RegisteredTool

        for skill in self.load_skills():
            if skill.executor != "terminal_command" or not skill.command:
                continue

            def _handler(
                *,
                _command: str = skill.command,
                _cwd: str = skill.cwd or ".",
            ) -> object:
                return run_terminal_fn(
                    _command,
                    project_root=project_root,
                    cwd=_cwd,
                    timeout=20.0,
                    max_output_chars=6000,
                )

            registry.register(
                RegisteredTool(
                    name=self._tool_name(skill),
                    description=skill.description or skill.instructions[:160],
                    risk=skill.risk if skill.risk in {"low", "moderate", "high", "dangerous"} else "high",
                    input_schema={},
                    handler=lambda _handler=_handler: _handler(),
                    default_enabled=skill.status != "disabled",
                    enabled_env="SKILL_MANAGER_ENABLED",
                )
            )

    def agent_specs(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for score, skill in self.search(query, limit=limit):
            if not skill.agent_enabled or skill.status == "disabled":
                continue
            specs.append(
                {
                    "name": self._agent_name(skill),
                    "skill_name": skill.name,
                    "tool_name": self._tool_name(skill),
                    "score": score,
                    "description": skill.description,
                    "risk": skill.risk,
                    "status": skill.status,
                    "executor": skill.executor,
                    "entrypoint": skill.entrypoint,
                    "instructions": skill.instructions,
                    "triggers": skill.triggers,
                    "permissions": skill.permissions,
                }
            )
        return specs

    def agents_context(self, query: str, *, limit: int = 6, max_chars: int = 1800) -> str:
        specs = self.agent_specs(query, limit=limit)
        if not specs:
            return ""
        lines = ["Agentes modulares instalados:"]
        for spec in specs:
            lines.append(
                "- "
                f"{spec['name']} gestiona skill={spec['skill_name']} "
                f"tool={spec['tool_name']} risk={spec['risk']} score={spec['score']:.2f}; "
                f"executor={spec['executor'] or 'context'}; "
                f"instrucciones={str(spec['instructions'])[:320]}"
            )
        return "\n".join(lines)[: max(300, int(max_chars))]

    def upsert_learned_skill(
        self,
        *,
        name: str,
        description: str,
        triggers: list[str],
        instructions: str,
        evidence: str,
        risk: str = "moderate",
        executor: str = "",
        command: str = "",
        cwd: str = ".",
        status: str = "experimental",
    ) -> Path:
        slug = self.slugify(name)
        skill_dir = self.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = skill_dir / "manifest.json"
        old: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                old = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                old = {}
        version = str(old.get("version") or "0.1.0")
        success_count = int(old.get("success_count") or 0) + 1
        failure_count = int(old.get("failure_count") or 0)
        confidence = min(1.0, float(old.get("confidence", 0.45)) + 0.08)
        merged_triggers = sorted(set(self._as_list(old.get("triggers")) + [t for t in triggers if t]))
        manifest = {
            "name": slug,
            "version": version,
            "description": description.strip()[:500],
            "risk": risk if risk in {"low", "moderate", "high", "dangerous"} else "moderate",
            "status": status,
            "agent_enabled": True,
            "agent_role": f"AgenteSkill:{slug}",
            "triggers": merged_triggers[:24],
            "permissions": self._as_list(old.get("permissions")),
            "entrypoint": command or old.get("entrypoint", ""),
            "executor": executor,
            "command": command,
            "cwd": cwd or ".",
            "instructions": instructions.strip()[:1800],
            "success_count": success_count,
            "failure_count": failure_count,
            "confidence": confidence,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.invalidate_cache()
        readme = skill_dir / "README.md"
        readme.write_text(
            "\n".join(
                [
                    f"# {slug}",
                    "",
                    f"Estado: {status} | Riesgo: {manifest['risk']} | Confianza: {confidence:.2f}",
                    "",
                    "## Cuándo usarlo",
                    description.strip() or "Skill aprendido por el sistema.",
                    "",
                    "## Instrucciones",
                    instructions.strip(),
                    "",
                    "## Evidencia",
                    evidence.strip()[:2000] or "Sin evidencia textual.",
                ]
            ),
            encoding="utf-8",
        )
        examples = skill_dir / "examples.json"
        previous = []
        if examples.exists():
            try:
                loaded = json.loads(examples.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    previous = loaded
            except (OSError, json.JSONDecodeError):
                previous = []
        previous.append({"triggers": merged_triggers[:8], "evidence": evidence.strip()[:800]})
        examples.write_text(
            json.dumps(previous[-20:], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return skill_dir


def parse_skill_learning_response(text: str) -> list[dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = data.get("skills", data.get("habilidades", []))
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", item.get("nombre", "")) or "").strip()
        instructions = str(item.get("instructions", item.get("instrucciones", "")) or "").strip()
        if not name or len(instructions) < 24:
            continue
        triggers = item.get("triggers", item.get("activadores", []))
        out.append(
            {
                "name": name,
                "description": str(item.get("description", item.get("descripcion", "")) or "").strip(),
                "triggers": SkillManager._as_list(triggers),
                "instructions": instructions,
                "evidence": str(item.get("evidence", item.get("evidencia", "")) or "").strip(),
                "risk": str(item.get("risk", "moderate") or "moderate").strip().lower(),
                "executor": str(item.get("executor", "") or "").strip(),
                "command": str(item.get("command", "") or "").strip(),
                "cwd": str(item.get("cwd", ".") or ".").strip(),
                "status": str(item.get("status", "experimental") or "experimental").strip().lower(),
            }
        )
    return out[:5]


def parse_skill_run_request(text: str) -> tuple[bool, str, str]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False, "", "JSON no valido"
    needed_raw = data.get("necesario", data.get("needed", False))
    needed = bool(needed_raw)
    if isinstance(needed_raw, str):
        needed = needed_raw.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    tool_name = str(data.get("tool_name", data.get("herramienta", "")) or "").strip()
    reason = str(data.get("motivo", data.get("reason", "")) or "").strip()[:500]
    return needed, tool_name, reason
