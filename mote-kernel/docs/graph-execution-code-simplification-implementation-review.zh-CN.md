# Graph execution 代码简化实施方案（候选 A）评审

> **结论：`CHANGES REQUESTED / NOT APPROVED`。本轮只允许把 `EX-A0` 作为只读证据探针继续完善；`EX-A1`、`EX-A2` 不得修改 production 或 behavior tests。当前实施方案没有证明 `ScopedStateIndex` 会删除一个真实的第二事实源，反而预先引入了一个与 `GraphRunContext` 重叠的 owner/API。必须先按本评审重做 A 的 owner、删除面、错误边界和复杂度账本，再申请单项批准。State、Store、协议、持久化和 failover 不在本轮范围内，也不得因整改被带入。**

## 1. 评审对象与范围

用户给出的路径 `docs/graph-execution-explosive-simplification-implementation.zh-CN.md` 在当前工作树中不存在。实际存在、且内容对应“实施 md”的文件是
[`graph-execution-code-simplification-implementation.zh-CN.md`](graph-execution-code-simplification-implementation.zh-CN.md)。本记录以该文件为唯一评审对象；不创建同内容的别名或第二份实施文件，避免制造第二个文档事实源。

- 评审日期：2026-08-25
- 评审对象状态：`DESIGN DRAFT / NOT APPROVED FOR PRODUCTION`
- 评审对象 SHA256：`1f5fca41eb2cde31ae4240978d87449e17a68dee874ee0a0351103c0b18ceec3`
- 研究依据：[`graph-execution-explosive-simplification-research.zh-CN.md`](graph-execution-explosive-simplification-research.zh-CN.md)
- 交叉依据：requirements、主实施方案、当前 `run_context.py` / `invocation.py` / `family_driver.py` / `facade.py` 与现有 execution tests
- 本轮范围：只评审候选 A（`ScopedStateIndex` / `ScopedStateBinding`）的 owner、target、生命周期、错误和证据；候选 B 不进入本轮判断
- 本轮不做：production、behavior tests、State/Store/协议/持久化/failover 设计或修改

本文是独立 review record，不拥有 A 的 requirements、target shape、批准状态或 production 事实；整改完成后仍须由唯一 owner 文档回写。

## 2. 先决原则

### 2.1 唯一真相

已确认的 root/child runtime state 当前由 `GraphRunContext` 持有；`lineage_states()`、Result projection 和 recovery seed 是不同生命周期的 typed projection，不是可以按字段形状合并的第二份 runtime state。planned successor 仍只属于 invocation，continuation 仍是边界快照。任何 target 都必须明确一个 canonical producer、所有 consumer、失效时点和投影方向。

### 2.2 零负债

不得以“先引入 index、以后再清理”为迁移策略。禁止 compatibility alias、双写/双读、重复 wrapper、宽 DTO、隐藏 cache、第二解释器、无语义 helper 和只为让旧测试通过而保留的 private shape。新增类型或方法必须由可复核的删除面抵消，并在同一原子 change unit 中完成 producer、consumer、fixture 和无用 import 的删除。

### 2.3 复用基础设施

优先复用现有 `GraphRunContext`、`ScopeRunCoordinate`、`root_scope_run()`、`child_scope_run_for_activation()`、`GraphRunState`、既有 continuation validator、`ScopedFrameIndex` 和 family-driver 的 commit/install 顺序。已有 owner 能直接表达事实时，迁移 consumer，不再包一层同义 façade。

### 2.4 优美、合理且规范

保留 root/child、planned/confirmed、runtime/recovery 的 nominal boundary；使用窄 typed immutable value；保持首错阶段、错误文本、`__cause__`、排序和 commit/state/frame 顺序确定。代码短小只有在语义 owner 更清楚、理解成本更低且复杂度账本逐项不回退时才算简化。

## 3. 总体裁决

