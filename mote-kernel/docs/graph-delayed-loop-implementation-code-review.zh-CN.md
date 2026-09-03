# Graph 延迟回环 P1 实现代码评审

状态：**不通过，当前不能交付**
评审日期：2026-09-02
评审对象：当前工作树中的 Graph P1 实现，以及与它同步修改的 State、recovery、测试和文档

本评审只审实现是否符合已经敲定的 Graph 边界，不重新讨论产品取舍。下面的规则就是实施方要执行的规则，**没有待
owner 再裁决的问题**。

## 1. 先说结论

| 项目 | 结论 |
| --- | --- |
| 一个显式 `Graph.failure()` 不阻止同一 frontier 的并行 sibling | **基本正确，但测试和文档没有锁死** |
| 已 claim 的 sibling 继续完成 | **通过** |
| 尚未启动、正在等资源的 Pending sibling 继续执行 | **通过，需补 `max_parallel_tasks=1` 回归** |
| failure 不 routing、不创建下一 frontier | **通过** |
| Graph 不做 failed retry/skip，Failover 是唯一重试 owner | **通过，不能回退** |
| `Failed + Interrupted` 按最终优先级结算 | **通过（保持 Failed 优先）** |
| nested failed child 的清理不影响其他 sibling 执行，且与 recovery 一致 | **不通过，P0** |
| runtime 与 recovery 对混合结果给出同一语义 | **不通过，P0** |
| public result 正确表达 terminal Failed 的失败和中断诊断 | **通过，需补边界测试** |
| state-led recovery 能重建完整失败/子图证据 | **不通过，P1** |
| 复杂度、无用私有字段和全仓门禁 | **不通过，P1** |
| 文档、测试、实现互相一致 | **不通过，P1** |

核心规则只有两句：**一个并行节点失败，不得取消其他 sibling 的执行；已经在跑的继续跑，没启动的也继续排队执行。
但当这个 frontier 没有 Pending 后，`Failed` 按最终约定优先于 `Interrupted`，run 直接进入 terminal Failed；未恢复的中断
只能作为诊断/清理事实，不能再把 run 变回 Awaiting。**

## 2. 已经敲定的唯一状态规则

同一个 frontier 的状态必须按最终约定按下面顺序派生：

```text
有 Pending                         -> EXECUTABLE
没有 Pending，有 Failed            -> FAILED（terminal）
没有 Pending，没有 Failed，有 Interrupted
                                   -> AWAITING_RESUME
全部 Succeeded                     -> SETTLED
```

这不是“失败重试”。`Graph.failure()` 只是一次已经确认的业务失败事实，Graph 不会把它改回 Pending，也不会因为它
失败而重新调用该节点。失败与中断同时出现时，先按 Pending 规则排空所有 Pending；排空后由 Failed 终止优先，Interrupted
保留为诊断或由 child owner 清理，但不再开放 interrupt resume。

因此，以下三种情况都必须成立：

1. `Failed + Pending`：继续执行所有 Pending；不 routing，不创建下一 frontier。
2. `Failed + Interrupted`：Pending 排空后直接进入 terminal `FAILED`；结果保留 failure 和 interrupt 诊断，但不能 resume。
3. 只有 `Interrupted`、没有 Failed 时才返回 `AwaitingResume`；恢复后再按普通结算继续运行。

普通 callable 抛出的 `TaskRaised` 是基础设施异常，不是 `Graph.failure()`。现有“停止新调度、排空/关闭 session、精确
fence 后传播异常”的语义要保留，不能为了修复业务 failure 而把普通异常改成 Graph 失败重试。

## 3. 必须修复的代码问题

### 已确认：`Failed + Interrupted` 保持 Failed 优先，不改这条规则

涉及：

