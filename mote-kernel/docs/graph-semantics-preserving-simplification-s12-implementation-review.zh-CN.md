# S12 `GSP-A06` 单项实施设计评审

> **结论：S12 的核心方向成立，删除重复 resume-input fact、同步去除 phantom generic、把 scope/coordinate 解释收口到共享 typed owner 的选择没有发现语义性反例，也没有触及 State 或持久化。但当前实施文档仍缺少强制 requirement 映射、完整 malformed evidence、准确的非复杂度门禁口径和完整结构成本账本，因此本轮裁决为 `CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL`。整改后必须重新独立技术评审；本记录不授权修改 production/tests。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 评审对象 SHA256：`79e51906b2324e557541638c0217f785115d5b089d0516e4709bb917f4ef38a6`
- 文档声明源码基线：Git `35e7c95206e4124be68a9706359d1cc129e98c17`
- 当前复核源码：`src/mote_kernel/execution/engine/recovery.py`、`invocation.py`、`executor.py`、`engine/resume_input.py`、`graph/topology.py`
- 交叉依据：requirements 第 2–7 节、Graph Node I/O normative implementation、现有 recovery/resume/continuation/architecture tests 及仓库 `AGENTS.md`
- 本文性质：独立 review record；只拥有本次裁决、问题和验证记录，不拥有 S12 target shape、requirements 批准状态、production shape 或测试 shape
- 本轮范围：只评审 S12；不评审或实施其他单元，不修改 State、Store、protocol 或持久化能力

## 2. 总体裁决

| 维度 | 裁决 | 结论 |
| --- | --- | --- |
| State、Store、protocol、持久化 | **通过** | S12 明确 HARD KEEP；planned manifest 不包含 State/persistence 文件，也不新增存储或执行路径 |
| 删除重复事实 | **通过设计** | valid production seed 中 non-skip action 的 resume-input presence 已由 equality-participating availability 完整拥有；删除 action mirror field 的方向成立 |
| valid-domain equality/hash/seen | **通过设计** | action tuple 在一次 proof family 内恒定，resume-input coordinates 不在 transfer 中被移除；删除字段不改变声明 valid domain 的 equality partition |
| generic migration | **通过设计** | `AdmittedResumeFact` 与 `_RecoveryFamily` 删除唯一承载 `GraphValueT` 的链后应同步变为非泛型；transfer state、seed 和 frame/availability 类型继续保持真实泛型 |
| 唯一 scope/coordinate owner | **通过设计** | recovery 若要验证 canonical exact coordinate，必须复用 compiled scope 与 materialization truth；移动既有 traversal、迁移全部 constructor consumer 不属于无关扩 scope |
| malformed invariant | **方向通过，evidence 不完整** | 在现有 preflight seed boundary fail closed 合理，但 unknown scope/materialization 的异常契约和精确测试尚未闭合 |
| requirement 映射 | **不通过** | 文档没有按 requirements 强制规则逐项裁决适用的 `GSP-P01`–`GSP-P08` |
| 结构净账本 | **不通过** | 删除旧 scan 已登记，但新增 availability membership scan及可能的重复 materialization lookup 未完整计账 |
| 非复杂度门禁 | **不通过** | 最终状态错误地把 legacy gates 与 complexity 一并写为 out of scope；本轮只允许排除 automated complexity gate |
| `GSP-A06` / 是否可实施 | **未闭合、未批准** | 必须由 target owner 回写、再次独立技术评审通过并由 requirements owner 显式批准后才能实施 |

## 3. 已确认成立的设计

### 3.1 action 与 availability 的唯一事实关系

当前 production chain 对每个 non-skip resume action 恰好产生一个 `AdmittedResumeInput`，对 skip action 不产生该 record；
`plan_resumes()` 将 record 加入 candidate frames，`RecoveryAvailabilityCoordinates.from_frames()` 再投影其 exact coordinate。
在 public `Graph.run()` 经 executor、context/frame admission 后形成的 valid seed 域内：

