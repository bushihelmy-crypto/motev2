# 父子图 GraphRun 本地 ownership 实施规范第十三次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本版不是方向倒退。第十二次提出的七项中，canonical postorder、typed cleanup report、
> owner-local context、source scan 和 cancellation 测试 manifest 都已明显收敛；剩余问题
> 已压缩为少数真正影响实现闭合的调用/类型缺口。由于这些缺口仍会让实现者自行选择
> owner、顺序或异常来源，本轮还不能交付 production/test 实施。

本文件是 docs-only 的独立评审记录。不修改 production、State、reducer、Store/persistence、
protocol、public API、continuation/frame ABI 或 tests；不新增 persistence、failover、worker
handoff、仅凭 `child_run_id` 的跨 invocation recovery、overlap gate、第二 runner 或 public
handle。工作区中的用户修改全部保留。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `61300c0cb9649b1196e88e67b8aa622f0ecf91e4be9c49a4786244ebaa3f6b2b` |
| target 行数 | 1983 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第十二次独立评审](graph-independent-run-context-local-ownership-implementation-twelfth-review.zh-CN.md) |
| 上一轮评审 SHA256 | `028cdae0182bbdeb9b329b897b7c17eb931df0941182bd10bd83acb388075e80` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-30 |

本轮继续以 requirements 的窄范围为准：每个图调用拥有自己的 `_GraphRun`、state、
transition、commit、session 和 local frame；parent 不保存、查询或控制 child `run_id`，
只在当前调用栈使用 opaque handle；child 先完成自己的 exact commit/projection，parent 再
结算自己的 nested node；invocation-level cancellation 沿 live handle 向下传播，各 owner
独立使用既有 `AbortGraphRun`。不做持久化、failover 或仅凭 `child_run_id` 的跨 invocation
恢复。

## 2. 本轮与第十二次相比的真实收敛

| 第十二次问题 | 本版状态 | 结论 |
| --- | --- | --- |
| LO-IR58 递归 handoff | 已大部修订 | 仍有字段/调用签名和 fresh order 缺口，见 LO-IR65 |
| LO-IR59 scope commit/context | 已补 nominal 类型 | 与既有 `commit_transition` 的适配及 owner proof 仍未闭合，见 LO-IR66 |
| LO-IR60 awaiting evidence | 已补 staging envelope | slot 到 evidence 的 typed 关联和 terminal 分支仍未闭合，见 LO-IR67 |
| LO-IR61 reverse order 反例 | **已关闭** | 明确生成 `child_first_postorder`，不再 reverse key（target L1405–L1412） |
| LO-IR62 cleanup report | **基本关闭** | 三条外层路径均收集 report；仍有 cancellation owner 调用细节，见 LO-IR68 |
| LO-IR63 cancellation manifest | **基本关闭** | 旧 orphan-claim 主断言已允许迁移；standalone 语义仍需与 requirements 统一，见 LO-IR68 |
| LO-IR64 source scan | **已关闭** | scan 已加入 `child_state()`、`child_states`，并有逐项表（L1747–L1791） |

因此，本轮剩余不是“又新增七类需求”，而是三组实现闭合问题及一组取消/异常边界：
递归/动态 order、scope capability 接入、awaiting 关联、cancellation/type union。

## 3. 仍开放的阻塞项

### LO-IR65 ——递归 admission 的字段交接与 fresh owner order 仍不具备唯一调用图

本版已经把 `append_subtree()`、`seal()` 和未登记清理写进 nominal contract（L115–L210），
但实际调用仍有不一致：

1. `_ChildCallAdmission` 明确包含 `_discard_unregistered_once`（L115–L123），而
   L729 和 L1247–L1250 的 `_ChildCallAdmission(...)` 构造示例仍只传
   `handle, export_provider, plan_provider, _admit_descendants`，没有说明 discard closure
   从哪里进入。`_RecursiveAdmissionResult` 也带同一字段（L194–L201），但
   `append_subtree()` 如何合并多个 descendant cleanup closure 没有 nominal 规则。
