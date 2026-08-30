# S19 / S21 / S22 实施方案第三次独立技术评审

> **结论：`PASS / READY FOR REQUIREMENTS OWNER PER-UNIT DISPOSITION`。** 二审 R9 已闭合：S19 现在明确覆盖 decoder 返回 owner-produced `Graph.Values`、但字段名称或 exact nominal value type 不匹配 compiled descriptor 的两条独立 admission boundary，并固定 error、cause、codec 时序、无结果返回及输入 State/frame 不变。该 target case 在 strict typing 下可实现，仍只新增一个 nodeid 且不扩大两文件 manifest。未发现新的技术阻断项。本文只允许 S19 进入 requirements owner 的显式 `GSP-A06 APPROVED` 裁决，并确认 S21、S22 可分别进入 `SATISFIED / CLOSED — KEEP` 裁决；本文本身不批准任何单元，也不授权修改 production、tests 或 requirements。

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S19 / S21 / S22 实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- 本轮绑定 implementation owner SHA256：`07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`
- 第二次独立评审：[S19 / S21 / S22 第二次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review.zh-CN.md)，SHA256：`9a964c3b25338a5b5c9d5f32da0302b099015126de447ab291e50140f8c97a81`
- 第二次评审回复：[S19 / S21 / S22 第二次独立技术评审回复](graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review-response.zh-CN.md)，SHA256：`0fbcddd90fff955cbea130350713c3f16abe8553724c38e92e8f65e9dbbc5cfc`
- 首次 review / response 继续作为历史记录保留，不因本轮通过而改变其绑定 SHA 或历史裁决。
- 源码基线：Git `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad`（当前 `HEAD` 一致）
- production 基线：
  - `src/mote_kernel/execution/executor.py`：`98f0a1725c9fd618cbd28bd6a8d28ef0985106915208b5a197ff202de4d66ebb`
  - `src/mote_kernel/execution/facade.py`：`d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9`
  - `src/mote_kernel/execution/invocation.py`：`5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4`
- behavior 基线：
  - `tests/execution/test_executor.py`：`2b37f8ffb28cb1e409816c920b0f03fc947839e1725413210c60c400ed0bb103`
  - `tests/execution/engine/test_resume_input_contract.py`：`56dcd49e4de114e465579596a6cba3c32328ccad634e6363c46a89fb0961c5ff`
- 本文性质：第三次独立 technical review record；只拥有本轮裁决和复核证据，不拥有 exact target、requirements 批准状态或 production/test shape。
- 本轮 actual change unit：只新增本文；保留并排除工作树中全部既有用户修改。

## 2. 审核边界与总裁决

本轮继续执行用户明确边界：唯一事实、复用既有基础设施、零新增已知负债和高质量 typed code；不实现持久化、Store/backend、retry、fallback、checkpoint、failover、补偿事务或第二 recovery runner。automated complexity/baseline/ratchet 与 legacy/private-source-shape gate 均为 `USER-EXCLUDED / NOT RUN`；current behavior、strict typing、owner/dependency、lint、format、coverage、package 和适用 pre-commit 仍是 actual implementation/closure unit 的 required gates。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| 二审 R9 | **已关闭** | 两类 owner-produced decoded-value descriptor tamper 已进入同一 planned behavior nodeid，且 error/cause/order/immutability predicate 可执行 |
| S19 exact target | **通过设计** | 只收敛两个重复 encode/decode sites；validation、materialization lookup、coordinate、command、frame 与 frontier owner 不增长 |
| S19 nominal typing | **通过** | 复用现有 request、durable binding 与 frame nominal types；无 DTO、alias、protocol、cache、context bag、export 或第二 owner |
| S19 error / mutation order | **通过** | settlement/interrupt-ID validation 仍先于 codec；frame admission 仍先于 local accumulators；authoritative State/frame 不被修改 |
| S19 `GSP-P02` | **通过 / HARD KEEP** | command binding shape、codec identity/version、State revision 与 reducer projection 保持，helper 不构造 command 或修改 State |
| S21 | **通过 / KEEP** | 只有一个 `Graph.run()` lifecycle owner；无可删除的第二 mechanics owner；production/test manifest 为空 |
| S22 | **通过 / KEEP** | helper 候选会净增 private type ports 与 variant dispatch；保持两个显式 transaction loops 是更小且更清晰的 owner surface |
| 持久化与错误恢复 | **通过 / HARD KEEP** | 三单元均不新增存储、恢复策略、retry/fallback/checkpoint/failover 或第二 runner；S21/S22 零 production diff |
| `GSP-A06` 当前状态 | **仍未批准** | 本轮只确认技术设计与证据可进入 requirements owner 的三个独立 disposition；批准不得跨单元继承 |

