# 父子图 GraphRun 本地 ownership 拆分窄范围需求

> **需求 ID：`GRC-LO-001`**
> **状态：SCOPE CONFIRMED / REVISED AFTER LO-A1–LO-A5 / READY FOR INDEPENDENT REVIEW。**
> 本需求只解决同一 `Graph.run()` invocation 内的父子图状态 ownership。每个图的
> `GraphRun` 自己维护自己的 `run_id` 和 `GraphRunState`；父图不维护 child 状态副本，
> 只在当前调用栈内持有不暴露 child identity 的 opaque call handle 或 transient typed
> result。本文不新增 child 跨 invocation load/recovery、persistence/Store 协议或
> failover；既有 owner-local authoritative state 读写语义继续由原 normative source 负责。

## 1. 文档信息与事实分工

- 需求日期：2026-08-27；本轮复审修订：2026-08-28
- 需求 owner：Mote Kernel execution owner（本需求只冻结行为边界，不代行批准）
- 评审基线：Git `ebcd043fdfe324c610328a08cb1a3e8a14b37e10`
- 关联调研：[GraphRun 独立状态可行性调研](graph-independent-run-context-feasibility-research.zh-CN.md)
- 历史评审：[原独立 GraphRun 实施方案评审](graph-independent-run-context-implementation-review.zh-CN.md)
- 历史回复：[原评审回复](graph-independent-run-context-implementation-review-response.zh-CN.md)
- 历史边界文档：[原范围撤回版实施文档](graph-independent-run-context-implementation.zh-CN.md)
- 最新复审输入：[需求与复审回复审批](graph-independent-run-context-local-ownership-implementation-review-response-review-response-review.zh-CN.md)
- 对应实施 target：[本需求对应的本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)

本文只拥有 `GRC-LO-001` 的需求、范围、验收条件和非目标。当前 API、State schema、
command、reducer、frame ABI、continuation、Store/persistence contract 和错误文本继续
由各自 normative source 拥有；implementation target 只拥有如何在本需求内落地的计划。
任何 requirements 批准、production authorization 和测试合入授权都必须由相应
owner/用户另行确认。

## 2. 背景与问题

当前一次父图执行把 root 和 nested child 放在同一个 invocation envelope 中：

```text
一次 Graph.run()
  = 一个 GraphRunContext
  + root GraphRunState
  + child state bindings
  + family 级 transient frame index
  + family driver 调度循环
```

`CompiledGraph` 本身是不可变的静态定义；在静态分析、顺序调用或不重叠的运行中可以
共享其引用。问题在于运行状态和执行上下文的 ownership 被 family envelope 集中管理。
这样会让“两个父节点同时调用同一个 compiled child graph”看起来像是在共享一个 child
状态，且 parent 可以通过 context 的 state lookup/replacement 操作直接替 child 推进状态。
跨不同 parent invocation 的重叠并发复用不是本需求支持的运行前提，而是 caller
precondition；本需求既不承诺该调用成功，也不承诺 kernel 检测或拒绝它。

本需求要消除的是**运行实例之间的可变 state ownership 共享**，不是静态 compiled
definition 的共享，也不是要求把每个 child 变成可以跨进程恢复的持久化任务。

## 3. 术语

### 3.1 Graph definition

`CompiledGraph` 及其不可变 topology、node descriptor、nested definition 和 executor
代码。定义对象本身不包含某次运行的 state、frame 或结果；其引用可在顺序或不重叠的
运行中复用。跨不同 parent invocation 的重叠并发复用不在本需求的支持面内。

### 3.2 GraphRun

一次具体图执行的内部运行单元。每个 `GraphRun` 独占：

- 自己的 `run_id`/`ScopeRunCoordinate`；
- 自己当前的 `GraphRunState`；
- 自己的 state transition/commit 游标；
- 自己的 invocation-local 执行资料（必要时包括本地图 frame/context）。

