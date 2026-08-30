# S19 / S21 / S22 实施方案第二次独立技术评审

> **结论：`PARTIAL PASS / S19 CHANGES REQUESTED / S21-S22 READY FOR PER-UNIT KEEP CLOSURE`。** 首轮 R1、R3、R4、R6、R7、R8 已闭合，R2 的结构与 applicability 账本也已闭合；S22 原 helper target 被正确撤回，S19 收窄后的 typed target 可实现且保持现有 owner、首错顺序和 State-command/frame 语义。但 S19 仍缺一个已被首轮 R5、整改回复和当前 §4.5 共同承诺的 case-level 证据：decoder 返回合法 `Graph.Values`、但其名称或精确值类型不匹配 compiled node-input descriptor 时，必须继续在 accumulator mutation 前由既有 frame admission 拒绝。S21、S22 可分别进入 requirements owner 的 `SATISFIED / CLOSED — KEEP` 裁决；S19 在该窄证据缺口回写并重新绑定 SHA 前不得获 `GSP-A06 APPROVED`，更不得实施 production/tests。

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S19 / S21 / S22 实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- 本次绑定 implementation owner SHA256：`d3ef4eabcd6e109fa0a6f08d8e175400719c8d12a41c49e535cea85f0da9459a`
- 同审对象：[首次独立技术评审回复](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review-response.zh-CN.md)
- 本次绑定 review response SHA256：`c888b80966c5ceee846914bdc0ecf7e056070f0c1957b78789e637e371b09548`
- 历史评审：[首次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review.zh-CN.md)，SHA256：`08dd6bf86ab8c5fe6b77f9b312aba2a71d5199a69c62a205e9f5583ee65d4eeb`
- 源码基线：Git `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad`
- production 基线：
  - `src/mote_kernel/execution/executor.py`：`98f0a1725c9fd618cbd28bd6a8d28ef0985106915208b5a197ff202de4d66ebb`
  - `src/mote_kernel/execution/facade.py`：`d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9`
  - `src/mote_kernel/execution/invocation.py`：`5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4`
- 本文性质：第二次独立 technical review record；只拥有本轮裁决、发现和验证证据，不拥有 exact target、requirements 批准状态、production shape 或 test shape。
- 本轮 actual change unit：只新增本文；保留并排除工作树中所有既有用户修改。

## 2. 审核边界与逐单元裁决

本轮继续执行用户明确边界：唯一事实、复用既有基础设施、零新增已知负债和高质量 typed code；不实现持久化、Store/backend、retry、fallback、checkpoint、failover、补偿事务或第二 recovery runner；automated complexity/baseline/ratchet 与 legacy/private-source-shape gate 均为 `USER-EXCLUDED / NOT RUN`。排除这两类 gate 不排除 current behavior、strict typing、owner/dependency、lint、format、coverage、package 和适用 pre-commit 义务。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、command、reducer、Store、protocol、持久化 | 通过 / `HARD KEEP` | S19 只复用既有 codec/command nominal types；S21/S22 production/test manifest 为空；没有第二 durable fact 或存储路径 |
| 错误恢复范围 | 通过 / 不扩展 | S19 不触碰 recovery runner；S21/S22 零 production diff；既有 fence/resume/partial-prefix 只作为冻结证据 |
| S19 exact target | 设计通过 | helper 只拥有重复的 encode → decode pair；caller 继续唯一拥有 validation、coordinate、command、replacement、admitted input 和 frontier simulation |
| S19 typing / owner / 净结构 | 通过 | 完整泛型；只新增一个 private method；无 DTO、alias、protocol、cache、context bag、export 或第二 owner；隔离 target probe 通过 |
| S19 case-level shape/tamper evidence | **不通过** | 合法 `Graph.Values` 的 decoded descriptor name/type mismatch 未登记 direct target predicate，R9 阻断 S19 `GSP-A06` |
| S21 | **通过** | lifecycle audit 支持 `KEEP / NO PRODUCTION CHANGE`；可由 requirements owner 独立记录 `SATISFIED / CLOSED — KEEP` |
| S22 | **通过** | private-plan helper 会净增 type/dispatch surface，撤回 target 是正确的零负债裁决；可独立记录 `SATISFIED / CLOSED — KEEP` |
| 门禁口径 | 通过 | complexity 与 legacy/private-shape 单独排除；其余行为、typing、owner、lint、format、coverage、package 门禁仍为 required |
| 总体 `GSP-A06` | 按单元分拆 | S19 未闭合；S21/S22 已达到 requirements owner per-unit `KEEP` closure 的技术评审前置条件 |

