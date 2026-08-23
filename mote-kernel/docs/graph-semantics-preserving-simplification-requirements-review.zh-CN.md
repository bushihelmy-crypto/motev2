# Graph 执行代码语义保持型简化需求再次复审

> **结论：需求文档尚未闭合，不批准 `GSP-A05`，不得进入 Phase 1，也不得修改 production/tests。**
> 本轮按当前 `GraphRunState`/State shape 审查；不要求本轮实现 Store、journal、checkpoint 或 State 持久化。
> 真正尚未闭合的是 `GSP-A03`、`GSP-A04` 的证据记录；architecture 双语差异只需做 owner/范围澄清，不能被解释为新增持久化目标。

## 1. 复审信息

- 复审日期：2026-08-19
- 复审对象：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 交叉对象：[实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)、架构双语文档、Node I/O 与 skip-output normative source
- 范围：只做文档、owner、映射、测试路径和门禁记录的静态核对；不修改 production code 或 tests
- 原则：零增复杂度、唯一事实源、复用既有 owner、完整门禁、严格泛型、模块级导包、可推导事实不重复保存、逻辑保持最短闭环

## 2. 已确认的闭合项

1. requirements 已限定为 `GSP-Pxx`、`GSP-Nxx`、`GSP-Sxx`、`GSP-Axx`，没有复制 S01–S23 的 target dataclass、consumer 清单或实施顺序。
2. `GSP-A02` 已明确 P2 只登记列表、owner、候选方向和未批准状态；P2 不继承 P1 批准，详细设计由 `GSP-A06` 单项约束。
3. `GSP-A03` 已明确不能用文件级 owner 列表冒充 case-level evidence，并保留 shape 删除的 exact-shape/tamper 要求；当前文档如实标记其尚未满足。
4. `GSP-A05` 明确要求显式批准，当前状态没有授权 Phase 1 或 production/tests 修改。
5. requirements 中六个相对链接均指向现有文件；README 的稳定导航已指向 requirements、implementation 和既有 normative source，未把 review 历史复制进 README。

这些项只能说明需求结构基本成形，不能抵消下面的阻断项。

## 3. 阻断问题

### R0. architecture 双语和跨文档 owner 需要澄清（不构成实现 State 持久化的要求）

requirements 第 2 节第 19 行把 `docs/architecture.zh-CN.md` 和 `docs/architecture.md` 同时列为
“当前具体行为与 shape”的 normative source。两份文件不是单纯措辞差异：

- `docs/architecture.md:10` 把 authoritative snapshot 定义为由独立版本的 `GraphState` 与
  `DomainState` 组成的 `AgentState`，并写明 state store 原子提交、确认后才替换 Python memory snapshot；
- `docs/architecture.zh-CN.md:14-18` 只以 `GraphRunState` 描述 authoritative/durable truth，且明确说这是
  commit boundary、不是具体 Store 或 durability 承诺；
- 英文文件 `docs/architecture.md:14` 还声明 required/optional ports 的组装语义，中文文件没有对应事实。

这确实是 source-owner/双语同步问题，但不应被解释成当前简化必须实现 `AgentState`、Store 或持久化。
本轮的当前行为基准应固定为 production 正在使用的 `GraphRunState`、中文 architecture 的当前描述、Node I/O
`8.5` 的“State 保持当前模型”裁决，以及 skip-output 对现有 State/reducer 的约束。requirements line 46
已经明确：保持 `GraphRunState`、command/revision/status/run identity/codec identity，不把 concrete frame、
publication value 或 continuation snapshot 写入 State。这与“暂不扩展 State 持久化能力”的范围是一致的。

因此，英文 architecture 中的 `AgentState`/state store 段落不能作为本轮新增 target，也不能授权改动
`state/**`；它只需要被标为非本单元的更高层方向、同步为当前文档，或明确其非本轮 normative 地位。requirements
line 34 仍需要补充 source precedence，避免评审者误把该段落当成实施要求。

即使先解决中英文差异，第 2 节仍把 architecture、Node I/O 和 skip-output 文档以集合方式列为
“当前具体行为与 shape” owner；三者都分别描述 State、commit 或 publication 的部分边界（例如 Node I/O
`8.2–8.5`、skip-output requirements `5.1–5.3`）。requirements 没有字段级 precedence，仍可能出现
“多个文档都是真相”的重叠。应把共享事实拆成可核对的 sub-owner，或指定一个 source 为 canonical、其他文档只引用它。

最小的文档裁决是：

1. 将当前简化的 State owner 固定为 `GraphRunState`/现有 reducer/command/commit 行为；明确本轮不新增 Store、
   journal、checkpoint、output persistence 或 State mirror；
2. 选择一个 canonical architecture source，并把另一份明确标为同步翻译/非独立 normative source，或逐项同步
   双语文本；共享的 State/commit/publication 事实再按字段指定 Node I/O、skip-output 或 architecture 的 owner；
