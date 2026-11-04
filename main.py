import json
import logging
import sys
from pathlib import Path

import config
from s2_normalize.data_loader import (
    load_machine_recipes,
    load_item_translations,
    load_ore_dictionary,
)
from s2_normalize.recipe_cleaner import clean_and_merge_recipes
from s2_normalize.recipe_grouper import group_recipes as build_grouped_recipes
from s2_normalize.translator import translate_recipes
from s0_contracts.logger import configure_logging
from s0_contracts.schema import Recipe as RecipeContract
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


def _machine_field(entry):
    """聚合条目中标识机器的字段（machine_key / machine 均可）。"""
    return entry.get("machine_key") or entry.get("machine") or ""


def _merge_machine_file(path, machine_keys, new_items):
    """把本次机器的条目合并进聚合列表文件。

    保留不属于本次机器的旧条目，追加本次机器的新条目，再按机器排序写出。
    machine_keys: 本次已重新生成的机器 key 集合（用于剔除旧条目避免重复）。
    """
    old = _load_list(path)
    kept = [item for item in old if _machine_field(item) not in machine_keys]
    merged = kept + list(new_items)
    merged.sort(key=_machine_field)
    _write_json(path, merged)
    return merged


def _cleaned_entry(original_name, source_name, base_name, output_file):
    """复用清洗产物时的索引条目：优先取既有 index.json 中同文件元数据，否则构造最小条目。"""
    for entry in _load_list(config.CLEANED_RECIPES_DIR / "index.json"):
        if entry.get("file") == output_file.name:
            return entry
    return {
        "original_name": original_name,
        "machine_key": base_name,
        "source": source_name,
        "translated_name": "",
        "file": output_file.name,
    }


def _coloring_objective(coloring):
    """着色优劣：催化组数越少越好；并列时按「各催化组覆盖配方数」降序逐位比较。

    即把各催化组覆盖的配方数列成列表从大到小排序，从最大组起逐位比较，
    谁先更大谁更差。历史方案缺少明细列表时，以最大组值补齐（保守估计，
    不会把信息不全的旧方案误判为更优）。
    """
    color_count = coloring.get("color_count", 0)
    sizes = coloring.get("recipe_class_sizes")
    if not sizes:
        largest = coloring.get("largest_recipe_class", 1 << 30)
        sizes = [largest] * max(1, color_count)
    return (color_count, tuple(sizes))


def _coloring_config_signature():
    """当前着色搜索配置（与历史方案比对，配置不同则不复用历史结果）。"""
    return (
        config.COLOR_CLASS_MAX_SIZE,
        config.COLOR_CLASS_SIZE_SWEEP,
        config.COLOR_CLASS_SIZE_SWEEP_MAX,
        config.COLORING_ROUNDS,
    )


def _coloring_config_of(coloring):
    """历史方案记录着的搜索配置；旧文件缺字段时为 None，与当前配置不相等。"""
    return (
        coloring.get("max_class_size"),
        coloring.get("size_sweep"),
        coloring.get("sweep_max_size"),
        coloring.get("rounds"),
    )


def _coloring_within_limit(coloring):
    """历史方案是否满足当前的硬上限（单色类节点数不得超过 COLOR_CLASS_MAX_SIZE）。"""
    limit = config.COLOR_CLASS_MAX_SIZE
    if limit is None:
        return True
    largest = coloring.get("largest_color_class")
    if largest is None:
        sizes = [color.get("size", 0) for color in coloring.get("colors", [])]
        largest = max(sizes) if sizes else 0
    return largest <= limit


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
    print(
        f"机器 {result['machine']} 着色完成{note}: "
        f"{result['color_count']} 个催化组，最大组覆盖 "
        f"{result['largest_recipe_class']} 个配方"
        f"（{result['largest_color_class']} 个节点，"
        f"单色类节点数上限={limit_text}）。"
    )
    print(f"  各组覆盖配方数（降序）: {result.get('recipe_class_sizes', [])}")
    for color in result["colors"]:
        group = result["merged_groups"][color["id"]]
        label = readable_group_label(group, abstract_groups)
        print(f"  {color['id']}（{color['size']} 个节点）：{label}")


