# 父子图独立 GraphRun 状态实施方案评审回复复审

> **结论：`PASS WITH SCOPE CAVEAT / FINDINGS = 0 / RESPONSE CONSISTENT / NO IMPLEMENTATION AUTHORIZATION`。**
> 评审回复正确地把原方案中超出本次要求的跨 invocation 恢复、持久化、跨 run 幂等和
> failover 承诺撤回，并保持当前 `GraphRunState` 状态模型。这里的 `PASS` 只表示回复
> 作为 docs-only scope/owner correction 是合理且自洽的；它不表示独立 child invocation
> 已实现，也不授权修改 production、State、Store、protocol、public API 或 tests。

## 1. 复审信息与冻结输入

- 复审日期：2026-08-27
- 复审对象：[父子图独立 GraphRun 状态实施方案评审回复](graph-independent-run-context-implementation-review-response.zh-CN.md)
- 回复 SHA256：`40bbf09abaaaff1f71748a3e0fe9670c6896d7af45c7b2c38329b42d018b7686`
- 对照评审：[父子图独立 GraphRun 状态实施方案独立技术评审](graph-independent-run-context-implementation-review.zh-CN.md)
- 对照评审 SHA256：`9f6a27958432153b6aed31994e705fa2060c52ae16d09d5ce3358ee7ff71bcb3`
- 修订边界文档：[父子图独立 GraphRun 状态实施方案（边界修订版）](graph-independent-run-context-implementation.zh-CN.md)
- 修订边界文档 SHA256：`b996e47b077ba360125a786a20a5cdfcf5c10eb2256eb10ce23cd890e98bb2c3`
- 可行性调研：[GraphRun 独立状态可行性调研](graph-independent-run-context-feasibility-research.zh-CN.md)
- 可行性调研 SHA256：`d0068b0c72558cef0945102a87b00a5a84ee6339aa19b00233370c3b5e07dc7f`
- production source baseline：Git `d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`

本复审只拥有本文件的复审结论和证据，不拥有 requirements、当前 API、State、frame、
continuation 或未来独立 run 的 target truth。本轮 change unit 只新增本文件；既有
用户 dirty changes、production 和 tests 均不归因于本复审。

## 2. 最终裁决

| 事项 | 裁决 | 说明 |
| --- | --- | --- |
| 回复文档作为 review disposition record | **PASS** | R1–R14 的关闭方式、owner 分工和证据层级一致 |
| 原始跨 invocation implementation target | **WITHDRAWN** | 没有用临时 DTO、内存 registry 或模糊 phase 假装已解决 blocker |
| 修订边界文档 | **RESEARCH-ONLY / NOT READY** | 只保留边界、复用原则和未来准入条件 |
| 持久化、Store、codec、checkpoint | **OUT OF SCOPE** | 没有本期交付或授权 |
| failover、worker handoff、lease、reclaim | **OUT OF SCOPE** | 明确留给后续独立设计 |
| `GraphRunState` schema/status | **KEEP CURRENT** | 不增加 field、status、command；当前状态仍由既有 owner 负责 |
| production / State / Store / protocol / API / tests | **NO CHANGE / NO AUTHORIZATION** | 本回复没有把文档复审写成实施批准 |

原评审中的 blocker 并非通过新算法关闭，而是通过撤回不具备前置条件的 target 关闭。
这与用户“本期不做持久化、不做 failover，状态与当前对齐”的限定相符。

## 3. 用户范围逐项核对

### 3.1 不做持久化

回复第 2 节、第 3 节和第 5 节把以下能力全部标为撤回或未来 requirements：按
`child_run_id` 的外部 load、durable terminal output、output codec、Store、checkpoint、
跨 run CAS、read-after-commit、replay 和 ack。文中出现这些术语时均位于“撤回项”或
“未来准入前置条件”中，没有把它们写成当前可用能力。

因此，本期结论是 invocation-local；不能从回复推导出进程重启、跨 invocation 或
跨 worker 的恢复承诺。

### 3.2 State 与当前对齐

修订文档保留既有 `GraphRunState` 字段和 reducer/transition owner，不新增 output、
frame、continuation、receipt 或 handoff 字段。源码中的 `GraphRunStatus` 仍只有：

```text
RUNNING / COMPLETED / ABORTED
```

child awaiting/resume 继续由 frontier 和 settlement 表达，不引入新的 status。回复还
明确区分 control truth 与 invocation-local concrete frame，未将 output mirror 塞回 State。

### 3.3 不做 failover

worker identity、handoff、lease expiry/renewal、reclaim、aggregate budget、跨 worker
arbitration 和跨 invocation cancellation 均被标为本期排除。回复没有以现有 token
或 deterministic identity 冒充 worker failover 协议。

