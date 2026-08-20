# Graph 执行代码语义保持型简化实施方案第七次复审

> **结论：当前 State 对齐与“不实现持久化”仍通过；实施方案整体没有闭合，新增调用链文档还引入了范围和唯一事实源问题。不得批准 `GSP-A05`，不得进入 Phase 1，也不得修改 production/tests。**
>
> 本轮按代码基线 `7944159`、最新实施方案、requirements、上次复审和新增的
> `execution-state-frontier-call-chain.zh-CN.md` 重新核对。复审不把 callback 的提交边界解释成
> 内置 Store 或 durability 能力。

## 1. 复审信息

- 复审日期：2026-08-20
- 复审对象：[实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)（当前 823 行）
- 新增交叉对象：[Execution / State / Frontier 调用链](execution-state-frontier-call-chain.zh-CN.md)（当前 335 行）
- 其他交叉对象：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[第六次复审](graph-semantics-preserving-simplification-implementation-sixth-review.zh-CN.md)、当前 execution/state 代码、README 与 architecture
- 范围：静态文档、调用链、owner、State 边界、证据矩阵和 manifest；不修改 production/tests，不重复运行重测试
- 审查原则：唯一事实源、零增复杂度、复用现有 owner、严格泛型、模块级导包、可推导事实不重复保存、逻辑最短闭环

## 2. 本轮确认的闭合项

### 2.1 State 与“不实现持久化”：通过

实施方案第 2.4、7.3、8 节的硬边界仍与当前代码一致：

1. `src/mote_kernel/state/**`、`tests/state/**`、command variant、reducer/validation、revision、status、run/resource/codec identity 均保持当前 shape；
2. 不新增 Store、repository、persistence port、database adapter、journal、event log、checkpoint、默认 commit backend 或第二 publication store；
3. concrete frame/publication/continuation 仍只由 execution-owned `ScopedFrameIndex`/continuation 持有；
4. `commit_transition()` 只负责现有 pure reducer candidate、可选 callback 和 exact-successor confirmation；没有 callback 时是进程内 candidate，不代表持久化成功；
5. 当前没有 `src/mote_kernel` 或 `tests` 的 production/test 差异触及 State。

因此本轮继续确认：**与当前 `GraphRunState`/`GraphRunCommand`/reducer 对齐通过；不实现 State 持久化通过；不得因为架构文档措辞差异生成 State 或 Store 工作项。**

### 2.2 新调用链的高层 owner 关系大体可核对

调用链文档中 `Graph.run()` → `GraphExecutor.prepare()` → `family_driver` → `GraphExecutionSession.next()` →
`SettleGraphNode` → `commit_transition()` → `reduce_graph_run()` 的主线，与当前
`facade.py`、`superstep.py`、`family_driver.py`、`session.py` 和 State reducer 的现状基本相符。
这只能作为待收口的说明草稿，不能抵消下面的 scope、source-owner 和证据阻断。

## 3. 新增阻断项

### R5（阻断）：调用链把 callback/in-memory 边界写成了持久化和崩溃恢复保证

新增文档出现了相互矛盾的表述：

- 第 1 节第 42 行写“持久化成功前，candidate 不能成为后续执行输入”；
- 第 6 节第 222–244 行写“persisted routing contributions”以及“保证崩溃后可以从 `SETTLED` 屏障恢复”；
- 同一文档第 8 节第 294–300 行又明确无 callback 时 candidate 是进程内确认结果；第 4 节第 166 行也说 executor 不拥有持久化；
- 当前 README 第 31–33 行和中文 architecture 第 14、31–33 行明确：callback 是提交边界，不内置具体 Store，也不保证跨进程 concrete-value recovery。

当前实现只检查 callback 返回的 `GraphRunState` 与 reducer candidate **结构相等**（见
`src/mote_kernel/execution/family_driver.py:129-153`）；它不判断 callback 是否 durable，也不能在没有调用方重新提供
State/continuation 时承诺进程崩溃恢复。另一个事实是，最后一个节点若是 `FailedGraphNode` 或
`InterruptedGraphNode`，`frontier_status()` 会得到 `AWAITING_RESUME`，并不会形成 `SETTLED`
（见 `src/mote_kernel/state/graph_state/frontier_model.py:112-122`）。

最小修复必须统一为：

