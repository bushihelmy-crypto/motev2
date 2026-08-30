# 父子图 GraphRun 本地 ownership 实施规范第十次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前版本相较第九次已经明显更接近闭合：LO-IR45 的 boundary 顺序、LO-IR46/47 的
> factory/identity/owner 绑定、LO-IR48 的取消分类骨架、LO-IR49 的 confirmed-prefix 方向和
> LO-IR51 的 manifest 都已回写。剩余问题集中在递归 admission 的真实调用路径、frame adapter
> 签名、partial commit 的 owner 来源，以及 cancellation 的竞态/严格类型；它们仍会迫使实现者
> 自行选择 owner、调用者或失败语义，因此暂不能开始 production/test 编码。

本文件是 docs-only 评审记录。它不修改 requirements、production、State、Store、protocol、
public API、continuation/frame ABI 或 tests；不增加 persistence、failover、worker handoff、
仅凭 `child_run_id` 的跨 invocation recovery、overlap gate、第二 runner 或新的 public type。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `e70f9d8fecbd7be8ec0d2ee2caa09bdaae99f364014eb30f38bd9e0cb40579de` |
| target 行数 | 1152 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第九次独立评审](graph-independent-run-context-local-ownership-implementation-ninth-review.zh-CN.md) |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

基线行为检查：

```text
python -m pytest -q tests/execution tests/state/graph_state \
  tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider
→ 804 passed in 1.99s
```

该结果仅说明当前未修改源码的既有行为；本评审没有以它替代新 owner boundary 的实现证据。

## 2. 与第九次相比是否更接近闭合

答案是：**是，且是实质性接近，不是只增加文字长度。**

| 上轮问题 | 本轮状态 | 依据与剩余点 |
| --- | --- | --- |
| LO-IR44 continuation export/admission | **大部关闭** | 新增 provider、admission adapter、root export 和一次性 collect；递归 owner admission 仍未真正闭合，且 partition 签名冲突 |
| LO-IR45 child boundary | **已关闭（方向）** | §2.3B、§3、§4.1 都写出 `CompletedChild → install → re-prepare → claim` |
| LO-IR46 identity caller | **大部关闭** | recovery/frontier/projector 的迁移职责已列出；binding 类型转换和 primitive caller ledger 仍需统一 |
| LO-IR47 factory/limits/executor | **基本关闭** | parent scope 单一来源、fresh executor/claim、commit capability 已冻结 |
| LO-IR48 cancellation | **大部关闭** | registration、class-2、scheduler/session origin、safe join 已补；nested 无 live child、ack/register race、Awaitable 类型仍未闭合 |
| LO-IR49 partial commit | **方向关闭** | §2.3D 与 §5.3 已统一 confirmed-prefix 规则；owner-plan 如何取得各 `_GraphRun` 仍缺调用点 |
| LO-IR50 waiting/result | **基本关闭** | private shape、missing precedence、awaiting projection 已明确 |
| LO-IR51 manifest | **基本关闭** | `state/graph_state/__init__.py`、scheduler/session 及相关测试已逐项列出 |

所以当前剩余缺口已经从“ownership 方案是否成立”收敛为“递归调用图和 typed 生命周期是否
能够按字面实现”。这也是为什么结论仍未通过，但不需要恢复历史两千行设计或扩张用户范围。

## 3. 本轮仍需修订的阻塞项

### LO-IR52 — continuation admission 的递归 owner 路径和 active handle 对应仍不完整

Target §2.1 的 admission 图（约第 209–228 行）写成：

```text
for each binding:
    parent_factory.admit_existing(...)
```

但 §2.3A 又规定 factory 在构造时只绑定一个 `parent_scope_run`（约第 283–348 行）。因此
同一循环无法处理 grandchild：grandchild 的 `binding.parent_activation.run_id` 属于 child
scope，不属于 root factory 的 bound parent scope。`_ChildCallAdmission` 只返回
`handle + export_provider`，没有把“下一层应由哪个 child factory admission”交出来的递归
操作；“按 depth-first”本身不是调用路径。

同一问题也出现在正常运行：`WaitingForChildren.active_children` 只有 projection，projection
没有 handle。文档没有规定 parent 如何在不读取 child ID、不建 map 的前提下，把多个 active/
awaiting/completed projection 与当前 invocation 的 opaque handle 一一对应，或如何处理一个
child awaiting、另一个 child runnable 的混合情况。若实现者自行按 child ID 建索引，就违反
本需求；若按位置猜测，又无法证明 canonical sibling 行为。

