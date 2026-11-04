"""Canonical catalyst-group data model.

A recipe owns a list of catalyst alternatives::

    "catalystGroups": [
        {"catalysts": [...], "circuit": {...} | null},
        ...
    ]

The list may be empty.  Empty groups are not stored: zero groups is the
single representation of an unconditional recipe.  Within one group the
catalysts are required together (AND); separate groups are OR alternatives.
"""

from __future__ import annotations

from copy import deepcopy
from itertools import product


def _item_identity(item):
    return (item.get("key", ""), item.get("amount", 0))


def _dedupe_items(items):
    result = []
    seen = set()
    for item in items:
        identity = _item_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(deepcopy(item))
    return result


def _canonical_group(group):
    catalysts = _dedupe_items(group.get("catalysts", []) or [])
    catalysts.sort(key=lambda item: (item.get("key", ""), item.get("amount", 0)))
    circuit = deepcopy(group.get("circuit"))
    if not catalysts and not circuit:
        return None
    return {
        "catalysts": catalysts,
        "circuit": circuit,
    }


def group_signature(group):
    """Canonical identity: sorted catalyst keys + circuit key."""

    catalysts = tuple(sorted(item.get("key", "") for item in group.get("catalysts", [])))
    circuit = group.get("circuit") or {}
    return catalysts, circuit.get("key")


def concrete_catalyst_groups(group):
    """Expand one group's catalystChoices into concrete canonical groups."""

    fixed = list(group.get("catalysts", []) or [])
    choice_slots = []
    for choice in group.get("catalystChoices", []) or []:
        members = list(choice.get("members", []) or [])
        if not members:
            return []
        choice_slots.append(members)

    combinations = product(*choice_slots) if choice_slots else [()]
    result = []
    seen = set()
    for combination in combinations:
        candidate = _canonical_group(
            {
                "catalysts": fixed + list(combination),
                "circuit": group.get("circuit"),
            }
        )
        if candidate is None:
            continue
        signature = group_signature(candidate)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(candidate)
    return result


def normalize_catalyst_groups(groups):
    """Expand choices, remove empty groups and deduplicate alternatives."""

    normalized = []
    seen = set()
    for group in groups or []:
        for concrete in concrete_catalyst_groups(group):
            signature = group_signature(concrete)
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(concrete)
    return normalized


def normalize_recipe_catalyst_groups(recipe):
    """Return a copy with only canonical catalystGroups."""

    result = deepcopy(recipe)
    result["catalystGroups"] = normalize_catalyst_groups(
        result.get("catalystGroups", [])
    )
    return result


def effective_analysis_groups(recipe):
    """Groups used by graph analysis.

    A zero-group recipe is unconditional for conflict analysis and maps to one
    virtual empty group.  The recipe itself is never mutated.
    """

    groups = recipe.get("catalystGroups", [])
    if groups:
        return groups
    return [{"catalysts": [], "circuit": None}]
