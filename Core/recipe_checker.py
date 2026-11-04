"""配方冲突检查核心逻辑，供主流程和配方复核脚本共用。"""

import logging
from collections import Counter
from copy import deepcopy

logger = logging.getLogger(__name__)


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


def condition_counter(recipe, group):
    """返回配方输入与催化条件的存在性集合（以 key 为身份，忽略数量）。"""
    condition = Counter()

    def add_items(kind, items):
        for item in items:
            key = item.get("key", "")
            condition[(kind, key)] = 1

    add_items("item", recipe.get("inputItems", []))
    add_items("fluid", recipe.get("inputFluids", []))
    add_items("catalyst", group.get("catalysts", []))
    circuit = group.get("circuit")
    if circuit:
        add_items("circuit", [circuit])
    return condition


def input_condition(recipe):
    condition = Counter()
    for kind, field in (("item", "inputItems"), ("fluid", "inputFluids")):
        for item in recipe.get(field, []):
            key = item.get("key", "")
            condition[(kind, key)] = 1
    return condition


def cache_condition(input_values, left_group, right_group):
    condition = input_values.copy()
    for group in (left_group, right_group):
        for key in condition_counter({}, group):
            condition[key] = 1
    return condition


def condition_satisfies(cache_values, required_values):
    return all(cache_values[key] for key in required_values)


def recipe_io_signature(recipe):
    """返回不包含催化组的输入输出签名（以 key 为身份，忽略数量）。"""
    return tuple(
        tuple(
            sorted(item.get("key", "") for item in recipe.get(field, []))
        )
        for field in ("inputItems", "inputFluids", "outputItems", "outputFluids")
    )


def recipe_id(machine_name, recipe_index):
    return f"{machine_name}_recipe_{recipe_index:04d}"


def assign_recipe_ids(machine_name, recipes):
    for recipe_index, recipe in enumerate(recipes):
        recipe["recipeId"] = recipe_id(machine_name, recipe_index)


def recipe_conflict_directions(left_recipe, left_group, right_recipe, right_group):
    """判断两个催化组下的两个配方是否互相构成串配方。

    输入上已构成连带时，若两侧输出完全相同（key 集合，忽略数量），视为良性连带
    （做一条只是顺带产出同样的东西），直接豁免、不判为串配方。
    """
    if _recipe_output_keys(left_recipe) == _recipe_output_keys(right_recipe):
        return []

    left_cache = cache_condition(
        input_condition(left_recipe), left_group, right_group
    )
    right_cache = cache_condition(
        input_condition(right_recipe), right_group, left_group
    )
    directions = []
    if condition_satisfies(
        left_cache, condition_counter(right_recipe, right_group)
    ):
        directions.append("left_to_right")
    if condition_satisfies(
        right_cache, condition_counter(left_recipe, left_group)
    ):
        directions.append("right_to_left")
    return directions


def _condition_key(item):
    """条件用 key 作为身份。"""
    return item.get("key", "")


def _recipe_input_keys(recipe):
    """配方的普通输入条件键集合（以 key 为身份，忽略数量、忽略催化组）。"""
    keys = set()
    for kind, field in (("item", "inputItems"), ("fluid", "inputFluids")):
        for item in recipe.get(field, []):
            keys.add((kind, _condition_key(item)))
    return frozenset(keys)


def _recipe_output_keys(recipe):
    """配方的输出键集合（item/fluid 分型，以 key 为身份，忽略数量）。

    用于「输出相同则豁免」的良性连带判定。
    """
    keys = set()
    for kind, field in (("item", "outputItems"), ("fluid", "outputFluids")):
        for item in recipe.get(field, []):
            keys.add((kind, _condition_key(item)))
    return frozenset(keys)


def _recipe_input_slots(recipe):
    """Return material input slots as OR sets of (kind, key).

    Fixed items/fluids become singleton slots.  Every inputGroups entry becomes
    one multi-member slot; quantities and display names are ignored.
    """

    slots = []
    seen = set()
    for kind, field in (("item", "inputItems"), ("fluid", "inputFluids")):
        for item in recipe.get(field, []):
            slot = frozenset({(kind, _condition_key(item))})
            if slot not in seen:
                seen.add(slot)
                slots.append(slot)
    for group in recipe.get("inputGroups", []) or []:
        kind = group.get("kind")
        members = frozenset(
            (kind, _condition_key(member))
            for member in group.get("members", []) or []
        )
        if members and members not in seen:
            seen.add(members)
            slots.append(members)
    return tuple(slots)


