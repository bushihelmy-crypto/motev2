# Graph 延迟回环 P1 代码重新复审

状态：**不通过，当前 P1 不能交付**

评审日期：2026-09-02

评审对象：当前工作树中的 Graph P1 实现、State 转换、routing、recovery、nested family driver、测试与实施计划

本文是一份独立复审，不依赖其他评审文档才能理解结论。本次只检查已经敲定的架构是否正确落地，不重新讨论产品
取舍。**没有需要 owner 再裁决的问题。**

## 1. 结论先说人话

这版已经把 direct self-feedback 的主干跑通了：第一次读 seed，后续读紧邻上一轮 publication，正常出口、失败终止和
执行上限也都有测试。Graph 的失败节点不会重试，`Pending > Failed > Interrupted > Settled` 的 State 优先级也已经
实现正确。

但当前仍有三处会改变真实执行语义的硬问题：

1. 非首轮普通节点仍会被伪装成 `START` 激活，State 中保存的 cause 不是完整真相；
2. 一个 nested child 只要还在运行或等待恢复，就会挡住同一 frontier 的所有普通 Pending sibling；
3. 一旦出现 failed child，awaiting child 会在普通 Pending sibling 执行前被提前 abort。

所以现在不能说“代码逻辑和需求已经对上并闭合”。此外，公开 API、compiled-plan 校验、recovery proof 表示、类型包契约
和全仓门禁仍有明确的零负债问题。

| 检查项 | 当前结论 |
| --- | --- |
| direct self-feedback 的 seed/repeat happy path | 通过，必须保留 |
| Graph failure 不重试，Failover 是唯一 retry owner | 通过，必须保留 |
| `Pending > Failed > Interrupted > Settled` | 通过，不能改 |
| 每个非首轮 activation 都保存真实 routed cause | **不通过，P0** |
| failed sibling 不阻塞普通 Pending/resource waiter | 普通 callable/resource 基本通过；mixed nested **不通过，P0** |
| awaiting child 只在 terminal Failed 阶段清理 | **不通过，P0** |
| P1 不开放 public feedback API | **不通过，P1** |
| 单一 compiled-plan admission、无重复深校验 | **不通过，P1** |
| recovery proof 清楚表达必要的不动点等价关系 | **不通过，P1** |
| strict typing、PEP 561 包契约、全仓门禁 | **不通过，P1** |

## 2. 本次复审采用的最终语义

同一 frontier 只按下面的顺序派生状态：

```text
仍有 Pending                       -> 继续推进这些 Pending
没有 Pending，至少一个 Failed      -> terminal Failed
没有 Pending、没有 Failed、有 Interrupt
                                  -> AwaitingResume
全部 Succeeded                     -> Settled，再做 routing
```

这里的“继续推进 Pending”包括：

- 已经启动的普通 callable；
- 尚未启动但已在当前 frontier 的 callable；
- 正在等待资源的 callable；
- 尚未启动但已在当前 frontier 的 nested child；
- 已经启动的 nested child。

一个 sibling 的业务 failure 不能让这些节点消失、不能阻止它们首次执行，也不能把它们改成 failure。等真正可执行的
Pending 全部排空后，Failed 才赢过 Interrupted，run 进入 terminal Failed。此后允许 child owner 做 awaiting child 的
终局释放；这个 cleanup 不是 Graph retry，也不是外部 cancel。

普通 callable 抛异常仍走现有 `TaskRaised`、session drain/fence 和异常传播，不能被改写成 `Graph.failure()`。Graph
failure 也绝不能重新变成 Pending；需要再次调用 Port 时只能由 Failover 创建新的显式 activation。

## 3. P0：必须先修的逻辑问题

### P0-1：非首轮 activation 仍能伪装成 START

位置：

- `src/mote_kernel/state/graph_state/command.py:55-59,78-85`
- `src/mote_kernel/state/graph_state/execution_transitions.py:75-137`
- `src/mote_kernel/state/graph_state/frontier_model.py:135-145`
- `src/mote_kernel/state/graph_state/model.py:51-55`
- `src/mote_kernel/state/graph_state/validation.py:79-108`
- `src/mote_kernel/execution/engine/routing.py:392-452,508-523`
- `tests/execution/engine/test_routing.py:214-225,480-484,568-577`

