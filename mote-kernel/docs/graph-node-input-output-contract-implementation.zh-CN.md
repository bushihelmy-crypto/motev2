# Graph 节点显式多端口输入/输出与参数绑定实施方案

> 核心原则：public declaration 在调用边界规范化为 immutable nominal definition，builder 只提交完整 candidate；compiler 一次性拥有 reference、exact type、data/control topology 与 shared `FrontierTransitionPlan`。Runtime 只 materialize 已 admission、已确认的 frames，`GraphRunState` 是 control truth，`ScopeRunCoordinate` 是 concrete frame identity。State-only/recovered invocation 在任何 mutation 前沿 shared plan 做 bounded availability proof：既有 settlement/concrete skip 只走确定 route，ordinary callable 只展开 canonical success projection，failure/interrupt 由 awaiting quiescence invariant 闭合，nested proof 不做 Cartesian product。四类 frame、三类 Outcome/Result 与 complete/recovered continuation 保持需求规定的 nominal separation；所有 public values/outcomes/results/transitions 都由唯一 owner factory/seal 构造。`Graph` 是唯一 public facade，不新增 legacy、第二 runner、value truth、limits truth、persistence 或 State 扩张。

## 1. 文档信息与当前判定

- 状态：**Implemented / normative source**
- 日期：2026-08-17
- 所属项目：Mote Kernel
- 目标目录：`mote-kernel/src/mote_kernel`
- 唯一公共门面：`mote_kernel.execution.Graph`
- 唯一执行 owner：现有 `execution` engine
- 历史评审记录：`docs/graph-node-input-output-contract-implementation-review.zh-CN.md`、`docs/graph-node-input-output-contract-implementation-review-response.zh-CN.md`（均非规范事实源）
- 结构参考：`docs/frontier-node-settlement-implementation.zh-CN.md`、`docs/frontier-node-resume-implementation.zh-CN.md`
- 硬门禁：零已知架构负债、唯一事实源、复用既有基础设施、严格泛型、模块级导包、无 `Any`、无 `object` boundary、无反射、无 generic-erasing cast、无兼容双轨

本文是该能力唯一的规范事实源；历史 facade 方案与历次评审文档只保留决策过程，不再拥有当前 API、实现或验收语义。Production、tests 与 README 已按本文一次性实施，不存在 legacy overload、兼容 runner 或第二 value/control truth。第 5 节冻结的类型责任保持不变：Pyright 严格校验 Python API/generic 结构与 bare static factory inference，但不负责从运行时字符串键推导逐端口类型；跨节点输入/输出契约与全部 `add_edge()`/conditional/join declaration 由 graph compiler 在任何执行副作用之前 fail closed。

## 2. 目标、边界与已冻结决策

### 2.1 本次必须交付

1. callable node 通过 `add_node()` 显式声明具名 input source bindings 和 output type declarations。
2. input binding 是 named value source/readiness 的唯一事实源；调用方不重复维护同一条普通 data edge。
3. compiler 收集完整 immutable definition 后校验 source、scope、方向、类型解析、执行前后关系、data/control 合成和 activation gate。
4. runtime 为每次 activation 建立 destination-local input frame，把 producer 的具名 output 实值交给 consumer 的对应 input。
5. graph input、ordinary callable、nested graph boundary 和 graph result 使用同一个 parameterized immutable frame ABI；逐端口 exact type 由 compiled descriptor 约束。
6. graph input、continuation、materialization 和 output 分别在其最早可观察时点 admission；不同阶段拥有不同 side-effect guarantee。
7. failure、interrupt、skip、conditional、join、control loop、nested 和同进程 continuation 有确定、可测试的 value 语义。
8. compiler 拒绝真实执行上尚未产生、方向错误、越 scope、形成 data cycle 或不能 guaranteed-before 的 output reference；`add_node()` 文本顺序不决定 data/control topology，但按第 3.7 节唯一地决定 resource first-seen order。
9. `add_edge()`、`add_conditional_edge()` 和 `add_join()` 的 endpoint、方向、重复/冲突、data-cycle/control-loop 合法性、entry/completion 与 data/control 合成只在收集完整图后由 compiler 校验；builder 不以当前声明顺序提前判定 node 不存在。
10. completed/aborted/awaiting-resume Result 使用 invariant closed nominal variants，全部携带 non-optional invariant opaque continuation；state-only lineage 使用独立 sealed recovered snapshot 和 whole-invocation availability preflight。
11. nested success 的 parent routing、nested conditional-source 禁令和 terminal aborted-child recovery 在 compiler/resume preflight 有唯一 owner。
12. graph output projection 按 compiled source binding 从 admitted graph-input frame 或 confirmed node publication 读取，包括 root input passthrough 与 child input passthrough。
13. recovery availability 复用 runtime 的 canonical frontier/session/routing transition lowering：State 中已确定的 settlement route 与 concrete skip route 不枚举；被 resume 为 Pending 或尚未执行的 conditional callable 只在 canonical success projection 中枚举 declared routes；failure/interrupt 由“全部 callable input 已 admission、且二者不产生 publication/control contribution”的 quiescence invariant 证明止于 awaiting boundary，不展开 sibling completion permutation。
14. 三个 `run()` overload 都在 compile/cache 与任何 State mutation 前构造并校验同一 effective `ExecutionLimits`；runtime/recovery/nested 只消费该值，control-loop branch 以 exact `ExecutionLimitError` 为有限 boundary。
15. `Graph.Outcome`、outcome factories、完整 `run()` overload、`Graph.Transition` 与 commit result variants 的 public shape 全部在本文冻结，用户不导入 owner-internal `NodeOutcome`、`ExecutionLimits` 或 state command 类型。
16. `RecoveryTransferState` 的 equality/hash/dedup 覆盖所有 transfer-relevant control、availability、child 与 admitted-action facts；traversal sort key 只控制 canonical visit order，绝不作为 merge identity，concrete frame value 只由 run context/continuation 持有。
17. 裸静态 `Graph.values()`/`Graph.success()` 使用独立 `FactoryValueT`；heterogeneous value union、zero-argument `Graph.Values[Never]` 与 invariant outcome contextual typing 在 strict Pyright 下均无 `Unknown`。
18. Public outcome、commit result 与 transition aliases 仍直接指向 canonical nominal implementations，但每类 implementation 都要求 owner-private seal；factory/family driver/settlement projection 是唯一合法构造路径，public alias 只用于 annotation 与 `isinstance` narrowing。
19. `add_node()`、`set_outputs()`、所有 edge builders 与 resume-codec builder 都采用 detached-candidate/single-commit transaction；任一 local failure 后 builder definition、resource order、compiled cache 与 frozen state 和调用前结构完全相等。
20. Root/child runtime frames、availability、publication、resume input 与 child boundary 全部使用同一个 `ScopeRunCoordinate(scope, graph_run_id)` universe；`StableActivation` 嵌入该 coordinate，同一 nested path 的重复 activation 由 deterministic child run ID 精确区分。
21. `execution/graph/values.py` 唯一拥有 `NamedValue`、`_GraphValues`、四类 internal frames、shared immutable normalization 与 `_ValuesSeal`；`Graph.Values` 只用于 annotation/read/`isinstance`，唯一合法构造入口是 `Graph.values()`。

### 2.2 已冻结的公共方向

- 不采用 `Graph.fields()`；`inputs=`、`outputs=` 和 `set_outputs()` 直接接收 mapping。
- public mapping 只存在于调用边界，立即规范化为不同的 immutable nominal types；内部不保存 bare `dict`。
- `Graph.values()` 只表示一次运行的 concrete values，不接收 source ref 或 type declaration。
- `GraphValueT` 只绑定 graph instance/callable/run universe；裸静态 `Graph.values()`/`Graph.success()` 使用独立 module-level `FactoryValueT`。Empty factory frame 的唯一静态类型是 covariant `Graph.Values[Never]`，不产生 `Unknown`、`Any` 或 widening shortcut。
- `execution/graph/values.py` 是 concrete entries/public values/internal frames 的唯一 canonical owner；`Graph.values()` 只做 Graph-namespaced typed delegation，不能复制 normalization。`Graph.Values` 直接 alias `_GraphValues`，但 `_ValuesSeal` 与 private construction token 阻止 public constructor；不暴露 `NamedValue` tuple、mapping constructor 或第二 value factory。
- Internal import direction 固定为 `ports.py -> values.py -> outcome/resume/request/result/run_context/engine/_graph`：箭头表示后者可依赖前者；`values.py` 不反向导入 facade、outcome、resume、request/result、run context、engine 或 State，内部调用方直接导入 `execution.graph.values`，不经 `graph.__init__` 聚合回流。
- `ScopeRunCoordinate(scope, graph_run_id)` 是全部 execution-local frame 的 runtime owner coordinate；root 使用 `scope=()` 与 root run ID，child 使用完整 node-ID path 与 existing `child_graph_run_id(parent_run_id, parent_superstep, nested_node_id)`。`FrameDescriptorIdentity` 只标识 schema，不能独立索引 concrete frame。
- `Graph.node_output(node_id, parameter_name)` 保存两个独立字段，不使用 `"node.output"` path。
- `Graph.node_output()` 是有意保持非泛型的结构化 identity reference；它的 exact type 只能在 compiler 解析 source declaration 后得到。
- Pyright 不承担 mapping string key 到 exact port type 的证明；实现仍必须通过 strict Pyright 与现有 no-`Any`/no-`object`/bare-generic/generic-erasing-cast 门禁。两类证明不得混称：AST architecture lint 只拒绝上述显式类型擦除语法；TypeVar 输入输出贯穿、invariance/covariance、callback 参数 universe、closed alias 与 cross-universe assignability 只由独立 strict Pyright 正/负 fixture 矩阵证明。
- callable body 与 nested `Graph` 是 closed union，不修改 callable，不建立第二 runner。
- resource ID 继续是 `tuple[str, ...]`，不与 data port 合并；global acquisition order 保留 successful committed node definition 与 resource tuple 从左到右的 first-seen semantics，不改为 lexical sort。
- Resource first-seen registration 只发生在完整 `add_node()` candidate 的 single commit；失败调用从未进入 definition，也不得留下 resource ID 或改变后续 order。
- State 不保存 concrete input/output frame、publication ledger、continuation 或 Store handle。
- 当前 State 模型经第 8.5 节逐项充分性证明，已经最终裁决为 activation/resolution 的唯一 control truth，并足以区分 stable activation 与 execution attempt；本方案的 production 账本固定不修改 `state/**`，也不存在 receipt、epoch、variant、adapter 或待实施期选择的 State 方案。
- `Graph.Result` 是 completed/aborted/awaiting-resume 三个 closed variants 的公共 union；每个 variant 都有非 optional `state` 与 `continuation`，不存在语义不明的 optional continuation/output/failure 字段。
- `Graph.CompletedResult`、`Graph.AbortedResult`、`Graph.AwaitingResumeResult`、`Graph.Result` 与 `Graph.Continuation` 全部使用同一个 invariant `GraphValueT`；只有 immutable `Graph.Values[GraphValueT_co]` 自身保持 covariant，Result 不能借 outputs covariance 扩宽 continuation universe。
- continuation 的 complete/recovered snapshot variant 只在 private sealed implementation 内区分；public 不获得构造器、variant switch 或持久化协议。
- Outcome、commit result、transition 与 continuation 的 Graph-namespaced public nominal class aliases 都要求各自 canonical owner 的 module-private identity seal；公开 alias 不等于公开合法 constructor，不增加 wrapper 或并行 DTO。
- nested success 只向 parent 贡献 `ContinueGraphRouting`；nested node 不能作为 conditional-edge source，direct edge 和 join source 保留。
- 已 terminal `AbortedChild` 不允许通过 parent `resume_failed*()` 以同一 durable child identity restart；只允许 existing `skip_failed()` 或终止 parent。真正 restart 是独立 State/identity 需求。

### 2.3 明确不做

- Kernel Store、数据库、journal、checkpoint、event log、result reference 或 output codec。
- 把 output mirror 写入 `GraphRunState`、`DomainState` 或其他 durable snapshot。
- 进程重启后恢复 transient values，或用 process-local cache 冒充 crash recovery。
- retry/backoff、Graph exactly-once、Port 幂等补偿、多 worker arbitration 或新的 nested scheduler。
- optional/merge/delayed-feedback value declaration；本期普通 input binding 全部是 required current-activation value。
- `Graph.Value[T]` contract ID、node handle、字符串 path、字段 wrapper、签名反射、annotation inference 或动态 callable mutation。
- 为旧 shared-input/compile-only API 保留 overload、alias、fallback 或双写路径。
- 以 reconstructed child ID 重启 `AbortedChild`，或让 nested graph output 隐式决定 parent conditional route。
- 公开 `ExecutionLimits` object 或增加第二 limits/recovery-limits 入口；public 仍只接收 `max_supersteps`/`max_parallel_tasks`。
- 为了伪造 node insertion-order independence 而将 resource ID 改为 lexical sort，或在 compiler 里重写 first-seen order。
- 允许 `Graph.Values(...)` 直接接收 mapping、keyword values、`NamedValue` entries 或 private payload，或增加与 `Graph.values()` 并列的 public value constructor。

## 3. 唯一公共 API surface

### 3.1 Callable node：direct mapping

公共声明固定为：

```python
graph.add_node(
    "normalize",
    normalize,
    inputs={
        "raw": Graph.graph_input("raw", str),
        "locale": Graph.graph_input("locale", Locale),
    },
    outputs={
        "text": str,
        "tokens": Tokens,
    },
    resources=("database",),
)

graph.add_node(
    "render",
    render,
    inputs={
        "text": Graph.node_output("normalize", "text"),
        "tokens": Graph.node_output("normalize", "tokens"),
    },
    outputs={"html": Html},
)

graph.set_outputs({"html": Graph.node_output("render", "html")})

passthrough.set_outputs(
    {"request": Graph.graph_input("request", Request)},
)
```

`add_node()`/`set_outputs()` 在返回前完成 local normalization：

1. 先验证 builder 尚可 mutation，并捕获 current immutable `_GraphBuilderState`；该步骤不写 definition/cache/freeze state；
2. 在 detached locals 中接收 parameterized `Mapping[str, ...]` public boundary，并完成 node ID/body、port name、element closed variant、resources 或 graph-output position 的全部 local validation；
3. 按 canonical port name 排序并复制成 frozen、slots node/output candidate，丢弃原 mapping/container 引用；
4. `add_node()` 只有在 node candidate 已完整成功后，才按 current resource first-seen table 推导 node definition 与新增 `ResourceDefinition` 的完整 replacement `_GraphBuilderState`；`set_outputs()` 同样先推导完整 replacement；
5. Graph owner 以一次 `_builder_state = replacement` 提交；提交点之前不修改 nodes、edges、entries、graph outputs、resources、resume codec、compiled cache 或 frozen flag；
6. 不解析跨 node source、scope、execution order 或 topology，这些只由 compiler 负责。

全部 public builder mutators 共用上述 transaction primitive，而不是各自手写 mutate/rollback：`add_node()`、`set_outputs()`、`add_edge()`、`add_conditional_edge()`、`add_join()` 和 `set_resume_codec()` 都先纯构造完整 replacement，再单次提交。任一 local normalization/constructor exception 后，`_GraphBuilderState` 保持调用前同一 immutable value（实现上不发生 assignment），compiled family cache/frozen state 也完全不变；禁止先 append node/edge/resource、失败后尝试 rollback。Compile-time cross-definition semantic failure 仍由第 6.3/9.1 节 atomic family compile/freeze 负责，是另一层 transaction boundary。

`Graph.graph_input(..., value_type)` 与 `outputs=` 共用唯一 canonical type normalization，只接受可由 runtime exact admission 的 concrete nominal `type[ValueT]`。`Any`、`object`、union、parameterized generic alias、unresolved forward annotation 和其他不能形成唯一 runtime descriptor 的对象在 local normalization 拒绝；不做 subclass compatibility、coercion、numeric widening 或 `Optional` 解包。需要转换时增加显式 adapter node。

Python 在函数调用前已经求值 mapping。重复字面量 key 可能被覆盖，重复 `**` 可能由 Python 自身拒绝；normalizer/compiler 不承诺检测不可观察的源码重复 key，也不增加 AST scanner。独立 node/port identity 或 type declaration conflict 仍是 canonical definition error。

对 ordinary callable，`inputs` mapping 本身同时定义 destination input set 和 source binding。因此 compiler 不声称存在另一份 required-name schema，也不报告无独立基准的 ordinary-input `missing/extra binding`：它校验收到的每个 canonical entry。`inputs={}` 明确表示 zero-input node。

每个 graph 必须恰好调用一次 `set_outputs(mapping)`；control-only graph 使用显式 `set_outputs({})`，不能以“未调用”猜测 empty boundary。Source 只允许同 scope graph input 或 ordinary/nested node output；boundary name 可以与 source port name 不同，type 由 source canonical declaration 唯一派生。Compiler 将 source variant 写入 `GraphOutputBinding`：graph-input source 从 admitted graph-input frame 投影，node-output source 从 confirmed publication frame 投影；两者不先合并为一张无 owner 的 value map。

### 3.2 Structured references

- `Graph.graph_input(name, value_type)` 建立 graph input boundary 的 canonical typed reference。
- 同一个 graph input 被多处使用时复用同一个 reference value，不重复声明 type。
- `Graph.node_output(node_id, parameter_name)` 只保存 source node ID 和 source output name；type 由 compiler 从 source `outputs=` declaration 解析。
- source ref 不携带某次运行的 concrete value。
- reference 可以指向文本上稍后调用 `add_node()` 声明的 node；compiler 在完整收集后解析。
- compiler 拒绝 unknown node/port、input/output wrong direction、self reference、nested scope escape、data cycle 和真实执行上不能 guaranteed-before 的 source。

`Graph.node_output(str, str)` 的两个字符串只定位 identity，因此 `NodeOutputRef` 不伪装成 `NodeOutputRef[ValueT]`。Compiler 用 `(definition_scope, node_id, output_name)` 查找 source `OutputDeclaration` 并将其 exact descriptor 写入 `ResolvedInputBinding`；这一关系在 graph compile 时校验，不是 Pyright 关系。

### 3.3 Control edge declaration

`add_edge()`、`add_conditional_edge()` 和 `add_join()` 与 node/output reference 采用同一个“先收集、后编译”边界：

1. builder 调用只校验当前可观察的 Python 参数 shape、名称的 local canonical form 和 closed declaration variant；
2. builder 在 detached candidate 中复用并构造现有 frozen `DirectEdge`、`ConditionalEdge` 或 `JoinEdge`，不保存调用方 container；
3. 全部 local normalization 成功后才由第 3.1 节 transaction primitive 一次性提交 replacement `_GraphBuilderState`；失败 edge 调用不留下 edge、entry 或其他 builder mutation；
4. builder 不检查 endpoint node 当下是否已添加，因此 edge 可以引用文本上稍后声明的 node；
5. compiler 收集完整 node/edge/boundary definition 后，统一校验 unknown endpoint、START/END 位置与方向、route/join source、nested conditional source、重复 declaration、data/direct same-pair 冲突、data cycle、control-loop activation 坐标、scope、entry 与 completion；
6. 任一 edge 语义错误均在 compiled cache、atomic freeze、`StartGraphRun`、commit、resource claim 和 node call 之前返回 stable `GraphValidationError`。

`add_edge(Graph.START, target)` 在 builder 边界继续规范化到现有 `GraphDefinition.entries`，这是 START edge 的唯一 internal canonical representation；不增加 `set_entry()` 或并行 entry truth。`add_edge(source, Graph.END)` 继续使用现有 `DirectEdge` terminal target。Builder 不检查 target/source 存在性或 duplicate entry/edge；它们与 automatic entry、graph output completion 的合成只由第 7 节 compiler lowering 定义。

Nested node 没有 parent route declaration；其 success 只能投影 `ContinueGraphRouting`。因此 compiler 必须拒绝 nested node 作为 `ConditionalEdge.source`，而不是等 child 完成后交给 routing validator 失败。Nested node 仍可作为 direct-edge source 或 join source；需要基于 child output 选择 route 时，用户增加一个显式 ordinary router node 并让 conditional edge 以该 router 为 source。

### 3.4 Nested graph overload

Nested node 使用独立 overload：

```python
parent.add_node(
    "child",
    child_graph,
    inputs={
        "query": Graph.graph_input("query", Query),
    },
)
```

- parent `inputs` 必须与 child compiled graph-input boundary exact match；missing/extra/type mismatch 在 compile 时拒绝。
- parent 与 child 在 public typing 上使用同一 `Graph[GraphValueT]` universe，避免 nested adapter 擦除泛型；该上界不代替 compiler 对每个 child boundary port 做 exact-type 比较。
- nested overload 省略并拒绝 parent `outputs` mapping；node outputs 直接来自 child 的 `set_outputs()` boundary，parent 不重复声明 type。
- nested overload 同样省略并拒绝 parent `resources`；resource requirements 由 child 内部 ordinary nodes 唯一声明，parent nested activation 不创建虚假 acquisition。
- `Graph.node_output("child", "result")` 引用 child 同名 graph output boundary。
- child completed boundary 只形成 parent nested node 的 output frame；parent routing 固定为 `ContinueGraphRouting`，不透传 child 内部最后一个 route，也不从 output value 推断 route。
- nested node 不能作为 parent conditional source；direct edge 与 join source 合法。基于 child output 的 branch 必须由显式 ordinary router node 拥有。
- parent/child definition 的 atomic freeze 与运行协议见第 9 节。

### 3.5 Callable outcome、concrete values、run 与 continuation

Ordinary callable 的 public return contract 只使用 `Graph` namespace，不在用户签名中暴露 owner-internal `NodeOutcome`：

```text
module-level TypeVars:
    GraphValueT       # Graph instance/callable/run universe only
    FactoryValueT     # bare static factory generic only

Graph.SuccessOutcome[GraphValueT]
Graph.FailureOutcome
Graph.InterruptOutcome

Graph.Outcome[GraphValueT] =
    Graph.SuccessOutcome[GraphValueT]
    | Graph.FailureOutcome
    | Graph.InterruptOutcome

@overload
Graph.values() -> Graph.Values[Never]

@overload
Graph.values(**values: FactoryValueT) -> Graph.Values[FactoryValueT]

Graph.success(
    output: Graph.Values[FactoryValueT],
    *,
    route: str | None = None,
) -> Graph.SuccessOutcome[FactoryValueT]

Graph.failure(reason: str) -> Graph.FailureOutcome
Graph.interrupt(request_payload: bytes) -> Graph.InterruptOutcome
```

`GraphValueT` 与 `FactoryValueT` 都由模块级 `TypeVar(...)` 唯一定义，但不得复用：前者只由 `Graph[GraphValueT]` instance、callable/run/continuation universe 约束；后者在每次裸 `Graph.values(...)`/`Graph.success(...)` 调用上独立推导。Python 3.11 的 `Never`、`TypeVar`、`overload` 与其他 typing symbols 全部模块级导入，不在 class/function 内动态导包或重定义。`Graph.graph_input(..., type[ValueT])` 同样使用独立的模块级 `ValueT`，只在该 method call 上推导。禁止用 class-bound TypeVar、`Any`、`object`、bare generic、overload-erasing cast 或 implementation fallback 填补 static factory 的 inference context。

Concrete value ABI 的 owner 固定为新增的 `execution/graph/values.py`。该模块唯一声明 `FactoryValueT`、`GraphValueT_co`、`NamedValue[GraphValueT_co]`、`_GraphValues[GraphValueT_co]`、四类 internal frame 和全部 entry/frame normalization；`Graph.Values` 是 `_GraphValues` 的直接 class alias，`Graph.values()` 是对该模块 canonical `_make_graph_values()` factory 的唯一 public typed delegation。`_graph.py` 不重新复制 keyword validation、sorting、entry construction 或 empty-frame logic；outcome/resume/request/result/run-context/engine 也只消费同一 owner 的 types/factories。

`Graph.Values` 的 constructor policy 是 factory-only，且与 public class alias 不冲突：`_ValuesConstruction` 与 `_GraphValues` 都是 `frozen=True, slots=True, kw_only=True` dataclass；前者要求 owner singleton `_seal: InitVar[_ValuesSeal]` 并先校验 identity，后者的 stored `_entries` 使用 `init=False`，generated constructor 只要求 owner-private `_construction: InitVar[_ValuesConstruction[GraphValueT_co]]` 与 `_seal: InitVar[_ValuesSeal]`。Canonical factory 完成 name/value closed normalization、canonical name ordering 和 immutable copy 后，以同一 singleton seal 构造 token，再把 token 与 seal 交给 `_GraphValues`；`__post_init__` 校验 exact owner identity 后才安装 tuple。Token/seal 均不存储、不参与 fields/equality/hash/repr/serialization。两种 private types、singleton、`NamedValue` 和 internal frame constructors 都不从 `values.py`、`graph.__init__`、`execution` 或 `Graph` public surface 重导出。因而 `Graph.Values()`、`Graph.Values(raw=...)`、传入 raw mapping/entries 或新建伪 seal/token 都不能形成合法实例；public alias 只支持 annotation、只读访问和 `isinstance` narrowing，不存在第二 wrapper 或 alternate constructor。

Pyright-visible overload 必须直接声明在 public facade attribute 上，不能用 `values = staticmethod(_make_graph_values)`、class-variable assignment 或 cast 转接 owner overload。唯一形状为：

```python
class Graph(Generic[GraphValueT]):
    Values = _GraphValues

    @staticmethod
    @overload
    def values() -> "Graph.Values[Never]": ...

    @staticmethod
    @overload
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]": ...

    @staticmethod
    def values(**values: FactoryValueT) -> "Graph.Values[FactoryValueT]":
        return _make_graph_values(**values)
```

`Generic`、`Never`、`overload`、两个 TypeVar、`_GraphValues` 与 `_make_graph_values` 都在模块级导入或定义。返回 annotation 使用 public forward name，不能把 `_ValuesConstruction`、`NamedValue` 或 private entry tuple 泄漏到 public method signature。两个 facade overload 只拥有 public inference shape；implementation body 只调用 values owner，不能自行 normalize。Python 3.11 strict Pyright 必须证明 heterogeneous keywords 得到 value union、zero-argument 得到 `Values[Never]`、covariant read/`isinstance` 保留，并对 `Graph.Values()`/keyword direct call 报错；若上述 explicit method declarations 不能成立，必须停在 typing design，不得退回 assignment、`Any`、`Unknown`、cast 或第二 factory。

