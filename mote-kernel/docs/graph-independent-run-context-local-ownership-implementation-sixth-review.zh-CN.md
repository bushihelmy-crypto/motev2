# 父子图 GraphRun 本地 ownership 实施规范第六次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 上一轮 LO-IR19–24 的主要方向已经写回：frontier 不再产生 parent-facing child command，
> parent 在 child 完成后才 claim，owner-local cleanup 也有了独立的 shielded step。可是
> awaiting 子图的 continuation 交接、root/coordinator 的 owner 形状、recovery 的 identity
> 入口和 partial handoff 仍没有形成唯一可执行路径。因此本轮不能开始 production/test 编码。

本文件只做 docs-only 独立评审；不修改 requirements、production、State、Store、protocol、
public API 或 tests，也不引入 persistence、failover、worker handoff、child-ID-only 跨
invocation recovery、overlap gate 或第二 runner。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `6cfd899668b820f845f28afbedd35be31de2d56016595b290c8c1a921b5fe633` |
| target 行数 | 1451 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第五次独立评审](graph-independent-run-context-local-ownership-implementation-fifth-review.zh-CN.md) |
| production 基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

评审口径仍是已冻结的窄范围：每个 `GraphRun` 独占自己的 `run_id`、state、transition、
commit、session 和 local frame；parent 不拥有 child authoritative state 或 child ID；当前
调用只用 opaque wait/abort handle；child 先完成/投影、parent 再结算；调用级取消沿当前
调用链 child-first，由各 owner 使用既有 `AbortGraphRun` 独立提交；typed failure 和
ordinary exception 不广播 sibling；State/reducer、result/request、continuation/frame ABI
和既有 owner-local Store 语义保持不变。

## 2. 本轮已确认的改进

- §1.2、§5.1 已把 missing-child 的 identity/`StartGraphRun` construction 移到 child
  factory，并让 frontier 只产生 activation metadata；这是 LO-IR19 要求的正确方向。
- §7.1 已改为 child 先完成、parent 重新 prepare 后才进入现有 claim → session → tokened
  settlement 顺序，符合 waiting-before-claim 的 engine 事实，关闭了 LO-IR20 的旧顺序冲突。
- §3.1、§6.2 已声明 claim/session 是 `_GraphRun` 字段，并将 cleanup 拆成可独立继续的
  shielded step；LO-IR21、LO-IR22 的方向得到吸收。
- §6.1、§8.3 已选择 root collector 作为 family export 的唯一 reader，并给出了 terminal
  boundary 从 child coordinate 到 parent canonical owner 的映射；LO-IR23、LO-IR24 的
  方向性修订有效。
- `Awaitable[None]` 已参数化；作为 callable contract 的返回类型正确，未发现裸泛型问题。

以上是方向性通过，不等于 target 已达到可编码状态；以下问题仍会迫使实现者自行选择
第二个 owner、隐式 evidence 或不兼容的 recovery 路径。

## 3. 阻塞项

### LO-IR25 — `RUNNING/awaiting` 子图没有 continuation export/re-admit 的闭合路径

requirements §4.4、§6 明确要求 child interrupt/awaiting 时保持 `RUNNING`，下一次既有
continuation 行为不变。当前 target 的 sealed record 与 historical 路径却只定义了 terminal
child：

- `_SealedChildRecord` 的校验和 `_historical_child_projection()` 只覆盖 `COMPLETED`（有
  boundary）或 `ABORTED`（无 boundary）；
- §8.2 说 terminal child 不创建 live owner，而只有 snapshot 中仍需继续 quantum 的
  `RUNNING` child 才调用 `_GraphRun.admit()`；
- §8.3 的 `export_snapshot()` 又只从 root cell 的 complete `sealed_records` 构造
  `child_states` 和 family frames。文档没有规定正常返回 `AwaitingResume` 时，哪个 cell
  记录 `RUNNING` child 的 state、input/publication/resume frame，以及 root collector 如何
  读取和 relay 这些资料。

