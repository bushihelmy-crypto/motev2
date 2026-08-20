# Graph 执行代码语义保持型简化实施方案第五次复审回复

## 1. 回复信息

- 回复对象：[第五次复审结论](graph-semantics-preserving-simplification-implementation-fifth-review.zh-CN.md)
- 修订正文：[语义保持型简化实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 需求依据：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 回复日期：2026-08-19
- 状态：接受“不批准 Phase 1”裁决；R1、R2 已回写 requirements 与实施阶段定义；case-level matrix 尚待补齐
- 本轮范围：只补充评审记录，不修改 production code 或 tests

## 2. 总体裁决

接受第五次复审的判断：当前方案尚未满足 `GSP-A03`，因此不能申请 `GSP-A05`，不得进入
Phase 1 或修改 production/tests。

第五次复审指出的不是 production target 回退，而是实施方案自身的证据和时序没有闭合：

1. 第 7.2.1 节只有文件级 characterization owner，没有 exact success/boundary/shape case；
2. Phase 0 第 4 项要求立即完成全部 P2 详细设计，但 Phase 3/4 又把同一工作推迟到单项复审。

当前分阶段状态应解释为：

- `GSP-A01` owner 闭合：已满足；
- `GSP-A02` 原子边界闭合：已满足；
- `GSP-A03` 行为证据闭合：未满足；
- `GSP-A04` 文档与门禁闭合：已满足；
- `GSP-A05` 显式批准：因 `GSP-A03` 未满足而不能申请。

## 3. 接受 R1：补齐 P1 exact characterization 证据

### 3.1 接受理由

requirements 的 `GSP-A03` 要求每个当前拟实施单元同时具备：

1. 全部适用的 `GSP-P01`–`GSP-P08` 映射；
2. 确定性的成功路径和失败或边界路径；
3. shape 删除对应的 exact-shape/tamper 证据。

现有表格只列测试文件，且明确把具体 case 推迟到单元实施前，不能作为 Phase 0 的可复现证据。
后续修订必须把每个 P1 行扩展为：

```text
单元 -> 全部适用 GSP-Pxx
      -> exact test path::test_case（成功）
      -> exact test path::test_case（失败/边界）
      -> exact-shape/tamper path::test_case（适用时）
```

可以引用现有测试，不为填表新增重复测试；如果现有 case 不足，必须明确标为“待补”，并保持
该单元未批准。baseline characterization 与 target exact-shape gate 要分开标注：前者可以在 Phase 0
引用当前 production 测试，后者在对应 production 原子变更中同步更新，但 target case 的路径、断言目标
和失败条件必须在 Phase 0 先固定。

### 3.2 已确认的 requirement 映射漏项

复审列出的三项均接受：

- S06 迁移 `resource_order`，补 `GSP-P07`；
- S09 重构 `ResolutionCommand` projection，补 `GSP-P02`；
- S23B 合并 failure/interrupt Result view projection，补 `GSP-P04`。

此外，修订表格时必须做一次全量 conservative audit，至少复核以下可能漏项，不能只机械补上面三项：

- S05 的 compiled graph-input owner 迁移是否同时适用 `GSP-P08`；
- S04 的 admission/publication 与 nested boundary consumer 是否适用 `GSP-P03`、`GSP-P07`；
- S10/S11 的 graph-output、resume-frame availability 是否适用 `GSP-P03`、`GSP-P04`；
- S18 的 publication/action collision identity 是否适用 `GSP-P04`。

最终以单元实际触碰的 owner、可观察边界和第 2 节 normative source 为准；不得为了减少表格而遗漏
适用 ID，也不得把与该单元无关的 ID 机械全部添加。

### 3.3 可直接引用的现有 case 示例

以下仅是已核对的引用样例，后续完整矩阵必须覆盖全部 15 个 P1：

| 单元 | 成功路径 | 失败/边界路径 |
| --- | --- | --- |
| S06 | `tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order` | `tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order` |
| S09 | `tests/execution/engine/test_routing.py::test_direct_conditional_and_terminal_routing_use_one_contribution_model` | `tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance` |
| S23B | `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run` | `tests/execution/engine/test_session.py::test_interrupt_completion_is_settled_with_a_state_identity` |

S04/S05/S06 等 shape 删除单元还必须在对应原子变更中更新 architecture exact-shape/tamper case；
当前代码仍是旧 shape，不能把当前旧断言冒充未来 target gate。

## 4. 接受 R2：P2 详细设计移出 Phase 0

### 4.1 Phase 0 只保留的内容

Phase 0 只确认以下 P2 账本事实：

- P2 单元列表、唯一 owner 和当前候选方向；
- P2 未获批准，不能继承 P1 批准；
- P2 不得与 P1 混合提交；
- S12 继续保留 equality、malformed seed、泛型迁移和 normative synchronization 前置条件。

不要求 Phase 0 预先完成九个 P2 的函数签名、nominal type、增删计数或复杂度证明。

### 4.2 P2 单项准入时再提交

将实施方案 Phase 0 第 4 项移动到 Phase 3/4 的单项准入条件。每个 P2 在申请实施前必须独立提交：

1. 目标函数签名和输入/输出 nominal type；
2. 删除对象、最多新增对象和净复杂度变化；
3. 成功、失败/边界 characterization 及 exact-shape/tamper 证据；
4. 对应的 changed-file manifest 和完整门禁记录；
5. S12 额外提交 valid-domain equality、action ↔ availability、malformed seed 和 generic migration 证明。

这样既不为当前 P1 准入提前设计尚未批准的方向，也不降低 P2 的单项证明门槛。

## 5. 已回写与仍待完成

以下 owner/时序修订已经完成，不打开已闭合的 production target：

1. requirements 已明确 `GSP-A02` 的 P2 延后规则、`GSP-A03` 的 case-level 证据口径、`GSP-A05` 的当前阶段范围，并新增 `GSP-A06` 作为 P2 单项设计准入；
2. 实施方案已把 P2 详细设计移出 Phase 0，Phase 3/4 改为引用 `GSP-A06`；
3. requirements 与实施方案当前状态已改为 `A03 未满足、A05 阻断`；
4. 实施方案“关联记录”已加入第五次复审及本回复。

仍待完成的唯一 Phase 0 证据工作是：将 7.2.1 扩展为 15 个 P1 的 exact case matrix，补齐全量
requirement ID audit，并把本轮实际文件按 manifest 运行文档门禁。

本回复不改变 24 个原子单元、15 个 P1、9 个 P2 的计数，不授权 production/tests 修改。

## 6. 最终结论

第五次复审 R1、R2 均有道理，应全部吸收。补齐 exact characterization 证据并消除 P2 时序矛盾后，
再做一次聚焦准入复核；在此之前保持 Phase 0，`GSP-A05` 不通过。
