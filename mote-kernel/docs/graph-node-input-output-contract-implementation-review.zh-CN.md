# Graph 节点显式输入/输出契约实施方案评审

> **历史归档，非规范事实源。** 本文记录早期未通过方案；当前 API、实现与验收仅以 `graph-node-input-output-contract-implementation.zh-CN.md` 为准。

## 1. 评审结论

**当前实施方案不通过，不能进入代码实施。**

评审对象是当前 761 行的
[`graph-node-input-output-contract-implementation.zh-CN.md`](graph-node-input-output-contract-implementation.zh-CN.md)，
基线为 `main@f73ca32`。该方案选择的是：单个完整 input/output contract、compile-only 前置关系、所有 node 继续接收同一个 run input、runtime/state 零修改。

这与已确认需求直接冲突：本次必须支持真实多输入、多输出 dataflow，compiler 必须验证并生成 value materialization plan，runtime 必须把 producer 的具名 output 实值传给 consumer 的对应 input。

因此不存在“compile-only 或真实 dataflow 二选一”。此前评审回复选择的 compile-only 分支已经失效；实施方案必须整体重写并重新评审，未通过前不得开始实现。

## 2. 必须采用的公共声明方案

下一版方案必须以 `add_node()` 作为 node port 声明和 input binding 的唯一公共入口：

```python
graph.add_node(
    "normalize",
    normalize,
    inputs=Graph.fields(
        raw=Graph.graph_input("raw", str),
        locale=Graph.graph_input("locale", Locale),
    ),
    outputs=Graph.fields(
        text=str,
        tokens=Tokens,
    ),
    resources=("database",),
)

graph.add_node(
    "render",
    render,
    inputs=Graph.fields(
        text=Graph.node_output("normalize", "text"),
        tokens=Graph.node_output("normalize", "tokens"),
    ),
    outputs=Graph.fields(
        html=Html,
    ),
)
```

这是强制方案，不是候选草案。规则如下：

1. graph input 或 node output 第一次成为 canonical port 时声明名称和类型；后续引用只写 node ID 与 parameter name，不重复类型。
2. port identity 是结构化的 `(scope, boundary/node_id, input|output, parameter_name)`，不是用户另起的 contract 名称。
3. node output reference 必须保存两个独立字段 `node_id`、`parameter_name`；禁止拼接或解析 `"node.output"` path。
4. 用户不声明 `Graph.Value[T]`、枚举成员、contract ID、全局常量或 node handle。
5. 删除 `Graph.value()`、`NodeValueContract`、`NodeInputRequirement`、`NodeOutputRequirement` 及额外的 `Graph.node()`/`NodeSpec` wrapper；不增加 `Graph.inputs()` / `Graph.outputs()` alias。
6. 不需要 `normalize = graph.add_node(...)`；`add_node()` 可以继续返回 `Self`，引用使用已有字符串 node ID。
7. `add_node()` 不修改、挂属性或替换用户传入的 callable；它只建立 graph 自己持有的 immutable definition。第一次成功 compile 后 topology/definition 继续 immutable。
8. declaration order 不产生语义。compiler 必须先收集完整 definition，再解析 forward reference、scope 和 data dependency。
9. `Graph.fields()` 是 input/output 共用的 keyword surface；`inputs=` 只接受 input bindings，`outputs=` 只接受 output declarations，进入内部边界后立即规范化为不同的 frozen、slots、closed-union tuples，不传播 bare `dict`。

### 2.1 `add_node()` 的 body 是 callable/graph 闭合二选一

`add_node()` 的第二个参数必须只允许：

```text
CallableNode | Graph
```

public facade 使用两个 overload，进入 definition 边界后立即规范化为 nominal closed union：

```text
CallableNodeDefinition | NestedGraphNodeDefinition
```

不得使用字符串 kind discriminator，也不增加用户可见的 `Node` wrapper。

- callable node：`inputs` 声明目标 input ports 与 source bindings，`outputs` 首次声明 output ports 及类型；
- nested graph node：`inputs` 将 parent sources 绑定到 child graph input boundary；其 output ports 与类型直接来自 child graph 已编译的 output boundary，不重复声明；
- `Graph.node_output("child", "result")` 引用 child graph 的同名 boundary output；
- 两种 body 在 compiler 之后使用相同的 runtime value ABI，不建立第二套 nested runner。

### 2.2 Resource 不对齐为 typed port

`resources=("database",)` 保持当前字符串 API 和自动登记语义，不增加 enum、generic token 或第二声明入口。

Resource ID 表示装配期 capability lookup；input/output port 表示每次 activation 的 typed data value、来源和可用性。两者语义不同，没有必要为了表面一致而使用同一套声明机制。

## 3. 运行时统一使用 `Graph.Values`

