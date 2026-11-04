"""冲突图着色及着色后催化组合并。"""

import hashlib
import random
from collections import Counter
from copy import deepcopy

from Core.recipe_checker import (
    group_output_order_key,
    group_signature,
    item_signature,
)

# 取色探索：每步以 _COLOR_EXPLORE_PROB 的概率不取最小可用色，
# 而从前 _MAX_COLOR_CHOICES 个可用色中随机取一个，以增加解的多样性。
_COLOR_EXPLORE_PROB = 0.35
_MAX_COLOR_CHOICES = 3


def edge_exists(adjacency_matrix, left, right):
    if left < right:
        left, right = right, left
    return bool(adjacency_matrix[left][right])


def _greedy_round(
    adjacency_matrix, node_count, seed, max_class_size=None, explore=True,
):
    """一轮随机化 DSATUR 贪心，返回长度 node_count 的颜色数组。

    选点：DSATUR（饱和度、度数），平局用随机数打破。
    取色：explore 为假时为纯 first-fit（保底）；为真时以 _COLOR_EXPLORE_PROB
    的概率改取前几个可用色之一，否则各轮只会收敛到同一个目标值，失去探索意义。
    max_class_size 限制单个颜色类的节点数（搜索期启发式约束）。
    """
    randomizer = random.Random(seed)
    explore_prob = _COLOR_EXPLORE_PROB if explore else 0.0
    colors = [-1] * node_count
    while -1 in colors:
        uncolored = [index for index, color in enumerate(colors) if color < 0]

        def selection_key(index):
            neighbor_colors = {
                colors[neighbor]
                for neighbor in range(node_count)
                if colors[neighbor] >= 0
                and edge_exists(adjacency_matrix, index, neighbor)
            }
            degree = sum(
                edge_exists(adjacency_matrix, index, neighbor)
                for neighbor in range(node_count)
                if neighbor != index
            )
            return len(neighbor_colors), degree, randomizer.random()

        selected = max(uncolored, key=selection_key)
        forbidden = {
            colors[neighbor]
            for neighbor in range(node_count)
            if colors[neighbor] >= 0
            and edge_exists(adjacency_matrix, selected, neighbor)
        }
        color_sizes = Counter(colors)
        allowed = []
        candidate = 0
        while len(allowed) < _MAX_COLOR_CHOICES:
            if candidate not in forbidden and (
                max_class_size is None
                or color_sizes[candidate] < max_class_size
            ):
                allowed.append(candidate)
            candidate += 1
        if explore_prob > 0 and randomizer.random() < explore_prob:
            colors[selected] = randomizer.choice(allowed)
        else:
            colors[selected] = allowed[0]
    return colors


def _recipe_class_sizes(colors, node_coverages):
    """各颜色类（合并后的催化组）覆盖配方并集大小，按从大到小排序。

    作为着色的次要目标：从最大组起逐位比较，先更大者更差。
    """
    class_recipes = {}
    for node_index, color in enumerate(colors):
        class_recipes.setdefault(color, set()).update(node_coverages[node_index])
    return sorted(
        (len(recipes) for recipes in class_recipes.values()), reverse=True
    )


# 节点数档位扫描：单色类最大节点数从 _SIZE_SWEEP_MIN 起逐 1 放宽
_SIZE_SWEEP_MIN = 2


def _size_sweep_limits(max_class_size, node_count, sweep_max_size):
    """生成「单色类最大节点数」档位序列（从小到大，None 表示不限制）。

    用户上限 max_class_size 为 None 时扫到节点数，否则不超过该上限；
    sweep_max_size 限制逐 1 扫描的最大档位（None 表示不限）。
    仅当用户上限为 None 时末尾追加 None 档（不限制）。
    """
    upper = node_count if max_class_size is None else max_class_size
    upper = max(_SIZE_SWEEP_MIN, min(upper, max(_SIZE_SWEEP_MIN, node_count)))
    limits = [
        size
        for size in range(_SIZE_SWEEP_MIN, upper + 1)
        if sweep_max_size is None or size <= sweep_max_size
    ]
    if max_class_size is None:
        limits.append(None)
    return limits


