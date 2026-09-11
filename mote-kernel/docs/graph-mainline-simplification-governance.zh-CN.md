# Graph / Execution / State 真实主流程结构简化治理总账

## 目的与状态

本文是 Graph、Execution、State 主线的 canonical 治理总账：记录全部已识别的改进候选、已完成迁移、明确保留项、硬不变量、删除范围、风险和验收条件。后续每次代码治理都先更新本文件，再动生产代码，避免候选、临时方案或已关闭问题被遗忘。历史 49 个复杂度热点的逐项证据保留在 [`complexity-49-simplification-review.zh-CN.md`](./complexity-49-simplification-review.zh-CN.md)，本文件的登记号与其一一对应，不另造第二套事实清单。

本账不以文件行数或单个复杂度指标为目标；只有能够减少完整调用链中的重复事实推导、重复决策或重复执行路径，同时保持唯一 owner 和现有异常边界的改动，才进入实施。指标只负责召回审查点，不能替代设计裁决。

记录基线（用于区分 HEAD、本次治理提交与并发工作树）：

- 日期：2026-09-10；
- 提交：`e4ef968 fix(security): update vulnerable toolchains and dependency gates`；
- 分支：`feat/kernel-hooks-final`；
- HEAD 的隔离复杂度报告：`complexity_hotspots=122`、`type_definitions=680`、`dataclass_types=426`、`semantic_nodes=68812`、`cognitive_complexity=4154`、`internal_call_edges=1436`，最大圈复杂度 45、最大认知复杂度 52、最大调用链深度 17、最大调用链认知复杂度 325；zero-debt health 为 PASS；
- 本次治理提交完成 G1/G9 后的报告：`complexity_hotspots=122`、`type_definitions=679`、`dataclass_types=425`、`semantic_nodes=69308`、`cognitive_complexity=4197`、`internal_call_edges=1444`，最大圈复杂度 45、最大认知复杂度 52、最大调用链深度 17、最大调用链认知复杂度 325；zero-debt health 为 PASS；
- 本次治理提交相对 HEAD 的精确变化为：`type_definitions -1`、`dataclass_types -1`、`single_use_private_dataclasses -1`、`top_level_definitions +2`、`function_definitions +3`、`decision_points +25`、`semantic_nodes +496`、`cognitive_complexity +43`、`exception_handlers +4`、`internal_call_edges +8`、`cross_module_call_edges +6`、`ambiguous_internal_dispatches +2`、`low_usage_private_definitions -1`，最大复杂度与热点数不变；增长来自 Config 传播和异常边界的显式契约及确定性测试，已在完整调用链审查后按精确值写入 ratchet，没有放宽 zero-debt health 上限；
- 触发审查时记录的文件规模信号为 `family_driver.py≈1366`、`compiler.py≈1340`、`routing.py≈812`、`facade.py≈853` 行；本次治理门禁时分别为 1305、1494、901、851 行。行数变化只作定位证据，不作质量目标；
- 最终门禁结果：`make check` 通过，2258 tests 全部通过、分支覆盖率 100.00%、Strict Pyright 0 errors、Ruff/format、复杂度/架构、build 和 package-check 全部通过；monorepo 根目录 `pre-commit run --all-files` 全部通过；
- 当前工作树还包含同事并发开发的 persistence/codec/facade/family-driver 改动；本账只覆盖本次 Graph/Execution/State 治理提交，不能顺手暂存、回滚或重写并发改动。

状态标记：

- `R`：正在调研，尚未作出实施裁决；
- `A`：完整目标设计、迁移和删除范围已经明确，可以一次性实施；
- `P`：代码已按目标设计迁移，定向行为测试通过，但全量门禁或 ratchet 尚未复核；
- `C`：只有发现可证明的重复规则后才实施；
- `K`：当前复杂度表达真实领域语义，明确保留；
- `DONE`：实施、删除旧路径和全部门禁均完成。

## 最高判定原则

- **唯一真相**：每项静态规则、运行时状态和派生事实只有一个权威 owner。派生 evaluation 不进入 durable state，不缓存为镜像状态。
- **完整调用链优先**：以 `admit -> drive -> reduce -> commit -> project -> cleanup` 的总复杂度判断，不通过薄 helper、转发 wrapper 或宽 context 降低局部指标。
- **复用已有 owner**：静态拓扑归 compiler，动态事实归 `GraphRunState`，纯转换归 reducer，路由派生归 routing，原子提交归 commit，异步生命周期归 family driver。
- **保留领域边界**：fresh、continuation、live routing、commit admission、recovery、nested child 和 cancellation 可以复用同一规则，但必须保留各自的错误类型、错误优先级和恢复边界。
- **一次性迁移**：先定义不变量、调用点和删除范围，再同步迁移生产代码与测试；不保留旧 API alias、兼容 wrapper、双执行路径或临时 mirror。
- **门禁用于证明而非裁决**：指标命中只触发复审。设计和代码本身必须先足够简单、清晰、唯一，随后再以类型、测试、覆盖率、复杂度、架构和 pre-commit 证明没有回归。

## 当前真实主流程

### Fresh run

