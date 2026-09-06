# Observe 子图实施设计：两个业务节点、一个共享 Hook 与 ReAct 路由

最后更新：2026-09-06

状态：**Observe v1 实施完成**。本文冻结 `mote_kernel.observe` 的 v1 图契约和实现边界；
ReAct 顶层图及其父级跳转策略仍由 ReAct owner 负责，不在本次实现范围内。
`ReAct` 是唯一顶层 Graph；`Observe`、`Think`、`Act` 是 ReAct 的业务节点，其中
`Observe` 是一个 nested Graph。Observe 不拥有 ReAct 的顶层路由，也不把每个纯函数步骤
拆成 Graph 节点。

Graph 节点的判断标准是：它必须是需要独立状态、恢复、重试、取消、资源控制或审计的最小
业务块。Observe 不解释消息、不生成父图路由；`ObservationQueuePort` 在一个明确的读取边界
内返回完整 FIFO observation batch，不按类别截断。Observe v1 有两个业务状态节点：
`get_observation` 负责取得观察，`write_observation` 负责通过 capability Port 写入观察值；
每个业务节点成功后都进入同一个共享 Hook。按 Graph 直接节点计数是三个（两个业务节点
和一个共享 Hook），按业务状态节点计数是两个。共享 Hook 在一次 Observe activation 中
按阶段激活两次：第一次把取得的观察送回写入节点，第二次把写入后的结果送到 Observe 结束。

## 0. 固定结论

### 0.1 层级关系与节点计数

```text
ReAct（唯一顶层 Graph）
├── observe：ObserveNode（一个 nested Graph）
│   ├── get_observation：取得观察状态节点
│   ├── write_observation：观察值写入状态节点
│   └── hook：共享 HookNode（两次激活）
├── think：ThinkNode（nested Graph）
├── act：ActNode（nested Graph）
├── route：ReActRoute（顶层路由）
└── await_observation：顶层等待边界
```

Observe 子图的**业务状态节点恰好两个，直接 Graph 节点共三个**：

| 节点 | 是否为独立状态边界 | 责任 |
| --- | --- | --- |
| `get_observation` | 是 | 从 `ObservationQueuePort` 取得完整 FIFO batch 和同 revision 后台任务快照，验证非 Config family 互斥，构造内部观察 frame；不截断、不写 Context |
| `write_observation` | 是 | 消费取得观察后的内部 frame，通过 Config/Context Port 一次完成写入，生成四值 `ObservationKind` 和 settlement receipt |
| `hook` | 是（nested Hook） | 在两个业务节点之后分别执行 `Plan -> P1 -> P2 -> P3`；第一次回到写入节点，第二次结束 Observe |

共享 Hook 内部的 `plan`、`p1`、`p2`、`p3` 是 Hooks owner 的内部节点，不计入 Observe
的三个直接节点。`START`、`END`、ReAct 的 `route` 和 `await_observation` 也不属于
Observe 直接节点。

### 0.2 Observe 子图的固定形状

```text
START -> get_observation -> hook
hook -- WRITE_OBSERVATION --> write_observation -> hook
hook -- END --> ObserveGraph.END
```

`get_observation` 内部的 receive、family validation 和 frame construction，以及
`write_observation` 内部的 Config/Context settlement、enum wrapping，都是各自节点内的
有序纯/Port 子步骤，不再拆成更多节点。两个业务节点都直接进入同一个共享 Hook；Graph 根据
`HookResult.node_id` 选择编译期固定的写入边或结束边，Hook 脚本本身不选择边。这里没有隐式
Join、截断或额外 route 节点。Observe 的唯一领域结果是 `ObserveResult`；Graph 层的唯一 nested
output 是共享 Hook 第二次激活产生的 `HookResult`，其中
`WriteObservationStageValue.result == ObserveResult`。顶层 ReAct 解包并校验该结果后决定下一条边。

如果未来某个子步骤变成长时、可独立恢复、需要独立重试/取消/审计，它才可以提升为新的
状态节点；提升后必须定义自己的 stage 并接入现有共享 Hook，保持“业务节点 -> 共享 Hook”的
前驱绑定形状，不能预先为每个纯步骤建节点，也不能复制 Hook。

### 0.3 Observe 只返回当前状态，ReAct 定制跳转计划

Observe 不认识 `Think`、`Act` 或 ReAct 的任何节点名。它不把 observation 翻译成业务动作，
只返回一个描述当前观察类别的四值 enum；原始 batch 只留在 Observe 内部 frame 和 Port
receipt 中：

```text
ObserveResult
  └── current_state: ObservationKind
        ├── CONFIG
        ├── TOOL
        ├── USER
        └── ASSISTANT

ReActPolicy(ObserveResult)
  └── ReActJumpPlan（由父图定制）
        ├── RUN_THINK
        ├── RUN_ACT
        ├── REOBSERVE
        ├── WAIT
        └── END
```

`ObservationKind` 是一次 nested activation 对原始 observation batch 的 nominal 分类，不携带
`user₁`、`user₂` 等 payload，也不是第二个 `ObserveState`/store。原始 payload 已由写入节点
交给对应 Port；ReAct 根据 enum 和自己的组合方式定制 `ReActJumpPlan`，再决定顶层边。Observe
只能用 `ObserveGraph.END` 结束自己的
nested activation；它不产生顶层 `Graph.END`，不读取或修改 ReAct 的顶层边，也不解释
Think/Act 的内部阶段。

本文把“跳转计划”严格限定为包含父图节点/边 token 的控制决定。Observe 可以携带
observation 中的完成标记或任务事实，但不能把它们改写成绑定 Think/Act 的
Observe-owned 跳转计划类型。

### 0.4 结束候选遇到后台任务时必须作废

以下规则不可被实现细节改变：

1. ReAct 根据 `ObservationKind.ASSISTANT`（以及父图可用的 typed completion evidence）形成的一次
   `CompletionCandidate` 不是 terminal state，不能直接提交
   `GraphRunStatus.COMPLETED`；任何由 ReActPolicy 产生的**正常 END 候选**都必须经过同一
   blocking-task gate，而不只检查这一种状态。
2. `get_observation` 先从 `ObservationQueuePort` 取得完整读取边界，再通过
   `BackgroundTaskPort` 在同一 `observation_revision` 下取得不可变后台任务快照；ReAct
   只检查最终 `ObserveResult.current_state` 和 `background_task_snapshot`。
3. 快照中仍有 blocking task 时，ReAct 不得跳转顶层 END，也不得保存一个可恢复的
   “待结束决定”。
4. ReAct 进入可恢复等待，通过 `ObservationQueuePort` 等待当前 cursor 之后的新 delivery。
5. 唤醒后旧 `CompletionCandidate` 永久作废；必须重新进入 Observe，读取新 observation 并
   生成新的 `ObserveResult`，再由 ReActPolicy 重新决定。
6. 只有无 blocking task 且 revision fence 通过时，ReAct 才能把**这一次**正常候选转成
   顶层 END。

```text
ReAct CompletionCandidate
    │
    ├── background_task_snapshot.blocking_tasks == ()
    │       └── ReActRoute -> Graph.END
    │
    └── background_task_snapshot.blocking_tasks != ()
            └── AwaitObservation(after_cursor)
                    └── 新消息唤醒
                            └── 丢弃旧候选
                                    └── Observe -> 新 ObserveResult -> ReActPolicy
```

阻塞是 Graph interrupt/resume 边界，不是进程内永久 `Queue.get()`。wake 只提示有状态
变化；恢复后必须通过 `ObservationQueuePort` 从 durable stream 按 cursor 重读，不能把 wake payload 当作业务消息。

### 0.5 Capability Port 持有事实，Role 只负责装配

队列、delivery、Config 状态、Context 内容和后台任务分别由 capability Port 的 provider
持有或操作。
Role/Control 只负责身份、生命周期以及这些 Port 的装配；它不持有消息池、Context 存储或
后台任务注册表。Observe 不持有 Role facade、不创建第二个 mailbox、不保存可变队列，也不
直接修改任何 provider 的存储。assembly 向 Observe 注入以下窄接口：

```text
ObservationQueuePort    -> 按 cursor 原子读取 FIFO batch，并提供 wait coordinate
BackgroundTaskPort      -> 按 observation boundary 返回不可变后台任务 snapshot
ConfigObservationPort   -> 对 Config batch 做幂等覆盖/settlement（不改写 observation）
ContextObservationPort  -> 将 Tool/User/Assistant batch 按原顺序一次写入 Context
ObservationAckPort      -> 在 Graph settlement 确认后幂等确认本次 batch 的 deliveries
ObservationResumePort   -> 编解码 Graph interrupt/resume 所需的 ObserveRequest
```

注入 `get_observation` 的 `ObservationQueuePort` 只暴露 read/wait facet；append/publish facet
只给外部 producer/adapter，Observe 不能自行向消息队列写入或伪造 delivery。

ReAct 仍拥有结束 gate，但不绕过 Observe 再查询后台任务 provider；结束 gate 使用 Observe
携带的 immutable snapshot 和 observation revision fence。

