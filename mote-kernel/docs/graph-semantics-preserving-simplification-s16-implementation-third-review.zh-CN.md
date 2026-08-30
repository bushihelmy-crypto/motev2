# S16 `GSP-A06` 单项实施设计第三次独立技术评审

> **结论：`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`（仅表示当前 reviewed exact target 的设计与证据通过技术复核）。二审 R1′ 的实际 case-to-branch 映射与 R6′ 的直接异常 `__cause__` 谓词均已闭合；没有发现新的技术阻断项。本记录不批准 `GSP-A06`，不授权修改 production、tests、requirements、normative source、State、Store、protocol、持久化或错误恢复实现。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- 本轮绑定 owner SHA256：`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402`
- 第二次独立评审：[S16 第二次独立技术评审](graph-semantics-preserving-simplification-s16-implementation-second-review.zh-CN.md)，SHA256：`a2d3f9f0df5f96b211075887415ed5d627e78802477175b5988f57a57ce9c5ef`
- 第二次评审回复：[S16 第二次实施设计评审回复](graph-semantics-preserving-simplification-s16-implementation-second-review-response.zh-CN.md)，SHA256：`300f9f0eb751539c4786f489104590433f88274b238849b4dc905d4fd263e0f7`
- 声明源码基线：Git `f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497`（当前 `HEAD` 与该值一致）
- production 基线：`src/mote_kernel/execution/invocation.py` → `4165f689af384f9b91080b432328ce3003f9e4b4308bcf34960e1d3db0550f5d`
- frame owner 基线：`src/mote_kernel/execution/run_context.py` → `bf196695bce1687f0bd9554d3a8615e9afc5dbfa1bedbc859cd199e8ff54f648`
- behavior 基线：`tests/execution/test_continuation_integrity.py` → `d900f3b812f9618587182ed4e86974cfc7dc0d3fa0de647ad9f797693bdaa17e`
- 本文性质：第三次独立 technical review record；只拥有本轮裁决和复核证据，不拥有 S16 target、requirements 批准状态或 production/test shape
- 本轮 change unit：只新增本文；保留工作树中全部既有用户修改，不把它们纳入 S16 review manifest

## 2. 审核边界与总裁决

本轮继续按用户明确边界审核：唯一事实、复用现有 execution/frame 基础设施、零新增结构与所有权负债；不实现持久化，不新增 retry、fallback、checkpoint、failover、第二 recovery runner 或其他错误恢复能力。automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均为 `USER-EXCLUDED`，本轮没有新增、修改或依赖这些 gate；current behavior、strict typing、active owner/dependency/source-discipline、lint、format、coverage、build/package 与 no-persistence 条件仍按 owner 文档保留为 implementation required gates。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| R1′：case-to-branch 映射 | **已关闭** | owner §8.2 与 §13.3 现在只把实际能到达对应 `raise` 的 case 计入矩阵；七个 case 的集合覆盖四个 canonicality 分支 |
| R6′：直接异常 `__cause__` | **已关闭** | M0、七行 behavior 表、`S16.j`、required gate 与停止条件均明确要求直接 shape/canonicality 异常的 `raised.value.__cause__ is None`；Phase 3 的既有 chaining 不被改写 |
| exact target / nominal 等价 | **通过（有界）** | Ruff-canonical `pairwise` target 在 compiler-produced、hashable、total-order 一致的 exact nominal coordinate 域内与 current unique+sorted predicate等价 |
| phase、segment 与 error precedence | **通过设计** | lineage → shape → canonicality → content 以及 graph input → publication → resume input → child boundary 顺序均保持 |
| 唯一事实与基础设施复用 | **通过** | `_validate_frame_index()` 继续唯一编排；`run_context.py` 继续唯一拥有 record/coordinate/`ScopedFrameIndex`；不增加 helper、DTO、cache、index、validator 或第二 admission path |
| State、Store、protocol、persistence | **通过 / HARD KEEP** | planned implementation manifest 只有 `invocation.py` 与既有 continuation behavior test；State/Store/protocol/persistence 均禁止修改 |
| 错误恢复范围 | **通过 / 不扩展** | complete/recovered 继续复用同一 admission；不新增修复损坏 snapshot、retry、fallback、checkpoint、failover 或第二 runner |
| zero-debt 结构账本 | **通过设计并独立复算** | 15 个 module-level functions不增长；四个 coordinate projection、一个异构 dispatch、四个 `set` 与四个 `tuple(sorted(...))` 共 13 个旧构造/分派点归零，只增加一个标准库 import和四个 direct scans |
| `GSP-A06` 当前状态 | **未批准** | 本轮只允许进入 requirements owner approval；requirements 仍明确把 S16 列为未批准 P2，production/tests 仍不得实施 |

