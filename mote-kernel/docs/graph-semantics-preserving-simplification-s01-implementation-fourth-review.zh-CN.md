# S01 `GSP-A06` 单项重新设计第四次评审

> **结论：在用户明确要求无视 complexity gate 及其 baseline/ratchet/配置写回要求后，当前 target SHA256 `f95e883c746a34ab42dddf28b23d0ef97512c1934d7c2517b80e432ae500c28b` 的受审范围通过。第二次评审 R1–R3 仍全部闭合；没有发现新的非 complexity 设计或 evidence 阻断。target 满足当前不实现持久化、唯一真相、复用基础设计、单一 compiler/execution owner、逻辑简单清晰、S01 自身零新增结构负债，以及不新增或扩写 legacy/AST/private-source-shape 门禁测试的要求。第三次评审回复 SHA256 `0136f3d7b83eea53c5afb05f5aab0c5e1d02dfc1be2c2d3d0a825bffa70a3fea` 存在 R4–R5 两项裁决错误：它重新使用已排除的 complexity 要求否决本轮准入，并误述第三次评审的四文件非 complexity 清单。complexity 相关内容在本轮是 `DISREGARDED / NOT APPLICABLE`，不能形成通过、失败、整改项或实施前置。S01 的 requirements 批准仍待用户明确给出；本评审不代替批准，不授权修改 production/tests。**

## 1. 评审信息

- 评审日期：2026-08-23
- 当前 target：[主实施方案第 3.1.2 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23)
- 当前 target SHA256：`f95e883c746a34ab42dddf28b23d0ef97512c1934d7c2517b80e432ae500c28b`
- 第三次评审：[S01 单项重新设计第三次评审](graph-semantics-preserving-simplification-s01-implementation-third-review.zh-CN.md)
- 第三次评审 SHA256：`d082f07426564c2c1edce3ca492991df9d38c4d931ea87262888e78ddf6058dd`
- 第三次评审回复：[S01 第三次评审回复](graph-semantics-preserving-simplification-s01-implementation-third-review-response.zh-CN.md)
- 第三次评审回复 SHA256：`0136f3d7b83eea53c5afb05f5aab0c5e1d02dfc1be2c2d3d0a825bffa70a3fea`
- target shape 唯一 owner：主实施方案第 3.1.2 节
- 批准状态唯一 owner：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 本文性质：第四次 review record；只记录当前 target 与第三次评审回复的裁决，不拥有 target shape、requirements 批准状态或 production shape

## 2. 本轮范围

### 2.1 实质审核范围

本轮只按以下目标判断 S01 是否可接受：

- 当前不实现持久化，不改变 State、command、reducer、protocol、commit callback 或 memory-installation 语义；
- target、compiler facts、compiled lowering 与 execution engine 均保持唯一 owner；
- 复用现有 nominal/generic 类型、invocation-local typed `dict` 和最终 `FrozenMap` representation；
- 删除重复事实与无语义转交，不新增 DTO、context、cache、runner、第二 scan/index 或 compatibility path；
- 保持 nested recursion、phase/error priority、route/join/publication 和 resource ordering 行为；
- 只使用 public/compiled observable behavior 与既有 owner/type/import evidence；
- 不新增测试文件，不新增或扩写 legacy、AST、private-source-shape、consumer-list 或 source-layout 门禁测试。

### 2.2 明确排除的 complexity 要求

根据用户最新明确指令，下列内容全部不参与本次裁决：

- complexity gate 的当前或历史运行状态；
- metric snapshot、baseline、ratchet、health identity 与 limit 写回；
- `tests/architecture/complexity_rules.py`、`tests/architecture/test_complexity_gate.py`；
- `pyproject.toml` 中仅服务 complexity ratchet 的 limit；
- Makefile `complexity-ratchet` 与 monorepo pre-commit `kernel-complexity` hook；
- target 中把上述内容规定为 review、requirements approval 或 implementation 前置的条款。

这些内容在本轮统一记为：

```text
COMPLEXITY GATE REQUIREMENT: DISREGARDED / NOT APPLICABLE
```

因此本评审既不验证、引用或复述其数值，也不允许它们形成通过、失败、整改项、准入前置或否决理由。“零新增负债”仍按
target 的 exact shape、删除/新增面、唯一 owner、lookup/scan/freeze 边界和 behavior evidence 作实质判断，不依赖该门禁。