| 评审项 | 裁决 | 说明 |
| --- | --- | --- |
| 文档路径与 owner | **不通过** | 用户给出的路径不存在；新实施文档尚未被 requirements 或主实施方案登记为唯一 owner |
| A 的方向 | **条件可行，尚未证明** | 可能减少 confirmed context 的重复投影，但当前证据不足以证明存在可删除的第二事实源 |
| `ScopedStateIndex` target shape | **不通过** | 与 `GraphRunContext` 的 state/lookup/replacement API 重叠，owner 关系和 consumer 关系互相矛盾 |
| lifecycle / error boundary | **不通过** | index 构造时的 canonicalization 与现有 continuation tamper 首错阶段尚未闭合；child start/replacement 语义也未精确定义 |
| 复杂度与零负债 | **不通过** | 预期新增 class/fields/operations，却没有逐项 deletion ledger、after 值或全部 ratchet metric |
| evidence / acceptance | **不通过** | 只有文件级测试清单，没有可复现的 `path::test_case`、断言目标和失败条件 |
| no-State / no-persistence 边界 | **方向通过** | 文档声明不改 State 等边界；整改仍须保持该硬边界，不把恢复/存储设计带入 A |
| 当前 production baseline | **通过** | 当前相关行为、typing、ruff、architecture 和 complexity gates 均为绿色；没有理由先改代码 |

## 4. 阻断问题

### R1：产生了未登记的第二份 target/owner 事实

requirements 的文档分工规定：requirements 只拥有 requirement/批准状态，主实施方案拥有 target shape、原子边界、复杂度账本和 characterization；主实施方案第 4 节又明确把 `ScopedStateIndex` 列为账本外、未来必须另立需求/架构评审的方向。当前新文档没有 requirement ID、owner 指针、单项批准记录或主实施方案链接，却已经给出 exact target shape、manifest、停止条件和完成定义。

这同时违反了两个“唯一真相”要求：

1. `graph-semantics-preserving-simplification-requirements.zh-CN.md` 没有 A 的单项准入记录；
2. 主实施方案仍把 A 定义为未立项候选，而本文件把它写成可执行实施单元。

**整改要求：**先由 requirements owner 建立一个明确的 A 单项 ID，并把唯一 target owner、适用的保持义务和批准状态接入主实施方案；或者把本文件降级为研究/评审记录，删除 implementation target。不要通过复制、重命名或别名同时保留两份实施规范。

### R2：`GraphRunContext` 与 `ScopedStateIndex` 的 owner/API 重叠

当前 confirmed state owner 已经集中在 [`run_context.py`](../src/mote_kernel/execution/run_context.py) 的 `GraphRunContext`（`root_binding`、`child_states`、`state_at()`、`child_state()`、`replace_state()`、`replace_child()`）。现有 production consumer 也都直接调用 context operation：family driver 负责 state lookup/replacement，facade 负责 fence/resume handoff，invocation 负责 lineage projection。

方案第 5.2 节又为新 index 预设同义的 `state_at()`、`child_state()`、`replace_root()`、`replace_child()`、`ordered_bindings()`，第 5.3 节同时要求保留 context 的同名语义入口并称其为“真实 owner operation”。这会产生无法同时满足的合同：

- 如果 context 继续是 owner，index 方法通常只有 context 一个 production consumer，违反“每个方法至少两个真实 consumer”，并形成 forwarding plumbing；
- 如果 consumer 直接读取 index，context 方法就会变成 dead wrapper/第二入口，违反零负债；
- 如果两者都可被调用，就有两个 mutation/lookup owner，错误和生命周期规则可能漂移。

**整改要求：**A0 必须先画出 actual call graph，并选择唯一 owner。不能预先批准这组对称 API。若 probe 不能证明删除 context 入口后认知面下降，结论应为 `KEEP / NO IMPLEMENTATION`，继续使用现有 `GraphRunContext`。

### R3：当前事实是一次存储加多种 typed projection，不是已证实的重复 storage

当前 runtime context 只有一份 root binding 和一份 child tuple；`lineage_states()` 产生 invocation-local `_PlannedState`，`_scoped_states()` 产生 Result view，`recovery_seed()` 产生 recovery binding。这些记录分别携带不同的生命周期和验证责任。方案尚未给出任何 producer/consumer 证据，证明它们正在重复生产同一个 confirmed fact；目前更像是把现有字段从 context 搬到另一个 record。

