# S12 Recovery admitted-action 事实归一化实施方案

## 1. 文档信息

- 状态：`IMPLEMENTED / VERIFIED / IMPLEMENTATION-OWNER WRITEBACK CONTENT COMPLETE / UNCOMMITTED BY REQUEST`
- 日期：2026-08-24
- 单元：Graph 语义保持型简化 S12（P2）
- 源码基线：Git `35e7c95206e4124be68a9706359d1cc129e98c17`
- 实施提交：Git `269ffaa6fe101164c0055f8426a72b761135d393`
- 基线文件 SHA256：
  - `src/mote_kernel/execution/engine/recovery.py`：`f88b6fc68b7677d227acc438c962fce8164815e55a46d448ec44d20bd02d9fba`
  - `src/mote_kernel/execution/invocation.py`：`043a5b3da9f016c4b8193116a3212775e16fa23538b47b803ba1a55c4540249a`
- 当前阶段：七文件 production + behavior + normative implementation 已提交并通过适用门禁；implementation-owner writeback
  与两个 current-status 索引同步的内容已完成，并按用户要求保持未暂存、未提交
- State/持久化边界：HARD KEEP；不修改 `GraphRunState`、command、reducer、revision、commit、protocol、Store 或 persistence
- Complexity/ratchet：automated complexity gate、baseline、ratchet、`pyproject.toml` 锁值均不属于 S12 准入或交付证据
- Legacy/private-shape gate：用户已明确排除；这不排除本文列明的current behavior、strict typing与active owner/dependency checks

关联 owner：

- [Graph 语义保持型简化 requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)：唯一拥有
  `GSP-A06` 批准状态；已批准本文设计 SHA256
  `1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7` 对应的 exact target。
- [Graph 语义保持型简化主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)：只保留
  S12 目录级索引和阶段顺序，不再复制本单元 exact target。
- [Graph Node I/O normative implementation](graph-node-input-output-contract-implementation.zh-CN.md)：唯一拥有当前
  recovery equality、availability、action 和 generic shape；只在 S12 获批后的 implementation unit 中与 production 原子同步。
- [S12 首轮独立技术评审](graph-semantics-preserving-simplification-s12-implementation-review.zh-CN.md)：只拥有首轮
  `CHANGES REQUESTED` 裁决和审计证据，不拥有当前 target。
- [S12 首轮评审回复](graph-semantics-preserving-simplification-s12-implementation-review-response.zh-CN.md)：只记录
  owner 对各 review item 的接受/拒绝及理由，不复制本文 exact target。
- [S12 第二次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-second-review.zh-CN.md)：只拥有
  第二次 `CHANGES REQUESTED` 裁决和 R5--R8 审计证据，不拥有当前 target。
- [S12 第二次评审回复](graph-semantics-preserving-simplification-s12-implementation-second-review-response.zh-CN.md)：只记录
  owner 对 R5--R8 的 disposition，不复制本文 exact target。
- [S12 第三次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-third-review.zh-CN.md)：只拥有
  第三次 `CHANGES REQUESTED` 裁决和 R8/R9 审计证据，不拥有当前 target。
- [S12 第三次评审回复](graph-semantics-preserving-simplification-s12-implementation-third-review-response.zh-CN.md)：只记录
  owner 对第三次 R8/R9 的 disposition，不复制本文 exact target。
- [S12 第四次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-fourth-review.zh-CN.md)：只拥有设计
  `PASS / READY FOR REQUIREMENTS OWNER APPROVAL` 裁决，不拥有 target 或批准状态。
- [S12 首次实施验收](graph-semantics-preserving-simplification-s12-implementation-acceptance.zh-CN.md)：保留首次
  `CHANGES REQUESTED` 的历史审计证据。
- [S12 二次实施验收](graph-semantics-preserving-simplification-s12-implementation-second-acceptance.zh-CN.md)：确认七文件代码、
  materialization ledger、export boundary 和适用门禁通过，并把本 owner writeback 识别为唯一剩余交付阻断。

本文是 S12 target shape、valid-domain equality、action ↔ availability invariant、malformed seed 裁决、generic migration、
behavior evidence、原子 manifest 和停止条件的**唯一 owner**。未来 review/response 只记录裁决，不复制 target；requirements
只记录批准状态，不复制这里的算法和 shape。

## 2. 结论

S12 的合理目标不是机械删除一个 private field，也不是为了减少改动而把与删除事实直接相关的 malformed seed 排除在外。目标必须同时完成：

1. `RecoveryTransferState.availability.resume_inputs` 成为 resume-input presence 的唯一事实；
2. 删除 `AdmittedResumeFact.resume_input_availability`；
3. 删除由该字段产生的 `AdmittedResumeFact` 与 `_RecoveryFamily` phantom generic；
4. 把runtime、executor、recovery这组resume-input consumer使用的node-materialization lookup/error mapping与coordinate构造分别
   收口到一个narrow typed query和一个pure constructor，同时保持continuation validator与routing各自既有读取owner；
5. 把现有 compiled scope traversal 从 invocation-local function 提升为两个真实 consumer 共用的 topology-owned pure typed query；
6. 在 `preflight_recovery()` 现有 seed validation 边界 fail closed，证明每个 non-skip action 都有 exact admitted
   resume-input availability；
7. 保持 equality/hash、seen、traversal order、reachable boundary、4096 budget、异常 precedence 和 public behavior。

该方案新增一条必要的 malformed invariant 检查，但不新增任何 stored fact、DTO、cache、index、compatibility alias、执行路径或
永久 source-shape gate。它通过收口 owner 消除现有重复 coordinate construction，而不是用“生产链通常不会错”代替验证。

S12 已经第四次独立技术评审通过并由 requirements owner 明确批准；批准的 exact target 已在 commit
`269ffaa6fe101164c0055f8426a72b761135d393` 中实施。本文本次变化只记录实际交付，不改变批准 target、production、tests、
State/持久化或 Graph/Kernel failover 边界。

### 2.1 Graph、Kernel failover 与 Port 边界

S12 只简化 Graph execution 已有的显式 resume/recovery 机制，不建立 failover：

- `Graph.run()` 只执行调用方显式提交且通过 admission 的 resume/interrupt/skip action，不自行决定是否重试、何时重试、
  退避、最大次数或错误分类；
- failover 策略由 Kernel 在 Graph 之外通过 narrow typed Port 统一装配；S12 不定义、实现、缓存或持久化该 Port，也不把
  failover 决策写入 `CompiledGraph`、State、continuation 或 recovery transfer state；
- `CompiledGraph` 继续只拥有 immutable topology/materialization facts；本方案中的 compiled-scope traversal 只是对这些事实的
  纯查询，不是 failover policy、runner 或第二执行路径；
- `SnapshotMismatchError` 只表示显式 action/snapshot 与 compiled facts 不一致并在 execution admission 边界 fail closed，
  不表示 Graph 选择了任何 failover 行为。

该边界与“不实现持久化”相互独立且同时 HARD KEEP。未来 Kernel failover Port 或持久 Store 都必须另立需求，不能借 S12
顺带建立接口、占位 DTO、registry、默认实现或 compatibility path。

### 2.2 `GSP-P01`–`GSP-P08` applicability 与 evidence

本表只引用 requirements ID 并登记 S12 的落实责任，不复制 requirements 规范正文：

