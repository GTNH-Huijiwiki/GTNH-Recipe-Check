import json
import logging
import sys
from pathlib import Path

import config
from Core.data_loader import (
    load_machine_recipes,
    load_item_translations,
    load_ore_dictionary,
)
from Core.recipe_cleaner import clean_and_merge_recipes
from Core.translator import translate_recipes
from Core.logger import configure_logging
from Core.recipe_checker import (
    abstract_group_map,
    assign_recipe_ids,
    build_conflict_graph,
    group_output_order_key,
    readable_group_label,
)
from Core.catalyst_reducer import reduce_catalyst_groups
from Core.graph_coloring import (
    apply_coloring,
    color_graph,
    graph_fingerprint,
    node_coverages,
)
from Rules.registry import get_cleaner

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


def main():
    force = "--rebuild" in sys.argv
    issue_counter = configure_logging(config.LOG_FILE)
    if force:
        logger.info("检测到 --rebuild：强制重新生成 1/2")
    # 1：清洗（已有产物默认复用；--rebuild 或缺失时才重算）
    cleaned_entries, regenerated_keys = run_clean_recipes(force)
    # 2：删减（已有过滤产物默认复用）
    filtered_machine_recipes, active_keys, abstract_groups = run_filter_recipes(
        cleaned_entries, regenerated_keys, force
    )
    # 3：冲突图与着色（每次运行）
    run_color_recipes(filtered_machine_recipes, active_keys, abstract_groups)
    print(f"处理完成，发现 {issue_counter.count} 个问题。详细日志: {config.LOG_FILE}")


if __name__ == "__main__":
    main()
