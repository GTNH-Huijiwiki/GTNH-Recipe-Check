"""
split.py
数据准备脚本：从 GTNH Dumper 导出生成 Items / OreDictionary / Recipes 三类数据。

本脚本从 databaseDump/split_all.py 中抽出「生成 Items、OreDictionary、Recipes 数据」
的功能子集，去掉上传 / 图标 / Gtmaterial / Ticmaterial / Aspects / Index / Ores 等
无关功能，输出到 config.py 指定的 split 输出目录。

数据源与输出目录均取自项目根 config.py：
    DUMPER_DIR     待处理导出数据（advitempanel.json / fluid.json / oreDictionary.json / recipes/）
    DATA_SPLIT_DIR 处理后的输出目录（即本仓库其它模块的 DATA_DIR）

输出结构（DATA_SPLIT_DIR）：
    Items.json              (advitempanel.json -> 单文件，每条仅 key/originalName/translatedName)
    OreDictionary.json      (oreDictionary.json -> 单文件，{oreDict: items})
    Recipes/                (recipes/**/*.json -> 单文件 Recipes/<mod>/<handler>.json + NBT 键修正)
    nbt_regress_cache.json  (NBT 退行缓存，中间产物)
    nbt_missing.csv         (Recipes NBT 未匹配清单，中间产物，仅缺失时生成)

用法（在仓库根目录运行）：
    python scripts/split.py                # items + oredict + recipes（同一进程内依次执行）
    python scripts/split.py items          # 仅 Items
    python scripts/split.py oredict        # 仅 OreDictionary
    python scripts/split.py recipes        # 仅 Recipes（独立运行无法做 NBT key 解析，见下）
    python scripts/split.py items recipes  # 多个子步骤（items 必须先于 recipes）

Items 落盘只保留 key/originalName/translatedName；Recipes 的 NBT key 解析依赖完整
item 信息（rawKey/nbt），因此只有同一进程内先执行过 items 时才可解析。
"""

import glob
import json
import os
import re
import sys

# 让进度输出实时刷新
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, "reconfigure") else None

# 无论以脚本方式还是 `python -m scripts.split` 运行，都保证能 import 到仓库根的 config.py。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import config  # noqa: E402  —— 项目根目录集中配置
from recipe_association import RecipeAssociations  # noqa: E402
from recipe_roles import normalize_recipe_roles  # noqa: E402

_FORMAT_CODE_RE = re.compile(r"§.")


def _clean_text(value):
    """去掉 Minecraft 格式码并压缩首尾空白；落盘前才对小体量三列数据执行。"""
    return _FORMAT_CODE_RE.sub("", str(value or "")).strip()


DUMPER_DIR = str(config.DUMPER_DIR)          # split 的输入：导出数据
DATA_SPLIT_DIR = str(config.DATA_SPLIT_DIR)  # split 的输出：本仓库其它模块的数据根目录

def _load_nbt_regress_cache():
    """加载 NBT 退行缓存（位于最终结果文件夹）。返回 (regress_set, keep_set)。"""
    cache_path = os.path.join(DATA_SPLIT_DIR, "nbt_regress_cache.json")
    if not os.path.exists(cache_path):
        return set(), set()
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("regress", [])), set(data.get("keep", []))
    except Exception:
        return set(), set()


def _save_nbt_regress_cache(regress_set, keep_set):
    """保存 NBT 退行缓存（位于最终结果文件夹）。"""
    cache_path = os.path.join(DATA_SPLIT_DIR, "nbt_regress_cache.json")
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"regress": sorted(regress_set), "keep": sorted(keep_set)}, f, ensure_ascii=False, indent=2)


# IC2 充电工具前缀：:1 是没电，:26/:27 是满电。复制 :1 为裸 key。
_CHARGEABLE_TOOL_PREFIXES = {
    "IC2:", "GraviSuite:", "AdvancedSolarPanel:", "EMT:",
}

