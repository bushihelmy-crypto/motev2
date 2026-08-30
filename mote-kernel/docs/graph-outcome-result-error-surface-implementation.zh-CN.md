# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案（三审整改版）

> 状态：**R10/R12–R15 WRITEBACK COMPLETE / DOCS-ONLY / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION / INDEPENDENT ACCEPTANCE PENDING**
>
> 日期：2026-08-26
>
> 独立评审：[graph-outcome-result-error-surface-implementation-review.zh-CN.md](graph-outcome-result-error-surface-implementation-review.zh-CN.md)
>
> 评审回复：[graph-outcome-result-error-surface-implementation-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-review-response.zh-CN.md)
>
> 第二次独立评审：[graph-outcome-result-error-surface-implementation-second-review.zh-CN.md](graph-outcome-result-error-surface-implementation-second-review.zh-CN.md)
>
> 第二次评审回复：[graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md)
>
> 第三次独立评审：[graph-outcome-result-error-surface-implementation-third-review.zh-CN.md](graph-outcome-result-error-surface-implementation-third-review.zh-CN.md)
>
> 第三次评审回复：[graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md)

本文是对“Outcome、Node Result、Run Result 与 Error 应按生命周期分层”的**设计澄清和 docs-only 实施方案**。它不授权修改
`src/**`、`tests/**`、State、Store、README、CHANGELOG、`pyproject.toml` 或执行路径。

当前规范事实源仍是
[`graph-node-input-output-contract-implementation.zh-CN.md`](graph-node-input-output-contract-implementation.zh-CN.md)；该文档已经冻结
当前 public names。本方案不另造一套 public API 事实，也不把概念分组名称升级为新的 alias。

## 1. 最终决策

执行链路保留三个不同生命周期的返回值层，以及一个独立的异常层。callable 的合法返回不是一个额外的宽
`NodeOutcome` union，而是以下已经存在的精确二选一：

```text
callable node return
    -> Graph.Values（plain success） | Graph.Outcome（explicit success/failure/interrupt）
    -> TaskResult
    -> GraphCommitResult
    -> Graph.Transition.result
Graph.run() 返回
    -> RunResult（概念分组）

graph-owned fault 可能抛出
    -> Graph.Error 及其既有精确子类
external callable/callback/capability exception
    -> 保留原始 exception identity
```

### 1.1 当前版本不重命名 public aliases

当前版本继续使用已经存在并被规范、README、tests 和 examples 使用的名称。历史讨论中的
`NodeOutcome` 只可作为“callable return”的概念标签，并且必须展开为
`Graph.Values | Graph.Outcome`；它不是新的 alias：

```text
callable return（概念标签）
    = Graph.Values（plain success） | Graph.Outcome（explicit outcome union）

Graph.Outcome 的 concrete variants
    -> Graph.SuccessOutcome / FailureOutcome / InterruptOutcome

admitted node settlement result（概念）
    -> Graph.Transition.result
    -> Graph.SuccessResult / FailureResult / InterruptResult

RunResult（概念）
    -> Graph.Result
    -> Graph.CompletedResult / AbortedResult / AwaitingResumeResult

Error
    -> Graph.Error 及现有精确 exception aliases
```

`NodeOutcome`、`RunResult` 和 `SettledNodeResult`（或 `AdmittedNodeResult`）在本文中是帮助读者理解生命周期的**概念标签**，不是本次
新增的 `Graph.NodeOutcome`、`Graph.RunResult` 或 `Graph.SettledNodeResult` 属性。尤其不能用一个未定义的宽
`NodeOutcome` 概念遮蔽 plain `Graph.Values` success 路径。

`SettledNodeResult` 不作为 public 名称：`Graph.Transition.result` 在可选 commit callback 确认前已经构造，它是 admitted candidate
evidence，不是 durable receipt。若未来需要正式改名，必须另开有版本边界的 API migration change unit。

### 1.2 不合并为宽 DTO

以下模型继续保持 nominal separation：