当前发生了什么：

- `StartGraphRun` 和 `AdvanceGraphFrontier` 同时保存 `node_ids` 与 `activations`，同一批节点有两份事实；
- 两个 command 的 `activations` 都默认是空 tuple；
- `AdvanceGraphFrontier.activations` 为空时，reducer 会自动给所有下一轮节点补 `StartActivationCause()`；
- `GraphFrontierNode.cause` 本身也默认是 START；
- recovered State validator 允许任意 `superstep` 使用 START；
- routing 只为 feedback target 收集 cause。普通 direct、conditional 和 completed Join target 先进入
  `set[GraphNodeId]`，真实来源在形成 activation 前就丢了；
- 跨 superstep 的 `GraphJoinProgress` 只保存 arrived node id，不保存实际到达的 activation/route reference；即使
  routing 想补完整 Join cause，也没有可信事实可用；
- 现有普通 routing 测试继续把“空 activations”和“多个来源按 target 去重”当成正确行为。

为什么错：

`START` 只能说明“这个节点由图入口第一次激活”。把普通后继、普通控制回环或 Join 后继写成 START，会让持久 State
失去真实因果。恢复时只能猜，不再能证明这一轮到底由哪一个已成功 source、哪一条 route 或哪一个 Join 激活。

这也直接违反实施计划已经写明的规则：routing 必须在 collapse target 前保留每一个 `(target, cause)` candidate，
零条或多条都 fail closed，不能先用 target set 抹掉来源。

必须怎么改：

1. `StartGraphRun`、`AdvanceGraphFrontier` 只携带完整的 `GraphFrontierActivation`，删除平行的 `node_ids` 真相；需要
   node id 的地方从 activation 派生。
2. 删除两个 command 的空 activation 默认值和所有自动补 START 的 legacy 路径。
3. `GraphFrontierNode.cause` 改为必填，禁止默认 START。
4. State 校验明确要求：`superstep == 0` 只能是 START；`superstep > 0` 只能是 routed cause。
5. routing 对普通 direct、conditional、Join 和 feedback 使用同一个 candidate 流程。先完整保留来源，再验证每个
   target 恰好有一个合法 activation gate，最后才生成 canonical activation tuple。
6. `GraphJoinProgress` 保存已经提交的真实 arrival references，而不是只保存 node id。Join 完成时直接复用这些事实形成
   一个带多条 reference 的单一 cause；没有 Join 的多个独立到达不是“去重”，必须 fail closed。
7. P3 的 cyclic Join occurrence identity 完成前，compiler 必须拒绝 Join 处于 control cycle 的拓扑。这样 P1 不需要
   伪造完整 P3，也不会继续允许不同循环 occurrence 的同名 arrival 被拼成一次不存在的 Join。
8. 不保留兼容 alias、空 tuple 回填或“普通图继续用 START”的分支，内部 command 没有兼容义务。

验收测试：

- 普通 direct、conditional、Join 的下一 frontier 都持有真实 `RoutedActivationCause`；
- 任意非零 superstep 的 START State 在 claim 前被拒绝；
- command 的 activation 为空、漏 target、重复 target、顺序错误均原子拒绝；
- 同一 target 同轮出现 direct + Join、两个 direct 或其他多个 candidate 时，在去重前拒绝；
- 一个合法 acyclic Join 即使跨多个 superstep 收集 arrival，也只产生一次 activation，并保留每个真实 source reference；
- cyclic Join 在 P3 occurrence identity 落地前 compile fail；
- direct control self-loop 每一轮都引用紧邻前一轮，不能再靠 START 回填运行。

### P0-2：nested child 被实现成整个 frontier 的全局栅栏

位置：

- `src/mote_kernel/execution/engine/frontier.py:37-79`
- `src/mote_kernel/execution/engine/superstep.py:49-77`
- `src/mote_kernel/execution/family_driver.py:689-724`
- `tests/execution/test_resource_protocol.py:432-500`