此外，目标 shape 的 `children: tuple[...]` 仍然需要线性查找，`replace_*()` 仍需重建 tuple，`ordered_bindings()` 还会新增一次 projection。禁止 cache、generic map 和第二 interpreter 后，不能把“换一个容器”算作 lookup 复杂度下降。planned tuple 的 `_planned_state()` / `_replace_planned_state()` 与 recovery projection 按文档必须保留，因此 A 也不能声称删除这些阶段的扫描。

**整改要求：**A0 的 before/after 必须逐个记录真实 full scan、sort、allocation、lookup 和 projection；同时标明每个 projection 的 nominal 类型和 owner。只要删除面不能严格落在 confirmed context 的实际重复逻辑上，就保持现状。

### R4：目标 shape 与现有复杂度门禁没有闭合

当前执行结果为：

```text
top_level_definitions = 504
type_definitions = 288
dataclass_types = 178
dataclass_fields = 500
decision_points = 1327
logical_clone_pairs = 12
record_shape_clone_pairs = 21
thin_single_use_helpers = 17
single_use_private_dataclasses = 1
test_only_private_definitions = 0
reviewed = 51 / unreviewed = 0 / stale = 0
```

`ScopedStateIndex` 至少会增加一个 class 和两个 dataclass fields；其预设的方法实现还可能增加 decisions/imports。文档没有列出等量的 top-level/type/dataclass 删除，也没有给出 `dataclass_types` 和 `test_only_private_definitions` 的 before→after。第 6.4 节以“删除的扫描、字段、转发和分支严格多于新增 type、参数、校验和适配”作准入条件，把不同维度混成一个不可复算的总量，不能替代 ratchet 的逐指标约束。

**整改要求：**

1. A0 必须给出所有 ratchet metric 和 health candidate identity 的 before→after，而不是只给行数或四个汇总数字；
2. 每个新增 class/field/branch/import 必须对应明确的删除项，且每个指标都不得超过当前基线；
3. 不通过修改 `pyproject.toml` 上调基线或把新 candidate 填进 reviewed 清单来“放行”；
4. 若不能在不新增结构债务的情况下实现 target，结论必须是 `KEEP / NO IMPLEMENTATION`。

### R5：index invariant 与 continuation tamper 首错阶段未闭合

当前 continuation admission 先把 snapshot 的 root/child tuple 交给 `GraphRunContext`，再由 `validate_context()` / `lineage_states()` 按既有顺序检查 shape、canonicality、scope identity、parent consistency 和 frame integrity。现有 deterministic cases 明确锁定了这些边界：

- `tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_an_unknown_child_scope`
- `tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_duplicate_child_run_coordinates`
- `tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_a_child_run_id_mismatch`
- `tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_inconsistent_parent_coordinates`
- `tests/execution/test_continuation_integrity.py::test_continuation_validation_keeps_shape_before_canonicality_precedence`

如果 `ScopedStateIndex` 的 dataclass constructor 在 admission 时排序、去重或拒绝 child，错误会在 `validate_context()` 之前抛出；如果 constructor 不做这些检查，`children` 就不能声称“始终 canonical、无重复”。这不是测试 fixture 的小差异，而是 owner、错误类型、文本、`__cause__` 和首错阶段的改变风险。

**整改要求：**明确唯一的 admission boundary：要么 index 只在已验证输入上构造并不改变 malformed snapshot 的首错路径，要么保持现有 context/validator 结构。不得用 private-shape gate 掩盖差异；必须以现有可观察行为 cases 证明 shape→canonicality→content 顺序不变。

### R6：child acknowledged start 与 replacement 的 nominal contract 不完整

当前 [`family_driver.py`](../src/mote_kernel/execution/family_driver.py) 在 child commit 成功后通过 `replace_child()` 安装 acknowledged child；`replace_state()` 则先查找已存在 child，并保留原 `parent_activation`。现有 `GraphRunContext.replace_child()` 的实际行为是“按 coordinate 替换或插入”，并没有单独的 start operation。

方案要求新增 `acknowledge_child()`，同时要求 `replace_child()` 只能替换已存在 child，但没有定义：

