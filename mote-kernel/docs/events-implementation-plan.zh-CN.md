# Events 原子提交与可靠投递实施计划

状态：**核心原则已冻结；按 owner 补齐唯一 typed 契约后分阶段实施**

制定日期：2026-09-02

相关文档：

- [`events-design.zh-CN.md`](./events-design.zh-CN.md)
- [`events-design-review.zh-CN.md`](./events-design-review.zh-CN.md)
- [`events-implementation-plan-review.zh-CN.md`](./events-implementation-plan-review.zh-CN.md)（已驳回，仅保留审查记录）
- [`architecture.zh-CN.md`](./architecture.zh-CN.md)
- [`execution-state-frontier-call-chain.zh-CN.md`](./execution-state-frontier-call-chain.zh-CN.md)

本计划以需求方后续确认的“同一 commit 原子提交、执行事实只保存一份”为最终基线。该决定取代当前
实现中的“状态 callback 返回后再单独调用 event sink”路径，也取代评审稿中基于 post-commit sink 的
best-effort 建议。

## 1. 最终目标

每个节点 settlement 仍然只经过 Kernel 现有的唯一 commit 边界。一次 commit 必须原子完成：

```text
candidate GraphRunState 逻辑快照
+ 一条待投递 Event 记录
```

这里的逻辑快照同时包含执行位置，以及与该版本绑定的 Graph input、frame/publication、invocation 和 settlement
结果事实。持久化层可以把它们规范化到不同表，但它们只能共同组成一个 `GraphRunState` 快照，并使用同一个
`run_id + scope + revision` 坐标原子确认。任何一项写入失败，整个 commit 都失败；不能出现“State 成功但 Event
丢失”“Event 存在但 State 未确认”或“State 与执行值来自不同版本”。

Event 不复制节点入参和结果。节点入参、节点结果及其来源在 `GraphRunState` 逻辑快照中只持久化一次；
Event 只保存指向该快照中本次 settlement 的稳定坐标和投递状态。投递时根据该坐标读取同一份事实，组装出：

```text
节点名称 + 实际访问参数 + 执行结果
```

## 2. 已冻结的设计决定

以下决定不在编码阶段重新讨论：

1. `mote_kernel.execution.Graph` 仍是唯一图组合和执行门面；不增加第二张图或 Events runner。
2. `GraphRunState` 是唯一 Graph 运行时状态和唯一逻辑快照 owner；不增加平行 evidence state、result state 或
   snapshot。
3. Graph input、frame/publication、resume input、child boundary、invocation 和 settlement result 都必须归属同一个
   `GraphRunState` 版本。物理拆表不等于拆 owner，任何一部分都不能独立提交或独立成为权威读取结果。
4. Event 投递状态是 persistence 的 outbox 操作状态，不进入 `GraphRunState`，也不能反向驱动 Graph 恢复或推进。
5. 实际节点输入是 execution 完成绑定、历史选择和 resume override 后得到的最终 `NodeInputFrame`；events 不重新合并、
   不从最新 State 猜测，也不建立缓存。
6. Event/outbox 记录不复制业务值，只保存稳定 snapshot/activation 坐标、事件身份和投递状态。
7. candidate `GraphRunState` 完整快照和 Event/outbox 记录必须由同一个持久化 commit 在一个事务中提交。
8. `Graph.Transition` 是唯一 commit 输入；不再增加与 candidate snapshot 等价的 `GraphCommitEvidence` sidecar。
9. Graph commit 中不直接调用 Kafka、HTTP、Webhook 或其他远端 transport。
10. 远端投递由 persistence/outbox owner 在 Graph commit 之外完成；失败时重试同一个 `event_id`。
11. events 在 commit decorator 链内层，logging 在外层；组装方固定顺序，两个包不互相发现或重排。
12. 并发节点不承诺全局事件顺序；消费者只按稳定身份关联事件。
13. 根包唯一公共入口仍是 `EventingGraphCommit`，不增加 EventBus、manager、registry 或兼容 API。
14. 不保留“可丢失的进程内 Events sink”作为第二条路径；若 logging/observability 需要旁路观测，由其自身 decorator
    负责，不能复用或稀释 durable Events 契约。

