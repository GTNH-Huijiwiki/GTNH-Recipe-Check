"""Canonical stage-2 domain contracts for serialized recipe data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class MaterialRef:
    key: str
    kind: str
    amount: int
    original_name: str = ""
    translated_name: str = ""

    @classmethod
    def from_dict(cls, value, kind):
        return cls(
            key=value.get("key", ""),
            kind=kind,
            amount=int(value.get("amount", 0)),
            original_name=value.get("originalName", ""),
            translated_name=value.get("translatedName", ""),
        )

    def to_dict(self):
        return {
            "key": self.key,
            "originalName": self.original_name,
            "translatedName": self.translated_name,
            "amount": self.amount,
        }


@dataclass(frozen=True)
class InputSlot:
    alternatives: tuple[MaterialRef, ...]


@dataclass(frozen=True)
class InputGroup:
    slots: tuple[InputSlot, ...]


@dataclass(frozen=True)
class CatalystSet:
    catalysts: tuple[MaterialRef, ...]
    circuit: MaterialRef | None
    kind: str = "normal"

    def __post_init__(self):
        if self.kind == "normal" and not self.catalysts and not self.circuit:
            raise ValueError("normal CatalystSet may not be empty")


@dataclass(frozen=True)
class CatalystGroup:
    sets: tuple[CatalystSet, ...]


@dataclass(frozen=True)
class OutputGroup:
    items: tuple[MaterialRef, ...] = ()
    fluids: tuple[MaterialRef, ...] = ()


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    machine: str
    input_group: InputGroup
    catalyst_group: CatalystGroup
    output_group: OutputGroup
    orphan: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value):
        fixed_slots = [
            InputSlot((MaterialRef.from_dict(item, "item"),))
            for item in value.get("inputItems", [])
        ]
        fluid_slots = [
            InputSlot((MaterialRef.from_dict(item, "fluid"),))
            for item in value.get("inputFluids", [])
        ]
        group_slots = [
            InputSlot(
                tuple(
                    MaterialRef.from_dict(member, group.get("kind", "item"))
                    for member in group.get("members", [])
                )
            )
            for group in value.get("inputGroups", [])
        ]
        catalyst_sets = []
        for group in value.get("catalystGroups", []):
            catalysts = tuple(
                MaterialRef.from_dict(item, "catalyst")
                for item in group.get("catalysts", [])
            )
            circuit_value = group.get("circuit")
            circuit = (
                MaterialRef.from_dict(circuit_value, "circuit")
                if circuit_value
                else None
            )
            if catalysts or circuit:
                catalyst_sets.append(CatalystSet(catalysts, circuit))
        return cls(
            recipe_id=value.get("recipeId", ""),
            machine=value.get("machine", ""),
            input_group=InputGroup(tuple(fixed_slots + fluid_slots + group_slots)),
            catalyst_group=CatalystGroup(tuple(catalyst_sets)),
            output_group=OutputGroup(
                tuple(
                    MaterialRef.from_dict(item, "item")
                    for item in value.get("outputItems", [])
                ),
                tuple(
                    MaterialRef.from_dict(item, "fluid")
                    for item in value.get("outputFluids", [])
                ),
            ),
            orphan=dict(value.get("orphan") or {}),
            metadata={
                key: value[key]
                for key in ("alerts", "needsReview")
                if key in value
            },
        )

    def to_dict(self):
        input_items = []
        input_fluids = []
        input_groups = []
        for slot in self.input_group.slots:
            if len(slot.alternatives) == 1:
                material = slot.alternatives[0]
                (input_items if material.kind == "item" else input_fluids).append(
                    material.to_dict()
                )
            elif slot.alternatives:
                input_groups.append(
                    {
                        "kind": slot.alternatives[0].kind,
                        "members": [item.to_dict() for item in slot.alternatives],
                    }
                )
        catalyst_groups = []
        for catalyst_set in self.catalyst_group.sets:
            catalyst_groups.append(
                {
                    "catalysts": [
                        item.to_dict() for item in catalyst_set.catalysts
                    ],
                    "circuit": (
                        catalyst_set.circuit.to_dict()
                        if catalyst_set.circuit
                        else None
                    ),
                }
            )
        result = {
            "inputItems": input_items,
            "inputFluids": input_fluids,
            "outputItems": [item.to_dict() for item in self.output_group.items],
            "outputFluids": [item.to_dict() for item in self.output_group.fluids],
            "catalystGroups": catalyst_groups,
            "inputGroups": input_groups,
            "orphan": dict(self.orphan),
        }
        result.update(self.metadata)
        if self.recipe_id:
            result["recipeId"] = self.recipe_id
        return result
