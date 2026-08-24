# S15 `GSP-A06` 单项实施设计独立技术评审

> **结论：`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`（仅表示当前 exact target 的设计与证据通过技术复核）。本记录不批准 `GSP-A06`，不授权修改 production、tests、normative source、State、Store、protocol 或任何持久化路径。S15 继续保持 `GSP-A06 NOT APPROVED`。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S15 Recovery worklist 分支结果归一化实施方案](graph-semantics-preserving-simplification-s15-implementation.zh-CN.md)
- 本轮绑定 owner SHA256：`1e6629dfacad43ed1c87036fbc9f6589f606a220592c5fde3fadba068172e85a`
- 声明源码基线：Git `7247a93485f30746638a5168e06be8766a64a120`
- 基线 production 文件 SHA256：`d77df79d3f94ca973b29945a3abddf3382b280ed8da7f208ad455757c1d9514e`
- 复核对象：`src/mote_kernel/execution/engine/recovery.py::_prove_scope()`；基线与当前 `HEAD`、文件 SHA 一致
- 本文性质：独立 technical review record；只拥有本轮裁决和验证证据，不拥有 S15 target shape、requirements 批准状态或 production/test shape
- 本轮 change unit：只新增本文；不修改 owner、主实施方案、requirements、normative source、production、tests、State、Store、protocol 或 persistence

## 2. 审核边界与总裁决

本轮按用户给定边界复核：保持唯一事实源、复用现有 execution/recovery 基础设施、零新增负债；不实现持久化，不新增 retry、failover、checkpoint 或第二 recovery runner；automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均不作为 S15 准入条件。排除这些治理门禁不等于跳过当前行为、严格类型、owner/dependency、lint、format、build/package 和 no-persistence 契约检查。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、command、reducer、Store、protocol、持久化 | **通过 / HARD KEEP** | target 仅改 `recovery.py::_prove_scope()` 的 invocation-local loop 编排；不改变 State、commit、revision、codec、Store 或 persistence |
| 错误恢复范围 | **通过 / 不扩展** | 只保留现有 mutation-free recovery preflight；没有自动 retry/backoff、failover、checkpoint、错误重分类或第二 runner |
| 唯一真相与基础设施复用 | **通过** | `pending`、`seen`、`boundaries`、`sequence`、budget 仍由 `_prove_scope()` 唯一拥有；三个既有 expansion owner 原样复用 |
| branch precedence | **通过设计** | `seen → completed → aborted → awaiting → active → settled → executable` 的 predicate 和首错顺序不变 |
| budget、queue、dedup、boundary | **通过设计** | 只把三处消费归一为一个 typed tuple pipeline；admit 时点、4096 上限、heap key、sequence、full-semantic seen 和 final sort 不变 |
| nominal typing / source discipline | **通过设计** | 只使用现有 `_RecoveryWorkItem`、`_ScopeBoundary` 和 `GraphValueT`；无 `Any`、`object`、cast、DTO、alias、callback、反射或新 import |
| zero-debt structural ledger | **通过设计并独立复算** | 19 insertions / 24 deletions；decision points、semantic nodes、batch loop、重复 admit/enqueue 均净减少；无新增 owner 或 stored fact |
| behavior / malformed / nested evidence | **通过** | 当前 focused/public/architecture cases 与隔离 exact-target probe 均通过；异常由原 shared owner 原样传播 |
| `GSP-A06` 状态 | **未批准** | 本轮只允许进入 requirements owner approval；不授权 production 或 tests |

没有发现需要开立的技术阻断项。以下“通过”均限定为当前 owner SHA 的设计复核，不等于已实施或 requirements 自批准。

## 3. Exact target 技术复核

### 3.1 当前 owner 与 branch precedence

基线 `_prove_scope()` 在 `recovery.py:1015–1085` 具有一个 local batch `enqueue`，并在 active、settled、executable 三个非 terminal 分支分别完成 budget、boundary/work-item 判别和入队。其 terminal/awaiting 分支、`seen` 检查和最终 boundary sort 位于同一 loop owner 内。

目标的变化严格限定为：

1. 将现有 local `enqueue(items: tuple[_RecoveryWorkItem[GraphValueT], ...])` 收窄为 scalar `enqueue(candidate: _RecoveryWorkItem[GraphValueT])`，删除 closure 内 batch loop；
2. 用 invocation-local 的两成员 nominal tuple 统一 active、settled、executable producer 的消费；settled 只做 singleton 包装；
3. 在唯一 common site 执行 `family.budget.admit(len(successors))`，再按 tuple 原序将 boundary 放入既有 set、work item 交给 scalar enqueue。