- action target 按 scope/node canonical 且 distinct；
- materialization descriptor 来自对应 compiled scoped graph；
- current non-skip action 与 current admitted resume-input record 一一对应；
- historical resume-input coordinates 可以额外保留，但不会替代 current action 的 exact coordinate；
- recovery transfer 只增加 graph input、publication 或 child-boundary availability，不删除 seed resume-input coordinates。

因此，`AdmittedResumeFact.resume_input_availability` 是 recovery equality 中对已有 availability fact 的重复保存。删除该字段、
保留 action 的 target/kind/interrupt/reason/route 五项语义，并让 availability 独立承担 resume-input presence，设计方向成立。

### 3.2 equality、hash、traversal 与 budget

本轮复核没有发现文档 valid-domain 等价证明的反例：

- 同一次 `_prove_scope()` 内所有 transfer state 使用同一个 family action tuple；
- `RecoveryAvailabilityCoordinates.resume_inputs` 参与 frozen dataclass equality/hash；
- `recovery_traversal_key()` 当前不读取拟删除字段；
- successor generation、seen admission、boundary projection及 `_RecoveryProofBudget.admit()` 的位置不依赖该字段；
- concrete frame value不进入 action或availability coordinate equality/hash/order。

因此只要 implementation 严格保持文档声明的 valid domain、validation顺序和 owner migration，seen partition、reachable boundary、
traversal order和4096 budget可以保持。该裁决不覆盖 forged/malformed seed；其行为必须由第4.2节整改项单独闭合。

### 3.3 generic target

当前 `AdmittedResumeFact[GraphValueT]` 唯一通过 `resume_input_availability` 承载 type parameter，`_RecoveryFamily[GraphValueT]`
又只通过 action tuple传递该 parameter。删除字段后，两者若继续继承 `Generic[GraphValueT]` 就是 phantom generic。

文档要求同步移除 generic bases、production/test subscriptions，并保留 `RecoveryTransferState[GraphValueT]`、
`RecoveryInvocationSeed[GraphValueT]`、availability和frame types的真实关系，该迁移是完整且方向正确的。不得通过 bare generic、
`Any`、`cast`、type alias或 `compare=False` 隐藏未完成迁移。

### 3.4 owner 与 planned manifest

recovery 不能直接读取 `transition.materializations` 或复制 input interpreter。要从 action target 推导 exact coordinate，必须：

1. 从 root compiled graph解析 action scope；
2. 从 scoped compiled graph取得 canonical materialization descriptor；
3. 使用与 runtime/executor相同的 resume-input coordinate constructor。

因此，把 invocation-local scope traversal迁移到 compiled topology owner，并让 resume materialization、executor admission、recovery
invariant复用一个 coordinate owner，是 S12 fail-closed 方案的直接依赖，不是无关重构。planned manifest列出的五个 production
文件、一个既有测试文件和 Node I/O normative source覆盖了该依赖链；当前未发现需要加入 public facade、State、protocol、Store、
README、Makefile、`pyproject.toml` 或 complexity文件的理由。

## 4. 阻断问题

### 4.1 R1 — 缺少强制的 `GSP-P01`–`GSP-P08` 适用性映射

Requirements 第3节明确要求实施方案为每个原子单元映射全部适用 `GSP-Pxx`，第6节又规定 P2 在 `GSP-A06` 单项准入时执行
同一 evidence 要求。当前 S12 文档没有任何 `GSP-P01`–`GSP-P08` 的逐项裁决，只有对 `GSP-S01`–`GSP-S08` 的总引用，
不能替代强制映射。

Owner writeback 至少应增加以下 applicability/evidence matrix，但只引用 requirements，不复制其规范正文：