当前发生了什么：

`prepare_frontier()` 只要看到一个 `MissingChild` 或 `ActiveChild`，就立即返回 `WaitingForChildren`。普通 callable 的
input 甚至不会 materialize，也不会进入 claim/session。family driver 随后只启动或驱动 child，完全不调度同 frontier
的普通 callable 和资源节点。

现有 `test_active_child_blocks_resource_admission` 还明确断言：child 阻塞时普通 resource node 调用次数必须为 0，parent
甚至不能出现一次 claim。这个测试锁定的是已经被 owner 否决的旧语义。

为什么错：

同一 frontier 的 sibling 已经被声明为并行、无控制依赖。nested 只是节点实现形态不同，不因此拥有暂停整个 frontier
的权力。一个 child 很慢、正在运行或进入 interrupt，都不能让普通 sibling 永远得不到第一次执行机会。否则“failure
不影响 sibling”和 `Pending` 优先都只对普通 callable 生效，family graph 上不成立。

必须怎么改：

1. child readiness 必须按节点表达，不能再把 `WaitingForChildren` 当成整张 frontier 的全局 disposition。
2. 复用现有 planner、resource admission、session 和 child owner；不要新建 nested runner 或第二套 scheduler。
3. missing child 可以先创建，active child 可以继续推进；同时，已具备输入的普通 callable/resource node 必须仍可
   claim 和执行。
4. child 尚未 terminal 时，它自己的 parent node 保持未完成；这不能阻止其他 sibling settlement。
5. 即使 parent 的 `max_parallel_tasks=1`，awaiting/child-owner 状态也不能偷占一个永不释放的普通 task slot。真实正在
   执行的 task 仍按现有 limit 管理。

验收测试：

- blocking/awaiting child 与普通 callable 同 frontier 时，普通 callable 确实被调用；
- blocking/awaiting child 与 resource waiter 同 frontier 时，resource node 最终被 claim；
- 上述两组都覆盖 `max_parallel_tasks=1`；
- child 与普通 sibling 的完成顺序互换，最终 State 和诊断一致；
- 外部取消仍能关闭 child、ordinary session 和资源，不能因拆掉全局栅栏泄漏 task/handle。

### P0-3：awaiting child 在普通 Pending 排空前被提前清理

位置：

- `src/mote_kernel/execution/family_driver.py:599-612,706-712`
- `src/mote_kernel/execution/engine/recovery.py:828-858,890-960`
- `tests/execution/test_family_driver_local_ownership.py:602-616`
- `tests/execution/engine/test_recovery_identity.py:1033-1132`

当前发生了什么：

family driver 一进入 `WaitingForChildren`，就先调用 `_abort_awaiting_children_after_failure()`。只要任何 child 已经是
`FailedChild`，所有 `AwaitingResume` child 立即被改成 `AbortedChild`。这个动作发生在 missing child 启动、active child
推进和普通 callable claim 之前。

recovery preflight 也使用相同的提前支配模型：只要组合中有 failed/aborted child，就把 awaiting child 用
`"recovery-preflight-failure"` 模拟成 parent failure，然后才选择普通 live task。这里是无副作用 proof，不是实际
durable commit；问题是它证明的执行顺序仍与最终规则不一致。

为什么错：

最终规则不是“看见一个 failure 就开始清场”，而是“先排空 Pending，再由 Failed 终止”。提前 abort awaiting child
会先改变 family evidence，再决定普通 sibling 是否执行；当前又有 P0-2 的全局栅栏，所以它确实会影响普通 Pending
的执行机会和可观察提交顺序。

必须怎么改：

1. 先让 active child、普通 Pending 和资源 waiter 完成它们应有的推进；failure 期间不得 routing，也不得生成下一
   frontier。
2. 当普通可执行 Pending 已排空，State/family 能确认最终 disposition 是 Failed 后，才对仍 awaiting 的 child 做
   terminal-failure cleanup。