### 2.1 Owner 边界

| Owner | 唯一职责 | 明确不负责 |
| --- | --- | --- |
| State / execution | 生成完整 candidate `GraphRunState` 快照，并以唯一 `Graph.Transition` 调用 commit | Event、数据库、outbox、transport |
| `mote_kernel.events` | 从 settlement transition 纯投影 Event 引用；把 persistence capability 装饰成普通 `Graph.Commit` | Store、事务实现、恢复、dispatcher、后台任务 |
| `infra/persistence` | 实现 State 快照与 Event/outbox 的原子事务、历史版本读取和幂等 reconcile | Graph 调度、事件业务投影 |
| runtime / persistence dispatcher | 按引用读取快照、组装 wire Event、发送、重试和确认 | Graph commit、Graph State 推进 |
| logging | 在最外层只读记录 commit 生命周期 | 修改 transition、Event 或提交结果 |

本计划是一个跨 owner 的交付清单，不代表 `mote_kernel.events` 自己实现整条 vertical slice。Events 代码只能摆出
窄 typed 接缝；若 State 快照或 persistence capability 尚未就绪，应把它们列为前置依赖，不能在 events 内补临时
Store、缓存或 fallback commit。

## 3. 为什么不把参数再存进 Event

节点入参不总是“上一个节点的一个出参”，但一定来自 execution 已经准入的 frame 事实：

- 首个节点可能读取 Graph 初始输入；
- 一个节点可能组合多个上游节点输出；
- 失败重试或中断恢复可能使用 resume override；
- 嵌套图可能读取父图输入或 child boundary；
- 循环中的节点输出需要按明确 superstep/activation 选择。

这些来源本来就必须属于同一个可恢复 `GraphRunState` 逻辑快照。否则进程在节点执行中崩溃后无法重新物化相同
输入，说明 Graph snapshot 本身不完整，不能靠 Event 再保存一份参数来补洞。

因此正确关系是：

```text
GraphRunState @ run_id / scope / revision     唯一逻辑快照
├── control facts
├── frame / publication / invocation facts
└── settlement result
               |
               +---- Graph 恢复读取
               `---- Event dispatcher 按坐标读取

Event/outbox 记录                            只保存坐标和投递状态
```

底层可以把 State、frame 和 result 规范化到不同表，但 loader 必须按同一个版本合成完整快照后才对外可见；不存在
可单独恢复、单独更新的 execution evidence 状态。Event 不成为 Graph 恢复数据源，Graph 也不读取 Event 来继续执行。

## 4. 三个故障恢复点

### 4.1 节点执行前：恢复相同输入

execution 在 claim 前已经物化了本次 frontier 中每个节点的最终输入。claim commit 必须让下列事实进入同一个
candidate `GraphRunState` 逻辑快照：

- 该输入依赖的 Graph input、publication、resume input 或 child boundary 已经持久存在；
- 本次 invocation 事实保存每个本地参数名对应的稳定 frame/value 引用；
- invocation 身份包含 run、scope、superstep、node 和 execution generation；
- 引用与 claim candidate 使用同一个版本坐标。

若进程在 claim 成功后、节点 settlement 前崩溃：

```text
恢复 authoritative GraphRunState 完整快照
-> fence 旧 execution attempt
-> 读取该快照同一版本的 frame 事实
-> 根据 invocation 引用重新物化相同参数
-> 以新的 execution generation 重新执行
```

这里需要恢复的是 execution 输入，不需要也不允许从 Event 恢复，因为 settlement Event 此时还不存在。

### 4.2 节点完成时：原子确认结果和待投递 Event

`SettleGraphNode` commit 必须在一个事务中写入：

```text
candidate GraphRunState 完整逻辑快照
  ├── success/failure/interrupt 结果事实
  `── success publication（存在时）
+ Event/outbox 记录，初始状态为 pending
```

Event 记录引用该 snapshot revision 中的 invocation 和 settlement，不复制它们的值。事务成功后节点已经 settled，Graph
恢复不再重跑该次成功确认的节点；若进程尚未发送 Event 就崩溃，dispatcher 可以从 pending 记录继续。

### 4.3 Event 投递时：恢复同一条消息

