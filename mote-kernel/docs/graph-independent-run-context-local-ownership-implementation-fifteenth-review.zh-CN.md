# 父子图 GraphRun 本地 ownership 实施规范第十五次独立评审

> **结论：CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION。**
> **方向性：PASS / 本版已明显向目标收敛。**

本轮重新审核了重写后的 413 行实施稿。它已经不再是上一版的增量补丁：旧的 family
owner tree、owner sequence、provider/plan、scope proof/binder、invocation coordinator
和自定义 NodeCancelled event 都已从方案中删除，调用图也压缩为三条本地路径。

但是，当前稿仍有三个会阻止编码的真实缺口。它们不是新增需求，而是“删掉 family
context、零 legacy、复用基础设施、唯一真相”这四条原则在现有代码边界上的最后落点。
本轮不再沿用第十四次的六项清单，也不继续增加新的抽象类别；下次只复核本文件列出的
三项是否闭合。

本文件是 docs-only 独立评审记录。不修改 production、State、reducer、Store/persistence、
protocol、public API、tests 或 implementation target；不新增 persistence、failover、
worker handoff、仅凭 child_run_id 的跨 invocation recovery、overlap gate、registry、
第二 runner 或 public GraphRun。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | cee6b84914ea2b1fb66b74235896dd8efe2d85ecb87fedc07065d42d068aba1f |
| target 行数 | 413 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6 |
| 对照评审 | [第十四次独立评审](graph-independent-run-context-local-ownership-implementation-fourteenth-review.zh-CN.md) |
| production 对照基线 | ebcd043fdfe324c610328a08cb1a3e8a14b37e10 |
| 评审日期 | 2026-08-30 |

本轮采用用户已明确的最高优先级原则：

1. 每个 GraphRun 自己拥有 run_id、state、transition、commit、frame、session 和 executor；
2. 父子不读取或修改对方 state，只传播 typed input、typed result 和 cancellation/abort signal；
3. 删除 live family context 和 obsolete/legacy execution path；
4. 复用既有 Graph、GraphExecutor、GraphExecutionSession、commit_transition、纯 reducer、
   State command、Store/commit port 和既有 public facade；
5. state 只有 owner 自己的一份 authoritative truth；
6. 不新增持久化、failover、跨 invocation child-id recovery 或新的公共协议。

## 2. 本版已经正确吸收的内容

以下内容本轮视为已关闭，不再重复提出：

- GraphRun 是每个图调用的唯一 runtime owner；每个 owner 只保留一个
  GraphExecutor/ExecutionClaimOwner（target L18–20、L299–302）。
- 动态 activation 不再使用 family owner tree/sequence；position、active/terminal
  生命周期被限制在当前 invocation frame（L53–134）。
- WaitingForChildren 只在 GraphRun 的 private loop 内消费，facade 只接收 GraphBoundary
  和既有 GraphResult（L136–169）。
- fresh/existing 共用一个 construct_child handoff；root setup、admission 失败和
  release 的责任回收到 method-local setup/facade closure（L213–325）。
- child terminal commit 与 parent nested settlement 分离；parent 失败不回滚 child。
- typed child failure/ordinary node exception 不广播 sibling；调用级 cancellation 才沿
  opaque handle 向下传播。
- 不新增 child-ID lookup、registry、persistence、failover 或跨 invocation recovery。

因此本轮结论不是“方向错误”，而是“主方向已经收敛，但仍需消除三个边界歧义后才能编码”。

## 3. 编码阻塞项

### LO-IR76 —— live family context 和 state-bearing projection 仍没有真正删除

实施稿 L35–38 仍保留 GraphRunContext 作为 invocation 入口/出口对象，L40–45 又把
ChildStateBinding、ScopedFrameIndex 和 _GraphContinuation 作为 family transport ABI。
同时 L24–26 规定 parent 保留既有 ActiveChild、CompletedChild、AbortedChild、
AwaitingResume projection。

这与“父子不知道对方 state、删除 family context、零 legacy”存在具体冲突：

- 基线 src/mote_kernel/execution/run_context.py:379–421 的 GraphRunContext 仍持有
  child_states，并提供 child_state、state_at、replace_state、replace_child；
