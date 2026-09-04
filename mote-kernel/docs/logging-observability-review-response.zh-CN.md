# 对 Logging 与 Observability 实现评审的回复

状态：**整改已实施；Invocation Port 修订已补充；专项门禁通过；全仓无关失败已记录；未提交**

回复日期：2026-09-02

**2026-09-04 补充：**针对后续复核发现的调用链问题，本轮又固定了三条规则：诊断调用有默认 1 秒的有限协作式
deadline（`timeout_seconds` 只能是有限正数）；inner 已返回或抛出后，后置诊断收到取消不得覆盖 primary outcome；
`invoke_best_effort` 为 deadline 保留一个取消计数，额外的调用方取消仍传播；它只把 Invocation 自身错误或 deadline 视为可丢弃失败。
适配器主动 `uncancel()` 的来源恢复暂不纳入本轮契约。

对应评审：[`logging-observability-review.zh-CN.md`](./logging-observability-review.zh-CN.md)

整改计划：[`logging-observability-remediation-plan.zh-CN.md`](./logging-observability-remediation-plan.zh-CN.md)

## 1. 最终裁决

接受评审对当前实现的两个核心阻断：

1. 三个公开对象必须统一为 `Decorator(config)(inner)`；
2. 必须删除 `LoggedGraphCommit(inner=None)`，进程内确认只留在 execution owner。

二次复核中有关严格泛型、Events 依赖顺序、失败来源矩阵、Role 范围、复杂度隔离、breaking change 和交付历史的有效意见
也已进入整改计划。

用户进一步裁决：

- 业务 inner 或 Graph execution 收到的 `asyncio.CancelledError` 是停止信号：先尽力记录诊断，再原样传播同一个取消对象；
- sink/port 是基于共享 `Invocation` 的 async 诊断旁路。Port 在边界固定使用 best-effort；适配器自己误抛普通异常或
  `CancelledError` 时，只丢弃当前诊断，不改变业务结果、异常、取消或 mismatch，也不停止 inner；
- 观测 decorator 不负责校验 Port、factory 或 inner 是否装配正确；
- 本轮只完成 Logging/Observability 装饰链，不依赖 Events；
- Role/Flow assembly 不属于本轮；
- assembly error 不由观测 node 或日志 decorator 定义；
- “最小公共面”只表示两个根包合计通过 `__all__` 暴露三个 decorator。

## 2. 逐项处置

| 评审项 | 结论 | 处置 |
| --- | --- | --- |
| P0-1：不是两阶段 decorator | 接受 | 改为唯一 `Decorator(config)(inner)` |
| P0-2：Logging 存在 `inner=None` fallback | 接受 | 删除分支，不提供替代 helper |
| P0-3：Events 依赖与独立 commit 矛盾 | 接受 | 本批不依赖 Events，组合测试延后 |
| P1-1：decorator 应校验 required Port/inner | 不接受 owner 判断 | strict typing + 后续 Role assembly 负责 |
| P1-2：span factory 失败语义不清 | 接受澄清问题 | span factory 是 required invocation setup；实现 bug 或非法 Span 自然失败，inner 不执行 |
| P1-3/P1-7：Role 范围不闭合 | 接受 | Role 完全移出本轮实施与验收 |
| P1-4：公开配置类泛型产生 `Unknown` | 接受 | 非泛型配置类 + 方法级 TypeVar + 私有泛型 wrapper |
| P1-5：取消来源未闭合 | 接受并按来源分层 | 业务 inner/Graph execution 的取消保持 identity；sink/port 自有普通异常或 `CancelledError` 只丢弃当前诊断 |
| P1-6：runtime capability validation 不具体 | 不按原方向实施 | decorator 不做 assembly validation 或反射 |
| P1-8：复杂度门禁未定案 | 接受隔离审计 | clean baseline + 结构变化参考线；不直接抬 ratchet，也不为贴合指标保留真实债务 |
| P2-1：根包子模块属性可见 | 不处理 | `__all__` 是公共面，不动态隐藏 Python 子模块 |
| P2-2：局部重复信号 | 接受审计原则 | 不机械抽 `common/utils` |
| P2-3：交付状态 | 接受二次复核事实 | 最终 amend/squash 为一个修正 commit |
| Breaking change、identity、安全、并发测试 | 接受 | 已进入测试与交付矩阵 |