## 1. 范围与非目标

### 1.1 v1 必须交付

- 两个且仅两个 Observe 业务状态节点：`get_observation` 和 `write_observation`；
- 一个真实、注入的共享 `HookNode`：`hook`；两个业务节点都把结果送入该实例，Hook 按
  stage 选择下一条固定边；
- `get_observation` 内的 receive/family validation/frame construction，以及 `write_observation`
  内的 Config/Context settlement/enum wrapping，均保持在各自节点内；
- `config`、`tool`、`user`、`assistant` 四类 closed typed observation；Control、后台任务和
  Think/Act 结果在各自 Port/adapter 边界归入这四类，不在 Observe 增加第五类；
- cursor、delivery、task incarnation、observation revision 和 wait identity；
- queue 读取、后台任务快照、Config settlement、Context batch 写入和 delivery ack 的窄 Port；
- `CompletionCandidate`、blocking task、等待、唤醒、作废和重新 Observe 的确定性语义；
- Graph interrupt/resume、重复投递、旧 cursor、取消、revision fence 和 commit 边界测试；
- `ObserveNode` 作为 `mote_kernel.observe` 唯一包级图入口，被 ReAct 作为一个 nested node 使用。

### 1.2 明确不做

- 不把 receive、family validation、Context write、Config settlement、enum wrapping 各组装成独立 Graph 节点；
- 不实现 ReAct 顶层图、Think、Act 或它们的 Hook；本文只定义 typed handoff；
- 不在 Observe 内创建 route 节点、wait 节点、runner、session、线程、后台 task 或轮询循环；
- 不实现各 capability Port provider 的 mailbox、task registry、subscriber、调度器或持久化后端；
- 不把 `CompletionCandidate` 直接改写成 `GraphRunStatus.COMPLETED`；
- 不在 Observe 内决定 Think、Act 或顶层 END；
- 不解释、累计、去重或 apply Hook commands；
- 不使用 `Any`、裸字典、反射、字符串 discriminator 或第二个 state/runner/store；
- 不为 receive、validation、pass-through 等短步骤创建额外 Hook 或兼容参数。

## 2. Owner 边界

```text
┌────────────────────────────────────────────────────────────┐
│ Role / Control assembly                                     │
│ 身份、生命周期、Port 装配                                    │
└───────────────────────────┬────────────────────────────────┘
                            │ narrow typed Ports
                            ▼
┌────────────────────────────────────────────────────────────┐
│ Port providers                                               │
│ Queue/FIFO · Context · Config · BackgroundTask · Ack        │
│ （物理消息池、Context 内容和任务注册表均在各 provider 内）     │
└───────────────────────────┬────────────────────────────────┘
                            │ typed observations/snapshots
                            ▼
┌────────────────────────────────────────────────────────────┐
│ ObserveGraph（nested）                                      │
│ get_observation -> hook                                     │
│ write_observation -> hook                                   │
│ （两个业务节点都进入同一个共享 Hook）                        │
└───────────────────────────┬────────────────────────────────┘
                            │ 一个 nested output
                            ▼
┌────────────────────────────────────────────────────────────┐
│ ReAct                                                        │
│ 校验 ObserveResult，检查其后台任务快照，决定 Think/Act/Wait/End │
└───────────────────────────┬────────────────────────────────┘
                            │ Graph / GraphRunState / Commit
                            ▼
┌────────────────────────────────────────────────────────────┐
│ execution + state                                           │
│ 唯一 runner、publication、interrupt/resume、reducer、commit   │
└────────────────────────────────────────────────────────────┘
```

`get_observation` 是 Observe 对 `ObservationQueuePort` 和 `BackgroundTaskPort` 的读取
ingress：一次执行取得完整 FIFO batch、delivery evidence 和同 revision task snapshot，
不截断。`write_observation` 是对 Config/Context capability Port 的唯一写入 ingress：它
按 batch 一次完成 Config 应用或 Context 追加，再生成四值 `ObservationKind`。Config 的
覆盖只作用于 Config batch 内的旧 config；Tool/User/Assistant batch 不被 Config 删除、跳过
或标记为 superseded。Observe 两个业务节点和 settlement Port 默认不解释或改写 payload；共享
Hook 可以按第 6 节的有界规则改写当前 stage payload。ReAct 只消费最终 Observe nested output
中的 enum、receipt 和 snapshot，不重新拼接 queue/task 事实。

各 Port provider 的事实更新、delivery ack 和 Graph settlement 的原子关系由 composition
root 与统一 commit assembly 提供。Observe 不直接改 `GraphRunState`，也不直接修改任何
provider 的 Python 内存快照。

## 3. ObserveGraph 固定拓扑

### 3.1 两个业务节点、一个共享 Hook

```text
START -> get_observation -> hook
hook -- AFTER_GET_OBSERVATION --> write_observation -> hook
hook -- AFTER_WRITE_OBSERVATION --> ObserveGraph.END
```

`get_observation` 的成功 output 是 stage 为 `AFTER_GET_OBSERVATION` 的 `HookRequest`，
送入唯一共享 `hook`；共享 Hook 第一次激活后，Graph 将通过 transition admission 的
after-get envelope 交给 `write_observation`。`write_observation` 的成功 output 是 stage 为
`AFTER_WRITE_OBSERVATION` 的 `HookRequest`，再次送入同一个 `hook`；共享
Hook 第二次激活后沿该 route 边结束 Observe。共享 Hook 的最终 `HookResult` 是 Observe 的
唯一父图 output。
两个业务节点共用一个 Hook 实例，但每次激活的 predecessor 由
Graph 的 typed predecessor handle 精确绑定，不读取错误的前驱值；Hook P3 的 `result`
publication 由 Graph 的 `output_ref("hook", "result")` 统一解析。

空消息或读取 `Conflict` 都不产生成功 output：`get_observation` 分别返回统一
interrupt/wait 或 fail-closed failure 边界，或按 ReAct assembly 约定直接交给顶层
`await_observation`。只有 `Available` 成功读取并完成读取节点后才进入共享 `hook`，再进入
写入节点并再次回到共享 `hook`。

### 3.2 Assembly 形状

以下伪代码只冻结 Graph bindings；具体 DTO、纯函数和 Port 实现由后续模块提供。`hook`
必须是 composition root 预先装配的唯一真实 `HookNode`，拥有一个 `HookSlotId`，并在一次
Observe activation 中被激活两次。

~~~python
observe = Graph[HookGraphValue](definition_id, version=version)
request_ref = Graph.graph_input("request", ObserveRequest)
request_binding = Graph.bind("request", request_ref)

get_output = observe.add_node(
    "get_observation",
    get_observation,
    inputs=(request_binding,),
    input_type=ObserveRequest,
    materialize=lambda values: values.get(request_binding),
    output_name="hook_request",
    output_type=HookRequest,
)
observe.add_node(
    "hook",
    hook,
    inputs={"request": Graph.node_output(get_output)},
)

# Hook 的 P3 result descriptor 由 Graph 统一解析；Observe 不读取 Hook 私有 P3 API。
hook_result_ref = observe.output_ref("hook", "result")
hook_result_binding = Graph.bind(
    "hook_result",
    Graph.node_output(hook_result_ref),
)
observe.add_node(
    "write_observation",
    write_observation,
    inputs=(hook_result_binding,),
    input_type=HookResult,
    materialize=lambda values: values.get(hook_result_binding),
    output_name="hook_request",
    output_type=HookRequest,
)
observe.add_edge("get_observation", "hook")
observe.add_edge("hook", "get_observation", "write_observation")
observe.add_edge("write_observation", "hook")
observe.add_edge("hook", "write_observation", Graph.END)
observe.set_outputs({"result": hook_result_ref})
~~~

这里没有额外的 `route` 节点：共享 Hook 原样传递业务前驱的 `HookRequest.node_id`，Hook
完成时将同一个值放入 `HookResult.node_id`；Observe 在编译期声明以
`get_observation`、`write_observation` 为 route token 的两条 conditional edge。这个 token
只服务于 Observe 这个直接父图，不是 Hook 脚本可修改的字段，也不是顶层跳转计划；当前观察
类别仍只作为 `ObservationKind`，不携带 Think/Act 节点名。父 ReAct 在收到 nested result 后，
才根据 enum 和自己的策略生成并执行顶层跳转计划。

### 3.3 节点升级规则

以下两个业务节点内的纯/Port 子步骤只有在满足独立状态边界时才允许新增节点；新增业务
节点必须接入现有共享 Hook（并定义自己的 stage），而不是复制 Hook：

| 条件 | 例子 | 必须得到 |
| --- | --- | --- |
| 独立状态/恢复 | 长时间 Port settlement 有自己的 receipt 和恢复坐标 | 独立 settlement 和 resume admission |
| 独立副作用/重试 | 非幂等外部命令需要单独 CAS/对账 | settlement identity 与 receipt |
| 独立取消/资源/审计 | 子步骤需要自己的 lease 或审计记录 | 独立 GraphRunState 边界 |