典型反例是：child 执行 `Graph.interrupt()` 后变成 `RUNNING + AWAITING_RESUME`，parent 返回
`AwaitingResume`，handle 随 invocation 释放。若不新增 ABI，下一次调用仍必须拿到该 child
的 `ChildStateBinding` 和完整 frame evidence；当前 target 没有记录类型、canonical owner、
finalizer release 或 `from_snapshot()` admission 顺序可完成这件事。把它临时塞进
terminal-only record 会改变 `_owner_for_record()` 的状态约束，直接违反当前文字。

**必须修订：** 在不改变 `ChildStateBinding`/`ScopedFrameIndex` ABI 的前提下，明确
`RUNNING/awaiting` 的 transient handoff：记录由谁构造、哪个 owner/cell 持有、root export
如何只读取一次、relay 如何传播、下一次 `from_snapshot()` 如何建立唯一 live owner，
以及失败时的既有错误和生命周期。必须增加对应普通 behavior/typed test。

### LO-IR26 — `_InvocationRunCoordinator` 与 root owner 没有 nominal contract

§3.1 给出了 `_GraphRun` 字段，却只给出
`_InvocationRunCoordinator.stop_new_activation(root_cell, ...)`、
`finalize_scope(root_cell, ...)` 和 `export_snapshot()` 的方法名，没有 coordinator 的字段、
构造顺序或 owner 集合形状。仅凭 `root_cell` 参数无法确定：

1. root `_GraphRun` 在 cancellation 时由谁执行 `close → fence → AbortGraphRun`；
2. child cells、root owner 和 historical lease 的 lexical traversal 如何固定为一次；
3. `AwaitingResume`/partial commit 时从哪里取得各 scope 的 state/local view；
4. coordinator 如何保证不保存 child coordinate/ID map，同时又能完成 root export。

如果实现者自行加入 owner map、scope lookup 或隐藏闭包，就会违反“单一 cell 指针、无
cross-scope lookup/第二事实源”的约束；如果不加入，则 root 无法被 finalizer 处理。该缺口
也使 LO-IR25 和 `_PartialCommitError` 无法落地。

**必须修订：** 写出 coordinator 的最小 typed shape（root owner、按 lexical 顺序的 cell/
factory/entry 引用、result-boundary 状态及其存活范围），并给出 root finalizer、正常
export、cancellation unwind、partial handoff 的唯一 caller 图。明确哪些引用是 transient
且不可读 child identity，哪些 owner-local 字段只能由对应 `_GraphRun` 读取。

### LO-IR27 — identity “唯一 runtime caller”与现有 `project_start_graph_command()` 不一致

target §1.2、§5.1、§9.1 声明 `child_scope_run_for_activation()` 是唯一 runtime identity
producer，且 `graph_run.py` 只消费 child factory 的 method-local command。但当前基线的
`src/mote_kernel/execution/graph_run.py:18-24` 中，`project_start_graph_command(graph, run_id,
parent)` 仍直接调用 `child_graph_run_id()` 校验 parent/child 关系；target 又要求 child
factory 在生成 coordinate 后调用这个函数（§3.3、§5.1）。若只按现有函数签名迁移，runtime
caller 至少有 factory 和 `graph_run.py` 两处，且 recovery 仍可间接触发同一校验。

**必须修订：** 冻结唯一的 typed 边界：要么让 factory 传入已验证的 coordinate/proof，令
`project_start_graph_command()` 只做结构投影；要么明确 `graph_run.py` 是 child owner 的
唯一 validator 并删除“factory 是唯一 caller”的表述。需要同步写明 command 的 parent 校验、
`_GraphRun.start()` 的 scope 校验和测试/manifest，不能让实现者通过重复调用 primitive
自行补洞。

### LO-IR28 — recovery 禁止派生 child coordinate，却没有 nested resume/fresh-child 的入口

target §1.2、§9.1 要求 `execution/invocation.py` 和 `engine/recovery.py` 只消费已验证的
sealed `ChildStateBinding.coordinate`，不得从 parent activation 派生 child coordinate/ID。
当前基线的 `src/mote_kernel/execution/engine/recovery.py:646-647`、`:695-702`、
`src/mote_kernel/execution/invocation.py:258-275` 则分别：

- 从 `ResumeNodeRequest.scope`（它只携带 scope path）解析 nested child coordinate；
- 对 `invocation_new` child 生成 coordinate 和 `StartGraphRun`；
- 用同一 resolver 校验 child lineage。

