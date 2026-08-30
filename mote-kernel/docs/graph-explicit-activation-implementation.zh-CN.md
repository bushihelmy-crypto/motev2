# Graph 输入绑定与显式执行激活解耦实施方案

> 核心原则：graph definition 中的 `inputs=` 只声明一次 activation 读取什么值，direct、conditional 与 join edge 只声明什么 control event 可以产生该 activation。`NodeOutputRef` 不再隐式创建执行边；node-output consumer 缺少 incoming control edge 时，compiler 必须在任何 State mutation、commit callback 或 node side effect 之前以现有 `GraphValidationError` fail closed。

## 1. 文档信息与当前状态

- 状态：**IMPLEMENTED / VERIFIED / IMPLEMENTATION-OWNER WRITEBACK COMPLETE / ACCEPTED FOR INTEGRATION**
- 日期：2026-08-26
- 所属项目：Mote Kernel
- 唯一公共门面：`mote_kernel.execution.Graph`
- 唯一执行引擎：现有 `execution` engine
- 变更性质：删除 implicit data activation，复用现有 control topology
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 本轮评审：[Graph 输入绑定与显式执行激活解耦实施方案独立评审](graph-explicit-activation-implementation-review.zh-CN.md)
- 初始评审版本 SHA256：`ecc4bb6e885407d0ee15135ea1e8a09c63f2fa317f9800af79ca294ad2240f7b`
- 批准实施版本 SHA256（本次 writeback 前）：`5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6`
- production 基线：Git `563a45124311f11e870d0627461102baeffdf7ad`
- 实际实现：已按第 6.1 节五文件 production manifest 完成；当前工作树尚未创建独立 production commit
- production authorization：**GRANTED FOR THIS VERIFIED CHANGE UNIT**（2026-08-26；仅限本方案 reviewed target 与第 6.1 节 manifest）
- implementation-owner writeback：**COMPLETE**（2026-08-26）；实际 manifest、结构账本、门禁结果与边界裁决见第 13 节及独立验收记录
- 评审处置：R1–R6 全部接受；没有不接受条目，因此不新增 review-response 文档

本文只记录目标语义相对当前实现的 delta、必要 production manifest、definition/test/doc 迁移、复杂度 writeback 与验收门禁。它不重复拥有 State、persistence、recovery、commit、nested 或 deployment 的完整规范。

本方案对应的 production、active tests、README 和当前规范事实源已在当前工作树 change unit 中同步，并已由独立验收记录验证；本文现记录实际实施状态与 owner writeback，不改变 State、persistence、recovery、commit、nested 或 deployment 的唯一 owner。历史评审文档只拥有评审过程与整改裁决，不成为第二规范事实源。

## 2. 当前问题与目标 delta

### 2.1 当前 implicit activation 链

当前 compiler/runtime 将一个 `NodeOutputRef` 同时解释为 value binding 和 execution trigger：

```text
Graph.node_output("A", "value")
    -> compiler data_dependencies[B].add(A)
    -> B 没有 activation_gates 时 data_targets[A].add(B)
    -> FrontierTransitionPlan.data_triggers[A]
    -> routing 在 A publication 存在时加入 B
    -> AdvanceGraphFrontier(..., node_ids=(..., B, ...))
```

因此，下面两类 declaration 会产生合法但违反直觉的行为：

```python
# A 选择 go -> C 时，B 也会因读取 A.value 而进入同一下一 frontier。
graph.add_node(
    "B",
    run_b,
    inputs={"value": Graph.node_output("A", "value")},
    outputs={},
)
graph.add_conditional_edge("A", "go", "C")
```

```python
# A -> END 不会阻止 publication-driven B 继续执行。
graph.add_node(
    "B",
    run_b,
    inputs={"value": Graph.node_output("A", "value")},
    outputs={},
)
graph.add_edge("A", Graph.END)
```

这不是异步竞态，而是 value declaration 与 control declaration 的职责混合。

### 2.2 目标语义

改造后的线性声明必须同时包含 value binding 和 control edge：

```python
graph.add_node(
    "A",
    run_a,
    inputs={"request": Graph.graph_input("request", Request)},
    outputs={"value": Value},
)
graph.add_node(
    "B",
    run_b,
    inputs={"value": Graph.node_output("A", "value")},
    outputs={"result": Result},
)
graph.add_edge("A", "B")
```

