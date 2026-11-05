"""激光蚀刻机专属规则。"""

import logging

from s3_rules.helpers import (
    input_slot_count,
    remove_matching_inputs,
    remove_variants_where_all_inputs_match,
    replace_catalyst_sets,
)
from s0_contracts.schema import Recipe

logger = logging.getLogger(__name__)

_GEM_PREFIXES = ("Chipped ", "Flawed ", "Flawless ", "Exquisite ")
_REMOVED_CATALYST_KEYS = {"dreamcraft:ReinforcedGlassLense"}
_REMOVED_CATALYST_NAMES = {"Reinforced Glass Lens"}


def _uses_removed_catalyst(catalyst_set):
    return any(
        item.key in _REMOVED_CATALYST_KEYS
        or item.original_name in _REMOVED_CATALYST_NAMES
        for item in catalyst_set.catalysts
    )


def _remove_removed_catalyst_sets(recipe):
    original_sets = recipe.catalyst_group.sets
    kept = tuple(
        item for item in original_sets if not _uses_removed_catalyst(item)
    )
    if original_sets and not kept:
        return None
    if kept != original_sets:
        recipe = replace_catalyst_sets(recipe, kept)
    return recipe


def apply(recipe: Recipe):
    """应用激光蚀刻机规则，返回保留后的配方或 None。"""
    recipe = _remove_removed_catalyst_sets(recipe)
    if recipe is None:
        logger.debug("激光蚀刻机规则删除配方：使用受移除的催化剂")
        return None

    recipe = remove_matching_inputs(
        recipe,
        lambda item: item.original_name == "Unaligned Quark Releasing Catalyst",
    )
    if recipe is None:
        logger.debug("激光蚀刻机规则删除配方：仅包含不对齐夸克释放催化剂")
        return None

    output_names = [item.original_name for item in recipe.output_group.items]
    if output_names and all(
        name.startswith(_GEM_PREFIXES) for name in output_names
    ):
        logger.debug("激光蚀刻机规则删除配方 %s：输出全部为宝石", recipe.recipe_id)
        return None

    if input_slot_count(recipe) == 1:
        recipe = remove_variants_where_all_inputs_match(
            recipe,
            lambda item: item.original_name in {"Floppy Disk", "EEPROM"},
        )
        if recipe is None:
            logger.debug("激光蚀刻机规则删除配方：输入仅为软盘或 EEPROM")
            return None

    if input_slot_count(recipe):
        recipe = remove_variants_where_all_inputs_match(
            recipe,
            lambda item: item.original_name.startswith("Flawed "),
        )
        if recipe is None:
            logger.debug("激光蚀刻机规则删除配方：输入全部为 Flawed 候选")
            return None

    return recipe