升级后的节点必须返回共享 Hook 可接收的 exact `HookRequest` 并连接到共享 Hook；纯转换
不满足条件时不能新增节点或复制额外 Hook。

## 4. Typed contract

### 4.1 Observation family 与 delivery

`ObservationQueuePort` 暴露一条逻辑 observation stream；物理队列由该 Port 的 provider
持有。Observe 的 closed family 只有四个 exact nominal variants：

```text
Observation
├── ConfigObservation
├── ToolObservation
├── UserObservation
└── AssistantObservation
```

四类的归属由 queue/config adapter 在入队时确定：配置/控制面消息归
`ConfigObservation`；
工具调用、工具结果和后台工具任务事实归 `ToolObservation`；用户 query 归
`UserObservation`；Think/Act 等 child 的对外结果先擦除节点语义，再归
`AssistantObservation`。Observe 不导入 Think/Act，不解析 wire message，也不增加第五类。

每个 Observe definition/version 在 assembly 时绑定四个 exact observation payload class；
不得用 `object`、union、裸字典或 `Any` 把不同类别偷偷拼在同一边界里。Observe admission
证明的是 exact nominal class，而不是通过运行时反射自动证明任意对象的深不可变性；每个
concrete payload 的字段及内部对象不可变性由该 payload owner 在 composition root 保证。

每条 delivery 都有不可变 identity：

```text
ObservationDelivery
├── delivery_id
├── stream_id
├── cursor_before
├── cursor_after
├── payload: Observation
└── observation_revision / source_receipt
```

`delivery_id` 必须稳定、可幂等；`cursor_after` 单调递增且属于同一 stream。Control 的
wire message 先在 Port provider 的 adapter 边界转换为 nominal observation；Observe 不
解析 wire bytes。

### 4.2 Background task snapshot

`get_observation` 先从 `ObservationQueuePort` 取得读取边界，再通过
`BackgroundTaskPort` 在该边界取得不可变活动快照。后台任务注册表和生命周期事实属于
`BackgroundTaskPort` provider，不属于 Role：

```text
ObservationBoundary
├── stream_id
├── cursor_before
├── cursor_after
└── observation_revision

BackgroundTaskSnapshot
├── observation_revision
└── blocking_tasks: tuple[BlockingTaskRef, ...]
```

`BackgroundTaskPort.snapshot(boundary)` 必须回传与 boundary 相同的
`observation_revision`；若 provider 无法提供同一边界的快照，返回 typed revision mismatch，
不得让 Observe 拼接两个时间点的事实。

`ObservationKind` 是 Observe 对外返回的唯一观察值 enum；它不携带 payload：

```text
ObservationKind
├── CONFIG
├── TOOL
├── USER
└── ASSISTANT
```

`ObservationRead` 是 closed result，不返回裸 `None`。`ObservationQueuePort` 返回的是一个
完整、原子、按 FIFO 排列的 batch，而不是一个被覆盖后的“最后值”；Observe 不在第二种
family 出现时截断 batch：

```text
ObservationBatch
├── ConfigBatch
│   ├── deliveries: tuple[ConfigObservation delivery, ...]  # FIFO
│   └── effective: ConfigObservation                         # batch 中最后一条
├── ToolBatch
│   └── deliveries: tuple[ToolObservation delivery, ...]     # FIFO
├── UserBatch
│   └── deliveries: tuple[UserObservation delivery, ...]     # FIFO
└── AssistantBatch
    └── deliveries: tuple[AssistantObservation delivery, ...] # FIFO

ObservationRead
├── Available(batch: ObservationBatch, boundary: ObservationBoundary)
├── Empty(wait: ObservationWait, boundary: ObservationBoundary)
└── Conflict(conflict: ObservationConflict, boundary: ObservationBoundary)
```

```text
ObservationConflict
├── deliveries: tuple[ObservationDelivery, ...]
└── families: tuple[NonConfigObservationFamily, ...]  # 至少两类
```

`NonConfigObservationFamily` 是只含 `Tool`、`User`、`Assistant` 三个 nominal member 的
封闭类型；它不是可由调用方填写的字符串 discriminator。

`ObservationConflict` 只表示一个完整 batch 同时包含多个非 Config 类别；它不是第五种
observation。Tool/User/Assistant 在一个 batch 内必须互斥；如果 queue provider 返回混合
family，Observe 直接返回 `Conflict`，不截断、不部分写入、不推进 cursor、不 settlement、
不 ack，也不进入任何 Hook。要分开处理，producer 必须提交独立的 FIFO batch，而不是由
Observe 偷截断。

`ConfigBatch` 中旧 config 只在 config 自己的配置状态里被后来的 config 覆盖；被覆盖的
config delivery 仍须通过 typed receipt 留痕。Config 不会覆盖、删除或跳过 Tool/User/
Assistant delivery。一个合法 batch 中的非 Config payload 按原顺序一次交给
`ContextObservationPort`；不构造联合 payload，也不改变 concrete class/字段。

成功的 `ObserveFrame` 和最终 `ObserveResult` 都必须携带同一
`BackgroundTaskSnapshot`。后续纯子步骤只能沿 frame 读取；不能在节点中途重新查询
`BackgroundTaskPort` 或拼接第二份集合。若 Config/Context Port 的 settlement 推进
`observation_revision`，immutable settlement receipt 必须由 Port provider/atomic assembly
同时带上 successor boundary 和匹配的 successor `BackgroundTaskSnapshot`；Observe 以该
receipt 替换 frame 中的旧 snapshot，而不是发起第二次 live query。

`settlement_boundary` 的 successor 形状是严格的：如果 settlement 没有推进
`observation_revision`，它必须与 `read_boundary` 完全相同；如果 revision 前进，只能把
`cursor_before` 和 `cursor_after` 都锚定在本次 read 的 `cursor_after`，形成零长度 successor。
它不能在没有 delivery evidence 的情况下把 cursor 推到更远位置，也不能保留旧的读取范围。

### 4.3 Cursor、任务和 wait identity

```text
ObservationCursor
  stream_id + sequence

BlockingTaskRef
  task_id + incarnation + completion_policy

ObservationWait
  stream_id + after_cursor + observation_revision + wake_condition

WaitRegistration
  wait_id + stream_id + after_cursor + registration_revision

RevisionFence
  observation_revision + scope + commit_token

DeliveryAckReference
  stream_id + delivery_ids + settlement_id
```

`BlockingTaskRef` 是集合元素而不是计数器。旧 incarnation 的完成消息不能移除新
incarnation；detached/daemon task 不进入 blocking 集合。`ObservationWait` 只描述恢复
坐标和唤醒条件，不保存 Python `Event`、future 或 task。

### 4.4 默认观察处理、观察 enum 和父图 result

`get_observation` 不解释 observation，也不截断 queue 返回的完整 batch；它只把原始
concrete payload 放入内部 `ObserveFrame`。随后
`write_observation` 按 frame 调用对应的 Config/Context Port：Config batch 按 FIFO 应用，
非 Config batch 按 FIFO 一次写入 Context，并在写入结算后计算四值 `ObservationKind`。任何
Port 都不得替换 payload。共享 Hook 是唯一允许改写 stage payload 的扩展边界；其改写必须通过
第 6 节的 same-stage/read-only-state/exact-type admission，改写后的 payload 才成为下一业务节点
消费的事实。

Observe 对父 ReAct 暴露的当前观察值只有 enum：

```text
ObservationKind
├── CONFIG
├── TOOL
├── USER
└── ASSISTANT
```

不返回携带 `user₁`、`user₂` 等 payload 的 state variant。原始 payload 只存在于
本次 activation 的内部 frame、Context/Config settlement receipt 和 delivery evidence 中；
`CompletionCandidate` 是 ReAct 根据自己的事实和这个 enum 形成的父图候选，不是 Observe 的
第五个 state variant。父图自己的 `ReActPolicy` 再将 enum 定制为本图的 `ReActJumpPlan`，
例如决定运行某个节点、重新观察、等待或结束。

父 ReAct 看到的最小 typed result 为：

```text
ObserveResult
├── current_state: ObservationKind
├── delivery_ids: tuple[DeliveryId, ...] / cursor_range: CursorRange
├── background_task_snapshot: BackgroundTaskSnapshot
└── observation_receipt: ObservationBatchReceipt
```

`ObserveResult` 是一次 nested activation 的 immutable projection，不是第二个 state model、
runner 或 store。ReAct 不得从 result 之外再查询或拼装 queue/task 事实，也不能把自己的
跳转计划反向写入 Observe。

`ObservationBatchReceipt` 同时记录本次 batch 内的 Config 更新 receipt 和（若存在）Context
批量写入 receipt；它只用于确认 delivery 与恢复，不改变 `current_state` 的四类代数。

```text
ObservationBatchReceipt
├── read_boundary: ObservationBoundary
├── settlement_boundary: ObservationBoundary
├── config_receipt: ConfigSettlementReceipt | None
├── context_receipt: ContextAppendReceipt | None
└── ack_reference: DeliveryAckReference
```

