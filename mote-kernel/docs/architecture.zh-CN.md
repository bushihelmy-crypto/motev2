# 架构

Mote Kernel 将领域流程、图执行、统一状态与外部能力实现分离。

- Domain Flow 定义业务流程拓扑。
- Execution 是所有流程复用的唯一图执行底座。
- State transition 统一决定图推进、节点结果和业务变化的合法转换。
- Port 提供可替换的外部能力，不拥有 Kernel 状态。

当前唯一的权威状态类型是不可变的 `GraphRunState`。现阶段它记录 graph run 的执行事实（frontier、settlement、
routing、lease、resource、恢复坐标和 revision）。以后增加节点/Hook 结果或业务事实时，继续扩展这个类型，
不另建状态模型；状态只有一个 owner 和一个原子提交边界。

同一并发 frontier 中的所有节点接收同一个不可变输入快照。节点和 Port 必须只读该快照，通过类型化结果表达变化，Kernel 不会隐式复制任意领域 DTO；DTO 所有者必须将其定义为不可变值。

`mote_kernel.execution.Graph` 是唯一公开的图构建与执行门面。它在第一次成功编译前是 topology builder；`run()` 或只读的 `recovery_child_reads()` 投影触发编译，完成校验并冻结为 immutable compiled runtime。门面实例不保存 run snapshot、session 或 transient output，因此同一张已组装图可以驱动相互独立的 run，而不会成为第二份状态真相。

### Graph 节点与 Runtime Invocation 的边界

这里有两个容易被混叫成“调用”的概念，但它们不是两条 Graph 执行路径：

```text
Graph / Execution
    └─> Kernel-owned NodeCallable ──> TaskScheduler（唯一节点调用点）
                                      └─> owner-defined Port
                                           └─> Invocation（需要跨进程/传输时）
                                                └─> Runtime
```

`execution.graph.node.NodeCallable` 是 Kernel 节点契约：它描述一个已经装配进图的节点如何消费
typed frame、产生 Graph outcome；`CallableNodeDefinition` 只保存这个节点程序，运行时由
`execution.engine.scheduler.TaskScheduler` 统一调用。它不是 Runtime service，也不解析 transport、
resolver 或外部 operation receipt。

`mote_kernel.invocation.Invocation` 是 Port 到 Runtime 的窄适配契约。它由具体 Port 持有并按该 Port
自己的 request/result 使用；它不是通用 Graph node runner，Graph 也不直接依赖它。节点需要外部能力时，
由节点调用 owner-defined Port，Port 再决定是否通过 Invocation 到达 Runtime。这样既保留纯节点的本地
计算，又让外部能力的 Kernel/Runtime 边界保持显式。

当前 pre-alpha 没有具体的跨进程 Graph worker consumer，因此不在 Graph 中增加 operation registry、
通用 operation identity 或 callable wrapper。未来若出现远程节点执行的真实 consumer，应新增一个明确
版本化的 operation binding/resolver，并一次性迁移节点定义；不能在现有 NodeCallable 旁边再铺一条
隐式 Invocation 执行路径。

`Graph.run()` 从 typed values 新建运行，或从 authoritative state 与已准入的证据继续运行。failure、interrupt、skip、节点结果和
Hook 变化都通过同一个 `GraphRunCommand` 入口处理，不存在第二个状态或 resume runner。新 run 或 control-only state
调用未传 commit 时只在进程内应用纯状态转换；continuation 则继承原有 commit capability。存在回调时，
每条 command、candidate 和完整 typed write-set 都交给回调
完成统一状态的原子提交，且仅以回调精确返回的 candidate 继续执行。这是提交边界，不是具体 Store 或
durability 承诺。

提交边界的 typed `GraphTransition`、`GraphCommitWriteSet`、exact acknowledgement 和纯 frame projection 由
`execution/commit.py` 作为一个完整 owner 管理；`execution/family_driver.py` 只负责 family owner 的驱动、child handoff、
并发清理和结果投影，不复制提交规则或建立第二个 runner。driver 在提交前准备不可变 frame projection，仅在确认后
替换运行中的 state/frame。两者通过窄的内部调用连接，公共入口仍只有 `Graph`。

