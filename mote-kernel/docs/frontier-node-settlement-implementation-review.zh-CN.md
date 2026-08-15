# Frontier 节点级结算与资源即时调度实施方案评审

## 1. 评审对象

实施方案：

- docs/frontier-node-settlement-implementation.zh-CN.md

评审回复：

- docs/frontier-node-settlement-implementation-review-response.zh-CN.md

被替代的历史实施基线：

- docs/frontier-node-resume-implementation.zh-CN.md

适用工程规则：

- monorepo 根目录 AGENTS.md；
- mote-kernel/AGENTS.md；
- execution 是唯一 graph execution engine；
- GraphState 是可恢复执行位置的 authoritative owner；
- state transition 必须保持纯函数；
- 不建立兼容 alias、第二执行路径或隐藏 mutable state。

评审日期：2026-08-15。

本次评审重点：

1. 节点级 settlement、资源释放和 waiter 推进是否形成一个原子状态转换；
2. 动态 scheduler/session 是否有完整且可实现的生命周期；
3. stable SETTLED Frontier 与 standalone routing resolution 是否保持既有 routing/join 事实；
4. nested terminal result 是否真正进入唯一 completion pipeline；
5. Frontier、resources 和 execution token 是否各自只有一份 durable truth；
6. 异常、取消、fence 和 at-least-once 恢复边界是否闭合；
7. 测试迁移与完成门禁能否证明目标行为，而不是只证明旧符号被删除。

## 2. 总体结论

**方案方向通过，实施准入暂不通过；修订后需要复审。**

方案已经正确识别当前 batch settlement、batch collector、resource waves 和 settlement 内联 resolution 不能满足节点级恢复边界，并给出了总体正确的替换方向：

- 一个 typed node completion 对应一个 SettleGraphNode；
- 一个 reducer revision 原子表达 node settlement、resource release、waiter admission 和 execution disposition；
- resource-free 与 resource-bearing node 共用一个 scheduler；
- 最后一个 settlement 先形成 stable SETTLED snapshot，再独立执行 routing resolution；
- token-only durable lease 删除可从 Frontier 派生的 participant 镜像；
- 不增加 Store、retry、exactly-once、output persistence 或 multi-worker node lease；
- 旧 batch runtime 作为整体被替换，不保留兼容 alias、wrapper 或第二 runner；
- 测试迁移强调保留仍成立的语义，不以删除测试或 legacy absence gate 获得绿色结果。

这些决定与文档第 3 行提出的“零已知架构负债、复用基础设施、唯一真相源、最佳整体改动和不越界”原则基本一致。

但是，当前方案仍有三个 correctness / lifecycle blocker：

1. 长生命周期 GraphExecutionSession 没有明确的 close、cancel 和 quiescence 协议；
2. nested terminal result 没有被具体接入只选择 ordinary node 的 completion session；
3. CompleteGraphFrontier 没有保留“不得丢弃 unresolved join_progress”的 reducer guard。

此外还有两个必须收紧的协议口径：

1. resources=None 与空 ResourceSnapshot 的 canonical 表达尚未闭合；
2. ordinary error 后停止调度的范围及 deterministic error 选择规则不明确。

在这些问题关闭之前，方案不能作为无歧义的最终实施规范，也不能判定其已经按原则完成任务。

## 3. 必须修正项

### 3.1 GraphExecutionSession 缺少显式生命周期协议

#### 问题

方案第 9.2 节让 GraphExecutor.execute 返回一个跨多次 next(state) 调用存活的 session。第 9.3 节又明确 session 持有：

- live asyncio task handles；
- completion queue；
- 已启动 node IDs；
- 尚未交付的 ordinary exception。

第 9.5 节要求在 ordinary error 或 cancellation 后，外部确认 workers 已经停止，再提交 exact fence。

但方案没有定义任何能够完成或证明这一前置条件的 API：

- 没有 async context manager；
- 没有 aclose() / close()；
- 没有说明 close 是否幂等；
- 没有说明 next() 被取消时 live tasks 是继续、被取消，还是转入 error-draining；
- 没有说明调用方收到一个 command 后决定不继续调用 next() 时如何清理 siblings；
- 没有说明 close 时如何处理已经完成但尚未交付的 typed completions；
- 没有说明 session close 后再次 next() 的失败行为；
- 没有定义何时可以证明 session 已经 quiescent 并允许 exact fence。

