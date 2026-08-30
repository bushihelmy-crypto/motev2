# 父子图 GraphRun 本地 ownership 实施规范第八次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前版本从 1928 行压缩到 495 行后，ownership 的方向仍然正确，但同时删掉了上一轮
> 已冻结的若干必要闭包：family continuation 的导出、local frame/child boundary 交接、
> cancellation unwind、partial-commit 交接以及 factory 的 family identity/limits 绑定。
> 因此实现者仍必须自行选择 owner、caller 和失败事实，不能开始 production/test 编码。

本文件是 docs-only 独立评审；不修改 requirements、production、State、Store、protocol、
public API 或 tests，也不引入 persistence、failover、worker handoff、仅凭
`child_run_id` 的跨 invocation recovery、overlap gate、第二 runner 或新的 public type。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `7a0dcbd0b056483c63e8a0e198226c0606b400092a23289012b516ef3a55db73` |
| target 行数 | 495 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第七次独立评审](graph-independent-run-context-local-ownership-implementation-seventh-review.zh-CN.md) |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

评审口径仍是已确认的窄范围：每个 `GraphRun` 独占自己的 `run_id`、state、transition、
commit、session 和 live frame；parent 不拥有 child 的 authoritative state 或 child ID；
当前调用只使用 opaque wait/abort handle；child 先确认并投影，parent 再结算自己的 nested
node；调用级取消沿当前调用链 child-first，由各 owner 使用既有 `AbortGraphRun` 独立
提交；typed failure 和 ordinary node exception 不广播 sibling；State/reducer、
result/request、continuation/frame ABI 与既有 owner-local Store 语义保持不变。

## 2. 总体复核矩阵

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| 每个 `GraphRun` 独占 state/transition/commit/session | **方向通过** | §1.1、§2 的 ownership 意图清楚 |
| parent 不维护 child state/child ID | **意图通过** | §1.1、§1.3、§2 的禁止项与 requirements 一致 |
| child 先投影、parent 后 settlement | **方向通过** | §3、§4.1 的顺序正确 |
| child identity producer 边界 | **方向通过 / 验证未闭合** | producer 已移到 factory，但 foreign projection 的校验 owner 未定义 |
| continuation 与 local frame | **阻塞** | root-only context 没有 family snapshot/export 的可调用路径 |
| cancellation 与 handle lifecycle | **阻塞** | coordinator 没有可调用的 unwind/release contract |
| existing `_PartialCommitError` | **阻塞** | 现有 facade loop 仍依赖已删除的 parent-side context 操作 |
| factory、family identity、limits、executor | **阻塞** | 没有构造签名，且未处理当前 compiled owner 的 identity/共享 executor 事实 |
| waiting/result ABI 与 awaiting 聚合 | **阻塞** | 新旧 `WaitingForChildren` 形状和 public result 投影未形成唯一规则 |
| source/test manifest | **门禁未满足** | source scan 仍有未逐项分类的直接 consumer |

## 3. 阻塞项

### LO-IR44 — root/local context 无法生成原样 family continuation

**证据。** Target §2.1（第 125–136 行）同时规定：

- `_GraphContinuation.admit(...) -> GraphRunContext[T]` 与 `_context_from_continuation()` 保持不变；
- `GraphRunContext` 删除 `child_state()`、`state_at(child)`、`replace_state(child)` 和
  `replace_child()`，只拥有 root state/local view；
- continuation 的 `child_states`/family `ScopedFrameIndex` 只在 method-local adapter 中读取，
  然后“交给现有 `_continuation()`”。

但现有 `run_context.py:353-469` 的 `_GraphContinuation.admit()`、`GraphRunContext`、
`_snapshot()` 和 `_continuation()` 依赖同一 context 提供 `root_state + child_states + frames`。
当前 target 的 `_InvocationRunCoordinator`（§2，97–100 行）只保存 handle tuple，没有
`export_snapshot()`、root owner、child evidence 或任何 typed collector。于是：

1. child owner 的 local state/frame 无法进入既有 `_CompleteContinuationSnapshot`/
   `_RecoveredContinuationSnapshot`；
2. awaiting result 与 `_PartialCommitError` 无法携带完整、可配对的 child prefix；
3. 若让 root context 重新保存 child 状态，又直接恢复了被禁止的 family mutable mirror。