本轮没有遗留 R item，也没有发现迫使 target 扩大 manifest、建立第二事实源、引入兼容路径或触碰 State/persistence/recovery production 的问题。

## 3. R9：decoded descriptor tamper 证据已闭合

### 3.1 两条独立 admission boundary 已登记

implementation §4.5 现在明确区分：

1. decoder 返回非 `Graph.Values`，由 decoded-value nominal-owner guard 拒绝；
2. decoder 返回 owner-produced `Graph.Values`，但其字段名称或 exact value type 不符合 compiled node-input descriptor，继续由既有 node-input frame admission 拒绝。

第二类现已具有两个明确 subcase：wrong name 与 wrong exact type。每个 subcase 都固定 `GraphValueAdmissionError` 的 exact message、`__cause__ is None`、valid failed-override validation 后恰好一次 `encode → decode`、不返回 `PreparedResume`，以及输入 `request.state` / `request.frames` 的对象和值保持不变。这直接补齐二审指出的 case-level 缺口，没有把底层 values-contract test 或 full coverage 冒充 resume admission integration evidence。

### 3.2 可观察断言与 local accumulator 边界合理

第二次 response 拒绝直接暴露或 instrument `actions`、`replacements`、`admitted_inputs`，该取舍正确。这三个值只是一次 `resume()` 调用内的局部 accumulator；为测试它们新增 port、hook、AST/source-shape assertion 或 helper-name gate，反而会制造测试缝隙和第二 owner。

当前证据组合足够且无新增负债：

- typed codec 记录 validation 与 `encode → decode` 的外部 callable 时序；
- exact exception、cause、无 `PreparedResume` 返回和 authoritative State/frame snapshot 不变提供可观察失败边界；
- implementation §4.3 的完整 caller order 与 actual production diff review 确认 local accumulator 只在 frame admission 后更新。

这保留了 mutation-order 语义，又没有把 private local shape 变成 legacy gate。

### 3.3 Test count 与 manifest 未漂移

两个 tamper subcase 保留在原 planned nodeid 中，不使用 parametrization 生成额外 collected cases。因此 S19 仍只新增一个 test case，full-suite target 仍为 `834 passed`。maximum implementation manifest 仍只有：

```text
src/mote_kernel/execution/executor.py
tests/execution/test_executor.py
```

`engine/resume_input.py`、`graph/values.py`、facade、invocation、State、Store、protocol 与其他测试继续要求零 diff。没有新增 validator、codec port、测试文件、type export、legacy/private-shape gate 或 complexity gate。

## 4. R9 target 可实现性探针

本轮临时应用 implementation §4.2 的 exact production target，并在既有 executor test owner 中构造两个 R9 typed decoder tamper subcase。wrong-type fixture 只在 test tamper boundary 显式伪造不符合 descriptor 的 runtime value；production 没有类型擦除、cast 或 suppression。结果：

```text
R9 typed tamper target case
→ 1 passed

pyright src/mote_kernel/execution/executor.py tests/execution/test_executor.py
→ 0 errors, 0 warnings, 0 informations

ruff check / ruff format --check
→ passed / 2 files already formatted
```

探针同时观察到：

- wrong-name result 得到文档登记的 descriptor-name `GraphValueAdmissionError`；
- wrong-type result 得到文档登记的 exact-type `GraphValueAdmissionError`；
- 两者 `__cause__ is None`；
- codec 记录均为一次 `encode` 后一次 `decode`；
- request State/frame 对象和值均不变。

该探针证明 R9 fixture 与 strict typing 可以同时闭合，不冒充完整 planned target case 已经实施。探针完成后所有临时 production/test 变更均已恢复；`executor.py` 与 `test_executor.py` SHA 回到第 1 节基线，Git diff 为零。

## 5. R1–R8 与 S21/S22 复核保持通过