`execution/graph_result.py` 统一拥有 sealed family result、partial handoff 和 continuation provenance。
不可变的 `ContinuationSnapshot` 保存 exact `GraphCommit` capability，不导入或识别具体持久化实现。
省略 commit 或传 `None` 都继承原 capability；显式传入必须是原对象，字段相等的新对象也拒绝。
transient continuation 不能中途增加 commit；需要换提交能力时必须重新读取权威 checkpoint。
`run_context.py` 仍只拥有 frame/evidence，`result.py` 仍拥有 task outcome 与执行 disposition；不使用循环依赖或
无类型 capability token 绕过 owner 边界。

## State 包与所有权

`src/mote_kernel/state/` 是状态事实与状态转换的唯一 owner。当前具体实现位于 `state/graph_state/`；这只是
代码路径，不代表第二种状态。对外设计保持最小且统一：

- `state/graph_state/model.py` 定义不可变的 `GraphRunState` 及其值对象；
- `state/graph_state/command.py` 定义封闭的类型化 `GraphRunCommand` union；
- `state/graph_state/reducer.py`（`reduce_graph_run`）是唯一纯 dispatch 入口；
- validation、identity、frontier/resource/routing 值对象和 transition result 都仍属于同一个 `state` owner。

这些文件只是按职责组织实现，并不代表多个运行时状态。节点或 Hook 只能返回类型化 result/command；只有
`reduce_graph_run` 能生成下一个 `GraphRunState`。任何 flow、execution session 或 extension 都不得维护平行快照、
第二个 reducer 或另一条状态存储路径。

底层存储可以分记录保存，但同一 scoped run 的 `GraphRunState` 与完整值写集必须属于同一个原子提交。
root 和每个 child 各自使用已有 revision 与 `GraphCommitKey`；family 读取必须一致，不能假设所有 child revision 相等。
`GraphCheckpoint` 只是这些既有 state 和值证据的一致性读响应，不是第二种 runtime state。
Config payload 与能力解析仍归 Config owner；state/frame 只保存精确快照引用，不序列化 capability。

`AgentSession` 是 Runtime/Agent 调用方拥有的三字段快照（`hook_state`、`context`、`config`），不是
`GraphRunState` 的别名。节点通过 `Graph.success(..., session=...)` 或 `Graph.SessionActivation` 显式
提交完整 successor；`_GraphRun` 只在同一 transition receipt 确认后推进 owner Session。没有 successor
的节点继承当前 owner snapshot，Kernel 不 merge 业务字段。一次 invocation/family 只有一个由 root、child 和
sibling 共享的 `_FamilySessionOwner`；它把“读取当前值→准备 transition→durable commit/reconcile→推进 owner→
安装 state/frame”放在同一串行边界内，因此陈旧的继承快照不能覆盖已经确认的 successor。多个完整 successor
按实际 receipt 确认顺序生效，不做字段合并。嵌套 child 等待恢复时，父 root state 的 Config cursor 可以落后于
child 已确认的 Session；family checkpoint 会约束该 cursor 必须属于某个已确认 scope，但不会再保存 child 自己的
Session 镜像。
`GraphRecovery` 要求 checkpoint 与 bound
`DurableGraphCommit` 的 encoded Session 完全相等；存在 Session 时还用同一 codec 对 decoded snapshot 做
canonical re-encode。恢复的历史 frame 只恢复业务值和历史 Config provenance，当前 owner Session 由执行请求
注入节点输入，避免把旧 Config 与新 Session 伪造成冲突。`session.config` 引用的 immutable snapshot 必须
先由 Config owner 保存；Agent 不代为 save。

## 后端无关的持久化边界

`execution/persistence.py` 拥有持久值证据、durable commit 适配和 checkpoint materialization；这些类型均为
owner-internal 基础设施，不重新导出为平行公共入口，唯一执行门面仍是 `Graph`。

- `graph/codec.py` 的 `FrameCodec` 同时复用在 resume input 与持久 frame。领域提供版本化、确定性的不可变值 codec；
  持久记录保存完整 bytes、精确 compiled 坐标、birth commit 和可选的精确 Config cursor。已解析 Config 能力不进入 codec。
- `DurableGraphCommit` 将既有 sealed transition 投影为 `GraphPersistenceCommit`：scope、expected revision、
  candidate state、commit key 与完整 graph input/publication 写集。command 仍留在 execution/reducer owner，后端
  不解释执行命令。writer 必须原子写入并精确确认整个请求；不能以业务 DTO equality、仅 revision 或仅 state 代替值确认。
  跨 writer 边界前，commit owner 保存独立的已准入 baseline；调用后的原请求和返回 receipt 都必须与其精确相等，
  原地 mutation 不能重定义 acknowledgement。
