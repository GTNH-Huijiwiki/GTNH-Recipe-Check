"""规范化阶段使用的展示文本处理。"""

import re

_FORMAT_CODE_RE = re.compile(r"§.")


def clean_display_text(value):
    """删除 Minecraft 格式码并去除首尾空白。"""
    return _FORMAT_CODE_RE.sub("", str(value or "")).strip()
