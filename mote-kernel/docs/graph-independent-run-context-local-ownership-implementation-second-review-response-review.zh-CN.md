# 父子图 GraphRun 本地 ownership 实施方案第二次评审回复复审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮回复和 target 的方向仍与已冻结的 ownership 边界一致，但回复把两个实现 blocker
> 过早标成 closed：parent-side `bool` boundary predicate 没有可实现的 producer/storage
> contract，provisional owner 的 finalizer/cell 也没有与伪代码一致的严格 typed contract。
> 在这两项闭合并重新独立审核前，不能据此开始 production 或 tests 实施。

本文件是 docs-only 的独立复审；不修改 requirements、implementation target、production、
State、Store、protocol、public API 或 tests。评审通过与否也不等于 implementation
authorization；仍须由用户在本窄范围内另行明确授权。

## 1. 评审对象与冻结输入

| 对象 | 当前内容 / SHA256 |
| --- | --- |
| 受审 implementation target | [父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md) — `e1a5839af852d90038bf6884e7cfd0878830d6f92e90a049e3e82de905fdfc0c` |
| 本轮评审回复 | [实施方案第二次评审回复](graph-independent-run-context-local-ownership-implementation-second-review-response.zh-CN.md) — `3112d508c43ca2d859baa7e5d3bae4e6734e1c9ad9947ac51bf610917d55d132` |
| 被回复的第二次评审 | [实施方案第二次独立评审](graph-independent-run-context-local-ownership-implementation-second-review.zh-CN.md) — `afbde48070a2e3c419fa1a5a1e478b0e0a1411250ce12639da5524b7abbba3cd` |
| 已确认 requirements | [本地 ownership 拆分需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) — `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| requirements 独立审核 | [需求独立审核](graph-independent-run-context-local-ownership-requirements-independent-review.zh-CN.md) — `f13d980690883d780a04a8a5879f147f1c7f3a0fdf6ab0a907c35646b6888604` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 复审日期 | 2026-08-29 |

上述 hash 是本轮实际读取的文件内容。工作树中还存在其他用户已有的 docs、README、
example 和 package 改动，本复审不将其归因于本 change unit。

## 2. 评审口径

本轮只按已确认的窄范围审核，不重新打开已经排除的扩张：

1. 每个 `_GraphRun` 独占自己的 `run_id`、`GraphRunState`、local frame、transition 和
   commit；parent 不维护 child 的 authoritative state 镜像。
2. parent 不计算、保存、查询、恢复或控制 child `run_id`。当前调用若需要等待或取消，
   只能持有 invocation-local opaque wait/abort handle；既有 sealed identity 只能作为
   immutable transport/validation evidence。
3. child 先完成并提供既有 typed projection，parent 随后只结算自己的 nested node。调用级
   cancellation/abort 向当前调用链下传，child 与 parent 各自在自己的 owner 上完成既有
   `quiesce →（必要时）fence → AbortGraphRun → 独立 commit`。
4. typed failure 和 ordinary node exception 不广播 sibling；现有 State/reducer、
   `ChildProjection`/`StepRequest`/frontier、continuation/frame ABI、错误 taxonomy 和
   owner-local persistence read/commit 保持不变。
5. 不新增 persistence/Store/checkpoint、failover、worker handoff、child-ID-only 跨
   invocation recovery、global registry、overlap admission gate、第二 runner、State 字段、
   public API 或 AST/source-shape-only test。不同 parent `run_id` 下的 child identity 纯
   投影不碰撞仍须保持；cross-parent overlap 仍只是 caller precondition。

## 3. 总体复核矩阵

| 事项 | 结论 | 复核意见 |
| --- | --- | --- |
| 每个 GraphRun 独占 state/transition/commit | **方向通过** | `_GraphRun` 与 owner-local `confirm/abort` 的原则一致。 |
| parent 不拥有 child authoritative state | **通过** | context 收窄、child projection 和 sealed evidence 的角色描述一致。 |
| parent 不持有 child `run_id` | **方向通过** | handle/slot 不展开 child identity；但 boundary presence 的实现证据尚未闭合。 |
| `find_child_boundary() -> bool` | **阻塞** | 没有不读取/派生 child identity 即可判断 matching activation 的唯一 producer/storage。见 LO-IR5。 |
| provisional owner cancellation handoff | **阻塞** | cell/finalizer 的 nominal API 与实际伪代码不一致，无法保证一次性 abort/seal/disposal。见 LO-IR6。 |
| child-first 双边 abort、sibling 隔离 | **方向通过** | 顺序和“不广播 sibling”与 requirements 一致；依赖 LO-IR6 的可执行 handoff。 |
| continuation/family frame evidence | **方向通过** | 保留现有 sealed shape；local partition/merge 规则已写清主要 owner。 |
| factory construction | **通过（范围内）** | 采用 source discipline、不引入 construction token 是可接受取舍；需保留 direct-consumer manifest。 |
| compiled-family identity compile-order | **通过（前置条件已补）** | root identity 显式注入 nested adapter，未扩张成 cache merge、lock 或 overlap gate。 |
| persistence、failover、ID-only recovery | **通过** | 仍明确为“不新增能力”，没有误改 owner-local read/commit。 |
| implementation authorization | **未通过** | 两个 blocker 关闭并再次独立审核前，不进入代码实施。 |

## 4. 本轮合理吸收的修订

以下内容可以保留，不要求为了本复审扩大范围：

### 4.1 source-discipline factory

回复 LO-IR3 选择 module-private source discipline，承认 Python 的 private constructor 不是
runtime authorization，并拒绝 construction token/seal。这与本 change unit 的内部边界相容，
也没有伪造 public guarantee。实施时应继续要求所有 direct consumers 迁移到列出的
canonical factories，并把漏列 consumer 视为 target 偏离；不需要新增 token、alias 或 AST-only
absence test。

### 4.2 root family identity 注入

回复 LO-IR4 对 compile-order 的处理是合理的：public root facade 取得本次 root owner 的
`family_identity`，显式传给 nested adapter；nested graph 作为 standalone root 时才使用
自己的 cached owner identity。child 先/后 standalone compile 不应改变 parent invocation 的
family pairing，但不需要合并各 Graph 的 cache，也不需要为 unsupported overlap 添加 lock 或
rejection gate。LO-B02 的 compile-order behavior evidence 可保留，不能宣称它证明并发
admission 安全。

### 4.3 已冻结的其他边界

回复正确保留了 child-first 独立 abort、typed/ordinary failure 的 sibling 隔离、现有
`ChildProjection`/continuation/frame ABI，以及“不新增 persistence/failover/child-ID-only
恢复”的范围。`GraphRunContext` 的 source-only factory 约束、family-shaped recovery seed
和现有错误分类也不应被本复审改成新的公共协议。

## 5. 仍未闭合的 blocker

### LO-IR5 — `bool` boundary predicate 没有唯一的 producer/storage contract

#### 证据

回复 LO-IR1 只把签名改成了（target §4.2/§4.3，约第 487–505、823–826 行）：

```text
_GraphRun.find_child_boundary(
    parent_activation: ParentGraphActivation,
    child_graph: CompiledGraph[GraphValueT],
) -> bool
```

target §4.2/§4.3、§6.3.1 又明确规定它不得调用
`child_scope_run_for_activation()`、构造/读取 `ChildBoundaryAvailabilityCoordinate`，也不得
返回 marker 或 frame；它只能询问 parent local view 中“已经验证的 matching boundary”是否
存在。

但是当前唯一的 local frame ABI `ScopedFrameIndex.child_boundaries`（基线
`src/mote_kernel/execution/run_context.py:106–113, 171–203`）存储的是：

```text
ConfirmedChildBoundary(
    ChildBoundaryAvailabilityCoordinate(child_scope_run, descriptor),
    frame,
)
```

`_RunLocalFrameView`（target §6.3.1，约第 1481–1484 行）只有 `owner_scope` 和这个 index，没有
`parent_activation` presence relation。target 的 `install_child_boundary(parent_activation,
boundary)` 虽然接收 parent activation，但其规定的安装动作只是调用既有
`ScopedFrameIndex.add_*()` 并替换 local index；既有 record 安装后不保留 parent activation。

因此对同一 compiled child 在不同 superstep/node 的两个 activation：

- 扫描既有 `child_boundaries` 并比较 coordinate，必须重新派生/读取 child identity，违反
  target 对 live parent 的禁止；
- 只比较 `child_graph`、descriptor 或 output，不能保证区分 activation；
- 增加 activation-indexed map/marker 才能得到 presence，但 target 没有定义其 typed producer、
  owner、生命周期和与既有 boundary ABI 的唯一关系，且可能制造第二份 evidence truth。

所以“返回类型是 `bool`”本身没有闭合 LO-IR1。回复所称的“已验证 presence fact”目前没有
可编译的来源；foreign boundary、同图不同 activation、stale/missing boundary 的错误行为
也无法由当前 contract 唯一决定。

#### 必须补齐（不扩大本轮范围）

1. 在 target 中冻结一个唯一的 typed producer：它在 boundary 安装/admission 时把
   `ParentGraphActivation` 与 presence 事实绑定，并由明确的 owner 保存到当前 scope；live
   parent 只能消费 `True/False`，不能得到 child coordinate/run id。
2. 冻结该 presence 事实的存储和一次性更新/清理边界，明确它如何与现有
   `ConfirmedChildBoundary` 的 sealed ABI 对应；不得新增 public API、persistence、ID lookup、
   第二 boundary truth 或全局 map。若选择 owner-bound closure/capability，必须把其严格
   typed 形状、生命周期和不可读性写出；不能只写“opaque marker”而不定义 producer。
3. 明确 foreign、duplicate、stale/missing 的 error precedence，并加入行为证据：同一 graph
   的不同 activation、合法 presence、缺失 presence、foreign/mismatch 均在 child commit 前
   得到固定既有错误；测试不得通过反射或源码形状证明 absence。
4. 将 `LO-B16`、`project_missing_child()` 和 `install_child_boundary()` 的调用图统一到
   这一个 presence source；sealed `_owner_for_record()` 的 coordinate 校验仍可留在
   immutable evidence 边界，不能被 live predicate 偷换。

在上述 contract 回写前，实施者只能自行发明第二个 map、从 boundary 反推 child ID，或让
`False` 吞掉 malformed evidence，三者都违反当前 target/requirements。

### LO-IR6 — `_ScopeFinalizer`/`_ChildScopeCell` nominal type 与算法不一致

#### 证据

target §4.2（约第 241–245 行）把 `_ScopeFinalizer` 冻结为只有一个可调用成员：

```text
_ScopeFinalizer[GraphValueT]
  finish: (_InvocationAbortSignal | None) -> Awaitable[None]
