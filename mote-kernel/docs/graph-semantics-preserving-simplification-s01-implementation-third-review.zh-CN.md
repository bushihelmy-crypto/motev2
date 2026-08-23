# S01 `GSP-A06` 单项重新设计第三次评审

> **结论：评审通过。针对当前 target SHA256 `03fa8baa2d54aa0b9951c6d0663a6eb279eca3fc4be1471d48b0e4e19832a937`，第二次评审 R1–R3 已全部闭合，没有发现新的非 complexity 阻断。target 满足当前不实现持久化、唯一真相、复用基础设计、单一 compiler/execution owner、逻辑简单清晰、S01 自身零新增负债以及不新增或扩写 legacy/AST/private-source-shape 门禁测试的要求。按用户明确指示，本轮忽略 complexity gate、baseline、ratchet 及其当前运行状态，不使用这些内容批准或否决本 target。本评审只确认 target/evidence 已具备提交 requirements owner 明确批准的条件；它不代替批准，不授权修改 production/tests。**

## 1. 评审信息

- 评审日期：2026-08-23
- 评审对象：[主实施方案第 3.1.2 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23)
- target 文件 SHA256：`03fa8baa2d54aa0b9951c6d0663a6eb279eca3fc4be1471d48b0e4e19832a937`
- 第二次评审：[S01 单项重新设计第二次评审](graph-semantics-preserving-simplification-s01-implementation-second-review.zh-CN.md)
- 第二次评审 SHA256：`89a53a46fc4082966bc20bc206153c8a847b25e15f40e4891d5b237d38f28041`
- 整改回复：[S01 第二次评审回复](graph-semantics-preserving-simplification-s01-implementation-second-review-response.zh-CN.md)
- 整改回复 SHA256：`48bc23d122b22061becc0f6cf7c0c847dbba317e8764a43dfb6c626d84aa73b5`
- 本文性质：第三次 review record，只记录当前 target 的评审裁决和证据；不拥有 target shape、批准状态或 production shape

### 1.1 本轮范围

本轮按用户要求审核：

- 当前不实现持久化，State/Store/protocol/callback 边界保持不变；
- S01 target、编排 owner、compiler-local facts 和 compiled lowering 保持唯一；
- 复用现有 nominal/generic 类型、typed `dict` 工作索引和最终 `FrozenMap` representation；
- 删除重复事实和无语义转交，不新增 DTO/context/cache/runner/compatibility path；
- 行为 evidence 的 exact nodeid、断言目标、失败条件和 planned manifest 真实闭合；
- 新增或补强测试只验证可观察行为，不形成 legacy、AST 或 private-source-shape 门禁。

用户明确要求 complexity gate 先忽略，因此下列内容不参与本次批准或否决：

- complexity gate 当前运行状态；
- baseline/ratchet 是否已写回；
- `tests/architecture/complexity_rules.py`、`tests/architecture/test_complexity_gate.py` 及对应 Makefile hook 的治理裁决。

该排除不允许 S01 实现新增重复事实、额外 scan/freeze、低效 lookup、thin wrapper 或隐藏 mutable state。本文仍根据 exact
target、候选变换、behavior evidence 和一次性 source review 判断 S01 自身是否引入新债务，但不把 complexity gate
作为本轮阻断项或批准状态 owner。

## 2. 总体裁决

| 维度 | 裁决 | 说明 |
| --- | --- | --- |
| 当前不实现持久化 | **通过** | State、command、reducer、protocol、Store 和 callback 语义全部 `HARD KEEP` |
| target 文档唯一真相 | **通过** | target 只由主实施方案第 3.1.2 节拥有；旧 S01 文件只是迁移指针 |
| compiler/execution 唯一 owner | **通过** | `_compile_graph()` 保留 nested recursion、完整 phase/error order 和最终装配；不新增 runner |
| compiler-local facts 唯一 | **通过** | `activation_gates`、`direct_targets`、`gates_to_end` 分别拥有 route-aware、non-END direct 和 terminal facts |
| compiled lowering 唯一 | **通过** | `FrontierTransitionPlan` 继续由 runtime/recovery 共同消费，不新增 projection 或第二 selection truth |
| 基础设计复用 | **通过** | 工作索引保持 typed `dict`，`FrozenMap` 只用于最终 representation，descriptor/plan 类型原样复用 |
| 逻辑简单清晰 | **通过** | 只允许一个双 consumer predicate；零 phase wrapper、DTO、context bag、cache、额外 scan/sort/freeze |
| 行为 evidence | **通过** | R1 direct index、R2 NodeOutput negative/positive consumer、R3 exact-shape 能力边界均已真实闭合 |
| implementation manifest | **通过** | planned behavior 修改文件已穷尽登记；只复跑而不修改的 runtime evidence 不冒充 changed file |
| legacy/private-shape 门禁 | **通过** | 两个 planned case 和一个 planned assertion 均为可观察行为；不新增测试文件或 S01-specific AST gate |
| complexity gate | **本轮排除** | 按用户指示不参与本次裁决，不记录为通过或失败 |
| `GSP-A06` target review | **通过** | 可提交 requirements owner 明确批准；批准前仍不得修改 production/tests |