- 基线 src/mote_kernel/execution/result.py:176–195 的 ActiveChild、CompletedChild、
  AbortedChild 仍直接携带 child_state: GraphRunState；
- 如果实现只是不再调用这些方法，而保留类型和旧 mutation path，仍然留下 legacy 双路径；
- 如果 parent 继续接收带 child_state 的 projection，parent 在类型层面仍然知道 child state，
  也就不是“只传播结果”。

必须在 implementation target 中做出唯一选择并写出删除清单：

1. live execution 中删除 GraphRunContext 及其 parent-side child state/mutation API；不要
   用一个“只在入口/出口使用”的同名兼容壳保留旧 family owner。现有 continuation 若是
   normative transport，只能由独立的入口/出口适配直接消费，不能再作为 live context；
2. child→parent 的 call result 只携带既有 typed outcome/output/status，不携带
   GraphRunState、revision、token、frame、session 或 child run_id。若某个现有 projection
   的字段属于旧 family path，必须删除其 direct consumer 并迁移到 owner-local operation，
   不能用 alias 隐藏；
3. 明确唯一 authoritative state：child state 只存在 child GraphRun；父的 nested
   settlement 只写 parent GraphRun。continuation/transport 若必须保留，只是一次性输入/
   输出载体，不是第二份 state truth，也不进入 parent active record；
4. source/test manifest 逐项列出要删除的 GraphRunContext mutation caller、旧
   PreparedNestedRun/StartMissingChildren path 和 state-bearing projection consumer。

这不是要求新增 public result variant，也不是要求删除既有 State/reducer/Store；要求的是
把旧 family context 的 live ownership 和 legacy caller 真正移除。

### LO-IR77 —— owner_entries 与 active tuple 仍形成两份生命周期事实，terminal evidence 没有 owner handoff

实施稿 L57–75 定义 historical owner_entries，L63–69 又定义 parent direct active tuple；
L111–134 再分别修改两者的 terminal 状态。文档称它们不是 authoritative state，但没有
规定两份生命周期事实如何保持单一线性化，且 active tuple 仍包含 latest_projection
（L63–65）。

更具体的调用缺口是：

- terminal owner 从 active tuple 退役、handle 同时退役（L71–75）；
- export/continuation 随后却要按 ledger position“向对应 child owner 请求”evidence
  （L131–134）；
- owner_entries 只记录 position、parent activation 和 terminal 生命周期（L57–61），
  没有 child owner、仍存活的出口 closure 或已经捕获的 terminal typed result；
- 因而实现者必须自行决定是保存 owner 引用、复制 state、保留 handle，还是增加隐藏 registry，
  每一种选择都会违反本 target 的边界。

必须把它压成一个可证明的本地事实模型：

- 一个 parent-local child-call record 只保留当前 invocation 所需的 opaque handle、activation、
  lifecycle phase 和一次性 typed result/evidence closure；不要再维护互相独立的 historical
  ledger 与 active tuple；
- terminal settlement 的无 await 线性化顺序固定为“先取得并消费 child 的 typed terminal
  result，再从 active record 退役”；出口不再回头寻找已退役 owner；
- 如果 continuation 确实需要历史位置，历史记录只能是不可变的 position/outcome evidence，
  并明确其生产者和一次消费点，不能成为 child owner/state/frame 的索引；
- repeated activation 只产生新的 local record/position，旧 record 退役后不参加新的
  admission/settlement；不复用旧 handle、state 或 position；
- 明确 parent 看到的 projection 不含 child state，并给出 fresh、terminal、awaiting、
  setup-failure 四种 record 生命周期。

这样可以保留 deterministic position，也不需要 child-ID map、family tree 或跨 invocation
恢复。

### LO-IR78 —— scheduler/session 仍没有闭合 node-origin CancelledError 的既有消费语义

实施稿 L171–211 选择“不新增 NodeCancelled event”，让 node callable 自己抛出的
CancelledError 进入 TaskRaised，并把 TaskRaised.error/session._errors 的内部类型放宽为
BaseException。这个方向比上一版简单，但行为链仍缺一环：