两条 canonical declaration 各自只有一个职责：

```text
B.inputs["value"] <- A.value
    = B 被激活后如何 materialize value

DirectEdge(A, B)
    = A 的 routing contribution 可以激活 B
```

删除 binding 会改变 B 的 input frame contract；删除 edge 会在 compile 阶段失败。compiler/runtime 不再从前者推导后者。

## 3. 冻结的语义与 owner 边界

### 3.1 Canonical declaration 与 derived lowering

| 事实 | canonical declaration | derived compile/runtime form |
| --- | --- | --- |
| consumer 参数取值 | `GraphDefinition.nodes[*].inputs` 中的 `GraphInputRef | NodeOutputRef` | compiler-local `data_dependencies`、`ResolvedInputBindings`、`MaterializationPlan` |
| 节点激活资格 | `GraphDefinition.entries` 与 direct/conditional/join edges | compiler-local `activation_gates`、compiled direct/conditional/join indexes |
| concrete output 是否存在 | exact acknowledged publication frame | typed availability coordinate与 materialization lookup |

边界要求：

- `data_dependencies`、`activation_gates` 只是在一次 compile invocation 内使用的 typed local indexes；
- `ResolvedInputBindings`、`MaterializationPlan` 与 `FrontierTransitionPlan` 是 derived lowering，不是第二份 graph declaration truth；
- derived lowering 不持久化、不导出为 public builder API、不缓存为可独立修改的 topology；
- runtime/recovery 只消费唯一 `CompiledGraph.transition`，不回读 builder、反射 callable 或重建 reverse binding index。

### 3.2 Direct、conditional、join 与 coordinator

#### Direct

```python
graph.add_edge("A", "B")
```

适用于 A 的成功/skip routing contribution 无条件允许 B activation。

#### Conditional

```python
graph.add_conditional_edge("review", "approved", "publish")
graph.add_conditional_edge("review", "rejected", Graph.END)
```

`publish` 即使读取 `review` 输出，也只在 `approved` route 激活。

#### Join

```python
graph.add_node(
    "merge",
    merge,
    inputs={
        "left": Graph.node_output("left", "value"),
        "right": Graph.node_output("right", "value"),
    },
    outputs={"merged": Merged},
)
graph.add_join(("left", "right"), "merge")
```

multi-source AND barrier 使用现有 join。不得把 `left -> merge` 与 `right -> merge` 两条 direct edge 当作 join；direct alternatives 表达的是独立 control contributions，不是“全部到齐”。

#### Coordinator-controlled consumer

```python
graph.add_node(
    "consume",
    consume,
    inputs={"value": Graph.node_output("producer", "value")},
    outputs={},
)
graph.add_edge("coordinator", "consume")
```

该 definition 只有在现有 guaranteed-before proof 能证明每条 `coordinator -> consume` activation path 都已经产生 `producer.value` 时合法。

### 3.3 Automatic entry 与 natural completion 保留

本期不顺带强制显式 START/END：

```text
automatic entry
    = 没有 NodeOutputRef data dependency
      且没有 incoming direct/conditional/join control gate
```

因此 graph-input-only 或 zero-input root node 仍可自动进入 initial frontier：

```python
graph.add_node(
    "load",
    load,
    inputs={"request": Graph.graph_input("request", Request)},
    outputs={"value": Value},
)
```

现有边界保持不变：

- automatic entry 又被显式 `START -> node` 指向仍是 `DuplicateBoundaryError`；
- explicit START target 依赖 node output 仍以现有错误拒绝；
- 没有 outgoing control target 的 settled frontier 仍按现有 natural completion 规则解析；
- `set_outputs()` 的 `NodeOutputRef` 只声明 graph output projection，不是 node consumer，也不需要 control edge。

### 3.4 Missing incoming control

含一个或多个 `NodeOutputRef`、但没有 incoming direct/conditional/join gate 的节点，compiler 直接抛现有 `GraphValidationError`：

```text
node 'B' consumes node outputs from ('A', 'C') but has no incoming control edge
```

