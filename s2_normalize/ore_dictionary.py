"""展开物品中的矿辞典(#)与通配meta(:32767) key 为具体物品组合。"""

import logging

from s2_normalize.virtual_key import (
    name_pair_of,
    split_virtual_key,
    virtual_key,
)

logger = logging.getLogger(__name__)

_WILDCARD_META = ":32767"

# 是否将以 :32767 结尾的通配 meta key 展开为注册表中同前缀的具体 key。
# False：保留原通配 key；True：展开（展开结果同名时折叠为虚拟 key）。
EXPAND_WILDCARD_32767 = False

# 已就“匹配过多”告警过的通配 key（每个 key 只告警一次，避免刷屏）。
_WILDCARD_WARNED = set()


_WILDCARD_LIMIT = 8


def _is_wildcard(key):
    """key 是否以 :32767（通配 meta）结尾。"""
    return key.endswith(_WILDCARD_META)


def _warn_wildcard_too_many(key, members):
    """通配 key 匹配到过多具体物品时，直接在终端抛出警告。"""
    if key in _WILDCARD_WARNED:
        return
    _WILDCARD_WARNED.add(key)
    logger.warning(
        "通配 %s 匹配到 %d 个具体物品（>%d），将全部展开；如非预期请检查物品注册表。",
        key,
        len(members),
        _WILDCARD_LIMIT,
    )


def _wildcard_members(key, item_keys):
    """把通配 key 展开为物品注册表中同前缀的全部 key（由 EXPAND_WILDCARD_32767 控制）。

    约定 meta 0 为裸 key、其余为 :<meta>（如 minecraft:log / minecraft:log:1）。
    故 "minecraft:log:32767" 匹配 "minecraft:log" 及 "minecraft:log:*"。
    开关关闭、item_keys 为空/为 None 或找不到匹配时，均保留原通配 key。
    匹配数超过 _WILDCARD_LIMIT 时向终端告警。
    """
    if not EXPAND_WILDCARD_32767:
        return [key]
    base = key[: -len(_WILDCARD_META)]
    prefix = base + ":"
    members = (
        [k for k in item_keys if k == base or k.startswith(prefix)]
        if item_keys
        else []
    )
    if len(members) > _WILDCARD_LIMIT:
        _warn_wildcard_too_many(key, members)
    return members or [key]


def _maybe_virtualize(choices, translations):
    """若所有候选 key 的 (originalName, translatedName) 一致，折叠为单个虚拟 key。

    有任意候选缺少翻译映射时无法判定，保守地保留原候选列表。
    """
    if not translations or len(choices) <= 1:
        return choices

    name_groups = {}
    for choice in choices:
        pair = name_pair_of(choice, translations)
        if pair is None:
            return choices
        name_groups.setdefault(pair, []).append(choice)

    if len(name_groups) == 1:
        pair = next(iter(name_groups))
        return [virtual_key(*pair)]
    return choices


def _resolve_key(key, ore_dictionary, item_keys, missing_ore_keys, translations=None):
    """把一个 key 解析为候选具体 key 列表。

    先做 # 矿辞典查表（无成员则保留占位并记录），再对每个候选做 :32767 通配展开
    （受 EXPAND_WILDCARD_32767 控制），最后去重保序。
    若展开出的多个 key 对应完全相同的 (originalName, translatedName)，则用单个
    虚拟 key 替代（展开退化为 key 替换），避免产生无意义的多份等价配方。
    """
    if key.startswith("#"):
        ore_name = key[1:]
        choices = list(ore_dictionary.get(ore_name, []))
        if not choices:
            missing_ore_keys.add(key)
            choices = [key]
    else:
        choices = [key]

    resolved = []
    for choice in choices:
        if _is_wildcard(choice):
            resolved.extend(_wildcard_members(choice, item_keys))
        else:
            resolved.append(choice)

    seen = set()
    unique = []
    for k in resolved:
        if k not in seen:
            seen.add(k)
            unique.append(k)

    return _maybe_virtualize(unique, translations)


def expand_ore_catalysts(catalysts, ore_dictionary, item_keys=None, translations=None):
    """将一组物品中的 # 矿辞典与 :32767 通配 key 展开为具体物品组合。

    item_keys：物品注册表全部 key 的集合，用于 :32767 通配的前缀匹配；
    不提供时通配 key 原样保留。催化剂与普通输入共用此方法，由调用方决定
    展开结果是并入催化组还是拆成不同配方。
    translations：key -> {originalName, translatedName} 映射，用于在展开结果
    的多个 key 名称完全相同时折叠为虚拟 key；不提供时不做折叠。
    """
    alternatives = [[]]
    missing_ore_keys = set()

    for catalyst in catalysts:
        key = catalyst.get("key", "") if isinstance(catalyst, dict) else ""
        choices = _resolve_key(
            key, ore_dictionary, item_keys, missing_ore_keys, translations
        )

        next_alternatives = []
        for current in alternatives:
            for choice in choices:
                replacement = dict(catalyst)
                replacement["key"] = choice
                names = split_virtual_key(choice)
                if names:
                    # 虚拟 key 不在翻译表中，这里把名字一并携带，供翻译阶段识别。
                    replacement["originalName"] = names[0]
                    replacement["translatedName"] = names[1]
                next_alternatives.append(current + [replacement])
        alternatives = next_alternatives

    if missing_ore_keys:
        logger.warning(
            "矿物辞典展开时有 %d 个 key 未找到对应成员: %s",
            len(missing_ore_keys),
            ", ".join(sorted(missing_ore_keys)),
        )

    return alternatives

