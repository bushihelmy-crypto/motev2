# Execution 包实现说明

`execution` 是 Kernel 唯一的通用图运行底座。它实现图的静态结构与纯运行算法，但不拥有 Agent 的权威状态、领域语义、持久化、外部副作用或失败重试策略。

## 核心运行模型

一次 superstep 固定遵循：

~~~text
ExecutionSnapshot
  -> plan ready tasks
  -> execute each task once against the same committed snapshot
  -> collect results in stable TaskId order
  -> produce ExecutionTransition
~~~

`ExecutionTransition` 只是等待应用的运行结果。它不能直接修改 `GraphState` 或 `DomainState`；上层将其转换为 typed state commands，并通过根状态机原子提交完整 `AgentState`。

## graph/

### `graph/node.py`

- 定义稳定的 `NodeId` 和通用 Node Protocol。
- 定义一次节点调用所需的类型化输入与一次性结果边界。
- 允许节点绑定下层 `GraphDefinition`；Observe、Think、Act 等组件图仍使用同一种 GraphRun 语义。
- 节点读取同一份已提交快照的只读投影，不直接修改状态。
- 不提供节点级 retry、可变运行上下文或持久化能力。

### `graph/edge.py`

- 定义顺序边、条件边和多源 join 边。
- 边只描述静态拓扑关系，不执行条件、不推进状态。
- 循环由合法的有向边表达，不创建第二种循环执行器。

### `graph/definition.py`

- 定义不可变、版本化的 `GraphDefinition`。
- 保存 definition identity、版本、节点、边、入口和出口声明。
- Definition 是编译输入，不包含某次运行的 cursor、frontier 或 task。

### `graph/command.py`

- 定义节点可发出的执行控制命令：`Goto`、`Send`、`Suspend`、`Complete`、`Fail`。
- 命令只表达期望的下一步，不直接修改 GraphState。
- `Send` 必须携带稳定 identity，并受 fan-out 限制。

### `graph/topology.py`

- 定义不可变的 `CompiledGraph`/`Topology`。
- 保存节点索引、出边/入边索引、入口、出口、join 依赖和路由索引。
- Planner 只读取已编译拓扑，不在运行期间扫描或修改 Definition。

### `graph/compiler.py`

- 将 `GraphDefinition` 编译为不可变拓扑。
- 调用静态校验并构建运行期索引。
- 编译结果必须确定：相同 Definition 始终产生等价拓扑。

### `graph/validation.py`

- 校验节点与边引用、入口、出口、可达性、join 和循环结构。
- 校验条件路由目标、子图声明和保留 identity。
- 发现歧义或非法结构时 fail closed，返回 typed graph errors。

## engine/

### `engine/task.py`

- 定义 `GraphTask`、`TaskId`、任务来源和稳定排序键。
- Task identity 来自 graph run、superstep、node、静态或动态路径。
- 嵌套图调用通过 parent run、parent task 和 child run identity 表达，不定义特殊 Subgraph Task。
- 不把能力级 failover attempt 或 retry budget 放进 GraphTask。

### `engine/planner.py`

- 根据 `ExecutionSnapshot` 和 `CompiledGraph` 计算当前 ready tasks。
- 处理 frontier、join 满足条件、pending sends 和完成状态。
- 规划是纯函数；不得执行节点、访问 StateStore 或修改快照。

### `engine/scheduler.py`

- 在并行度限制内执行本轮 GraphTask。
- 所有任务读取同一份已提交输入快照。
- 每个任务只调用一次；失败直接成为 typed result，不自动重试整个 Node。
- 不包含退避、目标切换、Provider fallback 或副作用对账。

### `engine/collector.py`

- 收集同一 superstep 的节点结果并按稳定 TaskId 排序。
- 检测重复结果、不兼容控制命令和不可确定的状态冲突。
- 协程完成顺序不得影响最终归并结果。

### `engine/routing.py`

- 解释条件边、`Goto` 和动态 `Send` 的路由含义。
- 校验目标属于已编译拓扑，并生成确定的下一 frontier/pending sends。
- 强制 fan-out、动态任务数量和路由目标限制。

