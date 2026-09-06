# 强类型 Graph Contract 实施计划

最后更新：2026-09-06

状态：**长期架构方案，待分阶段实施**。本文把“将节点和端口的类型信息前移到 Graph
编译边界”落成可执行的实施计划。它不是新增一套 Graph，也不是 Think 的专用重构计划。

## 0. 先给结论

长期最优、也最符合 Kernel 现有原则的方案是：

1. **继续只有 `mote_kernel.execution.Graph` 一个公共组图与执行门面。** 把 typed node
   contract、typed port descriptor 和编译期 binding 直接纳入现有 `Graph`/compiler/engine，
   不新增 `TypedGraph`、`ThinkGraphBuilder`、第二个 compiler 或第二个 runner。
2. **让类型成为 Graph definition 的一等组成部分。** `GraphInputRef[T]`、
   `NodeOutputRef[T]`、`PredecessorOutputRef[T]`、`NodeInputPort[T]` 和 node contract
   携带同一个具体的 `T`；连接两个端口时由 Python 静态检查和 Graph compiler 同时检查。
3. **保留一个、且只有一个运行时类型边界适配器。** execution owner 在节点真正执行前把
   `Graph.Values` materialize 成 typed input DTO，调用 typed operation，再把 typed output
   DTO 发布回 `Graph.Values`。业务节点和 Think 不再自己从宽泛值中恢复类型。
4. **把不可避免的动态边界集中起来。** 运行时 exact-class admission、DTO 构造和
   `Graph.Values` 的转换只允许出现在该适配器（以及各自 owner 的 admission）中；
   `cast` 不是校验手段，最多只在 exact admission 之后作为静态类型恢复的最后一道内部实现。
5. **`GraphRunState` 仍是唯一运行时状态，状态转换仍是纯函数，Graph 仍是唯一执行引擎。**
   typed contract 只描述 definition、frame 和调用边界，不引入 ThinkState、typed runner、
   私有 reducer 或隐式缓存。
6. **Invocation 继续独占网络 DTO 边界。** `InvocationTypeContract`、`invoke_typed` 和
   request/result 的 wire-schema、递归字段校验由 `mote_kernel.invocation` 负责；Graph/Think
   只做图声明和本地 typed hand-off，不复制 Invocation 的 DTO 校验算法。

这条路线的关键取舍是：不追求“整个 Python 程序零 `cast`”这个不可实现的表面目标，而是
把动态性压缩到一个有明确 owner、可审计、可测试的适配器。这样既得到长期可维护的强类型
边界，又不破坏“唯一 Graph facade、唯一 State、唯一执行路径”的仓库原则。

## 1. 背景：当前类型为什么会在节点里丢失

### 1.1 当前调用链

现在的 Graph callable 看到的是统一的 `_GraphValues`：

```text
Graph.graph_input()/Graph.node_output()
        │  （descriptor 目前主要是运行时 class + 名称）
        ▼
compiler / materialization
        ▼
NodeCallable(Graph.Values[T])
        ▼
业务节点从 values["..."] 取 object-like 值，再 type(...) 检查和 cast
```

`Graph[GraphValueT]` 的泛型参数描述的是一张图的值族，不足以表达“这个输入槽必须是
`ContextRequest[具体参数]`、这个输出槽必须是 `HookRequest[具体 frame]`”。名称和运行时
class 能帮助 compiler 做一部分检查；而 `NodeCallable` 当前统一接收
`Graph.Values[GraphValueT]`，会把每个节点的具体 DTO 关系主动拓宽为同一个值载体。
这不是静态源码里的泛型被擦除，而是节点执行接口没有把每个 slot 的精确关系表达出来。

因此本计划不是从零发明“组图时类型检查”：现有 `graph_input`、output declaration 和
compiler descriptor 已经提供了这项能力。后续工作是把这套已有能力完整延伸到每个
`NodeOutputRef`/`PredecessorOutputRef`、node operation 的 DTO contract 和唯一 runtime
adapter，而不是另建一套平行类型系统。

Think 当前的 `node.py` 和各阶段 callable 因此要重复做以下工作：

- 从宽泛 `Graph.Values` 取出字段；
- `type(value) is ExpectedClass`；
- 用 `cast` 把运行时检查后的具体对象重新对齐到静态泛型视图；
- 再检查上游 `step`、`HookResult`、来源 node 和返回 DTO。

这些检查本身有价值，但散落在每个领域节点时会产生三类风险：

1. 某个节点漏掉一项检查，错误会在更晚的运行时才暴露；
2. 同一条规则在 Graph、Think、Hook、Invocation 中出现多个近似版本，边界不一致；
3. `cast` 容易被误解为“已经完成校验”，实际上 `cast` 在运行时没有任何效果。

### 1.2 静态类型没有擦除，运行时参数会擦除

先把两个经常混淆的事实分开：

- **静态源码/类型检查阶段没有擦除。** Pyright 读取到的
  `ContextRequest[Payload, State, ...]`、`NodeOutputRef[T]` 和 Port 泛型仍然完整存在；
  类型检查器可以据此检查函数参数、返回值和连接关系。
- **运行时对象没有完整的泛型参数。** Python 运行时通常只能拿到
  `ContextRequest` 这个 class，不能可靠地判断一个对象是 `ContextRequest[PayloadA]` 还是
  `ContextRequest[PayloadB]`。

因此以下两件事必须分开：

- **静态关系**：由 `TypeVar`、泛型 ref、contract 和 Pyright 检查；
- **运行时事实**：由 exact `NominalTypeDescriptor`、DTO owner admission 和编译 descriptor 检查。

`cast` 不能替代运行时检查。理想实现用类型守卫在 exact admission 后完成静态 narrowing；
如果 Pyright 对某个泛型关系仍无法推导，适配器内部可以保留一个局部 `cast`。禁止把这个
局部例外扩散到 Think、Act、Observe 或任意业务 Port。

### 1.3 当前 nominal type 门禁的边界