Zero-argument overload 的顺序和结果是唯一规则：`Graph.values()` 精确返回 `Graph.Values[Never]`。`Never` 只表示该 immutable frame 没有 entry，不会成为 runtime value 或 compiled descriptor。由于 `Graph.Values` covariant，empty frame 可以直接传给 `Graph[PipelineValue].run()`，也可以在返回 `Graph.Values[PipelineValue]` 的 callable 中作为 plain success。`Graph.success(Graph.values(), route=...)` 在返回表达式或 annotated assignment 中从 expected `Graph.SuccessOutcome[PipelineValue] | Graph.Outcome[PipelineValue]` context 推导 `FactoryValueT = PipelineValue`；若先无 context 保存，其已知类型就是 `Graph.SuccessOutcome[Never]`，不能事后绕过 invariant outcome widening。需要分步保存时必须在 empty `Graph.Values[PipelineValue]` 或 `Graph.SuccessOutcome[PipelineValue]` binding 上显式提供 expected type。所有路径都必须是零 `Unknown`，不增加 empty-frame class、special outcome variant 或第二 factory。

三个 outcome variants 是 final、frozen、slots 且只由 factory 构造的 canonical node outcomes，不是 facade wrapper，不复制第二份 result model。`Graph.SuccessOutcome.output` 是 immutable `Graph.Values[GraphValueT]`，`route` 是 `str | None`；`Graph.FailureOutcome.failure` 是 canonical `str`；`Graph.InterruptOutcome.request_payload` 是 `bytes`。Canonical `_GraphSuccessOutcome`/`_GraphFailureOutcome`/`_GraphInterruptOutcome` constructors 都要求 `execution/graph/outcome.py` 的 exact module-private `_OutcomeSeal` identity；同模块 canonical factory 持 seal，`Graph.success()`/`failure()`/`interrupt()` 直接委托该 owner。Graph-namespaced aliases 仍指向这些 concrete classes，供 annotation 与 `isinstance` narrowing 使用，但 `Graph.SuccessOutcome(...)` 等 public direct call 缺少 seal，不能形成合法实例。Seal/type/constructor 不从 owner module 或 `Graph` 重导出，且不以 `final`/`frozen` 冒充 construction control。

Callable 直接返回 `Graph.Values[GraphValueT]` 表示 plain success，与 `Graph.success(output)` 完全等价，都产生 `ContinueGraphRouting`。只有 `Graph.success(output, route="...")` 产生 `SelectGraphRoute`。Runtime 在 node call 之后、`SettleGraphNode` candidate/publication 之前，按唯一顺序完成 outcome variant、output frame 和 routing admission：

1. conditional source 的 success 必须带且只能带一个 compiled declared route；plain `Graph.Values` 或 `Graph.success(..., route=None)` 在此失败；
2. non-conditional source 的 success 必须是 plain route；携带任何 route 在此失败；
3. unknown route、missing route 和 unexpected route 复用 existing execution-owned `RoutingError` family，已发生的 node/resource side effect 不回滚，但不得产生 success settlement、publication 或 downstream activation；
4. failure/interrupt 不需要 route，也不会因该 node 同时是 conditional source 而伪造 success route。

Public `run()` 只保留以下三个 closed overload。`Graph.ResumeAction[GraphValueT]` 是 resume factories 产生的 invariant closed union；`Graph.Commit[GraphValueT]` 是唯一 async callback Protocol，其调用形状为 `Graph.Transition[GraphValueT] -> Awaitable[Graph.State]`：

```text
new run:
    Graph.run(
        values: Graph.Values[GraphValueT],
        /,
        *,
        run_id: str | None = None,
        commit: Graph.Commit[GraphValueT] | None = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> Graph.Result[GraphValueT]

transient continue:
    Graph.run(
        *,
        state: Graph.State,
        continuation: Graph.Continuation[GraphValueT],
        resume: tuple[Graph.ResumeAction[GraphValueT], ...] = (),
        commit: Graph.Commit[GraphValueT] | None = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> Graph.Result[GraphValueT]

control-only recovery:
    Graph.run(
        *,
        state: Graph.State,
        resume: tuple[Graph.ResumeAction[GraphValueT], ...] = (),
        commit: Graph.Commit[GraphValueT] | None = None,
        max_supersteps: int = 1_000,
        max_parallel_tasks: int = 64,
    ) -> Graph.Result[GraphValueT]

Graph.Result[GraphValueT] =
    Graph.CompletedResult[GraphValueT]
    | Graph.AbortedResult[GraphValueT]
    | Graph.AwaitingResumeResult[GraphValueT]
```

`run_id` 只属于 new-run overload；`values` 只能作为 new run 的 positional-only input；state overload 不接收 `values`/`run_id`，new-run overload 不接收 `state`/`continuation`/`resume`。Public 不接收 `ExecutionLimits` 或 `limits=`：三个 overload 都把两个 keyword 传给 existing `ExecutionLimits` 唯一 owner，并在 graph compilation/cache、continuation/resume admission 和任何 State mutation 之前首先构造、校验一个 immutable effective limits value。非 exact positive integer limits 统一抛出 `Graph.ExecutionLimitError`，不安装 compiled cache，不冻结 builder，也不产生 commit/resource/node side effect。

`Graph.Transition[GraphValueT]` 是 final、frozen、invariant 且只由 family driver 产生的 public commit value，字段固定为：

```text
scope: tuple[str, ...]
previous_state: Graph.State | None
command: owner-internal GraphRunCommand
candidate_state: Graph.State
result:
    Graph.SuccessResult[GraphValueT]
    | Graph.FailureResult
    | Graph.InterruptResult
    | None
```

`scope=()` 是 root 的唯一 canonical 表示；非空 tuple 每个 segment 是从 root 到目标 child scope 的 nested node ID。`previous_state is None` 当且仅当对该 exact scope 提交 `StartGraphRun`；`candidate_state` 是 `reduce_graph_run(previous_state, command)` 的 exact scoped successor，callback 必须返回与它结构相等的 authoritative `Graph.State`。`command` 保留真实 owner-internal closed command union 作为只读 commit evidence，不在 `Graph` 下重导出 command constructors 或创建并列 state API。

Canonical `_GraphTransition` constructor 额外要求 `execution/_graph.py` family driver 独占的 exact module-private `_TransitionSeal` identity；seal 不属于上述 public fields，不进入 equality/repr/serialization，也不从 module 或 `Graph` 导出。Public `Graph.Transition` 是同一 concrete class alias，保留 annotation/`isinstance` 能力，但普通调用方不能通过 `Graph.Transition(...)` 独立产生合法 commit evidence。Family driver 在 reducer candidate 与 admitted result 都已形成后使用 seal 一次构造，不提供 public/private alternate constructor。

`result` 只在该 scope 的 `SettleGraphNode` transition 上非空，且已通过 exact outcome/output/routing admission；其他 start/fence/resume/claim/resolve/abort transition 一律为 `None`。三个 public result aliases 直接指向 canonical execution result nominal types，不复制 wrapper，并只暴露以下只读领域字段：

```text
Graph.SuccessResult[GraphValueT]:
    node_id: str
    output: Graph.Values[GraphValueT]
    route: str | None

Graph.FailureResult:
    node_id: str
    failure: str

Graph.InterruptResult:
    node_id: str
    request_payload: bytes
```

Canonical `_GraphSuccessResult`/`_GraphFailureResult`/`_GraphInterruptResult` constructors 同样要求 `execution/result.py` 唯一 `_CommitResultSeal` identity；只有 outcome admission/settlement projection 的 private canonical factory 持 seal。它与 `_OutcomeSeal`、`_TransitionSeal` 分属各自 owner，禁止为方便而建立 global seal registry。Public result class aliases 只用于 read/narrowing，直接 constructor 调用不能形成合法 execution result；`isinstance(result, Graph.SuccessResult)` 仍保持 invariant `GraphValueT` narrowing。

Execution-owned task identity、routing contribution 和 settlement coordinate 仍保持 private，不要求用户导入 `GraphTask`、`GraphRoutingContribution` 或 `GraphRunCommand`。Commit callback 不得修改 transition/result；root/child 都使用同一 callback Protocol，不建 child commit entry point。

以下是必须通过 strict Pyright 的最终用户形状：

```python
from typing import TypeAlias

from mote_kernel.execution import Graph

PipelineValue: TypeAlias = str | Locale | Tokens | Html


async def plain(values: Graph.Values[PipelineValue]) -> Graph.Values[PipelineValue]:
    return values


async def empty_plain(_values: Graph.Values[PipelineValue]) -> Graph.Values[PipelineValue]:
    return Graph.values()


async def choose(values: Graph.Values[PipelineValue]) -> Graph.Outcome[PipelineValue]:
    return Graph.success(values, route="publish")


async def empty_choose(_values: Graph.Values[PipelineValue]) -> Graph.Outcome[PipelineValue]:
    return Graph.success(Graph.values(), route="publish")


async def reject(_values: Graph.Values[PipelineValue]) -> Graph.Outcome[PipelineValue]:
    return Graph.failure("rejected")


async def request_review(_values: Graph.Values[PipelineValue]) -> Graph.Outcome[PipelineValue]:
    return Graph.interrupt(b"approve?")


async def commit(transition: Graph.Transition[PipelineValue], /) -> Graph.State:
    if transition.scope == ():
        root_candidate = transition.candidate_state
    else:
        child_path = transition.scope
        root_candidate = transition.candidate_state
    command = transition.command
    admitted = transition.result
    if isinstance(admitted, Graph.SuccessResult):
        node_id = admitted.node_id
        output = admitted.output
        route = admitted.route
    elif isinstance(admitted, Graph.FailureResult):
        node_id = admitted.node_id
        failure = admitted.failure
    elif isinstance(admitted, Graph.InterruptResult):
        node_id = admitted.node_id
        request_payload = admitted.request_payload
    return root_candidate


result = await graph.run(
    Graph.values(raw=raw_value, locale=locale_value),
    run_id="document-run",
    commit=commit,
    max_supersteps=1_000,
    max_parallel_tasks=64,
)

empty_values: Graph.Values[PipelineValue] = Graph.values()
empty_success: Graph.SuccessOutcome[PipelineValue] = Graph.success(empty_values)
empty_result = await control_graph.run(
    Graph.values(),
    run_id="control-run",
    commit=commit,
)

continued = await graph.run(
    state=result.state,
    continuation=result.continuation,
    resume=(graph.resume_failed("render"),),
    commit=commit,
    max_supersteps=1_000,
    max_parallel_tasks=64,
)

recovered = await graph.run(
    state=result.state,
    resume=(),
    commit=commit,
    max_supersteps=1_000,
    max_parallel_tasks=64,
)
```

示例中的 local variables 只演示 narrowing/readability；不形成第二份 State、command 或 result owner。

三个 Result variants 都是 final、frozen、invariant nominal values，并且都具有 non-optional `state: Graph.State` 与 `continuation: Graph.Continuation[GraphValueT]`。`CompletedResult` 独占 `outputs: Graph.Values[GraphValueT]`；`AbortedResult` 独占 canonical abort view；`AwaitingResumeResult` 独占 failures/interrupts views。调用方通过 `isinstance(result, Graph.CompletedResult)` 等 closed narrowing 访问 variant-specific fields；其他 variants 根本不暴露这些字段，不以 optional、空 sentinel 或运行时 accessor exception 模拟 sum type。`outputs` 的 immutable covariance 不改变 containing Result/continuation 的 invariant universe。

三个 run overload 不能交叉传入 `values`/`state`/`continuation`。Continuation 的唯一 public type 是 opaque、seal-constructed `_GraphContinuation[GraphValueT]`，其 private payload 是以下 closed union：

```text
_CompleteContinuationSnapshot[GraphValueT]
    | _RecoveredContinuationSnapshot[GraphValueT]
```

- 新 run 建立 complete snapshot；它保存该 logical graph family 已 admission 的全部 root/child graph-input frames、全部 scoped confirmed publications、nested child state/frame snapshots 和 exact root-state value binding。以该 snapshot 继续时始终保持 complete variant。
- control-only recovery 在 State definition identity/version 与当前 compiled graph 匹配后建立 recovered snapshot。它只保存从 authoritative State 可 admission 的 frame，以及从本次 state-only lineage 开始新产生的 graph-input/publication/child snapshots；历史 frame 缺失是该 nominal variant 的合法语义，不叫 partial，也不伪造缺失值。Recovered lineage 不 opportunistically 升级为 complete variant。
- 每个 completed/aborted/awaiting-resume Result 都必须携带上述两种合法 continuation 之一；不存在“state-only Result 没有 continuation”的分支。
- complete snapshot 缺少其应有 frame，或任一 variant 含 extra/inconsistent frame，属于 malformed continuation；recovered snapshot 合法缺少历史 frame，是否足够执行由 compiled availability preflight 裁决，不能按 complete-snapshot completeness 规则拒绝。
- 每次 atomic graph-family compile 成功时生成一个 execution-private immutable `_CompiledFamilyIdentity`；两种 snapshot 都携带该 exact token，不仅比较可重复的 definition ID/version。Token 不进入 State、error text、serialization 或 persistence protocol。
- result/continuation 共享一个 immutable `_RootStateBinding` value；三个 Result variants 的 `state` 都只从该 binding 投影。后续 invocation 必须传入与 binding 结构相等的 state value 与同一 continuation：相同 revision 但任一字段不同、foreign family 或交叉配对在任何 fence/resume/claim commit 前拒绝；完全相等的 frozen copy 必须接受，不要求对象 identity。
- continuation 只能由 `Graph.Result` 产生；其 final private implementation 的构造必须提供 module-private seal singleton，public `Graph.Continuation` 仅作为类型命名空间，调用方无法构造合法实例。它没有 encode/decode、copy/pickle contract、Store path 或跨进程协议。
- 可实现的“stale”只指 family token、结构不相等的 root/child state value、scope snapshot 或 canonical frame 不匹配，以及 foreign、extra 或 malformed continuation。不使用 Store/global mutable flag 时，runtime 无法判定一个自洽的旧 state/continuation pair 是否已被新快照取代；最新性和单 driver 责任属于调用方/authoritative commit owner。
- 有 matching continuation 时，child-only progress 可以保持 root state value/revision 不变，但必须返回新 continuation snapshot。没有 continuation 的 state-only invocation 若 current root State 含 Pending nested activation，就无法区分其 child 是 Missing/Active/Completed/Aborted；必须在任何 fence/start/claim 前返回 `GraphValueUnavailableError`，不得把它列为可执行的 child-only recovery。

State-only invocation 以及后续 recovered-continuation invocation 在任何 fence/resume/claim/child-start commit 前，必须使用 compiled graph 的直接 `transition: FrontierTransitionPlan[GraphValueT]` field 对“从当前 authoritative State 到下一 public boundary”的全部可能 transition 做保守证明，而不是只检查当前 Pending node。Public boundary 是三个 Result dispositions 之一，或 exact planner coordinate 上的 `ExecutionLimitError`；limit boundary 不伪装成 Result、availability error 或 graph abort。该 proof 不是第二套 graph interpreter；compiler 将 runtime 与 recovery 共用的 immutable `FrontierTransitionPlan` lowering 一次，runtime 执行 concrete branch，recovery evaluator 只在同一 plan 上传播 value availability。

每个 immutable `RecoveryTransferState[GraphValueT]` 的 stored semantic basis 固定覆盖：current scope 的 exact status/superstep/frontier settlements（其中已包含 Pending 与 State-owned route）、join/resource control、本 invocation 唯一 effective `ExecutionLimits`、当前 live node set、全部 admitted graph-input/publication/resume-input/child-boundary availability coordinates、current child dispositions、本次全部 admitted resume/skip facts，以及本 invocation 新产生的 nested activations。已完成 task 已由 frontier settlement 唯一拥有，不重复保存 started history；Pending/routing、runnable/resource-waiting positions 与 available parallel slots 只能由 frontier + live + limits 确定性投影，不能作为第二份 stored truth。Stored basis 的所有字段都参与 structural equality/hash/dedup；route 已由 exact State settlement 或 concrete action 拥有，不复制 `future outcome/route` 或 `unpublished` 字段。禁止以 `compare=False`、缓存字段或缩短 tuple 排除任何会改变后续 transfer 的事实。

Availability 只使用以下 canonical coordinates，不把 concrete frame entry 放进 abstract state：

```text
ScopeRunCoordinate(
    scope,
    graph_run_id,
)

StableActivation(
    scope_run,
    superstep,
    node_id,
)

GraphInputAvailabilityCoordinate(
    scope_run,
    complete_graph_input_frame_descriptor_identity,
)

PublicationAvailabilityCoordinate(
    activation,
    complete_node_output_frame_descriptor_identity,
)

ResumeInputAvailabilityCoordinate(
    activation,
    complete_node_input_frame_descriptor_identity,
)

ChildBoundaryAvailabilityCoordinate(
    child_scope_run,
    complete_graph_output_frame_descriptor_identity,
)

ChildRecoveryDisposition(
    child_scope_run,
    ChildControlStateCoordinate | MissingChild,
)

AdmittedResumeFact(
    target_activation,
    action_variant,
    interrupt_identity | None,
    skip_reason | None,
    concrete_route | None,
)
```

`FrameDescriptorIdentity` 是 compiler 在 canonical definition/port order 上生成的 schema-only structural coordinate：`(definition identity, frame kind, canonical owner/plan ordinal)`；它只索引唯一 exact descriptor table，本身不包含 runtime graph run/parent activation，也不包含或排序 runtime type object、module/qualname/repr、callable 或 concrete value。任何 concrete frame key 都必须同时带 runtime identity，禁止以 descriptor、scope path 或 definition identity 单独索引。

从 compiled value binding 读取 availability 时，GraphInput 与 NodeOutput 两类 nominal source 各只有一个 typed
coordinate constructor：`engine/routing.py` 的 `_graph_input_coordinate()` 组合 exact compiled graph descriptor 与
`ScopeRunCoordinate`，`_node_output_coordinate()` 组合 exact `NodeOutputPort`、调用边界已解析的 superstep 与 canonical
publication descriptor。admission、routing 与 resume materialization 复用这两个 owner；optional publication
selection 仍由每个调用边界以自己的 error variant 校验和解析，constructor 不接受 optional selection 或 error。
resume-input coordinate 只由 `engine/resume_input.py::_resume_input_coordinate()` 从调用边界已验证的 exact
`StableActivation` 与 `MaterializationPlan[GraphValueT]` 构造；activation validation 不移入 helper，也不因 override
路径跳过。State settlement、recovery outcome、substitution 和 post-commit installation 不是这三类 binding source，
不伪造 port 进入这些 constructor，继续由各自边界形成 coordinate。

Resume-input runtime、executor admission 与 recovery invariant 这组 consumer 只通过
`engine/resume_input.py::_require_node_materialization(graph, node_id)` 从 authoritative
`CompiledGraph.transition.materializations` 取得 exact `MaterializationPlan[GraphValueT]`；unknown node 固定抛
`SnapshotMismatchError("node input references an unknown compiled materialization")`。该 query 不缓存或复制 compiled fact，
recovery 也不直接读取 materialization map。Continuation validator 为保持 coordinate/frame integrity 的统一错误契约、routing
为解释 binding/readiness，继续各自直接读取同一 immutable map；本 query 不冒充全仓 global accessor。Compiled scope traversal
同样只有 topology-owned `_compiled_graph_at_scope(root, scope)`：invocation 与 recovery 按原 segment 顺序复用它，unknown segment
固定抛 `SnapshotMismatchError("scope references unknown nested node <segment!r>")`，不建立 family map、path normalization 或
forwarding alias。

`ScopeRunCoordinate` 是 execution 对 existing State identity 的唯一 transient projection，不写入 State。Root coordinate 固定为 `ScopeRunCoordinate((), root_state.run_id)`；给定 parent nested `StableActivation(parent_scope_run, parent_superstep, nested_node_id)`，child coordinate 唯一为 `ScopeRunCoordinate(parent_scope_run.scope + (nested_node_id,), child_graph_run_id(parent_scope_run.graph_run_id, parent_superstep, nested_node_id))`。Family driver 在 child start 和 recovered child admission 都复用 existing `child_graph_run_id()` validation；同一 scope path 在不同 parent superstep 再次 activation 会得到不同 child run ID，因此不是同一 coordinate。`StableActivation` 直接嵌入 `ScopeRunCoordinate`，publication/resume/settlement 不再各自保存可漂移的 raw scope/run pair。

New-run root 在 acknowledgement 前也不得使用 descriptor-only 或 provisional alternate key：facade 先一次性生成/校验最终 `run_id`，让 graph-input candidate 使用 `ScopeRunCoordinate((), start_command.run_id)` 完成 exact frame admission，但暂不安装到 run context/continuation；`StartGraphRun` exact successor 经 commit 确认后，必须验证 `successor.run_id == start_command.run_id`，再以同一个 coordinate 原子安装 root graph-input frame。Commit mismatch、exception 或 cancellation 只丢弃 candidate，不留下 frame；确认后的 `successor` 就是上述 `root_state`，不存在第二 root-run identity 或 pre-State fallback。Child start 对称地由 exact parent activation 预先派生 expected child coordinate，并只在 child `StartGraphRun` successor 确认后安装 child input frame。

`ChildControlStateCoordinate` 是在 exact child State/snapshot binding admission 通过后、相对于 `ChildRecoveryDisposition.child_scope_run` 生成的 transfer-relevant control projection，覆盖 definition/parent/status/superstep/frontier settlement variants/routing/join/resource/revision/codec identity；child State 的 run ID 必须先与 `child_scope_run.graph_run_id` exact match，projection 内不再维护第二份可独立漂移的 run identity。`control is None` 唯一表示 `MissingChild`，并仍携带由 parent activation 预先派生的 expected `child_scope_run`，不是只有 path 的无 generation sentinel；Active/Completed/Aborted 直接由 non-optional control 的 exact status 派生，不重复保存 projection discriminator。State-owned opaque resume bytes、failure/request payload 等用户内容不进入 coordinate，已 admission override 只由 `ResumeInputAvailabilityCoordinate` 表示。

Recovery 的 private nested outcome 只携带 nested `node_id` 与 canonical scope boundary；boundary kind、availability
和 child disposition 不再作为 outcome 字段重复保存。所有 consumer 直接读取 boundary 的 equality-participating
`kind`/`availability`/`control`，其中 `ChildRecoveryDisposition` 逐字段从 `ScopeControlStateCoordinate` 投影；不得从
boundary 中 `compare=False` 的 concrete State 重建 child identity。Missing child 仍只由初始 child projection 的
`control is None` 表示，不伪造 scope boundary。

Frame-level availability 只有在 matching runtime coordinate 下 frame key set、每项 exact type 与完整 compiled descriptor 全部 admission 成功后才存在；因此 coordinate presence 同时证明 scoped-run identity、completeness 和 descriptor identity，不创建 partial-port bitmap。Child control coordinate 与 Missing/Active/Completed/Aborted 状态由 `ChildRecoveryDisposition` 拥有；child graph-input/`ChildBoundaryAvailabilityCoordinate` presence 由全局 `RecoveryAvailabilityCoordinates` 唯一拥有，不在 disposition 中复制。`GraphRunContext`/complete or recovered continuation snapshot 继续唯一持有第 8.2 节 immutable `ScopedFrameIndex`：它以 `GraphInputAvailabilityCoordinate | PublicationAvailabilityCoordinate | ResumeInputAvailabilityCoordinate | ChildBoundaryAvailabilityCoordinate` 的 closed typed segments 映射到各自 immutable concrete frame；seed admission 只把真实存在且完整的 frame 投影为同一个 coordinate，recovery successor 也只增删 coordinates。相同 exact coordinate 的 different frame 是 invariant violation；不同 `ScopeRunCoordinate` 的 frame 必须并存，不能覆盖、误判 duplicate 或复用旧值。`AdmittedResumeFact` 只拥有 target、action kind、interrupt ID、skip reason 与 concrete route 的五字段 equality；resume-input presence 只由 equality-participating `RecoveryAvailabilityCoordinates.resume_inputs` 拥有。每个 non-skip admitted action 必须在 recovery seed admission 时，用 shared scope/materialization owner 和唯一 coordinate constructor 推导 exact coordinate并确认其 presence；missing 或 same activation/wrong descriptor 均在任何 mutation 前抛 `SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")`。Skip action 不声明本 invocation resume-input requirement，也不删除或拒绝历史 resume coordinate。Worklist 从不比较、排序、hash、repr 或复制任意用户 concrete value。

唯一 transfer 规则为：