producer tuple 必须按 canonical node ID 排序并去重。public test 只通过 `Graph.ValidationError` 与关键消息观察该失败；不新增 `MissingActivationError`、helper、DTO、flag、index、facade alias 或 error factory。

错误优先级固定为：

1. malformed definition/identity/edge declaration；
2. unknown/self source、unknown output、nested boundary mismatch；
3. ordinary data cycle；
4. explicit START target with node-output dependency；
5. node-output consumer missing incoming control；
6. automatic/explicit entry与 reachability；
7. joint-route、guaranteed-before、publication-coordinate与 graph-output guarantee。

missing-control 不得遮蔽现有 explicit START 错误，也不得抢占 unknown source 或 data-cycle 错误。

### 3.5 Data/direct same-pair

同一个 `(source, target)` 可以同时存在以下两类不同 canonical declarations：

```text
GraphDefinition.nodes[target].inputs:
    source.value -> target.local_value

GraphDefinition.edges:
    DirectEdge(source, target)
```

前者由 input binding 声明 value contract，后者由 edge 声明 control contract；它们不是重复 truth。compiler 可以分别派生 `ResolvedInputBinding` 和 `direct_targets`，但 derived objects 不成为新的 declaration owner。

真正重复的两个 `DirectEdge(source, target)` declaration 继续由 `graph/validation.py` 以 `DuplicateEdgeError` 拒绝。

### 3.6 明确非目标

- 不修改 `src/mote_kernel/state/**`、State command、reducer、revision、execution token或 parent identity。
- 不修改 Store、persistence、checkpoint、definition version、deployment、failover或 rollback protocol。
- 不新增 state-only recovery、active-execution recovery或跨进程 frame recovery能力。
- 不重构现有 recovery worklist、coordinate、child disposition、traversal或 proof budget。
- 不新增 strict mode、feature flag、warning fallback、legacy compiler或 implicit compatibility path。
- 不新增 optional/latest-value、delayed feedback或按 node ID 读取“最近 publication”。
- 不给 callable增加 pure/side-effect metadata，不通过反射推断 side effect。
- 不修改 claim/session/resource/nested scheduling、commit callback或 continuation public shape。

## 4. Compiler 精确改造

### 4.1 Compile phase 顺序

每个 definition scope 的 `_compile_graph()` phase 固定为：

```text
validate definition/edge shape
    -> recursively compile nested definitions
    -> resolve value source/type/scope into local bindings/dependencies
    -> reject unknown/self source, boundary mismatch and ordinary data cycle
    -> build direct/conditional/join control indexes and activation_gates
    -> reject explicit START target with node-output dependency
    -> reject node-output consumer without incoming control edge
    -> derive automatic/explicit entries and check control reachability
    -> prove joint-route and guaranteed-before
    -> choose unique publication coordinates
    -> prove graph-output terminal guarantees
    -> assemble topology without data triggers
```

START entries继续由 `GraphDefinition.entries` canonical owner持有，不伪装成 ordinary `activation_gates`。

missing-control check 直接复用现有 `data_dependencies` 与 `activation_gates` locals，并写在 `_compile_graph()` 中；不增加 single-use helper 或新 record。

### 4.2 必须删除的 implicit lowering

从 `execution/graph/compiler.py` 一次删除：

1. data/direct same-pair conflict loop；
2. `data_targets` local map；
3. data dependency 对 `successors` 的注入；
4. data dependency 对 `reachability_successors` 的注入；
5. `DataTriggerPlan` import；
6. `FrontierTransitionPlan.data_triggers` assembly argument。

control reachability 只由 entries、direct targets、selected conditional targets与 completed join关系定义。data dependency 只参与 value proof，不使 node reachable。

### 4.3 必须删除的三条 dormant fallback

仅增加 missing-control precheck不够；以下 unreachable compatibility branches必须同时归零。

#### `_guaranteed_sets()`

删除 `data_dependencies` 参数和整段 fallback：

```python
elif data_dependencies[node_id]:
    guaranteed = set()
    for source in data_dependencies[node_id]:
        guaranteed.update(guarantees[source])
        guaranteed.add(source)
    alternatives.append(frozenset(guaranteed))
```

target guarantee只从 explicit entries与 `activation_gates` 传播。required data producers仍由后续：

```python
sources <= guarantees[target]
```

检查，不自行形成 activation alternative。