| Requirement | S12 裁决 | Exact target / evidence 责任 |
| --- | --- | --- |
| `GSP-P01` | 适用 | `Graph` facade、overload、public error taxonomy 不变；五字段 action 去泛型后由 strict Pyright 与 public behavior cases 证明类型面不变 |
| `GSP-P02` | 不触及 / HARD KEEP | `GraphRunState`、command/reducer、revision、protocol、Store 与 persistence 均不进入 manifest；以 changed-file manifest 和一次性 source review 提供 negative evidence，不新增持久化实现 |
| `GSP-P03` | 适用 | 新 seed invariant 位于 family/proof 以及任何 fence、commit、claim、child start、node execution 前；用 exact exception precedence 与既有 commit-boundary behavior 证明 |
| `GSP-P04` | 适用 | continuation/frame 的 exact scope-run、activation、descriptor 与 concrete-value 隔离保持；复跑 continuation integrity 与 frame behavior cases |
| `GSP-P05` | 适用 | failure/interrupt/skip action、settlement、resume-input availability 与 route facts保持；skip 不被错误要求 current resume input |
| `GSP-P06` | 适用，核心 | valid-domain equality/hash、malformed boundary、seen、traversal、reachable boundary 与 4096 budget 按第 5–8 节逐项证明 |
| `GSP-P07` | 适用 | nested scope 纯查询、binding/action canonical order、child recovery 与 repeated scope identity保持；unknown scope case固定原错误边界 |
| `GSP-P08` | 适用 | compiled map是唯一事实；resume-input runtime/executor/recovery lookup/error query、continuation integrity读取、routing binding读取和coordinate constructor各有不重叠owner；recovery不直接解释materializations；phantom generic原子删除，依赖方向与strict typing保持，不新增第二runner/store/failover owner |

`GSP-P02` 的“不触及”不是放弃保持义务：任何 State/Store/protocol/persistence diff 都直接触发停止条件。以上矩阵、
第 8 节 exact nodeid、结构账本和第 10.4 节 manifest 共同构成 S12 的 `GSP-A06` case-level evidence；任一项不能由文件级
概述替代。

## 3. 实施后 production 审计

### 3.1 实际数据流与 owner

commit `269ffaa6fe101164c0055f8426a72b761135d393` 后的唯一 production 调用链为：

```text
GraphExecutor.resume()
  ├─ non-skip action：调用一次 _require_node_materialization()
  │    └─ 由 _resume_input_coordinate() 构造 exact coordinate 并生成 AdmittedResumeInput
  └─ skip action：不查询 materialization，也不生成 current resume-input frame
        ↓
invocation.plan_resumes()
  ├─ 将每个 AdmittedResumeInput 加入 CandidateFrameAvailability.confirmed
  └─ _resume_facts() 只按 canonical action order 投影五字段 AdmittedResumeFact
        ↓
invocation.recovery_seed()
        ↓
recovery.preflight_recovery()
  ├─ RecoveryAvailabilityCoordinates.from_frames() 投影完整 resume_inputs
  ├─ 对每个 non-skip action 依次复用 scope query、materialization query 和 coordinate constructor
  ├─ exact coordinate 缺失或 descriptor 不匹配时在 family/proof 前 fail closed
  └─ skip 绕过 current-input invariant，历史 resume coordinate 原样保留
```

`RecoveryAvailabilityCoordinates.resume_inputs` 现在是 resume-input presence 的唯一 equality/hash 事实。
`AdmittedResumeFact` 只保存 target、action kind、interrupt ID、skip reason 与 concrete route；`_resume_facts()` 不再读取
`PreparedResume.inputs`，也不再复制 coordinate。

Compiled materialization 的 production direct read 固定为三处且语义不重叠：

1. `engine/resume_input.py::_require_node_materialization()`：runtime/executor/recovery consumer 集合的唯一 lookup/error owner；
2. `invocation.py::_validate_frame_index()`：continuation coordinate/frame integrity owner；
3. `engine/routing.py::resolve_routing_facts()`：binding/readiness interpretation owner。

Recovery 不直接读取 `transition.materializations`。`_require_node_materialization()` 在 source inventory 中恰为七处：一个
definition 加 `_admit_override()`、`node_inputs_available()`、`pending_node_input_available()`、`materialize_node_input()`、
`GraphExecutor.resume()`、`preflight_recovery()` 六个 consumer。`ResumeInputAvailabilityCoordinate(...)` 的 production constructor
只剩 `engine/resume_input.py::_resume_input_coordinate()` 一处。

Compiled scope traversal 也只有 topology-owned `_compiled_graph_at_scope()`；invocation 全部既有 consumer 与 recovery invariant
复用它，invocation-local `_compiled_at()` 已删除，没有 forwarding alias、family map、cache 或第二 traversal。

内部下划线 owner 通过各自 module-local `__all__` 供同一 execution 包内 strict-Pyright 消费；它们没有从
`mote_kernel.execution` 重导出，包外唯一公共 facade 仍为 `Graph`。Production 中没有
`# pyright: ignore[reportPrivateUsage]`。

### 3.2 泛型闭合

实施后的 shape 为：

```text
AdmittedResumeFact
  └─ 五个非泛型 action semantic fields

_RecoveryFamily
  └─ tuple[AdmittedResumeFact, ...]

RecoveryTransferState[GraphValueT]
  └─ RecoveryAvailabilityCoordinates[GraphValueT]

RecoveryInvocationSeed[GraphValueT]
  └─ ScopedFrameIndex[GraphValueT] | CandidateFrameAvailability[GraphValueT]
```

`AdmittedResumeFact` 与 `_RecoveryFamily` 的 phantom generic 及全部 production subscription 已删除；真实承载 frame/availability
类型关系的 transfer state、seed、availability 和 frame types 继续泛型。迁移没有使用 `Any`、bare generic、cast、type alias 或
`compare=False` 隐藏类型关系。

### 3.3 Malformed 边界与异常优先级

`preflight_recovery()` 的实际验证顺序为：

```text
root coordinate
→ binding uniqueness/canonical order
→ action uniqueness/canonical order
→ action scope/superstep/frontier/settlement/facts
→ frame availability projection及publication-coordinate uniqueness
→ unknown nested scope
→ unknown materialization node
→ missing/wrong exact resume-input coordinate
→ family construction / recovery proof
```

每个 non-skip action 在任何 fence、commit、claim、child start 或 node execution 前完成一次 exact membership 检查。Missing 与
same-activation/wrong-descriptor 统一抛
`SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")`；unknown scope、unknown
materialization 和 duplicate publication 保持各自固定错误及上述 precedence。检查不读取 concrete frame value，不建立第二
availability index，也不进入 proof successor loop。

### 3.4 实际验证结果

二次实施验收在 commit 前的同一七文件内容上记录，commit 后 manifest 与内容统计一致：

```text
tests/execution/engine/test_recovery_identity.py
→ 20 passed

tests/execution
→ 563 passed

tests（排除 complexity）
→ 826 passed, 7 deselected

tests/architecture（排除 complexity）
→ 56 passed, 7 deselected

pyright
→ 0 errors, 0 warnings, 0 informations

ruff check / ruff format --check（六个 changed Python files）
→ passed

python -B -m build --no-isolation
python -B -m twine check dist/*
→ sdist/wheel built；both artifacts PASSED

SKIP=kernel-complexity pre-commit run --files <seven-file manifest>
→ passed

git diff --check -- <seven-file manifest>
→ passed
```

Automated complexity 和 legacy/private-source-shape gates 按用户授权排除。完整 monorepo pre-commit / 全工作树
`git diff --check` 受 S12 manifest 外的 Cloudflare 文件 EOF/只读工作树问题影响，未冒记为通过；这不改变七文件 scoped evidence。

## 4. Exact target shape

### 4.1 唯一 compiled-scope owner

把 `invocation.py::_compiled_at()` 原样迁移到 compiled topology owner，exact signature 固定为：

```python
def _compiled_graph_at_scope(
    root: CompiledGraph[GraphValueT],
    scope: DefinitionScope,
) -> CompiledGraph[GraphValueT]: ...
```

规则：

- 实现只按 `scope` 原始 segment 顺序遍历 `CompiledGraph.nested_graphs`；
- unknown segment 继续抛 `SnapshotMismatchError("scope references unknown nested node ...")`；
- 不建立 family map、cache、第二 nested index 或 path normalization；
- 不加入 package export，仍是 execution owner-internal pure typed query；它不是 Kernel failover Port；
- invocation 的全部现有 consumer 和 recovery 新增的 invariant consumer 都调用该函数；
- 删除 invocation-local `_compiled_at()`，不保留 forwarding alias。

这是一个函数的 owner 搬迁，不是新增 helper 数量。它把已经存在、现在有两个 production consumer 的同一 scope 解释收口为
唯一事实。

### 4.2 Resume-input materialization typed query 与唯一 coordinate constructor

`engine/resume_input.py` 同时拥有一个窄typed lookup/error query和既有pure coordinate constructor：

