# Frontier 节点失败恢复需求评审

## 1. 评审信息

- 评审对象：`docs/frontier-node-resume-requirements.zh-CN.md`
- 评审日期：2026-08-13
- 评审范围：基于当前 GraphState 与唯一 graph execution engine 实现 frontier resume
- 评审结论：通过，按本文约束实施

## 2. 总体结论

需求的核心方案合理，与当前架构约束没有结构性冲突。

本次变更是在现有 `GraphRunState` 中增加当前 frontier 的 node settlement，使节点失败成为当前 superstep 的局部阻塞事实，并允许通过显式 resume 只重新执行失败节点。它不是动态修改静态 `GraphDefinition`，也不需要增加新的 runner、node-level lease 或持久化系统。

实现应保持与当前 GraphState 相同的能力边界：定义 typed state、pure transition、execution projection 和确定性行为测试。不要把本需求扩展为新的 durable store、journal、泛型 output 持久化或跨进程业务结果恢复项目。

## 3. 核心模型

### 3.1 静态节点与动态 activation 分离

`GraphDefinition` 和 `NodeId` 继续描述静态拓扑。运行时结算状态属于特定 node activation：

```text
(run_id, superstep, node_id)
```

同一个 `NodeId` 在新的 superstep 中是新的 activation，必须重新初始化为 Pending。同一个 activation 被 resume 时，superstep 和 task identity 不变，只产生新的 execution attempt。

因此，在 frontier 中增加动态 node settlement 不会污染静态图定义。

### 3.2 Frontier 仍是完整 superstep 边界

Frontier 必须继续表示当前 superstep 的完整激活集合，而不是某次 lease 实际领取的任务子集。

建议模型：

```python
@dataclass(frozen=True, slots=True)
class GraphFrontierState:
    nodes: tuple[GraphFrontierNode, ...]


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement


GraphNodeSettlement: TypeAlias = (
    PendingGraphNode
    | SucceededGraphNode
    | FailedGraphNode
)
```

Frontier 状态从 settlement 派生：

```text
存在 Failed                         -> BLOCKED
不存在 Failed 且存在 Pending         -> EXECUTABLE
全部 Succeeded                      -> SETTLED
```

不得再持久化一份可以与 node settlement 冲突的 frontier status。

### 3.3 成功节点只需保存 routing contribution

成功 sibling 在 resume 时不重新执行，因此 GraphState 需要保存其 routing contribution：

```text
ContinueGraphRouting | SelectGraphRoute
```

Routing contribution 是恢复控制流所需的 GraphState 事实。当前需求不要求把泛型 `NodeSuccess.output` 保存到 GraphState，也不要求新增 result store、journal 或 DomainState 持久化协议。

当原始 frontier 的全部节点最终成功后，routing engine 使用以下输入统一计算下一 frontier：

```text
此前 attempt 保存的成功 contribution
+ 本次 attempt 新产生的成功 contribution
+ 当前 prior join progress
```

Blocked 时不得提前应用 routing 或 join arrival。

## 4. 状态转换要求

### 4.1 Claim

继续复用现有 batch execution lease。Planner 只为当前 Pending 节点生成任务：

- 首次执行通常领取整个 frontier；
- resume 后只领取由 Failed 转回 Pending 的节点；
- Succeeded 和 Failed 节点不能被 claim；
- claim 继续产生新的 execution generation 和 attempt identity。

Lease 中只能有一套 authoritative task identity。建议只保存 claimed `node_ids`，task ID 继续由以下坐标派生：

```text
(run_id, superstep, node_id)
```

不得同时保留旧 `task_ids` 与新 `node_ids/tasks` 作为兼容路径。

### 4.2 Settlement

一次 lease 的 outcomes 必须原子结算，并满足：

- 精确匹配当前 execution token；
- 完整且唯一覆盖 lease 中的节点；
- 只能更新本次 claimed 的 Pending 节点；
- Succeeded 节点不可被覆盖或降级；
- 保存所有 success contribution 和所有 failure；
- 清除本次 lease 和 resource scheduler state。

当前 collector 在出现 failure 后丢弃所有 success，并只保留第一个 failure。该行为必须删除。

结算后：

- 存在 Failed：GraphRun 保持 RUNNING，frontier 为 BLOCKED，superstep 和 join progress 不变；
- 全部 Succeeded：统一 routing，随后 advance 或 complete。

### 4.3 Resume

增加显式纯转换：

```text
Succeeded -> Succeeded
Failed    -> Pending
```

Resume 必须：

- 只接受 RUNNING + BLOCKED frontier；
- 要求没有 active lease 和未清理 resource admission；
- 使用现有 `expected_revision` CAS；
- 保持 frontier 节点集合、superstep、成功 contribution 和 join progress 不变；
- 增加 revision，使旧 claim、admission 和 settlement 失效；
- 不自动发生，不在 Kernel 内增加 retry policy。

### 4.4 Abort

Operator abort 必须与 node failure 分开：

- node failure 形成 RUNNING + BLOCKED frontier；
- operator abort 形成不可 resume 的 ABORTED 终态。

本次需求只要求消除二者共用 `FAILED` 的旧语义。更完整的 termination diagnostic record、跨进程终止历史等设计不作为 resume 实现的前置条件。

## 5. 校验责任边界

GraphState 不持有 `CompiledGraph`，不得为了复算 topology 依赖 execution 包。

| Owner | 责任 |
| --- | --- |
| GraphState reducer | revision、lease token、claimed node 覆盖、Pending-only 更新、settlement 结构、canonical order、资源与 lease 清理、生命周期转换 |
| Execution projection/guard | definition/version 匹配、frontier node 属于 compiled graph、task identity 派生正确、routing contribution 对 node 合法 |
| Routing engine | 根据完整 contribution 和 join progress 计算 direct edge、conditional route、join 与下一 frontier |

