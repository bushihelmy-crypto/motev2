# S12 `GSP-A06` 单项实施设计第三次独立技术评审

> **结论：`CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL`。当前 owner writeback 已闭合 R5 的窄 materialization owner、R6 的 frame-owner 分界和 R7 的 valid-skip 支持域裁决；State/Store/protocol/persistence 与用户已审批的 failover 范围均通过。但门禁授权仍把“不得新增/扩写 legacy 门禁”与“跳过既有非复杂度门禁”混为一谈，且 unknown nested scope forged-seed 的 exact construction recipe 不足以复现所宣称的错误 precedence。两项均是设计/evidence 阻断，不授权 production、tests、requirements 或持久化变更。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 本轮绑定 owner SHA256：`8185956b0ac7537d3d0c39ab186d15e54d9f37ffb068364b6895450f49fe7804`
- 第二次评审：[S12 第二次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-second-review.zh-CN.md)，SHA256：`5213b5f6854561a3125a5dbc45c6e1a426f9b784131696abe7a237b1e503c78c`
- 第二次评审回复：[S12 第二次实施设计评审回复](graph-semantics-preserving-simplification-s12-implementation-second-review-response.zh-CN.md)，SHA256：`f879ae9206191d6645cd15f32a366cec7bbb0324ce7c3aff624b1f1fb4a855c0`
- 声明源码基线：Git `35e7c95206e4124be68a9706359d1cc129e98c17`
- 本文性质：第三次独立 review record；只拥有本轮裁决、问题和验证记录，不拥有 target shape、requirements 批准状态、production shape 或测试 shape
- 本轮 change unit：只新增本文；不修改 owner、response、requirements、normative source、production、tests、State、Store、protocol 或 persistence

## 2. 审核边界与总裁决

本轮遵循可核验的用户约束：不做持久化；唯一事实、复用现有基础设施、零已知负债和不扩大 S12 范围；复杂度门禁可暂时忽略；Graph/Kernel failover 范围已由用户审批。本轮不把 failover 范围本身列为阻断，也不要求 S12 建立 retry/backoff、第二 runner、registry、Store 或持久化。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、Store、protocol、持久化 | **通过** | owner 明确 `GraphRunState`、command/reducer、revision、commit、protocol、Store 与 persistence 为 HARD KEEP；planned manifest 未越界 |
| Graph/Kernel failover | **通过，非阻断** | 只保留显式 action admission；没有 Graph-owned retry/backoff/error policy、第二执行路径或 failover 实现 |
| 唯一 resume fact / phantom generic | **通过设计** | `resume_inputs` 承担 presence，`AdmittedResumeFact` 五字段和 `_RecoveryFamily` 去泛型方向闭合；真实泛型链条保留 |
| materialization / scope owner | **通过设计** | 七个 baseline direct reads 已登记；窄 query、唯一 coordinate constructor、continuation owner 与 routing owner 不重叠 |
| R7 valid skip 支持域 | **通过（带边界）** | compiler-produced graph 的 skip 不消费 node input；forged compiled map 缺项被明确排除，不把偶然 `KeyError` 当 public contract |
| current baseline evidence | **通过** | scoped execution、非复杂度 architecture、Pyright 和 source inventory 均可复核；未冒充 target 已实施 |
| 门禁授权口径 | **不通过 / R8** | response 没有可核验的用户授权可跳过既有非复杂度 legacy/private-source gate |
| forged nested-scope fixture | **不通过 / R9** | 只列出错误表，没有给出满足前置 binding/frontier/settlement 条件的确定构造；容易先命中其他 owner |
| `GSP-A06` | **未闭合、未批准** | R8、R9 回写并再次独立复核前，不得进入 requirements approval 或 implementation |

## 3. 已闭合事项

### 3.1 R5：窄 materialization owner

owner 已把 `_require_node_materialization()` 收窄为 resume-input runtime/executor/recovery consumer 集合的唯一 lookup/error query，并登记七个 baseline direct reads。`invocation.py::_validate_frame_index()` 继续拥有 continuation-specific existence/descriptor/frame 错误；`engine/routing.py::resolve_routing_facts()` 继续拥有 bindings/readiness 解释。它们读取同一个 immutable compiled map，但不被伪装成全仓 accessor。这满足“唯一事实”而不扩大 S12 范围。