```python
def _require_node_materialization(
    graph: CompiledGraph[GraphValueT],
    node_id: GraphNodeId,
) -> MaterializationPlan[GraphValueT]: ...
```

该query的唯一算法固定为：

```python
plan = graph.transition.materializations.get(node_id)
if plan is None:
    raise SnapshotMismatchError("node input references an unknown compiled materialization")
return plan
```

Coordinate constructor保持：

```python
def _resume_input_coordinate(
    activation: StableActivation,
    plan: MaterializationPlan[GraphValueT],
) -> ResumeInputAvailabilityCoordinate[GraphValueT]: ...
```

唯一算法为：

```text
return ResumeInputAvailabilityCoordinate(activation, plan.descriptor.identity)
```

边界要求：

- `CompiledGraph.transition.materializations` 继续是authoritative compiled materialization fact；只有
  `engine/resume_input.py`中的该query负责resume-input runtime/executor/recovery consumer集合的lookup与unknown-node error mapping；
- `_admit_override()`、`node_inputs_available()`、`pending_node_input_available()`、`materialize_node_input()`、
  `GraphExecutor.resume()`和`preflight_recovery()`全部复用该query；它有六个真实production consumers，不是single-use thin helper；
- 每个直接调用边界只通过query取得一次`MaterializationPlan`，再按需调用上述唯一constructor；
- `pending_node_input_available()`与`materialize_node_input()`不因coordinate构造再增加第二lookup；
- `GraphExecutor.resume()`对non-skip action取得plan并调用constructor；skip action不查找resume materialization；
- `preflight_recovery()`解析exact scoped graph后只调用query；recovery不得直接读取`transition.materializations`，不得复制
  unknown-node mapping、bindings/declarations或input interpreter；
- `pending_node_input_available()`、`materialize_node_input()`、`GraphExecutor.resume()` 和 `preflight_recovery()` 全部复用
  同一 constructor，不得直接构造 `ResumeInputAvailabilityCoordinate(...)`；
- `GraphExecutor.resume()` 删除自己的 direct `ResumeInputAvailabilityCoordinate(...)` construction 和只为它存在的
  `descriptor` local；
- `invocation.py::_validate_frame_index()`继续直接执行continuation-specific `.get()`：它同时校验materialization存在性、descriptor、
  superstep和concrete frame，并统一抛`"continuation resume input has inconsistent coordinates"`；不得改用本query而改变其错误契约；
- `engine/routing.py::resolve_routing_facts()`继续直接读取plan bindings：它是routing binding/readiness owner，不是resume-input
  coordinate/error consumer；
- 上述两个unchanged read与本query都只读取同一个immutable compiled map，不建立第二事实；不得把query宣称为全仓global accessor；
- 不新增第二resume-input lookup query、overload、cache、index、DTO、context bag、descriptor-only constructor或forwarding alias。

这里刻意不采用 `_resume_input_coordinate(graph, activation)`：该签名会让仍需消费 `MaterializationPlan.bindings` 的
`materialize_node_input()`对线性`FrozenMap`重复查找同一node。该query不是architecture capability Port，更不是failover Port：它只把
resume-input runtime/executor/recovery对compiled materialization的读取和typed admission error收口；不选择retry、backoff或任何
failover action。唯一事实仍是compiled plan；该consumer集合的唯一lookup/error owner是
`_require_node_materialization()`，唯一coordinate construction是`_resume_input_coordinate(activation, plan)`；continuation与routing
保留各自不重叠的validation/interpretation owner。

### 4.3 `AdmittedResumeFact` 与 generic target

Exact dataclass shape 固定为：

```python
@dataclass(frozen=True, slots=True)
class AdmittedResumeFact:
    target: StableActivation
    action: AdmittedActionKind
    interrupt_id: GraphInterruptId | None
    skip_reason: str | None
    concrete_route: GraphRouteId | None
```

以下 annotation 必须原子迁移：

```python
@dataclass(frozen=True, slots=True)
class RecoveryTransferState(Generic[GraphValueT]):
    ...
    availability: RecoveryAvailabilityCoordinates[GraphValueT]
    ...
    admitted_actions: tuple[AdmittedResumeFact, ...]

@dataclass(frozen=True, slots=True)
class RecoveryInvocationSeed(Generic[GraphValueT]):
    ...
    frames: ScopedFrameIndex[GraphValueT] | CandidateFrameAvailability[GraphValueT]
    ...
    admitted_actions: tuple[AdmittedResumeFact, ...] = ()

@dataclass(frozen=True, slots=True)
class _RecoveryFamily:
    bindings: tuple[RecoveryStateBinding, ...]
    limits: ExecutionLimits
    admitted_actions: tuple[AdmittedResumeFact, ...]
    budget: _RecoveryProofBudget
```

所有 `_RecoveryFamily[GraphValueT]`、`AdmittedResumeFact[GraphValueT]`、test annotation 和 invocation collection annotation
必须同步移除 subscription。`RecoveryTransferState[GraphValueT]`、`RecoveryInvocationSeed[GraphValueT]`、
`RecoveryAvailabilityCoordinates[GraphValueT]`、frame types 和 boundary types保持泛型。

禁止用 `Any`、bare generic、cast、phantom TypeVar、`compare=False` 或 type alias 隐藏迁移。

### 4.4 `_resume_facts()` target

Exact signature 固定为：

```python
def _resume_facts(
    scope_run: ScopeRunCoordinate,
    superstep: int,
    actions: tuple[ResumeNodeRequest[GraphValueT], ...],
) -> tuple[AdmittedResumeFact, ...]: ...
```

删除 `prepared: PreparedResume[GraphValueT]` 参数、逐 action 的 `next(... for item in prepared.inputs ...)` scan、`admitted`
local 和全部 constructor 第六实参。函数仍只按 action 原始 canonical order 一对一投影：

- `ResumeFailedNodeRequest + OverrideNodeInput` → `RESUME_FAILED_WITH`；
- `ResumeFailedNodeRequest + UseMaterializedInput` → `RESUME_FAILED`；
- `ResumeInterruptedNodeRequest` → `RESUME_INTERRUPTED` + exact interrupt ID；
- `SkipFailedNodeRequest` → `SKIP_FAILED` + reason + optional concrete route。

它不读取 frames、compiled graph 或 availability；action ↔ availability 的跨 owner invariant 只在
`preflight_recovery()` seed boundary 验证一次。

### 4.5 `preflight_recovery()` malformed invariant 与错误 precedence

保留现有 public/internal signature和前述验证顺序。所有现有 action binding/settlement/fact validation 完成后，仍按当前时点调用
`RecoveryAvailabilityCoordinates.from_frames(seed.frames)`；随后新增唯一 invariant loop：

```python
for action in seed.admitted_actions:
    if action.action is AdmittedActionKind.SKIP_FAILED:
        continue
    scoped_graph = _compiled_graph_at_scope(graph, action.target.scope_run.scope)
    plan = _require_node_materialization(scoped_graph, action.target.node_id)
    expected = _resume_input_coordinate(action.target, plan)
    if not availability.has_resume_input(expected):
        raise SnapshotMismatchError(
            "recovery admitted resume action lacks its exact resume-input availability"
        )
```

然后才构造 `_RecoveryFamily` 并进入 `_prove_scope()`。该 loop：

- 不读取 concrete frame value；
- 不构造第二 availability tuple/set/map；
- 不改变 action order；
- 每个 non-skip action只做一次 compiled materialization lookup和一次对完整 `resume_inputs` tuple的 exact membership；
- 不把 skip 与历史 resume availability错误绑定；
- 不要求 availability 中所有历史 resume coordinates 都属于本次 action；
- 对每个 current non-skip action 只验证 canonical owner推导的 exact coordinate presence；
- 在任何 fence、resume commit、claim、child start 或 node execution 前 fail closed。

`preflight_recovery()`的输入前提是public `Graph.run()`链已经完成state、continuation record nominal type、coordinate、descriptor和
concrete frame admission。它不复制`validate_context()`/`_validate_frame_index()`。直接forged private seed只对本函数已经拥有的
root/binding/action checks、publication-coordinate uniqueness projection和本次新增invariant建立契约。

