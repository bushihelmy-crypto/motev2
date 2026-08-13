# Frontier 节点统一恢复需求

## 1. 文档信息

- 状态：Ready for re-review（第三轮评审阻塞项已关闭）
- 所属项目：Mote Kernel
- 需求类型：Graph execution / unified node resume and interrupt semantics
- 基线文档：`docs/frontier-node-resume-requirements.zh-CN.md`
- 影响文档：`docs/frontier-node-resume-implementation.zh-CN.md`

本文是在已评审的 Frontier 节点失败恢复需求之上的增量替代需求。它把“恢复全部失败节点”扩展为选择性节点恢复，并将 failure 与
interrupt 纳入同一套恢复协议，同时增加仅针对 Failed 节点的 skip。

本文通过评审后，基线文档中以下约束由本文覆盖：

1. `Pending` 与 `Failed` 不可共存；
2. 一个 Failed 即令 Frontier 无条件不可执行；
3. `ResumeGraphFrontier` 一次恢复全部 Failed；
4. interrupt 只属于 GraphRun 顶层生命周期；
5. `GraphRunStatus.SUSPENDED` 是 node interrupt 的 authoritative state。

本文仍只要求与当前 `GraphState` snapshot 相同的能力层次，不引入 store、journal、跨进程恢复或 Python continuation 持久化。

本需求明确替代基线文档中“本期不保存 input binding”的非目标：只新增 activation-scoped resume input binding；基线中 store、journal、
通用 durable input binding、初始输入持久化和泛型 output persistence 仍然是非目标。

## 2. 背景与问题

基线方案已经能够保留同一 Frontier 中成功 sibling，并在失败后只恢复失败节点。但它仍以整个 Frontier 为恢复选择单位：

```text
A: Succeeded
B: Failed
C: Failed
       │ ResumeGraphFrontier
       ▼
A: Succeeded
B: Pending
C: Pending
```

这不能表达以下需求：

1. 只恢复 B，暂不处理 C；
2. 恢复 B，但明确跳过 C；
3. 同一 Frontier 中一个节点等待 interrupt resolution，其他节点继续结算；
4. 一次原子恢复命令同时处理多个不同类型的待恢复节点；
5. failure resume 与 interrupt resume 共用 revision、identity、quiescence、输入覆盖和选择校验，而不是形成两套恢复协议。

参考 task-level interrupt/replay 模型可以证明这些行为有实际价值，但 Mote 必须以显式 GraphState settlement 表达，不复制
checkpoint pending-writes 或私有 runner。

## 3. 目标

本需求必须实现：

1. 选择性 resume 一个或多个 Failed node activation；
2. 选择性 skip 一个或多个 Failed node activation；
3. node-level interrupt settlement 与精确 resolution；
4. failure 和 interrupt 共用唯一 node resume command；
5. 允许 `Pending` 与未处理的 `Failed`、`Interrupted` 在同一 Frontier 合法共存；
6. 只要存在 Pending activation，就允许继续执行当前 Frontier；
7. 已成功、已跳过和仍待恢复的 sibling 均不被重复执行；
8. 只有 Frontier 全部成为 `Succeeded` 或 `Skipped` 后才统一应用 routing/join 并推进 superstep；
9. 保持 batch lease、revision CAS、token fence、resource admission、routing guard、nested 和唯一 execution engine；
10. resume 时可以为指定 node activation 提供参数，覆盖该节点下一次执行的输入；
11. 对非法恢复 action、错误 interrupt identity、非法输入覆盖、非法 skip routing 和迟到 attempt 结果 fail closed。

## 4. 非目标

本需求不包括：

1. 跳过 `PendingGraphNode`；
2. 跳过 `InterruptedGraphNode`；
3. 在节点首次执行前进行调度过滤或 operator skip；
4. 将 batch execution lease 拆成 node lease；
5. 多 worker 独立领取同一 Frontier 的不同 Pending subset；
6. 保存 Python 栈、协程或函数内部 continuation；
7. 在 `GraphState` 中保存无限增长的 attempt、failure 或 interrupt 历史；
8. 自动恢复策略、退避、最大恢复次数或错误分类；
9. 新增 authoritative store、journal、跨进程恢复、所有调用通用的 durable input binding 或泛型 output persistence；
10. 兼容保留 GraphRun-level 与 node-level 两套 interrupt authoritative path。

本文要求的 resume input override 是当前 GraphState snapshot 中 activation-scoped、由下一次 typed settlement 消费的窄 binding。它不等同于
持久化每次 `StepRequest.node_input`，也不承诺跨进程重建任意泛型输入。

## 5. 核心概念

### 5.1 Frontier 仍是结算屏障

Node activation 是执行、失败、中断、恢复和跳过的粒度；Frontier 仍是 routing、join 和 superstep 推进的原子屏障。

选择性恢复不会把一个 Frontier 拆成多个逻辑 superstep：

```text
(run_id, superstep, node_id)
```

在 failure resume 或 interrupt resume 后保持不变，只创建新的 execution attempt generation。循环进入同一个静态 node 时必须创建新
superstep 和新的 activation。

### 5.2 恢复事实与运行生命周期分离

`Failed`、`Interrupted` 和 `Skipped` 是 Frontier node settlement，不是 GraphRun terminal status。

