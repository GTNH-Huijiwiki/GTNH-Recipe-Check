"""规范化阶段的配方整理与等效输入分组。

读取预处理后的 Data，生成包含 InputSlot/InputGroup 和具体
CatalystSet/CatalystGroup 的规范配方。
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict, deque
from copy import deepcopy
from itertools import product

from s0_contracts.catalyst_group import normalize_catalyst_groups
from s2_normalize.translator import translate_recipes

logger = logging.getLogger(__name__)

_WILDCARD_META = ":32767"
_CIRCUIT_RE = re.compile(r"(?:circuit|integrated_circuit)", re.IGNORECASE)

ALERT_MIXED_KIND = "MIXED_ITEM_FLUID_GROUP"
ALERT_MIXED_NC = "MIXED_NC_ALTERNATIVES"
ALERT_UNRESOLVED = "UNRESOLVED_PLACEHOLDER"
ALERT_FLUID_CATALYST = "FLUID_CATALYST"
ALERT_CROSS_KIND_MERGE = "CROSS_KIND_MERGE"
ALERT_INVALID_ITEM = "INVALID_ITEM"
ALERT_INVALID_FLUID = "INVALID_FLUID"
ALERT_MULTIPLE_CIRCUIT = "MULTIPLE_CIRCUIT"


# ---------------------------------------------------------------------------
# 基础辅助函数
# ---------------------------------------------------------------------------

def _amount(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number) if number.is_integer() else number
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return int(number) if number.is_integer() else number


def _sort_members(members):
    return sorted(members, key=lambda item: (item.get("key", ""), item.get("amount", 0)))


def _dedupe_members(members):
    seen = set()
    unique = []
    for member in _sort_members(members):
        signature = (member.get("key", ""), member.get("amount", 0))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(member)
    return unique


def _normalize_item(value, default_amount=1):
    if isinstance(value, str):
        return {"key": value, "amount": default_amount}
    if isinstance(value, dict):
        key = value.get("key")
        if not isinstance(key, str) or not key:
            return None
        return {"key": key, "amount": _amount(value.get("amount"), default_amount)}
    return None


def _normalize_fluid(value, default_amount=0):
    if isinstance(value, str):
        return {"key": value, "amount": default_amount}
    if isinstance(value, dict):
        key = value.get("key")
        if not isinstance(key, str) or not key:
            return None
        return {"key": key, "amount": _amount(value.get("amount"), default_amount)}
    return None


def _flatten_entries(value):
    if isinstance(value, list):
        flattened = []
        for entry in value:
            flattened.extend(_flatten_entries(entry))
        return flattened
    return [value]


def _is_circuit(key):
    return bool(_CIRCUIT_RE.search(key))


def _add_alert(recipe, code, message, **details):
    alerts = recipe.setdefault("alerts", [])
    for alert in alerts:
        if alert.get("code") == code and alert.get("message") == message:
            return
    entry = {"code": code, "message": message}
    entry.update(details)
    alerts.append(entry)
    recipe["needsReview"] = True
    suffix = " ".join(f"{key}={value!r}" for key, value in details.items())
    logger.warning("[%s] %s %s", code, message, details)


def _merge_alerts(target_recipe, source_recipe):
    alerts = target_recipe.setdefault("alerts", [])
    for alert in source_recipe.get("alerts", []):
        code = alert.get("code")
        message = alert.get("message")
        if any(
            existing.get("code") == code and existing.get("message") == message
            for existing in alerts
        ):
            continue
        alerts.append(deepcopy(alert))
    if alerts:
        target_recipe["needsReview"] = True


# ---------------------------------------------------------------------------
# 槽位签名与并集
# ---------------------------------------------------------------------------

def _catalyst_slot_signature(slot):
    if "item" in slot:
        item = slot["item"]
        return ("item", item.get("key", ""), item.get("amount", 0))
    members = _dedupe_members(slot.get("choices", []))
    return ("choices", tuple((m.get("key", ""), m.get("amount", 0)) for m in members))


def _catalyst_group_signature(group):
    slots = tuple(sorted(_catalyst_slot_signature(slot) for slot in group.get("catalysts", [])))
    circuit = group.get("circuit")
    circuit_signature = (
        (circuit.get("key", ""), circuit.get("amount", 0))
        if isinstance(circuit, dict)
        else ("", 0)
    )
    return (slots, circuit_signature)

def _catalyst_groups_signature(groups):
    return tuple(sorted(_catalyst_group_signature(group) for group in groups))


def _slot_signature(slot):
    kind = slot.get("kind")
    if kind in ("item", "fluid"):
        members = _dedupe_members(slot.get("members", []))
        return (kind, tuple((m.get("key", ""), m.get("amount", 0)) for m in members))
    if kind == "catalyst":
        return ("catalyst", _catalyst_groups_signature(slot.get("groups", [])))
    return ("invalid", repr(slot))


def _slot_union(first, second):
    if first.get("kind") != second.get("kind"):
        return None
    kind = first.get("kind")
    if kind in ("item", "fluid"):
        members = _dedupe_members(
            list(first.get("members", [])) + list(second.get("members", []))
        )
        return {"kind": kind, "members": members}
    if kind == "catalyst":
        groups = []
        seen = set()
        for group in list(first.get("groups", [])) + list(second.get("groups", [])):
            signature = _catalyst_group_signature(group)
            if signature in seen:
                continue
            seen.add(signature)
            groups.append(deepcopy(group))
        return {"kind": "catalyst", "groups": groups}
    return None


# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------

def _normalize_nc_group(items, alert_recipe, circuit, catalyst_slots):
    """把一组 nc 候选项整理为电路和催化剂候选。"""
    circuits = []
    others = []
    for item in items:
        if _is_circuit(item["key"]):
            circuits.append(item)
        else:
            others.append(item)
    if circuits:
        if circuit is not None:
            _add_alert(alert_recipe, ALERT_MULTIPLE_CIRCUIT, "multiple circuit items")
        else:
            circuit = circuits[0]
        if len(circuits) > 1:
            _add_alert(
                alert_recipe,
                ALERT_MULTIPLE_CIRCUIT,
                "multiple circuit alternatives",
                keys=[item["key"] for item in circuits],
            )
        if others:
            _add_alert(
                alert_recipe,
                ALERT_MIXED_NC,
                "circuit and non-circuit alternatives in one nc group",
                keys=[item["key"] for item in circuits + others],
            )
    if others:
        catalyst_slots.append({"choices": _dedupe_members(others)})
    return circuit


def _normalize_input_slots(raw, alert_recipe):
    slots = []
    catalyst_slots = []
    circuit = None

    for entry in raw.get("inputItems", []) or []:
        parsed = []
        for candidate in _flatten_entries(entry):
            if candidate is None:
                continue
            nc = isinstance(candidate, dict) and bool(candidate.get("nc"))
            item = _normalize_item(candidate, 1)
            if item is None:
                _add_alert(alert_recipe, ALERT_INVALID_ITEM, "invalid input item", value=repr(candidate))
                continue
            parsed.append((item, nc))
        if not parsed:
            continue

        if len(parsed) == 1:
            item, nc = parsed[0]
            if nc:
                if _is_circuit(item["key"]):
                    if circuit is not None:
                        _add_alert(alert_recipe, ALERT_MULTIPLE_CIRCUIT, "multiple circuit items")
                    else:
                        circuit = item
                else:
                    catalyst_slots.append({"item": item})
            else:
                slots.append({"kind": "item", "members": [item]})
            continue

        nc_count = sum(1 for _, nc in parsed if nc)
        if nc_count == 0:
            slots.append(
                {"kind": "item", "members": _dedupe_members([item for item, _ in parsed])}
            )
        elif nc_count == len(parsed):
            circuit = _normalize_nc_group(
                [item for item, _ in parsed], alert_recipe, circuit, catalyst_slots
            )
        else:
            _add_alert(
                alert_recipe,
                ALERT_MIXED_NC,
                "mixed nc and non-nc alternatives in one input slot",
                keys=[item["key"] for item, _ in parsed],
            )
            ordinary = [item for item, nc in parsed if not nc]
            nc_items = [item for item, nc in parsed if nc]
            if ordinary:
                slots.append({"kind": "item", "members": _dedupe_members(ordinary)})
            if nc_items:
                circuit = _normalize_nc_group(nc_items, alert_recipe, circuit, catalyst_slots)

    for entry in raw.get("inputFluids", []) or []:
        parsed = []
        for candidate in _flatten_entries(entry):
            if candidate is None:
                continue
            fluid = _normalize_fluid(candidate, 0)
            if fluid is None:
                _add_alert(alert_recipe, ALERT_INVALID_FLUID, "invalid input fluid", value=repr(candidate))
                continue
            if isinstance(candidate, dict) and candidate.get("nc"):
                _add_alert(
                    alert_recipe,
                    ALERT_FLUID_CATALYST,
                    "fluid nc input is not supported as catalyst",
                    key=fluid["key"],
                )
            parsed.append(fluid)
        if len(parsed) == 1:
            slots.append({"kind": "fluid", "members": [parsed[0]]})
        elif parsed:
            slots.append({"kind": "fluid", "members": _dedupe_members(parsed)})

    if catalyst_slots or circuit is not None:
        slots.append(
            {
                "kind": "catalyst",
                "groups": [{"catalysts": catalyst_slots, "circuit": circuit}],
            }
        )
    return slots


def _output_variants(raw, alert_recipe):
    item_options = []
    for entry in raw.get("outputItems", []) or []:
        options = []
        for candidate in _flatten_entries(entry):
            if candidate is None:
                continue
            item = _normalize_item(candidate, 1)
            if item is None:
                _add_alert(alert_recipe, ALERT_INVALID_ITEM, "invalid output item", value=repr(candidate))
                continue
            options.append(item)
        if options:
            item_options.append(options)

    fluid_options = []
    for entry in raw.get("outputFluids", []) or []:
        options = []
        for candidate in _flatten_entries(entry):
            if candidate is None:
                continue
            fluid = _normalize_fluid(candidate, 0)
            if fluid is None:
                _add_alert(alert_recipe, ALERT_INVALID_FLUID, "invalid output fluid", value=repr(candidate))
                continue
            options.append(fluid)
        if options:
            fluid_options.append(options)

    item_combos = product(*item_options) if item_options else [()]
    fluid_combos = product(*fluid_options) if fluid_options else [()]
    return [
        (list(item_combo), list(fluid_combo))
        for item_combo in item_combos
        for fluid_combo in fluid_combos
    ]


# ---------------------------------------------------------------------------
# 展开
# ---------------------------------------------------------------------------

def build_item_base_index(item_keys):
    """为 :32767 展开建立 ``基础 key -> 具体 key`` 索引。"""
    index = defaultdict(set)
    for key in item_keys:
        if not isinstance(key, str) or not key or key.endswith(_WILDCARD_META):
            continue
        parts = key.rsplit(":", 1)
        if key.count(":") >= 2 and parts[1].isdigit():
            base = parts[0]
        else:
            base = key
        index[base].add(key)
    return {base: sorted(keys) for base, keys in index.items()}


def _expand_key(key, kind, ore_dictionary, item_keys, item_base_index, alert_recipe, visited):
    if key in visited:
        _add_alert(alert_recipe, ALERT_UNRESOLVED, "cyclic ore dictionary reference", key=key)
        return [key]

    if key.startswith("#"):
        ore_name = key[1:]
        members = ore_dictionary.get(ore_name)
        if not members:
            _add_alert(
                alert_recipe,
                ALERT_UNRESOLVED,
                "ore dictionary key has no members",
                key=key,
            )
            return [key]
        expanded = []
        for member in members:
            expanded.extend(
                _expand_key(
                    member,
                    kind,
                    ore_dictionary,
                    item_keys,
                    item_base_index,
                    alert_recipe,
                    visited | {key},
                )
            )
        return expanded

    if key.endswith(_WILDCARD_META):
        base = key[: -len(_WILDCARD_META)]
        candidates = list(item_base_index.get(base, ()))
        if not candidates:
            candidates = [
                candidate
                for candidate in item_keys
                if candidate == base or candidate.startswith(base + ":")
            ]
        if not candidates:
            _add_alert(
                alert_recipe,
                ALERT_UNRESOLVED,
                "wildcard meta has no concrete candidates",
                key=key,
            )
            return [key]
        return sorted(set(candidates))

    if kind == "item" and key.startswith("fluid."):
        _add_alert(
            alert_recipe,
            ALERT_MIXED_KIND,
            "fluid key expanded inside an item group",
            key=key,
        )
        return []
    if kind == "fluid" and not key.startswith("fluid."):
        _add_alert(
            alert_recipe,
            ALERT_MIXED_KIND,
            "item key expanded inside a fluid group",
            key=key,
        )
        return []
    return [key]


def _expand_members(members, kind, ore_dictionary, item_keys, item_base_index, alert_recipe):
    expanded = []
    for member in members:
        for key in _expand_key(
            member["key"],
            kind,
            ore_dictionary,
            item_keys,
            item_base_index,
            alert_recipe,
            frozenset(),
        ):
            expanded.append({"key": key, "amount": member["amount"]})
    return _dedupe_members(expanded)


def _expand_slot(slot, ore_dictionary, item_keys, item_base_index, alert_recipe):
    kind = slot.get("kind")
    if kind in ("item", "fluid"):
        members = _expand_members(
            slot.get("members", []),
            kind,
            ore_dictionary,
            item_keys,
            item_base_index,
            alert_recipe,
        )
        if not members:
            _add_alert(
                alert_recipe,
                ALERT_UNRESOLVED,
                "input group became empty after expansion",
                kind=kind,
            )
            return {"kind": kind, "members": _dedupe_members(slot.get("members", []))}
        return {"kind": kind, "members": members}

    if kind == "catalyst":
        groups = []
        seen = set()
        for group in slot.get("groups", []):
            new_slots = []
            for catalyst_slot in group.get("catalysts", []):
                if "item" in catalyst_slot:
                    item = catalyst_slot["item"]
                    members = _expand_members(
                        [item],
                        "item",
                        ore_dictionary,
                        item_keys,
                        item_base_index,
                        alert_recipe,
                    )
                    if len(members) == 1:
                        new_slots.append({"item": members[0]})
                    else:
                        new_slots.append({"choices": members})
                else:
                    members = _expand_members(
                        catalyst_slot.get("choices", []),
                        "item",
                        ore_dictionary,
                        item_keys,
                        item_base_index,
                        alert_recipe,
                    )
                    if len(members) == 1:
                        new_slots.append({"item": members[0]})
                    else:
                        new_slots.append({"choices": members})
            new_group = {
                "catalysts": new_slots,
                "circuit": deepcopy(group.get("circuit")),
            }
            signature = _catalyst_group_signature(new_group)
            if signature not in seen:
                seen.add(signature)
                groups.append(new_group)
        return {"kind": "catalyst", "groups": groups}

    return slot


def normalize_recipe(raw, ore_dictionary, item_keys, item_base_index):
    """把一条原始配方整理为一条或多条分组配方。"""
    input_alert_recipe = {"alerts": [], "needsReview": False}
    slots = _normalize_input_slots(raw, input_alert_recipe)
    expanded_slots = [
        _expand_slot(slot, ore_dictionary, item_keys, item_base_index, input_alert_recipe)
        for slot in slots
    ]

    output_alert_recipe = {"alerts": [], "needsReview": False}
    variants = _output_variants(raw, output_alert_recipe)

    recipes = []
    for output_items, output_fluids in variants:
        recipe = {
            "outputItems": output_items,
            "outputFluids": output_fluids,
            "_slots": deepcopy(expanded_slots),
            "alerts": deepcopy(input_alert_recipe.get("alerts", [])) + deepcopy(
                output_alert_recipe.get("alerts", [])
            ),
        }
        recipe["needsReview"] = bool(recipe["alerts"])
        recipes.append(recipe)
    return recipes


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def _output_signature(recipe):
    values = [
        ("item", item.get("key", ""), item.get("amount", 0))
        for item in recipe.get("outputItems", [])
    ]
    values.extend(
        ("fluid", fluid.get("key", ""), fluid.get("amount", 0))
        for fluid in recipe.get("outputFluids", [])
    )
    return tuple(sorted(values))


def _core_entries(recipe):
    output_signature = _output_signature(recipe)
    signatures = [_slot_signature(slot) for slot in recipe.get("_slots", [])]
    seen = set()
    for index, signature in enumerate(signatures):
        core = tuple(sorted(signatures[:index] + signatures[index + 1 :]))
        key = (output_signature, core)
        if key in seen:
            continue
        seen.add(key)
        yield key, signature, index


def _merge_recipes(recipes):
    active = {index: recipe for index, recipe in enumerate(recipes)}
    core_index = defaultdict(list)
    pending = deque()
    pending_keys = set()
    next_id = [len(recipes)]

    def add_to_index(recipe_id, recipe):
        for key, signature, index in _core_entries(recipe):
            core_index[key].append((recipe_id, signature, index))
            if key not in pending_keys:
                pending.append(key)
                pending_keys.add(key)

    for recipe_id, recipe in active.items():
        recipe["_id"] = recipe_id
        add_to_index(recipe_id, recipe)

    while pending:
        key = pending.popleft()
        pending_keys.discard(key)
        entries = [entry for entry in core_index.get(key, []) if entry[0] in active]
        if len(entries) < 2:
            continue

        by_kind = defaultdict(list)
        for entry in entries:
            by_kind[entry[1][0]].append(entry)

        for kind in sorted(by_kind):
            kind_entries = [entry for entry in by_kind[kind] if entry[0] in active]
            unique = {}
            for entry in kind_entries:
                unique.setdefault(entry[1], entry)
            if len(unique) < 2:
                continue

            source_entries = list(unique.values())
            template_id, _, template_slot_index = source_entries[0]
            template = active[template_id]
            core_slots = [
                deepcopy(slot)
                for index, slot in enumerate(template["_slots"])
                if index != template_slot_index
            ]

            union_slot = None
            for recipe_id, _, slot_index in source_entries:
                source_recipe = active.get(recipe_id)
                if source_recipe is None:
                    continue
                slot = source_recipe["_slots"][slot_index]
                if union_slot is None:
                    union_slot = deepcopy(slot)
                else:
                    combined = _slot_union(union_slot, slot)
                    if combined is None:
                        _add_alert(
                            template,
                            ALERT_CROSS_KIND_MERGE,
                            "attempted cross-kind input group merge",
                            first=repr(_slot_signature(union_slot)),
                            second=repr(_slot_signature(slot)),
                        )
                    else:
                        union_slot = combined
            if union_slot is None:
                continue

            alerts = []
            for recipe_id in [entry[0] for entry in kind_entries]:
                source_recipe = active.get(recipe_id)
                if source_recipe is None:
                    continue
                for alert in source_recipe.get("alerts", []):
                    if alert not in alerts:
                        alerts.append(deepcopy(alert))

            new_recipe = deepcopy(template)
            new_recipe["_slots"] = core_slots + [union_slot]
            new_recipe.pop("_id", None)
            new_recipe["alerts"] = alerts
            new_recipe["needsReview"] = bool(alerts)

            for entry in kind_entries:
                active.pop(entry[0], None)

            new_id = next_id[0]
            next_id[0] += 1
            new_recipe["_id"] = new_id
            active[new_id] = new_recipe
            add_to_index(new_id, new_recipe)

    # 完全重复（槽位无差异）的项任取一项即可，只保留一个。
    deduped = {}
    for recipe_id in sorted(active):
        recipe = active[recipe_id]
        signature = (
            _output_signature(recipe),
            tuple(sorted(_slot_signature(slot) for slot in recipe.get("_slots", []))),
        )
        if signature in deduped:
            _merge_alerts(deduped[signature], recipe)
            continue
        deduped[signature] = recipe
    return list(deduped.values())


# ---------------------------------------------------------------------------
# 孤儿标记与收尾
# ---------------------------------------------------------------------------

def _mark_orphans(recipes, mode):
    presence = defaultdict(set)
    for index, recipe in enumerate(recipes):
        for slot in recipe.get("_slots", []):
            if slot.get("kind") == "catalyst":
                continue
            kind = slot.get("kind")
            for member in _dedupe_members(slot.get("members", [])):
                presence[(kind, member.get("key", ""))].add(index)

    for index, recipe in enumerate(recipes):
        orphan_slots = []
        orphan_items = set()
        orphan_fluids = set()
        for slot in recipe.get("_slots", []):
            if slot.get("kind") == "catalyst":
                continue
            members = _dedupe_members(slot.get("members", []))
            if not members:
                continue
            slot_orphan = all(
                len(presence[(slot.get("kind"), member.get("key", ""))]) == 1
                for member in members
            )
            orphan_slots.append(slot_orphan)
            if slot_orphan:
                target = orphan_items if slot.get("kind") == "item" else orphan_fluids
                target.update(member.get("key", "") for member in members)

        has_orphan = any(orphan_slots)
        only_orphan = bool(orphan_slots) and all(orphan_slots)
        recipe["orphan"] = {
            "hasOrphanInput": has_orphan,
            "onlyOrphanInputs": only_orphan,
            "orphanItems": sorted(orphan_items),
            "orphanFluids": sorted(orphan_fluids),
            "mode": mode,
        }


def _slots_to_fields(recipe):
    input_items = []
    input_fluids = []
    input_groups = []
    catalyst_groups = []

    for slot in recipe.get("_slots", []):
        kind = slot.get("kind")
        if kind in ("item", "fluid"):
            members = _dedupe_members(slot.get("members", []))
            if not members:
                continue
            if len(members) == 1:
                (input_items if kind == "item" else input_fluids).append(members[0])
            else:
                input_groups.append({"kind": kind, "members": members})
        elif kind == "catalyst":
            catalyst_groups.extend(deepcopy(slot.get("groups", [])))

    deduped_groups = []
    seen = set()
    for group in catalyst_groups:
        signature = _catalyst_group_signature(group)
        if signature in seen:
            continue
        seen.add(signature)
        deduped_groups.append(group)

    output_catalyst_groups = []
    for group in sorted(deduped_groups, key=_catalyst_group_signature):
        fixed = []
        choices = []
        for slot in group.get("catalysts", []):
            if "item" in slot:
                fixed.append(deepcopy(slot["item"]))
                continue
            members = _dedupe_members(slot.get("choices", []))
            if members:
                choices.append({"members": members})
        output_group = {
            "catalysts": fixed,
            "circuit": deepcopy(group.get("circuit")),
        }
        if choices:
            output_group["catalystChoices"] = choices
        output_catalyst_groups.append(output_group)

    ordered = {
        "inputItems": sorted(
            input_items, key=lambda item: (item.get("key", ""), item.get("amount", 0))
        ),
        "inputFluids": sorted(
            input_fluids, key=lambda fluid: (fluid.get("key", ""), fluid.get("amount", 0))
        ),
        "outputItems": recipe.get("outputItems", []),
        "outputFluids": recipe.get("outputFluids", []),
        "catalystGroups": normalize_catalyst_groups(output_catalyst_groups),
        "inputGroups": sorted(
            input_groups,
            key=lambda group: (
                group.get("kind", ""),
                tuple(
                    (m.get("key", ""), m.get("amount", 0))
                    for m in group.get("members", [])
                ),
            ),
        ),
        "alerts": recipe.get("alerts", []),
        "needsReview": bool(recipe.get("alerts")),
        "orphan": recipe.get("orphan", {}),
    }
    recipe.clear()
    recipe.update(ordered)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def group_recipes(
    raw_recipes,
    ore_dictionary,
    translations,
    item_keys,
    machine_name="",
    orphan_mode="contains",
):
    """返回翻译后的分组配方与统计报告。"""
    item_base_index = build_item_base_index(item_keys)
    normalized = []
    for raw in raw_recipes or []:
        if not isinstance(raw, dict):
            continue
        normalized.extend(
            normalize_recipe(raw, ore_dictionary, item_keys, item_base_index)
        )

    merged = _merge_recipes(normalized)
    _mark_orphans(merged, orphan_mode)

    for recipe in merged:
        _slots_to_fields(recipe)

    # 管理字段不参与递归物料翻译。
    admin_fields = ("alerts", "orphan")
    preserved = [
        {field: recipe.pop(field, None) for field in admin_fields}
        for recipe in merged
    ]
    translated = translate_recipes(merged, translations)
    for recipe, saved in zip(translated, preserved):
        for field, value in saved.items():
            if value is not None:
                recipe[field] = value

    alerts = Counter()
    orphan_has = 0
    orphan_only = 0
    for recipe in translated:
        for alert in recipe.get("alerts", []):
            alerts[alert.get("code", "UNKNOWN")] += 1
        orphan = recipe.get("orphan", {})
        if orphan.get("hasOrphanInput"):
            orphan_has += 1
        if orphan.get("onlyOrphanInputs"):
            orphan_only += 1

    report = {
        "machine": machine_name,
        "inputRecipes": len(raw_recipes or []),
        "normalizedRecipes": len(normalized),
        "outputRecipes": len(translated),
        "alerts": dict(alerts),
        "orphanHasInput": orphan_has,
        "orphanOnlyInputs": orphan_only,
    }
    return translated, report
