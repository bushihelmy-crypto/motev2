# Logging 与 Observability 整改实施计划

状态：**已实施；专项门禁通过；全仓门禁中的无关失败已记录；未提交**

制定日期：2026-09-02

评审基线：

- [`logging-observability-review.zh-CN.md`](./logging-observability-review.zh-CN.md)
- [`logging-observability-review-response.zh-CN.md`](./logging-observability-review-response.zh-CN.md)
- [`logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md)
- [`logging-observability-remediation-plan-review.zh-CN.md`](./logging-observability-remediation-plan-review.zh-CN.md)

## 1. 本轮范围与冻结决定

本轮是 **Kernel decorator contract remediation**，只收敛 Logging 与 Observability 的公共装饰器契约、
旁路失败语义、测试和文档，不实现 Role/Flow assembly，也不把 Events 纳入同一个交付 commit。

冻结以下决定：

1. `LoggedNode`、`ObservedNode`、`LoggedGraphCommit` 统一采用唯一的
   `Decorator(config)(inner)` 形状；
2. 三个公开配置类均为非泛型、`frozen=True`、`slots=True`；类型变量只出现在方法级
   `__call__` 和私有泛型 wrapper；
3. `LoggedGraphCommit` 必须包裹一个调用方提供的 `Graph.Commit`，不接受 `None`，也不提供替代 helper；
4. Logging 可以只读比较 inner 返回值以记录 `accepted`/`mismatch`，但不能据此确认 candidate、短路 inner、
   改写返回值或安装 State；
5. sink/port 是基于共享 `Invocation` 的异步诊断旁路。Port 接受普通 Invocation，并在边界固定使用 best-effort；每次调用默认有
   `BEST_EFFORT_TIMEOUT_SECONDS`（当前 1 秒）的有限协作式 deadline，可用 keyword-only `timeout_seconds` 覆盖为有限正数；
   期限到达、Invocation 自己误抛普通异常或 `asyncio.CancelledError` 都只丢弃当前诊断，不改变业务结果、异常、取消或 mismatch，也不停止 inner；
   deadline 自身占用一个 cancellation count；与调用方取消并发时，额外的 count 仍按调用方取消传播；适配器主动 `uncancel()` 暂不处理；
   业务 inner 或 Graph execution 收到的取消则在尽力记录诊断后原样传播同一个对象；inner outcome 已确定后，后置诊断收到的调用方取消不能覆盖该 primary；
6. `Graph.run(commit=None)` 的进程内确认只由 execution 的 `scoped_commit()` 处理；Logging 不参与；
7. decorator 不校验 Port、factory 或 inner 是否正确装配；这些 required capability 由 strict typing 和未来
   Role/Flow assembly owner 保证，不属于本轮；
8. Events 是独立 owner。本轮不 import 未交付的 `EventingGraphCommit`，跨包组合测试等 Events 先独立合入后再由
   Events 集成测试负责；
9. 不增加兼容重载、别名、第二状态模型、第二 runner、第二 reducer，或宽泛的
   `common`/`utils`/`shared` 包；
10. “最小公共面”仅指两个根包的 `__all__` 合计只暴露三个 decorator，不动态隐藏正常 Python 子模块属性。

本次复审已明确以下实施边界：

- “inner 恰好一次”只表示一次 wrapper invocation 内不主动重试，不承诺跨 Graph recovery 或新的 `Graph.run()` invocation
  的全局 exactly-once；
- `mote_kernel.invocation` 是 Kernel 侧薄适配接缝，不是 transport/runtime owner。composition 通过配置从
  `mote-infra/invocation` 注入 Invocation；local、Unix socket、HTTP、gRPC 等实现由 infra/config 选择，Kernel 不实现 resolver
  或具体通信机制；
- Invocation 提供 strict 与 best-effort 两条错误策略：Hook/核心业务调用使用 strict，Logging/Observability Port 固定使用
  best-effort。Port 不要求调用方再手动包一层策略适配器；
- 零参数 fields/span factory 是调用方提供的 callable。调用方可以用闭包或调用方自己的 context 机制取得坐标，Kernel 不注入
  隐藏 ContextVar、全局上下文或新的 execution API；
- 对 `ObservedNode` 的安装，`port + span_factory` 是一个完整 bundle：两者都缺时不安装节点观测；只缺一个时由 assembly
  报装配错误；两者都有时才安装。`ObservabilityPort` 仍可独立供其他 usage/timing/error 记录使用。

## 2. 目标公共 API 与严格类型形状

### 2.1 精确构造签名

目标签名固定为（required capability 位于首位，示例按位置传入；可选配置使用 keyword-only）：

```python
LoggedNode(
    sink,
    *,
    event="node",
    fields=(),
    fields_factory=None,
)

ObservedNode(port, span_factory)

LoggedGraphCommit(
    sink,
    *,
    event="commit",
)
```

required capability 不额外添加 positional-only `/` 约束，示例按位置传入；可选配置仅允许 keyword 参数。这样旧形状
`LoggedNode(inner, sink)`、`ObservedNode(inner, port, span_factory)`、`LoggedGraphCommit(inner, sink)`
不会形成另一条可用路径。对于结构上刻意同时伪装成多种 capability 的极端对象，不承诺依靠运行时参数猜测调用者意图；
正常配置由精确签名和 strict type checking 拒绝。

`fields_factory=None` 是合法的可选配置，表示只使用静态 fields；`Graph.run(commit=None)` 也是合法的 execution fallback。
required 的 `sink`、`port`、`span_factory` 和 decorator 的 `inner` 均不接受 `None`，不提供把 `None` 转成默认实现的兼容分支。

### 2.2 非泛型配置类、方法级 TypeVar

公开类不声明 `Generic`。其 `__call__` 使用方法级类型变量，输入和返回保持完全相同的 callable 契约：

```python
from collections.abc import Awaitable, Callable
from typing import TypeAlias, TypeVar

from mote_kernel.execution import Graph

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
GraphValueT = TypeVar("GraphValueT")

NodeOperation: TypeAlias = Callable[[InputT], Awaitable[OutputT]]

class LoggedNode:
    def __call__(
        self,
        inner: NodeOperation[InputT, OutputT],
    ) -> NodeOperation[InputT, OutputT]: ...

class ObservedNode:
    def __call__(
        self,
        inner: NodeOperation[InputT, OutputT],
    ) -> NodeOperation[InputT, OutputT]: ...

class LoggedGraphCommit:
    def __call__(
        self,
        inner: Graph.Commit[GraphValueT],
    ) -> Graph.Commit[GraphValueT]: ...
```

私有 `_LoggedNode`、`_ObservedNode`、`_LoggedCommit` 承担泛型生命周期实现。每个私有 wrapper 只保存：

- 一个 typed `inner`；
- 一个对应的 immutable 公共配置对象引用。

它们不重复复制 sink、event、fields 或 factory 配置，也不保存 invocation/run 状态。

### 2.3 规范 node 组装顺序

本轮文档和测试只保留下面一种规范示例。Python 本身不会阻止调用方手工反向嵌套；正式 assembly 必须遵循下面的顺序：

```python
node = LoggedNode(
    log_sink,
    event="role.node",
    fields=node_fields,
    fields_factory=invocation_fields,
)(
    ObservedNode(observability_port, span_factory)(base_node)
)
```

调用顺序是：

```text
LoggedNode.started
  -> ObservedNode.started
    -> base_node
  -> ObservedNode.finished
-> LoggedNode.finished
```

异常和取消路径保持同样的外包内顺序。两个包互不发现、重排或自动安装对方。

### 2.4 Commit 组装边界

本轮可执行示例只依赖 `Graph.Commit`：

```python
commit = LoggedGraphCommit(log_sink)(persistence_commit)
```

未来 Events 已独立合入后，可在 assembly 中组成：

```python
commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(persistence_commit)
)
```

第二段只是跨包组合目标，不是本轮源码依赖或验收测试。Logging 的实际调用链始终是：

```text
transition
  -> write commit.started（旁路）
  -> inner transition callback（本次 wrapper invocation 内一次，不主动重试）
  -> 只读比较 inner 返回值，记录 accepted/mismatch（旁路）
  -> 原样返回 inner 结果
  -> execution owner 做最终 exact-candidate 校验
