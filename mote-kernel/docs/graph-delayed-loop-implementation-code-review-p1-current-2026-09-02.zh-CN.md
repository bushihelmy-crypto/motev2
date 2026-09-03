# Graph 延迟回环 P1 当前代码验收评审

状态：审计中

评审日期：2026-09-02

评审对象：当前工作树中的 Graph 延迟反馈回环 P1 实现、State/reducer、routing、compiler、recovery、nested family driver、测试、复杂度门禁及实施计划。

本文是本轮独立评审记录。旧评审文档不修改、不覆盖。审计过程中发现的问题逐项写入“问题台账”，以便实现方按本文件整改。

## 1. 已锁定的验收原则

- Graph 不实现失败重试；Failover 是唯一 retry owner。
- 并行 sibling 互不构成控制依赖：一个失败不取消另一个，已运行继续跑，当前 frontier 中的 Pending 继续排空。
- 状态优先级固定为 `Pending > Failed > Interrupted > Settled`。
- Failed 节点不能恢复成 Pending，也不能 resume/skip/retry。
- 目标是 0 负债、唯一真相、复用基础设施、直白架构、fail-closed 安全边界。

## 2. 本轮问题台账

### P0-1：`join_progress` 仍可注入不存在的历史 arrival，也可无条件丢弃旧 progress

**状态：已用当前工作树复现，阻断 P1 验收。**

代码位置：

- `src/mote_kernel/state/graph_state/execution_transitions.py:96-136`
- `src/mote_kernel/state/graph_state/execution_transitions.py:195-213`
- `src/mote_kernel/state/graph_state/validation.py:39-77`

`_validate_next_activations()` 只验证 next activation 的 cause；`advance_graph_frontier()` 随后直接用
`command.join_progress` 覆盖 State。恢复态 validator 只检查 arrival 的形状、坐标早于当前 superstep 和 node id 属于
声明在该 record 里的 `sources`，没有证明这个 arrival 来自上一份 authoritative frontier 中真实成功并实际选择相应 route
的 source，也没有比较 old/new progress 的合法差分。

当前代码已确认接受两种非法转换：

1. 当前 frontier 只有 `a[0]` 成功，却可写入 `never-ran[0]` 已到达 `(b, never-ran) -> c` 的 progress；
2. State 已持有该 partial progress，下一次 `AdvanceGraphFrontier(..., join_progress=())` 可直接把它丢掉。

这会让恢复与实时 routing 的因果事实分叉：伪 arrival 后续可参与 Join，合法 arrival 也可能被静默遗失。完整
`ActivationReference` 只提高了记录精度，不自动证明记录真实。

必须让唯一 reducer 校验 progress 差分：旧 partial 默认原样保留；新增 arrival 只能来自当前 authoritative frontier 的
successful settlement 与实际 selected route；只有同一 command 合法完成相应 Join 时才能消费旧 partial；不得替换、伪造或
无关删除。State 不应再增加第二份历史真相。

### P0-2：公开的 state-only 恢复会执行拓扑无法解释的伪造 activation

**状态：已用当前 `Graph.run(state=...)` 端到端复现，阻断 P1 验收。**

代码位置：

- `src/mote_kernel/execution/engine/snapshot_guard.py:22-40`
- `src/mote_kernel/state/graph_state/validation.py:100-129`
- `src/mote_kernel/execution/facade.py:588-623`

恢复态校验只确认 frontier target 属于 compiled graph，并确认 cause 坐标早于 target；没有确认 source node、route 和
reference collection 命中该 target 的唯一 compiled activation gate。对无输入节点，空 `ScopedFrameIndex` 也不会形成保护。

当前代码已端到端接受并执行以下 State：真实拓扑只有 `a -> b -> END`，恢复 State 却直接把 `b[1]` 设为 Pending，cause
写成不存在的 `ghost[0] + bogus route`。调用公开 `Graph.run(state=state)` 后，`a` 零调用、`b` 被调用，最终返回
`CompletedResult`。

这不是“内部 forged object 测试”或 P2 durable adapter 问题；当前公开恢复入口已经能把非法控制事实变成真实 callable
调用。必须在任何 claim 前使用 compiler 产出的唯一 activation-gate proof 校验 START/普通 direct/conditional/Join/feedback
cause：target、source、route、reference 基数和 gate 必须精确匹配；零个或多个 gate 都 fail closed。不要在 snapshot guard
再推导一套拓扑算法，compiler 应产出单一可消费 proof，live routing 和 recovery admission 共用它。

## 3. 已核对通过项

（审计进行中。）

## 4. 门禁与复现记录

（审计完成后填写实际命令、结果和最小复现。）

## 5. 结论与整改顺序

（审计完成后填写。）
