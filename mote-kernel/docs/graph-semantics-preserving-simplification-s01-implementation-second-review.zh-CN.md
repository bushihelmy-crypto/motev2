# S01 `GSP-A06` 单项重新设计第二次评审

> **结论：暂不批准。S01 的 target 架构已经满足当前不实现持久化、唯一真相、复用基础设计、单一编排 owner、逻辑清晰和不新增 legacy/private-shape 门禁的要求；剩余阻断只在 `GSP-A06` 行为 evidence 的真实性与 implementation manifest 闭合性。当前 direct + conditional case 没有断言 direct index，runtime publication evidence 引用了不消费 NodeOutput publication selection 的用例，unique-lowering architecture case 也被写成了它没有证明的 consumer-owner gate。按用户最新指示，本轮忽略 complexity gate、baseline、ratchet 及其当前运行状态，不以它们批准或否决 S01。**

## 1. 评审信息

- 评审日期：2026-08-23
- 评审对象：[主实施方案第 3.1.2 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23)
- 对象文件 SHA256：`64293398e053752076721919a0c906368430e723abfb6012407608b5358c4d18`
- 前次记录：[S01 单项实施方案审查](graph-semantics-preserving-simplification-s01-implementation-review.zh-CN.md)
- 本文性质：review record，只记录本轮裁决、证据和整改条件；不拥有 target shape、批准状态或 production shape
- 用户目标：当前不实现持久化；S01 自身零新增负债；target、事实和 compiled lowering 保持唯一；复用现有 execution/compiler 基础设计；最终逻辑简单清晰；不得新增或扩写 legacy、AST、private-source-shape 门禁测试

### 1.1 本轮明确排除项

用户明确要求本轮先忽略 complexity gate。因此下列内容不参与本次批准或否决，也不在本文形成整改裁决：

- `tests/architecture/complexity_rules.py`；
- `tests/architecture/test_complexity_gate.py`；
- `pyproject.toml` complexity baseline/ratchet；
- `Makefile` complexity hook；
- complexity gate 当前是否通过。

该范围裁决只表示本轮不审，不表示本文批准、否定或接管这些独立变更。S01 的重复事实、额外 scan/freeze、lookup
复杂度、helper/DTO/cache 新增面仍通过 exact target、actual diff、一次性 source review 和行为证据验收；不得借“忽略
complexity gate”放宽 S01 的零新增负债要求。

## 2. 总体裁决

| 维度 | 裁决 | 说明 |
| --- | --- | --- |
| 当前不实现持久化 | **通过** | State、command、reducer、protocol、Store 和 callback 语义均为 `HARD KEEP` |
| target 文档唯一真相 | **通过** | target 只由主实施方案第 3.1.2 节拥有；旧 S01 文件已退化为迁移指针 |
| execution/compiler 唯一 owner | **通过** | `_compile_graph()` 保留 nested recursion、完整 phase/error order 和最终装配 |
| compiled lowering 唯一真相 | **通过** | `FrontierTransitionPlan` 保持 runtime/recovery 共用的唯一 lowering，不新增 projection 或 runner |
| 基础设计复用 | **通过** | 继续使用 `ActivationGate`、typed `dict`、既有 descriptor/plan 类型和最终 `FrozenMap` 边界 |
| 逻辑清晰与零新增负债方向 | **通过** | 不新增 phase wrapper、DTO、context bag、cache、兼容层、额外 scan/sort/freeze 或第二 predicate |
| 行为 evidence | **不通过** | 两个 nodeid 与所声明行为不匹配，一个 architecture nodeid 被过度解释 |
| implementation manifest | **不通过** | 若按正确 owner 补 direct-index 行为断言，manifest 必须包含 `test_compiler.py` |
| legacy/private-shape 门禁 | **通过** | planned 新测试均为可观察行为测试；禁止把本轮 private/local shape 写入永久测试 |
| `GSP-A06` 准入 | **暂不批准** | R1–R3 闭合并由 requirements owner 明确批准前不得修改 production |

这不是要求回到“小改即可”的方案。当前 target shape 已经是正确的最终结构；应保留它，并把证据改成与真实执行路径
一一对应。不能通过弱化断言、扩大 architecture AST gate 或把文件遗漏解释为“scoped checks 会覆盖”来绕过闭合条件。

## 3. 已确认成立的 target

