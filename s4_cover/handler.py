"""s4 催化集关系与最小覆盖处理器。"""

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
from s4_cover.relations import (
    analyze_relations,
    collect_catalyst_sets,
    coverage_by_set,
)
from s4_cover.set_cover import minimum_set_cover

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
        "upstream": {"selected_recipes": upstream_hash},
        "artifacts": artifacts,
        "outputHash": "sha256:" + digest.hexdigest(),
    }


class CatalystPickHandler:
    """构建催化集关系并选取最小覆盖。"""

    name = "cover"

    def run(self, context: RunContext) -> StageResult:
        log_stage("催化集覆盖：selected_recipes -> catalyst_pick")
        selected_dir = context.output_dir / "selected_recipes"
        selected_manifest = json.loads(
            (selected_dir / "manifest.json").read_text(encoding="utf-8")
        )
        upstream_hash = selected_manifest["outputHash"]

        output_dir = context.output_dir / "catalyst_pick"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        total_sets = 0
        total_selected = 0
        total_relations = 0
        for machine in sorted(config.INTERESTED_MACHINES):
            log_machine(machine)
            source_path = selected_dir / "machines" / machine / "recipes.json"
            source = json.loads(source_path.read_text(encoding="utf-8"))
            recipes = [
                Recipe.from_dict(value) for value in source.get("recipes", [])
            ]
            sets = collect_catalyst_sets(recipes)
            coverage = coverage_by_set(recipes, sets)
            relations = analyze_relations(sets, coverage)
            selected_ids, stats = minimum_set_cover(
                [recipe.recipe_id for recipe in recipes],
                [
                    {
                        "key": set_id,
                        "coverage": coverage[set_id],
                    }
                    for set_id in sets
                ],
            )
            selected_ids = sorted(selected_ids)
            coverage_document = {
                "machine": machine,
                "summary": {
                    "recipeCount": len(recipes),
                    "setCount": len(sets),
                    "relationCount": len(relations["edges"]),
                    "selectedCount": len(selected_ids),
                },
                "sets": [sets[set_id].to_dict() for set_id in sets],
                "relations": relations,
                "cover": {
                    "optimal": bool(stats["optimal"]),
                    "selectedSetIds": selected_ids,
                    "candidateCount": stats["candidate_count"],
                    "reducedCandidateCount": stats["reduced_candidate_count"],
                    "searchNodes": stats["search_nodes"],
                },
            }
            _write_json(
                output_dir / "machines" / machine / "coverage.json",
                coverage_document,
            )
            total_sets += len(sets)
            total_selected += len(selected_ids)
            total_relations += len(relations["edges"])
            logger.info(
                "催化集选择：%d 个配方，%d 个集合，%d 条关系，选中 %d 个",
                len(recipes),
                len(sets),
                len(relations["edges"]),
                len(selected_ids),
            )

        manifest = _build_manifest(upstream_hash, output_dir)
        _write_json(output_dir / "manifest.json", manifest)
        logger.info(
            "催化集覆盖完成：%d 台机器，%d 个集合，%d 个选中集合",
            len(config.INTERESTED_MACHINES),
            total_sets,
            total_selected,
        )
        return StageResult(
            stage=self.name,
            outputs=(output_dir, output_dir / "manifest.json"),
            stats={
                "machines": len(config.INTERESTED_MACHINES),
                "sets": total_sets,
                "relations": total_relations,
                "selectedSets": total_selected,
                "outputHash": manifest["outputHash"],
            },
        )