`Graph.graph_input(...)` 与 `Graph.node_output(...)` 只是结构化 definition reference，不承载某次运行的值。真实 graph input、callable input/output、nested graph boundary value 和最终 graph output 必须统一使用 immutable `Graph.Values` carrier：

```python
async def normalize(values: Graph.Values) -> Graph.Values:
    # typed input access 的确切形式由下一版方案闭合
    ...
    return Graph.values(text=text, tokens=tokens)


result = await graph.run(
    Graph.values(
        raw=raw,
        locale=locale,
    )
)
```

ordinary callable 的唯一 ABI 应收敛为：

```text
Graph.Values
    -> Awaitable[Graph.Values | NodeOutcome[Graph.Values]]
```

child graph 通过 adapter 使用同一 input/output frame ABI。这样无需读取或修改任意 Python 函数的参数签名，也不会让 callable node 与 nested graph node 形成两套 value path。

### 3.1 `Graph.fields()` 与 `Graph.values()` 必须保持语义分离

`Graph.fields(...)` 统一 input/output 的公共构造器名称，但通过两个 closed overload 产生不同类型：

```text
Graph.fields(**InputSourceRef) -> InputBindings
Graph.fields(**PortType)       -> OutputDeclarations
```

- 传给 `inputs=` 时，每个成员必须是 `Graph.graph_input(...)` 或 `Graph.node_output(...)` 等合法 source reference；
- 传给 `outputs=` 时，每个成员必须是合法 canonical type declaration；
- mixed reference/type、concrete value 或错误位置必须立即 fail closed；
- `Graph.values(...)` 只构造某次运行的具名 concrete values。

`InputBindings` 与 `OutputDeclarations` 必须是不同的 nominal frozen types，而不是同一个包含可选字段的宽结构。两者可以复用 canonical name/order normalization，但不得在 definition 内合并语义。

不能把这两种静态含义继续合并进 `Graph.values(...)`。否则同一 factory 会混合 source reference、Python `type` 和 concrete value，既模糊唯一事实源，也会削弱 overload、malformed-shape 校验和错误信息。

`Graph.values(...)` 中的 key 只是已经声明的 port parameter name，不是新的 contract ID；它不得接受 `Graph.graph_input()`、`Graph.node_output()` 或 port type declaration。

### 3.2 真实值流

```text
Graph.run(Graph.values(...))
    -> 校验并登记 graph input values
    -> 按 compiled plan 生成 consumer Graph.Values
    -> 调用 callable 或 child graph
    -> 接收并校验 output Graph.Values
    -> 原子发布 output availability
    -> 下游按 binding 取得具体值
    -> Graph result 暴露已声明的 output Graph.Values
```

下一版实施方案必须给出可直接运行的完整用户代码，包括 typed value 读取、graph output boundary、result 读取以及 failure、interrupt、resume 时的 value 规则。不得重新引入 nominal contract 名、`Graph.node()` wrapper、callable mutation、反射推断、`Any`、`object` boundary、bare mapping 或 generic-erasing cast；也不得在 producer output 缺失时回退读取原始 shared graph input。

## 4. Data binding 必须成为普通 data edge 的唯一事实源

`inputs` 中引用 `Graph.node_output("normalize", "text")` 后，compiler 必须自动建立 `normalize -> render` 的 data dependency；调用方不得为同一依赖再写一遍 `add_edge("normalize", "render")`。

强制语义：

- 仅依赖 graph inputs 的 node 自动成为 dataflow entry；
- 一个 consumer 的必填 inputs 来自多个 producers 时，自动形成 AND readiness/join；
- data dependency 由 input binding 唯一拥有，不能与 direct edge 形成两份会漂移的 topology；
- conditional route、loop feedback 和纯控制顺序保留为独立 control constructs；
- definition/compiler/runtime 中必须区分 data edge 与 control edge；
- control edge 可以继续使用字符串 node ID，因为 compiler 会严格校验；port reference 同理使用独立的 node ID 和 parameter name 即可；
- 当前 direct/conditional/join topology 可以作为 lowering target，但不能要求用户同步维护第二份普通 data topology。

## 5. Compiler 必须在任何副作用前 fail closed

`compile_graph()` 必须在缓存 compiled runtime、`StartGraphRun`、authoritative commit、node call 或 resource acquisition 前完成确定性校验。至少包括：

