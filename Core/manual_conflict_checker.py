"""手动输入催化组方案，检测是否存在串配方并输出全部矛盾案例。

方案格式：

    "{[Ruby Lens, Firestone Lens],[]}"
    "{[Sapphire Lens],[]}"
    "{[Emerald Lens],[]}"
    "{[Green Sapphire Lens, Fayalite Lens, ..., Quantum Anomaly],[编程电路17]}"

- 方案是一个「槽位」序列（可传入多条字符串，或用逗号合并成一条）。
- 每个槽位 = { [催化剂...], [电路...] }，催化剂与电路都写在方括号里。
- 每个槽位相当于一个“大催化组”，与最终着色分组无本质差别；
  各催化组在发配方式1下相互独立，只在各自组内判定串配方。
- 名称解析基于翻译表（originalName/translatedName → key），内部判决一律以
  key 为身份；编程电路的 key 已含配置编号，无需特殊后缀解析。
- 无法识别的名字只会给出警告并跳过对应项，不会中断检查。

兼容旧的 {催化剂},[电路] 与嵌套多选项 {{...},[...]} 写法。
"""

import re

from Core.recipe_checker import (
    recipe_conflict_directions,
    recipe_io_signature,
)
from Core.catalyst_reducer import group_can_trigger


# ---------------------------------------------------------------------------
# 名称解析
# ---------------------------------------------------------------------------

def normalize_name(name):
    """规范化名称，便于模糊匹配（去空格、Lens↔透镜）。"""
    if not isinstance(name, str):
        return ""
    s = name.strip().lower()
    s = s.replace(" ", "").replace("\u3000", "")
    s = s.replace("lenses", "透镜").replace("lens", "透镜")
    return s


def build_name_index(translations):
    """构建 normalized_name -> [key, ...] 的反向索引。"""
    index = {}
    for key, names in translations.items():
        for field in ("originalName", "translatedName"):
            name = names.get(field, "")
            if not name:
                continue
            index.setdefault(normalize_name(name), []).append(key)
    return index


def _pick_key(keys, name, translations):
    """在多个候选 key 中优先选择与输入完全一致的那个。"""
    if len(keys) == 1:
        return keys[0]
    for key in keys:
        names = translations[key]
        if names.get("originalName") == name or names.get("translatedName") == name:
            return key
    return keys[0]


def _circuit_config_number(name):
    """从编程电路输入中提取配置编号；非电路输入返回 None。"""
    if not re.search(r"(编程电路|电路|circuit|integrated)", name, re.IGNORECASE):
        return None
    match = re.search(r"(\d+)", name)
    return int(match.group(1)) if match else None


def _item_from_key(key, translations):
    names = translations[key]
    return {
        "key": key,
        "originalName": names["originalName"],
        "translatedName": names.get("translatedName") or names["originalName"],
        "amount": 1,
    }


def resolve_item(name, translations, name_index, warnings=None):
    """把手动输入的名称解析为 {key, originalName, translatedName, amount}。

    无法识别的名字会写入 warnings 并返回 None（调用方跳过该项），不中断检查。
    内部判决以 key 为身份。
    """
    if isinstance(name, dict):
        # 已解析对象（可能来自缓存/历史），确保带 key
        if "key" in name:
            return name
        original_name = name.get("originalName", name.get("translatedName", ""))
        if original_name:
            return resolve_item(original_name, translations, name_index, warnings)
        return None

    text = str(name).strip()
    if not text:
        return None

    norm = normalize_name(text)
    keys = name_index.get(norm)
    if keys:
        return _item_from_key(_pick_key(keys, text, translations), translations)

    # 编程电路：key 已含编号，直接按配置编号构造 key 匹配
    config_number = _circuit_config_number(text)
    if config_number is not None:
        key = f"gregtech:gt.integrated_circuit:{config_number}"
        if key in translations:
            return _item_from_key(key, translations)

    if warnings is not None:
        warnings.append(f"无法识别的催化条件「{text}」，已跳过")
    return None


# ---------------------------------------------------------------------------
# 方案解析
# ---------------------------------------------------------------------------

def _split_top_level(text, sep=","):
    """按顶层分隔符拆分（忽略花括号/方括号内部的分隔符）。括号不匹配时抛错。"""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                raise ValueError("括号不匹配：存在多余的右括号")
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if depth != 0:
        raise ValueError("括号不匹配：存在未闭合的括号")
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _split_atoms(text):
    """把槽位内容切分为平衡的 {…} / […] 原子（忽略顶层逗号与空白）。"""
    atoms = []
    depth = 0
    current = []
    for ch in text:
        if ch in "{[":
            depth += 1
            current.append(ch)
        elif ch in "}]":
            if depth == 0:
                raise ValueError("括号不匹配：存在多余的右括号")
            current.append(ch)
            depth -= 1
            if depth == 0:
                atoms.append("".join(current))
                current = []
        elif depth > 0:
            current.append(ch)
    if depth != 0:
        raise ValueError("括号不匹配：存在未闭合的括号")
    return [atom.strip() for atom in atoms if atom.strip()]