| Requirement | 建议裁决 | S12 evidence 责任 |
| --- | --- | --- |
| `GSP-P01` | 适用 | public `Graph` facade/signature不变；strict typing与错误分类保持 |
| `GSP-P02` | 排除 / HARD KEEP | 不修改 `GraphRunState`、command、reducer、revision、protocol、Store或持久化；不要求新增持久化实现 |
| `GSP-P03` | 适用 | 新 invariant必须位于任何 fence/resume commit、claim、child start或node execution之前 |
| `GSP-P04` | 适用 | frame/continuation exact coordinate、descriptor和concrete-value隔离保持 |
| `GSP-P05` | 适用 | failure/interrupt/skip action、resume-input availability和settlement保持 |
| `GSP-P06` | 适用 | equality/hash、malformed boundary、traversal、reachable boundary与4096 budget是核心证据 |
| `GSP-P07` | 适用 | nested scope traversal、canonical action/binding order和child recovery保持 |
| `GSP-P08` | 适用 | topology/coordinate唯一 owner、依赖方向与generic migration保持 |

缺少该矩阵时，S12 不满足 requirements 对 `GSP-A03`/`GSP-A06` 的 case-level evidence 要求。

### 4.2 R2 — unknown scope/materialization 的 fail-closed 契约没有精确证据

S12 target要求 shared owner 对 unknown nested scope和unknown materialization node抛 typed `SnapshotMismatchError`，不得泄漏
`KeyError`。但 planned target case当前只列出：

- exact coordinate存在；
- missing coordinate；
- same activation / wrong descriptor；
- skip action与historical resume coordinate共存。

这些输入都使用存在的 compiled materialization，不会触发 `FrozenMap.__getitem__()` miss，因此该 case 的“不得泄漏 `KeyError`”
失败条件目前没有可观察路径；同时 target只固定了missing coordinate错误文本，没有固定unknown materialization的新错误文本和precedence。

Owner writeback应在不新增测试文件、不mock private function的前提下闭合：

1. 在已计划的 `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_requires_exact_resume_input_availability_for_each_non_skip_action`
   中增加unknown nested action scope和unknown materialization node子场景；
2. 固定两类异常的exact type、text和相对已有root/binding/action/frame validation的precedence；
3. 在existing behavior表和scoped gate中精确引用
   `tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_an_unknown_child_scope`，证明scope helper迁移后原错误文本不变；
4. 使用exception precedence或public/result observable behavior证明invariant先于proof，不使用mock/monkeypatch或源码AST断言。

上述整改仍可落在当前planned manifest内：existing continuation case只需原样复跑，新增subcase仍位于已经列入manifest的
`test_recovery_identity.py`。

### 4.3 R3 — 非复杂度门禁状态与本轮原则冲突

S12文档第14节把最终状态写成：

```text
AUTOMATED COMPLEXITY / LEGACY GATES: OUT OF SCOPE
```

本轮明确允许暂时忽略的只有automated complexity gate/baseline/ratchet。现有非复杂度门禁，包括既有behavior、typing、owner、
dependency、source discipline以及历史上已存在的non-complexity/legacy checks，仍必须通过；只是不为S12新增或扩写
legacy/private-source-shape/AST gate。

