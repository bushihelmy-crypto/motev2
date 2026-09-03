# Events 代码改动验收评审

审查日期：2026-09-02
审查对象：`src/mote_kernel/events/`、`tests/events/` 及其直接设计文档。execution/state、persistence 和
dispatcher 的缺口作为跨 owner 后续事项记录，不作为本次 Events 包验收对象。

本文件与已确认的 `events-design.zh-CN.md`、`events-implementation-plan.zh-CN.md` 一起作为当前口径；同目录带
`-review` 的早期方案评审保留为历史记录，不覆盖本次已采纳的原子提交设计。

## 结论

**Events 包本次验收通过；可靠投递 vertical slice 尚未验收。**

一句话说明：Events 包的职责是把 settlement 投影成稳定引用，并把它和 candidate 一起交给唯一的原子 persistence
port；重启后还原“节点名称、实际访问参数、执行结果”需要 GraphRunState 快照和 dispatcher owner 提供能力，
不属于这个包的实现。

架构方向本身是对的，以下决定按已确认的基线审核，不再重复讨论：

- `events` 是 commit 装饰器，包住一个原子 persistence commit；Events 不实现数据库、事务或 dispatcher。
- `events` 在内层，`logging` 在外层；包装顺序由 assembly 决定，Events 不负责重排。
- Graph 只等待本地原子提交，不等待远端发送。
- 并行节点不要求全局事件顺序，只要求事件和对应节点事实能按身份配对。
- 持久化实现不属于本期 Events 代码；Events 提供 immutable `AtomicCommitRequest` 作为 owner-internal SPI，
  由 `infra/persistence` 接入。
- 事件的业务含义必须能得到：**节点名称 - 实际访问参数 - 执行结果**。

因此，本次只验收 Events 包自身的投影、原子提交接缝、身份和公共 API；完整快照、真实事务和投递恢复另行验收。

## 已通过的部分

这些部分可以保留，不需要为了修复下面的问题再造一套基础设施：

- [`events/__init__.py`](../src/mote_kernel/events/__init__.py) 只导出 `EventingGraphCommit`，没有 EventBus、sink、
  registry 或第二个 runner。
- [`events/commit.py`](../src/mote_kernel/events/commit.py) 没有创建 task、queue、后台 dispatcher，也没有在
  persistence 返回后再调用旁路通知。
- `project_event()` 是纯投影，只对 `SettleGraphNode` 生成一条引用，其他 transition 仍走同一个 persistence 调用。
- wrapper 对每次调用只调用一次 persistence，异常和取消原样传播，exact candidate 仍由 execution owner 校验。
- 引用使用 run/scope/superstep/node/generation/revision 组成确定性 `event_id`，没有把业务 DTO 复制到引用里。
- 引用现在拒绝非法身份、负数坐标、可变 `scope` 和布尔伪整数，保持幂等地址真正稳定。
- 测试覆盖了 nested scope、并发 run、并行节点身份配对和外层 decorator 的调用顺序；这些测试方向正确。

## P0 跨 owner 后续事项（不阻断 Events 包验收）

### 1. 事件内容没有闭合

