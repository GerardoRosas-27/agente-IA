from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.feature_store import (
    USER_TASK_ORIGIN,
    blocked_by_dependencies,
    feature_dependencies_satisfied,
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
                    {"id": 1, "origin": USER_TASK_ORIGIN, "status": "in_progress"},
                    {"id": 2, "origin": USER_TASK_ORIGIN, "status": "in_progress"},
            ]
        }
        errs = validate_feature_list(data)
        self.assertTrue(any("in_progress" in e for e in errs))

    def test_validate_rejects_bad_ids_and_unknown_dependencies(self) -> None:
        data = {
            "features": [
                {"id": "bad", "origin": USER_TASK_ORIGIN, "status": "pending"},
                {"id": 2, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": [99]},
                {"id": 3, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": [3]},
                {"id": 3, "origin": USER_TASK_ORIGIN, "status": "done"},
            ]
        }

        errs = validate_feature_list(data)

        self.assertTrue(any("id inválido" in e for e in errs), errs)
        self.assertTrue(any("depends_on desconocido" in e for e in errs), errs)
        self.assertTrue(any("depende de sí misma" in e for e in errs), errs)
        self.assertTrue(any("id duplicado" in e for e in errs), errs)

    def test_pick_next_pending_lowest_id(self) -> None:
        feats = [
            {"id": 3, "status": "done"},
            {"id": 2, "origin": USER_TASK_ORIGIN, "status": "pending"},
            {"id": 1, "origin": USER_TASK_ORIGIN, "status": "pending"},
        ]
        n = pick_next_pending(feats)
        self.assertIsNotNone(n)
        assert n is not None
        self.assertEqual(n["id"], 1)

    def test_pick_next_pending_ignores_legacy_tasks(self) -> None:
        feats = [
            {"id": 1, "status": "pending"},
            {"id": 2, "origin": USER_TASK_ORIGIN, "status": "pending"},
        ]
        n = pick_next_pending(feats)
        self.assertIsNotNone(n)
        assert n is not None
        self.assertEqual(n["id"], 2)

    def test_set_feature_status(self) -> None:
        data = {"features": [{"id": "bad", "status": "pending"}, {"id": 5, "status": "pending"}]}
        ok = set_feature_status(data, 5, "done")
        self.assertTrue(ok)
        self.assertEqual(data["features"][1]["status"], "done")

    def test_pick_next_pending_respects_depends_on(self) -> None:
        """Una feature con depends_on no se elige hasta que sus deps estén done."""
        feats = [
            {"id": 1, "origin": USER_TASK_ORIGIN, "status": "pending"},
            {"id": 2, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": [1]},
            {"id": 3, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": 1},
        ]

        n = pick_next_pending(feats)

        assert n is not None
        self.assertEqual(n["id"], 1)
        self.assertFalse(feature_dependencies_satisfied(feats[1], all_features=feats))
        self.assertEqual([f["id"] for f in blocked_by_dependencies(feats)], [2, 3])

    def test_pick_next_pending_unblocks_after_dependency_done(self) -> None:
        feats = [
            {"id": 1, "origin": USER_TASK_ORIGIN, "status": "done"},
            {"id": 2, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": [1]},
        ]

        n = pick_next_pending(feats)

        assert n is not None
        self.assertEqual(n["id"], 2)
        self.assertEqual(blocked_by_dependencies(feats), [])

    def test_pick_next_pending_returns_none_when_all_blocked(self) -> None:
        feats = [
            {"id": 1, "origin": USER_TASK_ORIGIN, "status": "pending", "depends_on": [99]},
        ]
        self.assertIsNone(pick_next_pending(feats))


if __name__ == "__main__":
    unittest.main()
