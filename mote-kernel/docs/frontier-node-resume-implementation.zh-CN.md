# Frontier 节点统一恢复实施方案

## 1. 文档信息

- 状态：已实施、已验证并完成代码审核闭环
- 需求基线：`docs/frontier-node-recovery-requirements.zh-CN.md`
- 被替代基线：`docs/frontier-node-resume-requirements.zh-CN.md`
- 实施范围：Mote Kernel Python `state.graph_state` 与唯一 `execution` graph engine
- 交付方式：一次 coordinated model replacement；最终合入版本只保留一套 authoritative path

本文整体替换旧版 Frontier failure-resume 实施方案。旧方案中的全量 `ResumeGraphFrontier`、顶层 GraphRun interrupt、`SUSPENDED` lifecycle、
`BLOCKED` Frontier 和共享 effective input 均不再成立，不得在编码时以兼容分支继续保留。

最终实现必须满足：

1. Frontier node settlement 是 failure、interrupt、skip 和 Pending input binding 的唯一状态事实；
2. `ResumeGraphNodes` 是 failure 与 interrupt 的唯一恢复 command；
3. `execution` 仍是唯一 graph engine，原位复用 revision、batch lease、token fence、resource、routing、join、nested 和 codec 基础设施；
4. GraphRun 顶层 operator pause 与旧 interrupt path 完整删除；
5. 不增加 store、journal、lease history、interrupt history、resume-value tape、第二 runner、alias 或 fallback；
6. State transition 保持纯函数，compiled topology 与泛型输入编解码继续由 execution owner 负责。

## 2. 已关闭的实施决策

| 议题 | 唯一实施决定 |
| --- | --- |
| Frontier | `GraphFrontierState` 保存完整 superstep activation settlement |
| Node settlement | `Pending / Succeeded / Failed / Interrupted / Skipped` 严格 union |
| Frontier status | 从 settlement 派生，不保存字段 |
| Mixed Frontier | `Pending` 可与 `Failed`、`Interrupted` 共存；存在 Pending 即可执行 |
| GraphRun lifecycle | 只保留 `RUNNING / COMPLETED / ABORTED` |
| Resume | 唯一 `ResumeGraphNodes`，支持选择性 failure resume、Failed-only skip 和 interrupt resume |
| Settlement | 唯一 `SettleGraphExecution` 合并本批全部 typed outcomes |
| Interrupt identity | 从 `(run_id, superstep, node_id, execution generation)` 由 state-owned 纯函数派生 |
| Interrupt 历史 | 只保存当前 `Interrupted` identity；resume 后消费，不保存 history/counter/index/tape |
| Input override | Pending node 保存默认输入或 activation-scoped opaque override；完整替换，不做 patch/merge |
| Codec | 原位泛化现有 resolution codec，形成唯一 deterministic、side-effect-free encoder/decoder binding |
| Lease identity | 单一 batch lease 保存 `node_ids`；task ID 只在 execution 投影派生 |
| Claim 范围 | 精确覆盖当前全部 Pending nodes，不允许 partial claim |
| Skip | 只接受 Failed；保存原 failure、reason 和合法 routing contribution |
| Nested wait | `MissingChild -> ActiveChild wait -> resource admission -> full Pending claim` |
| Operator pause | 明确删除，不保留 GraphRun `SUSPENDED` 或顶层 interrupt transition |
| Persistence | 不新增 store、journal、通用 input binding、output persistence 或跨进程承诺 |

本期唯一状态应用闭包为：

```text
execution service
    -> state-owned GraphRunCommand
    -> reduce_graph_run(previous GraphRunState, command)
    -> next GraphRunState
```

Execution 只投影 command 和 transient typed results，不调用 reducer、不替换 snapshot。调用方把 reducer 结果作为后续 `StepRequest.state`；本文不为这
一步新增 proposal DTO、store/loading port、事务接口或公共 composition entry point。

## 3. 目标运行模型

### 3.1 唯一状态树

```text
GraphRunState
├── run_id / definition_id / definition_version
├── status: RUNNING | COMPLETED | ABORTED
├── superstep
├── execution_sequence
├── resume_input_codec: GraphResumeInputCodec | None
├── frontier: GraphFrontierState
│   └── nodes: GraphFrontierNode[]
│       ├── node_id
│       └── settlement
│           ├── Pending(input_binding)
│           ├── Succeeded(routing)
│           ├── Failed(failure)
│           ├── Interrupted(identity, request_payload)
│           └── Skipped(failure, reason, routing)
├── join_progress
├── resources
├── execution: GraphExecutionLease(token, node_ids) | None
├── abort: GraphAbort | None
├── parent: ParentGraphActivation | None
└── revision
```

Frontier status 按固定顺序派生：

```text
at least one Pending                          -> EXECUTABLE
no Pending, any Failed or Interrupted         -> AWAITING_RESUME
all Succeeded or Skipped                      -> SETTLED
otherwise                                     -> invalid
```

`SETTLED` 只允许作为 reducer 内部瞬时判断。RUNNING snapshot 不得长期保存全部 `Succeeded/Skipped` 的 Frontier。

### 3.2 选择性恢复状态流

```text
A: Succeeded, B: Failed, C: Failed
        │ ResumeGraphNodes(ResumeFailed(B))
        ▼
A: Succeeded, B: Pending, C: Failed          EXECUTABLE
        │ Claim(B) / Settle(B success)
        ▼
A: Succeeded, B: Succeeded, C: Failed        AWAITING_RESUME
        │ ResumeGraphNodes(SkipFailed(C), resolution)
        ▼
next Pending Frontier | COMPLETED
```

### 3.3 Node interrupt 状态流

```text
Pending
  │ NodeInterrupt(request)
  │ identity := derive(run, superstep, node, active generation)
  ▼
Interrupted(identity, request)
  │ ResumeInterrupted(node, interrupt_id, override)
  ▼
Pending(OverrideInput(payload))
  │ new claim generation
  ├─ NodeSuccess -> Succeeded
  ├─ NodeFailure -> Failed
  └─ NodeInterrupt -> Interrupted(new identity)
```

任一稳定 snapshot 中每个 activation 最多一个 outstanding interrupt。新 attempt 可以再次产生 interrupt，但旧 identity 已被消费；不保存调用
序号、已回答列表或 resume-value tape。

### 3.4 异常状态流

```text
active batch lease
  │ Python exception / contract error / codec error after claim / infrastructure error
  ▼
no settlement; exact lease and resources retained
  │ external stop + FenceGraphExecution(exact token)
  ▼
RUNNING snapshot, no lease/resources, original Pending bindings unchanged
```

只有 typed `NodeFailure` 和 typed `NodeInterrupt` 能成为 node settlement。其他异常不得伪装成可恢复 outcome。

## 4. State-owned 类型设计

### 4.1 基础 identity 唯一 owner

`state/graph_state/identity.py` 是 graph state、静态 topology 和 execution 共用的基础 identity 唯一 owner，逐项定义：

```python
GraphRunId = NewType("GraphRunId", str)
GraphDefinitionId = NewType("GraphDefinitionId", str)
GraphDefinitionVersion = NewType("GraphDefinitionVersion", int)
GraphNodeId = NewType("GraphNodeId", str)
GraphRouteId = NewType("GraphRouteId", str)
GraphExecutionAttemptId = NewType("GraphExecutionAttemptId", str)
GraphInterruptId = NewType("GraphInterruptId", str)
```

所有 owner 直接导入这些类型，不建立 execution-local alias、同底层类型的第二个 `NewType` 或仅用于显式转换的 wrapper：

- `GraphDefinition`、`CompiledGraph` 直接使用 `GraphDefinitionId`、`GraphDefinitionVersion`；
- node definitions、edges、tasks、Frontier、lease 与 resources 直接使用 `GraphNodeId`；
- conditional edges 与 routing contribution 直接使用 `GraphRouteId`；
- `GraphExecutionToken.attempt_id`、claim command、lease projection 和 one-shot claim capability 对同一次 durable claim 使用同一个
  `GraphExecutionAttemptId`；
- `StepRequest.request_attempt_id` 若继续用于绑定 prepare/execute 调用，则使用 execution-owned `ExecutionRequestAttemptId`，明确只是 one-shot request
  correlation，不进入 `GraphRunState` 或 `GraphExecutionToken`；两者语义不同，不得互相转换或都命名为 execution attempt；
- `GraphInterruptId` 只作为 structured interrupt identity 的 deterministic comparison projection；
- execution-local task ID 仍可作为 `(run, superstep, node)` 的派生调度键，但不进入 state identity 模块，不成为 lease、resource、parent 或 child-run
  identity。

由这些 scalar identities 组成的 state value object 继续放在其语义 owner：`GraphNodeInterruptIdentity` 属于 `frontier_model.py`，
`GraphExecutionToken` 与 `ParentGraphActivation` 属于 `model.py`；execution 不重复定义。

同一模块私有实现唯一长度前缀字段编码，并公开两个 coordinate projection functions：

```python
def _identity_field(value: str) -> str: ...


def graph_interrupt_id(
    run_id: GraphRunId,
    superstep: int,
    node_id: GraphNodeId,
    execution_generation: int,
) -> GraphInterruptId: ...


def child_graph_run_id(
    parent_run_id: GraphRunId,
    parent_superstep: int,
    parent_node_id: GraphNodeId,
) -> GraphRunId: ...
```

`_identity_field(value) := decimal(len(value)) + ":" + value`，其中 `len` 是 Python `str` code point count。两个公开函数共同调用这一实现；其他模块
不得复制该编码。函数只接收 state-owned scalar coordinates，不导入 `model.py` 或 `frontier_model.py`，从而保持 `identity.py` 为依赖叶节点。

### 4.2 Frontier、input binding 与 settlement

在 `state/graph_state/frontier_model.py` 定义：

