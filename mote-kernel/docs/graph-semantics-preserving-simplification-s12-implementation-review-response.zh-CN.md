# S12 `GSP-A06` 首轮实施设计评审回复

> **Disposition：首轮评审的 `CHANGES REQUESTED` 总裁决成立。R1、R2、R4 的实质问题全部接受并已完整回写；R3 只接受“拆开复杂度与其他验证口径”，拒绝把 legacy/private-source-shape gate 重新设为 S12 准入条件；同时拒绝“最小回写”措辞。R4建议的narrow typed materialization port经现有active owner gate复核后接受，但必须是multi-consumer唯一lookup/error owner，不是薄wrapper。当前target已形成新的唯一事实源，必须以新SHA进行第二次独立技术评审；本回复不批准`GSP-A06`，也不授权production/tests。**

## 1. 回复信息

- 日期：2026-08-24
- 首轮评审：[S12 `GSP-A06` 单项实施设计评审](graph-semantics-preserving-simplification-s12-implementation-review.zh-CN.md)
- 首轮评审记录 SHA256：`e100fca249d4972fba0cbff346b8a8fd9f980407f4742fc15a0fa45d5fca66cd`
- 首轮评审 target SHA256：`79e51906b2324e557541638c0217f785115d5b089d0516e4709bb917f4ef38a6`
- Owner writeback：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- Owner writeback SHA256：`eae0a63988a205d723dbd9cecb475fc5dc7e8b097c0b795ad949e08e6c4e461c`
- 本文性质：review disposition/audit record；不拥有 S12 exact target、requirements 批准状态、production shape 或测试 shape
- 本次变更边界：只新增本文；不修改首轮评审历史，不修改 production、tests、requirements、normative source、State 或持久化

## 2. 逐项 disposition

| Review item | Disposition | Owner 处理 |
| --- | --- | --- |
| R1：缺少 `GSP-P01`–`GSP-P08` 映射 | **ACCEPTED** | 实施方案新增完整 applicability/evidence matrix，并把case-level evidence、结构账本和manifest共同绑定到`GSP-A06`。`GSP-P02`不用“排除”弱化，而写为“不触及 / HARD KEEP”：不实现持久化，同时任何State/Store/protocol/persistence diff都直接停止。 |
| R2：unknown scope/materialization evidence不完整 | **ACCEPTED** | 实施方案固定unknown scope、unknown materialization、missing/wrong coordinate三类exact错误、完整precedence和可到达的forged-seed子场景；existing continuation unknown-child case只原样复跑，不伪列入changed-file manifest。 |
| R3：复杂度与legacy状态混写 | **PARTIALLY ACCEPTED / PARTIALLY REJECTED** | 接受将automated complexity与其他验证拆开；拒绝“只有complexity可排除”及“existing legacy checks仍必须通过”。S12仍要求current behavior、typing及active owner/dependency checks，但legacy/private-source-shape gate不得新增、扩写或成为准入条件。 |
| R4：scan/lookup账本不完整 | **ACCEPTED** | 实施方案如实登记旧`PreparedResume.inputs` scan删除、新full-availability membership新增、所有direct access收口、executor skip lookup删除、recovery每个non-skip action经shared port一次resolution，以及现有nested lookup成本。 |

## 3. 接受 R4 后的 exact owner 选择

首轮评审正确指出旧 target 的 `_resume_input_coordinate(graph, activation)` 会让
`materialize_node_input()` 在仍需使用 `MaterializationPlan.bindings` 时重复查找线性 `FrozenMap`。该target已删除。

现有active architecture case
`test_compiled_routing_is_interpreted_only_by_routing_and_snapshot_guard`明确禁止recovery直接读取`materializations`。这不是冻结旧local的
legacy gate，而是当前single-owner边界。因此owner writeback接受评审建议的narrow typed port，并固定：

