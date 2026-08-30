# “每个图的 `GraphRun` 自己负责自己的状态”可行性调研

## 文档信息

- 调研日期：2026-08-27
- 调研范围：当前 `mote-kernel` 的父子图执行、状态提交、恢复和结果投影边界
- 本轮变更：只新增本文；不修改 production code、State schema、Store、公共 API 或测试
- 结论级别：`CONDITIONALLY FEASIBLE / NOT READY FOR IMPLEMENTATION`

## 1. 结论先行

把父子图从“一个 invocation 共享一个运行上下文”演进为“每个图的 `GraphRun` 自己负责自己的状态”，在控制状态层面是可行的：当前每个 child 已经有独立的不可变 `GraphRunState`，并且 child run id、父激活信息、定义身份和版本都有严格校验。

但目标描述中的真正语义不是简单地把一个 `GraphRunContext` 拆成多个对象，而是要求：

```text
父 Graph.run
  -> 以 child_run_id 调用 child GraphRun
  -> child 自己加载 authoritative state 和本地 continuation/frame
  -> child 自己提交每个 transition
  -> child terminal output 已经可读且可恢复
  -> 父验证并提交 nested-node settlement
  -> 父继续 routing
```

这项语义目前尚未闭合，原因有三类：

1. `GraphCommit` 只有“提交并确认 successor”的 callback 形状，没有按 `run_id` 加载状态、continuation、frame 或 terminal output 的能力。
2. child 的 concrete output 目前保存在 invocation 级共享 `ScopedFrameIndex` 中，不在 `GraphRunState` 内，也没有独立的 durable child boundary。child 已完成而父 settlement 尚未提交时，进程崩溃会留下无法仅凭 state 重建 output 的窗口。
3. 父子之间没有显式的 start-intent、terminal handoff、消费确认、CAS/idempotency 和预算协议。直接递归调用公共 `Graph.run()` 会把这些事务边界隐藏在 callback 或 `Graph` 实例中，违反唯一 execution owner 和无隐藏 mutable state 的架构约束。

因此裁决是：

> **概念上条件可行，当前不应直接实现公共 `child Graph.run(child_run_id)`。**
> 先完成 child-local ownership 的内部切分，再建立窄的 typed run/state 与 terminal-boundary 协议；只有 crash window、幂等和预算语义闭合后，才进入真正独立的 `GraphRun` 调用。

本文不把“有一个独立的 Python 对象”误写成“跨 invocation 或进程可恢复”。在没有持久化边界的情况下，后者不能宣称成立。

## 2. 术语和判定标准

### 2.1 对象的职责

| 术语 | 本文含义 | 应拥有的事实 |
| --- | --- | --- |
| Graph definition | 编译后的不可变拓扑和节点契约，当前主要是 `CompiledGraph` | 节点、边、输入/输出 descriptor、定义 id/version、资源声明 |
| `GraphRun` | 一次图执行的运行单元（目标模型；当前不是完整公共类型） | 一个 `run_id` 对应的状态、执行会话和本地图内 transient context |
| `GraphRunState` | authoritative、可恢复的控制状态 | lifecycle、frontier、superstep、lease/token、resource snapshot、parent activation、revision |
| `GraphRunContext` | 当前 invocation 的可变 envelope | 已确认 state binding、frame index、family identity、recovery 标记 |
| frame / continuation | 节点输入、已确认 publication、resume input、child boundary 等具体值及其执行位置 | 必须有明确 owner；不能靠另一份隐式镜像恢复 |
| parent activation | `(parent_run_id, superstep, nested_node_id)` | child 身份和父子 lineage 的不可变关联 |
| child terminal boundary | child 完成/中止/等待恢复后，父可读取的一次 typed 结果边界 | child identity、父 activation、输出或 reason、terminal revision、可重读语义 |

### 2.2 “独立”必须满足的四个条件

本文把独立 `GraphRun` 分成四个层次，避免把 transient 隔离和 durable 隔离混为一谈：

1. **状态隔离**：父和 child 不共用可变 state binding。
2. **执行隔离**：child 的 session、lease、frame/continuation 由 child 自己管理；父只能通过 typed boundary 观察 child。
3. **提交隔离**：child 的 transition 经过 child 所属 run 的 authoritative commit，父不能代替 child 写状态。
4. **恢复隔离**：新的 invocation 能仅凭 `child_run_id` 重新加载 child 所需事实，并在 terminal handoff 未被父消费时继续恢复。

