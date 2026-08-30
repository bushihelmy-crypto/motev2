# Graph 执行代码语义保持型简化需求

## 1. 文档信息

- 状态：Approved / `GSP-A01`–`GSP-A05` 已闭合；批准当前 evidence matrix 中的 15 个 P1；`GSP-A06` 已对 S07、S01、S02、S12、S15、S16、S19 单项满足并批准，S21/S22 已关闭为 KEEP；候选 A-v2 已登记为 PENDING / NOT APPROVED
- 日期：2026-08-26
- 适用范围：`src/mote_kernel/execution/**` 的内部语义保持型简化
- 公共边界：`mote_kernel.execution.Graph`
- 对应实施方案：[Graph 执行代码语义保持型简化实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)

本文只定义本轮重构必须满足的 requirement ID、行为保持义务、非目标、外部语义停止条件和
阶段准入条件。本文不定义未来 target dataclass、producer/consumer 清单、实施顺序、复杂度账本、
characterization 计划或可复现命令；这些内容唯一归实施方案所有。

## 2. 事实源与文档分工

具体当前行为与 production shape 继续由以下 normative source 拥有：

- [架构说明](architecture.zh-CN.md)及其[英文版本](architecture.md)：公共 facade、execution/state owner、持久化和依赖方向；
- [Graph Node I/O implementation](graph-node-input-output-contract-implementation.zh-CN.md)：Node I/O、compiled topology、frame、continuation、recovery、nested/resource 行为；
- [`skip_failed` requirements](skip-failed-output-requirements.zh-CN.md)：skip-output 的公共需求；
- [`skip_failed` implementation](skip-failed-output-implementation.zh-CN.md)：skip routing、admission、publication 与事务实现契约。

文档 owner 固定如下：

| 内容 | 唯一 owner |
| --- | --- |
| 当前具体行为与 shape | 上述 architecture、Node I/O、skip-output normative source |
| 本轮 requirement ID、保持义务、非目标、外部语义停止条件、阶段准入条件 | 本文 |
| S01–S23 target shape、原子迁移边界、实施顺序、复杂度账本、characterization 计划和实施门禁 | 对应实施方案 |
| 裁决、异议与验证记录 | 各轮 review/response；不得成为行为、需求或 target-shape owner |
| 稳定入口导航 | `README.zh-CN.md`、`README.md`；只链接 owner，不复制正文或枚举 review 历史 |

若本文对具体行为的概括与 normative source 冲突，以 normative source 为准，并先修订本文后重新评审；
不得借“简化”重新解释或放宽当前行为。未来 target shape 只有在对应 production 原子变更中与 normative
source 同步后才成为当前 shape，Phase 0 不提前改写 normative truth。

评审整改回写规则：评审中被接受、且会改变本轮保持义务或准入门槛的结论，必须回写本文相应的
`GSP-Pxx`、`GSP-Sxx` 或 `GSP-Axx`；review/response 只记录裁决、理由和验证证据，不能替代本文。
实施方案只能引用这些 requirement ID，并负责把它们落实到 target shape、原子账本和 characterization
矩阵，不得用自身文字扩大、缩小或重新解释本轮需求。

## 3. 行为保持义务

- **GSP-P01 — 公共 API 与类型保持。** `Graph` 公共签名、overload、Graph-namespaced 类型、错误分类和 strict typing 结果必须与 architecture/Node I/O normative source 保持一致。
- **GSP-P02 — Durable State 保持。** `GraphRunState`、State command（包括 routing 对 `ResolutionCommand` 的投影）、status、revision、run identity、codec identity 和 durable control facts 必须与当前 normative source 保持一致；不得把 concrete frame、publication value 或 continuation snapshot 写入 State。
- **GSP-P03 — 事务边界保持。** admission、commit、exact-successor confirmation、memory replacement、frame/publication installation、partial-confirmation handoff 的调用次数、先后关系和异常边界必须保持；具体契约引用 Node I/O 与 skip-output normative source。
- **GSP-P04 — Result、Continuation 与 concrete frame 保持。** Result/Continuation 的 public shape、failure/interrupt result view、identity/integrity、scope-run/descriptor coordinate、publication provenance 和唯一 frame store 行为必须保持。
- **GSP-P05 — Routing、resume 与 skip 保持。** failure/interrupt/skip action、route/data 正交关系、target/input/output availability、diagnostic identity 和 settlement/result projection 必须保持；具体规则引用 skip-output normative source。
- **GSP-P06 — Recovery 保持。** recovery 的 valid-domain structural equality/hash、reachable public/limit boundary、traversal ordering、错误边界和现有状态预算必须保持，并继续与 runtime 消费同一 compiled/routing truth。
- **GSP-P07 — Nested、resource 与确定性保持。** child scope-run 派生、terminal snapshot、parent settlement、resource first-seen/FIFO（包括 compiled resource order）、canonical ordering、parallel selection 和 repeated generation 行为必须保持。
- **GSP-P08 — 架构与类型边界保持。** `Graph` 与 execution engine 的唯一 owner、pure typed transition、durable-first、narrow typed ports、严格泛型关系、模块级连续 imports 和依赖方向必须保持；不得引入第二执行/存储路径、反射或类型擦除。