Reducer 不应盲目信任 execution command，但也不应越过 owner 边界自行解释静态 topology。

## 6. 现有基础设施复用

以下设施应原位复用：

| 基础设施 | Resume 中的用途 |
| --- | --- |
| GraphRun revision CAS | fence stale resume、claim、admission 和 settlement |
| Execution generation/token | fence 旧 lease 与迟到结果 |
| Stable task identity | 同一 activation resume 时保持 task identity |
| Batch execution lease | 领取当前 Pending subset |
| Pure GraphState reducer | settlement、resume、abort 状态转换 |
| Execution projection/guard | 校验 recovered state 与 compiled graph |
| Routing engine | 合并跨 attempt routing contribution |
| Join progress | blocked 时保持不变，settled 后统一更新 |
| Resource admission | resume 后仅为 Pending 节点重新 admission |
| Interrupt lifecycle | blocked 时不消费 resolution，成功推进后按现有规则消费 |
| Deterministic child run identity | nested child blocked 时复用原 child run |

不创建第二套 execution path。

## 7. Nested graph 范围

Nested child blocked 时：

- child GraphRun 保持 RUNNING；
- parent nested node 保持 Pending；
- parent 不得把 child blocked 当作 terminal failure；
- 必须复用原 deterministic child run identity；
- child 完成后 parent 才提交成功 outcome。

实现可以在现有 typed request/result 边界内补充区分 missing、active、completed 和 aborted child 所需的类型。当前需求不要求新增 state-store lookup port，也不要求设计 authoritative store 装配。

## 8. Legacy 清理

本次变更是现有模型的替换，不保留双 authoritative path。完成时应删除或替换：

- `GraphRunStatus.FAILED`；
- node failure 使用的顶层 `GraphRunState.failure`；
- `FailGraphExecution`；
- execution-owned `FailTransition`；
- `ExecutionStatus.FAILED`；
- node failure 清空 frontier 和 join progress 的旧转换；
- collector 丢弃 success、只保留第一个 failure 的行为；
- nested child 只用 FAILED/COMPLETED 二分终态的判断；
- flat `tuple[GraphNodeId, ...]` frontier 的旧运行路径；
- lease 的旧 `task_ids` 与新 identity 并存路径。

最终 production code、public exports 和 reducer dispatch 中不得保留兼容别名、fallback 或第二条可运行路径。迁移开发过程中的临时代码形态不属于架构合同；可合入版本只有一个 authoritative model 即可。

## 9. 明确非目标

本需求不包括：

- durable input binding 新协议；
- 泛型 node output 持久化；
- result store 或 append-only journal；
- GraphState/DomainState 的真实存储事务实现；
- authoritative state-store port；
- 跨进程完整业务结果恢复承诺；
- node-level lease；
- 多 worker 拆分同一 frontier；
- 自动 retry、backoff 或最大重试策略；
- 默认公共 composition entry point；
- 没有跨语言 consumer 的占位 conformance schema。

输入继续遵循当前 `StepRequest` 和 interrupt resolution 的既有语义。普通输入在多次调用间保持一致由现有调用方合同负责，不阻塞本次 GraphState resume。

## 10. 验收标准

至少覆盖：

1. 单节点失败后可显式 resume 并完成。
2. 并行 frontier 部分成功、部分失败时保存全部 settlement。
3. Resume 后只重新执行原失败节点。
4. 多个失败节点在同一新 batch lease 中执行。
5. Resume 后再次失败可再次进入 BLOCKED。
6. 已成功节点的 conditional route 在 resume 后保持不变。
7. Direct edge 和 join contribution 不丢失、不重复应用。
8. Resume 不增加 superstep；推进到下一 frontier 才增加。
9. 循环或自环进入新 superstep 时创建新的 Pending activation。
10. 同一 activation 的 task identity 不变，execution generation 改变。
11. 旧 revision、旧 lease、旧 admission 和迟到结果被拒绝。
12. Resource resume 仅重新 admission Pending 节点。
13. Resolved interrupt 在 blocked/resume 期间不被消费，成功推进后只消费一次。
14. Nested child blocked 时不重复创建 child run。
15. Operator abort 产生 ABORTED 且不可 resume。
16. 非法 recovered settlement、lease subset 和 lifecycle 组合 fail closed。
17. Planner 能明确区分 BLOCKED、terminal 与 executable，而不是统一投影为空任务。
18. Strict type checking、architecture tests、branch coverage 和现有行为测试继续通过。
19. 最终代码不存在旧 FAILED、FailGraphExecution、FailTransition、flat frontier 或双 lease identity 的可运行路径。

## 11. 建议实施顺序

1. 引入 frontier node settlement 和 ABORTED，替换旧 flat frontier/FAILED 模型。
2. 完成 GraphState reducer、validation 和 execution projection。
3. 修改 planner、claim 和 lease，只处理 Pending node subset。
4. 修改 collector，保留 lease 的全部 success/failure。
5. 实现 blocked settlement 和 `ResumeGraphFrontier`。
6. 修改 routing，合并 frontier 中跨 attempt 的全部成功 contribution。
7. 对齐 resource、interrupt、abort 和 nested child 行为。
8. 删除旧 authoritative 路径并补齐确定性测试。

每个可合入提交应保持单一可运行模型并通过完整检查；不应为了拆分提交而增加 compatibility layer。

## 12. 最终意见

需求可以按当前 GraphState 能力进入实现。

实现重点是 node-level frontier settlement、显式 resume、Pending subset execution、完整 routing contribution 和旧失败路径清理。所有新持久化设施、泛型 output 恢复与跨进程事务设计均不属于本次范围，不应成为实现 blocker。