1. 从 exact authoritative State、已校验 effective limits 与 snapshot 中真实存在且已完整 admission 的 frames/child snapshots 建立 seed；先从 root/child State 和 exact parent activation 生成统一 `ScopeRunCoordinate`，再让每个 frame 只投影上述 runtime + descriptor coordinate，concrete value 留在 incoming run context/continuation。State-owned opaque override、zero-input activation 和已 admission resume candidate 只形成对应 scoped activation 的 `ResumeInputAvailabilityCoordinate`；resume/skip request 只形成五字段 `AdmittedResumeFact`。Availability projection 完成后、family construction与proof之前，每个 non-skip action必须存在shared owner推导的exact resume-input coordinate，skip则绕过该current-input invariant；二者都不扩张其他 port。
2. Active seed 先按 existing `FenceGraphExecution` 与 reducer semantics 模拟 exact quiescent successor，再模拟 admitted resume/skip；quiescent seed 不伪造 fence。这一 abstract ordering 与 concrete driver 相同，但 preflight 不提前提交 fence。
3. Invocation seed 中 current frontier 已有 settlement 的 node 保留 State-owned canonical routing contribution，不对该 node 重新枚举 outcome；frontier 全部 quiescent 后才使用这些 concrete contributions 与 State-owned join progress 调用 shared routing resolver。已经给定的 `skip_failed(..., route=...)` 先通过同一 resume validation 投影唯一 simulated skipped settlement，再只沿该 concrete route。二者都不得枚举同一 source 的其他 declared routes。
4. 被 `resume_failed()`、`resume_failed_with()` 或 `resume_interrupted()` 重新置为 Pending 的 ordinary callable，与其他尚未执行的 ordinary callable 一样，availability proof 只展开 canonical success projection：conditional success 枚举全部 declared `SelectGraphRoute` contributions，non-conditional success 固定为 `ContinueGraphRouting`。Shared prepare 在 claim 前一次性 admission 当前 frontier 的全部 callable input；failure/interrupt 不产生 publication 或下一 frontier control contribution，因此由 quiescence invariant 单独证明最终只能到 awaiting boundary，不加入 worklist outcome/permutation 状态。一旦 success projection 产生 settlement，其后 routing 只读取该 concrete contribution。
5. 每当 shared runtime transition 即将在 RUNNING/Pending State 上调用 existing `plan_tasks()`，recovery 必须在同一 scope/state coordinate 先执行 `state.superstep >= effective_limits.max_supersteps` 检查。命中时把该 abstract branch 标记为 `EXECUTION_LIMIT` terminal analysis boundary，不再展开 loop、不检查该 boundary 之后的 value requirement，也不构造 Result/continuation/abort。
6. 未命中 superstep limit 时，shared selector 才按 `effective_limits.max_parallel_tasks - live_count`、canonical task order、resource first-seen acquisition/FIFO waiter 与 nested barrier 选择 runtime 可启动的 tasks。Recovery 对 ordinary callable 只推进该 selector 产生的 canonical all-success completion path，并按 conditional declaration 枚举 success routes；不枚举 completion/acknowledgement permutation，也不构造因 parallel slot、resource 或 child barrier 不可启动的 node completion。Precomputed nested completion 继续按 existing semantics 不占 ordinary live-task slot。
7. Typed failure/interrupt 只把对应 node 变成无 publication、无后继 control contribution 的 settlement。因为第 6 条之前已完整 admission 当前 frontier 全部 callable input，任一 failure/interrupt projection 都不会再要求下一 frontier materialization；shared frontier quiescence 保证其余 callable 无论 success/failure/interrupt，最终 disposition 只能是 AWAITING_RESUME。因此 recovery 记录并验证这条 invariant，而不复制 session 的 sibling outcome 或 completion-order 状态机。
8. Success branch 只有在 simulated output/routing admission 与 settlement acknowledgement 后才加入对应 `PublicationAvailabilityCoordinate`；因此 A failure + B success 的 Awaiting result branch 必须包含 B publication coordinate。Runtime 仍须在实际 admission/ack 后才能把 concrete frame 安装到 run context/continuation mapping。
9. Nested transfer 复用第 9 节 family driver：每个 root/child scope 都消费同一 effective limits 并在自己的 State/superstep 上调用同一 planner check，不建 nested limit；child 命中 limit 时以原 `ExecutionLimitError` 终止 family invocation，不伪造 `AbortedChild`/parent failure。Parked child 不阻断其他 runnable child，parent ordinary sibling 继续受 existing child barrier 约束。
10. Frontier 无 Pending 后才按 shared prepare/routing lowering 判断下一步：AWAITING_RESUME 是 Result boundary；SETTLED 沿 concrete contributions resolve 到下一 frontier 或 completion；COMPLETED/ABORTED 投影对应 Result。任一真实可达 branch 在其首个 Result/limit boundary 前需要缺失的历史 graph input/publication/child frame，整个 invocation 在任何 State mutation、resource claim 或 node call 前返回 `GraphValueUnavailableError`；不可能 concrete route 与 limit boundary 之后的缺值都不得拒绝 recovery。
11. 每个 successor 先 canonicalize 全部 coordinate/disposition/action tuples，再以完整 `RecoveryTransferState` structural equality/hash 查询 seen set；只有每个 semantic field 都相等才合并。`RecoveryTraversalKey` 只决定待处理 states 的 canonical visit order，即使 sort key 相同也不得合并 unequal transfer states。

Recovery proof 只把 `EXECUTION_LIMIT` kind 作为可终止的 abstract branch，不另存与 `_ScopeBoundary.control` 重复的 limit DTO；也不因某个 conditional success route 可能触发 limit 就提前向调用方抛错或拒绝其他 exit route。Concrete runtime 只在实际 route/outcome 到达该 `plan_tasks()` coordinate 时抛出 `Graph.ExecutionLimitError`。对 active seed，concrete driver 先提交 exact fence successor 与 optional admitted resume/skip successor，再在 planner 抛 limit；对已 quiescent seed，不伪造 fence，完成 optional admitted resume/skip 后直接在相同 planner coordinate 抛错。这与“非法 limits 参数在任何 mutation 前拒绝”是两个不同边界。

若上述 proof 通过，state-only invocation 可以执行 Pending zero-input、State-owned opaque override、active fence recovery、settled empty-output completion，或直接投影 current root `ABORTED`/`AWAITING_RESUME` disposition。Concrete branch 到达 completed、aborted 或 awaiting-resume 时，返回包含本轮真实 frame 的 recovered continuation；先到达 execution-limit boundary 时则抛出 `Graph.ExecutionLimitError`，不构造 Result 或伪 continuation。该 snapshot 让已返回 Result 的后续 invocation 继续按相同规则 fail closed，不尝试补回不可恢复的历史值。

`CompletedResult.outputs` 由 compiled `GraphOutputBinding` 逐字段投影：source 是 graph input 时从 matching admitted root/child graph-input frame 读取，source 是 node output 时从 matching confirmed publication frame 读取；empty boundary 返回 canonical empty `Graph.Values`。该 view 不复制第二份 value truth。`Graph.Values[GraphValueT]` 与 `CompletedResult[GraphValueT].outputs` 在 Pyright 中保留 graph-wide value universe，但不承诺 `outputs["html"]` 的逐键 exact static type；`html -> Html` 由 compiled graph-output binding 和 runtime exact admission 保证。

### 3.6 Nested resume address

现有 resume constructors 增加结构化 scope path，并全部迁移到 `Graph.Values[GraphValueT]` frame ABI，而不增加第二 resume entry point：

```python
graph.resume_failed("leaf", scope=("child",))
graph.resume_failed_with(
    "leaf",
    Graph.values(query=replacement_query),
    scope=("child",),
)
graph.resume_interrupted(
    "leaf",
    interrupt_id,
    Graph.values(query=answer),
    scope=("child",),
)
```

完整 public signatures 固定为：

```text
Graph.resume_failed(
    node_id: str,
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[GraphValueT]

Graph.resume_failed_with(
    node_id: str,
    values: Graph.Values[GraphValueT],
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[GraphValueT]

Graph.resume_interrupted(
    node_id: str,
    interrupt_id: str,
    values: Graph.Values[GraphValueT],
    *,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[GraphValueT]

Graph.skip_failed(
    node_id: str,
    reason: str,
    *,
    route: str | None = None,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[GraphValueT]
```

- empty scope 表示 root graph；每个 segment 是 parent scope 中的 nested node ID。
- compiler/runtime 逐段验证 graph family membership、definition identity 和 child run coordinates。
- `set_resume_codec(codec_id, version, encoder, decoder)` 的唯一 public codec 形状为 `Graph.Values[GraphValueT] <-> bytes`；它仍是 graph-local input/resume codec，不是 output codec。
- `set_resume_codec()` 先在 detached candidate 中完成 ID/version/typed encoder/decoder 的全部 local normalization，再通过 `_GraphBuilderState` single commit；任何异常或重复/非法 declaration 都不改变现有 codec、definition、cache 或 frozen state。
- `resume_failed_with()`/`resume_interrupted()` 只接收 immutable `Graph.values(...)` override frame。`Graph.run()` 先根据 scope 选择 root/child definition 自己的 codec，完成 encode、decode 与 target node exact input-plan admission，全部成功后才投影 `ResumeGraphNodes`；新 decoded frame 在此阶段只是 execution-local candidate。
- 无 override 的 `resume_failed()` 使用 run context 按 target node compiled bindings 重新 materialize input frame，不读取一份 shared raw run input。缺少 required publication 时在 resume commit 前返回 `GraphValueUnavailableError`。
- scope 指向 child 时使用 child definition 的 codec ID/version 和 decoder；codec identity/version 必须与该 child `GraphRunState` exact match。Encoder/decoder exception、decoded frame missing/extra/wrong type 均为 `GraphValueAdmissionError`，且不得产生 fence/resume/claim commit、resource claim 或 node call。
- State 继续只保存现有 `OverrideGraphNodeInput(opaque bytes)` 与 codec identity/version。`UseStepRequestInput` 在新 ABI 中唯一表示“使用当前 compiled materialization”，不再表示所有 node 共享 `StepRequest.node_input`。
- 新 override candidate 只有在 exact（按现有 State value equality）`ResumeGraphNodes` successor 被 commit callback 确认后，才安装到 run context 并进入可返回 continuation；commit mismatch/exception 时丢弃 candidate，不返回包含该 frame 的 continuation。若 authoritative State 已携带 `OverrideGraphNodeInput`，decoded frame 是 state/codec admission 后的 State-derived value，可直接用于当前 materialization，不伪造新的 Resume acknowledgement。
- `Graph.AwaitingResumeResult` 的 failure/interrupt views 携带 canonical scope path，调用方不解析拼接字符串。
- resume action 仍投影到目标 child/root 的现有 `ResumeGraphNodes` command，不建立新的 reducer path。
- child 仍为 RUNNING/AWAITING_RESUME 时，只能用 structured `scope=("child", ...)` 恢复 child 内部 failed/interrupted leaf，继续同一 deterministic child run。
- child 已 ABORTED 并投影为 parent nested `FailedGraphNode` 后，parent-level `resume_failed("child")`、`resume_failed_with("child", ...)` 以及任何会把该 nested node 重新置为 Pending 的 retry action 均在 resume preflight 返回 `SnapshotMismatchError`；检查先于 fence/resume/claim。允许 existing `skip_failed("child", ...)` 或终止 parent，但绝不丢弃 terminal child snapshot 并以同一 child run ID 重建。
- `Graph.Transition` 按第 3.5 节携带 canonical scope path、`previous_state`、owner-internal command、`candidate_state` 与 admitted result；commit callback 对 root/child 都返回与 `candidate_state` 结构相等的 exact authoritative successor，不保留旧 `next_state` alias。
- 三个 `Graph.Result` variants 的 `state` 只表示 root canonical State；`AwaitingResumeResult.failures/interrupts` 从 root State 与 continuation 中实际存在的 current child State snapshots 派生，不要求 recovered snapshot 拥有历史 value，也不把 child fact 镜像进 root State。

### 3.7 Resource boundary

`resources=("database",)` 保持字符串 tuple 和现有自动登记语义。Resource 是 assembly-time capability ID；input/output port 是 activation-time typed value。两者不共享 declaration factory、generic token 或 identity owner。

Global resource order 的唯一规则是 successful-builder-transaction first-seen order：`Graph` 按已完整 local-normalize 且已提交的 ordinary node definitions 顺序扫描，并在每个 node 内按 `resources` tuple 从左到右扫描；resource ID 首次出现时生成下一个 `ResourceDefinition.order`，后续 node 再引用同 ID 只复用该 definition。同一 node 内重复 resource ID 仍由 compiler fail closed，不以 global dedup 掩盖非法 local declaration。

`add_node()` 在 detached candidate 中先完成 node ID/body/inputs/outputs/resources 的全部 local normalization，再基于调用前 `_GraphBuilderState` 同时推导 node tuple 与 resource table，最后以一次 replacement assignment 提交。Resource registration 不是前置 side effect：任何 local error 都同时放弃 node/resource candidate；随后成功 node 的 first-seen order 与“失败调用从未发生”的 graph 结构完全相等。禁止先调用 `_register_resource_requirements()` 再 normalize node，或通过 rollback/hidden tombstone 保留失败 resource。

因此 successful resource-bearing node 的 builder order 是有意的 public semantics：交换两个已提交 node 并改变 resource first-seen sequence，就产生不同 compiled resource order、claim snapshot 与可观察 commit order，两个 definition 不要求 structural equal。Mapping/edge declaration 顺序仍按 canonical identity 规范化；node 重排只在不改变 resource first-seen order 时才应产生等价 compiled structure。Compiler 只验证并消费 `_GraphBuilderState` 已确定的 resource order，不再按 resource ID 字典序排列。

## 4. Validation owner、错误阶段与副作用边界

### 4.1 唯一 validation 分层

| 阶段 | 唯一 owner | 负责内容 | 不负责内容 |
| --- | --- | --- | --- |
| builder normalization/transaction | `Graph.add_node()` / `set_outputs()` / edge builders / `set_resume_codec()` + `_GraphBuilderState` owner | 当前参数 shape/name/closed variant/position、detached immutable candidate、resource first-seen replacement、single commit | endpoint/source 存在性、跨 node type/topology、runtime route selection、mutate-then-rollback |
| concrete value/frame normalization | `execution/graph/values.py` canonical owner，`Graph.values()` 仅 typed delegation | independent `FactoryValueT` inference、empty `Values[Never]`、name/value normalization、canonical entries、四类 internal frames | graph-instance TypeVar fallback、public `NamedValue`/mapping constructor、port exact topology |
| outcome/resume factory normalization | canonical outcome/resume owners | concrete action/outcome fields、frame variant acceptance | value entry normalization、callable route legality、第二 frame DTO |
| owner-sealed construction | values owner / outcome owner / settlement-result owner / family driver | exact private construction token/seal identity、factory/driver-only canonical nominal construction | public alternate constructor、wrapper DTO、global seal registry |
| execution-limits normalization | existing `ExecutionLimits` owner | 两个 public keyword 的 exact integer/positive validation，构造本 invocation 唯一 effective limits | graph compile、State、recovery-specific limit |
| graph compilation | graph compiler | canonical identity、全部 edge endpoint、source、scope、direction、exact type resolution、nested conditional-source prohibition、data/control、entry/completion、shared frontier transition/recovery/output-projection plans | callable 签名反射、concrete value |
| graph-input admission | public run facade + values owner | boundary key/type、root `ScopeRunCoordinate` 与 continuation invocation shape | node output、descriptor-only frame key |
| continuation/resume admission | public family facade + target compiled graph | family/root-state value equality、complete/recovered snapshot invariant、exact root/child `ScopeRunCoordinate`、codec identity、override encode/decode、aborted-child restart prohibition、target input frame | commit 或 latest-snapshot authority、按 path 隐式补 run identity |
| recovery availability preflight | public family facade + compiled graph 的直接 `transition: FrontierTransitionPlan[GraphValueT]` | exact State settlements、effective limits、admitted resume/skip semantics、完整 frontier quiescence、parallel/resource/nested barrier、Result/limit boundary、scoped-run frame coordinates、full control/availability/child/action equality、canonical success-route requirements 与 failure/interrupt awaiting invariant | 预测 callable concrete output、shortened dedup identity、concrete-value comparison、descriptor/path-only runtime key、另建 routing/join/session/limits 解释器 |
| materialization admission | run context + compiled plan + current authoritative State frontier | confirmed source、current frontier eligibility、exact `ScopeRunCoordinate`/activation、port、type | node return value |
| outcome/output admission | session completion projection + shared routing validator | public outcome variant、declared output key/type/frame identity、conditional missing/unknown route、non-conditional unexpected route | 回滚已经发生的 node/resource side effect |
| graph-output projection | compiled `GraphOutputBindings` + run context | matching scoped-run graph-input source、node-publication source 与 child-boundary coordinate 的 exact view | 持久化 output、按 definition/path 选 frame 或复制 value truth |
| post-settlement resolution | public family driver + State reducer + existing abort command | frontier-local triggers、control-selected readiness、completion boundary；dynamic required-value abort | 伪造未确认 publication 或复制 State control history |
| state acknowledgement | facade + scoped `Graph.Transition` + existing reducer/commit protocol | canonical scope、previous/command/candidate、admitted settlement result、exact returned successor、lease/token/revision | 预测 state、复制 control history 或 output persistence |

各阶段复用 canonical name、type descriptor 和 error construction infrastructure，但不保存 raw mapping 供 compiler 重验，也不复制两套 validator。

`FrontierTransitionPlan` 是 runtime 与 recovery 的唯一 control-transfer lowering，且由 `CompiledGraph.transition` 直接拥有：它引用同一组 canonical frontier、routing、join、resource、nested barrier、planner-limit 和 completion plans。Recovery evaluator 只在该 plan 上附加 immutable availability-coordinate transfer，不拥有第二份 edge table、route resolver、join accumulator、session scheduler、child barrier 或 limits policy。Current State settlement、admitted resume/skip action 是产生 concrete settlement 还是恢复 Pending、conditional success-route set，以及 `max_supersteps`/`max_parallel_tasks` 允许的 transition，都只能由该 shared plan 判定；worklist seen 只用完整 `RecoveryTransferState` equality，separate traversal key 不拥有 merge semantics。

Concrete admission 使用 declaration 中的 canonical runtime descriptor 做 exact check；普通 class value 采用 `type(value) is declared_type` 语义，不接受 subclass 或隐式转换。

不新增第二个公共 compiler facade。每次 `run()` 的第一个动作是构造并校验 effective `ExecutionLimits`；首次有效 `run()` 随后在构造任何 `StartGraphRun` 或触发 commit 前同步执行上述 graph compilation。因此本文所说“compile 时报错”是指该无执行副作用的 compilation phase，不是等节点运行后才报错。首次 compile 成功后才原子安装 immutable compiled family 并禁止后续 builder mutation。

### 4.2 Callable ABI 的校验边界

Callable 必须满足统一 parameterized async frame ABI，但 runtime compiler 不调用 `inspect.signature()`，不读取 annotations，也不判断任意 callable 的 `async`/return annotation。约束分为：

- strict Pyright consumer 在 public `add_node()` overload/Protocol 边界校验 `Graph.Values[GraphValueT] -> Awaitable[Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]]` 这一整体 shape，不推导 frame 内某个字符串键的 exact type，也不暴露 internal `NodeOutcome`；
- normalizer 将已类型检查的 callable 包装为唯一 owner-internal adapter；
- compiler 校验 canonical body variant、adapter presence 以及该 node 的 compiled input/output descriptors；
- compiler 根据 closed node body/outcome admission、conditional declaration 与 nested fixed routing 生成 canonical success-route set；recovery 不读取签名、猜测 concrete route 或建立 future-outcome 字段；
- runtime 对实际 public outcome/plain frame 先做 variant、output 和 routing admission，再由 shared frontier transition owner 消费其 concrete success/failure/interrupt settlement；conditional missing/unknown route 与 non-conditional unexpected route 不进入 settlement。

绕过 public typing 人工伪造 owner-internal object 不会把 compiler 扩张为通用 Python object decoder。

### 4.3 Side-effect guarantee

| 失败 | 最晚发现时点 | 保证 |
| --- | --- | --- |
| non-exact-integer or non-positive `max_supersteps`/`max_parallel_tasks` | `run()` 首个 normalization | 无 compile/cache/freeze、fence/resume/claim/child-start commit、resource claim 或 node call |
| invalid local builder argument/candidate | builder call before single commit | `_GraphBuilderState`、nodes/edges/entries/outputs/resources/resume codec、compiled cache/frozen state 与调用前完全相等；无 compile/run side effect |
| public direct Values/outcome/result/transition construction | owner-private construction-token/seal check | 不能形成合法 nominal value；不泄漏 `NamedValue` entries，无 alternate constructor 或 execution side effect |
| invalid graph semantics/reference/edge topology | compile | 无 compiled cache、Start commit、resource claim、node call |
| invalid graph input | run admission | 无 Start/fence/resume commit、resource claim、node call |
| mismatched/foreign/malformed continuation | continuation admission | complete snapshot 缺失、任一 variant extra/inconsistent 时无 fence/resume/claim commit、resource claim、node call；recovered variant 的合法历史缺失不在此阶段误判 |
| invalid resume scope/codec/override frame | resume admission | 无 fence/resume/claim commit、resource claim、node call |
| aborted nested child restart action | resume admission | `SnapshotMismatchError`；无 fence/resume/claim commit、child start、resource claim 或 node call |
| recovered lineage 的真实可达 branch 缺 required history/child snapshot | shared recovery availability preflight | `GraphValueUnavailableError`；State-owned settled route 与 admitted concrete skip route 不枚举其他 route；无 fence/resume/claim/child-start commit、resource claim 或 node call |
| required source unavailable | materialization preflight | 对该 activation 无 claim/resource/node call |
| required value 在已确认 skip/settlement 后动态缺失 | post-settlement resolution | 对当前 graph scope 提交现有 `AbortGraphRun`，返回携带 exact aborted state/continuation 的 `Graph.AbortedResult`；无 target frontier/claim/resource/node call |
| malformed node/child outcome/output/route | outcome/output admission | node call 及必要 claim/resource 已发生；无 success settlement、publication、downstream activation 或 graph-output projection；允许既有 fence/release/cancellation cleanup |
| valid limits 在 concrete planner coordinate 耗尽 | existing `plan_tasks()` | quiescent seed 直接抛 `ExecutionLimitError`；active seed 先确认 fence successor 再抛；已确认早期 transition 保留，无伪 Result/abort/continuation |
| commit mismatch/exception | acknowledgement | 未确认 output 不 publication，新 resume override candidate 不安装；按现有 session/fence/cancellation 协议停止 |

不得再以“所有 admission failure 都零 commit/resource/node call”作为测试断言。

### 4.4 Error taxonomy

- `GraphValidationError`：builder/compile definition、reference、scope、direction、type resolution、topology、entry/completion。
- `ExecutionLimitError`：public limits 不是 exact positive integer，或 runtime/recovery shared planner 在 exact scope/state coordinate 观察到 `state.superstep >= effective_limits.max_supersteps`；不是 Result 或 graph abort。
- `SnapshotMismatchError`：continuation family/root-state binding、scope state/definition、graph/state resume codec identity/version 不匹配，或试图以同一 child identity restart terminal `AbortedChild`。
- `GraphValueAdmissionError`：graph input、resume codec encode/decode、override frame 或 node output 的 concrete key/type/frame mismatch。
- existing `RoutingError` family：node success 在 outcome admission 阶段 missing/unknown/unexpected route；发生在 node call 之后、settlement/publication 之前，通过 `Graph.Error` public base 传播。
- `GraphValueUnavailableError`：shared whole-invocation recovery transfer 发现从 current exact State 与 admitted resume/skip action 形成的 branch 在首个 Result disposition 或 exact planner limit boundary 之前需要缺失的 graph input/publication/child snapshot；必须早于任何 fence/resume/claim/child-start mutation。Same State 下 availability/child coordinates 不同的 transfer states 不合并，因此存在 coordinate 的 branch 不会被 false reject，缺失 coordinate 的 branch 也不会被 false accept。State-owned settled route 或 admitted concrete skip 已排除的 route 不是 reachable path，limit boundary 之后的 requirement 也不再 reachable，二者都不能触发该错误；已确认 settlement 后才由不可预测 concrete output outcome 导致的 dynamic missing 仍进入 abort Result，不裸抛该异常。
- `GraphValuePublicationError`：publication token、acknowledgement 或 duplicate publication 违反 invariant。

已确认 skip/settlement 后，若 control-selected target 的 required input 或 graph completion boundary 缺值，execution 根据 compiled coordinates 构造 state-owned `GraphAbortReason`，再投影现有 `AbortGraphRun` 终止当前 root/child scope；这是已提交 state 之后的可观察 result，不是 exception taxonomy。Root abort 直接返回 `Graph.AbortedResult`；child abort 通过现有 `AbortedChild -> TaskFailure` 投影到 parent，root Result 仍携带与当前 lineage variant 一致的 continuation。

错误只包含 stable graph/scope/node/port/source coordinates，不包含 callable repr、任意 concrete value repr 或对象地址。

## 5. 类型责任与 graph compile 裁决

### 5.1 最终责任分工

本方案明确放弃“让 Pyright 从 runtime string key 证明逐端口 exact type”这一目标，不再建立旨在证明该 destination-local string-key 关系的 Pyright spike，也不再把它作为 production 前置阻断。Python API overload/generic/constructor 的可实现性仍必须用 strict Pyright signature spike 固定，例如第 3.5 节 `Graph.values()` explicit static overload；两类责任不得混淆。逐端口关系只在收集完整 graph definition 后才存在：

- `Graph.node_output("normalize", "text")` 的 exact type 来自 `normalize.outputs["text"]`；
- `inputs={"local": source_ref}` 的 local name 与 source type 关系来自该 node 的 canonical binding；
- `set_outputs({"html": source_ref})` 的 result type 来自 source declaration；
- source 是否 guaranteed-before、是否越 scope、是否被 control path 绕过只能在完整 data/control topology 上判定。

因此 exact port contract 的唯一权威是 compiled descriptor，不是 Python annotation、callable 签名或表面泛型。`NodeOutputRef` 保持非泛型；`Graph.Values[GraphValueT]["raw"]` 和 `Graph.CompletedResult[GraphValueT].outputs["html"]` 在静态上只返回 graph-wide `GraphValueT`，不伪造 `str`/`Html` 的逐键推导。

这不是关闭 Pyright。Strict Pyright 仍负责以下可静态证明的 Python 关系：

- `Graph[GraphValueT]`、`Graph.Values[GraphValueT]`、outcome、commit result、`Graph.Transition`、Result、continuation、callable/commit Protocol 与 internal adapter 的 graph-wide value universe 不丢失；
- `Graph`、三个 Result variants、Result union 与 continuation 使用同一个 invariant universe；不同 universe 不能 widening、交叉配对或传回 `run()`；
- public overload 不能交叉传入 declaration ref、type descriptor、concrete frame、state、continuation、resume 或 `run_id`；
- callable/Graph body、`Graph.Outcome`、`Graph.ResumeAction` 与 Result 是 closed unions，`run()` 是三个 closed overload；
- `Graph.success()`/`failure()`/`interrupt()`、plain Values return、root/child `Graph.Commit` 与 transition-result narrowing 可在只导入 `Graph` 时 strict type-check；
- bare `Graph.values(...)`/`Graph.success(...)` 由独立的模块级 `FactoryValueT` 在每次 factory call 上推导 heterogeneous union，zero-argument overload 为 `Values[Never]`，进入 typed new run/plain/routed success 时零 `Unknown`；
- 所有 generic 都完整 parameterize，TypeVar 与 import 位于模块级，无 `Any`、`object` boundary、bare generic、reflection、ignore 或 generic-erasing cast。

### 5.2 唯一 generic carrier 模型

Public/runtime carrier 使用 graph-wide value universe，而不声称 key-specific static schema：