这些 ID 只声明“必须保持哪类既有语义”。具体字段、算法、错误优先级和时序仍由第 2 节的 normative
source 定义；实施方案负责把每个原子单元映射到适用 ID 和 characterization 证据。为避免漏映射，至少遵守
以下强制判定：

| 触及的既有语义 | 至少必须映射 |
| --- | --- |
| `Graph` public signature、overload、namespaced type 或 typing/error surface | `GSP-P01` |
| State command、revision 或 durable control projection | `GSP-P02` |
| admission、commit、exact successor、安装或 partial-confirmation handoff | `GSP-P03` |
| Result/Continuation view、frame、publication 或其 identity/integrity | `GSP-P04` |
| failure/interrupt/skip routing、availability 或 settlement | `GSP-P05` |
| recovery proof、结构相等/hash、malformed boundary 或 budget | `GSP-P06` |
| nested scope、resource first-seen/FIFO、compiled resource order 或 canonical ordering | `GSP-P07` |
| Graph/execution owner、nominal typing、import 或依赖方向 | `GSP-P08` |

表格是“最低适用映射”而非完整替代；同一单元若同时触及多个边界，必须合并列出全部适用 ID，不能只选一个主 ID。

## 4. 非目标

- **GSP-N01 — 不新增运行能力。** 不增加第二 runner、`Graph.resume()`、result-owned execution path、checkpoint/store/journal、隐藏 registry 或跨进程 concrete-value recovery。
- **GSP-N02 — 不改变公共调用。** 不删除现有 run/resume/failed-retry 能力，不改变 overload 或把公共调用替换成另一套 inputs/answers/skips API。
- **GSP-N03 — 不改变 durable protocol。** 不把 concrete output 或 transient availability 写入 State，不增加 compatibility marker、第二 publication store 或新 durable discriminator。
- **GSP-N04 — 不改变事务语义。** 不删除或后移 admission、exact-successor confirmation、post-commit installation、partial-confirmation delivery 或既有错误边界。
- **GSP-N05 — 不建立平行解释器。** runtime、resume 与 recovery 不得各自解释 topology、routing、materialization 或 limits。
- **GSP-N06 — 不以代码行数替代简化。** 不用 `Any`、`object` 擦除、bare container、字符串 discriminator、context bag、compatibility alias、hidden mutable state 或重复 wrapper 换取表面缩行。

## 5. 外部语义停止条件

任一原子单元出现以下情况，必须停止实施并重新评审；不得通过更新本文来追认已发生的语义变化：

- **GSP-S01：** 无法保持 `GSP-P01` 的 public signature、overload、typing diagnostic 或错误分类；
- **GSP-S02：** 无法保持 `GSP-P02` 的 State/command/revision/run/resource durable facts；
- **GSP-S03：** commit 次数、顺序、exact confirmation、installation 或异常时序偏离 `GSP-P03`；
- **GSP-S04：** Result、Continuation、frame/publication 的可观察 shape、identity 或 integrity 偏离 `GSP-P04`；
- **GSP-S05：** failure/interrupt/skip routing、availability、diagnostic 或 settlement/result 行为偏离 `GSP-P05`；
- **GSP-S06：** recovery boundary、equality/hash、traversal ordering、budget 或 malformed-input 错误边界偏离 `GSP-P06`；
- **GSP-S07：** nested/resource/ordering/concurrency 行为偏离 `GSP-P07`；
- **GSP-S08：** 只能通过第二 owner、第二执行/存储路径、类型擦除、反射或逆转依赖方向完成，违反 `GSP-P08`。

实现内部的复杂度、原子迁移和 changed-file manifest 停止条件由实施方案拥有，不在本文复制。

## 6. 阶段准入条件

