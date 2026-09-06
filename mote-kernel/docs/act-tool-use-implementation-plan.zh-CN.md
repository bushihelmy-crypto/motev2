# Act / Tool Use 四个业务节点、共享 Hook 与路由实施计划

最后更新：2026-09-05

状态：实施版。本文是 `mote_kernel.act` 的进程内 v1 实施契约；不包含 Act 专用持久化、
跨进程恢复或 exactly-once。模型可见的 ToolResult、拒绝说明和其他文本不由 Kernel 定义，
统一由外部 protocol/presentation owner 序列化为英文。

## 0. 固定结论

### 0.1 拓扑只有四个业务节点、一个共享 Hook 和一个路由节点

Act 的业务阶段仍然是四个：`Resolve`、`Authorize`、`Execute`、`Settle`。四个业务节点
成功结束后都把同一个 concrete `HookRequest` output 送入**同一个** `HookNode`；Hook 完成
后进入普通 callable `RouteAfterHook`，再由 conditional edge 选择下一个业务节点或
`END`。不为每个业务节点复制一个 HookNode。

```text
Resolve ───────────────┐
Authorize ─────────────┤
Execute ───────────────┤── hook_request ──> Hook ──> RouteAfterHook
Settle ────────────────┘                              ├─ authorize
                                                      ├─ execute
                                                      ├─ settle
                                                      └─ END
```

实际控制顺序由 `ActHookEnvelope.next` 固定为：

```text
Resolve -> Hook -> Route -> Authorize
Authorize -> Hook -> Route -> Execute
Execute -> Hook -> Route -> Settle
Settle -> Hook -> Route -> END
```

图中四条业务到 Hook 的边是互斥控制路径，不是 Join。Hook 的输入使用当前 Graph 已支持
的前驱 output 绑定：

```python
Graph.node_output("hook_request")
```

这是一参数 `PredecessorOutputRef`，表示“从本次实际控制前驱读取名为
`hook_request` 的 output”。四个业务节点声明同一个 exact `HookRequest` class，compiler
因此可以把它们汇入一个 Hook。`RouteAfterHook` 输出同一个 exact `HookResult` class，后续
业务节点使用：

```python
Graph.node_output("hook_result")
```

读取路由节点的实际前驱 publication。这样不会从某个旧业务节点固定读取值，也不需要隐藏
缓存、Join 或第二个 runner。

Act 的直接节点数固定为六个：

| 节点 | 类型 | 作用 |
| --- | --- | --- |
| `resolve` | 业务 callable | 固定工具 binding，产生 Resolve stage envelope |
| `authorize` | 业务 callable | 发起/恢复授权，产生 Authorize stage envelope |
| `execute` | 业务 callable | 调用工具并产生唯一 `ToolExecutionResult` |
| `settle` | 业务 callable | 消费执行结果、投影并写入，产生 Settle stage envelope |
| `hook` | 一个真实 nested `HookNode` | 对四个 stage 共用的 envelope 执行 `Plan → P1 → P2 → P3` |
| `route` | 普通 callable | 校验 Hook 结果并按 typed route 选择下一业务节点 |

Hook 内部的 `plan`、`p1`、`p2`、`p3` 不计入 Act 直接节点。`route` 不是第五个业务阶段，
只负责 Graph conditional routing 和把 `HookResult` 原样向下游传递。

### 0.2 每个节点的职责

| 节点 | 输入 | 成功 output | Hook 后目标 | 失败/等待 |
| --- | --- | --- | --- | --- |
| Resolve | `ActRequest` | `HookRequest(ActHookEnvelope(RESOLVE, AuthorizationInput))` | `Authorize` | 解析失败直接 Graph failure，不运行 Hook |
| Authorize | 上一次 Hook 的 `HookResult` | `HookRequest(ActHookEnvelope(AUTHORIZE, AuthorizedInvocation))` | `Execute` | 首次返回 interrupt；Deny 直接 failure，不运行 Hook |
| Execute | 上一次 Hook 的 `HookResult` | `HookRequest(ActHookEnvelope(EXECUTE, ToolExecutionResult))` | `Settle` | 调用前终止/异常/取消沿 Graph 边界结束 |
| Settle | 上一次 Hook 的 `HookResult` | `HookRequest(ActHookEnvelope(SETTLE, SettledActResult))` | `END` | project/write 失败由对应 Port-level Failover 处理 |

“谁取得工具结果”固定为：只有 `ExecutePort.execute()` 产生
`ToolExecutionResult`；`Settle` 只消费来自 Execute 的结果，不查询工具、不重新执行、
不从 writer 读取结果。`Settle` 完成后仍必须经过同一个共享 Hook，再由 route 路由到
`END`；Hook 不是结果生产者，也不替代 Settle 的交付职责。

### 0.3 依赖和缺失行为

| 依赖 | v1 要求 | 缺失行为 |
| --- | --- | --- |
| `ResolvePort` | 必须 | Act assembly 失败 |
| 统一 `AuthorizePort` | 必须 | Act assembly 失败；不拆出 `ApprovalPort` |
| `ExecutePort` | 必须 | Act assembly 失败 |
| `SettlementPort` | 必须 | Act assembly 失败 |
| `ToolExchangeWriter` | 正常 ToolResult 交付必须 | Act assembly 失败 |
| 一个共享 `HookNode` | 必须 | Act assembly 失败；不得按阶段复制 Hook |
| `Graph`/`GraphRunState` | 由 execution facade 提供 | 不创建 Act 私有 runner/state |
| Port-level Failover | **每个启用故障恢复的 concrete Port 都必须先由 composition 套上 Failover**，再以同一 typed Port surface 注入；是否禁用由 composition 以显式 binding 决定 | Act 不实现 retry loop、不包整图 |

Authorize 的策略、主体/资源/风险判断和是否需要外部确认由 `AuthorizePort` owner 决定；
Kernel 不另设 `ApprovalPort`。本实施稿采用已冻结的流程：首次 Authorize 调用同一个
Port 并 `Graph.interrupt(bytes)`，恢复只接受无字段 `Allow`/`Deny`。没有外部确认配置的
场景由外部 owner 在 assembly 时提供符合该 Port contract 的实现；Kernel 不新增 auto
bypass、Pending 业务 variant 或第二条授权路径。