1. node-origin CancelledError 进入 TaskRaised；
2. session 记录并执行“既有 node-local close/drain/disposition”；
3. 没有定义该 disposition 的具体返回/转换点，也没有证明它不会继续从
   session.next() 以 CancelledError 逃到 facade；
4. 现有 session 的 next() 在错误容器非空时会直接抛出首个 error
   （基线 src/mote_kernel/execution/engine/session.py:232–305），因此仅修改注解不足以
   保证 facade 不把 node-origin 当成 invocation cancellation。

同时，scheduler 的 close marker helper（L185–199）需要与现有 task lifecycle 精确衔接：
waiter 取消、scheduler.aclose() 注入取消、node callable 自行抛出取消，三者必须有可执行的
来源判定和消费路径，而不是只列在 prose 矩阵中。

必须补齐：

- TaskRaised、pending event、session error container、next()、drain 和 aclose 的最终
  typed signature；
- node-origin CancelledError 在 session 内的唯一 disposition（重抛到哪个 owner-local
  边界，或转换为何种既有 node error），并证明它不会进入 facade invocation unwind；
- waiter cancellation 和 close marker 的传播/清理顺序，保证 scheduler 注入的取消不被
  当作 node-origin event；
- 保留现有 node-cancellation public 行为和测试 case，不新增 public error/result variant，
  不把 ordinary node failure 广播给 sibling。

## 4. 本轮不应重新扩张的边界

- 不恢复第十四次评审中的 owner tree、owner sequence、provider/plan、scope proof、
  binder、invocation coordinator 或自定义 NodeCancelled event。
- 不把 parent 持有 child run_id、child state、child frame 或 child session 作为“方便实现”
  的临时方案。
- 不新增 persistence、checkpoint、Store protocol、failover、worker handoff、retry 或
  仅凭 child_run_id 的跨 invocation recovery。
- 不改变 Graph public facade、State schema、reducer command、现有 commit port 或
  continuation 的外部契约；但旧 family context 的 live mutation caller 不得以兼容别名保留。
- 不因同一 immutable CompiledGraph 的跨 parent 重叠复用而增加 registry、锁或 overlap gate。
- parent 只接收 child 的 typed input/result/signal；child 和 parent 各自提交自己的 state。

## 5. 最小目标调用图

实施文档最终应收敛到下面这一条，不再增加中间协议：

~~~text
Graph.run()
  -> root GraphRun(own run_id/state/frame/session/executor/commit)
  -> nested node:
       child GraphRun(own run_id/state/frame/session/executor/commit)
       parent -> typed input
       child  -> typed outcome/output/awaiting
       parent -> settle only its nested node
  -> GraphResult

invocation cancellation:
  parent -> opaque live handle -> child owner-local close/fence/AbortGraphRun
  parent owner-local close/fence/AbortGraphRun
  -> release
~~~

现有 GraphExecutor、GraphExecutionSession、commit_transition、纯 reducer 和 State command
继续复用；不保留 family live context，也不建立第二套执行真相。

## 6. 验证记录与结论

本轮只读核对：

~~~text
target 行数         = 413
target SHA256       = cee6b84914ea2b1fb66b74235896dd8efe2d85ecb87fedc07065d42d068aba1f
requirements SHA    = 1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6
production HEAD     = ebcd043fdfe324c610328a08cb1a3e8a14b37e10
~~~

本版相较旧稿已经显著收敛；第十四次评审中关于 owner tree、result boundary、root setup、
release 参数和大量中间协议的意见不再重复。当前仍是：

~~~text
方向                         = PASS / 正确收敛
family context legacy debt   = NOT CLOSED
single local lifecycle truth = NOT CLOSED
node cancellation boundary  = NOT CLOSED
implementation authorization = PENDING
review result                = CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION
~~~

闭合 LO-IR76～LO-IR78 后，进行一次最终独立复审；若三项均按本文件的最小调用图闭合，
不再新增评审类别，再申请用户明确授权后进入 production/test 编码。当前未修改 production、
State、Store、API、tests 或 implementation target，也未运行源码门禁。
