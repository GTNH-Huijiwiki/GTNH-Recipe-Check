"""第一步 Raw -> Data 处理器。"""

from __future__ import annotations

import logging

from s0_contracts.logger import log_stage
from s0_contracts.stage import RunContext, StageResult
from s1_preprocess.split import run_items, run_oredictionary, run_recipes

logger = logging.getLogger(__name__)


class PreprocessHandler:
    name = "preprocess"

    def run(self, context: RunContext) -> StageResult:
        log_stage("预处理：Raw -> Data")

        logger.info("处理物品与流体")
        items = run_items(context.raw_dir, context.data_dir)
        logger.info("物品与流体完成：%d 条记录", len(items))

        logger.info("处理矿辞")
        ore_report, ore_count = run_oredictionary(
            context.raw_dir, context.data_dir, items
        )
        logger.info(
            "矿辞完成：%d 个条目，%d 个未解析通配",
            ore_count,
            len(ore_report["unresolvedWildcardMembers"]),
        )

        logger.info("处理配方")
        recipe_stats = run_recipes(context.raw_dir, context.data_dir, items)
        logger.info(
            "配方完成：%d 个文件，%d 条配方",
            recipe_stats["recipeFiles"],
            recipe_stats["recipes"],
        )
        logger.info(
            "预处理完成：物品 %d 条，矿辞 %d 个，配方 %d 条",
            len(items),
            ore_count,
            recipe_stats["recipes"],
        )
        return StageResult(
            stage=self.name,
            outputs=(
                context.data_dir / "Items.json",
                context.data_dir / "OreDictionary.json",
                context.data_dir / "Recipes",
            ),
            stats={
                "items": len(items),
                "oreDict": ore_count,
                "oreDictUnresolved": len(
                    ore_report["unresolvedWildcardMembers"]
                ),
                **recipe_stats,
            },
        )
