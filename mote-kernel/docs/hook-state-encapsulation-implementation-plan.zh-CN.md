# Hook 状态封装改造实施方案

状态：**待实施，设计已冻结**

规范来源：`docs/hook-state-encapsulation-requirements.zh-CN.md`

本文是需求的冻结实施方案，不保留编码阶段再决定的架构选项。目标是在现有
`execution.Graph` 和 `GraphRunState` 内完成共享 Hook 的状态封装，不新增 runner、state
model、registry、缓存、兼容构造或第二条执行路径。

本版吸收审核中发现的全部阻断问题：

1. 不为已经消失的 Hook state 转发需求增加原始 request binding。
2. 不把“外层 DTO 没有 state 字段”当作不可达证明。
3. 冻结 Act 的最终 facts DTO、raw/canonical arguments 关系和替代校验。
4. 无法运行门禁时只能交接为“待验证/阻塞”，不能宣称完成。
5. Think 不增加只为 command projection materialize 服务的终端节点，最终输出契约固定为
   已完成 P3 owner admission 的 HookResult。
6. P1/P2/P3 每次返回后都在进入下一 priority 前执行 owner typed transition admission。
7. Observe 由四个 producer-owned payload adapter 对 concrete payload 的传递闭包负责准入，
   不依赖 concrete class exact check 或反射递归。
8. Act 的无状态 `ActRequest` 是明确允许的 facts 例外；四个 Hook DTO 继续继承
   `HookGraphValue`。

## 1. 最终架构裁决

### 1.1 选择严格的闭合 projection 边界

本需求采用严格版本的方案 B：

> 共享 Hook 只能收到领域 owner 产生的显式、无状态、闭合 projection；原始领域 request、
> 领域 state 和任意开放业务对象不能直接进入 Hook-visible value。

这里的“闭合”指从 `HookRequest.value` 沿字段访问能够到达的完整对象闭包，而不只是根 DTO
的直接字段。闭包中的字段只能是：

- 领域 owner 定义的具体 immutable projection DTO；
- 已经定义清楚语义、只携带值或 bytes 的 identity/wire wrapper；
- immutable scalar 或由上述值组成的 tuple；
- 其他已经由同一 owner admission 的闭合 DTO。

以下对象不能进入 Hook-visible value：

- `ThinkRequest`、`ObserveRequest`、任何包含领域 state 的 activation request，或任何包含
  它们的 DTO；最终无状态 facts 形状的 `ActRequest` 是本规则的明确例外，可作为
  `ResolvedInvocation.request` 的 Hook projection；
- `hook_state`、state projection、runtime state、Graph snapshot 或它们的引用；
- `object`、`Any`、`unknown`、裸 `dict` 或开放的任意业务对象；
- callable、Port、registry、credential、异常对象、mutable client 或隐藏 owner field。

Act 现有的 `OpaqueArguments`、`OpaqueDefinitionReference` 等 wrapper 只有在其内部仍是
bytes/identity 值、没有 Python 对象引用且不编码领域 state 时，才属于允许的 wire wrapper。
“opaque”不等于“任意对象都可以穿过边界”。

### 1.2 保证范围

本方案保证的是：

- Hook Invocation 的 request/value 字段闭包无法取得 Think、Act、Observe 的领域 state；
- Hook 返回的 value 也不能把领域 state 重新塞回领域 Hook DTO；
- 状态只能由领域节点从明确的 Graph input 或领域 Port 输入取得。

本方案不承诺在同一 Python 进程中沙箱化恶意 Hook。若 Hook 通过全局变量、闭包、模块或
其他进程内旁路主动读取任意内存，那是进程隔离问题，不是 DTO contract 能解决的问题，也
不在本需求范围内。

### 1.3 对 pi 实现的取舍

`pi` 值得采用的部分是：

- 每个 Hook/event 自己定义 payload 和 result，而不是通过一个公共 state 字段传递所有事实；
- Harness 的运行状态留在 owner 内部，Hook 只收到当前事件的输入；
- Hook result 是事件专属 patch，而不是整个运行状态。

但 `pi` 的 `before_provider_payload.payload: unknown` 仍允许任意 payload 进入 Hook。因此
`pi` 不能证明“payload 内部状态不可达”。本仓库只采用它的 ownership/patch 思路，并将
Hook-visible payload 收紧为本方案定义的闭合 projection。

## 2. 共享 Hook contract

### 2.1 冻结的 DTO 形状

```python
@dataclass(frozen=True, slots=True)
class HookRequest(HookGraphValue, Generic[ValueT]):
    value: ValueT
    node_id: GraphNodeId | None = None


@dataclass(frozen=True, slots=True)
class HookInvocationRequest(HookGraphValue, Generic[PriorityConfigT, ValueT]):
    config: PriorityConfigT
    request: HookRequest[ValueT]


@dataclass(frozen=True, slots=True)
class HookStageResult(HookGraphValue, Generic[ValueT, CommandT]):
    value: ValueT
    commands: tuple[CommandT, ...] = ()


@dataclass(frozen=True, slots=True)
class HookResult(HookGraphValue, Generic[ValueT, CommandT]):
    value: ValueT
    commands: tuple[CommandT, ...] = ()
    node_id: GraphNodeId | None = None
```

`HookGraphValue` 是现有 Graph 的 Hook nominal base，四个 DTO 必须继续继承它；本需求只
删除 state 泛型和字段，不改变 Graph[HookGraphValue] 的边界。共享 contract 永远不声明
`StateT`、`state` 或 `hook_state`。`HookPayloadAdmission` 只负责 config、priority config、
value、command、node identity 和 owner 提供的 transition admission；不再有 `state_type`。

P1/P2/P3 的唯一传递形状是：

```text
HookRequest(original_or_previous_result.value, original.node_id)
```

`_HookProgress` 只保存当前 `HookRequest`、已捕获的 Plan/config 和 ordered commands。
共享 Hook 不保存任何领域 state，也不从 value 的字段闭包中寻找 state。

### 2.2 通用 transition 与领域 admission

通用 Hook transition 只负责：

- exact value type；
- exact command type 和 tuple 形状；
- `node_id`、descriptor、route 和 Invocation boundary；
- 在每一个 priority Invocation 返回后，调用领域 owner 提供的 typed transition admission。

领域 admission 负责自己的业务不变量，例如 Act 的 pairing/identity/evidence、Observe 的
cursor/receipt/boundary、Think 的 step kind 和 projection type。共享 Hook 不复制这些规则，
也不恢复通用 state equality 校验。

`HookPort.execute()` 的唯一顺序固定为：

```text
admit invocation request
  -> invoke current priority
  -> shared exact admit HookStageResult
  -> owner transition_admission(request, stage_result)
  -> only then publish to the next priority or construct final HookResult
```

因此 P1 返回的 candidate 必须先通过 Think/Observe/Act owner admission，才允许进入 P2；
P2 同理，P3 通过后才允许成为最终 `HookResult`。共享 Hook 只调用注入的
`HookTransitionAdmission`，不解释 projection 字段，也不自行遍历 projection。adapter
准入失败时当前 Hook activation 立即失败，不产生后续 priority、最终 result 或 command
settlement。

该注入点的 state-free typed 形状固定为：

```python
@runtime_checkable
class HookTransitionAdmission(Protocol[ValueT, CommandT]):
    def admit_transition(
        self,
        request: HookRequest[ValueT],
        result: HookStageResult[ValueT, CommandT],
        /,
    ) -> None: ...
```