3. cleanup 使用一个真实、typed、统一的 terminal-failure reason；live 与 recovery proof 对 child disposition、顺序和
   scope 的理解必须一致。
4. `Pending > Failed > Interrupted > Settled` 不变：这不是要求 Failed run 永远等待人工 resume。是要求它先不伤害
   其他 Pending；Pending 排空后 Failed 仍优先于 awaiting interrupt。
5. 外部 Cancel/Abort 的 owner 和 reason 保持原样，不能与 sibling business failure 混成同一条路径。

验收测试：

- 同一 parent frontier 同时有 failed child、awaiting child、普通 Pending 和 resource waiter：ordinary/resource 都先
  settlement，随后才出现 awaiting child 的 terminal cleanup，最终 parent 为 Failed；
- 提交日志能证明 cleanup 没有早于 ordinary/resource settlement；
- 最终 `FailedResult` 保留真实 failure 和 interrupt/cleanup 诊断，但不提供 failed-node resume；
- state-led preflight 与 live driver 接受同一组状态，得到同一最终 disposition，且 proof 中的模拟事实绝不进入 commit。

## 4. P1：交付前必须清理的架构和工程问题

### P1-1：内部 feedback 类型已经漏进公开 `Graph.add_node()`

位置：

- `src/mote_kernel/execution/facade.py:278-318`
- `src/mote_kernel/execution/graph/ports.py:56-76`
- `tests/execution/test_feedback_runtime.py:1-43`
- `docs/graph-delayed-loop-implementation-plan.zh-CN.md:3,647-648,865-870`

计划明确说 P2 durable capability 完成前不开放公开 feedback API，但公开 `Graph.add_node()` 的 overload 和实现签名已经
直接接受 `FeedbackInputBinding`。用户只要从 internal module import 这个类，就能通过唯一 public facade 运行 feedback；
这不是“API 未开放”，而是一个没有名字、没有稳定性说明的半公开 API。

必须怎么改：

- public overload、public typing surface 和文档中不能出现 `FeedbackInputBinding`；
- P1 内部测试通过现有内部 definition/compiler/execution 边界验证能力，不要为了测试新增 public wrapper；
- 不新增临时 `Graph._feedback_for_test()`、compat alias 或第二 facade；
- P2 的 reader、原子 evidence commit、limits/retention/codec 门禁闭合后，再一次性设计并开放正式 typed API。

验收：public typing fixture 和 API 枚举证明只能从 `mote_kernel.execution.Graph` 使用已承诺能力，feedback declaration
不会出现在公开签名、导出和生成的类型信息里。

### P1-2：compiled plan 在没有真实外部加载边界时被反复深校验

位置：

- `src/mote_kernel/execution/graph/topology.py:130-442`
- `src/mote_kernel/execution/engine/routing.py:158-240,281-312`
- `tests/execution/graph/test_feedback_plan_validation.py:1-171`

当前 `CompiledGraph` 由唯一 compiler 在进程内构造，P1 没有 compiled-plan codec 或外部反序列化入口。但代码新增了
三百多行字段级防御：topology 深查 declaration、descriptor、binding、graph output，routing 再查 control maps；
`_feedback_targets()` 在每次 routing 时又多次调用整套检查。对应测试靠 `cast(object())`、把 tuple 换成 list、用
`dataclasses.replace()` 伪造内部对象来驱动这些分支。

重复校验背后还有一份重复事实：`CompiledActivationRule.initial/repeat/repeat_selection`、
`FeedbackInputResolution.initial/repeat/repeat_selection`，以及 `ResolvedInputBinding.source/publication` 分别保存同一组
value-selection 信息，然后靠运行时逐字段比较来证明它们没有漂移。这正是应该在 compiler 内消除的双真相。

这不是必要的安全边界。它把“未来可能有 plan loader”提前包装成当前 runtime 的重复工作，也让 compiler 不再是唯一
plan admission owner。

必须怎么改：

