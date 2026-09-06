# Act / Tool Use 四节点、共享 Hook 实施计划评审

状态：**当前生效结论见第 9.21 节：P1 已完成架构收口，可以开始 Phase 0 契约编码；
Phase 1 公共 API 合入前只需完成列明的实现证据。** Act 只负责固定四节点编排、调用具名 Port、校验
固定 outer DTO 并返回 Graph outcome；Port 负责产生具体结果，Graph +
`GraphRunState` 负责执行位置、interrupt/resume 和 Act 层对账。Act 不维护
`observe/CAS/revision/operation_token/reconcile` 状态机。所有工具调用到 Authorize
都固定 interrupt；Runtime/`AuthorizePort` 已经向用户发起请求，Graph 的 interrupt 只保存
opaque request handle，恢复输入只有 `Allow` 或 `Deny`。Kernel 不关心谁决定、为什么拒绝，
也不定义任何模型可见内容。Failover 仍只由 composition 包住每个具体 Port，其内部不在本
评审范围。第 1–9.20 节保留历史审计，已由第 9.21 节取代，不再代表当前实施口径。

评审日期：2026-09-05

评审对象：[`docs/act-tool-use-implementation-plan.zh-CN.md`](./act-tool-use-implementation-plan.zh-CN.md)

## 0. 评审范围

本评审只覆盖 Act / Tool Use 的领域契约、四节点加一个共享 Hook 的拓扑、Approval interrupt/resume、Port 装配、Graph/State/Commit
边界和公共 API。

Failover 的 profile、policy、重试图、装饰器和 assembly 内部仍由独立 owner 开发，本评审**不评价**其实现细节。
本评审只固定 Act 的接入前置条件：`SettlementPort`/`ToolExchangeWriter`（以及未来明确需要的其他 Port）可以接收已经
装配好的 typed Failover capability；Failover 在 Port 调用边界内重试，不重跑 Act/Graph 节点，Act 自身不实现 retry
loop，也不改变 Failover 的 owner 边界。

## 1. 首次复审结论（历史记录）

> 注：第 1–7 节保留首次复审时的审计证据，第 8 节保留二次复审证据，第 9.1–9.16
> 节保留后续复审及当时结论。当前是否达到实施条件、实现时应遵守的门槛，以第 9.19
> 节为准。

首次复审时，这份计划的主方向是正确的，可以保留：

- `Resolve → Authorize → Execute → Settle` 作为 Act 的四个业务阶段；
- Approval 是 Authorize 的 interrupt/resume 分支，不额外创造一个业务节点；
- `mote_kernel.execution.Graph` 是唯一执行门面，Act 作为 nested graph 复用同一 family driver；
- `GraphRunState`、`GraphRunCommand`、pure reducer 和 `Graph.Commit` 仍是唯一状态与提交路径；
- 外部 capability 通过窄的 typed Port 注入，节点不直接操作 Store、reducer 或 transport；
- 用稳定的 invocation/binding identity、revision 和 digest 支撑恢复与幂等；
- Settle 只做业务结果投影，提交确认后才允许对外宣布完成。

目前还不能直接按本文开工，原因不是四节点方案不合理，而是几个会改变公共行为的契约仍停留在“建议”或互相矛盾：

1. Resolve/Authorize 的终止结果到底是 `Graph.FailedResult`，还是仍然要产生可消费的 `ActResult`，没有定案；
2. Act Port 的示例是同步 SPI，而当前 Graph node 和 `Invocation` 都是异步契约；
3. Approval 恢复时的精确输入 envelope、codec 和“不得重复创建审批请求”的规则尚未冻结；
4. Act 的 Graph value universe、运行时 nominal admission 和动态工具 binding 的类型边界还不够具体；
5. Settle 的 command/evidence 如何通过现有 `Graph.Transition` 交给外部 owner，仍缺一个完整的 typed 形状。

因此建议判定为：**设计方向通过，实施计划待契约补齐后通过**。这些问题应在 Act owner 的 MR 中解决；不要求本评审
改动 Failover 包。

## 2. 当前计划中已经成立的部分

### 2.1 业务拓扑和职责拆分

四个阶段的语义边界清楚：Resolve 只负责工具定义/binding，Authorize 只负责权限事实和审批，Execute 只调用一次
已授权 capability，Settle 只产生稳定结果和 command-facing projection。这样可以避免把权限、工具查找、结果包装和副作用
混在一个 callable 里。

Approval 放在 Authorize 内是正确的。审批等待应表现为现有 Graph 的 interrupted activation；恢复后继续同一个
Authorize 边界，不能通过后台任务、额外 runner 或私有 `ApprovalState` 保持等待。

### 2.2 Kernel 复用和 owner 边界

计划正确复用了当前执行链：

```text
Graph.run()
  -> GraphExecutor / Session
  -> node callable
  -> SettleGraphNode
  -> reduce_graph_run()
  -> Graph.Commit exact candidate
```

当前 [`NodeCallable`](../src/mote_kernel/execution/graph/node.py) 返回 awaitable；当前 [`Invocation`](../src/mote_kernel/invocation.py)
也只提供一个异步 `invoke()`。Act 不应再创建 `ActRunner`、`ActExecutor`、第二个 reducer 或另一套恢复入口。

### 2.3 类型和身份方向

计划拒绝 `Any`、裸字典、反射和字符串 discriminator，并要求 definition/version、binding reference、参数 digest
和审批 request identity，这与 Kernel 的 exact nominal admission 和 durable recovery 方向一致。

特别应保留以下不变量：

- 未完成 Resolve/Authorize，不得调度 Execute；
- approval decision 的 request ID、参数 revision/digest 和 policy revision 必须重新校验；
- opaque callable、registry、credential 或 transport client 不能进入 Graph value 或持久化 State；
- 节点只返回 typed value/result，不能直接修改 `GraphRunState`。

## 3. P0 阻断项

### P0-1：终止分支与公共 Act 结果没有闭合

计划第 0 节把 Settle 说成“得到可交付 Act 结果”的必需节点，但第 3.1、3.2 节又允许 Resolve 或 Authorize 直接返回
`Graph.failure(...)`。当前 Graph 的 failure settlement 只有节点 ID 和字符串 failure，不携带 `ActResult` output；嵌套
child 失败后，父图也不能从一个 typed Act output 继续路由。

这会造成至少三种互相冲突的对外语义：

- 工具业务失败：Execute 返回 typed failure，进入 Settle；
- 未知工具/拒绝/审批拒绝：直接成为 Graph failed result，跳过 Settle；
- 文档又把它们统称为“Act 结果”。

实施前必须选定一个公开契约，并同步修改拓扑、`Graph.Commit` evidence 和测试：

1. **Graph 终止契约**：Resolve/Authorize 的拒绝就是 `Graph.FailedResult`，Act API 不承诺所有终止分支都有
   `ActResult`；或者
2. **typed terminal projection**：拒绝、审批拒绝和解析错误也产生一个明确的 Act-owned terminal result，供父图和
   外部 owner 消费；四节点拓扑是否仍能保持要写出具体 adapter/边界，不得靠隐式第五节点解决。

无论选择哪一种，都要固定：

- 父图看到的 nested child disposition 和 output；
- failure/denial 是否进入 Settle 业务 projector；
- commit 中保存的错误 evidence、敏感字段和稳定 error code；
- Resolve failure、Authorize denied、Approval rejected、Execute business failure 的结果类型和调用次数。

在这个选择完成前，`ActResult` 不是可实现的公共承诺。

### P0-2：Port SPI 的同步/异步边界与取消语义未冻结

第 6 节的 Port 形状写成：

```text
resolve(request) -> ResolvedT
evaluate(input) -> DecisionT
request(approval) -> ApprovalDecisionT
execute(invocation) -> ExecutionResultT
project(result) -> SettlementT
```

但当前 Graph callable 必须返回 awaitable，现有 `Invocation.invoke()` 也是 `async`。如果按文档直接实现，远端
resolver、审批和工具调用会出现类型不匹配，或者被迫在事件循环中阻塞。

请在 Act contract 中明确：

- 外部/可等待 Port 是否统一使用 `async def`；
- 纯 projector 是否允许同步实现，以及由哪个显式 adapter 转成 node callable；
- `CancelledError` 是传播为 Graph cancellation/abort，还是转成某个 typed terminal variant；
- Port 抛出普通异常、返回错误 DTO、返回非法 nominal 类型时，各自在哪一层失败；
- Approval pending 不创建后台 task，调用方取消时如何清理正在等待的 invocation。

若允许同步和异步两种 Port，必须提供两个明确的 typed adapter，不能用 `callable()`、反射或运行时试调用猜测。

### P0-3：Approval interrupt/resume 的输入契约还只是建议

当前 `Graph.resume_interrupted()` 要求 resume values 与被恢复节点的输入 descriptor 精确匹配，见
[`execution/facade.py`](../src/mote_kernel/execution/facade.py) 和 [`execution/request.py`](../src/mote_kernel/execution/request.py)。
因此下面两种形状不能混用：

```text
初次：Resolve -> Authorize(ResolvedInvocation)
恢复：Authorize(AuthorizationInput + approval decision)
```

计划虽然建议使用 `AuthorizationInput`，但没有把它定为强制边界。必须冻结一个同一 nominal class 的 envelope，至少
包含：

- `ResolvedInvocation` 和 binding/definition identity；
- approval request ID、decision、参数 revision/digest；
- policy revision、主体/目标摘要和 resume provenance；
- 明确的“尚未审批 / 已批准 / 已拒绝”状态表示，不使用布尔拼接或裸字典。

同时必须明确：

1. `Graph.interrupt()` payload 的 codec owner、ID/version、大小上限和敏感数据处理；
2. 恢复前对 scope、node ID、interrupt ID、definition/version 和 codec 的校验顺序；
3. 恢复后只重新执行 Authorize，还是允许再次调用 ApprovalPort；若不允许，如何从 resume input 识别已存在的 request；
4. 重复 resume、过期 decision、参数 digest 不匹配时的 fail-closed 结果。

“恢复后不重复 Resolve/ApprovalPort”必须从该 envelope 和测试中得到证明，不能只写在说明文字里。

### P0-4：Graph value universe 和运行时 admission 未落地

当前 `Graph[GraphValueT]` 的每个输入/输出 descriptor 都要求一个具体 nominal class；nested graph 与 parent 也必须
使用同一个 value universe。Act 计划列出了许多泛型 DTO 和 union，但没有定义 Act 的 Graph value marker、一次性
admission contract 或动态工具选择如何得到具体参数类型。

实施前至少要冻结：

- 一个 Act-owned 的内部 Graph value universe（例如 `ActGraphValue`），或等价的单一 owner 类型；
- `request/resolved/authorization/execution/settlement/result` 每个 boundary 的 exact class；
- arguments、result、command 的具体 nominal class admission；
- nested Act output 如何映射到父图 `NodeOutputRef`；
- tool selector 动态选工具时的 typed binding adapter。

不得用 `object`、`Any`、`typing.Union` 作为 Graph port descriptor，也不能把动态工具目录实现成反射读取函数签名。
如果同一个 ActNode 不能静态承载多个工具契约，就应在 assembly 层为每个 binding 生成具体实例，而不是在运行时擦除
类型。

## 4. P1 高风险问题（历史记录；当前口径见第 9.19 节）

> 本节保留早期风险描述，不代表仍有待决架构问题；最终收口以第 9.19 节为准。

### P1-1：固定拓扑与当前 Graph 的可变窗口不一致

当前 [`Graph`](../src/mote_kernel/execution/facade.py) 只在第一次成功 compile 后冻结；此前仍可调用 `add_node`、`add_edge`、
`set_outputs` 和 `set_resume_codec`。如果 `ActNode` 继承 Graph，公共对象在构造完成后仍暴露这段 builder API。

计划同时声称“Act 是固定四节点拓扑”和“第一次 compile 后修改被拒绝”，这两个描述并不等价。请二选一并写入契约：

- 由 execution owner 提供 assembly seal/不可变组合 API，Act 在返回公共对象前完成 sealing；或
- 明确接受 compile 前的 builder 窗口，把“固定”限定为 compile 后，并增加防止外部污染的 composition 约束。

不要在 Act 包内复制一套 mutation guard，也不要用私有子类偷偷提供第二个 Graph facade。

同时需要冻结 `ActSlotId`/definition ID/version 的生成方式，以及 DTO/Port 的稳定 import 路径。`ActNode` 是唯一根包导出
对象，并不等于 composition root 无法构造 `ActRequest` 和 Port；公共和 owner-internal 类型必须区分清楚。

### P1-2：Settle command/evidence 的所有权和交付方式不完整

`ActSettlement.commands`、`ActCommand`、`ExecutionReceipt`、`ToolResultPresentation` 目前只有概念形状。计划要求
Settle 结果进入 `Graph.Transition.result`，但没有写清：

- command 是只作为一次 node output 返回，还是必须进入 persistence/outbox；
- 外部 command owner 如何按 invocation、scope、node 和 revision 去重；
- nested Act 的成功 output、失败 evidence、interrupt payload 如何在同一个 commit 边界被引用；
- command/参数/结果中哪些字段禁止持久化或需要脱敏；
- commit acknowledgement lost、非 exact candidate 和重复观察时的行为。

当前 [`GraphTransition`](../src/mote_kernel/execution/family_driver.py) 的 `result` 是可选的 `GraphCommitResult`，不是一
个自动的 Act command 总线。请在 Act 文档中只定义一个 typed settlement projection 切口，并明确外部 owner 的消费、幂等
和失败策略；Settle 不能自行写 Store、调用 reducer 或发通知。

### P1-3：Resolve 的动态工具 binding 与 schema 生命周期需要更具体

`ResolvePort` 需要把模型给出的 selector 固定到一个工具 definition、版本、输入 schema 和 capability identity。计划
描述了这些字段，但没有冻结以下实现边界：

- catalog/binding snapshot 在一次 Act activation 中何时读取、是否只读一次；
- arguments schema 的 admission 是由 catalog、ResolvePort 还是独立的 typed adapter 负责；
- definition 被撤销、版本不匹配或参数 revision 变化时的终止结果；
- 恢复时如何根据稳定 reference 重新取得 runtime handle，而不把 callable/registry 放进 State；
- 原始参数、错误消息和 schema 中的 secret/超大字段如何拒绝或脱敏。

建议把这些内容收敛成一个 `ResolvedInvocation` contract 和一组 deterministic admission tests，而不是让每个工具 adapter
自行解释 `ToolSelector`。

### P1-4：取消与结果代数存在矛盾

第 3.3 节规定 `asyncio.CancelledError` 必须传播；第 3.4 节又说 Settle 统一 cancelled/unknown；第 5.3 节的
`ToolExecutionResult` 只有 `Succeeded`、`BusinessFailed`、`Unknown`，没有 cancelled variant。

请明确取消属于哪一层：

- 作为 Graph cancellation/abort，Settle 不会被调用；或
- 由 runtime 先把“外部结果未知”转换为明确的 typed outcome，再由 Settle 投影；或
- 增加一个经过审查的 `Cancelled` domain variant。

不能一边传播取消、一边要求 Settle 统一一个它永远收不到的结果。测试应覆盖调用前取消、调用中取消、已有外部 receipt
后的取消和 parent nested cancellation。

### P1-5：条件能力缺失时的行为需要唯一化

当前计划对缺少 ApprovalPort 写了两种可能：assembly 拒绝，或 Authorize 返回 denied/unavailable。两者都可以 fail
closed，但对调用方、审计和测试来说不是同一个公共行为。

建议采用以下确定性规则：

- required Resolve/Authorize/Execute/Settlement capability 缺失：assembly 立即失败；
- 配置声明可能产生 Ask 而 ApprovalPort 缺失：assembly 立即失败；
- 只有静态证明 policy 不会 Ask 时才允许省略 ApprovalPort；
- 运行期出现未声明的 Ask：fail closed，且不得调 Execute。

如果产品必须支持动态 policy，应把“capability 可产生哪些 decision”也纳入 typed assembly contract，而不是运行时猜测。

## 5. 建议的修订顺序

### Phase 0：先冻结 Act 契约

在任何 Graph implementation 之前补齐：

1. `ActGraphValue`/payload admission 和每个 boundary 的 exact nominal class；
2. async Port SPI、异常/取消边界和 required capability assembly；
3. Resolve/Authorize/Approval 的 terminal result algebra；
4. `AuthorizationInput`、Approval request identity、resume codec 和安全上限；
5. `ActInvocationId`、`ToolBindingRef`、definition/version、arguments revision/digest；
6. `ActSettlement`、`ActCommand` 和唯一的 commit/evidence 投影切口。

交付条件：不依赖 Graph executor；没有 `Any`、裸字典、反射或 opaque runtime handle；每个终止分支都有明确的对外结果。

### Phase 1：四节点 happy path

按现有 Graph API 组装 `Resolve → Authorize → Execute → Settle`，每个 callable 都是 awaitable node，所有 output
声明 exact class。验证父图可以把 Act 作为一个 nested node，且没有第二个 runner。

这一阶段只验证 Allowed + Execute success/business failure；不要用未定义的 failure fallback 先掩盖 P0-1。

### Phase 2：终止分支和结果交付

实现并测试 Resolve invalid/unknown、Authorize denied、Approval rejected、Execute business failure 的最终结果契约，
包括 parent nested projection、commit evidence 和调用次数。

### Phase 3：Approval interrupt/resume

只使用现有 `Graph.interrupt()`、resume codec 和 `Graph.resume_interrupted()`。恢复必须匹配精确 scope/node/interrupt/
definition/revision，且不能创建重复审批请求或后台 task。

### Phase 4：Settle 与统一提交

Settle 只返回 immutable typed projection；由外层 `Graph.Commit` 负责 exact candidate。把 command/outbox、敏感字段、
acknowledgement lost 和 post-commit announcement 的边界写成测试，不在 Act 节点内写 Store。

### Phase 5：外部故障能力接入