- `GraphRecovery` 将已读取 checkpoint、必需的 `DurableGraphCommit` 与精确解析的 Config 绑定后交给同一个
  `Graph.run()`。恢复和后续提交只能使用该 commit 持有的同一个 codec；`run(recovery=...)` 拒绝另外传入 commit。
  材料解码复用 compiler
  descriptor、scoped state validation、typed frame admission、lineage、routing 和 output projection，然后进入已有
  fence/resume/preflight/family driver。缺失或冲突证据在任何恢复提交或节点调用前拒绝，不靠重跑已结算 producer、
  查找 latest publication 或 latest Config 补齐。
- 读取 envelope、嵌套 record 和 receipt 时完整重新准入，不能假定反序列化曾执行构造器。每个值只有一份
  State-owned `GraphEvidenceCommitment`，由 execution commit owner 对带领域前缀的 canonical evidence tuple 计算：
  scope/run 与 activation 坐标、descriptor identity、birth commit、codec identity/version、Config cursor 元数据
  （含缺席）和 payload bytes；publication 还绑定精确 settlement execution provenance。同一 commitment 同时进入
  权威 State 账本和持久记录；交换 payload 或重分配坐标不能靠只重建 frame 取得合法身份。非空 Frame Config 必须属于所属 state 的 Config definition/version，revision
  不得超前，同 revision 的 digest 必须相同；同一不可变 Config revision 不能提供两个解析结果，即使暂未被引用。
  历史上合法的无 Config frame 保持无 Config，不能用当前 state 或可用 capability 补齐。Graph 自己的 Config 更新
  command 仍由 Observe 消费并随其 settlement 提交持久化；节点可以在显式返回的完整 `AgentSession` successor
  中放入已解析的 Config，但 Kernel 不猜测或合并该字段。恢复不增加第二个更新入口。这里的 Config snapshot cursor
  与已删除的 Observe 调用方 cursor 无关。
- 全部生命周期（含 completed）保留 `GraphRunState.settled_publications`。每项唯一持有 activation reference、
  真实 settlement commit revision、execution token 和可选 durable value commitment。checkpoint publication 集必须
  与完整成功账本精确相等；中间 publication 缺失和从未结算的额外 publication 都拒绝，不另建 manifest 或终态
  压缩分支。completed child 的输出仍由既有 projection 自底向上重建。
- `GraphCheckpoint.child_runs` 只携带 `ScopedStateBinding` 或显式的 `UncreatedGraphRun` 读取证据。必需 child
  来自 state 持有的历史成功/current nested activation；省略记录不等于从未创建。只有当前 pending child 的权威
  negative read 能进入既有 create-if-absent 路径；确认创建后，由同一个 family evidence owner 替换该证据。
  持久化冲突不能越过提交边界执行 leaf。完整一致读取与权威 CAS 仍由 Port 实现负责。

`Graph.run(state=...)` 不读取 Store，也不恢复缺失 frame；opaque continuation 始终是不可序列化的进程内交接材料。
两者都不能替代完整 checkpoint read。

## Agent 权限与恢复接线

`agent.py` 是唯一外部恢复装配入口。`Agent` 只持有 frozen capabilities，不保留运行 state、continuation、权限缓存或
第二个 scheduler。`AgentStart` 只创建从未存在的 run；`AgentResume` 继续已有 run，或回放其终态业务结果。
回答保留精确 interrupt 问题和 typed 业务值；结果只暴露输出、失败、待回答问题或 abort，不暴露 Graph
state 或 continuation 恢复快照。
结果同时携带最后一份已确认的 `AgentSession`，供 Runtime 显式交给后续 run；不暴露 Graph state、continuation
或持久化能力。

每次调用只有一条完整链路：

1. 经 `AuthorityPort` 为 `AgentRunKey(agent_id, run_id)` 获取排他的 `ExecutionAuthority`。
2. `PersistencePort.load` 返回完整一致的 family 或明确的 `NeverCreated`。不可用、tombstone、记录丢失与身份冲突
   均不能转换成新建运行。
3. 精确解析 checkpoint 全部 Config 引用后装配 Graph。可选 `AgentConfig.initial` 仅决定新 run 的初始快照；
   Agent 不保存 Config、不拿 latest 替换历史，Observe 仍是唯一更新消费方。
