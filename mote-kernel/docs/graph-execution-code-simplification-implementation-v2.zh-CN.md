# Graph execution 代码简化候选 A-v2 实施方案

## 1. 文档信息与当前裁决

- 状态：REVISED / READY FOR INDEPENDENT RE-REVIEW / NOT APPROVED FOR PRODUCTION
- 日期：2026-08-26
- production baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- 当前 target：删除 `_RootStateBinding` 与 `GraphRunContext.replace_root()`
- 已拒绝 target：新增 `ScopedStateIndex` / `ScopedStateBinding`
- 唯一状态 owner：现有 `GraphRunContext`
- 公共入口：`mote_kernel.execution.Graph`
- 当前 production/test diff：**空**
- requirements owner：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 总账索引：[语义保持型简化主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 历史来源：[Graph execution 代码简化候选调研](graph-execution-explosive-simplification-research.zh-CN.md)
- 独立评审：[A-v2 实施方案独立评审](graph-execution-code-simplification-implementation-v2-review.zh-CN.md)
- 评审回复：[A-v2 独立评审回复](graph-execution-code-simplification-implementation-v2-review-response.zh-CN.md)

用户已明确否决“新增 index 后再寻找删除面”的旧方案。A-v1 现由
[历史处置索引](graph-execution-code-simplification-implementation.zh-CN.md)提供稳定落点，五轮 review 与四份 response 只保留
历史审计价值，不向本方案传递 `PASS`、`KEEP` 或批准状态。本文件是 A-v2 唯一 versioned target owner；A-v1 与 A-v2 不再
共用一个可变路径。

本文是候选 A-v2 target 的唯一 implementation owner。新 target 不再合并 root/child、confirmed/planned 或 runtime/proof
结构，而是删除当前 confirmed root state 上没有语义增量的单字段 wrapper：

~~~text
GraphRunContext.root_binding: _RootStateBinding(state)
    ↓
GraphRunContext.root_state: GraphRunState
~~~

**设计结论：该方向存在真实净删除面。** 它保持 GraphRunContext 为唯一 mutable state owner，不新增类型、字段、包装、helper、
property、cache 或第二操作入口；同时删除一个 private dataclass、一个 dataclass field、一个单调用者 method、两个 wrapper
constructor sites 和测试中的一段重复 replacement 实现。

本文只形成可独立复审的 exact proposal。取得独立技术复审、requirements owner 准入与显式批准前，不修改 production 或 tests。

## 2. 先决原则

### 2.1 唯一真相

invocation-time confirmed root/child state 继续只由 GraphRunContext 持有：

- root：现有 context field 直接持有 GraphRunState；
- child：现有 tuple[ChildStateBinding, ...] 保持不变；
- continuation：只冻结 context 的 immutable boundary snapshot；
- planned successor：继续只属于 invocation；
- proof projection：继续只属于既有 proof owner。

删除 wrapper 不增加第二份 state，也不把 snapshot、planned state 或 proof state 提升为 runtime owner。

### 2.2 零负债

本单元禁止新增：

- dataclass、protocol、type alias 或 nominal wrapper；
- context/snapshot stored field；
- method、property、forwarder、compatibility alias 或第二 replacement entry；
- map、cache、隐藏 mutable state或双写/双读；
- Any、裸容器、反射或字符串 discriminator。

旧 root_binding 与 replace_root() 必须在同一原子 change unit 中归零，不保留迁移桥。

### 2.3 复用现有基础设施

coordinate-driven state lookup 与 replacement 继续使用现有 `GraphRunContext.state_at()` / `replace_state()`；需要直接投影 root
result 的既有 consumer 则从同一 context field 读取。测试 fixture 也调用原 `replace_state()` 实现，不再复制 root/child 分派和
`parent_activation` 保留逻辑。

### 2.4 优美、合理且规范

只有携带独立 invariant 的 binding 才保留 nominal type。ChildStateBinding 同时拥有 coordinate、parent activation 与 state，
因此保留；_RootStateBinding 只有一个 state field，没有独立 validation、identity、ordering 或 lifecycle invariant，因此删除。

### 2.5 `GSP-P01`–`GSP-P08` 适用性

本表只把 A-v2 edit surface 映射到
[requirements 第 3 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#3-行为保持义务)已有 ID，不重定义要求：

| Requirement | 适用性 | A-v2 保持义务与 evidence |
| --- | --- | --- |
| `GSP-P01` / `GSP-S01` | 适用 | `Graph`、Result/Continuation public type 与 error surface 不变；public new-run、mismatch 与 strict typing/architecture gate 覆盖 |
| `GSP-P02` / `GSP-S02` | **HARD KEEP** | `GraphRunState`、command、reducer、revision、run identity 与 protocol 均不修改；planned manifest 不含 `state/**` 或 `tests/state/**` |
| `GSP-P03` / `GSP-S03` | 适用 | `replace_state()` 仍只在 confirmed commit 后替换 context；fence/resume partial handoff 的次数、顺序和异常边界由三项 partial-prefix case 覆盖 |
| `GSP-P04` / `GSP-S04` | 适用 | 两个 continuation variants 只把 wrapper field 原位替换为同一 root value；seal、family token、frames、child bindings、Result projection 与 integrity 保持 |
| `GSP-P05` / `GSP-S05` | 适用 | family-driver 的 settlement/result projection 只改 root read；failure/interrupt/skip、availability 与 result order 不变 |
| `GSP-P06` / `GSP-S06` | 适用 | recovered continuation 的 structural admission、frame/child snapshot、state-only lineage 与错误边界不变；recovered 成功 case 与 tamper cases 覆盖 |
| `GSP-P07` / `GSP-S07` | 适用 | root→child canonical order、child coordinate、parent activation、nested snapshot 与 repeated-generation 行为不变 |
| `GSP-P08` / `GSP-S08` | 适用 | `GraphRunContext` 仍是唯一 confirmed state owner；无第二入口、第二 store、反射、类型擦除或逆向依赖 |

任一对应 stop condition 触发即停止实施；不得通过修改本表放宽 requirements owner。

### 2.6 `GSP-A06` 单项准入映射

| `GSP-A06` 要求 | 唯一证据位置 | 当前状态 |
| --- | --- | --- |
| exact signature 与 nominal input/output | 4.1–4.2 | 已设计 |
| 删除对象、最多新增对象与唯一 owner | 2.1–2.4、3、5.1 | 已设计；新增对象为 0 |
| 净复杂度 | 5.1–5.2 | 已设计 |
| 成功、失败与边界 characterization | 6.1 | 已固定 existing nodeid |
| exact-shape/tamper target | 6.2 | 与 production 原子落地；当前未实施 |
| exact changed-file manifest | 7.2 | 已设计 |

requirements owner 只将 A-v2 登记为 `PENDING / NOT APPROVED`。独立复审通过后仍须由 requirements owner 绑定 reviewed SHA，
并取得用户对该 exact target 的显式批准；review、response 或本表都不构成 production authorization。

## 3. 当前事实与真实删除对象

### 3.1 _RootStateBinding 没有独立语义

| 维度 | 当前事实 | 结论 |
| --- | --- | --- |
| stored field | 仅 state: GraphRunState | 与 payload 一一等价 |
| constructor validation | 无 | wrapper 不负责 admission |
| identity/order | 无 coordinate、activation 或排序 | 不承担 root identity |
| equality usage | admission 比较 snapshot.root_binding.state != state | 实际只比较 state |
| lifecycle | context 与 continuation snapshot 都只传递同一 state value | 没有独立失效时点 |
| public exposure | 无 | 可原子删除 |

root identity 继续由 root_scope_run(root_state.run_id) 推导；compiled family ownership 继续由 _CompiledFamilyIdentity 验证。
删除 wrapper 不移动这两个 owner。

### 3.2 当前重复 mechanics

当前路径先包装再在所有 consumer 解包：

~~~text
_new_context(state)
  → _RootStateBinding(state)
  → GraphRunContext.root_binding
  → consumer.root_binding.state

replace_state(root, state)
  → replace_root(state)
  → _RootStateBinding(state)
  → consumer.root_binding.state

_snapshot(context)
  → snapshot.root_binding
  → admit(...): snapshot.root_binding.state
  → GraphRunContext(snapshot.root_binding)
~~~

这不是不同生命周期的必要 projection，而是同一 immutable `GraphRunState` 的重复 wrap/unwrap。以 production source 的 exact
token 计，当前 `_RootStateBinding` 6 处、`root_binding` 18 处、`replace_root` 2 处，合计 26 处；wrapper constructor site 为 2。

此外，test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails 的 monkeypatch 当前重新实现
replace_state() 的 root/child 分派。该 fixture 可直接保存并调用原 method，删除测试中的第二份 replacement mechanics。

### 3.3 保留的 nominal boundary

| 结构 | 保留原因 |
| --- | --- |
| ChildStateBinding | child coordinate、parent activation 与 state 是同一 acknowledged binding |
| _CompleteContinuationSnapshot / _RecoveredContinuationSnapshot | 两种 admission evidence strength 不同 |
| _PlannedState | successor 在 commit confirmation 前只属于 invocation |
| RecoveryStateBinding | proof owner 的窄 nominal input |
| ScopedFrameIndex | concrete admitted frames 的唯一 owner |

### 3.4 被排除的相邻方案

| 方案 | 裁决 | 原因 |
| --- | --- | --- |
| 只删 `replace_root()`，保留 `_RootStateBinding` | 拒绝 | 仍保留两个 wrapper constructor site 与全部 wrap/unwrap，无完整净删除 |
| 保留 `replace_root()` 作为兼容入口 | 拒绝 | 与 `replace_state()` 继续形成同义 root replacement 入口 |
| 建立 root/child 通用 index 或新 binding | 拒绝 | 重现 A-v1 的新增 owner、type、field 与 allocation 风险 |
| 删除 `ChildStateBinding` 或 `replace_child()` | 拒绝 | child start 需要把 coordinate、parent activation 与 state 原子绑定，语义不等价 |
| 增加 `root_binding` property/alias | 拒绝 | 形成兼容层和第二表示，抵消删除收益 |

A-v2 因而是当前删除面上的帕累托改进：外部行为、错误边界与 owner 不变，所有结构指标只下降或持平；不存在为换取删除而
新增的抽象、状态、分支或迁移路径。

## 4. Exact target

### 4.1 GraphRunContext 直接持有 root state

目标 shape：

~~~python
class GraphRunContext(Generic[GraphValueT]):
    __slots__ = ("child_states", "family_identity", "frames", "recovered", "root_state")

    def __init__(
        self,
        family_identity: _CompiledFamilyIdentity,
        root_state: GraphRunState,
        frames: ScopedFrameIndex[GraphValueT],
        child_states: tuple[ChildStateBinding, ...],
        *,
        recovered: bool,
    ) -> None: ...

    def state_at(self, coordinate: ScopeRunCoordinate) -> GraphRunState:
        if not coordinate.scope:
            return self.root_state
        ...

    def replace_state(self, coordinate: ScopeRunCoordinate, state: GraphRunState) -> None:
        if not coordinate.scope:
            self.root_state = state
            return
        ...
~~~

replace_root() 直接删除。root replacement 的唯一 operation 继续是已有 replace_state()；child path、错误类型/文本和
parent_activation 保留逻辑不变。

### 4.2 Continuation snapshot 直接持有 root state

两个既有 snapshot variant 只做同字段替换，不增加字段：

~~~python
class _CompleteContinuationSnapshot(Generic[GraphValueT]):
    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_states: tuple[ChildStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT]

class _RecoveredContinuationSnapshot(Generic[GraphValueT]):
    family_identity: _CompiledFamilyIdentity
    root_state: GraphRunState
    child_states: tuple[ChildStateBinding, ...]
    frames: ScopedFrameIndex[GraphValueT]
~~~

admission 保持同一比较和首错：

~~~text
snapshot.family_identity is family_identity
snapshot.root_state == supplied state
~~~

通过后把 snapshot.root_state 直接交给 GraphRunContext。complete/recovered variant、seal、child tuple、frames、equality 语义和
error text 均不变。

### 4.3 Production consumer 原子迁移

| 文件 | exact edit | 删除/保持 |
| --- | --- | --- |
| execution/run_context.py | 删除 _RootStateBinding、replace_root()；root_binding 原子改名为 root_state；snapshot/context 直接持有 state | 删除 wrapper 与第二 replacement entry |
| execution/invocation.py | context.root_binding.state → context.root_state | planned projection 保持 |
| execution/family_driver.py | 三处 root state read 改为 context.root_state | driver/result 行为保持 |
| execution/facade.py | 两处 partial-handoff root read 改为 context.root_state | commit/install 顺序保持 |

不新增 production import、helper、branch 或 adapter。

### 4.4 Test fixture 原子迁移

tests/execution/test_graph_api.py 只迁移既有 private fixture：

1. snapshot `root_binding` 字段改为 `root_state`，直接安装 `GraphRunState`；
2. `handed_off.root_binding.state` 等断言改为 `handed_off.root_state`；
3. 普通 fixture 中直接调用 `context.replace_root(...)` 的位置改用已有
   `context.replace_state(root_scope_run(state.run_id), state)`；
4. multi-scope monkeypatch 中另一处 `replace_root(...)` 随重复分派一起删除：先保存原
   `GraphRunContext.replace_state`，记录调用后直接 delegate。

不新增 test class、test helper 或 test case；现有 behavior case 继续拥有断言。

### 4.5 Normative source 同步

docs/graph-node-input-output-contract-implementation.zh-CN.md 中四处 _RootStateBinding 描述原子改为“exact immutable root
GraphRunState value”。继续明确：

- Result 与 continuation snapshot 共享同一个 immutable root state value；
- admission 使用 structural equality，不要求 object identity；
- family token、snapshot variant、seal 与 child binding 不变；
- direct root state 不进入 public constructor 或第二 storage。

### 4.6 Complexity 配置同步

`pyproject.toml` 只做两类机械同步：

1. 将四个确定下降的 ratchet baseline 改为 `503/287/177/499`；
2. 按 implementation 后的实际行号重定位 `complexity_reviewed` 中仍然存在的 snapshot shape 与 `run_context.py` helper identity。

第二项不增加 reviewed candidate，也不改变任何 health disposition；目标必须仍为 `reviewed=51`、`unreviewed=0`、`stale=0`。
若 implementation 产生新 candidate 或需要新增 exception，本方案失败，不能用更新 allowlist 掩盖。

## 5. 净删除与复杂度账本

### 5.1 Operation-level ledger

| 项目 | Before | Target | Delta |
| --- | ---: | ---: | ---: |
| private root wrapper type | 1 | 0 | -1 |
| wrapper dataclass field | 1 | 0 | -1 |
| root-only replacement method | 1 | 0 | -1 |
| wrapper constructor sites | 2 | 0 | -2 |
| legacy production token occurrences（6 + 18 + 2） | 26 | 0 | -26 |
| root replacement production entries | replace_state + replace_root | replace_state | -1 |
| duplicated test replacement branch | 1 | 0 | -1 |
| GraphRunContext state storage fields | root 1 + child 1 | root 1 + child 1 | 0 |
| snapshot stored fields per variant | 4 | 4 | 0 |
| new type/field/method/helper/property/alias/cache | 0 | 0 | 0 |

字段改名是原位替换，不计为新增 field。任何为兼容旧名增加 property/alias 的实现均不符合 target。

### 5.2 Ratchet-level exact target

| Metric | 当前 baseline | Target | Delta |
| --- | ---: | ---: | ---: |
| top_level_definitions | 504 | 503 | -1 |
| type_definitions | 288 | 287 | -1 |
| dataclass_types | 178 | 177 | -1 |
| dataclass_fields | 500 | 499 | -1 |
| decision_points | 1327 | 1327 | 0 |
| logical_clone_pairs | 12 | ≤12 | ≤0 |
| record_shape_clone_pairs | 21 | ≤21 | ≤0 |
| thin_single_use_helpers | 17 | ≤17 | ≤0 |
| single_use_private_dataclasses | 1 | ≤1 | ≤0 |
| test_only_private_definitions | 0 | 0 | 0 |

implementation 必须同步下调 `pyproject.toml` 前四项 baseline，并机械重定位因源码行号移动而变化的既有 reviewed identity；
这是记录已实现净删除，不是上调门禁或新增 reviewed exception。若任一前四项未达到 exact target、其余指标增长，或
health 集合不再是 `51/0/0`，实施失败。

## 6. 行为保持证据

### 6.1 Exact executable cases

| 边界 | Exact nodeid | 必须保持 |
| --- | --- | --- |
| public new run | tests/execution/test_graph_api.py::test_graph_is_the_single_public_execution_facade_and_runs_plain_node_outputs | Graph 唯一入口、result/state/continuation 行为 |
| root/unknown child operation | tests/execution/test_frame_index_contract.py::test_run_context_rejects_access_or_replacement_before_child_start_acknowledgement | root lookup/replacement、unknown child error |
| multi-scope partial prefix | tests/execution/test_graph_api.py::test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails | confirmed prefix、frames、cause、输入 snapshot 不变 |
| root then child failure handoff | tests/execution/test_graph_api.py::test_root_resume_then_child_commit_failure_hands_off_a_pairable_latest_root_snapshot | latest root state 与原 child snapshot 可配对 |
| fence handoff | tests/execution/test_graph_api.py::test_failure_after_exact_fence_explicitly_hands_off_the_fenced_snapshot | confirmed root snapshot 与错误 handoff |
| immutable input continuation | tests/execution/test_graph_api.py::test_normal_resume_never_mutates_the_input_continuation_snapshot | 输入 snapshot identity 不变 |
| shared continuation | tests/execution/test_graph_api.py::test_shared_input_continuation_is_not_modified_by_independent_invocations | invocation 间不共享 mutable context |
| root/continuation mismatch | tests/execution/test_graph_api.py::test_run_rejects_a_continuation_bound_to_another_root_state | exact error type/text 与 pre-commit rejection |
| canonical Result projection | tests/execution/test_graph_api.py::test_awaiting_result_views_preserve_canonical_root_to_child_scope_order | root→child order 与 payload |
| recovered continuation success | tests/execution/test_graph_recovery_contract.py::test_repeated_nested_path_keeps_distinct_child_runs_and_latest_boundary[recovered] | state-only 建立 recovered lineage 后，既有 root state、frames 与 child snapshots 可被 continuation 成功 readmit 并继续执行 |
| recovered frame tamper | tests/execution/test_continuation_integrity.py::test_recovered_continuation_readmits_existing_frame_content | 不一致的 recovered graph-input frame 在继续执行前拒绝；该现有名称描述“重新校验”，实际是负向 case |
| validation precedence | tests/execution/test_continuation_integrity.py::test_continuation_validation_keeps_shape_before_canonicality_precedence | 首错、cause、state/snapshot 不变 |
| canonicality precedence | tests/execution/test_continuation_integrity.py::test_continuation_validation_keeps_canonicality_before_content_precedence | 首错、cause、state/snapshot 不变 |

architecture evidence 继续由：

- tests/architecture/test_graph_execution_ownership.py；
- tests/architecture/test_source_discipline.py；
- Pyright、complexity ratchet 与 complexity health。

### 6.2 Target exact-shape gate

production 原子单元必须在既有 `tests/architecture/test_graph_execution_ownership.py` 中新增一个窄 target test：

```text
tests/architecture/test_graph_execution_ownership.py::test_run_context_owns_direct_root_state_without_legacy_binding_or_entry
```

该 test 只复用文件内已有 AST helpers，不新增通用反射框架，并同时断言：

1. `GraphRunContext.__slots__` 只有既有五个 storage slots，其中 root slot 精确为 `root_state`，不存在 `root_binding`；
2. `GraphRunContext.replace_state()` 的 root branch 直接更新 `self.root_state`，不存在 `replace_root()` 或同义 alias/forwarder；
3. `_CompleteContinuationSnapshot` 与 `_RecoveredContinuationSnapshot` 都精确拥有
   `family_identity/root_state/child_states/frames`，且 `root_state: GraphRunState`；
4. `run_context.py` 不再定义或引用 `_RootStateBinding`、`root_binding`、`replace_root`。

失败条件是任一旧 symbol、兼容入口、额外 root storage、snapshot variant 漏迁移或字段类型偏离重新出现。该 gate 与 production
变更同一原子 unit 落地；当前 baseline 不伪称它已经存在或通过。

### 6.3 设计基线验证（2026-08-26）

| 验证 | 当前结果 |
| --- | --- |
| 上述 13 个 existing exact nodeid + 两个 architecture files | `40 passed in 0.81s` |
| `make check` | Ruff/format、Pyright `0 errors`、complexity gate `9 passed`、health `51/0/0`、全量 `843 passed`、100% coverage、build/twine 全部通过 |
| 本轮六份 docs writeback 文档的相对链接 | `104 checked / 0 missing` |
| monorepo root scoped pre-commit | 全部适用 hooks 通过 |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml`、`Makefile` 无 diff |

### 6.4 Source-level acceptance

实施后必须满足：

~~~text
production _RootStateBinding references = 0
production root_binding references = 0
production replace_root references = 0
GraphRunContext root state owner = exactly one field
root replacement operation = exactly replace_state()
compatibility aliases/forwarders = 0
~~~

该检查只验证本原子删除面，不新增通用 private-source-shape framework。

## 7. 原子范围与实施顺序

### 7.1 本次 docs-only change units 与 actual manifests

**A-v2 owner / generation-path 整改单元：**

~~~text
docs/graph-execution-code-simplification-implementation.zh-CN.md
docs/graph-execution-code-simplification-implementation-v2.zh-CN.md
docs/graph-execution-explosive-simplification-research.zh-CN.md
~~~

**Requirements pending-registration 单元：**

~~~text
docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
~~~

**独立 response 单元：**

~~~text
docs/graph-execution-code-simplification-implementation-v2-review-response.zh-CN.md
~~~

**导航同步单元：**

~~~text
docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
~~~

四个 unit 不互相冒充 target owner、requirements disposition、review response 或导航事实源；production/tests 均不进入本轮
actual manifest。

### 7.2 批准后的 planned implementation manifest

~~~text
src/mote_kernel/execution/run_context.py
src/mote_kernel/execution/invocation.py
src/mote_kernel/execution/family_driver.py
src/mote_kernel/execution/facade.py
tests/execution/test_graph_api.py
tests/architecture/test_graph_execution_ownership.py
docs/graph-node-input-output-contract-implementation.zh-CN.md
pyproject.toml
~~~

只有 actual diff 证明必须同步额外 normative owner 时才能停止并重新评审；不得静默扩大 manifest。

### 7.3 原子实施顺序

1. 在 run_context.py 删除 wrapper 与 replace_root()，直接建立 root_state；
2. 同一 diff 迁移 continuation admission/snapshot 和全部 production consumers；
3. 同一 diff 迁移既有 test fixture，删除重复 replacement 分支，并落地 6.2 的窄 exact-shape gate；
4. 同一 diff 同步 normative source、降低 complexity baseline，并机械重定位仍存在的 reviewed identity；
5. 运行 exact cases、完整 checks 和 source-level acceptance；
6. 只有全部通过才写 implementation owner 的 actual ledger。

任何中间状态都不得保留 root_binding alias 或新旧双路径。

### 7.4 回滚

本方案尚未实施，无 runtime rollback。批准后的 change unit 必须作为一个整体回滚；不设计 compatibility rollback。

## 8. 停止条件与完成定义

出现任一情况立即停止并回到设计评审：

1. 需要新增 type、stored field、method、property、helper、alias、cache 或第二入口；
2. 需要改变 public Graph、Result 或 Continuation surface；
3. 需要改变 child binding、planned state、proof state 或 frame owner；
4. 需要改变 error type/text/cause、validation precedence 或 commit/state/frame 顺序；
5. 前四项 complexity target 未精确下降，或其余六项增长；
6. planned manifest 之外出现无法解释的 production/test 改动。

完成必须同时满足：

- 所有旧 wrapper/entry symbol 归零；
- GraphRunContext 仍是唯一 confirmed state owner；
- exact behavior、architecture、typing、complexity、coverage、build 与 scoped pre-commit 全部通过；
- 没有 compatibility layer、第二表示或临时债务；
- 独立技术复审通过、requirements owner 将当前 reviewed SHA 的 `GSP-A06` 标记为 satisfied，并取得显式实施批准。

## 9. 当前 disposition

~~~text
candidate A v1 ScopedStateIndex = REJECTED / RETIRED
candidate A v2 root-state de-wrapper = REVISED / READY FOR RE-REVIEW
requirements GSP-A06 = PENDING / NOT APPROVED
production/tests = NO CHANGE YET
implementation authorization = NOT GRANTED
~~~

**当前结论：新方案具有真实净删除面，但本轮只完成设计。独立评审与显式批准前不得实施。**