## 3. 首轮问题复核

### 3.1 R1、R7：S22 与错误恢复边界已闭合

旧 S22 helper 必须跨模块使用 `_PlannedFence` / `_PlannedResume` private nominal types，strict Pyright 会报 `reportPrivateUsage`；通过 export、alias、protocol、DTO、`Any`、`object` 或 string discriminator 修补都会扩大 type/owner surface。整改没有绕过类型系统，而是撤回 helper，将 S22 固定为 production/test 零 diff。该裁决同时消除了“不实现错误恢复”与“重构 recovery confirmation code”的范围歧义，R1、R7 均闭合。

### 3.2 R2、R3：per-unit 账本与 State-command 映射主体已闭合

第 7.1 节现已按 S19/S21/S22 分别登记 signature/nominal I/O、删除对象、最多新增对象、净结构、characterization、shape/tamper applicability、manifest 和 requirements-owner disposition。S19 也已将 `OverrideGraphNodeInput` / `ResumeGraphNodes` durable projection 显式映射到 `GSP-P02`，并冻结 codec identity/version、State revision 和 reducer projection。R2 的结构/applicability 口径与 R3 已闭合；R9 只针对 S19 case-level tamper predicate，不重开已闭合的结构账本。

### 3.3 R4、R5、R6：可复现门禁主体已闭合，但 R5 留有一个窄 case 缺口

格式门禁已统一为 Ruff；coverage 固定为排除整个 complexity gate 文件后的 full suite；package 统一复用 `make package-check`；`make check` 和 monorepo pre-commit 的 excluded/required 状态已拆账。第 4.5、5.3、6.4 节也已登记 existing exact nodeids，解决了旧版仅靠文件级命令的问题。

但首轮 R5 明确点名的 `decoded frame shape` 尚未进入 exact case matrix。该遗漏不推翻其余 R4–R6 整改，只形成下节 R9。

### 3.4 R8：S19 exact mechanics 与 lookup 账本已闭合

整改正确识别到公共 `AdmittedResumeInput` construction 在 baseline 本来就只有一个 caller owner，因此撤回了会制造第二 construction site 的旧 target。当前 target 只提取两个真实重复的 encode/decode sites，并明确保持：

```text
frontier lookup → StableActivation → materialization lookup #1
→ settlement / interrupt-ID validation
→ encode → decode / materialization lookup #2 / frame admission
→ command + replacement
→ existing common AdmittedResumeInput construction
→ simulated frontier validation
```

两个 lookup 分别属于 executor coordinate owner 与 decoder declaration owner；target 不新增 cache、plan forwarding、coordinate owner 或 mutation path。R8 闭合。

## 4. R9（仅阻断 S19）：decoded frame descriptor tamper 没有 case-level target predicate

当前实施方案 §4.5 的 exact matrix 覆盖：

- encoder 返回非 exact `bytes` 或抛异常；
- decoder 抛异常；
- decoder 返回非 `Graph.Values`；
- wrong settlement、stale interrupt ID、nominal failed/interrupt override、command/input shape 和 frontier validation。

这些证据都有效，但没有覆盖另一条独立 admission boundary：decoder 返回**合法、owner-produced `Graph.Values`**，其字段名集合或字段值 exact nominal type 与 compiled node-input descriptor 不一致。当前 production 的真实路径是：

```text
decode_resume_input
→ _require_decoded_values                 # 只确认 Graph.Values nominal owner
→ _admit_override
→ _make_node_input_frame / _admit_entries # 校验 descriptor 名称与 exact value type
```

一次性只读探针确认这两条边界当前分别产生：

```text
Graph.values(other="input")
→ GraphValueAdmissionError:
  node input names do not match the compiled descriptor: expected ('value',), got ('other',)
→ __cause__ is None

Graph.values(value=True)  # compiled descriptor 要求 exact str
→ GraphValueAdmissionError:
  node input value for 'value' does not have its exact declared type
→ __cause__ is None
```

`tests/execution/graph/test_values_contract.py` 对底层 frame owner 有局部 shape/type 证据，full coverage 也会执行该 owner；但它没有经过 resume decoder/executor admission，不能替代 `GSP-A06` 要求的 case-level target predicate。尤其是当前 response 第 3 节 R2、第 3 节 R5 和 implementation §4.5 已把 `malformed codec/tamper` / `decoded frame shape` 宣称为闭合，因此不能再把该路径视为未承诺的可选增强。

最小整改边界如下：