def _has_orphan_input(recipe):
    return bool((recipe.get("orphan") or {}).get("hasOrphanInput"))


def _slots_can_match(source_slots, target_slots):
    """Whether all target demand slots can consume distinct source slots.

    Slots are OR sets.  A target slot is satisfied by a source slot when their
    member sets intersect.  This prevents two target groups from incorrectly
    consuming different members of the same source group at once.
    """

    if len(target_slots) > len(source_slots):
        return False
    if not target_slots:
        return True

    adjacency = []
    for demand in target_slots:
        options = [
            index
            for index, supply in enumerate(source_slots)
            if demand & supply
        ]
        if not options:
            return False
        adjacency.append(options)

    matched_demand = [-1] * len(source_slots)

    def augment(demand_index, visited):
        for supply_index in adjacency[demand_index]:
            if supply_index in visited:
                continue
            visited.add(supply_index)
            previous = matched_demand[supply_index]
            if previous == -1 or augment(previous, visited):
                matched_demand[supply_index] = demand_index
                return True
        return False

    # Match the most constrained target slots first.
    for demand_index in sorted(range(len(adjacency)), key=lambda index: len(adjacency[index])):
        if not augment(demand_index, set()):
            return False
    return True


def recipe_can_trigger(source_recipe, target_recipe):
    """Whether executing source also satisfies target's material inputs."""

    return _slots_can_match(
        _recipe_input_slots(source_recipe),
        _recipe_input_slots(target_recipe),
    )


# 单向检查说明：其它催化组配方执行时，空组配方会被连带触发
_EMPTY_CONFLICT_DIRECTION_TEXT = (
    "其它组配方 → 编程电路0（做其它催化组配方会连带触发空催化组配方）"
)

# 每个「空组 × 其它组」最多保留的样板例数（计数始终是全量）
_EMPTY_CONFLICT_SAMPLE_LIMIT = 3


def _is_empty_group(group):
    """是否为空催化组（无催化剂、无编码电路），即无条件反应的「编程电路0」。"""
    return (
        not group.get("catalysts")
        and not group.get("catalystChoices")
        and not group.get("circuit")
    )


def _empty_group_triggered_by_other(empty_keys, other_keys):
    """单向判定：其它组配方执行时是否会连带触发空催化组配方。

    空催化组（编程电路0）无催化剂/电路要求，条件齐备即无条件执行，因此当
    空组配方的材料输入 ⊆ 其它组配方的材料输入时，做其它组配方会顺带触发它。
    反向（做空组配方连带触发其它组配方）不再统计。
    """
    return empty_keys.issubset(other_keys)


