# S01 `GSP-A06` 单项重新设计第二次评审回复

> **结论：接受 R1–R3，并已回写 S01 唯一 target；部分不接受第二次评审的总体裁决口径。direct-index evidence、NodeOutput publication evidence、architecture assertion target 和 implementation manifest 均已修正。接受“本轮不使用当前 complexity gate 运行状态裁决这三项 evidence”，但不接受由此推导“净复杂度不再参与 `GSP-A06`”或“剩余阻断只有 R1–R3”；requirements 与 target 中既有的净复杂度义务和 baseline 前置仍有效。R2 的三个负向用例也不足以单独证明正确的 relative coordinate 消费，因此 target 额外登记了现有 repeated-activation 正向行为用例。S01 继续保持 `PENDING REVIEW / NOT APPROVED`，本回复不修改 production、tests、requirements 或批准状态。**

## 1. 回复信息

- 回复日期：2026-08-23
- 回复对象：[S01 第二次评审](graph-semantics-preserving-simplification-s01-implementation-second-review.zh-CN.md)
- 评审文件 SHA256：`89a53a46fc4082966bc20bc206153c8a847b25e15f40e4891d5b237d38f28041`
- target owner：[主实施方案第 3.1.2 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23)
- 批准状态 owner：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 本文性质：review response，只记录接受/不接受裁决、理由和本次 owner writeback；不拥有 target、批准状态或 production shape

复核时，第二次评审登记的 target SHA256
`64293398e053752076721919a0c906368430e723abfb6012407608b5358c4d18` 与回写前主实施方案完全一致，
因此本回复处理的是同一份 target，不存在评审对象过期问题。

## 2. 逐项裁决

| 评审项 | 回复 | 回写结果 |
| --- | --- | --- |
| R1 direct + conditional case 未断言 direct index | **接受** | 既有 case 登记为 `PLANNED ASSERTION`；implementation 必须补 exact `direct_targets` 断言，并把 `test_compiler.py` 加入 manifest |
| R2 runtime publication evidence 引用错误路径 | **接受并补强** | 删除不含 NodeOutput dependency 的 override case 引用；改为真实 availability/materialization cases，并增加 repeated-activation 正向 relative-coordinate evidence |
| R3 architecture case 被过度解释 | **接受** | assertion target 收窄为 `FrontierTransitionPlan`/`CompiledGraph.transition` exact field shape；consumer 唯一性改由 behavior + actual source review 证明 |
| complexity gate 当前运行状态不参与本轮 R1–R3 判断 | **有限接受** | 可以不使用 dirty-worktree ratchet failure 判断 R1–R3 是否真实；不把未审项记为通过 |
| complexity/baseline/ratchet 不参与 S01 批准，剩余阻断只有 R1–R3 | **不接受** | `GSP-A06` 的净复杂度证据和 target 的 baseline 前置保持；review 无权在不回写 owner 的情况下取消 |
| R2 所列三个负向用例已完整证明 publication selection 消费 | **不接受其充分性** | 三个 case 证明 selection 必须存在且 missing publication fail closed；另用正向 repeated-activation case 证明 relative coordinate 真正被消费 |

## 3. 已接受并回写的整改

### 3.1 R1：edge-lowering evidence 与 manifest

`test_compile_indexes_conditional_routes_and_joins` 当前图形确实包含：

```text
a --direct--> b
a --left----> b
a --direct--> c
a --right---> c
b + c ------> d
```

但回写前只断言 conditional routes 和 joins。target 现在要求 implementation 在同一既有 observable behavior case 中
增加：

```python
assert compiled.transition.direct_targets[GraphNodeId("a")] == (
    GraphNodeId("b"),
    GraphNodeId("c"),
)
```

该 case 只证明 direct/conditional/join lowering indexes 共存，不声称命中 `_all_single_source_gates()`。source-only
publication predicate 仍由 planned same-source multi-route case 独立证明。因为 implementation 会修改既有 behavior
test，planned manifest 已加入：

```text
mote-kernel/tests/execution/graph/test_compiler.py
```

不新增测试文件，不测试 private helper 或源码形状。

### 3.2 R2：真实 NodeOutput consumer evidence

原引用的 `test_pending_input_availability_accepts_state_and_acknowledged_overrides` 只覆盖 graph input/resume override，
不承担 NodeOutput publication-selection 证明。target 已换成以下实际路径：

