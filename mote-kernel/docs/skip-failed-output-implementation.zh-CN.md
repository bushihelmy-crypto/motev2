# `skip_failed` 可选替代输出实施设计

## 1. 文档信息

- 状态：Implemented / verification complete
- 日期：2026-08-17
- 需求基线：`docs/skip-failed-output-requirements.zh-CN.md`
- 评审记录：`docs/skip-failed-output-requirements-review.zh-CN.md`、`docs/skip-failed-output-requirements-review-response.zh-CN.md`、`docs/skip-failed-output-requirements-second-review.zh-CN.md`
- 实施范围：Mote Kernel Python 唯一 `execution` graph engine
- 本文记录已批准并完成验证的 owner、typed model、调用顺序和测试证据；第二轮实施设计评审已批准 production 编码，最终实现按本文收敛。

需求文档是行为规范的唯一真相。本文不得重新解释或放宽需求；发生冲突时先修订需求并重新评审。

## 2. 实施结论

本改动不增加 execution engine、public Graph facade、State value、runner、frame store 或 routing path。实施沿用现有链路：

```text
Graph.skip_failed(output=Graph.values(...))
  -> typed SkipFailedNodeRequest
  -> GraphExecutor.resume() exact action/output admission
  -> invocation.plan_resumes() whole-invocation candidate planning
  -> shared routing/admission proof
  -> shared future-path proof（凡 invocation 含 pure skip）
  -> state-only recovery 附加 fail-closed proof（无 continuation 时）
  -> per-scope commit_transition()
  -> build complete admitted frame snapshot
  -> replace confirmed memory State
  -> replace ScopedFrameIndex with the complete snapshot
  -> existing materialization/routing/recovery consumers
```

唯一 durable settlement 继续是 `SkippedGraphNode`。State 不区分 pure skip 与 substitution skip；control truth 由 State 拥有，concrete data truth 与来源由唯一 `ConfirmedPublication` record 拥有。

## 3. 已关闭的实施决策

| 议题 | 唯一决定 |
| --- | --- |
| Public API | 只扩展 `Graph.skip_failed(..., output=...)` |
| Request typing | `SkipFailedNodeRequest[GraphValueT]` 保存 canonical `_GraphValues[GraphValueT] | None` |
| Output admission | 复用 `execution.graph.values` 和失败节点 compiled output descriptor |
| State | 继续使用单一 `SkippedGraphNode`，不增加 substitution marker |
| Publication store | 继续使用唯一 `ScopedFrameIndex.publications` |
| Provenance | `ConfirmedPublication` 内使用 closed nominal provenance union |
| Data trigger | candidate/confirmed substitution publication 贡献该 activation 的 data availability |
| Pure skip | 不贡献失败节点自身 data trigger；只校验由其他 contribution 触发的必达 target |
| Proof owner | engine-internal pure typed resume admission owner，runtime 与 recovery 共用 routing truth |
| Duplicate admission | whole invocation 首个 commit 前完成 |
| Commit | admission atomicity + per-scope durable-first，不模拟跨 scope transaction |
| Nested | 允许 parent nested-node boundary substitution，不恢复 child run |
| Recovery | continuation 保留 concrete frame；state-only 缺值 fail closed |

## 4. Typed model

### 4.1 Public facade 与 request

修改 `src/mote_kernel/execution/facade.py`：

```python
def skip_failed(
    self,
    node_id: str,
    reason: str,
    *,
    route: str | None = None,
    output: "Graph.Values[GraphValueT] | None" = None,
    scope: tuple[str, ...] = (),
) -> "Graph.ResumeAction[GraphValueT]": ...
```

非 `None` output 在 facade 边界调用现有 `_require_graph_values()`。facade 只做 canonical public shape admission，不解析 compiled descriptor、不扫描依赖、不构造 frame。

修改 `src/mote_kernel/execution/request.py`：

```python
@dataclass(frozen=True, slots=True)
class SkipFailedNodeRequest(Generic[GraphValueT]):
    scope: tuple[GraphNodeId, ...]
    node_id: GraphNodeId
    reason: str
    route: str | None
    output: _GraphValues[GraphValueT] | None
```