```text
Graph.run
  -> Graph._compile
  -> GraphCompiler.compile / _compile_definition
  -> admit_graph_input
  -> fresh_root
  -> _start_fresh_owner
       -> StartGraphRun
  -> prepare_transition / reduce_graph_run
  -> confirm_transition
  -> _GraphRun
  -> drive_quantum
```

### Continued / recovery run

```text
Graph.run(state=...)
  -> continuation / frame / lineage admission
  -> plan_fences
  -> plan_resumes
  -> admit_state_owned_overrides
  -> preflight_recovery
  -> admit_continued_root
  -> apply admitted fences / resumes
  -> _GraphRun
  -> drive_quantum
```

### 共同执行后缀

```text
drive_quantum
  -> settled frontier
       -> resolve_routing
       -> AdvanceGraphFrontier | CompleteGraphFrontier | AbortGraphRun
       -> _transition
  -> executable frontier
       -> prepare_frontier / plan_tasks / materialize_node_input
       -> prepare_claim
       -> _execute_frontier
       -> start nested children
       -> ClaimGraphExecution commit
       -> issue_session
       -> drive parent and child workers
       -> SettleGraphNode commits
       -> FenceGraphExecution commit
  -> child-only frontier
       -> drive child owners | AwaitingResume
  -> project_graph_result
  -> abort/release cleanup
```

任何候选改动都必须用修改前后的完整链路证明净减少；只减少某一函数行数或圈复杂度不计为收益。

## Owner 与硬不变量

| 事实或行为 | 唯一 owner | 不可违反的边界 |
| --- | --- | --- |
| 节点、边、route、Join occurrence、binding、frame descriptor | compiler / `CompiledGraph` | domain package 不得建立私有 runner 或重算静态拓扑 |
| frontier、settlement、Join progress、execution lease、revision | `GraphRunState` | 不创建第二 runtime state 或镜像 ledger |
| state transition | `reduce_graph_run` | service、tool、routing 和 recovery 不直接修改 state |
| routing 派生事实 | execution routing | 只在一次精确 graph/state/frame 输入上派生，不持久化 |
| transition candidate、write set、commit acknowledgement | execution commit | durable commit 确认后才能替换 Python snapshot 和安装 frame |
| session、worker、child owner、取消和 fence 顺序 | family driver / execution session | 不创建第二 scheduler、第二 child runner 或统一异常吞并层 |
| recovery 可达性证明 | recovery proof | work item 只是证明过程，不成为第二运行时真相 |

## 改进候选总表

| 编号 | 状态 | 候选 | 预期真实收益 | 主要风险 |
| --- | --- | --- | --- | --- |
| G1 | `DONE` | Routing / Join 一次性评价 | 删除同一 state 上重复的 Join ledger、successor 和 availability 推导 | 合并不同时间证据、改变错误优先级、产生第二派生容器 |
| G2 | `K` | Fresh / continued root admission 的窄 handoff | 让两类 admission 后只保留一条 drive/project/cleanup 生命周期 | 隐藏 fresh commit、recovery preflight 和 partial-commit 差异 |
| G3 | `K` | Compiler phase facts 与 topology traversal 复用 | 删除确实重复的静态关系构建或节点遍历 | 巨型 `CompilationContext`、改变 proof 顺序或错误优先级 |
| G4 | `K` | Recovery 复用 routing/materialization 事实 | 删除 recovery 对相同坐标、binding、Join 规则的第二实现 | 把 live/recovery 错误边界混为一体，或缓存跨 work item 的过期事实 |
| G5 | `DONE` | `family_driver` fresh owner admission 收敛 | root 与 nested child 共用唯一 fresh start/construct/abort owner，保留各自 handoff 与错误边界 | 新增第二 runner、宽 context、模糊化 continued/recovery admission |
| G6 | `K` | Typed record 审查 | 仅保留拥有不变量、坐标、证据或领域边界的 record | 为降低类型数量退回 dict、tuple、`Any` 或宽 context |

G2 已确认当前 root lifecycle 没有重复 owner，判定为 `K`。若未来在 G5 范围发现同一 lifecycle
出现新的重复，只能统一重新审查，不能分别引入两层抽象。

## G1：Routing / Join 一次性评价

当前状态：`DONE`。目标设计已落地，validation/error precedence、稳定 target 顺序和只读 declaration/arrival 映射均已复核；精确 ratchet、全量 `make check` 与 monorepo pre-commit 全部通过。

### 当前调研范围

当前 routing owner 包含下列相关路径：

```text
resolve_routing_facts
  -> _routing_evaluation
       -> settled_activation_admission_error
       -> _frontier_gate_error
       -> _pending_join_arrivals
       -> _historical_join_arrivals
       -> _post_advance_error
  -> _resolve_control(evaluation)
       -> routing_contributions
       -> successor / Join activation causes
  -> _required_target
  -> unavailable_graph_outputs
  -> _completion_route

transition_admission_error
  -> frontier_admission_error(candidate)
  -> frontier_admission_error(previous)
  -> _resolve_control(previous)
  -> _completion_route(previous)

snapshot_guard (独立 fail-closed 边界)
  -> _declared_joins
  -> frontier_admission_error -> _routing_evaluation

recovery
  -> resolve_routing_facts
  -> project_routing_facts
```

