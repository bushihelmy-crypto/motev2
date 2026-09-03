# Graph 显式反馈回环实施计划（P1 内部切片实施版）

状态：P1 已完成；typed declaration、compiler proof、State-owned cause、routing、input materialization、live/recovery 同序语义和全量门禁均已闭合；P2 durable recovery 尚未开始，公开 `Graph.feedback(...)` API 尚未开放

本文只规划 Kernel Graph 对“延迟数据回环”的支持，不定义业务策略、Port 状态码或重试参数，也不为失败节点提供
重试能力。失败重试只有 `mote_kernel.failover` 一个 owner。

本版根据
[Graph 延迟数据回环实施计划二次审计](./graph-delayed-loop-implementation-plan-review.zh-CN.md)
修订，并在[审计回复](./graph-delayed-loop-implementation-plan-review-response.zh-CN.md)中记录未采纳的绝对化要求。

重点先锁定四件事：

1. 重启时从哪里读取每个节点已经确认的证据；
2. 如何在持久化 State 中知道当前节点是第一次激活还是反馈激活；
3. 节点结果和 State 如何使用同一提交身份原子确认；
4. 第一步的简单拓扑如何沿同一模型扩展成多分枝，而不是写补丁。

## 1. 总体决定

第一版不开放任意普通数据边成环，只增加一种显式的 typed 反馈输入：

```text
feedback(initial=..., repeat=...)
```

它表示：

```text
目标节点命中 seed cause       -> 读取 initial
目标节点命中 feedback cause   -> 读取 cause 精确引用的紧邻前驱 publication
```

以下规则是硬约束：

1. 普通 `NodeOutputRef` 的语义不变，普通 data dependency 仍不能形成环；
2. `mote_kernel.execution.Graph` 仍是唯一公开的构图和运行门面；
3. `GraphRunState` 仍是唯一运行状态，`reduce_graph_run` 仍是唯一 reducer；
4. 不为每个 feedback binding 复制 payload，不创建 loop 专用 State slot；
5. 一个 producer activation 只有一个 canonical publication/result，所有消费者引用同一个值事实；
6. feedback publication 与 producer successful settlement 走同一个原子提交边界；
7. 后续 feedback 缺少历史证据时直接失败，不能重新使用 seed；
8. state-led 重启时由 Invocation/Execution 重新装载节点需要的已确认输入、输出和子图证据；已有 continuation
   仍是合法的崩溃恢复入口，但不能把已经失败的节点恢复为 Pending；
9. 节点一旦确认 `FailedGraphNode`，本次 Graph 运行即进入终止失败边界。Graph 不接受 `resume_failed`、
   `resume_failed_with`、`skip_failed` 或任何等价的失败后重调度；
10. Failover 若要再次调用 Port，必须把可重试的 Port 结果作为成功的 typed value 提交，再由 failover 图显式路由到
    一个新的 activation；Graph failure 不是 retry signal；
11. 未提交的 Pending activation 在进程崩溃后可能再次执行，这是对“没有确认结果”的崩溃重放，不是对
    `FailedGraphNode` 的失败重试。Kernel 不承诺 provider exactly-once；外部副作用必须由 failover 的 operation
    identity、幂等或 receipt/reconcile 保护；
12. 在 durable feedback capability 的恢复读取、原子提交、最小安全上限和 retention 契约闭合前，不开放公开
    durable feedback API。
13. 第一阶段只开放一个受限的直接自反馈形状：`START -> A(seed)`，`A` 通过同一次二选一 route 选择
    `feedback -> A(previous A output)` 或 `terminal -> END`。普通数据自环仍然非法，反馈只能由 typed binding
    跨 activation 表达。

两阶段交付不是两套实现：第一阶段只收窄 compiler 的准入范围，第二阶段沿同一套声明、cause、publication、
routing、commit 和 recovery 逻辑放开多分枝。

## 2. 时间坐标和两个身份

### 2.1 `superstep` 不是业务迭代次数

当前 Graph 中三个版本概念各有唯一职责：

| 名称 | 作用 | 能否表示反馈因果 |
| --- | --- | --- |
| `GraphDefinitionVersion` | 图结构版本和 compiled topology identity | 不能 |
| `GraphRunState.revision` | 每一次 reducer commit 的 CAS 版本 | 不能，只防陈旧提交 |
| `superstep` / State-owned `GraphActivationIdentity` | frontier 推进序号和节点激活坐标 | 能，用来拒绝同轮/未来读取 |

`superstep` 是执行坐标，不是“第几次重试”。一个业务循环可以跨多个 superstep。

第一阶段 direct self-feedback 会产生：

```text
A[0] -> A[1] -> A[2]
```

这个受限形状里相邻 A activation 恰好相差一个 superstep，但 `superstep` 仍是执行坐标，不是单独的业务循环计数器；
未来多节点、多分枝拓扑不能套用这个步长。feedback 不公开 `delay` 参数，publication 坐标由 compiler 根据已证明的
control cause 生成。

运行时必须额外校验：

```text
selected repeat publication.superstep < target activation.superstep
```

这是所有 feedback 的通用安全下限，不是新的业务计数器；对第一阶段 self-feedback，它**不充分**，还必须满足
第 3.4 节的 `RELATIVE(1)` 与 cause exact-immediate-predecessor 等式。

### 2.2 两个正交身份

Publication 身份和提交身份不能混为一个“版本号”。P0 固定使用两个正交的 typed key：

```text
GraphActivationIdentity = GraphRunId + superstep + GraphNodeId
PublicationKey = GraphActivationIdentity + FrameDescriptorIdentity
CommitKey      = GraphRunId + candidate_state.revision
EvidenceFingerprint = canonical(candidate State + ordered evidence operations)
```

含义是：

- `PublicationKey` 唯一确定一个 producer activation 的 value fact；多个消费者只引用它；
- `CommitKey` 证明这个 value fact 与哪一个 exact `GraphRunState` candidate 一起被确认；
- `GraphRunId` 已唯一标识 root/child run，Execution-owned scope path 只用于内存查找，不进入 canonical key；
- `ConfirmedPublication.acknowledged_revision` 只是联结元数据，不是 publication identity；
- 同一个 `CommitKey` 重试时，若 candidate 和 evidence 完全相同，必须幂等返回 exact candidate；
- 同一个 `CommitKey` 出现不同 candidate 或不同 evidence，必须 fail closed，不能覆盖原记录；
- sibling 完成顺序改变时，要求 publication-key/payload 集合和最终控制 State 等价，不要求逐次 revision 历史相同；
- acknowledgement lost 时，persistence Port 必须能按 `CommitKey` read/reconcile，不能靠生成第二个随机 key 猜测；
- “candidate 和 evidence 完全相同”必须由稳定的 `EvidenceFingerprint` 判断：输入是带 domain/version/长度边界的
  canonical bytes，evidence operations 按 typed key 排序；禁止依赖 Python 对象相等、`repr`、pickle 或容器迭代顺序；
- persistence 可以选择 wire schema，但相同逻辑提交在进程、重启和 adapter 间必须产生同一 fingerprint；同 key
  不同 fingerprint 一律冲突。

## 3. Feedback 的静态和运行语义

### 3.1 显式 typed declaration

第一版内部先使用一个封闭的 typed declaration，概念形状为：

```text
FeedbackInputBinding(
    initial = GraphInputRef | NodeOutputRef,  # 内部 union；P1 先只准 GraphInputRef
    repeat  = NodeOutputRef,
)
```

一个 `FeedbackInputBinding` 永远只有上面这一个 `repeat` source。即使将来一个 target 有多条互斥 feedback cause，
这些 cause 也只能选择该 binding 的同一 repeat source。若产品以后需要“不同 cause 读取不同 source”，必须设计新的
typed API 和输入语义，不能偷偷把当前 binding 解释成 cause-dependent map。

