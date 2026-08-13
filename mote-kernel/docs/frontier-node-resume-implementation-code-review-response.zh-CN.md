# Frontier Node Resume 当前实现代码审核正式回复

## 1. 回复信息

- 回复对象：`docs/frontier-node-resume-implementation-code-review.zh-CN.md`
- 回复日期：2026-08-14
- 实施依据：`docs/frontier-node-resume-implementation.zh-CN.md`
- 验证环境：用户指定的 `metagpt` Conda 环境，Python 3.11.15
- 当前结论：审核指出的三个 correctness 缺陷和一个镜像 DTO 债务均已关闭；fence/abort surface 已统一文档口径

## 2. 总体回复

接受原审核关于以下问题的事实判断：

1. 非 executable Frontier 曾可提交 resource admission，形成无法 resume 的稳定状态；
2. parent-bearing child state 曾未在 authoritative admission 边界强制 deterministic child run ID；
3. ABORTED retained Frontier 曾在 compiled node membership guard 之前提前返回；
4. `ExecutionSnapshot` 曾是无生产消费者的 `GraphRunState` 逐字段镜像。

这些问题不是仅靠既有覆盖率即可排除的理论风险，前三项均可由公开 reducer 或 executor admission 路径稳定构造，属于本次 Frontier node resume
开发边界。现已按 owner 边界修改生产代码并加入确定性反例测试，原审核报告中的 correctness blocker 不再存在。

审核范围同时按以下两点收敛：

- root/child 是当前 `GraphExecutor` composition 的权限，不是 graph definition 的全局永久角色；同一个 child definition 在独立
  `GraphExecutor(child)` 中仍可合法作为 root；
- fence/abort 只依赖 authoritative state facts，直接构造唯一 state-owned command 是正式认可的 surface，不增加无校验价值的 executor
  passthrough wrapper。

## 3. 已实施修复

### 3.1 Resource admission 状态闭包

已在 transition 与 stable validation 两层闭合：

- `UpdateGraphResources` 只允许作用于 quiescent、`RUNNING` 且 derived Frontier status 为 `EXECUTABLE` 的状态；
- 任何带 `resources` 的 stable/recovered state 必须至少包含一个当前 Pending node；
- acquisition participants 继续只能是当前 Pending nodes 的子集；
- state 不解释 compiled resource requirements，具体 resource participant subset 和 acquisition waves 仍由 execution owner 校验。

因此 Failed-only、Interrupted-only 及其他无 Pending Frontier 均不能创建或保留 resource admission；正常 Pending admission、replay extension、exact
fence 和 settlement 路径保持不变。

新增回归覆盖 Failed-only 与 Interrupted-only 两类真实反例，避免只修复其中一种 settlement 形状。

### 3.2 Nested deterministic child identity 与 composition authority

已完成四层闭合：

1. stable validator 对任意 `parent != None` 的 state 强制：

   ```python
   state.run_id == child_graph_run_id(
       state.parent.run_id,
       state.parent.superstep,
       state.parent.node_id,
   )
   ```

2. parent-bearing start projection 拒绝调用方提供的任意 child run ID；
3. `GraphExecutor.start_command()` 只启动当前 composition root，不再接收 parent；
4. 当前 executor 的 root definition key 拒绝 parent-bearing state，family 内非 root key 拒绝 `parent=None`，并继续校验 parent node 属于已编译的
   nested relationship。

修复没有把 graph definition 固化为全局 root 或 child。正向测试证明，同一个 child definition 单独构造 `GraphExecutor(child)` 后仍可作为
`parent=None` 的 root 启动和 prepare。

stable validator、start projection、child projections、claim 后重建与 nested resume 继续共用唯一 state-owned `child_graph_run_id()`，没有复制
字符串算法或建立第二 identity owner。

### 3.3 ABORTED retained Frontier fail closed

snapshot guard 的顺序现为：

```text
state-owned stable validation
-> compiled graph identity/version
-> Frontier compiled node membership
-> terminal early return
-> RUNNING-only parent/join/codec/routing checks
```

ABORTED retained Frontier 中的 unknown node 现会在 terminal disposition 前失败。修复没有把 RUNNING-only 逻辑前移：ABORTED retained override
仍不 decode、不投递，也不重新解释 routing、join、parent projection 或执行 planning。

### 3.4 删除无消费者的 `ExecutionSnapshot`

本项属于本次开发边界，现已完整删除：

- 删除 `src/mote_kernel/execution/snapshot.py`；
- 删除 `project_execution_snapshot()` 及 public export；
- 删除只固化逐字段镜像存在的 projection tests；
- architecture tests 改为正向证明 `StepRequest`、`ResumeRequest` 与 execution guard 直接读取 authoritative `GraphRunState`。

保留的 `InvalidExecutionSnapshotError` 是 recovered execution state 不符合 compiled guard 时的错误类型，不是已删除的 `ExecutionSnapshot` DTO，二者
没有 ownership 冲突。

删除后的架构更简单：没有第二份 snapshot shape、字段漂移面、默认值、mutable cache 或第二 invariant validator。未来只有出现真实且形状不同的
execution-only 消费视图时，才应重新设计窄 projection，而不是恢复一一镜像。

### 3.5 Fence/abort command surface

实施文档已明确：