`GraphRun` 是 execution-internal owner，不新增 public `GraphRun` 导出或第二公共
执行入口。

### 3.3 Parent GraphRun

当前调用链中发起 nested child 调用的运行实例。它只负责自己的 state，以及调用 child、
等待 child 结果和结算自己的 nested node。等待或传播调用级中断时，parent 最多持有当前
调用栈内的 opaque call handle；该 handle 不暴露或保存 child `run_id`。

### 3.4 Child GraphRun

由 nested graph definition 创建的独立运行实例。它只读写自己的 state/context，并以
typed transient result 将完成、等待或中止信息返回给 parent。child owner 在自身内部创建、
校验和使用 child `run_id`；parent 不成为该 identity 的 owner。

### 3.5 Transient child handle

当前 invocation 内表示一次 child call 的 opaque、不可持久化控制/等待对象。它可以让
parent 等待结果，或在调用级 cancellation/abort 时向对应 child owner 传递信号，但不得向
parent 暴露 child `run_id`、lookup key 或可重建 identity。handle 只在当前调用栈存活，
不得进入 parent state、continuation、result 或 coordinator 的 durable/reusable 资料；
不得缓存 child state、frame、result、session 或 task。child 返回或关闭后立即释放。
若实现存在 invocation coordinator，它只能持有该 opaque handle 对象本身；不得把 child
`run_id`/coordinate 展开成 parent/coordinator 的编排字段，即使这些字段只计划在本次
调用中使用。handle 内部可由 child owner 封装路由所需资料，但 parent 不能检查、派生、
持久化或按其 identity 重建 child。它不是 child state 的第二份存储、identity owner、
recovery 入口或 parent 控制权。

### 3.6 Invocation-level cancellation/abort

由当前 `Graph.run()` invocation 边界、caller cancellation、显式调用级 abort，或既有
lifecycle contract 已分类为 invocation-level error 的中断事件。它不包括 typed node/child
failure 或 ordinary node exception；后两者按各自的局部 failure 规则处理，不自动广播
到 sibling。

## 4. 目标

### 4.1 状态 ownership

1. 每个 `GraphRun` 必须拥有并推进自己的 `GraphRunState`。
2. Parent 不得保存一份可变的 child `GraphRunState` 镜像。
3. Child 的 claim、settlement、frontier resolution、fence 和 abort 只能由 child
   对象针对自己的 state 发起；parent 不能替 child 调用 state replacement。
4. Parent 的 state transition 只能修改 parent 自己的 `GraphRunState`，包括 nested
   node 的 `SettleGraphNode`。
5. 调用级 cancellation/abort 的信号可以经由当前 invocation 的 opaque handle 传递，
   但每个 `GraphRun` 必须在自己的 owner 上完成 quiesce（必要时先完成既有 fence 前置
   条件）、`AbortGraphRun` transition 和 commit；不得由 parent 代写 child state。

### 4.2 Nested 调用

到达 nested node 时，parent 通过既有 execution owner 的 opaque call boundary 请求该 node
对应的 child `GraphRun`：

```text
Parent GraphRun
  -> 从已编译 definition 找到 child graph
  -> 向 child owner 传入本次调用的 typed input（及既有 activation metadata，如有要求）
  -> child owner 创建/校验自己的 GraphRun identity/state
  -> child 使用既有 execution engine/reducer 推进自己的 state
  -> child 返回当前 invocation 的 opaque handle 或 transient typed result
  -> parent 只提交自己的 nested-node settlement
```

parent 不维护、计算、保存或按 ID 查找 child `run_id`，也不建立 child state 索引。若
child 尚未 terminal，parent 只能在当前调用栈保留一个 opaque handle 以等待结果或传递
调用级 cancellation；若 child 已同步返回，parent 直接消费 typed result 并释放 handle。
handle、parent-owned child identity 和 child live state/frame 都不得写入 parent state、
continuation、result 或 coordinator 的 durable/reusable 资料；child result 只能按既有
typed projection 短暂传递，不能成为第二份 authoritative/cache。既有 sealed evidence 若
按 normative contract 必须携带 child identity，只能按第 4.4/5.3 节作为 immutable
transport/validation evidence 保留。无需为此新增 public type、第二 runner、registry 或
identity 字段。

