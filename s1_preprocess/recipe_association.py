"""配方物品关联，逻辑对齐 Wiki Toolchain 第一步预处理。"""

from __future__ import annotations

import csv
import json
import logging
import os

try:
    from .nbt_identity import nbt_identity
except ImportError:
    from nbt_identity import nbt_identity

logger = logging.getLogger(__name__)

BEE_TYPES_BY_RAW_KEY = {
    "Forestry:beeDroneGE": "DRONE",
    "Forestry:beePrincessGE": "PRINCESS",
    "Forestry:beeQueenGE": "QUEEN",
    "Forestry:beeLarvaeGE": "LARVAE",
}


def _bee_query_key(identity):
    if not isinstance(identity, dict):
        return None
    species_uid = identity.get("speciesUid")
    bee_type = identity.get("type")
    if not isinstance(species_uid, str) or not species_uid:
        return None
    if not isinstance(bee_type, str) or bee_type not in set(BEE_TYPES_BY_RAW_KEY.values()):
        return None
    return f"bee:{bee_type}:{species_uid}"


def _index_wildcard_item(item, wildcard_index, wildcard_nbt_index):
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
    nbt = item.get("nbt") or ""
    if nbt:
        wildcard_nbt_index.setdefault((base, nbt_identity(nbt)), set()).add(key)


def _iter_recipe_item_entries(value):
    if isinstance(value, list):
        for entry in value:
            yield from _iter_recipe_item_entries(entry)
    elif isinstance(value, dict):
        if "key" in value or "beeIdentity" in value:
            yield value
        else:
            for entry in value.values():
                yield from _iter_recipe_item_entries(entry)


