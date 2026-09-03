# Events 设计评审（按需求澄清修订）

状态：**P0 阻断，暂不批准当前实现作为最终基线**

审查日期：2026-09-02

审查对象：[`docs/events-design.zh-CN.md`](./events-design.zh-CN.md)

本文件记录设计、实现边界和门禁评审。本轮只修改本评审文件，未修改 events 设计、生产代码或测试。

## 1. 先说结论

整体方向没有问题：events 复用 Graph 已有的 commit 接缝，做不可变事件投影，不建立第二个 Graph、runner、reducer、
State 或 EventBus。

按需求方已经明确的范围，以下三点**不是 events 层的阻断**：

- 本期不实现持久化，也不要求 events 证明数据已经落盘；
- 装配顺序已经确定为 **events 在内层、logging 在外层**，顺序由组装方负责，不由 events 重排；
- 并发节点的事件不要求全局顺序，节点并行时消费方按身份关联即可。

当前仍不能通过的重点只有三类：

1. async sink/inner 的错误配置可能通过装配，并在运行时被当成普通旁路错误静默处理；
2. sink 失败后的行为需要作出明确、可测试的决定，不能同时暗示“必达”和“失败隔离”；
3. 需求要求的事件内容是“节点名称–访问参数–执行结果”，而当前事件模型没有访问参数，也没有成功执行结果。

此外还有两项必须收尾的文档/交付问题：一处时序文字矛盾，以及全仓门禁和版本控制状态尚未完善。

| 项目 | 当前判定 |
| --- | --- |
| 持久化实现 | **本期范围外，不作为阻断**；只需避免文档把 callback 写成 durability |
| events/logging 包装顺序 | **已确认**：events 内层、logging 外层；不由 events 实现顺序重排 |
| async sink/inner 装配校验 | **P0，必须先闭合** |
| sink 普通异常、取消和丢通知 | **P1，必须研究并冻结策略** |
| 事件内容与需求不一致 | **P0/P1，必须先定数据来源和 typed schema** |
| 并发事件总序 | **不要求，不作为问题** |
| 文档时序矛盾 | **P2，要求修改** |
| 全仓门禁、tracked/clean checkout | **交付前完善** |

## 2. 评审基线

本次按项目已有原则判定：

