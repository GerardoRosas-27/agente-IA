# tests/test_goals_tree.py
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.goals.tree import GoalNode, GoalStatus, GoalTree


class TestGoalsTree(unittest.TestCase):

    def test_node_creation_and_subgoals(self) -> None:
        root = GoalNode(id="root", description="Meta Principal")
        self.assertEqual(root.id, "root")
        self.assertEqual(root.description, "Meta Principal")
        self.assertEqual(root.status, GoalStatus.PENDING)
        self.assertEqual(len(root.children), 0)

        child = root.add_subgoal("child_1", "Sub-meta 1", metadata={"priority": "high"})
        self.assertEqual(len(root.children), 1)
        self.assertEqual(child.id, "child_1")
        self.assertEqual(child.parent_id, "root")
        self.assertEqual(child.metadata.get("priority"), "high")

    def test_find_node_recursive(self) -> None:
        root = GoalNode(id="root", description="Meta Principal")
        child_1 = root.add_subgoal("child_1", "Sub-meta 1")
        child_2 = child_1.add_subgoal("child_2", "Sub-meta 2")

        found_root = root.find_node("root")
        self.assertEqual(found_root, root)

        found_child_1 = root.find_node("child_1")
        self.assertEqual(found_child_1, child_1)

        found_child_2 = root.find_node("child_2")
        self.assertEqual(found_child_2, child_2)

        not_found = root.find_node("invalid_id")
        self.assertIsNone(not_found)

    def test_status_update(self) -> None:
        root = GoalNode(id="root", description="Meta")
        root.update_status(GoalStatus.IN_PROGRESS, "Comenzando")
        self.assertEqual(root.status, GoalStatus.IN_PROGRESS)
        self.assertEqual(root.observation, "Comenzando")

    def test_serialization_roundtrip(self) -> None:
        root = GoalNode(id="root", description="Meta Principal")
        child_1 = root.add_subgoal("child_1", "Sub-meta 1")
        child_1.add_subgoal("child_2", "Sub-meta 2")

        tree = GoalTree(root)
        tree_dict = tree.to_dict()

        # Re-construir desde dict
        tree_back = GoalTree.from_dict(tree_dict)
        self.assertEqual(tree_back.root.id, "root")
        self.assertEqual(len(tree_back.root.children), 1)
        self.assertEqual(tree_back.root.children[0].id, "child_1")
        self.assertEqual(tree_back.root.children[0].children[0].id, "child_2")

    def test_file_save_and_load(self) -> None:
        root = GoalNode(id="root", description="Meta")
        root.add_subgoal("c1", "Desc")
        tree = GoalTree(root)

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "goal_tree.json"
            tree.save_to_file(file_path)
            
            self.assertTrue(file_path.is_file())
            
            loaded_tree = GoalTree.load_from_file(file_path)
            self.assertEqual(loaded_tree.root.id, "root")
            self.assertEqual(loaded_tree.root.children[0].id, "c1")