已经观察并处理的重复：

- 对同一 state 的 `_pending_join_arrivals` 在 `_post_advance_error` 与 `_resolve_control` 中重复执行；
- `_declared_joins` 在 pending ledger、frontier gate 和 snapshot guard 中重复构建；
- terminal transition admission 对 candidate/previous state 重复构建 routing evaluation；
- frontier provenance 校验和 routing command 生成分别取得同一组 settled activation、Join occurrence 与 successor cause；
- `_ControlResolution` 与 `RoutingFacts` 携带同义 control/Join 派生事实，造成第二个 invocation-local record。
- `snapshot_guard` 仍先建立自己的 declaration index，以保留 snapshot 专属的 fail-closed 错误优先级；它与 live routing 是不同异常边界，不合并为宽 evaluation context；
- `transition_admission_error` 分别评价 candidate/previous state，因为两者是不同权威输入；不跨 snapshot 复用派生事实。

当前实现的唯一派生投影是 `RoutingFacts`：在一次 routing/admission 调用内，`_routing_evaluation()` 对一个
精确 `(CompiledGraph, GraphRunState)` 输入只建立一次 declaration index、pending/historical arrival
投影和 admission error；`_resolve_control()`、transition admission、recovery 和 command projection
消费这份投影。它不是 `GraphRunState` 的第二 owner，也不写入 continuation、frame index 或持久化 store。
snapshot guard 仍是独立的 fail-closed 边界，当前会重新取 declaration index；这项跨边界重复已登记为
后续候选，不能把两个异常 owner 粗暴合并。

实际调用次数（典型 `superstep > 0` 的 `resolve_routing_facts`）：

| 计算 | 修改前 | 修改后 |
| --- | ---: | ---: |
| `_declared_joins` | 3 | 1 |
| `_pending_join_arrivals` | 2 | 1 |
| `_historical_join_arrivals` | 1 | 1 |
| `_post_advance_error` | 1 | 1 |
| `_resolve_control` | 1 | 1 |

这是真实调用链中的重复扫描删除，不是把逻辑搬进薄 helper。实现同时删除了
`_ControlResolution`、control target 的冗余集合和“没有 admitted activation”的不可达防御分支；
`recovery._resolve_quiescent` 改为直接消费 `facts.required_targets`。pending 与 historical
Join 仍是两个语义明确、只读的字段，不能为了字段数量把它们合并为模糊 ledger。

最终裁决只合并同一精确输入、同一调用边界内的重复推导。`previous_state` 与 `candidate_state` 是不同评价对象；live routing、snapshot admission 与 commit admission 也保留各自错误边界，没有跨 revision 缓存或第二运行时事实源。

### 目标设计约束

若调研判定为 `A`，目标应当是让 routing owner 对一次精确输入生成一份不可变派生评价：

```text
(CompiledGraph, GraphRunState, ScopeRunCoordinate, frame availability)
  -> one routing evaluation
```

评价所需事实可能包括：

- 已验证的 settled activation ledger；
- compiled Join declaration index；
- 当前 durable partial Join progress；
- 从 settlement history 重建的 historical Join arrivals；
- direct 与 completed-Join successor；
- 每个 successor 唯一的 activation cause；
- remaining / consumed Join occurrence；
- required node-input availability；
- graph-output availability；
- terminal completion route。

实施时必须满足：

1. current partial Join 与 historical Join arrivals 保持两个语义明确的字段，不能合并为模糊 ledger；
2. evaluation 仅在当前调用栈内使用，不进入 `GraphRunState`、continuation、frame index 或 owner 字段；
3. 现有 `RoutingFacts`、`_ControlResolution` 与候选新结构必须重新划分后只保留一份事实，不允许同义 record 并存；优先演进已有 owner，而不是增加 alias；
4. live routing、snapshot admission、transition admission 和 recovery 直接投影各自结果，不堆叠多层 forwarding helper；
5. terminal candidate 已丢失 previous frontier，transition admission 仍可显式使用 previous-state evaluation；
6. validation 顺序和公开异常类型不变，特别是 malformed snapshot、Join progress、routing deadlock、历史值缺失和 commit mismatch；
7. 不做跨 revision、跨 frame availability 或跨 recovery work item 的缓存。

### 预期删除范围

只有能够同时删除或实质收窄下列重复路径时才实施：

- 同一评价中的重复 `_declared_joins` 构建；
- 同一评价中的重复 pending Join progress 扫描；
- frontier provenance 与 control resolution 中重复的 arrival/cause 推导；
- terminal admission 对已得到 control/completion 事实的再次推导；
- 只为搬运同义 routing record 而存在的中间层。

如果最终方案只是新增 `RoutingEvaluation`，但仍保留现有全部扫描和 `RoutingFacts` / `_ControlResolution` 镜像，则判定为无收益并放弃。

### G1 验收矩阵

