# 父子图 GraphRun 本地 ownership 实施方案第二次评审回复

> **结论：`REVIEW ITEMS CLOSED IN TARGET / FOLLOW-UP INDEPENDENT REVIEW REQUIRED / CODE UNCHANGED`。**
> 本回复把第二次评审逐项分成“回写实施文档”和“明确不采纳的扩张”。实施文档已收窄
> parent boundary probe、闭合 provisional-owner cancellation 交接，并补齐编译顺序与
> factory construction 规则；本回复不授予 production、State、Store、protocol、public API
> 或 tests 的开发授权。

## 1. 回复对象与冻结输入

| 对象 | 内容 / SHA256 |
| --- | --- |
| 第二次评审 | [实施方案第二次独立评审](graph-independent-run-context-local-ownership-implementation-second-review.zh-CN.md) — `afbde48070a2e3c419fa1a5a1e478b0e0a1411250ce12639da5524b7abbba3cd` |
| 评审前 implementation target | [本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md) — `1c9adda1e2501386bbbeb46cf805c75c9699d8f968c21d21c5a945052fe63a1e` |
| 本轮修订后 implementation target | [本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md) — `e1a5839af852d90038bf6884e7cfd0878830d6f92e90a049e3e82de905fdfc0c` |
| requirements | [本地 ownership 拆分需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) — `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| requirements independent review | [需求独立审核](graph-independent-run-context-local-ownership-requirements-independent-review.zh-CN.md) — `f13d980690883d780a04a8a5879f147f1c7f3a0fdf6ab0a907c35646b6888604` |
| production baseline | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 回复日期 | 2026-08-29 |

评审前 target hash 是第二次评审冻结的输入；本轮修订后的 target hash 已在上表固定记录。
target 不在自身正文重复嵌入 hash，以避免自引用；本回复只记录 decision，不把尚未运行的
代码测试写成通过。

## 2. 总体裁决

第二次评审指出的两个实现 blocker 已按窄范围回写：

1. parent-side `find_child_boundary()` 现在只返回 `bool` presence，不再计算、返回或保存
   child identity；既有 `ConfirmedChildBoundary` 仍只在 child-owner/sealed-evidence
   validator 边界内使用。
2. exact-confirmed、尚未发布 slot 的 child owner 现在先登记到当前 scope 的
   `_ChildScopeCell`，并与唯一 finalizer 一起经历 provisional → published 的同步 tuple
   replacement；取消、异常和普通 unwind 均能看到该 entry。

同时补齐两项实现前条件：

- `_ChildScopeFactory` 明确捕获 owner、root family identity、同一 limits/commit 引用、sink
  和 relay，scope 结束即释放；它是 lexical factory，不是 runner、registry 或第二 state truth。
- root family identity 显式注入 nested adapter，nested graph 的 standalone cached identity
  永不被 parent invocation 读取；child 先/后 standalone compile 不改变 parent pairing。

以下内容仍被冻结：现有 State/reducer、`ChildProjection`/`StepRequest`/frontier、continuation
payload、family recovery seed、public API/results/errors、owner-local persistence read/commit、
child-first 双边 `AbortGraphRun`、无 sibling 广播、无 child-ID-only recovery、无 failover、无
第二 runner、无 legacy/compatibility/AST-only test。

## 3. 逐项处置

### LO-IR1 — parent boundary probe 泄漏 child identity

**采纳并回写实施文档。** `find_child_boundary(parent_activation, child_graph)` 的唯一
签名改为：

```text
_GraphRun.find_child_boundary(
    parent_activation: ParentGraphActivation,
    child_graph: CompiledGraph[GraphValueT],
) -> bool
```

它只确认 parent local view 中是否存在已经完成 owner/descriptor validation 的 matching
boundary。它不得调用 `child_scope_run_for_activation()`，不得构造或返回
`ChildBoundaryAvailabilityCoordinate`、`ScopeRunCoordinate`、`GraphRunState`、frame record 或
opaque marker。child coordinate 的派生和校验仍只发生在 child-owner closure 以及 immutable
family `_owner_for_record()` validator 内；live parent/coordinator 只分支于 `True/False`。

重复 activation、missing-child 和 install 路径均已改为消费该 predicate。新增的 behavior
evidence 只断言 presence 与既有错误分类，不通过反射或源码形状断言“私有字段不存在”。

**不采纳的解释：** 不修改 `result.py`、`request.py`、`frontier.py` 或 continuation payload，
也不把 boundary ABI 改成新的 marker/DTO。评审要求的 identity-free live predicate 已足够，
新增 marker 会制造第二种 boundary truth。

处置位置：implementation §4.2、§4.3、§6.3.1、§11 Step 2、§12、§13 `LO-B16`、§15 gate 6。

### LO-IR2 — provisional child owner 的 cancellation 交接

**采纳并回写实施文档。** 每个递归 scope 现在只有一个 method-local `_ChildScopeCell`：

```text
_ChildScopeCell
  direct_slots
  provisional_owners
  sealed_records
  finalizers
