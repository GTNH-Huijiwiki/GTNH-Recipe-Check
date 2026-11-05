"""机器规则使用的规范配方操作。"""

from dataclasses import replace

from s0_contracts.schema import CatalystGroup, InputGroup, InputSlot, Recipe


def input_slot_count(recipe):
    """返回普通输入槽数量，不包含催化组与输出组。"""
    return len(recipe.input_group.slots)


def iter_input_materials(recipe):
    """遍历全部输入槽及其候选项。"""
    for slot_index, slot in enumerate(recipe.input_group.slots):
        for alternative in slot.alternatives:
            yield slot_index, alternative


def has_input_name(recipe, name):
    """普通输入中是否存在指定 originalName。"""
    return any(
        item.original_name == name
        for _, item in iter_input_materials(recipe)
    )


def remove_matching_inputs(recipe, predicate):
    """删除命中的输入候选；槽位为空时返回 None。"""
    slots = []
    for slot in recipe.input_group.slots:
        alternatives = tuple(
            item for item in slot.alternatives if not predicate(item)
        )
        if not alternatives:
            return None
        slots.append(InputSlot(alternatives))
    return replace(recipe, input_group=InputGroup(tuple(slots)))


def remove_variants_where_all_inputs_match(recipe, predicate):
    """只有所有普通输入槽都有命中候选时，才删除对应候选。"""
    if any(item.kind == "fluid" for _, item in iter_input_materials(recipe)):
        return recipe
    if not recipe.input_group.slots:
        return recipe
    for slot in recipe.input_group.slots:
        if not any(predicate(item) for item in slot.alternatives):
            return recipe
    return remove_matching_inputs(recipe, predicate)


def replace_catalyst_sets(recipe, sets):
    """用新的催化集元组替换配方催化组。"""
    return replace(recipe, catalyst_group=CatalystGroup(tuple(sets)))


def has_catalyst_name(recipe, names):
    """配方任意催化集是否使用指定 originalName 的催化剂。"""
    return any(
        item.original_name in names
        for catalyst_set in recipe.catalyst_group.sets
        for item in catalyst_set.catalysts
    )


def has_catalyst_key(recipe, keys):
    """配方任意催化集是否使用指定 key 的催化剂。"""
    return any(
        item.key in keys
        for catalyst_set in recipe.catalyst_group.sets
        for item in catalyst_set.catalysts
    )


def outputs_all(recipe, predicate):
    """输出物品非空且全部满足 predicate。"""
    items = recipe.output_group.items
    return bool(items) and all(predicate(item) for item in items)