### `engine/superstep.py`

- 编排一次 `plan -> execute -> collect -> transition`。
- 组合 Planner、Scheduler、Collector 和 Routing 的纯结果。
- 不提交 StateStore，不持有权威状态，不决定领域转换是否合法。

### `engine/interrupt.py`

- 定义 `Suspend` 和 `Resume` 的通用图运行语义。
- 使用稳定 interrupt identity 和类型化 resume payload。
- Resume 产生新的执行转换；不得恢复 Python coroutine、Task、锁或调用栈。
- 这是 Workflow 的必备能力：模型定义的图可持久等待审批、外部输入、Operation 结果或其他唤醒事实。
- 下层 GraphRun 暂停时，暂停沿通用 GraphRun 调用关系向上返回；恢复后继续同一个 child run。
- 本模块负责暂停与恢复的合法性；暂停事实由 GraphState 保存，可靠唤醒与再次调用由 Kernel 外部 Runtime 承担。

## execution 根模块

### `snapshot.py`

- 定义 Planner 和节点运行所需的只读执行投影 `ExecutionSnapshot`。
- 包含 graph run、definition、superstep、frontier、pending sends、join、interrupt 等执行事实。
- GraphRun 可携带 parent run/task identity；每层运行拥有独立 frontier，但不形成第二套子图状态模型。
- 它不是权威 `GraphState`，也不能包含领域事实或 StateStore。

### `transition.py`

- 定义一次执行产生的 `ExecutionTransition`。
- 表达 task completion、下一 frontier、send、suspend、complete 或 fail。
- 由上层适配为 state-owned typed commands，再交给根状态机处理。

### `request.py`

- 定义 `StepRequest` 和 `RunRequest`。
- 请求显式携带编译图、只读快照、节点输入和限制。
- 不隐藏持久化 handle、Runtime service locator 或领域对象。

### `result.py`

- 定义 `Prepared`、`Executed`、`Suspended`、`Completed`、`Failed` 等执行结果。
- 结果描述图运行状态，不将异常文本作为稳定协议。
- 能力错误分类和 failover decision 不属于这里。

### `executor.py`

- 提供执行单个 superstep 或连续推进的无状态驱动入口。
- 组合 request、superstep 和 result，但不拥有 AgentState。
- 以同一种 GraphRun 机制递归驱动节点绑定的下层图，并回传 typed result。
- 不读取或写入 StateStore，不执行 CAS，不更新内存权威快照。

### `limits.py`

- 定义最大 superstep、并行度、fan-out、动态任务数、子图深度等限制。
- 限制必须显式注入并确定性执行。
- 不定义 failover attempt budget；该预算属于 `failover/`。

### `errors.py`

- 定义图结构、编译、路由、归并冲突和运行限制等 typed errors。
- 不收纳 Provider、Tool、存储或领域错误。
- 不使用不稳定异常文本作为跨边界语义。

## 明确不属于 execution

- Node 整体 retry、retry policy 和 graph error-handler node。
- 能力级重试、目标切换、退避和对账；这些由 `failover/` 拥有。
- `GraphState`、`DomainState` 及其 reducer；这些由 `state/` 拥有。
- StateStore、checkpoint、CAS、fencing 和持久化实现。
- 模型、工具、Prompt、Operation 等领域语义与具体 Port。
- 任何外部副作用是否已经发生的判断。

## 建议实现顺序

1. `node.py`、`edge.py`、`definition.py`。
2. `validation.py`、`topology.py`、`compiler.py`。
3. `snapshot.py`、`task.py`、`planner.py`。
4. `collector.py`、`transition.py`、`superstep.py`。
5. `interrupt.py`，完成 Workflow 必需的持久暂停与恢复。
6. `routing.py` 和动态 `Send`。
7. `scheduler.py` 的确定性有界并行。
8. `request.py`、`result.py`、`executor.py` 的连续驱动与嵌套 GraphRun 入口。
