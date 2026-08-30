# S12 `GSP-A06` 单项实施设计第四次独立技术评审

> **结论：`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`（仅表示当前设计证据闭合）。R8 的门禁授权和 R9 的 forged-seed 可复现性均已关闭；未批准 `GSP-A06`，未授权 production、tests、normative source、requirements 或任何持久化变更。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 本轮绑定 owner SHA256：`1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7`
- 第三次独立评审：[S12 第三次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-third-review.zh-CN.md)，SHA256：`33f71f27a77ea13e37154fa07216eb72709e755a5d2e87e4c6eb1fbaedf50e56`
- 第三次评审回复：[S12 第三次实施设计评审回复](graph-semantics-preserving-simplification-s12-implementation-third-review-response.zh-CN.md)，SHA256：`a47672f16675b718285a8f2ed1d25e0851209cbd119358cfbe62ce5a710aaf8c`
- 声明源码基线：Git `35e7c95206e4124be68a9706359d1cc129e98c17`
- 基线 production 文件 SHA 与 owner 记录一致：
  - `src/mote_kernel/execution/engine/recovery.py`：`f88b6fc68b7677d227acc438c962fce8164815e55a46d448ec44d20bd02d9fba`
  - `src/mote_kernel/execution/invocation.py`：`043a5b3da9f016c4b8193116a3212775e16fa23538b47b803ba1a55c4540249a`
- 本文性质：第四次独立 technical review record；不拥有 target shape、requirements 批准状态或 production/test shape
- 本轮 change unit：只新增本文，不修改 owner、任何 response 历史、requirements、normative source、production、tests、State、Store、protocol 或 persistence

## 2. 评审边界与总裁决

本轮按用户已经明确给出的边界审核：不做持久化；保持唯一事实源并复用现有基础设施；复杂度 gate、baseline 和 ratchet 暂不裁决；Graph/Kernel failover 范围已经批准。用户同时明确排除 legacy/private-source-shape 门禁测试，但没有排除仍然有效的 current-contract behavior、strict typing、owner/dependency、no-persistence 和质量检查。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| Durable State、Store、protocol、persistence | **通过** | `GraphRunState`、command/reducer、revision、commit、protocol、Store 与 persistence 均 HARD KEEP；planned manifest 未触及这些 owner |
| Graph/Kernel failover | **通过，非阻断** | 只执行调用方显式 action；没有 Graph-owned retry/backoff/error policy、第二 runner、registry、Port 实现或 failover 持久化 |
| 唯一 resume fact | **通过设计** | 删除 `AdmittedResumeFact.resume_input_availability` 后，presence 仅由 `RecoveryAvailabilityCoordinates.resume_inputs` 参与 equality/hash；没有第二 availability truth |
| generic migration | **通过设计** | `AdmittedResumeFact` 与 `_RecoveryFamily` 去除 phantom `GraphValueT`，`RecoveryTransferState`、seed、availability 和 frame 关系仍保留真实泛型 |
| compiled scope/materialization owner | **通过设计** | `_compiled_graph_at_scope()`、`_require_node_materialization()` 和 `_resume_input_coordinate()` 的职责不重叠且复用现有 compiled/frame 基础设施 |
| R8 门禁口径 | **已关闭** | 用户排除 complexity 与 legacy/private-shape；owner 仍把列明的 current-contract checks 标为 REQUIRED，没有整体跳过质量检查 |
| R9 forged-seed evidence | **已关闭** | typed base seed、七项机械派生、具体构造代码、precedence 和“前置 owner → 目标 owner → exact observable”矩阵足够复现目标边界 |
| skip 支持域 | **通过（有界）** | compiler-produced valid skip 继续不消费 node input；skip + 私下 forge compiled map 缺项明确属于 S12 支持域之外，不恢复无意义 lookup |
| manifest / 范围 | **通过** | planned production + behavior + normative manifest 未扩大；review/response 仍是独立 audit units |
| `GSP-A06` 当前状态 | **未批准** | 本文只把设计标为 ready for requirements owner；用户显式批准和 requirements-only 回写仍是后续必要步骤 |

本轮没有遗留需要再开立的 R item，也没有发现会迫使 S12 增加 State、Store、协议、第二解释器、缓存、registry、runner、validator 或持久化路径的设计负债。`GSP-A06` 的“通过”仅限设计审查，不等于 implementation approval。

## 3. R8：用户授权与 current-contract 边界已正确分离

owner 第 12、14 节现在使用穷尽且不混淆的三分法：

```text
REQUIRED: 明确列出的 current behavior、strict typing、active generic/dependency/owner/source-discipline、lint、format、build/package 与跳过 complexity hook 后的 pre-commit
USER-EXCLUDED: automated complexity gate / baseline / ratchet
USER-EXCLUDED: legacy/private-source-shape gate，无论既有还是拟新增
```

