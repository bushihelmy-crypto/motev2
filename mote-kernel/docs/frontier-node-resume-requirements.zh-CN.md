# Frontier 节点失败恢复需求

## 1. 文档信息

- 状态：Ready for implementation（已按当前 GraphState 能力边界通过评审）
- 所属项目：Mote Kernel
- 需求类型：Graph execution / GraphState resume semantics
- 目标版本：未定
- 本期实现 owner：Python Kernel 现有 graph execution 与 GraphState 模块

本需求只扩展当前 Kernel 已有的能力层次：typed GraphState、pure transition、execution projection、唯一 graph execution
engine 和确定性测试。本文中的“恢复”是指调用方基于一个合法 `GraphRunState` snapshot 继续执行，不代表已经实现真实 state
store、跨进程事务、journal、input binding 或泛型业务结果恢复。

## 2. 背景

当前图执行引擎以 superstep 为推进单位。`GraphRunState.frontier` 保存当前 superstep 激活的节点集合；同一 frontier 中的节点读取同一份不可变输入快照，由一个 batch execution lease 领取并执行，全部结果收集后再统一计算下一 frontier。

当前失败语义存在以下限制：

1. 任一节点返回 `NodeFailure` 后，整个 `GraphRunState` 被标记为 `FAILED`。
2. 失败转换会清空 frontier、join progress 和资源状态，丢失失败发生时的可恢复执行位置。
3. 并行 frontier 中只要一个节点失败，同批其他节点已经产生的成功 routing contribution 不会进入 GraphState。
4. `FAILED` 同时被用于节点执行失败和 operator abort，无法区分可恢复失败与不可恢复终止。
5. 当前 claim fencing 只能处理 active lease 被明确 fence 后的同一 superstep 重试，不能复活已经结算为失败的节点。

因此，系统无法安全地实现以下目标场景：

```text
frontier(superstep=3): a, b, c

attempt 1:
  a -> success
  b -> failure
  c -> success

resume:
  仅重新执行 b

attempt 2:
  b -> success

最终使用 a、b、c 的完整成功贡献统一路由到下一 frontier。
```

## 3. 目标

本需求需要实现显式的 frontier resume 语义，使 Kernel 能够：

1. 将节点失败建模为当前 frontier 的局部阻塞，而不是整张 GraphRun 的终态。
2. 在 GraphState 中记录 frontier 每个节点的当前结算事实。
3. 保留已经成功节点的 routing contribution，避免 resume 时重复执行成功节点。
4. 通过显式、受 revision fencing 保护的 resume 转换，将失败节点恢复为待执行状态。
5. Resume 后继续使用 batch lease，但只领取当前待执行节点。
6. 当原始 frontier 的所有节点最终成功后，统一计算 routing、join progress 和下一 frontier。
7. 保持循环图、自环、条件路由、join、资源节点、interrupt 和嵌套图的既有所有权边界。
8. 对传入的恢复状态、旧 revision、旧 lease 和迟到结果保持 fail closed。

## 4. 非目标

本需求不包括：

1. 将 execution lease 改为每个节点一把独立 lease。
2. 支持多个 worker 分布式领取同一 frontier 中的不同节点。
3. 修改静态 `GraphDefinition` 或在 resume 时动态增删图节点、边。
4. 在 `GraphRunState` 中保存无限增长的完整 attempt 历史。
5. 自动重试策略、退避算法、最大重试次数或失败分类策略。
6. 为不具备幂等、receipt 或 reconciliation 语义的外部副作用提供自动安全保证。
7. 在 owner、生命周期、首个跨语言 consumer 尚未确认前发布新的 conformance wire schema。
8. 设计默认公共 composition entry point。
9. 新增 authoritative state store、append-only journal 或真实 GraphState/DomainState 存储事务。
10. 新增 durable input binding，或校验不同 `StepRequest` 调用之间的泛型 input identity。
11. 持久化泛型 `NodeSuccess.output`，或在 resume 后重建整个 frontier 的聚合业务输出。
12. 承诺跨进程、跨调用者或进程重启后的完整业务事实恢复。

## 5. 术语

### 5.1 Graph node definition

静态图中的节点定义，由 `GraphDefinition` 和 `NodeId` 标识。它描述节点是什么以及节点之间如何连接，不保存某次运行的结算状态。

### 5.2 Frontier

一个 GraphRun 在某个 superstep 中，由同一份已提交状态激活，并且必须共同完成路由结算的一组节点调用。

Frontier 是 superstep 的完整激活集合与结算边界，不等同于某次 lease 实际领取的任务子集。

### 5.3 Node activation

某个静态节点在一个特定 GraphRun 和 superstep 中的逻辑调用，其稳定坐标为：

```text
(run_id, superstep, node_id)
```

循环再次进入相同 `NodeId` 时会产生新的 superstep，因此属于新的 node activation。

### 5.4 Execution attempt

对一个或多个待执行 node activation 的一次具体执行尝试，由 execution token generation 和 attempt identity fencing。

