# Graph 显式反馈回环二次审计回复

状态：已处理；已补入 owner 最终裁决，最终结论见第三次复审

对象：[`Graph 显式反馈回环实施计划`](./graph-delayed-loop-implementation-plan.zh-CN.md)

依据：[`Graph 显式反馈回环实施计划二次审计`](./graph-delayed-loop-implementation-plan-review.zh-CN.md)

## 1. 结论

二次审计抓住了几个必须先闭合的语义：普通数据环不能偷偷放行；反馈值必须复用 canonical publication；seed 和
repeat 必须由可恢复的 activation cause 区分；State、结果和输入证据必须在确认边界上保持一致；第一阶段必须有
正常成功出口。这些意见已经写入实施计划。

审计中有一部分把“生产级持久化系统的完整要求”误当成“第一阶段 Graph 设计的立即前置条件”。这些意见的方向
有价值，但原定范围和时序不成立，已在回复中保留原则、收窄承诺：

- 不承诺脱离 value evidence 的“纯 State-only recovery”，改称 **state-led recovery**；
- 第一阶段只做 fresh root、单 scope、单 feedback；failed-node resume/skip 永久不属于 Graph，
  interrupt continuation、child、multi-branch 按各自范围另行开放；
- cause 保持 State-owned typed fact，但不在 P0 锁死审计中给出的字段布局；
- codec、大小、脱敏和 retention 由 durable persistence capability 负责，先有最小安全边界，再做完整压缩优化；
- adapter、跨语言 conformance 和并发顺序测试进入影响面及 P2/P3 门禁，不把它们伪装成 P0 的 Graph 语义；
- 错误采用分层的确定性 admission 顺序，不强迫所有 owner 使用一条跨模块的异常总排序。

## 2. 已采纳并落入实施计划

| 审计意见 | 处理 | 落点 |
| --- | --- | --- |
| 普通 `NodeOutputRef` 数据环继续拒绝，反馈必须显式声明 | 采纳 | 计划第 1、3、6 节 |
| 一个 producer 只保留一个 canonical publication/result，不建 per-binding slot | 采纳 | 计划第 1、4 节 |
| seed/repeat 不能靠 frame 是否存在推断 | 采纳 | 计划第 3.2、3.3 节 |
| activation cause 要进入唯一 `GraphRunState` 控制事实 | 采纳，表示层留到 P1 | 计划第 3.2 节 |
| successful settlement 与 publication/result 共享 durable commit 边界 | 采纳，限定在 durable mode | 计划第 4、5 节 |
| publication identity 与 commit/CAS identity 分离，并处理 acknowledgement lost | 采纳为内部协议约束 | 计划第 2.2、5 节 |
| Invocation/Execution 装载证据，node callable 不接触 persistence/codec/State | 采纳 | 计划第 4.4 节 |
| direct loop 必须有 `done -> END` 正常出口，不能把 `max_supersteps` 当业务完成 | 采纳 | 计划第 6.2 节 |
| 多条回边不能让同一 target 被同一轮激活两次 | 采纳并现在锁定 | 计划第 3.3、6.3、13 节 |
| 两阶段共用 declaration、compiler proof、routing、commit、recovery，不建第二 runner/reducer | 采纳 | 计划第 1、6.3、9 节 |
| adapter 和 conformance 需要同步评估 | 采纳为影响面及后续门禁 | 计划第 11、9 节 |
| Graph 是否负责失败节点重试 | 最终裁决：不负责；failover 是唯一 owner | 计划第 1、4.2、7、12、13 节 |

## 3. 不采纳的绝对化要求及理由

### 3.1 将“state-only”改成精确的 state-led recovery

审计正文实际要求的是“无 continuation 时读取 exact State head，并加载该 head 可见的 live evidence”，这个方向
合理，已采纳。需要回绝的只是“state-only”这个容易被读成“State 单独包含所有 value”的表述：当前 typed frame
设计下，State 不能凭空产生 graph input、node result 或外部 receipt。

实施计划因此统一使用 **state-led recovery**：调用方可以不提供 continuation；Execution 读取 revision N 的 exact
authoritative State，以及在 N 可见的完整 durable evidence set，再重建 frame。live evidence 可以出生在更早 commit；
不能误解成“只读 N 写入的 records”。没有 evidence 时必须 fail closed，绝不回退 seed，也不重跑已确认的 producer。
已有 continuation 只对非失败恢复语义继续有效；它不能把 `FailedGraphNode` 改回 Pending。

