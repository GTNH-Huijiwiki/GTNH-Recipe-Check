"""数据加载工具：机器配方、物品翻译、矿辞典。"""

import json
import logging
from pathlib import Path

from s2_normalize.text import clean_display_text

logger = logging.getLogger(__name__)


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_machine_recipes(recipe_file: Path):
    """
    读取一台机器的配方文件，返回 (original_name, translated_name, recipes)。
    文件路径形如 Recipes/<来源mod>/GTNEIDefaultHandler_<机器名>.json（一机一文件）。
    若文件名不匹配或文件非法，返回 None。
    """
    prefix = "GTNEIDefaultHandler_"
    if not recipe_file.stem.startswith(prefix):
        logger.warning("文件名格式不匹配，跳过: %s", recipe_file.name)
        return None

    try:
        data = _read_json(recipe_file)
    except (OSError, ValueError) as e:
        logger.error("读取 %s 出错: %s", recipe_file, e)
        return None

    recipes = data.get("recipes", [])
    if not isinstance(recipes, list):
        logger.warning("%s 中的 recipes 不是列表，已忽略", recipe_file.name)
        return None

    original_name = recipe_file.stem[len(prefix):]
    return original_name, clean_display_text(data.get("name", "")), recipes


def load_item_translations(data_dir: Path) -> dict:
    """从 DATA_DIR/Items.json 加载所有物品的翻译映射（单文件）。"""
    items_file = data_dir / "Items.json"
    if not items_file.exists():
        raise FileNotFoundError(f"未找到物品文件: {items_file}")

    data = _read_json(items_file)
    items = data.get("items", []) if isinstance(data, dict) else []
    translations = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            key = item["key"]
            original_name = clean_display_text(item.get("originalName", key)) or key
            translated_name = (
                clean_display_text(item.get("translatedName", "")) or original_name
            )
            translations[key] = {
                "originalName": original_name,
                "translatedName": translated_name,
            }
    return translations


def load_ore_dictionary(data_dir: Path) -> dict:
    """从 DATA_DIR/OreDictionary.json 加载矿辞典成员映射（单文件）。"""
    ore_file = data_dir / "OreDictionary.json"
    if not ore_file.exists():
        raise FileNotFoundError(f"未找到矿辞典文件: {ore_file}")

    data = _read_json(ore_file)
    ore_dictionary = {}
    if isinstance(data, dict):
        for ore_name, keys in data.items():
            if isinstance(keys, list):
                ore_dictionary[ore_name] = list(dict.fromkeys(keys))  # 去重保序。
    return ore_dictionary