现有 `canonical_nominal_type()` 已排除 `object` 和 `typing.Any`，但“可传入一个 class”不
等于“它是适合作为 Graph contract 的 concrete nominal DTO”。Protocol 声明、抽象类、
`list`/`dict` 等可变容器也不应悄悄成为 contract 类型。

目标方案把这两个层次明确分开：

- Graph declaration/compiler：拒绝不能作为稳定 frame descriptor 的类型（包括 top type、
  Protocol 声明、abstract class、可变容器），只承认 concrete nominal class；不递归检查领域
  DTO 的业务字段；
- Invocation：在网络/外部调用边界继续做 request/result 的 exact DTO admission、字段和
  递归 data-only 规则。该算法的唯一 owner 是 `mote_kernel.invocation`，Think 不复制。

## 2. 目标与非目标

### 2.1 目标

- 在现有 Graph facade 内建立一份 node contract，统一静态类型、runtime descriptor 和
  frame materialization 的来源；
- 让错误的 input/output 连接尽量在 Pyright 或第一次 compile 时失败，而不是在业务节点
  深处猜测；
- 让每个节点业务逻辑直接接收/返回具体 frozen DTO；
- 将 `Graph.Values` 与 typed DTO 之间的动态转换限制在 execution owner 的唯一 adapter；
- 保持 direct edge、conditional edge、predecessor output、Join、nested graph、资源、
  取消、异常、提交和 mutation guard 的既有语义；
- 让 Think 的五个业务节点、共享 Hook 和 route 使用同一套 typed contract；Hook 只在
  Think 拓扑明确要求时进入，不把“每个节点后必须有 Hook”升级成全局 Graph 规则；
- 保留 Invocation、Failover、Hook 内部 command 和持久化恢复的 owner 边界；
- 用静态负例、编译期负例、运行时负例和组合回归证明这套边界，而不是只增加 happy path。

### 2.2 明确不做

- 不新增 `TypedGraph`、`ThinkGraphBuilder`、第二 public facade、第二 scheduler、第二
  reducer、第二 state model 或第二执行路径；
- 不把 typed contract 变成隐式 operation registry、字符串 discriminator 或反射系统；
- 不在 Think/Graph 中实现 Invocation 的网络传输、wire-schema、重试、Failover、非幂等
  receipt 或外部副作用；
- 不实现持久化、checkpoint、continuation 的跨进程恢复；这些仍由统一 persistence/recovery
  owner 处理；
- 不由 Think 解释、投递、聚合或 apply Hook commands；Think 只接收并沿 typed value 链
 传递 `HookResult`；
- 不为了消灭最后一个内部 `cast` 而引入代码生成、运行时反射或另一套 schema 真相；若未来
  证明生成 adapter 有收益，只能以现有 contract 为唯一输入，不能形成第二个组图系统。

## 3. 不可降级的架构原则

1. **唯一 facade**：所有领域都通过 `mote_kernel.execution.Graph` 组图和执行；typed API
   是 Graph 的增强，不是旁路入口。
2. **唯一 State**：typed frame 是 Graph value/publication，不是 `GraphRunState` 的镜像；
   所有状态变化仍走统一 command/reducer/commit 边界。
3. **单一真相**：一个 node contract 同时生成静态声明、runtime descriptor、输入 materializer
   和输出 publisher；不能让 annotation、字符串表和运行时表分别维护同一事实。
4. **纯转换**：contract/compiler/admission 只验证和构造值；节点、Port 和 adapter 不直接
   修改 `GraphRunState`。
5. **窄 Port**：外部 capability 通过具名 typed Port 注入。缺失 required Port 在 assembly
   失败；optional Port 在组图时移除对应步骤，不在节点内部用 `None` 分支隐藏缺陷。
6. **精确运行时类型**：contract descriptor 使用 exact class admission；子类、Protocol、
   abstract class 和可变容器不能靠“看起来有相同字段”通过。
7. **异常不猜来源**：adapter 不通过 `__cause__`、异常文本或包装层级猜测异常属于哪个
   owner；Invocation 异常、业务异常和取消按各自契约原样传播。
8. **编译后不可变**：第一次成功 compile 后继续使用 Graph 现有 mutation guard；typed
   contract 不另造 seal/finalize 状态机。
9. **不为测试保留 legacy 生产路径**：迁移仍有价值的测试语义，但删除已经废弃的旧入口、
   alias 和兼容 runner。

## 4. 目标架构

```text
Domain DTO / typed Port
        │  concrete NodeContract[InputT, OutputT]
        ▼
Graph facade（唯一 public composition API）
        │  typed refs / bindings
        ▼
Graph compiler
  ├─ name/shape/edge/gate 校验
  ├─ exact descriptor 与 nested boundary 校验
  └─ 生成 immutable compiled contract + materialization plan
        │
        ▼
Execution owner 的唯一 typed adapter
  ├─ Graph.Values ──exact admission──> InputDTO
  ├─ typed operation(InputDTO)
  └─ OutputDTO ──exact admission──> Graph.Values
        │
        ▼
TaskScheduler / Session / Family driver（唯一执行链）
        │
        ▼
GraphRunState + pure reducer + commit boundary
```

### 4.1 Owner 分工

| 层/模块 | 唯一责任 | 不负责的事情 |
| --- | --- | --- |
| `execution.facade.Graph` | 组装 definition、暴露 ref、触发 compile/run | 不执行第二套 typed runner |
| `execution.graph.ports` / contract declaration | descriptor、ref、binding 的不可变表示和基础声明门禁 | 不校验领域 DTO 的递归业务字段 |
| `execution.graph.compiler` | 根据 contract 解析 edge、gate、publication、nested boundary | 不调用节点、不修改 State |
| execution adapter | 唯一 `Graph.Values ↔ DTO` 动态边界 | 不重试、不写 State、不解释 Hook command |
| scheduler/session/family driver | 按现有 Graph 语义调度 callable、child 和资源 | 不复制 contract 或 reducer |
| `state.graph_state` | `GraphRunState`、command、纯 reducer 和生命周期校验 | 不持有 DTO 镜像或 Port 事实 |
| domain（Think/Act/Observe） | DTO、业务节点、Port contract、领域拓扑 | 不创建 runner/compiler/state |
| `mote_kernel.invocation` | 外部 request/result exact admission、wire DTO/schema、调用错误边界 | 不组图、不执行 Graph node |
| composition/Failover owner | Port/Invocation 装配、Failover、非幂等与重试 | 不改变 Graph contract 语义 |
| Hooks owner | Hook 子图内部 admission、command 语义和 slot 实现 | 不让 Think 复制 Hook runner |

