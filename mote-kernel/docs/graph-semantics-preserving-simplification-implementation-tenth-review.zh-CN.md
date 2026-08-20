# Graph 执行代码语义保持型简化实施方案第十次复审

> **结论：第九次复审 R14–R16 已全部实质闭合，实施方案的 15 个 P1 target、baseline evidence、T0 exact nodeid、复杂度账本以及 State/no-persistence 边界已经技术闭合。本轮没有发现新的简化单元或 production target。整体 Phase 0 仍未终局闭合：requirements 仍保持旧裁决，且其 `GSP-A04`“一个 manifest”口径尚未接受实施方案的 per-change manifest 模型；此外实施方案第 7.4 节有一处重复路径。完成这两个文档动作前，不得进入 Phase 1。**

## 1. 复审信息

- 复审日期：2026-08-20
- 复审对象：[实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)，1077 行
- 对象 SHA256：`ef89d251b3531bb4bb83014ea1ec07665f8ba014f9425ddfe06cbdd25a868604`
- 交叉依据：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[第九次复审](graph-semantics-preserving-simplification-implementation-ninth-review.zh-CN.md)、当前 execution production 与对应 tests
- requirements SHA256：`d9bdabf2f39cc6a4cd3ccb45db4047be43c28616d2e67face359bb1bd53a3200`
- 审查原则：零增复杂度、唯一事实源、复用现有 owner、严格 nominal/generic typing、模块级连续 imports、最少字段/参数/变量/扫描/分支、完整但不自锁的门禁
- 范围：只复核 R14–R16 回写、requirements 最终准入状态、State/no-persistence 与文档内部闭合；不重新发现第 25 个简化点，不修改 production/tests，不运行重测试

## 2. 第九次复审整改核验

### 2.1 R14：已闭合

第 7.2.2、7.2.3 节已经为原先缺失的 5 个 P1 登记可复现 T0 `path::test_case`：

- S03–S06 统一登记到
  `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering`；
- 每个原子单元只增加自己的 `S03.a`–`S06.c` 分组断言，不提前冻结后续单元的未来 shape；
- S18 固定待新增
  `tests/architecture/test_graph_execution_ownership.py::test_resume_duplicate_indexes_are_owner_local_and_linear`；
- S18 的 architecture gate 与现有 runtime behavior cases 已明确分工，前者检查两个 typed owner-local index、无 `.count()`、无双扫描，后者保持错误 identity、coordinate isolation 和 duplicate-before-collision 行为。

当前 15 个 P1 均已有 exact T0 nodeid、断言目标和失败条件；状态统一且如实保持为
`DESIGNED / PENDING IMPLEMENTATION`。

### 2.2 R15：已闭合

S20 在目标表、复杂度账本、T0 gate 和 Source/AST predicate 中已经统一为以下唯一计数：

```text
materialization-only replace(state, frontier=...)  1 -> 0
materialization-only GraphFrontierState(...)       1 -> 0
final simulated = GraphFrontierState(...)           1 -> 1
validate_graph_frontier(state, simulated)           1 -> 1
```

删除面只包含 failed-retry materialization 分支的 replacement projection；最终 simulated frontier 和
`validate_graph_frontier(state, simulated)` 明确要求各保留一次并维持原顺序。当前 production 静态形状也与该
before 计数一致，因此 target gate 不再误伤必须保留的 admission 安全边界。

唯一 materializer 继续是既有 `engine.resume_input.materialize_node_input`，新增面仅为：

```python
*,
failed_retry_input: UseStepRequestInput | None = None,
```

没有 wide union、第二 wrapper、State 写回或持久化 owner。

### 2.3 R16：已闭合

S23B interrupt baseline 已替换为：

```text
tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run
```

该 public case 读取 `AwaitingResumeResult.interrupts[0]` 和 `interrupt_id`，实际经过
`family_driver.project_graph_result()` 的 Result view projection，并继续验证 stale interrupt ID fail closed。
原先只验证 session settlement 的
`tests/execution/engine/test_session.py::test_interrupt_completion_is_settled_with_a_state_identity`
不再被列为 S23B Result projection evidence。

因此 S23B 的 failure 与 interrupt 两侧都已有经过被修改 owner 的 public baseline；root→child mixed-scope
ordering 继续由随 production 原子落地的 T0 冻结，口径正确。

## 3. 其余闭合项

