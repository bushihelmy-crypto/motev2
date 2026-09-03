# Graph 内存语义 P3-M4 / P3-M5 当前代码评审（2026-09-04）

状态：**代码 review 通过；P1-1、P1-2 均已关闭；全仓 commit 门禁不在本次复审中重跑**

评审基线：

- `a7605f1`：P3-M3 Join occurrence identity；
- `6f11193`：P3-M4 nested family 并发驱动；
- P3-M5：当前工作树中仅属于本阶段的组合回归与质量收口，集中在
  `test_family_driver_local_ownership.py`、`test_resource_protocol.py`、`test_graph_api.py` 和
  `test_graph_examples.py`。

工作树中执行层之外的并行修改（包括 hooks、logging/observability 等）、旧 P1 评审文档和其他计划修改均不属于本评审，
不修改、不归因，也不纳入 P3-M4/P3-M5 结论。

## 1. 判定边界

1. 只审公开 `Graph` 可达的进程内真实调用链，不把 durable/distributed、手工伪造 hostile 内部对象或几乎不可达的理论组合
   升级为 finding；
2. nested child 继续由唯一 family driver 递归驱动；不得增加 child runner、第二 scheduler、隐藏状态或兼容执行路径；
3. parent、child 和普通 sibling 可以并发执行，但每个 scope 的 `GraphRunState`、execution lease、frame 和 child boundary 必须
   在现有 reducer/commit/owner 边界完整收口；
4. typed failure、interrupt、普通异常和取消保留各自异常边界；一个 worker 的结束不得留下另一个已停止 worker 的幽灵 active
   lease，也不得伪造 terminal State；
5. P3-M5 只验证和收口共享规则与真实组合，不以测试数量或复杂度指标替代设计判断；没有真实问题就停止。

## 2. 审查台账

| 项目 | 当前状态 | 证据 |
| --- | --- | --- |
| P3-M4 复用唯一 family owner，不新增 child runner/scheduler/state | 已核对通过 | `Graph.run()` 仍只装配 `_GraphRun`；普通 callable 仍只由 `GraphExecutionSession` / `TaskScheduler` 调用；`_drive_workers()` 只持有 owner-local task handle，不创建第二状态或 reducer 路径 |
| parent ordinary session 与当前 frontier 的 active children 可共同推进 | 已核对通过 | `_execute_frontier()` 以一个 family fan-in 同时驱动 parent session 和当前 occurrence 的 child handles；公开 child/sibling 互相解锁用例在 `max_parallel_tasks=1/64` 均完成 |
| 多个 child、共享 child definition、scope/run identity 与 child boundary 不串线 | 已核对通过 | child handle 仍由完整 parent activation 派生 scope/run；两个 active children 可并发完成；两个 child completion order 均只向 parent Join 结算一次且读取各自 boundary |
| typed failure / interrupt 与普通 sibling、resource waiter 的优先级 | 已核对通过 | 已有与新增公开测试覆盖 failed child、awaiting child、ordinary/resource sibling；pending sibling 先完成，再按现有 family 规则 abort 被 sibling failure 取代的 awaiting child |
| 外部 invocation cancellation 的 family 收口 | 已核对通过 | active child、parent session 和 resource 同时存在时，调用方取消仍递归关闭 task/session，fence 后 abort 各个 running scope；`max_parallel_tasks=1/64` 的公开回归通过 |
| worker 普通异常后的 owner/lease 收口 | **复审已核对通过** | `_drive_workers()` 现在先等待并取消 family 内所有 worker，再沿 child owner 递归 `fence_after_worker_failure()`；公开 parent→child→grandchild、child→parent 和 sibling-child 方向均确认所有已停止 scope 清除 lease、保留 `RUNNING`、不生成 `AbortGraphRun`，原异常仍为 primary |
| node-origin cancellation 的 lease 边界 | **已修复，续审通过** | `_drive_workers()` 将 `_node_origin_cancellation` 与 commit-origin cancellation 一并排除在 family fence 外；root 与 root+active-child 回归确认无 `FenceGraphExecution`/`AbortGraphRun` 且 active lease 保留，详见 Finding P1-2 与第 7 节 |
| P3-M5 fan-out + conditional + Join 与 cyclic Join + nested child | 已核对通过 | 新公开组合回归分别证明互斥 branch 后的单 activation fan-out/Join，以及两个 cyclic Join occurrence 各自启动并读取两个 nested child boundary |
| P3-M5 未启动 coroutine/task cleanup | 已核对通过 | `_drive_workers()` 的 task 创建失败路径关闭尚未交给 event loop 的 coroutine，并取消、等待已经创建的 worker；无残留 `mote-graph-family:*` task |