目标 `GraphRunStatus` 为：

```text
RUNNING | COMPLETED | ABORTED
```

Node interrupt 不再通过顶层 `SUSPENDED` 表示。一个 RUNNING GraphRun 可以暂时没有 Pending、等待 node resume；也可以同时包含 Pending
和未恢复的 Failed/Interrupted。

`COMPLETED` 与 `ABORTED` 仍是唯一终态。Abort 继续要求 active worker 已停止且 execution lease 已精确 fence。

## 6. Node settlement 模型

`GraphNodeSettlement` 扩展为：

```python
GraphNodeSettlement: TypeAlias = (
    PendingGraphNode
    | SucceededGraphNode
    | FailedGraphNode
    | InterruptedGraphNode
    | SkippedGraphNode
)
```

建议的 state-owned 结构：

```python
GraphResumeInputPayload = NewType("GraphResumeInputPayload", bytes)
GraphResumeInputCodecId = NewType("GraphResumeInputCodecId", str)


@dataclass(frozen=True, slots=True)
class GraphResumeInputCodec:
    codec_id: GraphResumeInputCodecId
    version: int


@dataclass(frozen=True, slots=True)
class GraphNodeInterruptIdentity:
    run_id: GraphRunId
    superstep: int
    node_id: GraphNodeId
    execution_generation: int


@dataclass(frozen=True, slots=True)
class UseStepRequestInput:
    pass


@dataclass(frozen=True, slots=True)
class OverrideGraphNodeInput:
    payload: GraphResumeInputPayload


GraphNodeInputBinding: TypeAlias = (
    UseStepRequestInput | OverrideGraphNodeInput
)


@dataclass(frozen=True, slots=True)
class PendingGraphNode:
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SucceededGraphNode:
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class FailedGraphNode:
    failure: GraphFailure


@dataclass(frozen=True, slots=True)
class GraphNodeInterrupt:
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class InterruptedGraphNode:
    interrupt: GraphNodeInterrupt


@dataclass(frozen=True, slots=True)
class SkippedGraphNode:
    failure: GraphFailure
    reason: GraphSkipReason
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class GraphFrontierNode:
    node_id: GraphNodeId
    settlement: GraphNodeSettlement
```

`GraphRunState` 必须保存由 graph definition/version 固定的 `resume_input_codec: GraphResumeInputCodec | None`。它只保存 codec identity，
不保存 execution decoder。没有配置 codec 的 graph 仍可使用 `UseStepRequestInput`，但必须拒绝任何 `OverrideGraphNodeInput` 和 typed
`NodeInterrupt` settlement。

语义要求：

- `Pending(UseStepRequestInput)`：首次执行，或明确沿用本次 `StepRequest.node_input` 的 failure resume；
- `Pending(OverrideGraphNodeInput)`：resume 已为该 activation 绑定覆盖参数，下一 attempt 必须只把解码后的输入投递给该节点；
- `Succeeded`：节点成功并保存 routing contribution；
- `Failed`：节点显式返回 typed `NodeFailure`；
- `Interrupted`：节点产生 typed node interrupt，保存当前 identity 与 request payload，等待精确 resume；
- `Skipped`：operator 明确跳过一个 Failed activation，并提供其控制流 contribution；
- `Skipped` 保留原 failure 和 skip reason，不能伪装成 `Succeeded`；
- resume 后最近一次 failure 不再属于当前 settlement 历史；完整 attempt history 仍是非目标。

## 7. Frontier 派生语义

### 7.1 可执行性优先于未恢复 sibling

Frontier status 从 settlement 派生，规则按以下顺序计算：

```text
至少一个 Pending                                      -> EXECUTABLE
没有 Pending，至少一个 Failed 或 Interrupted           -> AWAITING_RESUME
全部为 Succeeded 或 Skipped                            -> SETTLED
其他组合                                               -> INVALID
```

因此以下均为合法稳定状态：

```text
Succeeded + Pending
Succeeded + Pending + Failed
Succeeded + Pending + Interrupted
Succeeded + Pending + Failed + Interrupted + Skipped
Succeeded + Failed
Succeeded + Interrupted
```

`Succeeded + Skipped` 已经是全部结算完成，只能作为 reducer 内部的瞬时 settled candidate；产生最后一个 skip 的同一命令必须立即
advance/complete，不得将该组合作为长期稳定 snapshot 提交。

`EXECUTABLE` 不表示所有 sibling 都健康，只表示当前存在可以领取的 activation。`AWAITING_RESUME` 表示 Frontier barrier 尚未满足且当前没有
可执行 activation。

Frontier status 仍然只是只读派生结果，不得作为第二份 authoritative field 持久化。同时提供以下独立查询：

```text
pending_node_ids
failed_node_ids
interrupted_node_ids
skipped_node_ids
```

### 7.2 Barrier 规则

只要还存在 `Pending`、`Failed` 或 `Interrupted`，不得应用本 Frontier 的 routing contribution、join arrival 或推进 superstep。

只有全部节点均为 `Succeeded` 或 `Skipped` 时：

