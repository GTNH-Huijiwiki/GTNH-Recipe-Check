"""冲压机床（Forming Press）专属删减规则。

本规则运行在 2「删减配方」阶段（清洗/翻译之后、催化组删减之前）。

删除逻辑：
  输出物品非空、且**全部**为模头（Extruder Shape，形如 `Extruder Shape (Plate)`）
  或**全部**为模具（Mold，形如 `Mold (Plate)`）的配方。这类配方是
  「空模板 + 模头/模具 → 模头/模具」的复制型配方（如
  `Empty Shape Plate + Mold (Plate) → Mold (Plate)`），只是把模头/模具本身复制
  一份以便继续在冲压机等处加工，不代表该机器真正的加工内容，故整条删除。

匹配以 originalName 前缀为准（`Extruder Shape` / `Mold (`）；若物品没有
originalName（未经翻译），退回按 key 判断（含 `ExtruderShape` / `dreamcraft:Mold`）。

另：输入物品中存在 originalName **严格等于** `Dough`（面团）的配方也整条删除。
"""

# 输出物品 originalName 前缀
_EXTRUDER_SHAPE_PREFIX = "Extruder Shape"
_MOLD_PREFIX = "Mold ("
# key 兜底匹配标记（仅在没有 originalName 时使用）
_EXTRUDER_SHAPE_KEY_MARKER = "ExtruderShape"
_MOLD_KEY_MARKER = "dreamcraft:Mold"


def _matches(item, name_prefix, key_marker):
    """物品是否命中：originalName 前缀，或（无翻译时）key 标记。"""
    if isinstance(item, dict):
        if item.get("originalName", "").startswith(name_prefix):
            return True
        return key_marker in item.get("key", "")
    text = str(item)
    return text.startswith(name_prefix) or key_marker in text


def _is_extruder_shape(item):
    """物品是否为 Extruder Shape（模头）。"""
    return _matches(item, _EXTRUDER_SHAPE_PREFIX, _EXTRUDER_SHAPE_KEY_MARKER)


def _is_mold(item):
    """物品是否为 Mold（模具）。"""
    return _matches(item, _MOLD_PREFIX, _MOLD_KEY_MARKER)


def _outputs_all(recipe, predicate):
    """配方输出非空、且全部满足 predicate。"""
    output_items = recipe.get("outputItems", [])
    return bool(output_items) and all(predicate(item) for item in output_items)


# 输入物品 originalName：严格等于该值时删除整条配方
_REMOVED_INPUT_NAME = "Dough"


def _has_input_named(recipe, name):
    """配方普通输入（inputItems，不含催化剂）中是否存在 originalName 严格等于 name 的物品。"""
    for item in recipe.get("inputItems", []):
        if isinstance(item, dict):
            if item.get("originalName") == name:
                return True
        elif str(item) == name:
            return True
    return False


def clean(recipes):
    """删除下列配方：
      - 输出物品全部为 Extruder Shape（模头）或全部为 Mold（模具）的复制型配方；
      - 普通输入中存在 originalName 严格为 `Dough`（面团）的物品的配方。

    输入：清洗并翻译后的配方列表。
    输出：删减后的配方列表。
    """
    filtered = []
    for recipe in recipes:
        if _outputs_all(recipe, _is_extruder_shape) or _outputs_all(recipe, _is_mold):
            continue
        if _has_input_named(recipe, _REMOVED_INPUT_NAME):
            continue
        filtered.append(recipe)
    return filtered