## 3. Findings

### P1-1：并发 worker 的普通异常会留下另一个 scope 的幽灵 active execution lease（复审已修复）

初审位置：`src/mote_kernel/execution/family_driver.py` 的 worker fan-in、session cleanup，以及
`src/mote_kernel/execution/facade.py` 的普通异常出口。

初审时，`_drive_workers()` 只取消并等待其余 Python task；被取消的 `_consume_session()` 会按“外部取消”边界关闭
scheduler，却保留 active execution lease，随后 `Graph.run()` 的普通异常分支只 `release()`，造成已停止 scope 的幽灵 lease。
该问题在 parent/child、child/parent 和两个 active child 的公开组合中均可复现。

当前修复沿现有 owner/State/reducer 路径收口：fan-in 先等待所有 worker 结束，再由同一 `_GraphRun` 递归调用 child
`fence()`，最后对仍持有 lease 的当前 scope 提交 `FenceGraphExecution`；只清除 lease，保持 `RUNNING` 诊断状态，不合成
`FAILED`/`ABORTED`，并保留确定性的原 worker 异常。修复没有增加 shadow lease、补偿 ledger、child runner 或兼容执行路径。

公开回归现在确认：

- parent ordinary failure 会递归 fence parent、child、grandchild；
- child ordinary failure 会 fence active parent；
- 一个 child failure 会 fence 仍 active 的 sibling child；
- 所有已停止 scope 的 authoritative State 都没有 active lease，且没有 `AbortGraphRun`，原异常仍为 primary。

因此 P1-1 已关闭；本轮新增的取消边界问题见 P1-2，不与该根因合并。

### P1-2：fan-in 将 root node-origin cancellation 误判为普通 failure，提前回收 authoritative lease

状态：**历史阻断；已修复并通过本地回归，最终结论见第 7 节。**

位置：`src/mote_kernel/execution/family_driver.py:672-710`、`:723-808`，以及
`src/mote_kernel/execution/facade.py:654-663`。

`_consume_session()` 对 root callable 自发的 `CancelledError` 已明确设置 `_node_origin_cancellation` 并原样上抛（`:683-685`），
目的是让 facade 的 node-origin 分支执行 `finish_root(None)`、保留已确认 State 和 active lease。修复前 `_drive_workers()` 只
把 `_commit_origin_cancellation` 从 family failure 中排除（历史位置 `:791-795`）；root node-origin cancellation 因而落入
`family_failure`，触发 `fence_after_worker_failure()`，额外提交 `FenceGraphExecution`。

这是公开、确定的最小调用链，不依赖 durable/distributed 或 hostile State：一个 root callable 直接抛
`asyncio.CancelledError` 即可复现。当前 commit 记录为：

```text
CancelledError CancelledError('node')
[('StartGraphRun', 'RUNNING', False, 0),
 ('ClaimGraphExecution', 'RUNNING', True, 1),
 ('FenceGraphExecution', 'RUNNING', False, 2)]
```

基线 `6f11193` 的同一公开场景只有 `StartGraphRun`、`ClaimGraphExecution`，并保留 active lease。该边界也被现有契约明确写出：

- `docs/complexity-49-simplification-review.zh-CN.md:296-300` 的取消矩阵要求 node-origin 使用 `finish(None)`、保留状态；
- `docs/execution-state-frontier-call-chain.zh-CN.md:275-282` 要求 session cancellation 只 close、保留 authoritative lease，后续
  recovery 在调用方确认旧 attempt 停止后才 exact fence；
- `README.zh-CN.md:36` 说明 active lease 的 reclaim 只能由后续显式 state-led recovery 确认触发。

当前额外 fence 在 `Graph.run()` 向调用方传播取消之前就改变了 authoritative lease/revision，直接违背 node-origin 的
`finish(None)` 契约；commit callback 会实际收到并确认这条不应发生的 fence。带 active child 的同一场景还会递归 fence child，
扩大这次错误回收。

