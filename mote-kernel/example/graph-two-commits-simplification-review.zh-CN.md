# Graph 最近两个提交的冗余与简化空间审查

> 核对状态：2026-08-18 已完成第五轮逐项核对。已完整读取 34 个改动文件的 diff；对 13 个生产文件逐行核对当前实现与两提交差异；完整读取 7 份文档；逐项核对 14 个测试文件的新增/修改测试及其覆盖目标。第三至第五轮又对照 `Graph.run()` 当前 overload、实现分支、调用测试、既有 facade 设计文档和“禁止隐藏可变状态”约束，明确区分“当前事实”与“目标语义”。下文以“确认”“条件性”“不建议”区分结论强度。

## 1. 审查范围

本报告逐文件审查以下两个提交：

- `8980e6f refactor(kernel): split graph facade orchestration`
- `7944159 feat(kernel): support outputs for skipped failures`

审查基线为 `HEAD~2..HEAD`。两个提交共改动 34 个文件：13 个生产代码路径（含删除的 `_graph.py`）、14 个测试路径、7 份设计与评审文档。diff 总计新增 7,135 行、删除 1,871 行，净新增 5,264 行。

判断“冗余”的标准不是代码行数，而是：

1. 类型是否拥有独立不变量；
2. 一层是否只把相同字段原样传给下一层；
3. 同一事实是否被多个结构重复保存并再次相互校验；
4. 同一遍历或坐标构造是否在多个函数重复实现；
5. 内部严谨模型是否不必要地泄漏到公共 API。

## 2. 总体结论

两个提交的主要方向是正确的：

- 将 1,643 行的 `_graph.py` 拆为 `facade.py`、`invocation.py` 和 `family_driver.py`，改善了所有权边界；
- skip substitution 在 commit 前完成 admission、commit 后才安装 publication，遵守“持久状态先确认，再替换内存快照”的原则；
- routing facts 被抽为共享事实，减少 recovery 自己解释拓扑的风险；
- partial commit 显式交接已确认前缀，避免悄悄丢失 durable progress。

但目前存在多类明显简化空间：

### 高优先级

1. **公共恢复 API 暴露过多内部概念**：调用者必须同时理解 `state`、`continuation`、`ResumeAction`、scope、node ID 和 interrupt ID。内部模型必要，但常规调用不应手工拼装。
2. **一次 resume plan 被重复表达**：`PreparedResume`、`_PlannedResume`、`ScopedResumeCandidate` 同时保存 command、successor、substitutions 和 action 派生信息，随后又互相校验。这是逐行核对后最确定的内部冗余。
3. **skip substitution 的阶段类型可以收敛，但不能粗暴合并**：`PreparedSubstitution -> AdmittedSubstitution -> ConfirmedPublication` 分别对应 descriptor admission、whole-invocation admission、exact commit 后确认，阶段本身有价值；冗余在于它们又被 `_PlannedResume` 和 `ScopedResumeCandidate` 平行包装、重复解释。
4. **`Graph.run()` 作为唯一公共事务入口是明确要求，不是冗余**。当前 `run()` 虽是唯一方法，但仍暴露 `state + continuation + resume`。目标仍只有一个 `run()`，但通过职责明确的关键字 `inputs`、`answers`、`skips` 表达三类意图，不能让同一个 `values` 参数根据 checkpoint 状态变换含义；不得增加第二个 public runner 或 `resume()` 执行入口。
5. **interrupt 继续执行与 skip 处置都进入同一个 `run()`，但参数必须分开**：`answers` 是 interrupt 回答列表；`skips` 是失败节点处置列表。图级失败重试不受支持，因此应删除 failed-retry variant：`resume_failed()`、`resume_failed_with()`、`ResumeFailedNodeRequest`、`ResumeFailedNode` 及其 executor/invocation/reducer/recovery 分支。共享的 `ResumeGraphNodes`、resume planning/admission/recovery 仍保留，并收窄为 interrupt answer 与 skip 两种 action。`resume_interrupted()` 与 `skip_failed()` factory 则由明确的 `answers=`/`skips=` 输入替代。
6. **不能用隐藏 registry 实现自动恢复**：`Graph` 当前只缓存 immutable compiled owner；若再偷偷保存运行 checkpoint，会把 facade 变成带隐藏 mutable session 的对象，违反架构约束。状态来源必须是显式装配的窄 typed checkpoint port；“对外只有一个 `run()`”不等于依赖也必须不可见。

### 中优先级

7. `ScopedFrameIndex` 四组 `has/lookup/add` 方法结构重复，可用内部通用原语消除机械代码，同时保留强类型 overload。
8. routing 对相同 binding 多次构造 availability coordinate，且 `graph_outputs_available()` 与 `unavailable_graph_outputs()` 重复遍历。
9. `result.py` 同时承载 task result、prepare disposition、child projection、resume plan、public result 和 partial-commit error，文件职责过宽；这是模块归属问题，不代表这些 nominal result 可以合并。
10. 测试大量锁定私有类型的精确字段和模块位置，增加安全重构成本；部分 tamper 测试可下沉到 owner 单元测试。

### 不建议简化

- 不合并 node `Outcome`、commit result 和 graph `Result`：三者位于不同边界。
- 不把 caller resume intent 直接等同于 reducer command：前者需 admission，后者是权威状态转换。
- 不取消 commit 前 admission / commit 后 installation 两阶段：这是 durable-first 的核心。
- 不把 concrete frames 塞入 `GraphRunState`：执行位置与具体业务值应继续分离。
- 不删除 partial-commit handoff：多 scope 顺序确认时它是必要语义。