- `engine/resume_input.py::_require_node_materialization(graph, node_id)`是唯一node-materialization lookup/error owner；
- 它被resume-input runtime、executor和recovery共六个production consumer复用，不是single-use thin helper；
- 保持既有pure constructor `_resume_input_coordinate(activation, plan)`；
- port唯一读取authoritative compiled map，并统一把unknown node映射为fixed typed error；
- recovery只调用port和constructor，既不读取map，也不复制materialization interpreter；
- 不新增第二lookup port、overload、DTO、context、cache、index、alias或第二coordinate constructor。

上述exact algorithm、签名、错误文本和账本只由实施方案第4、7、8节拥有，本文不复制其完整target。被拒绝的是无owner、
single-use的薄wrapper，不是这条由多consumer、typed error mapping和active architecture边界共同证明必要的port。

## 4. 对 R3 的拒绝边界

用户已经明确要求本单元忽略 automated complexity gate，并且不要 legacy 门禁测试。两者都不能被review重新解释：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
LEGACY / PRIVATE-SOURCE-SHAPE AST GATES: OUT OF SCOPE
```

这不授权跳过当前真实契约。S12仍必须通过与其改动直接相关的behavior、strict typing、generic、dependency、single-owner、lint、
format、build/package和pre-commit检查。现有architecture case即使内部使用AST，只要验证的是当前generic/dependency/owner约束，
就不是legacy gate；反之，任何只冻结已删除private symbol、local、表达式或source layout的检查都不属于S12准入证据。

首轮评审第4.3节中“历史上已存在的non-complexity/legacy checks仍必须通过”以及第6节要求把legacy状态改为existing gates required的
部分，因此明确拒绝。实施方案已经改成无歧义的三分口径：complexity排除、legacy/private-shape排除、current contract checks必需。

## 5. 拒绝“最小回写”口径

首轮评审第6节的“完成以下最小回写”不作为owner目标。此次不是以最少文字或最少改动勉强过审，而是完整闭合：

1. requirement映射；
2. malformed exact behavior与precedence；
3. scan、lookup、function和新增面净账本；
4. 唯一materialization port、唯一constructor与single-lookup调用形状；
5. Graph/Kernel failover边界；
6. atomic manifests、第二次评审与批准顺序；
7. complexity、legacy和current-contract三类验证边界。

任何一项缺失都不能用“核心方向已经可行”替代。第二次独立评审必须审核完整owner writeback，而不是只确认四条文字是否出现。

## 6. Graph 与 Kernel failover 边界澄清

S12不建立failover。Graph只接收并执行显式resume/interrupt/skip action，拒绝malformed snapshot/action；它不决定retry、backoff、
最大次数或错误分类。Kernel在Graph之外通过narrow typed Port装配failover策略。Compiled topology query与
`SnapshotMismatchError` admission不构成Graph-owned failover，也不得演化为第二runner、Port实现、registry、Store或持久化占位。

该澄清已写入实施方案作为HARD KEEP和停止条件，但不是新增S12能力。未来failover Port或持久化必须另立需求与owner。

## 7. 当前状态与下一步

```text
FIRST REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
OWNER DISPOSITION: R1 ACCEPTED / R2 ACCEPTED / R3 PARTIAL / R4 ACCEPTED
OWNER WRITEBACK: COMPLETE AT SHA256 eae0a63988a205d723dbd9cecb475fc5dc7e8b097c0b795ad949e08e6c4e461c
SECOND INDEPENDENT TECHNICAL REVIEW: REQUIRED
GSP-A06: NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH-OWNED FAILOVER POLICY: FORBIDDEN
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SHAPE GATES: OUT OF SCOPE
```

第二次评审必须新增
`docs/graph-semantics-preserving-simplification-s12-implementation-second-review.zh-CN.md`并绑定上述owner-writeback SHA；不得覆盖
首轮评审或本回复。二审通过后仍需用户显式批准，requirements owner才能把S12回写为`GSP-A06 SATISFIED`。

## 8. 本次 response change unit

本文是本次response audit唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-review-response.zh-CN.md
```

S12 exact target仍只由实施方案拥有；requirements仍唯一拥有批准状态。