- node outcome 不能与 admitted node result 合并；
- admitted node result 不能与 whole-run result 合并；
- `Graph.Error` 不能成为 `Graph.Result` 的 variant；
- 不新增 `kind` 字符串、optional payload、error DTO、wrapper、第二 factory 或第二 execution path。

这些层的区别来自事实来源和 construction capability，而不是名称数量：

| 层 | 当前表面 | 产生时机 | 是否表示 external commit 已确认 |
| --- | --- | --- | --- |
| callable plain success | `Graph.Values` | callable 返回后、output admission 前 | 否 |
| callable explicit outcome | `Graph.Outcome` 及三个 concrete outcome aliases | callable 返回后、admission 前 | 否 |
| task completion | internal `TaskResult` | scheduler 投影 callable return 后 | 否 |
| 节点结算 evidence | `Graph.Transition.result` 及三个 concrete result aliases | output/routing admission 后、callback 调用前 | 否 |
| 图级 disposition | `Graph.Result` 及三个 run-result aliases | family driver 到达 run boundary | 仅按现有 callback 配置的确认语义 |
| 异常 | `Graph.Error` 及现有精确 aliases | 参数、快照、规划或运行契约失败 | 不适用 |

## 2. 当前 canonical owner 与转换链

当前代码已经提供所需的 callable、settlement 和 run lifecycle projections；本 change unit 不新增 class、factory、seal、wrapper、store
或 index。

```text
callable node return
  -> Graph.Values | Graph.Outcome
  -> execution.result.TaskResult
  -> execution.result.GraphCommitResult
  -> execution.family_driver.GraphTransition.result
     （public projection: Graph.Transition.result）

family driver boundary
  -> execution.result.GraphResult
  -> Graph.run() return

graph-owned faults
  -> execution.errors.ExecutionError
  -> Graph.Error
```

上面的 callable 链只适用于带 node settlement 的 transition；`Graph.Transition.result` 在 fence、run-start 等没有 node result 的既有
transition 上仍可为 `None`。`TaskResult` 与 `GraphCommitResult` 是 owner-internal construction stages，不是新增的 public facade
aliases。

| 事实 | 唯一 owner | 当前证据 |
| --- | --- | --- |
| callable plain-success values 与 values factory seal | `execution/graph/values.py` | `_GraphValues`、`_ValuesSeal`、`_make_graph_values` |
| callable outcome union 与 factory seal | `execution/graph/outcome.py` | `GraphOutcome`、`_OutcomeSeal`、`_success/_failure/_interrupt` |
| task completion | `execution/result.py` | `TaskResult`、`TaskSuccess`、`TaskFailure`、`TaskInterrupt` |
| admitted commit evidence | `execution/result.py` | `GraphCommitResult`、`_CommitResultSeal`、`_commit_result()` |
| transition 与 exact successor contract | `execution/family_driver.py` | `GraphTransition`、`GraphCommit`、`commit_transition()` |
| root run disposition | `execution/result.py` + `execution/family_driver.py` | `GraphResult`、`project_graph_result()` |
| shared graph execution error taxonomy | `execution/errors.py` | `ExecutionError` 及其既有子类（不含 `result.py` 的 partial-prefix class） |
| sealed partial-prefix exception 与 state/continuation/cause 字段 | `execution/result.py` | `_PartialCommitError`、`_PartialCommitSeal`、`_partial_commit_error()` |
| 唯一 public namespace | `execution/facade.py` | `Graph`；`execution.__all__ == ["Graph"]` |

### 2.1 Outcome：节点 callable 的临时意图

唯一 `NodeCallable` 契约是：

```python
Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]
```

其中 plain `Graph.Values` 是最普通的 success 路径；scheduler 将它直接投影为 `TaskSuccess`，不会先包装成
`Graph.Outcome`。只有 callable 显式返回 `Graph.success()`、`Graph.failure()` 或 `Graph.interrupt()` 的结果时，才进入下面的
`Graph.Outcome` closed union：

```text
Graph.SuccessOutcome[GraphValueT]
Graph.FailureOutcome
Graph.InterruptOutcome
```

字段和 factory 保持不变：

