"""Helpers for grouped input rules and catalyst-group normalization.

Input OR alternatives stay in 'inputGroups' while recipe rules run.
Catalyst alternatives are always expanded at the schema boundary so downstream
code only sees the canonical catalyst-group form::

    {"catalysts": [...], "circuit": {...} | null}

A recipe may own zero, one or many such groups.
"""

from __future__ import annotations

from copy import deepcopy

from s0_contracts.catalyst_group import (
    concrete_catalyst_groups as _concrete_catalyst_groups,
    group_signature as _catalyst_group_signature,
    normalize_recipe_catalyst_groups,
)


def concrete_catalyst_groups(group):
    """Return concrete canonical groups for one choice-bearing group."""

    return _concrete_catalyst_groups(group)


def expand_catalyst_choices(recipe):
    """Return a copy containing only canonical concrete catalyst groups."""

    return normalize_recipe_catalyst_groups(recipe)


def catalyst_group_signature(group):
    """Identity of a canonical catalyst group."""

    return _catalyst_group_signature(group)


def input_slot_count(recipe):
    """Number of material input slots, excluding catalyst groups."""

    return (
        len(recipe.get("inputItems", []) or [])
        + len(recipe.get("inputFluids", []) or [])
        + len(recipe.get("inputGroups", []) or [])
    )


def remove_matching_input_items(recipe, predicate):
    """Remove matching alternatives from ordinary input slots.

    A fixed matching item is removed.  In a grouped slot only matching members
    are removed.  If a slot has no alternatives left, no concrete recipe
    variant remains and ``None`` is returned.  Singleton groups are collapsed
    back to fixed inputs to keep the schema compact.
    """

    result = deepcopy(recipe)
    fixed = []
    for item in result.get("inputItems", []) or []:
        if predicate(item):
            # A fixed item is present in every concrete variant, so no valid
            # variant can survive a "contains" rule.
            return None
        fixed.append(item)

    groups = []
    for group in result.get("inputGroups", []) or []:
        if group.get("kind") != "item":
            groups.append(deepcopy(group))
            continue
        members = [
            deepcopy(member)
            for member in group.get("members", []) or []
            if not predicate(member)
        ]
        if not members:
            return None
        if len(members) == 1:
            fixed.append(members[0])
        else:
            updated = deepcopy(group)
            updated["members"] = members
            groups.append(updated)

    result["inputItems"] = fixed
    result["inputGroups"] = groups
    return result


def remove_variants_where_all_inputs_match(recipe, predicate):
    """Remove only concrete variants whose every ordinary input matches.

    Fluids are never matched by this helper.  If a fluid is present, no variant
    can have "all ordinary inputs" matching, so the recipe is returned
    unchanged.  When a group mixes matching and non-matching members, the
    matching alternatives are removed from that slot; this preserves the
    non-matching variants without deleting the whole grouped recipe.
    """

    result = deepcopy(recipe)
    if result.get("inputFluids"):
        return result

    fixed = result.get("inputItems", []) or []
    groups = result.get("inputGroups", []) or []
    if any(group.get("kind") != "item" for group in groups):
        return result
    if not fixed and not groups:
        return result

    # The all-matching combination exists only when every slot has at least
    # one matching alternative.  Otherwise no variant qualifies and pruning
    # would incorrectly discard valid non-matching input values.
    if any(not predicate(item) for item in fixed):
        return result
    if any(
        group.get("kind") == "item"
        and not any(predicate(member) for member in group.get("members", []) or [])
        for group in groups
    ):
        return result

    return remove_matching_input_items(result, predicate)