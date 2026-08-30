# 父子图 GraphRun 本地 ownership 实施规范第十一次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前 target 相比第十次又有实质收敛：递归 admission、唯一的 frame partition 签名、
> pending candidate、nested cancellation 分支、`Awaitable` wrapper、owner-plan 名称和
> source/test manifest 都已补写。方向与已确认的窄范围一致，但仍有六个会让实现者自行
> 选择 owner、调用点或错误语义的阻塞缺口，因此本轮不能通过编码门禁。

本文件是 docs-only 的独立评审记录。它不修改 requirements、production、State、reducer、
Store/persistence、protocol、public API、continuation/frame ABI 或 tests；不新增 persistence、
failover、worker handoff、仅凭 `child_run_id` 的跨 invocation recovery、overlap gate、
第二 runner 或 public handle，也不改变用户已经确认的范围授权边界。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `21f29f6f73d51276f6f839d0a75116c9535601e43ef31ee19da0e25737962925` |
| target 行数 | 1377 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第十次独立评审](graph-independent-run-context-local-ownership-implementation-tenth-review.zh-CN.md) |
| 上一轮评审 SHA256 | `dddaa6a97ad07ca08e2300810ef1a626d8c9c6cb4b56de6220ee6b72d55379e3` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

本轮只审阅上述 target 的字面可执行性，并以 requirements 中已冻结的边界为准：每个
`GraphRun` 独占自己的 state/transition/commit/session/frame；parent 不保存、查询或
控制 child `run_id`，只在当前调用栈使用 opaque wait/abort handle；child 先完成自己的
exact commit 和 projection，parent 再结算自己的 nested node；invocation cancellation
沿 live handle 向下传播，各 owner 独立使用既有 `AbortGraphRun`；typed failure 与普通
node exception 不广播 sibling。

## 2. 本轮总体判断

**比第十次明显更接近闭合，但尚未闭合。** 现在的问题已经不是 ownership 方向或需求
范围，而是递归交接、typed 输入、一次性 boundary 和异常竞争能否按文档直接实现。

| 维度 | 本轮结论 | 说明 |
| --- | --- | --- |
| 每个 `GraphRun` 独占 state/transition/commit/session/frame | **方向通过** | §1.1、§2.3A 的 owner/factory 规则清楚 |
| parent 不持有 child state/ID | **边界通过，证明仍依赖第 6 项** | opaque handle 的禁止项正确；provider/context 的可调用边界仍需闭合 |
| child projection 后 parent settlement | **已闭合（方向）** | §2.3B、§3、§4.1 都列出 install → re-prepare → claim/session/settle |
| factory identity/limits/executor | **基本闭合** | fresh/existing caller ledger 与 scope binding 已明确 |
| continuation recursive admission/export | **阻塞** | descendant closure 没有把 owner plan 和结果交回唯一 caller |
| local frame partition | **阻塞** | 伪代码使用未声明的 frame 输入，无法证明唯一来源 |
| cancellation unwind | **阻塞** | pending tuple 没有线性化语义，异常路径可能跳过全量 abort |
| waiting/awaiting/result projection | **阻塞** | projection 到 `AwaitingResume` 的 typed 聚合和全局 comparator 未冻结 |
| provider/context ownership | **阻塞** | callable provider/plan 与“parent 仅持有 handle”之间仍有可达边界冲突 |
| cancellation strict typing | **阻塞** | scheduler/session 的 `Exception`/`BaseException` 契约仍是条件句 |
| persistence/Store/failover/跨 invocation 恢复 | **范围外且未改变** | 不应借本轮修订扩张 |

## 3. 仍开放的六个阻塞项

### LO-IR52/LO-IR54（延续）——递归 admission 没有把 owner-plan 交到唯一调用点

Target §2 最小 contract（约 L112–L154）声明：

```text
_ChildCallAdmission
  handle
  export_provider
  _admit_descendants: _RecursiveAdmission

_RecursiveAdmission
  (branches: tuple[_AdmittedChildBranch, ...]) -> Awaitable[None]
```

但 `_InvocationAdmission`（约 L757–L762）又要求 facade 得到
`owner_plan_provider: () -> tuple[_OwnerCommitPlan, ...]`，而 partial loop（约 L824–L849）
必须逐个调用 `owner_plan.apply_exact()`。admission 伪代码（约 L261–L279）只规定：

1. 用当前 factory 调用 `admit_existing()`；
2. 调用 `_admit_descendants()`；
3. 注册 handle、附加 export provider；
4. 返回 `_InvocationAdmission`。