- 普通 direct successor；
- conditional route 与 unknown route；
- 无 successor 的 terminal completion 及 completion route；
- partial Join 新增 arrival；
- historical partial Join 延续；
- Join 完成并消费 progress；
- terminal Join 与非 Join successor 同时出现；
- duplicate source、misprojected occurrence、unknown node、invented/lost progress；
- successor input unavailable 与 graph output unavailable；
- live routing、transition admission、snapshot guard、continued recovery 的异常类型和优先级；
- direct/Join 同一 target 的多 activation-cause 冲突；
- nested graph completion route 投影；
- exact reducer successor 和 commit acknowledgement。

G1 最终复核结果：

- 恶意 snapshot 的 validation 顺序与公开异常类型由定向 routing/recovery 测试锁定；
- `required_targets` 按 canonical activation target 顺序一次生成，command 与 recovery 共同消费，不再维护 direct/Join 镜像集合；
- declaration、pending arrival 和 historical arrival 映射均为 invocation-local 只读投影；
- `_ControlResolution` 和重复扫描已删除，完整调用链净减少；2258 项全量测试、100% 分支覆盖率和全部门禁通过。

## G2 / G5：Root admission 与 facade 内部编排

当前状态：G2 为 `K`，G5 为 `DONE`。fresh、continued 和 durable-recovery admission 仍只在各自的
准入语义中分支，随后统一交给一次 root owner wait、drive、result projection 和 cleanup。G5 收敛了
fresh root 与 fresh nested child 原先重复的 `StartGraphRun -> prepare -> frame staging -> confirm ->
_GraphRun -> construction-failure abort` 事务；continued/recovery 的 admission、partial-commit 和
cancellation policy 未被模糊化。

当前 fresh 与 continued admission 已经在 `Graph.run()` 后半段汇合到同一 owner drive、result projection 和 cleanup。迁移后的唯一 fresh owner 是 `_start_fresh_owner`；root 仍由 `fresh_root` 保留 evidence adapter，child 仍由 `_make_child_constructor` 保留 parent topology、coordinate、`_ChildCall` 和 handoff。进一步改动只有在发现以下新的真实重复时才成立：

- 两条 admission 分别实现同一 owner construction；
- 相同 partial-commit cleanup 在不同路径重复；
- 相同 evidence handoff 或 cancellation 分类被重复决定。

允许的目标是一个窄的 owner handoff。禁止：

- 把 invocation、state、frames、limits、commit、resume、fences、recovery facts 全塞进宽 `RunContext`；
- 把 fresh 的 `StartGraphRun` commit 与 continued 的 durable-state admission 合成一个模糊分支；
- 新增第二公共 Graph facade、第二 run service 或兼容入口；
- 为减少 `Graph.run()` 行数增加模块级薄转发函数。

## G3：Compiler phase facts

当前状态：`K`。历史 #32 已删除重复 canonicalization、资源/callable normalization 和末尾第二次
节点遍历；#29–#31、#34–#36 的剩余 traversal 分别拥有 fixed point、route-sensitive proof、
Join occurrence、terminal guarantee、definition/edge admission 等不同事实和错误边界。未发现仍由
两个 phase 独立构建且可能漂移的相同静态事实。

`_compile_definition()` 当前阶段具有真实依赖顺序：normalization、binding resolution、control topology、route-independent reachability、causal/route proof、terminal proof、Join occurrence proof、output binding 和 frame/materialization assembly。

仅审查下列可能的净收益：

- 多个 proof 是否重复构建完全相同的 successor、predecessor、gate 或 route relation；
- 同一个 node ordinal、descriptor 或 Join index 是否被独立遍历构建；
- 某个中间事实是否由两个 phase 分别推导且可能漂移。

保留项：

- route-sensitive 与 route-independent proof 不能合并；
- reachability、guarantee fixed point、cycle exit、Join occurrence 和 terminal guarantee 是不同证明；
- 不创建巨型 `CompilationFacts`，不为了降低 `_compile_definition()` 指标拆出连续 forwarding phase；
- 多个只读索引可以是同一 compiler owner 的不同投影，但必须一次构建、不可独立更新。

## G4：Recovery 规则复用

当前状态：`K`。历史 #4–#10 已确认 worklist、cycle signature、child outcome combination、
quiescent expansion 和 scope boundary 是同一有界恢复证明的必要阶段；G1 完成后 recovery 已直接消费
唯一 routing evaluation 的 `required_targets`，现有 binding/materialization 坐标也复用 compiled
projection，没有发现第二套 live routing、materializer 或 runner。持久化读取与 checkpoint 恢复
属于独立 P1 计划，不以其开发状态改写本项对 engine recovery proof 的裁决。

Recovery 的 worklist、cycle signature、child-outcome combination、bounded recurrence 和 scope boundary 是真实状态空间证明，默认保留。只处理 recovery 与 live execution 对同一规则的重复实现：

- binding source coordinate；
- publication / graph-input availability；
- compiled Join occurrence projection；
- node input availability；
- settled routing evaluation。

复用时只共享纯事实或纯投影；recovery 继续拥有自己的 `GraphValueUnavailableError`、snapshot diagnostic、budget 和 boundary classification。每个 simulated `GraphRunState` 单独评价，不跨 work item 缓存。

