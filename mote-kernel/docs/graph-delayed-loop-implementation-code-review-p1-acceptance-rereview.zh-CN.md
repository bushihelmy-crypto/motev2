# Graph 延迟回环 P1 再次验收评审

状态：**未通过，当前 P1 不可交付**

评审日期：2026-09-02

评审对象：当前工作树中的 P1 Graph 延迟反馈回环实现、State reducer、routing、compiler、recovery、nested family
driver、测试与实施计划。

本文是本轮独立评审台账。旧评审文档不修改、不覆盖；后续发现的问题直接追加到本文，不依赖压缩记忆。

## 1. 结论先说人话

direct self-feedback 的主路径已经能跑：首轮读 seed，后续读紧邻上一轮 publication，terminal route 能结束，达到上限会熔断，
Graph 失败不重试，`Pending > Failed > Interrupted > Settled` 也已落地。

但 P1 仍没有闭合。当前 State/reducer/runtime 仍能接受并执行无法由真实拓扑和真实提交事实证明的 activation，至少有以下四类
硬漏洞：

1. 历史 Join arrival 只有坐标，没有“源节点成功并实际选择该路线”的权威提交证据；伪造历史 arrival 可以拼出 Join。
2. 同一个 Join source 在不同 activation occurrence 到达时，按 node id 静默合并，普通无环 DAG 也会把两个 occurrence 拼成一次 Join。
3. activation 的拓扑准入不完整：非法 target、漏 successor、编译阶段未证明唯一 gate 的图都能绕过或拖到运行时。
4. `AdvanceGraphFrontier.join_progress` 可以无条件丢弃或替换已有 partial progress。

这不是测试数量问题，也不是复杂度数字问题，而是会改变真实执行结果、恢复结果或安全边界的逻辑问题。因此当前 P1 不能验收通过。

## 2. 已确认的最终语义（本轮不再反复讨论）

- Graph 不实现失败重试；Failover 是唯一 retry owner。
- 并行 sibling 互不构成控制依赖：一个失败不取消另一个，已运行继续跑，当前 frontier 中的 Pending 继续排空。
- 状态优先级固定为 `Pending > Failed > Interrupted > Settled`。
- Failed 节点不能恢复成 Pending，也不能 resume/skip/retry。
- 目标是 0 负债、唯一真相、复用基础设施、直白架构和 fail-closed 安全边界。

## 3. 本轮问题台账

### P0-1：历史 Join arrival 没有真实性和提交证据

**状态：已确认，阻断验收。**

代码位置：

- `src/mote_kernel/state/graph_state/validation.py:39-77`
- `src/mote_kernel/state/graph_state/execution_transitions.py:96-136`
- `src/mote_kernel/execution/engine/routing.py:265-350`

当前只检查 run id、arrival 早于当前 superstep、source node 属于 Join source 集合、tuple canonical/distinct，以及 arrival
与 `state.join_progress` 相等。没有检查 source activation 是否成功、是否实际选择了该 route、arrival 是否由真实 routing 产生并
已经提交。

最小复现已确认：构造 `(a,b) -> c` 的 partial progress，只放入伪造的 `a[0]`；当前 frontier 放入合法成功的 `b[1]`；提交
`c` 的 cause `(a[0], b[1])`。`reduce_graph_run()` 接受并推进 `c`，尽管 `a[0]` 从未成功执行。

必须收口：历史 arrival 只能绑定 authoritative、commit-linked 的成功 settlement/selected-route evidence；若当前 P1 无法证明，
最简单安全方案是暂时拒绝无法证明的跨 superstep Join。不能只按坐标放行，不能新增第二套 State truth。

### P0-1b：直接自反馈的恢复态也只验证坐标，不验证前驱提交事实

**状态：已确认，阻断验收。**

代码位置：

- `src/mote_kernel/execution/engine/routing.py:186-207`
- `src/mote_kernel/execution/engine/resume_input.py:144-159,257-291`
- `src/mote_kernel/state/graph_state/validation.py:100-129`

