# 父子图 GraphRun 本地 ownership 实施规范

> Target：GRC-LO-001-T01
> 状态：READY FOR INDEPENDENT REVIEW / NOT DEVELOPMENT AUTHORIZATION / CODE UNCHANGED

依据：[父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)。

本版只吸收第十六次独立评审的 LO-IR76～LO-IR78。实施文档不增加 private 类型或中间
协议，只闭合三个根因：live context 的删除、单一 child-call 生命周期事实、
node-origin cancellation 的消费边界。requirements 和现有 normative source 是唯一真相。

本文件只描述同一次 Graph.run invocation 的实现方式。State、reducer、Store、现有
Graph public API、对外 result/request ABI、sealed continuation/frame 的外部可观察形状和
错误 taxonomy 保持不变；内部 live context、state-bearing projection 字段及其 obsolete
consumer 按本方案删除，不以兼容别名保留。本文也不授予开发授权。

## 1. 冻结原则

1. 每个图调用建立一个私有 GraphRun。root 和每个 child 各自拥有自己的
   ScopeRunCoordinate、GraphRunState、transition、commit、GraphExecutor、session 和
   local frame。
2. child 在自己的 construction frame 内创建、校验和使用自己的 run identity。parent
   只传 ParentGraphActivation、CompiledGraph 和 typed input，不派生、保存、查询或控制
   child run_id。
3. parent 当前只保留 opaque child-call handle，以及不带 child state 的既有
   ActiveChild、CompletedChild、AbortedChild、AwaitingResume typed projection。handle 不暴露
   coordinate、state、frame、session、task 或 lookup key，invocation 结束即释放。
4. child 先完成自己的 exact commit；parent 只安装自己的 child boundary 并提交自己的
   nested-node settlement。child 已提交后 parent 失败，保留 child facts 并返回既有错误。
5. awaiting 只由独立的 continuation/frame 入口、出口适配消费既有
   ChildStateBinding 和 ScopedFrameIndex；它们不进入 live owner，不新增 record、relay、
   checkpoint、跨 invocation load 或 recovery。
6. 调用级 cancellation/explicit abort 可以沿 live handle 向下传递；每个 owner 独立
   close、必要的 fence、AbortGraphRun 和 release。不做跨 owner rollback、retry 或
   failover。

live execution 删除 GraphRunContext；不创建、不保留同名 transient/compatibility 壳，也不
保留其 `child_state`、`state_at`、`replace_state`、`replace_child` mutation path。真正的
执行循环只读取各自 GraphRun 的 state、frame 和 session。既有 `_GraphContinuation` 的
sealed snapshot/外部行为保持不变，但旧的 `admit() -> GraphRunContext` 和
`_context_from_continuation()`/`_continuation()` live helper 及其 direct caller 删除；入口/
出口适配直接读取或合成 snapshot。适配只把匹配的 ChildStateBinding/frame evidence 交给
对应 owner，parent 永远只看到 typed outcome/output/status，不看到 child state。

保留的既有基础设计：Graph/Graph.run、GraphRunState 和纯 reducer、单参数 GraphCommit、
GraphExecutor/ExecutionClaimOwner、GraphExecutionSession、既有 typed projection/result、
ChildStateBinding、ScopedFrameIndex、_GraphContinuation、AbortGraphRun、
FenceGraphExecution 和 _PartialCommitError。ChildStateBinding/frame 只在入口/出口适配
出现。禁止新增 public GraphRun/handle/result、State 字段或 variant、第二 runner、registry、
child-ID map、持久化协议、overlap gate、worker handoff、compatibility alias、legacy test
或 AST/helper-count test。

本版明确删除独立的 owner tree、owner sequence、child slot、export/plan provider、
cleanup gate、scope-proof/binder 和 invocation coordinator。还必须删除 live
`GraphRunContext` 及上述四个 mutation 方法、`PreparedNestedRun`、`StartMissingChildren`
及其 direct consumers；不以 alias 或旁路恢复。下面出现的 record、tuple 或 closure 只在
当前 method-local frame 存活，不是新类型、协议或可复用资料。

