"""冲突图着色及着色后催化组合并。"""

import hashlib
import random
from collections import Counter
from copy import deepcopy

from Core.recipe_checker import group_signature, item_signature

# 取色探索：每步以 _COLOR_EXPLORE_PROB 的概率不取最小可用色，
# 而从前 _MAX_COLOR_CHOICES 个可用色中随机取一个，以增加解的多样性。
_COLOR_EXPLORE_PROB = 0.35
_MAX_COLOR_CHOICES = 3


def edge_exists(adjacency_matrix, left, right):
    if left < right:
        left, right = right, left
    return bool(adjacency_matrix[left][right])


def _greedy_round(adjacency_matrix, node_count, round_index, max_class_size=None):
    """一轮随机化 DSATUR 贪心，返回长度 node_count 的颜色数组。

    选点：DSATUR（饱和度、度数），平局用随机数打破。
    取色：第 0 轮为纯 first-fit（保底）；后续轮以 _COLOR_EXPLORE_PROB 的概率
    改取前几个可用色之一——否则各轮只会收敛到同一个目标值，失去探索意义。
    """
    randomizer = random.Random(round_index)
    explore_prob = _COLOR_EXPLORE_PROB if round_index > 0 else 0.0
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


def _largest_recipe_class(colors, node_coverages):
    """各颜色类（合并后的催化组）覆盖配方并集大小的最大值。"""
    class_recipes = {}
    for node_index, color in enumerate(colors):
        class_recipes.setdefault(color, set()).update(node_coverages[node_index])
    return max(len(recipes) for recipes in class_recipes.values())


def color_graph(graph, rounds, max_class_size=None, node_coverages=None):
    """多轮随机化 DSATUR 贪心着色，只保留并返回最优的一轮。

    最优准则：催化组数（颜色数）最少；并列时，单个催化组覆盖的配方数最大值最小。
    node_coverages：每个节点覆盖的配方下标集合（与 graph["nodes"] 同序）；
    为 None 时次准则退化为颜色类内的节点数。
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
            "max_class_size": max_class_size,
            "best_round": None,
        }

    best = None
    for round_index in range(max(1, rounds)):
        colors = _greedy_round(
            adjacency_matrix, node_count, round_index, max_class_size
        )
        class_sizes = Counter(colors)
        largest_class = max(class_sizes.values())
        largest_recipe = (
            largest_class
            if node_coverages is None
            else _largest_recipe_class(colors, node_coverages)
        )
        objective = (len(class_sizes), largest_recipe)
        if best is None or objective < best["objective"]:
            best = {
                "objective": objective,
                "colors": colors,
                "round": round_index,
                "largest_color_class": largest_class,
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
        "largest_recipe_class": best["objective"][1],
        "max_class_size": max_class_size,
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
        for group in recipe.get("catalystGroups", []):
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
    """将原配方中的节点替换为着色后的催化组。"""
    node_by_signature = {
        group_signature(node["group"]): node["id"] for node in graph["nodes"]
    }
    merged_groups = merge_colored_groups(graph, coloring)
    colored_recipes = deepcopy(recipes)
    for recipe in colored_recipes:
        color_ids = []
        for group in recipe.get("catalystGroups", []):
            node_id = node_by_signature.get(group_signature(group))
            if node_id is None:
                continue
            color_id = coloring["node_colors"][node_id]
            if color_id not in color_ids:
                color_ids.append(color_id)
        recipe["catalystGroups"] = [merged_groups[color_id] for color_id in color_ids]
    return colored_recipes, merged_groups