本轮没有遗留需要再开立的 R item，也没有发现会迫使 S16 扩大 production manifest、引入持久化/恢复能力或建立第二事实源的设计问题。

## 3. 二审 R1′：实际 case-to-branch 矩阵已闭合

我按 owner §8.2 每个 case 的构造与固定首错顺序重新展开，当前唯一矩阵正确：

| canonicality raise branch | 实际到达该 branch 的 planned public case |
| --- | --- |
| graph input | `test_complete_continuation_rejects_descending_frame_coordinates`；`test_continuation_validation_keeps_canonical_segment_order` |
| publication | `test_continuation_validation_keeps_canonicality_before_content_precedence`；`test_recovered_continuation_rejects_noncanonical_frame_coordinates` |
| resume input | `test_complete_continuation_rejects_descending_resume_input_coordinates` |
| child boundary | `test_complete_continuation_rejects_descending_child_boundary_coordinates` |

`test_continuation_validation_keeps_shape_before_canonicality_precedence` 同时放入 graph-input duplicate 与 malformed publication；由于 Phase 1 的四个 shape guards 全部早于 Phase 2，它只命中 publication malformed branch，不被计入 canonicality coverage。`test_continuation_validation_keeps_canonical_segment_order` 同时破坏 graph input 与 publication，但固定 segment 顺序使它只命中 graph-input canonicality branch。当前矩阵没有再把被更早错误短路的 case 伪列为后续 branch evidence。

现有 recovered nested fixture 实际具有两个 publication records，因而 owner 为 recovered publication descending case 选择的构造可机械实现。resume-input 与 child-boundary 的 descending cases 各要求至少两个合法 coordinates，并明确必须绕过会主动排序的 `ScopedFrameIndex.add_*()`，使用既有 test-only continuation tamper harness 安装原始 tuple；该方式不扩大 production API 或引入 private-source-shape gate。

七个 planned cases 尚未实施是正确状态：它们是 requirements 批准后与 production 同一个两文件原子 implementation unit 的 target evidence，而不是本轮 design review 可以冒充的 implementation result。

## 4. 二审 R6′：直接 `__cause__` 谓词已闭合

当前 owner 已在所有相关层面登记同一个 exact contract：

- §8.2 的 M0 要求每个直接由 Phase 1 shape 或 Phase 2 canonicality precedence 抛出的公开 `Graph.SnapshotMismatchError` 满足 `raised.value.__cause__ is None`；
- 七行 behavior 表逐行写明 direct `__cause__ is None`；
- §8.3 的 `S16.j` 把该结果纳入一次性 actual source/evidence review；
- §13.1、§13.3 把 cause 纳入 required error/coverage gate；
- §14 把 direct cause chaining 变化列为停止条件。

该要求没有错误地扩展到 Phase 3 content admission。当前 Phase 3 对 frame admission 的 `raise SnapshotMismatchError(...) from error` 仍保留既有 cause contract；target 不增加 `try/except`、wrapper 或 normalizer，因此不会为了统一 `__cause__` 而改变 error owner。`Graph.run()` 在 `validate_context()` 周围也没有 catch/re-wrap，直接 shape/canonicality 错误可按原 cause 向 public boundary 传播。

## 5. Exact target、等价性与 zero-debt 复核

### 5.1 Target 与类型边界

当前 exact target只做以下改变：在 module-header 标准库 block 增加 `from itertools import pairwise`，删除四个 coordinate tuple projections及异构 `(name, segment)` loop，并按原 segment 顺序直接执行四个 strict-adjacent guards。前三个 guard为 Ruff 0.16.2 单行 canonical output，child-boundary guard按同一 formatter 折行；当前工具版本复核为 `ruff 0.16.2`。

四类 coordinate、其组成的 `ScopeRunCoordinate`、`StableActivation` 与 `FrameDescriptorIdentity` 都是现有 frozen/order nominal values。对 compiler-produced、hashable、total-order 一致的 exact nominal domain：

```text
len(C) == len(set(C)) and C == tuple(sorted(C))
```

当且仅当每个相邻 pair 满足 `previous < current`。空 tuple、singleton、duplicate 和任意 descending input 的接受/拒绝集合均保持；`previous.coordinate >= current.coordinate` 同时拒绝 equality 与 inversion。target只改变纯遍历方式，不读取 concrete user value，不写 State，不调用 node/resource/commit。