## 2. 三条唯一调用图

### 2.1 动态 owner、active child 生命周期

#### 唯一的本地事实：一个 child-call record

每个 parent invocation 只保留一条按 activation 排列的 method-local record 序列；每条
record 是该 activation 唯一的生命周期事实，不再同时维护两套生命周期集合或 projection
副本。record 只有以下内容（不新增运行时类型）：

- `activation`：既有 ParentGraphActivation 和一次生成的 deterministic position；
- `opaque_handle`：只允许 wait/abort/release，内部 identity 对 parent 不可见；
- `phase`：四个字面阶段 `fresh`、`awaiting`、`terminal`、`setup-failure`；
- `consume_once`：child owner 提供的一次性 closure，返回既有 typed terminal
  outcome/output/status 或出口所需 immutable evidence；调用后不可再次读取。

record 不含 GraphRunState、run_id、coordinate、frame、session、task、latest_projection 或
owner 引用。projection 只作为 parent 当前 prepare 的 transient typed 参数；parent 永远只
接收 outcome/output/status。

`fresh` 表示 exact start acknowledgement 已确认且 child 仍可运行；`awaiting` 表示 child
仍为 RUNNING 且等待 resume；`terminal` 表示 child 已确认 terminal，typed evidence 尚未
交给 parent，或已由当前 drive 取出但 parent settlement 尚未 exact acknowledgement；
`setup-failure` 仅表示未确认 candidate，绝不对 parent 可见。`consume_once` 只调用一次，
其返回的既有 typed outcome/output/status 和出口所需 immutable evidence 由当前 drive 的
method-local 变量接收，不写入 parent state。parent settlement exact acknowledgement 之前
record 保持 terminal 且 handle 仍可由 owner-local cleanup 使用；ack 后才清空 handle、退役
record，并仅保留本次出口所需的不可变 position/outcome evidence。

#### position 规则

- root position 是空 tuple；compiled definition 的 canonical node ordinal 与既有
  activation metadata 共同产生 child position。
- 同一 parent/node 的 generation 在同一条 record 序列中线性计算；terminal record 在当前
  invocation 出口前仅保留 immutable position/outcome evidence，因此不会复用 position，
  也不需要第二个集合或 map。
- 同一 invocation 内 position 不重复；兄弟按 definition ordinal 排列。完全相同的
  `ParentGraphActivation` 在同一 invocation/continuation 中是重复 admission，必须以既有
  ResultCollectionError/SnapshotMismatchError fail-closed，不创建第二条 record；foreign、
  stale 或乱序返回同样的既有错误。

旧 child 的 state、handle、frame、position 不复用；只有不同 superstep 或其他合法新
activation 才创建新 record、新 owner 和新 position。下一次 invocation 只从既有
continuation evidence 重新 admission，不做 child-ID lookup。

#### fresh child 的交接

    parent.prepare_frontier()
      -> MissingChild(parent_activation)
      -> parent materialize typed input
      -> child factory 显式校验 parent scope/activation/definition
      -> factory 调用 child_scope_run_for_activation()，这是唯一 runtime identity caller
      -> project_start_graph_command() 只校验传入 coordinate
      -> child exact StartGraphRun acknowledgement
      -> 创建 child GraphRun shell（自己的 state、scope 和 commit）
      -> 安装 child input frame、自己的 GraphExecutor/session 和递归 factory
      -> 在 parent 的 method-local record 序列追加一条 record（无 await）
      -> 只把 opaque handle 交给 parent

最后一个无 await 替换点前，child owner 由 construction frame 持有；parent 看不到 owner、
scope 或 commit。ack 前失败直接丢弃 candidate，不提交 AbortGraphRun。ack 后 frame、
executor、session、factory 或 handoff 失败由该 construction frame 清理 child。