Resume 不创建新的 node activation；它为相同 activation 创建新的 execution attempt。

### 5.5 Routing contribution

成功节点对本 superstep routing 的 GraphState contribution，包括继续沿静态边执行或选择一个条件 route。它是恢复控制流所需的
事实，不是节点的业务输出。

## 6. 核心设计

### 6.1 状态分层

整体状态应遵循以下分层：

```text
GraphRunState
├── status                 GraphRun 整体生命周期
├── superstep              当前逻辑执行世代
├── frontier               当前 superstep 的完整节点结算状态
├── join_progress          跨 superstep 的 join 到达事实
├── resources              当前 frontier 的 resource admission/scheduler snapshot
├── execution              当前 batch execution lease
├── interrupt              当前 interrupt generation state
├── abort                   仅属于 ABORTED 的 typed reason
└── revision               GraphRun 并发版本
```

### 6.2 GraphRun 生命周期

`GraphRunStatus` 应只描述整张图的生命周期：

```python
class GraphRunStatus(Enum):
    RUNNING = auto()
    SUSPENDED = auto()
    COMPLETED = auto()
    ABORTED = auto()
```

语义要求：

- 节点失败不得把 GraphRun 转换为终态。
- 节点失败表示 `RUNNING` GraphRun 的当前 frontier 被阻塞。
- `AbortGraphRun` 转换为 `ABORTED`，且不可 resume。
- `COMPLETED` 和 `ABORTED` 均为 GraphRun 终态。
- `ABORTED` 至少持有独立的 typed `GraphAbort(reason)`，不得复用 node failure。
- `abort` 只允许出现在 `ABORTED` 状态，其他状态必须为 `None`。
- 本期保留 abort 时的完整 frontier 作为只读诊断位置；该 frontier 不再是可恢复位置，也不得参与后续 routing。

### 6.3 Frontier 状态

`frontier` 从节点 ID 元组升级为有节点结算事实的状态对象：

```python
@dataclass(frozen=True, slots=True)
class GraphFrontierState:
    nodes: tuple[GraphFrontierNode, ...]


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement
```

节点结算使用明确的类型变体，不使用字符串 discriminator：

```python
GraphNodeSettlement: TypeAlias = (
    PendingGraphNode
    | SucceededGraphNode
    | FailedGraphNode
)


@dataclass(frozen=True, slots=True)
class PendingGraphNode:
    pass


@dataclass(frozen=True, slots=True)
class SucceededGraphNode:
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNode:
    failure: GraphFailure
```

本期不在 `GraphRunState` 中保存完整 attempt 历史。`FailedGraphNode.failure` 表示该 activation 最近一次已结算的失败事实。

### 6.4 Frontier 派生状态

Frontier 的整体状态必须从节点 settlement 推导，不得再持久化一份可与节点事实冲突的状态字段：

```text
存在 Failed 节点                         -> BLOCKED
不存在 Failed 且至少存在 Pending 节点     -> EXECUTABLE
所有节点均为 Succeeded                   -> SETTLED
```

可以提供只读的 `GraphFrontierStatus` 投影或计算属性，但它不是 authoritative state。

### 6.5 Routing contribution

GraphState 不能依赖 execution 包，因此 routing contribution 必须由 state owner 定义窄类型，例如：

```python
GraphRoutingContribution: TypeAlias = (
    ContinueGraphRouting
    | SelectGraphRoute
)
```

成功节点必须保存 routing contribution，原因包括：

- Resume 后成功 sibling 不得重新执行。
- 条件 route 必须保持原来的选择。
- direct edge 和 join arrival 必须在 frontier 最终 settle 时参与统一 routing。

泛型 `NodeSuccess.output` 不写入 GraphState。GraphState resume 只恢复执行位置与 routing contribution，不恢复成功 sibling 的泛型
业务输出。

### 6.6 Batch lease

Execution lease 继续是 batch 粒度，不拆为 node-level lease。

Lease 必须只有一套 authoritative claimed-subset identity，并能被 GraphState reducer 精确证明对应当前 frontier 的 Pending
activation subset。实现可以：

- 继续保存现有稳定派生的 `task_ids`，但须将 task identity 的窄派生/校验规则放到 state owner，使 reducer 能从
  `(run_id, superstep, node_id)` 独立验证对应关系；或者
- 改为保存 `node_ids`，在 execution projection 中派生 `task_id`。

Planner 只为 `PendingGraphNode` 创建任务。正常首次执行时，这通常是整个 frontier；resume 后则只是之前失败的节点。

任务身份继续由以下坐标稳定派生：

```text
(run_id, superstep, node_id)
```

同一 activation 的 resume attempt 保持 task identity 不变，但使用新的 execution token generation 和 attempt identity。

