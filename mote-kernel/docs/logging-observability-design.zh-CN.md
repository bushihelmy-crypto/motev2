# Logging 与 Observability 边界

本文固定 Mote Kernel 中诊断日志与可观测性的最小边界。两者都是可选旁路能力，但语义和挂载点不同；
它们都不拥有 Graph 状态、执行循环或具体 backend。

## 1. 最终职责

```text
ordinary node callable
  -> ObservedNode        span / timing / normalized error
  -> LoggedNode          node started / finished / failed / cancelled
  -> Graph.add_node(...)

Graph.Commit
  -> EventingGraphCommit 将结算引用与 State 交给同一持久化事务（启用 events 时）
  -> LoggedGraphCommit   transition callback 的诊断日志
  -> Graph.run(commit=...)
```

- `observability` 只装饰 node invocation。它回答节点调用何时开始、何时结束、耗时多少、是否异常或取消，
  并允许模型、工具等更窄的 Port 追加 usage/timing/error 记录。
- `logging` 同时装饰 node 和 `Graph.Commit`。node 日志用于定位没有进入 settlement/commit 的异常；commit 日志用于
  记录 callback 收到的 transition 坐标以及 callback 的返回、异常或取消。
- `events` 表达已经确认的节点结算事实。它与 logging、observability 都不是同一条语义边界；Events 装饰器只做
  settlement 引用投影，并恰好一次调用具备 State + outbox 原子事务能力的 persistence port。确认后若配置了 `EventPort`，
  再经共享 `Invocation` 向 runtime 发出一次 best-effort 通知；远端 durable 投递仍由事务后的 dispatcher 完成，EventPort
  不成为第二条提交路径。

Observability 不提供 `Graph.Commit` 装饰器。若某个具体 commit adapter 希望记录自身的基础设施指标，应在该 adapter
所属实现中完成；Kernel 的 node observability 不据此假定 callback 背后存在 Store、数据库或持久化行为。

## 2. 最小根包入口

根包只暴露 Role/Flow 组装需要的包装器：

Logging/Observability 的三个公开 decorator 都是非泛型、`frozen`、`slots` 配置对象；泛型只存在于方法级 `__call__` 和私有 wrapper，用于保持
被装饰 callable 的精确类型。
node callable 的真实返回契约是 `collections.abc.Awaitable`；执行引擎负责等待它，不另设只接受具体 coroutine 的静态分支。

| 根包 | `__all__` |
| --- | --- |
| `mote_kernel.events` | `EventingGraphCommit` |
| `mote_kernel.logging` | `LoggedNode`, `LoggedGraphCommit` |
| `mote_kernel.observability` | `ObservedNode` |

backend 实现使用的 `LogSinkPort`、`ObservabilityPort`、Events `EventPort` 和 immutable record/span 值位于明确子模块中，不从根包重复
导出，也不提供同义便利函数或兼容别名。

## 3. Backend 中立

Kernel 包不 import OpenTelemetry、Langfuse、日志框架、网络 client、runtime 或 exporter。它只定义：

- bounded immutable record/span 值；
- 一个持有 `Invocation` 的 `LogSinkPort`，其 `write()` 通过 best-effort 策略转发 `LogRecord`；
- 一个持有 `Invocation` 的 `ObservabilityPort`，其 `record()` 通过 best-effort 策略转发 `Observation`；
- 一个持有 `Invocation` 的 Events `EventPort`，其 `emit()` 在确认后的 settlement 引用上通过 best-effort 策略转发；
- 三个只包裹现有 callable 的装饰器。

`mote_kernel.invocation` 只提供 Python 侧的薄适配接缝。实际 `Invocation` 对象由 composition 从
`mote-infra/invocation` 注入；local、Unix socket、HTTP、gRPC 等实现以及目标 runtime 的选择都由配置决定，Kernel 不解析
endpoint、不创建 resolver，也不 import 具体 transport。Port 接受普通 typed `Invocation`，在自身边界固定选择
best-effort，不要求调用方重复包一层策略适配器。

由于 Invocation 是 async 的，诊断 Port 的 `write()`/`record()`/`emit()` 也是 async；它们只等待这一次已配置的适配器调用，不主动重试或
创建 fire-and-forget task。每个 Port 默认使用 `BEST_EFFORT_TIMEOUT_SECONDS`（当前为 1 秒），也可通过 keyword-only
`timeout_seconds` 配置为有限正数；期限到达即把本次诊断视为 adapter-owned failure 并丢弃。该期限是协作式 async 边界，Invocation
实现必须响应取消；Port 不创建脱离 owner 的后台 task。`invoke_best_effort` 隔离适配器自己的普通异常和 `CancelledError`，而调用方
task 的业务取消仍可传播；若 deadline 取消与调用方取消同时到达，deadline 只占用一个取消计数，额外的调用方取消仍传播。
适配器主动 `uncancel()` 的来源恢复暂不属于当前契约。需要强制报错的 Hook、核心业务调用使用 `invoke_strict`；诊断 Port 不把 transport
错误升级为业务结果或异常。

## 4. 不改变执行语义

