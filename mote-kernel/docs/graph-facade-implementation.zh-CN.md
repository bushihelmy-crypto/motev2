# Graph 唯一公共门面实施方案

> 核心原则：对外只暴露一个 `Graph` 门面；使用方只通过它构建图并调用同一个 `run()`，resume 合入 `run()`，其余 execution/state 基础设施均不形成并列公共入口。

## 1. 文档信息

- 状态：Implemented
- 日期：2026-08-15
- 所属项目：Mote Kernel
- 公共入口：`from mote_kernel.execution import Graph`
- 内部复用：现有 compiler、`GraphExecutor`、claim、session、scheduler、settlement、routing 与 pure reducer
- 设计参考：LangGraph 的 builder/compiled runtime 分离与 resume-as-run-input 思路
- 约束来源：零已知架构负债、唯一真相源、最佳整体改动、严格不超需求边界

本文扩展 `docs/frontier-node-settlement-implementation.zh-CN.md` 的公共组合层，不替换其节点级 settlement 与恢复协议。发生执行语义冲突时，节点级 settlement 文档仍是 state/reducer/runtime 的权威依据；本文只拥有公共门面的组装与驱动契约。

## 2. 唯一公开面

`mote_kernel.execution.__all__` 只包含：

```python
["Graph"]
```

以下类型继续由各自 owner module 持有，但不从 `mote_kernel.execution` 重导出：

- `GraphExecutor` 与 prepared claim；
- `GraphExecutionSession`；
- `StepRequest`、`ResumeRequest` 与 prepare/result dispositions；
- `GraphDefinition`、`CompiledGraph`、edge/node/resource definitions；
- `GraphRunState`、command、reducer 与 settlement 类型。

不保留旧公开 alias、兼容 wrapper 或第二入口。门面所需的 outcome、result、state 与 transition 类型只通过 `Graph.Outcome`、`Graph.Result`、`Graph.State`、`Graph.Transition` 命名空间引用；commit 中的 canonical node-result variants 只通过 `Graph.SuccessResult`、`Graph.FailureResult`、`Graph.InterruptResult` 判型。公共执行异常以 `Graph.Error` 为统一基类，并通过 `Graph.ValidationError`、`Graph.SnapshotMismatchError`、`Graph.ExecutionLimitError` 精确捕获。使用方无需额外 import，底层 result 或 error DTO 也不复制为第二份模型。

## 3. Builder 与 immutable runtime

`Graph[InputT, OutputT]` 直接提供：

- `add_node()`；
- `add_edge()`；
- `add_conditional_edge()`；
- `add_join()`；
- `set_resume_codec()`；
- `success()`、`failure()`、`interrupt()` outcome factories；
- failure/interrupt/skip resume action factories；
- 唯一执行方法 `run()`。

图入口与出口使用对称的虚拟边界：`add_edge(Graph.START, node_id)` 声明入口，`add_edge(node_id, Graph.END)` 声明终止路径；多入口通过多条 START edge 表达，不保留独立 `set_entry()` 或第二份入口配置。START edge 只在 builder 内投影为现有 canonical entries，不进入 compiled edge、Frontier 或 runtime task。

资源需求只在 `add_node(..., resources=(...))` 声明。门面按资源首次出现顺序自动生成并去重底层 `ResourceDefinition`，不提供要求调用方重复登记同一事实的独立 `add_resource()`；节点自身重复同一资源仍由现有 compiler fail closed。

每次 `run()` 首先由 `ExecutionLimits` 唯一 owner 构造并校验 limits；非法值在 compilation 和任何 commit 之前 fail closed。第一次有效 `run()` 随后同步完成定义校验与 compilation，并缓存 immutable compiled topology。此后所有 topology mutation fail closed。`Graph` 实例只保存定义与 compiled executor，不保存以下 runtime facts：

- 当前或最近一次 `GraphRunState`；
- live session/task；
- run output；
- resume input；
- child state；
- Store handle。

因此 compiled graph 可并发服务多个独立 run，而不会成为 durable state 的第二份真相。

## 4. 唯一 `run()` 路径

概念契约：

```python
result = await graph.run(
    node_input,
    run_id="stable-run-id",       # 新 run；可省略并生成本地 identity
    state=authoritative_state,     # 继续/恢复 run 时传入
    resume=(...),                 # 可选，恢复动作也是 run input
    commit=commit_transition,      # 可选，逐 transition authoritative commit
    max_supersteps=1000,
    max_parallel_tasks=64,
)
```

内部唯一闭包为：

```text
validate ExecutionLimits
    -> StartGraphRun / received authoritative state
    -> optional FenceGraphExecution for a quiescent recovered lease
    -> optional ResumeGraphNodes
    -> prepare
    -> ClaimGraphExecution
    -> GraphExecutor.execute
    -> session.next(authoritative state)
    -> one SettleGraphNode
    -> confirmed authoritative successor
    -> next session event / waiter scheduling
    -> persisted SETTLED barrier
    -> AdvanceGraphFrontier / CompleteGraphFrontier
```