def _parse_container_list(text):
    """解析 {a,b,c} 或 [a,b,c] 容器，返回名称字符串列表。"""
    text = text.strip()
    if not text:
        return []
    if len(text) < 2 or text[0] not in "{[" or text[-1] not in "}]":
        raise ValueError(f"容器格式错误: {text!r}")
    inner = text[1:-1].strip()
    if not inner:
        return []
    return _split_top_level(inner, ",")


def parse_scheme(scheme):
    """解析方案，返回槽位列表。

    scheme 可以是：
      - 单条字符串（顶层用逗号分隔多个槽位，兼容旧格式）；
      - 槽位字符串的可迭代对象（新格式，每个元素是一个槽位）。

    返回结构: [[(催化剂名列表, 电路名列表), ...], ...]，外层是槽位，内层是互斥选项。
    每个选项写成 `[催化剂...],[电路...]`，同时兼容旧的 `{催化剂},[电路]`
    与整体包裹 `{{催化剂},[电路]}` 写法。语法错误会直接抛出 ValueError。
    """
    if isinstance(scheme, str):
        slot_texts = _split_top_level(scheme.strip(), ",")
    else:
        slot_texts = [str(item).strip() for item in scheme if str(item).strip()]
        if not slot_texts:
            raise ValueError("方案内容为空")

    slots = []
    for slot_text in slot_texts:
        if not (slot_text.startswith("{") and slot_text.endswith("}")):
            raise ValueError(f"槽位未用 {{...}} 包裹: {slot_text!r}")

        inner = slot_text[1:-1].strip()
        atoms = _split_atoms(inner) if inner else []
        options = []
        index = 0
        while index < len(atoms):
            atom = atoms[index]
            if atom.startswith("{"):
                body = atom[1:-1].strip()
                body_parts = _split_top_level(body, ",")
                if len(body_parts) >= 2 and body_parts[1].startswith("["):
                    # 旧式整体包裹选项：{{催化剂},[电路]}
                    options.append(
                        (
                            _parse_container_list(body_parts[0]),
                            _parse_container_list(body_parts[1]),
                        )
                    )
                    index += 1
                    continue
            # 普通选项：催化剂容器（{...} 或 [...]）+ 电路容器 [...]
            cats = _parse_container_list(atom)
            circs = []
            if index + 1 < len(atoms) and atoms[index + 1].startswith("["):
                circs = _parse_container_list(atoms[index + 1])
                index += 2
            else:
                index += 1
            options.append((cats, circs))
        slots.append(options)

    return slots


# ---------------------------------------------------------------------------
# 串配方检测
# ---------------------------------------------------------------------------

def check_group_internal(recipes, group_node):
    """按“发配方式1”检查单个大催化组内部的串配方。

    方式1：每个催化组使用独立缓存空间，同一空间内同一时间只允许一个配方。
    组内配方 = 能被该大催化组（或其子集催化条件）触发的全部配方；
    串配方沿用旧判定 recipe_conflict_directions（配方两两、方向 A→B / B→A）。
    """
    # 对每个配方记录其被大催化组覆盖的原始催化组（组内可触发该配方的条件）
    active = []
    active_recipes = []
    for recipe_index, recipe in enumerate(recipes):
        groups = [
            group
            for group in recipe.get("catalystGroups", [])
            if group_can_trigger(group_node, group)
        ]
        active.append(groups)
        if groups:
            active_recipes.append(recipe_index)

    cases = []
    for left_index, left in enumerate(active_recipes):
        for right_index in range(left_index + 1, len(active_recipes)):
            right = active_recipes[right_index]
            if recipe_io_signature(recipes[left]) == recipe_io_signature(recipes[right]):
                continue
            for left_group in active[left]:
                for right_group in active[right]:
                    directions = recipe_conflict_directions(
                        recipes[left], left_group,
                        recipes[right], right_group,
                    )
                    if directions:
                        cases.append(
                            {
                                "left": left,
                                "right": right,
                                "left_group": left_group,
                                "right_group": right_group,
                                "directions": directions,
                            }
                        )
    return cases


