# Graph 显式反馈回环实施计划审计（含补充复审）

状态：**架构方向可实现；无待 owner 裁决；直接自反馈仍有 S0 工程缺口，实施方案修订前不批准进入 P1**

审计日期：2026-09-01

审计对象：[Graph 显式反馈回环实施计划（修订版）](./graph-delayed-loop-implementation-plan.zh-CN.md)

产品边界补充（2026-09-02，owner 最终裁决）：**Graph 不实现失败节点重试，Failover 是失败重试的唯一 owner。**
`resume_failed`、失败后输入 override、`skip_failed` 或任何把 `FailedGraphNode` 改回 Pending 的路径都不属于 Graph
recovery。Failed 必须落为 durable terminal status/result，不能继续复用 `AwaitingResume`；state-led recovery 只重建
已确认 State 和 value evidence。Failover 的下一次 Port attempt 必须由 typed outcome 经显式路由产生一个新
activation，而不是让 Graph 重跑失败 activation。

当前代码仍公开 `Graph.resume_failed()`、`Graph.resume_failed_with()`、`Graph.skip_failed()`，在 admission/reducer
中实现 Failed → Pending/Skipped，并把 Failed 归为 `AwaitingResume`；这是与最终边界直接冲突的存量实现，必须移除
并增加 terminal Failed disposition，不能保留兼容别名。

第 1—8 节保留 2026-09-01 二次审计当时的发现和推导；第 9 节记录第一次最终复审；现时判定以第 10 节直接
自反馈补充审计为准。

## 1. 结论

修订版已经关闭上一轮最关键的五个语义错误：

- 不再把 `superstep` 当作完整业务迭代，也不再使用普通边 `lag=0`、反馈边 `lag=1` 的错误模型；
- 删除 per-binding loop payload slot，复用 canonical publication/result；
- seed/repeat 由权威 activation cause 决定，缺 repeat evidence 明确 fail closed；
- feedback result 固定复用 successful `SettleGraphNode + GraphTransition.result` 的提交路径；
- exactly-once 已收敛为 live session 至多调度一次；未提交 activation 的崩溃重放与已确认失败后的重试严格分开。

因此，**`feedback(initial, repeat)` 这个方向在当前 Kernel 架构上可以实现，不需要第二个 runner、reducer、State
或 payload store**。

但“方向可实现”不等于“当前计划已可直接编码”。修订版把 state-only recovery 定为公开 feedback 的硬要求，
而当前代码只有 write-only commit callback，没有 durable evidence reader；`StartGraphRun` 也没有原子提交 graph
input。与此同时，State 在 `AdvanceGraphFrontier` 后没有保留 target 的 activation cause，无法在纯 State 重启时可靠
区分 seed 与 repeat。计划还允许 P2 后开放 API，却把持久化必需的 codec、payload 上限和 retention 放到 P4。

二次审计当时仍有五个 S0 阻断：

1. state-only recovery 的 typed 读取/装载契约没有定义，现有 persistence 只提交 State；
2. seed/repeat 所依赖的 activation cause 不是 durable State fact；
3. `commit_id`、publication identity、acknowledgement-lost 和并发顺序语义没有冻结；
4. codec、安全上限和有界 retention 被错误地排在公开 API 闸门之后；
5. 第一阶段 direct loop 没有定义正常成功退出路径。

这些问题都能在现有架构内关闭，但当时必须先补完 P0。完成第 8 节退出条件后，才可批准进入 P1；最终处理状态见
第 9 节。

## 2. 已关闭的上一轮问题

| 上一轮问题 | 修订结果 | 复审判定 |
| --- | --- | --- |
| `superstep` 被误当作业务循环轮次 | 明确它是 frontier 推进坐标，并复用 `ABSOLUTE/RELATIVE` selection | 已关闭 |
| per-binding loop slot 复制 payload | 改为一个 canonical publication/result，`ScopedFrameIndex` 只做投影 | 已关闭 |
| 缺 evidence 时可能重新 seed | 明确只有权威 seed cause 才能读 initial，其他情况 claim 前失败 | 语义已关闭；durable cause 见 S0-2 |
| loop update command/原子边界未定 | 固定复用 successful settlement 和现有 `GraphTransition.result` | 方向已关闭；持久化契约见 S0-1/S0-3 |
| activation exactly-once 承诺过强 | 改为未提交 Pending activation 可做崩溃重放；已确认 Failed 永不重调度 | 已关闭 |