#### terminal 退役和 settlement

    ActiveChild
      -> 以 status=RUNNING 的 transient projection 继续 drive，不写入 record
    CompletedChild
      -> 先调用该 record 的 consume_once()，由 child owner 取得并交出 typed terminal
         result/evidence；当前 drive 暂存该既有 evidence
      -> 校验 parent activation、definition、scope 和 output/status
      -> parent 以既有 add_child_boundary() 安装一次 immutable output frame
      -> parent 以同一 method-local typed evidence 重新 prepare/materialize
      -> parent claim -> session -> SettleGraphNode exact commit
      -> exact acknowledgement 后无 await 地清空 handle、退役该 record；出口适配只消费已
         交出的 immutable evidence，不再查找 child owner
      -> routing
    AbortedChild
      -> 先调用 consume_once() 取得并暂存既有 abort reason/status
      -> 校验后 parent 按既有 failed-child settlement exact commit
      -> exact acknowledgement 后无 await 地清空 handle、退役该 record
    AwaitingResume
      -> record 保持 awaiting，不安装 terminal boundary，不结算 success/failure

terminal consume、boundary 安装或 parent settlement 未获 exact acknowledgement 时，record
保持 terminal/已消费事实且 handle 不清空；child owner 继续负责其 last-exact state/frame
和 cleanup，直接返回既有 snapshot/value/publication/frame error。不重读 owner、不重跑 child、
不回滚 child；parent settlement 成功后才允许 record 退役和 handle release。

`consume_once` 是 terminal evidence 的唯一 producer；同一 method-local evidence 按
“parent settlement exact acknowledgement → 出口适配消费 → 丢弃”顺序流转，任何一步失败
都不回退到 owner lookup 或第二份 state。

新的 admission 只遍历 phase 为 fresh/awaiting 的 record；terminal record 仅由当前 drive
完成上述一次 handoff，不参加新的 admission。terminal evidence 在 consume_once() 时由
child owner 直接交给 parent 或出口适配；出口不得查找已退役 owner。cleanup 只关闭 record
仍持有的 opaque handle，并由 handle 递归 child-first；setup-failure candidate 不进入
active 序列，也不伪造 ABORTED。

### 2.2 waiting、result 和 cancellation 边界

#### drive 与 result

GraphRun.drive_quantum() 是唯一 private waiting loop，返回类型固定为 GraphBoundary。
WaitingForChildren 只在 loop 内消费，绝不传给 project_graph_result()，也绝不成为
GraphResult；它只携带 execution-internal 的 missing/active metadata，不携带或构造
PreparedNestedRun、StartMissingChildren。

    _GraphRun.drive_quantum()
      -> executor.prepare(owner request)
      -> ExecutableFrontier: owner claim -> owner session -> owner settlement -> loop
      -> ReadyToResolve: owner exact transition -> loop
      -> WaitingForChildren:
           missing: 直接按 parent activation 调用唯一 child factory，新增一条 record，loop
           active: 按 record position await opaque_handle.await_or_project()
                   runnable 继续 drive；terminal 先 consume_once，再按 2.1 settlement，loop
           无 runnable 且有 awaiting:
             按 record position 逐一消费 awaiting 的一次性出口 evidence，交给出口适配，
             构造既有 AwaitingResume boundary；消费后的 evidence 立即销毁
      -> AwaitingResume / CompletedGraph / AbortedGraph: 返回 GraphBoundary

missing 优先于 active。一个 sibling awaiting、另一个 runnable 时先继续 runnable，
直到 runnable 清空才生成 AwaitingResume。CompletedChild 必须先安装 parent boundary，
再重新 prepare，之后才允许 parent claim。

`project_graph_result(root, boundary: GraphBoundary) -> GraphResult` 是唯一 result 入口。
它只读取 root GraphRun 自己的 confirmed state/frame，以及 drive 已经交给出口适配的
transient typed child evidence；不查 child owner、child state 或已退役 record。runnable
分支不读取出口 evidence；awaiting evidence 由出口适配在本次 invocation 内消费一次，
terminal evidence 则已经在 parent settlement exact acknowledgement 前完成 handoff，
result 入口只读取适配交出的 immutable value。读取前验证非空、root-first、family、scope、
definition、lineage 和 revision；重复、foreign、stale 或 root/direct 错配返回既有错误。

