"""s5 冲突图处理器。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

import config
from s0_contracts.schema import CatalystSet, Recipe
from s0_contracts.logger import log_machine, log_stage
from s0_contracts.stage import RunContext, StageResult
from s5_conflict.modes.base import ConflictContext
from s5_conflict.registry import get_mode
from s5_conflict.report import (
    render_conflict_report,
    render_machine_report,
)

logger = logging.getLogger(__name__)

_SELF_CONFLICT_CONSOLE_LIMIT = 10


def _material_name(item):
    return item.translated_name or item.original_name or item.key


def _recipe_summary(recipe):
    input_parts = []
    for slot in recipe.input_group.slots:
        names = [_material_name(item) for item in slot.alternatives]
        if names:
            input_parts.append("|".join(names))
    output_parts = [
        _material_name(item)
        for item in recipe.output_group.items + recipe.output_group.fluids
    ]
    inputs = " + ".join(input_parts) or "(无输入)"
    outputs = " + ".join(output_parts) or "(无输出)"
    return f"{inputs} → {outputs}"


def _hash_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_manifest(upstream, output_dir):
    artifacts = {}
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".md"}:
            continue
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
        "upstream": upstream,
        "artifacts": artifacts,
        "outputHash": "sha256:" + digest.hexdigest(),
    }


def _load_recipes(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Recipe.from_dict(value) for value in data.get("recipes", []))


def _load_coverage(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sets = {
        value["setId"]: CatalystSet.from_dict(value)
        for value in data.get("sets", [])
    }
    return sets, data["cover"]["selectedSetIds"], data.get("relations", {})


class ConflictHandler:
    """构建每台机器的无向冲突图。"""

    name = "conflict"

    def run(self, context: RunContext) -> StageResult:
        log_stage("冲突图：selected_recipes + catalyst_pick -> conflict_graph")
        normalized_dir = context.output_dir / "normalized_recipes"
        selected_dir = context.output_dir / "selected_recipes"
        pick_dir = context.output_dir / "catalyst_pick"

        upstream = {
            "normalized_recipes": json.loads(
                (normalized_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
            "selected_recipes": json.loads(
                (selected_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
            "catalyst_pick": json.loads(
                (pick_dir / "manifest.json").read_text(encoding="utf-8")
            )["outputHash"],
        }

        output_dir = context.output_dir / "conflict_graph"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        mode = get_mode(config.CONFLICT_MODE)
        total_edges = 0
        total_self_conflicts = 0
        total_recipe_pairs = 0
        logged_self_conflicts = 0
        report_sections = [] if context.generate_conflict_report else None
        for machine in sorted(config.INTERESTED_MACHINES):
            log_machine(machine)
            normalized = _load_recipes(
                normalized_dir / "machines" / machine / "recipes.json"
            )
            selected = _load_recipes(
                selected_dir / "machines" / machine / "recipes.json"
            )
            sets, selected_ids, relations = _load_coverage(
                pick_dir / "machines" / machine / "coverage.json"
            )
            selected_sets = {
                set_id: sets[set_id] for set_id in selected_ids
            }
            graph = mode.build(
                ConflictContext(
                    machine=machine,
                    selected_sets=selected_sets,
                    selected_recipes=selected,
                    normalized_recipes=normalized,
                    max_conflicts=config.CONFLICT_MAX_PAIRS,
                )
            )
            _write_json(
                output_dir / "machines" / machine / "graph.json",
                graph,
            )
            total_edges += len(graph["edges"])
            machine_self_conflicts = sum(
                len(item["conflicts"]) for item in graph["selfConflicts"]
            )
            total_self_conflicts += machine_self_conflicts
            total_recipe_pairs += sum(
                len(item["conflicts"]) for item in graph["edges"]
            )
            total_recipe_pairs += machine_self_conflicts
            normalized_by_id = {
                recipe.recipe_id: recipe for recipe in normalized
            }
            selected_by_id = {
                recipe.recipe_id: recipe for recipe in selected
            }
            if report_sections is not None:
                report_sections.append(
                    render_machine_report(
                        machine,
                        graph,
                        selected_sets,
                        sets,
                        relations,
                        selected_by_id,
                        normalized_by_id,
                        pair_threshold=context.conflict_report_pair_threshold,
                    )
                )
            for item in graph["selfConflicts"]:
                for conflict in item["conflicts"]:
                    left_id = conflict["leftRecipeId"]
                    right_id = conflict["rightRecipeId"]
                    logger.warning(
                        "配方自冲突：催化集=%s，配方=%s / %s，方向=%s",
                        item["setId"],
                        left_id,
                        right_id,
                        ",".join(conflict["directions"]),
                    )
                    log = (
                        logger.info
                        if logged_self_conflicts < _SELF_CONFLICT_CONSOLE_LIMIT
                        else logger.debug
                    )
                    log(
                        "  配方 %s: %s",
                        left_id,
                        _recipe_summary(normalized_by_id[left_id]),
                    )
                    log(
                        "  配方 %s: %s",
                        right_id,
                        _recipe_summary(normalized_by_id[right_id]),
                    )
                    logged_self_conflicts += 1
            logger.info(
                "冲突图：%d 个节点，%d 条边，%d 个自冲突配方对",
                len(graph["nodes"]),
                len(graph["edges"]),
                machine_self_conflicts,
            )

        report_path = None
        if report_sections is not None:
            report_path = output_dir / "conflicts.md"
            report_path.write_text(
                render_conflict_report(
                    report_sections,
                    pair_threshold=context.conflict_report_pair_threshold,
                ),
                encoding="utf-8",
            )
        manifest = _build_manifest(upstream, output_dir)
        _write_json(output_dir / "manifest.json", manifest)
        logger.info(
            "冲突图构建完成：%d 台机器，%d 条边，%d 组配方对，%d 个自冲突配方对",
            len(config.INTERESTED_MACHINES),
            total_edges,
            total_recipe_pairs,
            total_self_conflicts,
        )
        outputs = [output_dir, output_dir / "manifest.json"]
        if report_path is not None:
            logger.info("人类可读冲突报告：%s", report_path)
            outputs.append(report_path)
        return StageResult(
            stage=self.name,
            outputs=tuple(outputs),
            stats={
                "machines": len(config.INTERESTED_MACHINES),
                "edges": total_edges,
                "recipePairs": total_recipe_pairs,
                "selfConflicts": total_self_conflicts,
                "conflictReport": report_path is not None,
                "outputHash": manifest["outputHash"],
            },
        )