#### `_validate_joint_activation_paths()`

data requirement继续与每个已有 control alternative合并，用于证明 conditional routes可共同满足 required producers；删除“没有 control alternative时，data requirement单独形成 path”的 fallback：

```python
else:
    alternatives.append(data_requirement)
```

该 branch 即使已被 missing-control validation挡住，也不得作为 dormant legacy path保留。

#### `_input_publication_selection()`

删除 `data_dependencies` 参数和 implicit directly-causal fallback：

```python
or (
    not gates and data_dependencies[target] == {source.node_id}
)
```

relative selection只能由 `_all_single_source_gates(source.node_id, gates)` 的显式 control cause证明；acyclic unique absolute selection保持现有逻辑。

### 4.4 Guarantee 与 publication selection

目标行为：

```text
A -> B，B reads A                         -> legal
C -> B，B reads A，所有 C 路径经过 A       -> legal
C -> B，B reads A，C 可先于 A              -> guaranteed-before error
A -> B 或 C -> B，B 同时 reads A/C         -> 每个 alternative 都必须保证 A/C
join(A, C) -> B，B reads A/C               -> legal
```

publication coordinate保持：

- acyclic control topology优先产生唯一 absolute selection；
- direct/conditional self-loop等唯一 single-source gate可产生 relative selection；
- ambiguous join/control-loop publication继续 compile fail closed；
- runtime不增加 latest-value fallback。

### 4.5 Target compiled shape

删除：

```python
@dataclass(frozen=True, slots=True)
class DataTriggerPlan:
    targets: tuple[GraphNodeId, ...]
```

并从 `FrontierTransitionPlan` 删除：

```python
data_triggers: FrozenMap[GraphNodeId, DataTriggerPlan]
```

目标 shape：

```python
@dataclass(frozen=True, slots=True)
class FrontierTransitionPlan(Generic[GraphValueT]):
    entries: tuple[GraphNodeId, ...]
    direct_targets: FrozenMap[GraphNodeId, tuple[GraphNodeId, ...]]
    conditional_targets: FrozenMap[GraphNodeId, FrozenMap[GraphRouteId, GraphNodeId]]
    joins_by_source: FrozenMap[GraphNodeId, tuple[JoinEdge, ...]]
    materializations: FrozenMap[GraphNodeId, MaterializationPlan[GraphValueT]]
    publications: FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]
    graph_outputs: GraphOutputBindings[GraphValueT]
    resource_order: tuple[ResourceId, ...]
```

不得增加 `implicit_targets`、`publication_consumers`、`readiness_targets`、reverse binding index、empty compatibility field/property或第二 transition plan。

### 4.6 Compile failure atomicity

missing-control failure必须发生在：

- `_compiled_owner`/family identity安装之前；
- `StartGraphRun`/child start之前；
- commit callback、claim、resource admission之前；
- callable/nested execution之前；
- run-context frame安装之前。

首次 compile失败后 builder保持 mutable；调用方可以补充合法 control edge后再次 `run()`。不得通过提前 freeze 或残留 partial compiled family阻止修复。

## 5. Runtime 与既有 consumer 的机械收口

### 5.1 `execution/engine/routing.py`

从 `RoutingFacts` 删除：

```python
data_targets: tuple[RequiredTarget, ...]
```

删除 settled frontier publication scan：

```python
if isinstance(node.settlement, (SucceededGraphNode, SkippedGraphNode)) and frames.has_publication(...):
    data_targets.update(graph.transition.data_triggers[node.node_id].targets)
```

删除 `data_facts`、`ready_data` 及 completion condition中的 `facts.data_targets`。successor只来自现有 direct、selected conditional与 completed join control contributions：

```python
next_nodes = control_targets
if next_nodes:
    return AdvanceGraphFrontier(...)
if facts.remaining_join_progress:
    raise RoutingDeadlockError(...)
if facts.unavailable_graph_outputs:
    return AbortGraphRun(...)
return CompleteGraphFrontier(...)
```

`required(target)`、input availability、join progress与 graph output availability保持现有 owner和行为；它们只验证一个显式 target，不发现 target。

### 5.2 `execution/engine/resume_admission.py`

只机械删除两处 `facts.data_targets` 消费：