它没有一个 nominal operation 说明 descendant closure 如何产生、追加、返回
`_OwnerCommitPlan`，也没有说明 plan 属于哪一层 owner、何时物化、何时销毁。L283–L286
虽声称“把递归 plan closure 接到同一 owner-local plan provider”，但没有参数、返回值、
所有权或唯一 caller；`_RecursiveAdmission` 的 `Awaitable[None]` 不能承载该交接。于是
facade 仍无法在不取得 child owner/context/map 的前提下调用 child 自己的 fence/resume/
commit。这正是 requirements 所禁止的 parent child 状态提交权与当前 target 的 handoff
之间的缺口。

此外，partial 伪代码 L849 把 `owner_plan.owner_scope`（`ScopeRunCoordinate`）直接传给
既有 `_partial_commit_error`。当前源码 `src/mote_kernel/execution/result.py:64-69` 的
`failed_scope` nominal 类型是 `tuple[str, ...]`，`cause` 也仍是 `Exception`。target 没有
给出纯转换或既有签名的最终决定，严格类型实现会在这里停住。

**关闭条件：** 用一套唯一的 method-local、递归 typed handoff 同时服务 fresh、existing
和 partial 路径：明确每层 factory/owner 如何返回直接 child admission，如何把 descendants
的 plan/provider 按唯一顺序拼接到当前 owner closure，如何把最终 `owner_plan_provider`
交给 facade；plan 只能是一次性 adapter closure，不能进入 coordinator/context/State/Store。
同时明确 `ScopeRunCoordinate → failed_scope` 的既有类型转换（或在 normative owner 处统一
签名），并给出 callback-after-commit、non-exact successor 和 frame failure 的 last-exact
落点。不得以“plan 已存在”代替来源和生命周期。

### LO-IR53（延续）——`partition_family_frames()` 的输入生产仍未闭合

Target L503–L508 给出唯一 nominal 签名：

```text
partition_family_frames(
    graph, root_scope, root_state, owner_evidence
) -> tuple[_LocalFramePartition, ...]
```

但 continuation admission 伪代码 L253–L259 直接使用未声明的 `root_frames`、
`binding_frames` 构造 `owner_evidence`。文档没有说明这些 local frames 是由
`_context_from_continuation()` 返回、由 binding 携带，还是先由另一个 partition 操作生成；
如果它们来自 family-wide `ScopedFrameIndex`，又没有给出从 sealed evidence 得到每个 owner
输入的唯一纯步骤。这样会让实现者重新读取/复制 `GraphRunContext.child_states`，或私自增加
第二个 helper，二者都可能恢复 parent-side mutable mirror。

同一入口虽在 L798–L800、L864–L866、L938–L941 被重复引用，但 admission、normal result、
awaiting 和 partial 四条路径尚未共享一个完整的“evidence 生产 → partition → owner 安装”
契约；尤其没有规定 partition 失败时 owner 是否已创建、merge 是否允许在 admission 调用。

**关闭条件：** 保留一个 nominal signature，先定义 sealed continuation 到
`owner_evidence`/`local_frames` 的唯一 typed 生产步骤，再定义四条路径各自恰好一次的
partition/merge 调用和失败落点。不能使用未声明变量、overload、兼容包装器或第二套
partition helper；不能从 parent context 读写 child state。

### LO-IR55（延续）——ack/register 竞争和 pending cleanup 仍可能漏 abort

Target L599–L611 增加了 method-local `pending_candidates`，方向是正确的；但文档只写了
“exact acknowledgement 把 candidate 标为 confirmed，随后 register 成功才移除”，没有
规定以下更新的线性化点：

```text
acknowledge -> acknowledged=True -> pending tuple 替换 -> register(handle)
```

这些步骤之间可以有 `await`。外部 cancellation 若落在 tuple 替换和 `register()` 之间，
`drain_pending_candidates()` 与 `register()` 的先后关系、重复 cleanup、以及 register 成功
后是否仍把已 abort 的 handle 放入 coordinator 都没有 typed 状态机或不可抢占区保证。
“不需要 global lock”并不能替代当前 invocation 内的线性化规则。

另外，外层伪代码 L678–L701 先 `await drain_pending_candidates()`，再进入
`coordinator.abort_all()`。若 drain 本身抛出 cleanup error，控制流会跳过 abort_all；随后
finally 中的 `release_all()` 只覆盖已注册 handle，不能证明已确认 candidate 和 live child
都被 abort。文档虽写了错误优先级，却没有“记录错误但无条件继续 abort/release”的结构。

