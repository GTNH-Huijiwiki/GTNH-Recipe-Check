"""本项目使用的 Wiki Toolchain 第一步预处理。

下列数据变换对齐 GTNH-Chinese-Wiki-Toolchain/databaseDump/split_all.py
的提交 8a94345a530fb1d843edaf814b253207bc74ac27。Wiki 页面生成、图片与
tooltip 处理以及本项目不消费的数据域均不在此实现。
"""

from __future__ import annotations

import json
import logging
import os
import re

from s1_preprocess.recipe_association import RecipeAssociations
from s1_preprocess.recipe_roles import normalize_recipe_roles

logger = logging.getLogger(__name__)

_CHARGEABLE_TOOL_PREFIXES = {
    "IC2:",
    "GraviSuite:",
    "AdvancedSolarPanel:",
    "EMT:",
}


def _load_nbt_regress_cache(output_dir):
    cache_path = os.path.join(output_dir, "nbt_regress_cache.json")
    if not os.path.exists(cache_path):
        return set(), set()
    try:
        with open(cache_path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        return set(data.get("regress", [])), set(data.get("keep", []))
    except Exception:
        return set(), set()


def _save_nbt_regress_cache(output_dir, regress_set, keep_set):
    cache_path = os.path.join(output_dir, "nbt_regress_cache.json")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as stream:
        json.dump(
            {"regress": sorted(regress_set), "keep": sorted(keep_set)},
            stream,
            ensure_ascii=False,
            indent=2,
        )


def _fix_ic2_chargeable_tools(items: list):
    """将 IC2 系列 :1 充电状态变体复制为裸 key。"""
    inserted = 0
    existing_keys = {item.get("key") for item in items}
    i = 0
    while i < len(items):
        item = items[i]
        raw_key = item.get("key", "")
        if not raw_key.endswith(":1"):
            i += 1
            continue
        base_key = raw_key[:-2]
        if base_key in existing_keys:
            i += 1
            continue
        if not any(base_key.startswith(prefix) for prefix in _CHARGEABLE_TOOL_PREFIXES):
            i += 1
            continue
        has_charged = False
        for index in (i - 1, i + 1):
            if 0 <= index < len(items):
                nearby = items[index].get("key", "")
                if nearby in (base_key + ":26", base_key + ":27"):
                    has_charged = True
                    break
        if not has_charged:
            i += 1
            continue
        copy_item = dict(item)
        copy_item["key"] = base_key
        copy_item["shortKey"] = base_key
        items.insert(i + 1, copy_item)
        existing_keys.add(base_key)
        inserted += 1
        i += 2
    if inserted:
        logger.debug("[IC2 工具] 插入 %d 个裸 key 副本", inserted)


def _fix_aspectrecipeindex_keys(items: list):
    fixed = 0
    for item in items:
        if item.get("rawKey") != "aspectrecipeindex:aspect":
            continue
        original_name = item.get("originalName", "")
        if not original_name:
            continue
        new_key = f"aspectrecipeindex:aspect_{original_name}"
        if item.get("key") != new_key:
            item["key"] = new_key
            fixed += 1
    if fixed:
        logger.debug("[AspectRecipeIndex] 修正 %d 个 key", fixed)


def _regress_nbt_only_items(items: list, source_dir, output_dir):
    recipes_dir = os.path.join(source_dir, "recipes")

    empty_nbt_suffix = "_gLuB0gegBqh1aRe"
    empty_nbt_regressed = 0
    existing_keys = {item.get("key") for item in items}
    for item in items:
        key = item.get("key", "")
        raw_key = item.get("rawKey", "")
        if (
            key
            and raw_key
            and key.endswith(empty_nbt_suffix)
            and key != raw_key
            and raw_key not in existing_keys
        ):
            item["key"] = raw_key
            empty_nbt_regressed += 1
    if empty_nbt_regressed:
        logger.debug(
            "[NBT 退行] 从 %d 条物品中移除空 NBT 后缀 %s",
            empty_nbt_regressed,
            empty_nbt_suffix,
        )

    raw_key_counts = {}
    for item in items:
        raw_key = item.get("rawKey", "")
        raw_key_counts[raw_key] = raw_key_counts.get(raw_key, 0) + 1

    nbt_keys_to_check = {}
    for index, item in enumerate(items):
        raw_key = item.get("rawKey", "")
        key = item.get("key", "")
        if raw_key_counts[raw_key] == 1 and key and raw_key and key != raw_key:
            nbt_keys_to_check[key] = index

    if not nbt_keys_to_check:
        logger.debug("[NBT 退行] 没有需要检查的 NBT 单例物品")
        return

    cache_regress, cache_keep = _load_nbt_regress_cache(output_dir)
    cache_hits = 0
    regressed_from_cache = 0
    keys_to_scan = {}

    for key, index in nbt_keys_to_check.items():
        if key in cache_regress:
            items[index]["key"] = items[index]["rawKey"]
            regressed_from_cache += 1
            cache_hits += 1
        elif key in cache_keep:
            cache_hits += 1
        else:
            keys_to_scan[key] = index

    if regressed_from_cache:
        logger.debug("[NBT 退行] 根据缓存退行 %d 个 key", regressed_from_cache)
    if cache_hits:
        logger.debug(
            "[NBT 退行] 缓存命中 %d 个 key，剩余 %d 个 key 需要扫描",
            cache_hits,
            len(keys_to_scan),
        )
    if not keys_to_scan:
        return

    if not os.path.isdir(recipes_dir):
        logger.warning(
            "[NBT 退行] recipes 目录不存在，跳过 %d 个 key 的扫描",
            len(keys_to_scan),
        )
        return

    logger.debug("[NBT 退行] 扫描配方以检查 %d 个新 key", len(keys_to_scan))
    unmatched = set(keys_to_scan)
    recipe_files = []
    for root, _dirs, files in os.walk(recipes_dir):
        for filename in files:
            if filename.endswith(".json"):
                recipe_files.append(os.path.join(root, filename))
    string_re = re.compile(r'"((?:\\.|[^"\\])*)"')
    for recipe_path in recipe_files:
        if not unmatched:
            break
        with open(recipe_path, "r", encoding="utf-8", errors="replace") as stream:
            content = stream.read()
        unmatched -= set(string_re.findall(content))

    new_regress = unmatched
    new_keep = set(keys_to_scan) - unmatched
    cache_regress.update(new_regress)
    cache_keep.update(new_keep)
    _save_nbt_regress_cache(output_dir, cache_regress, cache_keep)
    logger.debug(
        "[NBT 退行] 缓存更新：新增退行 %d 个，保留 %d 个",
        len(new_regress),
        len(new_keep),
    )

    regressed = 0
    for key in new_regress:
        index = keys_to_scan[key]
        item = items[index]
        if item.get("key") == key and item.get("rawKey"):
            item["key"] = item["rawKey"]
            regressed += 1
    total_regressed = regressed_from_cache + regressed
    logger.debug(
        "[NBT 退行] 扫描后退行 %d 条物品，共退行 %d 条",
        regressed,
        total_regressed,
    )


def _inject_custom_items(items: list):
    """按 Wiki Toolchain 原样注入自定义物品变体。"""
    start = len(items)
    for index in range(1, 25):
        items.append(
            {
                "shortKey": f"gregtech:gt.integrated_circuit:{index}",
                "key": f"gregtech:gt.integrated_circuit:{index}",
                "originalName": "Programmed Circuit",
                "translatedName": f"编程电路{index}",
                "tooltips": (
                    f"编程电路<br>配置== {index}<br>右键以重新配置"
                    f"<br>需要螺丝刀或编程工具"
                ),
                "mod": "GregTech",
                "iconName": f"icon_gregtech_gt.integrated_circuit_{index}.png",
                "size": 16,
                "frameCount": 1,
            }
        )
    logger.debug("[自定义物品] 注入 %d 条，物品总数 %d -> %d", len(items) - start, start, len(items))


def _load_fluid_map(source_dir):
    path = os.path.join(source_dir, "fluid.json")
    logger.debug("加载流体数据：%s（%.1f MB）", path, os.path.getsize(path) / 1024 / 1024)
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    fluid_map = {}
    for entry in data["fluids"]:
        display_key = entry.get("gtDisplayKey", "")
        if display_key:
            fluid_map[display_key] = entry
    del data
    logger.debug("流体映射条目：%d", len(fluid_map))
    return fluid_map


def _merge_fluid_data(items: list, fluid_map: dict):
    """按 Wiki 预处理规则合并流体 key 与字段。"""
    fluid_matched = set()
    merged = 0
    for item in items:
        item_key = item.get("key", "")
        fluid = fluid_map.get(item_key)
        if fluid is None:
            continue
        fluid_key = fluid.get("key", "")
        item["shortKey"] = fluid_key
        item["key"] = fluid_key
        skip_fields = {"key", "gtDisplayKey"}
        for field, value in fluid.items():
            if field in skip_fields or field in item:
                continue
            if value is not None:
                item[field] = value
        fluid_matched.add(item_key)
        merged += 1

    unmatched = [key for key in fluid_map if key not in fluid_matched]
    if unmatched:
        logger.warning("%d 个流体未匹配任何物品", len(unmatched))
        for display_key in unmatched[:10]:
            fluid = fluid_map[display_key]
            logger.warning(
                "未匹配流体：gtDisplayKey=%s，fluid.key=%s",
                display_key,
                fluid.get("key", "?"),
            )
    logger.debug("合并流体数据：%d 条物品", merged)


def _project_item(item: dict) -> dict:
    return {
        "key": item.get("key", ""),
        "originalName": item.get("originalName", ""),
        "translatedName": item.get("translatedName", ""),
    }


def _write_items_json(items: list, output_dir):
    entries = [_project_item(item) for item in items]
    path = os.path.join(output_dir, "Items.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({"items": entries}, stream, ensure_ascii=False, indent=2)
    size = os.path.getsize(path)
    logger.debug("写入 Items.json：%d 条，%.1f KB", len(entries), size / 1024)


def run_items(source_dir, output_dir):
    """预处理 advitempanel.json，并返回含完整身份字段的内存物品记录。"""
    logger.debug("开始处理 Items")
    path = os.path.join(source_dir, "advitempanel.json")
    size_mb = os.path.getsize(path) / 1024 / 1024
    logger.debug("加载 %s（%.1f MB）", os.path.basename(path), size_mb)
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    items = data["shortKey"]
    logger.debug("原始物品条目：%d", len(items))
    del data

    fluid_map = _load_fluid_map(source_dir)
    _merge_fluid_data(items, fluid_map)
    _inject_custom_items(items)
    _fix_ic2_chargeable_tools(items)

    for item in items:
        short_key = item.pop("shortKey", None)
        key = item.get("key")
        if short_key and key:
            item["rawKey"] = key
            item["key"] = short_key
        elif short_key:
            item["key"] = short_key

    _fix_aspectrecipeindex_keys(items)
    _regress_nbt_only_items(items, source_dir, output_dir)
    _write_items_json(items, output_dir)
    return items


def _index_wildcard_item(item, wildcard_index):
    raw = item.get("rawKey") or item.get("key", "")
    key = item.get("key", "")
    if not raw or not key or raw.endswith(":32767"):
        return
    base = (
        raw.rsplit(":", 1)[0]
        if raw.count(":") >= 2 and raw.rsplit(":", 1)[1].isdigit()
        else raw
    )
    wildcard_index.setdefault(base, set()).add(key)


def normalize_ore_dictionary(entries, items):
    """按 Wiki 预处理规则发布矿辞成员。"""
    wildcard_index = {}
    item_keys = set()
    for item in items:
        _index_wildcard_item(item, wildcard_index)
        item_keys.add(item["key"])

    flat = {}
    unresolved = []
    missing_exact = []
    expanded = 0
    for entry in entries:
        members = []
        seen = set()
        for key in entry["items"]:
            if key.endswith(":32767"):
                candidates = sorted(wildcard_index.get(key[:-6], ()))
                expanded += 1
                if not candidates:
                    unresolved.append({"oreDict": entry["oreDict"], "key": key})
            else:
                candidates = [key]
                if key not in item_keys:
                    missing_exact.append({"oreDict": entry["oreDict"], "key": key})
            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    members.append(candidate)
        flat[entry["oreDict"]] = members
    return flat, {
        "expandedWildcardMembers": expanded,
        "unresolvedWildcardMembers": unresolved,
        "missingExactMembers": missing_exact,
    }


def run_oredictionary(source_dir, output_dir, items):
    logger.debug("开始处理 OreDictionary")
    path = os.path.join(source_dir, "oreDictionary.json")
    size_mb = os.path.getsize(path) / 1024 / 1024
    logger.debug("加载 %s（%.1f MB）", os.path.basename(path), size_mb)
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    entries = data["oreDictionaries"]
    del data

    flat, report = normalize_ore_dictionary(entries, items)
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "oredictionary_associations.json")
    with open(report_path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    logger.debug(
        "矿辞通配展开：%d 个；无导出候选：%d 个",
        report["expandedWildcardMembers"],
        len(report["unresolvedWildcardMembers"]),
    )

    out_path = os.path.join(output_dir, "OreDictionary.json")
    with open(out_path, "w", encoding="utf-8") as stream:
        json.dump(flat, stream, ensure_ascii=False, indent=2)
    size = os.path.getsize(out_path)
    logger.debug(
        "写入 OreDictionary.json：%d 个 key，%.1f KB",
        len(flat),
        size / 1024,
    )
    return report, len(flat)


def _fix_recipe_format(recipes: list, filepath: str) -> list:
    fixed = []
    for recipe in recipes:
        recipe = dict(recipe)
        if any(
            key in recipe
            for key in ("outputFluids", "inputFluids", "outputItems", "inputItems")
        ):
            fixed.append(recipe)
            continue

        if "GasSiphonRecipeHandler" in filepath:
            fluid_key = recipe.pop("fluid", {}).get("key", "")
            amount = recipe.pop("outputAmount", 0)
            recipe["outputFluids"] = [{"key": fluid_key, "amount": amount}]
            recipe["duration"] = 0
            recipe.setdefault("baseRatePerSecond", amount * 2)
            planet = recipe.pop("planet", "")
            depth = recipe.pop("depth", 1)
            recipe["metadata"] = {"planet": planet, "depth": depth}
        elif "SpacePumpModuleRecipeHandler" in filepath:
            fluid_key = recipe.pop("fluid", {}).get("key", "")
            amount = recipe.pop("amount", 0)
            recipe["outputFluids"] = [{"key": fluid_key, "amount": amount}]
            recipe["eut"] = 0
            recipe["duration"] = 0
            planet_type = recipe.pop("planetType", 0)
            gas_type = recipe.pop("gasType", 0)
            recipe["metadata"] = {"planetType": planet_type, "gasType": gas_type}
        fixed.append(recipe)
    logger.debug("修正非标准配方格式：%d 条", len(fixed))
    return fixed


def run_recipes(source_dir, output_dir, items):
    logger.debug("开始处理 Recipes")
    recipes_dir = os.path.join(source_dir, "recipes")
    json_files = []
    for root, dirs, files in os.walk(recipes_dir):
        dirs[:] = [name for name in dirs if name.casefold() != "filtered"]
        json_files.extend(
            os.path.join(root, name) for name in files if name.endswith(".json")
        )
    json_files.sort()
    logger.debug("发现配方文件：%d 个", len(json_files))

    associations = RecipeAssociations(items)
    written_files = 0
    written_recipes = 0
    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as stream:
                data = json.load(stream)
        except Exception as exc:
            logger.warning("读取配方文件失败：%s，错误：%s", filepath, exc)
            continue

        recipes = [item for item in data.get("recipes", []) if isinstance(item, dict)]
        if not recipes:
            logger.debug("跳过空配方文件：%s", filepath)
            continue

        if "GasSiphonRecipeHandler" in filepath or "SpacePumpModuleRecipeHandler" in filepath:
            recipes = _fix_recipe_format(recipes, filepath)

        if "AcclimatiserRecipeHandler_acclimatiser" in filepath:
            logger.warning("跳过蜜蜂基因 NBT 过大的配方文件：%s", filepath)
            continue

        metadata = {key: value for key, value in data.items() if key != "recipes"}
        rel = os.path.relpath(filepath, recipes_dir)
        output = {**metadata, "recipes": recipes}
        for recipe in recipes:
            normalize_recipe_roles(recipe, metadata)
            associations.associate(recipe, rel)

        out_path = os.path.join(output_dir, "Recipes", rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as stream:
            json.dump(output, stream, ensure_ascii=False, indent=2)
        logger.debug("写入配方文件 %s：%d 条", rel, len(recipes))
        written_files += 1
        written_recipes += len(recipes)

    associations.write_reports(output_dir)
    logger.debug(
        "Recipes 处理完成：%d 个文件，%d 条配方",
        written_files,
        written_recipes,
    )
    return {
        "recipes": written_recipes,
        "recipeFiles": written_files,
        "association": {
            "missingNbt": len(associations.missing),
            "ambiguousNbt": len(associations.ambiguous),
            "unresolvedWildcards": len(associations.unresolved_wildcards),
        },
    }


__all__ = [
    "normalize_ore_dictionary",
    "run_items",
    "run_oredictionary",
    "run_recipes",
]