`ResumeNodeRequest[GraphValueT]` 的三个 variant 全部保持同一 `GraphValueT`。不得以非泛型 union、`object`、`Any` 或 cast 擦除关系。

### 4.2 Candidate substitution 与最终安装计划

在 `src/mote_kernel/execution/run_context.py` 增加两个 owner-internal planning records：

```python
@dataclass(frozen=True, slots=True)
class PreparedSubstitution(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    provenance: SkipSubstitutionProvenance


@dataclass(frozen=True, slots=True)
class AdmittedSubstitution(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    provenance: SkipSubstitutionProvenance
    expected_revision: int
```

`PreparedSubstitution` 由 `GraphExecutor.resume()` 在 action-local admission 后构造；它已固定 coordinate、frame 和 nominal provenance，但尚不伪造 revision。`invocation.plan_resumes()` 用 pure reducer 得到 exact successor 后，将同一个 prepared evidence 机械提升为 `AdmittedSubstitution(..., expected_revision=successor.revision)`。

`run_context.py` 已拥有 publication coordinate、frame availability、provenance 和 invocation-local frame records；candidate 放在这里可保持依赖方向，避免当前 `result.py -> run_context.py` 反向形成 import cycle。

修改 `src/mote_kernel/execution/result.py`，将它加入 `PreparedResume[GraphValueT]`：

```python
class PreparedResume(Generic[GraphValueT]):
    command: ResumeGraphNodes
    inputs: tuple[AdmittedResumeInput[GraphValueT], ...]
    substitutions: tuple[PreparedSubstitution[GraphValueT], ...]
```

两种 record 都是 planning evidence，不是 confirmed publication，不携带 execution token，不进入 State 或 continuation。只有绑定 exact successor revision 的 `AdmittedSubstitution` 可交给 post-commit installation；不得从 `PreparedSubstitution` 直接安装。

### 4.3 Closed publication provenance

修改 `src/mote_kernel/execution/run_context.py`，用 nominal union 替换 `ConfirmedPublication.execution_token` 的单一来源假设：

```python
@dataclass(frozen=True, slots=True)
class ExecutionPublicationProvenance:
    execution_token: GraphExecutionToken


@dataclass(frozen=True, slots=True)
class SkipSubstitutionProvenance:
    pass


PublicationProvenance: TypeAlias = (
    ExecutionPublicationProvenance | SkipSubstitutionProvenance
)


@dataclass(frozen=True, slots=True, eq=False)
class ConfirmedPublication(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    acknowledged_revision: int
    provenance: PublicationProvenance
```

类型名可在评审中调整，但 shape 固定为 closed nominal union。禁止 nullable/fake token、字符串 discriminator、reason/routing mirror 和平行 substitution record/store。

execution success 安装点构造 `ExecutionPublicationProvenance(completed.command.execution)`；skip exact commit 安装点构造 `SkipSubstitutionProvenance()`。两者仍只进入 `ScopedFrameIndex.publications`。

### 4.4 Candidate availability overlay

在 `run_context.py` 增加只读 invocation-local view：

```python
@dataclass(frozen=True, slots=True)
class CandidateFrameAvailability(Generic[GraphValueT]):
    confirmed: ScopedFrameIndex[GraphValueT]
    substitutions: tuple[AdmittedSubstitution[GraphValueT], ...]
```

它只实现现有 presence-only `ScopedFrameAvailability[GraphValueT]`：非 publication segment 委托 confirmed index；`has_publication()` 同时查询 confirmed 和 candidate coordinates。它不实现 `lookup()`，不允许 proof 读取 concrete candidate frame，不进入 continuation，也不是第二 store。

本设计确定 future proof 只依赖 coordinate availability。candidate frame 已在 action-local exact admission 中验证类型；真正 node materialization、graph output projection 和 nested boundary projection只在 exact commit 后读取 confirmed `ScopedFrameIndex`。若编码发现 proof 必须读取 candidate concrete value，立即停止并重新评审，不得增加 substitution-specific lookup 或第二 frame map。

## 5. Exact output admission