### 0.4 拒绝、ToolResult 和停止

Deny 的顺序固定为：

1. Runtime/Authorization owner 认证 response、关联 opaque request handle，并构造 exact
   `Deny` resume input；
2. Runtime/protocol owner 按原 `tool_call_id` 写入配对的拒绝 ToolResult；实际写给模型的
   内容由该 owner 以英文序列化；
3. owner 将 `Deny()` 交给 Act 的 resume helper；
4. Authorize 返回 `Graph.failure(opaque_failure_reason)`，父 Flow 得到
   `FailedResult`/stop；`Hook`、`Execute`、`Settle` 都不运行。

Act 不生成拒绝文案、不读取 ToolResult，也不把 Deny 原因放进 Graph value。配对或写入未
确认时不能伪装成 Deny，应该沿普通 contract/infrastructure failure 结束。

### 0.5 Settle 与 Failover

Settle 的一次 logical operation 是：

```text
ToolExecutionResult
  -> SettlementPort.project()
  -> SettlementProjection
  -> ToolExchangeWriter.write()
  -> ToolExchangeWriteResult
  -> SettledActResult
  -> shared Hook
  -> RouteAfterHook -> END
```

composition 必须在把启用故障恢复的 concrete Port 注入 Act 前完成 Port-level Failover 装饰；
`ResolvePort`、`AuthorizePort`、`ExecutePort`、`SettlementPort` 和 `ToolExchangeWriter` 均遵守
同一顺序。未启用的 Port 也必须由 composition 显式声明其 disabled binding，不能由 Act 自行
决定是否包裹。
装饰结果必须保留对应的完整 typed Port surface（Authorize 的 codec/correlation 方法也必须
保持可用）。Failover 只在该 Port 的一次 logical operation 内重试同一个 immutable request；
不能重跑 Act、重新 Execute、复制 Hook 或创建 Settle-only Graph 入口。v1 不把 Failover 的
attempt/reconcile state 放入 Act。

## 1. Owner 边界

| owner | 负责 | 不负责 |
| --- | --- | --- |
| `mote_kernel.act` | 四个业务 callable、一个共享 Hook 的外层接线、route、DTO/admission、Port 调用、resume helper 和 Graph outcome | 工具目录、授权策略、用户交互、ToolResult schema/文案、writer 状态、Failover 内部、持久化 |
| `mote_kernel.execution.Graph` + `GraphRunState` | 唯一 Graph facade、activation、执行位置、前驱 publication、节点 settlement、interrupt/resume、recovery admission、reducer/commit | 解释授权原因、解析模型内容 |
| Runtime/Authorization owner | 发起授权、保存/认证 opaque handle、构造 `Allow`/`Deny`、提供同一 Port 的 codec/correlation | 把 actor/reason/UI 文案塞进 Kernel |
| Resolve/Execute/Settlement/Protocol owner | 提供 Port 实现、binding、opaque outcome/projection、writer receipt | 修改 Graph state、创建私有 runner |
| Hooks owner | 提供**一个**共享 `HookNode`、其 concrete config/state/value/command admission 和 P1/P2/P3 | 为四个业务节点复制 Hook、把 command 写入 GraphRunState |
| composition/Failover owner | 为启用故障恢复的 concrete Port 装饰 Failover，保持原 Port contract，再注入 Act | 让 Act 自行决定是否包裹、包住整张 Act Graph、要求 Act 重放节点 |
| presentation/protocol owner | ToolCall/ToolResult pairing、英文序列化 | 要求 Kernel 定义默认文案/翻译 |

Port 可以持有自身 runtime client 或远端 handle；只要不进入 Act Graph value、`ActNode` 的
运行时可变字段或 `GraphRunState` 平行副本，就不构成第二个 Kernel state。

## 2. Graph 复用边界

已核对的现有 Graph 能力：

- `mote_kernel.execution.Graph` 是唯一公共组合和执行 facade；Act 不创建 runner/executor；
- `Graph[GraphValueT]` 的 parent/nested graph 必须使用同一个 value universe；Act 固定为
  `Graph[HookGraphValue]`，不定义 `ActGraphValue`；
- `Graph.add_node()` 支持 nested `Graph`；一个 Hook 在父图中仍是一个 node；
- `Graph.node_output("name")` 是当前控制前驱 output 的公开绑定，适用于多个互斥前驱汇入同一节点；
- 多个前驱 output 必须是同一个 exact nominal class。四个业务节点都使用
  `HookRequest`，route 和业务输入都使用 `HookResult`；
- `Graph.add_conditional_edge()` 提供 Hook 后普通路由；不要用 Join 或私有路由器替代；
- graph input 且没有 incoming control edge 的 `resolve` 自动成为 entry，不重复添加
  `START -> resolve`；
- 首次成功 compile 后 Graph definition 冻结，Act 构造器在 compile 前一次性完成 assembly；
- `Graph.interrupt(bytes)` 只运输 bytes；Graph 不展示、解析或翻译它；
- `GraphRunState` 是唯一运行时状态，普通节点结果先经 execution settlement，再进入 reducer/
  optional `Graph.Commit`；Act 不直接改 state/store。

### 2.1 共享 Hook 的类型边界

现有 `HookNode` 一次只接受一个 exact `ValueT`。因此不能让一个共享 Hook 的 `ValueT` 取
四种 stage value 的 union，也不能使用 `object`、`Any` 或 cast 绕过 descriptor。实现必须
定义一个 concrete `ActHookEnvelope`，把四个阶段的 payload 封装在同一个 nominal outer class
中：

```text
HookNode[..., ActHookEnvelope, HookStateProjection, ActHookCommand]
```

`ActHookEnvelope.payload` 使用封闭的 `ActStageValue` nominal family；四个具体 payload
分别承载 `AuthorizationInput`、`AuthorizedInvocation`、`ToolExecutionResult`、
`SettledActResult`。`stage`/`next` 使用 closed enum `ActHookStage`/`ActHookRoute`，不使用
字符串状态或布尔拼接。`HookPayloadAdmission` 由 Hooks owner 提供，并负责：

