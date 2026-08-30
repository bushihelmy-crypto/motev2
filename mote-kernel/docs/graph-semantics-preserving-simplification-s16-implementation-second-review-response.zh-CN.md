# S16 `GSP-A06` 第二次实施设计评审回复

> **Disposition：第二次评审的 `CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL` 总裁决成立。R1′（case-to-branch 映射）与 R6′（直接 shape/canonicality error 的 `__cause__` 谓词）均接受并已回写；不新增 production、tests、fixture、State、Store、protocol、持久化、错误恢复或任何 complexity/legacy gate。本回复不批准 `GSP-A06`，不授权 production/tests。**

## 1. 回复信息

- 日期：2026-08-24
- 第二次独立评审：[S16 GSP-A06 单项实施设计第二次独立技术评审](graph-semantics-preserving-simplification-s16-implementation-second-review.zh-CN.md)
- 第二次评审 SHA256：`a2d3f9f0df5f96b211075887415ed5d627e78802477175b5988f57a57ce9c5ef`
- 第二次评审绑定的旧 owner SHA256：`f20fd204a89c231a6fccb04d7a2e1e53469b6870515c2bef3601b0b0a25d411a`
- 首轮评审回复（历史记录）：[S16 实施设计评审回复](graph-semantics-preserving-simplification-s16-implementation-review-response.zh-CN.md)，SHA256：`1be7cb350ebaa3891ea43de34557f8d415e94211f7b3eaf9301f45719d3b0bb6`
- 当前 owner：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- 当前 owner writeback SHA256：`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402`
- 本文性质：二审 disposition/audit record；不拥有 S16 exact target、requirements 批准状态、production shape 或测试 shape
- 变更边界：只记录二审接受项与 owner writeback 证据；本文是独立 docs-only response unit，不修改首轮评审/回复历史、requirements、normative source、production、tests、State、Store 或持久化

## 2. 逐项 disposition

| Review item | Disposition | Owner 处理 |
| --- | --- | --- |
| R1′：case-to-branch evidence 映射不准确 | **ACCEPTED** | owner §8.2 与 §13.3 改用实际可到达矩阵：graph input = descending-frame + canonical-segment-order；publication = canonicality-before-content + recovered；resume-input 与 child-boundary 各由对应 descending case覆盖；shape-precedence case 不计入 canonicality branch。 |
| R6′：`__cause__` 未进入 exact behavior predicate | **ACCEPTED** | M0、7 行 behavior 表与一次性 predicate 新增直接 shape/canonicality `SnapshotMismatchError` 的 `raised.value.__cause__ is None`；Phase 3 既有 `raise ... from error` contract 保持，不新增 fixture。 |

## 3. R1′：实际 case-to-branch 矩阵

接受二审逐项展开的首错分析，owner 现在固定以下唯一矩阵：

| canonicality raise branch | 实际会到达该 raise 的 case |
| --- | --- |
| graph input | `test_complete_continuation_rejects_descending_frame_coordinates`；`test_continuation_validation_keeps_canonical_segment_order` |
| publication | `test_continuation_validation_keeps_canonicality_before_content_precedence`；`test_recovered_continuation_rejects_noncanonical_frame_coordinates` |
| resume input | `test_complete_continuation_rejects_descending_resume_input_coordinates` |
| child boundary | `test_complete_continuation_rejects_descending_child_boundary_coordinates` |

`test_continuation_validation_keeps_shape_before_canonicality_precedence` 先命中 publication shape error，不计入任何 canonicality
raise。七个 case 的集合仍然 branch-complete；本次只是修正 evidence owner 的可机械复现映射，不增加 case、production 文件、coverage
omit、pragma 或 legacy/private-source-shape gate。§13.3 仅引用该矩阵，coverage 100% 不替代 case-level 到达证据。

## 4. R6′：直接异常的 cause predicate

接受二审要求，但保持首轮 R6 的零负债取舍：

- 每个新增 case 继续通过公开 `Graph.run()` 断言完整 `str(error)`、首错 phase/segment、State/continuation 未修改与既有 mutation-free
  cross-evidence；
- 对直接由 Phase 1 shape 或 Phase 2 canonicality precedence 抛出的 `Graph.SnapshotMismatchError`，精确断言
  `raised.value.__cause__ is None`；
- 不把该断言套到 Phase 3 content-admission 的既有 `raise SnapshotMismatchError(...) from error`，不改写其 cause contract；
- 不新增 `CommitLog`、node/resource call counter、callback fixture，不修改 continuation test 文件或 recovery tests。

owner 第 8.3 节新增 `S16.j` 一次性 source/evidence predicate，第 13.1 节把 cause 纳入 required exact behavior，第 14 节把 direct
canonicality cause 改变列为停止条件。若实际 target 引入非 None cause，保持 production 现状并重新评审，不通过 catch/normalizer 或跨层
fixture 掩盖。

## 5. 其余结论保持关闭

二审已核对通过的 R2、R3、R4、R5 与首轮已接受的技术结论全部保持：

- `test_source_discipline.py` 的真实 architecture nodeid 与 9 passed；
- Ruff 0.16.2 canonical target、唯一 `itertools.pairwise` import、无 helper/DTO/第二 validator；
- compiler-produced、hashable、total-order exact nominal domain 的等价证明及 forged unhashable/mixed outside-contract 边界；
- 13 个旧构造/分派点的单一账本口径；
- `invocation._validate_frame_index()`、`run_context.py`、四类 record/coordinate、`ScopedFrameIndex` 的唯一 owner；
- State/Store/protocol/persistence/error-recovery HARD KEEP，以及 automated complexity 与 legacy/private-source-shape gate 的用户排除边界。

## 6. 当前状态与后续顺序

```text
S16 SECOND REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S16 SECOND-REVIEW DISPOSITION: R1′ ACCEPTED / R6′ ACCEPTED
S16 OWNER WRITEBACK: COMPLETE AT NEW SHA256 abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402
S16 THIRD INDEPENDENT RE-REVIEW: REQUIRED
S16 GSP-A06: NOT APPROVED
S16 PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
S16 STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
S16 AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SHAPE GATES: USER-EXCLUDED
```

首轮 response 保持历史不覆盖；本 response 与 owner writeback 分属独立 docs-only unit。重新绑定新 owner SHA 并完成第三次独立技术评审、
requirements owner 显式批准及用户授权前，不修改：

```text
src/mote_kernel/execution/invocation.py
tests/execution/test_continuation_integrity.py
src/mote_kernel/execution/run_context.py
State / Store / protocol / persistence artifacts
```

## 7. 本次 response change unit

本文是二审 response 的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-second-review-response.zh-CN.md
```

不得把二审历史、首轮 review/response、主实施方案、requirements、production、tests、State、Store、protocol、persistence、complexity 或
legacy gate artifact 伪列入本 response manifest.