- replacement 传入不同 `parent_activation` 时是拒绝、忽略还是覆盖；
- root coordinate、空 scope、run ID mismatch 在 index 层还是既有 owner 层失败；
- duplicate start、unknown replacement 的 exception type/text/cause；
- index 操作失败时 context/index 是否保持原 snapshot；
- child commit、state replacement 和 graph-input frame installation 的相对顺序。

**整改要求：**使用窄的 lifecycle-specific operation；replacement 必须保留既有 activation 或对不一致输入 fail closed，不能让 `ChildStateBinding` 参数成为重建 child identity 的通道。至少把以下现有行为固定为 exact cases，再决定是否值得引入新 operation：

- `tests/execution/test_frame_index_contract.py::test_run_context_rejects_access_or_replacement_before_child_start_acknowledgement`
- `tests/execution/engine/test_runtime_boundaries.py::test_family_driver_projects_an_acknowledged_aborted_child`
- `tests/execution/test_graph_api.py::test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails`
- `tests/execution/test_graph_api.py::test_normal_resume_never_mutates_the_input_continuation_snapshot`

### R7：A2 不是可复现的 case-level evidence

第 8 节只列文件和行为族，没有给出每个 case 的 exact nodeid、输入构造、完整错误文本、`__cause__`、mutation assertion 和失败条件；第 6 节的 identity/error matrix 也只是未来要“保存”的字段，不是当前 baseline evidence。requirements 的单项准入要求是 case-level evidence，文件级列表不能替代它。

A0/A2 至少应建立如下映射（不新增永久 private-source-shape/AST gate）：

| 适用保持义务 | A 触及面 | 必须固定的可观察证据 |
| --- | --- | --- |
| `GSP-P03` | commit confirmation、context replacement、frame installation 顺序 | root/child fence、resume、partial-prefix 和 installation failure 的 exact nodeid 与 mutation/order assertion |
| `GSP-P04` | continuation root/child snapshot、frame/index integrity、Result projection | malformed/duplicate/tamper、共享 continuation 不变、root→child ordering 的 exact nodeid |
| `GSP-P07` | nested child identity、repeated superstep、canonical child order | child start/replacement、parent mismatch、repeated activation 的 success/failure matrix |
| `GSP-P08` | 唯一 execution owner、strict typing、module-scope imports | 既有 architecture/source-discipline nodeid 与 actual diff/source review |
| `GSP-P01`（仅当 public error surface 被触及） | public Graph 观察到的错误分类 | exact public `Graph.run()` case；若不触及则明确排除 |

本单元不修改 State/command/protocol，也不设计跨进程恢复能力；对应 durable/存储范围应明确排除，而不是在 A 的 target 中扩写。

### R8：A 文档混入后置候选和过宽 manifest

本轮用户要求先处理 A。当前文件仍包含完整的候选 B 设计、B 的 probe/停止条件和 `EX-B0` 阶段；这会让 A 的实施文档同时拥有两个 target 目录。应把 B 保留在研究文档，A 文档只保留 A0/A1/A2 及其 disposition。

同样，`engine/recovery.py` 只应在 A0 证明存在必要的 typed projection 时进入 actual manifest；不能因为“可能需要”提前列入。A 不改变 recovery 算法、State 边界或任何存储能力；相关代码若不被实际 diff 触及，就不应出现在 manifest。

## 5. A 的唯一安全推进方式

### 5.1 EX-A0：只读、可复核、不可授权 production

A0 必须由一个 owner 文档/record 生成，且只做以下事情：

1. 固定 A 的 canonical owner、requirements ID 和唯一 target 文档路径；
2. 用 `definition / production consumer / test-only consumer / fixture` 四类标记列出实际 producer、consumer、replacement、snapshot 和 projection；
3. 画出 new run、state-only run、continuation admission、root/child resume、nested child start、repeated superstep 的 actual call chain，标出每个 state snapshot 的产生、消费、失效和 mutation 边界；
4. 记录 root/child lookup、child start/replacement、duplicate/unknown、parent mismatch、run ID/revision tamper 和 malformed continuation 的首错 type/text/cause 与 mutation-free 结果；
5. 逐指标记录 full scan、sort、allocation、lookup、definition、field、branch、import、forwarding property 及 health candidate 的 before→after；
6. 明确判定：若不能证明删除面大于新增面，输出 `KEEP / NO IMPLEMENTATION`，不继续写 A1 target。

