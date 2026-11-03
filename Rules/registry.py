"""规则注册中心：维护机器名到清理函数的映射。"""

from Rules import assembler, fluidchemicaleactor, laserengraver, press

# 机器名 -> 清理函数（输入：翻译后的配方列表，输出：删减后的配方列表）
_MACHINE_CLEANERS = {
    "assembler": assembler.clean,
    "fluidchemicaleactor": fluidchemicaleactor.clean,
    "laserengraver": laserengraver.clean,
    "press": press.clean,
}


def get_cleaner(original_name):
    """返回该机器的清理函数，若未注册则使用默认（不删除）。"""
    return _MACHINE_CLEANERS.get(original_name, _default_cleaner)


def _default_cleaner(recipes):
    return recipes