### 4.2 Hook 的范围

“业务节点成功后进入共享 Hook”是 Think/Observe 等领域的**明确拓扑需求**，不是所有
Graph 节点的通用强制规则。typed Graph 只验证声明出来的 edge 和 child boundary：

- 有需求的节点必须按 contract 声明到共享 Hook；
- 没有需求的节点可以直接跳到下一个业务节点；
- Hook 返回的来源 route 是 Hook contract 的输入/输出字段，父图按现有 Graph route 消费；
- nested Think 的内部 route 由父图消费其唯一 nested output，不由 Graph 类型层偷偷推断。

## 5. Typed contract 模型

下面是概念 API，不要求一次性照抄名称；必须保留的语义是“现有 descriptor 的静态泛型
视图、不可变 contract、同一 ref 同一 `T`”。实现时应复用现有
`NominalTypeDescriptor`、`GraphInputRef` 和 `NodeOutputRef` 位置，避免创建平行声明模块。

### 5.1 复用现有 `NominalTypeDescriptor`

```python
from mote_kernel.execution.graph.ports import NominalTypeDescriptor

ValueT = TypeVar("ValueT")

# 直接使用 execution.graph.ports 中已有的 descriptor，不新增 TypeToken。
descriptor: NominalTypeDescriptor[ValueT]
```

`NominalTypeDescriptor[T]` 已经是静态 `T` 与运行时 class 的绑定点。它不保存字符串类型名，
不依赖模块名、`__qualname__` 或反射查找。generic DTO 的参数在运行时擦除时，descriptor
仍能保证 outer class 精确；更深的字段由 DTO/Invocation owner admission 负责。

### 5.2 Node contract

```python
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class NodeOperation(Protocol[InputT, OutputT]):
    async def __call__(self, value: InputT, /) -> OutputT: ...


@dataclass(frozen=True, slots=True)
class NodeContract(Generic[InputT, OutputT]):
    input_descriptor: NominalTypeDescriptor[InputT]
    output_descriptor: NominalTypeDescriptor[OutputT]
    operation: NodeOperation[InputT, OutputT]
    input_materializer: Callable[[NodeInputFrame[GraphValueT]], InputT]
    output_publisher: Callable[[OutputT], Graph.Values[GraphValueT]]
```

`input_materializer` 和 `output_publisher` 是同一份 contract 的 typed 两端：前者按已编译的
binding 取出每个端口的具体值并构造 frozen input DTO，后者把 output DTO 按已声明的 output
descriptor 发布成现有 `Graph.Values`。它们必须是显式的 typed callable，不能用反射扫描
dataclass 字段，也不能用裸 `dict`/字符串 tag 猜字段关系。

真实实现还需要 immutable 的 named slot declaration（输入名、输出名、是否 predecessor
bound、publication 规则），但这些应作为 contract 的一部分，而不是再维护一张字符串表。
一个节点允许返回普通 output DTO，也允许沿用现有 Graph 的 failure/interrupt/success
outcome；contract 必须明确这两种形状，adapter 不能靠运行时猜测。
当一个节点有多个值时，用一个 frozen `InputFrame`/`OutputFrame` DTO 表示整组值；不要把
异构值重新塞回裸 tuple、裸 dict 或 `object` envelope。

### 5.3 Typed refs 和 typed binding

```python
T = TypeVar("T", covariant=True)


@dataclass(frozen=True, slots=True)
class GraphInputRef(Generic[T]):
    name: str
    descriptor: NominalTypeDescriptor[T]


@dataclass(frozen=True, slots=True)
class NodeOutputRef(Generic[T]):
    node_id: GraphNodeId
    output_name: str
    descriptor: NominalTypeDescriptor[T]


@dataclass(frozen=True, slots=True)
class NodeInputPort(Generic[T]):
    node_id: GraphNodeId
    local_name: str
    descriptor: NominalTypeDescriptor[T]
```

`PredecessorOutputRef[T]` 采用同样的 descriptor；它表达“当前 activation 的实际控制前驱发布的
这个名字和类型”，不表达“取最近一次任意 publication”。`bind(destination: NodeInputPort[T],
source: ValueRef[T])` 的两端必须拥有同一个静态 `T` 和同一个 runtime descriptor。若 Python
类型推导无法表达任意数量的异构 slot，则由 domain contract factory 生成 typed binding
tuple；不能退回宽泛 `Mapping[str, object]` 来绕过检查。

### 5.4 Frame DTO

业务节点的 callable 应变成如下形状：

```python
@dataclass(frozen=True, slots=True)
class ContextInput:
    request: ThinkRequest[PayloadT, HookStateT]
    prompt: PromptFrame[SystemPromptT, PlaceholderT, UserPromptT]


@dataclass(frozen=True, slots=True)
class ContextOutput:
    hook_request: HookRequest[ThinkFrame[ContextStepT, HookStateT], HookCommandT]


async def context_node(value: ContextInput) -> ContextOutput:
    ...
```

这里的 DTO 只表示一个 node activation 的 typed hand-off，不是第二个 runtime state。所有
DTO 应为 frozen/slots 的 nominal class；required 字段为空、字段递归 data-only 或 wire-schema
不变量由 DTO owner 或 Invocation owner admission 处理。

### 5.5 唯一运行时类型边界适配器