入口/出口适配只有这一种 sealed transport 形状：读取 `_GraphContinuation` 的 immutable
snapshot，校验 family/root/lineage 和 canonical 顺序；root evidence 交给 root owner，
每个 `ChildStateBinding` 按 `parent_activation.scope_run` 与直接 child coordinate 的
精确匹配交给对应 child owner，child boundary frame 按 exact coordinate、其他 frame 按
各自既有 scoped coordinate 分区。先 admission parent，
再按既有 definition/activation 顺序递归 grandchild，兄弟保持该顺序；terminal binding
只转为当前 record 可消费的既有 typed evidence，不建立 live owner。匹配失败、重复
`ParentGraphActivation` 或 foreign/stale evidence 立即 fail-closed。

每次 admission 只读取一次本次 immutable snapshot，不标记、销毁或改写 caller continuation；
同一 sealed continuation 可被多个独立 invocation 只读复用。owner-local admission 取得
各自 state/frame，出口适配用 owner 交出的 immutable evidence 合成原有 snapshot/result，
不从已退役 owner 反查，parent 也不读取 `ChildStateBinding.state`。旧的
`_GraphContinuation.admit() -> GraphRunContext`、`_context_from_continuation()`、
`_continuation()` 及 direct caller 删除，不以 alias 恢复；不构造 invocation coordinator。

#### scheduler/session 的取消来源

不新增 NodeCancelled event 或 public error variant。scheduler 只传递既有
`TaskResult | TaskRaised`；为承载 node callable 自行抛出的 `CancelledError`，
`TaskRaised.error` 的内部类型固定为 `BaseException`，但 public error/result shape 不变。

    live =
      dict[TaskId, tuple[ExecutableTask, asyncio.Task[TaskResult | TaskRaised]]]
    pending =
      list[TaskResult | TaskRaised]
    _capture(...) -> TaskResult | TaskRaised
    next_completion() -> TaskResult | TaskRaised
    drain_pending_events() -> tuple[tuple[TaskRaised, ...], tuple[TaskResult, ...]]
    aclose() -> None
    session._errors = list[tuple[GraphTask, BaseException]]
    session._record_error(task, error: BaseException) -> None
    session._next_event() -> TaskResult | TaskRaised
    session.next(state: GraphRunState) -> ExecutedGraphNode
    session.aclose() -> None

`TaskResult | TaskRaised` 只是上述签名的 union 记号，不增加 event 类或协议。session 另有
一个 invocation-local、one-shot 的 node-origin 标记（记录原始 CancelledError 对象），
只供拥有该 session 的 GraphRun 读取，不进入 handle、State、continuation 或 result。

GraphRun 在 construction 时持有同一个 concrete session 实例；对外仍只暴露既有
`GraphExecutionSession` protocol。`drive_quantum` 是 node-origin 标记唯一的 owner-local
消费点：`session.next()` 抛出 `CancelledError` 后，GraphRun 在同一 owner 内比较并清除
one-shot 标记；匹配则按既有 node-local 规则完成 drain/close/release，nested child 交给
parent 既有 typed failure/abort projection，root 将原异常交给 facade 的 node-origin 分支
并在 release 后重抛。标记为空才是 waiter cancellation，才交给 facade 的通用 invocation
unwind；不增加 type、event、protocol 或 callback。

Task 不增加 cancel_for_close() 方法。scheduler 只提供一个 private helper：

    cancel_scheduler_task(task)
      -> task.cancel(_SCHEDULER_CLOSE_CANCEL)

`cancel_scheduler_task()` 是唯一的 scheduler 注入点，具体签名和 `_capture()` 分支固定为：

    cancel_scheduler_task(
        task: asyncio.Task[TaskResult | TaskRaised],
    ) -> None:
      -> task.cancel(_SCHEDULER_CLOSE_CANCEL)

    try:
        return await _execute_task(...)
    except asyncio.CancelledError as error:
        if error.args and error.args[0] is _SCHEDULER_CLOSE_CANCEL:
            raise
        return TaskRaised(executable.task, error)
    except Exception as error:
        return TaskRaised(executable.task, error)

三种来源在同一条消费链中机械区分：