1. 用“callback exact-candidate confirmation；无 callback 时的进程内 reducer successor”替代“持久化成功”；
2. 用“已确认的 State-held routing/join facts”替代“persisted”；
3. 只在“该 settlement 使全部节点为 `Succeeded`/`Skipped`”的分支描述 `RUNNING + SETTLED`；failure/interrupt 分支明确进入 `AWAITING_RESUME`；
4. 删除或改写“保证崩溃后恢复”，改为“若调用方提供已确认 State 与合法 continuation，可按现有 recovery 协议继续”；不新增任何 Store、journal、checkpoint 或 durability promise。

在这些文字修正前，调用链会把本轮明确冻结的范围重新打开，属于 `GSP-N01/N03` 与第 2.4 节硬门禁的阻断，而不是措辞可忽略项。

### R6（阻断）：新增调用链是未登记的潜在第二 normative source，且使 7.6 manifest 失效

该文件声称说明“当前实现”，但目前：

- 不在实施方案第 1 节关联记录或第 8.1 节 owner 表中；
- 不在 requirements 第 2 节事实源/文档分工中；
- 不在 README 稳定导航中；
- 没有文档状态、唯一 owner、canonical/translation 关系或 non-normative 标记；
- 实施方案第 7.6 节记录的 13-path cutoff 没有包含它，也没有包含随后新增的第六次复审。

它同时定义 State owner、commit 顺序、recovery boundary、frontier status 和 publication 安装顺序，不能静默地作为“普通说明”。
若把它当行为事实源，就会违反 `GSP-A01` 的唯一 owner；若把它当本轮交付文件，又会违反 `GSP-A04` 的 actual changed-file manifest。

最小选择只有两种，必须由 owner 明确其一：

1. 将它标成非规范性调用链说明，删除持久化/崩溃保证，明确“以 architecture、Node I/O、skip-output 和当前代码为准”，并在本轮 manifest 中记录它的实际变更；或
2. 正式登记唯一 owner 和 source precedence，同步 requirements、实施方案关联记录、README 导航及稳定 anchors，再以新的 exact manifest 复核。

在作出选择前，不能把旧 13-path cutoff 当作当前 A04 证据。至少要纳入旧 cutoff 漏掉的第六次复审和调用链文件；本复审文件落地后还必须再次更新 cutoff，不能用固定文件数量代替 actual paths。

### R7（重要）：resume 图示和 active-lease 语义容易制造第二 runner

调用链第 7 节把 `Graph.resume_failed / resume_interrupted / skip_failed` 写成执行入口。当前 facade 中这些是构造
`ResumeAction` 的 action factory；真正执行仍是 `Graph.run(state=..., continuation=..., resume=...)`，代码入口见
`src/mote_kernel/execution/facade.py:601-623`。requirements 的 `GSP-N01` 明确禁止新增 `Graph.resume()` 或第二
resume runner。图示应改为“`Graph.resume_*()` 构造 action → `Graph.run(..., resume=...)` → 内部
`GraphExecutor.resume()` 生成 prepared command”。

同节第 274 行写“后续 recovery 确认旧 attempt 已停止后再 exact fence”，但当前 `plan_fences()` 只对调用方提供的 active
lease 做 exact `FenceGraphExecution` 规划（`src/mote_kernel/execution/invocation.py:229-268`）。旧 attempt 已停止/丢失是调用方
传入 State 时的前置确认，不是 kernel 内部 handshake；README 已明确 kernel 不仲裁并发存活 worker。该责任必须写回调用方边界，不能暗示新增 lease coordinator 或持久化 recovery service。

### R8（重要）：commit 的“原子/权威”措辞需限定为现有协议

调用链第 193、232、297 行把 reducer candidate、外部 callback 和“原子提交”连成无条件保证。当前 `GraphCommit` 协议只要求异步 callback
返回 exact successor；原子性、durability 和跨域 store 由调用方自行决定，且本轮不实现。文档应统一使用“现有 commit callback 边界/确认”并保留：

- State candidate 未获确认前不替换 invocation memory binding；
- 无 callback 时立即采用进程内 pure reducer successor；
- callback 的结构相等确认不等于 object identity，也不等于持久化确认；
- frame/publication 仍按现有 post-commit installation 顺序处理。

## 4. 第六次复审阻断项仍未关闭

