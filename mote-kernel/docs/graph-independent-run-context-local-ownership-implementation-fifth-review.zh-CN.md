# 父子图 GraphRun 本地 ownership 实施规范第五次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮 target 已实质补齐上一轮的 standalone cancellation 区分、historical projection、
> cell replacement、失败矩阵和 continuation wrapper；但仍有几处会让实现者在现有
> engine/session 契约之外自行发明路径，且 identity 文字直接越过已冻结 requirements。
> 因此不能开始 production/test 编码。

本文件是 docs-only 独立评审；不修改 requirements、production、State、Store、protocol、
public API 或 tests，也不引入 persistence、failover、worker handoff、child-ID-only 跨
invocation recovery、overlap gate、第二 runner 或新的 public type。

## 1. 评审对象与口径

| 对象 | 内容 |
| --- | --- |
| 受审 implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `7f44f4da3914391cf28b1bccfd082e2f3f199da966b0b96594ac37f153e493aa` |
| target 行数 | 1093 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮独立评审 | [第四次独立评审](graph-independent-run-context-local-ownership-implementation-fourth-review.zh-CN.md) |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

本轮只按已冻结的窄范围审核：每个 `_GraphRun` 独占自己的 state/transition/commit/
session/frame；parent 不拥有 child authoritative state 或 child `run_id`；当前调用只用
opaque wait/abort handle；child 先投影、parent 再结算；调用级 cancellation 仅在有 live
nested child 时按 child-first 由各 owner 使用既有 `AbortGraphRun` 独立提交；typed failure
和 ordinary exception 不广播 sibling；保持 State/reducer、result/request/frontier、
continuation/frame ABI 和 owner-local Store/read/commit；不新增 persistence、failover、
ID-only recovery 或 overlap runtime gate。

## 2. 本轮已通过的修订

- target 已明确 standalone root 保留既有 active-token/state-only regression，nested 双边
  abort 不扩大到 standalone（§1.1、§7.2）。
- historical terminal child 增加了 `_ValidatedChildBoundaryCandidate`、typed
  `CompletedChild`/`AbortedChild` 投影，并禁止创建 live owner/slot（§8.2–§8.3）。
- `sealed_records` 的“唯一存储”含义、claim/source replacement、sink/relay 失败矩阵和
  `ContinuationSnapshot -> _GraphContinuation` 唯一 wrapper 已写明（§3、§6、§7.3、§8.3）。
- `ParentGraphActivation` 不扩展，owner 通过显式 `parent_scope_run` 传递；`SettleGraphNode`
  的 active lease/acknowledgement 顺序已在文字中补回（§3.1、§7.1）。
- manifest 已记录 identity direct consumers，并继续排除 persistence、failover、全局
  registry/lock、legacy/AST-only test；本轮不要求扩张这些范围。

以上是方向性通过，不等于 implementation target 可执行；以下问题仍是 blocker。

## 3. 阻塞项

### LO-IR19 — target 仍允许 parent 计算并携带 child `run_id`，与 requirements 直接冲突

requirements §4.2、§5.4 明确写的是 parent 不得**计算、派生、保存、查询或控制** child
`run_id`。但 target §1.2（第 44–56 行）和 §5.1（第 401–414 行）明确规定：

1. `execution/engine/frontier.py::prepare_frontier()` 在 parent prepare 中调用
   `child_graph_run_id(parent.run_id, parent.superstep, parent.node_id)`；
2. 把结果写进带 `StartGraphRun.run_id` 的 `PreparedNestedRun.command`；
3. 由 parent-side `WaitingForChildren` 暂存该 command，再交给 child `start/admit`。

当前基线也确实如此：`src/mote_kernel/execution/engine/frontier.py:57-72` 在 parent
prepare 中产生该 command，`src/mote_kernel/execution/graph_run.py:18-32` 再校验并构造
`StartGraphRun`。这不是 sealed continuation evidence，而是当前调用中用于启动 child 的
控制 command。将“parent 不计算”重新定义成“parent `_GraphRun` 不做 live lookup”不能
覆盖 requirements 中更严格的禁止项；method-local 存活也不能改变其产生者和控制用途。

