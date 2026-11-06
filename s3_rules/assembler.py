"""组装机专属规则。"""

import logging
import re

from s0_contracts.schema import Recipe
from s3_rules.helpers import outputs_all, remove_variants_where_all_inputs_match

logger = logging.getLogger(__name__)

_TROPHY_MARKER = "trophy"
_CIRCUIT_KEY_PREFIX = "gregtech:gt.integrated_circuit:"
_CIRCUIT_24_KEY = f"{_CIRCUIT_KEY_PREFIX}24"
_SINGLE_WIRE_PATTERN = re.compile(r"1x .+ Wire")
_BUNDLED_WIRE_PATTERN = re.compile(r"\d+x .+ Wire")


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


def _is_wire(item):
    """返回物品名称是否以 Wire 结尾，避免把 Cable 识别为导线。"""
    return item.original_name.casefold().endswith(" wire")


def _is_superconductor_wire(item):
    """返回物品是否为超导导线。"""
    return _is_wire(item) and "superconductor" in item.original_name.casefold()


def _is_bundled_wire_recipe(recipe):
    """判断唯一输入是否为 1x 导线，且唯一输出是否为 Nx 导线。"""
    if recipe.output_group.fluids or len(recipe.output_group.items) != 1:
        return False
    if len(recipe.input_group.slots) != 1:
        return False
    slot = recipe.input_group.slots[0]
    if len(slot.alternatives) != 1:
        return False

    input_item = slot.alternatives[0]
    output_item = recipe.output_group.items[0]
    if input_item.kind != "item" or output_item.kind != "item":
        return False
    if _is_superconductor_wire(output_item):
        return False
    return (
        _SINGLE_WIRE_PATTERN.fullmatch(input_item.original_name) is not None
        and _BUNDLED_WIRE_PATTERN.fullmatch(output_item.original_name) is not None
    )


def apply(recipe: Recipe):
    """应用组装机规则，返回保留后的配方或 None。"""
    recipe_id = recipe.recipe_id
    if _is_bundled_wire_recipe(recipe):
        logger.debug(
            "组装机规则删除配方 %s：唯一输入为 1x 导线且输出为 Nx 导线",
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