target 没有给出替代路径。若直接删除这些调用，state-only nested resume 和
`invocation_new_children` 会失去 child factory 所需的 activation/coordinate；若把 coordinate
加进 `RecoveryInvocationSeed`，又改变了明确要求保持的 recovery ABI；若继续在 recovery
中推导，则违反 §1.2 的唯一 identity owner。

**必须修订：** 对 path-only resume 和 fresh child 分别给出不改变 ABI 的 typed handoff（例如
先由既有 proof 产出 method-local factory input，再由 child factory 创建 owner），并明确
proof、state mutation、`StartGraphRun` commit 的顺序；或者重新裁决 recovery validator 可
执行的纯 coordinate resolver 及其 owner。必须补齐对应 manifest 和 behavior tests。

### LO-IR29 — 既有 `_PartialCommitError` 的 confirmed-prefix handoff 没有 caller 图

target §7.3 保留 `_PartialCommitError` 只用于既有 fence/resume exact-confirmed-prefix，
同时规定普通 result boundary 的 `export_snapshot()` 发生在 finalizer 完成后，且失败窗口
不得生成新的 pair。现有 public contract 的 `_PartialCommitError` 必须携带最新 root state
和 sealed `_GraphContinuation`；但 target 没有说明以下顺序：

```text
scope A exact commit
  -> scope B callback/non-exact failure
  -> 哪个 owner 负责收敛 A/B
  -> 如何取得只含 confirmed prefix 的 child_states/frames
  -> 如何只调用一次 export/wrapper 并构造既有 _PartialCommitError
```

如果直接读取未 finalise 的 root cell，sealed batch 可能不完整；如果先走普通 finalizer，
又需区分 recovery prefix、未确认 candidate 和不应 abort 的 owner。缺少该 caller 图会使
现有多 scope resume/fence partial tests 无法保持，且容易误把 ordinary failure 包装成
`_PartialCommitError`。

**必须修订：** 明确 recovery prefix 的唯一 handoff operation、owner 状态/证据快照点、
finalizer/relay 是否运行、`_continuation_from_snapshot()` 的调用次数和失败优先级；保持
现有 `_PartialCommitError` ABI，不新增 receipt/retry/failover。

### LO-IR30 — cancellation 的“有 live child / standalone root”分类不覆盖中间状态

§7.2 把双边 abort 限定为“本 invocation 存在 live nested child”，并把“没有 live nested
child”归为 standalone root。可是下列合法时刻既不是 standalone invocation，又可能没有
live child：child 已完成、parent 尚未 settlement；或 child 已 settlement、parent 正在下一
个普通节点上执行。requirements §4.4 的调用级语义要求 parent 随后关闭自己的 session 并
对自己的 state 应用同一 `AbortGraphRun`；target 当前文字会把它送入 standalone 的旧
active-token/state-only 行为，导致 parent 不一定变成 `ABORTED`。

**必须修订：** 定义不可重叠的分类谓词（例如“本 invocation 是否建立过 nested owner”与
“当前是否有 live child”分开），并分别写出：

- 纯 standalone root 的既有取消回归；
- 已有 nested evidence 但当前无 live child 时 parent 的取消/abort；
- child 已 terminal 时不重复 abort、parent 仍如何处理。

验收矩阵必须覆盖 child terminal→parent running 期间的取消，不得只测 live-child wait。

### LO-IR31 — relay 的空 batch与“自有 record/descendant batch”语义仍有矛盾

§3.2/§5.2 将 `_accept_relay_batch()` 的空 tuple 列为固定
`GraphValuePublicationError`；§6.2 的可执行伪代码却在 `facts.seal_confirmed` 后无条件调用
`relay_current_complete_batch`。叶子 child 没有 descendant record，会因此把正常完成当成
错误。另一方面，多层示例（§6.1）明确 C 的 relay 只发送 `(r_G)`，而 C 自己的 `r_C` 由
self-sink 直接写到 root；“source current complete subtree tuple”与“是否包含当前 owner
自有 record”没有形式化定义。historical terminal scope 的 relay closure 也未在
`register_historical`/finalizer 形状中声明。

