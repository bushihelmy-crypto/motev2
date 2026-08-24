# S15 Recovery worklist 分支结果归一化实施方案

## 1. 文档信息

- 状态：`IMPLEMENTED / VERIFIED / IMPLEMENTATION-OWNER WRITEBACK COMPLETE`
- 日期：2026-08-24
- 单元：Graph 语义保持型简化 S15（P2）
- 源码基线：Git `7247a93485f30746638a5168e06be8766a64a120`
- 基线文件：`src/mote_kernel/execution/engine/recovery.py`
- 基线 SHA256：`d77df79d3f94ca973b29945a3abddf3382b280ed8da7f208ad455757c1d9514e`
- 唯一 production target：`_prove_scope()` 内的 worklist 编排；公共与 module-level private signature 均不改变
- State/持久化边界：`HARD KEEP`；不修改 State、command、reducer、commit、protocol、Store 或 persistence
- Error recovery 边界：只保持并简化现有 recovery preflight，不新增自动 retry、failover、checkpoint 或第二 runner
- Complexity/ratchet：明确排除；automated complexity gate、health、baseline、ratchet、limit 和 hook 均不属于 S15 准入或交付证据
- Legacy/private-shape gate：明确排除；不新增、扩写或依赖冻结 private local、源码行数或表达式布局的测试

关联 owner：

- [Graph 语义保持型简化 requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)：唯一拥有
  `GSP-P01`–`GSP-P08`、`GSP-A06` 与批准状态；当前已将 S15 限定批准为 reviewed SHA 对应的 exact target。
- [Graph 语义保持型简化主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)：只拥有总账、阶段顺序
  和本文索引，不复制 S15 exact target。
- [Graph Node I/O normative implementation](graph-node-input-output-contract-implementation.zh-CN.md)：唯一拥有当前 recovery
  worklist、boundary、ordering、budget 与 fail-closed 行为；S15 不提前把未来内部代码布局写成当前规范。
- 未来 S15 review/response：只记录裁决和证据，不拥有 target shape 或批准状态。

本文是 S15 exact target、nominal 输入/输出、结构净删除账本、等价证明、behavior/tamper evidence、planned manifest、
门禁和停止条件的**唯一 owner**。主实施方案只保留链接；requirements 只记录批准状态；review 只记录裁决；第 16 节记录
批准后的 implementation-owner writeback。批准前不得修改 production 或 tests；当前批准范围、实际 manifest 与验证结果以第 16 节和独立
[验收记录](graph-semantics-preserving-simplification-s15-implementation-acceptance.zh-CN.md)为准。

## 2. 结论

S15 不应机械提取 terminal/active/settled/executable 四个 handler。当前 production 已经分别复用：

- `_expand_live()`：active execution 的 success-route successor mechanics；
- `_resolve_quiescent()`：settled frontier 的 routing/completion resolution；
- `_expand_quiescent_executable()`：executable frontier 的 planner、resource、nested 与 limit expansion。

真正重复的是 `_prove_scope()` 中三个非 terminal 分支各自执行的 budget admission、boundary/work-item 判别和 enqueue。
Exact target 只做两件事：

1. 三个 existing mechanics 统一投影为同一个 invocation-local、两成员 nominal successor tuple：
   `_RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT]`；
2. loop owner 在唯一位置先按 tuple 长度调用 budget，再按原 tuple 顺序把 boundary 加入 `boundaries`、把 work item 交给
   收窄后的 scalar `enqueue()`。

不新增 handler、DTO、dataclass、type alias、protocol、callback、context bag、cache、index、field、property、import 或公共入口。
worklist heap、`seen`、sequence tie-break、enqueue、budget、branch precedence、boundary set 和最终 canonical sort 仍全部由
`_prove_scope()` 单独拥有。该目标复用现有 execution/recovery 基础设施，并删除 loop 内重复控制 mechanics，不建立第二解释器。

### 2.1 明确拒绝的替代方案

| 候选 | 裁决 | 原因 |
| --- | --- | --- |
| 新增 terminal/active/settled/executable 四个 handler | 拒绝 | 三个非 terminal mechanics 已有 owner；再包装只会增加函数、参数传递和控制跳转 |
| 新增 `_RecoveryExpansion`/`_BranchResult` DTO | 拒绝 | 只包装现有 tuple union，并会复制 successor count/partition 事实 |
| 新增 enum/string branch discriminator | 拒绝 | branch precedence 已由 nominal State/status 判断拥有；tag 会形成第二事实 |
| callback/generic dispatcher | 拒绝 | 擦除具体 branch 输入、错误边界和 Pyright narrowing，违反窄 typed boundary |
| 把 queue、seen 或 budget 搬进 class/context | 拒绝 | 扩大生命周期并分散唯一 loop owner |
| 提取 terminal optional-result helper | 拒绝 | 增加 `None` 分流与参数面，不能降低净分支或变量 |
| 合并/重写三个 existing expansion helper | 拒绝 | 越出 S15，复制或改变 planner/routing/nested owner |