Lease 字段形态不是核心 resume 语义的 blocker。无论选择哪种表示，都必须保持 `(run_id, superstep, node_id)` 是 task identity
的唯一派生坐标；不得让 `task_ids` 与 `node_ids` 同时成为可独立变化的 authoritative state，也不得为迁移保留 fallback identity
路径。Execution guard 仍负责 compiled graph/topology 校验，但不能代替 reducer 对 lease subset、outcome coverage 与 Pending-only 更新的
authoritative validation。

### 6.7 校验责任边界

GraphState 不持有 `CompiledGraph`，因此 reducer 只验证 state-owned 的结构、并发和生命周期不变量，不复算静态 topology。
责任必须严格划分为：

| Owner | 必须验证的内容 |
| --- | --- |
| GraphState reducer | revision、精确 lease token、claimed subset 与 outcome 的完整且唯一覆盖、仅更新 Pending、Succeeded 不可覆盖、settlement 结构、canonical order、资源与 lease 清理、合法生命周期转换；若 lease 保存 `task_ids`，还须以 state-owned 规则验证其 activation identity |
| Execution projection/guard | definition id/version 匹配、frontier node 属于 compiled graph、投影出的 task identity 正确、routing contribution 对该 node 合法、join progress 对应已声明 join |
| Execution routing | 使用完整 frontier contribution 与 prior join progress 计算唯一 next frontier，并验证 conditional route、direct edge 与 join 语义 |

State 包不得为复算 routing 而依赖 execution 包。若未来要求 reducer 独立复算 topology，必须先设计由 state owner 持有的窄、
版本化 topology projection；该设计不属于本需求。

### 6.8 架构与工程约束

本期实现必须遵守现有 Kernel 边界：

- `execution` 仍是唯一 graph execution engine；不得新增 private runner 或第二条 resume execution path。
- GraphState 只记录可恢复的控制流位置，不能借本需求写入泛型业务 output 或代替 DomainState。
- State transition 必须保持纯函数：接收 typed state 与 typed command，返回新的 immutable state，不直接执行服务、工具或存储副作用。
- Execution planner、collector、routing 与 nested projection 必须通过窄 typed DTO/command 交互；不得使用 `Any`、bare dictionary、反射或字符串 discriminator。
- 必须直接替换旧 authoritative model；不得保留 compatibility alias、旧新 reducer 双 dispatch、hidden mutable cache 或 fallback identity path。
- 本期不设计默认公共 composition entry point，也不把 internal DTO 提升为跨语言 durable protocol。

## 7. 状态转换需求

### 7.1 StartGraphRun

启动图时：

- `status = RUNNING`
- `superstep = 0`
- 入口节点形成第一个 frontier
- 每个入口节点初始为 `PendingGraphNode`
- 节点使用 canonical order

### 7.2 ClaimGraphExecution

Claim 必须满足：

1. GraphRun 为 `RUNNING`。
2. Frontier 为 `EXECUTABLE`。
3. 不存在 active execution lease。
4. Claim tasks 精确覆盖 planner 为当前 frontier 选出的 Pending 节点。
5. 已经 Succeeded 或 Failed 的节点不可被 claim。
6. Claim 创建新的 execution generation。

### 7.3 SettleGraphExecution

一次 execution attempt 正常返回的全部 typed task outcomes 必须原子结算到 frontier。

结算必须满足：

1. 命令持有当前精确 execution token。
2. Outcomes 精确覆盖 lease 中的任务，不缺失、不重复、不越界。
3. 本次只能更新 lease 中的 Pending 节点。
4. 已经 Succeeded 的节点不可被覆盖或降级。
5. 成功 outcome 写入 routing contribution。
6. 失败 outcome 写入 failure。
7. 本次 lease 被清除。
8. 本次资源 scheduler 状态被释放或清理。

结算后分为两种结果：

#### A. Frontier blocked

若合并后存在 Failed 节点：

- GraphRun 保持 `RUNNING`
- superstep 不变
- frontier 原始节点集合不变
- 成功节点及其 routing contribution 保留
- 失败节点保留失败事实
- join progress 不推进
- interrupt resolution 不被消费
- planner 不得继续调度该 frontier

#### B. Frontier settled

若合并后所有节点均为 Succeeded：

- 使用整个原始 frontier 的全部 routing contribution 统一计算 routing
- 合并进入本轮前的 join progress
- 若产生下一节点集合，原子创建下一 frontier，并使 `superstep += 1`
- 若不产生下一节点且没有未完成 join，GraphRun 转换为 `COMPLETED`
- 只有此时才消费当前 resolved interrupt

执行层可以保留 `AdvanceGraphRun`、`CompleteGraphRun` 和阻塞转换的不同命令类型，也可以使用一个严格 tagged settlement command；无论采用哪种形式，上述不变量必须由 GraphState reducer 重新验证，不能只信任 execution 层。

#### Typed failure 与 Python exception 边界

只有节点按 node contract 显式返回的 typed `NodeFailure`，以及现有 typed nested outcome 边界明确投影出的 node failure，才能形成
`TaskFailure` 并结算为 `FailedGraphNode`。

以下情况不属于可 resume 的 node settlement：

