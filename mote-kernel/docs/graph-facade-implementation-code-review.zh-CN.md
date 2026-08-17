# Graph 唯一公共门面当前实现代码验收报告

> 再次验收更新（2026-08-15）：本节是基于最新提交的当前结论，覆盖下文首次验收的“暂不通过”判定；首次记录保留为问题发现与修复依据。

## 0. 再次验收结论

**上轮两个阻断项均已关闭，新增的资源 API 收敛要求也已完整实现，当前代码、测试、文档与交付门禁通过验收。**

本轮验收基线为 `e2b4bc492ded2488793d3c0843f1e5fbf410730c`（`feat(kernel): add reviewed sole graph facade`），与
`origin/main` 一致。重新审核了 facade、limits owner、compiler/resource normalization、public typing consumer、state-driven recovery 和完整门禁；未发现仍阻止唯一 `Graph` 门面、resume-as-run-input 或 authoritative state 自动调度成立的代码缺陷。

### 0.1 上轮阻断项关闭情况

| 上轮阻断项 | 再次验收 | 修复与证据 |
| --- | --- | --- |
| 非法 limits 在 start/fence/resume 后才失败 | 已关闭 | `ExecutionLimits.__post_init__()` 成为唯一正数校验 owner；`Graph.run()` 在 compilation 和任何 transition 前构造 limits；新 run、active recovery、resume 的参数化测试均断言零 commit、零执行和输入 state 不变 |
| commit typed result 需要导入内部 variant | 已关闭 | `Graph.SuccessResult`、`Graph.FailureResult`、`Graph.InterruptResult` 以 namespaced alias 复用 canonical `TaskResult` variants；只导入 `Graph` 的 strict typing consumer 可完整判型，Pyright 通过 |

limits 最小反例的当前输出为：

```text
invalid-new      ExecutionLimitError [] node_calls=0
invalid-recovery ExecutionLimitError [] node_calls=0
invalid-resume   ExecutionLimitError [] node_calls=1
```

第三行的一次调用只用于预先构造 failed state；非法 resume invocation 自身保持零执行，commit log 为空，原 failure settlement 未被消费。

public typing test 只执行：

```python
from mote_kernel.execution import Graph
```

随后通过 `Graph.SuccessResult`、`Graph.FailureResult`、`Graph.InterruptResult` 严格 narrow，success output 保持精确 `OutputT`，没有使用内部 import、`Any`、reflection、字符串 discriminator 或复制 DTO。

### 0.2 新增资源 API 要求验收

用户新增要求为：独立 `add_resource()` 不作为对外函数，资源声明收敛到 `add_node()`。

**该要求通过。** 当前公共调用形态为：

```python
graph.add_node(
    "node",
    operation,
    resources=("database", "browser"),
)
```

具体证据：

1. `Graph` 不存在 `add_resource` 方法；execution 顶层仍只导出 `Graph`；
2. `add_node(..., resources=...)` 是唯一 facade 资源输入；
3. `_register_resource_requirements()` 只作为私有组装细节，按资源首次出现顺序生成连续的底层 `ResourceDefinition.order`；
4. 多个节点引用同一资源时只登记一次，不产生第二份资源定义；
5. 每个 node 的 requirement tuple 保留到 `NodeDefinition`，compiler 再按 graph resource order 规范化 acquisition 顺序；
6. 同一 node 重复资源不会被门面静默吞掉：底层 graph validation 仍以 `InvalidResourceDefinitionError` fail closed；
7. 空、未 trim 或其他非法 resource identity 仍在 compilation、任何 start commit 和 node execution 前拒绝；
8. graph compiled 后，`add_node()` 的既有 immutable guard 在资源登记前执行，不会产生 late hidden resource mutation。

仓库内确定性测试 `test_node_resources_register_once_in_deterministic_first_seen_order` 已证明：

```text
node a requires (beta, alpha)
node b requires (alpha, gamma)
graph resource order = (beta, alpha, gamma)
```

额外 facade 反例还验证了：

```text
node a requires (beta)
node b declares (alpha, beta)

graph resource order = (beta, alpha)
compiled node b requirement = (beta, alpha)
```

这证明资源顺序不依赖调用方重复维护另一份 declaration，也不会因后续节点以不同局部顺序引用共享资源而破坏 deterministic acquisition order。

同一 node 的 `("duplicate", "duplicate")` 反例在零 commit、零 node call 时拒绝，说明自动登记只去重 graph-level definition，没有错误地放宽 node-level duplicate requirement。

