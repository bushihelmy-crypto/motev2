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
  -> EventingGraphCommit 持久化确认后的结算通知（启用 events 时）
  -> LoggedGraphCommit   transition callback 的诊断日志
  -> Graph.run(commit=...)
```

- `observability` 只装饰 node invocation。它回答节点调用何时开始、何时结束、耗时多少、是否异常或取消，
  并允许模型、工具等更窄的 Port 追加 usage/timing/error 记录。
- `logging` 同时装饰 node 和 `Graph.Commit`。node 日志用于定位没有进入 settlement/commit 的异常；commit 日志用于
  记录 callback 收到的 transition 坐标以及 callback 的返回、异常或取消。
- `events` 表达已经确认的节点结算事实。它与 logging、observability 都不是同一条语义边界；若部署需要可靠投递或
  outbox 原子性，应由具体能力自行保证，Kernel 装饰器只做投影与通知，不偷偷建立第二个提交路径。

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

backend 实现使用的 `LogSinkPort`、`ObservabilityPort` 和 immutable record/span 值位于明确子模块中，不从根包重复
导出，也不提供同义便利函数或兼容别名。

## 3. Backend 中立

Kernel 包不 import OpenTelemetry、Langfuse、日志框架、网络 client 或 exporter。它只定义：

- bounded immutable record/span 值；
- 一个同步、必须快速返回的 `LogSinkPort.write()`；
- 一个同步、必须快速返回的 `ObservabilityPort.record()`；
- 三个只包裹现有 callable 的装饰器。

具体 adapter 可以把记录映射到任意 backend。异步上传、缓冲、重试、批处理、时间戳、采样、格式化与落盘均属于
adapter/runtime；Port 的同步调用只应完成内存入队或等价的快速操作，避免给 node invocation 增加新的 await/cancellation
边界。

## 4. 不改变执行语义

旁路字段投影、可丢弃的 record 构造、sink 写入或 observation adapter 的普通错误都不会替换 node/commit 的结果。同步 sink/port
若适配器自己误抛普通异常或 `asyncio.CancelledError`，只丢弃当前诊断，不停止 inner、不改变业务结果/异常/取消/mismatch，
也不触发重试或重放。只有已知诊断适配器/投影调用边界显式隔离其自有 `CancelledError`；无法投影成 bounded record 的诊断
字段只会使当前记录降级或被丢弃。
业务 inner 或 Graph execution 收到 `asyncio.CancelledError` 时，wrapper 先尽力记录诊断，再原样传播同一个取消对象。诊断
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

`EventingGraphCommit` 先投影 `SettleGraphNode`，再调用内层持久化 commit；只有内层返回 exact candidate 后才等待
单事件 sink。普通 sink 异常被隔离，避免已经确认的状态 transition 被重复提交；`asyncio.CancelledError` 仍向调用方传播。
非 settlement transition 不产生事件。

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

最终包装顺序由 Role assembly 决定，规范/推荐顺序是 `Logged(Observed(base))`；普通 Python API 无法强制阻止反向嵌套。required
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

安装 `ObservedNode` 时，`ObservabilityPort + span_factory` 是完整 bundle：两者都缺则不安装节点观测，只缺一个时报
assembly error，两者都有才安装。`ObservabilityPort` 仍可独立记录 usage/timing/error；decorator 自身不做 Port、factory 或
inner 的反射探测、试调用或 assembly validation。`fields_factory=None` 合法，required 的 sink、port、span_factory 和
inner 不接受 `None`；`Graph.run(commit=None)` 仍由 execution owner 提供 fallback。

`execution.Graph` 仍是唯一构图与执行门面，`GraphRunState` 和原有 commit path 仍是唯一权威状态边界。