## 3. 第二次评审整改复核

### 3.1 R1：direct + conditional evidence 已闭合

当前 target 已把
`tests/execution/graph/test_compiler.py::test_compile_indexes_conditional_routes_and_joins`
登记为 `PLANNED ASSERTION`，明确要求在既有 conditional/join 断言之外增加：

```python
assert compiled.transition.direct_targets[GraphNodeId("a")] == (
    GraphNodeId("b"),
    GraphNodeId("c"),
)
```

该 case 的职责已经收窄为 direct/conditional/join edge lowering indexes 共存，不再冒充 source-only publication
predicate evidence。`tests/execution/graph/test_compiler.py` 也已加入 production + behavior implementation manifest。

source-only predicate 的正向语义由独立 planned case
`test_compiler_uses_relative_selection_for_same_source_conditional_routes` 证明：source 自环使 absolute coordinate
不成立，两个不同 conditional route 指向同一 bound target，最终 materialization 必须是 `RELATIVE/1`。两个 case
各自拥有一个可观察职责，没有测试 private helper 名称或调用结构。

**裁决：R1 CLOSED。**

### 3.2 R2：NodeOutput publication consumer evidence 已闭合

target 已删除不含 NodeOutput dependency 的 resume-override case，并登记以下真实 runtime 路径：

| Exact nodeid | 证明内容 |
| --- | --- |
| `tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication` | availability 根据 compiled NodeOutput binding/selection 查 publication；缺失时 unavailable |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection` | compiled binding 缺 selection 时以 `SnapshotMismatchError` fail closed |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication` | selection 存在但 publication 未确认时抛 `GraphValueUnavailableError` |
| `tests/execution/engine/test_runtime_boundaries.py::test_repeated_child_activations_isolate_parent_boundary_substitutions` | 两个不同 superstep 的 publication 由同一 compiled relative selection 分别解析为 `first`、`second` |

最后一个正向 case 在 superstep `2` 和 `5` 安装不同的 parent-boundary publication，分别执行 routing 和
`materialize_node_input()`，最终断言：

```python
assert materialized_values == ["first", "second"]
```

它能够识别固定 absolute coordinate、错误 relative offset 和跨 activation 串值。negative fail-closed cases 与这个
positive repeated-activation case 组合后，既证明 selection 不可缺失，也证明 `RELATIVE/1` coordinate 被正确消费。

该 runtime-boundaries 文件只作为已有 evidence 复跑，不在 implementation 中修改，因此不加入 changed-file manifest；
它的 exact nodeid 已加入 scoped checks。该处理符合 actual changed-file manifest 的定义。

**裁决：R2 CLOSED。**

### 3.3 R3：architecture assertion target 已闭合

当前 target 已把
`test_frontier_transition_plan_is_the_single_compiled_execution_lowering`
的断言目标收窄为：

- `FrontierTransitionPlan` exact dataclass fields；
- `CompiledGraph.transition` 是 direct annotated field；
- field/projection shape 不漂移。

文档明确写出该 case 不证明 consumer owner set。runtime consumption 由第 3.2 节 behavior cases 证明；没有新增
projection/consumer owner 则由 implementation actual diff 和一次性 source review 证明。consumer 文件列表、private
调用图和 source text 不写进永久 AST test，也不新增 S01-specific architecture test。

**裁决：R3 CLOSED。**

## 4. Target 架构复核

### 4.1 唯一事实

目标实现删除两份重复常驻事实：

- 删除 route identity 丢失后的 `control_gates`；proof 直接消费 canonical `ActivationGate`；
- 删除 `direct_pairs`；data/direct duplicate membership 直接读取 `direct_targets[source]`。

剩余事实各有唯一 owner：

| 事实 | 唯一 owner |
| --- | --- |
| route-aware non-terminal activation | `activation_gates` |
| non-END direct membership | `direct_targets` |
| terminal activation | `gates_to_end` 派生的 `terminal_gates` |
| source-only causal predicate | `_all_single_source_gates()` 按需派生，不缓存 |
| runtime/recovery lowering | `FrontierTransitionPlan` |
| canonical resource order | `transition.resource_order` |

没有 compatibility alias、forwarding property、双写、第二 source map、第二 lowering 或隐藏 mutable state。

### 4.2 唯一编排与错误顺序

`_compile_graph()` 继续直接拥有：

1. definition-order nested recursion；
2. node/output lookup 和 graph-input collection；
3. binding scan、nested boundary validation 和 data-cycle check；
4. definition-order edge lowering；
5. duplicate、entry、reachability、joint activation、guarantee 和 terminal/output proof；
6. descriptor/transition assembly；
7. resource canonicalization 与最终 map freeze。

不把 nested loop、binding scan、edge lowering、proof 或 final assembly 搬进 single-use phase wrapper、闭包、DTO 或
wide tuple。parent/child phase、sibling 顺序和第一个 compile error 仍只有一个 owner。

### 4.3 基础容器与冻结边界