保留的 local `enqueue(candidate)` 不是 forwarding helper：它唯一封装 transfer-key、monotonic sequence 与 heap entry 三者必须同步
构造的 queue invariant，并捕获 invocation-local `pending`/`sequence`。Target 删除其 batch loop，把输入从 tuple 收窄为一个
`_RecoveryWorkItem[GraphValueT]`；它不成为 module-level definition，也不扩大所有权或生命周期。

## 3. `GSP-P01`–`GSP-P08` applicability

本表只引用 requirements ID，不复制 requirement 正文：

| Requirement | S15 裁决 | Exact target / evidence 责任 |
| --- | --- | --- |
| `GSP-P01` | 适用 | public `Graph.run()` surface、Result 与 error taxonomy 不变；public recovery cases保持相同 result/error |
| `GSP-P02` | 不触及 / `HARD KEEP` | 不修改 `GraphRunState`、command、reducer、revision、codec、State tests 或 protocol；manifest/source review提供 negative evidence |
| `GSP-P03` | 适用 | preflight 仍早于 fence/resume/claim/child start/resource/node mutation；budget 与缺值异常继续在同一 mutation-free 边界传播 |
| `GSP-P04` | 适用 | availability、boundary、Result/Continuation 及 concrete frame identity不变；target不新增或删除 stored fact |
| `GSP-P05` | 适用 | failure/interrupt/skip route、availability 与 awaiting/aborted/completed projection保持；不捕获或重分类现有错误 |
| `GSP-P06` | 适用，核心 | full-semantic seen、reachable boundary、branch precedence、canonical traversal、4096 budget和malformed error边界逐项证明 |
| `GSP-P07` | 适用 | nested boundary线性组合、resource waiter、canonical queue tie-break、repeated child与limit传播保持 |
| `GSP-P08` | 适用 | `_prove_scope()`仍是唯一 worklist owner；复用现有 typed planner/routing/settlement/nested helpers；无第二 runner/store、反射或类型擦除 |

`GSP-P02` 的“不触及”不是免除约束：任何 State/Store/protocol/persistence diff 都立即停止 S15。以上矩阵、第 7 节结构账本、
第 8 节 case-level evidence和第 11 节 manifest共同满足 S15 对 `GSP-A06` 的设计责任；技术评审通过也不能替代 requirements
owner 的显式批准。

## 4. 当前 production 审计与唯一事实

### 4.1 当前 branch precedence

`_prove_scope()` 对每个未见过的 transfer state 按下列固定顺序裁决；target 不改变任何一行的相对位置：

| 优先级 | 当前 predicate | 当前 disposition / error owner |
| --- | --- | --- |
| 0 | `transfer in seen` | 跳过 exact structural-equal transfer；`seen` owner不变 |
| 1 | root/scope `GraphRunStatus.COMPLETED` | 先由 shared `graph_outputs_available()`检查历史输出；缺失时原样抛 `GraphValueUnavailableError`，否则收集 COMPLETED boundary |
| 2 | `GraphRunStatus.ABORTED` | 收集 ABORTED boundary |
| 3 | `frontier_status(...) == AWAITING_RESUME` | 收集 AWAITING_RESUME boundary |
| 4 | `current.execution is not None` | active 优先于 settled/executable；空 `item.live` 原样抛 `SnapshotMismatchError`，否则调用 `_expand_live()` |
| 5 | `status == SETTLED` | 调用 `_resolve_quiescent()`；结果可能是一个 work item 或 boundary |
| 6 | 其余 non-terminal state | 调用 `_expand_quiescent_executable()`；结果可能混合 work item 与 boundary |

特别地，target 仍让 AWAITING_RESUME 先于 active，让 active 先于 SETTLED。不得把它们改成按 enum 排序、dict dispatch、match tag
或互相独立的 handler 自行判断；malformed State 的首错和 normal State 的可达 branch都依赖该 precedence。

### 4.2 当前 owner inventory

| 事实/mechanics | 唯一 owner | S15 处理 |
| --- | --- | --- |
| pending priority heap | `_prove_scope().pending` | 保持一个 heap，不新增 queue/context |
| canonical queue key | existing `recovery_traversal_key(_transfer_state(...))` | enqueue继续调用同一 producer一次 |
| equal-state dedup | `_prove_scope().seen` + `RecoveryTransferState` nominal equality | shape、hash和检查时点均不变 |
| tie-break sequence | `_prove_scope().sequence` | initial `0` 与每次 work-item enqueue 的 `+1` 保持 |
| proof safety budget | `_RecoveryFamily.budget` / `_RecoveryProofBudget.admit()` | initial admit保持；三个非 terminal branch共用一个 admit site |
| live expansion | `_expand_live()` | 原样复用，不搬迁 mechanics |
| settled resolution | `_resolve_quiescent()` | 原样复用，结果只包成 singleton tuple |
| executable/nested expansion | `_expand_quiescent_executable()` | 原样复用，不复制 planner/resource/nested/limit interpretation |
| boundary identity与排序 | `_ScopeBoundary` + `_prove_scope().boundaries/final sort` | set、kind precedence和sort key不变 |