当前 scheduler 使用一个 await 范围内的 asyncio.TaskGroup，因此退出 execute_tasks 时天然完成 task cleanup。新 session 把 task 生命周期扩展到多次 API 调用后，不能继续依赖这一隐含保证。

若不补齐该契约，可能出现：

1. 调用方停止消费 session 后遗留后台任务；
2. worker 仍可能产生 Port 副作用时 durable attempt 已被 fence；
3. next() cancellation 吞掉或重复暴露 completion；
4. session 被遗弃后只能依赖对象析构清理 asyncio task；
5. ordinary error 分支无法可靠满足“先停止 workers，后 fence”的恢复规则。

#### 必须修订

方案必须选择并固定一种显式生命周期，例如：

~~~python
async with await executor.execute(claim, claimed_request) as session:
    completed = await session.next(state)
~~~

或：

~~~python
session = await executor.execute(claim, claimed_request)
try:
    completed = await session.next(state)
finally:
    await session.aclose()
~~~

无论采用哪一种形态，都必须定义：

1. session 的 OPEN、ERROR_DRAINING、QUIESCENT、CLOSED 状态；
2. aclose 的幂等性；
3. 所有已启动 tasks 必须 cancel/await 或自然 quiesce；
4. close 完成前不得声称可以 fence；
5. close 后不得再启动 node 或产生 settlement command；
6. next() cancellation 是否自动 close，或是否要求调用方显式 close；
7. command 已 yield 但未提交时关闭 session，节点仍保持 Pending，允许 fence 后 at-least-once 重跑；
8. 已经 reducer-applied 的 sibling settlement 永不由 close 撤销。

#### 必须新增的测试

至少增加：

1. A command yield 后调用方提前退出，aclose 会等待或取消仍 live 的 B；
2. next() 被取消后不会遗留 task；
3. repeated aclose 幂等；
4. close 后 next() 和 submit fail closed；
5. 只有 aclose/quiesce 完成后才执行 exact fence；
6. close 前已应用的 sibling settlement 保留，未应用 completion 仍为 Pending。

### 3.2 Nested terminal result 没有进入 session completion source 的明确路径

#### 问题

方案同时规定：

- executable selector 只启动 ordinary NodeDefinition；
- scheduler 只接收 selector 选出的 ordinary executable tasks；
- parent nested node 的 terminal TaskSuccess / TaskFailure 必须通过相同的 SettleGraphNode path；
- nested node 不建立虚假 resource acquisition。

当前实现的 frontier preparation 会把 CompletedChild / AbortedChild 转换为独立的 nested_results，随后在 batch execution 完成后与 ordinary results 合并。

新方案删除 batch collector 后，没有说明 nested_results 如何进入新的动态 completion source。按第 9.3 节的 next(state) 步骤直译：

~~~text
selector ordinary nodes
    -> scheduler.submit
    -> scheduler.next_completion
~~~

一个只包含 completed nested children 的 Frontier 没有 ordinary task 可以 submit，也没有定义好的 completion event，session 因而可能永久等待。混合 ordinary + terminal child Frontier 也没有明确：

- terminal child result 何时入队；
- 相对 ordinary completion 的交付顺序；
- 是否占用 max_parallel_tasks slot；
- 如何保证同一 child terminal projection 只结算一次；
- 如何在每次 authoritative state acknowledgment 后丢弃已经 settlement 的 nested completion。

测试清单虽然要求“all Pending nested terminal results 通过同一 SettleGraphNode path”，但只有目标断言，没有实现协议。

#### 必须修订

方案应明确：

1. session 创建时重新验证 claimed request 中的 terminal child projections；
2. 将对应 TaskSuccess / TaskFailure 作为 precomputed completion 注入唯一 completion source；
3. precomputed nested completion 不调用 ordinary Port scheduler、不占 live task slot、不创建 resource acquisition；
4. 每个 nested activation 按 task identity 精确入队一次；
5. 已经在 authoritative state 中 settlement 的 nested node 不得再次交付；
6. completion command 仍使用调用方传入的最新 revision 和 exact active token；
7. terminal nested completion 与 ordinary completion 的排序策略必须确定；
8. MissingChild / ActiveChild barrier 保持 claim 前 disposition，不进入 active session。