Think/Observe/Act 各自实现这个 protocol；实现内部可以调用本领域 adapter，但不得把
`StateT`、state equality 或领域 state 重新放回 Hook contract。

在本需求覆盖的 Think、Act、Observe 三个 domain Hook 中，
`HookPayloadAdmission.transition_admission` 是 required capability，不能以 `None` 绕过；
缺失时在 Hook/领域 assembly 阶段失败。只有不携带领域 projection 的通用测试 fixture 才可
使用无 owner admission 的独立 Hook contract，不属于三领域迁移路径。

### 2.3 projection owner 责任

每个需要把 Port value 送入共享 Hook 的领域，必须注入一个窄的 typed projection adapter。
该 adapter 不是 serializer、registry、缓存或第二个执行器；它只负责一类 Port value 与一类
Hook projection 之间的双向转换和候选值准入。

#### 2.3.1 唯一 SPI

通用 SPI 定义在 `src/mote_kernel/hooks/contract.py` 的内部 contract 区域，不加入
`src/mote_kernel/hooks/__init__.py`，也不作为包级公共 API 导出。实现使用 Python 3.11 的
`TypeVar`，不得用 `Any`、`object` 或 `unknown` 放宽关联：

```python
SourceT = TypeVar("SourceT")
ProjectionT = TypeVar("ProjectionT", bound=HookGraphValue)


@runtime_checkable
class HookProjectionAdapter(Protocol[SourceT, ProjectionT]):
    source_type: type[SourceT]
    projection_type: type[ProjectionT]

    def project(self, source: SourceT, /) -> ProjectionT: ...

    def admit(
        self,
        original: ProjectionT,
        candidate: ProjectionT,
        /,
    ) -> ProjectionT: ...

    def materialize(self, projection: ProjectionT, /) -> SourceT: ...
```

`project()`、`admit()`、`materialize()` 都是纯函数语义：不写 Graph state、不修改输入、不
读取全局状态、不通过闭包补字段。`admit()` 成功时返回已准入的 `candidate`；它可以返回同
值的 immutable canonical DTO，但不能偷偷加入未在 projection 形状中声明的字段。需要规范化
的值必须在 `project()` 前由 Port owner 规范化，不能在 admission 中隐式改变事实。

这里选择不传 `baseline`。`materialize()` 必须能够仅凭已准入 projection 构造下游 Port
需要的 source；否则说明 projection 丢失了下游业务事实，或下游仍依赖隐藏 state，均不得
通过增加 `baseline`、原始 request 或闭包引用来掩盖。这样可以避免把中断前的 Graph input
误当成恢复后的 Hook result，也避免 Observe 的 S0/S1 混用。

#### 2.3.2 关联类型、装配和运行时检查

Python 没有真正的 associated type，因此 adapter 必须同时声明 `source_type` 和
`projection_type`，并由领域 assembly 固定 nominal 关联。类型参数由 Pyright 检查，外层
source/projection class 由运行时 exact class 检查，projection 内部字段由具体 owner admission
检查。不能用 `cast()` 把错误 adapter 伪装成正确类型。

Think 的五个 adapter 由 Graph builder 显式注入到对应节点，不使用发现式 registry 或统一
adapter map。装配依赖关系固定为：

```text
PromptNode(
    prompt_port,
    prompt_projection: HookProjectionAdapter[PromptFrame[...], PromptHookProjection],
)
ContextNode(
    context_port,
    prompt_projection: HookProjectionAdapter[PromptFrame[...], PromptHookProjection],
    context_projection: HookProjectionAdapter[ContextFrame[...], ContextHookProjection],
)
CompactNode(
    compact_port,
    prompt_projection,
    context_projection,
    compacted_projection: HookProjectionAdapter[CompactedContext[...], CompactedHookProjection],
)
InferenceNode(
    inference_port,
    prompt_projection,
    compacted_projection,
    inference_projection: HookProjectionAdapter[InferenceResult[...], InferenceHookProjection],
)
CommandNode(
    command_port,
    inference_projection,
    command_projection: HookProjectionAdapter[ThinkCoreResult[...], CommandHookProjection],
)
```

上面的 `...` 只代表已经由当前 Think graph 组合点绑定的具体业务类型参数；它不是开放
payload 的位置。实现前必须为每个组合点形成完整的 Pyright 类型实例。每个节点构造时必须
按以下顺序验证 adapter：

1. adapter 存在并满足 `HookProjectionAdapter` 的结构要求；
2. `source_type` 与节点声明的 source 外层 class exact 相同；
3. `projection_type` 与该节点的 Hook projection class exact 相同；
4. `project`、`admit`、`materialize` 均可调用；
5. Graph `add_node()` 前完成一次 assembly admission，不通过则不向父 Graph 注册任何节点。

每个 priority 的 adapter admission 必须发生在该 priority 返回之后、下一 priority 启动之前；
这不是下游业务节点的事后检查。共享 `HookPort` 通过已经存在的
`HookPayloadAdmission.transition_admission` 调用 owner 提供的 typed transition admission，
但不理解 projection 字段：

```text
source
  -> adapter.project(source)
  -> adapter.admit(projection, projection)
  -> HookRequest.value
  -> P1 Invocation
  -> shared exact admit HookStageResult
  -> adapter.admit(original_projection, candidate_projection)
  -> P2 Invocation (only after P1 admission)
  -> shared exact admit HookStageResult
  -> adapter.admit(original_projection, candidate_projection)
  -> P3 Invocation (only after P2 admission)
  -> shared exact admit HookStageResult
  -> adapter.admit(original_projection, candidate_projection)
  -> HookResult
  -> next business node: adapter.materialize(admitted_projection)
```

跨阶段携带的 projection 在产生它的 Hook activation 中完成 admission；下一节点只使用已
准入 projection 的 `materialize()` 构造真实 Port input。每个 priority 的
`original_projection` 是该 priority 收到的 `HookRequest.value` 中的 projection，candidate
是该 priority 返回的同一 nominal stage 中的 projection；两者都由 owner 的 typed transition
admission 逐字段比较。任何 conversion 返回错误外层类型、嵌套类型或 owner 不变量的情况，
均转为对应领域的 `ThinkContractError` 或 `ObserveContractError`；Port 自身调用失败保持
原有 Port/Invocation 异常边界。

具体 projection DTO 和 adapter 实现由领域 owner 放在 `src/mote_kernel/think/projection.py`
等领域模块中；Observe 的 concrete payload 继续由 `src/mote_kernel/observe/contract.py`
和 producer adapter 所有。owner transition admission 作为 typed
`HookTransitionAdmission` 注入 `HookPayloadAdmission`，由 `HookPort` 在每个 priority 返回后
调用；不得把字段语义下沉到共享 `HookNode`，也不得新增通用 `projection_utils`、反射遍历
或 `unknown` 递归检查器。

如果一个业务对象无法被转换为闭合、无状态 projection，则它不能进入共享 Hook。不能通过
在文档中称它为“opaque”来绕过边界。

## 3. 状态消费者与 Graph 接线

request binding 只能因为节点的真实业务输入而存在，不能因为“需要把旧 Hook state 继续
传下去”而存在。