**必须修订。** 在不改 sealed payload、public API 或增加 durable protocol 的前提下，写出
一个唯一的 private typed 适配闭包：明确 continuation admission 返回的是既有 evidence 还是
local context、谁在 result/partial boundary 只读收集各 owner、如何一次性重建原 snapshot，
以及 export 失败时的既有错误。可恢复上一轮已接受的 method-local export 方案；不能只写
“交给现有 `_continuation()`”。

### LO-IR45 — local frame 分区与 child boundary 安装没有 owner/caller

Target §1.1 第 15 行声称每个 owner 有 local frame，§4.1 第 202–207 行却只写了“读取
projection、重新 prepare、settlement”，没有定义 child output 如何成为 parent 可用的
`ChildBoundaryAvailabilityCoordinate`。

现有 `engine/admission.py:54-84`、`engine/routing.py:127-175` 和
`engine/resume_input.py` 都从 `ScopedFrameIndex` 查 graph input/publication；父在重新
prepare 前必须拥有 child boundary，而 child 的普通 publication 必须仍归 child owner。
如果没有明确的分区/合并和一次性安装边界，就只能出现以下两种未授权实现：共享一个可变
family index，或在 parent 复制第二份 value truth。

**必须修订。** 用最小 private contract 写明：

- graph-input、publication、resume-input、child-boundary 各自归哪个 owner；
- local view 如何从既有 family evidence 纯分区，result/continuation 时如何 canonical merge；
- child terminal output 由谁验证、谁把 boundary 安装到 parent、duplicate/foreign/缺值各返回
  哪个既有 error；
- 安装成功/失败后各 owner 的 last-exact state 与 frame 保留位置。

这只是现有 immutable frame 的传输适配，不要求新增 frame 字段、Store、relay 或持久化协议。

### LO-IR46 — identity producer 移出 parent 后，child projection 的完整性校验丢失

Target §1.3（第 52–61 行）禁止 frontier/projector 调用 `child_graph_run_id()`，但没有指定
新的 projection validator。现有 `engine/frontier.py:65-84` 会校验：

```text
child.run_id == child_graph_run_id(parent.run_id, superstep, node_id)
child.parent == parent activation
child definition id/version == nested definition
```

而 §2 的 `ActiveChild`/`CompletedChild`/`AbortedChild` 仍是可构造的既有 dataclass；
`StepRequest.child_projections` 仍接收它们。若 parent 不再派生 ID，且 handle 只返回普通
projection，就没有地方拒绝 foreign、stale 或伪造 child state，`SnapshotMismatchError` /
`ResultCollectionError` 的既有负向行为也无法保持。

**必须修订。** 明确由 child factory/handle 在返回 projection 前完成哪些 identity、definition、
status、output descriptor 校验，或明确一个仍属于 child owner 的纯 validator；parent 只能
消费已验证 projection，不得新增 parent lookup、ID map 或 public seal。需要保留现有
foreign/duplicate/stale 的错误优先级和行为测试。

### LO-IR47 — factory construction 没有闭合 family identity、limits、commit 与 executor ownership

`_ChildScopeFactory.prepare_start()/admit_existing()`（§2，73–83 行）没有构造签名；
`_GraphRun` 也没有 `family_identity`、`limits`、owner-local commit binding 或 start/admit
的完整参数。这个缺口会与当前源码的两个事实冲突：

- `facade.py:546-562` 在一次 compile 中给 root 和每个 nested `Graph` 分配不同的
  `_CompiledFamilyIdentity`；若 nested owner 直接使用自己的 cached identity，continuation
  pairing 会失败，若改用 root identity，则必须写出显式传播规则；
- `invocation.py:169-178` 当前按 definition scope 建立并复用 `GraphExecutor`，而
  `GraphExecutor` 内含可变 `ExecutionClaimOwner`。target 又要求每个 `_GraphRun` 独占 executor，
  却没有说明如何删除这个共享 map。

同时，target 的 factory contract 没有 `ExecutionLimits` 参数/捕获规则；因此
`max_supersteps` 是每个 owner 还是 family 上限、`max_parallel_tasks` 如何沿 child 传播，
仍可被实现者任意解释。

**必须修订。** 在一个短表中冻结：一次 root invocation 分配一个 family identity 并显式传给
所有 nested owner；同一 `ExecutionLimits` 按现有 per-scope/session 语义传入；每个 owner
取得不可被 parent 直接调用的 commit capability；每个 `_GraphRun` 新建自己的
`GraphExecutor`/claim owner/session；`prepare_start` 与 `admit_existing` 的 graph、scope、
parent activation、input/frame 和失败返回必须有完整 nominal signature。不得引入新的 Store、
aggregate budget 或第二 scheduler。