```text
SuccessOutcome[T]
    output: Graph.Values[T]
    route: str | None

FailureOutcome
    failure: str

InterruptOutcome
    request_payload: bytes
```

plain `Graph.Values` 仍表示 plain success。保留现有 `scheduler.NodeReturn` internal `TypeAlias`；本 change unit 不新增
`Graph.NodeReturn`、`Graph.NodeOutcome`、`PlainSuccessOutcome`、wrapper 或 factory。三个 concrete aliases 仍指向
`execution/graph/outcome.py` 的 canonical classes；`_OutcomeSeal` 继续确保 public alias 只可用于 annotation/`isinstance` narrowing，
不能绕过 `Graph.success()`、`Graph.failure()` 或 `Graph.interrupt()` factory 构造实例。

### 2.2 Node result：admitted settlement evidence

`Graph.Transition.result` 的现有 union 是单个 node settlement 的 public projection：

```text
Graph.SuccessResult[GraphValueT]
Graph.FailureResult
Graph.InterruptResult
```

它与 outcome 不合并，原因是：

- outcome 尚未通过 compiled output descriptor 和 routing admission；
- result 带有 public `node_id`，并且只在 settlement transition 中出现；
- result 是 callback invocation 的只读 candidate evidence，不是 callback receipt；
- outcome 与 result 使用不同 private seal，防止未 admission 对象伪装成 commit evidence。

`commit_transition()` 的顺序继续是：

```text
reduce_graph_run(previous_state, command)
    -> _commit_result(TaskResult)
    -> 构造 Graph.Transition
    -> 调用可选 Graph.Commit
    -> 仅接受 exact candidate successor
```

`commit=None` 时没有外部确认步骤；本方案不新增 Store、journal、receipt、publication record 或 confirmation DTO。外部持久化是否存在、
以及 candidate 如何被确认，仍完全由现有 `Graph.Commit` 调用方负责。

### 2.3 Run result：整个 graph 的 disposition

`Graph.Result[GraphValueT]` 仍是：

```text
Graph.CompletedResult[GraphValueT]
Graph.AbortedResult[GraphValueT]
Graph.AwaitingResumeResult[GraphValueT]
```

这些 variant 继续携带现有 `state`、`continuation`、`outputs`/`abort`/`failures`/`interrupts` 字段和 invariant graph universe。
本文不把内部 `GraphAbortView`、`GraphFailureView` 或 `GraphInterruptView` 提升为新的 `Graph` alias；它们仍由现有 owner 负责字段实现。

`Graph.Result` 表示 family driver 返回的当前 invocation disposition。若调用方提供 commit callback，state 按现有 exact successor contract
确认；若未提供 callback，执行仍遵循当前 local candidate 语义。本文不把该结果承诺为跨进程 durable snapshot。

## 3. Error 公共面

异常不是返回 union，采用现有继承树和 `Graph.Error` 根类。本文只整理当前 facade 已经暴露的 aliases，不新增 exception class。

### 3.1 当前 public error aliases

以下名称已经存在并继续保留：

```text
Graph.Error = ExecutionError
Graph.ValidationError = GraphValidationError
Graph.SnapshotMismatchError = SnapshotMismatchError
Graph.ExecutionLimitError = ExecutionLimitError
Graph.RoutingError = RoutingError
Graph.ValueAdmissionError = GraphValueAdmissionError
Graph.ValueUnavailableError = GraphValueUnavailableError
Graph.ValuePublicationError = GraphValuePublicationError
Graph.PartialCommitError = _PartialCommitError
```

它们的典型处置不同，但都属于 graph-owned exception control flow：

