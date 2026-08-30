# S02 `GSP-A06` 单项实施设计评审

> **结论：核心技术方案在声明的 nominal 类型域内成立，没有发现 State、持久化、owner 或执行路径方面的技术阻碍；但本记录不替代 `GSP-A06` 批准，也不授权修改 production/tests。S02 仍保持 `PENDING REVIEW / NOT APPROVED`，必须先由 requirements owner 完成单项批准，再实施计划中的原子 change unit。**

## 1. 评审信息

- 评审日期：2026-08-23
- 评审对象：[主实施方案第 3.1.3 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#s02-gsp-a06)
- 交叉依据：当前 `src/mote_kernel/execution/graph/validation.py`、`tests/execution/graph/test_validation.py`、`test_join.py`、`test_nested_graph.py`、requirements 第 2–7 节及仓库 `AGENTS.md`
- 本文性质：独立 review record；不拥有 S02 target shape、requirements 批准状态、production shape 或测试 shape
- 本轮范围：只审核 S02 的语义等价性、唯一事实源、基础设计复用、零新增负债、no-State/no-persistence 边界和 evidence 完整性；不实施代码

## 2. 总体裁决

| 维度 | 裁决 | 依据 |
| --- | --- | --- |
| 当前不实现持久化 | **通过** | target 只涉及 validation local facts；不读取/修改 `GraphRunState`、command、reducer、protocol、commit callback 或 Store |
| 唯一真相与 owner | **通过** | `GraphDefinition.nodes` 仍是定义事实；`nodes_by_id` 只作为校验期间的 local lookup；validation 仍集中拥有 phase、错误和顺序 |
| 复用基础设计 | **通过** | 复用已有 `GraphNode[GraphValueT]`、`GraphValueT`、`NestedGraphNodeDefinition`、typed `dict` 与现有 edge variants，不新增 DTO/context/runner |
| node lookup 简化 | **通过（nominal domain）** | identity loop 同时建立 lookup，删除 `node_ids`/node `known`/`nested_ids` 的重复投影；nested definition 各自保留 local namespace |
| endpoint/错误优先级 | **通过设计** | dict membership、conditional nested-source check、entries/edge/resume/nested phase 顺序均可与当前实现逐项对齐 |
| join identity | **通过（nominal domain）** | `frozenset(edge.sources)` 与当前 sorted tuple 对 unordered duplicate 的等价类一致；source declaration/order 不写回 definition |
| 逻辑清晰、零新增负债 | **通过设计** | 不机械拆 variant helper，不新增 field/property/cache/第二 index/compatibility bridge；净删除账本可核对 |
| evidence 完整性 | **部分通过** | 89-case baseline 已复跑；四个 target behavior case 尚未存在，必须在 implementation unit 中补齐后才能闭合 A06 evidence |
| `GSP-A06` / 是否可执行 | **未批准** | requirements 仍把 S02 列为未批准 P2；本 review 不代替显式 approval |

## 3. 技术复核

### 3.1 `nodes_by_id` 的等价性和生命周期

当前实现先收集 `node_ids`，再执行 identity loop、duplicate check 和 `known` 构造，`_validate_edges()` 再扫描
`definition.nodes` 形成 `nested_ids`。目标在同一个 node identity loop 中把合法 node ID 映射到完整
`GraphNode[GraphValueT]`，之后用同一 map 完成 endpoint membership 和 nested nominal lookup。

该 map 不是新的 definition truth：

- `GraphDefinition.nodes` 继续拥有节点声明及其原始顺序；
- 每个 `_validate_definition()` definition scope 使用自己的 node namespace，nested scope 不共享或合并 map；
- map 不返回、不写入 `GraphDefinition`/`CompiledGraph`/State、不进入 runtime/recovery，也不跨 invocation 缓存；
- duplicate check 必须留在完整 identity loop 之后，避免把“后续非法 node identity 优先于 earlier duplicate”改成提前 duplicate error。

因此该替换符合“唯一真相 + invocation-local typed work index”，没有引入第二 owner。需要注意的是，账本中的“1 个 index/1 次 traversal”只能按单个 definition validation scope 解释，不能宣称整个递归 graph family 在任意时刻只有一个 map。

### 3.2 edge 顺序与错误 precedence

当前 `_validate_edges()` 已按 `definition.edges` 原始顺序单遍处理三个 nominal variants。目标没有把分支改成三次 grouped scan，也没有移动以下顺序：

```text
graph identity → version → definition collision/recursion
→ all node identity/reserved checks → duplicate node → resources
→ entries → edges → resume binding → nested recursion
```

在 edge loop 内，以下局部顺序必须保持：

1. direct/conditional endpoint 先检查 source/target membership，再做 duplicate 或 nested-source 判断；
2. conditional route identity 先于 endpoint 检查；unknown endpoint 与 nested source 同时存在时仍先抛 `UnknownNodeError`；
3. join 先检查 source 数量和重复，再检查 known source、target、self-target，最后才检查 unordered duplicate join；
4. edge declaration 的跨 variant 顺序由原始 tuple 决定，不按 variant 分类或排序。

目标中的 `edge.target != END and edge.target not in nodes_by_id` 与原 `(*known, END)` 在声明的
`GraphNodeId` nominal domain 内等价；`frozenset` 与 sorted tuple 也只在该 domain 内承诺等价。不得借此扩大到 forged/untyped Python object 的新公共行为。

### 3.3 join canonicalization

`JoinEdge.sources` 的声明类型是 `tuple[GraphNodeId, ...]`，其语义是无序 source identity、但仍需拒绝重复 source。
目标先构造一个 `frozenset`，用长度比较保留 duplicate-source 检查，再复用该值完成 known/self-target/duplicate-key 判断。
这删除了现有的 sort 和两次 `set(sources)` 组装；因为 `join_seen` 只用于 membership，source 集合的迭代顺序不向 compiled topology 或错误文本泄漏。

`conditional_seen` 当前已有实现使用 `tuple[GraphNodeId, str]`。这不是 S02 必须新增的行为面，且 `GraphRouteId` 在运行时是 `str` 的 `NewType`；本轮不把它升级为阻断项。若 implementation 同步收窄为已有 `GraphRouteId`，必须保持同一 source/route identity，不得借机新增 wrapper、route discriminator 或 public shape。

## 4. Evidence 复核

### 4.1 已复跑 baseline

按实施方案登记的 scoped 命令复跑：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/graph/test_validation.py \
  tests/execution/graph/test_join.py \
  tests/execution/graph/test_nested_graph.py \
  tests/execution/test_executor.py::test_nested_conditional_source_is_rejected_at_compile_time \
  tests/execution/test_executor.py::test_nested_invalid_completion_enters_error_draining \
  tests/architecture/test_generic_integrity.py \
  tests/architecture/test_source_discipline.py \
  tests/architecture/test_dependency_direction.py \
  tests/architecture/test_graph_execution_ownership.py
```

结果：`89 passed`（运行时间随环境变化，不作为契约）。这只证明当前 production baseline，不能冒充四个 target case 已实施。

当前源码单文件严格 Pyright 检查也通过：`pyright src/mote_kernel/execution/graph/validation.py` → `0 errors`。未对 production/tests 做候选 patch 或临时实现。

### 4.2 必须保留的 target cases

以下四个 planned case 的职责划分合理，且都应通过 public/compiled observable behavior 断言，不测试 private local/source shape：

| Case | 必须冻结的语义 |
| --- | --- |
| `tests/execution/graph/test_validation.py::test_validation_checks_all_node_identities_before_duplicate_nodes` | 完整 identity loop 后才报告 duplicate；后续非法 identity 仍优先 |
| `tests/execution/graph/test_validation.py::test_validation_preserves_edge_declaration_order_across_nominal_variants` | direct/conditional/join 混排时首个声明 edge 决定错误 |
| `tests/execution/graph/test_validation.py::test_conditional_endpoint_error_precedes_nested_source_error` | endpoint unknown 与 nested source 冲突时 endpoint error 优先 |
| `tests/execution/graph/test_nested_graph.py::test_nested_validation_preserves_definition_order_error_priority` | sibling nested definitions 按 `definition.nodes` 原始顺序递归 |

这四个 case 当前 `rg` 未找到，状态应保持 `TARGET — PENDING IMPLEMENTATION`；不能把 baseline 绿色写成 target 已闭合。

### 4.3 shape、manifest 和门禁

S02 不修改 dataclass/public shape，因此既有 topology immutability、generic、owner 和 dependency gates 足够作为 shape/tamper baseline；不应新增 S02-specific AST、legacy、private-source-shape 或 source-layout gate。

批准后的 planned manifest 三个文件范围合理：

```text
mote-kernel/src/mote_kernel/execution/graph/validation.py
mote-kernel/tests/execution/graph/test_validation.py
mote-kernel/tests/execution/graph/test_nested_graph.py
```

当前仍是 planned manifest，不是 actual changed-file manifest。任何 implementation diff 触及 State/protocol/Store/persistence、增加 helper/DTO/第二 index，或需要修改额外 normative owner，都必须停止并重新评审。

## 5. 需要明确的事项

本轮没有发现会否定 S02 方向的技术阻碍；但以下事项在批准/实施前必须保持明确：

1. `nodes_by_id` 的“一个”是每个 definition validation scope 一个，不是递归 family 全局一个；不得跨 nested scope 共享 node ID map。
2. `frozenset`/dict membership 的等价性只承诺严格声明类型域；不新增 forged object 的行为契约。
3. 四个 target case 尚未落地，A06 evidence 仍未闭合；implementation 后必须在同一原子 diff 中补齐并复跑适用 gates。
4. automated complexity gate 按当前用户/方案范围不作为 S02 gate，但结构净删除账本仍是“零新增负债”的设计证据；不能借排除 gate 保留旧 projection 或增加新抽象。

## 6. 最终状态

```text
S02 TECHNICAL DESIGN: NO BLOCKING ISSUE FOUND
S02 EVIDENCE: BASELINE PASS / TARGET CASES PENDING
S02 GSP-A06: PENDING REVIEW / NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
```

本 review record 不修改 requirements，不修改主实施方案，不修改 production/tests；不另写 response MD，因为本轮没有需要驳回的外部评审意见，避免产生第二个裁决事实源。

## 7. 本次 review change unit

本文件是本次独立 review audit 的唯一 actual changed-file：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s02-implementation-review.zh-CN.md
```

主实施方案中的 S02 target 仍由其第 3.1.3 节唯一拥有；requirements 的批准状态仍由 requirements 第 7 节唯一拥有。