## G6：Typed record 审查

当前状态：`K`。逐项复核单用途 private record 后，没有 record 同时满足“无独立不变量、无领域边界、
删除后调用链更短且不引入裸参数/镜像状态”的全部条件；因此不做按数量驱动的删除。当前类型数量中
包含 Config、持久化和并发工作树的新增契约，不能用指标反推删除。

类型或 dataclass 数量本身不构成债务。只有同时满足以下条件才删除或内联：

- record 不拥有独立不变量；
- 不标识领域边界、坐标、证据、command 或封闭 variant；
- 只被一个调用点使用；
- 删除后不会增加参数列表、裸 tuple、dict、`object`、`Any`、cast 或字符串 discriminator；
- 删除后完整调用链更短，而不是把字段搬进宽 context。

`GraphRunState`、typed frame、activation/Join identity、commands、commit write set、recovery boundary 和 child lifecycle phase 不因类型数量指标而合并。

## 明确保留的真实复杂度

以下内容除非发现规则重复或行为缺陷，否则标记为 `K`：

- `GraphRunState` 及 `reduce_graph_run` 的唯一状态和纯转换边界；
- `prepare_transition -> confirm_transition -> apply_commit_writes` 的原子提交顺序；
- `_drive_workers` 对 worker fan-in、取消来源、family fence 和 cleanup 错误优先级的显式处理；
- `_ChildCall` 对 nested owner、phase、evidence handoff 和 release 的生命周期；
- `_prove_scope` 的唯一 recovery traversal；
- compiler 中不同 proof phase 的显式依赖顺序；
- `Graph` 作为唯一公共 composition/execution facade；
- settlement 与 routing 分成两个 durable transition 的恢复屏障。

## 一次性实施与验收流程

每个改进项按同一顺序执行：

1. 记录修改前完整调用链、事实 owner、重复推导和异常优先级；
2. 给出目标调用链及唯一派生类型，列出所有生产与测试调用点；
3. 明确新增、迁移、删除清单，确认不存在过渡 alias 或双路径；
4. 一次性迁移生产代码；
5. 迁移仍有价值的测试语义，并增加缺失的状态转换、恢复和错误优先级测试；
6. 先运行受影响模块的定向测试和 Pyright；
7. 运行复杂度报告，人工复核完整调用链是否净简化；若指标真实改善，同步下调 ratchet；
8. 运行 `make check`；
9. 从 monorepo 根目录运行 `pre-commit run --all-files`；
10. 更新本账本的状态、实际删除范围、指标变化和最终裁决。

任何一步发现下列情况即停止并回退该候选设计：

- 新增第二事实源、镜像 state、alias、兼容 wrapper 或第二执行路径；
- validation 或异常优先级只能依赖隐式调用顺序；
- 生产定义、调用边或 record 总量增长，却没有删除完整重复推导；
- 只降低局部函数指标，完整调用链深度、认知负担或跨模块往返没有改善；
- 为通过 legacy test 污染生产边界。

## 全量改进点登记（防遗忘清单）

下面是本轮和此前 Graph/Execution/State 代码审查汇总出的全部后续工作。`G1–G6`
是最初登记的主线设计候选；其中 G1、G5 已完成，G2–G4、G6 已复核为保留。
其余项目是已经完成、明确保留或门禁收口事项。每一项都必须在
本文件留下状态变更和证据，不能只在聊天记录里口头约定。

