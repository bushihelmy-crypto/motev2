# S16 `GSP-A06` 单项实施设计独立技术评审

> **结论：`CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL`。** S16 的 `pairwise` 方向在声明的 exact nominal coordinate 域内成立，且没有引入 State、持久化、错误恢复策略、第二执行路径或第二事实源；但当前文档的可复核证据尚未闭合：验证命令含有不存在的 nodeid，exact target 代码块不能通过当前 Ruff format check，新增四个 canonicality 分支的 behavior/coverage 计划漏掉 resume-input 与 child-boundary 两个分支。本记录不批准 `GSP-A06`，不授权修改 production/tests/normative source/requirements，也不新增 legacy 或 complexity gate。

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- 本轮绑定 owner SHA256：`8eb7bc7da3feb3f4440cf363670cff39b90453616515dbbd2ea479ad39aed163`
- 声明源码基线：Git `f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497`
- production 基线 SHA256：`src/mote_kernel/execution/invocation.py` → `4165f689af384f9b91080b432328ce3003f9e4b4308bcf34960e1d3db0550f5d`
- frame owner 基线 SHA256：`src/mote_kernel/execution/run_context.py` → `bf196695bce1687f0bd9554d3a8615e9afc5dbfa1bedbc859cd199e8ff54f648`
- behavior 基线 SHA256：`tests/execution/test_continuation_integrity.py` → `d900f3b812f9618587182ed4e86974cfc7dc0d3fa0de647ad9f797693bdaa17e`
- 本文性质：独立 technical review record；只拥有本轮裁决和验证证据，不拥有 S16 target shape、requirements 批准状态或 production/test shape
- 本轮 actual manifest：只新增本文；保留用户工作树中的其他修改，不把它们纳入 S16

## 2. 审核边界与总体裁决

本轮按用户给定边界审核：保持唯一事实源、复用现有 execution/frame 基础设施、零新增结构/所有权负债；不实现持久化，不新增 retry、fallback、checkpoint、failover、第二 recovery runner 或其他错误恢复能力。automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均不参与本轮裁决，也没有新增或扩写这些 gate。文档自己列明的 current behavior、strict typing、owner/dependency、lint、format、coverage 和 package 条件仍按其原口径检查。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、command、reducer、Store、protocol、持久化 | **通过 / HARD KEEP** | target 只替换 `invocation._validate_frame_index()` 的 canonicality 检查；planned manifest 没有 State/Store/protocol/persistence diff |
| 错误恢复范围 | **通过 / 不扩展** | 只保持既有 continuation admission 与 recovered preflight；不增加 retry、failover、checkpoint 或第二 runner |
| 唯一事实与基础设施复用 | **通过设计** | `run_context.py` 继续拥有四类 record/coordinate/`ScopedFrameIndex`；`invocation.py` 继续拥有 phase 编排；没有新 DTO、index、cache 或 validator |
| nominal-domain 等价性 | **通过（有界）** | 在 compiler-produced、hash/total-order 一致的 coordinate 域内，`unique + sorted` 与严格相邻递增等价 |
| public phase/error 顺序 | **通过设计，待 target evidence** | shape → canonicality → content 与四段顺序可以保持；尚未由完整 target branch matrix 和可复现命令闭合 |
| verification reproducibility | **不通过** | §13.2 有错误 nodeid；§5.3 exact code block 未通过当前 Ruff format；coverage 计划漏分支 |
| `GSP-A06` / 是否可实施 | **未批准** | 修正文档/evidence 后须重新绑定 owner SHA 并再次独立评审 |

## 3. 阻断问题

### R1（阻断）：target 分支 coverage 计划不闭合

文档 §5.3 把一个 phase loop 展开为四个独立的 `if any(...)`，每个 `raise` 都是新的可执行分支。当前 baseline 只有
`test_complete_continuation_rejects_duplicate_frame_coordinates` 命中 graph-input canonicality 分支；§8.2 计划的五个 case
中，publication 分支最多由两个 publication precedence case 命中，但没有任何 case 反转两个 resume-input coordinates，或反转两个
child-boundary coordinates。

我把 §5.3 target 应用到 `/tmp` 临时源码副本（未修改仓库），先按文档现有 suite 运行 §13.3 命令：

```text
826 passed
coverage 99.90% (invocation.py missing lines: canonical publication, resume input, child boundary raises)
```

在临时副本补上 publication、resume-input、child-boundary 三个 public `Graph.run()` descending cases 后，才得到：

```text
829 passed
coverage 100.00%
```