terminal `COMPLETED`、`ABORTED`、`AWAITING_RESUME` blocks 不动。active 仍先于 settled，settled 仍先于 executable；malformed state 仍沿相同首错路径。目标没有引入 branch tag、dict dispatch、match、独立 handler 或第二 classification projection。

### 3.2 budget 与错误时点

当前与目标的 admitted count 均为：initial `1`；active `len(_expand_live(...))`；settled `1`；executable `len(_expand_quiescent_executable(...))`。目标的 common `admit` 发生在任何 boundary set 或 heap 写入之前，因此：

- 4096 safety budget 仍由 `_RecoveryProofBudget` 唯一拥有；
- `ExecutionLimitError("recovery proof exceeded its bounded transfer-state budget")` 的 type、text 和时点不变；
- expansion、planner、routing、nested、materialization、resource、reducer 和 claim 错误不被捕获或重分类；
- settled boundary 的 singleton 包装不产生可观察副作用，且仍在 budget admission 之前完成与当前 resolution 相同的 nominal result。

### 3.3 queue、ordering 与 full-semantic dedup

基线 batch enqueue 对 `(x0, ..., xn)` 逐项执行 `sequence += 1`、`recovery_traversal_key(_transfer_state(...))` 和 `heappush`。目标的 outer loop 对同一 tuple 原序调用 scalar enqueue，因此每个 work item 的 key、sequence tie-break、heap entry 和 `heappop` 顺序保持一致。Boundary 从不进入 heap，work item 从不进入 boundary set。

`seen` 仍在 branch dispatch 之前以完整 `RecoveryTransferState` 做 equality/hash；`RecoveryTraversalKey` 仍只是排序投影，不取得 dedup 语义。没有 set/dict partition、二次扫描、先排序再入队或新 index。mixed boundary/work-item、parallel completion、repeated child 和 nested limit 的既有 cases 覆盖了这些可观察关系。

### 3.4 类型边界与唯一事实

目标 union 精确为：

```python
tuple[_RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT], ...]
```

两个成员都是现有 nominal types；不把结果擦除为 `object`/`Any`，不增加 result DTO、protocol、type alias 或 callback。tuple 只在当前 loop iteration 内存在，不进入 State、Continuation、Graph instance、equality、hash、cache 或 persistence。

`_expand_live()`、`_resolve_quiescent()`、`_expand_quiescent_executable()` 继续分别拥有 active success-route、settled routing/completion、planner/resource/nested/limit mechanics；`_prove_scope()` 仍是唯一 worklist/seen/budget/boundary owner。这满足“复用基础设施 + 单一事实”，没有建立第二解释器。

## 4. 零新增负债账本复核

我按 owner 文档的计数口径，从基线源码重建了 exact textual target（仅在临时副本中替换第 5 节代码；未修改仓库源码），结果如下：

| 结构项 | Before | Target | 结果 |
| --- | ---: | ---: | ---: |
| module-level function definitions | 23 | 23 | 不增长 |
| `_prove_scope()` decision points | 13 | 11 | `-2`（删除一个 batch `for` 和一个重复 dispatch `if`） |
| `_prove_scope()` semantic AST nodes | 351 | 323 | `-28` |
| unique `ast.Name(Store)` identities | 14 | 13 | `-1`（删除 batch-loop `candidate` identity） |
| enqueue batch loop | 1 | 0 | `-1` |
| non-initial budget admit sites | 3 | 1 | `-2` |
| total budget admit sites in `_prove_scope()` | 4 | 2 | `-2` |
| textual enqueue call sites | 3 | 1 | `-2` |
| successor boundary discriminator sites | 2 | 1 | `-1` |
| existing expansion owner calls | 3 | 3 | 保持 |
| new function/class/DTO/field/cache/index/import/export | 0 | 0 | 保持 |

`ast.Name(Store)` 上表明确指“去重后的 identity 名称”；原始 AST occurrence 数为 17→17，不能把两种口径混写。该说明不构成永久 AST gate，只保证 owner 的 ledger 可复算。独立 diff 计数为 `19 insertions / 24 deletions`，与 owner 文档一致。

## 5. Behavior、typing 与 gate evidence

### 5.1 当前仓库基线

在声明的 `HEAD`/production SHA 上实际运行：

