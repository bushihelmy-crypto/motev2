# Graph 执行代码语义保持型简化实施方案第九次复审

> **结论：第八次复审的 R9–R13 已实质吸收，State 对齐、“本轮不实现持久化”、24 项范围账本和 15 个 P1 的技术 target 均已闭合。当前只剩 3 个可一次修完的 evidence/gate 缺口，以及 requirements owner 的最终裁决动作；在这些修正回写前仍不批准 `GSP-A05`。**
>
> 本轮不新增第 25 个简化点，不重新打开 architecture 全文治理，也不要求提前实现 T0。下面三项不改变 production target，只把已设计的门禁变成真正可执行且不误伤现有语义的 exact gate。

## 1. 复审信息

- 复审日期：2026-08-20
- 复审对象：[实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)，1035 行
- 对象 SHA256：`231369d826ffb04643206ef3c2a839023265519d4d3309ac680a7edb6054087a`
- 交叉依据：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[第八次复审](graph-semantics-preserving-simplification-implementation-eighth-review.zh-CN.md)、当前 execution/state production 与既有 tests
- 审查原则：零增复杂度、唯一事实源、复用现有 owner、严格 nominal/generic typing、模块级连续 imports、最少字段/参数/变量/扫描/分支、完整但不自锁的门禁
- 范围：静态文档和代码可实现性复核；不修改 production/tests，不运行重测试

## 2. 第八次复审整改核验

### 2.1 R9–R13：已闭合

实施方案已经正确完成以下整改：

1. T0 在 Phase 0 统一为 `DESIGNED / PENDING IMPLEMENTATION`；`GSP-A05` 后才与对应 production 原子落地，不再存在“批准前先编码”的死锁；
2. requirements 被明确为 `GSP-A01`–`GSP-A05` 状态的唯一 owner，实施方案第 11.1 节只提交 evidence，不再维护第二张状态表；
3. S18 固定为每个 owner 一个 typed count index，合计两个；duplicate/collision 在 admission 的一次 enumeration 中收集，并保持 duplicate 优先；
4. S20 固定为 `failed_retry_input: UseStepRequestInput | None = None`，不再接受 `GraphNodeInputBinding` wide union，也不保留编码期临时降 P2 的分支；
5. S23A 接受现有 end-to-end behavior 作为 Phase 0 baseline，private marker 的 direct shape test 随 production 落地；
6. manifest 改为 per-change actual files，owner 回写与 review audit 分离，不再累积全部历史 review；
7. architecture 双语治理和非规范性调用链整改已移出 execution P1 关键路径，且没有重新开放 State 或持久化实现。

这些整改方向正确，原第八次复审阻断不再重复计算。

### 2.2 State 与不实现持久化：继续通过

第 2.4、5.1、5.2、7.3、9、10 节保持一致：

- `src/mote_kernel/state/**`、`tests/state/**`、State command/reducer/validation/protocol 全部 HARD KEEP；
- callback 只保持 exact-candidate confirmation 和既有 memory-install 顺序，不解释为 durability；
- 不新增 Store、repository、journal、checkpoint、database、persistence port/backend 或第二 publication store；
- 24 个单元继续只允许 execution-owned production 变更；State/State tests/protocol 一旦进入 manifest 即直接失败。

因此，本轮仍按用户指定口径裁决：**对齐当前 State，不实现 State 持久化；通过。**

## 3. 剩余缺口

### R14（阻断）：5 个 P1 仍没有 exact T0 `path::test_case`

实施方案第 7.2.2 节声明 15 个 target gate 的 path、子断言和失败条件都已固定，但实际还有以下缺口：

- S03–S06 只写“同一 owner case”，没有给出完整 `tests/...py::test_case`；
- S18 列出了三个 runtime behavior nodeid，但 `S18.a/b/c` 的 typed index、无 `.count()`、无 double enumeration 是 source/AST gate，没有登记承载这些断言的 architecture nodeid；
- 第 7.2.3 节列出 AST predicate 不能替代 requirements `GSP-A03` 要求的可复现 test path。

这不是 target-shape 缺失，而是 target gate 注册不完整。最小修复固定为：

1. S03–S06 的 grouped `S03.a`–`S06.c` 均登记到现有 owner case：

   ```text
   tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering
   ```

   该 case 随四个原子单元逐步更新，每个单元只落地自己的分组断言；不得在 S03 阶段提前断言 S04–S06 的未来 shape。
2. S18 增加并固定一个 architecture target nodeid：

   ```text
   tests/architecture/test_graph_execution_ownership.py::test_resume_duplicate_indexes_are_owner_local_and_linear
   ```

   该 case 承载两个 exact typed count dict、每 owner 一个 index、无 `.count()`、无先 `any` 后重扫以及 duplicate-before-collision；现有三个 runtime nodeid 继续只证明行为和错误 identity。

在这 5 个 path 补齐前，只能说 15 个 target shape 已设计，不能说 15 个 T0 都满足 `GSP-A03` 的 exact path 要求。

### R15（重要）：S20 gate 会误伤必须保留的 final simulated frontier

S20 的目标本身已经唯一，但门禁措辞仍有内部冲突：

