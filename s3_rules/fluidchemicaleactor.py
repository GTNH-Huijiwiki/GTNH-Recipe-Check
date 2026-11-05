"""化工厂专属规则。"""

import logging

from s0_contracts.schema import (
    UNCONDITIONAL_SET_ID,
    CatalystSet,
    Recipe,
    make_catalyst_set_id,
    make_unconditional_set,
)
from s3_rules.helpers import replace_catalyst_sets

logger = logging.getLogger(__name__)

_UNCONDITIONAL_SET = make_unconditional_set()


def apply(recipe: Recipe):
    """清空全部催化剂，只保留电路；无电路时转为无条件条件。"""
    if not recipe.catalyst_group.sets:
        return recipe

    transformed = {}
    for catalyst_set in recipe.catalyst_group.sets:
        if catalyst_set.circuit is None:
            transformed[UNCONDITIONAL_SET_ID] = _UNCONDITIONAL_SET
            continue
        candidate = CatalystSet(
            set_id=make_catalyst_set_id("normal", (), catalyst_set.circuit),
            catalysts=(),
            circuit=catalyst_set.circuit,
            kind="normal",
        )
        transformed[candidate.set_id] = candidate

    sets = tuple(transformed[key] for key in sorted(transformed))
    if any(item.kind == "unconditional" for item in sets):
        sets = (_UNCONDITIONAL_SET,)
    logger.debug(
        "化工厂规则改写配方 %s：%d 个催化集 → %d 个",
        recipe.recipe_id,
        len(recipe.catalyst_group.sets),
        len(sets),
    )
    return replace_catalyst_sets(recipe, sets)