dispatcher 处理 pending Event 时：

```text
读取 Event 的 snapshot/activation 坐标
-> 读取对应 revision 的完整 GraphRunState 逻辑快照
-> 组装节点名称、实际参数和结果
-> 使用固定 schema/codec 发送
-> 收到确认后标记 delivered
```

若发送成功但标记 delivered 前崩溃，dispatcher 会再次发送同一个 `event_id`。因此 transport 语义是
at-least-once，消费方按 `event_id` 幂等；不宣称跨网络 exactly-once。

被 pending Event 引用的 snapshot revision 不能提前清理。retention/GC 必须先证明 Event 已 delivered，
或者按产品定义进入明确的终止状态。pending Event 指定的 schema/codec 版本也必须继续可用，不能在消息投递前
随应用升级被删除。

## 5. 唯一事实与持久化记录

### 5.1 唯一 GraphRunState 逻辑快照

`GraphRunState` 是 Kernel 对外可见的唯一完整快照。该快照对应的同一 run/scope/revision 必须能够恢复：

| 记录 | 唯一值来源 | 用途 |
| --- | --- | --- |
| Graph control facts | State reducer | frontier、settlement、routing、lease、resource、恢复坐标和 revision |
| Graph input frame | Graph 初始输入准入 | 首个及后续节点输入、恢复 |
| Confirmed publication | 成功节点 output | 下游节点输入、最终输出、Event 内容 |
| Resume input frame | 已准入的恢复覆盖输入 | 重试/中断恢复后的实际参数 |
| Child boundary | 已确认子图输出 | 嵌套图输入输出衔接 |
| Node invocation binding | 编译后的 materialization + 当前 activation | 记录本次调用实际选择了哪些 frame value |
| Settlement result | execution 的 typed result | success/failure/interrupt Event 结果 |

“唯一快照”不等于把同一个值重复塞进一个大对象。Graph input、publication、resume input 和 child boundary 保存
canonical value；invocation 只保存参数名到这些 value 的 typed 坐标，settlement success 只引用已确认 publication，
不能再复制 output。这样 State 内部也只有一份值真相。

Event payload、pending/delivered、lease、attempt 和 transport error 不属于 Graph runtime，不进入 `GraphRunState`。

### 5.2 物理持久化规则

持久化 adapter 可以按查询和事务需要拆分 State、frame、invocation 和 result 表，但必须满足：

- 所有行带同一个 typed run/scope/revision snapshot coordinate；
- transaction 成功前，任何新版本都不能对 Graph 或 dispatcher 可见；
- loader 只有在所有组成部分版本一致时才返回该 `GraphRunState` 快照；
- 缺失、跨版本、错误 run/scope/activation 一律 fail closed；
- 不提供绕过 `GraphRunState` 版本独立更新 frame/result 的公共 Store；
- 不把任一物理表包装成第二个 state model、snapshot 或 reducer。

### 5.3 Event/outbox 记录

Events 投影只产生不可变引用：

```text
event_id
snapshot_coordinate(run_id, scope, settlement_revision)
activation_coordinate(superstep, node_id, execution_generation)
schema_version                 # 类型级固定常量，不是可写实例字段
```

`infra/persistence` 在写入 outbox 时为该引用增加 pending/delivered、领取 lease、attempt 和诊断等投递元数据；这些
字段归 persistence owner，不反向进入 Events projection 或 `GraphRunState`。

`snapshot_coordinate + activation_coordinate` 必须能在对应 snapshot revision 内唯一定位 invocation 和 settlement。
因此不再额外保存一套 `invocation_reference`、`result_reference`，也不在 outbox 中放 `Graph.Values`、
`NodeInputFrame`、NodeOutputFrame 或任意业务 DTO。

### 5.4 最终投递的 Event 内容

outbox 引用不是最终 wire payload。dispatcher 按引用读取唯一快照后，物化一条只用于传输的消息：

```text
NodeSettledEvent
├── event_id
├── node_id                 # 唯一节点名称，不另存 display name
├── input                   # 本次 invocation 的最终 NodeInputFrame
└── result                  # success | failure | interrupt typed variant
```

