"""Build stage-1 grouped recipes into Output/grouped_recipes.

This runner is intentionally independent from main.py so the existing
Rules/checker pipeline remains usable while the grouped schema is validated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from Core.data_loader import load_item_translations, load_ore_dictionary  # noqa: E402
from Core.logger import configure_logging  # noqa: E402
from Core.recipe_grouper import group_recipes  # noqa: E402


def _find_machine_file(machine: str):
    recipes_root = Path(config.DATA_DIR) / "Recipes"
    matches = sorted(recipes_root.glob(f"*/GTNEIDefaultHandler_{machine}.json"))
    if not matches:
        return None, None
    return matches[0], matches[0].parent.name


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stage-1 equivalent-input grouping")
    parser.add_argument("machines", nargs="*", help="machine names; default: INTERESTED_MACHINES")
    args = parser.parse_args()

    machines = args.machines or sorted(config.INTERESTED_MACHINES)
    output_dir = Path(config.GROUPED_RECIPES_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    issue_counter = configure_logging(output_dir / "grouping.log")

    translations = load_item_translations(Path(config.DATA_DIR))
    ore_dictionary = load_ore_dictionary(Path(config.DATA_DIR))
    item_keys = set(translations)

    index = []
    orphan_report = {}
    for machine in machines:
        recipe_file, source = _find_machine_file(machine)
        if recipe_file is None:
            print(f"[skip] {machine}: recipe file not found")
            continue

        data = json.loads(recipe_file.read_text(encoding="utf-8"))
        raw_recipes = data.get("recipes", [])
        grouped, report = group_recipes(
            raw_recipes,
            ore_dictionary,
            translations,
            item_keys,
            machine_name=machine,
            orphan_mode=config.GROUP_ORPHAN_MODE,
        )

        output_path = output_dir / f"{machine}.json"
        output_path.write_text(
            json.dumps(grouped, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        entry = {
            "machine": machine,
            "name": data.get("name", ""),
            "source": source,
            "file": output_path.name,
            **report,
        }
        index.append(entry)
        orphan_report[machine] = report
        print(
            f"[done] {machine}: {report['inputRecipes']:,} -> "
            f"{report['normalizedRecipes']:,} -> {report['outputRecipes']:,} recipes; "
            f"alerts={report['alerts']}; "
            f"orphan(has/only)={report['orphanHasInput']}/{report['orphanOnlyInputs']}"
        )

    (output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "orphan_report.json").write_text(
        json.dumps(orphan_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Output: {output_dir}")
    print(f"Warnings/errors: {issue_counter.count}")


if __name__ == "__main__":
    main()