**必须修订：** 冻结 `relay_current_complete_batch` 的精确语义：空 descendant batch 是否
是合法 no-op、source tuple 的构成/排序、self-sink 与 relay 的先后、每条 edge 的 exactly-once
以及 historical path 的 closure。`_accept_relay_batch()` 的错误表和伪代码必须一致，并
增加 leaf、grandchild、sibling、historical 四种 typed behavior 证据。

### LO-IR32 — test manifest 未覆盖必然受影响的旧 direct consumers

§9.2 只列出 10 个测试文件，却要求 frontier 不再携带 `PreparedNestedRun.command`、
`GraphRunContext` 删除 child state operation、recovery 改变 coordinate owner。当前 source
scan 显示至少下列未列文件直接断言旧 contract：

- `tests/execution/test_executor.py` 多处断言 `WaitingForChildren.action` 是
  `StartMissingChildren`，并读取 `children[*].command`；
- `tests/execution/engine/test_recovery_identity.py` 直接覆盖 recovery coordinate/child
  disposition；
- architecture/state tests 也调用 identity primitive，需明确哪些保持不变、哪些只做
  pure validation regression。

如果不修改 `test_executor.py`，只能保留 parent-facing command 兼容路径，正好违反 §1.2、
§5.1 和“无 compatibility alias/旧入口”；如果修改却不登记，manifest 与 direct-consumer
scan 不一致。

**必须修订：** 重新运行 source/test scan，逐文件标注 KEEP、MODIFY 或新增，并把所有因
signature/producer/owner 变化而需要修改的测试加入 §9.2；保留现有 case 与断言强度，不用
删除、改名或兼容 alias 解决。

## 4. 不因本轮修订而扩大的范围

关闭上述问题不授权或要求：

- 新 State/status/command、public `GraphRun`/handle、第二 scheduler/runner；
- 新 persistence/checkpoint/journal/receipt、跨 invocation child-ID load/recovery、retry、
  failover 或 worker handoff；
- global registry、overlap detector、乐观锁或持久化锁；
- 修改 `ChildProjection`、`StepRequest`、`ScopedFrameIndex`、`ChildStateBinding` 或 public
  `Graph.run()` ABI；
- 新 legacy、compatibility、AST-only 或 private-helper-count test。

## 5. 验证记录

| 检查 | 结果 |
| --- | --- |
| target / requirements hash | target `6cfd8996…`；requirements `1ff31e95…` |
| baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed** |
| source spot-check | `graph_run.py:18-24` 仍由 `project_start_graph_command()` 调用 `child_graph_run_id()`；`recovery.py:646-702` 仍有 parent-derived coordinate/start command；测试 scan 发现 LO-IR32 文件 |
| production/State/Store/API/tests changes | 本轮无修改；仅新增本评审文档 |
| complexity gate | 按用户范围 `USER-EXCLUDED / NOT RUN`；不宣称完整 `make check` 通过 |

## 6. 最终 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
parent authoritative child state/ID      = FORBIDDEN / PASS IN INTENT
frontier waiting-before-claim            = PASS IN TEXT
child-owned identity producer            = LO-IR27 OPEN (projector/recovery caller conflict)
RUNNING/awaiting continuation handoff    = LO-IR25 OPEN
coordinator/root owner contract          = LO-IR26 OPEN
recovery nested/fresh-child path         = LO-IR28 OPEN
partial confirmed-prefix handoff         = LO-IR29 OPEN
cancellation middle-state classification = LO-IR30 OPEN
relay empty/own-batch semantics          = LO-IR31 OPEN
direct consumer/test manifest            = LO-IR32 OPEN
finalizer shielded cleanup               = PASS IN DIRECTION / depends on owner contract
terminal historical projection           = PASS IN DIRECTION / depends on RUNNING handoff
continuation/frame ABI                   = KEEP EXACT / export path incomplete
persistence / Store                      = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover        = OUT OF SCOPE
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
implementation authorization             = NOT GRANTED BY THIS REVIEW
```

请先按第 3 节补齐唯一 typed contract、更新 manifest 并重新计算 target hash，再进行下一次
独立 implementation review；在 blockers 关闭且用户另行明确授权前，不开始编码。
