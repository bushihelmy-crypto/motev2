# Graph 内存语义 P3-M1 / P3-M2 当前代码评审（2026-09-03）

状态：**通过（P3-M1 两个初审阻断项已修复；P3-M2 继续通过）**

评审基线：`2e51410`（P1 ledger compiled-graph admission 收口）。

评审对象：

- `152a999`：P3-M1 mutually exclusive branch Join、非循环 Join 的分支顺序与资源组合；
- `01a708f`：P3-M2 multiple feedback binding 的 compiler、compiled topology、routing 与 materialization；
- 当前 HEAD `ef88a7c`：上述能力经过同一 reducer candidate / compiled-frontier admission / commit 边界后的实际生产调用链。
- 当前工作树：只在唯一 compiler proof 中补齐 START-plus-incoming repeatability 与 route-domain exactness，并同步增加两个
  compiler 回归及复杂度 ratchet；没有修改 routing、State、reducer、materialization 或 public API。

本文是新的独立评审台账。旧评审、实施计划以及工作树中的跨项目 Cloudflare 迁移改动均不修改、不覆盖、不归因；
审查过程中只记录由当前代码、可达调用链、确定性反例或实际门禁输出支持的结论。

## 1. 最高判定标准

1. 先判断设计和代码本身是否简单、清晰且只有一个 owner，再以门禁证明没有回归；
2. compiler、planner、routing、reducer、typed frame 和 family driver 的现有规则必须复用，不允许阶段专用 runner、
   parallel State、兼容 alias 或第二执行路径；
3. 复杂度指标只触发完整调用链复审，不驱动薄 helper、宽 context 或状态机碎片化；
4. 只把能改变真实生产行为、破坏冻结契约或形成实际技术债的问题列为 finding； hostile 内部对象、未开放能力和纯指标
   命中不能单独构成缺陷；
5. 没有真实问题就停止，不做代码攻击式扩张。

## 2. 阶段边界与调用链

### 2.1 P3-M1

P3-M1 不新增 Join runner。它只允许 compiler 证明来自同一上游 route decision 的互斥 activation gates，并继续复用
`CompiledGraph.transition.activation_gates`、`RoutedActivationCause`、`GraphJoinProgress`、routing candidate collapse、
resource admission 和唯一 reducer。独立或可共存的多条 target gate 仍必须 compile fail closed；cyclic/repeatable Join source
在 P3-M3 occurrence identity 落地前仍必须关闭。

### 2.2 P3-M2

P3-M2 仍限定为单 callable target、单固定 repeat producer（target 自身）、一条 feedback route、一条 terminal route和
单 graph output，只把一个 target 的 feedback input 从一个扩展为多个。每个 input 由独立 immutable
`CompiledActivationRule` 指向固定 initial/repeat source；所有 binding 共用同一个 State-owned activation cause、route 和
publication frame，不增加 per-binding route map、loop State 或缓存。

### 2.3 当前生产调用链

```text
Graph.run()
  -> compile_graph()
       -> route-requirement / activation-gate proof
       -> CompiledActivationRules + MaterializationPlan
  -> _GraphRun.drive_quantum()
  -> GraphExecutor.prepare()
       -> plan_tasks() / materialize_node_input() / resource admission
  -> commit_transition()
       -> reduce_graph_run()
       -> frontier_admission_error(compiled graph, candidate)
       -> Graph.Commit(exact candidate)
  -> GraphExecutionSession / TaskScheduler
  -> resolve_routing_facts()
       -> the same cause, route, Join and activation-gate facts
```

## 3. 审查台账

| 项目 | 当前状态 | 证据 |
| --- | --- | --- |
| P3-M1 互斥 gate 的静态证明只由 compiler 持有 | **复审已核对修复** | `_RouteRequirementProof` 区分安全 over-approximation 与 exact Join proof；分支局部条件被消去或多维相关性丢失时不再用于证明完整 source set，旧反例在 callable 前 compile fail closed |
| P3-M1 可共存 gate、partial Join、cyclic/repeatable source 保持 fail closed | **复审已核对修复** | repeatability proof 把同时具有 incoming gate 的显式 entry 纳入唯一 seed set，并沿既有 gate 传播；旧反例在 callable 前拒绝 |
| P3-M1 分支完成顺序、资源等待不改变 Join 逻辑结果 | 已核对通过（限 compiler 正确准入的拓扑） | left/right 两种选择、两种 completion order、共享 resource waiter 的公开回归均通过；routing 仍按 canonical cause/Join progress 投影 |
| P3-M2 每个 feedback binding 保留固定 source/type/selection | 已核对通过 | compiler 为每个 target input 生成 immutable `CompiledActivationRule`；逐项验证 initial/repeat exact type、target-owned repeat output 和 `RELATIVE(1)` |
| P3-M2 cause、route、publication 和 materialization 不产生第二路径 | 已核对通过 | 所有 binding 复用同一 `RoutedActivationCause` 和 target publication frame；materialization 逐 rule 读取固定 output，不扫描 latest、不回退 seed |
| candidate 在任何外部 commit 前通过同一 compiled admission | 已核对通过 | 当前 HEAD 的 `commit_transition()` 统一执行 reducer 和 `frontier_admission_error()`，通过后才构造并调用 `Graph.Commit`；所有生产调用点显式传入所属 compiled graph |
| public/durable/nested/cyclic 等未开放能力没有被意外放开 | 已核对通过 | facade 仍拒绝 `FeedbackInputBinding`；feedback 仍拒绝 nested/multi-node/Join control；cyclic Join occurrence 仍关闭；未新增 persistence/read path |