- [`frontier_model.py`](../src/mote_kernel/state/graph_state/frontier_model.py#L92-L104)
- [`execution_transitions.py`](../src/mote_kernel/state/graph_state/execution_transitions.py#L192-L207)
- [`superstep.py`](../src/mote_kernel/execution/engine/superstep.py#L56-L77)
- [`validation.py`](../src/mote_kernel/state/graph_state/validation.py#L168-L192)

当前 `frontier_status()` 的顺序正是 `Pending > Failed > Interrupted > Settled`，这符合最终设计，不是 bug。它表达的
是：失败不会提前打断仍为 Pending 的 sibling；但 Pending 全部排空后，Failed 是本 frontier 的最终 disposition。

这里不要求把顺序改成 `Pending > Interrupted > Failed`，也不要求给混合结果增加“失败但继续 Awaiting”的第三种生命周期。
`settle_graph_node()` 在混合结算后清理 execution/resource、`prepare_superstep()` 返回 `FailedGraph`、对 terminal Failed
调用 `resume_interrupted()` 被拒绝，都是预期行为。Interrupted 节点可以作为诊断事实保留；child owner 若持有 live handle，
按统一的 terminal-failure 清理规则收尾。

必须守住的只有这些边界：

- 有 Pending 时继续调度所有 Pending；failure 只阻止 routing 和下一 frontier；
- 已启动的普通 sibling 继续完成，资源 waiter 释放后也继续执行；
- 没有 Pending 且存在 Failed 时进入 terminal Failed，不得偷偷重试 Failed 节点；
- 只有没有 Failed、没有 Pending、存在 Interrupted 时才进入 `AwaitingResume`，此时才允许 resume；
- terminal Failed 的结果可以同时带 failure 和未恢复 interrupt 的诊断，但这些 interrupt 不是可继续执行的入口；
- `resume_graph_nodes()` 仍只允许把 `InterruptedGraphNode` 改回 Pending，绝不打开 Failed 节点。

验收：`a=Failed、b=Interrupted` 在 Pending 排空后得到 Failed，结果保留真实 failure/interrupt 诊断，resume 被拒绝；
`a=Failed、b=Pending` 则先执行 b，且不发生 routing/下一 frontier。

### P0-1：nested child 的失败清理时序和 recovery 证据必须统一

涉及：

- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L599-L612)
- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L689-L724)

`_abort_awaiting_children_after_failure()` 发现任一 `FailedChild` 后，会把 `AwaitingResume` child 改成
`AbortedChild("nested graph was superseded by a sibling failure")`。按最终的
`Pending > Failed > Interrupted > Settled` 规则，**terminal Failed 清理未恢复的 Awaiting child 是允许的**，所以这个
helper 本身不再被判定为必须删除；但清理不能提前影响仍应执行的 sibling，也不能在 recovery 中换成另一种假失败。

必须做：

- Failed child 不得取消仍在执行的 active sibling，也不得阻止尚未启动的 Pending sibling；父图必须先按 Pending 规则
  排空这些工作；
- Pending 排空、frontier 确认进入 terminal Failed 后，才允许对未恢复的 Awaiting child 做 abort/释放，这是终局清理，
  不是业务失败重试；
- runtime 和 recovery 使用同一个 typed abort reason、child 状态和父节点投影，不能在 recovery 中写死
  `"recovery-preflight-failure"` 这种另一种失败事实；
- terminal Failed 结果可以保留真实 interrupt/abort 诊断，但不能再提供 resume 入口；
- 只有整个 Graph 被外部取消/Abort 时，才沿既有外部取消 owner 处理取消；不要把业务 failure 偷换成外部取消。

验收：父图有一个失败 child、一个 awaiting child 和一个 Pending/active sibling 时，Pending/active sibling 都完成；最终
父图为 Failed，awaiting child 的终局清理和诊断在 live/recovery 两条路径完全一致，不出现 recovery 专用伪失败文本。

### 已确认：结果类型按最终 Failed 优先语义保持现状

涉及：

- [`result.py`](../src/mote_kernel/execution/result.py#L316-L406)
- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L1238-L1299)

当前 `_FailedGraphResult` 同时带 `failures` 和 `interrupts`，这是最终语义允许的：interrupt 在这里是未恢复中断的诊断，
不是可继续执行的入口。`_AwaitingResumeGraphResult` 只带 interrupts 也正确，因为只要本 frontier/family 已有 Failed，
就不再返回 Awaiting。

不需要为混合状态增加第三种生命周期，也不需要把 failures 塞进 Awaiting。需要守住的是：

- `Failed + Interrupted` 在 Pending 排空后投影为 `FailedResult`，保留真实 failure/interrupt 诊断；
- terminal Failed 上的 interrupt 不能被 `resume_interrupted()` 接受；
- 只有没有 Failed 的 frontier 才能投影 `AwaitingResumeResult`，并按现有 codec/ID 恢复；
- projection 仍必须依据 authoritative State 和 child evidence，不依据“谁先结束”猜结果；
- nested child 的 failure/interrupt/abort 要按 scope 完整投影，不能只投影父节点一层；
- terminal Failed 重载不 claim、不调用 callable、不调用 Port，已确认 Failed 节点不重跑。

验收：`a=Failed、b=Interrupted` 得到 `FailedResult` 且两类诊断完整，resume 被拒绝；只有 `b=Interrupted` 时才得到
`AwaitingResumeResult`。

### P0-2：recovery 和实时执行的 terminal-failure 清理不一致

涉及：

- [`recovery.py`](../src/mote_kernel/execution/engine/recovery.py#L836-L867)
- [`recovery.py`](../src/mote_kernel/execution/engine/recovery.py#L914-L929)
- [`recovery.py`](../src/mote_kernel/execution/engine/recovery.py#L1099-L1111)

当前 recovery 有 `failure_dominates`。保留 Failed 优先本身是正确的，但它把 Awaiting child 合成为
`project_failure_settlement(..., "recovery-preflight-failure")`；实时路径则通过 child handle 产生
`AbortedChild("nested graph was superseded by a sibling failure")`。两条路径因此留下不同的 child 状态、失败原因和
诊断视图。

必须做：

- 保留 recovery 的 `Pending > Failed > Interrupted > Settled` 规则：Pending 先排空，之后 Failed 终止优先；
- 不要把 Awaiting child 伪造成新的业务 `FailedGraphNode`；recovery 必须复用与 runtime 相同的 typed abort/cleanup
  事实、reason 和 child boundary；
- Failed child 可以和 Awaiting sibling 暂时共存，但最终进入 Failed 时要完成同样的终局清理；不能让 recovery 偷留一个
  runtime 不会留下的 awaiting child；
- `FailedResult` 保留真实 failure/interrupt/abort 诊断，terminal Failed 不提供 resume；
- recovery、实时执行和结果 projection 的 child 状态、disposition、错误诊断必须等价；
- 外部取消/显式 Abort 继续按现有 owner 语义处理，不能把它和业务 failure 混为一类。

验收：同一组 root/child State，live run 和 state-led recovery 都先排空 Pending，最终得到相同的 Failed disposition、
相同的 failure/interrupt/abort 视图和相同的 child 状态；两条路径都不得重复执行已经确认的 Failed 节点。

### P1-1：state-led recovery 会丢失 nested failure 和输入/结果证据

涉及：

- [`facade.py`](../src/mote_kernel/execution/facade.py#L582-L605)
- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L1180-L1208)
- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L1238-L1299)
- [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L107-L117)

当前 `Graph.run(state=...)` 在没有 continuation 时主动使用空的 `child_states` 和空的 `ScopedFrameIndex`。而
`fresh_root()` 先提交 `StartGraphRun(result=None)`，再把 graph input 只安装到进程内存。`GraphCommit` 目前也是只写入、
只返回 State 的 callback，没有 typed evidence reader。

实际后果是：live 路径可以看到 root failure 和 child failure，但 state-only 重载只能看到 root failure；缺少 input、
publication 或 child boundary 时还可能在错误的地方才失败，或者错误地尝试重新 materialize。

当前计划已经承诺“exact State head + 在该 head 可见的完整 live evidence”的 state-led recovery，所以这里不是可忽略的
测试差异。

必须做：

- 增加通用 typed recovery snapshot/evidence reader；不要增加 loop 专用 Store、runner 或第二份 State；
- `StartGraphRun` 与 admitted graph input evidence 原子提交；
- successful settlement 与 canonical node result/publication 原子提交；
- child State、child result/boundary evidence 可读取并重建完整 family view；
- evidence 的版本、scope、definition、descriptor、codec、nominal type 在 claim 前全部校验；
- 缺 evidence 时 fail closed，不能回退 seed、扫描“最新值”或重跑已确认 producer；
- evidence/payload 要有明确大小和版本边界；corruption、codec mismatch 或 admission 错误不能把 value、payload 或
  secret 的 `repr` 写进异常和日志；
- 只有 durable commit 确认后，才替换内存 State、安装 frame。

如果这一版明确不做 state-led 完整恢复，就必须同步收窄实施计划和 public contract，删掉“与完整 continuation 等价”的
承诺和测试；不能一边保留承诺，一边继续用空 frame/空 child state。按当前计划，推荐直接补齐通用 reader。

验收：live 与 state-only 对 root failure、nested failure、interrupt、child boundary、Graph input 的视图完全一致；
缺任何一条必要 evidence 都在 claim 前以稳定 typed error 失败，零 callable、零 Port 调用。

### P1-2：recovery 新增复杂度和无用字段直接触发 0 负债门禁

涉及：[`recovery.py`](../src/mote_kernel/execution/engine/recovery.py#L327-L350)

当前静态质量分析明确报出以下无用私有字段：

```text
_CyclePublicationAvailability.temporal_coordinate
_RecoveryCycleSignature.absolute_publications
_RecoveryCycleSignature.relative_publications
_RecoveryCycleSignature.current_resume_inputs
_RecoveryCycleSignature.current_child_boundaries
```

此外，结构复杂度、重复结构、调用边和热点指标均超过当前 ratchet。不能通过提高阈值、加 suppress、登记“以后再还”或
再包一层 dataclass 来过门。

必须做：

- 删除未参与证明的字段；或者把签名缩成真正参与不动点判断的最小不可变 proof key；
- 删除只为当前测试存在的包装和分支，复用已有 State/frontier/evidence 基础设施；
- 不修改 ratchet 基线，不增加兼容 alias，不保留第二套 recovery 状态模型；
- 让 recovery 逻辑直白：先做 admission，再按同一 frontier 规则推进，不用复杂组合去掩盖失败优先级。

验收：复杂度门禁无 `unused_private_definitions`，结构指标不回退，且 `make check` 通过。

### P1-3：全仓 generic integrity 门禁仍红，但不能由 Graph 绕过

当前完整 pytest 结果为 **1152 passed, 3 failed**。失败是：

- `tests/architecture/test_complexity_gate.py::test_proven_debt_is_absent`；
- `tests/architecture/test_complexity_gate.py::test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked`；
- `tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types`。

generic integrity 的具体问题是：

```text
failover/policy.py:103 object erases the boundary type
failover/policy.py:316 object erases the boundary type
```

Failover 是独立 owner，但这是全仓交付门禁。应在 failover owner 内把 `fixed_rules()`、`_return_to_model()` 等边界改成
保持真实类型参数的 typed 形状，不能把 `object` 留着，也不能让 Graph 增加 cast/兼容层来遮掩。Graph 仍然不负责失败
重试。

本轮门禁的实际结果也说明当前不能交付：

- `python -m pytest -q --disable-warnings --maxfail=20`：`1152 passed, 3 failed`，失败为上面两个 complexity 测试和
  一个 generic integrity 测试；
- `make check`：ruff 已通过，随后在 `pyright` 处失败（170 个类型错误），因此后续 complexity、完整 test 和 package
  check 没有被 `make` 执行；错误集中在新增 failover 类型边界/测试构造，以及
  `tests/architecture/test_source_discipline.py` 的可选 AST 节点类型；
- monorepo 根目录 `pre-commit run --all-files`：除 `kernel structural complexity ratchet` 外的 hooks 通过；该 hook
  的两个 complexity 测试仍失败。不能把这些红灯标成“与 Graph 无关”后交付，必须修复 owner 或拆分未完成改动，再重新
  跑全套门禁。

## 4. 测试必须迁移和新增

### 4.1 必须迁移或补强的断言

这些测试要和最终优先级统一，不能继续写成“Failed 后先 Awaiting”：

- `tests/state/graph_state/test_frontier_model.py`：保留并扩展完整优先级表，明确 `Pending > Failed > Interrupted > Settled`；
- `tests/state/graph_state/test_execution_transitions.py`：保留 `Failed + Interrupted -> terminal Failed`，同时增加
  `Failed + Pending` 仍持有同一 claim 并继续结算 Pending 的断言；
- `tests/state/graph_state/test_state_validation.py`：允许 terminal Failed 保留 Interrupted 诊断，但必须拒绝对该状态
  resume；只含 Interrupted、没有 Failed 时才是 Awaiting；
- `tests/execution/test_resource_protocol.py::test_resource_waiters_preserve_mixed_failure_and_interrupt_outcomes`：
  保持 `FailedResult`，failure 和 interrupt 都保留，`a,b,c` 都执行；补充 terminal Failed 不能 resume；
- `tests/execution/test_graph_api.py::test_failed_result_views_preserve_canonical_root_to_child_scope_order`：根节点同时有
  failure 和 interrupt 时保持 Failed，并保留 root/child 的完整 scope 诊断；
- `tests/execution/test_graph_api.py::test_failed_nested_child_dominates_an_awaiting_sibling_child`：保留 terminal Failed
  断言，补充 active/Pending sibling 仍完成，并固定 awaiting child 的终局 abort 诊断；
- `tests/execution/test_family_driver_local_ownership.py`：保留
  `_abort_awaiting_children_after_failure()` 的必要测试，但验证它只清理终局 awaiting child，不触碰 active/Pending sibling；
- `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_makes_a_failed_child_dominate_an_awaiting_sibling`：
  保持 mixed recovery 为 Failed，但要求与 runtime 使用同一个 child abort 状态、reason 和 projection。

### 4.2 必须新增的场景

- `max_parallel_tasks=1`：第一个节点显式失败，未启动 sibling 仍执行；
- 一个 active sibling 失败时，其他已运行 sibling 继续完成；
- resource owner 显式失败后，resource waiter 释放等待并执行；
- failure 后不 routing、不创建下一 frontier；
- `Failed + Interrupted` 在 Pending 排空后返回 Failed，双方诊断都保留，resume 被拒绝；
- 只有 `Interrupted` 时返回 Awaiting，resume 后继续执行；
- nested failed child + awaiting child 在终局 Failed 时按统一规则清理，不 abort active/Pending sibling；
- mixed live execution 与 recovery 完全等价，包括 child abort reason 和诊断；
- terminal Failed 重载零 claim、零 callable、零 Port 调用；
- state-only 与 continuation recovery 的完整 failure/child view 等价；
- 普通 callable 抛异常仍停止新调度、精确 fence、传播 `TaskRaised`，不能被误判为 Graph failure retry；
- producer 执行后、settlement 前崩溃允许按未确认 Pending activation 重放，但不能把已确认 Failed 当作可重试。

有意义的通用测试要迁移而不是删除：resume codec 校验、frame 安装原子性、多 scope continuation、interrupt resume
的资源/child/commit failure、混合 frontier 不重跑已成功节点等都要保留。只验证 failed retry、failed input override、
skip 或 failed output substitution 的旧测试和 examples 才可以果断删除；不要为了迁就测试留下 legacy 生产路径。

## 5. 文档必须同步，不要让实现和计划继续互相打架

### 5.1 实施计划

[`graph-delayed-loop-implementation-plan.zh-CN.md`](./graph-delayed-loop-implementation-plan.zh-CN.md) 至少要改：

- §7.1：保留“Failed 优先终止”，明确最终顺序是 `Pending > Failed > Interrupted > Settled`；
- §13 的“混合结算”：保留 Pending 排空后直接 terminal Failed；Interrupted 只作为诊断/child 清理事实，不再进入
  resume；
- §14：保留 terminal-failure 下的 sibling abort 设计，但补上“不得取消 active/Pending sibling，且 runtime/recovery
  必须使用同一 abort 事实”的限定；
- §7.1、§10 和验收矩阵：明确 Pending sibling（包括资源 waiter）继续执行，failure 只阻止 routing/下一 frontier；
- 结果契约：Awaiting 只表达没有 Failed 的中断等待；terminal Failed 可以携带未恢复 interrupt 的诊断，但不能 resume；
- state-led recovery：明确 exact State head 加 live evidence 的读取/原子提交契约，不能只写空 frame 的概念图。

### 5.2 调用链和历史审计文档

[`execution-state-frontier-call-chain.zh-CN.md`](./execution-state-frontier-call-chain.zh-CN.md) 当前状态图和失败结算段落
仍写着“无 Pending 有 Failed/Interrupted -> Awaiting”，需要改成“无 Pending 有 Failed -> FAILED；仅有 Interrupted
才 Awaiting”；同时保留“最后一个 Pending 后直接 FAILED”的最终语义，并补充 Pending sibling 必须先排空。

历史审计和回复文档可以保留，但必须注明“历史记录”，不能继续把“Failed 优先”写成待裁决；也不能让 runtime 和
recovery 对 sibling abort 使用不同的状态或原因。

## 6. 这些正确行为必须保护，修复时不能顺手改坏

- `Graph.failure()` 是显式业务失败，不是异常，不是 retry signal；Graph 不实现失败重试。
- Failover 是失败重试唯一 owner。可重试 Port outcome 通过 failover 的新 activation 表达，不能让 Graph 重跑
  `FailedGraphNode`。
- 同一 frontier 的并行节点互相独立：一个失败不能取消 sibling；已运行的继续跑，Pending/资源 waiter 也继续跑。
- 含 failure 的 frontier 永不 routing、永不产生下一 frontier；失败节点确认后永不回到 Pending。
- 未确认的 Pending activation 在崩溃后可以按基础设施重放规则执行；这和已确认 Failed 的 retry 不是一回事。
- 普通 callable 异常仍走 `TaskRaised`/fence 语义；不要把它改造成 Graph failure。
- 外部取消/Abort 仍由既有 owner 负责清理 child；不要把业务 failure 伪装成外部 abort。
- `Graph` 仍是唯一公开 facade，`GraphRunState`/唯一 reducer/现有 commit、session、resource、evidence 基础设施仍是
  唯一真相；不得增加第二 runner、平行 State、隐藏 mutable cursor、兼容 alias 或测试专用生产路径。
- 本切片没有 `Graph.feedback(...)` 生产路径；不要借修 failure 边界提前加入另一套 feedback runner。

## 7. 交付门槛

实施方完成代码和测试后，至少要同时满足：

1. 上述 P0/P1 项全部关闭，特别是 mixed failure/interrupt、nested sibling 和 recovery parity；
2. 失败节点、Pending sibling、resource waiter 的调用次数和最终 State 有确定性测试；
3. state-led evidence 的读取、版本联结、缺失 fail-closed 和子图诊断有端到端测试；
4. evidence/payload 的大小、codec、corruption 和 secret-safe error 有测试；
5. `python -m pytest -q --disable-warnings --maxfail=20` 全绿；
6. `make check` 全绿；
7. 在 monorepo 根目录运行 `pre-commit run --all-files` 全绿；
8. 不提高 ratchet、不加 suppress、不恢复任何 failed retry/skip alias。

本次评审不要求 owner 再做架构选择。按上面的状态规则和改动清单实现、迁移测试、同步文档并跑绿门禁后，再提交下一
轮复审即可。
