# Graph / State 反馈改动再次验收评审（2026-09-04）

状态：**静态复审完成；2026-09-05 已增量验收 GraphTransition 原子写集；本轮不重新运行测试、`make check` 或全仓 pre-commit**

## 1. 范围与判定原则

本轮以当前工作树为基线，审查公开 `Graph` 可达的完整调用链：

`Graph` facade → definition/compiler → compiled routing/materialization → `GraphRunState` cause/ledger → reducer/commit → resume/recovery。

只记录能由常规公开调用触发、会改变执行结果或公开状态/恢复边界的真实问题。不把当前明确未承诺的 durable concrete-value
recovery 能力，误判成进程内 runtime 缺陷；但必须审查真实 distributed concurrency、CAS、fence、lease takeover、partial
commit 和 acknowledgement 边界。手工伪造 hostile 内部对象、port/hooks/logging/observability/failover 等并行改动，以及仅由
复杂度指标命中的热点不纳入本轮 finding。没有真实问题的路径不强行拆抽象或扩大修复范围。

当前审查基线：

- `HEAD`：`4dff057 feat(execution): expose typed graph feedback binding`；
- 工作树包含后续 Graph feedback compiler/runtime 与测试修改；
- `GraphRunState` 仍是唯一 runtime state owner，settled activation ledger、frontier cause 和 Join progress 均在同一 reducer 边界内。

## 2. 审查台账

| 调用链/不变量 | 状态 | 证据与后续判断 |
| --- | --- | --- |
| 唯一 Graph facade 与唯一 execution engine | 已核对通过 | `Graph` → compiler/planner → family driver → 唯一 reducer/commit；没有第二 runner 或 parallel public entry |
| feedback gate/source/publication 选择与实际 cause 一致 | 已核对通过 | 多 gate、Join、nested scope、相邻 publication 均沿同一 typed rule/cause/materialization 选择；不静默取第一项 |
| State cause、settled ledger、Join progress 的 reducer/恢复一致性 | 已核对通过（in-memory）；durable 证据边界见第 4 节 | live 与 state-led proof 共用 State-owned cause、ledger、Join occurrence 和 compiled topology；跨进程 concrete value 仍未承诺 |
| 普通 NodeOutput 数据依赖与 feedback 跨 activation 依赖不混淆 | 已核对通过 | 普通 data cycle 仍由 compiler 拒绝，feedback 只由 `CompiledActivationRule` 跨 activation 解析 |
| 文档/public contract 与已开放能力一致 | 发现契约漂移（非 runtime finding） | README 与两份实施计划仍保留旧的 direct-self-feedback/`GraphInputRef` 限定，见第 5 节 |

### 复审记录（第一轮：声明、编译与 live routing）

- `Graph.feedback()` 只构造一个 `FeedbackInputBinding`；普通 `NodeOutputRef` 仍走原有同一 activation 的
  data-dependency 路径，feedback source 则由 compiler 生成的 `CompiledActivationRule` 接管，没有新增 runner、缓存或
  第二份 State。
- compiler 对每个 feedback target 将所有实际 incoming gate 做一次 initial/repeat partition，并要求 source、route、Join
  occurrence 与 publication selection 可唯一对应；无法证明的共存 gate、未覆盖 gate 或多坐标会在 compile 阶段拒绝。
- live routing 在 collapse 前保留 `(target, cause)` candidates；`feedback_source_for_cause()` 和普通 gate admission
  共同读取当前 State cause/settled ledger，`_required_target()`、`resolve_routing_facts()` 与 reducer 使用同一批
  `GraphFrontierActivation`，未见静默选择第一条或回退 latest/seed 的路径。
- 直接自反馈仍额外要求 `RELATIVE(1)`、同一 target 的紧邻 predecessor、唯一 feedback route；多节点、node-output initial、
  mutually-exclusive route 与 Join 形状沿同一 rule/cause/publication 机制处理，未见为它们另建状态模型。

### 续审收尾（当前工作树）