- S19 的可删除面继续只有 failed override 与 interrupt override 的重复 codec pair；公共 `AdmittedResumeInput` construction 保持 caller 唯一 owner，两个 materialization lookup 的职责与顺序不变。
- S19 已显式映射 `GSP-P02`，不修改 State schema、revision、reducer projection、codec identity/version 或 durable command shape。
- S21 拒绝 `_run_new()` / `_run_state()`、wide admission DTO、string/enum dispatcher 与第二 runner 的理由仍成立；`facade.py` 基线与 public overload 保持。
- S22 的 direct private-plan import 仍违反 strict Pyright；通过 export/helper 修补会增加两个跨模块 type ports 与三处 nominal dispatch，因此 `KEEP` 仍是零负债裁决。
- `invocation.__all__` 仍为空，`execution.__all__` 仍只有 `Graph`；没有 parallel public execution facade。
- Ruff、coverage、package、pre-commit 与用户排除项已分账；没有把 `make check` 因 complexity 子目标未运行冒记为整体通过。

## 6. 独立验证证据

当前 production/tests 相对 Git baseline 无 diff，SHA 与 implementation 第 1 节一致。本轮复跑：

```text
S19/S21/S22 scoped six-file baseline
→ 160 passed

implementation 登记的 28 个 existing exact nodeid executions
→ 28 passed

full suite excluding tests/architecture/test_complexity_gate.py
→ 833 passed
→ 4,734 statements / 1,470 branches
→ 0 missing / 0 partial
→ line / branch coverage 100.00%

full strict Pyright
→ 0 errors, 0 warnings, 0 informations

Ruff lint / Ruff format --check
→ passed / 152 files already formatted

git diff --check
→ passed

monorepo pre-commit（implementation + second-response + 本 review）
→ applicable document hooks 与 detect-secrets passed
→ kernel-complexity: no files to check
```

上述 full-suite 结果只证明当前 baseline 没有漂移；S19 的完整 planned behavior case、target `834 passed`、actual diff/manifest 审计、package check 与 implementation-file pre-commit 仍必须在 requirements 批准后的原子 implementation unit 中执行。

完整 `make check` 未运行，因为它无条件包含用户明确排除的 complexity ratchet；本轮没有读取、修改、执行或依赖 complexity 与 legacy/private-source-shape gate。`make package-check` 也没有冒记为本次 docs-only review 已通过，它仍是 S19 actual implementation unit 的 required gate。S21/S22 不存在 package implementation unit。

## 7. 当前状态与合法后续顺序

```text
S19 THIRD INDEPENDENT TECHNICAL REVIEW:
PASS / READY FOR REQUIREMENTS OWNER GSP-A06 DISPOSITION
S19 GSP-A06: NOT APPROVED
S19 PRODUCTION / TEST IMPLEMENTATION: NOT AUTHORIZED

S21 THIRD INDEPENDENT TECHNICAL REVIEW:
PASS / READY FOR REQUIREMENTS OWNER SATISFIED / CLOSED — KEEP
S21 PRODUCTION / TEST MANIFEST: EMPTY

S22 THIRD INDEPENDENT TECHNICAL REVIEW:
PASS / READY FOR REQUIREMENTS OWNER SATISFIED / CLOSED — KEEP
S22 PRODUCTION / TEST MANIFEST: EMPTY

STATE / STORE / PROTOCOL / PERSISTENCE / ERROR-RECOVERY FEATURES:
HARD KEEP / UNTOUCHED

AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES:
USER-EXCLUDED / NOT RUN
```

下一合法步骤是 requirements owner 对本 review 绑定的 implementation SHA 按单元分别裁决：S19 只能显式记录 `GSP-A06 SATISFIED / APPROVED`；S21、S22 只能显式记录 `GSP-A06 SATISFIED / CLOSED — KEEP`。review 通过、response 完成、probe 通过或 empty manifest 都不能自行形成批准。

只有 requirements owner 完成 S19 的 exact-SHA approval 后，才能按 implementation §8–§10 的顺序实施 S19 两文件原子 unit；S21/S22 不得创建空 implementation/acceptance commit。若 implementation SHA、production baseline、target case 义务、manifest 或硬边界发生变化，本次技术通过不继承，必须重新评审。

## 8. 本次 review change unit

本文是本次第三次独立技术评审的唯一 actual changed-file：

```text
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-third-review.zh-CN.md
```

本文不覆盖既有 review/response，不修改 implementation owner、requirements、主实施方案、production、tests、State、Store、protocol、persistence 或任何 gate artifact，也不把本 review 变成 target shape 或批准状态的第二事实源。