```

`_ProvisionalChildOwner` 也只列出 `parent_activation` 与
`owner_local_abort_and_seal`，`_ChildScopeCell`（约第 303–307 行）只有四个 tuple：
`direct_slots`、`provisional_owners`、`sealed_records`、`finalizers`。

但 target §4.2（约第 376–423 行）的 `finalize_scope()` 伪代码实际读取或调用：

```text
is_complete
is_provisional
is_exact_confirmed
is_admitted_evidence
owner_is_sealable
abort_once(...)
seal_and_dispose_once()
discard_candidate_once()
dispose_evidence_only_once()
discard_once_without_retry()
record(...)
```

§10.2（约第 2166–2180 行）的 `abort_scope()` 还使用 `entry.slot.call`、`scope.owner`、
`scope.has_ancestor`。
这些字段/操作都不在 nominal roles 或 cell operation 中定义。正文说这些状态可由“cell
对象 identity/operation 返回事实”推导，但没有给出能读取、替换、完成和一次性消费它们的
typed operation；单独的 `finish(signal)` 也不能表达伪代码需要的分支、错误记录和交接结果。

同一问题还出现在三个边界：

1. `_ProvisionalChildOwner` 被定义为“exact-confirmed、尚未发布”，但算法仍有
   `is_provisional and not is_exact_confirmed` 分支；若要覆盖未确认 candidate，必须说明它
   何时登记、谁负责丢弃，以及这是否改变“确认前无 owner/finalizer”的规则。
2. `from_snapshot()` 对 historical evidence-only admission 明确不创建 live owner/slot，
   而 finalizer nominal target 又包含“evidence-only admitted entry”；该 entry 是否存在、
   谁登记、如何只做 disposal 没有定义。
3. `_GraphRun.seal_for_export()` 是无参方法，`_GraphRun` nominal fields 没有 sink；正文只
   说“physical private binding if exists”可以存在。若不把 sink 变成可读的 owner/handle 字段，
   就必须冻结 owner-local closure/wrapper 的确切调用方式和 exactly-once 语义。

此外，`_ChildScopeCell` 已在 §4.1/§4.2 描述为一个 cell，但 root coordinator 又分别暴露
`children`、`provisional_owners`、`finalizers`、`sealed_child_evidence` 四个字段。文档没有
定义一次“tuple replacement”如何原子替换这四个物理字段，也没有定义它们与 child closure
cell 的统一状态值。若实现者逐字段赋值，frame/slot/evidence 失败可能留下两份 mutable
truth；若另建一个隐藏 cell，又违反“只有一个 scope cell”。这正是 confirmation-to-
publication cancellation handoff 需要被锁定的地方。

#### 必须补齐（不新增 State/persistence/failover）

1. 让 `_ScopeFinalizer` 的 nominal shape 与算法二选一并完全一致：要么 `finish(signal)`
   封装全部分支且定义其 completion/error result，要么逐项冻结所需的 typed operations；不能
   让实现者凭经验添加属性、list、map 或 lifecycle enum。
2. 为 `_ChildScopeCell` 冻结唯一 owner、register/replace/complete/dispose 操作，以及
   provisional → published、published/evidence → sealed 的同步交接；root coordinator 的
   四类物理资料必须通过一个明确的 cell/replacement 机制表示，不能出现第二份 mutable truth。
3. 给出 `finalize_scope()`、handle 的复合 `abort_invocation()`、owner-local
   `seal_for_export()`、descendant relay 的唯一 caller 图，证明 child-first 顺序下不会二次
   abort、二次 sealing、二次 sink 写入或漏掉 provisional owner。
4. 定义 confirmation acknowledgement 与登记之间的 cancellation-safe one-shot 操作边界，
   以及 candidate、exact-confirmed provisional、published slot、historical evidence-only
   四种来源的固定错误/清理行为；补充对应 behavior tests（含重复 finalizer、relay failure、
   publication window 和 export 前 disposal）。

这些要求只是把回复已经声称的 lexical cell/one-shot finalizer 变成可实施 contract，不要求
持久化 receipt、retry、child-ID 恢复、全局 registry 或新的 public type。

## 6. 非 blocker 的一致性说明

以下事项目前不构成新的 blocker，但实现前须按 target 既有文字保持一致：

- `finalize_scope()` 与 `abort_scope()` 的伪代码应明确谁调用 child scope 的递归 cleanup、谁
  调当前 owner 的 self seal；ancestor finalizer 只能观察已完成的 cell replacement，不能再次
  调 handle、sink 或 relay。
- response §6 的“LO-IR1/2 closed”在 LO-IR5/6 修订前不能继续写入最终 ledger；行为测试尚
  未执行，不能把 planned evidence 写成通过。
- LO-B02 只证明纯 identity 和 compile-order independence；不能加入或暗示两个 parent 并发
  复用同一 immutable compiled child 的 success/rejection gate。
- “不做持久化”应继续表述为“不新增 persistence/Store 协议”：父可读取/提交自己的既有
  authoritative state，child 同理；持续失败属于既有 Store/commit 故障，不由本 target 设计
  retry、failover 或跨 invocation 接管。

## 7. Requirements → target → evidence 对照

| requirements 边界 | target / response 位置 | 本轮结论 |
| --- | --- | --- |
| §4.1 每 run 独占 state、双边 abort | target §4、§10.2；response LO-IR2 | 方向通过；provisional handoff 受 LO-IR6 阻塞。 |
| §4.2 opaque child call、parent settlement | target §4.2–§5、§8 | child-first/result 顺序通过；boundary presence 受 LO-IR5 阻塞。 |
| §4.3 immutable definition、identity 注入、overlap precondition | target §1.2、§9；response LO-IR4 | 通过；不增加 overlap gate。 |
| §4.4 lifecycle/cancellation/sibling | target §4.4、§10 | 方向通过；finalizer caller/operation 需闭合。 |
| §5 State/API/frame/identity invariants | target §2、§6–§8 | 保持项通过；live predicate producer 未定义。 |
| §6 acceptance matrix | target §13 | LO-B06/LO-B13/LO-B16 目前不可直接实施，需补 evidence。 |
| §7 no persistence/failover/ID-only recovery | target §1.3、§7、§9.3 | 通过，未要求扩张。 |

## 8. 复审门禁与下一步

在下一轮独立 implementation review 前，必须：

1. 回写 LO-IR5 的 presence producer/storage/owner/error contract，并更新 `find_child_boundary`、
   `install_child_boundary`、missing/duplicate 路径和行为矩阵；
2. 回写 LO-IR6 的 finalizer/cell nominal operations、唯一 caller 图、原子 replacement 和
   cancellation-safe confirmation handoff；
3. 重新计算 target 与 response hash，确保回复不再把未闭合项标成 closed；
4. 只运行与最终 manifest 相符的 targeted behavior/type/lint/format/Markdown 检查；不修改
   production 或 tests 作为本 docs-only 复审的一部分；
5. 保持 existing State/reducer、continuation/frame ABI、owner-local persistence read/commit、
   no failover、no child-ID-only recovery、no overlap gate、no second runner 和 no public API
   expansion。

本轮结论不是否定“每个图的 GraphRun 自己负责自己的状态”，而是要求该原则在 live boundary
presence 和取消交接的实现契约中保持唯一真相。关闭 blocker 后再审，仍不自动授予代码开发
授权。

## 9. 验证记录

本轮为 docs-only 评审，未修改 production、State、Store、protocol、public API 或 tests。

| 检查 | 结果 |
| --- | --- |
| target SHA256 | `e1a5839af852d90038bf6884e7cfd0878830d6f92e90a049e3e82de905fdfc0c` |
| response SHA256 | `3112d508c43ca2d859baa7e5d3bae4e6734e1c9ad9947ac51bf610917d55d132` |
| requirements / prior-review inputs | 与第 1 节冻结 hash 一致 |
| source spot-check | `ScopedFrameIndex.child_boundaries` 仅按 `ChildBoundaryAvailabilityCoordinate` 存储；`GraphRunContext` 当前仍含 family child state；`child_scope_run_for_activation()` 负责派生 child coordinate；`_compile()` 当前为 per-Graph owner 分配 family identity |
| code/test diff for this change unit | 无 source/test 修改；本轮仅新增本评审文档 |
| behavior tests | 未执行（target 尚未获实施授权；文档中的 LO-Bxx 仍是 planned evidence） |
| Markdown links / formatting | 本文件 5 条相对链接全部存在；文件级 pre-commit（EOF、换行、尾随空白、secrets 等）通过；显式 `git diff --check`/untracked no-index 检查无诊断 |
| `make check` | 既有基线结果：ruff、format、pyright 通过；complexity-ratchet 因 5 个未登记 candidate 失败，`decision_points=1314` 超过上限 `1312`。该失败不归因于本 docs-only change unit，不得写成完整 `make check` 通过 |

```text
target blocker status                    = LO-IR5 + LO-IR6 OPEN
parent authoritative child state         = FORBIDDEN / PASS
parent child run_id                      = FORBIDDEN / PASS IN INTENT; LIVE BOUNDARY PROBE BLOCKED
child-first dual abort                   = REQUIRED / DIRECTION PASS; HANDOFF CONTRACT OPEN
typed failure / ordinary exception       = LOCAL / NO SIBLING BROADCAST
continuation/frame ABI                   = KEEP EXACT / IMMUTABLE EVIDENCE ONLY
factory construction                     = SOURCE DISCIPLINE ACCEPTED
family compile-order                    = ROOT IDENTITY INJECTION ACCEPTED
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
persistence / failover                   = KEEP EXISTING / NO NEW PROTOCOL
child_run_id-only recovery               = OUT OF SCOPE
State/status/commands/public API         = KEEP
implementation authorization             = PENDING; NOT READY
```