Target exception precedence固定为：

```text
root coordinate
→ binding uniqueness/canonical order
→ action uniqueness/canonical order
→ action scope/superstep/frontier/settlement/facts
→ frame availability projection及其既有publication-coordinate uniqueness check
→ unknown nested scope
→ unknown materialization node（由shared resume-input materialization query裁决）
→ missing/wrong exact resume-input coordinate
→ family construction / recovery proof
```

若一个 forged seed同时违反多项约束，只观察上述最早错误；S12 不改变排在新增 invariant之前的既有 exact type/text。
Unknown nested scope继续由共享 compiled-scope query抛
`SnapshotMismatchError("scope references unknown nested node <segment!r>")`；unknown materialization和missing/wrong coordinate
分别使用`SnapshotMismatchError("node input references an unknown compiled materialization")`和本节missing文本，不泄漏
`KeyError`。

## 5. Action ↔ availability 完整证明

### 5.1 Valid domain

S12 valid domain 是现有 public `Graph.run()` 调用经以下全部 owner admission 后形成的 recovery seed：

1. `GraphExecutor.resume()` 已验证 action variant、frontier settlement、interrupt ID、input codec 和 concrete frame；
2. 每个 `ResumeFailedNodeRequest` 和 `ResumeInterruptedNodeRequest` 恰好追加一个 `AdmittedResumeInput`；
3. 每个 `SkipFailedNodeRequest` 不追加 resume-input record；
4. action 已按 `(scope, node_id)` canonical 且 distinct；每个 scoped executor 内又按 node ID canonical 且 distinct；
5. 唯一 `_resume_input_coordinate()` 用 exact scoped `StableActivation` 与 compiled materialization descriptor 构造 coordinate；
6. `plan_resumes()` 在形成 seed 前把全部 admitted resume inputs通过 `ScopedFrameIndex.add_resume_input()` 加入 candidate
   frames；duplicate exact coordinate fail closed；
7. continuation/frame admission 已验证 record nominal type、scope、activation、descriptor 和 concrete frame shape；
8. `RecoveryAvailabilityCoordinates.from_frames()` 只投影已 admission record 的 exact coordinates，并保留既有publication-coordinate
   uniqueness check；它不承担record nominal type、scope、descriptor或concrete value validation。

令 `A` 为本 invocation 的 non-skip admitted actions，令 `R_current` 为这些 action 在同一次 `GraphExecutor.resume()` 中产生的
resume-input records。生产构造链建立双射：

```text
f: A → R_current
f(action).coordinate
  = _resume_input_coordinate(action.target, materialization(root, action.target))

materialization(root, action.target)
  = _require_node_materialization(
      _compiled_graph_at_scope(root, action.target.scope_run.scope),
      action.target.node_id,
    )
```

action target distinct 保证 injective；executor 对每个 non-skip action恰好追加一个 record 保证 surjective。完整 availability
可以额外包含前序 invocation 的历史 resume coordinates，因此不错误声称 `A` 与全部 `availability.resume_inputs` 全局等长。

### 5.2 Malformed seed 裁决

S12 不把本次删除直接产生的 action ↔ availability malformed 类别悄然排除；其他 forged-private-input类别继续由既有
continuation/frame/action owners裁决，不借S12扩大支持域。与本单元相关的目标行为固定为：

| malformed 类别 | target 行为 |
| --- | --- |
| invalid root/binding order/duplicate binding | 保持当前 `SnapshotMismatchError` type/text/precedence |
| duplicate/noncanonical action target | 保持当前 `SnapshotMismatchError` type/text/precedence |
| action scope/superstep/frontier target 不匹配 | 保持当前 fail-closed 行为 |
| skip settlement/reason/route 不匹配 | 保持当前 fail-closed 行为 |
| resume action 对应非 Pending settlement | 保持当前 fail-closed 行为 |
| non-skip action 缺少 exact resume coordinate | 新增上述 typed `SnapshotMismatchError`，早于 proof/mutation |
| coordinate activation 对但 descriptor 错 | canonical owner推导的 exact coordinate membership失败，同一错误 |
| unknown nested scope | 在frame projection后、proof前抛`SnapshotMismatchError("scope references unknown nested node <segment!r>")` |
| unknown materialization node | 对compiled/state均已知且settlement为Pending的non-skip node，只forge compiled materialization map缺项；shared query在scope解析后、coordinate membership前抛`SnapshotMismatchError("node input references an unknown compiled materialization")`，recovery不读取map且不泄漏`KeyError` |
| skip action 与历史 resume coordinate 同时存在 | 不误拒绝；skip 不声明本 invocation resume-input requirement |
| concrete frame malformed | 继续由 continuation/executor frame admission owner拒绝；recovery 不复制 value interpreter |

Unknown-materialization forged case只证明non-skip新增query的typed failure。Compiler产出的valid `CompiledGraph`保证每个node拥有
materialization；skip不消费node input，因此skip对“compiled map被私下forge为缺项”的行为不属于S12支持域，也不保留baseline偶然
`KeyError`。S12不会为该非法组合恢复无意义lookup、增加全图validator或新增forged-skip gate；valid skip及“skip + historical
coordinate”行为仍必须通过现有与target behavior cases。

该变化是 owner-internal malformed boundary tightening，不扩大 public API，也不把非法 seed 或非法 compiled topology变成受支持输入。

## 6. Valid-domain equality、hash、traversal 与 budget 证明

### 6.1 投影

对任一 valid old action：

```text
π_action(old) = (target, action, interrupt_id, skip_reason, concrete_route)
```

对任一 valid old transfer state，`π_state` 只对每个 admitted action应用 `π_action`，其余 control、limits、live、availability、
children、invocation-new-children 全部逐字段保持。

### 6.2 Equality/hash 等价

对 valid-domain old states `x`、`y`：

```text
x == y  ⇔  π_state(x) == π_state(y)
```

证明：

- 正向显然成立：删除相等对象的同一字段后剩余字段仍相等；
- 反向中，new state equality 已包含完整 `availability.resume_inputs` 和 action 剩余五字段；
- 对 non-skip action，删除字段由 action target + shared compiled coordinate owner + availability presence唯一确定；
- 对 skip action，删除字段按构造不承载 resume coordinate；skip reason/route 仍在 action equality；
- 因而 valid old state 中不存在 remaining fields 全同但 deleted field 可独立变化的自由度。

Python frozen dataclass hash 与 equality 使用相同剩余字段，因此 equality partition 与 seen membership保持。这里不承诺 old/new
整数 hash 值逐位相同；要求且证明的是相等关系、集合基数和去重结果相同。

### 6.3 Seen、reachable boundary 和 4096 budget

- 每次 `preflight_recovery()` 只建立一个 `_RecoveryFamily`；root 与所有 recursive child proof共享同一 admitted-actions tuple；
- `_transfer_state()` 对本次 proof 的所有 state 注入同一 tuple；
- `seen` 是每次 `_prove_scope()` invocation-local set，不跨 seed/invocation 缓存；
- successor generation、routing、availability transfer、child combination 和 boundary projection不读取删除字段；
- valid-domain equality partition不变，所以每个 scope 的 seen admission序列和 distinct-state count不变；
- `_RecoveryProofBudget.admit()` 的调用位置与 successor count不变，因此 4096 boundary不变；
- returned boundary 的 control、limits、availability、action facts 和 canonical order不变。

### 6.4 Traversal ordering

`recovery_traversal_key()` 当前已经只包含 action target/kind/interrupt/reason/route，不包含删除字段。target 不修改该 projection。
因此 pending heap key、tie sequence、visit order和 boundary sort order逐项保持。Traversal key仍只排序，不能成为 seen key。

## 7. 结构净删除账本

Automated complexity gate不属于 S12；本表是批准 target 与 commit `269ffaa` actual diff 的共同结构证据。Actual 已逐项达到：