第一阶段允许一个受限例外：`repeat` 可以引用 target 自己的 output，但**只能**在 typed
`FeedbackInputBinding` 中表示跨 activation 的自反馈。普通输入若直接绑定 `NodeOutputRef(target.output)`，仍按
ordinary data dependency 处理，继续拒绝 self-cycle；不能用放宽普通数据边的方式实现自反馈。

它只能作为节点输入绑定使用，不改变普通 `NodeOutputRef` 的含义，也不能直接作为 graph output source。
第一阶段 validator 只接受 `GraphInputRef` 作为 initial；非零首轮的 `NodeOutputRef` seed 留给后续 proof，不能
靠 `superstep == 0` 猜测。`repeat == target.output` 只对下文定义的直接自反馈白名单开放，其他 self/multi-source
组合一律拒绝。

不公开用户可填写的 `delay`。需要表达的是“哪一个 control cause 是反馈”，不是让调用者手工填写一个数字。

编译器生成一个不可变的 activation-rule collection，记录：

```text
seed cause pattern(s)     -> initial source
feedback cause pattern(s) -> repeat publication selection
```

第一阶段 validator 只允许一条 seed rule 和一条 feedback rule；内部表示仍是不可变 collection，第二阶段放宽
准入，不改运行时分支。

### 3.2 State-owned activation cause

仅靠 `frame` 是否存在不能判断第一次激活。target 被推进到下一 frontier 后，旧 frontier 的 routing 已经被替换，
所以必须把 activation cause 作为现有 State 的控制事实保存下来。

P0 只冻结行为，不锁死字段布局。State 中需要一个封闭、不可变、可序列化的 cause union（名称可调整，职责不可
删除）：

```text
GraphActivationCause:
    StartActivationCause
    RoutedActivationCause(tuple[ActivationReference, ...])

GraphFrontierActivation:
    node_id
    cause: GraphActivationCause
```

`GraphActivationIdentity` 和 `ActivationReference` 都由 State owner 定义。`ActivationReference` 是不可变的前序
activation + 已选择 routing fact 引用，不是任意字符串；Execution 可以把它投影成自己的 lookup coordinate，但
State 绝不能导入 Execution-owned `StableActivation`，也不能再维护一份平行 run identity。

相应地：

- `StartGraphRun` 安装每个初始节点的 `GraphFrontierActivation`；
- `AdvanceGraphFrontier` 携带完整的下一 frontier activation，而不是只有 node id；
- `GraphFrontierNode` 或等价的 State-owned frontier record 保留该 cause；
- P1 将现有 Execution-owned `StableActivation` 拆掉或改为纯投影，canonical activation identity 只保留 State-owned
  一份，不建兼容别名；
- State reducer 除了验证 run identity、canonical order、重复 source 和时间，还必须证明每个 cause source 确实是
  当前 authoritative frontier 中已成功 settlement 且实际选择了被引用 route 的 activation，或是带 occurrence
  identity 的已提交 Join arrival；不能仅凭“坐标更早”放行伪造 cause；
- 对直接自反馈，cause 必须精确引用同一 target 的**紧邻上一轮** activation 及其已选择的 feedback route：
  `A[n + 1] -> ActivationReference(A[n], feedback_route)`。这里的“紧邻”由已提交 cause 链和
  `RELATIVE(1)` selection 共同证明，不是扫描历史 publication 后挑一个“最新”值，也不是只比较
  `superstep` 的大小；source 必须是实际成功且实际选择该 route 的 `A[n]`。
- Execution/compiler 验证该真实 routing fact 是否能按 compiled topology 激活 target，并唯一匹配 feedback rule；
- State 不导入、不读取 `CompiledGraph`，也不复制整张 topology。

第一阶段把 `RoutedActivationCause` 限制为一个同 scope source，并把 seed cause 限制为唯一的 Start cause；直接自反馈
还必须满足 source node == target node、source route == feedback route、且 source 是紧邻上一轮。上述限制属于
validator，不属于另一套运行时状态结构。以后多分枝时使用同一个 tuple/union 表示多个已证明 source。

### 3.3 多条回边与一次激活

这里先锁定一个不可变的不变量：**同一个 target 在同一个 frontier 坐标只能产生一次 activation**。值相同不等于
激活可以合并；否则 A 可能被同一轮跑两遍。

对多个能回到同一 target 的控制边，编译器只接受两种形状：

1. **可证明互斥的路线**：每次只会命中一条路线。每条 typed cause 仍读取该 `FeedbackInputBinding` 唯一的 repeat
   source；“source 相同”本身不能代替互斥证明。
2. **显式 Join**：调用者明确声明要等待哪些 control arrivals，Join 只产生一个 target activation。Control Join
   不合并 payload；target 若需要多个值，必须声明多个 typed inputs，或显式增加 aggregator node。

以下情况一律在 compile 阶段拒绝：

- 多条路线可能在同一 frontier 同时到达 target，却没有显式 Join；
- 多条路线共享一个 publication，但没有互斥证明或 Join；共享一个值不能自动去重 activation；
- 同一 target 的重复 edge、重复 cause 或无法唯一匹配的 rule。

第一阶段仍只开放一个 direct self-feedback cause，以及与它互斥的一条 terminal route；上述 generic 互斥多路线和
Join 是后续沿同一 rule/cause/recovery 模型扩展，不能通过 runtime 选“第一条”或把重复激活静默合并。

运行时 routing 在完成唯一性证明前必须保留每个 `(target, cause)` candidate。它先按 compiled activation gate/rule
逐项匹配，再要求某个 target 在该 frontier 坐标**恰好命中一条**合法规则，最后才收敛成一个
`GraphFrontierActivation`。零条或多条都 fail closed。禁止像当前 `routing.py` 那样先写入
`set[GraphNodeId]`，因为那会在检查前丢掉多条 cause 的事实。

### 3.4 Seed、repeat 和丢失证据

运行时按已提交的 cause 匹配 compiler 生成的 rule：

| 情况 | 行为 |
| --- | --- |
| 首个、且命中 seed rule 的 target activation | 读取 `initial` |
| 后续、且命中 feedback rule 的 target activation，repeat evidence 存在 | 按 cause 指定的唯一 `PublicationKey` 读取；直接自反馈必须是紧邻上一轮 publication |
| 后续 feedback activation 缺 repeat evidence | `GraphValueUnavailableError`，claim 前失败 |
| cause、scope、definition、descriptor 或坐标不匹配 | `SnapshotMismatchError` |
| codec、bytes、corruption 或 exact nominal type 不合格 | value admission 错误，不调用节点 |

“找不到 repeat frame 就重新使用 initial”永远不是合法分支。历史证据缺失代表快照/装载问题，不代表新一轮循环。

直接自反馈的 exact-predecessor admission 必须满足下面的等式，而不只是“小于”：

```text
target = GraphActivationIdentity(run, superstep = s + 1, node = A)
source = target.cause.reference.activation

source == GraphActivationIdentity(run, superstep = s, node = A)
target.cause.reference.route == compiled_feedback_route
compiled_repeat_selection.kind == RELATIVE
compiled_repeat_selection.superstep == 1
compiled_repeat_selection.resolve(target.superstep) == source.superstep
selected PublicationKey.activation == source
```

任一 identity、route、descriptor 或 selection 不相等都在 claim 前 fail closed。这里的 `s` 是 State-owned frontier
坐标，不是另加的业务循环次数。

### 3.5 第一阶段的直接自反馈白名单

第一阶段不开放任意条件反馈，只开放下面这一种可静态证明的形状：

