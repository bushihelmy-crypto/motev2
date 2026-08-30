# 父子图 GraphRun 本地 ownership 实施方案评审回复复审回复

> **回复结论：技术 blocker 已按窄范围吸收；AST gate 采用“不修改既有测试”的分支；本轮仍不授权 production/test 实施。**
> 复审指出的 family evidence、post-confirm 失败窗口、cancellation phase、local frame
> owner 和 consumer manifest 缺口均是合理的可实施性要求，已写入 implementation target。
> 关于 AST/source-shape test 的矛盾，接受“target 内部不一致”这一判断，但拒绝以例外
> 方式扩写 AST gate；既有测试保持不变，ownership 改由 public/typed behavior 证明。

本轮又补齐了复审草稿中仍可能被实现者自行解释的细节：异步 factory/commit/drive 的
exact signatures、post-confirm 的 provisional owner、frame partition 所需的 root state、
child boundary 的唯一 parent producer，以及 missing-child 的 parent owner 和重复 boundary
判定。它们仍是 private implementation contract，不改变 requirements、sealed payload、public
API 或错误 taxonomy。

末次一致性审计又把四个边界写成不可重叠的规则：coordinator 的唯一
`export_snapshot()` typed producer、running/idle 与 awaiting 的 phase 区分、export duplicate
与 output-admission 的既有 error precedence，以及 child output projection 与确认后 boundary
安装的分界；这些修订仍只收紧实现契约，不改变可观察行为。

## 1. 回复对象与冻结输入

- 被回复复审：[父子图 GraphRun 本地 ownership 实施方案评审回复复审](graph-independent-run-context-local-ownership-implementation-review-response-review.zh-CN.md)
  - SHA256：`a5953fa99256fd8ccaacd11a52a4c4051678f03935617f1c7beac15ca7fb13fa`
- 原逐项回复：[本地 ownership 实施方案继续独立评审回复](graph-independent-run-context-local-ownership-implementation-review-response.zh-CN.md)
  - SHA256：`3017d0298e8315dd5342107e88e1dbf183faffecf54b8dfd5f20723cf33701ce`
