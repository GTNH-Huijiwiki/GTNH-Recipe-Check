"""Catalyst relationship partitioning and minimum set cover."""

import logging
from copy import deepcopy

from s4_cover.catalyst_relations import (
    RELATION_SCHEMA,
    classify_relation,
    pure_containment_chains,
)
from s2_normalize.grouped_schema import concrete_catalyst_groups, expand_catalyst_choices
from s0_contracts.recipe import group_label, group_signature, item_signature
from s4_cover.set_cover import minimum_set_cover

logger = logging.getLogger(__name__)


def is_empty_group(group):
    """Whether a group is unconditional (no catalysts, choices or circuit)."""

    return (
        not group.get("catalysts")
        and not group.get("catalystChoices")
        and not group.get("circuit")
    )


def _catalyst_key_set(items):
    """Catalyst identity set; quantities and display names do not matter."""

    return {item.get("key", "") for item in items if item.get("key")}


def _concrete_groups(group):
    concrete = list(concrete_catalyst_groups(group))
    if concrete:
        return concrete
    # A legacy group without catalystChoices is already concrete.
    if not group.get("catalystChoices"):
        return [group]
    return []


def _concrete_group_can_trigger(candidate, required):
    if is_empty_group(required):
        return is_empty_group(candidate)

    candidate_circuit = candidate.get("circuit") or {}
    required_circuit = required.get("circuit") or {}
    if item_signature(candidate_circuit) != item_signature(required_circuit):
        return False

    candidate_catalysts = _catalyst_key_set(candidate.get("catalysts", []))
    required_catalysts = _catalyst_key_set(required.get("catalysts", []))
    return required_catalysts.issubset(candidate_catalysts)


def group_can_trigger(candidate, required):
    """Whether triggering candidate also triggers a required catalyst group.

    A group with several catalysts is treated as one complete trigger set.  A
    grouped choice is expanded into concrete alternatives first; the candidate
    only needs to trigger one concrete alternative of the required group.
    """

    for candidate_variant in _concrete_groups(candidate):
        for required_variant in _concrete_groups(required):
            if _concrete_group_can_trigger(candidate_variant, required_variant):
                return True
    return False


class DisjointSet:
    def __init__(self, size):
        self.parents = list(range(size))

    def find(self, value):
        while self.parents[value] != value:
            self.parents[value] = self.parents[self.parents[value]]
            value = self.parents[value]
        return value

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def _empty_metadata(machine_name):
    return {
        "machine": machine_name,
        "components": [],
        "selected_groups": [],
        "relationship_component_count": 0,
        "active_recipe_count": 0,
        "unique_group_count": 0,
        "relation_schema": RELATION_SCHEMA,
        "group_nodes": [],
        "equivalence_classes": [],
        "relations": [],
        "relation_counts": {},
        "pure_containment_chains": [],
        "set_cover": {
            "optimal": True,
            "candidate_count": 0,
            "selected_count": 0,
            "search_nodes": 0,
        },
    }


