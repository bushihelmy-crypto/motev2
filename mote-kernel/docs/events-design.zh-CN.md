# Events 设计说明

本文定义 `mote_kernel.events` 的最终边界：节点 settlement 与唯一 `GraphRunState` 快照原子写入 outbox，随后由
persistence/runtime 可靠投递。Events 不提供可丢失通知模式，也不建立第二套状态或执行基础设施。

## 1. 核心模型

```text
Graph.Transition
      |
      v
EventingGraphCommit
      |
      | AtomicCommitRequest(transition, event_reference | None)
      v
唯一 persistence transaction
      ├── candidate GraphRunState 完整快照
      `── pending outbox record
                  |
                  v
             dispatcher
                  |
                  `── at-least-once transport
```

必须同时成立：

- `GraphRunState` 是唯一 Graph 运行时状态和唯一逻辑快照 owner；
- 每个已确认的 `SettleGraphNode` 恰好对应一条 outbox Event；
- snapshot 与 Event 要么一起提交，要么一起回滚；
- Event 不复制节点参数和结果，只引用产生该 settlement 的 snapshot revision；
- Graph 不等待远端发送，dispatcher 失败不改变已经确认的 Graph；
- 没有 post-commit sink、EventBus、内存队列、隐藏 task 或兼容双路径。

## 2. Owner 边界

| Owner | 负责 | 不负责 |
| --- | --- | --- |
| State / execution | 产生完整 candidate `GraphRunState`，通过唯一 `Graph.Transition` 调用 commit | Event、Store、outbox、transport |
| `mote_kernel.events` | 纯投影 settlement 引用；把 atomic persistence port 包成普通 `Graph.Commit` | State、事务实现、恢复、发送、重试 |
| `mote-infra/persistence` | snapshot 与 outbox 的原子事务、CAS、历史 revision 读取和留存 | Graph 调度、Event 业务语义 |
| dispatcher / runtime | 领取 outbox、读取 snapshot、组装消息、发送和确认 | Graph commit、State 推进 |
| logging / observability | 独立的诊断旁路 | durable Event 语义 |

`execution` 不 import `events`。装饰器只在 assembly 注入。若 persistence 不具备 atomic outbox capability，assembly
不得安装 Events，也不能退化成“先提交 State，再通知 sink”。

## 3. 唯一真相

Graph input、publication、resume input、child boundary、invocation binding 和 settlement result 都属于同一个
`GraphRunState` 逻辑快照：

```text
GraphRunState @ run_id / scope / revision
├── control facts
├── canonical frame values
├── invocation bindings
└── settlement result
```

持久化层可以将这些内容规范化到不同表，但所有物理记录必须共享同一个 typed snapshot coordinate，在同一个事务中
可见，并由 loader 完成版本一致性校验。物理拆表不能产生第二个 State、第二个 reducer 或可独立更新的 evidence Store。

invocation 只保存参数名到 canonical frame value 的 typed binding；success settlement 只引用唯一 confirmed
publication。不能在 State 内部再复制一份 effective input/output，也不能把副本塞进 Event。

## 4. Event 引用与最终消息

### 4.1 Outbox 引用

`NodeSettlementEventReference` 只保存定位信息：

```text
run_id
scope
superstep
node_id
execution_generation
settlement_revision
schema_version           # 类型级固定常量，不是每条记录的可写字段
event_id                 # 只读属性；由以上 settlement 坐标确定性生成
```

它不包含 input、output、failure、interrupt payload、routing 或任意业务 DTO。类型本身表示 node settlement，不再增加
字符串 `event_kind` discriminator。

`event_id` 使用带 schema identity 的长度前缀编码。相同 settlement 重试投影得到同一 ID；不同 scope、node、
generation 或 revision 得到不同 ID。persistence 对 `event_id` 建唯一约束，dispatcher 重试始终复用该 ID。

引用构造是严格边界：`run_id`、`node_id` 和 scope 各段必须是 canonical identity，scope 必须是不可变 tuple，
三个数值坐标拒绝负数和 `bool` 伪整数。`schema_version` 是引用类型级固定常量，并与 event identity 使用同一版本
来源；它不是可由调用者填写的业务字段。

### 4.2 Wire Event

dispatcher 根据引用读取对应的完整 snapshot，再物化最终消息：

```text
NodeSettledEvent
├── event_id
├── node_id
├── input       # invocation 最终实际访问的 NodeInputFrame
└── result      # success | failure | interrupt typed variant
```

wire payload 是传输投影，不是 Graph 恢复数据源。codec、schema evolution、大小限制、redaction 和 secret policy 属于
persistence/transport assembly；Kernel 不使用反射猜业务 DTO。读取不完整、版本不匹配或无法安全编码时不得发送残缺
消息，outbox 必须保留可诊断的未投递状态。

## 5. 唯一 Commit 路径

execution 仍只认识普通单参数 `Graph.Commit`：