| 领域/节点 | 普通执行的真实输入 | state 是否有真实消费者 | Hook-visible 输入 | resume override |
| --- | --- | --- | --- | --- |
| Think `prompt` | `ThinkRequest.payload` 供 `PromptPort` 生成 prompt | `hook_state` 不用于 Hook 转发 | 无状态 `PromptStep` projection | 使用现有 Graph input |
| Think `context` | 完整 `ThinkRequest` 供 `ContextPort` 使用 | 是，Port request contract 真实消费 | 无状态 `ContextStep` projection | 不新增 binding |
| Think `compact` | 上一 Hook result 的 Context projection | 否 | 无状态 `CompactStep` projection | 不新增原始 request |
| Think `inference` | 上一 Hook result 的 Compact projection | 否 | 无状态 `InferenceStep` projection | 不新增原始 request |
| Think `command` | 上一 Hook result 的 Inference projection | 否 | 无状态 `CommandStep` projection | 不新增原始 request |
| Act `resolve` | 原始 `ActRequest` 供 `ResolvePort` 使用 | 只消费 request facts | 无状态 Resolve stage value | facts/authorization ref |
| Act `authorize` | Resolve Hook result | 否 | 无状态 Authorize stage value | 不绑定原始 request |
| Act `execute` | Authorize Hook result | 否 | 无状态 Execute stage value | 不绑定原始 request |
| Act `settle` | Execute Hook result | 否 | 无状态 Settle stage value | 不绑定原始 request |
| Observe `get_observation` | `ObserveRequest.cursor` | 只消费 cursor | 无状态 observation frame | resume request 只重建 cursor |
| Observe `write_observation` | Get/Write Hook result | 否 | 无状态 Write observation frame | 不绑定原始 request |

`src/mote_kernel/execution/engine/resume_input.py` 的整帧 override 语义不因本需求改变。
resume override 必须继续显式提供该节点要求的完整输入；不能期待 Graph 自动把原始 request
字段与 override 混合补齐。

## 4. 领域最终 DTO 形状

### 4.1 Think

Think 保留 `ThinkRequest(payload, hook_state)` 作为 Graph activation input，因为
`ContextPort` 的真实 request contract 需要完整 `ThinkRequest`。但 Hook-visible frame 不再
携带 state，也不直接复用带开放泛型 payload 的 Port DTO。

ThinkNode 的装配签名必须显式接收五个 adapter：

```text
prompt_projection: HookProjectionAdapter[PromptFrame[...], PromptHookProjection]
context_projection: HookProjectionAdapter[ContextFrame[...], ContextHookProjection]
compacted_projection: HookProjectionAdapter[CompactedContext[...], CompactedHookProjection]
inference_projection: HookProjectionAdapter[InferenceResult[...], InferenceHookProjection]
command_projection: HookProjectionAdapter[ThinkCoreResult[...], CommandHookProjection]
```

省略号只表示现有 Port 的业务泛型，不表示可以使用 `object`、`Any` 或 `unknown`。五个
adapter 的 source/projection 类型由组合点一次性绑定，缺失或不匹配时在构建 Graph 前失败。

五种 projection 的承载形状也在组合前冻结，不允许退化成一个开放的 `payload` 字段：

| projection | 具体承载字段 | source 对应字段 | 字段规则 |
| --- | --- | --- | --- |
| `PromptHookProjection` | `system`、`placeholder`、`user` | `PromptFrame` 的三个 prompt 组件 | 每个组件使用 owner 定义的 concrete projection 类型；本阶段没有隐藏 evidence 字段；字段可按业务需要被 Hook 修改 |
| `ContextHookProjection` | `snapshot` | `ContextFrame.snapshot` | `snapshot` 是 owner 定义的 concrete closed DTO，不得是 `ContextFrame`、`ThinkRequest` 或开放泛型 |
| `CompactedHookProjection` | `snapshot`、`token_count` | `CompactedContext.snapshot`、`token_count` | `snapshot` 是 concrete closed DTO；`token_count` 保持非负整数约束 |
| `InferenceHookProjection` | `output` | `InferenceResult.output` | `output` 是 provider-neutral concrete closed DTO，不得携带 model client、response object 或 state |
| `CommandHookProjection` | `command` | `ThinkCoreResult.command` | `command` 是 owner 定义的 concrete closed DTO，不得携带 callable、Port 或 runtime handle |

上述 concrete 类型在 `src/mote_kernel/think/projection.py` 命名并实现；如果某字段本身是
`str`、`bytes`、整数、枚举或由闭合值组成的 tuple，也必须在该表对应的 DTO 字段中以明确
类型出现，不能以开放泛型占位。每个 DTO 都是 `frozen=True, slots=True`，并在构造时拒绝
缺失值、可变容器和非 owner 类型。这里冻结的是承载形状和边界规则，不把具体业务字段
复制到共享 Kernel。

最终 Hook-visible 根 DTO 为：

```python
@dataclass(frozen=True, slots=True)
class ThinkFrame(HookGraphValue, Generic[ThinkHookStepT]):
    step: ThinkHookStepT
```

`ThinkHookStepT` 是本模块封闭的五阶段 nominal family：

| Hook step | Hook-visible 字段 | 产生位置 | Hook 返回后使用位置 |
| --- | --- | --- | --- |
| `PromptStep` | `PromptHookProjection` | `PromptNode` 调用 `PromptPort` 后 | `ContextNode` 重建 context 输入 |
| `ContextStep` | `PromptHookProjection`、`ContextHookProjection` | `ContextNode` 调用 `ContextPort` 后 | `CompactNode` 重建 compact 输入 |
| `CompactStep` | prompt/context/`CompactedHookProjection` | `CompactNode` 调用 `CompactPort` 后 | `InferenceNode` 重建 inference 输入 |
| `InferenceStep` | prompt/compacted/`InferenceHookProjection` | `InferenceNode` 调用 `InferencePort` 后 | `CommandNode` 重建 command 输入 |
| `CommandStep` | prompt/compacted/inference/`CommandHookProjection` | `CommandNode` 调用 `CommandPort` 后 | Think parent output |

这些 projection 不是包裹任意对象的 `payload: object` 容器。每个 projection 的字段只能是
该 owner 定义的 closed DTO、scalar、bytes 或 tuple；不能有 `ThinkRequest`、state 或开放
业务对象的字段或引用。

Port-facing DTO 与 Hook-facing projection 的职责固定如下：

| DTO 类别 | 是否可进入 Hook | 责任 |
| --- | --- | --- |
| `ThinkRequest` | 否 | Graph input；保存 Think state，并供真实需要它的 Port 使用 |
| `ContextRequest` | 否 | ContextPort 内部 request；可以包含完整 `ThinkRequest` |
| `CompactRequest`/`InferenceRequest` | 否 | 对应 Port 的内部 request |
| `PromptFrame`/`ContextFrame`/`CompactedContext`/`InferenceResult`/`ThinkCoreResult` | 否 | 只能作为 adapter 的 source；无论其当前字段是否恰好 closed，都不能直接作为 Hook value |
| `ThinkFrame` | 是 | 只携带五阶段 state-free step projection |

Think 的字段规则：