当前代码已经部分满足第 1 项，但第 2～4 项仍依赖同一个 invocation 的内存上下文或调用方自备的 callback。

## 3. 当前实现 inventory

以下行号以本调研时工作树中的源码为准；行号用于定位事实，不代表授权修改这些文件。

| 位置 | 当前事实 | 对目标的影响 |
| --- | --- | --- |
| `src/mote_kernel/execution/facade.py:601-738` | 公共 `Graph.run()` 负责新 run admission、state/continuation recovery、创建 context、调用 `drive_root()` 并投影结果 | 当前一次调用的入口只接受显式 state/continuation，没有按 id load 的入口 |
| `src/mote_kernel/execution/run_context.py:308-421` | `GraphRunContext` 同时持有 `root_state`、`child_states` 和一个 `ScopedFrameIndex`；`state_at()`/`replace_state()` 在同一 envelope 中切换 scope | 这是当前父子共享上下文的直接 owner |
| `src/mote_kernel/execution/run_context.py:315-376,452-476` | continuation snapshot 包含 root、所有 child binding 和统一 frames；continuation 禁止复制/序列化 | child 无法独立携带自己的恢复资料 |
| `src/mote_kernel/execution/family_driver.py:123-147` | `commit_transition()` 生成 `GraphTransition`，调用单个可选 `GraphCommit`，并要求返回精确 reducer successor | callback 没有 load、CAS 外的幂等、terminal handoff 或 ack 语义 |
| `src/mote_kernel/execution/family_driver.py:167-212` | child projection 从共享 context 查找 state；完成时在共享 frames 中确保 child boundary | 父直接读取 child-local 事实，边界尚未独立 |
| `src/mote_kernel/execution/family_driver.py:301-330` | 父 materialize child input，提交 `StartGraphRun`，再把 child state 和 input frame 放入同一个 context | child start commit 与本地运行资料安装属于同一 invocation |
| `src/mote_kernel/execution/family_driver.py:333-384` | `_advance_scope_quantum()` 用同一个 context、executor map、limits 和 commit 驱动任一 scope | scope 不是独立运行单元，只是 family driver 的参数 |
| `src/mote_kernel/execution/family_driver.py:387-432` | `_drive_children()` 循环调用 child quantum；父等待 child projection 变化 | child 不通过自己的 `run()` 入口加载/提交 |
| `src/mote_kernel/execution/family_driver.py:435-453` | `drive_root()` 以 root scope 循环，直到返回 completed/aborted/awaiting boundary | root driver 统筹整个 family 的控制流 |
| `src/mote_kernel/execution/family_driver.py:493-515` | 最终结果从 root state 和共享 frames 投影；failure/interrupt 会遍历所有 scoped states | 结果投影依赖 family 级 context，非单 run terminal record |
| `src/mote_kernel/execution/engine/frontier.py:47-106` | frontier preparation 要求 child projection 精确覆盖 pending nested activation，并把 completed child output 转为 parent `TaskSuccess` | 父 settlement 需要 child output，而 state 本身没有该 output |
| `src/mote_kernel/state/graph_state/model.py:57-71` | `GraphRunState` 已含 run/definition identity、status、frontier、lease、resources、parent、revision | 控制状态的 per-run schema 基础已经存在 |
| `src/mote_kernel/state/graph_state/identity.py:36-49` | `child_graph_run_id(parent_run_id, superstep, node_id)` deterministic | 可用作幂等 start key 和重试查找 key |
| `src/mote_kernel/state/graph_state/command.py:90-141` | `StartGraphRun`、claim/fence、`SettleGraphNode`、resume、frontier resolution、abort 是 typed commands | reducer 命令足够表达单 run 生命周期，但未表达跨 run handoff |
| `src/mote_kernel/state/graph_state/execution_transitions.py:46-61,98-103,134-176` | reducer 纯地生成 start/fence/complete/abort/settlement successor，并校验 revision/token | 可保留 durable-first 和 exact successor，不应让 execution 直接改 state |
| `src/mote_kernel/execution/result.py:167-235` | `MissingChild`、`ActiveChild`、`CompletedChild(output)`、`AbortedChild` 是 invocation 内部 projection | 这些类型可作为未来 boundary 的语义来源，但不是持久化协议 |