## 4. Findings

### P1-1：显式 START entry + 入边形成的可重复 Join source 未被 compiler 拒绝

**初审状态：`ef88a7c` 可通过公开 `Graph` facade 稳定复现。当前工作树复审：已修复。**

代码位置：

- `src/mote_kernel/execution/graph/compiler.py` 的 entries 计算与 `_reject_repeatable_join_sources()`；
- `src/mote_kernel/execution/engine/routing.py` 的 Join arrival/progress 投影。

当前 repeatability 只以 control-cycle reachable nodes 为种子，再沿 activation gates 传播；它没有把“既属于显式 START
entries、又拥有一个后续 activation gate”的节点视为可重复。该节点会先在 superstep 0 由 START 激活，再由其入边在后续
superstep 再激活。普通 activation 有 `(run_id, superstep, node_id)` 可以区分两次 occurrence，但 P3-M3 前的
`GraphJoinProgressKey` 仍只有 `(sources, target)`，不能为 Join 安全归属这两次 arrival。

公开最小拓扑：

```text
START -> s
a     -> s
(s, other) -> Join(target)
```

其中 `a`、`other` 是 automatic entries。当前 compiler 接受该图；公开 `Graph.run(Graph.values())` 的实际调用序列为
`['a', 'other', 's', 's', 'target']`，随后第二个 `s` 留下没有任何后续 `other` 可以补齐的 partial Join，抛出：

```text
RoutingDeadlockError: partial join progress has no next task able to complete it
```

这不是要求提前实现 P3-M3 occurrence identity。最小正确收口应仍在同一个 compiler repeatability proof 中，把“entry 且有
incoming activation gate”的节点作为 repeatable seed，并继续复用现有下游传播，然后在 Join source admission 统一拒绝；
不要在 routing 增加特例、清空 partial progress 或建立第二 occurrence 状态。

当前补丁正按该边界收口：`_reject_repeatable_join_sources()` 接收 compiler 已确定的 canonical entries，把其中仍有 incoming
activation gate 的节点加入原 repeatable set，再复用原有下游传播和 Join source admission。公开 facade 重跑相同形状得到
`GraphValidationError("a join source can have more than one activation occurrence")`，任何 callable 均未执行；回归还覆盖了
该 repeatability 向 acyclic descendant 的传播。没有新增 State 字段、runtime 清理或 occurrence 伪实现。

### P1-2：互斥分支的 route proof 丢失局部必选条件，compiler 接受只会产生 partial Join 的路线

**初审状态：`ef88a7c` 可通过公开 `Graph` facade 稳定复现。当前工作树复审：已修复。**

代码位置：

- `src/mote_kernel/execution/graph/compiler.py` 的 `_alternative_route_requirements()`、
  `_validate_joint_activation_paths()` 与 `_reject_ambiguous_activation_gates()`；
- `src/mote_kernel/execution/engine/routing.py` 的 partial Join deadlock 边界。

当前 P3-M1 正向形状为：`choose` 的 left/right route 分别进入 `left`/`right`，二者再通过自己的 `go` route 汇合到
`shared`；`ordinary` 是 `choose` 的 direct sibling，最后 `(ordinary, shared) -> Join(target)`。当 `left` 和 `right`
各自只有 `go -> shared` 时，这个形状安全。

但只要两个 branch 各增加一条合法的 `stop -> END` route，`shared` 就不再由每个 `choose` route 保证。当前
`_alternative_route_requirements()` 只保留各 alternative 的共同 source，并对 route 做并集；`left:go` / `right:go`
属于不同的局部 source，汇总时被完全丢弃，`shared` 最终与 `ordinary` 都被近似成
`choose in {left, right}`。Join proof 因此错误通过。

