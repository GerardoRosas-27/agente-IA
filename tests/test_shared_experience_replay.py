import tempfile
import unittest
from pathlib import Path


class TestSharedExperienceReplay(unittest.TestCase):
    def test_working_memory_pool_keeps_salient_context(self) -> None:
        from plastic_swarm_state import WorkingMemoryPool

        pool = WorkingMemoryPool(max_items=3)
        pool.add("Entiende", "objetivo claro")
        pool.add("Planifica", "plan importante", salience=0.9)
        pool.add("Ruido", "dato menor", salience=0.1)

        ctx = pool.context(limit=2)

        self.assertIn("Memoria de trabajo compartida", ctx)
        self.assertIn("Planifica", ctx)
        self.assertIn("Entiende", ctx)

    def test_records_retrieves_and_consolidates(self) -> None:
        from plastic_swarm_state import SharedExperienceReplay

        with tempfile.TemporaryDirectory() as tmp:
            replay = SharedExperienceReplay(
                Path(tmp) / "replay.sqlite",
                capacity=24,
                embed_dim=16,
            )
            replay.record_cycle(
                cycle=1,
                objective="optimizar memoria compartida",
                events=[
                    ("Planifica", "usar replay acotado y recuperar experiencias relevantes"),
                    ("Ejecuta1", "implementar tabla episodica y memoria procedimental"),
                    ("Revisor", "OBJETIVO_ALCANZADO: SI\nRESPUESTA_FINAL: listo"),
                ],
                reached=True,
            )

            ctx = replay.retrieval_context(
                "memoria compartida con replay acotado",
                agent_key="Ejecuta",
            )

        self.assertIn("Procedimental", ctx)
        self.assertIn("Episodica compartida", ctx)
        self.assertIn("Transactiva", ctx)
        self.assertIn("Ejecuta", ctx)

    def test_objective_pipeline_uses_replay(self) -> None:
        import torch

        from objective_agent_cycle import run_objective_pipeline
        from plastic_swarm_state import CycleBuffer, SharedExperienceReplay
        from tool_library import ToolLibrary
        from unified_fly_memory import SharedFlyMemory

        def fake_chat(_model, messages, _options):
            system = messages[0]["content"]
            if "INTERPRETE" in system:
                content = "OBJETIVO_CLARO: mejorar el sistema\nCRITERIO_1: responder claro"
            elif "REVISOR FINAL" in system:
                content = (
                    "OBJETIVO_ALCANZADO: SI\nMOTIVO: -\nRETROALIMENTACION: -\n"
                    "RESPUESTA_FINAL: sistema mejorado"
                )
            else:
                content = "paso util con memoria compartida"
            return {"message": {"content": content, "role": "assistant"}}

        with tempfile.TemporaryDirectory() as tmp:
            replay = SharedExperienceReplay(
                Path(tmp) / "replay.sqlite",
                capacity=24,
                embed_dim=16,
            )
            tool_library = ToolLibrary(Path(tmp) / "tools.sqlite", embed_dim=16)
            memory = SharedFlyMemory(
                n_slots=4,
                mem_dim=32,
                msg_dim=40,
                n_agents_max=8,
                writer_hidden=32,
                blank_init=True,
            ).to(torch.device("cpu"))

            final, cycles, _loss, ok = run_objective_pipeline(
                "mejorar sistema",
                memory,
                "fake",
                fake_chat,
                cycle_buffer=CycleBuffer(),
                experience_replay=replay,
                max_cycles=1,
                n_discuss=1,
                n_execute=1,
                n_test=1,
                internet_agent_enabled=False,
                python_test_agent_enabled=False,
                tool_library=tool_library,
            )

            ctx = replay.retrieval_context("mejorar sistema", agent_key="Revisor")

        self.assertTrue(ok)
        self.assertEqual(cycles, 1)
        self.assertIn("sistema mejorado", final)
        self.assertIn("Procedimental", ctx)

    def test_objective_pipeline_records_modular_agent_in_shared_replay(self) -> None:
        import json
        import torch

        from objective_agent_cycle import run_objective_pipeline
        from plastic_swarm_state import CycleBuffer, SharedExperienceReplay
        from skill_manager import SkillManager
        from tool_library import ToolLibrary
        from tool_registry import build_default_tool_registry
        from unified_fly_memory import SharedFlyMemory

        def fake_chat(_model, messages, _options):
            system = messages[0]["content"]
            if "INTERPRETE" in system:
                content = "OBJETIVO_CLARO: validar tests\nCRITERIO_1: usar agente especialista"
            elif "AgenteSkill:demo" in system:
                content = (
                    '{"aporta": true, "ejecutar": false, '
                    '"motivo": "aplica", "nota": "especialista demo aporto evidencia"}'
                )
            elif "REVISOR FINAL" in system:
                content = (
                    "OBJETIVO_ALCANZADO: SI\nMOTIVO: -\nRETROALIMENTACION: -\n"
                    "RESPUESTA_FINAL: completado"
                )
            else:
                content = "paso util"
            return {"message": {"content": content, "role": "assistant"}}

        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "description": "Agente demo para tests",
                        "risk": "low",
                        "status": "validated",
                        "agent_enabled": True,
                        "agent_role": "AgenteSkill:demo",
                        "triggers": ["tests", "validar"],
                        "instructions": "Aporta evidencia demo.",
                    }
                ),
                encoding="utf-8",
            )
            replay = SharedExperienceReplay(
                Path(tmp) / "replay.sqlite",
                capacity=24,
                embed_dim=16,
            )
            tool_library = ToolLibrary(Path(tmp) / "tools.sqlite", embed_dim=16)
            skill_manager = SkillManager(Path(tmp) / "skills")
            registry = build_default_tool_registry(
                search_fn=lambda *_args, **_kwargs: "",
                run_terminal_fn=lambda *_args, **_kwargs: "",
                tool_library=tool_library,
                project_root=tmp,
                skill_manager=skill_manager,
            )
            memory = SharedFlyMemory(
                n_slots=4,
                mem_dim=32,
                msg_dim=40,
                n_agents_max=8,
                writer_hidden=32,
                blank_init=True,
            ).to(torch.device("cpu"))

            final, _cycles, _loss, ok = run_objective_pipeline(
                "validar tests con agente demo",
                memory,
                "fake",
                fake_chat,
                cycle_buffer=CycleBuffer(),
                experience_replay=replay,
                max_cycles=1,
                n_discuss=1,
                n_execute=1,
                n_test=1,
                internet_agent_enabled=False,
                python_test_agent_enabled=False,
                node_test_agent_enabled=False,
                terminal_agent_enabled=False,
                tool_library=tool_library,
                skill_manager=skill_manager,
                tool_registry=registry,
            )
            ctx = replay.retrieval_context("especialista demo", agent_key="AgenteSkill:demo")

        self.assertTrue(ok)
        self.assertIn("completado", final)
        self.assertIn("AgenteSkill:demo", ctx)


if __name__ == "__main__":
    unittest.main()