### 3.2 R6：frame validation owner

owner 已明确 `RecoveryAvailabilityCoordinates.from_frames()` 只拥有 publication-coordinate uniqueness projection；record nominal type、scope、descriptor 和 concrete frame 继续由 public `validate_context()`/`_validate_frame_index()` 拒绝，recovery 不复制第二套 frame interpreter。unknown-materialization 的支持 fixture 也已收窄为“compiled/state 均已知的 Pending non-skip node，只删除 materialization map entry”。这部分设计通过，但其 exact forged-seed recipe 仍受 R9 约束。

### 3.3 R7：skip 边界

对 compiler-produced valid graph，skip 不产生 resume-input record，跳过无意义的 materialization lookup 不改变 public behavior。owner 已明确：`skip + 私下 forge compiled map 缺项` 不属于 S12 支持的 malformed topology domain；不新增全图 validator、无意义 lookup 或 private-shape gate。该裁决与“不扩大实施范围”一致，前提是后续文档和测试只把 valid skip 及 historical coordinate 行为作为契约，不宣称 forged skip 的偶然异常为稳定错误。

## 4. 阻断问题

### R8 — complexity 豁免不等于跳过既有非复杂度门禁

当前 owner 第 12、14 节把下列两类同时写成 out of scope：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
LEGACY / PRIVATE-SOURCE-SHAPE AST GATES: OUT OF SCOPE; MUST NOT BE ADDED, EXPANDED OR REQUIRED
```

第二次 response 又在其第 2、6 节声称“用户已经明确要求不要做 legacy 门禁测试”。本轮可核验的用户授权明确包含“复杂度门禁可以暂时忽略”、不做持久化以及 failover 范围批准；没有一条可审计指令授权跳过既有非复杂度 gate。response 自身的 disposition 不能替代用户授权，也不能改写 requirements 的准入 owner。

这里必须区分两个不同约束：

- 可以排除 automated complexity gate、baseline、ratchet 及其 hook；
- 可以禁止新增或扩写冻结 private symbol/local/source layout 的 legacy/private-shape gate；
- 但现有 active 的非复杂度行为、typing、dependency、owner、source-discipline、format、build/package、pre-commit 及其他既有检查，不能仅凭 response 自述整体跳过。

最小回写不是新增测试或扩大范围，而是把 owner/response 的 gate 口径改成：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED
NO NEW OR EXPANDED LEGACY / PRIVATE-SOURCE-SHAPE GATE
```

若用户确实另有意图，要排除某个既有非复杂度 gate，必须由用户明确确认并在 authoritative requirements/准入记录中登记具体 gate；不能由 owner response 单方面推断。该整改不要求新增 legacy AST/private-source test，也不把已删除 symbol 固化成永久门禁。

### R9 — unknown nested scope 的 forged seed 不能按当前文字稳定复现

owner 第 8.1 节要求每个 forged seed 先满足 root/binding/action/frontier/settlement precedence，但对 unknown nested scope 没有给出实际构造步骤。源码 baseline 的验证顺序意味着仅把 action 的 scope 改成 `("unknown",)` 不足以观察目标错误：若没有同 scope 的 binding，先得到 `recovery admitted resume action does not match a simulated scoped successor`；若把未知 node 放进 frontier，则会先由 state/frontier owner 抛 `InvalidExecutionSnapshotError`；直接把 `object()` 放进 frame segment，则绕过 `validate_context()` 后会得到 `AttributeError`，不属于 recovery 的 frame contract。

因此 target case 必须把以下 recipe 写成可执行的 fixture 说明（仍可落在既有 `test_recovery_identity.py`，不新增 validator、测试文件或 source-shape gate）：