## 3. Span factory 没有受支持的失败模式

`span_factory` 的唯一职责是为本次 invocation 构造一个新的 immutable `Span`，例如填入：

- trace id；
- span id；
- parent span id；
- node/span name；
- 已准入的静态 attribute。

它不应调用 backend、网络、Store、exporter，也不负责上传、缓冲、采样或重试。在受支持契约内，它没有需要恢复或降级的
失败模式：调用方提供可并发安全的零参数 factory，factory 同步返回合法 `Span`。

Python 调用在机械上仍可能因为 factory 实现 bug、构造非法 `Span`，或收到 `CancelledError` 而抛出异常，但这不构成
Observability 定义的一类“factory 运行故障”。`ObservedNode` 不预检 factory，也不捕获后改成“无 span 继续执行”：实现
异常和 `Span`/observation 值契约错误自然向上传播，base node 不执行；若 factory 自身抛出 `CancelledError`，也按 setup
失败自然结束。factory 所需的
run/scope/trace/parent 坐标由调用方通过闭包或自己的 context 机制捕获，Kernel 不创建隐藏上下文，也不在运行期探测装配。

与之不同，`ObservabilityPort.record()` 已经拿到合法 immutable observation 后发生的普通 backend 输出失败或适配器误抛
`CancelledError`，都是 best-effort 旁路错误：只丢弃当前 observation，不改变被装饰 node 的业务结果。

## 4. 取消语义：业务取消与诊断适配器失败分层

用户通过 UI、控制面或上层任务发出的停止，若由业务 inner 或 Graph execution 收到，才是本轮要保持的业务取消：wrapper
先尽力记录失败/取消诊断，再原样传播同一个 `asyncio.CancelledError` 对象。sink/port 则是 async best-effort 旁路；日志或观测适配器自己
误抛普通异常或 `CancelledError` 都只是当前诊断失败，必须隔离，不改变业务返回值、业务异常、业务取消或 mismatch，也不停止
inner、不触发重试或重放。

| 来源 | 处理 |
| --- | --- |
| node/commit inner 或 Graph execution | 记录诊断后原样传播同一个取消对象；普通异常同样保持原样 |
| fields factory | 普通/非法字段结果回退静态 fields；自身 `CancelledError` 不在回退范围；只捕获其约定的普通失败，不用 `BaseException` 泛吞系统中断 |
| span factory | required invocation setup；实现 bug 或非法 Span 自然失败，inner 不执行 |
| logging sink | 普通异常或误抛 `CancelledError` 只丢弃当前 record，继续 inner 或保留已产生结果 |
| observability port | 普通异常或误抛 `CancelledError` 只丢弃当前 observation，继续 inner 或保留已产生结果 |
| persistence/Eventing | 由各自 owner 定义和传播，本轮不改写 |

因此只在已知 sink/port 诊断调用边界显式隔离适配器自己的 `CancelledError`；不能使用 `except BaseException` 把
`KeyboardInterrupt`、`SystemExit` 等系统级中断一并吞掉，也不能把诊断适配器的异常升级成新的业务取消。若 inner 已经因
普通异常失败，failure record 写入失败时仍重新抛出原 inner 异常；若 inner 已收到取消，
诊断适配器失败也被丢弃，重新抛出同一个取消对象。若 commit inner 已经返回，随后 accepted/mismatch 日志写入失败，只丢弃
当前诊断并原样返回 inner，绝不重试 inner。真正的外部提交在取消后是否需要 recovery、重放或 reconcile，仍由 execution
与外部 adapter 的既有契约负责；外部 commit adapter 自行处理幂等、去重或 reconcile，Logging 不建立补偿提交、durability、
ack 恢复或第二确认路径。

## 5. 不接受：由观测 decorator 校验 Port 装配

评审要求 `ObservedNode`/`LoggedNode` 在配置或应用时检查：