## 3. 生产代码逐文件审查

### 3.1 `src/mote_kernel/execution/__init__.py`

改动仅将 `Graph` 的来源从 `_graph.py` 切到 `facade.py`。

结论：**无冗余**。这是唯一公共 re-export，符合架构约束。

建议：保持只导出 `Graph`，不要为简化调用再平行导出 executor、request 或 reducer command。恢复也只能由 `Graph.run()` 在内部决定，不能挂到 result 上形成另一执行入口。

### 3.2 `src/mote_kernel/execution/_graph.py`（删除）

原文件同时拥有 builder、invocation planning、family driving、commit 和 result projection，共 1,643 行。删除并拆分是合理的。

结论：**删除正确，不应恢复**。

注意：`facade.py`、`family_driver.py`、`invocation.py` 当前合计 1,914 行，较原文件 1,643 行增加 271 行。但第二个提交随后在这些文件继续加入 skip-output 与 partial-commit 逻辑，因此不能把 271 行全部归因于拆分开销。拆分提交本身主要是代码搬迁和公开 `_GraphTransition -> GraphTransition`，方向正确。

### 3.3 `src/mote_kernel/execution/facade.py`

合理部分：

- builder transaction、compile cache 和公共 factory 都属于 facade；
- `_GraphBuilderState`、`_NestedNodeCandidate`、`_CompiledOwner` 各有明确不变量；
- `Graph.values/success/failure/interrupt` 保持唯一公共命名空间。

冗余与简化空间：

1. **`Graph` 类属性很多，但不能按数量判定冗余**。既有 facade 契约明确要求只导出 `Graph`，因此 `Transition`、三类 commit result、result variants 和精确 error aliases 是 commit callback、严格 narrowing 与异常捕获所需的 namespaced public contract。自动恢复落地后，真正可退出常规调用面的主要是 `State`、`Continuation`、`ResumeAction` 及显式恢复 overload；其余 alias 是否保留需按使用场景逐项判断。
2. **`_ResumeCodec` 是 adapter，不是已确认冗余**：它把两个 callable 适配成 `ResumeInputEncoder`/`ResumeInputDecoder` protocol，并让同一 frozen 对象同时承担两种能力。方法本身虽只转发，但直接删除会迫使 `ResumeInputBinding` 改为保存 callable 或增加两个 adapter；收益很低，暂不建议动。
3. **`run()` 应覆盖完整生命周期**：这是目标职责，但当前实现尚未完整达到。现有代码以 `values` 启动新运行，以显式 `state + continuation + resume` 恢复；公开的 `run_id` 只用于新建 `GraphRunId`，没有据此加载已有状态。目标 API 不应再要求用户传 `run_id`：`run()` 应以内部 compiled graph identity 定位 checkpoint。用户既不保存运行 ID，也不接触内部 `CompiledGraph`。
4. **fence 和 resume commit 循环重复**：两者都有 `current -> commit_transition -> catch -> partial error -> replace -> confirmed_prefix`。应抽为 invocation transaction，但不能用泛化 `Any` helper。
5. **公共恢复参数过底层**：`state + continuation + resume` 总是协作，且调用者必须从上一次 result 拆出来再传回。
6. `continuation is None + multi-scope substitution` 的限制写在 facade，属于当前显式 state-only 恢复模式的 invocation capability。自动 checkpoint 加载落地后，常规路径不会缺失 continuation；兼容/跨进程 state-only 路径若仍保留，该限制才需要下移。

建议：

- 保持 `Graph.run()` 为唯一公共入口；如需降低方法体复杂度，只抽取 private planning/commit 函数，不产生第二执行 owner；
- `inputs=` 明确要求启动新运行；`answers=` 明确回答 interrupt；`skips=` 明确处置 failed 节点。`run()` 仍先加载 checkpoint 以验证意图与当前状态一致，但不能再根据状态重新解释参数含义；
- 普通调用不应手工拆传 `state + continuation + resume action`，这些应由 `run()` 和其内部运行状态加载机制处理；
- `answers` 与 `skips` 都使用列表嵌套字典；每项携带完整节点路径。即使只有一个 pending interrupt/failed node，也不依靠隐式选择；图级 failed-node retry 不进入目标 API；
- 当前 `commit` 是 transition 确认端口，不是可加载状态的 store。要实现自动选择，需要一个显式、窄 typed checkpoint port；默认实现可以只是同进程内存存储，跨进程时换成持久化实现。它只保存/加载运行上下文，不参与执行，也不能成为 `Graph` 上未声明的隐藏 registry；
- `GraphTransition` 和 commit results 可继续用于高级持久化，但从常规文档路径移除；
- 不增加第二个 runner、public `Graph.resume()` 或 result-owned 执行路径。

优先级：**高**。

### 3.4 `src/mote_kernel/execution/family_driver.py`

合理部分：

- `commit_transition()` 集中执行 reducer、暴露 candidate、确认 exact successor，是关键边界；
- nested graph 统一由 family driver 驱动，避免私有 runner；
- result projection 从 facade 移出是正确所有权调整。

简化空间：

