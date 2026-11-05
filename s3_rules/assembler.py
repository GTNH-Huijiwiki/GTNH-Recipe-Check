"""组装机专属规则。"""

import logging

from s0_contracts.schema import Recipe
from s3_rules.helpers import outputs_all, remove_variants_where_all_inputs_match

logger = logging.getLogger(__name__)

_TROPHY_MARKER = "trophy"
_CIRCUIT_24_KEY = "gregtech:gt.integrated_circuit:24"
_MULTI_SUPERCONDUCTOR_PREFIXES = (
    "2x Superconductor ",
    "4x Superconductor ",
    "8x Superconductor ",
    "12x Superconductor ",
    "16x Superconductor ",
)


def _is_trophy_catalyst(item):
    return _TROPHY_MARKER in item.original_name.casefold()


def _is_trophy_circuit_set(catalyst_set):
    return (
        len(catalyst_set.catalysts) == 1
        and catalyst_set.circuit is not None
        and _is_trophy_catalyst(catalyst_set.catalysts[0])
    )


def _uses_circuit_24(catalyst_set):
    if catalyst_set.circuit and catalyst_set.circuit.key == _CIRCUIT_24_KEY:
        return True
    return any(item.key == _CIRCUIT_24_KEY for item in catalyst_set.catalysts)


def _is_cable(item):
    return "Cable" in item.original_name


def _is_multi_superconductor_wire(item):
    return item.original_name.startswith(
        _MULTI_SUPERCONDUCTOR_PREFIXES
    ) and item.original_name.endswith(" Wire")


def apply(recipe: Recipe):
    """应用组装机规则，返回保留后的配方或 None。"""
    recipe_id = recipe.recipe_id
    if any(_is_multi_superconductor_wire(item) for item in recipe.output_group.items):
        logger.debug(
            "组装机规则删除配方 %s：输出包含多重超导导线",
            recipe_id,
        )
        return None

    if any(_is_trophy_circuit_set(item) for item in recipe.catalyst_group.sets):
        logger.debug(
            "组装机规则删除配方 %s：存在战利品与编程电路组合",
            recipe.recipe_id,
        )
        return None

    recipe = remove_variants_where_all_inputs_match(
        recipe,
        lambda item: item.original_name == "Volumetric Flask",
    )
    if recipe is None:
        logger.debug(
            "组装机规则删除配方 %s：输入仅为容量瓶",
            recipe_id,
        )
        return None

    if outputs_all(recipe, _is_cable) and any(
        _uses_circuit_24(item) for item in recipe.catalyst_group.sets
    ):
        logger.debug(
            "组装机规则删除配方 %s：编程电路24且输出全部为线缆",
            recipe.recipe_id,
        )
        return None
    return recipe
