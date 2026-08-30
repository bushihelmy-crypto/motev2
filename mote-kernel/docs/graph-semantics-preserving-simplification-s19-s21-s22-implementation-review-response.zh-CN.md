# S19 / S21 / S22 实施方案首次独立技术评审回复

## 1. 回复信息

- 日期：2026-08-24
- 状态：`REVIEW DISPOSITION COMPLETE / IMPLEMENTATION OWNER UPDATED / PENDING SECOND INDEPENDENT REVIEW / NOT APPROVED`
- 首次评审：[S19 / S21 / S22 独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review.zh-CN.md)
- 首次评审 SHA256：`08dd6bf86ab8c5fe6b77f9b312aba2a71d5199a69c62a205e9f5583ee65d4eeb`
- 首次评审绑定的旧实施方案 SHA256：`d8161e0b2e3e22349411091b9a5c28927998d356ad01bb74886d075fd6c95bf0`
- 整改后的唯一 implementation owner：
  [S19 / S21 / S22 实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- 整改后 implementation owner SHA256：`d3ef4eabcd6e109fa0a6f08d8e175400719c8d12a41c49e535cea85f0da9459a`
- requirements owner：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)；继续唯一拥有 `GSP-A06`
  批准/关闭状态
- 本次 change unit：实施方案整改 + 本回复；production、tests、requirements、主实施方案、State、Store、protocol、持久化与
  error-recovery code 均未修改

本文只拥有 R1–R8 disposition、整改证据与 implementation-owner 回写索引，不拥有 S19/S21/S22 的修订后 target shape。所有 exact
signature、算法、账本、case matrix、manifest、门禁与停止条件只以整改后的实施方案为准。首次 review 仍只裁决旧 SHA，不能自动
批准新 SHA；下一步必须重新独立评审。

## 2. 总体回复

首次评审的核心结论有道理：R1、R3–R8 全部接受；R2 的证据缺口接受，但“必须修改 requirements 文字”只接受其 owner 明确裁决
目的，不接受把 requirements 改成第二份 per-unit applicability owner。整改还通过完整实现探针发现旧 S19 target 对公共
`AdmittedResumeInput` construction 的审计有误，因此比 review 要求更进一步收窄。

最终 disposition：

```text
S19: TARGET NARROWED / DESIGN EVIDENCE EXPANDED / NOT APPROVED
S21: KEEP / NO PRODUCTION CHANGE / PENDING EXPLICIT GSP-A06 CLOSURE
S22: KEEP / NO PRODUCTION CHANGE / ORIGINAL HELPER TARGET WITHDRAWN
GSP-A06: NOT APPROVED
PRODUCTION / TEST / STATE / STORE / PROTOCOL / PERSISTENCE: UNTOUCHED
```

## 3. 逐项 disposition

### R1 — 接受：S22 direct private import 不可实施；拒绝用新增 type surface 绕过

评审结论成立。整改期间在真实 `src/mote_kernel/execution/` package 内运行 direct-import 探针，strict Pyright 精确得到：

```text
"_PlannedFence" is private and used outside of the module in which it is declared (reportPrivateUsage)
"_PlannedResume" is private and used outside of the module in which it is declared (reportPrivateUsage)
```

探针随后被删除，production 恢复原 SHA。为排除“只差一个显式 export”的可能性，又临时把两个既有 private type 加入
`invocation.__all__` 并落地旧 helper target：Pyright 变为 `0 errors`，当前 Graph API `73 passed`。但候选同时新增一个 top-level
helper、两个跨模块 type exports 和三处 nominal dispatch；探针 diff 为 `53 insertions / 45 deletions`。这说明技术上可运行，不说明
净简化成立。

在零负债与唯一 owner 约束下，不接受下列替代：

- private-use suppression；
- `Any` / `object` / string kind；
- 新 alias、protocol、DTO 或 callback；
- 把 plan types 提升为并行公共入口；
- 把 commit/partial-handoff owner 移到 invocation。