1. `_AdvancedFrontier` 是无字段 marker，仅用于让 `drive_root()` 区分“继续循环”和 `None`。可让 `_advance_scope_quantum()` 返回一个内部 enum/closed disposition，或直接由 driver 内部循环吸收 advance；当前 marker 概念价值较低。
2. `_advance_scope_quantum()` 返回 `GraphBoundary | _AdvancedFrontier | None`，其中 `None` 同时表示“执行过节点”“启动/驱动完 child”“resolve 后非 advance”，语义不够自描述。虽然少一个类型，但增加理解成本。
3. `_child_projections()` 与 `_drive_children()` 在循环中反复重建全部 projection；可返回按 coordinate 索引的内部 plan，减少扫描，但当前规模下不是首要性能问题。
4. `_failure_views()` 和 `_interrupt_views()` 各自扫描全部 scoped state，可合并为一次 result-view projection。
5. `GraphTransition` seal、三个 `_Graph*Result` seal 和 graph result seal 是重复的 capability 模式；可抽取内部统一 construction capability，但不要通过反射或通用无类型工厂实现。

建议：定义一个明确的内部 `DriveDisposition`，去掉 `None` 和空 marker 的混合语义；合并 failure/interrupt 视图扫描。

优先级：**中**。

### 3.5 `src/mote_kernel/execution/invocation.py`

这是当前最值得简化的生产文件。它同时负责：

- lineage 建模；
- fence planning；
- scope resolution；
- resume planning；
- substitution admission 的桥接；
- recovery seed；
- continuation/frame 全量验证。

合理部分：这些职责都属于 invocation，而不属于 facade 或 executor。拆出该模块是正确的。

冗余与简化空间：

1. `_PlannedState` 与 `ChildStateBinding` 高度相似，仅多了 root 可用的 `parent_activation | None`。这是**条件性**简化：两者分别代表 invocation candidate 与 acknowledged context，直接合并可能模糊 durable-confirmed 边界；更安全的是先引入只读 `ScopedStateIndex` 统一 lookup/replace，而非立即合并 record 类型。
2. `_PlannedResume`、`PreparedResume`、`ScopedResumeCandidate` 重复保存同一 plan 的不同投影。`_PlannedResume` 保存 `successor + prepared + substitutions`；candidate 又保存 `previous + successor + substitutions + skip_actions + command`。
3. `install_confirmed_resume_frames()` 重复检查 scope、superstep、revision、provenance、settlement、reason、route。这里不能完全删除 post-commit 防御，因为需求明确要求验证 exact confirmed successor；可确认的简化是让 sealed admitted plan 保存 action-to-installation 绑定，安装时只检查 `confirmed == expected_successor` 并机械提升，不再二次搜索 command action。
4. `plan_resumes()` 同时维护 `planned_states`、`candidate_frames`、`plans`、`facts`、`candidates` 五个并行集合，容易产生错位。
5. `_validate_frame_index()` 超过百行，四类 frame 依次执行“类型、canonical、scope、descriptor、payload”校验，结构重复。应按 frame owner 拆为四个 typed validator，再由一个入口组合。
6. `lineage_states()` 每次从 context 构造 `_PlannedState`，随后 `_planned_state()` 多次线性搜索、`_replace_planned_state()` 多次排序。可使用一个不可变、规范化的 `ScopedStateIndex` owner，内部维护 tuple 并提供 typed lookup/replace。
7. `_resume_facts()` 再次把 request action 投影成 recovery fact；如果 admitted plan 已包含 canonical action evidence，可直接生成 recovery seed。

推荐目标模型：

```text
PreparedResume（executor 输出，未跨 whole-invocation admission）
    ↓ admit once
AdmittedResumePlan（sealed）
    - scope_run
    - previous/successor
    - command
    - resume inputs
    - publication installations
    - recovery facts
    ↓ commit exact successor
install(plan.installations)
```

这样可合并 `_PlannedResume` 与 `ScopedResumeCandidate` 的平行职责，并将 `AdmittedSubstitution` 约束为 plan 内 installation evidence。`PreparedSubstitution` 与 `AdmittedSubstitution` 不应直接删除阶段差异：前者尚未绑定 successor revision，后者已经绑定。

优先级：**最高**。

### 3.6 `src/mote_kernel/execution/engine/resume_admission.py`

合理部分：whole-invocation admission 必须在任何 commit 前完成，独立 owner 是正确的。它验证跨 scope substitution collision 和未来 routing availability，不能简单删除。

冗余与简化空间：

1. `ScopedResumeCandidate` 有 8 个字段。`skip_actions` 明确可从 `command.actions` 推导，是确认冗余；`successor` 虽可由 reducer推导，但缓存它可固定 expected successor，不能仅以“可推导”认定冗余；`has_pure_skip` 无法只根据 substitutions 精确推断，因为一个 scope 可同时有 pure skip 和 substitution skip，应改为保存具体 pure-skip targets，而不是简单删除。
2. substitution validation 与 `install_confirmed_resume_frames()` 有重叠，但分别处于 pre-commit admission 与 post-commit exact-confirmation 边界。应减少重新搜索和字段反推，不应删除任一边界的验证。
3. duplicate detection 使用 `coordinates.count()`，是 O(n²) 且表达繁琐；单次计数即可。
4. 对每个 candidate 调用 `resolve_routing_facts()` 时传同一个全局 overlay，依靠完整 `ScopeRunCoordinate` 隔离 scope。测试已覆盖 sibling scope 和 repeated superstep 不串值，因此这不是已发现缺陷；仅可考虑按 scope 提供 view 改善可读性。
5. `has_pure_skip` 这个布尔值会擦除“哪些 action 无 output”的事实，错误信息只能再从 `skip_actions` 拼装。

建议：让 admission 接收一个较小的 `PreparedScopedResume`，只保存不可推导事实；返回 sealed `AdmittedResumePlan`，同时拥有 candidate availability 和安装清单。

优先级：**高**。

### 3.7 `src/mote_kernel/execution/engine/recovery.py`