- node contract violation，例如返回非 `NodeSuccess | NodeFailure`；
- 节点抛出的普通 Python exception；
- scheduler、executor、resource scheduler、routing 或其他 execution infrastructure error。

出现上述异常时必须保持当前错误传播与 lease/fence 恢复边界：异常向调用方抛出，不得转换或伪装为 `FailedGraphNode`，也不得提交
同批其他节点的部分 settlement。若异常发生时 GraphState 已有 active lease，该 lease 保持有效，直到调用方确认执行已经停止并通过精确
token 显式 fence；若异常发生在 claim 提交前，则不存在需要 fence 的 lease。即使同批其他节点已经在内存中返回 typed
success/failure，这些结果也不能单独提交。

### 7.4 ResumeGraphFrontier

增加显式命令：

```python
@dataclass(frozen=True, slots=True)
class ResumeGraphFrontier:
    expected_revision: int
```

Resume 前置条件：

1. GraphRun 为 `RUNNING`。
2. 当前 frontier 为 `BLOCKED`。
3. 不存在 active execution lease。
4. 不存在未清理的 resource admission。
5. `expected_revision` 精确匹配传入 reducer 的当前 `GraphRunState` revision。

Resume 的纯状态转换为：

```text
Succeeded -> Succeeded
Failed    -> Pending
```

Resume 必须：

- 保持原始 frontier 节点集合不变；
- 保持 superstep 不变；
- 保持成功节点 routing contribution 不变；
- 保持 join progress 不变；
- 增加 GraphRun revision；
- 使基于旧 revision 准备的 claim、admission 和 settlement 失效。

Resume 不得自动发生。失败分类、重试次数和是否 resume 由更高层 flow/failover 语义决定。

### 7.5 FenceGraphExecution

`FenceGraphExecution` 是 claim 已提交、但 attempt 因取消、普通 Python exception 或 infrastructure error 未能形成 typed settlement 时，
回到 scheduler-quiescent state 的唯一转换。

调用该命令前，调用方必须已经停止对应 worker，并确认该 attempt 不会继续执行外部副作用。Reducer 不能证明这一外部事实，只负责以
精确 execution token fail closed。

转换必须：

- 仅接受 `RUNNING` 且持有完全匹配 execution token 的 GraphRun；
- 原子清除 active execution lease，以及与该 lease 共同属于当前 frontier 的完整 resource admission/scheduler snapshot；
- 保持 frontier settlement、superstep、join progress、interrupt 和 abort 不变；
- 增加 revision，并保持 execution generation 单调；
- 拒绝旧 token、错误 token、无 active lease 或 terminal GraphRun。

清理完成后，GraphRun 必须处于 scheduler-quiescent state。若 frontier 仍为 EXECUTABLE，调用方可以重新 prepare，使 Pending subset 重新
进行 resource admission 和 claim；也可以直接执行 `AbortGraphRun`。Fence 本身不得生成 `FailedGraphNode`，也不得把异常 attempt
视为一次 typed settlement。

### 7.6 AbortGraphRun

Abort 必须：

- 将 GraphRun 转换为 `ABORTED`；
- 保留 abort 时的完整 frontier 作为只读 diagnostic position；
- 写入独立的 typed `GraphAbort(reason)`，不复用 node failure；
- 只接受 reducer 所见 state 中不存在 active execution lease 的 GraphRun；
- 若存在尚未 claim 的 resource admission，由 abort transition 原子释放并清除；
- 不得直接清除 active lease，也不得将 active execution 隐式视为已经完成外部 fencing；
- 按既有规则取消未消费的 interrupt generation；
- 可以保留 prior join progress 作为诊断事实，但不得再次参与 routing；
- 拒绝任何后续 resume、claim、advance 或 complete。

纯 reducer 无法证明正在执行的 worker 已停止，因此 active execution 必须先由外部停止并以精确 token fence，再提交
`AbortGraphRun`。没有 active lease 的 resource admission 只表示尚未领取的 scheduler reservation，可以由 abort 原子释放；这与
清除仍可能运行的 lease 不同。Abort failure 与 node failure 必须使用不同的 owner 和状态语义，不得再次合并为一个 `FAILED`
状态。

## 8. Planner、Collector 与 Routing 需求

### 8.1 Planner

Planner 必须：

- 验证 frontier 引用的节点属于当前 compiled graph；
- 仅为 Pending 节点生成任务；
- 对 BLOCKED、COMPLETED、ABORTED 和 SUSPENDED 状态返回各自明确的不可执行 disposition；
- 继续执行 `max_supersteps` 和 `max_parallel_tasks` 限制；
- 使用 run、superstep、node 坐标派生稳定 task identity。

Prepare/result projection 必须区分：

```text
ExecutableFrontier
BlockedFrontier
SuspendedGraph
CompletedGraph
AbortedGraph
```