3. 在 `GSP-A01` 增加 source precedence/scope 检查，但不以此要求 production State 变更。

R0 因此是 **文档收口项，不是本轮 production 阻断项**；A03/A04 仍是准入阻断。

### R1. `GSP-A03` 仍只有文件级映射，且列出的 evidence 与可复现命令不一致

requirements line 99 要求每个 P1 有全部适用 requirement ID、exact success case、exact failure/boundary
case，以及适用的 exact-shape/tamper case。实施方案第 7.2.1 节 line 371–387 仍只有测试文件名，未提供
`test path::test_case`、断言目标、失败条件和 evidence 状态，因此 15 个 P1 仍不能申请 A05。

已知的直接漏映射仍在当前表中：

- S06 触及 `resource_order` 与 compiled resource ordering，却只列 `P05/P06/P08`，应至少审计 `P07`；
- S09 负责 `ResolutionCommand` projection，却只列 `P05/P06/P08`，应包含 `P02`；
- S23B 合并 failure/interrupt Result view，却只列 `P01/P05/P07`，应包含 `P04`。

按 requirements line 69 的“全部适用 ID”规则，还必须对下列边界逐项给出接受或排除理由，而不能靠文件名推断：

- S04 的 admission/publication 与 nested/family consumers 是否同时适用 `P03/P07`；
- S05 的 compiled graph-input owner、import 和 dependency direction 是否适用 `P08`；
- S10/S11 的 graph-output、publication、resume-frame availability 是否适用 `P04`（以及 admission 触碰的 `P03`）；
- S18 的 publication/action collision identity 是否适用 `P04`；
- S20 保持 codec identity 与 scoped resume-input materialization，是否适用 `P02`。

此外，实施方案的相关测试命令（`implementation.zh-CN.md:327-346`）没有列出：

- `tests/execution/engine/test_admission.py`，但 S05 的 owner 表和第五次复审给出的 S06 failure case 都引用它；
- `tests/execution/engine/test_recovery_boundaries.py`，但 S13 的 owner 表引用它。

因此即使补上函数名，当前命令也不能证明矩阵所列全部 owner/case 已运行。每个 P1 行至少应固定：

```text
unit -> all applicable GSP-Pxx
     -> baseline: exact repo-relative path::case + assertion + recorded result
     -> target gate: exact path::case + assertion + failure condition + pending/passed status
     -> changed-file manifest / gate command
```

baseline（当前旧 shape 的证据）与 target exact-shape gate（对应 production 原子变更才会落地）必须分栏；
不能把尚不存在的未来测试路径写成已经通过的 evidence。实施方案 7.1 的“398/817 passed、100% coverage、
Pyright 0 errors”等历史数字也没有绑定 commit、执行时间和完整 stdout；在补齐矩阵时必须把它们标成历史基线，
不能直接当作本轮 A03 的新证据。

### R2. `GSP-A04` 的 changed-file manifest 已过期，且 manifest 的生命周期没有闭合

requirements line 100 要求 Phase 0 使用一个 exact repo-relative actual changed-file manifest，并纳入本轮实际
新增/修改的 review/response。当前实施方案至少存在三套时间点不同的记录：

- 第 7.4 节（line 438–451）仍是五个旧 paths；
- 第 7.5 节（line 455–466）另列四个第五次回复 paths，并把后续 command output 推迟到下一次记录；
- 第五次复审 line 104 声称以第 7.4 节加该复审组成六文件 expanded manifest，但这没有回写为当前唯一 manifest。

之后又有第五次回复、requirements 内容更新以及本次复审文件；实施方案第 1 节/第 8.1 节的 review index
也尚未纳入本 requirements 复审路径；当前 `git status --short --untracked-files=all`
还显示多份 review/response、implementation、requirements 和 `example/` 用户新增文件。`example/` 不一定属于
本单元，但必须在 manifest 之外有明确的 scope/exclusion 记录，不能静默遗漏。

这使 requirements line 110 所称“`GSP-A04` 已形成”与实际证据不一致。应：

1. 明确 manifest 的边界是“每个 Phase 0/原子单元一份”，并固定 monorepo root 与 repo-relative 路径口径；
2. 在一个确定的 review cutoff 后重新生成当前实际文件清单，列出 exact paths、命令和输出；未变化文件不重复加入，
   明确排除的用户文件单独记录；
3. 将本次复审及后续 response 的路径纳入下一次 manifest，再重新判定 A04；不要用旧的五/六文件数字代替当前集合；
4. 不以 staging 或修改 Git index 作为覆盖证据。

### R3. 需求条目仍有几个不可验收的模糊词

requirements 的 owner 分层方向正确，但以下表述在严格门禁下不足以停止语义漂移：