### 4.3 共享 compiled child definition

`CompiledGraph` 的 immutable 定义可在顺序或不重叠的运行中共享；但以下场景不是本需求
支持的成功行为，而是调用方必须避免的 caller precondition：

```text
Parent A invocation ─┐
                     ├─ 重叠并发复用同一个 immutable CompiledGraph(child)
Parent B invocation ─┘
```

本需求不要求 kernel 为该场景创建两个可并发推进的 child `GraphRun`，也不建立 runtime
overlap detector 或 rejection gate。调用方/authoritative commit owner 必须保证同一
logical run 使用最新 State/continuation pair，且同一 compiled child 不被两个 parent
invocation 重叠驱动。若未来需要 deterministic rejection，必须另立 requirements/change
unit 定义 admission owner；本需求不新增 global registry、optimistic/persistent lock 或
第二 runner。

无论两个 parent 是否重叠并发，以下 identity 不变量仍属于本需求：对同一个 immutable
child definition，若两个 parent 的 `run_id` 不同，则它们在任意 activation 上由 child
owner 派生的 child `run_id` 必须不同。现有
`child_graph_run_id(parent_run_id, superstep, node_id)` 的长度前缀编码已经提供该 tuple
的无歧义投影；实现必须保留这一注入性。parent 可以按既有 typed contract 提供自己的
activation metadata，但不得自行生成、保存、查找或控制 child `run_id`。该数学不变量不
承担 fresh invocation admission 或 overlap 检测职责。

同一 parent 在不同 superstep 再次进入同一个 nested node，也必须由 child owner 创建/使用
新的 child `GraphRun` 实例；旧实例的 state、handle 或 evidence 不得被覆盖或重用为新
activation。

### 4.4 结果和生命周期

- `CompletedChild`、`AbortedChild`、child awaiting/resume 等现有 typed projection
  继续表达 parent 可观察的结果语义；不新增 public variant。
- Child 的 terminal result 只在当前调用栈内作为 transient value 传给 parent；opaque live
  handle、session 或 capability 不进入下一次 `Graph.run()`。任何既有 sealed evidence 中
  必须出现的 child identity 仅用于 immutable 校验/传输，不是 parent-owned identity 或
  lookup key。
- child awaiting 时，当前 handle 在 invocation unwind/export 前释放；后续 resume 只按
  既有 continuation/state pairing contract 重新 admission，不使用 handle 或 child ID 作为
  lookup/recovery 入口。
- 现有 sealed continuation 仍按当前 contract 保留 immutable child state/boundary/frame
  snapshot evidence；这些只读 evidence 是 transport/validation evidence，不是 parent
  runtime 维护的 authoritative state 镜像。每个 GraphRun 的 live frame、session 和
  continuation 资料仍由各自 owner 负责。
- Parent settlement 成功后，parent 继续自己的 routing；child 不直接修改 parent state。
- 调用级 cancellation、explicit abort 或由既有 lifecycle 分类为 invocation-level error
  时，信号必须沿当前调用链（含嵌套层级）传播到所有 live child call。每个 child owner
  先关闭自己的 session；若仍有 active execution token，则完成既有 owner-local fence
  前置条件，再对自己的 state 应用既有 `AbortGraphRun` 并独立 commit。parent 随后关闭
  自己的 session，按同一顺序对自己的 state 应用同一 `AbortGraphRun`；两次 commit 独立，
  不构成跨图原子事务，也不产生 orphaned-claim recovery 分支。