具体可以使用严格 tagged DTO 或 enum disposition，但不得把 blocked、suspended、completed 和 aborted 统一投影为空
`PreparedFrontier` 或普通 idle。调用方必须能够判断接下来应显式 resume、等待 interrupt resolution，还是结束运行。

### 8.2 Collector

当本次 lease 的所有 task 均正常返回 typed outcome 时，Collector 必须保留全部结果。

当前“出现任意 typed failure 后丢弃所有 success，并只选择排序后的第一个 failure”的行为必须删除。多个 typed failure 必须全部进入
frontier settlement。若任一 task 抛出普通 exception 或发生 infrastructure error，则按第 7.3 节的异常边界整体不提交 settlement。

### 8.3 Routing

Routing 必须只在 frontier 全部成功后发生，并以以下完整集合为输入：

```text
此前 attempt 已保存在 GraphState 中的成功 contribution
+ 本次 attempt 新提交的成功 contribution
+ 进入当前 superstep 前的 join progress
```

Routing 不得只基于本次 lease 的节点子集。

### 8.4 Attempt-local execution result

一次 execute 调用返回的 task results 只表示当前 execution attempt 实际领取并执行的节点，不表示整个原始 frontier 的聚合业务
结果。

例如首次 attempt 中 A 成功、B 失败，resume attempt 只执行 B，则第二次结果只包含 B 的 `TaskSuccess.output`。GraphState 会保留 A
此前的 routing contribution，但不会重建或再次返回 A 的泛型 output。

现有 `ExecutedSuperstep` 名称会暗示结果覆盖整个 superstep，建议改名为 `ExecutedFrontierAttempt`。该重命名不是核心 resume
语义的 blocker，可以随本次模型替换完成，也可以作为独立重构；若实施重命名，应同步更新公开 export、测试 driver 和文档，不得增加
兼容 alias。

## 9. 循环语义

当前建模支持图内拓扑循环和自环，本需求必须保持该能力。

循环进入相同静态节点时：

```text
superstep 0: node A
superstep 1: node B
superstep 2: node A
```

`superstep 0 / A` 与 `superstep 2 / A` 是不同 node activation，因此新 frontier 中的 A 必须重新初始化为 Pending，不能继承旧 frontier 的 settlement。

相反，resume 同一个失败 activation 时：

```text
superstep 2 / A / attempt generation 3 -> failed
superstep 2 / A / attempt generation 4 -> succeeded
```

其 superstep 和 task identity 不变，仅 execution attempt identity 改变。

嵌套 GraphDefinition 递归仍不在支持范围内。

## 10. Join 语义

Join progress 是跨 superstep 的 GraphState arrival 事实，必须与当前 frontier node settlement 分开保存。

要求：

- Frontier blocked 时不提交本轮新的 join arrival。
- Frontier 中成功 sibling 的 routing contribution 保留，但只在 frontier 全部成功后统一应用。
- Resume 不修改 prior join progress。
- Frontier 最终 settle 后，join progress 只能被统一 routing 计算更新一次。
- 循环再次到达同一个 join 时，必须属于新的 superstep 推进，不得复用已消费的本轮 settlement。

## 11. 资源语义

资源 admission 与 execution lease 的既有顺序保持不变：admission state 先提交，execution claim 后提交。

Resume 场景要求：

- admission 只考虑 Pending 节点；
- 已经 Succeeded 的节点不得重新申请资源；
- attempt settle 为 blocked 时必须释放/清理 scheduler resource state；
- Resume 后失败节点按新 attempt 重新参与 admission；
- 旧 admission command 因 revision 变化被拒绝。

资源状态必须在所有异常与终止路径上保持闭包：

```text
claim 后 attempt 异常:
  execution != None, resources != None
      -- 外部停止 + FenceGraphExecution(exact token) -->
  execution == None, resources == None

admission 已提交但尚未 claim:
  execution == None, resources != None
      -- AbortGraphRun -->
  status == ABORTED, execution == None, resources == None
```

不得出现只能重新 claim、却无法清理资源后 abort 的 GraphState。普通 retry 也不得复用异常 attempt 遗留的 resource snapshot；精确
fence 清理后必须从当前 Pending subset 重新计算并提交 admission。

## 12. Interrupt 语义

Resolved interrupt payload 必须持续投递到同一个未推进 superstep，直到该 frontier 成功推进、完成或被终止。

因此：

- Frontier blocked 时不得将 interrupt 标记为 consumed；
- Resume 后失败节点仍读取同一个 resolution-decoded immutable input；
- Frontier 成功 advance 或 complete 时才写入 consumption receipt；
- Abort 时按现有取消语义写入 terminal receipt；
- 第一版禁止在 blocked frontier 上请求新的 interrupt generation，避免 resume 与 interrupt 两个恢复协议重叠。

## 13. 嵌套图语义

Nested graph node 的完成条件仍然是确定性 child run 到达终态成功。

当 child frontier blocked 时：