新增调用链没有修复下列既有问题，故整体裁决不变：

1. **唯一状态真相未闭合（R1）**：requirements 第 7 节仍称 `A01/A02/A04` 已形成，而实施方案第 11.1 节把 `A01` 标为 `BLOCKED`、`A04` 标为待 requirements owner 接受；requirements review 又把 A01/A03/A04 列为未闭合。
2. **复杂度账本未量化（R2）**：S11、S14、S17、S18、S20、S23B 仍只有“删除/最多新增”和 O(n²) 定性描述，没有逐 P1 的 before→after 扫描/分支/变量数、精确 nominal signature 和 cache 生命周期证明。
3. **owner-specific baseline 不足（R3）**：S23A 没有直接覆盖 `_advance_scope_quantum()` 的 `AdvanceGraphFrontier`/`None` 行为；S14 没有 recovery `_NestedOutcome` 的直接 boundary case；S18 没有 `invocation.py::plan_resumes()` duplicate action-coordinate case；S10/S11 没有 malformed、顺序、重复 target 的 characterization。
4. **exact-shape owner 仍含歧义（R4）**：S03–S06 复用一个 architecture test path，却未给出按单元可核对的子断言；S08 对 snapshot guard 是否直接读取 `joins_by_source` 的表述前后不一；S18 将“复杂度账本”当作 gate，但没有可执行 AST/source 条件。

这些问题均不因 State/no-persistence 通过而自动消失，也不能用新增调用链替代 case-level evidence 或 target gate。

## 5. 当前准入裁决

| 条件 | 当前结论 | 依据 |
| --- | --- | --- |
| State/不实现持久化 | **通过** | State tree、State tests、command/reducer/protocol 保持；无 Store/State mirror target |
| `GSP-A01` owner/source | **未闭合** | requirements/implementation 状态冲突，且新增调用链未登记 owner |
| `GSP-A02` 原子边界 | **基本具备，尚不可批准** | 24 个 execution-only 单元仍有复杂度与 exact nominal 缺口 |
| `GSP-A03` 行为证据 | **未满足** | target gates 全部 `PENDING`，R3 的直接 owner cases 缺失 |
| `GSP-A04` 文档门禁 | **旧 cutoff 失效** | 13-path 记录漏掉第六次复审、调用链及本轮新增记录 |
| `GSP-A05` 显式批准 | **不可申请** | R1–R8 尚未收口 |
| `GSP-A06` P2 单项设计 | **未触发** | 9 个 P2 不继承任何 P1 状态 |

## 6. 最小收口顺序

1. 先由 requirements/architecture owner 决定调用链文件是 non-normative 说明还是正式唯一 owner；同步删除持久化/崩溃恢复越界表述，不修改 State 或新增 storage 能力。
2. 以新的 review cutoff 生成 exact actual changed-file manifest，至少覆盖旧 13 paths、此前第六次复审、调用链和本复审；对明确排除的用户 `example/` 文件单独记录排除理由。
3. 统一 requirements 与 implementation 的 A01–A04 状态；保持 A05 阻断。
4. 补逐 P1 净复杂度账本和 R3/R4 的直接 characterization/exact assertions；证据不足的单元保持未批准，不新增 cache、wrapper、sentinel 或泛型擦除。
5. 完成一次只审 A01–A04 的准入复核。显式 A05 之前，production/tests 继续保持不变。

## 7. 本轮验证记录

- 静态重读实施方案 823 行、requirements 112 行、调用链 335 行及第六次复审；核对 State owner、commit callback、frontier status、resume dispatch 和 manifest。
- `git diff 7944159 -- src/mote_kernel tests` 无 production/tests 差异；没有修改 State 或 execution 代码。
- 代码核对确认：`frontier_status()` 对 failure/interrupt 返回 `AWAITING_RESUME`；`commit_transition()` 只做 reducer candidate 与 exact-successor check；`plan_fences()` 不提供旧 worker 存活仲裁。
- 未在本轮运行 pytest、Pyright、`make check` 或 build；文档中已有的历史 baseline 数字不作为本轮新增运行证据。

**第七次复审裁决：State 对齐/不做持久化通过，但新增调用链必须先收回范围并纳入唯一 owner/manifest；实施方案整体仍未闭合，不批准 `GSP-A05`，不得进入 Phase 1 或修改 production/tests。**
