import tempfile
import unittest
from pathlib import Path


class TestSharedExperienceReplay(unittest.TestCase):
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
            )

            ctx = replay.retrieval_context("mejorar sistema", agent_key="Revisor")

        self.assertTrue(ok)
        self.assertEqual(cycles, 1)
        self.assertIn("sistema mejorado", final)
        self.assertIn("Procedimental", ctx)


if __name__ == "__main__":
    unittest.main()
