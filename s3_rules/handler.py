"""s3 机器规则处理器。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

import config
from s0_contracts.schema import Recipe
from s0_contracts.logger import log_machine, log_stage
from s0_contracts.stage import RunContext, StageResult
from s3_rules.registry import get_rule

logger = logging.getLogger(__name__)


def _hash_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_manifest(upstream_hash, output_dir):
    artifacts = {}
    for path in sorted(output_dir.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        relative = path.relative_to(output_dir).as_posix()
        artifacts[relative] = "sha256:" + _hash_file(path)

    digest = hashlib.sha256()
    for relative in sorted(artifacts):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(artifacts[relative].encode("ascii"))
        digest.update(b"\0")
    return {
        "upstream": {"normalized_recipes": upstream_hash},
        "artifacts": artifacts,
        "outputHash": "sha256:" + digest.hexdigest(),
    }


class RulesHandler:
    """应用机器规则并生成 selected_recipes。"""

    name = "rules"

    def run(self, context: RunContext) -> StageResult:
        log_stage("机器规则：normalized_recipes -> selected_recipes")
        normalized_dir = context.output_dir / "normalized_recipes"
        normalized_manifest = json.loads(
            (normalized_dir / "manifest.json").read_text(encoding="utf-8")
        )
        upstream_hash = normalized_manifest["outputHash"]

        output_dir = context.output_dir / "selected_recipes"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        input_count = 0
        output_count = 0
        for machine in sorted(config.INTERESTED_MACHINES):
            log_machine(machine)
            source_path = normalized_dir / "machines" / machine / "recipes.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            rule = get_rule(machine)
            selected = []
            for value in source.get("recipes", []):
                recipe = Recipe.from_dict(value)
                result = recipe if context.no_rules else rule(recipe)
                if result is not None:
                    selected.append(result.to_dict())
            input_count += len(source.get("recipes", []))
            output_count += len(selected)
            _write_json(
                output_dir / "machines" / machine / "recipes.json",
                {"machine": machine, "recipes": selected},
            )
            if context.no_rules:
                logger.info(
                    "无规则复制：%d 条配方",
                    len(selected),
                )
            else:
                logger.info(
                    "应用规则：%d → %d 条配方",
                    len(source.get("recipes", [])),
                    len(selected),
                )

        manifest = _build_manifest(upstream_hash, output_dir)
        _write_json(output_dir / "manifest.json", manifest)
        logger.info(
            "机器规则完成：%d 台机器，输入 %d 条，输出 %d 条",
            len(config.INTERESTED_MACHINES),
            input_count,
            output_count,
        )
        return StageResult(
            stage=self.name,
            outputs=(output_dir, output_dir / "manifest.json"),
            stats={
                "machines": len(config.INTERESTED_MACHINES),
                "inputRecipes": input_count,
                "selectedRecipes": output_count,
                "outputHash": manifest["outputHash"],
            },
        )