2. `acknowledge_start_and_register(child_admission.handle)`（L470、L544）只接收 handle；
   pending candidate、acknowledgement mask 和 `_discard_unregistered_once` 并未作为参数或
   当前 frame 的显式绑定传入。这样无法按字面证明 register/append 失败时清理的是同一个
   subtree，而不是另一个 candidate。
3. `canonical_owner_order(sequence, "root_first")` 的返回值是包含 root 的完整 sequence
   （L248–L265），但 admission 主图写的是“每个 direct branch”并直接迭代该返回值
   （L455–L466）；root 的 `input` 是 `None`，会与 `branch.input.binding` 的访问冲突。递归
   示例改用 `this_owner.direct_children`（L530–L547），但该字段不在任何 private contract
   中，也没有从 sequence 得到 direct-child tuple 的操作。
4. fresh path 只写“复用同一 handoff”（L549–L554、L1240–L1245），没有给出
   `build_canonical_owner_sequence()` 的 fresh producer 或新增 child activation 时如何更新
   invocation 的 `owner_order`。同一 parent 在不同 superstep 可能创建新 child（requirements
   §4.3），而本版又规定 sequence 在首次完整 owner tree 形成后只生成一次、之后不重建
   （L1405–L1412）。如果初始 sequence 不含尚未发现的 nested owner，后续 partial/abort
   就没有可迭代的 order；若每次重建，又违反该冻结规则。

**关闭条件：**

- 让 `_ChildCallAdmission`、`_RecursiveAdmissionResult`、candidate、register 和 discard
  closure 的字段/参数逐字一致，并定义 subtree cleanup closure 的组合与失效点；
- 提供 `direct_children(sequence, owner)` 或等价的唯一 private operation，明确 root 是否
  排除；不得把 `this_owner.direct_children` 当作未声明字段；
- 给出 fresh activation 的 order producer/增量规则，覆盖 conditional nested node 和跨
  superstep 新实例；同一 invocation 内只能有一个 order owner，admission、slot、export、
  partial、abort/release 均消费同一已验证 sequence，不建立 child-ID map 或第二 registry。

### LO-IR66 —— scope-bound commit 已定义，但尚未接入既有 commit port 和 owner proof

本版新增 `_ScopeBoundCommit`/`_ScopeBoundCommitBinder`（L125–L140、L653–L746），方向正确；
但仍有两个实施者必须自行决定的边界：

- 既有 production `GraphCommit.__call__(transition)` 与 `commit_transition(..., commit)`
  的 nominal 形状仍是单参数 callable（`src/mote_kernel/execution/family_driver.py:115-147`）。
  target 的 `_ScopeBoundCommit.commit(transition, owner_state)` 却是双参数方法，文档没有给出
  该 bound capability 如何通过既有 `commit_transition`，也没有给出唯一的 private adapter。
  直接把它当 `GraphCommit` 传入会失败严格类型检查；另造第二个 commit port 又违反 KEEP
  existing port 的边界。
- `bind(owner_scope)` 接收任意 `ScopeRunCoordinate`（L135–L140），`install_owner_context(partition)`
  也没有 expected owner scope 参数（L149–L154）。文档靠 prose 说 factory 只能绑定自己
  的 scope，但没有一个 proof/token 或 expected-scope 参数把该约束落实到调用点；错误的
  partition/binder 仍可被传入 child owner。

另外 `from_raw(raw_commit: GraphCommit[T])` 未说明 `Graph.run(commit=None)` 的路径，不能用
省略分支替代 nominal contract。

**关闭条件：** 保留一个既有 commit port，通过唯一 private scoped adapter 将 raw callable
映射为 owner-local call；明确 `None` commit 的 no-persistence/in-memory 路径。`bind()` 和
`install_owner_context()` 必须接收/验证当前 owner 的不可伪造 proof 或 expected scope，且
所有 `_GraphRun` transition/commit 调用都只能经该 adapter；不新增 public protocol、State
字段或 Store 协议。

### LO-IR67 —— awaiting evidence 与 slot 的对应关系、terminal result 分支仍不完整

本版把 staging 顺序写成 `classify → consume_once → project → collect → destroy`
（L1284–L1299），这是有效进展；但 typed 输入仍不足以实现：