```text
previous + command
-> candidate GraphRunState
-> Graph.Transition
-> Graph.Commit(transition)
-> exact candidate
```

`EventingGraphCommit` 将注入的 atomic persistence port 适配成这个形状：

```text
收到 transition
-> project_event(transition)
-> AtomicCommitRequest(transition, event_reference | None)
-> await persistence(request)        # 恰好一次
-> 原样返回 persistence 结果
```

规则：

- 只有 `SettleGraphNode` 投影一条引用；其他 command 使用 `None`，仍走同一个 persistence port；
- `AtomicCommitRequest` 只引用原 transition，不复制 candidate 或 result；
- projection 失败时 persistence 尚未调用；
- persistence 的普通异常和取消原样传播，Events 不捕获、不重试；
- persistence 返回值原样交回 execution，exact-candidate 校验仍由 Graph owner 完成；
- Events 不先调用 State commit，也不在返回后调用另一个 Event port；
- Events 不创建 transport、dispatcher、task、queue、registry 或 run-local mutable state。

本地 transaction 必须在 `Graph.Commit` 返回前完成。远端 transport 必须在 transaction 返回后由 dispatcher 执行，
不能占用 Graph commit 调用链。

## 6. 故障与恢复语义

| 故障点 | 必须发生的结果 |
| --- | --- |
| snapshot 写入前失败 | snapshot/Event 都不存在 |
| snapshot 某物理组成写入失败 | 整个 transaction 回滚 |
| outbox append 失败 | 整个 transaction 回滚，Graph 不安装 candidate |
| transaction 确认丢失 | persistence 按 candidate coordinate reconcile，不盲目重试 stale CAS |
| commit 后、发送前崩溃 | pending Event 在重启后继续投递 |
| 发送成功、确认前崩溃 | 以同一 `event_id` 重发 |
| transport 普通失败 | 保留未投递状态，按外部策略重试 |
| snapshot/codec/security 永久错误 | 不发送残缺消息，不让毒消息形成无界热循环 |

投递语义是 at-least-once，不宣称跨网络 exactly-once；消费方按 `event_id` 幂等。pending Event 引用的 snapshot
revision、schema 和 codec 在完成投递或进入明确终止状态前不能被 GC。

没有形成 `SettleGraphNode` 的未处理异常不能由 Events 伪造成 settlement Event。如果产品要求每次异常 attempt 也成为
durable Event，必须先由 State/execution owner 定义对应的 typed 状态事实和 transition，再沿同一原子路径投影。

## 7. 公共 API 与装饰器链

根包唯一公共入口：

```python
from mote_kernel.events import EventingGraphCommit

eventing_commit = EventingGraphCommit(persistence_commit)
commit = OtherCommitDecorator(other_config)(eventing_commit)
result = await graph.run(values, commit=commit)
```

`persistence_commit` 接收 owner-internal `AtomicCommitRequest`；`eventing_commit` 对外是普通 `Graph.Commit`，因此可被
logging 等其他 decorator 包裹。adapter-facing request/reference 类型不从 events 根包导出，也不是第二个应用入口。

包结构：

```text
src/mote_kernel/events/
├── __init__.py     # 只导出 EventingGraphCommit
├── commit.py       # AtomicCommitRequest 与 Graph.Commit adapter
├── identity.py     # 确定性 event_id
├── record.py       # immutable settlement reference
└── projection.py   # settlement transition -> reference | None
```

不增加 `contract.py` 空基类、generic Event payload、sink、manager、registry、`utils`、`common`、`shared` 或
`helpers`。

## 8. 测试与审核点

Kernel Events 测试必须覆盖：

- 根包只暴露 `EventingGraphCommit`；
- success、failure、interrupt 都产生一条同形引用；
- 非 settlement、未确认异常和 resume control 不伪造 Event；
- request/reference 不可变且不含业务值副本；
- 相同 settlement 的 `event_id` 稳定，不同 generation/revision 不同；
- projection 失败时 persistence 未调用；
- persistence 每次 wrapper invocation 恰好调用一次，返回值、异常和取消不被改写；
- Graph 等待本地原子 transaction；
- exact mismatch 最终由 Graph owner 拒绝；
- 并发节点只验证引用与 transition 配对，不锁定全局顺序；
- nested scope/child run、并发 run 和 interrupt recovery 不串线；
- 外层 decorator 的 before/after 完整包住 Events 与 persistence。

persistence/dispatcher owner 另行覆盖真实 transaction rollback、ack-lost reconcile、唯一约束、lease takeover、重放、
retention、codec/security 和毒消息隔离。Kernel 的 fake port 测试不能冒充这些基础设施测试。

审核实现时只问核心逻辑是否直接：一个 snapshot、一个 transaction request、一个 persistence 调用、一条 outbox
记录。复杂度门禁用于高召回发现风险，不能为了数字增加抽象，也不能用放宽门禁掩盖重复责任。