- `stage` 与 payload concrete class 的对应关系；
- 该 stage 的 identity/provenance 保持；
- `next` 只能是固定的下一路由；
- composition 提供的 `HookStateProjection` 和 `ActHookCommand` 的 exact nominal admission。

四个阶段若产生不同业务 command，必须由 Hooks/composition owner 封装成这个共享
`ActHookCommand` concrete class；Act 不为阶段再声明四种 command，也不复制 Hook。

Act 只检查外层 `HookRequest`/`HookResult`、envelope class、合法 stage/route 和跨阶段
identity；不读取 Hook 私有 plan、config 或 command 业务内容。

## 3. Concrete DTO 契约

### 3.1 通用规则

- 所有进入 Graph frame 的 Act DTO 都直接继承 `HookGraphValue`；不得定义独立
  `ActGraphValue`；
- 使用 `@dataclass(frozen=True, slots=True)` 或等价不可变 nominal class；禁止 `Any`、裸
  dict、反射和兼容 alias；
- Graph descriptor 只使用 concrete class，不使用 union alias、`object` 或泛型擦除别名；
- closed control variant 用独立 concrete class 表示；
- Act admission 只检查 exact outer class、immutable 外壳、通用 identity/digest、长度和
  route；业务 payload 的解释由 producer/Port/Hooks owner 负责；
- callable、registry、credential、raw exception 和模型文本不得进入 Graph value。

### 3.2 固定 outer class 和 owner

| class | 结构职责 | owner |
| --- | --- | --- |
| `HookGraphValue` | Think/ReAct/Act 唯一 carrier | Hooks package |
| `HookRequest`/`HookResult` | 业务节点与共享 Hook 的 boundary | Hooks package |
| `ActRequest` | ToolCall pairing、selector、arguments、caller、hook state | Runtime/Act contract |
| `ResolvedInvocation` | request 与稳定 definition/binding/arguments digest 的绑定 | ResolvePort |
| `ResolvePortResult` | `ResolvedInvocation`/`ResolutionStopped` 的 closed Port result | ResolvePort |
| `AuthorizationRequestRef` | 可编码的 opaque request handle | Authorization owner |
| `AuthorizationInterruptView` | 从 Graph 公开 awaiting 字段投影的 Act DTO | Act helper |
| `ActSlotId` | Act 内四个业务 slot 的 definition/version/node identity | Act assembly |
| `AuthorizationInput` | initial/resumed 两个 phase 的统一 outer class | Act + Authorization owner |
| `Allow`/`Deny` | 无字段恢复决定 | Authorization owner |
| `AuthorizedInvocation` | Allow 后的同一 resolved identity | Act |
| `ToolExecutionResult` | Execute 唯一产生的结果 | ExecutePort |
| `ExecutePortResult` | `ToolExecutionResult`/`ExecutionStopped` 的 closed Port result | ExecutePort |
| `SettlementProjection` | 带 execution identity 的 opaque protocol projection | SettlementPort |
| `ToolExchangeWriteRequest/Result` | 一次 writer delivery 与 receipt | Act/writer |
| `SettledActResult` | writer 确认后的正常结果 | Act |
| `ActStageValue` | 四个 stage payload 的 sealed nominal base | Act |
| `ActHookEnvelope` | 共享 Hook 的统一 ValueT | Act + Hooks admission |
| `ActHookCommand` | 共享 Hook 的 opaque command concrete class | Hooks owner |
| `HookStateProjection` | 共享 Hook 的 composition-provided concrete immutable state projection | Hooks/composition owner |

### 3.3 v1 强制字段

以下是 outer structure；opaque bytes 的业务语义不在 Kernel 内定义。

表中所有 Act-owned class（包括 identity wrapper、phase/decision、stage payload 和 result
envelope）都直接或通过已冻结的 Act nominal family 继承 `HookGraphValue`；
`HookRequest`/`HookResult` 继续使用 Hooks package 的既有 carrier。只有
`HookStateProjection`/`ActHookCommand` 的具体字段由 composition/Hooks owner 提供，但它们
也必须满足同一 carrier 和 immutable admission。

```text
ToolCallId                 value: str, canonical, <= 256 UTF-8 bytes
ToolExchangeScopeId        value: str, canonical, <= 256 UTF-8 bytes
ActInvocationKey           value: str, canonical, <= 256 UTF-8 bytes
ToolSelector               value: str, canonical, <= 256 UTF-8 bytes
ToolBindingRef             value: str, canonical, <= 256 UTF-8 bytes
OpaqueDefinitionReference  value: str, canonical, <= 256 UTF-8 bytes
CallerIdentityRef          value: bytes, <= 4096 bytes
OpaqueAuthorizationHandle  value: bytes, <= 4096 bytes
OpaqueArguments            value: bytes, <= 65536 bytes
ArgumentsDigest            value: bytes, non-empty, <= 128 bytes
OpaqueExecutionOutcome     value: bytes, <= 1048576 bytes
OpaqueProtocolPayload      value: bytes, <= 1048576 bytes
OpaqueToolExchangeReceipt  value: bytes, <= 1048576 bytes
OpaqueGraphFailureReason   value: str, <= 512 UTF-8 bytes

AuthorizationDecision      closed nominal family: Allow | Deny
AuthorizationPhase         closed nominal family: InitialAuthorization | ResumedAuthorization
```