- **GSP-A01 — Owner 闭合。** 第 2 节所有文档实际存在，事实分工无重叠；requirements 不复制 target shape，实施方案不复制行为/外部停止/准入清单，README 只保留稳定导航。
- **GSP-A02 — 原子边界闭合。** 当前拟进入本阶段的 P1 单元必须在实施方案中具有唯一 producer/consumer edit owner、删除对象、最多新增面和 exact target；Phase 0 对 P2 只确认单元列表、owner、候选方向和“未批准且不得继承 P1 批准”，不提前把 P2 设计视为已准入。
- **GSP-A03 — 当前阶段行为证据闭合。** 每个当前拟进入本阶段的 P1 单元必须映射全部适用 `GSP-P01`–`GSP-P08`，并在 Phase 0 固定可复现的 `test path::test_case` 成功路径、失败/边界路径；shape 删除还必须固定 exact-shape/tamper 的目标路径、断言目标和失败条件。baseline characterization 可以引用当前 production/tests，target exact-shape gate 必须在对应 production 原子变更中同步落地；不能以文件级 owner 列表替代 case 级证据。矩阵必须覆盖当前拟实施的全部 P1，证据不足的单元保持未批准。P2 在其单项准入时按 `GSP-A06` 执行同一证据要求。
- **GSP-A04 — 文档与门禁闭合。** 每个 actual change unit 各自生成一份 exact repo-relative actual changed-file manifest，只纳入该单元实际新增或修改的文件，并通过实施方案规定的文档门禁。owner writeback 与 review audit 是两个独立 change unit，分别验证、不得合并为累计 manifest；历史 review/response 不因曾经存在而进入后续 manifest。不得以固定文件数量、漏列本单元实际改动、重复加入未修改文件或修改 Git index 来伪造覆盖。
- **GSP-A05 — 显式批准。** 仅对当前 evidence matrix 明确列出的、拟进入本阶段的 P1 申请批准；requirements、实施方案、稳定导航、`GSP-A01`–`GSP-A04` 证据和 Phase 0 验证记录必须完成最终准入评审。历史审查 ID、未列入当前阶段的 P1 以及全部 P2 均不因本条自动获批；在明确批准前，不得进入 Phase 1 或修改 production/tests。
- **GSP-A06 — P2 单项设计闭合。** 任一 P2 申请实施前，必须单独提交目标函数签名、输入/输出 nominal type、删除对象、最多新增对象、净复杂度证据、成功/失败或边界 characterization、exact-shape/tamper 证据和 changed-file manifest；S12 还必须提交 valid-domain equality、action ↔ availability、malformed seed 与 generic migration 证明。P2 不因 P1 的 `GSP-A05` 批准而自动满足本条件。

满足 `GSP-A01`–`GSP-A05` 后，只能批准实施方案中当前证据闭合的 P1 原子单元按顺序逐项实施；P2 还必须满足 `GSP-A06`，账本外方向不继承本文批准。

## 7. 当前裁决

第九次复审 R14–R16 回写后，实施方案已经提交当前 15 个 P1 的完整 case-level evidence、exact T0
`path::test_case`、断言目标、失败条件、净复杂度账本和 no-State/no-persistence negative gate。本 requirements
owner 同时接受第 6 节 `GSP-A04` 的 per-change actual manifest 口径；本次 owner writeback 的 exact manifest
和门禁记录见实施方案第 7.8 节。

S01 随后在 design/review commit `d34c117` 收口为实施方案第 3.1.2 节唯一 target：保留 exact 结构净删除与零新增负债
证据，明确 complexity gate/baseline/ratchet 对 S01 不适用，并把 production + behavior implementation 固定为该节拥有的
四文件原子 manifest。用户于 2026-08-23 以原文“你做到让我可以交付一个直接实施的文档”明确要求把已通过技术评审的
S01 收口为可直接实施状态；本 requirements owner 据此批准 S01，不把 review record 或本段变成第二份 target shape。

S02 随后由实施方案第 3.1.3 节收口为唯一 target；第三次技术评审以主实施方案 SHA256
`a386534d3657c15485842bf63657f296805614f7c3bea43505f161f5e14d66b2` 为对象，在前两次评审基础上再次裁决
`PASS / NO NEW BLOCKER`，确认 exact signature/nominal types、结构净删除账本、behavior/source evidence、
no-State/no-persistence 边界与三文件 planned manifest 已闭合。用户于 2026-08-24 明确批准 S02，并要求本次只回写
requirements、不实施代码；本 requirements owner 据此批准 S02，仅授权第 3.1.3 节该 exact target。automated complexity
gate/baseline/ratchet 不适用于本单元，但该节的零新增负债和结构净删除约束继续完整适用。

