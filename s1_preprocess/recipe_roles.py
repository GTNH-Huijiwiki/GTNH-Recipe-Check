"""在发布配方前，对 NEI 辅助槽位进行分类。"""

INPUT_SOURCES = {
    'codechicken.nei.recipe.FurnaceRecipeHandler',
    'codechicken.nei.recipe.FuelRecipeHandler',
    'ganymedes01.etfuturum.compat.nei.BlastFurnaceRecipeHandler',
    'ganymedes01.etfuturum.compat.nei.SmokerRecipeHandler',
}
# 研究站的辅助槽位用于展示已写入的研究结果，因此保留在 otherItems 中。
INPUT_IDENTIFIERS = {'cropsnh.recipes.cropGeneExtractor'}
OUTPUT_IDENTIFIERS = {
    'EnderIOSagMill', 'EnderIOSoulBinder', 'thaumictinkerer.infusedseeds',
    'wgInfernalBlastfurnace', 'extratrees.lumbermill', 'bq_quest',
    'genetics.sequencer', 'kubatech.eec',
    'forestry.centrifuge',  # 林业离心机的辅助物品全部归入产物，保持原槽位顺序。
    'harvestcraftAnimalTrapDrops', 'harvestcraftFishTrapDrops', 'harvestcraftGardenDrops',
}
OUTPUT_SOURCES = {
    'alkalus.main.nei.NEI_Handler_Distillery',
}


def normalize_recipe_roles(recipe, metadata):
    """把已知的输入材料或副产物从 otherItems 中移出，并保持候选顺序。

    NEI 的 getOtherStacks 会把燃料、催化剂和副产物混在一起。这里按来源识别
    槽位角色，使查询、计算和渲染共享同一结果。无法识别的辅助槽位保持原字段
    和语义不变。
    """
    source = metadata.get('source') or ''
    identifier = metadata.get('identifier') or ''
    others = recipe.get('otherItems')
    if not others:
        return

    if source in INPUT_SOURCES or identifier in INPUT_IDENTIFIERS or source.endswith('ImbuingStationRecipeHandler'):
        recipe['inputItems'] = list(recipe.get('inputItems') or []) + others
    elif source == 'alkalus.main.nei.NEI_Handler_Oven':
        # 烤箱导出依次为可选燃料、副产物和必需的粘土罐。
        inputs = list(recipe.get('inputItems') or [])
        inputs.extend([None] * max(0, 3 - len(inputs)))
        if len(others) == 3:
            inputs[1] = others[0]
        inputs[2] = others[-1]
        recipe['inputItems'] = inputs
        recipe['outputItems'] = list(recipe.get('outputItems') or []) + [others[-2]]
    elif identifier in OUTPUT_IDENTIFIERS or source in OUTPUT_SOURCES or source.endswith('LootBagRecipeHandler'):
        recipe['outputItems'] = list(recipe.get('outputItems') or []) + others
    else:
        return
    recipe.pop('otherItems', None)
