# Mote Kernel Failover 设计

状态：设计共识（Role 默认策略、Port 绑定继承、参数热加载、Graph 持久恢复）

本文定义 Kernel 中 Failover 的边界和最小模型。它基于：

- [Mote v2 总体架构](/home/longert/motev2/README.md)；
- [平台架构](/home/longert/motev2/docs/mote-platform-architecture.zh-CN.md)；
- [Kernel 架构](/home/longert/motev2/mote-kernel/docs/architecture.zh-CN.md)；
- 当前唯一的 [`GraphRunState`](/home/longert/motev2/mote-kernel/src/mote_kernel/state/graph_state/model.py)、
  [`GraphRunCommand`](/home/longert/motev2/mote-kernel/src/mote_kernel/state/graph_state/command.py) 和
  [`reduce_graph_run()`](/home/longert/motev2/mote-kernel/src/mote_kernel/state/graph_state/reducer.py)。

## 1. 一句话结论

Failover 是一张固定的、可持久恢复的重试图。它一次只包裹一个已经存在的 Port：

```text
Role config
  ├─> 默认 FailoverProfile
  └─> Port binding（继承 / 覆盖 / 禁用）
        └─> 每个 operation 的 FailoverPlan 快照
              └─> 固定五节点重试图
                    └─> 一个具体 Port 的单次调用
```

同一套 `FailoverProfile` 可以被多个 Port 继承，但每个 Port 仍分别装配自己的重试图；每次 logical
operation 都有独立的配置快照、持久化 cursor/预算和 operation identity。共享的是无状态的策略模板，
不共享包装实例、cursor 或运行状态。cursor 属于 Graph 的状态值，不属于 Port 或 decorator 实例的可变字段。
Failover 不调用其他 Port，不编排业务流程；主业务 Graph 负责决定何时调用付款、审批或其他 Port。

Role/Flow 的组装层负责把默认 profile 应用到声明的 Port。用户不需要为每个 Port 手写一层 while-loop，
也不能把整个主业务 Graph 一次性包起来。

Failover 图由现有 `mote_kernel.execution.Graph` 执行。它不能在 Port 内部启动第二个 Graph runner，
也不能拥有另一份状态或隐藏的可变重试计数器。

这里必须区分 owner：**Graph 本身不重试失败节点。** `InvokeOnce` 遇到可重试的 provider/Port 结果时，在 Graph
语义上成功返回一个 `PortOutcome`；`ObserveAndRoute` 再决定是否走到一个新的 `InvokeOnce` activation。若节点已经
提交为 `FailedGraphNode`，本次 Graph 运行终止，Failover 也不能通过 Graph resume/override/skip 把它改回 Pending。

## 2. 设计目标

- 同一个重试图拓扑可以长期复用；重试次数、退避、超时等参数可以热加载；
- 每次重试都是 Graph 的普通状态推进，并进入同一个 durable state/commit 边界；
- 一个 Port 的恢复和另一个 Port 完全隔离；
- 不确定的外部结果先对账，不因网络超时盲目重放副作用；
- 底层 Port 只执行一次调用，重试策略只有一个 owner；
- 失败、恢复、取消、预算耗尽和崩溃恢复都能确定性重放。

## 3. 非目标

Failover 包不负责：

- 定义或实现一个通用的 Port 基类；
- 提供 provider、SDK、credential、endpoint registry 或具体网络实现；
- 在多个 Port 之间做业务编排；
- 维护全局 `FailoverManager` 或共享 retry cursor；
- 创建私有 runner、私有 reducer 或独立状态存储；
- 直接操作数据库、journal、CAS、lease 或远程 worker；
- 以异常字符串推断重试策略；
- 通过兼容别名保留旧的通用 recovery loop。

Failover 可以依赖一个最窄的“单次调用”结构，但这不是新的 Port SPI：

```text
SingleAttempt<RequestT, AttemptResultT>
  invoke_once(request) -> AttemptResultT
```

具体 Port 由 Role/Flow 的组装层或 Runtime 提供，Failover 只对它进行组合和调度。

## 4. 所有权边界