`GraphExecutor.resume()` 继续拥有 failed settlement、route、scope 和 action-local admission。处理 `SkipFailedNodeRequest` 时：

1. 验证 current settlement 是 `FailedGraphNode`；
2. 构造并校验 `ContinueGraphRouting` 或 `SelectGraphRoute`；
3. 构造既有 `SkipFailedNode` command 和模拟 `SkippedGraphNode`；
4. output 为 `None` 时不构造 substitution；
5. output 非 `None` 时，从 `graph.publications[node_id]` 取得失败节点自己的 output descriptor；
6. 复用 `execution.graph.values` 的 canonical frame factory/admission 构造 exact `NodeOutputFrame`；
7. 用 current `scope_run + superstep + node_id + descriptor` 构造 coordinate；
8. 返回 `PreparedSubstitution`，不安装到 frame index。

不得根据 downstream input 拼 frame，不得 partial/default/`None` fill。若现有 values owner 缺少从 canonical `_GraphValues` 构造 output frame 的窄入口，只在 `values.py` 增加 private factory并复用 `_admit_entries()`，不得在 executor 复制 validation。

## 6. Shared resume admission 与 routing

### 6.1 Owner

新增 `src/mote_kernel/execution/engine/resume_admission.py`，只包含 pure、typed、无副作用的 candidate proof：

```python
@dataclass(frozen=True, slots=True)
class ScopedResumeCandidate(Generic[GraphValueT]):
    graph: CompiledGraph[GraphValueT]
    scope_run: ScopeRunCoordinate
    previous: GraphRunState
    successor: GraphRunState
    substitutions: tuple[AdmittedSubstitution[GraphValueT], ...]
    skip_actions: tuple[SkipFailedNode, ...]
    has_pure_skip: bool
    command: ResumeGraphNodes


def admit_resume_candidates(
    candidates: tuple[ScopedResumeCandidate[GraphValueT], ...],
    frames: ScopedFrameIndex[GraphValueT],
) -> CandidateFrameAvailability[GraphValueT]: ...
```

`invocation.plan_resumes()` 负责 scope resolution、executor admission、reducer simulation 和汇总；`admit_resume_candidates()` 一次性验证全部 scope 后返回 canonical candidate overlay。每个 candidate 自带该 scope 的 compiled graph，以避免 root graph 与 nested graph descriptor 混淆；`command` 是必填的 exact proof evidence，绝不允许 `None`。admission 无条件验证 `reduce_graph_run(previous, command) == successor`，并验证 `skip_actions` 正好等于 command 中的 `SkipFailedNode` actions、每个 substitution 与 exact successor 中的 skipped settlement/action/descriptor/revision逐项绑定。`has_pure_skip` 只是触发 future fail-closed proof 的 typed projection，不参与 reducer truth 的构造。

### 6.2 唯一 routing facts resolver

`src/mote_kernel/execution/engine/routing.py` 是 topology/routing facts 的唯一 owner，固定下列最小 pure contract：

```python
@dataclass(frozen=True, slots=True)
class RequiredTarget:
    node_id: GraphNodeId
    inputs_available: bool
    historical_inputs_missing: bool
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    control_targets: tuple[RequiredTarget, ...]
    completed_join_targets: tuple[RequiredTarget, ...]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    data_targets: tuple[RequiredTarget, ...]
    completion_output_available: bool
    completion_output_history_missing: bool
    unavailable_graph_outputs: tuple[str, ...]


def resolve_routing_facts(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameAvailability[GraphValueT],
) -> RoutingFacts: ...
```

该函数族唯一负责：direct/conditional selection、join arrivals/completion/remaining progress、基于 publication availability 的 data triggers、必达 target 完整 input availability，以及无 next target 时的 graph completion output availability。`historical_inputs_missing` 与 `completion_output_history_missing` 是 state-only recovery 对同一 availability truth 的 typed 投影；`unavailable_inputs` 与 `unavailable_graph_outputs` 是确定性诊断 identity，不构成第二 resolver，也不携带 concrete value。