内部 `ResourceDefinition`、resource admission、snapshot 与 reducer 仍由原 owner 持有；本次只是消除公共 builder 上的重复事实输入，没有复制 resource engine 或新增第二资源模型。

### 0.3 核心目标再次复判

| 核心目标 | 当前判定 | 说明 |
| --- | --- | --- |
| 唯一公共门面 | 通过 | `mote_kernel.execution.__all__ == ["Graph"]`，实现位于私有 `_graph.py` |
| Resume 合入唯一 run | 通过 | failure、interrupt、skip action 只作为 `run(resume=...)` 输入，不存在 `Graph.resume()` |
| 公共资源声明收敛 | 通过 | 只有 `add_node(..., resources=...)`，没有 public `add_resource()` |
| authoritative state 自动推进 | 通过 | 自动覆盖 start、exact fence、resume、prepare、claim、session、逐节点 settle、resolve 和 terminal/boundary return |
| 唯一真相源 | 通过 | facade 不保存 run state、session、output、resume input 或 child state |
| 逐 transition commit | 通过 | candidate 先经 pure reducer，精确确认后才替换 local snapshot 并继续 |
| strict public typing | 通过 | outcome、run result、state、transition 与 node-result variants 均通过 `Graph` namespace 使用 |
| cancellation recovery | 通过 | worker 先 quiesce，active lease 保留；重新读取 state 后由下一次 `run()` exact fence/reclaim |
| active-state safety boundary | 通过 | `run()` docstring 已明确传入 active state 等价于调用方确认旧 attempt stopped/lost；不承诺 multi-worker arbitration 或 exactly-once |

### 0.4 再次验收测试与门禁

定向验证：

- facade、public typing 与两组 architecture tests：50 passed；
- resource facade 额外反例：通过；
- limits 三路径旧反例：全部变为零 commit；
- `pyright tests/execution/test_graph_public_typing.py`：0 errors、0 warnings、0 informations。

执行 `make check`：完整通过。

- Ruff lint：通过；
- Ruff format：112 files already formatted；
- Pyright strict：0 errors、0 warnings、0 informations；
- Pytest：553 passed；
- Coverage：2,347 statements、716 branches，statement/branch 100%；
- sdist/wheel build：成功；
- Twine package check：两个产物均通过。

执行标准 monorepo `pre-commit run --all-files --show-diff-on-failure`：全部通过，包括 large file、case conflict、merge conflict、TOML、YAML、EOF、line ending、trailing whitespace、Ruff、Ruff format、rustfmt 和 detect-secrets。

`git diff --check f4a2a98..e2b4bc4`：通过。

### 0.5 当前最终意见

上轮评审要求的 limits fail-closed、public typed result、active recovery/cancellation 文档与测试均已落实；本轮新增的“资源只从 `add_node()` 声明”也形成了完整的 public API、deterministic normalization、shared-resource dedup 和 compiler guard 闭包。

**当前结论更新为：代码、测试、文档与完整交付门禁通过，可以验收。**

## 1. 首次验收信息

- 验收对象：`docs/graph-facade-implementation.zh-CN.md` 对应的最新实现；
- 验收基线：`1ab40d06b5c48fd21d73d4c40b02b85a7cc60530`（`feat(kernel): add sole public graph facade`）；
- 验收日期：2026-08-15；
- 重点目标：`resume` 作为唯一 `run()` 的参数、公共能力收敛到 `Graph`、按 authoritative `GraphRunState` 自动推进调度；
- 验收方式：代码与提交 diff 审核、公开类型面检查、确定性最小反例、定向测试、完整项目与 monorepo 门禁；
- 变更边界：未修改被验收的 production code 或 tests，本文件只记录验收结果。

## 2. 首次验收总体结论

**三个核心方向已经落地，但当前代码验收不通过，不能认定实施方案已无条件完成。**

已经确认：

1. `resume` 确实只作为 `Graph.run(..., resume=...)` 的输入，不存在公开 `Graph.resume()` 或第二 runner；
2. `mote_kernel.execution.__all__ == ["Graph"]`，builder、outcome factory、resume action factory 与运行入口均收敛在 `Graph`；
3. `Graph.run(state=...)` 会根据 authoritative state 自动完成 fence、resume、prepare、claim、session、逐节点 settle 和 frontier resolve；
4. 调度仍复用唯一 `GraphExecutor`、`GraphExecutionSession`、scheduler、settlement、routing 与 pure reducer，没有复制第二套执行语义；
5. `Graph` 不保存 run state、session 或 output，同一 compiled facade 可以并发驱动独立 run；
6. 每条 transition 都先经 pure reducer 形成 candidate，并且只有 commit 回调精确确认后才继续。