Failover 按其独立 owner 的计划接入。本评审不审查该阶段的策略、装饰器或固定图；Act 只验证接入后的 Port 仍满足既定
typed request/result 和“一次 Act 业务阶段不自行重试”的契约。

## 6. 必须补齐的验收矩阵

| 类别 | 场景 | 预期 |
| --- | --- | --- |
| assembly | 缺 Resolve/Authorize/Execute/Settlement | 构造期稳定失败，不创建可运行的半成品图 |
| assembly | policy 可能 Ask 但缺 ApprovalPort | 按唯一规则 fail closed，不能到 Execute |
| typing | 错误 request、state、arguments、result、command 或 nested universe | 在进入下一节点前 exact admission 失败 |
| happy path | Resolve → Allowed → Execute success → Settle | 每个 Port 调用次数符合契约，输出是稳定 typed Act result |
| resolve | 未知工具、版本/schema/digest 不匹配 | Authorize/Execute 不调用；结果遵循 P0-1 的唯一终止契约 |
| authorize | Denied | Execute 调用次数为零；父图 disposition/output 可预测 |
| approval | Pending | 产生精确 interrupt identity 和 codec 版本，不创建后台 task |
| approval resume | 正确/错误/过期 decision、重复 resume、错误 scope | 正确恢复只执行约定的 Authorize 路径；其余 fail closed |
| approval idempotency | 恢复或重放同一 request ID | 不产生重复审批请求，不改变已固定的参数 digest |
| execute | typed business failure、普通异常、调用方取消 | 分别落入约定的 Settle/Graph failure/cancellation 边界，不伪造成功 |
| settle | projector 异常、非法 settlement、敏感/超大字段 | 在正确边界失败，不能直接写 State/Store 或发通知 |
| commit | commit 异常、非 exact candidate、acknowledgement lost | 内存 State 不前移；重复观察按唯一 evidence/幂等契约处理 |
| nested | Act 作为父 Graph node，子图成功/失败/中断 | scope、child projection、output 和恢复坐标不串线 |
| topology | compile 前修改、compile 后修改、definition/version mismatch | 按固定拓扑契约拒绝或明确允许，不静默改变旧 snapshot |
| security | 参数/审批 payload/错误消息含 secret 或超大值 | 按 codec/admission policy 拒绝或脱敏，不写入不该持久化的 evidence |
| architecture | Act 节点尝试创建 runner、reducer、Store 或 retry loop | 静态结构门禁和运行时测试均拒绝 |

## 7. 首次 MR 评审意见（历史记录）

> 评审结论：四节点 Act 拓扑、Approval 放在 Authorize、复用唯一 Graph/State/Commit 链的方向是合理的，建议保留。
> 当前实施计划暂按“有条件通过”处理，进入实现前请先关闭四个 P0：终止分支的公共 Act 结果契约、异步 Port SPI、Approval
> resume 的精确输入/codec/idempotency 契约，以及 Graph value universe/admission。另请补齐 Settle command/evidence 的
> 唯一提交切口、动态工具 binding 生命周期和取消结果代数。
>
> Failover 不属于本 MR 的评审范围，按独立 owner 的既定计划接入即可；Act 侧只接收 typed capability，不在自身拓扑或
> Port callable 内实现重试。关闭上述契约并补齐 deterministic tests 后，再批准进入生产实现。

## 8. 二次复审（2026-09-04）

### 8.1 结论

本次复核以实施计划当前版本为准，上一轮提出的四个 P0 已有明确的设计决策和测试落点。
因此，**Act v1（单进程、内存 ToolExchange projection、可选 `commit=None`）可以进入
Phase 0 契约实现，并在完成本节列出的门槛后进入 Phase 1；结论是“有条件通过”，不是
生产 durable 就绪。**

本期明确不承诺以下能力：持久化 Context/AgentState、outbox、崩溃恢复、跨进程恢复和
跨进程 ToolExchange 去重。它们必须等待统一 persistence/recovery owner 提供一个把
Graph candidate、settlement/exchange 和恢复事实放在同一提交边界的方案，不能用
`Graph.run(commit=None)` 或 writer 的内存表替代。

特别地，只有在 `ToolExchangeWriter` 被证明是非权威、进程内的协议 projection 时，才可按
本结论进入 v1；若其写入的是受治理的 Context/AgentState 或其他 durable 事实，则当前计划
仍未达到实施条件，必须先改为统一 `Graph.Commit` 的 typed projection/transaction。

### 8.2 上一轮 P0 / P1 的复核结果

| 上一轮意见 | 当前状态 | 复核依据 |
| --- | --- | --- |
| Resolve/Authorize 终止分支与 `ActResult` 语义冲突 | **已闭合** | 实施计划第 0、3.1–3.3、5.3 节固定为 paired terminal `ToolResult` + `Graph.failure`/stop；拒绝不产出正常 `ActResult`，Execute/业务 Settle 不调用 |
| Port 同步/异步边界未冻结 | **已闭合** | 第 3.4、6 节统一外部 Port 为 `async`；同步 projector 仅允许作为 Act-owned 纯计算并由 async node 显式调用；取消沿 Graph cancellation 边界传播 |
| Approval resume envelope、codec、重复请求规则不足 | **基本闭合** | 第 4.3、5.2、6 节固定 exact `AuthorizationInput`、request/interrupt identity、revision/digest、codec version 与 64 KiB 上限，并禁止恢复重复调用 ApprovalPort；仍需补齐调用方如何构造该 envelope（见 8.3-2） |
| Graph value universe / runtime admission 不具体 | **已闭合（待代码验证）** | 第 4.2、5.4 节增加 `ActGraphValue`、一次性 admission contract、exact nominal class 和 `CanonicalToolArguments`；动态 binding 不再依赖反射或 `Any` |
| compile 前 builder 可变窗口与“固定拓扑”矛盾 | **已闭合** | 第 4.2 节明确接受首次成功 compile 前的既有 Graph 窗口，之后沿用 Graph mutation guard，不复制第二套 seal |
| dynamic binding/schema snapshot 生命周期不清 | **已闭合（待代码验证）** | 第 3.1、5.4、6.2 节固定一次 activation 的 snapshot、definition/version、binding reference 和 digest，并要求恢复时重新校验 runtime handle |
| 取消与 Settle 结果代数矛盾 | **已闭合** | 第 3.4、5.3、7.4 节明确 `CancelledError` 不进入本版 Settle，不伪造成功或用户拒绝；typed business failure 才进入 Settle |
| 缺少 ApprovalPort 时行为不唯一 | **已闭合** | 第 6.1 节规定可能产生 `ApprovalRequired` 的 profile 必须在 assembly 注入 ApprovalPort，运行时未声明的 Ask fail closed |
| Settle/ToolExchange 的写入不具备 durable 原子性 | **按 v1 范围降级为条件项** | 第 3.3、7.2–7.3 节承认 writer 与 `commit=None` 没有跨组件原子性，并把统一 durable 提交延期；writer 的 owner、并发原子性和失败后语义仍需冻结（见 8.3-1） |
| ActNode 是 Graph 子类还是外部 wrapper、Approval payload 谁编码 | **尚未完全闭合** | 第 4.1/4.2 节存在“继承/组合”双重表述，第 4.3 节同时出现 Port 编码与 Act codec；见 8.3-4 |

### 8.3 进入 Phase 1 前仍必须冻结的四个契约

#### 8.3-1 `ToolExchangeWriter` 的 owner、副作用和幂等边界（P1）

计划已经把 writer 从 Graph State/reducer 中分离出来，但“Context Runtime capability”目前
仍是概念 owner，包结构又把协议放在 `act/port.py`；实现前必须把依赖方向写成可编译的
契约：Act 只依赖窄 protocol，Context/runtime 负责实现，`execution` 不反向依赖 Act，且
writer 不是第二个 State owner。若该 projection 实际上会修改受治理的业务/持久状态，
就会违反“服务和工具只返回 typed result/command”的内核规则，不能仅用“Context projection”
这个名称规避；此时应把它收回统一 `Graph.Commit` 的 projection/transaction 边界。

还要明确并测试以下行为：

- `append_once` 在并发 task 下必须是线性化/原子的；不能出现两个调用都先检查通过再各自写入；
- 去重 key 的唯一范围（至少说明是否全局 `ActInvocationId`，或为 `(scope/run, invocation_id)`）以及
  `tool_call_id` 在不同 invocation 间碰撞时的处理；
- 同一 key、同一 exchange 重放返回同一个 receipt；同一 key、不同参数/result 返回一个
  明确的 typed conflict，而不是覆盖或静默成功；
- writer 已成功而随后 Graph settlement、commit 或调用方取消失败时，v1 projection 是否
  保留、如何被后续重放观察，以及这是否明确被定义为“非 durable、可能孤儿”的进程内事实；
- 将来 durable 版本必须把 exchange command 移入统一 `Graph.Commit`/persistence 原子边界，
  不能继续把“先写 writer、再 commit”当作可靠事务。

这些规则不要求本期实现跨进程 exactly-once，但没有它们就无法证明“最多一个
ToolExchange”在并发和重放下成立。

#### 8.3-2 Approval resume 调用方的完整构造路径（P1）

当前 `Graph.resume_interrupted()` 的实际入口要求调用方提供与节点 descriptor 完全相同的
`Graph.Values`；continuation 对调用方是 opaque，`GraphInterruptView` 只暴露编码后的
payload。计划虽规定 Authorize 初次和恢复都使用 `AuthorizationInput`，却没有闭合调用方
如何取得其中的原始 `ResolvedInvocation`、或如何从受保护 reference 重建它。

实施前必须二选一并写成公共/owner-internal API：

1. 提供 Act-owned 的 resume helper，从已验证的 interrupt payload/受保护参数 reference
   通过公开的 `Graph.values(input=AuthorizationInput(...))` 构造 exact `Graph.Values`，再交给现有
   `Graph.resume_interrupted()`；或
2. 明确要求调用方保留初次 activation 的 immutable `AuthorizationInput`/resolved
   envelope，并规定其生命周期、跨 nested scope 的传递和丢失时的 fail-closed 行为。

无论选择哪一种，都要补一条从 `AwaitingResumeResult` 到 approved/rejected resume 的端到端
测试：测试不得访问私有 frame/continuation 字段来“拼”输入，也不得把 secret 放入 interrupt
payload。只有这样，exact nominal 类型和“恢复不重复 Resolve/ApprovalPort”的承诺才在真实
调用入口闭合。

#### 8.3-3 identity、冲突和稳定公共 import 路径（P1）

`ActInvocationId`、`tool_call_id`、`ToolBindingRef`、receipt 和 definition/version 已
在正文中出现，但仍需在 Phase 0 把以下内容定为不可变的 nominal contract：

- `ActInvocationId` 的唯一性域（单次 Graph run、nested scope、进程还是全局）以及 writer 去重
  key 的对应关系；
- 同一 `tool_call_id` 被不同 invocation/不同 scope 使用时，是拒绝、关联到独立 pair，还是
  由上层先做 admission；不能由 adapter 自行解释；
- receipt 的 equality/replay/retention 语义，以及 definition/version 变更后旧 receipt 的
  处理；
- arguments、result、evidence、interrupt/resume frame 的大小上限、脱敏和 secret 拒绝规则
  要有明确 owner 与具体限值，不能只写“有严格上限”；
- `ActRequest[ArgumentsT]` 与 `CanonicalToolArguments` 的层级要固定：明确 Graph 输入究竟
  采用 canonical envelope，还是由 assembly 先物化一个工具专用 `ArgumentsT`，以及具体
  nominal 参数对象在 Resolve 的哪一步产生；不能让动态 selector 在同一 descriptor 中
  隐式改变 `ArgumentsT`；
- Kernel 级 `Invocation`、transport 和通用 resolution 仍由 `mote-infra/invocation` owner
  负责；Act 的 `ResolvePort` 只能承担工具领域的 binding/schema admission，不能在 Act 内
  再造 resolver、registry 或 transport contract；
- `ActRequest`、`AuthorizationInput`、各 union variant、Port protocol 和 admission contract
  的稳定 import 路径。根包只导出 `ActNode` 可以保留，但实现不能继续以“实现阶段再命名”的
  建议形状作为跨包依赖。

这项不要求把 DTO 全部提升到 `mote_kernel.act` 根包，只要求在 Phase 0 交付前冻结一个可供
composition root 和测试使用的 owner-facing 路径。

#### 8.3-4 ActNode 的具体 Graph 形状与 Approval codec owner（P1）

实施计划第 1.2、4.1 节同时使用“继承/组合 `Graph`”和“作为 nested `Graph`”两种表述。
当前 `Graph.add_node()` 只接受 `Graph` 实例作为 nested node；一个仅持有内部 Graph 的
普通 wrapper 不能直接接入父图。因此实现前必须明确采用 `class ActNode(Graph[ActGraphValue])`
这一类可嵌套形状，或由 execution owner 提供明确的 typed nested adapter；Act 包不能偷偷
增加第二个 facade、runner 或 wrapper protocol。相应地要测试：父图绑定 ActNode 的 exact
child boundary、ActNode 的四节点 assembly 只发生一次，以及首次 compile 后 inherited
mutation guard 仍有效。`ActGraphValue` 本身也必须是类似 `HookGraphValue` 的具体 nominal
marker/owner contract，不能把 `typing.Union` 或一个仅供静态检查的 TypeAlias 传给 Graph
descriptor；每个端口仍须声明具体 DTO class。

Approval 编码也要只有一个 owner。当前 `ApprovalDecision.Pending` 示意为已编码的
`encoded_request`，而第 4.3 节又指定 `ApprovalRequestCodec` 负责编码。实现前请固定为
以下一种：ApprovalPort 返回 typed request，由 Act codec 统一编码并施加 identity/大小/
脱敏规则；或 ApprovalPort 返回已由其 owner 编码且带 codec identity/version 的 payload，
Act 不再二次编码。两条路径不能并存，也不能让不同 adapter 自行决定 64 KiB、secret 和
版本校验。应增加 Pending、重复 resume、非法 codec 和超限 payload 的 deterministic test。

### 8.4 需要在正文/测试中补一条的语义澄清

“每一个到达 Act 业务终态的有效 ToolCall 都必须有 ToolResult”应明确限定为**typed
business outcome 或 terminal projection 已被生成且 writer 成功**的路径。Resolve/Authorize
终止和 Execute typed business failure 要配对；Port 普通异常、`CancelledError`、writer
自身异常属于 Graph infrastructure/取消边界，不应被实现成 `UserRejectedToolResult`，也
不能在未写入成功时伪造 pair。现有第 6、7 节已接近这一语义，建议在 Phase 2/测试名称中
使用同一措辞，避免与顶层“一律配对”的简写冲突。

另外，正文中的“写入相应的 terminal ToolResult”还应列出完整映射：例如 binding 被撤销、
definition/version 漂移、审批 decision 过期或 digest 不匹配分别使用哪个 variant/error
code；`tool_call_id` 本身无法准入、writer conflict 和 writer 故障则是否属于不配对的
protocol/infrastructure error。不能让每个 adapter 自由选择这些语义，否则主路径虽然闭合，
审计和重放仍会出现不可比的结果。

### 8.5 v1 验收门槛与生产边界

可以批准的最小范围是：

- Phase 0 完成具体 DTO/Port/admission/identity 定义和 deterministic tests；
- Phase 1–3 覆盖 Allowed、Execute success/business failure、Resolve/Authorize terminal
  rejection、Approval interrupt/resume，并验证 nested Graph、取消和调用次数；
- writer 只作为明确的进程内 Context projection，具备上面的线性化/冲突行为；
- `commit=None` 只被测试为内存 reducer successor，不被描述为 durable commit。

以下内容**不能**以当前计划宣称已达到实施条件：统一持久化、exchange 与 Graph candidate
原子提交、crash-after-execute-before-commit、ack-lost reconcile、跨进程恢复和跨进程
去重。这些应作为后续 persistence/recovery owner 的独立验收，不因本轮 Act v1 通过而默认
获得。

### 8.6 二次复审 MR 意见（可直接粘贴）

> 二次复审结论：Act 四节点拓扑、Approval 作为 Authorize 的 interrupt/resume 分支、
> 唯一 Graph/GraphRunState/Commit 边界以及 terminal ToolResult + Graph failure 的结果
> 语义已经闭合。**v1 进程内实施条件基本满足，有条件通过，可进入 Phase 0；进入 Phase 1
> 前请冻结四项 P1：ToolExchangeWriter 的 owner/线性化幂等与写入后失败语义、Approval
> resume 调用方构造完整 `AuthorizationInput` 的 helper 或保留规则、invocation/tool
> call identity 冲突域和稳定 import 路径，以及 ActNode 必须采用的具体 Graph 子类型/组装
> 边界，并补齐端到端 deterministic tests。**
>
> durable Context/AgentState、Graph candidate 与 exchange 的原子提交、崩溃/ack-lost 恢复和
> 跨进程去重不属于本期，当前不能据此宣称生产就绪。Failover 按独立 owner 的既定方向接入；
> 本评审不评价其装饰器、策略、重试图或固定图，Act 只接收装配完成的 typed capability，
> 不在四节点内实现 retry loop。

### 8.7 本轮检查记录

- 评审文档空白检查：通过（无尾随空白）。
- `make check`：未通过。当前混合工作树先在 `compiler.py`、`test_compiler_contract.py`
  的 Ruff 导入检查处停止；经仓库 pre-commit 自动整理后，复杂度 ratchet 仍因既有
  execution/failover/feedback 改动超出基线而失败。该失败不来自 Act（当前尚无 Act 实现），
  不能作为 Act 计划已实现或未实现的依据。
- `pre-commit run --all-files`：除上述全局复杂度 ratchet 外的检查通过；本轮不据此宣称
  Act 代码门禁通过。

## 9. 三次复审（2026-09-04）

### 9.1 本轮审查基线与结论

本轮在保留前两轮审计证据的基础上，加入两条不可放宽的范围约束：

