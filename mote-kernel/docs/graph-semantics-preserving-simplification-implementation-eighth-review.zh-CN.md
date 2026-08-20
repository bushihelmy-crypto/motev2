# Graph 执行代码语义保持型简化实施方案第八次复审

> **结论：State 对齐与“本轮不实现持久化”已经闭合；24 个简化单元的范围账本也已稳定，但 P1 准入仍未闭合。当前不能批准 `GSP-A05`。真正剩余的阻断不是 15 个 target gate 尚未随 production 落地，而是 A05/T0 时序自锁、准入状态存在两个事实源，以及 S18/S20 的 target 仍不唯一。**
>
> 本轮不再把 architecture 双语文案整改、非规范性调用链草稿或历史 review 累积清单解释成 State/持久化实施项。只要 State HARD KEEP gate 保持，它们不应继续扩大 execution simplification 的 production 范围。

## 1. 复审信息

- 复审日期：2026-08-20
- 复审对象：[实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)，1017 行
- 对象 SHA256：`c5f886ef3ad3c07376db75e43a1e22adfe4ee052ce074819072fb453602e8a47`
- 交叉依据：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[第七次复审](graph-semantics-preserving-simplification-implementation-seventh-review.zh-CN.md)、当前 execution/state production 与既有 tests
- 审查口径：唯一事实源、零增复杂度、复用现有 owner、严格 nominal/generic typing、模块级连续 imports、最少字段/变量/扫描/分支、完整但不自锁的门禁
- 范围：静态复审和轻量文档门禁；不修改 production/tests，不重复运行 `make check`、全量 pytest 或全量 pre-commit

## 2. 已闭合项

### 2.1 State 对齐和不实现持久化：通过

实施方案第 2.4、7.3、9、10 节已经把边界固定清楚：

1. `src/mote_kernel/state/**`、`tests/state/**`、`GraphRunState`、command variants、reducer/validation、revision、status、run/resource/codec identity 全部保持当前 shape；
2. 不新增 Store、repository、persistence port、database、journal、checkpoint、event log、默认 commit backend 或第二 publication store；
3. callback 只表示现有 exact-candidate confirmation，结构相等不等于 object identity、durability 或外部持久化成功；
4. concrete frame/publication/continuation 继续由 execution-owned frame/continuation owner 持有，不写入 State；
5. 24 个单元的 production location 均限定在 `execution/**`，manifest 一旦触及 State、State tests 或 protocol 即直接失败。

因此，本轮 State 考核结论是：**只对齐当前 State，保持当前 callback/reducer/memory-install 顺序，不实现持久化；通过。** architecture 文案差异不能据此生成 Store 或 State migration 工作项。

### 2.2 简化点总账本：通过

实施方案稳定为 23 个历史 ID、24 个原子单元，其中：

- P1：15 个；
- P2：9 个；
- S23 拆为 S23A/S23B，但没有虚增第 24 个历史来源；
- P2 和账本外方向不继承 P1 批准，也不得混入 P1 原子提交。

S03–S06 的 producer/consumer 迁移边界、S04/S06 的硬依赖、S08/S09 的 routing owner、S12 的 P2 降级以及 State HARD KEEP 均已写清，本轮不要求重新打开这 24 个单元的范围发现。

### 2.3 复杂度、类型与 import 约束：主体通过

第 3.6 节已补齐 15 个 P1 的 before→after 结构计数、nominal signature、owner-local index 生命周期和错误顺序；第 7.2.2/7.2.3 节也固定了 target path、子断言和 source/AST 失败条件。以下原则已形成可执行门禁：

- 不用 `Any`、`object`、bare container、反射、动态导入或 compatibility alias 换取缩行；
- production import 继续位于连续 module-header block；
- owner-local `dict`/`set` 不提升为 State、continuation 或跨 invocation cache；
- target 不能增加第二 materializer、第二 routing interpretation、第二 runner 或第二 storage path；
- 行数和全量测试绿色不能替代字段、扫描、分支、index 的净复杂度下降证明。