因此没有扩大 S22 manifest，而是撤回原 helper target，将 S22 收敛为 `KEEP / NO PRODUCTION CHANGE`。完整候选账本、existing
transaction 时序与 closure evidence 已回写实施方案第 6 节。

### R2 — 部分接受：补齐 per-unit 证据；不修改 requirements 全局口径

接受部分：旧方案确实缺少每单元 exact-shape/tamper applicability 和完整认知面账本。整改已增加：

- S19 case-level baseline、target behavior case、shape/tamper applicability 与 negative manifest evidence；
- S21/S22 `N/A — KEEP` 的理由、空 manifest、existing public/tamper/owner nodeids；
- 一个覆盖 signature、nominal I/O、删除/新增对象、净结构、characterization、shape/tamper、manifest 与 owner disposition 的
  per-unit `GSP-A06` closure matrix；
- S22 explicit export/helper 候选的真实 type-port 与 dispatch 成本。

不接受部分：无需修改 requirements 第 6 节的全局 `GSP-A06` 文字。该条已经要求 per-unit exact-shape/tamper evidence，并未规定
所有单元必须新增 source-shape gate，也未规定 `N/A` 必须通过 requirements 文本修订实现。requirements owner 在第二次 review
绑定的新 implementation SHA 上逐项接受或拒绝 applicability，即可形成可审计裁决；若不接受，单元继续 `NOT APPROVED`。

修改 requirements 去复制 S19/S21/S22 的 N/A 理由会制造第二份 target/applicability truth，并削弱全局准入规则。实施方案第 7.1
节现已明确：`N/A` 不自批准、不自动豁免，也不得用 legacy/private-source-shape test 补票。

### R3 — 接受：S19 显式映射 `GSP-P02`

旧方案把 S19 `GSP-P02` 写成“不触及”过窄。虽然 S19 不修改 State schema/reducer，override admission 产生的
`OverrideGraphNodeInput` 会继续进入 existing `ResumeGraphNodes` command。因此实施方案第 7 节已改为适用，并固定以下 negative
evidence：

- command binding shape 不变；
- codec identity/version 不变；
- State revision 不变；
- reducer projection 不变；
- helper 不构造 command、不读取或修改 State。

任一项变化均进入实施方案停止条件。

### R4 — 接受：门禁统一为仓库 Ruff/build 口径

已删除 `black --check` 和模糊的 `python -m build` 建议，改为：

- `ruff check`；
- `ruff format --check`；
- `make package-check`，复用仓库 `build --no-isolation` 与 `twine check`。

不新增 Black 依赖，也不把 isolation/network 差异混入 production 裁决。

### R5 — 接受：固定 exact nodeids、coverage baseline/target 与 package check

实施方案第 4.5、5.3、6.4 节现在登记 exact `path::test_case`，覆盖：

- failed override/materialized retry/skip/interrupt；
- action canonical/scope/lifecycle 与 wrong settlement；
- encoder/decoder malformed/error boundary；
- public mixed resume、interrupt identity、cross-universe typing；
- fence/resume exact confirmation、frame installation、partial-prefix continuation 与 owner boundary。

Coverage 口径固定为排除用户明确排除的整个 complexity gate 文件后的 full suite：baseline `833 passed`、line/branch
`100.00%`；S19 只新增一个 target case，target 为 `834 passed`、line/branch `100.00%`。Scoped pytest 只作为快速行为证据，不能冒充
coverage。构建/包验证统一走 `make package-check`。

### R6 — 接受：拆开记录聚合门禁与用户排除项

实施方案第 9.1 节已固定四类状态：

```text
AUTOMATED COMPLEXITY / BASELINE / RATCHET: USER-EXCLUDED / NOT RUN
LEGACY / PRIVATE-SOURCE-SHAPE GATE: USER-EXCLUDED / NOT RUN
CURRENT BEHAVIOR / TYPING / OWNER / LINT / FORMAT / COVERAGE / PACKAGE: REQUIRED
STATE / PERSISTENCE / ERROR-RECOVERY FEATURE IMPLEMENTATION: OUT OF SCOPE / ZERO DIFF
```

