import json
import logging
import sys
from pathlib import Path

import config
from s0_contracts.logger import configure_logging
from s0_contracts.stage import RunContext
from s0_contracts.recipe import (
    assign_recipe_ids,
    group_output_order_key,
    readable_group_label,
)
from s4_cover.catalyst_reducer import reduce_catalyst_groups
from s4_cover.catalyst_relations import (
    abstract_group_map,
    containment_markdown,
    machine_relation_report,
)
from s5_conflict.recipe_checker import build_conflict_graph
from s6_color.graph_coloring import (
    apply_coloring,
    color_graph,
    graph_fingerprint,
    node_coverages,
)
from s3_rules.registry import get_cleaner
from s1_preprocess.handler import PreprocessHandler
from s2_normalize.handler import NormalizeHandler

logger = logging.getLogger(__name__)

def _write_json(path: Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ensure_dir(directory: Path):
    """确保目录存在（增量模式不再整目录清空）。"""
    directory.mkdir(parents=True, exist_ok=True)


def _load_list(path: Path):
    """读取 JSON 列表；文件不存在或非法时返回空列表。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _coloring_summary(machine_name, coloring, merged_groups, fingerprint):
    """把选定着色方案整理为写入 coloring.json 的摘要。

    各组（含终端列表）按输出规则排序：无编程电路的组排最后，
    其余按组内最小编号升序（见 `group_output_order_key`）。
    """
    colors = sorted(
        coloring["colors"],
        key=lambda color: group_output_order_key(merged_groups[color["id"]]),
    )
    return {
        "machine": machine_name,
        "color_count": coloring["color_count"],
        "largest_color_class": coloring["largest_color_class"],
        "largest_recipe_class": coloring["largest_recipe_class"],
        "recipe_class_sizes": coloring.get("recipe_class_sizes", []),
        "max_class_size": coloring["max_class_size"],
        "best_max_class_size": coloring.get("best_max_class_size"),
        "swept_max_class_sizes": coloring.get("swept_max_class_sizes", []),
        # 复现该方案所需的搜索配置（供下次运行判断能否沿用历史结果）
        "size_sweep": config.COLOR_CLASS_SIZE_SWEEP,
        "sweep_max_size": config.COLOR_CLASS_SIZE_SWEEP_MAX,
        "rounds": config.COLORING_ROUNDS,
        "colors": colors,
        "node_colors": coloring["node_colors"],
        "merged_groups": merged_groups,
        "best_round": coloring.get("best_round"),
        "graph_fingerprint": fingerprint,
    }


def _print_coloring(result, abstract_groups, note=""):
    limit = result.get("best_max_class_size")
    limit_text = "不限" if limit is None else str(limit)
    logger.info(
        "机器 %s 着色完成%s：%d 个催化组，最大组覆盖 %d 个配方"
        "（%d 个节点，单色类节点数上限=%s）",
        result["machine"],
        note,
        result["color_count"],
        result["largest_recipe_class"],
        result["largest_color_class"],
        limit_text,
    )
    logger.debug(
        "机器 %s 各组覆盖配方数（降序）：%s",
        result["machine"],
        result.get("recipe_class_sizes", []),
    )
    for color in result["colors"]:
        group = result["merged_groups"][color["id"]]
        label = readable_group_label(group, abstract_groups)
        logger.debug(
            "机器 %s 颜色组 %s（%d 个节点）：%s",
            result["machine"],
            color["id"],
            color["size"],
            label,
        )


def run_cover_grouped_recipes():
    """执行规则删减与最小催化组覆盖。"""

    logger.info("========== s3 cover: Rules + minimum catalyst cover ==========")
    grouped_dir = config.GROUPED_RECIPES_DIR
    output_dir = config.FILTERED_GROUPED_RECIPES_DIR
    _ensure_dir(output_dir)

    source_index = {
        entry.get("machine", ""): entry
        for entry in _load_list(grouped_dir / "index.json")
        if entry.get("machine")
    }
    output_index = []
    reduction_metadata = []
    relation_reports = []

    for machine_name in sorted(config.INTERESTED_MACHINES):
        source_path = grouped_dir / f"{machine_name}.json"
        if not source_path.exists():
            logger.warning("缺少分组配方文件，跳过: %s", source_path)
            continue

        recipes = _load_list(source_path)
        cleaner = get_cleaner(machine_name)
        rule_filtered = cleaner(recipes)
        filtered_recipes, metadata = reduce_catalyst_groups(
            machine_name, rule_filtered
        )
        assign_recipe_ids(machine_name, filtered_recipes)

        output_path = output_dir / source_path.name
        _write_json(output_path, filtered_recipes)

        source_entry = source_index.get(machine_name, {})
        relation_report = machine_relation_report(machine_name, metadata)
        relation_reports.append(relation_report)
        reduction_metadata.append(metadata)
        summary = {
            "machine": machine_name,
            "name": source_entry.get("name", ""),
            "source": source_entry.get("source", ""),
            "file": output_path.name,
            "recipeCount": len(recipes),
            "filteredRecipeCount": len(filtered_recipes),
            "catalystRecipeCount": sum(
                bool(recipe.get("catalystGroups"))
                for recipe in filtered_recipes
            ),
            "uniqueGroupCount": metadata.get("unique_group_count", 0),
            "selectedGroupCount": len(metadata.get("selected_groups", [])),
            "componentCount": metadata.get("relationship_component_count", 0),
            "optimal": bool(metadata.get("set_cover", {}).get("optimal")),
            "relationCounts": metadata.get("relation_counts", {}),
            "containmentChainCount": len(
                metadata.get("pure_containment_chains", [])
            ),
        }
        output_index.append(summary)
        logger.info(
            "s3 cover %s: %d -> %d recipes; unique=%d; selected=%d; optimal=%s",
            machine_name,
            summary["recipeCount"],
            summary["filteredRecipeCount"],
            summary["uniqueGroupCount"],
            summary["selectedGroupCount"],
            summary["optimal"],
        )

    _write_json(output_dir / "index.json", output_index)
    _write_json(output_dir / "catalyst_reduction.json", reduction_metadata)
    _write_json(config.CATALYST_RELATION_REPORT, relation_reports)
    config.CATALYST_CONTAINMENT_REPORT.write_text(
        containment_markdown(relation_reports),
        encoding="utf-8",
    )
    logger.info("已生成: %s", output_dir)
    return len(output_index)


def run_grouped_conflicts():
    """根据覆盖后的配方构建无向冲突矩阵。"""

    logger.info("========== s4 conflict: conflict matrices ==========")
    input_dir = config.FILTERED_GROUPED_RECIPES_DIR
    metadata_by_machine = {
        entry.get("machine", ""): entry
        for entry in _load_list(input_dir / "catalyst_reduction.json")
        if entry.get("machine")
    }
    index_by_machine = {
        entry.get("machine", ""): entry
        for entry in _load_list(input_dir / "index.json")
        if entry.get("machine")
    }

    conflict_graphs = []
    empty_conflict_reports = []
    for machine_name in sorted(config.INTERESTED_MACHINES):
        entry = index_by_machine.get(machine_name)
        metadata = metadata_by_machine.get(machine_name)
        if not entry or not metadata:
            logger.warning("缺少 s3 覆盖产物，跳过冲突图: %s", machine_name)
            continue
        source_path = input_dir / entry.get("file", f"{machine_name}.json")
        if not source_path.exists():
            logger.warning("缺少覆盖后配方文件，跳过: %s", source_path)
            continue
        recipes = _load_list(source_path)
        empty_report = {}
        graph = build_conflict_graph(
            machine_name,
            recipes,
            abstract_group_map([metadata]),
            config.LOG_EMPTY_CONFLICT_CONSOLE,
            empty_conflict_report=empty_report,
        )
        conflict_graphs.append(graph)
        empty_conflict_reports.append(empty_report)
        logger.info(
            "s4 conflict %s: nodes=%d; edges=%d; orphan_pruned=%d",
            machine_name,
            len(graph.get("nodes", [])),
            len(graph.get("edges", [])),
            graph.get("build_stats", {}).get("orphan_targets_pruned", 0),
        )

    _write_json(config.GROUPED_CONFLICT_FILE, conflict_graphs)
    _write_json(config.GROUPED_EMPTY_CONFLICT_FILE, empty_conflict_reports)
    return len(conflict_graphs)


def run_grouped_color():
    """在冲突矩阵上执行配置指定的着色搜索。"""

    logger.info("========== s5 color: coloring search ==========")
    _ensure_dir(config.COLORED_GROUPED_RECIPES_DIR)
    input_dir = config.FILTERED_GROUPED_RECIPES_DIR
    index_by_machine = {
        entry.get("machine", ""): entry
        for entry in _load_list(input_dir / "index.json")
        if entry.get("machine")
    }
    metadata_by_machine = {
        entry.get("machine", ""): entry
        for entry in _load_list(input_dir / "catalyst_reduction.json")
        if entry.get("machine")
    }
    graph_by_machine = {
        entry.get("machine", ""): entry
        for entry in _load_list(config.GROUPED_CONFLICT_FILE)
        if entry.get("machine")
    }

    coloring_results = []
    for machine_name in sorted(config.INTERESTED_MACHINES):
        entry = index_by_machine.get(machine_name)
        metadata = metadata_by_machine.get(machine_name)
        graph = graph_by_machine.get(machine_name)
        if not entry or not metadata or not graph:
            logger.warning("缺少 s4 冲突图产物，跳过着色: %s", machine_name)
            continue
        source_path = input_dir / entry.get("file", f"{machine_name}.json")
        if not source_path.exists():
            logger.warning("缺少覆盖后配方文件，跳过着色: %s", source_path)
            continue
        recipes = _load_list(source_path)
        coloring = color_graph(
            graph,
            config.COLORING_ROUNDS,
            config.COLOR_CLASS_MAX_SIZE,
            node_coverages(recipes, graph),
            size_sweep=config.COLOR_CLASS_SIZE_SWEEP,
            sweep_max_size=config.COLOR_CLASS_SIZE_SWEEP_MAX,
        )
        colored_recipes, merged_groups = apply_coloring(
            recipes, graph, coloring
        )
        fingerprint = graph_fingerprint(graph)
        result = _coloring_summary(
            machine_name, coloring, merged_groups, fingerprint
        )
        coloring_results.append(result)
        _write_json(
            config.COLORED_GROUPED_RECIPES_DIR / source_path.name,
            colored_recipes,
        )
        _print_coloring(result, abstract_group_map([metadata]))
        logger.info(
            "s5 color %s: colors=%d; largest_recipe_class=%d",
            machine_name,
            result.get("color_count", 0),
            result.get("largest_recipe_class", 0),
        )

    _write_json(config.GROUPED_COLORING_FILE, coloring_results)
    return len(coloring_results)


def run_preprocess(context):
    """第一步：通过专用处理器完成 Raw -> Data。"""

    return PreprocessHandler().run(context)


def _run_logged_stage(stage_name, runner):
    logger.info("%s 开始", stage_name)
    count = runner()
    logger.info("%s 完成：%d 台机器", stage_name, count)
    return count


def _formal_stage(stage, context):
    if stage == "preprocess":
        return run_preprocess(context)
    elif stage == "normalize":
        return NormalizeHandler().run(context)
    elif stage == "cover":
        _run_logged_stage("规则删减与最小催化组覆盖", run_cover_grouped_recipes)
    elif stage == "conflict":
        _run_logged_stage("冲突矩阵构建", run_grouped_conflicts)
    elif stage == "color":
        _run_logged_stage("冲突图着色", run_grouped_color)
    else:
        raise ValueError(
            "未知 stage；可用值: preprocess, normalize, cover, conflict, color, all"
        )


def main():
    stage = None
    if "--stage" in sys.argv:
        index = sys.argv.index("--stage")
        if index + 1 >= len(sys.argv):
            raise SystemExit("--stage 需要参数")
        stage = sys.argv[index + 1]

    if stage == "all":
        stages = ["preprocess", "normalize", "cover", "conflict", "color"]
    elif stage is None:
        stages = ["normalize", "cover", "conflict", "color"]
    else:
        stages = [stage]
    context = RunContext(
        version=config.DATA_SPLIT_DIR.name,
        raw_dir=config.DUMPER_DIR,
        data_dir=config.DATA_SPLIT_DIR,
        output_dir=config.VERSIONED_OUTPUT_ROOT,
    )
    counter = configure_logging(config.LOG_FILE)
    logger.info("========== Recipe Check 开始 ==========")
    try:
        for current_stage in stages:
            _formal_stage(current_stage, context)
    except Exception:
        logger.exception(
            "阶段 %s 执行失败，详细堆栈已写入 %s",
            current_stage,
            config.LOG_FILE,
        )
        raise SystemExit(1)
    logger.info(
        "========== Recipe Check 完成：记录 %d 个问题 ==========",
        counter.count,
    )


if __name__ == "__main__":
    main()