静态类型信息并没有在这里“重新传过去”；它在源码和 contract 中本来就一直存在。这个
适配器只负责把运行时的宽泛 `Graph.Values` 与静态 contract 对应起来，概念上只有一条路径：

```python
async def _invoke_compiled_node(
    frame: NodeInputFrame[GraphValueT],
    contract: CompiledNodeContract[InputT, OutputT],
) -> Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]:
    typed_input = contract.input_materializer(frame)
    result = await contract.operation(typed_input)
    if is_graph_outcome(result):
        return pass_through_existing_outcome(result)
    admitted_output = admit_exact(result, contract.output_descriptor)
    return contract.output_publisher(admitted_output)
```

实现要点：

1. `input_materializer` 只按 compiler 生成的 binding/descriptor 取值，不能扫描“最新值”、
   猜前驱或从缺失 publication 回退到 seed；它对每个端口调用 `graph.values` 的
   `_frame_value_typed()`，再构造 input DTO；
2. `admit_exact()` 直接接收现有 `NominalTypeDescriptor`，先做
   `type(value) is descriptor.value_type`，失败就在 callable 前 fail closed；不再另造
   类型载体；
3. 类型守卫优先用于静态 narrowing。若检查器仍无法把已经通过运行时检查的对象对应回某个
   泛型参数，唯一允许的 `cast` 位于此 adapter，并紧邻已经成功的 exact admission；`cast`
   本身永远不承担校验，也不会在运行时“补回”泛型；
4. operation 返回普通 output DTO 时由 `admit_exact()` 和 `output_publisher` 处理；返回现有
   Graph success/failure/interrupt outcome 时原样交给 `_project_outcome()`，不新增第二套
   outcome 或 settlement；
5. operation 抛出的异常和 `CancelledError` 原样交给现有 Graph execution 边界，不通过
   `__cause__` 猜“异常来源”或重新包装成 Invocation/Think 错误；
6. adapter 不写 `GraphRunState`、不启动后台 task、不执行重试、不 apply command。结果仍由
   现有 scheduler、settlement、reducer 和 commit 链处理。

`graph.values` 还需要补上端口级 typed materialize。当前 `_admit_entries()` 能校验整帧，
但 `_frame_value()` 仍只按名字返回图级 `GraphValueT`。保留旧查找职责，同时增加一个带
descriptor 的窄入口：

```python
ValueT = TypeVar("ValueT")


def admit_exact(
    value: object,
    descriptor: NominalTypeDescriptor[ValueT],
) -> ValueT:
    if type(value) is not descriptor.value_type:
        raise GraphValueAdmissionError("value does not have its exact declared type")
    return cast(ValueT, value)


def _frame_value_typed(
    frame: NodeInputFrame[GraphValueT],
    name: str,
    descriptor: NominalTypeDescriptor[ValueT],
) -> ValueT:
    return admit_exact(_frame_value(frame, name), descriptor)
```

`materialize_node_input()`/typed adapter 必须按每个 resolved input binding 的 descriptor
调用这个入口，不能继续把 `_frame_value()` 的 `GraphValueT` 直接交给业务 operation。这样
`Graph.Values` 仍是统一载体，端口级 `T` 则在进入 operation 前被明确恢复；不需要把泛型参数
或 descriptor 复制进每一个值对象。

现有 `_admit_entries()` 也应改为调用同一个 `admit_exact()`，只负责遍历和检查结果，不再
保留第二份 `type(value) is ...` 算法。这样“整帧 admission”和“端口级 typed materialize”
共享一个 exact 校验原语。

与现有执行链的接法必须是：

```text
materialize_node_input()
    → NodeInputFrame（每个 binding 已按 descriptor admission）
    → scheduler._execute_task()
    → 唯一 _invoke_compiled_node(contract, frame)
    → input_materializer() 得到 InputDTO
    → operation(InputDTO)
    → OutputDTO / 现有 Graph outcome
    → output_publisher() 或原样交给 _project_outcome()
```

当前 scheduler 中的 `definition.operation(_public_node_input(...))` 不能继续作为 typed
节点的最终调用路径；`_public_node_input()` 只能在一次性迁移窗口内作为旧 callable 的内部
适配，迁移完成后删除，避免同时存在两条节点执行语义。

### 5.6 Nested Graph contract

子图编译后产生一个 immutable child boundary contract：输入/输出 DTO descriptor、slot 名称和
descriptor 均来自子图的 compiled definition。父图只能绑定这个 boundary；不能绑定子图
内部的节点、Hook、Port 或 frame publication。

因此 Think 仍然只导出 `ThinkNode`：它作为一个 nested Graph 被父图消费，内部五个业务节点、
共享 Hook 和 route 通过 typed child contract 隐藏。nested boundary mismatch 在 compiler
阶段拒绝，不靠父图的 `cast` 绕过。

## 6. 静态检查与运行时检查的责任分工

| 时机 | 检查内容 | 失败 owner |
| --- | --- | --- |
| Pyright | `NodeOperation[InputT, OutputT]` 的参数/返回、`ValueRef[T]` 到 `NodeInputPort[T]` 的连接、Port 请求/结果泛型 | 类型检查门禁 |
| contract 构造 | concrete nominal descriptor、重复 slot、非法名称、required capability、DTO 外层 class | Graph/domain assembly |
| Graph compile | source/target 存在、descriptor exact identity、predecessor gate、publication、nested child boundary、route 形状 | `execution.graph.compiler` |
| callable 前 runtime admission | compiled frame 是否属于当前 definition/run、exact class、值是否可用、来源/cause 是否合法 | execution admission/materialization |
| operation 返回后 | output exact class、DTO owner admission、声明的 output 名称/类型 | typed adapter + DTO owner |
| 外部 Invocation | wire request/result exact class、字段/递归 data-only/schema、transport contract | `mote_kernel.invocation` |
| 状态提交 | `GraphRunState`、command、revision、lease、commit acknowledgement | `state`/`execution.commit` |