## 3. Findings

### R4 — 第三次评审回复越过范围，重新用 complexity 要求否决准入（高）

第三次评审已在第 1.1 节明确排除 complexity gate、baseline、ratchet 和对应治理裁决，并在结论中只把
requirements approval 保留为 `PENDING`。第三次评审回复却：

- 在第 4 节用 `GSP-A06` 的净复杂度义务和 baseline 前置否决 `TARGET REVIEW: PASS`；
- 在第 5 节重新运行并引用 complexity snapshot 与 `1 failed, 6 passed`；
- 在第 8 节把 `COMPLEXITY BASELINE PRECONDITION: NOT CLOSED` 写入当前状态，并据此规定后续顺序。

这不是对受审事实能力边界的限定，而是把用户明确排除的要求重新变成了准入否决条件。其 R1–R3、no-persistence、唯一
owner、基础设计复用和 19 个既有 nodeid 的接受结论仍成立；以下部分在本轮不成立：

```text
以 complexity gate、baseline、ratchet 或 limit 状态否决 target review
以 complexity baseline owner 完成为 requirements approval 或 implementation 前置
以 complexity 当前运行结果描述 S01 的通过/失败状态
```

**整改裁决：**本第四次评审直接纠正并取代第三次评审回复中上述范围外裁决，不修改历史回复原文。complexity 要求不能
阻断本轮 target 通过。requirements 仍为 `PENDING` 的唯一原因是用户尚未明确批准 S01，不能偷换成 complexity failure。

### R5 — 第三次评审回复误述四文件非 complexity manifest（中）

第三次评审第 5.2 节的标题就是“非 complexity 范围的 changed-file manifest”，并明确写出：

```text
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/graph/test_nested_graph.py
```

同节还明确说明 `pyproject.toml` 与 complexity owner 不在该轮裁决中。它没有宣称用四文件清单替换 target 的五文件完整
planned manifest。第三次评审回复第 2、6 节把不存在的“替换”主张列为不接受项，事实不成立。

正确边界是：

- 上述四个文件完整覆盖本轮受审的 production + behavior 修改面；
- runtime、generic、source-discipline 与 owner evidence 文件只复跑、不修改，不进入 changed-file manifest；
- target 当前登记的第五个文件 `pyproject.toml` 只服务被用户排除的 complexity ratchet，本轮不审核、不要求、也不据此
  否决四文件清单；
- 本评审不修改 target manifest，也不把 review record 变成 manifest owner。

**整改裁决：**第三次评审的四文件清单在其明示范围内成立；第三次评审回复对它的“不接受”裁决不成立。本第四次评审
记录该纠正，不篡改历史评审或回复。

## 4. 当前 target 复核

| 维度 | 裁决 | 依据 |
| --- | --- | --- |
| 当前不实现持久化 | **通过** | State/command/reducer/protocol/Store/callback 全部 `HARD KEEP`；manifest 明确排除 State、protocol 与 persistence 实现 |
| target 唯一真相 | **通过** | 具体 target 只存在于主实施方案第 3.1.2 节；旧 S01 文件只保留迁移指针；requirements 只拥有批准状态 |
| compiler/execution 唯一 owner | **通过** | `_compile_graph()` 保留 nested recursion、phase/error order 与最终装配；`FrontierTransitionPlan` 保持唯一 compiled lowering |
| compiler-local facts 唯一 | **通过** | 删除 `control_gates` 与 `direct_pairs`；分别复用 canonical `activation_gates` 与 `direct_targets`，不建立第二投影 |
| 基础设计复用 | **通过** | `nested_graphs`、`node_outputs` 和 proof indexes 继续使用 typed `dict`；`FrozenMap` 只在最终 representation 边界形成 |
| 逻辑简单清晰 | **通过** | 只允许一个有两个 production consumer 的窄 predicate；不拆 single-use phase helper，不引入 DTO/context/cache/wide tuple |
| S01 自身零新增结构负债 | **通过** | 一个共享 predicate 替换两份判断，同时删除单次 alias、两个重复 facts/index 和三个转交 alias；新增面上限明确且无 compatibility bridge |
| 错误与顺序保持 | **通过** | nested definition order、binding/edge scan、first-error phase、route-aware joint proof 与 resource first-seen order 均有明确 owner 和停止条件 |
| R1 direct-index evidence | **DESIGN CLOSED / IMPLEMENTATION PLANNED** | exact public compiled-index assertion 与修改文件已登记；未冒记为当前已实现 |
| R2 NodeOutput consumer evidence | **CLOSED** | 三个 fail-closed runtime case 与 repeated-activation 正向 case 共同覆盖 compiled selection 的真实消费 |
| R3 architecture evidence | **CLOSED** | 既有 assertion 只证明 exact lowering field shape；consumer owner 由 behavior + actual diff/source review 闭合，不扩大 AST gate |
| planned behavior | **通过设计，待实现** | 两个新 behavior case 与一个既有 case assertion 职责互斥且验证可观察行为；当前状态明确为 `PLANNED` |
| legacy/private-shape 门禁 | **通过** | 不新增测试文件，不测试 private helper 名称、局部变量、调用次数、AST、scan 次数或源码布局 |
| 非 complexity manifest | **通过** | 四个 production/behavior changed files 穷尽覆盖本轮修改面；只复跑的 evidence 文件不冒充 changed file |
| complexity gate/baseline/ratchet | **不适用** | 按用户明确指令无视，不参与裁决 |