- 已确认 `COMPLETED`/`ABORTED` 的 run 不重复 abort；尚未确认的 start candidate 在
  unwind 时丢弃，不凭 cancellation 伪造 `ABORTED`。Store/commit 失败沿各自既有错误
  语义传播，不猜测或回滚另一 owner 的结果。
- Typed node/child failure 只结算失败的 node/child；普通 node exception 停止新的
  activation 并按既有 session 规则排空已启动任务，但两者都不得主动向 sibling 广播
  abort。只有调用级 cancellation/abort 才触发上述传播。
- child blocked/awaiting 不改变 `GraphRunStatus`；状态仍为现有 `RUNNING`，awaiting
  由 frontier/projection 表达。

## 5. 必须保持的不变量

### 5.1 State schema 与 reducer

`GraphRunState` 的字段、`GraphRunStatus` 的值、现有 `GraphRunCommand` variants、
纯 `reduce_graph_run()`、revision/token fence 和 exact successor 语义保持不变：

```text
GraphRunStatus = RUNNING | COMPLETED | ABORTED
```

本需求不把 concrete output、frame、handle、session 或 child map 写入 State。

### 5.2 公共 API

- `Graph` 仍是唯一 public graph facade 和 execution entry point。
- 不新增 public `GraphRun`、`GraphRunRef`、`ChildRunInvoker` 或 recursive `run()` overload。
- 现有 `Graph.run()` 的 values/state/continuation/resume/commit 参数形状保持不变。
- 内部 `GraphRun` 如需新增，只能留在 execution owner 内，不从 `mote_kernel.execution`
  作为并列公共入口导出。

### 5.3 Continuation 与 frame

- 现有 complete/recovered continuation 的可观察行为保持不变，仍是 opaque、
  invocation-local、不可序列化的 contract。
- `_CompleteContinuationSnapshot.child_states`、
  `_RecoveredContinuationSnapshot.child_states` 和 family-wide `ScopedFrameIndex` 的当前
  sealed admission/export 形状保持；`ChildStateBinding` 与合并后的 frame index 只作为
  immutable、transient 的 continuation/recovery transport/validation evidence，不成为
  parent runtime state、identity 或 live frame owner。binding 内若按既有 sealed contract
  携带 child `run_id`，该字段仍只能作为 evidence，不能成为 parent 的 lookup/control key。
- 如果内部把 frame/context 按 run 拆开，continuation 可以作为当前已有 transport
  重新装配这些 transient 资料，但不得借此新增 checkpoint、codec 或跨 invocation
  load 语义。
- Parent-child input/output 只能通过既有 typed frame/result owner 传递；不创建第二
  frame/value truth，也不得用 family evidence 驱动或替换 child 的 live state/frame。

### 5.4 Identity

每个 child `GraphRun` 仍可使用现有 `GraphRunState.run_id`、
`ParentGraphActivation`、`ScopeRunCoordinate` 和 deterministic child identity，以
满足既有 State/reducer/frame 校验；但这些 identity 由 child 运行实例在自身 owner
内创建、校验和使用，不是 parent 的 child registry、lookup key、取消入口或跨
invocation recovery key。parent 只能按既有 typed contract 提供自己的 activation
metadata，不得自行派生、保存、查询或控制 child `run_id`；当前 invocation 若必须等待
或取消 child，只能使用第 3.5 节定义的 opaque handle。不得新增 identity 类型、字段或
公共 lookup API。

对于同一 compiled child，`parent_run_id_A != parent_run_id_B` 必须推出对应 child
`run_id_A != run_id_B`；该保证独立于是否允许两个 parent 重叠执行。本文所称“不同
parent 运行”以其 `parent_run_id` 区分；若两个 invocation 声称不同却携带相同 parent
`run_id`，它们在 contract 中不可区分，不能借随机 ID 补救。当前 API 不记录“已使用”
标志；caller/authoritative commit owner 负责 root `run_id` 唯一性、最新 pair 和
single-driver precondition。本需求不新增 fresh-run admission owner。

## 6. 验收行为

