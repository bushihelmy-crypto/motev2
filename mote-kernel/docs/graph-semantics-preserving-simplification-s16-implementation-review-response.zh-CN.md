# S16 `GSP-A06` 实施设计评审回复

> **Disposition：本轮评审的 `CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL` 总裁决成立。R1、R2、R3、R5 全部接受并已回写；R4 以 exact nominal-domain limit 接受；R6 部分接受：接受完整 literal、phase/segment 与 mutation-free evidence，拒绝把跨层 commit callback、node/resource call counter fixture 强制塞入 S16 continuation test 文件。本回复不批准 `GSP-A06`，不授权 production/tests。**

## 1. 回复信息

- 日期：2026-08-24
- 独立评审：[S16 `GSP-A06` 单项实施设计评审](graph-semantics-preserving-simplification-s16-implementation-review.zh-CN.md)
- 评审记录 SHA256：`962c4686e9d0c33315e727030fb2085b89241d46effbb952c0f84b5ea99b5702`
- 评审绑定的旧 owner SHA256：`8eb7bc7da3feb3f4440cf363670cff39b90453616515dbbd2ea479ad39aed163`
- 当前 owner：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- 当前 owner writeback SHA256：`f20fd204a89c231a6fccb04d7a2e1e53469b6870515c2bef3601b0b0a25d411a`
- 本文性质：owner disposition/audit record；不拥有 S16 exact target、requirements 批准状态、production shape 或测试 shape
- 变更边界：实施文档吸收接受项；本文是独立 docs-only response unit；不修改评审历史、requirements、normative source、production、tests、State、Store 或持久化

## 2. 逐项 disposition

| Review item | Disposition | Owner 处理 |
| --- | --- | --- |
| R1：canonicality 分支 coverage 不闭合 | **ACCEPTED** | 第 8.2 节改为至少 7 个 public `Graph.run()` cases；新增 resume-input 与 child-boundary descending cases，publication 由 precedence/recovered cases覆盖；每项固定完整错误 literal、首错 phase/segment 与 `M0` mutation-free evidence。 |
| R2：§13.2 architecture nodeid 错误 | **ACCEPTED** | `test_execution_is_the_only_generic_executor_owner` 固定在 `tests/architecture/test_source_discipline.py`；9 个 nodeid 的可复现结果固定为 `9 passed`。 |
| R3：exact code block 与 Ruff format 冲突 | **ACCEPTED** | §5.3 回写 Ruff 0.16.2 canonical output，并继续将其作为 exact target；不改变算法、不新增 helper、import 或 production owner。 |
| R4：nominal/tamper 边界过宽 | **ACCEPTED WITH NOMINAL-DOMAIN LIMIT** | 等价证明与公共 canonicality 错误契约限定为 compiler-produced、hashable、total-order 一致的 exact nominal coordinate domain；inner-tamper probe 保持 `PENDING`，forged unhashable/mixed inner field 不构成新增 contract，不新增 catch/normalizer。 |
| R5：结构账本重复计数 | **ACCEPTED** | `sorted-result tuple copies` 改为上一行 `sorted(...)` 的非计数说明，并将 dynamic label 标为 dispatch 的非计数说明；旧构造净删除总数仍为 13，不允许机械加总出两个口径。 |
| R6：完整错误与 mutation-free evidence 不具体 | **PARTIALLY ACCEPTED** | 接受每个新增 case 固定完整 `str(error)`、phase/segment、public admission 边界与 `M0`；复用既有 recovery/commit/continuation contract 作为跨层交叉证据。拒绝强制新增或迁入 CommitLog、node/resource call counter、callback 计数 fixture。 |

## 3. R1：branch-complete public evidence

实施文档第 8.2 节现在明确登记 7 个 target cases。四个 production canonicality `raise` 分支均由 public behavior 覆盖：graph input
由 descending case 与两个 precedence case覆盖，publication 由两个 precedence case及 recovered case覆盖，resume input 与 child boundary
分别由新增 descending case覆盖。每个 case 都通过 `Graph.run()` 观察 exact `Graph.SnapshotMismatchError`，并固定首错阶段、segment 和完整
错误文本；descending tuple 只由 test-only harness 原样安装，不调用会自动排序的 `ScopedFrameIndex.add_*()`。

第 13.3 节同步说明 `--cov-fail-under=100` 必须在 7 个 cases 落地后复核。评审临时副本的 `826 passed / 99.90%` 与补齐 probes 后的
`829 passed / 100.00%` 只作为 branch-planning evidence，不是已实施结果，也不授权修改 coverage omit、pragma 或测试发现规则。

## 4. R2 与 R3：可复现命令和唯一 target 形状

R2 的 owner 回写固定以下真实 nodeid，评审原文中的不存在路径不再出现在实施命令中：

```text
tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner
```

其余 8 个 active architecture/source/owner nodeid 与原计划保持，逐字执行应得到 `9 passed`。

R3 的 §5.3 代码块采用当前 Ruff 0.16.2 的 canonical formatter output。`pairwise` 仍是唯一新增标准库 import，四个 nominal guard
仍直接位于 `_validate_frame_index()` 的 canonicality phase；不把 formatter 布局变成永久 source-shape/AST gate，也不因格式修正引入
generic helper、wide union、callback 或第二 validator。

## 5. R4：exact nominal-domain limit 与 pending tamper probe

实施文档现在把证明域写成 compiler-produced、hashable、total-order 一致的 exact nominal coordinate domain。`unique + sorted` 与严格
相邻递增的等价、accepted/rejected 集合、canonicality 错误 type/text/phase precedence 均只对该域作出承诺；target 不比较、hash、排序
或 repr concrete user frame/value。