1. execution engine 校验完整 contribution；
2. 统一计算 direct edge、conditional route 和 join；
3. 原子创建下一 Frontier 或完成 GraphRun；
4. `Skipped` contribution 与 `Succeeded` contribution 各应用一次。

## 8. 唯一恢复协议

### 8.1 Command

删除 `ResumeGraphFrontier` 和 GraphRun-level `ResolveGraphRunInterrupt` 恢复入口，使用唯一命令：

```python
@dataclass(frozen=True, slots=True)
class ResumeGraphNodes:
    expected_revision: int
    actions: tuple[GraphNodeResumeAction, ...]
    resolution: GraphFrontierResolution | None
```

Action 是严格 union：

```python
GraphNodeResumeAction: TypeAlias = (
    ResumeFailedNode
    | SkipFailedNode
    | ResumeInterruptedNode
)


@dataclass(frozen=True, slots=True)
class ResumeFailedNode:
    node_id: GraphNodeId
    input: GraphNodeInputBinding


@dataclass(frozen=True, slots=True)
class SkipFailedNode:
    node_id: GraphNodeId
    reason: GraphSkipReason
    routing: GraphRoutingContribution


@dataclass(frozen=True, slots=True)
class ResumeInterruptedNode:
    node_id: GraphNodeId
    interrupt_id: GraphInterruptId
    input: OverrideGraphNodeInput
```

不得使用 string action discriminator、多个 Optional payload 或一个无类型的通用 resume value。

上述类型是 state command 边界。面向调用方的 execution resume request 必须是泛型 typed request：

```python
@dataclass(frozen=True, slots=True)
class OverrideNodeInput(Generic[InputT]):
    value: InputT


ResumeNodeInput: TypeAlias = UseStepRequestInput | OverrideNodeInput[InputT]
```

Execution resume service 将 `OverrideNodeInput[InputT]` 通过 compiled graph 的唯一 codec 编码为
`OverrideGraphNodeInput(GraphResumeInputPayload)`，完成 routing/input guard 后才产生 state-owned `ResumeGraphNodes`。调用方不直接构造 opaque
payload，state reducer 也不接收泛型 `InputT`。

### 8.2 原子转换

转换规则固定为：

```text
Failed      --ResumeFailedNode------> Pending(action.input)
Failed      --SkipFailedNode--------> Skipped(failure, reason, routing)
Interrupted --ResumeInterruptedNode-> Pending(action.input)
```

未被 action 选中的节点 settlement 原样保留。一个 command 可以同时 resume 或 skip 不同节点；整组 actions 要么全部成功，要么全部
拒绝，不允许部分应用。

`resolution` 解决“skip 最后一个 Failed 后 Frontier 立即全部结算”的状态闭包：

- action 应用后仍有 Pending、Failed 或 Interrupted 时，`resolution` 必须为 `None`；
- action 应用后全部为 Succeeded/Skipped 时，`resolution` 必须为 `AdvanceGraphFrontier` 或 `CompleteGraphFrontier`；
- reducer 必须在同一次 revision 中完成 skip settlement 与 advance/complete，不得提交长期 `SETTLED` snapshot；
- execution engine 必须先用完整 Succeeded/Skipped contributions 计算并校验 resolution，再生成命令。

### 8.3 Reducer 前置条件

`ResumeGraphNodes` 必须验证：

1. revision 精确匹配；
2. GraphRun 为 `RUNNING`；
3. 没有 active execution lease 和 resource admission；
4. actions 非空、按 node ID canonical order、node ID 唯一；
5. action 中每个 node 属于当前 Frontier；
6. `ResumeFailedNode` 和 `SkipFailedNode` 只接受当前 `Failed`；
7. `ResumeInterruptedNode` 只接受当前 `Interrupted`；
8. interrupt ID 必须与该节点当前 outstanding interrupt 精确匹配；
   identity 必须来自该 node 当前 `InterruptedGraphNode.interrupt.identity`，不能由调用方另行指定 identity 来源；
   action `interrupt_id` 必须等于唯一 state-owned `interrupt_id(identity)` 投影结果；
9. `ResumeFailedNode.input` 必须是合法的 `UseStepRequestInput` 或 `OverrideGraphNodeInput`；
10. `ResumeInterruptedNode` 必须携带 `OverrideGraphNodeInput`，不能用 `UseStepRequestInput` 假装 interrupt 已解决；
11. reason、input payload 和所有 state-owned value object 合法；
12. 非最终结算分支不修改 superstep、node set、已有 success/skip settlement 或 prior join progress；
13. 最终结算分支必须按 resolution 原子 advance/complete，并更新相应 superstep、frontier 和 join progress；
14. 成功转换后 revision 只增加一次。

明确禁止：

```text
Pending     --Skip--> ...
Succeeded   --Resume/Skip--> ...
Skipped     --Resume/Skip--> ...
Interrupted --Skip--> ...
Failed      --ResumeInterruptedNode--> ...
```

## 9. Skip 语义

Skip 严格属于 failure resume，不是普通调度控制。

要求：

