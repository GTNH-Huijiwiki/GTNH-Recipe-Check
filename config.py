"""集中配置：数据目录、兴趣机器、来源目录、输出路径与着色参数。"""

from pathlib import Path

# GTNH Dumper 导出的原始数据目录（split.py 的输入）
DUMPER_DIR = Path(
    r"D:\Special\PrismLauncher\instances\GT_New_Horizons_2.9.0-beta-3_Java_17-26\.minecraft\dumps"
)

# split.py 的输出目录，即本仓库其它模块的数据根目录
DATA_SPLIT_DIR = Path("./Data/2.9.0-beta-3")
DATA_DIR = DATA_SPLIT_DIR

# 兴趣机器白名单；机器名 = 导出文件名去掉 GTNEIDefaultHandler_ 前缀与 .json 后缀
INTERESTED_MACHINES = {"fluidchemicaleactor"}

# 在 Recipes/ 下搜索机器的来源 mod 目录
# 同一机器名出现在多个来源时按来源分别输出（文件名加 `__来源` 后缀）
RECIPE_SOURCE_DIRS = ("gregtech", "miscutils")

# 输出根目录
OUTPUT_ROOT = Path("Output")

# 运行日志
LOG_FILE = OUTPUT_ROOT / "recipe_check.log"

# 1 清洗产物目录
CLEANED_RECIPES_DIR = OUTPUT_ROOT / "cleaned_recipes"

# 2 过滤产物目录
FILTERED_RECIPES_DIR = OUTPUT_ROOT / "filtered_recipes"

# 3 着色产物目录
COLORED_RECIPES_DIR = OUTPUT_ROOT / "colored_recipes"

# 冲突图着色探索轮数
COLORING_ROUNDS = 128

# 单个颜色类允许的最大节点数；None 表示不限制
COLOR_CLASS_MAX_SIZE = None