以下是本需求必须覆盖的 public/typed behavior；测试名称可在 implementation review 中
微调，但语义不可删减。跨不同 parent invocation 重叠复用同一 compiled child 不作为
必须成功或必须拒绝的 runtime behavior，也不进入本需求的 acceptance/gate；它仅是调用方
必须遵守的 caller precondition。调用级 cancellation/abort 与 typed node failure、ordinary
node exception 必须按下表区分，不能以一个泛化的“parent exception”替代：

| Case | 必须证明的事实 |
| --- | --- |
| 单个 nested child success | child state 只由 child transition 更新；parent 只结算自己的 nested node |
| 同一 compiled child + 不同 parent identity | 在相同或不同 activation 上，由 child owner 从两个不同 parent `run_id` 派生出的 child `run_id` 必须不同；identity 投影不得碰撞 |
| 同一 parent 跨 superstep 再次调用同一 nested node | 新 activation 不复用旧 child state；旧 child 结果不会覆盖新 child |
| child typed failure | 只结算该 child/node；parent 按既有 settlement 规则处理，child 不写 parent，其他 sibling 不被取消 |
| child abort（非调用级传播） | child owner 只转换/提交自己的 `ABORTED`；parent 消费既有 projection，不能代写 child 或广播 sibling abort |
| child awaiting/resume | child 保持 `RUNNING`；parent 不伪造 success/failure，现有 continuation 行为不变 |
| parent 继续 routing | 只有 parent 自己的 `SettleGraphNode` confirmed 后才推进 parent routing |
| invocation-level cancellation/abort/error | 信号传播到当前调用链所有 live child handle；每个 child 和 parent 各自在 quiesce（必要时既有 fence）后使用同一 `AbortGraphRun` 并独立 commit；该路径不得依赖或产生 orphaned-claim recovery |
| cancellation 的 candidate/commit 边界 | 未确认的 start candidate 丢弃且不伪造 `ABORTED`；一方 Store/commit 失败只传播该 owner 的既有错误，不回滚或猜测另一 owner，也不触发 retry/failover |
| ordinary node exception | 停止新的 activation，按既有 session 规则排空已启动任务；不向 sibling 广播 abort，也不伪造全 family `ABORTED` |
| immutable definition 的顺序/不重叠复用 | definition 可共享；每次实际 activation 仍拥有自己的 mutable `GraphRun`/context/session |
| 同一 run identity/revision 的重复 transition | 现有 exact-successor、revision/token fence 拒绝第二次提交；不得覆盖已确认状态 |
| continuation round trip | 现有 opaque continuation 的 state pairing、nested projection 和 frame 语义不变 |
| 状态隔离负向 case | 任一 child 的 state replacement、frame install 或 terminal result 不会改变 sibling/parent state |
| opaque handle 生命周期 | handle 仅当前调用栈可见、不可按 ID 查找或重建；返回/关闭后释放，不进入 parent state、continuation、result 或可复用 coordinator 资料 |
| 既有 owner-local persistence 读取 | parent 只读取/提交自己的 authoritative state，child 只读取/提交自己的 authoritative state；不新增 Store/persistence 协议或跨 owner lookup |

## 7. 明确非目标

以下新增能力不属于 `GRC-LO-001`，也不能作为本需求的隐含验收条件；它们不否定各自
normative source 已有的 owner-local 行为：

1. 仅凭 `child_run_id` 在新的 invocation 中 `load/drive/recover` child。
2. 新增或改变 State、frame、continuation 或 terminal output 的 persistence、Store、
   checkpoint、journal、wire codec 或 durable boundary 协议。既有机制仍可由各自 owner
   读取/提交自己的 authoritative state，但本需求不改变其语义，也不允许 parent 读取或
   写入 child state。
3. 新增 child terminal publish/read/ack、跨 run CAS、receipt、read-after-commit、
   exactly-once 或 crash replay 协议；既有 owner-local commit/read 语义保持不变。