本文件本来就很复杂（当前 1,185 行）。本次改动的正面价值是删除 recovery 私自实现的 `_target_has_historical_gap()` 和 `_outputs_have_historical_gap()`，改用 routing owner 的 facts。这是明显的去重。

剩余简化空间：

1. `RecoveryAvailabilityCoordinates.from_frames()` 接收 confirmed index 或 candidate overlay，是 live/plan 两阶段的显式输入，不是纯冗余。可通过共同 `ScopedFrameAvailability` 加一个 canonical-coordinate projection protocol 规范化；不能只换成现有 protocol，因为 recovery 需要枚举全部 coordinate，而 protocol 当前只有 presence check。
2. `action_node_ids()` 每次从 admitted actions 计算并排序，主要服务错误信息；可在构建 family 时一次计算，或错误处直接输出 structured targets。
3. 多个错误都重复 `resume actions {family.action_node_ids()!r}`，可构造统一的 recovery diagnostic context。
4. `preflight_recovery()` 对 admitted action 再验证 simulated successor settlement，是 recovery seed 的 trust-boundary 校验；测试专门覆盖 foreign/missing/mismatched action。不能删除。若 recovery 只接收 sealed admitted plan，才可收窄这组重复检查。

不建议：不要为了减少代码把 recovery proof 合并回 live executor；静态 proof 和具体执行职责不同。

优先级：**中**。

### 3.8 `src/mote_kernel/execution/engine/routing.py`

本次抽出 `RoutingFacts` 是正确方向，让 live routing、resume admission 和 recovery 使用同一解释器。

冗余与简化空间：

1. `graph_outputs_available()` 与 `unavailable_graph_outputs()` 完整重复遍历和 coordinate 构造，这是确认的机械重复。不要简单让 bool 函数调用诊断函数造成无用字符串分配；应共用一次 typed `_graph_output_availability()`。
2. `unavailable_target_inputs()` 与 `_target_has_historical_gap()` 再次遍历相同 materialization bindings、构造相同 coordinate。应一次产生每个 input 的 `available + historical_gap + display` facts。
3. `_completion_output_history_missing()` 当前确实只是 `not graph_outputs_available(...)`，且只调用一次，是确认可内联项。进一步看，`completion_output_history_missing` 与 `not completion_output_available` 在当前实现同源，字段是否需要独立保留应由 recovery diagnostics 测试验证后再决定。
4. `plan_routing -> resolve_routing` 的确只有 `.command` 薄包装，但 tests 和 superstep 分别需要 detailed resolution 与 command。它是便利投影，不构成高价值冗余。
5. `_declared_joins()` 每次 resolution 都重新从 compiled graph 构造 dict；应在 compile plan 中预先冻结唯一 join index。
6. `required()` 对 control/join/data 的重叠 target 可能重复计算 availability。可对 `all_targets` 一次 memoize，再按集合投影。

建议：引入内部 `BindingAvailability`/`TargetAvailability` 事实，由一次 binding scan 同时产出 unavailable inputs 和 historical-gap；compile 时保存 declared join index。

优先级：**高**。

### 3.9 `src/mote_kernel/execution/errors.py`

新增 `FrameInstallationInvariantError` 用于区分 commit 前 admission 错误与 commit 后不可能失败的 installation 错误。语义合理。

结论：错误类本身无冗余。它没有挂成 `Graph.*` 精确 alias，但仍继承 `ExecutionError`，用于直接传播或成为 `PartialCommitError.cause`。这能让调用者统一捕获执行底座错误，同时保持它不是正常可恢复分支；目前没有足够证据修改继承关系。

优先级：**低**。

### 3.10 `src/mote_kernel/execution/executor.py`

合理部分：executor 负责将 public request admission 成 typed frame 和 reducer action，不直接提交状态。

简化空间：

1. `resume()` 已超过 120 行，同时处理 failed retry、failed-input override、interrupt answer、pure skip 和 skip substitution。既然不支持图级失败重试，应只删除 failed-retry 使用的 materialized-input/override 分支及其 public factory、request、reducer action、recovery proof 和测试，再评估剩余 interrupt/skip 逻辑是否还需要拆分。interrupt answer 使用的 `OverrideNodeInput`、`OverrideGraphNodeInput`、resume codec 与 admitted resume-input frame 链路必须保留。
2. 为了在 executor 返回 command 前 fail closed，代码先构造 `replacements/simulated` 并调用 `validate_graph_frontier`；invocation 随后才运行 reducer。当前 reducer command 也会验证 transition，但 executor 的 pre-return validation 是独立 admission 边界。没有证明前不建议删除，原报告将其列为简化候选过于激进。
3. skip output 从 `_GraphValues` 转 `NodeOutputFrame` 的 admission 属于 output/publication owner，可提取窄函数，executor 只拿 `PreparedSubstitution`。
4. `SkipSubstitutionProvenance()` 是无字段对象，每次创建没有信息量，可用 singleton capability。

优先级：**中**。

### 3.11 `src/mote_kernel/execution/request.py`

新增 `output` 后，`SkipFailedNodeRequest` 泛型化是必要的，保证 graph value universe 不交叉。

简化空间：

