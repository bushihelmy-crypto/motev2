# Graph examples / 图示例

These runnable examples use only `mote_kernel.execution.Graph`, the public graph composition and execution facade.
The node callables keep graph state explicit: execution decisions and recoverable values travel through typed graph
inputs, outcomes, state, and resume actions. The modules are grouped from basic topology through recovery and
operational boundaries, so a reader can start with one small graph and then choose a production pattern.

这些可运行示例只使用公开门面 `mote_kernel.execution.Graph`。图的执行决定与可恢复值都通过有类型的 graph input、
outcome、state 和 resume action 显式传递。示例从基础拓扑逐步覆盖恢复和运行边界，读者可以先运行一个小图，再按需求选择生产方案。

| Module | Scenario |
| --- | --- |
| `linear_treasure_hunt` | Direct linear activation / 线性直接激活 |
| `conditional_mood_radio` | Conditional route followed by a shared continuation / 条件分支后汇入统一后继 |
| `parallel_detectives` | Parallel fan-out and join / 并行扇出与汇合 |
| `fanout_terminal` | Direct fan-out, input projection, and join-to-END / 直接扇出、输入投影与 Join 结束 |
| `nested_space_mission` | Parent and independently owned child graph runs / 父子图各自拥有运行上下文 |
| `nested_batch_review` | Reused child graph, scoped interrupts, and batch resume / 子图复用、作用域中断与批量恢复 |
| `polling_loop` | Explicit `START`, self-loop, and conditional exit / 显式 `START`、自循环与条件退出 |
| `concurrent_runs` | Multiple independent runs on one graph instance / 同一 graph 实例上的独立并行 run |
| `human_in_the_loop` | Interrupt, graph reassembly, and state-only resume / 中断、重新装配与仅凭状态恢复 |
| `resource_customer_report` | Parallel reads, an exclusive resource, and a join / 并行读取、独占资源与汇合 |
| `checkpointed_import` | Commit callback and state-only restart / commit 回调与仅凭状态重启 |
| `bounded_execution` | Superstep budget and fail-closed retry / superstep 预算与安全停止后重试 |
| `partial_commit_recovery` | Partial commit handoff and scoped retry / 部分提交交接与作用域重试 |
| `cancellation_abort` | Caller cancellation and `AbortedResult` / 调用方取消与 `AbortedResult` |
| `versioned_deployment` | Versioned definitions and snapshot rejection / 版本化定义与快照拒绝 |

## 方案选择 / Choosing a pattern

| 需求 / Need | 示例 / Example | 关键 API / API to study |
| --- | --- | --- |
| 固定步骤串联 / Fixed sequence | `linear_treasure_hunt` | `add_edge`, `node_output` |
| 按结果选择路径 / Select a route | `conditional_mood_radio` | `Graph.success(..., route=...)`, `add_conditional_edge` |
| 同时取数后汇总 / Fan-out then aggregate | `parallel_detectives` | `add_join` |
| 扇出后直接结束 / Fan-out then finish | `fanout_terminal` | multiple `add_edge`, `add_join(..., Graph.END)` |
| 循环轮询 / Poll until an answer | `polling_loop` | `Graph.START`, back-edge, `resume_interrupted` |
| 封装可复用子流程 / Encapsulate a subflow | `nested_space_mission` | Nested `Graph` node |
| 多个子流程共享定义 / Reuse one child definition | `nested_batch_review` | same child graph, `scope=(...)` |
| 并行处理多个请求 / Serve concurrent requests | `concurrent_runs` | `asyncio.gather`, independent run state |
| 等待人工决定 / Wait for a human | `human_in_the_loop` | `Graph.interrupt`, `resume_interrupted` |
| 并发访问共享能力 / Limit a shared capability | `resource_customer_report` | `resources=(...)`, `max_parallel_tasks` |
| 每次推进都落检查点 / Checkpoint every transition | `checkpointed_import` | `commit=...`, state-only `run(state=...)` |
| 保护长流程预算 / Bound a long run | `bounded_execution` | `max_supersteps`, `Graph.ExecutionLimitError` |
| 提交只确认了前缀 / Commit confirms a prefix | `partial_commit_recovery` | `Graph.PartialCommitError` |
| 主动停止运行 / Stop from the caller | `cancellation_abort` | task cancellation, `Graph.AbortedResult` |
| 变更拓扑并安全部署 / Deploy a changed topology | `versioned_deployment` | `version=...`, `Graph.SnapshotMismatchError` |