**必须修订：** 在不擅自改写 requirements 的前提下，选择并冻结唯一方案：

- child owner/factory 产生 child coordinate/command，parent 只提供 activation metadata；或
- 如果确实必须保留现有 frontier command ABI，先由 requirements owner 明确批准这一
  infrastructure-only exception，并写出 command 不可被 parent 读取/控制的可验证边界。

无论选哪一条，必须同步修改 identity owner、调用图、manifest 和 acceptance；当前
“frontier/graph_run KEEP + parent 仍调用 identity primitive + parent 不计算 ID”三者不能
同时成立。

### LO-IR20 — parent claim/session 顺序没有现有 engine 实现路径

target §7.1 把正常 nested drive 固定为：

```text
parent ClaimGraphExecution commit
  -> parent session + active token
  -> start/admit child
  -> child projection
  -> tokened parent SettleGraphNode + session acknowledgement
```

这与当前 KEEP 的 engine/session contract 不相容：

- `src/mote_kernel/execution/engine/superstep.py:68-81` 先运行 `prepare_frontier()`；只要
  有 `missing_children` 或 `active_children`，就在 claim 之前返回 `WaitingForChildren`；
- 同文件 `:84-92` 的 `validate_execution_session_request()` 明确拒绝带 waiting child 的
  claimed request；
- `src/mote_kernel/execution/engine/session.py:198-204` 只有在 session 已由 claim 发出后，
  才用完整 child projection 建立 preparation/queued nested result；session 是
  single-consumer、逐次 acknowledgement 的既有对象。

所以按现有入口，parent 不能在 child 尚未启动/完成时先获得可用 session，再让该 session
等待 child。若绕过 `prepare_superstep()` 直接调用 `prepare_claim()`，就必须冻结
`PreparedExecutionClaim` 在 child wait 期间的持有、资源 snapshot、session 延迟 issue、
取消和 acknowledgement 规则；target 的 `_GraphRun` 字段没有 claim 或 session，且
manifest 又把 `engine/**` 标为 KEEP。若新增另一套 prepare/session 路径，则违反单一
execution engine/无第二路径约束。

**必须修订：** 二选一并写入可执行调用图：

1. 将顺序改为 child owner 先完成/投影，parent 再按现有 engine claim → session →
   `SettleGraphNode`；或
2. 明确只复用现有低层 claim/session primitive 的延迟 issue 方案，补齐 claim/session
   owner、字段、取消窗口、资源和 acknowledgement contract，并把实际受影响文件列入
   manifest（不得隐藏成第二 runner）。

在此闭合前，§7.1 的 acceptance 无法由现有 reducer/session ABI 实现。

### LO-IR21 — `_GraphRun` nominal contract 仍缺少 session/claim/recovery mode 和唯一 caller

§3.1 的 `_GraphRun` 字段只有 `graph, context, executor, limits, commit, _seal_sink`，但
其方法又要求：

- `close_and_quiesce()` 能找到当前 `GraphExecutionSession`；
- `drive_quantum()` 能保留/消费 `PreparedExecutionClaim` 并完成 session acknowledgement；
- `seal_for_export()` 能知道 `parent_scope_run`、`parent_activation`、self sink 的 anchor
  和当前 recovered/complete variant；
- `from_snapshot()` 的 terminal historical 分支不创建 live owner，而 §3.1 又把
  `_GraphRun.admit()` 描述成可在 construction 时选择 ownerful 或 historical 分支。

这些资料既没有出现在 `_GraphRun`、`GraphRunContext` 或 coordinator 的 nominal fields，也
没有一个完整 lexical closure contract。`finalize_scope()`、`stop_new_activation()`、
`_HistoricalEvidenceLease`、`_ChildBoundaryPresence` 和 self-sink 的 record producer 也
没有完整签名/返回值/错误 contract。当前 target 同时要求“禁止 hidden mutable state”和
“由方法自行找到既有 session/claim”，实现者仍必须自行选择第二存储或未声明闭包。