| 维度 | 本轮结论 |
| --- | --- |
| State 对齐 | **通过**；`GraphRunState`、command、reducer、validation、protocol、State tests 全部 HARD KEEP |
| 当前不实现持久化 | **通过**；不新增 Store、repository、journal、checkpoint、database、persistence port/backend |
| 范围账本 | **通过**；仍为 23 个历史 ID、24 个原子单元，即 15 P1 + 9 P2 |
| 多余实现 | **未发现新增**；没有 compatibility alias、第二 runner、第二 store、临时双写或账本外 helper |
| 泛型与导包 | **设计通过**；target 使用现有 `GraphValueT` 和窄 nominal type，并要求依赖保持 module-header import |
| 变量、扫描与逻辑 | **设计通过**；S18 每 owner 一个 index，S20 只增加一个 optional nominal input，S23B 只合并既有两次扫描 |
| P1 baseline evidence | **15/15 闭合** |
| P1 T0 exact nodeid/断言/失败条件 | **15/15 已设计，0/15 已实施**；该状态符合 Phase 0 时序 |

本轮没有理由新增 S24、把 P2 偷渡为 P1，或修改 `src/mote_kernel/state/**`、`tests/state/**`。

## 4. 剩余两个文档动作

### C1（终局阻断）：requirements owner 尚未回写最终准入裁决

requirements 文件未随本轮更新，当前仍明确写着：

- `GSP-A03` 尚未闭合；
- `GSP-A05` 尚不可申请；
- `GSP-A04` 要求 Phase 0 相关文件“使用一个 exact repo-relative actual changed-file manifest”。

实施方案则已经提交 15/15 case-level evidence，并采用“每个 actual change unit 独立 manifest；owner 回写与
review audit 分离”的模型。由于 requirements 是 `GSP-Axx` 状态和准入规则的唯一 owner，实施方案第 11 节不能
自行消除这组差异，也不能据此开始编码。

最小终局回写是：

1. 在 requirements 的 `GSP-A04` 中明确接受 per-change actual manifest，以及 owner/review 两个 change unit
   分别验证的规则；不要同时保留“一个累计 manifest”的第二解释；
2. 在 requirements 第 7 节把 `GSP-A03` 更新为已形成；
3. 由 requirements owner 明确决定是否只批准矩阵中的 15 个 P1 的 `GSP-A05`；若不批准，必须写出仍未满足的
   exact Axx 条件；
4. 9 个 P2 继续受 `GSP-A06` 单项准入，不继承 P1 批准；
5. 保留当前 State/no-persistence HARD KEEP，不生成 State schema、Store 或 protocol 工作项。

在该 owner 回写完成前，正确裁决仍是：**实施 evidence 已闭合，但 Phase 0/A05 未闭合，禁止 Phase 1。**

### C2（重要但局部）：第 7.4 节的 exact manifest 有重复路径

实施方案第 7.4 节称“实际新增或修改的五个 repo-relative paths”，但代码块把
`mote-kernel/README.zh-CN.md` 连续列了两次，形成 6 行、5 个唯一路径。后文“两个 tracked README + 三个
untracked Markdown”也证明这里应当只有 5 行。

最小修复仅为删除重复的第二行 `mote-kernel/README.zh-CN.md`。不得借此扩大历史 manifest、重新加入未修改的
review/response，或改成累计清单。

## 5. 最终闭合度

| 项目 | 裁决 |
| --- | --- |
| 第九次复审 R14–R16 | **全部闭合** |
| 15 个 P1 技术 target 与 evidence | **闭合** |
| State 对齐 / 不实现持久化 | **闭合** |
| 是否存在第 25 个简化点 | **否** |
| 实施方案自身可修正文案 | **尚余 C2 一处重复路径** |
| requirements 与 per-change manifest 口径 | **尚未闭合** |
| requirements 的 `GSP-A03` 状态 | **仍是旧裁决** |
| `GSP-A05` | **尚未获批** |
| 是否允许修改 production/tests | **否** |

## 6. 本轮验证记录

- 完整重读最新实施方案的文档信息、R14–R16 ledger、S18、S20、S23B、复杂度账本、7.2.1–7.2.3、7.3–7.7 和第 11 节；
- 交叉读取 requirements 第 6、7 节，确认其 SHA256 和裁决文本均未更新；
- 静态核对现有 compiled-lowering architecture case、S20 两类 frontier construction、family-driver 两次 Result view scan，以及 public interrupt Result case；
- 未修改 production/tests；未运行 pytest、Pyright、`make check` 或全量 pre-commit；历史绿色结果不冒充本轮新增运行证据。

**第十次复审裁决：技术方案已经收敛，不再继续发现新实现项。只修 C2，并由 requirements owner 完成 C1 后即可作一次终局准入裁决；不需要再通过新增评审轮次证明前一轮评审已经存在。**