1. 任何实际交给模型的文本、ToolResult 展示内容、审批摘要、错误/状态提示和 transcript
   projection 都必须是英文；本评审文档是给人看的中文说明，不属于该运行时边界。
2. Kernel 不定义任何业务具体实现语义。这里的“定义”不等于禁止 Kernel 或其明确的
   domain-contract 模块声明 Port 所需的 nominal DTO 形状；Port 可以声明 request/result
   类型，节点也可以在返回处做 exact admission。禁止的是 Kernel 代替业务 owner 决定这些
   DTO 的具体业务含义、策略算法、外部副作用、模型文案和协议映射。

按这两条约束重新检查当前实施计划后，上一轮的“v1 进程内有条件通过”结论仍不能直接沿用。
当前版本在多个模型可见路径中保留中文文案，也没有清楚区分“Port 所需的 DTO 契约形状”和
“Kernel 固定的业务实现语义”。因此，**当前版本尚未达到实施条件，不能按现文直接进入
Phase 0**；但不要求把所有 `ToolResult`/Port DTO 一律移出 Kernel。应先完成 owner 分层、
契约形状与语义实现的拆分，再重新评审。

Failover 仍按用户指定的独立方向处理，本轮不评价其具体 Port 装饰器、策略、重试图、固定图
或 assembly 实现。这里仅要求未来的外部装配把已装饰的 typed Port 注入 domain graph。

### 9.2 模型可见内容审计（P0）

中文解释性文字留在 `*.zh-CN.md` 并不违反语言约束；违反约束的是会进入 provider transcript、
ToolResult、approval payload 或模型 presentation 的运行时字段。当前计划存在以下直接证据：

| 模型可见面 | 计划中的证据 | 问题 | 必须的处理 |
| --- | --- | --- | --- |
| 用户/审批拒绝的 terminal result | `safe_message="用户拒绝调用"`（第 0、3.2、5.3、7.4、10.2 节重复出现） | 明文中文会被作为 tool-role result 或 presentation 交给模型 | 保留字段形状并不妨碍；但禁止 Kernel 生成该文案。由 protocol/Product owner 提供并验证英文 typed payload |
| Approval interrupt 的展示摘要 | `redacted display summary`（第 4.3 节）没有语言契约 | 可能把本地化或未经审查的摘要送入模型 | 摘要由外部 approval/presentation owner 生成；Kernel 只传 opaque、已认证的 payload |
| terminal `error_code`/Graph failure 映射 | `act.user_rejected`、`act.approval_invalid` 等固定映射（第 3.2、5.3 节） | 这是 provider-facing 业务协议和模型可消费错误代数，不是通用 Graph 语义 | 若这些代码是已批准的 Act 协议，可由明确的 Act/protocol contract owner 声明；Kernel execution 不得解释、生成或路由它们，且模型可见代码/文本必须由外部 owner 保证英文 |
| failure/evidence/result payload | 计划允许 `safe_message`、failure evidence 和 result 直接进入 ToolExchange | 类型检查不能证明内容是英文，工具返回的任意自然语言也可能越过模型边界 | Port 可以返回声明的 DTO，Kernel 只做结构/admission；最终 serialization/presentation owner 定义 English-only contract 和测试，Kernel 不翻译、不生成 fallback 文案 |

即使把上述中文替换成英文，也不能自动解决 owner 越界：`UserRejectedToolResult`、拒绝文案、
展示摘要和 transcript 配对本身仍是业务/协议语义。正确做法不是在 Kernel 内增加语言检测器或
一套本地化表，而是让明确的协议/domain owner 产生一个已经满足 English-only 约束的 nominal
value（或受保护的 opaque bytes），再由 Port 声明该 DTO；Kernel 只按声明的类型和通用
Graph value/interrupt 规则传递、校验它。

这里还必须明确“所有给模型看的东西”的范围。若工具结果或用户原文允许任意自然语言，计划
不能一边声称全量 English-only，一边仅限制 `safe_message`；应由 provider/presentation owner
决定是拒绝非英文内容、翻译后再投递，还是把它标记为不进入模型 transcript。该策略及其检测器
不应落入 Kernel。验收应对最终序列化的 model-facing transcript、ToolResult 和 approval
summary 做 deterministic English-only 检查；不应对中文评审文档或普通内部日志做无意义的全局
Unicode 扫描。

### 9.3 Kernel 业务语义与 DTO 契约的分层审计（P0）

本轮需要修正一个容易误读的结论：**DTO 放在 Kernel 并不自动等于 Kernel 定义了业务实现**。
如果 Act 是 Kernel 明确拥有的可复用 Agent flow，Kernel 可以提供该 flow 的最小 nominal
DTO/Port contract；每个 Port 声明它接收和返回的具体 DTO，节点在边界做 exact admission。
真正越界的是把业务 owner 的解释、策略、外部副作用和模型展示行为写死在这些 DTO/节点中。

| 当前计划内容 | 可以保留的契约部分 | 不应由 Kernel 固定的实现语义 | owner/修改方向 |
| --- | --- | --- | --- |
| `Resolve → Authorize → Execute → Settle` | 若项目把 Act 作为 Kernel-owned reusable flow，可保留节点编排形状和 typed stage boundary | catalog 如何查找、策略如何判断、settlement 如何面向产品交付 | Kernel 只保留编排/执行机制；具体业务适配由 Act/Agent owner 注入 |
| `ActNode`、`ActGraphValue`、`mote_kernel.act.*` | 可作为明确 owner 的 contract module，声明 Graph value marker、Port protocol 和 admission | provider/tool 的具体实现、默认策略、业务状态机和展示行为 | 在文档中写清 owner；若 Act 不属于 Kernel，则整体移到 domain 包 |
| `ActRequest`、`ResolvedInvocation`、`AuthorizedInvocation`、`ToolExecutionResult`、`ActSettlement` | Port 所需的 immutable nominal DTO 形状、字段不变量和 exact return validation | 字段的业务解释、如何解析 binding、如何产生副作用、如何本地化 | contract owner 定义形状；Port/adapter 构造并负责业务语义 |
| `Allowed/Denied/Ask`、`Approved/Rejected/Pending` | 可作为闭合的 typed control outcome，供节点做结构化分支 | 谁能批准、策略规则、风险模型、审批 UI、重试/过期决策 | Authorization/Approval owner 提供具体值；Kernel 不自行推断或替换 |
| `ToolCall`/`ToolResult`、`tool_call_id`、`ToolExchangeScopeId`、`ToolExchangeWriter` | Port 可以声明一个 `ToolExchange`/writer DTO 形状，Kernel 可校验类型和基本引用一致性 | transcript 配对政策、去重/receipt、Context projection、持久化和孤儿处理 | Context/protocol owner 实现；Kernel 只依赖窄 typed capability，不拥有 projection state |
| `UserRejectedToolResult`、`AuthorizationDeniedToolResult` 等 variant、`act.*` code | 若它们是项目批准的统一 Act 协议，可作为 domain contract 暴露给 Port | 由 Kernel 生成何种 variant、错误码映射、父图是否继续、模型如何呈现 | 明确由 Act/protocol owner 构造；Kernel 仅做声明的 exact admission，或把它们作为 opaque payload |
| `ApprovalInterruptCodec`、HMAC、display summary、大小限制 | Kernel 可要求一个通用 bytes codec/length admission 接口 | 编码字段、密钥生命周期、脱敏、语言和安全策略 | Approval/security owner 注入 codec；Kernel 不实现 Approval-specific policy |
| “拒绝先写 pair 再 `Graph.failure`”“Execute 至多一次” | Graph 可保证一次 node activation、异常/取消传播和 commit 边界 | 何时必须生成 ToolResult、如何配对、一次业务调用的幂等含义 | 业务不变量留在 Act/Tool protocol owner；Port 返回已构造的 typed result |

因此，正确的判断标准不是“这个名字像不像业务 DTO”，而是“谁声明并解释它、Kernel 是否
自行生成/改写其业务含义”。`mote_kernel.act` 可以存在，但必须明确它是可复用 contract
owner 还是某个 Product 的实现包；不能一边把 Product 语义写死，一边只用“窄 Port”名义
宣称已完成解耦。

### 9.4 必须先关闭的 P0

| ID | 阻断项 | 当前计划中的具体表现 | 关闭条件 |
| --- | --- | --- | --- |
| P0-1 | contract 与 implementation owner 未分层 | 第 8 节把 Act DTO、Port、codec、writer 与节点实现全部列为 `mote_kernel.act` 固定面，但未逐项说明哪些只是可复用形状、哪些是业务实现 | 为每个 DTO/Port/codec 标注唯一 owner；允许 Kernel 声明所需 DTO 形状，但业务构造、解释和副作用必须由外部 adapter/owner 提供 |
| P0-2 | Kernel 固定业务结果和错误协议 | `TerminalToolResult` 全部 variant、`act.*` failure、拒绝/失效映射写成不可变 contract | 若这些是批准的共享 Act 协议，可保留类型声明；Kernel 不自行生成/解释/路由 variant 或 code，具体值由 domain/protocol owner 返回并通过 exact admission |
| P0-3 | 模型可见语言不合规 | `safe_message="用户拒绝调用"`，以及未定义语言的 approval summary/evidence/result | 删除中文默认文案；外部 protocol/presentation owner 对最终 model-facing serialization 建立 English-only contract 和 deterministic tests；Kernel 不翻译或生成 fallback 文案 |
| P0-4 | 协议 projection/副作用 owner 越界 | `ToolExchangeWriter.append_once/observe`、pairing index、receipt 和孤儿语义被写入 `mote_kernel.act.port` | writer、receipt、transcript scope 和去重由 Context/protocol owner 实现；Kernel 可声明 writer request/result DTO，但不维护第二 projection state |
| P0-5 | Approval 安全与恢复语义越界 | Act codec 固定 approval envelope、HMAC、protected context、Pending/Rejected 规则和大小上限 | Kernel 只声明通用 bytes codec/interrupt seam；Approval/security owner 提供 codec 和安全策略，Kernel 不自行生成 Approval-specific payload 或决策 |
| P0-6 | Graph failure 被业务化 | 计划把 `Graph.failure` 固定为 `act.resolution_failed`、`act.user_rejected` 等并规定父图停止/不可 Think | Graph 只负责通用 failure propagation；业务 failure token、父图是否继续和 presentation 映射由外部 Flow owner 决定 |

前两轮已经关闭的异步边界、exact nominal admission、resume envelope、nested Graph 和 v1
durability 限定，不能抵消以上 P0；它们解决的是“如何可靠地传递一个已声明的 DTO”，而本轮
还要求先说明“谁拥有 DTO 的业务解释和副作用”。

### 9.5 在 Kernel 内可以保留的最小范围

将计划改写为“契约形状与实现语义分离”后，以下内容可以保留：

- 使用现有 `mote_kernel.execution.Graph` 作为唯一组合/执行门面；外部 domain graph 可以
  作为 nested `Graph` 接入，不能创建第二个 runner、executor、reducer 或 State owner；
- 如果项目决定由 Kernel-owned Act contract 提供可复用流程，可以声明 `ActNode`、
  `ActGraphValue` 以及各 Port 所需的 immutable nominal request/result DTO；这只是类型和
  连接形状，不代表 Kernel 实现工具目录、授权策略或 provider 行为；
- 每个 Port 明确声明 exact request/result class；节点在 await 返回处做 exact nominal
  admission 和基础引用不变量校验。错误类型、错误子类、裸字典、`Any`、Union 或非法
  envelope 在进入下游前拒绝；Kernel 不凭字段内容猜测业务含义；
- 使用现有 `Graph.success()`、`Graph.failure()`、`Graph.interrupt(bytes)` 和
  `Graph.resume_interrupted()` 的通用传播边界；interrupt bytes 的业务 codec、内容、语言和
  安全策略由外部 owner 注入；
- 遵循现有 callable 的 awaitable、普通异常、`CancelledError` 和 resource cleanup 语义；
  不把取消或异常改写成某个业务结果；
- 遵循唯一 `GraphRunState`、纯 reducer、exact candidate `Graph.Commit` 和确认后替换内存
  snapshot 的边界；不在 Kernel 增加第二份业务事实、Context writer 或 outbox；
- 通过通用 typed capability/`Invocation` seam 注入外部能力。Port 返回的具体业务值由其
  owner 构造；Failover 按独立 owner 接入，本轮不审查其内部实现。

因此，外部 owner 可以继续设计一个四阶段 Act graph；它可以复用 Kernel 提供的 contract
形状，也可以在外部定义自己的 DTO，但“Port 声明 DTO + Kernel 校验返回”必须写成唯一的
边界规则。无论 DTO 放在 `mote_kernel.act` 还是外部 domain 包，Kernel 都不能替 Port
决定业务结果的含义、错误映射、模型文案或副作用策略。

### 9.6 达到实施条件前的修订顺序

1. 在文档首页和 MR 中列出每个 contract 的唯一 owner，区分 Kernel 可复用的 DTO/Port
   形状、Act/Agent 业务语义、Context/protocol projection、Approval/security codec 和
   persistence；保留 Failover 为独立 owner。
2. 保留必要的 nominal DTO 声明（若 Act contract 确由 Kernel owner），但将 DTO 的构造、
   业务解释、策略计算、外部副作用和 presentation 映射交给对应 Port/adapter；不能因为
   DTO 在 Kernel 就让 Kernel 生成业务结果。
3. 把 `ToolExchangeWriter`、receipt、transcript pairing、approval codec 的具体实现和
   安全策略放到 Context/protocol/approval owner；Kernel 只保留窄 protocol 以及 request/result
   exact admission（若该 protocol 明确归 Kernel contract）。
4. 为所有模型可见字段建立外部 English-only contract：包括 ToolResult、approval summary、
   failure/evidence、最终 transcript 和任何 fallback 文案。Kernel 不做翻译、语言检测、
   本地化或默认拒绝文本生成；不能用把中文改成英文来掩盖 owner 越界。
5. 将 `Graph.failure` 的业务 code、父图是否继续和 terminal projection 规则改为外部
   adapter 行为；Kernel 文档只保留 generic failure propagation，不注册未经 owner 批准的
   `act.*` 命名空间。
6. 为每个 Port 增加 deterministic return-admission 测试：声明的 request/result exact class、
   closed variant、引用一致性和安全 envelope 在下游调用前校验；非法值按通用 Graph
   contract 失败，不能被 Kernel 猜测或改写成另一个业务 variant。
7. 补齐跨包依赖和架构测试：`execution` 不反向依赖 domain；若 `mote_kernel.act` 保留，
   其代码不得包含未标注 owner 的业务实现；外部 domain 的 nested/interrupt smoke test 使用
   真实 `Graph` API；模型 serialization tests 证明所有交给模型的内容是英文。
8. 完成以上 owner/validation 重划分后重新提交本评审；在此之前不要按现有 Phase 0/1 任务
   创建未定 owner 的 Kernel 生产实现。

### 9.7 新的验收门槛

只有同时满足以下条件，才可把“Kernel 接入部分”标为可实施：

- **Owner/依赖**：若 Kernel 是已批准的 Act contract owner，可以保留 Port 所需的 Act DTO/
  variant 形状；但每个类型必须标注 owner，Kernel 不包含未授权的业务构造、policy、
  presentation、Context projection 或 codec 实现。外部 domain 通过现有 Graph/Invocation
  seam 注入，不能有反向 import；
- **通用执行**：nested Graph、exact value admission、awaitable node、interrupt/resume、
  cancellation、failure propagation 和 single `GraphRunState`/Commit 路径有 deterministic
  测试；测试不需要读取私有 continuation/frame；
- **模型语言**：外部协议 owner 对最终 model-facing bytes/text 建立 English-only contract，
  覆盖正常 ToolResult、拒绝/错误、Approval summary、evidence、fallback 和重放路径；测试
  断言无未审查的中文文案，且任意不满足语言策略的工具 payload 不会静默送入模型；
- **Port admission**：每个 Port 声明所需 request/result DTO，Kernel 在返回处做 exact nominal
  admission 和通用不变量检查；非法返回 fail closed，不由 Kernel 猜测或转换业务含义；
- **副作用**：Context/ToolExchange writer、receipt、transcript 去重和持久化由其 owner
  负责；Kernel 不维护第二 state/store，不把 `commit=None` 或内存 projection 宣称 durable；
- **范围**：Failover 的 Port 装饰器、策略、重试和固定图仍由独立 MR 验收，本评审不把它们
  作为 Kernel 通过证据。

### 9.8 三次复审 MR 意见（可直接粘贴）

> **Request changes：当前实施计划尚未达到实施条件。** 本轮新增约束要求：所有实际交给模型的
> 内容必须是英文，且 Kernel 不定义任何业务具体实现语义。计划第 0、3.2、5.3、7.4、10.2 节
> 仍把 `safe_message="用户拒绝调用"` 及 approval display/evidence 作为模型可见契约；这部分
> 文案必须改由协议 owner 提供英文值。另需说明：Port 声明所需 DTO 并由 Kernel 做返回校验
> 是可行方案，`ToolResult`/decision 等类型不必一律移出 Kernel；但 `UserRejectedToolResult`、
> `act.*` 错误映射、ToolExchange pairing、Approval codec 和 writer 的具体业务解释/副作用
> 不能由 Kernel 自行生成或决定。
>
> 请先拆分 Kernel integration 与外部 Tool Use/Agent/Context/Approval protocol：Kernel 可以保留
> 明确 owner 的 Port request/result DTO 形状，并在 node/port 边界做 exact return admission；
> 但业务 DTO 的构造、策略、ToolExchange/writer 副作用、Approval codec 和 presentation 映射
> 必须由对应 owner 提供。Kernel 只负责通用 `Graph`/nested Graph、typed value admission、
> interrupt/resume、取消/异常传播和唯一 State/Commit 边界，不生成或本地化模型文案，也不擅自
> 注册 `act.*` failure code。由外部 protocol/presentation owner 对最终 model-facing
> serialization 建立 English-only deterministic tests。完成 owner/validation 重划分后再重新
> 评审。Failover 按既定方向由独立 owner 接入；本 MR 不评价其具体 Port 装饰器、策略、重试图
> 或固定图。