def run_clean_recipes(force):
    """1. 清洗、矿物辞典展开、合并、翻译（默认复用已有产物）。

    只处理 config.INTERESTED_MACHINES 中的机器。已有清洗文件且未传 --rebuild 时
    直接复用，不重新清洗；仅对本次真正重算的机器做 index.json 合并更新。
    返回 (可用条目列表, 本次重算的 machine_key 集合)。
    """
    logger.info("========== 1 清洗与翻译 ==========")
    _ensure_dir(config.CLEANED_RECIPES_DIR)

    # 收集 Recipes/ 下所有来源目录里的机器配方文件（GTNEIDefaultHandler_*.json，一机一文件）
    recipes_root = config.DATA_DIR / "Recipes"
    if not recipes_root.is_dir():
        raise FileNotFoundError(f"配方根目录不存在，已停止处理：{recipes_root}")
    source_roots = {
        source_dir.name: source_dir
        for source_dir in sorted(recipes_root.iterdir())
        if source_dir.is_dir()
    }
    if not source_roots:
        raise FileNotFoundError(
            f"配方根目录下没有来源目录，已停止处理：{recipes_root}"
        )

    if not config.INTERESTED_MACHINES:
        raise ValueError("config.INTERESTED_MACHINES 为空，请先配置兴趣机器。")

    # source -> {机器名 -> 配方文件}
    machine_files = {}
    for source_name, source_root in source_roots.items():
        files = {}
        for recipe_file in source_root.glob("GTNEIDefaultHandler_*.json"):
            files[recipe_file.stem[len("GTNEIDefaultHandler_"):]] = recipe_file
        machine_files[source_name] = files

    all_available = {
        machine for files in machine_files.values() for machine in files
    }
    missing = sorted(
        name
        for name in config.INTERESTED_MACHINES
        if name not in all_available
    )
    if missing:
        raise FileNotFoundError(
            "兴趣机器白名单中存在未找到的机器配方文件，已停止处理。\n"
            f"未找到: {missing}\n"
            f"请检查 config.INTERESTED_MACHINES（应为文件名去掉 "
            f"GTNEIDefaultHandler_ 前缀与 .json 后缀）或 DATA_DIR/Recipes 下的来源目录。\n"
            f"可用机器（{len(all_available)} 个），示例: "
            f"{sorted(all_available)[:10]}"
            f"{'...' if len(all_available) > 10 else ''}"
        )

    # 记录每台机器出现在几个来源，用于文件名消歧
    machine_source_count = {
        machine: sum(
            1 for files in machine_files.values() if machine in files
        )
        for machine in config.INTERESTED_MACHINES
    }

    # 待处理目标：{base_name -> (original_name, source_name, recipe_file, output_file, needs_clean)}
    pending = {}
    for source_name, files in machine_files.items():
        for original_name, recipe_file in sorted(files.items()):
            if original_name not in config.INTERESTED_MACHINES:
                continue
            if machine_source_count[original_name] > 1:
                base_name = f"{original_name}__{source_name}"
            else:
                base_name = original_name
            output_file = config.CLEANED_RECIPES_DIR / f"{base_name}.json"
            needs_clean = force or not output_file.exists()
            pending[base_name] = (
                original_name, source_name, recipe_file, output_file, needs_clean
            )

    # 仅当确实需要重算时才加载（大）数据文件
    translations = None
    ore_dictionary = None
    if any(item[4] for item in pending.values()):
        logger.info("加载物品翻译和矿辞典...")
        translations = load_item_translations(config.DATA_DIR)
        ore_dictionary = load_ore_dictionary(config.DATA_DIR)

    index_entries = []
    regenerated_keys = set()
    for base_name, (original_name, source_name, recipe_file, output_file, needs_clean) in pending.items():
        if not needs_clean:
            index_entries.append(_cleaned_entry(original_name, source_name, base_name, output_file))
            logger.info("1 复用现有清洗产物: %s", output_file)
            continue

        loaded = load_machine_recipes(recipe_file)
        if loaded is None:
            continue
        _, translated_name, raw_recipes = loaded
        logger.info("1 重新清洗: %s（来源 %s）", original_name, source_name)

        # 清洗 + 合并等效配方（内部展开 # 矿辞典 / :32767 通配输入）
        cleaned_merged = clean_and_merge_recipes(
            raw_recipes, ore_dictionary, set(translations), translations
        )
        translated_recipes = translate_recipes(cleaned_merged, translations)
        _write_json(output_file, translated_recipes)
        logger.info("已生成: %s", output_file)

        index_entries.append(
            {
                "original_name": original_name,
                "machine_key": base_name,
                "source": source_name,
                "translated_name": translated_name,
                "file": output_file.name,
            }
        )
        regenerated_keys.add(base_name)

    # 合并更新 index.json：只剔除并重写本次真正重算的机器
    _merge_machine_file(
        config.CLEANED_RECIPES_DIR / "index.json", regenerated_keys,
        [e for e in index_entries if e["machine_key"] in regenerated_keys],
    )
    logger.info(
        "1 完成：复用 %d 台，重算 %d 台",
        len(index_entries) - len(regenerated_keys), len(regenerated_keys),
    )
    return index_entries, regenerated_keys