| alias | 语义边界 | 是否新增 |
| --- | --- | --- |
| `Error` | 所有 graph-owned execution fault 的稳定根类 | 否，保持 |
| `ValidationError` | graph definition/public builder 参数不合法 | 否，保持 |
| `SnapshotMismatchError` | state、continuation、scope 或 transition evidence 不匹配 | 否，保持 |
| `ExecutionLimitError` | 拒绝非法 public limit 参数，或表示执行/恢复超过 explicit bound | 否，保持 |
| `RoutingError` | route/conditional/join 无法形成合法 deterministic frontier | 否，保持 |
| `ValueAdmissionError` | public values、frame 或 resume codec 不符合 compiled descriptor | 否，保持 |
| `ValueUnavailableError` | 当前 recovery lineage 缺少下一边界所需的已确认 value | 否，保持 |
| `ValuePublicationError` | `ScopedFrameIndex.add_*()` 的四类 frame coordinate duplicate；resume substitution coordinate 的重复 publication，或与已确认 publication coordinate 冲突 | 否，保持 |
| `PartialCommitError` | 现有 invocation-local confirmed-prefix handoff | 否，保持既有行为 |

`Graph.failure(reason)` 是 node/business control outcome；`Graph.Error` 是 raised exception。前者可以进入现有 settlement/recovery 语义，
后者不能塞进 `Graph.Result`。

`ValuePublicationError` 的 post-commit 安装边界保持独立：若 `invocation.py` 在 confirmed successor 的 frame installation 阶段捕获同类
冲突，会转换为 `FrameInstallationInvariantError`；本文不把所有 candidate/confirmed source conflict 统称为
`ValuePublicationError`。

### 3.2 不从 facade 新增 aliases 的 internal exception classes

以下 owner-internal exception classes 继续只能通过 `Graph.Error` 捕获，不在本文扩张 public namespace。`PlanningError` 是
`SnapshotMismatchError`、`InvalidExecutionSnapshotError` 和 `ExecutionLimitError` 的 internal intermediate base，不是 leaf：

```text
PlanningError
InvalidExecutionSnapshotError
ResultCollectionError
NodeExecutionContractError
UnknownRouteError
InvalidRoutingCommandError
JoinProgressError
RoutingDeadlockError
FrameInstallationInvariantError
DuplicateNodeError
DuplicateBoundaryError
DuplicateEdgeError
DuplicateGraphDefinitionError
RecursiveGraphDefinitionError
UnknownNodeError
MissingEntryError
UnreachableNodeError
InvalidJoinError
InvalidGraphIdentityError
InvalidResourceDefinitionError
```

`FrameInstallationInvariantError` 是独立的 internal `Graph.Error` subclass，负责 confirmed successor 与 pre-admitted frame 安装不一致；
它不属于 `ValuePublicationError` 的语义，也不提升为 facade alias。

该清单不是新的 exception hierarchy：shared taxonomy 由 `execution/errors.py` 持有，sealed `PartialCommitError` 由
`execution/result.py` 持有，public Graph-namespaced aliases 由 `execution/facade.py::Graph` 持有。`PartialCommitError` 仍只保留当前
`_PartialCommitError` direct alias、seal 和字段，不扩写成 failover、checkpoint 或跨进程 handoff protocol。

`Graph.Error` 只覆盖 graph-owned faults。ordinary node callable、commit callback 或其他外部 capability 抛出的任意 exception 不会
自动转换为 `Graph.Error`，执行链保留其 identity；只有已有 confirmed-prefix 场景按当前契约形成 `Graph.PartialCommitError`，原始
exception 位于其 `cause`。因此 `except Graph.Error` 不是“捕获所有外部异常”的总入口。

## 4. Runtime narrowing 与 typing 边界

union alias 与 concrete variant 的用途必须分开：

| 用途 | 正确表面 | 禁止做法 |
| --- | --- | --- |
| callable annotation | `Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]` | 在 facade 重新拼 union、使用 `Any`/`object` |
| `run()`/transition annotation | `Graph.Result`、`Graph.Transition.result` 的既有 union | 在 facade 重新拼 union、使用 `Any`/`object` |
| runtime `isinstance` narrowing | `Graph.SuccessOutcome`、`Graph.FailureOutcome`、`Graph.InterruptOutcome`；`Graph.SuccessResult`、`Graph.FailureResult`、`Graph.InterruptResult`；run concrete aliases | `isinstance(value, Graph.Outcome[T])` 或把 parameterized union 当 nominal class |
| construction boundary | `Graph.*` factories、family driver、settlement projection、run projection | public direct constructor、伪造 seal、wrapper/registry |
| generic universe | 现有 invariant `GraphValueT` 与 covariant `Graph.Values` 关系 | widening、bare generic、generic-erasing cast |

