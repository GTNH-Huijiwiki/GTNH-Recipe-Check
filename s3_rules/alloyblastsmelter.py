"""合金高炉专属规则。"""

import logging

from s0_contracts.schema import Recipe

logger = logging.getLogger(__name__)

_CIRCUIT_11_KEY = "gregtech:gt.integrated_circuit:11"


def _uses_circuit_11(catalyst_set):
    return (
        catalyst_set.circuit is not None
        and catalyst_set.circuit.key == _CIRCUIT_11_KEY
    )


def apply(recipe: Recipe):
    """应用合金高炉规则，返回保留后的配方或 None。"""
    if any(
        _uses_circuit_11(catalyst_set)
        for catalyst_set in recipe.catalyst_group.sets
    ):
        logger.debug(
            "合金高炉规则删除配方 %s：使用编程电路11",
            recipe.recipe_id,
        )
        return None
    return recipe