- `input` 保留 execution 已确认的本地参数名和编译顺序；events 不重新合并多个上游值。
- success 使用该 settlement 确认的 publication；failure 和 interrupt 使用同一 snapshot 中的 typed result。
- Python 内部使用封闭 typed variant，不使用字符串 discriminator；仅 wire schema 可以有显式版本/tag。
- codec、字节上限、redaction 和 secret policy 由 transport/persistence assembly 提供，Kernel 不用反射猜测业务 DTO。
- 无法读取完整快照、无法编码或违反安全策略时不得发送残缺 Event；由 dispatcher 保留可诊断的未投递状态。

wire payload 是从权威快照生成的传输副本，不是 Graph 的恢复数据源，也不是 Kernel 中第二份持久化真相。

### 5.5 身份和幂等

`event_id` 必须由稳定执行坐标确定性生成，至少覆盖：

```text
node-settlement schema identity + snapshot_coordinate + activation_coordinate
```

持久化层对 `event_id` 建唯一约束。同一个事务因确认丢失而被 adapter reconcile 或重试时，不能插入第二条 Event。
不同 execution generation 的重试 settlement 是不同事实，应产生不同 `event_id`。

引用值对象在构造边界拒绝空/非 canonical identity、可变 scope、负数坐标和 `bool` 伪整数；scope 只接受 tuple。
schema version 保持类型级固定常量，并与 event identity 共用同一个版本常量，避免每条记录自行填写或版本漂移。

## 6. Commit 契约的目标形状

### 6.1 Execution 仍只调用一个 Graph.Commit

Graph owner 仍以一个参数调用 commit，并只在 commit 返回 exact candidate 后推进：

```text
previous state + command
-> candidate GraphRunState 完整快照
-> Graph.Transition(previous + command + candidate)
-> Graph.Commit
-> exact candidate
```

Graph input、frame、invocation、publication 和 result 都属于 candidate snapshot，不能再通过 optional `result`、
`GraphCommitEvidence` 或其他 sidecar 传一份等价事实。若当前实现仍有 `GraphTransition.result`，由 State/execution
owner 收敛到 candidate snapshot；events 不读取旧字段做兼容 fallback。

### 6.2 EventingGraphCommit 只扩充同一个 commit 请求

目标调用链：

```python
commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(persistence_commit)
)
```

`EventingGraphCommit` 对 Graph 和外层 decorator 表现为普通 `Graph.Commit`。它只做：

```text
收到 Graph.Transition
-> 非 settlement：构造无 Event 的原子 commit 请求
-> settlement：根据 candidate snapshot 的稳定坐标构造 Event 引用
-> 调用内层 persistence commit 恰好一次
-> 原样返回 inner 结果
```

唯一 adapter 请求形状是：

```text
AtomicCommitRequest
├── transition              # 内含唯一 candidate GraphRunState
└── event_reference | None  # 只含 snapshot/activation 坐标
```

request 只是事务 envelope，不拥有 State、不复制 candidate，也不增加第二个 commit 入口。内层 persistence port 在一个
事务中写入完整 snapshot 的物理投影和 outbox。`EventingGraphCommit` 不先调用普通 State commit，再调用 Event port。

当前 `event_sink`、post-commit `await event_sink(event)` 和 `suppress(Exception)` 路径全部删除，不保留兼容模式。

### 6.3 Exact candidate 的所有权

- persistence adapter 必须按照同一事务的 CAS 结果返回 candidate；
- `EventingGraphCommit` 不自行确认 State，也不提供 `inner=None` fallback；
- outer logging 可以只读记录 exact/mismatch，但不能改写结果；
- execution owner 保留最终 exact-candidate 校验和内存 snapshot 安装。

若 snapshot 或 Event/outbox 任一写入失败，整个 transaction 回滚，inner 直接抛错，execution 不安装 candidate。

### 6.4 异步与不确定提交边界

- Graph 只等待本地 authoritative persistence transaction，不等待远端 Event transport。
- transaction 提交前取消必须不留下任何新版本；transaction 已交给存储后，adapter 必须按 candidate
  run/scope/revision reconcile，不能向 Graph 暴露“可能已提交”后再盲目重试。