| 字段 | 唯一 owner | 产生位置 | 校验位置 | 允许变化 |
| --- | --- | --- | --- | --- |
| Think `hook_state` | Think Graph input owner | activation 输入 | `ThinkRequest`/Think assembly | 只由真实 Port 读取，不进入 Hook frame |
| prompt projection | Prompt owner | `PromptPort` 输出后 | `prompt_projection.admit` | 三个 concrete prompt 组件可按业务修改；类型、必填约束和闭合性不变 |
| context projection | Context owner | `ContextPort` 输出后 | `context_projection.admit` | `snapshot` 的 owner 声明业务字段可修改；类型、必填约束和闭合性不变 |
| compacted projection | Compact owner | `CompactPort` 输出后 | `compacted_projection.admit` | `snapshot` 可按业务修改；`token_count` 仍须为非负整数 |
| inference projection | Inference owner | `InferencePort` 输出后 | `inference_projection.admit` | provider-neutral output 的 owner 声明业务字段可修改；不得改变为 provider object |
| command projection | Command owner | `CommandPort` 输出后 | `command_projection.admit` | command 的 owner 声明业务字段可修改；不得增加 callable、Port 或 runtime handle |

若某个 Port 的原始输出含有不可投影的 runtime handle、state 或任意对象，必须在 Port owner
边界完成转换；不能让该输出原样进入 `ThinkFrame`。

Think 的逐阶段调用链固定为：

```text
prompt:
  ThinkRequest
    -> PromptPort.load_*(request.payload)
    -> PromptFrame(source)
    -> prompt_projection.project/admit
    -> HookRequest(ThinkFrame(PromptStep(projection)))
    -> Hook(P1/P2/P3 owner transition admission)
    -> HookResult(accepted PromptHookProjection)

context:
  HookResult(PromptStep(prompt_projection))
    -> prompt_projection.materialize(accepted_prompt_projection)
    -> ContextRequest(ThinkRequest, PromptFrame)
    -> ContextPort.load_context
    -> ContextFrame(source)
    -> context_projection.project/admit
    -> HookRequest(ThinkFrame(ContextStep(projection)))
    -> Hook(P1/P2/P3 owner transition admission)
    -> HookResult(accepted ContextHookProjection)

compact:
  HookResult(ContextStep(prompt_projection, context_projection))
    -> prompt_projection.materialize(accepted_prompt_projection)
    -> context_projection.materialize(accepted_context_projection)
    -> CompactRequest(PromptFrame, ContextFrame)
    -> CompactPort.compact
    -> CompactedContext(source)
    -> compacted_projection.project/admit
    -> HookRequest(ThinkFrame(CompactStep(projection)))
    -> Hook(P1/P2/P3 owner transition admission)
    -> HookResult(accepted CompactedHookProjection)

inference:
  HookResult(CompactStep(prompt_projection, context_projection, compacted_projection))
    -> prompt_projection.materialize(accepted_prompt_projection)
    -> compacted_projection.materialize(accepted_compacted_projection)
    -> InferenceRequest(PromptFrame, CompactedContext, ModelBinding)
    -> InferencePort.infer
    -> InferenceResult(source)
    -> inference_projection.project/admit
    -> HookRequest(ThinkFrame(InferenceStep(projection)))
    -> Hook(P1/P2/P3 owner transition admission)
    -> HookResult(accepted InferenceHookProjection)

command:
  HookResult(InferenceStep(prompt_projection, compacted_projection, inference_projection))
    -> prompt_projection.materialize(accepted_prompt_projection)
    -> compacted_projection.materialize(accepted_compacted_projection)
    -> inference_projection.materialize(accepted_inference_projection)
    -> CommandPort.build_command(InferenceResult)
    -> ThinkCoreResult(source)
    -> command_projection.project/admit
    -> HookRequest(ThinkFrame(CommandStep(projection)))
    -> Hook(P1/P2/P3 owner transition admission)
    -> HookResult(accepted CommandHookProjection)
    -> parent output
```

其中 `prompt` 是第一条 Hook request 的生产者，没有前置 Hook result；`context`、`compact`、
`inference`、`command` 才是前一个 Hook activation result 的消费者。每个 Port source 都是
当前节点从 Graph input 或前一 Port 得到的内部值，不会进入共享 Hook。projection 的
materialize 不接收额外 source；任何需要额外 source 才能构造的字段都不属于当前 projection
契约，必须在进入本方案前重新划分为真实 Port input，而不是通过隐藏引用保留。

Think 接线固定为：

- `prompt` 继续接收 `ThinkRequest`，因为它需要 `request.payload`；
- `context` 继续接收完整 `ThinkRequest`，因为 `ContextPort` 的 request contract 真实需要它；
- `compact`、`inference`、`command` 不增加仅为旧 state 转发服务的原始 request binding；
- `prompt` 产生第一次 Hook request；它没有前置 Hook result；
- `context`、`compact`、`inference`、`command` 四个后续节点分别消费前一个 Hook activation
  的 result，经已完成 admission 的 projection materialize 后重建各自 Port request；
- 每个 P1/P2/P3 result 的候选 projection 都由当前 Hook 的
  `HookTransitionAdmission` 在 priority 返回边界完成 owner admission；下一个真实业务消费者
  只对已准入 projection 执行 `materialize`；共享 `HookNode` 不解释或遍历 projection；
- 每个 Port 输出在进入下一次 Hook activation 前都经过对应 adapter 的 projection；
- Think 的 P1/P2/P3 均在 priority 返回边界完成 step kind、来源 node、projection、route 和
  owner transition admission，不比较 state；
- Think 维持现有五个业务节点加一个共享 Hook 的直接拓扑；`hook -> command -> END` 不增加
  terminal reification node；`ThinkNode` 的最终输出是已完成 P3 owner admission 的
  `HookResult[ThinkFrame[CommandStep[CommandHookProjection]], HookCommand]`；
- Think 对父图的唯一结果契约就是上述 `HookResult`；父图不得把它直接解释成
  `ThinkCoreResult`。ReAct 的直接消费节点若其真实 Port contract 是 `ThinkCoreResult`，必须在
  该节点输入边界调用同一 `command_projection.materialize()`；这是父图唯一的 command
  reification 点，且只能复用 Think assembly 注入的同一个 typed adapter；Think 不为此新增
  节点，也不把未准入 projection 交给父图；
- 五阶段 route、nested output、typed materializer 和并发隔离保持不变。

### 4.2 Act

#### 删除无消费者的旧 state

当前 Act `hook_state` 只用于写入 `ActHookEnvelope`、`HookRequest.state`，以及从
`ResolvedInvocation.request` 在 authorization resume 中重新转发。没有 Resolve/Authorize/
Execute/Settle Port 的真实业务操作消费它。为避免无消费者死字段，Act 最终删除
`ActRequest.hook_state`、`HookStateProjection` 和 Act admission 中的 `hook_state_type`。
这是本需求明确的 breaking change，不新增旧构造兼容路径。

#### 冻结 request facts DTO

```python
@dataclass(frozen=True, slots=True)
class ActRequest(HookGraphValue):
    pairing: ToolPairingIdentity
    selector: ToolSelector
    arguments: OpaqueArguments
    caller: CallerIdentityRef


@dataclass(frozen=True, slots=True)
class ResolvedInvocation(ResolvePortResult):
    request: ActRequest
    definition: OpaqueDefinitionReference
    binding: ToolBindingRef
    canonical_arguments: CanonicalArguments
```

`ResolvedInvocation`、`AuthorizationInput`、`InitialAuthorization`、
`ResumedAuthorization`、`AuthorizedInvocation` 和全部 Act Hook stage value 的字段闭包
可以引用无状态的 `ActRequest` facts，但不得引用任何包含 state 的 request、Graph snapshot
或运行时 owner。