- 再次逐段核对 `FeedbackInputBinding` / `Graph.feedback()`、compiler gate partition、`feedback_source_for_cause()`、
  `pending_node_input_available()` / `materialize_node_input()` 与 recovery publication history；initial/repeat 的唯一判定仍由
  compiled gate 和 State-owned cause 共同完成，没有 runtime phase flag、latest publication 扫描或 seed fallback。
- 多个 feedback binding 仍必须共享同一个 activation gate partition，各 binding 只拥有自己的 typed publication source；
  target candidate 在 collapse 前保留 cause，零个或多个 cause 均 fail closed，没有“取第一条”或静默合并。
- 复核未发现新的公开可达 runtime finding。本次不把手工篡改 compiled plan/ledger、无协议远程参与者或未承诺的 durable
  concrete-value recovery 当成当前实现缺陷，也不因 compiler 热点继续拆分 owner。

## 3. Findings

本轮没有确认新的、能由常规公开调用触发并改变当前进程内 Graph/State 执行结果的 runtime finding。下文第 4 节记录的 durable
原子证据项是未来能力的条件阻断，不倒灌为当前 in-memory 缺陷；第 5 节记录文档/public contract 漂移，属于提交收口项而非
执行漏洞。

## 4. 复审记录（第二轮：分布式边界与恢复契约）

### 4.1 revision、execution token 与 CAS

- 除 `StartGraphRun` 外，所有 Graph command 都携带 `expected_revision`；`reduce_graph_run()` 在任何领域分支前严格比较
  当前 revision。每次成功 reducer transition 才递增 revision，旧 command 不会被静默重放。
- active execution 另外携带精确的 `GraphExecutionToken(generation, attempt_id)`。`SettleGraphNode`、`FenceGraphExecution`
  和 worker cleanup 都要求 token 与 State 中的 lease 完全相等；新一代 claim 生成新 generation/attempt，旧 worker 的 settlement
  或 fence 至少会被 revision 或 token 拒绝。
- recovery 的 `plan_fences()` 先为仍有 lease 的 scope 生成 exact fence，再进入 claim/resume；接管的前提是调用方已经确认旧
  lease/worker 停止。Kernel 不负责、也不假设能够物理停止远程 worker，因此没有把“完全无视协议的远程参与者”当作本轮网络攻击面。

**判定：通过。** 在正常的多进程竞争（同一 run 被两个 worker/恢复者同时操作）下，CAS、generation 和 token 共同形成唯一提交
owner；没有观察到 stale worker 能通过公开 Graph 路径改变 State 或继续暴露旧 execution。

### 4.2 exact acknowledgement 与丢 ack

当前提交顺序固定为：

```text
previous state + typed command
    -> reduce_graph_run(candidate)
    -> Graph.Commit(transition)
    -> callback 返回 exact candidate
    -> 本地 State/frame 才前移
```

`commit_transition()` 对 callback 的异常、非 `GraphRunState` 返回值或非 exact candidate 都 fail closed；`_GraphRun._transition()`
不会先安装 candidate。若外部 persistence 已写入 candidate 但 acknowledgement 在返回途中丢失，内存仍停留在 previous state，
后续用旧 revision 盲目 retry 会得到 stale-CAS，而不是重复执行或伪造成功。

这是一项明确的外部 persistence owner reconcile 边界：应以 `(run_id, scope, revision)` 及 candidate coordinate 读取/核对
已提交 head，再决定继续或报告冲突。Kernel 不猜测“可能已经写入”，也没有第二条自动重试/旁路恢复路径。

**判定：Kernel 通过；ack-loss 不作为当前 runtime finding。** 当前 README/计划只把 callback 定义为注入的确认边界，不承诺
Kernel 自带 durable store 或 ack-loss 自动重建。若未来把 callback 宣称为 crash-safe durable commit，必须同时交付外部
reconcile 契约，不能只放宽 retry。

### 4.3 多 scope partial commit 与失败归属

`admit_continued_root()` 按 scope 顺序应用 fence/resume，并维护 `confirmed_prefix`、`failed_scope` 与 continuation evidence：

