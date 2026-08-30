# 父子图 GraphRun 本地 ownership 实施方案继续独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮确认了正确的基础方向（child identity 的长度前缀投影、Graph/State/reducer
> 复用和不引入 persistence/failover），但 requirements 与 implementation target 仍有
> 运行时准入、continuation、结果投影、recovery、生命周期和 change-unit manifest
> 未闭合项。它们不是可以在编码时凭经验补齐的细节；在“零负债、唯一真相、复用基础
> 设计”的门槛下，当前不能进入 production/test 实施。
>
> 本评审只新增本文件，不修改目标 requirements、implementation、production、State、
> Store、protocol、public API 或 tests；不新增或扩写 legacy test。complexity gate 按用户
> 要求不参与本轮裁决，也不运行。

## 1. 评审对象与冻结输入

- 评审日期：2026-08-28
- requirements：[父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
- requirements SHA256：`cfb9a941251d69cf20c6090be8c0990a75da1cd7fa1ec4c965566d179cd012b5`
- implementation target：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
- implementation SHA256：`0f557b65d537d696204d470cc12cd63195e429ed3f7827182919d0ceefadd105`
- production baseline：`ebcd043fdfe324c610328a08cb1a3e8a14b37e10`
- 当前项目：`/home/longert/motev2/mote-kernel`

两个目标文件当前是工作树中的未跟踪文件；上述 hash 是本轮实际读取的内容。工作树
另有用户既有的 README、examples、sibling package 和历史 review 修改，均不归因于本
评审。

## 2. 评审口径

本轮只检查可实施性和非复杂度门禁：

- `Graph`、`GraphRunState`、pure reducer、typed command、frame、continuation、session
  和错误分类的既有 owner 必须继续唯一；不能用 alias、第二 runner、generic-erasing
  wrapper 或隐式 registry 填洞。
- requirements 的“当前 API/State/frame/continuation 事实不变”优先于 target 对未来
  内部形状的描述；任何有意改变都必须先回写其 normative source 和 requirements。
- complexity gate、health、ratchet 和 complexity baseline 按用户指示排除，既不作为
  失败理由，也不被写成通过。
- 不新增、扩写或依赖 legacy/private-source-shape/AST-only test。已有测试只能在获批
  后按 public/typed behavior 迁移，不能以保留旧内部路径换取绿灯。
- review 通过也不等于 implementation authorization；requirements owner 批准和用户
  明确批准仍是独立硬门禁。

## 3. 总体复核矩阵

| 维度 | 结果 | 说明 |
| --- | --- | --- |
| child identity 的纯投影 | **通过（局部）** | 长度前缀编码对完整 tuple 是 injective；不能因此推出 admission 唯一性 |
| parent 不拥有 child authoritative state | **方向通过** | 目标方向正确，但 handle/result 的实际 owner 尚未闭合 |
| 相同 parent `run_id` 的重复准入 | **不通过** | 无 commit/无 registry 路径可重复成功，和 LO-B02/requirements 矛盾 |
| continuation 与 frame contract | **不通过** | target 的 opaque slot/回收规则改变当前 canonical snapshot，且没有 slot/fork 协议 |
| result/request/frontier owner | **不通过** | KEEP 清单与“删除 child state mirror”没有唯一选择 |
| recovery proof | **不通过** | 当前 proof 是 family-shaped；per-run seed/evidence/递归消费未定义 |
| lifecycle、partial commit、cancellation、limits | **不通过** | 没有可执行状态机和失败窗口契约 |
| unsupported overlap acceptance | **不通过** | “不检测、检测则拒绝、能构造才测”不是稳定门禁 |
| changed-file/test manifest | **不通过** | 真实 consumer 未全部纳入，KEEP 文件又承载待改变的字段 |
| implementation authorization | **不通过** | 文档自身仍是 `CODE NOT AUTHORIZED`，且未取得 owner/user approval |

## 4. 必须关闭的 blocker

### LO-R1 — identity projection 不能替代重复 `run_id` admission

**证据。** Requirements 第 129–136、193–194 行和 target 第 413–424、607 行同时要求：

1. 不同 parent `run_id` 派生出的 child `run_id` 不同；以及
2. 相同 parent `run_id` 的第二次声明不得作为新的 distinct run 成功接纳。

`child_graph_run_id()`（`src/mote_kernel/state/graph_state/identity.py:36-49`）只是纯
函数，能证明 tuple 投影不歧义，不能知道某个 identity 是否已经被另一次 invocation
使用。`Graph.run()` 的新 values 路径（`src/mote_kernel/execution/facade.py:601-644`）
允许显式 `run_id`，`commit` 还是可选的；target 第 54–56、455–458 行又明确禁止本
change unit 引入 global registry/guard。

本轮只读探针构造同一 parent、同一显式 `run_id="same"` 的两次 fresh invocation，
第二次使用不同输入且不传 commit，结果为：

```text
_CompletedGraphResult _CompletedGraphResult a b same same
```

这说明当前 API 没有任何可观察 admission owner。即使 child ID 的数学投影正确，
LO-B02 所要求的“伪装/重复在 child execution 前 fail closed”仍无法在该路径成立。

**必须整改（二选一，不能含糊保留两种语义）。**

- 把重复 fresh invocation 明确定义为 caller precondition，并删除 LO-B02 的运行时拒绝
  承诺；只保留同一 invocation/continuation 内由本地 owner 检测的重复 activation；或
- 另立 requirements，定义 admission owner/guard 的 key、状态来源、生命周期、exact
  error 和无 commit 时的行为。该 guard 不得偷偷塞入本 target，也不能用随机 ID、alias
  或隐藏 module state 伪造唯一性。

长度前缀 identity 本身应保留；本 blocker 针对的是 admission，不是 identity 算法。

### LO-R2 — source precedence 形成第二事实源

**证据。** Requirements 第 20–24 行规定当前 API、State schema、frame ABI、continuation
和错误文本由各自 normative source 拥有，implementation target 只拥有落地计划。Target
第 83–91 行却写成“若本文与现行 State/API/normative source 冲突，以前者为准”。“前者”
若指本文，就允许 implementation target 覆盖 canonical behavior；若指 requirements，
又与第 86–90 行的 State/execution 排序冲突。

**影响。** 评审无法判断 `child_states`、continuation snapshot 和 error mapping 哪一份
是唯一真相；实现者可以任选一份文字而不被判定为偏离。

**必须整改。** 把优先级写成无歧义的规则：现有事实由 normative source 拥有；approved
requirements 只冻结新增 scope/acceptance；任何有意改变现有行为必须在 requirements、
normative source、target 和 manifest 中同一 change unit 明确回写；target 不得覆盖前两者。

### LO-R3 — continuation slot 与当前 continuation contract 未闭合

**证据。** Requirements 第 170–178 行要求 complete/recovered continuation 的可观察
行为不变。当前 normative source 第 1222–1244 行以及源码
`src/mote_kernel/execution/run_context.py:315-421` 明确规定：

- `_CompleteContinuationSnapshot` / `_RecoveredContinuationSnapshot` 携带 child State
  bindings 和同一 invocation 的 `ScopedFrameIndex`；
- admission 会校验 child scope、State、frame descriptor 和 canonical index；
- 当前 `_GraphContinuation` 没有 fork API，并明确拒绝 copy/pickle/serialization。

Target 第 370–388 行却要求删除 `child_states`、换成 opaque slots、terminal 后丢弃 slot，
并允许 slot 携带 live child capability 或 opaque continuation；第 386 行还要求同一个
输入 continuation 每次 fork 新 `_GraphRun`。

**影响。** “opaque”本身不能告诉 facade 如何按完整 scope 路由 resume，也不能让 recovery
proof 校验 child identity/frame。live capability 不能在两个 invocation 间安全 fork；若
terminal slot 被丢弃，现有 complete continuation 的 child-boundary admission 又失去其
要求的 child State。直接照写会改变 malformed continuation、round trip、partial handoff
和旧 terminal frame retention 语义。

**必须整改。** 先作唯一选择：

- 仅做内部 ownership refactor，并保留当前 sealed continuation snapshot 的 exact shape，
  将 child State 明确标为 immutable transient observation 而非 parent owner；或
- 先更新 normative source/requirements，再定义 sealed `ChildContinuationSlot` 的 nominal
  type、owner、activation/run identity、admit/fork、malformed error、awaiting/terminal/
  partial-commit 生命周期，并同步更新 manifest 和 behavior contract。

不能同时声称“snapshot 形状不变”和“child State/terminal slot 被删除”，也不能用隐藏
mutable capability 或兼容 alias 绕过选择。

### LO-R4 — `result.py`/`request.py`/`frontier.py` 的 KEEP 与无 mirror 目标冲突

**证据。** 当前 canonical types 为：

- `src/mote_kernel/execution/result.py:171-195` 的 `ActiveChild`、`CompletedChild`、
  `AbortedChild` 都直接带 `child_state`；
- `src/mote_kernel/execution/request.py:16-24` 的 `StepRequest` 带
  `child_projections`；
- `src/mote_kernel/execution/engine/frontier.py:47-100` 读取并校验
  `projection.child_state`，再把它转换为 `TaskSuccess`/`TaskFailure`。

Target 第 392–399 行要求继续保持这些 nominal variants，第 647–667 行又把
`result.py`、`request.py`、`frontier.py` 列为 KEEP，同时第 304–320、397–399 行要求
parent 不再读取 child state map、改为消费 handle/result。文中使用的 `ChildResult` 也
没有 nominal 定义、构造者或 consumer。

**影响。** 实现只能在两条不兼容路径中选一条：继续把 child State 放进 transient
projection，或改变 projection/request/frontier 的类型。前者需要明确它不是第二
authoritative store、由谁产生/校验/何时丢弃；后者必然修改 KEEP 文件并更新测试/规范。
此外，`invocation.py:546-562` 的 child-boundary validation 当前还通过 family binding
查 child State，单改 `family_driver.py` 无法闭合。

**必须整改。** 在 target 中给出唯一 nominal contract、producer/consumer、所有权和
retention；若字段或 union 改变，立即把 `result.py`、`request.py`、`frontier.py` 及其
normative source 纳入 manifest。不得以宽 DTO、optional 字段、forwarding wrapper 或
旧字段 alias 暂时兼容。

### LO-R5 — `_GraphRun` 和 child handle 没有可执行生命周期契约

**证据。** Target 第 194–223、230–276、530–557 行只列出 `_GraphRun` 的六个建议字段，
但正文依赖未定义的 `_GraphRun.start_child()`、`confirm_state()`、`resume()`、
`_drive_run()`、`_invoke_child()`、`ChildResult` 和 opaque handle。六个字段没有说明：

- constructor 如何接收 initial state、parent activation 和 family token；
- pending handles 由谁持有、如何按 scope 唯一匹配而不变成 registry；
- claim/session/task 的 close、cancellation 和 terminal ownership；
- child awaiting、completed、aborted 的返回 union；
- commit callback unknown/partial failure 后 state 与 handle 的转移；
- nested recursion 的 owner 与 re-entrancy 规则。

现有 `GraphExecutor`/`ExecutionClaimOwner`/`GraphExecutionSession`（分别见
`executor.py:68-121`、`claim.py:24-111`、`engine/session.py:77-319`）已有 one-shot claim、
single-consumer session 和 cancellation-safe close 语义。没有新的 private protocol，
实现者必然会在 `_GraphRun` 外再造 session/registry，或把 parent context 重新变成第二
owner。

**必须整改。** 冻结一个 execution-private、严格 typed 的 `_GraphRun` state machine
和 handle protocol：constructor、每个 transition 的输入/输出、唯一 owner、session
close 顺序、terminal/awaiting 返回值、partial handoff 和错误优先级都必须写出。若
pending handles 不在 context 中，须明确它们的唯一外部持有者和 canonical tuple 语义；
不得靠“推荐字段”让实现阶段自行发明第二 runner。

### LO-R6 — per-run recovery proof 没有输入/输出定义

**证据。** 当前 recovery 仍以 family 为单位：

- `src/mote_kernel/execution/invocation.py:203-424` 的 `lineage_states()`、
  `plan_fences()`、`plan_resumes()` 和 `recovery_seed()` 组装 root + children；
- `src/mote_kernel/execution/engine/recovery.py:307-367` 定义
  `RecoveryInvocationSeed` 和 `_RecoveryFamily.bindings`；
- `recovery.py:996-1162` 的 proof 递归读取这些 bindings；
- `invocation.py:450-562` 的 frame validation 通过 child scope 找对应 State。

Target 第 315–320、481–495、553–557 行要求改成 per-run seed、opaque child slots 和递归
消费，却没有定义 seed/evidence type、child proof 的返回值、parent 如何合并 availability、
grandchild/sibling 的 canonical order，或 slot 缺失时的 owner。opaque slot 又不能被
当前 `preflight_recovery()` 直接检查。

**影响。** LO-B06/LO-B09/LO-B12、state-only pending child、partial commit 和三层
grandchild 的“早于 fence/start/claim fail closed”无法证明；保留 family proof 则没有
达到目标的 ownership 拆分，删除 bindings 则失去现有 recovery invariant。

**必须整改。** 二选一：保留当前 family-shaped continuation/proof，并把本 target 限定为
不改变其输入形状的内部 owner 重构；或定义 per-run sealed recovery evidence（包括
scope/run/definition、availability、child disposition、递归结果和错误），由 child owner
先 proof、parent 只消费 typed evidence。必须给出 exact traversal/dedup/order 和
mutation-before-proof 的禁止点，不能在 `recovery.py` 里隐式反射 opaque slot。

### LO-R7 — child failure/interrupt/awaiting 的递归结果投影没有闭合

**证据。** 当前 `family_driver.py:456-515` 的 `_project_result_views()` 遍历 root 和
全部 child State，按 scope 顺序产生扁平 `GraphFailureView`/`GraphInterruptView`；公共
`GraphResult` 仍只有 `Completed/Aborted/AwaitingResume` 三个 closed variants
（`result.py:321-363`）。Target 第 397–399、509–523 行改为“递归读取 child returned
view”，但没有说明：

- `ChildResult` 是复用 `GraphResult`、`TaskResult` 还是新的 private union；
- child 的 nested failure/interrupt 如何进入 root awaiting view，且如何保持 root → child
  → grandchild canonical ordering；
- child awaiting 时 parent continuation 如何保留 pending activation；
- child terminal output、aborted reason 和 parent `ConfirmedChildBoundary` 的重复/丢失
  检查；
- sibling 同时返回不同 boundary 时的错误优先级。

**影响。** 不能证明 public result 三个 closed variants 的字段和可观察行为保持不变；
尤其不能从“只传 typed transient value”推出失败/interrupt 不会被吞掉或重复投影。

**必须整改。** 定义唯一的内部 child-return union（或逐项证明复用现有 union），给出
producer、consumer、递归排序、重复/foreign view 错误和 slot disposal 时点；再把它映射
到现有 `Graph.Result`/`ChildProjection`，不得新增宽的 public DTO。

### LO-R8 — terminal、partial commit、cancellation 和 limits 缺少状态机

**证据。** Target 第 468–475、477–523 行只给出顺序叙述：child terminal → transient
view → parent settlement → routing。没有裁决以下窗口：

- child terminal 已确认但 parent `SettleGraphNode` callback 失败或返回 unknown 时，child
  handle 是否保留、是否可重试、parent continuation 如何 handoff；
- parent frame install 失败、child session exception、parent cancellation 时，哪一方
 先 close，是否需要 fence，取消期间再次取消如何传播；
- awaiting child 的 continuation 与 live session 的所有权交接；
- `max_supersteps` 是每个 `_GraphRun` 还是整个 invocation 的上界，
  `max_parallel_tasks` 在 parent/child 串行策略下如何解释；
- `_PartialCommitError`（`result.py:40-75`）固定的 root state/continuation/failed scope
  如何承载已确认 child handles。

**影响。** “保持当前 session/lifecycle 语义”不是可执行契约；任一异常窗口都可能留下
active lease、丢失 transient output 或重复 parent settlement，违反 exact successor 和
zero-debt 要求。

**必须整改。** 写出 `created → started → running → awaiting/terminal → parent-settled →
closed` 的唯一状态机及每条异常边；固定 fencing、close、handle retention/disposal、
partial handoff 和 limits 语义，并以 behavior/fault tests 覆盖。不要借此引入 persistence、
replay、failover 或新的 cancellation protocol。

### LO-R9 — unsupported overlap 的 acceptance 不是确定性门禁

**证据。** Requirements 第 123–127、198–206 行和 target 第 430–458、622、714 行同时说：

- 跨 parent 重叠复用同一 compiled child 是非法/不支持；
- kernel 不要求检测所有 overlap；
- 若“现有边界”观察到冲突必须 fail closed；
- LO-B17 只有在 harness 恰好能构造冲突时才测试。

这使同一调用在不同 commit/调度路径上可能“成功但不受支持”或“被拒绝”，而 gate 没有
固定检测点、owner、error type、时序和可重复 fixture。它也和 target 第 58–63 行的
identity 保证混在同一段文字中，容易把纯 ID 证明误写成并发 admission 证明。

**必须整改。** 从本 target 的 required behavior 中完全移除 overlap runtime case，只
把它写成 caller precondition；或另立一个 guard requirement，定义 deterministic guard
和 typed rejection。保留 LO-B02 的不同 parent ID 纯投影/顺序行为测试，但不得用条件性
LO-B17 作为实施准入证据。

### LO-R10 — changed-file/test manifest 没有覆盖删除闭包

**证据。** Target 第 302–324、638–693 行要求 production 对
`ChildStateBinding`、`child_states`、`state_at`、`replace_state`、`replace_child` 的
引用扫描归零，却把 `result.py`、`request.py`、`frontier.py` 列为 KEEP。实际扫描还显示：

```text
tests/execution/engine/test_runtime_boundaries.py:103,1334-1342
tests/execution/test_graph_api.py:32,995,1160-1365,2586
tests/execution/test_continuation_integrity.py:23,1037-1113
tests/execution/test_frame_index_contract.py:65-69
tests/execution/driver.py:35-95
tests/architecture/test_source_discipline.py:197-222
```

其中 `test_runtime_boundaries.py` 直接构造 `ChildStateBinding`/`replace_child()`，但不在
第 13.2 节测试 manifest；`test_source_discipline.py` 对 `StepRequest` 字段做 exact
assertion，`tests/execution/driver.py` 也直接构造 `StepRequest`。若 projection/request
形状变化，当前列出的文件不足以迁移所有 consumer；若形状不变，target 又必须明确
child_state 只是 transient observation，不能同时声称它已被删除。

**影响。** 删除闭包无法在一个窄 change unit 中验证，最容易出现“旧路径留作兼容”或
“遗漏测试后假绿”的新负债，违反不加 legacy test 与唯一 owner 要求。

**必须整改。** 以当前 source/test 搜索结果反向生成完整 manifest，逐文件写明 producer、
consumer 和迁移动作；需要改变 `result.py`/`request.py`/`frontier.py` 时先解除 KEEP
裁决。manifest 闭合后再写 exact behavior tests；不得新增 AST/旧符号兼容测试来代替行为
证明，也不得漏列 `test_runtime_boundaries.py` 等真实 consumer。

## 5. 已确认的正向事实（不等于通过）

- `child_graph_run_id(parent_run_id, superstep, node_id)` 的长度前缀编码对完整 tuple
  是 injective；不同 parent ID 的纯函数投影探针返回 `True`。这部分应复用，不应改成
  截断、随机分配或全局 ID。
- `GraphRunStatus` 当前仍只有 `RUNNING / COMPLETED / ABORTED`；target 没有要求把
  awaiting 伪装成新 status。
- `Graph` 仍是唯一 public facade，target 明确排除了 persistence、Store、checkpoint、
  worker handoff、failover、第二 scheduler/runner 和 global registry（但 LO-R1/LO-R9
  说明重复/overlap admission 文字仍需重新裁决）。
- target 有 producer/consumer 表、behavior matrix 和 docs-only 边界，且明确不新增
  legacy test；问题在于这些表目前没有闭合到唯一 nominal contract 和完整 manifest。

## 6. 本轮验证记录

| 检查 | 结果 |
| --- | --- |
| requirements SHA256 | `cfb9a941251d69cf20c6090be8c0990a75da1cd7fa1ec4c965566d179cd012b5` |
| implementation SHA256 | `0f557b65d537d696204d470cc12cd63195e429ed3f7827182919d0ceefadd105` |
| source symbol scan | 仍能在 production 与上述未完整列入的 tests 中找到旧 family symbols/consumers |
| different-parent identity probe | **PASS（纯投影）** |
| same-parent same-`run_id` fresh invocation probe | **两次均成功**；证明 LO-R1 尚无 runtime admission owner |
| targeted current behavior tests | **3 passed**：nested facade、missing child snapshot、shared executor concurrent ordinary runs |
| complexity gate | **未运行 / USER-EXCLUDED** |
| legacy/private-source-shape test | **未新增、未扩写** |
| Markdown 相对链接 | **12 checked / 0 missing**（requirements、target、review 三份文档） |
| Markdown EOF/CRLF/trailing whitespace | **通过**：三份文档均有 EOF newline、无 CRLF、无 trailing whitespace |
| production/State/Store/protocol/tests edits | **未做** |

本轮没有把当前 dirty worktree 的全量 lint、coverage、build 或 `make check` 当作 target
证据；`make check` 无条件包含被用户排除的 complexity gate。代码获批后仍需按最终 manifest
记录适用的 strict typing、lint、format、behavior、coverage、build/package、pre-commit
和 Markdown 完整性结果，但不得伪写成 complexity 已通过。

## 7. 达到开发条件的必要顺序

当前不能开发。至少要按以下顺序完成并重新评审：

1. 关闭 LO-R1：明确重复 `run_id` 是 caller precondition，或建立另一个经批准的
   admission requirement；不能保留无 guard 的 fail-closed 承诺。
2. 修正 LO-R2 的 source precedence，确定当前 normative behavior、approved requirements
   和 implementation plan 的唯一关系。
3. 关闭 LO-R3/LO-R4：在“保留现有 continuation/projection contract”和“引入新的 sealed
   slot/result contract”之间做唯一选择，并同步 normative source、owner、producer/
   consumer、删除闭包和 manifest。
4. 关闭 LO-R5/LO-R6/LO-R7/LO-R8：冻结 `_GraphRun`、handle、child result、per-run
   recovery evidence 和 lifecycle/limits 状态机；为 malformed、awaiting、failure、
   cancellation、partial commit、grandchild 和 duplicate transition 写 exact behavior
   matrix。不得新增 persistence/failover/第二 runner。
5. 关闭 LO-R9/LO-R10：把 overlap 处理改成确定性 caller contract 或独立 guard，并以
   source scan 反向补齐所有真实 consumer；不得靠兼容 alias 或 legacy test 过门。
6. requirements owner 先批准 `GRC-LO-001` 及修订后的 target；随后取得用户明确的
   implementation approval。只有这两项都存在，才可修改 production/tests。
7. 获批后按窄 manifest 实施并记录非复杂度门禁；complexity gate 继续按用户范围单独
   标记 `USER-EXCLUDED / NOT RUN`，不新增或扩写 legacy/private-shape test。

## 8. 最终裁决与本评审 manifest

```text
technical blockers                 = 10 (LO-R1 … LO-R10)
child identity pure projection     = PASS (局部；不是 admission proof)
requirements/implementation target = CHANGES REQUESTED / NOT READY
current Graph/State/reducer owner  = KEEP
continuation/result/recovery       = NOT CLOSED
cross-parent overlap               = NOT A DETERMINISTIC GATE
complexity gate                    = USER-EXCLUDED / NOT RUN
legacy tests                       = NO NEW / NO EXPANSION
production / State / Store / API   = NO CHANGE / NO AUTHORIZATION
requirements owner approval        = PENDING
user implementation approval       = PENDING
```

本轮唯一 change-unit manifest：

```text
mote-kernel/docs/graph-independent-run-context-local-ownership-implementation-review.zh-CN.md
```

**最终结论：** line 129 的 identity 数学不变量和 line 58 的复用方向可以保留，但它们
不能掩盖重复 run admission、opaque continuation slot、family-to-per-run recovery、
result projection、生命周期和 manifest 的缺口。上述问题关闭并取得双重批准前，不能
进入开发。