def check_scheme(machine_name, recipes, scheme, translations):
    """检查手动方案是否产生串配方，返回完整报告结构。

    语义：方案的每个槽位（选项）都是一个“大催化组”，与最终着色分组无本质差别。
    各催化组在发配方式1下相互独立，因此只在各自组内判定串配方（组内配方按旧判定）。
    """
    warnings = []
    name_index = build_name_index(translations)
    slots = parse_scheme(scheme)

    groups = []
    all_cases = []
    for slot_index, options in enumerate(slots):
        for catalysts, circuits in options:
            resolved_catalysts = []
            for name in catalysts:
                item = resolve_item(name, translations, name_index, warnings)
                if item is not None:
                    resolved_catalysts.append(item)

            circuit_items = []
            for name in circuits:
                item = resolve_item(name, translations, name_index, warnings)
                if item is not None:
                    circuit_items.append(item)

            # 一个槽位内声明多个电路时视作多个互斥的大催化组变体，各自独立判定
            for circuit in (circuit_items or [None]):
                group_node = {"catalysts": list(resolved_catalysts), "circuit": circuit}
                cases = check_group_internal(recipes, group_node)
                group_entry = {
                    "slot_index": slot_index,
                    "catalysts": resolved_catalysts,
                    "circuit": circuit,
                    "cases": cases,
                }
                groups.append(group_entry)
                for case in cases:
                    case["slot_index"] = slot_index
                    all_cases.append(case)

    if not groups:
        raise ValueError("方案中没有可识别的催化组")

    return {
        "machine": machine_name,
        "slots": slots,
        "groups": groups,
        "cases": all_cases,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 可读输出
# ---------------------------------------------------------------------------

def _item_label(item):
    if not item:
        return ""
    return item.get("translatedName") or item.get("originalName", "")


def _option_label(node):
    parts = [_item_label(c) for c in node.get("catalysts", [])]
    label = "+".join(parts) or "(无催化剂)"
    circuit = node.get("circuit")
    if circuit:
        label = f"{label} / {_item_label(circuit)}"
    return label


def _recipe_label(recipe):
    inputs = [_item_label(i) for i in recipe.get("inputItems", [])]
    fluids = [_item_label(i) for i in recipe.get("inputFluids", [])]
    outputs = [_item_label(i) for i in recipe.get("outputItems", [])]
    out_fluids = [_item_label(i) for i in recipe.get("outputFluids", [])]
    lhs = " + ".join(inputs + fluids) or "(无输入)"
    rhs = " + ".join(outputs + out_fluids) or "(无输出)"
    return f"{lhs} → {rhs}"


_DIRECTION_TEXT = {
    "left_to_right": "左→右（做左配方会连带触发右配方）",
    "right_to_left": "右→左（做右配方会连带触发左配方）",
}


def format_report_with_recipes(result, recipes):
    """带配方详情的可读报告。"""
    lines = []
    lines.append(f"机器: {result['machine']}")

    for warning in result.get("warnings", []):
        lines.append(f"警告: {warning}")

    # 每个槽位命中的组内串配方数量
    slot_conflict_count = {}
    for group in result["groups"]:
        slot_conflict_count[group["slot_index"]] = (
            slot_conflict_count.get(group["slot_index"], 0) + len(group["cases"])
        )

    lines.append("手动催化组方案（方式1：每个槽位 = 独立大催化组，分别做组内判定）:")
    for slot_index, options in enumerate(result["slots"]):
        option_texts = []
        for catalysts, circuits in options:
            catalyst_text = "、".join(catalysts) or "无"
            text = f"[{catalyst_text}]"
            if circuits:
                text += f",[{','.join(circuits)}]"
            option_texts.append(text)
        conflict_count = slot_conflict_count.get(slot_index, 0)
        suffix = f"  → 组内串配方 {conflict_count} 条" if conflict_count else "  → 组内无串配方"
        lines.append(f"  槽位 {slot_index + 1}: {' 或 '.join(option_texts)}{suffix}")

    if not result["cases"]:
        lines.append("")
        lines.append("结论: 每个槽位（独立大催化组）内部均无串配方 ✔")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"结论: 存在串配方 ✘，共 {len(result['cases'])} 条组内串配方。")
    lines.append("")

    order = 0
    for group in result["groups"]:
        if not group["cases"]:
            continue
        slot_index = group["slot_index"]
        slot_group_label = _option_label(
            {"catalysts": group["catalysts"], "circuit": group["circuit"]}
        )
        lines.append(f"--- 槽位 {slot_index + 1} · {slot_group_label} ---")
        for case in group["cases"]:
            order += 1
            left = recipes[case["left"]]
            right = recipes[case["right"]]
            dirs = "、".join(
                _DIRECTION_TEXT.get(d, d) for d in case["directions"]
            )
            lines.append(f"矛盾 {order}: {dirs}")
            lines.append(f"  配方A [{_option_label(case['left_group'])}]: "
                         f"{_recipe_label(left)}")
            lines.append(f"  配方B [{_option_label(case['right_group'])}]: "
                         f"{_recipe_label(right)}")
        lines.append("")

    return "\n".join(lines)