### 3.4 父子 ownership 的准确含义

回复保留当前 invocation 的 `GraphRunContext`/projection 事实，但没有宣称父可以拥有或
推进 child 的 authoritative state。正确解释是：

```text
child 的 GraphRunState = child-owned authoritative truth
parent 持有的 child binding/projection = 当前 invocation 的 transient 观察资料
parent 不直接写 child state，child 不直接写 parent frontier
```

当前代码仍有 `GraphRunContext.child_states`，这是既有 family invocation contract；
回复中的“parent child-state mirror = 0”是本 change unit 的新增面账本，不是“当前代码
已经没有 child binding”的声明。该区分在回复第 4 节和边界文档第 3 节中有记录。

### 3.5 public API 与 coordinate

`Graph` 仍是唯一 public facade，没有新增 public `GraphRun`、recursive overload、
`GraphRunRef` 或 `ChildRunInvoker`。当前 coordinate 也没有被偷偷改成 local root：root
继续使用 `scope=()`，child 继续使用完整 nested node-ID path 和 deterministic child run
id。未来若改 coordinate，必须另立规范、迁移和 review。

## 4. R1–R14 复审矩阵

所有条目的共同 disposition 是“接受技术风险判断，并以 scope withdrawal 或事实回写
处理”；以下复核确认没有一项被误写成未来协议已经完成。

| 项目 | 复审结果 | 关闭方式是否合理 |
| --- | --- | --- |
| R1 Store/recovery 越界 | **CLOSED BY WITHDRAWAL** | 删除本期 durability、CAS、lease、budget 交付，保留规范源优先级 |
| R2 requirements owner/ID/approval 缺失 | **CLOSED BY OWNER BOUNDARY** | 回复不代行 requirements 批准，并把准入顺序留给唯一 owner |
| R3 context/continuation/ownership 冲突 | **CLOSED BY KEEPING CURRENT CONTRACT** | 不伪造 per-run checkpoint；现有 opaque continuation 继续有效 |
| R4 typed output 不能成为 durable boundary | **CLOSED BY REMOVAL** | `GraphOutputView`/`CompletedChild` 仅保留 invocation-local projection |
| R5 terminal/publish crash window | **CLOSED BY NO TRANSACTION CLAIM** | 不声称跨写入原子性、outbox、replay 或重启恢复 |
| R6 start/input admission 非原子 | **CLOSED BY NO INDEPENDENT START** | deterministic child construction 限定在现有 invocation |
| R7 definition resolver/按 ID load 缺失 | **CLOSED BY ID LOAD REMOVAL** | 不增加隐藏 registry、resolver 或 public overload |
| R8 scope coordinate 未裁决 | **CLOSED BY FREEZING CURRENT FACT** | 当前完整 path 保持；改坐标需新规范和迁移 |
| R9 新类型/port 与既有 owner 重叠 | **CLOSED BY ZERO NEW SURFACE** | 复用既有 identity、projection、frame、transition owner |
| R10 cross-run idempotency/CAS 不可执行 | **CLOSED BY PROTOCOL REMOVAL** | 只保留现有 revision/token/exact successor，不宣称 exactly-once |
| R11 worker lease/aggregate budget 未定义 | **CLOSED BY CONCURRENCY REMOVAL** | 不开放 worker、handoff、lease、reclaim 或 aggregate budget |
| R12 cancellation/exception/child lifetime 未闭合 | **CLOSED BY INVOCATION LIFETIME** | 保留现有 session close/fence 事实，不扩写成 failover |
| R13 assembly fallback 矛盾 | **CLOSED BY NO IMPLICIT FALLBACK** | 未定义新 mode；未来 capability 必须显式装配并 fail closed |
| R14 测试、门禁和授权口径冲突 | **CLOSED BY DOCS-ONLY BOUNDARY** | 不新增测试；complexity 排除且不写成 `make check` 完整通过 |

R1–R14 的“closed”只代表评审记录已得到正确 disposition。它不代表 Store、codec、
transaction、lease 或 recovery protocol 已经设计完成。

## 5. 一致性与残余注记

### 5.1 已确认的一致性

1. 回复顶部的 `TARGET WITHDRAWN / RESEARCH-ONLY / NOT READY` 与边界文档状态一致，
   没有保留原 Phase 0–4 的实施授权。
2. 回复明确说 review disposition complete 不等于 production complete，避免把
   “接受 blocker”误读为“技术 blocker 已实现修复”。