1. **Unknown scope**：保留合法 root binding；增加一个 scope 为 `(GraphNodeId("unknown"),)`、run ID 与 action 一致、superstep 与 action 一致且 frontier 中有目标 node 的 forged `RecoveryStateBinding`；action target 使用同一 unknown scope/node，并使用 non-skip + Pending settlement，使 action/binding/frontier checks 先通过，再由 `_compiled_graph_at_scope()` 产生 exact `SnapshotMismatchError("scope references unknown nested node 'unknown'")`。
2. **Unknown materialization**：保留 node 同时存在于 compiled `nodes` 与 Pending frontier；仅用 `replace(graph.transition, materializations=...)` 删除该 node entry；action 使用该 known node 的 non-skip Pending target，预期 shared query 抛固定 typed error，而不是 `KeyError`。
3. **Duplicate publication precedence**：直接构造带重复 publication coordinate 的 forged `ScopedFrameIndex`（不要通过 `add_publication()`，后者会提前拒绝），再叠加 unknown scope/materialization，确认 `from_frames()` 的既有 duplicate error 先于新增 lookup。
4. **Wrong descriptor**：使用同一 activation、但来自另一 compiled plan 的 descriptor，确认与 missing coordinate 使用同一 exact membership error；不要只断言 activation 相等。

每个 subcase 都应列出“前置 owner 已通过 → 目标 owner → exact type/text”的三元关系。否则表格中的 precedence 只是叙述，reviewer 无法区分目标错误和前置构造错误，`GSP-A06` 的 malformed/tamper evidence 仍不闭合。

## 5. 复核证据

### 5.1 Baseline behavior

在 production 未有 S12 diff 的前提下复跑：

```text
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_recovery_contract.py
→ 81 passed
```

非复杂度 architecture suite：

```text
python -B -m pytest -q -p no:cacheprovider tests/architecture -k 'not complexity'
→ 56 passed, 7 deselected
```

严格类型检查：

```text
pyright
→ 0 errors, 0 warnings, 0 informations
```

上述结果只证明当前 baseline；S12 production/test target 尚未获授权，不能写成 implementation verification。

### 5.2 一次性 owner inventory

当前 baseline `rg` 结果与 owner 账本一致：

```text
transition.materializations direct reads：7
  resume_input.py：4
  executor.py：1
  invocation.py：1
  routing.py：1

recovery.py direct materializations reads：0
ResumeInputAvailabilityCoordinate constructors：resume_input.py 1、executor.py 2
```

目标 inventory 只能在获批后的 actual diff 中验证；本轮不把 source scan 变成永久 private-shape gate。

`git diff --check` 已通过。未运行完整 `make check`、full package build 或 monorepo pre-commit；这些不是当前 docs-only review 的 target implementation evidence，且 complexity hook 按用户边界暂不裁决。若 R8 采用上述修正口径，owner 必须在获批 implementation unit 中逐项报告适用的非复杂度检查结果。

## 6. 最小整改与复审条件

在不扩大 S12 manifest、不修改 production/tests/requirements 的前提下，owner 只需：

1. 修正 R8 的授权/门禁三分口径；不得把 response 自述当作用户授权；
2. 为 R9 的 unknown scope、duplicate publication、unknown materialization 和 wrong descriptor 写出可执行 forged-seed construction 与 exact precedence；
3. 保持已通过的 R5/R6/R7、no-persistence 和 user-approved failover boundary，不引入第二 validator、lookup、constructor、cache、registry、runner 或 persistence path；
4. 更新 owner SHA 和实际变更摘要，再提交下一次独立技术复核。

本轮不要求新增 legacy/AST/private-source-shape gate，不要求恢复 skip 的无意义 lookup，不要求把 routing、continuation、failover 或 State 纳入 S12。

## 7. 当前状态

```text
S12 FIRST INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 SECOND INDEPENDENT REVIEW: CHANGES REQUESTED / RESPONSE RECORDED
S12 THIRD INDEPENDENT REVIEW: CHANGES REQUESTED / R8 + R9 OPEN
S12 GSP-A06: NOT APPROVED
S12 PRODUCTION / TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH/KERNEL FAILOVER RANGE: USER-APPROVED; NON-BLOCKING; NO S12 IMPLEMENTATION
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED PENDING EXPLICIT EXCLUSION
NO NEW OR EXPANDED LEGACY / PRIVATE-SOURCE-SHAPE GATE
```

只有 owner 回写 R8/R9、下一次独立评审确认 evidence 闭合、随后 requirements owner 在用户显式批准后回写 `GSP-A06`，才能按 owner 第 10.4 节 planned manifest 实施。

## 8. 本次 review change unit

本文件是本次第三次独立技术评审的唯一 actual changed-file：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-third-review.zh-CN.md
```

本文不覆盖任何既有 review/response，不拥有 S12 exact target；requirements 仍是唯一批准状态 owner。
