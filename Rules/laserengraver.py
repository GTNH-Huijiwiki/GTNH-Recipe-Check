"""激光蚀刻机专属删减规则。"""

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
        input_items = recipe.get("inputItems", [])
        output_items = recipe.get("outputItems", [])
        input_names = [item.get("originalName", "") for item in input_items]
        output_names = [item.get("originalName", "") for item in output_items]

        if len(input_items) == 1 and input_names[0] in {"Floppy Disk", "EEPROM"}:
            continue
        if "Unaligned Quark Releasing Catalyst" in input_names:
            continue
        if _uses_removed_catalyst(recipe):
            continue
        if output_items and all(
            name.startswith(_LASER_GEM_PREFIXES) for name in output_names
        ):
            continue
        if input_items and all(name.startswith("Flawed ") for name in input_names):
            continue

        filtered.append(recipe)
    return filtered