4. worker handoff、multi-worker arbitration、lease expiry/renewal/reclaim、failover
   和 aggregate budget。
5. 跨不同 parent invocation 重叠并发复用同一 immutable `CompiledGraph` 的成功保证、
   检测或拒绝；它是 caller precondition，不是 runtime behavior。全局 optimistic
   admission guard、持久化锁或跨 invocation 协调器也不在本需求内。
6. 新 public `GraphRun`、`GraphRunRef`、ChildRunInvoker、第二 scheduler、global mutable
   registry、compatibility alias 或 legacy-only test path。
7. 新 `GraphRunState` field/status/command、改变现有 continuation 序列化契约，或把
   concrete child output 写入 State。
8. 改变 compiled topology、routing、resource 语义、既有错误分类或现有 Graph.run() 公共
   overload；调用级 abort 只复用既有 `AbortGraphRun`，不新增 status、command 或错误
   taxonomy。

## 8. Owner 与责任矩阵

| 事实 | owner / responsibility boundary | Parent 可做的事 | Parent 不得做的事 |
| --- | --- | --- | --- |
| child `run_id` 与 `GraphRunState` | child `GraphRun` | 通过 opaque handle 等待/消费只读 projection | 复制、替换、提交、保存、建索引或按 ID 控制 |
| child `run_id` 派生与唯一性 | 现有 `child_graph_run_id()` identity owner + child `GraphRun` | 提供既有 parent activation metadata | 自行派生、截断、覆盖、查询、碰撞后复用或随机改写 ID |
| opaque child call handle | invocation-local call owner | 在当前调用栈等待结果，或传递调用级 cancellation/abort | 暴露 child identity、缓存 state/frame/result/session、跨 invocation 复用或按 ID 重建 |
| child 执行 session/quantum | child `GraphRun` + existing execution engine | 等待 transient completion；接收 owner 发出的 abort 结果 | 创建第二 scheduler、直接驱动 child state 或代替 child fence/commit |
| invocation-level cancellation signal | 当前 invocation/session lifecycle owner | 向当前 live child handles 传播同一 cancellation/abort signal | 通过 child ID 查找、接管或跨 invocation 广播 |
| child/parent `AbortGraphRun` transition 与 commit | 各自 `GraphRun` owner | 关闭自己的 session 并提交自己的 abort | 由 parent 代写 child、伪造未确认 run 的 `ABORTED` 或建立 orphaned-claim recovery |
| typed node/child failure | 产生该 failure 的 node/child owner | 结算自己的 failure 并按既有 routing/session 规则继续或停止 | 因单个 sibling failure 主动取消其他 sibling |
| parent nested activation | parent `GraphRun` | 提交自己的 settlement/routing | 让 child 直接写 parent frontier 或 state |
| compiled child definition | immutable `CompiledGraph` | 在合法的顺序/不重叠运行中共享引用 | 保存运行状态 |
| child completion projection | child result/projection owner | 消费并验证后结算 parent | 把 projection/evidence 当成 child state 或 identity 镜像 |
| family continuation/frame evidence | existing continuation/frame owner | 传递 immutable、transient transport/validation evidence | 将 evidence 变成 live owner、驱动/替换 child state/frame 或新增 durable boundary |
| owner-local persistence read/commit | 各自 GraphRun 的既有 authoritative Store/commit owner | 读取/提交自己的 state | 读取/写入另一 owner 的 state，或新增 Store/persistence/recovery 协议 |

## 9. 准入和门禁

在独立 implementation review 与用户明确批准前，本需求不授权 production/test 修改。
后续 target 必须提供：

- producer/consumer call graph；
- `GraphRunContext` child-state operation 的删除闭包；
- opaque child call handle、local context/frame、parent projection 和 cancellation signal 的
  nominal type owner；证明 parent/coordinator 的 durable 或 reusable 资料不含 child
  `run_id`；