def _log_empty_group_conflicts(
    machine_name, recipes, groups, empty_group_indexes,
    group_recipe_counts, empty_conflicts, abstract_groups,
    to_console=False,
):
    """把「空催化组（编程电路0）↔ 其它催化组」的串配方单向全量检查写入日志。

    空组无任何触发条件，与它冲突的组不能并入同一输入组；这里对所有配方对做了
    全量判定，只统计「做其它组配方会连带触发空组配方」这一个方向，按
    「空组 × 对方组」汇总对数（示例条数有上限，计数为全量）。
    to_console 为假时以 DEBUG 级别只写日志文件，不刷屏终端（默认）。
    """
    if not empty_group_indexes:
        return
    level = logging.INFO if to_console else logging.DEBUG
    logger.log(
        level,
        "========== 空催化组（编程电路0）与其它催化组的串配方单向检查：机器 %s ==========",
        machine_name,
    )
    if not empty_conflicts:
        logger.log(
            level,
            "机器 %s 空催化组（编程电路0）与其它催化组无串配方。", machine_name,
        )
        return

    total_pairs = sum(record["count"] for record in empty_conflicts.values())
    for (empty_index, other_index), record in sorted(empty_conflicts.items()):
        logger.log(
            level,
            "编程电路0（%d 个配方）<-> [%s]（%d 个配方）：%d 对配方构成串配方",
            group_recipe_counts[empty_index],
            readable_group_label(groups[other_index], abstract_groups),
            group_recipe_counts[other_index],
            record["count"],
        )
        logger.log(level, "  方向：%s", _EMPTY_CONFLICT_DIRECTION_TEXT)
        samples = record["pairs"][:_EMPTY_CONFLICT_SAMPLE_LIMIT]
        for sample in samples:
            empty_recipe = recipes[sample["empty_recipe"]]
            other_recipe = recipes[sample["other_recipe"]]
            logger.log(
                level,
                "  编程电路0配方 %s: %s",
                empty_recipe.get("recipeId", sample["empty_recipe"]),
                recipe_io_text(empty_recipe),
            )
            logger.log(
                level,
                "  其它组配方 %s: %s",
                other_recipe.get("recipeId", sample["other_recipe"]),
                recipe_io_text(other_recipe),
            )
        omitted = record["count"] - len(samples)
        if omitted > 0:
            logger.log(
                level,
                "  （以上仅列前 %d 对示例，其余 %d 对从略；全量见 %s）",
                len(samples),
                omitted,
                "empty_group_conflicts.json",
            )
    logger.log(
        level,
        "机器 %s 空催化组检查完成：%d 个空组节点与 %d 个催化组存在串配方，共 %d 对配方。",
        machine_name, len(empty_group_indexes), len(empty_conflicts), total_pairs,
    )


def empty_group_conflict_report(
    machine_name, recipes, groups, empty_group_indexes,
    group_recipe_counts, empty_conflicts, abstract_groups,
):
    """生成「空催化组（编程电路0）↔ 其它催化组」单向串配方的**全量**报告。

    与日志不同，这里列出每一对配方（recipeId + 输入输出摘要），供单独落盘
    （`Output/filtered_recipes/empty_group_conflicts.json`）。
    """
    def recipe_entry(recipe_index):
        recipe = recipes[recipe_index]
        return {
            "recipe_id": recipe.get("recipeId", recipe_index),
            "io": recipe_io_text(recipe),
        }

    conflicts = []
    for (empty_index, other_index), record in sorted(empty_conflicts.items()):
        conflicts.append(
            {
                "empty_group_index": empty_index,
                "other_group_index": other_index,
                "empty_group_label": readable_group_label(
                    groups[empty_index], abstract_groups
                ),
                "other_group_label": readable_group_label(
                    groups[other_index], abstract_groups
                ),
                "empty_group": groups[empty_index],
                "other_group": groups[other_index],
                "empty_group_recipe_count": group_recipe_counts[empty_index],
                "other_group_recipe_count": group_recipe_counts[other_index],
                "pair_count": record["count"],
                "pairs": [
                    {
                        "empty_recipe": recipe_entry(pair["empty_recipe"]),
                        "other_recipe": recipe_entry(pair["other_recipe"]),
                    }
                    for pair in record["pairs"]
                ],
            }
        )

    return {
        "machine": machine_name,
        "direction": "other_to_empty",
        "direction_text": _EMPTY_CONFLICT_DIRECTION_TEXT,
        "empty_group_node_count": len(empty_group_indexes),
        "conflict_group_pair_count": len(conflicts),
        "pair_count": sum(conflict["pair_count"] for conflict in conflicts),
        "conflicts": conflicts,
    }