另外，`ChildStateBinding.parent_activation` 的现有类型是 `StableActivation`，而
`admit_existing()` 签名要求 `ParentGraphActivation`；第 2.1 图直接传入前者（约第 221 行），
严格类型和字段语义都不成立。

**必须闭合：** 给出一个递归的 private admission/drive contract：每一层 owner 如何创建并
继续使用自己的 factory，如何以 parent-owned activation 或 canonical ordered handle tuple
关联 projection，如何在 mixed awaiting/active sibling 下保持既有结果优先级。允许 transient
method-local tuple/closure；不允许 child-ID map、registry、持久化或 public handle。并明确
`StableActivation → ParentGraphActivation` 的纯转换与 proof 位置。

### LO-IR53 — `partition_family_frames()` 的声明与所有调用仍然不一致

Target §2.3B（约第 375–397 行）声明：

```text
partition_family_frames(
    graph, root_scope, root_state, child_states, family_frames
) -> tuple[_LocalFramePartition, ...]
```

但 admission/export 路径分别调用：

```text
partition_family_frames(evidence_context)
partition_family_frames(owner_exports)
```

而 `_OwnerExport` 又已经成为新的 evidence 输入。当前文档没有说明这是 overload、包装器
还是旧签名应删除。这个冲突会直接决定 adapter 是否读取 `GraphRunContext.child_states`，
以及是否能在不恢复 parent mutable mirror 的前提下校验 compiled graph/descriptor。

**必须闭合：** 只保留一个 nominal typed signature，并在 admission、normal result、awaiting
和 partial 四条路径使用同一入口；明确 graph topology/owner export evidence 的来源、一次
partition/merge 的调用次数和既有错误优先级。不能用未声明的 overload 或第二套 helper 绕过
该冲突。

### LO-IR54 — existing partial commit 仍没有取得 owner 的唯一调用点

Target §2.3D、§5.3 已正确删除 `context.state_at(child)`/`replace_state(child)`，并改用
`owner_plan`、`owner.export_exact()` 和 `_ConfirmedPrefix`（约第 618–657、869–885 行）。
但当前 admission contract 的返回值只有：

```text
(root_run, coordinator, root_export_provider)
```

coordinator 被明确禁止保存 child owner；provider 只负责读 evidence，也没有 commit operation。
因此 facade 的 recovery loop 没有一个已定义的值可以让它：

- 找到某个 `owner_plan` 对应的 `_GraphRun`；
- 对该 owner 使用自己的 scope-bound commit 执行 fence/resume；
- 在 exact acknowledgement + frame install 后调用该 owner 的 `export_exact()`；
- 在 callback-after-commit、non-exact successor 或 frame failure 时保留正确的 last-exact owner。

**必须闭合：** 写出 facade/recovery 到 owner-local plan 的唯一 method-local 交接（可以是递归
operation closure 或按 parent activation 排列的 transient typed tuple），并让 §2.3D 与 §5.3
引用同一个 operation。不得把 owner 集合塞回 coordinator、context 或 parent child-state map；
也不得以“owner_plan 已存在”代替其来源和生命周期。

### LO-IR55 — cancellation 的 nested 分支和 acknowledgement 竞态仍会漏 abort

Target §2.3C 的外层图（约第 512–554 行）在 facade 捕获取消后判断：

```text
if root has live child handles:
    coordinator.abort_all(root, ...)
else:
    existing standalone session cancellation cleanup(root)
```

但文档同时规定“没有 live child 的 nested invocation 只 abort parent”。上述条件没有区分
`root.parent_scope_run is not None` 的 nested owner 和真正 standalone root；当 nested owner
没有 live descendant 时，可能只做 session cleanup 而不提交 parent 的 `AbortGraphRun`。

另一个窗口是：child `StartGraphRun` exact acknowledgement 返回后，construction frame 再返回
`_ChildCallAdmission`，随后异步 `register()`。外部 cancellation 可以落在 acknowledgement
与 register 之间；candidate 已经是 authoritative child，却不在 coordinator 的 handles 中，
`abort_all()` 无法触达。当前文字只规定时点，没有规定 atomic registration、pending-candidate
cleanup 或 cancellation mask。

**必须闭合：** 明确 nested/standalone 判别、无 descendant 时 parent 的 abort、ack/register
之间的不可漏登记保证，以及 registration 失败时的 owner-local cleanup。该保证只能是当前
invocation 的局部生命周期处理，不得引入 global lock/overlap gate。

