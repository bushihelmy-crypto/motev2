# 父子图 GraphRun 本地 ownership 实施规范第十二次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 最新 target 的方向正确，并且相较第十一次已有明显、实质性的收敛：递归 admission
> result、one-shot export/plan adapter、frame partition、registration mask、严格的
> `_NodeCancelled` union、partial commit adapter 以及 source/test manifest 都已经写入。
> 但仍有七个字面契约缺口会迫使实现者自行决定 owner、调用点或错误语义，因此本轮仍
> 不能通过编码准入。

本文件是 docs-only 的独立评审记录。不修改 production、State、reducer、Store/persistence、
protocol、public API、continuation/frame ABI 或 tests；不新增 persistence、failover、worker
handoff、仅凭 `child_run_id` 的跨 invocation recovery、overlap gate、第二 runner 或 public
handle。工作区中原有用户修改全部保留。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `87db973a6811194a81f4a78ab4b4ee8f7eb80c821b8d55317ad168a6d0c69530` |
| target 行数 | 1679 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第十一次独立评审](graph-independent-run-context-local-ownership-implementation-eleventh-review.zh-CN.md) |
| 上一轮评审 SHA256 | `6143a785c050be0591afc6f5b11f1c439d934289f8575f477ec8bcfaf0604e08` |
| production 对照基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

本轮以 requirements 已冻结的边界为唯一依据：每个图调用有自己的 `_GraphRun`、state、
transition、commit、session 和 local frame；parent 不保存、查询或控制 child `run_id`，
只在当前调用栈使用 opaque wait/abort handle；child 先完成自己的 exact commit 和 projection，
parent 再结算自己的 nested node；调用级 cancellation 沿 live handle 向下传播，各 owner
独立使用既有 `AbortGraphRun`。本 change unit 不做持久化、failover 或仅凭 `child_run_id`
的跨 invocation 恢复。

## 2. 总体判断

**比第十一次明显更接近闭合，但还没有闭合到“实现者无需自行补语义”。** ownership 方向、
父子状态隔离和主要执行顺序没有发现根本性错误；阻塞项集中在 private typed handoff、
scope capability、一次性 evidence 生命周期、排序、cleanup 传播和门禁一致性。

| 维度 | 结论 | 依据 |
| --- | --- | --- |
| 每个 `GraphRun` 独占 state/transition/commit/session/frame | **方向通过** | target §1.1、§2.3A |
| parent 不持有 child state 或 child `run_id` | **边界通过** | target §1.1、requirements §3.4–§3.5 |
| child 完成后 parent 再安装 boundary、claim、settle | **方向通过** | target §2.3B、§3、§4.1 |
| identity producer / factory / fresh executor | **基本闭合** | target §1.3、§2.3A |
| continuation recursive admission 与 owner plan | **阻塞** | LO-IR58 |
| scope-bound commit 与 live local context | **阻塞** | LO-IR59 |
| awaiting/result evidence 生命周期 | **阻塞** | LO-IR60 |
| canonical owner order 的 cleanup 语义 | **阻塞** | LO-IR61 |
| cleanup error 传播 | **阻塞** | LO-IR62 |
| cancellation 测试门禁 | **阻塞** | LO-IR63 |
| source/test direct-consumer manifest | **阻塞** | LO-IR64 |
| persistence、failover、跨 invocation recovery | **范围外，未扩张** | requirements §8 |

## 3. 已正确吸收、不得重新打开的内容

以下修订是有效收敛，本轮不要求恢复旧的 parent-side state mirror 或扩大范围：

1. `_RecursiveAdmissionResult` 已承载完整 subtree 的一次性 provider；parent drive 仍只拿
   opaque handle（target L153–L173、L400–L415）。
2. `partition_family_frames()` 已收敛为单一 nominal 签名，terminal child boundary 的
   `install → re-prepare → claim/session/settle` 顺序正确（L632–L728）。
3. pending candidate、registration mask 和 cleanup once gate 已覆盖 ack/register 竞态的主要
   状态窗口（L747–L804）。