- `plan_routing()` 只把 `RoutingFacts` 投影为 `AdvanceGraphFrontier / CompleteGraphFrontier / AbortGraphRun`；
- `resume_admission.py` 只把相同 facts 投影为允许或 `GraphValueUnavailableError`；
- `recovery.py` worklist transfer只调用相同 resolver，不得重新扫描 direct/conditional targets、joins 或 `transition.data_triggers`；
- nested boundary future traversal仍由 recovery worklist编排，但每个 scoped graph step 的 routing/availability facts只能来自该 resolver。

architecture test 必须把 `transition.data_triggers`、direct/conditional target maps 和 join topology 的 runtime读取限制在 `routing.py` 的该 owner 内。

### 6.3 Duplicate admission

shared admission 在首个 commit 前：

1. canonicalize 全部 candidate coordinates；
2. 拒绝 candidate 与 `frames.publications` collision；
3. 拒绝同 invocation candidate 间 collision，包括不同 scope；
4. collision 抛出 `GraphValuePublicationError`；
5. 返回 unique/canonical overlay。

不能调用 `ScopedFrameIndex.add_publication()` 做 candidate admission，因为 candidate 尚未 confirmed。commit 后 `add_publication()` 只保留 defensive invariant gate。

### 6.4 Data contribution 与必达输入

`resolve_routing_facts()` 避免用 settlement 类型直接等价推断 data availability：

```text
control contribution
  <- SucceededGraphNode / SkippedGraphNode.routing

data contribution
  <- exact activation 是否存在 confirmed 或 candidate publication
```

固定规则：

- pure skip 无 publication，不贡献该 source data trigger；
- substitution candidate 在 preflight 中贡献 candidate data trigger；
- exact commit 后 confirmed substitution 贡献正常 data trigger；
- direct/conditional、本 invocation 完成的 join、其他 source data trigger 激活的 target 都是必达 target；
- 每个必达 target 复用现有 materialization/availability rules 校验完整 inputs；
- 缺少 skipped source publication时，resume admission 抛出 `GraphValueUnavailableError`；
- 只有未触发 compiled data dependency 的 target 不必达，不误拒绝 pure skip。

正常 runtime `plan_routing()`、resume preflight 和 recovery traversal必须调用同一 target/data-contribution resolver。runtime 可继续把普通执行后的 unavailable control target 投影为现有 abort；resume admission 则在 commit 前将同一缺值事实提升为 `GraphValueUnavailableError`。共享的是拓扑、target 和 availability truth。

### 6.5 所有 pure skip 的 future-path proof

凡本 invocation 包含至少一个 `output=None` 的 `SkipFailedNodeRequest`，无论输入是 complete continuation 还是 state-only State，都必须在首个 commit 前运行 shared whole-future proof。该 proof 以全部 simulated scoped successors、candidate availability 和同 invocation actions 为 seed，沿 recovery worklist证明：

- 当前及未来必达 node inputs；
- graph completion outputs；
- nested parent boundary；
- join 将完成/不会完成的精确分支；
- loop exact superstep 与 activation identity。

substitution skip 的 candidate availability参与同一 proof。若 invocation 没有 pure skip，则仍执行 current-step shared routing facts与duplicate admission，但不因本功能额外遍历 whole future。

state-only recovery在同一 owner之上增加“没有 continuation history”的 fail-closed约束；它不是唯一运行 future proof 的路径。continuation与state-only对相同可证明 scoped path必须得到相同 target/availability facts。

### 6.6 Recovery

`execution/engine/recovery.py` 保留 whole-invocation worklist owner，但消费 shared resolver 和 candidate overlay：

- continuation resume 与 state-only recovery先执行相同 candidate admission；
- 只要包含 pure skip，两者都运行同一个 future-path traversal；
- state-only recovery再应用无 continuation history的附加 fail-closed约束；
- stable continuation 不重演已消费的历史 skip；
- state-only 没有 substitution frame且未来需要时 fail closed；
- 不从 `SkippedGraphNode` 推导默认 publication。

## 7. Planning 与 commit 顺序

### 7.1 Whole-invocation planning

`invocation.plan_resumes()` 重构为无副作用 planning：

