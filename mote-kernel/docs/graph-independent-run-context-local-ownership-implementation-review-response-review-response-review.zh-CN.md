# 父子图 GraphRun 本地 ownership 需求与复审回复审批

> **结论：`CHANGES REQUESTED / NOT APPROVED`。**
> 两份文档的 ownership 主方向正确，但尚未完全落实本轮已经确认的边界：父图不需要
> 持有 child `run_id`，调用级中断要向下传播并由父、child 各自执行同一套
> `AbortGraphRun`，普通节点失败不连带取消 sibling。当前不能据此批准 implementation
> target；先完成下列文字和 contract 修订。

本文件是 docs-only 的独立审批记录，不修改 production、State、Store、protocol、public API
或 tests，也不扩张 persistence、failover、跨 invocation recovery。它只审查需求和复审回复
是否与刚才确认的窄范围原则一致。

## 1. 评审对象与冻结输入

- 需求：[父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
  - SHA256：`8a91cf520650fd127d756aab311714fc084e42e41f76723c56b0df6065b96a1e`
- 复审回复：[父子图 GraphRun 本地 ownership 实施方案评审回复复审回复](graph-independent-run-context-local-ownership-implementation-review-response-review-response.zh-CN.md)
  - SHA256：`28e789fe13704445fb9f7fba89fc551de8bcd853d7304579fb6f72a8a0779b56`
- 对照 target：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
  - SHA256：`e343cfd93c504314b969474e997f5c06a61b7cabcf530e961fbc2fb76ca4da18`
- 评审日期：2026-08-28

工作树中上述文档为本轮既有文档；本文件是唯一新增文件。没有把其他 dirty changes
归因于本评审。

## 2. 审批口径

以下原则以本轮对话的确认结果为准，并与现有 typed State/reducer owner 相容：

1. 每个 `GraphRun` 独占自己的 `run_id`、`GraphRunState`、live frame、continuation、
   transition 和 commit；父、child 不共享可变运行上下文。
2. 父图只发起当前 child 调用并接收结果。父图不需要知道、保存、按 ID 查找/恢复或
   通过 ID 控制 child；child 自己拥有并使用自己的 `run_id`。当前调用若确实需要等待对象，
   只能是不可持久化的 opaque call handle，不是父的 child-state/identity owner。
3. child 的最终结果先由 child 自己确认；父随后只确认自己的 nested settlement。两次
   commit 不组成跨图原子事务。
4. 调用级取消/错误向当前调用链下传；父、child 分别在自己的 owner 上使用既有
   `AbortGraphRun` 转换和 commit。父不能直接替 child 写 state。普通节点的 typed failure
   是局部 settlement，不广播取消 sibling。
5. 只是不新增 persistence/Store/checkpoint/failover；已有持久化机制仍是各自 owner 的
   authoritative state 来源。不能用 child ID 作为新的 lookup/recovery 协议。

## 3. 总体矩阵

| 事项 | 审批结果 | 结论 |
| --- | --- | --- |
| 每个 GraphRun 独占 state/transition/commit | 通过 | 需求 §4.1 与回复总体方向一致 |
| 父不维护 child authoritative state | 通过 | 需求 §4.1、§8 已明确禁止镜像/替换/提交 |
| 父不持有 child `run_id` | **需修订** | 需求允许 transient handle；回复/target 仍把 activation、child coordinate 和 handle tuple 写成编排资料，边界不够窄 |
| child 结果与 parent settlement 分离 | 通过 | 现有 projection/settlement 顺序正确 |
| 调用级取消传播与双边 `AbortGraphRun` | **阻塞** | 回复 LO-RR3 和 target §10.2 明确保留 `RUNNING + orphaned claim`，与已确认语义相反 |
| 普通节点失败与 sibling 隔离 | **需补充** | 文档没有冻结“typed failure 局部、普通异常停止新调度但不主动广播 sibling cancel” |
| awaiting/resume | 通过 | child 保持 `RUNNING`，父不伪造终态 |
| frame/continuation 独立 ownership | **通过但需澄清** | family snapshot 可以是 immutable transport/evidence，不能被写成 live shared continuation/frame owner |
| 既有 persistence 语义 | **需澄清** | “不做 persistence”应改为“不新增 persistence”，不能排除父读取自己的 authoritative state |
| no failover / no child-ID-only recovery | 通过 | 文档排除项与用户范围一致 |

## 4. 必须修订的事项

### LO-A1 — 去掉父持有 child `run_id` 的暗示

需求 §3.5、§4.2、§5.4、§8 允许 parent 通过 transient handle 调用 child，并反复出现
`child_scope_run`、`ParentGraphActivation` 和“父按 activation 派生/校验 child ID”；复审
回复 §2、§3 LO-RR1、§3 LO-RR4 以及 target §4.1–§4.3 又把 coordinator 定义为持有
canonical child handle tuple。这些文字虽然禁止 state mirror 和跨 invocation lookup，但
仍会让实现者把 child `run_id` 当成 parent/coordinator 的必要身份资料。

按已确认原则，必须改成以下唯一 contract：

- parent 的 public/private graph operation 只接收 child graph 的 typed input，并等待一个
  当前调用的 opaque result/handle；不得在 parent state、continuation、result 或 coordinator
  的 durable/可复用资料中保存 child `run_id`。
- child `_GraphRun` 在自己的 owner 内创建、校验和使用自己的 `run_id`。现有
  `child_graph_run_id()` 的不同 parent identity 不碰撞不变量可以保留，但它是 child 内部
  identity 约束，不是 parent lookup、恢复、取消或控制入口。
- 若当前执行循环确实需要一个等待对象，明确其只在本次调用栈存活、不可按 ID 查找，返回
  后立即释放；它不缓存 child state/frame/result，也不代表 parent 拥有 child。
- 不需要为此新增 public type、第二 runner、registry 或新的 identity 字段。

这不是要求删除 child 的内部 identity，而是把 identity owner 与调用方的临时控制对象分开。

### LO-A2 — 取消/调用级错误必须采用双边 abort，删除 orphaned-claim 作为该路径

复审回复 §3 LO-RR3（第 181–202 行）和 target §10.2 仍规定：取消后 session
close/quiesce，保留 active token，不自动 fence，保持 `RUNNING`，下一次 state-only
invocation 再 fence。这正是本轮已经否定的“取消后 orphaned claim”语义，不能审批。

应冻结为：

```text
parent invocation cancellation/error
  -> propagate signal to every live child call in this invocation
  -> each child closes its own session; if an execution token is active, finish the existing
     owner-local fence prerequisite, then apply existing AbortGraphRun to its own state
  -> parent closes its own session and applies the same AbortGraphRun (after the same fence
     prerequisite when needed) to its own state
  -> commits remain separate; no parent write to child state
```

补充约束：

- 已经 `COMPLETED`/`ABORTED` 的 run 不再重复 abort；尚未确认的 candidate 丢弃。
- 没有已确认 run 的 start candidate 不凭取消信号伪造 `ABORTED`；已确认且仍为
  `RUNNING` 的 owner 才按“quiesce（必要时 fence）→ `AbortGraphRun`”顺序处理。
- 这条路径不创建 orphaned-claim recovery 分支，不凭 `child_run_id` 接管，也不引入新的
  status、field、command、跨图事务或 failover。
- 必要的 owner-local fence 只是让既有 `AbortGraphRun` 满足“quiescent running”前置条件，
  不是新增的跨 invocation recovery 或 failover 协议。
- 如果 Store/commit 失败，按各自既有 commit/持久化错误传播；不能借 ownership 文档猜测
  另一方是否成功。
- 必须区分 typed node failure、ordinary node exception 与 invocation cancellation：不能把
  普通节点 failure 误写成全 family abort，也不能把 sibling 的局部失败变成取消广播。

这里需要同步检查 target 开头“observable behavior 不变”的表述：若底层当前
`CancelledError` regression 仍要求保留 active token，应把它限定为 session 清理细节，并
明确随后由各自 owner 完成 abort；不能同时保留“取消后继续 RUNNING、下次再恢复”的旧
结论和本次双边 abort 结论。

### LO-A3 — 明确 sibling 失败边界

需求 §6 的 `child failure/abort` 和 `parent cancellation/exception` 行目前不足以区分
业务失败与调用级取消。应加入可验收的规则：

- child/node 返回既有 typed failure：只结算该 child/node；其他 sibling 不被取消，无法
  调度的节点按既有 session/frontier 规则停止新调度并保留其既有状态。
- node 抛 ordinary exception：沿现有 session 语义停止新 activation，并排空/收敛已经启动
  的任务；不因为一个 sibling 的业务失败而主动向其他 sibling 广播 abort。
- 只有 parent invocation 被取消，或明确发生调用级 abort，才向当前 live child 调用传播
  cancellation/error；每个 owner 仍独立执行自己的 abort transition。

### LO-A4 — “不新增持久化”不等于“父不能读取自己的状态”

需求 §7 和回复 §4 把 persistence、Store、checkpoint 一概列为 out of scope，这作为“不
新增能力”是合理的；但本轮已确认父图可以从既有持久化机制读取自己的 authoritative state。
文档应统一使用：

- 本 target 不改变或新增 Store/persistence 协议；
- parent 只恢复/读取自己的 state，child 只恢复/读取自己的 state；
- 持久化持续失败属于 Store/commit 故障，不由本 target 设计 retry、failover 或 child-ID
  lookup；
- “无 persistence/failover 承诺”只能表示不新增协议，不能表述成现有 state 永远不可读。

### LO-A5 — family evidence 只能是 transport/evidence

这一项总体方向通过，但需在需求和回复中用同一术语重申：现有 family-shaped
`ContinuationSnapshot`、`ChildStateBinding` 和合并后的 `ScopedFrameIndex` 是 immutable、
transient 的传输/校验证据；每个 GraphRun 的 live frame/continuation 仍由各自 owner 负责。
family evidence 不得成为 parent 的 child state mirror，也不能让 parent 通过它驱动 child。

## 5. 已确认且无需扩张的事项

以下内容可以保留，不应借评审再引入新能力：

- `Graph` 仍是唯一 public facade；不新增 public `GraphRun`、`GraphRunRef` 或
  `Graph.run(child_run_id)`。
- State schema、`RUNNING | COMPLETED | ABORTED`、pure reducer、revision/token fence 和
  exact successor 机制保持现有 owner；双边 abort 复用既有 `AbortGraphRun`，不增加第二
  reducer。
- child terminal result 由 child 自己确认，parent 只结算自己的 nested node；父 settlement
  失败不回滚 child，也不重跑 child。父后续是否继续取决于父自己的既有 Store/commit state。
- child awaiting/resume 保持 `RUNNING` 与现有 continuation 语义；不能仅凭 child ID 恢复。
- 同一 immutable compiled child 在不同 parent `run_id` 下的纯 identity 不碰撞不变量可以
  保留；它不变成 overlap admission、registry 或 parent lookup 保证。
- cross-parent overlap、persistence expansion、failover、worker handoff、全局锁和 AST/source
  shape gate 继续不在本 target 内。
- typed child/node failure 不连带 sibling；sibling 的 state/frame/result 仍彼此隔离。

## 6. 需要回写的章节

| 文档 | 必须回写 |
| --- | --- |
| requirements | §3.5、§4.2、§4.4、§5.3–§5.4、§6 的 cancellation/sibling 行、§7 persistence 表述、§8 owner 矩阵、§11 ledger |
| review response | §2 的 handle/identity 描述、LO-RR1 的 operation owner、LO-RR3 全节、LO-RR4 的 evidence 术语、§4 persistence 表述、§6 ledger |
| implementation target（随后的同步变更） | §1.1、§4.1–§4.3、§6.3、§7.1–§7.3、§8、§10.2–§10.4、§11 Step 3/4 |

在这些回写完成并重新计算 SHA256 前，本评审不把旧 hash 对应的文档视为已批准版本。

## 7. 审批结论与门禁

```text
ownership direction                         = PASS
parent child-state mirror                  = FORBIDDEN / PASS
parent-held child run_id                   = CHANGES REQUIRED
opaque invocation call handle              = ALLOWED ONLY TRANSIENTLY
child/parent independent state transitions = PASS
invocation cancellation propagation        = REQUIRED
parent + child AbortGraphRun               = REQUIRED / SAME EXISTING TRANSITION LOGIC
ordinary typed node failure                = LOCAL / NO SIBLING BROADCAST
family snapshot/frame evidence             = IMMUTABLE TRANSPORT ONLY
existing persistence                       = KEEP; NO NEW PROTOCOL
child_run_id-only recovery                 = OUT OF SCOPE
failover / worker handoff                  = OUT OF SCOPE
requirements                               = NOT APPROVED PENDING TEXT CORRECTIONS
review response                            = CHANGES REQUESTED
implementation target                      = NOT READY FOR IMPLEMENTATION
production / State / Store / API / tests   = NO CHANGE IN THIS DOCS-ONLY TURN
```

本结论不是否定“每个图的 GraphRun 自己负责自己的状态”，而是把该原则贯彻到调用、取消、
恢复和 evidence 文字中。修订完成后可重新做一次窄范围审批；不得以本文件推导出
child-ID-only recovery、持久化扩张或 failover 授权。