```text
ActSlotId
  definition_id: str              # canonical, <= 256 UTF-8 bytes
  definition_version: int         # exact int >= 1
  node_id: str                    # exactly resolve|authorize|execute|settle

ToolPairingIdentity
  scope: ToolExchangeScopeId
  invocation: ActInvocationKey
  tool_call_id: ToolCallId

ActRequest
  pairing: ToolPairingIdentity
  selector: ToolSelector
  arguments: OpaqueArguments
  caller: CallerIdentityRef
  hook_state: HookStateProjection

CanonicalArguments
  payload: OpaqueArguments
  digest: ArgumentsDigest

ResolvedInvocation
  request: ActRequest
  definition: OpaqueDefinitionReference
  binding: ToolBindingRef
  arguments: CanonicalArguments

InitialAuthorization
  resolved: ResolvedInvocation

ResumedAuthorization
  resolved: ResolvedInvocation
  request_ref: AuthorizationRequestRef
  decision: AuthorizationDecision

AuthorizationInput
  phase: InitialAuthorization | ResumedAuthorization

AuthorizationRequestRef
  pairing: ToolPairingIdentity
  handle: OpaqueAuthorizationHandle

AuthorizedInvocation
  resolved: ResolvedInvocation

ToolExecutionIdentity
  pairing: ToolPairingIdentity
  binding: ToolBindingRef
  arguments_digest: ArgumentsDigest

ToolExecutionResult
  identity: ToolExecutionIdentity
  outcome: OpaqueExecutionOutcome

SettlementProjection
  source_identity: ToolExecutionIdentity
  payload: OpaqueProtocolPayload

ToolExchangeWriteRequest
  projection: SettlementProjection

ToolExchangeWriteResult
  receipt: OpaqueToolExchangeReceipt

SettledActResult
  projection: SettlementProjection
  receipt: OpaqueToolExchangeReceipt

AuthorizationInterruptView
  scope: tuple[str, ...]
  node_id: str                 # must be authorize
  interrupt_id: str
  request_payload: bytes
```

`ResolvePortResult` 是只允许 `ResolvedInvocation`/`ResolutionStopped` 的 closed result
family；`ExecutePortResult` 是只允许 `ToolExecutionResult`/`ExecutionStopped` 的 closed
result family。两者不是 Graph input/output descriptor，也不能通过字符串 tag 扩展。

`ResolvedInvocation.arguments.digest` 必须与 `ToolExecutionIdentity.arguments_digest` 逐值
相等；ExecutePort 必须原样传递，Settle 不重新计算。`AuthorizationInput.initial(resolved)`
只能构造 `InitialAuthorization`；resume helper 只能构造带同一 pairing 的
`ResumedAuthorization`。`ResolutionStopped` 和 `ExecutionStopped` 是各带一个
`OpaqueGraphFailureReason reason` 的 closed stop class，不是 Graph descriptor。

`ActSlotId` 标识 Act 自身的四个业务 slot，不是父图挂载点，也不是共享 Hook 的 slot。父图
挂载点只由父图 `Graph.add_node(node_id, act)` 决定；共享 Hook 使用 Hooks owner 的
`HookSlotId(node_id="hook", stage=AFTER_NODE)`。同一 Act definition 可以在父图中挂载多次，
由 Graph scope 区分实例。

`HookStateProjection` 不是 Act 自己定义的第二个 state model；它是 composition/Hooks owner
在装配时提供的一个 concrete immutable `HookGraphValue` class。其内部字段和业务含义由该
owner 解释，Act 只要求 `ActRequest.hook_state`、`ActHookEnvelope.hook_state` 和
`HookRequest.state` 使用同一个 exact class/值，不允许把 `GraphRunState` 或可变 client 放入其中。

### 3.4 共享 Hook envelope 的强制结构

```text
ActHookStage (closed enum)
  RESOLVE = "resolve"
  AUTHORIZE = "authorize"
  EXECUTE = "execute"
  SETTLE = "settle"

ActHookRoute (closed enum; graph_token is the only Graph string boundary)
  AUTHORIZE.graph_token = "authorize"
  EXECUTE.graph_token = "execute"
  SETTLE.graph_token = "settle"
  END.graph_token = "end"

ResolveStageValue
  authorization: AuthorizationInput

AuthorizeStageValue
  invocation: AuthorizedInvocation

ExecuteStageValue
  result: ToolExecutionResult

SettleStageValue
  result: SettledActResult

ActHookEnvelope
  stage: ActHookStage
  next: ActHookRoute
  payload: ActStageValue
  hook_state: HookStateProjection

HookRequest[ActHookEnvelope, HookStateProjection]
  value: ActHookEnvelope
  state: HookStateProjection

HookResult[ActHookEnvelope, ActHookCommand]
  value: ActHookEnvelope
  commands: tuple[ActHookCommand, ...]
```

`ActStageValue` 及其四个 stage 子类都直接继承 `HookGraphValue`；
`ResolveStageValue`、`AuthorizeStageValue`、`ExecuteStageValue`、`SettleStageValue` 都是
`ActStageValue` 的 concrete nominal 子类；不把四者声明成 Graph descriptor union。共享
Hook 的 admission 必须拒绝以下情况：stage 与 payload 不匹配、跳过阶段的 route、改变
pairing/definition/binding/digest、改变 `hook_state`、把 `Settle` route 改回业务节点，或
返回错误的 state/command class。`HookRequest.state` 必须与
`HookRequest.value.hook_state` exact 相等。Hook 可以产生 opaque command，但不能改变已
交付的 `SettledActResult`。

### 3.5 Admission API 和上限

`ActPayloadAdmission` 是 frozen/slots 的固定结构门禁，不接受动态 type registry：

```text
ActPayloadAdmission                         # frozen/slots；无可变运行时状态
  IDENTITY_MAX_BYTES = 256
  SCOPE_MAX_SEGMENTS = 16
  SCOPE_MAX_BYTES = 4096
  PROTECTED_REF_MAX_BYTES = 4096
  INTERRUPT_PAYLOAD_MAX_BYTES = 65536
  ARGUMENTS_MAX_BYTES = 65536
  DIGEST_MAX_BYTES = 128
  OUTCOME_MAX_BYTES = 1048576
  PROJECTION_MAX_BYTES = 1048576
  RECEIPT_MAX_BYTES = 1048576
  FAILURE_REASON_MAX_BYTES = 512

admit_act_slot(ActSlotId) -> ActSlotId
admit_tool_pairing_identity(ToolPairingIdentity) -> ToolPairingIdentity
admit_tool_execution_identity(ToolExecutionIdentity) -> ToolExecutionIdentity
admit_graph_failure_reason(OpaqueGraphFailureReason) -> OpaqueGraphFailureReason
admit_request(ActRequest) -> ActRequest
admit_resolved_invocation(ResolvedInvocation) -> ResolvedInvocation
admit_authorization_request_ref(AuthorizationRequestRef) -> AuthorizationRequestRef
admit_interrupt_view(AuthorizationInterruptView) -> AuthorizationInterruptView
admit_interrupt_payload(bytes) -> bytes
admit_authorization_input(AuthorizationInput) -> AuthorizationInput
admit_authorized_invocation(AuthorizedInvocation) -> AuthorizedInvocation
admit_resolution_result(ResolvePortResult) -> ResolvePortResult
admit_execution_result(ResolvedInvocation, ToolExecutionResult) -> ToolExecutionResult
admit_execution_outcome(ExecutePortResult) -> ExecutePortResult
admit_settlement_projection(ToolExecutionResult, SettlementProjection) -> SettlementProjection
admit_write_request(ToolExchangeWriteRequest) -> ToolExchangeWriteRequest
admit_write_result(ToolExchangeWriteResult) -> ToolExchangeWriteResult
admit_settled_result(SettledActResult) -> SettledActResult
admit_hook_envelope(ActHookEnvelope) -> ActHookEnvelope
admit_hook_result(HookResult[ActHookEnvelope, ActHookCommand]) -> same result
```

