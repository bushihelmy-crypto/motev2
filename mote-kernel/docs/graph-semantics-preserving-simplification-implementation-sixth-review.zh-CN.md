# Graph 执行代码语义保持型简化实施方案第六次复审

> **结论：State 对齐与“不实现持久化”方向已闭合；实施方案整体仍未闭合，不批准 `GSP-A05`，不得进入 Phase 1，也不得修改 production/tests。**
>
> 本轮重新按当前 production 的 `GraphRunState`、`GraphRunCommand`、reducer/validation、revision 和现有 commit callback 协议审核。英文 architecture 中的 `AgentState`、Store、journal、checkpoint 等描述只作为文档 owner/范围澄清，不构成本轮实现目标。

## 1. 复审信息

- 复审日期：2026-08-20
- 复审对象：[最新实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)（823 行）
- 交叉对象：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[需求再次复审](graph-semantics-preserving-simplification-requirements-review.zh-CN.md)、当前 State/执行代码和已有 architecture gate
- 范围：只做静态文档、目标 shape、State 范围、case 映射、owner 和门禁审查；不修改 production/tests，不重复运行重测试
- 原则：零增复杂度、唯一事实源、复用既有 owner、严格泛型、模块级导包、可推导事实不重复保存、逻辑最短闭环

## 2. 已闭合事项

### 2.1 State 与持久化硬边界：通过

第 2.4、3.3、7.3、8 节已经明确并且相互一致：

1. `src/mote_kernel/state/**`、`tests/state/**`、协议/序列化 shape 为 `KEEP`；
2. 不新增或修改 `GraphRunState`、command variant、reducer/validation、status、revision、run/resource/codec identity；
3. 不新增 Store、repository、persistence port、database adapter、journal、event log、checkpoint、默认 commit backend 或第二 publication store；
4. concrete frame/publication/continuation 仍只由 execution-owned `ScopedFrameIndex`/continuation 持有；
5. S09、S17、S20、S22 即使读取现有 State 或调用现有 reducer，也只能整理 execution-owned projection/admission/临时对象。

因此，本轮的 State 结论是：**与当前 `GraphRunState`/State shape 对齐通过；不实现 State 持久化通过；没有发现需要新增 State mirror 或 storage owner 的 target。** `RecoveryTransferState` 也被明确限定为 invocation-local pure proof value，不是 State schema。

### 2.2 先前整改已吸收

- P1 已有 15 行 case-level baseline matrix；27 个 unique baseline nodeid 展开为 31 个 case，并区分了 `B0 PASS` 与 `T0 PENDING`。
- S12 继续是 P2，未借 State 或泛型删减绕过 equality、malformed seed、valid-domain 和 generic migration 前置条件。
- S04/S06、S09 的 owner/原子边界说明已比上一轮清楚；S03–S06 的 publication consumer 迁移没有再被拆成双写桥。
- 7.3 增加了 no-State/no-persistence cross-unit gate，符合本轮范围。

这些改动只说明方案更可审计，**不等于 target gate 或 Phase 0 已获批准**。

## 3. 仍未闭合的阻断项

### R1（阻断）：requirements 与 implementation 的准入状态不是唯一真相

当前至少有三种互相冲突的状态：

- implementation 第 11.1 节把 `GSP-A01` 标为 `BLOCKED`，`GSP-A04` 仅标为 “EVIDENCE RECORDED（待 requirements owner 接受范围）”；
- requirements 第 7 节仍写 `A01/A02/A04 已形成，A03 pending`；
- requirements 再次复审第 4 节仍把 `A01`、`A03`、`A04` 列为未闭合。

这不是 State 设计问题，但违反唯一事实源，也会让实施者无法判断 `A05` 的前置状态。必须在 requirements owner 裁决后同步三份文档的状态；在同步前不得使用 implementation 的 baseline 记录自行宣布批准。最小修复是：明确 State/no-persistence 已通过，`A01` 仅为 architecture 文档 owner/source precedence 阻断，`A03` 仍因 target/复杂度证据阻断，`A04` 在新的 cutoff 被 owner 接受前保持未确认。

