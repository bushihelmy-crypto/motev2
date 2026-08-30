# Execution / State / Frontier 核心调用链

本文说明当前实现中 `Graph.run()` 如何驱动一个 Frontier，以及节点完成后如何通过
`GraphRunCommand` 和 pure reducer 逐次更新权威 `GraphRunState`。

## 1. 核心边界

```text
                         Execution owner
  +-------------------------------------------------------------+
  | Graph / CompiledGraph / GraphExecutor                       |
  |                                                             |
  | 读取 authoritative GraphRunState                            |
  |        + compiled topology                                  |
  |        + request / node result                              |
  |                         |                                   |
  |                         v                                   |
  |                构造 GraphRunCommand 实例                    |
  +-------------------------+-----------------------------------+
                            | typed transition boundary
                            v
                          State owner
  +-------------------------------------------------------------+
  | GraphRunState / GraphRunCommand / reduce_graph_run          |
  |                                                             |
  | reduce_graph_run(previous_state, command)                   |
  |                         |                                   |
  |                         v                                   |
  |                 candidate GraphRunState                     |
  +-------------------------+-----------------------------------+
                            | commit exact confirmation
                            v
                 Execution 才替换内存快照并继续
```

边界规则：

- `GraphRunState`、`GraphRunCommand` 及其转换语义都由 State 拥有。
- Execution 可以根据 `CompiledGraph` 创建 command，但不能直接修改 State。
- Reducer 不读取 `CompiledGraph`，只验证 state-owned 结构、生命周期、revision 和 token。
- Execution 依赖 State；State 不依赖 Execution。
- 持久化成功前，candidate 不能成为后续执行输入。

## 2. 顶层调用链

```text
Graph.run(...)
  |
  +-- Graph._compile()
  |     `-- GraphDefinition -> immutable CompiledGraph
  |
  +-- 新运行
  |     `-- StartGraphRun
  |           `-- commit_transition(...)
  |
  +-- 已有 State
  |     +-- validate_context(...)
  |     +-- 必要时 FenceGraphExecution
  |     `-- 必要时 ResumeGraphNodes
  |           `-- commit_transition(...)
  |
  `-- _GraphRun.drive_quantum()
        |
        `-- loop
              +-- StepRequest(authoritative state, frames, limits)
              `-- GraphExecutor.prepare(request)
                    `-- prepare_superstep(...)
```

`Graph` 门面不保存 run state。一次调用由 owner-local `_GraphRun` 持有当前 state 和
`ScopedFrameIndex`；每次权威提交确认后才替换其内存 state。

## 3. `GraphExecutor.prepare()` 的分流

```text
prepare_superstep(state, compiled_graph)
  |
  +-- state.status == COMPLETED ---------> CompletedGraph
  |
  +-- state.status == ABORTED -----------> AbortedGraph
  |
  +-- frontier == SETTLED
  |     `-- resolve_routing(...)
  |           `-- ReadyToResolve(
  |                 AdvanceGraphFrontier |
  |                 CompleteGraphFrontier |
  |                 AbortGraphRun)
  |
  +-- frontier == AWAITING_RESUME -------> AwaitingResume
  |
  +-- active execution lease ------------> 拒绝新 prepare；必须使用原 session
  |
  `-- frontier == EXECUTABLE
        |
        +-- prepare_frontier(...)
        |     +-- plan_tasks(...)
        |     +-- 校验 nested child projections
        |     +-- 投影 terminal child result
        |     +-- 区分 callable tasks / missing children / active children
        |     `-- 无待处理 child 时，每个 callable node 只物化一次 effective input
        |
        +-- missing / active child ------> WaitingForChildren
        |
        +-- claim_resource_snapshot(...)
        `-- prepare_claim(...)
              `-- ExecutableFrontier(
                    PreparedExecutionClaim + ClaimGraphExecution)
```

Frontier status 是从节点 settlement 派生的，不是第二份存储字段：

```text
存在 Pending                              -> EXECUTABLE
无 Pending，存在 Failed / Interrupted     -> AWAITING_RESUME
全部为 Succeeded / Skipped                -> SETTLED
```

## 4. 可执行 Frontier 的完整链路

```text
ExecutableFrontier
  |
  v