```text
┌────────────────────────────────────────────────────────┐
│ Product / Role config                                  │
│ 默认 FailoverProfile、Port binding 规则、不可变配置快照     │
└──────────────────────────┬─────────────────────────────┘
                           │ typed snapshot
                           ▼
┌────────────────────────────────────────────────────────┐
│ Role / Flow assembly                                   │
│ 解析 inherit/override/disabled，并为每个 Port 组装一张图    │
└──────────────────────────┬─────────────────────────────┘
                           │ per-Port binding
                           ▼
┌────────────────────────────────────────────────────────┐
│ Kernel Failover                                        │
│ 固定图、失败分类的语义、决策、预算边界、Graph transition   │
└──────────────────────────┬─────────────────────────────┘
                           │ single-attempt capability
                           ▼
┌────────────────────────────────────────────────────────┐
│ Runtime / concrete Port                                │
│ provider wire、receipt、poll、reconcile、credential 实现 │
└────────────────────────────────────────────────────────┘
```

如果某个重试改变的是 Agent flow 的业务含义，它是 Kernel 图的一条边；如果只是底层 SDK 为完成一次
调用而自带的微重试，则属于 Runtime。两者不能同时对同一逻辑调用拥有 retry loop。

## 5. 固定重试图

### 5.1 核心图：五个节点，固定骨架

本节记录已经确认的核心图。核心图不按 HTTP 状态码展开，也不为每一种 Role config 生成一张新拓扑。
Role config 只生成不可变的 `FailoverPlan`；状态码和 provider 证据交给“观察并分流”节点处理。

一个单 Port 的 failover nested graph 只有五个实际节点（`START`、`END` 是 Graph 边界，不计入节点数）：

```text
START
  │
  ▼
LoadPlanOnce                 1. 读取计划
  │
  ▼
InvokeOnce(single_port)      2. 调用一次
  │
  ▼
ObserveAndRoute              3. 观察结果并选择路线
  ├─ completed / rejected / in_doubt ───────────────> END
  ├─ prepare_next ───────> PrepareNextAttempt ───────┐
  │                                                   │
  │                                                   └──> InvokeOnce
  └─ reconcile ──────────> Reconcile ──> ObserveAndRoute
```

`PrepareNextAttempt` 统一承载四种“下一次调用前的准备”：等待、修改请求、刷新/更换凭证、更换服务地址。
`Reconcile` 独立存在，因为它是在确认上一次请求的结果，不是重新提交请求。对账仍未完成时可以再次等待，
但不能绕过 `Reconcile` 直接回到 `InvokeOnce`。

节点职责如下：

| 节点 | 职责 | 边界 |
| --- | --- | --- |
| `LoadPlanOnce` | 读取本次 operation 的 plan snapshot，生成 `RetryContext` | 只读一次配置；恢复时使用已持久化的 plan |
| `InvokeOnce` | 调用被装饰 Port 的一次 wire operation | 一次 activation 至多一次底层调用，不拥有 retry loop |
| `ObserveAndRoute` | 看调用结果，判断成功、失败、未知，并选择固定路线 | 不执行等待、切换或重新提交 |
| `PrepareNextAttempt` | 按 typed 策略等待、改请求、换凭证或换地址 | 只准备下一步；不隐藏新的 retry loop |
| `Reconcile` | 查询同一 `operation_id` 的外部结果 | 不是重新 submit；无 reconcile 能力时进入 `InDoubt` |

图中传递的核心值是不可变类型，而不是裸字典或局部变量：

```text
RetryContext:
  operation_id / plan_revision / request_version
  attempt_ordinal / endpoint_cursor / credential_cursor
  retry_budget / receipt_or_reconcile_handle

PortOutcome:
  Completed | Rejected(FailureEvidence) | InProgress(Receipt) | Unknown(Handle)

PreparationAction:
  Wait | TransformRequest | RefreshCredential
  | RotateCredential | SwitchEndpoint
```

原始 SDK/HTTP 错误在 Port adapter 边界先转换为 `PortOutcome`/`FailureEvidence`；`ObserveAndRoute` 再根据
操作语义、提交状态和预算选择路线。两者都是纯判断，Graph 负责推进和持久化。

### 5.2 图循环与状态推进

回边只传递上一轮 activation 产生的 immutable `RetryContext`，使用 Graph 的 loop publication/relative
selection；不能把当前节点直接绑定成普通数据依赖，形成 data cycle。

一次重试的顺序固定为：

```text
InvokeOnce
  → ObserveAndRoute
  → 提交 settlement、route 和下一轮 RetryContext
  → 确认 GraphRunState successor
  → 才执行 PrepareNextAttempt 或 Reconcile
```