```text
START -> A(seed)

A --feedback route--> A(previous A output)
A --terminal route--> END
```

具体 route 名称仍由调用者提供并编译为 typed `GraphRouteId`（例如 `continue`、`done`），不引入字符串 kind。
两条 route 必须是不同的 canonical `GraphRouteId`，来自同一次 `SelectGraphRoute`，且每次只能选择其中一条。白名单
内恰好有一条 feedback edge 和一条 terminal edge，不允许额外的无条件 `A -> A`、重复 edge 或第三条 outgoing edge：

1. 选择 feedback route：先原子确认 `A[n]` 的 successful settlement、canonical publication 和 route，再创建
   带 `ActivationReference(A[n], feedback route)` 的 `A[n + 1]` frontier activation；
2. 选择 terminal route：使用当前 `A[n]` 的 canonical publication 作为 Graph output，直接完成 Graph，不创建
   `A[n + 1]`；
3. 没有可证明 terminal route 的无条件 `A -> A` 一律 compile fail，不能依赖 `max_supersteps` 作为正常完成；
4. runtime 不扫描“最新的 A”，不按 frame 是否存在猜 seed，不保存第二个 previous-value slot，也不创建
   self-loop 专用 manager。上述行为全部复用普通 activation-rule、publication、routing、commit 和 recovery。

这是一条受限的 conditional self route，而不是放开 generic conditional feedback。generic conditional、多 feedback、
join、nested 和多分枝仍按第 6.3 节的阶段边界处理。

## 4. 唯一值真相与持久化恢复

### 4.1 Canonical publication

一个 producer activation 的值沿用现有结果链：

```text
TaskSuccess.output
  -> SettleGraphNode + GraphTransition.result
  -> exact GraphRunState candidate
  -> 同一 CommitKey 下的一个 result/publication evidence
  -> ScopedFrameIndex 的 execution-local projection
```

多个 feedback consumer、普通 consumer 和 graph output 都通过 `PublicationKey` 读取同一 payload，不创建
`LoopSlotValue` 或 per-binding 副本。

### 4.2 state-led recovery 的恢复 Port

当前 `Graph.run(state=...)` 没有 continuation 时会建立空 `ScopedFrameIndex`，现有 `commit` 也只有写入方向。
因此，**不带 continuation 的 state-led 重启**必须先补一个窄的、typed 的 persistence/recovery capability，不能只
在文档里画概念图。这里的 state-led 不是“State 单独包含所有 value”：State 提供唯一控制坐标，value 仍必须从
该 State head 可见的 durable evidence 重建。已有完整 continuation 的调用不需要绕过现有入口。

语义形状如下（具体协议名称和 Python 签名在 P0 冻结）：

```text
GraphPersistence:
    load_recovery_snapshot(authoritative_state, graph_identity)
        -> version-joined RecoverySnapshot

    commit_transition(GraphTransition + typed evidence writes/releases)
        -> exact candidate GraphRunState
```

`Graph` 仍是唯一 facade；这个 Port 只是能力注入，不是第二个 runner、Store facade 或 recovery loop。

`RecoverySnapshot(N)` 的含义必须精确：它包含 **revision N 的 exact authoritative `GraphRunState`**，以及在该
State head 上仍然 live/visible 的完整 evidence set。某个仍然存活的 graph input 或 publication 可以出生在早于 N
的 commit，不能因为它不是“由 N 写入”就漏载。每条 evidence/release 至少带 `born_at: CommitKey` 和可选
`released_at: CommitKey`（或等价的 typed 可见区间）；loader 必须沿同一 run 的已确认 commit chain 判定
`born_at <= N < released_at`。State head、writes 和 releases 必须由 persistence 一次 version join 返回，禁止先读
State 再松散查询“差不多同版本”的 records。

启用 durable feedback 时，`RecoverySnapshot` 必须在 claim 前完成以下 admission；仅使用进程内 continuation 的
Graph 不被迫注册 persistence codec：

- scope/run/definition/version；
- `PublicationKey`、activation cause 和 descriptor；直接自反馈还必须校验 cause 中的
  `ActivationReference(A[n], feedback route)` 与 `RELATIVE(1)` selection 指向同一 `A[n]`；
- `CommitKey` / state revision 联结；
- codec id/version、bytes 上限和 corruption；
- exact nominal type；
- 第一阶段支持的 graph input、node result evidence 变体；failed-node resume/skip 永久不属于 Graph recovery，
  Invocation admission 必须在 commit/claim 前拒绝相关 runtime action；第一阶段 compiler 只拒绝声明在 topology 中的
  child/nested/multi-scope 形状。未来开放 interrupt continuation 或 child boundary 时仍复用同一 snapshot 协议。

### 4.3 Evidence 生命周期

第一阶段至少闭合以下原子 evidence（仅对 durable feedback mode）：

| transition | 同一 commit 中的 evidence |
| --- | --- |
| `StartGraphRun` | admitted graph input |
| successful `SettleGraphNode` | canonical node output/result publication |
| failed settlement | 只提交终止失败控制事实；不得生成 retry/override/skip evidence，也不得重新调度该节点 |
| interrupted settlement | 保留 State 已拥有的 interrupt 控制事实；interrupt continuation 与失败重试严格分开 |
| failed-node resume/skip substitution | Graph 永久拒绝；失败重试只由 failover 的显式新 activation 表达 |
| child completion | 第一阶段单 scope 直接拒绝；后续从 child canonical evidence 重建，不能复制 payload |

Self-feedback 的最小 live-set 固定如下：

- `A[n + 1]` 仍是 Pending 时，`PublicationKey(A[n])` 必须 live，因为它是该 activation 的唯一 repeat 输入；
- `A[n + 1]` settlement 被确认时，`A[n]` 才不再是恢复所需输入。其 release 必须和该 settlement State candidate
  使用同一 CommitKey；若 settlement 成功，则 `A[n + 1]` publication 的 birth 也在同一原子提交中；
- 若 `A[n + 1]` 选择 feedback route，新的 `A[n + 2]` 只保持 `A[n + 1]` publication live；不能继续依赖或复活
  `A[n]`；
- 若 `A[n]` 选择 terminal route，当前 `A[n]` publication 作为唯一 completed Graph output evidence 保持可见，旧轮次
  publication 按上述规则释放；它不能被上一轮 output 替代。

release 与 State transition 不能拆成两个互不关联的提交。任一位置崩溃都必须按 `RecoverySnapshot` 的
born/released 区间重建：Pending successor 永不丢输入，已经 settlement 的 successor 不复活旧轮次值。Adapter 可以在
容量上限内保守地多保留记录，但可见 live set 必须符合上述规则，runtime 不能把 retained-but-released record 当输入。

因此 fresh run 必须这样开始：

```text
admit graph input
  -> StartGraphRun candidate
  -> atomic commit(State + graph-input evidence)
  -> install confirmed graph input
  -> preflight/claim
```

在选择 state-led durable recovery 时，不能先提交 `StartGraphRun`，再只把 graph input 放在内存里；若调用方明确
提供并持有完整 continuation，则沿现有 continuation 契约恢复。

### 4.4 重启时谁负责加载

节点 callable 不知道 persistence、codec 或 State。重启流程由 Invocation/Execution 负责：

```text
load RecoverySnapshot
  -> 验证并投影 ScopedFrameIndex
  -> 根据 State-owned activation cause 匹配 seed/repeat rule
  -> materialize typed NodeInputFrame
  -> existing preflight/claim/session
```

“每个节点重新加载”指 Execution 为每个节点恢复它需要的已确认证据，不是把 loader 塞进节点，也不是无条件再次
调用外部 Port。