**关闭条件：** 给出不引入 global lock 的 invocation-local 线性化 contract（例如无 await
的登记状态转换或明确的 cancellation mask/state machine），定义 ack/register/drain 的每个
交错结果及 register 失败时的 owner-local close/fence/abort。任何 pending cleanup 错误都
必须被记录后仍保证 `abort_all()`/parent abort 的执行；最后再按既有 caller-visible 优先级
返回。每个 owner 至多一次 close/fence/`AbortGraphRun` exact commit。

### LO-IR50（残余）——awaiting projection 与 canonical order 没有一个可执行的 typed 聚合

Target L204–L211 把 handle 的返回值扩为 `ActiveChild`、`CompletedChild`、`AbortedChild`
和 `AwaitingResume`；但 `_ChildHandleSlot`（L151–L160）保存的是包含 `AwaitingResume` 的
projection union，而 `WaitingForChildren.active_children`（L341–L346）只允许
`tuple[ActiveChild, ...]`。L361–L367 只用 prose 描述“active 先 drive、无 runnable 才返回
awaiting”，没有一个 typed operation 把 slot tuple 转换为 waiting projection，也没有定义
多个 child awaiting 与 root/child `GraphFailureView`/`GraphInterruptView` 的去重、来源和
错误优先级。L891–L895 的 result adapter 直接说“从 transient frontier views 收集”，但
这些 views 的 owner/caller 和输入并未声明。

此外，target 同时使用 lexical/depth-first（L287、L300–L305）、slot tuple order（L315–L327）、
coordinate sort（L563–L566）、depth-first post-order（L624–L629）、
`(parent.run_id, superstep, node_id)`（L885–L889）和 scope/activation order（L891–L895）。
没有一个 shared nominal comparator 说明这些顺序在 admission、drive、export、awaiting、
partial 和 cleanup 中如何统一；同一 sibling 集合可能得到不同的 projection/错误首项。

**关闭条件：** 定义唯一的 private canonical-order comparator/type，并由 slot、waiting
projection、recursive export/plan、partial prefix、abort/release 共用；再定义
`AwaitingResume`/`ActiveChild` 到现有 `WaitingForChildren` 与 public result 的纯转换、
混合 awaiting/runnable sibling 的一次聚合和既有错误优先级。不得新增 public result variant、
awaiting record 或 child-ID map。

### LO-IR57（延续）——context/provider/plan 的 capability 边界仍靠文字而非类型闭合

Target L225–L236 说 `GraphRunContext` 中既有 `child_states` 可以被 adapter 一次读取，
同时 L300–L307、L757–L774、L783–L796 又把可调用的 `root_export_provider` 和
`owner_plan_provider` 通过 `_InvocationAdmission` 传到 facade。文档称 provider “不是
child capability”，但其 nominal 类型是无参数 callable，能够读取递归 owner evidence；
没有类型或唯一 caller 阻止 parent drive、coordinator 或 result path 之外的代码重复调用、
保存或将其与 plan 配对。`owner_plan` 更直接绑定 child 的 commit capability，若交接边界
不明确就会让 facade 看起来取得了 parent 不应拥有的 child 提交权。

同样，删除 `GraphRunContext` 的 mutation 方法并不自动使既有 `child_states` 字段成为
immutable/sealed evidence；target 没有给出 field 的 nominal sealed wrapper、一次性消费
位置及失败后销毁保证。当前文档对“parent 只持有 handle”和“facade 持有 provider/plan”
使用了两个不同的 owner 术语。

**关闭条件：** 明确 provider/plan 只由唯一的 invocation-local export/partial adapter
消费，并以不可复制的一次性 closure 或等价 private owner contract 交接；facade 只能调用
adapter，不直接持有 child capability，coordinator/parent drive 不得看到 provider/plan。
明确 `child_states`/frame evidence 的 sealed immutable 类型、一次读取点和销毁点。该修订
不得增加 child ID、lookup、registry、持久化或跨 invocation 资料；第十次已正确拆开的
identity producer/validator ledger 不需要重新扩张。

### LO-IR56（延续）——`Awaitable` wrapper 已补，但 scheduler/session 的严格异常类型仍未定稿

Target L632–L650 用 `_await_operation()` 包装任意 `Awaitable[None]` 后再传给
`asyncio.create_task()`，这一点已经消除了第十次指出的直接 `create_task(Awaitable)` 类型
冲突；wrapper 方向可以保留。