其中 `None` 只表示本次 batch 没有对应 Config/Context settlement；它不是缺失 Port 或未
处理 delivery 的暗号。

### 4.4.1 观察策略（不产生父图跳转计划）

“观察策略”只回答一个问题：在给定 cursor 和 observation revision 下，Observe 如何从
`ObservationQueuePort` 返回的完整 observation batch 中确定 enum 并完成 Port 写入。它不是
`ReActPolicy`，不包含 Think、Act、Wait 或顶层 END 的节点选择。

策略分层固定如下，避免把“取什么”“返回什么”“下一条边是什么”混成一个可变回调：

| 层 | owner | 产物 | 是否认识父图节点 |
| --- | --- | --- | --- |
| FIFO batch selection | `ObservationQueuePort` provider | 完整 FIFO observation batch + delivery evidence | 否 |
| Config/Context settlement | `write_observation` + 窄 Port | 原 concrete payloads + receipts | 否 |
| current-state enum | `write_observation` | `ObservationKind` | 否 |
| jump routing / completion gate | ReAct | `ReActJumpPlan` 或 END/WAIT | 是 |

v1 的默认业务策略固定为“完整 FIFO 批量、观察到什么就返回什么”。这里的优先级是：只要完整 batch
存在 `ToolObservation`、`UserObservation` 或 `AssistantObservation`，就把对应 enum 作为
`current_state` 返回；只有 batch 完全没有非 Config delivery 时，才返回 `CONFIG`。Config 的
覆盖只发生在 Config 自己内部（后来的 config 覆盖较早的 config），不会覆盖或淘汰其他类型。

大白话：先看完整 batch 里有没有 Tool/User/Assistant；有就把这一类消息按 FIFO 一次交给
`ContextObservationPort`，并把对应 enum 返回；Config 仍通过 `ConfigObservationPort` 单独
应用。只有 batch 里全是 Config 时，才返回 `CONFIG`。所谓“覆盖”只让较早 Config 在有效
配置值上失效，低优先级的 Tool/User/Assistant 仍按 FIFO batch 操作并确认，绝不会被 Config
单独淘汰或标记为已处理。

1. **Queue Port 返回完整 batch。** `ObservationQueuePort.read_after(cursor)` 从 cursor
   开始读取一个完整、原子、同一 revision 的 batch；Observe 不因出现第二种 family 而截断、
   延后或丢弃任何 delivery。
2. **先校验非 Config 互斥。** 一个合法 batch 可以包含 Config 和一种非 Config family；
   Tool/User/Assistant 不能在同一个 batch 混合。若 queue Port 返回混合 family，直接返回
   typed `Conflict`，不推进 cursor、不调用任何 settlement Port、不 ack，也不进入 Hook。
3. **Config 与非 Config 一起结算。** batch 中有 Config 时，`ConfigObservationPort` 按 FIFO
   应用；有非 Config 时，`ContextObservationPort` 把该 family 的全部 payload 按 FIFO 一次
   写入 Context。Config 只在自身 batch 内覆盖较早 Config，不覆盖其他 delivery。
4. **enum 选择。** batch 含 Tool/User/Assistant 时，`current_state` 是对应的
   `ObservationKind`；只有 Config 时是 `CONFIG`。enum 不携带 payload，payload 只在内部
   frame/Port receipt 中保留。
5. **同类一起操作。** 一个 batch 内的同类 payload 保持原顺序，Context Port 一次提交完整
   tuple；不取最后一条、不合并字段、不改变 concrete class。Config batch 同理按 FIFO 应用。
6. **默认原样处理。** 两个 Observe 业务节点不解析 payload、不改变字段、不按来源改名；操作
   Port 只产生幂等 receipt，不能替换观察内容。若共享 Hook 显式返回合法的 same-stage payload
   rewrite，则后继节点使用已通过 transition admission 的改写值。
7. **空读转为可恢复等待。** 没有有效 observation 时只返回 `ObservationWait`（或由父图
   承接同一 wait coordinate），不生成 enum、不提交 cursor，也不创建常驻 `Queue.get()`
   task。wait registration 必须和一次原子 recheck 配对，避免“检查后、注册前”丢唤醒。
8. **在同一 revision 上取证。** 返回的 observation batch、delivery evidence 和
   `BackgroundTaskSnapshot` 必须来自同一 `observation_revision`；queue/task provider 若需
   多次存储读取，必须在 Port contract 内完成 revision fence。
9. **父图才决定跳转。** `ObservationKind` 不包含 route token、Think/Act 节点名或父图跳转
   计划。父 ReAct 收到 `ObserveResult` 后，才用自己的策略决定下一条边。

`background_task_snapshot` 是观察边界的证据，不是另一条输入流：任务快照只参与当前状态（尤其是
完成候选）的事实投影和父图结束 gate，不会单独触发 settlement、唤醒或
路由。后台任务的完成/失败/取消必须先由 task provider/adapter 通过
`ObservationQueuePort` 追加新的 `ToolObservation` 或 `ConfigObservation` delivery，再由
同一读取策略读取；它们不构成第五类 observation。

这个策略刻意选择“Queue Port provider 返回完整 FIFO batch，Config Port 只覆盖 Config，
Context Port 负责同类批量写入，Observe 只返回 enum”。它保留 cursor、ack、恢复和幂等边界；
Observe 内不增加隐式优先队列、截断逻辑或 route 分支。

多条 delivery 的决策可以写成一个不含业务解释的闭合函数。`F` 是完整 batch 中的非 Config
family 集合；queue provider 不在 Observe 内截断：

```text
W = ObservationQueuePort 在同一 revision 返回的完整未读 batch
C = { d ∈ W | d.payload 是 ConfigObservation }
F = W 中出现的非 Config family 集合（Tool / User / Assistant）

W = ∅                         -> Empty(wait)
C = ∅ 且 F = ∅               -> Empty(wait)
C ≠ ∅ 且 F = ∅               -> Available(kind=CONFIG, ConfigBatch(W))
|F| = 1                      -> Available(kind=family(F))，同时应用 ConfigBatch(C)（若 C 非空）
|F| > 1                      -> Conflict（cursor 不变，不截断）
```

最后一行是对 queue Port contract 的防线：Observe 不替 provider 拆批或截断；混合 family
必须作为完整 batch 的 Conflict 原子失败。

当 `|F| = 1` 时，batch 中的 Config deliveries 仍按 FIFO 应用并确认，`current_state` 只返回
该 family 的 enum；Config 不是被该 family 覆盖，只是没有成为本次 enum。只有 Config batch
内较新的 Config 才覆盖较旧 Config。所有 delivery 都必须在完整 batch settlement 后按同一
cursor/receipt 确认；不存在“截断处之后留待下一次”的隐式行为。

### 4.5 Frame、enum 与共享 Hook envelope

```text
ObserveFrame
├── batch (exact Config/Tool/User/Assistant batch)
├── boundary: ObservationBoundary
└── background_task_snapshot: BackgroundTaskSnapshot
```

`boundary` 表示本次 settlement 后可供父图继续读取的位置；若 settlement 没有推进 revision，
它就是 queue read boundary；若推进了 revision，则使用 receipt 携带的 successor boundary
及其匹配的 `BackgroundTaskSnapshot`。

Observe v1 只有一个共享 Hook；它通过同一个 outer envelope 承载两个阶段的闭合 payload：

```text
ObserveHookStage (closed enum)
├── AFTER_GET_OBSERVATION
└── AFTER_WRITE_OBSERVATION

ObserveStageValue
├── GetObservationStageValue
│   └── frame: ObserveFrame
└── WriteObservationStageValue
    └── result: ObserveResult

ObserveHookEnvelope
├── stage: ObserveHookStage
├── payload: ObserveStageValue
└── hook_state: concrete immutable HookStateProjection
```

`ObserveStageValue` 是闭合的 nominal base；`GetObservationStageValue` 和
`WriteObservationStageValue` 是它的两个 exact concrete member。两者都被包在同一个
`ObserveHookEnvelope` 中，不把 Receive、Validate、Operate、Projection 拆成额外 Graph value。
语义上的 `GetObservationHookRequest` 和 `WriteObservationHookRequest` 都是同一个运行时
outer class：`HookRequest[ObserveHookEnvelope, HookStateProjection]`；区别只在 envelope 的
stage/payload，不定义两个互不兼容的 Hook request class。

`ObserveHookStage.AFTER_GET_OBSERVATION` 和 `ObserveHookStage.AFTER_WRITE_OBSERVATION` 只是
共享 Hook 的两个内部阶段标记。Hook 不拥有 Observe 的拓扑；它只把业务前驱传入的
`node_id` 原样带到 `HookResult.node_id`。Observe 编译期把这两个合法业务身份绑定到自己的
conditional edge（`get_observation -> write_observation`、`write_observation -> END`）。
`node_id` 是直接父图的 opaque continuation token，同时保留 provenance；它不是 Hook
envelope 中可由脚本改写的字段，也不是顶层 ReAct 跳转计划。