1. graph、node、scope 和 port identity 合法且各自唯一；
2. graph input/output、node input/output declaration shape 合法；`inputs=` 必须收到 `InputBindings`，`outputs=` 必须收到 `OutputDeclarations`，不得交换或混合；
3. body 必须精确属于 callable/graph closed union；callable 满足固定 `Graph.Values` ABI，child graph 已成功编译且 boundary 完整；
4. 每个 canonical port 恰有一个 type declaration，缺失、重复或冲突均拒绝；
5. 每个 required node input 恰好绑定一次，missing、extra、duplicate binding 均拒绝；
6. source node 和 output parameter 存在、方向正确，不能引用 input、boundary、self 或越过 nested scope；
7. producer output type 与 consumer input expected type exact match；不做 subclass、coercion、`Optional` 解包或 numeric widening，转换使用显式 adapter node；
8. parent bindings 与 child graph input boundary，以及 child output projection 与 parent port 完全相容；
9. 所有 graph boundary outputs 都绑定到存在且类型相容的 source；
10. 每次可能 activation 前，每个 required value 都必然由相应 activation 成功发布；reachability、settlement 或 skip 不能冒充 value availability；
11. conditional/optional branch、parallel producer、AND join、loop 首轮与反馈轮、nested boundary、failure、interrupt、skip、resume/recovery 都有闭合的 availability 结论；
12. 生成 deterministic input materialization、output publication、data topology 和 control topology plan，runtime 不再临时猜测；
13. malformed low-level declaration/reference/type descriptor 返回精确 `GraphValidationError`，不得泄漏 `AttributeError`、`KeyError` 或 callable 地址；
14. repeated compile、node declaration order、keyword construction order 和 edge declaration order 不改变结果。

### 5.1 Compile validation 与 concrete value admission 必须分层

graph compile 只能验证静态 definition、binding、declared type compatibility 和所有 activation path 的 value availability；尚未出现的运行时 concrete values 不能伪装成 compile-time 可验证对象。

因此还必须有两个 fail-closed admission boundary：

1. `Graph.run(Graph.values(...))` 在 `StartGraphRun`、commit、resource acquisition 和 node call 前，按 compiled graph input boundary 校验 missing/extra key 与 concrete value exact type；
2. callable/child graph 返回后，在 output publication、settlement 和 routing 前，按 compiled output plan 校验 missing/extra key 与 concrete value exact type。

resume/interrupt override values 使用同一规则。失败必须产生稳定、可定位的 typed execution error，且不得建立任何对应 output availability。

### 5.2 `Graph.Values` 的 port 类型仍需设计闭环

固定的单参数 frame 解决了任意 Python callable 参数签名与反射问题，但没有自动解决 frame 内每个 port 的静态类型问题。

下一版方案必须说明 node 实现如何按名称读取 `raw: str`、`locale: Locale`，并让 strict type checker 得到精确类型。如果 `values.raw`、`values["raw"]` 或 `get()` 返回 `Any`/`object`，就只是把类型擦除藏进 carrier；如果每次读取都要求用户重复 port contract/type，也违反“类型只在 canonical declaration 处声明”的易用性决定。

必须通过具体 type-check spike 证明 typed access、heterogeneous storage 和 callable ABI 可以同时闭合；不能只画 `Graph.Values` 类型外壳。

## 6. 当前 runtime 无法兑现该契约

当前代码的实际语义是：

| 当前基线 | 与目标的缺口 |
| --- | --- |
| 所有 ordinary node 接收同一个 `StepRequest.node_input` | 没有为每个 activation materialize 独立 `Graph.Values` |
| `NodeDefinition[InputT, OutputT] -> GraphDefinition[InputT, OutputT]` | 同质泛型链不能表达一般异构多端口 node |
| success output 只在 transient `TaskSuccess.output` / `Graph.Result.outputs` | 下游和跨进程 resume 没有 authoritative value source |
| `GraphRunState`、`SettleGraphNode` 不保存 output | 恢复后无法证明或重建 required value availability |
| failed node 可被 skip 并继续贡献 routing | settlement predecessor 不代表成功 output 存在 |
| nested runtime 共享一个完整 `InputT` | 没有 parent/child 多 port boundary |

所以当前方案第 3.2、3.3、3.4、5、6、8.2、8.3、11.4、13 和 14 节关于 compile-only、单值 contract、runtime/state 零修改的结论都必须删除或重写。

新的 runtime 设计必须明确：

- authoritative runtime value owner；
- graph/run/node/port/activation/iteration identity；
- callable 与 nested graph 共用的 immutable `Graph.Values` frame；
- 多 input materialization 与多 output publication；
- 同一 frontier 并发 output 的隔离和确定性收集；
- output publication、node settlement、routing 与 authoritative state 的原子顺序；
- required output 缺失时 consumer 不执行；
- failure、interrupt、skip、selective resume 的 value 规则；
- loop 每轮使用哪一代 value，不能复用错误 generation；
- nested parent/child input/output projection；
- restart/resume 后 value 的 durable lookup 或确定性恢复协议。

