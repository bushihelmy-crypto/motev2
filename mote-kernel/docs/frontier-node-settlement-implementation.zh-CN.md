# Frontier 节点级结算与资源即时调度实施方案

> 核心原则：交付时不遗留已知架构负债，复用现有基础设施并保持唯一真相源，以优美、一致的代码实现最佳整体改动而非最小改动，且严格不超出需求边界。

## 1. 文档信息

- 状态：Implemented（方案复审与代码复审阻断项均已关闭，终版验证完成）
- 日期：2026-08-15
- 所属项目：Mote Kernel
- 实施范围：Python `state.graph_state` 与唯一 `execution` graph engine
- 需求来源：本轮确认的节点级结算、结果与资源原子更新、统一执行路径和 Frontier 最终 resolution 要求
- 替代文档：`docs/frontier-node-resume-implementation.zh-CN.md`
- 评审文档：`docs/frontier-node-settlement-implementation-review.zh-CN.md`
- 评审回复：`docs/frontier-node-settlement-implementation-review-response.zh-CN.md`
- 代码复审：`docs/frontier-node-settlement-implementation-code-review.zh-CN.md`
- 交付方式：一次 coordinated model replacement；最终代码、公开类型、测试和文档只保留一套 authoritative path

本文完整替代现有实施方案中的 batch settlement、batch result collection、resource wave execution 和 settlement 内联 resolution 模型。
原方案中与 failure/interrupt resume、input override、identity、routing、join、nested graph、fence 和 abort 有关且不与本文冲突的语义，由本文明确继承。
发生冲突时，以本文为唯一实施依据。

本方案以以下工程结果为验收标准：

1. 每个 typed node completion 单独生成一个 state-owned settlement command；
2. 节点 outcome、该节点资源释放和 waiter 推进由同一次 reducer 转换原子表达；
3. waiter 在释放资源后的新 `GraphRunState` 中立即成为 admitted；确认该 state 的同一次 session step 必须将其提交到 scheduler，不能被已排队的 typed sibling completion 延迟；
4. 有资源与无资源节点共用同一个 task scheduler 和 completion pipeline；
5. 最后一个节点先形成可恢复的 `SETTLED` Frontier snapshot，之后才能独立执行 routing resolution；
6. `GraphFrontierState`、`ResourceSnapshot` 和 execution token 各自只有一个 owner，不保存可从它们派生的第二份 durable truth；
7. 不保留兼容 alias、转发 wrapper、旧批量执行分支或第二 runner；
8. 保留所有仍然成立的测试语义，重写冲突测试，不通过删除测试降低覆盖；
9. 不新增 legacy 符号缺失、已删除文件检查或全仓字符串扫描门禁。
10. `GraphExecutionSession` 具有显式的 cancellation、close 和 quiescence 协议；只有已确认 quiescent 后才能 fence；
11. nested terminal projection 进入同一个 completion source 和 `SettleGraphNode` path，不启动普通 Port task、不占用并发 slot；
12. `GraphRunState.resources` 对“无 acquisition”只有 `None` 一种 durable 表达，底层 resource reducer 的临时 replay snapshot 不受此限制；
13. ordinary error 一旦被 session 观察，停止启动所有尚未启动的 Pending activation，并以不依赖 event-loop race 的规则暴露错误。
14. `GraphExecutionSession` 的 concrete creation 只能发生在 `GraphExecutor.execute()` 完成 owner-checked linear claim consumption 之后；公开类型不可直接构造；
15. 同一 session 是单消费者协议，并发 `next()` 必须在触碰 scheduler 前确定性 fail closed。

## 2. 需求边界

### 2.1 必须实现

#### 节点级结算

同一 Frontier 中的节点按完成顺序逐个提交：

```text
Pending(A, B)
    │ A typed completion
    ▼
A = Succeeded, B = Pending / live in current execution session
    │ B typed completion
    ▼
A = Succeeded, B = Succeeded
    │ persisted SETTLED Frontier
    ▼
resolve_routing
    │
    ├── AdvanceGraphFrontier
    └── CompleteGraphFrontier
```

不得等全部 Frontier tasks 结束后才生成一个包含全部 outcomes 的 command。

#### 结果与资源原子转换

一个节点 typed completion 对应一次且仅一次 pure reducer 转换：

```text
record node settlement
+ release resources owned by that node
+ advance resource waiters
+ update active execution disposition
= next GraphRunState
```

本文中的“节点结果”指 GraphState 控制面 outcome：success routing、failure 或 interrupt。泛型业务 `TaskSuccess.output` 继续是 transient result，
不新增 output store、DomainState transaction 或通用 result codec。

#### 统一执行路径

唯一普通节点执行闭包为：

```text
select currently executable Pending nodes
    -> execute_tasks
    -> one completion event at a time
    -> SettleGraphNode
    -> reducer-applied next GraphRunState
    -> select newly executable nodes
```

资源只影响 selector 是否选择某个 Pending node。不得保留 `execute_resource_waves` 或等价的第二执行函数。

#### Frontier resolution barrier

只有 authoritative state 已经是完整 `SETTLED` Frontier 时，execution 才能调用 routing engine。最后一个 node settlement 与
`AdvanceGraphFrontier` / `CompleteGraphFrontier` 必须是两个 state revisions。

### 2.2 明确不实现

本方案不包括：

1. 具体 Store、数据库、journal、event log 或 commit protocol；
2. Graph 内建重试、退避、最大尝试次数或错误分类策略；
3. Port 副作用幂等、补偿或 receipt reconciliation；
4. Graph 层 exactly-once；
5. 多 worker 对同一 Frontier 的 partial claim 或 node-level distributed lease；
6. Python coroutine、task handle 或 continuation 的持久化；
7. 泛型 node output 持久化、result reference store 或 DomainState API；
8. 新的默认 composition entry point；
9. 跨语言 conformance schema，除非最终实现改变已有跨语言 observable contract；
10. 为证明旧实现已删除而新增 legacy gate tests。

允许且必须明确的恢复语义是 at-least-once：

```text
node completed, settlement 未应用即崩溃
    -> node 仍是 Pending，恢复后可以重跑

settlement 已应用，之后崩溃
    -> node settlement 保留，恢复后不得重跑
```

“单个 session 内最多交付一次”只表示 session runtime 的重复交付保护，不是 Graph 层 exactly-once；command 在应用前丢失时，恢复后的新
attempt 仍可按 at-least-once 重新产生该 completion。

## 3. 当前代码基线

### 3.1 可直接复用的基础设施

当前实现已经具备以下正确基础，不应重写：

| 能力 | 当前 owner | 本方案用途 |
| --- | --- | --- |
| 五态 node settlement | `state/graph_state/frontier_model.py` | 继续作为 activation 结果唯一事实 |
| derived Frontier status | `frontier_status()` | 扩展为可稳定保存 `SETTLED` |
| revision CAS | `GraphRunState.revision` 与 reducer | 每个 node command 独立 revision |
| execution generation/token | `model.py`、identity functions | completion、interrupt 和 fence 的 attempt fence |
| resource model | `resource_model.py` | owner、waiter、acquisition 唯一事实 |
| ordered resource reducer | `resource_reducer.py` | settlement 内执行 `ReleaseResources` 并唤醒 waiter |
| resource admission planner | `execution/engine/admission.py` | claim 前生成 deterministic snapshot |
| routing/join resolver | `execution/engine/routing.py` | 只在 persisted `SETTLED` state 上运行 |
| snapshot/topology guard | `execution/engine/snapshot_guard.py` | 校验 compiled requirements 和 routing |
| resume input codec | `execution/engine/resume_input.py` | 保持 per-node override 行为 |
| selective resume | `ResumeGraphNodes` | 继续处理 failure、interrupt 和 skip |
| nested graph identity/projection | execution graph family | terminal child 结果汇入相同 settlement path |
| exact fence/abort | state transitions | 清理剩余 active attempt，保留已结算 sibling |

### 3.2 必须替换的批处理边界

当前代码存在以下与新需求直接冲突的事实：

1. `ClaimGraphExecution.node_ids` 和 stable validator 强制 lease 精确覆盖全部 Pending nodes；
2. `SettleGraphExecution.outcomes` 强制精确覆盖 active lease；
3. `collect_results()` 强制全部 planned tasks 都已有结果；
4. `execute_tasks()` 只在整个 `TaskGroup` 完成后返回 tuple；
5. `GraphExecutor.execute()` 一次只返回一个 `ExecutedFrontierAttempt` 和一个 batch command；
6. `execute_resource_waves()` 在 execution-local `ResourceSnapshot` 上释放资源，GraphState 看不到中间状态；
7. batch settlement 最后无条件清除整个 resource snapshot 和 lease；
8. `RUNNING + SETTLED` 被 stable validator 拒绝；
9. resolution 嵌套在 settlement/resume command 中，最后 settlement 与 routing 没有 durable barrier；
10. 初始 resource admission 和 claim 是两个 prepare rounds，并形成资源与执行两套准备分支。