- 已 exact-confirm 的 scope 才进入 continuation 前缀；
- fence/resume/child construction 在某个 scope 失败时记录该 scope，不把后续未确认 scope 标成成功；
- 没有确认前缀时直接清理并抛出原始错误；有确认前缀时抛出带 root state、confirmed child/frame evidence 和 failed scope 的
  `PartialCommitError`；
- child evidence 只在 owner handoff 完成后发布，未 handoff 的 child 不会被伪装成已确认 continuation。

因此多 scope 的 partial 结果不会错误扩大确认范围。若 persistence 在某一 scope 写入后丢 ack，外部 owner 仍需按同一 scope/revision
坐标 reconcile；当前 API 没有承诺拿到 `PartialCommitError` 后可以无条件直接 retry。

**判定：通过（在 exact callback/外部 reconcile 契约下）。** 未发现可由公开调用触发的错误 scope 标记、虚假 confirmed prefix 或
第二次执行路径。

### 4.4 Start/input 与 settlement/output 原子写集（2026-09-05 增量复审）

上一轮记录的 State 与 concrete-value evidence 两段接缝，现已在 **commit contract 层**收口：

- `execution/commit.py` 是 `GraphTransition`、`GraphCommitWriteSet`、transition 构造、exact confirmation 与确认后 frame
  projection 的唯一 owner；`family_driver.py` 不再保存一套平行的 transition/commit 定义；
- root 与 nested child 的 `StartGraphRun` 都先由 `prepare_transition()` 生成 candidate，并把唯一
  `GraphInputEvidence(coordinate, GraphInputFrame)` 放入同一个 write set，再调用 `Graph.Commit`；
- `SettleGraphNode` 同样先生成 candidate。成功 settlement 唯一拥有对应的 `GraphPublicationEvidence`，
  `writes.publications` 只是该 settlement 的派生投影，不复制第二份 output；failure/interrupt 明确禁止携带 publication；
- `GraphCommitKey(run_id, candidate revision)` 与 candidate 绑定；command-specific 校验禁止 Start 混入 settlement、非 Start
  混入 graph input、非 Settle 混入 settlement，或成功 settlement 缺 publication；
- `confirm_transition()` 只有在 callback 返回 exact `GraphRunState` candidate 时才返回。callback 抛错、返回错误类型或返回
  非 exact candidate 时，`_GraphRun._transition()` 的 State/frame 安装语句均不可达；root/child Start 也不会构造一个带未确认
  input frame 的 live owner；
- exact confirmation 后，`apply_commit_writes()` 才把 write-set evidence 投影成 `AdmittedGraphInput` /
  `ConfirmedPublication`。该投影复用既有 `ScopedFrameIndex`，没有增加第二个 frame owner、旁路 publication 或兼容执行路径。

这使未来 persistence adapter 可以把 candidate State、graph input、settlement 与 publication 作为一个写集做原子 CAS；但本次
**没有实现具体数据库 adapter、durable loader/read-reconcile、canonical evidence fingerprint 或 crash-safe concrete-value
recovery**，也不宣称已经提供这些能力。同一 `CommitKey` 下 candidate State 相同但 concrete output 不同的竞争，未来 adapter
必须比较完整 write set 并 fail closed，不能只比较 State 后把另一份 evidence 当作幂等成功。

**判定：当前提交契约与 in-memory 安装顺序通过；上一轮的 contract-level 条件项已关闭。具体持久化实现和 crash-safe durable
concrete-value recovery 仍是后续能力，不属于本次阻塞项。** 手工伪造 durable ledger 仍不倒灌为当前 runtime finding；未来一旦
交付 durable recovery，真实性、版本联结和 reconcile 必须由同一 persistence/evidence owner 收口。

### 4.5 recovery cycle signature

`_recovery_cycle_signature()` 只在 scope 已 quiescent（无 active execution、live worker 或 child）时生成有界 proof key，当前
纳入：

