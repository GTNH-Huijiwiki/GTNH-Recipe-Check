"""人类可读的冲突图报告。"""

from __future__ import annotations

from collections import Counter

from s0_contracts.schema import Recipe

_MACHINE_NAMES = {
    "alloyblastsmelter": "合金冶炼炉",
    "assembler": "组装机",
    "blastfurnace": "工业高炉",
    "fluidchemicaleactor": "化工厂",
    "largechemicalreactor": "大型化学反应釜",
    "laserengraver": "激光蚀刻机",
    "multimixer": "大型搅拌机",
    "press": "冲压机床",
}


def _display_name(item):
    return item.translated_name or item.original_name or item.key


def _number(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _material_text(item):
    name = _display_name(item)
    if item.kind == "fluid":
        return f"{_number(item.amount)}mB {name}"
    if item.amount != 1:
        return f"{_number(item.amount)}x {name}"
    return name


def _slot_text(slot):
    labels = [_material_text(item) for item in slot.alternatives]
    text = " or ".join(labels) or "(空)"
    return f"({text})" if len(labels) > 1 else text


def _catalyst_set_text(catalyst_set):
    catalysts = " + ".join(
        _material_text(item) for item in catalyst_set.catalysts
    ) or "无催化剂"
    if catalyst_set.circuit:
        return f"{catalysts} / {_material_text(catalyst_set.circuit)}"
    return catalysts


def _recipe_catalyst_text(recipe):
    if not recipe.catalyst_group.sets:
        return "无催化剂"
    labels = sorted(
        {_catalyst_set_text(catalyst_set) for catalyst_set in recipe.catalyst_group.sets}
    )
    text = " or ".join(labels)
    return f"[{text}]" if len(labels) > 1 else text


def format_recipe(recipe: Recipe):
    """按 `输入 + 催化剂 → 输出` 输出完整配方摘要。"""
    inputs = " + ".join(
        _slot_text(slot) for slot in recipe.input_group.slots
    ) or "(无输入)"
    catalyst = _recipe_catalyst_text(recipe)
    outputs = " + ".join(
        _material_text(item)
        for item in recipe.output_group.items + recipe.output_group.fluids
    ) or "(无输出)"
    return f"{inputs} + {catalyst} → {outputs}"


def _equivalence_index(relations):
    result = {}
    for group in relations.get("equivalenceGroups", ()):
        set_ids = tuple(group.get("setIds", ()))
        if len(set_ids) < 2:
            continue
        for set_id in set_ids:
            result[set_id] = set_ids
    return result


def _catalyst_names(catalyst_set):
    names = sorted(
        {_display_name(item) for item in catalyst_set.catalysts}
    )
    return "+".join(names) or "无催化剂"


def _node_condition(catalyst_set, all_sets, equivalence_index):
    set_ids = equivalence_index.get(catalyst_set.set_id)
    members = (
        tuple(all_sets[set_id] for set_id in set_ids if set_id in all_sets)
        if set_ids
        else (catalyst_set,)
    )
    catalysts = " or ".join(
        sorted({_catalyst_names(item) for item in members})
    ) or "无催化剂"
    circuits = sorted(
        {
            _material_text(item.circuit)
            for item in members
            if item.circuit is not None
        }
    )
    if circuits:
        return f"{catalysts} / {'+'.join(circuits)}"
    return catalysts


def _table_cell(value):
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _recipe_text(recipe_id, primary, fallback):
    recipe = primary.get(recipe_id) or fallback.get(recipe_id)
    if recipe is None:
        return f"`{recipe_id}`（配方缺失）"
    return f"`{recipe_id}`：{format_recipe(recipe)}"



def _node_codes(graph):
    return {
        set_id: f"N{index:02d}"
        for index, set_id in enumerate(graph.get("nodes", ()), 1)
    }


def render_machine_report(
    machine,
    graph,
    selected_sets,
    all_sets,
    relations,
    selected_by_id,
    normalized_by_id,
    pair_threshold=None,
):
    """渲染单台机器的完整 Markdown 冲突报告。"""
    equivalence_index = _equivalence_index(relations)
    labels = {
        set_id: _node_condition(catalyst_set, all_sets, equivalence_index)
        for set_id, catalyst_set in selected_sets.items()
    }
    codes = _node_codes(graph)
    all_edges = tuple(graph.get("edges", ()))
    all_self_conflicts = tuple(graph.get("selfConflicts", ()))

    def _included(item):
        return (
            pair_threshold is None
            or len(item.get("conflicts", ())) < pair_threshold
        )

    edges = tuple(edge for edge in all_edges if _included(edge))
    self_conflicts = tuple(
        item for item in all_self_conflicts if _included(item)
    )
    omitted_edge_pairs = sum(
        len(edge.get("conflicts", ())) for edge in all_edges
    ) - sum(len(edge.get("conflicts", ())) for edge in edges)
    omitted_self_pairs = sum(
        len(item.get("conflicts", ())) for item in all_self_conflicts
    ) - sum(len(item.get("conflicts", ())) for item in self_conflicts)

    node_pair_counts = Counter()
    for edge in edges:
        pair_count = len(edge.get("conflicts", ()))
        node_pair_counts[edge["left"]] += pair_count
        node_pair_counts[edge["right"]] += pair_count
    for item in self_conflicts:
        node_pair_counts[item["setId"]] += len(item.get("conflicts", ()))

    edge_pairs = sum(
        len(edge.get("conflicts", ())) for edge in edges
    )
    self_pairs = sum(
        len(item.get("conflicts", ()))
        for item in self_conflicts
    )
    display_name = _MACHINE_NAMES.get(machine, machine)
    lines = [
        f"## 机器：{machine}（{display_name}）",
        "",
        f"- 冲突节点：{len(graph.get('nodes', ()))} 个",
        f"- 节点间冲突对：{len(edges)} 对节点，{edge_pairs} 组配方对",
        f"- 节点内自冲突：{self_pairs} 组配方对",
    ]
    if pair_threshold is not None:
        lines.extend(
            [
                f"- 报告筛选：仅列出冲突配方对少于 {pair_threshold} 的节点关系",
                f"- 未列出：节点间 {omitted_edge_pairs} 组配方对，节点内 {omitted_self_pairs} 组配方对",
            ]
        )
    lines.extend(
        [
            "",
            "### 节点",
            "",
            "| 节点 | 催化剂条件 | 冲突配方对 |",
            "|---|---|---:|",
        ]
    )
    for set_id in graph.get("nodes", ()):
        lines.append(
            "| {code} | {label} | {count} |".format(
                code=codes[set_id],
                label=_table_cell(labels.get(set_id, set_id)),
                count=node_pair_counts[set_id],
            )
        )

    lines.extend(["", "### 节点间冲突", ""])
    edges = sorted(
        edges,
        key=lambda edge: (
            -len(edge.get("conflicts", ())),
            codes[edge["left"]],
            codes[edge["right"]],
        ),
    )
    if not edges:
        lines.append("无。")
    for edge in edges:
        left = edge["left"]
        right = edge["right"]
        conflicts = edge.get("conflicts", ())
        directions = {item["direction"] for item in conflicts}
        left_label = labels.get(left, left)
        right_label = labels.get(right, right)
        if directions == {"left_to_right"}:
            relation = f"{codes[left]} {left_label} ⊃ {codes[right]} {right_label}"
        elif directions == {"right_to_left"}:
            relation = f"{codes[right]} {right_label} ⊃ {codes[left]} {left_label}"
        else:
            relation = f"{codes[left]} {left_label} ⇄ {codes[right]} {right_label}"
        lines.extend(
            [
                f"#### {relation}",
                "",
                f"- 冲突配方对：{len(conflicts)}",
                "",
            ]
        )
        for direction, arrow in (
            ("left_to_right", f"{codes[left]} ⊃ {codes[right]}"),
            ("right_to_left", f"{codes[right]} ⊃ {codes[left]}"),
        ):
            entries = sorted(
                (
                    item
                    for item in conflicts
                    if item["direction"] == direction
                ),
                key=lambda item: (
                    item["sourceRecipeId"], item["targetRecipeId"]
                ),
            )
            if not entries:
                continue
            lines.extend([f"##### {arrow}（{len(entries)} 对）", ""])
            for index, conflict in enumerate(entries, 1):
                source_id = conflict["sourceRecipeId"]
                target_id = conflict["targetRecipeId"]
                lines.extend(
                    [
                        f"{index}. 来源 {_recipe_text(source_id, selected_by_id, normalized_by_id)}",
                        f"   目标 {_recipe_text(target_id, normalized_by_id, selected_by_id)}",
                        "",
                    ]
                )
        lines.append("")

    lines.extend(["### 节点内自冲突", ""])
    self_conflicts = sorted(
        self_conflicts,
        key=lambda item: codes.get(item["setId"], item["setId"]),
    )
    if not self_conflicts:
        lines.append("无。")
    for item in self_conflicts:
        set_id = item["setId"]
        lines.extend(
            [
                f"#### {codes.get(set_id, set_id)} {labels.get(set_id, set_id)}",
                "",
                f"- 冲突配方对：{len(item.get('conflicts', ()))}",
                "",
            ]
        )
        for index, conflict in enumerate(item.get("conflicts", ()), 1):
            left_id = conflict["leftRecipeId"]
            right_id = conflict["rightRecipeId"]
            arrows = []
            if "left_to_right" in conflict.get("directions", ()):
                arrows.append(f"`{left_id}` ⊃ `{right_id}`")
            if "right_to_left" in conflict.get("directions", ()):
                arrows.append(f"`{right_id}` ⊃ `{left_id}`")
            lines.extend(
                [
                    f"{index}. 配方 {_recipe_text(left_id, normalized_by_id, {})}",
                    f"   配方 {_recipe_text(right_id, normalized_by_id, {})}",
                    f"   方向：{'；'.join(arrows) or '无'}",
                    "",
                ]
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_conflict_report(machine_sections, pair_threshold=None):
    """拼装完整的人类可读冲突报告。"""
    header = [
        "# 冲突结果",
        "> - `⊃`：左侧配方的输入条件包含右侧，可能提前触发右侧配方。",
        "> - `⇄`：双向包含/触发。",
        "> - 配方格式：`输入 + 催化剂 → 输出`。",
    ]
    if pair_threshold is not None:
        header.append(
            f"> - 报告筛选：仅列出冲突配方对少于 {pair_threshold} 的节点关系。"
        )
    header.append("")
    return "\n".join(header) + "\n\n".join(machine_sections).rstrip() + "\n"
