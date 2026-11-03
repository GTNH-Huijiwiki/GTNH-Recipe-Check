"""集中配置：数据目录、兴趣机器、输出路径与着色参数。"""

from pathlib import Path

# GTNH Dumper 导出的原始数据目录（split.py 的输入）
DUMPER_DIR = Path("./Raw/2.9.0")

# split.py 的输出目录，即本仓库其它模块的数据根目录
DATA_SPLIT_DIR = Path("./Data/2.9.0")
DATA_DIR = DATA_SPLIT_DIR

# 兴趣机器白名单；机器名 = 导出文件名去掉 GTNEIDefaultHandler_ 前缀与 .json 后缀
INTERESTED_MACHINES = {
    "alloyblastsmelter",
    "assembler",
    "fluidchemicaleactor",
    "largechemicalreactor",
    "laserengraver",
    "multimixer",
    "press",
}

# 输出根目录
OUTPUT_ROOT = Path("Output")

# 运行日志
LOG_FILE = OUTPUT_ROOT / "recipe_check.log"

# 1 清洗产物目录
CLEANED_RECIPES_DIR = OUTPUT_ROOT / "cleaned_recipes"

# 2 过滤产物目录
FILTERED_RECIPES_DIR = OUTPUT_ROOT / "filtered_recipes"

# 空催化组（编程电路0）单向串配方全量报告（每对配方均列出）
EMPTY_CONFLICT_FILE = FILTERED_RECIPES_DIR / "empty_group_conflicts.json"

# 3 着色产物目录
COLORED_RECIPES_DIR = OUTPUT_ROOT / "colored_recipes"

# 1 阶段等效输入组产物目录（独立于现有冲突检查下游）
GROUPED_RECIPES_DIR = OUTPUT_ROOT / "grouped_recipes"

# 孤儿标记偏好：contains | only（两种 flag 始终记录，仅用于下游选择）
GROUP_ORPHAN_MODE = "contains"

# 冲突图着色探索轮数（每个「单色类最大节点数」档位各跑这么多轮）
COLORING_ROUNDS = 128

# 单个颜色类允许的最大节点数；None 表示不限制
COLOR_CLASS_MAX_SIZE = None

# 是否按「单色类最大节点数」从小到大多档扫描（每档 COLORING_ROUNDS 轮）以增加探索多样性。
# 限制节点数只是搜索期约束，不改变目标函数：各档位候选统一比较，最终仍取全局最优
# （含不限制档）。部分机器在限制节点数时次目标（各组配方数降序）反而更优。
COLOR_CLASS_SIZE_SWEEP = True

# 逐 1 扫描的最大档位（从 2 起）；None 表示扫到 COLOR_CLASS_MAX_SIZE 或节点数
COLOR_CLASS_SIZE_SWEEP_MAX = 12

# 空催化组（编程电路0）与其它催化组的串配方检查是否在终端显示；
# 关闭（默认）时只在 Output/recipe_check.log 中以 DEBUG 级别记录，不刷屏终端。
LOG_EMPTY_CONFLICT_CONSOLE = False
