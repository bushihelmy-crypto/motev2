# Graph 内存语义收口实施计划（P2-M / P3-M）

状态：**仅内存；不实施持久化。基线为 Graph M4 提交（`6f11193`，2026-09-03）：P2-M、P3-M1、P3-M2、
P3-M3、P3-M4 已落地；P3-M5 的组合测试、worker lease 收口和 P1 取消边界修复已落地，并已提交为 `5abe5d2`。
当前工作树中的并行改动不计入本阶段。**

本文是 Kernel 当前 Graph 内存语义的审核后实施计划，不是“所有列出的能力都尚未开始”的状态声明。已落地的能力只做
事实核对和组合回归；没有真实缺口就不重写生产代码。

## 0. 审核决定和不可降级的原则

### 0.1 范围决定

本文件只覆盖进程内 Graph 执行、同一进程内的 transient continuation、typed State transition、routing、
materialization、scheduler、family driver 和结果投影。

本文件**不做任何持久化**，包括但不限于：

- 不修改 `mote-infra/persistence`，不增加 persistence/recovery adapter、Store facade 或 storage read path；
- 不定义或实现 durable codec、RecoverySnapshot、read/reconcile、CAS/commit identity、retention/release 或跨语言协议；
- 不承诺进程崩溃后的自动继续、跨进程 state-only value recovery、crash-safe、exactly-once 或 provider side effect 语义；
- 不开放公开 durable `Graph.feedback(...)` API，也不为它预留一条内存专用执行路径。

`Graph.Commit` 是现有的**调用方注入的进程内确认接缝**。省略它时，当前实现确认 reducer candidate 以便继续同一进程的调用；
注入测试 double 时只验证调用顺序、exact candidate 和 State 不变性。两者都不是本文件的持久化实现或崩溃恢复证明。

`Graph.run(state=...)` 在没有 continuation 时只接收调用方提供的 State，并从空的 `ScopedFrameIndex` 开始做控制准入；需要的
graph input、publication、resume input 或 child boundary 若不在当前调用提供的 transient evidence 中，就在 callable 前以 typed
value-unavailable/admission 错误停止。它不是从存储读取值的重启入口。

### 0.2 评审原则

以下原则同时约束设计、代码迁移、测试、示例和文档：

1. **唯一真相**：每项规则、状态和事实只有一个权威 owner；不建立别名、镜像状态或第二执行路径。
2. **复用基础设计**：相同规则集中复用已有 compiler、planner、routing、reducer 和 typed frame；不同领域保留自己的异常边界。
3. **逻辑最简**：按完整调用链总复杂度判断设计，不机械拆函数、不增加转发 helper、不用宽 context 掩盖复杂度。
4. **一次成功**：先冻结完整调用链、不变量、迁移和删除范围，再一次性迁移生产代码、测试和示例；不留下临时双路径。
5. **零技术债**：拒绝无意义抽象、重复路径、兼容层、隐性状态和未明确归属的规则。
6. **代码优美**：结构直接表达 owner、状态转换、异常边界和行为顺序。
7. **复杂度门禁是高召回雷达**：命中只触发真实调用链复审；指标没有设计裁决权，不能驱动假抽象、薄转发或状态机碎片化。
8. **满足全部门禁**：类型、测试、覆盖率、复杂度、架构和 pre-commit 必须全部通过；门禁通过不等于设计自动合格。
9. **不污染生产代码以兼容 legacy test**：只迁移仍有价值的行为语义、错误优先级和恢复边界；不保留旧 API alias、wrapper 或兼容执行路径。

最终判断顺序固定为：先判断设计和代码是否足够简单、清晰且唯一，再用门禁证明没有回归，不能反过来用门禁替代质量判断。

## 1. 当前 HEAD 事实台账

