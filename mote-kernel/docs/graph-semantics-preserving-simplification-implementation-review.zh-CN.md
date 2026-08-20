# Graph 执行代码语义保持型简化实施方案复审结论

> **评审不通过。** 本文是对
> [`graph-semantics-preserving-simplification-implementation.zh-CN.md`](graph-semantics-preserving-simplification-implementation.zh-CN.md)
> 当前工作树版本的复审记录，不是 Graph 行为规范，也不授权修改 production 代码。

## 1. 评审信息

- 评审日期：2026-08-19
- 代码基线：`feat/kernel-graph-node-io-contract@7944159`
- 评审对象：当前工作树中的语义保持型简化实施方案
- 结论：**不通过；不得进入 Phase 1**
- 复审原则：零增复杂度、唯一事实源、复用既有 owner、完整门禁、泛型关系不擦除、导包位于连续模块头、变量不保存可推导事实、逻辑保持最短闭环

## 2. 总结论

现稿把 23 个审查编号全部称为“可直接实施”的简化项，这个结论不成立。逐项复核后的处置为：

| 处置 | 数量 | ID |
| --- | ---: | --- |
| 保留 | 6 | S03、S05、S06、S13、S18、S20 |
| 重写后再审 | 6 | S04、S09、S10、S11、S14、S17 |
| 降为条件性 P2 | 7 | S01、S07、S12、S16、S19、S21、S22 |
| 删除当前提案 | 3 | S02、S08、S15 |
| 拆成两个独立改造 | 1 | S23 |

因此，S01–S23 只能继续作为审查账本编号，不能再等同于 23 个已获准直接实施的改造单元。尤其是当前 Phase 1 同时包含 S07、S09–S12、S17 等尚未闭合的设计；在以下阻断项修正并重新评审前，不能以“本文不授权修改代码”之外的措辞暗示已经具备实施条件。

## 3. 阻断问题

### B1. S12 会改变 recovery 等价关系，并制造悬空泛型