方法只做 exact nominal class、carrier、不可变外壳、长度、identity/digest 和固定 route
检查；不解析 provider payload，不生成错误码或文案。固定上限为：identity/reference
256 bytes、scope 最多 16 段/总计 4096 bytes、protected ref 4096 bytes、interrupt
payload 65536 bytes、arguments 65536 bytes、digest 128 bytes、execution/projection/
receipt 1048576 bytes、Graph failure reason 512 UTF-8 bytes。

## 4. Port Protocol

所有访问外部资源的操作是 async；codec/correlation 是本地确定性 sync 方法。

```python
@runtime_checkable
class ResolvePort(Protocol):
    async def resolve(self, request: ActRequest, /) -> ResolvePortResult: ...


@runtime_checkable
class AuthorizePort(Protocol):
    async def request_authorization(
        self, invocation: ResolvedInvocation, /
    ) -> AuthorizationRequestRef: ...

    def encode_interrupt(self, request_ref: AuthorizationRequestRef, /) -> bytes: ...

    def build_resume_input(
        self,
        interrupt: AuthorizationInterruptView,
        decision: Allow | Deny,
        /,
    ) -> AuthorizationInput: ...

    def encode_graph_input(self, values: Graph.Values[HookGraphValue], /) -> bytes: ...
    def decode_graph_input(self, payload: bytes, /) -> Graph.Values[HookGraphValue]: ...

    @property
    def codec_id(self) -> str: ...

    @property
    def codec_version(self) -> int: ...


@runtime_checkable
class ExecutePort(Protocol):
    async def execute(self, invocation: AuthorizedInvocation, /) -> ExecutePortResult: ...


@runtime_checkable
class SettlementPort(Protocol):
    async def project(self, result: ToolExecutionResult, /) -> SettlementProjection: ...


@runtime_checkable
class ToolExchangeWriter(Protocol):
    async def write(self, request: ToolExchangeWriteRequest, /) -> ToolExchangeWriteResult: ...
```

`AuthorizePort` 是唯一授权/确认能力；不定义 `ApprovalPort`。是否为某个 Port 启用 Failover
必须在 composition 显式绑定后再注入，Act 不在运行时自行包裹或重试。`codec_id: str`、
`codec_version: int` 是同一 Port 的字段，并原样用于一次
`Graph.set_resume_codec()`。Port 只关联 opaque handle 并构造已认证的 Allow/Deny；它不
决定 Graph 是否仍可恢复。重复 resume、旧 state、scope/interrupt 和 codec mismatch 由
Graph recovery admission 权威拒绝，Kernel 不在 Port 调用前另做一套认证。

Port 返回错误 nominal class、错误 pairing、错误 digest 或超限 payload 时，Act 在下游
调用前 fail closed；普通异常/取消沿 Graph 边界传播，不被转换成业务成功或 Deny。

## 5. Resume helper

Act 对外只提供 Act-owned immutable interrupt projection，不公开 execution 内部
`GraphInterruptView` 等类型：

```python
def resume_authorization(
    self,
    *,
    awaiting: Graph.AwaitingResumeResult[HookGraphValue],
    interrupt_id: str,
    decision: Allow | Deny,
) -> Graph.ResumeAction[HookGraphValue]: ...
```

helper 的固定步骤：

1. 从 `awaiting.interrupts` 的公开字段投影 `AuthorizationInterruptView`；不读取私有
   continuation/frame；
2. 检查目标 `interrupt_id`、`node_id="authorize"` 和外壳上限；
3. 将 view 和无字段 `Allow`/`Deny` 交给同一个 `AuthorizePort.build_resume_input()`；
4. exact-admit 返回值，要求是 resumed phase、decision class 与参数一致、request ref 与
   原 pairing 一致；
5. 将 resumed `AuthorizationInput` 放进与正常路径相同的 `HookResult` outer envelope：构造
   `ActHookEnvelope(RESOLVE, AUTHORIZE, ResolveStageValue(resumed_input),
   resumed_input.phase.resolved.request.hook_state)`；`hook_state` 从已认证的 resumed
   invocation 派生，不能从私有 continuation 或可变缓存读取；用
   `Graph.values(hook_result=HookResult(envelope, ()))` 构造与 `authorize` 输入 descriptor
   完全一致的 override；
6. 调用现有 `Graph.resume_interrupted()`，保留公开 scope/node/interrupt 字段。

helper 不承诺提前拒绝重复 resume、旧 state 或 codec mismatch；这些由持有 awaiting result
的 root Graph 权威拒绝。Act 作为 nested graph 时只生成 action，不直接驱动第二个 runner。

## 6. ActNode assembly

### 6.1 构造签名

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
    hook: ActHookNode,
    failure_reason: OpaqueGraphFailureReason,
    admission: ActPayloadAdmission,
)
```

其中 `ActHookNode` 是现有 `HookNode` 的具体静态绑定：

```text
HookNode[HookConfigT, PriorityConfigT,
         ActHookEnvelope, HookStateProjection, ActHookCommand]