1. 只能 skip 已经结算为 `Failed` 的 activation；
2. 不提供 skip Pending 的 API、命令变体或内部 fallback；
3. skip 不执行节点业务逻辑，也不产生泛型 output；
4. skip 必须保存原 failure、operator reason 和 routing contribution；
5. 普通 direct-edge node 使用 `ContinueGraphRouting`；
6. conditional node 必须显式提供合法 `SelectGraphRoute`；
7. 未知 route、conditional node 的 `Continue`、普通 node 的 `SelectGraphRoute` 均属于 execution topology error；
8. routing guard 失败时不得提交 `ResumeGraphNodes`，原 Failed settlement 保持不变；
9. skip 后下游是否执行完全由所提供 contribution 和 compiled topology 决定。

Skip routing 必须复用 failure-resume 方案中的唯一 `validate_routing_contribution`，不得实现第二套 conditional route 规则。

## 10. Node interrupt 语义

### 10.1 Interrupt settlement

Node interrupt 是 typed execution outcome，不是普通 Python exception。Scheduler/collector 必须允许同批 sibling 完成，并原子保存本 attempt 的
全部 typed outcomes：

节点执行合同相应扩展为严格 typed union：

```text
NodeSuccess | NodeFailure | NodeInterrupt
```

`NodeInterrupt` 携带 request payload；execution 根据当前 activation 与 interrupt generation 生成稳定、在当前 Frontier 内唯一的
interrupt identity，并通过 state-owned 纯函数投影稳定 interrupt ID。普通 exception、contract violation 或 cancellation 不得转换为
`NodeInterrupt`。

`NodeInterrupt.request_payload` 是节点发给调用方的请求信息；`OverrideGraphNodeInput.payload` 是调用方提供给下一次执行的完整输入。两者方向
不同，必须分别保存，不得复用字段，也不得把 request payload 隐式作为 resume input。

Interrupt identity 必须由 state owner 定义的唯一纯函数派生：

```text
derive_interrupt_identity(
    run_id,
    superstep,
    node_id,
    execution_token.generation,
)

interrupt_id(identity)
```

Identity 只保存上述结构化坐标；`GraphInterruptId` 是 identity 的 deterministic projection，不作为第二份 authoritative field 保存。
`InterruptedGraphNodeOutcome` 必须携带 identity；reducer 使用当前 exact lease token、outcome node ID、run ID 和 superstep 重新派生并精确比对。
Execution 只能调用同一组 state-owned 派生/投影函数，不得维护随机 ID、第二个 interrupt counter 或 fallback identity。未形成 typed
settlement 的 interrupt 不成为 GraphState 事实。

Exact lease 来源证明只属于 `SettleGraphExecution` transition。当 reducer 同时持有 active lease 时，必须验证：

1. command execution token 与当前 lease token 精确匹配；
2. outcomes 精确、唯一覆盖 lease node IDs；
3. interrupt outcome 只更新 lease 中当前 Pending activation；
4. identity 使用当前 active lease token generation 与 run/superstep/node 坐标重新派生；
5. 重新派生的 identity 与 outcome identity 完全一致；
6. 通过后才写入 `InterruptedGraphNode` 并原子清 lease/resources。

该历史来源证明不得委托给 execution guard，也不得延迟到稳定 snapshot validator。

Typed interrupt settlement 必须原子完成：

```text
settlement: Pending -> Interrupted(identity, request_payload)
```

Resume 把当前 `Interrupted` settlement 替换为 `Pending(input override)`，当前 interrupt identity 随 settlement 一起消费。若后续 attempt 再次
产生 interrupt，则用该 attempt 的新 execution generation 派生新 identity；旧 identity 不保留为历史，也不能再次 resume。

```text
A -> Succeeded
B -> Interrupted(id=b1)
C -> Failed
```

结算后：

```text
A: Succeeded
B: Interrupted(b1)
C: Failed
```

如果同一 attempt 仍因 contract violation、普通 Python exception 或 infrastructure error 失败，则保持现有规则：整批不 settlement，保留
active lease，外部停止后精确 fence。不得把这些异常转换成 `InterruptedGraphNode`。

### 10.2 Resolution 与重执行

Resume 不恢复 Python continuation。它把精确 input override 绑定到原 activation，随后节点从其正常执行入口重新执行：

```text
Interrupted(b1)
    │ ResumeInterruptedNode(b1, input=Override(payload))
    ▼
Pending(OverrideGraphNodeInput(payload))
    │ claim / execute
    ▼
Succeeded | Failed | Interrupted(new attempt identity)
```

要求：

1. input binding 只能投递给相同 `(run_id, superstep, node_id)`；
2. claim/task projection 不得把 override 广播给 sibling；
3. typed settlement 成功提交时消费该 Pending binding；
4. Python exception 后未 settlement 时 binding 保留，fence 后再次执行仍收到同一 override；
5. 节点再次 interrupt 时必须使用当前新 attempt generation 派生的新 identity；旧 identity 不得复用；
6. 节点在 interrupt 前的外部副作用必须由节点契约保证幂等或拆分到独立 activation；Kernel 不保存函数内部 continuation。

一个 activation 在任一稳定 snapshot 中最多存在一个 outstanding interrupt，但可以在多次 attempt 中依次产生多个 interrupt。本期仍不提供
interrupt index、按调用顺序匹配的 resume-value tape 或节点内部 continuation；每次 resume 只处理当前 settlement 中的唯一 interrupt。节点若
需要依赖多个先前答案，必须通过显式完整 input override 自行携带所需输入，Kernel 不自动回放历史 resume values。