```text
GraphValueT                      # invariant graph/callable universe
GraphValueT_co                   # immutable value/frame projection
FactoryValueT                    # independent bare-static-factory universe
Never                           # zero-entry Values bottom type only

Graph[GraphValueT]
Graph.Values[GraphValueT_co] = _GraphValues[GraphValueT_co]  # exact class alias
Graph.SuccessOutcome[GraphValueT]
Graph.FailureOutcome
Graph.InterruptOutcome
Graph.Outcome[GraphValueT]
Graph.ResumeAction[GraphValueT]
Graph.Commit[GraphValueT]
Graph.Transition[GraphValueT]
Graph.SuccessResult[GraphValueT]
Graph.FailureResult
Graph.InterruptResult
Graph.Continuation[GraphValueT]
Graph.CompletedResult[GraphValueT]
Graph.AbortedResult[GraphValueT]
Graph.AwaitingResumeResult[GraphValueT]
Graph.Result[GraphValueT] =
    Graph.CompletedResult[GraphValueT]
    | Graph.AbortedResult[GraphValueT]
    | Graph.AwaitingResumeResult[GraphValueT]

Graph.graph_input(name, type[ValueT]) -> GraphInputRef[ValueT]
Graph.node_output(node_id, output_name) -> NodeOutputRef
Graph.Values(...) -> no legal public constructor
@overload Graph.values() -> Graph.Values[Never]
@overload Graph.values(**values: FactoryValueT) -> Graph.Values[FactoryValueT]
Graph.success(Graph.Values[FactoryValueT], *, route: str | None = None)
    -> Graph.SuccessOutcome[FactoryValueT]
Graph.failure(str) -> Graph.FailureOutcome
Graph.interrupt(bytes) -> Graph.InterruptOutcome

Graph.Values[GraphValueT_co].__getitem__(name) -> GraphValueT_co
Graph.CompletedResult[GraphValueT].outputs -> Graph.Values[GraphValueT]
all Result variants.state -> Graph.State
all Result variants.continuation -> Graph.Continuation[GraphValueT]
Graph.Commit[GraphValueT]:
    Graph.Transition[GraphValueT] -> Awaitable[Graph.State]
Graph.Transition[GraphValueT].result ->
    Graph.SuccessResult[GraphValueT]
    | Graph.FailureResult
    | Graph.InterruptResult
    | None

NodeCallable[GraphValueT]:
    Graph.Values[GraphValueT]
        -> Awaitable[
            Graph.Values[GraphValueT]
            | Graph.Outcome[GraphValueT]
        ]

callable add_node:
    operation: NodeCallable[GraphValueT]
    inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef]
    outputs: Mapping[str, type[GraphValueT]]

nested add_node:
    child: Graph[GraphValueT]
    inputs: Mapping[str, GraphInputRef[GraphValueT] | NodeOutputRef]
    outputs/resources: rejected
    success routing: ContinueGraphRouting
```

`execution/graph/values.py` 中的 `_GraphValues[GraphValueT_co]` 是 public ABI 的唯一 immutable nominal implementation，内部使用 parameterized immutable `NamedValue[GraphValueT_co]` entries，不传播 bare mapping；只有这个只读 value/frame projection 使用 covariance。Graph input frame、node-local input frame、node output frame 和 graph output view 也只由该模块拥有，仍是不同 nominal types，仅共享同一 `GraphValueT` 关系、canonical entry tuple 与 normalization infrastructure。Materialization 和 graph-output projection 只在同一 `GraphValueT` universe 内由 owner factories 构造、复制或重命名已 admission entry，不通过 cast 把擦除类型恢复回来。

`Graph.Values` 不另建 facade wrapper，而是 exact `_GraphValues` class alias；它的可调用 class surface 不是 value input API。只有 `Graph.values()` 可以把 public keywords 交给 values owner，后者生成 private `_ValuesConstruction`、持 exact `_ValuesSeal` 并构造 canonical instance。`_ValuesConstruction`/`_GraphValues` 均使用 keyword-only dataclass construction，stored `_entries` 为 `init=False`；public error surface 最多出现 private `_construction`/`_seal` 参数，不暴露 `NamedValue` entry tuple。Internal graph-input/materialization/output factories 直接接收 canonical owner types，不经 `Graph.values()` 反向调用 facade。`NamedValue`、construction token、seal、internal frames 与 canonical internal factories 均不 public re-export；strict consumer 对 `Graph.Values()`/`Graph.Values(...)` 的任何 direct call 都报错，但 annotation、`isinstance(frame, Graph.Values)`、covariant read 与 `__getitem__` 保持可用。

Import DAG 也是 carrier contract 的一部分：`ports.py` 可以被 `values.py` 用于 canonical port name/descriptor primitives，但不得导入 concrete values；`outcome.py`、`graph/resume_input.py`、`request.py`、`result.py`、`run_context.py`、engine 和 `_graph.py` 只能从 exact `execution.graph.values` owner 导入 carrier/frame symbols，不能各自声明 `NamedValue`/frame/dataclass，也不能经 `execution.graph.__init__` 聚合导入造成回环。`values.py` 不导入上述高层模块或 `Graph` facade，因此 factory delegation 不形成 cycle。

`FactoryValueT` 只出现在裸 static factory signatures，不能出现在 `Graph[...]` storage、compiled definition、run context、Result 或 continuation。Non-empty `Graph.values(raw=..., locale=...)` 由所有 keyword value 的共同 union 推导 `FactoryValueT`；`Graph.success(frame)` 保留该 exact factory universe。Zero-argument overload 固定先于 variadic overload，并返回 `Values[Never]`；`Values` covariance 允许 empty frame 进入 `Graph[GraphValueT].run()`/plain success，invariant success outcome 则只通过 call-site expected type 把 `FactoryValueT` contextually 绑定到 `GraphValueT`。无 expected context 的 `Graph.success(Graph.values())` 保持 `SuccessOutcome[Never]`，不得自动 widening。Facade 必须使用第 3.5 节直接声明的 `@staticmethod` overload set；把 owner factory 赋给 class attribute 会丢失 Pyright 的 heterogeneous keyword call shape，属于门禁失败。该关系已经由 Python 3.11 strict Pyright signature test 固定，不留 assignment/alternate overload/codegen/cast 方案。

`Graph.Outcome[GraphValueT]`、`Graph.ResumeAction[GraphValueT]`、`Graph.Commit[GraphValueT]`、`Graph.Transition[GraphValueT]` 和 `Graph.SuccessResult[GraphValueT]` 使用 invariant `GraphValueT`，避免 callback input、resume frame 或 admitted result 绕过 graph-wide universe。Nongeneric failure/interrupt outcome/result variants 可以进入任一同 universe closed union，但不得用 `Any`/`object` 模拟该关系。Public outcome/commit aliases 只指向 canonical owner nominal types，不引入 facade DTO 副本。

`Graph.CompletedResult[GraphValueT]` 必须与 `AbortedResult`、`AwaitingResumeResult`、`Result`、`Continuation` 一样 invariant。虽然其 `outputs` 属性返回 immutable `Graph.Values[GraphValueT]`，但 Result 本身同时携带可作为后续 `run()` 输入的 invariant continuation；禁止通过 `CompletedResult[Dog] -> CompletedResult[Animal]` 间接得到伪造的 `Continuation[Animal]`。Result disposition 通过 invariant closed nominal union 窄化，不以 `None`、空 tuple 或 boolean + optional fields 擦除 variant-specific typing。

Public typing tests 必须包含负向 universe case：`CompletedResult[Dog]` 不能传给接收 `CompletedResult[Animal]` 的函数，任一 `Result[UniverseA]` 的 continuation、`Transition[UniverseA]` callback、`ResumeAction[UniverseA]` 或 `SuccessResult[UniverseA]` 不能与 `Graph[UniverseB]` 交叉传回 `run()`；uncontextualized `SuccessOutcome[Never]` 也不能 widening 为 `Outcome[PipelineValue]`。正向 case 允许 independent immutable `Graph.Values[Dog] -> Graph.Values[Animal]` covariance、bare heterogeneous factory union inference，以及 `Values[Never]` 进入 typed empty run/plain success；expected outcome context 可以在 factory call 当场推导 `FactoryValueT = GraphValueT`，不得连带 widening 已构造的 outcome、transition、Result 或 continuation。

持续门禁中每个负向契约必须使用独立 fixture，固定为恰好一条预期 Pyright error，并同时断言契约相关类型、universe、variance 与 diagnostic rule 片段；禁止把多个非法程序放进一个“出现任意 error 即通过”的聚合 fixture。具体矩阵至少分别覆盖 `Graph.Values`/sealed aliases direct construction、`Result`、`Continuation`、concrete `CompletedResult`、`Transition`、cross-universe commit callback、cross-universe `ResumeAction` run admission、`SuccessResult`、uncontextualized `SuccessOutcome[Never]` 与 cross-universe new-run values。AST type-erasure lint 的模块说明、helper 命名、错误信息与文档不得声称自己证明这些关系。

一个 strict consumer 可以使用静态上界：

```python
from typing import TypeAlias

PipelineValue: TypeAlias = str | Locale | Tokens | Html

graph: Graph[PipelineValue] = Graph("document-pipeline")
```

`PipelineValue` 只是 Python 静态 carrier universe，不建立 name-to-type mapping，不参与 compile，不能替代 `Graph.graph_input(..., type)` 或 `outputs={name: type}`，因而不是第二份 graph contract truth。Parent 与 nested child 在 public generic boundary 使用同一 `GraphValueT`；它们的实际 boundary exact types 仍由 compiler 比较。

若 callable/result consumer 需要在 strict Python 代码中对 `GraphValueT` union 做具体操作，使用普通 `type(...) is ...`、`isinstance()` 或 pattern matching 缩小静态上界。这种窄化不创建 graph contract，不参与 compiler，也不允许反向覆盖 compiled descriptor。Public API 不新增 `read(name, expected_type)` 之类要求调用方重复声明 port type 的 accessor。

### 5.3 Compile-time exact validation

Compiler 从 canonical declaration 构建每个 port 的唯一 exact descriptor table，并在安装 compiled graph 前完成：

1. 验证每个 graph input/output declaration 与 node output declaration 都是可 exact runtime admission 的 nominal type；
2. 解析每个 `GraphInputRef`/`NodeOutputRef`，拒绝 unknown node/port、wrong direction、self reference、scope escape 与 unresolved descriptor；
3. 为 ordinary callable 的每个 destination input 生成带 source exact descriptor 的 `ResolvedInputBinding`；`inputs` mapping 本身就是该 callable 的 input schema，不比较不存在的第二份 callable schema；
4. 对 nested child，将 parent 每个 resolved source descriptor 与 child compiled graph-input boundary 做 missing/extra/exact-type 比较；
5. 对 `set_outputs()`，解析每个 source descriptor 并建立 graph-output boundary，明确保存 `GraphInputPort | NodeOutputPort` source variant，拒绝缺失、方向错误或无法 guaranteed-before-completion 的 source；
6. 拒绝 nested node 作为 conditional-edge source；nested success 的 `ContinueGraphRouting`、direct/join eligibility 在同一 topology plan 中冻结；
7. 为每个 ordinary node 固定唯一 outcome-admission rules：conditional source 只接收 declared `SelectGraphRoute`，non-conditional source 只接收 `ContinueGraphRouting`，failure/interrupt 不伪造 route；这些规则不另存为第二 DTO；
8. 保留 successful builder transactions 已提交的 resource first-seen order，验证 unique IDs/order 与 exact node requirements，不在 compiler 中 lexical re-sort；
9. 将 frontier settlement、routing/join resolution、resource waiter、nested barrier/family driving、planner-limit boundary 与 completion 统一 lowering 为一个 immutable `FrontierTransitionPlan`，供 runtime concrete execution 与 recovery proof 共同使用；
10. Recovery evaluator 只在 `CompiledGraph.transition` 引用的 shared transition/materialization/publication plans 与同一 outcome-admission rules 上附加 availability transfer：current State 中已有的 settlement 与已 admission 的 concrete skip 使用唯一 contribution；resume 后重新成为 Pending 的 conditional callable 与其他尚未执行的 conditional callable 一样，其 canonical success projection 包含全部 declared route branches；
11. 将 exact schema descriptors 写入 immutable materialization、outcome/output-admission 和 graph-output projection plans；每类 nominal plan 同时固定它在 runtime 调用 identity owner 构造 `GraphInputAvailabilityCoordinate`、`PublicationAvailabilityCoordinate`、`ResumeInputAvailabilityCoordinate` 或 `ChildBoundaryAvailabilityCoordinate` 的唯一位置，不保存或猜测 invocation run ID，runtime 也不重新解析 declaration；
12. 同时完成第 3.3 节 edge semantics 和第 7 节 data/control availability 校验。

任一失败都是 deterministic `GraphValidationError`，不安装 partial compiled cache，不冻结 partial graph family，不产生 `StartGraphRun`、commit、resource claim 或 node call。Concrete graph input 与 callable output 在 compile 时尚不存在，仍分别由 run admission 和 output admission 按同一 compiled exact descriptor 检查；不将这两类 runtime value error 伪称为 compile-time 可证明事实。

## 6. Canonical definition 与 compiler plan

### 6.1 Port identities

Compiler 使用不同的结构化 identity：

```text
GraphInputPort(definition_scope, name)
NodeInputPort(definition_scope, node_id, local_name)
NodeOutputPort(definition_scope, node_id, output_name)
GraphOutputPort(definition_scope, boundary_name)
```

`InputBinding[GraphValueT]` 同时保存 destination `NodeInputPort` 与 source ref。Source identity 不冒充 destination identity；同一 source 绑定到不同 local names 时生成不同 materialization targets。

对 ordinary callable，destination exact type 按已冻结 API 就是 source resolution 得到的 descriptor；compiler 将它写入新的 `ResolvedInputBinding[GraphValueT]`，不虚构独立 producer/consumer declaration。对 nested graph，child input boundary 提供独立 expected descriptor，parent source 必须 exact match。

### 6.2 Frozen nominal model

各 canonical owner module 使用以下 frozen、slots、fully-parameterized nominal shapes：

```text
NominalTypeDescriptor[GraphValueT_co]
GraphInputRef[GraphValueT_co]
NodeOutputRef

InputBinding[GraphValueT]
InputBindings[GraphValueT]
ResolvedInputBinding[GraphValueT]
ResolvedInputBindings[GraphValueT]
OutputDeclaration[GraphValueT_co]
OutputDeclarations[GraphValueT]
GraphOutputBinding[GraphValueT]
GraphOutputBindings[GraphValueT]
DataTriggerPlan
MaterializationPlan[GraphValueT]
FrontierTransitionPlan[GraphValueT]
RecoveryTransferState[GraphValueT]
RecoveryTraversalKey
FrameDescriptorIdentity
ScopeRunCoordinate
StableActivation
GraphInputAvailabilityCoordinate[GraphValueT]
PublicationAvailabilityCoordinate[GraphValueT]
ResumeInputAvailabilityCoordinate[GraphValueT]
ChildBoundaryAvailabilityCoordinate[GraphValueT]
ChildControlStateCoordinate
ChildRecoveryDisposition
AdmittedResumeFact
_ScopeBoundary(kind=EXECUTION_LIMIT)
AdmittedGraphInput[GraphValueT]
ConfirmedPublication[GraphValueT]
AdmittedResumeInput[GraphValueT]
ConfirmedChildBoundary[GraphValueT]
ScopedFrameIndex[GraphValueT]

DirectEdge
ConditionalEdge
JoinEdge

NamedValue[GraphValueT_co]             # values.py only
_ValuesConstruction[GraphValueT_co]   # values.py construction capability
_ValuesSeal                           # values.py singleton identity
_GraphValues[GraphValueT_co]          # Graph.Values exact class alias
GraphInputFrame[GraphValueT_co]
NodeInputFrame[GraphValueT_co]
NodeOutputFrame[GraphValueT_co]
GraphOutputView[GraphValueT_co]
ResumeInputBinding[GraphValueT]
_ResumeAction[GraphValueT]

_GraphSuccessOutcome[GraphValueT]
_GraphFailureOutcome
_GraphInterruptOutcome
_OutcomeSeal
_GraphSuccessResult[GraphValueT]
_GraphFailureResult
_GraphInterruptResult
_CommitResultSeal
_GraphCommit[GraphValueT]
_GraphTransition[GraphValueT]
_TransitionSeal

SettledFrontierCoordinate       # derived from the current GraphRunState; never persisted
InitialActivationOrigin | DataActivationOrigin | ControlActivationOrigin
_CompiledFamilyIdentity
_RootStateBinding
_ContinuationSeal
_CompleteContinuationSnapshot[GraphValueT]
_RecoveredContinuationSnapshot[GraphValueT]
_GraphContinuation[GraphValueT]       # final, seal-constructed implementation
_CompletedGraphResult[GraphValueT]
_AbortedGraphResult[GraphValueT]
_AwaitingResumeGraphResult[GraphValueT]

_GraphBuilderState[GraphValueT]
CallableNodeDefinition[GraphValueT] | NestedGraphNodeDefinition[GraphValueT]
GraphDefinition[GraphValueT]
CompiledGraph[GraphValueT]
```

这些名称是实施时的唯一 shape 分工，不是候选列表。`InputBindings` 与 `OutputDeclarations` 不共享包含 optional fields 的宽 DTO；graph boundary、node-local input、node-local output、result view 和 continuation 也不得合并成一个隐藏 scope 的宽 storage。`NamedValue`、`_GraphValues`、`GraphInputFrame`、`NodeInputFrame`、`NodeOutputFrame`、`GraphOutputView`、`_ValuesConstruction` 与 `_ValuesSeal` 全部且只属于 `execution/graph/values.py`；其他模块只能导入和消费，不能定义 shadow DTO/factory。`AdmittedGraphInput`、`ConfirmedPublication`、`AdmittedResumeInput`、`ConfirmedChildBoundary` 与 `ScopedFrameIndex` 全部且只属于 `execution/run_context.py`，分别组合 values owner 的 exact frame 与 identity owner 的 exact coordinate；四类 record 不能合并成 optional-field record 或另建第二 frame map。`_CompleteContinuationSnapshot | _RecoveredContinuationSnapshot` 是唯一 private snapshot union，`_CompletedGraphResult | _AbortedGraphResult | _AwaitingResumeGraphResult` 是唯一 invariant Result union；三个 Result implementations 与 `_GraphContinuation` 均使用 invariant `GraphValueT`，不得使用 `GraphValueT_co`。不得再增加 optional continuation/output/failure DTO。

Public `Graph.Values`、`Graph.SuccessOutcome`/`FailureOutcome`/`InterruptOutcome`、`Graph.SuccessResult`/`FailureResult`/`InterruptResult`、`Graph.Commit`、`Graph.Transition` 与 `Graph.ResumeAction` 分别只是上述 canonical private implementations/closed unions 的 Graph-namespaced 只读表面，不新增 wrapper、copy 或 parallel owner。`_ValuesConstruction` 与 `_GraphValues` 都是 keyword-only dataclass；前者 constructor 要求 values owner exact private `_ValuesSeal`，后者 stored entries 使用 `init=False` 且 constructor 要求 exact private `_ValuesConstruction` 和 `_ValuesSeal`。Outcome constructors 要求 outcome owner 的 exact `_OutcomeSeal`；commit-result constructors 要求 result owner 的 exact `_CommitResultSeal`；transition constructor 要求 family driver 的 exact `_TransitionSeal`。每个 seal-constructed canonical dataclass 使用 required keyword-only `InitVar`，在 `__post_init__` 做 singleton/capability identity check；construction token/seal 不成为 stored/public field，不参与 equality/hash/repr/serialization。四组 seal/capability owner 都是 module-private、互不共享且不导出，public alias 只读/narrowing，不能作为合法 direct constructor。`Graph.values()` 的两个 `@staticmethod` overload 必须直接声明在 facade class 上，再由 implementation body 单向调用 values owner；禁止通过 class attribute assignment 转接 overload。`_GraphTransition.command` 直接持有 owner-internal `GraphRunCommand`，但 public 不得重导出 command constructors。`_ScopeBoundary(kind=EXECUTION_LIMIT)` 只是 recovery worklist 的 terminal analysis variant，不是 State、Result、continuation 或 public exception payload。

`CompiledGraph` 的 graph-input declaration 只存在于 `graph_input_descriptor.declarations`；它不再存储 `graph_inputs` mirror。其 compiled topology 直接以 `transition: FrontierTransitionPlan[GraphValueT]`、graph input/output descriptors、nodes、nested graphs、resources 与 resume binding 组成，不保留 `recovery` wrapper 或 entries/materializations/graph-output/resource-order forwarding property。

`FrontierTransitionPlan` 是 runtime/recovery 共用的唯一 control-transfer definition，具体持有 entries、routing/join/data maps、materialization、每个 node 的 publication `FrameDescriptor`、graph completion 与 resource order，并由 `CompiledGraph.transition` 直接拥有；callable 分类只由 `CompiledGraph.nodes` 中的 `CallableNodeDefinition` nominal variant 派生，nested 分类只由 `CompiledGraph.nested_graphs` keys 派生，不在 transition 中存储镜像集合。`RecoveryTransferState` 是 invocation-preflight worklist 中的 immutable transient abstract state，其 dataclass/nominal equality 与 hash 覆盖 exact frontier control/resource/live-task positions、effective `ExecutionLimits`、全部 scoped-run availability coordinates、child dispositions、admitted actions 与 invocation-new-child identities；route 已由 State settlement/action 拥有，不复制 future outcome/route 或 unpublished facts。Seen set 直接使用完整 state identity，不另造缩短 dedup tuple。`RecoveryTraversalKey` 是单独的 canonical ordering projection，即使 key collision 也不允许合并 unequal states。`FrameDescriptorIdentity` 只拥有 schema；`ScopeRunCoordinate` 只拥有 runtime graph-run scope；`StableActivation(scope_run, superstep, node_id)` 在二者之上拥有一次 activation。Graph-input/publication/resume-input/child-boundary availability 必须分别组合 exact runtime coordinate 与 descriptor，不能按 path/definition/descriptor 隐式补 generation。Concrete frames 继续由 run context/continuation 持有，绝不进入 abstract equality/hash/sort/repr。`InitialActivationOrigin`、`DataActivationOrigin` 和 `ControlActivationOrigin` 仅是当前 compiler/runtime candidate 的局部元数据，不进入 State、continuation 或任何 history index。Activation/resolution 不在 run context 中建立第二组 nominal ledger；它们由当前 authoritative `GraphRunState` 派生。Public `Graph.Values[GraphValueT]` 是 values owner 中这些具体 frames 的唯一 facade ABI，internal nominal scope 不因此丢失。Public `Graph.Continuation[GraphValueT]` 只指向 seal-constructed `_GraphContinuation[GraphValueT]` 的只读类型表面，不暴露 seal、snapshot variant 或第二 implementation。`NodeOutputRef` 必须保持非泛型，禁止再添加一个无法由公共参数推导的表面 `ValueT`。

`_GraphBuilderState` 是 Graph builder 的唯一 assembly truth，frozen 保存 node definitions、edges、entries、graph-output declaration、按 first-seen 排列的 resource definitions 与 resume codec declaration；next resource order 只由 ordered resource tuple 长度派生，不保存第二 counter。Compiled family/cache/frozen flag 仍由 Graph owner 单独持有，但 builder transaction 不修改它们。每个 public mutator 从 current state 纯推导完整 replacement 并单次 assignment，禁止暴露 mutable list/dict、分步 append 或 rollback ledger。

### 6.3 Compile 顺序

```text
snapshot one committed immutable _GraphBuilderState per family member
    -> collect canonical definitions from those snapshots
    -> validate graph family recursion and atomic freeze candidates
    -> canonicalize graph/node/input/output and control-edge identities
    -> validate all edge endpoints, START/END positions, nested conditional sources and duplicate/conflict declarations
    -> resolve graph input and node output refs into exact descriptor bindings
    -> assign schema-only FrameDescriptorIdentity and attach each descriptor to its nominal graph-input/publication/resume-input/child-boundary coordinate-construction plan; no runtime run ID is guessed
    -> build data-readiness and independent control graphs
    -> validate scope/direction/exact type/data cycles and control-loop coordinates
    -> lower initial entries and frontier-local producer-to-target data trigger plans
    -> lower control eligibility/data readiness/completion gates
    -> retain successful-builder-transaction resource first-seen order and validate exact requirements
    -> lower one shared frontier/session/routing/resource/nested/planner-limit transition plan
    -> lower graph-input/node-publication output source projections
    -> attach recovery availability transfer to the shared transition/materialization/publication plans
    -> install compiled family and freeze builders atomically
```

任一步失败都不缓存 partial compiled graph，也不冻结部分 parent/child family。

Builder local normalization 不进入上述 pipeline：它先由第 3.1 节 transaction boundary 成功提交完整 `_GraphBuilderState`，compile 才读取 snapshot。Local builder failure 没有新 snapshot；compile semantic failure 保留原 committed builder state 供调用方观察错误，但不安装 cache/freeze。Compiler 不补登记 resource、修复 partial builder mutation 或读取 failed candidate。

Effective `ExecutionLimits` 是 invocation input，不写入 compiled family 或 cache identity。但 shared transition plan 必须保留对 existing planner/selector 的唯一调用点，让 runtime 与 recovery 在每次 invocation 消费同一 effective limits。

### 6.4 Compiler 证明边界

Compiler 证明的是：

- source candidate/producer 在 consumer 可能 activation 前存在合法 guaranteed-before path；
- consumer 只有在全部 required sources confirmed publication 后才可 materialize/call；
- data-driven target 只能被包含其 required producer 的某个 settled frontier 触发，不需要也不允许 runtime 重扫全部历史 ledger；
- control route、join 或 loop 不会绕过 data readiness gate；current State 中已有的 settlement contribution 与 admitted concrete skip 使用唯一 route，resume 后重新成为 Pending 的 conditional callable 与其他尚未执行 conditional callable 的 canonical success projection 才产生 declared route branch set；
- nested success 只产生 parent `ContinueGraphRouting`，nested node 不会进入 conditional-source plan；
- graph terminal path 只能在每个 declared output binding 可从 matching `ScopeRunCoordinate` 的 graph-input frame 或 exact scoped activation 的 node-publication frame 投影时成功完成；
- shared `FrontierTransitionPlan` 对 runtime concrete execution 与 recovery abstract execution 使用同一 frontier quiescence、planner limit coordinate、parallel-task selector、resource waiter、nested barrier、routing/join 和 completion lowering；
- `state.superstep >= effective_limits.max_supersteps` 只在 existing `plan_tasks()` coordinate 终止该 branch；`max_parallel_tasks` 与 resource admission 只允许 runtime 真能启动的 canonical task path，recovery 不枚举 completion permutations；
- typed failure/interrupt 在全部 current-frontier callable input admission 后不产生 publication/control contribution，并由 shared quiescence invariant 证明最终形成 AWAITING_RESUME，不展开 sibling outcomes；
- parked nested child 不阻断其他 runnable child，parent ordinary sibling 仍遵守 child barrier。

Compiler 证明 shared plan 与 canonical topology/control/limits owner 的调用坐标一致，并生成 closed success-route set；它不在 compile 时猜测 invocation 的 concrete State、effective limits、resume action 或 callable output。Invocation recovery evaluator 才以 exact State/snapshot/effective limits 为 seed 做 availability proof：State-owned settled contribution 与 concrete skip route 只走唯一 branch，resume 为 Pending 的 callable 和其他尚未执行 callable 走 canonical success projection，conditional success 才枚举 declared routes；success publication 只在 simulated admission/ack 后可用，failure/interrupt 使用上述 awaiting invariant，limit branch 在 exact planner coordinate 终止。Malformed concrete output、user exception 和其他无法预知的 runtime outcome 仍由现有 session/admission gate 处理。

