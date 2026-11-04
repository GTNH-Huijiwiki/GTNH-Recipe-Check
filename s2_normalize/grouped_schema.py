"""分组输入规则与催化组规范化辅助函数。

配方规则运行时，输入 OR 候选保留在 inputGroups 中。
催化剂候选在数据模式边界立即展开，下游只看到规范形式：

    {"catalysts": [...], "circuit": {...} | null}

一条配方可以拥有零个、一个或多个催化组。
"""

from __future__ import annotations

from copy import deepcopy

from s0_contracts.catalyst_group import (
    concrete_catalyst_groups as _concrete_catalyst_groups,
    group_signature as _catalyst_group_signature,
    normalize_recipe_catalyst_groups,
)


def concrete_catalyst_groups(group):
    """返回单个带候选项催化组展开后的具体规范组。"""

    return _concrete_catalyst_groups(group)


def expand_catalyst_choices(recipe):
    """返回只包含具体规范催化组的配方副本。"""

    return normalize_recipe_catalyst_groups(recipe)


def catalyst_group_signature(group):
    """规范催化组的身份。"""

    return _catalyst_group_signature(group)


def input_slot_count(recipe):
    """物料输入槽数量，不包含催化组。"""

    return (
        len(recipe.get("inputItems", []) or [])
        + len(recipe.get("inputFluids", []) or [])
        + len(recipe.get("inputGroups", []) or [])
    )


def remove_matching_input_items(recipe, predicate):
    """从普通输入槽中移除匹配候选项。

    固定输入命中时直接删除该槽；分组槽只移除命中的成员。若某个槽位
    已无候选，则不存在具体配方变体并返回 None。只剩一个成员的分组
    会折叠回固定输入，以保持结构紧凑。
    """

    result = deepcopy(recipe)
    fixed = []
    for item in result.get("inputItems", []) or []:
        if predicate(item):
            # 固定输入存在于所有具体变体中，因此命中 contains 规则后
            # 不可能保留任何有效变体。
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
    """只删除所有普通输入均命中的具体变体。

    本函数不匹配流体；若配方含有流体，则不存在“所有普通输入均命中”
    的变体，直接返回原文。分组槽同时包含命中和未命中成员时，只移除
    命中成员，从而保留未命中变体，不删除整条分组配方。
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

    # 只有当每个槽位都至少有一个命中候选时才存在全命中组合。
    # 否则不存在符合条件的变体，删除会误伤未命中的有效输入。
    if any(not predicate(item) for item in fixed):
        return result
    if any(
        group.get("kind") == "item"
        and not any(predicate(member) for member in group.get("members", []) or [])
        for group in groups
    ):
        return result

    return remove_matching_input_items(result, predicate)