- 数据库确认丢失时，adapter 读取 candidate revision 及对应 Event；两者完整且精确匹配才返回 exact candidate，
  两者都不存在才允许重试，半提交必须 fail closed。
- transaction 成功后发生的 transport、codec、lease 或远端 ack 失败，只影响 outbox 状态，不改变已确认的 Graph。
- `EventingGraphCommit` 不创建 task、queue 或 sink；取消和异常只来自它唯一调用的 persistence port。

## 7. 公共 API 与包结构

目标根包公共面仍为：

```python
from mote_kernel.events import EventingGraphCommit
```

不再从根包导出 sink、payload helper、EventBus 或 persistence 类型。建议实现结构：

```text
src/mote_kernel/events/
├── __init__.py     # 只导出 EventingGraphCommit
├── commit.py       # Graph.Commit -> atomic persistence request decorator
├── identity.py     # event_id 的确定性身份
├── record.py       # immutable Event/outbox 引用记录
└── projection.py   # settlement transition -> Event record | None
```

adapter-facing 的 immutable request/Protocol 是 Kernel 与 persistence 的 owner-internal SPI，不从 events 根包导出，
也不是第二个应用入口。它们放在最接近 `commit.py` 的明确模块中，不建立宽泛 `port.py`、`common`、`utils`、
`shared` 或 `helpers`。是否单独拆出文件以实际代码长度和所有权为准，不能为了包结构图机械增加空抽象。

删除或替换当前文件职责：

| 当前内容 | 处理 |
| --- | --- |
| `contract.py: EventSink` | 删除；Graph commit 内不再调用异步通知 sink |
| `contract.py: EventPayload` | 若无真实边界用途则删除，不保留 nominal 空基类 |
| `graph.py` 中复制 outcome 的 Event payload | 删除；最终 payload 由 dispatcher 从 snapshot 物化 |
| `projection.py` | 改为从 candidate snapshot 投影稳定 Event/outbox 引用 |
| `commit.py` post-commit sink | 改为一次性调用 atomic persistence port |

## 8. 分阶段实施

### 阶段 0：同步设计文档并删除旧语义

任务：

- 将 [`events-design.zh-CN.md`](./events-design.zh-CN.md) 改为本计划的原子 commit + 引用模型；
- 删除“inner 返回后通知 sink”“普通 sink 异常隔离”“本期不做 outbox”等已被推翻的描述；
- 固定 events 内层、logging 外层的唯一组装示例；
- 明确 Event 引用 `GraphRunState` snapshot，不保存参数/结果副本；
- 将旧评审文件保留为历史审查记录；`events-implementation-plan-review.zh-CN.md` 已被需求方驳回，不能作为实现
  基线，也不能借此恢复可丢失旁路。

验收：

- 设计、实施计划和代码注释只描述一条 commit 路径；
- 全仓文档不再展示 `EventingGraphCommit(event_sink)(persistence_commit)`；
- 不再出现 events best-effort、post-commit sink 或允许丢 Event 的正式契约。

### 阶段 1：确认 GraphRunState 快照完整性

本阶段由 State/execution owner 完成，是 Events 的外部前置依赖。Events 不修改 Graph 调度，也不能用自己的缓存补齐
缺失事实。若现有实现已经满足以下契约，只需冻结 typed contract 和测试；若未满足，由对应 owner 补齐。

任务：

- StartGraphRun candidate snapshot 包含已准入 Graph input frame；
- ResumeGraphNodes candidate snapshot 包含已准入 resume input/substitution；
- ClaimGraphExecution candidate snapshot 包含本次 invocation 的稳定输入 binding，不复制参数值；
- SettleGraphNode candidate snapshot 包含 typed result，并在 success 时包含唯一 confirmed publication；
- child graph start/boundary 和 nested settlement 归入同一 `GraphRunState` snapshot 路径；
- `Graph.Transition` 只携带 previous、command 和完整 candidate，不再携带等价 result/evidence sidecar；
- persistence 返回 exact candidate 前不替换 Python 内存 State 或 frame view；
- 删除任何只存在于 continuation、却无法从 authoritative snapshot 恢复的隐式事实。

必须保持：