```python
GraphFailure = NewType("GraphFailure", str)
GraphInterruptPayload = NewType("GraphInterruptPayload", bytes)
GraphResumeInputPayload = NewType("GraphResumeInputPayload", bytes)
GraphResumeInputCodecId = NewType("GraphResumeInputCodecId", str)
GraphSkipReason = NewType("GraphSkipReason", str)


@dataclass(frozen=True, slots=True)
class GraphResumeInputCodec:
    codec_id: GraphResumeInputCodecId
    version: int


@dataclass(frozen=True, slots=True)
class UseStepRequestInput:
    pass


@dataclass(frozen=True, slots=True)
class OverrideGraphNodeInput:
    payload: GraphResumeInputPayload


GraphNodeInputBinding: TypeAlias = UseStepRequestInput | OverrideGraphNodeInput


@dataclass(frozen=True, slots=True)
class PendingGraphNode:
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SucceededGraphNode:
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNode:
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class GraphNodeInterruptIdentity:
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId
    execution_generation: int


@dataclass(frozen=True, slots=True)
class GraphNodeInterrupt:
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class InterruptedGraphNode:
    interrupt: GraphNodeInterrupt


@dataclass(frozen=True, slots=True)
class SkippedGraphNode:
    failure: GraphFailure
    reason: GraphSkipReason
    routing: GraphRoutingContribution


GraphNodeSettlement: TypeAlias = (
    PendingGraphNode
    | SucceededGraphNode
    | FailedGraphNode
    | InterruptedGraphNode
    | SkippedGraphNode
)


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement


@dataclass(frozen=True, slots=True)
class GraphFrontierState:
    nodes: tuple[GraphFrontierNode, ...]
```

该模块提供纯查询，不保存 cache：

- `frontier_status`
- `frontier_node`
- `pending_node_ids`
- `failed_node_ids`
- `interrupted_node_ids`
- `skipped_node_ids`
- `routing_contributions`

所有 node ID 返回值使用 canonical order。

`frontier_model.py` 只依赖 `identity.py` 与 `routing.py`。`GraphFailure`、`GraphInterruptPayload`、codec/input binding、interrupt value objects、五种
settlement、Frontier 组合与派生查询均由该模块唯一拥有；它不导入 `model.py`、`command.py`、execution 或 compiled topology。

### 4.3 Routing contribution

`state/graph_state/routing.py` 定义 routing identity 之外的全部 state-owned routing value objects：

```python
@dataclass(frozen=True, slots=True)
class ContinueGraphRouting:
    pass


@dataclass(frozen=True, slots=True)
class SelectGraphRoute:
    route: GraphRouteId


GraphRoutingContribution: TypeAlias = ContinueGraphRouting | SelectGraphRoute
```

Owner 与使用规则固定为：

1. `ConditionalEdge.route` 直接使用 state-owned `GraphRouteId`；`DirectEdge`、`JoinEdge` 直接使用 `GraphNodeId`；
2. `NodeSuccess.routing` 直接使用 `GraphRoutingContribution`，默认值为 `ContinueGraphRouting()`；
3. `SkippedGraphNode.routing`、`SucceededGraphNode.routing` 和对应 attempt outcomes 使用同一 union，不做 projection wrapper；
4. 删除 `execution/graph/command.py`；不得在 execution 继续定义 `Continue`、`SelectRoute`、`RoutingCommand` 或 re-export alias；
5. `execution/graph/edge.py` 只定义 topology edge 组合，直接导入 `GraphNodeId` 和 `GraphRouteId`；
6. State 只验证 identity/value-object 结构，不解释 route 是否属于 compiled topology；具体合法性仍由
   `execution.engine.routing.validate_routing_contribution` 唯一校验。

`Succeeded` 与 `Skipped` 提供 contribution；`Pending`、`Failed`、`Interrupted` 不提供。这样 state 可以保存 routing 事实而不反向依赖 execution，
execution 也不会形成第二套 routing DTO。

### 4.4 Interrupt identity

在 state owner 中定义且只定义一次：

```python
def derive_graph_node_interrupt_identity(
    run_id: GraphRunId,
    superstep: int,
    node_id: GraphNodeId,
    execution_generation: int,
) -> GraphNodeInterruptIdentity: ...
```

Identity 保存结构化坐标；`GraphInterruptId` 是 deterministic internal projection，不作为第二份状态字段保存。比较 ID 时调用第 4.1 节的
`graph_interrupt_id(identity.run_id, identity.superstep, identity.node_id, identity.execution_generation)`；唯一算法为：

```text
GraphInterruptId :=
    field("mote.graph-node-interrupt.v1")
    + field(run_id)
    + field(decimal(superstep))
    + field(node_id)
    + field(decimal(execution_generation))
```

约束：

- 所有长度使用 Python `str` code point count，且只对紧随其后的字段文本计数；
- superstep 和 generation 使用无前导零的 canonical non-negative decimal；generation 仍必须为正；
- 算法支持 GraphState 已接受的无上限 Python int，不另加 u64 范围；
- `run_id` 与 `node_id` 原样参与投影，不做 Unicode normalization、case folding 或 locale-dependent 转换；
- version/domain 字段与字段顺序属于 v1 规范，不允许静默改变；未来改变必须新增 version 字段值；
- Execution、API projection 和 reducer 只能调用 state-owned `graph_interrupt_id`，不得复制、拼接或 hash 另一份 ID；调用前只允许从结构化 identity
  读取四个坐标，不允许重算或替换其中字段。

每个字段自描述长度，因此字段内容包含冒号、数字、Unicode 或与其他坐标具有相同前缀时仍无分隔符歧义。该算法是 Kernel 内部 identity
projection，不声明跨语言 wire protocol。

### 4.5 Attempt outcomes 与 resolution

Attempt outcomes、Frontier resolution、resume actions 和 graph commands 全部定义在 `state/graph_state/command.py`。它们是 pure reducer 的 typed
transition input，不是稳定 Frontier snapshot value object。`command.py` 可以单向依赖 `identity.py`、`routing.py`、`frontier_model.py` 与
`model.py`；上述模块均不得反向导入 `command.py`。

```python
@dataclass(frozen=True, slots=True)
class SucceededGraphNodeOutcome:
    node_id: GraphNodeId
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNodeOutcome:
    node_id: GraphNodeId
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class InterruptedGraphNodeOutcome:
    node_id: GraphNodeId
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


GraphNodeOutcome: TypeAlias = (
    SucceededGraphNodeOutcome
    | FailedGraphNodeOutcome
    | InterruptedGraphNodeOutcome
)
```

Routing resolution 保留：

```python
@dataclass(frozen=True, slots=True)
class AdvanceGraphFrontier:
    node_ids: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...]


@dataclass(frozen=True, slots=True)
class CompleteGraphFrontier:
    pass


GraphFrontierResolution: TypeAlias = AdvanceGraphFrontier | CompleteGraphFrontier
```

Attempt outcomes 精确覆盖当前 lease。它们不保存泛型 output，也不是 attempt history。

`AdvanceGraphFrontier.join_progress` 直接使用 `model.py` 的 `GraphJoinProgress`。因此 `GraphNodeOutcome` 与 `GraphFrontierResolution` 不放入
`frontier_model.py`：否则 Frontier snapshot owner 会为了 transition payload 反向依赖 run model，形成循环或迫使复制 join DTO。

`TaskSuccess.output` 是本次 execution call 的 transient result，不写入 GraphState，也不参与恢复事实。本期只规定 pure reducer 与 execution
projection；不定义 AgentState proposal、store、commit/loading port、内存 handoff 或默认 composition entry point。项目级持久化原则保持成立，但不是
本文的实施对象。

### 4.6 Run、lease、abort 与 parent

`state/graph_state/model.py` 最终模型：

```python
class GraphRunStatus(Enum):
    RUNNING = auto()
    COMPLETED = auto()
    ABORTED = auto()


GraphAbortReason = NewType("GraphAbortReason", str)


@dataclass(frozen=True, slots=True)
class GraphAbort:
    reason: GraphAbortReason


@dataclass(frozen=True, slots=True)
class GraphExecutionToken:
    generation: int
    attempt_id: GraphExecutionAttemptId


@dataclass(frozen=True, slots=True)
class GraphExecutionLease:
    token: GraphExecutionToken
    node_ids: tuple[GraphNodeId, ...]


@dataclass(frozen=True, slots=True)
class ParentGraphActivation:
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId


@dataclass(frozen=True, slots=True)
class GraphJoinProgress:
    sources: tuple[GraphNodeId, ...]
    target: GraphNodeId
    arrived: frozenset[GraphNodeId]
```

`model.py` 唯一定义 run lifecycle、`GraphExecutionToken`、batch lease、join progress、abort、parent activation 与 `GraphRunState`。它单向依赖
`identity.py`、`routing.py`、`frontier_model.py` 和 resource state，不导入 `command.py`；`GraphRunState.frontier` 直接使用
`GraphFrontierState`。

`GraphRunState`：

- `frontier` 使用 `GraphFrontierState`；
- 增加单调 `execution_sequence`，Start 为 0，每次成功 claim 增加 1；
- 保存 `resume_input_codec` identity/version，不保存 encoder/decoder；
- `execution` 保存 exact token 与 `node_ids`；
- `abort` 只属于 ABORTED；
- `parent` 保存 activation coordinates；
- 删除顶层 `failure`、`interrupt`、`resolution_codec`、`GraphTaskId` 和 `ParentGraphTask`。

完成态使用唯一 empty Frontier。ABORTED 保留非空 Frontier 作为诊断位置，但不可 prepare、decode、resume 或 routing。

## 5. Command 与 reducer

### 5.1 最终 command 集合

```text
StartGraphRun
ClaimGraphExecution
FenceGraphExecution
SettleGraphExecution
ResumeGraphNodes
AbortGraphRun
UpdateGraphResources
```

删除：

```text
ResumeGraphFrontier
AdvanceGraphRun
CompleteGraphRun
FailGraphExecution
RequestGraphRunInterrupt
ResolveGraphRunInterrupt
```

核心 command：

```python
@dataclass(frozen=True, slots=True)
class SettleGraphExecution:
    expected_revision: int
    execution: GraphExecutionToken
    outcomes: tuple[GraphNodeOutcome, ...]
    resolution: GraphFrontierResolution | None


@dataclass(frozen=True, slots=True)
class ResumeGraphNodes:
    expected_revision: int
    actions: tuple[GraphNodeResumeAction, ...]
    resolution: GraphFrontierResolution | None
```