### 9.9 本轮检查记录

- 计划与评审文档的模型可见字段、业务类型、错误码和 owner 依赖已逐项复核；未修改实施计划，
  仅更新本评审文档。
- `git diff --check` 应作为交付前门禁；当前工作树已有 execution/failover/feedback 改动，
  前两轮记录的 `make check`/全仓 pre-commit 失败原因仍适用，不能据此宣称 Act 代码已通过。
- 当前没有 Act 生产实现或专项测试；本轮结论是文档范围/契约不通过，不是代码测试结论。

### 9.10 对“Port 声明 DTO、Kernel 校验返回”的明确答复

这个方案是正确的，也是本计划应采用的基础边界。可以按下面的分层理解：

```text
Port contract owner
  └─ 声明 request/result 的 nominal DTO（或由 Kernel contract owner 提供其形状）
Port implementation
  └─ 构造并返回该 DTO，承载具体业务语义
Kernel node boundary
  └─ exact type/admission 校验；通过后才发布到下一节点
Graph engine
  └─ 只处理 success/failure/interrupt、State/reducer/Commit 和取消/异常传播
```

例如 `ExecutePort.execute()` 可以声明接收 `AuthorizedInvocation`、返回
`ExecutePortResult`。Kernel 应校验返回值是声明的 exact nominal class，校验必要的通用引用
不变量，并在非法返回时 fail closed；它不应根据字段内容自行把结果改成
`UserRejectedToolResult`、`Unknown` 或某个 `act.*` code。后者应由 Port/domain adapter
构造后交给 Kernel 做类型校验。

返回校验能保证的是结构和通用安全边界：类型、不可变性、引用配对、长度、codec version
等。它不能替代业务 owner 对以下问题作决定：结果究竟表示拒绝还是工具失败、是否要写
ToolExchange、是否允许父 Flow 继续、外部副作用是否已发生，以及文本是否适合发送给模型。

因此本轮不是要求“ToolResult 一律移出 Kernel”，而是要求在计划中逐项写清：

- `ToolResult`/decision 等 DTO 的**形状**可以作为已批准的 Act contract 留在 Kernel；
- concrete Port/adapter 负责填充其业务值，Kernel 只做声明的 exact admission，不做语义猜测
  或自动转换；
- `ToolExchangeWriter`、receipt、transcript 去重、审批安全策略和具体 codec 实现由各自
  owner 提供，Kernel 仅依赖窄的 typed protocol（若该 protocol 被批准为 Kernel contract）；
- 任何最终交给模型的字段（包括拒绝文案、错误文本、审批摘要、工具结果中的自然语言和
  fallback）必须在 protocol/presentation owner 的 English-only serialization 边界通过
  deterministic tests；Kernel 不生成中文或其他本地化默认文本。

按此修正后，P0 的名称应理解为“业务语义 owner 未明确且模型语言契约未闭合”，而不是“DTO
放在 Kernel 本身违规”。

### 9.11 最新复审（2026-09-04）

#### 9.11.1 结论

以当前第三版实施计划为准，前一轮关于“所有 ToolResult/DTO 必须移出 Kernel”的判断不再适用。当前计划已经采用正确的边界：Port 声明所需的 request/result DTO，Port/adapter 构造具体值，Act 节点在返回处做 exact nominal admission；`mote_kernel.execution` 只负责通用 Graph 执行、State、reducer、Commit、interrupt/resume 和取消传播。

因此结论分两层：

- **架构方向：通过。** 四节点拓扑、Approval 位于 Authorize、`ActNode(Graph[ActGraphValue])`、nested Graph 和唯一 Graph/State/Commit 路径均与当前源码一致；Failover 按独立 owner 接入，本评审不评价其实现。
- **实施条件：有条件通过。** 可以开始不接 Graph 的 Phase 0 契约工作；尚不能按现文进入 Phase 1 四节点生产实现。以下 P1 必须先在 contract 和 deterministic tests 中闭合。当前仓没有 Act 生产实现或专项测试，本文不是代码已通过的证明。

#### 9.11.2 本版已闭合的关键项

| 项目 | 结论 | 依据 |
| --- | --- | --- |
| DTO 所有权 | 通过 | `mote_kernel.act` 可拥有可复用 flow/contract 的类型形状；具体业务值、策略、ToolResult variant 和 provider serialization 由外部 owner 构造 |
| 返回值校验 | 通过设计 | 每个 Port 声明 exact request/result class，节点在下游调用前做 nominal/admission 校验；非法返回 fail closed，不猜测或转换业务语义 |
| 模型语言 | 通过设计、待外部测试 | Kernel 不生成文案；Provider/protocol/presentation owner 负责最终 serialization 的 English-only contract |
| Graph 边界 | 通过 | 无第二 runner/State/reducer/Store；Act 复用现有 nested Graph 和 `Graph.Commit` |
| durable 范围 | 明确排除 | v1 只做进程内 projection；持久化、崩溃恢复、跨进程去重和 exactly-once 延后到统一 persistence owner |

#### 9.11.3 进入 Phase 1 前必须关闭的实施阻断

1. **Settle 缺少 request/identity plumbing（P1）。** 计划要求每个 callable 先执行 `observe(request)`（第 3.3、6 节），但固定拓扑中的 Settle 只有 `ToolExecutionResult` 输入（第 4.2 节）；该 DTO 没有 `ActInvocationKey`、原始 `ActRequest` 或 `ToolBindingRef`，而 `SettlementPort.project(result)` 和 `ActSettlement` 又需要这些信息。这样无法在 Settle 调用 writer 前验证 pairing，也无法在重放时构造完整 `ActSettlement`。应增加显式的 `SettlementInput`（携带 request/稳定 identity/执行结果）并接入拓扑，或让 result/projection 携带同等 identity；不得靠闭包或隐藏可变状态补齐。

2. **writer/replay 语义尚未冻结（P1）。** `append_once` 在同一 projection 重放时应返回既有 receipt，在同 key 不同 projection 时应返回明确 typed conflict，并定义进程内 retention/孤儿观察行为。另需规定 `SettlementPort` 是基于已提交执行结果的纯确定性 projector，还是具备稳定幂等/reconcile 能力；目前允许异步访问远端，却又要求 replay 产生相同 projection，二者尚未闭合。没有这一规则，不能证明重放不会重复副作用或产生不同模型结果。

3. **Approval resume 调用次数和入口语义有歧义（P1）。** 第 4.3 节说恢复不调用 `AuthorizePort`/`ApprovalPort`，第 9 节和第 10.3 节又写“重新执行 Authorize”。必须明确“Authorize node activation”与“AuthorizePort 调用”是两个概念，并冻结矩阵：初次 Allowed 为 `ResolvePort=1, AuthorizePort=1, ApprovalPort=0, ExecutePort=1`；初次 Ask/Pending 为 `ResolvePort=1, AuthorizePort=1, ApprovalPort=1, ExecutePort=0`；approved resume 执行 Authorize node 一次但 `AuthorizePort=0, ApprovalPort=0`，随后 `ExecutePort=1`；rejected resume 同样两个 Port 均为 0、Execute 为 0，并只写一次 terminal projection。当前 Graph 仍公开继承的 `resume_interrupted()` 和可构造的 `Graph.ResumeAction`，所以“唯一入口是 `ActNode.resume_approval()`”无法仅靠文档强制；应明确 helper 是便利入口并由 engine/codec 统一校验，或设计可验证的 sealed/provenance action。

4. **条件 Approval 能力缺少 typed assembly fact（P1）。** “policy/profile 可能 Ask 时才要求 ApprovalPort/ApprovalCodecPort”没有对应的显式 profile/capability declaration；动态策略不能通过反射在构造期猜测。应改为 v1 一律装配，或增加不可变的 typed profile（例如明确声明 `may_ask`）并规定运行期出现未声明 Ask 时的 fail-closed 行为。

5. **执行结果的敏感内容边界不足（P1）。** 第 5.3 节允许 `CanonicalToolExecutionPayload` 包含任意工具领域内容，但它会进入 Graph value/frame，后续可能进入 continuation、日志或持久化边界；与 arguments 不同，正文没有强制在进入 Kernel 前将 confidential/raw secret 改为 protected reference。必须由 execution-result codec/admission 在构造 payload 前完成分类，明确 inline/ref、脱敏、大小和 evidence/frame 规则，不能把“最终不发给模型”当成状态安全证明。

6. **model-facing 术语和 contract 形状仍需统一（P1）。** `ActRequest` 在第 5.1 节被称为“模型 ToolCall 视图”，第 8、12 节又笼统写成 Kernel“不定义任何 model-facing DTO”。应明确区分“模型产生的 inbound request envelope”和“发送给模型的 outbound projection”；后者不得由 Kernel 定义。第 3.2 节仍写 Authorize“接收 `ResolvedInvocation`”，却与第 4.2/4.3 节冻结的 `AuthorizationInput` 输入不一致，必须统一为一个 exact descriptor。`ApprovalRequest`、`AuthorizationFacts`、`ToolDefinitionSnapshot` 等在 Port 签名中被引用但尚未给出完整 nominal shape/owner/import path，也必须在 Phase 0 固定，否则不能实施 strict typing。文中“用户拒绝了这次工具调用”只能作为人类可读语义标签，最终模型 payload 必须由外部 owner 生成英文值；应避免把中文示例误读为运行时默认文案。

7. **仍有业务语义动作词落在 Act node（P1）。** 第 3.2 节要求节点自行“校验主体、资源目标、风险级别”，其中风险/策略事实属于 Authorization adapter 的业务解释，不是 Kernel 的通用 admission。应改为：adapter 负责计算并构造 authorization facts，Act 只校验 exact DTO、引用/版本/digest 等结构不变量；同理，父图是否停止和具体拒绝映射应标注为外部 Flow/protocol contract。否则即使 DTO 放在 Port contract 中，Kernel 仍会通过字段检查间接定义业务实现语义。

#### 9.11.4 验收门槛

- 关闭上述七项并补齐 exact DTO、identity、writer、resume 和 assembly tests 后，Phase 1 才可进入四节点实现。
- 外部 protocol/presentation owner 必须提供覆盖 success、business failure、denial、approval、evidence、fallback 和 replay 的 English-only serialization tests；Kernel 只测试 payload opaque、不会生成或改写模型内容。
- v1 仍不得宣称 durable、crash recovery、跨进程恢复、跨进程去重或 exactly-once；这些能力须单独由 persistence/recovery owner 验收。
- `make check`/全仓 pre-commit 的既有 execution/failover/feedback 工作树失败不能作为 Act 计划通过证据；当前没有 Act 代码可供该门禁验证。

#### 9.11.5 可直接粘贴的最新 MR 意见

> **Conditional approval / Request changes before Phase 1：** Act 四节点拓扑、nested Graph、唯一 Graph/State/Commit 边界，以及“Port 声明 DTO、Kernel 做 exact return admission”的方向合理，ToolResult/decision DTO 不需要一律移出 Kernel。当前可以开始 Phase 0 契约工作，但还不能按现文进入 Phase 1：请先补齐 Settle 的 request/identity plumbing，冻结 writer 的 replay/conflict 语义和 SettlementPort 的确定性/幂等边界，消除 Approval resume 的 node-vs-port 调用次数歧义并明确 typed assembly profile，收紧 execution payload 的 secret/protected-ref admission，统一 inbound/outbound model-facing 术语并补齐所有引用 DTO 的 nominal owner/import path，同时把主体/资源/风险等业务判断移回 Authorization adapter。所有最终交给模型的内容必须由外部 protocol/presentation owner 序列化为英文并以 deterministic tests 证明；Kernel 不生成、翻译或解释业务 ToolResult。Failover 按独立 owner 的具体 Port 装饰器方向接入，本评审不评价其内部实现。durable/跨进程恢复/exactly-once 不属于 v1。

## 9.12 用户决策后的复审（2026-09-04，历史中间结论）

> 本节记录在最终采用 Port-level Failover 之前的收敛过程；其中“外层显式提供同一
> `ToolExecutionResult` 重投影”的表述后来被 9.15/9.16 的 Port-level Failover 方案
> 取代；9.16 又已被第 9.17 节记录的边界取代。当前实现不得增加 Settle-only Graph 入口，
> 最终口径以第 9.19 节为准。

### 9.12.1 三项架构决策已收敛

在 9.11 的基础上，用户已明确以下实施口径，实施计划已同步采用：

1. **Settle 只接收一个输入。** Graph 的 Settle node 只接收一个带
   `ToolExecutionIdentity` 的 `ToolExecutionResult`，不新增 `SettlementInput`，也不靠闭包
   保存 request。identity 至少包含 pairing、binding、request revision 和 arguments
   digest；Execute node 在发布 result 时 exact-admit 这些字段，SettlementPort 返回的
   projection 必须回显同一 identity。
2. **失败重跑覆盖未完成 projection。** v1 的重跑定义为同一执行结果再次进入 Settle；
   `ToolExchangeWriter.upsert()` 按 pairing identity 原子覆盖尚未完成的旧 projection，
   不重复 `Execute` 或 `tool.call()`。不再要求“同 key 不同 projection 必须 conflict”，
   也不把 v1 writer 描述成 durable 或 exactly-once；进程退出后的恢复留给后续 persistence
   owner。若从 Execute 重新开始，重复副作用仍属于 Execute/failover owner 的幂等和对账问题。
3. **AuthorizePort 统一授权与外部确认。** Act 只装配一个必需的 `AuthorizePort`；
   它的外部 adapter 自己决定自动授权、人工确认和服务端确认；resume codec 作为同一
   Port 上的可选 `resume_capability`。Kernel 只处理 `Allowed`、`Denied(terminal)`、
   `Pending(opaque interrupt)` 三种结构结果，不再定义或装配独立的
   ApprovalPort/ApprovalCodecPort。没有外部确认配置时，adapter 采用 auto-only，将
   `resume_capability` 设为 `None`，只返回 Allowed/Denied 且不安装 Graph codec；意外
   返回 Pending 时 fail closed，不自动放行或伪装成用户拒绝。恢复继续使用同一个
   AuthorizePort。

### 9.12.2 对 9.11 P1 的处理结果

| 9.11 项目 | 最新结论 |
| --- | --- |
| Settle request/identity plumbing | **已闭合**：identity 进入 `ToolExecutionResult`，Settle 保持单一 Graph input，writer 按 pairing 观察 |
| writer replay/conflict 语义 | **已按 v1 范围闭合**：失败重跑使用 `upsert` 覆盖未完成 projection；不承诺进程退出后恢复，未来 persistence 再定义最终化 |
| Approval resume node/Port 调用次数 | **已闭合**：初次和 resume 都只经过同一个 AuthorizePort；没有第二个 ApprovalPort；Resolve 不重复，Execute 仅在最终 Allowed 后调用 |
| 条件 Approval assembly fact | **已闭合**：是否支持 Pending 由同一 AuthorizePort 的可选 `resume_capability` 表示；auto-only 为 `None`，Kernel 不增加 `may_ask` profile 或第二个 Port |
| execution payload secret/protected-ref | **已闭合设计、待代码测试**：result codec 在进入 Graph 前分类，Kernel 只准入 safe inline/reference 和固定大小 |
| inbound/outbound model-facing 术语与 DTO owner | **已闭合设计、待代码测试**：`ActRequest` 是 inbound envelope，outbound projection 由 protocol/presentation owner 定义；Port/import path 在 Phase 0 固定 |
| 主体/资源/风险判断落在 Act node | **已闭合设计、待代码测试**：事实由 AuthorizePort adapter 构造，Act 只校验结构、identity、revision 和 digest |

### 9.12.3 实施门槛

当前可进入 Phase 0 契约实现；Phase 1 仍必须用 deterministic tests 证明：

- 单一 `ToolExecutionResult` 的 identity 与 projection pairing 不串线；
- Settle 失败重跑只覆盖未完成 projection，不重调 Execute；
- 统一 AuthorizePort 的 Allowed/Denied/Pending/resume 分支和调用次数；
- auto-only adapter 返回 Pending 时 fail closed；
- secret/reference、opaque model payload、English-only serialization 和 Graph commit 边界符合
  实施计划；
- v1 不宣称 durable、crash recovery、跨进程恢复、跨进程去重或 exactly-once。

上述属于契约落地和测试工作，不再需要新的架构拍板。Failover 继续由独立 owner 负责。

## 9.13 实施计划完善后的复审（2026-09-04，历史中间结论）

> 本节保留当时对 contract/admission 的审计记录。其关于由外层重新提供 result 进行
> Settle 重投影的文字后来被 Port-level Failover 方案取代；当前实施清单以第 9.19 节为准。

实施计划已把 9.11 的 P1 落点写成可执行约束，当前不再有需要用户选择的架构分叉：

- `Settle` 的唯一 Graph 输入是带 `ToolExecutionIdentity` 的
  `ToolExecutionResult`；没有 `SettlementInput`、闭包身份或隐藏状态；
- Settle 重投影只允许在已有失败/未完成证据时由外层显式提供同一个 immutable result，
  使用 writer `upsert` 覆盖未完成 projection；已完成 invocation 不得重跑。这不是 Graph
  failed-node retry，也不重新调用 Execute；
- `AuthorizePort` 是唯一授权/确认依赖。auto-only 时仍保留 Authorize 节点；不存在
  独立 `ApprovalPort`/`ApprovalCodecPort`，auto-only 时 `resume_capability=None` 且不安装
  Graph codec；有 capability 时 resume 继续调用同一个 AuthorizePort；
- `resume_authorization()` 是 Act 集成唯一受支持的恢复入口；通用 Graph facade 的
  `resume_interrupted()` 仍由 execution owner 保留，有 resume capability 时所有恢复路径
  都必须经过同一 codec 和 exact admission；auto-only 没有恢复路径，不形成第二条授权执行
  路径；