未发现 R1–R3 之外新的 target 设计或 evidence 缺口。

## 5. Evidence 复跑

本轮按当前 target 登记的 exact existing nodeid 复跑，没有运行 complexity gate：

```text
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_value_source_from_an_unknown_node
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_duplicate_data_and_direct_control_pair
tests/execution/graph/test_compiler_contract.py::test_compiler_uses_relative_selection_for_loop_producer_data_trigger
tests/execution/graph/test_compiler.py::test_compile_indexes_conditional_routes_and_joins
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_ambiguous_loop_publication_for_join_consumer
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_join_between_mutually_exclusive_routes
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_join_that_can_receive_only_one_source_on_a_route
tests/execution/graph/test_compiler_contract.py::test_join_to_end_is_one_terminal_gate_for_output_guarantees
tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_output_not_guaranteed_on_every_terminal_branch
tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication
tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection
tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication
tests/execution/engine/test_runtime_boundaries.py::test_repeated_child_activations_isolate_parent_boundary_substitutions
tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering
tests/execution/graph/test_nested_graph.py::test_invalid_deeply_nested_graph_fails_root_compilation
tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order
tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types
tests/architecture/test_source_discipline.py::test_imports_form_a_contiguous_module_header
tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners
```

结果：

```text
19 passed in 0.55s
```

该结果证明当前 production baseline 与既有 evidence 引用一致。两个新 case 和一个 direct-index assertion 仍准确标记为
`PLANNED`，本评审没有把它们冒记为已落地。

## 6. 最终状态

```text
R1–R3 / REVIEWED TARGET DESIGN AND EVIDENCE: PASS
S01 TARGET REVIEW UNDER USER-AUTHORIZED SCOPE: PASS
THIRD-REVIEW RESPONSE R4–R5: OVERRIDDEN BY FOURTH REVIEW
COMPLEXITY GATE REQUIREMENT: DISREGARDED / NOT APPLICABLE
REQUIREMENTS APPROVAL: PENDING
PRODUCTION IMPLEMENTATION: NOT AUTHORIZED
```

本评审通过只表示当前 target 已满足用户指定的设计与 evidence 要求。它不构成 S01 的显式批准，也不允许提前修改
production/tests。只有用户明确批准后，requirements owner 才能单独更新 S01 状态；review/response 均不得代替该 owner。

## 7. 本次 change unit 与检查边界

本第四次评审的 actual manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s01-implementation-fourth-review.zh-CN.md
```

本次没有修改主实施方案、requirements、第三次评审、第三次评审回复、production 或 tests，也没有新增任何 legacy、AST、
private-source-shape 或 complexity gate 测试。

本次不运行完整 `make check` 或 monorepo-root `pre-commit run --all-files`：当前 `make check` 无条件依赖被用户排除的
`complexity-ratchet`，monorepo pre-commit 也包含同一 `kernel-complexity` hook。该排除不冒充完整门禁通过；本轮可复现
证据仅为上述 19 个既有 nodeid 与本文件的 whitespace/diff 检查。
