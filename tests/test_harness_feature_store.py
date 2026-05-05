from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.feature_store import (
    load_feature_list,
    pick_next_pending,
    save_feature_list,
    set_feature_status,
    validate_feature_list,
)


class TestFeatureStore(unittest.TestCase):
    def test_atomic_save_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "feature_list.json"
            data = {
                "features": [
                    {"id": 1, "name": "a", "status": "pending"},
                ]
            }
            save_feature_list(p, data)
            back = load_feature_list(p)
            self.assertEqual(back["features"][0]["id"], 1)

    def test_validate_rejects_two_in_progress(self) -> None:
        data = {
            "features": [
                {"id": 1, "status": "in_progress"},
                {"id": 2, "status": "in_progress"},
            ]
        }
        errs = validate_feature_list(data)
        self.assertTrue(any("in_progress" in e for e in errs))

    def test_pick_next_pending_lowest_id(self) -> None:
        feats = [
            {"id": 3, "status": "done"},
            {"id": 2, "status": "pending"},
            {"id": 1, "status": "pending"},
        ]
        n = pick_next_pending(feats)
        self.assertIsNotNone(n)
        assert n is not None
        self.assertEqual(n["id"], 1)

    def test_set_feature_status(self) -> None:
        data = {"features": [{"id": 5, "status": "pending"}]}
        ok = set_feature_status(data, 5, "done")
        self.assertTrue(ok)
        self.assertEqual(data["features"][0]["status"], "done")


if __name__ == "__main__":
    unittest.main()