该口径符合用户的“不做 legacy 门禁测试”授权，同时没有把 current-contract 检查伪装成 legacy gate。owner 仍要求复跑 recovery behavior、generic、dependency、single-owner、no-persistence、typing、lint、format、build/package 和适用 hooks；一次性 `rg` inventory 也明确不是永久 source-shape gate。

因此第三次评审的 R8 不再是阻断项。requirements 仍保持 S12 未批准是正确的：门禁分类已闭合，但批准状态不能由 review/response 自行改写。

## 4. R9：typed base seed 与七项派生的独立复核

### 4.1 Base seed 的前置条件

第 8.1.1 节使用现有 `empty_graph()`、`project_start_graph_command()` 和 `reduce_graph_run()` 生成真实 root state，而不是手写无效 frontier。该 state 的 frontier 含 known `PendingGraphNode("node")`，run ID、superstep、scope-run 和 action target 一致；`MaterializationPlan` 来自同一 compiled graph，`NodeInputFrame[str]` 和 `AdmittedResumeInput[str]` 保持 typed annotation，exact coordinate 使用真实 descriptor identity。

我按当前 baseline 复演了这组前置关系：exact seed 可进入既有 proof boundary；只替换为空 `ScopedFrameIndex` 不会被更早的 binding/frontier 错误截断；这说明新增 invariant 的目标位置确实在既有 action/state/frame projection 之后、proof/mutation 之前。

### 4.2 七项派生与 precedence

| Subcase | 已通过的前置 owner | 目标 owner → exact observable |
| --- | --- | --- |
| exact current coordinate | root/binding/action/frontier/settlement、projection、scope、materialization | exact membership → 既有 proof boundary |
| missing coordinate | 同 exact；只把 frames 替换为空 index | exact membership → `SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")` |
| same activation / wrong descriptor | 同 exact；record 仍为 nominal typed frame，descriptor 取自真实 `interruptible_graph()` compiled plan | exact membership → 与 missing 完全相同的 type/text；不接受 activation-only match |
| unknown nested scope | root binding 保留；新增 `("unknown",)` scope 的 matching run/superstep/Pending state binding 和 action | `_compiled_graph_at_scope()` → `SnapshotMismatchError("scope references unknown nested node 'unknown'")` |
| unknown materialization | compiled `nodes`、state frontier、known Pending action 保持不变；只过滤 immutable materializations entry | `_require_node_materialization()` → `SnapshotMismatchError("node input references an unknown compiled materialization")`，不泄漏 `KeyError` |
| duplicate publication projection | 直接构造 nominal `ConfirmedPublication`，以 `ScopedFrameIndex(publications=(record, record))` 绕过会提前拒绝 duplicate 的 `add_publication()`；叠加 unknown-scope seed | `RecoveryAvailabilityCoordinates.from_frames()` → `SnapshotMismatchError("recovery publication availability coordinates must be unique")`，早于 scope/materialization query |
| valid skip + historical coordinate | 使用 compiler-produced valid skip post-resume state，保留既有 historical resume coordinate；不 forge compiled topology | skip exclusion → 不要求本 invocation current resume coordinate，不删除历史 coordinate，既有 skip route/output boundary 保持 |

上表中的关键构造均有 owner 文档中的具体代码或机械 `replace()` 派生。特别是：

- unknown scope 不把未知 node 放进 frontier，而是让 known `node` 位于由同一 valid graph/reducer 产生的 matching binding 中，避免 state/frontier owner 抢先报错；
- unknown materialization 不删除 `graph.nodes` 或 frontier node，只删除 map entry，因此正好命中 shared query 的 typed error；
- wrong descriptor 不手写 malformed descriptor，而取另一个真实 compiled graph 的 descriptor；
- duplicate publication 不调用 `add_publication()`，确保观察的是 recovery projection 的既有 precedence，而不是 frame admission owner 的错误；
- skip 的 forged compiled topology 被明确排除，required behavior 只覆盖 valid skip 与历史 coordinate 共存，不把 baseline 偶然 `KeyError` 固化为 contract。

对当前 baseline 的独立顺序检查得到：duplicate publication 先命中既有 duplicate-coordinate `SnapshotMismatchError`；unknown-scope seed 的 binding/frontier/action 前置条件可成立；known-node-only 的 missing-materialization forge 当前会泄漏 `KeyError`，正是目标 shared query 要收口的路径。该检查只验证设计 fixture 与 baseline precedence，不冒充 target implementation 已通过。

## 5. 唯一事实、owner 复用与结构边界

owner 的 target 没有引入第二事实：