| 对象 | baseline | target / actual | 净变化 |
| --- | ---: | ---: | ---: |
| `AdmittedResumeFact` stored fields | 6 | 5 | -1 |
| `AdmittedResumeFact` phantom generic base | 1 | 0 | -1 |
| `_RecoveryFamily` phantom generic base | 1 | 0 | -1 |
| `AdmittedResumeFact[GraphValueT]` / `_RecoveryFamily[GraphValueT]` production subscriptions | 16 | 0 | -16 |
| `_resume_facts()` parameters | 4 | 3 | -1 |
| `_resume_facts()` per-action scan over `PreparedResume.inputs` | 1 | 0 | -1 |
| preflight exact resume availability membership | 0 | 每个 non-skip action 对完整 `resume_inputs` tuple 1 次 | +1 必要 malformed invariant，明确不是 scan 归零 |
| executor direct resume-coordinate constructors | 2 | 0 | -2 |
| `engine/resume_input.py` direct `materializations` access sites | 4 | 1（只在shared query） | -3，该consumer集合的resume-input lookup/error mapping收口 |
| executor direct `materializations` access sites | 1 | 0 | -1，改为调用shared query |
| invocation continuation direct read | 1 | 1 | 0；保留continuation-specific coordinate/frame integrity owner与错误文本 |
| routing direct read | 1 | 1 | 0；保留routing binding/readiness owner |
| recovery direct `materializations` access sites | 0 | 0 | 0，architecture owner invariant保持 |
| `GraphExecutor.resume()` coordinate-plan resolution | 每个action 1次（含skip） | 每个non-skip action经query 1次、skip 0次 | 删除skip无效lookup；non-skip不增加 |
| `pending_node_input_available()` coordinate lookup | 1 | 1（经query） | 0；fallthrough的`node_inputs_available()`既有第二lookup保持，不因S12增加 |
| `materialize_node_input()` primary lookup | 1 | 1（经query） | 0；override decode既有admission lookup保持，不因constructor增加第三次 |
| override frame admission lookup | 1 | 1（经query） | 0；与coordinate/materialization边界分列，不隐瞒nested调用成本 |
| recovery materialization resolution | 0 | 每个non-skip action经query 1次 | +1，为canonical malformed validation所必需 |
| compiled-scope traversal implementations | 1 invocation-local | 1 topology-owned shared | 0，owner 收口 |
| resume-coordinate constructor implementations | 1 shared + 2 direct sites | 1 shared | -2 direct sites |
| shared resume-input materialization typed query | 0 | 1 | +1，六个production consumers + 该集合唯一unknown-node error owner，不是thin helper或全局accessor |
| top-level production function总数 | baseline | baseline + 1 | +1（scope函数迁移净0；新增上述multi-consumer query） |
| 新 dataclass/DTO/field/property/type alias/cache/index | 0 | 0 | 0 |
| 新 execution/state/persistence path | 0 | 0 | 0 |
| 新 preflight action loop | 0 | 1 | +1，只在seed admission执行，不进入proof successor loop |
| 新 skip exclusion branch | 0 | 1 | +1，skip不声明current resume-input requirement |
| 新 unknown-materialization branch | 0 | 1 | +1，单次`FrozenMap.get()`后typed fail closed |
| 新 exact-membership failure branch | 0 | 1 | +1，missing/wrong descriptor统一fail closed |

旧 scan 已从 `PreparedResume.inputs` 删除，但不能把新 validation谎报为“全部 scan归零”：implementation 以每个 non-skip action一次
compiled lookup和一次full recovery availability linear membership换取malformed seed fail-closed。该成本只在一次
`preflight_recovery()` seed admission发生，不进入worklist successor loop，不建立长期index。允许新增的逻辑面只有shared
resume-input materialization query及这条validation；不得为压低表面分支数删除invariant，也不得拆成validator class/context/registry。
新增query的正当性由多consumer复用、recovery architecture边界和该集合唯一typed error mapping共同成立。Actual diff没有第二
stored fact、第二coordinate constructor、第二resume-input lookup query、family cache或compatibility alias。

一次性 actual source review 结果为：

```text
resume_input_availability / AdmittedResumeFact[ / _RecoveryFamily[
→ production 无输出

ResumeInputAvailabilityCoordinate(
→ production 仅 engine/resume_input.py 中 1 个 constructor

_require_node_materialization(
→ 7 处：1 definition + 6 production consumers

transition.materializations
→ 3 处：resume_input shared query / invocation continuation validator / routing owner

transition.materializations in recovery.py
→ 无输出

def _compiled_at in invocation.py
→ 无输出
```

七文件实际 diff 为 `341 insertions, 114 deletions`。新增面保持为一个 multi-consumer typed query和一条 preflight invariant；
没有新增 dataclass、DTO、field、property、type alias、cache、index、execution path、State/Store/persistence owner 或 failover policy。

## 8. Behavior、malformed 与 typing evidence

### 8.1 必须同步落地的 target cases

只修改既有 `tests/execution/engine/test_recovery_identity.py`，新增两个行为 case：

| exact nodeid | 断言目标 | 失败条件 |
| --- | --- | --- |
| `test_recovery_valid_domain_equality_uses_availability_as_the_only_resume_input_fact` | 同 action 下 exact resume coordinate不同的 transfer states 仍不相等且 seen不合并；action五字段差异仍参与 equality；不再构造重复 action coordinate field | availability 差异被吞掉、action semantic field被移出 equality、使用 traversal key去重 |
| `test_recovery_preflight_requires_exact_resume_input_availability_for_each_non_skip_action` | exact coordinate存在时通过；missing、wrong descriptor、unknown scope、unknown materialization按固定precedence和文本fail closed；skip + historical coordinate不误拒绝 | malformed non-skip seed进入proof、泄漏`KeyError`、precedence漂移、skip被错误绑定或读取concrete value |

现有 `test_recovery_identity_keeps_every_availability_and_admitted_action_fact` 同步迁移为五字段 non-generic action constructor，
保留 control/availability/child/action/invocation-new-child full identity断言。现有
`test_recovery_preflight_rejects_invalid_binding_sets_and_unfenced_execution` 只迁移 constructor shape，全部 exception type/text断言
原样保持。

第二个 target case 必须在同一 behavior test function内覆盖以下独立subcases；每个 forged seed先满足排在目标错误之前的
root/binding/action/frontier/settlement约束，不得因前置构造错误形成假阳性：

| Subcase | Exact observable |
| --- | --- |
| exact current coordinate | `preflight_recovery()`成功进入既有proof并返回boundary |
| 缺少current coordinate | exact `SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")` |
| same activation / wrong descriptor | 与missing相同的exact type/text，不接受activation-only匹配 |
| unknown nested action scope + missing coordinate | 先抛`SnapshotMismatchError("scope references unknown nested node 'unknown'")` |
| existing scope、compiled/state-known Pending node；只forge materialization map缺项 + missing coordinate | shared query先抛`SnapshotMismatchError("node input references an unknown compiled materialization")`；recovery不接触map且不产生或泄漏`KeyError` |
| duplicate publication coordinate projection + unknown scope/materialization | 先抛`SnapshotMismatchError("recovery publication availability coordinates must be unique")`，不进入新增lookup |
| skip action + historical resume coordinate | 通过该invariant，不删除历史coordinate、不为skip建立current requirement |

#### 8.1.1 Exact forged-seed construction recipe

第二个target case不得临场发明fixture。先用现有`empty_graph()`和真实reducer建立同一套base objects；target implementation删除
`AdmittedResumeFact`泛型及第六字段后，构造形状固定为：

```python
graph = empty_graph()
node_id = GraphNodeId("node")
root_state = reduce_graph_run(
    None,
    project_start_graph_command(graph, GraphRunId("resume-invariant-root")),
)
root_scope = root_scope_run(root_state.run_id)
activation = StableActivation(root_scope, root_state.superstep, node_id)
action = AdmittedResumeFact(
    activation,
    AdmittedActionKind.RESUME_FAILED,
    None,
    None,
    None,
)
plan = graph.transition.materializations[node_id]
input_frame: NodeInputFrame[str] = _make_node_input_frame((), ())
exact_record: AdmittedResumeInput[str] = AdmittedResumeInput(
    ResumeInputAvailabilityCoordinate(activation, plan.descriptor.identity),
    input_frame,
)
limits = ExecutionLimits(2, 1)
base_seed: RecoveryInvocationSeed[str] = RecoveryInvocationSeed(
    RecoveryStateBinding(root_scope, root_state),
    (),
    ScopedFrameIndex(resume_inputs=(exact_record,)),
    limits,
    (action,),
)
```