| 编号 | 范围 | 已识别的改进点 | 目标结构与禁止事项 | 当前状态 |
| --- | --- | --- | --- | --- |
| G1 | `execution/engine/routing.py`、`recovery.py` | 同一 snapshot 重复扫描 Join declaration、pending/historical arrivals、control successor；`RoutingFacts` 与 `_ControlResolution` 同义 | 一个 invocation-local `RoutingFacts`；复用一次 evaluation；不写入 `GraphRunState`，不跨 revision/work item 缓存；保留 live/recovery 错误边界 | `DONE`：删除镜像 record 与重复扫描，优先级、排序、只读映射和全量门禁均已复核 |
| G2 | `Graph.run` fresh/continued admission | mode dispatch、root admission、drive、result、cleanup 的编排容易再次膨胀 | 两种 admission 只在各自语义边界分支，随后共用唯一 `_GraphRun` 生命周期；不引入 `RunContext`、第二 facade 或第二 runner | `K`：#20 已收敛；当前各 admission 后只存在一个 root lifecycle，进一步拆分只会增加转发层 |
| G3 | `execution/graph/compiler.py` | topology/proof/descriptor phase 可能重复建立 successor、gate、node index | 先证明完全相同的静态事实，再一次构建并投影；route-independent proof 与 route-sensitive proof 不合并；不造巨型 `CompilationFacts` | `K`：#32 局部净化已完成；其余 traversal 分属不同 proof 和异常边界，没有相同事实的第二 owner |
| G4 | `execution/engine/recovery.py` | recovery 可能重新实现 live routing、binding/availability 或 Join 规则 | 只复用纯坐标/纯 projection；每个 work item 独立评价；recovery 仍拥有 proof、budget、诊断和 boundary，不复制 runtime runner | `K`：#4–#10 的状态空间证明已复核；G1 后直接复用唯一 routing facts，未发现第二 runner 或规则实现 |
| G5 | `execution/family_driver.py` | root/child fresh owner wiring 曾重复；大方法命中复杂度热点 | `_start_fresh_owner` 统一 fresh start、frame staging、confirm、owner construction 和 construction-failure abort；root/child 保留各自 topology、evidence 与 handoff 边界 | `DONE`：删除两套重复 fresh transaction；continued/partial-commit/cancellation 路径未合并 |
| G6 | 全部 typed record / dataclass | 类型数量高，部分单用途 record 可能只是参数搬运 | 逐个证明无独立不变量且删除后调用链更短才内联；不退回 dict、tuple、`Any`、字符串 tag 或镜像状态 | `K`：专项审计未找到满足全部删除条件的 record |
| G7 | resume / frame / frontier / resource admission | 手工 frontier 模拟、重复坐标解析、重复 canonical order/resource order | 复用 compiled binding、canonical lineage 和 tuple 资源顺序；删除旧 failed/skip/substitution、`ResourceDefinition.order` 等无 owner 字段 | `DONE`：对应历史 #3、#11–#14、#18、#27–#28、#32–#33、#38–#40 |
| G8 | `state/graph_state/*` reducer/validation | reducer、frontier、resource snapshot 分支命中复杂度门禁 | `GraphRunState`/`reduce_graph_run`/resource FIFO 保持唯一 owner；只做能净删除重复校验的改动，不拆原子 transition | `K`：历史 #43–#49 已复核，当前无可证明重构 |
| G9 | Config → execution/state/frame | config cursor/pointer 在 admission、frame、state、recovery 间传播，存在形成第二 config truth 的风险 | `ConfigSnapshotKey`/`GraphConfigCursor` 只由 Config/State owner 持有；execution 只携带同一 immutable pointer，节点通过窄 `Config.bind` 投影；不把 Config 放进业务 DTO 或另建 runtime state | `DONE`：START、routed/direct/Join、resume 和空输出 nested graph 统一从既有 frame/cause 窄投影并检查 Config；未新增 state、DTO 或第二路径 |
| G10 | `failover/contract.py` 泛型适配 | `TypedPortDecorator` 与 uniform rank-2 `PortDecorator` 的静态/运行时边界容易被错误 cast 或重复适配 | 先调研实际 pyright 推断和四个 domain seam；只在 contract owner 证明需要时改动；不加 wrapper、alias 或反射式执行 | `K`：专项 review、generic integrity、decorator boundary 测试和 Strict Pyright 均无 finding；现有泛型边界合理，保留且不编码 |
| G11 | 测试、类型与错误治理 | `tests/loop/support.py:318`、routing 错误优先级、legacy 测试迁移、100% coverage，以及四个文件中曾出现的隐藏 `# pyright` 指令需要持续闭合 | 修复真实测试错误并迁移有价值语义；删除/审计隐藏 Pyright 抑制并保持 strict 0 errors；测试不得反向要求生产兼容路径；为每个 transition/recovery/error boundary 保留确定性测试 | `DONE`：2258 tests、100.00% 分支覆盖率、Strict Pyright 0 errors；源码、测试和示例无隐藏 `# pyright` 指令 |
| G12 | 复杂度/架构/交付门禁 | G1 结构指标有增有减，ratchet 仍按 HEAD；跨包用户改动可能污染结果 | 先人工审完整调用链，再按精确测量更新 ratchet；通过 typecheck、lint、pytest/coverage、complexity、architecture、package-check、pre-commit 才可完成 | `DONE`：精确 ratchet、zero-debt health、`make check`、build/package-check 与 monorepo 全量 pre-commit 全部通过 |
| G13 | 删除范围与历史误触 | `loop/react`、`role` 的删除曾被提出并出现误触风险 | 当前 HEAD 的目录/架构状态作为事实；没有新的明确指令不重复删除、不恢复、不新增兼容壳；架构门禁只反映实际目录 | `CLOSED`：本轮不改动 |
| G14 | 文档与审查证据 | 多份历史 review 容易产生互相矛盾的基线和状态 | 本文件维护当前状态；49 项逐项解释以历史 review 为唯一详细 owner；每次实施同步记录删除清单、指标、测试和门禁 | `ACTIVE`：本次新增总账，后续持续更新 |

### 数量摘要（2026-09-11）

按 G1–G14 的当前状态，数量如下；“保留”表示已完成设计复核并不再机械重构，
“条件再计划”才表示只有发现可证明重复才重新立项：