`run()` 不实现另一套 scheduler、resource wave、batch settlement 或 routing。所有执行均回到现有唯一 engine。

## 5. Resume 合入 `run()`

不提供公开 `Graph.resume()`。选择性恢复动作由门面 factory 创建，并作为 `run(resume=(...))` 的输入：

- `resume_failed(node_id)`：失败节点重跑并使用本次普通 run input；
- `resume_failed_with(node_id, input)`：失败节点以 codec 编码的 override 重跑；
- `resume_interrupted(node_id, interrupt_id, input)`：按 exact interrupt identity 恢复；
- `skip_failed(node_id, reason, route=...)`：跳过失败节点并贡献合法 routing。

`run()` 将动作 canonicalize 后复用 `GraphExecutor.resume()` 投影唯一 `ResumeGraphNodes`，确认其 authoritative successor 后立即回到同一 prepare/claim/session path。stale interrupt identity、错误 settlement variant、重复 action 与不匹配 graph state 均 fail closed。

## 6. Commit 与唯一真相源

未提供 commit 回调时，门面通过 pure `reduce_graph_run()` 做明确的进程内运行，适合无持久化的简单调用；该模式不声称 crash recovery 或 durability。

提供异步 commit 回调时，每一次 transition 都收到：

- `previous_state`；
- state-owned `command`；
- pure reducer 生成的 `next_state` candidate；
- node settlement 时对应的 transient typed `result`，其他 transition 为 `None`；其 success、failure、interrupt variants 分别通过 `Graph.SuccessResult`、`Graph.FailureResult`、`Graph.InterruptResult` 严格判型。

回调可以据此将 GraphState candidate 与由 node result 派生的 DomainState command 原子提交。门面只有在回调返回与 candidate 精确相等的 authoritative `GraphRunState` 后才更新本地 snapshot 并继续；错误或不一致确认立即停止执行。

这保证：

1. 门面不预测 Store 中的 state；
2. 每个节点 completion 都有独立可提交 command；
3. resource release 与 waiter admission 已包含在同一 reducer successor；
4. transient output 不被误当成 durable GraphState；
5. 不新增具体 Store、数据库或 journal。

## 7. 运行边界

- 已确认 commit 的节点 settlement 在后续错误中保留；
- ordinary node error 先关闭并确认 session quiescent，再 fence exact active token，随后原样传播；
- commit 回调自身报错时 authoritative outcome 未知，门面关闭 session 但不基于预测 state 擅自 fence；
- 新一次 `run(state=active_state)` 只在调用方确认旧 attempt 已停止或丢失后 fence 并恢复；本期不扩展为多 worker lease arbitration；
- `run()` 被取消时先等待 live worker quiescent，不预测 settlement 或 fence；authoritative active lease 留给下一次读取 state 后恢复；
- 返回结果的 `outputs` 仅包含本次 `run()` 调用中已确认 settlement 的 transient success output；它不是 output store。

当前公共 builder 不暴露 nested graph composition。现有 nested runtime 与测试继续保留在内部 owner path；在 child output aggregation/persistence 尚无权威契约时，门面不得引入隐藏 child state 或仅在内存成立的恢复承诺。这一边界不删除既有能力，也不新增第二 runner。

## 8. 明确不纳入范围

1. 具体 Store、数据库、journal 或 event log；
2. Graph 内建 retry/backoff；
3. Port 副作用幂等或补偿；
4. Graph exactly-once；
5. generic output persistence/aggregation；
6. multi-worker lease 协调；
7. nested graph 的新公共 output contract；
8. legacy symbol absence、已删除文件或全仓字符串扫描门禁。

## 9. 验收

1. 使用方只需导入 `Graph`，并可严格判型 commit result、精确捕获公共 validation/snapshot/limit errors；
2. 普通 output 与 typed success/failure/interrupt 均走同一 Node adapter 与 scheduler；
3. direct/conditional/join/resource topology 经现有 compiler 校验；
4. 每条 start/claim/settle/resume/fence/resolve command 均独立经过 commit 确认；
5. resource waiter 在前驱 settlement successor 中 admitted，并在同一 session 下一步立即调度；
6. failure、interrupt 与 skip 全部通过 `run(resume=...)`；
7. 非法 limits 在新 run、active recovery、resume 三条路径均零 commit、零执行；
8. active claim 恢复、cancellation、session error、node error 与 commit mismatch fail closed；
9. 原有有效测试全部保留，不新增 legacy gate；
10. 新公共代码语句与分支覆盖 100%；
11. 完整 `make check` 与 monorepo pre-commit 通过。
