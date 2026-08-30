# Graph execution 代码简化候选 A 实施方案第四次评审

> **结论：`CHANGES REQUESTED / R9–R11 RESOLVED / R12 OPEN / KEEP TECHNICAL DISPOSITION UPHELD / NO PRODUCTION AUTHORIZATION`。第三次评审要求的索引、state-only trace 与 provenance 均已正确整改；候选 A 仍应关闭为 `KEEP / NO IMPLEMENTATION`。当前仅剩一个 major 文档问题：累计 change-unit 账本漏列独立第三次评审，且第三次评审回复声称拆分两个 unit、实际却给出一个未分组的四文件 manifest。该原子账本闭合前，当前 SHA 不能记为最终无 finding closure。**

## 1. 评审对象与冻结版本

- 日期：2026-08-26
- 当前受审 implementation SHA256：`ee9f6fd5d9372c22cbbd4e9e97bb059933286f46979c01daba2c0283d7bab292`
- production source baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- production/test diff：空

关联整改输入：

| Target | SHA256 | 职责 |
| --- | --- | --- |
| [候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md) | `ee9f6fd5d9372c22cbbd4e9e97bb059933286f46979c01daba2c0283d7bab292` | 当前 A0 evidence、disposition 与累计 unit ledger owner |
| [第二次评审回复](graph-execution-code-simplification-implementation-second-review-response.zh-CN.md) | `78f5cecad587caac3000eb071db5d610f5e34e702d4d9ae02cc84213c4e404e6` | R11 provenance 修订 |
| [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md) | `3e2ccc6507c5652be5e1667ab8512d2486b21bf6445d56ffafa651e983d3f20e` | R9 动态 review/response 索引 |
| [第三次评审回复](graph-execution-code-simplification-implementation-third-review-response.zh-CN.md) | `108d1caf4827329adafe00ba7ff2b25551563a845ea5c9b7d95759d224b0e3fd` | R9–R11 disposition 与本轮 manifest |
| [第三次独立评审](graph-execution-code-simplification-implementation-third-review.zh-CN.md) | `9df9d790ee5deb4e0bb3ac956fb79b31c6a596dda7a68f3cf03af9908ca62dda` | 历史 finding 与整改准入条件；本轮未修改 |

本文只评审上述冻结内容，不拥有 A0 evidence、requirements、批准状态或未来 target。

## 2. R9–R11 验收

| Finding | 四审裁决 | 证据 |
| --- | --- | --- |
| R9 review/response 索引 | **RESOLVED** | 主实施方案第 1 节已登记二审回复、三审及三审回复；README 继续只提供稳定主方案入口；navigation sync 没有追写进历史二审 unit |
| R10 state-only actual trace | **RESOLVED** | trace 已在 `_new_context()` 前列出 resume-scope/substitution 分类和 `GraphValueUnavailableError` early branch，并明确无 context、无 commit；与 source 和 exact test 一致 |
| R11 review provenance | **RESOLVED** | 二审回复改为“writeback 前已存在且保持未修改”，删除不可复核的人物归因；版本 SHA 只由对应 review record 拥有 |

上述整改没有改变技术 target、复杂度账本、production/tests 或 reopening 条件。

## 3. R12 — MAJOR：累计 unit ledger 漏项且本轮 manifest 未按声明分组

implementation 第 8.1 节声称候选 A 收口由“六个不互相反向拥有的 docs-only change unit”组成，并声明“各轮独立 review record
均与后续 response/writeback 分离”。实际列出的六个 unit 是：

1. A0 owner writeback；
2. disposition/navigation sync；
3. independent second review；
4. second-review disposition writeback；
5. third-review response / owner writeback；
6. post-third-review navigation sync。

