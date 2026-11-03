"""配方物品关联：NBT 规范化、通配展开与缺失诊断。

逻辑对应 GTNH-Chinese-Wiki-Toolchain/databaseDump/split_all.py 的
_RecipeAssociations，供 scripts/split.py 在写出 Recipes 前调用。
"""

import csv
import json
import os

try:
    from .nbt_identity import nbt_identity
except ImportError:
    from nbt_identity import nbt_identity

_ITEM_SLOTS = (
    "inputItems", "outputItems", "otherItems", "keyItem", "exampleInput",
    "inputFluids", "outputFluids",
    "normalDrops", "rareDrops", "additionalDrops", "infernalDrops",
    "eliteItems", "ultraItems", "infernoItems", "fishItems", "junkItems",
    "treasureItems",
)

_WILDCARD = ":32767"


def _wildcard_base(raw):
    """通配 key 的注册名：去掉末尾数字 meta，保留物品名本身。"""
    if raw.count(":") >= 2 and raw.rsplit(":", 1)[1].isdigit():
        return raw.rsplit(":", 1)[0]
    return raw


def iter_item_entries(value):
    """递归展开槽位中的物品对象（兼容单个对象、候选列表与嵌套列表）。"""
    if isinstance(value, list):
        for entry in value:
            yield from iter_item_entries(entry)
    elif isinstance(value, dict):
        if "key" in value:
            yield value
        else:
            for entry in value.values():
                yield from iter_item_entries(entry)


class RecipeAssociations:
    """一次建立物品索引，在写出配方前完成 NBT 与 :32767 归一化。"""

    def __init__(self, items):
        self.nbt_index = {}
        self.wildcard_index = {}
        self.wildcard_nbt_index = {}
        self.missing = set()
        self.ambiguous = []
        self.unresolved_wildcards = set()
        self.total = 0
        self.replaced = 0
        for item in items:
            raw = item.get("rawKey") or item.get("key", "")
            key = item.get("key", "")
            if not raw or not key or raw.endswith(_WILDCARD):
                continue
            base = _wildcard_base(raw)
            self.wildcard_index.setdefault(base, set()).add(key)
            nbt = item.get("nbt") or ""
            identity = nbt_identity(nbt or "{}")
            self.nbt_index.setdefault((raw, identity), set()).add(key)
            if nbt and identity != nbt_identity("{}"):
                self.wildcard_nbt_index.setdefault((base, identity), set()).add(key)

    def expand_wildcard(self, entry):
        """把 :32767 槽位展开为真实物品候选；无候选时保留并记录。"""
        if isinstance(entry, list):
            expanded = []
            for candidate in entry:
                value = self.expand_wildcard(candidate)
                expanded.extend(value if isinstance(value, list) else [value])
            return expanded

        raw = (
            entry if isinstance(entry, str)
            else entry.get("key", "") if isinstance(entry, dict)
            else ""
        )
        if not raw.endswith(_WILDCARD):
            return entry

        nbt = entry.get("nbt") if isinstance(entry, dict) else None
        base = raw[: -len(_WILDCARD)]
        identity = nbt_identity(nbt) if nbt else None
        if identity is not None and identity != nbt_identity("{}"):
            choices = sorted(self.wildcard_nbt_index.get((base, identity), ()))
        else:
            choices = sorted(self.wildcard_index.get(base, ()))
        if not choices:
            self.unresolved_wildcards.add(raw)
            unresolved = dict(entry) if isinstance(entry, dict) else {"key": raw}
            unresolved["displayKey"] = "unresolved-wildcard:" + raw
            unresolved["associationError"] = "missing_concrete_wildcard_candidates"
            return unresolved

        return [
            dict({k: v for k, v in entry.items() if k != "nbt"}, key=key, rawKey=raw)
            if isinstance(entry, dict)
            else key
            for key in choices
        ]

    def associate(self, recipe, relative_file=""):
        """展开输入通配，并把所有槽位中的 NBT 物品解析为最终 key。"""
        for slot in ("inputItems", "keyItem"):
            if slot not in recipe or recipe[slot] is None:
                continue
            value = recipe[slot]
            if isinstance(value, list):
                recipe[slot] = [self.expand_wildcard(entry) for entry in value]
            else:
                recipe[slot] = self.expand_wildcard(value)

        for slot in _ITEM_SLOTS:
            value = recipe.get(slot)
            if not isinstance(value, (list, dict)):
                continue
            for item in iter_item_entries(value):
                nbt = item.get("nbt", "") or ""
                key = item.get("key", "")
                if not nbt or not key:
                    continue

                self.total += 1
                identity = nbt_identity(nbt)
                candidates = self.nbt_index.get((key, identity), set())
                if len(candidates) == 1:
                    item["rawKey"] = key
                    item["key"] = next(iter(candidates))
                    del item["nbt"]
                    self.replaced += 1
                elif len(candidates) > 1:
                    item["associationError"] = "ambiguous_exact_item_association"
                    item["displayKey"] = "unresolved-nbt:" + key
                    self.ambiguous.append(
                        {
                            "file": relative_file,
                            "key": key,
                            "nbt": nbt,
                            "candidates": sorted(candidates),
                        }
                    )
                else:
                    item["associationError"] = "missing_exact_nbt_item"
                    item["displayKey"] = "unresolved-nbt:" + key
                    self.missing.add((key, nbt))

    def report(self):
        print(
            f"  [NBT fix] Total NBT items: {self.total}, replaced: {self.replaced}, "
            f"remaining: {self.total - self.replaced}"
        )
        print(
            f"  [NBT fix] missing pairs: {len(self.missing)}, "
            f"ambiguous pairs: {len(self.ambiguous)}, "
            f"unresolved wildcards: {len(self.unresolved_wildcards)}"
        )

    def write_reports(self, output_dir):
        self.report()
        os.makedirs(output_dir, exist_ok=True)
        if self.missing:
            path = os.path.join(output_dir, "nbt_missing.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["key", "nbt"])
                for key, nbt in sorted(self.missing):
                    writer.writerow([key, nbt])
            print(
                f"  [NBT fix] Missing (key, nbt) written to {path}: "
                f"{len(self.missing)} unique rows"
            )

        report = {
            "ambiguous_nbt": self.ambiguous,
            "missing_nbt": [
                {"key": key, "nbt": nbt} for key, nbt in sorted(self.missing)
            ],
            "unresolved_wildcards": sorted(self.unresolved_wildcards),
        }
        path = os.path.join(output_dir, "association_report.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  [NBT fix] Association report written to {path}")