1. node callable 自己抛出的 `CancelledError` 由 `_capture()` 变成 `TaskRaised`；session
   停止新的 activation、排空已启动任务并 close，随后按上面的 owner-local boundary 消费
   标记。该路径保留既有 `test_node_initiated_cancellation_waits_for_sibling_cleanup`
   行为，nested child 交给 parent，root release 后重抛；不调用 invocation abort，也不广播
   sibling。
2. waiter 取消 `session.next()` 时没有 node-origin 标记；`next()` 只执行
   `_close_after_cancellation()`（shield 到 `aclose()` 完成）并重抛 waiter 的
   `CancelledError`，不写 `_errors`。该异常到达 facade 后才进入 invocation cancellation
   unwind。
3. `session.aclose()` 对每个 live task 调用 `cancel_scheduler_task()`；带有私有
   `_SCHEDULER_CLOSE_CANCEL` 的 `CancelledError` 原样离开 `_capture()`，由
   `gather(..., return_exceptions=True)` 消费并清空 live/pending，不进入 event 或
   `_errors`。

因此 facade 的通用 cancellation 分支只处理未被 GraphRun owner-local 消费的 waiter
`CancelledError`，才执行 2.3 的递归 abort；node-origin 分支只做该 owner 的既有
close/release 后重抛，不进入 invocation unwind。ordinary `Exception` 仍按既有 session
error-drain/fence 规则处理，不向 sibling 广播。

### 2.3 construction、失败 unwind 和 release

#### 一个 setup frame

fresh root 和 continuation admission 使用同一 method-local 保护形状：

    root = None
    records = ()
    pending = ()
    cleanup_errors = []
    try:
        完成 root 和递归 child admission
        全部 handoff 成功后才把 root 和 opaque handles 交给 facade；每条 record 的
        owner-local export/release closure 仍留在同一个 invocation frame
    except BaseException as primary:
        await unwind_setup(root, records, pending, primary)
        raise

setup frame 结束前不返回 owner 集合或 child capability。`records` 只是一条
child-call record 序列：已确认的 record 由其 opaque handle 负责递归 cleanup，`pending` 只
包含尚未完成 handoff 的 candidate；已登记 sibling、当前 candidate、root 和 descendants
均由同一 frame 负责，不能遗漏。`cleanup_errors` 是该 frame 的 local list，
`record_cleanup_errors` 只写这一处，不能新增 hidden/global accumulator。

#### fresh root 的顺序

    facade 校验 caller-provided root coordinate、family 和 definition
      -> 建立 scope-capturing commit closure
         （单参数 GraphCommit；检查 scope、previous state、revision/token）
      -> project_start_graph_command() 校验该 coordinate
      -> 既有 commit_transition() exact 提交 StartGraphRun
      -> exact acknowledgement
      -> 创建 root GraphRun shell（自己的 state、scope 和 commit）
      -> 安装 root input frame
      -> 安装 GraphExecutor（唯一 claim owner）、session、自己的 child factory
      -> 递归执行 fresh child admission

root start/commit 失败时没有 confirmed root，直接传播原错误，不伪造 ABORTED。ack 后
的 frame、executor、session 或 factory 失败由 setup frame close、必要 fence、root
AbortGraphRun、release；cleanup error 不覆盖 primary。

#### continuation admission 的顺序

    continuation entry adapter exactly once
      -> 校验 evidence 非空、root-first、family/root/definition/lineage
      -> 通过校验后再读取 root evidence
      -> 按已有 ScopeRunCoordinate 分区 root/local frames
      -> _GraphRun.admit(root state, root frames)
      -> 按 binding canonical 顺序递归调用当前 child factory.admit_existing()
      -> 每个 exact child admission 后无 await 创建一条 child-call record
      -> 全部成功后才向 facade 返回

COMPLETED/ABORTED binding 只由入口适配转成 parent 可消费的 typed terminal evidence，不
重新 admission 为 live owner；RUNNING binding 才建立 live owner。child 1 已完成而 child 2
的 admission、frame、registration 或 handoff 失败时，setup frame child-first 清理 child 2
candidate、child 1 descendants、child 1 和 root，再销毁入口 evidence；facade 不会取得
半成品。

