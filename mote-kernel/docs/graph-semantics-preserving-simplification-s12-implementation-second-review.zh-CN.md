# S12 `GSP-A06` 单项实施设计第二次独立技术评审

> **结论：`CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL`。首轮 R1、R2、R4 的主要回写已经完成，删除重复事实、phantom generic、no-State/no-persistence 和 Graph/Kernel failover 边界的方向均成立；但当前 owner writeback 仍有 materialization owner/consumer 账本不闭合、forged-seed precedence 证据不可按所写方式构造、skip malformed lookup precedence 未裁决，以及把 legacy/private-source gate 一并排除却没有用户授权四项阻断。本文不授权 production、tests、requirements 或持久化变更。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 本次 owner writeback SHA256：`eae0a63988a205d723dbd9cecb475fc5dc7e8b097c0b795ad949e08e6c4e461c`
- 首轮评审：[S12 `GSP-A06` 单项实施设计评审](graph-semantics-preserving-simplification-s12-implementation-review.zh-CN.md)
- 首轮评审 SHA256：`e100fca249d4972fba0cbff346b8a8fd9f980407f4742fc15a0fa45d5fca66cd`
- 首轮评审回复：[S12 首轮实施设计评审回复](graph-semantics-preserving-simplification-s12-implementation-review-response.zh-CN.md)
- 首轮回复 SHA256：`a4a6875d99ad79de9c9c7c213f49973e991847def5ea807c5143c69c32740da5`
- 声明源码基线：Git `35e7c95206e4124be68a9706359d1cc129e98c17`
- 基线文件 SHA256：
  - `src/mote_kernel/execution/engine/recovery.py`：`f88b6fc68b7677d227acc438c962fce8164815e55a46d448ec44d20bd02d9fba`
  - `src/mote_kernel/execution/invocation.py`：`043a5b3da9f016c4b8193116a3212775e16fa23538b47b803ba1a55c4540249a`
- 本文性质：第二次独立技术 review record；只拥有本轮裁决、发现和验证记录，不拥有 S12 target、requirements 批准状态、production shape 或测试 shape
- 本轮变更边界：只新增本文；不修改 owner writeback、首轮 review/response、requirements、normative source、production、tests、State、Store、protocol 或 persistence

## 2. 本轮审核边界

本轮继续按用户已经明确的约束审查：

- 不做持久化；`GraphRunState`、command/reducer、revision、commit、protocol、Store 和 persistence 是 HARD KEEP。
- 复杂度门禁可以暂时忽略；这不等于可以省略结构净账本、行为、类型、依赖和 active owner 证据。
- 唯一事实、复用已有基础设施、零已知负债和不扩大实施范围优先于表面减行。
- 用户已审批 Graph/Kernel failover 的边界。本轮不再把 failover 范围本身列为 blocker，只核对 S12 没有实现第二 runner、registry、Store、持久化或 Graph-owned retry/backoff/error policy。
- 不因为本轮发现 owner 口径问题就把 `routing.py`、continuation validator 或其他 S 单元未经批准地纳入 S12 manifest。

## 3. 总体裁决

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、Store、protocol、持久化 | **通过** | owner writeback、normative 计划和 planned manifest 均保持 HARD KEEP；没有新增 durable fact、Store 或 persistence path |
| Graph/Kernel failover 边界 | **通过，非阻断** | 按用户批准的范围，S12 只保留显式 action admission；没有第二 runner、registry、failover 实现或持久化占位 |
| `GSP-P01`–`GSP-P08` applicability matrix | **通过** | R1 已补齐逐项映射，且 P02 明确为不触及/HARD KEEP |
| 重复 resume fact 删除与 phantom generic migration | **通过设计** | `AdmittedResumeFact` 五字段、`_RecoveryFamily` 非泛型和真实泛型链条的方向闭合；valid-domain equality/hash/seen 证明没有发现反例 |
| action ↔ availability invariant | **方向通过，证据未闭合** | preflight 的位置和 non-skip/skip 规则合理，但 forged fixture 与 frame-owner precedence 需要回写 |
| compiled scope owner | **通过设计** | 将现有 `_compiled_at()` 收口到 topology-owned pure query，不建立第二 traversal/cache |
| node-materialization typed port | **不通过** | “唯一 lookup/error owner”与源码中 invocation/routing 的两个现有 consumer、账本和 source-review 查询不一致 |
| skip action malformed precedence | **不通过** | skip 不查 materialization 对 valid compiled graph 没有差异，但 forged compiled graph 的既有/目标首错没有裁决 |
| 非复杂度门禁口径 | **不通过** | 用户只授权暂时忽略 complexity；owner 不能自行把既有 legacy/private-source gate 一并宣布 out of scope |
| changed-file manifest / no scope expansion | **通过（前提）** | planned manifest 本身未越界；上述问题应通过 owner 文档和 case evidence 回写解决，不应顺手扩大 production 范围 |
| `GSP-A06` / 是否可实施 | **未闭合、未批准** | 需要 owner 新 SHA 和第三次独立复核；当前不得修改 production/tests/requirements |

