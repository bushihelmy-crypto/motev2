# 父子图 GraphRun 本地 ownership 实施规范第九次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前实施稿已经明显收敛：每个 `GraphRun` 的本地 state/transition/commit/session/frame、
> opaque child handle、child-first cancellation、既有 continuation/Store/API 边界都已经写出
> 明确方向；但 continuation export 的实际调用者、child boundary 的调用顺序、identity
> producer 的现有 caller 清理、`CancelledError` 分类和 partial-commit 路径仍有可执行性或
> 规范自相矛盾。实现者仍需自行选择 owner、caller 或错误优先级，因此本轮不能通过编码门禁。

本文件是第九次独立评审，也是本轮最后一次评审记录。它只记录评审，不修改 requirements、
production、State、Store、protocol、public API、continuation/frame ABI 或 tests；完成本文件后
停止，不继续修订实施稿、不开始编码、不发起第十次评审。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `9625a1d73826ed94729d05e1d7520088ad175c4421f981e729bb060643e7eb6d` |
| target 行数 | 853 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第八次独立评审](graph-independent-run-context-local-ownership-implementation-eighth-review.zh-CN.md) |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

评审继续采用已经对齐的窄范围：父图不维护 child 的 authoritative state 副本，也不保存、
查询或控制 child `run_id`；每个 owner 只在当前 invocation 内使用 opaque handle；child
先 exact commit/投影，parent 再结算自己的 nested node；invocation-level cancellation 向下
传播并由各 owner 独立使用既有 `AbortGraphRun`；typed failure 和 ordinary node exception
不广播 sibling。

以下事项仍明确不属于本 change unit：新增 persistence/Store/checkpoint、failover、worker
handoff、仅凭 `child_run_id` 的跨 invocation 恢复、overlap lock/gate、跨 owner 事务回滚、
第二 runner/scheduler、public `GraphRun`/handle 和新的 State/result/protocol variant。

## 2. 总体复核

| 维度 | 本轮结论 | 说明 |
| --- | --- | --- |
| 每个 `GraphRun` 独占 state/transition/commit/session/frame | **方向通过** | §1.1、§2.3A 已把 owner-local 目标写清楚 |
| parent/coordinator 不持有 child state 或 child ID | **意图通过** | opaque handle 和 transient evidence 的禁止项与 requirements 一致 |
| child projection 后再进行 parent settlement | **方向通过** | §3、§4.1 的主顺序正确，但 boundary install 缺一跳 |
| factory、family identity、limits、fresh executor | **大部关闭** | §2.3A 已补签名和绑定规则，仍需消除重复参数的权威性歧义 |
| waiting shape 与 missing 优先级 | **大部关闭** | §2.2、§2.3E 已给 private shape 和排序；awaiting 聚合依赖未闭合的 export caller |
| local frame/child boundary | **阻塞** | 定义了 operation，但主调用图没有调用它 |
| continuation export/admission | **阻塞** | provider 的取得路径和 admission 适配路径仍不存在 |
| identity producer 边界 | **阻塞** | target 的唯一 caller 规则与现有 recovery/projector caller 冲突 |
| invocation cancellation/unwind | **阻塞** | facade 分类与当前 scheduler/session 的 `CancelledError` 行为不匹配 |
| existing `_PartialCommitError` | **阻塞** | §5.3 仍保留已被 §2.1/§2.3D 删除的 parent-side 操作 |
| source/test manifest | **阻塞** | source scan 仍有直接 consumer 未逐项冻结 |

因此，本轮不是“方案不可行”，而是“压缩稿还没有达到实现者无需自行补语义”的程度。

## 3. 仍开放的阻塞项

### LO-IR44 — continuation export/admission 没有可达的唯一 caller

Target §2.3D（约第 396–435 行）定义了：

```text
_ContinuationExportAdapter.collect(
    root: _GraphRun[T],
    owner_exports: tuple[_OwnerExport[T], ...],
) -> _GraphContinuation[T]
```

同时又规定每个 owner 的 `_OwnerExport` provider 不挂在 handle 的可见接口上，也不经过
coordinator；coordinator 只能保存 `handles`。这留下一个实际的断点：

1. construction frame 创建的 provider 没有返回值、字段或 method-local tuple 的交接点；
   `collect()` 因而无法取得递归 child/grandchild 的 `owner_exports`。
