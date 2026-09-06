# Think 图实施说明

状态：**v1 实现完成，本文是当前实施口径**（2026-09-06）。本文只描述 Think 自身的
组图、类型边界和节点职责；不把并行分支中的旧测试或历史评审结论当作当前 API。

## 1. 目标和硬边界

Think 对外只有一个图入口：`mote_kernel.think.ThinkNode`。它是一个普通
`execution.Graph`，可以直接交给 `Graph.run()`，也可以作为一个 nested node 放入未来的
ReAct 图。Graph 是唯一的组图和执行引擎，`GraphRunState` 是唯一运行时状态模型。

Think 固定包含五个业务节点：

1. `prompt`：通过一个 `PromptPort` 依次获取 system prompt、placeholder 和 user prompt；
2. `context`：获取本轮历史/上下文快照；
3. `compact`：按外部规则管理并压缩上下文；
4. `inference`：在 `command` 之前组装最终模型请求、调用模型并得到归一化结果；
5. `command`：把 inference 结果结构化为类型化 command/turn。

这五个节点都是真正的 Graph node。对于 Think 这条固定业务链，每个业务节点完成后都进入
同一个共享 `HookNode`；普通 Graph 没有 Hook 需求时不应被强制插入 Hook。

下列内容不属于 Think：

- 持久化、checkpoint、恢复、reconcile 和第二套状态模型；
- Port 的非幂等副作用、重试和 Failover 策略；
- Hook 的 Plan/P1/P2/P3 实现、Hook command 的解释或提交；
- Graph command、Store 写入、工具执行、最终回答渲染和 ReAct 总路由。

这些职责由对应 owner 统一提供。Think 只接收已经装配好的 capability，并按固定 DTO/Graph
边界传递。

## 2. 实际拓扑

Think 使用一个共享 Hook，而不是为五个业务阶段各创建一个 Hook：

```text
START
  |
prompt ───────┐
context ──────┤
compact ──────┤──> hook (一个共享 HookNode)
inference ────┤       |
command ──────┘       |
                       +-- route="prompt"   -> context
                       +-- route="context"  -> compact
                       +-- route="compact"  -> inference
                       +-- route="inference"-> command
                       +-- route="command"  -> END
```

`route` 在这里是 Hook completion 携带的 route token，不是额外的 callable node。Graph 的
三参数 `add_edge(source, route, target)` 直接声明条件边，父图消费的是 Hook 的终端 route。
因此 Think definition 的直接节点数是 **6 个**：五个业务 callable node 加一个共享 nested
`HookNode`；没有 `route` 节点，也没有第六个业务阶段。

一次成功运行的控制顺序是：

```text
prompt -> hook(prompt) -> context -> hook(context) -> compact
       -> hook(compact) -> inference -> hook(inference) -> command
       -> hook(command) -> END
```

同一个 Hook 实例会被激活五次。Hook 子图内部仍由 Hooks owner 定义的
`Plan -> P1 -> P2 -> P3` 负责执行；这四个内部节点不计入 Think 的直接节点。

## 3. 数据流和 typed boundary

### 3.1 Think value 链

所有业务事实都放在不可变 `ThinkFrame` 中，不创建 ThinkState、reducer、缓存或并行事实表。
`ThinkFrame.step` 只能是 `PromptStep`、`ContextStep`、`CompactStep`、`InferenceStep`、
`CommandStep` 五个 closed nominal variant 之一：

```text
PromptStep    = PromptFrame
ContextStep   = PromptFrame + ContextFrame
CompactStep   = PromptFrame + ContextFrame + CompactedContext
InferenceStep = PromptFrame + CompactedContext + InferenceResult
CommandStep   = PromptFrame + CompactedContext + InferenceResult + ThinkCoreResult
```

每个业务节点读取前一次 Hook 发布的 frame，生成下一种 step，并保留同一个
`hook_state`。Hook command 始终作为不透明的 `HookResult.commands` 透传；Think 不累计、
解释、apply 或转换它。

### 3.2 共享 Hook 的输入

五个业务节点都发布同名 `hook_request`。共享 Hook 的 `request` 输入使用 Graph 的
predecessor-bound 引用：

