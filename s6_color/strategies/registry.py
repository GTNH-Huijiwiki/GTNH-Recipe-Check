"""图着色策略注册中心。"""

from s6_color.strategies.dsatur_multistart import DSATURMultistartStrategy

_STRATEGIES = {
    DSATURMultistartStrategy.name: DSATURMultistartStrategy(),
}


def get_strategy(name):
    """按名称返回着色策略。"""
    try:
        return _STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(f"未知着色策略：{name}") from exc