通过 `cast`、`object.__setattr__` 或其他反射伪造的 unhashable/mixed inner field 不属于新增 contract，也不要求 target 为偶发
`TypeError` 增加 catch 或 normalizer。descriptor identity、scope/run identity、activation superstep、node identity 与 enum/int scalar
probe 仍明确标记为 `PENDING / NOT EVIDENCE COMPLETE`；在 target 实施前不把 tamper evidence 写成已完成。若已列出的 characterized scalar
probe 改变 exception type/text/cause，仍触发 owner 第 14 节停止条件。

## 6. R5：结构账本的单一计数口径

接受评审建议后，表格只把以下项目计入净变化：4 个 coordinate projections、1 个 heterogeneous dispatch tuple、4 个 `set(...)` 与
4 个 `tuple(sorted(...))`，合计删除 13 个旧构造/分派点。`canonicality sorted-result tuple copies` 与 dynamic segment label/f-string
projection 只作为上一行的结果说明，明确“不另计”，因此不会把同一份 tuple 或 dispatch label 重复扣除。该修正不引入 complexity gate，
也不改变 `pairwise` target。

## 7. R6：接受部分与明确拒绝部分

### 7.1 接受

每个新增 behavior case 必须通过公开 `Graph.run()` 断言：

- `str(error)` 与表中完整 `Graph.SnapshotMismatchError` literal 逐字相等；
- 首错 phase/segment 固定为 shape/canonicality 的既定顺序；
- admission failure 发生在生命周期推进前，输入 State 与 continuation snapshot 不被修改；
- fence、resume、claim、child start、resource、node、commit 的 mutation-free 结论引用既有 execution/recovery contract 交叉证据。

实施文档明确复用以下既有 evidence，而不把 recovery owner 纳入 S16 manifest：

```text
tests/execution/test_graph_recovery_contract.py::test_recovered_plain_skip_rejects_a_missing_graph_output_before_commit
tests/execution/test_graph_recovery_contract.py::test_recovered_control_target_rejects_a_lost_graph_input_before_mutation
tests/execution/test_graph_api.py::test_state_only_multi_scope_substitution_is_rejected_before_first_commit
tests/execution/test_graph_api.py::test_normal_resume_never_mutates_the_input_continuation_snapshot
```

### 7.2 拒绝

**拒绝将新的跨层计数 fixture 强制加入 S16 continuation-integrity test 文件。**评审提出的 commit callback、node/resource call counter
或统一 `CommitLog` fixture 对 mutation-free 结论有帮助，但不是本 production target 的必要 owner 证据：S16 只替换纯 canonicality
validation，既有 execution/recovery contract 已经覆盖 admission 早于 commit、node/resource 与 snapshot installation 的边界。
强行把这些计数器搬入 S16 文件会扩大 test manifest、混入无关 owner、制造新的 fixture 负债，并把一次性跨层计数误写成 S16 永久契约。

因此本单元不新增 callback/call-counter fixture、不修改 `tests/execution/test_graph_api.py` 或 recovery tests，也不建立新的 AST/source-shape
gate。若后续实际实现无法由上述既有 evidence 与 public case 完成 mutation-free 证明，必须另立 review/manifest；不能在 S16 中隐式扩张
production 或测试边界。

## 8. 评审中已通过的技术结论

以下结论全部接受并继续作为实施文档硬边界：

- 在 exact nominal domain 内，`pairwise` strict-adjacent predicate 与原 `unique + sorted` accepted/rejected 集合等价；不读取 concrete
  frame/value，不写 State，不触发 node/resource/commit。
- `invocation._validate_frame_index()` 继续是唯一 continuation frame-index 编排 owner；不拆第二 validator、第二 execution path 或
  recovery runner。
- `run_context.py`、四类 record/coordinate、`ScopedFrameIndex` 与 canonical add path 保持不变；不新增 DTO、cache、index、callback 或
  public export。
- State、Store、protocol、persistence、commit/memory-install 顺序及既有错误恢复边界 `HARD KEEP`；不实现持久化、retry、fallback、
  failover、checkpoint 或自动恢复。
- automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 继续按用户指令排除；不因本轮评审新增或
  扩写任何门禁。current behavior、strict typing、active owner/dependency、lint、format、coverage、build/package 与 scoped pre-commit
  仍是 required checks。

## 9. 当前状态与后续顺序

```text
S16 REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S16 OWNER DISPOSITION: R1 ACCEPTED / R2 ACCEPTED / R3 ACCEPTED / R4 NOMINAL-LIMIT / R5 ACCEPTED / R6 PARTIAL
S16 OWNER WRITEBACK: COMPLETE AT NEW SHA256 f20fd204a89c231a6fccb04d7a2e1e53469b6870515c2bef3601b0b0a25d411a
S16 INDEPENDENT RE-REVIEW: REQUIRED
S16 GSP-A06: NOT APPROVED
S16 PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
S16 STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
S16 AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SHAPE GATES: USER-EXCLUDED
```

整改后的实施文档必须以新 SHA256 重新绑定下一次独立技术评审；review record 不拥有 target，response record 不拥有批准状态。
在 requirements owner 对新 SHA 显式授予 `GSP-A06` 且用户另行授权前，保持：

```text
src/mote_kernel/execution/invocation.py          UNTOUCHED
tests/execution/test_continuation_integrity.py   UNTOUCHED
src/mote_kernel/execution/run_context.py         UNTOUCHED
State / Store / protocol / persistence artifacts  UNTOUCHED
```

## 10. 本次 response change unit

本文是本次评审回复的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-review-response.zh-CN.md
```

它与实施文档 writeback 分属独立 docs-only change unit；不得把评审历史、主实施方案、requirements、production、tests、State、Store、
persistence、complexity 或 legacy gate artifact 伪列入本 response manifest.
