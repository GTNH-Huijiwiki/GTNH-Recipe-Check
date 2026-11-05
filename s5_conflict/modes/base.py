"""发配模式接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from s0_contracts.schema import CatalystSet, Recipe


@dataclass(frozen=True)
class ConflictContext:
    """单个机器的冲突检查上下文。"""

    machine: str
    selected_sets: dict[str, CatalystSet]
    selected_recipes: tuple[Recipe, ...]
    normalized_recipes: tuple[Recipe, ...]
    max_conflicts: int | None = 100


class DispatchConflictMode(Protocol):
    """发配冲突检查模式接口。"""

    name: str

    def build(self, context: ConflictContext) -> dict:
        ...