def run_filter_recipes(cleaned_entries, regenerated_keys, force):
    """2. 配方删减与催化组删减（默认复用已有过滤产物）。

    cleaned_entries:  1 返回的可用机器条目（含复用与本次重算）。
    regenerated_keys: 1 本次真正重算的 machine_key（其过滤必须重算）。
    force:            --rebuild 时强制全部重算。
    已有过滤文件且（未被重算 / 未强制）时直接复用；仅对本次重算的机器更新聚合文件。
    返回 (filtered_machine_recipes, 本次涉及的全部 machine_key, abstract_groups)。
    """
    logger.info("========== 2 配方删减与催化组删减 ==========")
    _ensure_dir(config.FILTERED_RECIPES_DIR)
    _ensure_dir(config.COLORED_RECIPES_DIR)

    output_index_fresh = []
    reduction_metadata_fresh = []
    filtered_machine_recipes = []
    recomputed_filter_keys = set()
    reused_filter_keys = set()

    for entry in cleaned_entries:
        original_name = entry.get("original_name", "")
        machine_key = entry.get("machine_key", "") or original_name
        file_name = entry.get("file", f"{machine_key}.json")
        filtered_file = config.FILTERED_RECIPES_DIR / file_name

        if (
            not force
            and machine_key not in regenerated_keys
            and filtered_file.exists()
        ):
            filtered_machine_recipes.append((machine_key, _load_list(filtered_file)))
            reused_filter_keys.add(machine_key)
            logger.info("2 复用现有过滤产物: %s", filtered_file)
            continue

        input_file = config.CLEANED_RECIPES_DIR / file_name
        if not input_file.exists():
            logger.warning("缺少清洗文件，跳过: %s", input_file)
            continue

        recipes = json.loads(input_file.read_text(encoding="utf-8"))
        cleaner = get_cleaner(original_name)
        filtered_recipes = cleaner(recipes)
        filtered_recipes, machine_metadata = reduce_catalyst_groups(
            machine_key, filtered_recipes
        )
        assign_recipe_ids(machine_key, filtered_recipes)
        reduction_metadata_fresh.append(machine_metadata)
        filtered_machine_recipes.append((machine_key, filtered_recipes))
        recomputed_filter_keys.add(machine_key)

        _write_json(filtered_file, filtered_recipes)
        output_index_fresh.append(
            {
                "original_name": original_name,
                "machine_key": machine_key,
                "source": entry.get("source", ""),
                "translated_name": entry.get("translated_name", ""),
                "file": file_name,
            }
        )
        logger.info("2 已过滤并生成: %s", filtered_file)

    _merge_machine_file(
        config.FILTERED_RECIPES_DIR / "index.json",
        recomputed_filter_keys, output_index_fresh,
    )
    _merge_machine_file(
        config.FILTERED_RECIPES_DIR / "catalyst_reduction.json",
        recomputed_filter_keys, reduction_metadata_fresh,
    )

    # 抽象催化剂映射：本次重算元数据 + 复用机器的既有删减元数据
    combined_metadata = list(reduction_metadata_fresh)
    if reused_filter_keys:
        for entry in _load_list(
            config.FILTERED_RECIPES_DIR / "catalyst_reduction.json"
        ):
            if _machine_field(entry) in reused_filter_keys:
                combined_metadata.append(entry)
    abstract_groups = abstract_group_map(combined_metadata)

    active_keys = recomputed_filter_keys | reused_filter_keys
    logger.info(
        "2 完成：复用 %d 台，重算 %d 台",
        len(reused_filter_keys), len(recomputed_filter_keys),
    )
    return filtered_machine_recipes, active_keys, abstract_groups