但是存在两个验收阻断项：

1. 非法 execution limits 会在 `StartGraphRun`、`FenceGraphExecution` 或 `ResumeGraphNodes` 已经提交后才失败，违反无效公共输入应在任何 authoritative transition 前 fail closed 的要求；
2. commit 回调中的 typed node result 无法只通过 `Graph` 公共命名空间安全判型，严格类型使用方必须导入 owner-internal `TaskSuccess` / `TaskFailure` / `TaskInterrupt`，与“只暴露一个 `Graph`、无需额外 import”及 DomainState 原子提交目标冲突。

完整测试和质量门禁虽然全部通过，但没有覆盖这两个反例，因此不能用 100% statement/branch coverage 替代行为闭口。

## 3. 核心目标逐项复判

| 目标 | 判定 | 代码证据与说明 |
| --- | --- | --- |
| execution 顶层只公开 `Graph` | 通过 | `execution/__init__.py` 只导入并导出 `Graph`；实现位于私有 `_graph.py` |
| builder 与运行能力收敛到 `Graph` | 通过 | node、edge、join、resource、entry、codec、outcome、resume action 与 `run()` 均为 `Graph` 方法 |
| 不提供公开 `Graph.resume()` | 通过 | 恢复动作只能由 factory 创建后传给 `run(resume=(...))` |
| failure / interrupt / skip 复用同一 run path | 通过 | action canonicalize 后由 `GraphExecutor.resume()` 投影唯一 `ResumeGraphNodes`，随后回到 `_drive()` |
| 按 authoritative state 自动推进 | 通过 | active state 先 fence；settled state 自动 resolve；executable state claim/session；awaiting-resume、completed、aborted 返回边界结果 |
| 每个节点独立 settle 并确认 commit | 通过 | session 每次只交付一个 completion；每个 `SettleGraphNode` 独立调用 `_commit_transition()` |
| resource waiter 使用最新 successor 立即调度 | 通过 | facade 持续把已确认 state 传回同一 session；现有确定性测试验证 release/admit 与下一节点执行 |
| persistent state 是唯一真相源 | 通过 | facade `__slots__` 只保存 topology、codec 与 compiled executor，不保存运行态 |
| 无效 run 参数在 transition 前 fail closed | **不通过** | limits 直到 planner 才验证，此前可能已提交 start/fence/resume |
| commit typed result 只通过 `Graph` 可用 | **不通过** | `Graph.Transition.result` 暴露未命名空间化的内部 `TaskResult` union，公共侧无法严格判型 |
| active-state recovery 不扩展为 multi-worker arbitration | 符合范围 | 自动 fence 仅在调用方已确认旧 attempt quiescent/lost 的前提下成立；不提供 exactly-once |
| nested graph 新公共契约 | 符合范围 | builder 不公开 nested composition，意外进入 nested coordination 时 fail closed |

## 4. 验收阻断项

### 4.1 高：非法 limits 在 authoritative state 已改变后才被拒绝

涉及位置：

- `src/mote_kernel/execution/_graph.py:473-500`；
- `src/mote_kernel/execution/engine/planner.py:16-24`；
- `src/mote_kernel/execution/limits.py:6-11`。

`Graph.run()` 的顺序目前是：

```text
compile
    -> optional StartGraphRun / FenceGraphExecution / ResumeGraphNodes commit
    -> construct ExecutionLimits
    -> executor.prepare
    -> plan_tasks validates limits
```

`ExecutionLimits` 自身没有校验；正数校验只发生在 planner。此时 `run()` 已可能确认并持久化一个或多个 transition。

本地确定性反例得到：

```text
invalid-new      ExecutionLimitError ['StartGraphRun']       node_calls=0
invalid-recovery ExecutionLimitError ['FenceGraphExecution'] node_calls=0
invalid-resume   ExecutionLimitError ['ResumeGraphNodes']     node_calls=1
```

其中第三个计数中的一次调用来自构造 failed state；非法 resume 调用本身没有执行 node，但已经把失败节点转换回 Pending。

影响不是单纯“异常类型稍晚”：