4. node-origin `CancelledError` 与 invocation-origin cancellation 已分开建模，未新增 public
   result variant（L864–L896）。
5. partial commit 已改为 invocation-local adapter 与 confirmed prefix，并补上
   `failed_scope_for_owner()` 的既有类型转换（L994–L1137）。
6. awaiting projection、source scan 和 test manifest 已有明确章节；这些章节仍需修正下列
   内部矛盾，但不需要改变总体 ownership 方案。

## 4. 仍开放的阻塞项

### LO-IR58 —— 递归 admission 的 nominal 交接仍不一致

Target 同时给出了三套互不完全一致的形状：

- `_OwnerLocalClosure` 声明的是 `append_subtree(result: _RecursiveAdmissionResult) -> None`
  （L166–L173）；
- continuation admission 伪代码却调用 `current_owner_closure.append(export_provider,
  plan_provider)`（L334–L383）；
- 递归示例使用未定义的 `one_shot(...)`（L400–L415），且顶层伪代码中的 `root_scope`、
  `current_owner_graph`、`current_owner_factory` 没有在该调用图中生产或绑定（L346–L362）。

此外，`_GraphRun.admit()` 的声明需要 `family_identity`、`graph`、`scope_run`、
`parent_activation`、`admission_mode`、`limits`、`commit` 等参数（L565–L575），而 admission
伪代码只传 `root_state` 和 `local_frames`（L349–L353）。这不是可省略的注释参数：严格类型
实现无法判断这些值来自哪个 owner，也无法证明 root/child 的 factory 绑定没有被绕过。

注册成功后再追加 subtree provider 的失败落点也没有完整规定（L366–L374）：此时 handle 可能
已进入 coordinator，但 provider 尚未进入 closure；“registration failure cleanup”不能覆盖
“append failure”。fresh `prepare_start()` 只在 prose 中说复用同一交接（L417–L419），没有
给出同样的 nominal 返回和清理路径。

**关闭条件：**

1. 选择一个唯一的 `append_subtree`/`one_shot` 私有签名，写明 tuple 的生产者、所有者、
   消费者、生命周期和失败后的销毁顺序；调用点必须与声明逐字一致。
2. 在 admission 图中显式绑定每个递归 frame 的 `current_owner_graph`、`current_owner_factory`、
   `root_scope`，并按完整 `_GraphRun.admit()` 签名传参；不得用省略号或隐含捕获替代。
3. 明确 register、provider append 任一步失败时，candidate、handle、subtree descendant
   如何由 owner-local once cleanup 收敛；不得留下已登记但无 export/plan 的半成品。
4. fresh、existing、partial 三条路径共用这一个交接，不新增 parent child state/ID map、
   registry 或第二 runner。

### LO-IR59 —— scope-bound commit 和 live context 的 nominal 边界没有闭合

Target 在 prose 中要求每个 owner 有 scope-bound `GraphCommit`（L512–L515），但所有构造
签名仍是原始 `GraphCommit[T] | None`（L121–L136、L260–L271、L526–L575）。当前 production
`GraphCommit` 只是接收一个 `GraphTransition`；`commit_transition()` 的 `scope_run` 和
`commit` 是两个独立参数（`src/mote_kernel/execution/family_driver.py:115-147`）。因此类型
系统不能证明 child 只能提交 child scope；拿到同一个 callable 的代码仍可能构造 parent scope
的 transition。文字上的“只以自己的 scope_run 调用”不足以形成实施契约。

同时 target 的 `_GraphRun.context` 仍标为 `GraphRunContext[T]`（L260–L267）。当前类型实际
包含 family `child_states`，并公开 `child_state()`、`state_at()`、`replace_state()`、
`replace_child()`（`src/mote_kernel/execution/run_context.py:379-421`）。target L314–L324
说这些 parent-side mutation 将被删除、evidence 只读一次，却没有给出 live owner context 的
最终 nominal shape，也没有列出 `family_driver`、`facade`、`invocation` 等现有 `state_at`
调用如何迁移。这样实现者可能保留一个可写的 family context，直接违背 local ownership，或
自行发明第二个 context 类型。

