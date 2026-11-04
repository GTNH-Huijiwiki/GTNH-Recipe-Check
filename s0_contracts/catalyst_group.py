"""催化组规范数据模型。

一条配方拥有若干催化条件：

    "catalystGroups": [
        {"catalysts": [...], "circuit": {...} | null},
        ...
    ]

列表可以为空。空组不会保存，零个催化组是无条件配方的唯一表示。
同一个组内的催化剂同时满足，即 AND；不同组之间是 OR 候选。
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
    """规范身份：排序后的催化剂 key 与电路 key。"""

    catalysts = tuple(sorted(item.get("key", "") for item in group.get("catalysts", [])))
    circuit = group.get("circuit") or {}
    return catalysts, circuit.get("key")


def concrete_catalyst_groups(group):
    """把单个组的 catalystChoices 展开为具体规范组。"""

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
    """展开候选项、移除空组并对催化组去重。"""

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
    """返回只包含规范 catalystGroups 的配方副本。"""

    result = deepcopy(recipe)
    result["catalystGroups"] = normalize_catalyst_groups(
        result.get("catalystGroups", [])
    )
    return result


def effective_analysis_groups(recipe):
    """图分析使用的催化组。

    零催化组配方在冲突分析中视为无条件配方，并映射为一个虚拟空组。
    配方自身不会被修改。
    """

    groups = recipe.get("catalystGroups", [])
    if groups:
        return groups
    return [{"catalysts": [], "circuit": None}]