Reducer dispatch 对最终 command union 穷尽处理，禁止 catch-all 把未知 command 解释为 abort。

### 5.2 Start

`StartGraphRun`：

1. 校验 entry node IDs 非空、唯一、canonical；
2. 每个 entry 创建 `PendingGraphNode(UseStepRequestInput())`；
3. 初始化 `RUNNING / superstep=0 / execution_sequence=0 / revision=0`；
4. 固定 graph definition 对应的 resume input codec identity/version；
5. 无 execution/resources/abort；
6. `parent is not None` 时，`run_id` 必须精确等于 `child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)`。

`GraphExecutor.start_command()` 只启动当前 composition 的 root graph，因此不接收 parent。Nested child start 只由 pending nested activation 的
execution projection 产生 deterministic run ID。单独构造 `GraphExecutor(child_graph)` 时，该 definition 是新 composition 的 root，仍可使用
`parent=None` 独立运行；不得把 root/child 角色固化为 graph definition 的全局属性。

### 5.3 Claim

`ClaimGraphExecution` 携带 canonical `node_ids` 和 attempt ID。Reducer 必须验证：

1. revision 匹配、RUNNING、Frontier 为 EXECUTABLE；
2. 无 active lease；
3. `node_ids` 精确等于当前全部 Pending node IDs；
4. Failed、Interrupted、Succeeded、Skipped 不进入 lease；
5. resource admission 若存在，其 participants 均属于 Pending nodes，且 snapshot 满足 state-owned 结构与生命周期不变量；
6. `execution_sequence += 1`；
7. token generation 等于新的 execution sequence；
8. 保存 `GraphExecutionLease(token, node_ids)`；
9. revision 增加一次。

Claim reducer 不解释 compiled resource requirements，不要求每个 Pending 都有 acquisition，也不要求所有 acquisition 已 admitted。Execution guard
必须在生成 claim 前证明 participants 精确等于当前需要资源的 executable Pending subset；无资源 Pending 与 nested Pending 不创建虚假
acquisition。Waiting acquisition 由既有 resource waves 在同一 batch lease 下依次推进。Claim 不生成 task ID，也不解码 input override。

### 5.4 SettleGraphExecution

Reducer 固定顺序：

1. 校验 revision、RUNNING 和 exact execution token；
2. outcomes 非空、唯一、canonical，精确覆盖 lease node IDs；
3. 每个 outcome 对应当前 Pending node；
4. 校验 state-owned routing/failure/payload value objects；
5. 对每个 interrupt outcome，从当前 run/superstep/node/active token generation 重新派生 identity并精确比对；
6. 任一 interrupt outcome 存在时要求 `resume_input_codec` 非 None；
7. 原子将 outcomes 合并到完整 Frontier；
8. 原子清 execution/resources；
9. 根据合并后的派生状态选择唯一分支；
10. revision 增加一次。

`InterruptedGraphNodeOutcome.identity` 与 exact lease 的历史来源证明只发生在第 5 步。稳定 snapshot validator 不重复证明。

非 settled 分支：

- 合并后不得残留 Pending，因为 lease 已精确覆盖全部 Pending；
- 存在 Failed/Interrupted 时 `resolution` 必须为 `None`；
- 保持 RUNNING、superstep、node set 和 prior join progress；
- 保存所有 success、failure 和 interrupt outcomes。

Settled 分支：

- 全部为 Succeeded/Skipped；
- `resolution` 必须非 None；
- `AdvanceGraphFrontier` 创建下一 superstep 的全新 `Pending(UseStepRequestInput)` Frontier；
- `CompleteGraphFrontier` 转为 COMPLETED 和 canonical empty Frontier；
- 新 superstep 不复制 settlement、interrupt identity 或 input override。

### 5.5 ResumeGraphNodes

State-owned action：

```python
@dataclass(frozen=True, slots=True)
class ResumeFailedNode:
    node_id: GraphNodeId
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SkipFailedNode:
    node_id: GraphNodeId
    reason: GraphSkipReason
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNode:
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideGraphNodeInput


GraphNodeResumeAction: TypeAlias = (
    ResumeFailedNode | SkipFailedNode | ResumeInterruptedNode
)
```

转换：

```text
Failed      + ResumeFailed(input)       -> Pending(input)
Failed      + SkipFailed(reason, route) -> Skipped(failure, reason, route)
Interrupted + ResumeInterrupted(id,input)-> Pending(input)
```

Reducer 验证：

1. revision 匹配、RUNNING、无 lease/resources；
2. actions 非空、node ID 唯一、canonical；
3. action node 属于 Frontier且 settlement 类型精确匹配；
4. skip 只接受 Failed；
5. interrupt resume ID 等于当前 identity 的 `graph_interrupt_id` 投影；
6. Failed resume input 可为默认或 override；interrupt resume 必须为 override；
7. 未选节点及已有 success/skip 原样保留；
8. 整组 actions 原子应用；
9. revision 增加一次。

应用后仍有 Pending/Failed/Interrupted 时，`resolution` 必须为 None，保持当前 superstep。应用后全部 Succeeded/Skipped 时，必须在同一命令中按
execution 计算的 resolution advance/complete，不提交长期 SETTLED snapshot。

Reducer 不自行校验 conditional route 拓扑；execution resume service 必须在生成 command 前调用唯一 routing validator 并计算最终 resolution。

### 5.6 Fence、resource 与 abort

`FenceGraphExecution` 使用 exact token，并原子执行：

```text
execution = None
resources = None
frontier settlement/input bindings unchanged
superstep/join/execution_sequence unchanged
revision += 1
```

调用方必须先完成外部停止。Stale token 不得清除新 lease。

Resource participant 使用当前 Frontier 的 `node_id`。Resource snapshot 只允许存在于至少含一个 Pending node 的 `EXECUTABLE` Frontier，admission
participant 必须属于当前 Pending subset。正常 settlement 或 exact fence 清除本次 admission；未 claim 的 admission 可由 abort 清理。Failed-only、
Interrupted-only 或其他无 Pending Frontier 不得提交空 acquisition snapshot 来阻断 resume。

`AbortGraphRun`：

- 拒绝 COMPLETED/ABORTED 和 active lease；
- 接受 quiescent RUNNING；
- 原子清 no-lease resource admission；
- 保留 Frontier 与 join progress 作为诊断事实；
- 写入 `GraphAbort`，转为 ABORTED；
- ABORTED 不再 decode override、project interrupt、planning、claim、resume 或 routing。

## 6. Stable snapshot validation

`state/graph_state/validation.py` 只验证当前 snapshot 可证明的事实：

1. lifecycle、empty/non-empty Frontier、abort、lease/resources 组合合法；
2. node IDs 唯一、canonical，settlement/value object 结构合法；
3. RUNNING 不保存长期 SETTLED Frontier；
4. active lease 精确等于全部 Pending nodes；
5. Interrupted identity 的 run/superstep/node 匹配当前 activation；
6. interrupt generation 为正且 `<= execution_sequence`；
7. 当前 Frontier 的 interrupt identities 及其派生 IDs 唯一；
8. override 或 Interrupted settlement 存在时 state codec identity/version 存在；
9. `parent is not None` 时，run ID 精确等于 parent coordinates 的 `child_graph_run_id`；
10. resource snapshot 只存在于至少含一个 Pending node 的 Frontier；
11. 不存在顶层 interrupt、SUSPENDED lifecycle 或已消费 identity history。

`generation <= execution_sequence` 只证明 identity 没有引用未来 generation。Validator 不重新证明 historical exact lease 来源，也不得为此增加
lease history、interrupt history、attempt journal 或隐藏 cache。

## 7. Execution-owned resume input codec

### 7.1 唯一 binding

将 `execution/graph/resolution.py` 原位替换为 `execution/graph/resume_input.py`：

```python
class ResumeInputEncoder(Protocol[InputT_contra]):
    def encode(self, value: InputT_contra) -> bytes: ...


class ResumeInputDecoder(Protocol[InputT_co]):
    def decode(self, payload: bytes) -> InputT_co: ...


@dataclass(frozen=True, slots=True)
class ResumeInputBinding(Generic[InputT]):
    codec_id: GraphResumeInputCodecId
    version: int
    encoder: ResumeInputEncoder[InputT]
    decoder: ResumeInputDecoder[InputT]
```

`GraphDefinition.resume_input` 和 `CompiledGraph.resume_input` 保存唯一 binding。Start projection 只把 identity/version 写入 GraphState。
没有 binding 的 graph 在 state 中保存 `None`，仍可执行默认 input 和 typed failure resume，但必须拒绝 override 与 typed node interrupt。

唯一合法流程为：

```text
typed resume request value
    -> encoder.encode(value)

opaque state payload
    -> decoder.decode(payload) -> InputT
```

输入结构和值合法性属于唯一 codec contract：encoder 必须拒绝不能由该 codec 表示的 value，decoder 必须拒绝 malformed payload、错误结构或不合法
值，只有成功返回时才产生 `InputT`。不得再增加全局 validator owner，也不得在 scheduler 或单个 node 中复制 codec shape rules。

Encoder/decoder 必须 deterministic、side-effect-free，不得读取隐藏可变状态、执行业务 IO、产生副作用或承担外部协调。这是 provider contract；
通用测试无法证明任意 callback 纯净。实现只对 Kernel 内建 binding 提供固定 encode/decode/round-trip/error vectors，外部 provider 通过 assembly
contract 承担纯度。

### 7.2 Typed resume request

Execution API 定义 typed request，避免调用方直接构造 bytes：