- rejection 的顺序固定为“外部 adapter 构造英文 ToolResult projection → pairing admission
  → writer 确认 → `Graph.FailedResult`/stop”，拒绝不成为正常 ActResult；
- inbound `ActRequest` 与 outbound model-facing projection 已区分。Kernel 只传递 opaque
  projection/ref，不定义或生成模型内容；最终序列化必须由外部 owner 以英文完成；
- Phase 0 增加了 concrete nominal type inventory、assembly constructor seam、调用次数
  矩阵和 `observe` 行为矩阵；Phase 1 只需按这些固定契约实现代码与 deterministic tests。

结论：文档层面可开始 Phase 0/Phase 1 实施；剩余风险属于代码、外部 adapter 和协议
测试的验收，不再是架构决策。v1 的进程内、无持久化范围保持不变。

## 9.14 再次实施条件复审（2026-09-04，历史结论）

> 本节保留当时发现 Graph 没有 Settle-only 入口的审计证据。用户随后明确采用
> Port-level Failover 包住 `SettlementPort`/`ToolExchangeWriter` 的方案，因此本节的
> “必须增加 Graph recovery handoff”结论已被第 9.15 节撤销；不要按本节作为当前开码门槛。

### 9.14.1 结论：暂不通过 Phase 1 开码门槛

本轮把第 9.13 节的文字结论再次与当前 `Graph` 公共 API、恢复 admission 和计划列出的
测试落点逐项对照。结论需要修正为 **Request changes**：四节点正常路径的方向正确，
但计划仍包含一个当前无法实现的 Settle-only 执行入口，并把该入口所需的恢复证据和
writer 最终化事实留在了 typed contract 之外。按现文进入 Phase 1，开发者只能选择以下
任一种违规实现：直接调用内部 Settle callable、另建一张只含 Settle 的图/runner、从
Resolve 重新跑整图，或用闭包/全局对象保存上次 Execute result。

当前可以先做的仅是不会预判下列决策的 Phase 0 基础工作，例如 package/module
scaffolding、已经冻结 owner 的 identity wrapper，以及独立的 immutable/nominal
contract test。`ActRequest`/`ResolvedInvocation` 的完整 DTO、四节点 assembly、Settle
重投影及其测试暂不能视为可实施。

本轮确认以下方向已经闭合，无需反复讨论：

| 项目 | 本轮结论 |
| --- | --- |
| 授权/外部确认 | 通过：只有一个 required `AuthorizePort`；可选 `resume_capability` 是 typed assembly fact，auto-only 的非法 `Pending` fail closed |
| Settle 身份 | 通过：`ToolExecutionIdentity` 随单一 `ToolExecutionResult` 进入 Settle，不需要第二个 `SettlementInput` |
| resume 语义 | 通过方向：恢复再次调用同一个 AuthorizePort，Resolve 不重复；`resume_authorization()` 是 Act 集成的受支持 helper，通用 Graph admission 仍是最终边界 |
| payload 安全 | 通过设计：arguments/result/evidence 在进入 Graph frame 前必须成为受限 inline value 或 protected reference |
| 业务语义 owner | 通过：策略、主体/资源/风险解释、具体 ToolResult 和 presentation 均由外部 adapter/owner 构造，Kernel 只校验结构和 identity |
| 模型语言 | 通过设计、待外部集成测试：Kernel 不生成或翻译模型内容；最终交给模型的全部 serialization 必须为英文 |

### 9.14.2 P0-1：Settle-only activation 没有合法的 Graph 入口

计划第 3.2、3.3、3.5、7.2、9、10.2/10.4 和 12 节都要求：Settle/投影失败后，外层
显式提供同一个 immutable `ToolExecutionResult`，只重新激活 Settle，且不调用
Resolve/Authorize/Execute；同时又明确这不是 Graph failed-node retry，不新增
`resume_failed`、私有 runner 或第二条执行路径。

当前源码不能表达这个组合：

- [`execution/facade.py`](../src/mote_kernel/execution/facade.py) 的新 run 只接收整张图的
  declared graph input；continued run 只接收 `state`、可选 sealed `continuation` 和
  interrupt `resume` action。它没有 node entry、Settle input override 或任意节点重入 API；
- `Graph.resume_interrupted()` 只能构造 `ResumeInterruptedNodeRequest`；
  [`execution/engine/resume_admission.py`](../src/mote_kernel/execution/engine/resume_admission.py)
  要求目标当前确实是同 scope、同 ID 的 `InterruptedGraphNode`，不能用于 Settle exception；
- typed `Graph.failure()` 会令图进入 terminal failed disposition；
  [`execution/engine/superstep.py`](../src/mote_kernel/execution/engine/superstep.py) 不会再次调度
  failed frontier；
- 普通 node callable 抛异常后，engine 可以把已提交的 execution lease fence 掉，使节点仍为
  pending；但 `Graph.run()` 不会向调用方返回这次失败后的新 continuation。使用
  `commit=None` 时调用方连该中间 state 也拿不到；即使外部 commit 捕获了 fenced state，
  Settle 所依赖的 Execute publication 仍在 continuation/frame 中，state-only recovery 会按
  现有历史 publication admission fail closed。计划要求调用方“重新提供 result”，而
  continued `Graph.run()` 又没有合法位置接收它。

因此，第 7.2 节的“启动一个仅针对 Settle 的受治理 activation”以及
`tests/act/test_settle_replay.py` 目前没有对应的 production seam。这不是补一个 fake test
可以解决的问题。

进入 Phase 1 前必须二选一并写回计划：

1. **建议的 v1 收敛：**删除 v1 的 Settle 重投影承诺、调用次数行和专项测试，把 writer
   已成功但 Graph 未完成的记录定义为 orphan/unknown，交给后续统一 persistence/recovery
   owner reconcile；v1 只实现一次正常 Settle activation。
2. **若 v1 必须支持重投影：**先由 `mote_kernel.execution.Graph` owner 增加并验收通用、
   sealed 的 recoverable-run handoff。它必须交付 exact state + continuation/frame、证明目标
   activation 仍为 pending，并让同一 Settle input 通过 Graph facade 恢复；Act 只能消费该
   通用能力。不得增加 `ActNode.replay_settle()` 直调 callable、第二张 recovery graph、私有
   executor 或隐藏 result cache。

### 9.14.3 P0-2：外部 owner 字段尚无可实现的 nominal boundary

第 5.0 节把 `ToolSelector`、`ToolCallId`、`CallerIdentity`、
`ToolDefinitionSnapshot` 以及具体 arguments/result/evidence 排除在 Kernel 类型清单之外，
却又直接把前四者写成 `ActRequest`/`ResolvedInvocation` 的字段类型；文中没有给出它们的
稳定 import path、Kernel 可依赖的 nominal base，或 `ActPayloadAdmission` 如何接收并冻结
这些 concrete classes。`request_revision` 仍写成裸 `int`，也与同节要求 identity/version
使用 nominal wrapper 的口径不一致。

这会使 `mote_kernel.act.contract` 无法同时满足以下三项：独立编译、strict typing、运行时
exact admission。实现者最终只能擅自导入某个业务包、退回 `object`/`Any`/Protocol，或重新
引入运行时反射；四种做法都不符合本仓边界。

这不要求 Kernel 拥有业务语义。与用户已确认的“Port 声明所需 DTO，Kernel 校验返回”一致，
计划只需冻结一条可实现路径。建议由 `mote_kernel.act` 声明最小、opaque、immutable 的
structural wrapper/ref，例如 ToolCall identity、selector envelope、caller protected ref、
definition snapshot ref/digest 和 request revision；外部 adapter 负责从业务对象构造它们，
Kernel 只做 exact class、长度、digest、reference 和相等性校验。若坚持外部 concrete class，
则必须完整定义类型注入/generic 方案、owner-facing import 和 runtime admission，不能留给
Phase 0 实现者自行选择。

### 9.14.4 P0-3：writer 覆盖授权与 SettlementPort 重投影安全性仍未成为 typed contract

当前 `ToolExchangeObservation` 只有 terminal/settlement projection + receipt 两种观察，
没有“未完成/已完成”、revision 或 replacement token；`upsert(projection)` 也只接收
projection 并返回 receipt。与此同时，计划要求：

- 外层先证明上一次 Settle 失败/未完成；
- writer owner 再原子判断是否允许覆盖；
- 已完成 invocation 必须拒绝重跑；
- 相同 result 可以再次调用一个可能访问远端的 `SettlementPort`。

这些事实没有进入 Settle input、Graph recovery evidence 或 writer request，
`observe → SettlementPort → upsert` 之间也没有 typed compare-and-set 前提。因此现有
签名无法用 deterministic test 证明“只覆盖未完成记录”，更无法防止 observation 后状态
变化。`SettlementPort` 目前也没有冻结为 pure deterministic projector，或声明稳定
idempotency/reconcile capability；“由外部 owner 负责”说明了所有权，但没有形成 Act
可以安全依赖的 Port precondition。

若按 9.14.2 的建议把重投影移出 v1，本项可同步从 v1 不变量和测试矩阵删除。若保留，计划
必须定义一条 typed、原子的闭环：谁签发未完成 evidence、它如何随 execution-owned recovery
seam 进入 Settle、writer 如何以 expected observation/revision 或等价 token 原子接受/拒绝
替换，以及 SettlementPort 以何种显式 contract 保证重复 project 安全或返回 reconcile。
这些 DTO 可以保持 opaque，具体判定仍归 recovery/Context/Settlement owner；Kernel 不需要
也不得解释业务完成语义。

### 9.14.5 修订后的开码边界

| 阶段 | 当前是否可开始 | 边界 |
| --- | --- | --- |
| Phase 0 module/已冻结 identity scaffolding | 可以 | 不能预设外部字段类型或 Settle recovery API；不得以 scaffolding 宣称契约完成 |
| Phase 0 完整 DTO/admission/Port contract | 暂不可以完成 | 先关闭 9.14.3，并对 9.14.4 的 replay contract 作保留或移出 v1 的明确选择 |
| Phase 1 四节点 assembly/happy path | 暂不可以 | 先关闭 9.14.2–9.14.4；否则实现会依赖不存在的入口、未声明类型或偷偷增加第二执行路径 |
| Phase 2/3 terminal 与 Authorize resume | 设计方向可保留 | 实施仍建立在 Phase 0/1 通过之上 |
| Phase 4 Settle replay | 暂不可以 | 移出 v1，或先交付 execution-owned 通用 recovery seam 和 typed writer/replay contract |

关闭上述三项后，正常四节点实现本身不再需要 Kernel 作业务策略选择。Failover 继续由独立
owner 按既定方向开发，本轮不评价其实现。

### 9.14.6 可直接粘贴的最新 MR 意见

> **Request changes before Phase 1：**统一 AuthorizePort、单一带 identity 的
> ToolExecutionResult、opaque projection、English-only 和 Kernel 不解释业务语义的方向
> 已通过；但计划尚未达到四节点开码条件。当前 Graph facade 没有“外层重新提供同一 result、
> 只激活 Settle”的入口，普通 Settle 异常后也不会向调用方交付包含 Execute publication 的
> 新 state/continuation。请把 Settle replay 移出 v1，或先由 execution owner 定义并验收
> sealed 的通用 recoverable-run handoff，禁止 Act 私有 runner/直调 callable/隐藏 result
> cache。同时补齐 ToolSelector、ToolCallId、CallerIdentity、ToolDefinitionSnapshot 等字段
> 的 nominal shape/import/type-injection 方案；若保留 replay，还必须把未完成 evidence、原子
> replacement admission 和 SettlementPort replay safety 写成 typed Port contract。完成前仅可
> 做不依赖这些选择的 Phase 0 scaffolding，不能宣称 Phase 0/Phase 1 contract 已冻结。所有
> 最终交给模型的内容仍必须由外部 protocol/presentation owner 序列化为英文；Failover 不在
> 本评审范围。

### 9.14.7 本轮检查记录

- 逐段复核了最新版 1864 行实施计划及第 9.13 节此前结论；
- 对照了 `Graph.run()`、`Graph.resume_interrupted()`、resume admission、terminal failed
  frontier 和 state-only publication recovery；
- 本轮只修改评审文档，没有修改 Act、execution 或 Failover 代码；
- `make check`：Ruff、format 和 strict pyright 通过；随后既有 complexity ratchet 失败，
  当前工作树指标高于基线（例如 `top_level_definitions 695 → 701`、
  `decision_points 2166 → 2260`）。本轮未新增生产代码，该失败来自工作树中已有的
  execution/failover 等源码变更，不是本评审文档造成；
- monorepo 根目录对本轮评审文件执行
  `pre-commit run --files mote-kernel/docs/act-tool-use-implementation-plan-review.zh-CN.md`，
  所有适用 hooks 通过（含 whitespace、line ending、large-file 和 detect-secrets）。

## 9.15 用户确认由 Failover 处理 Settle 写入后的当前复审（2026-09-04，历史中间结论）

> 本节记录当时的收敛过程；其中“仍需补齐”的工程项曾在 9.16 固化为当时的实施清单，
> 当前是否可开工以 9.17 为准。

### 9.15.1 结论：撤销 Settle-only Graph recovery 阻断

用户已明确：**Settle 写 ToolResult/投影失败时，由 Failover 重试写入；不增加
Graph 的 Settle-only 恢复入口。** 因此第 9.14.2 节提出的 recoverable-run handoff、
`resume_failed`、外层重新提供 `ToolExecutionResult` 和 `tests/act/test_settle_replay.py`
不再是 v1 要求，也不应实现。

当前正确的调用边界是：

```text
Settle
  -> SettlementPort.project()                 （若可能失败则由 composition 包 Failover）
  -> ToolExchangeWriter.upsert(projection)   （若可能失败则由 composition 包 Failover）
       ├─ transient failure -> Failover retry/reconcile
       │                       同一 operation identity/projection
       ├─ success            -> receipt -> Settle 正常返回
       └─ exhausted/unknown  -> Graph failure 或 reconcile/orphan
```

这里的 Failover 是包住**单个 Port 调用**的既有能力，不是包住整张 Act Graph 的第二个
runner。重试发生在当前 Settle callable 等待 Port 返回期间，因此：

- writer 的每次 `upsert` 都使用同一个 `ToolExecutionIdentity`、pairing key 和 projection；若重试 projector，则沿用同一个 `ToolExecutionResult`，其 projection 稳定性由 projector owner 的 contract 保证；
- 不重新调度 Settle，不从 Graph state 取回 result，不重新调用 Resolve/Authorize/Execute，
  更不会再次调用 `tool.call()`；
- acknowledgement 不确定时先按 Failover policy reconcile，不能盲目重复写；
- 重试预算耗尽、不可重试 contract error 或 reconcile 仍未确定时，沿普通 Graph failure/
  reconcile 边界结束，不伪造成功或用户拒绝；
- writer 已成功但后续 Graph settlement/commit 失败时，记录是 orphan/reconcile 候选，
  v1 不承诺重新进入 Settle，后续统一 persistence owner 再处理。

`SettlementPort` 若本身会访问远端，必须按其 operation semantics 绑定 Failover；纯本地且
明确不会产生 retryable fault 的 projector 才可直连。`ToolExchangeWriter.upsert()` 必须保证同一 logical identity 的
重试可安全确认/覆盖，且不同 scope、invocation 或 pairing 永不串线。Failover 的具体
策略、attempt 预算、错误分类和对账图仍由独立 owner 实现；本节只冻结 Act 能看到的 typed
接入契约。当前 `src/mote_kernel/failover/assembly.py` 仍是组装骨架，文档不把该能力
冒充为已经交付的生产实现。

### 9.15.2 当前仍需补齐的工程契约（不需要新的架构拍板）

| 项目 | 必须落地的内容 | 是否需要用户重新决策 |
| --- | --- | --- |
| Port-level Failover contract | `SettlementPort`/`ToolExchangeWriter` 的 operation semantics、可重试与不可重试错误、attempt budget、unknown→reconcile 顺序、writer 同一 identity/projection 重试、projector 同一 execution result 重试以及 composition 注入方式；不得重跑 Graph/Execute | 否，按本节固定口径实现 |
| Act nominal types/import | `ToolSelector`、`ToolCallId`、`CallerIdentity`、`ToolDefinitionSnapshot` 等字段的 concrete nominal owner/import path；`request_revision` 使用 nominal wrapper | 否，属于 Phase 0 类型实现 |
| 文档与测试同步 | 用 `test_settle_failover.py` 验证 Port 重试、不重复 Execute、identity 不串线；删除 v1 Settle replay 测试要求 | 否，属于实施和验收 |

### 9.15.3 Phase 1 开码门槛与验收

完成上表前，Phase 1 只进行不依赖具体实现的 contract scaffolding。完成后应由
deterministic tests 证明：

1. writer 第一次 transient failure、第二次成功时，第二次调用仍是同一
   `upsert(identity, projection)`，Execute 只发生一次；
2. 不可重试 contract error 不再次调用 Port；acknowledgement unknown 先进入 reconcile，
   没有安全证据时不盲重试；
3. Failover 耗尽后 Settle/Graph 得到明确 failure 或 reconcile，绝不提供 Settle-only
   activation；
4. 不同 invocation/scope 的 projection 不能被同一次重试覆盖；Graph commit 失败只产生
   orphan/reconcile 观察，不重复工具副作用。

这些是已确定方案的 typed contract 和测试工作，不再需要用户拍板。除上述工程落点外，
四节点拓扑、单一 `AuthorizePort`、拒绝先配对写入再 `Graph.FailedResult`/stop、模型
可见内容由外部 owner 以英文序列化以及 v1 不做专用持久化均保持不变。

### 9.15.4 可直接粘贴的最新 MR 意见