这些不是局部 bug，而是旧运行模型的闭合约束。实施时必须整体替换，不得在旧 batch path 旁增加 node path。

## 4. 已关闭的架构决策

| 议题 | 唯一实施决定 |
| --- | --- |
| Node truth | `GraphFrontierState.nodes[].settlement` 是唯一 durable node status/result truth |
| Resource truth | `GraphRunState.resources` 中的 `ResourceSnapshot` 是 owner/waiter/admission 唯一 durable truth |
| Active attempt | `GraphExecutionLease` 只保存 exact token，不重复保存 Pending node IDs |
| Attempt participants | active attempt 的剩余 participants 始终由 `pending_node_ids(frontier)` 派生 |
| Running node | 不增加 `RunningGraphNode` settlement；live task handle 只属于显式 execution session |
| Claim scope | 一个 attempt 继续拥有当前全部 Pending activations，不引入 partial/node lease |
| Initial admission | resource admission 与 execution claim 在同一个 `ClaimGraphExecution` reducer transition 中提交 |
| Settlement | 唯一 `SettleGraphNode`，一次只接受一个 typed outcome |
| Resource release | reducer 从 authoritative resource snapshot 推导，不由 execution 提交替代 snapshot |
| Waiter scheduling | `ReleaseResources` 自动推进 waiter；新 state 中 admitted waiter 可立即进入 selector |
| Scheduler | 只有一个动态 task scheduler；资源节点不再走 wave executor |
| Stream protocol | execution session 每次接收最新 authoritative state，产出一个 result + command |
| Frontier barrier | `SETTLED` 是合法稳定 RUNNING state，resolution 是下一条独立 command |
| Resume/skip | `ResumeGraphNodes` 只改变 node settlements，不内联 resolution |
| Routing | execution owner 从 persisted `SETTLED` state 投影 standalone advance/complete command |
| Generic output | 继续 transient；GraphState 只保存 routing/failure/interrupt 控制结果 |
| Exceptions | ordinary exception 不产生 node outcome；此前已应用的 sibling settlements 保留 |
| Recovery | active attempt 异常或进程丢失后 exact fence，仅剩 Pending activations 可重跑 |
| Tests | 保留有效语义测试；冲突测试重写；不新增 legacy absence/string-scan gates |

## 5. Authoritative state model

### 5.1 目标状态树

```text
GraphRunState
├── run_id / definition_id / definition_version
├── status: RUNNING | COMPLETED | ABORTED
├── superstep
├── execution_sequence
├── resume_input_codec
├── frontier: GraphFrontierState
│   └── nodes[]
│       ├── node_id
│       └── settlement
│           ├── Pending(input_binding)
│           ├── Succeeded(routing)
│           ├── Failed(failure)
│           ├── Interrupted(identity, payload)
│           └── Skipped(failure, reason, routing)
├── join_progress
├── resources: ResourceSnapshot | None
├── execution: GraphExecutionLease(token) | None
├── abort
├── parent
└── revision
```

`GraphExecutionLease.node_ids` 删除。只要 `execution is not None`，当前 Frontier 中所有 `PendingGraphNode` 都属于该 attempt；每次 settlement
把一个 Pending 变为非 Pending，因此剩余 participants 自然随 Frontier 缩减。

这消除以下重复事实：

```text
lease.node_ids == pending_node_ids(frontier)
```

不再需要保存并持续校验等式两边。

### 5.2 Frontier status

继续使用现有三态派生规则：

```text
存在 Pending                                      -> EXECUTABLE
无 Pending，存在 Failed 或 Interrupted             -> AWAITING_RESUME
全部为 Succeeded 或 Skipped                        -> SETTLED
其他形状                                            -> invalid
```

与旧实现不同，三种 derived status 都允许成为稳定 `RUNNING` snapshot：

| Frontier status | execution/resources | 合法含义 |
| --- | --- | --- |
| EXECUTABLE | 均为空 | 等待 claim 或 resume 后尚未 claim |
| EXECUTABLE | execution 非空，resources 可空 | attempt active，部分节点可能 running/waiting |
| AWAITING_RESUME | 均为空 | 无 Pending，等待显式 resume/skip |
| SETTLED | 均为空 | 所有 node settlement 已提交，等待 routing resolution |

### 5.3 Stable invariants

State validator 必须保证：

1. `resources is not None` 必须同时满足 `execution is not None`；不再允许资源已 admission、execution 尚未 claim 的稳定状态；
2. `resources is not None` 时 `resources.acquisitions` 必须非空；没有 acquisition 的 active attempt 唯一使用 `resources=None`；
3. resource acquisition participants 必须是当前 Pending nodes 的子集；
4. execution guard 进一步证明 participants 精确等于 compiled graph 中需要资源的 executable Pending nodes；
5. admitted acquisition 的 owner/acquired prefix/wait queues 继续由 resource reducer replay validation 保证；
6. `AWAITING_RESUME` 和 `SETTLED` 必须 quiescent，不能携带 execution/resources；
7. active execution token generation 必须等于 `execution_sequence`；
8. `COMPLETED` 继续使用 canonical empty Frontier/resources/execution；
9. `ABORTED` 继续保留 quiescent diagnostic Frontier，并清 resources/execution；
10. retained success/skip routing、interrupt identity、override codec、join progress 和 parent identity 继续使用现有校验；
11. State validator 不导入 `CompiledGraph`，不解释 node resource requirements 或 conditional topology。

上述 `resources` canonical 约束只针对 `GraphRunState.resources` 的 durable/recovered state。`initial_resource_snapshot()` 和 resource reducer replay
可以继续使用带 resource locks、但尚无 acquisition 的临时基础 snapshot；该值不得直接挂入 authoritative `GraphRunState`。

## 6. State-owned command model

### 6.1 目标 command union

```python
@dataclass(frozen=True, slots=True)
class ClaimGraphExecution:
    expected_revision: int
    attempt_id: GraphExecutionAttemptId
    resources: ResourceSnapshot | None


@dataclass(frozen=True, slots=True)
class SettleGraphNode:
    expected_revision: int
    execution: GraphExecutionToken
    outcome: GraphNodeOutcome


@dataclass(frozen=True, slots=True)
class AdvanceGraphFrontier:
    expected_revision: int
    node_ids: tuple[GraphNodeId, ...]
    join_progress: tuple[GraphJoinProgress, ...]


@dataclass(frozen=True, slots=True)
class CompleteGraphFrontier:
    expected_revision: int


@dataclass(frozen=True, slots=True)
class ResumeGraphNodes:
    expected_revision: int
    actions: tuple[GraphNodeResumeAction, ...]
```

完整 `GraphRunCommand` 为：

```text
StartGraphRun
| ClaimGraphExecution
| SettleGraphNode
| FenceGraphExecution
| ResumeGraphNodes
| AdvanceGraphFrontier
| CompleteGraphFrontier
| AbortGraphRun
```

### 6.2 删除的 command surfaces

最终实现删除：

- `SettleGraphExecution`；
- `GraphFrontierResolution`；
- settlement command 中的 `outcomes` tuple；
- settlement/resume command 中的 optional `resolution`；
- `UpdateGraphResources`。

不提供旧名称 alias、deprecated wrapper 或 union fallback。

### 6.3 Outcome types

现有 state-owned outcomes 原位复用：

```text
SucceededGraphNodeOutcome(node_id, routing)
FailedGraphNodeOutcome(node_id, failure)
InterruptedGraphNodeOutcome(node_id, identity, request_payload)
```

一个 `SettleGraphNode` 精确携带一个 variant。`TaskSuccess.output` 只存在于 execution result，不复制到 state command 或 settlement。

## 7. Pure reducer transitions

### 7.1 ClaimGraphExecution

Claim transition 固定顺序：

1. 校验 `RUNNING`、quiescent、Frontier 为 `EXECUTABLE`；
2. 校验 expected revision 和 attempt identity；
3. 从 Frontier 派生非空 `pending_node_ids`；
4. 校验 proposed `ResourceSnapshot` 的 state-owned structure、FIFO replay 和 participant membership；若无 acquisition，唯一合法 command 表达为
   `resources=None`，拒绝挂载 empty `ResourceSnapshot`；
5. 创建 `GraphExecutionToken(execution_sequence + 1, attempt_id)`；
6. 原子写入 token-only lease 和 resources；
7. revision 增加一次。

Compiled resource order、exact participant subset 和 requirements 由 execution 在 command 投影前校验，并在 reducer-applied state 进入 execute 前再次 guard。

资源 free Frontier 使用同一个 command，`resources=None`。不再存在 admission-only disposition。

