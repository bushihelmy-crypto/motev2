# 父子图 GraphRun 本地 ownership 压缩实施方案第三次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮复核的是压缩后的 implementation target。它保留了已对齐的窄边界（每个
> `GraphRun` 独占 state、parent 不拥有 child `run_id`、调用级取消向下传播、无新增
> persistence/failover/跨 invocation 恢复），也尝试保留 presence witness 和单一 cell
> 指针；但压缩过程中删掉了上一版已经冻结的若干可执行 typed contract。当前仍会迫使
> 实现者自行决定 family evidence 如何交接、finalizer 如何一次性收敛、确认后失败采用
> 哪个既有错误，以及哪些测试可以新增。因此本轮不能开始 production 或 tests 编码。

本文件是 docs-only 独立评审；不修改 requirements、implementation target、production、
State、Store、protocol、public API 或 tests。评审结论不扩大范围，也不引入持久化、
failover、worker handoff、child-ID-only 跨 invocation recovery、overlap gate、第二 runner
或新的 public type。

## 1. 评审对象与冻结输入

| 对象 | 内容 / SHA256 |
| --- | --- |
| 受审 implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) — `92f452bc8c5386a67aa1ccd6f158590c72de98a29729dfdcfadd71bce1136fe5` |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) — `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一版 blocker 回复 | [实施方案评审回复复审回复](graph-independent-run-context-local-ownership-implementation-review-response-review-response.zh-CN.md) — `28e789fe13704445fb9f7fba89fc551de8bcd853d7304579fb6f72a8a0779b56` |
| 上一轮 presence/finalizer 复审 | [实施方案第二次评审回复复审](graph-independent-run-context-local-ownership-implementation-second-review-response-review.zh-CN.md) — `8c680b770856a3d4fe390147e6de0b5213a935d76920e1576a49f4fe60f1704f` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

工作树中存在其他用户已有的 README、examples、package 和历史 docs 改动；本评审不将其
归因于本 change unit。

## 2. 评审口径

只按已经确认的窄范围判断：

1. 每个 `_GraphRun` 独占自己的 `run_id`、`GraphRunState`、local frame、transition、
   session 和 commit；parent 不维护 child authoritative state 镜像。
2. parent 不保存、派生、查询、恢复或控制 child `run_id`。既有 sealed
   `ChildStateBinding`/boundary 只能作为 immutable transport/validation evidence；当前
   invocation 的等待/取消只能使用 opaque handle。
3. child 先完成并投影，parent 再只结算自己的 nested node。调用级 cancellation/abort
   沿当前调用链 child-first，各 owner 使用既有 `AbortGraphRun` 并独立 commit。
4. typed failure 和 ordinary node exception 不广播 sibling；State/reducer、result、
   request/frontier、continuation/frame ABI 和 owner-local persistence read/commit 不变。
5. 不增加 persistence、failover、child-ID-only recovery、overlap runtime gate、第二
   scheduler、State 字段、public API 或 legacy/AST-only test。

## 3. 已通过的方向项

以下方向与 requirements 一致，但不等于 target 可实施：

| 事项 | 结论 |
| --- | --- |
| 每图一个 private `_GraphRun` | 方向通过；仍需可编译的 factory/owner 签名 |
| parent 不保存 child state/ID | 方向通过；sealed record 与 live view 的边界尚未闭合 |
| `find_child_boundary() -> bool` | 目标正确；presence 的稳定来源和重建规则仍有缺口 |
| 单一 `_ChildScopeCell._current` | 目标正确；cell 的具体 replacement/claim API 未完整冻结 |
| child-first 双边 abort | 顺序正确；取消后 active token/session 阶段未完整定义 |
| 不新增 persistence/failover/ID-only recovery | 通过 |
| cross-parent overlap 作为 caller precondition | 通过；不应增加 lock、registry 或 runtime gate |

## 4. 阻塞项

### LO-IR7 — family evidence handoff 被压缩成不可编译的伪调用

target §8（约第 380–408 行）只给出：

```text
partition_family_frames(...)
  -> _owner_for_record(...)
  -> _live_view_from_partition(...)
  -> merge_local_frames(...)