- `ResumeFailedNodeRequest` 属于不支持的图级失败重试，应连同 `UseMaterializedInput`、`resume_failed()`、`resume_failed_with()`、state command `ResumeFailedNode` 及对应 executor/invocation/reducer/recovery 分支删除；不要为了保留无效语义去抽取公共基类。
- `ResumeInterruptedNodeRequest` 与 `SkipFailedNodeRequest` 仍是不同 decision；即使都含 `scope + node_id`，也不建议使用继承压缩 dataclass。
- `UseMaterializedInput` 仅服务 failed resume；随图级失败重试链路一起删除，不再讨论 marker 的实现形式。
- `UseStepRequestInput` 不能随之删除：它不仅曾被 failed retry 引用，也是正常 `StartGraphRun`/`AdvanceGraphFrontier` 将新节点建立为 `PendingGraphNode` 时的 authoritative input binding。
- 删除 `skip_failed()`，也不新增 `Graph.skip()` 或 `skip_many()`。对外使用 `run(skips=[...])`；list 表示一次处置多个节点，每个 dict 表示一个节点的独立处置。
- 每项的 `node` 是完整路径：nested 节点例如 `("editor", "review")`，root 节点例如 `("review",)`。即使当前只有一个失败节点也不能省略，避免图演化为并行失败后语义改变。
- 每项可独立携带 `reason`、`route` 和 `output`。replacement `output` 继续要求 `Graph.Values[GraphValueT]`，保留 graph value universe 的泛型安全；随后再按目标节点 compiled output descriptor 检查字段名、缺失/多余字段和 exact value type。
- `run()` 入口负责校验 target 不重复、节点存在且当前为 failed，并将 public `skips` list/dict 规范化为 canonical immutable internal decisions；bare dict 不进入 reducer、routing 或 state 边界。

结论：**基本无内部冗余**。

### 3.12 `src/mote_kernel/execution/result.py`

合理部分：`_PartialCommitError` 携带 exact state + continuation + cause + failed scope，是多 scope durable prefix 的必要交接。不能只抛原始异常。

冗余与简化空间：

1. 文件拥有六组不同概念：task result、commit result、child projection、prepare disposition、resume preparation、public graph result；可按 owner 拆分，当前“result”已成为聚合包。但这是认知/归属优化，不一定减少代码，优先级低于 resume plan 去重。
2. `_PartialCommitError` 与三种 graph result 都携带 `state + continuation`。可以有一个内部 sealed `RunCheckpoint` 值对象，但公共 variant 仍应保持 nominal 类型，不建议使用基类字段继承制造协变问题。
3. `_PartialCommitSeal`、`_CommitResultSeal`、`_ResultSeal` 是重复 construction capability；可统一实现模式，但每种 owner 仍需独立 token。
4. `PreparedResume` 不属于 public result，但它是 executor 的 prepare 输出，不宜放进 caller request owner；更合适的是独立的 engine resume-plan owner，随后由 admission 提升为 sealed plan。
5. `CompletedGraph`、`AbortedGraph` 是空 marker，和 `AwaitingResume` 组成 boundary。可以使用 enum + awaiting payload，但 closed nominal types对 exhaustive narrowing有益，收益有限。

建议：至少将 `PreparedResume` 移至 engine resume-plan owner；是否拆 child/prepare disposition 取决于模块导航收益。这主要降低认知负担，不一定减少总行数。

优先级：**中**。

### 3.13 `src/mote_kernel/execution/run_context.py`

这是第二个高价值简化点。

合理部分：不同 coordinate 和 frame nominal type 防止 graph input、publication、resume input、child boundary 混用；不能压成 bare dict。

冗余与简化空间：

1. `PreparedSubstitution` 与 `AdmittedSubstitution` 字段接近，但实施文档明确将前者定义为 descriptor-admitted candidate、后者定义为绑定 exact expected revision 的 evidence。阶段区分成立。简化方向应是缩小可见范围并让二者只由 resume-plan owner 构造，而不是合并成一个带 optional revision 的类型。
2. `SkipSubstitutionProvenance` 是 closed nominal provenance 的必要 variant。可以使用 owner singleton减少实例创建，但不能用 enum/string/sentinel 替代 nominal 类型；收益很小。
3. `CandidateFrameAvailability` 四个方法中三个纯委托，只有 `has_publication` 加 overlay。可组合一个 publication overlay protocol，而不是复制整个 `ScopedFrameAvailability` facade；但 routing 需要统一 protocol，当前设计也有合理性。
4. `ScopedFrameIndex` 四类 tuple 各自重复 `has`、`lookup`、`add + sort + duplicate`。可用内部泛型 `_CanonicalRecordIndex[T]` 作为实现细节，外部仍提供 typed methods。
5. `_CompleteContinuationSnapshot` 与 `_RecoveredContinuationSnapshot` 字段相同，但用 nominal variant控制 complete validation，避免字符串 discriminator。保留优于改成 enum/布尔字段，不再列为推荐简化。
6. `_RootStateBinding` 只有一个 `state` 字段，且 context 频繁 unwrap；可让 context 直接持 root state。但它可能用于 snapshot nominal ownership，属于轻度冗余。
7. `state + continuation` 在三种 public result 与 partial-commit handoff 上成对出现，却在 API 中被拆开传回。内部仍应分离 durable state 和 concrete frames；常规调用不应再传 opaque handle，而应由同一个 `Graph.run()` 按内部 compiled graph identity 加载上下文。

优先级：**高**。

## 4. 测试代码逐文件审查

### 4.1 `tests/architecture/test_graph_execution_ownership.py`

正面：确保 Graph 唯一公共 facade、routing 单 owner、continuation 无隐藏 mutation。

问题：测试精确锁定 `_PlannedResume`、`CandidateFrameAvailability` 的字段和模块位置，会阻止删除中间 DTO，即使行为和不变量更好。建议架构测试锁定“唯一 owner”和“禁止重复解释”，不要锁定每个临时类的精确字段。

### 4.2 `tests/architecture/test_graph_typing_fixtures.py`