### 7.2 SettleGraphNode

Settlement transition 固定顺序：

1. 校验 `RUNNING` 和 exact active execution token；
2. 校验 outcome variant、node identity 和该 node 当前为 `PendingGraphNode`；
3. 对 interrupt 校验 `(run_id, superstep, node_id, generation)` 和 resume codec；
4. 构造 replacement settlement；
5. 若 resources 中存在该 node acquisition：
   - acquisition 必须 `admitted`；
   - 调用唯一 `reduce_resources(snapshot, ReleaseResources(node_id))`；
   - 由 resource reducer 同步释放 owner、推进 FIFO waiter 和继续其 available prefix；
6. 原子替换 Frontier node settlement 和 resource snapshot；
7. 若仍有 Pending：保留同一个 execution token；若 resource acquisitions 已空，将 resources 规范化为 `None`；
8. 若无 Pending：清 execution/resources，使 Frontier 成为 `AWAITING_RESUME` 或 `SETTLED`；
9. 运行完整 stable validation；
10. revision 增加一次。

Reducer 不调用 routing、不创建 next Frontier、不完成 GraphRun。

所有 typed outcomes 都表示 node invocation 已经完成，因此 success、failure 和 interrupt 使用相同资源释放逻辑。ordinary exception、contract error、
cancellation 或 infrastructure error 不生成 `SettleGraphNode`，不会伪造资源释放。

### 7.3 FenceGraphExecution

Fence 继续要求 exact active token，并原子清理：

```text
execution = None
resources = None
```

Fence 不改变：

- 已提交的 Succeeded/Failed/Interrupted/Skipped settlement；
- 仍未完成的 Pending input binding；
- superstep；
- join progress；
- execution sequence。

Reducer 只能验证 state-owned exact token，不能观察 session 的 live task；execution owner 必须在 session 已 `aclose()` 并确认 quiescent 后才提交
fence。该前置条件属于 execution-local lifecycle，不新增 `GraphRunState` 字段或 fence receipt。

因此部分结算后发生 ordinary exception 时，已结算 sibling 保留，只有剩余 Pending 在后续 claim 中重跑。

### 7.4 ResumeGraphNodes

Resume transition 保持现有 selective failure/interrupt/skip 规则，但删除 resolution 参数：

1. 只接受 quiescent `RUNNING` state；
2. actions 非空、distinct、canonical；
3. failure resume 和 interrupt resume 将指定节点变为 Pending；
4. skip 将 Failed 变为 Skipped；
5. override/interrupt identity/routing validation 保持现状；
6. resulting Frontier 可以是 `EXECUTABLE`、`AWAITING_RESUME` 或 `SETTLED`；
7. 即使最后一个 skip 令 Frontier `SETTLED`，也不内联 resolution。

### 7.5 AdvanceGraphFrontier / CompleteGraphFrontier

Standalone resolution command 只接受：

```text
status == RUNNING
frontier_status == SETTLED
execution is None
resources is None
expected_revision == state.revision
```

`AdvanceGraphFrontier`：

1. `superstep += 1`；
2. 使用 command 中 canonical next node IDs 创建全新 `Pending(UseStepRequestInput)` Frontier；
3. 替换 join progress；
4. 要求 next node IDs 非空且 canonical，join progress 使用 state-owned canonical shape；
5. 不复制旧 settlement、override 或 interrupt identity。

`CompleteGraphFrontier`：

1. `status = COMPLETED`；
2. 要求当前 `join_progress` 为空；存在 unresolved join progress 时整个 transition 原子失败；
3. 清 Frontier、join progress、resources 和 execution；
4. 使用 canonical terminal position。

State reducer 只校验 state-owned lifecycle 和 shape。next nodes、route choice 和 join calculation 的正确性继续由唯一 compiled routing engine 保证。

## 8. Resource admission 与即时调度

### 8.1 Claim-time admission

`prepare` 在创建 claim command 前：

1. 为全部当前 Pending activations 派生 canonical tasks；
2. 完成现有 input materialization 和 nested terminal projection validation；
3. 对 compiled resource-requiring ordinary nodes 调用现有 `admit_tasks()`；
4. 从 `initial_resource_snapshot(graph)` 开始一次性建立完整 acquisition queue；
5. 验证 resource order、participant subset 和 exact requirements；
6. 将 resulting snapshot 放入 `ClaimGraphExecution.resources`；
7. 生成 one-shot `PreparedExecutionClaim`。

调用方只需应用一次 claim command，随后使用 reducer-applied state 启动 execution。资源 free 与 resource-bearing Frontier 不再返回不同 prepare 分支。

### 8.2 唯一 executable selector

Execution owner 定义一个窄 selector，输入为 compiled graph、authoritative state 和显式 session runtime disposition。一个普通 node 可启动，当且仅当：

1. Frontier settlement 是 Pending；
2. state 携带与 session 相同的 active execution token；
3. node 尚未被当前 session 启动；
4. node 是 ordinary `NodeDefinition`；
5. 若无 resource requirements，直接可选；
6. 若有 resource requirements，对应 acquisition 必须存在、requirements 精确匹配且 `admitted=True`；
7. 启动后不超过 `ExecutionLimits.max_parallel_tasks`。

Selector 不修改 state，不抢占资源，不维护第二份 admission status。

候选节点按现有 planner 产生的 canonical `GraphTask.sort_key` 顺序选择；当可执行节点多于空闲 slot 时，取该顺序的前缀。
该顺序是 execution-local 的纯选择规则，不持久化 scheduler queue，也不改变 resource reducer 的 FIFO acquisition 顺序。

`max_parallel_tasks` 约束 live/selected tasks，而不是完整 Pending Frontier 的节点总数。`planner.py` 必须删除“Pending 总数超过并发限制即拒绝整个
Frontier”的 batch guard；超出当前空闲 slot 的 Pending nodes 保持未启动，并在后续 session step 继续参与选择。

### 8.3 释放后立即可调度

以共享资源 `file` 为例：

```text
before A completion
  Frontier: A=Pending, B=Pending
  file.owner=A
  B.waiting_for=file
  B.admitted=False

SettleGraphNode(A success)
  -> replace A settlement
  -> ReleaseResources(A)
  -> resource reducer wakes B

after reducer
  Frontier: A=Succeeded, B=Pending
  file.owner=B
  B.waiting_for=None
  B.admitted=True
  execution token unchanged
```

Execution session 下一次接收这个 state 时，selector 必须立即返回 B。不得要求：

- 当前 Frontier 全部结束；
- 额外 `UpdateGraphResources`；
- 新 execution claim；
- resource wave loop 的下一轮；
- execution-local resource snapshot 推演。

“立即”以 authoritative state application/commit 为边界。A 的 settlement command 只是被 yield、但尚未应用时，B 不得基于预测状态启动。

## 9. 唯一异步执行协议

### 9.1 为什么不能返回普通 batch result

现有 API：

```text
GraphExecutor.execute(...)
    -> await all tasks
    -> ExecutedFrontierAttempt(results, one command)
```

无法暴露中间 durable boundary。仅把 `results` 改成 list 或把 state 改成 node shape 都不能满足恢复要求。

### 9.2 State-acknowledged execution session

目标 API 使用显式 session capability：

```python
async with await executor.execute(claim, claimed_request) as session:
    while state.execution is not None:
        completed = await session.next(state)
        state = reduce_graph_run(state, completed.command)
```

`GraphExecutor.execute()` 是唯一的 execution entry point，但它返回的是 state-acknowledged session；`session.next(state)` 是唯一的逐节点
completion yield 协议。`execute()` 不等待整个 Frontier，不提供 batch overload，也不在 session 外另设 runner。若调用方不使用 async context
manager，也必须在 `finally` 中调用同一个幂等 `await session.aclose()`。

其中 transient result 为：

```python
@dataclass(frozen=True, slots=True)
class ExecutedGraphNode(Generic[OutputT]):
    result: TaskResult[OutputT]
    command: SettleGraphNode
```

实际调用方未来可以把 `reduce_graph_run` 替换为 store transaction，但本方案不定义该 Store。session 每次 `next(state)` 都必须使用调用方提供的最新
authoritative snapshot，不在内部调用 reducer，也不把预测 state 当成已提交事实。

### 9.3 Session lifecycle and quiescence

`GraphExecutionSession` 的生命周期只存在于 execution memory，不进入 `GraphRunState`，也不构成第二份 durable truth。实现必须表达以下
四个 session-local disposition；它们可以是内部 enum 或等价的私有状态机，不要求成为新的 state command：