- `port.record` / `sink.write` 是否存在且 callable；
- factory 是否 callable；
- inner 是否 callable；
- Port 是否绕过共享 Invocation 或自行选择 transport。

这些判断属于 assembly owner，不属于观测 node。Decorator 应当相信自己收到的 typed capability，只负责调用窄协议和
处理协议调用结果。否则每个使用 Port 的节点都会重复一遍装配器职责，并出现不同的探测规则和错误类型。

本轮边界因此固定为：

1. Protocol 和方法级 TypeVar 表达静态契约；
2. strict Pyright 拒绝 `None`、非 Invocation capability、错误 callable 以及跨 `GraphValueT` 组合；
3. 未来 Role/Flow assembly 决定 optional capability 是否存在；要安装 `ObservedNode` 时保证
   `port + span_factory` bundle 完整（两者都缺则不安装、只缺一个则 assembly error），但 `ObservabilityPort` 仍可独立
   服务其他 usage/timing/error 记录；
4. decorator 不使用 `getattr`、`inspect`、`callable()`、运行时 Protocol 扫描或试调用；
5. 调用方通过 `cast` 或关闭类型检查强行传入非法对象，不属于受支持公共契约。

各值对象仍校验自己拥有的值契约，例如 event、静态 fields、factory 返回的 fields，以及由 observation record 构造器
准入的 Span。这是值 owner 的职责，不是 `ObservedNode` 对 factory 或 Port 做 assembly validation。

因此不新增统一 assembly error，也不规定缺 Port 时由 decorator 抛 `TypeError`。旧构造形状的错误 positional/keyword 组合由
Python 签名自然拒绝；本轮不额外强制 required 参数 positional-only，因为新签名已经移除了旧的 `inner` 参数。真正的装配错误以后
由 Role/Flow owner 定义。

## 6. Events 与 Role 不属于本轮

### 6.1 Events

本轮只负责以下链：

```text
base node
  -> ObservedNode
  -> LoggedNode

Graph.Commit
  -> LoggedGraphCommit
```

Logging commit 测试使用测试内 typed persistence callback，不 import 当前未交付的 Events。等 Events 独立合入后，再由
Events owner 验证：

```python
LoggedGraphCommit(log_sink)(
    EventingGraphCommit(event_sink)(persistence_commit)
)
```

因此接受评审指出的原计划依赖矛盾，但不让本批复制 event projection-before-persistence、async event cancellation 等
Events 契约测试。

### 6.2 Role/Flow

最终装饰链仍应由 Role/Flow assembly 组装，但当前 `role/` 尚无真实 assembly API。它不是本轮要做的内容，也不是本轮
验收条件。不能为了验证 optional capability 而在测试中临时发明一个 assembly owner。

## 7. 公共面裁决

“只暴露最小公共面”的精确定义是：

```text
mote_kernel.logging.__all__       = LoggedNode, LoggedGraphCommit
mote_kernel.observability.__all__ = ObservedNode
```

总计只有三个根包 decorator。Port、record、span 仍位于明确子模块中，供 adapter 实现显式导入，但不从根包重复导出。

Python import 后正常出现 `logging.node`、`observability.span` 等子模块属性，不属于新增公共入口。本轮不增加
`__getattr__`、动态删除 attribute 或定制 `dir()` 来隐藏它们。

## 8. 复杂度与交付回复

接受评审要求的复杂度审计，但必须隔离归因。当前全工作树数字同时包含 Events、Failover、Cloudflare 和分析器改动，不能
全部归因于本整改。

整改计划以旧 Logging/Observability tree 做 before snapshot，只应用目标 diff；当前共同父提交为
`008848c05136c6252538d1f61de1b7687b99be7c`，旧实现 tree 为 `695849ebd2c3e093a7aaf98c21ddd8c57efb2e97`。
报告分开列出 production structural budget、test-only 增量，以及 clone、hotspot、最大复杂度和 health debt。指标只是高召回
风险线索，仍须结合代码判断新增结构是否必要、简洁、可解释且便于人类维护；预算不自动授权修改 `pyproject.toml`，也不能
用 ratchet 掩盖问题。