- unavailable required-target集合不再拼接 data targets；
- pure-skip completion判断不再检查 data targets。

skip/substitution、candidate collision、exact successor和 pre-commit availability行为保持不变。依赖 implicit activation的既有 test fixture补显式 control edge后全量回归，不新增 resume能力或新 contract。

### 5.3 `execution/engine/recovery.py`

只机械删除 `_resolve_quiescent()` 中一处 `facts.data_targets` 消费，使 recovery继续共享收窄后的 routing facts。

明确禁止在本 change unit 中：

- 新增/修改 State、availability coordinate、work item或boundary kind；
- 修改 traversal、branch expansion、proof budget或dedup identity；
- 新增 state-only/active-execution/failover语义；
- 把 publication重新解释为 activation source。

若删除 stale field read后 recovery需要上述任一改动，实施立即停止并重新评审。

### 5.4 其他 runtime owner保持不变

- ordinary success仍只在 exact settlement acknowledgement后安装 publication；
- failure/interrupt、callable exception、session drain/fence行为不变；
- materialization继续按 compiled binding与 exact activation coordinate取值；
- nested child completion仍投影现有 parent task result；parent downstream只由显式 parent topology决定；
- claim、scheduler、resource、commit、continuation与 result projection零语义改动。

## 6. 精确实施 manifest

### 6.1 Production：只允许五个文件

| 文件 | 必要改动 |
| --- | --- |
| `src/mote_kernel/execution/graph/compiler.py` | inline generic missing-control validation；删除 same-pair conflict、implicit lowering和三条 dormant fallback |
| `src/mote_kernel/execution/graph/topology.py` | 删除 `DataTriggerPlan` 与 `FrontierTransitionPlan.data_triggers` |
| `src/mote_kernel/execution/engine/routing.py` | 删除 publication-trigger collection、`RoutingFacts.data_targets`、ready-data merge并简化 completion |
| `src/mote_kernel/execution/engine/resume_admission.py` | 只删除两处 `facts.data_targets` 消费 |
| `src/mote_kernel/execution/engine/recovery.py` | 只删除一处 `facts.data_targets` 消费 |

禁止 production diff：

```text
src/mote_kernel/execution/errors.py
src/mote_kernel/execution/facade.py
src/mote_kernel/state/**
Store / persistence / checkpoint / failover / version / deployment owners
```

若实际 production 需要第六个文件，必须停止并重新核对 manifest；不能以“顺手清理”扩大范围。

### 6.2 Tests、examples 与 docs：按 actual inventory

允许修改：

- compiler contract：missing control、same-pair、START precedence、guarantee与 publication coordinate；
- public graph API：conditional/END不泄漏、compile前零副作用、失败后补 edge；
- resume admission：原 data-trigger fixture改成显式 control target并保持既有断言；
- topology/architecture：删除 compiled data-trigger field与 owner allowlist；
- active graph definitions：按业务意图补 direct/conditional/join；
- examples、双语 README和当前规范事实源：同步唯一目标语义；
- `pyproject.toml`：只按实际 complexity下降收紧 ratchet或修正移动后的 reviewed identity。

不允许为了本变更新增 versioned recovery、active execution recovery、fence/token/revision、deployment或failover tests。

### 6.3 Definition 迁移分类

对每个含 `Graph.node_output(P, ...)` 的 consumer C，必须先回答“哪个 control event 激活 C”：

| 业务意图 | 目标声明 |
| --- | --- |
| P成功后无条件运行 C | `add_edge(P, C)` |
| P选择某 route时运行 C | `add_conditional_edge(P, route, C)` |
| P/Q全部完成后运行 C | `add_join((P, Q), C)` |
| coordinator完成后运行 C，C读取更早 P | `add_edge(coordinator, C)` 并通过 guarantee proof |
| 只需把 P输出作为 graph result | `set_outputs()` 直接绑定 P，不创建虚假 consumer edge |

不得按每个 data producer机械生成 direct edge。尤其 multi-source required values通常需要 join，而不是多个 OR alternatives。

当前示例的预期迁移：