`require_feedback_activation_cause()` 只比较 `A[n-1]` 坐标和 feedback route；`materialize_node_input()` 随后只要找到同坐标
publication 就放行。恢复态 validator 没有查询前驱 settlement、route 选择或 commit-linked provenance。

最小复现：构造一个 `superstep=1` 的 Pending `A`，cause 写成 `ActivationReference(A[0], feedback)`，但从未执行或提交过
`A[0]`；向 `ScopedFrameIndex` 放入一帧坐标匹配的伪造 publication。当前 `pending_node_input_available()` 返回 `True`，即允许
进入 claim/materialization。这样“紧邻上一轮”只剩坐标检查，不是真实因果证明。

必须收口：自反馈和 Join 共用同一 authoritative settlement/publication evidence admission；缺少前驱成功提交、route 选择或
commit 联结时在 claim 前 fail closed，绝不能因为坐标和 frame 存在就执行下一轮。

同一缺口也影响普通 routed activation：`snapshot_guard` 只检查节点是否存在和 contribution 形状，不检查 frontier cause 是否
由该 compiled edge 生成。对 `a -> b` 图，把 `b` 的 cause 改成不存在的 `foreign + bogus route`，只要 graph input frame 存在，
当前 `require_scoped_snapshot_matches_graph()` 和 `pending_node_input_available()` 仍通过。修复必须是统一的 cause/topology
admission，不能只给 feedback 加特判。

### P0-2：同一 Join source 的不同 activation occurrence 被静默拼接

**状态：已确认，阻断验收。**

代码位置：

- `src/mote_kernel/execution/engine/routing.py:275-320`
- `src/mote_kernel/state/graph_state/validation.py:60-71`

当前以 `reference.activation.node_id` 建立 `arrived_sources`，只按 node id 去重，没有 occurrence identity。

已确认的普通无环 DAG：

```text
entries: a, b
a -> x
b -> d -> x
b -> c -> y
join (x, y) -> z
```

执行到第三轮后，`z` 会被生成，cause 同时包含 `x[1]`、`x[2]`、`y[2]`。这是同一 Join source `x` 的两个 activation
occurrence，不是一次合法 Join；该图无环，不能用“P3 只处理 cyclic Join”解释掉。

必须收口：P3 occurrence identity 落地前，compiler 拒绝可能让一个 Join source 产生多个 occurrence 的拓扑；或者引入可验证
occurrence identity。至少在合并历史和当前 arrival 时发现重复 source 就 fail closed，禁止按 node id 静默合并。

### P0/P1-3：拓扑 activation admission 不完整

**状态：已确认，阻断验收。**

代码位置：

- `src/mote_kernel/state/graph_state/execution_transitions.py:96-136,195-213`
- `src/mote_kernel/execution/engine/snapshot_guard.py:22-59`
- `src/mote_kernel/execution/graph_run.py:20-37`
- `src/mote_kernel/execution/family_driver.py:132-158`

reducer/commit 边界只检查坐标、当前 source settlement 和部分 Join progress；没有对照 compiled topology 验证：

- target 是否属于 compiled graph；
- source route 是否真的允许该 target；
- target 是否有且只有一个合法 activation gate；
- 多个 reference 是否对应同一个合法 Join；
- 是否完整覆盖合法 successor，是否漏 successor；
- 是否出现非法 START target。

确定性反例：图只有 `a -> c` 时，提交 `a -> b` 的 activation，reducer 接受；图 `a -> {b,c}` 时只提交 `b`，reducer 也接受；
图 `a -> c` 与 `b -> c` 时 compiler 允许编译，但两个 source 同轮成功会在 runtime 才报多 cause；同一 source 的 direct edge 和
conditional edge 同时指向一个 target 也能编译，运行时才报多 cause。这说明“可能同时到达且没有显式 Join 必须 compile fail”的
计划要求尚未落地。