```text
prompt.hook_request ─┐
context.hook_request ─┤
compact.hook_request ─┼─> hook.request (actual predecessor)
inference.hook_request┤
command.hook_request ─┘
```

Graph compiler 会为每次激活选择实际控制前驱，并检查各候选 output 的运行时 value class
一致。不能把 Hook 固定绑定到 `prompt` 的 output，否则后续激活会重复消费旧 frame。

### 3.3 Nested typed output handle

Graph 的 typed output descriptor 是组装时分配的对象，不能在消费者处用相同的 Python class
重新制造 descriptor。共享 `HookNode` 是其 P3 output 的 owner，构造完成后保存真实 handle：

```python
hook.result_output
```

当它嵌入父图的某个节点时，使用：

```python
hook.output_ref("hook")
```

这个方法只替换父图中的 node identity，保留 child P3 output 的 exact descriptor。Think、Act
和 Observe 都通过这个边界连接共享 Hook；消费者禁止自行调用
`canonical_nominal_type(HookResult)` 生成第二份 descriptor。Graph compiler 的 descriptor
identity 检查继续保留，伪造 handle 应在编译期失败。

Observe 的第二次 Hook 激活仍使用 typed predecessor handle（而不是固定读取第一次
`result` publication），从而保持两个 activation 的数据因果关系。

### 3.4 Graph.Values 的 materialize 路径

节点 DTO 不从普通 mapping 直接猜类型。typed Graph API 的路径是：

```text
Graph.bind(source)
  -> TypedInputBinding(destination descriptor, source descriptor)
  -> Graph.add_node(... input_type, materialize, output_type)
  -> NodeInputs.get(binding)
  -> graph.values.admit_exact(value, descriptor)
  -> NodeOperation(具体 DTO)
  -> typed output publisher
```

`graph.values._frame_value_typed()` 是端口级 materialize 的窄入口；它使用现有
`NominalTypeDescriptor` 做 `type(value) is descriptor.value_type` 检查，不从运行时值反推泛型。
静态泛型关系由 `NodeContract` 保留到执行边界，运行时只在少数 descriptor/admission 位置做
exact 检查。业务节点内部不应到处写无关的 `cast`，更不能用 `object`/`Any` 绕过边界。

## 4. 五个业务节点

### 4.1 Prompt

`PromptNode` 只接收一个 `PromptPort`，在一次 activation 内严格按以下顺序调用一次：

```text
load_system_prompt(request.payload)
load_placeholder(request.payload)
load_user_prompt(request.payload)
```

三个结果组成 `PromptFrame -> PromptStep -> ThinkFrame -> HookRequest(node_id="prompt")`。
Prompt 不读取历史、不压缩上下文、不调用模型、不结构化 command。

### 4.2 Context

`ContextNode` 接收 graph input 的 `ThinkRequest` 和 predecessor-bound 的
`HookResult[ThinkFrame[PromptStep, ...], ...]`，构造一个 `ContextRequest`，调用一次
`ContextPort`，再发布 `ContextStep`。它不再次调用 Prompt，也不维护 history store。

### 4.3 Compact

`CompactNode` 接收 `ContextStep`，构造一个 `CompactRequest`，调用一次 `CompactPort`，
发布 `CompactedContext` 和 `CompactStep`。它不修改原始 `ContextFrame`，也不以未压缩数据
偷偷 fallback。

### 4.4 Inference

`InferenceNode` 接收 `CompactStep`，用构造时捕获的 immutable `ModelBinding` 组装最终
`InferenceRequest`，调用一次 `InferencePort`，发布 `InferenceResult` 和 `InferenceStep`。
它是唯一的模型调用/请求定稿节点；`ModelBinding` 不负责 resolver、client、凭据或缓存。

### 4.5 Command

`CommandNode` 接收 `InferenceStep`，调用一次 `CommandPort.build_command()`，发布
`ThinkCoreResult` 和 `CommandStep`。它不执行工具、不路由 Act、不提交状态，也不解释 Hook
commands。

## 5. Port、Invocation 和 Failover

### 5.1 Port 形状