```text
1. 校验 resume tuple canonical order
2. resolve 全部 scopes
3. 每个 scope 调 GraphExecutor.resume()
4. reduce_graph_run() 模拟 exact successor
5. 把 PreparedSubstitution 机械提升为绑定 successor.revision 的 AdmittedSubstitution
6. 收集 admitted resume inputs 与最终 substitution installation plans
7. 对所有 scope 一次性做 duplicate admission
8. 用 candidate availability 调 resolve_routing_facts()
9. invocation 含 pure skip时，无条件运行 shared whole-future proof
10. state-only recovery追加无 continuation history的 fail-closed proof
11. 只有上述全部通过，才把 plans 交给 facade commit loop
```

candidate frames 可以逐 scope immutable 累积以支持后续 scope planning，但所有 collision 与 shared proof 必须在任何 commit 前完成。

### 7.2 Per-scope commit

扩展 `_PlannedResume[GraphValueT]`，保存 scope、previous State、expected successor、command、inputs 和已经绑定 expected revision 的 admitted substitutions。facade 每个 scope 按以下顺序执行：

1. `commit_transition()` 返回 exact reducer successor；
2. 验证 confirmed successor 中 substitution 对应的 `SkippedGraphNode` reason/routing 与 admitted action一致；
3. 验证每个 admitted substitution 的 `expected_revision == confirmed.revision`；
4. 由 admitted plan 机械构造 `ConfirmedPublication`：coordinate、frame、provenance 原样复用，revision 只取已相等的 expected revision；
5. 在局部变量上从旧 `context.frames` 构造包含本 scope 全部 resume inputs 与 substitution publications 的新 immutable `ScopedFrameIndex`；
6. 任一 frame installation invariant失败时抛 `FrameInstallationInvariantError`，不替换 `context.frames`；
7. `context.replace_state(scope, confirmed)`；
8. 一次性 `context.frames = installed_frames`。
9. 成功 scope 只更新 invocation-local context；调用方传入的 continuation 永不修改。

commit throw、拒绝或 non-exact successor时不执行步骤 2–8。`FrameInstallationInvariantError` 是 `execution.errors` 中明确的 owner-internal invariant error，不从 `Graph` public namespace 暴露；内部 install helper捕获 `ScopedFrameIndex.add_*()` 的 defensive `GraphValuePublicationError` 并以 invariant error 链式抛出。

State 的 memory replacement仍先于 frame snapshot replacement，符合 durable-first。新 frame snapshot在局部完整构造后才依次替换 State 与 frames，避免同一 scope 的 resume inputs/publications半安装。若 frame局部构造失败，当前 scope虽可能已由外部 commit确认，但不得进入 invocation memory或显式 checkpoint；若此前已有完整确认前缀，则以该前缀构造 `PartialCommitError`，并以 `FrameInstallationInvariantError` 为 cause；若没有前缀，则原 invariant error直接传播。不得声称失败 scope 的frame已经安装，也不得发送补偿 transition。

### 7.3 跨 scope failure 与显式交付

scope A exact-confirmed 后 scope B commit 失败：

- A 的 State、resume input 和 substitution publication保留；
- B 的 memory State、resume input 和 publication不更新；
- 不回滚 A，不发送补偿 command；
- 若此前没有 scope exact-confirmed，原始 commit failure 原样传播；
- 若已有 scope exact-confirmed，抛 owner-sealed `Graph.PartialCommitError[GraphValueT]`，其 `cause` 保留原始异常并作为 `__cause__` 链式传播；
- error 的 `state` 是当前 root State，`continuation` 是从局部 context 新建的 immutable snapshot，`failed_scope` 精确标识失败 scope；
- non-exact successor以对应 `SnapshotMismatchError` 为 cause并遵循相同协议；
- 后续 scope frame installation invariant失败时，以 `FrameInstallationInvariantError` 为 cause遵循相同前缀交付协议，失败 scope不进入checkpoint；
- 调用方使用 error 显式携带的 authoritative State/continuation 重新 admission。

continuation 是 Graph owner 创建的 immutable opaque snapshot。`Graph.run()` 绝不原地修改输入 continuation；历史 Result 的 State/continuation pair永久稳定。只有 scope exact durable confirmation、完整 frame安装和 memory State替换都完成后，该 scope才进入可交付的新 snapshot。当前 scope frame安装失败时，新 snapshot不得声称该 scope已安装。concrete replacement仍不进入 durable State。