Target successor tuple不是新事实源。它只是三个现有 producer在当前 loop iteration 内的临时统一消费域；其元素仍是现有 nominal
objects，tuple不保存、不返回、不缓存、不进入 equality、State、Continuation 或 Graph instance。

## 5. Exact target shape

### 5.1 保持不变的 function signature

```python
def _prove_scope(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    availability: RecoveryAvailabilityCoordinates[GraphValueT],
    family: _RecoveryFamily,
) -> tuple[_ScopeBoundary[GraphValueT], ...]: ...
```

输入与输出保持现有 nominal types，不引入新 TypeVar 或 alias。`_RecoveryFamily`、`_RecoveryWorkItem[GraphValueT]`、
`_ScopeBoundary[GraphValueT]`、`RecoveryTransferState[GraphValueT]` 与 `RecoveryTraversalKey` 的定义和 generic关系全部不改。

### 5.2 收窄后的 local enqueue

```python
def enqueue(candidate: _RecoveryWorkItem[GraphValueT]) -> None:
    nonlocal sequence
    sequence += 1
    heappush(
        pending,
        (
            recovery_traversal_key(_transfer_state(scope_run, candidate, family)),
            sequence,
            candidate,
        ),
    )
```

删除当前 `items: tuple[...]` 参数和 closure 内部 `for candidate in items`。Queue insertion 的 key构造、sequence increment、
heap tuple shape及其先后顺序逐字保持。`enqueue()`仍是 `_prove_scope()` local closure，不导出、不复用到其他 owner。

### 5.3 唯一 non-terminal result pipeline

terminal/awaiting branches保持当前原文和 `continue`。其后的 exact target固定为：

```python
successors: tuple[
    _RecoveryWorkItem[GraphValueT] | _ScopeBoundary[GraphValueT],
    ...,
]
if current.execution is not None:
    if not item.live:
        raise SnapshotMismatchError("recovery simulated execution has no legal live task")
    successors = _expand_live(graph, item, scope_run, family)
elif status is GraphFrontierStatus.SETTLED:
    successors = (_resolve_quiescent(graph, item, scope_run, family),)
else:
    successors = _expand_quiescent_executable(graph, item, scope_run, family)
family.budget.admit(len(successors))
for successor in successors:
    if isinstance(successor, _ScopeBoundary):
        boundaries.add(successor)
    else:
        enqueue(successor)
```

该 union只有两个现有 nominal成员，精确等于 existing `_expand_quiescent_executable()` 的当前返回域，不是 wide union或 string
discriminator。不得改为 `object`、`Any`、bare tuple、cast、protocol、callback、tagged tuple 或 context bag。

### 5.4 新增上限与禁止漂移

Exact target上限固定为：

```text
new module-level function/class/dataclass/type alias/protocol: 0
new local handler/closure: 0
new field/property/cache/index/stored fact: 0
new import/public export: 0
new branch/loop/callback/string discriminator: 0
new State/command/reducer/protocol/persistence artifact: 0
new or modified test: 0
new or modified normative behavior document: 0
```

唯一新增源码形状是一个 invocation-local `successors` annotation；它替代三个 branch-local consumption blocks。若 Pyright要求
新增 alias/cast/`object` 才能接受 assignment，立即停止并重新设计，不得通过类型擦除实施。

## 6. 语义等价证明

### 6.1 Branch selection 等价

terminal COMPLETED、ABORTED、AWAITING_RESUME blocks完全不动。对其余 state，target只把当前连续的
`if active → continue; if settled → continue; executable default` 改写为等价的 `if/elif/else`，predicate及顺序相同：

```text
active: current.execution is not None
settled: active为false 且 status is SETTLED
executable: active为false 且 status不是SETTLED
```

因此每个 nominal或malformed current state仍恰好调用同一个 existing expansion owner一次。没有 handler重新读取 status，也没有
第二 classification projection。

### 6.2 Budget 等价与错误时点

| 路径 | 当前 admitted count | Target admitted count | admit 相对时点 |
| --- | ---: | ---: | --- |
| initial item | `1` | `1` | pending traversal前，保持 |
| active | `len(_expand_live(...))` | `len(successors)` | expansion完成后、任何 enqueue前，保持 |
| settled | `1` | singleton tuple的 `len(successors) == 1` | resolution完成后、boundary/enqueue前，保持 |
| executable | `len(_expand_quiescent_executable(...))` | `len(successors)` | expansion完成后、任何 boundary/enqueue前，保持 |
| terminal/awaiting item | 本 iteration不再 admit | 相同 | 该 item在initial或其producer处已计入，保持 |

