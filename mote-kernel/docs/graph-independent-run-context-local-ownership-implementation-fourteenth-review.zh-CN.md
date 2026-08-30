# 父子图 GraphRun 本地 ownership 实施规范第十四次独立评审

> **结论：CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION。**
> **方向性：PASS / MOVING TOWARD CONVERGENCE。**

本轮可以明确回答：实施文档的方向是在向需求收敛，但还没有收敛到“实现者无需自行选择
owner、生命周期、类型或调用方”的闭合程度。相较第十三次，文档补充了多组 nominal
contract，且大部分历史意见已经吸收；剩余问题集中在六个会直接改变运行时行为或让严格
类型无法成立的结构性缺口。因此本轮不批准编码，也不把文档变长当作方案闭合。

本文件是 docs-only 的独立评审记录。不修改 production、State、reducer、Store/persistence、
protocol、public API、continuation/frame ABI 或 tests；不新增 persistence、failover、worker
handoff、仅凭 child_run_id 的跨 invocation recovery、overlap gate、第二 runner、registry
或 public GraphRun。工作区已有用户修改全部保留。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | 82cd436d98a4769407242c9b527399d96ce30e4fccc08d1c2fec118d9fb9c4fd |
| target 行数 | 2367 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6 |
| 上一轮评审 | [第十三次独立评审](graph-independent-run-context-local-ownership-implementation-thirteenth-review.zh-CN.md) |
| 上一轮评审 SHA256 | 4836208906e9de61eb5c2460f4891d4773e81236cd59474ebc51546f1ee81ba2 |
| production 对照基线 | ebcd043fdfe324c610328a08cb1a3e8a14b37e10 |
| 评审日期 | 2026-08-30 |

评审口径继续以 requirements 的窄范围为准：每个图调用拥有自己的 _GraphRun、state、
transition、commit、executor、session 和 local frame；parent 不保存、查询或控制 child
run_id，只在当前调用栈持有 opaque handle；child 先确认自己的结果，parent 再结算自己的
nested node；invocation cancellation 沿 live handle 向下传播，各 owner 独立使用既有
AbortGraphRun。不做新的持久化协议、failover 或仅凭 child_run_id 的跨 invocation 恢复。

## 2. 与第十三次相比的真实收敛

| 上轮意见 | 本版变化 | 本轮判断 |
| --- | --- | --- |
| LO-IR65 递归 admission 交接 | 增加 SubtreeCleanup、candidate 的 object-identity 绑定、direct_children 和 append_fresh_owner（target L116–363、L685–715） | 明显收敛，但动态 owner 生命周期仍未闭合，见 LO-IR70 |
| LO-IR66 scope-bound commit | 增加单参数 ScopeBoundCommit、binder、scope proof，以及 commit=None 的明确分支（L138–171、L830–937） | 方向正确；factory 到 owner 的 proof/构造交接仍需唯一调用图，见 LO-IR75 |
| LO-IR67 awaiting/result boundary | 增加 slot/evidence matching 和 terminal/runnable 分支描述（L379–414、L1530–1648、L1731–1780） | 文字更完整，但 internal shape 仍被声明成 public result，见 LO-IR71 |
| LO-IR68 cancellation order | 增加 root 去重、postorder、explicit abort caller 和 standalone 规则（L1409–1455、L1917–1947） | 大部吸收；release_all 的 root 交接仍矛盾，见 LO-IR74 |
| LO-IR69 scheduler/session origin | 增加 NodeCancelled union、close marker 和来源矩阵（L1258–1349、L2277–2284） | 行为意图明确；concrete handle 类型及 session 消费仍未闭合，见 LO-IR72 |

因此本轮不是方向倒退，也不是重新引入 parent child-state mirror 或 child run_id 编排；
但“已写出 operation 名称”尚不等于 operation 的输入、输出、所有者和失败路径唯一。

## 3. 仍然阻塞编码的事项

### LO-IR70 —— dynamic fresh owner 的 tree、active slot 与历史 owner 生命周期未闭合

target 将 CanonicalOwnerTree 描述为 immutable（L316–321），将 CanonicalOwnerSequence 描述为
同一 invocation 的 immutable ledger（L323–329）；direct_children 实际依据 owner.children
（L344–350），而 _GraphRun.owner_tree 仍是一个独立字段（L440–454）。append_fresh_owner
只返回新的 sequence（L352–363），正常 drive 另外更新 slot tuple（L749–765），文字则说
sequence 与 slot 在一个无 await 点替换（L939–949）。文档没有一个 nominal record/operation
同时替换 owner tree、sequence、active slots 和当前 superstep 的 activation 状态。