1. compiler 成功返回的 immutable typed plan 只 admission 一次；runtime 直接消费已 admitted plan。
2. 让一个 canonical compiled feedback rule/binding 拥有 initial、repeat、selection 和 route gate；materialization 与
   routing 只引用或派生该对象，不再各复制一份再互相比对。
3. 保留真正有意义的 compiler rejection：非法反馈形状、类型不匹配、无 terminal route、多 feedback、nested、Join、
   非紧邻 selection 等。
4. 删除每次 routing/materialization 都执行的全图深校验和只靠不可能的 object cast 存在的生产分支。
5. P2 如果真的增加外部 plan schema/loader，就在那个真实 trust boundary 做一次 schema/version/admission，然后仍返回同一
   `CompiledGraph`；不要把校验散到每个消费者。
6. 不用缓存第二份“validated plan”，也不引入 manager/visitor/context 包装。

### P1-3：recovery 的有界不动点是必要能力，但当前等价模型还没有讲清楚

位置：

- `src/mote_kernel/execution/engine/recovery.py:81-365,568-636,1031-1153`
- `tests/execution/engine/test_recovery_identity.py:699-775`

先区分必要与可清理部分：

- **必要**：recovery preflight 是无副作用 proof，不是第二个 runner；用户可设置很大的 `max_supersteps`，因此不能总靠
  展开到上限才停。有界 worklist 和控制回环的不动点检测有真实价值，不能只为降低指标删除。
- **尚未闭合**：P1 新增的 `_RecoveryCycleKey` 是一个 11 段位置 tuple；`RecoveryFrontierNode` 不带 normalized
  activation cause，`_ScopeBoundary.state` 又被排除在 equality/hash 之外。与此同时 failed、aborted，以及被 failure
  支配的 awaiting child 都通过字符串 `"recovery-preflight-failure"` 注入模拟 State。

问题不在“tuple 有 11 个字段”这个数字本身，而在这些字段共同定义了哪些状态可以安全合并。现在只有一个 happy-path
cycle test，无法证明：少看 cause 不会错并分支、多看无关字段不会永不收敛、不同 availability window 不会错误合并。
模拟字符串也把 proof abstraction 和业务 failure 事实写成同一种形状，后续很容易被误用。

必须怎么改：

1. 保留一个有界 recovery proof owner，不新增 runner/reducer。
2. 明确定义“两个 recovery 状态可以视为同一循环位置”的最小等价关系；activation cause 要么以相对/normalized 形式
   进入 proof，要么用测试证明它在当前 admitted topology 下可唯一派生，不能靠注释默认忽略。
3. 每个 signature 字段都要回答“删掉会把哪两个后继不同的状态错误合并”；回答不了的字段删除。
4. 使用已有 State/evidence 类型表达事实。若需要一个 named signature 来消除 11 个位置参数，可以有一个窄 immutable
   value；不要再加 manager、phase context 或第二份 mutable state。
5. proof 中的 failed/aborted/awaiting abstraction 使用明确的 typed proof disposition，或把 synthetic settlement 严格
   封在单一 projection 内；不能让一个普通字符串看起来像真实 child failure reason。
6. 补差分测试：只改变 cause、absolute publication、relative predecessor、resume input、child boundary、Join progress、
   resource state 时，分别证明该合并或不合并。

### P1-4：State identity 到 Execution lookup coordinate 的转换仍散落

位置：

- `src/mote_kernel/execution/identity.py:19-27`
- `src/mote_kernel/state/graph_state/identity.py:15-43`
- `src/mote_kernel/state/graph_state/model.py:44-48`
- `src/mote_kernel/execution/engine/recovery.py:639-648`
- `src/mote_kernel/execution/engine/resume_admission.py:74`
- `src/mote_kernel/execution/engine/resume_input.py:255,284`
- `src/mote_kernel/execution/engine/routing.py:113-122`
- `src/mote_kernel/execution/family_driver.py:657-660,861`
- `src/mote_kernel/execution/invocation.py:277,472`