共享 Hook 的 concrete binding 为：

```text
hook: HookNode[
    HookConfigT,
    PriorityConfigT,
    ObserveHookEnvelope,
    HookStateProjection,
    ObserveHookCommand,
]
```

共享 Hook 的 output 沿用同一个 Hook boundary：

```text
hook.result: HookResult[ObserveHookEnvelope, ObserveHookCommand]
  └── value: ObserveHookEnvelope
        ├── stage: AFTER_GET_OBSERVATION 或 AFTER_WRITE_OBSERVATION
        └── payload: 对应的 ObserveStageValue
```

Hook command 对 Observe 不透明且不会被 Observe 隐式 apply。共享 Hook 的两次激活都允许
`HookStageResult.value` 在当前 business stage 内改写 payload；改写值必须通过完整 Observe DTO
admission。Hook 不得改变 business stage、只读 `hook_state` 或由原 request 固定的 `node_id`，
也不能取得顶层路由权。

## 5. 两个业务节点的内部流程

### 5.1 `get_observation`：取得观察

`get_observation` 一次 activation 内只负责读取和构造内部 frame；箭头表示同一节点内的
函数/Port 调用，不是 Graph edge：

```text
ObserveRequest.cursor
    -> ObservationQueuePort.read_after()
       （返回完整 FIFO batch，不截断）
    -> BackgroundTaskPort.snapshot(boundary)（仅在 Available 时）
    -> family validation（Tool/User/Assistant 互斥）
    -> ObserveFrame(batch + boundary + task snapshot)
    -> HookRequest(value=ObserveHookEnvelope(
         stage=AFTER_GET_OBSERVATION, ...))
```

空读返回 `ObservationWait` interrupt；混合非 Config family 返回 typed `Conflict`。这两种
结果都不进入 Hook、不写 Context、不 ack。`get_observation` 不调用 Config/Context Port，
也不确认 delivery；它只取得完整原始 batch，绝不按类别截断。

### 5.2 共享 `hook` 第一次激活：取得观察后的 Hook

共享 `hook` 第一次激活时只执行 Hooks owner 的固定 `Plan -> P1 -> P2 -> P3`。P1/P2/P3 可以
返回同 stage 的合法 payload rewrite；Observe transition admission 拒绝 stage 或只读 state
变化。随后由 `HookResult.node_id == "get_observation"` 沿 Observe 声明的 conditional edge
把 admitted frame 交给 `write_observation`。Hook 不写 Port，也不生成顶层父图路由计划。

### 5.3 `write_observation`：观察值写入

`write_observation` 消费共享 Hook 第一次激活返回的 `ObserveFrame`，按以下顺序完成 settlement：

1. batch 有 Config 时，调用 `ConfigObservationPort.apply(config_batch)`；同一 Config batch
   内后来的 config 覆盖较早 config；
2. batch 有唯一非 Config family 时，调用 `ContextObservationPort.append(non_config_batch)`，
   一次写入该 family 的全部 FIFO payload；
3. 没有非 Config 时把 `ObservationKind` 设为 `CONFIG`，否则设为唯一非 Config family 对应
   的 enum；
4. 生成 `ObservationBatchReceipt`，若 settlement 推进 revision，receipt 一并携带 successor
   boundary 和匹配的 successor `BackgroundTaskSnapshot`；
5. 保留当前 admitted frame 中的 delivery/boundary/payload，构造阶段为
   `AFTER_WRITE_OBSERVATION` 的 `HookRequest`；HookResult 携带
   `node_id == "write_observation"`，Observe 的 conditional edge 将该结果送到 `END`。

Config 与 Context Port 是两个独立调用，但属于同一个 `write_observation` settlement；不能
因为 enum 选择了非 Config 就省略 Config 应用，也不能因 Config 覆盖而淘汰其他 delivery。
若两个 Port 都返回 successor boundary，它们必须指向同一个 boundary；不能从两个不同时间点
拼出一个“较新”的结果。若 settlement 推进 revision，provider 必须在对应 receipt 中同时给出
匹配的 `BackgroundTaskSnapshot`。
`write_observation` 不重新读取 queue/task Port，不确认 delivery；Graph commit 确认后才由
`ObservationAckPort` ack 完整 batch。
`ObservationBatchReceipt` 至少要证明每个 Config/Context 子 receipt 在总 ack reference 中
保持自己的 FIFO 相对顺序；Config 与 Context 之间的交错顺序由本次 batch 的原始 delivery
序列在 `write_observation` 中保留。所有 delivery-id 集合沿同一 admission 上限校验，不能
通过 Conflict 或 receipt 形状绕过 batch 限制。

### 5.4 共享 `hook` 第二次激活：观察值写入后的 Hook

共享 `hook` 第二次激活时执行同一固定 Hook pipeline，并可在
`AFTER_WRITE_OBSERVATION` stage 内返回合法 payload rewrite；改写后的 `ObserveResult` 必须重新
通过 delivery/cursor/完整 task snapshot/receipt 的 DTO admission。它不等待消息、不检查后台
任务，也不取得顶层路由权。第二次激活成功后 Observe child 沿固定结束边结束。

## 6. 共享 Hook 契约

### 6.1 一个共享 Hook、一个 slot

Observe assembly 只接受一个真实 `HookNode` 参数 `hook`。两个业务节点都连接到这个实例，
共享 Hook 在一次 Observe activation 中激活两次。唯一 `HookSlotId` 必须满足：

```text
definition_id      == Observe definition_id
definition_version == Observe version
node_id            == "hook"
stage              == HookStage.AFTER_NODE
```

所有 capability、slot、admission、codec 和 identity 校验必须在第一次
`Graph.add_node()` 前完成；失败时不返回半成品 Graph。不能传入普通 Graph、普通 callable
或为纯子步骤复制 Hook。共享 Hook 的 predecessor-bound request 必须来自
`get_observation` 或 `write_observation` 的当前激活，不能固定绑定某一个前驱。

Observe 还必须在 assembly 比较共享 Hook 的公开 `payload_admission`：value type 必须是 exact
`ObserveHookEnvelope`，state/command type 必须与 Observe admission 的 concrete binding 相同，
`transition_admission` 必须就是同一个 `ObservePayloadAdmission` 实例。任一项不匹配都必须在
Graph builder 被修改前 fail-closed。

### 6.2 Stage/identity 不变量

Observe v1 只允许：

```text
get_observation -> hook(stage=AFTER_GET_OBSERVATION)
hook -- WRITE_OBSERVATION --> write_observation -> hook(stage=AFTER_WRITE_OBSERVATION)
hook -- END --> ObserveGraph.END
```

Hook admission 必须拒绝：

- 缺少或替换对应 Hook envelope 的 outer class；
- `stage` 与 payload concrete class 不匹配，或 `HookResult.node_id` 不是对应业务前驱身份；
- 把当前 business stage 改成另一 stage，或让 payload class 与当前 stage 不匹配；
- 改变只读 `hook_state` 的 concrete class/value；
- 返回未通过对应 frame/result、delivery、cursor、boundary、task snapshot 和 receipt 不变量的
  payload；
- 把 envelope 的 stage 或 payload 伪装成顶层 Graph route；
- 返回非绑定 exact concrete class 的 Hook command；
- 让 Hook command 变成 Observe 的隐式状态更新。

Hook admission 明确允许：在 stage 不变、只读 state 不变且所有 DTO 不变量成立时，
`HookStageResult.value` 用同 stage 的另一个合法 payload 替换当前 payload。该改写值是后继业务节点
或最终父图实际消费的值，不要求与进入 Hook 前逐字段相等。`node_id` 不属于脚本返回的
`HookStageResult`；`HookNode` 必须从原 request 原样构造最终 `HookResult.node_id`，所以 payload
rewrite 不能改变 Observe 已声明的边。

如果未来新增独立状态节点，必须定义新的 stage/payload 并接入这个共享 Hook，保持“业务
节点 -> 共享 Hook”的 predecessor-bound 形状；不得复制 Hook 或另建第二个共享 Hook。

### 6.3 Hook 的职责边界

共享 Hook 的两次激活都只按 Hooks owner 的固定 `Plan -> P1 -> P2 -> P3` 执行；它可以按正式
Hook 契约有界改写当前 stage payload，但不检查后台任务、不等待消息，也不越过 ReActRoute。
Hook 完成后只能由 Observe 编译期声明的 node-id conditional edge 沿固定后继继续，不能自行选择
或改写父图节点。Hook P3 最终 publication 的 descriptor 由 Graph 统一拥有并通过 `output_ref`
解析，Observe 不调用 Hook 私有阶段 API。

## 7. 消息池、投递和四类 observation

### 7.1 一个逻辑 stream，由 Queue Port provider 持有

`ObservationQueuePort` provider 持有 durable FIFO stream、delivery 和 cursor，并负责
wait coordinate 的原子 recheck/registration。Role/Control 不持有 mailbox、outbox、消息池
或 cursor；它们只在 assembly 时把 queue Port 和其他 capability Port 注入图。所有来源在
queue ingress adapter 边界归入四类：

