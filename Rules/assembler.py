"""组装机专属删减规则。

本规则运行在 2「删减配方」阶段：此时配方已完成清洗与等效合并
（催化组列表可能含多个），但尚未进入 2 的「删减催化剂」
（reduce_catalyst_groups），因此催化组尚未做子集拆分/抽象替换——
即这里的催化组列表即“原始配方”声明的触发条件。

删除逻辑：
  1. 输入仅为 Volumetric Flask 的配方：整条删除。
  2. 命中目标催化组的配方（见 _TARGETS 定义，例如战利品 + 编程电路17、
     远程请求器 + 编程电路18）：
     - 若该配方的全部催化组都命中目标组 → 整条删除
       （此配方仅能靠这些目标组触发）；
     - 若该配方的催化组列表中仅部分命中目标组 → 仅移除命中的组，
       保留其余组与配方（配方在其它催化组条件下仍可触发）。
  3. 催化组含编程电路 24（按 key 匹配）且输出物品全部为线缆
     （originalName 含 Cable）的配方：整条删除。
"""


# 目标催化组：{催化剂 key 集 : 电路 key 集}
# 命中条件 = 催化剂恰为某指定催化剂 + 电路恰为对应电路。
_TARGETS = {
    # 娜迦战利品 / 米诺陶战利品 / 幻影骑士战利品 + 编程电路17
    frozenset(
        {
            "TwilightForest:item.trophy:1",  # Naga Trophy 娜迦战利品
            "TwilightForest:item.trophy:5",  # Minoshroom Trophy 米诺陶战利品
            "TwilightForest:item.trophy:6",  # Knight Phantom Trophy 幻影骑士战利品
        }
    ): frozenset({"gregtech:gt.integrated_circuit:17"}),
    # 远程请求器 + 编程电路18
    frozenset({"LogisticsPipes:item.remoteOrdererItem:32767"}): frozenset(
        {"gregtech:gt.integrated_circuit:18"}
    ),
}


def _group_is_target(group):
    """催化组是否命中任一目标：催化剂恰为某目标催化剂 + 电路恰为对应电路。"""
    catalysts = group.get("catalysts", [])
    circuit = group.get("circuit") or {}
    if len(catalysts) != 1 or not circuit:
        return False
    catalyst_key = catalysts[0].get("key", "")
    circuit_key = circuit.get("key", "")
    for catalyst_keys, circuit_keys in _TARGETS.items():
        if catalyst_key in catalyst_keys and circuit_key in circuit_keys:
            return True
    return False


# 编程电路 24（按 key 匹配）
_CIRCUIT_24_KEY = "gregtech:gt.integrated_circuit:24"


def _group_uses_circuit_24(group):
    """催化组是否含编程电路 24（匹配 circuit 字段或催化剂列表中的 key）。"""
    circuit = group.get("circuit") or {}
    if circuit.get("key") == _CIRCUIT_24_KEY:
        return True
    return any(
        catalyst.get("key") == _CIRCUIT_24_KEY
        for catalyst in group.get("catalysts", [])
    )


def _outputs_are_cables(recipe):
    """输出物品非空且全部为线缆（originalName 含 Cable）。"""
    output_items = recipe.get("outputItems", [])
    if not output_items:
        return False
    return all("Cable" in item.get("originalName", "") for item in output_items)


def clean(recipes):
    """
    组装机配方删减规则。
    输入：清洗并合并后、尚未做催化组子集替换的配方列表。
    输出：删减后的配方列表。
    """
    filtered = []
    for recipe in recipes:
        input_items = recipe.get("inputItems", [])
        input_names = [item.get("originalName", "") for item in input_items]

        # 删除条件：输入仅为容量瓶 / 存储总线 / Volumetric Flask
        if set(input_names) == {"Capacity Bottle"}:
            continue
        if set(input_names) == {"Memory Bus"}:
            continue
        if set(input_names) == {"Volumetric Flask"}:
            continue

        # 删除条件：催化组含编程电路24 且输出物品全为线缆(Cable)
        if _outputs_are_cables(recipe) and any(
            _group_uses_circuit_24(group)
            for group in recipe.get("catalystGroups", [])
        ):
            continue

        catalyst_groups = recipe.get("catalystGroups", [])
        if catalyst_groups:
            hit_flags = [_group_is_target(group) for group in catalyst_groups]
            if all(hit_flags):
                # 该配方只能靠目标催化组触发：整条删除
                continue
            if any(hit_flags):
                # 部分命中：仅移除命中的目标组，保留其余组与配方
                recipe["catalystGroups"] = [
                    group
                    for group, hit in zip(catalyst_groups, hit_flags)
                    if not hit
                ]

        filtered.append(recipe)
    return filtered