修复只收窄 fan-in failure 判定：精确的 root node-origin cancellation 与 commit-origin cancellation 一样，先取消并等待
sibling Python task，但不调用 family fence；保留各 scope 的 authoritative active lease，让已有 facade/recovery 边界处理后续
回收。普通异常仍走 P1-1 的递归 fence；没有通过新 runner、shadow state、兼容 wrapper 或 durable 假设绕过这条区分。
standalone root 与 root+active-child 的确定性断言（无 `FenceGraphExecution`、lease 仍 active）已补充，并保留原
`CancelledError` 对外传播。

## 4. 初审验证（修复前，历史记录）

已运行 P3-M4/P3-M5 四个核心测试文件（含当前新增组合回归）：

```text
167 passed in 0.70s
```

已运行全部 `tests/execution` 回归：

```text
759 passed in 2.55s
```

修复前的公开 node-origin probe（root callable 直接抛 `CancelledError`）稳定复现了 P1-2：当时产生一个额外
`FenceGraphExecution`；带 active child 时 root 与 child 都被 fence。生产源码单独 Pyright 通过：`0 errors, 0 warnings,
0 informations`；`git diff --check` 通过。

完整门禁当前仍未通过：

- 修复前的 `make check` 在并行范围外的 typecheck 修改处停止；该失败不属于 P3-M4/P3-M5。本阶段执行层相关源码单独检查通过，
  此前完整 lint/typecheck 通过后，complexity ratchet 仍失败；
- 直接对执行层相关源码运行 Pyright（`family_driver.py`、`facade.py`、`engine/session.py`）仍为
  `0 errors, 0 warnings, 0 informations`；
- 单独 `make test` 为 `1406 passed, 1 failed`：complexity ratchet 失败；覆盖率因新增 fence 分支未覆盖为
  `99.80%`（14 statements / 4 partial branches）；
- `make complexity` 的 zero-debt health 通过，但 `make complexity-ratchet` 因当前工作树的结构指标超过 ratchet 失败。该增长还包含
  本轮范围外的 hooks、logging/observability、events 等并行改动，不能把指标命中直接归因给 P3-M4/M5；
- 根目录 `pre-commit --all-files` 的通用文件检查、Ruff、Rustfmt 和 persistence Python static 通过；kernel complexity hook
  同样因上述 ratchet 失败，后续 Cloudflare hooks 在环境超时前未完成，不能视为通过。

因此门禁结果只作为交付阻断证据，不替代前面的设计判定；P1-2 仍需先修复并补齐确定性测试。当前工作树未再出现旧
`_ChildHandle` slots 断言或新增测试返回值的类型错误。

## 5. 初审结论（修复前，历史记录）

修复前 P3-M4/P3-M5 尚未达到 commit 条件。P1-1 的普通异常 lease 收口已通过复审；唯一新的设计阻断是 P1-2 的
node-origin cancellation 误 fence。覆盖率和全局 complexity ratchet 仍未闭合；它们是交付门禁问题，不改变前述设计判断。
除上述确定性问题外没有发现其他真实 finding；后续只需修复这条取消分类、补齐其最小回归和门禁，不扩展到
durable/distributed 或理论 hostile 组合。

## 6. 续审记录（2026-09-04，修复前，历史记录）

本次续审明确排除工作树中的 `events/*/port.py`、`hooks/*/port.py`、`logging/port.py`、
`observability/port.py` 及其配套测试；这些并行改动不参与 P3-M4/P3-M5 的判定，也不作为门禁归因。

对执行层当前代码重新核对并复现了取消边界：

- standalone root callable 自发抛出 `asyncio.CancelledError("node")` 时，公开 `Graph.run()` 仍收到原取消对象，
  但 commit 顺序稳定包含 `StartGraphRun`、`ClaimGraphExecution`、`FenceGraphExecution`；后者清除了 root 的
  authoritative active lease；
- 同样的 root + active child 场景稳定对 child 和 root 各提交一条 `FenceGraphExecution`；这不是调用方取消，也不依赖
  durable/distributed 或伪造对象，因此仍是 P1-2 的同一问题，不新增另一个 finding；