#### raw/canonical arguments 规则

- `ActRequest.arguments` 是调用方提交的原始 arguments，唯一由 `ActRequest` 持有；
- `CanonicalArguments` 是 `ResolvePort` 产生的规范化 arguments 和 digest，唯一由
  `ResolvedInvocation.canonical_arguments` 持有；
- ResolvePort 负责 raw arguments 的解析、规范化和 digest 生成；Act 不重复计算 canonical
  arguments；
- ResolveNode 逐字段确认 `resolved.request` 与 `request` 相等：`pairing`、
  `selector`、`caller`、原始 `arguments` 一个都不能变化；
- 该关系校验不比较 state，也不能用完整 `ActRequest` equality 代替。

#### Act Hook value

```python
@dataclass(frozen=True, slots=True)
class ActHookEnvelope(HookGraphValue):
    stage: ActHookStage
    payload: ActStageValue
```

四个 `ActStageValue` 继续使用封闭 nominal family：

```text
RESOLVE   -> AuthorizationInput(ResolvedInvocation facts)
AUTHORIZE -> AuthorizedInvocation(ResolvedInvocation facts)
EXECUTE   -> ToolExecutionResult(identity, outcome)
SETTLE    -> SettledActResult(projection, receipt)
```

Act 当前没有额外声明的可变业务 projection。四个阶段的 pairing、selector、caller、raw
arguments、definition、binding、canonical digest、execution identity、settlement projection
和 receipt 都是 invariant facts/evidence；Hook 只能产生符合这些 invariant 的值和
commands，伪造或替换会由 Act admission 拒绝。

`AuthorizeNode`、`ExecuteNode`、`SettleNode` 不增加原始 `ActRequest` binding。Authorize
resume 只使用 state-free `ResolvedInvocation`、`AuthorizationRequestRef` 和 decision
构造 `ResumedAuthorization`，不再从 `resolved.request.hook_state` 取 state。

### 4.3 Observe

#### 删除无消费者的旧 state

当前 Observe `hook_state` 只用于 Get/Write Hook envelope 和 `HookRequest.state` 转发；
queue、background task、config/context settlement、ACK 和 receipt 都不消费它。为避免
保留死字段，Observe 最终删除 `ObserveRequest.hook_state`、Observe state projection 及
admission 中对应的 state type。`resume_observation()` 删除 `hook_state` 参数。

最终 Graph input 为：

```python
@dataclass(frozen=True, slots=True)
class ObserveRequest(HookGraphValue):
    cursor: ObservationCursor
```

最终 Hook envelope 为：

```python
@dataclass(frozen=True, slots=True)
class ObserveHookEnvelope(HookGraphValue):
    stage: ObserveHookStage
    payload: GetObservationStageValue | WriteObservationStageValue
```

`ObserveFrame`、`ObservationBatch`、`ObservationDelivery`、各 concrete `ObservationPayload`、
`BackgroundTaskSnapshot`、receipt 和 `ObserveResult` 的传递闭包必须是 state-free。生产者有
任意对象时，先转成 `ObservationPayload` 的具体闭合 projection；不能把 producer request、
queue handle 或隐藏 state 放进 payload。

Observe 不新增整帧双向 adapter。原因是 Get Hook 修改后的 `ObservationBatch` 就是
`ConfigObservationPort`/`ContextObservationPort` 的业务输入；这里不存在需要隐藏在
projection 外的额外 Port-only 字段。闭合规则具体冻结为：

- `ObservationPayload` 只能由各 producer 提供的 concrete immutable payload class 实现；
- `ConfigObservation`、`ToolObservation`、`UserObservation`、`AssistantObservation` 的
  payload 类型由 producer-owned adapter 和 `ObservePayloadAdmission` 在 assembly 时 exact 绑定；
- payload class 的字段只能是 scalar、bytes、tuple 或其他同样 closed 的 DTO，不得有
  `ObserveRequest`、queue/Port handle、runtime state 或 `object`/`unknown` 字段；
- queue Port 的返回值已经是 `ObservationBatch` projection；provider-native raw value 必须
  在 queue/producer adapter 内部完成转换，不能由 `ObserveNode` 延迟转换；
- `ObservePayloadAdmission` 必须持有 Config、Tool、User、Assistant 四个 required
  producer-owned payload adapter；不允许使用一个覆盖所有 family 的开放 adapter；
- `ObservePayloadAdmission.admit_observation()`、`admit_batch()`、`admit_frame()` 和
  `admit_result()` 是唯一的 payload/frame/result 准入入口，失败统一抛出
  `ObserveContractError`。

#### 4.3.1 producer-owned payload adapter

Observe 不依赖“producer 自觉提供 closed class”作为证明。producer/queue owner 必须提供
以下 state-free typed adapter，并在 Observe assembly 时显式注入四个具体实例：

```python
RawT = TypeVar("RawT")
PayloadT = TypeVar("PayloadT", bound=ObservationPayload)


@runtime_checkable
class ObservationPayloadAdapter(Protocol[RawT, PayloadT]):
    raw_type: type[RawT]
    payload_type: type[PayloadT]

    def project(self, raw: RawT, /) -> PayloadT: ...

    def admit(self, payload: PayloadT, /) -> PayloadT: ...
```

四个 adapter 的装配形状固定为：

```text
ObservePayloadAdmission(
    config_payload_adapter: ObservationPayloadAdapter[ConfigRawT, ConfigPayloadT],
    tool_payload_adapter: ObservationPayloadAdapter[ToolRawT, ToolPayloadT],
    user_payload_adapter: ObservationPayloadAdapter[UserRawT, UserPayloadT],
    assistant_payload_adapter: ObservationPayloadAdapter[AssistantRawT, AssistantPayloadT],
)
```

`raw_type` 只供 producer adapter 在 provider 边界验证输入；`payload_type` 供 Observe
assembly 验证具体 payload class。`project()` 在 provider/queue owner 内把 raw value 转成
payload，`admit()` 在每次 observation、delivery、batch、frame 和 result admission 中验证
payload 的递归字段闭包。adapter 实现必须逐字段检查其已知的嵌套 DTO，不能用反射、序列化
blacklist、`object` 或递归 `unknown` 检查器替代。

assembly 必须 exact 检查四个 adapter 的 `payload_type`、方法可调用性和 family 对应关系；
缺失、错配或方法返回错误 payload type 时，在 `Graph.add_node()` 前抛出
`ObserveContractError`。adapter 的 `admit()` 若发现 payload 内嵌 `ObserveRequest`、queue
handle、runtime state 或可变容器，必须在进入 shared Hook 前失败；P1/P2/P3 的 Hook result
也必须通过同一 owner admission 后才可继续。

Observe 的两次 Hook activation 使用不同的 mutation contract，不能用一个宽泛的
`admit_result()` 规则代替：

| activation | Hook 可以改变 | 必须保持不变 | 准入顺序 |
| --- | --- | --- | --- |
| Get 后第一次 Hook | 每个 `ObservationDelivery.payload` 的 concrete observation value；因此允许改变 observation family，但候选 batch 必须仍满足完整窗口和下游 family 规则 | `delivery_id`、`cursor_before`、`cursor_after`、observation revision、frame boundary、`BackgroundTaskSnapshot` | 先 exact-admit envelope/stage，再逐 delivery 做 payload admission，最后做 batch/frame admission |
| Write 后第二次 Hook | 不允许改变 `ObserveResult` 的任何 value 字段；只允许按 shared Hook contract 透传合法 commands | receipt、delivery ids、cursor range、settlement boundary、revision、ACK reference、task snapshot、`ObservationKind` | 先 exact-admit envelope/stage，再对 settlement result 做与原值相等的 evidence admission |