```python
@dataclass(frozen=True, slots=True)
class UseRequestInput:
    pass


@dataclass(frozen=True, slots=True)
class OverrideNodeInput(Generic[InputT]):
    value: InputT


@dataclass(frozen=True, slots=True)
class ResumeFailedNodeRequest(Generic[InputT]):
    node_id: GraphNodeId
    input: UseRequestInput | OverrideNodeInput[InputT]


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNodeRequest(Generic[InputT]):
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideNodeInput[InputT]


@dataclass(frozen=True, slots=True)
class SkipFailedNodeRequest:
    node_id: GraphNodeId
    reason: GraphSkipReason
    routing: GraphRoutingContribution


ResumeNodeRequest: TypeAlias = (
    ResumeFailedNodeRequest[InputT]
    | ResumeInterruptedNodeRequest[InputT]
    | SkipFailedNodeRequest
)


@dataclass(frozen=True, slots=True)
class ResumeRequest(Generic[InputT]):
    state: GraphRunState
    actions: tuple[ResumeNodeRequest[InputT], ...]
```

`GraphExecutor.resume(request) -> ResumeGraphNodes`：

1. 选择并 guard compiled graph；
2. 验证 typed actions 与 current settlement；
3. 用唯一 encoder 编码 override；encoder 同时执行其 codec contract 校验；
4. 校验 skip contribution；
5. 模拟 action merge；
6. 若全部 settled，调用唯一 routing engine 计算 resolution；
7. 投影 state-owned command，不修改 state。

### 7.3 Effective input

将 `execution/engine/resolution_input.py` 替换为 `resume_input.py`。它按 task/node 物化：

```text
Pending(UseStepRequestInput)       -> StepRequest.node_input
Pending(OverrideGraphNodeInput)    -> resume_input.decoder.decode(payload)
```

同一 batch 可以包含默认 input 和多个不同 override。Scheduler API 改为接收 task 与其 effective input 的严格结构，不再广播单个已计算输入。

错误阶段：

- encode 失败：不生成 command，无状态提交、无 lease；
- guard codec mismatch：不 prepare/claim；
- claim 前 override decode 失败：不生成 claim；
- claim command 经 pure reducer 应用后，对 resulting snapshot 重新 guard/物化失败：不 settlement，保留 lease，外部停止后 fence；
- 所有 codec error 均不得转成 node failure/interrupt。

## 8. Execution projection 与执行管线

### 8.1 Execution 直接读取 authoritative GraphRunState

`StepRequest`、`GraphExecutor.prepare/execute/resume` 和唯一 snapshot guard 直接只读消费 `GraphRunState`。旧 `execution/snapshot.py`、
`ExecutionSnapshot` 与 `project_execution_snapshot()` 删除：identity、Frontier、lease、parent、join、resource、codec 和 revision 已全部使用
state-owned immutable value objects，逐字段镜像不再提供转换、组合或隔离价值。

删除 execution-local `ExecutionStatus`、`InterruptRecord`、`InterruptLifecycle`、`ExecutionLeaseSnapshot`、`ParentTaskRef`、`JoinProgress` 及其
identity aliases。Execution 不复制 state reducer、保存 mutable cache，也不建立可独立恢复或独立校验的第二份 snapshot model。

DTO ownership 固定为：

1. `GraphNodeId`、`GraphFailure`、`GraphNodeInputBinding`、`GraphRoutingContribution`、`GraphNodeInterrupt`、`GraphSkipReason`、
   `ParentGraphActivation` 等 immutable state value object 由 execution 直接复用；
2. 不得仅为换名创建 `FailureSnapshot`、`ParentActivationRef`、`RoutingContributionSnapshot` 等一一对应 wrapper、alias 或复制类型；
3. Execution 只在组合形状确实不同或携带 execution-only 派生信息时定义 projection DTO；不得为五种 settlement 再建立一组平行 node snapshot；
4. 每个确有必要的 execution-only DTO 必须在实际消费边界明确拥有者，并由 total、side-effect-free projection 从 `GraphRunState` 派生；
5. Projection 不得重新分配 identity、引入默认值、归一化字段、保存 mutable cache，或定义与 state validator 重叠的 lifecycle/structure validation；
6. `state.graph_state.validation` 仍是 state snapshot invariant 的唯一 owner；`snapshot_guard.py` 只验证 compiled topology、codec 与 execution
   consumption compatibility。

如果某个候选 execution DTO 删除后，调用方可以无损直接读取同一个 state value object，则必须删除该 DTO。只读 projection 是消费视图，不是第二套
identity、value object 或 validation model。

### 8.2 唯一 snapshot guard

扩展 `execution/engine/snapshot_guard.py`，但保持 terminal 与 RUNNING 两层责任：

1. definition ID/version；
2. Frontier node membership 与 canonical order；
3. lifecycle 投影与 terminal disposition；
4. 仅对 RUNNING snapshot 校验 parent activation 对应 compiled parent node；
5. 仅对 RUNNING snapshot 校验 join progress 对应 compiled joins；
6. 仅对 RUNNING snapshot 校验 state codec identity/version 与 compiled binding；
7. 仅对 RUNNING snapshot 校验每个 retained `Succeeded/Skipped` contribution 的 compiled topology 合法性；
8. 仅对 RUNNING snapshot 校验每个 Pending override 有可用 decoder。

`COMPLETED/ABORTED` 在 graph identity 与 state-owned snapshot validation 通过后立即返回 terminal disposition。尤其 ABORTED retained Frontier 仅是
诊断事实，不得 decode override、解释 contribution、重算 join、投递 nested child 或进入 planning。

Routing contribution 的窄规则只在 `execution/engine/routing.py` 定义：普通 node 只接受 continue，conditional node 必须选择已声明 route。
snapshot guard、resume service 和 attempt settlement 调用同一函数，不复制条件边规则。

### 8.3 Planner 与 prepare disposition

`execution/engine/planner.py` 只为 Pending settlement 派生 task：

```text
task_id := task_identity(run_id, superstep, node_id)
planned node IDs == pending_node_ids(frontier)
```

Task ID 是 execution-local deterministic projection；State、lease、resource participant 与 parent link 均不保存它作为 authoritative identity。

旧 `PreparedFrontier` 替换为穷尽 disposition：

```python
@dataclass(frozen=True, slots=True)
class StartMissingChildren(Generic[InputT, OutputT]):
    children: tuple[PreparedNestedRun[InputT, OutputT], ...]


@dataclass(frozen=True, slots=True)
class WaitForActiveChildren:
    children: tuple[ActiveChild, ...]


ChildWaitAction: TypeAlias = (
    StartMissingChildren[InputT, OutputT] | WaitForActiveChildren
)


@dataclass(frozen=True, slots=True)
class WaitingForChildren(Generic[InputT, OutputT]):
    action: ChildWaitAction[InputT, OutputT]


PrepareDisposition: TypeAlias = (
    ExecutableFrontier[InputT, OutputT]
    | WaitingForChildren[InputT, OutputT]
    | AwaitingResume
    | CompletedGraph
    | AbortedGraph
)
```

- `ExecutableFrontier` 携带 resource admission command 或 exact prepared claim，二者互斥；
- `WaitingForChildren.action` 是严格 union：`StartMissingChildren` 携带非空、canonical child start commands，
  `WaitForActiveChildren` 携带非空、canonical active child projections；两个分支不能同时出现；
- `AwaitingResume` 分别暴露 failed 与 interrupted node projections；
- terminal disposition 不携带 tasks、decoder、routing decision 或 resume action。

Prepare 固定顺序：

1. 直接读取 state 并运行唯一 snapshot guard；
2. `COMPLETED -> CompletedGraph`，`ABORTED -> AbortedGraph`；
3. 无 Pending -> `AwaitingResume`；
4. 对全部 Pending nested activation 分类；
5. 任一 `MissingChild` -> `WaitingForChildren(StartMissingChildren(...))`，包含全部确定性 child start commands；
6. 无 MissingChild、任一 `ActiveChild` -> `WaitingForChildren(WaitForActiveChildren(...))`；
7. 所有 child 均 terminal 后，先按 node 物化全部 Pending effective inputs；override decode 同时执行 codec contract 校验；
8. input 全部有效后才执行现有 resource admission；
9. admission 完成后为全部 Pending nodes 创建一个 batch claim。

该顺序意味着 ActiveChild 会阻塞同 Frontier 的普通 Pending sibling。不得通过 partial claim、临时删除 task 或第二 lease 模型绕过 barrier。

### 8.4 Claim projection 与 claim 后重建

`PreparedExecutionClaim` 继续作为 executor-owned one-shot capability，内容改为 exact token、canonical `node_ids`、派生 task IDs 和
`ExecutionRequestAttemptId` correlation。
`ClaimGraphExecution` 只提交 node IDs。

调用方将 `ClaimGraphExecution` 交给现有 pure reducer 后，再以 resulting `GraphRunState` 调用 `GraphExecutor.execute`。Execute 消费该 claim 时必须从
request 携带的 resulting state 重新：

1. 直接读取 reducer-applied authoritative state；
2. 运行同一 snapshot/topology/codec guard；
3. 验证新 `child_projections` 对 Pending nested activations 的显式、完整闭包；
4. 证明 claim token 和全部 Pending node IDs 精确一致；
5. 重新派生 tasks；
6. 按 node 解码并验证 effective inputs；
7. 执行 resource waves 或普通 scheduler；
8. collect、validate routing、select settlement。

prepare 阶段的 Python 对象不能代替 reducer 应用 claim 后的 resulting snapshot。这里不引入 store 或提交接口；claim 是否已生效完全由 request 中
`GraphRunState.execution` 与 one-shot capability 的 exact token/node IDs guard 证明。Claim 后任何 guard、decode、input contract 或
infrastructure error 均不生成 settlement。

### 8.5 Scheduler typed outcomes

Graph node contract 原位扩展为：

```python
NodeOutcome: TypeAlias = NodeSuccess[OutputT] | NodeFailure | NodeInterrupt
```

Execution result 对应为：

```text
TaskSuccess(output, routing)
TaskFailure(failure)
TaskInterrupt(request_payload)
```

`execute_tasks` 接收严格的 `ExecutableTask(task, effective_input)` tuple。每个节点获得自己的 immutable input，不再接收 batch-shared
`node_input`。

Scheduler 继续捕获同批 ordinary exceptions 以等待 sibling quiesce，但收集结束后必须重新抛出第一个 deterministic exception；不得把它转换为
`TaskFailure` 或 `TaskInterrupt`。Contract violation、cancellation、executor error 同理。

