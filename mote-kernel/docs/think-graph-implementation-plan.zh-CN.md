# Think 图实施计划

状态：**拓扑订正后的实施方案**。本稿以现有 Graph 路由能力为准：Think 只有五个业务
阶段，一个共享的 HookNode，以及一个只负责条件路由的 route 控制节点。核心节点完成后
都跳转到同一个 Hook 实例；不为每个核心节点另建 HookNode。

编码进度：五个阶段、共享 Hook、route 和唯一 `ThinkNode` 已完成 v1 实现；专项测试覆盖
平铺模块、七节点拓扑、nested 边界、value 链及异常/取消传播。Think 不实现 Invocation、
Failover、Hook command 解释或持久化，这些仍由既定外部 owner 负责。

本文只规划 src/mote_kernel/think/ 的 v1 实现，不修改 Graph/State 执行引擎，不修改
src/mote_kernel/invocation.py 或其测试，也不实现 Hooks 内部、Failover、持久化或恢复。所有
业务 Port 在外部 assembly 注入 Think 前必须先套上对应的 Failover 装饰；Think 只消费装饰
后的同一 typed Port，不实现装饰器或重试策略。Graph 仍是唯一的组合与执行门面，
GraphRunState 仍是唯一运行时状态模型。Think 作为一个
Graph[HookGraphValue]，既可直接由 Graph.run() 执行，也可作为一个 nested node 嵌入未来
的 ReAct 图。

## 1. 结论与固定拓扑

Think 的五个业务阶段固定为：

1. prompt：获取 system prompt、placeholder 内容和 user prompt；
2. context：获取本轮历史及上下文快照；
3. compact：按 token/window 规则管理并压缩上下文；
4. inference：在 Command 之前完成最终请求组装、模型调用和结果归一化；
5. command：把归一化的 inference 结果结构化为类型化 command/turn。

Hook 拓扑不是五个后置节点，而是一个可重复激活的共享 nested graph。Graph 的控制形状为：

~~~text
START → prompt ───────────────┐
                              ▼
                 ┌────────── hook（一个共享 HookNode）
                 │              │
                 │              ▼
                 │             route（条件路由）
                 │              ├─ context ───┐
                 │              ├─ compact ───┤
                 │              ├─ inference ─┤
                 │              ├─ command ───┤
                 │              └─ finish → END
                 │
                 └── context / compact / inference / command 均直接回到 hook
~~~

线性执行时等价于：

~~~text
prompt → hook → route(context)
context → hook → route(compact)
compact → hook → route(inference)
inference → hook → route(command)
command → hook → route(finish) → END
~~~

因此 Think definition 有七个直接节点：五个业务 callable node、一个共享 HookNode nested
node 和一个 route callable node。七个是图实现节点数，五个才是业务阶段数；route 不是
第六个业务阶段，Hook 内部的 Plan → P1 → P2 → P3 也不属于 Think 直接节点。共享 Hook
在一次 Think activation 中被激活五次，始终是同一个装配实例和同一条 nested Graph 定义。

route 节点是不可省略的最小控制节点：现有 HookNode 的输出是 HookResult，不能直接选择
Graph 条件边；route 从 HookResult.value 取出当前 frame，按 frame 的 nominal step
选择下一个核心节点或 END。它不解释 Hook command、不聚合状态，也不执行业务逻辑。

对外唯一 Think 图 API 仍为 mote_kernel.think.ThinkNode。父图只看见一个 think nested
node，不看见五个核心 node、共享 Hook 或 route node。

## 2. 范围

### 2.1 本轮必须交付

- 在 src/mote_kernel/think/node.py 组装上述固定拓扑；所有五个核心节点都直接连到同一个
  HookNode；
- 使用 Graph 已有的 predecessor-bound output（Graph.node_output("hook_request")）将
  多个核心节点的同类型 hook request 安全送入共享 Hook；
- 用一个不可变 ThinkFrame 携带当前阶段的 nominal step 和已产生的业务事实；它是 Graph
  value，不是第二个 ThinkState、reducer 或 store；
- 让 route 只依据 step 类型选择 context/compact/inference/command/finish，并把同一次
  Hook 的 HookResult 原样作为 result output 继续传递；
- 以窄的、类型化 Port 注入五项能力：一个包含三个收集方法的 PromptPort，以及 Context、
  Compact、Inference 和 Command 四个阶段 Port；每个具体 Port 在外部 assembly 注入前套上
  Failover，核心节点只调用装饰后的具名 Port；
- 保持固定 DTO、模型 binding、Invocation typed boundary、失败/取消和 nested Graph 契约；
- 为共享 Hook 的重复激活、路由、value 链和最终 output 增加确定性测试。

### 2.2 明确不做

- 不增加 Interpret、Act、工具执行、最终回答渲染或发送节点；
- 不为 prompt/context/compact/inference/command 各创建一个 HookNode，也不创建
  PromptHook、ContextHook 等并列节点/参数；
- 不在 route 中实现 Hook manager、dispatcher、command delivery、聚合器或私有 runner；
- 不复制 Hook 的 Plan/P1/P2/P3、payload admission、内部拓扑或 command 语义；
- 不实现 Think 专用持久化、checkpoint、continuation、恢复、重试、failover 或非幂等副作用；
- 不维护 ThinkState、history store、缓存、第二个 reducer，或直接修改 GraphRunState；
- 不实现通用 DTO 校验算法。request/result 的统一 exact admission 由外部
  mote_kernel.invocation typed boundary 提供；字段和递归 data-only 不变量由 DTO/Port owner
  的构造或 admission 提供；
