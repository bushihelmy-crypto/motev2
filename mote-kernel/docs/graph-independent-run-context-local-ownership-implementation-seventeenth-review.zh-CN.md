# 父子图 GraphRun 本地 ownership 实施规范第十七次独立评审

> **结论：`PASS / READY FOR IMPLEMENTATION`（技术评审通过）。**
> 本结论只表示实施方案已经闭合；不改变用户授权边界，也不等同于本轮已经修改
> production 或 tests。

本轮按已批准的 `GRC-LO-001` 及用户冻结的最简原则审核：每个 GraphRun 独占自己的
`run_id`、state、frame、session、executor 和 commit；parent 不持有 child state 或
child `run_id`；父子只传播 typed input/result 与调用级 cancellation；删除 live family
context 和 legacy 旁路；不新增 persistence、failover、跨 invocation child-ID recovery、
registry、overlap gate、第二 runner 或 public API。

## 1. 评审对象

| 项目 | 值 |
| --- | --- |
| implementation target | [graph-independent-run-context-local-ownership-implementation.zh-CN.md](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| target 行数 | `517` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| production 基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | `2026-08-30` |

本轮只读检查 implementation target、requirements 和现有 source contract；未修改
production、State、reducer、Store、public API 或 tests，未运行源码门禁。

## 2. 上轮三项 blocker 的关闭核对

### LO-IR76：live family context / adapter 契约

**已关闭。** 当前稿已经同时固定了以下边界：

- live `GraphRunContext`、四个 mutation 方法、旧 `admit() -> GraphRunContext` 路径及
  direct callers 删除，不保留同名兼容壳；
- `_GraphContinuation` 仍只作为既有 immutable sealed snapshot，入口/出口适配一次读取、
  校验并按 scope 分区，不改写 caller continuation；
- `ChildStateBinding` 和 `ScopedFrameIndex` 只作 transport/validation evidence；
  child boundary 由 matching parent activation 归属，parent 不读取 `binding.state`；
- `ActiveChild`/`CompletedChild`/`AbortedChild` 的 state-bearing projection consumer 和
  `PreparedNestedRun`/`StartMissingChildren` direct consumer 均列入删除闭包，parent 只接收
  typed outcome/output/status。

因此“保留 sealed ABI”与“删除 live 内部 projection/context”已被区分，没有留下第二份
state truth 或可由实现者自行选择的 coordinator 分支。

### LO-IR77：terminal child 一次性交接

**已关闭。** 当前稿采用唯一线性化顺序：

```text
child exact terminal commit
  -> record.consume_once() 取得 typed outcome/evidence
  -> parent 安装 immutable child boundary
  -> parent 重新 prepare 并完成自己的 SettleGraphNode exact acknowledgement
  -> 清空 handle、退役 record、出口消费已交 evidence
```

在 parent settlement 未获得 exact acknowledgement 前，terminal record 和 owner-local
cleanup 责任保持有效；失败不重读 owner、不重跑 child、不回滚 child，也不建立 registry 或
state mirror。`consume_once` 是唯一 producer，重复/foreign/stale activation 按既有错误
fail-closed；完全相同的 `ParentGraphActivation` 不创建第二条 record，只有合法的新
superstep/activation 才创建新 owner/position。

### LO-IR78：node / waiter / scheduler-close cancellation

**已关闭。** 当前稿给出了同一条可执行的 typed 消费链：

- `_capture()` 将 node callable 自己抛出的 `CancelledError` 转成既有 `TaskRaised`，并由
  concrete session 的 one-shot owner-local marker 交给 `GraphRun.drive_quantum()` 消费；
- waiter 取消 `session.next()` 没有该 marker，只在 session close 完成后进入 facade 的
  invocation unwind；
- `session.aclose()` 只能通过私有 `_SCHEDULER_CLOSE_CANCEL` 注入 scheduler task，marker
  在 `_capture()` 被消费，不进入 event 或 `_errors`；
- parent-driven abort 不回调 ancestor，child-origin 信号只通过既有 typed handoff 由
  parent 决定一次，避免递归 abort/重复 commit。

这保持既有 node-origin cleanup 行为，同时将调用级 cancellation 的 child-first
`close -> 必要 fence -> AbortGraphRun -> 独立 commit -> release` 与 node-local 分支分开，
没有新增 public event、error variant 或 protocol。

## 3. 范围与唯一真相复核

| 检查项 | 结论 |
| --- | --- |
| 每个 GraphRun 独占 state/transition/commit/frame/session/executor | PASS |
| parent 不保存/查询/控制 child state 或 child `run_id` | PASS |
| 不同 parent identity 的既有 child identity 注入性 | PASS |
| 同一 parent 跨 superstep 不复用旧 owner | PASS |
| child terminal 后 parent 只结算自己的 nested node | PASS |
| typed failure / ordinary exception 不广播 sibling | PASS |
| awaiting/resume 保持 RUNNING | PASS |
| invocation cancellation 的双边既有 `AbortGraphRun` | PASS |
| continuation/frame 仅作 immutable transient evidence | PASS |
| State/reducer/Store/public API 无新增协议 | PASS |
| persistence、failover、child-ID-only recovery | OUT OF SCOPE |
| overlap detector、lock、registry、第二 runner | OUT OF SCOPE |
| legacy path / compatibility alias | 明确删除，PASS |

## 4. 实施门槛与非阻塞约束

方案已经可以进入编码，不需要再增加架构层或新的评审类别。编码时只需遵守文档已有
门禁：

1. `phase` 是本地控制语义，不得落成违反 strict typing 规则的字符串 discriminator；
   使用现有 typed boundary/closure 事实表达即可。
2. awaiting/terminal 的 immutable evidence 必须在对应出口适配完成交接后，才释放
   handle；不得通过 owner lookup 补取。
3. 先按文档的 source/test 删除闭包和 manifest 实施，再运行 strict typing、Ruff、行为
   测试、build/package、适用 pre-commit 与 Markdown 检查；不得借机修改 State、Store、
   public API 或新增持久化/恢复能力。

这些是编码验收约束，不是当前 target 的 blocker。

## 5. 最终裁决

```text
ownership direction                 = PASS / converged
LO-IR76 live context deletion       = CLOSED
LO-IR77 terminal handoff            = CLOSED
LO-IR78 cancellation consumption    = CLOSED
scope/persistence/failover boundary = PASS / preserved
implementation readiness             = READY FOR IMPLEMENTATION
production/test changes this turn    = NONE
```

因此，技术上已经可以开始实施。实际改码仍应按用户已授权的范围执行；本评审文档本身不
替代启动编码的操作指令。