### 10.3 Resume input override

当前 `StepRequest.node_input` 是一个 Frontier attempt 的共享不可变默认输入。实施本需求后，scheduler 必须按 node 选择 effective input：

```text
Pending(UseStepRequestInput)
    -> StepRequest.node_input

Pending(OverrideGraphNodeInput(payload))
    -> compiled_graph.resume_input.decoder.decode(payload)
```

同一 claimed batch 可以同时包含默认输入节点和不同 override 输入节点。Scheduler 不得再把一个预先计算的 `node_input` 无条件广播给整个
batch；它必须为每个 task 物化自己的 effective input，并继续把该输入视为节点执行期间的不可变值。

GraphState 不能依赖 execution，也不能保存泛型 `InputT`。因此：

1. state 只保存 `GraphResumeInputPayload` opaque bytes；
2. compiled graph 定义唯一、版本化的 resume input codec identity、encoder 与 decoder；
3. execution snapshot guard 校验 state codec identity/version 与 compiled graph 匹配；
4. execution 是唯一 codec owner：resume request 边界把 `InputT` 编码为 payload，调度前再将 payload 解码为 `InputT`；
5. codec 缺失、版本不匹配或 encode/decode 失败属于 infrastructure/snapshot error，不得转换成 node Failed/Interrupted；
6. failure resume 与 interrupt resume 共用同一个 codec，不创建两套 decoder。

唯一 codec binding 的 execution-owned 合同至少为：

```python
class ResumeInputEncoder(Protocol[InputT]):
    def encode(self, value: InputT) -> bytes: ...


class ResumeInputDecoder(Protocol[InputT]):
    def decode(self, payload: bytes) -> InputT: ...


@dataclass(frozen=True, slots=True)
class ResumeInputBinding(Generic[InputT]):
    codec_id: ResumeInputCodecId
    version: int
    encoder: ResumeInputEncoder[InputT]
    decoder: ResumeInputDecoder[InputT]
```

Encoder 与 decoder 必须 deterministic、side-effect-free：相同 codec identity/version 与等价输入必须产生相同 payload；相同 payload 必须解码
为等价 `InputT`。Codec 不得读取隐藏可变状态、执行业务 IO、产生外部副作用或承担外部协调。这样 claim 前预校验与 claim 后对 committed
snapshot 的重新物化才具有相同语义。

错误阶段必须区分：

- resume request encode 失败：不生成 `ResumeGraphNodes`，不提交状态，也没有 lease 可 fence；
- codec identity/version 与 compiled graph 不匹配：snapshot guard fail closed，不进入 prepare/claim；
- prepare/claim 前 payload decode 或 input validation 失败：不产生 claim command，不存在 active lease；
- claim 已提交后的 claimed snapshot 必须重新运行同一 guard 并重新物化 effective inputs；此后发生的 decode、contract 或 infrastructure error
  整批不 settlement，保留 active lease，外部停止后精确 fence；
- 所有 encode/decode/codec error 都不得转换成 Failed 或 Interrupted settlement。

现有 graph resolution codec 基础设施应原位泛化/重命名为 resume input codec；最终实现不得同时保留 `resolution codec` 和 `resume input
codec` 两套 authoritative binding。

Override 生命周期与作用域：

- override 只属于一个精确 `(run_id, superstep, node_id)`；
- override 是完整 `InputT` 替换，不做 dict merge、字段 patch、反射复制或节点自定义合并；
- override 不修改 `StepRequest.node_input`，也不修改任何 sibling；
- failure resume 可以显式选择 `UseStepRequestInput` 或 `OverrideGraphNodeInput`；
- interrupt resume 必须提供 `OverrideGraphNodeInput`，该参数同时表达 interrupt resolution 和节点重执行输入，不再保存第二份 resolution payload；
- skip 不接受 input，因为 skipped node 不执行；
- typed outcome settlement 后，Pending 被 outcome 替换，override 随之消费；
- exception/cancellation/infrastructure error 未 settlement 时，override 仍留在 Pending，exact fence 后可再次执行；
- Frontier advance/complete、abort 后不得把 override 投递到其他 activation；
- loop/self-loop 新 superstep 初始化为 `Pending(UseStepRequestInput)`，不得继承旧 override。

### 10.4 移除顶层 interrupt 真相

node-level interrupt 成为 authoritative path 后：

- 删除 `GraphRunState.interrupt` 作为当前 outstanding interrupt 的真相；
- 删除 `GraphRunStatus.SUSPENDED`；
- 删除 `RequestGraphRunInterrupt` / `ResolveGraphRunInterrupt` 的旧恢复路径；
- prepare 从 Frontier settlements 投影全部 outstanding interrupts；
- 不保留 compatibility alias、双写或旧/新 interrupt fallback。

这是明确的产品行为删除，而不是暂未决定的兼容问题。本期不保留执行前 operator GraphRun pause；必须同步删除相关 production path、公开
export、调用方、测试和文档，包括：

```text
GraphRunStatus.SUSPENDED
ExecutionStatus.SUSPENDED
GraphRunState.interrupt
RequestGraphRunInterrupt
ResolveGraphRunInterrupt
GraphInterruptLifecycle / receipt / 顶层 resolution input path
SuspendedGraph prepare disposition
```