2. 当前 `_GraphContinuation.admit()`（`execution/run_context.py:353-369`）仍直接把
   `snapshot.child_states` 放进 `GraphRunContext`；当前 `_snapshot()`/
   `_continuation()`（`run_context.py:452-469`）也仍要求 root context 直接提供整个 family
   的 child states/frames。
3. 当前 `Graph.run()` 的结果路径（`facade.py:731-738`）只调用既有
   `project_graph_result(graph, context, disposition)`，没有把 root owner、递归 provider
   或一次性 export context 交给 adapter。
4. `_context_from_continuation()` 的签名没有 adapter/owner admission 参数；“再把 evidence
   交回同一 adapter”没有对应的可调用接口或顺序。

这不是要求 parent 保存 child state 的理由。requirements 允许 sealed continuation 中的
`ChildStateBinding`/`ScopedFrameIndex` 作为 immutable、transient transport evidence；缺的是
“evidence 如何在 boundary 被一次读取、按 owner admission、再按既有 snapshot 形状导出”的
纯调用闭包。

**关闭条件。** 在不引入 map、registry、durable record 或 public handle 的前提下，target
必须给出唯一的 private typed 调用图，至少说明：

- root 和每层 owner 的 export provider 如何以 method-local 方式被 result/awaiting/partial
  boundary 收集，且递归顺序唯一；
- `_context_from_continuation()` 如何先完成 family/root proof，再把每个 immutable evidence
  交给对应 child factory，不能直接恢复 parent-side mutation；
- root/local context 的一次性组装值如何填入既有 `_CompleteContinuationSnapshot`/
  `_RecoveredContinuationSnapshot`，以及 export/merge/wrapper 失败的既有错误优先级；
- owner provider 不可用、重复、foreign 或 stale 时由谁拒绝。

### LO-IR45 — child boundary 定义了安装操作，但主调用图漏掉安装时点

Target §2.3B（约第 281–336 行）已经正确规定 `install_child_boundary()` 由 parent owner 唯一
调用，并规定 child output 仍归 child owner、parent 只安装 immutable boundary evidence。但
§3 的唯一调用图（约第 487–503 行）在“child projection 完成”后直接写成：

```text
parent 重新 prepare
-> claim
```

§4.1 的第 4–5 步也只有“读取 projection、重新 prepare、claim/session/settlement”，没有
`install_child_boundary(parent, activation, completed_child)`。按字面执行时，parent 的下一次
`materialize_node_input()` 可能还没有 `ChildBoundaryAvailabilityCoordinate`，而 boundary
安装又可能被实现者放到 claim 之后，违反 waiting-before-claim 和 frame ownership。

**关闭条件。** 在 §3、§4.1 和 acceptance 中都固定同一顺序：

```text
CompletedChild
-> parent 验证 projection
-> parent install_child_boundary()（一次）
-> parent 重新 prepare/materialize
-> ClaimGraphExecution
-> session -> tokened settlement -> acknowledgement
```

同时明确安装失败时 parent local view 保持旧值、child last-exact facts 保留，并沿 target §2.3B
已经列出的 `SnapshotMismatchError`/value/publication/`FrameInstallationInvariantError` 返回；
不得把 boundary 安装变成 parent 对 child state 的写入。

### LO-IR46 — identity 的“唯一 runtime caller”与现有 recovery/projector caller 冲突

Target §1.3、§2.3A 和 §3 规定 `child_scope_run_for_activation()` 的唯一 runtime caller 是
`_ChildScopeFactory.prepare_start()`，并规定 recovery 只做 proof/planning。当前源码仍有：

```text
execution/engine/recovery.py:647
execution/engine/recovery.py:696
execution/invocation.py:238,273,329,587
execution/engine/frontier.py:65-84
execution/graph_run.py:23
```

其中 recovery 直接派生 coordinate，frontier 直接派生 child ID 并构造
`StartGraphRun`，projector 也直接调用 `child_graph_run_id()`。这些文件虽列为 MODIFY，但
target 没有闭合它们如何迁移到 factory，也没有说明 existing path 的 parent activation 与
`binding.coordinate` 由哪个纯 validator 建立 exact 关系。

另外，§2 的 `admit_existing(parent_scope_run, binding, local_frames)` 与 §2.3A 的
`admit_existing(parent_scope_run, parent_activation, child_graph, binding, local_frames)`
重复定义了签名；factory `__init__` 又捕获一个 `parent_scope_run`。实现者可以误用不同的
参数作为 parent scope，导致 foreign/stale binding 在 owner 创建后才失败。