4. `Graph.recovery_child_reads` 复用 compiled topology 和 lineage 推导缺少的 pending child 坐标。仅当需要时，
   Agent 在同一权限下重读；`GraphCheckpoint.admit_child_reads` 要求全部已有事实不变且负证据恰好对应请求。
   后端不解释 Graph 拓扑。
5. 以 codec 绑定的 `DurableGraphCommit` 调用同一个 `Graph.run()`，投影业务结果，等待执行任务清理完成后释放权限。
   部分提交交接只以原始异常离开 Agent，不交付另一条 continuation 恢复路径。

根级 `persistence.py` 拥有后端无关 Port 契约；`execution/persistence.py` 继续独占 Graph 编码、checkpoint 和精确
receipt 准入。每次读取、提交与对账都携带同一 opaque authority。Adapter 必须原子检查权限、scope 的 absence/revision、
完整请求身份，并一起提交 state/value/receipt；同 key 同内容是精确重放，不同内容是冲突。Kernel 不建立租约时钟、锁、
后端选择器、传输、数据库 schema 或工具执行账本。获取权限无法返回 grant 时，其 Port 自行收敛获取的不确定结果；
释放旧权限不得撤销后继权限。

`CommitUnknown` 只对账同一不可变请求，不重新编码或执行节点。只有 `CommitNotApplied` 才能在显式
`max_commit_attempts`（默认 3）内重发。结果仍未知、权限失效、冲突或非精确 acknowledgement 均停止推进。
提交 owner 用 `GraphCommitError` 保留错误来源，直到 Graph 边界还原原始异常；worker fan-in 等待全部任务后仍保留
该分类，包括构造、交接、fence 和 abort 期间的提交失败。祖先/sibling 不再从旧内存写 cleanup transition。
普通非提交错误的优先级、caller/node 取消边界继续独立；权限释放失败不覆盖原有执行错误。

snapshot 与 receipt-journal 两种测试适配器经过同一个 Agent API。独立进程故障矩阵覆盖 linear、并发 sibling、loop、
nested family、Config、interrupt、旧权限 fencing 与 Runtime 类型化结果边界的写前/写后退出，每次都以全新的
Agent/Graph/codec 恢复。这些适配器不是生产后端，也不证明真实数据库断电持久性、网络分区或外部权限服务；精确
验收证据与限制以[实施计划](kernel-persistence-implementation-plan.zh-CN.md)为准。工具执行记录与崩溃对账归 Runtime；
ReAct END 后的新任务仍由上层驱动。

## Graph Frontier 执行

统一 `GraphRunState` 中的执行字段是 Frontier 结算、资源所有权和 active execution token 的唯一 durable truth。一次原子的
`ClaimGraphExecution` 转换安装 token-only lease，并在需要时同时安装初始 `ResourceSnapshot`。

门面内部的 `GraphExecutor.issue_session()` 是唯一受支持的 session 创建入口。它线性消费 prepared claim 后签发单消费者
`GraphExecutionSession`；内部 session contract 是不可直接构造的协议。每次 `next(authoritative_state)` 先确认上一条 reducer command
产生的精确后继已经提交，再至多交付一个 typed node completion 和一个 `SettleGraphNode`。并发 `next()` 在进入 scheduler 前 fail closed；`aclose()`
幂等，并等待所有 live task 停止。
取消 `next()` 会先完成 close 再传播 cancellation；cleanup 期间再次取消同一 task 也不能中断 close。

`SettleGraphNode` 在同一个新 `GraphRunState` 中原子记录该节点 settlement、节点确认结果、释放该节点资源并推进确定性 resource waiter。资源要求只影响
唯一 scheduler 当前可以选择哪些 Pending node。调用方应用 settlement 并确认 successor state 后，即使已有 typed sibling completion
排队，刚 admitted 的 waiter 也会在该次 session step 中立即提交；已经观察到 ordinary error 时则停止全部新 activation。

最后一个节点先持久形成稳定的 `RUNNING + SETTLED` Frontier。Routing 只能基于这个已提交屏障解析，再单独产生
`AdvanceGraphFrontier` 或 `CompleteGraphFrontier` 转换。Session queue 与 task handle 都是 transient runtime facts，不构成 Store、retry
策略、exactly-once 保证或第二套 durable state。

Publication 将值与已确认的 scoped revision、execution provenance、descriptor 和 activation 坐标一起保存。
它是同一提交的不可变证据，不是可独立更新的第二份状态。
