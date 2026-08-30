# Graph execution 代码简化候选 A 实施方案第三次评审

> **结论：`CHANGES REQUESTED / KEEP TECHNICAL DISPOSITION UPHELD / NO PRODUCTION AUTHORIZATION`。候选 A 的技术结论仍然成立：当前只有一个 confirmed root/child runtime owner，原 `ScopedStateIndex` target 没有净删除面，因此不得开始代码实施。但二审 writeback 后的当前文档 SHA 存在 1 个 major 与 2 个 minor 文档问题：唯一 review/response 索引未同步、state-only actual lifecycle 漏掉 pre-context 拒绝分支、二审输入来源表述不可复核。三项闭合前，不能把当前 SHA 记为最终无 finding 的 closure。**

## 1. 评审对象与冻结版本

- 日期：2026-08-26
- 评审范围：候选 A（`ScopedStateIndex` / `ScopedStateBinding`）当前 A0 closure、二审 disposition writeback 与关联 owner/navigation
- 当前受审 implementation SHA256：`eacfa0408c024f98be4f90eb614c3f44072e536c66f0ce4a62edcff5e7a1dba0`
- production source baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- production/test diff：空

本轮冻结的关联输入：

| Target | SHA256 | 职责 |
| --- | --- | --- |
| [候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md) | `eacfa0408c024f98be4f90eb614c3f44072e536c66f0ce4a62edcff5e7a1dba0` | 当前 A0 evidence 与 disposition owner |
| [第二次评审回复](graph-execution-code-simplification-implementation-second-review-response.zh-CN.md) | `286d16dff189ec6f73c3c057f4f07736ef2c3c1607205c310832ed24bf514a76` | 二审接受与 writeback manifest |
| [第二次独立评审](graph-execution-code-simplification-implementation-second-review.zh-CN.md) | `b7e59a3cf9c0a7828d73d26ab6d6645881e1947897dc37ca6859b0152e98154a` | 冻结旧 owner SHA `2d24…` 的历史 PASS 裁决 |
| [候选调研](graph-execution-explosive-simplification-research.zh-CN.md) | `ea414d510443ef77099569a4b6ca36db4446ab27da0631f4559a6beb2c4b1ac6` | 历史候选来源与 closure 指针 |
| [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md) | `73527660a897bfc94cbef06f75448997b64947f8df7395e34b4d800b31e93e74` | 账本外候选与 review/response 动态索引 |
| [Requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md) | `4bc0f50dc461501e8e312a8ab3593c4936a424e8961aed77f6a6f6d8bb31fd5d` | requirement 与批准状态 owner |

二审只冻结并通过 implementation SHA `2d24…`。当前 `eacfa0…` 新增二审接受状态、response 链和 writeback manifest；因此必须按
新 SHA 独立复核，不能从历史 PASS 自动推导当前版本无 finding。

## 2. 原则性复核

| 原则 | 技术结论 | 当前文档闭合度 |
| --- | --- | --- |
| 唯一真相 | `GraphRunContext` 仍是唯一 confirmed state owner | **未完全闭合**：指定的动态 review/response 索引缺一条已存在记录 |
| 0 负债 | 不创建 index、wrapper、cache、空批准项或双路径是正确结论 | **PASS** |
| 复用基础设施 | 继续复用 context、coordinate constructors、validator、frame index 与现有 commit/install path | **PASS** |
| 优美代码 | confirmed/planned/snapshot/proof nominal boundary 不合并为宽 record | **PASS** |
| 合理且规范 | complexity/evidence 分层正确，但 actual lifecycle 与 manifest provenance 仍有两处不精确 | **CHANGES REQUESTED** |

## 3. Findings

### R9 — MAJOR：唯一 review/response 索引与二审 writeback 不一致

[主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)第 8.1 节明确声明，review record 的 exact path 由其第 1 节
“关联记录”逐条列出；README 也把动态 review/history 索引委托给主实施方案。当前关联记录已经列出候选 A 调研、closure、首轮
review/response 和二审，却没有列出已经存在、且已被当前 closure 吸收的
[第二次评审回复](graph-execution-code-simplification-implementation-second-review-response.zh-CN.md)。

与此同时，当前 implementation 第 8.1 节把二审 disposition writeback 的 actual manifest 限定为 implementation + second-review
response，没有形成后续 navigation sync。结果是：closure 声称 `SECOND REVIEW ACCEPTED`，指定索引却只能导航到二审、不能导航到
接受该二审的 response。这不是 target shape 重复，但属于动态审计链的第二状态，违反唯一索引规则。

**整改要求：**

1. 在主实施方案“关联记录”登记 second-review-response；本第三次 review 形成后，也应由后续 response/writeback 按同一规则登记；
2. 将这次索引更新记录为独立、准确的 docs-only navigation sync manifest，不要追写成历史二审 review unit 的组成部分；
3. 不修改 requirements 批准状态，不创建空 requirement，也不扩大 production/test manifest。

### R10 — MINOR：state-only `Actual lifecycle trace` 漏掉 pre-context 拒绝分支