新增 partial commit、skip output 的不变性和 universe 测试是必要的。无明显冗余。可将重复 diagnostic tuple 构造抽成参数 helper，但不要合并负向 fixture；每个非法程序恰好一个 error 的约束应保留。

### 4.3 `tests/architecture/test_source_discipline.py`

仅更新 Graph owner 文件路径。无冗余。

### 4.4 `tests/execution/engine/test_recovery_identity.py`

新增 skip substitution/recovery identity 覆盖是必要的。简化点：多处手工 drive state 到特定 frontier，可抽取 typed scenario builder，减少 setup 重复；不要抽成通用 dict fixture。

### 4.5 `tests/execution/engine/test_resume_admission.py`

451 行新测试集中覆盖 141 行 admission 代码。逐项核对后，这些用例覆盖 duplicate、existing collision、scope/run identity、exact reducer successor、command/action binding、descriptor/provenance/revision、scope/superstep 隔离、control/data/join availability，不能仅凭比例认定测试冗余。它们确实反映 `ScopedResumeCandidate` 保存了多组需互证字段。

建议：

- 先收敛 production candidate；只有被类型结构消除的不可能状态，其 tamper 测试才可删除；
- 保留行为边界：duplicate coordinate、existing publication collision、missing consumer input、pure skip output loss、join 分支；
- 将“字段 A 与字段 B 不一致”类测试收敛到 sealed constructor owner，而不是 public workflow 重复验证。

### 4.6 `tests/execution/engine/test_runtime_boundaries.py`

新增 commit 前/后 frame visibility 与 installation failure 覆盖很重要。问题是测试直接构造 `_PlannedResume`、`PreparedResume`、`ScopedResumeCandidate`，和生产内部形状强耦合。建议改为通过 executor prepare/admission owner 生成 sealed plan，只在 owner 单测中构造原始 candidate。

### 4.7 `tests/execution/test_continuation_integrity.py`

验证 substitution provenance、continuation 不可篡改和 publication revision 必要。可简化 setup，但测试主题不冗余。若统一为 `PublicationInstallation`，对应测试应检查 installation 在 exact commit 后成为 confirmed publication，而不是逐字段 tamper。

### 4.8 `tests/execution/test_frame_index_contract.py`

新增 overlay presence-only 测试合理。若 `CandidateFrameAvailability` 被简化为 publication overlay，应保留“不提供 lookup、不能读取未确认 frame”的关键契约，避免 candidate concrete value提前泄漏。

### 4.9 `tests/execution/test_graph_api.py`

本次新增约 1,499 行，是最大测试增长点。逐个测试核对后，主要行为矩阵并非重复：loop、sibling scope、partial commit throw/non-exact/install failure、root-child prefix、fence prefix、state-only、future graph output、nested boundary、join、branch、exact output、collision分别覆盖不同不变量。但存在三类结构重复：

1. 多个 partial-commit 测试重复 monkeypatch `_PlannedResume` installation；
2. 多个测试直接访问 `continuation._snapshot` 并 import 私有 `_PlannedResume`；
3. pure skip、substitution、join、branch 的 setup 大量重复。

建议按公共行为重组：

- 公共 API 文件只保留 end-to-end：单 scope、多 scope、partial commit、state-only recovery、nested scope；
- tamper/invariant 测试下沉到 invocation、resume admission、run context owner；
- 建立少量 typed graph scenario factory，如 linear consumer、conditional branch、join、nested siblings；
- 不要用一个大参数化测试掩盖不同恢复边界。

### 4.10 `tests/execution/test_graph_public_typing.py`

新增 partial commit 类型暴露测试必要。无明显冗余。

### 4.11 `tests/execution/test_graph_recovery_contract.py`

改动使 recovery error diagnostics 包含 action target 和 missing binding。属于行为契约，合理。建议避免逐字锁定过长错误句，只锁定结构化关键片段，便于简化 diagnostics。

### 4.12 `tests/typing_negative/cross_universe_skip_output.py`

必要，证明 skip output 不可跨 graph universe。无冗余。

### 4.13 `tests/typing_negative/invariant_partial_commit.py`

必要，防止 partial continuation 被错误 widening。无冗余。

### 4.14 `tests/typing_negative/skip_output_factory_inference.py`

必要，固定 heterogeneous/empty factory inference。无冗余。

## 5. 文档逐文件审查

### 5.1 `docs/skip-failed-output-requirements.zh-CN.md`

主需求文档，应保留，作为语义来源。

### 5.2 `docs/skip-failed-output-requirements-review.zh-CN.md`

一次评审记录，提出多 scope、data trigger、shared proof owner、provenance 和 nested child 五个问题。它记录了需求为何变化，具有独立审计价值，不是主需求的简单重复。

### 5.3 `docs/skip-failed-output-requirements-review-response.zh-CN.md`

评审回复修正了“stable continuation 重新证明历史 settlement”这一过强要求，并明确 transition-time/stable-time 责任边界。该修正不可丢失。若合并，必须作为带时间顺序的决策记录保留。

### 5.4 `docs/skip-failed-output-requirements-second-review.zh-CN.md`

二次评审进一步发现 pure skip/data target 与 duplicate pre-admission 两个 P1。它不是形式性复核。可归档到 ADR，但不应无痕删除。

### 5.5 `docs/skip-failed-output-implementation.zh-CN.md`

实现设计主文档，应保留，但需在简化后更新 plan 类型和边界。

### 5.6 `docs/skip-failed-output-implementation-review.zh-CN.md`