对第一阶段 self-feedback，materialization 的选择步骤必须是确定的：先读取 target 当前 activation 的
`RoutedActivationCause`，取得其中唯一的 `ActivationReference(A[n], feedback route)`，再用编译器生成的
`RELATIVE(1)` rule 解析出同一个 `A[n]`，最后按其 `PublicationKey` 读取 output frame。禁止按 node id 扫描历史、
按 superstep 只做“更早”过滤、按 frame 存在与否回退到 `initial`，或在 State 外维护 previous-value 缓存。

## 5. 原子提交时序

### 5.1 fresh run

以下是 durable feedback mode 的提交顺序；纯进程内运行仍可使用现有无持久化 callback 语义。

```text
admit graph input
  -> StartGraphRun + graph-input evidence
  -> persistence atomic CAS by CommitKey
  -> confirm exact State
  -> install graph-input frame
  -> prepare/claim
```

### 5.2 producer success

在 durable feedback mode 中，result/publication evidence 与 settlement candidate 必须同一提交；这不是给
普通 Graph 增加第二条提交路径。

```text
execute producer activation
  -> admit NodeOutputFrame
  -> SettleGraphNode + GraphTransition.result + publication evidence
  -> reduce_graph_run() 得到 exact candidate
  -> same CommitKey atomic commit(State + result/publication)
  -> replace authoritative State
  -> install/rebuild ConfirmedPublication
  -> resolve committed routing
  -> if feedback route: AdvanceGraphFrontier carrying the exact predecessor cause
  -> if terminal route: project the current activation publication as Graph output and complete
```

反馈值不能在 `AdvanceGraphFrontier` 时补写，不能另设 `CommitLoopValue`，也不能让节点、Port 或 facade 直接写 State。
对 self-feedback，`AdvanceGraphFrontier` 只有在前一提交已经确认 `A[n]` 的 publication 和 feedback route 后才允许
携带 `ActivationReference(A[n], feedback route)`；terminal route 不产生下一 frontier。

### 5.3 recovery

```text
authoritative GraphRunState
  + commit-linked graph/result/input evidence set
  -> loader joins each record to its CommitKey/version chain
  -> descriptor/cause/codec/nominal admission
  -> for self-feedback, verify RELATIVE(1) resolves exactly to the cause predecessor
  -> ScopedFrameIndex projection
  -> preflight_recovery
  -> claim
```

任何缺失或不一致都在 claim 前失败。提交确认后 frame 安装失败，只能从同一 committed evidence 重建，不能重跑
已经确认的 producer。

## 6. 编译 proof 和第一阶段拓扑

### 6.1 复用现有 compiler proof

feedback compiler 不另写一套“加权环检测”把问题推给 runtime，而是扩展现有：

- activation gates；
- joint activation path；
- producer guarantees；
- route compatibility；
- unique publication selection；
- entry、reachability、join 和 terminal guarantees。

判定顺序固定为：

1. 普通 `NodeOutputRef` data dependency 子图仍必须无环；
2. 只有显式 feedback binding 才能脱离普通同轮 dependency；
3. initial/repeat exact descriptor 必须一致；
4. 第一阶段只允许 `repeat producer == target` 的受限 direct self-feedback；普通 self data cycle 仍拒绝；
5. seed cause 与 feedback cause 必须唯一、可达且不重叠；feedback route 与 terminal route 必须由同一次
   `SelectGraphRoute` 二选一；
6. repeat publication 必须有唯一 `RELATIVE(1)` selection，并证明它解析出的 activation 与
   `ActivationReference` 中的紧邻前驱完全相同；不能用“严格更早”或“最新 publication”替代；
7. reducer/admission 必须验证 source activation 已成功 settlement 且实际选择 feedback route；伪造坐标或 route 一律拒绝；
8. target 选择 terminal route 时必须有唯一 graph-output publication 坐标，且不得创建下一 activation；
9. 无可证明正常出口的无条件 direct `A -> A`、generic conditional feedback、join/nested/multi-feedback 等未能证明
   的组合在 compile 阶段拒绝，不等 runtime 猜。

### 6.2 第一阶段的正常成功退出

第一阶段不能只有“不断自循环直到 `max_supersteps`”。必须有可证明的成功出口：

```text
START -> A(seed)

A --continue/feedback--> A(previous A output)
A --done/terminal-----> END
```

这里：

- `A` 的输入显式声明 `feedback(initial=GraphInputRef, repeat=A.output)`；
- `continue/feedback` 与 `done/terminal` 是同一次 `SelectGraphRoute` 的互斥结果；只有前者创建下一轮 `A`；
- 下一轮 `A[n + 1]` 的 cause 精确引用 `ActivationReference(A[n], feedback route)`；
- `A[n]` 选择 `done/terminal` 时，当前 `A[n]` 的 canonical publication 直接成为 Graph output，不再执行 `A[n + 1]`；
- compiler 必须证明 seed/repeat cause 唯一、graph output publication 坐标唯一；
- output frame 在提交后、安装前丢失时，必须从 durable evidence 重建；
- `max_supersteps` 只是安全上限，不是业务正常退出。

### 6.3 两阶段拓扑范围

第一阶段的准入范围：

- 一个 `FeedbackInputBinding`；
- 一个 repeat producer，且第一阶段必须是 target 自己的 output；
- 一条 direct feedback route 和一条 terminal route，二者来自同一次 typed route selection；
- 一个明确、唯一的 Start seed cause，且 initial 为 `GraphInputRef`；
- 一个 scope 内恰好一个 callable target，不含 sibling 或其他中间节点；
- target 有明确且可证明的 `terminal -> END` 成功出口；
- 不含 generic conditional feedback、join feedback、nested feedback、多 feedback 或其他多分枝；受限的两路 self route
  是第一阶段唯一允许的条件选择。

第二阶段再逐项开放 generic 多分枝、多个消费者、conditional cause、多个 feedback、join 和 nested。每次只放宽
compiler validator，并复用同一套 runtime/evidence/commit/recovery；不能新增简单版专用 runner、reducer、State
字段或 `if one_feedback` 的执行分支。

多分枝的准入不改变第 3.3 节的不变量：互斥路线可以按 cause 分流；可能同时到达的路线必须显式 Join；仅仅因为
几条路线引用同一个 publication 不能放行。

从第一天开始，内部只使用一个封闭 typed `FeedbackInputBinding` 和一个 immutable compiled activation-rule
collection；不要预造 `FeedbackManager`、visitor、反射、字符串 kind 或 generic event bag。

## 7. 错误 admission 和终止边界

### 7.1 节点失败是 durable terminal state

“Graph 不重试失败节点”必须落实到 State，而不是只删 facade 方法：

- `TaskFailure` 仍通过唯一 `SettleGraphNode` 提交 `FailedGraphNode`；失败节点永远不能回到 Pending；
- 同一 frontier 中所有 Pending sibling 都必须按现有确定性调度继续推进，包括尚未 claim 的普通 callable、resource
  waiter、尚未启动或仍在运行的 nested child；一个 sibling 的业务 failure 不能阻止它们首次执行，也不能把它们改写
  为 failure；
- 当前 frontier 的 Pending 全部排空后，Failed 才进入 terminal Failed；含 Failed 的 frontier 在排空期间和终局都不得
  routing 或生成下一 frontier；仍在 awaiting resume 的 child 只在 terminal Failed 已确定后做统一终局清理；
- 当 frontier 不再有 Pending 且至少有一个 Failed 时，唯一 reducer 将 run 转成新的 typed terminal
  `GraphRunStatus.FAILED`（名称可调整，语义不可与 `ABORTED` 混用），清理 execution lease/resource；
- 若同一 frontier 同时含 Failed 与 Interrupted，Failed 终止优先；interrupt continuation 只适用于没有 Failed 的
  awaiting-interrupt run；