Session creation 同样是 linear capability：顶层公开的 `GraphExecutionSession` 只提供不可直接构造的类型协议；唯一 concrete instance 由
`GraphExecutor.execute()` 在验证 graph/request/task scope、以 executor owner 消费 exact prepared claim 后签发。消费结果是一次性 construction
receipt，同一 prepared claim 或 receipt 都不能建立第二个 session。Validation 失败必须发生在 node invocation 前，并且 request/task validation
失败不得提前消费仍可正确使用的 claim。

| disposition | 允许行为 |
| --- | --- |
| `OPEN` | 校验 acknowledged state、选择并启动 ordinary nodes、交付 typed completion；可以转为 `ERROR_DRAINING` 或 `QUIESCENT` |
| `ERROR_DRAINING` | 不再启动任何新 Pending activation；继续收集/交付已启动 task 的 typed completion 和 ordinary error，直到 quiescent |
| `QUIESCENT` | 没有 live task、没有未处理的 task handle；completion source 已交付或被 close 丢弃；不再启动 node，可由 `aclose()` 转为 `CLOSED` |
| `CLOSED` | `aclose()` 已完成；不得 `next()`、submit 或产生 settlement command |

`aclose()` 契约固定为：

1. 幂等；
2. 请求取消仍存活的 task，并等待所有 task handle 完成或确认取消；
3. 丢弃尚未 yield 的 transient completion，不把它伪造成 settlement；
4. 不调用 reducer、不应用 fence、不撤销已经 reducer-applied 的 sibling settlement；
5. 先进入 `QUIESCENT`，确认没有 live task 后转为 `CLOSED`；`aclose()` 成功返回时调用方才可提交 exact `FenceGraphExecution`；
6. `next()` 被取消时执行 cancellation-safe close，再重新抛出 cancellation；cleanup 等待期间同一 task 再次被取消也不得中断 close；
7. `CLOSED` 后的 `next()` 或内部 submit 必须 fail closed。

`next()` 是明确的单消费者操作。一个 `next()` 尚未返回时，第二个并发 `next()` 必须在读取或修改 scheduler、acknowledgment、completion queue
之前确定性 fail closed，不能串行后从同一 revision 再交付一条 command。`next()` cancellation、并发 `aclose()` 和 disposition 转换共享同一
lifecycle 协调边界；并发 close 仍只执行一次 task cleanup。

正常 Frontier 完成时，最后一个 `SettleGraphNode` 被调用方应用后，session 也必须先确认没有 live task，再结束。close/quiescence 只提供
execution-local 清理证明，不创建 durable receipt；进程崩溃仍按 at-least-once 规则恢复。

### 9.4 Session responsibilities

`GraphExecutionSession` 只拥有不可持久化的运行时事实：

- executor owner identity；
- exact claim capability 和 execution token；
- 已启动 ordinary node IDs；
- 已注入/已交付的 precomputed nested node IDs；
- live asyncio task handles；
- 唯一 completion source（包含 ordinary scheduler events 与 precomputed nested terminal completions）；
- 已观察但尚未向调用方交付的 ordinary exception。

这些事实不能从 crash 中恢复，也不进入 `GraphRunState`。它们不构成第二份 durable truth。

每次 `next(state)` 固定执行：

1. 验证 state graph identity/version、stable invariants 和 compiled snapshot guard；
2. 验证 exact active token 与 session capability；
3. 首次调用要求 exact reducer-applied claim state；后续调用要求 state 恰好是上一条 yielded settlement command 的已应用 successor；
4. 重新验证 claimed request 中的 nested terminal projections，并按 `GraphTask.sort_key` 将每个尚未 settlement 的 terminal child projection
   注入唯一 completion source；
5. 若 completion source 尚有 precomputed nested completion，按 canonical 顺序逐个交付，不启动 ordinary task；nested completion 不调用普通
   Port scheduler、不占 `max_parallel_tasks` slot、不建立 resource acquisition；
6. 否则使用唯一 selector 找到新可执行但未启动的 ordinary nodes，并通过同一个 scheduler 提交；
7. 等待一个 typed completion 或 execution error；
8. 校验 result task coordinates、outcome variant 和 success routing；
9. 使用当前 state revision 投影一个 `SettleGraphNode`；
10. 返回 `ExecutedGraphNode`，并暂停需要新 authoritative state 的后续 scheduling。

ordinary scheduler 已有 queued event 时也必须遵守最新 state。Session 先移出 queued `TaskRaised` 并进入 `ERROR_DRAINING`，此时不得启动新
activation；若 queued event 是 typed completion，则先基于该 completion 投影 command，再按刚确认的 authoritative state 补满 ordinary
scheduler slots，并确保 newly admitted waiter 已实际开始后返回 queued completion。该规则不适用于 precomputed nested completion；按第 5 项，
nested queue 仍优先逐个交付且不启动 ordinary task。

Session 必须把 state acknowledgment 视为外部 reducer/store 的单-command commit 证明，至少验证：

1. revision 精确增加一次；
2. 上一条 command 指定的 node 已变为对应 settlement 且不再 Pending；
3. 其他 node 的 identity、graph definition/version、run/superstep 未非法变化；
4. active token 仍与 session capability 匹配，或因最后一个 settlement 合法清空；
5. resource snapshot 通过 stable validation 和 compiled resource guard；
6. 已交付的 nested completion 不会在 acknowledged state 中再次入队。

Session 不在内部调用 reducer、不预测或重算 successor，也不复制完整 graph reducer。这样不会预先从同一个 revision 生成多个 commands，也不会
在 settlement 尚未应用时调度新 waiter。若 command 尚未应用即 close/crash，completion 可在恢复后重新投影；这不是 exactly-once 承诺。

### 9.5 唯一 scheduler

`execution/engine/scheduler.py` 继续是 graph node invocation 的唯一 owner，但接口从“等待全部 tuple”调整为动态 completion source：

```text
submit(executable tasks)
    -> task handles

next_completion()
    -> TaskSuccess | TaskFailure | TaskInterrupt | TaskRaised
```

`TaskRaised` 必须携带原始 `GraphTask` identity 和 exception；session 在 quiescence 后按 `GraphTask.sort_key` 选择对外错误。precomputed nested
completion 复用同一 completion source 的队列与 task-coordinate validation，但不经过 ordinary node invocation。

资源 free、resource admitted 和后续 newly admitted nodes 全部通过相同的 submit/invoke/capture 逻辑。`execute_resource_waves` 删除。

Scheduler 保持：

- immutable per-node effective input；
- async-only invocation；
- typed NodeSuccess/NodeFailure/NodeInterrupt contract；
- unsupported outcome fail closed；
- task cleanup 和 cancellation safety；
- deterministic task identity。

### 9.6 Ordinary exception 与 cancellation

ordinary Python exception、contract error、codec error、routing validation error 或 infrastructure error：

1. 不转换为 Failed/Interrupted settlement；
2. 不释放该 node 的 authoritative resources；
3. 不撤销此前已经 reducer-applied 的 sibling settlements；
4. session 观察到首个 ordinary error 后转为 `ERROR_DRAINING`，不再启动任何尚未启动的 Pending activation，包括 resource-free、admitted
   和 waiting node；
5. 已启动的 siblings 继续执行 scheduler 的 quiescence/cleanup 规则；已得到的 typed completion 仍可逐个交付 settlement command；
6. 所有已启动 task quiescent 且 typed completion 已交付后，才暴露 ordinary error；
7. 多个 ordinary error 以所有已观察错误中 `GraphTask.sort_key` 最小者作为对外错误；只有一个错误时保留其原始异常对象；
8. 外部确认 workers 已停止后，对剩余 active attempt 使用 exact fence。

若调用方在 command yield 后尚未应用便取消或崩溃，该 completion 可以丢失并在恢复后重跑；若 `next()` 被取消，按 §9.3 执行
cancellation-safe close。本文不承诺 exactly-once。

## 10. Frontier resolution pipeline

### 10.1 ReadyToResolve disposition

`PrepareDisposition` 增加：

```python
@dataclass(frozen=True, slots=True)
class ReadyToResolve:
    command: AdvanceGraphFrontier | CompleteGraphFrontier
```

Prepare 固定顺序调整为：

```text
validate authoritative state and compiled graph
    ├── COMPLETED -> CompletedGraph
    ├── ABORTED -> AbortedGraph
    ├── SETTLED -> ReadyToResolve
    ├── AWAITING_RESUME -> AwaitingResume
    ├── active execution -> require original execution session
    ├── missing/active child -> existing WaitingForChildren disposition
    └── EXECUTABLE quiescent -> one atomic prepared claim
```

### 10.2 Resolution projection

只有输入 state 已经 `SETTLED` 时：

1. snapshot guard 校验每个 retained Succeeded/Skipped routing contribution；
2. routing engine 读取完整 `routing_contributions(frontier)` 和 prior join progress；
3. 计算 next nodes 和 next join progress；
4. 使用当前 state revision 投影 standalone `AdvanceGraphFrontier` 或 `CompleteGraphFrontier`；
5. execution 不自行应用 command。