### 3.1 唯一事实与编排

以下整改方向成立，后续不得因 evidence 修正而重新开放：

1. `_compile_graph()` 是 graph-family compiler、nested definition-order recursion 和完整错误顺序的唯一 owner；
2. `activation_gates` 是 route-aware non-terminal activation 的唯一 compiler-local truth；
3. 删除 route 丢失后的常驻 `control_gates`，source-only 判断只按需从 `ActivationGate` 派生；
4. `direct_targets` 是 non-END direct membership 的唯一事实，删除重复的 `direct_pairs`；
5. `gates_to_end`/`terminal_gates` 继续独立拥有 terminal control，不把 `direct_targets` 错当 END owner；
6. `FrontierTransitionPlan` 继续是 runtime/recovery 共享的唯一 compiled lowering；
7. nested/output/proof 工作索引继续使用完整泛型 typed `dict`，`FrozenMap` 只用于最终 compiled representation；
8. descriptor assembly 直接复用既有 `_frame_descriptor()`、resolved declarations 和 `MaterializationPlan`；
9. 不新增 Store、repository、journal、checkpoint、persistence port、runner 或第二 execution path。

### 3.2 唯一允许的新函数

`_all_single_source_gates()` 同时服务 controlled-producer proof 和 input-publication selection，具有两个真实 production
consumer。它只读取 exact `ActivationGate`，不缓存、不排序、不构造第二 source map，也不接管 phase ordering。
删除单次使用的 `RouteCause` alias 并保留 `ActivationGate` 的 route identity，方向正确。

### 3.3 测试边界

本单元允许新增或补强的测试只有 observable compiler behavior：

- nested sibling definition-order error priority；
- same-source multi-route 的 `RELATIVE/1` publication selection；
- direct/conditional/join compiled indexes；
- runtime 对已有 publication selection 的消费。

不得测试 `_all_single_source_gates()` 名称、private 参数、局部变量、helper 数量、loop/comprehension、扫描次数、源码文本
或 import 布局。private/local 删除只写入一次性交付证据，不形成 repository legacy gate。

## 4. 阻断问题

### R1：direct + conditional evidence 没有证明 direct index

主方案行为矩阵声明：

```text
tests/execution/graph/test_compiler.py::test_compile_indexes_conditional_routes_and_joins
-> 同一 source/target 的 direct 与 conditional gate 共存且 compiled indexes 不变
```

该用例确实构造了：

```text
a --direct--> b
a --left----> b
a --direct--> c
a --right---> c
b + c ------> d
```

但现有断言只读取 `conditional_targets` 和 `joins_by_source`，没有读取 `direct_targets`。因此删除、遗漏或错误 canonicalize
direct index 时，该测试仍可能绿色，不能支持“compiled indexes 不变”的完整声明。

该用例也没有 NodeOutput data binding，因此不会进入 controlled-producer proof 或
`_input_publication_selection()`；它不应被描述为 `_all_single_source_gates()` 的 behavior characterization。

**目标整改：**

1. 在这个既有行为测试中增加对 `compiled.transition.direct_targets[GraphNodeId("a")]` 的 exact 断言；
2. 保留 conditional route 与 join index 断言，使一个用例完整证明同一 source/target 的 lowering 共存；
3. 把 `tests/execution/graph/test_compiler.py` 加入 production + behavior implementation manifest；
4. 文档明确该 case 只证明 edge lowering/index，不声称命中 source-only publication predicate；
5. 不新增测试文件，不测试 private helper 或源码形状。

source-only predicate 的核心 positive 继续由 planned same-source conditional routes case 证明：source 自环、两个 route
指向同一 bound target，并断言 target materialization 为 `PublicationSelectionKind.RELATIVE`、`superstep == 1`。这种职责
拆分与真实可达路径一致，也复用了现有 compiler behavior test owner。

### R2：runtime publication evidence 引用了错误路径

主方案当前引用：

```text
tests/execution/engine/test_resume_input_contract.py::
test_pending_input_availability_accepts_state_and_acknowledged_overrides
```

该用例调用默认 `compiled_graph()`，只有 source 的 graph-input binding，没有 NodeOutput data dependency。它读取
materialization descriptor 并验证 State-owned/acknowledged resume-input override，但不消费 NodeOutput
`PublicationSelection`。因此它可以证明 resume-input materialization path，不能证明主方案所写的
“compiled materialization/publication selection”。