### R2（阻断）：复杂度账本仍是定性清单，没有净减少证据

本文声明自己是“复杂度账本”唯一 owner，但当前只有“删除什么/最多新增什么”和若干 O(n²) 描述，没有逐 P1 的可核对 before/after 计数。尤其以下候选仍可能把一个重复事实换成新的 wrapper/cache，而不是减少认知面：

- **S11**：`typed local cache` 的 key/value、重复 target 的 canonical 表示、首次错误顺序和 cache 生命周期未固定；`RequiredTarget` 在三个 group tuple 中可重复出现，且表格声称生成的 “display identity” 不在给出的 exact facts model 中；
- **S14**：boundary 已拥有 `kind`/`availability`/`control`，新增 `ScopeControl → ChildControl` 投影函数的签名、调用次数和净分支减少未给出；
- **S17**：derived tuple/index 的 nominal type、pure-skip 差集使用的坐标键（尤其 repeated superstep/scope）和错误顺序未给出；
- **S18**：两个 owner 的 count/index 类型、首次 duplicate/collision identity 与错误消息顺序未给出；`invocation.py` 与 `resume_admission.py` 的改动也没有分开的净复杂度表；
- **S20**：已有 `engine/resume_input.py::materialize_node_input` owner，方案只写“新增一个窄 typed materialization 函数”，没有证明不是再包一层 wrapper；
- **S23B**：合并 failure/interrupt projection 只描述“一次扫描”，没有证明新增分支和两个累积器小于删除的两次简单 projection，也没有固定混合 scope 的顺序。

在每个 P1 行补齐 `删除字段/扫描/分支`、`新增字段/helper/cache`、`before→after 扫描与分支数`、nominal signature、错误/排序不变量后，才能把“目标 shape 已闭合”作为准入证据；否则应把相应单元降回未批准/P2。不能用 31-case 行为通过代替复杂度证明。

### R3（阻断）：部分 baseline 与目标 owner 不直接对应

1. **S23A 证据不直接。** 当前成功/失败 case 与 S03 共用 facade/nested case；它们证明同一 execution owner 和非法 nested coordination，但没有直接断言 `_advance_scope_quantum()` 在 `AdvanceGraphFrontier`、其他 non-boundary 返回和 nested coordination 边界上的 `None`/错误分类。应加入 family-driver/session 精确 case，覆盖 advance 后 root loop 继续、普通 executable/等待路径继续以及错误路径，不把 S03 case 充当 S23A 证据。
2. **S14 证据偏离 owner。** 目标删除的是 `engine/recovery.py::_NestedOutcome` 的镜像字段，当前 matrix 却主要引用 `test_runtime_boundaries.py` 的 child projection；至少应补 recovery preflight 的 completed/aborted/awaiting 或 malformed boundary case，证明 boundary identity 和 equality 语义保持。
3. **S18 漏掉 `invocation.py` 的行为。** 目标位置同时包含 `invocation.py` 和 `engine/resume_admission.py`，但 matrix 只有 resume-admission publication coordinate cases，没有 `plan_resumes()` duplicate action-coordinate 的 success/failure characterization（现有 graph API duplicate-skip case 可作为入口）。
4. **S10/S11 缺少 malformed/order 边界。** S10 把短路 helper 与完整 diagnostic scan 并存；后续 malformed graph-output binding 可能改变“首个缺失”与后续 descriptor 错误的优先级。S11 也必须覆盖重复 target、sibling scope、稳定排序和首次 unavailable identity，而不只覆盖一个完整/一个缺失 input。

上述缺口即使不新增 production 能力，也必须在 `B0`/`T0` 中逐项固定；否则 `GSP-A03` 仍未满足。

### R4（重要）：exact-shape gate 的 owner/表述仍有交叉歧义