必须收口：在 compiler/admission 边界形成唯一可验证的 activation gate；非法 target、零 gate、多 gate、漏 successor、非法 START
和 route 不匹配全部 fail closed。不能让 reducer 只凭坐标接受任意 command，也不能等 runtime 才发现 compiler 本可证明的问题。

### P1-4：`join_progress` 可以无故丢弃或替换

**状态：已确认，阻断验收。**

代码位置：

- `src/mote_kernel/state/graph_state/execution_transitions.py:195-213`

`advance_graph_frontier()` 直接把 `command.join_progress` 写入新 State，没有要求旧 partial progress 必须保留、只能在合法完成并
消费时移除，也没有要求新增 arrival 来自当前 settled success。

确定性反例：合法 State 已有一个 Join partial arrival；下一轮提交一个看似合法的 activation，但 `join_progress=()`；reducer 接受，
旧 partial 被静默丢弃，后续 source 到达可能永久等待或产生不完整 Join。

必须收口：旧 partial 必须保留，除非同一 command 合法完成并消费它；无关/伪造 progress 不能写入；Join 完成后不得继续携带已消费
progress。该规则要与 provenance、occurrence identity 一起在 live 和 recovery 两条路径验证。

### 3.5 复现结果留档

本轮用当前工作树执行了最小脚本，输出如下：

```text
历史伪造 arrival：AdvanceGraphFrontier 被 reducer 接受，下一 frontier = c
伪造 self-feedback 恢复态：pending_node_input_available(...) = True（前驱从未 settlement）
伪造普通 routed cause：snapshot/materialization 通过（source=foreign，route=bogus）
重复 occurrence DAG：z 的 cause = (x[1], x[2], y[2])
非法 target：illegal-target accepted: ('b',)
漏 successor：missing-successor reducer accepted: ('b',)
progress 丢失：before = (a[0],)；after = ()
```

这些结果是当前代码的复现，不是旧版本描述。

## 4. 本轮已通过项（只列一次）

- `StartGraphRun` / `AdvanceGraphFrontier` 已携带完整 `GraphFrontierActivation`，command 不再保留平行 `node_ids` 真相。
- `GraphFrontierNode.cause` 已必填；superstep 0 只能 START，后续只能 routed cause。
- direct/conditional/Join routing 在 target collapse 前保留 candidate；多 candidate 会 fail closed。
- direct self-feedback compiler 已收窄为单 callable、GraphInputRef seed、target 自身 repeat output、一条 feedback route、
  一条 terminal route、一个 graph output；普通 `NodeOutputRef` 自环仍拒绝。
- cyclic Join 当前 compile fail；P1 没有假装已经支持 occurrence identity。
- 普通 callable、resource waiter、nested child sibling 已按 Pending 语义推进；typed failure 不会阻止其他 Pending sibling。
- `Pending > Failed > Interrupted > Settled` 已实现；Failed 不会恢复成 Pending，Graph 不提供 failed-node resume/skip/retry。
- failed child / awaiting child 的 recovery 顺序已改为普通 Pending 先排空，再做 terminal cleanup。
- 普通 callable 异常仍走 `TaskRaised`，不等价于 Graph business failure。
- public `Graph.add_node()` 不接受 `FeedbackInputBinding`，也没有为 P1 偷开 feedback facade。
- `src/mote_kernel/py.typed` 存在，strict typing 包契约未被删除。

这些通过项不能抵消第 3 节的 State/topology 真实性漏洞。

## 5. 架构整洁度与 0 负债要求

### 5.1 State identity 到 Execution projection 仍有分散转换

`GraphActivationIdentity` 是 State 唯一 canonical identity；`StableActivation` 可以保留为 Execution-owned lookup projection，
但各模块仍多处手工拼接 `GraphActivationIdentity(state.run_id, state.superstep, node_id)` 再投影。应集中一个窄转换函数并统一
校验 scope/run 一致；`ChildStateBinding.parent_activation` 不能重新成为另一份 State identity。不要因此增加 manager/registry/adapter
层。