当前模式可以准确表示为：

```text
一次 root Graph.run
  = root GraphRunState
  + 多个 child GraphRunState
  + 一个 GraphRunContext
  + 一个 ScopedFrameIndex
  + 一个 family-driver 调度循环
  + 一个 commit callback
```

这里的“多个 state”不能被误解为“多个独立 GraphRun”。state 已分开，执行资料和提交入口仍然聚合。

## 4. 当前父子调用时序

```text
父 Graph.run(values/state)
  |
  +-- compile family -> CompiledGraph + executor map
  |
  +-- 新 root: StartGraphRun -> commit_transition -> confirmed root state
  |
  `-- 创建一个 GraphRunContext
        root_state = root state
        child_states = ()
        frames = 一个空/恢复后的 ScopedFrameIndex
        |
        `-- drive_root()
              |
              `-- _advance_scope_quantum(root, context, executors, limits, commit)
                    |
                    +-- child_projections(root state, 同一个 context)
                    +-- prepare_frontier(...)
                    |
                    +-- MissingChild
                    |     +-- 从 parent frame materialize child input
                    |     +-- commit StartGraphRun(child id, parent activation)
                    |     +-- context.replace_child(child state)
                    |     `-- context.frames.add_graph_input(child input)
                    |
                    +-- _drive_children()
                    |     `-- _advance_scope_quantum(child, 同一个 context, 同一 limits/commit)
                    |           `-- child transition -> context.replace_state(child scope)
                    |
                    +-- child COMPLETED
                    |     `-- 共享 frames.add_child_boundary(output view)
                    |
                    +-- parent projection
                    |     `-- CompletedChild -> TaskSuccess
                    |         AbortedChild  -> TaskFailure
                    |
                    +-- parent SettleGraphNode -> parent state commit
                    `-- parent routing/frontier resolution
```

这个流程的优点是事务顺序和行为已经有测试覆盖；缺点是 child 的所有依赖都借用了父 invocation 的生命周期。父调用结束、进程崩溃或跨 worker 转移后，`child_states` 和 frames 不会自动成为可加载的独立资料。

## 5. 目标时序和边界

建议的目标模型不是“public `Graph.run()` 任意递归”，而是先在 execution owner 内引入一个 typed child-call protocol：

```text
Parent GraphRun
  |
  +-- 读取自己的 authoritative state / local continuation
  +-- derive deterministic child_run_id
  +-- materialize typed child input
  +-- commit/ensure child-start intent (parent activation + definition identity)
  |
  `-- invoke Child GraphRun(child_run_id)
        |
        +-- child load(child_run_id)
        +-- 校验 definition/version/parent activation
        +-- 建立 child-local context、session、lease
        +-- child prepare -> claim -> execute -> settle
        |       每条 transition 都由 child 的 commit port 确认
        |
        +-- child terminal state commit
        +-- terminal output/reason boundary durable 且可重读
        `-- return typed ChildResult
              |
              +-- COMPLETED(output boundary)
              +-- AWAITING_RESUME(metadata)
              `-- ABORTED(reason)
  |
  +-- parent 验证 child id、activation、definition/version、terminal revision
  +-- parent SettleGraphNode commit
  +-- parent 安装 publication / routing contribution
  `-- parent 继续执行