因此 retry cursor 不放在 decorator 或 Port 实例中。nested graph 的每次重试都属于主 Graph 的 execution
lineage，由现有 `GraphRunState`、`GraphRunCommand` 和 `reduce_graph_run()` 处理；不新增 runner、reducer
或平行 `FailoverState`。

图的节点和边在组装时确定。`max_attempts`、退避和 timeout 变化不会增加或删除节点，只会改变
`ObserveAndRoute` 的 guard。循环必须同时受语义预算和 `ExecutionLimits.max_supersteps` 保护；热加载参数
不能取消硬上限。

### 5.3 状态码和策略

底层 Port/adapter 先把 provider 的原始状态码、异常和响应转换为固定的类型化失败类别，例如：

```text
RateLimited
AuthRejected
TransientTransport
InvalidRequest
UnknownOutcome
PolicyDenied
```

图只认识这些类别，不直接解析 HTTP 整数或 SDK 异常。

固定图中的动作集合是一个互斥的 typed decision union。四种执行前准备共用一条 `prepare_next` 边，
由 `PreparationAction` 携带具体动作：

```text
PrepareNext(PreparationAction)
Reconcile
Finish
Abort
```

这里的 `Finish` 是“直接离图”的决策，不是额外的 Graph 节点。

Role config 只提供动作所需的参数：次数、退避、timeout、deadline、每类预算和 hard cap。若要增加一个
全新的动作类型，才需要新的 Graph definition/version；仅修改次数或等待时间不需要重建图。

### 5.4 预期结果不应直接变成 Graph failure

可重试的 Port 结果必须先作为类型化值进入 `ObserveAndRoute`，例如：

```text
Completed(response)
Rejected(failure)
InProgress(receipt)
Unknown(reconcile_handle)
Conflict(existing_fingerprint)
```

只有真正终止的结果才转换成 Graph failure、interrupt 或 abort。否则一次可重试的 429 会过早终止 failover 图，
无法走固定重试边。

“下一次尝试”是 failover topology 中一个新的 activation，不是 Graph 对失败 activation 的 retry。Graph 不公开、
不接收 `resume_failed`、failed-input override 或 `skip_failed` 作为 Failover 实现机制。

## 6. 单 Port 装饰

### 6.1 装饰发生在组装期

装饰不是写在 Port 实现类上的 Python 注解，而是 Role/Flow 组装时对 Port binding 应用 profile。
Role 给出一个默认 profile，单个 Port 可以继承、覆盖或禁用：

```text
Role:
  failover_profile: standard

  ports:
    payment:
      failover: inherit
      semantics: non_repeatable
      reconcile: required

    approval:
      failover: inherit
      semantics: idempotent
```

配置文件可以使用名字表达，进入 Kernel 前必须转换成类型化的 profile、binding 和 plan。组装层看到
`inherit` 后，自动把同一个固定图模板分别绑定到每个 Port：

```text
payment_port
  └─> payment_failover_graph

approval_port
  └─> approval_failover_graph
```

这里复用的是无状态的 `FailoverProfile` 和图拓扑，不复用包装实例、`RetryContext`、cursor、receipt
或 `operation_id`。每个 Port 都有独立的运行状态和预算。

主业务 Graph 可以按普通节点或 nested graph 使用这些结果：

```text
主业务 Graph
  ├─> payment_failover_graph
  ├─> approval_failover_graph
  └─> 其他业务节点
```

Failover 图不会从 `payment_port` 调用 `approval_port`。付款彻底失败后是否进入审批，是主业务 Graph 的
路由问题，不是 Failover 的职责。

### 6.2 不增加平行公共 Graph 门面

`mote_kernel.execution.Graph` 仍是唯一公开的 Graph 构建和执行门面。Failover 可以提供一个最小的组装
函数，返回普通 `Graph`/nested graph；不提供另一个公开的 `FailoverGraph` runner。组装函数由
Role/Flow binding 调用，不负责扫描或接管整个主业务 Graph。

装饰必须在主 Graph 第一次 `run()` 之前完成。Graph 冻结后，参数热加载只能改变下一次 activation 的
plan，不能修改已经编译的节点和边。整个主 Graph 不能设置一个全局 failover 包装，否则会把业务节点
也纳入重试。