S10、S11、S14、S18 的 B1 owner-specific evidence 已补齐；S23A 也已如实标为 indirect，而没有继续冒充 direct PASS。这些修正关闭了第七次复审的大部分 R2–R4 问题。

## 3. 当前阻断项

### R9（阻断）：`GSP-A05` 与 T0 的时序形成编码授权死锁

requirements 的 `GSP-A03` 已明确区分两件事：

- Phase 0 固定 target gate 的路径、断言目标和失败条件；
- target exact-shape gate 随对应 production 原子变更同步落地。

requirements 的 `GSP-A05` 又明确规定：批准前不得进入 Phase 1，也不得修改 production/tests。

实施方案第 7.2.1 节正确写明 T0 在 production 尚未落地时只能是 `PENDING`，但第 7.2.2 节末尾又写“在它们落地前，`GSP-A05` 不可申请”，第 11.1 节也要求先“完成所有 T0 exact gate”再闭合 A03。这样会形成不可执行环：

```text
A05 前禁止修改 production/tests
  -> T0 必须与 production/tests 原子落地
  -> T0 未落地又禁止申请 A05
```

最小修复：

1. Phase 0 把 15 个 T0 标为 `DESIGNED/PENDING IMPLEMENTATION`，只要求 path、`Sxx.a/b/c`、失败条件和预期 manifest 类别完整；
2. `GSP-A05` 依据 baseline evidence、target gate 设计和 A01–A04 文档证据作实施授权；
3. 每个获批单元在同一 production 原子变更中落地 target test，运行完整门禁后才把该单元 T0 标为 `PASS`，T0 未通过不得合并或进入下一单元；
4. 删除实施方案第 754、1009–1012 行中“必须先落地 T0 才可申请 A05”的含义。

T0 当前全部 `PENDING` 是“尚未编码”的正常状态，不应被错误地当成 Phase 0 未闭合证据。

### R10（阻断）：准入状态仍有两个事实源，且实施方案重新扩大了 A01

requirements 第 2、6 节规定 requirements 是阶段准入条件和当前裁决的唯一 owner，其第 7 节当前写明 `GSP-A01`、`GSP-A02`、`GSP-A04` 已形成。实施方案第 1、5.3、11、11.1 节却把 A01–A04 全部写为 `BLOCKED/PENDING`；加上“实施记录，不替代 requirements 裁决”并不能消除两份相反状态。

同时，实施方案把以下内容继续列为 A01/A05 blocker：

- architecture 双语全文 precedence/parity；
- State/commit/publication 的字段级跨文档 sub-owner 表；
- 已明确为 non-normative 的调用链草稿 R5–R8。

这些文档可以独立整改，但在本轮已冻结 State/protocol、禁止持久化且 24 个 target 都是 execution-only 的前提下，不应继续成为所有 P1 的 production 准入前置。否则会把“保持当前 State”变成“先完成整个架构文档治理”，偏离用户给定范围，也制造新的唯一事实源。

最小修复：

1. requirements 一次性拥有 A01–A05 的最终状态；实施方案只记录可核对 evidence，不再维护第二张状态表；
2. 以“当前代码 + 现行 normative sections + no-State/no-persistence negative gate”作为本轮行为基线；不要求本轮实现或裁决未来 `AgentState`/Store；
3. 调用链草稿保留醒目的 `non-normative` 标记并单独修正文案，但不作为 P1 target、State owner 或 A05 blocker；
4. requirements 根据本轮最新矩阵更新其第 7 节，实施方案只引用该裁决。

### R11（阻断）：S18 与 S20 仍未满足“唯一 exact target”

#### S18：index 数量前后矛盾

实施方案第 3.4 节写：

- `plan_resumes()` 一次 action-coordinate count/index；
- `admit_resume_candidates()` 一次 duplicate index **和**一次 collision index；
- 同一行“最多新增”却限定为两个 owner-local typed indexes。