`GraphActivationIdentity` 是 State-owned canonical identity。当前 State 中原有的 `ParentGraphActivation` 与它拥有完全
相同的 `run_id/superstep/node_id`，表达的也是一个 parent node activation；保留两个 nominal record 会让“canonical
activation identity 只有一份”落空。带 scope 的 `StableActivation` 则可以继续作为 Execution lookup projection，它
增加了 definition scope，不必被误判成第二个 canonical identity。

另一个问题是各模块都手工把 scope、superstep、node id 拼成 `StableActivation`，未来很容易漏掉 run/scope 对应校验。

必须让 child parent、activation reference 和 child-run-id projection 直接复用 `GraphActivationIdentity`；若确实需要标记
“这是 parent”这个角色，只保存一个 `activation: GraphActivationIdentity`，不能再复制三个标量字段。随后新增或复用一个
窄的集中转换函数，由它校验 `GraphActivationIdentity.run_id == ScopeRunCoordinate.graph_run_id` 后生成 lookup coordinate。
各模块禁止继续手工重建。不要为了这个转换增加 adapter class 或 identity registry。

### P1-5：实施计划和测试仍保留已否决的旧语义

位置：

- `docs/graph-delayed-loop-implementation-plan.zh-CN.md:3,532-535,632-648,815,852-870`
- `tests/execution/engine/test_routing.py:98-101,214-225,568-577,652-718`
- `tests/execution/test_resource_protocol.py:432-500`

必须同步修正：

- 计划第 7.1 节和验收表仍写“只完成已 claim 的 Pending”。最终规则是当前 frontier 中所有 Pending sibling 都继续，
  包括尚未启动和资源 waiter；
- 计划宣称 P1 已完成，但本评审的 cause、nested 调度和公开边界尚未闭合，状态必须改回“实施中/未通过复审”；
- routing 测试不能再把空 activation 和多 cause target 静默去重当成成功；
- routing 测试当前仍把可重复 firing 的 cyclic Join 当成已支持能力；P3 occurrence identity 落地前，这些测试应迁移为
  compiler fail-closed 测试，不能用 node-id progress 继续拼接跨轮 arrival；
- resource/nested 测试不能再把 active child 阻止 ordinary claim 当成正确；
- failed resume/skip 的旧测试和 example 已删除是正确方向，不能为了测试数量恢复；
- 旧测试里仍有意义的原子拒绝、错误顺序、scope 隔离、cancellation 和 evidence integrity 必须迁移到 interrupt/terminal
  Failed/新 cause 模型；只属于 failed retry/skip 产品语义的测试应删除。

### P1-6：typed package 契约和 strict typecheck 已经破坏

位置：

- `src/mote_kernel/py.typed`：当前被删除
- `pyproject.toml:22,43`
- `tests/execution/engine/test_routing.py:295-326`
- `tests/failover/test_plan.py`
- `tests/failover/test_policy.py`

项目仍声明 `Typing :: Typed`，构建配置仍把 `src/mote_kernel/py.typed` 列为 artifact，但 marker 被删除。当前 wheel 能构建，
却不包含 `mote_kernel/py.typed`，外部类型检查器因此不能把这个包当成 PEP 561 typed package。

同时 `pyright` 当前是 **189 errors**：其中 20 个直接位于 Graph routing 测试，其余位于同一交付工作树的 Failover 测试。
很多是负向 runtime 测试故意构造非法类型，但 strict typecheck 仍要求这些位置用精确 `cast`/typed factory 隔离，不能靠
unknown lambda、裸 `object` 或忽略规则通过。

必须恢复 `py.typed`，保留 typed classifier，并把 pyright 清零。不要删 strict 配置、加全局 ignore，或因为负向测试
需要造坏数据就污染生产类型。

## 5. 复杂度退回：哪些必要，哪些应清除

高召回复杂度指标只用于定位，不代替人工架构判断。当前不能简单要求“所有数字回到原值”，也不能用“功能新增必然变
复杂”接受全部增长。

本评审不因“34 项指标回退”本身判定 P1 不通过。即使直接提高 ratchet 让门禁变绿，前文已经逐条定位的错误执行语义、
重复真相和职责泄漏仍然存在；反过来，只要某项增长是在直接表达不可省略的领域事实，也不要求为追平基线而删除。最终
只看代码能否用最少 owner、最少路径和清楚的类型把正确语义闭合，并由确定性测试证明。