- 不把领域 command 转换为 GraphRunCommand，不提交 Store，不执行或 apply Hook commands。

持久化与恢复后续由统一执行引擎任务处理；非幂等外部行为、重试和 Failover 由外部 Port/任务
处理。本稿只冻结 Think 的图边界和必要的 typed handoff。

## 3. Graph 拓扑与数据绑定

### 3.1 共享 Hook 的关键机制

Graph 已支持 predecessor-bound input：当一个目标节点有多个控制前驱，
Graph.node_output("hook_request") 会根据本次 activation 的实际 predecessor 选择该前驱
发布的同名 output。Think 利用这一机制把五个核心节点的同类型 hook_request 绑定到一个
共享 Hook：

~~~text
prompt.hook_request ─┐
context.hook_request ─┤
compact.hook_request ─┼─> hook.request（predecessor-bound）
inference.hook_request┤
command.hook_request ─┘
~~~

不能把五个 output 绑定到五个不同 Hook，也不能用一个固定的 prompt output 让共享 Hook
重复消费旧值。Graph compiler 会验证目标有单一控制因果、所有前驱 output descriptor 相同，
并在运行时按实际 predecessor 选取 publication。

共享 Hook 的 value 类型是 Think 自己的一个固定 nominal ThinkFrame，而不是五种不同的
PromptFrame/ContextFrame 泛型实例。ThinkFrame.step 使用私有、不可变的 nominal step
变体表达当前阶段；不使用裸字典、字符串 tag 或宽 object envelope。Hook 的外层仍是现有
HookRequest/HookResult class：

~~~text
HookRequest[ThinkFrame, HookState]
    → 一个共享 HookNode（Plan → P1 → P2 → P3）
    → HookResult[ThinkFrame, HookCommand]
~~~

Hook 返回的 HookResult.value 可以是该同一 ThinkFrame nominal class 的合法修订值；route
读取修订后的 step/facts 决定下一条边。HookResult.commands 对 Think 保持不透明：非终端
激活只把 value 继续交给 route，终端激活的完整 result 通过 Graph output 原样返回。

### 3.2 固定节点、输入和输出

内部节点 ID 固定为：

~~~text
prompt, context, compact, inference, command, hook, route
~~~

图输入和输出固定为：

~~~text
graph input : request : ThinkRequest
graph output: result  : HookResult[ThinkFrame, HookCommand]
~~~

HookCommand 是 composition/Hooks owner 为共享 Hook 选择的一个具体 nominal command 类型；
Think 不定义、解释或聚合它。五个阶段的业务 command 若类型不同，应在 Hooks/外部 owner 的
contract 中封装为该共享 Hook 能接受的类型，而不是在 Think 中增加五个 Hook。

route 只声明一个 output：

~~~text
hook_result : HookResult
~~~

它对每次 Hook 激活都原样发布 HookResult。后续核心节点使用一参数的
Graph.node_output("hook_result") predecessor reference 取得本次 route activation 的结果，
再从 HookResult.value 读取 ThinkFrame。Graph 的唯一 output 绑定到
route.hook_result；编译器会选择完成 route --finish--> END 的那次 activation，而不会把前
四次中间 Hook result 暴露给父图。

### 3.3 组装伪代码（现有 Graph API）

以下伪代码只描述必须冻结的 bindings/edges；具体 callable 和 DTO 构造在后续阶段实现。
hook 是 composition root 预先装配的**一个**真实 HookNode。

~~~python
from mote_kernel.hooks.contract import HookRequest, HookResult

request = graph.graph_input("request", ThinkRequest)
hook_request_type = HookRequest
hook_result_type = HookResult