## 4. 已闭合事项

### 4.1 R1 applicability matrix

owner 已增加 `GSP-P01`–`GSP-P08` matrix，并将 P02 写为 State/Store/protocol/persistence 不触及。这满足 requirements 对
P2 单项 evidence 的逐项映射要求；本轮没有发现 matrix 与 S12 删除目标之间的遗漏。

### 4.2 重复事实与泛型目标

在声明的 valid production seed 域内，每个 non-skip action 仍由 executor 恰好产生一个 resume-input coordinate，完整
`RecoveryAvailabilityCoordinates.resume_inputs` 保留该 presence，历史 coordinate 也不会被 proof 删除。因此删除
`AdmittedResumeFact.resume_input_availability`、同步删除 `AdmittedResumeFact[GraphValueT]` 和
`_RecoveryFamily[GraphValueT]` 的方向成立。`RecoveryTransferState`、`RecoveryInvocationSeed` 和 availability/frame
类型仍有真实 `GraphValueT` 承载，不能顺手去泛型；owner writeback 对此限制已写清楚。

### 4.3 no-State/no-persistence 与 failover

planned production/normative/test manifest 不包含 State、Store、protocol、persistence 或新的 runner。Graph/Kernel failover
章节只说明边界，不新增策略、重试、退避、registry、cache 或第二执行路径。用户已批准该范围，本轮将其记为通过而非再次要求扩大
或缩小 S12。

### 4.4 baseline evidence

本轮在 production 未有 S12 工作树 diff 的前提下实际复跑：

```text
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_recovery_contract.py
→ 81 passed

pyright src/mote_kernel/execution/engine/recovery.py \
        src/mote_kernel/execution/invocation.py
→ 0 errors, 0 warnings, 0 informations

相关 generic/dependency/owner/no-persistence/source-discipline nodeid
→ 10 passed

git diff --check
→ passed
```

这些结果证明当前 baseline 和现有 owner 边界，没有证明未实施的 S12 target 已交付，也不代替 requirements approval。
本轮没有运行完整 `make check`、full pytest、build/package 或 monorepo pre-commit；production/target tests 尚未获授权，且
automated complexity gate 按用户边界排除。未运行项不冒充通过。

## 5. 阻断问题

### R5 — materialization typed port 的唯一 owner 口径没有覆盖全部既有 consumer

owner writeback 第 4.2、7、8.3 节把 `_require_node_materialization(graph, node_id)` 定义为“唯一 node-materialization
lookup/error owner”，并把 source review 目标固定为恰好 `1 definition + 6 production consumers`。但源码 baseline 的全部
直接读取如下：

| 现有位置 | 读取 | 当前 owner 语义 |
| --- | --- | --- |
| `execution/engine/resume_input.py` | 4 处（`_admit_override`、`node_inputs_available`、`pending_node_input_available`、`materialize_node_input`） | S12 计划收口到 shared port |
| `execution/executor.py` | 1 处 | S12 计划收口到 shared port |
| `execution/invocation.py:552` | `scoped_graph.transition.materializations.get(...)` | continuation resume-input coordinate/frame integrity；缺失时有 continuation-specific error text |
| `execution/engine/routing.py:233` | `graph.transition.materializations[target]` | routing facts 的 binding/readiness owner |

因此全仓是 7 个 direct read sites，而不是 owner 账本暗示的 5 个 baseline reads。该发现不意味着把 routing 或 continuation
validator 盲目迁入 S12：routing 是 active routing owner，且 `resume_input.py` 已依赖 routing；跨迁移可能引入循环依赖、错误文本
改变或扩大 S12/S16 范围。