Get 阶段的 family 改写不是绕过业务准入：改变后的 batch 必须继续满足完整窗口、单一
non-Config family、Config/Context 分流和对应 Port admission；如果改写制造 conflict，必须
在写入前失败。Write 阶段的 result 是 settlement evidence 的最终投影，任何字段不一致都
由 `ObservePayloadAdmission` 拒绝。两次 Hook 的 stage 和 `node_id` 始终由领域 admission
保持。

Observe 接线固定为：

- `GetObservationNode` 只用 `ObserveRequest.cursor` 读取 queue，并构造无 state 的 frame；
- `WriteObservationNode` 消费 Get 完成后第一次 Hook activation 的 result，不绑定原始
  `ObserveRequest`；
- `WriteObservationNode` 完成 settlement 后产生第二次 Hook request；第二次 Hook activation
  的 result 才是 Observe 对父图的最终输出；
- 双 activation 顺序保持：`request -> get -> Hook(第一次) -> write -> Hook(第二次) -> parent output`；
- `resume_observation()` 从 interrupt 的 durable `ObservationWait` 重建新的 cursor request，
  不携带旧 Hook state；
- stage、cursor、delivery、receipt、family、boundary、ACK、settlement evidence 和并发隔离
  约束保持不变。

## 5. 字段 owner 与校验矩阵

### 5.1 共享 Hook 字段

| 字段 | owner | 产生位置 | 校验位置 | 允许变化 |
| --- | --- | --- | --- | --- |
| `HookRequest.value` | 领域 projection owner | 每个 Hook activation 前 | HookPayloadAdmission + 领域 admission | 仅领域声明的业务 projection 字段 |
| `HookRequest.node_id` | Graph/领域节点 | 节点进入 Hook 时 | shared Hook + route admission | P1/P2/P3 不得伪造 |
| `config`/Plan | Hook owner | activation 开始时 | Hook plan admission | 本 activation 内不变 |
| `commands` | Hook/command owner | P1/P2/P3 | exact command admission | 保留顺序、重复项和终端透传 |
| `state`/`hook_state` | 无共享 Hook owner | 不产生 | 不存在该字段 | 不允许出现 |

### 5.2 Act 关键字段

| 字段 | owner | 产生位置 | 校验位置 | 允许变化 |
| --- | --- | --- | --- | --- |
| `pairing` | Act caller/Resolve | `ActRequest` | ResolveNode + Act admission | 不允许 |
| `selector` | Act caller/Resolve | `ActRequest` | ResolveNode + ResolvePort | 不允许 |
| 原始 `arguments` | Act caller | `ActRequest.arguments` | ResolveNode facts relation | 不允许 |
| `caller` | Act caller | `ActRequest` | ResolveNode facts relation | 不允许 |
| `definition` | ResolvePort | `ResolvedInvocation` | Resolve admission | 不允许 |
| `binding` | ResolvePort | `ResolvedInvocation` | Resolve admission | 不允许 |
| canonical arguments/digest | ResolvePort | `CanonicalArguments` | ResolvePort/Act admission | 不允许；不与 raw bytes 混比 |
| authorization request ref | AuthorizePort | interrupt/resume DTO | Authorize admission | 只能匹配同一 pairing |
| execution identity | ExecutePort | `ToolExecutionResult` | Execute/Settle admission | 必须匹配 authorized invocation |
| settlement projection/receipt | SettlementPort/writer | Settle stage | settlement admission | 写入后 evidence 不允许伪造 |
| `hook_state` | 无 Act 业务消费者 | 不产生 | 删除 | 不允许恢复旧字段 |

### 5.3 Think/Observe projection 字段

Think 的五个 adapter 已在 §4.1 冻结。Observe 不为整帧增加第二套转换 adapter，而是把
`ObservationPayload` 本身冻结为 Hook-visible projection；其 producer/Port 在进入 Graph
前必须完成 raw value 到 concrete payload 的转换。

Think projection 的 adapter 矩阵如下；source 只存在于 owner/Port 一侧，projection 才能
进入 Hook：

| adapter | source | projection | `project` 产生 | `admit` 保持/允许 | `materialize` 消费 |
| --- | --- | --- | --- | --- | --- |
| prompt | `PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]` | `PromptHookProjection` | 三个 prompt component 的 closed projection | 类型、必填和闭合性保持；三个业务 component 可修改 | `ContextNode`、`CompactNode`、`InferenceNode` 的 `PromptFrame` |
| context | `ContextFrame[ContextSnapshotT]` | `ContextHookProjection` | context snapshot 的 closed projection | 类型、必填和闭合性保持；snapshot 业务字段可修改 | `CompactNode` 的 `ContextFrame` |
| compacted | `CompactedContext[CompactedSnapshotT]` | `CompactedHookProjection` | compacted snapshot 与 token count | token count 必须是非负整数；snapshot 业务字段可修改 | `InferenceNode` 的 `CompactedContext` |
| inference | `InferenceResult[ModelOutputT]` | `InferenceHookProjection` | provider-neutral model output | 类型、必填和闭合性保持；业务 output 字段可修改 | `CommandNode` 的 `InferenceResult` |
| command | `ThinkCoreResult[CommandT]` | `CommandHookProjection` | structured command 的 closed projection | 类型、必填和闭合性保持；业务 command 字段可修改 | Think 内不 materialize；父图的真实 `ThinkCoreResult` consumer 在输入边界调用同一 adapter |

每个 projection 的 `admit(original, candidate)` 必须逐字段冻结两类规则：

- 允许 Hook 改变的业务字段；
- 必须保持的 identity、provenance、cursor、boundary、revision 和 evidence 字段。

Think 当前五类 projection 没有额外的 identity/evidence 字段，只有表中列出的类型、必填、
闭合性和阶段-specific business value 约束；未来若增加 provenance/evidence，必须把字段
加入具体 projection DTO，并在该 adapter 的 `admit` 中明确列为 immutable，不能依赖共享
Hook 的通用 equality。

`materialize(projection)` 必须只从已准入 projection 重建 Port value，不得从 Hook request、
原始 Graph input、全局状态或隐藏闭包补字段。未提供具体 adapter、具体字段规则和具体
materialize 调用点的 payload 不得接入共享 Hook。

Observe 的字段矩阵如下：

| 阶段 | Hook-visible value | 可变字段 | 不可变字段 | 最终 owner |
| --- | --- | --- | --- | --- |
| Get 后第一次 Hook | `ObserveFrame(ObservationBatch, ObservationBoundary, BackgroundTaskSnapshot)` | delivery payload 及其 observation family | delivery ids、前后 cursor、observation revision、frame boundary、task snapshot | `ObservePayloadAdmission` + 四个 producer-owned payload adapter |
| Write 后第二次 Hook | `ObserveResult` | 无 value 字段；仅允许合法 commands | kind、delivery ids、cursor range、receipt、settlement boundary/revision、ACK reference、task snapshot | `ObservePayloadAdmission`（该 result 不含 observation payload） |

## 6. 生产代码修改面

### 6.1 直接修改

