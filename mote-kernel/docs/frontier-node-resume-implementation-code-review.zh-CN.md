# Frontier Node Resume 当前实现代码审核报告

## 1. 审核信息

- 审核对象：`docs/frontier-node-resume-implementation.zh-CN.md` 对应的当前工作树实现
- 审核日期：2026-08-14
- 审核范围：`mote-kernel` 的 state、execution、resource、nested、routing、codec、identity、测试与架构约束
- 审核方式：本地只读代码审核、本地测试与最小反例验证
- 安全边界：未访问网络或外部目标，未执行任何网络扫描或攻击行为，未修改被审核源码

## 2. 总体结论

**当前实现不满足最终闭合条件，不能认定为“完全闭合、唯一真相、复用基础设计且零负债”。**

实现的总体架构方向正确，质量门禁也全部通过：GraphState 已成为主要 authoritative state owner，execution 保持唯一 graph engine，revision、batch lease、exact token fence、resource、routing、join、nested 和 codec 基础设施均得到原位复用，旧 transition、旧 identity/command/codec 路径也已删除。

但审核发现三个可在本地稳定复现的状态或 guard 闭包缺陷，以及一个明确的冗余 DTO 债务。其中 resource admission 和 nested durable identity 会破坏恢复状态机或唯一身份约束，属于合入前必须修复的问题。

## 3. 审核发现

### 3.1 高：Resource command 可以制造无法 resume 的合法状态

涉及代码：

- `src/mote_kernel/state/graph_state/resource_transitions.py:37`
- `src/mote_kernel/state/graph_state/validation.py:138`
- `src/mote_kernel/execution/executor.py:113`

`update_graph_resources()` 当前只要求 GraphRun 为 `RUNNING` 且没有 active execution lease：

```python
if state.status is not GraphRunStatus.RUNNING:
    raise GraphStateTransitionError(...)
if state.execution is not None:
    raise GraphStateTransitionError(...)
```

稳定状态 validator 对 resource participants 只验证其属于当前 Pending nodes：

```python
pending = frozenset(pending_node_ids(state.frontier))
if not frozenset(acquisition.node_id for acquisition in state.resources.acquisitions) <= pending:
    raise GraphStateTransitionError(...)
```

空 acquisition 集合天然是任意 Pending 集合的子集。因此，一个只有 `FailedGraphNode`、没有 Pending node 的 `AWAITING_RESUME` Frontier，可以合法提交：

```python
UpdateGraphResources(
    expected_revision=state.revision,
    resources=ResourceSnapshot(resources=(), acquisitions=()),
)
```

本地复现结果：

```text
ResourceSnapshot(resources=(), acquisitions=())
revision = 1
```

提交后 `state.resources is not None`，而 `GraphExecutor.resume()` 明确要求 `resources is None`：

```python
if state.status is not GraphRunStatus.RUNNING or state.execution is not None or state.resources is not None:
    raise SnapshotMismatchError("resume requires one quiescent running graph")
```

此状态没有 active lease，因此无法通过 fence 清理；正常恢复路径被永久阻断，只能 abort。

#### 影响

- reducer 可以产生通过稳定校验、但无法恢复的 GraphRunState；
- `AWAITING_RESUME -> ResumeGraphNodes` 状态闭包被破坏；
- 违反 resource admission 只服务于 executable Pending nodes 的设计。

#### 建议

`UpdateGraphResources` transition 至少应拒绝没有 Pending node、或 Frontier 并非 `EXECUTABLE` 的状态。Execution 仍负责解释 compiled resource requirements；state transition 只需关闭它能够基于 state-owned facts 证明的非法生命周期组合。

### 3.2 高：Nested durable child identity 没有形成唯一闭环

涉及代码：

- `src/mote_kernel/state/graph_state/validation.py:131`
- `src/mote_kernel/execution/graph_run.py:39`
- `src/mote_kernel/execution/executor.py:59`
- `src/mote_kernel/execution/engine/frontier.py:73`

当前 state validator 对 child parent link 仅验证：

```python
if state.parent.superstep < 0 or state.parent.run_id == state.run_id:
    raise GraphStateTransitionError("parent graph activation is invalid")
```

它没有验证 durable child run identity 必须满足：

```python
state.run_id == child_graph_run_id(
    state.parent.run_id,
    state.parent.superstep,
    state.parent.node_id,
)
```

`project_start_graph_command()` 同时允许调用方传入任意 `run_id` 和 `parent`。`GraphExecutor` 的 graph-family 选择仅依据 definition ID/version；snapshot guard 只检查 parent node 是否出现在某个 compiled child key 的 parent-node 集合中。