不得只增加 process-local output cache。架构规定 `GraphState` 保存可恢复执行位置、`DomainState` 保存已建立业务事实，并作为 `AgentState` 原子提交；方案必须据此裁决 value owner，而不能默认把任意业务 output 塞入 `GraphState`。如果形成 durable DTO 或跨语言可观察协议，还必须同步评审 monorepo `conformance/`。

## 7. 严格类型是实施前阻断项

仓库要求 strict typing，且 production boundary 禁止 `Any`、`object`、bare generic、反射和 generic-erasing cast。下一版方案必须用具体类型定义与最小 type-check spike 证明：

1. `str`、`Locale`、`Tokens` 等声明如何形成 canonical exact type descriptor；
2. `Graph.Values` 如何 immutable 地保存、查找和恢复 heterogeneous concrete values；
3. node 实现如何取得精确 typed port value，而不是动态属性、`Any` 或 `object`；
4. compiled plan 如何把每个 source value 安全放入 destination `Graph.Values`；
5. `Graph.values(...)` 如何立即规范化 keyword arguments，而不把 bare mapping 传播到内部；
6. callable/child graph 的统一输入和返回 ABI 如何与具名 ports 对齐；
7. callable/graph closed union 如何继续进入唯一 scheduler/executor，而不是建立第二 runner；
8. `Graph.fields()` 的两个 closed overload 与 `Graph.values()` 如何禁止语义混用。

若现有 Python typing 与 owner 约束无法同时闭合，必须先提交明确的架构变更裁决；不能以类型忽略或擦除掩盖缺口。

## 8. 必须替换的测试与迁移账本

当前文档用于证明 “A output 不转发”“A 被 skip 后 B 仍运行”“恢复后没有 output 也成立” 的测试目标与新需求相反，必须删除或改写。

新的 ADD/REPLACE 账本至少覆盖：

- 多 graph inputs 的实值绑定和类型错误；
- ordinary node 多 inputs、多 outputs 的真实调用与结果；
- `add_node()` 只接受 callable/graph，低层非法 body shape fail closed；
- callable 与 child graph 都使用 `Graph.Values` input/output frame；
- `Graph.fields()` 的 input/output overload 与 `Graph.values()` 不能交叉传入 reference、type declaration 或 concrete value；
- graph input `Graph.Values` 在首次 commit/node call 前拒绝 missing、extra 和错误 concrete type；
- node output `Graph.Values` 在 publication/settlement 前拒绝 missing、extra 和错误 concrete type；
- A 的 named output 成为 B 的对应 input，B 不得收到原始 shared input；
- unknown node/port、错误方向、越 scope、missing/extra/duplicate binding；
- source/destination exact type mismatch；
- binding 自动生成 data edge，且无需重复 `add_edge()`；
- 多 producer inputs 的 AND readiness；
- optional branch output 未产生时 compile 拒绝，或使用另行明确的 optional/merge 语义；
- producer failure/interrupt/skip 后 required consumer 不执行；
- resume/restart 后 upstream value 可恢复并正确 materialize；
- loop 首轮与后续 iteration 的 value generation；
- nested boundary inputs/outputs；
- `Graph.Values` typed access 的静态用例不产生 `Unknown`、`Any` 或 generic erasure；
- invalid graph 在零 commit、零 node/resource call 时 fail closed；
- repeated compile 与各种 declaration order deterministic；
- resource、lease、settlement、routing、cancellation 和 atomic commit 行为按新 value protocol 完整迁移。

覆盖率不能代替这些行为 case。文件级变更账本必须由新的 value-owner 与 ABI 设计得出，不能预先锁死 `state/**`、request/result、executor、session、scheduler、resume 或 nested runtime 零修改。

## 9. 下一版文档准入清单

下一版必须整体重写并同时闭合：

1. 目标与非目标；
2. 本评审第 2 节的唯一公共声明 API；
3. callable/graph body closed union 及统一 `Graph.Values` ABI；
4. `Graph.fields()` 两种 closed result 与 `Graph.values()` 的严格语义边界；
5. graph input/output boundary 与完整用户调用示例；
6. canonical immutable definition model；
7. data/control topology 唯一事实源；
8. compiler 校验顺序、concrete value admission、错误 taxonomy 与 compiled plans；
9. typed value access、runtime value owner、原子提交和恢复语义；
10. conditional、loop、nested、failure、skip、interrupt、resume 规则；
11. strict typing proof；
12. production、测试、文档和可能的 conformance 文件账本；
13. 一次性迁移策略与完整质量门禁。

## 10. 最终判定

**当前实施方案：不通过。**

下一版必须按第 2 至第 9 节实现真实多端口 dataflow，不得退回 `Graph.Value` nominal token、compile-only requirement、shared-input fallback 或 runtime 零修改方案。完成整体重写后再次评审。