修订版也正确保留了以下原则：

- `mote_kernel.execution.Graph` 是唯一公开构图和运行门面；
- `GraphRunState` 是唯一运行状态，`reduce_graph_run` 是唯一 reducer；
- State 不读取 `CompiledGraph`；
- durable commit 确认后才替换内存 State、安装 confirmed frame；
- 普通 `NodeOutputRef` 的 data-cycle rejection 不变；
- 未证明的 conditional/join/nested/multiple feedback 在 compiler fail closed。

## 3. S0 阻断项

### S0-1：state-only recovery 没有读取通道，也没有覆盖完整 evidence 生命周期

[修订计划第 4.3、7 节](./graph-delayed-loop-implementation-plan.zh-CN.md#L167)已经把 state-only recovery 定为
公开 API 的硬要求，但当前公共调用链无法满足该承诺。

当前 `Graph.run(state=...)` 在没有 continuation 时会创建空 `ScopedFrameIndex`，见
[`Graph.run()`](../src/mote_kernel/execution/facade.py#L627)。它只接收一个 write-oriented `commit` callback，
没有 typed evidence reader 或 recovery snapshot loader。

现有提交边界也没有覆盖所有恢复证据：

- [`GraphTransition`](../src/mote_kernel/execution/family_driver.py#L102)只有 `candidate_state` 和可选 node
  `result`；
- [`fresh_root()`](../src/mote_kernel/execution/family_driver.py#L1135)先以 `result=None` 提交
  `StartGraphRun`，随后才把 graph input 安装到内存 `ScopedFrameIndex`；
- nested graph input 也在 child `StartGraphRun` 确认后才安装，见
  [`_make_child_constructor()`](../src/mote_kernel/execution/family_driver.py#L809)；
- complete continuation 明确要求保留每个 scope 的 graph input 和当前 success publication，见
  [`_validate_complete_context()`](../src/mote_kernel/execution/invocation.py#L554)。

因此，即使 persistence 开始保存 `GraphTransition.result`，只拿 State 重启仍然无法恢复最初的 graph input，
也没有通道装载 result records。计划所写的“State + 同 commit_id 的节点结果/输入/子图证据”目前只是一张概念图，
还不是可实现的 Port 契约。

具体 backend 也印证了这一点：

- Cloudflare TypeScript `Transition` 只有 `candidateState`、`previousState` 和 `scope`，见
  [`cloudflare/src/index.ts`](../../mote-infra/persistence/cloudflare/src/index.ts#L7)；
- 它的事务只写 `mote_graph_state_v1` 的 State payload，见
  [`Commit()`](../../mote-infra/persistence/cloudflare/src/index.ts#L50)；
- Cloudflare Python adapter 同样只编码并提交 State，见
  [`_commit.py`](../../mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/_commit.py#L79)。

P0 必须先冻结一个**通用而非 loop 专用**的契约：

1. Kernel 定义一个窄 typed persistence/recovery Port，写入仍经过唯一 `GraphTransition`，读取返回一个经过版本联结的
   recovery snapshot；
2. fresh `StartGraphRun` 必须把 admitted graph input evidence 与 State 原子提交，不能先提交 State 再只装内存；
3. successful settlement 必须把 node result/publication evidence 与 candidate State 原子提交；
4. resume input、skip substitution 和 child evidence 要明确是单独持久化、从已有 canonical record 重建，还是第一版
   compile 拒绝；不能用一句“必要的子图证据”留给 runtime；
5. loader 必须在任何 claim 前完成 scope、definition、descriptor、commit version、codec 和 nominal type admission；
6. `Graph` 仍是唯一 facade。不得因为需要 loader 而公开第二个 recovery runner 或 Store facade。

如果不愿在第一版增加通用读取 Port，就必须撤销 state-only 硬要求并回到 complete-continuation-only；不能同时保留
“state-only 必须恢复”和当前 write-only `commit` API。

### S0-2：activation cause 尚未成为可恢复的权威控制事实

[修订计划第 3.2 节](./graph-delayed-loop-implementation-plan.zh-CN.md#L86)正确要求 runtime 只能依据已提交的
routing/activation fact 选择 seed 或 repeat，但当前 State 在 frontier 推进后没有保存这个事实。

现有形状是：

- [`GraphFrontierNode`](../src/mote_kernel/state/graph_state/frontier_model.py#L90)只有 `node_id` 和
  `settlement`，没有 activation cause；
- [`AdvanceGraphFrontier`](../src/mote_kernel/state/graph_state/command.py#L55)只携带下一批 `node_ids` 和
  `join_progress`；
- [`advance_graph_frontier()`](../src/mote_kernel/state/graph_state/execution_transitions.py#L119)用新的 Pending
  frontier 替换已 settlement 的旧 frontier，旧 routing contribution 随之不再存在；
- routing resolver 虽然从当前 settled frontier 计算 target，但最后投影的仍只是 target id，见
  [`project_routing_facts()`](../src/mote_kernel/execution/engine/routing.py#L257)。

对固定 `A[0] -> B[1] -> C[2] -> A[3]`，可以临时用 superstep 特判首轮；但这会让
`initial=NodeOutputRef` 的非零 seed、未来多分枝和不同路径长度重新依赖猜测，也违背修订计划“从第一天使用同一
通用模型”的要求。

推荐的最小闭合方式是扩展**唯一的 State 控制事实**，而不是增加 payload slot：

```text
GraphFrontierActivation
  target node
  + StartActivationCause
    | RoutedActivationCause(tuple[StateOwnedSourceActivation, ...])
```

- `StartGraphRun` 安装 canonical start cause；
- `AdvanceGraphFrontier` 携带 target activation，而不是只有 target id；
- State reducer 验证 source 坐标、canonical 顺序、重复和时间关系；
- Execution/compiler 验证这些 source/cause 与 `CompiledGraph` topology 相符；
- materialization 只读取该已提交 cause 来选择 compiled seed/repeat rule；
- State 类型归 `state/graph_state` 所有，不反向导入 Execution 的 `CompiledGraph` 或内部类型。

若 P0 不选择持久化 cause，第一阶段就必须严格缩窄为“feedback target 是 `START` 的 `superstep=0` entry，initial
只能是 GraphInputRef，所有后续 target activation 只有唯一 direct repeat cause”。这与当前承诺的
`initial=GraphInputRef | NodeOutputRef` 和后续多分枝方向不一致，因此不推荐。

### S0-3：commit identity、publication identity 与丢确认语义仍然含混

修订计划同时使用了 `state_version / commit_id`、`same commit identity` 和 `acknowledged revision`，但没有选定
唯一表示。P0 退出条件写“已固定”，正文实际仍是二选一占位符。

当前已有两个不同概念：

- canonical publication coordinate 是 `scope_run + StableActivation + FrameDescriptorIdentity`，见
  [`PublicationAvailabilityCoordinate`](../src/mote_kernel/execution/run_context.py#L29)；
- `ConfirmedPublication.acknowledged_revision` 是该 publication 被哪次 State commit 确认的联结信息，见
  [`ConfirmedPublication`](../src/mote_kernel/execution/run_context.py#L69)。

二者不能合并成一个“identity”。同一 superstep 的 sibling completion 顺序互换时，publication coordinates 和 payload
应相同，但每个 publication 对应的 acknowledged revision 可能互换。修订计划验收矩阵中的“publication/state identity
结果一致”若要求 commit history byte-identical，按当前逐 settlement commit 模型无法实现。

最终方案固定为 State-owned activation identity、publication identity、commit identity 和确定性 evidence fingerprint。
State 不导入 Execution-owned `StableActivation`，Execution scope path 也不进入 canonical key：

```text
GraphActivationIdentity = GraphRunId + superstep + GraphNodeId
PublicationKey = GraphActivationIdentity + FrameDescriptorIdentity
CommitKey      = GraphRunId + candidate_state.revision
EvidenceFingerprint = canonical(candidate State + ordered evidence operations)
```

并明确：

1. `PublicationKey` 唯一确定 value fact；多个消费者只引用它；
2. `CommitKey` 只证明该 value fact 与哪一个 exact State candidate 原子确认；
3. 同一 `CommitKey`、同一 candidate 和同一 evidence 重放必须幂等收敛；同 key 不同内容 fail closed；
4. acknowledgement lost 后，commit retry 或 read/reconcile 如何识别“已经精确提交”必须成为 Port 行为；
5. sibling 顺序互换只要求相同 publication-key/payload 集合和等价最终控制 State，不要求相同 revision 分配历史；
6. stale previous revision、duplicate publication、same-key-different-payload 的错误优先级必须固定。

第 3 条的“相同”必须用带 domain/version/长度边界的 canonical bytes 和稳定 fingerprint 判断，不能依赖 Python
对象相等、`repr`、pickle 或容器迭代顺序。

当前 Cloudflare adapters 对已经落盘的 candidate 再次提交会按 stale CAS 冲突，且没有 read/reconcile Port，因此
“acknowledgement lost 自动收敛”不能只写测试期望，必须先修改 Kernel Port contract 和跨语言 conformance。

### S0-4：codec、安全上限和 retention 不能推迟到 P4

[修订计划第 7、8 节](./graph-delayed-loop-implementation-plan.zh-CN.md#L286)允许 P2 垂直切片通过后考虑公开
`Graph.feedback(...)`，但把以下必需项放在 P4：

- codec id/version 和 decode admission；
- payload 字节上限；
- corruption/version mismatch；
- 敏感值脱敏；
- publication/result retention 和长循环上界。

这个顺序不可实施。P2 已要求 durable result evidence，没有 codec 就无法跨进程/跨语言稳定持久化任意
`GraphValueT`；没有 retention，direct loop 每轮新增 publication，当前 immutable
[`ScopedFrameIndex`](../src/mote_kernel/execution/run_context.py#L169)和 durable records 都会无界增长；没有大小上限和
secret-safe error，公开 API 也不满足安全边界。

必须把最小安全切片移入 P2，并作为公开 API 闸门：

- required codec 在 Graph assembly 时提供，缺失时 fail assembly；
- codec id canonical、version 为正整数，encoder 返回 exact bytes，decoder 输出重新通过 exact descriptor admission；
- encode 前后和 load 前均执行明确的 bytes 上限，异常不得包含 payload/repr；
- feedback source 必须是 persistence-admissible value，敏感数据的加密/拒绝策略必须明确；
- compiler/runtime 给出第一阶段 live publication 集合；过期 evidence 的删除与使其失活的 State transition 使用同一
  commit boundary；
- corruption、missing evidence 和 stale snapshot 在 claim 前按固定优先级失败。

P4 可以保留压力测试、复杂 topology liveness 和优化，但不能承载 P2 durable recovery 的基本正确性和安全性。

### S0-5：第一阶段 direct loop 没有正常成功退出语义

[修订计划第 6.2、13 节](./graph-delayed-loop-implementation-plan.zh-CN.md#L250)把第一阶段画成
`A -> B -> C -> A` 的 direct control loop，同时拒绝 conditional feedback。按现有 routing 语义，只要 direct target
存在就会继续 `AdvanceGraphFrontier`，见
[`project_routing_facts()`](../src/mote_kernel/execution/engine/routing.py#L257)。这样的图只能被取消、失败，或触发
`max_supersteps` 的执行上限；执行上限是安全错误，不是 successful graph completion。

第一阶段至少要明确一种可证明的正常退出形状。推荐保持 feedback cause 为 direct，同时允许 feedback target 选择退出：

```text
START -> A(seed/repeat)
         | continue -> B -> C --direct--> A
         ` done -----------------------> END
```

这里 `C -> A` 仍是唯一 direct feedback cause；`A -> END` 是 target 的退出 route，不应与“conditional feedback
cause”混为一谈。compiler 需要证明：

- seed/repeat cause 仍唯一；
- done route 不会再激活 repeat target；
- successful completion 的 graph output publication 有唯一坐标；
- 在退出 settlement 后、output frame 安装前崩溃仍可从 durable evidence 重建。

如果第一阶段连这个形状也不支持，则 P2 只能是 internal proof，公开 API 必须继续关闭到 P3 conditional exit 完成。
`max_supersteps` 不能作为业务正常退出机制。

## 4. S1 高风险问题

### S1-1：“节点重新加载证据”的责任表述需要纠正

修订计划多次写“每个节点重新加载自己的输入/输出证据”。节点不应知道 persistence、codec、State 或 recovery。
正确 owner 是 Invocation/Execution：它装载并准入 evidence、重建 `ScopedFrameIndex`、materialize typed
`NodeInputFrame`，节点只接收不可变输入并返回 typed result。

这应在正文中改成 owner 级表述，避免实现时把 loader 或 Port 调用塞进 node callable。

### S1-2：错误优先级仍未真正写出

P0 和退出条件都要求“错误优先级已有答案”，但当前只有场景到错误类型的零散映射，没有冲突场景顺序。至少应固定：

```text
malformed/untrusted record
  -> scope/run/definition/descriptor/commit mismatch
  -> duplicate/same-key conflict
  -> codec/version/size/corruption
  -> nominal value admission
  -> required evidence unavailable
  -> topology/materialization availability
  -> claim
```

还要明确失败是否只抛异常，还是提交 `AbortGraphRun`。当前普通 routing 缺值会投影
`AbortGraphRun`，而 state-only preflight 可能直接抛 `GraphValueUnavailableError`；feedback 不应产生第三套行为。

### S1-3：不要为未来多分枝预造抽象层

“第一天使用同一模型”是正确目标，但不等于第一阶段就实现未来 conditional/join/nested 的 runtime 泛化。
建议只定义一个 closed typed `FeedbackInputBinding` 和一个 immutable compiled activation-rule collection，第一阶段
validator 限制为一条 seed rule、一条 repeat rule。后续扩展同一个 union/tuple 即可。

不要新增 generic `FeedbackManager`、策略接口、visitor、反射或字符串 kind；也不要为了避免未来字段演进而把 State
设计成裸字典或通用 event bag。

## 5. 建议冻结的最小端到端契约

以下形状能满足唯一真相、复用基础设施和简单逻辑；名字可以调整，但职责不能再次悬空。

### 5.1 唯一提交与恢复 Port

```text
Graph.run(..., persistence=<narrow typed port>)
  -> load RecoverySnapshot at exact authoritative version
  -> Execution admission
  -> ScopedFrameIndex projection
  -> existing preflight/claim/session

GraphTransition(
  previous_state,
  command,
  candidate_state,
  typed evidence writes/releases,
)
  -> persistence atomic CAS by CommitKey
  -> exact candidate confirmation
```

这不是第二个 Graph facade。它是 concrete persistence capability 进入唯一 `Graph.run()` 的窄 Port。commit、load、
result evidence 和 release 必须属于同一个版本协议，不能分别拼装成彼此不知情的 callback。

### 5.2 通用 evidence，而非 loop evidence

第一阶段至少覆盖：

| transition | 原子 evidence |
| --- | --- |
| `StartGraphRun` | admitted graph input |
| successful `SettleGraphNode` | canonical node output/result publication |
| failed settlement | 只保留终止失败控制事实；不写 retry/override/skip evidence，不重新调度 |
| interrupted settlement | 保留 State 已拥有的 interrupt 控制事实；与失败重试分开 |
| failed-node resume/skip substitution | Graph 永久拒绝；Failover 通过显式新 activation 表达再次尝试 |
| child completion | 从 child canonical state/publications 重建，或定义唯一 child evidence；不得两份 payload |

evidence record 的 value key 使用 `PublicationKey`；graph input、resume input、child boundary使用各自现有
availability coordinate 类型的 durable 对应物。它们共享 commit/version、codec、admission 和 retention
基础设施，不需要 `LoopEvidence` 类型。

### 5.3 原子时序

fresh run：

```text
admit graph input
  -> StartGraphRun candidate
  -> atomic commit(State + graph-input evidence)
  -> install confirmed graph input
  -> preflight/claim
```

producer success：

```text
admit NodeOutputFrame
  -> SettleGraphNode + publication evidence
  -> atomic commit(State + evidence)
  -> replace authoritative State
  -> install/rebuild ConfirmedPublication
  -> resolve routing
```

recovery：

```text
load exact State head N + all evidence visible at N
  -> validate coordinates/codec/nominal types
  -> rebuild ScopedFrameIndex
  -> validate activation cause and materialization
  -> claim
```

任何一步不一致都在 claim 前失败。不得因为 loader 缺 record 而重新 seed，也不得在 commit 确认前安装 frame。

## 6. 修订后的实施边界

### P0：先关闭契约

只写设计、类型草案、conformance vectors 和故障矩阵：

- exact persistence/recovery Port；
- deterministic `PublicationKey` / `CommitKey`；
- State-owned activation cause；
- graph input/result/interrupt/child evidence 生命周期；failed-node resume/skip 不进入 Graph recovery；
- idempotent commit/reconcile；
- codec、大小、secret、retention 和错误优先级；
- 第一阶段成功退出 topology。

### P1：内部 declaration + compiler proof

P0 全部退出后，可以实现内部 `FeedbackInputBinding`、compiled activation rules 和 compile-time rejection。普通
binding、普通 data cycle、routing、join 和 graph output 行为必须保持回归不变。仍不公开 facade API。

### P2：durable 垂直切片

必须一次打通：

```text
fresh input atomic evidence
  -> seed/repeat materialization
  -> settlement/result atomic commit
  -> activation cause frontier
  -> state-led exact-head/live-evidence load/rebuild
  -> normal successful exit
  -> bounded retention + minimum security
```

只有这条垂直切片、跨语言 persistence conformance 和故障注入全部通过，才能公开 feedback。

### P3 以后：只扩 proof，不扩 owner

多个消费者、conditional feedback cause、多 feedback、join、nested 按 compiler proof 逐项开放。继续复用同一
State、Port、commit、evidence、materialization 和 recovery 路径。

## 7. 必须补齐的验收矩阵

修订版现有矩阵应保留，并增加：

| 类别 | 场景 | 预期 |
| --- | --- | --- |
| fresh recovery | `StartGraphRun` 已提交、graph input 内存安装前崩溃 | state-led 从该 State head 可见 evidence 重建 |
| loader | State revision 与 evidence commit version 不同 | claim 前 snapshot mismatch |
| loader | persistence Port 缺失 | durable feedback assembly/run fail closed |
| seed cause | initial 为 `NodeOutputRef`，首个 target 不在 superstep 0 | 只按 durable seed cause 选择 initial |
| cause recovery | target frontier 已推进后重启 | 从 State cause 选择 repeat，不看 frame presence |
| cause forgery | source coordinate/route 不属于已提交 predecessor | State/Execution 边界拒绝 |
| commit replay | 同 CommitKey + 同 candidate/evidence 重试 | 幂等返回 exact candidate |
| commit conflict | 同 CommitKey + 不同 evidence | fail closed，不覆盖 |
| sibling order | completion 顺序互换 | publication-key/payload 集合一致；revision history 可不同 |
| exit | feedback target 选择 done -> END | successful result 和 graph output 坐标唯一 |
| graph input | input codec mismatch/corruption/oversize | claim 前失败，不调用节点 |
| result codec | encode/decode 非 exact bytes/value | 原子提交前或 claim 前失败 |
| retention | 多轮 direct loop | live memory 和 durable evidence 满足明确上界 |
| retention crash | evidence release 同 commit 前后崩溃 | 不丢 live value，不复活 dead value |
| redaction | codec/commit/load 异常 | error/log 不包含 value、payload 或 secret repr |

## 8. P0 二次评审退出条件

只有以下条件全部写入实施计划并互相一致，才可把状态改为“批准进入 P1”：

1. 定义 state-led recovery 从哪里取得 exact State head 与 live evidence；`Graph.run()` 不再只有 write-only commit 假设；
2. 定义 `StartGraphRun + graph input evidence` 的原子提交，不能 commit 后只装内存；
3. 定义通用 evidence variants；failed-node resume/skip 永久拒绝，interrupt/child 按各自边界处理；
4. 把 activation cause 变成 State-owned typed control fact，或相应收窄第一阶段 API 承诺；
5. 唯一确定 `PublicationKey` 与 `CommitKey`，删除 `state_version / commit_id` 二选一措辞；
6. 定义 acknowledgement-lost 的幂等 commit/read-reconcile 行为；
7. 修正 sibling 顺序测试，不要求逐 settlement revision history 相同；
8. 将最小 codec、payload limit、secret-safe admission 和有界 retention 移到公开 API 之前；
9. 定义第一阶段 normal successful exit 和 graph output coordinate；
10. 写出冲突场景的固定错误优先级以及“抛异常/提交 abort”的边界；
11. Kernel Port 变更同步进入 persistence adapters 和跨语言 conformance 计划；
12. public feedback 闸门包含 fresh-input、settlement、recovery、retention 和安全故障注入的完整垂直切片。

完成这些条目后，方案可沿当前方向实施，无需重新推翻 `feedback(initial, repeat)`、canonical publication 或唯一
State/commit 架构。以上是二次审计当时的结论；最终复审见下节。

## 9. 2026-09-02 最终复审

当时结论：实施计划的总体架构可以实现，没有遗留的产品裁决，可以按 P0 → P1 → P2 顺序推进。随后发现的直接
自反馈缺口见第 10 节；它不推翻总体架构，但实施方案补齐前不能进入 P1。

最终复审又补齐了以下容易在编码时走偏的约束，均已进入实施计划，不需要 owner 再做选择：

1. Graph 不做失败节点重试。Failed 是 durable terminal status/result；只有 interrupt 可以 AwaitingResume；
2. 当前 `resume_failed*`、`skip_failed`、`SkippedGraphNode` 和 Failed → Pending/Skipped 路径必须删除，不留兼容入口；
3. routing 在验证唯一 activation gate 前保留每个 `(target, cause)`，不能先用 `set[target]` 丢掉多重 cause；
4. canonical activation identity 归 State owner；reducer 验证 source 真实成功且确实选择了被引用 route；
5. commit replay 使用 canonical `EvidenceFingerprint`，不能用任意 Python 对象相等判断 evidence；
6. `RecoverySnapshot(N)` 是 exact State head N + 在 N 可见的 live evidence；evidence 用 birth/release CommitKey 表达
   生命周期，不能只加载 revision N 新写的 records；
7. durable hard limits 在 `StartGraphRun` 时按 run 冻结，恢复时不能通过新的调用参数提高或重置；
8. 一个 `FeedbackInputBinding` 只有一个 repeat source；需要 cause-dependent source 时另设计 typed API；
9. Control Join 不合并值；cyclic Join 必须有 occurrence identity，不能把不同循环轮次的 node-id arrival 混在一起；
10. failed resume/skip 这类 runtime action 由 Invocation admission/reducer 拒绝，不能写成“compiler 会拒绝”。

现有代码与目标设计不一致是预期改造面，不是方案不可实现的证据。最明显的存量差异是：

- `execution/engine/routing.py` 过早把 target 收敛为 `set[GraphNodeId]`；
- `state/graph_state/model.py` 的 `GraphJoinProgress` 只有 node id，没有 occurrence/activation identity；
- `execution/identity.py` 拥有 `StableActivation`，State 还没有唯一 activation identity；
- `execution/limits.py` 的 limits 是每次调用参数，`GraphRunState` 没有 durable frozen run policy；
- facade/state/recovery 仍实现 Failed 节点 resume/skip。

本节当时批准范围是进入 P0 契约落地；现时批准范围由第 10 节更新。

## 10. 补充审计：自己读取上一次自己尚未闭合

### 10.1 结论

实施方案已经有区分 `A[0]`、`A[1]` 的 activation/publication identity，也要求 repeat publication 严格早于
target。但这还不足以证明：

```text
A[n + 1] 必须读取且只能读取 A[n]
```

现有文字同时存在两个缺口：

1. “严格更早”只排除了当前和未来，没有单独写死“紧邻的上一次自己”，实现者仍可能错误选择 `A[n - 1]` 之前的
   更老 publication，或运行时搜索所谓“最新值”；
2. 第一阶段只允许 direct feedback cause，并明确排除 conditional feedback。若直接声明无条件 `A -> A`，每次 A
   成功后都会再次激活 A，无法用 `done -> END` 阻止回边，只能撞执行上限。这与“执行上限不是正常完成”冲突。

当前 compiler 还会把普通 `A.output -> A.input` 判为 self data cycle。这个默认行为是正确的，但实施方案必须明确：
只有 typed feedback binding 能跨 activation 自引用，不能为了支持 self feedback 放开普通数据自环。

这是 **S0 工程阻断**，不是需要 owner 选择的产品问题。实施方应直接按下述唯一模型修订方案。

### 10.2 必须采用的语义

第一阶段增加一个受限的直接自反馈白名单：

```text
START -> A(seed)

A --feedback route--> A(previous A output)
A --terminal route--> END
```

示例 route 可以叫 `continue` / `done`，内部仍使用现有 typed `GraphRouteId`，不得引入字符串 kind。规则如下：

1. A 的输入显式声明 `feedback(initial=GraphInputRef, repeat=A.output)`；`repeat producer == target` 只在这个 typed
   binding 中合法；
2. feedback route 与 terminal route 由同一次 `SelectGraphRoute` 二选一。命中 feedback route 才创建下一个 A；命中
   terminal route 时只完成 Graph，不创建下一 activation；
3. `A[n]` successful settlement、canonical publication 和 route 先原子确认；确认后才能提交
   `A[n + 1]` frontier；
4. `A[n + 1]` 的 State-owned cause 精确引用 `ActivationReference(A[n], feedback route)`。Reducer 验证该 source
   就是已确认成功且实际选择该 route 的前驱；
5. compiler 为该白名单生成唯一 `RELATIVE(1)` publication selection，并证明它解析出的 activation 与 cause 中的
   `A[n]` 完全相同；materialization 据此读取 exact `PublicationKey(A[n], output descriptor)`；
6. 禁止扫描“最新的 A”、按 frame 是否存在猜测、保存第二个 `previous_value` slot，或另建 self-loop manager；
7. terminal route 使用当前 `A[n]` 的 canonical publication 生成 Graph output；不得重新执行 A，也不得读取上一轮
   output 充当最终结果；
8. generic conditional feedback 仍可留到后续。第一阶段只放开上述“一个 target、一个 self repeat source、一条
   feedback route、一条 terminal route”的封闭形状，runtime 不增加 `if self_loop` 专用执行路径。

这套语义复用唯一 Graph facade、`GraphRunState`、reducer、routing、publication、commit 和 recovery，不增加新
owner，也不复制 payload。

### 10.3 实施方案必须修改的位置

实施方至少要修改实施方案以下内容：

1. declaration 章节明确允许 `FeedbackInputBinding.repeat` 引用 target 自己的 output；
2. seed/repeat 章节增加“exact immediate predecessor”规则，不能只写 `strict earlier`；
3. 第一阶段 topology 白名单加入受限 conditional self route，并删除“第一阶段一律拒绝 conditional feedback”的
   绝对表述；
4. compiler proof 写明普通 self data cycle 继续拒绝，仅 typed feedback 绕开同轮 dependency；
5. State/reducer 写明 cause source 必须是真实的 `A[n] + selected feedback route`；
6. materialization 写明 `RELATIVE(1)` 与 cause identity 必须一致，不能只凭数值坐标读任意旧 publication；
7. normal exit、graph output、durable recovery 和 evidence release 同时覆盖 self feedback；
8. P0 退出条件、影响面和最小验收矩阵加入下一节全部用例。

### 10.4 必须增加的验收用例

| 场景 | 预期 |
| --- | --- |
| `A[0]` 从 START 激活 | 只读取 initial，不读取任何历史 A publication |
| `A[0]` 输出 `x0` 并选择 feedback route | 先确认 `PublicationKey(A[0])`，再创建 `A[1]` |
| materialize `A[1]` | 精确读取 `x0`；所选 publication identity 必须等于 cause 中的 `A[0]` |
| 已存在 `A[n - 1]`、`A[n]` 多份历史 evidence | `A[n + 1]` 只能读取 `A[n]`，不得读取更老记录 |
| cause 指向 `A[n - 1]`，但 target 是 `A[n + 1]` | reducer/compiler admission fail closed |
| `A[n]` publication 已提交、`A[n + 1]` frame 安装前崩溃 | 从 exact committed evidence 重建，不回退 seed |
| `A[n + 1]` 缺少 `A[n]` evidence | claim 前失败，不搜索其他 A publication |
| `A[n]` 选择 terminal route | 当前 `A[n]` output 成为 Graph output；没有 `A[n + 1]` |
| 无条件 direct `A -> A` 且没有可证明正常出口 | compiler 拒绝，不能依赖 `max_supersteps` 完成 |
| 普通输入直接绑定 `A.output -> A.input` | 继续按 ordinary data self-cycle 拒绝 |
| self-feedback 节点失败 | Graph 进入 terminal Failed；不得把 feedback 当失败重试 |
| predecessor evidence release 前后崩溃 | Pending successor 永不丢输入；已结算 successor 不复活旧值 |

### 10.5 现时批准范围

总体 owner、State、publication 和 commit 架构无需推翻，也没有问题需要再次交给 owner 裁决。当前只批准实施方修订
P0 实施方案和 proof；上述语义与验收矩阵进入实施方案并通过复审后，才批准进入 P1 内部实现。P2 durable vertical
slice 完成前仍不得开放公开 feedback API。