| 能力/规则 | 当前状态 | 事实 owner / 证据 | 本文件动作 |
| --- | --- | --- | --- |
| 唯一公开 `Graph` facade、immutable `CompiledGraph` | 已实现 | `execution/facade.py`、`execution/graph/compiler.py` | 复用，不增加 facade/runner |
| `GraphRunState`、typed cause、settled activation ledger、pure reducer | 已实现 | `state/graph_state/model.py`、`frontier_model.py`、`reducer.py` | 继续扩展同一 State，不建 loop State |
| direct self-feedback 的 seed/repeat、immediate predecessor、terminal exit | 已实现（内部 typed 路径） | `FeedbackInputBinding`、compiler/routing/materialization；`test_feedback_compiler.py`、`test_feedback_runtime.py` | 只补真实缺口和组合回归 |
| 公开 facade 接受 feedback declaration | 关闭 | `normalize_facade_input_bindings()` 明确拒绝 `FeedbackInputBinding` | 本文件不新增公开 feedback API |
| 普通 direct fan-out、多消费者 | 已实现 | `DirectEdge`、scheduler/session、fan-out 示例和 routing tests | 做顺序/资源组合审计 |
| conditional route 选择及 route/cause admission | 已实现 | `ConditionalEdge`、`SelectGraphRoute`、compiled activation gates | 复用现有 gate/cause 模型 |
| 非循环 Join、partial progress、Join-to-END | 已实现 | `JoinEdge`、`GraphJoinProgress`、routing/reducer tests、`parallel_detectives`/`fanout_terminal` | 做组合回归，不另造 Join runner |
| Join source occurrence identity | 已实现（可证明的循环/可重复 source） | `GraphJoinOccurrenceIdentity`、`CompiledJoin.source_target_offsets`、routing/reducer/admission tests | 复用；无法证明 occurrence 的形状继续 compile fail |
| multiple feedback binding | 已实现（内部 typed 路径；公开 facade 仍关闭） | `CompiledActivationRules`、compiler/materialization/routing、`test_feedback_compiler.py`、`test_feedback_runtime.py` | 复用固定 repeat source；不扩展普通 data cycle 或 Graph failure retry |
| nested graph、child boundary、scope 隔离、family driver | 已实现（含新语义组合） | `family_driver.py`、nested/family/resource/Join tests | 复用唯一 family owner；不增加 child 专用 runner |
| resource admission、并发 completion、failure/interrupt 优先级 | 已实现 | resource reducer、session/scheduler、frontier status tests | 做组合和顺序回归 |
| state-led 调用的值恢复 | 仅支持调用方提供的 transient evidence | `Graph.run(state=...)` + `ScopedFrameIndex`；缺值会 fail closed | 不增加 storage reader 或隐性缓存 |
| Graph failure retry/skip/override | 已关闭 | Failed 是 terminal；Failover 是唯一 retry owner | 不恢复旧 legacy 生产路径 |
| durable persistence/restart/codec/retention/conformance | 不属于本文件 | 当前 Kernel 没有 concrete Store/value reader | 明确排除，不作为本计划交付物 |

因此，P2-M/P3-M 的“阶段”是内存语义的复核、一次性迁移和质量证明；当前列出的内存能力已经落地，后续若有变更仍必须
沿同一 owner 和调用链演进，不另起兼容路径。

## 2. 唯一调用链和 owner

所有 live execution、transient continuation 和 state admission 必须能落回同一条调用链：

```text
Graph.run(...)
  -> Graph._compile()
  -> fresh_root(...) / admit_continued_root(...)
  -> _GraphRun.drive_quantum()
  -> GraphExecutor.prepare(StepRequest)
       -> prepare_superstep(...)
            -> plan_tasks(...)
            -> prepare_frontier(...)
            -> materialize_node_input(...)
            -> resource admission / child projection
  -> commit_transition(ClaimGraphExecution)
  -> GraphExecutor.issue_session(...)
  -> GraphExecutionSession.next(...)
       -> TaskScheduler（普通 callable 的唯一调用点）
       -> SettleGraphNode
       -> commit_transition(...)
            -> reduce_graph_run(...)
            -> Graph.Commit（若注入）
            -> 确认后替换 `_GraphRun` 的 State/frame
  -> 已结算 frontier 的 resolve_routing_facts(...)
       -> project_routing_facts(...)
       -> AdvanceGraphFrontier / CompleteGraphFrontier / AbortGraphRun
```

owner 不变：

