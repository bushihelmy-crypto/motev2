# Logging 与 Observability 整改实施计划复审

状态：**方向可行，但按修订后的明确边界实施**

审查日期：2026-09-02

审查对象：

- [`logging-observability-remediation-plan.zh-CN.md`](./logging-observability-remediation-plan.zh-CN.md)
- [`logging-observability-review-response.zh-CN.md`](./logging-observability-review-response.zh-CN.md)
- [`logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md)
- [`logging-observability-review.zh-CN.md`](./logging-observability-review.zh-CN.md)

本文是对整改计划的复审记录。本文只修订文档边界，不修改生产代码、测试或 Events、Failover、Cloudflare 等其他工作树内容。
本轮代码已按计划实施，专项门禁已通过；全仓门禁中的无关工作树失败在计划第 10.2 节记录，不能归因于本整改。

> **2026-09-03 架构修订：** 后续决定将 `LogSinkPort` 与 `ObservabilityPort` 接到共享
> `mote_kernel.invocation.Invocation`。因此本文早先把诊断 Port 称为“同步旁路”的表述均由当前契约替代：Port 现在是
> async 的 typed adapter，接收配置注入的普通 Invocation，并在边界固定使用 best-effort；transport/runtime 仍由
> `mote-infra/invocation` 和配置选择。以下历史评审结论只在不与这条修订冲突的部分继续有效。

## 1. 结论先行

整改方向成立，修订后的计划可以作为实现依据。已将评审中有道理的部分落入计划、回复和设计文档；不属于
Logging/Observability owner 或与用户已确认结果冲突的建议，记录在第 4 节并明确不采用。

本轮冻结的实现边界是：

- 三个公开入口只有 `Decorator(config)(inner)`，配置对象非泛型、frozen、slots；方法级 TypeVar 和私有泛型 wrapper
  保持 inner 的精确类型；
- `LoggedGraphCommit` 只包裹调用方提供的 `Graph.Commit`，不接受 `None`，`Graph.run(commit=None)` 的进程内 fallback
  仍由 execution owner 处理；
- Logging/Observability 只做旁路投影和通知，不创建 Graph、runner、reducer、状态模型，不确认 candidate，不改写 inner
  返回值，也不拥有 Store、durability、重试、exporter 或 Events；
- node 装饰顺序采用 `Logged(Observed(base))` 作为规范/推荐顺序，普通 Python API 不声称能够阻止反向嵌套；
- 本轮不实现 Role/Flow assembly 和 Events 跨包组合。

## 2. 已采纳并已落入整改计划的意见

| 评审主题 | 采纳的明确规则 | 计划中的落点 |
| --- | --- | --- |
| 两阶段 API | 统一为 `Decorator(config)(inner)`，不保留旧 positional、keyword 或泛型下标兼容路径 | 第 2、7、8 节 |
| commit owner | 删除 `LoggedGraphCommit(inner=None)`；Logging 只读记录 exact/mismatch，execution 负责权威校验 | 第 1、2、4 节 |
| 类型形状 | 公开配置类非泛型、frozen、slots；方法级 TypeVar + 私有泛型 wrapper | 第 2、7 节 |
| factory 分层 | fields factory 普通/非法失败回退静态 fields；span factory 是 required invocation setup，非法 Span 或实现错误自然失败且 inner 不执行 | 第 3、4、7 节 |
| factory 上下文 | 零参数 factory 的 run/scope/trace/parent 坐标由调用方闭包或自有 context 机制提供，Kernel 不注入隐藏上下文 | 第 1、3、8 节 |
| 取消与旁路 | 业务 inner/Graph execution 的取消在尽力记录诊断后原样传播同一对象；sink/port 自有普通异常或 `CancelledError` 只丢弃当前诊断 | 第 1、4、7、8 节 |
| bundle | port 与 span factory 都缺时不安装 ObservedNode；只缺一个时报 assembly error；都有时安装完整链；port 仍可独立记录其他观测 | 第 1、3、6 节 |
| exactly-once 语义 | “一次”只限单次 wrapper invocation 内不主动重试；跨 recovery 或新的 `Graph.run()` 由 execution/adapter 负责，外部 commit adapter 自行处理幂等、去重或 reconcile | 第 1、4、8 节 |
| 质量门禁 | production/test 复杂度分开统计，保存可复现 tree/report，clean checkout、类型负例和文档链接纳入交付门禁 | 第 9、10 节 |

这些项目是已确定的实施要求，不再作为待讨论项。

## 3. 取消与失败边界（规范文字）

### 3.1 业务取消

只有业务 inner 或 Graph execution 收到的 `asyncio.CancelledError` 属于本轮要保持的业务停止信号。wrapper 尽力写入
失败/取消诊断，然后重新抛出同一个取消对象。Logging 不选择主异常，不合并两个取消，也不把诊断错误升级成新的业务取消。

### 3.2 诊断适配器故障

`LogSinkPort.write()` 和 `ObservabilityPort.record()` 是 Invocation-backed async 旁路接口。每次调用有有限协作式 deadline（默认 1 秒，
由 Port 的 keyword-only `timeout_seconds` 覆盖为有限正数）；适配器自己误抛普通异常、`CancelledError` 或达到 deadline 时，
只丢弃当前 record/observation：

- pre-inner 的 started 诊断失败不阻止 inner 执行；
- inner 已返回、抛出异常、取消或返回 mismatch 后的诊断失败不改写该业务结果；
- deadline 与调用方取消同时到达时，保留 deadline 的一个取消计数，额外取消仍传播；适配器主动 `uncancel()` 暂不属于本轮契约；
- 不因诊断失败重试 inner，不触发 Graph recovery、重放或 reconcile；
- 不使用 `except BaseException` 吞掉 `KeyboardInterrupt`、`SystemExit` 等系统级中断。

因此，评审中把诊断适配器的异常当作业务取消、停止 inner 或重新选择主异常的表述均不属于当前契约。best-effort 适配器不应主动
制造业务取消。

### 3.3 factory

`fields_factory` 是可选字段补充；它自己的普通异常或非法字段结果回退到已验证的静态 fields；自身
`CancelledError` 不在该回退范围，按 factory 异常自然结束。实现只捕获约定的普通失败，不以 `BaseException` 泛捕获系统中断。

`span_factory` 是调用前的必需 invocation setup。它不访问网络、Store、backend 或 exporter；非法 `Span`、实现 bug 或
自身 setup 异常自然失败，base node 不执行，不增加“无 span 继续”的恢复分支。每次 invocation 各自调用 factory 一次，
wrapper 不保存运行状态。

## 4. 不采用的评审建议与回复

| 评审建议 | 处置 | 理由 |
| --- | --- | --- |
| 让 decorator 运行时用 `getattr`、`inspect`、`callable()` 或试调用校验 Port/factory/inner | 不采用 | capability 存在性、optional bundle 和 assembly error 属于 Role/Flow owner；decorator 只依赖窄 typed Protocol。 |
| 把 required capability 强制写成 positional-only `/` | 不采用 | required capability 放在首位并按位置示例传入即可；可选配置 keyword-only。额外 positional-only 限制不增加契约价值。 |
| 本轮实现 Role/Flow assembly 或在测试中伪造 assembly owner | 不采用 | 当前范围只关闭 decorator contract；未来 assembly owner 再接入 bundle、顺序和缺失策略。 |
| 本轮导入或实现 Events，并验证跨包 commit 链 | 不采用 | Events 是独立 owner；本轮只用测试内 typed persistence callback。Events 合入后由其 owner 测组合。 |
| 用完整嵌套 Graph recovery/replay 作为本轮门槛 | 不采用为本轮门槛 | 本轮保留 Graph facade smoke 与 `commit=None` fallback；跨 recovery 的重放语义由 execution owner 验证。 |
| 缺少 span factory 时全局禁止 ObservabilityPort 的其他用法 | 不采用 | 完整 bundle 只约束 ObservedNode 的安装；ObservabilityPort 仍可独立记录 usage/timing/error。 |
| 要求所有 aggregate complexity metric 绝对零增长 | 部分不采用 | 新增私有 wrapper 有最小 production structural budget；指标是高召回线索，必须结合结构是否必要、简洁、可解释且便于人类维护来判断；production/test、clone、hotspot、最大复杂度和 health debt 分开报告，不能用 ratchet 掩盖额外复杂度。 |
| 通过动态隐藏子模块属性来定义最小公共面 | 不采用 | 公共面由两个根包的 `__all__` 定义，正常 Python 子模块属性不另行隐藏。 |

以上是不采用的 owner 扩张或不必要复杂化，不是待解决缺陷。

## 5. 公共 API、组装和 `None` 规则

目标构造形状如下（required capability 放在首位并按位置示例传入，可选配置使用 keyword-only）：

```python
@LoggedNode(log_sink, event="role.node", fields_factory=invocation_fields)
@ObservedNode(observability_port, span_factory)
async def base_node(value: Input) -> Output:
    ...

commit = LoggedGraphCommit(log_sink)(persistence_commit)
```

推荐的实际调用链是 `Logged(Observed(base))`；这只是 assembly 规范/推荐顺序，不是普通 Python API 可强制的运行时约束。

`fields_factory=None` 合法，表示不提供动态字段补充；`Graph.run(commit=None)` 合法并走 execution fallback。required
`sink`、`port`、`span_factory` 和 `inner` 不接受 `None`。旧构造形状和公开泛型下标由 strict 类型/签名负例拒绝，不提供兼容
alias 或第二 helper。

Decorator 不做 assembly validation、反射探测或试调用。未来 Role/Flow assembly 按以下 bundle 规则组装 ObservedNode：
两者都缺则不安装，只缺一个时报 assembly error，两者都有才安装；Logging 的可选 sink 缺失时同样由 assembly 直接保留
原 callable。两个包不互相发现或自动重排。

## 6. 测试、复杂度和交付复现

整改实施必须覆盖以下可观察行为：

- 真正的 `@LoggedNode(...)`、`@ObservedNode(...)`、`@LoggedGraphCommit(...)` 语法，以及新旧 positional/keyword/泛型
  下标的正负向边界；负向类型 fixture 继续使用现有 harness，不混入普通 strict Pyright；
- fields/span factory 的调用次数、每次 invocation 的 fresh span identity、并发复用和调用方闭包/context 的坐标；
- inner 普通异常与业务取消的对象 identity；sink/port 自有普通异常或 `CancelledError` 不改变结果、异常、取消、mismatch，
  也不停止、不重试 inner；
- commit exact/mismatch、诊断开始/结束失败、`Graph.run(commit=None)` fallback 和 decorated Graph facade；
- 在 required setup 成功的单次 wrapper invocation 内 inner 只主动调用一次；setup 失败时不调用 inner，且测试不把它误称为跨 recovery 的全局 exactly-once；
- 默认记录不包含 input/output、异常对象、异常消息或 `repr`，wrapper 不保存运行期快照。

复杂度报告必须在隔离 clean checkout 中以旧 Logging/Observability tree 为 before snapshot，只应用目标 diff；production 与
test 指标分开，保存 tree/report，不能用全工作树的 Events、Failover、Cloudflare 或分析器改动解释本整改。`make check`、全仓
pre-commit、专项类型/覆盖率门禁和目标文档链接均须可复现；其他工作树改动不得被格式化或门禁覆盖。

目标交付清单只包含 Logging/Observability 实现、专项测试与类型 fixture、design、plan、response、相关 review 记录及确有
关联的 CHANGELOG/README hunk；不包含 Events、Failover、Cloudflare 或其他改动。`45ab874` 的 tree/report 在最终 amend 前须
保存，任何 commit/amend/push 仍需单独授权。

实施复核结果与计划第 10.2 节一致：目标专项测试 `66 passed`，诊断模块 line/branch coverage `100%`，目标源码与正向
fixture Pyright 为 `0 errors`，负向 fixture harness `32 passed`，clean checkout 定向测试 `65 passed`，package-check 通过。
全仓 `make check`、Pyright 和 pytest 的剩余失败均已逐项定位到其他 execution、hooks 或 failover 工作树改动；本轮没有
修改这些文件、分析器或 ratchet。

## 7. 最终评审结论

**方向可行，但按修订后的明确边界实施。**

文档层面的取消、factory、bundle、API、测试、复杂度和交付边界已经闭合；本轮生产代码整改已完成，专项代码、类型、覆盖率、
Graph facade 和 package 门禁均通过。全仓门禁仍有其他工作树的已知失败，但不归因于本整改，也不阻止本复审关闭本轮 decorator
contract。除上述明确的不采用项外，本复审不留下开放问题，也不扩大本轮范围。