## 最小模板 / Minimal recipe

Every ordinary graph follows the same five declarations: create a typed input reference, add callable nodes with
named inputs and output types, connect control edges, project graph outputs, then call `run()`.

普通 graph 都遵循同一个五步模板：声明 typed graph input，用具名 inputs/outputs 添加 callable node，连接 control edge，
投影 graph output，最后调用 `run()`。

```python
from mote_kernel.execution import Graph


async def work(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(result=values["request"].upper())


graph = Graph[str]("example.minimal")
request = Graph.graph_input("request", str)
graph.add_node("work", work, inputs={"request": request}, outputs={"result": str})
graph.add_edge("work", Graph.END)
graph.set_outputs({"result": Graph.node_output("work", "result")})

result = await graph.run(Graph.values(request="hello"), run_id="minimal-run")
if isinstance(result, Graph.CompletedResult):
    print(result.outputs["result"])
```

`Graph.node_output()` 只声明数据依赖，不会自动激活 consumer；consumer 仍需 `add_edge`、conditional edge 或
`add_join`。没有 incoming control edge 的 graph-input-only node 会自动成为 root。`set_outputs()` 只做结果投影，
不会启动任何 node。

`Graph.node_output()` declares only a data dependency; it does not activate the consumer. The consumer still needs a
direct edge, conditional edge, or join. A node that only reads graph inputs becomes an automatic root when it has no
incoming control edge. `set_outputs()` projects the result and never starts a node.

## 恢复动作 / Resume actions

An interrupt returns an `AwaitingResumeResult`. Persist its `state` through the commit port, then answer the exact
interrupt identity. A typed node failure is different: it durably terminates the graph and returns `FailedResult`;
retry policy belongs in an explicit graph topology around the protected port, not in a hidden executor resume path.

中断会返回 `AwaitingResumeResult`。通过 commit port 持久化 `state`，再用精确的 interrupt identity 回答。类型化 node failure
则不同：它会持久化终止 graph 并返回 `FailedResult`；重试策略应由包裹目标 port 的显式 graph 拓扑表达，而不是藏在执行器恢复分支里。

| Situation | Action | Example |
| --- | --- | --- |
| 等待人工回答 / human answer | `resume_interrupted(node, interrupt_id, values)` | `human_in_the_loop` |
| 子图内 node / nested node | add `scope=("child", ...)` | `nested_batch_review` |

Use `continuation=result.continuation` when the in-memory frame evidence is needed (transient continuation). A
state-only invocation is appropriate when the required values are already durable or supplied by an override action.
For nested in-flight children, the opaque continuation carries child state bindings, as shown in
`nested_batch_review`.

需要内存 frame evidence 时传入 `continuation=result.continuation`（transient continuation）；如果所需值已经持久化，
或由 override action 提供，则可以只传 `state`。嵌套子图尚在执行时，opaque continuation 还携带 child state binding，
`nested_batch_review` 展示了这一点。

## 结果与运行边界 / Results and run boundaries

| Result or error | Meaning | Example |
| --- | --- | --- |
| `CompletedResult` | 所有 terminal gate 已完成 / all terminal gates completed | most modules |
| `AwaitingResumeResult` | interrupt 正在等待精确回答 / an interrupt awaits an exact answer | `human_in_the_loop` |
| `FailedResult` | failure 已持久化终止 / a failure durably terminated the run | focused contract tests |
| `AbortedResult` | authoritative state 已终止，不会继续执行 / state is terminally aborted | `cancellation_abort` |
| `ExecutionLimitError` | 本次 invocation 预算耗尽 / invocation budget exhausted | `bounded_execution` |
| `PartialCommitError` | 只有部分 scope 被 commit 确认 / only a prefix was confirmed | `partial_commit_recovery` |

One compiled graph can serve independent runs concurrently. `run_id` is caller-owned for durable correlation; omit it
only when an automatically generated identity is sufficient. A graph definition becomes immutable after successful
compilation, so build a new versioned definition when the topology or port contract changes.

同一张已编译 graph 可以并行服务多个独立 run。`run_id` 通常由调用方提供以便持久关联；不关心外部关联时可以省略并由门面生成。
graph 首次成功编译后定义不可变；拓扑或 port contract 变化时应构建新的 version。

When deploying a changed topology, increment the constructor version and migrate or restart the run explicitly. The
facade rejects an old state with `Graph.SnapshotMismatchError`; malformed topology and port declarations fail as
`Graph.ValidationError` before any node is called.