反例：

~~~text
superstep N       parent 创建 nested node a -> child owner a1 terminal
parent settlement 成功
superstep N + 1   同一 nested node a 再次 activation -> 新 child owner a2
~~~

当前文本至少有三种互相不兼容的实现选择：

1. 保留 a1 和 a2。此时 direct_children 会看到两个节点；如果旧 terminal slot 被移出，
   classify_child_slots 的一一对应前提（L371–377、L1733–1740）失效。
2. 从 tree/sequence 删除 a1。此时 monotonic ledger、export/partial/cleanup 的历史
   evidence 失去唯一位置，且删除操作没有定义。
3. 复用 a1 的 position/slot。此时违反“新 activation 不复用旧 slot、handle、sequence”
   （L761–765、requirements §4.3）。

必须补齐：

- 明确定义 canonical historical ledger 与当前 active direct slots 的关系；
- 定义 terminal owner 何时、由谁、以什么纯 operation 从 active slots 退役；
- 以一个唯一的无 await 线性化操作原子替换 tree、sequence、slot tuple 和 activation
  生命周期，不能让 _GraphRun.owner_tree 留在旧版本；
- 为同一 node 跨 superstep 的新实例给出 deterministic position 规则；
- 明确 historical evidence-only owner 不再参加新的 admission/settlement，但在需要的
  export/cleanup 中如何恰好消费一次。

这些是 transient owner bookkeeping，不要求 parent 持有 child run_id，也不允许借机新增
child-ID map、registry 或跨 invocation recovery。

### LO-IR71 —— result boundary 仍把 execution-internal WaitingForChildren 当作 GraphResult

target 的 _GraphRun.drive_quantum() 声明只返回 GraphBoundary | None（L455），但
project_graph_result() 的 disposition 却接受 ChildSlotDisposition | GraphBoundary，并声明
返回 GraphResult（L1530–1541）。其 staging 分支在 runnable slot 时直接返回
WaitingForChildren（L1593–1601）。同一文档又明确 WaitingForChildren 仅是
execution-internal shape、不能进入 GraphResult（L773–789、L1711–1722）。

调用图也没有消除该矛盾：root.drive_quantum() 的结果直接传入 project_graph_result()
（L1364–1369、L1785–1818），但没有 private loop 说明谁继续消费 runnable slot、何时再次
调用 child handle、何时才形成真正的 GraphBoundary。

必须二选一并写成唯一 nominal 链：

- drive_quantum() 内部消费所有 runnable slot，只向 facade 返回 AwaitingResume、
  CompletedGraph 或 AbortedGraph；project_graph_result() 只接收 GraphBoundary；或
- 增加明确的 private waiting loop/internal projector，使 WaitingForChildren 在内部被
  消费后再进入 GraphBoundary，且保证它永远不会作为 GraphResult 返回。

同时要写明 runnable 分支不消费 export envelope；继续 drive 使用哪个 owner-local provider；
awaiting、terminal 和 result projection 的唯一 boundary 及 envelope destroy 点。不能仅用
“见下文分支”同时保留两个不相容的返回类型。

### LO-IR72 —— NodeCancelled 的 concrete task handle 与 node-origin 消费链仍未闭合

target 将 SchedulerTaskHandle 写成 asyncio.Task[SchedulerEvent]（L1266–1270），随后又给
这个别名声明 cancel_for_close()（L1292–1294）。asyncio.Task 本身没有该方法；实现者必须
自行选择 wrapper、protocol 或外部函数，违反了本文件要求的唯一 strict-typing 契约。

更重要的是行为链仍只有意图没有完整消费定义：

1. capture 把没有 close marker 的 CancelledError 变成 NodeCancelled（L1277–1289）；
2. drain_scheduler_events 将其 error 交给 record_error（L1333–1347）；
3. target 允许 SessionError 包含 asyncio.CancelledError（L1271–1319），但没有给出
   NodeCancelled 在 session 中如何变成既有 node-local disposition，又保证不从
   session.next() 逃到 facade 的 CancelledError 分支。

现有基线中 scheduler 只返回 TaskResult | TaskRaised，session errors 只保存 Exception
（src/mote_kernel/execution/engine/scheduler.py:80–148、src/mote_kernel/execution/engine/session.py:232–305）。
简单增加 union 或 marker 声明，不能证明 node callable 自己的取消与 scheduler/aclose 注入
的取消在所有 await 边界可判定，也不能证明既有 public node-cancellation 行为保持不变。

必须补齐：

- concrete SchedulerTaskHandle 的完整接口及 marker 注入/消费位置；
- scheduler event、pending event、next_completion、session error container、
  record_error、drain_scheduler_events 和 aclose 的最终 union/signature；