- 第 3.4 节明确要求 simulated frontier validation 不得删除或后移；
- 当前 `executor.py` 有两个 `GraphFrontierState(...)`：一个只为 default failed retry materialization 临时替换 State，另一个是最终 `simulated` frontier，随后传给 `validate_graph_frontier(state, simulated)`；
- 第 7.2.2 节的 `S20.b` 却笼统要求 executor 不构造“临时 State/frontier”，可能把最终必须保留的 `simulated` frontier 也判失败；
- 第 3.6 节的 `temporary State/frontier constructions 2 -> 0` 也没有说明只统计 materialization-only 的 `replace + frontier` 这一对对象。

S20 的 exact 计数和 AST gate 应改为：

```text
materialization-only replace(state, frontier=...)     1 -> 0
materialization-only GraphFrontierState(...)          1 -> 0
final simulated = GraphFrontierState(...)              1 -> 1
validate_graph_frontier(state, simulated)              1 -> 1
```

第 7.2.2/7.2.3 节应只禁止 failed-retry materialization 分支中的 replacement State/frontier；必须明确允许且要求最终 simulation/validation 原样保留。这样既删除多余临时投影，也不会为了通过 source gate 删除 admission 安全边界。

### R16（阻断）：S23B 的 interrupt baseline 没有经过被修改的结果投影 owner

S23B 要合并的是 `family_driver.py::_failure_views()` 与 `_interrupt_views()` 两次 scoped-state scan。当前矩阵的 failure 成功路径
`test_failure_resume_actions_are_canonicalized_and_share_run` 会通过 public Result 读取 `failed.failures`，可以作为直接行为证据；但其 interrupt 边界路径：

```text
tests/execution/engine/test_session.py::test_interrupt_completion_is_settled_with_a_state_identity
```

只验证 session settlement 写入 `InterruptedGraphNode`，不会调用 `project_graph_result()` 或 `_interrupt_views()`，因此不能证明 S23B 的 interrupt Result view、payload、ID 和顺序保持。

仓库已有可直接复用的 public case，无需 Phase 0 新增测试：

```text
tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run
```

该 case 会读取 `interrupted.interrupts[0]` 及其 `interrupt_id`，实际经过 family-driver Result projection。应把它加入 B0 命令并替换 S23B 行的旧 session nodeid；nested/mixed-scope ordering 继续由批准后随 production 落地的 S23B T0 冻结。

## 4. 唯一事实源与最终准入动作

requirements 文件本轮没有更新，当前第 7 节仍裁决 `GSP-A01`、`GSP-A02`、`GSP-A04` 已形成、`GSP-A03` 待 case-level evidence，`GSP-A05` 尚不可申请。这与当前状态相符：R14/R16 正是剩余的 A03 evidence 缺口。

R14–R16 回写后，由 requirements owner 一次性完成以下动作：

1. 明确接受 per-change actual manifest、owner writeback/review audit 分离的 A04 解释，避免 requirements 的“一个 manifest”与实施方案第 7.3 节被读成两种规则；
2. 将 `GSP-A03` 更新为已形成；
3. 仅对当前矩阵中的 15 个 P1 显式批准 `GSP-A05`；9 个 P2 继续保持 `GSP-A06` 单项准入；
4. 保留 State/no-persistence HARD KEEP，不产生 Store、State schema 或 protocol 工作项。

上述 exact 修正全部命中后，不需要再创建第十轮复审来证明第九轮复审存在；requirements 的最终裁决就是唯一准入真相。若 owner 不批准，只需在 requirements 中写出仍未满足的具体 Axx 条件。

## 5. 当前闭合度

| 维度 | 当前结论 |
| --- | --- |
| State 对齐 / 不实现持久化 | **通过** |
| 24 项范围账本（15 P1 + 9 P2） | **通过** |
| 15 个 P1 target shape、owner、复杂度与 nominal signature | **通过** |
| 泛型、模块级 import、no-`Any`/reflection/compatibility alias | **通过** |
| A05/T0 时序与 per-change manifest 模型 | **实施方案已闭合；待 requirements owner 接受** |
| T0 exact `path::test_case` | **10/15 完整；S03–S06、S18 待补** |
| baseline behavior evidence | **14/15 完整；S23B interrupt nodeid 待替换** |
| S20 source gate | **target 已闭合；计数/允许项待精确化** |
| `GSP-A05` | **当前不批准；完成 R14–R16 后可由 requirements 直接终局批准** |

## 6. 本轮验证记录

- 静态重读最新实施方案 1035 行、requirements 和第八次复审；核对 R9–R13、15 个 P1 target、B0/B1、T0、source/AST gate、manifest 与最终 evidence 表。
- 静态核对 `tests/architecture/test_graph_execution_ownership.py`：S03–S06 可复用的现有 owner case 确实存在；S18 当前没有登记 source predicate 的 exact architecture nodeid。
- 静态核对 `executor.py`：当前 materialization-only replacement 和 final simulated frontier 各一个，且 `validate_graph_frontier(state, simulated)` 必须保留。
- 静态核对 S23B 两个 baseline：failure public case 经过 Result projection；现列 interrupt session case 不经过 family-driver view projection，而仓库已有 public interrupt Result case 可直接复用。
- 未修改 production/tests；未运行 pytest、Pyright、`make check` 或全量 pre-commit。历史绿色结果不作为本轮新增运行证据。

**第九次复审裁决：技术设计已经收敛，不再新增简化方向；当前仅因 R14–R16 和 requirements 最终状态回写而暂不批准 `GSP-A05`。完成这些精确修正后，可直接由 requirements owner 批准 15 个 P1，随后按原子顺序实施并逐项将 T0 转为 PASS。**