已经存在且独立形成的
[第三次评审](graph-execution-code-simplification-implementation-third-review.zh-CN.md)没有作为自己的 unit 出现在账本中。因此当前序列实际
至少是七个 unit；“第三次评审在本轮整改前已存在且保持未修改”只说明它不属于本轮 response manifest，不能把它从累计历史账本中
删除。否则同类的 independent second review 被登记、independent third review 却未登记，审计规则按轮次漂移。

同一根因也出现在 third-review-response：第 3 节明确说本轮拆成“owner/response writeback”与“navigation sync”两个 docs-only
unit，第 6 节却只提供一个无分组的四文件 `Actual manifest`。implementation 已把三文件 writeback 与单文件 navigation sync 分开，
response 记录却没有使用相同原子边界。一个总文件集合不能替代每个 change unit 的 exact manifest。

**整改要求：**

1. 在 implementation 累计账本中新增 `independent third review` 单文件 unit，并把当前计数从六修正为七；
2. fourth review 在后续 response/writeback 开始前已经存在时，也必须作为新的 independent review unit 单独登记，不能并入 response；
3. 将 third-review-response 第 6 节明确拆成：
   - owner/response writeback：implementation、second-review-response、third-review-response；
   - navigation sync：主实施方案；
4. 验证表可以写“两个 unit 合计四个 changed files”，但不能再称一个未分组集合为 per-unit manifest；
5. production/test manifest 继续为空，不修改 requirements 批准状态。

## 4. 技术 disposition 再确认

R12 只影响文档原子账本，不改变以下已闭合事实：

- `GraphRunContext` 是唯一 confirmed root/child runtime storage owner；
- planned、Result、continuation snapshot 与 proof 都是生命周期不同的 nominal projection；
- 原 `ScopedStateIndex` target 不减少 lookup/filter/sort/projection，却至少使 top-level/type/dataclass/field
  `+1/+1/+1/+2`；
- current tests、source review 与 future characterization gap 已分层，没有用绿色测试夸大未断言行为；
- `EX-A1`、`EX-A2` 不适用，`KEEP` review 绝不构成 production implementation approval。

因此本轮裁决不是“方案接近可实施”，而是“代码继续不实施，文档账本还需一次收口”。

## 5. 验证记录

| 验证 | 结果 |
| --- | --- |
| implementation 第 6.1 节 15 个 exact behavior nodeid + 两个 architecture files | `42 passed in 0.78s` |
| `make check` | Ruff/format 通过；Pyright `0 errors`；complexity gate `9 passed`；health `reviewed=51 / unreviewed=0 / stale=0`；全量 `843 passed`、100% coverage；build/twine 通过 |
| 四个整改文件的相对 Markdown links | 全部存在 |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml` 与 `Makefile` 无本轮改动 |

门禁绿色不替代 R12 的 per-unit manifest 一致性。

## 6. 终止规则

为避免“PASS → owner 登记 PASS → owner SHA 改变 → 再审”的循环：

1. R12 response/writeback 完成后，对修订 SHA 做一次独立复审；
2. 若该复审为 `PASS / findings=0`，最终裁决只由该独立 review record 持有，不再为了复述 PASS 修改 implementation owner；
3. 最终 review path 可单独加入主方案动态导航；纯导航更新不改变已通过的 implementation SHA，也不复制技术 disposition；
4. 不为无技术 target 的 A 创建 implementation/acceptance unit。

## 7. 最终裁决

```text
blocker = 0
major = 1
minor = 0
R1–R8 = REMAIN RESOLVED
R9–R11 = RESOLVED
R12 = OPEN — ATOMIC CHANGE-UNIT LEDGER

EX-A0 technical disposition = COMPLETE / KEEP
current document closure = CHANGES REQUESTED
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE / NO AUTHORIZATION
```

**最终结论：不批准当前 `ee9f6f…` SHA 作为无 finding 的最终文档 closure；继续接受候选 A 的 `KEEP / NO IMPLEMENTATION` 技术结论。R12 完成后按终止规则做最后一次独立复审。**