- 同一 compiled child 在不同 parent `run_id` 下由 child owner 派生不同 child `run_id` 的确定性测试；
- 同一 invocation/continuation 内重复 activation，以及同一 state revision/token 重复
  transition 的确定性 fail-closed 测试；跨 parent overlap 不得进入 runtime test/gate；
- continuation、awaiting、typed failure、ordinary node exception、invocation-level
  cancellation/abort（含 parent/child 双边 `AbortGraphRun`、active-token fence、已确认/
  未确认 candidate 和 Store/commit failure）的回归矩阵；矩阵必须证明普通 sibling failure
  不广播 abort，且该 cancellation 路径不依赖或产生 orphaned-claim recovery；
- 既有 owner-local persistence read/commit 的边界证明：parent/child 只访问自己的
  authoritative state，Store/persistence protocol 无新增或改写；
- family-shaped continuation/frame snapshot 仅作为 immutable transport/validation evidence
  的 producer/consumer 证明，不得成为 live shared owner；
- production/test/docs per-change manifest；
- 不新增 compatibility alias 或 legacy-only test；既有 public regression consumer 只保留
  observable behavior 断言；
- strict typing、lint、format、behavior tests、coverage、build/package 和适用
  pre-commit 结果；complexity gate 按用户范围单独记录，不得伪写成完整 `make check`。

任一 target 若新增或改写 persistence/Store/checkpoint protocol、cross-invocation load、
failover、State schema 变更、public API、第二 runner 或专门的全局 optimistic admission
guard，必须退出本需求并创建新的 requirements/change unit。

## 10. 本需求的 change-unit manifest

需求阶段只新增：

```text
mote-kernel/docs/graph-independent-run-context-local-ownership-requirements.zh-CN.md
```

implementation、production、State、Store、protocol 和 tests 不在本需求文档 change
unit 中。完成独立 review 后，implementation target 才能单独给出自己的 manifest。

## 11. 当前裁决

```text
GRC-LO-001                         = SCOPE CONFIRMED / REVISED AFTER LO-A1–LO-A5
same-invocation ownership split   = IN SCOPE
shared immutable CompiledGraph    = ALLOWED FOR STATIC/SEQUENTIAL OR NON-OVERLAPPING USE
cross-parent overlapping reuse    = CALLER PRECONDITION / NO RUNTIME DETECTION OR REJECTION GATE
parent child-state mirror         = FORBIDDEN
parent transient handle           = OPAQUE / INVOCATION-LOCAL / NO CHILD ID / WAIT-OR-ABORT ONLY
child-owned run_id/state          = REQUIRED (INTERNAL)
same child + distinct parent IDs  = CHILD RUN IDS MUST DIFFER
invocation cancellation/abort     = PROPAGATE TO LIVE CHILD CALLS
parent + child AbortGraphRun      = REQUIRED / SAME EXISTING TRANSITION LOGIC
typed node/child failure          = LOCAL / NO SIBLING BROADCAST
ordinary node exception           = STOP NEW ACTIVATION / NO SIBLING ABORT
family evidence                   = IMMUTABLE TRANSIENT TRANSPORT/VALIDATION ONLY
cross-invocation load/recovery    = OUT OF SCOPE
persistence / Store protocol      = KEEP EXISTING OWNER-LOCAL SEMANTICS / NO NEW PROTOCOL
failover                          = OUT OF SCOPE
global optimistic admission lock  = OUT OF SCOPE (SEPARATE REQUIREMENT IF NEEDED)
GraphRunState schema/status       = KEEP CURRENT
public API                        = KEEP CURRENT
requirements approval             = PENDING INDEPENDENT REVIEW + REQUIREMENTS OWNER
implementation authorization      = PENDING IMPLEMENTATION REVIEW + USER APPROVAL
```

本需求的核心不是“让 parent 记住每个 child”，而是让每次 child 调用成为拥有自己状态
的 `GraphRun`；parent 只负责调用和结算自己的状态。
