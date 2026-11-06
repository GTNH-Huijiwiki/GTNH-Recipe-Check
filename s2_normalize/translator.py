"""将配方中的 key 字段翻译为 originalName 和 translatedName。

翻译后对象保留 key 作为唯一身份键，供后续所有内部判决使用；
originalName/translatedName 分别用于规则删减与展示输出。
"""

import logging

from s2_normalize.virtual_key import is_virtual_key

logger = logging.getLogger(__name__)


def _translate_value(value, translations, missing_keys):
    """递归翻译任意结构中的物品/流体对象。"""
    if isinstance(value, list):
        return [_translate_value(item, translations, missing_keys) for item in value]
    if not isinstance(value, dict):
        return value

    if "key" in value:
        key = value["key"]
        if is_virtual_key(key):
            # 虚拟 key 不在翻译表中，名称已在展开阶段携带，原样保留。
            names = {
                "originalName": value.get("originalName", key),
                "translatedName": value.get("translatedName", key),
            }
        else:
            names = translations.get(key)
            if names is None:
                missing_keys.add(key)
                names = {"originalName": key, "translatedName": key}

        translated = {
            "key": key,
            "originalName": names["originalName"],
            "translatedName": names["translatedName"],
        }
        for field, field_value in value.items():
            if field != "key":
                translated[field] = _translate_value(
                    field_value, translations, missing_keys
                )
        return translated

    # 没有 key 的对象递归处理其值。
    return {
        field: _translate_value(field_value, translations, missing_keys)
        for field, field_value in value.items()
    }


def translate_recipes(recipes, translations):
    """翻译配方列表，并记录映射失败的 key。"""
    missing_keys = set()
    translated = _translate_value(recipes, translations, missing_keys)

    if missing_keys:
        logger.warning(
            "翻译过程中有 %d 个 key 未能映射到物品/流体名称: %s",
            len(missing_keys),
            ", ".join(sorted(missing_keys)),
        )

    return translated