class RecipeAssociations:
    """在发布配方前建立精确 NBT 与蜜蜂身份索引。"""

    def __init__(self, items):
        logger.debug("[NBT 修正] 从 Items 建立精确 NBT 与蜜蜂身份索引")
        nbt_index = {}
        bee_index = {}
        wildcard_index = {}
        wildcard_nbt_index = {}
        for item in items:
            raw_key = item.get("rawKey", "") or item.get("key", "")
            nbt = item.get("nbt", "") or ""
            key = item.get("key", "")
            _index_wildcard_item(item, wildcard_index, wildcard_nbt_index)
            if raw_key and key and nbt:
                nbt_index.setdefault((raw_key, nbt_identity(nbt)), set()).add(key)
            query_key = item.get("queryKey") or _bee_query_key(item.get("beeIdentity"))
            if query_key and key:
                bee_index.setdefault(query_key, set()).add(key)
        logger.debug(
            "[NBT 修正] 已索引 %d 组精确 NBT，%d 个蜜蜂身份 key",
            len(nbt_index),
            len(bee_index),
        )

        self.slot_keys = (
            "inputItems",
            "outputItems",
            "otherItems",
            "keyItem",
            "exampleInput",
            "inputFluids",
            "outputFluids",
            "normalDrops",
            "rareDrops",
            "additionalDrops",
            "infernalDrops",
            "eliteItems",
            "ultraItems",
            "infernoItems",
            "fishItems",
            "junkItems",
            "treasureItems",
        )
        self.nbt_index = nbt_index
        self.bee_index = bee_index
        self.wildcard_index = wildcard_index
        self.wildcard_nbt_index = wildcard_nbt_index
        self.total = self.replaced = 0
        self.missing = set()
        self.unresolved_wildcards = set()
        self.diagnostics = []

    def associate(self, recipe, relative_file):
        nbt_index = self.nbt_index
        bee_index = self.bee_index
        wildcard_index = self.wildcard_index
        wildcard_nbt_index = self.wildcard_nbt_index
        modified = False

        def expand_wildcard(entry):
            if isinstance(entry, list):
                expanded = []
                for candidate in entry:
                    value = expand_wildcard(candidate)
                    expanded.extend(value if isinstance(value, list) else [value])
                return expanded
            raw = (
                entry
                if isinstance(entry, str)
                else entry.get("key", "") if isinstance(entry, dict) else ""
            )
            if not raw.endswith(":32767"):
                return entry
            nbt = entry.get("nbt") if isinstance(entry, dict) else None
            choices = sorted(
                wildcard_nbt_index.get((raw[:-6], nbt_identity(nbt)), ())
                if nbt
                else wildcard_index.get(raw[:-6], ())
            )
            if not choices:
                self.unresolved_wildcards.add(raw)
                unresolved = dict(entry) if isinstance(entry, dict) else {"key": raw}
                unresolved["displayKey"] = "unresolved-wildcard:" + raw
                unresolved["associationError"] = "missing_concrete_wildcard_candidates"
                return unresolved
            return [
                dict({key: value for key, value in entry.items() if key != "nbt"}, key=key, rawKey=raw)
                if isinstance(entry, dict)
                else key
                for key in choices
            ]

        if isinstance(recipe.get("inputItems"), list):
            previous = recipe["inputItems"]
            recipe["inputItems"] = [expand_wildcard(entry) for entry in previous]
            modified = modified or recipe["inputItems"] != previous
        if recipe.get("keyItem") is not None:
            previous = recipe["keyItem"]
            recipe["keyItem"] = expand_wildcard(previous)
            modified = modified or recipe["keyItem"] != previous

        for slot_key in self.slot_keys:
            items = recipe.get(slot_key)
            if not isinstance(items, (list, dict)):
                continue
            for item in _iter_recipe_item_entries(items):
                nbt = item.get("nbt", "") or ""
                key = item.get("key", "")
                if not key:
                    continue
                original_key = key
                expected_bee_type = BEE_TYPES_BY_RAW_KEY.get(key)
                identity = item.get("beeIdentity")
                query_key = _bee_query_key(identity)
                if identity is not None or expected_bee_type:
                    if not query_key or (
                        expected_bee_type and identity.get("type") != expected_bee_type
                    ):
                        self.diagnostics.append(
                            {
                                "file": relative_file,
                                "_recipe": recipe,
                                "recipeIndex": recipe.get("index"),
                                "slot": slot_key,
                                "rawKey": key,
                                "beeIdentity": identity,
                                "issue": "missing_or_mismatched_identity_reexport_required",
                            }
                        )
                    else:
                        item["queryKey"] = query_key
                        modified = True

                if not nbt:
                    continue
                self.total += 1
                exact_candidates = nbt_index.get((key, nbt_identity(nbt)), set())
                if len(exact_candidates) == 1:
                    item["rawKey"] = key
                    item["key"] = next(iter(exact_candidates))
                    del item["nbt"]
                    self.replaced += 1
                    modified = True
                elif len(exact_candidates) > 1:
                    item["associationError"] = "ambiguous_exact_item_association"
                    item["displayKey"] = "unresolved-nbt:" + key
                    modified = True
                    if query_key:
                        item["displayKey"] = query_key
                        item["associationError"] = "ambiguous_exact_item_association"
                    self.diagnostics.append(
                        {
                            "file": relative_file,
                            "_recipe": recipe,
                            "recipeIndex": recipe.get("index"),
                            "slot": slot_key,
                            "rawKey": key,
                            "issue": "ambiguous_exact_item_association",
                            "candidates": sorted(exact_candidates),
                        }
                    )
                else:
                    self.missing.add((key, nbt))
                    item["associationError"] = "missing_exact_nbt_item"
                    item["displayKey"] = "unresolved-nbt:" + key
                    modified = True
                    if query_key:
                        display_candidates = bee_index.get(query_key, set())
                        if len(display_candidates) == 1:
                            item["displayKey"] = next(iter(display_candidates))
                            modified = True
                        else:
                            issue = (
                                "missing_display_item"
                                if not display_candidates
                                else "ambiguous_display_item"
                            )
                            item["displayKey"] = query_key
                            item["associationError"] = issue
                            modified = True
                            self.diagnostics.append(
                                {
                                    "file": relative_file,
                                    "_recipe": recipe,
                                    "recipeIndex": recipe.get("index"),
                                    "slot": slot_key,
                                    "rawKey": original_key,
                                    "queryKey": query_key,
                                    "issue": issue,
                                    "candidates": sorted(display_candidates),
                                }
                            )
                    elif expected_bee_type:
                        item["displayKey"] = f"unresolved-bee:{expected_bee_type}"
                        item["associationError"] = (
                            "missing_or_mismatched_identity_reexport_required"
                        )
                        modified = True
        return modified

    @property
    def ambiguous(self):
        return [
            diagnostic
            for diagnostic in self.diagnostics
            if "ambiguous" in diagnostic.get("issue", "")
        ]

    def report(self):
        remaining = self.total - self.replaced
        logger.debug(
            "[NBT 修正] NBT 物品总数 %d，已替换 %d，剩余 %d",
            self.total,
            self.replaced,
            remaining,
        )

    def write_reports(self, output_dir):
        self.report()
        os.makedirs(output_dir, exist_ok=True)

        if self.missing:
            path = os.path.join(output_dir, "nbt_missing.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["key", "nbt"])
                for key, nbt in sorted(self.missing):
                    writer.writerow([key, nbt])
            logger.warning(
                "[NBT 修正] 缺失的 (key, nbt) 已写入 %s：%d 条",
                path,
                len(self.missing),
            )

        diagnostics = []
        for source in self.diagnostics:
            row = {key: value for key, value in source.items() if key != "_recipe"}
            row["recipeIndex"] = source["_recipe"].get("index")
            diagnostics.append(row)
        if diagnostics:
            path = os.path.join(output_dir, "bee_association_diagnostics.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(diagnostics, stream, ensure_ascii=False, indent=2)
            logger.warning(
                "[蜜蜂身份] 诊断信息已写入 %s：%d 条",
                path,
                len(diagnostics),
            )

        path = os.path.join(output_dir, "association_report.json")
        report = {
            "ambiguous_nbt": self.ambiguous,
            "missing_nbt": [
                {"key": key, "nbt": nbt} for key, nbt in sorted(self.missing)
            ],
            "unresolved_wildcards": sorted(self.unresolved_wildcards),
        }
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        logger.debug("[NBT 修正] 关联报告已写入 %s", path)