- child GraphRun 保持 `RUNNING`；
- parent nested node 保持 Pending；
- parent 不得将 child blocked 转换为自己的 node failure；
- 必须 resume 原来的确定性 child run；
- 不得创建新的 child run 或更换 child run identity；
- child `COMPLETED` 后，parent nested node 才能提交成功 outcome。

Parent prepare 路径必须接收明确的 typed child projection，以区分：

1. `MissingChild`：child 尚未创建，允许产生一次确定性的 `StartGraphRun`；
2. `ActiveChild`：child 已存在，且处于 RUNNING（frontier 可为 EXECUTABLE 或 BLOCKED）或 SUSPENDED；复用相同 child run，parent node 保持 Pending；
3. `CompletedChild`：child 已完成，转换为 parent node success；
4. `AbortedChild`：child 已终止，转换为明确的 typed nested abort outcome。

Child aborted 如何映射为 parent failure，需要使用明确的 typed nested outcome，不得伪装为可 resume 的 child frontier failure。

“未提供 terminal result”不得同时表示 child 不存在和 child 尚未结束，否则恢复后可能重复产生 `StartGraphRun`。Typed child
projection 本期通过现有 typed request/result 边界由调用方提供，不新增 state-store lookup port。上述四种状态的语义必须唯一。

## 14. 输入一致性

同一 frontier 的所有节点必须基于同一份不可变 input snapshot 执行。Resume 不得让失败节点使用与成功 sibling 不同的逻辑输入。

本期沿用当前 `StepRequest` 与 interrupt resolution 的输入合同：

- 调用方负责在同一 frontier 的首次 attempt 和 resume attempt 中提供逻辑一致的 `node_input`；
- GraphState 不保存泛型 input 或 input binding；
- Kernel 不验证跨多个 `StepRequest` 调用的 input identity；
- resolved interrupt 继续由现有 interrupt payload 与 graph-owned decoder 提供输入，并遵循第 12 节消费语义。

因此，本期只承诺 GraphState snapshot 内的控制流 resume，不承诺跨进程或跨调用者的 input consistency。Durable input binding 是
未来 state-store 语义，不作为本需求 blocker。

## 15. 外部副作用安全

Node failure 不证明外部副作用没有发生。允许 resume 的副作用节点必须至少满足以下一种语义：

- 使用稳定 operation identity 实现幂等执行；
- 持久化外部 receipt 并在 resume 前查询；
- 将结果标记为 unknown 并执行 reconciliation；
- 由 capability-local failover 在返回 `NodeFailure` 前完成安全处理。

Kernel 的 resume 仅负责图执行位置恢复，不得隐式承诺任意 Port 调用可以安全重复。

## 16. 未来持久化工作

以下能力与本期 GraphState resume 有关，但明确延后：

- authoritative state store 与 CAS transaction mechanism；
- append-only execution journal 与 attempt 历史；
- GraphState/DomainState 的真实原子持久化；
- durable input binding；
- 泛型成功 output 或 result reference 的保存与重载；
- 进程重启后成功 sibling 业务结果恢复；
- 跨语言 durable GraphRun wire protocol。

本期不得为了这些未来能力在 Kernel 中增加临时 store、bare result dictionary、隐藏内存缓存或未确认 owner 的 port。

## 17. GraphState snapshot 不变量

任何传入 reducer 或 execution projection 的 `GraphRunState` snapshot 都必须满足：

1. Frontier 至少包含一个节点，除非 GraphRun 已完成且模型明确不再保留当前 frontier。
2. Frontier node identity 非空、规范化且唯一。
3. Frontier node 均属于匹配版本的 compiled graph；此项由 execution projection/guard 验证。
4. Pending settlement 不携带 routing 或 failure。
5. Succeeded settlement 必须携带合法 routing contribution，且不携带 failure。
6. Failed settlement 必须携带非空、trimmed failure，且不携带 routing。
7. Active lease 只能属于 RUNNING、EXECUTABLE frontier。
8. Lease task 必须是 frontier 中唯一的 Pending 节点子集，并与 planner claim 规则匹配。
9. BLOCKED frontier 不得保留 active lease 或 resource admission。
10. SETTLED frontier 只能作为同一原子 advance/complete 转换中的候选中间结果，不得作为可调度的稳定 GraphState snapshot。
11. SUSPENDED GraphRun 必须保持 scheduler quiescent。
12. COMPLETED 或 ABORTED GraphRun 不得被再次调度。
13. ABORTED GraphRun 不得被 resume。
14. Revision、execution generation 和 attempt identity 必须满足现有单调与 fencing 规则。
15. `abort` 只允许存在于 ABORTED GraphRun，且必须携带合法的 typed abort reason。
16. ABORTED frontier 和 prior join progress 仅供诊断，不得重新进入 execution projection 的可调度或 routing 路径。
17. ABORTED GraphRun 不得保留 execution lease 或 resource admission。
18. Fence 后的 GraphRun 不得保留被 fence attempt 的 execution lease 或 resource admission；frontier settlement 不因 fence 改变。

## 18. 失败与并发行为

以下情况必须 fail closed：