```

但 target §3.1 又把 `GraphRunContext` 改成单 run 形状（第 84–115 行），并要求删除
`child_states`。当前源码中的 `_GraphContinuation.admit()`、`lineage_states()`、
`_validate_frame_index()`、`_validate_complete_context()` 和 `recovery_seed()` 仍分别以
family context、root state、child bindings 和 family `ScopedFrameIndex` 互相传递：

- `src/mote_kernel/execution/run_context.py:353-370` 的 `admit()` 当前直接构造含
  `child_states` 的 `GraphRunContext`；
- `src/mote_kernel/execution/invocation.py:203-213,450-619` 的 validator/lineage 当前
  直接读取该 context；
- `RecoveryInvocationSeed` 仍是既有 family-shaped evidence。

压缩版没有规定：

1. `admit()` 返回的 exact immutable evidence 类型以及 seal/root pairing 的 owner；
2. `partition`、`_owner_for_record`、`merge` 的完整输入/输出类型、root state 参与方式和
   每条 record 的唯一归属；
3. local owner 建立前后 evidence 的存活范围，以及唯一的 `export_snapshot()` producer；
4. state-only pending child 缺 evidence 时在 fence/start/claim/resource/node call 之前的
   固定 `GraphValueUnavailableError` 路径。

实现者只能在“把 child map 留回 context”“新增第二 DTO”“从 record 反推 child ID”之间
自行选一个，分别违反 target 的删除、唯一事实源或 parent identity 边界。该缺口不是代码
细节，必须在 target 中补回 typed handoff 与生命周期，才能开始编码。

### LO-IR8 — boundary presence witness 与既有 frame ABI 的生命周期/身份不一致

target §4.1（第 164–196 行）同时要求：

- `candidate_frames` 必须是既有 `ScopedFrameIndex.add_child_boundary(boundary)` 的返回值；
- `_RunLocalView.frames` 继续保存 `ConfirmedChildBoundary`；该 record 的 coordinate 含
  child `ScopeRunCoordinate`/`run_id`；
- parent live view 只保存 `_ChildBoundaryPresence`，并以 boundary **object identity** 做
  bijection；
- `base_view.owner_scope is owner_scope` 必须成立。

这里有三个未闭合的冲突：

1. target §2 明文禁止 child identity 进入 parent live state/live capability/coordinator，
   但 §4.1 又把含 child coordinate 的 boundary record 放进 parent 的 live `frames`。如果
   该 record 被视为 sealed evidence，必须写明它何时从 live view 转为 evidence；如果不是，
   就违反已批准边界。
2. 当前 `ScopeRunCoordinate` 是值相等的 frozen dataclass，调用和 continuation partition
   会反复构造等价对象；用 `is` 而不是 `==` 没有稳定保证，合法 snapshot 可能被误判为
   foreign。target 没有冻结 canonical object 的创建/传递边界。
3. 当前 `ConfirmedChildBoundary` 是 `eq=False`，`ScopedFrameIndex` 只按 coordinate 检查
   重复。partition、sorted tuple、sealed continuation 重建时如何保留同一个 boundary
   对象，target 没有定义；无法唯一实现 witness↔record bijection、幂等 install 和 stale
   检测。

需要补回一个唯一 private local-view contract：明确 boundary record 是 immutable
transport evidence 还是 live frame、canonical coordinate 的来源、partition/merge 如何保留
对象关系，以及 foreign/duplicate/stale 的固定错误优先级。不能以第二个 activation map
或 parent child-ID map 填补。

### LO-IR9 — `_GraphRun`、cell、finalizer 的 nominal shape 仍不足以实现一次性清理

target §3.1–§3.3（第 84–158 行）只列出名称，未定义确切字段/签名的关键类型包括：
`_ChildBoundaryPresence`、`_ChildScopeAnchor`、`_HistoricalEvidenceLease`、
`_ScopeFinalizerClaim`、`_ScopeFinalizerEntry`、`_SealedChildEvidenceSink`、
`_InvocationAbortSignal`、`_RunLocalView`、`_ChildScopeCell` 和 `_GraphRun` 的
`start/admit/confirm/drive_quantum/seal_for_export`。

这与 §6（第 275–321 行）的算法不一致：算法需要读取 owner/session 的状态、递归
descendant、区分 provisional/slot/historical source、保存第一个 error、调用
`seal_and_remove`/`discard_once`/`release_historical_once`，但 `_ScopeFinalizer` 的唯一
nominal operation 只有 `finish(signal) -> Awaitable[None]`，cell operation 表也没有规定
claim 如何被后续 seal/discard 消费、relay 的 source/destination owner 如何验证。

尤其是：

- `claim_finalizer()` 明确“不返回 claim”，却没有规定 `seal_and_remove()` 如何获得并验证
  同一个 claim；
- `_ChildScopeCellSnapshot.sealed_records` 含 child state evidence，但没有规定它只能作为
  immutable export transport，还是可被 occupancy/drive 读取，容易重新形成 parent state
  mirror；
- `finish()` 的 `None` 返回、错误聚合、sink 写入、entry removal 和 relay 失败没有 exact
  result/error contract；
- `source` union 中的 provisional、published、historical 和 claim 没有唯一构造/销毁者。

在 strict typing、禁止 hidden mutable state/第二事实源的约束下，不能把这些生命周期分支
留给实现者“用 closure 自行补齐”。必须补回完整 nominal operations 或把所有分支封装成有
明确 completion/error 语义的单一 finalizer contract，并画出唯一 caller 图。

### LO-IR10 — post-confirm failure 与 cancellation phase 没有逐窗口裁决

target §5.1、§6.2、§7.2 只给出概括规则（确认后 owner-local cleanup、child-first
quiesce/fence/abort），没有恢复上一版 target 的逐窗口矩阵。至少以下窗口仍无固定结果：

| 窗口 | 当前未冻结的事实 |
| --- | --- |
| `StartGraphRun` callback 抛错或返回 non-exact successor | acknowledgement unknown 时保留哪个 state/evidence，是否允许下一次调用 |
| start 已确认、graph-input frame 安装失败 | owner 是否进入 provisional cleanup，采用何种既有 error |
| child transition 已确认、publication/boundary 安装失败 | child sealed evidence 是否保留，parent 是否可再次 project |
| frame 已装入、opaque slot publication 失败 | handle/owner 如何释放且不二次 abort/seal |
| sibling 部分 start | 已确认 sibling 是否继续 seal，失败 sibling 的 state 如何交给 caller |
| child terminal 已确认、parent settlement callback 失败 | 如何避免 child 重跑或重复 boundary |
| cancellation 与 start acknowledgement 同时到达 | one-shot acknowledgement、provisional 登记和 cancellation 传播顺序 |
| session 已关闭但 `state.execution` 仍存在 | 当前 coordinator 是否绝对不可 drive，后续唯一 state-only fence 入口是什么 |

现有实现和测试已经定义了相关既有语义：
`FrameInstallationInvariantError`、confirmed-prefix `_PartialCommitError`、
`GraphValueUnavailableError`/`SnapshotMismatchError`，以及取消后保留 active execution
token、下一次 state-only invocation 先 fence。压缩版没有规定哪些窗口沿这些语义，哪些明确
不产生 partial handoff，也没有错误优先级和“不能 retry/failover”的 caller-visible 结果。
这会直接影响 continuation pairing 和重复 publication，属于 blocker。

### LO-IR11 — test manifest 自相矛盾且无法满足 acceptance

target §9.2 标题写“**只改现有行为测试**”，但 manifest 列出当前不存在的：

```text
tests/execution/test_graph_run_ownership.py
```

同时 target §10 要求该文件承载 ownership、handoff、finalizer、relay 等行为证据。二者
不能同时成立：

- 若新增该文件，必须把规则改为“仅新增/修改 manifest 列出的 behavior tests”，并明确它
  不是 legacy/AST-only test；
- 若坚持只改现有测试，必须移除该路径并把行为证据放入已有文件。

此外，source scan 显示以下真实 private consumers 会受 context/result projector 收窄
影响，必须逐项在 manifest 中声明迁移或 KEEP 证明：

```text
tests/execution/test_result_boundary_contract.py
tests/execution/test_continuation_integrity.py
tests/execution/test_frame_index_contract.py
tests/execution/test_graph_api.py
tests/execution/engine/test_runtime_boundaries.py
tests/architecture/test_graph_execution_ownership.py  # target 声明 KEEP
```

target 虽列出前五个，但没有给出 architecture KEEP 与“不依赖 AST/source-shape acceptance”
之间的验证边界，也没有提供最终 direct-consumer scan 记录。manifest 修订前不能宣称
“零债务”或开始编码。

## 5. 不应借修订引入的范围扩张

关闭上述 blocker 只需要恢复窄的 typed contract，不需要也不授权：

- 新 State/status/command、public `GraphRun`/handle、第二 runner/scheduler；
- persistence、checkpoint、journal、receipt、retry、failover、worker handoff；
- 仅凭 child `run_id` 的跨 invocation load/drive/recovery；
- global registry、optimistic/persistent lock、overlap detector/rejection gate；
- 改写 `ScopedFrameIndex`/continuation sealed payload 的 ABI；
- 新增或扩写 legacy、private-source-shape、AST-only、helper-count test。

“不做持久化”仍表示保留各 owner 既有 authoritative Store/read/commit 语义；持续 commit
失败属于既有 Store/commit 错误，不由本 change unit 设计接管、回滚、重试或 failover。

## 6. 达到开发条件的必要修订

1. 恢复 family evidence 的 exact typed admission/partition/validation/export producer，明确
   `GraphRunContext` 收窄后 `lineage_states`、frame validators 和 recovery seed 的输入/输出。
2. 冻结 boundary record 与 presence witness 的唯一 owner、canonical object/lifecycle、
   bijection、merge 和错误优先级；明确 live view 与 sealed evidence 的分界。
3. 让 `_GraphRun`、`_ChildScopeCell`、finalizer、claim、sink、relay 和 abort signal 的
   nominal shape 与伪代码一致，规定每个 source 的唯一构造/移除 caller。
4. 回写 confirmation/start/frame/slot/settlement/cancellation 的 post-confirm failure
   matrix，复用既有错误和 confirmed-prefix 语义，不添加 retry/failover。
5. 修正 test manifest 的“只改现有”矛盾，重新运行 production/test direct-consumer scan；
   architecture AST test 保持不变且不作为本 change unit 的新增 acceptance。
6. 重新计算 target hash，并进行下一次独立 implementation review；通过后再依据用户明确
   授权进入窄 manifest 编码。

## 7. 验证记录

| 检查 | 结果 |
| --- | --- |
| target / requirements / prior-response hash | 与第 1 节冻结值一致（target `92f452…`） |
| source direct-consumer scan | 发现 architecture KEEP 边界与缺失 test 文件；见 LO-IR11 |
| targeted baseline behavior | **164 passed**：nested facade、continuation、frame、result boundary、runtime boundary |
| production / State / Store / tests 改动 | **无**；本轮只新增本评审文档 |
| `git diff --check` | 无诊断 |
| complexity gate | `USER-EXCLUDED / NOT RUN`；不作为本轮裁决 |

## 8. 最终 ledger

```text
parent authoritative child state       = FORBIDDEN / direction pass
parent live child run_id                = FORBIDDEN / boundary lifecycle OPEN
family evidence typed handoff           = LO-IR7 OPEN / BLOCKER
boundary presence/ABI                   = LO-IR8 OPEN / BLOCKER
cell/finalizer nominal contract         = LO-IR9 OPEN / BLOCKER
post-confirm/cancellation matrix        = LO-IR10 OPEN / BLOCKER
test/direct-consumer manifest           = LO-IR11 OPEN / BLOCKER
typed/ordinary failure                  = LOCAL / NO SIBLING BROADCAST
continuation/frame ABI                  = KEEP EXACT / evidence boundary unresolved
persistence / Store                     = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover       = OUT OF SCOPE
cross-parent overlap                    = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                  = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
implementation authorization             = NOT GRANTED BY THIS REVIEW
```

**最终结论：** 压缩版的原则方向正确，但缺失的不是可由编码阶段自由决定的实现细节；
它们决定唯一事实源、错误语义和 cancellation safety。请先按第 6 节回写并重新独立审核，
本轮不开始编码。