本 docs-only change 不新增 Pyright fixture、不改 `NodeCallable`、`Graph.Commit` 或 `Graph.run()` overload。未来若另行批准 API rename，typing
和 runtime identity 必须拆成独立 evidence；不能用一个“任意 Pyright error”或 union runtime error 代替两类证明。

## 5. 所有权、范围与硬保持项

### 5.1 按轮次与职责分开的文件账本

以下 ledger 是本主题的唯一可复算职责记录。历史 review/response 文件保持原样；只有 `T0` implementation target 接受当前
docs-only writeback，`A3` 记录本轮 disposition，不把审计记录伪装成规范实施目标。

| unit | exact path | owner / 职责 | 输入或状态 | 是否属于 current normative target manifest |
| --- | --- | --- | --- | --- |
| `T0` | `docs/graph-outcome-result-error-surface-implementation.zh-CN.md` | implementation target；唯一写入当前决策的文档 | 吸收已接受的 R1–R10；当前回写 R12–R15 | **是（唯一 target）** |
| `R1` | `docs/graph-outcome-result-error-surface-implementation-review.zh-CN.md` | 首次独立 review record | 冻结历史输入，不回写 | 否，review-only |
| `A1` | `docs/graph-outcome-result-error-surface-implementation-review-response.zh-CN.md` | 首次 disposition response | 冻结历史裁决，不回写 | 否，response-only |
| `R2` | `docs/graph-outcome-result-error-surface-implementation-second-review.zh-CN.md` | 二次独立 review record | 冻结历史输入，不回写 | 否，review-only |
| `A2` | `docs/graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md` | 二次 disposition response | 记录 R8–R11；当时的 target writeback 归属 `T0` | 否，response-only |
| `R3` | `docs/graph-outcome-result-error-surface-implementation-third-review.zh-CN.md` | 三次独立 review record | 本轮冻结输入；只提出 R12–R15 | 否，review-only |
| `A3` | `docs/graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md` | 三次 disposition response | 接受/不采纳裁决；本轮 target writeback 仍归属 `T0` | 否，response-only |

因此，历史上曾将首轮三文件称为 manifest 的表述，仅保留为 `A1/A2` 的历史记录，不继续承担当前规范范围的含义。当前
`normative target manifest` 只指 `T0`；review/response 通过有向链接和 hash binding 提供审计证据。规范 API 事实源仍是：
[`graph-node-input-output-contract-implementation.zh-CN.md`](graph-node-input-output-contract-implementation.zh-CN.md) 及其 owner 代码，
本账本不复制它们的定义。

不修改：

```text
src/**
tests/**
README.md
README.zh-CN.md
CHANGELOG.md
pyproject.toml
State / Store / protocol / persistence files
```

本轮实际 writeback 仍只触及 `T0`，并新增 `A3`；不改变 production/test/State/Store 范围，也不清理或覆盖用户已有的 unrelated
dirty files。

### 5.2 硬保持项

下列是已有架构和运行时 invariant，只在本文中作为 HARD KEEP 复核，不构成本期新增实现目标：

- `Graph` 仍是唯一 public graph composition/execution facade；顶层 `execution.__all__` 仍只有 `Graph`；
- outcome、task result、commit evidence、transition 和 run result 各由现有 owner 产生；
- persistent state、publication installation 和 Python snapshot 的现有顺序不改变；
- `Graph.Commit` 的 exact candidate successor contract 不改变；
- `Graph.failure()`、interrupt、skip、resume 和 nested graph 语义不改变；
- 不增加 Store、checkpoint、journal、retry、worker arbitration、failover、跨进程 value recovery 或第二 runner；
- 不新增 compatibility alias、第二解释路径、宽 DTO、hidden mutable state 或 public state command。

### 5.3 未来 rename 的独立准入条件

