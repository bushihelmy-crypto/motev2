# 父子图 GraphRun 本地 ownership 实施规范第四次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前 809 行 target 已吸收上一轮大部分方向性意见，尤其是 per-GraphRun owner、opaque
> handle、presence witness、cell replacement、child-first 双边 abort 和 family evidence
> 的 transient 定位。但本轮仍发现会迫使实现者自行选择 owner、恢复投影或错误语义的阻塞项，
> 因此不能开始 production/test 编码。

本文件是 docs-only 独立评审；不修改 requirements、production、State、Store、protocol、
public API 或 tests，也不引入 persistence、failover、worker handoff、child-ID-only 跨
invocation recovery、overlap gate、第二 runner 或新的 public type。

## 1. 评审对象与口径

| 对象 | 内容 |
| --- | --- |
| 受审 implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `ef51d1e9adc500880e07d8da0ef73bc7268db1924429c5e75043bec4d18ee564` |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

只按已冻结的窄范围审核：每个 `_GraphRun` 独占自己的 state/transition/commit/session/frame；
parent 不拥有 child authoritative state 或 child `run_id`；当前调用只用 opaque wait/abort
handle；child 先投影、parent 再结算自己的 nested node；调用级 cancellation/abort 按
child-first 顺序由各 owner 使用既有 `AbortGraphRun` 独立提交；typed failure/ordinary
exception 不广播 sibling；保留现有 State/reducer、result/request/frontier、continuation/frame
ABI 和 owner-local Store/read/commit；不新增 persistence、failover、ID-only recovery 或
overlap runtime gate。

## 2. 已通过的方向项

- `_GraphRun` 作为每个图调用的私有 state owner，parent 不再通过 `GraphRunContext` 替换
  child state；这与需求的 ownership 方向一致。
- handle 被收窄为 invocation-local、wait/project 或调用级 abort，且不进入 State、result、
  continuation 或可复用 coordinator 资料。
- `find_child_boundary() -> bool`、单一 `_ChildScopeCell._current` 和 immutable
  snapshot replacement 的总体方向正确。
- nested cancellation 的 child-first quiesce/fence/abort/commit 顺序及 sibling failure
  隔离规则已写出；没有借此增加跨图事务或新的 status/command。
- family-shaped `ChildStateBinding`/`ScopedFrameIndex` 被声明为 immutable transient evidence，
  没有把 child-ID-only recovery、持久化扩张或 failover 偷渡进 target。
- 新 ownership 测试路径被明确标为普通 typed/behavior test；既有 architecture AST test
  声明为 KEEP，未要求新增 source-shape gate。

这些通过项不等于 target 已可实施；以下 blocker 需要先闭合。

## 3. 阻塞项

### LO-IR12 — cancellation 规范与 standalone regression 互相冲突

requirements §4.4、§6 要求 invocation-level cancellation/abort 对 child 和 parent 都执行
quiesce（必要时 fence）→ 同一 `AbortGraphRun` → 各自 commit，并明确该路径不产生
orphaned-claim recovery。target §7.2 的 nested 流程符合这一点，但同一节随后又规定：

- 没有 nested child 的 standalone root 保持旧的 active-token/state-only recovery；
- §7.3、§11 继续把该回归列为保留项。

当前基线测试 `tests/execution/test_graph_api.py:2408-2451` 也明确断言取消后只提交
`StartGraphRun`/`ClaimGraphExecution`，保留 active execution，下一次调用先 fence。按
target 字面，读者无法判断 requirements 的双边 abort 是否只适用于 nested parent-child，
还是覆盖所有 `Graph.run()` invocation；两种解释对已有测试和 State 生命周期给出相反结果。

**必须修订：** 二选一并同步 normative text、acceptance 和 manifest：

1. 明确本 target 的双边 abort 仅适用于存在 live nested child 的 invocation，并在 target/
   requirements 中把 standalone root regression 明确标为本 change unit 外的既有 contract；或