1. 在 implementation §4.5 已规划的
   `tests/execution/test_executor.py::test_override_resume_admission_preserves_validation_and_codec_order`
   中明确加入 typed/tamper decoder subcases：合法 `Graph.Values` 的 wrong names 与 wrong exact value type。
2. 固定 `GraphValueAdmissionError` 的分类、关键文本与 `__cause__ is None`；固定每个 subcase 仍为 validation 后 `encode → decode`，并在 command、replacement、admitted input 或 authoritative State/frame mutation 前失败。
3. 保持一个既有 planned test nodeid时，`834 passed` target count 可以不变；若拆成多个 nodeid，必须同步更新 exact manifest、nodeid matrix 和 full-suite target count。
4. 不修改 `engine/resume_input.py`、`graph/values.py` 或其他 production；不新增 validator、codec port、test file、legacy/private-shape assertion 或 complexity gate。S19 planned manifest 仍应只有 `executor.py` + `test_executor.py`。

这是一项 docs-only evidence 修正，不要求现在实施测试。回写后必须计算新的 implementation SHA，并进行下一次独立复核；首次 response 的当前 SHA 应保留为历史 disposition，由后续 response 明确承认 R9，而不是把历史记录静默改写成已通过。

## 5. 独立验证证据

### 5.1 当前 baseline

production 与测试基线 SHA 均与 implementation 第 1 节一致，五个相关 production/test 文件相对 Git baseline 无 diff。本轮复核结果：

```text
S19/S21/S22 scoped six-file baseline
→ 160 passed

implementation 登记的 existing exact nodeids
→ 28 passed

full suite excluding tests/architecture/test_complexity_gate.py
→ 833 passed
→ line / branch coverage 100.00%

full strict Pyright
→ 0 errors, 0 warnings, 0 informations

Ruff lint / Ruff format --check / git diff --check
→ passed
```

上述 baseline 证明当前 behavior 与 active owner，没有证明尚未实施的 S19 target 已交付。

### 5.2 S19 exact target 隔离探针

按 §4.2 exact method 与两处 caller replacement 临时应用 target，得到：

```text
actual production diff     → 13 insertions / 5 deletions
strict Pyright             → passed
Ruff lint / format         → passed
scoped baseline            → 160 passed
```

探针随后完整恢复；`executor.py` SHA 回到 `98f0a172...`，production 当前无 diff。该结果证明 target signature 与依赖方向可实现，不替代 R9 所要求的 target behavior predicate，也不构成 implementation 或 requirements approval。

### 5.3 未消费的门禁

本轮没有运行、读取、修改或用作证据的 automated complexity/ratchet 与 legacy/private-source-shape gate。`make check` 因无条件包含被用户排除的 complexity target，未冒记为整体通过。S19 尚未获实施授权，因此 `make package-check`、target `834 passed` 和 implementation-file pre-commit 属于实际 implementation unit 的未来必需门禁，本 review 不把未运行项写成通过。

## 6. 当前状态与合法后续顺序

```text
S19: DIRECTION + EXACT TARGET SOUND / R9 EVIDENCE WRITEBACK REQUIRED
S19 GSP-A06: NOT READY / NOT APPROVED / NOT IMPLEMENTED

S21: PASS / READY FOR REQUIREMENTS OWNER SATISFIED / CLOSED — KEEP
S21 PRODUCTION / TEST MANIFEST: EMPTY

S22: PASS / READY FOR REQUIREMENTS OWNER SATISFIED / CLOSED — KEEP
S22 PRODUCTION / TEST MANIFEST: EMPTY

STATE / STORE / PROTOCOL / PERSISTENCE / ERROR-RECOVERY FEATURES:
HARD KEEP / UNTOUCHED

AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES:
USER-EXCLUDED / NOT RUN
```

三个 disposition unit 不继承彼此状态。requirements owner 可以在当前 reviewed SHA 上分别关闭 S21、S22；不得借此批准 S19。S19 owner 应只做第 4 节的 docs-only case predicate 回写，生成后续 review response 并重新绑定 implementation SHA；第三次独立评审通过后，requirements owner 才能考虑 S19 `GSP-A06 APPROVED`。在此前不得修改 production 或 tests。

## 7. 本次 review change unit

本文是本次第二次独立评审的唯一 actual changed-file：

```text
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review.zh-CN.md
```

本文不修改 implementation owner、首次 review/response、requirements、主实施方案、production、tests、State、Store、protocol、persistence 或任何 gate artifact，也不把本 review 变成 target shape 或批准状态的第二事实源。