```text
focused recovery:
  tests/execution/engine/test_recovery_identity.py
  tests/execution/engine/test_recovery_boundaries.py
  tests/execution/test_graph_recovery_contract.py
  → 53 passed

第 13.2 节列出的 active architecture nodeids
  → 10 passed

tests/execution
  → 563 passed

tests/state/graph_state
  → 206 passed
加上 state single-owner architecture case → 207 项相关证据

pyright
  → 0 errors, 0 warnings, 0 informations

python -B -m ruff check src/mote_kernel/execution/engine/recovery.py
python -B -m ruff format --check src/mote_kernel/execution/engine/recovery.py
  → passed

python -B -m build --no-isolation
python -B -m twine check dist/*
  → build succeeded; both artifacts PASSED twine check

tracked/untracked whitespace checks for the S15 docs/index unit
  → passed
```

这些是当前 production/docs baseline，不冒充 target 已落地。

### 5.2 隔离 exact-target probe

为避免修改用户工作树，我把 owner 第 5 节的 exact replacement 应用到 `/tmp` 临时源码副本，随后运行相同 recovery contract、execution 和 active architecture checks：

```text
focused recovery on transformed copy → 53 passed
tests/execution on transformed copy   → 563 passed
tests/architecture (complexity case excluded) → 56 passed
pyright recovery.py                  → 0 errors, 0 warnings, 0 informations
ruff check / ruff format --check     → passed
```

该 probe 只证明 target 可在现有 nominal/type/behavior 边界内编码；它不是 production implementation、T0 approval 或 `GSP-A06` approval。没有把临时副本、测试、complexity artifact 或 build output 写入 S15 manifest。

### 5.3 门禁边界

- automated complexity、health、baseline、ratchet、limit 和 complexity hook 按用户范围 **USER-EXCLUDED**；本轮没有运行 `make check`，因为其 `check` 目标无条件包含 `complexity-ratchet`，不能运行后忽略再冒记为通过；
- legacy/private-source-shape gate **USER-EXCLUDED**；没有新增或修改 AST/source-layout test，也没有把 local 名称、行数、表达式布局列为永久准入；
- current recovery behavior、strict typing、active owner/dependency/source discipline、lint、format、build/package 与 no-persistence negative evidence 仍是 **REQUIRED**；
- 当前工作树中与 S15 无关的 Makefile、README、requirements、主实施方案、complexity files、examples 和历史 review 文件不属于本 review change unit，也没有被用于替代 S15 evidence。

## 6. 非阻断保持条件

1. requirements owner 必须对本评审绑定的 owner SHA `1e6629…`（完整值见第 1 节）显式授予 `GSP-A06`；本 review 不得自批准。
2. approval 前不得修改 `recovery.py`、任何 tests、State/protocol/Store、normative source 或 requirements；若 owner 文档 SHA、production baseline、union、admit 时点或 manifest 改变，必须重新评审。
3. approval 后 production implementation 仍只能是 `src/mote_kernel/execution/engine/recovery.py` 一个文件；不得以同步文档、target test、complexity baseline 或 legacy gate 扩大 change unit。
4. implementation writeback 必须把 actual diff/source review 与 gate 结果回写到 owner 文档，并保持 review record、approval unit、production unit、writeback unit 的 Git 顺序；不能把当前临时 probe 写成 production evidence。
5. “零负债”继续解释为**零新增结构/所有权/持久化/执行路径负债**，不是借排除 automated complexity 去保留旧 batch mechanics 或创建新的 wrapper/DTO。

以上是保持条件，不是新增 gate；它们只防止 reviewed exact target 漂移。

## 7. 最终状态与 review manifest

```text
S15 TECHNICAL DESIGN REVIEW: PASS / READY FOR REQUIREMENTS OWNER APPROVAL
S15 GSP-A06: NOT APPROVED
S15 PRODUCTION / TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
NEW ERROR-RECOVERY / RETRY / FAILOVER POLICY: FORBIDDEN
AUTOMATED COMPLEXITY GATES: USER-EXCLUDED
LEGACY / PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED
CURRENT BEHAVIOR / TYPING / OWNER / PACKAGE CHECKS: REQUIRED
```

本文件是本轮 independent review 的唯一 actual changed-file：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s15-implementation-review.zh-CN.md
```

它不复制 S15 target、不修改 requirements 或主实施方案，也不把任何生产/测试/持久化变更伪装成已批准。下一合法步骤是 requirements-only 的显式 `GSP-A06` approval；不是直接编辑 `recovery.py`。