特别注意：`runtime_checkable Protocol` 的 `isinstance` 只能证明成员大致存在，不能证明参数
个数、是否 async、awaitable 返回值或精确泛型。因此它只能作为 assembly 的最低能力检查；
真正的签名关系由静态类型和 typed adapter contract 保证，不能把 Protocol runtime check 当成
完整签名验证。

## 7. 在现有 Graph 中落地，而不是新增一套 Graph

### 7.1 规范入口

保持 `Graph` 的名字和生命周期不变，增强现有 `graph_input`、`node_output`、`add_node` 和
`set_outputs` 的类型声明。概念上：

```python
graph = Graph[RootValue]("domain.graph")

request: GraphInputRef[RequestT] = graph.graph_input("request", request_descriptor)
output: NodeOutputRef[OutputT] = graph.node_output("producer", "result")

graph.add_node(
    "producer",
    contract=producer_contract,
    bindings=(bind(producer_contract.input("request"), request),),
)
graph.set_outputs((bind_output("result", output),))
```

这只是同一个 facade 的 typed 形状。最终 API 应优先让已有 `Graph.add_node` 承载 contract，
而不是长期同时维护 `add_node` 与 `add_typed_node` 两条组图路径。若迁移期间需要内部
compatibility shim，必须在同一迁移窗口删除并迁移测试，不能作为公共 alias 发布。

### 7.2 不能用宽泛 mapping 逃避类型

当前 Graph 的字符串 input/output mapping 是历史声明形状。迁移后的 canonical IR 应将每个
slot 的 descriptor 固化到 immutable binding 中；如果保留 mapping 作为 facade 的语法糖，它必须
在进入 compiler 时立即转换成 typed binding tuple，并且只保留这一份 IR。禁止：

- 在 node callable 内继续以 `values["name"]` 推断业务 DTO；
- 用 `dict[str, object]`、`dict[str, Any]` 或字符串 type tag 作为内部 contract；
- 让多个 adapter 各自保存一份“名称到类型”的表；
- 为 Think、Act、Observe 各建一套相似但不兼容的 builder。

### 7.3 编译和 mutation guard

- compile 前：Graph 仍允许按现有 builder 生命周期补充节点、边和 output；
- 首次成功 compile：contract、descriptor、gate 和 materialization plan 一并冻结；
- compile 后：沿用 Graph 现有 mutation guard，任何修改都失败；
- compile 失败：不留下半成品 compiled owner，下一次修正仍从完整 builder state 重新编译；
- 同一 compiled definition 可驱动多个相互隔离的 run，不把 typed frame 存进 Graph facade。

## 8. Think 的迁移方案

### 8.1 保持 Think 的业务拓扑

Think 仍是五个业务阶段：

```text
prompt → shared Hook → context → shared Hook → compact
       → shared Hook → inference → shared Hook → command
       → shared Hook → END
```

实现上是五个业务 callable、一个共享 `HookNode` 和一个 route/control 形状；共享 Hook 是否
在每个阶段后激活由 Think 已冻结的拓扑决定。typed Graph 不会把 Hook 强加到没有需求的领域
节点上，也不会改变“Hook 的来源 route 是接收的参数、由父图消费”的约定。

### 8.2 每个阶段的 typed DTO

将当前各节点从 `Graph.Values` 中取值的逻辑移到 contract/adapter 后，节点内部只处理：

| 阶段 | typed input | typed output | Port |
| --- | --- | --- | --- |
| Prompt | `ThinkRequest[PayloadT, HookStateT]` | `HookRequest[ThinkFrame[PromptStep, HookStateT], HookCommandT]` | 一个 `PromptPort`，依次提供 system、placeholder、user 三个方法 |
| Context | `ContextRequest[...]` + 上一 Hook 的 typed value | `HookRequest[ThinkFrame[ContextStep, ...], ...]` | `ContextPort` |
| Compact | `CompactRequest[...]` | `HookRequest[ThinkFrame[CompactStep, ...], ...]` | `CompactPort` |
| Inference | `InferenceRequest[...]`（含 assembly 时固定的 `ModelBinding`） | `HookRequest[ThinkFrame[InferenceStep, ...], ...]` | `InferencePort` |
| Command | `InferenceResult[...]` | `HookRequest[ThinkFrame[CommandStep, ...], ...]` | `CommandPort` |

这些是概念上的 node contract 输入/输出；Hook 的内部 Plan/P1/P2/P3、command delivery、
外部模型 transport 不进入 Think contract。

### 8.3 Think 中应删除的动态工作

- `node.py` 中为 `ThinkRequest`、`HookRequest`、`HookResult` 恢复泛型的重复 `cast`；
- 各节点中重复的“取字段—检查 exact class—构造下一个 frame” plumbing；
- 用来源 node 字符串猜路由或猜异常 owner 的逻辑；
- 任意 Think-local state/reducer/cache/runner。

保留在 Think 的内容只有：五个阶段 DTO/Port、业务顺序、共享 Hook 的真实 node admission、
固定 route 和唯一 nested output。Hook command 仍作为 opaque typed output 接收，不在 Think
内解释或执行。

### 8.4 Think 的 Port 与 Invocation

外部 assembly 的顺序固定为：

```text
具体 DTO/Port admission
    → Invocation typed adapter（需要外部调用时）
    → Failover 装饰（由外部 owner 负责）
    → 注入 ThinkNode
```

`PromptPort` 是一个对象，包含三个收集方法；不能拆成三个 capability。Context、Compact、
Inference、Command 各自仍是一个窄 Port。Think 只调用已装配且已套 Failover 的 Port，不判断
幂等性、不实现 retry、不捕获并改写 Invocation 异常。

网络 DTO 的 exact request/result admission 使用 `mote_kernel.invocation.invoke_typed`；
Think 不重新实现 `InvocationTypeContract` 的字段递归校验。普通异常和调用方取消沿现有
Graph 边界传播，不能根据 `__cause__` 把 Invocation 异常误改写为 ThinkContractError。

## 9. 其他领域的迁移边界

### 9.1 Act