| 类别 | 数量 | 编号 | 说明 |
| --- | ---: | --- | --- |
| 最后需要收口的代码/门禁事项 | **0** | — | G1、G5、G9、G11、G12 已闭环；G6、G10 复核后判定保留 |
| 明确保留、不再机械重构 | **6 个主线组** | G2、G3、G4、G6、G8、G10 | Root lifecycle、compiler proof、recovery proof、typed record、State 状态机和 Failover 泛型边界均已复核；历史 49 项中对应 `K` 裁决共 **37 项** |
| 条件再计划候选 | **0** | — | 当前没有待批准的条件性重构 |
| 已完成或关闭 | **7** | G1、G5、G7、G9、G11、G12、G13 | 已实施项和全部门禁均闭环；目录删除事项不重复操作 |
| 文档持续维护 | **1** | G14 | 每次治理后更新状态和证据，不是生产代码重构项 |

因此，本轮必须完成的代码治理和门禁事项已归零。当前没有条件性“再计划”候选；明确保留的历史热点是
**37 项**。G14 只是持续维护总账，
不构成生产代码待办。

### 历史 49 个热点的完整状态索引

为避免文档本身出现第二真相，下面只登记编号、当前裁决和本总账的归属；每个符号的
职责、错误优先级和完整调用链证据仍以 [`complexity-49-simplification-review.zh-CN.md`](./complexity-49-simplification-review.zh-CN.md)
为准。这样既不会漏掉任何曾审查的热点，也不会复制两份容易漂移的长描述。

| 编号 | 所在链路 | 当前裁决 | 总账归属 |
| ---: | --- | --- | --- |
| 1–2 | resource admission / task selection | `K`：claim、selector 阶段边界不同 | G8 |
| 3 | frontier preparation | `DONE`：child 阻断先于 input materialization | G7 |
| 4–10 | recovery traversal、child outcomes、quiescent proof | `K`：有界 worklist/递归 proof 是算法本体 | G4/G8 |
| 11–12 | resume admission | `DONE`：删除 failed/skip/substitution 旧路径 | G7 |
| 13–14 | resume input/materialization | `DONE`：共享窄坐标事实，删除单用途 publication helper | G7 |
| 15–16 | live routing / required target | `K`：连续 accumulation/target/diagnostic 区块已足够清楚 | G1 |
| 17 | execution session | `K`：ack、scheduler drain、cancellation 顺序不可隐藏 | G5/G8 |
| 18 | snapshot guard | `DONE`：局部 guard 净化，校验顺序不变 | G7/G8 |
| 19 | `Graph.add_node` | `K`：统一 public overload 是明确 API 边界 | G2 |
| 20 | `Graph.run` | `DONE`：admission 与公共 lifecycle 已分段 | G2 |
| 21–26 | family driver / child / abort | `K`：owner 生命周期和 cleanup 顺序是真实复杂度 | G5 |
| 27–28 | continued root/child admission | `DONE`：owner construction/handoff 复用 | G5/G7 |
| 29–31 | compiler fixed points / joint paths / levels | `K`：proof 算法，不做机械拆分 | G3 |
| 32 | compiler graph assembly | `DONE`：删除重复 canonicalization/遍历 | G3/G7 |
| 33 | resource definition validation | `DONE`：tuple 顺序成为唯一来源 | G7/G8 |
| 34–36 | edge/definition/value validation | `K`：异常边界和 entry owner 已清晰 | G3/G8 |
| 37 | lineage state projection | `K`：canonicality guard 直白且必要 | G5/G9 |
| 38–40 | fence/resume/frame index | `DONE`：共享 canonical lineage，结构先于语义 | G7 |
| 41–42 | context completeness / frame lookup | `K`：recovered 分层和 nominal frame 分支保留 | G7/G8 |
| 43–45 | settlement/resume/reducer | `K`：原子 transition 与唯一 dispatch | G8 |
| 46–47 | resource snapshot/release | `K`：结构校验、durable replay、FIFO prefix 不可合并 | G8 |
| 48–49 | frontier/state validation | `K`：State owner 的状态矩阵不能分裂 | G8 |

### 跨域事项的明确边界

这些事项曾在其他 review 或用户通知中出现，但不应被误认为本轮 Graph 主线的隐性
实施承诺：

- **Failover 泛型适配**：先保持 `failover.contract` 的 `TypedPortDecorator`、uniform
  `PortDecorator` 和 domain error translation 三个边界；只有发现真实类型错误或重复执行路径才动代码。
- **Config 同步改造**：只检查同一 `GraphConfigCursor` 是否从 admission 到 frame/state/recovery
  原子传递；不复制 config payload，不新增 Config state model。
- **Loop/role 目录与架构门禁**：当前 HEAD 是唯一事实；过去的误触删除/恢复不作为本轮待办。
- **测试错误**：遇到 `tests/loop/support.py:318` 或其它失败，先定位真实 owner 和错误优先级，修复测试/生产代码中真正的缺陷；不为旧测试 API 增加生产 alias。
- **全仓其它包（Act/Think/Observe/Events/Logging）**：只记录其对 Graph typed boundary 的依赖和门禁影响，不在没有明确净收益证明时扩展本轮重构范围。

### 下一步执行清单