`nested_graphs`、`node_outputs` 和 proof indexes 在 compiler invocation 内继续使用完整泛型 typed `dict`，保持近似
O(1) lookup。`FrozenMap` 只在构造最终 `FrontierTransitionPlan`/`CompiledGraph` 时使用，不形成 dict/frozen 双工作
索引，也不把 tuple-backed linear lookup 带入 binding/output proof。

该设计正确复用当前基础容器职责，没有为了“更 immutable”牺牲 lookup 复杂度。

### 4.4 新增面与零新增负债

唯一允许新增的 production function 是 `_all_single_source_gates()`。它同时被 controlled-producer proof 和
input-publication selection 使用，不是 thin single-use phase wrapper。对应删除单次使用的 `RouteCause` alias，并删除：

- `control_gates`；
- `direct_pairs`；
- `input_descriptor`；
- `output_descriptor`；
- `resource_order` 转交 alias。

禁止新增 DTO/dataclass/field/property/alias/cache/index/runner/store、phase helper、module import、full scan、sort 或
freeze。候选变换的一次性诊断继续得到 planned positive `PublicationSelection(kind=RELATIVE, superstep=1)`，且未发现
health identity 换入；该诊断只作为本次 source-review 佐证，不作为 complexity gate 裁决。

### 4.5 State 与持久化边界

S01 不读取或修改 State shape、command、reducer、revision、protocol、commit callback、memory installation、Store 或
persistence backend。concrete input/output/publication/continuation 继续由 execution-owned frame/continuation 持有，
不进入 State、Graph 实例、全局 cache 或第二 store。

因此该 target 不实现持久化，也不隐含 durability、checkpoint、journal、exactly-once 或进程重启恢复承诺。

## 5. Behavior 与 manifest 闭合

### 5.1 Planned behavior 只有三处

production implementation 同步落地：

1. `test_compiler_uses_relative_selection_for_same_source_conditional_routes`；
2. `test_nested_compilation_preserves_definition_order_error_priority`；
3. `test_compile_indexes_conditional_routes_and_joins` 的 direct-index assertion。

它们全部位于现有 behavior test 文件，验证 public/compiled observable behavior，不新增测试文件，不读取 private helper，
不冻结局部变量、helper 数量、scan 次数、AST 表达式或源码布局。

### 5.2 非 complexity 范围的 changed-file manifest

当前 manifest 已覆盖全部 production 与 behavior 修改：

```text
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/graph/test_nested_graph.py
```

已有 runtime、generic、source-discipline 和 topology owner tests 只复跑、不修改，因此不应冒充 changed files。
`pyproject.toml` 与 complexity owner 按用户指示不在本轮裁决中处理。

### 5.3 批准与实施顺序

正确顺序保持为：

1. 当前第三次评审确认 target/evidence 通过；
2. requirements owner 在用户明确批准后单独把 S01 `GSP-A06` 记为 satisfied；
3. production + behavior implementation 按 manifest 原子落地；
4. scoped checks 和适用的完整工程检查通过；
5. target owner 单独 writeback actual diff、source review、manifest 和 gate 结果。

当前第三次评审不执行第 2 步，也不授权提前执行第 3 步。

## 6. 本轮复核证据

### 6.1 Existing exact nodeids

本轮复跑行为、runtime 与既有 owner/type/import evidence，共计：

```text
19 passed in 0.55s
```

覆盖：

- unknown source 与 direct/data duplicate error；
- data-only `RELATIVE/1` baseline；
- direct/conditional/join lowering baseline；
- multi-source join、mutually-exclusive route、partial join fail closed；
- terminal output positive/negative；
- NodeOutput availability/materialization negative paths；
- repeated activation relative-coordinate positive path；
- nested error propagation 与 resource ordering；
- compiled lowering exact fields、generic integrity、module-header import 和 single-owner baseline。

### 6.2 Exact candidate diagnostic

对当前文档规定的 exact compiler candidate 做一次性内存变换并执行 planned same-source multi-route positive case，结果：

```text
PublicationSelection(kind=<PublicationSelectionKind.RELATIVE: 2>, superstep=1)
```

同时确认 candidate 不新增 health identity。按用户指示，本次不运行或裁决 complexity gate，不把 complexity baseline
状态写成通过或失败。

## 7. 最终裁决与剩余动作

当前 SHA `03fa8baa…` 在本轮定义的非 complexity 范围内没有剩余设计或 evidence blocker，第三次评审结论为：

```text
S01 GSP-A06 TARGET REVIEW: PASS
REQUIREMENTS APPROVAL: PENDING
PRODUCTION IMPLEMENTATION: NOT AUTHORIZED
```

下一步只能由 requirements owner 在获得用户明确批准后更新批准状态。批准前：

- 不得修改 production/tests；
- 不得把 planned case 冒记为已落地；
- 不得新增 compatibility bridge、第二 execution path、State/Store/persistence 代码；
- 不得新增或扩写 legacy、AST、private-source-shape 或 source-layout gate；
- 不得由 review/response 文档代替 requirements 的批准状态。

本评审通过不改变 complexity gate 被用户明确排除出本轮裁决的事实，也不对该独立治理项作结论。