Act 的 tool/authorize/resolve/execute/settle 等业务阶段各自定义 frozen request/result DTO
和窄 Port；它们通过同一 Graph contract 接入。工具调用的非幂等、receipt、Failover 和
Invocation transport 仍归外部 owner。只有 Act 拓扑明确需要共享 Hook 时才声明 Hook edge。

### 9.2 Observe

Observe 的 observation batch、cursor、snapshot 和 write receipt 继续由 capability Port
持有；typed contract 只负责节点 hand-off。不要把 queue/store 事实复制到 Graph state，也
不要因为 typed contract 新增另一个 Observe state。读取、写入、ack 和等待的外部副作用仍
由各自 owner 负责。

### 9.3 父级 ReAct/nested Graph

父图只绑定 Think/Act/Observe 的 child boundary DTO。父图不能绑定子图内部节点、Hook 或
Port；子图输出的 route/结果按父图 contract 消费。nested scope、取消、失败、资源和
settlement 复用 execution/family driver，不为每个 domain 建 child runner。

## 10. 分阶段实施顺序

每阶段结束都要有可运行的测试和明确的删除范围；不在生产代码里留下长期双路径。

### 阶段 A：静态可行性 spike

**目的**：先证明 Python 3.11 + Pyright 能表达目标关系，不改执行语义。

交付：

- 为一个简单线性图复用 `NominalTypeDescriptor[T]`，定义 typed input/output ref 和 `NodeContract` 的最小
  内部样例；
- 增加 typing positive/negative 样例：错误输入类型、错误返回类型、错误 predecessor
  binding 必须被 Pyright 拒绝；
- 验证 generic DTO、nested child boundary、covariance/contravariance 和 async callable；
- 记录仍需局部 type guard/cast 的位置和原因。

门禁：Pyright strict 通过；没有新增 public facade、`Any`、反射或字符串 discriminator。

### 阶段 B：Graph declaration/IR

**责任文件**：`execution/graph/ports.py`、node contract 所属的 execution graph declaration
模块，以及 `execution/facade.py` 的现有声明入口。

交付：

- 将 `GraphInputRef`、`NodeOutputRef`、`PredecessorOutputRef` 与 `NodeInputPort` 改为携带
  `NominalTypeDescriptor[T]`；
- 把 slot、descriptor、binding 统一为 frozen immutable IR；
- 加强 concrete nominal class admission：拒绝 top type、Protocol 声明、abstract class、
  可变容器；
- 保留 `Graph.node_output` 的固定 producer 与实际 predecessor 两种语义，不改变其地址含义；
- 保留现有 public `Graph` facade 和 compile 前 builder 窗口。

门禁：现有 graph compiler/ports/nested 测试全绿；错误 descriptor、重复 slot、非法声明
在组装时 fail closed；无第二份 type table。

### 阶段 C：Compiler 与唯一 adapter

**责任文件**：`execution/graph/compiler.py`、materialization/admission、scheduler 调用点及
其直属 execution owner。

交付：

- compiler 从同一 contract 生成 input/output descriptor、publication selection、activation
  gate 和 nested boundary；
- 在 `graph.values` 中实现 `admit_exact()` 和端口级 `_frame_value_typed()`；让
  `materialize_node_input()` 按每个 binding 使用它；
- 在现有 `scheduler._execute_task()` 调用唯一 `Graph.Values ↔ typed DTO` adapter：
  不再直接执行 `definition.operation(_public_node_input(...))`；
- adapter 将普通 DTO 发布回现有 `Graph.Values`，并把现有 success/failure/interrupt outcome
  原样交给 `_project_outcome()`；
- exact admission 发生在 operation 前后；失败、普通异常、取消和 output unavailable 遵循
  现有 Graph 语义；
- adapter 不写 State、不启动后台 task、不做重试、不处理 Hook command；
- 首次成功 compile 后复用既有 mutation guard。

门禁：直接节点、conditional、predecessor、Join、resource、nested、fan-out 和 concurrent
run 回归通过；compile 后 mutation、foreign/stale publication、wrong descriptor 全部有负例。

### 阶段 D：Think 迁移

**责任文件**：`src/mote_kernel/think/contract.py`、五个平铺节点文件、`think/node.py` 以及
对应测试。不得创建 Think 专用 builder 或第二 runner。

交付：

- 五个阶段改为 typed DTO operation；
- Prompt 保持一个三方法 `PromptPort`，Context/Compact/Inference/Command 保持各自窄 Port；
- 共享 real `HookNode`、hook request/result、来源 route、五次激活和唯一 nested output
  的拓扑不变；
- 将 `cast` 从业务节点移到 execution adapter（若类型守卫已足够则删除）；
- Think 只接收 Hook commands，不解释、不投递、不聚合；
- Invocation typed boundary 由 `mote_kernel.invocation` 提供，Think 不复制 DTO 校验算法；
- 迁移仍有价值的旧测试语义，删除真正 legacy 的 `_RouteNode` 等入口，不保留兼容 alias。

门禁：Think 专项、Think+nested Hook、父图组合、取消、异常、并发隔离、route/value 链和
公共导出测试全绿；`from mote_kernel.think import *` 仍只有 `ThinkNode`。

### 阶段 E：Act/Observe/nested 迁移

按领域逐个迁移，顺序由依赖关系决定：先 DTO/Port，再 contract，再 adapter 接线，最后删除
旧 callable plumbing。每个领域完成后独立运行本领域测试和 execution 组合测试，不能一次引入
一个跨领域宽泛 registry。

### 阶段 F：删除旧宽泛路径并收口门禁

- 删除旧的宽泛 node contract、重复 adapter、兼容 alias 和只为旧测试存在的生产分支；
- 将所有 graph assembly、examples 和 typing samples 迁移到同一 `Graph.add_node` canonical
  入口；
- 增加架构测试：唯一 Graph facade、唯一 State、唯一 runner/compiler/adapter、无 forbidden
  imports、无 `Any`/裸字典/反射/字符串 discriminator；