#### 必须新增的测试

至少增加：

1. Frontier 只有一个 CompletedChild，首个 next() 立即返回该 node 的 SettleGraphNode；
2. Frontier 只有一个 AbortedChild，通过 TaskFailure 进入同一路径；
3. 多个 terminal children 使用 canonical、exact-once completion 顺序；
4. terminal child 与 ordinary sibling 共存时均能逐节点 settlement；
5. nested completion 不占 scheduler slot、不建立 resource acquisition；
6. acknowledged nested settlement 不会在下一次 next(state) 重复交付。

### 3.3 CompleteGraphFrontier 必须拒绝丢弃 unresolved join_progress

#### 问题

方案第 7.5 节对 CompleteGraphFrontier 的定义是：

1. status 变为 COMPLETED；
2. 清 Frontier、join_progress、resources 和 execution；
3. 使用 canonical terminal position。

它只要求输入为 RUNNING + SETTLED + quiescent，没有要求当前 join_progress 为空。

当前 state reducer 明确包含以下保护：

~~~text
if state.join_progress:
    reject CompleteGraphFrontier
~~~

这是必要的 state-owned correctness guard。routing engine 正常情况下不会在仍有 partial join progress 时生成 CompleteGraphFrontier，但 reducer 不能只信任 compiled execution projection。否则直接构造、错误投影或损坏恢复路径可以用一个合法 shape 的 command 清除 durable join facts，并错误完成 GraphRun。

这也与方案要求“保留 routing、join 和 deterministic resolution 既有语义”直接冲突。

#### 必须修订

CompleteGraphFrontier transition 必须明确：

1. 输入 Frontier 为 stable SETTLED；
2. execution/resources 均为空；
3. expected revision 精确匹配；
4. state.join_progress 必须为空，否则整个 transition 原子失败；
5. 只有通过上述验证后才能进入 canonical COMPLETED position。

AdvanceGraphFrontier 继续由 stable validation 验证 non-empty canonical next nodes 和 canonical join progress shape；compiled topology 正确性仍由 routing owner 负责。

#### 必须新增或保留的测试

1. SETTLED + non-empty join_progress 拒绝 CompleteGraphFrontier；
2. 失败转换不修改输入 state；
3. routing resolver 对 partial join deadlock 不投影 CompleteGraphFrontier；
4. join_progress 为空时 completion 正常进入 canonical terminal position。

## 4. 必须收紧的协议口径

### 4.1 resources=None 必须是“无 acquisition”的唯一 canonical state

#### 问题

方案规定 resource-free Frontier 的 ClaimGraphExecution.resources 为 None，并在最后一个 acquisition 被释放后把 resources 规范化为 None。

但 stable invariants 与 ClaimGraphExecution reducer 步骤只要求：

- resources 非空时 execution 也非空；
- acquisition participants 是 Pending subset；
- ResourceSnapshot 可 replay。

这些规则没有拒绝：

~~~text
ResourceSnapshot(resources=(...), acquisitions=())
~~~

因此 resource-free active attempt 可能同时有两种 durable 表达：

~~~text
resources = None
resources = empty ResourceSnapshot
~~~

两者都不包含 acquisition，却表达相同的调度事实。这违反方案自己的 canonical state 和唯一真相源原则，也会让 guard、测试和序列化产生无价值分支。

#### 必须修订

增加稳定不变量：

1. resources is not None 时 acquisitions 必须非空；
2. acquisition 为空时唯一合法 durable 表达是 resources=None；
3. claim reducer 拒绝或规范化调用方提交的 empty ResourceSnapshot；
4. settlement 释放最后一个 acquisition 后规范化为 None；
5. recovered active state 携带 empty ResourceSnapshot 时 fail closed。

该规则不要求 state owner 理解 compiled resource requirements，只约束 state-owned canonical shape。

### 4.2 Ordinary error 后必须停止所有未启动 task，而不只是 waiter

#### 问题

方案第 9.5 节写的是：

> 发现首个 ordinary error 后不再启动新的 waiter。

第 13.5 节写的是：