`root_state`由compiled graph真实start command产生，因此frontier中的`node`是known `PendingGraphNode`，其superstep与action一致；
base action/binding/frontier/settlement checks不是通过手写无效state绕过。`AdmittedResumeInput`、`NodeInputFrame`与
`_make_node_input_frame`只补入现有test imports，不新增test helper、production constructor或frame validator。

各subcase必须从上述base objects机械派生：

1. **Exact coordinate**：直接调用`preflight_recovery(graph, base_seed)`；必须越过新增invariant并返回既有proof boundary。
2. **Missing coordinate**：只做`replace(base_seed, frames=ScopedFrameIndex())`；action/binding/frontier/settlement、frame projection、scope和
   materialization query均通过，exact membership owner抛
   `SnapshotMismatchError("recovery admitted resume action lacks its exact resume-input availability")`。
3. **Wrong descriptor**：从现有`interruptible_graph().transition.materializations[node_id].descriptor.identity`取得另一个真实compiled
   NODE_INPUT descriptor；保持同一个`activation`和同一个valid `input_frame`，只以该descriptor构造一个`AdmittedResumeInput`并替换
   `base_seed.frames`。scope与materialization query仍按原graph得到canonical descriptor，exact membership owner必须抛与missing相同的
   exact错误；不得用activation-only比较，也不得直接手写malformed descriptor object。
4. **Unknown nested scope**：另用同一个valid `graph`执行start command得到`unknown_state`和run ID；构造
   `unknown_scope = ScopeRunCoordinate((GraphNodeId("unknown"),), unknown_state.run_id)`，再构造target为
   `StableActivation(unknown_scope, unknown_state.superstep, node_id)`的non-skip action。seed保留合法root binding，并按canonical顺序增加
   `RecoveryStateBinding(unknown_scope, unknown_state)`；frames使用空`ScopedFrameIndex()`。这样binding存在、run/superstep一致、target
   `node`在unknown-state frontier中且为Pending；前置checks及projection通过后，只能由`_compiled_graph_at_scope()`抛
   `SnapshotMismatchError("scope references unknown nested node 'unknown'")`。
5. **Unknown materialization**：保持原`graph.nodes`、root state、root scope、Pending target和action不变；通过
   以下机械构造只过滤掉`node_id` entry，再形成forged graph：

   ```python
   missing_materializations = replace(
       graph.transition.materializations,
       entries=tuple(
           (candidate, candidate_plan)
           for candidate, candidate_plan in graph.transition.materializations.entries
           if candidate != node_id
       ),
   )
   forged_graph = replace(
       graph,
       transition=replace(graph.transition, materializations=missing_materializations),
   )
   ```

   seed只把frames改为空；scope query通过后，shared materialization query必须抛
   `SnapshotMismatchError("node input references an unknown compiled materialization")`，不得泄漏`KeyError`。不得从frontier或
   `graph.nodes`删除node。
6. **Duplicate publication precedence**：基于原graph的`transition.publications[node_id]`、同一root activation、
   `_make_node_output_frame(Graph.values(), ())`构造如下nominal record和直接forged index：

   ```python
   publication = graph.transition.publications[node_id]
   record: ConfirmedPublication[str] = ConfirmedPublication(
       PublicationAvailabilityCoordinate(activation, publication.identity),
       _make_node_output_frame(Graph.values(), ()),
       root_state.revision,
       ExecutionPublicationProvenance(
           GraphExecutionToken(1, GraphExecutionAttemptId("duplicate-publication")),
       ),
   )
   duplicate_frames: ScopedFrameIndex[str] = ScopedFrameIndex(publications=(record, record))
   ```

   不得调用会提前拒绝duplicate的
   `add_publication()`。把该frames放入第4项unknown-scope seed（也可叠加第5项forged graph）；前置action checks通过后，
   `RecoveryAvailabilityCoordinates.from_frames()`必须先抛
   `SnapshotMismatchError("recovery publication availability coordinates must be unique")`，scope/materialization query均不得执行。
7. **Skip + historical coordinate**：使用compiler-produced valid graph和既有valid skip post-resume state；frames保留一个历史
   resume coordinate。只证明skip绕过本次non-skip invariant且历史coordinate被原样保留，不引入forged missing-materialization
   topology，也不冻结private lookup shape。

每项断言必须登记以下三元关系，且测试失败信息能区分前置构造错误：

| Subcase | 已通过的前置 owner | 目标 owner → exact observable |
| --- | --- | --- |
| exact | root/binding/action/frontier/settlement + projection + scope + materialization | membership通过 → existing proof boundary |
| missing | 同上 | exact membership → `recovery admitted resume action lacks its exact resume-input availability` |
| wrong descriptor | 同上；wrong record仍是nominal typed frame record | exact membership → 与missing完全相同的type/text |
| unknown scope | root/binding/action/frontier/settlement + projection | compiled-scope query → `scope references unknown nested node 'unknown'` |
| unknown materialization | root/binding/action/frontier/settlement + projection + root scope | shared materialization query → `node input references an unknown compiled materialization` |
| duplicate publication | root/binding/action/frontier/settlement | availability projection → `recovery publication availability coordinates must be unique` |
| valid skip + history | public continuation/action/frame owners | skip exclusion → 不误拒绝、不删除历史coordinate |

Unknown-scope helper迁移后还必须原样复跑
`tests/execution/test_continuation_integrity.py::test_recovered_continuation_rejects_an_unknown_child_scope`；该文件不修改，因此不进入
implementation changed-file manifest。

Record nominal type、scope/descriptor和concrete frame malformed不放进直接`preflight_recovery()` target case；它们继续由public facade
先调用的`validate_context()`拥有，并至少原样复跑
`test_complete_continuation_rejects_a_malformed_resume_input_record`、
`test_complete_continuation_rejects_an_inconsistent_resume_input`与
`test_complete_continuation_readmits_resume_input_frame_content`。不得向recovery复制第二套frame validator。

### 8.2 必须原样复跑的现有行为

| Requirement | 现有 case |
| --- | --- |
| canonical resume action + exact input install | `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run`；`::test_same_scope_resume_input_and_substitution_install_as_one_frame_snapshot` |
| interrupt exact action | `tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run` |
| skip route/future proof | `tests/execution/test_graph_recovery_contract.py::test_recovered_concrete_skip_keeps_its_route_and_rejects_missing_boundary_before_commit`；`::test_recovered_plain_skip_rejects_a_missing_graph_output_before_commit` |
| equality/seen/traversal | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_deduplicates_routes_with_the_same_successor_state`；`::test_recovery_preflight_uses_one_canonical_completion_order_for_plain_nodes` |
| exact 4096 budget | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_has_a_bounded_transfer_state_budget` |
| concrete value隔离 | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_never_hashes_orders_or_renders_concrete_frame_values` |
| recovery Result/limit/child boundary | `tests/execution/test_graph_recovery_contract.py` 全文件 |
| generic relationship | `tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types` + strict Pyright |
| owner/dependency | 第12节列出的exact nodeid：graph definition依赖方向、single contract owner、executor no-persistence、recovery不得直接读取materializations、single compiled lowering、recovery shared lowering、module import、no-reflection/no-`Any`与single generic executor owner |

所有新增断言必须通过 exception/result/equality/hash 和 typed value观察，不得 mock/monkeypatch private function，不得断言源码行数、
AST、local 名称、loop 次数或 import layout。

### 8.3 一次性 source/generic review

以下查询只写入 implementation writeback，不转成永久 legacy/private-source gate：

```bash
rg -n 'resume_input_availability|AdmittedResumeFact\[|_RecoveryFamily\[' \
  src/mote_kernel/execution/engine/recovery.py \
  src/mote_kernel/execution/invocation.py \
  src/mote_kernel/execution/executor.py
# target：exit 1 且无输出

rg -n 'ResumeInputAvailabilityCoordinate\(' \
  src/mote_kernel/execution/engine/resume_input.py \
  src/mote_kernel/execution/engine/recovery.py \
  src/mote_kernel/execution/executor.py \
  src/mote_kernel/execution/invocation.py