graph.add_node(
    "prompt",
    prompt_node,
    inputs={"request": request},
    outputs={"hook_request": hook_request_type},
)
graph.add_node(
    "context",
    context_node,
    inputs={
        "request": request,
        "hook_result": graph.node_output("hook_result"),
    },
    outputs={"hook_request": hook_request_type},
)
graph.add_node(
    "compact",
    compact_node,
    inputs={"hook_result": graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)
graph.add_node(
    "inference",
    inference_node,
    inputs={"hook_result": graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)
graph.add_node(
    "command",
    command_node,
    inputs={"hook_result": graph.node_output("hook_result")},
    outputs={"hook_request": hook_request_type},
)

# One nested HookNode, one predecessor-bound input; no prompt_hook/... parameters.
graph.add_node(
    "hook",
    hook,
    inputs={"request": graph.node_output("hook_request")},
)
graph.add_node(
    "route",
    route_node,
    inputs={"result": graph.node_output("hook", "result")},
    outputs={"hook_result": hook_result_type},
)

for node_id in ("prompt", "context", "compact", "inference", "command"):
    graph.add_edge(node_id, "hook")
graph.add_edge("hook", "route")
graph.add_conditional_edge("route", ThinkRoute.CONTEXT.value, "context")
graph.add_conditional_edge("route", ThinkRoute.COMPACT.value, "compact")
graph.add_conditional_edge("route", ThinkRoute.INFERENCE.value, "inference")
graph.add_conditional_edge("route", ThinkRoute.COMMAND.value, "command")
graph.add_conditional_edge("route", ThinkRoute.FINISH.value, Graph.END)
graph.set_outputs({"result": graph.node_output("route", "hook_result")})
~~~

prompt 只有 graph input、没有 incoming control edge，因此由 compiler 推导为唯一 automatic
entry；不得额外调用 graph.add_edge(Graph.START, "prompt")。其余四个核心节点只能由
route 的对应 conditional edge 激活。每个核心节点都必须有一条直接边到 hook，hook 只
有一条直接边到 route；不能以五条独立 Hook 链替代这组边。

### 3.4 Frame 与 route 的纯职责

ThinkFrame 是不可变 Graph value，至少包含一个 step 变体和本次 activation 的
hook_state projection。step 变体按 nominal 类型携带已经产生的事实，概念上为：

~~~text
PromptStep    { prompt: PromptFrame }
ContextStep   { prompt: PromptFrame, context: ContextFrame }
CompactStep   { prompt: PromptFrame, context: ContextFrame,
                compacted: CompactedContext }
InferenceStep { prompt: PromptFrame, compacted: CompactedContext,
                inference: InferenceResult }
CommandStep   { prompt: PromptFrame, compacted: CompactedContext,
                inference: InferenceResult, core: ThinkCoreResult }
~~~

这些是实施时的 owner-internal nominal classes/协议，不是可变 state store。每个核心 callable
只接受 route 发布的前一份 HookResult，exact-admit 后读取其 value，检查自己期望的前一
step，构造下一 step，并把它与同一 hook_state 封装为 HookRequest(ThinkFrame, hook_state)。
核心节点不能读取自己之前的原始 output；一参数 Graph.node_output("hook_result") 保证它
消费的是本次实际 route predecessor 的 publication。

`ThinkFrame` 只接受 `contract.py` 明确声明的 closed nominal step variant。当前增量实现的
集合只有 `PromptStep`；后续四个阶段完成时由同一 contract owner 显式加入各自 concrete
variant。消费者自定义的 `ThinkStep` 子类不得进入 Graph value，也不通过字符串 tag 或
运行时注册表放宽集合。

route 的唯一业务无关逻辑是 step-to-route 映射：

~~~text
PromptStep    → CONTEXT
ContextStep   → COMPACT
CompactStep   → INFERENCE
InferenceStep → COMMAND
CommandStep   → FINISH
~~~

route 先 exact-admit HookResult，并校验 HookResult.value 的 step 与固定的合法转移一致，
然后以 Graph.values(hook_result=result) 原样发布 result，再返回对应的 conditional route。
映射使用 nominal isinstance/模式匹配，不使用字符串 discriminator。Graph API 所需的
conditional route 字符串只由固定 ThinkRoute 枚举提供。未知或不允许的 step 是
ThinkContractError/TaskRaised，不能默认跳到下一阶段。

## 4. 五个核心节点契约

### 4.1 Prompt

Prompt 是唯一的 graph entry，在一个 callable activation 内按固定顺序各调用一次注入的
单一 node-local typed `PromptPort`：

1. `PromptPort.load_system_prompt(payload)`；
2. `PromptPort.load_placeholder(payload)`；
3. `PromptPort.load_user_prompt(payload)`。

三项结果组成不可变 PromptFrame，再组成 PromptStep 和 ThinkFrame 交给共享 Hook。Prompt
不读取历史、不计算 token window、不调用模型、不执行 command。三个方法属于同一个
PromptPort 对象，均只接收 ThinkRequest.payload，不能隐式读取 hook_state。该对象由外部
assembly 先套上 Port-level Failover 后再注入 PromptNode；PromptNode 不创建或判断
Failover。

### 4.2 Context

Context 接收 graph input 的 request 和 predecessor-bound 的 HookResult（其 value 是包含
PromptStep 的 ThinkFrame），构造单一 frozen ContextRequest，调用一次 ContextPort，生成 ContextFrame
和 ContextStep，再跳转共享 Hook。它不再次调用 PromptPort，不创建 history store；无历史
时由装配层提供明确的空 snapshot capability。

### 4.3 Compact

Compact 接收 predecessor-bound 的 HookResult（其 value 是 ContextStep），读取其中的 PromptFrame 和 ContextFrame，构造
CompactRequest，调用一次 CompactPort，生成 CompactedContext 和 CompactStep。它不
修改 ContextFrame、不回读历史、不把 token 计数写入 Think 可变字段，也不能以未压缩上下文
作为 Inference fallback。

### 4.4 Inference

Inference 接收 predecessor-bound 的 HookResult（其 value 是 CompactStep），在一个 activation 内完成：

~~~text
PromptFrame + CompactedContext
    → 解析 placeholder
    → 组装最终模型请求
    → 使用装配时捕获的 immutable ModelBinding
    → 调用一次 InferencePort
    → 得到归一化 InferenceResult
~~~

它是唯一的模型调用和最终请求定稿节点，不能拆出 Render/Prepare 节点，不能使用未压缩的
ContextFrame。ModelBinding 不携带 client、Invocation、resolver、registry、凭据、连接或
缓存；这些 capability 由 InferencePort/assembly 持有。

### 4.5 Command

Command 接收 predecessor-bound 的 HookResult（其 value 是 InferenceStep），调用一次 CommandPort 生成 ThinkCoreResult 和
CommandStep，再交给共享 Hook。它只负责结构化 typed command/turn，不解释 thinking、不执行
工具/Act、不路由 ReAct、不提交状态、不再次调用模型。终端 route 的 result 仍是共享 Hook
返回的 HookResult；Think 不把其中 command 转成 Graph command。

所有五个核心 callable 均遵守现有 Graph 边界：

~~~text
async (Graph.Values[HookGraphValue])
    -> Graph.Values[HookGraphValue] | Graph.Outcome
~~~

不能返回裸 DTO、同步值或未声明的 union。v1 不安装 resume-input codec，核心节点不返回
Graph.interrupt。

## 5. 共享 Hook 挂载契约

### 5.1 一个 Hook、一个 slot

composition root 只向 ThinkNode 传入一个已装配的真实 HookNode 参数 hook。该 Hook 的
payload admission 必须由 Hooks/composition owner 配置为：

~~~text
value  : ThinkFrame
state  : ThinkRequest.hook_state 的 concrete nominal type
command: 一个共享的 Hook command nominal type
~~~

Think 不读取 Hook 私有 HookPayloadAdmission，不复制 Plan → P1 → P2 → P3，也不为五个
阶段建立五套 value/state/command descriptor。Hooks owner 负责确保共享 Hook 能处理五种
step；每次 Hook activation 只能修订当前 step 的 payload，必须保持 step 的 nominal kind、
已经产生的前置事实和 hook_state 不变。若不同阶段的 command 需要不同领域类型，由 Hooks/
外部 owner 提供共同 envelope。上述 payload/step 保持规则由 Hooks owner admission 负责，
不是 Think 复制一套 Hook 校验算法。

Think 构造期仍必须做最小的 kind/slot 装配门禁，且在第一次 Graph.add_node() 前完成：

1. type(hook) is HookNode；普通 Graph、普通 callable 和未经 owner 授权的替代对象拒绝；
2. hook.slot 是 HookSlotId，且 definition_id、definition_version 与 Think 自身完全
   相同，node_id == "hook"，stage is HookStage.AFTER_NODE；
3. 通过后才把这个唯一 child 写入 builder，并按第 3.3 节加入 control/data edges。

这不是五个 slot 检查，也不存在交换/重复挂载检查；只有一个共享 slot。Think 不声称由 kind/
slot 检查证明 Hook 内部 topology、payload admission 或 handoff 后 mutation。Hooks/composition
owner 必须在交给 Think 后到首次成功 compile 前不再调用其 builder mutation；若未来需要可验证
seal，另行由 Hooks/execution owner 设计。

### 5.2 Hook value/state/commands 边界

- value：Hook 接收并返回 ThinkFrame；route 和后续核心节点只使用成功 HookResult.value；
- state：五次激活均使用同一 activation 输入中的 immutable hook_state projection；每次
  HookRequest.state 必须与其 ThinkFrame.hook_state exact 相等。Think 不读取或修改
  GraphRunState，不把 projection 当第二状态真相；
- commands：Think 不读取、累计、去重、投递、执行或 apply。route 在继续路径只转发
  HookResult.value，同时将当前 HookResult 原样作为自己的 hook_result output；只有最后一次
  FINISH route 的 hook_result 成为 Think graph output。

Hook 内部 P1/P2/P3 失败、普通异常和取消按现有 nested Graph 语义结束，不制造空
HookResult。Think 不增加 Hook manager、final-admission consumer 或 pre-commit consumer。

## 6. DTO、Port 与 Invocation 边界

### 6.1 v1 concrete DTO

src/mote_kernel/think/contract.py 定义 frozen=True, slots=True 的 nominal Graph values，
并直接继承现有 HookGraphValue。方括号只表示静态内层类型，Graph descriptor 使用实际
runtime class，不使用 typing alias：

| class | 字段 | owner/invariant |
| --- | --- | --- |
| ThinkRequest | payload, hook_state | ingress/composition 构造；均为已准入 immutable concrete value |
| PromptFrame | system, placeholder, user | Prompt 一次构造，三项不可缺失 |
| ContextFrame | snapshot | ContextPort 一次读取后的 immutable snapshot |
| CompactedContext | snapshot, token_count | token_count 为 exact int >= 0，不接受 ContextFrame 冒充 |
| ContextRequest | request, prompt | Context node 构造的单一 request envelope |
| CompactRequest | prompt, context | Compact node 构造的单一 request envelope |
| InferenceRequest | prompt, compacted, model | model 必须是 ThinkNode 捕获的 ModelBinding |
| InferenceResult | output | Port/adapter 归一化结果，不含原始异常/transport client |
| ThinkCoreResult | command | CommandPort 产生的结构化领域 payload，不自动 apply |
| ThinkFrame | step, hook_state | 共享 Hook 的唯一 value envelope；step 为 nominal 变体 |
| ModelBinding | provider_id, model_id, revision | 非空稳定 identity、exact 正整数 revision、无运行时句柄 |

ThinkRoute 是 contract.py 中的 closed enum，仅包含 CONTEXT、COMPACT、INFERENCE、COMMAND 和
FINISH 五个固定 token。它的字符串值只用于 Graph.add_conditional_edge；不得把自由字符串
route 或 stage discriminator 放进 ThinkFrame payload。

ThinkFrame 的 step 变体以及所有递归字段必须是 data-only immutable nominal values、标量或
明确约束的不可变 tuple；不得携带 callable、SDK client、Invocation、registry、Store、连接、
锁、task/future、Graph/Port 实例或可变缓存。frozen=True 的浅层保护不足以证明递归不变量，
因此由 concrete DTO constructor/factory 或 owner typed adapter 保证。不得使用 dict[str,
object]、Any、object bag、repr() 或字符串 discriminator。

通用职责分层如下：

| 边界 | owner |
| --- | --- |
| 固定 outer DTO、ModelBinding 字段、token_count | think.contract constructor/factory |
| payload、prompt 三项、snapshot、model output、command、hook state、step 内层 | 对应 ingress/Port/Hooks owner 的 typed constructor/admission |
| Invocation request/result outer exact type、调用顺序、单次 strict 调用 | mote_kernel.invocation typed boundary |
| Graph frame/carrier、nested input/output、publication/route | execution Graph compiler/runtime |

Think 节点不重复实现通用校验，不反射扫描对象图；被 object.__new__ 伪造的对象必须在下
一次 owner admission/读取边界失败，不能进入下游 Invocation。

### 6.2 五个窄业务 Port

~~~python
class PromptPort(Protocol[RequestT, SystemPromptT, PlaceholderContentT, UserPromptT]):
    async def load_system_prompt(self, request: RequestT, /) -> SystemPromptT: ...
    async def load_placeholder(self, request: RequestT, /) -> PlaceholderContentT: ...
    async def load_user_prompt(self, request: RequestT, /) -> UserPromptT: ...

class ContextPort(Protocol[ContextRequestT, ContextFrameT]):
    async def load_context(self, request: ContextRequestT, /) -> ContextFrameT: ...

class CompactPort(Protocol[CompactRequestT, CompactedContextT]):
    async def compact(self, request: CompactRequestT, /) -> CompactedContextT: ...

class InferencePort(Protocol[InferenceRequestT, InferenceResultT]):
    async def infer(self, request: InferenceRequestT, /) -> InferenceResultT: ...

class CommandPort(Protocol[InferenceResultT, ThinkCoreResultT]):
    async def build_command(self, request: InferenceResultT, /) -> ThinkCoreResultT: ...
~~~

这些是 capability contract，不是 Graph node。PromptPort 提供三个固定的 async/awaitable 方法，
其余 Port 提供各自一个固定的具名 async 方法；需要同步能力时由 assembly 提供显式 typed
async adapter。runtime_checkable
Protocol 只能证明成员存在，不能证明 arity、async 或 exact return；静态检查和首次 activation
的明确 TypeError/contract error 共同承担剩余保证。不得用反射猜签名。

think.port 可提供五个 owner-internal Invocation adapter：一个 PromptPort adapter 与四个
阶段 Port adapter。PromptPort adapter 保持三个方法的单一对象边界；它们只负责把节点已经
构造的 request 交给正式 typed boundary，再把准入结果返回；不重建 request、不定义
validator、resolver、registry、重试或 transport。
核心节点不得直接 import/call mote_kernel.invocation。

Role/Flow assembly 必须对五个业务 Port 对象逐一执行同样的注入顺序：先构造合规的基础
PromptPort/ContextPort/CompactPort/InferencePort/CommandPort，再用外部 typed Failover
decorator 包住对应对象，最后把装饰后的对象注入 ThinkNode。PromptPort 的三个方法由同一个
装饰对象暴露，不能拆回三个独立 capability 或三个独立 Failover 包装。不得包住核心 callable、共享
Hook、Think Graph 或 Graph.run()。Failover 的 retry/policy/receipt/identity/reconcile 完全
不在本稿。

### 6.3 Invocation typed boundary（只读前置依赖）

Invocation owner 独立提供并测试 InvocationTypeContract、InvocationAdmission 和
invoke_typed（最终 import/签名以 owner 发布版本为准）。概念语义为：

~~~text
invoke_typed(invocation, request, contract)
  1. exact 检查 request outer class，并运行纯 admit_request（若有）；
  2. 通过 strict Invocation.invoke 调用一次；
  3. exact 检查 result outer class，并运行纯 admit_result（若有）；
  4. 原样传播普通异常和 CancelledError；不重试、不写 State/Store、不转换 Graph failure。
~~~

Think 实施者不得修改 invocation.py、不得在 think/ 复制 helper/stub/shim，也不把
ThinkTypeBinding 传给 ThinkNode。DTO 字段/递归校验由 DTO owner；Hook 内层 admission 由
Hooks owner；Invocation 只做统一 typed boundary。正式 API 未就绪时，阶段 D 集成阻塞，不能
用本地假实现绕过。

## 7. 唯一公共 API、包结构与 nested 接入

### 7.1 ThinkNode 构造面

~~~python
from mote_kernel.think import ThinkNode

think = ThinkNode(
    "role.think",
    version=1,
    prompt_port=failover_prompt_port,
    context_port=failover_context_port,
    compact_port=failover_compact_port,
    inference_port=failover_inference_port,
    command_port=failover_command_port,
    model_binding=model_binding,
    hook=hook,  # 一个共享的真实 HookNode，slot.node_id == "hook"
)
~~~

mote_kernel.think.__all__ 只导出 ThinkNode。五个阶段按职责平铺为五个模块，每个模块的
`__all__` 只包含对应图节点：`think.prompt`、`think.context`、`think.compact`、
`think.inference` 和 `think.command` 分别只导出 `PromptNode`、`ContextNode`、
`CompactNode`、`InferenceNode` 和 `CommandNode`；共享 DTO/Port 仍由 `think.contract`
拥有，不成为顶层并列图入口。PromptHook、ContextHook、
CompactHook、InferenceHook、CommandHook、ThinkRunner、ThinkExecutor、ThinkState、
ThinkGraphBuilder 均不得成为任何 Think 公共包的并列入口；不提供第二个 run API、manager、
registry 或兼容 alias。

五个业务节点各自拥有一个命名模块；模块不增加独立执行/runner 入口，也不创建私有 Graph
runner，唯一公开符号就是该职责的图节点。当前包布局固定为：

~~~text
think/
├── __init__.py             # 只导出 ThinkNode
├── node.py                 # ThinkNode 的总装配 owner
├── contract.py             # Think 共享 outer DTO/step 基类
├── prompt.py               # 只导出 PromptNode
├── context.py              # 只导出 ContextNode
├── compact.py              # 只导出 CompactNode
├── inference.py            # 只导出 InferenceNode
└── command.py              # 只导出 CommandNode
~~~

五个职责模块均已实现并分别只导出自己的图节点，不能从 `mote_kernel.think` 顶层导入；
`PromptPort` 是 `think.contract` 的 owner-internal contract，不作为 Prompt 模块的第二个
公共导出。

ThinkNode 必须显式使用 __slots__，只保存初始化后不再重绑的 assembly capability 和
descriptor 引用，不保存 run-local cache、task handle 或动态 __dict__。首次成功 compile
后的 immutable boundary 继续由 Graph 自身 mutation guard 提供，不新增 Think seal。

### 7.2 父图接入

父图只把 Think 作为一个 nested node：

~~~python
react.add_node(
    "think",
    think,
    inputs={"request": Graph.graph_input("turn_request", ThinkRequest)},
)
react.add_node(
    "next",
    next_node,
    inputs={"think": Graph.node_output("think", "result")},
    outputs={"...": ...},
)
react.add_edge("think", "next")
~~~

父图 request/result 若不是同一个 exact nominal class，必须在父图 assembly 处增加显式 typed
adapter；不能用 cast、宽 envelope、裸字典或 object 绕过 compiler。父图不能绑定
prompt/context/compact/inference/command/hook/route 内部 output，不能重新执行共享 Hook，
也不为 Think 创建第二个 runner/session/state。ReAct 是否进入 Act、循环或结束是父图路由问题。

## 8. Graph 执行、失败与取消边界

### 8.1 正常执行

一次成功 Think activation 的普通 Graph 调度顺序为：

~~~text
Prompt → Hook → Route → Context → Hook → Route → Compact → Hook → Route
       → Inference → Hook → Route → Command → Hook → Route(FINISH)
~~~

其中五次 Hook 是同一个 nested Graph 定义的五次 activation，五次 Route 是同一个
callable 的五次 activation。每次 callable/nested child 都由 Graph 完成 task、settlement、
publication 和 frontier 推进；Think 不批量提交、不调用 reduce_graph_run()，不新增 commit/
store/recovery API。

### 8.2 失败和取消

- 任一核心节点、共享 Hook 的任一 priority，或 route 发现非法 frame 时，后续 activation 不
  调用；Think 不伪造空 HookResult，不回滚上游 settlement；
- Graph.failure(...) 仅表示显式 terminal business failure。success-only Port 的业务拒绝、
  provider exception、contract error 和普通 callable 异常按现有 Graph 产生 TaskRaised；
- 调用方取消 Graph.run() 按 Graph 的 cleanup/fence 语义向调用方传播；根 scope 节点自身
  抛出的 CancelledError 走 root owner 边界；共享 Hook nested child 自身取消时，family driver
  先 abort/fence child，再向父 scope 投影既有的 nested cancellation failure，不笼统承诺“原样
  抛出”；
- 无 resume-input codec 时，任何核心/route callable 返回 Graph.interrupt 都由现有 Graph
  以 ResultCollectionError 拒绝，不产生 awaiting-resume 或成功 Think result；
- Think 不吞异常、不把异常转换为成功值，不自行重试、failover、恢复或写入 State/Store；
- Hook commands 不是 Graph commands，不因 route 转发而自动 apply。

取消测试必须区分 caller cancellation、root node-origin cancellation 和 nested Hook
node-origin cancellation，并分别断言 cleanup/fence、父 scope 投影和无终端 HookResult。

### 8.3 明确延期

本轮不实现 Think 专用持久化、checkpoint、跨进程恢复、retry cursor、receipt、reconcile、
幂等键或外部副作用重建。后续统一 persistence/recovery 任务只能扩展 Graph 的统一契约，不能
新增 Think state/runner/reducer。

## 9. 分阶段实施顺序

### 阶段 A：冻结拓扑与 owner 边界

- 冻结五个业务 node、一个共享 HookNode、一个 route node 以及所有 control/data edge；
- 冻结 predecessor-bound Graph.node_output("hook_request") 机制和 route 条件路由；
- 冻结 ThinkFrame/step nominal envelope、唯一 request/result boundary 和 shared Hook slot；
- 冻结单一 PromptPort 的三项获取顺序、Inference 定稿位置、Command 只结构化结果；
- 确认五个业务 Port 均由外部 assembly 先套上 Failover，再注入对应节点；Hook command 只
  接收/传递，Invocation/Failover/Hooks 内部/持久化按 owner 分工外置。

验收：没有五个 *_hook node/参数，没有第六个业务阶段，没有平行 Think state 或 command
accumulator；图实现节点数明确写为 7（5 core + shared hook + route）。

### 阶段 B：实现 DTO 与 Protocol

文件：

~~~text
src/mote_kernel/think/contract.py
src/mote_kernel/think/prompt.py
~~~

任务：

- 实现固定 outer DTO、ThinkFrame 及 nominal step 变体的 frozen/slots 构造入口；
- 定义 ThinkRoute closed enum 及合法的 step→route 转移；
- 实现 ModelBinding concrete class 和 data-only immutable 规则；
- 定义一个三方法 PromptPort、四个阶段 Port Protocol；
- 明确 HookRequest/HookResult 外层使用现有 runtime class，shared Hook 的 value 是
  ThinkFrame；
- 明确 success-only Port 采用 exception-only contract；
- 不写通用 validator、不改 Invocation、不读取 Hook 私有 admission/topology。

### 阶段 C：实现核心节点、共享 Hook、route 和 ThinkNode

文件：

~~~text
src/mote_kernel/think/node.py
src/mote_kernel/think/__init__.py
src/mote_kernel/think/prompt.py
src/mote_kernel/think/context.py
src/mote_kernel/think/compact.py
src/mote_kernel/think/inference.py
src/mote_kernel/think/command.py
~~~

任务：

- 实现五个私有核心 callable，并让每个都输出同 exact HookRequest descriptor；
- 在任何 Graph.add_node() 前完成唯一 Hook 的 exact kind/slot 检查；
- 用 Graph.node_output("hook_request") 接入共享 Hook；
- 实现 route 的 step→conditional-route 映射、hook_result output 和 finish output；
- 按第 3.3 节添加五条 core→hook、一条 hook→route、五条 route conditional edge；
- 让 prompt 成为唯一 automatic entry，不显式添加 START edge；
- __init__.py 只导出 ThinkNode；不安装 resume-input codec。

本阶段可使用 typed test doubles 验证拓扑和节点行为，不得为 Invocation 创建本地 stub。

### 阶段 D：实现 Port adapter

前置门：Invocation owner 已发布并测试正式 typed boundary。未满足时本阶段阻塞，不修改共享
模块，也不以 shim 假装完成。

文件：

~~~text
src/mote_kernel/think/port.py
~~~

任务：

- 实现五个 concrete adapter，统一调用正式 invoke_typed/owner 等价 API；PromptPort adapter
  保持一个对象和三个方法的边界；
- 每个核心 activation 至多调用一次对应 Port，PromptPort 的三个方法按固定顺序各一次；
- Failover 装饰在 assembly 完成后再注入，adapter 不处理重试、Failover、缓存、State/Store
  或 transport；
- 集成测试证明 request-before/result-after admission、单次调用、异常/取消传播委托给正式
  boundary；不复制 Invocation owner 的算法。

### 阶段 E：nested 接入与专项测试

- 用代表性父 Graph 完整运行 Think，验证父图只看见 request/result；
- 验证共享 Hook 被同一实例激活五次、每次收到实际 predecessor 的 frame；
- 验证调用序列、route 分支、最终 route.hook_result、Hook commands 不被 Think 处理；
- 覆盖 kind/slot、DTO、Port、失败/取消、无 codec interrupt 和 nested boundary 负例；
- 不实现完整 ReAct topology 或 Act。

## 10. 确定性测试矩阵

测试放在 tests/think/，必要的公共面门禁放在 tests/architecture/。

### 10.1 拓扑与公共面

- Think definition 的直接节点恰好为：五个核心 callable、一个 nested HookNode("hook")、
  一个 callable route；不存在 prompt_hook、context_hook、compact_hook、inference_hook、
  command_hook；
- prompt/context/compact/inference/command 各有一条 direct edge 到 hook，hook 只有一条
  direct edge 到 route，route 有四条继续 conditional edge 和一条 FINISH → END；
- hook 的 input 使用 Graph.node_output("hook_request") predecessor reference，五个
  前驱 output descriptor exact 相同；不能用固定单一前驱 output；
- prompt 是唯一 automatic entry，builder 未添加重复 START edge；
- route output 恰有 hook_result，Graph output 恰有 result <- route.hook_result；
- shared Hook 的 slot 精确匹配 Think definition/version、node_id == "hook" 和
  AFTER_NODE；普通 Graph、普通 callable 或错误 slot 在第一次 builder 写入前拒绝；
- from mote_kernel.think import * 只得到 ThinkNode；首次成功 compile 后沿用 Graph mutation
  guard；未安装 resume codec；
- Graph compiler 继续负责错误 descriptor、缺边、环、predecessor binding 和 nested boundary
  mismatch 的拒绝。

### 10.2 正常链路与调用次数

- Prompt 的同一 PromptPort 的 system/placeholder/user 方法顺序严格为 system → placeholder
  → user，各一次；
- Context、Compact、Inference、Command 各调用一次对应 Port；
- 共享 Hook nested Graph 被激活五次，且五次由同一个 HookNode/slot 提供；每次 request 的
  ThinkFrame 来自实际完成的核心前驱；
- Route 激活五次，step→route 顺序严格为 CONTEXT, COMPACT, INFERENCE, COMMAND, FINISH；
- 完整调用记录为：

  ~~~text
  Prompt, Hook, Route, Context, Hook, Route, Compact, Hook, Route,
  Inference, Hook, Route, Command, Hook, Route
  ~~~

- 每个后续核心节点只消费前一次 Hook 修订后的 ThinkFrame，不旁路读取旧 stage output；
- 终端 route.hook_result 是最后一次 HookResult，包含其完整 commands；前四次 commands 不在
  Think 内累计、投递或 apply；
- 同一 Think Graph 并发运行时，Port、Hook 和 route 不共享 run-local 可变字段。

### 10.3 类型与装配负例

- 缺失/None/明显不满足具名 Protocol 的 capability 在构造期失败；错误 async/arity 若结构性
  检查无法证明，则首次 activation 以明确 contract/TypeError 失败；
- ThinkFrame、各 step、Context/Compact/Inference/Command DTO 的错误 exact type、可变递归
  成员、运行时句柄、未知 ThinkStep 子类和 ModelBinding 缺失均被相应 constructor/admission
  拒绝；
- ContextFrame 不得冒充 CompactedContext，原始 provider response 不得冒充 InferenceResult；
- shared Hook 缺失、普通 Graph 同 boundary、普通 callable、错误 parent/version、错误 node
  id/stage 或未经授权子类在 builder 写入前拒绝；Hook 内层 payload mismatch 由 Hooks owner
  admission 负责，Think 不复制该算法；
- route 收到未知/不允许 step 时失败，不默认选择下一个 route；
- Invocation request admission 失败时不调用 invocation，result admission 失败时不交给下游；
- success-only Port 的 rejection/exception 按 exception-only contract 传播，不返回 None、裸
  错误字典、未声明 outcome 或字符串错误值；
- ThinkNode 不接收 ThinkTypeBinding、validator registry 或 DTO class registry。

### 10.4 失败、取消与 handoff

覆盖以下独立场景：

| 场景 | 触发 | 断言 |
| --- | --- | --- |
| caller cancellation | 调用方取消 Graph.run() | Graph cleanup/fence 完成后传播取消；无后台 task/伪造 result |
| root node-origin cancellation | 核心节点抛 CancelledError | root owner 按既有规则处理；后续 Hook/route 不调用 |
| nested Hook cancellation | 共享 Hook 内节点抛 CancelledError | child abort/fence 后向父 scope 投影既有 typed failure；无该次 HookResult |
| Hook priority failure | P1/P2/P3 普通异常或 failure | 后续 priority、route、核心节点不调用；不制造空 result |
| route/frame contract failure | route 收到非法 step/frame | TaskRaised/contract error；不猜路由、不继续执行 |
| unsupported interrupt | 任一 Think callable 返回 interrupt 且无 codec | ResultCollectionError；无 awaiting-resume/成功 result |
| nested boundary mismatch | 父图 request/result class 不同且无 adapter | compiler/assembly 拒绝；无 Port 调用 |

共享 Hook 完成 kind/slot 检查后，composition 在首次成功 compile 前不得再次 mutation child
builder。若 Hooks owner 提供 seal，测试可断言 mutation 被拒绝；没有 seal 时只记录这是
owner handoff 约定，不在 Think 复制第二套冻结机制。Think 的 kind/slot 负例必须在第一次
Graph.add_node() 前失败，且 builder 不留下半装配节点。

### 10.5 ReAct nested

- 父图能通过一个 nested node 得到最终 HookResult；
- 父图无法绑定 Think 内部七个节点中的任何一个；
- 父图与 Think 共用同一个 Graph execution/GraphRunState owner；不创建第二 runner/state；
- 父图下游自行决定如何解释 ThinkCoreResult/Hook commands，Think 不代为消费。

## 11. 完成定义与门禁

完成必须同时满足：

- 五个且仅五个业务阶段 node；一个且仅一个共享真实 HookNode；一个最小 route 控制 node；
  直接节点总数为 7，拓扑和 bindings 与本文一致；
- 所有五个核心节点均直接跳转共享 Hook，且通过 predecessor-bound output 接收实际前驱值；
- route 条件边、finish output、ThinkFrame value 链和五次共享 Hook activation 均有实现与测试；
- PromptPort 三项获取顺序、同一对象调用次数、Inference 定稿/调用位置、Command 结构化
  边界均有确定性测试；
- 只有 ThinkNode 是包级公共图入口，没有 runner/builder/manager/兼容 alias；
- 没有 Think 专用 state/reducer/store/persistence/recovery/retry/failover/command delivery；
  Failover 只作为外部 assembly 对五个业务 Port 的注入前装饰，Think 不实现其内部策略；
- 共享 Hook 的 kind/slot 只做 Think 必要装配门禁；Hook 内部 topology、payload admission、
  P1/P2/P3 和 command/pre-commit 语义由 Hooks/父图 owner 负责；
- DTO/Port 字段与递归 data-only 规则由其 owner 构造/admission 保证，通用 request/result exact
  算法只存在于外部 Invocation boundary；
- 所有 Think-owned Graph values 继承 HookGraphValue，不使用 Any、裸字典、反射、宽 envelope
  或通用 utils/common/helpers 包；
- 目标实现不修改 src/mote_kernel/invocation.py、tests/test_invocation.py、Failover、
  execution 或 state；Invocation 门禁未就绪时明确阻塞阶段 D，不提交本地 stub；
- 实现阶段运行并记录：

~~~text
python -m pytest tests/think -q
python -m ruff check src/mote_kernel/think tests/think
python -m ruff format --check src/mote_kernel/think tests/think
pyright（按仓库 make typecheck 配置）
make check
在 monorepo 根目录运行 pre-commit run --all-files
~~~

本计划止于 Think 可作为一个 nested node 被父图使用；完整 ReAct/Act 拓扑另行立项。