```text
src/mote_kernel/hooks/contract.py
src/mote_kernel/hooks/node.py
src/mote_kernel/hooks/port.py
```

删除通用 state 泛型、字段、admission 和 equality；保持 HookNode/Graph/Invocation 的
既有执行拓扑。

### 6.2 Think

```text
src/mote_kernel/think/contract.py
src/mote_kernel/think/projection.py
src/mote_kernel/think/node.py
src/mote_kernel/think/prompt.py
src/mote_kernel/think/context.py
src/mote_kernel/think/compact.py
src/mote_kernel/think/inference.py
src/mote_kernel/think/command.py
```

拆分 Port-facing DTO 与 Hook-facing projection；`ThinkFrame` 删除 state 泛型；只保留真实
需要完整 `ThinkRequest` 的 binding；在 `think/projection.py` 建立五种 concrete projection、
对应 adapter 和 owner admission/materializer。该模块不保存运行时状态，也不提供 registry。

### 6.3 Act

```text
src/mote_kernel/act/contract.py
src/mote_kernel/act/admission.py
src/mote_kernel/act/node.py
src/mote_kernel/act/port.py
src/mote_kernel/act/resolve.py
src/mote_kernel/act/authorize.py
src/mote_kernel/act/execute.py
src/mote_kernel/act/settle.py
```

冻结无状态 `ActRequest`/`ResolvedInvocation` 形状；删除 Act 无消费者的 state plumbing；
保留 Resolve/Authorize/Execute/Settle 的 facts、identity、interrupt、command 和 settlement
admission。

### 6.4 Observe

```text
src/mote_kernel/observe/contract.py
src/mote_kernel/observe/admission.py
src/mote_kernel/observe/node.py
```

删除 Observe 无消费者的 state plumbing；收紧 observation payload 为 closed projection；
在 `ObservePayloadAdmission` 中显式接收四个 producer-owned payload adapter，逐字段完成
payload closure admission；保持双 activation、cursor、receipt、ACK 和 settlement boundary。

### 6.5 原则上不修改

```text
src/mote_kernel/invocation.py
src/mote_kernel/hooks/identity.py
src/mote_kernel/hooks/plan.py
src/mote_kernel/hooks/__init__.py
src/mote_kernel/think/__init__.py
src/mote_kernel/act/__init__.py
src/mote_kernel/observe/__init__.py
src/mote_kernel/execution/
src/mote_kernel/state/
```

这些 owner 分别负责 Invocation boundary、Hook identity/Plan、公共导出、Graph execution
和唯一 runtime state。只有在类型引用明确要求时才做最小同步修改，不能借本需求改造
execution engine 或创建第二套恢复机制。

## 7. 文档修改面

完成代码后同步清理以下文档中的旧 API 和旧 state 语义：

```text
docs/hooks-extension-design.zh-CN.md
docs/hooks-extension-implementation-plan.zh-CN.md
docs/think-graph-implementation-plan.zh-CN.md
docs/act-tool-use-implementation-plan.zh-CN.md
docs/observe-graph-implementation-plan.zh-CN.md
docs/react-graph-implementation-plan.zh-CN.md
```

本文件和需求文件都必须明确：共享 Hook 没有 state；领域 request/state 只在真实 owner
需要时存在；Hook-visible value 使用闭合 projection；ReAct 若需要 `ThinkCoreResult`，只能
在直接消费边界复用 Think 的 typed materializer，不能改变 Think 的 nested output contract。

## 8. 实施顺序与检查点

### 阶段 A：先落地共享 Hook contract

修改 `hooks/contract.py`、`hooks/node.py`、`hooks/port.py` 及通用 Hook 测试。

检查点：

- Hook contract 的类型参数和实例属性中不存在 state；
- 旧 `HookRequest(value, state, node_id)` 构造失败；
- Invocation request 没有 `request.state` 属性；
- P1/P2/P3 可以替换 value，commands 顺序、重复项、node_id 和 route 语义保持；
- 每个 priority 返回后都会先经过 typed `HookTransitionAdmission`；P1/P2 的非法嵌套
  projection 不会进入下一 priority，P3 未通过 owner admission 时不产生最终 HookResult；
- 通用 Hook 源码不读取 `request.state` 或 `value.hook_state`。

### 阶段 B：建立 Think projection 和真实 binding

先完成 projection DTO、adapter、Port materializer 和 `ThinkFrame`，再迁移五个节点。

检查点：

- `ThinkFrame` 只有 state-free step；
- `ThinkRequest` 只在 prompt/context 的真实 Port 输入中使用；
- compact/inference/command 没有为旧 state 转发增加 request binding；
- ContextFrame、prompt、compacted snapshot、model output 的 Hook-visible 传递闭包均为
  closed projection；
- 五次 Hook activation、route、nested output、typed materializer 和并发隔离不回归；
- `hook -> command -> END` 保持原拓扑，不增加只为 command projection materialize 服务的
  terminal node；Think 输出是已经通过 P3 owner admission 的 HookResult。

### 阶段 C：重构 Act facts 并闭合 authorization resume

先把 `ResolvedInvocation` 改为直接持有无状态 `ActRequest`，再删除旧 envelope/request
state 字段。

检查点：

- ActRequest 最终只保留 pairing、selector、raw arguments、caller，不保留 Hook state；
- ResolveNode 逐字段拒绝 pairing、selector、caller、raw arguments 不匹配；
- canonical arguments/digest 仍只由 ResolvePort 产生并校验；
- Hook value 的字段闭包允许到达无状态 `ActRequest` facts，但不能到达任何 state-bearing
  request、Graph snapshot 或 runtime owner；
- authorization interrupt/resume 不读取 `resolved.request.hook_state`；
- 四阶段拓扑、identity、command、settlement evidence 和并发隔离不回归。

### 阶段 D：收紧 Observe projection 并修复 resume 输入

删除 Observe state plumbing，保持 cursor 作为唯一原始 request 事实；再调整两个业务节点
和 resume helper。

检查点：

- `ObserveRequest` 只保留 cursor；
- `ObserveHookEnvelope` 只保留 stage 和 observation projection；
- 四个 producer-owned payload adapter 均在 assembly 时注入并通过 exact type/callable 检查；
- observation payload、frame、receipt 和 result 的字段闭包没有 state；
- `write_observation` 不绑定原始 request；
- `resume_observation()` 不接收或重建 Hook state；
- available、empty、conflict、cursor、receipt、family、boundary、ACK 和并发隔离不回归。

### 阶段 E：文档、专项门禁和设计复审

更新公共文档、删除旧测试断言，补齐直接/间接访问负例，然后运行全部门禁。复杂度命中
时复审真实调用链和 projection owner，不通过无意义拆分规避 ratchet。

## 9. 测试迁移矩阵

### 9.1 通用 Hook

目标文件：`tests/hooks/test_hooks.py`

- dataclass fields、annotations 和实例属性不含 `state`/`hook_state`；
- 旧三参数构造失败，不新增兼容重载或 alias；
- Invocation 尝试访问 `request.state` 得到属性不存在结果；
- P1/P2/P3 逐步看到前一阶段 value；
- P1 返回携带非法嵌套 projection 时，P2 不会被调用；P2 同理阻断 P3；
- commands 保留 priority 顺序、重复项和终端透传；
- route、node_id、Invocation admission、异常和取消语义保持。

### 9.2 Think