### 6.5 Determinism

Compiled plan 以 canonical structural equality、稳定排序和稳定错误为验收目标，不承诺未定义的 byte-for-byte representation。Port mappings、edge collections 与不改变 resource first-seen sequence 的 successfully committed node declarations 不使用 insertion order、对象地址或 callable repr 决定 compiled equality/error。唯一有意的 builder-order semantics 是第 3.7 节 successful-node resource first-seen order：它是 canonical definition 的一部分，改变它就是改变图语义，不纳入 insertion-order independence。

Recovery worklist 明确分离 traversal ordering 与 semantic dedup：

1. `RecoveryTraversalKey` 是从 transfer state 投影的 canonical sort-only value，依次包含 effective limits、`ScopeRunCoordinate`/superstep/frontier control coordinates、live/resource positions、sorted availability coordinates、sorted child disposition coordinates、sorted admitted-action coordinates 与 invocation-new-child identities。Route 已存在于 exact State frontier control coordinate，不复制为第二个 future-outcome field。所有元素来自 canonical declaration/State/scalar identity，不读取 concrete frame value、user value hash/repr 或对象地址；scope path 与 graph run ID 始终作为一个 nominal coordinate 投影，不能只排序 path。
2. Worklist 使用该 key 与唯一 insertion sequence 的 bounded priority queue；successors 按 shared transition declaration ordinal 生成。Key 只决定 analysis visit order，不替 runtime 选择 canonical task path；两个 states 即使 key collision，也分别保留。
3. Seen/dedup 直接使用完整 frozen `RecoveryTransferState` structural equality/hash；control、availability、child、action 任一 semantic field 不同就不能合并。不存在另一个 hand-written dedup tuple，也不得把 `RecoveryTraversalKey` 当 seen key。
4. 因此同一 authoritative State/frontier 下，“A confirmed publication coordinate 存在/缺失”、child Missing/Active/Completed/Aborted 不同、child input/boundary frame coordinate 存在/缺失或 admitted resume/skip fact 不同，都会形成独立 transfer states；同一 nested path 上 C1/C2 两个 deterministic child run 的 graph-input/boundary coordinates 也始终不相等。只有所有 facts 和 scoped-run identities 完全相等才合并。State-owned settled route 与 concrete skip route 只产生一个 branch；resume 为 Pending 的 callable 与其他尚未执行 callable 只展开 compiled canonical success projection，conditional success routes 按 declaration identity 排列，不能因 set/dict iteration 改变 proof/error。

`max_supersteps` 只约束路径深度，`max_parallel_tasks` 只约束 concrete live width；二者不能约束同层 route 组合数，因此不得再以“状态空间最终有限”替代 CPU/内存安全证明。Evaluator 采用三重边界：ordinary live completion 只走 shared selector 给出的 canonical order；全部 callable input 已在 shared prepare/claim 前一次性 materialize 后，typed failure/interrupt 只能使该 frontier 最终进入 awaiting boundary 且不会再触发下一 frontier 的 value materialization，因此不枚举其 completion permutation；多个 child 分别递归证明，只建立一个全 completed continuation plan 与线性 single-abort variants，不做 Cartesian product。仍可能由多个 independent conditional success routes 形成的宽度，统一受每次 preflight **4096 个 admitted transfer/boundary states** 的固定安全预算约束；超出时在任何 fence/resume/claim/child-start/resource/node mutation 前抛出现有 `ExecutionLimitError`。Exact `superstep`、scoped-run identity、availability、child 与 action facts 继续保留在 equality 中，绝不通过删字段伪造收敛。

## 7. Data/control topology、entry 与 completion

### 7.1 唯一合成规则

- data binding 唯一拥有 named source 和 required readiness。
- direct/conditional/join/START/END 唯一拥有纯 control eligibility/order。
- activation 必须同时满足 control eligibility（若存在）与全部 data readiness。
- 初始 frontier 候选只来自 compiled automatic/explicit entries；zero-input node 不得因历史扫描在后续 superstep 重复成为 entry。
- 没有显式 incoming control 的 node 采用 frontier-local data-driven eligibility：只有当前 authoritative settled `GraphRunState.frontier` 包含至少一个 required producer，且该 producer 的 success publication 已 confirmed 时才发起一次 readiness 检查；全部 required sources 在该时点 confirmed 才生成 candidate。
- 有显式 incoming control 的 node 只有在 direct/selected conditional/completed join/START gate 到达后才有资格 activation；data 尚未 ready 时不得进入 target frontier，更不能 claim。
- data/control 在同一 resolution 选中同一 target 时按 `StableActivation` 合并为一个 candidate；同一 authoritative State transition 最多发出一个该 coordinate，后续是否仍可执行由 successor State 的 frontier/status 和现有 reducer 校验，不建立第二份 activation ledger。

同一 `(source_node, target_node)` 已有 data binding 时，重复普通 direct `add_edge(source, target)` 是确定的 `GraphValidationError`，不 coalesce，也不 lowering 为第二份 edge truth。Conditional/join 可以与 data source 涉及同一 node pair，因为它们表达独立 eligibility；compiled plan 仍只有一个 publication 和一个 activation。

### 7.2 Entry

- 没有 incoming node-output dependency、也没有 incoming control declaration 的 node 是 automatic entry；其 inputs 只能来自 graph boundary 或为空。
- 显式 `Graph.START -> node` 只用于声明纯 control root。
- automatic entry 又被显式 START 指向是 duplicate entry error。
- 有其他 incoming control 的 graph-input-only node 不是 automatic entry，必须等待对应 control gate。
- graph 至少有一个 automatic 或 explicit entry；否则 compile 拒绝。
- `StartGraphRun` 的 exact successor 确认后，successor `GraphRunState.frontier` 直接成为初始 node IDs 的 activation authority；Start commit 失败不改变 State，也不安装任何 run-context activation fact。

### 7.3 Frontier-local 一次性 activation

每个 authoritative settled `GraphRunState.frontier` 只按以下顺序 resolution。`SettledFrontierCoordinate` 由当前 State 对应的 exact `ScopeRunCoordinate`、superstep、frontier 和 revision 临时派生，不写入 continuation，也不作为历史 receipt 保存：

```text
derive ScopeRunCoordinate from exact GraphRunState + admitted family scope
    -> derive SettledFrontierCoordinate(scope_run, superstep, frontier, revision)
    -> collect settled node outcomes/routing contributions in this current frontier
    -> derive control candidates from this frontier routing contributions
    -> look up data targets only from compiled triggers of those current-frontier producers
    -> for each data target, require all compiled sources confirmed; otherwise it is not a candidate
    -> for each control target, require all compiled sources confirmed; otherwise choose scoped abort
    -> merge origins and canonicalize candidates as StableActivation(scope_run, superstep + 1, node_id)
    -> consult the current State frontier/status and existing reducer rules; never rescan historical frontiers
    -> AdvanceGraphFrontier / CompleteGraphFrontier / AbortGraphRun
    -> after exact successor acknowledgement, successor State becomes the sole activation/resolution authority
```

上述 resolution 只接收 `frontier_status() == SETTLED` 的 exact State。Typed failure/interrupt settlement 不终止当前 session；只要 frontier 仍有 `PendingGraphNode`，status 仍是 EXECUTABLE，session 必须按 existing resource/nested barrier 继续交付 started completion、runnable ordinary sibling、resource waiter 与 precomputed nested completion。Pending 全部消失后若存在 Failed/Interrupted settlement，prepare 返回 AWAITING_RESUME 而不是进入 routing resolution。Runtime 与 recovery 共用这一 quiescence rule。

Family driver 在一个 invocation 的确定性控制流中只消费一次当前 settled State；该局部控制流约束不写入 State/run context，也不承诺拒绝调用方再次提交同值 State。再次提交同值 pair 仍是 caller-owned branch，不能借此推导旧快照的 latestness。

一个 current-frontier producer 的 success 可以来自上一 invocation 中已确认的 sibling；resume 后仍按当前 frontier 的 canonical node set 收集，不按“本次新返回的值”收集。这保证 partial success + failure/resume 时可以使用早已确认的 sibling publication，同时不会让更早 superstep 的任意历史值重新触发 target。

多个 producers 在同一 frontier 触发同一 target 时，先按 target coordinate 去重，再做一次 AND readiness。不同 frontier 产生 required sources 时，只有包含最后一个使全部 sources ready 的 producer frontier 能生成 target。`AdvanceGraphFrontier` 的 acknowledged successor 已将 State 推进到下一 frontier，因此同一 accepted branch 不会再次解析旧 frontier；commit mismatch/exception 不安装 successor，也不产生新的 activation authority。

Continuation 不保存 activation/resolution history。调用方重放一个更早但结构自洽的 State/continuation pair 仍属于第 3.5 节的 caller-owned branch 边界；runtime 不伪称可以判定其 latestness，也不以缺失的 history 将合法 state-only recovery 判为 malformed。

### 7.4 Completion、END 与 dynamic abort

Graph 成功 completion 同时要求：

1. 当前 authoritative State frontier 没有 Pending/active activation；
2. routing 没有 next nodes；
3. 没有 unresolved join progress；
4. `set_outputs()` 声明的每个 compiled source binding 都已 available：`GraphInputPort` 从 matching current `ScopeRunCoordinate` 的 admitted graph-input frame 读取，`NodeOutputPort` 从 matching confirmed scoped activation publication frame 读取；
5. nested children 全部 terminal 且已投影 parent settlement。

`Graph.END` 仍可作为 conditional/join/direct control terminal target，但不是 graph output declaration，也不代替 `set_outputs()`。最后一个 node 没有 outgoing control 时可自然终止；不要求重复写 `last -> END`。Compiler 必须验证每条静态 successful terminal path 不会在缺少 required graph output 时完成，runtime completion gate 做最终 confirmed-value 检查。

无 next activation 时，runtime 先检查 unresolved join/nested child，再按 compiled `GraphOutputBindings` 检查 graph-output boundary。若编译时合法且 recovery preflight 已通过的路径因已确认 failure/skip 等 runtime dynamic outcome 导致 control-selected target 或 completion boundary 缺少 required graph-input/publication value，当前 graph scope 必须：

1. 不生成 target `AdvanceGraphFrontier`、claim、resource acquisition 或 node call；
2. 根据 stable coordinates 构造 state-owned `GraphAbortReason`，对当前 quiescent root/child state 投影现有 `AbortGraphRun`；
3. 经同一 `Graph.Transition` callback 确认 exact aborted successor；
4. 将 exact aborted successor 投影到 root binding 和 child scope snapshot，然后返回可观察 `Graph.AbortedResult`；不写入 activation/resolution history。

Root scope 直接返回 `Graph.AbortedResult`；child scope 经现有 `AbortedChild -> TaskFailure` 投影为 parent nested failure，并保留当前 complete/recovered lineage 的 root continuation。Recovery availability proof 在 invocation 的任何 State mutation 前发现历史 graph-input/publication/child snapshot 缺失时返回 `GraphValueUnavailableError`；只有不可由 preflight 预测的已确认 dynamic outcome 才进入上述 abort path。

纯 data-driven consumer 因某个 source skip/failure 没有 publication 时根本不成为 candidate；若没有 control gate 要求它执行，且所有 join/nested/completion/output 条件都已满足，graph 可以正常 completion。若其 output 属于 required graph boundary，则按上述规则 abort，不得无限重试该 consumer。

### 7.5 Conditional、join、skip 与 control loop

- 多个 required data sources 永远是 AND readiness；control OR/AND 继续分别由 direct/conditional 与 explicit join 表达。
- conditional route 先通过 acknowledged State 形成 eligibility，再由 run context 检查 data readiness。
- ordinary callable success 可以贡献 declared routing variant；nested success 永远贡献 `ContinueGraphRouting`，因此 nested node 作为 conditional source 在 compile 时拒绝。Nested success 仍可触发 direct edge 或作为 join arrival。
- 需要按 child output 路由时，child output 先 materialize 给显式 ordinary router node，由该 router 唯一产生 parent conditional route。
- success 即使声明/返回空 output frame，仍在 exact settlement acknowledgement 后产生该 `StableActivation` 的完整 confirmed publication；它证明 activation success，不伪造 output port。
- skip 保留现有 routing contribution，但不 publication，也不作为 data trigger。纯 data consumer 不会成为 candidate；若 skip routing 通过 control 选中一个依赖缺失 output 的 target，按第 7.4 节 abort 当前 scope。
- ordinary data graph 必须 acyclic。
- explicit control loop 可以进入下一 `superstep`；stable activation coordinate 因 superstep 不同而不同，并在每次下一 frontier planning 时受本 invocation effective `max_supersteps` 约束。
- 当 concrete loop branch 的 State 在 existing planner coordinate 满足 `state.superstep >= max_supersteps` 时，统一抛出 `Graph.ExecutionLimitError`；不提前 abort State，不投影 Result，也不为 recovery 建立另一 loop counter。
- 普通 `Graph.node_output()` 只引用 compiled forward-dataflow path 为该 consumer 选定的 guaranteed-before activation（通常位于更早 superstep），不表示 control-loop backedge 的 previous-iteration feedback。Delayed/feedback value port 需要独立 future requirement，本期不猜测。
- 对会重复 activation 的 control loop，materialization plan 必须从 current control traversal 推导 exact producer `StableActivation`，不能按 node ID 读取“最近一个”缓存值；只有新 `superstep`/parent coordinate 能生成下一轮 activation，无法静态形成唯一坐标的 loop topology 在 compile 时拒绝。

## 8. Runtime value owner、publication 与 State

### 8.1 Run-scoped owner

唯一 transient value owner 是每次 `Graph.run()` invocation 创建的 execution-private `GraphRunContext`。它经 facade、`_drive()`、frontier session 和 nested driver 显式传递；不挂在 `Graph`、`CompiledGraph`、executor、State 或 module global 上。

当前 `_drive()` 会为不同 frontier 创建并关闭多个 `GraphExecutionSession`，所以 session 不是 run lifetime owner。Session 只负责 node invocation、output admission 和 publication candidate；run context 跨 session 保存 confirmed publications、admitted input frames、child snapshots 和当前 invocation 的未确认 candidate，但不保存 activation/resolution authority。

### 8.2 Ledger shape

Run context 只使用一个 execution-owned immutable `ScopedFrameIndex[GraphValueT]` 作为 concrete frame truth，不把 value 与 State-owned control facts 合并，也不为四种 frame 各建可漂移的 lookup。该 nominal aggregate 由四个按 coordinate canonical 排序的强类型 record tuple 构成；不是 bare mapping，也不是包含 optional frame/ack 字段的宽 ledger：

```text
ScopedFrameIndex:
    graph_inputs: tuple[
        AdmittedGraphInput(
            GraphInputAvailabilityCoordinate(
                ScopeRunCoordinate(scope, graph_run_id),
                complete_graph_input_frame_descriptor_identity,
            ),
            immutable complete GraphInputFrame,
        ),
        ...,
    ]
    publications: tuple[
        ConfirmedPublication(
            PublicationAvailabilityCoordinate(
                StableActivation(
                    ScopeRunCoordinate(scope, graph_run_id),
                    superstep,
                    node_id,
                ),
                complete_node_output_frame_descriptor_identity,
            ),
            immutable complete NodeOutputFrame,
            acknowledged successor revision,
            acknowledged execution token,
        ),
        ...,
    ]
    resume_inputs: tuple[
        AdmittedResumeInput(
            ResumeInputAvailabilityCoordinate(
                StableActivation(scope_run, superstep, node_id),
                complete_node_input_frame_descriptor_identity,
            ),
            immutable complete NodeInputFrame,
        ),
        ...,
    ]
    child_boundaries: tuple[
        ConfirmedChildBoundary(
            ChildBoundaryAvailabilityCoordinate(
                child_scope_run,
                complete_graph_output_frame_descriptor_identity,
            ),
            immutable complete GraphOutputView,
        ),
        ...,
    ]
```

每个 coordinate 在其 typed segment 中最多出现一次。`ScopedFrameIndex.lookup()` 直接声明四个 internal overload：graph-input coordinate 返回 `AdmittedGraphInput[GraphValueT]`，publication coordinate 返回 `ConfirmedPublication[GraphValueT]`，resume-input coordinate 返回 `AdmittedResumeInput[GraphValueT]`，child-boundary coordinate 返回 `ConfirmedChildBoundary[GraphValueT]`；implementation signature 只能是这四类 coordinate union 到四类 record union，并通过 nominal variant narrowing 实现。它绝不把 records 擦除为 `dict[coordinate, object]`/`Any`，也不在第二个 publication/input map 重复保存 frame。每次 acknowledged addition 都先构造 detached replacement index，再替换 invocation-local run-context field；continuation snapshot 直接保留该 immutable value。Graph-output projection 只从同一 records 构造 view，不存第二份 frame truth。

四类 records 与 `ScopedFrameIndex` 都显式 unhashable、non-orderable，不参加 recovery/state/snapshot structural equality；它们的 diagnostic repr 只允许 coordinate/descriptor/acknowledgement scalar，禁止调用 concrete frame/value 的 equality、hash、ordering 或 repr。Duplicate admission 只按对应 typed segment 的 coordinate 检测：coordinate 已存在就 fail closed，不通过比较 frame 来区分“same”与“different”。Recovery seed 只从 index 投影 coordinate tuples，随后不再持有 record/index；这保证第 3.5/6.5 节 concrete-value exclusion 在实际容器层也成立。

其中 publication coordinate 的 exact shape 为：

```text
PublicationAvailabilityCoordinate(
    StableActivation(
        ScopeRunCoordinate(scope, graph_run_id),
        superstep,
        node_id,
    ),
    complete_node_output_frame_descriptor_identity,
)
```

Publication key 不包含 `output_name`，避免与完整 frame 重复；materialization plan 再从 frame 选择 declared output port。该 key 的 generation 是 logical `ScopeRunCoordinate + superstep`，不以 execution attempt generation 分区。

- `StableActivation` 只标识 publication/materialization 的 logical coordinate，不替代 State 的 activation authority；selective resume 后已成功 sibling publication 继续有效。
- existing execution token/generation 只验证 completion 属于当前 lease，并作为 acknowledgement evidence 保存；后续 value lookup 不按 attempt token/generation 分区。
- loop 由新 superstep 区分；nested child 的 `ScopeRunCoordinate` 使用 exact deterministic child run ID。同一 parent scope path 在不同 superstep 再次启动 child 时，child C1/C2 的 publication、graph-input 与 boundary coordinates 全部不同。
- 同一 scoped stable activation 最多有一个 confirmed publication；duplicate/different publication fail closed。空 output frame 仍作为 complete publication 保存。
- `GraphRunState.frontier/status/superstep/revision` 是 activation/resolution 的唯一 authority。当前 invocation 中尚未被 acknowledgement 的 candidate 只存在于 invocation-local driver context/call stack，commit mismatch/exception/cancellation 后立即丢弃，不进入 `ScopedFrameIndex` publication segment、continuation 或 State 镜像。Publication 中的 acknowledged revision 仅是 value acknowledgement evidence，不是 frontier history。

### 8.3 Publication sequence

```text
materialize confirmed inputs
    -> claim/resource admission
    -> callable or child invocation
    -> admit complete output frame
    -> create immutable publication candidate + SettleGraphNode
    -> caller confirms exact reducer successor
    -> run context installs frame under stable activation
    -> acknowledged routing/data gates may activate downstream
```

Commit mismatch、commit exception、session cancellation、fence 或 stale execution token 都丢弃未确认 candidate。Run context 不自行调用 reducer，不预测 successor。

### 8.4 Continuation snapshot

`Graph.Continuation[GraphValueT]` 是 final、seal-constructed `_GraphContinuation[GraphValueT]` 的只读 public surface。每个 `CompletedResult`、`AbortedResult` 与 `AwaitingResumeResult` 都携带一个非 optional continuation；wrapper 的 private payload 是且只能是以下 closed union：

```text
_CompleteContinuationSnapshot[GraphValueT]
    | _RecoveredContinuationSnapshot[GraphValueT]
```

两个 snapshot variants 共享同一个 immutable envelope：

- exact execution-private `_CompiledFamilyIdentity`、definition ID/version 与 root run ID；
- 与 Result `state` 共享的 exact immutable `_RootStateBinding` value；
- snapshot variant 自己真实持有的 canonical `ScopeRunCoordinate -> child State binding`、唯一 immutable `ScopedFrameIndex` 与 child snapshots；
- module-private `_ContinuationSeal`，不提供 public constructor、copy/pickle、codec、Store 或 serialization contract。

`_CompleteContinuationSnapshot` 只由带 concrete `Graph.values(...)` 的 new run 建立，并保存该 logical graph family 从 root admission 起产生的全部 admitted root/child graph-input frames、全部 confirmed publications、全部 confirmed child boundary frames，以及每个 current nested activation 的 child State/frame snapshot。每个 concrete frame 都按第 3.5 节 exact runtime + descriptor coordinate 保存；同一路径 repeated nested activation 的 C1/C2 frames 因 `ScopeRunCoordinate` 不同而同时保留，旧 terminal child frame 不回收、不覆盖新 run。Complete lineage 后续始终产生 complete snapshot；缺少任一应有 frame/child snapshot，或者含有 extra/inconsistent/coordinate-colliding entry，都是 malformed continuation。

`_RecoveredContinuationSnapshot` 只由合法 state-only invocation 建立。它保存从 authoritative State/codec 能实际 admission 的 frame，以及该 recovered lineage 从本次 invocation 起新产生并已确认的 graph-input/publication/child snapshots；所有新增 frame 同样使用 exact `ScopeRunCoordinate`，不能因 recovered variant 缺历史 frame 而退化为 scope/definition key。它可以合法缺少 state-only admission 之前的 root input、historical publication 或 historical child frame。该缺失是 recovered variant 的领域语义，不叫 partial，不允许填充 sentinel，也不能通过后续偶然重新获得部分值而升级为 complete variant。

Continuation admission 先验证 exact family token、definition/root run、结构相等的 root-state binding、snapshot variant invariant、已有 child scope State、frame descriptor 与 canonical index。Facade 从 root State 构造 exact root `ScopeRunCoordinate`，并对每个 child snapshot 从完整 scope path、child State run ID 和 parent activation 重新构造/验证同一 coordinate；任何 frame key 的 scope/run/descriptor 与 owner State 不一致都作为 malformed 拒绝，绝不按 path 或 `ChildControlStateCoordinate` 隐式补 run ID。Complete snapshot 的 required frame 缺失在 admission 阶段作为 `SnapshotMismatchError` 拒绝；recovered snapshot 的合法历史缺失不在此阶段误判，而交给第 8.6 节基于 `CompiledGraph.transition` 的 recovery availability proof 判断本次 invocation 是否可运行。两个 variants 的 extra、foreign、scope/state 不一致、same-coordinate different frame 或 malformed entry 都在任何 active-state fence、resume、claim 或 child start 前拒绝。

`CompletedResult.outputs` 不是 continuation 内的第二份存储；它只按 compiled `GraphOutputBindings` 从 result 对应 root/child `ScopeRunCoordinate` 已持有的 admitted graph-input frame 或 confirmed node publication 投影，child completion 另形成 exact `ChildBoundaryAvailabilityCoordinate`。`AbortedResult` 与 `AwaitingResumeResult` 不暴露 `outputs`。所有 Result 的 `state` 都是同一 `_RootStateBinding` 的投影。

Admission 不记录“已使用”标志，也不承诺检测一个结构自洽的旧 pair 是否已被取代。完全相等的 immutable State copy 合法；相同 revision 但字段不相等的 State 拒绝。调用方/authoritative commit owner 必须保证同一 logical run 使用最新 pair 且没有并发 driver；本需求不增加 multi-worker arbitration 或 hidden mutable linear-capability flag。有 matching continuation 的 child-only progress 必须返回同 lineage variant 的新 continuation，即使 root State value/revision 未变。

### 8.5 State 决策：保持当前模型，不做适配

State 是否需要修改在本文中直接裁决，不留到编码阶段。对本需求新增的每一类 correctness identity，现有模型的充分性证明如下：

| 必须表达的事实 | 现有唯一 State 坐标/transition | 充分性结论 |
| --- | --- | --- |
| graph family 与 scoped-run identity | `run_id`、`definition_id/version`、child `parent` | execution 以完整 scope path + exact State `run_id` 构造 `ScopeRunCoordinate`；child run ID 由 parent `(run_id, superstep, node_id)` deterministic 派生，同一路径重复 child activation 不碰撞 |
| stable node activation | current `superstep` + canonical `frontier.nodes[].node_id` | 与 `ScopeRunCoordinate` 组成唯一 `StableActivation`；`AdvanceGraphFrontier` 每轮递增 superstep，不需要 value epoch |
| current settlement 与 routing | `Pending/Succeeded/Failed/Interrupted/SkippedGraphNode` closed settlement + routing contribution | 当前 frontier 的 executable/awaiting/settled disposition 可完全派生；不需要 execution 侧 receipt/history |
| transition acknowledgement 与 stale State fencing | `revision` + 每个 command 的 `expected_revision` + exact reducer successor callback | State transition 的 accepted branch 已唯一；publication/override candidate 只在 exact successor acknowledgement 后安装 |
| execution attempt fencing | `execution_sequence` + active `GraphExecutionToken(generation, attempt_id)` | claim 产生新 generation；settle/fence 必须持有 exact token。Settled success 属于 stable activation，不应永久携带 attempt generation |
| interrupt/recovery identity | interrupt `(run_id, superstep, node_id, execution_generation)`、resume settlement variant、`GraphResumeInputCodec` | stale interrupt resume 可拒绝；`UseStepRequestInput | OverrideGraphNodeInput(bytes)` 已完整表达 State-owned resume control fact |
| join、resource 与 terminal disposition | `join_progress`、`resources`、`status`、`abort` | unresolved join、active resource lease、COMPLETED/ABORTED 均已有 canonical State 表达和 reducer validation |
| nested child control position | 每个 child 自身的 `GraphRunState` + `parent` coordinate | child RUNNING/AWAITING/COMPLETED/ABORTED 由 child State 表达；root/child snapshot pairing 由 sealed continuation 校验，不把 child 或 value 镜像进 root State |

由此得到唯一模型：