> ordinary exception 停止启动新的 tasks。

这两个规则并不等价。动态 max_parallel_tasks 下可能存在：

- 尚未启动的 resource-free Pending node；
- 已 admitted 但尚未占用 slot 的 resource node；
- 仍 waiting 的 resource node。

resource-free node 不是 waiter。若实现按第 9.5 节字面执行，ordinary infrastructure error 发生后仍可能启动新的 Port invocation，扩大副作用面。

#### 必须修订

统一规则为：

~~~text
一旦 session 观察到第一个 ordinary error，
不得再启动任何尚未启动的 Pending activation，
包括 resource-free、admitted 和 waiting node。
~~~

已启动 sibling 的处理继续遵循：

1. 进入 quiescence/cleanup；
2. 已得到的 typed completions 可以按协议逐个交付；
3. 不为 ordinary error node 生成 settlement；
4. 全部已启动 workers 停止后暴露 error；
5. 调用方随后对 remaining active attempt 使用 exact fence。

方案还必须定义“deterministic ordinary error”的选择规则。多个 task 抛出异常时，可以选择：

- quiescence 后按 GraphTask.sort_key 选择首个；
- 使用严格排序的 typed aggregate error；
- 其他明确、可测试且不依赖 event-loop race 的规则。

不能一方面要求 deterministic error，另一方面直接以并发 completion race 决定对外异常。

## 5. 建议收紧项

### 5.1 Selector 在 slot 不足时应明确 canonical selection order

方案定义了 node 可执行的条件，也要求 max_parallel_tasks 约束 live/selected tasks，但没有明确可执行节点多于空闲 slot 时选择谁。

建议直接复用 planner 产生的 canonical GraphTask order，或明确使用 GraphTask.sort_key。这样可以保证：

- 测试不依赖 set/dict iteration；
- resource-free 与 admitted nodes 的选择可预测；
- acquisition 已持有但迟迟未启动的 idle reservation 行为可解释；
- session 重复接收同一 authoritative state 时 selector 结果稳定。

这不要求持久化 scheduler queue；只规定同一 state + session runtime disposition 的纯选择结果。

### 5.2 State acknowledgment 的最小证明条件应写清

方案要求后续 next(state) 接收“上一条 yielded settlement command 的已应用 successor”，同时又要求 execution 不调用 reducer、不预测 next state。

建议明确 session 依赖 authoritative reducer/store 的单-command revision 语义，并验证至少：

1. revision 精确增加一次；
2. 上一 command 的 node 已变成与 outcome 对应的 settlement；
3. 该 node 不再 Pending；
4. 其他 active session identity 仍匹配 exact token，或因最后 settlement 合法清空；
5. resource snapshot 通过 stable validation 和 compiled resource guard；
6. graph identity、superstep、definition/version 未发生非法变化。

不应在 execution 中复制完整 graph reducer 来重新计算 successor。若未来需要 commit receipt，应由 Store/AgentState owner 另行设计，不在本方案中虚构。

## 6. 已通过的核心设计

### 6.1 Node settlement 与资源原子性

- 一个 SettleGraphNode 只携带一个 typed outcome；
- reducer 只更新当前仍为 Pending 的 node；
- success、failure 和 interrupt 都表示 invocation 已结束，使用同一 resource release 规则；
- resource reducer 从 authoritative ResourceSnapshot 推导 owner release 和 FIFO waiter progression；
- waiter 在同一个 next GraphRunState 中成为 admitted；
- execution 不提交预测 resource snapshot；
- ordinary exception 不伪造成 Failed/Interrupted，也不提前释放 authoritative resources。

该设计直接满足节点结果、资源释放和 waiter 推进的原子状态转换目标。

### 6.2 唯一事实源

- GraphFrontierState.nodes[].settlement 是 node status/result 的 durable truth；
- ResourceSnapshot 是 owner、waiter、acquisition 和 admission 的 durable truth；
- GraphExecutionLease 只保存 exact token；
- attempt remaining participants 从当前 pending_node_ids(frontier) 派生；
- live task handles、started IDs 和 completion queue 只属于 session；
- 不增加 RunningGraphNode settlement、第二 scheduler snapshot 或 output store。

