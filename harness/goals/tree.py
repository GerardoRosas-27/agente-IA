# harness/goals/tree.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class GoalStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    WAITING_APPROVAL = "waiting_approval"


@dataclass
class GoalNode:
    id: str
    description: str
    status: GoalStatus = GoalStatus.PENDING
    children: list[GoalNode] = field(default_factory=list)
    parent_id: str | None = None
    observation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_subgoal(self, subgoal_id: str, description: str, metadata: dict[str, Any] | None = None) -> GoalNode:
        """Agrega una sub-meta como hijo de este nodo."""
        child = GoalNode(
            id=subgoal_id,
            description=description,
            status=GoalStatus.PENDING,
            parent_id=self.id,
            metadata=metadata or {},
        )
        self.children.append(child)
        return child

    def find_node(self, node_id: str) -> GoalNode | None:
        """Busca recursivamente un nodo por su ID."""
        if self.id == node_id:
            return self
        for child in self.children:
            found = child.find_node(node_id)
            if found:
                return found
        return None

    def update_status(self, new_status: GoalStatus, observation: str = "") -> None:
        """Actualiza el estado del nodo actual y propaga o gestiona observaciones."""
        self.status = new_status
        if observation:
            self.observation = observation

    def to_dict(self) -> dict[str, Any]:
        """Serializa recursivamente el nodo a un diccionario."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "parent_id": self.parent_id,
            "observation": self.observation,
            "metadata": self.metadata,
            "children": [child.to_dict() for child in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalNode:
        """Deserializa recursivamente el nodo desde un diccionario."""
        children_data = data.get("children") or []
        node = cls(
            id=str(data["id"]),
            description=str(data["description"]),
            status=GoalStatus(data.get("status", "pending")),
            parent_id=data.get("parent_id"),
            observation=str(data.get("observation") or ""),
            metadata=dict(data.get("metadata") or {}),
        )
        for child_dict in children_data:
            child_node = cls.from_dict(child_dict)
            child_node.parent_id = node.id
            node.children.append(child_node)
        return node


class GoalTree:
    """Clase envoltorio para administrar un árbol de metas jerárquico completo."""
    def __init__(self, root: GoalNode):
        self.root = root

    def find_node(self, node_id: str) -> GoalNode | None:
        return self.root.find_node(node_id)

    def to_dict(self) -> dict[str, Any]:
        return self.root.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalTree:
        return cls(GoalNode.from_dict(data))

    def save_to_file(self, path: Path) -> None:
        """Guarda el árbol en un archivo JSON en disco."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_from_file(cls, path: Path) -> GoalTree:
        """Carga el árbol desde un archivo JSON en disco."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)