### 10.3 Crash boundary

```text
last SettleGraphNode committed
    -> RUNNING + SETTLED + revision N

process crashes before routing
    -> recover revision N
    -> prepare returns ReadyToResolve
    -> project one resolution command from this current revision
```

即使未来 Store 尚未实现，pure state model 和 reducer 已完整表达该恢复边界。

## 11. Resume、interrupt 与 nested 行为

### 11.1 Failure/interrupt resume

现有行为全部保留：

- failure 与 interrupt 共用 `ResumeGraphNodes`；
- resume action 可以只选择部分 nodes；
- Failed-only skip 保留原 failure、reason 和 routing contribution；
- override 只作用于指定 activation；
- stale interrupt identity fail closed；
- 已 Succeeded/Skipped sibling 不重跑；
- resume 创建新 execution generation，但保持同一 `(run_id, superstep, node_id)` activation。

唯一变化是：resume 或 skip 形成完整 `SETTLED` Frontier 后，先保存该 state，再通过 `ReadyToResolve` 推进。

### 11.2 Interrupt identity

Interrupt identity 继续从以下坐标派生：

```text
(run_id, superstep, node_id, active execution generation)
```

因为一个 attempt 内有多个 node settlement commands，它们共享 generation，但 node ID 不同，identity 仍然唯一。settlement command 的 exact token
和 outcome identity 必须一致。

### 11.3 Nested graph

本方案不扩展 nested scheduling 范围：

- MissingChild 和 ActiveChild 继续使用现有 explicit child projections 和 wait disposition；
- 当前“active child 阻塞同 Frontier ordinary sibling claim”的 barrier 语义保持，不在本需求中改为 child/ordinary partial claim；
- child terminal projection 继续转换为 parent `TaskSuccess` 或 `TaskFailure`，并在 session 创建/首次 `next()` 时重新验证其 child identity、terminal
  status、definition snapshot 和 parent activation；
- CompletedChild / AbortedChild 作为 precomputed completion 注入唯一 completion source，按 `GraphTask.sort_key` canonical 排序；它们不调用
  ordinary Port scheduler、不占 live task slot、不建立 resource acquisition；
- parent nested node 的 terminal result 必须通过同一个 `SettleGraphNode` reducer path；
- nested node 不建立虚假 resource acquisition；
- 同一 session 内已交付或 acknowledged 的 nested completion 不得再次入队；若 command 在应用前丢失，恢复后的新 attempt 可以重新投影；
- child identity、parent activation 和 child snapshot guard 保持现状。

这保留唯一 graph engine，同时不把 nested orchestration 强行伪装成 ordinary Port invocation。

## 12. 文件级实施清单

### 12.1 State owner

| 文件 | 实施内容 |
| --- | --- |
| `state/graph_state/model.py` | `GraphExecutionLease` 只保留 token；保持其他 run model |
| `state/graph_state/command.py` | 新增 `SettleGraphNode`；claim 携带 optional resources；advance/complete 成为 standalone commands；删除 batch resolution surfaces |
| `state/graph_state/execution_transitions.py` | 实现 atomic claim、single-node settlement、standalone advance/complete、exact fence |
| `state/graph_state/recovery_transitions.py` | 删除 resume 内联 resolution；保留 selective actions |
| `state/graph_state/resource_transitions.py` | `UpdateGraphResources` 删除后删除该模块，不保留 forwarding shell |
| `state/graph_state/resource_reducer.py` | 原位复用 acquisition/release/FIFO 算法；只在发现真实缺陷时修改 |
| `state/graph_state/validation.py` | 允许 stable SETTLED；强化 resources canonical shape 与 resources implies execution；participants 只从 Frontier 派生 |
| `state/graph_state/reducer.py` | dispatch 新 closed command union，保持 exhaustive `assert_never` |
| `state/graph_state/__init__.py` | 只导出新 authoritative surfaces |

### 12.2 Execution owner

| 文件 | 实施内容 |
| --- | --- |
| `execution/result.py` | `ExecutedGraphNode`、`ReadyToResolve` 和新的 prepare result shape |
| `execution/claim.py` | claim snapshot/capability 适配 token-only durable lease；保持 executor ownership |
| `execution/engine/claim_stage.py` | resource admission 与 claim projection 合并为一个 prepared claim |
| `execution/engine/admission.py` | 保留并集中 initial snapshot、exact compiled requirements 和 admission projection |
| `execution/engine/resource_stage.py` | 删除；不保留 wave executor 或 forwarding module |
| `execution/engine/planner.py` | 保持 Pending task identity derivation；为 selector/limits 提供 canonical plan |
| `execution/engine/frontier.py` | 校验 child projections，并将 terminal nested results 投影为 precomputed completion |
| `execution/engine/session.py` | `GraphExecutionSession` 生命周期、state acknowledgment、唯一 completion source 编排和 quiescence；不拥有 reducer/Store |
| `execution/engine/scheduler.py` | 改为唯一动态 task pool/completion source，支持 task identity、quiescence 和 cancellation |
| `execution/engine/collector.py` | 删除 batch collector；单 completion validation 归 settlement/session owner |
| `execution/engine/settlement.py` | 校验一个 result 并投影一个 `SettleGraphNode` |
| `execution/engine/routing.py` | 从 persisted SETTLED state 投影 standalone resolution command |
| `execution/engine/superstep.py` | prepare、session creation、selector 驱动和 ReadyToResolve orchestration |
| `execution/engine/snapshot_guard.py` | 支持 active partial settlement snapshot 与 stable SETTLED routing guard |
| `execution/executor.py` | `execute()` 返回 state-acknowledged session；保持唯一 graph executor |
| `execution/__init__.py`、`engine/__init__.py` | 更新唯一公开 surfaces |

### 12.3 文档

需要同步：

1. 将 `docs/frontier-node-resume-implementation.zh-CN.md` 标记为被本文替代的历史实施基线；
2. 更新 architecture 文档中的 batch settlement、resource waves 和 atomic resolution 描述；
3. 不删除仍可解释历史决策的评审文档，但其状态必须清楚，不得与当前 authoritative implementation 混淆；
4. 将本方案对应的评审与回复文档保持为 review history，不把评审建议重新变成第二份 implementation specification；
5. 若最终 observable contract 仅限 Python internal API，不修改 `conformance/`；若跨语言 contract 发生变化，再按 monorepo 规则同步。

## 13. 测试保留与迁移策略

### 13.1 基本原则

迁移前历史基线为 504 个 collected tests（当时实测 504 passed）。该数字只用于建立迁移清单，不是通过删除、合并或新增低价值断言追求的目标。

每个现有测试必须归入以下四类之一：

| 分类 | 处理方式 |
| --- | --- |
| KEEP | 行为和 API 未变化，原测试原样保留 |
| MIGRATE | 语义仍成立，但 command/result shape 改变，迁移断言到新 surface |
| REPLACE | 测试固化旧 batch 行为，使用新需求反例完整替换 |
| REMOVE | 仅验证已删除且不再成立的具体旧 API；其底层有效语义必须已由 KEEP/MIGRATE/REPLACE 覆盖 |

实施提交必须维护测试迁移清单。不得整文件删除测试后只补少量 happy-path，也不得用 collected count 掩盖语义覆盖下降。

### 13.2 必须原样或等价保留的测试

以下现有语义与新需求无冲突，必须保留：

1. graph compiler、topology、edge、conditional route、join 和 loop validation；
2. nested graph identity、missing/active/completed/aborted projection 与 parent guard；
3. state identity、immutability、canonical ordering、invalid recovered state rejection；
4. Frontier 五态 union、mixed Pending/Failed/Interrupted 和 derived status queries；
5. resource reducer acquisition order、FIFO waiters、partial prefix、release、snapshot replay 和 corruption rejection；
6. resume input codec、override isolation、round-trip/error contract；
7. selective failure resume、interrupt resume、skip、stale identity 和 action atomicity；
8. routing contribution validation、unknown route、join progress 和 deterministic resolution；
9. exact revision、execution generation、stale token、fence 和 quiescent abort；
10. immutable per-node input、falsy output、typed outcome contract 和 async cleanup；
11. package structure、dependency direction、single owner、no `Any`、module-scope imports 和唯一 executor ownership；
12. GraphExecutor 不调用 reducer、不拥有 Store、不修改 caller state 的 architecture contract；
13. existing terminal COMPLETED/ABORTED guards；
14. snapshot definition/version/membership/codec/parent/resource requirement guards；
15. build、type checking、coverage 和 packaging tests。

### 13.3 必须迁移的测试

以下测试的有效 invariant 保留，但适配新 API：