def run_color_recipes(filtered_machine_recipes, this_run_keys, abstract_groups):
    """3. 冲突图与着色（增量，仅处理本次机器）。

    filtered_machine_recipes: 本次 2 返回的 (machine_key, recipes) 列表。
    this_run_keys:           本次机器 key 集合，用于聚合文件合并更新。
    """
    logger.info("========== 3.1 构建催化组冲突图 ==========")
    _ensure_dir(config.COLORED_RECIPES_DIR)

    conflict_graphs = []
    empty_conflict_reports = []
    for machine_name, recipes in filtered_machine_recipes:
        report = {}
        conflict_graphs.append(
            build_conflict_graph(
                machine_name, recipes, abstract_groups,
                config.LOG_EMPTY_CONFLICT_CONSOLE,
                empty_conflict_report=report,
            )
        )
        empty_conflict_reports.append(report)
    _merge_machine_file(
        config.FILTERED_RECIPES_DIR / "conflict_graph.json",
        this_run_keys,
        conflict_graphs,
    )
    _merge_machine_file(
        config.EMPTY_CONFLICT_FILE,
        this_run_keys,
        empty_conflict_reports,
    )
    logger.info(
        "已生成: %s",
        config.FILTERED_RECIPES_DIR / "conflict_graph.json",
    )
    logger.info("已生成: %s", config.EMPTY_CONFLICT_FILE)
    logger.info("3.1 步骤完成。")

    logger.info("========== 3.2 冲突图着色与催化组合并 ==========")
    previous_by_machine = {
        entry.get("machine"): entry
        for entry in _load_list(config.COLORED_RECIPES_DIR / "coloring.json")
    }
    coloring_results = []
    for machine_name, recipes in filtered_machine_recipes:
        graph = next(
            graph for graph in conflict_graphs if graph["machine"] == machine_name
        )
        new_coloring = color_graph(
            graph,
            config.COLORING_ROUNDS,
            config.COLOR_CLASS_MAX_SIZE,
            node_coverages(recipes, graph),
            size_sweep=config.COLOR_CLASS_SIZE_SWEEP,
            sweep_max_size=config.COLOR_CLASS_SIZE_SWEEP_MAX,
        )
        fingerprint = graph_fingerprint(graph)
        previous = previous_by_machine.get(machine_name)
        if (
            previous
            and previous.get("graph_fingerprint") == fingerprint
            # 历史方案必须与当前搜索配置同源，且满足当前硬上限（单色类节点数）
            and _coloring_config_of(previous) == _coloring_config_signature()
            and _coloring_within_limit(previous)
            and _coloring_objective(previous) <= _coloring_objective(new_coloring)
        ):
            # 图未变且历史方案不差：沿用历史最优，仅重新套用到当前配方
            logger.info(
                "机器 %s 沿用历史更优着色: %s 个催化组 / 最大组 %s 个配方"
                "（本次 %s / %s）",
                machine_name,
                previous["color_count"],
                previous["largest_recipe_class"],
                new_coloring["color_count"],
                new_coloring["largest_recipe_class"],
            )
            chosen, note = previous, "（沿用历史更优）"
        else:
            if previous:
                if previous.get("graph_fingerprint") != fingerprint:
                    logger.info("机器 %s 冲突图已变，重新着色。", machine_name)
                elif _coloring_config_of(previous) != _coloring_config_signature():
                    logger.info(
                        "机器 %s 着色配置已变（历史 max_class_size=%s），重新着色。",
                        machine_name,
                        previous.get("max_class_size"),
                    )
                elif not _coloring_within_limit(previous):
                    logger.info(
                        "机器 %s 历史方案最大类 %s 个节点，超过当前上限 %s，重新着色。",
                        machine_name,
                        previous.get("largest_color_class"),
                        config.COLOR_CLASS_MAX_SIZE,
                    )
            chosen, note = new_coloring, ""

        colored_recipes, merged_groups = apply_coloring(recipes, graph, chosen)
        result = _coloring_summary(machine_name, chosen, merged_groups, fingerprint)
        coloring_results.append(result)
        _write_json(
            config.COLORED_RECIPES_DIR / f"{machine_name}.json",
            colored_recipes,
        )
        _print_coloring(result, abstract_groups, note)

    _merge_machine_file(
        config.COLORED_RECIPES_DIR / "coloring.json",
        this_run_keys,
        coloring_results,
    )
    logger.info("已生成: %s", config.COLORED_RECIPES_DIR / "coloring.json")
    logger.info("3.2 步骤完成。")


def _find_machine_recipe_file(machine_name):
    """在 DATA_DIR/Recipes 下查找一台机器的原始配方文件。"""
    recipes_root = Path(config.DATA_DIR) / "Recipes"
    matches = sorted(
        recipes_root.glob(f"*/GTNEIDefaultHandler_{machine_name}.json")
    )
    if not matches:
        return None, None
    recipe_file = matches[0]
    return recipe_file, recipe_file.parent.name