**关闭条件：** 给出一个 private、scope-bound 的 commit 绑定操作/协议（不需 public export），
使 child capability 在类型和运行时 proof 上只能接受自己的 `ScopeRunCoordinate`；同时定义
live `_GraphRun` context 的 owner-local nominal shape，以及 sealed continuation evidence 到
该 shape 的唯一安装点。禁止通过 parent context 读取或替换 child state；不改变既有 State、
Store、public API 或 continuation ABI。

### LO-IR60 —— awaiting evidence 的生产、消费和 result 调用链仍未接上

`project_waiting_children()` 要求同时接收 `disposition` 和 `owner_evidence`
（L227–L232），`collect_awaiting_views()` 也要求从 awaiting slot 对应的 evidence 读取
`GraphFailureView`/`GraphInterruptView`（L1161–L1182）。但是：

- `project_graph_result()` 的 nominal 签名只有 `root`、`disposition`、`export_adapter`
  （L1027–L1033）；
- 唯一调用图只写 `export_adapter.consume_once()` 后 `collect()`（L1222–L1226），没有写出
  `owner_evidence` 如何从 slot/disposition 进入 `project_waiting_children()`；
- awaiting 路径又声称与 continuation export “使用同一 owner evidence”并在调用一次后销毁
  （L1189–L1193、L1295–L1299）。

因此存在未定义的二选一：若 drive 先消费 evidence，result/continuation 会重复消费 one-shot
adapter；若 result 才消费，drive 阶段没有证据生成 `AwaitingResume`。混合 sibling（一个
runnable、一个 awaiting）虽然有 prose precedence（L495–L502），但没有把 slot tuple、
一次性 evidence 和最终 result 的输入连成一个 typed operation。

**关闭条件：** 明确一个 invocation-local 的 evidence staging boundary：给出
`classify → export once → project waiting/result → collect continuation` 的唯一顺序、输入
输出类型、重复调用错误和销毁点。相同 evidence 必须通过同一个不可复制的 transient envelope
在 awaiting 与 continuation 之间传递，不能再读 parent mutable context，也不能新增 awaiting
record、child-ID map 或 public result variant。

### LO-IR61 —— `reverse(canonical_owner_order)` 不能保证最深优先

Target L199–L214 定义了以 `owner_scope_path` 做 lexical/depth-first 前缀排序的 key，并在
L817–L822 声称简单反转该 key 即“最深 owner 优先”。反例：

```text
root
├── a
│   └── a/x
└── b
```

正向 tuple key 为：

```text
root, a, a/x, b
```

简单 reverse 后是：

```text
b, a/x, a, root
```

`b` 会先于更深的 `a/x`，所以该规则既不能满足 child-first cleanup，也会影响 cleanup
error 的首项选择。L1184–L1187 把 lexical、slot、coordinate 等说成同一 comparator 的
不同投影，不能消除这个排序反例。

**关闭条件：** 定义一个真正可执行的 family comparator：要么使用显式 recursive post-order
   rank，要么按每个 subtree 递归 post-order 生成顺序；并明确 sibling tie-break。admission、
slot、export/plan、partial、abort/release 必须引用同一个 nominal order。不得用第二个隐含
排序器或 child-ID map 补救。

### LO-IR62 —— cleanup error 的传播规则仍互相矛盾

Target 一方面声明 `drain_pending_candidates()`、`abort_all()`、`release_all()` 会记录错误并
继续（L794–L804、L851–L862），另一方面：

- `first_cleanup_error_or()` 的 `primary` 类型是必填 `BaseException`（L851–L855），取消
  路径又总是把原始 `CancelledError` 作为 primary 传入（L907–L917）；文档所说“无 primary 时
  返回首个 cleanup error”在该调用图中无法发生；