- declaration/compiler 只产生 immutable topology、materialization plan、publication descriptor 和 activation gate；
- `GraphRunState`/`GraphRunCommand`/`reduce_graph_run` 是状态事实和转换的唯一 owner；
- routing 保留 `(target, cause)` candidate，验证唯一 gate 后才投影 activation；
- `ScopedFrameIndex` 只保存本次进程调用可见的 typed frame，不是第二个 State 或长期缓存真相；
- family driver 递归驱动 parent/child，但不创建 nested 专用 runner；
- recovery/preflight 只做无副作用 admission proof，不调用节点、不提交第二份状态，也不成为第二 runner。

State 不读取 `CompiledGraph`；Execution 可以根据 compiled topology 创建 command，但不能直接修改 State。任何新事实都必须放进
已有 `GraphRunState` 和同一 reducer/commit 边界，不能新增 `feedback_state.py`、`join_runner.py`、第二 scheduler 或 generic
`utils/common/shared/helpers` 包。

## 3. 不变量和异常边界

### 3.1 激活、cause 和 publication

1. 首轮 frontier 的每个 activation 只能带 `StartActivationCause`；后续 activation 只能带 canonical、distinct 的
   `RoutedActivationCause`。
2. cause 中的每个 `ActivationReference` 必须引用同一 run 的已成功、已选 route 的 predecessor；direct self-feedback 还必须
   精确指向紧邻上一轮的 target activation，不能扫描“最新 publication”、回退 seed 或比较一个宽松的 `superstep` 范围。
3. 一个 producer activation 只有一个 canonical publication；多个 consumer 通过同一 publication coordinate 读取，不复制 per-binding 值。
4. 同一 target 在同一个 frontier occurrence 只能有 0 或 1 个 activation。routing 在 collapse 前必须保留所有 candidate；零条或多条都 fail closed。
5. 普通 `NodeOutputRef` 仍表示同一 activation 内的数据依赖，普通 data cycle 继续在 compiler 拒绝。跨 activation 的回环只能由已批准的 typed declaration 表达。

### 3.2 Join 和 scope

1. Join 只表达控制上的“等齐”，不自动合并业务值；目标节点所需值必须由显式 typed input binding 读取。
2. 非循环 Join 的 `GraphJoinProgress` 只保存已结算且有完整 `ActivationReference` 的 partial arrival；新 arrival 只能来自当前 frontier 的成功 settlement。
3. partial progress 必须原样保留，除非同一批事实完成并明确消费；重复 source、foreign/stale route、伪造历史 arrival 和错误 target 全部 fail closed。
4. `GraphJoinOccurrenceIdentity` 由 compiler 根据 Join 定义、run 和目标 superstep 唯一投影；可证明的 control-cycle Join 和可重复
   source 可以执行，跨 occurrence 混入、无法唯一归属的跨轮 Join 仍必须 compile/runtime fail closed。
5. 跨 scope routing 只能经过现有 parent/child boundary；不能共享另一 scope 的 frame、State 或隐藏缓存。

### 3.3 生命周期、提交和异常

1. 失败节点是本次 Graph 的 terminal Failed 事实；Graph 不负责 retry/skip/failed override，Failover 通过显式新 activation 表达下一次 Port attempt。
2. frontier 终态优先级保持 `Pending > Failed > Interrupted > Settled`；失败不会被 Join 静默吞掉，普通 sibling 的已确认 settlement 也不能被回滚。
3. `commit_transition()` 固定先生成 reducer candidate，再调用可选 `Graph.Commit`，只有 exact candidate 确认后才替换 Python State 和 frame；异常、取消或非 exact 返回不得推进内存快照。
4. compiler 负责拓扑/声明合法性，State validator 负责结构、生命周期、revision、token 和本地 cause 形状，runtime admission 负责 compiled graph membership、gate、publication/frame 可用性；不复制 owner，也不强行把所有领域异常压成一个错误类型。
5. compile 错误、snapshot/admission 错误、routing 错误、value unavailable/publication 错误和 execution limit 错误保持现有 typed 边界；先做最小身份/信任校验，再做值可用性校验，不能泄漏 payload 或靠异常顺序猜测另一 owner 的规则。

## 4. 分阶段工作（只含内存语义）

### P2-M：P1 基线收口（已完成）

P2-M 不是重新实现 direct loop，而是确认当前内部路径没有真实缺口：