- 普通异常递归 fence、typed failure/interrupt、Join/nested boundary、资源等待和 task cleanup 未出现新的可达问题。

续审回归结果：四个 P3-M4/P3-M5 核心执行测试文件 `171 passed`；全部 `tests/execution` 为 `763 passed`；执行层
`family_driver.py`、`facade.py`、`engine/session.py` 的 Pyright 为 `0 errors`。这些结果只证明现有实现未引入额外回归，
不能抵消 P1-2 的设计阻断；在该问题修复前仍不满足 commit 条件。

## 7. 修复后续审结（2026-09-04）

### 7.1 修复内容

`_drive_workers()` 现在把 `_node_origin_cancellation` 与 `_commit_origin_cancellation` 一并排除在 family fence 之外：

1. 先取消并等待同一 family 内所有仍在运行的 Python worker，保证没有未收口的 task；
2. node-origin cancellation 不调用 `fence_after_worker_failure()`，不提交额外的 `FenceGraphExecution`；
3. 保留每个 scope 已确认 State 中的 active lease，由既有 facade/recovery 边界决定后续回收；
4. 普通异常仍沿唯一 family owner 递归 fence，P1-1 的 lease 防御不变；
5. 原始 `CancelledError` 对外传播，未引入第二 runner、shadow state 或兼容路径。

### 7.2 回归证据

- `test_root_node_origin_cancellation_rethrows_without_invocation_abort`：root 只提交
  `StartGraphRun → ClaimGraphExecution`，没有 `FenceGraphExecution`/`AbortGraphRun`；
- `test_root_node_origin_cancellation_preserves_active_child_lease`：root + active child 均保留 authoritative active lease，
  没有任何 `FenceGraphExecution`/`AbortGraphRun`，原取消对象按 identity 传播；
- 同时保留 nested node-origin cancellation 的 typed parent failure 语义；
- 四个 P3-M4/P3-M5 核心文件：`172 passed`；
- 全部 `tests/execution`：`764 passed`；
- 对完整 execution 测试集收集 `family_driver.py` 分支覆盖率：`786 statements / 280 branches，100%`；
- `make lint`、`make typecheck` 通过；`git diff --check` 通过；执行层相关 Pyright 仍为 `0 errors`。
- `make complexity`（zero-debt health）通过；仅包含 HEAD 与本阶段 staged Graph 变更的隔离快照运行
  `test_complexity_gate.py` + `test_semantic_index.py` 为 `22 passed`；当前混合工作树的全局 `make complexity-ratchet` 仍只因并行
  模块的额外结构指标失败，具体边界见下文。

### 7.3 设计结论与门禁边界

P1-1、P1-2 的真实调用链问题均已关闭。普通 worker failure 仍会清理整个 family 的已停止 lease；node-origin 和
commit-origin cancellation 则保持 authoritative lease，避免在调用方尚未确认旧 attempt 停止前误 fence。两条路径的差异由
`_GraphRun` 现有 owner 和 cancellation provenance 字段表达，没有新增状态 owner。

本地 execution 回归和 `family_driver.py` 100% 覆盖率已通过；混合工作树的全局 `complexity-ratchet`/部分 `make check` 仍可能
受并行的 events、hooks、logging/observability 改动影响。该条件性门禁失败不归因于本阶段，也不通过提高阈值规避；最终 commit 前仍需
按仓库规则记录实际全局门禁结果。当前执行层 `ruff format --check` 已通过；隔离 Graph staged 快照的 complexity gate 也已通过。

本轮未运行、未审查、未归因任何 port 并行改动；它们不能用来替代本阶段执行层的格式和全局门禁收口。

当前评审结论：**P3-M4/P3-M5 生产逻辑与关键回归已收口，P1 阻断已关闭；设计 review 通过，并已提交为 `5abe5d2`。**

## 8. 复审收口

本次复审没有发现 P1-1、P1-2 之外的真实、公开可达问题；没有把 durable/distributed、伪造 hostile 对象、ghost activation
或几乎不可达的理论组合升级为 finding，也没有要求为复杂度指标增加抽象或第二路径。复审完成后按要求不再运行测试；第 7.2 节只保留
此前已经取得的验证记录。port、hooks、logging/observability 等并行改动继续不纳入本结论。