#### fresh/existing 共用的唯一 construction handoff

fresh 和 existing 只允许这一条 handoff，不另设 factory/result/provider/plan：

    construct_child(parent_scope_run, parent_activation, child_graph,
                    fresh_input | existing_binding, parent_frame,
                    family_identity, limits, raw_commit):
      -> 校验 parent activation、definition、family 和 parent scope
      -> fresh 才调用 child_scope_run_for_activation()；
         existing 直接使用已经校验的 binding.coordinate
      -> 建立 child scope 和单参数 scope-capturing commit closure
      -> fresh 执行 project_start_graph_command() 与 exact StartGraphRun；
         existing 先确认既有 state/frame admission
      -> exact acknowledgement 后建立 child GraphRun shell（自己的 state、scope 和 commit）
      -> 安装 local state/frame、GraphExecutor、session 和递归 child construction closure
      -> 将 child owner 的一次性 evidence closure 和 release closure 留在 opaque handle 的
         owner-local 实现中
      -> 一个无 await handoff：将 record 追加到 parent 的唯一 record 序列
      -> 只返回 opaque_handle 给 parent

parent 只能看到最后一个返回值；record 中的 closure 不能被 parent-facing API、
continuation 或 State 读取。`construct_child` 是 fresh 与 existing 的唯一 owner/factory
交接；失败前不追加 record，失败责任仍归当前 setup frame。

commit closure 只接受一个 GraphTransition，验证 scope、previous state、revision/token；
captured scope 由该 owner 自己验证，不新增 proof/binder 类型。取消只沿当前 parent→child
opaque handle 向下传播；child-origin 的 node cancellation 按既有 typed projection 返回，
不回调 parent 的 abort，也不形成递归 callback 环。

GraphRun 不复制 GraphExecutor 的 claim owner；每个 owner 只有一个
GraphExecutor/ExecutionClaimOwner。parent 看不到 child scope、state、frame、
factory、commit 或 owner-local closure。`ChildStateBinding`/frames 只由入口/出口适配读取；
它们不参加 handoff，不构成 live context，也不进入 child-call record。

#### live abort/release

不构造或保留 invocation coordinator，也不提供没有 root 来源的 release_all。facade 的
method-local closure 显式捕获当前 root、records 和必要的 owner-local closure：

    abort_invocation(cause):
      drain pending
      records 中仍 live 的 opaque handles 递归 child-first close -> 必要 fence -> 各自 AbortGraphRun
      root close -> 必要 fence -> root AbortGraphRun
      到此结束，不向上回调

    release_invocation():
      同一 child-first 顺序递归 release records 中仍 live 的 handles
      release root

root 是 closure 的显式参数/捕获值，不外置到共享对象。standalone 且无 child 时，只有
invocation-level cancellation 才执行 root close、必要 fence、AbortGraphRun 和 release；
node-origin cancellation 仍按 owner-local 分支处理。terminal record、setup-failure
candidate 不重复 abort；每个 owner 的 lifecycle/exact successor 操作幂等。cleanup 失败
追加到 local cleanup_errors 后继续；success 无 primary 时返回 child-first 首个 cleanup
error，ordinary error 保留 primary，invocation cancellation 按既有优先级返回 cleanup
error 或原始 CancelledError。node-origin cancellation 走 2.2 的 owner-local 分支，不调用
abort_invocation。

## 3. 文件责任和实施顺序

