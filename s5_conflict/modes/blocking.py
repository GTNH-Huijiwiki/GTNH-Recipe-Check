"""阻挡发配模式的冲突图构建。"""

from __future__ import annotations

from collections import defaultdict

from s0_contracts.schema import CatalystSet, Recipe, make_unconditional_set
from s4_cover.relations import covers
from s5_conflict.modes.base import ConflictContext


def _set_condition_keys(catalyst_set: CatalystSet):
    keys = {item.key for item in catalyst_set.catalysts}
    if catalyst_set.circuit:
        keys.add(catalyst_set.circuit.key)
    return frozenset(keys)


def _base_slots(recipe: Recipe):
    return tuple(
        frozenset(item.key for item in slot.alternatives)
        for slot in recipe.input_group.slots
    )


def _supply_slots(recipe: Recipe, union_keys):
    slots = set(_base_slots(recipe))
    slots.update(frozenset((key,)) for key in union_keys)
    return tuple(sorted((slot for slot in slots if slot), key=lambda item: tuple(sorted(item))))


def _normalize_demand_slots(recipe: Recipe, union_keys):
    slots = set(_base_slots(recipe))
    slots.update(frozenset((key,)) for key in union_keys)
    if any(not slot for slot in slots):
        return None
    ordered = sorted(slots, key=lambda item: (len(item), tuple(sorted(item))))
    kept = []
    for slot in ordered:
        if any(existing <= slot for existing in kept):
            continue
        kept.append(slot)
    return tuple(kept)


def _slots_can_satisfy(supply_slots, demand_slots):
    if demand_slots is None or len(demand_slots) > len(supply_slots):
        return False
    if not demand_slots:
        return True

    adjacency = []
    for demand in demand_slots:
        options = [
            index
            for index, supply in enumerate(supply_slots)
            if demand & supply
        ]
        if not options:
            return False
        adjacency.append(options)

    matched_supply = [-1] * len(supply_slots)

    def augment(demand_index, visited):
        for supply_index in adjacency[demand_index]:
            if supply_index in visited:
                continue
            visited.add(supply_index)
            previous = matched_supply[supply_index]
            if previous == -1 or augment(previous, visited):
                matched_supply[supply_index] = demand_index
                return True
        return False

    for demand_index in sorted(range(len(adjacency)), key=lambda index: len(adjacency[index])):
        if not augment(demand_index, set()):
            return False
    return True


def _contains(source_recipe, target_recipe, union_keys):
    return _slots_can_satisfy(
        _supply_slots(source_recipe, union_keys),
        _normalize_demand_slots(target_recipe, union_keys),
    )


def _recipe_catalyst_sets(recipe):
    if recipe.catalyst_group.sets:
        return recipe.catalyst_group.sets
    return (make_unconditional_set(),)


def _membership(recipes, selected_sets):
    members = {set_id: [] for set_id in selected_sets}
    for recipe in recipes:
        matched_sets = set()
        for required in _recipe_catalyst_sets(recipe):
            for set_id, selected in selected_sets.items():
                if covers(selected, required):
                    matched_sets.add(set_id)
        for set_id in matched_sets:
            members[set_id].append(recipe)
    return members


def _key_bitsets(recipes):
    bitsets = defaultdict(int)
    for index, recipe in enumerate(recipes):
        bit = 1 << index
        for slot in _base_slots(recipe):
            for key in slot:
                bitsets[key] |= bit
    return bitsets


def _candidate_mask(target_recipe, union_keys, key_bitsets, source_count):
    mask = (1 << source_count) - 1
    demand_slots = _normalize_demand_slots(target_recipe, union_keys)
    if demand_slots is None:
        return 0
    for demand in demand_slots:
        if demand & union_keys:
            continue
        slot_mask = 0
        for key in demand:
            slot_mask |= key_bitsets.get(key, 0)
        mask &= slot_mask
    return mask


def _direction_conflicts(source_recipes, target_recipes, union_keys, key_bitsets, direction, limit):
    conflicts = []
    source_count = len(source_recipes)
    for target in target_recipes:
        mask = _candidate_mask(target, union_keys, key_bitsets, source_count)
        while mask:
            bit = mask & -mask
            source_index = bit.bit_length() - 1
            mask ^= bit
            source = source_recipes[source_index]
            if not _contains(source, target, union_keys):
                continue
            conflicts.append(
                {
                    "direction": direction,
                    "sourceRecipeId": source.recipe_id,
                    "targetRecipeId": target.recipe_id,
                }
            )
            if limit is not None and len(conflicts) >= limit:
                return conflicts
    return conflicts


def _self_conflicts(set_id, recipes, union_keys, limit):
    key_bitsets = _key_bitsets(recipes)
    directed = defaultdict(set)
    source_count = len(recipes)
    for target_index, target in enumerate(recipes):
        mask = _candidate_mask(target, union_keys, key_bitsets, source_count)
        while mask:
            bit = mask & -mask
            source_index = bit.bit_length() - 1
            mask ^= bit
            if source_index == target_index:
                continue
            source = recipes[source_index]
            if not _contains(source, target, union_keys):
                continue
            left_id, right_id = sorted((source.recipe_id, target.recipe_id))
            direction = (
                "left_to_right"
                if source.recipe_id == left_id
                else "right_to_left"
            )
            directed[(left_id, right_id)].add(direction)
            if limit is not None and len(directed) >= limit:
                break
        if limit is not None and len(directed) >= limit:
            break
    return [
        {
            "leftRecipeId": left_id,
            "rightRecipeId": right_id,
            "directions": sorted(directions),
        }
        for (left_id, right_id), directions in sorted(directed.items())
    ]


class BlockingMode:
    """按“配方条件包含”规则构建阻挡冲突图。"""

    name = "blocking"

    def build(self, context: ConflictContext):
        set_ids = sorted(context.selected_sets)
        members_s3 = _membership(context.selected_recipes, context.selected_sets)
        members_s2 = _membership(context.normalized_recipes, context.selected_sets)
        bitsets_s3 = {
            set_id: _key_bitsets(members_s3[set_id]) for set_id in set_ids
        }

        edges = []
        for index, left in enumerate(set_ids):
            for right in set_ids[index + 1:]:
                union_keys = _set_condition_keys(context.selected_sets[left]) | _set_condition_keys(
                    context.selected_sets[right]
                )
                conflicts = _direction_conflicts(
                    members_s3[left],
                    members_s2[right],
                    union_keys,
                    bitsets_s3[left],
                    "left_to_right",
                    context.max_conflicts,
                )
                remaining = (
                    None
                    if context.max_conflicts is None
                    else max(0, context.max_conflicts - len(conflicts))
                )
                if remaining is None or remaining > 0:
                    conflicts.extend(
                        _direction_conflicts(
                            members_s3[right],
                            members_s2[left],
                            union_keys,
                            bitsets_s3[right],
                            "right_to_left",
                            remaining,
                        )
                    )
                if conflicts:
                    edges.append(
                        {
                            "left": left,
                            "right": right,
                            "conflicts": conflicts,
                        }
                    )

        self_conflicts = []
        for set_id in set_ids:
            union_keys = _set_condition_keys(context.selected_sets[set_id])
            conflicts = _self_conflicts(
                set_id,
                members_s2[set_id],
                union_keys,
                context.max_conflicts,
            )
            if conflicts:
                self_conflicts.append({"setId": set_id, "conflicts": conflicts})

        return {
            "machine": context.machine,
            "nodes": set_ids,
            "edges": edges,
            "selfConflicts": self_conflicts,
        }
