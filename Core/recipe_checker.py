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


def _circuit_order_key(item):
    """编程电路的排序键：无编号的（如占位用的编程电路0）排最前，其余按编号升序。"""
    tail = item.get("key", "").rsplit(":", 1)[-1]
    return (1, int(tail)) if tail.isdigit() else (0, 0)


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


def build_conflict_graph(machine_name, recipes):
    """构建催化组冲突图，并把自冲突写入日志。

    节点 = 去重后的催化组；边 = 会被同一缓存同时触发、且构成串配方的两组。

    冲突方向判定只依赖材料输入（item/fluid）集合的包含关系：催化剂/电路属于
    独立类型，不会替代材料输入，故「输入互相包含 ⇔ 存在串配方方向」。据此用
    「输入集合 → 配方」子集索引枚举互相包含的配方对，避免 O(R^2) 两两判定。
    输出相同的配方对视为良性连带，不记录。
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
                    # 组间边：两配方分属的不同催化组之间均连边
                    for gi in first_groups:
                        for gj in second_groups:
                            if gi == gj:
                                continue
                            lo, hi = (gi, gj) if gi < gj else (gj, gi)
                            adjacency_matrix[hi][lo] = 1

    for index in range(len(groups)):
        adjacency_matrix[index][index] = 0

    edge_count = sum(sum(row) for row in adjacency_matrix)
    graph = {
        "machine": machine_name,
        "nodes": [
            {"id": node_ids[index], "group": deepcopy(group)}
            for index, group in enumerate(groups)
        ],
        "adjacency_matrix": adjacency_matrix,
    }
    logger.debug(
        "机器 %s 冲突图构建完成: %d 个节点，%d 条边，%d 个自冲突。",
        machine_name, len(groups), edge_count, self_conflict_count,
    )
    return graph