`project_event()`（[`projection.py:12-29`](../src/mote_kernel/events/projection.py#L12-L29)）只读取：

```text
run_id / scope / superstep / node_id / execution_generation / settlement_revision
```

它没有读取本次节点的实际输入，也没有读取成功输出、失败结果或中断结果。`NodeSettlementEventReference`
（[`record.py:15-52`](../src/mote_kernel/events/record.py#L15-L52)）也只有这些坐标。

当前运行时的事实分布如下：

| 需要的事实 | 当前实际位置 | 能否随本次原子请求恢复 |
| --- | --- | --- |
| 节点名称 | `SettleGraphNode.outcome.node_id` | 能，坐标里有 node id |
| 实际访问参数 | `ExecutableTask.effective_input`（[`task.py:30-33`](../src/mote_kernel/execution/engine/task.py#L30-L33)） | 不能，只在执行内存 |
| 成功输出 | `TaskSuccess.output` / `transition.result.output` | 不能，未进入 `GraphRunState` |
| 失败原因 | frontier 的 `FailedGraphNode` | 部分能 |
| 中断 payload | frontier 的 `InterruptedGraphNode` | 部分能 |

`GraphRunState` 当前字段只有控制状态、frontier、lease、resource 和 revision（[`model.py:33-49`](../src/mote_kernel/state/graph_state/model.py#L33-L49)），
没有 input/frame/invocation/output/evidence 字段。也就是说，dispatcher 以后即使拿到了当前引用，仍无法稳定组装
用户要求的三项内容。

这不是要求 Events 自己复制一份参数或结果。正确做法是由 execution/state owner 提供一份可恢复的 canonical
execution fact，Events 只投影引用；当前代码连这个 canonical fact 和读取契约都没有。

### 2. 仍然存在第二份事实：`GraphTransition.result`

实施计划明确要求不要再用 optional result/evidence sidecar，但当前 [`GraphTransition`](../src/mote_kernel/execution/family_driver.py#L105-L120)
仍然包含：

```python
result: GraphCommitResult[GraphValueT] | None
```

而且这份 sidecar 实际承载了唯一的成功 output（[`result.py:108-145`](../src/mote_kernel/execution/result.py#L108-L145)）：

- candidate state 的成功 settlement 只有 routing，没有 output；
- `transition.result` 另存一份 output/failure/interrupt；
- Events 的 `AtomicCommitRequest` 只包住整个 transition，没有把这份事实变成明确、可持久化、可恢复的 typed 契约。

结果是两种坏情况二选一：

1. persistence 忽略 `transition.result`，成功事件永远没有结果；
2. persistence 私下读取并保存这个 optional 字段，形成计划明令禁止的旁路事实和不透明协议。

需要由 execution/state owner 收敛成唯一 canonical 路径，再让 Events 投影；不能在 Events 里再加缓存或第二个
`Evidence` 对象掩盖这个问题。

### 3. 输入和成功输出都在原子提交边界之外

这是上面问题的具体时序证据：

- root 先提交 `StartGraphRun`，提交成功后才执行 `root.install_graph_input(input_frame)`（[`family_driver.py:1167-1185`](../src/mote_kernel/execution/family_driver.py#L1167-L1185)）；
- child graph 也先提交 start，之后才安装 child input（[`family_driver.py:846-863`](../src/mote_kernel/execution/family_driver.py#L846-L863)）；
- 节点 settlement 先提交 `_transition()`，成功后才把 `TaskSuccess.output` 加入 execution-local
  `ScopedFrameIndex`（[`family_driver.py:637-653`](../src/mote_kernel/execution/family_driver.py#L637-L653)）。

因此即便 persistence 现在愿意写 outbox，它拿到的 snapshot revision 也没有本次 invocation 的完整 input/output。
进程在 start commit 或 settlement commit 后崩溃时，无法从 authoritative state 重建同一调用事实。

已用一个只依赖 graph input 的最小探针复现：捕获 `StartGraphRun` 的 candidate 后只用 `state` 恢复，当前代码报：

```text
GraphValueUnavailableError:
resume actions () require unavailable historical values for pending nodes ...
```

这说明“重启后 dispatcher/Graph 只读 canonical snapshot”这个前提尚未成立。不能用 continuation、全局缓存或
Events 自己的副本补救，否则会违反唯一真相。

### 4. persistence 切口当前没有接上现有 persistence adapter

Events wrapper 传给内层的是：

```python
AtomicCommitRequest(transition, event_reference)
```

见 [`events/commit.py:16-27`](../src/mote_kernel/events/commit.py#L16-L27)。但现有 Cloudflare persistence adapter 的
`__call__` 仍接收并直接读取 `transition.previous_state`、`transition.candidate_state`（[`_commit.py:20-29`](../../mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/_commit.py#L20-L29)、
[`_commit.py:89-96`](../../mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/_commit.py#L89-L96)）。当前没有发现把
`AtomicCommitRequest` 适配成该 adapter 输入的 typed assembly 代码。

这不意味着要在本期把数据库实现搬进 Events；持久化实现仍由 `infra/persistence` 负责。问题是：Events 暴露的
SPI 目前还不能被现有 owner 直接消费，也没有明确的 reference adapter 和类型契约。至少要在 owner 之间把这一条
路径接通并通过类型检查，不能以“以后 adapter 自己猜 transition.result”作为接口。

## P1 处理结果与后续测试

### 5. 引用坐标防御（已采纳）

这个意见合理，已在 `NodeSettlementEventReference` 增加 `__post_init__` 校验（[`record.py:29-39`](../src/mote_kernel/events/record.py#L29-L39)）。
现在运行时会拒绝：

- 空的 `run_id` 或 `node_id`；
- 负数 `superstep`、`execution_generation`、`settlement_revision`；
- `scope` 为 `list` 而不是 tuple；
- 空字符串或非法 scope 部分。

特别是 `scope` 不再接受 list；不会自动转换错误输入，避免调用者拿到一个之后还能改变的幂等地址。这是边界保险丝，
不改变正常 `EventingGraphCommit` 路径。

### 6. schema 版本（不采纳“改成实例字段”）

`schema_version` 保持 `ClassVar[int]` 是刻意的：它是这个引用类型的固定协议身份，不是每条记录可以自行填写的业务值。
本次将版本常量与 event identity 前缀统一由 [`identity.py:7-31`](../src/mote_kernel/events/identity.py#L7-L31) 提供：
`schema_version == 1` 与 `mote.node-settlement-event.v1` 不会各自漂移。

将来需要演进时，新增 schema identity（例如 `v2`）并由 loader/dispatcher 按版本处理；不把版本变成可被调用者伪造的
实例字段，也不制造第二份版本真相。

### 7. 完整事件内容测试（跨 owner 后续）

当前 `tests/events/test_events.py` 的 19 个测试验证：引用坐标相等、调用一次、异常传播、并发身份和 wrapper
组合；`test_internal_records_are_exact_immutable_transaction_values` 甚至明确断言引用没有 `input/output/result`。

“引用不复制业务值”本身是对的，但还缺少另一半：

- 从 canonical snapshot 恢复后能得到实际 input；
- success 能得到对应 output；
- failure/interrupt 能得到各自 typed result；
- root/child/resume/循环 activation 的引用不会指向别的版本；
- persistence adapter 能消费完整 request，而不是只接受 fake。

这些内容测试仍然需要 canonical snapshot、persistence loader 和 dispatcher；应放在对应 owner 的 conformance/集成测试，
不能在 Events 包里用 fake payload 伪造。

## 工作树中的另一个 owner 回归（不计入 Events 包验收）

当前工作树同时删除了 failure resume/skip 的生产 API 和状态类型，但有意义的现有测试仍在使用它们。例如：

- `Graph.resume_failed()`、`Graph.resume_failed_with()`、`Graph.skip_failed()` 被从 [`facade.py`](../src/mote_kernel/execution/facade.py#L430-L460) 删除；
- 对应 `ResumeFailedNode`、`SkipFailedNode`、`SkippedGraphNode` 也被删除；
- 最近一次全量运行（2026-09-02）为 **3 failed, 1123 passed**；失败是既有的复杂度/泛型架构门禁（集中在
  `failover`），不是 Events 测试或 Events 生产代码失败。此前的 **25 failed, 594 passed** 是历史快照，不再代表当前
  工作树；
- 这些删除曾在早期工作树快照中触发相关 architecture/typing 失败；当前快照的可复现全量结果已收敛为上面列出的
  3 个 `failover`/复杂度门禁失败，不能把历史失败数字当作 Events 当前结果。

这不是“为了 Events 必须删掉的旧测试”。如果这些删除属于本次 Events 变更，就是无关行为回归和机械删测试，
应恢复/真实迁移并保留有价值覆盖；如果属于另一项工作，应拆成独立提交后再验收 Events。不能拿删测试或放宽门禁
把失败变成绿灯。

## 门禁记录

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -B -m pytest tests/events -q --tb=short -p no:cacheprovider` | **通过** | 19 passed |
| `python -B -m pytest tests/events tests/architecture/test_dependency_direction.py tests/architecture/test_package_structure.py -q` | **通过** | 26 passed |
| `pyright src/mote_kernel/events tests/events` | **通过** | 0 errors |
| `ruff check src/mote_kernel/events tests/events` | **通过** | 局部代码格式/规则正常 |
| `ruff format --check src/mote_kernel/events tests/events` | **通过** | 7 files formatted |
| `make package-check` | **通过** | wheel/sdist 构建及 twine 元数据检查通过 |
| `make check` | **未通过（非 Events）** | 现有 execution/test 文件格式检查先失败，未因 Events 代码失败；其后的全局 complexity 也有既有超限 |
| `python -B -m pytest -q --tb=short -p no:cacheprovider` | **未通过（非 Events）** | 3 个既有架构门禁失败：复杂度健康/ratchet 与 `failover` 泛型边界；Events 测试均通过 |
| `make complexity` | **未通过（非 Events）** | zero-debt health 有 5 个既有 `execution/recovery` 未使用私有定义；Events 未进入风险候选 |
| `make complexity-ratchet` | **未通过（非 Events）** | 工作树累计指标超过既有 ratchet；不通过调高阈值掩盖 |
| `pre-commit run --all-files` | **未通过（非 Events）** | 其他 hook 通过，仅全工作树 complexity ratchet 失败 |
| `git diff --check` | **通过** | 无空白错误 |

复杂度指标不是 Events 包的阻断理由；完整 vertical slice 和工作树中其他 owner 的问题仍按上文单独跟踪。

## Events 包已完成；vertical slice 后续事项

1. **（跨 owner）由 execution/state owner 闭合 canonical evidence。** 在不新建平行 State 的前提下，使同一版本能够定位本次
   invocation 的实际输入和 success/failure/interrupt 结果；root、child、resume、claim、settlement 都要覆盖。
2. **（跨 owner）收敛 `GraphTransition` 契约。** 删除或吸收 `GraphTransition.result` 这条等价 sidecar，不能让 persistence
   通过未声明字段自行猜事实；最终只保留一条 typed、不可变、可恢复的真相路径。
3. **（跨 owner）把准入事实放进正确的 commit 边界。** Graph input/child input 和 settlement publication 不能在对应 commit
   成功后才只写 execution-local memory；提交前失败必须不留下半套事实，提交后恢复必须能重建。
4. **（跨 owner）闭合唯一 persistence SPI。** `EventingGraphCommit` 继续只做 wrapper，不实现数据库；但 `AtomicCommitRequest`
   的字段、返回/取消/exact 语义必须能被 `infra/persistence` 的 typed adapter 直接消费，并有至少一个跨 owner
   reference test。
5. **（Events 已完成）修正引用不变量和版本表达。** 已增加深度不可变边界和严格坐标校验；schema version 固定为
   类型级常量，并与 event identity 共用同一个版本常量，不引入可伪造的实例字段。
6. **（跨 owner）补真正的事件内容测试。** 用新 Graph 实例仅从 authoritative snapshot 重建 success、failure、interrupt 的
   “节点名 + 实际参数 + 结果”，并验证没有 continuation/cache/第二份副本依赖。
7. **（另一 owner）处理无关 execution 回归。** 恢复或真实迁移有意义的 resume/skip 行为和测试；若不属于 Events，先拆分工作树，
   再在干净基线重新跑全套门禁。

## 最终判定

| 范围 | 判定 |
| --- | --- |
| Events wrapper 的局部代码形状 | **通过** |
| Events 包的唯一公共 API、引用不变量和原子 persistence SPI | **通过** |
| 可靠投递 vertical slice | **待跨 owner 完成** |
| 当前工作树整体交付 | **不通过（与 Events 无关的问题仍在）** |

Events 包可以按本次范围验收；完成可靠投递前，仍需由对应 owner 关闭上述跨 owner 后续事项。
