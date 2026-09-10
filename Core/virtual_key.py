"""虚拟 key：把 originalName 与 translatedName 均相同的多个 key 归一为同一身份。

当矿辞典(#)或通配 meta(:32767)展开出多个具体 key，或不同配方中出现多个 key，
只要它们对应的 originalName 与 translatedName 完全相同，就统一替换为同一个
虚拟 key，使下游以 key 为身份的等效合并、冲突检测能正确识别它们是同一物品/流体。
"""

import json

VIRTUAL_KEY_PREFIX = "@virtual:"


def virtual_key(original_name, translated_name):
    """由 (originalName, translatedName) 生成确定性的虚拟 key。"""
    payload = json.dumps(
        [original_name, translated_name],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{VIRTUAL_KEY_PREFIX}{payload}"


def is_virtual_key(key):
    """判断 key 是否为虚拟 key。"""
    return isinstance(key, str) and key.startswith(VIRTUAL_KEY_PREFIX)


def split_virtual_key(key):
    """从虚拟 key 还原 (originalName, translatedName)；非虚拟 key 返回 None。"""
    if not is_virtual_key(key):
        return None
    try:
        payload = json.loads(key[len(VIRTUAL_KEY_PREFIX):])
    except (TypeError, ValueError):
        return None
    if isinstance(payload, list) and len(payload) == 2:
        return payload[0], payload[1]
    return None


def name_pair_of(key, translations):
    """返回 key 对应的 (originalName, translatedName)；无映射时返回 None。"""
    names = translations.get(key) if translations else None
    if not names:
        return None
    original = names.get("originalName", key)
    translated = names.get("translatedName") or original
    return original, translated