```

边界规则：

- child 只能读写自己的 `GraphRunState`、本地 frames 和 terminal record。
- parent 不直接读取 child-local frame；只能消费已经确认的 typed child boundary。
- topology/compiler/execution engine 可以共享不可变代码和定义，但不能由 child 创建第二套私有 runner。
- child terminal boundary 在 child terminal state commit 成功之前不可见。
- parent settlement 失败后，重复 parent invocation 必须能读到同一个 child terminal boundary，而不是重新猜测或重复执行 child。
- public `Graph` 仍是唯一图组合和执行 facade；`GraphRun` 首先应是 execution-internal owner，是否最终公开另行评审。

## 6. 已有能力与缺口矩阵

| 能力 | 当前状态 | 可否直接复用 | 必须补的契约 |
| --- | --- | --- | --- |
| 每个 child 有独立 `GraphRunState` | 已有 | 可以 | 保持 schema 与 reducer 不被 output 污染 |
| deterministic child id | 已有 | 可以 | 将其同时作为 idempotency/start key，并拒绝不同 activation 的复用 |
| parent activation / definition version 校验 | 已有 | 可以 | child load 和 terminal read 都做同样校验 |
| pure reducer + exact successor | 已有 | 可以 | 每个 run 使用自己的 commit owner；保留 revision/token CAS |
| child-local execution context | 缺失 | 不可直接复用 | 从 aggregate context 抽出本地 frame/continuation owner |
| 按 `run_id` 加载 authoritative state | 缺失 | 不可 | 窄 typed load port 和 stale/missing 错误语义 |
| 按 `run_id` 加载 continuation/frame | 缺失 | 不可 | 定义 checkpoint/boundary 的 owner、codec 和版本 |
| child terminal output durable boundary | 缺失 | 不可 | output/reason/awaiting metadata 的 typed record、读后不变 |
| parent-child start intent / ensure-start | 部分存在（命令） | 不能直接当幂等协议 | expected identity、重复提交返回规则、冲突规则 |
| parent terminal consumption acknowledgment | 缺失 | 不可 | parent settlement 与 child boundary 的关联、重放/确认规则 |
| cross-run CAS / idempotency | 只有 state revision/token | 不足 | start、terminal publish、ack 各自的 key 和冲突结果 |
| aggregate parallel budget | 仅 invocation 内 `ExecutionLimits` | 不足 | parent 分配 token 或明确 child 串行策略 |
| resource ownership | child transition 内可表达 | 基本可以 | 明确跨 child 是否隔离，禁止隐式全局锁表 |
| cancellation / ordinary exception fence | 当前语义已有 | 可保留映射 | 父取消、child 取消、lease fence 和重试责任需写清 |
| continuation serialization | 明确禁止 | 不能宣称跨进程恢复 | 另立 codec/Store 协议，不能偷偷放宽当前 API |

最关键的判断是：已有 `GraphRunState` 只解决“控制位置”，没有解决“完成结果在哪里以及谁有权消费”。

## 7. 崩溃窗口与提交顺序

### 7.1 必须处理的窗口

| 窗口 | 可能留下的 durable 事实 | 重新启动时所需事实 | 当前能否恢复 |
| --- | --- | --- | --- |
| child start 已提交，父在调用 child 前崩溃 | child `RUNNING`、parent nested node 仍 pending | 按 deterministic id 找到 child，并加载 child input/checkpoint | 不能仅凭当前 public API；没有 load port |
| child 某节点 settlement 已提交，父 invocation 崩溃 | child state 有 frontier 进展 | child-local input/publication/frame | 依赖原 context；不能从 state 单独重建 concrete values |
| child terminal state 已提交，父 settlement 失败 | child `COMPLETED`，parent nested node `PENDING` | child output boundary、精确 activation、可重读 token | 当前 output 只在共享 frames，存在丢失窗口 |
| terminal boundary 已提交，父重试两次 | child terminal + 未/已 ack | 同一 output 的幂等 read，重复 ack 不重复 publication | 尚无 ack/read 协议 |
| parent 先提交 nested success，child terminal 尚未 durable | parent 看似成功，child 可能仍运行或丢失 | 需要反向补偿/回滚 | 应明确禁止 |
| commit callback 报错但 authoritative 结果未知 | 可能已写入，也可能未写入 | 重新 load 后按 revision/token 判断 | 当前仅能停止；不能自行推测 successor |

### 7.2 推荐的严格顺序

```text
1. parent 确认 child start intent
2. child start state commit
3. child 自己完成所有 claim / settle / resolve transition
4. child terminal state commit
5. child terminal output boundary commit（与 terminal state 的可见性规则明确）
6. parent 读取并验证 terminal boundary
7. parent SettleGraphNode commit
8. parent 安装 publication / routing
9. parent 可选地确认 child boundary 已消费
```

第 4、5 步不能被交换成“先让父看到成功，再补 output”。若 terminal state 和 output 必须跨两个物理写入，协议至少要提供可观察的 `published` 状态、重试安全和 fail-closed 规则；理想情况是由同一 authoritative store 原子提交，若做不到则必须明确两阶段/补偿语义，而不是把两个 callback 调用假装成原子事务。

### 7.3 `GraphCommit` 的边界

当前 `GraphCommit`（`family_driver.py:115-120`）适合表达：

```text
给定 previous_state + command -> 确认 exact candidate GraphRunState
```

它不适合在不改变职责的情况下同时承担：

- `load(run_id)`；
- child input admission；
- frame/continuation checkpoint；
- terminal output publish/read；
- parent consumption acknowledgment；
- 跨 run CAS、幂等 key 和 worker ownership。

这些能力应由窄的 typed ports 分开设计。若未来扩展 `GraphCommit`，必须把新增操作写成明确的协议，而不能在 callback 闭包里捕获一个未声明的 registry 或共享字典。

## 8. State、frame、continuation 的 ownership 方案

### 8.1 推荐的目标所有权

```text
GraphRun (child)
  ├─ authoritative GraphRunState
  ├─ child-local frame index
  ├─ child-local continuation/checkpoint
  ├─ execution lease + session disposition
  └─ terminal boundary (typed, durable)

