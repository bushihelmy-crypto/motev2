# Graph 执行代码语义保持型简化实施方案

## 1. 文档信息

- 状态：Approved for 15 P1 / requirements 已明确批准 `GSP-A05`；S08 production/owner gate 已完成，工作树交付待独立 complexity unit 分离
- 日期：2026-08-21（本轮独立回写 S08；不把混合工作树冒记为零负债交付）
- 适用目录：`src/mote_kernel/execution/**`
- State/持久化边界：HARD KEEP；本轮不实现持久化，不修改当前 `GraphRunState`/command/reducer/protocol
- 唯一公共门面：`mote_kernel.execution.Graph`
- 唯一执行 owner：现有 execution engine
- 本文性质：内部重构实施方案，不改变现有公共行为

关联记录：

- [语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- [第一次复审结论](graph-semantics-preserving-simplification-implementation-review.zh-CN.md)
- [第一次复审回复](graph-semantics-preserving-simplification-implementation-review-response.zh-CN.md)
- [第二次复审结论](graph-semantics-preserving-simplification-implementation-second-review.zh-CN.md)
- [第三次复审结论](graph-semantics-preserving-simplification-implementation-third-review.zh-CN.md)
- [第四次复审结论](graph-semantics-preserving-simplification-implementation-fourth-review.zh-CN.md)
- [第五次复审结论](graph-semantics-preserving-simplification-implementation-fifth-review.zh-CN.md)
- [第五次复审回复](graph-semantics-preserving-simplification-implementation-fifth-review-response.zh-CN.md)
- [Requirements 再次复审](graph-semantics-preserving-simplification-requirements-review.zh-CN.md)
- [第六次复审结论](graph-semantics-preserving-simplification-implementation-sixth-review.zh-CN.md)
- [第七次复审结论](graph-semantics-preserving-simplification-implementation-seventh-review.zh-CN.md)
- [第八次复审结论](graph-semantics-preserving-simplification-implementation-eighth-review.zh-CN.md)
- [第九次复审结论](graph-semantics-preserving-simplification-implementation-ninth-review.zh-CN.md)
- [第十次复审结论](graph-semantics-preserving-simplification-implementation-tenth-review.zh-CN.md)
- [Execution / State / Frontier 调用链（非规范性草稿）](execution-state-frontier-call-chain.zh-CN.md)
- [历史两提交调研](../example/graph-two-commits-simplification-review.zh-CN.md)

本文覆盖最近四个历史节点，其中只有三个包含独立实现差异：

| 历史节点 | 提交 | 本方案中的处理 |
| --- | --- | --- |
| `4c17a8f` | feat(kernel): implement graph node input-output contracts | 主要复杂度来源，纳入完整审查 |
| `f071c09` | merge pull request | 无独立实现差异，不重复计数 |
| `8980e6f` | refactor(kernel): split graph facade orchestration | 合理的 owner 搬迁，不新增独立简化债务 |
| `7944159` | feat(kernel): support outputs for skipped failures | 新增 resume、routing、partial-commit 候选 |

历史调研和评审记录都不是行为规范事实源。Node I/O 与 skip-output 的 normative source 仍由第 8 节列出的文档拥有。

### 1.1 审查口径

本次按 `4c17a8f^..7944159` 审查完整变更面，并分别复核：

- production：`src/mote_kernel/execution/**` 的新增、修改和搬迁后最终实现；
- contract：相关 requirements、implementation、review 与 review-response；
- behavior：architecture、execution、typing-negative 测试冻结的类型、owner、事务和恢复行为；
- history：对 `8980e6f` 做移动前后映射，不把文件拆分重复计为新复杂度。

搜索口径覆盖重复权威事实、未消费字段或参数、重复扫描/索引/坐标构造、只包装现有对象的 DTO、超长分支，以及为复用既有逻辑而临时构造完整状态对象的间接路径。同一 producer 的 canonical facts 被多个 consumer 接入时只计一个改造单元。

当前没有发现新的第 24 个历史审查来源；但原 S23 混合了两个可独立实施和验证的问题，现拆成 S23A、S23B。因此本文保留 S01–S23 共 23 个历史审查 ID，实际跟踪 24 个原子实施单元。第六至第十次复审以及调用链草稿是本轮的文档审计输入，不是新的实施单元。

### 1.2 第七至十次复审输入的范围裁决

本轮采用的最小 source precedence 是：requirements 链接的现行 normative sections 与当前
production/characterization 共同定义必须保持的行为；第 2.4 节的 State/no-persistence 约束是无条件
HARD KEEP。若说明性文档与这些基线冲突，不从冲突中推导未来 shape，也不据此修改 State。这个最小口径足以
约束 execution-only P1，不要求先完成整个 architecture 双语文案的 canonical/translation 治理。

`execution-state-frontier-call-chain.zh-CN.md` 当前只登记为**非规范性调用链草稿**和独立文档整改项，不能
成为第二个 State、commit、recovery 或 target-shape 事实源，也不阻断 `GSP-A05`。本文不替它修改内容；其中
冲突均按下列当前代码事实识别：

- `commit_transition()` 无 callback 时只返回进程内 pure-reducer successor；有 callback 时只校验返回的
  `GraphRunState` 与 candidate 结构相等。两者都不等于持久化、durability 或跨进程恢复确认。
- 只有全部节点为 `Succeeded`/`Skipped` 时，settlement 后才可能形成 `frontier=SETTLED`；failure/interrupt
  形成 `AWAITING_RESUME`，不得写成无条件的 `RUNNING + SETTLED` 屏障。
- `Graph.resume_failed()`、`resume_interrupted()`、`skip_failed()` 是 action factory；真正的执行入口仍是
  `Graph.run(..., resume=...)`。active lease 的停止确认和 exact fence 由调用方提供，kernel 不仲裁旧 worker 的
  存活，也不新增 recovery runner。
- “原子提交”在本文仅指现有 commit callback 边界和既有 memory-install 顺序；原子性、durability、Store 由
  调用方自行决定，且不在本轮实现。

因此，调用链草稿中的 R5–R8 保留为该文档自己的整改记录，不是 execution P1 的 State/持久化实施项或
`GSP-A05` blocker。只有实际修改该草稿的变更单元才把它列入自己的 actual manifest；其他单元不得为累积历史
而反复纳入该路径，也不得把草稿当作 normative 文本或准入证据。

## 2. 目标、语义边界与分级

### 2.1 目标

只处理能够证明不改变外部语义的内部简化：

- 删除可由现有权威事实无损推导的字段、参数、wrapper 和 sentinel；
- 合并同一事实的重复扫描、坐标构造和诊断投影；
- 在不分散算法 owner 的前提下，用窄 nominal 输入/输出降低局部变量和分支复杂度；
- 保持现有 owner、类型边界、事务顺序和错误边界。

“外部语义不变”的唯一需求定义见
[语义保持型简化需求第 3 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#3-行为保持义务)。
每个实施单元必须声明适用的 `GSP-P01`–`GSP-P08`，并以该节链接的现行 normative source
和 characterization 为证据；本文不再复制公共行为、State、事务、Result/Continuation、recovery
或 nested/resource 的具体契约。

### 2.2 分级

- P1：候选目标 shape、唯一 owner 和删除对象已经写明；满足 `GSP-A01`–`GSP-A05` 并通过
  Phase 0 最终准入评审后可单项原子实施。
- P2：方向可能成立，但必须先给出窄 nominal target、净复杂度下降证据和行为 characterization，并单项再评审；不得与 P1 混合实施。

P1/P2 都不表示可以跳过规范同步、完整门禁或代码评审。requirements 第 7 节只授权当前矩阵中的 15 个 P1；
9 个 P2 和账本外方向仍未获批。

### 2.3 允许与禁止

所有单元首先遵守 requirements 的 `GSP-P08` 与 `GSP-N06`。以下只补充 target-shape 和
复杂度账本特有的实施约束。

允许：

- private typed helper、窄 nominal adapter 和同一 owner 内的实现重排；
- 删除 dead field、dead parameter、无独立行为 wrapper 和只供测试读取的镜像字段；
- 一次 typed scan 产生现有多个扫描分别产生的 facts；
- 用 canonical record 直接替代完全相同的派生 record；
- 在同一原子改造中同步 production consumer、规范和 exact-shape 测试。

禁止：

- 以 compatibility alias、forwarding property 或临时双写作为跨单元迁移桥；
- 为减少行数引入 context bag、wide union helper、回调式 generic validator 或隐藏 mutable state；
- 以无语义 sentinel、phantom generic、`Any`/`object`、bare container 或 generic-erasing `cast` 代替 nominal 边界；
- 在函数、分支或动态加载路径中导包；production imports 必须继续形成连续的 module-header block；
- 先删除 producer、后迁移 consumer 的跨提交过渡。

### 2.4 持久化与 State 硬边界

本轮明确不实现持久化，State 保持当前实现和行为。该约束高于任何 P1/P2 候选方向，也不因
architecture 双语文档尚待 owner 收口而重新开放：

- `src/mote_kernel/state/**` 与 `tests/state/**` 均为 `KEEP`；现有 state tests 只随 `make check` 原样复跑，
  不因本轮简化修改断言、fixture 或 expected shape；
- `GraphRunState`、`GraphRunCommand`、全部 command variant、reducer/validation、status、revision、run/resource/
  codec identity 以及现有序列化/跨语言协议不增加、不删除、不改名、不迁移 owner；
- 不新增 Store、repository、persistence port、database adapter、journal、event log、checkpoint、snapshot service、
  retry/exactly-once 层、默认 commit backend 或任何同义目录/类型；
- 现有 `Graph.Commit`/`Graph.Transition` callback、exact-candidate acknowledgement、partial-prefix handoff 和
  memory State/frame installation order 只作为必须保持的当前调用协议；本文不把 callback 实现成持久化层，也不
  承诺新的 durability。无 callback 时 candidate 只是当前 invocation 的进程内 reducer successor；有 callback
  时也只确认 `GraphRunState` 结构相等，不确认外部存储已经 durable；
- concrete graph input、node output、publication、resume input 与 continuation frame 继续只由当前
  execution-owned `ScopedFrameIndex`/continuation 持有，不写入 State、Graph 实例、全局缓存或第二 store；
- S09、S17、S20、S22 即使消费现有 State/command/reducer，也只能简化 execution-owned projection、admission
  或临时对象；不得改 State 定义、command/reducer 行为、commit port 形状或协议编码。

调用方若在进程重启后重新提供一个已确认的 `GraphRunState`、合法 continuation 和现有 recovery 输入，kernel
可以按当前 recovery 协议继续；这不是 kernel 对崩溃恢复的保证。任何文档、测试或 target gate 把 routing 写成
“外部已持久化”、把 candidate 写成“已耐久确认”、写成无条件的 `SETTLED` 崩溃屏障、自动 checkpoint/Store
或 kernel 对重启恢复作保证，均视为越界，必须改回“已确认的 State-held routing/join facts”或“调用方提供的
State/continuation”。

任一 actual changed-file manifest 若包含 `src/mote_kernel/state/**`、`tests/state/**`、conformance/protocol
artifact，或新增任何持久化/存储实现，即视为当前单元越界并立即停止；不能降为 P2、补 adapter 或另加测试后
继续。本轮若未来确需这些能力，必须另立需求和架构方案，不继承本文批准。

## 3. 审查账本与目标 shape

当前 23 个历史审查 ID 拆解为 24 个原子实施单元：

- P1：15 个；
- P2：9 个；
- `4c17a8f` 来源：18 个历史 ID，因 S23 拆分对应 19 个原子单元；
- `7944159` 来源：S10、S11、S17、S18、S22，共 5 个原子单元；
- `8980e6f`：0 个新增单元。

每项都明确“删除什么”和“最多新增什么”。若实际实现需要超出该新增上限，或净认知面不下降，立即停止并重新评审。

### 3.1 Compiler、validation 与 compiled topology（S01–S06）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S01 | `_compile_graph()` 仍是唯一编排 owner；只有收集/解析、edge lowering、activation proof、descriptor assembly 已形成窄 nominal phase 边界时才拆分 | 跨阶段局部变量和重复阶段判断 | 不超过 6 个窄 phase 函数；禁止 context bag | `graph/compiler.py` | P2 |
| S02 | validation 仍集中拥有错误优先级；只有 direct/conditional/join 或 definition 子校验能形成独立 invariant 且净分支/变量下降时才提取 | 经证明可独立的重复校验分支 | 每个 nominal variant 至多一个 validator；不得改变遍历顺序 | `graph/validation.py` | P2 |
| S03 | nested 分类直接取 `nested_graphs` key；callable 分类按 `nodes` 中 `CallableNodeDefinition` nominal variant 判断 | `FrontierTransitionPlan.callable_node_ids`、`nested_node_ids` 及 compiler producer | 无 | 见 3.1.1 完整迁移清单 | P1 |
| S04 | `FrontierTransitionPlan.publications` 精确收窄为 `FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]`；本单元同时删除 `CompiledGraph.publications` projection，全部 publication consumer 一次迁移到 `graph.transition.publications[node_id]`；outputs 取 `descriptor.declarations`，routes 取 `conditional_targets[node_id]` | `FrontierTransitionPlan.outcomes`、`CompiledGraph.outcomes`、`CompiledGraph.publications`、`OutcomeAdmissionPlan`、`PublicationPlan`、keyed plan 中重复 `node_id`，包括 `MaterializationPlan.node_id` | 不新增 DTO；只收窄 map value type | 见 3.1.1 完整迁移清单 | P1 |
| S05 | graph input declaration 只由 `graph_input_descriptor.declarations` 拥有，nested compiler 和 runtime admission 直接消费该字段 | `CompiledGraph.graph_inputs` | 无 property/alias | 见 3.1.1 完整迁移清单 | P1 |
| S06 | `CompiledGraph` 直接拥有唯一 `transition` lowering field；删除 S04 后剩余的 convenience projections，consumer 统一读取 `graph.transition.*`；publication 只做 zero-use/exact-shape 验证 | `RecoveryAvailabilityPlan`、`CompiledGraph.transition` property、`entries`、`materializations`、`graph_outputs`、`resource_order` 四个 forwarding properties（`outcomes`、`publications` 已由 S04 删除） | 一个直接 `transition: FrontierTransitionPlan[GraphValueT]` field；无 alias/property | 见 3.1.1 S06 完整迁移清单 | P1 |

S03–S06 必须逐项原子实施；不得先引入新 field 双写，再在后续提交删除旧 field。

#### 3.1.1 S03–S06 完整原子迁移清单

以下清单是实施边界，不以“迁移全部 consumer”代替可核对位置。每项必须在同一原子变更中完成 production producer、consumer、imports、normative shape 和 direct exact-shape tests：

- S03 definition/producer：`graph/topology.py` 删除两个 field，`graph/compiler.py` 删除两个 classification tuple 的构造和 `FrontierTransitionPlan` 实参；runtime consumers：`engine/frontier.py`、`engine/recovery.py`、`engine/admission.py`；direct tests/import gate：`tests/execution/graph/test_topology.py`、`tests/execution/graph/test_compiler.py`、`tests/architecture/test_graph_execution_ownership.py`。同时复跑 frontier、resource admission、nested recovery characterization。
- S04 definition/producer：`graph/ports.py` 删除两个 DTO 和 `MaterializationPlan.node_id`；`graph/topology.py` 删除 `outcomes` field、`CompiledGraph.outcomes`/`CompiledGraph.publications` projections 并收窄 `publications` value type；`graph/compiler.py` 只生成 descriptor map。runtime consumers：`engine/scheduler.py`、`engine/frontier.py`、`engine/recovery.py`、`executor.py`、`engine/routing.py`、`engine/resume_input.py`、`engine/resume_admission.py`、`engine/admission.py`、`invocation.py`、`family_driver.py`；direct tests/import gate：`tests/execution/graph/test_topology.py`、`tests/execution/graph/test_compiler_contract.py`、`tests/architecture/test_graph_execution_ownership.py`、`tests/execution/engine/test_routing.py`、`tests/execution/engine/test_output_projection.py`、`tests/execution/engine/test_resume_input_contract.py`、`tests/execution/engine/test_resume_admission.py`、`tests/execution/engine/test_recovery_identity.py`、`tests/execution/engine/test_runtime_boundaries.py`、`tests/execution/test_executor.py`、`tests/execution/test_continuation_integrity.py`、`tests/execution/test_graph_api.py`。所有 removed DTO imports、`.outcomes[node_id]`、`graph.publications`/`scoped_graph.publications` 和 `.publications[node_id].descriptor` 访问必须同原子归零；publication consumers 必须直接形成 `graph.transition.publications[node_id]`，不得留给 S06 再次触碰。
- S05 definition/producer：`graph/topology.py` 删除 field，`graph/compiler.py` 删除 `CompiledGraph` 实参并把 nested child declaration 读取迁移到 `graph_input_descriptor.declarations`；runtime consumers：`engine/admission.py`；direct tests/import gate：`tests/execution/graph/test_compiler.py`、`tests/execution/graph/test_compiler_contract.py`、`tests/execution/graph/test_topology.py`、`tests/execution/engine/test_recovery_identity.py`、`tests/architecture/test_graph_execution_ownership.py`。同时复跑 compiler contract、graph-input admission 和 nested boundary tests，并更新 architecture/generic shape gate。
- S06 definition/producer：`graph/topology.py` 删除 `RecoveryAvailabilityPlan`、`CompiledGraph.recovery` field、`transition` property、`entries`/`materializations`/`graph_outputs`/`resource_order` properties，并把 `transition: FrontierTransitionPlan[GraphValueT]` 作为 direct field；`graph/compiler.py` 直接传入 transition，删除 recovery wrapper assembly。剩余 consumer 必须改为 `graph.transition.*`，不得新增 alias、property、局部 wrapper 或双写过渡：

  | projection | production consumers | direct tests/import gate |
  | --- | --- | --- |
  | `entries` | `graph_run.py` | `tests/execution/graph/test_compiler.py` |
  | `materializations` | `engine/resume_input.py`、`engine/routing.py`、`executor.py`、`invocation.py` | `tests/execution/engine/test_resume_input_contract.py`、`tests/execution/graph/test_compiler_contract.py` |
  | `graph_outputs` | `engine/admission.py`、`engine/routing.py`、`graph/compiler.py` | `tests/execution/engine/test_output_projection.py`、`tests/execution/graph/test_compiler_contract.py` |
  | `resource_order` | `engine/admission.py`、`engine/snapshot_guard.py` | `tests/execution/graph/test_compiler.py` |
  | all exact fields/properties + publication zero-use | `graph/topology.py`、`graph/compiler.py` | `tests/architecture/test_graph_execution_ownership.py` |

S04 完成后的 exact shape 必须同时满足：`FrontierTransitionPlan` 不再有 `outcomes`，`CompiledGraph` 不再有 `outcomes` 或 `publications` projection，最终访问路径为 `graph.transition.publications[node_id]`，value 本身就是 `FrameDescriptor[GraphValueT]`，且不存在 `OutcomeAdmissionPlan`、`PublicationPlan` 或 keyed plan 内重复的 `node_id`。

S06 完成后的 `CompiledGraph` exact-shape gate 必须断言：`recovery` 不再存在；`transition` 是 direct dataclass field 而不是 property；`entries`、`materializations`、`publications`、`graph_outputs`、`resource_order` 均不再在 `CompiledGraph` 定义；publication projection 和 direct consumer 已由 S04 归零，本单元只验证其未回归。topology exact shape 与唯一访问路径只由 `tests/architecture/test_graph_execution_ownership.py` 冻结；`test_source_discipline.py` 仍作为连续模块头、`Any`、动态导入和反射门禁运行，不复制 topology 断言。

### 3.2 Routing、join 与 availability facts（S07–S11）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S07 | 只在现有 GraphInput、NodeOutput、resume nominal source 内形成 source-specific typed coordinate constructor；错误仍由调用边界拥有 | 经证明完全相同的 coordinate assembly | 每类 nominal source 至多一个窄函数；禁止 wide union/generic adapter | `engine/admission.py`、`engine/resume_input.py`、`engine/routing.py` | P2 |
| S08 | `joins_by_source` 保持唯一 compiled truth；snapshot guard 只能在 module scope import routing 的 `_declared_joins()` 并调用它，不能直接读取 field 或重建 comprehension | snapshot guard 的重复 join comprehension | 不新增 helper、cache 或 topology field；最多增加一个 module-scope import/call | `engine/routing.py`、`engine/snapshot_guard.py`、architecture owner gate | P1 |
| S09 | `resolve_routing_facts()` 保留诊断 facts；`project_routing_facts(state, facts) -> ResolutionCommand` 是唯一 projection，`resolve_routing(...) -> ResolutionCommand` 只做组合 | `RoutingResolution`、`plan_routing()`、同签名 forwarding path 和镜像字段 | 无新 DTO/property/alias/rename；command decision branches 保持 4 类 | 见 3.2.1 闭合目标 | P1 |
| S10 | `resolve_routing_facts()` 对 graph outputs 只保留一次完整 diagnostic scan，保存 canonical `unavailable_graph_outputs: tuple[str, ...]`；独立 `graph_outputs_available(...) -> bool` 仍保留首次缺失短路 | resolver 内重复 output scan、`completion_output_available`、`completion_output_history_missing` | 无新 DTO/property/cached bool；completion facts 由 tuple/target work 推导 | `engine/routing.py`、routing/recovery/resume consumers、skip-output normative | P1 |
| S11 | 每个 unique `GraphNodeId` 的 binding 只扫描一次，cache value 直接是 `RequiredTarget`；`inputs_available := not unavailable_inputs`，首次访问顺序固定为 control → completed join → data，binding/diagnostic 顺序不变 | `inputs_available`、`unavailable_target_inputs()` 与 historical-gap 双扫描、重叠 target 重算、独立 display identity | `dict[GraphNodeId, RequiredTarget]` invocation-local typed cache；禁止无序 set 决定首错或额外 identity DTO | `engine/routing.py`、`engine/recovery.py`、`engine/resume_admission.py`、skip-output normative | P1 |

S08 不增加 compile-time index、cache、第二 representation 或第二同义 helper；完成后 `joins_by_source` field
的**直接读取**只留在 routing owner，`snapshot_guard.py` 只能 module-scope import 并调用 routing 的
`_declared_joins()`，不得读取该 field。S10 不改变独立短路 helper 的行为。S11 保持每个 group 的既有成员和
稳定排序，只复用相同 target 的已计算 fact。

#### 3.2.1 S09–S11 闭合目标

S09 的唯一调用结构固定为：

```text
resolve_routing_facts(graph, state, scope_run, frames) -> RoutingFacts
project_routing_facts(state, facts) -> ResolutionCommand
resolve_routing(graph, state, scope_run, frames) -> ResolutionCommand
```

其中 `resolve_routing()` 是 runtime 唯一组合入口，只组合前两个步骤；recovery 因需要诊断 facts，可显式消费 `resolve_routing_facts()` 和现有跨模块 owner-internal projection `project_routing_facts()`。保留该现名，不做下划线 rename；删除 `RoutingResolution` 与 `plan_routing()`，不得为测试兼容保留 wrapper。必须同原子迁移 `engine/recovery.py`、`tests/execution/engine/test_routing.py`、`tests/execution/engine/test_output_projection.py`、`tests/execution/engine/test_recovery_identity.py`、architecture symbol/owner gate，以及 skip-output normative 中对 `plan_routing()` 的全部引用。

`engine/recovery.py::_resolve_quiescent()` 迁移时必须保留同一份 local `facts`，用
`facts.unavailable_graph_outputs`、`facts.control_targets`、`facts.completed_join_targets` 和
`facts.remaining_join_progress` 判断 historical-gap/错误边界；不得为了补回
`completion_outputs_available` 或 selected-target tuple 再创建一个 `RoutingResolution`、result wrapper 或
第二次 facts scan。runtime 只消费 `resolve_routing()` 的 command，recovery 才在同一 invocation 内保留 facts
用于诊断。

`ResolutionCommand` 继续只是 `execution/engine/routing.py` 对现有 State-owned command variants 的 owner-internal
type alias；S09 不新增、删除或修改任何 `GraphRunCommand` variant、reducer branch、revision rule 或协议编码。

S10/S11 完成后的 exact facts model 固定为：

```python
@dataclass(frozen=True, slots=True)
class RequiredTarget:
    node_id: GraphNodeId
    historical_inputs_missing: bool
    unavailable_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoutingFacts:
    control_targets: tuple[RequiredTarget, ...]
    completed_join_targets: tuple[RequiredTarget, ...]
    remaining_join_progress: tuple[GraphJoinProgress, ...]
    data_targets: tuple[RequiredTarget, ...]
    unavailable_graph_outputs: tuple[str, ...]
```

`unavailable_inputs`/`unavailable_graph_outputs` 中的字符串只是现有诊断文本，按 binding/graph-output declaration
顺序生成；它们不构成额外的 display-identity record、坐标 key 或 cache value。任何需要独立保存 node/display
identity 的实现都应退回 S11 复审。

消费处只能按以下 canonical facts 推导，不得改成 property、缓存字段或第二诊断 DTO：

```text
target inputs available
  := not target.unavailable_inputs

has routing work
  := bool(control_targets or completed_join_targets or remaining_join_progress or data_targets)

completion output available
  := has routing work or not unavailable_graph_outputs

completion output history missing
  := not has routing work and bool(unavailable_graph_outputs)
```

若实施时需要保留任一被删布尔，必须先证明它不能由上述 semantic basis 精确推导，并把 S10/S11 退回单项评审。

### 3.3 Recovery 与 continuation validation（S12–S15）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S12 | 候选方向是 resume-input presence 只由 `RecoveryAvailabilityCoordinates`/frame availability 拥有；字段删除前必须闭合 valid-domain equality、action ↔ availability 和 malformed 行为 | 候选删除 `resume_input_availability`，并同时移除 `AdmittedResumeFact`、`_RecoveryFamily` 的 phantom generic | 不新增 runtime field/cache；若选择 fail closed，至多一个窄 seed invariant validator且须单项批准 | `engine/recovery.py`、`invocation.py`、recovery/generic tests、Node I/O normative | P2 |
| S13 | `_initial_children()` 只接收实际使用的参数 | 未使用的 `availability` 参数和全部实参 | 无 | `engine/recovery.py` | P1 |
| S14 | `_NestedOutcome` exact shape 为 `(node_id: GraphNodeId, boundary: _ScopeBoundary[GraphValueT])`；`kind`/`availability` 直接读 `boundary`，child disposition 从 equality-participating `boundary.control` 投影 | `kind`、`availability`、`disposition` 三个重复字段 | 一个精确签名 `_child_disposition_from_control(control: ScopeControlStateCoordinate) -> ChildRecoveryDisposition`；要求 `control.parent is not None` 后逐字段构造 `ChildControlStateCoordinate`，不得读取 `boundary.state`（`compare=False`）重建 identity | `engine/recovery.py` | P1 |
| S15 | `_prove_scope()` 保留 worklist、seen、enqueue、budget 和 branch precedence 的单一 loop owner；只有 branch 能以窄 nominal result 降低净分支/变量时才提取 | 经证明独立的 branch-local mechanics | terminal/active/settled/executable 至多各一个 handler；不得搬走 queue/budget | `engine/recovery.py` | P2 |

S12 当前不获准随 P1 实施。proof-local constancy 只能证明一次 `_prove_scope()` 内 `family.admitted_actions` 对 transfer state 固定，不能替代完整结构语义设计。S12 单项再评审前必须同时提交：

1. valid-domain 定义，以及每个 non-skip admitted action 与本 invocation availability 中 exact `ResumeInputAvailabilityCoordinate` 的对应不变量；
2. 删除前后 valid-domain `RecoveryTransferState` equality/hash、reachable boundary、seen equivalence、traversal ordering 和 4096 budget 的证明与 characterization；
3. malformed seed 的明确裁决：保持现有行为、先以独立变更 fail closed，或明确退出本轮语义保持范围；不得用删除字段悄然改变 equality 或错误边界；
4. 完整泛型目标：`AdmittedResumeFact` 与 `_RecoveryFamily` 均改为非泛型；`RecoveryTransferState[GraphValueT]` 和 `RecoveryInvocationSeed[GraphValueT]` 只因自身 availability/frames 继续泛型；
5. 保持 architecture gate：recovery 不得直接读取 `materializations`，也不得建立 recovery-only input interpreter；
6. 与 production 原子同步的 Node I/O normative equality/shape 和 generic-integrity/exact-shape tests 迁移方案。

S12 禁止用 `compare=False`、phantom TypeVar、第二 availability field 或 compatibility shape 代替上述证明。任一项未闭合则保持现状。
这里的 `RecoveryTransferState` 是 execution-local、invocation-bounded 的 pure proof value，不是
`GraphRunState`、持久化 schema 或 checkpoint；S12 即使未来通过 P2 单项评审，也不获得修改 State/protocol
或增加 persistence owner 的权限。

### 3.4 Invocation、resume admission 与 executor（S16–S20）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S16 | 四类 frame segment 可各自拥有窄 nominal validator，但总入口继续固定 segment/error 顺序 | 每段内部经证明重复的 shape/coordinate boilerplate | 每个 segment 至多一个 validator；禁止 wide union、`object`、回调 generic helper | `invocation.py`、`run_context.py` | P2 |
| S17 | `command.actions` 是 skip action 真相，`substitutions` 是 replacement 真相；pure skip 在 validation 后由二者差集推导 | `ScopedResumeCandidate.skip_actions`、`has_pure_skip` | 仅 owner-local derived tuple/index，不保存缓存字段 | `engine/resume_admission.py`、`invocation.py` | P1 |
| S18 | `plan_resumes()` 对 action coordinate 做一次 owner-local typed count/index；`admit_resume_candidates()` 对 publication coordinate 做一次 owner-local typed count/index，并在同一次 canonical enumeration 中同时收集 duplicate 与 confirmed-collision 的有序结果 | `tuple.count()` O(n²)、先 `any` 再重复枚举、重复 set/count 组合 | 每个 owner 最多一个 index、合计两个；允许非 index 的有序错误 accumulator，不抽跨 owner generic helper、不新增 boundary DTO；仍先报 duplicate、再报 collision，消息内 identity 顺序不变 | `invocation.py`、`engine/resume_admission.py` | P1 |
| S19 | failed/interrupt/skip nominal 分支继续拥有 action-local validation；只有完全相同的 encode/decode/frame admission 才可提取 | 经证明相同的 admission mechanics | 窄 typed 函数；不得形成 action context bag | `executor.py` | P2 |
| S20 | 继续由既有 `engine.resume_input.materialize_node_input` 作为唯一 owner；只增加 `failed_retry_input: UseStepRequestInput \| None = None`，executor 只删除 materialization-only replacement State/frontier projection，并保留最终 simulated frontier validation | `executor.py` 中 failed-retry materialization 分支的 replacement `state/frontier` 和重复 materialization path | 不新增 wrapper/function/DTO，不接受 `GraphNodeInputBinding`/`OverrideGraphNodeInput` wide union；exact signature 固定如下；最终 `simulated = GraphFrontierState(...)` 与 `validate_graph_frontier(state, simulated)` 各保留一次，不保留实施期 P1/P2 分支 | `executor.py`、`engine/resume_input.py` | P1 |

S19–S20 都不得删除或后移 simulated frontier validation。
S17/S20 对现有 command/State 的读取与 pure reducer simulation 只用于保持当前 admission/materialization 结果；
其 production manifest 仍只允许 execution-owned files，不得修改 State model、command/reducer 或 State tests。
S20 的唯一目标签名为：

```python
def materialize_node_input(
    graph: CompiledGraph[GraphValueT],
    state: GraphRunState,
    scope_run: ScopeRunCoordinate,
    frames: ScopedFrameIndex[GraphValueT],
    node_id: GraphNodeId,
    *,
    failed_retry_input: UseStepRequestInput | None = None,
) -> NodeInputFrame[GraphValueT]:
    ...
```

`UseStepRequestInput` 和 `GraphValueT` 等依赖必须位于连续 module-header import block。函数仍先验证 authoritative
`state`、scope/run/node identity：参数为 `None` 时保持当前 pending-node 路径；非 `None` 时只允许当前 node 是
failed retry，并把该 nominal value 作为本次 materialization 的 effective input，不替换或写回
`GraphRunState.frontier`。override 继续只走现有 codec encode/decode，不进入该 keyword，也不新增一个
`OverrideGraphNodeInput` 拒绝分支。simulated frontier validation、codec 校验和现有错误优先级全部保持。这样删除
的是 executor 的 materialization-only 临时 projection，不是最终 admission simulation，也不是 State 的 pending
settlement 语义；若实现无法满足此 exact target，S20 本单元停止并重新评审，而不是在 P1 编码过程中临时改成
另一签名或自动退回 P2。

S17 的 local derivation 约束也固定如下：`admit_resume_candidates()` 每个 candidate 只从
`candidate.command.actions` 生成一次 `skip_actions` tuple，并在同一 candidate 生命周期内复用；以该 tuple
对应的 publication activation coordinates 与 `candidate.substitutions` coordinates 做差集，差集非空才表示存在
pure skip，再叠加“无 control/data/remaining-join work 且 `unavailable_graph_outputs` 非空”的既有条件。
不得恢复 `has_pure_skip` 字段，也不得因删除 `completion_output_available` 再引入一个 bool mirror；坐标差集
必须保留 scope、superstep、node 和 descriptor identity。

### 3.5 Facade、family driver 与事务循环（S21–S23）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S21 | `Graph.run()` 继续是唯一 lifecycle owner；private path 只能返回窄 typed admission result | 经证明重复的 new/state/continuation dispatch mechanics | 窄 private path；禁止第二 runner、隐藏 mutable context | `facade.py` | P2 |
| S22 | 只有一个 typed transaction helper 能保留 fence/resume 全部差异时才合并 | 重复 exact-confirmation/partial-prefix exception handoff | 无 `Any` 的窄 helper；不得统一掉差异参数 | `facade.py`、`invocation.py` | P2 |
| S23A | `_advance_scope_quantum(...) -> GraphBoundary \| None`；`AdvanceGraphFrontier`、executable、child-progress 等非 boundary 分支均返回 `None`，`drive_root()` 只按 `None` 继续；nested coordination 仍返回原 boundary/error | `_AdvancedFrontier` class、union member、construction 和 `isinstance` branch | 无 disposition、sentinel 或第二 runner | `family_driver.py` | P1 |
| S23B | 一个按 `_scoped_states()` canonical root→child 顺序的 private projection 同时返回 `tuple[GraphFailureView, ...]` 和 `tuple[GraphInterruptView, ...]`；不得改变 interrupt identity/error priority | failure/interrupt 两次完整 scoped-state scan | 一个 owner-local typed projection（两个 tuple accumulator）；不保存 Result mirror | `family_driver.py` | P1 |

S22 必须逐项保持现有 callback/in-memory 协议：首个 commit 原异常直抛；已有 confirmed prefix 才包装
partial handoff；resume frame 在替换 memory State 前预计算；成功安装时 State 先于 frames。这不是 Store/
persistence 设计，S22 不得新增持久化 helper、backend、transaction manager 或 State/protocol 改动。

### 3.6 P1 净复杂度账本与 nominal signature

第六次复审要求把“复杂度下降”从定性描述改成可核对的 before→after 账本。本表采用固定的结构计数，
不是代码行数：

- `field/DTO` 计 dataclass 字段、wrapper 和 forwarding property；`pass` 计一次完整 tuple/map/binding
  enumeration；短路 helper 的独立扫描单列，不得用它掩盖 resolver 内的重复扫描；
- `branch` 只计语义分支（source kind、decision outcome、错误分类），不把等价的 `isinstance` 拼写变化算成
  下降；`index` 计 invocation-local 且有明确 key/value 类型的 lookup/count 结构；
- S04/S06 的 “before” 按其硬依赖边界计数：S06 在 S04 已删除 publication/outcome projection 后计数；
  其余行按当前代码基线 `7944159` 计数。目标数字是 T0 必须满足的上限，不是当前实现已经具备的证据。

任何 AST/source 计数与本表不一致、目标新增项超过表中上限，或 `Δ` 不能净减少，均将该单元留在
`PENDING` 并停止实施；不得用测试通过替代账本证明。

表中 `JoinKey` 仅是 routing owner 内既有 join key 的类型记法：
`tuple[tuple[GraphNodeId, ...], GraphNodeId]`；不新增跨模块 DTO 或公共 alias。

| 单元 | before → after（结构计数） | exact nominal signature / shape | 不变量与净变化 |
| --- | --- | --- | --- |
| S03 | mirrored classification field `2 → 0`；compiler classification tuple `2 → 0`；新增 scan `0 → 0` | 不新增 helper；`node_id in nested_graphs` 与 `isinstance(nodes[node_id], CallableNodeDefinition)` 是唯一推导 | `Δ=-4`，分类只从 canonical maps 推导，遍历顺序不变 |
| S04 | outcome/publication DTO `2 → 0`；keyed-plan `node_id` fields `3 → 0`（materialization/outcome/publication）；CompiledGraph projection `2 → 0` | `transition.publications: FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]`；consumer 直接读取该值 | `Δ=-7`，不得保留 descriptor wrapper、alias 或二次 keyed plan |
| S05 | `CompiledGraph.graph_inputs` field `1 → 0`；alias/property `0 → 0` | 直接读取 `graph_input_descriptor.declarations`；不新增 property/adapter | `Δ=-1`，nested/runtime admission 只保留一个 declaration owner |
| S06 |（S04 后）wrapper `1 → 0`；`recovery` storage field `1 → 0`；forwarding properties `5 → 0`；direct transition field `0 → 1` | `CompiledGraph.transition: FrontierTransitionPlan[GraphValueT]` 是 dataclass direct field；无 `recovery`/projection property | `Δ=-6`，访问 hop 减少一层；publication zero-use 由 S04 gate 验证 |
| S08 | compiled join interpretation pass `2 → 1`；snapshot direct field read `1 → 0`；helper `1 → 1` | `_declared_joins(graph: CompiledGraph[GraphValueT]) -> dict[JoinKey, JoinEdge]`；snapshot guard 只 module-scope import/call | `Δ=-1`；不得新增第二 helper/cache/index，错误顺序不变 |
| S09 | `RoutingResolution` fields `5 → 0`；`plan_routing` wrapper `1 → 0`；projection return wrappers `1 → 0`；decision branches `4 → 4` | `project_routing_facts(state: GraphRunState, facts: RoutingFacts) -> ResolutionCommand`；`resolve_routing(...) -> ResolutionCommand` | `Δ=-7`；command variant、revision 和 abort/advance/complete/deadlock 顺序不变 |
| S10 | resolver full output passes `3 → 1`；derived bool fields `2 → 0`；canonical diagnostic tuple `1 → 1`；独立 short-circuit pass `1 → 1` | `unavailable_graph_outputs(...) -> tuple[str, ...]`；`graph_outputs_available(...) -> bool` 仍是独立短路 owner | `Δ=-2`；首个缺失 binding、diagnostic identity 和 malformed selection 错误顺序不变 |
| S11 | binding passes per unique target `2 → 1`；`RequiredTarget` fields `4 → 3`；extra identity record `1 → 0`；cache `0 → 1` | `dict[GraphNodeId, RequiredTarget]`（local only）；`RequiredTarget(node_id, historical_inputs_missing, unavailable_inputs)` | `Δ=-3`；cache 按 control→completed join→data 首次访问，binding entries 顺序决定首错 |
| S13 | `_initial_children` parameters `6 → 5`；call-site arguments `6 → 5`；loop `1 → 1` | `def _initial_children(graph, state, scope_run, family, invocation_new) -> tuple[ChildRecoveryDisposition, ...]` | `Δ=-2`；删除 unused availability，不改变 child sorting/error boundary |
| S14 | `_NestedOutcome` fields `5 → 2`；state-derived identity path `1 → 0`；projection helper `0 → 1` | `_NestedOutcome(node_id: GraphNodeId, boundary: _ScopeBoundary[GraphValueT])`；`_child_disposition_from_control(control: ScopeControlStateCoordinate) -> ChildRecoveryDisposition` | `Δ=-3`；kind/availability 来自 boundary，identity 只来自 equality-participating control |
| S17 | candidate mirror fields `2 → 0`；derived action/difference scans `2 → 2`；candidate shape `8 → 6` | `ScopedResumeCandidate(graph, scope_run, previous, successor, substitutions, command: ResumeGraphNodes)`；skip facts 由 command actions，pure skip 由 action-publication coordinate 与 substitutions 的 typed 差集派生 | `Δ=-2`；不保存 `skip_actions`/`has_pure_skip`，exact-command validation 和 pure-skip fail-closed 不变 |
| S18 | invocation duplicate-check passes `2 → 1`；admission duplicate/collision-check passes `4 → 1`；`.count()` occurrences `2 → 0`；owner-local count indexes `0 → 2` | `action_counts: dict[tuple[tuple[str, ...], GraphNodeId], int]`；`publication_counts: dict[PublicationAvailabilityCoordinate[GraphValueT], int]`；每个 index 只在所属 owner 生命周期内存在 | `Δ=-4` check passes、`Δ=-2` count；每 owner 一个 index；duplicate 优先于 collision，canonical/排序后的 identity 和错误文本顺序不变 |
| S20 | materialization-only `replace(state, frontier=...)` `1 → 0`；materialization-only `GraphFrontierState(...)` `1 → 0`；final `simulated = GraphFrontierState(...)` `1 → 1`；`validate_graph_frontier(state, simulated)` `1 → 1`；materializer owners `1 → 1`；new wrapper `0 → 0`；optional typed input parameter `0 → 1` | 复用 `materialize_node_input(..., *, failed_retry_input: UseStepRequestInput \| None = None) -> NodeInputFrame[GraphValueT]`；不得新增同义函数或 wide union；最终 simulation/validation 必须保持原调用顺序 | `Δ=-2`（仅删除 materialization-only replacement projection）；codec/scope/descriptor/resume-input 优先级、final simulated frontier 和 validation 不变 |
| S23A | sentinel class `1 → 0`；return-union member `1 → 0`；`isinstance` loop branch `1 → 0`；semantic branches `3 → 3` | `_advance_scope_quantum(...) -> GraphBoundary \| None`；`drive_root()` 不识别 marker | `Δ=-3`；AdvanceGraphFrontier、普通 executable/child progress 均返回 None，boundary/error 分类不变 |
| S23B | scoped-state full passes `2 → 1`；projection accumulators `0 → 2`；Result mirror fields `0 → 0` | `_project_result_views(context) -> tuple[tuple[GraphFailureView, ...], tuple[GraphInterruptView, ...]]`；名称、参数和返回 nominal shape 固定 | `Δ=-1` pass；root→child 顺序、failure/interrupt identity 和 mixed-scope ordering 不变 |

S20 的 `2 → 0` 只统计 failed-retry materialization 分支中的一对 replacement projection：
`replace(state, frontier=...)` 和其内部 `GraphFrontierState(...)`。最终 `simulated = GraphFrontierState(...)` 以及
`validate_graph_frontier(state, simulated)` 不属于删除面，必须分别保持 `1 → 1`，并在同一顺序执行；任何把它们
计入“临时对象”而删除、后移或改成宽松校验的 target 都不满足 S20。

本表中的 `dict`/`set` 只描述 owner-local implementation index，不是公共边界、State 字段或持久化 store；
任何把它提升为 GraphRunState、continuation 或跨 invocation cache 的实现都违反第 2.4 节。

## 4. 不纳入本账本的后续条件性重构

以下方向不属于上述 24 个单元，必须另开需求/架构评审：

1. 以 sealed `AdmittedResumePlan` 合并 `PreparedResume`、`_PlannedResume`、`ScopedResumeCandidate`；
2. 将 `AdmittedResumeFact` 的 enum + optional fields 改为 closed nominal variants；
3. 用 generic canonical-index 内核实现 `ScopedFrameIndex` 四组 has/lookup/add；
4. 用 `ScopedStateIndex` 收敛 child state 与 planned state；
5. 合并 candidate availability、recovery availability 与 frame index membership；
6. 移动 `PreparedResume`、拆分 result owner 或删除 `_RootStateBinding`。

这些方向不能与 P1/P2 单元混合提交，也不能以兼容层跨阶段迁移。
其中 `ScopedStateIndex` 仅是未来 execution-local 结构候选，不是持久化 Store 或 State schema；它同样不在本轮
实施。第 4 节任何方向都不授权修改 `src/mote_kernel/state/**`、协议或增加存储能力。

## 5. 必须保留的架构边界

公共 facade、唯一 execution engine、State/transaction、continuation/publication、recovery、nested/resource
以及 typed dependency direction 的保持义务分别由 `GSP-P01`–`GSP-P08` 定义，见
[requirements 第 3 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#3-行为保持义务)；
具体当前行为继续由该文档链接的 architecture、Node I/O 与 skip-output normative source 拥有。

本文只拥有以下未来实现 shape，不把它们提升为当前 production 行为规范：

- S04 后 publication descriptor 只从 `graph.transition.publications` 读取；
- S06 后 `CompiledGraph.transition` 是唯一 direct lowering field，不保留 convenience projection；
- S08/S09 后 routing 仍是 join/routing facts 的唯一算法 owner，recovery 只消费其 typed projection；
- S12 若获单项批准，runtime/recovery 仍共用同一 transition 与 input interpretation；
- 所有单元遵守 2.3 的原子迁移和净复杂度约束；类型、import 与依赖纪律直接引用 `GSP-P08`，不在本文重写。

### 5.1 本轮最小 source precedence 与 evidence anchors

本轮不承担整个 architecture 双语治理。对 15 个 execution-only P1，source precedence 固定为：

1. requirements 唯一拥有 `GSP-Pxx`、`GSP-Axx` 及其当前裁决；本文只拥有 target shape、复杂度账本和实施门禁；
2. requirements 链接的现行 normative sections 与当前 production/characterization 共同构成行为基线；任何冲突
   都不得被解释成可以改变当前可观察行为；
3. 第 2.4 节的 State/no-persistence HARD KEEP 优先于说明性文案：当前 State、callback/reducer/memory-install
   顺序保持，不实现 Store 或未来 `AgentState` 方案；
4. 非规范性调用链和历史 review 只提供审计线索，不拥有行为、target 或准入状态。

如果某个具体单元必须先在冲突文案中选择一种**新语义**才能编码，则只停止该单元并回到 requirements owner；
现有 15 个 P1 的 exact target 均不需要这种选择。architecture 的 canonical/translation、全文 parity 和未来
State/Store 表述可作为独立文档治理继续处理，但不是 execution P1 的统一前置。

为让第 7.2.1 节的 characterization 可复核，本文固定下列 evidence anchors；它们是验证入口，不是第二套需求正文：

| Requirement | 当前 evidence anchor |
| --- | --- |
| `GSP-P01` | architecture 的唯一 `Graph` facade；Node I/O 第 3.5、13.1 节；`test_graph_public_typing.py` 与 `test_graph_typing_fixtures.py` |
| `GSP-P02` | Node I/O 第 8.5 节的 State KEEP 决策；当前 State reducer/validation production 与 tests；第 2.4 节 negative gate |
| `GSP-P03` | Node I/O 第 8.3、9.2 节；skip-output requirements 第 5.2.1、5.3 节；transaction/partial-confirmation cases |
| `GSP-P04` | Node I/O 第 8.2–8.4 节的唯一 `ScopedFrameIndex`、publication 与 continuation；frame/continuation integrity cases |
| `GSP-P05` | skip-output requirements 第 4–7 节及 implementation 第 6–8 节；routing/resume/result cases |
| `GSP-P06` | Node I/O 第 8.5、13.1 节，特别是 4096-state pre-mutation safety budget；recovery identity/boundary cases |
| `GSP-P07` | Node I/O 第 3.7、6.3、9、13.1 节的 resource first-seen、nested 与 canonical ordering；compiler/admission/nested cases |
| `GSP-P08` | 当前唯一 `Graph`/execution owner 与 port assembly；`test_generic_integrity.py`、`test_source_discipline.py`、`test_dependency_direction.py`、`test_graph_execution_ownership.py` |

`ResolutionCommand` 等 owner-internal symbol 只在对应 S 单元的 applicability matrix 中出现，不由本文把它提升为
公共或跨文档规范。若 requirements 后续调整 requirement wording，本文只同步 ID 映射和 evidence anchor，不复制正文。

### 5.2 独立文档治理项

下列差异只登记为独立文档治理输入。本文不从中选择未来 target，也不把它们列为全部 P1 或 `GSP-A05` 的
blocker：

| 主题 | 当前差异 | 本轮固定处理 | 对 P1 准入的影响 |
| --- | --- | --- | --- |
| authoritative State 组合 | 英文 architecture 概括 `AgentState = GraphState + DomainState`，中文版本描述当前 `GraphRunState` commit boundary | 维持当前 production State shape；不实现未来 `AgentState`、Store 或 migration | 无统一阻断；若某单元触及 State，直接按 2.4 越界失败 |
| store/durability 语义 | 英文版本描述 persistent store，中文版本把当前 commit boundary 与具体 durability 分开 | 只保持当前 callback exact-candidate confirmation 和 memory replacement 顺序 | 无统一阻断；不得把 callback 改写成 durability 成功 |
| required/optional ports | 两个 architecture 版本的展开程度不同 | 保持当前 assembly 行为与 owner tests，不在本方案补写新 port 语义 | 无统一阻断；具体 target 若需改变 assembly 行为则停止 |
| 调用链 R5–R8 | 草稿含 persistence、settlement、resume-entry 和 atomicity 的不准确说明 | 保持 non-normative，另行修正文案；只有修改该文件的单元才验证它 | 不作为 A01/A03 证据，也不阻断 execution P1 |

行号和差异只用于审计定位，不是永久规范锚。独立治理不得生成 State/Store production 工作项，也不得把
历史 review 或调用链草稿累积进无关单元的 manifest。

### 5.3 第六至第十次复审收口 ledger（R1–R16、C1–C2）

下表记录已经吸收进本文的实施规则，不维护第二套 `GSP-A01`–`GSP-A05` 状态：

| 复审项 | 最终实施规则 | 本文证据位置 |
| --- | --- | --- |
| R1/R10 | requirements 是准入状态唯一 owner；本轮只保留 5.1 的最小 source precedence，不以前置全局 architecture 双语治理扩大 A01 | 1.2、5.1、11 |
| R2 | 15 个 P1 都有 before→after 结构计数、nominal target 和新增上限 | 3.6 |
| R3/R12 | owner-specific 证据如实区分 direct/indirect；S23A 以现有 end-to-end behavior 作为 Phase 0 baseline，不为即将删除的 private marker 先加 characterization | 7.2.1、7.2.2 |
| R4 | exact-shape gate 使用可单独定位的 `Sxx.a/b/c` 与 source/AST predicate | 7.2.2、7.2.3 |
| R5–R8 | 调用链保持 non-normative 独立整改；State/no-persistence 只按当前 production 和 HARD KEEP gate 处理 | 1.2、2.4、5.2 |
| R9 | Phase 0 只完成 T0 设计；`GSP-A05` 批准后，target test 与对应 production 原子落地，通过后才能交付 | 6、7.2.2 |
| R11/S18 | 每个 owner 最多一个 typed count index；admission 同一次 enumeration 收集 duplicate/collision，仍先报 duplicate | 3.4、3.6、7.2.3 |
| R11/S20 | 唯一 keyword 是 `failed_retry_input: UseStepRequestInput \| None`；不接受 wide union，不保留实施期退 P2 分支 | 3.4、3.6、7.2.3 |
| R13 | manifest 按 actual change unit 生成；owner 回写与 review audit 分开，历史 review 不进入后续累计清单 | 7.3、7.6–7.8 |
| R14 | S03–S06 的 grouped exact-shape 断言登记到现有 compiled-lowering owner nodeid，并逐单元只更新自己的断言；S18 另有固定 architecture target nodeid 承载 source/AST gate | 7.2.2、7.2.3 |
| R15 | S20 只删除 materialization-only replacement State/frontier；最终 simulated frontier 与 `validate_graph_frontier` 各保持一次且顺序不变 | 3.4、3.6、7.2.2、7.2.3 |
| R16 | S23B interrupt baseline 使用经过 family-driver Result projection 的 public graph case，不再用只验证 session settlement 的 case | 7.2.1 |
| C1 | requirements 接受 per-change manifest，裁决 A03/A04 闭合，并明确只对当前矩阵中的 15 个 P1 批准 A05；9 个 P2 不继承批准 | requirements 第 6、7 节；本文 7.8、11 |
| C2 | **不接受。** 第十次复审所引用 SHA 对应的 7.4 代码块实际正好 5 行、5 个唯一 path，`mote-kernel/README.zh-CN.md` 只出现一次；删除该唯一 path 会漏掉当时真实 changed file | 7.4 |

Review 只记录裁决来源；上述规则回写后由本文拥有 target/实施含义。State、State tests、protocol 与持久化仍
无条件受第 2.4 节约束。

## 6. 原子实施顺序

### Phase 0：requirements、文档导航、characterization 与最终准入（已闭合）

1. 按第 5.1 节固定本轮最小 source precedence，并保持第 2.4 节 State/no-persistence HARD KEEP；architecture
   双语全文治理和调用链整改作为独立文档工作，不是本阶段 production 前置；
2. 维护唯一 requirements 文件 `docs/graph-semantics-preserving-simplification-requirements.zh-CN.md`：只拥有本轮
   requirement ID、行为保持义务、非目标、外部语义停止条件、阶段准入条件及其当前状态；target shape、原子
   迁移账本、实施顺序、复杂度账本和 characterization 计划继续唯一归本文所有；
3. 在 `README.zh-CN.md` 和 `README.md` 只维护稳定 owner 导航，不复制正文或枚举 review/response 历史；
4. 为每个 P1 单元建立 `GSP-P01`–`GSP-P08` 全量映射，并在 7.2.1 固定现有 `test path::test_case` 成功、失败/
   边界 evidence。S23A 接受现有 end-to-end behavior evidence，不在禁止修改 tests 的 Phase 0 为 private marker
   增加 direct characterization；
5. 为 15 个 P1 在 7.2.2/7.2.3 固定 target test path、`Sxx.a/b/c`、source/AST predicate、失败条件和预期
   manifest 类别；Phase 0 状态统一为 `DESIGNED / PENDING IMPLEMENTATION`，不创建或修改这些 tests；
6. 用第 3.6 节逐项核对 P1 的 before→after 计数、nominal signature、index/cache 生命周期和错误顺序；不能
   用 baseline 或 full-suite 绿色替代净复杂度证明；
7. Phase 0 只登记 P2 单元、owner、候选方向和未批准状态；P2 在 Phase 3/4 逐项满足 `GSP-A06`，S12 另需
   equality、malformed seed 和 generic migration 证明；
8. 每个 owner 文档回写与 review audit 分别按第 7.3 节生成自己的 actual changed-file manifest。requirements
   第 7 节已接受上述 evidence 和 per-change manifest，并只对当前 15 个 P1 明确批准 `GSP-A05`；T0 尚未编码
   是批准后的正常起点。

### 每个单元的原子步骤

requirements 已明确批准当前矩阵中的 15 个 P1。每个获批 P1 独立执行以下闭环；一个单元未过完整门禁不得
交付或开始下一个：

1. 冻结当前成功、失败、错误优先级、遍历/调用顺序和 exact shape；
2. 明确唯一 canonical owner 和目标类型；
3. 在同一最小变更中迁移全部 consumer，并删除重复 producer 字段/扫描/分支；
4. 在同一原子变更中落地对应 target test，并同步实际受影响的 normative 文档；Phase 0 不提前提交未来 shape；
5. 运行第 7 节完整门禁并核对净新增/删除账本；只有 T0 从 `DESIGNED / PENDING IMPLEMENTATION` 变为
   `PASS` 后该单元才可交付。

不得增加 forwarding property、compatibility alias 或临时双写让中间提交过门禁。

### Phase 1：不改变 compiled/recovery shape 的 P1

按单元实施 S13、S23A、S18、S08、S09、S10、S11、S17、S20、S23B。

### Phase 2：compiled/recovery shape 的 P1

按单元实施 S03、S04、S05、S06、S14。每项都必须在同一提交完成 producer/consumer、normative 文档和 exact-shape tests 迁移。

### Phase 3：engine 内部 P2

S01、S02、S07、S12、S15、S16、S19 只有满足 `GSP-A06` 并通过单项设计复审后才能实施；未通过的单元保持现状，不影响已批准 P1。S12 即使其他 P1 已获准，也不继承其批准状态。

### Phase 4：facade/transaction P2

S21、S22 只有满足 `GSP-A06` 并通过单项设计复审后才能实施，不得与其他单元混合。三个 public `run()` overload 及 transaction error timing 必须逐字保持。

### Phase 5：账本外重构

第 4 节方向需另立 requirements 和实施方案，不继承本文批准状态。

### 6.1 P1 原子依赖约束

Phase 1/2 的列表是建议执行顺序；下列同时标明真正的硬依赖和仅为降低交叉触碰的推荐顺序。约束只适用
于已获 `GSP-A05` 的单元，不能被用作提前实施的理由。

| 前置单元 | 后置单元 | 级别 | 依赖理由 | 未满足时 |
| --- | --- | --- | --- | --- |
| S03 | S04、S05、S06 | 推荐顺序 | 先从 compiled topology 去掉分类镜像，再收窄 transition/publication/input projection，减少旧 consumer 被重复触碰 | 若 owner 证明无交叉，可在单项复审后调整；不得双写 |
| S04 | S06 | 硬依赖 | publication consumer 必须在 S04 一次迁移到 `graph.transition.publications[...]`；S06 只能验证 zero-use | 任一 consumer 仍走旧 projection 即停止 |
| S08 | S09、S10、S11 | 硬依赖 | routing facts 与 join owner 先闭合，后续 projection/availability 才能消费唯一 compiled truth | 不得并行改 recovery/resume consumer |
| S09 | S10、S11 | 推荐顺序 | 先固定 facts → `ResolutionCommand` 的唯一 projection，再删除镜像 output/input diagnostics | 不得保留 `RoutingResolution`/`plan_routing()` 兼容路径 |
| S13 | S14 | 推荐顺序 | 先缩窄 `_initial_children()` 输入，再删除 `_NestedOutcome` 的 boundary 镜像，避免两个 recovery shape 同时漂移 | 若无法证明独立，保持两项现状 |
| S23A | S23B | 推荐顺序 | 先统一 advance 的 `None` 返回，再合并 awaiting-result 有序 projection，保持 family-driver 分支边界清晰 | 不得合并两个 sentinel/scan 变更 |

其余 P1（S17、S18、S20）无生产字段依赖，但仍必须各自等待同一 `GSP-A05`，并按单元 manifest 独立交付；
“无硬依赖”不等于可以合并提交或跳过完整门禁。

## 7. 验收矩阵与可复现命令

### 7.1 当前基线

- 代码基线：`7944159`（`feat(kernel): support outputs for skipped failures`）；本轮未修改 production/tests；
- `make check` 历史基线（2026-08-19，本工作区）：full suite 817 passed，coverage 100%，Pyright strict 0 errors，Ruff/build/Twine 通过；
- 第九次复审吸收后复跑 `make check`（2026-08-20）：817 passed in 44.85s，coverage 100%，Pyright 0 errors，Ruff/build/Twine 通过；该结果仍只证明当前 baseline，不能替代尚未落地的 target gate；
- State/持久化范围审计（2026-08-20）：24 个账本单元的 production location 均属于 `execution/**`；
  `test_graph_state_and_execution_contracts_have_single_owners` 与现有 `tests/state/graph_state/**` 合计
  `207 passed in 0.40s`，本轮未修改 State production/tests；
- 本轮 `monorepo pre-commit run --all-files`（2026-08-20）：通过；该结果只证明当前代码与文档的基础门禁，不替代第 7.2.1 节逐 case evidence 或 per-unit manifest gate。

`tests/typing_negative/**` 是被 `test_graph_typing_fixtures.py` 调用的 Pyright fixture，不单独作为 pytest 模块重复计数。

相关测试的可复现命令：

```bash
python -B -m pytest -q \
  tests/execution/graph/test_compiler.py \
  tests/execution/graph/test_compiler_contract.py \
  tests/execution/graph/test_topology.py \
  tests/architecture/test_graph_execution_ownership.py \
  tests/execution/engine/test_admission.py \
  tests/execution/engine/test_routing.py \
  tests/execution/engine/test_output_projection.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_recovery_boundaries.py \
  tests/execution/engine/test_runtime_boundaries.py \
  tests/execution/test_graph_recovery_contract.py \
  tests/execution/test_executor.py \
  tests/execution/engine/test_session.py \
  tests/execution/engine/test_settlement.py \
  tests/execution/test_continuation_integrity.py \
  tests/execution/test_graph_api.py \
  tests/execution/test_frame_index_contract.py \
  tests/execution/test_graph_public_typing.py \
  tests/architecture/test_graph_typing_fixtures.py
```

State/持久化 HARD KEEP 的独立复核命令：

```bash
python -B -m pytest -q \
  tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners \
  tests/state/graph_state
```

固定数字不是唯一通过条件。测试数量减少必须逐项解释被删除测试对应的已删除行为或重复覆盖；不得仅以“仍大于旧基线”为理由接受减少。

### 7.2 领域矩阵

| 领域 | 必跑测试 |
| --- | --- |
| compiler/topology/validation | `test_compiler.py`、`test_compiler_contract.py`、`test_topology.py`、`test_graph_execution_ownership.py`、`test_admission.py` |
| routing/availability | `test_routing.py`、`test_output_projection.py`、`test_resume_input_contract.py`、`test_resume_admission.py` |
| recovery identity/proof | `test_recovery_identity.py`、`test_recovery_boundaries.py`、`test_runtime_boundaries.py`、`test_graph_recovery_contract.py` |
| executor/session | `test_executor.py`、`test_session.py`、`test_settlement.py` |
| continuation/transaction | `test_continuation_integrity.py`、`test_graph_api.py`、`test_frame_index_contract.py` |
| public typing | `test_graph_public_typing.py`、`test_graph_typing_fixtures.py`、`tests/typing_negative/**` fixtures |

每个单元至少需要成功路径和失败/边界路径各一个 characterization。字段删除需覆盖 malformed/tamper；扫描合并需覆盖 sibling scope、repeated superstep、join、pure skip、replacement output；事务整理需覆盖 commit throw、non-exact successor、installation invariant failure 和 partial prefix。

#### 7.2.1 P1 requirement/characterization 映射

本节现在固定全部 15 个 P1 的 case-level evidence。`baseline` 是当前 production shape 在代码基线
`7944159` 上的 characterization；`target gate` 是对应 production 原子变更必须新增或更新的 owner/shape/tamper
断言。S08 已通过更新既有 owner 集合断言闭合，不新增 private helper/import/comprehension 的 legacy AST 断言；
S09 已迁移既有行为 case，并以 actual diff/source review 确认 DTO、wrapper 和镜像字段归零，同样不新增针对
已删除 private shape 的 legacy AST 断言。尚未落地的 gate 保持 `DESIGNED / PENDING IMPLEMENTATION`，已完成
production-only 的 S13、S18、S23A 保持
`PRODUCTION IMPLEMENTED / T0 DEFERRED`，不能把尚不存在的未来测试写成已通过。
每个 baseline case 均须有
可核对的成功与失败/边界路径；target gate 还须写明断言对象和失败条件。下表中的 `PASS` 只表示本轮
`make check` 覆盖的当前测试已经通过，不表示未来 target 已获准。

本轮按表中当前 baseline nodeids 复跑 31 个 case（含参数化展开），第九次复审替换 S23B interrupt nodeid 后
结果为 `31 passed in 0.29s`
（2026-08-20，代码基线 `7944159`）；15 个 target gate 的设计已形成。随后 S08 复用并收窄既有 architecture
owner case，没有新增 exact-shape test；其他 target 仍按各自当前状态处理。

矩阵各行的 evidence profile 固定如下：`B0` 是当前 baseline case 命令；`T0` 在 Phase 0 是已固定的 target
path、断言、失败条件和预期 manifest 类别；S08 的既有 owner gate 已通过单一集合收窄，S09 的既有行为 gate
和一次性 source review 已通过，但两个 change unit 的完整交付仍被工作树中的独立 complexity unit 阻断；尚未
落地的 T0 状态为 `DESIGNED / PENDING IMPLEMENTATION`，已落地 production-only 的 S13、S18、S23A 状态为
`PRODUCTION IMPLEMENTED / T0 DEFERRED`。`GSP-A05` 后，T0
才随对应 production 生成 actual changed-file manifest，并按 7.3 的 `pre-commit run --files`、scoped
whitespace、完整 `make check` 和 architecture exact-shape gate 执行。`B0` 已通过；T0 不得用未来文件名、
固定历史清单或未运行的测试冒充 `PASS`。

`B0` 的可复现 nodeid 命令（参数化展开后应为 31 cases）为：

```bash
python -B -m pytest -q \
  tests/execution/test_graph_api.py::test_facade_drives_nested_graph_through_the_same_execution_owner \
  tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly \
  tests/execution/test_graph_api.py::test_skip_failed_substitution_publishes_exact_output_for_downstream_materialization \
  tests/execution/engine/test_output_projection.py::test_output_projection_reports_a_missing_confirmed_publication \
  tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input \
  tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order \
  tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order \
  tests/execution/engine/test_routing.py::test_join_fires_only_after_all_sources_arrive_across_supersteps \
  tests/execution/engine/test_routing.py::test_duplicate_recovered_join_progress_fails_closed \
  tests/execution/engine/test_routing.py::test_direct_conditional_and_terminal_routing_use_one_contribution_model \
  tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance \
  tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_accepts_triggered_data_target_with_complete_inputs \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_triggered_data_target_with_an_unavailable_input \
  tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children \
  tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding \
  tests/execution/engine/test_runtime_boundaries.py::test_terminal_child_projects_its_matching_parent_result_variant \
  tests/execution/engine/test_runtime_boundaries.py::test_child_projection_validates_terminal_state_before_projecting_variant \
  tests/execution/test_graph_api.py::test_pure_skip_future_proof_accepts_a_substitution_candidate_path \
  tests/execution/test_graph_api.py::test_pure_skip_future_proof_rejects_output_lost_after_a_runnable_step_before_commit \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_keeps_distinct_scope_coordinates_isolated \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_duplicate_and_confirmed_substitution_coordinates \
  tests/execution/test_executor.py::test_resume_projection_covers_override_default_skip_and_interrupt_input_guards \
  tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication \
  tests/execution/test_graph_api.py::test_facade_fails_closed_if_internal_preparation_requests_nested_coordination \
  tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run \
  tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run
```

第六次复审 R3 的 owner-specific 补充 profile 记为 `B1`。以下 12 个现有 nodeid（参数化展开为 15 cases）
已在同一代码基线定向复跑，结果为 `15 passed in 0.20s`：

```bash
python -B -m pytest -q \
  tests/execution/engine/test_output_projection.py::test_output_projection_rejects_a_compiled_binding_without_activation_selection \
  tests/execution/engine/test_recovery_identity.py::test_recovery_historical_output_scan_retains_present_outputs_before_the_gap \
  tests/execution/engine/test_recovery_identity.py::test_recovery_historical_target_scan_retains_present_inputs_before_the_gap \
  tests/execution/engine/test_routing.py::test_completed_joins_and_direct_arrivals_deduplicate_targets \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_keeps_distinct_scope_coordinates_isolated \
  tests/execution/engine/test_resume_admission.py::test_resume_admission_keeps_repeated_superstep_coordinates_isolated \
  tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children \
  tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_propagates_an_awaiting_child_boundary \
  tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding \
  tests/execution/test_graph_api.py::test_duplicate_public_skip_candidates_are_rejected_before_commit \
  tests/execution/test_graph_facade_boundaries.py::test_resume_dispatch_rejects_non_tuple_noncanonical_and_unknown_scope \
  tests/execution/engine/test_recovery_boundaries.py::test_final_settlement_recovers_as_ready_to_resolve_without_reexecution
```

`B1` 只补现有 owner evidence，不把尚未落地的 target test 写成通过。R3/R12 的精确口径如下：

| 单元 | B1 owner evidence | 当前覆盖结论 | 随 production 落地的 T0 |
| --- | --- | --- | --- |
| S10 | malformed graph-output selection、historical output 在 gap 前的稳定保留 | DIRECT PASS | target 的单次 full scan AST gate 仍待 T0 |
| S11 | historical target gap、direct/join 重叠 target 去重、sibling scope、repeated superstep | DIRECT PASS | target cache 首次访问/首个 unavailable identity 的 AST/断言仍待 T0 |
| S14 | completed/aborted、awaiting child boundary 及 malformed child control 均由 `engine/recovery.py` 路径覆盖 | DIRECT PASS | `_NestedOutcome` exact field/control projection gate 仍待 T0 |
| S18 | public duplicate skip 在首个 commit 前由 `plan_resumes()` 拒绝；non-tuple/noncanonical/unknown scope 保持 invocation 错误顺序；resume-admission coordinate cases 由 B0/B1 覆盖 | DIRECT PASS | 两个 owner 的 no-`.count()`/no-double-enumeration AST gate 仍待 T0 |
| S23A | final settlement → `ReadyToResolve`、facade nested success/fail-closed 均经过现有 driver | BEHAVIOR PASS（INDIRECT OWNER COVERAGE） | `_AdvancedFrontier` 归零、return annotation、advance/non-boundary `None` 与 nested boundary/error 分类由 7.2.2 的 direct target gate 验证 |

S23A 删除的是 private `_AdvancedFrontier` marker；Phase 0 需要冻结的是 `drive_root()` 的外部循环行为，而不是先为
即将删除的 private return variant 新增 characterization。现有 end-to-end cases 作为 B0/B1 behavior evidence
已经足够；direct exact-shape/return-class assertion 与 S23A production 在 `GSP-A05` 后原子落地。不得把它提前
写成当前 PASS，也不得把 direct baseline 缺失继续当作 A05 blocker。

| 单元 | 全部适用 requirements | baseline 成功路径与断言（当前） | baseline 失败/边界路径与断言（当前） | baseline 状态 |
| --- | --- | --- | --- | --- |
| S03 | `GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_facade_drives_nested_graph_through_the_same_execution_owner`；完成结果通过同一 facade/engine，提交中存在 child scope | `tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly`；child boundary 不匹配在 compile 前抛 `GraphValidationError` | B0 PASS / T0 DESIGNED / PENDING IMPLEMENTATION |
| S04 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_skip_failed_substitution_publishes_exact_output_for_downstream_materialization`；replacement publication 被下游消费并得到 `accepted:replacement` | `tests/execution/engine/test_output_projection.py::test_output_projection_reports_a_missing_confirmed_publication`；缺失 confirmed publication 抛 `GraphValueAdmissionError` | PASS（31-case run） |
| S05 | `GSP-P01`、`GSP-P03`、`GSP-P04`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input`；admitted graph input 按声明投影为 renamed output | `tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly`；nested boundary mismatch 保持 compile-time rejection | PASS（31-case run） |
| S06 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order`；graph、node、resource tuple 均保持 canonical FIFO 顺序 | `tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order`；顺序 tamper 在 admission 前抛 `ResourceTransitionError` | PASS（31-case run） |
| S08 | `GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_routing.py::test_join_fires_only_after_all_sources_arrive_across_supersteps`；首步保留 join progress，全部 source 到达后才 advance target | `tests/execution/engine/test_routing.py::test_duplicate_recovered_join_progress_fails_closed`；重复 recovered progress 抛 `JoinProgressError` | B0 PASS / PRODUCTION IMPLEMENTED / OWNER GATE PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S09 | `GSP-P02`、`GSP-P05`、`GSP-P06`、`GSP-P08` | `tests/execution/engine/test_routing.py::test_direct_conditional_and_terminal_routing_use_one_contribution_model`；direct/conditional/terminal 产生既有 `AdvanceGraphFrontier`/`CompleteGraphFrontier` | `tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance`；缺失 control input 先产生 `AbortGraphRun`，不 advance | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S10 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input`；可用 graph output 正常完成并保留投影值；B1 historical-output case 固定 gap 前已见 output 的顺序 | `tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable`；completion output 不可用时 abort；B1 malformed selection 保持 `InvalidRoutingCommandError` 优先 | B0 PASS / B1 DIRECT PASS / T0 DESIGNED / PENDING IMPLEMENTATION |
| S11 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_resume_admission.py::test_resume_admission_accepts_triggered_data_target_with_complete_inputs`；完整 input 被 admission；B1 覆盖重叠 target、sibling scope、repeated superstep 与 gap 前 present input | `tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_triggered_data_target_with_an_unavailable_input`；缺失 input 在 admission 前抛 `GraphValueUnavailableError`，首个 binding/target 顺序由 B1 锁定 | B0 PASS / B1 DIRECT PASS / T0 DESIGNED / PENDING IMPLEMENTATION |
| S13 | `GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children`；completed/aborted child 映射到预期 boundary status | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding`；run/parent control tamper 抛 `SnapshotMismatchError` | PASS（31-case run） |
| S14 | `GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children` 与 `::test_recovery_preflight_propagates_an_awaiting_child_boundary`；recovery owner 保持 completed/aborted/awaiting boundary control | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding`；run/parent/control tamper 保持 `SnapshotMismatchError` | B0 PASS / B1 DIRECT PASS / T0 DESIGNED / PENDING IMPLEMENTATION |
| S17 | `GSP-P02`、`GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_pure_skip_future_proof_accepts_a_substitution_candidate_path`；pure skip + replacement 完成，consumer 看到 replacement | `tests/execution/test_graph_api.py::test_pure_skip_future_proof_rejects_output_lost_after_a_runnable_step_before_commit`；历史 output 丢失抛 `ValueUnavailableError`，commit 仍为空 | PASS（31-case run） |
| S18 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_resume_admission.py::test_resume_admission_keeps_distinct_scope_coordinates_isolated` 及 repeated-superstep B1 case；不同 coordinate 保持隔离 | `tests/execution/test_graph_api.py::test_duplicate_public_skip_candidates_are_rejected_before_commit` 直接覆盖 `plan_resumes()` duplicate action coordinate；resume-admission duplicate/confirmed collision 保持 `GraphValuePublicationError` | B0 PASS / B1 DIRECT PASS / PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S20 | `GSP-P02`、`GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_executor.py::test_resume_projection_covers_override_default_skip_and_interrupt_input_guards`；override/default/skip/interrupt 各自保留既有 input guard | `tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication`；materialization 缺失 node output 抛 `GraphValueUnavailableError` | PASS（31-case run） |
| S23A | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | facade nested success 与 `test_final_settlement_recovers_as_ready_to_resolve_without_reexecution` 间接证明 root loop/resolve 可继续 | facade nested coordination case 保持 fail closed；private return shape 已落地，direct T0 因不新增测试而 deferred | B0/B1 BEHAVIOR PASS（indirect owner coverage）；PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S23B | `GSP-P01`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run`；failure actions canonicalize 且结果共享 run | `tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run`；public case 读取 `AwaitingResumeResult.interrupts[0]` 及其 `interrupt_id`，实际经过 Result projection，stale ID 仍 fail closed；payload/mixed-scope order 继续由 S23B T0 冻结 | PASS（31-case run） |

上述 15 行的 baseline behavior 均为 `PASS`；S23A 的 owner coverage 是 indirect，但足以冻结外部循环语义。
每行对应的 `T0` target path、断言和失败条件均已设计；尚未实施的单元保持
`DESIGNED / PENDING IMPLEMENTATION`。S09 已按最终裁决以既有行为 gate 和一次性 source review 闭合，不新增
legacy AST test；S13、S18 与 S23A 则按后续 owner writeback 标记为
`PRODUCTION IMPLEMENTED / T0 DEFERRED`。requirements 第 7 节已依据本矩阵只批准这 15 个 P1；其余单元的
T0 仍须随对应 production 原子落地并通过后才可交付。

#### 7.2.1（续）保守适用性审计

下列审计专门记录复审指出的交界项；其余映射按第 3 节的“触及语义即合并全部 ID”规则逐行复核。
“排除”只表示该单元的 target 不触及该 ID 所拥有的可观察边界，不表示实现可以跳过全量门禁。

| 单元/交界项 | 接受的适用 ID 与理由 | 明确排除及理由 |
| --- | --- | --- |
| S04 publication/admission/nested consumer | `P03`（admission、publication installation 与 partial handoff）；`P04`（publication/frame provenance）；`P05`（output/routing availability）；`P06`（recovery consumer）；`P07`（nested/family 与 canonical consumer）；`P08`（nominal value shape、owner/dependency） | `P01`、`P02`：不改 public signature、overload、State command、revision 或 durable control fact |
| S05 graph-input owner | `P01`（graph input public projection）；`P03`（admission）；`P04`（input/frame coordinate）；`P06`（recovery boundary）；`P07`（nested child boundary）；`P08`（CompiledGraph owner、imports、dependency direction） | `P02`、`P05`：不改 State command/revision，也不改 routing/skip algorithm；availability 只作为既有 admission input 被消费 |
| S06 resource order/projection | `P03`（admission boundary）；`P04`（publication/continuation projection）；`P05`（output/routing consumers）；`P06`（recovery compiled truth）；`P07`（resource first-seen/FIFO 与 canonical order）；`P08`（唯一 compiled owner） | `P01`、`P02`：仅收窄 private compiled shape，不改 public signature 或 State command/revision |
| S09 `ResolutionCommand` projection | `P02`（State command/revision projection）；`P05`（routing/availability）；`P06`（recovery consumes same facts）；`P08`（唯一 routing owner与 typed projection） | `P01`、`P03`、`P04`、`P07`：不改 public API、commit/install 时序、Result/frame shape 或 nested/resource owner |
| S10/S11 output、publication、resume availability | `P03`（admission/abort boundary）；`P04`（publication/frame availability）；`P05`（routing/skip）；`P06`（recovery diagnostic）；`P07`（canonical target/resource ordering）；`P08`（RoutingFacts/typed scan owner 与依赖纪律） | `P01`、`P02`：不改 public signature、State command 或 revision |
| S18 publication/action collision identity | `P03`（admission collision boundary）；`P04`（coordinate/publication identity）；`P05`（skip/resume action）；`P07`（scope/repeated-superstep ordering）；`P08`（typed owner-local index） | `P01`、`P02`、`P06`：不改 public API、durable command 或 recovery proof/budget |
| S20 resume materialization | `P02`（codec identity与State-owned control）；`P03`（admission/install boundary）；`P04`（frame/publication identity）；`P05`（resume/skip input availability）；`P07`（nested scope coordinate）；`P08`（窄 typed materializer 与 owner direction） | `P01`、`P06`：不改 public signature，也不改 recovery proof/equality/budget |
| S23B Result view projection | `P01`（public Result view）；`P04`（failure/interrupt identity）；`P05`（settlement projection）；`P07`（scope/canonical ordering）；`P08`（唯一 family-driver projection owner） | `P02`、`P03`、`P06`：不改 State command/revision、commit timing 或 recovery proof |

该表是本文的 applicability evidence，不会把 `ResolutionCommand`、`ScopedFrameIndex` 或任何 target type
升级为公共规范；若 owner 裁决改变 requirement anchor，必须重新做全量 audit，而不是沿用“历史上已映射”的结论。
映射 `GSP-P02/P03` 只表示相应 State/transaction 行为必须保持，不授权修改 State、command、reducer、commit
port 或实现持久化；全部单元仍无条件受第 2.4、7.3 节的 HARD KEEP gate 约束。

#### 7.2.2 P1 target exact-shape/tamper gate

Phase 0 已固定所有 target gate 的 path、子断言和失败条件；当前状态按下表逐项记录。获得 `GSP-A05` 后，标为
“更新现有 case”或“待新增 case”的 gate 都必须
与对应 production、实际受影响的 normative source 在同一原子变更中落地。不能用文件级测试名或历史 PASS
代替；失败条件是旧字段/旧 owner/重复扫描仍存在，或 tamper 没有在预期边界 fail closed。

| 单元 | exact-shape/tamper gate（路径、断言目标、失败条件） | 当前状态 |
| --- | --- | --- |
| S03 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S03.a` 子断言：`FrontierTransitionPlan` 无 `callable_node_ids`/`nested_node_ids` field；`S03.b`：compiler 不再构造这两个 tuple；`S03.c`：consumer 只从 `nested_graphs`/`nodes` nominal variant 推导；任一旧 symbol/producer path 即失败。该 grouped owner case 随 S03 原子变更只落地 S03 断言，不提前断言 S04–S06 shape | DESIGNED / PENDING IMPLEMENTATION |
| S04 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S04.a`：`outcomes`/`publications` DTO 与 `CompiledGraph` projection 不存在；`S04.b`：`MaterializationPlan` 无重复 `node_id`；`S04.c`：publication map value exact 为 `FrameDescriptor[...]` 且 consumer 访问 `graph.transition.publications[node_id]`；malformed publication 继续由 `tests/execution/test_continuation_integrity.py::test_complete_continuation_rejects_a_malformed_publication_record` fail closed。该 grouped owner case 随 S04 原子变更只落地 S04 断言 | DESIGNED / PENDING IMPLEMENTATION |
| S05 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S05.a`：`CompiledGraph` 不定义 `graph_inputs` field/property/alias；`S05.b`：compiler/runtime 只读取 `graph_input_descriptor.declarations`；`S05.c`：`tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly` 仍在 compile 前拒绝 boundary mismatch。该 grouped owner case 随 S05 原子变更只落地 S05 断言 | DESIGNED / PENDING IMPLEMENTATION |
| S06 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S06.a`：`recovery` wrapper/storage 不存在；`S06.b`：`transition` 是 direct dataclass field，不是 property；`S06.c`：`entries`/`materializations`/`publications`/`graph_outputs`/`resource_order` 不在 `CompiledGraph` 定义；resource-order tamper 仍由 `tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order` 拒绝。该 grouped owner case 随 S06 原子变更只落地 S06 断言 | DESIGNED / PENDING IMPLEMENTATION |
| S08 | 更新既有 `tests/architecture/test_graph_execution_ownership.py::test_compiled_routing_is_interpreted_only_by_routing_and_snapshot_guard`，只把 `joins_by_source` 的 production direct-owner 集合从 routing + snapshot guard 收窄为 routing。`_declared_joins` 的 import/call、helper symbol 和 comprehension 形状只作为本次 actual diff/source review，不新增永久 legacy AST 断言；重复/malformed progress 继续由既有行为测试 fail closed | OWNER GATE PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S09 | 更新既有 `tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance` 与 `tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable`，直接断言既有 abort command/reason；DTO、forwarding wrapper、镜像字段和全部 consumer 迁移只作为本次 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S10 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_routing_facts_have_one_canonical_output_diagnostic`：`S10.a` 要求 resolver 内只有一次 full binding scan；`S10.b` 只保留 `unavailable_graph_outputs`，两个镜像 bool 不存在；`S10.c` 独立 `graph_outputs_available` 仍短路并保留首个 missing/error order；completion output 缺失仍 abort/error | DESIGNED / PENDING IMPLEMENTATION |
| S11 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_routing_target_facts_have_one_typed_scan`：`S11.a` `RequiredTarget` exact 三字段；`S11.b` 每个 unique target 只有一次 binding scan，cache key/value exact 为 `GraphNodeId/RequiredTarget`；`S11.c` source-kind、control→join→data 顺序和 first unavailable identity 固定；unavailable input 仍在 admission 前拒绝 | DESIGNED / PENDING IMPLEMENTATION |
| S13 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_initial_children_signature_contains_only_consumed_inputs`：`_initial_children()` 不再接受未使用 availability 参数或构造 phantom input；malformed child binding 仍 fail closed | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S14 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_nested_outcome_keeps_boundary_owned_identity`：`S14.a` `_NestedOutcome` 仅两个字段；`S14.b` kind/availability 只读 boundary；`S14.c` disposition 只由 `ScopeControlStateCoordinate` projection 生成，不读取 `compare=False` state；非-terminal child 仍拒绝 projection | DESIGNED / PENDING IMPLEMENTATION |
| S17 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_resume_candidate_derives_skip_actions_from_command`：candidate 不再存储 `skip_actions`/`has_pure_skip` 镜像；pure-skip historical output loss 仍在 commit 前 fail closed | DESIGNED / PENDING IMPLEMENTATION |
| S18 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_resume_duplicate_indexes_are_owner_local_and_linear`，承载 `S18.a` invocation/admission 的两个 typed count dict、每 owner 一个 index、`S18.b` 无 `.count()`、`S18.c` 无先 `any` 后重扫且 duplicate-before-collision；`tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_duplicate_and_confirmed_substitution_coordinates`、`::test_resume_admission_keeps_repeated_superstep_coordinates_isolated` 和 `tests/execution/test_graph_api.py::test_duplicate_public_skip_candidates_are_rejected_before_commit` 继续证明行为、错误 identity 和 coordinate isolation | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S20 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_resume_materialization_does_not_construct_temporary_state`，其 `S20.a` 只允许既有 `materialize_node_input` owner 和唯一 `failed_retry_input: UseStepRequestInput \| None` keyword；`S20.b` 只禁止 failed-retry materialization 分支构造 replacement State/frontier，最终 `simulated = GraphFrontierState(...)` 与 `validate_graph_frontier(state, simulated)` 各保留一次，override 仍只走 codec；`S20.c` failed/pending identity、materialization missing publication 与现有错误优先级保持 | DESIGNED / PENDING IMPLEMENTATION |
| S23A | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_family_driver_uses_none_for_advance_without_marker`：`S23A.a` return annotation 无 `_AdvancedFrontier`；`S23A.b` AdvanceGraphFrontier、普通 non-boundary 均返回 `None`；`S23A.c` nested coordination boundary/error 分类不变 | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S23B | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_awaiting_result_views_use_one_ordered_projection`：`S23B.a` root→child 只一次 scoped-state scan；`S23B.b` 返回两个 typed tuple；`S23B.c` public Result/interrupt identity 与 mixed-scope order 不变，interrupt settlement mismatch 仍 fail closed | DESIGNED / PENDING IMPLEMENTATION |

上述 target gate 是已完成的 Phase 0 设计，不冒充当前已存在的测试，也不要求在 `GSP-A05` 前落地。
requirements 已依据 baseline behavior、这张 target 设计表及 `GSP-A01`–`GSP-A04` evidence 完成 `GSP-A05`
授权；每个 gate 必须与对应 production 原子落地，T0 未通过的单元不得交付或进入下一单元。

S08 已复用既有 architecture owner case，只收窄 `joins_by_source` 的 direct-owner 集合；没有新增针对
`_declared_joins` 私有 symbol、import/call 或具体 comprehension 的 legacy AST 断言。production actual diff 和
一次性 source review 已确认 snapshot guard 复用 routing owner，既有 malformed/duplicate join progress 行为用例
保持 fail closed，因此 S08 的 owner target 已通过；完整 change unit 仍须在独立 complexity framework 与 S08
manifest 分离后重跑不可替代门禁，当前不记为整体交付 `PASS`。

S09 已把既有 routing/output/recovery 行为用例迁移为直接消费 `ResolutionCommand`，并从既有 architecture case
删除对已移除 `plan_routing` 名称的禁止断言，没有用新测试冻结 `RoutingResolution`、wrapper 名称或 private return
shape。actual diff/source review 已确认活跃 production、tests 与 skip-output normative 中旧五字段 DTO、
forwarding wrapper、四个镜像字段 consumer 和 `.command` wrapper 读取均为 0；missing control input、completion
output 缺失和 recovery historical gap 仍在原边界 fail closed。因此 S09 的行为/source target 已通过，但完整
工作树仍受独立 complexity unit 阻断，
当前不记为零负债整体交付 `PASS`。

S13 的 `PRODUCTION IMPLEMENTED / T0 DEFERRED` 不是 `PASS`：production 已完成，但本次明确不新增
exact-shape architecture test，因此该单元仍不满足 T0 交付条件。其余 target gate 仍保持
`DESIGNED / PENDING IMPLEMENTATION`。

S23A 同样标记为 `PRODUCTION IMPLEMENTED / T0 DEFERRED`：production 已完成，但本次按约束不新增
`test_family_driver_uses_none_for_advance_without_marker` exact-shape architecture test，因此不将其写成
T0 `PASS`。S23B 仍保持独立的 `DESIGNED / PENDING IMPLEMENTATION` 状态。

S18 也标记为 `PRODUCTION IMPLEMENTED / T0 DEFERRED`：两个 owner 的 production 检查已完成，但本次按约束
不新增 `test_resume_duplicate_indexes_are_owner_local_and_linear` exact-shape architecture test，因此不将
S18 写成 T0 `PASS`。

#### 7.2.3 Source/AST 子断言（R4 可执行口径）

以下谓词必须由对应 architecture owner gate 或等价的静态检查直接执行；只检查文件名、注释或测试名称
不算通过。S08/S09 按 7.2.2 的最终裁决复用既有行为/owner gate，并以各自 implementation actual diff/source
review 闭合，不为已删除 private symbol 或具体表达式形状新增永久断言。除这两个单元外，每个 predicate 的可复现
`path::test_case` 由 7.2.2 同一单元行注册：S03–S06 使用
`test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 并逐原子单元只更新自己的分组断言，
S18 使用待新增的 `test_resume_duplicate_indexes_are_owner_local_and_linear`；本节的 predicate 表不能脱离这些
nodeid 单独充当 `GSP-A03` evidence。谓词针对目标提交的最终 source shape，不改变当前 baseline，也不允许通过
兼容 alias 绕过：

| 单元 | 必须为真的 source/AST 谓词 | 失败条件 |
| --- | --- | --- |
| S03 | `FrontierTransitionPlan.__dataclass_fields__` 不含 `callable_node_ids`/`nested_node_ids`；`compiler.py` 不构造同名 keyword；consumer 只出现 `nested_graphs[...]` 或 `CallableNodeDefinition` nominal check | 任一旧字段、producer keyword、旧 projection 读取残留 |
| S04 | `OutcomeAdmissionPlan`/`PublicationPlan` 不再被 topology/compiler/runtime imports；`MaterializationPlan` 无 `node_id` field；`CompiledGraph` 无 `outcomes`/`publications` attribute；publication consumer 的最终访问形状为 `graph.transition.publications[node_id]`，其 value 不再 `.descriptor` | DTO/import/property/重复 key 残留，或 consumer 继续走旧 projection |
| S05 | `CompiledGraph.__dataclass_fields__` 不含 `graph_inputs`；`CompiledGraph` `__dict__`/class MRO 无同名 property；nested compiler/admission 只读取 `graph_input_descriptor.declarations` | field、property、alias 或第二 graph-input declaration source |
| S06 | `CompiledGraph.__dataclass_fields__["transition"]` 存在且不是 property；不含 `recovery`/`entries`/`materializations`/`publications`/`graph_outputs`/`resource_order`；`RecoveryAvailabilityPlan` 无 production import | wrapper/forwarding property/direct-field 反转或 publication projection 回归 |
| S08 | 既有 owner gate 只允许 routing 直接读取 `joins_by_source`；snapshot guard 对 routing owner 的复用由本次 actual diff/source review 核对，不新增 private helper/import/comprehension AST 断言 | owner gate 仍登记 snapshot guard 或其他 production 模块为 `joins_by_source` direct reader；行为用例不再对 malformed/duplicate progress fail closed |
| S09 | 本次 actual diff/source review 确认 `RoutingResolution` class 和 `plan_routing` definition/import 数为 0；`project_routing_facts` 与 `resolve_routing` return annotation 均为 `ResolutionCommand`；recovery 只保留同一 local facts | forwarding DTO/wrapper 残留、command projection 分叉或第二 facts scan |
| S10 | `resolve_routing_facts` 内 `graph_outputs_available` 调用数为 0、`unavailable_graph_outputs` 调用数为 1；`RoutingFacts` 只含 canonical diagnostic tuple，不含两个 completion bool | full diagnostic 重复扫描、短路 helper 被错误删除或 bool mirror 回归 |
| S11 | `RequiredTarget.__dataclass_fields__` 精确为 `node_id/historical_inputs_missing/unavailable_inputs`；`unavailable_target_inputs` 与 historical-gap scan 不再对同一 target 各调用一次；local cache annotation 为 `dict[GraphNodeId, RequiredTarget]` | field/scan/cache key 漂移、首错顺序改变或新增 display identity |
| S14 | `_NestedOutcome.__dataclass_fields__` 精确为 `node_id/boundary`；`_child_disposition_from_control` 参数不含 `GraphRunState`/`_ScopeBoundary.state` | 镜像字段、compare=False state 重建 identity 或第二 child projection |
| S17 | `ScopedResumeCandidate.__dataclass_fields__` 不含 `skip_actions`/`has_pure_skip`；每个 candidate 的 command-action tuple 与 action-publication/substitution coordinate difference 只保留一个 local derivation 生命周期 | mirror field、重复 action scan、忽略 descriptor/scope coordinate 或纯 skip 通过独立 bool 恢复 |
| S20 | `materialize_node_input` 是唯一 materializer symbol，签名只允许 `failed_retry_input: UseStepRequestInput \| None = None`；`GraphNodeInputBinding`/`OverrideGraphNodeInput` 不进入该 keyword；`executor.py` 的 failed-retry materialization 分支不构造 `replace(state, frontier=...)` 或 replacement `GraphFrontierState`；最终 `simulated = GraphFrontierState(...)` 和 `validate_graph_frontier(state, simulated)` 各存在且调用一次 | wide union、同义 wrapper、第二 materializer、删除/后移 final simulation 或 validation、materialization-only replacement State/frontier 残留 |
| S23A | `_AdvancedFrontier` symbol/union member/constructor/reference 数为 0；`_advance_scope_quantum` annotation 为 `GraphBoundary \| None`，`drive_root` 无 marker `isinstance` | sentinel、第二 disposition 或 loop 分支语义漂移 |
| S23B | `_project_result_views` 只调用一次 `_scoped_states`，返回两个 typed tuple；`_failure_views`/`_interrupt_views` 不再各自完整枚举 | 两次 scoped scan、Result mirror 或 mixed-scope ordering 改变 |
| S18 | `invocation.py` 只有 `dict[tuple[tuple[str, ...], GraphNodeId], int]` action-count index；`engine/resume_admission.py` 只有 `dict[PublicationAvailabilityCoordinate[GraphValueT], int]` publication-count index；admission 在同一次 canonical enumeration 中收集 duplicate/collision；source AST 不出现 `.count(` 或先 `any(...)` 后再次完整枚举 | 第三个 index、O(n²) count、双扫描、跨 owner generic helper、duplicate/collision 报错顺序或 identity 顺序改变 |

除上述逐项谓词外，7.2.2 中每个 `Sxx.a/b/c` 子断言都必须在同一 owner 测试中可单独定位。静态检查发现
任何 `Any`、`object`、动态导入、反射或 compatibility alias 时，按第 2.3 节和 `GSP-P08` 立即失败；不能以
Ruff/formatter 通过代替 source-discipline gate。

### 7.3 不可替代的完整门禁

```bash
python -B -m pytest -q \
  tests/architecture/test_generic_integrity.py \
  tests/architecture/test_source_discipline.py \
  tests/architecture/test_dependency_direction.py \
  tests/architecture/test_graph_execution_ownership.py \
  tests/architecture/test_graph_typing_fixtures.py
make check
cd .. && pre-commit run --all-files
git diff --check
```

上面的 `pre-commit run --all-files` 和 `git diff --check` 只作为最终 tracked baseline，不能替代每个原子变更单元
的 changed-file gate。门禁不以修改 index 为前置条件，也不硬编码某一批 review 文档。每个单元必须先生成
完整、repo-relative 的 `changed-file manifest`，只纳入该单元**实际新增或修改**的 production、tests、normative、
requirements/navigation、owner 文档或 audit 记录；未变化的历史文件不得为凑固定清单重复加入。

owner 回写与 review audit 是两个独立单元：review 文件只因该 audit 单元实际创建或修改而进入它自己的 manifest；
review 结论回写 requirements/implementation 时，再为实际回写文件生成 owner 单元 manifest。一个 review 的
存在不会把它和全部历史 review 自动带入后续 owner manifest，最终 review 也不要求再创建一轮 review 才生效。
随后对各自 manifest 执行同一种策略：

在运行命令前先执行第 2.4 节的范围 gate。以下不是“需要额外评审即可通过”的候选，而是本方案内直接失败：

| manifest/source 发现 | 裁决 |
| --- | --- |
| 修改 `src/mote_kernel/state/**` 或 `tests/state/**` | 越界；停止当前单元，恢复为当前 State/test shape |
| 修改或新增 durable/conformance/protocol artifact | 越界；本轮没有协议迁移 |
| 新增 Store/repository/persistence/database/journal/checkpoint/event-log/default commit backend | 越界；另立需求，不得纳入 P1/P2 |
| 改变 `GraphRunState` fields、`GraphRunCommand` variants、reducer/validation 或 codec identity/version | 越界；不得用 adapter、alias 或测试迁移掩盖 |
| 仅在 `execution/**` 中读取现有 State、构造现有 command、调用现有 reducer/commit callback | 可继续，但必须由原有 State/transaction characterization 证明行为完全一致 |
| 本单元实际修改的文档把 callback exact confirmation 写成外部持久化/耐久成功/无条件 crash recovery，或把 `Graph.resume_*()` 写成第二 runner | 直接触发 R5–R7；停止该单元并回到 owner 裁决；未修改的调用链草稿不进入 manifest，也不作准入证据 |
| 任何 manifest 文档把外部 callback/Store 的原子性写成 kernel 无条件保证，或把 candidate object equality 写成 identity/durability | 直接触发 R8；只允许“现有 callback 边界/结构相等确认/调用方责任”措辞 |

所有 P1/P2 的 cross-unit negative gate 固定复跑
`tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners`，
并要求该 case 的现有 State owner/shape expectation不因本轮修改；`make check` 中的 `tests/state/**` 也必须原样
通过。该 gate 不新增第 25 个实施单元，只是每个 `T0` manifest 的共同前置条件。

```bash
# 从 monorepo root 执行；<unit-files> 是本原子单元完整的 repo-relative path 列表
pre-commit run --files <unit-files>
git diff --check -- <tracked-unit-files>
git diff --cached --check -- <tracked-unit-files>

# 每个 untracked path 单独执行；exit 1 且无输出才表示“只有 no-index 内容差异”
check_output=$(mktemp)
if git diff --no-index --check /dev/null <untracked-unit-file> >"$check_output" 2>&1; then
  check_status=0
else
  check_status=$?
fi
if test "$check_status" -ne 1 || test -s "$check_output"; then
  cat "$check_output"
  exit 1
fi
rm -f "$check_output"
```

`<unit-files>` 必须同时传给 `pre-commit run --files`；`<tracked-unit-files>` 只列 tracked path，并同时检查
staged/unstaged diff；`<untracked-unit-file>` 逐个列出 untracked path。每次交付记录必须把占位符展开为 exact
paths，带占位符的模板本身不能作为通过证据。无 whitespace diagnostic 才算通过，不能以“文件未 staged”、
历史上曾经修改或固定文件数量代替覆盖证据。review audit 至少运行同一 manifest 的 `pre-commit --files` 与
whitespace gate；若它没有 production/tests，就不冒充 production gate。门禁不主动修改 index。

其中：

- `test_generic_integrity.py` 不得被局部 Pyright 替代；
- `test_graph_execution_ownership.py` 唯一拥有 topology exact-shape 与 compiled lowering 访问路径断言；
- `test_source_discipline.py` 只拥有连续模块头、`Any`、动态导入和反射纪律，不得被 Ruff import sorting 替代，也不得复制 topology owner 断言；
- `test_dependency_direction.py` 不得被模糊的 owner check 替代；
- `make check` 必须覆盖 Ruff、Pyright、full pytest/coverage、build 和 package check；
- monorepo pre-commit、每个原子单元的 manifest gate，以及 scoped `git diff --check`/`git diff --cached --check` 必须在交付前通过。

### 7.4 Phase 0 文档单元验证记录（历史记录，2026-08-19）

本次只纳入该文档单元实际新增或修改的五个 repo-relative paths：

```text
mote-kernel/README.md
mote-kernel/README.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation-fourth-review.zh-CN.md
```

该记录只证明当时五文件文档单元的 gate；前序 review/response 和历史调研未在当时单元修改，因此不为凑固定清单重复加入。
它不是当前整个 Phase 0 的 cumulative manifest。以该 exact manifest
从 monorepo root 执行 `pre-commit run --files ...` 已通过；两个 tracked README 的
`git diff --check`/`git diff --cached --check` 均无诊断；三个 untracked Markdown 逐个执行
`git diff --no-index --check /dev/null <path>`，均为 exit 1 且无输出。验证未修改 Git index。

### 7.5 第五次复审回复文档单元记录（历史记录，2026-08-19）

本次回复单元实际新增或修改的 repo-relative paths 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation-fifth-review.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation-fifth-review-response.zh-CN.md
```

该 manifest 只覆盖当时评审回复记录和关联 owner 回写，不把尚未实施的 P1/P2 production、tests 或 normative
shape 变更混入本单元。它是历史记录，不进入后续单元，也不把旧的四文件数字当作当前覆盖证据。

### 7.6 第八次复审吸收的 owner 文档单元（2026-08-20）

本次只修改实施方案，exact repo-relative manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

第八次复审文件是本单元的既有 audit 输入，本次未修改，因此不进入 owner 回写 manifest；它作为 review audit
独立运行轻量 gate。第七次复审后曾记录的 16-path cumulative cutoff 只保留历史审计意义，自本节起废止为
后续准入模型，不再沿用或扩成 17-path。此前 review/response、README、requirements、调用链和历史调研均未在
本次 owner 回写中修改，故不重复纳入。

最终验证从 monorepo root 对上述 one-path owner manifest 执行 `pre-commit run --files`，全部 hooks 通过；
`git diff --no-index --check /dev/null <implementation-path>` 返回预期 exit 1 且无输出。第八次复审 audit path
独立执行相同的 scoped pre-commit 与 whitespace gate，也分别通过、返回预期 exit 1 且无输出。两次验证均未
修改 Git index，不冒充 production/tests gate，也不由本文自行裁决 `GSP-A04` 或 `GSP-A05` 状态。

### 7.7 第九次复审吸收的 owner 文档单元（2026-08-20）

本次只修改实施方案，exact repo-relative owner manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

第九次复审文件是本单元的既有 audit 输入，本次未修改，不进入 owner 回写 manifest；其 exact audit manifest
独立为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation-ninth-review.zh-CN.md
```

从 monorepo root 对 owner manifest 与 audit manifest 分别执行 scoped `pre-commit run --files`，全部 hooks
通过；两条 `git diff --no-index --check /dev/null <path>` 均返回预期 exit 1 且无输出。B0 复跑 R16 替换后的
exact nodeid 集合，结果为 `31 passed in 0.29s`。验证未把 audit path 合并到 owner manifest，未修改 Git index，
也不冒充 production/T0 gate 或自行批准 `GSP-A05`。补充完整验证为 `make check` 817 passed、coverage 100%、
Pyright/Ruff/build/Twine 全部通过，monorepo `pre-commit run --all-files` 全部通过。

### 7.8 Requirements 终局准入 owner writeback（2026-08-20）

本次 requirements 最终裁决与实施文档状态同步属于同一个 owner writeback change unit，exact repo-relative
manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

第十次复审是未修改的独立 audit 输入，其 exact audit manifest 为：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation-tenth-review.zh-CN.md
```

requirements owner 在本单元接受 per-change manifest，闭合 A03/A04，并只对 15 个 P1 明确批准 A05。第十次
复审 C2 所称的 7.4 重复路径经其登记 SHA 对象复核不成立：7.4 代码块恰有 5 行、5 个唯一 path，
`mote-kernel/README.zh-CN.md` 只出现一次，因此该历史 manifest 保持不变。

owner writeback 与 audit 已分别运行 scoped pre-commit 和 untracked whitespace gate：全部 hooks 通过，三个
untracked Markdown 的 `git diff --no-index --check /dev/null <path>` 均返回预期 exit 1 且无输出。完整
`make check` 通过：Ruff、格式检查、Pyright 0 errors、817 tests、coverage 100%、build 与 Twine 全部通过；
monorepo `pre-commit run --all-files` 也全部通过。两类 manifest 保持分离，验证未修改 Git index，也不把文档
批准冒充尚未实施的 production/T0 gate。

### 7.9 S13 production implementation writeback（2026-08-20）

S13 已在 commit `de7e935` 中完成 production 变更：`_initial_children()` 删除未使用的
`availability` 参数，并同步删除两个调用点的对应实参。函数体、child sorting、错误边界和调用顺序均未改变。

本次 S13 production change unit 的 actual changed-file manifest 只有：

```text
mote-kernel/src/mote_kernel/execution/engine/recovery.py
```

该变更没有触及 State、State tests、public API、normative behavior source 或持久化路径，也没有新增 helper、DTO、
缓存或兼容层。已有 recovery characterization 通过：

```text
tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children
tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding
5 passed（含参数化展开）
```

S13 变更前后 `make check` 均通过（817 tests、coverage 100%、Ruff、Pyright、build、Twine），变更后的
monorepo `pre-commit run --all-files` 也通过。

本次实施明确不新增 S13 的 exact-shape architecture test，因此 T0 保持 `DEFERRED`，不能将 S13 记为完整
`PASS` 或据此自动放行 S14。本文本节是独立 owner writeback，不计入上述 S13 production manifest。

### 7.10 S23A production implementation writeback（2026-08-20）

S23A 已在 commit `cc215b4` 中完成 production 变更：`family_driver.py` 删除 `_AdvancedFrontier`
sentinel、收窄 `_advance_scope_quantum()` 返回类型为 `GraphBoundary | None`，并让所有非 boundary
路径统一返回 `None`；`drive_root()` 不再识别 marker。commit、state replacement、executable/child
coordination、boundary/error 分类和 S23B 的 result projection 均未改变。

本次 S23A production change unit 的 actual changed-file manifest 只有：

```text
mote-kernel/src/mote_kernel/execution/family_driver.py
```

该变更没有触及 State、State tests、public API、protocol、持久化路径或 S23B，也没有新增 helper、DTO、
缓存、兼容层或第二 runner。现有受影响行为用例通过；变更后的 `make check` 结果为 817 passed、coverage
100%、Ruff、Pyright、build、Twine 全部通过。

本次实施明确不新增 S23A 的 exact-shape architecture test，因此 T0 保持 `DEFERRED`，不能将 S23A 记为完整
`PASS` 或据此自动放行 S23B。本文本节是独立 owner writeback，不计入上述 S23A production manifest。

### 7.11 S18 production implementation writeback（2026-08-20）

S18 已在 commit `7f778a2` 中完成 production 变更，actual changed-file manifest 为：

```text
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/src/mote_kernel/execution/engine/resume_admission.py
```

`plan_resumes()` 现在使用 owner-local `action_counts`，在一次 canonical action enumeration 中记录重复
`(scope, node_id)`；`admit_resume_candidates()` 使用 owner-local `publication_counts`，在一次 canonical
substitution enumeration 中同时收集 duplicate 与 confirmed-publication collision。删除了 `tuple.count()`、
先 `any()` 再重扫和重复 set/count 组合；duplicate 仍优先于 collision，坐标 identity、排序和错误文本保持。

本变更没有修改 State、State tests、public API、protocol、持久化路径、candidate DTO 或跨 owner helper，也
没有新增 architecture test。现有 resume/API 定向用例为 89 passed；变更后的 `make check` 为 817 passed、
coverage 100%、Ruff、Pyright、build、Twine 全部通过。

由于本次明确不新增 S18 exact-shape architecture test，T0 保持 `DEFERRED`，不能将 S18 记为完整 `PASS` 或
据此自动放行后续单元。本文本节是独立 owner writeback，不计入上述 S18 production manifest。

### 7.12 S08 implementation owner writeback（2026-08-21）

S08 已在 commit `28693d9` 中完成 join interpretation owner 收敛。routing 既有 `_declared_joins()` 继续唯一构造
`(sources, target) -> JoinEdge`；snapshot guard 删除自己的 join comprehension 和 `joins_by_source` direct read，
改为消费该 routing owner。严格 Pyright 对跨模块下划线 symbol 的检查通过 routing module-local `__all__`
显式登记满足；该 symbol 没有从 `mote_kernel.execution` 公共导出，不形成第二公共入口。

本次 S08 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/snapshot_guard.py
mote-kernel/tests/architecture/test_graph_execution_ownership.py
```

其中只有前两个 path 是 production；既有 architecture case 只把 `joins_by_source` direct-owner 集合从
routing + snapshot guard 收窄为 routing，没有新增 `_declared_joins` symbol/import/call 或 comprehension 的
legacy AST 断言。已有 join success、malformed progress、duplicate progress 和 snapshot mismatch 用例继续保持
原错误边界。没有新增 helper、cache、compiled field、State/State test、protocol 或持久化路径。

当前工作树中的 complexity framework、Makefile、`pyproject.toml` complexity tables、README 和 monorepo
pre-commit 改动属于独立 change unit，不进入 S08 manifest，也不作为 S08 或“全仓零负债”的证据；其 ratchet
只防止结构指标回升，health target 的现有非零债务必须由该独立单元自行说明和审核。

S08 scoped 验证结果为：7 个 join owner/behavior case 通过，Ruff 与格式检查通过，Pyright 0 errors，build 和
Twine 通过。当前混合工作树的完整 test run 为 823 passed、1 failed、coverage 100%；唯一失败是独立 complexity
ratchet 仍登记 `decision_points=1350`，而 S08 删除 comprehension 后实际为 1348。implementation scoped
pre-commit 的其他 hooks 均通过，也只在同一个独立 complexity hook 失败。该结果证明 S08 没有行为/类型回归，
但不能冒充完整 `make check` 或零负债工作树交付；必须先把 complexity framework 作为独立 change unit 审核、
落地或移出当前工作树，再对 S08 manifest 重跑完整门禁。本 owner writeback 的 scoped pre-commit 已通过。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.13 S09 implementation owner writeback（2026-08-21）

S09 已在当前 implementation change unit 中完成 routing facts → command projection 收敛：删除五字段
`RoutingResolution` DTO 和 `plan_routing()` forwarding wrapper；`project_routing_facts()` 现在直接返回
`ResolutionCommand`，`resolve_routing()` 只组合一次 facts resolution 与该唯一 projection。recovery 仍保留同一份
local `facts` 供 historical-gap 诊断使用，同时直接消费 projection 返回的 command，不再经 result wrapper 或镜像
completion 字段。

本次 S09 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/tests/execution/engine/test_routing.py
mote-kernel/tests/execution/engine/test_output_projection.py
mote-kernel/tests/execution/engine/test_recovery_identity.py
mote-kernel/tests/architecture/test_graph_execution_ownership.py
mote-kernel/docs/skip-failed-output-implementation.zh-CN.md
```

三个既有行为测试文件只迁移 consumer，并把两个 abort case 改为直接断言原有 command 与 reason；没有新增测试。
既有 architecture case 只删除对已移除 `plan_routing` 私有名称的禁止断言，没有增加 DTO、wrapper、return
annotation 或具体表达式形状的 legacy AST 断言。skip-output normative 已同步唯一 facts → command projection 与
runtime 入口。actual source review 确认活跃 production、tests 和该 normative source 中 `RoutingResolution`、
`plan_routing`、四个镜像字段及 wrapper `.command` 消费均为 0。

该变更保持 command variant、revision、abort → advance → deadlock → completion 的判断顺序和全部错误文本；没有
修改 State/State tests、公共 API、protocol、持久化或 commit/install 时序，也没有新增 helper、cache、DTO、alias
或兼容路径。收益是 runtime routing 与 recovery 只剩同一个 command projection owner，诊断 facts 只保留在确实
需要它的 recovery 生命周期内；以后调整 routing decision 不再需要同步 wrapper 字段和第二套 consumer 读取。

scoped 验证结果为：routing、output projection、两个 historical recovery case、runtime boundaries 和既有 owner
case 合计 72 passed；Ruff 与格式检查通过；严格 Pyright 为 0 errors。完整 `make check` 的 lint、format 和 Pyright
通过，随后只在独立 complexity ratchet 截停：配置值 → 当前值为 top-level definitions `511 → 509`、type
definitions `293 → 292`、dataclass types `182 → 181`、dataclass fields `526 → 521`、decision points
`1350 → 1348`，均是尚未锁入该独立单元的改进而非回归。排除该独立 gate 后，完整行为套件为 817 passed、
coverage 100%；build 与 Twine 通过，`git diff --check` 通过。monorepo `pre-commit run --all-files` 的其他 hooks
全部通过，也只在相同 complexity ratchet 失败。该结果证明 S09 无行为、类型、格式、package 或 secret 回归，但
不能冒充零负债整体工作树交付；S09 不修改独立 complexity framework 的 limits、tests 或 hook 配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

## 8. 文档同步和缺口

当前行为与功能语义的 normative source 文件均存在，本轮按第 5.1 节的最小 source precedence 使用。完整
architecture 双语 parity/canonical 治理是独立文档工作，不是全部 execution P1 的前置。新增调用链仍是
非规范性草稿，不能加入以下 normative 集合，也不能替代其中任一 owner：

- [architecture](architecture.zh-CN.md)；
- [Node I/O implementation](graph-node-input-output-contract-implementation.zh-CN.md)；
- [skip-output requirements](skip-failed-output-requirements.zh-CN.md)；
- [skip-output implementation](skip-failed-output-implementation.zh-CN.md)。

Phase 0 和各原子单元中仍必须完成：

1. 维护唯一 requirements 文件 `docs/graph-semantics-preserving-simplification-requirements.zh-CN.md` 及 `README.zh-CN.md`、`README.md` 的稳定链接/owner 导航；requirements 不复制具体 target shape，README 不枚举动态增长的 review 列表；requirements 独立拥有准入状态，本文只提交 evidence；
2. 本文是 target shape、原子迁移账本、实施顺序、复杂度账本和 characterization 计划的唯一 owner，不创建第二份 target-shape proposal；各轮 review/response 只记录裁决、异议和整改，不拥有 requirements、当前行为或目标 shape；
3. S03–S06、S09–S12、S14、S17 的原子变更必须同时修订对应 frozen internal shape 的 normative implementation；S09/S10/S11 必须同步 `skip-failed-output-implementation.zh-CN.md`，S03/S04/S05/S06/S12/S14 必须同步 `graph-node-input-output-contract-implementation.zh-CN.md`；不得先形成 production-only 或 docs-only 的长期中间状态；
4. P2 单元各自补充 target-shape 评审记录；S12 还必须补充 action ↔ availability、malformed seed、valid-domain equality 和 `_RecoveryFamily` 泛型迁移记录。
5. 上述 normative 同步只能描述 execution-owned internal shape 的变化；`state/graph_state/**`、State tests、
   durable/conformance protocol 与持久化能力均保持当前状态，不建立对应实施条目或“顺便同步”的 schema 修改。

本文只引用并同步 normative source，不复制一套长期并行的完整类型规范。

### 8.1 Phase 0 文档 owner 与 exact paths

| 文档/产物 | 唯一 owner | exact path | 当前状态与边界 |
| --- | --- | --- | --- |
| 本轮 requirement ID、行为保持义务、非目标、外部语义停止条件、阶段准入条件 | requirements 文档 | `docs/graph-semantics-preserving-simplification-requirements.zh-CN.md` | Phase 0 最终裁决已完成；A05 只批准当前 15 个 P1；只链接具体 normative truth，不写第二套 target shape |
| target shape、原子迁移账本、实施顺序、复杂度账本、characterization 计划和实施门禁 | 本实施方案 | `docs/graph-semantics-preserving-simplification-implementation.zh-CN.md` | 本文唯一拥有；不复制 requirements 的行为清单 |
| 当前架构行为 | architecture normative source | `docs/architecture.zh-CN.md`、`docs/architecture.md` | 按 5.1 最小 precedence 保持当前行为；全文 parity/canonical 治理独立进行，不生成本轮 State/Store target |
| 当前 Node I/O shape | Node I/O normative source | `docs/graph-node-input-output-contract-implementation.zh-CN.md` | 在对应 production 原子提交前继续描述现行 shape |
| 当前 skip-output shape | skip-output normative source | `docs/skip-failed-output-implementation.zh-CN.md` | 已随 S09 同步唯一 facts → command projection；S10/S11 在各自 production 原子提交前继续描述现行 output/target facts shape |
| 文档导航 | package README | `README.zh-CN.md`、`README.md` | Phase 0 已加入稳定文档链接和 owner 关系；不复制正文或枚举 review 历史 |
| 评审裁决/回复 | review record | 本文第 1 节“关联记录”逐条列出 exact path，包括第五次复审、第五次回复和 requirements 再次复审 | 只记录裁决、接受/不接受理由和验证，不拥有 requirements、当前行为或 target shape |
| 第六至第十次复审 | review record | `docs/graph-semantics-preserving-simplification-implementation-sixth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-seventh-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-eighth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-ninth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-tenth-review.zh-CN.md` | 只记录 R1–R16/C1–C2 审计结论；不拥有 target shape、State 或准入批准 |
| Execution / State / Frontier 调用链 | non-normative draft / 独立整改项 | `docs/execution-state-frontier-call-chain.zh-CN.md` | 只作导航草稿；不是 State/commit/recovery/持久化事实源或 A05 blocker，不进入稳定 README normative 导航 |
| 历史调研 | history record | `example/graph-two-commits-simplification-review.zh-CN.md` | 只供溯源，不是 normative 或实施事实源 |

requirements、稳定 README 导航、本文 target 设计和 per-change manifest 规则均已形成。requirements 第 7 节
已经只对当前 15 个 P1 显式批准 `GSP-A05`；本文只同步该裁决，不扩大批准范围。

## 9. 明确排除的方向

本轮非目标由 requirements 的 `GSP-N01`–`GSP-N06` 唯一拥有，见
[requirements 第 4 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#4-非目标)。
第 4 节另列的是可能具有独立价值、但不属于当前 24 个原子单元的后续重构候选；两者都不得混入本轮实施。
持久化、State/schema/protocol 演进不是“账本外候选”，而是第 2.4 节明确冻结的范围外能力；不能通过新增 S24、
把现有单元降级为 P2 或修改 architecture 文案纳入本轮。

## 10. 停止条件

外部语义停止条件由 requirements 的 `GSP-S01`–`GSP-S08` 唯一拥有，见
[requirements 第 5 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#5-外部语义停止条件)。

本文只补充实施特有的停止条件；出现以下任一情况同样立即停止当前单元并重新评审：

- producer/consumer 不能在一个原子变更中完成迁移；
- 新增字段、变量、helper 或 import 的认知面不小于删除面；
- 只能通过放宽断言、删除 characterization/tamper case 或复制 owner 让测试通过；
- target shape 与当前 normative truth 的同步不能在对应 production 原子变更中完成；
- actual changed-file manifest 无法覆盖本单元全部改动或无法复现门禁结论；
- actual diff 触及 State/State tests/protocol，或需要 Store、journal、checkpoint、database、持久化 port/backend；
- 某个具体 target 必须从冲突文案中选择一种新语义，且第 5.1 节最小 precedence 无法证明当前行为保持；
- baseline/target case 的断言目标、失败条件或状态（`PASS`、`DESIGNED / PENDING IMPLEMENTATION`）无法逐项核对；
- 本单元实际修改的文档把 callback exact confirmation 写成 persistence/durability/crash-recovery 保证，或把
  `Graph.resume_*()` 写成第二 runner。

## 11. 当前结论

本次审查保留 23 个历史 ID，并把 S23 拆成两个原子单元，共得到 24 个实施单元：15 个 P1、9 个 P2（S12
保持 P2）。15 个 P1 的范围、owner、删除对象、最多新增面、before→after 计数和 exact target 均已唯一化，
目标 shape 已按实施方案固定。S08 已完成 production、既有 owner gate 收窄和独立 owner writeback；S09 已完成
production、既有行为 gate、一次性 source review、normative 同步和独立 owner writeback。当前工作树仍混有未独立
审核的 complexity unit，完整门禁未绿，因此两者都不能记为零负债整体交付；S13、S18、S23A 已分别完成
production-only 简化，未新增 exact-shape architecture test，因此三者的 T0 均保持 `DEFERRED`。

第八次复审 R9–R13 已回写：T0 在 Phase 0 只要求设计完成，批准后才与 production 原子落地；准入状态只由
requirements 拥有；S18 固定每 owner 一个 count index，S20 固定 `UseStepRequestInput | None` keyword；S23A
接受现有 end-to-end baseline；manifest 改为 per-change actual files，不再维护 cumulative review cutoff。
architecture 全文治理和调用链整改不再扩大 execution P1 范围。State/no-persistence HARD KEEP 未改变。

第九次复审 R14–R16 也已回写：S03–S06 与 S18 均登记了 exact architecture `path::test_case`；S20 的删除面
精确限定为 failed-retry materialization-only replacement projection，最终 simulated frontier/validation 明确
保持 `1 → 1`；S23B interrupt baseline 改为实际经过 family-driver Result projection 的 public case。由此 15 个
P1 均具备可复现的 baseline behavior nodeid 和 T0 exact nodeid，且 source gate 不会误删 admission 安全边界。

requirements 第 7 节现已接受 per-change manifest、闭合 `GSP-A03/GSP-A04`，并明确只对当前矩阵中的 15 个
P1 批准 `GSP-A05`。第十次复审 C2 经其登记 SHA 对象复核不成立：第 7.4 节只有 5 个唯一 path，
`README.zh-CN.md` 仅出现一次，历史 manifest 无需删除。

截至上述终局文档裁决时，production/tests 均未修改，15 个 T0 均为
`DESIGNED / PENDING IMPLEMENTATION`。随后 S13、S18、S23A 已完成 production-only 变更，但因本次明确不新增
exact-shape architecture test，三者 T0 均为 `DEFERRED`，不计为完整 `PASS`。S08 随后完成唯一 join owner
收敛，只更新既有 architecture owner 集合，不新增 legacy AST 断言；S09 也已删除 routing result DTO/wrapper，
迁移既有行为测试并以 source review 闭合，不新增 legacy AST 断言。两者的 scoped gate 已通过，但混合工作树的
独立 complexity hook 阻断完整交付。其余 10 个 P1 的 production 仍未开始。后续单元仍必须按各自批准口径落地
production、gate 和实际受影响的 normative source。9 个 P2 继续逐项受 `GSP-A06` 约束，State/no-persistence
HARD KEEP 保持不变。

### 11.1 Requirements owner 已接受的实施证据

| 条件 | 本文提交的可核对 evidence | 位置 |
| --- | --- | --- |
| `GSP-A01` | requirements/implementation/review 分工明确；本轮最小 source precedence、State HARD KEEP 与 non-normative 调用链边界固定 | 1.2、2.4、5.1–5.3、8.1 |
| `GSP-A02` | 24 个 execution-only 原子单元；15 个 P1 exact target 无条件式分支，S18/S20 已收口 | 3、3.6、6.1 |
| `GSP-A03` | 15 个 P1 均映射行为 requirement 和现有成功/失败或边界 case；15 个 T0 均有 exact `path::test_case`、断言和失败条件；S08 只收窄既有 direct-owner 集合、不新增 legacy AST 断言，S20 final simulation 与 S23A indirect baseline 口径明确 | 7.2.1–7.2.3 |
| `GSP-A04` | actual change unit manifest、owner/review 分离规则、State/no-persistence negative gate 与可复现命令固定 | 7.3–7.8 |
| `GSP-A05` | Phase 0 设计 → 显式批准 → production + target test 原子落地 → T0 PASS 后交付的时序无循环 | 6、7.2.2 |
| `GSP-A06` | 9 个 P2 保持未继承批准，按单项设计和 evidence 另行准入 | 2.2、4、6 |

Phase 0 到此终局闭合，不需要再创建评审轮次证明本轮裁决存在。S08 与 S09 的 production/scoped gate 已完成，
但在独立 complexity unit 与两个 implementation manifest 分离并重跑完整门禁前，不把它们记为零负债整体交付，
也不开始 S10；不重新发现第 25 个简化点，不提前实施 P2，不把独立文档治理放回关键路径，也不触及 State 或
持久化。