### 5.1 必要成本，应保留

- State-owned `GraphActivationIdentity`、`ActivationReference`、START/Routed cause；
- acyclic Join progress 中真实、已提交的 arrival references；
- 一个显式 feedback binding 和一个 immutable compiled activation rule；
- `RELATIVE(1)` 与 immediate predecessor 的 typed proof；
- terminal Failed lifecycle/result，以及 interrupt-only resume；
- recovery 的有界 worklist和循环不动点能力；
- 对真实 trust boundary、State transition、publication/evidence 的 fail-closed 校验。

这些类型和分支直接表达领域事实。不能为了让 definition 数下降，把它们改回字符串 discriminator、bare dict 或隐式
superstep 猜测。

### 5.2 明确可清除成本

- `node_ids + activations` 双份 command 真相和空值回填；
- `GraphFrontierNode` 的默认 START legacy；
- 普通 routing 的 target set 与 feedback-only candidate 两条路径；
- compiled plan 在 topology、routing、materialization 中反复深校验；
- 只为 forged internal object 测试存在的生产分支；
- child 全局栅栏和随后补救式的提前 abort；
- 各模块重复手工构造 `StableActivation`；
- plan/test 中已经被 owner 否决的“只排空已 claim”行为。

这些内容不承载必要语义，删除后反而更接近唯一真相和直白架构。

### 5.3 需要用证明决定去留，不能机械处理

- recovery cycle signature 的每一个 availability/control 维度；
- compiler 中已有的 reachability、joint activation、cycle exit 固定点；
- resource FIFO、atomic reducer、session cancellation/fence 的状态分支。

处理原则是：能展示两个后继不同的反例，就保留并写确定性测试；不能展示，说明是多余维度，应净删除。不要为了门禁
把一个连续算法拆成更多 helper，也不要用新 context/dataclass 只搬运原来的字段。

当前结构门禁报出 34 项回退，代表性数据如下；它们是上面人工结论的旁证，不是单独的判决理由：

| 指标 | 配置值 | 当前值 |
| --- | ---: | ---: |
| top-level definitions | 520 | 669 |
| dataclass fields | 504 | 639 |
| decision points | 1345 | 2059 |
| semantic nodes | 27948 | 37745 |
| cognitive complexity | 1914 | 2630 |
| statement clones | 7 | 23 |
| cross-module call edges | 715 | 816 |
| complexity hotspots | 47 | 74 |

健康类指标当前全部为 0：unused private definitions、unread fields、unconsumed async calls、unowned coroutine/task handles
没有报错。这是好事，但不能抵消上面的重复 owner、重复路径和逻辑问题。

## 6. 当前已经正确的行为，修复时必须保护

以下内容不是 bug，不要在整改时回退：

1. `frontier_status()` 的顺序就是 `Pending > Failed > Interrupted > Settled`。
2. 普通 `Failed + Pending` settlement 会保留 execution claim，让同 session 的普通 sibling 继续。
3. resource owner failure 后会释放锁并让 waiter 继续。
4. Pending 排空后 `Failed + Interrupted` 返回 `FailedResult`；interrupt 作为诊断保留，但不能 resume terminal Failed。
5. failure frontier 不 routing、不创建下一 frontier。
6. 已确认的 Failed 节点不会回 Pending，也没有 resume/skip command 或 public API。
7. callable exception 仍走 `TaskRaised`/fence，不转成 Graph business failure。
8. feedback happy path按 State cause 选择 seed/repeat，repeat 精确读取紧邻上一轮 publication，不扫描“最新值”。
9. normal terminal route 才成功完成，`max_supersteps` 只是安全熔断。

## 7. 最小验收矩阵

