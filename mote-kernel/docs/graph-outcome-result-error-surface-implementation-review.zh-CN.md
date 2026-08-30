# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案独立评审

> **结论：`CHANGES REQUESTED / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION`。**
> 将 callable outcome、commit evidence、run disposition 与 exception control flow 分开是正确的抽象；但被审方案把
> 已冻结的 public API 当成未发布 API，提出没有需求依据的 breaking rename，并把持久化、failover 与恢复协调写进了本期范围。
> 当前最合适的处置是复用现有 owner 和既有命名，保持生产代码、测试、State、Store 与执行路径不变。整改完成前不得按该方案修改
> production、tests 或 public docs。

## 1. 评审对象与边界

- 评审日期：2026-08-26
- 评审对象：[Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 评审对象 SHA256：`1e976385f5812132b1c357444dc7044c8ed28c840c35bbcf4fc3f6be2bfc9fc3`
- production baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 本文性质：独立 design review；本文只拥有本次裁决与整改要求，不拥有目标语义、API 版本决策或 production 授权。
- 本轮不修改实施正文、production、tests、State、Store、README 或其他用户文件。

评审硬边界如下：

1. 每个事实只由现有 canonical owner 持有；facade 只提供 direct namespace alias 和 delegation。
2. 复用 `execution` 的现有 outcome、settlement、reducer、commit callback、run projection 与 error owner，不复制 DTO、runner、store 或 index。
3. 不新增持久化、checkpoint、journal、跨进程 value recovery、failover、worker arbitration、retry 或 deployment migration。
4. 不以 compatibility alias、wrapper、feature flag 或第二解释路径掩盖 breaking change。
5. 只有在需求、版本边界、manifest、strict typing、complexity 和行为证据全部冻结后，才可申请 production 实施。

## 2. 现有事实与通过项

当前代码已经提供了方案想要表达的四个生命周期层，而且没有重复 runtime DTO：

```text
callable
  -> execution.graph.outcome.GraphOutcome
  -> TaskResult / settle_result()
  -> execution.result.GraphCommitResult
  -> GraphTransition.result

family driver boundary
  -> execution.result.GraphResult
  -> Graph.run() return

all graph-owned faults
  -> execution.errors.ExecutionError
  -> Graph.Error
```

具体 owner 和转换均已存在：

| 事实 | 唯一 owner | 当前证据 |
| --- | --- | --- |
| callable outcome union 与 factory seal | `execution/graph/outcome.py` | `GraphOutcome`、`_OutcomeSeal`、`_success/_failure/_interrupt` |
| task completion | `execution/result.py` | `TaskResult`、`TaskSuccess`、`TaskFailure`、`TaskInterrupt` |
| commit evidence | `execution/result.py` | `GraphCommitResult`、`_CommitResultSeal`、`_commit_result()` |
| transition 与 exact successor contract | `execution/family_driver.py` | `GraphTransition`、`GraphCommit`、`commit_transition()` |
| root run disposition | `execution/result.py` + `family_driver.py` | `GraphResult`、`project_graph_result()` |
| exception hierarchy | `execution/errors.py` | `ExecutionError` 及其既有子类 |
| 唯一 public namespace | `execution/facade.py` | `Graph`；`execution.__all__ == ["Graph"]` |

因此，“不把 outcome、node result、run result 和 error 合成一个宽 DTO”应保留为确认事实；它不需要新增 class、wrapper、factory、seal
或第二 execution path。

## 3. 阻断项与整改要求

### R1（BLOCKER）：方案错误地假设 public API 尚未发布

方案第 4.1 节以“public API 尚未发布”为前提，要求删除 `Graph.Outcome`、`Graph.Result`、三个 outcome aliases、三个 node-result
aliases 以及三个 run-result aliases。该前提与当前仓库事实直接冲突：

- 当前规范事实源第 1 节已标为 **`Implemented / normative source`**；
- 当前规范事实源第 2.2、5.2 节冻结的正是 `Graph.Outcome`、`Graph.SuccessOutcome`、`Graph.SuccessResult`、
  `Graph.CompletedResult`、`Graph.Result` 等名称；
- `README.md`、`README.zh-CN.md`、active typing fixtures、public API tests 和 recovery/continuation tests 正在使用这些名称；
- `facade.py` 当前已把这些名称直接绑定到 canonical owner（不是 wrapper 或第二 DTO）。

这不是内部重命名，而是对已使用 public contract 的 breaking change。仓库没有为该 breaking change 提供版本边界、需求 owner、迁移通知或
完整 active-file manifest。按架构规则不能一边删除旧名、一边在同一 facade 上临时保留新旧双轨。

**整改：**本方案应先冻结为 **`KEEP CURRENT PUBLIC SURFACE / NO IMPLEMENTATION`**。如果产品确实需要新命名，必须另开一个有版本决策的
API migration 需求；该需求须单独审查所有 active docs/tests/examples、semver 边界和一次性迁移清单，不得借本文直接改名。

### R2（MAJOR）：目标 alias 与 canonical alias 的关系没有闭合

`outcome.py` 当前唯一拥有 `GraphOutcome`，`result.py` 当前唯一拥有 `GraphCommitResult` 与 `GraphResult`。方案第 5、6 节又要求在
owner 中建立 `NodeOutcome`、`SettledNodeResult`、`RunResult`，同时要求删除旧名且“不保留 compatibility aliases”。这留下两种互相冲突的实现：

1. 新旧都定义：违反唯一真相和“不保留双轨”；
2. 直接替换：需要迁移 `NodeCallable`、family driver、facade、所有 typing fixtures、README 和 active docs，远不止“只调整命名和文档”。

方案没有给出 exact symbol manifest、import 迁移顺序、删除账本或失败回滚边界，因此不能作为可执行实施计划。

**整改：**在未获 R1 的独立 API 决策前，不新增这三个 union alias。若未来获批改名，必须让每个 owner 只保留一个 canonical alias，facade 只保留
一个 direct alias；不得在 facade 重新拼 union，不得新建 nominal wrapper，也不得用 `Any`、`object` 或 cast 解决迁移后的泛型错误。

### R3（MAJOR）：`SettledNodeResult` 会把“candidate evidence”误读为已确认/已持久化

当前 `commit_transition()` 的顺序是：

```text
reduce_graph_run()
  -> _commit_result(TaskResult)       # 构造 GraphTransition.result
  -> 调用可选 GraphCommit callback
  -> 仅在 callback 返回 exact candidate 后继续
```

`_commit_result()` 在 callback 之前构造 `_Graph*Result`；`commit=None` 时甚至没有外部确认步骤。它是 settlement admission 后提供给 callback 的
只读 candidate evidence，不是 durable-store receipt，也不表示 callback 成功。方案虽然在第 1、3.2 节补充了说明，但一个需要长篇免责声明才能成立的
“Settled”名称本身已经制造了错误语义。

**整改：**在本方案中保留现有 `SuccessResult`/`FailureResult`/`InterruptResult` 语义和 `GraphCommitResult` owner，或者在另一个已批准的
API 变更中选择一个不暗示 durable confirmation 的名称。必须明确：`GraphTransition.result` 的生命周期止于当前 callback invocation；不新增
receipt、publication store、commit journal 或 confirmation DTO。

### R4（BLOCKER）：持久化、failover 与恢复协调越过用户边界

以下内容已经从“保持现有 callback 行为”扩张为持久化/failover 处置设计：

- 第 1、3 节反复把 result 与 state 写成 durable commit/evidence；
- 第 3.4 节要求调用方“重新读取 authoritative snapshot”“补齐 recovery lineage/value”；
- 第 3.4 节把 `PartialCommitError` 定义成继续协调 partial handoff 的协议；
- 第 7.4 节把“persistent state 先 commit、再 publication、再替换 Python snapshot”列为本期人工验收；
- 第 9 节把 partial handoff、恢复策略和跨边界风险纳入实施目标；
- 第 3.4、4.2、9 节把 `Graph.failure()` 描述成“可持久化、可恢复”。

现有可选 `Graph.Commit` 是 execution 调用方提供的 typed callback；本方案可以把 `reduce candidate -> callback -> exact successor`
顺序列为 **KEEP invariant**，但它不是本方案新增的 Store，也不授权设计 durable protocol。现有 `_PartialCommitError` 是 invocation-local
的既有异常类型，携带 confirmed state/continuation 不能被扩写成 failover contract。

**整改：**删除本期所有 Store、journal、checkpoint、跨进程恢复、retry、worker arbitration、handoff、deployment/rollback 和 durable-claim
要求。只保留既有 callback 顺序、exact successor contract 与当前进程内的 typed return/error propagation；不得新增 State 字段、持久化 owner、
恢复算法或异常分支。`Graph.failure()` 应描述为 node/business control outcome，是否被外部确认由现有 callback 决定，不承诺持久化或 crash recovery。

### R5（MAJOR）：Error surface 表格自相矛盾且没有精确 public 清单

第 3.4 节把 `Graph.ValuePublicationError` 列为 public exact alias；第 4 节也把它放入 facade；但第 6.4 节又写明对
`ValuePublicationError`、`NodeExecutionContractError`、`ResultCollectionError` 等“不额外扩大 facade”。同时，目标继承树没有说明
`PlanningError`、`InvalidExecutionSnapshotError` 等 owner-internal leaf 是否 public，也没有区分现有 alias 与拟新增 alias。

这会导致实现者不清楚应否修改 `errors.py`、`facade.py`、`__all__` 和 typing fixtures，并可能无意中扩张异常公共面。

**整改：**以 `execution/errors.py` 的现有继承树为唯一事实源，单独列出：

1. 已存在且由 `Graph` 命名空间暴露的 aliases；
2. 只可通过 `Graph.Error` 捕获、不得新增 facade alias 的 internal leaves；
3. 本期明确不新增的 exception classes。

不得创建 `ErrorResult`、新的 error DTO、string discriminator 或恢复专用异常。`Graph.PartialCommitError` 若继续存在，只能保持当前
`_PartialCommitError` 的 direct alias/构造 seal 与既有行为，不得新增 handoff 语义。

### R6（MAJOR）：验收矩阵与变更范围不匹配

方案声明“不改变 reducer、commit 顺序、state ownership、frame storage 或 execution path”，却在 S3/S4/S5 要求修改内部 annotations、全部
active tests、README、CHANGELOG 和规范文档；这已经是一次广泛 public rename。相反，方案没有提供：

- production/test/docs exact manifest；
- 当前 normative source 的 owner precedence；
- complexity before/after 与 `make complexity-report`/ratchet 账本；
- 每个新增测试对应的真实 delta 和首个错误/构造边界；
- docs-only 评审时应保持 `src/**`、`tests/**`、State 和 `pyproject.toml` 不变的 gate。

第 8 节还把独立 `pytest`、`pyright` 与 `make check` 重复列为同一门禁，并把 nested/recovery/partial-commit 全量行为列为本次新测试，
但这些并非命名变更的新行为。

**整改：**推荐处置为 docs-only review unit：不修改 production/tests，不添加新的 typing fixture，不冻结 private class/line shape；只核对现有
规范与当前源码的一致性。若未来另行批准 rename，再以 exact manifest 补充定向 positive/negative typing、runtime identity、active docs
迁移、complexity report，以及仓库根目录的适用 pre-commit；不得以“全量 coverage 通过”代替 owner/语义证据。

### R7（MAJOR）：union alias 与 runtime narrowing 的验收语义混淆

方案第 7.1 节同时要求 `Graph.NodeOutcome`、`Graph.SettledNodeResult`、`Graph.RunResult` 不产生第二套 class，并要求相关对象可正常
`isinstance` narrowing。前者说明三者是 parameterized union aliases；后者若指 union alias 本身，则不是稳定的 runtime contract：带有
parameterized generic member 的 typing union 不能被当成普通 nominal class 构造或可靠地传给 `isinstance()`。

正确边界应是：

- 三个 union aliases 只用于 annotation、返回类型和 exhaustiveness；
- runtime narrowing 只使用三个 family 的 concrete final variant aliases；
- direct-constructor negative test 只针对 concrete sealed variants，不把 union alias 冒充 class；
- 不为支持 `isinstance(value, Graph.RunResult)` 新增 wrapper base class、tag、registry 或 metaclass。

**整改：**把 typing 与 runtime 矩阵拆开，明确每条 case 使用 union 还是 concrete variant。若未来 rename 获批，strict positive fixtures 应证明
annotation 和 concrete-variant narrowing，runtime identity tests 只断言 concrete alias identity/seal；不得用一个“任意 TypeError/任意 Pyright error”
同时替代两类证据。

## 4. 推荐的唯一处置

在不改实施正文的前提下，本评审建议 owner 后续按以下顺序处理：

```text
当前 draft
  -> 标记为 CHANGES REQUESTED / KEEP CURRENT PUBLIC SURFACE
  -> 删除本期 rename、durable、failover、handoff 与 recovery strategy 目标
  -> 以现有 outcome/result/run/error owner 作为唯一事实
  -> 若没有独立 API 版本需求，则关闭为 KEEP / NO IMPLEMENTATION
```

这不是为了少改代码而拒绝工作，而是因为当前 canonical owner、sealed construction、commit evidence projection、run disposition 和
`Graph.Error` facade 已经存在；在没有行为需求的情况下重命名会制造大规模迁移、文档分叉和兼容压力，不能称为零负债的最佳整体改动。

若产品最终确认新名字具有独立价值，必须另开版本化 change unit，至少先冻结以下事实：

- 新名字的需求与 public version boundary；
- 每个 owner 的唯一 canonical symbol 和一次性删除清单；
- active docs/tests/examples 的完整迁移 manifest；
- callback candidate 与 external confirmation 的明确时间边界；
- no-persistence/no-failover 停止条件；
- strict Pyright、runtime identity、architecture ownership、complexity 与行为回归证据。

## 5. 当前验收裁决

```text
conceptual separation of outcome/result/run/error       PASS
single canonical owner                                 PASS (existing code)
reuse existing execution infrastructure                PASS (existing code)
proposed public rename                                 BLOCKED
error-surface definition                               BLOCKED (contradictory)
no persistence / no failover boundary                  BLOCKED (scope leakage)
zero-debt implementation manifest                      NOT PROVIDED
production/tests authorization                          NO AUTHORIZATION
overall implementation-plan review                     CHANGES REQUESTED
```

本轮没有修改 production、tests、State、Store 或实施正文；因此不能把任何 `make check`、Pyright、coverage 或 runtime 结果冒充为该方案的
实施证据。方案完成整改并重新冻结前，不应创建 rename 代码、兼容 alias、迁移 wrapper、持久化接口或 failover 测试。

## 6. 本轮验证记录

- 静态核对目标实施文档全文、当前 `facade.py`、`outcome.py`、`result.py`、`family_driver.py`、`errors.py`、`graph/node.py` 和现有
  architecture/public typing tests。
- 交叉核对当前规范事实源的 `Implemented / normative source` 状态以及其中冻结的旧 public names。
- 目标文档 SHA256 为 `1e976385f5812132b1c357444dc7044c8ed28c840c35bbcf4fc3f6be2bfc9fc3`；production baseline 为
  `563a45124311f11e870d0627461102baeffdf7ad`。
- 本 review 是独立 docs-only change unit；没有运行 production implementation，也没有修改被评审文件。
- `make check`、全仓库 `pre-commit` 和全量 pytest 不作为本次 design review 的通过证据；它们应在获得 production authorization、形成
  exact manifest 后运行。若本 review unit 需要交付门禁，只需对文档 change 运行 `git diff --check` 与仓库根目录适用的文档 hooks。

**最终裁决：概念方向通过；当前实施方案不通过，保持现有 public surface，整改后重新评审。**