### 3.2 failed-node resume/skip 不进入 Graph recovery

这里不再存在“第一阶段是否支持失败重试”的待裁决项。最终边界是：节点一旦确认 `FailedGraphNode`，本次 Graph
运行在当前 frontier settlement 闭合后进入 durable terminal Failed；Graph 不得通过 `resume_failed`、输入 override、
`skip_failed` 或同输入重调度把它改回 Pending。Failed 不再属于 `AwaitingResume`；只有 interrupt 可以等待
continuation。

Failover 的再次尝试由专门的 failover 图表达：一次 `InvokeOnce` 把可重试的 Port outcome 作为成功 typed value
提交，policy 再显式路由到新的 activation。Graph failure 不是 retry signal。interrupt continuation、child boundary
和跨 scope 是别的语义问题；未来若支持，它们仍复用同一 typed evidence Port 和 commit 边界，但不能借此重新引入
failed-node retry。

当前生产代码中的 `Graph.resume_failed()`、`Graph.resume_failed_with()`、`Graph.skip_failed()`、`SkippedGraphNode`、
Failed → `AwaitingResume` 以及对应 admission/reducer/materialization 路径都与该边界冲突，是明确的待清理架构债；
不能作为兼容 API 保留。

### 3.3 不锁死审计建议的 cause 字段布局

“cause 必须是 State-owned typed fact”是必要的。最终实现不允许 State 反向导入 Execution-owned
`StableActivation`：P1 要在 State owner 提取唯一 `GraphActivationIdentity(run_id, superstep, node_id)`，Execution
只能把它投影成 lookup coordinate，不能保留第二份 canonical identity 或兼容别名。

`Start` 与 `Routed` 是封闭的 cause union，cause 不可变、可序列化、可校验。State reducer 不读取
`CompiledGraph`，但必须验证 cause 引用的是 authoritative frontier 中真实成功 settlement 且实际选择了该 route 的
source（或有 occurrence identity 的已提交 Join arrival）；Execution/compiler 再验证它能按 topology 唯一激活
target。仅检查“source 坐标比 target 早”不够。

### 3.4 不要求所有 Graph 在 assembly 时都提供 codec

当前 `Graph` 支持进程内 typed value，Graph facade 不应因为未来某个 persistence backend 的编码方式而改变所有
图的装配契约。codec 属于 persistence capability 的 admission 边界：启用 durable feedback 时必须有匹配的 codec、
版本和 bytes 上限；纯内存或 continuation 模式不被迫注册一个无意义的序列化器。

这仍然保留审计关心的安全性：进入 durable commit 前必须完成 exact bytes、版本、大小、corruption 和 nominal type
检查；错误不能泄露 payload。不同 backend 的 codec/schema 由 persistence owner 实现，Kernel 只依赖窄 typed Port。

### 3.5 不把后端排期倒灌成 Graph P0

审计要求 persistence adapter 和跨语言 conformance 进入影响面，这一点采纳；不采纳的是把所有后端的迁移进度当成
Graph P0 的完成条件。Graph P0 应先冻结 Kernel-owned declaration、State 和 recovery contract，不能因为某个
adapter 尚未实现就改变 Graph 语义或增加后端特判。

计划保留 adapter/conformance 影响面，并把至少一个 reference persistence adapter、故障向量和跨语言契约放进
durable public API 的 P2 闸门。其他 adapter（包括 Cloudflare）按同一 contract 跟进；未完成 adapter 时不开放
对应 durable capability。

### 3.6 不强制一条跨所有 owner 的“总错误顺序”

审计列出的顺序对 recovery admission 很有参考价值，但要求 routing、State reducer、persistence adapter 和
facade 在所有冲突中返回同一层级错误，会把不同 owner 的职责和既有 `AbortGraphRun`/typed exception 边界绑成一条
脆弱链路，也可能让不可信记录在尚未完成最小身份校验时暴露更多信息。