可复核的 baseline 查询为：

```text
rg -n 'transition\.materializations' src/mote_kernel/execution
→ resume_input.py 4、executor.py 1、invocation.py 1、routing.py 1（compiler producer 另计）
```

批准前必须在 owner writeback 中二选一并写成可审计边界：

1. **不扩大范围（推荐）**：把 port 的名称和责任收窄为 resume-coordinate/input-admission/recovery consumer 的唯一 lookup/error
   owner；明确 invocation continuation validator 与 routing binding lookup 是各自既有 owner，列出其 unchanged direct reads、错误
   contract 和结构账本；source-review 查询只对 S12 consumer 集合断言，并另列 recovery 禁止 direct map read。
2. **扩大到全局 accessor**：补齐 invocation/routing 的迁移、依赖方向、错误包装、manifest、行为 cases 和成本账本，再重新评审；
   不能以“恰好 7 处”而不登记这两个 consumer 默默改变范围。

在用户要求“不扩大实施范围”和当前 planned manifest 下，选项 1 才是可接受方向；但 owner 尚未完成该口径回写，所以
`GSP-P08`/唯一 owner evidence 仍不闭合。

### R6 — forged-seed subcase 的可构造性与 frame validation owner 混淆

owner 第 8.1 节把以下 subcase 写成同一个 `preflight_recovery()` target case 的证据：unknown nested scope、unknown
materialization、malformed frame projection 和固定 precedence。源码显示它们不能按当前文字随意构造：

- `preflight_recovery()` 的既有顺序是 root → bindings → action target/settlement → `RecoveryAvailabilityCoordinates.from_frames()`
  → `_prove_scope()`；`from_frames()` 只做 publication coordinate duplicate 检查，不验证 frame record 的 nominal type、scope
  或 descriptor。
- frame record 的 malformed owner 是 `invocation.validate_context()` / `_validate_frame_index()`，public facade 在
  `preflight_recovery()` 前调用它（`facade.py:664`、`invocation.py:638`）。preflight 直接接收 forged `ScopedFrameIndex` 时，错误
  不会由该 owner 提供；例如把 `object()` 放入 `resume_inputs` 会得到 `AttributeError: 'object' object has no attribute 'coordinate'`。
- 若把一个**不属于 compiled graph 的 node id** 直接放进 Pending frontier，现有 state/frontier owner 会先抛
  `InvalidExecutionSnapshotError("snapshot frontier contains unknown nodes: ...")`，不会走新的 materialization port。
- 要观察 planned unknown-materialization typed error，fixture 必须保留一个 compiled/state 已知的 Pending node，只 forged
  `CompiledGraph.transition.materializations` 使该 node 缺失；当前 baseline 随后确实泄漏 `KeyError("'node'")`，这才是 shared
  port 应替换的路径。

因此当前表格中的“Pending frontier unknown node + missing coordinate”和“malformed frame projection + unknown scope/materialization”
会形成假阳性或错误 owner 断言。owner 必须在不新增 validator、不复制 frame interpreter、不新增测试文件的前提下：

1. 固定 unknown-materialization fixture 为“known frontier node + forged compiled materialization map missing node”，并明确它是
   non-skip action；
2. 将 malformed frame precedence 交给 `validate_context`/continuation public case，或明确 `preflight_recovery()` 的输入前提并
   从该 target case 删除其不属于 preflight 的断言；
3. 保持既有 owner 顺序：frame owner 的错误先于新 scope/materialization/missing-coordinate lookup，且不在 recovery 中新增第二套
   frame validation。

在这些边界没有写回前，R2 的“exact error + precedence”证据还不能视为闭合。

### R7 — skip action 删除 lookup 后的 malformed compiled-graph precedence 未裁决

当前 `GraphExecutor.resume()` 在 action branch 之前读取
`self._graph.transition.materializations[requested.node_id]`（`executor.py:136`），所以 forged compiled graph 缺少该 plan 时，
即使请求是 skip，也会先得到 `KeyError`。owner target 改为 skip 不查 resume materialization；对正常 compiler 产出的 graph，每个
node 都有 plan，public valid behavior 不变；但对 owner 已经纳入测试的 forged compiled graph boundary，目标行为会改变：skip 可能绕过
lookup 并进入后续 admission/proof。

这不是要求恢复无效的 skip lookup，而是要求明确边界并有可观察证据：

