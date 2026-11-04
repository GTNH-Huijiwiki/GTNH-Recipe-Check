# 数据结构约定

本文件是 s0-s6 的阶段边界约定。阶段之间通过版本化产物和哈希 manifest 交流，不依赖运行时对象或全局状态。

## 1. 阶段与目录

~~~text
s0_contracts   公共结构和工具
s1_preprocess  Raw -> Data
s2_normalize   Data -> NormalizedDataset
s3_rules       机器规则过滤
s4_cover       独特催化集关系与最小覆盖
s5_conflict    无向冲突矩阵
s6_color       图染色
s9_validation  本地测试、实验和 profiling，不进入 Git
~~~

所有正式阶段产物位于：

~~~text
Output/<version>/
~~~

当前 s2 正式产物：

~~~text
Output/<version>/normalized/
├── manifest.json
└── machines/
    └── <machine>/
        ├── recipes.json
        └── catalyst_sets.json
~~~

## 2. 基础实体

### MaterialRef

~~~json
{
  "key": "minecraft:gunpowder",
  "kind": "item",
  "amount": 1,
  "originalName": "Gunpowder",
  "translatedName": "火药"
}
~~~

- 身份使用 `(kind, key)`；
- `originalName` 用于规则匹配；
- `translatedName` 用于展示；
- `amount` 必须保留；
- `originalName`、`translatedName` 在 s2 中移除 Minecraft 格式码；
- 名称和数量不参与物料身份判断。

### InputSlot

一个输入槽内部取 OR：

~~~text
material_a OR material_b OR material_c
~~~

固定输入是只包含一个候选项的 `InputSlot`。

### InputGroup

一个配方的全部输入槽取 AND：

~~~text
slot_1 AND slot_2 AND ... AND slot_n
~~~

### CatalystSet

一个催化条件：

~~~json
{
  "setId": "catalyst_set:...",
  "kind": "normal",
  "catalysts": [],
  "circuit": null
}
~~~

语义：

~~~text
catalyst_a AND catalyst_b AND ... AND circuit
~~~

- 同一催化集内部取 AND；
- 最多一个电路；
- 普通催化集不能同时缺少催化剂和电路；
- `setId` 由 `kind`、排序后的催化剂身份和电路身份稳定生成；
- 覆盖关系使用催化集身份、电路和类型，不使用显示名称。

### CatalystGroup

一个配方的多个催化集取 OR：

~~~text
catalyst_set_1 OR catalyst_set_2 OR ...
~~~

- 一个配方允许 0、1 或更多个催化集；
- `sets: []` 表示无条件配方；
- s2 不生成 `unconditional` 占位集合；
- 无条件集合在规则过滤与覆盖阶段实体化。

### OutputGroup

~~~json
{
  "items": [],
  "fluids": []
}
~~~

输出组保存全部产物，数量必须保留。

## 3. Recipe 序列化结构

~~~json
{
  "recipeId": "assembler_recipe_0000",
  "machine": "assembler",
  "inputGroup": {
    "slots": [
      {
        "alternatives": []
      }
    ]
  },
  "catalystGroup": {
    "sets": []
  },
  "outputGroup": {
    "items": [],
    "fluids": []
  },
  "orphan": {
    "hasOrphanInput": false,
    "onlyOrphanInputs": false,
    "orphanItems": [],
    "orphanFluids": [],
    "mode": "contains"
  },
  "provenance": {
    "sourceFile": "Recipes/gregtech/GTNEIDefaultHandler_assembler.json",
    "source": "gregtech.nei.GTNEIDefaultHandler",
    "identifier": "gt.recipe.assembler"
  },
  "diagnostics": []
}
~~~

约定：

- `recipeId` 在 s2 中按机器和稳定顺序分配；
- `diagnostics` 只保存需要随配方传递的诊断；
- 全局处理信息、警告和异常只进入 `recipe_check.log`；
- 不再生成独立的 `diagnostics.json`；
- 不再生成 `materials.json`；
- 不再生成 `index.json`。

## 4. CatalystSet 注册表

每台机器输出：

~~~text
machines/<machine>/catalyst_sets.json
~~~

~~~json
{
  "machine": "assembler",
  "sets": []
}
~~~

注册表保存该机器 recipes 中出现的全部唯一 `CatalystSet`。下游通过 `setId` 引用和验证催化集。

## 5. manifest

`manifest.json` 只保存哈希：

~~~json
{
  "upstream": {
    "items": "sha256:...",
    "oreDictionary": "sha256:...",
    "recipes": "sha256:..."
  },
  "artifacts": {
    "machines/assembler/recipes.json": "sha256:...",
    "machines/assembler/catalyst_sets.json": "sha256:..."
  },
  "outputHash": "sha256:..."
}
~~~

约定：

- `upstream` 只包含本阶段实际消费的输入；
- 文件哈希按相对路径排序后聚合；
- `outputHash` 是正式产物路径与文件哈希的聚合值；
- 不记录时间戳、统计数字、运行状态等非必要信息；
- 下游阶段使用该 manifest 判断上游是否变化。

## 6. 阶段职责

### s1_preprocess

~~~text
Raw -> Data
~~~

输出：

~~~text
Data/<version>/
├── Items.json
├── OreDictionary.json
├── Recipes/
├── nbt_regress_cache.json
├── association_report.json
└── nbt_missing.csv
~~~

s1 严格对齐 Wiki Toolchain 数据变换，不负责本项目展示名称清理。

### s2_normalize

~~~text
Data -> Output/<version>/normalized
~~~

职责：

- 清理展示名称中的格式码；
- 展开输入候选、矿辞和通配；
- 拆分普通输入、nc 催化剂和电路；
- 构建 InputGroup、CatalystGroup 和 CatalystSet；
- 建立孤儿标记；
- 合并等价配方；
- 分配 recipeId；
- 生成催化剂集注册表；
- 生成输入与产物哈希 manifest。

### s3_rules

输入：

~~~text
Output/<version>/normalized/
~~~

职责：

- 在分层 Recipe 上应用机器规则；
- 删除或裁剪配方；
- 输出规则过滤后的配方和事件日志。

### s4_cover

输入：

~~~text
规则过滤后的 Recipe / CatalystSet
~~~

职责：

- 选取独特 `CatalystSet`；
- 计算覆盖关系；
- 实体化无条件催化集；
- 完成最小催化集覆盖。

覆盖定义：

~~~text
S.circuit == T.circuit
并且
T.catalysts ⊆ S.catalysts
~~~

### s5_conflict

输入：

~~~text
已选出的独特 CatalystSet
~~~

职责：

- 构建无向冲突图；
- 输出稀疏边、上三角矩阵或 dense bitset；
- 附带冲突 witness；
- 节点不再使用虚拟催化组。

### s6_color

输入：

~~~text
ConflictModel + CatalystSet 注册表
~~~

职责：

- 对冲突图进行多轮着色；
- 一个颜色类表示多个 CatalystSet 的 OR；
- 输出最终 CatalystGroup 和着色方案。

## 7. 日志约定

统一日志：

~~~text
Output/<version>/recipe_check.log
~~~

- 终端只显示具体进度摘要和错误摘要；
- DEBUG、WARNING 和完整异常堆栈写入日志；
- 异常同时输出终端摘要；
- 不允许业务代码使用 `print`。