2. 将 standalone root 也纳入双边 abort，更新相应 behavior test，删除 active-token
   orphaned-claim 作为取消路径的表述。

不能同时宣称“取消路径不产生 orphaned claim”和“standalone 取消保留 active token、下一次
再 fence”。

### LO-IR13 — child identity owner 与 KEEP 的 frontier 实现不一致

target §1.1、§3.3、§5.1 要求 child owner 在自身内部创建/校验 child identity，parent 不得
计算或保存 child `run_id`；但 §9.1 又把整个 `engine/**` 列为 KEEP。基线中
`src/mote_kernel/execution/engine/frontier.py:57-72` 在 parent 的 `prepare_frontier()` 内：

1. 调用 `child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)`；
2. 把该值写入 `PreparedNestedRun.command` 的 `StartGraphRun`；
3. 将带 ID 的 command 交给 parent-side `WaitingForChildren`。

这不是 `_GraphRun.start()` 的 child-owner 内部校验，而是当前 parent prepare 路径产生
child identity。`src/mote_kernel/execution/graph_run.py:18-32` 及 state validator 也参与
同一 identity contract，却没有出现在 target 的 MODIFY manifest。若保持这些文件不变，
“parent 不计算 child ID”只能靠未定义的语义例外解释；若要移走派生，就必须重新裁定
`PreparedNestedRun`/frontier 的 owner、ABI 和 manifest。

**必须修订：** 明确唯一 identity producer 的 module/调用边界。若保留现有 frontier 作为
immutable validator，需明确它不是 parent owner、只产生 method-local validation evidence，
并证明 parent 不保存/控制该 ID；若改为 child owner 生成，则把实际受影响文件和 typed
handoff 写进 manifest。不得用 parent child-ID map、随机替换或 overlap lock 解决。

### LO-IR14 — historical child projection 与 boundary presence 没有闭合

target 同时作出以下要求：

- §4.2 的 `find_child_boundary()` 只能返回 presence `bool`，不能返回 child state、frame 或
  coordinate；
- §7.1 的 `project_missing_child()` 在“无 handle 但已有 completed boundary”时固定抛
  `SnapshotMismatchError`；
- §8.4 的 historical-only admission 不创建 live owner/slot/executor；
- §8.3/§10.1 又要求现有 continuation round trip 和 completed/aborted child projection
  不变。

在新的 invocation 从已有 continuation 恢复时，child handle 按需求不应跨 invocation 存活，
而 historical-only scope 又没有 live slot。文档没有一个 typed 路径把已验证的 boundary
 evidence 投影成 parent 所需的 `CompletedChild`/`AbortedChild`；按字面要么错误拒绝合法
 continuation，要么迫使 parent 读取 child state/ID。

同一问题还出现在 §8.2 的 admission 流程：`_live_view_from_partition()` 被要求先调用
`add_child_boundary()` 再调用唯一 presence producer，但其签名只接收
`_ValidatedLocalPartition`，而该类型只列出 `owner_scope/state/parent_activation/local_view`，
没有 boundary candidate。若 `local_view.index` 已含既有 boundary，`add_child_boundary()`
会按当前 ABI 抛 duplicate；若不含 boundary，又没有合法的 method-local record 来源，且
文档禁止通过重建/删除 `ScopedFrameIndex` 来补齐。

**必须修订：** 明确 live completion 与 snapshot-admitted historical projection 的两条 typed
路径、各自的 owner/生命周期和错误优先级；补出 partition 的边界 record 输入/输出（允许
method-local immutable field，但不得改 sealed ABI），规定一个 record 只绑定一个 owner，并
证明多 child boundary、completed/aborted child 和 continuation round trip 都能在不暴露
child ID 的情况下完成。

### LO-IR15 — cell/finalizer 定义自相矛盾，且伪代码调用未声明 operation

target §3.2 明确说 cell 不得另存 sealed evidence 集合，但同一节的
`_ChildScopeCellSnapshot` 又把 `sealed_records` 作为 `_current` 的字段，并在 §3.2、§5.2、
§6.1 中要求由 cell 读取、加入、relay 和 export。需要明确“禁止另存”是禁止第二份集合，
还是连该字段也禁止；当前文字会让实现者选择不同的事实源。

