"""流水线阶段公共契约。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class RunContext:
    """一次流水线运行共享的路径与版本上下文。"""
    version: str
    raw_dir: Path
    data_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class StageResult:
    """阶段执行完成后返回的产物引用与统计信息。"""
    stage: str
    outputs: tuple[Path, ...]
    stats: Mapping[str, Any]


class StageHandler(Protocol):
    """所有流水线阶段处理器必须实现的接口。"""
    name: str

    def run(self, context: RunContext) -> StageResult:
        ...
