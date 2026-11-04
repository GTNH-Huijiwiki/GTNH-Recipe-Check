"""配方与催化组的共享身份、标签和排序规则。"""

from __future__ import annotations


def item_signature(item):
    """物品的唯一身份签名：key（数量不参与身份判定）。"""
    return item.get("key", "")


def group_signature(group):
    catalysts = tuple(
        sorted(item_signature(item) for item in group.get("catalysts", []))
    )
    choices = tuple(
        tuple(sorted(item_signature(item) for item in choice.get("members", [])))
        for choice in group.get("catalystChoices", []) or []
    )
    circuit = group.get("circuit") or {}
    return catalysts, choices, item_signature(circuit) if circuit else None


def _display_name(item):
    """展示用名：优先 translatedName，其次 originalName，最后 key。"""
    return (
        item.get("translatedName")
        or item.get("originalName")
        or item.get("key", "")
    )


def group_label(group):
    catalysts = sorted(
        _display_name(item) for item in group.get("catalysts", [])
    )
    circuit_item = group.get("circuit") or {}
    circuit = _display_name(circuit_item) if circuit_item else ""
    label = "+".join(catalysts) or "(无催化剂)"
    return f"{label} / {circuit}" if circuit else label


def _name(item):
    """物品/流体的可读名。"""
    return _display_name(item)


def recipe_io_text(recipe):
    """生成配方输入输出的一行摘要，如 `A + B → C`。"""
    inputs = [_name(item) for item in recipe.get("inputItems", [])]
    inputs += [
        _name(item) for item in recipe.get("inputFluids", [])
    ]
    outputs = [_name(item) for item in recipe.get("outputItems", [])]
    outputs += [
        _name(item) for item in recipe.get("outputFluids", [])
    ]
    lhs = " + ".join(inputs) or "(无输入)"
    rhs = " + ".join(outputs) or "(无输出)"
    return f"{lhs} → {rhs}"


def abstract_group_map(reduction_metadata):
    """建立抽象催化剂 key 到原始催化组的映射。"""
    abstract_groups = {}
    for machine in reduction_metadata:
        for component in machine.get("components", []):
            for equivalent in component.get("equivalent_groups", []):
                replacement = equivalent.get("replacement") or {}
                members = replacement.get("members") or equivalent.get("members", [])
                replacement_key = replacement.get("key", "") or replacement.get(
                    "originalName", ""
                )
                if replacement_key and members:
                    abstract_groups[replacement_key] = members
    return abstract_groups


# 空催化组占位电路的 key（展示为“编程电路0”）
_EMPTY_CIRCUIT_KEY = "__circuit_0"


def circuit_number(item):
    """编程电路编号：占位「编程电路0」为 0，其余取 key 末尾数字段；非电路返回 None。"""
    if not item:
        return None
    key = item.get("key", "")
    if key == _EMPTY_CIRCUIT_KEY:
        return 0
    tail = key.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _circuit_order_key(item):
    """编程电路的排序键：无编号的（如占位用的编程电路0）排最前，其余按编号升序。"""
    number = circuit_number(item)
    return (1, number) if number is not None else (0, 0)


def group_circuit_numbers(group):
    """催化组内的编程电路编号集合（circuit 与 circuitOptions 合并）。

    完全空组（无催化剂、无电路）＝无条件反应的特殊组，输出中视作「编程电路0」；
    仅有催化剂而无电路的组不算电路组，返回空集合。
    """
    items = []
    circuit = group.get("circuit")
    if circuit:
        items.append(circuit)
    items.extend(group.get("circuitOptions") or [])
    numbers = {number for number in map(circuit_number, items) if number is not None}
    if not numbers and not group.get("catalysts"):
        numbers.add(0)
    return numbers


def group_output_order_key(group):
    """输出的组间排序键。

    规则：无编程电路的组排最后；含编程电路（含「编程电路0」）的组按
    「组内最小编号」升序；编号仍相同者再按编号集合升序，保证输出稳定。
    """
    numbers = group_circuit_numbers(group)
    if not numbers:
        return (1, 0, ())
    ordered = tuple(sorted(numbers))
    return (0, ordered[0], ordered)


def readable_group_label(group, abstract_groups):
    """将催化组显示为实际名称，抽象催化剂成员用“或”连接。"""
    def catalyst_label(catalyst):
        abstract_members = abstract_groups.get(
            catalyst.get("key", ""),
            abstract_groups.get(catalyst.get("originalName", "")),
        )
        if abstract_members:
            return " 或 ".join(
                catalyst_label(member_catalyst)
                for member in abstract_members
                for member_catalyst in member.get("catalysts", [])
            )
        return _display_name(catalyst)

    catalyst_labels = sorted(
        catalyst_label(catalyst) for catalyst in group.get("catalysts", [])
    )

    label = " + ".join(catalyst_labels) or "无催化剂"
    circuits = []
    circuit = group.get("circuit")
    if circuit:
        circuits.append(circuit)
    circuits.extend(group.get("circuitOptions", []))
    circuit_by_label = {
        _display_name(item): item for item in circuits if item
    }
    circuit_labels = [
        _display_name(item)
        for item in sorted(circuit_by_label.values(), key=_circuit_order_key)
    ]
    if circuit_labels:
        label = f"{label} / {'+'.join(circuit_labels)}"
    elif not group.get("catalysts"):
        # 空催化组（无催化剂、无电路）= 无条件反应的特殊组，展示为编程电路0
        label = f"{label} / 编程电路0"
    return label


def recipe_io_signature(recipe):
    """返回忽略催化组和数量的输入输出签名。"""

    return tuple(
        tuple(sorted(item.get("key", "") for item in recipe.get(field, [])))
        for field in ("inputItems", "inputFluids", "outputItems", "outputFluids")
    )


def recipe_id(machine_name, recipe_index):
    return f"{machine_name}_recipe_{recipe_index:04d}"


def assign_recipe_ids(machine_name, recipes):
    for recipe_index, recipe in enumerate(recipes):
        recipe["recipeId"] = recipe_id(machine_name, recipe_index)
