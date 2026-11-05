"""机器规则注册中心。"""

from s3_rules import (
    alloyblastsmelter,
    assembler,
    blastfurnace,
    fluidchemicaleactor,
    laserengraver,
    press,
)

_MACHINE_RULES = {
    "alloyblastsmelter": alloyblastsmelter.apply,
    "assembler": assembler.apply,
    "blastfurnace": blastfurnace.apply,
    "fluidchemicaleactor": fluidchemicaleactor.apply,
    "laserengraver": laserengraver.apply,
    "press": press.apply,
}


def _identity(recipe):
    return recipe


def get_rule(machine):
    """返回机器对应的规则函数。"""
    return _MACHINE_RULES.get(machine, _identity)