**必须修订：** 给出唯一 owner 的完整 typed shape（可以是 `_GraphRun` 字段，也可以是
  明确的 lexical owner contract）：session/claim 的 issue、consume、ack、close；
  `recovered` variant 的 owner；parent metadata/anchor；`finalize_scope` 和
  `stop_new_activation` 的 caller、返回/错误和 exactly-once 语义。并明确 terminal
  historical scope 是否绕过 `_GraphRun.admit()`，不能两种说法并存。

### LO-IR22 — finalizer 在首个 cleanup 错误后跳过必需的 fence/abort/seal

§6.2 的可执行伪代码把 `close_and_quiesce()`、`fence_if_execution_active()`、
`abort_invocation()` 和 `seal_for_export()` 放在同一个 `try`：

```text
try:
    close_and_quiesce()
    fence_if_execution_active()
    if signal: abort_invocation()
    seal_for_export()
except BaseException:
    discard_once(anchor)
```

因此：

- close 失败时不会再尝试必要的 fence/`AbortGraphRun`；
- fence callback 失败时不会执行后续的 `AbortGraphRun`/seal，只会进入一次 discard 分支；
- abort 或 seal 失败时没有明确区分“state 已确认但 record 未写入”和“entry 已移除”。

这与 requirements §4.1/§4.4 要求每个 live owner 在自己的 owner 上完成 quiesce、必要的
fence、`AbortGraphRun` 和 commit，以及 target 自己所写的“仍尽力完成 ancestor cleanup”
不一致。尤其 nested cancellation 禁止依赖 orphaned-claim recovery；若第一步错误后
留下 active token/session，当前 coordinator 又终止且 handle 释放，下一入口没有固定的
owner-local处理方式。

**必须修订：** 将每个 cleanup operation 变成独立的 shielded step，固定错误累积和继续
规则：哪些错误允许继续 fence/abort/seal，哪些必须停止；若 fence 未确认而不能 abort，
必须明确 state、entry、evidence、caller-visible error 和唯一下一入口。不能用一次
`discard_once` 掩盖尚未 quiesce 的 owner。

### LO-IR23 — relay 复制与 export 的 canonical owner 未定义，grandchild evidence 可能重复

target §6.1 规定 source cell 将完整 `sealed_records` relay 到 ancestor，source 与 destination
都保留该 tuple；§8.4 又规定 `export_snapshot()` 读取“各 owner 的 sealed records”。例如
grandchild `G` 的 record 会同时出现在 `G`/中间 child cell 和 ancestor cell：

```text
G cell sealed_records --relay--> C cell sealed_records --relay--> root cell sealed_records
```

target 同时声称：

- 每条 record 只绑定一个 owner；
- relay 不复制/不 retry；
- export 读取各 owner 的 records；
- 不得新增 consumed 集合、map 或第二事实源。

文档没有规定 export 是只读 root canonical collector，还是读取全部 cell；也没有规定在
immutable object alias 已存在时如何不把同一 record 追加两次到 `merge_local_frames()` 的
family `ScopedFrameIndex`。`_owner_for_record()` 对 `ConfirmedChildBoundary` 的 coordinate
（其字段是 child scope）与“boundary live owner 是 parent scope”的特殊映射也没有一条
完整算法/唯一 caller。这个缺口会直接影响多 child、grandchild、历史 completed/aborted
round trip 和 presence bijection。

**必须修订：** 冻结一个 canonical export/relay 规则，例如明确 root collector 是唯一
family export reader、下游 cell 的 relay tuple 仅作不可写 transport alias；或给出不改变
ABI 且不引入 consumed 集合的唯一去重/归属算法。与此同时补齐 boundary record 从 child
coordinate 到 parent owner 的 typed mapping 和 candidate 构造顺序，并以多层/多 sibling
例子证明每条 record 在 export 中恰好一次。