`_RecoveryProofBudget.admit()`仍在任何本轮 successor写入 heap或boundary set之前执行。超出4096时继续原样抛
`ExecutionLimitError("recovery proof exceeded its bounded transfer-state budget")`；不得改为按 work item逐个 admit、只计算 queue item、
跳过 boundary、在dispatch后admit或吞并该异常。

### 6.3 Enqueue、heap 与 traversal ordering 等价

当前 batch enqueue 对有序 tuple `(x0, ..., xn)`执行：

```text
sequence += 1; heappush(key(x0), sequence, x0)
...
sequence += 1; heappush(key(xn), sequence, xn)
```

Target outer successor loop按同一 tuple顺序对每个 work item调用 scalar enqueue，产生完全相同的 increment和`heappush`序列。
Active producer只返回 work item；settled producer返回 singleton；executable producer原本已逐元素判别。因此：

- 每个 work item 的 `RecoveryTraversalKey`、sequence tie-break和heap entry不变；
- boundary不进入heap，work item不进入boundary set；
- mixed executable result的原始顺序不变；
- `heappop()`、full-semantic `seen`检查与最终boundary sort不变；
- key collision仍由sequence稳定线性化，unequal transfer state仍由full equality分别访问。

不得用 set/dict partition、两个comprehension或先收集再排序替代单遍dispatch；这些方案会重复扫描或改变sequence assignment。

### 6.4 Boundary 与 error recovery 等价

Target不构造新 boundary，也不改变 `_boundary()`、`_ScopeBoundaryKind`、`_ScopeBoundary` equality或final sort。以下异常仍由原owner、
以原type/text和precedence直接传播：

- completed history缺失：`graph_outputs_available()`后的 `GraphValueUnavailableError`；
- active execution没有legal live task：`SnapshotMismatchError`；
- planner/superstep或proof budget：existing `ExecutionLimitError`；
- routing/nested/materialization/resource malformed或缺值：existing shared helper error；
- reducer/claim/settlement invariant：existing reducer/projection error。

S15不增加 `try/except`、fallback、retry、logging side effect或错误映射。Preflight仍是mutation-free proof：内部 reducer只构造模拟 successor，
不提交、不安装memory frame、不持久化，也不代表kernel crash recovery保证。

## 7. 零新增负债与结构净删除账本

下表以第1节源码基线和第5节exact target的AST/source probe计数。Decision point口径与当前结构扫描一致：`if/for/while/ifexp/`
`comprehension/except`各计一项，bool/match按分支计；semantic AST node排除Load/Store/Del context。固定数字用于证明target净下降，
不是新增永久complexity或legacy gate。

| 结构项 | Before | Target | 净变化 |
| --- | ---: | ---: | ---: |
| module-level function definitions | 23 | 23 | 0 |
| 新 module/local branch handler | 0 | 0 | 0 |
| 新 DTO/dataclass/type alias/protocol | 0 | 0 | 0 |
| `_prove_scope()` decision points | 13 | 11 | -2 |
| `_prove_scope()` semantic AST nodes | 351 | 323 | -28 |
| `_prove_scope()` `ast.Name(Store)` identities | 14 | 13 | -1（删除batch-loop `candidate` store） |
| enqueue内部batch loop | 1 | 0 | -1 |
| non-initial budget admit sites | 3 | 1 | -2 |
| `_prove_scope()` total budget admit sites | 4 | 2 | -2 |
| enqueue call sites | 3 | 1 | -2 |
| successor `isinstance(..., _ScopeBoundary)` sites | 2 | 1 | -1 |
| existing expansion owner calls | 3 | 3 | 0 |
| worklist/seen/boundary full scan或第二index | 0 | 0 | 0 |
| field/cache/stored fact/import/public export | 0 | 0 | 0 |

隔离 exact-target feasibility diff为`19 insertions / 24 deletions`，production净删5行；行数只作补充，不替代上述结构账本。
Probe已经通过single-file Pyright、Ruff check/format、53个recovery focused cases、10个active architecture contract cases和全部563个
`tests/execution` cases。该段记录的是批准前的 feasibility probe；当时它不构成 production implementation、T0通过、技术评审或
`GSP-A06`批准。实际 implementation 与验收结果已在第 16 节独立记录。

零负债裁决同时要求：target不得把现有三个 expansion owner之一包装成single-use top-level helper，不得新增result DTO或callback，
不得修改独立 complexity framework的Makefile、ratchet、baseline、limit或测试。Implementation actual diff若不能达到本表全部上限，
即使测试绿色也停止。

