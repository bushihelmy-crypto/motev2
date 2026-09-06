# Graph + State 最新改动验收代码评审（2026-09-06）

状态：**代码 review 通过；未发现正常公开调用和分布式恢复边界下的阻塞问题；本轮未运行测试和门禁**

审查基线：`HEAD 277b7a8` 及其当前工作树中的 Graph/State 增量；该提交只新增 Think 测试，未改变本轮 Graph/State
生产调用链；本轮不修改生产代码。

本记录按“先判断设计和完整调用链，再用门禁证明实现没有回归”的顺序维护。判定标准是唯一真相、复用既有
compiler/planner/routing/reducer/typed frame、逻辑最简、一次迁移和零技术债；复杂度热点、纯理论攻击面、恶意伪造
内部对象，以及当前明确留给后续持久化适配器的 durable/crash-safe 能力，不直接升级为缺陷。

## 1. 范围和排除项

纳入本轮当前工作树中与 Graph + `GraphRunState` 直接相连的改动：

- `src/mote_kernel/execution/**` 中 compiler、frontier、routing、scheduler、recovery、family driver、result 和 facade；
- `src/mote_kernel/state/graph_state/**` 中 model、command、execution transition、validation；
- 上述公开调用链对应的 execution/state 测试，仅用于核对行为语义和迁移覆盖。

不纳入：README、`port`、Failover/Act/Think/Hooks/Observe 等其它领域改动（除非直接改变 Graph/State 调用链），以及
未承诺的具体 persistence adapter、durable loader/reconcile、crash-safe durable concrete-value recovery。后者由后续
持久化适配器实现，当前不是阻塞项；本轮不为其增加第二状态 owner 或兼容路径。

## 2. 待核对的唯一调用链

```text
公开 Graph facade
  -> compiler / validation
       -> immutable compiled topology, gates and route facts
  -> planner / frontier materialization
       -> routing selection and typed input frames
  -> node settlement / reducer
       -> complete GraphTransition (candidate State + input + settlement + publication)
  -> Graph.Commit(exact candidate)
       -> confirmation 后才前移内存 GraphRunState 和 frame
  -> recovery 仅用已确认 frame/evidence 重建候选，不创建第二 store/state owner
```

本轮重点是新增 `completion_route` 从 terminal settlement 到 nested Graph 父节点的因果闭环，以及 recovery 在 route
证据不完整时的保守边界；同时核对 `Graph.add_edge` overload 迁移和 State invariant 是否仍只有一个 owner。

## 3. 审查台账

| 检查项 | 结论 | 依据 |
| --- | --- | --- |
| Graph facade / execution engine / GraphRunState 唯一 owner | 已核对通过（首轮） | 当前新增字段和路径仍落在既有 Graph facade、execution engine 与 `GraphRunState`；未见第二 runner、第二 reducer 或镜像 State |
| `GraphTransition` 与 exact `Graph.Commit` 边界 | 已核对通过（首轮） | transition 仍一次携带候选 State、input、settlement、publication；只有精确确认后内存 State/frame 前移 |
| compiler → planner → routing → reducer 的主链未分叉 | 已核对通过（首轮） | 新 route 逻辑复用已有 compiled routing contribution、frontier materialization 和 reducer；未见旁路执行入口 |
| `completion_route` 状态归属与完成态保留 | 已核对通过 | `GraphRunState.completion_route` 是唯一完成态事实；`CompleteGraphFrontier`、reducer、validation、family driver 均沿同一字段传递，running/failed/aborted 不保留该字段 |
| 正常 nested child terminal route 透传 | 已核对通过 | `CompletedChild.route`、family driver terminal projection、frontier `TaskSuccess` 和父 routing 使用同一条 typed route 链；公开 nested conditional happy path 可闭合 |
| recovery route-known/unknown 模拟 | 已核对通过（保守边界） | 已确认的 completed State 直接使用 `completion_route`；没有 route evidence 的 terminal child 只展开父图声明的合法 route，缺值即 fail closed；无 conditional edge 的 non-terminal nested node 合法贡献只有 `ContinueGraphRouting`。sticky unknown 只会扩大拒绝范围，不会错误放行或绕过 live validation |
| `Graph.add_edge` 新 overload 迁移 | 已核对通过 | `add_edge(source, target)` 与 `add_edge(source, route, target)` 均收敛到唯一 facade；生产代码和相关测试未见旧 `add_conditional_edge` 路径残留，START/END 方向校验仍在同一入口 |
| running/failed/aborted/completed State invariant | 已核对通过 | `GraphRunState`/`CompleteGraphFrontier` 所有新增构造和 validation 分支已枚举；完成态清空 frontier 后仅保留 `completion_route`，其它终态不会镜像该事实 |