1. exact batch claim coverage：迁移为“claim 始终从 Frontier 派生全部 Pending，command/lease 不保存第二份 node IDs”；
2. collector task coordinate checks：迁移到 single completion/session validation；
3. mixed success/failure/interrupt collection：拆为连续 node commands 和每步 state assertions；
4. resource admission replay：迁移到 atomic `ClaimGraphExecution.resources`；
5. resource wave FIFO execution：迁移为每次 release 后新 state admitted + session selector 启动；
6. settlement token/interrupt coordinate guards：迁移到 `SettleGraphNode`；
7. executor batch result assertions：迁移到 `ExecutedGraphNode` stream sequence；
8. final settlement atomic resolution：迁移为 stable SETTLED revision + standalone resolution revision；
9. resume skip resolution：迁移为 ResumeGraphNodes 后 SETTLED + ReadyToResolve；
10. claim 后 codec/guard failure：继续证明不产生 settlement，并由 exact fence 清理 remaining attempt；
11. cancellation cleanup：适配 execution session 生命周期，保留所有 started task cleanup assertions；
12. architecture single-owner tests：更新新 command/session owner 和 token-only lease fields；
13. state acknowledgment：只接受上一条 command 的单 revision successor，不在 execution 内复制 reducer；
14. nested terminal completion：迁移为唯一 completion source 的 precomputed event，不改变 child wait barrier。

### 13.4 文件级测试迁移账本

以下表是实施时必须遵守的初始账本；同一文件可以同时包含 KEEP、MIGRATE 和 REPLACE cases。辅助 factory/driver 随消费者调整，不计作删除测试。

| 当前测试文件 | 分类 | 要求 |
| --- | --- | --- |
| `tests/architecture/test_dependency_direction.py` | KEEP | 保持现有 dependency direction |
| `tests/architecture/test_generic_integrity.py` | KEEP | 保持 generics、typing 和 source integrity |
| `tests/architecture/test_package_structure.py` | KEEP/MIGRATE | 保持 package contract，只更新真实删除/新增的 owned modules |
| `tests/architecture/test_source_discipline.py` | KEEP/MIGRATE | 保持 async-only、唯一 executor、input contract；更新新 session/scheduler surface |
| `tests/architecture/test_graph_execution_ownership.py` | MIGRATE | 保持 single owner、无 reducer/store ownership、无 forwarding alias；更新 token-only lease 和新 command owners |
| `tests/state/graph_state/test_frontier_model.py` | KEEP/ADD | 原五态与 mixed Frontier tests 保留，增加 stable SETTLED 使用场景 |
| `tests/state/graph_state/test_identity.py` | KEEP | identity 与 canonical query 行为不变 |
| `tests/state/graph_state/test_resource_reducer.py` | KEEP | acquisition、FIFO、partial prefix、release 和 replay 全部保留 |
| `tests/state/graph_state/test_projection.py` | MIGRATE | claim、lease、command projection shape 迁移，不降低 identity/immutability 覆盖 |
| `tests/state/graph_state/test_execution_transitions.py` | MIGRATE/REPLACE | batch settle cases 改为逐节点 transition；start、token、fence、interrupt guards 保留 |
| `tests/state/graph_state/test_execution_resource_transitions.py` | MIGRATE | admission replay invariant 移入 atomic claim/settlement tests；不直接丢弃原非法 snapshot cases |
| `tests/state/graph_state/test_recovery_transitions.py` | MIGRATE | selective resume 行为保留，resolution 改为下一 revision |
| `tests/state/graph_state/test_state_validation.py` | MIGRATE/ADD | 原 identity/codec/lifecycle guards 保留；更新 resources implies execution 和 stable SETTLED |
| `tests/state/graph_state/test_reducer.py` | MIGRATE | closed union、revision、purity、failure atomicity 保留并适配新 commands |
| `tests/execution/engine/test_admission.py` | KEEP/MIGRATE | `admit_tasks` 的 deterministic/FIFO/requirement guards 保留，接入 claim projection |
| `tests/execution/engine/test_planner.py` | MIGRATE | Pending-only planning 保留；Frontier 总数限制改为 dynamic live-task limit |
| `tests/execution/engine/test_completion_projection.py` | MIGRATE/MOVE | task coordinate、duplicate、missing、variant、falsy output cases 归入 single-completion projection/session tests；不保留 batch collector 语义 |
| `tests/execution/engine/test_session.py` | ADD/MIGRATE | session lifecycle、state acknowledgment、nested precomputed completion、error draining 和 canonical selector order；不增加 legacy gates |
| `tests/execution/engine/test_settlement.py` | REPLACE | 完整重写为 one result -> one command，同时保留 routing/interrupt/token 反例 |
| `tests/execution/engine/test_routing.py` | KEEP/MIGRATE | routing/join 算法 cases 保留，只迁移 standalone command projection shape |
| `tests/execution/engine/test_recovery_boundaries.py` | MIGRATE | codec、claim 后失败、session/fence、nested boundaries 保留并适配 streaming |
| `tests/execution/graph/test_topology.py` | KEEP | 无关行为原样保留 |
| `tests/execution/graph/test_compiler.py` | KEEP | 无关行为原样保留 |
| `tests/execution/graph/test_validation.py` | KEEP | 无关行为原样保留 |
| `tests/execution/graph/test_nested_graph.py` | KEEP | nested static semantics 原样保留 |
| `tests/execution/graph/test_join.py` | KEEP | join semantics 原样保留 |
| `tests/execution/test_executor.py` | MIGRATE/REPLACE | start/prepare/inputs/nested/cleanup 保留；batch result 与 exception all-or-nothing cases 反转 |
| `tests/execution/test_interrupt_flow.py` | MIGRATE | interrupt/resume identity 和 override cases 保留，settlement 改为逐节点 stream |
| `tests/execution/test_resource_protocol.py` | MIGRATE/REPLACE | admission/FIFO/fence/resume cases 保留；waves 改为 state-driven immediate scheduling，later-error case 反转 |
| `tests/test_package.py` | KEEP | package version/import contract 不变 |

`tests/execution/driver.py` 及各 `factories.py` 是测试基础设施，应适配新的 claim/session/reducer loop，为迁移后的全部测试提供唯一 driver，不能复制一套
batch driver 和一套 streaming driver。

### 13.5 终版逐 case 审计规则

实施初次收口时曾以 `422 passed + 100% coverage` 作为完成证据，这是错误的验收结论。相对历史 504 项净少 82 项时，文件级账本和覆盖率都不能
证明每个独立边界仍被测试；多个 child projection、interrupt、resource authority、start/codec、fence/generation 和 resume atomicity case 被合并进
少量大测试，另有旧 batch 反例没有逐项落成新模型的反向测试。最终审计按历史 collected case 逐项执行以下规则：

1. 未受模型替换影响的 case 原样 KEEP；
2. API shape 改变但 invariant 不变的 case，必须能指向一个独立的 MIGRATE case；
3. 与节点流式结算冲突的 case，必须指向一个明确验证相反恢复保证的 REPLACE case；
4. 只有纯旧 symbol/constructor shape 且其底层 invariant 已有落点时才 REMOVE；
5. 不允许以 parameter 合并、statement/branch coverage 或其他新增测试的数量抵消一个没有落点的历史 case。

高风险区域的终版收集闭环如下；这是审计结果，不是按数字配平测试的门禁：

| 历史区域 | 历史收集 | 终版主要落点 | 终版收集/结论 |
| --- | ---: | --- | --- |
| batch collector | 25 | `test_completion_projection.py`，并由 session acknowledgment 补充 claim scope | 25；坐标、未知/重复、typed variants、falsy output 和乱序逐节点投影均有独立落点 |
| recovery boundaries | 38 | `test_recovery_boundaries.py` + `test_runtime_boundaries.py` | 6 + 34；crash window、child/snapshot/runtime fail-closed 与 claim-consumption-issued 一次性 session receipt 边界分开表达 |
| resource protocol | 22 | `test_resource_protocol.py` | 22；missing/active child barrier、requirement drift、authority mismatch、竞争 claim、mixed outcome、override、nested 和 later-error 反转均保留 |
| execution transitions | 45 | `test_execution_transitions.py` + `test_execution_resource_transitions.py` | 44 + 11；旧 batch coverage 改为逐节点 revision，资源原子转换单独覆盖 |
| executor / interrupt / reducer | 跨文件迁移 | `test_executor.py`、`test_session.py`、`test_interrupt_flow.py`、`test_recovery_transitions.py`、`test_reducer.py` | owner/request/fence/cancel、interrupt generation、selective resume、closed union 和 standalone resolution 逐项交叉核对 |