state-only调用没有可承载部分确认 concrete frame的原始 lineage。因此，无 continuation、跨多个 resume scope且包含 substitution output时，必须在首个commit前 fail closed；不得把frame写入 State、Graph实例或全局缓存。

## 8. Continuation integrity

修改 `invocation._validate_frame_index()`。共同校验：

- record、coordinate、provenance 是 exact nominal variant；
- coordinate scope-run 属于 lineage；
- node descriptor 与 compiled publication exact match；
- activation superstep 不晚于 scoped current State；
- acknowledged revision 为正且不晚于 scoped current revision；
- frame 重新通过 exact admission；
- coordinates unique/canonical。

按 provenance 分支：

- execution provenance 验证真实 token 的 generation、run/superstep/node 等当前可证明结构；
- substitution provenance 验证 nominal、coordinate/revision consistency；不要求当前 frontier仍有历史 `SkippedGraphNode`。

不得增加历史 State、settlement journal、substitution marker 或 reason/routing mirror重新证明历史来源。

## 9. Nested、loop 与 identity

现有 `_forbid_aborted_child_restart()` 只禁止 `ResumeFailedNodeRequest` 重启 terminal child；不得禁止 parent 对 failed nested node执行 `SkipFailedNodeRequest`。

parent nested-node substitution 使用 parent `scope_run`、parent current superstep、parent nested node ID 与 parent compiled publication descriptor；descriptor declarations 来自 child graph output boundary。不得读取或写入 child内部失败 leaf，不创建 child run，不删除 terminal child binding。

现有 `ScopeRunCoordinate + StableActivation + descriptor` 继续隔离 loop superstep、repeated child activation 与 sibling scope。

## 10. 错误映射

| 检查点 | 错误 |
| --- | --- |
| facade 非 canonical `Graph.Values` | `Graph.ValueAdmissionError` |
| exact output descriptor mismatch | `Graph.ValueAdmissionError` |
| invalid route | 现有 `Graph.RoutingError` 子类 |
| scope/node/settlement mismatch | `Graph.SnapshotMismatchError` |
| 必达 target、graph output或 nested boundary 缺 publication | `Graph.ValueUnavailableError` |
| pre-commit candidate collision | `Graph.ValuePublicationError` |
| malformed continuation provenance/coordinate/lineage | `Graph.SnapshotMismatchError` |
| post-commit 未预检 collision | `FrameInstallationInvariantError`，owner-internal，非公共业务错误 |
| 已有 exact-confirmed prefix 后，后续 scope commit throw/non-exact/frame invariant | `Graph.PartialCommitError[GraphValueT]`，原异常保存在 `cause`/`__cause__` |
| 首 scope/fence failure | 原始异常对象原样传播 |

错误消息包含 action node ID；可唯一定位时包含 consumer node/input、graph output 或 nested boundary identity。

## 11. 文件级改动

### 11.1 Production

| 文件 | 改动 |
| --- | --- |
| `execution/facade.py` | `skip_failed(output=...)`；commit 后安装 substitution；显式交付 partial-confirmation snapshot |
| `execution/request.py` | 泛型化 request 并保存 canonical output |
| `execution/result.py` | 扩展 `PreparedResume` 引用 prepared substitution；定义 owner-sealed generic `PartialCommitError` |
| `execution/executor.py` | failed/route/output exact admission，产生 candidate |
| `execution/graph/values.py` | canonical values 到 output frame 的窄 private factory |
| `execution/run_context.py` | prepared/admitted substitution、provenance union、candidate availability、唯一 store |
| `execution/invocation.py` | whole-invocation planning、integrity、planned commit evidence |
| `execution/engine/resume_admission.py` | shared pure resume/data-flow proof |
| `execution/engine/routing.py` | shared target/data-contribution resolver |
| `execution/engine/recovery.py` | 复用 shared resolver 与 candidate overlay |
| `execution/family_driver.py` | execution publication包装真实 provenance |
| `execution/errors.py` | 增加不公开到 `Graph` namespace 的 `FrameInstallationInvariantError` |

