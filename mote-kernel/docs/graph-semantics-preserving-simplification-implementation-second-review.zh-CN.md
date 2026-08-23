# Graph 执行代码语义保持型简化实施方案第二次复审

> **结论：仍不通过，不能授权 Phase 1。** 修订稿已关闭上一轮大部分问题，但 routing 最小模型、S12 recovery 不变量、原子迁移清单和规范同步时序仍未闭合。

## 1. 复审信息

- 复审日期：2026-08-19
- 代码基线：`feat/kernel-graph-node-io-contract@7944159`
- 复审对象：[修订后的实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 参考回复：[第一次复审回复](graph-semantics-preserving-simplification-implementation-review-response.zh-CN.md)
- 范围：只审查文档和现有实现，不修改 production code 或 tests
- 裁决：**不通过；修正本轮阻断项后再审**

## 2. 已关闭的问题

修订稿有实质进展，以下整改可以接受：

1. 不再把 S01–S23 全部称为可直接实施项；23 个历史 ID 与 24 个原子单元的计数闭合；
2. S02、S15 保留为未获准实施的 P2，并把 validation/recovery 主循环 owner、错误顺序和 budget 留在原 owner；
3. S08 明确 `joins_by_source` 仍是唯一 compiled truth，不再增加 stored join index；
4. S17 同时删除 `skip_actions` 与 `has_pure_skip`，由 command 和 substitutions 推导；
5. S23 已拆为 sentinel 删除与 result-view 扫描合并两个原子单元；
6. producer/consumer 改为同一原子变更迁移，不使用 alias、forwarding compatibility 或临时双写；
7. 相关测试基线已经修正为 398，full suite 基线为 817；
8. generic integrity、连续模块头 import、dependency direction、typing-negative、coverage、build/package、pre-commit 和 whitespace gate 已显式列出。

这些改进足以关闭第一次复审的 B2–B7 原始表述，但新目标 shape 仍有以下缺口。

## 3. 阻断问题

### R1. S09 删除 wrapper 后仍留下两个等价 routing 入口

修订稿规定 [`project_routing_facts()` 和 `plan_routing()` 都返回 `ResolutionCommand`](graph-semantics-preserving-simplification-implementation.zh-CN.md#L119)，却没有处置现有
[`resolve_routing()`](../src/mote_kernel/execution/engine/routing.py#L366)。

按该方案实施后会形成：

```text
project_routing_facts(state, facts) -> ResolutionCommand
plan_routing(graph, state, scope, frames) -> ResolutionCommand
resolve_routing(graph, state, scope, frames) -> ResolutionCommand
```

其中后两个签名和结果完全相同，一个只能转发另一个。这违反唯一执行路径和“无 forwarding alias”原则，也与 architecture gate 当前认定
[`resolve_routing` 是唯一 runtime symbol owner](../tests/architecture/test_graph_execution_ownership.py#L171)
不一致。

S09 必须固定为一个闭合目标：

1. 保留 `resolve_routing_facts(...) -> RoutingFacts`；
2. 保留一个 facts → command 的 private typed projection；
3. 保留唯一组合入口 `resolve_routing(...) -> ResolutionCommand`；
4. 删除 `RoutingResolution` 和 `plan_routing()`，迁移测试；
5. 不得以测试兼容为由保留同签名 wrapper。

### R2. S10/S11 仍保存可由 canonical facts 精确推导的布尔字段

当前 [`RequiredTarget`](../src/mote_kernel/execution/engine/routing.py#L54) 同时保存：

```text
inputs_available
unavailable_inputs
```

其构造恒满足 `inputs_available == not unavailable_inputs`。修订稿 S11 仍要求一次扫描同时“产生 available、missing”，没有删除该重复变量。

当前 [`RoutingFacts`](../src/mote_kernel/execution/engine/routing.py#L62) 又同时保存：

```text
completion_output_available
completion_output_history_missing
unavailable_graph_outputs
```

按现有 resolver 构造，`completion_output_history_missing` 恒等于
`not completion_output_available`；而 completion availability 又可由“是否存在 target/join progress”与
`unavailable_graph_outputs` 精确推导。S10 只把扫描合并为一次，却继续保存多个派生结果，不满足唯一真相和变量最简原则。

最小目标应改为：

- `RequiredTarget` 只保存 `node_id`、`historical_inputs_missing`、`unavailable_inputs`；availability 在消费处由空 tuple 推导；
- `RoutingFacts` 只保存四组 target/progress facts 与 `unavailable_graph_outputs`；completion availability/history 在唯一 command/recovery projection 中推导；
- 不增加 property、cached bool 或第二诊断 DTO；
- 若保留某个布尔字段，必须证明它不能由剩余 canonical fields 推导。

这会修改 skip-output normative 文档冻结的 exact model：
[`skip-failed-output-implementation.zh-CN.md`](skip-failed-output-implementation.zh-CN.md#L217)。
因此第 8 节的同步清单必须加入 S10/S11；不能只更新 production 和测试。

### R3. S12 的 proof-local constant 证明不足以保持完整 structural equality

第一次复审回复正确证明了：同一次 `_prove_scope()` 内，`family.admitted_actions` 对所有 transfer state 固定。因此删除 `resume_input_availability` 不会单独改变该次局部 `seen` 集合中的两两区分。

但这只证明了 **proof-local constancy**，尚未证明该字段能由剩余 semantic basis 唯一恢复：

- `preflight_recovery()` 返回的 `RecoveryTransferState` 仍可跨 seed/invocation 比较；
- normative source 明确要求 admitted-action 的全部 semantic fields 参与 equality/hash；
- 当前 `preflight_recovery()` 没有校验 action 中的 resume coordinate 与 `seed.frames`/availability 一致；
- 删除字段后，原本 coordinate 不同的 malformed facts 会变成相等，和“字段删除必须覆盖 malformed/tamper 且 fail closed”的门禁冲突。

此外，删除 `AdmittedResumeFact[GraphValueT]` 的唯一泛型字段后，不只 `AdmittedResumeFact` 会成为 phantom generic；
[`_RecoveryFamily`](../src/mote_kernel/execution/engine/recovery.py#L355)
也会失去唯一承载 `GraphValueT` 的字段。修订稿只明确删除前者的泛型，没有把 `_RecoveryFamily` 改为非泛型写入目标 shape。

S12 在下列内容闭合前不能列为 P1：

1. 定义并证明每个非 skip admitted action 与 availability 中 exact resume coordinate 的一一对应不变量；
2. 说明 malformed seed 是保持现有行为、先独立 fail closed，还是退出本轮“语义保持型”范围；
3. 保持 recovery 不直接读取 `materializations` 的 dependency/owner gate；
4. 明确 `AdmittedResumeFact` 和 `_RecoveryFamily` 均改为非泛型，`RecoveryTransferState`/seed 仅因自身 availability/frames 继续泛型；
5. 同步 Node I/O normative equality/shape，并提供删除前后的 valid-domain equality 证明。

在完成以上设计前，S12 应降为 P2，而不是带“证明失败即停止”条件的 P1。

### R4. S03–S05 的原子 consumer 清单不完整

修订稿要求每个单元在同一最小变更迁移全部 consumer，但表格中的位置不足以执行该要求：

- S03 未列 `graph/compiler.py` producer 和使用 callable classification 的 `engine/admission.py`；
- S04 只列 ports/topology/compiler，却遗漏直接消费 `outcomes`/`publications` 的 scheduler、frontier、recovery、executor、routing、resume-input、resume-admission、invocation、family-driver 等模块；
- S05 未列读取 `graph.graph_inputs` 的 `engine/admission.py`，也没有明确 nested compiler consumer 的迁移点。

S04 的目标 shape 本身也应写成 exact form：

```text
FrontierTransitionPlan.publications:
    FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]
```

并明确同时删除：

- `FrontierTransitionPlan.outcomes`；
- `CompiledGraph.outcomes` forwarding projection；
- `OutcomeAdmissionPlan`、`PublicationPlan`；
- `MaterializationPlan.node_id`；
- 所有 `.publications[node_id].descriptor` 和 `.outcomes[node_id]` consumer。

仅写“迁移全部 consumer”而不给出可核对清单，容易在泛型、导包和 exact-shape tests 中漏改，不能满足原子实施门禁。

### R5. Phase 0 与结论对 normative 文档的更新时间互相冲突

原子步骤要求 production、normative 文档和 exact-shape tests 在同一变更同步，且禁止 docs-only 长期中间态；这一点正确。

但当前结论又要求先完成“requirements、**normative shape 修订**和本文再次评审”，之后才开始 P1：
[`实施方案第 11 节`](graph-semantics-preserving-simplification-implementation.zh-CN.md#L350)。
这会让 normative source 在 production 尚未变化时提前描述未来 shape，与第 6、8 节的原子同步规则冲突。

Phase 0 只能新增：

- 语义保持型重构 requirements；
- target-shape proposal/迁移账本；
- characterization tests 或测试计划。

现行 normative source 必须继续描述现行代码；它的正式 shape 修订应与对应 production 单元在同一原子变更完成。第 11 节需删除“Phase 1 前先修订 normative shape”的要求。

## 4. 非阻断但必须收紧

### N1. S08 不需要新增 helper

routing 已有 [`_declared_joins()`](../src/mote_kernel/execution/engine/routing.py#L97)。S08 应明确复用并按需要收窄/重命名该函数，最多只增加 snapshot-guard 的 module-scope import；“最多新增一个纯投影函数”仍给实现留下重复 helper 的空间。

### N2. 新文件可能绕过当前 whitespace/pre-commit 命令

当前新建的 simplification 文档（包括本文）均为 untracked。`git diff --check` 和 `pre-commit run --all-files` 默认都不会覆盖未跟踪文件。因此门禁需补充以下二选一：

1. 新文件进入 index 后再运行既有 gate；或
2. 对未跟踪文件显式运行 `pre-commit run --files ...`，并用 `git diff --no-index --check /dev/null <file>` 检查 whitespace。

否则“门禁通过”不能证明新 requirements/review 文档本身通过门禁。

## 5. 修订后的准入条件

下一版满足以下条件后，可以只对 P1 重新做一次准入评审：

1. S09 删除重复的 `plan_routing`/`resolve_routing` 入口之一，固定唯一 command path；
2. S10/S11 删除可推导布尔，并同步 skip-output normative exact model；
3. S12 降为 P2，或补齐 action ↔ availability 不变量、malformed 行为和完整泛型迁移；
4. 为 S03–S05 列出完整 producer/consumer/import/test 迁移清单，补全 S04 exact target type；
5. Phase 0 只写 proposal/requirements，不提前改写现行 normative truth；
6. S08 明确复用现有 helper，不新增同义函数；
7. 新增文件被纳入真实 pre-commit/whitespace gate。

## 6. 本次验证

本轮以静态审查为主，遵循“避免重复运行大套件”的要求：

| 检查 | 结果 |
| --- | --- |
| 实施方案列出的相关 pytest 路径 | `398 tests collected`，计数正确；未重复执行 |
| 既有 coverage 数据 | `100`；只读取报告，未重跑 suite |
| Ruff | 本轮启动的 `make check` 中已通过 |
| 完整 `make check` | 本轮在 Pyright 阶段被会话中断，未作为新的通过证据 |
| 后台测试进程 | 无残留 |
| `git diff --check` | tracked diff 通过，但不覆盖当前 untracked 文档 |

文档记录的 `817 passed`、Pyright 0 errors、build/twine/pre-commit baseline 本轮未重复执行；第二次复审不以未重跑为失败，只是不把它们记作本轮新证据。

## 7. 最终裁决

修订稿已从“范围未经裁决”推进到“多数单元方向可接受”，但 R1–R5 仍会分别造成重复 routing path、派生变量双存、recovery equality/泛型缺口、原子迁移漏项和规范事实源时序冲突。

**第二次复审仍不通过，不授权 Phase 1。** 修正第 5 节七项准入条件后再审；其余已经关闭的整改无需回退或重复论证。