如果产品确实需要 `Graph.NodeOutcome`、`Graph.RunResult` 或其他显式新名称，必须另开 versioned API migration change unit，至少先冻结：

1. 需求 owner 与 semver/version boundary；
2. 每个 owner 的唯一 canonical symbol 和一次性删除清单；
3. README、active docs、tests、examples 和 typing fixtures 的完整 manifest；
4. candidate evidence 与 external confirmation 的精确时间边界；
5. no-persistence/no-failover 停止条件；
6. strict Pyright、runtime identity、architecture ownership、complexity 与行为回归证据。

该未来 change unit 不能在本文中通过新增 alias、兼容 wrapper 或临时双轨提前实现。

## 6. Docs-only 实施与复核记录

### D1：核对 canonical owner（COMPLETE）

逐项核对 `outcome.py`、`result.py`、`family_driver.py`、`errors.py`、`graph/node.py` 和 `facade.py`，只记录已有 symbol、owner、seal、
union 和 public alias；不在 facade 中重新定义 union。

### D2：写入概念映射（COMPLETE）

在文档和代码评审中使用以下固定映射：

```text
NodeOutcome（仅历史概念标签）       -> Graph.Values | Graph.Outcome
admitted node settlement result  -> Graph.Transition.result + result concrete aliases
RunResult                        -> Graph.Result + run concrete aliases
Error                            -> Graph.Error + current public error aliases
```

概念名称不转化为新 class attribute，不改变任何调用方 import 或 annotation。

### D3：固定 error matrix（COMPLETE）

按第 2 节的三方 ownership 记录维护第 3 节的 public/internal 清单：shared taxonomy 在 `execution/errors.py`，partial-prefix
exception 在 `execution/result.py`，facade aliases 在 `execution/facade.py::Graph`。不得因为“精确捕获更方便”而逐个把 internal
class 搬到 `Graph` facade；只有已有 alias 或独立需求获批后才可扩大 public surface。

### D4：固定 candidate/evidence 语义（COMPLETE）

文档必须明确 `_commit_result()` 发生在可选 callback 之前；`Graph.Transition.result` 不是 receipt，`Graph.PartialCommitError` 不是新的
failover protocol。所有 durable/store 行为继续归属于现有 callback/外部 owner。

### D5：完成 docs-only 复核（COMPLETE）

检查 exact directed link graph、旧 public names 没有被误写成待删除项，并按第 7.2 节的 tracked/index/untracked 分层命令核对
change-unit scope；再运行适用的文档门禁。不得以已有全量测试通过数冒充本 docs-only change 的 production authorization。

### D6：回写 R12–R15 并记录 disposition（COMPLETE）

接受 manifest ledger、negative-scope、可复现 evidence 和 `NodeReturn`/`ValuePublicationError` wording 的事实性修正；不采纳的
过宽建议集中写入 `A3`，不改写冻结的 `R1`–`R3` 文件。独立验收仍是后续 review unit 的职责。

## 7. 证据与验收

### 7.1 已有基线证据

以下是 2026-08-26、production baseline Git `563a45124311f11e870d0627461102baeffdf7ad` 截止的历史 snapshot；它发生在本次
docs-only writeback 之前：

```text
make check: 850 passed, 100% coverage, build/twine passed
monorepo pre-commit run --all-files: all applicable hooks passed
```

这些结果只证明未修改的源码基线健康，不证明任何 rename、new alias 或 production implementation 已授权，也不替代当前 docs
unit 的 scoped evidence。

本次回写后的源码门禁复跑记录（`2026-08-26T20:17:08+08:00`，cwd=`/home/longert/motev2/mote-kernel`，
`HEAD=d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`）：`make check` **PASS**（Ruff/format、Pyright `0 errors`、complexity `9 passed`、
health `51 reviewed / 0 unreviewed / 0 stale`、`850 passed`、coverage `100%`、build/twine passed）。这只是当前源码 baseline 的健康
证据，不是本 docs-only change 的 production authorization。

### 7.2 当前 docs-only evidence（VERIFIED FOR THIS SNAPSHOT）