def color_graph(
    graph, rounds, max_class_size=None, node_coverages=None,
    size_sweep=False, sweep_max_size=12,
):
    """多轮随机化 DSATUR 贪心着色，只保留并返回全局最优的一轮。

    最优准则：催化组数（颜色数）最少；并列时，把各催化组覆盖的配方数从大到小
    排成列表逐位比较，先更大者更差（即先最小化最大组，再最小化第二大组，依此类推）。
    node_coverages：每个节点覆盖的配方下标集合（与 graph["nodes"] 同序）；
    为 None 时次准则退化为颜色类内的节点数。

    size_sweep：把「单色类最大节点数」从 _SIZE_SWEEP_MIN 逐 1 放宽多档扫描，
    每档跑 rounds 轮。限制节点数只是搜索期的启发式约束，不改变目标函数：受限档位
    同样是合法着色，因此与不限制档公平比较，最终仍取全局最优（含不限制档）。
    """
    nodes = graph["nodes"]
    adjacency_matrix = graph["adjacency_matrix"]
    node_count = len(nodes)
    if not node_count:
        return {
            "node_colors": {},
            "colors": [],
            "color_count": 0,
            "largest_color_class": 0,
            "largest_recipe_class": 0,
            "recipe_class_sizes": [],
            "max_class_size": max_class_size,
            "best_max_class_size": None,
            "swept_max_class_sizes": [],
            "best_round": None,
        }

    limits = (
        _size_sweep_limits(max_class_size, node_count, sweep_max_size)
        if size_sweep
        else [max_class_size]
    )
    best = None
    for limit_index, limit in enumerate(limits):
        for round_index in range(max(1, rounds)):
            seed = limit_index * max(1, rounds) + round_index
            colors = _greedy_round(
                adjacency_matrix, node_count, seed, limit,
                explore=round_index > 0,
            )
            class_sizes = Counter(colors)
            largest_class = max(class_sizes.values())
            if node_coverages is None:
                recipe_class_sizes = sorted(class_sizes.values(), reverse=True)
            else:
                recipe_class_sizes = _recipe_class_sizes(colors, node_coverages)
            objective = (len(class_sizes), tuple(recipe_class_sizes))
            if best is None or objective < best["objective"]:
                best = {
                    "objective": objective,
                    "colors": colors,
                    "round": round_index,
                    "class_size_limit": limit,
                    "largest_color_class": largest_class,
                    "recipe_class_sizes": recipe_class_sizes,
                }

    color_nodes = {}
    for node_index, color in enumerate(best["colors"]):
        color_nodes.setdefault(color, []).append(node_index)
    colors_output = [
        {
            "id": f"color_{color:04d}",
            "node_ids": [nodes[index]["id"] for index in indexes],
            "node_indexes": indexes,
            "size": len(indexes),
        }
        for color, indexes in sorted(color_nodes.items())
    ]
    return {
        "node_colors": {
            nodes[index]["id"]: f"color_{color:04d}"
            for index, color in enumerate(best["colors"])
        },
        "colors": colors_output,
        "color_count": best["objective"][0],
        "largest_color_class": best["largest_color_class"],
        "largest_recipe_class": (
            best["recipe_class_sizes"][0] if best["recipe_class_sizes"] else 0
        ),
        "recipe_class_sizes": best["recipe_class_sizes"],
        "max_class_size": max_class_size,
        "best_max_class_size": best["class_size_limit"],
        "swept_max_class_sizes": limits,
        "best_round": best["round"],
    }