业务 Port 是五个 capability：一个三方法 `PromptPort` 加四个阶段 Port。每个节点只依赖自己
的具名 Port；每个 Port 在其内部持有一个 typed `Invocation` adapter：

```text
Think node
  -> node-local typed Port
  -> mote_kernel.invocation.invoke_typed()
  -> Invocation.invoke() exactly once
```

`think/port.py` 的 adapter 只负责把 DTO request 交给正式 Invocation boundary，并接收已准入
result。它不实现 transport、retry、Failover、state write 或 Graph outcome。调用方异常和
`CancelledError` 原样传播；只有明确识别为 Invocation boundary admission failure 时才转换为
对应的 Think/Hook contract error，不能通过检查 `__cause__` 猜异常来源。

### 5.2 Failover 注入位置

外部 composition 的顺序固定为：

```text
基础 typed Port -> 外部 Failover 装饰 -> ThinkNode
```

Think 不包整个 Graph、ThinkNode、Hook 子图或 `Graph.run()`，也不实现重试/非幂等策略。普通
不需要 Failover 的图不应因为 Think 的存在而新增隐藏 wrapper。

### 5.3 DTO 校验 owner

`mote_kernel.invocation` 统一负责 Invocation request/result 的 outer exact class admission。
具体 DTO 的字段、递归不可变性和业务不变量由 DTO/Port/Hooks owner 的构造或 admission 负责。
Think 只做自己不可避免的 closed step、node provenance 和 Graph descriptor 边界检查，不复制
通用网络校验算法。

## 6. Hook 挂载契约

composition root 传入一个已经组装好的真实 `HookNode`，slot 必须匹配 Think 的：

```text
definition_id == Think definition_id
definition_version == Think version
node_id == "hook"
stage == HookStage.AFTER_NODE
```

检查在 Think 第一次 `Graph.add_node()` 前完成。Think 不读取 Hook 私有 builder state，不复制
Hook 的 Plan/P1/P2/P3 或 payload admission。共享 Hooks 层公开 immutable `payload_admission`
和 `output_ref()`，供需要知道边界的组合 owner 复用；不知道具体 state/command 类型的消费者
不应自行猜测泛型参数。

Hook P3 返回：

```text
HookResult(value=ThinkFrame, commands=opaque tuple, node_id=current business node)
```

`node_id` 是父图条件边使用的 route token。Hook 只报告来源，不拥有父图拓扑；Think 自己声明
`prompt/context/compact/inference/command` 五个 conditional target。若通用 Hook request 没有
`node_id`，Hook 可作为独立 Graph 正常结束，但 Think 的五个业务 request 必须提供 canonical
node id。

## 7. 文件布局和唯一公共 API

Think 目录保持扁平，不保留阶段子包：

```text
think/
├── __init__.py       # 唯一包级公共图 API：ThinkNode
├── node.py           # ThinkNode 总装配
├── contract.py       # Think DTO、step、Port Protocol
├── prompt.py         # 唯一公共符号：PromptNode
├── context.py        # 唯一公共符号：ContextNode
├── compact.py        # 唯一公共符号：CompactNode
├── inference.py      # 唯一公共符号：InferenceNode
├── command.py        # 唯一公共符号：CommandNode
└── port.py           # Invocation adapter；不作为包级公共 API
```

`mote_kernel.think.__all__ == ["ThinkNode"]`。五个职责模块的 `__all__` 只包含对应图节点；
DTO/Protocol 可以被节点和 composition 的类型注解引用，但不形成第二个 runner、builder、
manager、registry 或兼容 alias。`HookNode` 的包级公共 API 仍由 `mote_kernel.hooks` 单独
拥有，Think 不重新导出它。

## 8. 失败、取消和编译边界

- 业务 Port、Hook priority 或 typed admission 抛出的异常停止当前 Graph activation；Think
  不伪造空 frame/result，也不自动重试。
- `Invocation` 自身抛出的普通异常和取消保持原对象/语义传播；`invoke_typed()` 只把自己在
  request/result admission 阶段产生的内部 `InvocationBoundaryAdmissionError`（它是
  `InvocationBoundaryError` 的专用子类）交给 Port 转换。Invocation 实现即使主动抛出公开的
  `InvocationBoundaryError`，也必须原对象穿透，Port 不依据 `__cause__` 猜测来源。