- normal/ordinary-error 的 `finally` 直接用 `_ = await drain_pending_candidates()` 和
  `_ = await cancellation_safe_join_record(...)` 丢弃 cleanup error（L921–L925）；
- `abort_all()`/`release_all()` 的声明返回 `None`（L738–L744），但调用方又需要按 canonical
  顺序取得 cleanup error 来选择 caller-visible 结果（L931–L956），没有错误收集通道。

这会导致成功路径的 cleanup failure 被静默吞掉，也无法证明普通异常、外部取消和无 primary
路径具有一致的可观察语义。L1423–L1425 的“cleanup 不覆盖更早错误”不足以解决没有更早错误
时应返回什么。

**关闭条件：** 分别给出 success、ordinary-error、invocation-cancellation 三条路径的
   cleanup accumulator 和返回规则；明确 `primary` 是否可为 `None`，以及 coordinator 的
   cleanup operation 如何返回/汇总错误。任何一个 owner 的 cleanup error 都必须在继续其他
   owner 后按既有优先级处理；不得静默丢弃、包装成新 public error，或因 cleanup error 跳过
   parent/child 的后续 abort/release。

### LO-IR63 ——新 cancellation 语义与测试 manifest 仍不能同时成立

Requirements 已冻结 invocation-level cancellation 的语义：parent 和每个 live child 各自
执行既有 `AbortGraphRun`，不产生 orphaned-claim recovery（requirements §3.6、§6、§9）。
Target 也采用了该新语义（L1311–L1338、L1636–L1641），但 test manifest 同时要求：

- 所有既有 case、名称、数量、主断言全部保留（L1542–L1544、L1601–L1604）；
- `test_graph_api.py` 的旧取消测试仍断言取消后只有 `StartGraphRun + ClaimGraphExecution`，
  下一次 invocation 才 fence/recover（`tests/execution/test_graph_api.py:2408-2451`）；
- `test_executor.py` 的旧 session 测试仍断言取消后保留 exact lease、下一次再 fence/reclaim
  （`tests/execution/test_executor.py:1575-1603`）；
- `test_resource_protocol.py` 被列为 KEEP，而其取消测试仍断言最后 transition 是 claim、
  execution token 保留（`tests/execution/test_resource_protocol.py:304-314`）。

这些主断言正是新语义要删除的 orphaned-claim/recovery 行为，不能靠“只迁移调用”同时满足。
另外，target L1328–L1329 对 standalone 无 child 保留旧 contract，L1636–L1638 又将 nested
entry/有 child 与 standalone 分开，必须和 requirements 中 parent 也执行 abort 的表述统一。
`_NodeCancelled` 在 L894 被描述为内部“消失”，但既有 node-cancellation case 仍要求外部
`CancelledError` 行为；需要明确内部 union 消费与 public caller-visible 异常的区别。

**关闭条件：** 重写 manifest 的保留规则：保留测试目的、case 覆盖和错误类型，但允许明确
   修改与旧 orphaned-claim 语义相冲突的 transition/token 主断言；逐项列出 standalone、
   nested、node-origin cancellation 和 invocation-origin cancellation 的期望序列。若某个旧
   recovery case 仍需保留，必须标注它测试的是独立的 commit-ack-loss 场景，而不是
   `Graph.run()` task cancellation。不得删除或以新测试替代原有行为覆盖。

### LO-IR64 —— source scan 没有覆盖 `child_state()`，manifest 也未逐项闭合

Target L1507–L1537 声称给出了完整 direct-consumer scan，但命令只包含：

```text
child_scope_run_for_activation|child_graph_run_id|project_start_graph_command|
GraphRunContext|state_at\(|replace_state\(|replace_child\(
```

其中漏掉了 `child_state\(`。当前源码仍有直接调用/定义：

```text
src/mote_kernel/execution/family_driver.py:195
src/mote_kernel/execution/run_context.py:399,405,414
```

