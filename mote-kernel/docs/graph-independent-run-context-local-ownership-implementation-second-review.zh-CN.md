# 父子图 GraphRun 本地 ownership 实施方案第二次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 当前 target 已经正确吸收上一轮关于 family evidence、frame 分区、post-confirm 失败窗口、
> 双边 abort、sibling 隔离和 consumer manifest 的主要意见；但仍有两处会直接影响已批准
> 边界的实现 blocker：父侧 `find_child_boundary()` 仍会计算并返回 child identity，且
> exact-confirmed 但尚未发布 slot 的 child owner 没有一条可执行、可验证的取消交接路径。
> 另有 factory construction 和 compiled-family identity cache 两项需要在实现前明确。
>
> 本文件是 docs-only 独立评审，不修改 implementation target、production、State、Store、
> protocol、public API 或 tests，也不授予代码实施授权。persistence、child-ID-only recovery、
> failover、worker handoff、overlap gate 和 AST/source-shape 扩张仍不在本轮范围内。

## 1. 评审对象与冻结输入

| 对象 | 当前内容 / SHA256 |
| --- | --- |
| 受审 implementation target | [graph-independent-run-context-local-ownership-implementation.zh-CN.md](graph-independent-run-context-local-ownership-implementation.zh-CN.md) — `1c9adda1e2501386bbbeb46cf805c75c9699d8f968c21d21c5a945052fe63a1e` |
| 已批准的 requirements 输入 | [graph-independent-run-context-local-ownership-requirements.zh-CN.md](graph-independent-run-context-local-ownership-requirements.zh-CN.md) — `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| requirements 独立审核 | [graph-independent-run-context-local-ownership-requirements-independent-review.zh-CN.md](graph-independent-run-context-local-ownership-requirements-independent-review.zh-CN.md) — `f13d980690883d780a04a8a5879f147f1c7f3a0fdf6ab0a907c35646b6888604` |
| 最新审批依据（不单独批准 target） | [graph-independent-run-context-local-ownership-implementation-review-response-review-response-review.zh-CN.md](graph-independent-run-context-local-ownership-implementation-review-response-review-response-review.zh-CN.md) — `992d72a9c3eee970a0b55b3c3cabe877837ee7b7dd3e89b3944207419161333e` |
| 上一轮 implementation 评审 | [graph-independent-run-context-local-ownership-implementation-review.zh-CN.md](graph-independent-run-context-local-ownership-implementation-review.zh-CN.md) — `304ed4d9a904734896d9fc8d7513e9a3ebc070fb4b05ff2fac56b7069d03f4bf` |
| 上一轮回复复审 | [graph-independent-run-context-local-ownership-implementation-review-response-review.zh-CN.md](graph-independent-run-context-local-ownership-implementation-review-response-review.zh-CN.md) — `a5953fa99256fd8ccaacd11a52a4c4051678f03935617f1c7beac15ca7fb13fa` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

工作树中还有用户已有的 README、示例、兄弟 package 和历史 docs 改动；本评审不将其归因于
本 change unit。

## 2. 评审口径

本轮按当前 requirements 的唯一边界审核 target：

1. 每个 `GraphRun` 独占自己的 `run_id`、`GraphRunState`、live frame、transition 和 commit；
   parent 不维护 child 的 authoritative state。
2. parent 不计算、保存、查询或控制 child `run_id`。现有 projection/snapshot 中为兼容
   sealed ABI 而携带的 identity 只能是一次性的 immutable transport/validation evidence，
   不能变成 parent/coordinator 的 live lookup 或 control API。
3. nested invocation cancellation/abort 必须向当前调用链传播；child、parent 分别完成
   `quiesce →（必要时）owner-local fence → AbortGraphRun → 独立 commit`。typed failure 和
   ordinary node exception 不广播 sibling。
4. 保留现有 State/reducer、`ChildProjection`/`StepRequest`/frontier、continuation/frame
   shape、owner-local persistence read/commit 和错误 taxonomy；本 target 不新增 persistence、
   Store、checkpoint、failover 或仅凭 `child_run_id` 的跨 invocation 恢复。
5. 评审通过不等于 implementation authorization；target 还必须能在不创造第二 runner、
   hidden registry 或第二 value/frame truth 的前提下直接实施。

## 3. 总体复核矩阵

| 维度 | 结论 | 评语 |
| --- | --- | --- |
| 每个 `GraphRun` 独占 state/transition/commit | **通过** | `_GraphRun`、`confirm()` 和 owner-local commit 顺序已写明。 |
| parent 不拥有 child authoritative state | **通过** | `GraphRunContext` 收窄为单 run；family binding 只作 immutable evidence。 |
| parent 不持有/派生 child `run_id` | **阻塞** | 新增的 `find_child_boundary()` 在 parent owner 内调用 identity projection，且 nominal 返回值含 child coordinate。 |
| opaque handle 的 wait/abort 权限 | **方向通过** | 两个操作和 transient lifetime 已冻结；递归 closure 的构造/交接仍未完全闭合。 |
| child 先完成结果、parent 后 settlement | **通过** | §5、§8、§10.3 的顺序与 requirements 一致。 |
| nested cancellation 双边 abort | **方向通过 / 交接阻塞** | 正确写出 child-first 和独立 commit，但 provisional owner 不在已定义的 finalizer 集合中。 |
| typed failure / ordinary exception sibling 隔离 | **通过** | 明确不广播 sibling abort。 |
| continuation/recovery family shape | **通过** | 保留现有 `child_states`、family `ScopedFrameIndex` 和 family-shaped recovery seed。 |
| local frame owner / child boundary | **条件通过** | `_RunLocalFrameView` 和 `_owner_for_record()` 足够表达 ABI；live parent helper 不应再暴露 identity。 |
| post-confirm 失败矩阵 | **通过** | §10.4 覆盖 start、projection、boundary、settlement、relay 和 cancellation 窗口。 |
| manifest / AST policy | **通过** | direct consumers 已列入；既有 AST 测试保持不变且不作为新增 gate。 |
| family identity 的 compile-order 语义 | **待补强** | Step 5 有目标性文字，但没有处理当前 `_compile()` 缓存行为的 exact rule/test。 |
| persistence / child-ID-only recovery / failover | **通过** | 仅保留既有 owner-local 语义，不新增协议。 |
| implementation authorization | **未通过** | 需先关闭下述 blocker，并重新独立审核后再由用户明确批准。 |

## 4. 必须关闭的 blocker

### LO-IR1 — parent-side `find_child_boundary()` 仍计算并返回 child identity

**证据。** Requirements §3.5、§4.2、§5.4、§8 把 child identity 的创建、校验和使用归
child owner，明确禁止 parent 自行派生或查询 child `run_id`。Target §4.2/§4.3 却冻结了：

```text
_GraphRun.find_child_boundary(
    parent_activation: ParentGraphActivation,
    child_graph: CompiledGraph[GraphValueT],
) -> ConfirmedChildBoundary[GraphValueT] | None
```

并要求这个 **parent owner** 内部调用 `child_scope_run_for_activation()` 做匹配。该函数在
当前源码 `src/mote_kernel/execution/identity.py:46-52` 明确通过
`child_graph_run_id()` 构造新的 `ScopeRunCoordinate`；返回类型
`ConfirmedChildBoundary` 的 `coordinate.child_scope_run` 又直接携带该 child `run_id`。Target
随后在 §4.3 的 duplicate start 检查中让 coordinator 调用这个 helper（§4.3，约第 884–890
行）。因此下面两句话不能同时成立：

- “helper 不向 parent 返回或保存 child coordinate”；
- helper 的返回值是带 `coordinate.child_scope_run` 的现有 boundary，且由 parent owner 计算
  expected child coordinate。

这不是现有 `ChildProjection.child_state` 的必要 evidence 读取。`prepare_frontier()` 对既有
projection 的 identity 校验可以继续保持（它是 normative consumer，requirements 明确要求
projection shape 不变）；本 blocker 针对的是本 target 新增的 parent-side live operation。

**必须改成唯一的窄 contract：**

1. 将 `find_child_boundary()` 改为真正不含 identity 的 predicate/opaque marker（例如只返回
   “matching boundary exists”的 typed predicate），不得返回 `ConfirmedChildBoundary`、
   `ScopeRunCoordinate`、`GraphRunState` 或 frame。
2. boundary record 的 coordinate/descriptor 校验留在 sealed evidence validator 或 child-owner
   closure 内；coordinator 只消费整体 predicate，不读取或重算 child ID。若 family
   `_owner_for_record()` 为 immutable snapshot validation 需要重算坐标，必须明确它不是 live
   parent API、不能被 duplicate/cancel/recovery 路径调用。
3. 增加一个 typed behavior 证据：parent/coordinator 只能判断 matching boundary，不能从该
   helper 取得 child identity 或用 identity 做 lookup/control。不得为此修改现有
   `result.py`、`request.py`、`frontier.py` 或 continuation payload。

### LO-IR2 — exact-confirmed provisional child owner 没有可执行的 cancellation 交接边界

**证据。** Target §4.2 把每个 scope 的 cell 描述成 `(direct_slots, local_sealed_records)`，
并规定 finalizer 在 `start_child()` **成功发布 slot 后**才登记；§4.3 的 `_GraphRun.start()`
又规定 `StartGraphRun` exact confirmation 之后、input-frame 安装和 slot 发布之前先创建
一个 method-local provisional owner。§10.2 只用叙述性文字说这个 owner “追加到本次后序访问”，
但 `finalize_scope()` 的唯一伪代码只遍历已登记的 `scope.finalizers`/direct slots，没有
provisional owner 的 nominal cell、producer、consumer、canonical order 或一次性 disposal
操作。

同样的缺口出现在递归 factory：`child_owner.start_from_nested_input()`/
`admit_from_binding()`（§4.2，约第 770–784 行）没有定义 `child_owner` 的 nominal 类型，也
没有冻结它如何绑定 ancestor sink、descendant relay、scope cell 和 cancellation finalizer。
如果实现者在这个窗口自行选择 list、map 或另一个 owner registry，就会重新引入 target 明确
禁止的 hidden mutable state/第二 orchestration path；如果不登记，invocation cancellation
可能看不到一个已经 exact-confirmed 的 child owner，违反 requirements §4.1/§4.4 的双边 abort。

**必须补齐：**

1. 定义一个 execution-private 的 child-scope factory/closure 及其 nominal scope cell；cell
   至少要能区分 direct published slots、exact-confirmed provisional owners、sealed records
   和 one-shot finalizers，且不保存 child ID 作为 parent key。
2. 冻结 confirmation-to-publication 的原子交接：要么 exact `StartGraphRun` 后在第一次
   await 前完成 frame+slot publish，要么 cancellation/error 在 publication 前统一经过同一
   child-first `quiesce → fence（必要时）→ AbortGraphRun → commit → seal/dispose` 路径。最终
   finalizer 必须同时覆盖 published slot 和 provisional owner，各最多一次。
3. 明确 `start_from_nested_input()` 的 owner、sink、relay、limits/commit 引用和 closure
   lifetime；grandchild 只能留在 child closure，不得回写 ancestor `children`。补充 confirmation/
   install 窗口的 typed behavior test；不引入 persistence、retry、failover 或 ID lookup。

## 5. 实现前必须明确、但不要求扩大范围的事项

### 5.1 `GraphRunContext` / `_GraphRun` factory-only construction

Target §4.2、§6.1、§4.3 声称除 `_GraphRun.start()`/`admit()` 和 `_new_run_context()` 外
不存在直接构造路径，但没有给出 context/run 的 construction token/seal。当前基线
`src/mote_kernel/execution/run_context.py:379-397` 的 `GraphRunContext` 是普通可调用构造器；
仅靠下划线命名不能阻止其他 private consumer 用 foreign state/frame 绕过 family validation。

这不应变成 public API 或新的 registry。实现前二选一并写回 target：

- 增加 module-private construction token/seal，只有 canonical factories 持有；或
- 明确这是 module-private source-discipline 而非可伪造失败的 runtime guarantee，并删除
  “不存在直接构造路径”的强断言；所有 tests/consumers 只走 factory。

当前记为 `LO-IR3 = REQUIRED CLARIFICATION`，不是要求改变 State/continuation shape。

### 5.2 root compiled-family identity 与 `_compile()` cache 顺序

Target §1.2、§4.2、§11 Step 5 正确要求：一个 root compiled family 只分配一个 stable
`family_identity`，nested standalone `Graph` 的 identity 不进入 parent invocation。当前基线
`src/mote_kernel/execution/facade.py:546-562` 却为每个 `Graph` owner 分配
`_new_family_identity()`，并且对已有 `_compiled_owner` 跳过安装。若 child graph 先 standalone
compile，再 compile parent，缓存的 nested owner identity 与 root identity 会不同。

这可以在不增加 global state 的前提下实现，但 target 还没有写出唯一规则：parent invocation
必须把 root identity 显式传给每个 nested adapter，任何 nested `_CompiledOwner.family_identity`
都不得被读取；standalone child invocation 仍使用自己的 identity。建议补一条 compile-order
independence behavior test（child 先/后 standalone compile），并把该项加入 Step 5/LO-B02 的
evidence；否则 continuation family pairing 的 identity 可能依赖编译调用顺序。

当前记为 `LO-IR4 = IMPLEMENTATION PRECONDITION`。这不是要求支持 cross-parent overlap，也
不是要求新增 lock/registry。

## 6. 本轮确认已合理吸收的事项

以下内容与已批准 requirements 一致，本轮不再要求扩大：

- **family evidence typed handoff（前一轮 LO-RR1）**：target §7.1.1 已给出
  `_GraphContinuation.admit()`、`from_state_only()`、`from_snapshot()`、lineage/frame
  validators、`recovery_seed()` 和唯一 `export_snapshot()` 的输入/输出与生命周期；
  `ChildStateBinding`/family frames 仍是 immutable transient evidence。
- **post-confirm failure windows（LO-RR2）**：§10.4 已区分 confirmation 前未知副作用、
  confirmation 后 frame/boundary/publication 安装失败、sibling partial start、parent settlement
  failure、relay failure 和 `_PartialCommitError` 的适用范围，没有伪造 rollback/retry。
- **cancellation phase（LO-RR3）**：nested invocation 已改为 child-first 双边
  `AbortGraphRun`；standalone root 的 active-token/state-only regression 被明确标为
  `OUT OF TARGET`，没有重新引入 orphaned-claim recovery。
- **local frame owner（LO-RR4）**：`_RunLocalFrameView`、`_owner_for_record()`、parent
  boundary producer 和 candidate overlay 已形成唯一 family partition/merge 方案；本轮只要求
  处理 live helper 的 identity 泄漏。
- **consumer manifest（LO-RR5）**：`test_result_boundary_contract.py` 已列入 MODIFY，
  direct runtime-context consumers 也已逐项登记。
- **AST policy（LO-RR6）**：既有 `tests/architecture/test_graph_execution_ownership.py`
  保持不变，不作为新增 acceptance，也没有申请例外。
- **sibling/definition/identity 边界**：typed node/child failure 不广播 sibling；同一
  immutable compiled child 的 cross-parent overlap 只是 caller precondition；不同 parent
  `run_id` 的 child identity 只保留纯函数注入性，不被当作 admission/lock 保证。
- **范围边界**：不新增 persistence/Store/checkpoint、child-ID-only recovery、failover、
  worker handoff、global registry、second runner、State field/status/command 或 public API。

## 7. Requirements → target 对照

| requirements | target 位置 | 本轮结论 |
| --- | --- | --- |
| §4.1 每 run state ownership、双边 abort | §4、§4.3、§10.2 | state owner 通过；provisional cancellation 需补契约（LO-IR2）。 |
| §4.2 parent→child typed call、parent settlement | §5、§8、§10.3 | 通过；`find_child_boundary` identity API 需收窄（LO-IR1）。 |
| §4.3 immutable definition、identity 注入性、overlap precondition | §1.2、§9、Step 5 | 纯 identity/overlap 通过；compile cache 需补 exact rule（LO-IR4）。 |
| §4.4 result/lifecycle/cancellation/sibling | §4.4、§8.1、§10 | 结果与错误矩阵通过；取消交接待补。 |
| §5 State/API/frame/identity invariants | §2、§6、§7 | family shape 与 no-ID-only 通过；live parent identity helper 不通过。 |
| §6 acceptance matrix | §13 | 16 项行为矩阵齐全；需增加/明确 LO-IR1/IR2 evidence。 |
| §7 non-goals / §9 gates | §1.3、§9.3、§14–§15 | 通过；无 persistence/failover/overlap/AST 扩张。 |

## 8. 验证记录

本轮只读验证，未修改 production、State、Store、protocol、public API 或 tests：

| 检查 | 结果 |
| --- | --- |
| 受审 target SHA256 | `1c9adda1e2501386bbbeb46cf805c75c9699d8f968c21d21c5a945052fe63a1e` |
| requirements / independent review / latest approval SHA256 | 与第 1 节冻结值一致 |
| source/test consumer scan | 直接使用 `GraphRunContext`、`_new_context`、`project_graph_result` 的 consumers 均已在 target §14 manifest；未发现新的漏列文件 |
| identity / lifecycle source spot-check | 确认 `child_scope_run_for_activation()` 生成 child coordinate；确认 `GraphRunContext` 当前为普通 constructor；确认当前 `_compile()` per-owner family identity cache |
| targeted baseline tests | `129 passed`（identity、continuation、frame、result boundary、recovery、resource、public typing、nested）；另 `10 passed`（Graph API nested/cancellation/continuation subset） |
| existing architecture test (read-only baseline) | `18 passed`；本 target 要求保持该测试不变且不作为新增 gate |
| Markdown 相对链接 | 目标文档与本评审共检查 14 条相对链接，全部存在 |
| scoped pre-commit | 目标文档与本评审通过；代码相关 hooks 因无代码文件而跳过 |
| `make check` | **失败于既有 complexity-ratchet 基线**：ruff、format、pyright 通过；`test_current_candidates_are_explicitly_reviewed_and_inventory_is_fresh` 发现 5 个未登记候选，`test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked` 的 `decision_points` 为 1314（配置上限 1312）。本 change unit 未修改 `src/` 或 `tests/`，故不将该失败归因于本评审；后续不能把完整 `make check` 写成通过 |
| production/tests diff | 无本轮修改 |

target 修订并重新审核后，仍需按最终 manifest 运行 strict typing、lint、format、behavior、
coverage、build/package、适用 pre-commit 和 Markdown 检查；不得把未执行的完整 `make check`
写成通过。

## 9. 下一步与最终 ledger

必须先关闭 LO-IR1、LO-IR2，并在 target 中明确 LO-IR3；LO-IR4 至少要写入 compile-order
rule/test。之后重新计算 target hash，再做一次独立 implementation review；requirements 的
批准不替代该 review，review 也不替代用户对 production/test 的明确授权。

```text
target ID                              = GRC-LO-001-T01
reviewed implementation SHA256         = 1c9adda1e2501386bbbeb46cf805c75c9699d8f968c21d21c5a945052fe63a1e
per-GraphRun state ownership           = PASS
parent authoritative child state      = FORBIDDEN / PASS
parent child run_id                    = FORBIDDEN / BLOCKED BY LO-IR1
opaque handle                         = TRANSIENT / WAIT-OR-ABORT ONLY / LIFECYCLE GAP LO-IR2
child-first result -> parent settle   = PASS
nested cancellation                   = REQUIRED / DOUBLE ABORT ORDER PASS / HANDOFF OPEN
typed failure / ordinary exception    = LOCAL / NO SIBLING BROADCAST
family evidence                       = IMMUTABLE TRANSIENT TRANSPORT/VALIDATION ONLY
local frame owner                     = OWNER-TAGGED VIEW / CONDITIONAL ON LO-IR1
continuation/recovery shape           = KEEP EXACT
family identity cache order           = PRECONDITION LO-IR4
GraphRunContext construction          = CLARIFICATION LO-IR3
cross-parent overlap                  = CALLER PRECONDITION / NO RUNTIME GATE
child_run_id-only recovery            = OUT OF SCOPE
persistence / Store protocol          = KEEP EXISTING / NO NEW PROTOCOL
failover / worker handoff             = OUT OF SCOPE
State/status/command/public API       = KEEP
AST/source-shape expansion            = FORBIDDEN / EXISTING TEST UNCHANGED
production / State / Store / API/tests = NO CHANGE IN THIS DOCS-ONLY REVIEW
implementation authorization           = PENDING; NOT READY
```

本评审坚持的最终边界仍是：child 自己拥有并提交自己的 state；parent 只调用 child、消费既有
typed transient evidence 并提交自己的 nested settlement。修订时不得为了关闭本评审而引入
父侧 child-ID map、跨 invocation 恢复、持久化/failover 或第二执行引擎。