不得将这些符号保留为 alias，也不得用隐藏 transition 继续提供 operator pause。未来如果重新需要执行前 pause，必须作为独立需求重新设计，
不能复用 node interrupt 或 `ResumeGraphNodes`。

## 11. Claim、settlement 与 prepare

### 11.1 Claim

Batch lease 继续保留，且必须精确覆盖当前 Frontier 的全部 Pending nodes：

```text
lease.node_ids == pending_node_ids(frontier)
```

Failed、Interrupted、Succeeded、Skipped 均不得进入 claim。剩余 Failed/Interrupted 不阻止 Pending batch 执行。

### 11.2 Attempt settlement

`SettleGraphExecution` 的 outcomes 扩展为：

```text
SucceededGraphNodeOutcome
FailedGraphNodeOutcome
InterruptedGraphNodeOutcome
```

它们必须精确覆盖 lease node IDs。Reducer 原子合并 outcomes 并清 execution/resources：

- 合并后有 Pending：非法，因为 lease 已精确覆盖全部 Pending；
- 无 Pending且仍有 Failed/Interrupted：保持 `RUNNING + AWAITING_RESUME`；
- 全部 Succeeded/Skipped：必须携带 execution engine 计算出的 advance/complete resolution。

所有新 success contribution 仍必须在判断 failure/interrupt 分支前通过统一 compiled-topology validator。

### 11.3 Prepare disposition

Prepare 必须在统一 snapshot/topology guard 之后返回严格 disposition：

```text
ExecutableFrontier   至少一个 Pending
AwaitingResume       无 Pending，至少一个 Failed/Interrupted
WaitingForChildren   Pending nested activation 仅在等待 active child
CompletedGraph
AbortedGraph
```

`AwaitingResume` 必须分别暴露 failed nodes 与 interrupted nodes，不用一个模糊 failure 字段混合二者。原 `SuspendedGraph` disposition 删除。

Prepare 顺序固定为：

1. 选择 definition/version 对应 compiled graph，完成 snapshot/topology/input codec guard；
2. 返回 terminal 或 `AwaitingResume` disposition；
3. EXECUTABLE Frontier 中存在 `MissingChild` 时，先产生全部确定性 child start actions；
4. 不存在 MissingChild、但任一 Pending nested activation 对应 `ActiveChild` 时，返回 `WaitingForChildren`；
5. 所有 Pending nested children 均有 terminal projection 后，执行 resource admission；
6. 最后才 claim 当前全部 Pending nodes。

因此，只要存在 ActiveChild，普通 Pending sibling 也暂不单独 claim。`WaitingForChildren` 是 execution prepare disposition，不写入 GraphState，
也不改变 Frontier 的 `EXECUTABLE` 派生状态。不得为此引入 partial claim 或第二种 lease 覆盖规则。

## 12. Resource、fence 与 abort

- `ResumeGraphNodes` 只接受 scheduler-quiescent snapshot；
- typed attempt settlement 原子清 lease/resources；
- exception/cancellation 后调用方先完成外部停止，再以 exact token fence；
- fence 原子清除该 attempt 的 lease/resources，不修改 node settlement 或 input binding；
- active lease 存在时 abort 必须拒绝；
- quiescent abort 可以保留完整 Frontier 作为诊断事实，但其节点不可再 resume；
- ABORTED snapshot 只验证 state-owned payload 形状、graph identity 与 node membership；不得解码 retained override、重新解释 interrupt/routing/join、
  进入 planning 或投递 input；
- stale resume revision、stale interrupt ID 和 stale fence 均不得影响新 generation。

## 13. Routing、join、循环与 nested

### 13.1 Routing 与 join

- Succeeded 与 Skipped 均提供 routing contribution；
- Failed、Interrupted 和 Pending 不提供 contribution；
- routing/join 只在 Frontier 全部 Succeeded/Skipped 后统一执行一次；
- 选择性 resume/skip 不提前应用 join arrival；
- retained success 或 skip contribution 必须通过同一个 compiled-topology guard；
- skip 最后一个待恢复节点时，`ResumeGraphNodes.resolution` 必须与完整 contribution 的 routing 结果一致；
- next Frontier 与 join progress 仍与 settlement 原子提交。

Resolution 责任边界保持不变：execution 使用完整 Succeeded/Skipped contributions 与 prior join progress 计算 resolution，并通过统一 compiled
topology guard 验证；state reducer 只验证 actions、settlement 类型、resolution presence、next Frontier 的 state-owned 结构和生命周期转换，
不得依赖 `CompiledGraph` 或自行复算 topology。

### 13.2 循环

- failure/interrupt resume 保持当前 superstep 和 activation coordinates；
- loop/self-loop 进入新 superstep，所有新 activation 初始化为 `Pending(UseStepRequestInput)`；
- 不从前一 superstep 复制 Succeeded、Failed、Interrupted、Skipped 或 input binding。

### 13.3 Nested graph