- public run result 返回 terminal failure，不再把 Failed 节点包装成 `AwaitingResume`；`AwaitingResume` 只表达
  interrupt continuation；
- 删除 `ResumeFailedNodeRequest`、`SkipFailedNodeRequest`、`ResumeFailedNode`、`SkipFailedNode`、
  `SkippedGraphNode` 及其 materialization/routing 分支。不得留下 deprecated alias、隐藏 command 或第二条迁移路径。

这样 crash recovery 遇到 terminal Failed State 只返回同一个 terminal result，不 claim 节点，也不调用 Port。

### 7.2 Admission 与其他终止

不同 owner 不共享一条跨模块的异常总排序，但 recovery/materialization 采用同一组最低顺序，避免在未完成身份和
值校验前 claim：

```text
record 形状 / 信任
  -> scope / run / definition / descriptor / commit identity
  -> duplicate publication / same-key conflict
  -> codec / version / size / corruption
  -> exact nominal value admission
  -> required evidence availability
  -> topology / materialization availability
  -> claim
```

这是 admission 的阶段顺序，不要求 reducer、persistence adapter、routing 和 facade 返回同一个错误字符串或
全局优先级。每个 owner 必须返回稳定的 typed error，并在 conformance 中固定自己的可观察边界。

边界固定为：

- malformed、stale、descriptor、codec、evidence 缺失等恢复前错误：在 claim 前抛既有 typed exception，不产生
  第三种 feedback 专用状态；
- 普通 routing 在已提交 frontier 上发现业务所需值不可用：复用现有 `AbortGraphRun` 投影语义；
- 节点本身返回失败：提交失败事实后终止本次 Graph 运行，不产生下一 frontier，不允许 resume、override、skip 或
  同输入重试；
- interrupt 和取消继续使用各自既有语义，不能被伪装成失败重试入口；
- 不能为了 feedback 把同一种缺值在不同路径悄悄改成另一种错误或自动 seed。

Failover 的可重试结果不是 `FailedGraphNode`。`InvokeOnce` 应把 `Rejected`、`Unknown` 等 Port 结果作为 typed value
成功提交，随后由 failover topology 决定是否产生一个新的 `InvokeOnce` activation。只有 failover 已决定终止时，
才把最终失败提交给 Graph。这样失败策略只有 failover 一个 owner，Graph 只负责确定性执行显式拓扑。

## 8. Codec、安全和 retention（durable capability 的前置条件）

这些约束属于启用 durable feedback 的 persistence capability，不改变所有进程内 Graph 的 assembly API。纯内存或
已有完整 continuation 的运行可以不注册 codec；一旦选择 state-led durable recovery，必须满足最小安全切片：

- persistence capability 提供匹配的 codec；Kernel 只接收窄的 typed codec/admission 结果，不要求 Graph 对所有值
  预先注册序列化器；
- codec id 是 canonical identity，version 是严格正整数；
- encoder 返回 exact `bytes`，decoder 结果重新经过 compiled descriptor 的 exact nominal admission；
- encode、commit、load 三处都执行明确的 payload bytes 上限；
- codec/commit/load 异常不能输出 payload、value `repr` 或 credential secret；
- corruption、codec mismatch、大小超限、缺 evidence 在 claim 前固定失败；
- 第一阶段只需给出单 scope direct self-loop 的最小 live evidence 规则；过期 publication/result 的 release 与使其
  失活的 State transition 使用同一个 commit boundary；
- durable feedback mode 必须在 `StartGraphRun` 时把所有影响执行和容量的 hard limits 冻结为 State-owned run
  policy（至少含 `max_supersteps`、live-evidence/bytes hard cap）；该 policy 与 State 同 commit 持久化、进入
  candidate fingerprint，并由 recovery snapshot 原样恢复；
- 重启调用不得提高或重置已冻结 hard limits。调用方若再次传入 limits，必须与 durable run policy exact match，
  否则 claim 前拒绝；当前每次 `Graph.run(..., max_supersteps=...)` 临时构造 `ExecutionLimits` 的做法不足以保护
  durable loop；
- 冻结的 limits 与 persistence retention 共同防止 live memory/durable records 无界增长，无法安全释放时
  fail closed；
- persistence adapter 的记录迁移必须显式按 codec version 处理，不做隐式 fallback 或 legacy alias。

完整 live-set 推导、长 loop compaction、跨 topology 的 liveness proof 属于 P3/P4；它们不能被用来掩盖第一阶段
缺少的基本安全边界。

## 9. 分阶段实施

### P0：关闭契约（历史阶段，已冻结；不实现 feedback 生产路径）

P0 是进入 P1 前的契约冻结阶段。下面列出当时需要冻结的边界，作为后续实现的验收基线；其中 durable
读写、原子提交和恢复部分仍属于尚未开始的 P2 实现，不把“契约已冻结”误写成“能力已经提供”。

- typed persistence/recovery Port 的读、写、版本联结和 idempotent reconcile；
- `StartGraphRun + graph input evidence` 原子提交；
- successful settlement/result/publication 原子提交；
- `PublicationKey`、`CommitKey`、canonical `EvidenceFingerprint` 和 acknowledgement-lost 语义；
- State-owned activation cause 的行为契约（字段表示在 P1 通过 proof 冻结）；
- 第一阶段 `START -> A`、`A --feedback--> A`、`A --terminal--> END` 的封闭 topology、typed route gate，以及
  ordinary self-cycle 保持拒绝的 proof obligation；
- 第一阶段 graph input、result evidence 的支持边界；failed-node resume/skip 永久由 Invocation admission/reducer
  拒绝，child/nested/multi-scope topology 在 compiler 拒绝；
- seed/repeat rule、`RELATIVE(1)` immediate-predecessor/cause identity proof 和第一阶段成功出口；
- self-feedback 最小 live-set、predecessor publication 的原子 birth/release 与崩溃恢复向量；
- exact-head + live-evidence `RecoverySnapshot` 及 evidence birth/release 可见性；
- durable capability 的最小 codec、bytes 上限、secret-safe error，以及每个 run 冻结且可恢复的 hard limits；
- admission 的共同阶段顺序，以及异常与 `AbortGraphRun` 的 owner 边界。

第 12 节记录了 P0 的退出条件。经批准先行落地的 terminal FAILED 基础切片不依赖
feedback declaration/cause/publication，用于先删除与总体边界冲突的 failed retry/skip 旧语义；P0 退出时没有增加
公开 feedback API。

### P1：内部 declaration + compiler proof（已完成）

交付：

- 内部 `FeedbackInputBinding`；
- immutable compiled activation-rule collection；
- State-owned `GraphActivationIdentity`、`GraphActivationCause` 和 frontier activation command；拆除 Execution 到 State
  的身份反向依赖，不保留第二份 canonical identity；
- 普通 binding/data-cycle/routing/graph-output 回归不变；
- 第一阶段 self-feedback 白名单内的唯一 seed/repeat cause、两路 route gate、`RELATIVE(1)` immediate-predecessor
  publication proof；
- 明确普通 `NodeOutputRef` self-cycle 继续在 compiler 拒绝，只有 typed feedback binding 可跨 activation 自引用；
- routing 在唯一 gate 命中前保留 `(target, cause)`，State reducer 验证 source 的真实 settlement/selected route；
- 未支持拓扑在 compile 阶段 fail closed。

P1 的进程内 typed declaration、compiler proof、State cause、routing 和 materialization 已落地，复审整改与全量门禁
已经闭合。此阶段仍不公开 `Graph.feedback(...)`。durable 读写和重启恢复不属于 P1 的完成范围。

### P2：durable 单 scope 垂直切片