目标文件：`tests/think/test_contract.py`、`test_graph.py`、`test_nodes.py`、`test_prompt.py`、
新增 `tests/think/test_projection.py`

- ThinkFrame 无 state 字段；
- `ContextPort` 仍能收到完整 ThinkRequest；
- compact/inference/command 不要求额外原始 request binding；
- prompt/context/compact/inference/command 五个 projection 的直接和间接 state 负例均
  fail closed；
- 缺失 adapter、source/projection nominal type 不匹配、adapter 方法不可调用均在 Graph
  `add_node()` 前失败；
- `project`、`admit`、`materialize` 返回错误外层类型或嵌套开放对象时均被领域 contract
  拒绝；
- Hook 修改允许的 projection 字段后能被 `materialize` 仅凭 projection 正确构造 Port source；
- `materialize` 不读取 baseline、ThinkRequest、Graph snapshot、全局变量或闭包；
- Command P3 的合法 projection 直接作为最终 HookResult 输出，不存在额外 terminal node；
- 需要 ThinkCoreResult 的父图 consumer 在自己的真实输入边界复用同一 command adapter；
- 错误 step、route、node_id、projection type、nested output 和并发隔离继续失败或通过。

### 9.3 Act

目标文件：`tests/act/test_contract.py`、`test_nodes.py`；若 Port SPI 变化同步检查
`tests/act/test_port.py`

- `ActRequest` 与 `ResolvedInvocation.request` 的完整 facts 关系通过；
- selector、caller、pairing、raw arguments 任一不匹配都失败；
- canonical arguments/digest 不被误当作 raw arguments；
- `ResolvedInvocation`、authorization phase、authorized invocation 的字段闭包允许到达无状态
  `ActRequest` facts，但不含任何 state-bearing request、Graph snapshot 或 runtime owner；
- Act Hook 伪造 stage、pairing、binding、digest、execution result、receipt 继续 fail closed；
- authorization interrupt/resume、allow/deny、四阶段拓扑和并发隔离不回归。

### 9.4 Observe

目标文件：`tests/observe/test_admission.py`、`test_contract.py`、`test_graph.py`、`test_nodes.py`；
`test_port.py` 做 provider capability 回归。

- ObserveRequest 只有 cursor；
- Hook envelope 不含 state；
- observation payload 内嵌 ObserveRequest/state 的负例被 owner admission 拒绝；
- provider-native raw value、开放 payload、可变容器和错误 concrete family 均在 producer/
  assembly boundary 被拒绝；
- 四个 producer-owned adapter 的 `project`/`admit` 返回错误类型或非法嵌套字段时，Observe
  assembly/activation 失败，不能只因为外层 payload class exact 就放行；
- P1/P2/P3 返回的非法 observation projection 在下一 priority 启动前被拒绝；
- 双 Hook activation、available/empty/conflict 分支保持；
- cursor、delivery、receipt、family、boundary、ACK 和 settlement evidence 约束保持；
- Get Hook 可改写 payload/family 但不能改写 delivery coordinates、revision、boundary 或 task
  snapshot；Write Hook 改写任一 settlement evidence 均失败；
- resume 从 durable wait cursor 重建 request，不携带旧 state；
- 两个并发 Observe activation 不串线。

### 9.5 跨领域静态审计

专项测试和源码审计必须覆盖以下模式，并检查 projection 的传递闭包：

```text
HookRequest(..., state=...)
HookRequest(..., hook_state=...)
HookInvocationRequest(... state ...)
value.hook_state
resolved.request.hook_state
ThinkFrame(... hook_state ...)
ActHookEnvelope(... hook_state ...)
ObserveHookEnvelope(... hook_state ...)
```

只检查根 DTO 字段、不检查嵌套路径的测试不算通过。

至少包含以下间接可达负例：

- `ThinkFrame.step.prompt.system` 通过自定义嵌套 DTO 指向 `ThinkRequest` 或其
  `hook_state`；
- `ContextHookProjection.snapshot` 通过 wrapper 指向 `ContextFrame.snapshot.hook_state`；
- `InferenceHookProjection.output` 通过 provider-native response 携带 request/state；
- `ObservationDelivery.payload` 的 concrete payload 通过嵌套字段指向 `ObserveRequest` 或
  queue/runtime state；
- Act 的 `ResolvedInvocation.request` 只能到达无状态 `ActRequest` facts，任何带 state 的
  替代 request 都被 exact admission 拒绝。

## 10. 门禁与完成定义

专项门禁：

```text
python -m pyright src/mote_kernel/hooks src/mote_kernel/think src/mote_kernel/act src/mote_kernel/observe
python -m pytest tests/hooks tests/think tests/act tests/observe tests/test_invocation.py -q
python -m ruff check src tests
python -m ruff format --check src tests
git diff --check
```

目录门禁：

```text
make check
```

仓库门禁：从 monorepo 根目录执行 repository-level pre-commit，且保留并行工作树已有改动。

只有同时满足以下条件，才能将状态改为“完成”：

- 设计复审确认采用闭合 projection 边界；
- 共享 Hook 无法通过 request/value 字段闭包直接或间接读取三个领域的 state；
- `HookRequest`、`HookInvocationRequest` 和通用 admission 不再声明 state；
- P1/P2/P3 每次返回后均经过 owner typed transition admission，非法 candidate 不会进入下一
  priority 或最终 HookResult；
- Think 只在真实 Port contract 需要时保留 ThinkRequest/state；
- Think 维持五个业务节点加一个共享 Hook 的拓扑，最终输出为已经通过 P3 owner admission
  的 HookResult，不新增 terminal reification node；
- Observe 的四个 producer-owned payload adapter 已在 assembly 注入，并对 observation payload
  的传递闭包执行真实 owner admission；
- Act/Observe 不保留无消费者的旧 Hook state plumbing；
- Act facts、raw/canonical arguments、selector/caller/pairing 校验已冻结并通过测试；
- route、commands、typed descriptor、nested Graph、interrupt/resume、异常、取消和并发隔离
  无回归；
- 所有专项门禁、`make check` 和 repository-level pre-commit 全部通过。

任一门禁无法运行时，交接状态只能是“待验证”或“阻塞”，必须记录命令、环境原因和未覆盖
范围；不能以“已给出明确报告”替代通过门禁，也不能宣称本需求完成。

## 11. 明确禁止的实现

- 不恢复 `HookRequest.state` 的兼容字段、兼容构造或兼容 alias；
- 不给 compact/inference/command、Act authorize/execute/settle、Observe write 增加仅为
  state 转发服务的原始 request binding；
- 不新增只为 `command_projection.materialize()` 服务的 Think terminal node；如果父图需要
  `ThinkCoreResult`，由直接消费边界复用同一 typed adapter 完成 materialize；
- 不把 `ThinkRequest`、`ObserveRequest` 或任何带 state 的 request 放回 Hook value 的任何
  嵌套 DTO；无状态 `ActRequest` 只能作为已冻结 facts 的明确例外，且不得携带 Graph snapshot
  或 runtime owner；
- 不新增共享 Hook 的反射递归扫描、序列化 blacklist 或“发现 state 就剥离”的通用工具；
- 不用 `unknown`、`object`、`Any`、裸字典或 string discriminator 扩大 projection 边界；
- 不在 execution/state 包新增第二套 state owner、runner、resume path 或缓存；
- 不通过无意义的函数拆分规避 complexity ratchet；复杂度命中时必须复审真实调用链。
