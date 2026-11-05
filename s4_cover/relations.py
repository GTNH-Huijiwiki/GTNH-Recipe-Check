"""催化集关系分析。"""

from __future__ import annotations

from s0_contracts.schema import CatalystSet, Recipe, make_unconditional_set


def _catalyst_identities(catalyst_set):
    return frozenset((item.kind, item.key) for item in catalyst_set.catalysts)


def _circuit_identity(catalyst_set):
    circuit = catalyst_set.circuit
    return (circuit.kind, circuit.key) if circuit else None


def covers(left, right):
    """判断 left 是否覆盖 right。"""
    if left.kind == "unconditional" or right.kind == "unconditional":
        return left.kind == right.kind == "unconditional"
    if _circuit_identity(left) != _circuit_identity(right):
        return False
    return _catalyst_identities(right) <= _catalyst_identities(left)


def classify(left, right):
    """返回两个催化集之间的固定关系。"""
    if left.set_id == right.set_id:
        return "equal"
    if left.kind == "unconditional" or right.kind == "unconditional":
        return "equal" if left.kind == right.kind else "disjoint"
    if covers(left, right):
        return "covers"
    if covers(right, left):
        return "covered_by"
    if _circuit_identity(left) == _circuit_identity(right):
        if _catalyst_identities(left) & _catalyst_identities(right):
            return "intersects"
    return "disjoint"


def collect_catalyst_sets(recipes):
    """收集全部独特催化集，并补充无条件催化集。"""
    result = {}
    for recipe in recipes:
        if not recipe.catalyst_group.sets:
            catalyst_set = make_unconditional_set()
            result[catalyst_set.set_id] = catalyst_set
            continue
        for catalyst_set in recipe.catalyst_group.sets:
            result[catalyst_set.set_id] = catalyst_set
    return dict(sorted(result.items()))


def coverage_by_set(recipes, sets):
    """计算每个催化集能够覆盖的 recipeId 集合。"""
    coverage = {set_id: set() for set_id in sets}
    for recipe in recipes:
        required_sets = recipe.catalyst_group.sets
        if not required_sets:
            unconditional = make_unconditional_set()
            if unconditional.set_id in coverage:
                coverage[unconditional.set_id].add(recipe.recipe_id)
            continue
        for candidate in sets.values():
            if any(covers(candidate, required) for required in required_sets):
                coverage[candidate.set_id].add(recipe.recipe_id)
    return coverage


def _classes_by_coverage(sets, coverage):
    grouped = {}
    for set_id in sets:
        grouped.setdefault(frozenset(coverage[set_id]), []).append(set_id)
    entries = []
    for index, (covered, members) in enumerate(
        sorted(grouped.items(), key=lambda item: (-len(item[0]), item[1]))
    ):
        entries.append(
            {
                "class_id": f"class_{index:04d}",
                "set_ids": tuple(sorted(members)),
                "coverage": covered,
            }
        )
    return entries


def _components(sets, coverage):
    ids = list(sets)
    parent = {set_id: set_id for set_id in ids}

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left, right):
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            if coverage[left] & coverage[right]:
                union(left, right)

    grouped = {}
    for set_id in ids:
        grouped.setdefault(find(set_id), []).append(set_id)
    return [
        tuple(sorted(members))
        for members in sorted(grouped.values(), key=lambda item: sorted(item))
    ]


def _direct_containments(classes):
    children = {entry["class_id"]: [] for entry in classes}
    parents = {entry["class_id"]: [] for entry in classes}
    for left in classes:
        for right in classes:
            if left is right or not (left["coverage"] < right["coverage"]):
                continue
            has_intermediate = any(
                middle is not left
                and middle is not right
                and left["coverage"] < middle["coverage"] < right["coverage"]
                for middle in classes
            )
            if not has_intermediate:
                children[left["class_id"]].append(right["class_id"])
                parents[right["class_id"]].append(left["class_id"])
    return children, parents


def _top_level_containments(classes):
    """统计每个最顶层等价类包含的全部下级集合。"""
    result = []
    for top in classes:
        if any(top["coverage"] < other["coverage"] for other in classes):
            continue
        contained = [
            entry for entry in classes if entry["coverage"] < top["coverage"]
        ]
        result.append(
            {
                "topClassId": top["class_id"],
                "topSetIds": list(top["set_ids"]),
                "containedClassIds": [entry["class_id"] for entry in contained],
                "containedSetIds": sorted(
                    set_id
                    for entry in contained
                    for set_id in entry["set_ids"]
                ),
                "coverageSize": len(top["coverage"]),
            }
        )
    return result


def _containment_chains(classes):
    children, parents = _direct_containments(classes)
    class_by_id = {entry["class_id"]: entry for entry in classes}
    roots = sorted(
        class_id for class_id, values in parents.items() if not values
    )
    chains = []

    def walk(class_id, path):
        next_ids = sorted(children[class_id])
        if not next_ids:
            if len(path) > 1:
                chains.append(tuple(path))
            return
        for next_id in next_ids:
            walk(next_id, path + [next_id])

    for root in roots:
        walk(root, [root])
    return chains


def analyze_relations(sets, coverage):
    """构建关系边、等价类、连通分量和纯包含链。"""
    ids = list(sets)
    edges = []
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            edges.append(
                {
                    "left": left_id,
                    "right": right_id,
                    "relation": classify(sets[left_id], sets[right_id]),
                }
            )

    classes = _classes_by_coverage(sets, coverage)
    chains = _containment_chains(classes)
    return {
        "edges": edges,
        "equivalenceClasses": [
            {
                "classId": entry["class_id"],
                "setIds": list(entry["set_ids"]),
                "coverageSize": len(entry["coverage"]),
            }
            for entry in classes
        ],
        "equivalenceGroups": [
            {
                "classId": entry["class_id"],
                "setIds": list(entry["set_ids"]),
                "coverageSize": len(entry["coverage"]),
            }
            for entry in classes
            if len(entry["set_ids"]) > 1
        ],
        "components": [list(component) for component in _components(sets, coverage)],
        "containmentChains": [list(chain) for chain in chains],
        "topLevelContainments": _top_level_containments(classes),
    }