变更拓扑部署时应递增构造器的 `version`，并显式迁移或重启 run。门面会用 `Graph.SnapshotMismatchError` 拒绝旧 state；
非法拓扑和 port 声明会在任何 node 调用前以 `Graph.ValidationError` 失败。

The recovery examples deliberately use frozen records and deterministic inputs. This makes it clear which values must
be persisted, and avoids teaching a process-local retry counter that would disappear when a worker restarts.

恢复示例都使用 frozen record 和确定性输入，明确展示需要持久化的值；不会用进程内重试计数器伪装成可恢复状态。

Run a module from the Kernel repository root, for example:

```bash
python -m example.graph.linear_treasure_hunt
python -m example.graph.conditional_mood_radio
python -m example.graph.parallel_detectives
python -m example.graph.fanout_terminal
python -m example.graph.nested_space_mission
python -m example.graph.nested_batch_review
python -m example.graph.polling_loop
python -m example.graph.concurrent_runs
python -m example.graph.human_in_the_loop
python -m example.graph.resource_customer_report
python -m example.graph.checkpointed_import
python -m example.graph.bounded_execution
python -m example.graph.partial_commit_recovery
python -m example.graph.cancellation_abort
python -m example.graph.versioned_deployment
```

The caller supplies only the root `run_id`. Nested child run identities are internal to their child owners.

`resource_customer_report` shows that two nodes can share the exclusive `customer-db` resource while an unrelated
cache read remains eligible to run. The `checkpointed_import` store is an in-memory teaching adapter; replace it with
an atomic database transaction in a real application.

`resource_customer_report` 展示两个节点共享独占的 `customer-db`，而无关的缓存读取仍可并发执行。
`checkpointed_import` 中的 store 只是便于运行的内存教学适配器；实际应用应替换为原子数据库事务。
`bounded_execution` 先用过小的 `max_supersteps` 安全停止，再用新的 run ID 和足够预算重新运行。
`partial_commit_recovery` 和 `cancellation_abort` 是两个运行边界示例：分别展示部分确认交接和调用方取消后的终止状态。
`versioned_deployment` 展示拓扑升级时递增 `version`，旧 state 会被拒绝，随后以新 run 显式启动。

`partial_commit_recovery` and `cancellation_abort` cover the two operational handoffs that are easiest to miss in a
first integration. `versioned_deployment` shows the explicit version boundary for a topology change.

## 覆盖边界 / Coverage boundary

This cookbook now has at least one runnable module for each public happy-path family: topology, success and interrupt
outcomes, exact interrupt resume, nested scopes, concurrent invocations, commit checkpoints, limits, cancellation,
and partial handoff. It is
still not an exhaustive Cartesian product of every graph shape and failure ordering.

这里已经为公开 API 的主要正常路径各提供了至少一个可运行模块：拓扑、success/interrupt outcome、精确 interrupt resume、
嵌套作用域、并行 run、commit 检查点、预算、取消和部分交接。但它仍不是所有图形状与故障顺序的笛卡尔积。

The focused tests intentionally retain malformed declarations, stale interrupt IDs, codec corruption, snapshot/version
mismatches, active-lease fencing, terminal failures, node-origin cancellation, cleanup failures, and continuation
tampering. Those cases teach fail-closed contracts rather than application topology.

以下内容继续放在 focused tests 中：非法声明、过期 interrupt ID、编解码损坏、snapshot/version mismatch、active lease
接管、terminal failure、node-origin cancellation、cleanup 故障和 continuation 篡改。这些是 fail-closed contract，
不是业务拓扑。

For a nested in-flight recovery, keep the opaque continuation together with the state. A state-only call cannot invent
child run bindings or historical frames that were not durably recorded; `nested_batch_review` makes this requirement
visible by passing both values.

嵌套子图尚在运行时恢复，应将 opaque continuation 与 state 一起保存。仅凭 state 无法凭空恢复未持久化的 child run binding
或历史 frame；`nested_batch_review` 明确传入了两者。

The contract-level assertions live in `tests/execution/test_graph_api.py`, `tests/execution/test_interrupt_flow.py`,
`tests/execution/test_graph_recovery_contract.py`, and `tests/execution/test_family_driver_local_ownership.py`.

契约级断言位于 `tests/execution/test_graph_api.py`、`tests/execution/test_interrupt_flow.py`、
`tests/execution/test_graph_recovery_contract.py` 和 `tests/execution/test_family_driver_local_ownership.py`。