未闭合的是 L654–L676 的 node-vs-invocation cancellation contract：文档写成“若现有
scheduler event 的内部错误槽仅标注 `Exception`，只在该边界扩为 `BaseException`”。这仍
是条件句，没有最终 nominal 类型。当前源码中 `TaskRaised.error` 是 `Exception`
（`src/mote_kernel/execution/engine/scheduler.py:32-35`），`_capture()` 只捕获
`Exception`（:80-86），session `_record_error()` 也接收 `Exception`
（`src/mote_kernel/execution/engine/session.py:232-235`）。Python 3.11 的
`asyncio.CancelledError` 属于 `BaseException`；若不扩展/分类，node 自身取消会穿过当前
事件 union；若扩展，又必须定义 session error list、排序、drain 和 public projection 的
完整 typed 关系。

**关闭条件：** 明确最终的 private scheduler event/error union（包括 node-origin
`CancelledError` 的承载方式）、session 内部 error 容器类型和 facade 外部 cancellation 的
唯一识别点；给出 pyright 可接受的签名，不使用“若需要则……”的条件文本。保持 public
result/error 形状和 node-vs-invocation 传播优先级不变，并补齐对应的既有测试入口。

## 4. 已收敛且本轮不得重新打开的边界

- 每个图调用由自己的 private `_GraphRun` 负责 state、transition、commit、executor、
  session 和 local frame；parent 不维护 child authoritative state 副本。
- parent 不需要保存、查询或控制 child `run_id`；不同 parent identity 下 deterministic
  child ID 的不碰撞只是 child identity/State proof，不是 parent lookup 或 overlap gate。
- 同一 immutable `CompiledGraph` 被不同 parent 重叠复用仍是 caller precondition；本 change
  unit 不新增检测、乐观锁、全局 registry 或拒绝门。
- `CompletedChild → parent install boundary → re-prepare/materialize → claim/session/
  tokened settlement/ack` 的顺序正确；child exact commit 后 parent 失败不回滚、不重试、
  不 failover。
- `WaitingForChildren` 仍是 execution-internal metadata；State schema、command、public
  result、continuation/frame ABI 和 Store protocol 不新增字段/variant。
- invocation-level cancellation 才向当前 live handles 传播；typed child failure、ordinary
  node exception 不广播 sibling；父、child 各自复用既有 `AbortGraphRun`。
- 不新增 persistence/checkpoint、worker handoff、仅凭 `child_run_id` 的跨 invocation
  recovery、failover、第二 scheduler/runner 或 public `GraphRun`/handle。

## 5. 验证记录与处理范围

本轮完成的只读检查：

```text
target 行数       = 1377
target SHA256     = 21f29f6f73d51276f6f839d0a75116c9535601e43ef31ee19da0e25737962925
requirements SHA   = 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6
上一轮评审 SHA     = dddaa6a97ad07ca08e2300810ef1a626d8c9c6cb4b56de6220ee6b72d55379e3
```

本轮没有运行或修改 production、State、Store、API 或 tests；既有工作区中的用户修改未被
清理、重置或覆盖。`make check`、仓库级 pre-commit 和行为测试不作为 docs-only 评审的
通过证据；进入编码前仍须按 AGENTS.md 及 target §9 重新运行，并报告完整结果。

## 6. 最终裁决

```text
ownership direction                    = CORRECT
distance from tenth review             = SIGNIFICANTLY CLOSER
child-boundary installation order      = CLOSED IN DIRECTION
factory/identity/limits/executor       = SUBSTANTIALLY CLOSED
recursive admission + owner-plan       = LO-IR52/LO-IR54 OPEN
frame partition input/call contract    = LO-IR53 OPEN
cancellation registration/unwind       = LO-IR55 OPEN
awaiting projection/canonical order    = LO-IR50 RESIDUAL OPEN
context/provider capability boundary   = LO-IR57 OPEN
strict cancellation typing              = LO-IR56 OPEN
persistence/Store/failover/recovery    = OUT OF SCOPE / UNCHANGED
implementation target                  = CHANGES REQUESTED / NOT READY
production/State/Store/API/tests       = NO CHANGE IN THIS REVIEW
development authorization              = NOT GRANTED BY THIS DOCUMENT
```

因此，本轮结论是：**当前版本已经明显比上轮接近闭合，但六项仍未满足“实现者无需自行
补 owner/caller/type 语义”的编码门禁，不能开始编码。** 关闭上述六项后再做一次独立
implementation review；本文件不改变用户在窄范围内已有的开发授权，也不扩张未授权的
persistence、failover 或跨 invocation 恢复工作。