**关闭条件。** target 需要固定一条权威路径：

```text
fresh:  parent activation proof -> prepare_start() -> 唯一 identity producer
existing: binding/activation/definition proof -> admit_existing(binding.coordinate)
frontier/recovery: 只返回 metadata 或已 proof 的 binding/input
projector: 只验证调用者传入的 coordinate/parent/definition，不再派生 identity
```

并逐项说明现有 recovery/frontier/projector caller 的删除或改为纯 proof；factory field 与
method parameter 只能有一个权威 parent scope。不同 parent `run_id` 下 child identity 必须
继续满足既有确定性不碰撞不变量，但不因此新增 parent lookup、overlap gate 或持久化。

### LO-IR48 — `CancelledError` 分类和 cancellation-safe unwind 尚未与现有 engine 对齐

Target §2.3C（约第 338–394 行）要求 facade 区分外部取消 `Graph.run()` task 与 node callable
自己抛出的 `CancelledError`，并用 `shield(coordinator.abort_all(...))` 做 child-first unwind。
当前源码存在两个直接冲突：

1. Python 3.11 中 `asyncio.CancelledError` 是 `BaseException`；
   `engine/scheduler.py:80-87` 的 `_capture()` 只捕获 `Exception`，所以 node callable 抛出的
   `CancelledError` 会从 task `handle.result()`（`scheduler.py:130-148`）逃出，而不是形成
   session-owned 的普通 node 事件。
2. `engine/session.py:303-305` 对从 `next()` 逃出的 `CancelledError` 统一执行
   `_close_after_cancellation()`。facade 无法仅凭当前边界判断它来自外部 task 还是 node；若
   直接按 §2.3C 广播给所有 child，会改变既有 node cancellation 行为。

此外，已经取消的外层 task 仅 `await shield(coro)` 不保证 cleanup 被完整等待；现有
`session.py:257-266` 使用独立 `create_task`、循环 shield 和最终 `result()` 才能完成 close。
Target 没有给 coordinator/handle 的 registration/add path（只声明 `handles` tuple），也没有
定义 `INVOCATION_CANCEL` 的 nominal 所属、“class-2 nested invocation”、递归 descendant
遍历以及 cleanup error 与原始 cancellation 的最终优先级。

**关闭条件。** 在不改变 public cancellation 形状、不新增 cancellation protocol 的前提下，
target 必须指定：

- node `CancelledError` 的分类 owner（若需改 scheduler，必须把该文件加入 manifest；若不改，
  必须给出 session boundary 的可验证分类）；
- 外部 facade cancellation 的唯一识别点、`INVOCATION_CANCEL` 的类型/来源和 explicit abort
  的同一 signal 入口；
- exact acknowledgement 前后的 handle registration、递归 child-first 顺序、terminal/candidate
  的 no-op；
- 使用 cancellation-safe join（等价于 session 的 task+shield loop），确保每个 owner 至多一次
  close/fence/`AbortGraphRun` commit；已有 caller-visible error 优先于 cleanup error，且
  standalone root 行为不变。

“向下传播”仍只表示当前 invocation 的 live handles；不允许按 child ID 查找或跨 invocation
广播。

### LO-IR49 — §5.3 仍与 §2.1/§2.3D 自相矛盾

Target §2.3D（约第 437–456 行）已经写明 partial path 不调用
`context.state_at(child)`/`replace_state(child)`，使用 method-local `_ConfirmedPrefix` 和
`adapter.collect()`。但 §5.3（约第 636–651 行）仍写成：

```text
existing loop 取 root 的 last-exact state
-> existing _continuation(root context)
-> existing _partial_commit_error(...)
```

这与当前 `facade.py:682-730` 的实际调用完全一致：它仍对每个 scope 调用
`context.state_at()`、`context.replace_state()` 和 `_continuation(context)`。如果按 §5.3 保留
现状，root context 会重新成为 child-state mutable mirror；如果按 §2.3D 删除现状，§5.3
又没有说明实现者如何得到 root last-exact state、child evidence 和 failed scope。

**关闭条件。** §5.3 必须改为与 §2.3D 同一套唯一流程，并明确：