这三个临时 case 是针对 baseline 的 branch probe；其中 publication 已经由 §8.2 现有的两个 publication precedence case 计划覆盖，
因此在原有五个 case 基础上实际还需要新增 resume-input 与 child-boundary 两个 case（总数至少七个）。这不是要求增加
legacy/private-source-shape 测试，而是覆盖 target 自身新增的 public error branches。当前 §8.2 的五个 case、§12 第 3 步“增加五个
case”和 §13.3 的 `--cov-fail-under=100` 不能同时成立。必须在 owner 文档中：

1. 增加 resume-input 与 child-boundary 两个 exact public-run case，并确认既有 publication cases 确实命中 publication branch（或明确等价的 branch-complete 设计）；
2. 为每个 case 固定完整 `Graph.SnapshotMismatchError` 文本、构造方式、首错阶段和 mutation-free 断言；
3. 同步更新 case 数、implementation step 与 baseline/target coverage 说明；
4. 不通过 coverage omit、pragma、复杂度文件或 legacy/private-source-shape gate 掩盖缺口。

若不增加这些 behavior cases，必须重新设计 target，使 canonicality 不产生未覆盖的独立 production branches；不能把 100% coverage
继续写成 target 的交付条件。

### R2（阻断）：§13.2 的 architecture nodeid 不存在

文档把最后一个 nodeid 写成：

```text
tests/architecture/test_graph_execution_ownership.py::test_execution_is_the_only_generic_executor_owner
```

该命令实际失败：测试定义位于
`tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner`，而不是
`test_graph_execution_ownership.py`。按文档逐字执行会在 collection 阶段得到 `ERROR: not found`，因此 §8.1 的“9 个 active
architecture/source/owner nodeids → 9 passed”不能由当前命令复现。

将路径修正为 `test_source_discipline.py` 后，9 个 nodeid 实际为 `9 passed`。这是一个必须回写到 owner 文档的可复现性错误，不是
complexity 或 legacy gate 问题。

### R3（阻断）：§5.3 所称“精确替换”与必需 format gate 冲突

将 §5.3 代码逐字应用到临时 `invocation.py` 后，当前仓库 Ruff `0.16.2` 的结果是：

```text
ruff check → passed
ruff format --check → failed (File would be reformatted)
```

Ruff 会把前三个 `any(pairwise(...))` 压成单行，并重新折叠第四个生成器表达式。格式化后的临时 target 可以通过
`ruff format --check`，但它已不再是文档所称的“精确代码块”。owner 必须选择且写明一个唯一事实：

- 直接把 Ruff canonical output 回写到 §5.3；或
- 明确 §5.3 是语义伪代码，并把 source review predicate 改为允许 formatter 的等价布局。

不能一方面要求 §13.2 format gate，另一方面把无法通过该 gate 的代码块绑定为 exact target。该修正不需要新增 helper、gate、import 或
production 文件。

## 4. 需收紧但不否定算法方向的事项

### R4：tamper/equivalence 证据必须明确 nominal 边界

§6 的等价证明对 exact coordinate class、canonical scalar、hash 与 total ordering 一致的域成立；这正是本 target 可接受的核心域。
但 §8.2 的 inner-tamper probe 仍是 `PENDING`，而 §15 又写成“设计阶段 requirements 已闭合”。这两处口径不一致。

一个 exact outer record/coordinate class 但含不可 hash 的 forged inner descriptor field 的输入，当前实现会在
`len(set(segment))` 处抛 `TypeError`；adjacent target 可能继续到 typed `SnapshotMismatchError`。这类对象不属于文档声明的
compiler-produced nominal domain，不能反向要求 target 加 catch/normalizer；但文档必须二选一并回写：

1. 记录对 §8.2 所列 descriptor/scope/activation/node/enum-int canonical-scalar tamper probe 的 baseline-vs-target 结果，并明确
   forged unhashable/mixed object 不在 contract；或
2. 删除“所有 malformed/tamper error type/text 均保持”的宽泛表述，只保留 exact nominal-domain 结论和停止条件。

不得以“private input”静默扩大或缩小现有 public malformed contract，也不得新增 legacy/private-shape gate 来替代这项边界说明。

### R5：结构账本的计数口径需要去歧义

§7 同时列出 `canonicality sorted(...) constructions: 4` 和 `canonicality sorted-result tuple copies: 4`，随后又说“sorted-result
tuple copies”只是前一项的说明，并把旧构造总数算成 13。表格的四个净变化若直接求和会得到不同的删除总量。请把第二行改成非计数注释，或
明确标注“不可加总”，使 zero-debt ledger 能由第三方机械复算。此项不要求复杂度 gate，也不改变 target algorithm。