`NodeInterrupt` 还要求 compiled graph 配置唯一 resume input binding；否则 execution 在 outcome projection 时 fail closed，state reducer 也以
缺少 `resume_input_codec` 拒绝 settlement，不能形成无法恢复的 Interrupted 状态。

### 8.6 Collector、routing guard 与 settlement 选择

`CollectedResults` 保存完整、canonical、互斥集合：

```python
@dataclass(frozen=True, slots=True)
class CollectedResults(Generic[OutputT]):
    successes: tuple[TaskSuccess[OutputT], ...]
    failures: tuple[TaskFailure, ...]
    interrupts: tuple[TaskInterrupt, ...]
```

Collector 必须证明 results 精确覆盖 planned tasks，保留全部 typed outcomes，不再只留第一个 failure，也不因存在 failure/interrupt 丢弃
success output 或 routing contribution。

Settlement 的固定顺序为：

1. 重新 guard reducer-applied snapshot 与 exact lease；
2. collector 证明完整覆盖；
3. 对每个新 success 的 routing contribution 调用唯一 topology validator；
4. 将新 success contribution 与 retained Succeeded/Skipped contribution 合并；
5. 根据 active generation 为每个 TaskInterrupt 派生 state identity；
6. 投影完整 `GraphNodeOutcome` tuple；
7. 若合并后仍存在 Failed/Interrupted，生成 `SettleGraphExecution(..., resolution=None)`；
8. 仅当完整 Frontier 全为 Succeeded/Skipped 时，调用现有 routing/join engine，生成 advance/complete resolution；
9. 返回 `ExecutedFrontierAttempt(results, command)`。

第 3 步无条件先于第 7 步。因此 `success + typed failure/interrupt` 的混合批次中，非法 success routing 仍是 infrastructure error：整批不提交，
exact lease 保留。对 recovered `AWAITING_RESUME` Frontier，prepare 在返回 disposition 前也由 snapshot guard 校验 retained contribution，不能被
无 Pending 分支短路。

`ExecutedSuperstep` 删除并替换为 `ExecutedFrontierAttempt`，因为一次 attempt 可以只执行 mixed Frontier 中的 Pending subset，并不必然推进
superstep。旧名称不保留 alias。

```python
@dataclass(frozen=True, slots=True)
class ExecutedFrontierAttempt(Generic[OutputT]):
    results: tuple[TaskResult[OutputT], ...]
    command: GraphRunCommand
```

它不暴露“已持久提交”语义。`command` 仍由当前 GraphRunState pure reducer 应用；`results` 可用于本次调用的 transient 返回或观测，但不成为
GraphState 恢复事实。真实 store、AgentState transaction 和 commit-confirmed memory handoff 均不属于本期实现。

### 8.7 Exception 与 fence 边界

执行边界只有两类合法结果：

```text
全部 task 返回 typed outcome
    -> SettleGraphExecution 经 pure reducer 原子应用，清 lease/resources

任一 ordinary exception / contract / routing / codec / infrastructure error
    -> 不生成 command，不提交任何 node outcome
    -> 保留 exact lease/resources/input bindings
    -> 外部停止完成后 FenceGraphExecution(exact token)
```

Fence 同时清除本 attempt 的 execution lease 和 resource admission，解决 resource claim 异常后的状态闭包。它不改变 settlement、override、join、
superstep 或 generation；stale token 对新 attempt 无效。

## 9. Nested、resource、routing、join 与循环

### 9.1 Nested activation

`ParentGraphTask` 替换为 `ParentGraphActivation(run_id, superstep, node_id)`。Child run ID 不再经 task ID 间接派生，而由
`state/graph_state/identity.py` 的唯一纯函数稳定派生：

```python
def child_graph_run_id(
    parent_run_id: GraphRunId,
    parent_superstep: int,
    parent_node_id: GraphNodeId,
) -> GraphRunId: ...
```

调用方从 `ParentGraphActivation` 原样传入三个字段；`identity.py` 不导入该结构化 model，避免 identity owner 反向依赖模型。

算法调用第 4.1 节的唯一 `field` 实现：

```text
GraphRunId :=
    field("mote.child-graph-run.v1")
    + field(parent.run_id)
    + field(decimal(parent.superstep))
    + field(parent.node_id)
```

约束：

- `superstep` 使用无前导零的 canonical non-negative decimal，支持 GraphState 接受的无上限 Python int；
- run ID 与 node ID 原样参与投影，不做 Unicode normalization、case folding 或 locale-dependent 转换；
- domain/version、字段顺序和长度语义属于 v1 规范；未来改变必须使用新的 domain/version，不得静默替换；
- 输出直接构造 `GraphRunId`，不再拼入 task ID、definition identity、随机数或 execution generation；
- parent activation 在不同 superstep 的同一静态 nested node 派生不同 child run；同一 activation 的 failure/interrupt resume 始终派生同一个 child run；
- 任意 parent-bearing stable/recovered state 都必须满足 `state.run_id == child_graph_run_id(parent coordinates)`；
- child start projection、`MissingChild` 构造、`Active/Completed/AbortedChild` guard 和 claim 后重建只能调用 `child_graph_run_id`，不得复制字符串拼接；
- 当前 `GraphExecutor(root)` 的 root definition key 只接受 `parent=None`，其 family child keys 只接受 parent-bearing state；同一个 child definition 在独立
  `GraphExecutor(child)` composition 中仍可作为 root。

固定 vectors 必须覆盖空白以外的合法最小 identity、冒号/数字、Unicode、多 code point 字符、相似前缀、不同 superstep，以及循环中同一 node 的不同
activation，并证明同一输入稳定、不同坐标无歧义。该算法是 Kernel 内部 durable identity projection，不声明跨语言 wire protocol。

至少固定以下 exact vectors（组合/分解 Unicode 不归一化）：

| Parent activation `(run_id, superstep, node_id)` | `child_graph_run_id` |
| --- | --- |
| `("r", 0, "n")` | `23:mote.child-graph-run.v11:r1:01:n` |
| `("a:b", 12, "节点")` | `23:mote.child-graph-run.v13:a:b2:122:节点` |
| `("ab", 3, "c")` | `23:mote.child-graph-run.v12:ab1:31:c` |
| `("a", 3, "bc")` | `23:mote.child-graph-run.v11:a1:32:bc` |
| `("loop", 4, "self")` | `23:mote.child-graph-run.v14:loop1:44:self` |
| `("loop", 5, "self")` | `23:mote.child-graph-run.v14:loop1:54:self` |
| `("é", 0, "e\u0301")` | `23:mote.child-graph-run.v11:é1:02:e\u0301` |

最后一行使用 Python escaped notation 展示，实际字段是两个 code points `U+0065 U+0301`，输出中不包含反斜杠或字面量 `u0301`。

Pending nested node 的 execution projection 必须是严格 union：

```python
@dataclass(frozen=True, slots=True)
class MissingChild:
    parent: ParentGraphActivation


@dataclass(frozen=True, slots=True)
class ActiveChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


@dataclass(frozen=True, slots=True)
class CompletedChild(Generic[OutputT]):
    parent: ParentGraphActivation
    child_state: GraphRunState
    output: OutputT
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class AbortedChild:
    parent: ParentGraphActivation
    child_state: GraphRunState


ChildProjection: TypeAlias = (
    MissingChild | ActiveChild | CompletedChild[OutputT] | AbortedChild
)
```

Child `Failed/Interrupted` settlement 不终结 child run。调用方必须对原 child `ResumeGraphNodes`；parent activation 保持 Pending 并指向同一个 child
run。只有 child COMPLETED/ABORTED 才成为 parent terminal result。不得重建 child、增加 store lookup port 或把 child interrupt 复制到 parent
顶层字段。

`StepRequest` 继续直接接收 `GraphRunState`，并将旧 optional `nested_results` 替换为必填、不可省略的 `child_projections`：

```python
@dataclass(frozen=True, slots=True)
class StepRequest(Generic[InputT, OutputT]):
    state: GraphRunState
    node_input: InputT
    request_attempt_id: ExecutionRequestAttemptId
    child_projections: tuple[ChildProjection[OutputT], ...]
    limits: ExecutionLimits = field(default_factory=ExecutionLimits)
```

Execution 不自行查询 store，也不新增 loading owner/read view。调用方必须保证 parent state 与 child projections 属于同一个逻辑 snapshot；这是当前
execution request API contract，不承诺真实事务读取或跨进程一致性。

完整性规则：

1. 对当前 Frontier 每个 Pending nested activation 必须恰好提供一个 projection；
2. projection parent coordinates 必须非空、唯一、canonical，并精确覆盖 Pending nested activation coordinates；
3. `MissingChild` 必须显式出现；tuple 缺项绝不等于 MissingChild；
4. `ActiveChild/CompletedChild/AbortedChild` 必须携带调用方提供的 child `GraphRunState`；
5. child run ID、parent link、definition/version、status 与 projection variant 必须一致；
6. `ActiveChild` 只接受 RUNNING child，包括 EXECUTABLE 与 AWAITING_RESUME；
7. `CompletedChild/AbortedChild` 分别只接受 COMPLETED/ABORTED；
8. `AbortedChild` 不保存第二份 failure；execution 只从 `child_state.abort` 派生 typed parent TaskFailure；
9. 缺项、重复、额外 projection、非 canonical order、坐标错误或 variant/status 不符均 fail closed；
10. 即使当前图没有 Pending nested activation，也必须显式提供 canonical empty tuple，且拒绝额外 projection。

Prepare 不能用缺省空 tuple 推导 child 不存在。Claim command 经 reducer 应用后，`GraphExecutor.execute` 从 resulting parent state 重建时必须再次接收并验证满足上述
完整闭包的新 `child_projections`；prepare 阶段 projections 不能隐式复用。调用方也不能只用 task ID 或裸 output
声称 child 已完成。

### 9.2 Resource admission

Resource 算法、ordered acquisition 与 reducer 继续复用；participant identity 改为精确 node ID，并验证属于当前 Pending set。Task ID 不再作为
resource state identity。

集合关系固定为：