Parent GraphRun
  ├─ parent authoritative GraphRunState
  ├─ parent-local frames
  ├─ child reference / activation record
  └─ consumed child publication or ack evidence
```

父可以保存 child reference（run id、definition identity、activation），但不应保存一份 child output frame 的“镜像真相”。如果为了性能做缓存，缓存必须是可丢弃的，并且任何恢复路径都回到 child boundary 的 authoritative read。

### 8.2 `GraphRunState` 是否加入 output

不建议把 concrete `GraphOutputView` 或节点 output 写入 `GraphRunState`：

- State 当前是控制状态；output 的 codec、大小、敏感性和生命周期不同。
- 把 output 塞进 state 会把 DomainState/GraphState 责任混在一起，也会使 reducer 命令变宽。
- 当前 `GraphRunState` 已通过 `frontier` 记录 recoverable position；它没有声称保存所有 concrete values。

更清晰的方案是独立的 typed terminal boundary/ publication record，并为其定义 identity、版本、codec 和 durable-first 规则。若业务确实要求 output 与 state 同一事务，应该提出单独的 State/Store 需求，而不是添加隐藏字段。

### 8.3 scope coordinate 的裁决

有两种可行表示：

| 方案 | 优点 | 风险 |
| --- | --- | --- |
| child 在本地图内使用 `scope=()`，以 parent metadata 关联 | ownership 最清晰；child 内部可复用 root driver | 会改变现有 diagnostics、transition scope、continuation identity |
| child 保留完整 scope path，但拥有独立 context | 迁移现有 projection/诊断较平滑 | 容易让人误以为仍共享 family context；必须显式传 parent lineage |

第一阶段建议保留外部可见的现有 scope/diagnostic identity，内部先引入显式 `GraphRunRef` 和 parent activation boundary；待 boundary 协议稳定后，再决定是否把 child coordinate 归一为 child-local root。无论选择哪一种，`run_id`、definition id/version 和 parent activation 都必须独立校验。

## 9. Parent-child typed protocol（概念草案）

以下名称只是协议分解示意，不是本轮要新增的公共类型：

```text
load_run(run_id)
  -> authoritative GraphRunState + run-owned recovery material

ensure_child_started(start_intent)
  -> existing matching child state | newly committed child state

commit_transition(transition)
  -> exact successor for this run only

publish_terminal_boundary(boundary)
  -> durable, idempotent publication evidence

read_terminal_boundary(child_ref)
  -> same typed boundary, or explicit not-yet-published

acknowledge_child_boundary(ack)
  -> parent-side consumption evidence (if required by retention policy)