1. 新 run 的 commit callback 已经持久化 `RUNNING` state，但调用方只收到异常，没有拿到返回 state；
2. active recovery 已经清除 exact lease/resources，之后才发现本次 limits 无效；
3. resume 已经消费 failure/interrupt settlement 并形成 Pending override，之后才失败；
4. 使用方可能合理地把参数校验异常理解为“调用未生效”，与 authoritative store 中的事实不一致。

#### 必须修复

在 compilation、`StartGraphRun`、fence、resume 或任何 commit 回调之前，由 execution-limits 的唯一 owner 完成一次统一校验。非法 limits 必须满足：

- commit callback 调用次数为零；
- 输入 state 完全不变；
- 不创建 claim，不执行 node；
- 新 run、active-state recovery、failure/interrupt resume 三条路径行为一致。

至少增加 `max_supersteps <= 0` 与 `max_parallel_tasks <= 0` 的参数化 facade 测试，并分别覆盖新 run、active state 和 awaiting-resume state。

### 4.2 中高：公共 commit 契约无法只通过 `Graph` 严格处理 typed node result

涉及位置：

- `docs/graph-facade-implementation.zh-CN.md:33`；
- `docs/graph-facade-implementation.zh-CN.md:110-118`；
- `src/mote_kernel/execution/_graph.py:110-116`；
- `src/mote_kernel/execution/_graph.py:232-235`；
- `src/mote_kernel/execution/__init__.py:5-7`。

方案要求使用方无需额外 import，并允许 commit 回调从 typed node result 派生 DomainState command，和 GraphState candidate 原子提交。

当前 `Graph.Transition` 虽然已命名空间化，但它的 `result` 字段实际是内部 union：

```python
TaskSuccess[OutputT] | TaskFailure | TaskInterrupt | None
```

`Graph` 没有提供这些 variant 的 namespaced alias，也没有提供等价的 public typed view 或 narrowing API。只导入 `Graph` 的严格类型代码无法安全读取成功 output：

```python
from mote_kernel.execution import Graph


async def commit(transition: Graph.Transition[str]) -> Graph.State:
    result = transition.result
    if result is not None:
        output: str = result.output
    return transition.next_state
```

Pyright 会拒绝上述代码，因为 `TaskFailure` 与 `TaskInterrupt` 没有 `output`。要正确 narrow，只能额外导入
`mote_kernel.execution.result.TaskSuccess`，这正是方案声明为 owner-internal、不得形成并列公共面的类型。

这会使 §6 的核心用例无法同时满足：

- 顶层只使用 `Graph`；
- strict typing；
- 不使用反射、`Any` 或字符串 discriminator；
- 从 typed node result 派生 DomainState command。

#### 必须修复

在不增加第二 runner、顶层 export 或兼容 alias 的前提下，为 `Graph.Transition` 提供可通过 `Graph` 命名空间严格判型的 node-result contract。可以采用 namespaced typed view、明确的 transition accessor 或等价设计，但不能要求使用方导入内部 `execution.result`。

必须增加一个 consumer-facing strict typing 测试：测试文件只从 `mote_kernel.execution` 导入 `Graph`，能够分别处理 success、failure、interrupt，并从 success 中取得精确的 `OutputT`。该测试不能使用 `Any`、`cast`、反射或字符串 discriminator。

## 5. 已确认成立的关键行为

### 5.1 唯一门面和无第二执行路径

- `mote_kernel.execution.__all__` 只有 `Graph`；
- implementation module 已私有化为 `_graph.py`；
- `GraphExecutor`、session、request/result、compiled topology 和 state commands 不再从 execution 顶层导出；
- facade 自身没有 scheduler、resource wave 或 settlement reducer 副本；
- architecture tests 固定 `reduce_graph_run()` 的 facade 调用 owner 和无运行态 slots。

### 5.2 Resume 合入唯一 `run()`

以下动作均由 `Graph` 创建，并只作为 `run(resume=...)` 输入：

- `resume_failed()`；
- `resume_failed_with()`；
- `resume_interrupted()`；
- `skip_failed()`。

动作先 canonicalize，再由现有 `GraphExecutor.resume()` 完成 settlement variant、interrupt identity、codec、routing 和重复 action 校验。恢复 command 确认后，代码立即回到与普通执行相同的 `_drive()`。

### 5.3 State-driven 自动推进

当前状态驱动闭包与方案一致：