```text
Config source (control/config/task lifecycle) -> ConfigObservation
Tool execution/result/task                   -> ToolObservation
User query                                   -> UserObservation
Assistant/child result                       -> AssistantObservation
```

wire schema、授权、lineage 和物理存储属于各自 Port provider/adapter；Observe 只消费已
通过 admission 的 nominal DTO。Think、Act 的 terminal settlement 由 ReAct/adapter 在
Observe 边界之外擦除其节点语义，转换成 `AssistantObservation`；Observe 不导入 Think/Act
包，也不根据 producer 名称选择父图边。后台任务生命周期事实由 `BackgroundTaskPort`
provider 持有；其完成/失败/取消/超时事件通过 queue ingress 映射到 Config 或 Tool，不新增
observation variant。Queue provider 必须维护 FIFO，并按原子读取边界返回完整 batch；Observe
不得在遇到另一种 family 时提前结束、截断或自动拆分 batch。一个 batch 可以包含 Config 与
一种非 Config family；Tool/User/Assistant 若在同一完整 batch 中出现两种或以上，Observe
返回 typed `Conflict`，不推进 cursor、不做 settlement、不 ack。若 producer 需要分次处理，
必须在 ingress 处提交独立的 batch/读取边界，而不是让 Observe 偷拆。Config 可以和该 batch
共存，但只更新 Config 状态，不覆盖其他 delivery。

### 7.2 Assistant/Tool 结果投递

任意 child（包括 Think/Act）或 tool 的终端结果不能在 Python wrapper 中直接塞进 Observe
队列。生产者必须通过 queue Port provider 的 ingress/append 能力写入 durable stream；可靠
顺序为：

```text
child settlement
    -> typed observation projection
    -> ObservationQueuePort provider 的 durable append / outbox
    -> Observe ObservationQueuePort.read_after(cursor)
```

delivery identity 必须由 child activation/settlement 坐标确定性生成；重复投递由 queue
provider/`ObservationAckPort` 幂等处理。best-effort notification 不能作为 Observe 的可靠
输入。

同一 batch 的 Tool/User/Assistant delivery 由 `ContextObservationPort` 一次追加到 Context，
追加顺序严格等于 delivery 的 FIFO 顺序。Config delivery 不因为当前返回的是非 config
state 而消失；它由 `ConfigObservationPort` 单独按 FIFO 应用，只有 Config batch 内较新
的 config 会覆盖较旧 config。

### 7.3 ack 顺序

```text
read delivery
  -> get_observation + hook + write_observation + hook settlement/commit confirmed
  -> acknowledge every delivery in the FIFO batch（幂等）
```

不能先 ack 再等待 Graph commit。已知未提交的 ack 失败时，下一次应按尚未确认的 delivery
坐标重读；delivery/observation identity 必须保证重复 settlement 安全。若 Graph candidate
已确认而 ack 的结果未知，恢复 owner 必须先用已持久化的 `ObservationSettlementReceipt` 做
receipt-based reconcile；不能
仅凭“cursor 已前移”再次执行非幂等 provider effect，也不能把未确认 ack 当成成功。实现
可以把 cursor 与 queue ack 放入同一原子事务；若两者分属不同 provider，则 `GraphRunState`
必须保留 pending ack reference，直到 `ObservationAckPort` 返回明确确认。

## 8. 等待、唤醒与结束作废

### 8.1 两种等待边界

1. `get_observation` 发现 cursor 后没有消息：这是“没有观察内容”的 transport wait，可由
   Observe interrupt 或 ReAct 顶层 `await_observation` 表达。
2. Observe 已返回完成候选，或 ReActPolicy 针对其他状态提出正常 END，但其
   `background_task_snapshot` 仍有 blocking task：这是顶层 ReAct wait，必须通过
   `ObservationQueuePort` 等待新的 delivery。

两者都不创建常驻 task；唤醒后都按 durable cursor 重新 `read_after()`。

### 8.2 结束 gate 的唯一顺序

ReAct 的 `ReActPolicy` 消费 Observe 最终 result 时，先读取当前状态，再按父图自己的
节点集合定制跳转计划；任何正常 END 候选都必须再经过 snapshot 的 blocking-task gate：

```text
observe_result.current_state
  ├── CONFIG    -> ReActPolicy（按 config 内容定制）
  ├── TOOL      -> ReActPolicy（按 tool 内容定制）
  ├── USER      -> ReActPolicy（按 user 内容定制）
  ├── ASSISTANT -> ReActPolicy（按 assistant 内容定制）
  └── any state for which ReActPolicy proposes END
        └── observe_result.background_task_snapshot.blocking_tasks
              ├── empty -> END（再过 observation_revision fence）
              └── non-empty -> WAIT(after current cursor)
```

ReActPolicy 不在路由过程中再次读取 `BackgroundTaskPort`；snapshot 是 Observe 在同一观察
边界获取并携带出来的 immutable evidence。`ReActPolicy` 可以把四类 observation 的原始内容
映射到本图的 Think、Act 或其他节点，但这些名字和规则不进入 Observe contract。等待
transition 只保存 cursor、observation revision/receipt 和 interrupt identity，不保存可复用
的 terminal plan。显式 abort/cancel
是独立的生命周期转换，由其 owner 负责清理；本文的 blocking-task gate 约束的是正常 END。

### 8.3 唤醒后的强制流程

```text
ReActRoute -> WAIT
    -> durable interrupt(ObservationWait(after_cursor))
    -> producer 通过 ObservationQueuePort provider 追加新 delivery
    -> host 精确 resume
    -> Observe.get_observation 调用 ObservationQueuePort.read_after(after_cursor)
    -> validation -> frame construction（get_observation 内部）
    -> hook（第一次激活）
    -> write_observation -> hook（第二次激活）
    -> 全新的 ObserveResult.current_state
    -> ReActPolicy 再次定制跳转
```

wake payload 只携带可验证的 cursor/receipt，不携带未经读取的业务 DTO。任何唤醒都使旧
`CompletionCandidate` 失效，即使新消息重复、无关或再次表示完成，也必须走完整 Observe。

后台任务完成、失败、取消或超时必须由 `BackgroundTaskPort` provider/adapter 通过
`ObservationQueuePort` 投递新的 `ToolObservation` 或 `ConfigObservation`；不能只减少
provider 内存计数，也不能让 Observe 轮询 task registry 自行醒来。没有新消息就继续等待。

### 8.4 并发与 revision fence

`background_task_snapshot` 是点时证据，不是永久锁。统一 commit/lifecycle owner 在提交
顶层 END 前必须以 `observation_revision` 做 fence/CAS（由 queue/task Port provider 提供）；
若 revision 失配、快照过期或 blocking task 集合已更新，拒绝 END，按 cursor 进入等待/重新
Observe；不允许 ReActPolicy 直接做第二次 live query 或复用旧 completion candidate。

如果 observation settlement 推进 observation revision，只有 settlement receipt 明确携带
successor boundary 和匹配的 successor `BackgroundTaskSnapshot` 时才能更新 frame；不允许
Observe 为此再次 live-query task Port。旧 task incarnation 的 terminal message 不能清除新
incarnation；Control cancel/terminate 仍先通过 adapter 映射为 `ConfigObservation` 再处理。

## 9. 状态、提交和恢复

Observe 不定义 `ObserveState`、`RoleState` 或私有 reducer。`ObserveFrame`、`ObserveResult`
和 `ObservationWait` 都是 immutable Graph values/边界 DTO；执行位置、publication、
interrupt、cursor evidence 和必要业务事实仍归唯一 `GraphRunState`/`Graph.Commit`。

一次成功 delivery 的最小提交闭包为：

```text
previous GraphRunState
  + get_observation/hook/write_observation/hook settlement
  + delivery/cursor evidence
  + Config settlement and/or Context batch append receipt
  + observation revision / routing facts
  -> one candidate state
  -> durable commit confirmed
  -> replace in-memory snapshot
  -> idempotent delivery ack
```

持久化必须先确认 candidate，再替换 Python memory snapshot。`CompletionCandidate` + blocking
tasks 只能提交可恢复的等待/观察位置，不能提交 `COMPLETED`。恢复按精确 scope、run、
definition/version、cursor、interrupt 和 codec 校验，不能用“最新消息”或内存队列绕过
admission。

如果某个 Port effect 已确认而 Hook、settlement 或 Graph commit 失败，observation/delivery
identity 必须支持 receipt-based 对账；Observe 不重放未知非幂等 effect，也不增加第二条
recovery path。

## 10. Port 与 assembly contract