该状态必须改为不含歧义的两条：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED; NO NEW OR EXPANDED LEGACY/PRIVATE-SOURCE GATE
```

当前verification命令中的full pytest、strict Pyright、ruff、build/package、monorepo pre-commit和`git diff --check`方向正确；
状态文字不得授权跳过其中任何existing non-complexity gate。

### 4.4 R4 — 结构账本没有完整记录替代 scan 与 lookup 成本

结构账本记录 `_resume_facts()` 的 per-action `PreparedResume.inputs` scan从1变0，同时只把新preflight逻辑登记为一个
invariant branch。实际target loop会对每个non-skip action调用 `RecoveryAvailabilityCoordinates.has_resume_input()`；当前实现是
对 `resume_inputs` tuple的线性membership，而该tuple可包含多次历史invocation的coordinates。旧scan被删除属实，但整体不是
“scan归零”，而是从current `PreparedResume.inputs` scan迁移为对full recovery availability的seed invariant membership。

此外，target `_resume_input_coordinate(graph, activation)` 自身查找materialization；现有 `materialize_node_input()` 又必须继续
持有同一 `MaterializationPlan` 处理bindings和declarations。若exact implementation不进一步明确，就会对线性 `FrozenMap` 做两次
相同node lookup；若为了避免重复而在caller直接构造coordinate，又会破坏唯一owner。

Owner writeback必须在批准前固定并计账：

1. 把旧scan到新membership scan的before/after写入结构账本，不把自动complexity gate排除解释成可以省略结构事实；
2. 明确 `materialize_node_input()` 的exact lookup/constructor调用形状，避免把重复lookup作为未评审实现细节留下；
3. 若需要调整helper signature或增加一个窄typed materialization lookup port，必须同步修订function-count/最多新增面和source evidence；
4. 不得用cache、index、DTO、context bag、overload、compatibility alias或第二coordinate constructor规避该问题。

该问题不否定删除field和generic的方向，但在“零负债、结构净简化”原则下，当前账本还不足以支持 `GSP-A06` 批准。

## 5. Evidence 复核

### 5.1 源码基线

文档登记的两个baseline文件SHA256与当前工作树一致：

```text
src/mote_kernel/execution/engine/recovery.py
f88b6fc68b7677d227acc438c962fce8164815e55a46d448ec44d20bd02d9fba

src/mote_kernel/execution/invocation.py
043a5b3da9f016c4b8193116a3212775e16fa23538b47b803ba1a55c4540249a
```

当前HEAD在声明baseline之后的production差异只涉及S12范围外的graph validation；本轮审查的S12 production文件没有工作树diff。

### 5.2 已复跑baseline

按S12文档登记的scoped命令复跑：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_recovery_contract.py
```

结果：`81 passed`。

严格类型检查：

```text
pyright src/mote_kernel/execution/engine/recovery.py \
        src/mote_kernel/execution/invocation.py
→ 0 errors, 0 warnings, 0 informations
```

`git diff --check`通过。以上结果只证明当前baseline和文档源码审计可信，不冒充target已实施或full delivery gate已通过。

### 5.3 未运行的门禁

本轮是docs-only独立设计评审，production/target tests尚未实施，因此没有运行完整`make check`、full coverage、build/package或
monorepo pre-commit。完整`make check`当前无条件包含本轮明确排除的complexity gate；未来implementation必须按整改后的S12
verification plan运行其余全部non-complexity等价命令，并精确报告任何无法运行的check。

## 6. 整改与复审边界

本轮不是对核心方向的否决。Target owner可以只修改S12实施文档，完成以下最小回写：

1. 增加 `GSP-P01`–`GSP-P08` applicability/evidence matrix，其中`GSP-P02`明确排除并继续HARD KEEP；
2. 固定unknown nested/materialization的exact异常与case-level evidence；
3. 把legacy gate状态改为existing non-complexity gates required；
4. 补齐scan/lookup结构账本并固定无新增负债的exact owner shape；
5. 更新review target SHA/变更摘要，提交第二次独立技术评审。

若owner回写改变target algorithm、function signature、planned manifest、最多新增面或malformed precedence，必须完整重新评审对应项，
不能把本次对“方向成立”的裁决解释为对新target的批准。Requirements在二审通过且用户显式批准前不得把S12写为
`GSP-A06 SATISFIED`。

## 7. 最终状态

```text
S12 INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / NOT PASS
S12 CORE DIRECTION: TECHNICALLY VIABLE
S12 DESIGN EVIDENCE: INCOMPLETE
S12 GSP-A06: NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED
```

本review不修改S12 target owner、requirements、production或tests，不建立第二套target truth，也不授权持久化工作。

## 8. 本次 review change unit

本文件是本次独立review audit唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-review.zh-CN.md
```

S12 exact target仍由其实施方案唯一拥有；`GSP-A06`批准状态仍由requirements唯一拥有。