- fence/resume 的 canonical owner 顺序和每次 exact acknowledgement 后追加的 evidence；
- callback 在 commit 后抛错、non-exact successor、frame installation 失败时，当前 candidate
  是否排除；
- root last-exact state、child `_OwnerExport` 和 merged frames 的实际来源；
- `adapter.collect()` 只调用一次的边界，以及何时构造既有 `_PartialCommitError`、何时直接
  传播更早的 commit/callback/export error。

不得借此加入 handoff receipt、retry、rollback、failover 或 child-ID lookup。

### LO-IR51 — source manifest 仍缺少直接 consumer

第八次 source scan 后，target §8 已补列多项 KEEP/MODIFY 文件，但仍没有逐项列出：

```text
src/mote_kernel/state/graph_state/__init__.py
```

该文件直接 re-export `child_graph_run_id`（第 60、149 行），应明确标为 `KEEP`，并说明只
保留既有导出，不增加 runtime owner caller。另一个由 LO-IR48 引出的条件性 consumer 是
`src/mote_kernel/execution/engine/scheduler.py`：若为实现 node-vs-invocation cancellation
而修改它，必须在 production manifest 和 test manifest 中逐项加入；若保持不变，target 必须
写出为何现有行为已经满足分类要求（当前源码证据并不支持该结论）。

**关闭条件。** 先完成一次最终 `rg` producer/consumer scan，把上述文件和任何新发现的
直接 consumer 逐文件归类为 KEEP/MODIFY 及允许的变更；不以 `state/**`、“实现时再补”或
alias 代替清单。所有既有测试保持数量、命名、断言和错误分类不减弱。

## 4. 已明显收敛、但需保持的裁决

以下不是要求恢复历史长文，也不是新的范围：

- §2.3A 对 family identity、不可变 `ExecutionLimits`、owner-local executor/claim/commit
  capability 的方向已正确；只需消除参数权威性和 root construction caller 的歧义。
- §2.3B 对四类 frame 的 canonical owner、一次性 boundary install 和失败事实的方向已正确；
  只需把 install 写进主调用图。
- §2.3E 对 `WaitingForChildren(missing_children, active_children)`、missing 优先、canonical
  order 和不改 public result ABI 的方向已正确；不要把 private metadata 变成 public variant。
- 父不持有 child `run_id`/state、同一 immutable compiled child 在不同 parent identity 下的
  child ID 不碰撞，仍是 requirements 的边界；不应为关闭评审而新增 overlap lock 或 parent
  lookup。
- child exact commit 后 parent 后续失败时保留 child facts、不回滚、不 retry、不 failover，
  以及 typed/ordinary failure 不广播 sibling，均与已对齐原则一致。
- 本 change unit 继续不做 persistence、Store/checkpoint、worker handoff 或仅凭
  `child_run_id` 的跨 invocation 恢复；这些不是本轮阻塞项。

## 5. 验证记录

| 检查 | 结果 |
| --- | --- |
| target hash/行数 | `9625a1d7…` / 853 |
| production 对照 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10`；本轮无 production 修改 |
| baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed in 1.76s** |
| source scan | 发现 `state/graph_state/__init__.py` 未逐项列入；并确认 identity、partial、scheduler 的上述调用闭包问题 |
| requirements/State/Store/API/tests | 本轮未修改 |
| complexity gate | 按用户范围 **`USER-EXCLUDED / NOT RUN`**；不将其伪写成通过 |

## 6. 第九次评审 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
parent authoritative child state/ID      = FORBIDDEN / PASS IN INTENT
factory identity/limits/executor         = MOSTLY CLOSED; authority clarification open
local frame/child-boundary ownership     = LO-IR45 OPEN
continuation export/admission            = LO-IR44 OPEN
identity producer/recovery callers       = LO-IR46 OPEN
invocation cancellation/unwind           = LO-IR48 OPEN
confirmed-prefix partial path            = LO-IR49 OPEN
waiting/result private shape             = MOSTLY CLOSED; depends on export/cancel closure
source/test manifest                     = LO-IR51 OPEN
persistence / Store protocol             = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover        = OUT OF SCOPE
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
authorization                            = NOT GRANTED BY THIS REVIEW
review count                              = NINTH / STOP AFTER THIS REVIEW
```

本结论只说明当前实施稿还没有形成可直接编码的唯一 contract，不否定“每个图的
`GraphRun` 自己负责自己的状态”这一目标。第九次评审文档完成后，本轮工作停止。