```

## 3. Capability 与值契约边界

### 3.1 Decorator 不承担 assembly validation

`LoggedNode`、`ObservedNode`、`LoggedGraphCommit` 只通过类型签名声明自己需要什么：

```text
LoggedNode             requires LogSinkPort
ObservedNode           requires ObservabilityPort + NodeSpanFactory
LoggedGraphCommit      requires LogSinkPort + Graph.Commit
```

它们不使用 `getattr`、`inspect`、`callable()`、运行时 Protocol 扫描或试调用来判断 Port、factory、inner 是否装配正确，
也不定义 assembly error。原因是 capability 是否存在、optional bundle 是否启用、commit 是否为 `None` 都属于 Role/Flow
assembly owner，而不是观测 node 或日志 decorator 的职责。

本轮依靠 strict Pyright 保证：

- `LogSinkPort` 与 `ObservabilityPort` 持有 typed `Invocation`，其 async `write()`/`record()` 只转发一次并返回 `None`；
- Port 的 transport 和 runtime 选择不出现在 Kernel 类型或实现中；
- `NodeSpanFactory` 返回 `Span`；
- node inner 保持原输入/输出 callable 契约；
- commit inner 是相同 `GraphValueT` 的 `Graph.Commit`；
- `None`、非 Invocation capability 和错误 callable 签名不能通过正常类型检查。

对于调用方用 `cast`、禁用类型检查或故意传入结构错误对象的情况，不增加运行时反射和兼容分支兜底；行为不属于受支持
公共契约。

### 3.2 Decorator 仍负责自己产生的值

不做 assembly validation，不等于取消值契约。Decorator 仍负责：

- 校验自己的静态 `event` 和 `fields` 配置；
- 对 configured fields factory 的返回值执行现有 tuple/`LogField`/字段名准入；
- 使用 `SpanStarted`/`SpanFinished` 等 immutable record 构造器保证 span observation 的 nominal contract；
- 保持 record、span、duration、error category 和字段长度边界。

这些失败分别继续使用 `LogContractError` 或 `ObservationContractError`。Python 因错误参数数量自然产生的
`TypeError` 不被包装成新的 assembly error。

### 3.3 Span factory 的定位

`span_factory` 只根据调用方为本次 invocation 捕获的 run/scope/trace/parent 信息（如有）构造一个 immutable `Span`：

- 不访问网络、Store 或观测 backend；
- 不上传、缓冲、采样或重试；
- 每次 invocation 恰好调用一次；
- 在受支持契约内没有需要恢复或降级的失败模式。

`ObservedNode` 不接收 invocation context 参数，也不从 scheduler 自动取得 run/scope/trace/parent。调用方负责提供一个可并发
安全的零参数 factory，可用闭包或调用方自己的 context 机制捕获这些坐标；Kernel 不创建或维护这类上下文。

如果 Python 调用仍因 factory 实现 bug 或非法 `Span`/observation 值而抛错，异常自然向上传播，base node 不执行；不增加
“无 span 继续执行”的恢复分支，也不把它建模成 backend 故障。若 factory 自身抛出 `CancelledError`，同样按 setup 失败自然
结束，不启动 base node。

### 3.4 Optional bundle 与 commit absence

未来 Role assembly 若要安装 `ObservedNode`，必须一次性提供 `port + span_factory` 完整 bundle：两者都缺时不安装节点观测，
只缺一个时由 assembly 报装配错误，两者都有时才安装。Decorator 内没有自动禁用分支。`ObservabilityPort` 本身可以独立供
其他 usage/timing/error 记录使用。

同理，assembly 只能用 `commit is None` 判断 callback 是否缺失：

- `commit is None`：不安装 `LoggedGraphCommit`，直接让 `Graph.run()` 使用 execution fallback；
- commit 存在：按类型作为 inner 传入，不检查 backend、Store 或 durability。

这些规则记录未来 owner 的责任，但 Role/Flow assembly 不在本轮实现或验收范围内。

## 4. 运行期失败与取消矩阵

这里的业务取消专指 node/commit inner 或 Graph execution 收到的 `asyncio.CancelledError`。wrapper 先尽力记录取消诊断，
再原样传播同一个取消对象。相反，sink/port 是异步 best-effort 旁路；每次调用有有限协作式 deadline（默认 1 秒，可由 Port 的
`timeout_seconds` 覆盖），如果适配器自己误抛普通异常、`CancelledError` 或 deadline 到达，那只是当前诊断失败，必须隔离并继续业务调用或
保留已经产生的业务结果。只有已知的诊断适配器/投影调用边界才显式隔离其自有
`CancelledError`；业务 inner/execution 的取消仍按上句保留 identity。绝不能用 `except BaseException` 把 `KeyboardInterrupt`、
`SystemExit` 等系统级中断一并吞掉。

| 来源 | 普通异常/非法返回 | `CancelledError` | 是否继续 inner | 结果约束 |
| --- | --- | --- | --- | --- |
| node/commit inner 或 Graph execution | 原样传播 | 记录诊断后原样传播同一对象 | 否 | 不改写 primary |
| fields factory | 回退已验证静态字段 | 仅普通/非法失败纳入回退；自身 `CancelledError` 不在该回退范围，按 factory 异常自然结束；不使用 `BaseException` 泛捕获 | 普通/非法失败继续 | 配置后每次 invocation 恰好调用一次 |
| span factory | 实现或值契约违例自然传播 | 自身 setup 失败自然传播 | 否 | 每次 invocation 恰好调用一次 |
| logging sink | 丢弃当前 record | 丢弃当前 record | 继续 inner；已执行 inner 不重试 | 普通异常、取消或 deadline 都不改变业务结果、异常、取消或 mismatch |
| observation port | 丢弃当前 observation | 丢弃当前 observation | 继续 inner；已执行 inner 不重试 | 普通异常、取消或 deadline 都不改变业务结果、异常、取消或 mismatch |
| logging projection 或 `LogRecord` 构造 | 当前诊断降级或丢弃 | 只丢弃当前诊断 | 继续 inner；不主动重试 | 不改变 primary |
| observation record 构造 | `ObservationContractError` 自然传播 | 不由值构造器制造业务取消 | 否 | 不把非法观测值降级成合法值 |
| persistence/Eventing async inner | 不属于本批；按 owner 契约传播 | 由 owner 原样传播 | 否 | 由 execution/events 测试锁定 |

如果 inner 已经抛出普通异常，随后 failed observation 的 sink/port 又抛出普通异常或 `CancelledError`，只丢弃这条诊断，
仍重新抛出原来的 inner 异常。若 inner 本身已收到 `CancelledError`，诊断层的适配器错误同样被丢弃，重新抛出同一个
取消对象；日志层不把诊断适配器错误提升为第二个业务取消，也不决定主异常。

若 commit inner 已经成功返回、随后 `commit.accepted` 日志写入失败（包括适配器误抛 `CancelledError`），只丢弃该诊断并
原样返回 inner 结果；inner 不得重试。若真正的 commit inner 已经产生外部副作用后收到业务取消，可能形成 callback
acknowledgement uncertainty；后续是否恢复、重放或 reconcile 由 execution 与外部 adapter 的既有契约决定，外部 commit adapter
自行负责幂等、去重或 reconcile，Logging 不建立第二条补偿、durability、ack 恢复或确认路径。

## 5. 数据安全与状态所有权

保留现有已通过边界：

- 默认 lifecycle 不记录 node input/output、异常对象、异常消息或任意 `repr`；
- record/span/attribute 继续是有界、typed、frozen/slots 值；
- 动态字段不能占用 `duration_ns`、`error_type`、`outcome`；
- wrapper 不保存 run snapshot、field snapshot、span 或 transition；所有 invocation 数据只存在于当前调用栈；
- provider/exporter、网络 client、缓冲、重试、采样和落盘仍属于 adapter/runtime；
- Logging/Observability 不 import State reducer，不创建 Graph，不维护 scheduler，不安装内存 State；
- `LoggedGraphCommit` 可以分类记录 exact/mismatch，但分类结果不参与 authoritative decision。

## 6. Events 与 Role/Flow 的明确范围

### 6.1 Events 依赖顺序

截至本计划编写时，本地 `HEAD` 为 `45ab874`，其中是旧的 Logging/Observability 实现、测试和设计；本轮整改 overlay
仍未提交，远端分支已回退到 `008848c`。这只是交付历史记录，不是未来 amend 后唯一可依赖的 baseline。当前 Events 实现和
测试仍是其他未提交工作树内容。

本轮固定采用以下隔离方案：

- Logging commit 测试只使用测试内 typed persistence callback；
- Logging/Observability 生产代码和测试不 import `mote_kernel.events`；
- `events/commit.py` 与 `tests/events` 不进入本轮影响面或目标 commit；
- Events 独立合入后，由 Events 所有的集成测试验证
  `LoggedGraphCommit(log_sink)(EventingGraphCommit(persistence_commit))` 的实际顺序和取消语义。

概念设计文档可以展示未来组合，但必须明确前置依赖，不能把未跟踪模块当作 clean checkout 已存在。

### 6.2 Role/Flow 后续计划

当前 `src/mote_kernel/role/` 没有实际 assembly API。本轮不创建临时 assembly owner，也不在测试中伪造公共 Role API。

后续 Role/Flow assembly 负责：

- optional Port 缺失时不安装相应 decorator；
- 要安装 `ObservedNode` 时，Observability port/factory bundle 只允许完整启用或完整禁用；Port 仍可独立服务其他观测记录；
- 固定 node 与 commit decorator 顺序；
- 将已经组装好的 node 传给 `Graph.add_node()`，将已经组装好的 commit 传给 `Graph.run()`。

这些是后续接入条件，不是本轮 decorator contract 关闭条件。

## 7. 实施阶段

### 阶段 A：公共 API 与类型形状

修改：

- `src/mote_kernel/logging/node.py`
- `src/mote_kernel/logging/commit.py`
- `src/mote_kernel/observability/node.py`

步骤：

1. 将三个公开类改为非泛型 immutable 配置类；
2. 增加三个私有泛型 wrapper，并让其持有 `inner + config`；
3. `__call__` 使用方法级 TypeVar，返回与 inner 相同的 callable 协议；
4. required capability 放在首位并按位置示例传入，可选配置保持 keyword-only；不额外引入 positional-only 自定义构造器；
5. 删除 commit 的 `inner=None` 分支和所有旧构造示例；
6. 不增加兼容 overload、alias 或另一种 helper。

阶段退出条件：正向 `assert_type`/Pyright fixture 中 node、commit 单层和组合链均没有 `Unknown`；公开签名测试与目标
构造规则一致。

### 阶段 B：值契约与旁路语义

步骤：

1. 删除 decorator 内针对 Port/factory/inner 的 assembly validation；
2. 保留 event、fields、record 和 observation 的值契约；
3. 不为 span factory 定义恢复分支；实现/值契约违例自然失败，inner 不执行；fields factory 的普通/非法失败回退静态字段；
4. sink/port 的普通异常和适配器误抛的 `CancelledError` 都只丢弃当前诊断，不得改变业务返回值、异常、取消或 mismatch，
   也不得停止 inner、触发重试或重放；
5. 保留 logging 字段和 transition 投影对普通错误的 best-effort 降级；业务 inner/Graph execution 收到取消时记录诊断后
   保持同一个取消对象。诊断边界不得用 `except BaseException` 吞掉系统级中断；
6. 确保已配置的 fields factory 和 required span factory 在每次 invocation 中各恰好调用一次；
7. 在 required setup 成功的每次 invocation 内，确保 node/commit inner 恰好调用一次；setup 失败时按各自契约不调用 inner。

阶段退出条件：普通 sink/port 故障或适配器误抛 `CancelledError` 均不改变业务结果、异常、取消、mismatch 或调用次数；
业务 inner/Graph execution 的取消在尽力记录诊断后保持同一个取消对象；factory setup/value 失败按各自契约处理；任何路径
都不主动重试或重放 inner。

### 阶段 C：测试与 Graph 集成

改写现有旧形状测试，并新增第 8 节矩阵。测试不得通过兼容构造继续覆盖旧 API，也不得依赖未合入 Events。本轮对 Graph
只保留一个 facade smoke test 和 `commit=None` fallback test；嵌套 Graph 的 recovery/replay 由 execution owner 负责。

阶段退出条件：定向 line + branch coverage 为 100%，strict Pyright 通过，Graph facade smoke test 只经过唯一 execution
路径；不以本轮测试重新实现 execution recovery。

### 阶段 D：文档与 breaking-change 清理

更新：

- `docs/logging-observability-design.zh-CN.md`；
- `CHANGELOG.md` 的 `Unreleased / Changed`，明确这是有意 breaking change；
- README/README.zh-CN 中若存在或新增诊断示例，两种语言同步；
- 本计划和评审回复文档的最终状态。

检索生产代码、测试、README、CHANGELOG 和最终设计文档，清除旧构造方式。评审/回复文档作为历史证据可以保留旧代码片段，
不应被机械替换。

### 阶段 E：复杂度、全门禁与 clean delivery

按第 9、10 节执行隔离复杂度审计、专项门禁、全仓门禁和 clean checkout 验证。不得为了通过门禁直接抬高
`pyproject.toml` ratchet。

本轮已完成阶段 A--E 的 Logging/Observability 范围。专项结果、隔离审计和全仓门禁中的无关失败见第 9、10 节；
本计划不表示 Events、Failover、Cloudflare 或其他工作树改动已经完成。

## 8. 验收测试矩阵

| 类别 | 场景 | 预期 |
| --- | --- | --- |
| 公共 API | 三个 `Decorator(config)(inner)` | 签名、运行和 strict 类型均通过 |
| 公共 API | 旧 positional、旧 keyword、旧泛型下标形状 | 无兼容路径；静态负例被 fixture harness 拒绝 |
| 公共 API | `@LoggedNode(...)`、`@ObservedNode(...)`、`@LoggedGraphCommit(...)` | 真实 decorator 语法可用 |
| 类型 | node 单层、Observed+Logged 链 | 保留精确输入/输出类型，无 `Unknown` |
| 类型 | commit 单层与 Graph.run | 保留 `GraphValueT`，无 `Unknown` |
| 类型 | Port/factory/inner 为 `None` 或错误 callable | strict 类型拒绝；decorator 不做 assembly validation |
| 类型 | Port 持有错误 request/result 的 Invocation | strict 类型拒绝；不把 transport 类型泄漏到 Kernel |
| node 顺序 | Logged(Observed(base)) 成功 | started/finished 顺序精确 |
| node primary | 正常返回、普通异常、inner 取消 | 单次 wrapper invocation 内调用一次；普通诊断失败不改变对象 identity |
| fields factory | 普通异常、非法返回 | 回退静态 fields，inner 继续 |
| span factory | 实现异常、非法 Span | 自然传播，inner 不执行；不建模为可恢复故障 |
| node sink/port | 普通异常 | 当前诊断丢弃，业务 primary 不变 |
| node sink/port | `CancelledError` | 丢弃当前诊断，inner/业务 primary 不变；不停止、不重试 |
| invocation | fields/span factory | 配置后每次 invocation 各恰好一次；并发不共享快照 |
| factory context | caller closure/自有 context 机制 | Kernel 不注入隐藏上下文；每次调用产生独立 fields/span identity |
| commit primary | exact、mismatch、异常、取消 | 单次 wrapper invocation 内调用一次；不主动重试；普通诊断失败不改变 primary identity |
| commit 日志 | started 写入普通异常或 `CancelledError` | 丢弃当前诊断，commit inner 仍执行 |
| commit 日志 | inner 返回后 accepted/mismatch 写入普通异常或 `CancelledError` | 丢弃当前诊断，原样返回 inner 结果；不重试 |
| 业务取消诊断 | inner/execution 取消且诊断适配器失败 | 原取消对象保持 identity；适配器错误不成为第二取消 |
| commit owner | exact/mismatch 分类 | 只影响日志，不替代 Graph owner 校验 |
| commit 缺失 | `LoggedGraphCommit(sink)(None)` | 负向类型 fixture 拒绝；无 `None` fallback |
| execution fallback | `Graph.run(commit=None)` | 正常进程内执行，不经过 Logging |
| transition 投影 | 超长/非法可观察字段或诊断适配器误抛 `CancelledError` | 当前诊断降级/丢弃，inner 仍一次；不改变业务 primary |
| Graph facade | decorated commit、`commit=None` fallback | 只验证 facade 接入唯一 execution 路径；不覆盖跨 recovery 全局 exactly-once |
| 数据安全 | 默认成功/失败/取消记录 | 不含 input/output、异常对象、异常消息或 `repr` |
| 并发 | 同一 decorator 配置复用多个 run | 无共享 run/field/span/transition 状态 |
| 公共面 | 两个根包 import 与 `__all__` | 仍只文档化导出三个 decorator |

Events 的 event projection-before-persistence、Invocation transport 和持久化次数继续由各自 owner 测试负责，不复制到
本轮 Logging 测试。

## 9. 隔离复杂度预算

当前全工作树包含 Events、Failover、Cloudflare 和复杂度分析的其他改动，不能用其聚合数字归因本整改。复杂度审计必须
在临时 clean checkout 中复现旧 Logging/Observability tree，只应用本计划目标 diff，再生成 before/after 报告。当前旧
feature commit 的共同父提交是 `008848c05136c6252538d1f61de1b7687b99be7c`，旧实现 tree 是
`45ab874^{tree}`（当前为 `695849ebd2c3e093a7aaf98c21ddd8c57efb2e97`）；最终 amend 前须保存该 tree/patch，不能把
未来会重写的 commit 对象当作唯一基线。

本设计预先列出的结构变化参考线是审计提示，不是改动上限或“最小改动”要求：

| 指标 | 参考净增长上限 | 原因 |
| --- | ---: | --- |
| `top_level_definitions` | `+3` | 三个私有 wrapper type 的预期量级 |
| `type_definitions` | `+3` | 同上 |
| `dataclass_types` | `+3` | immutable 私有 wrapper |
| `dataclass_fields` | `+3` | 每个 wrapper 增加一个 `config` 引用，不复制配置字段 |
| `method_definitions` | `+3` | 三个配置应用方法；不增加 capability validation 方法 |

这里的数字是 gross 结构参考线，不是为了让指标通过而必须填满的配额。当前 analyzer 的净结果中，三个 wrapper 使
`dataclass_types`/`dataclass_fields` 各增加 3；其余定义变化来自将生命周期实现移入私有 wrapper、保留一个真实的
`Awaitable` callable 契约，以及为字段 fallback 保留清楚的模块局部策略函数。实际 delta 及其理由见 9.1，不把预算当作
ratchet 例外，也不以静态检查分支制造第二套类型真相。

下列生产结构/健康指标以零增长为参考目标：

- logical/statement/near clone pairs；
- complexity hotspots；
- max cyclomatic/cognitive complexity 与 max nesting；
- import cycles、unused/unread、unconsumed async、unowned coroutine/task 等全部 health debt；
- 第二条 runtime/commit/state 调用链。

`complexity_snapshot()` 的部分语义指标会同时包含 production 和 tests。报告必须分别列出 production structural
budget、test-only 增量，以及 clone/hotspot/max-complexity/health debt 结果；测试数量增加本身不能被误报为生产结构退化。
这些指标是高召回风险线索，不是脱离代码阅读的自动通过/失败判定。评审还必须判断新增 wrapper 是否确有必要、结构是否
简洁明了、复杂度是否可由职责解释、是否便于人类维护。若实现超过参考线，应先说明不可消除的必要性并更新本计划；不能用
抬高 `pyproject.toml` ratchet 掩盖问题。只有隔离报告证明新形状合理、全仓基线变更来源可解释，并获得单独架构决定后，才可
调整 ratchet。

### 9.1 本轮隔离审计结果

审计在临时 clean checkout 中复现，before 使用 `45ab874` 的 Logging/Observability tree，after 只叠加本轮目标生产模块、
专项测试、类型 fixture 和诊断边界测试。可复现基线为：

```text
baseline commit: 45ab874
baseline tree:   695849ebd2c3e093a7aaf98c21ddd8c57efb2e97
report:          /tmp/mote-logging-observability-audit-20260902/complexity-audit.txt
```

生产增量（同一 analyzer、同一口径）为：

| 指标 | before → after | delta |
| --- | ---: | ---: |
| `top_level_definitions` | 561 → 565 | +4 |
| `type_definitions` | 341 → 342 | +1 |
| `dataclass_types` | 198 → 201 | +3 |
| `dataclass_fields` | 548 → 551 | +3 |
| `function_definitions` | 423 → 426 | +3 |
| `decision_points` | 1462 → 1467 | +5 |
| `exception_handlers` | 57 → 61 | +4 |
| `semantic_nodes`（production-only） | 29775 → 29846 | +71 |
| `cognitive_complexity` | 2013 → 2016 | +3 |
| `record_shape_clone_pairs` | 23 → 24 | +1 |

`single_use_private_dataclasses` +3、`low_usage_private_definitions` +3、`production_unreferenced_definitions` -3，
`cross_module_call_edges` -1；`max cyclomatic`、`max cognitive`、`max nesting`、import cycles、unread fields、
unconsumed async、unowned coroutine handles 和 orphaned tasks 均无新增。test-only 增量另行统计，不能并入生产预算。

production-only 的 `unused_private_definitions` 保持 `12 → 12`（既有高召回候选，非新增债务）；按完整 tests 语义计算的
health 指标保持全为零。

含 tests 的完整 snapshot 中 `semantic_nodes` 为 `29775 → 29854`（+79），其中 +8 来自 test-only 语义节点；其余上述
生产指标没有额外 test-only 增量。分析器的 production structural definitions 与 tests 的混合语义/reference 指标分开解读，
避免把专项测试本身误报为生产复杂度。

`top_level_definitions` 比原参考线 `+3` 多 1，来自命名的 `_invocation_fields` policy helper；它让每次 invocation 的
fields fallback 规则独立于 wrapper 生命周期，避免在一段大异常块里混合两种失败来源。三个私有 wrapper 的 `+3`
dataclass/type/field 结构是保持配置与 inner 精确类型所必需的实现，不复制配置字段，也不保存运行状态；如果范围内发现
其他真实债务，也应一并清理，不能为了贴合这条参考线保留债务。Logging 的
node/commit 写入现在共用一个 logging 包内的窄 `emit` 函数；它不进入根包公共面，也没有为消除局部重复引入宽泛的
`common/utils` 层。`record` 与 `span` 的值校验仍由各自 owner 保持独立，不能为了消除高召回 clone 提示而制造跨包耦合。

报告中的 clone、hotspot 和低使用率条目是高召回审计提示，不是自动失败结论。人工复核确认新增结构职责单一、复杂度低、
没有新增 health debt，且比抽象共享层更容易维护；不通过抬 ratchet 掩盖指标变化。

## 10. 门禁与交付策略

### 10.1 必跑门禁

```text
定向 pytest + line/branch coverage 100%
ruff check
ruff format --check
pyright strict（目标源码、测试及正向/负向类型 fixture）
公开签名与旧形状检索门禁
隔离复杂度 before/after 审计
make check                         # mote-kernel
pre-commit run --all-files         # /home/longert/motev2
clean checkout import + 定向测试
```

`tests/typing_negative` 被全局 Pyright 排除；其负向用例必须继续由现有 fixture harness 单独执行，不能混入普通 strict
run，也不能以关闭类型检查来“通过”旧构造反例。

全仓既有失败必须精确记录 owner、文件和错误，不能通过修改诊断包或 ratchet 掩盖。

### 10.2 本轮已执行结果

目标范围已执行并通过：

- 诊断 Logging、Observability 及边界/类型 fixture：`66 passed`；
- 诊断模块定向 line + branch coverage：`32 passed`，生产三模块均 `100%`；
- 定向 Ruff 与 `ruff format --check`：通过；
- 目标生产源码与正向 fixture strict Pyright：`0 errors`；
- 负向 fixture harness：`32 passed`；
- clean 临时 checkout import/signature 与定向测试：`65 passed`；
- `make package-check`：通过；
- 目标文档相对链接扫描：`missing links=[]`。

全仓门禁也已运行，但当前工作树的其他改动造成以下失败，不能归因于本整改：

- `make check` 首步 lint：`src/mote_kernel/execution/engine/recovery.py:831` 的 `UP034`，以及
  `tests/hooks/test_hooks.py:22` 的 `F401`；
- 全仓 complexity gate：`unused_private_definitions actual=5` 和 structural ratchet 超出，来源为其他 execution 等改动；
- 全仓 Pyright：`2032 errors`，主要来自 execution/failover/tests 的其他改动；
- 全仓 pytest：仅 complexity 两项及 `tests/architecture/test_generic_integrity.py` 中 failover/policy 两处
  object boundary 失败，其余测试通过。

本轮没有修改这些无关文件、门禁规则或 ratchet。没有 commit、amend 或 push；`45ab874` 的 tree/report 已保存，待用户
另行授权后再决定历史整理。

### 10.3 Breaking change 与版本历史

从 `LoggedNode(inner, sink)` 迁移到 `LoggedNode(sink)(inner)` 是有意 breaking change，即使根包名称和 `__all__`
不变，也必须更新 CHANGELOG 和最终使用示例，不提供兼容层。

远端已经撤回 `45ab874`，本地仍保留该提交。因此最终交付应把原实现和整改 **amend/squash 为一个修正后的独立
feature commit**，不能向远端推送“已知有问题的 `45ab874` + 后续修复”两段历史。任何 amend、commit 或 push 都需用户
再次明确授权。

目标提交只包含：

- Logging/Observability 实现；
- 对应专项与架构测试、正向/负向类型 fixture；
- design、remediation plan、review response；
- 原始 review 与本次 remediation-plan review（作为本次交付的审计记录）；
- CHANGELOG 及确有相关内容的 README 精确 hunk。

明确排除：Events、Failover、Cloudflare、复杂度分析器本身及其他工作树改动。相关 Logging/Observability 评审文档作为
审计记录纳入目标文档集，但不是生产代码依赖。

## 11. 最终验收条件

以下条件是本轮实施的验收定义；目标范围已满足，未满足的全仓项均是第 10.2 节记录的无关工作树失败：

1. 三个公开对象均是非泛型 immutable 配置类，并遵循唯一两阶段 decorator 协议；
2. 方法级 TypeVar 和正向类型 fixture 证明链式组合不存在 `Unknown`，负向 fixture 拒绝旧构造形状；
3. 不存在 `LoggedGraphCommit(inner=None)` 或任何等价 fallback；
4. Logging 只读分类 exact/mismatch，不短路 inner、不改写返回、不承担 execution 校验；
5. decorator 不承担 assembly validation；自身配置、observation 值契约、普通及误抛取消的 Port 故障、以及业务取消边界清晰；
6. 第 4 节失败/取消矩阵及第 8 节测试矩阵全部通过；“恰好一次”均明确限定为单次 wrapper invocation，诊断适配器失败不
   改变业务 primary，也不由 Logging 选择或合并取消；
7. 默认记录不泄漏业务值或异常详情，wrapper 并发复用无隐藏状态；
8. 本轮源码和测试不依赖未合入 Events，也不伪造 Role assembly；
9. 隔离复杂度报告可复现且 production/test 增量可分别归因；若偏离结构参考线有必要性说明，health debt 仍为零；
10. 专项目录覆盖率、Ruff、format、strict Pyright、目标 Kernel 检查和 clean checkout 验证通过；全仓无关失败按第 10.2 节
    精确归因；
11. 旧构造方式已从最终用户文档、生产代码和普通测试清除，CHANGELOG 明确 breaking change；
12. `mote_kernel.logging.__all__` 与 `mote_kernel.observability.__all__` 合计只暴露三个 decorator，不做动态隐藏；
13. 后续交付 commit（须另行授权）只包含目标文件，其他用户工作树改动保持原样；本轮未提交不构成代码契约失败。