- `example/graph/linear_treasure_hunt.py`：增加 `decode -> locate -> open` direct edges；
- `example/graph/nested_space_mission.py`：增加 child `ignite -> orbit` 与 parent `launch -> probe` direct edges；
- `example/graph/parallel_detectives.py`：现有 join已显式激活 `deduce`，只更新必要说明；
- `example/graph/conditional_mood_radio.py`：现有 conditional gates已符合目标；
- `example/graph/human_in_the_loop.py`：单 automatic entry，不增加虚假 edge；
- `README` 的单 root normalize示例保持 automatic entry，但文字改为 binding不拥有 activation。

## 7. 聚焦测试矩阵

### 7.1 新增或改变的 contract evidence

| ID | 场景 | 预期 |
| --- | --- | --- |
| EACT-01 | B reads A，无 incoming control | `Graph.ValidationError`，消息含 canonical producer tuple |
| EACT-02 | B reads A且 direct A -> B | compile成功；compiled direct target只有一份 |
| EACT-03 | A route=go -> C，B reads A但无 gate | compile在任何 node/commit前失败，publication不能泄漏激活 |
| EACT-04 | A route=go -> B、stop -> END，B reads A | 只有 go route执行 B |
| EACT-05 | A -> END，B reads A但无 gate | compile失败，不再出现 END 后隐藏 consumer |
| EACT-06 | join(A, C) -> B且 B reads A/C | 复用现有 join/guaranteed-before proof并只激活一次 |
| EACT-07 | coordinator -> B，B reads A | guaranteed path成功；不保证 A时保持现有 compile error |
| EACT-08 | explicit START -> B且 B reads A | 保持 `an explicit START target cannot require a node output` 优先 |
| EACT-09 | compile失败后补合法 edge并重试 | builder未冻结，第二次 compile成功 |
| EACT-10 | `set_outputs()` 直接绑定 A publication | graph output projection不要求 consumer control edge |

### 7.2 零副作用断言

EACT-01、03、05、08 的 compile failure均断言：

- commit callback调用次数为 0；
- node callable调用次数为 0；
- child start、claim与resource acquisition为 0；
- `_compiled_owner` 未安装；
- builder可以继续补 edge。

### 7.3 既有回归而非新 contract

以下只迁移依赖 implicit activation 的 fixtures并运行现有 assertions，不扩张测试语义：

- resume、skip substitution与interrupt；
- nested graph；
- state-only recovery；
- claim/session/resource/parallelism；
- continuation、publication与 graph output；
- direct/conditional/join loops。

architecture tests保留 exact compiled-field与 owner gate，但不冻结 compiler local变量、helper名称、源码行号或 phase implementation细节。

## 8. Complexity 与结构 writeback

### 8.1 当前 baseline

评审记录的 production baseline：

```text
top_level_definitions: 503
type_definitions: 287
dataclass_types: 177
dataclass_fields: 499
decision_points: 1326
health: 51 reviewed / 0 unreviewed / 0 stale
```

### 8.2 预期结构变化

确定删除：

- 一个 `DataTriggerPlan` top-level dataclass/type；
- `DataTriggerPlan.targets` field；
- `FrontierTransitionPlan.data_triggers` field；
- `RoutingFacts.data_targets` field；
- compiler/routing中的 implicit branches和loops。

确定不新增：

- error type；
- helper、DTO、flag、index、property或compatibility alias；
- reviewed waiver。

missing-control inline validation会增加必要 decision point；最终 `decision_points` 只能按实际报告下调，不在方案阶段伪造精确净值。

### 8.3 Required writeback

production落地前后都运行：

```bash
make complexity-report
```

实施后：

1. 将 `pyproject.toml [tool.mote_kernel.complexity_ratchet]` 下调到 actual metrics；
2. 不提高任何 limit；
3. 不把本变更新产生的 smell登记为 reviewed；
4. 若既有 reviewed identity只因行号移动而变化，只同步其 exact identity；
5. 运行 health check确认 `0 unreviewed / 0 stale`。

本次实际 writeback（2026-08-26）已完成上述要求：结构指标为
`top_level_definitions=502`、`type_definitions=286`、`dataclass_types=176`、
`dataclass_fields=496`、`decision_points=1312`；所有 ratchet limit 均只下调、不提高，
health 为 `51 reviewed / 0 unreviewed / 0 stale`，没有新增 waiver 或 reviewed smell。

## 9. 原子实施顺序