### LO-IR48 — cancellation coordinator 没有真实的 unwind/release caller

Target §2 的 `_InvocationRunCoordinator` 只有 `handles` 字段（97–100 行），没有
`abort_all`、`close_all`、`release` 或 export operation；§3 第 157–179 行却要求 facade
停止新 activation、递归等待 child、finally 释放 handles。`_OpaqueChildCallHandle` 也没有
释放/关闭操作或可验证的 lifecycle state。

此外，§4.3（228–255 行）仍未固定：多个 sibling 的 deterministic abort 顺序和错误优先级、
grandchild 递归、start acknowledgement 竞争、`Graph.run()` 如何捕获 facade-level
`CancelledError`、node 自己抛出的 `CancelledError` 如何与 invocation cancellation 区分，
以及 shield 后哪个错误最终可见。现有 `engine/session.py:268-305` 已在 session 层捕获并
重抛 `CancelledError`；若 facade 再无规则地捕获，会产生重复 close/abort 或覆盖原错误。

**必须修订。** 给出从 `Graph.run()` 入口到 finally 的唯一调用图和最小操作签名：对所有
live child（含递归 descendant）执行 child-first quiesce → 必要时 owner-local fence →
`AbortGraphRun` → commit，再处理 parent；明确 terminal/candidate 的 no-op、所有 owner
的 commit error 传播、shield 范围、handle release 时点和 node-vs-invocation
`CancelledError` 分类。保持双边独立 commit，不新增 cancellation/persistence protocol，
也不产生 orphaned-claim recovery。

### LO-IR49 — existing `_PartialCommitError` loop 与 local owner 拆分没有交接点

Target §5.3（307–320 行）说“不新增 `handoff_confirmed_prefix()`，现有 facade recovery
loop 仍是唯一 caller”。但当前 `facade.py:683-730` 的 loop 直接调用
`context.state_at(scope)`、`context.replace_state(scope, confirmed)` 和
`_continuation(context)`；这些正是 target §2.1 要删除的 parent-side child operation。

如果保留 loop 原样，parent 又重新持有 child state；如果删除 loop，target 没有说明如何记录
exact-confirmed owner prefix、如何排除 callback-after-commit 的 unknown candidate，以及如何
把最新 root state + child evidence 放入既有 `_PartialCommitError`。这会直接破坏现有多 scope
recovery/partial tests。

**必须修订。** 明确 facade、invocation/recovery 和 owner/export adapter 的唯一 caller 图，
给出 method-local confirmed-cut 的 nominal 表示、调用次数、失败优先级和 root/child snapshot
来源。可以继续复用现有 `_PartialCommitError`，但不得以“existing loop”代替迁移闭包，也
不得新增 retry、receipt、rollback 或 failover。

### LO-IR50 — waiting/result ABI 与 awaiting 聚合没有唯一规则

Target §1.2 第 37 行称 `result/error types KEEP`，§2.2 第 140–152 行又把
`WaitingForChildren` 改成 `missing_children/active_children`，并在第 369 行要求删除
`PreparedNestedRun`/`StartMissingChildren` producer/consumer。需要明确这些类型是
execution-internal 可变更类型还是必须保持的 nominal contract；当前
`engine/superstep.py:68-72` 对 missing 优先于 active，且 `prepare_frontier()` 可能同时看见
两类 projection，新的“恰有一个 tuple 非空”规则没有给出规范化顺序。

同样，child 返回现有 `AwaitingResume` 后，root 如何聚合 nested failure/interrupt view 没有
定义。当前 `family_driver.py:493-515` 从 root 与全部 `child_states` 生成 public awaiting
result；root-only context 若没有 export/collector，会返回空或不完整的 failure/interrupt 集合。

**必须修订。** 明确 `WaitingForChildren` 的最终 private shape、missing/active precedence、
canonical ordering 和所有 direct consumers；写出 child awaiting 到 root `GraphBoundary` /
public result 的 typed projection/aggregation，保持现有 `Graph.Result`、error type 和
`RUNNING` 状态语义，不新增 public variant。

### LO-IR51 — source/test manifest 仍未完成逐项分类

