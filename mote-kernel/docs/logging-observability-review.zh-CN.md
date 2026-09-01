# Logging 与 Observability 实现评审

状态：**P0 阻断，不批准按当前形状交付**

审查日期：2026-09-01
审查对象：

- [`src/mote_kernel/logging`](../src/mote_kernel/logging)
- [`src/mote_kernel/observability`](../src/mote_kernel/observability)
- [`docs/logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md)

本文件是设计与实现审查，不是实现变更记录。本轮没有修改 logging/observability 生产代码。

## 1. 结论先行

当前实现已经把 backend、中间件、状态和执行器排除在 Kernel 之外，记录值也基本满足不可变、有限长度和类型化的要求；但它还没有闭合用户要求的核心公共契约：

> logging 与 observability 对外只提供可链式装饰器，并且只做旁路装饰。

阻断原因有两项：

1. 三个公开对象是“带 `inner` 的可调用 wrapper”，不是统一的 `Decorator(config)(inner)` 装饰器工厂；
2. `LoggedGraphCommit(inner=None, ...)` 可以在 logging 包内直接确认 candidate，形成 execution 之外的另一条 commit 确认路径。

在这两项定案前，不能把当前测试通过解释为公共 API 已完成。现有测试主要验证手工嵌套 wrapper，未验证用户所说的标准链式装配。

## 2. 判定基线

评审以以下约束为准，门禁结果不能替代架构判断：

- [`architecture.md`](./architecture.md#L30)：`execution.Graph` 是唯一公开构图/执行门面，required capability 在装配期校验，可选 capability 在装配时移除对应步骤；
- [`architecture.zh-CN.md`](./architecture.zh-CN.md#L10)：`GraphRunState` 是唯一权威状态，只有一个状态 owner 和一个原子提交边界；
- [`execution-state-frontier-call-chain.zh-CN.md`](./execution-state-frontier-call-chain.zh-CN.md#L278)：command、candidate 和确认必须经过唯一 `commit_transition()`；
- [`logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md#L20)：observability 只装饰 node，logging 装饰 node 与 `Graph.Commit`；
- [`logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md#L45)：Kernel backend-neutral，不引入 exporter、网络 client、Store、scheduler 或第二 runner；
- [`AGENTS.md`](../AGENTS.md)：不使用兼容别名、重复执行路径、隐藏可变状态或宽泛的通用包。

## 3. 目标公共形状

三个根包入口应表达同一个装饰器协议：配置在第一阶段固定，第二阶段只接收并返回原 callable 的同契约 wrapper。

| 根包 | 公开对象 | 挂载点 | 必须禁止 |
| --- | --- | --- | --- |
| `mote_kernel.logging` | `LoggedNode` | async node | 输入/输出值镜像、状态写入、第二 runner |
| `mote_kernel.logging` | `LoggedGraphCommit` | `Graph.Commit` | candidate 自行确认、第二 commit owner |
| `mote_kernel.observability` | `ObservedNode` | async node | Graph/commit 装饰、Store、全局 trace 状态 |

建议的唯一语法是：

```python
node = LoggedNode(log_sink, fields=node_fields)(
    ObservedNode(observability_port, span_factory)(base_node)
)

commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(event_sink)(persistence_commit)
)
```

如果项目有意支持“手工传入 `inner` 的 wrapper”而不是装饰器工厂，必须改写用户契约和设计文档；两种形状不能并存。

## 4. 阻断问题

### P0-1：公开对象不是标准链式装饰器

证据：

- [`logging/node.py:50`](../src/mote_kernel/logging/node.py#L50) 的构造签名为 `LoggedNode(inner, sink, ...)`；
- [`logging/commit.py:45`](../src/mote_kernel/logging/commit.py#L45) 的构造签名为 `LoggedGraphCommit(inner, sink, ...)`；
- [`observability/node.py:50`](../src/mote_kernel/observability/node.py#L50) 的构造签名为 `ObservedNode(inner, port, span_factory)`；
- [events/commit.py:43](../src/mote_kernel/events/commit.py#L43) 已经明确采用 `EventingGraphCommit(config)(inner)`。

因此以下目标写法当前均不能工作：

```python
LoggedNode(log_sink)(base_node)
ObservedNode(observability_port, span_factory)(base_node)
LoggedGraphCommit(log_sink)(persistence_commit)
```

当前实现可以通过 `LoggedNode(ObservedNode(base, ...), sink)` 进行人工嵌套，但这只是可调用对象嵌套，不是统一的链式 decorator contract；也无法直接使用 `@LoggedNode(...)` 形式。

影响：

- Role/Flow assembly 必须知道每个 wrapper 的私有参数顺序；
- logging、observability 与 events 的装配协议不一致；
- 继续添加诊断层时容易出现多种构造形状，违反“单一公共入口、无兼容别名”的规则。

处理要求：

1. 选择 `Decorator(config)(inner)` 作为唯一公共形状；
2. 将执行生命周期放到私有 wrapper，公开对象只保存不可变配置并返回 wrapper；
3. 删除旧构造方式及其测试，不保留兼容重载或别名；
4. 用 node 顺序、commit 顺序、异常和并发复用测试锁定链式语义。

### P0-2：`inner=None` 让 logging 成为第二个 commit 确认者

证据：

- [`logging/commit.py:39-43`](../src/mote_kernel/logging/commit.py#L39) 把 `inner=None` 描述为进程内确认模式；
- [`logging/commit.py:76`](../src/mote_kernel/logging/commit.py#L76) 在没有 inner 时直接返回 `transition.candidate_state`；
- execution 的唯一确认边界是 [`family_driver.py:127-153`](../src/mote_kernel/execution/family_driver.py#L127)，而 `scoped_commit()` 已经在 [`family_driver.py:156-170`](../src/mote_kernel/execution/family_driver.py#L156) 处理 `commit=None`。

实际行为可以绕过用户持久化 callback：

```text
Graph.run(commit=LoggedGraphCommit(None, sink))
  -> logging 直接返回 candidate
  -> 原本应调用的 persistence callback 次数为 0
```

这不是单纯的日志旁路，而是一个可被误用的 process-local commit path。它与设计文档中“inner commit 恰好一次”和“logging 不成为第二个 commit owner”相冲突。

处理要求：

- 公开 `LoggedGraphCommit` 必须要求一个真实的 `Graph.Commit`；
- `commit=None` 的进程内确认只能保留在 execution owner 内部；
- 增加“持久化 callback 恰好一次”和“缺失 inner 在装配期失败”的测试；
- 不用另一个公开 helper 来替代 `None` 分支。

## 5. 高优先级问题

### P1-1：required inner/port 没有在装配期 fail fast

当前以下非法配置都能成功构造：

```python
LoggedNode(object(), object())
LoggedGraphCommit(object(), object())
ObservedNode(object(), object(), object())
```

对应代码：

- [`logging/node.py:56-63`](../src/mote_kernel/logging/node.py#L56) 只验证 event 和 fields；
- [`logging/commit.py:49-51`](../src/mote_kernel/logging/commit.py#L49) 只验证 event；
- [`observability/node.py:40-52`](../src/mote_kernel/observability/node.py#L40) 没有 `__post_init__`。

运行时后果不一致：

| 非法依赖 | 当前表现 |
| --- | --- |
| 非 callable `inner` | 直到节点/commit 执行才失败 |
| 缺少 `sink.write` | `_write()` 捕获错误，静默丢日志 |
| 缺少 `port.record` | `_record()` 捕获错误，静默丢 observation |
| 非 callable `fields_factory` | 每次调用退回静态字段 |
| 非 callable/非法 `span_factory` | 运行到节点前才失败 |

这与“required ports 缺失必须 assembly fail”不一致。建议在装饰器应用阶段校验：

- `inner` 可调用；
- `LogSinkPort.write`、`ObservabilityPort.record` 可调用；
- `fields_factory`（配置时存在）和 `span_factory` 可调用；
- `LoggedGraphCommit` 不接受空 inner。

诊断写入本身仍可保持 best-effort；“依赖没有装配”与“已装配 adapter 在运行时暂时失败”必须是两种不同边界。

### P1-2：span factory 的失败语义没有与“纯旁路”闭合

[`observability/node.py:54-57`](../src/mote_kernel/observability/node.py#L54) 在调用 inner 前执行 `span_factory()`，工厂异常或返回非法 `Span` 时，inner 不会运行。当前测试 [test_observability.py:292-305](../tests/observability/test_observability.py#L292) 将此行为固化为预期。

这里需要明确一个设计选择：

- 若 span factory 是 required capability 的契约校验，必须在 assembly 阶段拒绝非法配置，并在文档中明确运行期工厂异常的错误归属；
- 若“只做旁路”覆盖所有诊断构造失败，则工厂异常应降级为无 observation 并继续执行 inner，且保留 inner 的结果/异常/取消身份。

在选择前，不能同时宣称“所有观测失败都不影响节点”和“span factory 失败阻止节点”。

## 6. 中优先级问题与范围边界

### P1-3：Role/Flow 尚未真正接入装饰器

生产代码中没有 Role/Flow assembly 使用这些 decorator；[`role/__init__.py`](../src/mote_kernel/role/__init__.py) 目前只有模块说明。当前交付因此更准确地称为“契约、值对象和 wrapper 原型”，还不是完整的可选能力接入。

如果本轮目标仅是建立 Kernel contract，应在设计文档中明确“assembly integration deferred”；如果目标是可直接启用 logging/observability，则需要补一条 Role assembly 路径，并确保缺少可选 Port 时在构图阶段直接使用原 node/commit。

### P2-1：根包的 `__all__` 正确，但子模块属性仍泄漏

[`logging/__init__.py`](../src/mote_kernel/logging/__init__.py) 和 [`observability/__init__.py`](../src/mote_kernel/observability/__init__.py) 的 `__all__` 分别只列出两个和一个 decorator，这满足 `from package import *` 的最小入口。

但导入根包后，Python package namespace 仍包含 `commit`、`node`、`port`、`record`、`span` 等子模块属性。若“对外只暴露”仅指文档化/`__all__` API，可接受；若还要求 `dir()`/`hasattr()` 只出现 decorator，需要额外的包边界测试和实现决策。不要通过兼容别名或动态隐藏制造第二套入口。

### P2-2：复杂度 ratchet 出现可解释但未定案的重复信号

当前新增代码被高召回分析标记出：

- `logging/commit.py:_write` 与 `logging/node.py:_write`；
- `logging/record.py:require_log_label` 与 `observability/span.py:require_observation_label`；
- 两个模块中相似的 scalar value 校验。

这不是立即重构许可。应先定案 decorator 形状和 owner；之后只在能净减少总复杂度、且不引入宽泛 `common/utils` 或跨边界 helper 时整理。机械抽取共享工具会违反当前 package 约束。

### P2-3：源代码管理状态会阻断可复现交付（初审快照）

初审时以下实现、测试和设计文件均为未跟踪文件（`??`）：

- `src/mote_kernel/logging/*.py`；
- `src/mote_kernel/observability/*.py`；
- `tests/diagnostic_logging/*`、`tests/observability/*`；
- `docs/logging-observability-design.zh-CN.md`。

两个已跟踪的 `__init__.py` 已经 import 这些模块；如果交付只包含 tracked diff，干净检出会出现 `ModuleNotFoundError`。本问题不是通过代码兼容层解决，而是交付前明确纳入版本控制并检查干净检出。二次复核时，以上 logging/observability 实现、测试和设计文档已经由 `45ab874` 纳入 HEAD；本条历史结论不应再写入整改计划的当前状态。`events` 实现、整改计划和本评审文档仍需按交付策略明确纳入哪个 commit。

## 7. 已通过的边界

以下方面与文档原则一致：

- 根包 `__all__` 只列出 `LoggedNode`、`LoggedGraphCommit`、`ObservedNode`；
- 未引入 OpenTelemetry、Langfuse、日志框架、网络 client、Store、scheduler 或 exporter；
- logging/observability 不创建 Graph、不维护执行循环、不保存 run snapshot；
- `LogRecord`、`LogField`、`Span`、`SpanContext` 及 observation record 都是 frozen/slots 的有界值；
- 默认 lifecycle 记录不携带 node 输入、输出、异常对象或异常消息；
- 被装饰 node/commit 的普通异常和 `asyncio.CancelledError` 在现有测试中保持身份传播；
- sink/port 的普通旁路失败不会替换被装饰 callable 的正常结果；
- Observability 没有额外的 `Graph.Commit` 装饰器；
- span/field factory 按 invocation 生成值，wrapper 实例本身不持有运行状态。

这些优点不能抵消 P0 的公共形状和 commit owner 问题，但可以作为后续修订的保留基线。

## 8. 建议收敛顺序

按以下顺序一次性收敛，不保留过渡双路径：

1. **先定 API**：统一三个 decorator 为 `Decorator(config)(inner)`，同步设计文档、示例和根包测试；
2. **移除第二 commit 路径**：删除 `LoggedGraphCommit(inner=None)`，把 process-local confirmation 留给 execution；
3. **补 assembly validation**：required inner、sink/port、factory 在装饰器应用时 fail fast；
4. **定旁路异常政策**：明确 span factory 是契约错误还是可丢弃诊断错误，并分别测试普通异常、取消和非法返回；
5. **补链式行为测试**：顺序、嵌套、并发复用、inner 恰好一次、commit exact/mismatch、sink 失败隔离；
6. **接入 Role/Flow（若属于本批范围）**：optional Port 缺失时在 assembly 直接移除 wrapper；
7. **纳入版本控制并做 clean checkout 验证**；
8. **最后再处理复杂度 ratchet 与全仓既有 failover 类型错误**。

## 9. 验收条件

本评审建议只有在以下条件全部满足后关闭：

- `inspect.signature` 和类型检查都显示三个公开对象遵循同一 decorator-factory 形状；
- 不存在 logging 自己确认 candidate 的分支；
- 非法 required capability 在 assembly 阶段失败，optional capability 缺失时不安装 wrapper；
- 旁路失败不会改变 inner 的返回值、异常对象或取消传播；
- 没有第二状态模型、第二 runner、第二 reducer 或 backend import；
- 目标测试覆盖链式顺序与所有异常边界，目标目录覆盖率保持 100%；
- 干净检出可以直接 import 两个根包；
- `make check` 的阻断项已修复，或在交付记录中明确列出与本目录无关的既有失败。

## 10. 验证记录

截至 2026-09-01：

```text
python -B -m pytest tests/diagnostic_logging tests/observability \
  tests/architecture/test_diagnostic_boundaries.py -q
25 passed

target logging/observability coverage (line + branch)
100%

pyright src/mote_kernel/logging src/mote_kernel/observability \
  tests/diagnostic_logging tests/observability
0 errors
```

`make check` 的 Ruff 检查和格式检查通过；全仓 pyright 在 failover 测试处报告 169 个既有类型错误，因此未达到全仓绿色。当前工作树还包含其他模块的用户改动，本轮没有运行会改写工作树的全仓 pre-commit。

## 11. 整改计划二次复核（2026-09-01）

### 11.1 判定

**结论：方向正确，但有条件退回，暂不批准按当前计划直接实施。**

计划已经覆盖初审的两个 P0：三个入口统一为 `Decorator(config)(inner)`，并删除
`LoggedGraphCommit(inner=None)` 的第二条确认路径；同时保留了 execution 的唯一状态/commit owner 和诊断旁路定位。
不过，以下问题会使计划在干净提交、严格类型和取消控制上无法闭环。至少修订完 P0-3、P1-4、P1-5 后再开始阶段 A。

### 11.2 必须先修订的条目

#### P0-3：events 依赖与“独立 commit”策略互相矛盾

计划第 4 阶段 C（约第 175 行）要求用
`LoggedGraphCommit(log_sink)(EventingGraphCommit(event_sink)(persistence_commit))` 做组合回归，
第 5 节也把 `events/commit.py` 列入影响面；但第 8 节又明确 `events` 不得混入 Logging/Observability 独立 commit。
当前 `EventingGraphCommit` 实现和 `tests/events` 仍是工作树未跟踪内容，HEAD `45ab874` 只包含旧的
`events/__init__.py`。因此按计划创建 clean commit 后，C 阶段测试不能导入该 decorator。

计划必须二选一并写出顺序：

1. 将 events 实现作为已提交的前置依赖，并在计划中记录依赖 commit；或
2. 本批 logging 测试只使用本地 typed `Graph.Commit`，跨包组合测试延后到 events 合入后。

仅写“events 不得混入”而不声明依赖顺序，不能作为可执行交付计划。

#### P1-4：严格泛型方案没有落到公共签名

阶段 A 要求公开类去掉 `inner`、私有 wrapper 保留泛型，但没有规定配置类的泛型形状。若直接保留当前
`LoggedNode[InputT, OutputT]` / `ObservedNode[InputT, OutputT]`，而两个类型变量只出现在 `__call__`，
`LoggedNode(log_sink)(base_node)` 在 strict pyright 下会推断出 `Unknown -> Awaitable[Unknown]`；
`LoggedGraphCommit` 也有同样风险。

计划应明确采用以下形状并用正向类型测试锁定：

- 公开配置类是非泛型、frozen/slots；
- `__call__` 使用方法级 `TypeVar`，参数和返回值保持同一 callable 契约；
- 私有 `_LoggedNode`、`_ObservedNode`、`_LoggedCommit` 承担 `Generic` 生命周期实现；
- 用 `assert_type` 或独立 pyright fixture 验证 node、commit 链不出现 `Unknown`，而不是只验证旧构造方式的
  `0 errors`。

公共签名应明确类似下面的形式（具体字段可按各 decorator 调整）：

```python
class LoggedNode:
    def __call__(
        self,
        inner: NodeOperation[InputT, OutputT],
        /,
    ) -> NodeOperation[InputT, OutputT]: ...
```

同时固定配置字段的 positional/keyword 规则（建议可选字段 keyword-only），避免旧形状因参数错位而“偶然可用”。

#### P1-5：取消语义没有按来源闭合

第 3.2 节规定 span factory 抛出 `asyncio.CancelledError` 时吞掉并继续 inner；但
[`events-design.zh-CN.md`](./events-design.zh-CN.md#L101-L105) 要求取消继续传播，execution 也把
`CancelledError` 作为控制信号。第 3.1 节的“sink/port 写入异常被隔离”又没有说明 factory、inner、commit
inner 和 eventing inner 的差异。

请在计划中给出逐来源矩阵，至少覆盖：

| 来源 | 应否继续 inner/commit | 应否向上继续取消 | 必须测试 |
| --- | --- | --- | --- |
| node/commit inner | 否 | 是，保持原对象 | 结果、异常和取消 identity |
| fields factory | 是（降级字段） | 需明确 | 普通异常、取消、非法返回 |
| span factory | 计划当前写“是” | 需明确如何区分调用方取消 | 普通异常、取消、非法 `Span` |
| logging sink / observation port | 是 | 需明确 sync adapter 的取消处理 | 普通异常、显式取消 |
| Eventing/persistence inner | 否 | 是，遵守 events/execution 协议 | 持久化次数和取消传播 |

如果无法可靠区分 adapter 自己抛出的 `CancelledError` 与调用方取消，默认不应吞掉该信号；无论最终选择哪一项，
都要同步 design 文档，不能只在测试中固化。

#### P1-6：runtime capability validation 仍不够具体

第 3.1 节列出了 `write`、`record`、factory 和 inner，但没有规定窄 validator 的实现、稳定异常类型以及
capability bundle 的完整性。请补充：

- 直接验证 `LogSinkPort.write`、`ObservabilityPort.record` 和 inner 的 callable 边界，不使用 `getattr`、
  `inspect` 等反射式通用探测，遵守 `AGENTS.md`；
- 明确缺失属性/错误签名使用 `LogContractError`、`ObservationContractError` 还是专门的 assembly error；
- 装配期不调用 `fields_factory` / `span_factory`，也不以调用结果推断持久化；
- `observability_port` 与 `span_factory` 视为完整 bundle：一方存在而另一方缺失时是 fail assembly 还是整体跳过；
  orphan factory 也要有明确规则；
- 保留同步 `write/record` 契约，拒绝把返回 coroutine 悄悄丢弃；
- “没有真实 persistence callback”改为可判定的 `commit is None`。任何提供的 `Graph.Commit` 都原样调用，
  不做 durability/Store introspection。

阶段 A 的完成标准“旧形状不能通过运行时装配”也应收窄为：不提供兼容路径，正常类型检查和正常配置会失败；
不对一个结构上同时满足两种形状的极端对象承诺运行时可区分。

#### P1-7：Role/Flow 范围与验收条件没有闭合

阶段 D 已正确承认当前 `role/` 没有真实 assembly API，但目标和验收条件仍把 optional capability assembly
写成完成条件。请明确本计划是“Kernel decorator contract remediation”，Role 接入属于后续独立计划；或把
Role assembly owner 作为本计划的前置条件。不能为了通过测试在 `tests/` 中建立临时 assembly owner。

#### P1-8：复杂度 ratchet 的交付门禁未定案

当前工作树运行复杂度门禁失败，配置值到实际值至少包括：

```text
top_level_definitions 520 -> 637
type_definitions       309 -> 395
complexity_hotspots      47 -> 57
logical_clone_pairs      13 -> 17
statement_clone_pairs     7 -> 23
```

这些数字包含 failover/events 等其他工作树改动，不能全部归因于本整改；但计划只记录了 failover 的 169 个
pyright 错误，没有给出复杂度基线策略。阶段 A 还计划新增三个私有 wrapper，可能继续增加定义数和调用边。
请增加“隔离整改 diff 前后报告”、允许的净增长预算及架构审查要求；不得只改 `pyproject.toml` ratchet 数值来
掩盖失败。

### 11.3 需要补齐的交付与验收项

1. 根包名称不变不等于 API 不变：从 `LoggedNode(inner, sink)` 到 `LoggedNode(sink)(inner)` 是有意的 breaking
   change。必须更新 [`CHANGELOG.md`](../CHANGELOG.md)、README/设计示例，并用检索门禁清除旧构造形状；不提供兼容别名。
2. 计划第 6 节的未跟踪文件清单已过时：logging/observability 实现、测试和设计文档已在 HEAD；当前主要未跟踪的
   是计划、评审、events/failover 等工作树文件。交付清单应按目标 commit 重新列出，并做 clean checkout import。
3. 计划文件第 3、4 行含行尾空格（pre-commit 的 `trailing-whitespace` 会拒绝）；提交前先修正文档格式。
4. “logging 不自行确认 candidate”措辞过强且与 accepted/mismatch 记录并列。允许 wrapper 做只读比较以分类日志，
   但不得用比较结果替代 execution 的 exact-candidate 校验、提前安装内存快照、短路 inner 或改写返回值。
5. 验收测试还应锁定：每次 invocation 的 fields/span factory 各调用一次；node/commit inner 各调用一次；返回值、
   异常对象和取消对象保持 identity；logging 的 transition 字段投影失败不阻断 commit inner，而 events 的
   event projection 仍按其设计在 persistence 前失败；不记录 input/output、异常对象或异常消息；同一 decorator
   并发复用不共享字段/span 快照；optional bundle 缺失、sync `write/record` 和 `Graph.run(commit=None)`（不经过
   logging）均有明确测试。

### 11.4 二次复核后的保留项与验证记录

计划中以下部分可以保留：

- 三个入口统一为两阶段 decorator，且不添加 alias、第二 runner 或第二状态模型；
- `LoggedGraphCommit` 只观察 inner 返回，execution 保留唯一 commit owner；
- sink/port 的运行期故障与 assembly 缺陷分层；
- span/field identity 按 invocation 生成，wrapper 不持有 run 状态；
- Role 接入不反向依赖诊断包，events 继续作为独立 owner。

本次只读验证（基于当前工作树）：

```text
pytest tests/diagnostic_logging tests/observability tests/architecture/test_diagnostic_boundaries.py
25 passed
目标 logging/observability 分支覆盖率
100%
pyright（目标源码与测试，strict）
0 errors
复杂度 ratchet
failed（见上方配置值与实际值）
```

这些绿色结果仍主要覆盖旧的 `Decorator` 构造方式，不能作为新链式 API 已实现的证据。修订计划、确定依赖顺序、
补齐类型/取消/装配决策后，才可将状态从“待实施”改为可执行；在此之前评审保持阻断。