family_driver._execute_frontier(...)
  |
  +-- commit_transition(ClaimGraphExecution)
  |     `-- GraphRunState 安装 execution lease / resources
  |
  +-- GraphExecutor.issue_session(prepared_claim, claimed_state)
  |     +-- 校验 claimed State 是 claim command 的精确 reducer 后继
  |     +-- 校验 preparation 的完整 canonical GraphTask 序列
  |     +-- 线性消费 PreparedExecutionClaim（不重建 frontier）
  |     `-- issue_execution_session(...)
  |
  `-- family_driver._consume_session(...)
        |
        `-- loop: session.next(authoritative_state)
              |
              +-- acknowledge State 是上一条 settlement command 的精确 reducer 后继
              +-- select_executable_tasks(...)
              +-- 复用 prepare 阶段的 ExecutableTask / effective input
              +-- TaskScheduler.submit(...)
              |     `-- await node.operation(node_input)
              |
              +-- TaskSuccess | TaskFailure | TaskInterrupt
              |
              `-- settle_result(compiled_graph, state, result)
                    `-- SettleGraphNode
                          |
                          v
                    commit_transition(...)
                          |
                          v
                    reduce_graph_run(...)
                          |
                          v
                    confirmed GraphRunState
                          |
                          `-- _GraphRun 替换内存 state
                                `-- 下一次 session.next(new_state)
```

`GraphExecutor.prepare()` 只准备 disposition 和 claim，不执行节点，也不调用 reducer。
`GraphExecutor.issue_session()` 只验证精确 reducer 后继并签发由 claim 授权的 session，不拥有持久化或 State 变更。
普通 callable node 的唯一调用点是 `TaskScheduler`。

## 5. 并发执行、逐节点提交

节点可以并发运行，但 settlement 按 completion 逐条提交：

```text
                         同一个 Frontier
                 +-----------+-----------+
                 |           |           |
               Node A      Node B      Node C
                 |           |           |
                 +----- 并发执行 --------+
                             |
                    completion 到达队列
                             |
                   单消费者 session.next()
                             |
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
   SettleGraphNode(B) SettleGraphNode(A) SettleGraphNode(C)
          |                  |                  |
   commit revision N+1 commit revision N+2 commit revision N+3
```

每个 `SettleGraphNode` 都生成一个完整的新 `GraphRunState`，并原子完成：

```text
目标节点: Pending -> Succeeded | Failed | Interrupted
资源:     释放该节点 acquisition，并推进确定性 waiter
lease:    仍有 Pending 时保留；无 Pending 时清除
revision: +1
```

因此更新粒度是“逐节点 command”，权威快照粒度仍是“整个 Graph Run”。不存在独立可提交的
Node State。

`TaskSuccess.output` 不写入 `GraphRunState`。成功 settlement 确认后，Execution 才把 output
安装为 execution-local confirmed publication；Graph State 只保存 success routing 等控制事实。

## 6. Frontier 结算后的推进

最后一个节点 settlement 必须先形成稳定的 `RUNNING + SETTLED` State，然后才能解析 routing：

```text
最后一个 SettleGraphNode
  |
  `-- commit -> GraphRunState(RUNNING, frontier=SETTLED)
                         |
                         v
                下一轮 prepare_superstep
                         |
                         v
                  resolve_routing(...)
      persisted routing contributions + join progress
      + CompiledGraph topology + confirmed frame availability
                         |
          +--------------+----------------+
          |              |                |
          v              v                v
 AdvanceGraphFrontier CompleteGraphFrontier AbortGraphRun
          |              |                |
          `--------------+----------------+
                         |
                  commit_transition
                         |
                  reduce_graph_run
```

三种结果：

- `AdvanceGraphFrontier`：`superstep + 1`，创建下一组 `Pending` 节点，然后回到 prepare loop。
- `CompleteGraphFrontier`：进入 `COMPLETED`，使用 canonical empty Frontier。
- `AbortGraphRun`：进入 `ABORTED`，保留允许的诊断事实并停止执行。

Routing 由 Execution 根据 compiled topology 计算；Reducer 只应用并验证 command 的
state-owned 结构。Settlement 与 routing 拆成两次提交，保证崩溃后可以从 `SETTLED` 屏障恢复。

## 7. Resume 与异常分支