计划采用最小共同顺序：记录形状/信任与 scope identity 先于 value admission；codec/type 先于 evidence availability；
claim 前必须完成全部 admission。具体 owner 继续返回自己的 typed error；业务 routing 缺值沿用现有
`AbortGraphRun`，恢复前置失败沿用既有异常。P1 conformance 会固定每个边界的可观察错误，不承诺一个跨模块的总排序。

### 3.7 不在单 scope 第一阶段承诺 sibling 顺序等价

审计要求 sibling completion 顺序交换后 publication 集合和最终 State 等价，这对未来多分枝是正确的测试方向，
但第一阶段明确没有 sibling、join 或多 feedback。现在把它列为 P0 退出条件只会制造没有被实现模型支撑的承诺。

该性质移到 P3 multi-branch proof；第一阶段只验证单 activation 的 deterministic publication key、CAS 冲突和恢复
结果一致。

这不影响已经锁定的激活基数规则：无论未来是否有 sibling，同一个 target 在同一个 frontier 坐标都只能有一次
activation。互斥路线必须由 compiler 证明；可能同时到达的路线必须显式 Join；仅共享同一 publication 不能消除
重复激活。

### 3.8 不要求第一阶段支持非零 seed 的 `NodeOutputRef`

审计担心 `initial=NodeOutputRef` 的非零 seed 会迫使 runtime 猜测首轮。这个担心成立，但结论不应是立即扩大
第一阶段：第一阶段把 initial source 收窄为 `GraphInputRef`，首个 target 必须带唯一 Start cause；内部 declaration
仍保留可扩展的 source union，未来在已有 cause/recovery 模型上开放 NodeOutputRef seed。

这样不会用 `superstep == 0` 猜首轮，也不会为一种尚未需要的 seed 形状造第二套执行逻辑。

### 3.9 不把完整 live-set/compaction proof 提前成 API 前置

无界 publication 是必须避免的风险，但“编译器在 P0 产出所有未来拓扑的完整 live publication 集合”属于生命周期
优化，不是 feedback 语义成立的最小条件。第一阶段先要求最小 live evidence 规则，并在 `StartGraphRun` 时把
`max_supersteps`、live-evidence/bytes hard cap 等执行上限冻结到 durable run policy；重启不得提高或重置。超限和
无法安全释放时 fail closed。完整 retention compaction、长 loop 压测和复杂 topology liveness 放到 P4/P3，并继续
使用同一 commit boundary。

## 4. 对实施计划的影响

以上取舍已反映到实施计划：

1. 使用“state-led recovery”替代没有 evidence 的“纯 state-only”；
2. 第一阶段限制为 fresh root、单 scope、`GraphInputRef` seed、单 direct feedback 和明确 done exit；
3. persistence codec/size/redaction 是 durable capability 的前置，不改变所有 Graph 的 assembly API；
4. P0/P1 关注 Kernel contract 与 compiler proof，P2 才闭合 reference adapter、conformance 和 durable vertical
   slice；
5. 错误采用共同 admission 原则，具体异常归 owner；
6. 多分枝 sibling 顺序和完整 retention proof 延后到相应阶段，不为简单情况增加专用分支；但“同一 target 同一
   frontier 只能激活一次”现在就是硬不变量；
7. Graph 的 state-led recovery 只重建已确认 State/evidence，不恢复失败节点；failed-node retry/override/skip 类型和
   公开路径必须删除，reducer 对旧/伪造 action 拒绝，Failed 必须成为 durable terminal status/result，Failover 用
   显式的新 activation 表达下一次 Port attempt；
8. canonical activation identity 归 State owner；routing 在 collapse target 前保留 `(target, cause)` 并要求唯一
   gate 命中，reducer 验证 cause 的真实 settlement/selected route；
9. `RecoverySnapshot(N)` 返回 exact State head N 与 N 上 live 的 evidence，evidence 记录 birth/release CommitKey；
   commit replay 使用 canonical `EvidenceFingerprint`，不靠 Python 对象相等；
10. 一个 feedback binding 只有一个 repeat source；Control Join 不合并值；cyclic Join 必须按 occurrence identity
    隔离 arrivals；durable hard limits 按 run 冻结并跨恢复保持不变。

最终复审已经确认方案可实现。当前只批准进入 P0 契约落地；P0 退出前不修改 Graph 生产执行路径，P2 durable
vertical slice 通过前不新增公开 `Graph.feedback(...)`。
