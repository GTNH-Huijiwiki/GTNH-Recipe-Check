"""激光蚀刻机专属删减规则。"""

_LASER_GEM_PREFIXES = ("Chipped ", "Flawed ", "Flawless ", "Exquisite ")


def clean(recipes):
    """
    删除下列配方：
      - 输入仅为 Floppy Disk 或 EEPROM
      - 输入包含 Unaligned Quark Releasing Catalyst
      - 输出全部为宝石（带特定前缀）
      - 输入全部为 Flawed 开头
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
        if output_items and all(
            name.startswith(_LASER_GEM_PREFIXES) for name in output_names
        ):
            continue
        if input_items and all(name.startswith("Flawed ") for name in input_names):
            continue

        filtered.append(recipe)
    return filtered