### 6.3 一个 Port 一层 Failover

同一个 Port 不允许无意地套多层 failover。否则次数、退避和 unknown 处理会叠加，无法判断谁拥有逻辑
operation。组装层应在装配时拒绝重复包装，而不是在运行时猜测。

## 7. 配置快照和热加载

### 7.1 快照一次

每个 logical operation 进入重试图时，只把当前 Port binding 与 Role 默认 profile 合并并读取一次配置：

```text
Role default profile + Port binding override
    → FailoverConfigSource.snapshot()
    → FailoverConfigSnapshot(revision=N)
    → FailoverPlan(revision=N)
    → 整个 operation 使用 plan N
```

图不订阅配置更新，也不在每次 retry 中读取“最新配置”。当前 operation 使用自己的 plan；新 operation
使用新 revision。这使得崩溃恢复和审计结果保持确定。

profile 可以被多个 Port 复用，但 plan 不能跨 operation 或跨 Port 复用。`inherit` 只表示继承默认参数，
`override` 只覆盖声明的参数，`disabled` 表示该 Port 不装配 failover；三者都在组装期解析完毕。

### 7.2 参数更新与策略更新

| 变化 | 是否重建图 | 说明 |
| --- | --- | --- |
| 最大次数、退避、timeout、deadline | 否 | 新 activation 读取新 plan |
| 已有失败类别的参数 | 否 | 图分支不变 |
| Role 默认 profile 的参数 | 否 | 只影响采用 `inherit` 的新 operation |
| 单 Port 的 binding override | 否 | 只影响该 Port 的新 operation |
| Port 的 failover binding 开关变化 | 是 | 组装结果增加或移除一层 failover graph |
| 增加新的动作类型 | 是 | 新 Graph definition/version |
| 改变 Port 的输入/输出契约 | 是 | 需要重新装配和类型检查 |
| 增加未声明的 capability | 是 | 必须重新组装并验证 required Port |

如果必须改变一个已经等待中的 operation，应通过显式的 `ReloadPlan` 控制转换完成。该转换必须保留已
消耗的次数和 operation identity，不能通过重新构造 decorator 把计数归零。

### 7.3 硬上限

热加载的次数只能在预先声明的 hard cap 内变化。计数至少包括：

- logical retry attempt；
- 同一 Port/target 的次数；
- 必要时的 reconcile/poll 次数。

它们与 Graph 的 superstep 上限、Runtime 的 wire-attempt 上限分开管理。

## 8. Graph state 和持久化

### 8.1 每次重试都要进入同一个状态边界

重试图中的每个节点 settlement、路由选择和 cursor 更新都通过现有的 `GraphRunCommand` 与
`reduce_graph_run()` 完成。持久化提交确认之前，内存快照不能前进。

因此一次 retry 的顺序是：

```text
执行 InvokeOnce
  → 得到 typed result
  → ObserveAndRoute
  → 提交本次 node settlement 和下一步 cursor
  → 确认 durable successor
  → 执行下一次 Graph step
```

Failover 不在 decorator 实例中保存可变 `attempt_count`。

### 8.2 Cursor 的承载

`RetryContext` 是不可变的、可持久化的 Graph 值，至少包含：

```text
operation_id
attempt_ordinal
failure_class
candidate_position
plan_revision
waiting/reconcile handle（如有）
```

优先把它作为重试图的 typed publication/输入沿 Graph 传递。若某种 state-only recovery 不能从现有
publication/codec 恢复它，则由 `state` owner 把最小的 failover facts 加入现有 `GraphRunState`；不能新建
平行 `FailoverState`、第二个 reducer 或第二条提交路径。

### 8.3 Graph state 与外部 receipt 的分工

Graph state 记录“下一步应做什么”；Runtime/Persistence 记录“外部世界究竟发生了什么”。例如：

```text
Graph state: 等待 reconcile
Runtime receipt: provider operation id、已接受/已完成/未知
```

涉及外部副作用时，两者需要在同一个 commit boundary 中配对提交，但 Kernel 不直接操作 receipt journal。

`GraphExecutionToken` 只负责防止旧 worker 继续提交 Graph 状态；它不等同于外部 operation identity，也
不保证 provider exactly-once。

## 9. Unknown、InProgress 和副作用

### 9.1 Unknown 不得直接重试