- Resume 基于旧 revision；
- Resume 一个非 blocked frontier；
- Resume 一个 suspended、completed 或 aborted run；
- Resume 时仍存在 execution lease 或资源 admission；
- Claim 包含 Succeeded 或 Failed 节点；
- Settlement 缺少 lease task outcome；
- Settlement 包含 lease 外 task；
- Settlement 尝试覆盖已有 Succeeded 节点；
- 旧 execution token 在 resume 后提交迟到结果；
- 旧 admission command 在 resume 后提交；
- Fence 使用旧 token、错误 token 或作用于无 active lease 的 state；
- Abort 试图直接越过 active execution fencing；
- recovery snapshot 的 settlement 组合不合法；
- recovered routing contribution 与当前 compiled graph 不兼容；
- blocked、suspended、completed 或 aborted 状态被投影为普通空任务并继续执行。

## 19. 可观测行为示例

### 19.1 并行节点部分失败

```text
state revision 10
superstep 3
frontier: a(Pending), b(Pending), c(Pending)

claim generation 4: a, b, c

settle:
  a -> Succeeded(Continue)
  b -> Failed("temporary failure")
  c -> Succeeded(SelectRoute("right"))

state revision 12
superstep 3
frontier: a(Succeeded), b(Failed), c(Succeeded)
frontier projection: BLOCKED
```

### 19.2 Resume

```text
ResumeGraphFrontier(expected_revision=12)

state revision 13
superstep 3
frontier: a(Succeeded), b(Pending), c(Succeeded)

claim generation 5: b
```

### 19.3 恢复成功并推进

```text
b -> Succeeded(Continue)

routing input:
  a -> Continue
  b -> Continue
  c -> SelectRoute("right")

state revision 15
superstep 4
frontier: next nodes initialized as Pending
```

### 19.4 循环重新进入同一节点

```text
superstep 4 frontier: a(Succeeded)
routing returns to a

superstep 5 frontier: a(Pending)
```

Superstep 5 的 A 是新的 node activation，不继承 superstep 4 的 settlement。

## 20. 验收标准

功能验收至少覆盖：

1. 单节点失败后可以显式 resume 并成功完成。
2. 并行 frontier 部分成功、部分失败时，所有节点 settlement 均保留在返回的 GraphState snapshot 中。
3. Resume 后只重新执行失败节点，成功节点不会再次调用。
4. 多个失败节点能够在同一次新 batch lease 中重新执行。
5. Resume 后再次失败会重新进入 BLOCKED，并可再次显式 resume。
6. 条件 route 在 sibling resume 后保持不变。
7. Direct edge 和 join contribution 不因 resume 丢失或重复应用。
8. Resume 不增加 superstep；成功路由到下一 frontier 才增加 superstep。
9. 自环和多节点循环进入新 superstep 时创建新的 Pending activation。
10. 相同 activation 的 resume task identity 不变，execution generation 改变。
11. 旧 revision、旧 lease、旧 admission 和迟到结果均被拒绝。
12. Resource node resume 时重新 admission，成功 sibling 不重新占用资源。
13. Resolved interrupt 在 blocked/resume 期间保持未消费，并在成功推进后消费一次。
14. Nested child blocked 时复用同一 child run，parent 不重复创建 child。
15. Abort 产生 ABORTED 终态且不可 resume。
16. 所有非法 frontier settlement、lease subset 和生命周期组合被对应 owner 的 validator/guard 拒绝。
17. 全部新增状态转换、恢复边界和可观察行为具有确定性测试。
18. Strict type checking、architecture tests 和 100% branch coverage 继续通过。
19. Planner/prepare 明确区分 `ExecutableFrontier`、`BlockedFrontier`、`SuspendedGraph`、`CompletedGraph` 和 `AbortedGraph`。
20. 合法形状但包含未知 conditional route 的 recovered contribution 被 execution guard 拒绝。
21. ABORTED frontier、abort、join progress 与 interrupt receipt 的合法组合能够通过 state validator，但不可调度。
22. Active child 未提供 terminal result 时不会产生第二个 `StartGraphRun`。
23. Resume attempt 的执行结果只包含本次 lease 实际执行的节点，不伪造或聚合此前成功 sibling 的泛型 output。
24. Node contract violation、普通 Python exception 和 executor infrastructure error 均向调用方传播，不生成 `FailedGraphNode`，不提交同批部分 settlement；若已有 active lease，则保留到外部停止并显式 fence。
25. Resource execution exception 后，外部停止 worker 并使用精确 token fence，会原子清除 lease 和本次完整 resource admission；随后可以重新 prepare 或 abort。
26. Resource admission 已提交但尚未 claim 时，`AbortGraphRun` 可以原子释放 admission 并进入 ABORTED；存在 active lease 时仍必须 fail closed。
27. Fence 使用旧 token、错误 token 或作用于无 active lease 的 state 时被拒绝，且不会误清理较新的 lease/resource state。
28. Lease 继续使用 `task_ids` 或改用 `node_ids` 均可，但 claimed subset 必须精确对应 Pending activation，且只能存在一套 authoritative identity；若保留 `task_ids`，其派生/校验规则必须由 state owner 提供给 reducer。
29. 若本次实施重命名 `ExecutedSuperstep`，则由 `ExecutedFrontierAttempt` 完整替换且不存在兼容 alias；未重命名不阻塞核心 resume 验收。
30. 代码库中不存在 `GraphRunStatus.FAILED`、顶层 node-failure `GraphRunState.failure`、`ExecutionStatus.FAILED`、`FailGraphExecution`、`FailTransition`、旧 flat frontier projection、丢弃 typed success/failure 的 collector 或双 authoritative lease identity 路径。