除第 4.1 节指出的 empty ResourceSnapshot canonical gap 外，owner 边界正确。

### 6.3 唯一执行路径

- resource-free 与 resource-bearing node 使用同一个 selector、scheduler 和 completion source；
- resource 只决定 selector 是否允许启动 node；
- execute_resource_waves 被删除；
- batch collector 被 single completion validation 替代；
- max_parallel_tasks 转为 live task limit，不再拒绝整个大 Frontier；
- execution 仍是唯一 graph engine，没有 domain-private runner。

这是对当前 batch/resource-wave 闭合约束的合理整体替换，不是旁路补丁。

### 6.4 Stable SETTLED 与 routing barrier

- 最后一个 node settlement 不执行 routing；
- RUNNING + SETTLED 成为合法、quiescent、可恢复 snapshot；
- ReadyToResolve 只从 persisted SETTLED state 投影；
- AdvanceGraphFrontier / CompleteGraphFrontier 使用下一条 revision；
- crash 发生在 final settlement 与 routing 之间时，不重跑 Frontier node；
- resume/skip 形成 SETTLED 后也经过相同 barrier。

在补回 CompleteGraphFrontier 的 join guard 后，该模型能正确表达最终 resolution 的恢复边界。

### 6.5 Recovery、fence 与边界控制

- completion 未应用即崩溃时 node 仍为 Pending，可 at-least-once 重跑；
- settlement 已应用后 node 不得重跑；
- partial settlement 后 exact fence 只清理 remaining attempt 的 execution/resources；
- 已结算 sibling 和 Pending input binding 保留；
- stale token、stale revision 和 stale interrupt identity fail closed；
- 不承诺 coroutine persistence、Port exactly-once 或 distributed partial claim。

该边界符合本期明确非目标。

### 6.6 测试迁移策略

方案对 KEEP、MIGRATE、REPLACE、REMOVE 的分类合理，并明确：

- 不整文件删除仍有效的行为覆盖；
- 不用 collected count 代替语义覆盖；
- 反转 later resource wave exception 和 batch all-or-nothing 旧断言；
- 增加 settlement/release/admit、SETTLED recovery 和 session acknowledgment 场景；
- 不新增 legacy symbol、file、import absence 或全仓字符串扫描门禁；
- 保留正向 architecture owner tests。

该策略符合“删除旧 runtime，但不让测试永久持有 legacy 知识”的原则。

## 7. 需求与原则对照

| 原则或目标 | 判定 | 说明 |
| --- | --- | --- |
| 每个 typed completion 独立 settlement | 通过 | SettleGraphNode 为唯一 single-result command |
| settlement、release、waiter progression 原子 | 通过 | 同一 pure reducer revision 表达 |
| waiter 在 authoritative next state 即时 admitted | 通过 | 复用唯一 resource reducer |
| 资源与无资源节点共用 scheduler | 通过 | 删除 resource waves，使用统一 selector/session |
| final settlement 与 routing 分 revision | 通过 | stable SETTLED + ReadyToResolve |
| Frontier/resource/token 唯一 durable truth | 有条件通过 | empty ResourceSnapshot canonical gap 需关闭 |
| 不保留第二 runner/compatibility path | 通过 | coordinated replacement 边界明确 |
| nested 既有语义保留 | 不通过 | terminal result 注入 session 的协议缺失 |
| join 既有语义保留 | 不通过 | CompleteGraphFrontier 缺少 unresolved join guard |
| exception/cancellation/fence 闭合 | 不通过 | session close/quiescence 契约缺失 |
| deterministic scheduling/error | 有条件通过 | selector tie-break 与 multi-error policy 需明确 |
| 不越界到 Store/retry/exactly-once | 通过 | 非目标边界明确 |
| 测试迁移不降低覆盖 | 通过 | 账本和新增场景总体充分 |
| 不新增 legacy absence gates | 通过 | 文档已明确禁止 |

## 8. 当前代码完成度核对

本次工作区中的实施方案状态为 Ready for implementation review；当前 production code 仍是被方案描述为待替换的 batch runtime：

