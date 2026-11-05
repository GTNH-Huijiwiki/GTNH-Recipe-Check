"""冲压机床专属规则。"""

import logging

from s0_contracts.schema import Recipe
from s3_rules.helpers import outputs_all, remove_matching_inputs

logger = logging.getLogger(__name__)

_EXTRUDER_SHAPE_PREFIX = "Extruder Shape"
_MOLD_PREFIX = "Mold ("
_EXTRUDER_SHAPE_KEY_MARKER = "ExtruderShape"
_MOLD_KEY_MARKER = "dreamcraft:Mold"
_REMOVED_INPUT_NAME = "Dough"


def _matches(item, name_prefix, key_marker):
    if item.original_name.startswith(name_prefix):
        return True
    return not item.original_name and key_marker in item.key


def _is_extruder_shape(item):
    return _matches(item, _EXTRUDER_SHAPE_PREFIX, _EXTRUDER_SHAPE_KEY_MARKER)


def _is_mold(item):
    return _matches(item, _MOLD_PREFIX, _MOLD_KEY_MARKER)


def apply(recipe: Recipe):
    """应用冲压机床规则，返回保留后的配方或 None。"""
    if outputs_all(recipe, _is_extruder_shape) or outputs_all(recipe, _is_mold):
        logger.debug(
            "冲压机床规则删除配方 %s：输出全部为模头或模具",
            recipe.recipe_id,
        )
        return None

    recipe = remove_matching_inputs(
        recipe,
        lambda item: item.original_name == _REMOVED_INPUT_NAME,
    )
    if recipe is None:
        logger.debug("冲压机床规则删除配方：输入仅包含 Dough")
        return None
    return recipe