```text
batch lease node_ids
    == 当前 Frontier 的全部 Pending node IDs

resource acquisition participants
    == compiled graph 中需要资源的 executable Pending node IDs
    ⊆ batch lease node_ids
```

无资源的 executable Pending 与 nested Pending 不建立 acquisition。Execution 的 resource guard 负责依据 compiled node definitions 验证精确
participant subset、requirements 与 resource order；state reducer 只验证 participant 属于当前 Pending set、resource snapshot 的 state-owned
结构、合法 admission replay、至少存在一个 Pending node 和 lease/resource lifecycle，不解释 compiled resource topology。

资源流程固定为：

```text
all Pending children terminal
    -> calculate/replay admission for resource-requiring executable Pending nodes
    -> apply UpdateGraphResources as needed
    -> claim all Pending nodes
    -> execute admitted/waiting acquisitions through existing resource waves
    -> typed settlement or exact fence clears admission
```

Claim 时 resource snapshot 可以同时包含 admitted 与 waiting acquisitions。Waiting 不表示 claim 非法；既有 resource scheduler 在同一 batch lease
内按确定顺序执行 admitted wave、释放资源并推进后续 waiting acquisition。只有 snapshot 无法合法 replay、participant/requirements 不匹配或
resource waves 无法推进时才 fail closed。不得为满足 batch lease 全覆盖而给无资源或 nested Pending 创建虚假 acquisition。

Resume 只接受 `resources is None`。Quiescent abort 可以清理尚未 claim 的 admission；active lease 时必须先外部停止并 exact fence。

### 9.3 Routing 与 join

`execution/engine/routing.py` 同时提供：

- 单个 contribution 对 compiled node topology 的窄校验；
- 完整 `Succeeded/Skipped` contribution 集合的 routing/join resolution。

两者共享同一规则和图索引。Routing 输入按 Frontier node identity，而不是本 attempt task results；这样 retained success、skip 和新 success 恰好各
应用一次。

只要存在 Pending、Failed 或 Interrupted：

- 不更新 join arrivals；
- 不创建 next Frontier；
- 不完成 GraphRun。

最后一个 typed outcome 或 skip 令 Frontier settled 时，resolution 与 settlement 由同一 command 在同一 state revision 原子应用。

### 9.4 Loop 与新 activation

Self-loop 或普通 loop 仍由 routing 产生新的 superstep。`AdvanceGraphFrontier` 必须：

1. `superstep += 1`；
2. 为 next node IDs 创建全新 `Pending(UseStepRequestInput)`；
3. 按 routing decision 替换 join progress；
4. 清 execution/resources；
5. 不复制旧 settlement、override 或 interrupt identity。

因此相同静态 node ID 在新 superstep 是新 activation；failure/interrupt resume 则保持原 superstep，只增加后续 claim generation。

## 10. 文件级改动清单

### 10.1 State owner

| 文件 | 目标改动 |
| --- | --- |
| `state/graph_state/identity.py` | 唯一定义 `GraphRunId`、`GraphDefinitionId/Version`、`GraphNodeId`、`GraphRouteId`、`GraphExecutionAttemptId`、`GraphInterruptId`，以及只接收 scalar coordinates 的共享 length-prefixed field、interrupt/child-run projections；不导入其他 graph-state models |
| `state/graph_state/routing.py` | 唯一定义 `ContinueGraphRouting`、`SelectGraphRoute` 与 `GraphRoutingContribution`；不解释 compiled topology |
| `state/graph_state/frontier_model.py` | 唯一定义 `GraphFailure`、`GraphInterruptPayload`、codec/input binding、interrupt value objects、五种 settlement、Frontier 与纯查询；只依赖 identity/routing |
| `state/graph_state/model.py` | 唯一定义 GraphRun lifecycle/token/lease/join/parent/abort 与组合 `GraphRunState`；单向依赖 identity/routing/frontier/resource，不导入 command |
| `state/graph_state/command.py` | 唯一定义 attempt outcomes、Frontier resolution、resume actions 和 Start/Claim/Settle/Resume/Fence/Abort commands；可依赖 model/frontier/routing，删除旧 advance/complete/fail/resume/interrupt commands |
| `state/graph_state/execution_transitions.py` | Start、full Pending claim、exact settle、exact fence 与共享 advance/complete primitive |
| `state/graph_state/recovery_transitions.py` | 实现统一 node resume/skip reducer；最终 skip 复用共享 advance/complete primitive |
| `state/graph_state/interrupt_transitions.py` | 删除文件；Abort 移入窄 `lifecycle_transitions.py`，不保留顶层 interrupt 逻辑 |
| `state/graph_state/resource_model.py` | 删除泛化 `ParticipantId` 第二身份；`ResourceAcquisition`、lock owner/waiters 直接使用唯一 `GraphNodeId` |
| `state/graph_state/resource_command.py`、`resource_reducer.py` | acquisition/release commands 与 pure resource reducer 全链路改用 `GraphNodeId` |
| `state/graph_state/resource_transitions.py` | 只允许 EXECUTABLE Frontier 更新 admission，并验证 node membership、replay 与 quiescent lifecycle |
| `state/graph_state/validation.py` | lifecycle/settlement/mixed Frontier/identity/codec/lease/resource invariants，包括 parent-bearing deterministic child ID 与 resource/Pending 闭包 |
| `state/graph_state/transition_guard.py` | exact batch lease、token、canonical node coverage 共享 guard |
| `state/graph_state/reducer.py` | 对最终 command union 穷尽 dispatch；删除 abort catch-all |
| `state/graph_state/__init__.py`、`state/__init__.py` | 只导出新模型/commands；删除全部 legacy exports |

`recovery_transitions.py` 是 state-owned command transition 模块，不是 runner 或第二套恢复引擎；resolution 应与 settlement transition 共享一个纯
advance/complete primitive，禁止复制生命周期逻辑。

State 模块依赖方向必须保持无环：

```text
identity ───────────────> routing
identity + routing ─────> frontier_model
identity + routing + frontier_model + resource_model
                       └> model
identity + routing + frontier_model + model + resource_model
                       └> command
```

箭头表示“被右侧模块导入”。`command.py` 位于 transition DTO 顶层，可以依赖 `model.py` 与 `frontier_model.py`；两者不得反向依赖 command。

### 10.2 Execution graph 与 projection

| 文件 | 目标改动 |
| --- | --- |
| `execution/graph/outcome.py` | `NodeSuccess` 直接使用 state-owned `GraphRoutingContribution`，增加 typed `NodeInterrupt`；保持 ordinary exception 在 union 外 |
| `execution/graph/resume_input.py` | 唯一 encoder/decoder binding，替换并删除 `resolution.py` |
| `execution/graph/identity.py` | 删除；静态图、GraphState、execution projection 与 resource 统一导入 state-owned identities，不保留 `NodeId` 或 definition identity aliases |
| `execution/graph/command.py` | 删除；routing command 直接使用 state-owned `GraphRoutingContribution`，不保留 `Continue/SelectRoute/RoutingCommand` aliases |
| `execution/graph/edge.py` | topology edge 直接使用 state-owned `GraphNodeId`、`GraphRouteId`；删除 local `RouteId` |
| `execution/graph/definition.py`、`topology.py`、`compiler.py`、`validation.py` | 全链路使用 state-owned definition/node/route identities，并从 definition 到 compiled graph 传递、验证唯一 resume binding |
| `execution/graph/__init__.py` | 只导出 resume codec 与新 outcome；删除 resolution exports |
| `execution/snapshot.py` | 删除；execution 直接只读消费 authoritative `GraphRunState`，不保留逐字段镜像 DTO |
| `execution/graph_run.py` | 只保留 compiled root/child start command projection，并强制 parent-bearing deterministic child ID；删除旧 snapshot/status/interrupt/transition mapping |
| `execution/transition.py` | 删除；不再保留 execution-local transition hierarchy，node outcomes 与 optional resolution 直接投影为唯一 state settlement command |
| `execution/request.py` | `StepRequest` 继续直接接收 `GraphRunState`；旧 optional nested results 替换为必填、严格的 `child_projections`；新增 typed resume request/actions |
| `execution/claim.py` | claim capability 使用 authoritative node IDs，task IDs 只作 execution projection 的派生校验 |
| `execution/result.py` | 五种 prepare disposition、完整 typed task outcomes、`ExecutedFrontierAttempt` 与严格 `ChildProjection` union |
| `execution/executor.py` | 唯一 root start/prepare/execute/resume API、compiled graph family root/child authority guard 和 graph command projection surface |
| `execution/__init__.py` | 新 public execution DTO exports；删除 legacy aliases |

### 10.3 Execution engine

| 文件 | 目标改动 |
| --- | --- |
| `execution/engine/snapshot_guard.py` | 唯一 snapshot/topology/codec/contribution guard |
| `execution/engine/resume_input.py` | node-scoped effective input encode/decode/materialization，替换并删除 `resolution_input.py` |
| `execution/engine/planner.py`、`task.py` | Pending-only planning 与 task ID 派生 |
| `execution/engine/frontier.py` | 五种 disposition、严格 child wait action union，以及 `ChildProjection` 的显式、canonical、精确覆盖 guard；child start 与 guard 共用 state-owned `child_graph_run_id` |
| `execution/engine/admission.py`、`resource_stage.py` | resource-requiring executable Pending participant subset 与既有 admitted/waiting resource waves |
| `execution/engine/claim_stage.py` | exact all-Pending node claim 与派生 task guard |
| `execution/engine/scheduler.py` | per-node input、typed interrupt、ordinary exception propagation |
| `execution/engine/collector.py` | 保存全部 success/failure/interrupt 并精确覆盖 batch |
| `execution/engine/routing.py` | 唯一 contribution validator 与完整 Frontier routing/join |
| `execution/engine/settlement.py` | 唯一 settlement selection：先校验 contribution，再直接投影 mixed settlement 或 settled resolution |
| `execution/engine/transition.py` | 删除；选择逻辑并入唯一 `settlement.py`，不保留转发 wrapper |
| `execution/engine/superstep.py` | 固定 prepare 优先级、claim 后重建与 `ExecutedFrontierAttempt` orchestration |
| `execution/engine/__init__.py` | 只导出最终 engine surfaces |