### 5.2 Recovery proof 复杂度要按等价关系清理

`_RecoveryCycleSignature`、worklist、fixed-point 和 availability 维度有真实用途，不能为了降指标机械删除。每个字段必须能回答：
“删掉会不会把两个后继不同的 recovery 状态错误合并？”回答不了的字段才净删。不要拆 helper、加 context/dataclass 或提高
ratchet 来伪装简化。

## 6. 复杂度和门禁事实

高召回复杂度指标只做定位，不单独决定退回。当前实际值为：

```text
top-level definitions       675
type definitions            404
dataclass types             234
dataclass fields            649
decision points             1879
cognitive complexity        2502
complexity hotspots         65
cross-module call edges     785
cross-module call pairs     234
max runtime module fan-out  24
```

本目录 `make check` 已通过：ruff、format、pyright、复杂度 gate/health、pytest（1272 passed，覆盖率 100%）、sdist/wheel 构建和
twine check 均通过。门禁绿只说明当前测试和静态规则通过，不能证明第 3 节的伪造 State 会被拒绝。

monorepo 根目录 `pre-commit run --all-files` 本轮未运行：工作树含大范围用户改动，且 ruff hook 带 `--fix`，全量运行可能改写无关
文件；交付前应在隔离/确认安全后补跑并报告结果。

## 7. P2 边界

以下仍是 P2，不应冒充 P1 已完成：

- durable/state-led evidence reader；
- graph input 与 `StartGraphRun` 原子证据；
- successful settlement/publication 原子持久化；
- acknowledgement lost read/reconcile；
- retention/release、codec/bytes hard limit、跨语言 conformance、外部 persistence adapter。

P1 在这些 capability 完成前不得开放 public durable feedback API；但 P1 进程内 State cause、routing 和 admission 仍必须先修复第 3 节
的真实性漏洞。

## 8. 整改顺序

1. 先给 historical Join arrival 补 authoritative settlement/selected-route evidence；做不到就关闭跨 superstep Join。
2. 在 P3 occurrence identity 未落地前，compile fail 可能产生重复 Join source occurrence 的普通 DAG；runtime 发现重复也必须
   fail closed。
3. 让 compiler 和 admission 共享唯一 gate 事实，拒绝非法 target、零/多 gate、漏 successor 和非法 START。
4. 让 `join_progress` 只能按“保留旧 partial / 合法完成后消费”的差分更新，live/recovery 共用同一规则。
5. 集中 identity projection，按反例清理 recovery signature；保留有证明价值的复杂度，删除重复 owner/路径。
6. 为四类漏洞补 State 不变、零 callable/Port 调用、live/recovery 一致的确定性测试；不恢复 failed retry/skip legacy。
7. 重新跑本目录 `make check` 和根目录 pre-commit，并把计划状态从“P1 已完成”改为与代码一致。

## 9. P1 通过条件

- 每个 activation/cause 都由 compiled topology 唯一生成并在 admission/reducer 边界验证；
- historical Join 只接受 authoritative、commit-linked 的成功 settlement/selected-route evidence；
- 同一 Join source occurrence 不可重复拼接；
- old partial `join_progress` 不能无故丢失或替换，合法完成后才消费；
- live 与 recovery 对上述规则完全一致；
- 普通、nested、resource Pending 语义和不同完成顺序有确定性测试；
- Graph 不开放 failed-node resume/skip/retry，Failover 保持唯一 retry owner；
- strict typing、typed package、复杂度 health、全量测试和全仓门禁通过；
- 不靠提高 ratchet、删有意义测试、关闭 strict typing 或增加生产 legacy 过门禁；
- 实施计划状态与实际代码一致；旧评审文档保持不变。