```text
state is None
    -> StartGraphRun

state.execution is active
    -> exact FenceGraphExecution

resume actions exist
    -> ResumeGraphNodes

RUNNING + executable
    -> prepare -> ClaimGraphExecution -> session -> SettleGraphNode*

RUNNING + settled
    -> AdvanceGraphFrontier / CompleteGraphFrontier -> continue

RUNNING + awaiting resume / COMPLETED / ABORTED
    -> return boundary result
```

这证明“按读取的 GraphState 自动推进调度”已经实现；它不是按内存 session 或最近一次 result 猜测位置。

### 5.4 Commit 与错误边界

- reducer candidate 在 commit 前纯计算；
- commit 返回值必须与 candidate 精确相等，否则立即停止；
- node success output 只在 settlement commit 确认后加入本次调用的 transient outputs；
- ordinary node/session error 先关闭 session，再 fence exact active token；
- commit 自身异常时不根据未确认 candidate 擅自 fence；
- recovered active claim 可在新 invocation 中 fence 后重新 claim。

## 6. 范围边界与非阻断观察

### 6.1 Active state 自动 fence 有明确调用方前提

`run(state=active_state)` 会无条件投影 exact fence。该行为符合实施方案，但只在“调用方已确认旧 attempt 停止或丢失”时安全；传入 active state 本身必须被视为这项确认。

如果旧 session 仍在运行，旧 attempt 与 reclaimed attempt 可能都已产生 Port 副作用。store 的 revision/token guard 可以拒绝旧 settlement，却不能撤销外部副作用。方案已明确排除 multi-worker arbitration、Port 幂等和 exactly-once，因此本项不作为本轮代码缺陷，但建议在 `Graph.run()` 公共 docstring 与 README 中直接写出该前置条件，而不只放在实施文档中。

### 6.2 Cancellation 保留 active lease 供后续恢复

`CancelledError` 不走 ordinary `Exception` fence 分支。底层 session 会先完成 close/quiescence，再传播 cancellation，authoritative state 仍保留 active token；调用方重新读取该 state 后，下一次 `run(state=...)` 会自动 fence/reclaim。这与“取消期间不预测 durable outcome”的保守边界一致。

建议增加 facade 级 cancellation 回归，直接证明 commit log 最后保留 claim、worker 已停止，并能从读取的 active state 自动恢复。现有 engine/session 测试已覆盖底层 close 行为，因此该测试缺口暂不单独阻断。

## 7. 测试与门禁结果

### 7.1 定向验证

- `tests/execution/test_graph_api.py`：15 passed；
- facade + 两组 architecture tests：35 passed；
- 普通 output、并发独立 run、conditional/join、resource waiter、failure/interrupt/skip resume、commit mismatch、active claim recovery、node/session error、nested boundary 与 aborted state 均通过。

额外最小反例稳定复现：

- invalid new run 已 commit `StartGraphRun`；
- invalid active recovery 已 commit `FenceGraphExecution`；
- invalid resume 已 commit `ResumeGraphNodes`；
- 只导入 `Graph` 的 typed commit consumer 无法 narrow `transition.result`，Pyright 报错。

### 7.2 `make check`

完整通过：

- Ruff lint：通过；
- Ruff format：111 files already formatted；
- Pyright strict：0 errors、0 warnings、0 informations；
- Pytest：538 passed；
- Coverage：2,337 statements、712 branches，statement/branch 100%；
- sdist/wheel build：成功；
- Twine package check：通过。

### 7.3 Monorepo pre-commit

标准 `pre-commit run --all-files --show-diff-on-failure` 全部通过，包括：

- large file、case conflict、merge conflict；
- TOML、YAML、EOF、line ending、trailing whitespace；
- Ruff、Ruff format；
- rustfmt；
- detect-secrets。

`git diff --check HEAD^ HEAD` 通过。验收基线提交与 `origin/main` 一致，执行验收时工作树干净。

## 8. 首次验收意见（已由第 0 节覆盖）

实现已经正确完成用户特别强调的三项结构性目标：

1. resume 是 `run()` 参数；
2. 所有公共构建、恢复和执行能力收敛到唯一 `Graph`；
3. `run()` 按传入的 authoritative `GraphRunState` 自动推进唯一调度链。

但“无效 limits 已提交状态”和“typed commit 仍要求内部 import”分别破坏 fail-closed authoritative 边界与唯一公共面的可用闭包，均不能作为非阻断风格问题处理。

**首次验收判定：暂不通过。修复第 4.1、4.2 节并补充对应行为/类型测试后，需要再次验收；其余核心执行语义可以保留。**