# target：唯一 production constructor 位于 engine/resume_input.py

rg -n '_require_node_materialization\(' \
  src/mote_kernel/execution/engine/resume_input.py \
  src/mote_kernel/execution/executor.py \
  src/mote_kernel/execution/engine/recovery.py
# target：恰好7处（1个definition + 6个production consumers）

rg -n 'transition\.materializations' src/mote_kernel/execution
# target：三个direct read：resume_input.py shared query、invocation.py continuation validator、routing.py routing owner；
# executor/recovery不得direct read。该结果是一次性owner inventory，不是永久source-shape gate

rg -n 'transition\.materializations' src/mote_kernel/execution/engine/recovery.py
# target：exit 1；recovery只调用shared typed query，不读取map/bindings/declarations或形成第二interpreter

rg -n 'def _compiled_at' src/mote_kernel/execution/invocation.py
# target：exit 1；不保留 compatibility alias
```

## 9. Normative 同步

同一 implementation unit 必须更新 `graph-node-input-output-contract-implementation.zh-CN.md`：

1. exact shape 中删除 `AdmittedResumeFact` 第六字段；
2. `AdmittedResumeFact[GraphValueT]` 改为非泛型 `AdmittedResumeFact`；
3. 明确 action equality只拥有 target/kind/interrupt/reason/route，resume presence只由
   `RecoveryAvailabilityCoordinates.resume_inputs` 拥有；
4. 明确 non-skip action 在 recovery seed admission时必须存在 shared owner推导的 exact coordinate；
5. 明确 malformed missing/wrong coordinate在 mutation前以 `SnapshotMismatchError` fail closed；
6. 保持 `RecoveryTransferState[GraphValueT]` full-semantic equality、traversal-key sort-only、4096 budget 和 concrete value隔离；
7. 增加resume-input runtime/executor/recovery consumer集合唯一materialization typed query
   `_require_node_materialization(graph, node_id)`及其固定unknown-node错误，保持coordinate唯一constructor签名为
   `_resume_input_coordinate(activation, plan)`；该集合均经query取得authoritative plan，recovery不直接读取materializations；
   continuation validator与routing保留各自direct read和既有错误/解释owner，不把query伪称为全局accessor；
8. 把 compiled scope traversal owner同步为 `_compiled_graph_at_scope()`，不引入 graph-family map。

不修改 architecture public API、State schema、skip-output requirements/implementation、README 或 persistence 文档；S12 不改变这些
owner 的 normative truth。

## 10. Atomic change units 与 exact manifests

### 10.1 已完成与当前 docs-only units

首轮、第二次与第三次review records均保持历史原文。每次owner writeback的exact manifest都只包含：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation.zh-CN.md
```

首轮review response是独立audit unit，只包含：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-review-response.zh-CN.md
```

第二次review response也是独立audit unit，只包含：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-second-review-response.zh-CN.md
```

第三次review response也是独立audit unit，只包含：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-third-review-response.zh-CN.md
```

各unit不得把review历史文件、主实施方案、requirements或未修改文件伪列入manifest。本文继续是唯一target owner；response只登记
disposition。

### 10.2 第四次独立技术评审（已完成）

第四次独立评审记录为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-fourth-review.zh-CN.md
```

该 review 绑定批准设计 SHA256 `1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7`，裁决为
`PASS / READY FOR REQUIREMENTS OWNER APPROVAL`。它只拥有 valid-domain、malformed、generic、owner、manifest、Kernel/Graph
failover 边界和 behavior evidence 的独立裁决，不拥有 target 或批准状态；既有 review/response 历史保持原文。

### 10.3 Requirements approval（已完成）

第四次独立技术评审通过后，用户已显式批准；requirements owner 只在以下文件记录批准状态：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

该单元把 S12 记为 `GSP-A06 SATISFIED / APPROVED — 仅限当前 reviewed exact target`，不复制本文 target shape。

### 10.4 Production + behavior + normative implementation（已完成）

commit `269ffaa6fe101164c0055f8426a72b761135d393` 的 exact actual manifest 为：

```text
mote-kernel/src/mote_kernel/execution/graph/topology.py
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/engine/test_recovery_identity.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

Actual diff 为 `341 insertions, 114 deletions`，与批准的七文件 planned manifest 完全一致。没有新增测试文件，没有修改
legacy/private-source-shape 或 complexity gate，也没有触及 `pyproject.toml`、Makefile、pre-commit、State、protocol、Store、
README、persistence 或 S02/S12 外 production。

### 10.5 Implementation owner writeback（内容已完成，未提交）

Production unit 及全部适用 gate 已通过；S12 implementation-owner writeback 单元的 exact manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation.zh-CN.md
```

该单元记录第 3、7、10.4、12 和 14 节的 actual manifest、structural ledger、source review 与验证结果，不修改 production、
tests、requirements、normative source 或历史 review/response/acceptance。批准生命周期顺序为：

```text
design → first independent review → first review response → owner writeback
→ second independent review → second review response → owner writeback
→ third independent review → third review response → owner writeback
→ fourth independent review → requirements approval → production+behavior+normative
→ implementation owner writeback
```

Production implementation 已作为独立 commit `269ffaa` 落地；该 docs-only owner writeback 不 amend 或混入该提交，当前内容
按用户要求保持未暂存、未提交。

### 10.6 Current-status owner/index 同步（内容已完成，未提交）

为避免 current truth source 仍把 S12 写成未实施，另有两个不复制 exact target 的 docs-only 同步单元。Requirements owner
只记录批准后的生命周期状态，exact manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

主实施方案只更新总账索引、阶段状态与剩余 P2 数量，exact manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

这两个同步单元不进入第 10.5 节 S12 implementation-owner writeback manifest，也不进入 production commit `269ffaa`。
当前三份 docs-only 内容均未暂存、未提交；后续若交付，仍须保持 owner 边界，不得把历史 review/response/acceptance 纳入 manifest。

## 11. 实施顺序（已完成）

以下八步已在 commit `269ffaa` 及其适用验证中完成：

1. 在 `graph/topology.py` 建立 `_compiled_graph_at_scope()`，迁移 invocation全部 consumer并删除 `_compiled_at()`；先用现有
   invocation/continuation malformed cases确认错误 type/text不变；该纯 topology query不承载failover policy。
2. 在`engine/resume_input.py`增加resume-input consumer集合唯一`_require_node_materialization(graph, node_id)` typed query，把该模块
   四个direct access、executor direct access和recovery新增consumer全部迁入；保持invocation continuation与routing direct read不变；
   保持`_resume_input_coordinate(activation, plan)`签名，让executor两个
   direct constructor改用它，并重排request branch使skip不再查找resume materialization。不改变现有public/internal caller
   signatures，不新增第二resume-input query或alias。
3. 在 recovery中删除 action重复字段，完成 `AdmittedResumeFact`/`_RecoveryFamily` 去泛型及所有 annotation迁移。
4. 简化 `_resume_facts()`，删除 prepared参数、scan、local和第六实参。
5. 保持preflight现有action validation顺序；完成availability projection后按每个non-skip action解析scope、查找一次plan、调用
   唯一coordinate constructor并做一次membership；unknown-materialization fixture固定为compiled/state-known Pending node且只forge
   materialization map缺项；frame projection precedence只使用本owner已有的duplicate-publication check，不复制frame validator；固定
   unknown scope/materialization/missing precedence后再构造family。
6. 同步迁移existing recovery identity cases并增加两个target behavior cases；第二个case严格使用第8.1.1节base seed与七项机械派生，
   逐项断言“已通过前置owner → 目标owner → exact observable”，不得用更早错误形成假阳性。
7. 同一原子 diff同步 Node I/O normative shape和 invariant。
8. 运行scoped current-contract gates、source review和actual structural ledger；明确排除automated complexity及
   legacy/private-shape gates，任一适用检查或停止条件触发则整体回退S12。

不得先双存新旧 action shape，不得把 coordinate owner收口、field删除、generic migration、target tests或 normative同步拆成可合并的
长期中间提交。

## 12. Verification gates

门禁分类以语义而不是“是否已经存在”裁决，且三类穷尽本单元：