本次验证记录采用以下精确有向链接图；“互链”不表示每一对文档都必须有反向 backlink：

```text
implementation
  -> first review, first response, second review, second response, third review, third response, normative source
first review
  -> implementation
first response
  -> implementation, first review
second review (frozen input)
  -> implementation, first review, first response
second response
  -> implementation, second review, first review, first response
third review (frozen input)
  -> implementation, second review, second response, first review, first response, normative source
third response
  -> implementation, third review, second review, second response, first review, first response, normative source
```

当前 snapshot bindings：

```text
first review SHA256       = a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668
first response SHA256     = efc3a8d5cdae46016626f9f4796da1a637f6396d302288e0fab34f0c6056b18b
second review SHA256      = 4f30fcf23cb5009ee0ba5fcd46cb27891f0158395cdc66eaa411744414ae0c3f
second-review bound old target SHA256 = be747243b7419604dc8d9cdffa268efbeb368a7aeaeaa043c6bcc3ac3866a5d6
third review SHA256       = 292522ee8a6a5aabb496e1f706af040dcbd2f8f6483d1b94f5395d7515d5c66a
normative source SHA256    = 233ba6be90d9ae3d7d7c3817c584ca44dfd2d9a76dff3a36968cbda136043f09
```

`T0` 当前 SHA256 与 `A3` 的 writeback binding 记录在第三次评审回复的 §1；`T0` 不嵌入 `A3` 自身 hash，避免 target/response
互相引用造成自引用 digest。`A3` 自身 SHA 由交付时的独立 `sha256sum` 计算。历史 `R1`/`A1`/`R2`/`A2` hashes 保持不变，分别
表示冻结输入，不冒充当前 snapshot。

当前 docs-unit verification 记录（验证批次起始时间：`2026-08-26T20:21:36+08:00`；以下命令均为只读；`cwd` 和 scope 明确写出）：

- Markdown links、EOF、CRLF 与 trailing whitespace：cwd=`/home/longert/motev2`，运行以下只读命令（路径列表是本 change unit 的七个
  outcome/result/error docs 加一个 normative source）：

  ```bash
  python -B - <<'PY'
  from pathlib import Path
  import re

  paths = [
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-review.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-review-response.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-second-review.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-third-review.zh-CN.md"),
      Path("mote-kernel/docs/graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md"),
      Path("mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md"),
  ]
  errors = []
  link_count = 0
  for path in paths:
      if not path.is_file():
          errors.append(f"missing: {path}")
          continue
      raw = path.read_bytes()
      if not raw.endswith(b"\n"):
          errors.append(f"missing-final-newline: {path}")
      if b"\r" in raw:
          errors.append(f"CRLF-or-CR: {path}")
      content = raw.decode("utf-8")
      for line_no, line in enumerate(content.splitlines(), 1):
          if line.endswith((" ", "\t")):
              errors.append(f"trailing-whitespace: {path}:{line_no}")
          for match in re.finditer(r"\[[^]]+\]\(([^)]+)\)", line):
              target = match.group(1).strip().split(" ", 1)[0]
              if target.startswith(("http://", "https://", "mailto:", "#")):
                  continue
              target = target.split("#", 1)[0].strip("<>")
              if target:
                  link_count += 1
                  if not (path.parent / target).resolve().is_file():
                      errors.append(f"broken-link: {path}:{line_no}: {target}")
  if errors:
      print("\n".join(errors))
      raise SystemExit(1)
  print(f"files={len(paths)} links={link_count} errors=0")
  PY
  ```

  实际输出：`files=8 links=31 errors=0`（exit `0`）。
- docs hooks（cwd=`/home/longert/motev2`）：

  ```bash
  pre-commit run --files \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-review.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-review-response.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-second-review.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-third-review.zh-CN.md \
    mote-kernel/docs/graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md \
    mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
  ```

  实际结果：适用 docs hooks 通过，源码 hooks 因路径过滤 skipped（exit `0`）。