- 修订 implementation target：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
- 本轮修订后 SHA256：`e343cfd93c504314b969474e997f5c06a61b7cabcf530e961fbc2fb76ca4da18`
- requirements：[GRC-LO-001 本地 ownership 拆分需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
  - SHA256：`8a91cf520650fd127d756aab311714fc084e42e41f76723c56b0df6065b96a1e`
- production baseline：`ebcd043fdfe324c610328a08cb1a3e8a14b37e10`
- 回复日期：2026-08-28

本轮只修改 implementation target 和本回复文档；requirements、production、State、Store、
protocol、public API 与 tests 均未修改。复审原文保持不变。

## 2. 总体裁决

复审的合理核心是“目标必须能按唯一 typed contract 实施”，而不是要求扩大能力边界。
最终采用如下处理：

```text
family continuation/recovery evidence
  -> 继续使用现有 sealed snapshot 与 family-shaped seed
  -> 明确 typed producer/consumer、验证顺序和生命周期

runtime mutable ownership
  -> 每个 _GraphRun 只拥有自己的 state 与 local frame view
  -> coordinator 只持有 invocation-local handle tuple

observable result/session behavior
  -> 保留 ChildProjection、StepRequest、frontier、Graph.Result 和现有 errors
  -> 补齐 post-confirm/cancellation 的失败窗口契约
```

这不改变以下边界：不做 persistence、Store、checkpoint、failover、跨 invocation
`child_run_id` 恢复、global admission guard、第二 runner、State/public API 变更，也不把
cross-parent overlap 变成 acceptance。既定的纯 identity 不变量仍保留：同一 immutable
compiled child 在不同 parent `run_id` 下派生的 child `run_id` 必须不同；这不是 overlap
admission 或并发安全承诺。该 identity 只用于把 child state 绑定到 parent activation、
definition 和 frame descriptor，并判定同一 invocation 的重复 activation，不是 parent lookup
或跨 invocation recovery key。

## 3. 逐项处置

### LO-RR1 — family evidence typed 输入/输出 owner

**采纳技术意见。** 原 target 只说“把 snapshot 交给 coordinator”，没有冻结实际输入、
输出和存活范围；这确实会让实现者在 context、opaque slot 或第二 DTO 之间自行选择。

实施文档 §4.2、§7.1.1 现在明确：

- `_GraphContinuation.admit(_seal, family_identity, state)` 只负责 seal、family identity
  和 root pairing 校验，并返回现有 `ContinuationSnapshot[GraphValueT]` union 的同一份 immutable
  evidence；不构造 family `GraphRunContext`、live handle 或 ID lookup。
- `from_new_root()`、`from_state_only()` 和 `from_snapshot()` 是唯一 coordinator/context
  factory；`from_new_root()` 接收 root input frame（并异步确认 `StartGraphRun`），
  `from_state_only()` 只由 facade 调用，接收 root state 与新建的空 root local frame index，
  并在 factory 内固定使用空的 child-binding tuple，`from_snapshot()` 接收已 seal 的
  `ContinuationSnapshot`。所有涉及
  `GraphCommit` 的 `start/confirm/drive` operation 都是 async，
  `drive_quantum(child_projections)` 只消费 coordinator 传入的 immutable vector，并只返回
  existing waiting/boundary subset 或 `None`。
- `recovered` 的既有语义也保持闭合：new-values `from_new_root()` 为 `False`，state-only
  `from_state_only()` 为 `True`，`from_snapshot()` 按 `_RecoveredContinuationSnapshot`/
  `_CompleteContinuationSnapshot` 分别取 `True`/`False`；它只控制 validation、preflight
  和 export variant，不新增 lifecycle/status 字段。
- coordinator factory 是唯一 live-owner consumer；`lineage_states(root_state, child_states)`、
  `_validate_frame_index(graph, root_state, child_states, frames)`、
  `_validate_complete_context(...)`、`validate_context(..., recovered)`、
  `require_child_evidence(...)` 和
  `recovery_seed(states, frames, limits, facts)` 的参数/返回形状已逐项写明。
- family identity 只由 root facade 为一次 compiled family 分配一次，再显式传给所有 nested
  `_GraphRun`；nested `Graph` 的 standalone owner identity 不进入 parent invocation 的
  runtime evidence，避免按 definition 或 handle 产生第二份 family truth。
- `_GraphRun.start/admit/confirm/drive_quantum` 与
  `project_missing_child(parent, activation)`/`project_child(handle)` 的最小 typed
  operation 签名也已冻结（start/admit 显式携带 `ParentGraphActivation | None`，missing
  projection 显式携带 parent owner），任何
  operation 都不能接收或替换另一个 scope 的 state/frame。
- export 通过 `_snapshot(family_identity, root_state, child_states, frames, recovered)`
  重新构造原 sealed union；不会把 live coordinator 反向塞回 `run_context.py`。
- coordinator 的唯一 runtime export operation 已冻结为
  `_InvocationRunCoordinator.export_snapshot() -> ContinuationSnapshot[GraphValueT]`：它只读
  root/child owner 的 immutable state/local frames，fresh 构造 canonical bindings、调用
  `merge_local_frames()` 后返回既有 snapshot；`project_graph_result()` 每个 result boundary
  只调用一次，recovery partial handoff 仅在其既有 confirmed-prefix 路径消费，export 不做
  commit 或 mutation。
- evidence 生命周期固定为 seal/root pairing → full frame/lineage validation →
  whole-invocation proof → local owner drive → canonical export；local owner 建立后 evidence
  仍只供 proof 读取，运行期只替换对应 `_GraphRun` 的 state/frame pointer，任一步失败都不
  修改输入 evidence；export 后仅释放 coordinator 对 evidence 的本地引用，caller 持有的
  opaque continuation 仍保持 immutable。
- state-only pending nested activation 缺 matching child evidence 时，在 fence、start、
  claim、resource admission 和 node call 之前抛既有 `GraphValueUnavailableError`；malformed
  continuation 仍由既有 `SnapshotMismatchError` 在 partition 前拒绝。

**不采纳的扩张。** “typed handoff”不等于新增 per-run recovery protocol、opaque slot
或第二 family DTO。现有 `ContinuationSnapshot` 是 continuation evidence 的唯一来源，
`RecoveryInvocationSeed` 只是由已验证 evidence 纯派生、供既有 recovery engine 消费的
family-shaped view，不是第二份 state/frame authority；这足以在不改变 ABI 的前提下完成
runtime ownership 拆分。

处置位置：implementation §4.2–§4.3、§6.4、§7.1.1、§7.2、§11 Step 1/2/4。

### LO-RR2 — confirmed state 与 frame/handle 安装失败窗口

**采纳。** 复审正确指出 exact commit 返回后外部确认事实不可回滚，原文“原子加入”不足以
说明失败后的观察。实施文档新增 §10.4 post-confirm failure matrix，逐项覆盖：

- 新建 root/child 的 `StartGraphRun` 在 confirmation 前 callback exception/non-exact successor；
- 已存在 run 的 claim/settle/fence/resume 在 confirmation 前 callback exception/non-exact successor；
- root 或 child `StartGraphRun` 已确认但 graph-input frame 安装失败；
- child state 已确认但 publication/boundary 安装失败；
- parent `SettleGraphNode` 已确认但 parent publication/routing frame 安装失败；
- frame 已安装但 canonical handle 插入失败；
- sibling partial start；
- child terminal 已确认但 parent `SettleGraphNode` callback 失败或 unknown。

每行均冻结 state、frame、handle 在当前 structured unwind 中的保留/丢弃、exact existing
error、`_PartialCommitError` 是否适用，以及下一次调用是否允许重试。另明确了
`StartGraphRun` exact acknowledgement 后先由一个不可见 provisional `_GraphRun` 持有
confirmed state；只有 input frame 安装和 canonical handle 插入都成功才发布 handle。已有
owner 在确认前失败时保留原 state/frame 到 unwind；只有新建 start 在确认前失败时没有
owner。root 没有 child handle，但同样遵循 provisional owner 的安装顺序。
关键规则是：

- 对应 record installation/settlement commit 前的 projection/descriptor/value validation
  仍原样使用现有 `SnapshotMismatchError`、`GraphValueAdmissionError` 或
  `GraphValueUnavailableError`；
- post-confirm frame record failure 使用既有 `FrameInstallationInvariantError`（原始
  `GraphValuePublicationError` 保留为 cause）；不伪造 external rollback；callback 抛错或
  non-exact successor 则记为 acknowledgement unknown，不能宣称没有外部副作用；
- child terminal 的 output projection/descriptor validation 发生在 parent boundary install
  之前，失败时原样传播 `SnapshotMismatchError` 或 `GraphValueAdmissionError`；只有已通过
  projection 后的 boundary/publication frame add duplicate 才把既有
  `GraphValuePublicationError` 包装为 `FrameInstallationInvariantError`，不混淆两类窗口；
- ordinary runtime start/publication/boundary/settlement failure 不产生
  `_PartialCommitError`，不提供同 invocation retry；
- `_PartialCommitError` 仍只属于既有 recovery fence/resume confirmed-prefix；
- 若同一 frame 安装窗口发生在该既有 recovery loop，仍由现有 confirmed-prefix 外层包装，
  不改变其 handoff 语义；
- export-time detached merge/output projection 只读且不再调用 commit；foreign/descriptor
  mismatch 原样传播 `SnapshotMismatchError`，duplicate frame 原样传播
  `GraphValuePublicationError`，output value/descriptor admission 原样传播
  `GraphValueAdmissionError`；不回滚、重试或伪造 `_PartialCommitError`；
- coordinator unwind 后不保存 retry token、receipt 或 checkpoint。caller 若自行保留了
  confirmed state，下一次只能走现有 state/continuation pairing 和 frame validation。

target 还统一写死：普通错误传播后当前 coordinator 不可再次 drive；只有 root/parent state
且缺 child evidence 时下一次固定为 `GraphValueUnavailableError`，continuation binding/frame
不完整时固定为 `SnapshotMismatchError`，完整合法 pair 才进入既有 admission，而不是隐式
重试或按 ID 猜测。

**不采纳的扩张。** 复审若被理解为要求为这些窗口增加 durable handoff、replay、自动
  retry 或 failover，则超出 GRC-LO-001；矩阵明确的是“不承诺”，不是新增恢复协议。

处置位置：implementation §4.3、§10.1–§10.4、§11 Step 3。

### LO-RR3 — cancellation 后 phase 与现有 engine

**采纳。** “`RUNNING` 且无 session”不能笼统称为 idle。实施文档 §4.4 和 §10.2 现在把
`state.execution != None` 且 session 已关闭/不存在标为派生的 `cancelled/orphaned claim`
边界：

- 不增加 status、field 或 lifecycle enum；phase 只由现有 state execution lease、frontier
  disposition 与 method-local session presence 推导；只有 `state.execution is None` 且
  frontier 不是 awaiting 才是可 prepare/claim 的 running/idle；active token + 无 session
  是不可运行 orphaned claim；
- cancellation 先等待当前调用链所有 session close/quiesce，再释放 invocation-local
  handles；active token 保留，不自动 fence、不生成 continuation；
- 当前 coordinator 取消后不再调度 drive/confirm/resume/child invoke；`CancelledError` 单独
  走 close/quiesce 后原样传播，不进入 ordinary exception 的自动 fence 分支；仍带 active
  token 的 stale drive/confirm 沿现有 `ResultCollectionError("active execution requires its
  original execution session")` 失败，直接调用 `GraphExecutor.resume()` 沿其既有
  `SnapshotMismatchError` 失败；idle stale object 不再有 caller-visible operation，不创建新
  claim；
- 唯一后续入口是新的 `Graph.run(state=...)`，完成结构校验和 whole-invocation proof 后，
  先确认 planned `FenceGraphExecution`，之后才允许 resume/claim/resource/node call；只有
  新建且持有该既有 plan 的 recovery coordinator 可以确认 fence。

这保留了现有 cancellation regression：active state 由 caller/commit owner 保留，下一次
state-only invocation 先 fence，再恢复执行。

**不采纳的扩张。** 不新增“cancelled”持久化状态、close receipt、跨 run cancellation
protocol、worker reclaim 或新的 error class；复审要求的 exact boundary 用已有 errors
表达即可。

处置位置：implementation §4.4、§10.2、§11 Step 4、§13 `LO-B08`。

### LO-RR4 — local frame owner 与 child-boundary producer

**采纳。** 复审指出 `ChildBoundaryAvailabilityCoordinate` 只有 child coordinate，不能
仅靠命名推断 parent owner。实施文档 §6.3.1 选择 private owner-tagged view（不改 sealed
payload），并冻结：

- `_RunLocalFrameView[GraphValueT]` 只持有一个 detached `ScopedFrameIndex[GraphValueT]` 和
  `owner_scope`；不复制 record，不保存第二份 value truth；
- `partition_family_frames(graph, root_scope, root_state, child_states, family_frames)`、
  `frame_view_for(...)`、`merge_local_frames(graph, root_scope, root_state, child_states,
  partitions)` 的输入/输出与纯度已写明；每条 record 恰好进入一个 owner view；
- `_owner_for_record()` 是 partition/merge 共享的唯一纯 owner 判定点，先用 authoritative
  root/child state 校验 revision、status、descriptor，再决定 owner；不按 frame coordinate
  猜 state；
- 已确认 publication/resume record 的 revision 使用现有历史上界（正数且不超过 owner
  当前 revision，activation superstep 不超过 owner 当前 superstep）；只有 candidate
  `AdmittedSubstitution` 使用下一 revision（`owner.state.revision + 1`）规则；
- graph input owner 是 `scope_run`；publication/resume owner 是 activation scope；child
  boundary 虽带 child coordinate，却由 matching binding 的 `parent_activation.scope_run`
  拥有，并要求 `child_scope_run_for_activation(parent_scope, parent_activation)` 与 record
  coordinate 精确相等；child output publication 仍留在 child view；
- `project_missing_child(parent, activation)` 显式接收 parent `_GraphRun`，先校验 activation
  的 run/superstep、nested pending 节点与无 matching handle，禁止用 raw `run_id` 猜 owner；
  若无 handle 却已有 completed boundary，则按 `SnapshotMismatchError` 视为失去 owner 的
  malformed/stale evidence；completed boundary 的幂等复用只发生在 matching
  `project_child(handle)`/`install_child_boundary()` 路径，且仅在 activation、descriptor 和
  output 完全一致时允许，冲突才按 duplicate 失败；
- foreign/descriptor/parent activation mismatch 在 partition 前是
  `SnapshotMismatchError`；candidate duplicate 沿现有 `GraphValuePublicationError`；
  post-confirm add failure 外层包装为 `FrameInstallationInvariantError`；合法但缺值的
  state-only path 是 `GraphValueUnavailableError`；
- candidate resume overlay 复用既有 `CandidateFrameAvailability` shape：resume-input records
  进入各 owner 的 method-local detached index，`AdmittedSubstitution` 保持既有
  substitutions tuple，按 owner 分组后 canonical 合并给 family proof，只读消费后丢弃；
  child completion 由 child project output、parent `_GraphRun.install_child_boundary()` 一次安装
  boundary，live projector 只消费 owner local index，export 时才 merge 回原 family index。

**不采纳的扩张。** 不在 `ScopedFrameIndex` sealed record 上新增 owner 字段，不改变
`project_graph_outputs()`、`StepRequest` 或 continuation payload，也不保留一个无 owner
的共享 mutable family index 作为“local truth”。private view 只是受控 partition descriptor，
不是第二可变 store。

处置位置：implementation §6.3/§6.3.1、§8、§12、§11 Step 2/3。

### LO-RR5 — `test_result_boundary_contract.py` manifest 遗漏

**采纳。** Source scan 的遗漏成立。该测试直接使用 `project_graph_result`、`_new_context`、
`GraphRunContext` 并断言 boundary subclass rejection；runtime context owner 收窄后必须
迁移或证明 exact typed boundary behavior。它已加入 implementation §14.3，并在
producer/consumer graph 与 source/test scan 表中登记；同一 scan 还逐项列出
`test_runtime_boundaries.py`、`test_frame_index_contract.py` 等直接 private consumers，
以及保持 nominal contract 不变的 KEEP consumers。

本轮同步再冻结 result projector 的入口：`project_graph_result(coordinator,
disposition: GraphBoundary) -> GraphResult[GraphValueT]` 是唯一 result assembly boundary；它不接收 raw
`CompiledGraph`/`GraphRunContext`。`drive_root()` 是 invocation 级唯一对外 boundary
producer，且只能返回 public `GraphBoundary`，内部
`WaitingForChildren`/quantum 值不得越过该边界；不支持的 boundary（包括 subclass）在读取
owner/frame 前沿现有 `SnapshotMismatchError` 拒绝。`test_result_boundary_contract.py` 的
fixture 必须通过 approved coordinator factory 构造并调用这一新签名，不得用 forwarding
参数维持旧调用形状。

同时重新界定 manifest：如果最终 source scan 发现其他因 signature/owner 变化受影响的
consumer，必须逐项补入，不能用 forwarding alias 或旧 helper 隐藏；`StepRequest`、
`result.py`、`frontier.py` 的 nominal contract 仍保持。只消费 unchanged
`ScopedFrameIndex`/recovery types 的 engine consumers 和对应 tests 已在 target §14.2/§14.4
列为 KEEP，并写明了重新分类规则。

处置位置：implementation §8.1、§12、§14.3–§14.5、§15 gate 11。

### LO-RR6 — AST-only policy 与 LO-B15/architecture manifest 冲突

**部分采纳（采纳冲突判断，拒绝扩大 gate）。** 复审指出的内部矛盾准确：target 一方面
禁止新增/扩写 AST-only test，另一方面把既有 `tests/architecture/test_graph_execution_ownership.py`
列为 LO-B15 修改证据。按用户边界选择复审给出的第一分支：

1. 既有 architecture AST test 保持不变，不加入本 change unit 的 MODIFY/ADD manifest；
2. 不修改其 source assertions、不申请“不新增 AST gate”的例外；
3. LO-B15 改由 `test_graph_public_typing.py`、`test_graph_api.py` 和
   `test_graph_run_ownership.py` 的 public/typed behavior 证明（不检查 private helper 数量、
   AST 或源码布局）；
4. 若未来确需改变既有 AST contract，另立 requirements/change unit，由 owner 单独批准。

**拒绝的具体内容。** 不接受“为本 target 申请 AST gate 例外”、新增 `_GraphRun`/cross-scope
mutation 的 source-shape 断言，或把既有 AST test 作为本轮通过条件。这不是削弱 ownership
要求，而是避免用与用户明确边界冲突的测试类型证明内部实现布局。

处置位置：implementation §13 `LO-B15`、§14.3、§14.4、§15 gate 11/14、§16 ledger。

## 4. 复审中不纳入的范围扩张

下列建议即使能增强某些失败场景，也不属于本次窄 target，明确拒绝：

| 建议 | 裁决 | 原因 |
| --- | --- | --- |
| persistence、Store、checkpoint、codec、terminal receipt、replay | 拒绝 | 用户明确不做持久化；后续另行设计 |
| 仅凭 `child_run_id` 跨 invocation load/drive/recover | 拒绝 | 本需求是同一 invocation ownership split，不是恢复协议 |
| failover、worker handoff、lease reclaim、global admission registry/optimistic lock | 拒绝 | 无现有 owner，需独立 requirements；overlap 是 caller precondition |
| opaque child slot、live capability、fork、per-run recovery protocol | 拒绝 | 会改变现有 sealed family snapshot/proof shape，且非必要 |
| 新 public `GraphRun`/`GraphRunRef`、State field/status/command、第二 runner | 拒绝 | 违反既定 public/State/engine ownership |
| cross-parent overlap success/rejection test | 拒绝 | 不属于 acceptance；只保留不同 parent ID 的纯 identity 不变量 |
| 新增或扩写 legacy/private-source-shape/AST-only test | 拒绝 | 用户边界；使用 public/typed behavior evidence |

对本轮复审中可能被误读为“必须采纳”的实现推断，再明确三点：

- **不要求给 sealed frame record 增加 `owner` 字段。** `ChildBoundaryAvailabilityCoordinate`
  的 owner 可由唯一 matching `ChildStateBinding.parent_activation` 纯推导；新增字段会改变
  现有 continuation/frame ABI，制造第二份 identity truth。private `_RunLocalFrameView`
  已提供可验证的 owner tag，足以闭合 LO-RR4。
- **不要求把所有 `ScopedFrameIndex`/recovery consumers 改成 per-run API。** 这些基础
  consumers 接收的是已由 owner 选定的 immutable index；强行改动会扩大 KEEP contract，
  也违背“复用基础设计”。只有直接读写 runtime `GraphRunContext` 的消费者才进入 MODIFY
  manifest。
- **不把 callback unknown 解释成可自动 rollback/retry。** exact successor 未返回时只能
  标为 acknowledgement unknown；任何自动重试、receipt 或回滚都需要新的 durable protocol，
  已明确超出本 change unit。

## 5. 修订 target 的变更摘要

本轮 implementation target 的实质新增分为六类：

1. §4.2–§4.3、§7.1.1 的现有 `ContinuationSnapshot`/family seed typed handoff、异步
   factory/operation signatures、provisional owner、唯一 `export_snapshot()` producer 与
   evidence lifecycle；
2. §6.3.1 的 owner-tagged local frame partition、root-state-backed owner mapping、candidate
   overlay、parent boundary producer、missing-child owner 和 export merge invariant；
3. §4.4、§10.2、§10.4 的 disjoint lifecycle phase、orphaned-claim/cancellation 与
   post-confirm failure/error-precedence contract；
4. §8.1、§12–§14 的唯一 result projection boundary、producer/consumer graph、完整 source/test manifest（含
   `test_result_boundary_contract.py`）、family identity propagation 和 KEEP consumer audit；
5. §13–§15 的 public behavior acceptance 与 AST test policy 修正；
6. §14.5 的 requirements → target → evidence traceability，以及无 handle stale boundary 的
   fail-closed 规则。

没有新增 State/reducer、public API、persistence、failover、overlap gate 或第二 execution
engine。production/tests 仍保持本轮未改。

## 6. 最终 ledger 与授权状态

```text
LO-RR1 family evidence typed boundary       = ACCEPTED / FROZEN IN TARGET §4.2/§7.1.1
LO-RR2 post-confirm failure windows         = ACCEPTED / FROZEN IN TARGET §10.4
LO-RR3 cancellation lifecycle phase         = ACCEPTED / FROZEN IN TARGET §4.4/§10.2
LO-RR4 local frame/boundary ownership       = ACCEPTED / FROZEN IN TARGET §4.2/§6.3.1
LO-RR5 changed-file/test manifest           = ACCEPTED / test_result_boundary_contract ADDED
LO-RR6 AST-only policy contradiction         = CONFLICT ACCEPTED / NO AST EXPANSION

parent authoritative child state            = FORBIDDEN
child state binding/projection              = IMMUTABLE TRANSIENT READ-ONLY EVIDENCE
family continuation/recovery shape          = KEEP EXACT
async operation/provisional owner            = FROZEN IN TARGET §4.2/§4.3
frame owner mapping/producer                 = FROZEN IN TARGET §6.3.1
cross-parent overlap                        = CALLER PRECONDITION / NO RUNTIME GATE
persistence / child-ID-only restore         = OUT OF SCOPE
failover / worker handoff                   = OUT OF SCOPE
implementation target                      = REVISED / READY FOR NEXT INDEPENDENT REVIEW
target blockers after this revision         = NONE IDENTIFIED AFTER FINAL CONSISTENCY AUDIT; independent review still required
requirements owner approval                = PENDING
production/test implementation              = NOT EXERCISED BY THIS DOCS-ONLY TURN
production / State / Store / API / tests    = NO CHANGE IN THIS DOCS TURN
complexity gate                             = USER-EXCLUDED / NOT RUN
```

本回复只说明复审意见如何进入窄 implementation target，不替 requirements owner 或用户
授予代码实施授权。下一轮若继续复核，应检查上述 typed contract 与 manifest 是否能直接
实现；不得再以本 change unit 未授权的 persistence、failover、跨 invocation recovery、
global overlap guard 或 AST gate 扩张作为通过条件。