def node_coverages(recipes, graph):
    """计算每个节点（催化组）覆盖的配方下标集合，与 graph["nodes"] 同序。"""
    index_by_signature = {
        group_signature(node["group"]): index
        for index, node in enumerate(graph["nodes"])
    }
    coverages = [set() for _ in graph["nodes"]]
    for recipe_index, recipe in enumerate(recipes):
        groups = recipe.get("catalystGroups") or [
            {"catalysts": [], "circuit": None}
        ]
        for group in groups:
            node_index = index_by_signature.get(group_signature(group))
            if node_index is not None:
                coverages[node_index].add(recipe_index)
    return [frozenset(coverage) for coverage in coverages]


def graph_fingerprint(graph):
    """冲突图指纹：节点催化组签名顺序 + 邻接矩阵。

    用于判断历史着色方案是否仍适用于当前冲突图；图未变时同一方案仍然合法。
    """
    digest = hashlib.sha1()
    for node in graph["nodes"]:
        digest.update(repr(group_signature(node["group"])).encode("utf-8"))
        digest.update(b"\x1f")
    for row in graph["adjacency_matrix"]:
        digest.update(bytes(row))
    return digest.hexdigest()


_EMPTY_CIRCUIT_0 = {
    "key": "__circuit_0",
    "originalName": "Programmed Circuit",
    "translatedName": "编程电路0",
    "amount": 1,
}


def _is_empty_group(group):
    """是否为无条件反应的特殊组（无催化剂、无编程电路）。"""
    return (not group.get("catalysts")) and (not group.get("circuit"))


def merge_colored_groups(graph, coloring):
    """将同一颜色下的所有催化组合并为一个新的催化组。

    若该颜色包含“无条件反应”的空组节点（无催化剂、无电路），
    则在输出合并组中补一个“编程电路0”占位，表示该槽位也包含无条件反应线。
    注意：这只是着色后输出的表示，不影响此前完成的串配方判定。
    """
    merged = {}
    for color in coloring["colors"]:
        groups = [
            graph["nodes"][index]["group"] for index in color["node_indexes"]
        ]
        catalysts = {}
        circuits = {}
        has_empty = False
        for group in groups:
            if _is_empty_group(group):
                has_empty = True
            for catalyst in group.get("catalysts", []):
                catalysts[item_signature(catalyst)] = deepcopy(catalyst)
            circuit = group.get("circuit")
            if circuit:
                circuits[item_signature(circuit)] = deepcopy(circuit)
        if has_empty:
            circuits[item_signature(_EMPTY_CIRCUIT_0)] = deepcopy(_EMPTY_CIRCUIT_0)

        merged_group = {
            "catalysts": [catalysts[key] for key in sorted(catalysts)],
            "circuit": None,
        }
        if len(circuits) == 1:
            merged_group["circuit"] = next(iter(circuits.values()))
        elif circuits:
            merged_group["circuitOptions"] = [circuits[key] for key in sorted(circuits)]
        merged[color["id"]] = merged_group
    return merged


def apply_coloring(recipes, graph, coloring):
    """将原配方中的节点替换为着色后的催化组。

    每条配方的催化组按输出规则排序（见 `group_output_order_key`）：
    无编程电路的组排最后，其余按组内最小编号升序。
    """
    node_by_signature = {
        group_signature(node["group"]): node["id"] for node in graph["nodes"]
    }
    merged_groups = merge_colored_groups(graph, coloring)
    colored_recipes = deepcopy(recipes)
    for recipe in colored_recipes:
        color_ids = []
        groups = recipe.get("catalystGroups") or [
            {"catalysts": [], "circuit": None}
        ]
        for group in groups:
            node_id = node_by_signature.get(group_signature(group))
            if node_id is None:
                continue
            color_id = coloring["node_colors"][node_id]
            if color_id not in color_ids:
                color_ids.append(color_id)
        recipe["catalystGroups"] = sorted(
            (merged_groups[color_id] for color_id in color_ids),
            key=group_output_order_key,
        )
    return colored_recipes, merged_groups