- `AdmittedResumeFact` 只保留 target、kind、interrupt、reason、route 五个 action 语义字段；resume presence 只由 `RecoveryAvailabilityCoordinates.resume_inputs` 拥有；concrete frame value 不进入 recovery equality/hash/order/repr；
- `_resume_input_coordinate(activation, plan)` 仍是唯一 coordinate constructor；`_require_node_materialization(graph, node_id)` 只服务 resume-input runtime/executor/recovery consumer 集合；continuation validator 与 routing binding/readiness 继续保留各自既有 direct read 和错误 owner，不被伪装成全局 accessor；
- `_compiled_graph_at_scope()` 只是把已有 traversal 搬到 topology owner，不建立 family map、cache、第二 nested index 或 compatibility alias；
- `preflight_recovery()` 只在 seed admission 做一次 non-skip action loop，先投影 availability，再按 canonical compiled scope/plan 检查 exact membership；不读取 concrete value，不复制 frame validator，不提前 claim、fence、commit、child start 或 node execution；
- 结构账本如实计入删除旧 `PreparedResume.inputs` scan、新 membership、shared lookup 和分支成本；复杂度 gate 虽按用户授权排除，仍没有用删除账本掩盖新增逻辑面。

这满足“唯一真相、复用基础设施、代码结构收口”而没有扩大 S12 到 routing、continuation、State、failover 或 persistence。

## 6. no-persistence、failover 与 changed-file manifest

以下硬边界在 owner 文档和本轮复核中均保持：

- `GraphRunState`、command/reducer、revision、commit、protocol、Store、checkpoint、journal、database、persistence port/backend 均不进入 S12 manifest；
- Graph 不选择 retry、backoff、error classification 或 failover 行为；Kernel 的已审批 failover 范围仍在 Graph 外的 typed Port 边界，本单元不定义、实现、缓存或持久化该 Port；
- 不新增第二 runner、registry、execution path、frame/value store、DTO、context bag、validator 或长期 index；
- 批准后的 planned manifest 仍精确为：

```text
mote-kernel/src/mote_kernel/execution/graph/topology.py
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/engine/test_recovery_identity.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

requirements、production/test/normative 只有在后续独立批准链完成后才可按该 manifest 原子修改；本轮没有授权提前实施。

## 7. 复核证据与未运行项

当前 baseline 复跑结果：

```text
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_recovery_contract.py
→ 81 passed

python -B -m pytest -q -p no:cacheprovider tests/architecture -k 'not complexity'
→ 56 passed, 7 deselected

pyright
→ 0 errors, 0 warnings, 0 informations

git diff --check
→ passed
```

这些结果证明当前 baseline 和 review fixture audit 可复核，不证明 S12 target 已实施。没有运行完整 `make check`、full package build 或 monorepo 全量 pre-commit：本轮是 docs-only review，且 `make check`/默认 hooks 包含用户明确排除的 complexity gate；implementation unit 获批后仍必须按 owner 第 12 节逐项运行 REQUIRED current-contract、typing、lint、format、build/package 和适用 hooks，并把结果写回实际 implementation owner。legacy/private-source-shape gate 不因“既有”而重新纳入。

## 8. 当前准入状态与后续授权

```text
S12 DESIGN: COMPLETE
S12 FIRST INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 SECOND INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 THIRD INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 FOURTH INDEPENDENT TECHNICAL REVIEW: PASS / READY FOR REQUIREMENTS OWNER APPROVAL
S12 R8: CLOSED BY EXPLICIT USER GATE BOUNDARY
S12 R9: CLOSED — TYPED BASE + SEVEN MECHANICAL DERIVATIONS + PRECEDENCE MATRIX
S12 GSP-A06: NOT APPROVED
S12 REQUIREMENTS WRITEBACK: NOT AUTHORIZED YET
S12 PRODUCTION / TEST / NORMATIVE IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH-OWNED FAILOVER / RETRY POLICY: FORBIDDEN; KERNEL TYPED PORT BOUNDARY HARD KEEP
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: USER-EXCLUDED
LEGACY / PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED WHETHER EXISTING OR NEW
CURRENT BEHAVIOR / TYPING / ACTIVE OWNER-DEPENDENCY CHECKS: REQUIRED
```

本轮通过只允许 requirements owner 进入下一次 **requirements-only** 审批准备；仍须用户显式批准后，requirements owner 才能把 `GSP-A06` 回写为仅限本文 reviewed exact target 的 satisfied 状态。任何 production/test/normative diff、持久化 diff、manifest 扩大或 failover policy 变化都必须停止并重新评审。

## 9. 本次 review change unit

本文件是第四次独立技术评审的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-fourth-review.zh-CN.md
```

不覆盖任何既有 review/response，不把用户或其他单元的 dirty changes 纳入本 change unit。