> **Request changes before Phase 1：**四节点拓扑、统一 AuthorizePort、单一带 identity 的
> ToolExecutionResult、opaque projection 和 English-only 边界已通过。第 9.14 节关于
> Settle-only Graph recovery 的阻断已撤销：Settle 的 SettlementPort/ToolExchangeWriter
> 由 composition 注入 Port-level Failover，在同一个 logical operation 内重试（writer 使用
> 同一 identity/projection，projector 使用同一 execution result），unknown 先 reconcile；
> 不增加 Graph recovery handoff，不重跑 Settle 或 Execute。
> 进入 Phase 1 前只需补齐 Port-level Failover 的 typed retry/reconcile/idempotency contract，
> 以及 ToolSelector、ToolCallId、CallerIdentity、ToolDefinitionSnapshot 和
> request_revision 的 nominal owner/import path，并同步 `test_settle_failover.py` 验收。
> 这些属于工程落地，不需要新的架构选择；v1 仍不承诺 durable、跨进程恢复或 exactly-once。

### 9.15.5 本轮检查记录

- 复核了实施计划中所有 Settle 写入、upsert、Failover、replay/recovery 和测试落点；
- 将 Settle 失败处理统一收敛为 Port-level Failover，明确没有 Graph Settle-only 入口；
- 保留第 9.14 节作为历史审计，当前结论以本节为准；
- 本节只修改文档，没有声称 `failover/assembly.py` 已完成生产实现。

## 9.16 最终实施级复审（2026-09-04，历史结论，已由 9.17 取代）

### 9.16.1 结论

本节覆盖并取代前面各轮中仍标为“Request changes”的历史结论。结合用户已经拍板的
四节点、单一 `AuthorizePort`、拒绝先配对再停止、Settle 失败由 Port-level Failover
重试以及 v1 暂不持久化，本计划现在**可以进入 Phase 0 和 Phase 1 实施**。没有新的
架构分叉需要用户选择；剩下的是必须按下列契约完成的工程交付和测试门槛。

“不做持久化”不等于“做一个脆弱的最小实现”。v1 仍须在单进程生命周期内完整处理
immutable value、并发写入、幂等、冲突、unknown acknowledgement、取消、硬重试预算、
安全 telemetry 和 nested Graph。只有 durable state、进程重启后的恢复、跨进程去重和
exactly-once 明确后置到统一 persistence owner。

### 9.16.2 已冻结的工程方案（实现不得自行改形）

1. **稳定类型落点。** `mote_kernel.act.identity` 提供 `ToolCallId`、`ToolSelector`、
   `CallerIdentityRef`、`ToolDefinitionSnapshotRef`、`RequestRevision`、
   `ProjectionRevision`、`OpaqueOperationToken` 和 `OpaqueContextReconcileRef` 等
   immutable nominal wrapper。外部 adapter 只负责构造/解释值；provider 的业务对象、
   ToolResult schema 和模型文本不直接进入 Kernel。这样不依赖未定义的产品 import，也不
   用运行时擦除的 `NewType`、`object` 或 `Any` 逃避 exact admission。
2. **单一 Settle 输入。** Settle Graph node 只有一个 `ToolExecutionResult` input；
   `ToolExecutionIdentity` 必须随 result 携带，projection 必须回显同一 identity。不得
   增加 `SettlementInput`、闭包身份或隐藏 result cache。
3. **Writer 原子语义。** `ToolExchangeWriter` 接收 typed
   `ToolExchangeWriteRequest`，返回 `ToolExchangeWriteResult`。写入在一个原子 CAS/upsert
   边界完成：已完成记录的同 identity+digest 返回
   `ToolExchangeAlreadyWritten`；未完成记录只有 expected revision/token 匹配才可
   `ToolExchangeReplaced`（同 digest 也要经过一次明确的替换/确认）；已完成、phase 不兼容、
   不同 scope/invocation 或 pairing 冲突返回 typed `ToolExchangeConflict`；acknowledgement
   unknown 先返回 reconcile reference，不盲写。`ToolExchangeWriteAccepted`/
   `ToolExchangeAlreadyWritten`/`ToolExchangeReplaced` 才能让 Settle 产出成功 result。
4. **Port-level Failover。** composition 分别包住 `SettlementPort.project` 和
   `ToolExchangeWriter.upsert`，每层各自捕获 immutable operation request、identity、plan
   revision、deadline 和 hard cap。writer 重试完全复用同一 projection/token；projector
   重试完全复用同一 `ToolExecutionResult`。`Unknown`/`InProgress` 先 reconcile；contract
   error、身份冲突和 `CancelledError` 不重试；预算耗尽沿普通 Graph failure/reconcile
   边界结束。Failover 不包整张 Act、不重新激活 Settle、不重新调用 Execute。
5. **Projector 安全门槛。** `SettlementPort` 在 assembly 时必须声明 `PURE`、
   `IDEMPOTENT` 或 `RECEIPT_BASED` 之一并提供对应证据；未声明的远端/非确定 projector
   不得绑定可重试 Failover。同一 execution result 生成不同 projection digest 时立即
   fail closed。
6. **Approval。** `AuthorizePort` 是唯一授权/确认能力；`resume_capability=None` 的
   auto-only 实现不产生 Pending、不安装 codec；有 capability 时 Pending 通过现有
   Graph interrupt/resume，恢复仍调用同一个 Port，不能重复 Resolve、创建后台 task 或
   绕过 exact `AuthorizationInput` admission。
7. **终止和模型内容。** Resolve/Authorize/Execute pre-call 的 terminal projection 必须
   由外部 adapter 以原 `tool_call_id` 构造，writer 确认后返回 `Graph.FailedResult`/stop。
   Kernel 不生成 ToolResult variant、错误码、文案、翻译或语言检测；最终交给模型的所有
   bytes/text 由 protocol/presentation owner 序列化并通过 English-only tests。

### 9.16.3 Phase 1 开工清单

以下项目是实现门槛，但不构成新的架构选择：

- 在 `act.identity`/`act.contract`/`act.admission`/`act.port` 落地上面列出的 concrete
  nominal classes、稳定 import 和 exact admission；`request_revision` 不再使用裸 `int`；
- 在 Context/protocol owner 落地进程内 writer 的 pairing index、原子 CAS、revision/token、
  phase 冲突和 typed result。具体锁、哈希和容器可由 owner 自行选择；
- 在 composition 接入 Failover 的 `SingleAttempt`/`ReconcileAttempt`，为 projector 和
  writer 分别声明 operation semantics，并验证 retry request 不被重新构造；
- 实现四个 async callable 和 `ActNode(Graph[ActGraphValue])`，只通过现有 Graph facade
  组合、运行、interrupt/resume 和 commit；不创建第二 runner、State、reducer 或 Store；
- 添加 deterministic tests：第一次 writer transient failure、第二次成功时 Execute 仍只
  调用一次；unknown 先 reconcile；不可重试错误不重试；并发 upsert 只有一个 CAS winner；
  已完成 projection 不被覆盖；不同 identity 不串线；
- 添加 projector determinism/idempotency、auto-only Pending fail-closed、重复/过期 resume、
  cancellation、nested failure 和 commit 后 orphan 的测试；
- 添加安全 telemetry 断言：只含 stage、safe identity digest、attempt、route、latency 和
  failure class，不含 payload、secret、模型文本或原始 traceback；
- `test_settle_failover.py` 取代历史 `test_settle_replay.py`，测试 Port-level retry，
  不测试也不创建 Settle-only Graph recovery。

### 9.16.4 验收结论

满足上述清单后，Act v1 的 Kernel 接入可标记为“实现完成（进程内、非 durable）”。以下
能力仍必须在后续独立 MR/owner 中验收，不能从本计划推导出来：

| 后续能力 | 当前结论 |
| --- | --- |
| Graph candidate 与 exchange/outbox 的 durable 原子提交 | 后续 persistence owner |
| crash-after-execute、ack-lost 的跨进程恢复 | 后续 recovery/reconcile owner |
| 跨进程幂等、exactly-once、持久 receipt retention | 后续 Context/provider owner |
| provider-specific ToolResult schema 和 English serializer | protocol/presentation owner |

因此当前没有需要用户再次拍板的阻断问题。实现过程中若有人提出新增 ApprovalPort、
Settle-only API、整图 retry、第二状态模型、默认中文文案或放宽 writer CAS，应视为违反已
冻结架构，而不是“实现细节”。

## 9.17 参考 Think 已认可边界后的复审（2026-09-05，历史结论）

### 9.17.1 结论：边界已拍板，实施稿需据此重写后再编码

本轮以已认可的
[`think-graph-implementation-plan.zh-CN.md`](./think-graph-implementation-plan.zh-CN.md)
为 owner 边界基线，并结合用户对 Act 的三项明确决定重新复审。第 9.16 节要求 Act
声明 writer 的 observation/CAS/token/reconcile 状态机，并把 Authorize 设计为可选
`Pending` 的 decision Port；这两部分已经被本轮决定取代，不再是有效实施口径。

当前实施稿的四节点方向仍然成立，但文档本身还没有同步新的边界，因此现状应判定为：
**总体方向通过，Request changes before coding。**这里没有需要再次选择的架构分叉；实施稿
按本节逐项修订并消除旧口径后，即可再次做一次只检查一致性的编码准入复审。

本轮已经拍板的三项决定是：

1. Act 只调用 Port、校验 Port 返回的固定 outer DTO 并推进流程。执行恢复、重复 activation
   和对账由 `Graph` + `GraphRunState` 统一拥有；Act 不分配或携带 writer 的
   `operation_token`，不维护 revision/CAS/reconcile 状态机。
2. Act 不在 Resolve、Authorize、Execute 或 Settle 前查询 writer，也不根据 writer observation
   决定是否再次调用业务 Port。节点是否执行由 Graph/State 决定，具体结果由对应 Port 返回。
3. 每个工具调用都必须经过 Authorize；首次进入 Authorize 时固定向 Runtime 发起授权请求并
   interrupt。恢复时业务输入只有 exact `Allow` 或 `Deny`。Kernel 不关心谁作出决定、为什么
   Deny，也不把这些事实放进 DTO。

因此，当前实施稿不能一边声明“节点只推进流程”，一边又让 Act 根据独立 writer 的
`Absent/Incomplete/Committed/Unknown` 状态决定是否调用 Resolve/Authorize/Execute；也不能一边
声明“所有工具都 interrupt”，一边保留 auto-only、初次直接 `Allowed` 或可选
`resume_capability` 路径。

### 9.17.2 与 Think 一致的 owner 边界

Act 应复用 Think 已认可的分层原则：Kernel-owned flow 固定外层结构和调用顺序，具体能力
及其业务含义留给 Port owner；外部能力被注入，不因其内部持有 runtime resource 就自动成为
第二个 Kernel State。

| owner | 本期负责 | 明确不负责 |
| --- | --- | --- |
| `mote_kernel.act` | 四节点 topology、固定 outer DTO/closed control variant、具名 Port 调用、Port 返回 exact admission、interrupt/resume 接线和 Graph outcome | 工具目录、授权策略、用户交互、ToolResult schema/文案、writer 对账、Failover 内部、持久化 |
| `mote_kernel.execution.Graph` + `GraphRunState` | activation、settlement、interrupt identity、resume、执行位置、重复 activation/recovery admission 和统一 commit 边界 | 解释 Allow/Deny 原因、展示授权请求、解析 ToolResult |
| Runtime/Authorization owner | 通过 `AuthorizePort` 向用户发起请求、保存或解析 opaque request handle、认证响应并构造 `Allow`/`Deny` | 把 actor、reason、UI 文案或策略事实塞给 Kernel |
| Tool/Protocol Port owner | Resolve/Execute/Settlement/write 的具体实现及其 request/result 业务语义 | 修改 Graph State、让 Act 猜业务结果或建立私有 Graph runner |
| Provider/protocol/presentation owner | ToolCall/ToolResult schema、配对、最终序列化和 English-only 保证 | 要求 Kernel 定义默认 ToolResult、错误码、文案、翻译或语言检测 |
| Failover owner | 在 composition 中装饰每个具体 Port，并在其自己的 Graph/State 边界处理 attempt、retry 和恢复 | 把 cursor/token/reconcile 状态暴露给 Act，包住整张 Act Graph，要求 Act 重放节点 |

这里需要纠正此前评审的两个过度要求：

- 外部 Port 可以持有完成自身调用所需的 runtime client、远端 request handle 或外部资源状态；
  只要这些内容不进入 Act Graph value、ActNode 字段或 `GraphRunState` 的平行副本，就不能仅凭
  “有状态”判定它违反唯一 Kernel State。Act 只看 Port 的 typed request/result。
- Kernel 只需冻结 Port 所需的 outer nominal DTO、字段位置、不可变性和通用引用约束；
  `ToolSelector`、opaque handle、evidence、ToolResult payload 等值的业务内部 schema 由其
  producer/Port owner 定义。评审不再要求 Kernel 固定它们的 provider 编码或业务字段。

反过来，外部 writer 的记录不能成为 Act 的执行调度真相。是否重新激活节点、是否已经完成
一个 Graph step、恢复到哪里以及同一 activation 是否可继续，都只能由 Graph/State 决定。
Port 自身的远端实现细节与“Act 再读取一套 observation 状态来驱动流程”不是一回事；前者
允许，后者禁止。

### 9.17.3 Authorize 的唯一运行流程

实施稿必须把 Authorize 改成以下唯一流程，不再描述多个配置模式：

```text
Resolve 返回 ResolvedInvocation
  -> Authorize 首次 activation
       -> 调用 AuthorizePort.request_authorization(...) 一次
          Runtime/Port owner 已在该调用中向用户发起请求
       <- 返回 opaque AuthorizationRequestRef
       -> 同一 Port 的 codec/capability 把 ref 编码成 bytes
       -> Graph.interrupt(bytes)
  -> Graph 返回 AwaitingResumeResult

用户在 Runtime 中选择 Allow 或 Deny
  -> Runtime 调用 ActNode.resume_authorization(..., decision=Allow | Deny)
  -> 同一 Authorization owner 校验 opaque request ref/interrupt binding
     并构造 Graph 所需的 exact AuthorizationInput
  -> Graph 恢复 Authorize
       |-- Allow -> 发布 AuthorizedInvocation -> Execute
       `-- Deny  -> Graph failure/stop；Execute 不调用
```

这里的 `bytes` 只是当前 `Graph.interrupt(request_payload: bytes)` 的通用运输格式。它最多携带
Authorization owner 生成的 opaque request ID/受保护引用，用来让恢复响应对应到正确的
interrupt；Graph 不解析、不展示，也不根据它判断策略。用户看到的确认界面已经由 Runtime/
`AuthorizePort` 发起，不存在“Graph 拿 bytes 去展示”的职责。

Authorize 的 contract 应遵守以下硬约束：

1. `AuthorizePort` 是 required capability；每个合法 ToolCall 都调用它并 interrupt，没有
   auto-only/no-approval 组装模式。
2. 首次 activation 不允许直接返回 `Allowed`/`Denied`，也没有 `Pending` decision。
   “等待中”由 Graph 的 interrupt state 表达，不在业务 decision union 中重复表达。
3. resume decision 的 closed variants 只有无业务字段的 exact `Allow` 和 `Deny`。不得携带
   actor、reason、policy facts、审批类型、UI 文案或模型文案。
4. 删除 `AuthorizationFacts`；`AuthorizedInvocation` 只表示同一个
   `ResolvedInvocation` 已收到结构性的 `Allow`，不能成为权限事实集合。
5. 恢复 Authorize 时不再次调用 `request_authorization()`，否则会重复向用户发起请求。
   request handle 的校验和恢复输入构造由同一 Authorization owner 的 codec/capability 完成。
6. codec/correlation capability 是同一个 required `AuthorizePort` 的组成部分，不是第二个
   Approval Port，也不是 optional capability。它必须在 ActNode assembly 时冻结并适配现有
   `Graph.set_resume_codec()`/`Graph.resume_interrupted()`。
7. `ActNode.resume_authorization()` 对调用方只暴露 sealed awaiting result、interrupt ID 和
   `Allow | Deny`；它不得要求调用方读取 private continuation/frame，或传回 reason/actor。

`Deny` 之后需要闭合的 provider ToolResult 属于 Runtime/protocol owner。该 owner 根据自己的
协议契约产生与原 ToolCall 配对的结果，并保证任何实际进入模型的内容为英文；Kernel 只收到
`Deny` 并停止，不定义“user declined”、系统拒绝、策略拒绝等文案或分类。Graph failure 所需
的非模型展示 safe reason 应由 Flow/composition owner 以 opaque 配置提供，不能从 Deny reason
推导，因为 Kernel 根本不接收 reason。

### 9.17.4 删除 Act-owned writer 对账状态机

实施稿第 3.3、5、6、7、9、10 和 12 节中，下列类型与行为不应继续作为
`mote_kernel.act` 的 contract 或测试目标：

- `ToolExchangeWriter.observe()`；
- `AbsentObservation`、`IncompleteObservation`、`CommittedObservation`、
  `ConflictObservation`、`UnknownObservation`；
- Act-facing `expected_revision`、`ProjectionRevision`、`OpaqueOperationToken`、
  `OpaqueContextReconcileRef`；
- Act 根据 observation 短路 Resolve/Authorize/Execute，或根据 incomplete/unknown 决定
  CAS、replace、reconcile；
- 在 Act 测试中复刻 writer 的并发 CAS winner、retention、acknowledgement-lost 或 orphan
  状态机；
- 要求 Act 为 Failover 构造 operation identity、plan revision、deadline、attempt cap 或
  telemetry route。

如果 ToolResult 交付仍使用具体 `ToolExchangeWriter` Port，Act-facing 形状应收窄为一次普通
typed Port 调用，例如：

```text
ToolExchangeWriteRequest
  projection: ToolExchangeProjection

ToolExchangeWriteResult
  receipt: OpaqueToolExchangeReceipt

ToolExchangeWriter
  async write(request: ToolExchangeWriteRequest) -> ToolExchangeWriteResult