旁路字段投影、可丢弃的 record 构造、sink/EventPort 写入或 observation adapter 的普通错误都不会替换 node/commit 的结果。异步 sink/port
若适配器自己误抛普通异常、`asyncio.CancelledError` 或达到有限 deadline，均只丢弃当前诊断，不停止 inner、不改变业务结果/异常/取消/mismatch，
也不触发重试或重放。只有已知诊断适配器/投影调用边界显式隔离其自有 `CancelledError`；无法投影成 bounded record 的诊断
字段只会使当前记录降级或被丢弃。
业务 inner 或 Graph execution 收到 `asyncio.CancelledError` 时，wrapper 先尽力记录诊断，再原样传播同一个取消对象；inner 已经返回或抛出后，
后置诊断阶段收到的调用方取消只终止该诊断，不覆盖已经确定的 primary outcome。诊断
边界不得用 `except BaseException` 吞掉 `KeyboardInterrupt`、`SystemExit` 等系统级中断。
`span_factory` 的 setup/Span 值契约错误仍按其 required invocation setup 规则自然失败，不被伪装成 backend 旁路故障。
`LoggedGraphCommit` 对 callback 的返回只做诊断：

```text
transition
  -> inner commit callback（单次 wrapper invocation 内不主动重试）
  -> 日志记录 accepted 或 mismatch
  -> 原样返回 callback 的结果
  -> Graph owner 执行 exact-candidate 校验
```

因此日志装饰器不成为第二个 commit owner，也不声称 transition 已被某种 Store 确认。

`EventingGraphCommit` 先为 `SettleGraphNode` 投影稳定引用，再将 transition 与该引用作为一个不可变请求交给内层
persistence commit。内层在同一事务中提交 candidate `GraphRunState` 和 outbox；若返回 exact candidate 且 assembly 配置了
`EventPort`，装饰器随后只调用该 Port 一次。Port 通过 `invoke_best_effort` 隔离适配器自己的普通异常和 `CancelledError`，
不重试、不回滚，也不自行发送 outbox；持久化异常仍原样传播。非 settlement transition 使用空 Event 引用，但仍经过同一个
persistence port。

## 5. 数据安全与边界

默认 lifecycle 记录不写入 node 输入、输出、异常对象或异常消息。记录只携带 bounded scalar、静态节点字段、
provider-neutral error category 和耗时。调用方若添加关联字段，仍必须通过 immutable typed field/attribute 序列，不能把
裸字典或任意对象穿过 Port。`duration_ns`、`error_type` 和 `outcome` 由 node logging lifecycle 独占；动态字段若占用这些
名称，本次调用会退回已验证的静态字段。

Span/trace identity 由调用方通过闭包或调用方自己的 context 机制提供给每次调用 factory；Kernel 不维护隐藏的全局 trace
状态，也不自动注入 scheduler context。一个装饰器实例不保存 run snapshot，因此可以复用于并发 Graph run。

`fields_factory` 只补充可选动态字段；它自己的普通异常或非法字段结果回退静态 fields。`span_factory` 是
`ObservedNode` 的必需 invocation setup，非法 `Span` 或实现错误自然失败且不执行 base node。两类 factory 都不访问网络、
Store、backend 或 exporter。

## 6. 组装位置

Port 在组装时先由配置选出对应 Invocation，再构造成 `LogSinkPort(invocation)` 或
`ObservabilityPort(invocation)`。最终包装顺序由 Role assembly 决定，规范/推荐顺序是 `Logged(Observed(base))`；普通 Python API 无法强制阻止反向嵌套。required
capability 放在构造签名首位，示例按位置传入，不额外添加 positional-only `/` 约束。缺少
可选 logging/observability capability 时，assembly 直接使用原 node 或 commit；不能在运行时创建另一张图、第二个 scheduler
或平行状态模型。典型组装形式为：

```python
node = LoggedNode(
    log_sink,
    event="role.node",
    fields=node_fields,
)(ObservedNode(observability_port, span_factory)(base_node))

commit = EventingGraphCommit(persistence_commit)
commit = LoggedGraphCommit(log_sink)(commit)
```

需要 runtime 通知时，由 assembly 先用配置解析 Invocation，再显式注入 `EventPort`：

```python
event_port = EventPort(runtime_invocation)
commit = EventingGraphCommit(persistence_commit, event_port=event_port)
commit = LoggedGraphCommit(log_sink)(commit)
```

安装 `ObservedNode` 时，`ObservabilityPort + span_factory` 是完整 bundle：两者都缺则不安装节点观测，只缺一个时报
assembly error，两者都有才安装。`ObservabilityPort` 仍可独立记录 usage/timing/error；decorator 自身不做 Port、factory 或
inner 的反射探测、试调用或 assembly validation。`fields_factory=None` 合法，required 的 sink、port、span_factory 和
inner 不接受 `None`；`Graph.run(commit=None)` 仍由 execution owner 提供 fallback。

`execution.Graph` 仍是唯一构图与执行门面，`GraphRunState` 和原有 commit path 仍是唯一权威状态边界。