```

它只有一个实例，`hook.slot.node_id` 必须是 `"hook"`，definition/version 与 Act 相同，
stage 为 `HookStage.AFTER_NODE`。不再有 `resolve_hook`、`authorize_hook`、
`execute_hook`、`settle_hook` 四个参数，也不允许四个 Hook 实例。

`ActNode` 继承 `Graph[HookGraphValue]`。除 Graph 基类字段外，只保存上述装配引用；不保存
run-local cache、task、approval state、writer state 或结果缓存。构造器在任何 `add_node()`
前完成所有 exact protocol、slot、definition/version、codec 和 admission 校验，失败时不
返回半成品 Graph。

其新增 slots 固定为：

```text
_resolve_port, _authorize_port, _execute_port, _settlement_port,
_exchange_writer, _hook, _failure_reason, _admission
```

不得创建 `__dict__`、run-local mutable cache、后台 task 或可重绑的 capability 字段。

构造器使用同一个 `definition_id/version` 派生四个 `ActSlotId`（`resolve`、`authorize`、
`execute`、`settle`），只用于固定业务 node identity 和测试；不从 Port 地址、`repr()`、
运行时配置或父图挂载 id 派生。共享 Hook 不占用这四个 Act slot。

### 6.2 固定 assembly 形状

下面是必须实现的结构伪代码；`resolve_callable` 等是 Act 包内四个普通 callable，
`hook` 是注入的唯一真实 nested `HookNode`，`route_callable` 是普通 callable：

```python
act = Graph[HookGraphValue](definition_id, version=version)
request = Graph.graph_input("request", ActRequest)
hook_request_type = HookRequest
hook_result_type = HookResult