def build_conflict_graph(
    machine_name, recipes, abstract_groups=None, empty_conflict_console=False,
    empty_conflict_report=None,
):
    """Build the catalyst-group conflict graph from grouped recipes.

    Candidate discovery uses orphan-aware bitsets over material input slots.
    Each target recipe slot is an OR-set; candidate sources are intersected by
    their ability to supply at least one member.  A final bipartite slot match
    confirms that different target slots do not consume the same source group.

    Output compatibility:
    - nodes and adjacency_matrix remain unchanged for graph coloring;
    - edges and build statistics are added for sparse consumers.
    """

    groups = []
    group_indexes = {}
    recipe_group_ids = []

    for recipe in recipes:
        current_ids = []
        effective_groups = recipe.get("catalystGroups") or [
            {"catalysts": [], "circuit": None}
        ]
        for group in effective_groups:
            signature = group_signature(group)
            if signature not in group_indexes:
                group_indexes[signature] = len(groups)
                groups.append(deepcopy(group))
            current_ids.append(group_indexes[signature])
        recipe_group_ids.append(current_ids)

    node_ids = [f"group_{index:04d}" for index in range(len(groups))]
    recipe_input_slots = [_recipe_input_slots(recipe) for recipe in recipes]
    recipe_signatures = [recipe_io_signature(recipe) for recipe in recipes]
    recipe_outputs = [_recipe_output_keys(recipe) for recipe in recipes]

    adjacency_matrix = [[0 for _ in groups] for _ in groups]
    edges = set()
    empty_group_indexes = {
        index for index, group in enumerate(groups) if _is_empty_group(group)
    }
    group_recipe_counts = Counter(
        group_index for ids in recipe_group_ids for group_index in ids
    )
    empty_conflicts = {}
    seen_pairs = set()
    self_conflict_count = 0

    # Recursive index: active recipes have at least one catalyst group node.
    active_mask = 0
    all_supply_masks = {}
    for recipe_index, slots in enumerate(recipe_input_slots):
        if not recipe_group_ids[recipe_index]:
            continue
        active_mask |= 1 << recipe_index
        for slot in slots:
            for key in slot:
                all_supply_masks[key] = (
                    all_supply_masks.get(key, 0) | (1 << recipe_index)
                )

    output_signature_masks = {}
    io_signature_masks = {}
    for recipe_index, recipe in enumerate(recipes):
        if not recipe_group_ids[recipe_index]:
            continue
        output_signature = recipe_outputs[recipe_index]
        output_signature_masks[output_signature] = (
            output_signature_masks.get(output_signature, 0)
            | (1 << recipe_index)
        )
        io_signature = recipe_signatures[recipe_index]
        io_signature_masks[io_signature] = (
            io_signature_masks.get(io_signature, 0)
            | (1 << recipe_index)
        )

    def record_empty_conflict(empty_recipe, other_recipe, empty_index, other_index):
        """Record other-recipe → empty-group-recipe, using exact slot semantics."""
        if not _slots_can_match(
            recipe_input_slots[other_recipe],
            recipe_input_slots[empty_recipe],
        ):
            return
        record = empty_conflicts.setdefault(
            (empty_index, other_index), {"count": 0, "pairs": []}
        )
        record["count"] += 1
        record["pairs"].append(
            {"empty_recipe": empty_recipe, "other_recipe": other_recipe}
        )

    active_recipe_indices = [
        index for index in range(len(recipes)) if recipe_group_ids[index]
    ]
    valid_target_indices = [
        index
        for index in active_recipe_indices
        if not _has_orphan_input(recipes[index])
    ]
    orphan_targets_pruned = len(active_recipe_indices) - len(valid_target_indices)
    candidate_pair_checks = 0
    exact_conflict_pairs = 0

    for target_index in valid_target_indices:
        target_slots = recipe_input_slots[target_index]
        source_mask = active_mask

        # Optimistic recall: each target slot must have at least one source
        # recipe able to supply one of its alternatives.
        for target_slot in target_slots:
            slot_mask = 0
            for key in target_slot:
                slot_mask |= all_supply_masks.get(key, 0)
            source_mask &= slot_mask
            if not source_mask:
                break
        if not source_mask:
            continue

        source_mask &= ~(1 << target_index)
        source_mask &= ~output_signature_masks.get(
            recipe_outputs[target_index], 0
        )
        source_mask &= ~io_signature_masks.get(
            recipe_signatures[target_index], 0
        )

        while source_mask:
            lowest = source_mask & -source_mask
            source_index = lowest.bit_length() - 1
            source_mask ^= lowest
            pair = (
                (source_index, target_index)
                if source_index < target_index
                else (target_index, source_index)
            )
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            candidate_pair_checks += 1

            source_to_target = _slots_can_match(
                recipe_input_slots[source_index], target_slots
            )
            target_to_source = _slots_can_match(
                target_slots, recipe_input_slots[source_index]
            )
            if not source_to_target and not target_to_source:
                continue
            exact_conflict_pairs += 1

            first, second = pair
            if source_index < target_index:
                left_to_right = source_to_target
                right_to_left = target_to_source
            else:
                left_to_right = target_to_source
                right_to_left = source_to_target

            first_groups = set(recipe_group_ids[first])
            second_groups = set(recipe_group_ids[second])

            # Self conflict under a shared catalyst group.
            for group_index in first_groups & second_groups:
                self_conflict_count += 1
                left_id = recipes[first].get("recipeId", first)
                right_id = recipes[second].get("recipeId", second)
                directions = []
                if left_to_right:
                    directions.append("left_to_right")
                if right_to_left:
                    directions.append("right_to_left")
                logger.warning(
                    "机器 %s 发现催化组自身冲突: 配方 %s 与 %s，催化组 [%s]，方向=%s",
                    machine_name, left_id, right_id,
                    group_label(groups[group_index]), ",".join(directions),
                )
                logger.info(
                    "  配方 %s: %s",
                    left_id,
                    recipe_io_text(recipes[first]),
                )
                logger.info(
                    "  配方 %s: %s",
                    right_id,
                    recipe_io_text(recipes[second]),
                )

            # Empty-group one-way report: other recipe → empty recipe.
            if empty_group_indexes:
                first_empty = first_groups & empty_group_indexes
                second_empty = second_groups & empty_group_indexes
                first_other = first_groups - empty_group_indexes
                second_other = second_groups - empty_group_indexes
                for empty_index in first_empty:
                    for other_index in second_other:
                        if (
                            empty_index in second_empty
                            and other_index in first_other
                        ):
                            continue
                        record_empty_conflict(
                            first, second, empty_index, other_index
                        )
                for empty_index in second_empty:
                    for other_index in first_other:
                        record_empty_conflict(
                            second, first, empty_index, other_index
                        )

            # Undirected group edge for every cross-group recipe pair.
            for first_group in first_groups:
                for second_group in second_groups:
                    if first_group == second_group:
                        continue
                    lo, hi = (
                        (first_group, second_group)
                        if first_group < second_group
                        else (second_group, first_group)
                    )
                    edges.add((lo, hi))

    for lo, hi in edges:
        adjacency_matrix[hi][lo] = 1

    _log_empty_group_conflicts(
        machine_name, recipes, groups, empty_group_indexes,
        group_recipe_counts, empty_conflicts, abstract_groups or {},
        empty_conflict_console,
    )
    if empty_conflict_report is not None:
        empty_conflict_report.update(
            empty_group_conflict_report(
                machine_name, recipes, groups, empty_group_indexes,
                group_recipe_counts, empty_conflicts, abstract_groups or {},
            )
        )

    edge_count = len(edges)
    implicit_zero_group = any(
        not recipe.get("catalystGroups")
        for recipe in recipes
    )
    graph = {
        "machine": machine_name,
        "nodes": [
            {
                "id": node_ids[index],
                "group": deepcopy(group),
                "implicit": bool(
                    implicit_zero_group and _is_empty_group(group)
                ),
            }
            for index, group in enumerate(groups)
        ],
        "adjacency_matrix": adjacency_matrix,
        "edges": [
            {
                "source": node_ids[lo],
                "target": node_ids[hi],
            }
            for lo, hi in sorted(edges)
        ],
        "empty_group_conflict_counts": [
            {
                "empty_group_index": empty_index,
                "other_group_index": other_index,
                "pair_count": record["count"],
            }
            for (empty_index, other_index), record in sorted(empty_conflicts.items())
        ],
        "build_stats": {
            "active_recipe_count": len(active_recipe_indices),
            "valid_target_count": len(valid_target_indices),
            "orphan_targets_pruned": orphan_targets_pruned,
            "candidate_pair_checks": candidate_pair_checks,
            "exact_conflict_pairs": exact_conflict_pairs,
            "edge_count": edge_count,
            "self_conflict_count": self_conflict_count,
        },
    }
    logger.debug(
        "机器 %s 冲突图构建完成: %d 个节点，%d 条边，%d 个自冲突；"
        "orphan 目标剪枝 %d，候选配方对 %d，确认配方对 %d。",
        machine_name,
        len(groups),
        edge_count,
        self_conflict_count,
        orphan_targets_pruned,
        candidate_pair_checks,
        exact_conflict_pairs,
    )
    return graph