```

上述名称可在实施稿中一次性冻结，但语义必须只有“交付一个已构造的 projection，并返回该
Port 的 typed result”。pairing identity 已经是 projection 的结构字段，不再额外传一份可
错配参数。Port 抛出的普通异常和 `CancelledError` 按 Graph 既有边界传播；需要重试时，
composition 注入 Failover 装饰后的同一个 Port。Act 不接收 unknown/reconcile cursor，也不
实现 retry loop。

因此，Settle 的合理边界是：接收单一 `ToolExecutionResult`，调用外部
`SettlementPort` 得到 opaque projection，调用已注入的 write Port 得到 exact typed result，
再发布 `SettledActResult`。Act 可以验证 outer class、source/pairing identity 和引用相等，
但不解析具体 ToolResult 或模型文本。Failover 如何让这次具体 Port 调用安全结束，属于其
owner；本评审只要求装饰位置是“raw concrete Port -> Failover decorator -> ActNode”。

Act 也不得为了新 Graph run 或重复 ToolCall 做 writer preflight。新的请求是否允许开始、
已提交 activation 是否恢复以及是否再次调度 Execute，统一由 ingress + Graph/State owner
处理。Act 专项测试只验证节点按 Graph 提供的 activation 调用一次 Port、错误返回不进入下游，
不验证外部 Failover/writer 内部算法。

### 9.17.5 DTO/Port 的实施级冻结方式

这里沿用 Think 计划的 concrete-schema 口径，而不是要求 Kernel 定义业务内容。修订后的
实施稿应提供一张可直接映射为 Python class 的表，至少覆盖：

| outer class | 必须冻结的结构职责 | 不进入 Kernel 的内容 |
| --- | --- | --- |
| `ActRequest` | ToolCall pairing identity、selector、arguments/caller 的 opaque typed value | provider ToolCall schema、参数业务解释 |
| `ResolvedInvocation` | 原 request 与稳定 binding/definition reference | catalog 查询和 handle 解析算法 |
| `AuthorizationRequestRef` | 可被 Authorization owner 编码进 interrupt 的 opaque request reference | UI 内容、actor、reason、策略事实 |
| `AuthorizationInput` | 初次 resolved input，或由 resume capability 构造的 resumed input | 审批业务 envelope 的内部 schema |
| `Allow` / `Deny` | 两个无业务字段的 exact nominal decision variant | 谁决定、为何决定 |
| `AuthorizedInvocation` | 被 Allow 的同一 `ResolvedInvocation` | `AuthorizationFacts` |
| `ToolExecutionResult` | ExecutePort 返回的 exact outer result 及其 source identity | provider 结果/错误的具体业务 schema |
| `SettlementProjection` | 与 execution identity 配对的 opaque projection | ToolResult variant、英文文案、serializer |
| `ToolExchangeWriteRequest/Result` | 单次 typed Port 请求/返回 | CAS、token、reconcile、retention |
| `SettledActResult` | Settle 正常 Graph output | 模型展示和 announcement |

每个 outer class 必须冻结：字段名称和顺序、`frozen/slots`、所继承的 Graph value carrier、
正常 constructor/factory 的通用不变量，以及由哪个 Port producer 构造。opaque wrapper 可以
只冻结为一个受限 bytes/string/reference value；其内部编码、业务字段和解释继续由外部
owner 负责。

Port 应各自声明唯一 request/result DTO，节点在调用后执行 exact outer-class admission；
DTO 自己的字段不变量由 DTO constructor 或 producer-owned typed admission 保证。不要继续
让 `ActPayloadAdmission` 变成一个知道所有业务 payload/evidence/provider variant 的中央
validator；若保留该名称，只能校验 Act-owned outer structure 和跨阶段 identity，不得解析、
修复或生成 Port 业务内容。也不得用 `Any`、裸字典、反射或字符串 discriminator 取代固定
outer DTO。

所有实际交给模型的内容仍必须是英文，但 English-only 检查只发生在最终 protocol/
presentation serializer。Kernel 测试只证明它不生成、不解析、不翻译这些内容；外部 owner
的 contract suite 才断言正常、Deny、工具失败和 fallback 的最终模型输入为英文。

### 9.17.6 实施稿修订清单与编码授权矩阵

实施稿下一版应按以下顺序修改：

1. 在摘要、owner matrix、四节点语义、typed contract、Port、状态、Phase 和测试章节统一
   写入第 9.17.2 节 owner 边界，删除互相冲突的旧口径。
2. 用第 9.17.3 节的强制 interrupt 流程完整替换 `Allowed/Denied/Pending + optional
   resume_capability + auto-only` 方案。
3. 删除 `AuthorizationFacts`、decision 中的 terminal projection/actor/reason，以及恢复时
   再次请求授权的路径；明确 Deny 的 provider result 由 Runtime/protocol owner 闭合。
4. 删除第 9.17.4 节列出的 observe/CAS/token/reconcile 类型、算法、Phase 任务和测试；将
   writer 收窄成普通 typed result-delivery Port。
5. 按第 9.17.5 节给出 concrete outer DTO 表和每个具名 async Port 的单一签名；业务 payload
   保持 opaque，不为 Kernel 增加 ToolResult schema 或业务码。
6. 保留 `Resolve -> Authorize -> Execute -> Settle`、单一带 identity 的
   `ToolExecutionResult`、nested `Graph`、无第二 runner/State/Store、English-only 外部边界。
7. Failover 章节只保留每个具体 Port 在 composition 中被装饰后注入的位置和 contract 保持
   要求；删除 Act 对 Failover policy/token/reconcile/telemetry 内部行为的规定与测试。

修订后的授权矩阵应为：

| 状态 | 内容 |
| --- | --- |
| **实施稿修订后可编码** | `act` 的固定 outer DTO、具名 Port Protocol、四个 callable、`ActNode` nested Graph、强制 Authorize interrupt/resume 接线及 `tests/act/**` |
| **外部 owner 就绪后集成** | Runtime `AuthorizePort`/codec、具体 Resolve/Execute/Settlement/write adapter、provider ToolResult/English serializer、各 concrete Port 的 Failover 装饰 |
| **不在 Act 任务** | execution/state 对账机制、Failover 内部、writer store/CAS、Authorization UI/策略、provider ToolResult schema、持久化/跨进程恢复 |
| **禁止** | Act `observe`/token/reconcile、auto-only 绕过 interrupt、`Pending` 业务态、第二 Approval Port、第二 runner/state/store、Kernel 默认文案或业务 error mapping |

外部依赖尚未实现时，可以先完成与其解耦的 DTO/Protocol/Graph assembly 和严格 test double；
到对应集成阶段必须报告 owner blocker，不能在 `act` 中创建临时 writer state、authorization
stub、Failover shim 或 execution 兼容入口。最终 v1 仍需完成范围内全部实现和测试，不能把
阶段性 scaffolding 宣称为完成。

### 9.17.7 可直接用于实施稿 MR 的评审意见

> **Request changes before coding：**四节点 nested Graph、Port 返回 typed DTO、单一
> `ToolExecutionResult`、Kernel 不解释业务语义以及 model-facing English-only 外部边界可以
> 保留；但当前实施稿的 Authorize 与 writer 状态机不再符合已拍板边界。请将 Authorize 改为
> 每个 ToolCall 首次必调 `AuthorizePort` 并固定 `Graph.interrupt`：Runtime/Port 已经向用户
> 发起请求，interrupt bytes 只保存 opaque request handle；恢复只接受无 actor/reason 的 exact
> `Allow | Deny`，Allow 才进入 Execute，Deny 直接 stop。删除 `Pending`、auto-only、可选
> resume capability、`AuthorizationFacts` 和恢复时再次发起授权。Deny 的配对 ToolResult 及
> 全部模型文案由 Runtime/protocol owner 负责，Kernel 不定义或解析。Act 节点只推进流程，
> 不调用 writer `observe`，不持有 `expected_revision/operation_token`，不根据
> Absent/Incomplete/Committed/Unknown 做 CAS 或 reconcile；这些执行与对账事实统一由
> Graph/State owner 管理。若保留 ToolExchange write Port，将其收窄为一次 exact
> request/result 调用；Failover 只在 composition 装饰每个具体 Port，内部不属于 Act 计划或
> 测试。请按 Think 计划同样的 concrete-schema 方式冻结 outer DTO 字段/constructor/owner，
> opaque 业务内容仍留给各 Port owner。完成全文、Phase 和测试矩阵的一致性修订后再进入编码。

## 9.18 当前实施稿编码准入复审（2026-09-05，已由 9.19 收尾）

> 本节只保留问题清单；四项工程契约的最终选择和关闭条件以第 9.19 节为准。

### 9.18.1 结论

结论是：**已经达到“可以开工”的条件，但应先从 Phase 0 开始；Phase 1 的公共 API
合入前还要把下面列出的机械性签名/类型契约写死。** 这些事项不需要新的架构拍板，
也不应借机把业务语义、writer 对账或 Failover 内部重新塞回 Kernel。

换成更直接的话：现在可以创建 `act/contract.py`、`identity.py`、`admission.py`、
`port.py` 和严格的 test double；不能把当前文档中的伪代码直接当成已经冻结的 Python
公共签名并跳过 Phase 0。完成 9.18.3 的四项收口后，四节点/四 Hook 的 Phase 1 可以继续。

### 9.18.2 已经闭合、无需重开的边界

| 检查项 | 当前结论 | 依据 |
| --- | --- | --- |
| 拓扑 | 通过 | `Resolve → ResolveHook → Authorize → AuthorizeHook → Execute → ExecuteHook → Settle → SettleHook`，Hook 是真实 nested `HookNode`，不是第二 runner |
| 授权 | 通过 | 每个可授权 ToolCall 首次必调同一个 `AuthorizePort` 并 `Graph.interrupt(bytes)`；resume 只有无字段 `Allow`/`Deny`，没有 `Pending`、auto-only 或第二 Approval Port |
| 结果归属 | 通过 | `ExecutePort` 是 `ToolExecutionResult` 的唯一生产入口；Settle 只消费 `ExecuteHook.result.value`，不重新查询或执行工具 |
| 状态/恢复 | 通过 | activation、位置、interrupt/resume、重复 activation、recovery、settlement 和 commit 由 `Graph` + `GraphRunState` 独占；Act 不维护平行 state |
| writer/对账 | 通过 | Act 不调用 `observe`，不持有 revision/CAS/token/reconcile，也不读 writer 状态决定调度；writer 只是一次 typed delivery Port |
| 业务语义和语言 | 通过 | Port/协议 owner 构造具体业务值；Kernel 只做 outer exact admission，不生成/解析 ToolResult 或文案；最终 model-facing serialization 由外部 owner 保证英文 |
| Failover | 通过（仅接入位置） | composition 对启用故障恢复的具体 Port 先加装饰器，并以原 Port contract 注入；禁用必须是显式 binding，Act 不实现 retry loop。Failover 的 policy/attempt/reconcile 仍不在本评审范围 |

### 9.18.3 Phase 1 前必须补齐的四项工程契约

#### A. `ActNode` 构造签名与 `ActSlotId` 的含义

实施稿第 6.3 节目前只列出 `ActNode(slot, ...)`（约第 1126–1143 行），但没有说明
这个单一 `slot.node_id` 是 Act 图自身的节点、父图挂载点，还是四个业务节点之一。它
也没有给出可直接用于 strict typing 的完整 `__init__` 签名。Think 计划已经有可对照的
`ThinkNode(definition_id, version, ...required capabilities...)` 形状。

在 Phase 0 交付前必须二选一并写入实施稿：

1. 与 Think 对齐，`ActNode` 接收稳定的 `definition_id: str` 和 `version: int`，四个
   Hook 的 slot 逐项校验这两个值；或
2. 保留 `ActSlotId`，明确 `node_id` 的唯一语义、如何得到 `Graph` definition id/version、
   以及同一父图中多个 Act 实例如何避免 identity 冲突。

推荐第一种，因为它与现有 `Graph` 构造器和已认可的 Think 边界一致。无论采用哪一种，
都要把 required Port、四个 Hook、`failure_reason`、`ActPayloadAdmission`、`__slots__`
和 codec 接线写成完整参数表；编码者不应自行猜测 `slot` 的含义。

#### B. 四个 Hook 的静态泛型绑定与命令类型

当前稿正确地把 Hook 内层 admission 留给 Hooks owner；现有
[`HookNode`](../src/mote_kernel/hooks/node.py:100) 的构造签名包含四组
`StageValue/HookState/Command`，但这些类型如何在 `ActNode.__init__` 中静态绑定仍未写明。至少要
明确：四个 stage value 分别是 `AuthorizationInput`、`AuthorizedInvocation`、
`ToolExecutionResult`、`SettledActResult`；四个 Hook 是否共享同一个 concrete
`HookStateProjection`；command 是一个共享 concrete class 还是每个 slot 一个 concrete
class。第 4.4 节伪代码中的 `ActHookCommand`（约第 487 行）目前没有在 schema 中定义，
不能让它成为隐含类型。

这不是要求 Act 读取 Hook 私有 admission。正确边界仍是：Act 只检查 outer
`HookRequest/HookResult` 和跨阶段 identity，Hooks owner 的 `HookPayloadAdmission` 负责
inner exact type；但构造器的静态签名和终端 `HookResult` 的 command 类型必须能让 pyright
检查通过。

#### C. 收窄“不可变性检查”的责任

第 5.5 节把 `ActPayloadAdmission` 描述为检查所有外部 payload 的“不可变性”。在禁止
反射、且 `frozen=True` 只能浅冻结的前提下，Act 无法递归证明任意 composition 提供的
`HookStateProjection`、opaque owner value 或 command 是不可变的。这里应沿 Think 计划
第 5.1 节的明确口径修订：

- concrete DTO 的 constructor/producer-owned admission 负责字段、递归 data-only 和
  不可变性保证；
- Act admission 只检查 exact nominal class、outer wrapper、长度、引用/identity 和
  digest 关系；
- 不为完成“深冻结”而引入反射、通用 validator registry 或 `Any`。

否则实现者会在这一点上被迫违反仓库的禁止反射规则，或形成一个实际上无法兑现的安全
承诺。

#### D. 把 Port 文本形状落成可编译的 Protocol

第 6.1 节已经把方法名、参数和同步/异步方向写对了，但仍是 text signature。Phase 0
应补一份与 Think 第 5.1 节同等粒度的 Python `Protocol` 定义（至少明确
`ResolvePort`、`AuthorizePort`、`ExecutePort`、`SettlementPort`、`ToolExchangeWriter`）：

- `resolve/request_authorization/execute/project/write` 是单参数 `async` 方法；
- `encode_interrupt`、`build_resume_input`、`encode_graph_input`、`decode_graph_input`
  是本地、确定性的同步 codec/correlation 方法；
- `AuthorizePort` 的 codec id/version 与 `Graph.set_resume_codec()` 的
  `str`/`int` 绑定方式固定，不能再拆出第二个 Approval Port；
- 若使用 `runtime_checkable`，文档必须说明它只能检查成员存在，错误 arity/非 awaitable
  由静态检查或首次明确的 contract error 暴露，不能用反射猜测。

同时要明确 interrupt payload 的版本关联：现有
[`Graph.set_resume_codec()`](../src/mote_kernel/execution/facade.py:445) 记录的是 resume-input
codec 的 id/version，而 interrupt view 暴露的是原始 bytes。应规定 `encode_interrupt`
输出自带 owner 可验证的版本/correlation，或明确它与同一 codec id/version 绑定；否则
同一 Graph definition 下更换 interrupt 编码格式时，恢复兼容性没有可审计的门禁。

### 9.18.4 不阻塞 Act 核心开工、但必须由外部 owner 提供的集成前置

以下项目不应再变成 Act 内部的临时实现，也不是新的用户决策：

- Runtime/Authorization owner 提供真实的 request/ref correlation、codec 和
  `Allow`/`Deny` resume input；
- Resolve/Execute/Settlement/write 的具体 Port、ToolResult/projection serializer 和
  model-facing English-only 测试；
- `ActInvocationKey`/`tool_call_id` 的唯一性域、跨 scope 碰撞规则和协议配对策略；这些
  由 Context/protocol owner 声明，Act 只做结构性 identity admission；
- 每个启用故障恢复的具体 Port 都必须在 composition 中套上 Failover decorator；禁用必须由该 owner 的显式 binding 表示。装饰器内部的 retry、
  attempt、预算和不确定结果处理不纳入 Act 评审；
- 若 writer 将来写入受治理的 durable 事实，必须改接统一 `Graph.Commit` projection/
  transaction；当前 v1 的 `commit=None` 和进程内 projection 不能宣称 durable。

Act 可以先用严格 test double 完成 Phase 0/1 的契约、assembly、首次 interrupt 和 nested
Graph 测试；外部 owner 未就绪时只能报告 integration blocker，不能在 `act` 包内创建
临时 writer state、authorization stub、Failover shim 或第二执行入口。

### 9.18.5 编码授权矩阵

| 现在可以编码 | 需外部 owner 就绪后集成 | 明确禁止 |
| --- | --- | --- |
| `contract/identity/admission/port` 的 outer DTO、closed variant、exact admission；`ActNode` 八节点 assembly；公开 interrupt view 和 resume helper；Graph/nested/取消/失败测试 | Runtime authorization/ref codec；具体 Resolve/Execute/Settlement/write Port；provider ToolResult 和 English serializer；各具体 Port 的 Failover 装饰；统一 durable persistence | `Pending`/auto bypass；第二 Approval Port；writer `observe`/CAS/token/reconcile；Act retry loop；第二 runner/state/store；Kernel 默认文案或业务 error mapping |

### 9.18.6 可直接用于 MR 的结论

> **Approve to start Phase 0; conditional approval for Phase 1.** 当前实施稿已经符合已拍板
> 的四节点/四 Hook、强制 Authorize interrupt/resume、单一 `ToolExecutionResult`、
> Graph/GraphRunState 唯一状态、Port-level Failover 接入和 Kernel 不定义业务实现语义的
> 边界。请在 Phase 0 同步冻结 `ActNode` 的完整构造签名与 `ActSlotId` 语义、四个 Hook
> 的静态泛型/command 绑定、不可变性检查责任和可编译的 Port Protocol；修正
> `ActHookCommand` 未定义的伪代码名，并为 interrupt bytes 绑定可审计的 codec 版本。
> 这些是公共契约收口，不是新的架构选择。完成后即可进入 Phase 1；Failover、Runtime、
> provider serializer 和 durable persistence 按各自 owner 的集成门禁推进，不能回流到
> `mote_kernel.act`。

## 9.19 P1 收尾（2026-09-05，历史结论；拓扑已由第 9.21 节订正）

### 9.19.0 本次三项意见的最终结论

1. **Graph carrier**：Act 固定为 `Graph[HookGraphValue]`；Act-owned frame DTO 直接继承
   `HookGraphValue`，删除/禁止独立的 `ActGraphValue`。
2. **公开 resume**：只公开 Act-owned immutable `AuthorizationInterruptView`；helper 从
   awaiting result 的公开字段投影，codec 固定为 `codec_id: str`、`codec_version: int`。
   Port 只关联 opaque handle；重复 resume、旧 state、scope/interrupt 和 codec mismatch 由
   Graph 权威拒绝，Kernel 不在 Port 调用前另行认证。
3. **Concrete schema**：第 5.3 节的 outer class、字段顺序/类型、上限、stop reason、
   `ResolvedInvocation.arguments.digest == ToolExecutionIdentity.arguments_digest` 和
   `ActPayloadAdmission` 方法是 v1 强制契约；opaque 业务内容仍由 producer/Port owner
   解释，不能留给编码者现场选择。

### 9.19.1 结论

P1 不再保留架构选择，全部收敛为实现前必须写死的契约和验收证据。现在可以按实施稿
开始 Phase 0；Phase 1 合入前完成下表即可，不得因此增加节点、Port、状态模型或恢复入口。

| 项目 | 最终固定口径 | 关闭证据 |
| --- | --- | --- |
| Act 身份与构造 | `ActNode` 接收 `definition_id: str` 和 `version: int`；不再用含义不明的 `slot` 参数。`ActSlotId` 仅表示 Act 内四个业务 slot（`resolve`、`authorize`、`execute`、`settle`），由构造器从同一 definition/version 派生；它不是父图挂载点。父图挂载点由 `Graph.add_node()` 的 node id 决定。 | 构造签名、四个 canonical slot、Hook slot 逐项匹配和多实例 nested Graph 测试 |
| 四个 Hook 的静态绑定 | 四个 stage value 固定为 `AuthorizationInput`、`AuthorizedInvocation`、`ToolExecutionResult`、`SettledActResult`；四个 Hook 共享一个 composition 提供的 concrete `HookState` 类型；command **按 slot 分别使用 concrete 类型**，不再使用未定义的 `ActHookCommand`。内层 command/state 的 concrete admission 仍由 Hooks owner 负责，不由 Act 另造 carrier。 | 静态 `HookNode[...]` 绑定、错误 slot/command、HookResult value 不变和四个真实 child 测试 |
| 不可变性责任 | DTO constructor/producer 负责递归 data-only 与不可变性；Hooks owner 负责 Hook 内层 payload；Act admission 只做 exact nominal class、outer wrapper、长度、引用/identity 和 digest 检查。禁止反射、通用 validator registry 和 `Any`。 | 造假/可变成员/超限/identity mismatch 的负例在下游调用前失败 |
| Port 与 codec | `ResolvePort.resolve`、`AuthorizePort.request_authorization`、`ExecutePort.execute`、`SettlementPort.project`、`ToolExchangeWriter.write` 是单参数 async 方法；Authorize 的 `encode_interrupt`、`build_resume_input`、`encode_graph_input`、`decode_graph_input` 是同步确定性方法。`codec_id: str`、`codec_version: int` 是同一 `AuthorizePort` 的字段，并原样用于唯一一次 `Graph.set_resume_codec()`；interrupt bytes 使用同一版本化 owner codec，Kernel 只做 bytes/长度 admission。 | `Protocol` 可被 pyright 编译；缺失/错误方法、重复安装 codec、错误版本和非法 resume input 的测试 |

`ActNode` 的冻结构造形状为：

```python
ActNode(
    definition_id: str,
    *,
    version: int = 1,
    resolve_port: ResolvePort,
    authorize_port: AuthorizePort,
    execute_port: ExecutePort,
    settlement_port: SettlementPort,
    exchange_writer: ToolExchangeWriter,
    resolve_hook: ResolveHookNode,
    authorize_hook: AuthorizeHookNode,
    execute_hook: ExecuteHookNode,
    settle_hook: SettleHookNode,
    failure_reason: OpaqueGraphFailureReason,
    admission: ActPayloadAdmission,
)
```

`ActNode.__slots__` 固定只保存上述装配引用：

```text
_resolve_port, _authorize_port, _execute_port, _settlement_port,
_exchange_writer, _resolve_hook, _authorize_hook, _execute_hook,
_settle_hook, _failure_reason, _admission
```

不创建 `__dict__`、run-local cache、task、审批状态或结果缓存；这些引用初始化后不可重绑。

`ResolveHookNode`、`AuthorizeHookNode`、`ExecuteHookNode`、`SettleHookNode` 是静态泛型绑定名，
不是新的运行时 node 类型；运行时仍只接受四个真实 `HookNode`。每个 command 类型由对应
Hooks/composition owner 提供并在 `HookPayloadAdmission` 中固定，Act 不读取或解释其内容。

### 9.19.2 历史 P1 项的统一收口

- **P1-1 固定拓扑/可变窗口**：接受 Graph 现有的 compile 前 builder 窗口；仅 `ActNode` 构造器
  在该窗口内完成八节点组装，首次成功 compile 后沿用 Graph guard，不另造 seal。
- **P1-2 Settle command/evidence**：Settle 只经 `SettlementPort.project()` 和普通
  `ToolExchangeWriter.write()` 交付；`SettledActResult` 在 writer 返回 exact result 后形成，
  不维护 evidence、outbox 或 writer 状态。
- **P1-3 动态 binding/schema**：ResolvePort 在一次 activation 产生一个
  `ResolvedInvocation`；definition/version、binding、canonical arguments digest 和 pairing
  随 identity 传递。runtime handle 不进入 Graph value/state，恢复不重新解析。
- **P1-4 取消/结果代数**：`CancelledError` 按 Graph cancellation 边界传播，不增加
  `Cancelled` 业务 variant；只有 Execute 产生的 `ToolExecutionResult` 才进入 Settle。
- **P1-5 条件能力**：`AuthorizePort`、其 request/correlation/codec 接线及四个 Hook 都是
  required；不提供第二 ApprovalPort、Pending 业务态或绕过 interrupt 的 auto path。

### 9.19.3 关闭条件与授权

实现稿第 5.3–6.3 节按上述口径落成真实 class/Protocol，并通过 `tests/act/` 的 contract、
assembly、Hook 泛型、resume、取消/失败和 Settle 边界测试后，P1 即关闭。外部 Runtime、
provider/protocol serializer、Context writer 和 Failover 的集成测试仍由各自 owner 负责，
不作为 Act P1 的隐藏前置。

**结论：没有需要用户再拍板的 P1。** 未完成的项目只能标记为实现阻断；不得用新增
ApprovalPort、Settle-only API、整图重试、第二 state/runner 或默认模型文案来绕过。

## 9.20 最终编码准入复审（2026-09-05，历史结论；拓扑已由第 9.21 节订正）

### 9.20.1 本轮核对结果

对照实施稿第 0–13 节逐项复核后，当前口径已经一致：

- 拓扑固定为四个业务节点汇入一个共享 `HookNode`，再经 `RouteAfterHook` 的 conditional
  edge 路由；直接节点为 `resolve`、`authorize`、`execute`、`settle`、`hook`、`route`，
  使用单一 `Graph[HookGraphValue]` 和单一终端 `HookResult`；
- 每个合法 ToolCall 的首次 Authorize 必须调用同一个 `AuthorizePort` 并
  `Graph.interrupt(bytes)`；Runtime/Authorization owner 负责先发起请求，bytes 只是
  transport；恢复只有无字段 `Allow`/`Deny`，没有 `Pending`、auto-only 或第二 Approval
  Port；
- Execute 是 `ToolExecutionResult` 的唯一生产者，Settle 只消费它；正常 Settle 只调用
  `SettlementPort` 和普通 `ToolExchangeWriter`，Deny 的 ToolResult 由 Runtime/protocol
  owner 先配对并写入，Act 只返回 Graph stop；
- `ActNode` 的 `definition_id/version`、四个 `ActSlotId`、四个 slot-specific Hook
  command、outer DTO、codec id/version、Port Protocol 和 exact admission 均有固定落点；
- `Graph` + `GraphRunState` 是 Act activation、执行位置、恢复、提交及 Act 层对账的唯一
  权威。Port-level Failover 可以在自己包裹的具体 Port 内使用其 graph/state，但其策略、
  attempt 和 reconcile 实现不属于本评审；Act 不读取 writer 状态、不重跑节点、不实现
  retry loop；
- Kernel 不解析 ToolResult、provider payload 或模型文案；所有实际给模型的内容由外部
  protocol/presentation owner 以英文序列化。

全篇搜索未发现当前实施章节仍保留 `Pending`/auto bypass、writer `observe`/CAS/token、
第二 runner/state/store 或未定义 `ActHookCommand` 的有效路径；历史章节中的旧口径均已
明确标注为历史记录。

### 9.20.2 本轮文档收口

本轮对实施稿做了四项机械性修订，不改变已拍板的架构：

1. 将递归 data-only/不可变性证明明确归给 DTO、Port 和 Hook owner；Act admission 只做
   exact outer class、已声明的 immutable 外壳、长度及 identity 检查；
2. 在示例动作中补上 Resolve 返回值与原 request、request-ref 与 pairing、Settle result
   与原 pairing 的显式结构比较；
3. 明确通用 `HookNode` 不携带跨 activation 的 provenance，因此 ResolveHook 的 initial
   phase、AuthorizeHook 的 resolved identity、ExecuteHook 的 execution identity 必须由
   各 slot 的 concrete `HookPayloadAdmission`/Hooks owner contract 保证并测试；这不是让
   Act 偷藏第二份 state；
4. 明确 nested Act 生成的 resume action 必须提交给持有 awaiting result 的 root Graph，
   并把 Graph/GraphRunState 的 Act 层对账与 Failover 自身的 Port-level graph/state 分开。

### 9.20.3 编码授权结论

**通过，可以开始编码。** 建议按以下顺序推进：

1. 立即开始 Phase 0：实现 `contract/identity/admission/port` 的真实 immutable nominal
   class、单一 `AuthorizePort` Protocol 和 codec 接线；
2. Phase 1 合入前必须有 deterministic tests 证明六节点 assembly、共享 Hook slot/具体
   command 绑定、首次 interrupt、root/nested resume、Allow/Deny 分支、identity mismatch、
   cancellation 和 Graph mutation guard；Hooks owner 还必须提供上述跨 Hook identity/phase
   preservation 的 concrete admission 证据；
3. Runtime authorization、具体 Resolve/Execute/Settlement/Writer、provider 的英文
   serializer、各具体 Port 的 Failover decorator 和 durable persistence 是外部 owner 的
   集成前置，不是 Act 契约继续扩张的理由。缺失时报告 integration blocker，不在 Act 中
   添加替代状态、恢复入口或业务语义。

本结论只表示实施稿已达到“可按契约开工”，不表示 Act 生产代码已经实现，也不表示 v1
承诺持久化、跨进程恢复或 exactly-once。

## 9.21 共享 Hook 拓扑订正（2026-09-05，当前生效）

### 9.21.1 订正内容

实施稿原先把四个业务节点分别接到四个 `HookNode`。这不是当前 Graph 的推荐组图方式，
现已统一订正为：四个业务节点成功后都输出同名、同 exact class 的 `hook_request`，汇入
一个共享 `HookNode`；Hook 完成后进入普通 `RouteAfterHook`，由 conditional edge 路由到
下一个业务节点或 `END`。

```text
Resolve ───────────────┐
Authorize ─────────────┤
Execute ───────────────┤──> shared Hook ──> RouteAfterHook
Settle ────────────────┘                         ├─ Authorize
                                                 ├─ Execute
                                                 ├─ Settle
                                                 └─ END
```

这不是“每个业务节点后再创建一个 Hook 子图”，而是“每个业务节点完成后跳转到同一个
Hook 节点”。四条业务到 Hook 的路径在一次 activation 中互斥，不使用 Join。当前 Graph
的 `Graph.node_output("hook_request")`（一参数 `PredecessorOutputRef`）会从实际控制
前驱读取同名 output；它要求四个前驱声明同一个 exact `HookRequest` class，正好满足该
拓扑。Hook 后 route 输出同一个 exact `HookResult` class，后续业务节点通过
`Graph.node_output("hook_result")` 读取 route 的实际 predecessor publication。

### 9.21.2 共享 Hook 的 concrete value

`HookNode` 每个实例只有一个 `ValueT`，因此共享 Hook 不得使用四种 stage value 的 union、
`object` 或 `Any`。实施稿冻结一个 Act-owned `ActHookEnvelope` 作为唯一 `ValueT`：

```text
HookNode[..., ActHookEnvelope, HookStateProjection, ActHookCommand]
```

envelope 内部使用 sealed nominal `ActStageValue` family（四个 concrete stage payload）和
closed `ActHookStage`/`ActHookRoute` enum。Hooks owner 的 `HookPayloadAdmission` 负责
stage/payload 对应、identity/provenance 保持、固定下一 route、共享
`HookStateProjection`/command exact
admission；Act 不读取 Hook 私有 Plan/config，也不复制 runner。这样既满足严格 Graph value
universe，又不把四个 Hook 的业务语义塞进一个开放 union。阶段差异 command 由
Hooks/composition owner 封装进同一个 `ActHookCommand` concrete class，不能再增加四个
Hook 或四个 command slot。

### 9.21.3 固定 assembly 和结果归属

Act 的直接节点现在固定为六个：`resolve`、`authorize`、`execute`、`settle`、`hook`、
`route`。边固定为四条业务到 `hook`、一条 `hook -> route` 和四条 route conditional
edge（`authorize`、`execute`、`settle`、`END`）。不存在 `resolve_hook`、
`authorize_hook`、`execute_hook`、`settle_hook` 参数、slot 或实例。

`RouteAfterHook` 只校验 envelope 的合法阶段迁移并把 `HookResult` 原样作为
`hook_result` output；它不执行 Port、不解释 command、不生成业务结果。`Settle` 仍是
唯一消费 `ToolExecutionResult`、调用 `SettlementPort`/writer 并形成 `SettledActResult` 的
业务节点；继续路径只读取 `HookResult.value`，中间 commands 不由 Act 累计或 apply；Settle
成功后也必须经过共享 Hook，再由 route 到 `END`。工具结果仍只能由 `ExecutePort` 产生。

### 9.21.4 实施和验收要求

- `ActNode` 只接收一个 `hook` 参数，slot 为 `hook/AFTER_NODE`，并与 Act 使用同一
  `Graph[HookGraphValue]`；
- 四个业务 callable 的成功 output 名统一为 `hook_request`，route output 名统一为
  `hook_result`；compiler 必须通过多前驱 exact descriptor admission；
- Phase 1 测试证明共享 Hook 在四个阶段各运行一次、没有重复 Hook 实例、非法跨阶段 route
  会失败、Hook/route 失败不会旁路下游；
- 更新所有拓扑、节点计数、slot、泛型、调用顺序和测试矩阵的文字，历史章节若保留旧
  四 Hook 形状必须明确标为历史，不得作为实施依据。

本节取代第 9.19、9.20 中关于“四个独立 Hook、八个直接节点、七条顺序边”的旧拓扑文字；
其余已拍板的 Graph/State/Commit、Authorize、Settle、Failover、codec 和模型可见内容边界
保持不变。当前没有新的架构选择需要用户拍板。

## 9.22 扁平模块布局（当前生效）

Act 不保留 `tool_use` 层或阶段子包。所有实现按职责放在
`mote_kernel.act` 下的普通模块：`node.py`、`resolve.py`、`authorize.py`、
`execute.py`、`settle.py`、`route.py`、`contract.py`、`identity.py`、
`admission.py`、`port.py`。`admission.py` 与 `port.py` 同样是扁平职责模块，不再
挂在阶段子包中；阶段模块各自只声明对应图节点，根包唯一导出 `ActNode`。这只是
文件布局调整，不增加节点、Port、状态或恢复入口。

## 10. 参考源码

- [`docs/architecture.zh-CN.md`](./architecture.zh-CN.md)：唯一 Graph、State、reducer 和 commit 边界；
- [`src/mote_kernel/execution/facade.py`](../src/mote_kernel/execution/facade.py)：Graph builder、nested graph、compile freeze、interrupt/resume；
- [`src/mote_kernel/execution/graph/node.py`](../src/mote_kernel/execution/graph/node.py)：异步 `NodeCallable` contract；
- [`src/mote_kernel/execution/graph/outcome.py`](../src/mote_kernel/execution/graph/outcome.py)：success/failure/interrupt factory；
- [`src/mote_kernel/execution/family_driver.py`](../src/mote_kernel/execution/family_driver.py)：`GraphTransition`、exact commit 和 nested family owner；
- [`src/mote_kernel/execution/result.py`](../src/mote_kernel/execution/result.py)：`GraphCommitResult` 与 child projection；
- [`src/mote_kernel/invocation.py`](../src/mote_kernel/invocation.py)：唯一异步 Invocation 适配接缝；
- [`src/mote_kernel/hooks/node.py`](../src/mote_kernel/hooks/node.py)：现有 typed nested-domain graph 的参考形状。
- [`docs/think-graph-implementation-plan.zh-CN.md`](./think-graph-implementation-plan.zh-CN.md)：本轮采用的 DTO、Port、Graph/State 和外部 Failover 装饰边界基线。