Target §8 说未列出的文件要在实现前再扫描，但 requirements §9 要求 target 在编码前提供
完整 producer/consumer manifest。当前 source scan 仍找到未在 §8.1/§8.2 明确 KEEP/MODIFY
的直接 consumer，至少包括：

```text
tests/architecture/test_source_discipline.py
tests/execution/engine/test_routing.py
tests/state/graph_state/test_frontier_model.py
tests/state/graph_state/test_reducer.py
tests/state/graph_state/test_recovery_transitions.py
tests/state/graph_state/test_execution_resource_transitions.py
src/mote_kernel/execution/request.py
src/mote_kernel/state/graph_state/identity.py
src/mote_kernel/state/graph_state/validation.py
```

其中部分最终可以标为 KEEP；问题是必须逐文件说明为何不受 signature、projection、frame、
cancellation 或 existing AST/source-discipline contract 影响，并与最终 `rg`/consumer scan
一致。不能用 broad `state/**` 或“实现时再补”作为门禁证据，也不能删减、改名或弱化既有测试。

## 4. 不构成 blocker 的范围裁决

以下边界本轮确认无误，不应为了关闭上述问题而扩张：

- parent 不需要保存、查询或控制 child `run_id`；child identity inequality 只是 child owner
  的纯数学不变量，不是 parent lookup 或 overlap admission；
- 不新增 persistence/Store/checkpoint/terminal receipt、cross-invocation child-ID-only
  recovery、failover、worker handoff、global lock 或 overlap detector；
- 不新增 public `GraphRun`/handle、State field/status/command、第二 runner/scheduler 或
  compatibility alias；
- child/parent 继续分别使用既有 `AbortGraphRun` 独立 commit；typed failure 与 ordinary
  node exception 继续局部处理，不广播 sibling；
- 现有 family-shaped continuation/frame evidence 可以继续作为 immutable transient
  transport/validation evidence，但必须有明确的 local owner/export adapter。

这些是“把实现闭合”所需的本地 typed 说明，不是要求设计 child 跨 invocation 恢复或持久化。

## 5. 最小修订建议（不需要恢复 2000 行）

不必把历史长文全部恢复；在当前压缩文档后追加五个短表即可重新评审：

1. `_GraphRun`/factory 的构造签名（family identity、limits、commit、executor、scope）；
2. local frame partition/merge 与 child-boundary install 的 owner、caller、失败事实；
3. handle/coordinator 的 wait、recursive abort、release 和 `CancelledError` unwind；
4. continuation export 与现有 `_PartialCommitError` confirmed-prefix 的调用闭包；
5. waiting/result projection 和逐文件 source/test manifest。

只要这五个表能让实现者不再自行选择第二个 owner、第二份 frame truth 或新的恢复协议，
无需增加任何用户未授权的能力。

## 6. 验证记录

| 检查 | 结果 |
| --- | --- |
| target hash/行数 | `7a0dcbd0…` / 495 |
| production 对照 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10`，本轮无 production 修改 |
| baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed** |
| source scan | 发现 §8 未逐项分类的 consumer（LO-IR51） |
| requirements/State/Store/API/tests | 本轮未修改 |
| complexity gate | 按用户范围 **USER-EXCLUDED / NOT RUN**；不把它当作实施授权 |

## 7. 最终 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
parent authoritative child state/ID      = FORBIDDEN / PASS IN INTENT
child-first parent-settlement            = PASS IN DIRECTION
continuation export/admission            = LO-IR44 OPEN
local frame/child-boundary ownership     = LO-IR45 OPEN
projection identity validation            = LO-IR46 OPEN
factory family identity/limits/executor  = LO-IR47 OPEN
invocation cancellation/release          = LO-IR48 OPEN
confirmed-prefix partial handoff          = LO-IR49 OPEN
waiting/result/awaiting projection        = LO-IR50 OPEN
source/test manifest                      = LO-IR51 OPEN
persistence / Store protocol              = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover         = OUT OF SCOPE
cross-parent overlap                      = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                     = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests  = NO CHANGE IN THIS REVIEW
implementation authorization              = NOT GRANTED BY THIS REVIEW
```

本评审不否定“每个图的 `GraphRun` 自己负责自己的状态”这一目标；结论只是当前压缩稿还
没有把该原则落成可直接编码的唯一 contract。关闭 LO-IR44–51 后再做一次独立实现评审，
并由用户另行明确授权，才进入 production/test 修改。