1. `_ChildHandleSlot` 只有 `parent_activation`、opaque handle 和 projection（L212–L216），
   `_OwnerExport` 只有 `owner_scope`、binding、state、frames（L1161–L1167）。
   `collect_awaiting_views(awaiting, envelope.records, owner_order)` 声称“取对应 owner”
   （L1398–L1403），却没有纯 matching operation 说明如何在不读 child ID、不建 map 的情况
   将 direct slot 关联到 owner evidence。`owner_order.root_first` 还包含 root，而 slots
   只包含 direct child，不能默认按相同 tuple index 配对。
2. `project_graph_result()` 的 `disposition` 可以是 `_ChildSlotDisposition | GraphBoundary`
   （L1229–L1237），但 staging 图仍无条件调用 `classify_child_slots(slots, owner_order)`
   （L1284–L1293、L1448–L1454）。`CompletedGraph`/`AbortedGraph` 没有 slots 时，输入来源、
   是否消费 export envelope、何时 collect/destroy 都未给出分支契约。
3. 对 runnable/awaiting 混合 sibling，文档规定 runnable 优先，但没有说明当
   `project_waiting_children()` 返回 `WaitingForChildren` 后是否继续 drive、何时再次消费同一
   envelope，以及 terminal settlement 与 result projection 的边界。不能只依赖 prose 的
   precedence。

**关闭条件：** 定义一个不使用 child-ID map 的纯 slot/evidence matching operation（例如由
   owner tree 的已验证位置/activation proof 产生），明确 root 排除规则；为
   `_ChildSlotDisposition` 与 terminal `GraphBoundary` 分别给出 result boundary 分支。一个
   envelope 必须在单一 boundary 内有明确的借用次数、异常路径和 destroy 点，且不从 parent
   mutable context 旁路读取 child state。

### LO-IR68 —— cancellation 的 owner 次序、explicit abort 和 standalone 语义仍有冲突

cleanup report 的返回类型已经补齐（L888–L1044），但 cancellation 主图仍有三个冲突：

- `child_first_postorder` 明确定义包含 root（L248–L253、L1405–L1408）；
  `abort_all(order)` 却先“按 order 处理 live child”，随后又“无条件处理 root”
  （L1125–L1133）。若不显式排除 root，root 会被执行两次；若排除，排除规则和
  `_parent_abort_invocation` 祖先链的顺序没有 nominal 定义。target 同时要求每个 owner
  最多一次（L1129–L1132）。
- 文档说非 `CancelledError` 的 explicit invocation-abort 由 lifecycle classifier 构成同一
  signal（L1141–L1143），但唯一外层 caller 只展示 `except CancelledError` 和通用
  `except BaseException`（L1080–L1113）；通用分支只 drain/release，不调用 `abort_all()`。
  需要一个可达的 classifier→abort 分支，而不是孤立说明。
- requirements §4.4/§6 要求 invocation cancellation 对 parent 和 live child 都执行
  `AbortGraphRun`；target L1563–L1564、L1933–L1935 又规定 standalone、无 child 时保留旧
  state-only contract。若 standalone root 也是当前 `GraphRun` owner，这两条语义不一致，
  现有旧测试的“保留 claim、下一次 fence/recover”也不能同时作为新语义证据。

此外，`_OpaqueChildCallHandle.abort_invocation(cause, reason)` 没有接收 order（L104–L108），
而 coordinator 需要把同一 postorder 传给递归 descendant；handle 如何消费外部 sequence、
并返回 child cleanup report，仍未写成唯一调用。

**关闭条件：** 明确 owner sequence 中 root、direct child、nested parent 的去重与祖先延续
规则；为 explicit abort 写出与外部 cancellation 相同的可达 caller 图；统一 standalone 是否
执行 `AbortGraphRun`，并更新 manifest 的期望 transition/token 序列。handle/coordinator 必须
有唯一的 sequence/report 交接，不展开 child identity 或建立全局协调器。

### LO-IR69 —— `_NodeCancelled` union 尚未覆盖 scheduler/session 的完整 nominal 链

Target L1046–L1078 定义了 `_NodeCancelled`、`_SchedulerEventError` 和 `_SessionErrors`，
但没有把 union 接到 scheduler/session 的每个生产和消费签名。当前 production 仍是：