- node-origin cancellation 在 session 内的确切 disposition/重抛或转换规则；
- waiter cancellation、scheduler close marker、node callable 自行抛出三者的可判定边界；
- 测试证明 node-origin 不进入 facade 的 INVOCATION_CANCEL，而外部 invocation cancellation
  不被伪造成 NodeCancelled。

不得因此新增 public error/result variant，也不得把普通节点 failure 变成 sibling 广播。

### LO-IR73 —— root fresh construction 与递归 admission 失败的整体 unwind 没有唯一 owner

continuation admission 的伪代码在 root 建立后逐个 admission/register child（L562–634）；
fresh root 的唯一调用图只写成“facade 生成/验证 root coordinate → root _GraphRun + factory”
（L1785–1796），没有给出 root StartGraphRun exact acknowledgement、root input frame
安装及 candidate 生命周期的完整顺序。

当以下任一窗口失败时，文档没有指定仍持有清理责任的 caller：

- root 尚未建立时 caller cancellation 或 root start/commit 失败；
- root exact acknowledgement 后 input frame、executor、session 或 factory 安装失败；
- continuation admission 中 child 1 已 register，child 2 的 admission/append/register 失败；
- root 已创建但 InvocationAdmission 尚未返回，外层 facade 因而尚未取得 coordinator。

L1852–1856 只概括“清理已建立的 owner-local candidate”，不足以说明已注册 sibling、root
owner 和祖先 closure 谁负责清理，也没有把失败 report 交给 caller。若实现者把 coordinator
提前泄漏给 facade，会违反 admission 尚未完成的生命周期；若不泄漏，则必须有一个 setup
guard/method-local unwind 负责所有已建立 owner。

必须分别给出 fresh root 与 continuation admission 的同一失败保护形状：

1. root proof、start command、exact ack、input frame、owner/factory 建立的线性顺序；
2. 每个 cancellation/commit/frame/registration 点的 candidate phase；
3. 已注册 sibling、当前 root、递归 descendant 的 child-first cleanup 责任与 report 交接；
4. setup 失败后如何销毁 evidence/provider/handle，且不伪造 ABORTED、不重试、不生成
   新 recovery path。

### LO-IR74 —— release_all(order) 没有 root，却要求调用 root 的递归 release

InvocationRunCoordinator 的 contract 只有 handles，且明确禁止保存 root/owner（L482–495）；
其中 abort_all() 有 root 参数，但 release_all() 只有 release_all(order)（L491、L1099–1108）。
外层 cancellation/success caller 仍调用 coordinator.release_all(order)（L1354–1389），
而后文又要求它把 order 交给 root 的 release_descendants_and_self()（L1435–1440）。当前
没有合法来源取得这个 root。

必须选择并固定一个方案：

- 将 private release_all() 改为显式接收当前 root（coordinator 不保存，只在调用期间转发）；或
- 将它定义为 facade 的 method-local closure，由同一 setup frame 捕获 root。

无论选择哪一个，都要明确 nested entry 的祖先 release、family sequence 的去重和 cleanup
report 返回顺序；不能通过给 coordinator 增加隐藏 root 字段来补洞。

### LO-IR75 —— factory 到 _GraphRun 的 parent scope、proof 与祖先 abort handoff 仍不唯一

本版已经补出 ChildScopeFactory 的 constructor 和 _GraphRun.start()/admit() 的 nominal
参数（L830–887），这是实质进展；但 _GraphRun 仍要求持有 parent_scope_run、
parent_activation、child_factory 和 parent_abort_invocation（L440–476），而 start()/admit()
的签名并不接收 child_factory 或 ancestor-abort closure。文档只说“成功后各自绑定新的
child factory”以及“construction frame 捕获 ancestor chain”（L889–911、L951–955），
没有定义该绑定的唯一 operation/参数。

另外，scope proof 的 owner 文字不一致：contract 说只有 factory/GraphRun 能 issue proof
（L147–155），而 fresh flow 又写成“facade 已创建”的 proof（L892–897）。如果实现者自行
在 facade、factory 或 _GraphRun 中创建 proof，就可能把 parent scope capability 传给 child，
或丢失 nested owner 的 nearest-parent abort 链。

必须补齐一个不可歧义的 construction handoff：

- 明确由哪个 private operation 同时创建 child owner、child factory、scope proof、bound
  commit、parent scope bookkeeping 和 parent-abort closure；
- 将这些值作为显式参数或同一 sealed construction result 交接，不能从未声明的闭包/字段
  隐式捕获；
