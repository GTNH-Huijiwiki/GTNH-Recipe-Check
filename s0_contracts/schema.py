"""规范化阶段的正式领域契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Union


Number = Union[int, float]

UNCONDITIONAL_SET_ID = "catalyst_set:unconditional"


def make_unconditional_set():
    """创建规范化无条件催化集。"""
    return CatalystSet(
        set_id=UNCONDITIONAL_SET_ID,
        catalysts=(),
        circuit=None,
        kind="unconditional",
    )


def make_catalyst_set_id(kind, catalysts, circuit):
    """根据催化集身份生成稳定 set_id。"""
    identity = {
        "kind": kind,
        "catalysts": sorted((item.kind, item.key) for item in catalysts),
        "circuit": (circuit.kind, circuit.key) if circuit else None,
    }
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"catalyst_set:{digest[:16]}"


@dataclass(frozen=True)
class MaterialRef:
    """配方中引用的一种具体物料。"""

    key: str
    kind: str
    amount: Number
    original_name: str = ""
    translated_name: str = ""

    @property
    def identity(self):
        return self.kind, self.key

    @classmethod
    def from_dict(cls, value):
        return cls(
            key=value.get("key", ""),
            kind=value.get("kind", "item"),
            amount=value.get("amount", 0),
            original_name=value.get("originalName", ""),
            translated_name=value.get("translatedName", ""),
        )

    def to_dict(self):
        return {
            "key": self.key,
            "kind": self.kind,
            "amount": self.amount,
            "originalName": self.original_name,
            "translatedName": self.translated_name,
        }


@dataclass(frozen=True)
class InputSlot:
    """一个输入槽，槽内候选取 OR。"""

    alternatives: tuple[MaterialRef, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(
            tuple(MaterialRef.from_dict(item) for item in value.get("alternatives", []))
        )

    def to_dict(self):
        return {"alternatives": [item.to_dict() for item in self.alternatives]}


@dataclass(frozen=True)
class InputGroup:
    """一个配方的全部输入槽，槽间取 AND。"""

    slots: tuple[InputSlot, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(tuple(InputSlot.from_dict(slot) for slot in value.get("slots", [])))

    def to_dict(self):
        return {"slots": [slot.to_dict() for slot in self.slots]}


@dataclass(frozen=True)
class CatalystSet:
    """一个催化条件，内部材料与电路取 AND。"""

    set_id: str
    catalysts: tuple[MaterialRef, ...]
    circuit: MaterialRef | None
    kind: str = "normal"

    def __post_init__(self):
        if self.kind == "normal" and not self.catalysts and not self.circuit:
            raise ValueError("普通催化集不能为空")

    @classmethod
    def from_dict(cls, value):
        circuit = value.get("circuit")
        return cls(
            set_id=value.get("setId", ""),
            catalysts=tuple(
                MaterialRef.from_dict(item) for item in value.get("catalysts", [])
            ),
            circuit=MaterialRef.from_dict(circuit) if circuit else None,
            kind=value.get("kind", "normal"),
        )

    def to_dict(self):
        return {
            "setId": self.set_id,
            "kind": self.kind,
            "catalysts": [item.to_dict() for item in self.catalysts],
            "circuit": self.circuit.to_dict() if self.circuit else None,
        }


@dataclass(frozen=True)
class CatalystGroup:
    """一个配方的全部催化集，集合间取 OR。"""

    sets: tuple[CatalystSet, ...]

    @classmethod
    def from_dict(cls, value):
        return cls(tuple(CatalystSet.from_dict(item) for item in value.get("sets", [])))

    def to_dict(self):
        return {"sets": [item.to_dict() for item in self.sets]}


@dataclass(frozen=True)
class OutputGroup:
    """配方的具体产物。"""

    items: tuple[MaterialRef, ...] = ()
    fluids: tuple[MaterialRef, ...] = ()

    @classmethod
    def from_dict(cls, value):
        return cls(
            items=tuple(
                MaterialRef.from_dict(item) for item in value.get("items", [])
            ),
            fluids=tuple(
                MaterialRef.from_dict(item) for item in value.get("fluids", [])
            ),
        )

    def to_dict(self):
        return {
            "items": [item.to_dict() for item in self.items],
            "fluids": [item.to_dict() for item in self.fluids],
        }


@dataclass(frozen=True)
class OrphanInfo:
    """配方输入的孤儿标记。"""

    has_orphan_input: bool = False
    only_orphan_inputs: bool = False
    orphan_items: tuple[str, ...] = ()
    orphan_fluids: tuple[str, ...] = ()
    mode: str = "contains"

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(
            has_orphan_input=bool(value.get("hasOrphanInput", False)),
            only_orphan_inputs=bool(value.get("onlyOrphanInputs", False)),
            orphan_items=tuple(value.get("orphanItems", [])),
            orphan_fluids=tuple(value.get("orphanFluids", [])),
            mode=value.get("mode", "contains"),
        )

    def to_dict(self):
        return {
            "hasOrphanInput": self.has_orphan_input,
            "onlyOrphanInputs": self.only_orphan_inputs,
            "orphanItems": list(self.orphan_items),
            "orphanFluids": list(self.orphan_fluids),
            "mode": self.mode,
        }


@dataclass(frozen=True)
class RecipeProvenance:
    """规范化配方的来源信息。"""

    source_file: str
    source: str = ""
    identifier: str = ""

    @classmethod
    def from_dict(cls, value):
        value = value or {}
        return cls(
            source_file=value.get("sourceFile", ""),
            source=value.get("source", ""),
            identifier=value.get("identifier", ""),
        )

    def to_dict(self):
        return {
            "sourceFile": self.source_file,
            "source": self.source,
            "identifier": self.identifier,
        }


@dataclass(frozen=True)
class Recipe:
    """规范化阶段的完整配方契约。"""

    recipe_id: str
    machine: str
    input_group: InputGroup
    catalyst_group: CatalystGroup
    output_group: OutputGroup
    orphan: OrphanInfo = field(default_factory=OrphanInfo)
    provenance: RecipeProvenance = field(
        default_factory=lambda: RecipeProvenance(source_file="")
    )
    diagnostics: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value):
        return cls(
            recipe_id=value.get("recipeId", ""),
            machine=value.get("machine", ""),
            input_group=InputGroup.from_dict(value.get("inputGroup", {})),
            catalyst_group=CatalystGroup.from_dict(
                value.get("catalystGroup", {})
            ),
            output_group=OutputGroup.from_dict(value.get("outputGroup", {})),
            orphan=OrphanInfo.from_dict(value.get("orphan", {})),
            provenance=RecipeProvenance.from_dict(value.get("provenance", {})),
            diagnostics=tuple(dict(item) for item in value.get("diagnostics", [])),
        )

    def to_dict(self):
        return {
            "recipeId": self.recipe_id,
            "machine": self.machine,
            "inputGroup": self.input_group.to_dict(),
            "catalystGroup": self.catalyst_group.to_dict(),
            "outputGroup": self.output_group.to_dict(),
            "orphan": self.orphan.to_dict(),
            "provenance": self.provenance.to_dict(),
            "diagnostics": [dict(item) for item in self.diagnostics],
        }