按前两项直读是 3 个 index；第 3.6 和 7.2.3 节又按“两个 index、每个 owner 一个”验收。target 无法据此唯一实现，也无法判断第三个结构是超限还是必需。

最小 target 应固定为：每个 owner 最多一个 typed count/index。`admit_resume_candidates()` 在同一次 canonical enumeration 中同时累计 duplicate count 和 confirmed collision 的有序结果；仍先报告 duplicate，再报告 collision，不进行第二次完整枚举，不增加跨 owner generic helper。

#### S20：参数类型比实际允许语义更宽，且 P1/P2 仍是条件式目标

第 3.4 节把 exact signature 固定为：

```python
materialize_node_input(..., *, input_binding: GraphNodeInputBinding | None = None) -> NodeInputFrame[GraphValueT]
```

但紧接着又规定显式参数只允许 `UseStepRequestInput` 的 failed-retry 分支，`OverrideGraphNodeInput` 仍走现有 codec decode。用包含两种 variant 的 `GraphNodeInputBinding` 会凭空增加一个必须拒绝的分支；第 3.6 节的 `0 -> <=1` 和“证明失败则退回 P2”也使 P1 target 仍是候选而非唯一目标。

若 S20 保持 P1，建议直接收窄为唯一 nominal 输入：

```python
materialize_node_input(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameIndex[GraphValueT],
    node_id: GraphNodeId,
    *,
    failed_retry_input: UseStepRequestInput | None = None,
) -> NodeInputFrame[GraphValueT]
```

非 `None` 时只允许当前 node 是 failed retry，仍先做 authoritative state/scope/node 校验；override、simulated frontier validation、codec 和错误优先级保持现状。`UseStepRequestInput`、`GraphValueT` 等全部模块级导入，不新增 wrapper/DTO。若不能接受这个 exact target，应现在把 S20 降为 P2，而不是在 P1 实施中临时决定。

在 S18、S20 收口前，15 个 P1 中只有 13 个具备无歧义的 exact target，`GSP-A02` 不能按“15/15 已闭合”批准。

### R12（重要）：S23A 的 direct baseline 要求与 Phase 0 禁改 tests 冲突

实施方案已经诚实记录 S23A `DIRECT B0 MISSING`，但又同时规定 A05 前不得修改 tests。若 direct private-owner case 被认定为 A03 的硬条件，S23A 会形成与 R9 相同的死锁。

S23A 删除的是 private `_AdvancedFrontier` marker；真正需要保持的外部语义是 `drive_root()` 在 advance、普通 progress 和 nested coordination 后继续或返回同一 boundary。现有 end-to-end cases 已覆盖这项外部行为。因此最小、最符合语义保持审查的做法是：

- 接受现有 end-to-end B0/B1 作为 baseline behavior evidence；
- 把 `_AdvancedFrontier` 归零、return annotation 和 `None` 分支分类留给随 production 落地的 T0 exact-shape/direct target test；
- 不再要求 Phase 0 为即将删除的 private return marker 新增 characterization。

若 owner 坚持 direct baseline，则 requirements 必须明确允许一个 tests-only characterization 原子单元；不能一边禁止改 tests，一边把该测试作为 A05 前置。两种口径必须只保留一种。

### R13（重要）：cumulative review manifest 会让每次评审自动使上次证据失效

实施方案第 7.6 节维护 16-path cumulative cutoff，并规定任何后续 Phase 0 文档变更都必须生成新 cutoff。本第八次复审一落地就会成为第 17 个 path；若为纳入它再改实施方案，又需要一次新复审，形成评审自循环。

`GSP-A04` 应验证一个原子文档单元的 **actual changed files**，而不是永久累积全部历史 review。最小修复：

1. implementation/requirements/navigation 的候选改动使用一次 exact manifest；
2. review 本身作为审计输出单独运行轻量 `pre-commit --files` 和 whitespace gate；
3. 被接受且改变规范/target 的结论回写 owner 文档后，只对该实际改动单元生成新 manifest；
4. 历史 review、response 和 history record 不因“曾经存在”反复进入后续 manifest；最终 review 不反向要求再创建一个 review 才有效。