act.add_node(
    "resolve",
    resolve_callable,
    inputs={"request": request},
    outputs={"hook_request": hook_request_type},
)
act.add_node(
    "authorize",
    authorize_callable,
    inputs={"hook_result": Graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)
act.add_node(
    "execute",
    execute_callable,
    inputs={"hook_result": Graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)
act.add_node(
    "settle",
    settle_callable,
    inputs={"hook_result": Graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)
act.add_node(
    "hook",
    hook,
    inputs={"request": Graph.node_output("hook_request")},
)
act.add_node(
    "route",
    route_callable,
    inputs={"result": Graph.node_output("hook", "result")},
    outputs={"hook_result": hook_result_type},
)

for business_node in ("resolve", "authorize", "execute", "settle"):
    act.add_edge(business_node, "hook")
act.add_edge("hook", "route")
act.add_conditional_edge("route", "authorize", "authorize")
act.add_conditional_edge("route", "execute", "execute")
act.add_conditional_edge("route", "settle", "settle")
act.add_conditional_edge("route", "end", Graph.END)
act.set_outputs({"result": Graph.node_output("route", "hook_result")})
```

实现中 route 字符串只能是 Graph edge 的 canonical route token；业务含义由
`ActHookRoute` closed enum 表示，不能在 payload 中使用自由字符串 discriminator。route
callable 将 enum 映射为上述四个固定 Graph edge token，并原样返回 `HookResult`：

```text
route(result):
  result = admission.admit_hook_result(result)
  envelope = result.value
  validate_stage_payload_and_legal_transition(envelope)
  return Graph.success(Graph.values(hook_result=result), route=envelope.next.graph_token)
```

route 不读取、累计、去重、投递或 apply `HookResult.commands`；继续路径只让后续业务节点
从 `HookResult.value` 读取 envelope。只有 route 到 `END` 的最后一次 `HookResult` 作为
Act graph output 对外可见，前面激活产生的 commands 不在 Act 内形成第二条提交路径。

只有 `route` 允许产生 conditional route；业务节点不能自行跳过 Hook 直接连到下游。四个
业务节点的成功 output 名必须都是 `hook_request`，route output 名必须是 `hook_result`，
否则 compiler 无法证明共享前驱类型。

### 6.3 节点 callable 行为

#### Resolve

```text
request = exact(values["request"], ActRequest)
resolved = await resolve_port.resolve(request)
if ResolvedInvocation:
  admit request identity and arguments digest
  input = AuthorizationInput.initial(resolved)
  envelope = ActHookEnvelope(
      RESOLVE, AUTHORIZE, ResolveStageValue(input), request.hook_state
  )
  return Graph.success(Graph.values(
      hook_request=HookRequest(envelope, request.hook_state)
  ))
if ResolutionStopped:
  return Graph.failure(reason.value)
```

Resolve 只固定 definition/version/binding/arguments digest，不查权限、不把 callable 或
registry 放进 Graph value。失败或异常时没有成功 output，因此共享 Hook 不运行。

#### Authorize

```text
hook_result = exact(values["hook_result"], HookResult)
envelope = admit_hook_result_and_get_stage(hook_result, RESOLVE)
input = exact(envelope.payload.authorization, AuthorizationInput)
if input.phase is InitialAuthorization:
  ref = await authorize_port.request_authorization(input.phase.resolved)
  check ref.pairing == input.phase.resolved.request.pairing
  payload = admit_interrupt_payload(authorize_port.encode_interrupt(ref))
  return Graph.interrupt(payload)
if input.phase is ResumedAuthorization:
  if input.phase.decision is Deny:
    return Graph.failure(failure_reason.value)
  invocation = AuthorizedInvocation(input.phase.resolved)
  next_envelope = ActHookEnvelope(
      AUTHORIZE,
      EXECUTE,
      AuthorizeStageValue(invocation),
      envelope.hook_state,
  )
  return Graph.success(Graph.values(
      hook_request=HookRequest(next_envelope, envelope.hook_state)
  ))
```

首次只能 request + interrupt；恢复不再次调用 `request_authorization()`。Allow/Deny 是
无字段 closed class。Deny 不产生 `hook_request`，所以 route/Hook/Execute/Settle 都不运行。

#### Execute

```text
hook_result = exact(values["hook_result"], HookResult)
envelope = admit_hook_result_and_get_stage(hook_result, AUTHORIZE)
invocation = exact(envelope.payload.invocation, AuthorizedInvocation)
outcome = await execute_port.execute(invocation)
if ToolExecutionResult:
  check outcome.identity == invocation's resolved identity
  next_envelope = ActHookEnvelope(
      EXECUTE, SETTLE, ExecuteStageValue(outcome), envelope.hook_state
  )
  return Graph.success(Graph.values(
      hook_request=HookRequest(next_envelope, envelope.hook_state)
  ))
if ExecutionStopped:
  return Graph.failure(outcome.reason.value)
```

`ExecutePort.execute()` 是唯一结果生产入口。业务失败/unknown 仍是
`ToolExecutionResult`，按正常路径进入 shared Hook -> route -> Settle；调用前失效、普通
异常和取消不产生成功 envelope。

#### Settle

```text
hook_result = exact(values["hook_result"], HookResult)
envelope = admit_hook_result_and_get_stage(hook_result, EXECUTE)
execution = exact(envelope.payload.result, ToolExecutionResult)
projection = await settlement_port.project(execution)
check projection.source_identity == execution.identity
written = await exchange_writer.write(ToolExchangeWriteRequest(projection))
settled = SettledActResult(projection, written.receipt)
next_envelope = ActHookEnvelope(
    SETTLE, END, SettleStageValue(settled), envelope.hook_state
)
return Graph.success(Graph.values(
    hook_request=HookRequest(next_envelope, envelope.hook_state)
))
```

Settle 的业务输入只有 Execute 产生并经 shared Hook 返回的
`ToolExecutionResult`；`hook_state` 由同一 envelope 沿 Hook/route typed plumbing 传递，不能
形成第二个结果输入。`SettlementPort` 的 projection 必须回显 execution identity；writer
只收到一个 `ToolExchangeWriteRequest(projection)`。共享 Hook 在处理 Settle envelope 时不能
改变已写入的 `SettledActResult`，只能产生 opaque command。

### 6.4 Hook owner contract

共享 Hook 的 `HookPayloadAdmission`/Invocation owner 必须对每次 P1/P2/P3 返回：

- exact `ActHookEnvelope` 和共享 `HookStateProjection`；
- `HookRequest.state` 与 envelope 的 `hook_state` exact 相等并沿四阶段保持；
- `stage`、payload identity、pairing、binding、digest 保持；
- `next` 仍是该 stage 的固定下一步；
- `ActHookCommand` 为 concrete immutable class，commands 顺序保持；
- Settle stage 的 `SettledActResult` projection/receipt 逐值不变。

Act 不反射读取这些 generic 参数、不复制 Hook runner、不累计或 apply commands。Hook 自身
失败、异常或取消沿 Graph 边界传播，route 不运行。

## 7. 状态、恢复和提交

Act 不定义 `ActState`。以下事实全部由 `GraphRunState`/execution owner 管理：

- 当前 activation、scope、node position 和实际 predecessor cause；
- shared Hook 与 route 的执行 publication；
- Authorize interrupted/awaiting 状态、interrupt identity 和 resume admission；
- 节点 success/failure/cancellation、reducer candidate 和 optional commit；
- nested Act 的 parent/child projection。

v1 只承诺进程内 Graph 执行。`commit=None` 只代表 reducer successor，不代表持久化。若
writer 已确认但随后 Hook、Graph settlement 或 commit 失败，Act 不重跑 Execute，也不提供
Settle-only recovery API；外部 persistence/Failover owner 后续在统一 Graph 边界处理。

恢复 action 必须提交给持有 awaiting result 的 root Graph。重复 action、旧 state、错误
scope/node/interrupt/codec 由 Graph 拒绝；Act helper 不复制这些规则。

## 8. 包结构

```text
src/mote_kernel/act/
├── __init__.py           # 只导出 ActNode
├── node.py               # ActNode assembly（shared Hook + route）
├── resolve.py            # ResolveNode
├── authorize.py          # AuthorizeNode
├── execute.py            # ExecuteNode
├── settle.py             # SettleNode
├── route.py              # RouteAfterHook
├── contract.py           # Act DTO、HookGraphValue family envelope、closed variants
├── identity.py           # pairing、slot、stage/route identity
├── admission.py          # 固定 outer/identity/size/route admission
└── port.py               # Resolve/Authorize/Execute/Settlement/Writer Protocol
```

不创建 `ActState`、`ActRunner`、`ActExecutor`、`utils/common/shared/helpers` 或第二个
recovery path。Act 不再有 `tool_use` 或阶段子包；公共图入口只从 `mote_kernel.act`
暴露 `ActNode`，其余模块是按职责组织的内部契约，execution 内部 result/request 类型不重新导出。

## 9. 分阶段实施

### Phase 0：契约和 carrier

1. 实现 `HookGraphValue` family 下的 identity、opaque wrapper、Act DTO、closed variants；
2. 实现 `ActHookStage`、`ActHookRoute`、四个 `ActStageValue`、`ActHookEnvelope`；
3. 实现 `ActPayloadAdmission` 固定方法和上限；
4. 实现五个 Port Protocol，冻结 `codec_id: str`/`codec_version: int`；
5. 实现一个共享 Hook 的 concrete `HookPayloadAdmission`/command/state test double。

### Phase 1：六节点 assembly

1. 实现四个业务 callable、一个 `RouteAfterHook` 和一个真实 nested `HookNode`；
2. 按第 6.2 节组装四条到 Hook 的 direct edge、Hook->route 和 conditional route edges；
3. 验证 `Graph.node_output("hook_request")` 的多前驱 exact descriptor、
   `Graph.node_output("hook_result")` 的 route predecessor binding 和同一
   `Graph[HookGraphValue]` universe；
4. assembly 前校验一个且仅一个 Hook，slot 为 `hook/AFTER_NODE`，并只安装一次 resume
   codec；
5. 验证第一次 compile 后 Graph mutation guard 生效。

### Phase 2：Authorize interrupt/resume

1. 首次 Resolve -> shared Hook -> route -> Authorize 只调用一次
   `request_authorization()` 并产生 `Graph.interrupt(bytes)`；
2. helper 从公开 awaiting 字段投影 immutable interrupt DTO；
3. Allow 恢复只执行 Authorize node 一次，不重复 request，随后经 shared Hook/route 调 Execute；
4. Deny 先由外部 owner 配对写 ToolResult，再 Graph failure/stop；Hook/Execute/Settle 不调用；
5. 重复 resume、旧 state、wrong scope/interrupt/codec 交给 Graph admission 拒绝。

### Phase 3：Execute 和唯一结果

1. 证明 Execute 是唯一 `ToolExecutionResult` producer；
2. 验证 identity、binding、arguments digest mismatch 在 shared Hook 前失败；
3. success、business failure、unknown 均经 shared Hook/route 进入 Settle；
4. cancellation/ordinary exception 不伪装成 Deny 或成功。

### Phase 4：Settle、writer 和 Failover 接入

1. 验证 Settle 只消费 Execute result，project/write 顺序固定；
2. 验证 source identity pairing 一致、writer receipt 形成 `SettledActResult`；
3. 验证 Settle envelope 经过同一个 Hook 后 route 到 END，Hook value pass-through；
4. 验证启用故障恢复的 concrete Port 都经过 Port-level Failover；证明其重试不增加业务节点、Hook 或 Execute
   activation；
5. 验证 Graph commit 失败不触发隐藏重跑。

### Phase 5：统一持久化（后续）

由统一 persistence owner 扩展现有 `Graph.Commit`/`GraphRunState`；不在 Act 包新增 store、
handoff 或恢复入口。

## 10. 测试矩阵

### 10.1 Assembly 和类型

- 缺任一 required Port、writer、admission 或 shared Hook 时 assembly 失败；
- Hook 必须是一个真实 `HookNode`，slot 恰为 `hook/AFTER_NODE`；四个业务节点不得各自传入 Hook；
- Act、Hook 和 parent graph 都使用 `Graph[HookGraphValue]`；拒绝 `ActGraphValue`、`object`、`Any`；
- 四个业务节点的 `hook_request` output exact 同类；compiler 接受多前驱 predecessor binding；
- route 的 `hook_result` output exact 同类，业务节点只能从 route predecessor 读取；
- 编译后 mutation 被 Graph 拒绝。

### 10.2 正常顺序和结果归属

- Resolve -> Hook -> route -> Authorize -> Hook -> route -> Execute -> Hook -> route -> Settle -> Hook -> route -> END；
- shared Hook 每个成功业务 activation 恰好执行一次，四阶段不创建四个 Hook 实例；
- Route 只根据 envelope 的 closed route 选择下一目标，非法跳跃失败；
- ExecutePort 调用一次并是唯一 ToolExecutionResult producer；Settle 不查询/执行工具；
- Settle writer 成功后终端 graph output 是 route 传递的完整 `HookResult`；
- Hook commands 不被 Act 累计、解释或 apply。

### 10.3 分支、停止和恢复

- Resolve failure：Hook/Authorize/Execute/Settle 均不调用；
- 初次 Authorize：request 一次、interrupt 一次、Execute 为零；
- Allow：resume 不重复 request，Execute 一次；
- Deny：外部先配对写 ToolResult，Graph failure/stop，shared Hook、Execute、Settle 均为零；
- 重复/旧 resume 由 Graph rejection 处理，不由 Port 预判；
- cancellation 传播，不能转成 Deny；
- nested Act 的 action 提交 root Graph，scope 保持不变。

### 10.4 Settle/Failover/commit

- projection source identity 与 execution identity 不一致时 writer 不调用；
- writer 失败由其 Port-level Failover 在同一 request 上重试，不重跑 Execute；
- Settle 成功后 shared Hook 修改 `SettledActResult` 时 Graph failure，不重写/重执行；
- commit candidate 未确认时不宣布 Act 完成；
- v1 不测试进程重启恢复、跨进程去重或 exactly-once。

### 10.5 模型可见内容

- Kernel 不生成或断言 ToolResult 文本、拒绝文案、错误码和语言；
- protocol/presentation owner 的集成测试证明所有最终模型内容为英文并正确 pairing。

## 11. 验收清单

- [ ] 文档和代码只出现一个 shared Hook 节点，未保留 per-stage Hook 参数、别名或边；
- [ ] `Graph[HookGraphValue]` 是 Act/parent/nested 的共同 carrier；
- [ ] 四个业务节点成功后都以同名 `hook_request` 汇入 Hook；Hook 后统一进入 route；
- [ ] route 以 `ActHookRoute` 驱动 ordinary conditional edge，不能绕过 Hook；
- [ ] Execute 唯一产生工具结果，Settle 只消费、投影、写入；
- [ ] Deny 的 ToolResult 由外部 owner 配对写入，Act 只 Graph stop；
- [ ] interrupt DTO 是 Act-owned immutable projection，codec 字段为 `str`/`int`；
- [ ] 每个启用故障恢复的 concrete Port 均先经过 Port-level Failover；Failover 不包整图、不复制节点、不改变 Graph state owner；
- [ ] 无 Act 专用持久化、runner、state、writer 对账状态机或模型文案；
- [ ] `git diff --check`、文档 hooks、`make check`（若工作树已有无关失败则记录）均已执行。

## 12. 参考源码

- [`src/mote_kernel/execution/facade.py`](../src/mote_kernel/execution/facade.py)：Graph builder、nested graph、前驱 output、conditional edge、interrupt/resume；
- [`src/mote_kernel/execution/graph/compiler.py`](../src/mote_kernel/execution/graph/compiler.py)：多前驱 `PredecessorOutputRef` 的 exact descriptor admission；
- [`src/mote_kernel/hooks/node.py`](../src/mote_kernel/hooks/node.py)：现有 typed `HookNode` 与 `Plan → P1 → P2 → P3`；
- [`src/mote_kernel/hooks/contract.py`](../src/mote_kernel/hooks/contract.py)：`HookGraphValue`、`HookRequest`、`HookResult`、payload admission；
- [`src/mote_kernel/failover/assembly.py`](../src/mote_kernel/failover/assembly.py)：共享 Hook、多个互斥前驱汇入、Hook 后普通 route 的现成组图形状；
- [`docs/failover-design.zh-CN.md`](./failover-design.zh-CN.md)：共享 Hook/route 设计依据；
- [`docs/graph-delayed-loop-implementation-plan.zh-CN.md`](./graph-delayed-loop-implementation-plan.zh-CN.md)：前驱 output 汇入共享节点的 Graph 规则；
- [`docs/think-graph-implementation-plan.zh-CN.md`](./think-graph-implementation-plan.zh-CN.md)：`HookGraphValue` family 与唯一 Graph/State/Commit 边界。