def run_group_recipes():
    """1. 将 DATA_DIR 中的机器配方构建为等效输入分组产物。

    正式实现中，本步骤由 s2_normalize 承担；s1_preprocess 只负责
    Raw -> Data 的数据预处理。
    """
    logger.info("========== 1 等效输入分组 ==========")
    output_dir = config.GROUPED_RECIPES_DIR
    _ensure_dir(output_dir)

    logger.info("加载物品翻译和矿辞典...")
    translations = load_item_translations(Path(config.DATA_DIR))
    ore_dictionary = load_ore_dictionary(Path(config.DATA_DIR))
    item_keys = set(translations)

    index = []
    orphan_report = {}
    for machine_name in sorted(config.INTERESTED_MACHINES):
        recipe_file, source_name = _find_machine_recipe_file(machine_name)
        if recipe_file is None:
            logger.warning("未找到机器配方文件，跳过: %s", machine_name)
            continue

        data = json.loads(recipe_file.read_text(encoding="utf-8"))
        raw_recipes = data.get("recipes", [])
        grouped, report = build_grouped_recipes(
            raw_recipes,
            ore_dictionary,
            translations,
            item_keys,
            machine_name=machine_name,
            orphan_mode=config.GROUP_ORPHAN_MODE,
        )
        # Validate the stage-boundary contract without changing serialized data.
        for recipe in grouped:
            RecipeContract.from_dict(recipe)

        output_path = output_dir / f"{machine_name}.json"
        _write_json(output_path, grouped)

        entry = {
            "machine": machine_name,
            "name": data.get("name", ""),
            "source": source_name,
            "file": output_path.name,
            **report,
        }
        index.append(entry)
        orphan_report[machine_name] = report
        logger.info(
            "分组 %s: %d -> %d -> %d 条；报警 %d；孤儿(has/only)=%d/%d",
            machine_name,
            report["inputRecipes"],
            report["normalizedRecipes"],
            report["outputRecipes"],
            sum(report.get("alerts", {}).values()),
            report["orphanHasInput"],
            report["orphanOnlyInputs"],
        )

    _write_json(output_dir / "index.json", index)
    _write_json(output_dir / "orphan_report.json", orphan_report)
    logger.info("已生成: %s", output_dir)
    return len(index)


def run_cover_grouped_recipes():
    """Stage 3: apply Rules and select the minimum catalyst-set cover."""

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
    """Stage 4: build undirected conflict matrices from covered recipes."""

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
    """Stage 5: run the configured coloring search on the conflict matrices."""

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


def run_preprocess():
    """Stage 1: execute the existing Raw -> Data preprocessing entrypoint."""

    from s1_preprocess.split import main as split_main

    saved_argv = sys.argv[:]
    try:
        sys.argv = [saved_argv[0]]
        split_main()
    finally:
        sys.argv = saved_argv


def _run_logged_stage(stage_name, log_path, runner):
    _ensure_dir(Path(log_path).parent)
    counter = configure_logging(Path(log_path))
    count = runner()
    print(
        f"{stage_name} 完成：{count} 台机器；"
        f"发现 {counter.count} 个问题。详细日志: {log_path}"
    )


def _formal_stage(stage):
    if stage == "preprocess":
        run_preprocess()
    elif stage == "normalize":
        _run_logged_stage(
            "s2 normalize",
            config.GROUPED_RECIPES_DIR / "grouping.log",
            run_group_recipes,
        )
    elif stage == "cover":
        _run_logged_stage(
            "s3 cover",
            config.FILTERED_GROUPED_RECIPES_DIR / "filter_grouped.log",
            run_cover_grouped_recipes,
        )
    elif stage == "conflict":
        _run_logged_stage(
            "s4 conflict",
            config.FILTERED_GROUPED_RECIPES_DIR / "conflict.log",
            run_grouped_conflicts,
        )
    elif stage == "color":
        _run_logged_stage(
            "s5 color",
            config.COLORED_GROUPED_RECIPES_DIR / "color.log",
            run_grouped_color,
        )
    else:
        raise ValueError(
            "未知 stage；可用值: preprocess, normalize, cover, conflict, color, all"
        )


def main():
    if "--legacy" in sys.argv:
        force = "--rebuild" in sys.argv
        issue_counter = configure_logging(config.LOG_FILE)
        if force:
            logger.info("检测到 --rebuild：强制重新生成旧链路 1/2")
        cleaned_entries, regenerated_keys = run_clean_recipes(force)
        filtered_machine_recipes, active_keys, abstract_groups = (
            run_filter_recipes(cleaned_entries, regenerated_keys, force)
        )
        run_color_recipes(
            filtered_machine_recipes, active_keys, abstract_groups
        )
        print(
            f"旧链路处理完成，发现 {issue_counter.count} 个问题。"
            f"详细日志: {config.LOG_FILE}"
        )
        return

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
    for current_stage in stages:
        _formal_stage(current_stage)


if __name__ == "__main__":
    main()