```text
ObservationQueuePort
  read_after(cursor) -> ObservationRead  # 一个 FIFO batch + boundary
  register_wait(wait: ObservationWait) -> WaitRegistration  # atomic recheck

BackgroundTaskPort
  snapshot(boundary: ObservationBoundary) -> BackgroundTaskSnapshot

ConfigObservationPort
  apply(batch: ConfigBatch) -> ConfigSettlementReceipt

ContextObservationPort
  append(batch: ContextObservationBatch) -> ContextAppendReceipt

ObservationAckPort
  acknowledge(
      delivery_ids: tuple[DeliveryId, ...],
      receipt: ObservationBatchReceipt,
  ) -> DeliveryAck

ObservationResumePort
  encode_graph_input(values: Graph.Values[HookGraphValue]) -> bytes
  decode_graph_input(payload: bytes) -> Graph.Values[HookGraphValue]
  codec_id / codec_version
```

以上六个 capability Port 是 Observe 的完整装配契约；它们的具体 provider 可以相同，也可以
分离，但都必须通过 typed contract 连接，不能把 provider client 传入节点。最后一个
`ObservationResumePort` 是 Graph interrupt/resume 所需的持久化输入编解码能力，不能在运行
时才发现缺失。

`ObservationRead`、`ObservationBoundary` 和 `BackgroundTaskSnapshot` 必须是 closed typed
result；不能返回裸 `None`、可变集合或 provider/client 对象。Queue 与 task provider 必须在
同一个 `observation_revision` 上完成 boundary/fence；若底层需要多次存储读取，由 Port
provider 在内部完成原子 recheck，Observe 不得暴露或拼接第二个 task 查询。`ContextObservationBatch`
是只含 `ToolBatch`、`UserBatch`、`AssistantBatch` 三个 nominal member 的封闭类型；Config
batch 和 Context batch 的 delivery_ids 都必须从 queue 返回的 batch 导出，不能由 Observe
自己重建、跳过或改序。

缺少任一 Queue/Task/Config/Context/Ack/Resume Port、Hook 或 admission 时 assembly
fail-closed，不在运行时吞掉消息或跳到 END。Port 的 async/arity/return contract 由 strict
Protocol 在静态类型边界固定；assembly 做 capability/callability 校验，异步返回后再由 Observe
admission 校验 exact nominal result。Observe 不通过运行时签名反射复制一套 invocation owner。

## 11. 包结构

```text
src/mote_kernel/observe/
├── __init__.py       # 只导出 ObserveNode
├── node.py           # 两业务节点、一个共享 Hook 的 ObserveGraph assembly
├── contract.py       # 四类 observation、delivery、frame、settlement、Hook envelope
├── identity.py       # stream/cursor/delivery/task/wait/revision identity
├── admission.py      # exact outer/identity/size/stage/route admission
└── port.py           # Queue/Task/Config/Context/Ack/Resume capability Port Protocol
```

不创建 `receive.py`、`selection.py`、`settlement.py`、`route.py` 作为节点模块，
也不创建 `queue.py`、`observe/state.py`、`observe/runner.py`、`observe/reducer.py`。
若纯函数需要按 concern 组织，可放在 `node.py` 的 owner-internal 区域；不能形成平行
reducer、公共执行入口或隐藏 mutable cache。

## 12. 分阶段实施

### Phase 0：contract、identity 和 admission

1. 实现四类 observation closed family、delivery/cursor/task/wait/revision identity；
2. 实现 settlement receipt、`ObservationKind`、ObserveFrame、ObserveResult；
3. 实现 `ObserveHookEnvelope` 的两个 stage payload 和共享 Hook admission；
4. 实现 Queue/BackgroundTask/Config/Context/Ack 五类 capability Port 的取消、receipt、
   revision 和 closed-result 约定；
5. 固定一个共享 Hook slot、exact outer type 和两个 stage transition。

### Phase 1：两节点 Observe Graph

1. 实现 `get_observation`（receive/validation/frame）和 `write_observation`
   （Config/Context settlement/enum）两个节点；各节点内部保持自己的有序子步骤；
2. 安装一个真实共享 Hook，拓扑固定为
   `get_observation -> hook -> write_observation -> hook -> ObserveGraph.END`，由 HookResult
   的前驱 `node_id` 选择 Observe 已声明的 `write_observation` 或 `END` edge；
3. 两个业务 operation 通过 `Graph.bind`、typed materializer 和 typed output descriptor 装配；
4. 通过 Graph `output_ref` 绑定 Hook P3 result，验证两次 Hook request 的 predecessor binding、
   stage transition 和唯一 nested output；
5. 验证没有 route、Join、缓存或第二 runner，首次 compile 后 topology freeze。

### Phase 2：Observation queue/task ingress

1. 接入 `ObservationQueuePort.read_after(cursor)`、多条消息窗口和同 revision
   `BackgroundTaskPort.snapshot(boundary)`；
2. 接入 Config/Context settlement Port、幂等 identity、receipt 和 successor boundary；
3. 接入 cursor/delivery commit 与 ack 顺序；
4. 覆盖空队列、重复 delivery、旧 cursor、缺失 delivery 和 revision mismatch。

### Phase 3：等待 contract（由 ReAct assembly 消费）

1. 固定 `ObservationWait` codec、scope/node/interrupt projection 和大小上限；
2. 让 ReAct 的 `await_observation` 消费 Observe 的 wait coordinate；
3. 实现 atomic recheck + wake registration，禁止常驻未受 owner 管理的 task；
4. 验证 CompletionCandidate + blocking task 不会提交 END，唤醒后旧候选不会被复用。

### Phase 4：四类 observation ingress

1. 为 config、tool、user、assistant 四类来源定义 typed observation adapter；
2. 以 deterministic delivery identity 追加到 `ObservationQueuePort` provider 的 stream/outbox；
3. 覆盖 config 覆盖、tool/user/assistant 互斥、crash/retry、duplicate delivery 和 Observe re-entry；
4. 证明 Observe 只消费外层 observation，不读取 Think/Act 私有 frame、Hook command 或 executor。

## 13. 测试矩阵

### 13.1 节点数量、拓扑和共享 Hook

- ObserveGraph 业务状态节点恰好为 `get_observation`、`write_observation` 两个；直接 Graph
  节点恰好为三个：这两个业务节点及一个共享 `hook`；不存在 receive/selection/
  pass-through/settlement/route/wait 节点；
- 唯一成功路径是
  `get_observation -> hook(AFTER_GET_OBSERVATION) -> write_observation -> hook(AFTER_WRITE_OBSERVATION) -> ObserveGraph.END`；
  共享 Hook 内部 P1/P2/P3 不计入 Observe direct-node count，但同一 Hook 会激活两次；
- `hook` 必须是一个真实、共享的 `HookNode`，slot 精确匹配 definition/version、`hook` 和
  `AFTER_NODE`；不能传普通 Graph/callable，也不能安装第二个 Hook；
- 两个业务节点都输出同一 exact outer `HookRequest[ObserveHookEnvelope, HookStateProjection]`，
  只通过 `Graph.node_output(get_output_descriptor)` 形式的 predecessor-bound source 送入共享 Hook；Hook 输出
  同一 exact `HookResult`，并原样携带前驱 `node_id`，由 Observe 自己声明的 node-id route
  edge 选择写入边或结束边；stage 与 node-id/边域不匹配时必须 fail-closed；
- 两个业务节点必须使用 `Graph.bind`、`input_type`、typed `materialize`、`output_type` 的统一
  typed-node contract；缺失 materializer 输入在 capability 调用前失败，不保留节点内部
  `Graph.Values` mapping 执行路径；
- Hook P3 `result` 只能通过 Graph `output_ref("hook", "result")` 的统一 descriptor 绑定，
  Observe 不读取 Hook 私有 P3/result API，也不制造平行 output descriptor；
- Observe nested output 只有共享 Hook 第二次激活的最终 HookResult，父 ReAct 只看到一个
  node publication；
- compile 后不能增加节点、边、Hook、codec 或纯步骤 publication。

共享 Hook 的编译器回归还必须锁定以下边界：

- `hook` 的两个 incoming edge（来自 `get_observation`、`write_observation`）都声明同一个
  exact `HookRequest` descriptor；任一前驱改成不同 outer type、不同 output name 或固定
  非 predecessor source，compile 必须失败；
- wrong Hook value/state/command concrete binding 或不同 transition admission 必须在第一次
  `Graph.add_node()` 前 fail-closed；
- `write_observation` 只能消费其直接前驱本次 Hook 激活的 `result`；不能回读第一次 Hook
  的绝对 publication，也不能让 `via`/额外 direct edge 形成两个可同时满足的 activation gate；
- `hook` 的 conditional route 集合必须恰好是 `get_observation`、`write_observation`，并与
  `HookResult.node_id` 的合法身份集合完全相等；漏 edge、未知 node-id 或 stage/身份映射
  不匹配都必须在 compile/admission 失败；
- 该循环（`hook -> write_observation -> hook`）必须有可证明的 `END` exit；增加无条件
  自环、第二个入口或额外 `via` 后无法证明唯一 predecessor/occurrence 时，compiler 必须
  fail-closed，而不是运行时猜测；