终版全套为 522 个 collected tests：历史 504 项全部取得 KEEP/MIGRATE/REPLACE 落点，新增项覆盖 session linear creation/concurrency、
queued completion 下的即时调度与 ordinary error 优先级，以及节点级模型独有的 canonical claim scope、late settlement、
cancel/fence/reclaim、node-initiated cancellation cleanup 和 forged attempt token 边界。数量增加只是逐 case 补全后的结果；
验收依据仍是上述语义映射以及所有测试真实执行通过。没有新增 legacy symbol、旧文件存在性或 source string-scan 门禁。

### 13.6 必须反转的旧行为测试

以下旧断言与新需求直接冲突，必须替换为相反的恢复保证：

#### Later resource wave exception

旧行为：B 的 later exception 导致已成功的 A result 全部丢弃。

新行为：

```text
A success command 已应用
    -> A remains Succeeded
    -> A resources already released
    -> B may already be admitted/running
B raises ordinary exception
    -> B remains Pending
    -> exact fence clears remaining token/resources
    -> retry only B
```

#### Parallel ordinary exception

旧行为：任一 ordinary exception 阻止同批 typed sibling settlement。

新行为：任何已经交付并应用的 typed sibling completion 必须保留；ordinary exception 只阻止其自身 settlement，并停止启动新的 tasks。

#### Batch completion return

旧行为：`execute()` 只在所有 nodes 完成后返回一个 command。

新行为：A 完成时必须在 B 尚未完成的可控测试中取得 A 的 `SettleGraphNode`。

### 13.6 必须新增的行为覆盖

至少增加以下 deterministic tests：

1. A/B 并发，A 完成后立即返回 A command，B 仍 blocked/running；
2. A settlement 后 Frontier 为 A Succeeded、B Pending，active token 保持；
3. A 持有资源、B waiting，A settlement 同一 reducer revision 令 B admitted；
4. B 在 A command 应用前不得启动，应用后的下一次 session step 必须启动；
5. A failure 和 A interrupt 同样释放资源并推进 B；
6. resource-free 与 resource-admitted nodes 经同一个 scheduler 并发执行；
7. 多个不同资源 owner 完成时，各自 settlement 独立释放且不覆盖 sibling state；
8. partial multi-resource acquisition 在 owner settlement 后立即完成 available prefix；
9. completion command 使用最新 revision；两个完成事件不能复用同一 expected revision；
10. duplicate/stale node completion 被 Pending settlement 和 token guard 拒绝；
11. settled sibling + remaining Pending 的 exact fence 只清 execution/resources；
12. 最后一个 success 只生成 stable SETTLED，不直接 advance/complete；
13. 从 recovered SETTLED snapshot 可投影并应用 ReadyToResolve command；
14. final settlement 与 resolution 的 revision 精确相差一次 command；
15. skip 最后一个 Failed 后先 SETTLED，再 resolution；
16. task completion 后、settlement 前的模拟 crash 保持 Pending，可再次执行；
17. settlement 后、下一 waiter 启动前的模拟 crash 保留 settled node 和 authoritative resource progression；
18. final settlement 后、resolution 前的模拟 crash 不重跑任何 Frontier node；
19. ordinary error 后已经完成的 typed siblings 逐个结算，remaining Pending fence 后可重新 claim；
20. session 不能接收错误 graph、错误 token、倒退 revision 或另一个 executor 的 claim capability；
21. max parallel limit 对动态 newly admitted nodes 持续生效；
22. all Pending nested terminal results 通过同一 `SettleGraphNode` path，现有 nested wait barrier 不变；
23. session 支持 async context manager 与幂等 `aclose()`，close 后无 live task 且 `next()` fail closed；
24. `next()` cancellation 会完成 cancellation-safe close；cleanup 期间再次 `cancel()` 仍等待 close 完成，未 yield completion 不伪造 settlement；
25. nested-only Frontier 的首个 `next()` 立即交付 precomputed `SettleGraphNode`，不占 scheduler slot；
26. nested command 应用后不重复入队，command 丢失后允许新 attempt at-least-once 重投；
27. SETTLED + non-empty `join_progress` 拒绝 `CompleteGraphFrontier`，失败转换不修改 state；
28. GraphRunState 挂载 empty `ResourceSnapshot` 被拒绝，低层 resource reducer 的初始 replay snapshot 仍可用；
29. ordinary error 后不启动任何未启动 activation，多个 error 按 `GraphTask.sort_key` 选择 deterministic error；
30. selector slot 竞争按 canonical `GraphTask.sort_key` 选择，不依赖集合迭代顺序。
31. 公开 `GraphExecutionSession` 协议不可直接实例化；prepared claim 只能经 owner-checked `GraphExecutor.execute()` 建立 session；
32. 同一 prepared claim 的并发 `execute()` 只有一个成功；consumed receipt 不能由 snapshot 直接构造，并且只能签发一个 concrete session；
33. 一个 `next()` 等待 completion 时，并发第二个 `next()` 在 scheduler 前确定性拒绝，且只发生一次 node invocation、只交付一条 command；
34. active `next()` 与多个 `aclose()` 并发时只清理一次，不泄漏内部 collection race 或 `StopIteration`；
35. owner 与 resource-free sibling 同时完成且 sibling completion 已排队时，应用 owner settlement 后的下一次 `next()` 在返回 sibling 前已启动 newly admitted waiter；
36. 同一场景若排队的是 ordinary error，则先进入 `ERROR_DRAINING`，newly admitted waiter 不得启动。

### 13.7 明确不新增 legacy 门禁测试

不得新增以下测试：

- `assert not hasattr(..., "SettleGraphExecution")`；
- 扫描 `__all__` 只为证明旧名称不存在；
- 使用 `rg`、AST 或全仓字符串匹配旧 symbol/file/module 名；
- 断言某个旧文件路径已删除；
- 导入旧 API 并期待 ImportError；
- 仅为证明“没有 compatibility alias”逐项枚举历史名称。

现有通用 architecture tests，例如 dependency direction、single owner、no forwarding-only module、唯一 executor 和 reducer exhaustive dispatch，仍然验证当前
架构正向约束，应保留并更新到新模型；它们不是 legacy 名称黑名单。

## 14. 实施阶段

以下 phase 是本地实施顺序，不允许在最终提交中形成新旧并行 runtime：

### Phase 1：State model replacement

1. 修改 token-only lease；
2. 定义新 command union；
3. 实现 single-node settlement 与 atomic resource release；
4. 允许 stable SETTLED；
5. 拆分 standalone resolution；
6. 修改 resume/fence/validation/reducer；
7. 迁移 state tests。

### Phase 2：Atomic claim/admission

1. 将 resource snapshot 放入 claim command；
2. 合并 prepare admission/claim；
3. 删除 `UpdateGraphResources` transition 和 admission-only result；
4. 迁移 resource transition、projection 和 recovery tests。

### Phase 3：Streaming scheduler/session

1. 引入 explicit execution session；
2. 固定 OPEN/ERROR_DRAINING/QUIESCENT/CLOSED 的 session-local lifecycle 和幂等 close；
3. scheduler 改为 dynamic completion source；
4. 引入唯一 executable selector 与 canonical `GraphTask.sort_key` order；
5. settlement service 改为 per-result projection；
6. 删除 batch collector 和 resource wave executor；
7. 迁移 executor、interrupt、exception、cancellation 和 resource protocol tests。

### Phase 4：Resolution、resume 与 nested integration

1. 增加 ReadyToResolve；
2. routing engine 从 persisted SETTLED state 投影 command；
3. resume/skip 使用两阶段 settlement-resolution；
4. nested terminal results 作为 precomputed completion 接入唯一 node settlement stream；
5. 保留并验证原 nested wait barrier、terminal projection identity 和 at-least-once re-delivery 边界。

### Phase 5：Cleanup、文档与完整门禁

1. 删除旧 production surfaces 和空 forwarding modules；
2. 完成 KEEP/MIGRATE/REPLACE/REMOVE 测试迁移核对；
3. 更新 architecture 文档与旧实施文档状态；
4. 运行 formatter、lint、strict type checking、完整 tests、coverage、build 和 package checks；
5. 运行 monorepo pre-commit 与 `git diff --check`；
6. 不添加 legacy absence/string-scan tests。

## 15. 验收场景

### 15.1 无资源并发节点

```text
claim(A, B)
execute_tasks(A, B)
A completes
yield SettleGraphNode(A)
apply -> A Succeeded, B Pending, token retained
B completes
yield SettleGraphNode(B)
apply -> Frontier SETTLED, token cleared
prepare -> ReadyToResolve
apply Complete/Advance
```

### 15.2 冲突资源节点

```text
claim installs resources: A admitted, B waiting
selector -> A
A completes
SettleGraphNode(A)
  + A Succeeded
  + release A
  + admit B
next state -> selector immediately returns B
B completes -> SettleGraphNode(B)
```

### 15.3 Typed failure 释放资源