一次打通完整链路：

```text
fresh input atomic evidence
  -> seed/repeat materialization
  -> State-owned activation cause
  -> settlement/result atomic commit
  -> publication projection/rebuild
  -> direct self-loop + terminal exit
  -> state-led restart load
  -> bounded retention + minimum security
```

必须同步完成：

- persistence read/reconcile Port；
- 一个 reference persistence adapter 的 state/result/input 版本联结；
- 对应的跨语言 conformance vectors；其他 adapter（包括 Cloudflare）按同一契约跟进，不在 Graph 内增加特判；
- 提交前崩溃、提交后安装前崩溃、acknowledgement lost、缺 evidence、stale key 故障注入；
- self-feedback 的多轮 `A[n] -> A[n + 1]`、terminal exit、immediate-predecessor materialization、release 前后崩溃
  故障注入。

只有 P2 垂直切片全部通过，才开放公开 `Graph.feedback(...)`。

### P3：沿同一模型扩展多分枝

每次只放开一种组合：多个普通消费者、generic conditional cause、多 feedback、join、nested、跨 scope。每次都必须同时
补齐成功、缺 evidence、恢复、并发顺序、duplicate/stale 错误优先级 proof。不能新建 owner 或执行路径。

开放 cyclic Join 前必须先增加 State-owned `GraphJoinOccurrenceIdentity`（名称可调整）：Join progress 的 key 必须
包含本次 Join occurrence/目标 activation 坐标，arrival 必须引用完整 source activation identity，不能继续只保存
source node id。否则上一轮 A 的 arrival 可能和下一轮 B 的 arrival 拼成一次不存在的 Join。Control Join 只证明
activation gate 完成，不负责 value merge。

### P4：容量和生命周期稳定化

在 P2 正确性已成立后，完成长 loop 压力、retention compaction、资源/并发、cancel、interrupt、执行上限和 adapter
迁移性能测试；P4 不承载基本恢复语义。

### P5：文档和全门禁

同步 Graph、State、recovery 调用链和 failover 文档，运行：

```text
ruff
pyright
定向 pytest
make check                         # mote-kernel
pre-commit run --all-files         # monorepo root
```

## 10. 运行和崩溃语义

### 10.1 可以保证

- 同一个 live claim/session 内，一个 ordinary task 至多调度一次；
- settlement 已确认且 canonical evidence 可重建时，不再次执行已确认 activation；
- routing 只读取已提交 State cause 和已确认 publication；直接自反馈只读取 cause 精确引用的紧邻前驱；
- transient frame 丢失可以从同一 commit identity 的 evidence 重建；
- 同一 CommitKey + 同一 candidate/evidence 重试会幂等收敛。

### 10.2 不保证

- producer 执行完成但 settlement 尚未提交时崩溃，Pending activation 可以再次执行；
- Kernel 不提供 provider exactly-once；外部副作用必须依靠 operation identity、幂等或 receipt/reconcile；
- `GraphRunState.revision` 不等于外部 operation identity；
- sibling 的逐次 revision 历史在并发完成顺序变化时不必 byte-identical。

## 11. 影响面

预期评估以下 owner，不代表每个文件最终都要修改：

| 层 | 位置 | 任务 |
| --- | --- | --- |
| Graph facade | `execution/facade.py` | 在恢复/提交 Port 和垂直切片完成后开放 feedback API |
| 声明与类型 | `execution/graph/ports.py`、`definition.py` | typed feedback binding、descriptor 和 source selection；允许受限的 `repeat == target.output` |
| Compiler | `execution/graph/compiler.py`、`topology.py` | self-feedback 两路 route gate、`RELATIVE(1)` immediate-predecessor selection、普通 self-cycle 保持拒绝 |
| Frontier/State | `state/graph_state/frontier_model.py`、`model.py`、`command.py` | State-owned activation identity/cause、精确 predecessor+route reference、durable run limits 和 frontier command；P3 增加 Join occurrence identity |
| Reducer | `execution_transitions.py`、`reducer.py`、`validation.py` | cause 结构、时间、revision、token，以及 self predecessor 的 success/selected-feedback-route 真实性校验 |
| Materialization | `execution/engine/resume_input.py` | seed/repeat rule、`RELATIVE(1)` 与 cause identity 一致、缺证据错误；禁止扫描“最新值” |
| Routing | `execution/engine/routing.py`、`frontier.py` | 在唯一 gate 命中前保留 `(target, cause)`；feedback route 生成一次 next activation，terminal route 只完成；禁止 target set 提前去重 |
| Settlement/result | `execution/engine/settlement.py`、`execution/result.py` | 唯一 task result → settlement/result evidence |
| Commit owner | `execution/family_driver.py` | same CommitKey + canonical EvidenceFingerprint 原子提交、确认后 frame 安装/重建 |
| Failure boundary | `execution/facade.py`、`request.py`、`result.py`、`engine/superstep.py`、`engine/resume_admission.py`、`engine/resume_input.py`、`state/graph_state/frontier_model.py`、`recovery_transitions.py` | 增加 durable terminal Failed disposition/result；移除 failed resume/skip/Skipped 路径，不保留兼容别名或隐藏入口 |
| Recovery | `execution/invocation.py`、`recovery.py`、`run_context.py` | exact State head + live evidence version join、self predecessor/cause admission、evidence projection/release、limits admission；不得把 Failed 改回 Pending |
| Persistence | `mote-infra/persistence` | state/result/input records、read/reconcile、迁移和 CAS；interrupt/child 以后再接入，不存 failed-retry evidence |
| Conformance | `conformance/`、跨语言 tests | CommitKey、PublicationKey、崩溃和恢复向量；P2 reference adapter 先落地 |

## 12. P0 补充复审退出条件

以下是进入 P1 前必须冻结的 Graph 语义和最小 durable capability 契约；不把未来 multi-branch、所有 adapter 或
完整 compaction 当作 P0 的隐性前置：

1. 定义 state-led recovery 的 typed 读取通道；没有 continuation 时从 exact authoritative State head N + 在 N
   可见的 live evidence 重建，不能宣称 State 单独包含 value，也不能只取 N 新写的 records；
2. 定义 durable mode 下 `StartGraphRun + graph input evidence` 的原子提交；
3. 定义第一阶段 result evidence；冻结 Failed 为不可重调度的终止事实，移除 Graph 的
   `resume_failed*`/`skip_failed` 公共入口，Invocation/reducer 对残留 action fail closed；interrupt/child/multi-scope
   另按各自语义处理；
4. 将 activation identity/cause 作为 State-owned typed fact，并由 `StartGraphRun/AdvanceGraphFrontier` 携带；State
   不导入 Execution-owned `StableActivation`，且 reducer 验证 cause source 的真实 settlement/selected route；
5. 固定内部 `PublicationKey`、`CommitKey` 与 canonical `EvidenceFingerprint` 的职责，保留 persistence owner 对
   wire/schema 的实现自由；
6. 定义 acknowledgement-lost 的幂等 commit/read-reconcile 行为，以及 `RecoverySnapshot(N)` 的 exact State head、
   live evidence、birth/release 可见区间；
7. 固定“一次 target activation 只能产生一次”的基数不变量；明确第一阶段没有 sibling，multi-branch 的顺序等价
   性质移到 P3；
8. 把 durable capability 的最小 codec、payload limit、secret-safe admission 和 per-run frozen hard limits 放在
   公开 durable API 之前；重启不能提高/重置 limits，完整 retention compaction 移到 P4；