`frontier.py` 对调用方提供的 `ActiveChild/CompletedChild/AbortedChild` 会检查确定性 child ID，但该检查只保护 parent request 的 child projection，不能保护直接恢复并交给 executor 的 child `GraphRunState`。

本地反例：

```text
EXPECTED   = 23:mote.child-graph-run.v16:parent1:66:nested
ARBITRARY  = arbitrary-child -> ExecutableFrontier
STANDALONE = standalone-child / child.g -> ExecutableFrontier
```

即：

1. 同一个 `ParentGraphActivation` 可以使用任意 child run ID；
2. graph family 中的 child definition 可以作为 `parent=None` 的独立顶层 run 被执行。

#### 影响

- 同一 nested activation 可以建立多个 durable child runs；
- failure/interrupt resume 无法从状态边界证明一定恢复原 child run；
- `child_graph_run_id()` 虽然算法唯一，但没有成为 durable admission invariant，唯一身份链未闭合。

#### 建议

child start 与 recovered child admission 必须共同强制确定性 run ID。应同时明确 root graph 与 nested graph 的启动权限：root start 只允许 root graph；nested start 必须携带合法 parent activation，并使用唯一 `child_graph_run_id()` 投影。

### 3.3 中：ABORTED snapshot 跳过 compiled node membership 校验

涉及代码：

- `src/mote_kernel/execution/engine/snapshot_guard.py:24`

当前 guard 顺序为：

```python
validate_graph_run_state(state)
if state.definition_id != graph.definition_id or state.definition_version != graph.version:
    raise SnapshotMismatchError(...)
if state.status is not GraphRunStatus.RUNNING:
    return

unknown = tuple(node.node_id for node in state.frontier.nodes if node.node_id not in graph.nodes)
```

因此，`COMPLETED/ABORTED` 在 node membership 校验之前返回。COMPLETED 使用 canonical empty Frontier，不产生实际问题；ABORTED 会保留诊断 Frontier，因而可以携带 compiled graph 中不存在的 node ID。

本地将一个合法 ABORTED snapshot 的 retained Frontier node 替换为 `unknown` 后：

```text
GraphExecutor.prepare(...) -> AbortedGraph
```

需求明确要求 ABORTED 只跳过 override decode、routing、join、nested projection 和 planning，但仍必须验证 state shape、graph identity 与 node membership。

#### 建议

将 Frontier membership 与 canonical order 放在 terminal early-return 之前；parent、join、codec、routing contribution 和 override decoder 等 RUNNING-only 检查继续保留在 early-return 之后。

### 3.4 中：`ExecutionSnapshot` 是无生产消费者的镜像 DTO

涉及代码：

- `src/mote_kernel/execution/snapshot.py:16`
- `src/mote_kernel/execution/graph_run.py:20`
- `tests/architecture/test_graph_execution_ownership.py:263`

当前 `ExecutionSnapshot` 逐字段镜像 `GraphRunState`，投影函数也只是逐字段复制 state-owned immutable values。全仓引用检查显示：

- 生产代码没有 `project_execution_snapshot()` 消费者；
- 只有 projection tests 调用该函数；
- architecture test 固化了这个逐字段镜像关系。

它目前不会形成第二套 authoritative state，但增加了公共 API、字段漂移面、测试维护和无业务价值的复制代码。实施文档已经明确：如果删除候选 execution DTO 后，调用方可以无损直接读取同一个 state value object，则必须删除该 DTO。

#### 建议

若不存在即将落地且确有组合视图需要的生产消费者，应删除 `ExecutionSnapshot`、`project_execution_snapshot()`、相关 export 和只验证该镜像存在的测试。不要为了 architecture test 本身保留 DTO。

## 4. 文档与 API 口径待收敛项

测试矩阵第 49 项要求 start、admission、claim、settle、resume、fence 和 abort 都具有 execution-facing command projection surface。

当前生产投影位置为：

- start：`project_start_graph_command()`；
- admission：`prepare_superstep()`；
- claim：`prepare_claim()`；
- settle：`settle_tasks()`；
- resume：`GraphExecutor.resume()`；
- fence：无 execution-facing projection；
- abort：无 execution-facing projection。

调用方和测试直接构造 `FenceGraphExecution`、`AbortGraphRun`。另一方面，文件级设计又把 `GraphExecutor` 明确描述为唯一 `start/prepare/execute/resume` API，因此当前文档存在口径歧义。

关闭评审前应明确以下二者之一：

1. 直接构造 state-owned fence/abort command 就是被认可的 execution-facing surface；或
2. execution 应提供窄 command projection API，但仍不得调用 reducer 或替换 snapshot。

在口径明确前，不将其单独定为功能缺陷，但测试矩阵第 49 项不能视为已被明确证明。

## 5. 已通过的架构目标

以下方向经代码、测试与 source/reference search 确认成立：