| 场景 | 必须结果 |
| --- | --- |
| 首轮 feedback activation | 唯一 START cause，只读 graph seed |
| 后续 feedback activation | 唯一 Routed cause，只读 exact previous publication |
| 普通 direct/conditional/Join | 每个下一 activation 都有真实 routed cause |
| 同 target 同轮零 cause | fail closed，零 claim |
| 同 target 同轮多 cause、无显式 Join | fail closed，不能 target 去重 |
| `Failed + ordinary Pending` | ordinary 首次执行并 settlement，之后 terminal Failed |
| `Failed + resource waiter` | waiter 获得资源并 settlement，之后 terminal Failed |
| failed child + active child + ordinary Pending | active/ordinary 均推进，之后 terminal Failed |
| failed child + awaiting child + ordinary Pending | ordinary 先 settlement，随后 child cleanup，最终 Failed |
| `Failed + Interrupted` 且无 Pending | FailedResult，诊断完整，resume 拒绝 |
| terminal Failed recovery | 同一 FailedResult，零 claim、零 callable、零 Port |
| ordinary callable 抛异常 | TaskRaised/fence 语义不变 |
| feedback 缺 predecessor evidence | claim 前失败，不回退 seed |
| direct self-feedback 选择 terminal route | CompletedResult，输出来自最后一次 activation |
| feedback 永远选择 repeat | 达到真实 max_supersteps 后 ExecutionLimitError |
| 外部 cancel 混合 child/session/resource | 全部 owner 收尾，无泄漏、原异常优先级不变 |

以上测试至少覆盖 `max_parallel_tasks=1` 和大于 1、不同完成顺序、live continuation 与 state-led preflight。负向 State
测试必须同时断言 State/commit 不变以及 callable/Port 零调用。

## 8. 当前门禁事实

本次复审实际检查结果：

- 完整 pytest：**1240 passed, 1 failed**；唯一失败是结构复杂度 ratchet；
- complexity health：通过，所有强制为零的健康指标均为 0；
- pyright：**189 errors**；
- wheel：构建产物存在，但不包含 `mote_kernel/py.typed`；
- 因 pyright 与 complexity 未通过，`make check` 不通过，当前不能 handoff。
- monorepo root `pre-commit run --all-files`：只有 kernel structural complexity ratchet 失败；其余已执行的格式、Rust、
  Cloudflare 与 secrets 检查通过。

门禁只能在完成上述语义和架构整改后重新运行。禁止先提高 ratchet、删除有意义测试、关闭 strict typecheck 或去掉 typed
classifier 来制造绿色结果。

## 9. P2 边界，不要倒灌进 P1

以下内容按实施计划仍属于 P2，不应被误报成当前 P1 的缺陷：

- state-led durable evidence reader；
- graph input 与 `StartGraphRun` 的原子证据；
- successful settlement 与 publication 的原子持久化；
- retention/release、codec、bytes hard limit、跨语言 conformance。

P1 的硬要求是：在这些能力完成前，public durable feedback 必须关闭；当前进程内 cause/routing/materialization 模型不能
留下会迫使 P2 建第二套 State、runner 或 publication truth 的债。

## 10. 整改顺序与退出条件

建议按下面顺序实施，都是工程上已有明确更优解的事项，不需要 owner 再选方案：

1. 先收口 activation command 和 State cause，删除 START fallback 与双份 node truth；
2. routing 对所有 target 统一生成并校验 candidate cause；
3. 拆掉 child 全局栅栏，落实 mixed frontier 的 Pending 排空；
4. 把 awaiting child cleanup 移到 terminal Failed 阶段，并同步 recovery proof；
5. 关闭 public feedback 类型泄漏，删除重复 compiled-plan 深校验；
6. 明确 recovery cycle 等价关系，保留必要 proof、净删没有反例支撑的维度；
7. 集中 activation lookup projection，迁移/删除相应测试和旧计划口径；
8. 恢复 `py.typed`，清零 pyright，按人工判断化简可清除复杂度；
9. 跑完整 `make check` 和 monorepo root `pre-commit run --all-files`。

只有 P0 全部关闭、P1 架构项完成、验收矩阵通过且全仓门禁为绿，才能把实施计划状态重新写成“P1 已完成”。
