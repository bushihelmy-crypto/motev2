# S15 Recovery worklist 分支结果归一化代码验收

> **最终裁决：`PASS / IMPLEMENTED / VERIFIED`**。本记录只验收已经获得 `GSP-A06` 明确授权的 S15 exact target；不扩大批准范围，不批准任何 State、Store、持久化、自动错误恢复或第二执行路径变更。

## 1. 验收对象与授权边界

- 验收日期：2026-08-24
- 单元：Graph 语义保持型简化 S15（P2）
- 设计 owner：[S15 Recovery worklist 分支结果归一化实施方案](graph-semantics-preserving-simplification-s15-implementation.zh-CN.md)
- 独立技术评审：[S15 implementation review](graph-semantics-preserving-simplification-s15-implementation-review.zh-CN.md)
- approved design SHA256：`1e6629dfacad43ed1c87036fbc9f6589f606a220592c5fde3fadba068172e85a`
- requirements 授权：2026-08-24，requirements owner 将 `GSP-A06` 标记为
  `SATISFIED / APPROVED`，且只授权上述 reviewed SHA 与单文件 production manifest。
- 声明源码基线 Git：`7247a93485f30746638a5168e06be8766a64a120`
- production implementation commit：`4b8f3727644687ffd63d6bdd7e0dc4159274b892`
- 基线 `recovery.py` SHA256：`d77df79d3f94ca973b29945a3abddf3382b280ed8da7f208ad455757c1d9514e`
- 实际 `recovery.py` SHA256：`40d0fff5240f8ce479006699e07f2a767ecfc533d889c86244e48589794e28df`

本次 production implementation manifest **只有**：

```text
mote-kernel/src/mote_kernel/execution/engine/recovery.py
```

验收记录与 owner writeback 是独立的审计文档，不属于 production manifest；工作树中的其他既有修改均不纳入 S15。

## 2. 实际变更与 exact target 核对

源码 diff 统计为 **19 insertions / 24 deletions**（净删 5 行），且所有变更均位于
`recovery.py::_prove_scope()`：

1. local `enqueue` 从 batch tuple 输入收窄为单个 `_RecoveryWorkItem`，保留原有 transfer key、sequence 和 heap entry 构造顺序；
2. active、settled、executable 三个既有 producer 统一投影到
   `tuple[_RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT], ...]`；
3. 保留一个 common `budget.admit(len(successors))` 和一个按原序 dispatch 的 boundary/work-item 消费点。

已核对：函数签名、公共入口、三个 existing expansion owner、terminal/awaiting 分支、`seen`、heap、canonical sort、错误类型与错误文本均未改变。未新增 handler、DTO、alias、protocol、callback、import、字段、缓存、索引、测试或 public export。

## 3. 零新增负债结构账本

按 S15 owner 文档同一 AST/source probe 复算实际源码：

| 结构项 | Before | Actual | 结果 |
| --- | ---: | ---: | ---: |
| module-level function definitions | 23 | 23 | 不增长 |
| `_prove_scope()` decision points | 13 | 11 | `-2` |
| `_prove_scope()` semantic AST nodes | 351 | 323 | `-28` |
| 去重后的 `ast.Name(Store)` identities | 14 | 13 | `-1` |
| `enqueue` 内部 batch loop | 1 | 0 | 删除 |
| non-initial budget admit sites | 3 | 1 | `-2` |
| `_prove_scope()` total budget admit sites | 4 | 2 | `-2` |
| enqueue call sites | 3 | 1 | `-2` |
| successor boundary discriminator sites | 2 | 1 | `-1` |
| existing expansion owner calls | 3 | 3 | 保持 |
| 新 module/local owner、DTO、field、cache、index、import、export | 0 | 0 | 保持 |

该账本是本次一次性 source review 证据，不转化为 legacy/private-source-shape 永久门禁。

## 4. 行为、错误与所有权验收

以下不变量与批准 target 一致：

- branch precedence 仍为 `seen → completed → aborted → awaiting → active → settled → executable`；
- initial item 仍先 `admit(1)`；非 terminal successor 仍在任何 boundary/heap 写入前统一 admission；4096 bounded budget 与 `ExecutionLimitError` 边界不变；
- full-semantic `RecoveryTransferState` dedup、canonical traversal key、sequence tie-break、mixed boundary/work-item 原序和最终 boundary sort 不变；
- completed history 缺失、active 无 legal live task、planner/routing/nested/materialization/resource malformed 等异常仍由原 shared owner 以原类型/文本/时点传播；没有新增 `try/except`、fallback、retry、failover、错误映射或 logging side effect；
- `_prove_scope()` 仍是唯一 worklist/seen/budget/boundary owner，复用 `_expand_live()`、`_resolve_quiescent()` 与 `_expand_quiescent_executable()`；没有第二 runner 或第二事实源；
- 未触及 `src/mote_kernel/state/**`、State tests、command/reducer、commit、protocol、Store 或 persistence path；不新增持久化、不把 in-memory proof 写成 durability 或 crash-recovery 保证。

## 5. 验证结果

在实际源码 SHA `40d0fff…` 上运行：

```text
focused recovery contract                         53 passed
active architecture contract                      10 passed
tests/execution                                   563 passed
tests/state/graph_state                            206 passed
all tests, excluding tests/architecture/test_complexity_gate.py  826 passed
pyright                                             0 errors, 0 warnings, 0 informations
ruff check recovery.py                              passed
ruff format --check recovery.py                     1 file already formatted
python -B -m build --no-isolation                    succeeded
python -B -m twine check dist/*                      both artifacts PASSED
SKIP=kernel-complexity pre-commit (recovery.py)      passed
git diff --check (recovery.py)                       passed
```

architecture focused cases覆盖 generic boundary、dependency direction、single execution/State owner、no-persistence 与 source discipline；行为集覆盖 budget、dedup、canonical ordering、terminal/awaiting、parallel/nested/limit 及 malformed/error paths。

## 6. 门禁口径与未运行项

- `current behavior`、strict typing、owner/dependency/source discipline、lint、format、build/package、no-State/no-persistence 和 scoped repository checks：**REQUIRED / PASS**。
- automated complexity、health、baseline、ratchet、limit 与 complexity hook：按用户授权 **USER-EXCLUDED**，不作为 S15 准入或交付证据。
- legacy/private-source-shape gate：按用户授权 **USER-EXCLUDED**；没有新增或修改此类测试。
- 未运行完整 `make check`：其 `check` 目标无条件包含被排除的 `complexity-ratchet`，因此不能将该命令记录为 S15 通过依据。已用上述 scoped checks 覆盖其余适用条件。

## 7. 交付状态

S15 exact target 已实现并通过本记录列出的验收。Production 已作为独立 commit `4b8f372` 落地；本验收记录仍是独立的
docs-only audit unit，不属于 production manifest。当前工作树仍有用户既有的无关变更；提交过程没有执行 reset、stash 或其他
破坏性操作，也没有把无关文件纳入 S15。

```text
S15 GSP-A06: SATISFIED / APPROVED (requirements owner; reviewed SHA only)
S15 IMPLEMENTATION: IMPLEMENTED / VERIFIED
S15 PRODUCTION COMMIT: 4b8f3727644687ffd63d6bdd7e0dc4159274b892
PRODUCTION MANIFEST: recovery.py only
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
NEW ERROR-RECOVERY / RETRY / FAILOVER POLICY: NONE
COMPLEXITY / LEGACY GATES: USER-EXCLUDED
```
