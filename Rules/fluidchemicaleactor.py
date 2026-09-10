"""流体化学反应器专属规则。

该机器的反应条件在设计上只由编程电路区分，配方里的催化剂（nc 催化剂项）
不参与区分。这里把全部催化组中的催化剂部分置空，只保留编程电路，使后续的
「催化组删减」（reduce_catalyst_groups）、冲突图与着色都基于归一后的组。

本规则运行在 2「删减配方」阶段，且早于 reduce_catalyst_groups。
注意：原本「带催化剂但无编程电路」的组，置空后将等价于「无条件反应」空组。
"""


def clean(recipes):
    """将每个催化组的 catalysts 置空，仅保留 circuit。"""
    for recipe in recipes:
        for group in recipe.get("catalystGroups", []):
            group["catalysts"] = []
    return recipes
