"""Recipe Check 阶段编排入口。"""

import argparse
import logging

import config
from s0_contracts.logger import configure_logging
from s0_contracts.stage import RunContext
from s1_preprocess.handler import PreprocessHandler
from s2_normalize.handler import NormalizeHandler
from s3_rules.handler import RulesHandler
from s4_cover.handler import CatalystPickHandler
from s5_conflict.handler import ConflictHandler
from s6_color.handler import ColorHandler

logger = logging.getLogger(__name__)

_HANDLERS = {
    "preprocess": PreprocessHandler(),
    "normalize": NormalizeHandler(),
    "rules": RulesHandler(),
    "cover": CatalystPickHandler(),
    "conflict": ConflictHandler(),
    "color": ColorHandler(),
}


_ALL_STAGES = ("preprocess", "normalize", "rules", "cover", "conflict", "color")
_DEFAULT_STAGES = ("normalize", "rules", "cover", "conflict", "color")


def _parse_args():
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description=(
            "GTNH 配方检查流水线：规范化、机器规则、催化集覆盖、"
            "冲突图和图着色。"
        ),
        epilog=(
            "常用示例:\n"
            "  python main.py\n"
            "      执行 normalize → rules → cover → conflict → color\n"
            "  python main.py --stage conflict --conflict-report\n"
            "      只重建冲突图，并生成完整人类可读报告 conflicts.md\n"
            "  python main.py --no-rules\n"
            "      跳过全部 s3 机器规则，结果写入 Output/<版本>-no-rules/\n"
            "  python main.py --stage all --no-rules --conflict-report\n"
            "      从 Raw 开始执行完整无规则链路，并生成冲突报告"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        choices=(*_ALL_STAGES, "all"),
        help=(
            "只执行一个阶段：preprocess / normalize / rules / cover / "
            "conflict / color / all；不传时执行 normalize 到 color"
        ),
    )
    parser.add_argument(
        "--conflict-report",
        action="store_true",
        help=(
            "生成完整人类可读冲突报告 conflict_graph/conflicts.md；"
            "默认关闭，仅当本次运行包含 conflict 阶段时生效"
        ),
    )
    parser.add_argument(
        "--no-rules",
        action="store_true",
        help=(
            "s3 不应用任何机器规则，原样复制 normalized recipes；"
            "全部产物写入 Output/<版本>-no-rules/，不覆盖正式输出"
        ),
    )
    return parser.parse_args()


def _stage_list(stage):
    if stage is None:
        return list(_DEFAULT_STAGES)
    if stage == "all":
        return list(_ALL_STAGES)
    return [stage]


def main():
    args = _parse_args()
    stages = _stage_list(args.stage)
    output_version = config.OUTPUT_VERSION
    if args.no_rules:
        output_version = f"{output_version}-no-rules"
    output_dir = config.OUTPUT_ROOT / output_version
    log_file = output_dir / "recipe_check.log"
    context = RunContext(
        version=config.DATA_SPLIT_DIR.name,
        raw_dir=config.DUMPER_DIR,
        data_dir=config.DATA_SPLIT_DIR,
        output_dir=output_dir,
        generate_conflict_report=args.conflict_report,
        no_rules=args.no_rules,
    )
    counter = configure_logging(log_file)
    logger.info("========== Recipe Check 开始 ==========")
    try:
        for stage in stages:
            _HANDLERS[stage].run(context)
    except Exception:
        logger.exception(
            "阶段 %s 执行失败，详细堆栈已写入 %s",
            stage,
            log_file,
        )
        raise SystemExit(1)
    logger.info(
        "========== Recipe Check 完成：记录 %d 个问题 ==========",
        counter.count,
    )


if __name__ == "__main__":
    main()
