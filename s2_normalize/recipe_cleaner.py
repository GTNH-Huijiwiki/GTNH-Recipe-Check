"""清洗原始配方：展开输入/催化组矿辞典、提取输入输出、合并等效配方。"""

import re
from itertools import product

from s2_normalize.ore_dictionary import expand_ore_catalysts


def _item_options(item):
    """物品字段可能是单个对象或对象列表（即多个可选）。"""
    return item if isinstance(item, list) else [item]


def _normalize_item(item):
    """将物品规范为 {'key': str, 'amount': int}。"""
    if isinstance(item, str):
        return {"key": item, "amount": 1}
    if not isinstance(item, dict):
        raise TypeError(f"物品格式无效: {item!r}")
    return {"key": item.get("key", ""), "amount": item.get("amount", 1)}


def _normalize_fluid(fluid):
    """将流体规范为 {'key': str, 'amount': int}。"""
    if not isinstance(fluid, dict):
        raise TypeError(f"流体格式无效: {fluid!r}")
    return {"key": fluid.get("key", ""), "amount": fluid.get("amount", 0)}


def _is_circuit(item):
    """根据 key 判断是否为编程电路。"""
    return bool(
        re.search(r"(?:circuit|integrated_circuit)", item.get("key", ""), re.IGNORECASE)
    )


def _process_raw_recipe(raw_recipe, ore_dictionary, item_keys=None, translations=None):
    """
    处理单个原始配方，返回清洗后的配方列表（可能因矿辞典展开而产生多个）。

    展开沿用催化组的做法（expand_ore_catalysts）：
      - # 开头：查矿辞典取全部成员；
      - 以 :32767 结尾：取物品注册表 item_keys 中同前缀的全部 key（与矿辞典无关，
        如 "minecraft:log:32767" → "minecraft:log" 及其各 meta）。
    对展开结果：
      - nc 催化剂：展开得到多组「催化组选项」，合并时留作同一配方的多个催化组；
      - 普通输入：展开得到多组「具体输入组合」，因输入不同，合并时各自成为
        独立配方——#A 或通配代表 a、b 时，输入 a 与输入 b 即为两个不同配方。
    若展开出的多个 key 对应完全相同的 (originalName, translatedName)，则折叠为
    单个虚拟 key（展开退化为 key 替换），不再拆成多个等价配方。
    步骤：
      1. 展开输入/输出物品的可选列表。
      2. 规范化并依据 nc 标记拆分普通输入、催化剂与编程电路。
      3. 普通输入、催化剂分别用 expand_ore_catalysts 展开，生成最终清洗配方。
    """
    # 输入/输出可选列表展开
    input_options = [_item_options(item) for item in raw_recipe.get("inputItems", [])]
    output_options = [_item_options(item) for item in raw_recipe.get("outputItems", [])]
    input_variants = product(*input_options) if input_options else [()]
    output_variants = list(product(*output_options)) if output_options else [()]

    cleaned_recipes = []

    for input_items in input_variants:
        # 规范化输入，并依据 nc 标记拆分普通输入、催化剂和编程电路。
        catalysts = []
        circuit = None
        inputs = []
        for raw_item in input_items:
            if raw_item is None:
                continue
            item = _normalize_item(raw_item)
            if isinstance(raw_item, dict) and raw_item.get("nc", False):
                if _is_circuit(item):
                    circuit = item
                else:
                    catalysts.append(item)
            else:
                inputs.append(item)

        # 与催化组相同的方法展开矿辞典(#)与通配meta(:32767)：
        # 催化剂→多组催化组；普通输入→多组具体输入
        catalyst_variants = expand_ore_catalysts(
            catalysts, ore_dictionary, item_keys, translations
        )
        input_combos = expand_ore_catalysts(
            inputs, ore_dictionary, item_keys, translations
        )

        for input_combo in input_combos:
            for output_items in output_variants:
                for catalyst_variant in catalyst_variants:
                    cleaned_recipes.append(
                        {
                            "inputItems": input_combo,
                            "inputFluids": [
                                _normalize_fluid(item)
                                for item in raw_recipe.get("inputFluids", [])
                            ],
                            "outputItems": [
                                _normalize_item(item) for item in output_items
                            ],
                            "outputFluids": [
                                _normalize_fluid(item)
                                for item in raw_recipe.get("outputFluids", [])
                            ],
                            "catalystGroups": [
                                {
                                    "catalysts": catalyst_variant,
                                    "circuit": circuit,
                                }
                            ],
                        }
                    )

    return cleaned_recipes


def _signature(recipe):
    """生成除催化组外的配方签名，用于判断输入输出是否完全相同。"""

    def entries(field):
        return tuple(
            sorted(
                (item.get("key", ""), item.get("amount", 0))
                for item in recipe.get(field, [])
            )
        )

    return tuple(
        entries(field)
        for field in ("inputItems", "inputFluids", "outputItems", "outputFluids")
    )


def _group_signature(group):
    """生成催化组的签名，用于去重。"""
    catalysts = tuple(
        sorted(
            (item.get("key", ""), item.get("amount", 0))
            for item in group.get("catalysts", [])
        )
    )
    circuit = group.get("circuit") or {}
    return catalysts, circuit.get("key", ""), circuit.get("amount", 0)


def merge_equivalent_recipes(recipes):
    """合并输入输出完全相同的配方（物品+流体），其催化组合并去重。"""
    merged = {}
    for recipe in recipes:
        signature = _signature(recipe)
        if signature not in merged:
            merged[signature] = {
                field: recipe[field]
                for field in (
                    "inputItems",
                    "inputFluids",
                    "outputItems",
                    "outputFluids",
                )
            }
            merged[signature]["catalystGroups"] = []

        for group in recipe.get("catalystGroups", []):
            groups = merged[signature]["catalystGroups"]
            if not any(
                _group_signature(existing) == _group_signature(group)
                for existing in groups
            ):
                groups.append(group)

    return list(merged.values())


def clean_and_merge_recipes(raw_recipes, ore_dictionary, item_keys=None, translations=None):
    """
    输入原始配方列表、矿辞典、物品注册表 key 集与翻译映射，返回清洗并合并后的配方列表。
    item_keys 用于展开以 :32767 结尾的通配输入（同前缀全部 key），可为 None。
    translations 用于展开结果同名折叠（key -> {originalName, translatedName}），可为 None。
    合并规则：输入输出完全相同的配方（物品+流体），其催化组合并。
    """
    cleaned = []
    for raw_recipe in raw_recipes:
        # 先处理原始配方，得到清洗后的多个变体
        cleaned.extend(
            _process_raw_recipe(raw_recipe, ore_dictionary, item_keys, translations)
        )
    return merge_equivalent_recipes(cleaned)