## 21. 实施阶段

建议按以下顺序落地：

1. 引入 `GraphFrontierState`、node settlement、routing contribution、typed abort 和 `ABORTED`，替换旧 flat frontier/`FAILED` 模型。
2. 完成 GraphState reducer、state validator、execution projection 和 compiled graph guard。
3. 修改 planner、claim 和 lease validation，使其只处理 Pending node subset，并提供明确的 prepare disposition；是否将现有 `task_ids` 改为 `node_ids` 在此阶段按改动收益决定。
4. 修改 collector 与 attempt settlement：所有 task 正常返回时完整保留本次 lease 的全部 typed success/failure；任一 task 抛出异常时整批不提交并保留 lease/fence 边界。
5. 实现 blocked settlement 与显式 `ResumeGraphFrontier`，保持成功 contribution、superstep 和 join progress。
6. 修改 routing，使其在 frontier 全部成功后合并跨 attempt 的完整 contribution，并统一 advance/complete。
7. 对齐 resource、interrupt、quiescent-only typed abort 和 nested child 生命周期；补齐 fence 清理 active attempt resources、abort 清理未领取 admission 的状态闭包；`ExecutedSuperstep` 重命名可随本阶段完成或拆为独立重构。
8. 删除旧 authoritative 路径，补齐 reducer、projection、executor、循环及恢复边界的确定性测试和 architecture assertions。

建议对应独立提交：

```text
refactor(kernel): model node-level frontier settlement
refactor(kernel): project executable pending frontier nodes
refactor(kernel): settle partial frontier outcomes
feat(kernel): resume blocked graph frontier
refactor(kernel): align nested resource and interrupt recovery
test(kernel): cover frontier resume recovery boundaries
```

## 22. Conformance 影响

当前 conformance manifest 未启用 graph-state 或 recovery suite，也没有已发布的 GraphRun durable wire schema。

本需求的 Python internal DTO 变更暂不要求创建占位 protocol schema。待以下条件全部确认后，应在同一跨语言变更中补充 conformance：

1. durable GraphState wire owner；
2. frontier/node settlement 的稳定身份和版本；
3. resume command 生命周期与失败行为；
4. 首个 Rust state mechanism consumer 或其他跨语言 consumer；
5. 对应 implementation runner。

届时至少应增加：

- strict versioned protocol schema；
- partial failure → resume → success recovery scenario；
- stale revision、stale lease 和 invalid recovered state cases；
- 循环节点跨 superstep与同 activation resume 的身份向量。

## 23. 待评审决策

实现时还需确定以下代码级形态，但它们不扩大本期范围，也不改变本文核心模型：

1. Settlement 使用一个统一 tagged command，还是保留 block/advance/complete 三种命令。
2. Prepare disposition 使用严格 tagged DTO，还是使用带窄 payload 的 enum/result 类型。
3. `MissingChild | ActiveChild | CompletedChild | AbortedChild` 类型放在现有 request/result 边界的哪个 owner 模块。
4. Lease 继续使用稳定派生的 `task_ids`，并将窄 identity 派生规则归 state owner，还是改为 `node_ids`；两者都必须满足唯一 authoritative identity 与 reducer 的 Pending subset 校验。
5. `ExecutedSuperstep` 在本次模型替换中重命名，还是拆为后续独立重构。

以下决定已在首轮评审后关闭：

- Lease 保持 batch 粒度，不改为 node-level lease；
- ABORTED 保留 abort 时的完整 frontier 作为 diagnostic position，并使用独立 typed abort reason；
- Active attempt 必须先由外部停止并以精确 token fence；fence 原子清除 lease 与该 attempt 的完整 resource admission；
- `AbortGraphRun` 不直接清除 active lease，但可以原子释放没有 active lease 的未领取 resource admission；
- 只有 typed `NodeFailure`/typed nested failure 能形成可 resume settlement，普通 exception 与 infrastructure error 保持 lease/fence 边界；
- 旧 FAILED、FailGraphExecution、FailTransition 和 flat frontier 路径必须被替换删除。

上述代码形态不阻塞核心 resume 实施。无论最终选择哪种形态，都不得通过兼容别名、bare dictionary 或隐式 fallback 绕过类型和状态机边界。