```

`_ChildScopeFactory` 在当前 parent scope 建立该 cell，并捕获同一 root
`family_identity`、`limits`、`commit` 引用、ancestor sink 和 descendant relay。child/grandchild
只更新自己的 cell；ancestor 不取得 descendant owner。

`start_from_nested_input()` 和 `admit_from_binding()` 共用一条交接：

```text
exact confirmation/admission
  -> register provisional owner + one finalizer (synchronous cell replacement)
  -> attach local input/frame (before the next await)
  -> replace provisional entry with published opaque slot (synchronous cell replacement)
```

如果 frame 安装、slot publication、取消或 structured error 在窗口内发生，finalizer 都能看到
exact-confirmed owner，并按 child-first `quiesce → 必要时 owner-local fence → AbortGraphRun →
独立 commit → seal/dispose` 处理；尚未 exact-confirm 的 candidate 直接丢弃，不伪造 `ABORTED`。
已有 sealed binding 的 dormant admission 是 evidence-only disposal，不重复写 self sink。每个
published/provisional entry 最多一次 finalizer，成功后从 cell 消失；不新增 lifecycle enum、
handle 第三个操作、receipt、retry 或 registry。

`start()` 的 exact commit await 采用既有 cancellation-safe 的 one-shot 等待规则：如果取消与
exact successor 返回同时发生，先让同一次 commit 的 acknowledgement 完成；factory 在重新抛出
pending cancellation 前同步登记 provisional entry。没有 exact acknowledgement 时才丢弃 candidate，
不进行第二次 commit、retry 或 receipt。

**不采纳的扩张：** 不建立 `scope -> children` 全局表、parent child-owner map、可重建 ID key、
第二 scheduler 或 durable provisional record。scope cell 是当前调用栈的 immutable tuple
replacement，不是持久化 owner registry。

处置位置：implementation §4.1–§4.3、§5.1、§7.1.1–§7.2、§10.2、§10.4、§11 Step 1/3、§12、
§13 `LO-B06`/`LO-B13`、§14.4。

### LO-IR3 — `GraphRunContext` / `_GraphRun` factory construction

**采纳澄清，选择 source-discipline 分支；不采纳 construction token/seal。**

Python 的 module-private constructor 不是 runtime authorization。target 已删除“绝不存在直接
构造路径”的不可验证断言，改为明确的 source discipline：production 与 behavior tests 只从
三个 canonical coordinator factory 及 `_GraphRun.start()`/`_GraphRun.admit()`/
`_new_run_context()` 进入；任何绕过 family validation 的 direct consumer 都是 target 偏离，
必须在 manifest/source scan 中迁移，不能用 forwarding alias 掩盖。

construction token/seal 会新增一项 capability 生命周期，却不能提供 public observable guarantee，
也不能解决 State/continuation 的 authoritative ownership；为保持唯一真相和低复杂度，本
target 不引入它。该选择不改变 public API、State schema 或错误 taxonomy。

**不采纳的测试形式：** 不增加 AST/source-shape 或 reflection-only 的“不能直接构造”测试。
factory 使用由 typed behavior、direct-consumer manifest、strict typing 和既有 architecture
policy 共同门禁；既有 `tests/architecture/test_graph_execution_ownership.py` 保持不变。

处置位置：implementation §4.1、§4.2、§6.1、§7.1.1、§14.3–§14.5、§15 gate 11。

### LO-IR4 — compiled-family identity 与 `_compile()` 顺序

**采纳规则和行为证据；拒绝合并 standalone cache identity。** target 现在规定：

- public root facade 只读取本次 root `_CompiledOwner.family_identity`；
- 该同一对象显式传入 nested `_GraphRun`/`_ChildScopeFactory`；
- nested adapter 不读取 nested `Graph._compiled_owner.family_identity`；
- child 先 standalone compile 或 parent 先 compile 都得到相同的 parent-invocation family
  pairing；child 自己作为 public root 时仍使用自己的 identity。

`_CompiledOwner` 的 per-Graph cache 仍可保留各自 standalone identity。把多个 Graph owner 的
identity 合并为一个全局 cache、registry、lock 或 definition-level guard 不属于本 target，
会把 standalone identity owner 与 invocation family marker 混为一谈，也会扩张 overlap/admission
语义。新增 compile-order behavior evidence 只验证两种合法编译顺序的 nested execution/
continuation pairing，不测试 cross-parent overlap 或 fresh same-ID admission。

处置位置：implementation §1.2、§4.1、§9.1、§11 Step 5、§13 `LO-B02`、§14.4。

## 4. 评审意见与本 target 边界

下列建议明确不进入本 change unit：

| 不采纳项 | 原因 | 保留的窄替代 |
| --- | --- | --- |
| 用 construction token 伪造 private constructor 的 runtime 防线 | 无 public 可观察收益，引入第二 capability 生命周期 | source discipline + typed consumer manifest |
| 用 opaque marker/新 DTO 替换既有 boundary ABI | 产生第二 value/evidence truth，违反复用基础设计 | `find_child_boundary() -> bool`；既有 sealed boundary 不变 |
| 合并所有 `_CompiledOwner.family_identity` 或建立全局 cache/registry | 扩大 identity owner、overlap 和 admission 范围 | root identity 显式注入 nested adapter |
| AST/reflection-only absence test，或修改既有 architecture test | 违反“不加 AST-only/private-source-shape test” | public/typed behavior evidence；既有 AST test unchanged |
| provisional owner 的 durable receipt、retry、ID lookup、failover | 新增 persistence/recovery 协议，超出 requirements | lexical cell + one-shot finalizer |

这些拒绝不否定评审指出的实现风险；风险已由唯一 typed boundary、cell replacement、现有
validator 和错误矩阵解决。任何需要改变现有 State/reducer、persistence、public API 或
cross-invocation semantics 的后续方案，必须另立 requirements 和 change unit。

## 5. 行为证据与 manifest 变更

实施文档仅增加/收紧以下 planned behavior evidence，不增加 legacy test：

- `LO-B02`：纯 identity 稳定性 + child/parent compile-order independence；不含 overlap gate。
- `LO-B06`：exact-confirmed provisional owner 在 publication 前的 cancellation-safe disposal，
  以及 evidence-only admission 不重复 sealing。
- `LO-B13`：递归 scope cell、provisional/published/finalizer 的 child-first relay/disposal。
- `LO-B16`：`find_child_boundary()` 只返回 `bool`，duplicate 判定不读取 child identity。

对应测试文件仍是现有 behavior test / direct consumer migration：

```text
tests/execution/test_graph_run_ownership.py
tests/execution/test_identity_contract.py
tests/execution/test_graph_recovery_contract.py
tests/execution/test_graph_api.py
tests/execution/test_continuation_integrity.py
```

不修改、不扩写 `tests/architecture/test_graph_execution_ownership.py`；不添加 compatibility
alias、legacy test、private-helper-count test 或源码布局 test。

## 6. 复核门禁与状态

```text
LO-IR1 parent boundary identity leak       = CLOSED IN TARGET
LO-IR2 provisional cancellation handoff    = CLOSED IN TARGET
LO-IR3 factory construction                = CLARIFIED AS SOURCE DISCIPLINE; TOKEN REJECTED
LO-IR4 compile-order family identity       = RULE + BEHAVIOR EVIDENCE ADDED; CACHE MERGE REJECTED
existing ABI / State / reducer             = KEEP
existing persistence / Store               = KEEP; NO NEW PROTOCOL
child-ID-only recovery / failover          = OUT OF SCOPE
legacy / compatibility / AST-only tests    = FORBIDDEN
production / State / Store / API / tests   = NO CHANGE IN THIS DOCS-ONLY TURN
implementation authorization               = PENDING FOLLOW-UP INDEPENDENT REVIEW + EXPLICIT USER APPROVAL
```

本回复与修订后的 implementation target 共同构成下一次独立 implementation review 的输入，
不等于“可以开发”。在获得独立 review 通过及用户明确授权前，不应修改 production 或 tests。