[`AdmittedResumeFact`](../src/mote_kernel/execution/engine/recovery.py#L263) 是 frozen dataclass，
`resume_input_availability` 当前参与自动生成的 `__eq__` 与 `__hash__`。该 fact 又嵌入
[`RecoveryTransferState.admitted_actions`](../src/mote_kernel/execution/engine/recovery.py#L273)，而
`RecoveryTransferState` 被放入 `_prove_scope()` 的 `seen` 集合。因此，“生产逻辑从未读取该字段”并不等于字段未被消费；结构等价、去重和遍历预算本身就是可观察的 recovery 语义。

此外，`resume_input_availability` 是 `AdmittedResumeFact` 唯一承载 `GraphValueT` 的字段。只删除字段却保留 `Generic[GraphValueT]`，会留下没有类型关系的 phantom generic；保留泛型不满足严格泛型原则，直接移除泛型又需要同步迁移整条类型链。规范文档也明确冻结了该字段：
[`graph-node-input-output-contract-implementation.zh-CN.md`](graph-node-input-output-contract-implementation.zh-CN.md#L496)。

必须修正为：

1. S12 降为 P2，不进入首轮 dead-field 删除；
2. 先证明删除前后 recovery reachable boundary、seen equality、排序键和 4096 状态预算完全一致；
3. 若最终删除，必须同时移除 `AdmittedResumeFact` 的无效泛型参数并迁移所有使用点；
4. 禁止用 `compare=False` 隐藏差异，也不能只改 exact-shape 测试来迎合新 shape。

### B2. S08 会在 `joins_by_source` 之外增加第二份 compiled join 真相

现有 canonical join owner 是
[`FrontierTransitionPlan.joins_by_source`](../src/mote_kernel/execution/graph/topology.py#L55)。
[`routing._declared_joins()`](../src/mote_kernel/execution/engine/routing.py#L97) 与
[`snapshot_guard`](../src/mote_kernel/execution/engine/snapshot_guard.py#L48)
只是从该 owner 构造消费侧索引。现稿提议再保存一个 compile-time canonical join index，却没有删除或替换原 owner，也没有定义两个 shape 的原子一致性不变量。

这不是简化，而是把瞬时投影升级为可漂移状态。当前 S08 应删除。若以后有性能证据，方案必须选择一个唯一 compiled representation，并在同一改造中迁移全部 consumer、删除旧 representation；不能同时保存 `joins_by_source` 和第二索引。

### B3. S17 只删 `skip_actions`，却留下更危险的 `has_pure_skip`

[`ScopedResumeCandidate`](../src/mote_kernel/execution/engine/resume_admission.py#L30)
同时保存 `skip_actions`、`has_pure_skip` 和 `command`。当前 admission 至少会校验
[`skip_actions` 与 command 一致](../src/mote_kernel/execution/engine/resume_admission.py#L53)，但
[`has_pure_skip`](../src/mote_kernel/execution/engine/resume_admission.py#L126)
直接来自 request 侧布尔值，未与 command/substitution 做同等级一致性验证。现稿仅删除已校验字段，反而保留可漂移布尔真相。

S17 必须重写为：candidate 只保存 canonical `command` 与 admitted `substitutions`；skip actions 从 `command.actions` 的 nominal variant 推导，pure-skip 从 skip action 是否缺少对应 replacement substitution 推导。不得保存 `has_pure_skip` 布尔缓存，也不得通过字符串 kind、额外 set 或第二 action 列表建立新真相。

### B4. S23 把一个可直接删除的 sentinel 与另一项扫描优化混在一起

[`_AdvancedFrontier`](../src/mote_kernel/execution/family_driver.py#L93)
没有数据或独立不变量。在 [`drive_root()`](../src/mote_kernel/execution/family_driver.py#L448)
中，返回 `_AdvancedFrontier` 和返回 `None` 最终都会进入下一轮循环，因此不需要再引入一个“自描述 disposition”；直接删除 class、对应 union 成员和分支即可。

另一方面，
[`_failure_views()`](../src/mote_kernel/execution/family_driver.py#L473) 与
[`_interrupt_views()`](../src/mote_kernel/execution/family_driver.py#L482)
的重复 scoped-state 扫描是独立问题，应另设改造 ID，以一次有序扫描同时产出两个 tuple，并保持 scope、frontier 和各自结果的原顺序。

S23 必须拆分，不能用一个提交、一个测试结论同时覆盖控制流 sentinel 删除和 result projection 扫描合并。

### B5. Phase 2 的 producer/consumer 迁移顺序不可执行

现稿 Phase 2 要求先“删除重复字段或 wrapper”，再更新文档/测试，最后迁移 runtime/recovery consumers。删除 producer 字段后 consumer 尚未迁移，代码会在阶段中间失去类型闭合或直接不可运行，也不满足每个提交均可过门禁的要求。

每个 compiled-shape 改造必须按以下原子顺序独立完成：

1. 冻结当前行为、错误优先级、顺序与 exact shape；
2. 明确唯一 canonical owner 和目标类型；
3. 在同一最小变更中迁移全部 consumer，并删除重复 producer 字段；
4. 同步 normative 文档和 architecture exact-shape 测试；
5. 运行完整门禁后再开始下一项。

不得增加 forwarding property、兼容 alias 或临时双写 shape 来跨阶段过渡。

### B6. 验收矩阵的 277 基线计数错误

按第 7 节实际列出的路径运行，明确文件收集并通过 382 个测试；`tests/typing_negative/**` 另有 16 个测试，总数为 **398 passed**，不是 277。错误基线会让后续误删 121 个测试仍看似“达到基线”。

文档必须改为可复现的命令和当前真实计数，并规定测试数减少必须解释，不能把固定数字本身作为唯一验收条件。完整 suite 当前为 817 passed，应继续作为总回归基线。

### B7. 泛型、导包和依赖方向门禁没有被显式列为不可替代项

“pyright/typing-negative 和 architecture owner checks”过于宽泛。当前候选包含 generic index、private helper 提取、模块拆分与 import 调整，必须显式冻结：

- [`test_generic_integrity.py`](../tests/architecture/test_generic_integrity.py#L23)：禁止 `object` 边界、所有 bare generic 和 generic-erasing cast；
- [`test_source_discipline.py`](../tests/architecture/test_source_discipline.py#L106)：所有 import 构成连续模块头，同时禁止内部 `Any`、动态导入和反射逃生口；
- [`test_dependency_direction.py`](../tests/architecture/test_dependency_direction.py#L70)：保持 state、execution、graph definition 与 domain 的依赖方向。

`make check` 当前会通过 full pytest 间接覆盖这些测试，但实施方案仍需把它们列成不得用局部测试或模糊的 “owner checks” 替代的显式门禁。

## 4. S01–S23 逐项处置

| ID | 处置 | 复审要求 |
| --- | --- | --- |
| S01 | 条件性 P2 | 不能按函数行数决定拆分。只有 phase 输入/输出已经是窄 nominal type、没有 context bag/额外中间状态，且净分支和变量数下降时才实施；compile 顺序仍由一个 owner 编排。 |
| S02 | 删除当前提案 | `_validate_edges()` 与 `_validate_definition()` 集中拥有错误优先级。单纯拆成分派函数增加跳转，不删除事实或扫描；保留集中校验，只在出现可独立复用的不变量时另提窄函数。 |
| S03 | 保留 | 删除两个分类 tuple；nested 真相严格取 `nested_graphs` 的 key，callable 真相按 `nodes` 中的 nominal callable-node variant 判断，不能用“非 nested 即 callable”的补集推断。 |
| S04 | 重写 | 指定且只保留一个 node-output descriptor owner。建议以 `publications[node_id].descriptor` 为真相，outputs 取 `descriptor.declarations`，routes 取 `conditional_targets[node_id]`；删除 keyed value 中重复 `node_id` 及不再有独立行为的 plan，不加 forwarding property。 |
| S05 | 保留 | 删除 `CompiledGraph.graph_inputs`；所有 consumer 直接读取 `graph_input_descriptor.declarations`。不得保留同名 property 作为兼容转发。 |
| S06 | 保留 | `RecoveryAvailabilityPlan` 当前只包一层 `transition`，应无条件删除；`CompiledGraph` 直接拥有 transition，不以新 wrapper 或 alias 替代。 |
| S07 | 条件性 P2 | 坐标构造只能复用既有 typed coordinate/descriptor 基础设计。GraphInput、NodeOutput、resume 等 nominal source 继续分开；禁止跨 owner 的 `object`/wide union/generic helper，也不能把调用边界的错误类型藏进 helper。 |
| S08 | 删除当前提案 | 不增加 compile-time join index。若未来替换 canonical representation，必须迁移所有 consumer 并删除 `joins_by_source`，不能双存。 |
| S09 | 重写 | 优先删除整个 `RoutingResolution` wrapper，让 projection 直接返回 `ResolutionCommand`。诊断继续由 `RoutingFacts` 拥有；recovery 读取 facts，不能为测试保留 production 不消费的镜像字段。 |
| S10 | 重写 | 在 `resolve_routing_facts()` 内由一次完整诊断扫描同时得到 available、history 和 unavailable identity；独立 `graph_outputs_available()` 的首次缺失短路行为必须保留，避免改变 lookup 次数和错误时序。 |
| S11 | 重写 | 对每个 target 只做一次 typed binding scan，同时产生 available、missing、historical-gap 和 display identity；结果按现有 control → completed join → data 的首次访问顺序缓存，不能用无序 set 改变首错。 |
| S12 | 条件性 P2 | 先完成 equality/hash、reachable boundary、budget 与泛型传播证明；不得作为 dead field 直接删除，详见 B1。 |
| S13 | 保留 | 删除 `_initial_children()` 未使用参数及实参，保持函数导包、局部变量和调用顺序不变；这是纯机械删除。 |
| S14 | 重写 | `_NestedOutcome` 只保留 `node_id + boundary`。kind/availability 从 boundary 取得；child disposition 从 equality-participating 的 `boundary.control` typed 投影，不能从 `compare=False` 的 `boundary.state` 重建等价事实。 |
| S15 | 删除当前提案 | `_prove_scope()` 的 worklist、seen、budget、排序和 branch precedence 是一个算法闭环。按 branch handler 拆分不会删除状态，反而分散不变量；保留单一循环 owner。 |
| S16 | 条件性 P2 | 四类 segment 可各有窄 nominal validator，但不能抽成接受 wide union、`object` 或回调的通用 validator；先证明提取后错误优先级和遍历顺序不变。 |
| S17 | 重写 | 同时删除 `skip_actions` 与 `has_pure_skip`；两者均从 command 加 substitutions 推导并在一个 owner 内使用，详见 B3。 |
| S18 | 保留 | 在 `plan_resumes()` 与 `admit_resume_candidates()` 各自 owner 内使用 typed 单遍 counting/index；保持 canonical 输入顺序和首次重复/collision identity，不抽跨 owner generic helper。 |
| S19 | 条件性 P2 | action-local validation 仍由 failed/interrupt/skip nominal 分支拥有；仅提取确实相同的 encode/decode/frame admission，不能用一个宽 adapter 重排首错或模拟步骤。 |
| S20 | 保留 | 在 `resume_input.py` 提取窄 typed materialization 函数，删除临时完整 State；必须保留 descriptor、codec/scope、resume-input 优先级、原错误分类以及 simulated frontier validation。 |
| S21 | 条件性 P2 | `Graph.run()` 继续是唯一生命周期编排 owner。private path 只能返回窄 typed 结果，不能形成第二 runner、隐藏 mutable context 或重复 new/resume 事务流程；三个 overload 原样保留。 |
| S22 | 条件性 P2 | 只有 helper 无 `Any` 且能保留全部事务差异时才合并：首个 commit 原异常直抛、已有 confirmed prefix 才包装 partial handoff、frame 在替换内存 State 前预计算、成功安装时 State 先于 frames。 |
| S23 | 拆分 | A：直接删除 `_AdvancedFrontier`，不新增 disposition。B：单独合并 failure/interrupt view 扫描并分别验证稳定顺序。 |

## 5. 实施方案必须重写的部分

下一版至少完成以下修改后才能再次申请实施评审：

1. 将“23 项直接候选”改为“23 个审查编号”，按本评审处置重新计数；
2. 从直接实施范围删除 S02、S08、S15，并把 S23 拆成两个独立 ID；
3. 将 S12 移出 Phase 1；S07、S16、S19、S21、S22 统一降为有证明前置条件的 P2；
4. 按 B1–B4 重写 S04、S09–S12、S14、S17、S23 的目标 shape 和唯一 owner；
5. 重排 Phase 2，禁止 producer 先删、consumer 后迁移以及任何临时兼容层；
6. 把每项改造约束为一个最小、始终可 typecheck/test 的提交，不按大 Phase 批量删除 shape；
7. 修正 277 测试基线为实际 398，并补充 full-suite 基线；
8. 显式加入 generic integrity、source discipline、dependency direction、typing-negative、coverage、build/package、monorepo pre-commit 与 `git diff --check` 门禁；
9. 对每项列出“删除的字段/变量/扫描/分支”与“新增的字段/变量/helper/导包”。若新增认知面不小于删除面，不应归类为简化；
10. 保持 Node I/O normative 文档是 frozen internal shape 的唯一事实源；本方案只引用并同步它，不复制一套可漂移的完整类型清单。

## 6. 验证证据

本次复审基于当前代码执行结果：

| 检查 | 结果 |
| --- | --- |
| full pytest | `817 passed in 59.93s` |
| 文档第 7 节列出的测试文件 | `382 passed` |
| `tests/typing_negative/**` | `16 passed` |
| 第 7 节实际合计 | `398 passed`，与文档的 277 不一致 |
| 额外直接相关测试集 | 8 个文件，`85 passed` |
| Pyright strict | `0 errors` |
| Ruff lint | 通过 |
| Ruff format check | `150 files already formatted` |
| `git diff --check` | 通过 |

以下检查本次没有运行，不能在本文中记为已通过：

- 完整 `make check` wrapper；
- coverage run；
- build 与 twine package check；
- monorepo root 的 pre-commit checks。

本次任务是文档复审，没有修改 production 代码；为避免生成 `dist/` 等工作区产物，上述发布型检查留给实施方案修订或实际代码变更时执行。

## 7. 最终裁决

当前方案方向上识别到了一批真实重复，但把“字段没有显式属性读取”“函数较长”“扫描出现两次”过早等同于可直接简化，尚未守住 structural equality、唯一 compiled truth、事务错误时序与严格泛型边界。

**最终裁决：不通过，不授权 Phase 1。** 先按第 5 节重写方案，再逐项复审；在此之前不得开始 S01–S23 的 production 实施。