### LO-IR24 — confirmed child evidence 失败后的 caller-visible 终点仍不可操作

§7.3 多行 failure matrix 规定 child transition 已确认但 boundary、self sink 或 relay 失败
时：child state/evidence 保留、coordinator 终止、禁止 retry/failover/`_PartialCommitError`，
并把“新 authoritative state/continuation pair”列为唯一下一入口。

但本 change unit 又禁止 child-ID-only lookup/recovery，且失败发生在 boundary 尚未进入
parent continuation 时；target 没有说明谁产生这个“新 pair”，也没有既有 public/typed
返回值携带 retained child evidence。若“新 pair”只是 caller 在外部已有的 pair，应明确本
次 invocation 失败后不再提供 child evidence 的 recovery 保证；若要让 caller 继续 parent
settlement，则必须证明现有 continuation/result ABI 的 transient handoff，不得暗中增加
persistence 或 retry。

**必须修订：** 对每个 post-confirm evidence failure 明确其是不可恢复的 caller-visible
terminal error，还是可由现有 pair 继续；删除无法兑现的“新 pair”承诺，或给出不依赖
child ID、persistence、failover 的现有 typed handoff。该项不要求新增 recovery 能力，
只要求语义不自相矛盾。

## 4. 不应借修订扩大的范围

关闭上述 blocker 不授权或要求：

- 新 State/status/command、public `GraphRun`/handle、第二 scheduler/runner；
- 新 persistence/checkpoint/journal/receipt、跨 invocation child-ID load/recovery、
  failover 或 worker handoff；
- global registry、optimistic/persistent lock、overlap detector/rejection gate；
- 修改 `ChildProjection`、`StepRequest`、`ScopedFrameIndex` 或 continuation sealed payload
  ABI；
- legacy/compatibility/AST-only/private-helper-count test。

## 5. 验证记录

| 检查 | 结果 |
| --- | --- |
| target / requirements hash | target `7f44f4da…`；requirements `1ff31e95…` |
| target review inputs | 已读取当前 target 全文、requirements、第四次评审及现有 execution/state contracts |
| source spot-check | `frontier.py:57-72` parent-side child ID command；`superstep.py:68-92` waiting-before-claim/claimed-wait rejection；`session.py:198-204` nested result preparation only after session issue |
| targeted baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed** |
| links / whitespace | 本评审文档相对链接存在；文件级 no-index whitespace 检查无诊断 |
| production/State/Store/API/tests changes | 本轮无修改；仅新增本评审文档 |
| complexity gate | 按用户范围 **`USER-EXCLUDED / NOT RUN`**；不宣称完整 `make check` 通过 |

## 6. 最终 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
standalone cancellation boundary         = PASS IN TEXT / KEEP EXISTING CONTRACT
parent authoritative child state         = FORBIDDEN / PASS IN INTENT
parent child run_id                      = LO-IR19 OPEN (normative conflict)
parent claim/session order               = LO-IR20 OPEN (no KEEP-engine path)
_GraphRun nominal owner contract         = LO-IR21 OPEN
finalizer cleanup after error            = LO-IR22 OPEN
relay/export canonical ownership         = LO-IR23 OPEN
post-confirm evidence terminal semantics = LO-IR24 OPEN
historical typed projection              = PASS IN DIRECTION / depends on LO-IR23
parent settlement lease                  = TEXT COMPLETE / execution path blocked by LO-IR20
opaque invocation handle                 = ALLOWED / transient / wait-or-abort only
typed/ordinary failure                   = LOCAL / NO SIBLING BROADCAST
continuation/frame ABI                   = KEEP EXACT / export ownership unresolved
persistence / Store                      = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover        = OUT OF SCOPE
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
implementation authorization              = NOT GRANTED BY THIS REVIEW
```

请先按第 3 节修订 target、重新计算 hash，再进行下一次独立 implementation review；在此
之前不开始编码。
