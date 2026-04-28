import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestAppConfig(unittest.TestCase):
    def test_load_config_uses_env_file_defaults_and_os_override(self) -> None:
        from app_config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "INTERNET_AGENT_ENABLED=0\nLLM_MODEL=file-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LLM_MODEL": "os-model"}, clear=False):
                cfg = load_config(root)

        self.assertEqual(cfg["INTERNET_AGENT_ENABLED"], "0")
        self.assertEqual(cfg["LLM_MODEL"], "os-model")
        self.assertIn("TERMINAL_AGENT_ENABLED", cfg)

    def test_save_config_preserves_comments_and_updates_known_keys(self) -> None:
        from app_config import load_config, save_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / ".env"
            env.write_text(
                "# comentario\n"
                "UNKNOWN_VALUE=keep\n"
                "# INTERNET_AGENT_ENABLED=1\n"
                "LLM_MODEL=old\n",
                encoding="utf-8",
            )
            cfg = load_config(root)
            cfg["INTERNET_AGENT_ENABLED"] = "0"
            cfg["LLM_MODEL"] = "new-model"
            cfg["SELF_IMPROVEMENT_AUTO_PROMOTE"] = "0"
            save_config(cfg, root)
            raw = env.read_text(encoding="utf-8")

        self.assertIn("# comentario", raw)
        self.assertIn("UNKNOWN_VALUE=keep", raw)
        self.assertIn("INTERNET_AGENT_ENABLED=0", raw)
        self.assertIn("LLM_MODEL=new-model", raw)
        self.assertIn("SELF_IMPROVEMENT_AUTO_PROMOTE=0", raw)
        self.assertEqual(os.environ["LLM_MODEL"], "new-model")


if __name__ == "__main__":
    unittest.main()