def _fix_ic2_chargeable_tools(items: list):
    """充电工具：:1=没电，:26/:27=满电。
    复制 :1 变体为去 meta 的裸 key 版本，插入在原条目旁边。"""
    inserted = 0
    existing = {
        value
        for item in items
        for value in (item.get("key"), item.get("shortKey"))
        if value
    }
    i = 0
    while i < len(items):
        item = items[i]
        rk = item.get("key", "")  # 此时尚未 rename，key 即原始含 meta 的 key
        if not rk.endswith(":1"):
            i += 1
            continue
        base_key = rk[:-2]  # 去掉 :1
        # 检查是否在充电工具前缀列表中
        is_chargeable = False
        for prefix in _CHARGEABLE_TOOL_PREFIXES:
            if base_key.startswith(prefix):
                is_chargeable = True
                break
        if not is_chargeable:
            i += 1
            continue
        # 向前后找 :26 或 :27 变体
        has_charged = False
        for j in (i - 1, i + 1):
            if 0 <= j < len(items):
                nk = items[j].get("key", "")
                if nk == base_key + ":26" or nk == base_key + ":27":
                    has_charged = True
                    break
        if has_charged:
            # 新导出已自带裸 key 时不再重复注入
            if base_key in existing:
                i += 1
                continue
            # 复制 :1 条目，去掉 meta
            copy_item = dict(item)
            copy_item["key"] = base_key
            copy_item["shortKey"] = base_key
            items.insert(i + 1, copy_item)
            existing.add(base_key)
            inserted += 1
            i += 2  # 跳过插入的条目
        else:
            i += 1
    if inserted:
        print(f"  [IC2Tools] Inserted {inserted} bare-key copies for IC2 chargeable tools")


def _fix_aspectrecipeindex_keys(items: list):
    """修正 aspectrecipeindex:aspect 物品的 key：用 originalName 替换无意义的 NBT hash。
    aspectrecipeindex:aspect_85gjJ462zA0lgsJ → aspectrecipeindex:aspect_Terra"""
    fixed = 0
    for item in items:
        if item.get("rawKey") == "aspectrecipeindex:aspect":
            oname = _clean_text(item.get("originalName", ""))
            if oname:
                new_key = f"aspectrecipeindex:aspect_{oname}"
                if item.get("key") != new_key:
                    item["key"] = new_key
                    fixed += 1
    if fixed:
        print(f"  [AspectRecipeIndex] Fixed {fixed} keys (NBT hash → originalName)")