implementation 第 4.3 节的 state-only trace 从 `_new_context(..., recovered=True)` 开始，但当前
`Graph.run()` 在此之前先计算 `resumed_scopes` 与 `substitution_actions`；当 continuation 缺失、resume 跨多个 scope 且携带
substitution 时，会直接抛出 `GraphValueUnavailableError`。该路径不会创建 context、不会进入 `validate_context()`，也不会调用 commit。

第 6.1 节已经正确登记
`test_state_only_multi_scope_substitution_is_rejected_before_first_commit`，因此 executable evidence 与标题为 “Actual lifecycle trace”
的调用链现在不一致。

**整改要求：** 在 state-only trace 的 `_new_context()` 前补入 resume-scope/substitution 分类与 early rejection 分支，并明确只有通过
该 guard 的 state-only run 才进入 root-only recovered context。不得用测试表中的一行替代实际调用顺序。

### R11 — MINOR：二审输入来源表述不可复核

second-review-response 第 5 节写道“第二次评审是本轮用户提供且保持不变的独立输入”。可复核事实只支持：该 review 在 disposition
writeback 开始前已经存在，并且本 change unit 未修改它；“用户提供”不是 manifest 所需事实，也无法从文件、SHA 或 Git 状态证明。
同一类 provenance 表述在首轮整改时已经被收敛为“本 change unit 前已存在且未修改”，这里不应重新引入人物归因。

**整改要求：** 改为“第二次评审在本次 disposition writeback 前已存在且保持未修改，因此不计入该 writeback manifest”。这只修正
审计事实，不改变二审裁决。

## 4. 无新增 finding 的技术部分

以下结论经当前 production/source 再次复核，继续成立：

- `_RootStateBinding` 只由 `_new_context()` 与 `replace_root()` 构造；continuation admission 只运输既有 binding；
- `ChildStateBinding` 的 production constructor 只有 acknowledged child start 与 existing-child replacement；
- `replace_root()` 只有 `replace_state()` 一个 production caller，`replace_child()` 由 `replace_state()` 与 child start 调用；
- `lineage_states()`、`_scoped_states()`、`recovery_seed()` 分别属于 planned、Result 与 proof projection，不是重复 confirmed storage；
- 原 tuple-dataclass index 不减少 scan/filter/sort/projection，却使 top-level/type/dataclass/field 至少
  `+1/+1/+1/+2`；把 projection 搬成 method 不是 mechanics 删除；
- current/future evidence 已正确区分 executable assertion、source review 与 future characterization gap；
- reopening 条件允许新证据或新 Pareto target，不会错误冻结后续分析；
- `EX-A1`、`EX-A2` 仍不适用，任何 review PASS 都不能转化为 production 实施授权。

因此 R9–R11 不推翻 `KEEP` 技术 disposition；它们阻止的是当前 SHA 被标记为“文档完全闭合、findings=0”。

## 5. 验证记录

| 验证 | 结果 |
| --- | --- |
| implementation 第 6.1 节 15 个 exact behavior nodeid + 两个 architecture files | `42 passed in 0.78s` |
| `make check` | Ruff/format 通过；Pyright `0 errors`；complexity gate `9 passed`；health `reviewed=51 / unreviewed=0 / stale=0`；全量 `843 passed`、100% coverage；build/twine 通过 |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml` 与 `Makefile` 无本轮改动 |
| local links | 当前 implementation 的 7 个相对 Markdown 链接均存在 |

绿色门禁只证明当前代码 baseline 与已声明 executable assertions；不能消除 R9–R11 的文档事实问题。

## 6. 最小整改与再审准入

只需一个 docs-only response/writeback 周期：

1. 修正 implementation 的 state-only trace；
2. 修正 second-review-response 的输入 provenance；
3. 同步主实施方案的候选 A review/response 索引，并登记本第三次 review 与后续 response 的 exact path；
4. 在 implementation 中追加新的独立 actual manifest，保留历史四个 unit，不篡改历史 manifest；
5. 固定修订后 implementation/response/main-plan SHA，再核对链接、`git diff --check` 与 scoped pre-commit。

上述整改不得创建 `ScopedStateIndex`、修改 production/tests、登记空批准项或把历史二审 PASS 扩张为 implementation approval。

## 7. 最终裁决

```text
blocker = 0
major = 1
minor = 2
R1–R8 = REMAIN RESOLVED
R9 = OPEN — REVIEW/RESPONSE INDEX
R10 = OPEN — STATE-ONLY ACTUAL TRACE
R11 = OPEN — REVIEW INPUT PROVENANCE

EX-A0 technical disposition = COMPLETE / KEEP
current document closure = CHANGES REQUESTED
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE / NO AUTHORIZATION
```

**最终结论：不批准当前 `eacfa0…` SHA 作为无 finding 的最终文档 closure；继续接受候选 A 的 `KEEP / NO IMPLEMENTATION` 技术结论。完成 R9–R11 的 docs-only 整改并绑定新 SHA 后再复审。**
