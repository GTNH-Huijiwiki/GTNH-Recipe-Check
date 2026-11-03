"""配方冲突检查核心逻辑，供主流程和配方复核脚本共用。"""

import logging
from collections import Counter
from copy import deepcopy
from itertools import combinations

logger = logging.getLogger(__name__)


def item_signature(item):
    """物品的唯一身份签名：key（数量不参与身份判定）。"""
    return item.get("key", "")


def group_signature(group):
    catalysts = tuple(
        sorted(item_signature(item) for item in group.get("catalysts", []))
    )
    circuit = group.get("circuit") or {}
    return catalysts, item_signature(circuit) if circuit else None


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


# 单向检查说明：其它催化组配方执行时，空组配方会被连带触发
_EMPTY_CONFLICT_DIRECTION_TEXT = (
    "其它组配方 → 编程电路0（做其它催化组配方会连带触发空催化组配方）"
)

# 每个「空组 × 其它组」最多保留的样板例数（计数始终是全量）
_EMPTY_CONFLICT_SAMPLE_LIMIT = 3


def _is_empty_group(group):
    """是否为空催化组（无催化剂、无编程电路），即无条件反应的「编程电路0」。"""
    return (not group.get("catalysts")) and (not group.get("circuit"))


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
    """构建催化组冲突图，并把自冲突、空催化组串配方写入日志。

    节点 = 去重后的催化组；边 = 会被同一缓存同时触发、且构成串配方的两组。

    冲突方向判定只依赖材料输入（item/fluid）集合的包含关系：催化剂/电路属于
    独立类型，不会替代材料输入，故「输入互相包含 ⇔ 存在串配方方向」。据此用
    「输入集合 → 配方」子集索引枚举互相包含的配方对，避免 O(R^2) 两两判定。
    输出相同的配方对视为良性连带，不记录。

    空催化组（无催化剂、无电路，即编程电路0）是对其它催化组的特殊条件：它自身
    无任何触发条件，因此本函数额外对它与其他催化组的串配方做**单向全量检查**：
    只统计「其它催化组配方执行时会连带触发空组配方」（空组配方输入 ⊆ 对方配方
    输入）的方向，按「空组 × 对方组」汇总对数：日志只列前几条示例（计数为全量）。
    empty_conflict_console 控制是否同时在终端显示（默认只写日志文件）；
    empty_conflict_report 传入 dict 时，其中会填入全量报告（见
    `empty_group_conflict_report`），供调用方单独落盘。
    """
    groups = []
    group_indexes = {}
    recipe_group_ids = []

    for recipe in recipes:
        current_ids = []
        for group in recipe.get("catalystGroups", []):
            signature = group_signature(group)
            if signature not in group_indexes:
                group_indexes[signature] = len(groups)
                groups.append(deepcopy(group))
            current_ids.append(group_indexes[signature])
        recipe_group_ids.append(current_ids)

    node_ids = [f"group_{index:04d}" for index in range(len(groups))]
    self_conflict_count = 0
    recipe_signatures = [recipe_io_signature(recipe) for recipe in recipes]
    # 配方材料输入（item/fluid）条件集
    recipe_input_keys = [_recipe_input_keys(recipe) for recipe in recipes]
    # 配方输出集合，用于「输出相同则豁免」的良性连带过滤
    recipe_outputs = [_recipe_output_keys(recipe) for recipe in recipes]
    adjacency_matrix = [[0 for _ in groups] for _ in groups]

    # 空催化组（编程电路0）相关的串配方统计：{(空组下标, 对方组下标) -> 记录}
    empty_group_indexes = {
        index for index, group in enumerate(groups) if _is_empty_group(group)
    }
    group_recipe_counts = Counter(
        group_index for ids in recipe_group_ids for group_index in ids
    )
    empty_conflicts = {}

    def record_empty_conflict(empty_recipe, other_recipe, empty_index, other_index):
        """记录一对「空组配方 ↔ 其它组配方」的串配方（单向：其它组 → 空组）。

        仅当空组配方的材料输入 ⊆ 其它组配方的材料输入（做其它组配方会连带
        触发空组配方）时才计入；反向不统计。全量配方对写入 `pairs`
        （日志只列前 _EMPTY_CONFLICT_SAMPLE_LIMIT 条）。
        """
        if not _empty_group_triggered_by_other(
            recipe_input_keys[empty_recipe], recipe_input_keys[other_recipe]
        ):
            return
        record = empty_conflicts.setdefault(
            (empty_index, other_index), {"count": 0, "pairs": []}
        )
        record["count"] += 1
        record["pairs"].append(
            {"empty_recipe": empty_recipe, "other_recipe": other_recipe}
        )

    # 输入集合 -> 配方下标列表（子集命中索引）
    input_index = {}
    for recipe_index, keys in enumerate(recipe_input_keys):
        input_index.setdefault(keys, []).append(recipe_index)

    seen_pairs = set()
    for right_index in range(len(recipes)):
        right_keys = recipe_input_keys[right_index]
        right_sig = recipe_signatures[right_index]
        items = list(right_keys)
        # 枚举 right 输入的全部子集 T：任何输入恰为 T 的配方 left 都满足 left⊆right，
        # 即二者材料输入互相包含，构成互串配方对（等价于原方向判定为真）。
        for subset_size in range(len(items) + 1):
            for combo in combinations(items, subset_size):
                subset = frozenset(combo)
                for left_index in input_index.get(subset, ()):
                    if left_index == right_index:
                        continue
                    if recipe_signatures[left_index] == right_sig:
                        continue
                    if left_index < right_index:
                        first, second = left_index, right_index
                    else:
                        first, second = right_index, left_index
                    # 输出完全相同 → 输入上的连带无害，豁免（不记自冲突、不连边）
                    if recipe_outputs[first] == recipe_outputs[second]:
                        continue
                    pair = (first, second)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    first_groups = set(recipe_group_ids[first])
                    second_groups = set(recipe_group_ids[second])
                    # 自冲突：公共催化组下两配方互相包含，逐对判定并记录（不跳过）
                    for group_index in first_groups & second_groups:
                        self_conflict_count += 1
                        left_id = recipes[first].get("recipeId", first)
                        right_id = recipes[second].get("recipeId", second)
                        directions = []
                        if recipe_input_keys[second].issubset(
                            recipe_input_keys[first]
                        ):
                            directions.append("left_to_right")
                        if recipe_input_keys[first].issubset(
                            recipe_input_keys[second]
                        ):
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
                    # 空催化组（编程电路0）与其它催化组的串配方：全量记录
                    if empty_group_indexes:
                        first_empty = first_groups & empty_group_indexes
                        second_empty = second_groups & empty_group_indexes
                        first_other = first_groups - empty_group_indexes
                        second_other = second_groups - empty_group_indexes
                        for empty_index in first_empty:
                            for other_index in second_other:
                                # 两侧都含该组对时只在后一个循环里记一次，避免重复计数
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
                    # 组间边：两配方分属的不同催化组之间均连边
                    for gi in first_groups:
                        for gj in second_groups:
                            if gi == gj:
                                continue
                            lo, hi = (gi, gj) if gi < gj else (gj, gi)
                            adjacency_matrix[hi][lo] = 1

    for index in range(len(groups)):
        adjacency_matrix[index][index] = 0

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

    edge_count = sum(sum(row) for row in adjacency_matrix)
    graph = {
        "machine": machine_name,
        "nodes": [
            {"id": node_ids[index], "group": deepcopy(group)}
            for index, group in enumerate(groups)
        ],
        "adjacency_matrix": adjacency_matrix,
        "empty_group_conflict_counts": [
            {
                "empty_group_index": empty_index,
                "other_group_index": other_index,
                "pair_count": record["count"],
            }
            for (empty_index, other_index), record in sorted(empty_conflicts.items())
        ],
    }
    logger.debug(
        "机器 %s 冲突图构建完成: %d 个节点，%d 条边，%d 个自冲突。",
        machine_name, len(groups), edge_count, self_conflict_count,
    )
    return graph
