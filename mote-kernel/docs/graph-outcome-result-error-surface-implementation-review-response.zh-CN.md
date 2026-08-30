# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案评审回复

> **Disposition：REVIEW RESPONSE COMPLETE / R1–R7 CLOSED / KEEP CURRENT PUBLIC SURFACE / DOCS-ONLY / NO PRODUCTION AUTHORIZATION。**
>
> 本回复接受独立评审对 breaking rename、error surface 歧义、union/runtime narrowing 混淆和范围账本的核心意见；同时对“完全不回写实施正文”和“把既有 durable-first/partial-commit invariant 视为本 change unit 的新增能力”两点作出范围澄清。

## 1. 回复信息

- 日期：2026-08-26
- 独立评审：[graph-outcome-result-error-surface-implementation-review.zh-CN.md](graph-outcome-result-error-surface-implementation-review.zh-CN.md)
- 评审 SHA256：a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668
- 原实施方案 SHA256：1e976385f5812132b1c357444dc7044c8ed28c840c35bbcf4fc3f6be2bfc9fc3
- 整改后实施方案：[graph-outcome-result-error-surface-implementation.zh-CN.md](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 整改后实施方案 SHA256：be747243b7419604dc8d9cdffa268efbeb368a7aeaeaa043c6bcc3ac3866a5d6
- 本文性质：review disposition / docs-only audit record；不拥有 production、tests、State、Store、API version 或 requirements 批准状态。
- 变更范围：只回写实施文档并新增本回复；不修改 src、tests、README、CHANGELOG、State、Store、pyproject.toml 或执行路径。

## 2. 总体裁决

评审的总体 CHANGES REQUESTED 成立。原实施方案把现有 public contract 误判为尚未发布，并把概念命名提案写成了立即 breaking rename。整改后的唯一 target 是：

~~~text
现有 canonical owner / sealed variants       KEEP
现有 Graph public aliases                    KEEP
NodeOutcome / RunResult 等名称               仅作概念分组，不新增 alias
SettledNodeResult                            不作为 public 名称
Error matrix                                按现有 facade 精确列出
production / tests / State / Store           NO CHANGE
~~~

因此，本 change unit 的“实施”是文档和边界澄清，不是运行时重构。

## 3. 逐项 disposition

| Review item | Disposition | 整改结果 |
| --- | --- | --- |
| R1：错误假设 public API 尚未发布 | **ACCEPTED** | 删除 breaking rename 目标；保留 Graph.Outcome、Graph.Result、三个 outcome aliases、三个 node-result aliases 和三个 run-result aliases。 |
| R2：目标 alias 与 canonical owner 未闭合 | **ACCEPTED** | 不新增 Graph.NodeOutcome、Graph.SettledNodeResult、Graph.RunResult；文档只维护概念映射，union 仍由现有 owner 唯一拥有。 |
| R3：SettledNodeResult 误导 candidate evidence 语义 | **ACCEPTED** | 不把 SettledNodeResult 作为 public alias；保留现有 Graph.SuccessResult 等名称，并明确 result 在 callback 前构造、不是 receipt。 |
| R4：持久化/failover/recovery 越界 | **PARTIALLY ACCEPTED** | 删除新增 Store、journal、checkpoint、failover、retry、worker handoff 目标；保留既有 callback 顺序、partial-commit error 和 durable-first 作为 HARD KEEP，不扩写其语义。 |
| R5：Error surface 清单矛盾 | **ACCEPTED** | 实施文档新增 public aliases 与 internal leaves 的精确清单；不新增 exception class 或 facade alias。 |
| R6：验收矩阵与变更范围不匹配 | **PARTIALLY ACCEPTED** | 改为 docs-only manifest，不新增 typing/runtime tests；拒绝“因此不得回写实施正文”的过宽结论，因为用户明确要求基于 review 完善实施 MD。 |
| R7：union alias 与 runtime narrowing 混淆 | **ACCEPTED** | 明确 union 只用于 annotation，isinstance 只对 concrete variants；不为 union 新增 wrapper/base class。 |

## 4. 已接受的整改

### 4.1 保留现有 public surface

当前规范、README、examples 和 tests 已经使用：

~~~text
Graph.Outcome
Graph.SuccessOutcome / FailureOutcome / InterruptOutcome
Graph.Result
Graph.CompletedResult / AbortedResult / AwaitingResumeResult
Graph.SuccessResult / FailureResult / InterruptResult
Graph.Error 及已有精确 exception aliases
~~~

整改文档不再要求删除这些名称，也不增加新旧双轨。未来如果确实需要显式 NodeOutcome 或 RunResult，必须另开 versioned API migration change unit，先冻结 semver、需求 owner、active-file manifest 和完整 typing/runtime evidence。

### 4.2 保留唯一 owner 与 sealed construction

评审指出 GraphOutcome、GraphCommitResult、GraphResult 已分别由 outcome/result owners 持有。整改后：

- facade 继续只做 direct alias 和 delegation；
- 不在 facade 重新拼 union；
- 不新增 nominal wrapper、registry、tag 或第二 factory；
- concrete variants 的 private seal、factory ownership、transition seal 和 run-result seal 保持不变；
- union annotation 与 concrete isinstance narrowing 分开验证。

### 4.3 收口 Error surface

整改文档现在区分：

- 已由 Graph 暴露的 Error、validation、snapshot、limit、routing、value 和 partial-commit aliases；
- 只能通过 Graph.Error 捕获的 owner-internal leaves；
- 本期不新增的 exception classes。

ValuePublicationError 继续保留，是因为它已经是当前 facade alias；本回复不把它扩展成新的 recovery API。PartialCommitError 继续是现有 invocation-local exception，不被重新定义为 failover/checkpoint protocol。

## 5. 对不合理评审建议的回复

### 5.1 不接受“不得回写实施正文”

评审第 4 节建议在不修改实施正文的前提下关闭方案。但用户要求的是“基于评审完善实施 MD，并对不合理意见写回复 MD”，因此只保留 review 而不回写 target 会留下已知的错误前提：

- public API 尚未发布；
- SettledNodeResult 是 public target；
- Error aliases 的 public/internal 边界；
- docs-only 与 production authorization 的关系。

本回复接受 review 的技术结论，但拒绝把“review 期间不修改 target”扩展为“review 完成后也不允许 docs-only writeback”。整改后的 implementation MD 已将这些事实回写，并保持源码/测试零变更。

### 5.2 部分不接受“删除所有 durable/recovery 表述”

评审正确禁止本 change unit 新增持久化、failover、checkpoint、journal、retry 或 worker arbitration；但完全删除 durable-first、exact successor、partial-commit 和 publication 顺序会使文档失去架构边界，且可能被误读为允许改变既有实现。

因此采用以下边界：

~~~text
既有顺序与异常语义 = HARD KEEP / 只读复核
新增 Store、协议、恢复算法 = 明确禁止
~~~

文档只说明 Graph.Commit callback 的现有 candidate/evidence 顺序，不承诺外部存储、crash recovery 或跨进程 handoff。

### 5.3 不把“docs-only”误写成“无实施”

本 change unit 不做 production implementation，但仍有实际 docs implementation：

- 修正目标状态；
- 删除未获批准的 rename 计划；
- 建立 current public alias 与 conceptual family 的映射；
- 固定 Error public/internal matrix；
- 写清 union 与 concrete runtime narrowing 的边界；
- 固定未来 rename 的独立准入条件和文件范围。

这不会形成第二事实源：规范 shape 仍由现有 normative source 和 production owner 共同约束，本实施文档只拥有本次概念映射和 docs-only scope。

## 6. 准确的最终 target

整改后的 implementation MD 只承诺以下内容：

~~~text
NodeOutcome                      -> Graph.Outcome + outcome concrete aliases
admitted node settlement result  -> Graph.Transition.result + result concrete aliases
RunResult                        -> Graph.Result + run concrete aliases
Error                            -> Graph.Error + current public error aliases
~~~

其中：

- Graph.Transition.result 不是 durable receipt；
- Graph.Result 不承诺跨进程 durable snapshot；
- Graph.failure() 是 node/business outcome，不是 exception；
- Graph.Error 不进入 Graph.Result；
- SettledNodeResult、NodeOutcome、RunResult 不成为新的 Graph attributes；
- Commit、Transition、ResumeAction、State、Continuation 保持各自独立职责。

## 7. 验证与交付

本次 docs-only 回写的验收目标：

| 检查 | 结果/要求 |
| --- | --- |
| target/review/response 互链 | 已写入 |
| old public names 被误标为待删除 | 已清除 |
| src / tests / State / Store diff | 无授权、应保持无变更 |
| new alias/class/factory/seal/exception | 不新增 |
| SettledNodeResult durable wording | 已改为非 public 概念并明确 candidate evidence |
| Graph.Error 与 Graph.Result 混用 | 已禁止 |
| git diff --check | 交付前必须通过 |
| docs hooks | 交付前运行适用 hooks |

此前未修改源码的 baseline 已通过 make check（850 passed、100% coverage、build/twine passed）及 monorepo 全量 pre-commit；该结果不冒充本 docs-only 回写的 production authorization。

## 8. 最终状态

~~~text
review verdict                         = CHANGES REQUESTED (resolved by writeback)
R1–R3, R5, R7                         = ACCEPTED
R4, R6                                = PARTIALLY ACCEPTED
public surface                         = KEEP
production/tests/State/Store           = NO CHANGE
rename implementation                  = NOT AUTHORIZED
docs-only writeback                    = COMPLETE
future API migration                   = SEPARATE CHANGE UNIT
~~~

本回复完成后，实施文档状态为 Revised / docs-only / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION。
任何 public rename、new error alias、wrapper、compatibility path 或 persistence/recovery capability 都必须另行立项和评审。