已有行为测试已经拥有所需 runtime 证据，无需新建或扩写 architecture/legacy gate：

| Exact nodeid | 实际证明 |
| --- | --- |
| `tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication` | availability consumer 按 compiled selection 查找 publication；缺失时返回 unavailable |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection` | materialization 对缺失 selection 的 compiled plan fail closed |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication` | selection 存在但对应 publication 未确认时抛 `GraphValueUnavailableError` |

**目标整改：**把 P06 evidence 拆成“availability consumer”和“materialization consumer”，引用上述真实命中的 nodeid，
分别写清 assertion target 与 failure condition。原 override case 可以继续作为既有 scoped regression 运行，但不得再承担
NodeOutput publication-selection 证明。

### R3：unique-lowering architecture case 被过度解释

`test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 当前只通过 AST 读取
`FrontierTransitionPlan` 与 `CompiledGraph` 的 dataclass fields，并验证 `transition` 是 direct field。它没有扫描
materialization/publication consumers，也没有形成 consumer owner set。

主方案却把它写成：

```text
FrontierTransitionPlan field/consumer owner set 原样通过
```

该 assertion target 超出测试实际能力。若据此批准，会把“测试名称像 owner gate”误当成“测试证明了全部 owner”。

**目标整改：**

- 把该行收窄为“`FrontierTransitionPlan`/`CompiledGraph.transition` exact field shape 保持”；
- unique runtime consumption 由 R2 的既有 runtime behavior cases 和 actual source review 共同证明；
- 不扩写该 AST test 去冻结 consumer 文件列表、private 调用图或 source text；
- 不新增 S01-specific architecture test。

## 5. 最满足目标的整改闭环

按下列顺序闭合，不能以缩小文字承诺代替真实证据：

1. 保留第 3.1.2 节现有 target shape、唯一 owner、no-State/no-persistence 和 typed-dict/final-freeze 设计；
2. 修正 direct + conditional row，并在现有 `test_compiler.py` 中补 direct-index observable assertion；
3. 把 `test_compiler.py` 加入 implementation changed-file manifest；
4. 用现有 availability/materialization behavior cases 替换错误的 P06 citation；
5. 收窄 unique-lowering architecture case 的 assertion target，不扩大 AST gate；
6. production 落地时同步两个 planned behavior cases；
7. actual diff 必须确认 `control_gates`、`direct_pairs`、`RouteCause` 和三个无语义转交 alias 归零，且没有新
   DTO/context/cache/scan/freeze/runner/store；
8. requirements owner 最后单独记录 S01 的明确批准状态；review record 不得代替批准。

整改后的 behavior implementation 仍只能修改现有测试文件。禁止新增测试文件、compatibility layer、legacy gate、
private-source AST assertion 或 State/persistence 代码。

## 6. 本轮复核证据

本轮精确复跑：

```text
tests/execution/graph/test_compiler.py::test_compile_indexes_conditional_routes_and_joins
tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication
tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection
tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication
tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering
```

结果：`5 passed in 0.24s`。

绿色结果证明这些现有用例当前成立，但不能补足 R1 中缺失的 direct-index assertion，也不能让错误引用自动变成正确
evidence。complexity gate 按用户指示未纳入本轮裁决。

## 7. 最终批准条件

只有同时满足以下条件，S01 才可提交 requirements owner 批准：

- R1–R3 全部闭合；
- planned behavior nodeid、断言目标和失败条件与实际测试完全一致；
- implementation manifest 包含所有实际修改的 production/behavior 文件；
- 不新增或扩写 legacy、private-source-shape、AST consumer-list 或 source-layout 门禁；
- State、Store、protocol、commit callback 和持久化边界零修改；
- `_compile_graph()`、`FrontierTransitionPlan` 与 execution engine 的 owner 关系保持唯一；
- 实际实现没有新增 DTO、context、cache、兼容层、额外 scan/freeze 或第二 execution path；
- scoped behavior、Ruff、strict Pyright、`make check` 中本轮适用的非排除项以及 monorepo-root pre-commit 均通过，或精确记录非 S01 阻断。

在这些条件闭合前，S01 保持 `PENDING REVIEW / NOT APPROVED`，不得修改 production，也不得更新 requirements 中
`GSP-A06` 的批准状态。