实现评审发现 continuation pure skip 未运行 whole-future proof、candidate 未绑定 expected revision 两个 P1，以及 resolver/overlay/install 三个 P2。内容对理解当前复杂度来源很关键。

### 5.7 `docs/skip-failed-output-implementation-second-review.zh-CN.md`

第二轮实现评审逐项关闭前述 P1/P2，是批准记录。当前 7 份文档合计 1,988 行，确有重复引用，但它们构成需求与实现的审计链。只有在仓库明确不保留评审历史时，才建议收敛为：

- 一份 requirements；
- 一份 implementation；
- 一份 ADR/review history。

更稳妥的建议是保留历史文件、增加一个索引标注“当前规范事实源”和“历史评审”，而不是直接合并删除。

## 6. 推荐的简化顺序

### 6.0 核对后的证据分级

确认可以简化：

- `ScopedResumeCandidate.skip_actions` 与 `command.actions` 重复保存；
- `_PlannedResume` 与 `ScopedResumeCandidate` 平行保存 scope、successor、command 和 substitutions；
- `plan_resumes()` 同步维护多组平行 list/tuple；
- routing 对 graph output 和 target input 做重复 binding scan；
- `_completion_output_history_missing()` 是单行反向包装；
- `run()` 内部 fence/resume exact-confirmation 异常交接循环存在机械重复，但只能以 private implementation detail 收敛；
- duplicate diagnostics 中的 `tuple.count()` 是 O(n²) 机械实现；
- `result.py` 的 `PreparedResume` 模块归属不准确，应移到 engine resume-plan owner，而不是 caller request owner。

条件性简化，必须先建立替代不变量：

- sealed `AdmittedResumePlan` 可合并 plan/candidate，但必须保留 pre-commit admission 与 post-commit exact confirmation；
- `ScopedStateIndex` 可减少 lookup/sort，但不能混淆 candidate state 与 acknowledged state；
- frame index 可复用 typed canonical-index 内核，但不能退化为 bare dict 或擦除 nominal frame type；
- `run()` 可通过内部加载的运行上下文隐藏 state/continuation；这项是尚待实现的目标，不是当前行为。多 interrupt 选择由 `answers` 列表中的完整节点路径表达，但不形成第二执行 API。

核对后撤回的激进建议：

- 不直接合并 `PreparedSubstitution` 和 `AdmittedSubstitution`；
- 不删除 recovery 对 admitted action/successor 的边界验证，除非输入变为 owner-sealed plan；
- 不删除 executor 的 simulated frontier validation，除非证明 reducer admission 完全覆盖且错误时序不变；
- 不把 complete/recovered continuation snapshot 改成布尔或字符串 discriminator；
- 不因测试行数多就删除 tamper tests；只有类型结构消除相应非法状态后才能删；
- 不直接删除评审文档，优先建立规范/历史索引。

### 阶段一：只收敛内部 resume plan，不改变公共行为

1. 引入唯一 sealed `AdmittedResumePlan`；
2. 合并 `_PlannedResume` 与 `ScopedResumeCandidate` 的平行字段；
3. 保留 prepared/admitted 两阶段，但把 substitution evidence 封装进 plan 内部 installation；
4. admission 验证 reason/route/action binding；post-commit 通过 sealed plan 和 exact successor 做机械提升，避免再次搜索 action；
5. 移动 `PreparedResume` 到 engine resume-plan owner。

预期收益：删除多组平行 tuple、减少双重验证、缩减大量 tamper 测试，同时保持 durable-first。

### 阶段二：简化 routing availability

1. 一次 binding scan 同时产生 available、missing、historical-gap；
2. 合并 graph output 的 bool/list 两次遍历；
3. compile 时冻结 join index；
4. target availability 做一次 memoize。

预期收益：routing、resume admission、recovery 共用更小且更清晰的 facts。

### 阶段三：整理唯一 `Graph.run()` 的内部事务实现

1. `Graph.run()` 保持唯一 public composition/execution facade；
2. 先引入显式、窄 typed checkpoint port，并提供同进程内存实现；不能把 registry 偷藏进 `Graph` 实例。port 不是 runner，只负责按内部 compiled graph identity 保存和加载 checkpoint；
3. `run()` 内部编译 graph 后，以 compiled graph identity 加载已有 run state/context；有可恢复状态就继续运行，确实没有可加载状态时才创建新 run；
4. 用 private typed 函数收敛 fence/resume 的 exact-confirmation 机械循环；
5. 所有 private 函数仍由 `run()` 单向调用，不形成 private runner 或平行执行路径。

预期收益：保持单一入口和完整事务语义，同时降低方法体内的机械重复。

### 阶段四：让唯一 `run()` 自动选择恢复或新运行

面向普通调用者仍只有一个 `run()`，但意图由互不混淆的关键字参数表达：

```python
# 明确启动新运行
paused = await graph.run(
    inputs=Graph.values(article="标题"),
)

# 明确回答 interrupt
completed = await graph.run(
    answers=[
        {
            "node": ("editor", "review"),
            "values": Graph.values(article="修改后的标题"),
        }
    ],
)
```

目标签名应使用 closed overload，而不是三个 optional 参数组成的宽签名，也不增加公共 `Command`：

```python
@overload
async def run(
    self,
    *,
    inputs: Graph.Values[T],
) -> Graph.Result[T]: ...

@overload
async def run(
    self,
    *,
    answers: list[AnswerSpec[T]],
) -> Graph.Result[T]: ...

@overload
async def run(
    self,
    *,
    skips: list[SkipSpec[T]],
) -> Graph.Result[T]: ...

@overload
async def run(
    self,
    *,
    answers: list[AnswerSpec[T]],
    skips: list[SkipSpec[T]],
) -> Graph.Result[T]: ...
```