def _regress_nbt_only_items(items: list):
    """退行机制：对于 rawKey 唯一的 item（只有一个 NBT 变体），
    如果其 key（带 NBT 后缀）从未在 recipe 中出现过，则退行 key → rawKey。

    前提：rawKey 相同的 item 总是连续排列。
    使用 nbt_regress_cache.json 缓存已确认的结果，避免重复扫描 recipe。
    """
    recipes_dir = os.path.join(DUMPER_DIR, "recipes")

    # Step 0: 空 NBT (nbt: "{}") 对应的固定 hash 后缀 _gLuB0gegBqh1aRe，直接退行
    EMPTY_NBT_SUFFIX = "_gLuB0gegBqh1aRe"
    empty_nbt_regressed = 0
    for item in items:
        k = item.get("key", "")
        rk = item.get("rawKey", "")
        if k and rk and k.endswith(EMPTY_NBT_SUFFIX) and k != rk:
            item["key"] = rk
            empty_nbt_regressed += 1
    if empty_nbt_regressed:
        print(f"  [NBT regress] Stripped empty-NBT suffix ({EMPTY_NBT_SUFFIX}) from {empty_nbt_regressed} items")

    # Step 1: 用全量计数找出 rawKey 唯一的 NBT 单例。
    # 新导出的条目不再按 rawKey 连续排列，不能依赖相邻分组。
    raw_counts = {}
    for item in items:
        raw = item.get("rawKey", "")
        if raw:
            raw_counts[raw] = raw_counts.get(raw, 0) + 1

    nbt_keys_to_check = {}   # key → index in items
    for idx, item in enumerate(items):
        rk = item.get("rawKey", "")
        k = item.get("key", "")
        if rk and k and k != rk and raw_counts.get(rk) == 1:
            nbt_keys_to_check[k] = idx

    if not nbt_keys_to_check:
        print("  [NBT regress] No NBT-singleton items to check.")
        return

    # Step 2: 加载缓存，分流
    cache_regress, cache_keep = _load_nbt_regress_cache()
    cache_hits = 0
    regressed_from_cache = 0
    keys_to_scan = {}  # key → index (not in cache)

    for k, idx in nbt_keys_to_check.items():
        if k in cache_regress:
            # 缓存确认：recipe 中未出现 → 直接退行
            items[idx]["key"] = items[idx]["rawKey"]
            regressed_from_cache += 1
            cache_hits += 1
        elif k in cache_keep:
            # 缓存确认：recipe 中出现过 → 跳过
            cache_hits += 1
        else:
            keys_to_scan[k] = idx

    if regressed_from_cache:
        print(f"  [NBT regress] {regressed_from_cache} keys regressed from cache")
    if cache_hits:
        print(f"  [NBT regress] {cache_hits} keys resolved from cache, {len(keys_to_scan)} new keys need scanning")

    if not keys_to_scan:
        return

    if not os.path.isdir(recipes_dir):
        print("  [NBT regress] recipes 目录不存在，跳过剩余 {len(keys_to_scan)} 个 key 的扫描")
        return

    # Step 3: 扫描 recipe 文件，纯字符串匹配（仅对新 key）
    print(f"  [NBT regress] Scanning recipes for {len(keys_to_scan)} new keys...")
    unmatched = set(keys_to_scan.keys())
    recipe_files = []
    for root, dirs, files in os.walk(recipes_dir):
        for fn in files:
            if fn.endswith(".json"):
                recipe_files.append(os.path.join(root, fn))
    string_re = re.compile(r'"((?:\\.|[^"\\])*)"')
    for rpath in recipe_files:
        if not unmatched:
            break
        with open(rpath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # 一次提取文件内所有 JSON 字符串，避免对每个候选 key 做整文件子串扫描
        unmatched -= set(string_re.findall(content))

    # 更新缓存：未匹配到的 → regress，匹配到的 → keep
    new_regress = unmatched
    new_keep = set(keys_to_scan.keys()) - unmatched
    cache_regress.update(new_regress)
    cache_keep.update(new_keep)
    _save_nbt_regress_cache(cache_regress, cache_keep)
    print(f"  [NBT regress] Cache updated: +{len(new_regress)} regress, +{len(new_keep)} keep")

    # Step 4: 对未匹配到的 key，退行 key → rawKey
    regressed = 0
    for k in new_regress:
        idx = keys_to_scan[k]
        item = items[idx]
        if item.get("key") == k and item.get("rawKey"):
            item["key"] = item["rawKey"]
            regressed += 1

    total_regressed = regressed_from_cache + regressed
    print(f"  [NBT regress] Regressed {regressed} items from scan ({total_regressed} total: key → rawKey, NBT suffix stripped).")


# ============================================================
# Custom item injection
# ============================================================

def _inject_custom_items(items: list):
    """注入导出可能缺失的自定义条目（如编程电路各配置变体）。"""
    n = len(items)
    existing = {
        value
        for item in items
        for value in (item.get("key"), item.get("shortKey"))
        if value
    }

    # 编程电路 (gregtech:gt.integrated_circuit) — 配置 1~24
    for i in range(1, 25):
        key = f"gregtech:gt.integrated_circuit:{i}"
        if key in existing:
            continue
        items.append({
            "shortKey": key,
            "key": key,
            "originalName": "Programmed Circuit",
            "translatedName": f"编程电路{i}",
        })
        existing.add(key)

    print(f"  Injected {len(items) - n} custom items")


# ============================================================
# Items
# ============================================================

# 完整 items（含 rawKey/nbt/key），供同进程内 Recipes 的 NBT key 解析使用；
# 落盘时只投影为 key/originalName/translatedName 三列。
_FULL_ITEMS = None


def _load_fluid_key_map():
    """加载 fluid.json，构建 {gtDisplayKey: fluid.key} 映射（仅用于改写流体 key）。"""
    path = os.path.join(DUMPER_DIR, "fluid.json")
    print(f"  Loading {os.path.basename(path)} ({os.path.getsize(path) / 1024 / 1024:.1f} MB)...")
    data = json.load(open(path, "r", encoding="utf-8"))
    fluid_keys = {}
    for entry in data["fluids"]:
        gtk = entry.get("gtDisplayKey", "")
        if gtk:
            fluid_keys[gtk] = entry.get("key", "")
    del data
    print(f"  Fluid key map: {len(fluid_keys):,} entries")
    return fluid_keys


def _rewrite_fluid_keys(items: list, fluid_keys: dict):
    """将流体 item 的 shortKey/key 改写为 fluid.key。
    流体 item 的 originalName/translatedName 已自带且与 fluid 相同，无需合并其它字段。"""
    rewritten = 0
    for item in items:
        fluid_key = fluid_keys.get(item.get("key", ""))
        if fluid_key is None:
            continue
        item["shortKey"] = fluid_key
        item["key"] = fluid_key
        rewritten += 1
    print(f"  Fluid keys rewritten: {rewritten} items")


def _dedupe_items(items: list) -> list:
    """按最终 key 去重；新导出自带条目与旧注入逻辑可能产生同 key。"""
    seen = set()
    unique = []
    dropped = 0
    for item in items:
        key = item.get("key", "")
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        unique.append(item)
    if dropped:
        print(f"  [Items] Dropped {dropped} duplicate key(s)")
    return unique


def _project_item(item: dict) -> dict:
    """Items 落盘只保留三列，并在裁剪后去除格式码。"""
    return {
        "key": item.get("key", ""),
        "originalName": _clean_text(item.get("originalName", "")),
        "translatedName": _clean_text(item.get("translatedName", "")),
    }


def _write_items_json(items: list, out_path: str):
    """items 投影为 {key, originalName, translatedName} 后整体写入单个 Items.json。"""
    entries = [_project_item(it) for it in items]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    payload = {"items": entries}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    size = os.path.getsize(out_path)
    print(f"  -> {os.path.basename(out_path)}: {len(entries):,} entries, {size / 1024:.1f} KB\n")


def do_items():
    """Split advitempanel.json -> Items.json（仅保留 key/originalName/translatedName 三列）"""
    global _FULL_ITEMS
    print("\n[Items]")
    path = os.path.join(DUMPER_DIR, "advitempanel.json")
    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"  Loading {os.path.basename(path)} ({size_mb:.1f} MB)...")
    data = json.load(open(path, "r", encoding="utf-8"))
    items = data["shortKey"]
    print(f"  Total: {len(items):,} items")
    del data

    # ① 流体：改写 key（GregTech_FluidDisplay:<id> -> fluid.key）。
    #    名字字段 item 已自带且与 fluid 相同，无需加载图标或合并其它字段。
    fluid_keys = _load_fluid_key_map()
    _rewrite_fluid_keys(items, fluid_keys)

    # ② 注入自定义条目（编程电路 1~24，与现有 wiki 页面对齐）
    _inject_custom_items(items)

    # ③ IC2 充电工具：:1 变体复制为去 meta 裸 key
    _fix_ic2_chargeable_tools(items)

    # ④ key/shortKey 对调（key = 原 shortKey）
    for item in items:
        sk = item.pop("shortKey", None)
        k = item.get("key")
        if sk and k:
            item["rawKey"] = k
            item["key"] = sk
        elif sk:
            item["key"] = sk

    # ⑤ 修正 aspectrecipeindex:aspect 的 key：用 originalName 替换无意义的 NBT hash
    _fix_aspectrecipeindex_keys(items)

    # ⑥ 退行：唯一 NBT 变体且 recipe 中未引用 → key 退行为 rawKey
    _regress_nbt_only_items(items)

    # ⑦ 去重：新导出自带条目与旧注入逻辑可能产生同一最终 key
    items = _dedupe_items(items)

    # 记录完整数据（含 rawKey/nbt）供同进程内 Recipes 的 NBT key 解析
    _FULL_ITEMS = items

    # ⑧ 写入：只保留 key/originalName/translatedName，单文件 Items.json
    _write_items_json(items, os.path.join(DATA_SPLIT_DIR, "Items.json"))