以下是同一个不可拆分交付单元内部的工作顺序，不允许合并其中间态；本次已按顺序执行完成，实际结果见第 13 节。

### Phase 0：基线与 inventory

1. 保存 `git status` 与相关 diff，保护用户已有修改。
2. 运行 `make complexity-report`、targeted tests与当前全量门禁，记录 baseline。
3. inventory 所有 active `NodeOutputRef` consumers及其 incoming direct/conditional/join gates。
4. 按 direct、conditional、join、coordinator分类缺失 gate的 definitions；不盘点 persistence、version或 deployment。

### Phase 1：冻结目标 tests

1. 增加 EACT-01–10 中最小必要行为证据。
2. 将 data/direct duplicate case改为合法 compile。
3. 增加 explicit START precedence与 failed-compile mutable builder断言。
4. 更新 topology/owner architecture expected shape。
5. 确认 tests先因当前 implicit semantics失败，而不是因 fixture错误失败。

### Phase 2：原子 production 删除

1. 修改 compiler phase order并加入 inline `GraphValidationError`。
2. 删除 same-pair conflict、data-target lowering与三条 dormant fallback。
3. 删除 topology dataclass/field。
4. 删除 routing producer与 resume/recovery stale consumers。
5. source review确认五文件manifest和禁止路径零 diff。

### Phase 3：迁移 active definitions与规范

1. 按业务意图为 affected tests/examples补 control gates。
2. 更新双语 README和当前规范事实源。
3. 不修改历史 review为新事实源，不增加兼容说明或 deployment协议。

### Phase 4：complexity writeback与全量门禁

1. 运行 `make complexity-report`并按 actual下调 ratchet。
2. 修正必要的 existing reviewed identity，确认无新 waiver。
3. 运行 targeted tests、`make check`、monorepo pre-commit与 `git diff --check`。
4. 核对 actual changed-file manifest与 source-search归零结果。

## 10. 验收标准

### 10.1 行为

1. node-output consumer缺少 incoming control edge时 deterministic `Graph.ValidationError`。
2. data binding与 direct same-pair合法且只产生一个 control target。
3. conditional未选择 route时，publication不泄漏 consumer activation。
4. `A -> END` 不再被 hidden data consumer穿透；invalid definition在 A执行前失败。
5. join/coordinator继续复用现有 guaranteed-before与 publication-coordinate proof。
6. graph output projection不要求虚假 consumer control edge。
7. compile failure前零 State mutation、commit、claim、resource、child start与 node call。
8. automatic entry、natural completion及既有 resume/nested/recovery行为保持。

### 10.2 结构

active production/tests中以下名称归零：

```text
DataTriggerPlan
data_triggers
data_targets
implicit_targets
publication_consumers
trigger_on_data
```

历史、评审和当前实施文档中的删除说明不计入 production/active-test source query。

`FrontierTransitionPlan` exact fields只剩 entries、direct/conditional/join、materializations、publications、graph outputs与resource order。

### 10.3 架构

- `Graph` 仍是唯一 public facade，execution仍是唯一 runner；
- canonical definition与 derived lowering边界符合第 3.1 节；
- production diff严格限于第 6.1 节五个文件；
- State、errors、facade、Store/persistence/version/deployment owners零 diff；
- 无新 helper、DTO、type、flag、index、alias、fallback或 hidden mutable state；
- strict typing、module-scope imports、no-`Any`/bare internal mapping与 generic integrity门禁通过；
- complexity ratchet按 actual下降，health保持零未评审/零 stale。

### 10.4 Required gates

```bash
python -m pytest \
  tests/execution/graph/test_compiler_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_api.py \
  tests/architecture/test_graph_execution_ownership.py -q
make complexity-report
make check
cd .. && pre-commit run --all-files
git diff --check
```

绿色门禁不能替代 manifest、fallback deletion、phase precedence与 owner boundary的 source review。

## 11. 停止条件

出现任一情况立即停止实施并重新评审：