- S03、S04、S05、S06 都指定同一个 architecture test path。topology exact-shape 由一个 owner 持有本身是正确方向，但四个独立原子单元没有列出该 case 中可独立归属的断言段，容易造成同一测试文件的跨单元隐式耦合。应在同一 owner 文件内标明按单元分组的 exact assertions，或把每个 gate 的断言目标写成可独立核对的子区段；不能复制第二个 topology owner test。
- S08 第 3.2 节写“`joins_by_source` 的 production 直接读取只留 routing owner”，而 target gate 又写“只由 routing/snapshot guard 消费”。应明确：snapshot guard 只能 module-scope import 并调用 routing 的 `_declared_joins()`，不得直接读取 field；否则 gate 文案本身互相矛盾。
- S18 的“复杂度账本共同承担”不是可执行断言。需要写出 exact AST/source 条件（一次 typed index、无 `tuple.count()`、无先 `any` 再二次枚举）和保留的错误顺序，不能只写 O(n²) 残留即失败。

## 4. 当前准入裁决

| 条件 | 本轮结论 | 说明 |
| --- | --- | --- |
| State/持久化硬边界 | **通过** | 当前 `GraphRunState`/command/reducer/protocol 原样 KEEP；无 Store/journal/checkpoint/State mirror target |
| `GSP-A01` owner/source | **文档阻断** | requirements、implementation、requirements-review 状态冲突；英文/中文 architecture 仍需 source precedence，但不产生 State 工作项 |
| `GSP-A02` 原子边界 | **基本具备，尚不可批准** | 24 个 execution-only 单元已列出，但 S11/S14/S17/S18/S20/S23B 的 exact nominal/复杂度面仍不够闭合 |
| `GSP-A03` 行为证据 | **未满足** | target gates 全部 `PENDING`，且 S23A/S14/S18/S10/S11 有 owner-specific evidence 缺口 |
| `GSP-A04` 文档门禁 | **旧 cutoff 已记录，但本轮将失效** | 新增本复审文件后，7.6 的 13-path manifest 不再覆盖当前文档集合 |
| `GSP-A05` 显式批准 | **不可申请** | R1–R4 未收口；不得进入 Phase 1 或修改 production/tests |
| `GSP-A06` P2 | **未触发** | 9 个 P2 仍需逐项设计，不继承任何 P1 状态 |

## 5. 最小收口顺序

1. 由 requirements owner 统一 A01–A04 状态，并把 State/no-persistence 作为已闭合范围记录；不要因此新增或修改 `state/**`。
2. 在 implementation 中补逐 P1 的净复杂度/nominal signature 账本，优先处理 S11、S14、S17、S18、S20、S23B；证据不足者降级，不要为了保留 P1 添加泛型 bag、第二 cache 或兼容 wrapper。
3. 补 S23A 的直接 advance/`None` characterization、S14 的 recovery-owner case、S18 的 invocation duplicate case，以及 S10/S11 的 malformed/order/repeated-target cases。
4. 统一 S03–S06 与 S08/S18 gate 的 owner 和可执行断言口径；保持一个 topology owner，不增加重复测试 owner。
5. 以本复审为新 cutoff，重新生成 exact actual changed-file manifest（至少纳入本文件；后续 response/implementation 修改继续触发新的 cutoff），只运行 targeted pre-commit/no-index whitespace。不要把旧 13-path 记录当作当前 A04 证据。

完成上述文档和证据收口后，再做一次只审 A01–A04 的准入复核；在显式 A05 之前，所有 production/tests 保持不变。

## 6. 本轮验证记录

- 静态重新读取 implementation 823 行，并核对第 2.4、5.1/5.2、7.2.1/7.2.2、7.3、7.6、11.1 节。
- `git diff 7944159 -- src/mote_kernel tests` 无 production/tests 差异；当前 State/执行代码仍以 `GraphRunState`/现有 reducer 为基准。
- 静态核对 matrix：15 个 P1、30 个表格 case 引用、27 个 unique baseline nodeid；9 个未来 target gate 名称不存在且明确标为 `PENDING`，不把它们冒充已通过。
- 未在本轮重复运行 pytest、Pyright、`make check` 或 build；implementation 中记录的 2026-08-20 baseline 输出仅作为其自述记录，不作为本轮新增运行证据。

**第六次复审裁决：State 对齐/不做持久化通过，但实施方案仍未闭合；不批准 `GSP-A05`，不得进入 Phase 1 或修改 production/tests。**