1. `GraphRunState.frontier/status/superstep/revision` 是 activation/resolution authority；execution 从 admitted family scope 与 exact State `run_id` 临时派生 `ScopeRunCoordinate`，再构造 `StableActivation(scope_run, superstep, node_id)`。
2. execution attempt 只在 active lease 内有语义；success publication 在 exact `SettleGraphNode` successor acknowledgement 后绑定 stable activation，因此 `SucceededGraphNode` 不需要 generation field。
3. complete/recovered continuation variant、graph-input/publication frame、child snapshot pairing 和基于 `CompiledGraph.transition` 的 recovery availability proof 都是 execution 的 transient value/recovery proof，不是新的 durable control fact。
4. terminal `AbortedChild` 没有 restart generation；本方案已在 public resume preflight 禁止同 identity restart，所以不需要为一个明确不支持的动作扩张 State。
5. state-only invocation 缺 transient value 或 child snapshot 时由 whole-invocation preflight fail closed；不以增加 State value 字段来扩大恢复承诺。
6. `ScopeRunCoordinate`、frame availability 与 child boundary coordinate 都由 existing root/child `GraphRunState.run_id`、child `parent` 和 execution-owned scope path 组合得到，是 transient index identity，不是新增 durable State fact；修复 repeated child frame collision 不要求 State 字段、adapter 或 persistence。

因此 `state/graph_state/**` 在本实施账本中无条件 `KEEP`：不增加 value epoch、generation field、binding variant、child mirror、`Graph.Values` import 或 execution dependency，依赖方向保持 `execution -> state`。Production 实施若试图修改 State，即偏离本文，而不是可选的“更合适实现”；必须在代码进入前纠正实现设计。State 决策已经闭合，不存在实施期 behavior proof 后再选 State 方案的步骤。

### 8.6 Resume/restart 边界

- 同一 invocation 内的 multiple frontier sessions 共享 run context。
- 后续 invocation 若需要既有 graph input/publications/child states，只能通过 matching continuation 取得；不从 State、Graph、executor 或 process-local cache 猜测 concrete value。
- failed/interrupted node override 继续走 existing execution-owned graph-local codec，但 codec 的 generic payload 一次性迁移为 `Graph.Values[GraphValueT]`；State 只见 opaque bytes，不引入通用 output codec。
- limits-first normalization 通过后，invocation admission 先完成 family/root-state/snapshot 校验，再解析 resume scope 与 target compiled graph，验证 graph/state codec ID/version，并对新 override 做 encode/decode/exact node-input admission，或对 `UseStepRequestInput`/State-owned opaque override 做 compiled re-materialization。Aborted-child restart prohibition 也在该阶段校验；任一失败都先于 fence/resume/claim/child start。
- 对 state-only invocation 以及所有 recovered-continuation invocation，facade 随后在 `CompiledGraph.transition` 上执行 recovery availability proof。三个 `run()` overload 在进入该步骤前都已由 existing `ExecutionLimits` owner 构造并校验本 invocation 唯一的 effective limits；同一个 immutable value 进入 concrete driver、recovery seed 和全部 nested scopes，不存在 recovery-specific、child-specific 或 compiled limits。Evaluator 不自行解释 edges；它先从 current authoritative root/child State、admitted scope path 和 exact parent activation 构造/校验每个 `ScopeRunCoordinate`，再把 snapshot 中真实存在且完整 admission 的 frame/child snapshot 投影为带同一 runtime coordinate 的 availability，以及把已 admission 但尚未提交的 resume/skip facts 放入 `RecoveryTransferState`。Concrete frames 仍留在 run context/continuation；shared `FrontierTransitionPlan` 推进到该 branch 首个 public Result disposition 或 exact planner `ExecutionLimitError` boundary。
- 同一 State/frontier 可以因 recovered snapshot availability 不同形成多个合法 seed：confirmed publication coordinate 存在的 seed 与历史 publication 缺失的 seed、child snapshot/boundary coordinate 不同的 seeds 都使用完整 semantic equality 独立推进。同一路径 repeated nested activation 派生的 C1/C2 `ScopeRunCoordinate` 不同，其 graph-input/publication/boundary availability 不能合并，即使 definition、descriptor 与 traversal prefix 全部相同。Seen set 只合并 structural-equal `RecoveryTransferState`；`RecoveryTraversalKey` 仅排序，即使 collision 也不能吞掉其中一个 seed/branch，worklist 不触碰 frame concrete value 的 ordering/hash/repr。
- Active State 先按 existing `FenceGraphExecution` validation/reducer semantics 模拟 exact quiescent successor，清除 lease/resource snapshot 但不增加 availability；若 invocation 带 resume/skip，再按 existing resume validation/reducer semantics 模拟 exact successor，随后才进入 prepare/planner。Concrete driver 保持同一 `fence -> optional resume -> prepare -> plan_tasks()` 顺序：valid limits 已在该 State coordinate 耗尽时，已确认的 fence/resume transition 保留，再由 planner 抛出 `Graph.ExecutionLimitError`；quiescent seed 不伪造 fence。Current State 中已有的 settlement 与 concrete skip settlement 只使用各自 State-owned routing contribution；resume 后重新成为 Pending 的 conditional callable 与其他尚未执行的 conditional callable 只在 canonical success projection 中枚举 declared routes。无 route prediction，也不因不可达 sibling route 缺值拒绝。
- Current/future EXECUTABLE frontier 在与 runtime `plan_tasks()` 完全相同的 scope/State/superstep coordinate 先检查 `effective_limits.max_supersteps`。命中时该 abstract branch 立即成为 `EXECUTION_LIMIT` terminal boundary，不再检查后续 availability，也不构造 Result、abort 或 continuation；未命中时才按 `effective_limits.max_parallel_tasks - live_count`、resource first-seen acquisition/FIFO waiter 与 existing nested barrier 处理完整 Pending set。Evaluator 只覆盖 selector 真能启动的 canonical all-success task path 及 conditional success routes，不枚举 acknowledgement/completion permutations，也不构造被 parallel slot、resource 或 child barrier 排除的 completion。Typed failure/interrupt 由全部 callable input 已 admission 且无 publication/control contribution 的 quiescence invariant 证明只能形成 `AwaitingResumeResult` boundary。
- 每个 simulated success 在该 branch 的 output admission/settlement ack 后贡献 provisional publication availability；实际 invocation 仍只在 exact acknowledgement 后把 concrete frame 安装进 run context/returned continuation。Failure/interrupt invariant 不为该 node 伪造 publication，也不要求展开 sibling outcome 状态；同 frontier 已存在的 confirmed publication 仍保留在 seed availability 中。
- 上述 proof 是 whole-invocation gate，不是“当前 Pending node 能否执行”的局部检查。任一 reachable branch 在其首个 completed/aborted/awaiting-resume Result 或 exact limit boundary 前需要缺失的 historical graph input、publication 或 child snapshot 时，立即返回 `GraphValueUnavailableError`；此前不得提交 fence、`ResumeGraphNodes`、claim、child `StartGraphRun`，也不得 acquire resource 或调用 node。Limit boundary 已终止的 branch 不检查其后的 value requirement；某个 future route 可能触达 limit，也不能提前拒绝或替代其他可在 limit 前 exit 的 branch。
- invocation admission 时已经存在的 nested Pending activation 必须有 matching parent activation、derived child `ScopeRunCoordinate` 和 child snapshot。Complete snapshot 缺失该 snapshot 是 malformed；state-only/recovered lineage 缺失时返回 `GraphValueUnavailableError`，不得把它解释成 `MissingChild` 后重建。只有本 invocation 内由 acknowledged parent transition 新产生的 nested activation 才可投影带 expected child scope/run 的 `MissingChild`、start deterministic child，并立即把 acknowledged child State 与 `GraphInputAvailabilityCoordinate(child_scope_run, descriptor)` frame 写入当前 lineage。
- 新 override 的 admitted node-local frame 只作为 execution-local candidate，并以 target `StableActivation(scope_run, superstep, node_id)` 形成 `ResumeInputAvailabilityCoordinate`；`ResumeGraphNodes` exact（按现有 State value equality）successor 经 commit callback 确认后，才安装到当前 run context/continuation。commit mismatch/exception/cancellation 时丢弃 candidate，不返回包含该 frame 的 continuation。Engine 不从 shared `StepRequest.node_input` 重建该 frame。
- recovered State 已携带的 opaque override 在 state/codec admission 后可直接 materialize，不需要伪造新的 `ResumeGraphNodes` acknowledgement；其 State transition 已由 authoritative State 提供。`UseStepRequestInput` 只表示使用当前 compiled materialization。
- whole-invocation proof 通过后，state-only recovery 才可执行 Pending zero-input、完整 State-owned opaque override、active fence recovery、settled empty-output completion，或直接投影 current root `ABORTED`/`AWAITING_RESUME` disposition。Concrete branch 若到达 `CompletedResult`、`AbortedResult` 或 `AwaitingResumeResult`，就建立并携带 recovered continuation；若先到达 exact planner limit boundary，则直接抛出 `Graph.ExecutionLimitError`，不返回 continuation、伪 Result 或 graph abort。后续 invocation 继续使用同一 proof，永不补写无法恢复的历史值。
- 无 continuation 的 current nested Pending/Active/Completed/Aborted disposition 一律不属于可执行的 child-only recovery；有 matching complete/recovered continuation 时，RUNNING/AWAITING_RESUME child 才可沿 structured scope 与 exact child run ID 继续同一 `ScopeRunCoordinate`，不能仅凭 path 选择任意历史 child frame。
- 新进程或缺少 continuation 时，不恢复 output；required value fail closed。
- restart/crash recovery、Store replay 和 output durability 不在本方案承诺内。

## 9. Nested graph freeze 与唯一 driving protocol

### 9.1 Atomic graph-family freeze

Parent compile 先递归收集 child definitions，拒绝 composition recursion、scope escape 和 boundary mismatch；全部 family compile 成功后，才原子安装 compiled definitions 并冻结所有涉及的 builders。失败不留下 partially frozen graph。

被冻结的 child：

- 不得再修改 topology、inputs、outputs、resources 或 resume codec；
- 可以独立作为另一个 root 运行，复用同一 immutable compiled definition；
- 可以被多个 parent definition 引用，run identity 仍由各 parent activation 决定。

### 9.2 Public facade 驱动 sequence

Public facade 必须消费现有 `WaitingForChildren`，不能继续直接报“不支持 nested”。`StartMissingChildren.children` 和 `WaitForActiveChildren.children` 已按 `ParentGraphActivation` canonical 排序，root family driver 必须消费完整 tuple，不能只处理第一个 child。Family driver 从 root invocation 接收同一个已校验 effective `ExecutionLimits`，并原样传给 root、每个 child 和更深 scope；不得为 child 重置、扩大、缩小或另行构造 limits。

进入 child driving 前必须先完成 continuation/resume admission 和第 8.6 节 whole-invocation recovery availability proof。Invocation admission 时已经处于 Pending 的 nested parent activation 必须在 matching continuation 中有 exact derived child `ScopeRunCoordinate`、child State 与 frame snapshot；state-only/recovered lineage 缺失时在任何 fence/start/claim 前返回 `GraphValueUnavailableError`，不能把“没有 snapshot”解释为从未启动过 child。`MissingChild` 只可由本 invocation 内 acknowledged parent transition 新创建的 nested activation 产生，并在 child start 前携带由该 activation 唯一派生的 expected scope/run identity。

唯一多 child algorithm 为：

```text
construct/validate one effective ExecutionLimits before compile/cache or mutation
    -> compile/family/root-state/continuation/resume admission
    -> whole-invocation recovery availability proof over `CompiledGraph.transition`/shared FrontierTransitionPlan with the same limits when lineage is recovered
    -> parent prepare with the same limits -> WaitingForChildren(canonical children)
    -> validate projections exactly cover all pending nested parent activations
    -> for every invocation-new MissingChild in canonical order:
         derive child_scope_run from full child scope + child_graph_run_id(parent activation)
         project deterministic child StartGraphRun
         commit exact child successor through scoped Graph.Transition
         validate successor.run_id == child_scope_run.graph_run_id and successor.parent == parent activation
         install acknowledged child State/input frame under the same child_scope_run in root continuation
         child successor frontier is its activation authority
    -> repeat canonical round-robin passes over non-terminal child scopes:
         advance each runnable child by one shared family-driver quantum with the same effective limits
         run that scope's plan_tasks() check against its own authoritative State/superstep before any task selection
         update its scoped State/publications after each exact commit; derive the next child frontier from that State
         park AwaitingResume child; continue every other runnable child
         retain CompletedChild/AbortedChild as terminal canonical projections
         propagate a child ExecutionLimitError directly out of the root family invocation
    -> if no runnable child remains and at least one child is parked:
         return AwaitingResumeResult(state=root binding, continuation=updated same-variant snapshot, scoped views)
    -> otherwise, when every child is terminal:
         pass the exact canonical projection vector back to existing parent prepare
         for each CompletedChild:
             project/admit child GraphOutputBindings from child input/publication frames
             construct parent TaskSuccess(output_frame, ContinueGraphRouting)
         for each AbortedChild:
             construct existing typed parent TaskFailure from the child abort
         settle parent nested tasks through the ordinary parent session
         publish only CompletedChild output after exact parent SettleGraphNode acknowledgement
```

一个 family-driver quantum 只调用根 scope 也使用的现有 prepare/claim/session/settlement/routing 原语，并传入本 invocation 的同一 effective limits：`ReadyToResolve` 提交一个 resolve command；`ExecutableFrontier` 先在该 scope 自己的 authoritative State/superstep 上调用同一 `plan_tasks()` limit check，再由同一 selector 按该 scope 的 live count、`max_parallel_tasks` 与 resource admission 形成 legal start set，随后提交 claim 并驱动该 child frontier session 到当前 quiescent disposition；更深层 `WaitingForChildren` 递归使用同一 algorithm。这是同一 family driver 的 scoped 调用，不是第二 runner。

Recovery evaluator 调用的也是上述 shared family-driver transition plan：parked child 只从 runnable set 移除，不阻断其他 child；precomputed `CompletedChild`/`AbortedChild` 继续作为 parent session completion；parent ordinary sibling 只有在 child barrier 全部 terminal 后才进入同一 session。每个 child 独立证明后只建立一个 all-completed plan、线性 single-abort variants 与直接 terminal boundaries，不做 Cartesian product。每个 scope 的 abstract transition 受同一 `max_supersteps` check、该 scope 的 `max_parallel_tasks` slots 与相同 resource selector 限制；ordinary typed failure/interrupt 使用 awaiting quiescence invariant，recovery 不复制 runtime sibling outcome/completion-order 状态，也不枚举 runtime 不可能的 task。

Canonical round-robin 排序键为 child `ScopeRunCoordinate` 加 exact parent `StableActivation`；完整 scope path 和 deterministic child run ID 均参与，不能只按 path 将 repeated activation 合并。一轮内每个 runnable child 最多消费一个 quantum，然后重新 canonicalize，避免第一个长 child 长期阻塞其他 child；quantum 内部仍只能选择该 scope 在 `max_parallel_tasks`/resource 约束下的 legal order。所有 child start/settle/resume/fence/resolve transition 都经过同一 commit callback，并携带 structured scope path；commit 可观察顺序与上述 canonical passes 一致。任一 child 在自己的 planner coordinate 命中 limit 时，原 `Graph.ExecutionLimitError` 直接终止整个 root `run()` invocation；它不是 `CompletedChild`/`AbortedChild`，不得转成 parent `TaskFailure`、`AbortGraphRun`、Result 或 continuation。

按现有 `prepare_superstep()` 的 child-barrier owner，与 nested tasks 同处一个 parent frontier 的 ordinary siblings 在全部 children 变为 terminal projection 前不 claim；不建立一条旁路让 ordinary session 与 child driver 竞争 parent settlement。Children 之间使用上述 round-robin 保证公平。这一 barrier/fairness 是唯一可观察语义，不由实现临时选择。

`CompletedChild` 的 parent routing 只能由 nested projection owner 构造为 `ContinueGraphRouting`；不得透传 child 内部 route、读取 child output 推断 route，或等待现有 routing validator 在运行时兜底。Compiler 已拒绝 nested conditional source；direct edge 与 join arrival 继续消费该 success。需要按 child output 分支时，必须在 parent 增加 ordinary router node。

RUNNING/AWAITING_RESUME child 保持原 deterministic child run，并只通过 structured scope-aware resume action 恢复其内部 failed/interrupted leaf。`AbortedChild` 投影并经 parent settlement acknowledgement 成为 parent nested `FailedGraphNode` 后，terminal child snapshot 必须保留；parent `resume_failed("child")`、`resume_failed_with("child", ...)` 或任何 retry/restart action 均在 preflight 返回 `SnapshotMismatchError`，只允许 existing `skip_failed("child", ...)` 或终止 parent。不得丢弃 snapshot、复用同一 child run ID 重建 child；真正 restart 必须另立 State/identity 需求。

一个 child 已 completed/aborted 而另一个 child 尚在运行或 awaiting resume 时，已终止 child 的 snapshot/boundary frame 以 exact child `ScopeRunCoordinate` 留在 root continuation，但尚不投影 parent settlement。Parent loop 后续在同一 path 启动新 child run 时，旧 coordinate/frame 继续保留，新 coordinate 独立安装；current parent activation 只能解析其 derived child run，不能选择 path 下“最近”或任意 terminal snapshot。Child-only progress 必须返回与 incoming lineage variant 相同的新 continuation，即使 root State revision 不变。Execution 不查询 Store。

### 9.3 Boundary values

- parent input materialization 在 derived child `ScopeRunCoordinate` 下形成唯一 `GraphInputAvailabilityCoordinate` 与 child graph-input frame；
- child continuation 内部 publications 只在其 exact `ScopeRunCoordinate` 可见，同路径其他 child generation 不可见；
- child compiled `GraphOutputBinding` 保留 source variant：`GraphInputPort` 直接从 matching child scoped-run graph-input frame 投影，`NodeOutputPort` 从 matching child scoped activation publication frame 投影；child input passthrough 不要求虚假 node publication；
- child completion acknowledgement 后，completed graph-output boundary 经 exact admission 形成 `ChildBoundaryAvailabilityCoordinate(child_scope_run, graph_output_descriptor)` 并成为 `CompletedChild.output`，固定携带 `ContinueGraphRouting`；
- parent projection owner 只按 parent activation derived child coordinate 读取该 boundary，并构造成 `TaskSuccess(output_frame, ContinueGraphRouting)`；parent nested settlement 未确认前，不 publication 给 parent downstream；
- `AbortedChild` 不产生 output frame 或 publication，只投影 existing typed parent failure；
- child State 仍只保存 control facts，concrete boundary frame 只在 root continuation/run context 中以 exact child-boundary coordinate transient 保存。

该协议复用 existing child projection、deterministic child run ID、frontier/session/settlement infrastructure，不增加 nested runner 或第二 completion source。

## 10. 文件级实施账本

### 10.1 Production

| 文件/目录 | 动作 | 约束 |
| --- | --- | --- |
| `execution/graph/ports.py` | ADD schema-only refs、port identities、bindings、declarations、frame descriptors、dual-source graph-output bindings | `GraphOutputBinding` 保留 `GraphInputPort | NodeOutputPort` source variant；`FrameDescriptorIdentity` 不保存 runtime run/activation；不定义 concrete entry/frame |
| `execution/graph/values.py` | ADD 唯一 canonical value/frame owner：`NamedValue`、`_ValuesConstruction`、`_ValuesSeal`、`_GraphValues`、`GraphInputFrame`、`NodeInputFrame`、`NodeOutputFrame`、`GraphOutputView` 与 `_make_graph_values`/shared immutable factories | `Graph.Values` exact alias `_GraphValues`；`Graph.values()` 是唯一 public constructor；construction/value dataclass 都 keyword-only，stored entries `init=False`，private token/seal construction；不导出 entries/internal frames，不依赖 facade/outcome/resume/request/result/run-context/engine/State |
| `execution/graph/node.py`、`definition.py` | REPLACE homogeneous node contract 为 callable/nested closed definitions | 不修改 callable，不保留旧 overload |
| `execution/graph/outcome.py` | REPLACE node return ABI 为 canonical `Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]` closed model；ADD `_OutcomeSeal` owner factories | 只从 `graph.values` 导入 public frame implementation/`FactoryValueT`；outcome variants/factories 只保留一份，public alias 无独立合法 constructor，删除 public `NodeOutcome` |
| `execution/graph/compiler.py`、`validation.py`、`topology.py` | ADD frontier-local data triggers、control eligibility、entry/completion、outcome validation/materialization/publication descriptors、planner-limit、resource-order、`FrontierTransitionPlan`、graph-output projection 与 recovery availability view | compiler 统一拒绝非法 edge/source/type/topology；生成 schema descriptor 与 runtime-coordinate construction plan，但不猜 run ID；frontier/session/routing/resource/nested/limits lowering 只生成一次；保留 committed resource first-seen order |
| `execution/graph/resume_input.py` | MIGRATE graph-local codec generic payload 为 canonical `Graph.Values[GraphValueT]` | 从 `graph.values` 直接导入唯一 carrier；保留现有 codec identity/version owner；不建 output codec |
| `execution/identity.py` | EXTEND existing execution identity owner with `ScopeRunCoordinate`、`StableActivation` 与 root/child canonical derivation functions | 复用 State `GraphRunId`、`ParentGraphActivation`、existing `child_graph_run_id()`；不保存 concrete value，不写 State，不允许 caller-provided arbitrary child run ID |
| `execution/errors.py` | EXTEND existing hierarchy with port/reference/type admission、unavailable-value 与 publication invariant errors | 不建第二 error hierarchy；错误只携带 stable coordinates |
| `execution/limits.py` | HARDEN exact-positive-integer validation in the existing sole `ExecutionLimits` owner；KEEP defaults | `type(value) is int` 且大于零，统一抛 existing `ExecutionLimitError`；三个 public `run()` overload 立即构造同一 effective value，不公开 `limits=`，不建 recovery/nested limits |
| `execution/resource/definition.py` | KEEP canonical resource identity/order model | `_graph.py` 只在完整 `add_node()` replacement commit 中按 successful node order 与 resource tuple 左到右 first-seen 登记；失败 candidate 零污染，compiler 只验证/消费，不另排序 |
| `execution/_graph.py` | REPLACE public builder/run/outcome/resume surface；ADD immutable `_GraphBuilderState` transactions、`Graph.Values = _GraphValues` alias、直接声明的两个 `Graph.values()` static overload 与 owner factory delegation、`_TransitionSeal`、exact scoped commit projection、atomic family freeze、limits-first dispatch、recovery preflight、closed Result projection 与 multi-child driving | 不定义/normalize concrete entries，不用 `staticmethod(factory)` assignment/cast 转接 overload；从 exact values/identity owners 导入；root scope 仅 `()`；preflight 覆盖 full scoped-run availability identity 与首个 Result/limit boundary；同一 limits 传入全部 scopes；不保存 live run context |
| `execution/request.py`、`result.py` | REPLACE typed frame request/result；ADD `_CommitResultSeal` canonical factories、exact root-state binding、completed/aborted/awaiting-resume closed variants、opaque continuation view 与 canonical commit result aliases | 直接依赖 `graph.values` 唯一 frames；三个 Result/union/continuation 都 invariant；`CompletedResult` 独占 covariant Values outputs；无 optional 宽字段、frame DTO 或 facade wrapper |
| `execution/run_context.py` | ADD private family/continuation seal、四类 scoped-run availability coordinates、四个 typed frame records、唯一 immutable `ScopedFrameIndex`、complete/recovered snapshot union | 每个 record 组合 exact `ScopeRunCoordinate`/`StableActivation`、descriptor 与 values-owner frame；typed segments 不擦除为 object/Any/bare dict，不建第二 frame map；不同 child runs 并存、same-coordinate different frame fail closed；不挂 Graph/executor/global，不进入 State，不作 activation/resolution authority |
| `execution/engine/recovery.py` | ADD immutable full-semantic `RecoveryTransferState` evaluator、separate `RecoveryTraversalKey` over compiled shared plans | equality/hash/dedup 覆盖 scoped-run control/availability/child/action facts；同 path different child run 不合并；sort key 只排序且不触碰 concrete value；禁止自建 edge/routing/session/resource/limits/nested semantics |
| `execution/engine/admission.py`、`superstep.py`、`planner.py` | REPLACE shared input 为 compiled materialization preflight，并消费 shared frontier transition/limits plan | `planner.py` 保持 `state.superstep >= limits.max_supersteps` 的唯一 concrete check coordinate；required value 缺失先于 claim/resource/node；prepare 的 Result/limit disposition 是 runtime/recovery 共同 boundary |
| `execution/engine/session.py`、`scheduler.py`、`settlement.py` | MIGRATE callable frame ABI、outcome/output admission、acknowledged publication candidate 与 shared quiescence transfer | typed failure/interrupt 不截断 remaining Pending、started completion 或 resource waiter；`max_parallel_tasks - live_count` 与 resource selector 唯一决定 legal starts/completions |
| `execution/engine/frontier.py`、nested projection path | MIGRATE exact child-scope-run input/boundary、`CompletedChild -> TaskSuccess(..., ContinueGraphRouting)`、`AbortedChild` failure 与 public family driving | parent activation 唯一派生/校验 child coordinate；同一路径 repeated child frames 不碰撞；同一 limits 进入每个 scope；parent ack 后才 publication |
| `execution/engine/routing.py` | MIGRATE current-frontier control candidates、data-trigger merge、nested fixed routing、completion/abort projection | State-owned settled/concrete-skip contribution 只解析一次；resume-to-Pending/future branch set 来自 compiler；recovery 不复制 resolver |
| `execution/engine/resume_input.py` | MIGRATE scoped codec selection、node-local frame encode/decode/admission/re-materialization | 每个 override/materialization 使用 target `StableActivation` coordinate；State 只见 existing opaque binding；失败先于 fence/resume/claim |
| `state/graph_state/**` | KEEP | 不修改、不导入 execution、不保存 value |

表中 owner/file 落点是本方案的唯一实施账本。若实现发现任一 dependency 无法按本文的单一模型闭合，必须停止并先回写本文复审，不能在代码中临时改名、增设 generic helper 或复制 owner。

### 10.2 Tests、docs 与 conformance