`make check` 因内嵌 complexity ratchet 不得冒记为整体通过；若在排除项停止，必须单独跑完其余 component gates。monorepo
pre-commit 也只能跳过明确排除的 hook ID，不得连带跳过 detect-secrets、whitespace、Ruff 或其他适用 hooks。

### R7 — 接受并采用更严格边界：S22 不触碰 recovery production

旧文档“error recovery HARD KEEP”与“重构 recovery confirmation mechanics”容易被读成矛盾。整改后：

- S19 只去重 existing resume override value admission，不新增 retry/fallback/checkpoint/failover；
- S21 production/test 零 diff；
- S22 production/test 零 diff，existing fence/resume/partial-prefix handoff 只作为冻结的 baseline behavior；
- 不实现持久化、错误恢复能力、补偿事务或第二 runner。

这满足用户“不实现错误恢复”的严格解释，同时仍完成 S22 候选方向的可审计关闭。

### R8 — 接受并进一步整改：S19 只提取真实重复的 codec pair

评审指出 `frontier_node()`、`StableActivation` 与 materialization lookup 次数缺失，结论成立。实施方案现已固定完整顺序：

```text
frontier lookup → activation → materialization lookup #1 → action-local settlement/interrupt ID
→ encode → decode/materialization lookup #2/frame admission → command/replacement
→ existing common AdmittedResumeInput construction → final simulated frontier validation
```

两次 materialization lookup 在 target 中原样保留：executor lookup 拥有 coordinate，decoder lookup 防御性拥有 declaration；不新增
cache、plan forwarding 或第二 coordinate owner。

完整 S19 实现探针还发现旧方案把 `AdmittedResumeInput` construction 误记为两个 duplicate sites。当前源码本来就在三个 resume
路径后共用一次；旧 target 把 construction 搬进 helper 后，反而迫使 materialized retry 新增第二份 construction，探针为
`34 insertions / 11 deletions`。该旧 target 已撤销。

修订 target 只让 two-consumer method 接受既有 `GraphNodeId` + `OverrideNodeInput[GraphValueT]`，返回既有 durable binding +
`NodeInputFrame[GraphValueT]`；common coordinate construction 留在 caller。修订 target 的完整临时实现通过 strict Pyright、Ruff、
format 与同一 160-case scoped baseline，探针为 `13 insertions / 5 deletions`。行数不是门禁；决定性证据是 codec pipeline owner
`2 → 1`，branch/lookup/coordinate/command/frame/public surface 均不增长。探针随后全部恢复。

## 4. 恢复与工作树隔离证明

所有 S19/S22 production 探针均已通过 `apply_patch` 删除或反向恢复。最终 SHA256：

```text
src/mote_kernel/execution/executor.py
98f0a1725c9fd618cbd28bd6a8d28ef0985106915208b5a197ff202de4d66ebb

src/mote_kernel/execution/facade.py
d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9

src/mote_kernel/execution/invocation.py
5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4
```

它们与实施方案第 1 节 baseline 完全一致，Git diff 为零。未吸收或覆盖工作树中的 Makefile、README、requirements、主实施方案、
pyproject、其他 review/acceptance 文档、example 或 complexity 文件改动。

## 5. 本次 docs-only manifest 与下一步

本次 exact changed-file manifest：

```text
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-review-response.zh-CN.md
```

首次 review 文件保持原样，继续只裁决旧 implementation SHA。本回复完成后必须：

1. 计算并冻结实施方案与本回复最终 SHA；
2. 对实施方案新 SHA 做第二次独立技术评审；
3. requirements owner 分别裁决 S19 `APPROVED`、S21 `CLOSED — KEEP`、S22 `CLOSED — KEEP`；
4. 在明确裁决前不修改 production/tests；
5. 只有 S19 获批后，才能按实施方案两文件 manifest 独立实施和验收。

review 通过、probe 通过、本文回复完成或 empty manifest 均不等于 `GSP-A06` 已批准。
