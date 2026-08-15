# Frontier 节点级结算实施方案评审回复

## 1. 回复信息

- 回复对象：`docs/frontier-node-settlement-implementation-review.zh-CN.md`
- 实施方案：`docs/frontier-node-settlement-implementation.zh-CN.md`
- 回复日期：2026-08-15
- 当前状态：方案复审与后续代码复审阻断项均已关闭；production code、测试迁移与交付验证完成
- authoritative specification：仅为 `docs/frontier-node-settlement-implementation.zh-CN.md`

## 2. 总体结论

接受评审当时的总体判断：方案方向正确，但在 session 生命周期、nested terminal completion ingress、join 防护、resource canonical shape
和 ordinary error 协议闭合前，不能直接进入代码实施。相关缺口在终版方案中闭合并经复审通过后，现已完成代码实施。

本次已将真正影响 correctness、恢复边界和唯一事实源的意见合入实施方案，主要包括：

1. `GraphExecutionSession` 的显式生命周期、幂等 `aclose()`、cancellation-safe close 和 quiescence-before-fence；
2. nested terminal projection 进入唯一 completion source，并通过同一个 `SettleGraphNode` path；
3. `CompleteGraphFrontier` 拒绝丢弃 non-empty `join_progress`；
4. `GraphRunState.resources` 对无 acquisition 采用唯一 `None` 表达，同时保留底层 resource replay 的临时空 acquisition snapshot；
5. ordinary error 后停止所有尚未启动的 Pending activation，并按 canonical task order 选择 deterministic error；
6. selector 的 canonical `GraphTask.sort_key` 顺序和 state acknowledgment 的最小证明条件；
7. 对应的 session、nested、join、resource、error 和 recovery 行为测试纳入迁移账本。

实施方案的开头原则、需求边界、唯一真相源和“不新增 legacy 门禁测试”约束保持不变。评审文档和本回复只记录决策，不构成第二份
runtime 或 implementation specification。

## 3. 已接受并合入实施方案

### 3.1 Session lifecycle、close 和 quiescence

接受评审关于长生命周期 session 不能依赖旧 `TaskGroup` 隐式清理的判断。

实施方案现固定以下语义：

- session-local disposition 为 `OPEN`、`ERROR_DRAINING`、`QUIESCENT`、`CLOSED`，不写入 `GraphRunState`；
- session 同时提供 async context manager 和幂等 `aclose()`；
- close 会取消并等待 live task，未 yield 的 transient completion 不伪造成 settlement；
- 已应用的 sibling settlement 不会被 close 撤销；
- `aclose()` 成功返回并确认 quiescent 后，调用方才可提交 exact `FenceGraphExecution`；
- `next()` cancellation 走 cancellation-safe close；cleanup 期间同一 task 再次被取消也会等待 close 完成后再传播 cancellation；
- closed session 不得再次启动 node、submit task 或产生 settlement command。

这样既闭合了后台 task、异常和 fence 的生命周期，又没有把 coroutine、task handle 或 continuation 写入 durable state。

### 3.2 Nested terminal completion ingress

接受评审指出的真实缺口：当前 batch runtime 的 `nested_results` 不能直接替代 streaming completion source。实施方案现在要求：

- session 创建/首次 `next()` 重新验证 child identity、terminal status、definition snapshot 和 parent activation；
- `CompletedChild` / `AbortedChild` 转为 precomputed completion，注入唯一 completion source；
- precomputed completion 按 `GraphTask.sort_key` canonical 排序；
- 不调用 ordinary Port scheduler，不占 `max_parallel_tasks` slot，不建立 resource acquisition；
- 仍使用同一个 `SettleGraphNode` reducer path；
- acknowledged nested completion 不得在同一 session 再次入队；
- command 未应用即崩溃时允许新 attempt 重新投影，保持 at-least-once，而不是 Graph exactly-once。

该设计保持 MissingChild / ActiveChild barrier，不把 nested orchestration 扩展成 child/ordinary partial claim。

### 3.3 Join progress guard

完全接受。当前实现已有 `CompleteGraphFrontier` 的 unresolved join guard；standalone command 重构时必须原样保留：

```text
state.frontier_status == SETTLED
state.execution is None
state.resources is None
state.join_progress == ()
```

`join_progress` 非空时 transition 整体失败，不能清空 Frontier 并进入 `COMPLETED`。这属于 state-owned correctness，不依赖 routing owner 永远生成正确
command。

### 3.4 Resource canonical shape

接受评审对 durable state 双重表达的判断，但将约束精确限定为 `GraphRunState.resources`：

- authoritative state 中没有 acquisition 时只能为 `resources=None`；
- state 挂载 empty `ResourceSnapshot` 时 fail closed；
- settlement 释放最后一个 acquisition 后规范化为 `None`；
- `initial_resource_snapshot()` 和底层 `resource_reducer` 仍可使用带 resource locks、无 acquisition 的临时 replay 基础 snapshot。