## 4. Findings

本轮没有发现可由正常公开 `Graph` 调用触发、且会造成错误提交、错误路由、状态分叉或分布式恢复不一致的阻塞 finding。

### 对 GSR-1 争议点的复核结论（不升级）

先前怀疑 recovery 在未知 nested terminal route 上与 live routing 不一致，沿完整调用链复核后确认不构成 under-approximation：

1. 已有 completed child binding 时，`_boundary()` 读取已确认 `GraphRunState.completion_route`；`None` 也表示已确认的
   Continue，而不是“未知”。
2. state-only proof 没有 child output frame 时，只能对父 nested node 的**已声明 conditional route 域**做保守展开。任一声明
   分支需要不可用历史值，proof 在 claim/callable 前拒绝；这与实施计划和现有“检查每个声明 success route”的契约一致。它可能
   过度拒绝，但不会选择未声明 route 或偷偷提交另一条路径。
3. 父 nested node 没有 conditional edge、仍有 direct successor 或 Join 时，live 契约只接受 `ContinueGraphRouting`；非空 route
   本来就会由 `validate_routing_contribution()` 拒绝。recovery 投影唯一合法的 Continue，不是绕过该校验。
4. `completion_route_known` 的 sticky 传播可能让外层再次展开更多 conditional 分支，这是 proof 的保守收紧（false negative），
   不会改变候选 State、exact acknowledgement、CAS/revision 或 lease/fence 边界。因此不把它写成 correctness blocker。

实现中的“non-terminal parent fails closed”注释比实际的“采用唯一合法 Continue、保留 unknown 供外层保守展开”更窄，属于
后续可整理的措辞观察，不影响当前受支持路径的安全性和唯一 owner 结构。

### 非阻塞观察（不改变本轮结论）

- `preflight_recovery()` 在 `recovery.py:1518-1527` 构造公开返回 DTO 时没有带上 `boundary.completion_route_known`；当前 facade 只把该返回值当 proof gate 且不消费它，因此暂不升级为独立 blocker，但后续若调用方消费 proof 结果需补齐该事实。
- `_RecoveryFamily.frames` 以及 `_completed_child_outcomes(..., family)` 的生产读取仍很少，属于残留抽象迹象；按“复杂度门禁是雷达”原则，不因指标机械拆除或另建 helper，本轮不作为 finding。
- scheduler 先构造 output frame 再验证 route 可能影响 malformed output/invalid route 的错误优先级；当前没有足够契约证据证明公开行为改变，不升级。

## 5. 测试和门禁

按本轮要求，评审完成后不运行测试、coverage、`make check` 或 pre-commit；测试只作静态行为证据。具体 persistence adapter、
durable loader/reconcile，以及 crash-safe durable concrete-value recovery（进程崩溃后从持久化介质恢复具体值）仍由后续适配器
负责，当前明确不实现、不作为 Graph + State 代码阻塞项。门禁未重新运行；本记录只给出代码 review 结论，不把历史门禁结果冒充
当前工作树证据。

因此，本轮是“代码 review 通过、门禁未重跑”，不是对未实现持久化能力的承诺。
