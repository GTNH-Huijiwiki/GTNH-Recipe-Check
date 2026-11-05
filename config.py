"""集中配置：数据目录、兴趣机器、输出路径与着色参数。"""

from pathlib import Path

# GTNH Dumper 导出的原始数据目录（第一步处理器的输入）
DUMPER_DIR = Path("./Raw/2.9.0")

# 第一步处理器的输出目录，即本仓库其它模块的数据根目录
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
    "blastfurnace",
}

# 输出根目录；每个数据版本单独一个子目录
OUTPUT_ROOT = Path("Output")
OUTPUT_VERSION = DATA_SPLIT_DIR.name
VERSIONED_OUTPUT_ROOT = OUTPUT_ROOT / OUTPUT_VERSION

# 统一运行日志
LOG_FILE = VERSIONED_OUTPUT_ROOT / "recipe_check.log"

# 规范化阶段产物目录
NORMALIZED_RECIPES_DIR = VERSIONED_OUTPUT_ROOT / "normalized_recipes"

# 机器规则筛选后的配方目录
SELECTED_RECIPES_DIR = VERSIONED_OUTPUT_ROOT / "selected_recipes"

# 催化集关系与最小覆盖产物目录
CATALYST_PICK_DIR = VERSIONED_OUTPUT_ROOT / "catalyst_pick"

# 无向冲突图产物目录
CONFLICT_GRAPH_DIR = VERSIONED_OUTPUT_ROOT / "conflict_graph"

# 最终输入组方案目录
INPUT_GROUPS_DIR = VERSIONED_OUTPUT_ROOT / "input_groups"

# 冲突图构建模式
CONFLICT_MODE = "blocking"

# 每个催化集对最多保留的冲突配方对数
CONFLICT_MAX_PAIRS = 100

# 孤儿标记偏好：contains | only（两种 flag 始终记录，仅用于下游选择）
GROUP_ORPHAN_MODE = "contains"

# 图着色策略
COLORING_STRATEGY = "dsatur_multistart"

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