# ============================================================
# OreDictionary
# ============================================================

def _wildcard_base(raw):
    """通配 key 的注册名：去掉末尾数字 meta。"""
    if raw.count(":") >= 2 and raw.rsplit(":", 1)[1].isdigit():
        return raw.rsplit(":", 1)[0]
    return raw


def _normalize_ore_dictionary(flat, items):
    """按真实物品变体展开矿辞通配，并报告未解析/缺失。"""
    wildcard_index = {}
    item_keys = set()
    for item in items:
        raw = item.get("rawKey") or item.get("key", "")
        key = item.get("key", "")
        if not raw or not key or raw.endswith(":32767"):
            continue
        wildcard_index.setdefault(_wildcard_base(raw), set()).add(key)
        item_keys.add(key)

    normalized = {}
    unresolved = []
    missing_exact = []
    expanded = 0
    for ore_name, members in flat.items():
        out = []
        seen = set()
        for key in members:
            if key.endswith(":32767"):
                candidates = sorted(wildcard_index.get(key[:-6], ()))
                expanded += 1
                if not candidates:
                    unresolved.append({"oreDict": ore_name, "key": key})
                    continue
            else:
                candidates = [key]
                if key not in item_keys:
                    missing_exact.append({"oreDict": ore_name, "key": key})
            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    out.append(candidate)
        normalized[ore_name] = out
    return normalized, unresolved, missing_exact, expanded


