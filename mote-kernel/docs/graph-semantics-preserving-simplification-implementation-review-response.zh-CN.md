# Graph 执行代码语义保持型简化实施方案复审回复

## 1. 回复信息

- 回复对象：[实施方案复审结论](graph-semantics-preserving-simplification-implementation-review.zh-CN.md)
- 修订正文：[语义保持型简化实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 回复日期：2026-08-19
- 状态：接受总体“不通过”裁决；接受大部分整改；4 组意见不接受或只部分接受
- 本轮范围：只修订文档，不修改 production code 或 tests

## 2. 总体回复

接受复审的总体裁决：原方案不能直接进入 Phase 1。修订正文已经：

1. 不再把 S01–S23 全部称为可直接实施项；
2. 明确 23 个历史审查 ID 与 24 个原子实施单元的区别；
3. 分为 16 个 P1 和 8 个必须单项再评审的 P2；
4. 重写 S04、S09–S11、S14、S17、S23 的目标 shape；
5. 将 producer/consumer 迁移改为同一最小变更中的原子步骤；
6. 修正相关测试基线为 398、full suite 基线为 817；
7. 显式增加 generic integrity、source discipline、dependency direction、typing-negative、coverage、build/package、monorepo pre-commit 和 `git diff --check` 门禁；
8. 为每项列出删除对象、唯一替代和允许新增上限。

以下意见不接受或只部分接受。回复只解释差异；修订正文仍保持 Draft，必须再次评审后才能编码。

## 3. 部分接受 B1：S12 的 equality/hash 与泛型问题

### 3.1 接受的部分

接受以下事实：

- `AdmittedResumeFact` 是 frozen dataclass，`resume_input_availability` 当前参与自动 equality/hash；
- 该字段是 `AdmittedResumeFact[GraphValueT]` 唯一直接承载 `GraphValueT` 的字段；
- 删除字段时不能保留 phantom generic，必须同步把 `AdmittedResumeFact` 改为非泛型，并迁移 `RecoveryTransferState`、seed、family 和 invocation 的全部注解；
- normative Node I/O implementation 冻结了当前字段，实施时必须同步修订；
- 不得用 `compare=False` 隐藏 shape 变化。

### 3.2 不接受“必然改变 seen/budget”的结论

复审从“字段参与 equality/hash”直接推导“删除会改变 `_prove_scope()` 的 seen 去重和 4096 budget”，当前代码不足以支持该必然结论：

1. `preflight_recovery()` 每次只创建一个 `_RecoveryFamily(bindings, limits, seed.admitted_actions, budget)`；
2. `_prove_scope()` 及其递归 child proof 都接收同一个 family；
3. `_transfer_state()` 给该次 proof 的每个 `RecoveryTransferState` 注入完全相同的 `family.admitted_actions`；
4. `seen` 是单次 `_prove_scope()` 的局部集合，不跨 seed 或 invocation 复用；
5. `recovery_traversal_key()` 当前只投影 action target/kind/interrupt/reason/route，本来就不包含 `resume_input_availability`。

因此，在同一个 `seen` universe 内，不存在仅因该字段不同而应被区分的两个 reachable transfer state。字段参与结构 equality 是事实，但它在当前算法中的 proof-constant 性质同样是事实。

### 3.3 修订后的处置

不接受把 S12 无条件降为 P2。修订正文保留 S12 为 P1，但增加三个硬条件：

1. 删除字段与移除无效泛型必须是同一个原子变更；
2. 先用 characterization 固定 `family.admitted_actions` 对单次 proof 恒定、traversal key 不含该字段；
3. 删除前后 reachable boundary、seen equivalence、traversal ordering 和 budget 必须一致，否则立即停止 S12。

该处置不再使用“没有显式属性读取”作为唯一 dead-field 证明，而是以 proof-local invariance 加 characterization 作为实施依据。

## 4. 部分接受 B2：拒绝第二 compiled truth，但不删除 S08

### 4.1 接受的部分

接受复审对原 S08 wording 的批评：不得在 `FrontierTransitionPlan.joins_by_source` 之外保存第二份 compile-time join index，也不得增加 cache、双写一致性或 forwarding representation。

### 4.2 不接受删除整个优化项

当前 duplication 不要求增加 stored representation 才能消除：

- routing 已有从 `joins_by_source` 生成 keyed join projection 的纯逻辑；
- snapshot guard 已经依赖 routing 的 `validate_routing_contribution`，复用同一 routing-owned pure projection 不新增反向依赖；
- projection 可在调用时即时生成，不写入 `CompiledGraph`，不缓存，也不成为第二 truth；
- routing 需要 key → `JoinEdge`，snapshot guard 只做 key membership，可消费同一个 typed mapping。

因此不接受“当前 S08 应删除”。修订后的 S08 是 P1：保持 `joins_by_source` 为唯一 compiled owner，删除两份 comprehension，只增加一个 routing-owned、即时、纯、typed 的 consumer projection。

## 5. 不接受彻底删除 S02、S15

### 5.1 S02

接受当前 `_validate_edges()` / `_validate_definition()` 不能仅按 direct/conditional/join 名称机械拆分，也接受错误优先级和遍历顺序必须由集中 owner 保持。

不接受因此永久删除 S02。用户目标同时包括减少语义繁杂和间接逻辑；若某个 nominal variant 校验能形成独立 invariant，并且提取后净分支数、局部变量数和重复判断下降，它仍可能是有效简化。

修订正文维持 S02 为 P2，并增加以下批准条件：

- validation 总入口和错误顺序不变；
- 每个 variant 至多一个窄 validator；
- 不传 context bag，不增加回调或共享 mutable validation state；
- 若净复杂度不下降则保持现状，不实施。

### 5.2 S15

接受 worklist、seen、enqueue、budget 和 branch precedence 必须留在 `_prove_scope()` 的单一 loop owner，不能分散到多个 handler。

不接受把“任何 branch extraction”都等同于分散算法 owner。terminal、active execution、settled、executable branch 若能接收窄 nominal 输入并返回 closed successor/boundary，仍可能减少主循环局部变量和嵌套分支，而不移动 queue/budget。

修订正文将 S15 从 P1 降为 P2，并规定：

- queue、seen、budget、enqueue、排序和 branch precedence 全部留在主循环；
- handler 不能持有或修改 proof context；
- 只有净分支/变量下降且 characterization 证明顺序不变时才实施；
- 单项设计未通过则保持现状。

## 6. 接受的逐项处置

以下处置已回写正文：

| 复审意见 | 回复 |
| --- | --- |
| S01 降为条件性 P2 | 接受；增加窄 phase type、无 context bag 和净复杂度下降条件 |
| S03 保留 | 接受；明确 nested key 与 callable nominal variant |
| S04 重写 | 接受；选择唯一 descriptor owner，删除重复 plan/`node_id`，不加 forwarding property |
| S05 保留 | 接受；直接读取 `graph_input_descriptor.declarations` |
| S06 保留 | 接受；删除 wrapper，`CompiledGraph` 直接拥有 transition |
| S07 降为条件性 P2 | 接受；只允许 source-specific typed constructor |
| S09 重写 | 接受；删除整个 `RoutingResolution`，projection 直接返回 command |
| S10 重写 | 接受；resolver 一次完整诊断扫描，独立短路 helper 保持行为 |
| S11 重写 | 接受；按 control → completed join → data 首次访问顺序缓存 |
| S13 保留 | 接受；机械删除未使用参数 |
| S14 重写 | 接受；只保留 `node_id + boundary`，从 equality control 投影 disposition |
| S16 降为条件性 P2 | 接受；禁止 wide union/object/callback generic validator |
| S17 重写 | 接受；同时删除 `skip_actions`、`has_pure_skip` |
| S18 保留 | 接受；各 owner 内单遍 typed counting/index |
| S19 降为条件性 P2 | 接受；nominal action-local validation 不迁移 |
| S20 保留 | 接受；窄物化函数替代临时完整 State |
| S21、S22 降为条件性 P2 | 接受；唯一 lifecycle owner 和事务差异保持 |
| S23 拆分 | 接受；S23A 删除 sentinel，S23B 合并 view 扫描 |

## 7. 编号和验证证据澄清

### 7.1 编号

复审同时要求“保留 23 个审查编号”和“把 S23 拆成两个独立 ID”，若不说明层级会产生 23/24 歧义。修订正文采用：

- 历史审查 ID：S01–S23，共 23 个；
- 原子实施单元：S01–S22、S23A、S23B，共 24 个；
- P1：16 个；P2：8 个。

这既保留历史映射，也保证 S23A/S23B 可以独立提交、测试和裁决。

### 7.2 测试基线

接受 277 错误，修订为：

- 382 个普通相关 pytest cases；
- 16 个由 `tests/architecture/test_graph_typing_fixtures.py` 驱动的 typing cases；
- 合计 398；
- full suite 817。

`tests/typing_negative/**` 是 Pyright fixture，不是另一组直接 pytest 模块；修订正文已避免双重计数，并给出完整可复现命令。

### 7.3 不采信未列命令的 85 测试证据

复审记录“额外直接相关测试集：8 个文件，85 passed”，但没有列文件或命令。该数字不进入 normative baseline。若后续需要纳入，必须补充 exact paths 和可复现命令；这不影响复审其他验证结论。

## 8. 最终结论

总体“不通过、不得进入 Phase 1”裁决已接受。B2 的唯一 compiled truth、B3 的 pure-skip 重复事实、B4 的 S23 混项、B5 的原子迁移、B6 的测试基线和 B7 的完整门禁均已实质回写。

不接受或部分接受的意见已经用更窄替代方案处理：

- S12 以 proof-local invariance + generic cleanup + characterization 取代“必然改变 seen”的断言；
- S08 以即时纯投影取代第二 compiled index，不删除真实重复；
- S02/S15 保留为未获准实施的 P2，不把尚可证明的简化方向永久删除；
- 编号和验证证据改为可追溯、可复现口径。

修订后的实施方案仍是 Draft，不授权 production 修改；必须再次评审通过后，才可逐项实施 P1。P2 必须各自完成设计证明和单项复审。