- [x] 复核 G1 的 validation/error precedence、`required_targets` 排序和 declaration 映射只读性；
- [x] 运行 routing 定向测试、execution/graph API 测试以及全量 pytest/coverage：2258 passed，100.00%；
- [x] 运行 Strict Pyright，并确认源码、测试和示例中没有隐藏 `# pyright` 指令：0 errors；
- [x] 运行 Ruff/format、complexity report/ratchet、architecture、package-check 和 `make check`；
- [x] 完整调用链人工复核通过后，将精确新指标写入 ratchet；未新增 wrapper、alias 或宽 context；
- [x] 从 monorepo 根目录运行 `pre-commit run --all-files`：全部通过；
- [x] 回写状态、删除范围、测试和门禁证据。

## 进度记录

### 2026-09-10：建立总账并完成 G1 第一轮迁移

- 工作树在本轮调研开始前已存在 monorepo 其它目录的用户改动；本轮只在 Kernel 的 routing/recovery/test
  文件和本账本中工作，未回滚或暂存无关改动；
- 已完成 49 个历史热点的状态索引，并把 Graph/Execution/State、Config、Failover、测试和门禁事项登记为
  G1–G14；详细解释仍由历史 review 单一拥有，避免文档双真相；
- 已核对 snapshot guard、live routing、transition admission 和 recovery 的实际输入/异常边界；确认
  `RoutingFacts` 可以作为一次 invocation-local evaluation，且能净删除重复 Join 扫描；G1 状态由 `R` 更新为 `P`；
- G1 当前代码已删除 `_ControlResolution`、重复 pending/declaration 推导和不可达 activation 防御分支，
  并让 recovery 使用统一 `required_targets`；该阶段随后按下节证据完成全量闭环。

### 2026-09-10：完成 G1/G9/G10/G11/G12 收口

- G1 将 Join declaration、pending/historical arrival、control resolution 收敛为一次 invocation-local
  `RoutingFacts` 评价；`GraphRunState` 仍是唯一 durable owner，snapshot/live/recovery 异常边界保持独立；
- G9 让 START、direct/routed/Join、resume override/cached frame、普通 materialization 和空输出 nested graph
  从已有 cause/source frame 窄投影同一 Config，并统一冲突校验；没有业务 DTO、Config 镜像状态或第二执行路径；
- G10 经泛型边界专项复核未发现实际缺口，判定为 `K`；不以 cast、wrapper 或 alias 制造技术债；
- G11 完成测试、类型和错误治理：2258 tests 全通过，分支覆盖率 100.00%，Strict Pyright 0 errors，
  源码、测试和示例中没有隐藏 `# pyright` 指令；
- G12 完成精确 ratchet 和完整交付门禁：最大圈复杂度 45、最大认知复杂度 52、最大调用链深度 17，
  zero-debt health、`make check`、build/package-check 与 monorepo 全量 pre-commit 全部通过；
- `docs/img_v3_02159_a3272a89-3c99-4916-9856-1777e602c7fg.jpg` 的删除由用户确认是预期清理，保留该删除。

### 2026-09-11：完成 G5 fresh owner 收敛并将 G6 判定为 K

- 复核确认 root 与 nested child 原先各自实现同一 fresh 启动事务：`StartGraphRun` 投影、
  `prepare_transition`、`apply_commit_writes`、确认提交、`_GraphRun` 构造，以及构造失败后的 durable
  `AbortGraphRun`；两处的 frame-first 顺序和原始异常优先级存在真实漂移风险；
- 新增唯一内部 owner `_start_fresh_owner`，root 的 `fresh_root` 和 child 的 `_make_child_constructor`
  均通过它完成 fresh admission。净删除两套重复事务和两个局部 cleanup coroutine；没有新增 alias、
  wrapper、第二 runner 或宽 context。root evidence adapter、child topology/coordinate、`_ChildCall`
  handoff，以及 continued/recovery/partial-commit/cancellation 边界保持原样；
- 受影响执行测试 `176 passed`，Ruff、format 和 `family_driver.py` Strict Pyright 通过；隔离 HEAD + G5
  的复杂度报告通过 zero-debt health。相对同一并行改动基线，`top_level_definitions +1`（新增真实 owner）、
  `function_definitions -1`、`nested_function_definitions -2`、`semantic_nodes -47`、`await_points -2`、
  `task_creations -1`、内部调用边 `-8`、跨模块调用边 `-9`、`linear_private_call_chain_links -2`；最大
  圈/认知复杂度和调用链深度不变，已将精确值写入 complexity ratchet；
- G6 对单用途 typed record 逐项审计后未找到可在不增加裸参数、镜像状态或调用链长度的前提下删除者，
  因此判定为 `K`；
- 当前共享工作树含同事未完成的 persistence/Agent/gateway 改动，工作树全量 complexity ratchet 不能
  代表 G5；本项不暂存、不回滚、不改写这些并行改动，待整合后再执行仓库级全量门禁。

### Kernel 持久化分阶段工作

后端无关的 Kernel 持久化与 `agent.py` 恢复装配，按
[`Kernel 持久化闭环实施计划`](./kernel-persistence-implementation-plan.zh-CN.md) 独立执行。
阶段状态、验收结果和用户 review 记录只在该计划维护；持久化不属于本轮代码治理范围，本账不复制其进度，
也不把 G1/G9 的完成状态记作持久化交付。