1. state/graph_state/command.py 仍定义 SettleGraphExecution；
2. settlement command 仍携带 outcomes tuple 和 optional resolution；
3. ResumeGraphNodes 仍内联 optional resolution；
4. UpdateGraphResources 和独立 resource admission prepare round 仍存在；
5. GraphExecutionLease 仍保存 node_ids；
6. GraphExecutor.execute 仍返回 ExecutedFrontierAttempt；
7. execute_tasks 仍等待整个 TaskGroup 后返回 tuple；
8. execute_resource_waves 仍在 execution-local snapshot 上推进资源；
9. stable validator 仍拒绝 RUNNING + SETTLED；
10. final settlement 与 routing 仍由同一 command 原子完成。

因此必须区分两个结论：

- 作为实施方向：主体架构正确，但需按本评审修订；
- 作为实际交付：尚未实施，不能按完成定义判定完成。

当前 504 个 tests 全部通过，只能证明历史 batch baseline 自洽，不能证明新 settlement/session 模型已经完成。

## 9. 文档整改与复审门槛

实施方案进入编码前，至少完成以下文档修订：

1. 增加 GraphExecutionSession 显式 close/cancel/quiescence 状态机；
2. 明确 fence 只能发生在 session 已确认 quiescent 之后；
3. 定义 nested terminal results 注入唯一 completion source 的机制；
4. 补回 CompleteGraphFrontier 对 non-empty join_progress 的拒绝规则；
5. 规定 empty acquisition 的唯一 durable 表达为 resources=None；
6. 将 ordinary error 后“停止新 waiter”统一为“停止所有未启动 tasks”；
7. 定义多个 ordinary errors 的 deterministic 对外规则；
8. 明确 selector slot 竞争的 canonical order；
9. 在测试清单中加入 session early-close、next cancellation、nested-only terminal、join discard rejection 和 empty resource snapshot cases；
10. 在完成定义中加入“session close 后无 live worker，才能 exact fence”。

修订后复审应确认：

- 方案各章节不存在相互冲突的异常/调度口径；
- 文件级实施清单能找到 session lifecycle 和 nested completion 的明确 owner；
- 测试清单覆盖本报告中的真实反例；
- 没有通过引入第二 reducer、第二 scheduler、execution-local resource truth 或 Store abstraction 解决这些问题；
- conformance 影响在最终 observable contract 确定后重新评估。

## 10. 最终判定

| 评审维度 | 判定 | 说明 |
| --- | --- | --- |
| 总体架构方向 | 通过 | node settlement、resource atomicity、unified scheduler、SETTLED barrier 方向正确 |
| 唯一事实源 | 有条件通过 | 需关闭 empty ResourceSnapshot 双重表达 |
| Session 可实现性 | 不通过 | 缺少 close/cancel/quiescence 契约 |
| Nested 集成 | 不通过 | terminal results 尚无明确 completion ingress |
| Routing/join 正确性 | 不通过 | CompleteGraphFrontier 缺少 join_progress guard |
| 异常与恢复 | 有条件通过 | at-least-once/fence 原则正确，运行时停止协议未闭合 |
| 范围控制 | 通过 | 未扩展 Store、retry、exactly-once、output persistence 或 multi-worker lease |
| 测试策略 | 有条件通过 | 主账本合理，需补充本评审列出的反例 |
| 当前代码完成度 | 未完成 | production code 仍为 batch settlement/resource waves |

最终结论：

**该方案符合目标的大方向，但尚未达到“交付时不遗留已知架构负债”的实施准入标准。关闭三个 blocker 和两个协议缺口、补齐对应测试后，可重新评审并进入实施；当前代码任务尚未完成。**

## 11. 评审验证

本次评审执行了：

~~~text
python -B -m pytest -q
~~~

结果：

~~~text
504 passed in 0.72s
~~~

该结果是当前历史 batch baseline 的回归基线。

本次评审检查了：

- 当前 Git 状态和用户未提交文档变更；
- monorepo 与 mote-kernel 的 AGENTS.md；
- 实施方案全文；
- 被替代实施方案及既有评审口径；
- state command/model/reducer/validation/resource reducer；
- execution executor/claim/result/planner/admission/scheduler/resource stage/frontier/superstep/routing；
- 当前 nested terminal projection、join completion guard 和测试文件账本；
- conformance 当前目录与 manifest 状态。

除新增本评审文档外，本次未修改实施方案和 production code。