S12 随后由独立的 [S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
收口为唯一 target。三次独立技术评审及对应 owner writeback 已闭合重复 resume fact、phantom generic、valid-domain
equality/hash、action ↔ availability、malformed seed construction、materialization/scope owner、skip 支持域、no-State/
no-persistence 与 Graph/Kernel failover 边界；当前 reviewed exact target 固定为该实施方案 SHA256
`1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7`。用户于 2026-08-24 明确原文批准并要求本
requirements owner 将 S12 的 `GSP-A06` 标记为仅限当前 reviewed exact target 的已批准；本 requirements owner 据此批准
S12，仅授权上述 SHA 对应的 exact target、行为证据和七文件 planned implementation manifest。任何后续 target 内容或 SHA
变化均不继承本次批准，须重新取得显式批准。automated complexity 与 legacy/private-source-shape gates 不适用于本单元；
current behavior、strict typing、active owner/dependency、lint、format、build/package及适用 pre-commit checks 继续必须通过。
该 exact target 已在 commit `269ffaa6fe101164c0055f8426a72b761135d393` 以批准的七文件 manifest 实施并通过二次代码验收；
implementation-owner writeback 由 S12 独立实施方案拥有，本文仍只记录批准与生命周期状态。

S15 随后由独立的 [S15 Recovery worklist 分支结果归一化实施方案](graph-semantics-preserving-simplification-s15-implementation.zh-CN.md)
收口为唯一 target；独立技术评审绑定该实施方案 SHA256
`1e6629dfacad43ed1c87036fbc9f6589f606a220592c5fde3fadba068172e85a`，裁决为
`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`。用户于 2026-08-24 以原文“我授权执行S15”明确批准并要求开始实施；
本 requirements owner 据此将 S15 记为 `GSP-A06 SATISFIED / APPROVED`，只授权上述 reviewed SHA、对应 existing behavior
证据和 `src/mote_kernel/execution/engine/recovery.py` 单文件 production manifest。任何 target、baseline、manifest 或 SHA
变化均不继承本次批准。automated complexity 与 legacy/private-source-shape gates 继续按用户范围排除；current recovery
behavior、strict typing、active owner/dependency、lint、format、build/package、no-State/no-persistence 与 scoped repo checks
仍为必须通过的实施条件。本文不复制 S15 算法、结构账本或 exact source shape。

S16 随后由独立的 [S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
收口为唯一 target；第三次独立技术评审绑定该实施方案 SHA256
`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402`，裁决为
`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`。用户于 2026-08-24 以原文“实施S16吧，我给予批准”明确批准并要求开始实施；
本 requirements owner 据此将 S16 记为 `GSP-A06 SATISFIED / APPROVED`，只授权上述 reviewed SHA、对应 existing behavior
证据以及 `src/mote_kernel/execution/invocation.py` 与既有 `tests/execution/test_continuation_integrity.py` 两文件原子
implementation manifest。任何 target、baseline、manifest 或 SHA 变化均不继承本次批准。automated complexity 与
legacy/private-source-shape gates 继续按用户范围排除；current continuation behavior、exact error type/text/cause/phase/segment
precedence、strict typing、active owner/dependency、coverage、lint、format、build/package、no-State/no-persistence 与 scoped repo
checks 仍为必须通过的实施条件。本文不复制 S16 算法、结构账本或 exact source shape。

S19、S21、S22 随后由独立的 [S19 / S21 / S22 Graph 执行尾项语义保持型简化实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
绑定 reviewed implementation SHA256 `07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`，第三次独立技术评审裁决为
`PASS / READY FOR REQUIREMENTS OWNER PER-UNIT DISPOSITION`。用户于 2026-08-24 明确批准该方案中的 S19、S21、S22；本
requirements owner 据此分别记录：S19 为 `GSP-A06 SATISFIED / APPROVED`，仅授权该 reviewed target 的
`src/mote_kernel/execution/executor.py` 与 `tests/execution/test_executor.py` 两文件 implementation unit；S21、S22 为
`GSP-A06 SATISFIED / CLOSED — KEEP`，production/test manifest 为空，不创建空 implementation/acceptance commit。批准与关闭
均不延伸到其他 target、SHA、complexity/legacy gate 或账本外方向；本文只拥有本 disposition，不复制 implementation target shape。

候选 A-v2 随后以独立的
[versioned root-state de-wrapper 实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)
登记为新增 P2。首次独立技术评审只接受其技术方向并裁决 `CHANGES REQUESTED`；当前修订补入适用
`GSP-P01`–`GSP-P08` 映射、recovered continuation 成功/负向 evidence 与 planned exact-shape gate，但尚未经独立复审，也没有
用户对 reviewed exact SHA 的显式实施批准。因此本文只登记 `GSP-A06 PENDING / NOT APPROVED`，不复制 target shape、不授权
production/tests，也不使 A-v2 继承任何既有 P1/P2 的批准。

| 条件 | requirements owner 最终裁决 | 依据 |
| --- | --- | --- |
| `GSP-A01` | **CLOSED** | requirements、implementation、normative source、README 与 review 的 owner 分工已形成；architecture 全文治理和 non-normative 调用链整改不扩大 execution P1 范围 |
| `GSP-A02` | **CLOSED** | 24 个 execution-only 原子单元已固定；当前 15 个 P1 均有唯一 owner、删除对象、最多新增面和 exact target |
| `GSP-A03` | **CLOSED** | 15/15 P1 均映射适用 `GSP-P01`–`GSP-P08`，具有当前成功/失败或边界 baseline，以及 exact T0 nodeid、断言和失败条件；已批准 P2 另有各自 implementation/acceptance evidence |
| `GSP-A04` | **CLOSED** | 接受每个 actual change unit 独立 manifest、owner writeback/review audit 分离且历史记录不累计的模型；文档门禁可复现 |
| `GSP-A05` | **APPROVED — 仅限下列 15 个 P1** | A01–A04 evidence 已闭合；批准后 production、对应 target test 和实际受影响 normative source 按实施方案逐单元原子落地，T0 通过后才可交付 |
| `GSP-A06` | **SATISFIED / APPROVED — 仅限 S07、S01、S02、S12、S15、S16、S19 当前 reviewed exact target；S21/S22 CLOSED — KEEP；A-v2 PENDING / NOT APPROVED** | 既有 disposition 不变。A-v2 只有 versioned target 与整改后 evidence，仍等待独立复审、reviewed SHA 绑定和用户显式批准，不继承任何既有批准 |

`GSP-A05` 本次明确且穷尽地只批准以下 15 个 P1：

```text
S03, S04, S05, S06, S08, S09, S10, S11, S13, S14, S17, S18, S20, S23A, S23B
```

S07、S01 已于 2026-08-23 分别单项满足 `GSP-A06`，S02、S12、S15、S16、S19 已于 2026-08-24 分别单项满足 `GSP-A06`。S07、S01、
S02 的 target shape、证据和 actual manifests 分别由主实施方案第 3.2.2、3.1.2、3.1.3 节唯一拥有；S12、S15 分别由上述
独立实施方案 SHA256 `1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7` 与
`1e6629dfacad43ed1c87036fbc9f6589f606a220592c5fde3fadba068172e85a` 唯一拥有；S16 由上述独立实施方案 SHA256
`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402` 唯一拥有。本文只拥有批准状态，不复制 target。
当前新增且未批准的 P2 必须逐项满足 `GSP-A06`：

```text
A-v2 root-state de-wrapper — PENDING / NOT APPROVED
```

因此 Phase 1/2 对上述 15 个 P1 开放，Phase 3 对 S01、S02、S12、S15、S16、S19 开放；S21/S22 已按 KEEP 关闭。各单元必须遵循
实施方案的原子顺序、per-change manifest 和适用门禁。S01、S02、S12、S15、S16、S19 均已按各自 reviewed exact target 完成
implementation、验收与 owner writeback，production commits 分别为 `0f34aa2`、`170f1f2`、`269ffaa`、`4b8f372`、`f9854e1`、
`2709a59`；S21/S22 不创建空 implementation commit，仅保留 KEEP disposition。S01、S02、S12、S15、S16、S19 均不继承、等待或修改
独立 complexity framework，其批准分别只覆盖各自 reviewed exact target；S21/S22 的关闭也不改变 complexity/legacy gate 的用户排除口径。
A-v2 在独立复审与显式批准前不进入 Phase 3，production/tests manifest 保持关闭。
本次批准不授权修改 `src/mote_kernel/state/**`、`tests/state/**`、durable/conformance protocol，不授权新增
Store、repository、journal、checkpoint、database、persistence port/backend 或任何第二执行/存储路径；State
保持当前 shape，本轮继续不实现持久化。账本外方向也不继承本次批准。

本次 S01 approval unit 的 exact actual manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S02 approval unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S12 approval unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S15 approval unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S16 approval unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S19 approval unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

本次 S21/S22 KEEP closure unit 的 exact actual manifest 也只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```