- Event 投递状态不改变 reducer 输入或 `GraphRunState` schema；
- 不建立第二 reducer、第二 State、第二 snapshot 或公开 runner；
- execution 不 import `mote_kernel.events`；
- `Graph` facade 仍是唯一公开执行入口。

验收测试：

- fresh root input 与 StartGraphRun 使用同一 snapshot commit；
- child root input 使用相同规则；
- resume override 和 skip substitution 与 ResumeGraphNodes 原子提交；
- claim snapshot 中的 binding 能还原本次最终 `NodeInputFrame`；
- failure retry 的新 generation 不复用旧 invocation identity；
- success publication 只有 settlement commit exact 确认后才进入内存 frame view；
- durable `GraphRunState` 完整快照可以在新 Graph 实例中恢复，不依赖不可序列化 continuation；
- snapshot 任一物理组成缺失、跨版本或 scope 错误时，在节点再次执行前 fail closed。

### 阶段 2：重做 Events 契约和 commit decorator

任务：

- 定义 deterministic `event_id` 和 immutable Event/outbox reference；
- `project_event()` 只对 `SettleGraphNode` 产生一条引用记录；
- 引用只保存 snapshot/activation 坐标，并能定位同一次 invocation 和 typed settlement result；
- 将 `EventingGraphCommit` 改为包裹 atomic persistence port，并返回普通 `Graph.Commit`；
- 对每个 transition 调用 inner 恰好一次；
- 非 settlement transition 使用 `None` event reference，仍走同一个 inner；
- 删除 `EventSink`、post-commit await、异常 suppress 和后台通知语义；
- 保持 decorator 无 run-local mutable state，可安全复用于并发 run 和嵌套 scope。

验收测试：

- 根包只导出 `EventingGraphCommit`；
- 每个 success/failure/interrupt settlement 各产生一条 Event 引用；
- claim/fence/frontier/complete/resume/skip 不产生节点 settlement Event；
- Event 引用中不存在业务参数、output 或 DTO 副本；
- event_id 在相同 transition 重投影时稳定，不同 generation/revision 时不同；
- inner 对每个 transition 恰好调用一次，返回值、普通异常和取消对象原样传播；
- decorator 不创建后台 task、队列、registry 或第二 commit；
- 并发测试只验证身份配对，不锁定跨节点总顺序；
- nested scope、child run 和 recovery generation 均能唯一定位 `GraphRunState` snapshot 中的 settlement。

### 阶段 3：扩展唯一持久化 adapter 的原子写集

本阶段由 persistence owner 实现。Kernel 只定义 typed commit request，不实现数据库或 transport。

任务：

- 在现有 State CAS transaction 中写入完整 `GraphRunState` 物理投影和 Event/outbox append；
- 为 State、frame/invocation/result 和 Event 使用同一 run/scope/revision coordinate；
- 为 `event_id` 建唯一约束；
- 事务失败时回滚全部写入；
- adapter 只有在事务成功后才返回 exact candidate；
- 提供按 snapshot coordinate 加载完整同版本快照的 typed 查询；
- 对确认丢失使用 candidate coordinate reconcile，不盲目重放 stale CAS；
- 定义 pending Event 所引用 snapshot revision 的 retention 规则；
- 不新增独立的“Event 持久化调用”供 Kernel 顺序调用。

故障注入测试：

- State CAS 前失败：snapshot/Event 均不存在；
- State control row 写入后、frame/result 行写入失败：整个事务回滚；
- snapshot 物理行写完后、Event append 失败：整个事务回滚；
- 事务提交确认丢失：恢复后只有一个 candidate 版本和一个 event_id；
- stale revision/lost CAS：不留下孤立 snapshot 组成或 Event；
- Event 引用的 snapshot 缺失或版本不一致：加载 fail closed；
- pending Event 存在时 GC 不删除被引用 snapshot revision。

### 阶段 4：实现 persistence/outbox dispatcher

dispatcher 不属于 Graph 执行引擎，也不放入 `mote_kernel.events`。它复用 persistence owner 的 outbox 能力。

任务：