9. 定义第一阶段 direct self-loop 的 normal successful exit 和当前 terminal activation 的 graph output coordinate；
10. 写出共同 admission 阶段和每个 owner 的“抛异常/提交 abort”边界，不规定一条全局错误字符串顺序；
11. 记录 persistence adapters 和跨语言 conformance 影响面，并在 P2 reference adapter 中验证；
12. public durable feedback 闸门包含 fresh input、settlement、recovery、最小 retention 和安全故障注入的垂直
    切片；
13. 两阶段使用同一套内部 declaration/rule/runtime/recovery 模型，第一阶段没有简单版专用分支。
14. routing 在 collapse target 前保留每个 `(target, cause)`，并证明每个 target 恰好命中一个 activation gate/rule；
    零个或多个命中都拒绝。
15. 一个 `FeedbackInputBinding` 只有一个 repeat source；Control Join 不合并值；cyclic Join 在 P3 开放前必须有
    occurrence identity，不能按 node id 跨轮累计 arrival。
16. `FailedGraphNode` 在 frontier settlement 完成后进入 durable terminal Failed status/result；Failed 不再映射为
    `AwaitingResume`，含 Failed 的 frontier 永不 routing/advance，interrupt continuation 与它分开。
17. 第一阶段白名单固定为一个 callable target 的 direct self-feedback：`initial=GraphInputRef`、
    `repeat=target.output`，以及同一次 `SelectGraphRoute` 产生的一条 feedback route 和一条 terminal route；普通
    `NodeOutputRef` self-cycle、无正常出口的无条件 `target -> target` 和 generic conditional feedback 继续拒绝。
18. `A[n + 1]` 的 State cause 必须精确引用 `ActivationReference(A[n], feedback route)`；compiler 生成唯一
    `RELATIVE(1)` selection，reducer/admission 验证 source 已成功且实际选择该 route，materialization 验证 selection
    与 cause identity 完全相同。只写 `publication.superstep < target.superstep` 不足以退出 P0。
19. terminal route 使用当前 `A[n]` 的 canonical publication 生成唯一 Graph output，不创建 `A[n + 1]`；self-feedback
    predecessor evidence 的 born/release 可见区间与 State transition 原子联结，恢复时不得扫描最新值、回退 seed 或
    复活已释放的历史 publication。

## 13. 最小验收矩阵

| 类别 | 场景 | 预期 |
| --- | --- | --- |
| 普通环 | `A`、`B` 通过普通 output 互相绑定 | compiler 拒绝 ordinary data cycle |
| 普通自环 | 普通输入直接绑定 `A.output -> A.input` | 继续按 ordinary data self-cycle 拒绝 |
| feedback 编译 | 单 self-feedback、两路互斥 route、exact descriptor | 通过 |
| feedback seed | `A[0]` 从 START 激活 | 只读取 initial，不读取任何历史 A publication |
| feedback 提交 | `A[0]` 输出 `x0` 并选择 feedback route | 先确认 `PublicationKey(A[0])` 和 route，再创建 `A[1]` |
| feedback 读取 | materialize `A[1]` | 精确读取 `x0`；publication identity 必须等于 cause 中的 `A[0]` |
| feedback 前驱 | 已存在 `A[n - 1]`、`A[n]` 多份历史 evidence | `A[n + 1]` 只能读取 `A[n]`，不得扫描或读取更老记录 |
| feedback 伪造 | cause 指向 `A[n - 1]`，target 却是 `A[n + 1]` | reducer/compiler admission fail closed |
| feedback 恢复 | `A[n]` 已提交、`A[n + 1]` frame 安装前崩溃 | 从 exact committed evidence 重建，不回退 seed |
| feedback 缺值 | `A[n + 1]` 缺少 `A[n]` evidence | claim 前失败，不搜索其他 A publication |
| feedback 退出 | `A[n]` 选择 terminal route | 当前 `A[n]` output 成为 Graph output；没有 `A[n + 1]` |
| feedback 无出口 | 无条件 direct `A -> A` 且没有可证明正常出口 | compiler 拒绝，不能依赖 `max_supersteps` 完成 |
| feedback 失败 | self-feedback 节点返回 failure | Graph 进入 terminal Failed；不得把 feedback 当失败重试 |
| feedback retention | predecessor evidence release 前后崩溃 | Pending successor 永不丢输入；已结算 successor 不复活旧值 |
| cause | seed/repeat cause 重叠、缺失或不唯一 | compile fail closed |
| 后续多回边 | generic 互斥 conditional routes 各自回到 target | cause 唯一匹配；同一 binding 始终读取其唯一 repeat source |
| 多回边 | 可能同时到达且没有 Join | compiler 拒绝，不调度两次 |
| 多回边 | 多路线共享 publication 但没有互斥证明/Join | compiler 拒绝，不因值相同而去重 |
| 多回边 | 显式 Join 汇聚多个来源 | 第一阶段拒绝；后续只产生一个 target activation，值仍走显式 inputs/aggregator |
| routing 基数 | 两个 `(target, cause)` 在去重前同时成立 | fail closed，不能用 `set[target]` 吞掉一个 cause |
| cause 真实性 | cause 只引用更早坐标，但 source 未成功或未选择该 route | reducer/admission 拒绝 |
| cause recovery | frontier 已推进后重启 | 从 State cause 选择 repeat，不看 frame presence |
| 坐标 | repeat publication 与 target 同一 superstep | 拒绝 |
| 坐标 | repeat publication 晚于 target activation | 拒绝 |
| 坐标 | repeat publication 虽更早但不是 cause 的紧邻前驱 | 拒绝；strict earlier 不能代替 exact predecessor |
| seed | 第一阶段 initial 为 `NodeOutputRef` 或缺少唯一 Start cause | compiler 拒绝；不以 `superstep == 0` 猜首轮 |
| 丢证据 | 后续 activation 缺 repeat evidence | claim 前失败，不重新 seed |
| 节点失败 | activation 提交 `FailedGraphNode` | 本次 Graph 终止；不产生下一 frontier，不允许 resume/override/skip |
| 并行失败 | frontier 中一个 Failed、其他节点仍 Pending | 排空当前 frontier 全部 Pending，包括未启动 callable、resource waiter 和 nested child；随后进入 terminal Failed，绝不 routing |
| 失败恢复 | 从 terminal Failed State 重启 | 返回同一 terminal failure；零 claim、零 Port 调用 |
| 混合结算 | 同一 frontier 同时有 Failed 与 Interrupted | terminal Failed 优先，不进入 awaiting interrupt |
| Failover 重试 | `InvokeOnce` 返回可重试 `PortOutcome` | 作为 success value 提交；由 failover route 产生新的 activation |
| 非法恢复 | 对 Failed 节点提交 resume/skip action | admission 拒绝，State 不变 |
| fresh recovery | `StartGraphRun` 已提交、graph input 尚未装入内存 | 从同 commit evidence 重建 |
| 复用 | 多消费者读取同一 producer output | 一个 PublicationKey/payload |
| 原子性 | producer 执行后、settlement 前崩溃 | 无 confirmed result、无下一 frontier；只能按未确认 activation 的崩溃重放规则处理，不属于 failed retry |
| 原子性 | settlement/result 后、frame 安装前崩溃 | 从同一 committed result 重建，不重跑 producer |
| 丢确认 | 同 CommitKey 重试且 candidate/evidence 相同 | 幂等返回 exact candidate |
| 冲突 | 同 CommitKey 出现不同 candidate/evidence | fail closed，不覆盖 |
| fingerprint | 相同逻辑 evidence 以不同容器顺序编码 | canonical fingerprint 相同；不得依赖对象相等/`repr` |
| 并发 | 第一阶段出现 sibling/join | compiler 拒绝；顺序等价在 P3 验证 |
| 防陈旧 | stale revision/token/coordinate | reducer/evidence owner 拒绝 |
| 恢复 | exact State head N + 在 N 可见的完整 evidence（无 continuation） | state-led 重建后与 complete continuation 得到同一 disposition |
| 恢复 | publication 出生于 N 之前、在 N 仍 live | 必须装载；不能按“只取 N 写入的 records”漏掉 |
| 恢复 | evidence 缺失、descriptor/codec 不匹配 | claim 前安全失败 |
| 退出 | target 选择 `done -> END` | successful completion，不能靠 max_supersteps |
| 组合 | generic conditional/join/nested/multiple feedback | 第一阶段 compile 拒绝；第二阶段沿同一模型逐项开放 |
| codec | encoder/decoder 非 exact bytes/value、版本不符 | 原子提交或 claim 前失败 |
| 安全 | payload 超限、异常/log 含 value repr 或 secret | fail closed 且不泄漏 |
| retention | 多轮 direct self-loop 达到 hard cap | 安全停止；live evidence 不得无界增长 |
| retention crash | evidence release 与 State transition 前后崩溃 | 不丢 live value，不复活 dead value |
| 上限 | 达到 `max_supersteps` | 按真实 frontier 序号停止 |
| 上限恢复 | 重启时传入更大的 `max_supersteps` 或重置 live-evidence cap | claim 前拒绝；继续使用 Start 时冻结的 run policy |
| cyclic Join | Join 位于 control cycle | P3 occurrence identity 落地前由 compiler 拒绝，绝不按 node id 跨轮累计 arrival |