- parent node 的 Failed/Interrupted/Skipped 与普通 node 使用同一 settlement union；
- child blocked 或存在 node interrupt 时，parent activation 仍指向同一个 child run；
- resume child interrupt 必须恢复原 child GraphRun，不得重建 child；
- child 最终 COMPLETED 才转换为 parent success；child ABORTED 按 typed nested failure 进入 parent Failed；
- 不新增 state-store lookup port。

## 14. 状态不变量

Validator 至少强制：

1. Frontier node IDs 非空、唯一、canonical；
2. RUNNING Frontier 不允许全为 Succeeded/Skipped 的长期 snapshot；
3. COMPLETED 使用唯一 empty Frontier 表示；
4. ABORTED 无 active lease/resources，retained Frontier 只读；
5. active lease 精确等于当前全部 Pending nodes；
6. active lease 中每个 Pending 的 input binding 合法且只属于该 node；
7. Failed/Interrupted 与 Pending 可以共存；
8. 一个 node 只能持有一种 settlement；
9. `InterruptedGraphNode` 必须同时保存当前 identity 与 request payload，identity 坐标匹配当前 run/superstep/node；
10. Interrupted identity generation 必须为正且不大于当前 `GraphRunState.execution_sequence`；
11. 一个 Frontier 内所有当前 Interrupted identity 及其派生 ID 唯一；
12. Skipped 必须同时保存 source failure、reason 和 routing；
13. Failed/Interrupted/Skipped 不得出现在 active lease；
14. override 存在时 GraphRun codec identity/version 必须存在且 state-owned payload 合法；
15. no-Pending + no-Failed + no-Interrupted 的非终态 snapshot 非法；
16. revision、lease token、resource participant 和 activation identity 保持现有 fencing 规则；
17. GraphRun 不得包含 SUSPENDED lifecycle 或顶层 interrupt state；
18. GraphState 不得保存已消费 interrupt identity、lease history、interrupt history、counter、index、journal 或 resume-value tape。

稳定 snapshot validator 只验证当前 snapshot 可证明的上述事实。`generation <= execution_sequence` 仅证明 identity 没有引用未来 generation，
不等价于重新证明它在历史上来自某一把 exact lease。identity 与生成它的 exact lease token 的一致性，已经在
`SettleGraphExecution` transition 中由 reducer 一次性证明；稳定 validator 不得要求或推导历史 lease 来源。

## 15. 代表性状态流

### 15.1 选择性 resume

```text
A: Succeeded, B: Failed, C: Failed
    │ Resume(ResumeFailed B)
    ▼
A: Succeeded, B: Pending, C: Failed       EXECUTABLE
    │ execute B -> success
    ▼
A: Succeeded, B: Succeeded, C: Failed     AWAITING_RESUME
```

### 15.2 Resume 与 skip 原子组合

```text
A: Succeeded, B: Failed, C: Failed
    │ Resume(ResumeFailed B, Skip C)
    ▼
A: Succeeded, B: Pending, C: Skipped      EXECUTABLE
    │ execute B -> success
    ▼
A: Succeeded, B: Succeeded, C: Skipped    SETTLED -> route
```

### 15.3 Interrupt 与 failure 独立恢复

```text
A: Interrupted(a1), B: Failed, C: Succeeded
    │ Resume(ResumeInterrupted A, input=Override(payload))
    ▼
A: Pending(Override(payload)), B: Failed, C: Succeeded  EXECUTABLE
    │ execute A -> success
    ▼
A: Succeeded, B: Failed, C: Succeeded            AWAITING_RESUME
    │ Resume(Skip B, resolution=advance/complete)
    ▼
next Pending Frontier | COMPLETED
```

### 15.4 Nested child 阻塞普通 sibling

```text
A: Pending ordinary node
B: Pending nested node -> ActiveChild
    │ prepare
    ▼
WaitingForChildren(B)

不 claim A；child terminal 后再对当前全部 Pending nodes 统一 admission/claim。
```

### 15.5 同一 activation 再次 interrupt

```text
A: Interrupted(identity-1, request-1)
    │ ResumeInterrupted A
    ▼
A: Pending(Override(input))
    │ execute -> NodeInterrupt again
    ▼
A: Interrupted(identity-2, request-2)

identity-1 已消费且不保留；identity-2 由新 attempt generation 派生。
```

## 16. 验收标准

至少覆盖：