- 首轮只读 `GraphInputRef` seed，后续只读 exact immediate predecessor publication；
- `feedback` route 产生下一轮，`terminal` route 使用当前 publication 完成；
- 缺 predecessor、错 route、错 superstep、旧 publication、input override 在 callable 前失败；
- feedback failure 是 terminal，不产生下一轮；`max_supersteps` 只做安全上限；
- commit 异常、非 exact candidate、重复确认和取消不改变已确认 State；
- continuation 的 frame/child evidence 只在当前进程使用，state-only 无 evidence 时明确返回 unavailable/admission 错误。

以上行为由当前 P1 compiler/runtime/routing/State 测试覆盖；本阶段未扩展普通 data cycle，也未把 internal
`FeedbackInputBinding` 变成公开 alias/wrapper。

### P3-M1：fan-out、conditional、非循环 Join 的组合收口（已完成）

这些基础能力已在当前 HEAD 存在，本阶段不建第二路径，只做统一审计和缺口补测：

- direct、conditional、Join 都继续通过 compiled activation gates/candidate 流程；
- `A -> B/C` 的 sibling 各自独立推进；无控制依赖的 sibling 不因另一个分支等待资源、child 或 completion 而被全局阻塞；
- B/C 汇合到 D 必须显式 `JoinEdge((B, C), D)`，除非 compiler 能证明同一 source 的 conditional routes 互斥；不确定即拒绝；
- partial Join 不调度目标，补齐最后 source 后目标只激活一次；`Join(..., END)` 等齐后才完成；
- completion 顺序改变时，逻辑 cause/publication 集合和结果投影保持等价，不要求 revision 历史逐项相同；
- direct、conditional、Join candidate 可能同时成立且没有同一显式 Join 时，compile/runtime 都 fail closed，不先用 set 去重。

### P3-M2：条件路由扩展和 multiple feedback（已完成）

现有 generic conditional route/route admission 继续复用；multiple feedback 已按固定 typed 声明落地：

- 一个 target 可以有多个 typed `FeedbackInputBinding`，每个 binding 仍只有一个固定 repeat source；
- 不把“不同 route 读取不同 source”偷偷编码成 map；若需要该语义，先另行冻结 typed declaration 和异常边界；
- feedback cause、普通 routed cause 和 Join cause 继续共享 `RoutedActivationCause`/candidate 流程；materialization 只按 cause 精确选 publication；
- compiler 必须证明 route、input descriptor、publication selection 和 target gate 的唯一性；不能让 runtime 选择“第一条”或静默合并重复 activation；
- 普通 data cycle、条件路由重试和 Graph failure retry 仍关闭；反馈图与额外 fan-out/control source 的组合按当前
  单目标约束 compile fail closed，不通过 runtime 猜测或静默合并放行。

### P3-M3：Join occurrence identity 和 cyclic Join（已完成）

`GraphJoinProgress` 现在以 `GraphJoinOccurrenceIdentity` 区分同一 Join 的不同轮次；compiler 只放行能够推导唯一
source-to-target offset 的形状，无法证明的形状继续拒绝：

1. occurrence identity 包含 Join 定义身份、run 和目标 superstep，不使用 source node id 或独立 loop counter 冒充身份。
2. 每个 arrival 保留完整 source `ActivationReference`（run、superstep、node、selected route）；partial、完成消费、admission 和 routing 使用同一 key。
3. compiler 通过 source-to-target offset 和 activation cohort 证明 source 属于哪个 occurrence；无法证明就拒绝。
4. runtime 对跨 occurrence 混入、重复 source、过期 arrival、错 target 或多 candidate fail closed；一个 occurrence 只产生一个目标 activation。

这一阶段仍然只做进程内 State/frame/continuation 语义，不引入任何 durable Join evidence 或 storage recovery。

### P3-M4：nested/family 与新语义组合（已完成）

普通 nested graph、child family、scope identity、resource waiter 和 child boundary 已与 P3-M2/M3 统一：