```text
Unknown
  → Reconcile/Poll 同一个 operation
  → 已确认完成：返回 durable result
  → 已确认未执行：按安全语义决定是否 retry
  → 仍然未知：InDoubt / interrupt
```

`Unknown` 不得直接切到另一个 Port，也不得因为 timeout 就重新提交支付、消息或媒体任务。

### 9.2 Operation identity

- `operation_id` 在同一 Port 的逻辑重试中保持不变；
- 每一次具体调用可以有新的 attempt id；
- 不同 Port 即使请求内容相似，也必须使用不同的 operation identity；
- request fingerprint 改变时，不能复用同一个 operation identity；
- receipt、plan 和 state 中不能出现 credential secret。

### 9.3 取消和预算耗尽

取消是显式终止，不被归类为可重试的 provider failure。预算耗尽必须产生明确的 terminal/interrupt 结果，
不能继续绕过预算循环。

## 10. 端到端示例

### 10.1 付款 Port

```text
InvokeOnce(payment_port)
  → 429 / RateLimited
  → Decide: RetrySameEndpoint
  → 持久化 attempt=1、下一次 retry
  → Delay
  → InvokeOnce(payment_port)
```

如果第一次请求的结果是 `Unknown`：

```text
Unknown
  → reconcile payment operation_id
  → 已支付：返回已有 receipt
  → 未支付且安全可重试：继续同一策略
  → 无法确认：InDoubt，不再次扣款
```

### 10.2 审批 Port

审批 Port 使用另一张独立的重试图和另一份 plan。付款图不会包含审批节点；主业务 Graph 在付款图返回
最终结果后，才决定是否调用审批 Port。

## 11. 建议的包结构

这是职责布局，不代表新的执行或状态 owner：

```text
src/mote_kernel/failover/
  __init__.py       # 最小公共面；不提供新的 runner
  contract.py       # FailureClass、typed outcome、PreparationAction、单次调用结构
  plan.py           # FailoverProfile、Port binding、config snapshot、FailoverPlan、RetryContext
  policy.py         # 纯观察/分流规则
  assembly.py       # 五节点固定图组装，以及按 binding 绑定单一 Port
```

同一 `FailoverProfile` 可以由多个 Port 复用；`assembly.py` 为每个 binding 生成独立的 graph activation。
不建立 `port.py` 来定义所有 Port，也不建立 `runner.py`、`manager.py`、`decorator.py` 或通用 `utils` 包。

## 12. 必须锁定的测试不变量

实现时至少覆盖：

1. 同一 Port 的一次 operation 只读取一次 config snapshot；
2. 参数热加载不改变 Graph definition identity/version；
3. 同一 `FailoverProfile` 可被多个 Port 继承，但各 Port 的 `RetryContext`、budget 和 operation identity 互不共享；
4. `inherit`、`override`、`disabled` 只在组装期解析一次；
5. 每次 `InvokeOnce` 最多发出一次底层调用；
6. 每次 retry 都先提交当前 settlement/cursor，再进入下一步；
7. 重启后从最后一个已确认的 Graph state 继续，而不是从 decorator 内存计数器继续；
8. `Unknown` 只进入 reconcile/poll，不盲目重新 submit；
9. `InProgress` 只继续同一个 operation；
10. `CancelledError` 不产生下一次 retry；
11. budget 耗尽、policy denied、fingerprint conflict 都安全终止；
12. stale execution token 不能提交旧 worker 的结果；
13. 重复装饰同一 Port 在装配期失败；
14. state、plan、receipt 不含 secret；
15. 策略图的每个 status 分支都有 deterministic recovery test。

## 13. 实施顺序

1. 先实现类型化的 failure/outcome、profile/binding、plan 和纯 policy，不接具体 provider；
2. 实现固定的五节点单 Port nested Graph 组装；
3. 用一个可控的测试 Port 验证 profile 继承、覆盖、禁用、retry、budget、config snapshot 和 crash recovery；
4. 接入 Runtime 的 receipt/reconcile Port，验证 `Unknown` 语义；
5. 再接入真实的 Model/Service Port；
6. 最后决定 `RetryContext` 是完全由 publication/codec 承载，还是需要向 `GraphRunState` 增加最小字段。

策略图不变、参数可热加载、每次重试进入同一 Graph/state 提交边界，是本设计的核心不变量。