def reduce_catalyst_groups(machine_name, recipes):
    """Partition catalyst relations, then select a minimum group cover.

    The relation partition is retained intentionally: coverage is split into
    connected components, equivalent coverage classes become abstract catalyst
    groups, and groups dominated by a strict superset are discarded.  The
    remaining top-level candidates are then solved exactly as a minimum
    cardinality set cover.  A multi-catalyst group is one complete candidate and
    therefore covers every required catalyst subset as expected.
    """

    expanded_recipes = [
        expand_catalyst_choices(recipe) for recipe in recipes
    ]
    groups = []
    group_indexes = {}
    recipe_groups = []

    for recipe in expanded_recipes:
        current_groups = []
        for group in recipe.get("catalystGroups", []):
            signature = group_signature(group)
            if signature not in group_indexes:
                group_indexes[signature] = len(groups)
                groups.append(deepcopy(group))
            current_groups.append(group_indexes[signature])
        recipe_groups.append(current_groups)

    active_recipe_indexes = {
        recipe_index
        for recipe_index, current_groups in enumerate(recipe_groups)
        if current_groups
    }
    if not groups:
        logger.debug("machine %s has no reducible catalyst groups", machine_name)
        metadata = _empty_metadata(machine_name)
        return expanded_recipes, metadata

    coverage = []
    for candidate in groups:
        coverage.append(
            {
                recipe_index
                for recipe_index, current_groups in enumerate(recipe_groups)
                if any(
                    group_can_trigger(candidate, groups[group_index])
                    for group_index in current_groups
                )
            }
        )

    relation = DisjointSet(len(groups))
    for left in range(len(groups)):
        for right in range(left + 1, len(groups)):
            if coverage[left] & coverage[right]:
                relation.union(left, right)

    components = {}
    for group_index in range(len(groups)):
        components.setdefault(relation.find(group_index), []).append(group_index)

    reduced_recipes = deepcopy(expanded_recipes)
    metadata = _empty_metadata(machine_name)
    metadata["relationship_component_count"] = len(components)
    metadata["active_recipe_count"] = len(active_recipe_indexes)
    metadata["unique_group_count"] = len(groups)

    selected_groups = []
    abstract_index = 1
    class_index = 1
    total_candidates = 0
    total_search_nodes = 0

    component_values = list(components.values())
    for component_number, component_indexes in enumerate(
        component_values, start=1
    ):
        component_id = f"c{component_number:04d}"
        equivalence_classes = {}
        for group_index in component_indexes:
            equivalence_classes.setdefault(
                frozenset(coverage[group_index]), []
            ).append(group_index)

        class_entries = []
        for class_indexes in equivalence_classes.values():
            representative = class_indexes[0]
            candidate_group = deepcopy(groups[representative])
            abstract_replacement = None
            if len(class_indexes) > 1:
                abstract_key = f"__abstract_catalyst_{machine_name}_{abstract_index}"
                abstract_name = f"Abstract Catalyst ({machine_name} #{abstract_index})"
                abstract_index += 1
                abstract_replacement = {
                    "key": abstract_key,
                    "originalName": abstract_key,
                    "translatedName": abstract_name,
                    "amount": 1,
                    "members": [deepcopy(groups[index]) for index in class_indexes],
                }
                candidate_group["catalysts"] = [
                    {
                        "key": abstract_key,
                        "originalName": abstract_key,
                        "translatedName": abstract_name,
                        "amount": 1,
                    }
                ]
                logger.debug(
                    "machine %s equivalent catalyst groups %d -> %s",
                    machine_name,
                    len(class_indexes),
                    abstract_name,
                )
            class_entries.append(
                {
                    "class_id": f"ec{class_index:04d}",
                    "indexes": class_indexes,
                    "coverage": set().union(
                        *(coverage[index] for index in class_indexes)
                    ),
                    "group": candidate_group,
                    "abstract_replacement": abstract_replacement,
                }
            )
            class_index += 1

        top_level = [
            entry
            for entry in class_entries
            if not any(entry["coverage"] < other["coverage"] for other in class_entries)
        ]
        all_top_level = list(top_level)
        component_universe = set().union(
            *(entry["coverage"] for entry in top_level)
        )
        keyed_entries = {
            index: entry for index, entry in enumerate(top_level)
        }
        selected_keys, stats = minimum_set_cover(
            component_universe,
            [
                {"key": index, "coverage": entry["coverage"]}
                for index, entry in keyed_entries.items()
            ],
        )
        total_candidates += stats["candidate_count"]
        total_search_nodes += stats["search_nodes"]
        component_selected = [keyed_entries[index] for index in selected_keys]
        selected_class_ids = {
            entry["class_id"] for entry in component_selected
        }
        top_level_ids = {entry["class_id"] for entry in top_level}

        for entry in class_entries:
            for group_index in entry["indexes"]:
                metadata["group_nodes"].append(
                    {
                        "id": f"g{group_index:04d}",
                        "component": component_id,
                        "group": deepcopy(groups[group_index]),
                        "coverage_size": len(coverage[group_index]),
                        "equivalence_class": entry["class_id"],
                    }
                )

        for entry in class_entries:
            metadata["equivalence_classes"].append(
                {
                    "id": entry["class_id"],
                    "component": component_id,
                    "member_group_ids": [
                        f"g{index:04d}" for index in entry["indexes"]
                    ],
                    "member_labels": [
                        group_label(groups[index]) for index in entry["indexes"]
                    ],
                    "member_count": len(entry["indexes"]),
                    "member_relation": (
                        "equivalent"
                        if len(entry["indexes"]) > 1
                        else None
                    ),
                    "coverage_size": len(entry["coverage"]),
                    "coverage": sorted(entry["coverage"]),
                    "top_level": entry["class_id"] in top_level_ids,
                    "selected": entry["class_id"] in selected_class_ids,
                    "abstract_replacement": deepcopy(
                        entry["abstract_replacement"]
                    ),
                }
            )

        for left_index, left in enumerate(class_entries):
            for right in class_entries[left_index + 1:]:
                relation_type = classify_relation(
                    left["coverage"], right["coverage"]
                )
                metadata["relations"].append(
                    {
                        "left": left["class_id"],
                        "right": right["class_id"],
                        "relation": relation_type,
                        "left_coverage_size": len(left["coverage"]),
                        "right_coverage_size": len(right["coverage"]),
                        "intersection_size": len(
                            left["coverage"] & right["coverage"]
                        ),
                    }
                )

        for entry in component_selected:
            selected_groups.append(entry)
            logger.debug(
                "machine %s selected catalyst group %s covering %d recipes",
                machine_name,
                group_label(entry["group"]),
                len(entry["coverage"]),
            )

        metadata["components"].append(
            {
                "id": component_id,
                "groups": [deepcopy(groups[index]) for index in component_indexes],
                "equivalence_class_ids": [
                    entry["class_id"] for entry in class_entries
                ],
                "top_level_candidates": [
                    deepcopy(entry["group"]) for entry in all_top_level
                ],
                "equivalent_groups": [
                    {
                        "members": [
                            deepcopy(groups[index]) for index in entry["indexes"]
                        ],
                        "replacement": deepcopy(entry["abstract_replacement"]),
                    }
                    for entry in class_entries
                    if len(entry["indexes"]) > 1
                ],
                "set_cover": deepcopy(stats),
            }
        )

    selected_groups.sort(key=lambda entry: group_label(entry["group"]))
    metadata["selected_groups"] = [
        {
            "group": deepcopy(entry["group"]),
            "coverage": sorted(entry["coverage"]),
            "class_id": entry["class_id"],
        }
        for entry in selected_groups
    ]
    relation_counts = {}
    for relation in metadata["relations"]:
        relation_type = relation["relation"]
        relation_counts[relation_type] = relation_counts.get(relation_type, 0) + 1
    metadata["relation_counts"] = relation_counts
    metadata["pure_containment_chains"] = pure_containment_chains(metadata)
    metadata["set_cover"] = {
        "optimal": True,
        "candidate_count": total_candidates,
        "selected_count": len(selected_groups),
        "search_nodes": total_search_nodes,
    }

    for recipe_index, recipe in enumerate(reduced_recipes):
        recipe["catalystGroups"] = [
            deepcopy(entry["group"])
            for entry in selected_groups
            if recipe_index in entry["coverage"]
        ]

    return reduced_recipes, metadata