这不是纯命名问题：`family_driver.py:195` 仍通过 parent context 查 child binding，而 target
又要求删除 parent-side child lookup；`run_context.py` 的方法则是要迁移或删除的 ownership
边界。仅在 manifest 中写“run_context MODIFY”不能证明所有 direct caller 已归类，尤其还要
处理 `child_states` 字段读取和 facade/invocation 中的 `state_at` 调用。

**关闭条件：** 在 target 中给出包含 `child_state\(`、`child_states`、`state_at\(`、
`replace_state\(`、`replace_child\(` 的完整 scan，并逐项列出每个 production/test consumer 的
KEEP/MODIFY、唯一 owner 和迁移后的调用。scan 发现的新文件必须先加入 manifest，再开始编码；
不得用 alias、反射或 AST-only 门禁隐藏 direct consumer。

## 5. 已对齐的边界（本轮不应重新扩张）

- parent 无需知道或保存 child `run_id`；child owner 自己创建、校验和使用 identity。不同
  parent `run_id` 下的 deterministic non-collision 是 child identity proof，不是 parent lookup。
- 同一 immutable `CompiledGraph` 的跨 parent 重叠复用属于 caller precondition；本 change unit
  不新增乐观锁、全局 registry、overlap gate 或拒绝逻辑。
- parent 不维护 child authoritative state 副本；continuation 中既有 binding/frame 只能作为
  immutable、transient transport evidence，不能变成 live mirror。
- child exact commit 后 parent boundary、settlement、routing 或 result 失败时保留 child
  confirmed fact；不跨 owner 回滚、重试或 failover。
- invocation cancellation 可以向下传播，但 parent 没有 child 状态提交权；每个 owner 独立
  使用同一既有 `AbortGraphRun` transition/commit 逻辑。typed child failure 和 ordinary node
  exception 不广播 sibling。
- State schema、reducer、Store protocol、public API、continuation/frame ABI 和 public result
  shape 保持不变；不新增 persistence、跨 invocation load/recovery、custom durable record、
  relay、receipt 或第二 runner。
- target 中的 `_SealedOwnerEvidence`、`_SealedContinuationContext` 等只能被理解为 private、
  transient、不可复制的实现包装；应在文档中明确这不等于新增 public/durable sealed record，
  以免与 L47–L50 的禁止项发生字面冲突。

## 6. 验证记录与下一步

本轮只做了只读核对：

```text
target 行数        = 1679
target SHA256      = 87db973a6811194a81f4a78ab4b4ee8f7eb80c821b8d55317ad168a6d0c69530
requirements SHA   = 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6
previous review SHA= 6143a785c050be0591afc6f5b11f1c439d934289f8575f477ec8bcfaf0604e08
production HEAD    = ebcd043fdfe324c610328a08cb1a3e8a14b37e10
```

没有运行或修改 production、State、Store、API 或 tests；没有清理、重置或覆盖工作区中的
用户 dirty changes。进入编码前仍须先修订 target，完成下一次独立 implementation review，
再按 `AGENTS.md` 运行 strict typing、`make check`、针对性行为测试、build/package、仓库级
pre-commit 和 Markdown 检查；这些检查不能替代本轮 private contract 的闭合。

## 7. 最终裁决

```text
ownership direction                 = CORRECT
distance from eleventh review       = SIGNIFICANTLY CLOSER
parent child-state/child-ID mirror  = NOT REQUIRED / FORBIDDEN
recursive admission handoff         = LO-IR58 OPEN
scope-bound commit/local context    = LO-IR59 OPEN
awaiting evidence lifecycle         = LO-IR60 OPEN
canonical child-first order         = LO-IR61 OPEN
cleanup error propagation           = LO-IR62 OPEN
cancellation test compatibility     = LO-IR63 OPEN
source/test manifest completeness   = LO-IR64 OPEN
persistence/failover expansion      = OUT OF SCOPE
production/test coding authorization= NOT GRANTED
```

结论：**本轮应吸收上述七项修订后再复审；在此之前不开始编码。**