## 8. Behavior、boundary 与 tamper evidence

### 8.1 当前 baseline

2026-08-24在第1节 production基线上原样运行：

```text
tests/execution/engine/test_recovery_identity.py
tests/execution/engine/test_recovery_boundaries.py
tests/execution/test_graph_recovery_contract.py
→ 53 passed

第13.2节10个active architecture contract nodeids
→ 10 passed

tests/execution
→ 563 passed

State single-owner case + tests/state/graph_state
→ 207 passed
```

Baseline只证明当前行为，不冒充target已实施。Implementation必须在同一 production unit上复跑至少以下case-level evidence：

| 义务 | Exact existing case |
| --- | --- |
| budget count与fail closed | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_has_a_bounded_transfer_state_budget` |
| canonical queue completion | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_uses_one_canonical_completion_order_for_plain_nodes` |
| full-semantic seen dedup | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_deduplicates_routes_with_the_same_successor_state` |
| terminal completed/aborted | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children` |
| awaiting boundary | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_propagates_an_awaiting_child_boundary` |
| mixed boundary/work-item linearization | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_linearizes_completed_and_aborted_child_possibilities` |
| completed output missing error | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_completed_child_without_output_history` |
| parallel completion-order merge | `tests/execution/test_graph_recovery_contract.py::test_recovery_worklist_merges_parallel_completion_orders_by_full_semantics` |
| exit branch优先于limit alternative | `tests/execution/test_graph_recovery_contract.py::test_recovered_future_exit_stops_before_an_alternative_limit_branch` |
| quiescent exact planner limit | `tests/execution/test_graph_recovery_contract.py::test_quiescent_recovered_seed_at_limit_has_no_mutation` |
| active precedence/fence/limit | `tests/execution/test_graph_recovery_contract.py::test_active_recovered_seed_fences_before_the_same_limit_boundary` |
| nested limit boundary传播 | `tests/execution/test_graph_recovery_contract.py::test_nested_child_limit_propagates_without_parent_failure_or_abort` |

### 8.2 Exact target predicates与tamper能力

S15不新增private-source/AST pytest。下列predicate只在implementation actual diff/source review中一次性核对，并与上表behavior cases
共同构成 `GSP-A06` exact-shape/tamper evidence：

| ID | Target predicate | 可杀死的错误变体 |
| --- | --- | --- |
| `S15.a` | `_prove_scope()`仍唯一拥有一个pending heap、一个seen set、一个boundary set、一个sequence和一个while worklist | 第二queue/index/context、seen搬迁或branch handler自建遍历 |
| `S15.b` | `enqueue` exact输入为一个 `_RecoveryWorkItem[GraphValueT]`，只有一个call site；key/sequence/heappush各一个且顺序不变 | batch loop残留、sequence后移、非canonical key或多enqueue owner |
| `S15.c` | initial `budget.admit(1)`一次，non-terminal `budget.admit(len(successors))`一次且位于dispatch前 | branch少计boundary、settled非1、dispatch后才超预算 |
| `S15.d` | 三个existing expansion owner各调用一次；active → settled → executable precedence保持 | duplicate mechanics、handler重判status、active/settled倒序 |
| `S15.e` | `successors` exact为两成员generic nominal tuple；只有一个boundary discriminator和一个dispatch loop | `Any`/`object`/cast/tag、两个partition scan或work item/boundary错投 |
| `S15.f` | module-level function/class/alias/import集合与baseline相同；State/protocol/persistence path零变化 | wrapper/DTO/new owner、第二runner/store或typing debt |

Tamper evidence不靠local名称本身放行。比如改名但仍有三个budget blocks不满足`S15.c`；把tuple换成DTO不满足`S15.e/f`；
删掉budget会被bounded-budget behavior case拒绝；颠倒active/settled会被active recovery与limit cases拒绝；错误partition会被mixed nested、
canonical completion与terminal cases拒绝。一次性source review只确认结构净删除，current behavior cases确认可观察语义。

禁止把上述predicate转成永久测试去断言 `_prove_scope` 源码行数、local名称、AST节点数、helper名称、union文本或具体表达式布局。
未来实现若需要这种legacy gate才能证明正确，说明nominal behavior evidence不足，应停止并重新评审。

## 9. State、持久化与错误恢复硬边界

S15 planned production manifest只能包含execution-owned `recovery.py`，并遵守：

- 不修改 `src/mote_kernel/state/**`、`tests/state/**`、GraphRunState/command/reducer/validation、revision或codec identity；
- 不新增 Store、repository、journal、event log、checkpoint、snapshot service、database、persistence port/backend、retry queue或exactly-once层；
- 不把availability、work item、boundary、pending heap、seen、sequence或budget写入State、Graph instance、global registry或continuation；
- 不改变commit callback、candidate structural acknowledgement、durable-first/memory-install顺序，且不把callback描述成持久化保证；
- 不新增`Graph.resume()`、recovery runner、automatic retry/backoff、Kernel failover policy或错误分类策略；
- 不捕获 shared helper错误，不把 `GraphValueUnavailableError`、`SnapshotMismatchError`、`ExecutionLimitError`互相映射；
- 不把preflight模拟reducer successor描述成已提交、已持久化或可跨进程自动恢复的事实。

当前 error recovery含义保持为：调用方显式提供authoritative State、合法continuation/frames与resume action后，现有Graph执行入口先做
whole-invocation preflight；证明通过才进入existing concrete transaction。S15只简化该proof的内部队列分支归并，不扩大可恢复范围。

## 10. 唯一真相与 normative 同步裁决

S15改变的是private loop代码布局，不改变当前 recovery type shape、stored fact、错误文本、budget常量、ordering规则或公共行为。
因此获批后的production implementation**不修改**architecture、Node I/O、skip-output、State/protocol或README normative文档；修改它们
会制造无必要diff或第二真相。

文档owner固定为：

| 内容 | 唯一 owner |
| --- | --- |
| `GSP-Pxx`/`GSP-A06`与批准状态 | requirements |
| S15 future target、结构账本、evidence、manifest、gate、writeback | 本文 |
| 总账、阶段顺序、S15链接与生命周期索引 | 主实施方案 |
| 当前 recovery behavior/type truth | existing normative source + production/characterization |
| review裁决 | future S15 review record |

若实施时发现必须改变任何 normative behavior才能完成，不得通过同步文档追认；直接触发停止条件并保持production现状。

## 11. Atomic change units 与 exact manifests

### 11.1 当前 design/index unit

本次docs-only单元的exact planned/actual manifest为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s15-implementation.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

前者唯一拥有target；后者只增加链接、owner delegation和未批准生命周期索引。不得把requirements、production、tests、历史review、
README、complexity或用户工作树中其他已修改文件列入本单元manifest。

### 11.2 独立技术评审 unit

未来技术评审如创建，manifest只能包含实际新增的S15 review record，例如：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s15-implementation-review.zh-CN.md
```

Review必须绑定本文SHA256并裁决exact target；不复制target，不修改requirements，不自批准。若有owner整改，另以只包含本文的docs-only
writeback unit落地，再重新绑定新SHA评审。

### 11.3 Requirements approval unit

技术评审通过且用户显式批准后，requirements owner才可用独立unit记录：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

只记录`GSP-A06 SATISFIED / APPROVED — 仅限reviewed S15 exact target SHA`，不复制本文算法。批准前production/tests保持不变。

### 11.4 Production implementation unit

批准后的exact planned manifest只有：

```text
mote-kernel/src/mote_kernel/execution/engine/recovery.py
```

不新增或修改tests：第8节existing behavior cases已覆盖target；不新增legacy/private-shape gate。不修改normative文档：第10节已证明
current behavior/type truth不变。不修改complexity framework、State、protocol、README或本设计文档。若actual diff需要第二文件，先停止并
重新评审manifest，不能以“同步”名义扩大。

### 11.5 Implementation owner writeback与总账索引

Production与全部适用门禁通过后，implementation owner writeback是独立docs-only unit：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s15-implementation.zh-CN.md
```

它记录implementation commit、actual diff/ledger、source review和gate结果。随后主实施方案如需更新生命周期，只以自身一个文件形成独立
index unit。两者不得与production commit或review audit合并，也不得累计历史manifest。

## 12. 批准后的实施顺序

1. 校验production `recovery.py`仍等于第1节baseline或重新生成baseline、diff并回到技术评审；不得把漂移静默并入S15。
2. 原样运行第8.1节focused baseline，确认branch、budget、ordering、nested和error behavior均绿。
3. 只在`_prove_scope()`内把local `enqueue`收窄为scalar input并删除batch loop。
4. 增加exact `successors` annotation，把active/settled/executable三个producer改为`if/elif/else`；不改existing helper。
5. 保留一个common budget call和一个single-pass nominal dispatch；不先保留旧blocks形成双路径。
6. 运行single-file Pyright/Ruff和focused behavior，先关闭typing与错误precedence。
7. 按第7节逐项核对actual结构账本，按第8.2节完成一次性source review；不生成legacy test。
8. 运行第13节全部适用门禁并核对第11.4节one-file actual manifest。任一失败整体回退本implementation unit。
9. production单独提交后再做第11.5节owner writeback；不得amend混入design/review/approval。

## 13. Verification gates

### 13.1 Gate 分类

```text
REQUIRED: current recovery behavior、error/budget/ordering boundary、strict typing、active generic/dependency/owner/
          source-discipline、lint、format、build/package、State/no-persistence negative gate、scoped repo checks
REQUIRED: 第7节manual structural ledger与第8.2节一次性actual diff/source review
USER-EXCLUDED: automated complexity gate、health、baseline、ratchet、limit和hook，无论既有还是拟新增
USER-EXCLUDED: legacy/private-source-shape gate，无论既有还是拟新增
```

完整`make check`当前无条件包含用户明确排除的`complexity-ratchet`，因此不属于S15 gate；不得运行后忽略失败再冒记为通过。
第13.2节逐项覆盖其余current behavior、typing、lint、format、build/package与active architecture contract。S15不得新增、修改、
点名依赖或用legacy/private-shape case证明target；零负债只由第7节exact结构账本、第8节behavior/tamper evidence和一次性actual
diff/source review共同闭合。排除automated complexity不放宽任何结构上限。

### 13.2 Focused current-contract gates

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/test_graph_recovery_contract.py

python -B -m pytest -q -p no:cacheprovider \
  tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types \
  tests/architecture/test_dependency_direction.py::test_execution_does_not_depend_on_domain_packages \
  tests/architecture/test_dependency_direction.py::test_graph_definition_layer_does_not_depend_on_runtime_execution_modules \
  tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners \
  tests/architecture/test_graph_execution_ownership.py::test_executor_does_not_apply_state_or_own_persistence \
  tests/architecture/test_graph_execution_ownership.py::test_recovery_consumes_shared_claim_and_settlement_lowering \
  tests/architecture/test_source_discipline.py::test_imports_form_a_contiguous_module_header \
  tests/architecture/test_source_discipline.py::test_dynamic_import_and_reflection_escape_hatches_are_forbidden \
  tests/architecture/test_source_discipline.py::test_internal_any_is_forbidden \
  tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner

python -B -m pytest -q -p no:cacheprovider tests/execution
python -B -m pytest -q -p no:cacheprovider tests/state/graph_state
python -B -m ruff check src/mote_kernel/execution/engine/recovery.py
python -B -m ruff format --check src/mote_kernel/execution/engine/recovery.py
pyright
python -B -m build --no-isolation
python -B -m twine check dist/*
```

这些architecture nodeids验证仍有效的generic、dependency、single execution/State owner、no-persistence和source discipline，
不冻结S15删除的private batch loop、local名称或具体表达式形状。

### 13.3 Repository、manifest 与 whitespace gates

从monorepo root对第11.4节one-file manifest运行；只跳过用户明确排除的complexity hook：

```bash
cd ..
SKIP=kernel-complexity pre-commit run --files \
  mote-kernel/src/mote_kernel/execution/engine/recovery.py
git diff --check -- mote-kernel/src/mote_kernel/execution/engine/recovery.py
git diff --cached --check -- mote-kernel/src/mote_kernel/execution/engine/recovery.py
```

当前docs-only design/index unit只对第11.1节两个实际文档运行对应scoped pre-commit与tracked/untracked whitespace检查；它没有
production/tests，不能冒充implementation gate。其exact命令同样使用`SKIP=kernel-complexity`并只列两个文档。门禁不修改Git index，
不把用户现有未提交文件纳入S15 manifest；完整`make check`与未跳过complexity的pre-commit结果不得写成S15通过条件。

## 14. 停止条件

除requirements `GSP-S01`–`GSP-S08`外，出现任一条件立即停止并保持production现状：

1. branch precedence不能保持`seen → completed → aborted → awaiting → active → settled → executable`；
2. initial或active/settled/executable admitted count、4096阈值、admit-before-dispatch时点任一变化；
3. heap key、sequence increment、heappush顺序、seen equality或final boundary sort任一变化；
4. existing expansion helper的input/output、调用次数、内部planner/routing/resource/nested mechanics需要改变；
5. completed history、active-no-live、limit、nested或materialization错误type/text/precedence变化，或需要新增`try/except`；
6. target需要新增handler、DTO、alias、protocol、callback、context、cache、index、field、property、import或public export；
7. target需要`Any`、`object`、bare container、cast、reflection、string discriminator或dynamic import；
8. 第7节任一结构上限不能闭合，或只是移动分支而没有净减少decision/semantic node；
9. actual production manifest不再是`recovery.py`一个文件，或需要新增/修改test/normative/complexity artifact；
10. 需要新增或依赖legacy/private-source-shape gate才能证明正确；
11. 触及State、State tests、command/reducer、commit、protocol、Store/persistence或第二execution/recovery runner；
12. 把preflight、callback acknowledgement或in-memory successor写成durability、automatic crash recovery或Kernel failover保证；
13. 任一current behavior、strict typing、active owner/dependency/source-discipline、lint/build/package或repo gate失败且不能在exact target内修复；
14. requirements owner尚未对reviewed S15 exact SHA显式授予`GSP-A06`。

## 15. 当前准入状态

S15设计已闭合requirements要求的target function signature、nominal输入/输出、删除对象、新增上限、净复杂度证据、成功/失败/边界
characterization、exact-shape/tamper evidence、no-State/no-persistence边界、错误恢复保持义务、one-file production manifest和无legacy
gate口径。第 1–15 节保留实施前的 target/准入约束；第 16 节记录批准后的实际 implementation-owner writeback。

当前状态已收口为：

```text
IMPLEMENTED
VERIFIED
IMPLEMENTATION-OWNER WRITEBACK COMPLETE
GSP-A06 APPROVED — reviewed exact target SHA only
PRODUCTION COMMIT: 4b8f3727644687ffd63d6bdd7e0dc4159274b892
PRODUCTION MANIFEST: recovery.py only
```

完整验收证据见独立 [S15 implementation acceptance](graph-semantics-preserving-simplification-s15-implementation-acceptance.zh-CN.md)。

## 16. Implementation owner writeback（2026-08-24）

### 16.1 授权与实际 manifest

requirements owner 已依据独立技术评审和用户原文授权，将 `GSP-A06` 限定批准到本方案 reviewed SHA256
`1e6629dfacad43ed1c87036fbc9f6589f606a220592c5fde3fadba068172e85a`。本次 writeback 不改变该批准范围，也不重新解释
requirements、review 或 target；它只记录实际实现结果。

Production implementation 已作为独立 commit `4b8f3727644687ffd63d6bdd7e0dc4159274b892` 落地。

实际 production manifest 与批准 manifest 完全一致：

```text
mote-kernel/src/mote_kernel/execution/engine/recovery.py
```

实际源码 SHA256 为 `40d0fff5240f8ce479006699e07f2a767ecfc533d889c86244e48589794e28df`；声明基线 SHA256 为
`d77df79d3f94ca973b29945a3abddf3382b280ed8da7f208ad455757c1d9514e`。diff 为 `19 insertions / 24 deletions`，且只改变
`_prove_scope()` 内的 local enqueue 与统一 successor/budget/dispatch 编排。没有 production/test/normative/State/Store/protocol/
persistence 之外的 S15 文件变更。

### 16.2 Actual structural ledger 与 source review

按第 7 节口径对实际源码复算：

| 结构项 | Before | Actual | 净变化 |
| --- | ---: | ---: | ---: |
| module-level function definitions | 23 | 23 | 0 |
| `_prove_scope()` decision points | 13 | 11 | -2 |
| `_prove_scope()` semantic AST nodes | 351 | 323 | -28 |
| 去重后的 `ast.Name(Store)` identities | 14 | 13 | -1 |
| enqueue 内部 batch loop | 1 | 0 | -1 |
| non-initial / total budget admit sites | 3 / 4 | 1 / 2 | -2 / -2 |
| enqueue call sites | 3 | 1 | -2 |
| successor boundary discriminator sites | 2 | 1 | -1 |
| existing expansion owner calls | 3 | 3 | 0 |
| new owner/DTO/alias/protocol/field/cache/index/import/export | 0 | 0 | 0 |

source review 确认：`enqueue` scalar 输入、两个成员 nominal successor tuple、single-pass dispatch、admit-before-dispatch、branch precedence、
full-semantic seen、heap key/sequence/final sort均与 reviewed exact target一致；没有第二 worklist、第二事实源、类型擦除、错误捕获或
自动 recovery policy。该一次性账本不构成永久 complexity/legacy/private-source-shape gate。

### 16.3 Verification record

在实际源码 SHA 上完成：

```text
focused recovery contract                         53 passed
active architecture contract                      10 passed
tests/execution                                   563 passed
tests/state/graph_state                            206 passed
all tests excluding complexity gate                826 passed
pyright                                             0 errors, 0 warnings, 0 informations
ruff check / ruff format --check                    passed
build --no-isolation / twine check                  succeeded / both artifacts PASSED
SKIP=kernel-complexity pre-commit (recovery.py)      passed
git diff --check                                    passed
```

current behavior、strict typing、owner/dependency/source discipline、lint、format、build/package、no-State/no-persistence 与 scoped
repository checks均为 required 且通过。automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate
按用户授权排除；完整 `make check` 未运行，因为其 `check` 目标无条件包含被排除的 `complexity-ratchet`，不能将其记录为 S15 通过证据。

### 16.4 交付与工作树状态

S15 implementation 现为 `IMPLEMENTED / VERIFIED`。Production、独立验收记录与本 writeback 分属独立 change unit；本次只提交
本文，不 amend production，也不累计 review/acceptance 历史 manifest。工作树中的其他用户既有变更保持未提交；提交过程没有执行
reset、stash 或删除操作，也没有把其他文件纳入 S15 owner writeback。