默认且批准的路径不修改 `state/graph_state`。编码若发现必须修改 durable State，立即停止并回到设计评审。

### 11.2 Tests

| 文件 | 覆盖 |
| --- | --- |
| `tests/execution/test_graph_api.py` | public API、canonical output、route/output正交矩阵、零commit、partial-confirmation/immutable continuation、root-child与frame-invariant交付边界 |
| `tests/execution/test_graph_public_typing.py` | public type shape不分叉、`PartialCommitError` owner seal |
| `tests/execution/test_executor.py` | failed/route/output exact admission |
| `tests/execution/test_frame_index_contract.py` | provenance、duplicate、candidate/confirmed boundary |
| `tests/execution/test_continuation_integrity.py` | closed provenance、revision、descriptor/lineage |
| `tests/execution/engine/test_resume_admission.py` | exact command/candidate evidence、control/join/data target、collision与确定性诊断 |
| `tests/execution/engine/test_routing.py` | pure skip、substitution trigger、other-source、join/branch共享facts |
| `tests/execution/engine/test_recovery_boundaries.py` | state-only fail closed、shared future proof |
| `tests/execution/engine/test_recovery_identity.py` | recovery loop/nested/repeated activation identity |
| `tests/execution/engine/test_runtime_boundaries.py` | parent nested substitution真实消费、terminal child保留、repeated child materialization隔离、mechanical publication安装 |
| `tests/execution/test_graph_recovery_contract.py` | continuation 与 state-only public behavior |
| `tests/architecture/test_graph_typing_fixtures.py` | action/output/partial-error cross-universe与factory inference negative fixtures |
| `tests/architecture/test_graph_execution_ownership.py` | 单一 store/resolver/runner 与 owner shape |

## 12. 测试矩阵

### 12.1 API、admission 与 typing

- `output=None` 保持既有 construction；
- canonical empty/non-empty output；
- raw mapping、伪 Values、missing/extra/wrong exact type；
- conditional route 与 output 分别 admission；
- `GraphValueT` 全链路与 cross-universe、heterogeneous、empty `Never` fixtures；
- public namespace不暴露 provenance、candidate 或 internal request。

### 12.2 Routing 与 availability

- pure skip 仅有未触发 data dependency时允许；
- selected direct/conditional target 缺值时零 commit；
- 本 invocation 完成 join 且缺值时零 commit；
- 未完成 join 不误判必达；
- other-source data trigger 激活 target 且缺 skipped value时零 commit；
- substitution candidate 激活自身 data-only target；
- graph completion output 缺值时零 commit；
- complete continuation + pure skip当前可推进但future graph output缺值时零 commit；
- complete continuation + pure skip当前可推进但future nested boundary缺值时零 commit；
- complete continuation + substitution candidate参与同一future proof并通过；
- state-only与continuation对相同可证明路径得到相同 routing facts；
- 未选 branch reference不误拒绝。

### 12.3 Publication 与 commit

- existing publication collision零 commit；
- invocation 内 candidate collision零 commit，包括跨 scope；
- commit throw/non-exact successor不更新 memory 或 frame；
- exact commit 后先在局部构造完整 frame snapshot，再按 replace State -> replace complete frame snapshot 顺序发布；
- provenance candidate与expected successor revision在首个commit前完成 admission；
- confirmed publication由 admitted plan机械提升，coordinate、frame、provenance、revision逐项exact；
- 同一 scope全部resume inputs/publications在一个immutable frame snapshot中安装；
- post-commit collision只在故意破坏 invariant 的白盒测试触发，抛 `FrameInstallationInvariantError`，绝不映射为 `GraphValuePublicationError`；
- scope A success、scope B throw/non-exact时不补偿 A，B不更新，并通过 `PartialCommitError` 显式交付 A 的新 immutable snapshot；
- 输入 continuation与历史 Result保持不变，共享同一旧 continuation的调用互不修改；
- root A success、child B failure时 error state与continuation仍是可配对的新快照；
- 首 scope失败原样传播，frame installation invariant不虚报当前 scope安装；
- state-only多scope substitution在首个commit前拒绝。