## 14. 当前状态

本版已纳入补充审计第 10 节的直接自反馈闭合模型：第一阶段不再使用 `A -> B -> C -> A` 作为准入形状，而是固定
为带 feedback/terminal 二选一 route 的 direct self-feedback，并用 State cause + `RELATIVE(1)` 共同证明 exact
immediate predecessor。该修订没有新增 owner、runner、payload slot 或 runtime 特判，也没有放开普通 data self-cycle。

### 14.1 已完成：P1 内部切片

以下生产代码和测试已经落地：

- `FeedbackInputBinding` 只允许一个 typed initial/repeat 声明；第一阶段收窄为
  `initial=GraphInputRef`、`repeat=target.output`；普通 `NodeOutputRef` self-cycle 仍由 compiler 拒绝；
- immutable `CompiledActivationRules` 记录唯一 seed/repeat rule、两路 conditional route 和
  `RELATIVE(1)` selection；编译器拒绝 nested、join、multi-feedback、额外控制源和无 terminal 出口；
- `GraphActivationIdentity`、`ActivationReference`、`StartActivationCause`、`RoutedActivationCause` 归 State owner；
  `StartGraphRun`/`AdvanceGraphFrontier` 携带 activation cause，reducer 校验 predecessor 的 run、superstep、settlement
  和实际 route；acyclic Join progress 保存已提交的完整 `ActivationReference`，不再只保存 source node id；
- routing 在收敛 target 前保留每个 `(target, cause)` candidate，反馈 route 只创建一个下一轮 activation，terminal
  route 只完成当前 frontier；零 cause 或多 cause 一律 fail closed；compiler 是 immutable compiled plan 的唯一 admission
  owner，runtime 直接消费已准入计划，不对进程内计划重复做伪造对象式深校验；
- materialization 首轮只读 graph input，后续只按 State cause 指定的紧邻上一轮 publication 读取；不扫描最新值、不回退
  seed、不接受 feedback activation 的 input override；
- ordinary、resource 和 nested sibling 统一遵循 `Pending > Failed > Interrupted > Settled`；failed sibling 不会阻止
  当前 frontier 的其他 Pending 首次执行，awaiting child 只在 terminal Failed 确定后清理；
- recovery preflight 仍是同一执行模型的无副作用 proof，不是第二个 runner；循环位置由具名 immutable signature 表达，
  cause 按相对 predecessor distance 归一化，只有会改变后继的 availability/control facts 进入等价关系；
- Graph failure 已保持 durable terminal 边界：失败不是 retry signal，Failover 仍是唯一重试 owner。

这部分是 P1 的进程内 typed declaration/compiler/runtime proof，不是 durable persistence 实现。当前仍没有公开
`Graph.feedback(...)`，也没有引入第二个 runner、State 模型或 feedback 专用 payload slot。

### 14.2 有意保留的边界

- acyclic control Join 使用已提交的完整 `ActivationReference` 作为 arrival provenance；完整的跨轮
  `Join occurrence identity` 留到 P3，在此之前 compiler 直接拒绝任何位于 control cycle 的 Join；
- P1 只支持单 scope、单 callable target、单 feedback binding 和一条 feedback/一条 terminal route；nested、multi-branch、
  generic conditional feedback、cyclic Join 和多消费者不在本切片；
- `Graph.run(state=...)` 的完整 state-led durable evidence reader、原子 State+publication commit、重启 retention、codec
  和跨语言 conformance 留到 P2；没有这些 capability 时不宣称 durable feedback 已可用。

### 14.3 P1 收口架构审计

复杂度门禁是 package-wide 高召回提示，不是按 P1 diff 单独计数；当前工作树同时包含 events、failover、logging 等独立
typed domain 增量。因此 ratchet 只在逐项查看 hotspot、clone、单次使用 record、跨模块调用和零债务 health 报告后按
当前完整 package 重新定标，不把数值增长本身解释为设计正确，也不靠拆 helper 或关闭规则制造下降。

本轮实际清掉的可消除成本包括：command 的双份 activation 真相、普通 routing 与 feedback routing 双路径、运行时重复
compiled-plan 深校验、child 全局栅栏、提前 child cleanup、分散的 activation lookup 拼装，以及 routing/output
availability 和 activation batch validation 的重复逻辑。人工审计又补出了两个测试原先未覆盖的真实问题：普通节点
failure 同样必须触发 awaiting child 的终局清理；recovery 中 awaiting child 不能挡住普通 Pending，模拟 session 排空后
必须 fence execution。live 与 state-led proof 现已使用同一顺序。

最终门禁结果为：ruff 与 format 通过、pyright 0 error、复杂度 ratchet 与 zero-debt health 通过、1316 个测试全部通过，
生产包 6956 条语句和 2388 个分支覆盖率均为 100%，sdist/wheel 构建及 twine 校验通过。清理得到的复杂度下降已同步
锁紧 ratchet，不留可回涨的旧上限。2026-09-03 从 monorepo 根目录运行 `pre-commit run --all-files`，全部 hook
通过；其中 Cloudflare TypeScript Persistence 因当前迁移目录没有匹配文件而按规则跳过。

保留的增长必须直接表达领域事实：State-owned activation/cause/Join arrival、terminal Failed、compiler 的唯一准入 proof、
具名 recovery cycle signature 及其 availability 维度。`_RecoveryCycleSignature` 虽只作为一个 dict key 使用，其字段由
关键字构造并通过等值/哈希定义不动点，不回退成难读的位置 tuple。最终 health 指标继续要求 unused private definition、
unread field、未消费 coroutine/task 和 import cycle 全部为零。

### 14.4 下一步：P2 durable 垂直切片

P2 将沿 P1 已冻结的 declaration、cause、publication、routing、commit 和 recovery 单一模型补齐：fresh input 原子证据、
successful settlement/publication 原子提交、exact-head + live-evidence 重建、release/retention 和故障注入。P2 全部门禁通过
后，才评估开放公开 `Graph.feedback(...)` API。

历史取舍仍记录在
[审计回复](./graph-delayed-loop-implementation-plan-review-response.zh-CN.md)
和[补充审计](./graph-delayed-loop-implementation-plan-review.zh-CN.md#10-补充审计自己读取上一次自己尚未闭合)中；这些文档不替代本节的当前实施状态。