- 证明 proof.owner_scope 等于 child.scope_run、parent_activation 与 factory-bound parent
  scope exact 相等，且 ancestor closure 不会重复 abort/release；
- 保持 parent/coordinator 只看 opaque handle，不把 proof、scope、state 或 factory 暴露给
  parent。

这项不要求 parent 持有 child run_id；它只要求 child owner 的内部构造责任完整。

## 4. 次级但应在编码前一并澄清的缺口

以下问题目前未另立新的范围，不改变上面六项结论；修订时应在同一章节中消歧：

1. record_cleanup_errors() 被称为“既有 invocation/session accumulator”（L1244–1248、
   L1402–1404），但当前源码没有该 accumulator 的 owner、字段或生命周期。应指定
   method-local closure/既有 session owner，不得实现者自行新增隐藏全局容器。
2. classify_invocation_abort() 被描述为既有 lifecycle classifier 的 caller（L1322–1331、
   L1393–1397），当前 production scan 未找到对应的 explicit invocation-abort 来源。应
   明确现有来源和调用点；若当前没有该来源，就删除不可达分支或把它限定为未来 change
   unit，不得为了本 target 擅自增加 public abort API。
3. target 同时列 _GraphRun.claim_owner 与 GraphExecutor 内部的 _claim_owner
   （L816–820、L440–448；基线 src/mote_kernel/execution/executor.py:68–76）。应明确
   它们是同一个 owner instance 的传递视图，还是删除重复字段；不能让一次 scope 出现两个
   可消费 claim owner。
4. sealed.records[0] 被直接当作 root（L574–585），应在索引前给出非空、root-first 和
   family proof；否则 malformed empty evidence 的错误分类仍由实现者决定。

这些澄清不能扩展 State、Store、public API 或 recovery 能力。

## 5. 已对齐且本轮不应重新扩张的边界

- 每个图的 _GraphRun 独占自己的 state、transition、commit、executor、session 和 frame。
- parent 不维护 child authoritative state，不保存、查询或按 ID 恢复 child run_id；当前
  调用最多持有 transient opaque handle。child identity 由 child owner 自己创建/校验。
- child terminal result 先由 child exact commit，parent 随后只安装 boundary 并结算自己的
  nested node；parent 失败不回滚、不重跑 child。
- invocation cancellation/explicit abort 可以沿 live handle 向下传播；parent 没有 child
  state 提交权，child 和 parent 各自执行既有 AbortGraphRun。typed child failure、普通
  node exception 不广播 sibling。
- awaiting 继续保持 RUNNING，只沿既有 continuation/state pairing 重新 admission；不支持
  仅凭 child_run_id 的跨 invocation 恢复。
- 现有 owner-local state/commit/read 语义保持；本 target 不新增 persistence、Store、
  checkpoint、failover、worker handoff 或 retry。持久化失败属于各 owner 的既有错误语义。
- 同一 immutable CompiledGraph 的跨 parent 重叠复用仍是 caller precondition；不新增
  overlap gate、全局锁、registry 或第二 runner。不同 parent identity 下的 child identity
  non-collision 仍是 child identity proof，不是 parent lookup 能力。
- Graph 仍是唯一 public graph facade；不新增 public GraphRun、handle、result variant、
  State 字段、reducer command 或 continuation/frame ABI。

## 6. 验证记录与门禁

本轮完成了只读核对：

~~~text
target 行数         = 2367
target SHA256       = 82cd436d98a4769407242c9b527399d96ce30e4fccc08d1c2fec118d9fb9c4fd
requirements SHA    = 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6
previous review SHA = 4836208906e9de61eb5c2460f4891d4773e81236cd59474ebc51546f1ee81ba2
production HEAD     = ebcd043fdfe324c610328a08cb1a3e8a14b37e10
~~~

未运行或修改 production、State、Store、API 或 tests；未清理、重置或覆盖工作区用户改动。
本文件也不回写 implementation target。

编码前必须先闭合 LO-IR70～LO-IR75 及第 4 节的签名/owner 澄清，再进行一次独立
implementation review。只有独立评审通过且用户明确授权后，才进入 production/test 实施；
届时仍须按 AGENTS.md 运行 strict typing、Ruff、format、针对性行为测试、make check、
build/package、仓库级 pre-commit 和 Markdown 检查。complexity gate 继续按用户范围记录为
USER-EXCLUDED / NOT RUN。

~~~text
direction                    = PASS / MOVING TOWARD CONVERGENCE
implementation closure       = NOT CLOSED
implementation authorization = PENDING
production/test changes      = NONE
review result                = CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION
~~~