| 范围 | 动作 |
| --- | --- |
| `tests/execution/graph/**` | direct mapping、全部 edge validation、builder transaction、canonical values owner/factory、sealed Values/outcome construction、plain/routed/failure/interrupt admission、nested conditional prohibition、dual-source output、resource first-seen、shared plan determinism |
| `tests/execution/engine/**` | unique typed `ScopedFrameIndex`、same-State/different-availability non-merge、same-path/different-child-run non-collision、no concrete-value worklist operations、exact route transfer、control-loop limits、parallel legal order、full-frontier quiescence、resource waiter、nested child availability/limit/barrier、多-child driving、publication/ack |
| `tests/execution/test_graph_api.py` 与 public typing tests | independent factory TypeVar、heterogeneous/empty Values inference、`Graph.Values` direct-constructor rejection/read/`isinstance`、sealed transition/commit results、三个 closed `run()` overload、limits-first rejection、root/child narrowing、required continuation、snapshots/scoped codec、invariant negative universe cases；不写逐字符串键 exact-type 推导测试 |
| `tests/state/graph_state/**` | KEEP existing behavior；只证明 execution change 没有要求 State value/epoch |
| `tests/architecture/**` | no Any/object/reflection/cast/Unknown、module-level TypeVars、`graph.values` 单一 owner/import DAG、private seal ownership、full scoped-run recovery equality、single facade/engine/value owner、无 Store/output persistence/State value 扩张 |
| README 与相关 docs | 一次性同步 direct mapping、Values factory-only owner、exact scoped-run frame identity、sealed outcome/run/transition、builder transaction、recovery identity、limits、resource first-seen、continuation 和 no-persistence boundary |
| `conformance/**` | KEEP；本方案已冻结不改变 State 或 durable/cross-language DTO |

### 10.3 一次性迁移

- 所有 public node call sites 一次性改为 callable direct `inputs={}`/`outputs={}` 或 nested overload；不保留旧 homogeneous input/output signature。
- 所有 callable return 与 public imports 一次性迁移到 `Graph.Values | Graph.Outcome`、`Graph.success()`/`failure()`/`interrupt()`；删除 public/internal 双轨 `NodeOutcome` 命名，不建 facade wrapper。
- 删除 static factory 复用 class-bound `GraphValueT` 或用 class-attribute assignment 转接 owner overload 的 prototype；`Graph.values()` 在 facade 直接声明两个 `@staticmethod` overload，并与 `Graph.success()` 一次性迁移到 independent `FactoryValueT`，zero-argument overload 固定为 `Values[Never]`，不保留 Unknown/cast fallback。
- 将全部 `NamedValue`、public `_GraphValues` implementation、graph-input/node-input/node-output/graph-output frames 与 normalization 一次性迁移到 `execution/graph/values.py`；`_graph.py` 只保留 `Graph.Values` exact alias 和 `Graph.values()` typed delegation，删除其他模块的 frame dataclass/factory、raw mapping constructor 与 `graph.__init__` carrier re-export。
- `_ValuesConstruction`/`_GraphValues` 一次性采用 keyword-only private `_ValuesConstruction + _ValuesSeal` construction，stored entries 固定 `init=False`；删除任何 public-valid dataclass constructor、entries 参数、alternate classmethod/public factory 或 wrapper prototype，所有 internal frame creation 走 values owner factories。
- Outcome、commit result 与 transition canonical classes 一次性加入各 owner private identity seal；删除任何 public-valid constructor、unsealed dataclass prototype 或 global/shared seal helper，public aliases 保持原 class narrowing。
- 所有 run/commit call sites 一次性迁移到第 3.5 节三个 closed overload 与 scoped `Graph.Transition`；删除旧 `next_state` 字段/alias，调用方只返回 exact `candidate_state` successor。
- Graph builder assembly 一次性迁移为 immutable `_GraphBuilderState` replacement transactions；删除各 mutator 对 nodes/edges/entries/resources/outputs/codec 的分步 append、rollback 或 hidden tombstone。
- 删除与 binding 重复的 direct data edges；保留真正的 pure control edges。
- 旧 `TaskSuccess.output`/`CompletedChild.output` 迁移为 admitted frame，但仍不进入 State。
- 旧 shared graph input fallback、compile-only requirement 和“skip 后下游照常读取输入”测试全部替换为真实 dataflow behavior。
- frontier/session/routing/resource/nested/planner-limit control lowering 一次性抽取为 runtime/recovery 共用的 `FrontierTransitionPlan`；不得保留 recovery-only all-route、immediate-failure-boundary、独立 limits 或不受 selector 约束的 completion path。
- 删除旧缩短 worklist dedup tuple；seen set 只使用 full-semantic `RecoveryTransferState`，traversal ordering 只使用独立 `RecoveryTraversalKey`，concrete frame 只保留在 run context/continuation coordinate mapping。
- 将旧 `(scope, descriptor)` graph-input key、`(scope, StableActivation(run_id, ...))` publication key 与 descriptor-only child boundary 一次性替换为 `ScopeRunCoordinate`/embedded `StableActivation` coordinates；不保留 path-only fallback、implicit child-run lookup、old/new key dual-read 或迁移 adapter。
- 保留 node builder/resource tuple 的 first-seen declaration semantics；删除任何 compiler lexical re-sort、错误的任意 node-order equality 断言或重复 resource-order owner。
- `CompletedResult` 的任何 covariant prototype/alias 一次性删除；全部 Result variants 与 Result union 统一使用 invariant `GraphValueT`，不保留双轨 typing。
- 不通过 symbol/file absence scan 证明迁移；使用正向 public behavior、typing 和 architecture tests。

## 11. 测试矩阵

至少覆盖：

1. `inputs=`/`outputs=`/`set_outputs()` mapping mutation 不影响 canonical definition；全部 builder mutators detached-normalize 并 single-commit immutable `_GraphBuilderState`；`Graph.fields()` 不存在，内部不保存 bare mapping/list。
2. wrong position、mixed ref/type/concrete value、非法 port name 在 public normalizer 精确拒绝。
3. Python mapping duplicate-key 不承诺自定义错误；canonical identity/type conflict 仍拒绝。
4. ordinary zero-input 合法；ordinary input 不虚构 missing/extra schema；nested input 相对 child boundary exact completeness。
5. edge builder 允许文本 forward declaration；compiler 统一拒绝 unknown endpoint、非法 START/END 方向、duplicate declaration、非法 route/join source、data/direct same-pair 冲突、data cycle 与不合法 control loop。
6. unknown node/output port、wrong direction、self output reference、scope escape、真实 downstream reference 和无法 guaranteed-before 的 source 在 compile 阶段拒绝；文本 forward ref 若语义合法则接受。
7. nested node 作为 conditional source 在 compile 阶段稳定拒绝；nested success 作为 direct-edge source 和 join source 成功，基于 child output 的 route 只能经 ordinary router node。
8. compile/freeze 是 atomic 且 deterministic；任一错误不安装 partial cache、不冻结部分 family。不同 mapping/edge declaration order，以及不改变 successful-node resource first-seen sequence 的 node 重排，产生 canonical structural equality 和稳定错误；已提交 node/resource tuple 左到右首次出现产生 exact `ResourceDefinition.order`，改变该 sequence 的 node 重排明确产生非等价 compiled structure，compiler 不 lexical re-sort。
9. graph input concrete missing/extra/wrong exact type 在 `StartGraphRun` commit 前拒绝；valid root frame 先以 final `start_command.run_id` coordinate 暂存为 candidate，只有 exact Start successor 确认且 run ID 相等才安装，commit mismatch/exception/cancellation 不留下 descriptor-only/provisional frame。
10. root graph-input passthrough output、node-publication output、boundary rename 和 explicit empty output 均按 compiled `GraphOutputBinding` 投影；graph input 不要求伪造 publication。
11. completed/aborted/awaiting-resume 三个 invariant closed Result variants 均有非 optional `state` 与 invariant `continuation`；只有 `CompletedResult` 暴露 covariant immutable `outputs`，其他 variant 不存在该字段并可由 strict Pyright narrowing 证明。
12. 相同 definition ID/version 但不同 compiled-family token、state/continuation 交叉配对、root/child 相同 revision 但字段不相等、foreign/extra/inconsistent snapshot 在 fence/resume/claim 前拒绝；结构完全相等的 frozen State copy 接受。
13. complete snapshot 缺少任一应有 root/child input、publication、confirmed child boundary 或 current child snapshot 时作为 malformed 拒绝；同一路径 repeated child runs 的历史 frames 按 distinct `ScopeRunCoordinate` 全部保留。Recovered snapshot 合法缺少 recovery 前历史 frame，但其新增 frames 仍必须带 exact scoped-run identity，且 lineage 永不升级为 complete。
14. state-only invocation 可分别返回 `CompletedResult`、`AbortedResult`、`AwaitingResumeResult`，三者均携带本轮建立的 recovered continuation；后续 recovered invocation 只使用真实保存的新 frame，不补造历史值。
15. Recovery availability proof 复用 `CompiledGraph.transition`/shared `FrontierTransitionPlan`：current State 已有 settlement 与 concrete `skip_failed(..., route=...)` 只沿唯一 route，resume 为 Pending 的 conditional callable 与其他尚未执行的 conditional callable 只在 canonical all-success path 枚举 declared routes；typed failure/interrupt 在所有 current-frontier callable input 已 admission 后由 shared quiescence invariant 直接证明只能到 awaiting boundary，不枚举 sibling completion permutations；child proof 不做 Cartesian product；4096-state budget fail closed；seen 只合并 control/availability/child/action/invocation-new-child 全字段 structural-equal states。
16. invocation admission 时已有 nested Pending 但无 matching derived child `ScopeRunCoordinate`/snapshot：complete variant 作为 malformed 拒绝，state-only/recovered lineage 返回 `GraphValueUnavailableError`；本 invocation acknowledged parent transition 新产生的 nested activation 可以 start，并立即以 expected child run coordinate 记录 child State/input snapshot。
17. 有 matching continuation 的 child-only progress 产生同 lineage variant 的新 continuation，但可保持 root State value/revision；无 continuation 的 child-only progress 永远不启动或重建 child。
18. continuation final implementation 无合法 public constructor/copy/pickle/codec/Store path；缺少 private seal 无法构造，也不形成 Graph/executor/global cache。自洽旧 exact pair 的 latestness 不伪称可检测，authoritative commit 可拒绝旧 successor。
19. root/child `Graph.Values` resume codec 正确选择 scope 与 codec ID/version；encoder/decoder exception、override missing/extra/wrong type 在 fence/resume/claim 前拒绝。
20. `resume_failed()` 按 compiled bindings 重新 materialize，`resume_failed_with()`/`resume_interrupted()` 只接收 admitted frame；新 override 仅在 exact `ResumeGraphNodes` acknowledgement 后进入 continuation，commit mismatch/exception 时丢弃；State-owned opaque override 不伪造 ack，`UseStepRequestInput` 不回退 shared raw input。
21. RUNNING/AWAITING_RESUME child 通过 structured scope 继续同一 deterministic run；`AbortedChild -> parent FailedGraphNode` 后，parent `resume_failed*()`/restart 在 mutation 前返回 `SnapshotMismatchError`，`skip_failed()` 允许且不会重建 child。
22. multi-input/multi-output ordinary node 使用真实 named materialization；consumer 不收到 shared raw input。
23. node output missing/extra/wrong exact type 在 success settlement/publication 前拒绝，同时允许 existing cleanup。
24. publication 只在 exact settlement successor 确认后按 `PublicationAvailabilityCoordinate(StableActivation(scope_run, ...), descriptor)` 进入 run context；Start/Advance/Complete/Abort successor 的 `GraphRunState.frontier/status/superstep/revision` 继续是唯一 activation/resolution authority；commit mismatch/cancel/fence 不留伪事实。
25. initial/zero-input node 只由 acknowledged State frontier 驱动；data target 只由 current settled frontier 的 required producer 触发，不重扫 historical ledger，不建立独立 activation/resolution history。
26. multi-source 同 frontier/跨 frontier 只在最后一个 source ready 时生成一个 target coordinate；success empty frame 是 publication，skip 不是 publication/data trigger。
27. partial success + sibling failure/resume 保留成功 publication；同一 scope 的 authoritative frontier 只生成一个 reducer successor，repeated invocation 不读取旧 history；重放旧但自洽 pair 的分支责任属于调用方。
28. `StableActivation` 嵌入 exact `ScopeRunCoordinate` 并与 attempt token 分离；parallel sibling/child-generation frame 隔离，snapshot canonical sorting，same-coordinate duplicate/different publication 拒绝，State 是唯一 activation/resolution owner。
29. conditional/join control gate 与 data readiness 正确合成；control 和 data 同时选中 target 时只有一个 activation。
30. automatic entry、explicit START、duplicate entry、natural terminal、END、graph-input/node-output completion boundary 均有独立 behavior case。
31. State-owned settled/concrete-skip branch 的 historical value/child snapshot 缺失在 mutation 前返回 `GraphValueUnavailableError`；已确认 settlement 后才由不可预测 concrete output outcome 导致的 target/completion 缺值提交 `AbortGraphRun` 并返回携带 same-lineage continuation 的 `AbortedResult`，两者互斥。
32. 纯 data consumer 因 skip 不成为 candidate；若 completion/output 不依赖它则正常完成，若 boundary 依赖它则 abort；skip control 选中缺值 target 同样 abort。
33. control loop 只用新 superstep/parent coordinate 生成下一轮 activation，不串 value；ordinary data feedback cycle 拒绝。
34. atomic parent/child freeze；compile failure 不 partial freeze；frozen child 可独立运行但不可 mutation。
35. 同一 `StartMissingChildren` 的全部 invocation-new children 按 canonical order start；每个 `MissingChild` 先由 parent activation 派生/校验 child `ScopeRunCoordinate`，active children 按 child scope-run/parent activation round-robin，每个 scope 的 quantum 只使用其 `max_parallel_tasks`/resource selector 允许的 legal order，commit order deterministic，ordinary parent siblings 遵守 child barrier。
36. child 部分 completed/aborted、部分 runnable/awaiting-resume 时继续驱动 runnable scopes；无 runnable 时返回 `AwaitingResumeResult` 与 same-variant root continuation，不提前 settle parent。
37. `CompletedChild` 只投影 `TaskSuccess(output_frame, ContinueGraphRouting)`，`AbortedChild` 只投影 typed `TaskFailure`；parent nested output 仅在 parent `SettleGraphNode` acknowledgement 后 publication。
38. child `set_outputs()` 直接绑定 child graph input 时，只从 matching child `ScopeRunCoordinate` 的 admitted input frame 投影 `ChildBoundaryAvailabilityCoordinate`/`CompletedChild.output`，并通过同一 parent settlement/publication path 交付。
39. State files 无 value/history/execution import，existing reducer/recovery/`AbortGraphRun` tests 保持；state-only Pending zero-input、完整 State-owned opaque override、settled empty-output completion、active fence recovery 与 current root ABORTED/AWAITING projection 可在 whole-invocation proof 通过后执行，current nested child-only recovery 不在此集合。
40. `str | Locale | Tokens | Html` graph-wide carrier、independent `FactoryValueT`、invariant closed Outcome/Result/continuation union 与 frame resume codec 通过 strict Pyright/architecture gates，零 Unknown/Any/object/ignore/erasing cast；逐端口 exact type 只由 compile/admission behavior tests 证明，且无 Store/output persistence/State value 扩张。
41. Current SETTLED State 已保存 safe conditional route、另一 declared route 缺 historical value：recovery 只沿 safe route 并接受；交换 concrete route 后精确拒绝。
42. 同 frontier A typed failure/interrupt、B ordinary success：覆盖 A/B 两种 legal acknowledgement order，session 均继续到 Pending 为空才返回 `AwaitingResumeResult`，returned continuation 必须含 B 的 exact acknowledged publication；若 B input 缺 historical value，任何 settlement 前拒绝。
43. Typed failure/interrupt 分别与 resource waiter、precomputed nested completion、多个 parked/runnable children 组合；每类都有独立 behavior case，证明 shared prepare 先 admission 全部 callable input，且无 publication/control contribution时 quiescence 只能落到 awaiting boundary，无需 recovery 复制 waiter、round-robin 或 sibling completion permutations。
44. `skip_failed(..., route="safe")` 只沿 admitted concrete safe route；`resume_failed*()`/`resume_interrupted()` 重新置为 Pending 的 conditional callable 和其他尚未执行的 conditional callable 只在 canonical success projection 中分别检查全部 declared route branches，不能混淆两者。
45. Strict Pyright 负向 case：`CompletedResult[Dog]` 不能 widening 为 `CompletedResult[Animal]`，任一 `Result[UniverseA]`/`Continuation[UniverseA]` 不能传回 `Graph[UniverseB].run()`；独立 `Graph.Values[Dog] -> Graph.Values[Animal]` covariance 保持合法。
46. Public callable outcome 覆盖 plain `Graph.Values` success、`Graph.success(values)`、conditional routed success、`Graph.failure()` 与 `Graph.interrupt()`；conditional missing/unknown route 和 non-conditional unexpected route 均在 node call 后、settlement/publication 前以 existing `RoutingError` family 稳定拒绝。
47. 三个 `run()` overload 分别覆盖 new run、`state + continuation + resume` transient continue、`state + resume` control-only recovery；`values/state/continuation` 的非法交叉组合同时由 strict Pyright 与 runtime closed dispatch 拒绝，不存在第四条 fallback。
48. Root/child commit 分别验证 `Graph.Transition.scope` 的 `()`/structured path、`previous_state`、owner-internal `command`、`candidate_state`、optional admitted `result`，以及 `SuccessResult.output/route`、`FailureResult.failure`、`InterruptResult.request_payload` narrowing；`previous_state is None` 只出现在对应 scope 的 `StartGraphRun`，callback 必须返回结构相等的 candidate。
49. New run、transient continue、control-only recovery 三条路径分别对 bool/non-integer/non-positive `max_supersteps` 与 `max_parallel_tasks` 做 behavior case：全部由 existing `ExecutionLimits` owner 在 compile/cache/family freeze、continuation/resume preflight、fence/commit/State mutation、resource claim 与 node call 前抛 `Graph.ExecutionLimitError`。
50. State-only/recovered zero-output loop 的 abstract worklist 在 `max_supersteps` boundary 有限终止；concrete callable 持续选择 backedge 时在相同 scope/State/superstep 的 existing `plan_tasks()` coordinate 抛 `Graph.ExecutionLimitError`，不返回 Result/continuation/abort。
51. Future conditional loop branch 在 limit 前选择 exit 时正常返回 Result，不因另一 branch 可触达 limit 而提前失败；limit boundary 之后才需要的 historical value 不触发 `GraphValueUnavailableError`。
52. Quiescent recovered seed 已处于 superstep limit 时无 fence commit 并直接由 planner 抛错；active seed 先确认 exact fence、再确认 optional resume/skip、随后在同一 planner coordinate 抛错，已确认 transition 保留且无伪 continuation。
53. Nested child 在自己的 authoritative State/superstep 命中同一 effective limit 时，原 `Graph.ExecutionLimitError` 直接传播出 root invocation；不构造 `AbortedChild`、parent `TaskFailure`、`AbortGraphRun` 或任何 Result。
54. 对同一 State、effective limits 与 concrete route，recovery abstract transfer 和 concrete runtime 必须在同一 scope/State/superstep 到达 limit boundary；改变 `max_parallel_tasks` 的测试只走各自 selector/resource admission 允许的 canonical task path，绝不枚举 completion permutations 或因 slot/resource/barrier 不可达的 task。
55. `add_node(..., invalid inputs, resources=("database",))` local failure 后再成功添加 `resources=("network",)`：最终 nodes/resources 与从未发生失败调用的 graph structural equal，first-seen order 只能是 `network`；`database` 不得成为 hidden resource/tombstone。
56. `add_node()` 的 ID/body/inputs/outputs/resources、`set_outputs()`、direct/conditional/join builders、`set_resume_codec()` 每类 local failure 都对比调用前后 `_GraphBuilderState` object/value、compiled cache 与 frozen flag；全部不变，随后相同合法 retry 产生与 clean graph 相同 definition/error/order。
57. 同一 authoritative State/frontier 下构造两个 admitted recovered seeds：一个包含 A confirmed publication coordinate，一个合法缺失；consumer 需要 A output 时前者可推进、后者在 mutation 前 `GraphValueUnavailableError`，seen set 证明二者不合并。
58. 同一 root State 下 incoming exact child `ScopeRunCoordinate`/State/snapshot binding、child Missing/Active/Completed/Aborted projection 或 derived `ChildControlStateCoordinate` 任一不同都形成独立 `ChildRecoveryDisposition`；child graph-input/boundary frame availability 的差异由同一 `RecoveryTransferState.availability` 唯一承载。分别覆盖可执行、awaiting、terminal projection 与 unavailable path，不因相同 path/traversal prefix 合并，opaque payload 不进入 equality。
59. Recovery worklist 使用 concrete frame entries 为 hash/order/repr 均会主动失败的测试 value；preflight 仍只按 descriptor/stable-activation coordinates 完成。`RecoveryTraversalKey` collision 的 unequal states 全部访问，seen 只使用 full transfer-state equality。
60. Strict Pyright 正向 case：facade 上直接声明的 `Graph.values()` `@staticmethod` overloads 让裸 `Graph.values(raw=str_value, locale=locale_value)` 推导 exact heterogeneous union，裸 `Graph.success(frame)` 保留相同 `FactoryValueT`，再进入 `Graph[PipelineValue].run()`/`NodeCallable[PipelineValue]` 时零 `Unknown` 且无 class-TypeVar fallback；`values = staticmethod(owner_factory)` assignment prototype 必须由独立 typing negative spike 证明不可采用。
61. Empty typing 独立覆盖 `Graph.values() -> Graph.Values[Never]`、typed new run、plain empty success、conditional routed empty success 与 annotated intermediate binding；无 context 的 `SuccessOutcome[Never]` 不得 widening 为 invariant `Outcome[PipelineValue]`，不新增 empty variant 或 cast。
62. Outcome factory、settlement commit-result factory 与 family-driver transition 正向构造均可用；public `Graph.SuccessOutcome(...)`/`Graph.FailureOutcome(...)`/`Graph.InterruptOutcome(...)`、`Graph.SuccessResult(...)`/`Graph.FailureResult(...)`/`Graph.InterruptResult(...)`、`Graph.Transition(...)` 在省略 seal 或传入伪造 seal 时均不能形成合法实例，而 `isinstance(..., Graph.*)` narrowing 保留 exact invariant universe。Seal 不导出、不共享、不进入 public fields/equality/hash/repr/serialization。
63. Parent control loop 在 superstep 1 与 3 两次激活同一 nested node/path：existing `child_graph_run_id()` 产生 C1/C2，输入分别为 `query="first"`/`"second"`，child `set_outputs()` 直接 passthrough input；两次 `GraphInputAvailabilityCoordinate`/`ChildBoundaryAvailabilityCoordinate` 不相等，第二次 output 必须是 `"second"`，不能覆盖、拒绝或读取 C1。
64. 上述 repeated nested case 分别跨 complete continuation 与 recovered continuation 推进：snapshot 可同时持有 C1/C2 frames，current parent activation 只选择 derived child run；recovery equality/traversal collision 不合并两代 availability，same path/definition/descriptor 不能成为 lookup fallback。
65. `Graph.values(...)` 正向 factory、empty `Values[Never]`、read/`isinstance` 与 covariance 均通过 typing/behavior tests；`_ValuesConstruction`/`_GraphValues` constructor 均为 keyword-only，`Graph.Values()`、keyword/mapping/`NamedValue` entries direct construction，以及新建伪 `_ValuesConstruction`/`_ValuesSeal` 均不能形成合法实例，public signature/error 不泄漏 internal entry type。
66. Architecture/import tests 证明 `execution/graph/values.py` 是 `NamedValue`/`_GraphValues`/四类 frame/normalization 的唯一 definition owner；outcome/resume/request/result/run-context/engine/facade 只单向依赖 exact module，无 `graph.__init__` 回流、import cycle、shadow DTO/factory 或第二 public value 入口。
67. `ScopedFrameIndex` 的 graph-input/publication/resume-input/child-boundary 四个 typed segments 分别只接受 matching coordinate/frame record；acknowledged replacement 保留其他 segments，任一 duplicate coordinate 不比较 frame 即拒绝，不同 child run 并存。Continuation 与 graph-output projection 读取同一 immutable index，不存在 `dict[..., object/Any]`、optional-field record、第二 publication/input map 或重复 frame truth；records/index 的 hash/order/equality/repr trap values 均不会被触发。

覆盖率不能替代独立 behavior case；删除旧测试必须有明确 KEEP/MIGRATE/REPLACE 落点。

## 12. 实施阶段与复审顺序

### Phase 1：Definition 与 compiler

1. 实现 immutable `_GraphBuilderState` 与全部 mutator detached-candidate/single-assignment transaction；node/resource replacement 同时提交，local failure 保持 builder/cache/frozen state 原样，不实现 rollback/tombstone。
2. 新建低层 `execution/graph/values.py`，一次性实现 `NamedValue`、`_GraphValues`、四类 internal frame、module-level `FactoryValueT`/`GraphValueT_co`、shared normalization、`_make_graph_values` 与 keyword-only `_ValuesConstruction + _ValuesSeal`；`Graph.Values` exact alias，facade 直接声明 zero/variadic 两个 `Graph.values()` static overload 且只有该 typed delegation 可 public 构造，先以 strict Pyright spike 与 import architecture tests 固定签名和无回环 owner DAG。
3. 实现 direct mapping、structured refs、callable/nested definitions、dual-source `GraphOutputBinding` 与 graph-wide generic carrier；empty factory 固定 `Values[Never]`；冻结 `_OutcomeSeal`/`_CommitResultSeal`/`_TransitionSeal` owner-only construction，以及三个 run overload、Resume/Commit/Transition/Result/continuation 的 invariant surface。
4. 实现全部 edge endpoint、START/END、route/join source、nested conditional-source prohibition、重复/冲突、data/control、entry/completion、scope/direction/exact-type/guaranteed-before validation；所有完整边语义只由 graph compiler 裁决。
5. 实现 plain/routed/failure/interrupt outcome branch admission 与 route requirements；只保留 successful node transaction/resource tuple 的 first-seen order 并写入 canonical definition/plan，compiler 只验证 exact requirements，禁止 lexical re-sort。
6. 在 `execution/identity.py` 实现唯一 `ScopeRunCoordinate`/embedded `StableActivation` derivation；compiler 只生成 schema descriptors/runtime-coordinate construction plan。将 frontier/session/resource/routing/nested/limits/completion lowering 为 shared `FrontierTransitionPlan`，定义四类 scoped-run availability、full-semantic `RecoveryTransferState` 与 separate `RecoveryTraversalKey`。
7. 实现 deterministic atomic graph-family compile/freeze；mapping/edge 与不改变 successful first-seen sequence 的 node 重排要求 structural equal，改变 resource first-seen sequence 明确非等价；任一步失败均不安装 partial cache 或冻结部分 family。

### Phase 2：Run context、admission 与 publication