| Exact nodeid | 精确证明 |
| --- | --- |
| `tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication` | availability 对 compiled NodeOutput binding 形成 publication lookup，缺失时 unavailable |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection` | compiled binding 缺 selection 时以 `SnapshotMismatchError` fail closed |
| `tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication` | selection 存在但 publication 未确认时抛 `GraphValueUnavailableError` |

第二次评审对上述三个 case 的选择方向正确，但它们都是 missing/malformed 路径：即使 relative coordinate 解析错误，
空 frame lookup 仍可能得到同样失败。因此 target 额外登记：

```text
tests/execution/engine/test_runtime_boundaries.py::
test_repeated_child_activations_isolate_parent_boundary_substitutions
```

该现有 case 的 consumer materialization 使用 compiler 生成的 `RELATIVE/1` selection，在两个不同 superstep 安装并读取
两个不同 publication，最后得到 `["first", "second"]`。它能拒绝固定 absolute coordinate、跨 activation 串值或第二
selection truth。该文件只作为既有 evidence 运行，当前 planned implementation 不修改它，因此不加入 changed-file
manifest；其 exact nodeid 已加入 scoped checks。

### 3.3 R3：architecture gate 能力边界

`test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 只验证：

- `FrontierTransitionPlan` exact dataclass fields；
- `CompiledGraph.transition` 是 direct annotated field；
- `CompiledGraph` 不含 forwarding property/method body。

target 已删除“consumer owner set 原样通过”的过度声明。runtime 对 materialization/publication selection 的消费由第 3.2
节 behavior cases 证明；S01 没有新增 projection/consumer owner 由 implementation actual diff 和一次性
`rg 'transition\.(materializations|publications)'` source review 证明。该结果只写入 owner writeback，不扩写 AST gate，
不新增 S01-specific architecture test。

## 4. 不接受的口径

### 4.1 “本轮不审”不能等同于“批准条件已取消”

接受第二次评审在 R1–R3 的真实性审查中不使用当前 dirty-worktree complexity gate 结果；这些 evidence 问题可以独立
判断。但不接受以下推导：

```text
忽略当前 complexity gate/baseline/ratchet
  -> complexity 不参与 S01 批准
  -> R1–R3 是唯一剩余阻断
```

原因是两个现行 owner 尚未改变：

1. requirements 的 `GSP-A06` 明确要求“净复杂度证据”；
2. S01 target 明确要求独立 complexity owner 先把 actual baseline 向下锁定，并规定 baseline 未锁定时 S01 不进入
   批准/实现。

review record 只拥有裁决证据，不能静默覆盖 requirements 或 target。正确口径应是：

```text
当前 ratchet 运行状态：本轮不审，不据此批准或否决 R1–R3
净复杂度义务：仍由 GSP-A06/target 拥有，不能记为已通过或已取消
R1–R3：是本轮确认的 additional blockers，不是无条件的全部剩余条件
```

若用户以后明确要改变 complexity 准入顺序，应先由 requirements/target owner 原子回写，再据新 owner 文本评审；
不能只在 review 中形成例外。

### 4.2 负向 lookup evidence 不能独立证明 coordinate 正确

selection missing、publication missing 和 empty availability 能证明 runtime 没有完全绕过 compiled binding，但不能单独
区分 `RELATIVE/1`、错误的 absolute coordinate 或错误的 anchor superstep。故不接受“R2 所列三个 case 已完整闭合
runtime selection semantics”的隐含结论；第 3.2 节的 existing positive repeated-activation case 是必要补强。

## 5. 本次 owner writeback

本次只修改 target/evidence 文档，不修改 production、tests、requirements、State、protocol、Store 或批准状态。
本 change unit 的 actual manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-s01-implementation-second-review-response.zh-CN.md
```

第二次 review record 原样保留，只提供本回复的审计输入，不成为 target owner。旧 S01 路径继续只是迁移指针。

## 6. 当前状态

成立的 R1–R3 已全部吸收，且 R2 已用正向 runtime evidence 补强。以下边界不变：

- 当前不实现持久化，不修改 State/command/reducer/protocol/Store/callback 语义；
- `_compile_graph()`、`FrontierTransitionPlan` 和 execution engine 保持各自唯一 owner；
- 不新增 phase helper、DTO、context bag、cache、compatibility layer、额外 scan/freeze 或第二 execution path；
- 不新增或扩写 legacy、private-source-shape、AST consumer-list 或 source-layout gate；
- requirements owner 未明确批准前，不修改 production/tests，不更新 S01 `GSP-A06` 状态。

因此本回复后的 S01 状态仍为：**`PENDING REVIEW / NOT APPROVED`**。