- parent sibling、普通 callable、resource waiter 和 child 按各自 activation 推进，不建立 child 全局栅栏；
- child 完成只结算自己的 parent activation，child output 通过现有 boundary/publication projection 回到 parent State；
- parent Join 只接受已确认的 child boundary arrival，并保留完整 parent activation reference；
- 同一 child definition 被多个 sibling 使用时 scope/run identity 不串线；
- child failure、interrupt、cancel 的优先级与普通 node 一致，不能被 Join 吞掉；
- family fan-in 中一个 worker 普通失败时，先等待同一 family 的其他 worker 停止，再沿现有 child owner 递归 fence 所有仍持有
  execution lease 的 scope；只清除 lease，不伪造 `FAILED`/`ABORTED` terminal State，并原样保留首个确定性的普通异常；
- 调用方取消仍走 invocation 的 fence + abort 边界；commit-origin cancellation 不做未经确认的补偿 fence，保留权威 active lease
  供后续恢复；节点主动抛出的 node-origin cancellation 同样只结束当前调用，不提前 fence 已确认 lease；
- 若组合需要 durable child evidence 才能判断完成，当前实现明确返回 admission/value-unavailable 或 compile fail closed，不添加隐式缓存。

### P3-M5：质量收口（实现与 P1 修复已落地，已提交 `5abe5d2`）

对 P2-M 至 P3-M4 的实际调用链做一次组合审计，至少覆盖：

- fan-out + conditional + Join；
- multiple feedback 与 fan-out 的边界（反馈图带额外 fan-out/control source 时按单目标约束 compile fail closed）；
- Join + resource waiting；
- Join + nested child；
- branch completion order permutations；
- duplicate/stale/foreign cause、非法 target、非法 route；
- branch failure 与 interrupt 混合；
- `max_parallel_tasks`、resource admission、execution fence；
- terminal output publication、output unavailable abort 和 state-only 无 evidence。

本阶段只收口共享规则、异常边界和测试；没有为某个组合增加特例分支。新增回归集中在
`test_family_driver_local_ownership.py`、`test_resource_protocol.py`、`test_graph_api.py` 和
`test_graph_examples.py`，并覆盖未启动 worker 清理、失败/等待/普通 sibling 顺序、fan-out/conditional/Join
以及 cyclic Join + nested child；另覆盖 parent/child/grandchild family 的普通失败递归 fence、调用方取消和 commit-origin
cancellation 的 lease 边界。

## 5. 允许修改的代码边界和删除范围

修改前必须先对照第 1、2 节；已满足的部分不重复改写。

| owner/层 | 当前位置 | 允许的内存工作 |
| --- | --- | --- |
| declaration / facade boundary | `execution/graph/ports.py`、`execution/facade.py` | 只增加已冻结的 typed declaration；不加 alias、wrapper、第二 public entry 或 durable API；当前 public facade 对 feedback 的拒绝保持有效 |
| compiler/topology | `execution/graph/compiler.py`、`topology.py`、`validation.py` | gate/candidate 唯一性、multiple feedback admission、occurrence/cyclic Join admission；静态可证明的规则只生成一份 |
| State identity/model | `state/graph_state/identity.py`、`model.py`、`frontier_model.py` | 只在 occurrence 设计冻结后扩展同一 `GraphRunState`；不建 parallel state 或 loop counter owner |
| command/reducer/validation | `state/graph_state/command.py`、`execution_transitions.py`、`validation.py` | partial 保留/消费、cause/occurrence 校验；保持 pure transition 和现有错误边界 |
| materialization/routing | `execution/engine/resume_input.py`、`routing.py`、`frontier.py` | 精确 publication selection、candidate 保留、唯一 target activation；不扫描最新值、不 seed fallback、不先去重 |
| scheduler/session/family | `execution/engine/planner.py`、`session.py`、`superstep.py`、`family_driver.py` | sibling/Join/nested 组合和资源顺序；继续使用唯一 executor/session/family driver |
| result/output | `execution/result.py`、`engine/admission.py` | 只补显式 typed input/output 投影和 terminal diagnostics；不把值复制进 State |
| persistence/infrastructure | `mote-infra/persistence` | **禁止修改；不新增 Port、adapter、codec、reader、retention 或 conformance 文件** |

禁止新增或保留：`feedback_state.py`、`join_runner.py`、第二 reducer、第二 scheduler、长期内存缓存、兼容 alias/wrapper、为 legacy test
保留的生产分支，以及仅用于绕过复杂度指标的薄转发 helper/宽 context。