1. 实现三个 closed `run()` dispatch；每条路径的首个动作都以 existing `ExecutionLimits` owner 校验两个 public keywords，并把同一 effective value 传给 runtime、recovery 与 nested scopes，早于 compile/cache/freeze 和任何 State mutation，不公开第二 limits 入口。
2. 实现 graph-input admission、compiled materialization preflight、owner-sealed plain/conditional/failure/interrupt callable outcome 与 child output admission，以及从 matching scoped-run graph-input frame/confirmed node publication 的 graph-output projection；new-run root input 只在 exact Start successor 确认后按 final run coordinate 安装，child input 对称地只在 child Start successor 确认后安装；route error 固定在 call 后、settlement/publication 前。
3. 建立 invocation-scoped run context，以四类 typed records 和唯一 immutable `ScopedFrameIndex` 保存 exact runtime + descriptor coordinate 到 canonical values-owner frame；child State snapshot 与未确认 candidate 保持独立职责，candidate 不进入 index。Same coordinate/different frame fail closed，不同 child run frames 并存；index replacement 保留其他 typed segments，不建 object/Any/bare-dict 或第二 frame map。实现 seal-constructed complete/recovered continuation snapshot union，不建立 activation/resolution index，也不把 concrete entry 投入 recovery state。
4. 只在 exact node settlement successor acknowledgement 后安装 concrete frame 与 publication coordinate；root/child 每次 commit 都由 owner seals 投影第 3.5 节 exact result/`Graph.Transition`，acknowledged successor 直接更新 State-owned activation/resolution facts。Commit mismatch、exception、cancellation 或 fence 不留伪事实。
5. Session 在 typed failure/interrupt 后按 shared `max_parallel_tasks`/resource/nested barrier 继续 started completion、runnable ordinary sibling、resource waiter 和 precomputed nested completion，直到同 frontier 无 Pending；以 current authoritative State frontier 的 compiled trigger/routing plan 解析下一 disposition，不提前返回 AWAITING。
6. 删除 shared-input fallback；node 只接收 destination-local admitted frame。Graph output、Result output view 与 child boundary 不复制第二份 value truth；run context 不挂到 Graph、executor、State 或 global。

### Phase 3：Failure、loop、nested 与 continuation

1. 迁移 failure、interrupt、skip、conditional、join 与 control-loop behavior；loop 只以新 superstep/parent coordinate 生成 activation，禁止按 node ID 读取“最近值”；concrete runtime 只在 existing `plan_tasks()` coordinate 以本 invocation effective `max_supersteps` 终止。
2. 实现 completed/aborted/awaiting-resume invariant closed Result variants：全部携带 exact successor State 与 non-optional invariant same-lineage continuation，只有 `CompletedResult` 暴露 covariant immutable dual-source Values outputs。
3. 实现 recovered-lineage whole-invocation recovery availability evaluation over `CompiledGraph.transition`：seed 先从 exact State/parent activation 导出 `ScopeRunCoordinate`，再从 admitted concrete frames 投影 coordinates；seen 以 full scoped-run control/availability/child/action equality 去重，sort-only traversal key 不合并 collision；current settlement/concrete skip 只沿唯一 route，failure/interrupt 消费完整 frontier；worklist 在首个 Result/limit 终止。
4. 将唯一 graph-local resume codec 一次性迁移为 `Graph.Values[GraphValueT] <-> bytes`，实现 root/child scope selection、override encode/decode/exact admission、无 override 的 compiled re-materialization、`AbortedChild` parent restart prohibition，以及 `ResumeGraphNodes` ack 后安装。
5. 接入唯一 public family driver：完整消费 missing/active child tuple，每个 child 先由 parent activation 派生/校验 `ScopeRunCoordinate`，按 scope-run/activation canonical round-robin 驱动；同一路径 repeated child input/boundary frames 不碰撞。每个 scope 复用同一 limits/planner/selector；`CompletedChild` 固定投影 `TaskSuccess(..., ContinueGraphRouting)`，parent ack 后才 publication exact child boundary。
6. 按第 8.5 节已完成的充分性证明保持 `state/graph_state/**` 原样；本阶段只验证 existing opaque resume bytes、codec identity/version 与 reducer behavior 未被 execution migration 改变，不设计或追加 adapter、epoch、variant、child mirror 或兼容路径。

### Phase 4：一次性迁移与全量门禁

1. 完成全部 production/test/docs call-site 迁移，不保留 compatibility path。
2. 运行 Ruff、format、strict Pyright（包含 FactoryValueT/empty Never、`Graph.Values` direct-constructor negative、sealed aliases、outcome/run/transition shape 与 invariant universe）、完整 pytest、100% statement/branch coverage、build、twine。
3. 运行 mote-kernel `make check`、monorepo `pre-commit run --all-files` 和 whitespace gate。
4. 审计无 descriptor/path-only frame key、implicit child-run lookup、shortened recovery identity、concrete-value worklist operation、public-valid Values/sealed-class constructor、frame shadow owner、import cycle、partial builder mutation、第二 facade/runner/value/limits/resource-order truth、persistence 或 State value dependency，并逐项通过第 11 节 behavior cases。

## 13. 验收与停止条件

### 13.1 Production 持续验收条件

- 本文作为唯一规范事实源，不再含候选 accessor、frame owner、generic/factory/constructor shape、construction seal、scoped-run coordinate、recovery equality、builder transaction、outcome/commit surface、limits 入口、resource order、activation identity、child scheduling 或未裁决 compile/runtime 语义；
- direct mapping、control edge、nested overload、`Graph.success()`/`failure()`/`interrupt()`、三个 run overload、scoped `Graph.Transition`/commit result、invariant closed Result union/continuation 与 scoped resume frame 的 public shape 可由 strict Pyright 检查；`CompletedResult[Dog] -> CompletedResult[Animal]`、uncontextualized `SuccessOutcome[Never] -> Outcome[PipelineValue]` 及 cross-universe `run()` 必须是负向错误，且不要求 Pyright 做逐字符串键 exact-type 推导；
- 裸 `Graph.values(...)`/`Graph.success(...)` 使用 independent module-level `FactoryValueT`：facade 直接声明 zero/variadic 两个 `Graph.values()` `@staticmethod` overload，heterogeneous union、same-universe success、`Graph.values() -> Values[Never]`、typed empty new run/plain/routed success 全部零 `Unknown`/Any/object/cast；`FactoryValueT` 不进入 Graph instance/storage/Result/continuation，也不通过 class-attribute assignment 转接 overload；
- `execution/graph/values.py` 唯一拥有 `NamedValue`、`_GraphValues`、四类 internal frame 与 normalization；`Graph.Values` 是 exact class alias，但只允许 `Graph.values()` canonical factory 构造。Private keyword-only `_ValuesConstruction + _ValuesSeal` 不导出、不存储、不泄漏 entries；public direct call/新建伪 token 或 seal 失败，read/`isinstance`/covariance 保留；所有消费者单向 direct-import owner，无 shadow DTO、第二 factory 或 import cycle；`execution/run_context.py` 只用四个 typed records/一个 immutable `ScopedFrameIndex` 持有这些 frame，无 object/Any/bare-dict 擦除或第二 frame map；
- outcome、commit result、transition concrete public aliases 分别要求 canonical owner 的 `_OutcomeSeal`/`_CommitResultSeal`/`_TransitionSeal`，只允许 factory/settlement projection/family driver 构造；direct public construction 无合法路径，class aliases 仍支持 exact `isinstance` narrowing，seal 不导出、不共享；
- 三个 run overload 都只公开 `max_supersteps`/`max_parallel_tasks`，并在 compile/cache/freeze、continuation/resume admission 与 State mutation 前由 existing `ExecutionLimits` owner 构造同一个 effective value；runtime、recovery 和全部 nested scopes 只消费该值，不存在 `limits=`、recovery limits 或 child limits；
- node/output reference、nested boundary、全部 `add_edge()`/conditional/join topology、nested conditional-source prohibition、scope/direction/exact type 和静态不可 guaranteed-before 的反例均在 compile phase 产生 deterministic `GraphValidationError`，且没有 compiled cache、Start commit、resource claim 或 node call；
- plain success、conditional routed success、failure、interrupt 与 route admission 只有第 3.5/4.2 节一个 outcome owner；conditional missing/unknown route 和 non-conditional unexpected route 在 node call 后、settlement/publication 前稳定拒绝，不暴露 `NodeOutcome` 或复制 facade DTO；
- 全部 builder mutators 从 immutable `_GraphBuilderState` 纯推导 replacement 并 single-commit；任一 local failure 后 builder value/identity、compiled cache/frozen state 完全不变。Resource global order 只由 successful `add_node()` transaction order 与每个 resource tuple 左到右的 first-seen sequence 决定，失败 node 不登记 resource；compiler 不 lexical re-sort；
- `GraphOutputBinding` 只保留 `GraphInputPort | NodeOutputPort` source variant；root/child graph-input passthrough 与 confirmed node-publication output 走同一 compiled projection，不建立 publication-only 假设或第二 value map；
- `FrameDescriptorIdentity` 只表示 schema；root/child concrete frames 统一使用 `ScopeRunCoordinate(scope, graph_run_id)`，`StableActivation` 嵌入该 coordinate。Graph-input/publication/resume-input/child-boundary keys 都组合 exact runtime coordinate 与 descriptor，不存在 definition/path-only key、implicit child-run lookup 或 old/new dual path；new-run root/child graph-input candidate 只在 matching `StartGraphRun` successor 确认后按同一 final coordinate 安装；
- initial candidate 只来自 automatic/explicit entry；后续 data candidate 只来自 current authoritative settled State frontier 的 compiled producer trigger；同一 State transition 最多生成一次 target coordinate，success empty frame 与 skip、resume、multi-source、loop/repeated `run()` 的一次性语义均有独立行为证据，且不依赖 history receipt；
- publication 只在 exact node settlement successor acknowledgement 后安装；`StartGraphRun`/`AdvanceGraphFrontier`/`CompleteGraphFrontier`/`AbortGraphRun` 的 successor State 直接拥有 activation/resolution，commit mismatch、cancellation、fence 和重复旧-frontier lowering 均不留下 transient control truth；
- `CompletedResult`、`AbortedResult`、`AwaitingResumeResult`、`Result` 与 continuation 都使用 invariant `GraphValueT`，每个 Result 有 non-optional State/continuation；只有 completed variant 有 covariant immutable `Graph.Values[GraphValueT]` outputs，不能借此 widening Result；
- continuation 与 Result 共享结构相等的 immutable root-state binding 并携带 execution-private compiled-family identity；complete snapshot 保留全部 distinct scoped-run historical frames，same path repeated child runs 不覆盖。Required frame 缺失、same-coordinate different frame 与所有 extra/inconsistent/malformed snapshot 拒绝；recovered 的合法历史缺失不误判但新增 frame 仍用 exact coordinate；
- `FrontierTransitionPlan` 是 runtime/recovery 共用的唯一 frontier/session/resource/routing/join/nested/planner-limit/completion lowering，并由 `CompiledGraph.transition` 直接持有；它持有 entries、materialization、publication descriptor map、graph completion 与 resource order；callable/nested classification 分别从 `CompiledGraph.nodes` nominal definitions 与 `nested_graphs` keys 派生，不重复存储；recovery evaluator 只传播 canonical availability coordinates，不自建 edge map、resolver、scheduler、barrier 或 limits policy；recovery 使用 canonical completion、linear child proof 与 4096-state pre-mutation safety budget；
- `RecoveryTransferState` equality/hash/dedup 覆盖所有 scoped-run control、availability、child 与 admitted-action semantic fields；same path/definition/descriptor 下不同 child run IDs 不合并，seen 不使用 shortened tuple。`RecoveryTraversalKey` 只排序且 collision 不合并；concrete frames 只在 run context/continuation coordinate mapping，worklist 不调用 user value ordering/hash/repr；
- state-only/recovered invocation 从 exact State/resume branch 推进到首个 Result 或 exact planner limit boundary：current State 已有 settlement 与 concrete skip 只沿唯一 route，resume-to-Pending/尚未执行 conditional callable 的 canonical success projection 才枚举 declared routes；全部 current-frontier callable input admission 后，typed failure/interrupt 由无 publication/control contribution 的 quiescence invariant 证明只能到 AWAITING，不展开 sibling outcome 或 completion-order 状态；limit branch 立即终止且不检查其后的 availability；
- parked child 不阻断其他 runnable child，parent ordinary sibling 遵守 existing child barrier；每个 child 在自己的 State/superstep 复用同一 planner check，limit 直接以原 `Graph.ExecutionLimitError` 终止 root invocation，不转成 `AbortedChild`/parent failure/Result；任一真实可达 branch 在首个 Result/limit boundary 前的 required history/child snapshot 缺失均在 fence/resume/claim/child-start/resource/node call 前返回 `GraphValueUnavailableError`；
- pre-mutation unavailable 与 post-settlement dynamic missing 的 `AbortGraphRun`/`AbortedResult` 严格互斥；root/child projection 保留 exact successor State 与 same-lineage continuation，纯 data consumer 的非触发 completion 边界有明确测试；
- resume codec 的唯一 ABI 是 graph-local `Graph.Values[GraphValueT] <-> bytes`；root/child scope selection、codec identity/version、override admission、compiled re-materialization、`ResumeGraphNodes` ack 后安装、State-owned opaque override 与 RUNNING/AWAITING child scoped resume 均闭合；
- invocation admission 时已有 nested Pending 却无 matching derived child `ScopeRunCoordinate`/snapshot 必须 fail closed；只有本 invocation acknowledged transition 新产生的 nested activation 可派生 expected child run、start 并按该 coordinate 记录 snapshot。同一路径 repeated activation 的 frames 并存，且 current parent 只选择其 child run；active children 按 scope-run/parent-activation round-robin 驱动；
- nested success 固定投影 `TaskSuccess(output_frame, ContinueGraphRouting)`，direct/join 合法；`AbortedChild -> parent FailedGraphNode` 后 parent retry/restart 拒绝、`skip_failed()` 允许；child boundary 只在 parent settlement ack 后 publication；
- whole-invocation proof 通过时，state-only Pending zero-input、完整 State-owned opaque override、active fence recovery、settled empty-output completion 与 current root ABORTED/AWAITING projection 可执行；到达 Result 才返回 recovered continuation，到达 limit 则不返回 Result/continuation。无 continuation 的 child-only progress 明确不可执行；
- data/control、entry/completion、values module/factory、generic、sealed construction、builder transaction、scoped-run/recovery identity、outcome/commit、limits、resource order、publication/ack、family driving 与 State owner 边界均只有一个定义；文件与测试迁移账本完整，无 compatibility、第二 runner/value/frame/limits truth 或 persistence 扩张。

### 13.2 必须停止并回到需求/架构复审

1. graph-wide generic carrier 只能依赖 `Any`、`object`、reflection、ignore、dynamic dict 或 generic-erasing cast；
2. 除非另经 requirement review，需要让调用方在 canonical `graph_input`/`outputs` 之外再维护一份 port-name-to-exact-type declaration，或引入 contract token/schema/codegen/plugin；graph-wide `PipelineValue` 静态上界不属于逐端口 contract；
3. 需要 Store、journal、checkpoint、cross-process output recovery 或把 concrete value 写入 State；
4. 需要在 Graph/executor/global 保存 live run context，或建立第二 runner/data edge truth；
5. production diff 试图修改 `state/graph_state/**`、增加 State value/child mirror，或绕过第 8.5 节已经冻结的 control coordinates；这是实现偏离，必须纠正 execution 设计，不能以 adapter 应付；
6. 需要保留旧 API overload、fallback、alias 或双写；
7. nested public driving、fixed success routing 或 aborted-child recovery 无法通过 existing child projection/settlement owner 闭合；
8. recovery preflight 需要复制 routing/join/session/resource/nested/planner-limit semantics，而不是消费 shared `FrontierTransitionPlan`；
9. 任一 Result variant 或 Result union 必须 covariant，导致 invariant continuation universe 可以被 widening。
10. 任一 run/recovery/nested path 需要第二个 limits 入口、不同 effective limits、不同 superstep check coordinate，或需要把 limits 写入 compiled cache/State；
11. 实现需要 lexical re-sort resource、改变 first-seen semantics，或同时声称改变 first-seen sequence 的 node 重排仍 structural equal；
12. `Graph.Outcome` factories、三个 closed run overload、scoped `Graph.Transition`/commit result 的 exact surface 无法按第 3.5 节一次性实现，必须留给编码期选择或兼容旧 shape。
13. recovery seen/dedup 需要省略任何 transfer-relevant availability/child/action fact、使用 traversal sort key 代替 full equality，或比较/hash/repr concrete user frame value；
14. bare static factory 需要复用 class-bound `GraphValueT`、通过 `staticmethod(factory)`/class-attribute assignment 转接 overload、产生 `Unknown`、依赖 Any/object/cast，或 empty values/outcome 需要第二 nominal variant/alternate factory；
15. public outcome/result/transition aliases 必须允许无 seal construction、依赖 `final`/`frozen` 假装禁止 constructor，或需要 global/shared/public seal registry；
16. 任一 builder mutator 必须先修改 node/edge/entry/resource/output/codec 再 rollback，local failure 会改变 `_GraphBuilderState`/cache/frozen state，或失败 `add_node()` 会占用 resource first-seen order。
17. 任一 concrete graph-input/publication/resume-input/child-boundary key 需要省略 exact `ScopeRunCoordinate`、以 schema descriptor/definition/scope path 充当 runtime identity、从 `ChildControlStateCoordinate` 隐式补 run ID，或同一路径 repeated child runs 需要覆盖/复用旧 frame；
18. `Graph.Values` canonical implementation/`NamedValue`/internal frames 需要散落多个模块、经 `graph.__init__` 回流形成 cycle、暴露 entries/mapping direct constructor、无法使用 keyword-only `_ValuesConstruction + _ValuesSeal` 阻止合法 public instance，或增加与 `Graph.values()` 并列的 value factory/wrapper；
19. run context/continuation 需要为 graph-input/publication/resume-input/child-boundary 建立互相漂移的多个 frame truth、`dict[..., object/Any]` 或 optional-field wide record，而不是消费唯一 typed `ScopedFrameIndex`；

## 14. 结论

本方案已经按最新参数绑定删除 `Graph.fields()`，固定 callable direct `inputs`/`outputs` mapping 与 structured refs。`add_edge()`、conditional edge、join 和 data binding 都先收集 immutable declaration，再由完整 graph compiler 统一校验 endpoint、方向、scope、exact type、data/control topology、entry/completion 与 guaranteed-before；mapping/edge declaration order 不形成另一份真相。Resource 是明确的例外：global order 唯一按 successful node builder transaction 与 resource tuple 左到右 first-seen 形成，改变该 sequence 就是改变图语义，compiler 不 lexical re-sort。

Builder mutation boundary 已闭合：`add_node()`、`set_outputs()`、edge builders 与 resume-codec builder 全部从 immutable `_GraphBuilderState` 构造 detached complete replacement，成功后 single assignment；local failure 不改变 definition、resource order、compiled cache 或 frozen state。特别是失败 `add_node()` 从未登记 resource，后续 order 与失败调用不存在时完全一致，不使用 rollback、tombstone 或兼容路径。

Concrete value owner 已闭合为唯一 module：`execution/graph/values.py` 独占 `NamedValue`、`_GraphValues`、graph-input/node-input/node-output/graph-output frames、immutable normalization、`FactoryValueT` 与 keyword-only `_ValuesConstruction + _ValuesSeal`。`Graph.Values` 直接 alias canonical class，保留 annotation/read/`isinstance`，但不能通过 class call、raw mapping/entries 或新建伪 capability 构造；`Graph.values()` 是唯一 public factory，`_graph.py` 直接声明两个 static overload 后只 typed-delegate，不通过 class attribute assignment 搬运 owner signature。Outcome/resume/request/result/run-context/engine/facade 单向 direct-import 该 owner，不经 package aggregator 回流，也不定义第二 frame DTO；run context/continuation 只通过四类 typed records 与一个 immutable `ScopedFrameIndex` 持有 owner frames，不另建 publication/input value truth。

唯一 public facade 也已闭合：裸 `Graph.values()`/`Graph.success()` 使用 independent `FactoryValueT`，heterogeneous frame 保留 union，zero-argument frame 固定为 covariant `Values[Never]`，通过 expected context 进入 invariant empty success，全程零 Unknown/Any/cast。Callable 只返回 plain `Graph.Values` 或由 `Graph.success()`/`failure()`/`interrupt()` 构造的 `Graph.Outcome`；conditional route 在 settlement/publication 前严格 admission。Outcome、commit result 与 transition public aliases 分别由 owner-private seals 阻止 direct construction，同时保留 class annotation/`isinstance` narrowing。New run、transient continue、control-only recovery 是三个 closed overload，且都只公开两个 limits keywords。Root/child commit 共用 exact scoped `Graph.Transition`，root scope 仅表示为 `()`，callback 返回与 `candidate_state` 结构相等的 authoritative successor；public 不导入 `NodeOutcome`、`ExecutionLimits`、seals 或 State command constructors。

Result/continuation 协议现已闭合：completed/aborted/awaiting-resume 三个 variants、Result union 与 continuation 全部使用 invariant `GraphValueT`，并携带 exact root State 与 non-optional sealed continuation；只有 completed variant 暴露 independently covariant immutable `Graph.Values[GraphValueT]` outputs。New run 建立 complete snapshot；state-only run 建立允许 recovery 前历史 frame 缺失的 recovered snapshot，后续永不升级。Result covariance 不再能绕过 continuation universe。

三个 run overload 都先由 existing owner 构造同一个 effective `ExecutionLimits`，早于 compile/cache 和任何 State mutation；该值原样进入 runtime、recovery 与每个 nested scope。State-only/recovered invocation 在任何 mutation 前用 `CompiledGraph.transition` 覆盖到首个 Result、exact planner limit boundary 或 recovery proof safety boundary，但 availability evaluator 不拥有 control semantics。Compiler 只 lowering 一份 shared `FrontierTransitionPlan`；runtime/recovery 共用 task planning、resource claim、三类 settlement projection 与 routing/join/completion，并从 canonical `CompiledGraph.nodes` nominal definitions / `nested_graphs` keys 消费 callable/nested classification。Current State settlement/concrete skip 只沿已确定 route；Pending/尚未执行 conditional callable 只在 canonical all-success path 枚举 declared success routes。Shared prepare 已对全部 callable 输入 admission，failure/interrupt 又不能贡献下一 frontier routing，因此 evaluator 以 quiescence invariant 证明这两类 branch 必然止于 awaiting，不复制 runtime completion permutations；child 独立证明后只建立一个 all-completed plan 与线性 abort variants。Recovery seen 使用包含完整 control/availability/child/action/invocation-new-child facts 的 frozen transfer-state identity；bounded priority traversal 只排序，same State/different snapshot availability 不合并，concrete frames 不参与 hash/order/repr。4096-state budget 超出时在 mutation 前抛 `ExecutionLimitError`；planner limit branch 同样立即终止且不检查其后的 availability。

Runtime frame identity 也已闭合：`FrameDescriptorIdentity` 只表示 schema，`ScopeRunCoordinate(scope, graph_run_id)` 表示 exact runtime graph run，`StableActivation` 嵌入该 coordinate。Root 使用 root run ID；child 使用完整 scope path 与 existing `child_graph_run_id(parent activation)`。Graph-input、NodeOutput-binding publication 与 resume-input coordinate 分别由上述三个 source-specific typed constructor 形成，其他 settlement/installation coordinate 留在自身 nominal 边界；child-boundary coordinate 同样组合 runtime identity 和 descriptor。same path 的 C1/C2 child runs 可以在 complete/recovered continuation 中并存，current parent activation 只读取其 derived child run。Recovery equality 与 traversal 同样消费这一 coordinate，不从 path、definition 或 child control disposition 补 identity。

Graph output 不再假设 publication-only：compiled binding 明确保留 `GraphInputPort | NodeOutputPort`，分别从 admitted graph-input frame 或 confirmed node publication 投影，因此 root input passthrough 与 child input passthrough 都有唯一 owner。该 view 不形成第二份 value truth，也不要求把 output 写入 State。

Nested terminal boundary 同样闭合：`CompletedChild` 固定成为 parent `TaskSuccess(output_frame, ContinueGraphRouting)`，nested conditional source 在 compile 阶段拒绝，direct edge/join 保留，按 child output 路由必须增加 ordinary router。`AbortedChild` 只投影 typed parent failure；成为 parent `FailedGraphNode` 后禁止用 parent `resume_failed*()` 复用同一 child identity restart，只允许 `skip_failed()` 或终止。多个 children 由唯一 family driver 按 child scope-run/parent activation canonical start、round-robin 推进，每个 scope 在自己的 State/superstep 使用同一 limits/planner/selector；child limit 原样终止 root invocation。Child graph-input passthrough 与 output boundary 只使用 matching child run coordinate，且只在 parent settlement acknowledgement 后 publication。

Data-driven activation 只由 current authoritative State frontier 的 compiled trigger 一次性生成，不重扫历史 frontier，也不复制 activation/resolution truth；已提交 skip/settlement 后的动态缺值复用 `AbortGraphRun`，并返回携带 exact successor same-lineage continuation 的 `AbortedResult`。Resume codec 唯一迁移到 scoped graph-local `Graph.Values` frame，新 override 仅在 `ResumeGraphNodes` acknowledgement 后安装，State-owned opaque override 不伪造 ack。

逐端口类型责任已最终裁决：Pyright 不从 runtime string key 推导 exact type，只保持 parameterized graph-wide value universe 与 Python API shape；graph compiler 从唯一 canonical declarations 解析 exact descriptors，并在任何 compiled cache、freeze、`StartGraphRun`、commit、resource claim 或 node call 前严格拒绝非法 node/output reference、nested boundary、`add_edge()`/conditional/join topology 与无法 guaranteed-before 的值。Concrete input/output 则在各自最早可观察的 admission 点按同一 descriptor exact check。

State 决策已经在第 8.5 节完成而非留待实施：现有 `run_id/definition/parent/superstep/frontier/revision/execution token/resume codec/join/status/abort` 足以拥有本需求全部 control/recovery facts，`state/graph_state/**` 无条件 KEEP。Concrete frame、publication、snapshot variant 与 family pairing 是 execution-owned transient facts，只存在 continuation/run context；本需求不实现 State value/child mirror、Store、journal、checkpoint、output persistence 或跨进程 output recovery。

因此本文已完整落地并成为唯一规范事实源：schema identity 与 exact scoped-run frame identity 已分离，同一路径 repeated nested activation 不碰撞；`Graph.Values` canonical owner、factory-only construction、strict generic、sealed outcome/result/transition、builder transaction、compiled joint-join proof、content-level continuation admission、bounded shared-plan recovery、resource/nested/State KEEP 均有 production 与持续门禁证据。历史评审状态不再覆盖本结论。