```text
AWAITING_RESUME
  |
  +-- Graph.resume_failed / resume_interrupted / skip_failed
  |     `-- plan_resumes(...) -> PlannedResume(successor)
  |           `-- admit_continued_root(...)
  |                 `-- _GraphRun.apply_admission_resume(...)
  |                       `-- commit_transition(admitted_successor=successor)
  |                             `-- 唯一一次 reduce_graph_run
  |                                   +-- 恢复节点变回 Pending
  |                                   `-- skip 节点变为 Skipped
  |
  `-- 回到 _GraphRun.drive_quantum / prepare_superstep
```

```text
session 观察到 ordinary exception / infrastructure error
  |
  +-- 不伪造 Failed 或 Interrupted settlement
  +-- session.aclose()，等待 live tasks 停止
  `-- FenceGraphExecution(exact token)
        `-- commit_transition -> 清 execution lease / resources

session.next() 被取消
  |
  +-- cancellation-safe session close
  `-- 向调用方传播 cancellation，保留 authoritative active lease
        `-- 后续 recovery 确认旧 attempt 已停止后再 exact fence
```

已有 active lease 的 recovered State 必须先通过 exact fence，之后才能重新 claim。

## 8. 唯一权威 reducer / commit 路径

权威状态推进集中在 `family_driver.commit_transition()`：

```text
previous_state + GraphRunCommand
  |
  +-- candidate = reduce_graph_run(previous_state, command)
  |
  +-- GraphTransition(
  |     previous_state,
  |     command,
  |     candidate_state,
  |     optional typed node result)
  |
  +-- 没有 commit callback ------> candidate 作为进程内确认结果
  |
  `-- 有 commit callback
        +-- 外部原子提交
        `-- 必须返回 exact candidate
              |
              +-- 相等：Execution 继续
              `-- 不相等：SnapshotMismatchError，停止执行
```

其他模块可以调用 reducer 做 recovery/resume 的预演或证明，但不能据此替换权威内存快照；真正的
authoritative transition、commit 和确认都经过 `commit_transition()`。

## 9. 关键对象与所有者

| 对象 | 所有者 | 职责 |
| --- | --- | --- |
| `Graph` | Execution | 唯一公开构图与运行门面 |
| `CompiledGraph` | Execution | immutable topology、端口、routing 和 resource requirements |
| `GraphExecutor` | Execution | prepare、精确 claim 后继校验和 session 签发 |
| `GraphExecutionSession` | Execution | 单消费者 completion/ack 协议和 transient task lifecycle |
| `TaskScheduler` | Execution | 唯一普通节点 invocation owner |
| `_GraphRun` / `ScopedFrameIndex` | Execution | owner-local State binding 与 concrete value availability |
| `GraphRunState` | State | 整个 Graph Run 的可恢复控制快照 |
| `GraphRunCommand` | State | 封闭的状态转换输入 union |
| `reduce_graph_run` | State | pure transition、invariant 校验与 revision 推进 |
| `GraphTransition` / commit callback | Execution 边界 | 暴露 reducer candidate 并要求精确提交确认 |

## 10. 代码导航

- Public 入口：[`execution/facade.py`](../src/mote_kernel/execution/facade.py)
- Graph-family 驱动与提交：[`execution/family_driver.py`](../src/mote_kernel/execution/family_driver.py)
- Executor：[`execution/executor.py`](../src/mote_kernel/execution/executor.py)
- Frontier prepare：[`execution/engine/frontier.py`](../src/mote_kernel/execution/engine/frontier.py)
- Superstep 分流：[`execution/engine/superstep.py`](../src/mote_kernel/execution/engine/superstep.py)
- Session：[`execution/engine/session.py`](../src/mote_kernel/execution/engine/session.py)
- Scheduler：[`execution/engine/scheduler.py`](../src/mote_kernel/execution/engine/scheduler.py)
- Settlement 投影：[`execution/engine/settlement.py`](../src/mote_kernel/execution/engine/settlement.py)
- Routing：[`execution/engine/routing.py`](../src/mote_kernel/execution/engine/routing.py)
- State model：[`state/graph_state/model.py`](../src/mote_kernel/state/graph_state/model.py)
- State commands：[`state/graph_state/command.py`](../src/mote_kernel/state/graph_state/command.py)
- Pure reducer：[`state/graph_state/reducer.py`](../src/mote_kernel/state/graph_state/reducer.py)
