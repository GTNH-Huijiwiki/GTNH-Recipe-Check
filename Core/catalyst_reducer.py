"""催化组覆盖分析与自动删减。"""

import logging
from copy import deepcopy

from Core.recipe_checker import group_label, group_signature, item_signature

logger = logging.getLogger(__name__)


def is_empty_group(group):
    """是否为空催化组（无催化剂、无编程电路），即无条件反应的特殊组。"""
    return (not group.get("catalysts")) and (not group.get("circuit"))


def _catalyst_key_set(items):
    """催化剂的 key 集合（以 key 为身份，不考虑数量）。"""
    return {item.get("key", "") for item in items}


def group_can_trigger(candidate, required):
    # 空催化组（无催化剂、无电路）是“无条件反应”的特殊组，只能由它自身触发，
    # 不应被其它无电路但带催化剂的候选组吸收（否则空组会被摊派/吞并）。
    if is_empty_group(required):
        return is_empty_group(candidate)

    candidate_circuit = candidate.get("circuit") or {}
    required_circuit = required.get("circuit") or {}
    if item_signature(candidate_circuit) != item_signature(required_circuit):
        return False

    # 只按存在性（key）判断，不比较数量
    candidate_catalysts = _catalyst_key_set(candidate.get("catalysts", []))
    required_catalysts = _catalyst_key_set(required.get("catalysts", []))
    return required_catalysts.issubset(candidate_catalysts)


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


def reduce_catalyst_groups(machine_name, recipes):
    """按配方覆盖集合删减催化组，并返回元数据。"""
    groups = []
    group_indexes = {}
    recipe_groups = []

    for recipe in recipes:
        current_groups = []
        for group in recipe.get("catalystGroups", []):
            signature = group_signature(group)
            if signature not in group_indexes:
                group_indexes[signature] = len(groups)
                groups.append(deepcopy(group))
            current_groups.append(group_indexes[signature])
        recipe_groups.append(current_groups)

    if not groups:
        logger.debug("机器 %s 没有可删减的催化组。", machine_name)
        return recipes, {"machine": machine_name, "components": [], "selected_groups": []}

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

    reduced_recipes = deepcopy(recipes)
    metadata = {"machine": machine_name, "components": [], "selected_groups": []}
    selected_groups = []
    abstract_index = 1

    for component_indexes in components.values():
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
                    "机器 %s 等价催化组 %d 个，使用抽象催化剂 %s。",
                    machine_name, len(class_indexes), abstract_name,
                )
            class_entries.append(
                {
                    "indexes": class_indexes,
                    "coverage": set().union(
                        *(coverage[index] for index in class_indexes)
                    ),
                    "group": candidate_group,
                    "abstract_replacement": abstract_replacement,
                }
            )

        top_level = [
            entry
            for entry in class_entries
            if not any(entry["coverage"] < other["coverage"] for other in class_entries)
        ]
        logger.debug(
            "机器 %s 关系分量候选顶层催化组: %s",
            machine_name,
            "; ".join(group_label(entry["group"]) for entry in top_level),
        )

        all_top_level = list(top_level)
        uncovered = set().union(*(entry["coverage"] for entry in top_level))
        component_selected = []
        while uncovered:
            selected = max(
                top_level,
                key=lambda entry: (
                    len(entry["coverage"] & uncovered),
                    group_label(entry["group"]),
                ),
            )
            if not selected["coverage"] & uncovered:
                break
            component_selected.append(selected)
            uncovered -= selected["coverage"]
            top_level.remove(selected)

        for entry in component_selected:
            selected_groups.append(entry)
            logger.debug(
                "机器 %s 选择催化组: %s，覆盖 %d 个配方。",
                machine_name, group_label(entry["group"]), len(entry["coverage"]),
            )

        metadata["components"].append(
            {
                "groups": [deepcopy(groups[index]) for index in component_indexes],
                "top_level_candidates": [deepcopy(entry["group"]) for entry in all_top_level],
                "equivalent_groups": [
                    {
                        "members": [deepcopy(groups[index]) for index in entry["indexes"]],
                        "replacement": deepcopy(entry["abstract_replacement"]),
                    }
                    for entry in class_entries
                    if len(entry["indexes"]) > 1
                ],
            }
        )

    selected_groups.sort(key=lambda entry: group_label(entry["group"]))
    metadata["selected_groups"] = [
        {"group": deepcopy(entry["group"]), "coverage": sorted(entry["coverage"])}
        for entry in selected_groups
    ]

    for recipe_index, recipe in enumerate(reduced_recipes):
        recipe["catalystGroups"] = [
            deepcopy(entry["group"])
            for entry in selected_groups
            if recipe_index in entry["coverage"]
        ]

    return reduced_recipes, metadata