远端已撤回 `45ab874`，本地仍保留它。本轮目标代码和文档已完成，但仍未 commit、amend 或 push；待用户再次授权后，才把
原实现和整改 amend/squash 为一个修正后的 feature commit，而不是推送“问题实现 + 修复”两段历史。相关
Logging/Observability 评审文档作为审计记录随目标文档集保留；Events、Failover、Cloudflare 和其他用户改动不得混入。

## 9. 原始实现评审的处置结论

评审中合理的架构问题已经落入整改计划；不合理的 owner 扩张已经明确拒绝：

- decorator 不成为 assembly validator；
- Logging/Observability 不承担 Events 集成；
- 本轮不设计 Role；
- 根包公共面用 `__all__` 定义，不做动态隐藏；
- 复杂度只对隔离目标 diff 归因。

计划已完成裁决，整改已经实施。专项门禁已通过；全仓门禁中的失败来自其他工作树改动，未修改这些文件或门禁规则，且本轮
仍未 commit、amend 或 push。

## 10. 对整改实施计划复审的处置

对应复审：[`logging-observability-remediation-plan-review.zh-CN.md`](./logging-observability-remediation-plan-review.zh-CN.md)。

复审意见已逐项处置；接受的边界已写入实施计划，不采用的建议及原因如下：

| 复审建议 | 处置 | 原因 |
| --- | --- | --- |
| `sink.write()` / `port.record()` 抛出的 `CancelledError` 作为适配器误抛隔离 | 接受 | 它们是 async best-effort 诊断旁路；只丢弃当前诊断，不改变业务结果、异常、取消或 mismatch，也不停止 inner。 |
| 用“业务 inner/Graph execution 才是业务取消来源”区分取消 | 接受 | 业务取消保持同一对象；诊断适配器自有错误不被提升成业务取消。 |
| 业务取消保持原始 cancellation identity | 接受 | inner/execution 取消经尽力诊断后原样传播；诊断适配器若再误抛 `CancelledError`，只丢弃该诊断，不由 Logging 选择主异常。 |
| required capability 一律改成 positional-only `/` | 不采用 | 新 API 已没有 `inner` 参数，旧 positional/keyword 形状由签名自然拒绝；required capability 按位置示例传入即可，额外的 positional-only 构造器会增加限制和复杂度。 |
| 本轮加入嵌套 Graph recovery/replay 的完整 `Graph.run()` 测试 | 不接受为本轮门槛 | 本轮只验证 decorator contract；保留 facade smoke 与 `commit=None` fallback，跨 recovery 的执行语义由 execution owner 验证。 |
| `port + span_factory` 缺一即把整个 Observability capability 判为错误 | 部分不接受 | 对 `ObservedNode` 的安装采用完整 bundle；但 `ObservabilityPort` 可以独立服务 usage/timing/error 记录，不把 port-only 用法全局禁掉。 |
| 所有 aggregate complexity metric 都必须绝对零增长 | 部分不接受 | 现计划为三个 wrapper 记录必要的 production structural 变化；接受拆分 production/test 报告，不接受用 ratchet 掩盖额外复杂度或为贴合指标保留真实债务。 |

以下复审建议直接采纳：

- 将“恰好一次”限定为单次 wrapper invocation 内不主动重试，不承诺跨 recovery 的全局 exactly-once；
- 说明零参数 fields/span factory 的上下文由调用方闭包或调用方自己的 context 机制提供，Kernel 不维护隐藏上下文；
- 增加真实 `@Decorator(...)` 语法、旧 keyword/泛型下标和负向类型 fixture 验证；
- 将 transition projection 的普通错误降级与诊断适配器误抛 `CancelledError` 隔离分别写入矩阵；
- 把 production/test 复杂度、旧实现 tree snapshot、clean checkout 和文档链接闭合作为交付门禁；
- 历史评审中的旧构造示例和初审快照保留为历史证据，不把它们当作当前实现状态。

复审提到的行尾空格目前已由门禁清理；相关文档当前无 trailing whitespace。计划中跨章节重复的矩阵/验收句子是为了让
实施步骤与最终条件互相对照，不视为需要新增抽象的代码问题。

因此，复审结论是：**方向可行，但按修订后的明确边界实施。**