此外，`claim_finalizer(anchor) -> None` 之后，`seal_and_remove`/`discard_once`/`release_historical_once`
如何确认同一 claim、谁构造 `_ScopeFinalizer`、谁持有 cell/sink/relay 均未形成完整 nominal
contract。§6.2 伪代码调用的 `close_and_quiesce()`、`fence_if_execution_active()`、
`owner.abort_invocation()`、`raise_first_error_if_any()` 不在 §3.1–§3.3 的 `_GraphRun`/
`_ScopeFinalizer` contract 中；其中 `abort_invocation` 还与 handle 的同名 operation 容易
被误实现为第二控制入口。`_accept_relay_batch()` 只写“既有 snapshot/result error”，没有
固定具体错误类型或 source/destination failure 后的 caller-visible 结果。

**必须修订：** 选择一个唯一的 cell record 类型和 `_current` replacement 机制，写清
provisional、published、historical、claim、sealed 五种 source 的构造/消费者；要么把全部
cleanup 封装进一个完整 typed `finish`，要么逐项声明所需 operation、返回值和错误。补齐
self-sink、relay、seal 失败时的保留/清理/不可重试结果，不新增 tombstone、consumed 集合、
持久化 receipt 或隐藏第二 cell。

### LO-IR16 — parent nested settlement 流程跳过了现有 execution lease

target §7.1 的唯一流程是：

```text
child projection -> install_child_boundary -> parent exact SettleGraphNode -> routing
```

但现有 reducer 要求 `SettleGraphNode` 必须携带 parent 的 active execution lease；见
`src/mote_kernel/state/graph_state/execution_transitions.py:150-154`。当前实现的合法路径是
parent `prepare` → `ClaimGraphExecution` → executor/session 产生 nested `TaskResult` →
`SettleGraphNode` exact commit（并按 session acknowledgement 更新 state）。target 没有说明
新 `_GraphRun.drive_quantum()` 是否保留这条路径，也没有定义 parent claim/session/settlement
失败时的 state/frame/handle 结果。

**必须修订：** 在不修改 `SettleGraphNode` ABI 的前提下，把 parent nested settlement 的
claim、session、token、acknowledgement、boundary install 与 routing 顺序写成可执行 typed
流程；明确 child transition 已确认而 parent settlement 失败时，child evidence 保留、parent
active token 如何按既有 owner-local 规则处理，且不得直接构造无 lease 的 settlement。

### LO-IR17 — post-confirm / sink-relay 失败矩阵仍不完整

§7.3 的矩阵已经覆盖 start、部分 publication、slot publication、sibling start 和 parent
settlement 的若干窗口，但没有裁决下列已由 §5.2/§6.1 引入的窗口：

- self sink 写入失败、source relay 失败或 destination `_accept_relay_batch()` 失败；
- 已确认 `ClaimGraphExecution`、`FenceGraphExecution`、`SettleGraphNode` 或 resume commit
  callback 抛错/返回 non-exact successor；
- parent settlement 后 publication/routing frame 安装失败；
- child transition 后 session 关闭但 `state.execution` 仍存在时，是否 fence、abort、保留
  evidence 或丢弃 owner；
- export-time merge/output projection 失败后 continuation 是否可返回、哪些 records 保留。

例如当前 session 在 completion commit 后若 frame publication 抛错，会关闭 session，但
active token 可能仍在已确认 state 中；target 同时要求“不 retry/不 failover”和“sealed
evidence 保留”，却没有给出下一入口或固定 error precedence。没有这些裁决，`finish(signal)`
可能留下不可驱动的 owner，或错误地把 `_PartialCommitError` 用在 ordinary window。

**必须修订：** 扩展逐窗口矩阵，至少固定 state/frame/handle/evidence 的保留或清理、既有
错误类型与优先级、当前 coordinator 是否终止、下一次允许的唯一入口；明确 ordinary
window 不伪造 partial handoff，且不新增 retry/receipt/failover。