```

协议至少需要以下字段/不变量：

- `run_id`、definition id/version、parent activation、terminal revision；
- expected previous revision 或 execution token；
- start/terminal/ack 的 deterministic idempotency key；
- missing、stale、identity-conflict、already-published、already-acked 的 typed outcomes；
- output codec/version 与大小/资源限制；
- owner/lease 归属，避免两个 worker 同时驱动同一 run；
- read-after-commit 语义：commit 返回成功后，下一次 read 必须能看见同一 successor/boundary，或返回明确的暂不可见状态。

协议设计应优先支持最小 terminal contract：

```text
Completed(output)
AwaitingResume(failure/interrupt metadata)
Aborted(reason)
```

不能把 `Completed(output)` 退化成“只有 `GraphRunStatus.COMPLETED`”，因为父 settlement 需要具体 output；也不能让父从 child 内部 frame 直接借值。

## 10. 并发、limits、资源和错误传播

### 10.1 `ExecutionLimits`

当前 `_drive_children()` 将同一个 `ExecutionLimits` 传给每个 child quantum（`family_driver.py:387-432`）。若改成多个独立 `Graph.run()` 并允许并发，每个 child 都各自使用 `max_parallel_tasks`，总 live task 数可能超过父 invocation 的预算。

第一阶段应选择一个明确策略：

1. **child 串行 quantum**：父持有 aggregate budget，语义变化最小，推荐作为迁移起点。
2. **typed budget token**：父将可用 superstep/task 配额分配给 child，child 只能消耗被授予的额度。
3. **全局/租约预算**：由独立 owner 协调跨 run 的总量，需额外的并发协议和故障回收。

不应让每个 child 看到一个“看起来完整、实际上未聚合”的本地默认限制。

### 10.2 resource semantics

当前 resource snapshot 属于各自 `GraphRunState` 的 claim/fence/settle 生命周期；nested activation 本身不会替父获取 child 的资源。建议保持这一语义：resource ownership 在 child graph 内闭合，不新增隐式全局 resource registry。若未来要求跨 child 互斥，必须单独设计 typed resource owner、租约和恢复规则。

### 10.3 失败、interrupt、abort

现有映射应保持：

| child boundary | parent 行为 |
| --- | --- |
| `COMPLETED` + 已验证 output | 生成 parent nested `TaskSuccess`，走 `SettleGraphNode`，固定 `ContinueGraphRouting` |
| `ABORTED` + canonical reason | 生成 parent nested `TaskFailure`，不以同一 child identity restart |
| `AWAITING_RESUME` | parent nested node 保持 pending；父结果可等待恢复，不伪造 success/failure |
| `ExecutionLimitError` | 继续按现有异常传播，不伪造 parent abort；child 是否保留 lease 由 recovery contract 决定 |
| cancellation | child session 先按现有 close 语义收敛；父是否连带取消、谁 fence child 必须显式规定 |
| ordinary exception | exact token fence 责任归 child；父只看到 typed failure 或未决状态，不猜测 commit 是否成功 |

父重试不得因为“没收到返回值”就重新生成一个不同的 child id；同一 parent activation 必须重读 deterministic child id，并按 terminal boundary 的幂等规则处理。

## 11. 方案比较

| 方案 | 能达到的目标 | 风险/代价 | 结论 |
| --- | --- | --- | --- |
| 保持共享 `GraphRunContext` | 保持现有行为和测试 | 不满足独立 load/commit/recovery | 仅作为基线 |
| 只拆 child-local context，仍由 family driver 调度 | 消除直接共享 frame/state 的 ownership 混淆；可验证 value boundary | 跨 invocation/crash 仍不可独立恢复；不应宣称完整目标 | 可作为 Phase 1 |
| 独立 context + typed state/run port + terminal boundary | 可恢复的 child call、清晰提交边界、可处理父重试 | 需要 Store/协议、幂等、预算、迁移和大量故障测试 | 目标方案，但当前尚未具备前置条件 |
| 直接让 nested node 递归调用 public `Graph.run()` | 表面上接近目标调用形态 | public overload 语义混乱；store/commit 依赖隐式化；容易产生第二 runner 或事务嵌套；无法解决 output durability | 不建议作为首步 |

这里的比较是架构选项分析，不是对现有实现“好坏”的评分；是否进入后一方案需要满足下一节的门槛。

## 12. 分阶段实施路线（仅建议，不是本轮授权）

### Phase 0：只读故障窗口证明

不改 production code，补齐可重复的设计证据：

- child start commit 成功、父随后崩溃；
- child terminal state 成功、父 settlement 失败；
- child state 存在但 child continuation/frame 不存在；
- commit callback 在 child transition 上的可观察顺序和异常语义；
- repeated activation 的 deterministic id；
- limit、cancellation、abort、awaiting-resume 的传播；
- 多 child 并发时 aggregate budget 的上界。

退出条件：没有 terminal output durability 方案时，停止，不进入实现。

### Phase 1：内部 child-local ownership

- 将 aggregate context 中的 child binding/frame 操作抽象为 child-local context；
- parent 只持有 typed child reference 和 projection boundary；
- 仍可由同一内部 family driver 以串行 quantum 驱动；
- 不改变 public `Graph.run()` overload，不宣称跨 invocation recovery。

退出条件：测试证明 child 不再直接读取 parent frame，且所有现有 nested 行为保持不变。

### Phase 2：窄 typed state/run port

- 增加 load authoritative state 的装配层 port；
- 为 exact transition 增加 expected revision/token、冲突和幂等语义；
- 定义 child input admission 与 child-owned recovery material 的 owner；
- 保持 `GraphRunState` schema 优先不变；若无法表达 handoff，另立 State/protocol 需求。

退出条件：可以在新 invocation 中仅凭 child id 恢复 child 的控制位置，且 stale/identity mismatch fail closed。

### Phase 3：terminal boundary protocol

- durable publish completed output、aborted reason、awaiting-resume metadata；
- 支持 read-after-commit、重复读取和父 settlement 失败后的重试；
- 定义 parent acknowledgment 是否需要以及 retention 规则；
- 验证 child terminal 与 boundary 的原子性或明确两阶段语义。

退出条件：模拟“child terminal commit 成功 / parent settlement 失败 / parent 重启”能够得到同一 typed output，且不重复执行 child。

### Phase 4：真正独立的 `GraphRun.run(child_run_id)`

只有 Phase 2/3 闭合后，才考虑让 child 自己 load/commit，并让 parent 以 child-call 方式驱动。仍须保持：

- 唯一 public `Graph` facade；
- 唯一 execution engine；
- child 不创建 private runner；
- durable-first、exact successor、pure reducer；
- shared immutable topology/routing owner；
- 无隐藏 registry、第二 frame truth 或未经授权的兼容 alias。

## 13. 验收矩阵和停止条件

### 13.1 必测行为

1. child start commit 成功、父随后失败，重试能找到同一 child。
2. child terminal commit 成功、父 nested settlement 失败，重试可读同一 boundary。
3. terminal output 缺失时 fail closed，不执行 parent downstream。
4. child state、boundary、parent activation 或 definition/version 任一不匹配时拒绝投影。
5. 同一路径不同 superstep 的 child run 并存，旧 child 不被覆盖。
6. child awaiting resume 独立恢复，父保持 pending。
7. child aborted 映射 parent failure，并禁止同一 identity restart。
8. cancellation 后 lease/fence 行为与现有 contract 一致。
9. ordinary exception 后 exact token fence，不猜测未知 commit 结果。
10. 多 child 串行/并发都不突破 aggregate `max_parallel_tasks`。
11. child resource 与 sibling resource 的隔离语义稳定。
12. grandchild 递归使用同一 protocol，不产生第二 scheduler。
13. parent/child commit callback 顺序和失败可观察且可重放。
14. child start、terminal read、ack 都是幂等的。
15. 只有 state、没有 boundary/frame 时恢复结果明确失败或等待，不伪造 output。
16. concrete output 不进入 `GraphRunState`，除非另有批准的 schema 需求。
17. public typing 不新增第二个执行入口或未声明的 recursive overload。
18. continuation/frame 不跨 child context 泄漏。

### 13.2 任一成立即停止

- 没有可重读的 child terminal output，却要求 parent 在崩溃后继续；
- 通过 `Graph` 实例字段、全局 registry 或裸容器保存 child authoritative state；
- 父可以直接写 child state，或 child 可以直接写 parent publication；
- 用“重新执行 child”掩盖 output 丢失，却没有重新定义副作用/exactly-once 语义；
- 每个 child 自带 scheduler，导致 aggregate limit 无法证明；
- 把 callback 的一次成功返回误写成跨 store 原子事务；
- 通过隐藏字段、兼容 alias 或第二 frame mirror 绕过 State/Store owner。

## 14. 最终裁决

**`CONDITIONALLY FEASIBLE / NOT READY FOR IMPLEMENTATION`。**

可行部分是：现有 `GraphRunState`、deterministic child id、parent activation、pure reducer、exact successor 和 recovery identity 校验，足以作为独立 run 的控制状态基础。

尚不可行部分是：child 自己按 id 加载并提交、terminal output 在父失败后可重读、父子完成确认幂等、跨 run 的 lease/budget/cancellation 协议。它们不是 `GraphRunContext` 的局部重构，而是新的 execution/state boundary。

本轮没有修改 production code。下一步应先完成 Phase 0 的故障窗口证明和 terminal boundary 设计评审；在此之前，不建议把 public nested node 改成直接递归调用 `Graph.run(child_run_id)`。