```text
REQUIRED: 本节明确列出的current behavior、strict typing、active generic/dependency/owner/source-discipline、lint、format、build/package与跳过complexity hook后的pre-commit
USER-EXCLUDED: automated complexity gate / baseline / ratchet
USER-EXCLUDED: legacy/private-source-shape gate，无论该gate是既有还是拟新增
```

因此“existing non-complexity gates全部必跑”不是本单元口径：它会重新纳入用户明确排除的既有legacy/private-shape tests。反之，
“legacy gate排除”也不能用于跳过上面REQUIRED的current-contract checks。不得仅按AST实现形式分类；判断标准是case验证当前有效的
behavior/type/owner契约，还是冻结已删除private symbol、local、表达式或source layout。

实施验证口径至少包括：

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_recovery_contract.py \
  tests/execution/test_graph_api.py \
  tests/execution/test_continuation_integrity.py

python -B -m pytest -q -p no:cacheprovider \
  tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types \
  tests/architecture/test_dependency_direction.py::test_execution_does_not_depend_on_domain_packages \
  tests/architecture/test_dependency_direction.py::test_graph_definition_layer_does_not_depend_on_runtime_execution_modules \
  tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners \
  tests/architecture/test_graph_execution_ownership.py::test_executor_does_not_apply_state_or_own_persistence \
  tests/architecture/test_graph_execution_ownership.py::test_compiled_routing_is_interpreted_only_by_routing_and_snapshot_guard \
  tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering \
  tests/architecture/test_graph_execution_ownership.py::test_recovery_consumes_shared_claim_and_settlement_lowering \
  tests/architecture/test_source_discipline.py::test_imports_form_a_contiguous_module_header \
  tests/architecture/test_source_discipline.py::test_dynamic_import_and_reflection_escape_hatches_are_forbidden \
  tests/architecture/test_source_discipline.py::test_internal_any_is_forbidden \
  tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner

python -B -m pytest -q -p no:cacheprovider tests/execution

python -B -m ruff check \
  src/mote_kernel/execution/graph/topology.py \
  src/mote_kernel/execution/engine/resume_input.py \
  src/mote_kernel/execution/executor.py \
  src/mote_kernel/execution/engine/recovery.py \
  src/mote_kernel/execution/invocation.py \
  tests/execution/engine/test_recovery_identity.py

python -B -m ruff format --check \
  src/mote_kernel/execution/graph/topology.py \
  src/mote_kernel/execution/engine/resume_input.py \
  src/mote_kernel/execution/executor.py \
  src/mote_kernel/execution/engine/recovery.py \
  src/mote_kernel/execution/invocation.py \
  tests/execution/engine/test_recovery_identity.py

pyright

python -B -m build --no-isolation
python -B -m twine check dist/*

cd .. && SKIP=kernel-complexity pre-commit run --files \
  mote-kernel/src/mote_kernel/execution/graph/topology.py \
  mote-kernel/src/mote_kernel/execution/engine/resume_input.py \
  mote-kernel/src/mote_kernel/execution/executor.py \
  mote-kernel/src/mote_kernel/execution/engine/recovery.py \
  mote-kernel/src/mote_kernel/execution/invocation.py \
  mote-kernel/tests/execution/engine/test_recovery_identity.py \
  mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md

git diff --check -- \
  src/mote_kernel/execution/graph/topology.py \
  src/mote_kernel/execution/engine/resume_input.py \
  src/mote_kernel/execution/executor.py \
  src/mote_kernel/execution/engine/recovery.py \
  src/mote_kernel/execution/invocation.py \
  tests/execution/engine/test_recovery_identity.py \
  docs/graph-node-input-output-contract-implementation.zh-CN.md
```

完整`make check`当前无条件包含用户明确排除的complexity unit，完整`make test`又会收集用户明确排除的legacy/private-shape
门禁，因此二者都不作为S12 gate。上列命令覆盖全部`tests/execution` behavior以及与本单元直接相关的exact generic、dependency、
single-owner、no-persistence和source-discipline nodeid，再覆盖typing、lint、format、build/package、七文件 scoped monorepo hooks
和changed-file whitespace检查。
本次 implementation writeback 已在第 3.4 节逐条记录结果，并精确报告未运行完整 `make check`、全仓 coverage 与完整
monorepo pre-commit 的范围和原因，没有冒充通过。

这里列出的architecture cases虽然使用AST实现检查，但只验证仍然有效的generic、dependency、single-owner和no-type-erasure
契约，不是冻结已删除private symbol/local/source layout的legacy gate。S12不得新增、扩写或要求运行任何legacy/private-shape
gate；一次性`rg`只作为actual diff审计写入owner writeback，不转化成pytest/pre-commit门禁。Automated complexity
gate/baseline/ratchet同样完全排除。

## 13. 停止条件

除 requirements `GSP-S01`–`GSP-S08` 外，出现任一条件即停止并保持当前 production：

1. 不能保持现有 root/binding/action/settlement malformed error type、text和 precedence；
2. valid-domain old/new equality partition、seen cardinality、traversal order、reachable boundary或4096 budget任一变化；
3. recovery需要直接读取`transition.materializations`，或读取bindings/declarations并复制node-input/materialization
   interpreter；
4. 需要保留旧 coordinate field、compatibility alias、phantom generic、`compare=False` 或第二 availability truth；
5. 需要新增 DTO/context/cache/index/registry、graph-family map或第二 scope traversal；
6. resume-input consumer集合唯一node-materialization query与coordinate constructor不能同时被runtime、executor admission和recovery
   invariant复用，或需要第二query/constructor、重复lookup cache或forwarding alias；
7. non-skip missing/wrong exact coordinate不能在任何 mutation前 typed fail closed；
8. skip action被错误要求拥有本 invocation resume-input coordinate，或历史 coordinate被误删/误拒绝；
9. concrete user value进入 recovery equality/hash/order/repr或 invariant validation；
10. generic migration需要 `Any`、bare generic、cast或擦除 `RecoveryTransferState`/seed的真实类型关系；
11. actual changed-file manifest越界，或需要新增/修改/依赖legacy/private-source/complexity gate；
12. 触及public `Graph` facade、State、command/reducer、commit、protocol、Store或持久化；
13. Graph需要自行选择retry/backoff/error classification，或S12需要新增Kernel failover Port、实现、registry、缓存或第二runner；
14. 任一适用current behavior/typing/owner/dependency check失败且无法在本exact target内解释并修复。

## 14. 当前准入状态

```text
S12 DESIGN: COMPLETE
S12 FIRST INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / RESPONSE RECORDED
S12 FIRST-REVIEW OWNER WRITEBACK: SUPERSEDED BY CURRENT TARGET
S12 SECOND INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / RESPONSE RECORDED
S12 SECOND-REVIEW OWNER WRITEBACK: SUPERSEDED BY CURRENT TARGET
S12 THIRD INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / RESPONSE RECORDED
S12 THIRD-REVIEW OWNER WRITEBACK: COMPLETE
S12 FOURTH INDEPENDENT TECHNICAL REVIEW: PASS
S12 GSP-A06: SATISFIED / APPROVED FOR DESIGN SHA 1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7
S12 PRODUCTION / TEST / NORMATIVE IMPLEMENTATION: COMPLETE IN 269ffaa6fe101164c0055f8426a72b761135d393
S12 IMPLEMENTATION VERIFICATION: PASS
S12 IMPLEMENTATION-OWNER WRITEBACK: CONTENT COMPLETE / UNCOMMITTED BY USER REQUEST
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH-OWNED FAILOVER / RETRY POLICY: FORBIDDEN; KERNEL PORT BOUNDARY HARD KEEP
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: USER-EXCLUDED
LEGACY / PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED WHETHER EXISTING OR NEW
CURRENT BEHAVIOR / TYPING / ACTIVE OWNER-DEPENDENCY CHECKS: REQUIRED
```

S12 已按第 10.4 节 exact manifest 完成实施并通过二次代码验收。后续变更不继承本次 `GSP-A06`；任何 target、State、Store、
persistence、public facade 或 Graph/Kernel failover policy 变化都必须另立需求和批准单元。