```text
A admitted, B waiting
A -> NodeFailure
SettleGraphNode(A failure)
  + A Failed
  + B admitted
B can execute and settle
no Pending -> AWAITING_RESUME
```

### 15.4 Ordinary exception

```text
A success command already applied
B raises RuntimeError
stop starting every not-yet-started Pending activation
drain already-started typed completions
quiesce session
FenceGraphExecution(exact token)
state keeps A Succeeded, B Pending
next claim executes only B
```

### 15.5 Final routing crash boundary

```text
last SettleGraphNode applied
state = RUNNING + SETTLED
crash
recover same state
ReadyToResolve
AdvanceGraphFrontier / CompleteGraphFrontier
```

### 15.6 Nested terminal completion

```text
parent has CompletedChild / AbortedChild projection
session validates child identity and terminal snapshot
precomputed completion enters the same completion source
next() -> SettleGraphNode(nested)
apply -> nested node settled and resources unchanged
next() -> ordinary selector, if any
```

## 16. 完成定义

本方案只有在以下条件全部满足时才算完成：

1. `GraphExecutor.execute` 不再等待整个 Frontier 后才返回唯一 batch command；
2. 每个 typed node completion 都能独立形成 `SettleGraphNode`；
3. single settlement reducer 原子更新 Frontier、resources 和 execution disposition；
4. resource release 后 waiter 在同一 next state 中 admitted；
5. newly admitted waiter 在最新 state 被确认的同一次 session step 立即进入同一 scheduler，即使已有 typed sibling completion 排队；queued ordinary error 则优先阻止新 activation；
6. production code 中没有 resource wave execution path；
7. final settlement 与 routing resolution 是两个明确 revisions；
8. recovered SETTLED state 可以独立 resolve；
9. Frontier、resources 和 execution token 没有重复 durable truth；
10. `GraphExecutionSession` 只能由 executor 线性消费 claim 后签发，单消费者 `next()` 与 close/cancel/quiescence 协议闭合，close 后无 live worker 才能 exact fence；
11. nested terminal projection 通过唯一 completion source 和 `SettleGraphNode` path，单 session 不重复交付且 crash 可 at-least-once 重投；
12. `CompleteGraphFrontier` 拒绝丢弃 non-empty `join_progress`；
13. `GraphRunState.resources` 对无 acquisition 只有 `None` 一种 durable 表达；
14. ordinary error 后不启动未启动 activation，并按 canonical task order 暴露 deterministic error；
15. selective resume、interrupt、skip、nested、routing、join、loop、fence 和 abort 的有效既有语义全部保留；
16. 所有仍成立的现有 tests 原样或等价保留，冲突 tests 已按新需求重写；
17. 没有通过删除测试文件、降低分支覆盖或只保留 happy paths 获得绿色结果；
18. 没有新增 legacy symbol/file/import absence 或字符串扫描门禁；
19. strict typing、lint、format、完整 tests、branch coverage、build、package check 和 monorepo pre-commit 全部通过；
20. 没有 Store、retry、Port idempotency、exactly-once、output persistence 或 multi-worker lease 等越界实现。

最终 authoritative 闭包为：

```text
authoritative GraphRunState
    -> prepare one atomic claim
    -> reducer applies claim
    -> explicit execution session selects runnable nodes
    -> one typed completion
    -> one SettleGraphNode
    -> reducer atomically settles + releases + admits
    -> repeat with returned/committed state
    -> stable SETTLED Frontier
    -> resolve_routing
    -> standalone AdvanceGraphFrontier / CompleteGraphFrontier
```

该闭包只包含一个 graph engine、一个 node settlement path、一个 resource truth 和一个 Frontier resolution barrier。

## 17. 实施与验证结果

### 17.1 实施结果

复审通过后已按本文完成 coordinated model replacement，最终 production code 只有一套 authoritative path：

1. `GraphExecutionLease` 已收敛为 token-only durable lease，resource snapshot 由 `ClaimGraphExecution` 在同一 claim revision 原子安装；
2. 每个 typed completion 由 `GraphExecutionSession.next(authoritative_state)` 单独投影为一个 `SettleGraphNode`；
3. `SettleGraphNode` reducer 在同一 revision 内记录 node settlement、释放该 node acquisition、推进 FIFO waiters，并更新 execution disposition；
4. waiter 被释放资源推进后立即出现在新的 authoritative state 中，session 只在调用方确认该 successor 后重新选择 runnable nodes；
5. 有资源、无资源和 precomputed nested terminal completion 共用同一个 scheduler/completion/settlement pipeline；
6. 最后一个 settlement 先形成可恢复的 `RUNNING + SETTLED` snapshot，routing 再单独生成 `AdvanceGraphFrontier` 或
   `CompleteGraphFrontier`；
7. `CompleteGraphFrontier` 保留 non-empty `join_progress` 防护，ordinary error、close/cancel/quiescence 和 exact fence 协议已经闭合；
8. 已删除 production batch collector、resource wave executor 和独立 resource transition module，没有保留 compatibility wrapper 或第二 runner；
9. 公开 `GraphExecutionSession` 已收敛为不可直接构造的协议；只有 `GraphExecutor.execute()` 在 owner-checked linear claim consumption 后通过一次性 receipt 签发 concrete session；
10. session 并发 `next()` 在进入 scheduler 前确定性拒绝，并发 `aclose()` 幂等串行清理；`next()` cancellation 的 close task 不会被 cleanup 期间的再次取消中断，也不会从同一 revision 交付第二条 command；
11. queued typed sibling 不会延迟最新 authoritative state 中刚 admitted 的 waiter；queued ordinary error 则先进入 `ERROR_DRAINING`，不越过错误启动新 activation；
12. 没有实现 Store、retry、Port idempotency、Graph exactly-once、output persistence 或 multi-worker lease，也没有新增 legacy 门禁测试。

### 17.2 测试迁移结果

历史 batch baseline 为 504 个 tests。实施初次收口的 422 passed 结论遗漏了逐 case 迁移证明，不能由 100% coverage 辩护；复核后已按
KEEP/MIGRATE/REPLACE 逐项补回；代码复审再以确定性交错反例补齐 linear session creation、concurrent `next()`、并发 close、queued typed
completion 即时调度、queued ordinary error 优先级与 repeated-cancel cleanup。终版重新收集并通过 522 项。旧 batch 专属断言没有原样复活，而是逐项改成节点级模型下的反向恢复保证。
补全后的恢复与并发边界包括：

1. claim executor owner、request attempt、exact token/resource successor acknowledgment 和 fence 后未消费 claim；
2. 并发 task 的 `ContextVar` 隔离、同一冻结请求输入和 canonical deterministic ordinary error；
3. nested child/grandchild identity、canonical entry、跨 superstep join，以及 nested/ordinary/resource mixed completion；
4. interrupt request payload 与 resume override 隔离、failure resume per-node input，以及 codec failure 的 quiescent state；
5. resource FIFO、multi-resource prefix、conditional routing、resource-free canonical `None` 和 typed failure 后 waiter 即时 admission；
6. partial settlement crash fence：已持久结算 sibling 保留，未结算 Pending activation 按 at-least-once 恢复；
7. child projection missing/duplicate/extra/noncanonical 与 run/step/parent/definition/version 坐标逐项 fail closed；
8. start identity/frontier/codec、interrupt 四坐标、fence/reclaim generation 与 failure reason 分项验证；
9. resource requirement drift、wrong order/stale participant、competing claim、exact participant revalidation、mixed failure/interrupt、override 与
   resource+nested completion；
10. later waiter ordinary error 的反向保证：此前已应用的 typed settlement 保留，只 fence 仍 Pending 的 activation。
11. public session protocol 不可直接构造，同一 prepared claim 的并发消费与一次性 construction receipt 都只有一个 session winner；
12. 一个 `next()` 等待 completion 时，第二个并发 `next()` 在 scheduler 前 fail closed，node invocation 与 command 均保持唯一；
13. active `next()` 与多个 close 并发时只执行一次 cancellation cleanup，不泄漏内部 scheduler race；
14. queued typed sibling 存在时，确认 owner settlement successor 的同一次 `next()` 在返回 sibling 前启动 newly admitted waiter；
15. queued ordinary error 存在时不启动该 waiter，session 先进入 deterministic `ERROR_DRAINING`；
16. queued typed completion 自身 projection 非法时走 ordinary error draining，不产生错误 settlement command。
17. `next()` cleanup 等待期间再次收到 cancellation 时，close task 不被中断，worker cleanup 完成后才向调用方传播 cancellation。

最终覆盖率结果为 2,105 statements、0 missed，676 branches、0 partial，即 statement/branch coverage 均为 100%。完整 lint、format、
strict typing、tests、coverage、build、package check、monorepo pre-commit 和 diff whitespace gate 均通过。