3. `GraphRunState`、typed command、pure reducer、exact successor、identity 和现有
   nested projection 继续由 canonical owner 持有，没有第二 scheduler、runner、
   frame truth、registry、alias 或 generic-erasing wrapper。
4. 验证表如实区分非 complexity 检查、coverage baseline 缺口和未运行的 `make check`；
   没有用 841 个既有测试证明未来独立 recovery。

### 5.2 非阻断审计注记

- 修订文档沿用同一个路径记录“原目标”和“边界修订版”，旧目标正文不再作为仓库内的
  独立文件存在；回复保留旧 SHA，因此可以识别历史快照，但链接本身会解析到当前
  修订版。该问题不改变本次 scope disposition，也不构成实现 blocker；未来若重新
  启动 target，建议使用 versioned path 或独立 tombstone，避免历史链接歧义。
- `git diff --check` 只覆盖 tracked 文件；回复同时记录了 untracked docs 的 EOF、
  CRLF 和 trailing-whitespace 扫描，故不能把两者混称为同一检查。
- “zero new surface”与“parent child-state mirror = 0”均应按本 change unit 的增量
  账本理解，不能被引用为当前 production 已完成 ownership 拆分的证据。

这些是可发现性和措辞上的后续改进，不要求修改本次 response 才能成立其 docs-only
范围裁决。

## 6. 验证记录

### 6.1 本复审执行的文档检查

| 检查 | 结果 |
| --- | --- |
| 四份相关文档 SHA256 | 与第 1 节冻结值一致 |
| Markdown 相对链接存在性 | **PASS：无缺失目标** |
| EOF / CRLF / trailing whitespace | **PASS：四份文档均以 LF 结尾且无尾随空白** |
| `git diff --check` | **PASS**（tracked workspace） |
| monorepo root scoped `pre-commit` | **PASS**；文档 hook 通过，代码复杂度 hook 无适用文件 |

### 6.2 对已有基线记录的正确归因

回复记录的 841 项非 complexity behavior tests、Ruff、format、Pyright、build/package
结果可作为当前 dirty workspace 的健康快照，但不是本复审对未来 independent run 的
证明。coverage 99.94% 的缺口仍按回复所述归因于评审前已有的
`family_driver.py:515`、`result.py:164` dirty source；本复审没有修改代码或新增测试
掩盖它。

`make check` 本轮不作为完整通过报告：该聚合目标无条件包含用户明确排除的 complexity
gate。保留“未运行”是正确的门禁口径，而不是遗漏。

## 7. 范围 caveat 与下一步

本复审通过的是**回复文档**，不是用户最初设想的完整独立 child invocation。若后续
要真正实施“每个图的 `GraphRun` 自己负责自己的状态”，需要另立一个更小、明确的
implementation target，至少冻结以下范围：

- 仅同一 invocation 内的 ownership 拆分，或明确另立跨 invocation requirements；
- child owns `GraphRunState`，parent 只能持有 transient child handle/projection；
- 保持当前 `RUNNING / COMPLETED / ABORTED` 状态和现有 continuation/nested 行为；
- 不加入 persistence、Store、codec、checkpoint、failover、worker handoff 或新的
  public API；
- 给出 child-local frame 的 producer/consumer、删除闭包和 exact behavior manifest。

这只是后续 target 的准入建议，不是本复审授予的编码权限。当前回复明确撤回了原目标，
因此若用户暂时只要求审计和范围收敛，文档已经足够；若要开始 ownership refactor，
必须先提交并复审新的 target。

## 8. 最终裁决与本复审 manifest

```text
blocker = 0
major = 0
minor = 0

response disposition              = PASS / DOCS-ONLY SCOPE CORRECTION
R1–R14                            = ACCEPTED / CLOSED BY WITHDRAWAL OR OWNER WRITEBACK
original implementation target   = WITHDRAWN / NOT READY
revised boundary target           = RESEARCH-ONLY / NOT READY
current GraphRunState/status      = KEEP (RUNNING / COMPLETED / ABORTED)
persistence / failover            = OUT OF SCOPE / SEPARATE DESIGN
production / State / Store / API  = NO CHANGE / NO AUTHORIZATION
tests / legacy tests              = NO CHANGE / NO AUTHORIZATION
complexity gate                   = USER-EXCLUDED / NOT RUN
```

本复审的唯一 change-unit manifest：

```text
mote-kernel/docs/graph-independent-run-context-implementation-review-response-review.zh-CN.md
```

未修改评审回复、边界文档、调研、production、State、Store、protocol、public API 或
tests。**最终结论：回复改动合理且边界已收敛；它保留了当前状态语义，明确排除了持久化
和 failover，但不应被引用为独立 GraphRun 已经实现。**