- 若 forged compiled topology 不属于 S12 malformed domain，文档必须明确 skip bypass 是 valid-domain-only 设计，并在 target case
  中验证没有 mutation、没有第二 lookup，且不把 `KeyError` 误写成要求保持的 public error；
- 若该 forged topology 属于 S12 malformed domain，则 skip 也必须在 mutation 前以固定 typed error fail closed，不能因 skip 分支
  静默改变首错。

当前 owner 同时要求 unknown materialization 的 forged case、又没有写 skip 的例外或 test，因此 `GSP-P05/P06` 的 malformed
  precedence 尚未闭合。

### R8 — legacy/private-source gate 的排除没有获得本轮用户授权

owner response 第 4 节和实施方案第 12、14 节把以下两类都写成 out of scope：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
LEGACY / PRIVATE-SOURCE-SHAPE AST GATES: OUT OF SCOPE
```

本轮用户明确给出的例外是“复杂度门禁可以暂时忽略”；最新追加批准只覆盖 failover 范围，没有批准把既有
legacy/private-source gate 也全部跳过。必须区分：

```text
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED
NO NEW OR EXPANDED LEGACY/PRIVATE-SOURCE-SHAPE GATE
```

这不要求 S12 新增任何 AST/private-source test，也不把已删除 symbol 固化成永久门禁；它只要求继续运行当前 active
behavior、typing、dependency、owner、source-discipline、format、build/package、pre-commit 等非复杂度验证。若 owner 仍要
排除某个现有 gate，必须取得用户明确批准并在 requirements/owner evidence 中记录，不能由 response 文档单方面裁决。

## 6. failover 与持久化的非阻断确认

按用户已批准的 failover 范围，本轮确认以下内容均不是 blocker：

- S12 没有实现 failover policy、retry/backoff、错误分类、第二 runner、registry 或 cache；
- Graph 只接收显式 resume/interrupt/skip action，Kernel boundary 仍在 Graph 外；
- planned manifest 没有 State、Store、protocol、persistence 或 durable schema 文件；
- `SnapshotMismatchError` 只表示 snapshot/action 与 compiled fact 不一致，不代表 Graph 自行选择 failover。

这项确认不授权把 future failover Port 或 persistence 顺带写入 S12，也不要求修改 owner 现有 failover 段落。

## 7. 需要的最小 owner 回写（不扩大实施范围）

在同一 S12 owner 文档中完成以下回写后，才能进行下一次独立复核：

1. 明确 `_require_node_materialization` 的窄 owner 范围，登记 `invocation.py` 与 `routing.py` 的 unchanged direct reads 及其理由，或提交经重新批准的全局迁移 target；推荐前者。
2. 修正 unknown-materialization、unknown-scope 和 malformed-frame subcase 的 exact fixture/owner/precedence；不新增 frame validator、第二 port、测试文件或 source-shape gate。
3. 为 skip 不 lookup 写出 forged compiled graph 的 valid/malformed domain裁决和 observable target case，保持 mutation 前 fail-closed 义务。
4. 将门禁口径改为 complexity 单独排除、现有非复杂度门禁必需、不得新增/扩写 legacy/private-source gate；若要排除现有 gate，先取得用户明确批准。
5. 更新 owner writeback SHA、实际变更摘要和 review 链接；本二审记录保持只读历史，不被覆盖。

这些是设计证据和 owner 边界的最小补闭，不授权提前修改 production/tests，也不要求把 routing、continuation 或 failover 纳入
S12。

## 8. 当前状态

```text
S12 FIRST INDEPENDENT REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 OWNER RESPONSE: RECORDED
S12 SECOND INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED
S12 GSP-A06: NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH/KERNEL FAILOVER SCOPE: USER-APPROVED; NON-BLOCKING; NO S12 IMPLEMENTATION
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: OUT OF SCOPE
EXISTING NON-COMPLEXITY GATES: REQUIRED PENDING AUTHORIZED EXCLUSION
```

## 9. 本次 review change unit

本文是本次第二次独立技术评审的唯一 actual changed-file：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-second-review.zh-CN.md
```

本轮未修改 S12 owner、首轮 review/response、requirements、normative source、production 或 tests。S12 exact target 仍由 owner
实施方案唯一拥有，`GSP-A06` 批准状态仍由 requirements 唯一拥有。
