"""冲突检查模式注册中心。"""

from s5_conflict.modes.blocking import BlockingMode

_MODES = {
    BlockingMode.name: BlockingMode(),
}


def get_mode(name):
    """按名称返回发配模式。"""
    try:
        return _MODES[name]
    except KeyError as exc:
        raise ValueError(f"未知冲突检查模式：{name}") from exc