这里 `AnswerSpec`/`SkipSpec` 只是签名说明；公共调用仍使用前述列表嵌套字典，不要求用户导入新的执行 facade 或 `Command`。实际实现可以用一个 private union 接收 overload 汇总参数，但 public typing 必须排除“全部缺失”“`inputs` 与恢复参数混传”等非法组合。

每次 `run()` 都先获得内部 compiled graph identity，再通过 checkpoint port 查找该 graph 的运行上下文，但加载只用于验证当前状态，不能改变参数含义：

- `inputs` 永远表示启动新运行；若已有活动 checkpoint，报错；
- `answers` 永远表示回答 interrupt；若没有 checkpoint、目标不存在或目标不是 interrupted，报错；
- `skips` 永远表示处置 failed 节点；若没有 checkpoint、目标不存在或目标不是 failed，报错；
- 三者都不传在静态类型层即不匹配任何 overload，运行时仍 fail closed；
- `inputs` 与 `answers`/`skips` 混传不匹配任何 overload；
- `answers + skips` 有独立 overload，表示同一个 checkpoint 上的一次 whole-invocation admission 与原子事务；目标重复、状态不匹配或未来不可安全推进时，在首个 commit 前拒绝。

不采用 `run(command=...)`：公共 `Command` 会再引入一层泛化包装，并与 owner-internal reducer command 混淆。当前三类用户意图已能由 closed overload 精确表达；只有未来确实增加 public goto/update 等组合能力时，才重新评估类似 LangGraph `Command` 的抽象。

默认 checkpoint port 可以只保存在内存；跨进程时替换为持久化实现。现有 `commit` 仍只确认 transition，不能同时冒充加载接口。

当前目标明确采用“一份 compiled graph 同时只对应一个活动运行”的约束，因此不需要 public `run_id`。若未来确实需要同一 compiled graph 并发多个运行，必须重新引入显式 session identity；不能在本轮提前增加该概念。

这段是目标 API，不是当前代码示例。当前实现仍要求：

```python
completed = await graph.run(
    state=paused.state,
    continuation=paused.continuation,
    resume=(graph.resume_interrupted(...),),
)
```

因此落地时需要让 public `run_id` 与显式 `state + continuation` 恢复 overload 退出常规调用路径，并定义同一 compiled graph checkpoint 在“已完成”“等待恢复”“运行中”三种状态下的确定行为。failed-retry variant 应删除，但共享 `ResumeGraphNodes` 继续承载内部 interrupt answer 与 skip action；`resume_interrupted()`/`skip_failed()` factory 由 `answers=`/`skips=` 替代。每项的完整节点路径不能省略：同一 checkpoint 可能有多个 root/nested interrupt 或 failure，Kernel 不能猜。这里的内部上下文不能成为第二执行 owner；它只负责 checkpoint 的 load/save，事务编排仍全部由 `Graph.run()` 完成。

单个节点的目标调用形态：

```python
result = await graph.run(
    skips=[
        {
            "node": ("editor", "review"),
            "reason": "人工忽略",
            "output": Graph.values(value="人工替代结果"),
        }
    ],
)
```

一次处置多个节点：

```python
result = await graph.run(
    skips=[
        {
            "node": ("editor", "review"),
            "reason": "忽略编辑失败",
        },
        {
            "node": ("publisher", "review"),
            "reason": "使用人工替代结果",
            "route": "approved",
            "output": Graph.values(value="replacement"),
        },
    ],
)
```

字段名 `value` 只是对应“该节点声明 `outputs={"value": str}`”的示例；真实 key 必须与目标节点自己的 output descriptor 完全一致。`Graph.Values` 保留泛型 universe 安全，descriptor admission 负责字段名及 exact value type。真正加载 checkpoint、校验节点状态、生成内部 typed decision、提交 reducer command并继续图执行的仍是唯一的 `Graph.run()`。

这里还必须区分两种“对外”：业务调用者始终只看见 `run()`；应用装配层仍需显式提供 checkpoint port（可以默认装配内存实现）。这不是第二个执行 API，而是状态依赖。若连装配依赖也完全隐藏，就会退化为 `Graph` 内部的隐式 mutable registry。

预期收益：普通用户不再手写 `state`、`continuation` 和 `ResumeAction`；内部精确模型继续保留。

## 7. 最终判断

内部并非“概念全部多余”，而是**同一 resume transaction 被太多相邻 DTO 重复表达**，同时这些内部概念又直接泄漏到了公共调用面。

最值得删除或合并的对象：

- `_PlannedResume` 与 `ScopedResumeCandidate` 的重复部分；
- `ScopedResumeCandidate.skip_actions` 等可从 command 精确派生的字段；
- prepared/admitted substitution 在多个 plan DTO 中的平行包装，而不是二者的阶段区分；
- routing 的重复 availability 遍历；
- `run()` 内部重复的 fence/resume commit 机械代码（只做 private 收敛，不拆公共入口）；
- 测试 setup 中反复手工拼装相同 nested/partial-commit 场景。

最应保留的边界：

- `GraphState` 与 concrete frames 分离；
- caller intent 与 reducer command 分离；
- admission、commit、installation 的顺序；
- exact commit confirmation；
- partial commit handoff；
- live execution 与 recovery proof 共用 topology/routing truth，但不合并成同一执行器。

因此建议不是“大幅删除状态机层”，而是收敛 resume plan 的表达数量，并在 facade 上隐藏精确恢复细节。