- `recovery.py` 除删除一处 `facts.data_targets` 外需要新算法或新状态；
- `resume_admission.py` 除删除两处 stale field consumption 外需要新 contract；
- production需要修改五文件manifest之外的路径；
- 需要新增 error/helper/DTO/flag/index/alias或compatibility branch；
- 需要修改 State、Store、persistence、definition version、deployment、failover或 rollback；
- 需要强制 explicit START/END或改变 natural completion；
- complexity出现新 unreviewed smell、ratchet需要提高或需要新增 reviewed waiver；
- current normative source、README、tests与 production无法在一个原子交付单元同步。

## 12. 最终实施裁决

本方案选择完整删除 implicit data activation，并复用现有 control infrastructure；该实施已完成并通过验收：

```text
canonical input binding
    = value source + exact type/readiness requirement

canonical direct/conditional/join declaration
    = activation eligibility

missing incoming control
    = existing GraphValidationError at compile time

derived compiler/runtime lowering
    = immutable proof/execution plan, not another declaration truth
```

目标不是增加 activation infrastructure，而是删除 `DataTriggerPlan`、runtime publication-trigger scan以及 compiler 中三条 dormant fallback。改造完成后，增加或删除 `Graph.node_output()` 只改变 value contract；增加或删除 edge/join只改变 execution topology，compiler、runtime和既有 recovery consumer对同一显式 graph definition得出一致结论。

## 13. Implementation-owner writeback（2026-08-26）

本节是 implementation owner 对批准实施版本的实际回写，只记录当前工作树中本 change unit 的结果，不扩大 production
范围，也不把历史 review、其他用户修改或未归属的示例文件变成第二事实源。

### 13.1 实际 manifest 与 owner 边界

production manifest 与批准的第 6.1 节完全一致：

```text
src/mote_kernel/execution/graph/compiler.py
src/mote_kernel/execution/graph/topology.py
src/mote_kernel/execution/engine/routing.py
src/mote_kernel/execution/engine/resume_admission.py
src/mote_kernel/execution/engine/recovery.py
```

同一实施单元同步的 supporting files 为 `pyproject.toml`、双语 README、当前 Graph node I/O 规范、批准测试清单中的
architecture/compiler/resume/runtime/continuation/public-API/recovery tests，以及本独立验收记录。`example/graph/` 在实施前已是
未跟踪目录，本次只执行验证，不将其归属到本 implementation manifest。

以下路径保持零 diff：

```text
src/mote_kernel/execution/errors.py
src/mote_kernel/execution/facade.py
src/mote_kernel/state/**
Store / persistence / checkpoint / version / deployment / failover owners
```

未创建 production commit；实际实现针对 Git `563a45124311f11e870d0627461102baeffdf7ad` 上的 working-tree diff，后续提交必须保持上述 manifest 边界。

### 13.2 实际语义与结构结果

- `NodeOutputRef` 只负责 value materialization；direct、conditional、join 只负责 activation eligibility；runtime、resume admission 与 recovery 只解释同一 `CompiledGraph.transition`。
- node-output consumer 没有 incoming explicit control edge 时，在任何 State mutation、commit callback、resource acquisition 或 node call 前以现有 `GraphValidationError` 失败。
- 同一 producer/consumer 的 value binding 与 direct edge 合法且只产生一个 control target；coordinator 间接路径、conditional、join 与 `set_outputs()` projection 均按批准语义工作。
- `DataTriggerPlan`、`data_triggers`、`data_targets`、`implicit_targets`、`publication_consumers`、`trigger_on_data` 在 active production/tests 中归零；没有 fallback、alias、第二 runner、持久化或 failover 语义。
- 结构结果为 `503→502` top-level definitions、`287→286` type definitions、`177→176` dataclass types、`499→496` dataclass fields、`1326→1312` decision points；complexity health 保持 `51 reviewed / 0 unreviewed / 0 stale`。

### 13.3 实际验证记录

```text
focused suite: 138 passed
five graph examples: verified (human-in-the-loop reaches awaiting resume)
make complexity-report: passed
make check: 850 passed, 100% coverage, Pyright 0 errors, build/twine passed
target-manifest scoped pre-commit: passed
git diff --check: passed
```

本次 implementation-owner writeback 已将原设计态状态关闭为 `IMPLEMENTED / VERIFIED / ACCEPTED FOR INTEGRATION`。完整逐项证据与
最终 monorepo hook 结果见 [实施验收记录](graph-explicit-activation-implementation-acceptance.zh-CN.md)。
