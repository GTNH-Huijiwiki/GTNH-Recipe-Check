"""s6 图着色与最终输入组翻译。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

import config
from s0_contracts.schema import CatalystSet, Recipe, make_unconditional_set
from s0_contracts.logger import log_machine, log_stage
from s0_contracts.stage import RunContext, StageResult
from s4_cover.relations import covers
from s6_color.strategies.base import ColorContext
from s6_color.strategies.registry import get_strategy

logger = logging.getLogger(__name__)

_UNCONDITIONAL_CIRCUIT_LABEL = "编程电路0"


def _hash_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_manifest(upstream, output_dir):
    artifacts = {}
    for path in sorted(output_dir.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        relative = path.relative_to(output_dir).as_posix()
        artifacts[relative] = "sha256:" + _hash_file(path)
    digest = hashlib.sha256()
    for relative in sorted(artifacts):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifacts[relative].encode("ascii"))
        digest.update(b"\0")
    return {
        "upstream": upstream,
        "artifacts": artifacts,
        "outputHash": "sha256:" + digest.hexdigest(),
    }


def _load_recipes(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Recipe.from_dict(value) for value in data.get("recipes", []))


def _load_sets(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sets = {
        value["setId"]: CatalystSet.from_dict(value)
        for value in data.get("sets", [])
    }
    return (
        sets,
        tuple(data["cover"]["selectedSetIds"]),
        data.get("relations", {}),
    )


def _recipe_coverage(recipes, selected_sets):
    coverage = {set_id: set() for set_id in selected_sets}
    for index, recipe in enumerate(recipes):
        required_sets = recipe.catalyst_group.sets or (
            make_unconditional_set(),
        )
        for set_id, selected in selected_sets.items():
            if any(covers(selected, required) for required in required_sets):
                coverage[set_id].add(index)
    return coverage


def _material_name(item):
    return item.translated_name or item.original_name or item.key


def _circuit_number(item):
    tail = item.key.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _equivalence_index(relations):
    """建立 setId -> 同覆盖等价 setIds 的索引。"""
    result = {}
    for group in relations.get("equivalenceGroups", ()):
        set_ids = tuple(group.get("setIds", ()))
        if len(set_ids) < 2:
            continue
        for set_id in set_ids:
            result[set_id] = set_ids
    return result


def _equivalent_members(catalyst_set, all_sets, equivalence_index):
    set_ids = equivalence_index.get(catalyst_set.set_id)
    if not set_ids:
        return (catalyst_set,)
    members = tuple(all_sets[set_id] for set_id in set_ids if set_id in all_sets)
    return members or (catalyst_set,)


def _catalyst_combination(catalyst_set):
    names = sorted(
        {_material_name(catalyst) for catalyst in catalyst_set.catalysts}
    )
    return "+".join(names) or "无催化剂"


def _group_label(sets, equivalence_index=None, all_sets=None):
    """生成输入组标签，等价催化集展开为 OR 选项。"""
    equivalence_index = equivalence_index or {}
    all_sets = all_sets or {item.set_id: item for item in sets}
    catalyst_labels = set()
    circuits = set()
    for catalyst_set in sets:
        members = _equivalent_members(
            catalyst_set,
            all_sets,
            equivalence_index,
        )
        alternatives = []
        for member in members:
            alternatives.append(_catalyst_combination(member))
            if member.circuit:
                circuits.add(
                    (
                        member.circuit.key,
                        _material_name(member.circuit),
                        _circuit_number(member.circuit),
                    )
                )
            if member.kind == "unconditional":
                circuits.add(
                    ("__circuit_0", _UNCONDITIONAL_CIRCUIT_LABEL, 0)
                )
        catalyst_labels.add(" or ".join(sorted(set(alternatives))))
    catalyst_label = " | ".join(sorted(catalyst_labels)) or "无催化剂"
    if not circuits:
        return catalyst_label
    ordered = sorted(
        circuits,
        key=lambda item: (
            item[2] is None,
            item[2] if item[2] is not None else 10**9,
            item[1],
        ),
    )
    return f"{catalyst_label} / {'+'.join(item[1] for item in ordered)}"


def _order_key(group):
    circuits = []
    for catalyst_set in group["sets"]:
        if catalyst_set.circuit:
            circuits.append(_circuit_number(catalyst_set.circuit))
        if catalyst_set.kind == "unconditional":
            circuits.append(0)
    numbers = sorted(number for number in circuits if number is not None)
    if not numbers:
        return (1, 0, (), group["label"])
    return (0, numbers[0], tuple(numbers), group["label"])


def _build_input_groups(
    coloring,
    sets,
    node_coverages,
    equivalence_index=None,
    all_sets=None,
):
    groups = []
    for color in coloring["colors"]:
        color_sets = [sets[set_id] for set_id in color["node_ids"]]
        recipes = set()
        for index in color["node_indexes"]:
            recipes.update(node_coverages[index])
        groups.append(
            {
                "setIds": color["node_ids"],
                "sets": color_sets,
                "label": _group_label(
                    color_sets,
                    equivalence_index,
                    all_sets,
                ),
                "recipeCount": len(recipes),
            }
        )
    groups.sort(key=_order_key)
    result = []
    set_groups = {}
    for index, group in enumerate(groups, 1):
        group_id = f"input_group_{index:03d}"
        result.append(
            {
                "inputGroupId": group_id,
                "displayName": f"输入组{index}",
                "setIds": group["setIds"],
                "sets": [item.to_dict() for item in group["sets"]],
                "label": group["label"],
                "recipeCount": group["recipeCount"],
            }
        )
        for set_id in group["setIds"]:
            set_groups[set_id] = group_id
    return result, set_groups


def _class_labels(relations, all_sets, equivalence_index):
    labels = {}
    for entry in relations.get("equivalenceClasses", ()):
        class_id = entry.get("classId", "")
        class_sets = [
            all_sets[set_id]
            for set_id in entry.get("setIds", ())
            if set_id in all_sets
        ]
        labels[class_id] = _group_label(
            class_sets,
            equivalence_index,
            all_sets,
        )
    return labels


def _build_top_level_containments(relations, all_sets, equivalence_index):
    """把顶层包含类翻译为可展示的实际催化组及直接包含关系。"""
    labels = _class_labels(relations, all_sets, equivalence_index)
    set_to_class = {}
    for entry in relations.get("equivalenceClasses", ()):
        class_id = entry.get("classId", "")
        for set_id in entry.get("setIds", ()):
            set_to_class[set_id] = class_id

    direct_edges = set()
    for chain in relations.get("containmentChains", ()):
        for child_id, parent_id in zip(chain, chain[1:]):
            direct_edges.add((parent_id, child_id))
    for edge in relations.get("edges", ()):
        left_class = set_to_class.get(edge.get("left"))
        right_class = set_to_class.get(edge.get("right"))
        if not left_class or not right_class or left_class == right_class:
            continue
        relation = edge.get("relation")
        if relation in {"covers", "strict_superset"}:
            direct_edges.add((left_class, right_class))
        elif relation in {"covered_by", "strict_subset"}:
            direct_edges.add((right_class, left_class))

    result = []
    for entry in relations.get("topLevelContainments", ()):
        contained_ids = list(entry.get("containedClassIds", ()))
        if not contained_ids:
            continue
        top_id = entry.get("topClassId", "")
        relevant = {top_id, *contained_ids}
        relationships = [
            {
                "parentClassId": parent_id,
                "parentLabel": labels.get(parent_id, parent_id),
                "childClassId": child_id,
                "childLabel": labels.get(child_id, child_id),
                "relation": "strict_containment",
            }
            for parent_id, child_id in sorted(direct_edges)
            if parent_id in relevant and child_id in relevant
        ]
        result.append(
            {
                "topClassId": top_id,
                "topSetIds": list(entry.get("topSetIds", ())),
                "topLabel": labels.get(top_id, top_id),
                "containedClassIds": contained_ids,
                "containedSetIds": list(entry.get("containedSetIds", ())),
                "containedLabels": [
                    labels.get(class_id, class_id)
                    for class_id in contained_ids
                ],
                "relationships": relationships,
            }
        )
    return result


def _log_top_level_containments(containments):
    if not containments:
        return
    logger.info("  顶层包含关系：")
    for entry in containments:
        logger.info("    顶层：%s", entry["topLabel"])
        logger.info(
            "    包含 %d 个催化组：",
            len(entry["containedClassIds"]),
        )
        relationships = entry["relationships"]
        if not relationships:
            for label in entry["containedLabels"]:
                logger.info("      ⊃ %s", label)
            continue
        for relationship in relationships:
            logger.info(
                "      %s ⊃ %s",
                relationship["parentLabel"],
                relationship["childLabel"],
            )


def _graph_matrix(graph):
    nodes = graph["nodes"]
    node_index = {set_id: index for index, set_id in enumerate(nodes)}
    matrix = [[0] * len(nodes) for _ in nodes]
    for edge in graph["edges"]:
        left = node_index[edge["left"]]
        right = node_index[edge["right"]]
        matrix[left][right] = 1
        matrix[right][left] = 1
    return matrix


class ColorHandler:
    """对冲突图着色并翻译为最终输入组。"""

    name = "color"

    def run(self, context: RunContext) -> StageResult:
        log_stage("着色与输入组翻译：conflict_graph -> input_groups")
        selected_dir = context.output_dir / "selected_recipes"
        pick_dir = context.output_dir / "catalyst_pick"
        graph_dir = context.output_dir / "conflict_graph"

        upstream = {
            "selected_recipes": json.loads(
                (selected_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
            "catalyst_pick": json.loads(
                (pick_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
            "conflict_graph": json.loads(
                (graph_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
        }

        output_dir = context.output_dir / "input_groups"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        strategy = get_strategy(config.COLORING_STRATEGY)
        total_groups = 0
        for machine in sorted(config.INTERESTED_MACHINES):
            log_machine(machine)
            recipes = _load_recipes(
                selected_dir / "machines" / machine / "recipes.json"
            )
            all_sets, selected_ids, relations = _load_sets(
                pick_dir / "machines" / machine / "coverage.json"
            )
            equivalence_index = _equivalence_index(relations)
            selected_sets = {
                set_id: all_sets[set_id] for set_id in selected_ids
            }
            graph = json.loads(
                (
                    graph_dir / "machines" / machine / "graph.json"
                ).read_text(encoding="utf-8")
            )
            coverage_by_set = _recipe_coverage(recipes, selected_sets)
            node_coverages = tuple(
                frozenset(coverage_by_set[set_id])
                for set_id in graph["nodes"]
            )
            adjacency = _graph_matrix(graph)
            adapter_nodes = tuple(
                {
                    "id": set_id,
                    "group": selected_sets[set_id].to_dict(),
                }
                for set_id in graph["nodes"]
            )
            coloring = strategy.solve(
                ColorContext(
                    nodes=adapter_nodes,
                    adjacency_matrix=tuple(tuple(row) for row in adjacency),
                    node_coverages=node_coverages,
                    rounds=config.COLORING_ROUNDS,
                    max_class_size=config.COLOR_CLASS_MAX_SIZE,
                    size_sweep=config.COLOR_CLASS_SIZE_SWEEP,
                    sweep_max_size=config.COLOR_CLASS_SIZE_SWEEP_MAX,
                )
            )
            logger.info(
                "着色：%d 个节点 → %d 个输入组",
                len(graph["nodes"]),
                coloring["color_count"],
            )
            input_groups, set_groups = _build_input_groups(
                coloring,
                selected_sets,
                node_coverages,
                equivalence_index,
                all_sets,
            )
            top_level_containments = _build_top_level_containments(
                relations,
                all_sets,
                equivalence_index,
            )
            sizes = sorted(
                (group["recipeCount"] for group in input_groups),
                reverse=True,
            )
            document = {
                "machine": machine,
                "setGroups": set_groups,
                "inputGroups": input_groups,
                "topLevelContainments": top_level_containments,
                "summary": {
                    "groupCount": len(input_groups),
                    "largestRecipeClass": sizes[0] if sizes else 0,
                    "recipeClassSizes": sizes,
                },
            }
            _write_json(
                output_dir / "machines" / machine / "input_groups.json",
                document,
            )
            logger.info(
                "着色完成：%d 个输入组，最大组覆盖 %d 条配方",
                len(input_groups),
                sizes[0] if sizes else 0,
            )
            logger.info("  输入组：")
            for group in input_groups:
                logger.info(
                    "    %s（%d 条）：%s",
                    group["displayName"],
                    group["recipeCount"],
                    group["label"],
                )
            _log_top_level_containments(top_level_containments)
            total_groups += len(input_groups)

        manifest = _build_manifest(upstream, output_dir)
        _write_json(output_dir / "manifest.json", manifest)
        logger.info(
            "图着色完成：%d 台机器，共 %d 个输入组",
            len(config.INTERESTED_MACHINES),
            total_groups,
        )
        return StageResult(
            stage=self.name,
            outputs=(output_dir, output_dir / "manifest.json"),
            stats={
                "machines": len(config.INTERESTED_MACHINES),
                "inputGroups": total_groups,
                "outputHash": manifest["outputHash"],
            },
        )