- runtime 需执行两次同一 Hook 实例并验证两次 activation identity 不同、第一次结果只到
  `write_observation`、第二次结果才成为 graph output；不得把第一次 HookResult 暴露给父图。

### 13.2 两节点 settlement 和类型

- `get_observation` 只执行 receive/family-validation/frame construction，随后进入共享
  `hook`；`write_observation` 只执行 Config/Context settlement/enum wrapping，随后再次进入
  同一个 `hook`；这些步骤不产生额外 Graph 节点或中间 publication；
- observation 只允许 `ConfigObservation`、`ToolObservation`、`UserObservation`、
  `AssistantObservation` 四个 exact variant；未知/非法 variant fail-closed；
- 只有 Config 时返回该 batch 中最新的 Config；Config 覆盖关系和所有被覆盖 delivery 都由
  `ConfigObservationPort` receipt 完整记录；Config 与非 Config 同窗时仍应用 Config，但
  `current_state` 返回非 Config；
- 同一非 Config 类别的多条 delivery 必须按 FIFO 一次组成一个 batch 并一次写入 Context，
  不能拆成逐条调用、静默丢弃或重复确认；Tool/User/Assistant 不得被合并到同一 batch；
- 默认业务路径返回的 observation concrete class/字段与 `ObservationQueuePort` 选定内容完全
  一致；只有共享 Hook 可以按 same-stage/read-only-state/exact-DTO 规则显式改写 payload；
- queue/task/settlement Port 缺失时 assembly fail-closed；每个必需 Port 每次 activation 至多
  调用一次；
- duplicate delivery settlement 幂等，旧 cursor/旧 task incarnation 不覆盖新事实；
- settlement 推进 revision 时 receipt 必须携带 successor boundary 和匹配的
  `BackgroundTaskSnapshot`；混用旧 frame 在 admission 处失败；
- Hook 的合法 same-stage payload rewrite 必须贯穿到后继业务节点或最终 nested output；改变
  stage、只读 state、`node_id`、command concrete class，或构造内部不一致的 frame/result 必须
  fail-closed；同 revision 但 `blocking_tasks` 不同的 settlement/result 也必须拒绝；
- Observe 不解释或 apply Hook commands；
- Observe 只返回四值 `ObservationKind`，不携带 Think/Act 节点名或顶层 route；ReAct 自己生成跳转计划。

多条 delivery 的确定性例子（应作为 `ObservationQueuePort` contract 的逐例测试，而不是靠
实现者口头约定）：

| 未读窗口（按 cursor） | 结果 | cursor / receipt 语义 |
| --- | --- | --- |
| `config₁, config₂` | `current_state=CONFIG` | 两条都按 FIFO 应用；`config₂` 是 Config 的有效值，`config₁` 的覆盖关系和 receipt 留痕，payload 不合并、不改写 |
| `config₁, user₁, user₂` | `current_state=USER` | `ConfigObservationPort` 应用 `config₁`；`ContextObservationPort` 一次按 FIFO 写入两条；三条 delivery 均在 settlement 后确认 |
| `config₁, tool₁, user₁` | `Conflict` | 完整 batch 含两类非 Config，cursor 不变；不调用任何 settlement Port，不 ack，不进入 Hook |
| `tool₁, tool₂` | `current_state=TOOL` | `ContextObservationPort` 一次按 FIFO 写入完整 batch，一次推进并确认两条 |
| `tool₁, user₁` | `Conflict` | 完整 batch 含两类非 Config；不自动截断成两个 batch；cursor 不变且无副作用 |
| `user₁, assistant₁` | `Conflict` | 完整 batch 含两类非 Config；不自动拆分、不丢消息；producer 若需分次处理必须提交独立 batch |
| 非法单 batch：未知或混合 family | `Conflict`/typed failure | cursor 不变，不进 Hook，不调用 settlement Port，不 ack |
| 空窗口 | `Empty(ObservationWait)` | cursor 不变；按 wait coordinate interrupt/resume |

### 13.3 队列、ack 和恢复

- 空队列进入 interrupt/wait，不创建常驻 `asyncio.Queue.get()` task；
- `Conflict` 不进入 wait/retry 热循环；在 cursor 不变的情况下沿 typed failure/diagnostic 边界结束；
- ack 只发生在 node settlement/commit 确认之后；ack 失败先按 receipt reconcile，重读同一
  delivery 也不得重复非幂等 effect；
- wake payload 不被当作业务消息，恢复后一定按 cursor 重读；
- 消息在 wait registration 前到达时不会丢失（atomic recheck）；
- process cancellation、lease/fence、旧 scope、旧 interrupt、旧 codec 由 Graph/Port 边界拒绝或传播。

### 13.4 结束候选和后台任务（必须逐例覆盖）

- Assistant observation 含完成事实、blocking task 为空且 revision fence 通过 -> ReActPolicy 允许 END；
- Assistant observation 含完成事实且有一个 blocking task -> 不得 END，进入 wait；
- 其他状态即使让 ReActPolicy 提出正常 END，只要 `background_task_snapshot` 含 blocking task
  也必须进入 wait；
- 等待期间 task 完成但**没有**新消息 -> 仍等待，不轮询、不自行醒来；
- task provider 完成并经 `ObservationQueuePort` 投递 `ToolObservation` 或 `ConfigObservation`
  -> 唤醒，旧 candidate 作废，重新 Observe；
- 唤醒后新消息要求 Think/Act -> 由新的 ReActPolicy 决定，不使用旧 candidate；
- 唤醒后新 Assistant observation 再次携带完成事实且 task 已空 -> 新状态经策略确认后才能 END；
- 唤醒后新 Assistant observation 再次携带完成事实但仍有 task -> 再次 wait，任何旧/新状态都不得直接 END；
- revision fence 失配、过期 snapshot 或旧 task incarnation completion 不能恢复旧 candidate；
- duplicate/stale wake 不能跳过 Observe；Control cancel/terminate 在等待中作为新 observation 处理。

### 13.5 长步骤升级防线

- 纯 selection/pass-through/current-state wrapping 不得因为代码拆成函数就变成 Graph node；
- 只有独立状态、恢复、重试、取消、资源或审计需求才允许增加第三个业务节点；
- 新增状态节点必须输出共享 Hook 可接收的 exact `HookRequest` 并连接现有共享 Hook，不得
  复制 Hook 或私建 runner；
- 长 Port settlement 的 receipt/recovery 测试必须证明升级前后的边界不会重复非幂等 effect。

### 13.6 四类 observation ingress

- config、tool、user、assistant 都经 queue Port provider/adapter 以对应 exact observation
  进入同一逻辑 stream；
- Think/Act 结果只作为 `AssistantObservation` 的外部来源，不能把节点名带入 Observe；
- child settlement 与 append identity 稳定，重试不产生不可区分的重复；
- Observe 只消费外层 result，不读取 Think/Act 私有 frame、Hook command 或 executor，也不
  依赖其节点名；
- nested Observe output 能被 ReAct 作为单一 node publication 精确接收。

## 14. 验收清单

- [ ] ReAct 是唯一顶层 Graph，Observe 是一个 nested Graph；
- [x] Observe 业务状态节点恰好两个：`get_observation`、`write_observation`；直接 Graph 节点恰好三个，另含一个共享 `hook`；
- [x] `get_observation` 内只做取得观察，`write_observation` 内只做观察值写入；两个节点执行后都进入同一个共享 Hook；
- [x] 成功拓扑固定为 `get_observation -> hook -> write_observation -> hook -> ObserveGraph.END`，由 HookResult 前驱 `node_id` 选择两条固定 conditional edge，没有额外 Observe route/wait 节点；
- [x] 两个业务节点使用 Graph typed-node contract；Hook P3 `result` 通过 Graph 统一 `output_ref` 绑定，没有 Observe 私有 P3/result 路径；
- [x] 共享 Hook 的 exact value/state/command/transition binding 在 assembly fail-closed；payload 可有界改写，stage、只读 state 和 `node_id` 不可改；
- [x] Observe 只返回 typed `ObserveResult.current_state: ObservationKind`，ReAct 自己定制 Think、Act、Wait、Observe 或顶层 END；
- [x] Queue/Task/Config/Context/Ack/Resume Port provider 持有各自事实，Role/Control 只负责装配，Observe 只经窄 Port 读取/操作/确认；
- [x] `ObserveResult.background_task_snapshot` 与 queue boundary 处于同一 observation revision，ReActPolicy 不重复 live-query task provider；
- [ ] Assistant 完成事实 + blocking task 永不提交 END，只通过 Queue Port 等待新消息；
- [ ] 唤醒后旧完成状态/父图计划必然作废，完整重新 Observe 并重新生成状态；
- [ ] revision fence 失配不能绕过等待或恢复旧 candidate；
- [x] 没有 ObserveState、ObserveRunner、ObserveStore、队列副本或第二执行路径；
- [x] 已运行 `pytest`、`ruff`、`pyright`、`make check` 和 monorepo pre-commit；Observe 精确门禁全绿，后两项只因既有全仓 complexity-ratchet 失败，原因记录于验收文档。