- `GraphFrontierState` 是 node settlement、failure、interrupt、skip 和 Pending input binding 的 authoritative state owner；
- `ResumeGraphNodes` 是 failure/interrupt 恢复的唯一 state command；
- `SettleGraphExecution` 是 attempt typed outcomes 的唯一 settlement command；
- `reduce_graph_run()` 是唯一 graph-run reducer dispatch；
- execution 是唯一 graph engine，没有第二 runner；
- revision CAS、batch lease、exact token fence、resource ordered acquisition、routing、join、nested 和 codec 均原位复用；
- resource participant、lease 和 parent activation 直接使用 state-owned `GraphNodeId`；
- durable claim attempt、definition、node 和 route identity 没有 execution-local duplicate `NewType`；
- 未发现 store、journal、lease history、interrupt history、resume-value tape、node lease、partial claim 或 fallback path；
- 旧 execution transition hierarchy、resolution codec、graph-local command/identity duplicate 已进入删除状态；
- 未发现 production `Any`、动态反射逃生口或 state 对 execution/compiled topology 的反向依赖；
- `conformance/` 未发现受本次变更影响的现有 observable DTO 或 runner。

## 6. 完成门禁执行结果

### 6.1 `mote-kernel` 全量检查

在用户当前环境原样执行：

```bash
cd /home/longert/motev2/mote-kernel
make check
```

结果：通过。

- Ruff lint：通过；
- Ruff format check：通过；
- Pyright strict：0 errors；
- Pytest：500 tests passed；
- Coverage：1952 statements、620 branches，100%；
- sdist/wheel build：成功；
- Twine metadata validation：通过。

### 6.2 Monorepo pre-commit

执行：

```bash
cd /home/longert/motev2
pre-commit run --all-files --show-diff-on-failure
```

结果：全部通过。

- check-added-large-files；
- check-case-conflict；
- check-merge-conflict；
- check-toml；
- check-yaml；
- end-of-file-fixer；
- mixed-line-ending；
- trailing-whitespace；
- Ruff；
- Ruff format；
- detect-secrets。

### 6.3 其他门禁

- `git diff --check`：通过；
- package build 与 metadata：通过；
- conformance impact：已检查，未发现需同步变更的共享 observable contract；
- 审核及复现未修改 tracked source；
- 测试产生的 `.coverage`、cache 和 `dist/` 均为已忽略文件，未进入 change。

## 7. 分维度判定

| 审核维度 | 判定 | 说明 |
| --- | --- | --- |
| 功能与状态闭合 | 不通过 | Resource transition 可产生不可 resume 状态 |
| Durable identity 闭合 | 不通过 | Nested child run ID 未成为 recovered-state invariant |
| Snapshot fail-closed | 不通过 | ABORTED 跳过 compiled node membership |
| 唯一事实源 | 部分通过 | GraphState ownership 正确，但 child durable identity 仍可分叉 |
| 基础设计复用 | 通过 | 既有 execution/resource/routing/join/nested/fence 均原位复用 |
| 唯一 owner 与依赖方向 | 通过 | identity、routing、codec、reducer 和 engine owner 基本清晰 |
| 零兼容路径 | 通过 | 未发现 alias、fallback、双写或第二 execution path |
| 零额外持久化负担 | 通过 | 未新增 store、journal、history 或通用 input/output persistence |
| 零代码债务 | 不通过 | 存在无生产消费者的 `ExecutionSnapshot` 镜像 DTO |
| 自动化质量门禁 | 通过 | `make check`、pre-commit、build、coverage、diff check 全绿 |

## 8. 修复优先级

建议按以下顺序关闭：

1. 关闭 resource admission 的非 Pending/非 EXECUTABLE transition 入口，并增加 Failed-only/Interrupted-only 反例测试；
2. 将 deterministic child run ID 纳入 child start 与 recovered child admission 的共同约束，区分 root start 和 nested start；
3. 调整 terminal snapshot guard 顺序，确保 ABORTED 执行 node membership 校验；
4. 删除无生产消费者的 `ExecutionSnapshot` 镜像 DTO 及固化测试；
5. 明确 fence/abort execution-facing surface 的文档口径；
6. 重新运行 `make check`、monorepo pre-commit、`git diff --check` 和最小反例测试。

## 9. 最终判定

当前实现可以评价为：

> 核心模型和基础设施复用方向正确，静态质量门禁优秀，但状态闭包与 durable identity 仍存在可执行反例，且尚有无消费者镜像 DTO 债务，因此暂不具备“完全闭合、唯一真相、逻辑简洁优美、0 负债”的合入条件。

上述三个闭包缺陷、一个 DTO 债务和一个文档口径问题全部关闭后，才建议重新判定为完成。