- `_capture()` 返回 `TaskResult | TaskRaised` 且只捕获 `Exception`
  （`src/mote_kernel/execution/engine/scheduler.py:80-86`）；
- `TaskScheduler._live`、`_events`、`next_completion()` 仍只承载
  `TaskResult | TaskRaised`（`scheduler.py:96-117,130-148`）；
- session `_errors` 和 `_record_error()` 仍是 `Exception`，`_next_event()` 也没有
  `_NodeCancelled`（`src/mote_kernel/execution/engine/session.py:117,232-244`）。

更根本的是，简单地在 `_capture()` 外层 `except asyncio.CancelledError`（target L1057–L1063）
无法区分 node callable 自己抛出的取消与 session/aclose 对同一个 task 注入的取消；两者都会
在同一个 await 边界出现。target 声称 scheduler“不捕获等待方 task 的取消”（L1070–L1077），
但没有给出可执行的 source marker/任务包装或捕获位置。

**关闭条件：** 给出最终的 scheduler event、task handle、pending event、session error
container、`drain_pending_events()`、`next_completion()` 和 `_record_error()` 全链签名；定义
node-origin 与 waiter/task-origin `CancelledError` 的可判定边界。保持 `TaskRaised.error`
与 public result/error shape 的既有类型，不把外部 invocation cancellation 错归为
`_NodeCancelled`，并补齐对应测试矩阵。

## 4. 已对齐且本轮不应重新扩张的边界

- parent 不需要知道、保存或查询 child `run_id`；child owner 自己创建和校验 identity。
  不同 parent identity 下的 non-collision 是 child identity proof，不是 parent lookup。
- 同一 immutable `CompiledGraph` 的跨 parent 重叠复用仍是 caller precondition；不新增锁、
  registry、overlap gate 或拒绝逻辑。
- parent 不维护 child authoritative state 镜像；continuation binding/frame 只作 immutable、
  transient transport/validation evidence。
- child exact commit 后 parent 失败保留 child confirmed facts；不跨 owner 回滚、重试或 failover。
- invocation cancellation 可以向下传播，但 parent 没有 child 状态提交权；各 owner 独立使用
  既有 `AbortGraphRun`。typed child failure 和 ordinary node exception 不广播 sibling。
- State schema、reducer、Store protocol、public API、continuation/frame ABI 和 public result
  shape 保持不变；不新增跨 invocation load/recovery、persistence、relay、receipt 或第二 runner。

## 5. 验证记录

本轮完成只读核对：

```text
target 行数         = 1983
target SHA256       = 61300c0cb9649b1196e88e67b8aa622f0ecf91e4be9c49a4786244ebaa3f6b2b
requirements SHA    = 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6
previous review SHA = 028cdae0182bbdeb9b329b897b7c17eb931df0941182bd10bd83acb388075e80
production HEAD     = ebcd043fdfe324c610328a08cb1a3e8a14b37e10
```

未运行或修改 production、State、Store、API 或 tests；未清理、重置或覆盖工作区用户改动。
进入编码前须先闭合 LO-IR65～LO-IR69，再进行一次独立 implementation review；通过后仍须
按 `AGENTS.md` 运行 strict typing、`make check`、针对性行为测试、build/package、仓库级
pre-commit 和 Markdown 检查。complexity gate 继续按用户范围记录为
`USER-EXCLUDED / NOT RUN`。

## 6. 最终裁决

```text
ownership direction                  = CORRECT
distance from twelfth review        = SUBSTANTIALLY CLOSER
LO-IR61 canonical postorder         = CLOSED
LO-IR62 cleanup report               = MOSTLY CLOSED
LO-IR63/64 manifest and source scan  = MOSTLY/CLOSED IN DIRECTION
LO-IR65 recursive/fresh order        = OPEN
LO-IR66 scoped commit integration    = OPEN
LO-IR67 awaiting matching/result     = OPEN
LO-IR68 cancellation semantics       = OPEN
LO-IR69 scheduler/session typing     = OPEN
persistence/failover expansion      = OUT OF SCOPE
production/test coding authorization = NOT GRANTED
```

结论：**本版已经接近实施闭合，但还不能交付编码；吸收 LO-IR65～LO-IR69 后再做最终独立
准入评审。**
