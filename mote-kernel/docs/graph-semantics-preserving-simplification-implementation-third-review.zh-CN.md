# Graph 执行代码语义保持型简化实施方案第三次复审

> **结论：仍不通过，不能授权 Phase 1。** 第二次复审的主要设计问题已经关闭，但 S06 仍遗漏五个 `CompiledGraph` forwarding projections，Phase 0 文档 owner 尚未闭合，新增文件门禁也没有覆盖未来产物。

## 1. 复审信息

- 复审日期：2026-08-19
- 代码基线：`feat/kernel-graph-node-io-contract@7944159`
- 复审对象：[再次修订后的实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 前序结论：[第二次复审](graph-semantics-preserving-simplification-implementation-second-review.zh-CN.md)
- 范围：只审查文档、现有 production shape、consumer 和门禁，不修改 production code 或 tests
- 原则：零增复杂度、唯一事实源、复用现有 owner、完整门禁、泛型关系不擦除、导包位于连续模块头、变量不保存可推导事实、逻辑保持最短闭环

## 2. 已关闭的问题

第二次复审的七项整改中，前六项已经实质闭合：

1. S09 固定为 `resolve_routing_facts()`、一个 facts → command projection 和唯一组合 runtime 入口 `resolve_routing()`；目标明确删除 `RoutingResolution` 与 `plan_routing()`；
2. S10/S11 固定了最小 `RequiredTarget`/`RoutingFacts`，删除可从 canonical facts 推导的三个布尔字段，并加入 skip-output normative 同步要求；
3. S12 已降为 P2，并补齐 valid-domain equality、malformed seed、action ↔ availability、`AdmittedResumeFact`/`_RecoveryFamily` 去泛型和 architecture gate 前置条件；
4. S03–S05 已列出 producer、consumer、imports、direct tests 和 exact target shape；当前代码交叉搜索没有发现这三项的新漏项；
5. Phase 0 已明确不得提前把现行 normative source 改写成未来 production shape；
6. S08 已回到复用 routing 现有 join projection，不再增加 compiled index、cache、第二 representation 或同义 helper。

计数也保持闭合：S01–S22、S23A、S23B 共 24 个原子单元，其中 15 个 P1、9 个 P2。扩充 S06 的删除面不会增加新实施单元，因此不需要改动该计数。

## 3. 阻断问题

### R1. S06 只删除 `transition` 转发，却保留另外五个 forwarding projections

实施方案全局禁止 `forwarding property`，并认定 `FrontierTransitionPlan` 是唯一 compiled execution lowering。可是当前 S06 只要求删除
[`CompiledGraph.transition`](../src/mote_kernel/execution/graph/topology.py#L90) property 和 `RecoveryAvailabilityPlan`，没有处置同一 class 上的以下投影：

```text
CompiledGraph.entries          -> transition.entries
CompiledGraph.materializations -> transition.materializations
CompiledGraph.publications     -> transition.publications
CompiledGraph.graph_outputs    -> transition.graph_outputs
CompiledGraph.resource_order   -> transition.resource_order
```

`CompiledGraph.outcomes` 已由 S04 删除，但其余五个若保留，最终 shape 仍同时允许 `graph.x` 与 `graph.transition.x` 两条内部访问路径。这与第 2.3 节和第 6 节的“无 forwarding property”约束直接冲突，也会让 S04 所称的 `FrontierTransitionPlan.publications` owner 继续存在平行入口。

S06 必须改成一个闭合的 topology owner 迁移：

1. `CompiledGraph` 直接保存唯一字段 `transition: FrontierTransitionPlan[GraphValueT]`；
2. 删除 `RecoveryAvailabilityPlan`；
3. `CompiledGraph` 当前定义的 forwarding properties 最终全部消失：`outcomes` 由先执行的 S04 删除，`transition` property 由 S06 替换成 direct field，`entries`、`materializations`、`publications`、`graph_outputs`、`resource_order` 由 S06 删除；
4. 所有 remaining consumer 统一读取 `graph.transition.*`，不得用新 alias、property、局部 wrapper 或双写过渡；
5. S04 的最终描述应明确写成 `graph.transition.publications[node_id]`，避免继续暗示 `graph.publications` 是合法入口。

按当前 Phase 2 顺序，在 S04/S05 完成后，S06 至少还需迁移以下位置：

| projection | production consumers | direct tests |
| --- | --- | --- |
| `entries` | `execution/graph_run.py` | `tests/execution/graph/test_compiler.py` |
| `materializations` | `engine/resume_input.py`、`engine/routing.py`、`executor.py`、`invocation.py` | `tests/execution/engine/test_resume_input_contract.py`、`tests/execution/graph/test_compiler_contract.py` |
| `publications` | `engine/admission.py`、`engine/recovery.py`、`engine/resume_admission.py`、`engine/resume_input.py`、`engine/routing.py`、`executor.py`、`family_driver.py`、`invocation.py` | `tests/execution/engine/test_recovery_identity.py`、`tests/execution/engine/test_resume_admission.py`、`tests/execution/engine/test_runtime_boundaries.py` |
| `graph_outputs` | `engine/admission.py`、`engine/routing.py`、`graph/compiler.py` | `tests/execution/engine/test_output_projection.py`、`tests/execution/graph/test_compiler_contract.py` |
| `resource_order` | `engine/admission.py`、`engine/snapshot_guard.py` | `tests/execution/graph/test_compiler.py` |

此外，`tests/architecture/test_graph_execution_ownership.py` 必须同时冻结 `CompiledGraph` 的 exact fields，并断言不再定义这些 forwarding properties；现稿仅为 S03–S05 给出完整迁移清单，S06 仍只有 `topology.py`、`compiler.py` 两个位置，无法支撑原子实施。

### R2. 新增文件门禁仍使用不闭合的固定四文件集合

第 7.3 节选择“先 `git add` 四个文件，再运行 `--all-files`”，第 11 节却写成“四个新增文档通过显式 `pre-commit --files`”。这不是同一策略，并且当前四个文件仍全部未跟踪。

更关键的是，Phase 0 还要求新增 requirements、proposal、迁移账本和 characterization 计划。无论这些内容最终落为一个还是多个文件，都不在固定四文件 `git add` 清单中；本次第三次复审文件也已使“四个新增文档”成为过期计数。因此当前命令只能证明一个历史快照，不能证明每个原子变更的完整 changed-file set。

门禁必须只保留一种可复现策略，并以“本原子单元的完整新增/修改文件清单”为输入，而不是硬编码四个旧路径。最小方案是：

1. 不把修改 index 作为门禁前置条件；
2. 从 monorepo root 对本单元全部 exact repo-relative paths 执行 `pre-commit run --files ...`；
3. tracked changes 同时运行 `git diff --check` 与 `git diff --cached --check`，但不为门禁主动修改 index；每个 untracked path 运行 no-index whitespace 检查并正确解释“存在内容差异”的退出码；
4. Phase 0 文件、后续 normative 修改、production、tests 和每次新增 review 都必须进入同一个逐单元文件清单；
5. 第 7.3 节和第 11 节使用完全一致的命令名、路径基准与覆盖口径。

若仍选择 staging 路径，也必须列出当次所有 exact 新文件并运行 `git diff --cached --check`；不能只 stage 当前四份文档。两种路径不得在同一准入结论中混用。

### R3. Phase 0 的文档 owner 和实际产物尚未闭合

第 6 节要求新增 requirements、target-shape proposal、producer/consumer 迁移账本和 characterization 计划，并再次评审“本文及 proposal/requirements”；第 8 节却只明确要求新增 requirements，而本文本身已经拥有 target shape、迁移账本和测试计划。当前工作区也不存在对应 requirements/proposal 文件。

若再建立一份包含相同 target shape 和迁移账本的 proposal，会与本文形成第二实施事实源；若这些词只是指本文中的章节，则“新增 proposal”和门禁文件清单又不准确。

Phase 0 必须先固定文档分工和 exact paths。按最小设计，建议：

- 新 requirements 只拥有不可变行为、停止条件和准入条件；
- 本实施方案继续唯一拥有 target shape、原子迁移账本、实施顺序和 characterization 计划，不再复制第二份 proposal；
- Node I/O/skip-output normative 文档继续拥有当前 production shape，直到对应 production 原子变更与其同步提交；
- review/response 只记录裁决与整改，不成为行为或 target-shape owner；
- docs 导航只记录上述关系，不复制正文。

在 requirements、导航和所选 Phase 0 产物实际存在并完成评审前，即使 R1/R2 修正，也不能授权 Phase 1。这与现稿“不提前修改 production/normative truth”的时序要求不冲突。

## 4. 非阻断但应收紧

### N1. S08 应固定一个导入名称

S08 表格允许“按需收窄/重命名” `_declared_joins()`，第 11 节却要求“只复用现有 `_declared_joins()`”。为避免无收益的 symbol/import churn，最小选择是保留现名，由 `snapshot_guard.py` 在连续模块头做一次 module-scope import，并更新 architecture owner gate；删除“按需重命名”的开放选项即可。

这不要求增加 public re-export，也不改变 routing 是 join projection 唯一 owner 的结论。

## 5. 修订后的准入条件

下一版只需处理本轮新增缺口，不必重新打开已经闭合的 S09–S12 设计：

1. 扩充 S06，删除 `CompiledGraph` 的全部 forwarding projections，并固定 consumer 统一访问 `graph.transition.*`；
2. 为 S06 增加完整 producer/consumer/import/test 清单和 `CompiledGraph` exact-shape architecture gate；
3. 明确 Phase 0 各文档的唯一 owner 与 exact paths，避免另建重复 target-shape proposal；
4. 把新增文件门禁改成覆盖每个原子单元完整 changed-file set 的单一策略；
5. 实际补齐 requirements、docs 导航和所选 Phase 0 产物，再对它们与本文做准入评审；
6. 固定 S08 的 module-scope import 名称，删除可选 rename wording。

## 6. 本次验证

本轮遵循“测试悠着点”的要求，只做静态核对：

| 检查 | 结果 |
| --- | --- |
| 实施方案全文 | 已复核 445 行 |
| S03–S06 production/test consumer | 已用当前代码逐项交叉搜索 |
| routing/recovery target shape | 已对照现有 `routing.py`、`recovery.py` 与 architecture gate |
| Phase 0 artifacts | requirements/proposal 当前不存在 |
| Git 跟踪状态 | 四份既有 simplification 文档均为 untracked；未修改 index |
| production/tests | 未修改 |
| pytest、Pyright、`make check` | 本轮未重复运行；不把历史基线冒充本轮新证据 |

本轮只需对新增复审 Markdown 做 whitespace/diff 静态检查；完整测试、Pyright 和发布门禁留给实际 production 原子变更。

## 7. 最终裁决

本版已经关闭第二次复审中 routing 最小 facts、S12 equality/泛型、S03–S05 迁移清单、normative 时序和 S08 重复 representation 等主要问题，P1/P2 数量也正确。

但 S06 当前会留下五个平行 forwarding 入口，Phase 0 文档分工尚可能制造第二 target-shape 真相，固定四文件门禁又覆盖不了 Phase 0 和后续新增文件。因此：

**第三次复审仍不通过，不授权 Phase 1。** 完成第 5 节六项后再做一次针对性准入评审；P2 继续保持逐项单独评审，不继承未来的 P1 批准。
