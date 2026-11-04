"""激光蚀刻机专属删减规则。"""

from Core.grouped_schema import (
    expand_catalyst_choices,
    input_slot_count,
    remove_matching_input_items,
    remove_variants_where_all_inputs_match,
)

_LASER_GEM_PREFIXES = ("Chipped ", "Flawed ", "Flawless ", "Exquisite ")

# 需删除其配方的催化剂：Reinforced Glass Lens（key 与 originalName 双匹配）
_REMOVED_CATALYST_KEYS = {"dreamcraft:ReinforcedGlassLense"}
_REMOVED_CATALYST_NAMES = {"Reinforced Glass Lens"}


def _uses_removed_catalyst(recipe):
    """配方是否存在任一催化组使用了待删除的催化剂。"""
    for group in recipe.get("catalystGroups", []):
        for catalyst in group.get("catalysts", []):
            if (
                catalyst.get("key") in _REMOVED_CATALYST_KEYS
                or catalyst.get("originalName") in _REMOVED_CATALYST_NAMES
            ):
                return True
    return False


def _remove_removed_catalyst_groups(recipe):
    """移除命中 Reinforced Glass Lens 的具体催化组；全部命中时删除配方。"""
    groups = recipe.get("catalystGroups", [])
    kept = []
    for group in groups:
        if _uses_removed_catalyst({"catalystGroups": [group]}):
            continue
        kept.append(group)
    if groups and not kept:
        return None
    recipe["catalystGroups"] = kept
    return recipe


def clean(recipes):
    """
    删除下列配方：
      - 输入仅为 Floppy Disk 或 EEPROM
      - 输入包含 Unaligned Quark Releasing Catalyst
      - 输出全部为宝石（带特定前缀）
      - 输入全部为 Flawed 开头
      - 催化剂为 Reinforced Glass Lens
    """
    filtered = []
    for recipe in recipes:
        # 分组催化候选先展开成具体组，再按规则移除命中的具体组。
        recipe = expand_catalyst_choices(recipe)
        recipe = _remove_removed_catalyst_groups(recipe)
        if recipe is None:
            continue

        # 固定输入命中时整条删除；分组输入只剪掉命中成员。
        recipe = remove_matching_input_items(
            recipe,
            lambda item: item.get("originalName")
            == "Unaligned Quark Releasing Catalyst",
        )
        if recipe is None:
            continue

        output_items = recipe.get("outputItems", [])
        output_names = [item.get("originalName", "") for item in output_items]

        if input_slot_count(recipe) == 1:
            recipe = remove_variants_where_all_inputs_match(
                recipe,
                lambda item: item.get("originalName") in {"Floppy Disk", "EEPROM"},
            )
            if recipe is None:
                continue

        if output_items and all(
            name.startswith(_LASER_GEM_PREFIXES) for name in output_names
        ):
            continue

        if input_slot_count(recipe):
            recipe = remove_variants_where_all_inputs_match(
                recipe,
                lambda item: item.get("originalName", "").startswith("Flawed "),
            )
            if recipe is None:
                continue

        filtered.append(recipe)
    return filtered