- normalized frontier/cause；
- Join occurrence/progress；
- 被 cause 或 Join 引用、以及 publication history window 覆盖的 settled activations；
- absolute/relative publication coordinates；
- 当前 resume inputs；
- 当前 child boundary coordinates；
- `invocation_new_children`。

resource/execution/child active 状态由签名前置条件及 transfer state 处理；payload 本身不决定 control successor，故不放入
signature；revision 等只影响 CAS 的 metadata，不改变 recovery 后继。按这些维度对 live 与 state-led worklist 的合并条件核对后，
没有发现会把不同可达控制后继错误合并的遗漏，也没有必要为了指标把 proof 拆成第二套状态机。

**判定：通过。** 如未来新增会影响 control reachability 的 State-owned 事实，必须在同一 signature/transfer owner 中补入，不能另建
隐式 recovery 状态。

### 4.6 lease takeover 与 cleanup 顺序

普通 worker 异常会等待/取消同一 family 的 Python worker，并在 owner 仍持有 lease 时递归 fence 已停止的 scope；node-origin 或
commit-origin cancellation 则保留 lease 给调用方/恢复路径处理。recovery takeover 先 exact fence，再恢复 admission，旧 owner 的
evidence 只有在停止/terminal handoff 后发布。

**判定：通过。** 该边界覆盖正常分布式竞争和取消顺序；不扩展到远程进程失联且不执行协议的对抗模型。

## 5. 文档与公开契约漂移（非运行时 finding）

当前代码和已有测试已经覆盖/开放的 feedback 形状明显超过 README 与部分实施计划的旧描述：

- multi-node feedback cycle；
- node-output initial source；
- mutually-exclusive repeat routes；
- explicit Join feedback；
- nested feedback；
- multiple feedback binding。

但以下文字仍把边界写成“仅 callable root target + `GraphInputRef` initial + 单 self/terminal route”，或明示“当前 public facade
对 feedback 的拒绝保持有效”：

- `README.md`、`README.zh-CN.md` 的 feedback 段落；
- `docs/graph-in-memory-p2-p3-implementation-plan.zh-CN.md` 的 P2-M 允许修改边界表；
- `docs/graph-delayed-loop-implementation-plan.zh-CN.md` 开头接缝说明和 `FeedbackInputBinding` 注释中的“P1 先只准
  `GraphInputRef`”。

这是用户无法据文档判断真实 public contract 的同步问题，不是当前 routing、State 或 recovery 的执行漏洞。本轮不修改这些并行
计划/README，提交前应由文档 owner 统一“已开放形状”与“仍未承诺 durable value recovery”的两条边界；若本次提交范围要求文档
与代码同步，则该项需先收口，不能用测试通过替代契约准确性。

## 6. 最终结论

**当前 Graph/State 进程内代码复审通过。** 唯一 facade、compiler/routing/materialization、`GraphRunState` reducer、exact
commit、live/recovery 共用的 cause/ledger/Join 规则均已核对；未发现需要修改生产代码的真实可达缺陷，也没有为满足复杂度指标
引入薄转发、兼容 alias、第二状态机或第二执行路径。

**分布式并发边界和原子 write-set contract 通过，但 durable concrete-value recovery 尚未实现，也不属于本次阻塞项。** revision
CAS、execution token、fence/lease 接管、partial scope 归属和 fail-closed acknowledgement 语义没有发现错误确认；
Start/input 与 settlement/output 已进入同一 `GraphTransition` write set，并且只在 exact confirmation 后投影到本地 State/frame。
未来 persistence owner 仍需实现原子 CAS、同 key 全 write-set 冲突判定、durable loader 与 reconcile。手工伪造 durable
ledger/ghost activation 不作为当前 in-memory runtime finding；一旦公开 durable recovery，必须在同一 evidence/admission owner
中收口真实性。

**提交建议：** 代码层面没有新的 Graph/State 阻断，可以进入提交候选；不过 README 和实施计划的 feedback 能力描述需要文档
owner 同步。若本次提交门禁明确要求 public contract 与实现完全同步，应先修正文档再提交；本轮按用户要求未修改并行文档、未跑测试
或其他门禁。