### 10.4 Tests 与 architecture rules

重写而不是兼容扩展：

- `tests/state/graph_state/test_reducer.py` 拆出 Frontier model、settlement、resume、interrupt identity、validation 测试；
- `tests/state/graph_state/test_identity.py` 固定基础 identity owner、共享 length-prefixed field、interrupt ID 与 child run ID vectors；
- `tests/state/graph_state/test_routing.py` 覆盖 state-owned contribution value objects；compiled route 合法性仍在 execution routing tests；
- `tests/state/graph_state/test_projection.py` 覆盖完整 settlement/codec/parent/含 node IDs 的 batch lease projection；
- `tests/execution/engine/` 覆盖 Pending planning、per-node input、collector、routing guard、mixed settlement；
- `tests/execution/test_executor.py` 覆盖 dispositions、selective resume/skip、nested、loop 与异常 fence；
- `tests/execution/test_interrupt_flow.py` 原位重写为 node interrupt/recovery flow，不保留 operator pause case；
- `tests/execution/test_resource_protocol.py` 改为直接 node identity、participant subset 与 exception/fence closure；
- `tests/architecture/` 只增加面向当前稳定架构的 owner/boundary 断言，例如唯一 engine/codec/routing owner 和 state 不依赖 execution；不保存
  legacy symbol 黑名单。

当前变更只涉及 Python Kernel 内部 snapshot DTO，不新增已确认的跨语言 durable protocol；若实现时发现 `conformance/` 已存在受影响 observable DTO，
必须在同一 change 更新 contract 与 affected runners，不能以文档假设绕过 monorepo 规则。

## 11. 实施顺序

### Phase 1：State model 与纯 transitions

1. 建立唯一基础 identity 与 routing owners，删除 execution-local node/definition/route/routing duplicates；
2. 引入 Frontier settlement、input binding、routing contribution、interrupt identity 与 child-run identity projection；
3. 替换 GraphRun lifecycle、lease、parent、abort 与 codec identity；
4. 实现 Start/Claim/Settle/Resume/Fence/Abort reducers；
5. 提取 settlement 与 resume 共用的 atomic advance/complete primitive；
6. 重写 stable validation 与 state unit tests；
7. 固定并测试 interrupt ID 与 child run ID v1 length-prefixed vectors；
8. resource model/commands/reducer 全链路改用 GraphNodeId；
9. 删除顶层 interrupt transitions 和旧 commands/exports。

Phase 1 完成条件：仅依赖 state-owned types 的 reducer 与 recovered snapshot tests 全部闭环，不需要 compiled graph。

### Phase 2：Codec、projection、resume API 与 prepare

1. 将 resolution codec 原位替换为唯一 resume input codec；
2. 固定唯一 encoder/decoder codec contract，重写 graph definition/compiler validation；
3. 删除无消费者的 `ExecutionSnapshot` 镜像，execution 直接只读消费 `GraphRunState`；
4. 实现唯一 snapshot/topology/codec guard；
5. 实现 typed `GraphExecutor.resume` 与 per-node effective input 物化；
6. 替换五种 prepare disposition、必填且完整的 `child_projections` 和 Pending-only planner；
7. 更新 claim capability 为 node identity。

Phase 2 完成条件：encode/decode、mixed Frontier prepare、AwaitingResume、terminal 与 claim 前 fail-closed tests 通过。

### Phase 3：Scheduler、collector、settlement 与 node interrupt

1. 扩展 node/task typed outcome union；
2. scheduler 接收 per-node effective input；
3. collector 保留完整 success/failure/interrupt；
4. settlement 无条件先验证所有新 success contribution；
5. 使用 active generation 派生 interrupt identity；
6. 生成唯一 `SettleGraphExecution` 和 `ExecutedFrontierAttempt`；
7. 返回 `ExecutedFrontierAttempt(results, command)`，其中 command 是唯一 GraphState transition input，results 仅为 transient execution result；
8. 删除旧 transition hierarchy、`ExecutedSuperstep` 与兼容 exports。

Phase 3 完成条件：mixed typed outcomes、routing error、Python exception、stale lease/identity 和重复 interrupt flow 全部通过。

### Phase 4：Nested、resource、join 与 loop

1. parent link 和 child ID 改用 activation coordinates；
2. 实现四种 typed child projection与固定 wait priority；
3. resource participant 改用 node ID；
4. exact fence 原子清 lease/resources；
5. routing 输入改为完整 Succeeded/Skipped contribution；
6. 验证 join 只在 barrier settled 时应用；
7. 验证 loop 创建干净新 activation。

Phase 4 完成条件：nested child recovery、ordinary sibling wait、resource exception closure、retained routing/join 和 loop tests 通过。

### Phase 5：Legacy 删除与全量门禁

1. 删除旧文件、符号、tests、exports、文档引用和 dead branches；
2. 删除兼容 alias、fallback、双 DTO、双 codec 与第二 identity；
3. 通过实现 diff 与一次性 source/reference search 确认旧路径删除完整，不把历史名称写入永久 tests；
4. 运行面向当前稳定架构事实的 owner/boundary tests；
5. 运行项目与 monorepo 全量质量门禁；
6. 检查 conformance impact 并同步更新任何真实受影响 contract/runners。

各 Phase 是 review/编排顺序，不是允许长期共存的迁移态。最终 change 必须一次交付唯一模型。

## 12. 测试矩阵

### 12.1 State model 与 reducer

1. 多个 Failed 中只 resume 一个，其他 Failed 原样保留；
2. `Pending + Failed`、`Pending + Interrupted` 通过 validation；
3. 无 Pending 且存在 Failed/Interrupted 派生 `AWAITING_RESUME`；
4. 一个 command 原子执行 failure resume、failure skip 与 interrupt resume；
5. 任一 action 非法、重复、乱序或 stale revision 时整组拒绝；
6. skip Pending/Succeeded/Interrupted/Skipped 均拒绝；
7. action 与 settlement 类型不匹配时拒绝；
8. stale、wrong、已消费 interrupt ID 拒绝；
9. Skipped 保留原 failure、reason、routing 且没有 output；
10. skip 最后一个 Failed 必须同 revision advance/complete；
11. active lease/resource 时 resume 拒绝；active lease 时 abort 拒绝；
12. quiescent abort 清 admission、保留诊断 Frontier 并转 ABORTED；
13. exact fence 清 lease/resources，stale fence 不影响新 generation；
14. claim 精确覆盖所有且仅 Pending nodes；
15. settlement outcome 精确覆盖 lease，保留全部 typed outcomes；
16. interrupt outcome identity 与 exact token generation/run/superstep/node 任一不一致时整批拒绝；
17. stable validator 拒绝 generation `<= 0` 或大于 execution sequence；
18. stable validator接受合法的历史 generation 小于当前 execution sequence；
19. sibling 后续 attempt 增加 sequence 后，既有 current Interrupted 仍合法；
20. stable validator 不要求 history/journal 证明 historical lease；
21. interrupt ID v1 vectors 覆盖分隔符、Unicode、多字节长度、组合/分解但未归一化的 Unicode、相似 run/node 坐标和不同 generation，证明
    无歧义且稳定；
22. execution/API/reducer 使用同一个 state-owned ID projection，拒绝自定义拼接 ID。

### 12.2 Resume input 与 execution guard

23. failure resume 可选择 request default 或 node override；
24. interrupt resume 必须 override；skip 不接受 input；
25. typed request value 必须经唯一 encoder；encoder 依 codec contract 拒绝不可表示或不合法的值；
26. decoder 必须拒绝 malformed payload、错误结构或不合法值；只有成功返回才产生 `InputT`；
27. 同 batch 默认 input 与不同 node overrides 分别正确投递；
28. override 不广播 sibling；typed settlement 后随 Pending 消费；
29. ordinary exception 未 settlement、exact fence 后 override 保留并再次投递；
30. resume action guard/encode 失败不生成 command、不应用状态、无 lease；
31. codec 缺失/version mismatch 在 snapshot guard 失败；
32. claim 前 override decode/codec contract 失败不生成 claim；
33. claim 后 decode/codec contract/guard error 不 settlement 并保留 lease；
34. ABORTED retained override 不 decode、不投递、不 routing；
35. interrupt request payload 与 resume input payload 分离；
36. Kernel 内建 binding 通过固定 deterministic、round-trip 和 encode/decode error vectors；外部 provider 纯度只作为 assembly contract；
37. 无 resume codec 的 graph 拒绝 override 和 typed interrupt，但允许默认 input 与 typed failure。

### 12.3 Planner、scheduler 与 settlement

38. Planner 只为 Pending 派生 task，Failed/Interrupted/Succeeded/Skipped 不重跑；
39. no Pending 返回 AwaitingResume，分别暴露 failed/interrupted；
40. Completed/Aborted 返回 terminal disposition；
41. scheduler 收集同批 success/failure/interrupt；
42. ordinary Python exception、contract violation、cancellation、infrastructure error 不伪装 typed settlement；
43. mixed batch 中非法 success routing 即使同时有 failure/interrupt 也整批拒绝；
44. recovered AwaitingResume 中非法 retained contribution 在 disposition 前拒绝；
45. 全部 Succeeded/Skipped 后 routing/join 恰好执行一次；
46. 尚有 Pending/Failed/Interrupted 时不提前 routing/join；
47. 已 success/skip sibling 在选择性恢复后不重跑；
48. `ExecutedFrontierAttempt` 可表示不推进 superstep 的 mixed settlement；
49. 需要 compiled/execution knowledge 的 start、admission、claim、settle、resume 只投影唯一 state-owned graph command；纯 state lifecycle 的
    fence/abort 允许调用方直接构造唯一 state-owned command。两类路径都不自行调用 reducer 或替换 snapshot；
50. `ExecutedFrontierAttempt.command` 精确对应本 attempt settlement，`results` 只承载 transient typed task results；
51. `TaskSuccess.output` 只存在于 transient `results`，不进入 `SettleGraphExecution` 或 GraphState projection；
52. 本期不新增 AgentState proposal、transaction、loading port、store integration 或内存 handoff 机制。