- start、admission、claim、settle、resume 等需要 compiled/execution knowledge 的路径，由 execution 投影唯一 state-owned command；
- fence 与 abort 只使用 revision、exact execution token、abort reason 等 state-owned facts，调用方可直接构造唯一 state command；
- 两类路径都不自行调用 reducer、不替换 authoritative snapshot。

因此没有新增 `GraphExecutor.fence()` 或 `GraphExecutor.abort()` 空壳 wrapper，也没有新增 reducer path、compatibility alias 或隐藏状态。

### 3.6 `DomainState` 空 package marker 的后续处置

用户明确删除了 `src/mote_kernel/state/domain_state/__init__.py`。该文件只有一行 package docstring，没有 DomainState model、command、reducer、export、
import 或运行时消费者；保留它会把尚未实施的 package 误报为已落地 owner。

因此同步从 package architecture contract 的已实现包清单中移除 `state/domain_state`。这不是删除 DomainState 的架构职责：GraphState 只记录可恢复
执行位置、未来 DomainState 记录已建立业务事实、二者独立演进并原子提交的原则仍然有效。本次也没有借此引入或设计 DomainState API。

## 4. 审核项关闭状态

| 审核项 | 原判定 | 实施结果 | 当前状态 |
| --- | --- | --- | --- |
| 非 executable Frontier 可提交 resource admission | 高 correctness | transition + stable validator 双层闭合 | 已关闭 |
| Nested child durable identity 可分叉 | 高 correctness | deterministic invariant + start/executor authority | 已关闭 |
| ABORTED 跳过 compiled node membership | 中 correctness | membership 移到 terminal return 前 | 已关闭 |
| `ExecutionSnapshot` 无消费者镜像 | 设计债务 | DTO、projection、export 与镜像测试已删除 | 已关闭 |
| Fence/abort 缺少 executor wrapper | 文档口径 | 正式认可 direct state command surface | 非缺陷，口径已关闭 |

## 5. 测试数量与新增价值

使用同一个 `metagpt` 环境，对本地 `origin/main` tracking ref
`1f8a426ce1e9bb2cff298951919592a82edb96e5` 做独立 `/tmp` 归档并执行 `pytest --collect-only`：

- 基线：461 项 collected tests；
- 当前：504 项 collected tests；
- 净增：43 项，而不是 11 项。

这是 collected test item 的净变化，不把重写、删除旧模型测试和 parametrized cases 混称为“新增 43 个测试函数”。新增与重写的有效覆盖集中在：

- Frontier settlement、mixed Pending/Failed/Interrupted 与 stable state invariants；
- deterministic interrupt/child identity exact vectors；
- selective failure/interrupt resume 与 stale identity；
- nested projection completeness、claim 后重建、root/family-child authority；
- resource replay、participant membership、Failed-only/Interrupted-only admission rejection；
- ABORTED retained diagnostic Frontier membership；
- codec、routing、join、exception/fence 与 cancellation recovery boundaries；
- owner/dependency architecture assertions。

没有增加无法由公开路径出现的对象拼装、同义断言堆砌或仅为抬高数量的测试。

在线执行 `git fetch origin --prune` 时，Git 凭据链调用了已不存在的临时 `gh` 可执行文件，且 VS Code credential socket 不可用，因此认证失败。
上述 461 严格表述为当前本地 `origin/main` tracking ref 的实测基线；不将其冒充为本次会话已在线确认的远端最新值。该 tracking ref 的本地
reflog 显示其于 2026-08-12 17:58:45 +0800 由 push 更新。

## 6. 最终门禁

最终门禁在用户已经激活的 `metagpt` 环境中直接执行，没有修改 Makefile 或降低检查规则：

```bash
cd /home/longert/motev2/mote-kernel
command -v python
command -v pyright
make check
```

结果：

- `python`：`/home/longert/anaconda3/envs/metagpt/bin/python`，Python 3.11.15；
- `pyright`：`/home/longert/anaconda3/envs/metagpt/bin/pyright`；
- Ruff check：通过；
- Ruff format check：109 files already formatted；
- Pyright strict：0 errors、0 warnings、0 informations；
- Pytest：504 passed；
- Coverage：1943 statements、634 branches、100%；
- sdist/wheel build：成功；
- Twine check：两个发行产物均通过。

随后执行：

```bash
cd /home/longert/motev2
pre-commit run --all-files --show-diff-on-failure
```

monorepo 全量 hooks 全部通过，包括 Ruff、format、detect-secrets、TOML/YAML、冲突、大小写、行尾和大文件检查。`git diff --check` 同样通过。

本次改动未改变已存在的跨语言 observable contract，不需要修改 `conformance/` 或其他语言 runner。

## 7. 最终边界与结论

本次关闭没有引入：

- store、journal、event log、history 或第二 runner；
- AgentState proposal/transaction/loading port 或默认 composition entry point；
- graph definition 全局 role registry；
- compatibility alias、fallback、双写、第二 codec/identity/validator；
- fence/abort passthrough wrapper；
- 跨语言 durable protocol 变更。

原审核报告指出的真实问题已经在约定开发边界内全部实施并通过回归与完整门禁。当前实现满足重新判定为“Frontier node resume 实施闭合”的条件；
后续若扩展 persistence、composition 或跨语言协议，应另立需求，不应反向塞入本次模型。