一次性迁移的删除清单必须在代码变更前冻结：旧声明路径、旧测试入口、旧示例和无价值 helper 一并删除；仍有价值的 legacy 测试只迁移语义、错误优先级和恢复边界，
不让生产代码反向适配测试。

## 6. 测试矩阵

### 6.1 当前已有的基线回归

- `tests/execution/graph/test_feedback_compiler.py`、`tests/execution/test_feedback_runtime.py`：P1 direct feedback 白名单、terminal/failure、immediate predecessor、cause state；
- `tests/execution/engine/test_resume_input_contract.py`：精确 publication、无 seed fallback、override 拒绝和 value unavailable；
- `tests/execution/engine/test_routing.py`：fan-out、conditional、非循环和循环 Join、partial progress、candidate 唯一性、ghost/foreign/stale admission；
- `tests/state/graph_state/test_execution_transitions.py`、`test_state_validation.py`：pure reducer、settlement、cause、Join 保留/消费、失败优先级；
- `tests/execution/graph/test_join.py`、`test_compiler.py`、`test_compiler_contract.py`：Join 形状、gate 共存、重复/可重复 source、occurrence offset 和无法证明形状的 cyclic Join 拒绝；
- `tests/execution/test_graph_api.py`、`test_family_driver_local_ownership.py`、`tests/execution/graph/test_nested_graph.py`：唯一 facade、continuation、scope/child boundary 和 family ownership；
- `tests/execution/test_graph_examples.py`：公开 fan-out、Join、nested、conditional、interrupt 等示例行为。

### 6.2 本轮已补齐的内存组合测试

- multiple feedback binding 的各自 repeat source、同一 target 的多 cause 在 collapse 前拒绝；
- occurrence 不同的 arrival 不得互相拼接，同 occurrence 只产生一次 target activation；
- fan-out/Join/conditional/feedback 与资源等待、并发顺序、nested child 的排列组合；
- duplicate/stale/foreign occurrence、错 target/route/superstep 在 callable 前 fail closed；
- child boundary 只结算一次，scope/run identity 串线被拒绝；
- `max_parallel_tasks` 改变调度顺序但不改变可证明的逻辑结果；
- live execution、transient continuation 和 admission 对同一快照采用同一 candidate/cause 语义；
- 没有 transient frame/evidence 时明确返回 value unavailable，而不是隐式重跑、补 START 或访问“最新值”。

其中新增的公开/组合回归包括：

- `test_fanout_conditional_branches_and_join_share_one_activation`；
- `test_cyclic_join_accepts_nested_children_at_each_occurrence`；
- `test_active_child_and_ordinary_sibling_are_driven_by_one_frontier_owner`；
- `test_multiple_active_children_are_driven_without_a_serial_family_barrier`；
- `test_failed_child_does_not_cancel_active_child_or_pending_sibling`、
  `test_awaiting_child_and_ordinary_sibling_reach_one_family_boundary` 和
  `test_failed_child_cleanup_waits_for_ordinary_sibling_before_aborting_awaiting_child`；
- `test_worker_driver_closes_unstarted_workers_and_handles_an_empty_batch`。
- `test_parent_worker_failure_fences_active_descendants_without_terminalizing_them`、
  `test_child_worker_failure_fences_an_active_parent_without_terminalizing_it`、
  `test_child_worker_failure_fences_a_sibling_child_without_terminalizing_it` 和
  `test_commit_origin_cancellation_preserves_active_family_leases`。
- `test_root_node_origin_cancellation_rethrows_without_invocation_abort` 和
  `test_root_node_origin_cancellation_preserves_active_child_lease`：验证节点主动取消不产生额外 fence/abort，且保留 root 与
  active child 的 authoritative lease。

每个状态转换、恢复边界、异常优先级和公开行为都必须有确定性测试；测试不得把 in-memory commit double 写成 durable evidence。

## 7. 一次成功的实施顺序和交付物

阶段顺序是设计和验收闸门，不是允许长期并存的双路径：