- tracked/index/untracked scope（从 monorepo root）：

  `mote-kernel/src/**` 覆盖 kernel 的 State、execution 与 failover owner；`mote-infra/persistence/**` 覆盖 Store/persistence
  实现；`conformance/**` 覆盖 protocol/spec fixtures。以下四条命令对同一组 pathspec 分别检查 worktree、unstaged index、staged
  index 和 untracked 文件，避免把某一层的空输出误读成全局 clean。

  ```bash
  git status --short --untracked-files=all -- 'mote-kernel/src/**' 'mote-kernel/tests/**' 'mote-kernel/pyproject.toml' 'mote-kernel/CHANGELOG.md' 'mote-infra/persistence/**' 'conformance/**'
  git diff --name-only -- 'mote-kernel/src/**' 'mote-kernel/tests/**' 'mote-kernel/pyproject.toml' 'mote-kernel/CHANGELOG.md' 'mote-infra/persistence/**' 'conformance/**'
  git diff --cached --name-only -- 'mote-kernel/src/**' 'mote-kernel/tests/**' 'mote-kernel/pyproject.toml' 'mote-kernel/CHANGELOG.md' 'mote-infra/persistence/**' 'conformance/**'
  git ls-files --others --exclude-standard -- 'mote-kernel/src/**' 'mote-kernel/tests/**' 'mote-kernel/pyproject.toml' 'mote-kernel/CHANGELOG.md' 'mote-infra/persistence/**' 'conformance/**'
  ```

  实际输出（四条命令均 exit `0`）：

  ```text
  status / unstaged diff:
    M mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed
  staged diff: (empty)
  untracked scoped files: (empty)
  ```

  上述 sibling `py.typed` 是本任务开始前的用户已有 unrelated baseline；它不属于本 change unit，本轮不清理或覆盖。
  结论只写“本 change unit 无 source/test/State/Store/protocol/persistence/execution-path diff”。若输出包含用户在本轮之前已有的
  sibling persistence 或其他 dirty path，单独标为 unrelated baseline，不写成全局 clean，也不清理它们。
- tracked diff whitespace：`git diff --check`；cwd=`/home/longert/motev2`；实际 exit `0`；该命令不覆盖 untracked docs，因此必须
  与上述显式 scanner 同时保留。
- target/review/response hashes（cwd=`/home/longert/motev2/mote-kernel`）：

  ```bash
  git rev-parse HEAD
  sha256sum \
    docs/graph-outcome-result-error-surface-implementation.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-review.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-review-response.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-second-review.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-third-review.zh-CN.md \
    docs/graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md \
    docs/graph-node-input-output-contract-implementation.zh-CN.md
  ```

  `HEAD`、target、review、response 与 normative-source hashes 以本次交付输出绑定；target 当前 hash 和 `A3` binding 以第三次回复为准。

本节的 `COMPLETE` 仅表示当前 docs snapshot 的验证已记录，不表示 production authorization；历史 baseline 与 current docs-unit
verification 明确分开。

同一批次的静态断言（均成立）：

- 不新增 public alias、class、factory、seal、exception class 或 compatibility path；
- 文档不把 `SettledNodeResult` 描述为 durable confirmation，不把 `Graph.Error` 放入 `Graph.Result`，也不把 `Graph.failure()` 描述成
  必然持久化；
- 当前 public names 与 normative source 保持一致。

## 8. 最终状态与完成定义

```text
conceptual separation of outcome/result/run/error = PASS
current canonical owner and public aliases       = KEEP
public rename                                     = NOT IN THIS CHANGE UNIT
error surface expansion                           = NOT IN THIS CHANGE UNIT
persistence/failover/recovery expansion           = NOT IN THIS CHANGE UNIT
production/tests authorization                    = NO AUTHORIZATION
R10 / R12–R15 docs-only writeback                  = COMPLETE
independent no-finding acceptance                  = PENDING
```

R12–R15 的 target writeback 已完成；最终无 finding 的 `CLOSED/PASS` 只能由后续独立 docs acceptance/review record 持有，不能在本文
中提前宣称。若未来要改名或新增精确错误 aliases，必须按第 5.3 节另行立项和评审。