### 12.4 Continuation、recovery、nested 与 loop

- execution/substitution provenance均通过合法 continuation；
- malformed provenance、fake token、wrong descriptor、future revision、unknown lineage拒绝；
- frontier推进后 substitution continuation仍合法，不倒推历史 skip；
- continuation丢失且未来需要 substitution时 fail closed；
- parent nested boundary substitution可被 parent consumer读取；
- terminal child snapshot保留且未改写；
- loop superstep、repeated child activation、sibling scope不串值。

### 12.5 回归与架构

- pure skip、retry、interrupt resume、execution publication不回归；
- no `Any`/`object`/bare container/reflection/string discriminator/generic-erasing cast；
- no second store/resolver/runner/routing path；
- architecture gate确保runtime/resume/recovery只有 `routing.resolve_routing_facts()` 可解释target/data contribution；
- architecture gate确保candidate overlay没有 `lookup()`、substitution-only lookup或第二frame map；
- no State concrete mirror、journal、substitution marker、fake token；
- package public export仍只有 `Graph` facade。

## 13. 实施顺序

1. 泛型化 request并增加 negative typing fixture，保持 runtime行为不变。
2. 引入 provenance union并迁移 execution publication，补 integrity tests。
3. 增加 output exact admission 和 candidate，不安装 publication。
4. 增加 candidate overlay 与 whole-invocation duplicate admission。
5. 抽取 shared routing/data-contribution resolver，先保持现有 routing tests。
6. 接入 resume admission 与 recovery proof，补 data-target matrix。
7. 接入 per-scope exact commit 后 substitution installation。
8. 补 nested、loop、multi-scope failure 与 architecture gates。
9. 删除迁移临时分支；最终只保留一套 resolver 和 publication path。

## 14. 验证与交付门禁

在 `mote-kernel` 运行：

```bash
make check
```

在 monorepo root 运行：

```bash
pre-commit run --all-files
```

若 shared durable protocol 未修改，交付说明记录：只扩展 Python owner-internal request、opaque continuation 和 invocation-local frames；`GraphRunState`、command codec 与跨语言 durable protocol未改变，因而不更新 `conformance/`。若实际修改 durable protocol，必须同步 conformance并重新评审。

## 15. 实施停止条件

出现以下任一情况立即停止编码并回到设计评审：

- 需要修改 `GraphRunState` 保存 replacement value 或便利 marker；
- 需要 nullable/fake token、字符串 provenance discriminator 或第二 publication store；
- candidate availability无法通过窄 typed view接入现有 materialization；
- future proof需要读取candidate concrete frame或增加candidate `lookup()`；
- runtime 与 recovery无法共用 target/data-contribution resolver；
- duplicate proof无法在首个 commit 前覆盖所有 scope；
- 需要跨 scope compensation 或 facade transaction coordinator；
- parent substitution要求恢复、重启或改写 child run；
- strict generic只能通过 `Any`、`object` 或 erasing cast维持；
- 需要新增 public method、compatibility alias 或 parallel execution path。

## 16. 评审与交付结论

第二轮实施设计评审已确认：

1. candidate 与 provenance union 的 owner/shape；
2. candidate overlay 不是第二 store；
3. shared admission 与 routing resolver 的模块边界；
4. `resolve_routing_facts()` 的typed facts覆盖control、join、data、input与completion；
5. continuation与state-only pure skip都运行whole-future proof；
6. whole-invocation preflight覆盖duplicate与必达input proof；
7. admitted plan绑定provenance和expected revision，并机械提升confirmed record；
8. per-scope frame records原子构造并以internal invariant error防御；
9. continuation只验证当前可证明事实；
10. State保持单一 `SkippedGraphNode`；
11. nested/loop identity复用现有 coordinate；
12. 测试矩阵和门禁能阻止双路径、泛型擦除与 concrete truth双写。

production implementation 已完成；`make check`、strict Pyright、100% statement/branch coverage、package build/Twine 与 monorepo pre-commit均通过。shared durable protocol未修改：`GraphRunState`、command codec与跨语言 durable protocol保持不变，因此无需更新conformance artifact。