A0 不得修改 production、behavior tests、State、Store、协议、持久化或 failover，也不得创建兼容层或永久 source-shape gate。

### 5.2 EX-A1：只有批准后才可设计原子实现

若且仅若 A0、独立技术评审和 requirements 单项批准全部通过，才可以重新写 A1。届时：

- exact target 只能有一个 owner；planned 与 actual manifest 分开，actual manifest 以最终 diff 为准；
- producer、consumer、fixture、无用 import 和旧路径在同一 change unit 中删除；不保留 alias/property/双写；
- `GraphRunContext`、planned state、continuation 和 recovery 各自保持 nominal boundary；不把它们合成宽 record；
- commit confirmation、context state replacement、frame installation 的顺序和异常边界按现有 behavior 保持；
- 不修改 State/Store/协议/持久化/failover，不新增 runner、cache 或 public export。

### 5.3 EX-A2：只验收行为，不冻结 private 源码形状

A2 必须以现有 behavior/architecture gates 和 exact public/owner behavior cases 验收。一次性 source review 可以记录删除面和实际调用图，但不得把具体 private class 名、局部变量、loop 次数或源码布局写成永久 gate。任一错误 type/text/cause、首错顺序、snapshot mutation 或 complexity metric 回退，A2 失败并保持当前 baseline。

## 6. 当前 disposition

```text
EX-A0  = ADMITTED AS DOCS-ONLY PROBE, EVIDENCE NOT YET CLOSED
EX-A1  = NOT APPROVED
EX-A2  = NOT APPROVED
production / behavior tests = NO CHANGE AUTHORIZED
GraphRunContext / planned / continuation / recovery nominal boundaries = KEEP
State / Store / protocol / persistence / failover = HARD KEEP / OUT OF SCOPE
```

最小整改不是先实现 `ScopedStateIndex`，而是先完成 A0 并回答一个问题：**在不新增第二 owner、第二事实源或结构负债的前提下，A 到底删除了哪一段真实代码？**答不出就保持现状；保持现状是合格的零负债结论。

## 7. 本轮验证记录

在当前工作树（未修改 production/tests）执行：

- `python -m pytest -q tests/execution/test_frame_index_contract.py tests/execution/test_continuation_integrity.py tests/execution/engine/test_runtime_boundaries.py tests/execution/test_graph_api.py` → **158 passed**
- `pyright` → **0 errors, 0 warnings, 0 informations**
- `python -m ruff check src/mote_kernel/execution/run_context.py src/mote_kernel/execution/invocation.py src/mote_kernel/execution/family_driver.py src/mote_kernel/execution/facade.py` → **passed**
- `python -m ruff format --check ...`（同四个文件）→ **4 files already formatted**
- `python -m pytest -q tests/architecture/test_graph_execution_ownership.py tests/architecture/test_source_discipline.py` → **27 passed**
- `python -m pytest -q tests/architecture/test_complexity_gate.py` → **9 passed**
- `python -m tests.architecture.complexity_rules --check-health` → **reviewed=51 / unreviewed=0 / stale=0**
- `make check` → **通过**（843 tests passed，100% coverage，build 和 twine check 通过）
- `pre-commit run --files mote-kernel/docs/graph-execution-code-simplification-implementation-review.zh-CN.md mote-kernel/docs/graph-execution-code-simplification-implementation.zh-CN.md` → **通过**

monorepo root 的 `pre-commit run --all-files` 返回 **1**：唯一失败是既有
`mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed` 被
`end-of-file-fixer` 改写；其余 hooks 均通过。该文件在运行前未出现在工作树变更中，已恢复到运行前内容；本评审没有把全量 pre-commit 的失败写成通过，也没有保留 hook 的越界修改。

**最终裁决：A 先做 A0 证据闭合；当前不批准 A1/A2，不修改 production。**