forged unhashable/mixed inner fields 继续明确排除在该 nominal contract之外；target不为这些反射构造新增 catch、normalizer 或 malformed contract。owner 列出的 descriptor/scope/activation/node/enum-int scalar tamper 仍须在批准后的 implementation unit 做 baseline-vs-target 一次性 probe，并按 type/text/cause 漂移停止。该 pending implementation evidence 不妨碍当前 exact nominal 设计进入 requirements owner approval，也不能被本 review 伪称为已完成 implementation evidence。

### 5.2 Owner 与结构账本

当前源码机械复算得到 `invocation.py` 顶层函数仍为 15 个。target不新增 function、validator、class、DTO、type alias、protocol、field、property、cache、index、callback、context bag、TypeVar、cast、ignore、public export或stored fact。旧结构的单一计数口径为：

```text
4 coordinate projections
+ 1 heterogeneous dispatch tuple
+ 4 set(...) constructions
+ 4 tuple(sorted(...)) constructions
= 13 old construction/dispatch sites removed
```

`sorted` result copy与dynamic label只是上述项目的说明，没有重复计数。新增面只有一个标准库 import和四个 exact nominal direct scans；`run_context.py` 零 diff，State/Store/protocol/persistence 与 recovery owner均零 diff。这符合用户要求的唯一事实、基础设施复用和零新增负债。

## 6. Baseline 与 gate 复核证据

三份 S16 source/behavior 文件当前相对声明 Git 基线无 diff，SHA256 与 owner 第 1 节逐字一致。本轮只读复跑结果：

```text
tests/execution/test_continuation_integrity.py
→ 34 passed

owner §13.2 的 9 个 active architecture/source/owner nodeids
→ 9 passed

all tests excluding tests/architecture/test_complexity_gate.py
→ 826 passed, coverage 100.00% (4732 statements / 1466 branches; 0 missing / 0 partial)

pyright
→ 0 errors, 0 warnings, 0 informations

ruff check (invocation.py + continuation test)
→ passed

ruff format --check (invocation.py + continuation test)
→ 2 files already formatted

trailing-whitespace scan (owner、second-response 与本 review 文档)
→ passed
```

完整 `make check` 未运行，因为其 `check` 目标无条件包含用户明确排除的 `complexity-ratchet`；本轮没有运行、添加、修改或依赖 complexity/legacy gate。build/package 与 scoped monorepo pre-commit 也没有冒记为本次 docs-only review 已通过：它们仍是 requirements 批准后的 actual implementation unit 必须按 owner §13 执行的 REQUIRED gates。coverage baseline只证明当前源码未漂移；七个 target behavior cases、target 100% coverage、inner-scalar probe与一次性 `S16.a`–`S16.j` source review仍须在 implementation unit落地和复核。

## 7. 当前准入状态与后续授权

```text
S16 FIRST INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S16 SECOND INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S16 SECOND-REVIEW RESPONSE: R1′ ACCEPTED / R6′ ACCEPTED
S16 THIRD INDEPENDENT TECHNICAL REVIEW: PASS / READY FOR REQUIREMENTS OWNER APPROVAL
S16 R1′: CLOSED — ACTUAL CASE-TO-BRANCH MATRIX
S16 R6′: CLOSED — DIRECT SHAPE/CANONICALITY __cause__ IS NONE
S16 GSP-A06: NOT APPROVED
S16 REQUIREMENTS WRITEBACK: NOT AUTHORIZED YET
S16 PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
S16 PLANNED IMPLEMENTATION MANIFEST: invocation.py + existing continuation-integrity test file
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
NEW ERROR RECOVERY / RETRY / FALLBACK / CHECKPOINT / FAILOVER: FORBIDDEN
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED
CURRENT BEHAVIOR / TYPING / ACTIVE OWNER / COVERAGE / PACKAGE CHECKS: REQUIRED
```

本轮通过只允许进入下一次 **requirements-only** 审批准备。下一合法步骤是用户显式批准后，由 requirements owner 单独把 `GSP-A06` 记录为仅限本评审绑定 owner SHA256 的 `SATISFIED / APPROVED`；不是直接修改 production/tests。requirements-only approval完成后，才可按 owner §11.4 的两文件原子 manifest实施。若 owner SHA、三份源码基线、exact target、七 case义务、planned manifest或硬边界变化，当前技术通过不继承，必须重新评审。

## 8. 本次 review change unit

本文件是第三次独立技术评审的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-third-review.zh-CN.md
```

它不覆盖任何既有 review/response，不修改 owner、requirements、主实施方案、production、tests、State、Store、protocol、persistence、complexity 或 legacy gate artifact，也不把工作树中的其他用户修改纳入本 review manifest。