- 领取 pending Event，避免同一 worker 内无界并发；
- 按 Event 引用加载完整 `GraphRunState` snapshot，再定位 invocation 输入和 settlement result；
- 根据固定 schema version 组装“节点名称、实际参数、执行结果”；
- 通过注入的 transport adapter 发送；
- 收到远端确认后标记 delivered；
- 普通发送失败保留 pending 并按外部策略重试；
- 取消、进程退出和 lease 过期后允许其他 worker 接管；
- 使用同一 `event_id` 重发，要求消费者幂等。

dispatcher 必须区分可重试 transport 失败和不可重试 materialization/codec/security 失败；具体 backoff、最大次数、
quarantine 或人工处理策略由 persistence/runtime owner 配置。无论采用什么状态名，都不能删除未成功交付的 Event，
也不能让毒消息阻塞其他 Event。

不在本阶段加入 Kernel EventBus、Graph node、全局订阅 registry 或隐藏线程。dispatcher 的 retry/backoff、
batch 和 lease 属于 persistence/runtime 配置，不写进 `GraphRunState`。

验收测试：

- commit 后、首次发送前崩溃：重启后能发送完整 Event；
- 发送成功、标记 delivered 前崩溃：重启后以同一 event_id 重发；
- 输入由多个 publication 组成时，参数名和值与原 invocation 一致；
- resume override、失败、interrupt 和 nested graph 均能组装正确内容；
- schema/codec 不支持某值时显式失败并保留可诊断状态，不删除 Event；
- transport 普通失败可重试，永久 materialization 错误不会形成无界热循环；
- delivered 后不再正常领取；
- 无全局顺序假设，不同 run/scope 可以并发投递。

### 阶段 5：装饰器链与 assembly

唯一组装顺序：

```python
persistence_commit = build_persistence_commit(...)

commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(persistence_commit)
)

result = await graph.run(values, commit=commit)
```

任务：

- Role/Flow assembly 只在 atomic persistence capability 完整时安装 `EventingGraphCommit`；
- 不接受只有 event transport、没有 atomic commit 的半套配置；
- logging 外层记录整个 atomic commit 的 started/accepted/failed/cancelled；
- logging 不读取或修改 Event 记录；
- events 不发现 logging，也不验证外层顺序；
- 缺少持久化 capability 时，不宣称支持可靠 Event；是否允许纯进程内 Graph run 仍由 execution owner 决定。

组合测试：

- 实际顺序为 logging-before -> event projection -> one persistence transaction -> logging-after；
- persistence 异常和取消穿过两层 decorator 保持同一对象；
- outer decorator 不导致 inner 重复调用；
- exact mismatch 由 execution owner 最终拒绝；
- 非 settlement transition 仍由 logging 观察，但不新增 Event。

### 阶段 6：删除旧路径并完成交付

任务：

- 删除旧 EventSink、RecordingSink 风格测试及 post-commit 行为说明；
- 不保留兼容构造重载、别名或 feature flag 双路径；
- 更新 README、logging/observability 组合示例及所有 Events 文档；
- 将 Kernel、persistence adapter 和 dispatcher 的 conformance 用例按 owner 放置；
- 只有 durable/wire protocol 实际影响到的 adapter 和语言 runner 才需要同步；不机械要求无关语言实现空 runner；
- intended files 纳入版本控制并做 clean checkout 验证；
- 运行定向和全仓门禁，不通过上调 ratchet 或静默白名单绕过问题。

## 9. 测试矩阵

| 维度 | 必须证明的行为 |
| --- | --- |
| 单一真相 | `GraphRunState` 是唯一逻辑快照；Event/outbox 没有 input/output/DTO 副本 |
| Start 恢复 | Graph input 与 Start snapshot 同 commit，可在新进程加载 |
| Claim 恢复 | claim 后崩溃可用相同来源还原节点参数 |
| Resume 恢复 | override/resume input 只在同一 snapshot 中保存一份，Event 引用该版本 |
| Settlement 原子性 | 完整 snapshot 与 Event 全部成功或全部回滚 |
| 成功事件 | 引用能组装实际参数和该 settlement 的 output |
| 失败事件 | 引用能组装实际参数和 failure |
| 中断事件 | 引用能组装实际参数、interrupt id 和 request payload |
| 未处理异常 | 无伪造 settlement Event，旧 attempt 按 execution fence 处理 |
| 幂等 | commit 重试不重复 Event；发送重试复用 event_id |
| 并发 | 身份和值一一对应，不要求全局顺序 |
| 嵌套 | parent/child run、scope、boundary 和结果引用不串线 |
| 留存 | pending Event 引用的 snapshot revision 不被 GC |
| 链式组合 | logging 外层、events 内层、persistence 最内层，各调用一次 |
| 公共面 | 根包只导出 EventingGraphCommit，无 EventBus/sink/manager |