这样消除 durable state 的无意义分支，同时不破坏现有资源 reducer 的纯函数 replay 语义。

### 3.5 Ordinary error 和 deterministic policy

接受“停止新 waiter”不足以覆盖 resource-free Pending node 的意见，统一为：

```text
session 观察到首个 ordinary error
    -> 不再启动任何尚未启动的 Pending activation
       （resource-free、admitted、waiting 均包括）
    -> 已启动 task 进入 quiescence/cleanup
    -> 已得到的 typed completion 仍逐个交付
    -> quiescent 后暴露 deterministic ordinary error
    -> exact fence remaining attempt
```

多个 ordinary error 按 `GraphTask.sort_key` 最小者选择对外异常；这只影响 execution-local error presentation，不写入 GraphState，也不构造 retry
policy 或 error history。

本方案不强制 ordinary error 一出现就立即取消所有已启动 sibling。已启动 task 可以按 scheduler 的自然 quiescence 规则完成；调用方显式 close
时才执行取消清理。这样保留已得到的 typed completion，且不在本需求中新增 Port 副作用补偿语义。

### 3.6 Selector order 和 state acknowledgment

接受两项协议收紧：

- slot 不足时按 canonical `GraphTask.sort_key` 选择，不持久化 scheduler queue；
- 后续 `next(state)` 必须验证单 revision successor、上一 command 的 node settlement、graph identity/version、superstep、exact token 和
  resource guard。

execution 不复制 reducer，也不自行计算 successor；未来 Store 若存在，只替换调用方的 commit 部分。

## 4. 部分接受与明确不接受的内容

### 4.1 不接受“nested completion exact-once”作为 durable 要求

评审测试建议中的“exact-once completion”字样不直接接受。

原因：需求明确排除 Graph 层 exactly-once；command 在 yield 后、应用前崩溃时，节点必须仍可按 at-least-once 重跑。最终采用的口径是：

- 单个 session 内同一 Pending activation 最多交付一次；
- acknowledged settlement 不得再次入队；
- 未提交 command 的 crash 允许新 attempt 重新投影；
- reducer 的 stale/Pending/token guard 拒绝已结算状态上的重复 command。

这是 session 内重复保护，不是跨崩溃、跨 attempt 的 exactly-once 保证。

### 4.2 不接受把 session lifecycle 变成 durable state 或新增 Store receipt

接受生命周期语义，但不接受把 `OPEN`、`ERROR_DRAINING`、`QUIESCENT`、`CLOSED` 写入 `GraphRunState`，也不新增 close receipt、journal 或
commit protocol。

原因：用户明确不实现具体 Store、数据库、journal 和 exactly-once；这些运行时事实只能属于 session。`aclose()` 返回只是 execution-local
quiescence proof，不能成为第二份 durable truth。

### 4.3 不接受对底层 `ResourceSnapshot` 全局禁止空 acquisition

评审的 durable canonical 目标接受，但不把它扩大到 `resource_reducer` 的所有中间值。

原因：资源 reducer 的初始 replay snapshot 天然可以只有 resource locks、没有 acquisitions；全局禁止会破坏现有纯 reducer 和 replay 语义。约束只
适用于挂载到 `GraphRunState.resources` 的 authoritative/recovered state。

### 4.4 不接受强制唯一调用语法或把 `execute()` 改成无状态 async iterator

接受 async context manager 与 `try/finally + aclose()` 两种调用方式，不强制调用方只能采用其中一种。

同时不把 `GraphExecutor.execute()` 改成隐藏 state acknowledgment 的普通 async iterator。当前确定的协议是：

```text
GraphExecutor.execute() -> GraphExecutionSession
GraphExecutionSession.next(authoritative_state) -> one ExecutedGraphNode
```

原因：每个 completion 之后必须等待调用方应用新的 authoritative state，才能决定 waiter 是否可启动；把 state acknowledgment 隐藏在无状态 iterator
中会重新引入 execution-local predicted state，违背唯一真相源要求。`execute()` 仍然是逐节点 execution entry point，只是逐次 yield 由其
state-aware session 完成。

### 4.5 不接受 ordinary error 自动取消所有已启动 sibling 作为强制策略

评审要求 close 必须能 cancel/await live task，这一语义已接受；但不把“观察到 ordinary error 后立即取消所有已启动 sibling”定为唯一运行策略。

原因：

1. 已启动 sibling 可能已经完成 typed outcome，必须保留可交付的 settlement；
2. 现有 scheduler 已有自然 quiescence 和 cleanup 语义；
3. 自动取消会扩大 cancellation 语义和 Port 副作用边界，超出本需求；
4. 调用方需要更快停止时，可以显式 `aclose()`，然后在 quiescent 后 fence。

规范只要求 ordinary error 后不再启动新 activation，并在暴露 error 或 fence 前完成 quiescence。