```text
设计冻结（调用链 + owner + 不变量 + 异常 + 迁移/删除清单）
       ↓
P2-M 基线复核与组合回归（完成）
       ↓
P3-M1 fan-out/conditional/非循环 Join 收口（完成）
       ↓
P3-M2 multiple feedback（仅内存，完成）
       ↓
P3-M3 occurrence identity/cyclic Join（仅内存，完成）
       ↓
P3-M4 nested/family 组合（完成）
       ↓
P3-M5 统一质量收口（实现与 P1 修复已落地，已提交 `5abe5d2`）
```

每个获准阶段必须在同一个变更中交付：

- production code、State/reducer/routing/materialization 变更；
- 对应 typed tests、组合测试和仍有价值的 legacy 测试迁移；
- 示例和调用链文档同步更新；
- 旧路径、兼容层和无意义抽象同步删除；
- 阶段性门禁证据和未完成项的准确状态。

任何阶段都不得先落一个“临时 runner/alias/cache”，再用后续阶段承诺删除。

## 8. 质量判断和全部门禁

交付判断按以下顺序执行：

1. **设计先行**：调用链只有一个，State、compiler、routing、materialization、session、family 和异常边界 owner 清楚；
   target activation 基数、Join occurrence 和失败终止规则可由结构直接读出。
2. **代码复核**：沿真实跨模块调用链复审复杂度热点；若指标命中但调用链合理，记录理由，不拆出假 helper；若发现重复 owner、隐性状态或第二路径，先修设计。
3. **行为证明**：运行正常、错误、恢复/continuation、并发顺序、资源、nested 和结果投影测试；测试覆盖率必须保持项目要求。
4. **门禁证明**：在 Kernel 目录运行 `make check`，并在 monorepo 根目录运行 `pre-commit run --all-files`；类型、测试、100% coverage、复杂度、架构、构建和 pre-commit 任何一项失败都不能宣称完成。

门禁输出只记录实际运行结果和工作树条件；若全仓存在不属于本任务的改动而未能复测，必须明确写“未复测/有条件”，不能引用历史输出冒充当前证据。

本阶段的可复核证据是：在只包含 Graph 已提交代码及本阶段测试/文档的隔离快照中，`make check` 的各目标（lint、strict
typecheck、complexity ratchet/health、测试、build 和 package-check）均通过；测试为 1381 passed、100% coverage。当前混合
工作树的 1388 个测试同样通过，pre-commit 仅因并行改动使全树 complexity ratchet 超出本阶段 ratchet 而失败，该失败不改变
Graph 快照的结论，也不允许用它抬高本阶段以外的阈值。

## 9. 明确排除项（再次确认：本文件不做持久化）

以下内容不是 P2-M/P3-M 的任务、验收标准或未来阶段的隐含承诺：

- 任何数据库、KV、Cloudflare/local Rust adapter、persistence Port 或 Store；
- durable State/result/publication/input/child evidence、原子后端事务、CAS、commit identity 或 acknowledgement-lost reconcile；
- 跨进程/跨语言 codec、schema/version、bytes limit、secret-safe persistence error、retention/release 和 conformance；
- 进程崩溃自动恢复、从 State 反查 concrete value、exactly-once provider side effect；
- 公开 durable feedback API、Graph failure retry、失败后 skip/override/兼容 wrapper。

未来若另立持久化项目，它必须在不改变本文唯一 owner、cause、Join、occurrence 和 activation 基数的前提下单独评审；本文件不为该项目修改生产代码，
也不以其进度作为当前内存语义的完成条件。

## 10. 最终状态结论

当前仓库可以把普通 Graph 的内存调用链作为唯一执行底座：P1 direct feedback 的内部 typed 语义、multiple feedback、fan-out、conditional、
occurrence-aware cyclic Join、nested/family、资源和 failure/interrupt 边界均已有代码与组合测试支撑。反馈图仍保持单目标、固定
repeat source 的编译边界；普通 data cycle、条件路由重试、Graph failure retry 和所有无法证明 occurrence 的形状继续关闭。

本文件的完成含义只有：**范围严格限定为不持久化的内存语义，唯一 owner/调用链和防御边界保持清晰，列出的 P2-M 至 P3-M5
内存工作在复审通过且门禁证据更新后，才可标记为完成。**它不等于任何 durable recovery 能力已经存在。