1. 从多个 Failed 中只 resume 一个，其他 Failed 原样保留；
2. `Pending + Failed` 合法且 prepare 只 claim Pending；
3. `Pending + Interrupted` 合法且未解决 interrupt 不进入 claim；
4. 无 Pending、有 Failed/Interrupted 时返回 `AwaitingResume`；
5. 一个 resume command 原子执行 failure resume、skip 和 interrupt resume；
6. 任一 action 非法时整组不提交；
7. skip Pending、Succeeded、Interrupted、Skipped 均被拒绝；
8. resume action 与目标 settlement 类型不匹配时被拒绝；
9. stale revision 与错误 interrupt ID 被拒绝；
10. direct node skip 使用 Continue；conditional node skip 必须选择合法 route；
11. 非法 skip routing 在提交前被唯一 routing validator 拒绝；
12. Skipped 保留 source failure 和 reason，不产生 output；
13. skip 最后一个 Failed 时必须在同一命令中 advance/complete，不提交长期 SETTLED snapshot；
14. node interrupt 与同批 success/failure 一起原子 settlement；
15. 同一 Frontier 多个 node interrupt 可按 ID 选择性 resume；
16. input override 只投递给匹配 activation，成功 settlement 后消费；
17. interrupt resume attempt 遇到 Python exception 时不 settlement，fence 后同一 override 仍可再次执行；
18. 已成功或已跳过 sibling 在其他 node resume 时不重跑；
19. 存在 Failed/Interrupted 时不提前应用 routing/join；
20. 全部 Succeeded/Skipped 后只 routing 一次并原子 advance/complete；
21. active lease/resource 时 resume 与 abort 均被拒绝；
22. exact fence 后可以 resume、重新 prepare 或 abort；
23. loop 创建新 superstep且不复制旧 settlement/input binding；
24. nested child interrupt 恢复同一个 child run；
25. GraphRun-level interrupt authoritative path、`SUSPENDED`、旧 resume command 和兼容 alias 全部删除；
26. ordinary Python exception、contract violation 和 infrastructure error 不伪装成 Failed 或 Interrupted settlement；
27. Failed resume 可以选择默认 input 或只覆盖该节点的 input；
28. interrupt resume 必须携带 override，且同一 payload 不保存为第二份 resolution；
29. 同一 batch 中不同 Pending 节点可以分别使用默认 input 和不同 override；
30. override 不广播给 sibling，成功 settlement 后消费，exception + fence 后保留；
31. codec identity/version mismatch 或 decode error 整批不 settlement；
32. 新 superstep 不继承旧 activation 的 override；
33. interrupt identity 由 `(run_id, superstep, node_id, execution generation)` 唯一派生，stale/wrong identity settlement 被拒绝；
34. 当前 identity 只保存在 `InterruptedGraphNode`，resume 后随 settlement 消费且不保留 history；
35. 同一 activation 的新 attempt 可以再次 interrupt，并使用新 generation 派生新 identity；
36. 任一稳定 snapshot 每个 activation 最多一个 outstanding interrupt，且不存在 interrupt index 或 resume-value tape；
37. resume encode 失败不生成 command、不提交状态且无 lease；
38. claim 前 codec mismatch/decode/input validation 失败不生成 claim；
39. claim 后 decode/infrastructure error 保留 exact lease，停止后 fence；
40. GraphRun operator pause、SUSPENDED、旧顶层 interrupt commands/exports/tests 全部删除；
41. ordinary Pending 与 ActiveChild 并存时返回 WaitingForChildren，不部分 claim ordinary sibling；
42. MissingChild 优先 start，ActiveChild 其次 wait，children terminal 后才 admission/claim；
43. ABORTED retained override 不 decode、不投递、不 routing；
44. interrupt request payload 与 resume input payload 分离，不发生隐式复用。
45. interrupt outcome generation 与当前 exact lease generation 不一致时，settlement reducer 拒绝整批；
46. interrupt outcome 的 run/superstep/node 坐标错误时，settlement reducer 拒绝整批；
47. recovered Interrupted identity generation 为 0、负数或大于 execution sequence 时，snapshot validator 拒绝；
48. recovered identity generation 小于当前 execution sequence 时，只要其他当前不变量合法，snapshot 可以通过；
49. 后续 attempt 增加 execution sequence 后，先前 sibling 的 current Interrupted identity 仍合法且可以 resume；
50. Resume reducer 使用当前 Interrupted identity 的唯一投影 ID，拒绝 stale、wrong 或已消费 ID；
51. stable snapshot validation 不重新证明历史 lease 来源，也不为此引入 lease/interrupt history 或 journal。

## 17. 实施约束与文档后续

本需求通过评审前，不修改已收敛的 failure-resume 实施基线。通过后必须同步修订
`frontier-node-resume-implementation.zh-CN.md`，不能在旧实施方案上叠加兼容分支。

修订后的实施方案必须做到：

1. 唯一 settlement union；
2. 唯一 `ResumeGraphNodes` command；
3. 唯一 routing contribution validator；
4. 唯一 execution engine；
5. 删除 GraphRun-level interrupt/resume authoritative path；
6. 删除 `Pending + Failed` 非法 invariant；
7. 增加 mixed Frontier、selective resume、skip 和 node interrupt 的 reducer、execution 与 architecture tests；
8. 将现有 resolution codec 归并为唯一 resume input codec，并按 node 物化 effective input；
9. interrupt identity 只由 state-owned activation/execution-generation 规则派生；
10. 当前 interrupt identity 只存在于 `InterruptedGraphNode`，不保存 history/counter/index/resume-value tape；
11. exact lease/interrupt identity 一致性只由 `SettleGraphExecution` reducer 在 transition-time 验证；
12. stable snapshot validator 只验证 identity 坐标、正 generation、`generation <= execution_sequence` 与唯一性；
13. 禁止为稳定 snapshot 的历史来源证明引入 lease history、interrupt history、attempt journal 或其他第二事实；
14. prepare 固定执行 MissingChild、ActiveChild、resource、full Pending claim 优先级；
15. 明确删除 operator GraphRun pause 的所有 production path、exports、tests 和文档；
16. 不保留 alias、fallback、双写、第二套 runner 或临时迁移模型。