### LO-IR56 — cancellation-safe join 的 `Awaitable` 声明不满足严格类型实现

Target §2.3C 声明：

```text
async cancellation_safe_join(operation: Awaitable[None]) -> None
```

并要求把 `operation` 直接交给 `asyncio.create_task`。`Awaitable[None]` 作为泛型本身是合法的，
但 `create_task` 的参数类型是 coroutine-like，而不是任意 `Awaitable`；按当前声明，pyright
会报告参数类型和未知 task 类型错误，运行时把一般 `Future` 传入也会抛 `TypeError`。这不是
要求改 public API，而是 private helper 的 nominal contract 不闭合。

**必须闭合：** 将参数收窄为实际 coroutine 类型，或在 helper 内用一个明确返回
`Coroutine` 的 async wrapper 包住任意 awaitable（并保持 `None` 的结果类型）；同时写明重复
取消、cleanup error 与原始 caller-visible cancellation 的传播优先级。

### LO-IR57 — provider 的 owner 边界和 identity ledger 仍有一处文字冲突

§1.1 第 3 条仍写“parent/coordinator 只保留 `_OpaqueChildCallHandle`”，而 §2.3D 又写
root owner 持有 child `export_provider` tuple 直到 result/partial boundary（约第 585–594 行）。
这可以作为 invocation-local adapter closure，但必须明确它不是 parent 的 child capability、
不允许 parent drive 直接调用，只能在指定 export boundary 一次物化；否则会与 requirements
“parent 最多持有 opaque handle 或 transient result”的边界冲突。

此外，§8 的 producer ledger 将 `child_graph_run_id()` 的直接 caller 写成 State validation，
但 §1.3 又明确 `execution/identity.py::child_scope_run_for_activation()` 会在 producer 内
调用它。应把“identity primitive 的 producer helper caller”与“State validator caller”分别
列出，避免实现者误删 producer 或误把 validator 当 runtime owner。

**必须闭合：** 统一 provider 的临时 owner 术语和 ledger 的 direct-caller 分类；不增加 identity
字段、lookup、registry 或跨 invocation 资料。

## 4. 已通过且不得借修订扩张的边界

- 每个 `GraphRun` 独占自己的 `run_id`、state、transition、commit、executor、session 和
  local frame；parent 不维护 child authoritative state 副本。
- parent 不需要保存、查询或控制 child `run_id`；不同 parent identity 下 deterministic child
  ID 不碰撞只是 child identity/State proof，不是 parent lookup 或 overlap gate。
- child terminal projection 先确认，parent 只安装自己的 boundary 并结算自己的 nested node；
  child exact commit 后的 parent 失败不回滚、不 retry、不 failover。
- `WaitingForChildren` 仍是 execution-internal metadata；`Graph.Result`、State、command、
  continuation/frame ABI 不新增 public variant 或字段。
- invocation cancellation 才向当前 live child handles 传播；typed child failure 与 ordinary
  node exception 不广播 sibling。父、child 各自复用既有 `AbortGraphRun` 独立 commit。
- 不新增 persistence/Store/checkpoint、worker handoff、仅凭 child ID 的跨 invocation recovery、
  global optimistic lock 或第二 scheduler。

## 5. 评审结论

```text
direction / ownership principle          = CORRECT
distance from previous review            = SIGNIFICANTLY CLOSER
LO-IR45 boundary order                   = CLOSED IN DIRECTION
LO-IR47 factory ownership                = SUBSTANTIALLY CLOSED
LO-IR50 waiting/result shape             = SUBSTANTIALLY CLOSED
LO-IR44 recursive admission/export       = LO-IR52 OPEN
frame partition contract                 = LO-IR53 OPEN
partial owner-plan handoff               = LO-IR54 OPEN
cancellation lifecycle                   = LO-IR55 OPEN
strict cancellation helper typing        = LO-IR56 OPEN
provider/identity boundary wording       = LO-IR57 OPEN
persistence / Store / failover           = OUT OF SCOPE / UNCHANGED
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
```

因此可以明确回答：**比上轮更接近闭合，但现在还不能开始编码。** 当前阻塞点是可执行性
和类型边界，不是要重新讨论 parent 是否持有 child `run_id`，也不是方案本身走不通。关闭
LO-IR52–LO-IR57 后，才具备按同一 owner/caller contract 进入实现的条件；本次评审不授予或
撤销用户已有的范围授权，只判断目标文档尚未达到编码门禁。