这不会放宽门禁，只会消除由门禁记录本身制造的无限增长。

## 4. P1 闭合度

| 维度 | 当前状态 | 结论 |
| --- | --- | --- |
| State/no-persistence 范围 | State、State tests、protocol HARD KEEP；无 Store target | **通过** |
| 原子单元总账本 | 24 个单元，15 P1 + 9 P2 | **通过** |
| P1 exact target | S18 index 数量冲突；S20 输入 union 过宽且仍条件式 | **13/15 闭合** |
| baseline behavior | 15 行均有现有行为 case；S23A 只有 end-to-end indirect evidence | **行为覆盖已形成；direct 口径待裁决** |
| target gate 设计 | 15 行已有 path、断言目标和失败条件 | **设计已形成；落地应在 A05 后逐单元完成** |
| 泛型/import/owner 纪律 | nominal generic、module-scope import、no `Any`/reflection/second owner gate 已固定 | **通过** |
| 准入流程 | A05/T0、S23A/tests 和 review manifest 均存在循环依赖 | **未闭合** |
| 唯一准入真相 | requirements 与 implementation 状态相反 | **未闭合** |

因此不能说“P1 都闭合了”。准确结论是：**P1 的范围和绝大多数 target 已闭合；13/15 的 exact target 无歧义，S18/S20 仍需一次性收口；T0 未落地本身不是阻断，真正阻断是准入时序和状态 owner 冲突。**

## 5. 一次性收敛顺序

1. 由 requirements 保留唯一 A01–A05 状态，删除实施方案中的相反状态结论；State/no-persistence 继续明确为 PASS。
2. 修正 A05/T0 时序：Phase 0 审 target 设计，A05 后 production + target gate 原子落地，T0 PASS 后才合并。
3. S23A 接受现有 end-to-end baseline；若不接受，则显式授权唯一 tests-only characterization，不能继续悬置。
4. 把 S18 固定为每 owner 一个 index；把 S20 收窄为 `UseStepRequestInput | None`，否则现在降 P2。
5. 将 architecture parity 和 non-normative 调用链整改移出 execution P1 的关键路径；只保留 no-State/no-persistence negative gate。
6. A04 改为 per-change actual manifest；本 review 作为终局审计记录，不触发无限 review/cutoff 循环。
7. 完成上述文字收口后，只复核 R9–R13，不重新发现第 25 个简化点，也不重新打开已固定的 24 项账本。

## 6. 本轮验证记录

- 静态重读最新实施方案 1017 行及 requirements，核对 15 个 P1、9 个 P2、复杂度账本、B0/B1、T0、manifest 和最终状态表。
- 静态核对当前 `family_driver.py`：`_advance_scope_quantum()` 仍返回 `_AdvancedFrontier` marker，现有 tests 没有直接调用该 private owner；实施方案对 S23A direct evidence 的 `MISSING` 判断属实。
- 静态核对当前 `executor.py`/`resume_input.py`：failed retry 通过临时 frontier 调用现有 materializer，override 已走 codec decode；因此 S20 没有必要把显式参数放宽到完整 `GraphNodeInputBinding` union。
- 静态核对当前 `invocation.py`/`resume_admission.py`：action duplicate、publication duplicate 和 confirmed collision 是三个检查目的，但可分别在两个 owner 的各一次 enumeration 中完成；S18 当前“两个/三个 index”文字确有冲突。
- 未修改 production/tests；未运行 pytest、Pyright、`make check` 或全量 pre-commit。实施方案已记录的历史绿色结果不冒充本轮新增运行证据。

**第八次复审裁决：State 对齐和不实现持久化通过；24 项范围账本通过；当前不批准 `GSP-A05`。先一次性修正 R9–R13，其中 S18/S20 是仅剩的 P1 target 设计缺口，T0 应在批准后的原子实现中落地。修正后进行一次只核验这些闭合项的终局准入，不再扩大范围。**