公开最小复现选择 `choose:left`、随后 `left:stop`，实际调用序列为 `['choose', 'left', 'ordinary']`，`shared` 和
`target` 均未执行，最终抛出：

```text
RoutingDeadlockError: partial join progress has no next task able to complete it
```

这不是运行时应“容错完成”的情况，而是 compiler 的 joint-activation proof 不完整。正确收口必须在唯一 compiler owner
保留足以证明每个互斥 alternative 都会提供完整 Join source set 的条件关系；如果当前抽象无法表达，就应保守拒绝，不能在
routing 丢弃 partial arrival、猜测 END，或让 runtime 选择第一条 gate。

当前补丁为 route requirements 增加 owner-local immutable `_RouteRequirementProof(requirements, exact)`：矩形 requirement
继续作为 coexistence 的安全 over-approximation；只有未丢失 branch-local restriction、未丢失多维 route correlation 的 exact
proof 才能证明 Join 各 source activation domain 相同。原 `left/right:stop -> END` 反例现在得到
`GraphValidationError("... can receive only a partial source set on a route")`，任何 callable 均未执行；原先每个 branch 只有
必选 `go` 的安全汇合仍在 left/right 两种选择下各执行一次 `shared` 和 `target`。该改变只收紧 compiler admission，未增加
runtime 补偿路径。

### 4.3 P3-M2 设计与代码复核

本轮没有发现 P3-M2 的独立阻断项：

- `_compile_activation_rules()` 只放宽 feedback binding 数量，没有放宽单 target、target-self repeat、单 feedback route、
  单 terminal route、单 graph output、root scope 和 callable target 等既有边界；
- 每个 input 的 rule 由同一 compiler collection 持有，`MaterializationPlan` 直接引用该 rule；routing、availability、
  materialization 和 recovery history window 都消费这一份 compiled fact，没有 `if one_feedback` 旧路径；
- target 的一次 State cause 与一次 node publication frame服务全部 binding。多个 binding 只选择 frame 内各自固定的 output，
  没有复制 per-binding State、route map、latest-value scan 或隐藏缓存；
- `for_target()` 是对同一 immutable collection 的 owner-local typed lookup，不是第二份索引状态；原先会覆盖同 target rule 的
  临时 target-map helper 已删除；
- 当前 HEAD 又把 reducer candidate admission 放进唯一 `commit_transition()`，避免 start/cleanup/owner-local 直调在
  `Graph.Commit` 前绕过 compiled frontier admission。

因此 P3-M2 本身继续通过代码 review；当前工作树也没有触及其 compiled activation rule、publication selection 或
materialization 路径。P1-1、P1-2 的生产代码复审及当前工作树完整门禁均已通过。没有为了复杂度数字要求拆 helper、新增
context 或改造 State。

## 5. 测试与门禁实测

本轮定向执行以下九个 compiler/routing/materialization/runtime/resource/family 测试文件：

```text
278 passed in 0.81s
```

修复前的正向矩阵和 P3-M2 multiple feedback 正向/错误矩阵为 `278 passed`；当前工作树另完成以下针对性复审：

- compiler/compiler-contract/routing：`95 passed`；
- 公开安全 Join 的 route、completion-order 和 resource 组合：`5 passed`；
- 两个初审反例均经公开 `Graph` facade 在 callable 前抛出预期 `GraphValidationError`，调用记录均为空。

当前工作树在 `mote-kernel` 目录完整执行 `make check`，结果通过：

- Ruff lint 与 format check：通过（234 files already formatted）；
- Pyright strict：通过（0 errors、0 warnings、0 informations）；
- complexity / semantic-index：`22 passed`，zero-debt health PASS；
- 全量测试及覆盖率：`1338 passed in 86.18s`，6990 statements / 2408 branches，100.00%；
- sdist/wheel build 与 `twine check`：通过。

从 monorepo root 对本次相关文件执行 scoped pre-commit，所有适用项通过。未执行 `pre-commit --all-files`：当前 monorepo
同时存在不属于本评审的 Cloudflare 路径迁移，all-files hooks 具有自动修写能力；本轮不越权改动或把该迁移纳入 P3 结论。
`git diff --check` 通过。

## 6. 最终结论

**复审结论：当前 P3-M1/P3-M2 相关修改通过代码 review，可以提交。**

P1-1、P1-2 都在唯一 compiler proof 中 fail closed，运行时、State 和 public API 未增加补偿或兼容路径；P3-M1 原有安全
Join 行为保持，P3-M2 调用链未受影响。复杂度增加对应必要的 exactness fact 和 proof composition，不是指标驱动的假抽象。
在当前公开、进程内能力范围内未发现新的真实问题，本轮停止继续扩张审查。