### 12.4 Nested、resource、loop 与 lifecycle

53. MissingChild 返回非空 `StartMissingChildren`，ActiveChild 返回非空 `WaitForActiveChildren`，严格 union 拒绝空 payload 或混合分支；
54. 每个 Pending nested activation 恰好有一个显式 `ChildProjection`；缺项不能推导 `MissingChild`；
55. projection 缺项、重复、额外、乱序、坐标或 variant/status 不符均 fail closed；
56. `StepRequest` 直接接收 parent `GraphRunState` 和必填 `child_projections`；调用方承担同一逻辑 snapshot contract，execution 不新增 loading port；
57. claim 后重建必须再次接收并验证新的完整 projections，不能复用 prepare projections；
58. 任一 ActiveChild 令普通 Pending sibling 一起等待且不 partial claim；
59. child Failed/Interrupted 恢复原 child run，不重建 child；
60. child COMPLETED/ABORTED 分别投影 parent success/typed failure；
61. `ResourceAcquisition`、lock owner/waiters 和 resource commands 直接使用 GraphNodeId，不保留 ParticipantId 第二身份；
62. resource participant 精确等于需要资源的 executable Pending subset，不为无资源或 nested Pending 创建 acquisition；
63. 合法 waiting acquisitions 可在同一 batch lease 下由 resource waves 推进；无法 replay 或无法推进时 fail closed；
64. Failed-only/Interrupted-only 等无 Pending Frontier 拒绝 resource update；resource execution exception 后 exact fence 清 lease/admission，随后可 prepare 或 abort；
65. loop/new superstep 不继承 settlement、override 或 interrupt identity；
66. node 可在新 attempt 再次 interrupt，新 identity 使用新 generation，旧 identity 不保留；
67. 每个 activation 的稳定 snapshot 最多一个 outstanding interrupt；
68. operator GraphRun pause、SUSPENDED、顶层 interrupt path 不存在；
69. COMPLETED/ABORTED 不可 claim、settle或 resume；
70. nested child projection 必须匹配 parent activation、deterministic child run ID、definition/version、child status 与 projection variant。
71. Frontier settlement、`GraphNodeId`、failure、routing、input binding、interrupt 和 parent activation 直接复用 state value objects；不存在平行 node snapshots 或只换名的一一对应 DTO；
72. execution 直接读取 authoritative `GraphRunState`，不存在 `ExecutionSnapshot` 逐字段镜像、独立 identity、默认值、mutable cache 或第二 state invariant validator。
73. definition、node、route、durable claim attempt identities 在 state identity owner 各只有一个 `NewType` 定义，静态图和 execution 直接导入；
74. `ConditionalEdge`、`NodeSuccess`、Frontier settlement 与 attempt outcome 使用同一 state-owned route/contribution types，不存在 execution routing command DTO；
75. child run ID v1 vectors 覆盖冒号、数字、Unicode、相似前缀、不同 superstep 和循环 activation，证明稳定且无歧义；
76. stable validator、child start、四种 child projection guard 与 claim 后重建调用同一个 `child_graph_run_id`；同 activation resume 不换 child，下一
    superstep 产生不同 child；当前 executor composition 区分 root key 与 family child keys，但不全局固化 definition role。
77. `frontier_model.py` 直接拥有 failure/interrupt payload/codec/input/settlement/Frontier，且不导入 `model.py` 或 `command.py`；
78. `GraphNodeOutcome`、`GraphFrontierResolution` 和 resume actions 只属于 `command.py`，`AdvanceGraphFrontier` 直接复用 model-owned join progress；
79. state 模块依赖图保持 `identity/routing -> frontier -> model -> command` 的单向闭包，不存在循环或复制 DTO 来绕过循环。

上述 79 项覆盖需求的 51 项验收；新增 case 用于证明 codec contract、显式 child projection、resource/route/definition identity、interrupt/child-run
ID 内部投影和 DTO owner 边界，
不扩大到真实持久化、AgentState transaction 或 loading owner。

## 13. Architecture assertions 与替换完整性

### 13.1 唯一 owner 断言

Architecture tests 必须证明：

- 只有 `state.graph_state` 定义 settlement、interrupt identity、resume command 与纯 reducer；
- 只有 `state.graph_state.identity` 定义 graph run/definition/node/route/durable claim attempt/interrupt scalar identities 和 durable child-run projection；
- 只有 `state.graph_state.routing` 定义 routing contribution value objects，static edge、node outcome 与 execution routing 均直接复用；
- `frontier_model` 不依赖 run model/command，run model 不依赖 command；outcome/resolution/resume action 只存在于 command owner；
- 只有 `execution.engine.routing` 解释 compiled topology；
- 只有 `execution` graph engine 执行节点；
- 只有一个 resume input binding 与一个 per-node materializer；
- resume input 只有一个 encoder/decoder binding，结构和值检查只属于 codec contract；
- task ID 不进入 state lease、resource participant 或 parent activation；
- resource state 直接使用 GraphNodeId，不定义第二 participant identity；
- state 不依赖 execution、不解码 InputT、不导入 compiled graph；
- execution 直接只读消费 `GraphRunState` 并复用 state-owned immutable identity/value objects；不存在 run snapshot 镜像或第二 validation owner；
- 只有确有不同组合形状或 execution-only 派生信息的 DTO 才能存在于明确消费边界；
- executor 只投影 state-owned graph command，不自行调用 reducer、查询 store 或替换 in-memory snapshot；
- 没有 compatibility module、re-export alias、fallback reducer 或第二 runner。

这些断言只描述当前必须长期成立的 ownership 和 dependency facts，不引用被删除接口的历史名称。

### 13.2 一次性替换检查

实施 change 在合入前必须通过 diff review 和一次性 source/reference search 确认：

- 旧 commands、statuses、DTO、codec、interrupt transitions、exports 和 tests 已删除；
- 没有 compatibility alias、转发 wrapper、fallback branch、双写或第二 execution path；
- 调用方与文档已经切换到最终模型；
- 删除文件不再被 import、re-export 或打包。

该检查属于本次 coordinated replacement 的验收活动，不新增永久 legacy symbol blacklist、历史名称 architecture test 或长期 `rg` gate。历史需求和
评审文档继续保留原术语用于溯源。

## 14. 完成门禁

实现只在以下条件全部满足时完成：

1. state、execution、resource、nested、architecture tests 全部通过；
2. Python 3.11+ strict type checking 通过，无 `Any`、bare dict、reflection 或 string discriminator；
3. Ruff、format、import 与 package architecture checks 通过；
4. 所有新增 transition、recovery boundary 和 public behavior 有 deterministic branch coverage；项目既有 100% coverage 门禁保持通过；
5. `make check` 在 `mote-kernel` 通过；
6. monorepo root `pre-commit run --all-files` 通过；
7. package build 与 metadata/twine validation 通过；
8. 当前 owner/boundary architecture assertions 通过，并完成本次 change 的一次性替换完整性检查；
9. `conformance/` 影响已检查，若存在共享 observable contract 则 DTO、vectors 与 affected runners 同 change 更新；
10. 未新增 AgentState proposal/transaction/loading port、state-store integration 或默认 composition entry point；
11. `git diff --check` 通过，无无关文件、兼容 shim、dead test 或生成 cache 进入 change。

如果环境导致任一门禁不能运行，handoff 必须精确报告未运行命令、原因和剩余风险，不得用局部测试代替全量通过声明。

### 14.1 最终执行记录（2026-08-14）

实现与代码审核修复完成后，在用户已经激活的 `metagpt` Conda 环境中直接执行最终门禁：

```bash
cd /home/longert/motev2/mote-kernel
command -v python
command -v pyright
make check

cd /home/longert/motev2
pre-commit run --all-files --show-diff-on-failure
```

最终结果：

- `python` 与 `pyright` 均来自 `/home/longert/anaconda3/envs/metagpt/bin`，Python 为 3.11.15；
- Ruff lint 与 format check 通过，109 个 Python 文件格式稳定；
- Pyright strict 为 `0 errors, 0 warnings, 0 informations`；
- Pytest 收集并通过 504 项测试；
- Coverage 覆盖 1943 statements、634 branches，均为 100%；
- sdist 与 wheel 构建成功，Twine metadata check 通过；
- monorepo 全量 pre-commit 通过，包括 Ruff、format、detect-secrets 与文件结构 hooks；
- `git diff --check` 通过；
- 未改变跨语言 observable contract，因此 `conformance/` 无同步变更；
- 用户明确删除了只有 package docstring、没有实现或消费者的 `state/domain_state` 空 marker；package architecture contract 已同步撤销“该具体
  Python package 已落地”的断言。GraphState 与未来 DomainState 事实分离、原子提交的架构边界保持不变；
- 本地 `origin/main` tracking ref `1f8a426ce1e9bb2cff298951919592a82edb96e5` 的独立归档收集到 461 项测试，当前净增
  43 项 collected tests。在线刷新该 tracking ref 时，Git 凭据链调用了已不存在的临时 `gh` 可执行文件，且 VS Code credential socket 不可用，
  因此认证失败；该数字只声明为当前本地 `origin/main` 基线，不冒充已在线确认的远端最新值。

## 15. 明确不实施

本 change 不实施：

- store、journal、event log、attempt/lease/failure/interrupt history；
- interrupt counter/index、调用顺序 resume-value list/tape、Python continuation；
- Pending/Interrupted skip、执行前 operator skip 或 GraphRun pause；
- retry policy、自动恢复、退避、次数限制或错误分类；
- node lease、partial claim、同 Frontier multi-worker split；
- 通用 durable input binding、初始输入持久化、dict merge/patch 或反射 input update；
- 泛型 output persistence、跨进程恢复承诺或新增 state-store lookup；
- 本期 node output 建立 DomainState fact、携带 DomainState command，或引入 AgentState proposal/transaction/loading port；
- 第二 runner、第二 routing validator、第二 codec、第二 interrupt identity 或兼容 alias。

这些项目若未来需要，必须基于本模型另立需求；不得在本次实现中以隐藏字段、可选 fallback 或未测试 extension point 预埋。
