"""图着色策略接口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ColorContext:
    """单台机器的着色上下文。"""

    nodes: tuple[dict, ...]
    adjacency_matrix: tuple[tuple[int, ...], ...]
    node_coverages: tuple[frozenset, ...]
    rounds: int
    max_class_size: int | None
    size_sweep: bool
    sweep_max_size: int | None


class ColorStrategy(Protocol):
    """图着色策略接口。"""

    name: str

    def solve(self, context: ColorContext) -> dict:
        ...
