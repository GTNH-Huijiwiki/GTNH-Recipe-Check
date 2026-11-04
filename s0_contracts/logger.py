"""应用日志配置与问题计数。"""

import logging
import sys
from pathlib import Path


class InfoOnlyFilter(logging.Filter):
    """只允许 INFO 级别日志显示在终端。"""

    def filter(self, record):
        return record.levelno == logging.INFO


class IssueCounter(logging.Handler):
    """统计警告及以上级别的日志数量。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


def configure_logging(log_path: Path) -> IssueCounter:
    """将应用日志写入文件，并返回本次运行的问题计数器。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(InfoOnlyFilter())
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    issue_counter = IssueCounter()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(issue_counter)
    return issue_counter