- Graph 的 caller/root/nested cancellation、cleanup、fence 和父 scope 投影由 execution
  owner 决定；Think 不创建 cancellation table 或后台 task。
- Think 不安装持久化/recovery codec，也不把 Hook command 变成 Graph command。
- 首次成功 compile 后沿用 Graph mutation guard；Think 不另建 seal。编译时若 nested output
  handle 的 descriptor 不是 child 声明的同一对象，compiler 必须拒绝。
- `ThinkFrame` 遇到未知 `ThinkStep` 子类、错误来源 node id 或非法 outer DTO 时 fail closed，
  不能默认推进下一阶段。

## 9. 实施状态和验收

### 9.1 已完成的生产实现

- 五个平铺业务 node 和唯一包级 `ThinkNode`；
- 一个共享真实 `HookNode`，五次 predecessor-bound 激活；
- Hook route token 直接驱动 Graph conditional edges，无 route callable；
- Prompt 单 Port 三次有序收集；Context/Compact/Inference/Command 各一次调用；
- Graph typed `bind/materialize/publish` 路径及 `graph.values.admit_exact`；
- Hooks child-owned P3 descriptor、`result_output`、`output_ref(parent_node_id)`，并接入
  Think/Act/Observe；
- Invocation typed adapter 的显式 boundary error 转换和原始 Invocation 异常/取消传播；
- closed ThinkStep admission、slot/definition/version/node/stage assembly checks；
- nested Graph terminal result 和并发 run 的 isolation。

### 9.2 必须保持的测试门禁

专项验证至少覆盖：

- 五阶段顺序、每个 Port 调用次数和 Prompt 三方法顺序；
- 五次共享 Hook 激活、每次 request 的实际 predecessor、最终 command route；
- output descriptor identity：child 真实 P3 handle 可在多个 parent node 下复用，伪造 descriptor
  编译失败；
- wrong outer DTO、unknown step、wrong Hook slot/kind、缺 capability、mutation guard；
- Port/Hook 普通异常、Invocation boundary admission error、caller/root/nested cancellation；
- nested Think 只暴露 `request/result`，父图不能绑定 Think 内部节点。

当前已验证的专项命令：

```text
python -m pytest tests/hooks tests/think/test_graph.py tests/act/test_nodes.py tests/observe -q
python -m pytest tests/execution tests/observe -q
python -m pytest tests/architecture/test_generic_integrity.py -q
python -m pyright src/mote_kernel/hooks src/mote_kernel/think src/mote_kernel/act src/mote_kernel/observe
python -m ruff check src/mote_kernel/hooks src/mote_kernel/think src/mote_kernel/act src/mote_kernel/observe
```

上述专项路径在当前工作树通过。全仓测试中仍可能出现两类范围外结果：一是未迁移的旧
`tests/think/test_nodes.py` 直接把 `Graph.Values` 当作节点 DTO 传入；二是并行 execution/Act/
Observe 改动带来的 complexity/package 门禁。它们不能通过恢复旧生产兼容路径来掩盖；若要纳入
全仓绿，应单独把旧测试迁移到 typed node API，或由其 owner 收口混合工作树。

## 10. 父图接入示例

父图只把 Think 当作一个 nested node：

```python
react.add_node(
    "think",
    think,
    inputs={"request": Graph.graph_input("turn_request", ThinkRequest)},
)
react.add_edge("think", "command", "next")
```

父图消费 Think 的 `result` boundary；ReAct 是否进入 Act、循环或结束由父图声明。若父图的
request/result 不是同一个 exact nominal class，必须在父图 assembly 放置显式 typed adapter，
不能用 `cast`、`object`、裸字典或隐式转换绕过 compiler。父图不绑定 Think 内部的五个业务
node 或 Hook，也不创建第二个 runner/state owner。

本文止于 Think 作为一个可复用 nested Graph 的稳定边界；ReAct/Act 总拓扑和统一持久化另行
实施。