- `GSP-P06`（line 50）的“现有状态预算”没有给出 normative anchor；Node I/O source 明确是 **4096-state
  pre-mutation safety budget**（例如其 line 1535/1580）。应引用 source section/anchor 或稳定的预算 ID，
  而不是允许实施者自行解释“现有”；
- `GSP-P03`（line 47）只概括“调用次数、先后关系和异常边界”。skip-output source 的
  `5.2.1/5.3` 已冻结 expected revision、exact successor equality、pre-commit zero-call、State-before-frame
  installation 和 partial-prefix handoff；requirements 至少应引用这些 exact anchors，才能让 A03 的断言目标可审计；
- `GSP-P04`、`GSP-P07`、`GSP-P08` 的“唯一 frame store”“FIFO”“严格泛型关系”“模块级连续 imports”没有明确
  对应 owner/gate。应分别指向 `ScopedFrameIndex`、resource first-seen 与 waiter FIFO 的 normative section，以及
  generic-integrity/source-discipline/dependency-direction gates；
- `GSP-N06` 虽禁止类型擦除和重复 wrapper，却没有明确禁止无语义的 sentinel/phantom generic、函数内动态导包或
  generic-erasing cast；S23A、S12 和 `GSP-P08` 的最简边界会因此留下文字 loophole，应补充或绑定到同一 gate；
- `GSP-P08` 的“narrow typed ports”没有说明 required port assembly failure 与 optional port removal 这两个现有
  组装语义；在 R0 的 architecture 裁决后必须明确是否属于本轮保持义务，不能让中英文 source 各自决定；
- line 11–13 声称 requirements 不定义 characterization 计划/可复现命令，line 99 又要求 exact test path。应明确
  “requirements 只定义 evidence 格式与准入义务，实施方案拥有具体矩阵和命令”，避免 owner 解释分叉。

这些条目未必要求新增类型或测试，但必须在 A05 前收紧；不得用“具体契约见其他文档”作为没有可验证停止条件的替代。

## 4. 阶段裁决

| 条件 | 当前结论 | 依据 |
| --- | --- | --- |
| `GSP-A01` owner 闭合 | **未闭合** | architecture 双语 normative source 尚未裁决（R0） |
| `GSP-A02` 原子边界 | 基本闭合 | 实施方案已有 S01–S23 target/owner/删除面；仍须按 A03 逐项证据化 |
| `GSP-A03` 行为证据 | **未满足** | 15 个 P1 没有 case-level matrix，且命令漏列 owner 测试（R1） |
| `GSP-A04` 文档门禁 | **未满足** | manifest 记录跨时间点且未覆盖当前实际文档集合（R2） |
| `GSP-A05` 显式批准 | **不可申请** | A01、A03、A04 均未闭合 |
| `GSP-A06` P2 单项设计 | 已定义、未触发 | P2 仍不得继承任何 P1 批准 |

## 5. 必须完成的最小收口

1. 先裁决并同步 architecture 双语 normative source，回写 `GSP-P02/P03/P08` 的 source precedence；
2. 把 15 个 P1 扩展成 case-level matrix，完成全部 requirement-ID conservative audit，并补齐命令中遗漏的 exact test paths；
3. 将 P03/P06/P04/P07/P08 的模糊词绑定到现有 normative section/gate，不复制第二套事实，只增加可验证引用和 evidence 状态；
4. 按 review cutoff 重新生成一个 per-unit exact manifest，纳入本复审/response，记录 in-scope 与明确排除项及完整轻量 gate 输出；
5. 更新 requirements 当前裁决，不再声称 A01/A04 已形成；完成后再做一次只针对 A01–A04 的准入复核，最后才可申请 A05。

在上述步骤完成并得到显式批准前，继续禁止 Phase 1、production/tests 修改和任何账本外重构；P2 仍逐项满足 A06。

## 6. 本次验证

| 检查 | 结果 |
| --- | --- |
| requirements 全文与行号 | 已复核 112 行 |
| 相对 Markdown 链接 | 6 个目标均存在 |
| architecture 中英文对照 | 发现 AgentState/GraphState/DomainState、Store/durability、ports 语义差异 |
| implementation 7.2.1 与 7.1 命令 | 文件级矩阵；确认 `test_admission.py`、`test_recovery_boundaries.py` 未列入相关命令 |
| Git 状态 | 保留用户已有 README/example 与文档改动，未修改 index |
| production/tests | 未修改 |
| pytest、Pyright、`make check`、full coverage/build | 本轮未运行；不把历史数字当作新证据 |

## 7. 最终结论

requirements 已比前一轮更接近单一 owner 和阶段准入，但还没有形成可执行、可复现的唯一真相：
architecture 双语文件的 normative 冲突是新的最高优先级问题，A03/A04 仍有明确证据缺口，P03/P06/P04/P07/P08
还存在可被宽解释的口径。

**本轮复审裁决：未闭合；不批准 `GSP-A05`，不得进入 Phase 1 或修改 production/tests。**
