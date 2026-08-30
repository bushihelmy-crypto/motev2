# 父子图 GraphRun 本地 ownership 实施规范第十六次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> **方向：`PASS / 已明显收敛`。**

本轮重新审核当前 481 行实施稿，并只按已经冻结的窄范围判断：每个 GraphRun 独占
自己的 `run_id`、state、frame、session、executor 和 commit；父子只传播 typed input、
typed outcome/result 与调用级 cancellation/abort；live family context 和旧路径必须
删除；不新增 persistence、failover、跨 invocation 的 child-ID recovery、registry、
overlap gate、第二 runner 或 public GraphRun。

当前版本已经从“family owner 编排方案”收敛为“owner-local GraphRun + 一个 parent-local
child-call record”。但仍有三处具体的调用/类型矛盾，若直接编码，必然需要实现者自行
选择第二份状态事实、隐藏 owner 引用或新的异常协议。因此本轮仍不能作为开发授权；
下轮只需复核 LO-IR76～LO-IR78，不再增加评审类别。

## 1. 评审对象与基线

| 项目 | 值 |
| --- | --- |
| implementation target | [graph-independent-run-context-local-ownership-implementation.zh-CN.md](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `877dbe1b0ab017030b786bcf8e39eb8ed91f8b9d47499dbdb103a3cb30539698` |
| target 行数 | `481` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| production baseline | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | `2026-08-30` |

本轮只读审核文档和当前 source contract；没有修改 production、State、reducer、Store、
public API、tests 或 implementation target，也没有运行源码门禁。

## 2. 已经收敛并确认通过的部分

以下内容本轮不再重复提出，也不应借修订重新扩张：

- 每个 owner 只有一个 GraphExecutor/ExecutionClaimOwner 和一个 session；child 的 exact
  commit 与 parent 的 nested settlement 分开。
- parent 不保存 child `GraphRunState`、child `run_id`、coordinate、frame、session、task
  或 lookup key；child identity 由 factory 在 child owner 内产生。
- owner tree、owner sequence、provider/plan、scope proof/binder、旧的
  `PreparedNestedRun`/`StartMissingChildren` parent path 和自定义 NodeCancelled event 已被
  明确列为删除对象。
- dynamic lifecycle 已压成一条 parent-local record 序列，不再保留上轮的 historical
  ledger + active tuple 双事实模型。
- `WaitingForChildren` 只在 private drive loop 内消费；facade 只接收 `GraphBoundary`。
- typed child/node failure 与 ordinary node exception 不广播 sibling；只有调用级
  cancellation/abort 才沿 live handle 向下传播。
- continuation/frame 只作为既有 immutable、transient transport/validation evidence；
  不新增 persistence、failover、worker handoff、child-ID-only recovery 或 overlap gate。

这些说明主方向已经正确，当前问题是实现闭合度，而不是方向不可行。

## 3. 仍阻塞编码的三项

### LO-IR76 —— 删除 live family context 后，transport adapter 和 result/request 契约仍有矛盾

目标同时写了：

- L12–14：不修改 `result/request`、sealed continuation/frame 外部契约；
- L36–41、L51–54：删除 `GraphRunContext`、四个 mutation 方法、
  `PreparedNestedRun`/`StartMissingChildren`；
- L24–26、L380：parent projection 不再带 `child_state`，并删除 result.py 中对应字段；
- L175–180：删除 `_context_from_continuation()`/`_continuation()`，入口适配直接按
  coordinate 将 evidence 交给各 owner。

这四组文字还没有给出一个唯一可编码的边界：

1. 基线 `run_context.py:353–370` 的 `_GraphContinuation.admit()` 仍以
   `GraphRunContext` 为返回类型；删除该类后，sealed continuation 的内部 admission
   producer/consumer 是什么没有写出。必须明确“public/ sealed shape 保持不变”与“内部
   obsolete helper 删除”分别指什么，不能让实现者用同名兼容壳或 forwarding alias 填补。
2. 基线 `ChildStateBinding` 仍含 `state`，而 `ScopedFrameIndex` 的
   `ConfirmedChildBoundary` 只含 child coordinate。目标只说“按 coordinate 分区”，没有
   说明 child boundary 如何由 matching `parent_activation` 归属 parent，也没有说明
   grandchild、sibling、historical terminal evidence 的唯一分区顺序。缺少该规则时，
   adapter 很容易再次变成 family state owner。
3. L175 的“每次 admission 只消费一次”必须明确为**本次 admission 对 immutable 输入的
   一次读取**，不能标记或销毁 caller 持有的 continuation；现有行为允许同一个 sealed
   continuation 被多个独立 invocation 只读复用。
4. L51–54 说删除 invocation coordinator，但 L343 又保留“若现有代码保留 coordinator”
   的可选分支。该分支会留下 legacy owner。应明确本 target 不保留 coordinator；facade 的
   method-local record/closure 是唯一当前调用事实，不能再有第二个 coordinator 方案。

**闭合条件（不新增类型/协议）：** 在 `run_context.py` 的责任中写出唯一的 sealed
transport adapter 调用形状：它只校验 immutable snapshot、递归把对应 binding 交给 child
owner、取得 owner-local frame/evidence，并在 export 时一次性合成原有 snapshot；它不构造
live context、不改变 caller continuation、不让 parent 读取 child state。同步修正
“result/request 不变”表述，区分 public/ABI 保持与内部 projection/obsolete consumer
删除。

### LO-IR77 —— terminal child 的一次性结果在“退役”与 parent settlement 之间没有交接点

目标的顺序存在直接断裂：

```text
L120  consume_once() 取得 terminal result/evidence
L122  parent 安装 child boundary
L123  record 退出 active admission、清空 handle
L124  parent 重新 prepare -> claim -> session -> SettleGraphNode
```

同时 L73 禁止 record 保存 state/owner 引用，L137–140 又规定 terminal record 不再参与
admission/settlement，L133–135 却要求 child 的 last-exact state/frame 保留。

因此当前没有合法来源完成下面三个动作：

- parent 在重新 prepare 时如何取得已消费的 `CompletedChild`/`AbortedChild` typed outcome，
  并只结算 parent 自己的 nested node；
- `parent SettleGraphNode` 尚未确认时，已清空的 handle/record 如何继续完成当前 invocation
  的 export 或 cleanup；
- 既有 continuation 仍需要 child state/frame evidence 时，哪个 child owner-local producer
  在 handle 退役后提供它，而不建立 owner registry 或第二份 state 镜像。

这不是要求保存 child state，而是必须固定一个一次性 handoff。二选一即可：

1. terminal record 暂不完全退役，只保留一次性 typed outcome/evidence 到 parent settlement
   exact acknowledgement，随后清空 handle 并退役；或
2. `consume_once()` 直接完成 parent 的既有 nested settlement，再退役 record，后续 export
   只消费 child owner 已交出的 immutable evidence。

无论选择哪一个，都要明确 terminal evidence 的唯一生产者、消费点、失败后的 owner/release
责任，且不得回退到 child state mirror、owner 引用集合或第二 lifecycle collection。

另外，L93“重复 activation 必然创建新 owner/new position”与 requirements 要求的“同一
invocation/continuation 中重复 activation fail-closed”需要统一：**完全相同的
`ParentGraphActivation` 必须拒绝；只有不同 superstep/合法新 activation 才创建新 record**。

### LO-IR78 —— node-origin cancellation 有标记描述，但没有合法的 typed 消费入口；ancestor abort 还可能重复回传

L184–204 已正确选择不新增 public event/variant，并把 node callable 自行抛出的
`CancelledError` 捕获为 `TaskRaised`。但后续仍缺一个可执行的消费边界：

- L229–235 规定 session 最终从 `next()` 原样重抛，并由拥有它的 GraphRun 读取
  invocation-local marker；
- 基线 `engine/session.py:56–74` 的 `GraphExecutionSession` protocol 没有该读取操作，
  `family_driver` 拿到的也是该 protocol；
- 基线 `session.py:268–305` 的 `next()` 对重抛的 `CancelledError` 会进入统一的取消 close
  路径。仅把 `_errors` 和 `TaskRaised.error` 改为 `BaseException`，不能证明 facade 不会
  把 node-origin 误判为 invocation cancellation。

必须在不改变 public protocol/error/result taxonomy 的前提下，写出唯一的 owner-local
typed 消费方式（例如由现有 concrete session owner 返回一次性 disposition，或由 GraphRun
在同一已声明的内部边界消费），并固定：

1. node-origin cancellation：排空已启动 sibling、close/release 当前 owner，按既有
   node-local 规则交给 parent；不进入 invocation-wide abort；
2. waiter cancellation：没有 node-origin 标记，才进入 invocation unwind；
3. scheduler close marker：只由 `aclose()` 注入并在 `_capture()` 消费，不进入 event 或
   `_errors`。

此外，L314、L333、L351、L364 同时引入 `ancestor_abort` closure。当前没有区分“parent
主动沿 handle 向下 abort”和“child 自己产生需要向上报告的 invocation-level signal”；若
前者触发 child closure 再回调 ancestor，会重复 abort/重复 commit。必须固定单向规则和
一次性线性化：parent-driven abort 不回调 ancestor；若确需 child-origin upward signal，
只能通过既有 typed handle result 由 parent 决定一次，不得形成递归 callback 环。

## 4. 不应重新扩张的边界

本轮及下一轮不得借上述闭合条件重新加入：

- family owner tree/ledger、global registry、child-ID map、第二 scheduler/runner；
- 新 public `GraphRun`、handle/result variant、State field/command 或 continuation codec；
- persistence、checkpoint、receipt、retry、failover、worker handoff、cross-invocation
  child-ID recovery；
- overlap detector/optimistic lock；跨 parent 重叠复用 immutable compiled child 仍是 caller
  precondition；
- 通过兼容 alias、旧 mutation caller、legacy-only test 或 AST/helper-count test 保留旧路径。

## 5. 结论与门禁

```text
ownership direction                 = PASS / converged
live family context deletion       = LO-IR76 OPEN
single terminal lifecycle truth    = LO-IR77 OPEN
node/waiter/close cancellation     = LO-IR78 OPEN
State/reducer/Store/public API     = KEEP / no change authorized here
persistence/failover/ID recovery   = OUT OF SCOPE
implementation authorization        = PENDING
review result                       = CHANGES REQUESTED / NOT READY
```

修订只需闭合 LO-IR76～LO-IR78，完成一次最终独立复审；不需要继续增加新的架构层或需求。
在最终复审通过并获得用户明确授权前，本轮不开始 production/test 编码。
