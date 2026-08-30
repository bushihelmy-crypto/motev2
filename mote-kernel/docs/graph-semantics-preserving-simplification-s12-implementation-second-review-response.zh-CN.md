# S12 第二次实施设计评审回复

> **Disposition：第二次评审的 `CHANGES REQUESTED` 总裁决成立，但四项理由不全部成立。R5、R6 接受并已完整写回；R7 接受“必须裁决支持域”，拒绝为不消费 node input 的 skip 恢复无意义 materialization lookup 或建立 forged-private-topology gate；R8 因直接违反用户已经明确给出的“不要 legacy 门禁测试”授权而拒绝。“最小 owner 回写”口径同样不采用，本次按零已知负债完整闭合 owner、fixture、precedence、支持域和验证边界。本文不批准 `GSP-A06`，不授权 production/tests。**

## 1. 回复信息

- 日期：2026-08-24
- 第二次评审：[S12 第二次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-second-review.zh-CN.md)
- 第二次评审 SHA256：`5213b5f6854561a3125a5dbc45c6e1a426f9b784131696abe7a237b1e503c78c`
- 第二次评审所绑定旧 target SHA256：`eae0a63988a205d723dbd9cecb475fc5dc7e8b097c0b795ad949e08e6c4e461c`
- 当前 owner：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 当前 owner writeback SHA256：`8185956b0ac7537d3d0c39ab186d15e54d9f37ffb068364b6895450f49fe7804`
- 本文性质：第二次 review disposition/audit record；不拥有 S12 exact target、requirements 批准状态、production shape 或测试 shape
- 本次边界：owner writeback只修改实施方案；本response是独立新增文件；不修改review历史、requirements、normative source、production、tests、State、Store或持久化

## 2. 逐项 disposition

| Review item | Disposition | Owner 处理 |
| --- | --- | --- |
| R5：materialization owner/consumer 账本不闭合 | **ACCEPTED** | 全仓七个direct reads已完整登记。`_require_node_materialization()`收窄为resume-input runtime/executor/recovery consumer集合的唯一lookup/error query；invocation continuation validator与routing binding/readiness保留各自既有direct read、错误契约和owner。不迁移成全局accessor，不复制compiled truth。 |
| R6：forged fixture与frame owner混淆 | **ACCEPTED** | unknown-materialization fixture固定为compiled/state均已知的Pending non-skip node，仅forge compiled materialization map缺项。直接preflight precedence只使用其已有的duplicate-publication-coordinate check；record nominal type、scope/descriptor和concrete frame继续由public `validate_context()`拥有并复跑现有cases，recovery不新增第二validator。 |
| R7：skip malformed lookup precedence未裁决 | **PARTIALLY ACCEPTED / PARTIALLY REJECTED** | 接受必须写清支持域。Compiler-produced graph中的skip不消费node input，因此target继续0次materialization lookup；“skip + 私下forge compiled map缺项”明确不属于S12支持域，baseline偶然`KeyError`不构成需保持语义。拒绝恢复无意义lookup、增加全图validator或新增forged-skip私有形状门禁；valid skip与历史resume coordinate仍有行为证据。 |
| R8：要求恢复legacy/private-source gates | **REJECTED** | 用户已经明确要求“不要做legacy门禁测试”。实施方案继续排除automated complexity与legacy/private-shape gates，同时保留current behavior、strict typing、active generic/dependency/owner、lint、format、build/package和跳过complexity hook后的pre-commit验证。 |

## 3. R5：唯一事实与不重叠 owner

唯一事实始终是 immutable `CompiledGraph.transition.materializations`，不是任何lookup函数。旧target的问题是把一个有固定错误映射的
query写成了全仓“唯一 lookup/error owner”，但源码中还有两种不同责任：

1. `invocation.py::_validate_frame_index()`把materialization存在性、descriptor、superstep和frame admission组合为continuation
   integrity，并统一维持`"continuation resume input has inconsistent coordinates"`错误；
2. `engine/routing.py::resolve_routing_facts()`读取bindings以解释routing readiness，不构造resume coordinate，也不拥有resume-input
   unknown-node admission错误。

当前target只收口六个同语义consumer：`_admit_override()`、`node_inputs_available()`、
`pending_node_input_available()`、`materialize_node_input()`、`GraphExecutor.resume()`和`preflight_recovery()`。这既消除该集合内重复
error mapping，又避免把不同语义强行压进一个global accessor。文档同时把“typed port”改成“typed query”，明确它不是Kernel
capability/failover Port。

