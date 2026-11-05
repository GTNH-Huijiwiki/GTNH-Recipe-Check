"""s2 规范化阶段处理器。"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

import config
from s0_contracts.schema import (
    CatalystGroup,
    CatalystSet,
    InputGroup,
    InputSlot,
    MaterialRef,
    OrphanInfo,
    OutputGroup,
    Recipe,
    RecipeProvenance,
    make_catalyst_set_id,
)
from s0_contracts.logger import log_machine, log_stage
from s0_contracts.stage import RunContext, StageResult
from s2_normalize.data_loader import load_item_translations, load_ore_dictionary
from s2_normalize.recipe_grouper import group_recipes
from s2_normalize.text import clean_display_text

logger = logging.getLogger(__name__)


def _sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def _hash_file(path: Path):
    return _sha256_bytes(path.read_bytes())


def _hash_files(paths, root: Path):
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _material(value, kind):
    return MaterialRef(
        key=value.get("key", ""),
        kind=kind,
        amount=value.get("amount", 0),
        original_name=clean_display_text(value.get("originalName", "")),
        translated_name=clean_display_text(value.get("translatedName", "")),
    )



def _build_catalyst_set(value):
    catalysts = tuple(_material(item, "item") for item in value.get("catalysts", []))
    circuit_value = value.get("circuit")
    circuit = _material(circuit_value, "item") if circuit_value else None
    kind = value.get("kind", "normal")
    return CatalystSet(
        set_id=make_catalyst_set_id(kind, catalysts, circuit),
        catalysts=catalysts,
        circuit=circuit,
        kind=kind,
    )


def _build_input_group(value):
    slots = []
    for item in value.get("inputItems", []):
        slots.append(InputSlot((_material(item, "item"),)))
    for fluid in value.get("inputFluids", []):
        slots.append(InputSlot((_material(fluid, "fluid"),)))
    for group in value.get("inputGroups", []):
        kind = group.get("kind", "item")
        slots.append(
            InputSlot(
                tuple(
                    _material(member, kind)
                    for member in group.get("members", [])
                )
            )
        )
    return InputGroup(tuple(slots))


def _build_recipe(value, machine, index, provenance):
    catalyst_sets = tuple(
        _build_catalyst_set(group)
        for group in value.get("catalystGroups", [])
    )
    diagnostics = tuple(dict(item) for item in value.get("alerts", []))
    return Recipe(
        recipe_id=f"{machine}_recipe_{index:04d}",
        machine=machine,
        input_group=_build_input_group(value),
        catalyst_group=CatalystGroup(catalyst_sets),
        output_group=OutputGroup(
            items=tuple(_material(item, "item") for item in value.get("outputItems", [])),
            fluids=tuple(
                _material(fluid, "fluid")
                for fluid in value.get("outputFluids", [])
            ),
        ),
        orphan=OrphanInfo.from_dict(value.get("orphan", {})),
        provenance=provenance,
        diagnostics=diagnostics,
    )


def _find_recipe_file(data_dir, machine):
    matches = sorted((data_dir / "Recipes").rglob(f"GTNEIDefaultHandler_{machine}.json"))
    if not matches:
        return None
    return matches[0]


def _manifest(upstream, output_dir):
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
        "upstream": upstream,
        "artifacts": artifacts,
        "outputHash": "sha256:" + digest.hexdigest(),
    }


class NormalizeHandler:
    """把 Wiki 预处理数据转换为正式规范化配方产物。"""

    name = "normalize"

    def run(self, context: RunContext) -> StageResult:
        log_stage("规范化：Data -> normalized_recipes")
        logger.info("加载物品与矿辞")
        translations = load_item_translations(context.data_dir)
        ore_dictionary = load_ore_dictionary(context.data_dir)
        item_keys = set(translations)
        logger.info(
            "物品与矿辞加载完成：物品 %d 个，矿辞 %d 个",
            len(translations),
            len(ore_dictionary),
        )

        output_dir = context.output_dir / "normalized_recipes"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        recipe_files = []
        total_recipes = 0
        for machine in sorted(config.INTERESTED_MACHINES):
            log_machine(machine)
            source_path = _find_recipe_file(context.data_dir, machine)
            if source_path is None:
                raise FileNotFoundError(
                    f"未找到机器 {machine} 的配方文件：{context.data_dir / 'Recipes'}"
                )
            recipe_files.append(source_path)
            data = json.loads(source_path.read_text(encoding="utf-8"))
            raw_recipes = data.get("recipes", [])
            metadata = {key: value for key, value in data.items() if key != "recipes"}
            provenance = RecipeProvenance(
                source_file=source_path.relative_to(context.data_dir).as_posix(),
                source=metadata.get("source", ""),
                identifier=metadata.get("identifier", ""),
            )
            grouped, report = group_recipes(
                raw_recipes,
                ore_dictionary,
                translations,
                item_keys,
                machine_name=machine,
                orphan_mode=config.GROUP_ORPHAN_MODE,
            )
            recipes = [
                _build_recipe(value, machine, index, provenance)
                for index, value in enumerate(grouped)
            ]

            machine_dir = output_dir / "machines" / machine
            _write_json(
                machine_dir / "recipes.json",
                {
                    "machine": machine,
                    "recipes": [recipe.to_dict() for recipe in recipes],
                },
            )
            total_recipes += len(recipes)
            logger.info(
                "规范化：%d → %d 条配方",
                report["inputRecipes"],
                report["outputRecipes"],
            )

        upstream = {
            "items": "sha256:" + _hash_file(context.data_dir / "Items.json"),
            "oreDictionary": "sha256:"
            + _hash_file(context.data_dir / "OreDictionary.json"),
            "recipes": "sha256:"
            + _hash_files(recipe_files, context.data_dir),
        }
        manifest = _manifest(upstream, output_dir)
        _write_json(output_dir / "manifest.json", manifest)
        logger.info(
            "配方规范化完成：%d 台机器，%d 条配方",
            len(config.INTERESTED_MACHINES),
            total_recipes,
        )
        return StageResult(
            stage=self.name,
            outputs=(output_dir, output_dir / "manifest.json"),
            stats={
                "machines": len(config.INTERESTED_MACHINES),
                "recipes": total_recipes,
                "outputHash": manifest["outputHash"],
            },
        )
