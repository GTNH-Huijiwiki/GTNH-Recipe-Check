"""工业高炉专属规则。"""

import logging

from s0_contracts.schema import Recipe

logger = logging.getLogger(__name__)

_HAFNIUM_IODIDE_DUST_NAME = "Hafnium Iodide Dust"


def _is_hafnium_iodide_dust(item):
    return item.original_name == _HAFNIUM_IODIDE_DUST_NAME


def _has_only_hafnium_iodide_dust_inputs(recipe):
    slots = recipe.input_group.slots
    return bool(slots) and all(
        slot.alternatives
        and all(
            _is_hafnium_iodide_dust(item)
            for item in slot.alternatives
        )
        for slot in slots
    )


def apply(recipe: Recipe):
    """应用工业高炉规则，返回保留后的配方或 None。"""
    if _has_only_hafnium_iodide_dust_inputs(recipe):
        logger.debug(
            "工业高炉规则删除配方 %s：输入仅为碘化铪粉",
            recipe.recipe_id,
        )
        return None
    return recipe