| 文件 | 唯一责任 |
| --- | --- |
| execution/family_driver.py | _GraphRun、owner-local state/frame、child-call records、child drive、parent settlement |
| execution/facade.py | root setup、result boundary、外部 cancellation、abort/release closure |
| execution/invocation.py | continuation 一次 admission、evidence 校验/分区、partial loop |
| execution/run_context.py | 仅保留 sealed continuation/frame transport adapter；删除 live context/mutation caller |
| execution/identity.py | factory-only child coordinate producer |
| execution/graph_run.py | 只校验 caller/factory 传入的 coordinate |
| execution/executor.py | 一个 owner 一个 executor/claim owner |
| execution/engine/frontier.py、superstep.py | 只产出 MissingChild/WaitingForChildren metadata；删除 PreparedNestedRun/StartMissingChildren consumer |
| execution/engine/scheduler.py、session.py | marker cancellation、node-origin 消费和既有 public shape |
| execution/result.py | typed projection/result（删除 child_state 字段）；WaitingForChildren 不越过 facade |
| state/graph_state/**、request.py、claim.py、errors.py | KEEP 既有 schema、ABI、taxonomy、exact semantics |

source 删除闭包固定为：`run_context.py` 的 `GraphRunContext`、四个 mutation 方法、
`_GraphContinuation.admit()` context 返回路径及其 callers；`result.py` 的 projection
`child_state` 字段；`frontier.py`/`superstep.py` 的 `PreparedNestedRun`、
`StartMissingChildren` direct consumers。不得以 alias 或第二条 live path 补回。

实施顺序：

1. 先删除 run_context 的 live GraphRunContext、四个 mutation 方法及 direct callers；让入口/出口
   adapter 直接消费既有 sealed continuation/frame evidence。
2. 在 family_driver 建立一个 _GraphRun，按 2.1 迁移 state/frame/claim/session，并让 parent
   只保留 child-call record。
3. 让 frontier/superstep 的 WaitingForChildren 由 drive loop 内部消费，移除
   PreparedNestedRun/StartMissingChildren 类型及其 direct consumer。
4. 接入 factory-only identity、fresh/existing admission 和 2.3 setup guard。
5. 接入 result boundary、scheduler/session cancellation 和 method-local abort/release。
6. 完成 source/test scan 后提交独立 implementation review；在 review 通过且用户明确
   授权前不改 production/test。

## 4. 测试、验收与门禁

除下表明确标记为 legacy 的旧路径测试外，现有测试的目的、case、数量、错误类型和覆盖
必须全部保留。与新 owner 边界冲突的 parent-side transition/token 断言只迁移到
owner-local 顺序；不得删除、改名、合并、减少任何非 legacy 测试，也不得增加 legacy、
compatibility、AST-only 或 helper-count 测试。

删除清单只限于已经删除的旧 live path：

| 旧测试 | 处理 | 原因 |
| --- | --- | --- |
| `tests/execution/test_frame_index_contract.py::test_run_context_rejects_access_or_replacement_before_child_start_acknowledgement` | 删除 | 唯一测试已删除的 GraphRunContext mutation API |
| `tests/execution/test_graph_api.py::test_facade_fails_closed_if_internal_preparation_requests_nested_coordination` | 删除 | 唯一测试已删除的 PreparedNestedRun/StartMissingChildren parent path |
| `tests/execution/engine/test_runtime_boundaries.py::test_child_wait_payloads_require_nonempty_canonical_parents` | 删除 | 只验证已删除的 PreparedNestedRun/StartMissingChildren 类型 |
| `tests/execution/test_graph_api.py::test_cancelled_run_quiesces_workers_retains_the_claim_and_recovers_from_authoritative_state` | 删除 | 旧的取消后保留 claim、下一次 invocation fence/recover contract |
| `tests/execution/test_executor.py::test_cancelled_session_retains_exact_lease_for_fence_and_reclaim` | 删除 | 旧的取消后保留 lease、下一次 invocation fence/reclaim contract |
| `tests/execution/test_resource_protocol.py::test_resource_session_close_is_quiescent_before_fence` | 删除 | 旧的取消后 resource close/fence contract |

以下不是 legacy，必须保留并只改测试夹具/断言的 owner 边界：

- `test_executor.py::test_node_initiated_cancellation_waits_for_sibling_cleanup`：node-origin
  `CancelledError` 仍由 session close/drain 后原样重抛；
- 所有 child success/failure/awaiting/resume、identity、frame、commit、resource、routing、
  partial-confirmation 和 result-boundary case：将 child state 构造改为 typed status/outcome/output，
  不删除行为覆盖；
- `test_graph_api.py` 中多 scope partial/continuation case：移除对
  `GraphRunContext.replace_state` 的 monkeypatch，改观察各自 GraphRun 的 confirmed commit，
  case 数量与失败优先级保持不变。

受影响的既有入口继续使用当前 manifest：tests/execution/test_executor.py、
tests/execution/driver.py、tests/execution/test_interrupt_flow.py、
tests/execution/test_graph_api.py、tests/execution/test_graph_facade_boundaries.py、
tests/execution/test_graph_recovery_contract.py、tests/execution/test_continuation_integrity.py、
tests/execution/test_frame_index_contract.py、tests/execution/test_result_boundary_contract.py、
tests/execution/test_identity_contract.py、tests/execution/engine/test_recovery_identity.py、
tests/execution/engine/test_recovery_boundaries.py、tests/execution/engine/test_runtime_boundaries.py、
tests/execution/engine/test_completion_projection.py、tests/execution/engine/test_output_projection.py、
tests/execution/engine/test_resume_input_contract.py、tests/execution/engine/test_admission.py、
tests/execution/engine/test_settlement.py、tests/execution/engine/test_planner.py、
tests/execution/engine/test_resume_admission.py、tests/execution/engine/test_session.py、
tests/execution/test_graph_public_typing.py、tests/execution/test_resource_protocol.py、
tests/state/graph_state/test_projection.py、tests/state/graph_state/test_identity.py、
tests/state/graph_state/test_state_validation.py、tests/state/graph_state/test_execution_transitions.py、
tests/typing_negative/invariant_continuation.py、tests/architecture/test_graph_execution_ownership.py、
tests/architecture/test_graph_typing_fixtures.py。

必须证明：child/parent state 隔离（parent projection 不含 child state）；不同 parent identity 的既有 child identity 注入性；
同一 parent 跨 superstep 不复用旧 owner；typed failure 不广播 sibling；awaiting/resume
保持 RUNNING；missing/runnable/awaiting/terminal precedence；CompletedChild 先 boundary
再 parent settlement；node cancellation、waiter cancellation、close marker 三者来源
可区分（node-origin 在 owner-local boundary 消费，waiter 才进入 invocation unwind）；外部
cancellation 的 pending drain → child/root abort → release；standalone 的 invocation
cancellation 仍 abort root；setup 各失败窗口不遗漏 owner、不伪造 ABORTED；child exact
commit 后 parent 失败保留 child facts；continuation 非空/root-first proof 和一次消费；
不出现两个 claim owner；handle/record closure 不进入 State/continuation/result/Store/registry；
已删除的 GraphRunContext mutation caller、PreparedNestedRun/StartMissingChildren direct
consumer 和 child_state-bearing projection consumer 均为零。

开始 production/test 修改前必须同时满足：

1. 下一次独立 implementation review 通过；
2. 用户明确授权开发；
3. source/test scan 与本文件一致，既有测试数量和覆盖不减少；
4. strict typing、Ruff、format、targeted behavior、coverage、build/package、适用
   pre-commit 和 Markdown 检查可复现；
5. State、public API、continuation/frame ABI、Store protocol 无变更；
6. complexity gate 记录为 USER-EXCLUDED / NOT RUN。

本轮只修改本实施文档；不运行源码/测试门禁，不修改需求文档、评审文档或源码。

## 5. 最小约束摘要

    state owner        = one private GraphRun per graph invocation
    parent view        = opaque handle + existing typed projection
    dynamic lifecycle  = one parent-local child-call record per activation; no second lifecycle collection
    new activation     = deterministic non-reused position
    waiting boundary   = drive consumes WaitingForChildren; facade receives GraphBoundary only
    construction       = one setup frame until every exact handoff succeeds
    cancellation       = child-first owner-local AbortGraphRun, then release
    release owner      = facade closure explicitly captures root
    commit             = existing single-argument GraphCommit with scope-capturing closure
    claim              = one GraphExecutor/ExecutionClaimOwner per owner
    post-child failure  = retain child facts and return existing error
    persistence/API    = keep exact
    tests              = retain all non-legacy cases; remove only listed legacy direct consumers
    authorization      = pending independent review and explicit user approval