- 运行 kernel `make check` 和仓库级 pre-commit。若混合工作树导致非本专项失败，报告准确
  失败 owner，不把它误记为 typed Graph 设计通过。

## 11. 测试矩阵

### 11.1 静态类型门禁

- 正例：同一 `T` 的 graph input → node input、node output → predecessor input、nested
  child boundary；
- 负例：`PromptFrame` 绑定到 `ContextFrame`、错误 `HookResult`、错误 Port request/result、
  sync callable 绑定 async contract；
- 负例：子类/Protocol/抽象类/可变容器/top type 作为 contract descriptor；
- 负例：错误 model binding、错误 `CommandPort` result 和错误 Hook command 泛型；
- 检查 domain node 源码不再出现分散的 `cast`（允许的唯一例外是 execution adapter 的
  exact-admission 之后）。

### 11.2 Compiler/assembly

- duplicate input/output slot、非法名称、未声明 output、错误 source node；
- fixed producer 与 predecessor-bound ref 的语义分别正确；
- 多个控制前驱的 descriptor 必须 exact 相同，不能选择“第一个”或静默合并；
- nested child input/output descriptor mismatch 在 compile 阶段拒绝；
- real `HookNode` kind/slot admission、Think 六个直接节点（五个业务节点 + 一个共享 HookNode）和 route token 拓扑；
- 首次成功 compile 后 mutation 被拒绝，compile 失败不污染下一次 assembly；
- 同一 compiled graph 的并发 run 不共享 frame、Port 调用计数或 transient output。

### 11.3 Runtime adapter

- `graph.values._frame_value_typed()` 按传入 descriptor 返回具体 `T`；错误 descriptor 或
  错误 exact class 在 operation 前失败；`_admit_entries()` 与它使用同一个 `admit_exact()`；
- scheduler 只通过唯一 adapter 调用 typed operation，不再有 typed 节点直接接收宽泛
  `Graph.Values` 的旁路；
- 输入 exact class 错误在 operation 前失败；
- operation 返回错误 exact class、非法 DTO 或错误 output 名称时失败；
- operation 返回普通 DTO 时正确发布为 `Graph.Values`；返回现有 success/failure/interrupt
  outcome 时不改写其 route、failure 或 interrupt payload；
- `type(value) is expected` 通过后 typed operation 收到正确静态/运行时 DTO；
- operation 普通异常原样传播为现有 Graph execution 错误；`CancelledError` 不被吞掉或改写；
- adapter 不因异常 `__cause__` 猜测 Invocation/Think 来源；
- adapter 不写 State、不绕过 reducer、不启动脱离 Graph 的 task、不做 retry；
- output publication/value unavailable/foreign publication/stale frame 均按现有 execution admission
  失败。

### 11.4 Graph 组合回归

- direct、fan-out、conditional、predecessor loop、Join、resource waiter、nested/family；
- branch completion 顺序置换后结果、cause、publication 和 State 语义等价；
- caller/root/nested 三类取消、unsupported interrupt、普通失败与 sibling 清理；
- commit candidate 非 exact、commit 抛异常、确认前取消时内存 State 不前进；
- terminal output 只来自正确的 publication，不能泄漏中间 Hook result；
- compile 后 mutation、并发运行隔离、scope/definition/version identity；
- 现有 state/reducer、execution/session/family driver 测试不回归。

### 11.5 Think/Hook/Invocation 组合

- Prompt 三个方法按固定顺序各调用一次；其它四个 Port 每个 activation 至多调用一次；
- 五个业务节点按要求进入同一共享 Hook，Hook 激活次数、来源 route 和 value 链准确；
- 没有拓扑需求的测试图可以直接相连，不被 typed Graph 强行插入 Hook；
- Hook 的 `commands` 被完整接收/返回但不被 Think 解释或执行；
- Hook 返回错误来源 route、错误 step kind 或错误 state 时按双方 contract fail closed；
- Invocation request/result 的字段和递归 DTO 错误由 `invocation.py` 报告，Think 不重复校验
  或重写错误；
- Invocation 普通异常、adapter 异常、调用方取消和 timeout 的既有边界保持不变；
- Think 作为 nested node 时父图只能消费唯一 output，不能绑定内部节点或 Port。

## 12. 架构门禁与可观测性

实施完成后应增加/保持以下静态门禁：

- `Graph` 是唯一从外部可用的图组装/执行 facade；execution 内部类型不作为平行入口 re-export；
- 全仓没有第二 runner、第二 State、第二 reducer、第二 compiler 或 domain-local cache；
- 业务节点不直接导入/调用 Invocation transport；只能调用注入的 typed Port；
- `mote_kernel.think` 只导出 `ThinkNode`；domain contract 类型不是第二个图入口；
- 禁止 `Any`、裸字典、反射和字符串 discriminator 进入 internal boundary；
- Graph definition/version、node id、slot、publication、route、scope 和 invocation identity
  在日志/诊断中使用现有 typed identity，不把业务 payload 写进诊断；
- adapter 的 admission failure 包含稳定的 owner/slot/definition 信息，但不泄漏外部 DTO
  payload 或 transport secret；
- 复杂度 ratchet 只作为雷达。若命中，先审查完整调用链，不能通过增加薄 helper、宽 context
  或复制 adapter 来规避指标。

## 13. 为什么不选其他“看起来更强”的方案

### 13.1 Think 专用 typed builder

它可以在一个领域里获得较好的类型推导，但会产生第二套组图 DSL。Think 的拓扑、Hook、
route 和 nested boundary 将与 Graph compiler 各维护一份规则，最终违反唯一 facade、唯一
compiler 和“领域只定义拓扑”的原则。把 typed contract 放进现有 Graph，能让 Think、Act、
Observe 使用同一套门禁，总代码和长期迁移成本更低。

### 13.2 独立 `TypedGraph`