### 4.6 不接受固定 public lifecycle enum 名称

实施方案采用四种 disposition 作为必须表达的语义，但不要求它们必须是公开 enum、特定类名或 durable command。

原因：公开固定名称会扩大 API surface，且不改善 GraphState correctness。只要 session-local state machine 能证明 open、draining、quiescent 和
closed 的行为，内部 enum 或等价实现均可。

## 5. 测试与门禁处置

接受评审提出的行为测试，并合入实施方案测试账本：

- session early close、repeated close、next cancellation、close 后 fail closed；
- fence 只能发生在 quiescence 后；
- nested-only terminal Frontier、mixed nested/ordinary completion 和 acknowledged nested 不重复入队；
- `CompleteGraphFrontier` 丢弃 unresolved join progress 的反例；
- GraphRunState empty resource snapshot rejection，同时保留低层 replay snapshot 测试；
- ordinary error 停止所有未启动 activation、多异常 deterministic selection；
- selector canonical order 和 state acknowledgment successor proof。

这些都是正向行为/状态边界测试，不是 legacy symbol/file/import absence gate。原有仍成立的测试继续按 KEEP/MIGRATE/REPLACE 账本保留，不能通过
删测试获得绿色结果。

复审通过后的实施现已完成。实施初次收口曾报告 422 passed，并以 100% statement/branch coverage 解释相对历史 504 项的下降；这个解释不成立，
因为覆盖率不能证明历史独立 case 已逐项迁移。复核后已按历史 case 建立 KEEP/MIGRATE/REPLACE 落点，补回 child projection 坐标、interrupt
generation/fence/cancel、resource authority/nested/later-error、start/codec、standalone resolution 和 resume atomicity 等边界。终版重新收集并通过
522 项：历史 504 项均有落点，新增项覆盖新 session 独有的 claim/cancellation/reclaim、linear construction、并发 lifecycle、repeated-cancel cleanup 和 queued
completion 调度边界。旧 batch collector、resource wave 和内联 resolution 专属断言均改为新模型的反向行为测试，没有原样恢复旧路径，也没有新增
legacy symbol/file/import/string-scan 门禁。最终 2,105 statements 与 676 branches 均为 100% coverage；完整 lint、format、strict typing、tests、
build、package check、monorepo pre-commit 与 diff whitespace gate 均通过。

## 6. 后续代码复审意见处置

`docs/frontier-node-settlement-implementation-code-review.zh-CN.md` 提出的三个 correctness 阻断项全部合理并已接受，没有以测试覆盖率或 reducer
stale guard 代替修复：

1. 公开 `GraphExecutionSession` 已改为不可直接构造的 runtime-checkable protocol；`GraphExecutor.execute()` 先验证 request/task scope，再以
   executor owner 线性消费 prepared claim，并用一次性 consumed receipt 签发唯一 concrete session。错误 owner、request、token、resource、task
   scope、重复 claim 或重复 receipt 都在 node invocation 前 fail closed；
2. session 增加 scheduler 前的 in-flight `next()` gate，并发第二个 `next()` 确定性返回 `ResultCollectionError`。`aclose()` 通过同一 lifecycle
   disposition 和 close lock 幂等清理；scheduler 在 await 前冻结 handle-to-task 映射，close race 不再泄漏内部 collection 异常；
   cancellation cleanup 由独立 close task 完成，即使同一 `next()` task 在等待期间再次被取消也不会中断 worker cleanup；
3. session 确认最新 authoritative successor 后，先提取 queued ordinary errors；若只剩 queued typed completion，则在返回它之前按新 state
   补满 scheduler slot，并让 newly admitted waiter 实际开始。若 queued event 是 ordinary error，则先进入 `ERROR_DRAINING`，不启动 waiter。

对应新增了 public construction、一次性 receipt、concurrent `next()`、concurrent close、repeated-cancel cleanup、queued typed waiter、queued error
waiter 和 queued invalid projection 的确定性测试。三项修复与加固都属于进程内 execution/session 正确性，没有引入 Store、retry、Port 幂等、Graph exactly-once、external
adversary 或网络攻击模型，也没有增加 legacy symbol/file/import/string-scan 门禁。

## 7. 最终边界与结论

修订后的唯一实施闭包为：

```text
authoritative GraphRunState
    -> atomic claim
    -> state-acknowledged GraphExecutionSession
    -> ordinary or precomputed nested completion
    -> one SettleGraphNode
    -> reducer atomically settles + releases + admits
    -> session waits for authoritative successor
    -> stable SETTLED
    -> standalone routing resolution
```

本回复接受评审指出的 correctness/lifecycle 闭口，同时拒绝把 exactly-once、Store receipt、durable session state、全局 resource snapshot 禁令或
强制 API 语法引入本次范围。上述取舍已落实并通过验证。实施方案仍是唯一 authoritative specification；本回复只记录为什么这样取舍。