## 4. R6：可构造 fixture 与 precedence

第二次评审指出的核心事实成立：`RecoveryAvailabilityCoordinates.from_frames()`不负责record nominal type、scope、descriptor或
concrete frame validation，不能把`object()`一类malformed record塞进直接`preflight_recovery()` case后声称由既有typed owner拒绝。

当前target据此固定：

- unknown materialization：node必须同时存在于compiled `nodes`与Pending frontier，只删除forged graph的materialization entry；
- frame projection precedence：只使用`from_frames()`真实拥有的duplicate publication coordinate检查，并断言其exact
  `SnapshotMismatchError`早于unknown scope/materialization；
- record/frame malformed：继续通过public facade先执行的`validate_context()`及既有continuation cases证明，不进入recovery target
  case，不复制frame interpreter。

这同时消除了假阳性和owner重复。

## 5. R7：skip 的正确域裁决

对compiler-produced valid graph，materialization完整；skip不产生resume-input record，也不需要node-input descriptor或bindings。将lookup
移入non-skip branch是删除无意义工作，不是行为缺口。

第二次评审要求“必须裁决 forged compiled topology 是否属于支持域”这一点已接受并写回；但若进一步要求skip也对缺失
materialization抛固定typed error，就只能增加无意义lookup或新增全图validator，反而扩大S12逻辑和非法输入契约。若新增
forged-skip case来冻结“是否lookup”的private shape，也与用户禁止legacy/private-shape gate的边界冲突。因此当前裁决是：

```text
non-skip + known node + forged missing materialization: S12 targeted malformed evidence，typed fail closed
skip + forged missing materialization: outside S12 supported malformed domain，不保留偶然 KeyError
valid skip + historical resume coordinate: required behavior evidence，不能误拒绝或删除历史coordinate
```

## 6. R8 与验证边界

第二次评审称legacy/private-source gate“没有用户授权排除”，与已记录的明确指令相反，因此不能成为blocker。当前三分边界保持：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
LEGACY / PRIVATE-SOURCE-SHAPE AST GATES: OUT OF SCOPE
CURRENT BEHAVIOR / STRICT TYPING / ACTIVE OWNER-DEPENDENCY / QUALITY CHECKS: REQUIRED
```

一次性`rg`只用于implementation writeback核对actual diff，不新增pytest/pre-commit gate，不冻结已删除private symbol、local或source
layout。现有architecture case若验证的是仍有效的generic、dependency、single-owner和no-type-erasure契约，仍按实施方案运行；这不等于
legacy gate。

## 7. 拒绝“最小回写”口径

本次不是按最少文字修补。owner writeback同步完成：

1. 七个materialization direct reads与三类不重叠owner的完整账本；
2. shared query的精确责任、六个consumer、固定错误和非Port边界；
3. unknown-materialization可构造fixture；
4. preflight frame projection与public continuation frame validation的owner分界；
5. skip forged topology支持域及不建立额外lookup/validator/gate的裁决；
6. behavior cases、source inventory、normative同步、实施顺序、停止条件和atomic manifest；
7. complexity、legacy与current-contract checks三分边界；
8. 第三次独立评审、requirements批准和implementation的审计顺序。

## 8. 当前状态

```text
S12 SECOND INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 SECOND REVIEW DISPOSITION: R5 ACCEPTED / R6 ACCEPTED / R7 PARTIAL / R8 REJECTED
S12 SECOND-REVIEW OWNER WRITEBACK: COMPLETE AT SHA256 8185956b0ac7537d3d0c39ab186d15e54d9f37ffb068364b6895450f49fe7804
S12 THIRD INDEPENDENT TECHNICAL REVIEW: REQUIRED
S12 GSP-A06: NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH-OWNED FAILOVER POLICY: FORBIDDEN; KERNEL TYPED PORT BOUNDARY HARD KEEP
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SHAPE GATES: OUT OF SCOPE
CURRENT CONTRACT CHECKS: REQUIRED
```

第三次评审必须新增
`docs/graph-semantics-preserving-simplification-s12-implementation-third-review.zh-CN.md`并绑定上述owner SHA；不得覆盖任何既有
review/response。第三次评审通过后仍需用户显式批准，requirements owner才能回写`GSP-A06 SATISFIED`。

## 9. 本次 response change unit

本文是第二次review response的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-second-review-response.zh-CN.md
```

S12 exact target仍只由实施方案拥有；requirements仍唯一拥有批准状态。