- [`architecture.zh-CN.md`](./architecture.zh-CN.md#L10-L22) 规定 `GraphRunState` 是唯一状态模型，
  `execution.Graph` 是唯一执行门面；`Graph.Commit` 是 callback 提交边界，本期不附带 Store/durability 承诺。
- [`execution-state-frontier-call-chain.zh-CN.md`](./execution-state-frontier-call-chain.zh-CN.md#L278-L304)
  规定 candidate 由唯一 `commit_transition()` 生成和确认，外部 callback 必须返回 exact candidate。
- [`AGENTS.md`](../AGENTS.md) 要求复用已有基础设施、不建第二执行路径、不留兼容别名或隐藏状态，并保持严格类型和
  完整门禁。
- `GraphTask`/`ExecutableTask` 在执行阶段才同时拥有节点身份和有效输入
  （[`execution/engine/task.py:17-33`](../src/mote_kernel/execution/engine/task.py#L17-L33)）；节点结果在 scheduler
  投影为 `TaskResult`（[`execution/engine/scheduler.py:41-80`](../src/mote_kernel/execution/engine/scheduler.py#L41-L80)）。
  这决定了“访问参数–执行结果”不能靠 commit wrapper 从 State 猜出来。
- 当前实现和测试：
  [`events/commit.py`](../src/mote_kernel/events/commit.py)、
  [`events/graph.py`](../src/mote_kernel/events/graph.py)、
  [`events/projection.py`](../src/mote_kernel/events/projection.py)、
  [`tests/events/test_events.py`](../tests/events/test_events.py)。

## 3. 已通过、应保留的部分

以下设计与“零重复责任、复用基础设施、唯一真相、代码简洁”一致：

- 根包只导出 `EventingGraphCommit`；事件值、协议和投影留在职责明确的子模块；
- `project_event()` 是纯函数，只识别 `SettleGraphNode`，不写 State、不启动任务、不维护全局计数器；
- 一个 settlement 至多投影一条事件，未处理异常、claim、fence、frontier advance、complete 和 skip 不伪造节点事件；
- outcome 使用 frozen/slots typed value，不复制整份 `GraphRunState`；
- 投影发生在 inner callback 前，inner 失败时不通知 sink；
- sink 使用显式 `await`，没有后台任务、事件总线或隐式队列；
- child `run_id`、scope 和 revision 从 execution transition 读取，装饰器实例不持有当前 run 的可变状态；
- execution 没有反向依赖 events，也没有第二 runner/reducer/Store。

这些部分可以作为修订后的保留基线，但不代表当前事件载荷和 async 错误边界已经完成。

## 4. 已确认的范围和装配决定

### 4.1 本期不做持久化

本期目标是事件接缝和事件内容，不实现 Store、落盘、outbox 或 durability。`Graph.Commit` 只表示调用方提供的
callback；没有 callback 的进程内执行仍由 execution owner 处理。

因此“没有持久化实现”本身不扣分，也不要求 events 现在增加 persistence adapter。需要改的只是术语：
`events-design.zh-CN.md` 中“持久化 port 接受”“真实 persistence commit”等说法会让读者误以为本期已经有 durable
保证，应统一称为“注入的 `Graph.Commit` callback 返回 exact candidate”。这是文档准确性要求，不是本期新增功能。

### 4.2 固定 events 内层、logging 外层

最终装配约定为：

```python
commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(event_sink)(commit_callback)
)
```

events 只负责自己的 projection → inner callback → notification 路径，不负责发现、重排或验证 logging 的装配位置。
logging 是否透明地保留 callback 返回值、异常和取消，由 logging owner 负责；这不是 events 层的第二条执行路径。

此前把“必须让 Eventing 最外层”列为 events 阻断不适用于这个已确定的 assembly contract，本版不再这样判定。

### 4.3 不要求并发事件总序

并行节点可以按完成先后产生事件，跨 run、跨 scope 的到达顺序不属于事件契约。消费方使用 `run_id`、scope、node
identity 及调用坐标关联事件，不依赖一个全局 ordinal。

`revision` 仍表示 Graph candidate 的状态版本；它不是事件排序号。测试可以验证每条事件和对应 settlement 的身份、
revision、输入/结果配对，但不应把跨并发事件的排列当成必须稳定的行为。

## 5. P0：async sink/inner 的装配和运行时边界没有闭合

### 5.1 现状

`EventingGraphCommit` 当前只检查 `callable()`：

- sink：[`commit.py:56-60`](../src/mote_kernel/events/commit.py#L56-L60)；
- inner：[`commit.py:62-69`](../src/mote_kernel/events/commit.py#L62-L69)。

类型协议虽然声明 sink 是 `async __call__`（[`contract.py:20-27`](../src/mote_kernel/events/contract.py#L20-L27)），
但 Python 的 Protocol 不会在运行时阻止调用方传入同步函数、错误参数数量的 callable 或返回普通值的 wrapper。

当前后果不一致：

- 同步 sink 在 `await self.event_sink(event)` 处产生 `TypeError`；该错误位于
  `suppress(Exception)` 内，可能被当作普通通知失败直接吞掉；
- 同步或错误 arity 的 inner 要到真正执行时才失败；
- 同一个错误配置，在 sink 和 inner 上得到不同的可见行为，调用方无法判断是装配错误还是业务通知失败。

### 5.2 为什么不能只写一句“加 inspect 就行”

要求“装配时验证 async、参数数量、返回类型”看似简单，但对任意 Python callable 并不等价：

- async function、绑定方法、`partial`、可调用对象和返回 coroutine 的同步 wrapper 的形状不同；
- `iscoroutinefunction` 或签名检查不能证明实际调用一定返回 awaitable；
- 不调用依赖就无法观察动态返回值，调用依赖又会产生副作用；
- 项目规则禁止在内部边界引入泛化反射/动态探测来掩盖 owner。

所以真正需要闭合的是**契约分层和错误可见性**，而不是机械增加一个反射 helper。

### 5.3 必须冻结的最小契约

建议按三层实现，具体代码方案由 events owner 选择，但不能继续保持现在的模糊状态：

1. **静态层**：`Graph.Commit` 和 `EventSink` 的严格类型必须表达“一参数、返回 awaitable”；增加正向 pyright fixture，
   防止正常 typed assembly 推断出 `Unknown`。
2. **装配层**：非 callable 的 sink/inner 必须立即失败；如果产品要求任意动态 callable 也在装配时被拒绝，采用
   明确的 nominal async adapter/注册契约，而不是偷偷扫描所有对象。
3. **调用层**：对动态对象无法在装配时证明的部分，第一次调用时必须把“返回非 awaitable/错误调用形状”识别为
   `EventContractError` 或稳定的 `TypeError`，不能落入普通 sink 异常的静默隔离分支。

其中，合法 sink 自己抛出的普通业务异常是否隔离，属于下一节的通知策略；“配置不是 async callable”不能伪装成
“sink 暂时不可用”。

### 5.4 必须补的测试矩阵

至少覆盖：

- 非 callable sink、非 callable inner：装配立即失败；
- 一参数 `async def`、async callable object、绑定方法和 `partial`：行为一致；
- 同步函数、错误 arity、返回 `None`/普通值：按契约错误处理，不静默成功；
- 合法 inner 恰好调用一次，返回值和异常对象保持原样；
- sink 合法但运行时普通异常、显式取消分别走下一节定义的策略；
- `Graph.Commit` 链与 logging 外层组合不改变上述边界。

在这个问题关闭前，不能说“required async capability 已在 assembly fail fast”，这是当前最明确的技术阻断。

## 6. P1：sink 失败语义需要认真定案

### 6.1 先分清三种失败

事件通知不是 primary commit。调用链中的失败来源必须分开：

| 失败来源 | State/inner 状态 | sink 是否调用 | 应否向上抛 |
| --- | --- | --- | --- |
| projection/事件契约错误 | inner 尚未调用 | 否 | 是，暴露设计/数据错误 |
| inner callback 普通异常或取消 | 可能未确认，也可能由 adapter 自行处理 | 否 | 是，保持原异常/取消 |
| sink 普通异常 | inner 已返回；可能已经确认 State | 是但通知失败 | 需要冻结策略 |
| sink `CancelledError` | inner 可能已确认 | 进入通知阶段后取消 | 原则上保留取消，不能假装回滚 |

### 6.2 当前实现的取舍和风险

当前代码用 `suppress(Exception)` 隔离 sink 普通异常（[`commit.py:33-40`](../src/mote_kernel/events/commit.py#L33-L40)）。
这有一个明确好处：不会因为通知失败再次提交同一个 transition，也不会让 events 变成第二个 State owner。但代价是：

- 通知可能永久丢失；
- 调用方没有失败信号；
- 没有 retry、replay、幂等键、超时或 ack-lost reconcile；
- 如果改成 fail-closed，调用方可能在 State 已经确认后重试 commit，反而产生重复提交风险。

因此“吞掉异常”不能同时被描述成可靠发送；它只能表示 **best-effort、非权威、允许丢失的通知**。

### 6.3 当前范围的推荐决策

在本期不做 persistence/outbox 的前提下，建议明确采用以下最小策略：

- 合法 sink 的普通 `Exception`：隔离并返回已确认的 inner 结果，不重试 inner；事件允许丢失；
- `asyncio.CancelledError`：继续向上抛出，因为它发生在真实 await 点，调用方必须知道任务被取消；同时文档说明
  inner 可能已经成功，不能假设回滚；
- `KeyboardInterrupt`、`SystemExit` 等不属于普通旁路故障，不应被吞掉；
- 不在 events 内加入后台队列、隐式重试、全局错误 registry 或“伪造成功”状态；
- 需要可靠送达时，另建明确的 Persistence/outbox adapter 契约，由那个 owner 负责原子性、重试、重放和幂等。

这不是把可靠性问题忽略掉，而是把“当前 best-effort 选择”和“未来可靠投递 owner”分开。若产品不接受事件丢失，
则当前 API 不能直接宣称可用，必须先提供外部 durable/outbox 能力，不能只改 `suppress` 的一行代码。

### 6.4 必须补的验证

- sink 普通异常不会再次调用 inner，且 primary 结果保持不变；
- sink 取消的异常对象保持 identity，调用方能观察到取消，且测试记录 inner 可能已完成；
- sink 返回非 awaitable 属于契约错误，不被普通异常隔离吞掉；
- 没有后台 task、无限等待或隐式 retry；
- 文档和示例不出现 exactly-once、必达、durable event 等超出范围的说法。

## 7. P0/P1：当前事件内容没有实现需求

### 7.1 需求与现状的差异

需求已经明确：事件核心内容是

```text
节点名称 - 访问参数 - 执行结果
```

但当前 `NodeSettledEvent` 只有：

```text
run_id - scope - node_id - revision - outcome
```

见 [`events/graph.py:71-89`](../src/mote_kernel/events/graph.py#L71-L89)。具体缺口是：

- `node_id` 可以作为节点名称，但没有节点本次调用的访问参数/有效输入；
- `NodeSucceeded` 只保留 routing，没有成功节点的 output/result
  （[`graph.py:32-43`](../src/mote_kernel/events/graph.py#L32-L43)）；
- `project_event()` 只读取 `transition.command` 和 candidate metadata，完全没有使用 `transition.result`
  （[`projection.py:41-56`](../src/mote_kernel/events/projection.py#L41-L56)）；
- 设计文档还明确写“不复制节点输入输出”（[`events-design.zh-CN.md:57-61`](./events-design.zh-CN.md#L57-L61)），
  与当前需求正好相反。

这不是“再加几个字段”的小问题：commit decorator 收到的 `Graph.Transition` 没有 `ExecutableTask.effective_input`。
如果从 State、全局缓存或另一个副本反推参数，就会制造第二份事实，违反唯一真相和执行 owner 原则。

### 7.2 必须先选唯一数据来源

只能从 authoritative execution 数据投影，不能让 events 自己猜。可行方向有三种，需由 owner 明确选一条：

1. **扩展 transition 的 typed invocation evidence**：由 execution 在 settlement 时把不可变、已准入的 input/result
   作为 transition 的一部分传给 commit decorator；这会同步影响 `Graph.Commit` contract 和所有 adapter。
2. **把事件投影放在 settlement/execution 层**：那里同时拥有 `ExecutableTask.effective_input` 和 `TaskResult`，
   生成事件后再沿现有 commit 接缝通知；events 不读取 persistence，也不维护缓存。
3. **明确改为 node invocation event**：由 node 层产生“参数–结果”事件，commit 层只保留 settlement confirmation；但
   这样就不再是当前文档所说的单一 commit decorator 方案，必须同步改 API 和 owner 说明。

无论选哪条，都必须保留已有的 `run_id`、scope、node identity 和必要的 execution coordinate，以便并发消费方关联；
但不能把这些 metadata 当成访问参数或执行结果的替代品。

### 7.3 payload 必须是 typed value，不是裸业务字典

“需要参数和结果”不等于可以把任意对象直接塞进事件。应复用 execution 已有的 typed `Graph.Values`/frame admission，
并明确：

- 参数和结果的字段名、类型、成功/失败/中断 variant；
- 值的不可变性（底层业务 DTO 不能在 sink 调用期间被修改）；
- 是否传完整值、脱敏值或只传摘要；
- 超大值、敏感值和无法投影的值如何 fail closed 或降级。

本期不要求建设通用序列化框架，但在事件对外消费前必须把这个最小 schema 写清楚。否则测试即使“收到一个事件”，也
无法证明事件含义正确。

## 8. 非阻断但必须修改的文档/交付项

### 8.1 exact 检查时序文字矛盾

正文是“exact candidate 检查通过后再通知 sink”（[`events-design.zh-CN.md:84-94`](./events-design.zh-CN.md#L84-L94)），
但模块职责表写成“exact 检查前的通知”（[`events-design.zh-CN.md:193-200`](./events-design.zh-CN.md#L193-L200)）。

请把职责表改为“exact 检查通过后的通知”，并注明 decorator 的比较只是通知 gate，最终 authoritative 校验仍由
execution owner 完成。这是明确的文档修正，不需要增加代码抽象。

### 8.2 持久化术语

本期不做 persistence，但设计文档仍应把“持久化 commit”改成“注入的 Graph.Commit callback”，避免下一位实现者误把
events 当成 Store owner。此项不要求本期实现任何数据库或 outbox。

### 8.3 版本控制和 clean checkout

当前审核快照中，events 设计、源码和测试仍显示为未跟踪（`??`）。交付前应将 intended files 纳入明确的 commit，
然后在 clean checkout 验证根包导入、测试和类型检查；不能用兼容 alias 或复制文件掩盖交付状态。

## 9. 门禁记录

### 9.1 定向 events 门禁：通过

当前复核结果：

```text
python -B -m pytest tests/events -q --tb=short -p no:cacheprovider
20 passed

pyright src/mote_kernel/events
0 errors, 0 warnings, 0 informations

python -B -m ruff check src/mote_kernel/events tests/events
All checks passed

python -B -m ruff format --check src/mote_kernel/events tests/events
7 files already formatted
```

此前带 branch coverage 的 events 定向结果为 **100%**；定向架构/依赖/包结构/ownership 检查为 **37 passed**，
`make complexity` health 检查和 `git diff --check` 通过。

### 9.2 全仓和交付门禁：未通过

审查快照中：

- `make check`：当前脏工作树全仓 pyright 报告 **2140 个错误**，主要分布在现有测试、failover 和其他并行改动；
  events 目录定向 pyright 仍为 0 errors；
- `make complexity-ratchet`：`complexity_hotspots=57`，配置上限为 47，另有结构指标超限；
- monorepo 根目录 `pre-commit run --all-files`：除 kernel structural complexity ratchet 外，其余 hook 通过；
- events 目标文件仍未跟踪，clean checkout 结果无法复现。

不能通过上调 ratchet、添加静默白名单或引入兼容路径来“修复”这些数字。应隔离 intended diff 后重新跑全套门禁，
并把与 events 无关的既有失败单独记账。

## 10. 建议整改顺序

1. **先解决 async 契约**：冻结 sink/inner 的 typed contract、动态对象的运行时错误边界和装配失败行为；补完整测试矩阵。
2. **再冻结 sink 失败策略**：当前范围采用 best-effort 还是引入外部可靠 adapter，明确普通异常、取消、丢失和重试边界。
3. **冻结事件 schema 和数据来源**：把节点名称、访问参数、执行结果从 authoritative execution 数据投影出来，不从 State
   猜、不维护副本、不塞裸字典。
4. **修正文档**：删除本期不做的 durability 暗示，修复 exact 时序矛盾，保留 events 内层/logging 外层的装配示例，
   删除跨并发总序承诺。
5. **完善交付门禁**：纳入 intended files，做 clean checkout，隔离并解释全仓类型/复杂度失败，再决定是否批准。

## 11. 最终通过条件

只有以下条件全部满足，才能把状态改为“通过”：

- async sink/inner 的 required contract 在静态和运行时边界上说法一致；错误配置不会被当作普通通知失败静默吞掉；
- 合法 sink 的普通异常、取消和丢通知策略明确且有测试；没有隐含 exactly-once 或必达承诺；
- 事件确实包含需求约定的节点名称、访问参数和执行结果，且这些值来自 execution 唯一权威事实，不创建第二状态或缓存；
- 事件 payload 使用 typed immutable 结构，参数/结果的敏感值、大小和无法投影行为有明确边界；
- events 内层、logging 外层的 assembly contract 写入文档并有组合回归测试；
- 并发场景只验证身份和结果配对，不错误引入全局顺序要求；
- exact 检查时序、commit callback 术语和模块职责表一致；
- intended files 已纳入版本控制，clean checkout 可复现；
- 定向和全仓门禁均通过，或对与 events 无关的既有失败提供隔离、可审计的交付说明。

当前准确结论仍是：**方向可行，定向代码测试通过，但 async 契约、通知失败语义和事件内容尚未闭合，方案暂不通过。**