## 10. 明确禁止的实现方式

- 先调用 State commit，再单独调用 event sink；
- 先发送远端 Event，再提交 State；
- 在 Event/outbox 中复制一份节点参数或 output，形成两个可独立变化的数据源；
- 从“最新 State”反推历史参数，而不读取 Event 固定引用的 snapshot revision；
- 用 Event 作为 Graph 恢复输入；
- 在 `GraphRunState` 中加入 pending/delivered 投递状态；
- 在 `Graph.Transition` 旁增加 `GraphCommitEvidence`、optional result 或其他等价 snapshot sidecar；
- 允许 frame/result 绕过 `GraphRunState` 版本单独提交或成为独立权威读取结果；
- 使用 ContextVar、全局 dict、进程内 registry 或 decorator mutable cache 暂存参数；
- 为 Event 新建第二个 Graph、runner、scheduler、reducer 或 Store 调用链；
- 在 Graph commit 事务中直接等待 Kafka/HTTP/Webhook；
- 声称跨网络 exactly-once 或依赖全局事件顺序；
- 使用 `Any`、裸字典、反射、字符串 discriminator 或宽泛共享工具包；
- 为兼容当前未交付实现保留 post-commit sink 双路径；
- 在 Events 包内保留任何“允许丢失”的 sink、subscriber 或旁路。

## 11. 验证命令

Kernel 定向检查至少包括：

```text
python -B -m pytest tests/events tests/execution -q --tb=short -p no:cacheprovider
python -B -m pyright src/mote_kernel/events src/mote_kernel/execution
python -B -m ruff check src/mote_kernel/events src/mote_kernel/execution tests/events tests/execution
python -B -m ruff format --check src/mote_kernel/events src/mote_kernel/execution tests/events tests/execution
```

持久化 adapter 必须单独运行其 transaction/CAS/fault-injection/conformance 测试；dispatcher 必须运行投递重放、
ack 丢失、retention 和并发领取测试。

交付前执行：

```text
make check
make complexity
pre-commit run --all-files
```

复杂度门禁继续作为高召回审查参考。最终判断以单一 owner、单一 commit、故障恢复正确性、代码简洁和无重复事实
为准；不得为了降低指标引入抽象层，也不得为了通过指标放宽 ratchet。

## 12. 完成定义

### 12.1 Kernel Events 切口完成

- `GraphRunState` 完整快照契约已由 State/execution owner 提供，Events 不维护任何 sidecar 状态；
- `EventingGraphCommit` 只从 settlement snapshot 投影稳定引用，并调用 persistence port 恰好一次；
- 根包唯一公共入口是 `EventingGraphCommit`，可被 logging 等 decorator 包裹；
- Event/outbox reference 不复制节点参数和结果；
- 旧 post-commit sink、可丢失旁路和兼容双轨已彻底删除；
- events 不拥有 transport、retry、Store、Graph、State、runner 或后台任务；
- Events 和组合定向测试、typing、lint、format 门禁通过。

### 12.2 可靠投递 vertical slice 完成

- candidate `GraphRunState` 完整 snapshot 和 Event/outbox 由一个 persistence transaction 原子提交；
- Graph 可以从 durable snapshot 在新进程恢复，不依赖进程内 continuation；
- dispatcher 能通过稳定坐标组装节点名称、实际参数和结果；
- commit 崩溃窗口不会产生半个 snapshot 或孤立 Event；
- 发送崩溃窗口通过同一 `event_id` 安全重放；
- pending Event 引用的 snapshot、schema 和 codec 在交付前不会被 GC；
- 受影响 persistence adapter、dispatcher、conformance runner 和故障注入测试通过；
- `make check` 和根级 pre-commit 通过；无关既有失败如仍存在，则有隔离且可审计的说明。