### R6：mutation-free 与完整错误 literal 的 evidence 需要 exact 化

§8.2 的表格列出“必须断言”但多数只写错误后缀或“先出现”，没有固定完整 message、`__cause__`、commit callback 计数、node/resource
调用计数的具体断言路径。由于 S16 映射了 `GSP-P03`/`P04`/`P06`，至少应明确复用哪个既有 `CommitLog`/call-counter fixture，或在同一
existing continuation-integrity test file 中登记这些断言。不得新增永久 source-shape/AST gate；这里只补 public behavior evidence。

## 5. 已通过的技术复核

### 5.1 baseline 与 owner 边界

第 4.5 节列出的三份 production/behavior SHA 与当前 `HEAD` 对照一致；三份目标文件相对
`f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497` 无 diff。当前基线实际复跑结果为：

```text
tests/execution/test_continuation_integrity.py → 34 passed
tests/execution                              → 563 passed
tests/state/graph_state                       → 206 passed
all tests excluding tests/architecture/test_complexity_gate.py → 826 passed, 100.00% coverage
pyright                                      → 0 errors, 0 warnings, 0 informations
ruff check (current invocation.py + continuation test) → passed
ruff format --check (current files) → 2 files already formatted
```

按修正后的 architecture nodeid 命令运行得到 `9 passed`。完整 `make check` 未运行，因为它无条件包含用户明确排除的
`complexity-ratchet`；本轮没有运行、添加或修改 complexity/legacy gate。

### 5.2 nominal target 隔离 probe

在 `/tmp` 临时副本中应用 §5.3 target（只修改 invocation.py，不写回仓库）后：

```text
tests/execution/test_continuation_integrity.py → 34 passed
tests/execution → 563 passed
tests/architecture -k 'not complexity' → 56 passed, 7 deselected
pyright invocation.py → 0 errors, 0 warnings, 0 informations
ruff check → passed
```

该 probe 说明 `pairwise` 的 strict-adjacent 实现可以在现有 nominal/generic owner 边界内 typecheck，并保持当前 execution 行为；
它不证明 §8.2 尚未登记的 target cases、requirements approval 或 production implementation。临时 copy、测试和 coverage output
均不属于本次 manifest。

## 6. 复核后的 target 评价与后续顺序

以下结论不受 R1–R6 文档/evidence 问题影响，应保留：

- `_validate_frame_index()` 继续是唯一 continuation frame-index 编排 owner；不拆出第二 validator 或 recovery runner；
- `run_context.py`、四类 record/coordinate、`ScopedFrameIndex`、State/Store/protocol/commit/recovery owner 均保持不变；
- 在声明的 exact nominal total-order 域内，`previous.coordinate >= current.coordinate` 与原 unique+sorted predicate 接受/拒绝集合一致；
- `pairwise` 不读取 concrete frame/value、不写 State、不安装 frame、不触发 node/resource/commit；
- complexity 与 legacy/private-source-shape gate 继续 `USER-EXCLUDED`，不因本 review 产生任何新 gate 或治理文件。

整改只应发生在 S16 owner 文档的独立 docs-only unit 中。整改后必须重新计算 owner SHA，并重新审核 §5.3 code block、§8.2 case
matrix、§13.2 nodeids、§13.3 coverage 和 §7 ledger；在 requirements owner 对新 SHA 显式授予 `GSP-A06` 前，不修改：

```text
src/mote_kernel/execution/invocation.py
tests/execution/test_continuation_integrity.py
src/mote_kernel/execution/run_context.py
State/Store/protocol/persistence artifacts
```

## 7. 当前状态与 review manifest

```text
S16 DESIGN: CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL
S16 TARGET NOMINAL ALGORITHM: CONDITIONALLY SOUND
S16 EVIDENCE: COVERAGE / NODEID / FORMAT ITEMS OPEN
S16 GSP-A06: NOT APPROVED
S16 PRODUCTION / TEST / NORMATIVE IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
AUTOMATED COMPLEXITY GATE: USER-EXCLUDED
LEGACY / PRIVATE-SOURCE-SHAPE GATE: USER-EXCLUDED
```

本文件是本轮独立 technical review 的唯一 actual changed-file：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-review.zh-CN.md
```

它不修改 S16 owner 文档、requirements、主实施方案、production、tests 或任何门禁 artifact；不把 review record 变成 target 或批准状态的
第二事实源。
