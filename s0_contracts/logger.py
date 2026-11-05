"""应用日志配置与问题计数。"""

import logging
import sys
from pathlib import Path


logger = logging.getLogger("recipe_check")


class InfoAndErrorFilter(logging.Filter):
    """终端只显示摘要信息与错误。"""

    def filter(self, record):
        return record.levelno == logging.INFO or record.levelno >= logging.ERROR


class SummaryFormatter(logging.Formatter):
    """终端摘要格式：只输出消息，不展开异常堆栈。"""

    def format(self, record):
        return self.formatMessage(record)


class IssueCounter(logging.Handler):
    """统计警告及以上级别的日志数量。"""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


def log_stage(title):
    """输出阶段分隔线。"""
    logger.info("=" * 80)
    logger.info("[阶段] %s", title)
    logger.info("=" * 80)


def log_machine(machine):
    """输出机器分隔线；机器名只出现一次，后续日志不再重复。"""
    logger.info("")
    logger.info("- " * 40)
    logger.info("%s", machine)


def configure_logging(log_path: Path) -> IssueCounter:
    """配置统一日志：文件记录完整详情，终端只显示摘要与错误。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.addFilter(InfoAndErrorFilter())
    console_handler.setFormatter(SummaryFormatter())

    issue_counter = IssueCounter()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(issue_counter)
    return issue_counter