### LO-IR18 — result/partition 的 exact typed 输出仍有缺口

target §8.3 声明 `_InvocationRunCoordinator.export_snapshot() -> ContinuationSnapshot[T]`，
但现有 public result 需要 sealed `_GraphContinuation[T]`，文档没有规定唯一的 wrapper
producer 及其调用时机；同时 `_GraphRun` 的 `start/admit/confirm`、`_ChildScopeCell` 的
record 参数和 `_RunLocalView` 的 replacement 虽称“nominal”，多个参数仍无类型/返回错误
契约。`ParentGraphActivation` 也没有 `scope_run` 字段，而 §4.1/§8.4 直接写
`parent_activation.scope_run`。

这不是要求新增 public type，而是需要让 existing continuation/result ABI 有唯一、可编译的
封装路径，并消除把 `ParentGraphActivation` 与 `StableActivation` 混用的歧义。

**必须修订：** 明确 `snapshot -> _GraphContinuation` 的唯一 private producer（或将 export
签名改为现有 sealed type），补齐关键 private operation 的 exact typed 参数/异常/生命周期，
并将 boundary owner 表述改为现有类型实际拥有的 scope 来源。

## 4. 不应借修订扩大的范围

关闭上述 blocker 只需补齐 owner、typed handoff、历史 projection、cleanup 和错误矩阵，
不授权或要求：

- 新 State 字段/status/command、public `GraphRun`/handle、第二 scheduler/runner；
- 新 persistence/checkpoint/journal/receipt、跨 invocation child-ID load/recovery、
  failover、worker handoff 或 durable cancellation protocol；
- global registry、optimistic/persistent lock、overlap detector/rejection gate；
- 修改 `ChildProjection`、`StepRequest`、`ScopedFrameIndex`、continuation sealed payload
  的 ABI；
- legacy/compatibility/AST-only/private-helper-count test。

## 5. 验证记录

| 检查 | 结果 |
| --- | --- |
| target / requirements hash | 与第 1 节一致；target `ef51d1e9…`，requirements `1ff31e95…` |
| source direct-consumer / identity scan | 确认 `engine/frontier.py:65-72` 仍由 parent prepare 派生 child ID；确认当前 `GraphRunContext` 仍含 `child_states`，属于待实施基线 |
| manifest existence | target 列出的 9 个既有测试文件存在；`tests/execution/test_graph_run_ownership.py` 尚不存在，按 target 属于计划新增文件 |
| targeted baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed** |
| whitespace | `git diff --check` 与 target no-index 检查无诊断 |
| complexity gate | 按用户范围 **`USER-EXCLUDED / NOT RUN`**；不宣称完整 `make check` 通过 |
| production/State/Store/API/tests changes | 本轮无修改；仅新增本评审文档 |

## 6. 最终 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
parent authoritative child state         = FORBIDDEN / PASS IN INTENT
parent child run_id                      = FORBIDDEN / LO-IR13 OWNER BOUNDARY OPEN
opaque invocation handle                 = ALLOWED / TRANSIENT / WAIT-OR-ABORT ONLY
boundary presence/historical projection  = LO-IR14 OPEN
cell/finalizer one-shot lifecycle        = LO-IR15 OPEN
parent settlement lease path             = LO-IR16 OPEN
post-confirm/sink-relay errors           = LO-IR17 OPEN
continuation exact wrapper/types         = LO-IR18 OPEN
cancellation scope                       = LO-IR12 OPEN
typed/ordinary failure                   = LOCAL / NO SIBLING BROADCAST
continuation/frame ABI                   = KEEP EXACT / EVIDENCE BOUNDARY NEEDS CLOSURE
persistence / Store                      = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover        = OUT OF SCOPE
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
implementation authorization              = NOT GRANTED BY THIS REVIEW
```

请先按第 3 节回写 target，重新计算 hash，并再次独立审核；在此之前不开始编码。