这相当于维护两条执行路径：普通 Graph 一条、TypedGraph 一条。任何 Join、取消、资源、
commit 或 recovery 修复都要做两遍，还会产生结果/异常语义漂移。typed Graph 应是现有 Graph
的 IR/contract 能力，而不是新的运行时类型。

### 13.3 全量代码生成

代码生成能把一部分 `cast` 变成生成代码，但需要 schema 源、生成器版本、构建顺序、生成
文件审查和 stale artifact 处理；动态 nested Graph、用户自定义 Port 和运行时 assembly 也
不适合全部静态生成。更重要的是，若 schema 与 Graph definition 分开，就新增第二份真相。
本计划先让 contract 成为唯一真相；未来若确有规模收益，可以生成 adapter/typing stub，
但生成器必须消费同一 compiled contract，不能生成第二个 builder/runner。

### 13.4 用 Pydantic/msgspec 取代 Graph contract

这些库可改善字段校验或序列化，却不能表达 Graph 的 control edge、publication、predecessor
gate、nested scope、State commit 和 route。它们可由 Invocation/DTO owner 选择，但不能取代
Graph contract，也不应被 Think 用来复制网络 DTO admission。

### 13.5 mypy plugin 或自定义类型检查器

插件可以弥补 Python 泛型推导缺口，但会增加工具链、IDE、CI 和维护成本，并把 Graph 的核心
正确性依赖到一个仓库外插件。先用标准 Pyright + typed ref/contract 将动态边界收口；只有
当真实 schema 证明标准类型系统无法覆盖关键错误时，才评估一个极小、可选的 plugin，且不
改变运行时 owner。

### 13.6 改用 Rust/TypeScript

更强的编译期类型不能消除 Graph 与 Python domain/Invocation 的边界，反而会引入 FFI、构建、
调试和部署成本。当前问题是边界分散而不是 Python 缺少另一种语言；先集中 boundary 和
contract，收益更直接且可渐进迁移。

## 14. 风险与处理

| 风险 | 表现 | 处理 |
| --- | --- | --- |
| Pyright 无法推导异构 slot | 组图代码需要显式类型参数 | 用 domain contract factory 生成 typed tuple；不回退到 object/dict |
| generic 参数运行时擦除 | `type()` 只能看到 outer class | `NominalTypeDescriptor` 做 outer exact admission；字段/递归规则交给 DTO/Invocation owner |
| 旧 callable 仍依赖 `Graph.Values` | 迁移期间出现两种节点形状 | 设定一次性 cutover，adapter 兼容窗口只在内部存在并随后删除 |
| descriptor 与 DTO schema 漂移 | compile 通过、运行时才拒绝 | contract 作为唯一来源；增加 compiler/runtime descriptor 一致性测试 |
| 适配器变成“万能 helper” | 业务规则被偷偷搬入 execution | adapter 只做 materialize/exact admit/publish，领域规则回到 DTO/Port owner |
| Hook/Invocation 异常被误包装 | 错误 owner 或错误重试 | 不检查 `__cause__`，按现有异常传播矩阵测试；Failover 仍由外部 owner 处理 |
| 复杂度门禁反向驱动抽象 | 薄转发、宽 context、重复 registry | 以完整调用链和 owner 清晰度为先，指标只做告警/ratchet |
| 并行工作树污染验证 | `make check` 被无关包阻断 | 分别记录 Think/Graph 专项结果与仓库级失败 owner，不覆盖其他改动 |

## 15. 最终验收标准

只有以下条件同时满足，才可把 typed Graph 方案标记为完成：

1. `Graph` 仍是唯一公共组图/执行入口；没有 `TypedGraph`、domain builder、第二 runner、
   第二 State 或第二 compiler。
2. 所有 canonical input/output/predecessor ref 和 node input port 都携带具体 `T` 与现有
   `NominalTypeDescriptor`；错误连接能在 Pyright 或 compile 阶段失败。
3. 一个 node contract 同时驱动静态声明、runtime descriptor、materialization plan 和
   output publication；没有平行字符串类型表。
4. `Graph.Values ↔ typed DTO` 的运行时动态转换只有一个 execution adapter；业务节点不再散落
   `cast`/`type()` plumbing。若仍需 `cast`，它只能位于 exact admission 之后的 adapter 内。
5. Graph declaration 的 concrete nominal 门禁覆盖 top type、Protocol、abstract class 和
   mutable container；Invocation 的 request/result 字段和递归 DTO 校验仍只由
   `mote_kernel.invocation` 负责。
6. Think 五个节点、共享 Hook、route、Port/Failover 注入和唯一 nested output 的既有需求
   不变；没有把 Hook 强制成所有 Graph 节点的全局规则，也没有 Think-local command runner。
7. Graph 的 direct/conditional/predecessor/Join/nested/resource/取消/异常/commit/mutation
   语义与迁移前一致；GraphRunState 仍是唯一状态真相。
8. 静态正负例、compiler admission、runtime adapter、Think/Hook/Invocation 组合和并发/取消
   边界测试齐全；专项覆盖率和架构门禁有可复现记录。
9. 旧宽泛组图路径、legacy alias、重复 adapter 和只为旧测试存在的生产分支已删除；所有
   有价值的测试与示例已迁移到 canonical Graph API。
10. 本目录 `make check` 和可运行的仓库级检查结果已记录。无关并行改动导致的失败必须明确
    标注 owner，不得用“全绿”或“全红”替代专项证据。

## 16. 本计划之外的后续工作

- 统一 persistence/recovery、durable value reader 和跨进程 nested evidence；
- 外部 Port 的非幂等 receipt、invocation identity、Failover/retry 和 reconcile；
- Hook command 的投递、部分交付、幂等和外部消费；
- 在真实 contract 数量和 Pyright 负例积累后，评估基于同一 contract 的 generated adapter 或
  stub（不得新增第二 Graph 系统）；
- 若未来需要网络 worker 执行 Graph node，新增明确版本化的 operation binding/resolver，
  但必须一次性接入现有 execution owner，不能在 NodeCallable 旁边铺隐式 Invocation runner。