def do_oredictionary():
    """oreDictionary.json -> OreDictionary.json（单文件 {oreDict: items}，无更深结构）"""
    print("\n[OreDictionary]")
    path = os.path.join(DUMPER_DIR, "oreDictionary.json")
    size_mb = os.path.getsize(path) / 1024 / 1024
    print(f"  Loading {os.path.basename(path)} ({size_mb:.1f} MB)...")
    data = json.load(open(path, "r", encoding="utf-8"))
    entries = data["oreDictionaries"]
    del data

    # 单文件扁平对象 {oreDict_name: items_array}
    flat: dict[str, list] = {}
    for entry in entries:
        flat[entry["oreDict"]] = entry["items"]
    print(f"  Total: {len(flat):,} oreDict keys")

    if _FULL_ITEMS is not None:
        flat, unresolved, missing_exact, expanded = _normalize_ore_dictionary(
            flat, _FULL_ITEMS
        )
        print(
            f"  Wildcards expanded: {expanded:,}; "
            f"unresolved: {len(unresolved)}, missing exact: {len(missing_exact)}"
        )
        report = {
            "unresolved_wildcards": unresolved,
            "missing_exact_keys": missing_exact,
        }
        report_path = os.path.join(DATA_SPLIT_DIR, "oredictionary_associations.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  OreDictionary association report: {report_path}")
    else:
        print("  [OreDictionary] Items 未加载，跳过通配/缺失检查")

    out_path = os.path.join(DATA_SPLIT_DIR, "OreDictionary.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, ensure_ascii=False, indent=2)
    size = os.path.getsize(out_path)
    print(f"  -> OreDictionary.json: {len(flat):,} keys, {size / 1024:.1f} KB\n")


# ============================================================
# Recipes
# ============================================================

def _fix_recipe_format(recipes: list, filepath: str) -> list:
    """修正非标准 NEI recipe 格式为标准格式。
    如果配方已经是标准格式（有 outputFluids / inputFluids / outputItems），
    则跳过，避免未来数据已标准化时重复破坏。"""
    fixed = []
    for r in recipes:
        r = dict(r)

        # 已经是标准格式 → 不改
        if any(k in r for k in ("outputFluids", "inputFluids", "outputItems", "inputItems")):
            fixed.append(r)
            continue

        if "GasSiphonRecipeHandler" in filepath:
            # 旧格式：{fluid, planet, depth, outputAmount}
            # → 新格式：{outputFluids, eut, duration, metadata}
            fluid_key = r.pop("fluid", {}).get("key", "")
            amount = r.pop("outputAmount", 0)
            r["outputFluids"] = [{"key": fluid_key, "amount": amount}]
            r["eut"] = 0
            r["duration"] = 0
            planet = r.pop("planet", "")
            depth = r.pop("depth", 1)
            r["metadata"] = {"planet": planet, "depth": depth}
        elif "SpacePumpModuleRecipeHandler" in filepath:
            # 旧格式：{fluid, planetType, gasType, amount}
            # → 新格式：{outputFluids, eut, duration, metadata}
            fluid_key = r.pop("fluid", {}).get("key", "")
            amount = r.pop("amount", 0)
            r["outputFluids"] = [{"key": fluid_key, "amount": amount}]
            r["eut"] = 0
            r["duration"] = 0
            planet_type = r.pop("planetType", 0)
            gas_type = r.pop("gasType", 0)
            r["metadata"] = {"planetType": planet_type, "gasType": gas_type}
        fixed.append(r)
    print(f"    Fixed {len(fixed)} non-standard recipes -> standard format")
    return fixed


def do_recipes():
    """recipes/**/*.json -> Recipes/<mod>/<handler>.json（单文件，保留源相对路径与命名）"""
    print("\n[Recipes]")
    recipes_dir = os.path.join(DUMPER_DIR, "recipes")
    json_files = glob.glob(os.path.join(recipes_dir, "**/*.json"), recursive=True)
    json_files.sort()
    print(f"  Found {len(json_files)} source file(s)")

    associations = (
        RecipeAssociations(_FULL_ITEMS) if _FULL_ITEMS is not None else None
    )
    if associations is None:
        print("  [NBT fix] 本进程未先运行 items，跳过 NBT/通配归一化")

    written_files = 0
    written_recipes = 0
    for fp in json_files:
        rel = os.path.relpath(fp, recipes_dir)   # 如 gregtech/GTNEIDefaultHandler_abs_non_alloy_recipes.json
        try:
            data = json.load(open(fp, "r", encoding="utf-8"))
        except Exception as e:
            print(f"  !! 读取失败 {rel}: {e}")
            continue

        recipes = [r for r in data.get("recipes", []) if isinstance(r, dict)]
        if not recipes:
            continue

        # 修正非标准 recipe 格式（行星气体钻机 / 太空钻机）
        if "GasSiphonRecipeHandler" in fp or "SpacePumpModuleRecipeHandler" in fp:
            recipes = _fix_recipe_format(recipes, fp)

        # Special case: bee genetics NBT data makes individual recipes >2MB
        if "AcclimatiserRecipeHandler_acclimatiser" in fp:
            print(f"  !! 跳过（蜜蜂基因 NBT 过大）{rel}")
            continue

        out_obj = {k: v for k, v in data.items() if k != "recipes"}
        out_obj["recipes"] = recipes

        if associations is not None:
            for recipe in recipes:
                normalize_recipe_roles(recipe, out_obj)
                associations.associate(recipe, rel)

        out_path = os.path.join(DATA_SPLIT_DIR, "Recipes", rel)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_obj, f, ensure_ascii=False, indent=2)

        print(f"  {rel}: {len(recipes):,} recipes")
        written_files += 1
        written_recipes += len(recipes)

    print(f"  Recipes done: {written_files:,} file(s), {written_recipes:,} recipes")
    if associations is not None:
        associations.write_reports(DATA_SPLIT_DIR)


# ============================================================
# Entry point
# ============================================================

TASKS = {
    "items":   ("Items",         do_items),
    "oredict": ("OreDictionary", do_oredictionary),
    "recipes": ("Recipes",       do_recipes),
}


def main():
    args = [a.lower() for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        tasks_to_run = list(TASKS.keys())
    else:
        tasks_to_run = [a for a in args if a in TASKS]
        unknown = [a for a in args if a not in TASKS]
        if unknown:
            print(f"Unknown task(s): {unknown}")
            print(f"Available: {', '.join(TASKS.keys())}")
            return

    print(f"Output root: {DATA_SPLIT_DIR}")
    for key in tasks_to_run:
        TASKS[key][1]()

    print("\nAll done!")


if __name__ == "__main__":
    main()
