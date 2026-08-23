# Graph 执行代码语义保持型简化实施方案

## 1. 文档信息

- 状态：Approved for 15 P1 / requirements 已明确批准 `GSP-A05`；S07、S01 已分别单项完成；S01 implementation commit 为 `0f34aa2`
- 日期：2026-08-23（S01 complexity gate 明确排除；target 已收口为四文件 production/behavior 原子实施）
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
- [S01 单项实施方案审查](graph-semantics-preserving-simplification-s01-implementation-review.zh-CN.md)
- [S01 单项重新设计第二次评审](graph-semantics-preserving-simplification-s01-implementation-second-review.zh-CN.md)
- [S01 第二次评审回复](graph-semantics-preserving-simplification-s01-implementation-second-review-response.zh-CN.md)
- [S01 单项重新设计第三次评审](graph-semantics-preserving-simplification-s01-implementation-third-review.zh-CN.md)
- [S01 第三次评审回复](graph-semantics-preserving-simplification-s01-implementation-third-review-response.zh-CN.md)
- [S01 单项重新设计第四次评审](graph-semantics-preserving-simplification-s01-implementation-fourth-review.zh-CN.md)
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

当前没有发现新的第 24 个历史审查来源；但原 S23 混合了两个可独立实施和验证的问题，现拆成 S23A、S23B。
因此本文保留 S01–S23 共 23 个历史审查 ID，实际跟踪 24 个 target ledger ID。S05、S06 的目标与 evidence
仍分别编号，但按 3.1 节的明确授权只形成一个联合 delivery change unit；当前交付边界共 23 个 change unit
（14 个 P1、9 个 P2）。第六至第十次复审以及调用链草稿是本轮的文档审计输入，不是新的 target 或交付单元。

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
  Phase 0 最终准入评审后，按第 6 节登记的 delivery change unit 原子实施。15 个 P1 target ledger ID
  对应 14 个 delivery change unit，其中只有 S05+S06 是明确批准的联合边界。
- P2：方向可能成立，但必须先给出窄 nominal target、净复杂度下降证据和行为 characterization，并单项再评审；不得与 P1 混合实施。

P1/P2 都不表示可以跳过规范同步、完整门禁或代码评审。requirements 第 7 节通过 `GSP-A05` 只授权当前矩阵中的
15 个 P1；随后 `GSP-A06` 已分别对 S07、S01 单项闭合，其余 7 个 P2 和账本外方向仍未获批。

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

当前 23 个历史审查 ID 拆解为 24 个 target ledger ID，并映射到 23 个 delivery change unit：

- P1：15 个 target ledger ID；S05+S06 联合交付后为 14 个 delivery change unit；
- P2：9 个 target ledger ID，也是 9 个独立 delivery change unit；
- `4c17a8f` 来源：18 个历史 ID，因 S23 拆分对应 19 个 target ledger ID；
- `7944159` 来源：S10、S11、S17、S18、S22，共 5 个 target ledger ID；
- `8980e6f`：0 个新增单元。

每项都明确“删除什么”和“最多新增什么”。若实际实现需要超出该新增上限，或净认知面不下降，立即停止并重新评审。

### 3.1 Compiler、validation 与 compiled topology（S01–S06）

| ID | 目标与唯一 owner | 删除 | 最多新增/替代 | 位置 | 级别 |
| --- | --- | --- | --- | --- | --- |
| S01 | `_compile_graph()` 保留 nested recursion、完整 phase/error order 和最终装配；typed `dict` 是 invocation-local lookup，`ActivationGate`/`direct_targets`/`FrontierTransitionPlan` 分别拥有各自唯一事实 | `control_gates`、`direct_pairs`、单次使用的 `RouteCause` alias、三个无语义转交 alias 和两份 source-only predicate | 一个被两个 production consumer 复用的窄 predicate；零 phase helper/DTO/cache/额外 scan，顶层定义不增长 | 见 3.1.2 完整单项设计 | P2 |
| S02 | validation 仍集中拥有错误优先级；只有 direct/conditional/join 或 definition 子校验能形成独立 invariant 且净分支/变量下降时才提取 | 经证明可独立的重复校验分支 | 每个 nominal variant 至多一个 validator；不得改变遍历顺序 | `graph/validation.py` | P2 |
| S03 | nested 分类直接取 `nested_graphs` key；callable 分类按 `nodes` 中 `CallableNodeDefinition` nominal variant 判断 | `FrontierTransitionPlan.callable_node_ids`、`nested_node_ids` 及 compiler producer | 无 | 见 3.1.1 完整迁移清单 | P1 |
| S04 | `FrontierTransitionPlan.publications` 精确收窄为 `FrozenMap[GraphNodeId, FrameDescriptor[GraphValueT]]`；本单元同时删除 `CompiledGraph.publications` projection，全部 publication consumer 一次迁移到 `graph.transition.publications[node_id]`；outputs 取 `descriptor.declarations`，routes 取 `conditional_targets[node_id]` | `FrontierTransitionPlan.outcomes`、`CompiledGraph.outcomes`、`CompiledGraph.publications`、`OutcomeAdmissionPlan`、`PublicationPlan`、keyed plan 中重复 `node_id`，包括 `MaterializationPlan.node_id` | 不新增 DTO；只收窄 map value type | 见 3.1.1 完整迁移清单 | P1 |
| S05 | graph input declaration 只由 `graph_input_descriptor.declarations` 拥有，nested compiler 和 runtime admission 直接消费该字段 | `CompiledGraph.graph_inputs` | 无 property/alias | 见 3.1.1 完整迁移清单 | P1 |
| S06 | `CompiledGraph` 直接拥有唯一 `transition` lowering field；删除 S04 后剩余的 convenience projections，consumer 统一读取 `graph.transition.*`；publication 只做 zero-use/exact-shape 验证 | `RecoveryAvailabilityPlan`、`CompiledGraph.transition` property、`entries`、`materializations`、`graph_outputs`、`resource_order` 四个 forwarding properties（`outcomes`、`publications` 已由 S04 删除） | 一个直接 `transition: FrontierTransitionPlan[GraphValueT]` field；无 alias/property | 见 3.1.1 S06 完整迁移清单 | P1 |

S03、S04 仍逐项原子实施。2026-08-22 用户明确要求 S05、S06 一起处理，因此两项正式组成一个
`S05+S06` 联合原子 change unit：两个审查 ID、目标 shape、行为证据和净删除子账本仍分别可核对，但 production、
tests、normative 同步、T0 gate、actual manifest、提交与回滚边界只有一份。该联合授权只合并两个已获
`GSP-A05` 批准且共享 `CompiledGraph`/compiler/owner gate 的实施边界，不扩大任一目标或批准范围。不得先引入
新 field 双写，也不得把联合 diff 拆写成两个独立 actual manifest。

#### 3.1.1 S03–S06 完整原子迁移清单

以下清单是实施边界，不以“迁移全部 consumer”代替可核对位置。S03、S04 各自在自己的原子变更中完成；
S05、S06 的两组清单必须由同一个联合原子 change unit 一次完成 production producer、consumer、imports、
normative shape 和 direct exact-shape tests：

- S03 definition/producer：`graph/topology.py` 删除两个 field，`graph/compiler.py` 删除两个 classification tuple 的构造和 `FrontierTransitionPlan` 实参；runtime consumers：`engine/frontier.py`、`engine/recovery.py`、`engine/admission.py`；direct tests/import gate：`tests/execution/graph/test_topology.py`、`tests/execution/graph/test_compiler.py`、`tests/architecture/test_graph_execution_ownership.py`。同时复跑 frontier、resource admission、nested recovery characterization。
- S04 definition/producer：`graph/ports.py` 删除两个 DTO 和 `MaterializationPlan.node_id`；`graph/topology.py` 删除 `outcomes` field、`CompiledGraph.outcomes`/`CompiledGraph.publications` projections 并收窄 `publications` value type；`graph/compiler.py` 只生成 descriptor map。runtime consumers：`engine/scheduler.py`、`engine/frontier.py`、`engine/recovery.py`、`executor.py`、`engine/routing.py`、`engine/resume_input.py`、`engine/resume_admission.py`、`engine/admission.py`、`invocation.py`、`family_driver.py`；direct tests/import gate：既有 `tests/architecture/test_graph_execution_ownership.py`、`tests/execution/graph/test_compiler_contract.py`、`tests/execution/engine/test_routing.py`、`tests/execution/engine/test_output_projection.py`、`tests/execution/engine/test_resume_input_contract.py`、`tests/execution/engine/test_resume_admission.py`、`tests/execution/engine/test_recovery_identity.py`、`tests/execution/engine/test_runtime_boundaries.py`、`tests/execution/test_executor.py`、`tests/execution/test_continuation_integrity.py`、`tests/execution/test_graph_api.py`。所有 removed DTO imports、旧 projection 读取和 `.descriptor` 二次访问由本次 actual diff/source review 归零；publication consumers 直接形成 `graph.transition.publications[node_id]`，不得留给 S06 再次触碰，也不新增 legacy AST 子断言。
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

<a id="312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23"></a>
<a id="s01-gsp-a06"></a>

#### 3.1.2 S01 `GSP-A06` 单项重新设计（APPROVED / IMPLEMENTED，2026-08-23）

本节是 S01 target shape、结构净删除账本、characterization、manifest 和实施门禁的**唯一 owner**。
原独立 S01 proposal 已撤销为不含 target 内容的迁移指针；S01 review 只保留裁决和整改证据。requirements
继续唯一拥有批准状态，并已在 approval commit `785c796` 将 S01 记为 `GSP-A06 SATISFIED`。S01 已按本节
四文件 manifest 完成 production/tests；不引入独立 complexity unit，也不需要扩大批准范围。

**直接实施授权**

- final design/review commit：`d34c117`；
- requirements-only approval commit：`785c796`；
- 当前实施状态：`IMPLEMENTED / VERIFIED`（implementation commit `0f34aa2`）；
- 唯一实施范围：本节第 4 项登记的 compiler + 三个既有 behavior test 文件；
- 明确排除：持久化/State/protocol、`pyproject.toml`、complexity framework、legacy/AST/private-source-shape gate 和新测试文件。

本 target 只删除 compiler invocation 内的重复事实和无语义转交。当前不实现持久化；第 2.4 节的 State、command、
reducer、protocol、Store/no-Store 与 callback 边界全部 `HARD KEEP`。S01 也不改变 public `Graph` API、
`CompiledGraph`/`FrontierTransitionPlan` 字段 shape、错误分类或 runtime/recovery 的消费路径。

**Requirement 适用性**

| Requirement | 裁决 | S01 触及面 |
| --- | --- | --- |
| `GSP-P01` | 适用 | compiler validation error 分类、文本和首错时点仍从 public `Graph`/`compile_graph()` surface 可观察 |
| `GSP-P02` | 排除 | 不读取或修改 State shape、command、revision、durable control facts；State tests 只原样复跑 |
| `GSP-P03` | 排除 | 不触及 admission、commit、confirmation、memory installation 或 partial-confirmation transaction |
| `GSP-P04` | 适用 | resolved binding 的 materialization/publication descriptor assembly 被就地简化 |
| `GSP-P05` | 适用 | direct/conditional/join/data eligibility、guarantee 与 publication selection proof 继续消费同一 gate facts |
| `GSP-P06` | 适用 | runtime 与 recovery 必须继续消费同一个 `FrontierTransitionPlan`，不得形成第二 lowering |
| `GSP-P07` | 适用 | nested definition-order recursion、resource first-seen order、canonical node/resource ordering 保持 |
| `GSP-P08` | 适用 | `_compile_graph()`/execution owner、nominal generic、module-scope import 和无第二 execution/store owner 是硬边界 |

**Exact target signature 与新增上限**

`_compile_graph()` 的签名和递归入口保持不变：

```python
def _compile_graph(
    definition: GraphDefinition[GraphValueT],
    scope: DefinitionScope,
) -> CompiledGraph[GraphValueT]: ...
```

S01 不新增 phase helper。唯一允许新增的 private function 是下列被 controlled-producer proof 与 input-publication
selection 两个 production consumer 共同复用的 predicate：

```python
ActivationGate: TypeAlias = tuple[tuple[GraphNodeId, GraphRouteId | None], ...]


def _all_single_source_gates(
    source: GraphNodeId,
    gates: list[ActivationGate],
) -> bool:
    return bool(gates) and all(
        len(gate) == 1 and gate[0][0] == source
        for gate in gates
    )
```

`ActivationGate` 保留为 route-aware typed alias；仅删除只被它引用一次的 `RouteCause` alias，并把 exact pair
shape 内联到 `ActivationGate`。这使新增的共享语义函数与被删除的单次 alias 在 top-level-definition 账本中一进一出，
不靠删除一个有独立行为的 leaf helper 为新 helper 腾指标。predicate 依赖 validation 已保证的非空、无重复 source
gate shape；它不排序、不分配 source map、不缓存，也不改变 route-aware joint-activation proof。

两个既有 helper 只做窄类型迁移，不增加 owner：

```python
def _guaranteed_sets(
    node_ids: tuple[GraphNodeId, ...],
    entries: tuple[GraphNodeId, ...],
    activation_gates: dict[GraphNodeId, list[ActivationGate]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
) -> dict[GraphNodeId, frozenset[GraphNodeId]]: ...


def _input_publication_selection(
    source: NodeOutputPort,
    target: GraphNodeId,
    absolute_levels: dict[GraphNodeId, int],
    activation_gates: dict[GraphNodeId, list[ActivationGate]],
    data_dependencies: dict[GraphNodeId, set[GraphNodeId]],
) -> PublicationSelection: ...
```

`_guaranteed_sets()` 只以 `for source, _route in gate` 消费 source；`_input_publication_selection()` 调用唯一
predicate。`_resolve_source()` 继续接收
`dict[GraphNodeId, OutputDeclarations[GraphValueT]]`；不为提前 `FrozenMap` 化改成掩盖线性 lookup 的抽象。
`node_outputs` 在 `_compile_graph()` 内显式标注为同一完整 generic `dict` 类型。

新增上限固定为：一个 private predicate、零 DTO/dataclass/field/property/alias/cache/index/runner/store、零
phase wrapper、零 module import、零额外 full scan/sort/freeze。若 exact candidate 需要第二个新函数或任何宽
context/multi-map return，即不满足本 target，不得以“小改”继续。

**唯一编排与错误顺序**

`_compile_graph()` 直接保留下列顺序，并用 phase comment 表达边界：

1. 按 `definition.nodes` 的原始顺序递归编译 nested graph；parent/child phase、sibling 次序和首个 child error
   只由 `_compile_graph()` 拥有；
2. 建立 typed `nodes`/`nested_graphs`/`node_outputs` lookup，收集 graph input declaration；
3. 按 canonical `node_ids` 完成一次 binding scan、nested boundary validation 和 data-cycle check；
4. 按 `definition.edges` 原始顺序完成一次 direct/conditional/join/activation lowering；
5. 依次完成 duplicate、entry、successor/reachability、joint activation、guarantee、terminal/output proof；
6. 复用既有 `_frame_descriptor()` 组装 materialization/publication，构造唯一 `FrontierTransitionPlan`；
7. 直接按 `transition.resource_order` canonicalize callable resources，在最终 `CompiledGraph` 边界冻结 maps。

不得把 nested loop、binding scan、edge lowering、proof 或 final assembly 移入 single-use wrapper、闭包、DTO 或
多 map tuple。不得新增 `definition.edges`、`node.inputs.entries`、`definition.outputs.entries` 或 node-id full scan；
现有各 scan 的顺序和首错时点保持，不把“仍有多个不同职责的 `node_ids` 遍历”误报为重复扫描。

**唯一事实与删除面**

| 事实 | 唯一 owner | 删除/禁止 |
| --- | --- | --- |
| route-aware non-terminal activation | `activation_gates: dict[GraphNodeId, list[ActivationGate]]` | 删除 route 丢失后的常驻 `control_gates` 投影 |
| non-END direct target membership | `direct_targets: dict[GraphNodeId, set[GraphNodeId]]` | 删除 `direct_pairs`；duplicate data/direct 用 `target in direct_targets[source]` |
| terminal control gate | `gates_to_end` 派生的 `terminal_gates` | 不把不含 END 的 `direct_targets` 错称为 terminal owner |
| controlled source-only causal relation | `_all_single_source_gates()` 按需从 `ActivationGate` 派生 | 不保存 source-only map/set/bool，不复制 predicate |
| compile-time lookup | invocation-local typed `dict` | 不提前生成 `FrozenMap`，不保留 dict/frozen 双工作索引 |
| compiled execution lowering | `FrontierTransitionPlan` | 不新增 recovery/runtime projection、runner 或 cache |
| descriptor construction | resolved declarations + 既有 `_frame_descriptor()` | 删除 `input_descriptor`、`output_descriptor` 转交 alias |
| canonical resource order | `transition.resource_order` | 删除 `resource_order` 转交 alias；`positions` 直接 enumerate canonical tuple |

`nested_graphs`、`node_outputs` 和 proof indexes 在 compiler invocation 内保持 typed `dict` 的近似 O(1) lookup；
`frozen_map()` 只在 `FrontierTransitionPlan`/`CompiledGraph` 最终 representation 边界调用。S01 不减少必要的最终
freeze，也不增加额外 sort/freeze；不得把 tuple-backed `FrozenMap` 用作 binding/output proof 的高频工作索引。

source-only predicate 的完整语义固定如下：

| Gate shape | 结果/consumer 行为 |
| --- | --- |
| empty gates | `False`；data-only producer 继续走唯一 `data_dependencies` 分支 |
| 同一 source 的两个 conditional routes 指向同一 target | `True`；route identity 不参与 producer guarantee/relative selection |
| 同一 source 的 direct 与 conditional gate 指向同一 target | `True`；两个 gate 都只有同一个 causal source |
| 任一 join gate 含第二 source | `False`；不得误判为 single-source causal |
| route mutually exclusive / partial join | `_validate_joint_activation_paths()` 继续读取完整 `(source, route)` 并 fail closed |
| terminal join/output | 继续由 `terminal_gates`、`_terminal_guarantees()`、`_output_publication_selection()` 拥有 |

**结构净删除与零新增负债账本**

S01 的净复杂度证据只由 exact target 的结构删除/新增面、一次性 source review 和 behavior evidence 构成，不依赖仓库
complexity gate、snapshot、baseline、ratchet、health identity 或 `pyproject.toml` limit。上述独立治理项按用户明确要求
不适用于 S01，不得成为 review、批准、实施或交付前置。

| 可核对结构 | 当前 | target | 净变化/约束 |
| --- | ---: | ---: | --- |
| duplicate mutable facts/index | 2（`control_gates`、`direct_pairs`） | 0 | `-2`；不得换名或转移到第二 projection |
| source-only predicate copies | 2 | 1 | `-1`；只保留一个双 consumer predicate |
| 无语义转交 alias | 3（`input_descriptor`、`output_descriptor`、`resource_order`） | 0 | `-3`；直接消费 canonical value |
| top-level definitions | `RouteCause` alias | `_all_single_source_gates()` | 一删一增；新增函数有两个 production consumer |
| phase helper/DTO/dataclass/field/property | 0 | 0 | 不新增 |
| cache/context/runner/store/compatibility path | 0 | 0 | 不新增 |
| 额外 full scan/sort/freeze | 0 | 0 | 不新增；最终 representation 所需 freeze 原样保留 |
| public/compiled/State/protocol shape | 当前 shape | 当前 shape | 不改变 |

“零负债”在 S01 中精确表示：重复事实和无语义转交净删除，新增 predicate 具有两个真实 production consumer，并且不新增、
不转移、不掩盖 DTO、wrapper、cache、第二 index、低效 lookup、private test hook、compatibility 或持久化债务。implementation
writeback 必须按 actual diff 逐行闭合上表；任何一项需要扩大新增面或不能达到 target 即停止并重新评审。

**Case-level behavior evidence**

以下均通过 public `compile_graph()` 或既有 owner/runtime surface 断言行为；原设计登记的两个新 case 和一个既有 case
补强已与 production 在同一 implementation unit 落地。不得新增测试 private helper
名称、局部变量、loop/comprehension、扫描次数或源码文本。

| Requirement/场景 | Exact `path::test_case` | 断言目标 | 失败条件 |
| --- | --- | --- | --- |
| P01 unknown source | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_value_source_from_an_unknown_node` | 保持 `UnknownNodeError` 与 source error surface | 错误分类、文本归属或首错阶段改变 |
| P01 direct/data duplicate | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_duplicate_data_and_direct_control_pair` | 删除 `direct_pairs` 后仍抛 `DuplicateEdgeError` | membership 漏检、改报其他错误或错误延后 |
| P01 nested error priority | `tests/execution/graph/test_nested_graph.py::test_nested_compilation_preserves_definition_order_error_priority` | 两个 child 同时含不同 compile-time invalidity 时仍报告 definition-order 第一个 child | 第二个 child/parent error 抢先，或 sibling traversal 被排序/搬迁 |
| P04 data-only relative selection | `tests/execution/graph/test_compiler_contract.py::test_compiler_uses_relative_selection_for_loop_producer_data_trigger` | 无 control gate、唯一 data producer 仍为 `RELATIVE/1` | empty gate 被误判为 causal control 或 selection 改变 |
| P04/P05 same-source multi-route | `tests/execution/graph/test_compiler_contract.py::test_compiler_uses_relative_selection_for_same_source_conditional_routes` | loop source 的两个 routes 指向同一 bound target，编译成功且 materialization 为 `RELATIVE/1` | route identity 误入 source-only proof、编译失败或 superstep 漂移 |
| P05 direct + conditional 同 target | `tests/execution/graph/test_compiler.py::test_compile_indexes_conditional_routes_and_joins` | 在既有 conditional/join 断言之外，精确断言 `direct_targets["a"] == ("b", "c")`；该 case 只证明 edge lowering/index 共存，不冒充 source-only publication predicate evidence | direct target 遗漏/排序漂移，或 conditional/join index 改变 |
| P05 multi-source join 不可冒充 single source | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_ambiguous_loop_publication_for_join_consumer` | join consumer 的 publication selection 继续 fail closed | 多 source gate 被误判为 relative causal |
| P05 mutually exclusive route | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_join_between_mutually_exclusive_routes` | route-aware joint proof 保持 `jointly satisfiable` error | source-only projection 污染 route proof 或错误消失 |
| P05 partial join | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_a_join_that_can_receive_only_one_source_on_a_route` | partial source set 继续 fail closed | route requirement 被忽略 |
| P04/P05 terminal output | `tests/execution/graph/test_compiler_contract.py::test_join_to_end_is_one_terminal_gate_for_output_guarantees` | join-to-END 仍形成一个 terminal gate 并允许 guaranteed output | `direct_targets` 被误作 END owner 或 terminal guarantee 漂移 |
| P04/P05 terminal negative | `tests/execution/graph/test_compiler_contract.py::test_compiler_rejects_output_not_guaranteed_on_every_terminal_branch` | 每条 terminal branch 的 output guarantee 保持 | output proof 被弱化或错误顺序改变 |
| P06 NodeOutput availability consumer | `tests/execution/engine/test_resume_input_contract.py::test_node_input_availability_reports_missing_publication` | availability 从 compiled NodeOutput binding/selection 形成 publication lookup；缺失时返回 unavailable | NodeOutput binding 不再要求 publication，或错误地报告 available |
| P06 NodeOutput materialization fail closed | `tests/execution/engine/test_resume_input_contract.py::test_materialization_rejects_compiled_node_output_without_selection`；`tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication` | selection 缺失时抛 `SnapshotMismatchError`；selection 存在但 publication 未确认时抛 `GraphValueUnavailableError` | selection 被绕过、错误分类改变或缺失 publication 被静默接受 |
| P06 relative coordinate 正向消费 | `tests/execution/engine/test_runtime_boundaries.py::test_repeated_child_activations_isolate_parent_boundary_substitutions` | 两次不同 superstep 的 publication 均由同一 compiled `RELATIVE/1` materialization selection 解析并得到各自值 | runtime 固定 absolute coordinate、跨 activation 串值或建立第二 selection truth |
| P06 compiled lowering exact shape | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` | `FrontierTransitionPlan` fields 与 `CompiledGraph.transition` direct-field shape 原样保持 | field/projection shape 漂移或 `transition` 不再是 direct field；该 case 不声称证明 consumer owner set |
| P07 nested recursion | `tests/execution/graph/test_nested_graph.py::test_invalid_deeply_nested_graph_fails_root_compilation` | deep child error 继续冒泡到 root | recursion owner、scope 或失败时点漂移 |
| P07 resource order | `tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order` | first-seen `resource_order` 与 canonical node resources 保持 | alias 删除改变排序、identity 或 operation |
| P08 generic/owner/import | `tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types`；`tests/architecture/test_source_discipline.py::test_imports_form_a_contiguous_module_header`；`tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners` | 完整 generic、module-scope import、Graph/execution/State owner 原样保持 | bare container、`Any`/`object`/cast、局部 import、第二 execution/store owner |

**第三次评审接受项回写（2026-08-23）**

第三次评审对当前 target 的非 complexity 设计与 evidence 引用完成复核。成立部分由本节吸收为 owner 状态，不由 review
record 反向拥有 target 或批准状态：

| 项目 | 当前 owner 状态 | 精确边界 |
| --- | --- | --- |
| R1 direct/conditional index evidence 与 manifest | **IMPLEMENTED / PASS** | direct-index exact assertion 已在 `test_compiler.py` 落地；actual manifest 与 implementation commit 见 7.24 |
| R2 NodeOutput publication consumer evidence | **CLOSED / IMPLEMENTED** | 三个 negative consumer、repeated-activation positive consumer 与 same-source compiler case 均已落地并通过 |
| R3 compiled-lowering architecture evidence | **CLOSED** | 只证明 exact field shape；consumer 唯一性继续由 behavior + actual source review 闭合 |
| no-persistence、唯一 owner 与基础设计复用 | **HARD KEEP** | 不修改 State/Store/protocol/callback，不新增 DTO/cache/runner/compatibility path |
| 既有 behavior/owner baseline | **PASS — 19 existing nodeids** | 本次按上表 exact nodeid 复跑为 `19 passed in 0.43s`；只证明当前 production baseline |
| 两个 target case 与一个既有 case 补强 | **PASS / IMPLEMENTED** | same-source multi-route、nested definition-order priority 与 direct-index assertion 均已落地并通过 |
| `GSP-A06` 批准 | **SATISFIED / IMPLEMENTED** | requirements approval commit `785c796` 后由 implementation commit `0f34aa2` 完成 production + behavior unit |

核心 positive case 必须包含：source 自环使 absolute coordinate 不成立；同一 source 的两个不同
`ConditionalEdge` route 指向同一 target；target input 绑定该 source output；最终断言
`PublicationSelectionKind.RELATIVE` 且 `superstep == 1`。它不测试 `_all_single_source_gates()` 名称。

既有 direct + conditional index case 在 implementation unit 中只增加一个 public compiled-index 断言：
`direct_targets[GraphNodeId("a")] == (GraphNodeId("b"), GraphNodeId("c"))`。它与 same-source multi-route
case 职责分离：前者证明 edge lowering/index，后者才证明 route-insensitive source-only publication selection。

error-priority case 必须构造两个 validation 可通过、但 compile phase 分别失败的 sibling child，并用不同错误分类/
文本证明原 `definition.nodes` 顺序决定首错；不得通过 mock private helper、AST 或调用计数实现。

**第四次评审接受项回写（2026-08-23）**

[第四次评审](graph-semantics-preserving-simplification-s01-implementation-fourth-review.zh-CN.md)对第三次评审回复的
R4–R5 纠偏由本唯一 owner 完整吸收：

- complexity gate、snapshot、baseline、ratchet、health identity、`pyproject.toml` limit、Makefile
  `complexity-ratchet` 和 pre-commit `kernel-complexity` 对 S01 均为 `NOT APPLICABLE`；
- 这些内容不形成 S01 的通过、失败、整改项、批准前置、implementation manifest 或交付门禁；
- S01 的净复杂度证据由本节 exact 结构账本、behavior matrix 与 actual diff/source review 闭合；
- 非 complexity production + behavior manifest 精确为四个文件，第三次评审从未主张用该子集替换另一个有效 manifest；
- R1–R3 和 target 技术质量保持 `PASS`；requirements 随后已在独立 approval commit `785c796` 明确授权实施。

该回写只纠正准入范围，不改变第四次评审已经通过的 production target shape、behavior evidence 或 no-persistence 边界，
也不创建第二份 target。

**Exact-shape/source review 与 lookup evidence**

public/owner-internal topology shape不变，因此既有
`tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering`
只证明 exact field shape并原样复跑，不增加 S01-specific architecture assertion。runtime 对 canonical materialization/
publication selection 的消费由上表 P06 behavior cases 证明；没有新增 consumer/projection owner 则由 actual diff 与
一次性 source review 证明，不扩大 AST gate。private/local 删除只在 owner writeback 记录 actual diff 和下列一次性
命令；这些命令不是 repository test：

```bash
rg -n '\b(control_gates|direct_pairs|RouteCause)\b' \
  src/mote_kernel/execution/graph/compiler.py
# 期望：exit 1 且无输出

rg -n '^\s+(input_descriptor|output_descriptor|resource_order)\s*=' \
  src/mote_kernel/execution/graph/compiler.py
# 期望：exit 1 且无输出；transition.resource_order 的 canonical field 读取必须仍存在

rg -n 'transition\.(materializations|publications)' \
  src/mote_kernel/execution
# 记录 actual readers，并通过本单元 diff 确认没有新增 projection/consumer owner；不把列表写进永久测试
```

writeback 还必须列出 `activation_gates`、`direct_targets`、`nested_graphs`、`node_outputs` 的 actual typed owner，核对
`node_outputs`/`nested_graphs` 后续 lookup 发生在 `frozen_map(...)` 之前，并报告 `frozen_map`/sort/full-scan source diff。
发现提前 `FrozenMap`、dict/frozen 双工作索引、额外 sort/freeze/full scan 或 lookup 从 typed `dict` 退化，即使测试
绿色也失败。不得把这些一次性检查转写为永久 AST/private-source test。

**原子 change units 与 exact planned manifest**

1. 本次 design-owner migration unit 只修改：

   ```text
   mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
   mote-kernel/docs/graph-semantics-preserving-simplification-s01-implementation.zh-CN.md
   ```

   第二个文件只撤销原 proposal 并指向本节，不复制 target、账本、evidence 或批准状态。review record 不在本单元
   修改，也不成为 target owner。

2. 第四次评审与本次 owner 回写只负责闭合设计和准入范围；它们不改变 requirements 批准状态。S01 不等待、不修改、
   也不消费独立 complexity framework，后者不得进入 S01 的提交历史或验收结论。

3. 用户以原文“你做到让我可以交付一个直接实施的文档”明确授权后，approval commit `785c796` 只修改：

   ```text
   mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
   ```

   该单元已把 S01 的 `GSP-A06` 状态改为 satisfied；design/review commit `d34c117`、approval commit `785c796`
   在前，production implementation commit `0f34aa2` 在后，Git 历史已形成可核对的 design → approval → implementation 顺序。

4. approval 之后，production + behavior implementation unit 的 planned manifest 精确为：

   ```text
   mote-kernel/src/mote_kernel/execution/graph/compiler.py
   mote-kernel/tests/execution/graph/test_compiler.py
   mote-kernel/tests/execution/graph/test_compiler_contract.py
   mote-kernel/tests/execution/graph/test_nested_graph.py
   ```

   S01 不修改 `pyproject.toml`、complexity rules/tests、Makefile 或 pre-commit hook，也不改变 frozen normative shape，
   因此不修改 Node I/O、architecture、State/protocol 文档；若 actual behavior/shape 证明必须同步其他 normative owner，
   先停止并重新评审，不静默扩大 manifest。

5. implementation 通过后，owner writeback unit 只修改本文，记录 actual structural ledger、lookup/scan/freeze、exact
   manifest 和全部适用 gate；后续 review audit 若发生，只修改自己的 review record，不反向拥有 target/approval。

四类 unit 不得累计历史文件、混合提交或依赖 compatibility bridge。implementation 不得包含
`src/mote_kernel/state/**`、`tests/state/**`、protocol/conformance、Store/repository/journal/checkpoint/database、
README、private/AST architecture gate 或新测试文件。

**实施门禁与停止条件**

批准后的 scoped checks 至少为：

```bash
python -B -m pytest -q \
  tests/execution/graph/test_compiler.py \
  tests/execution/graph/test_compiler_contract.py \
  tests/execution/graph/test_join.py \
  tests/execution/graph/test_nested_graph.py \
  tests/execution/graph/test_topology.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/engine/test_runtime_boundaries.py::test_repeated_child_activations_isolate_parent_boundary_substitutions
python -B -m pytest -q \
  tests/architecture/test_generic_integrity.py \
  tests/architecture/test_source_discipline.py \
  tests/architecture/test_dependency_direction.py \
  tests/architecture/test_graph_execution_ownership.py
python -B -m ruff check src tests
python -B -m ruff format --check src tests
pyright
python -B -m pytest --ignore=tests/architecture/test_complexity_gate.py \
  --cov=mote_kernel --cov-report=term-missing
python -B -m build --no-isolation
python -B -m twine check dist/*
cd .. && SKIP=kernel-complexity pre-commit run --all-files
git diff --check
```

完整 `make check` 不作为 S01 命令，因为它无条件包含已排除的 `complexity-ratchet`；上述命令逐项覆盖其余 lint、typecheck、
behavior/coverage 和 package checks。monorepo pre-commit 同样只跳过 `kernel-complexity`，其他 hooks 必须全部通过。不得把
这种明确排除冒记为完整 `make check` 或未跳过 pre-commit 已通过。

除 requirements 的 `GSP-S01`–`GSP-S08` 外，出现任一条件即停止 S01：需要第二 predicate/phase helper/DTO/context/
cache/index；错误优先级、definition-order nested recursion、edge/binding scan 或排序改变；`control_gates`/`direct_pairs`/
无语义转交 alias/双 freeze 残留；typed `dict` lookup 退化；新增 private/AST/source-layout test；actual manifest 越界；
State/持久化边界被触及；任一适用 scoped/engineering check 未通过且无精确阻断记录。

actual diff 已同时满足唯一 owner、结构账本全部闭合、重复事实归零、behavior matrix、无 legacy gate、no-State/
no-persistence、四文件原子 manifest 和全部适用 gate；S01 implementation 完成证据见 7.24 owner writeback。

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

#### 3.2.2 S07 GSP-A06 单项设计（APPROVED / IMPLEMENTED，2026-08-23）

本节先以 docs-only design-review unit 完成设计。随后用户在本对话明确写下原文“补上吧，我是批准的”，
requirements approval commit `8b6785e` 据此将 S07 准入状态改为 `GSP-A06 SATISFIED`；production 与 normative
implementation 仅在该 approval commit 之后由 `dd15084` 应用，最终 actual evidence 由本节之后的独立
owner-writeback commit 记录。

S07 只收敛从 compiled GraphInput、NodeOutput binding 与 resume materialization plan 形成 availability coordinate 的
机械重复，适用 `GSP-P03`–`GSP-P08`；不触及公共 API 或 State/command，因此排除 `GSP-P01/P02`。函数签名固定为：

```python
def _graph_input_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
) -> GraphInputAvailabilityCoordinate[GraphValueT]: ...


def _node_output_coordinate(
    graph: CompiledGraph[GraphValueT],
    scope_run: ScopeRunCoordinate,
    source: NodeOutputPort,
    superstep: int,
) -> PublicationAvailabilityCoordinate[GraphValueT]: ...


def _resume_input_coordinate(
    activation: StableActivation,
    plan: MaterializationPlan[GraphValueT],
) -> ResumeInputAvailabilityCoordinate[GraphValueT]: ...
```

前两个 constructor 由 `engine/routing.py` 作为 binding availability owner 持有，供 admission、routing 与
resume-input consumer 复用；第三个只留在 `engine/resume_input.py`。跨模块下划线 symbol 只在 routing 模块自己的
`__all__` 中显式登记以满足 strict Pyright 的 `reportPrivateUsage`，不从 `mote_kernel.execution` 导出，不形成公共
入口。NodeOutput constructor 只接受 exact `NodeOutputPort` 和调用方已经解析出的 `int` superstep；每个调用边界仍先
以自己的 error variant 执行 `require_publication_selection()`，再调用 `selection.resolve()`。三个 constructor 都不
接受 optional selection、wide union、`object`、callback、context bag 或 error 参数，因而不吸收调用边界错误分类。

单项净复杂度账本如下：GraphInput direct assembly `6 → 1`，NodeOutput-binding direct assembly `6 → 1`，resume-input
direct assembly `2 → 1`；State-settlement 派生的 publication assembly 不是 NodeOutput nominal source，保持 `1 → 1`。
因此三个目标 source 的 assembly sites `14 → 3`、重复 assembly `11 → 0`，全部 direct coordinate constructor calls
`15 → 4`；private source-specific constructor `0 → 3`，field/DTO/cache/index/semantic branch/full scan 均
`0 → 0`。三个新函数分别有 6、6、2 个 production call site，不是 single-use thin helper；implementation commit
`dd15084` 的四文件 diff 为 `70 insertions / 72 deletions`，其中 production 与 normative 文件净删 12 行；行数只作补充，
不替代上述结构账本。

成功 characterization 复用
`tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input`、
`tests/execution/engine/test_routing.py::test_direct_conditional_and_terminal_routing_use_one_contribution_model` 与
`tests/execution/engine/test_resume_input_contract.py::test_pending_input_availability_accepts_state_and_acknowledged_overrides`。
失败/边界 characterization 复用 output projection/availability 的 missing GraphInput、missing publication、missing
selection cases，resume input 的 missing GraphInput/publication、missing selection、foreign scope cases，以及 recovery
historical target/output gap cases。exact-shape/tamper 证据继续由既有 coordinate type single-owner、generic integrity、
source discipline、dependency direction 与 public/owner behavior cases 承担；不新增 legacy/private-shape AST test。
`14 → 3` 与旧 direct blocks 归零由 implementation commit 的 actual diff/source review 闭合。

change unit 必须按以下顺序独立提交，manifest 互不混合；本次 Git 历史已按该顺序形成：

1. design-review commit `a0ee588` 只包含
   `mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md`。
2. approval commit `8b6785e` 只包含
   `mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md`；只有该单元改变了 `GSP-A06`
   的 S07 准入状态。
3. implementation commit `dd15084` 只包含：

   ```text
   mote-kernel/src/mote_kernel/execution/engine/admission.py
   mote-kernel/src/mote_kernel/execution/engine/resume_input.py
   mote-kernel/src/mote_kernel/execution/engine/routing.py
   mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
   ```

4. implementation 完成并通过 scoped gate 后，owner-writeback unit 只包含
   `mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md`，记录 actual manifest、actual
   diff/source review 与验证结果；本次 writeback 尚未计入上述三个历史提交，待本提交单独落地。

任一单元都不包含测试、State、protocol、README 或 complexity unit；existing behavior gates 只复用，不增加 legacy
测试。

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

以下方向不属于上述 24 个 target ledger ID，必须另开需求/架构评审：

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
- S08–S11 后 routing 仍是 join/routing facts 的唯一算法 owner，recovery 只消费其 typed projection；
- S17 后 resume candidate 只保存 exact command 与 substitutions，不保存 skip/pure-skip 镜像事实；
- S20 后 `materialize_node_input` 是 pending execution 与 failed retry 的唯一 compiled materializer；
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
| R14 | S03–S06 的 grouped exact-shape 断言登记到现有 compiled-lowering owner nodeid；S03、S04 各自更新一次，S05+S06 联合单元以一次完整 `CompiledGraph` exact-shape 更新同时闭合两项，且不新增 legacy case；S18 另有固定 architecture target nodeid 承载 source/AST gate | 7.2.2、7.2.3 |
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

### 每个 delivery change unit 的原子步骤

requirements 已明确批准当前矩阵中的 15 个 P1 target ledger ID。除 3.1 节明确授权的 S05+S06 联合边界外，
每个获批 P1 都是独立 delivery change unit；合计 14 个 P1 delivery change unit。任一 change unit 未过完整
门禁，不得交付或开始下一个：

requirements 第 5 节已把实现内部的原子迁移边界唯一交给本文；其“逐项实施”在执行层按本文登记的 delivery
change unit 解释，不等于强制每个 target ledger ID 各自形成提交。该口径不改变 15 个 P1 的批准集合或 evidence。

1. 冻结当前成功、失败、错误优先级、遍历/调用顺序和 exact shape；
2. 明确唯一 canonical owner 和目标类型；
3. 在同一最小变更中迁移全部 consumer，并删除重复 producer 字段/扫描/分支；
4. 在同一原子变更中落地对应 target test，并同步实际受影响的 normative 文档；Phase 0 不提前提交未来 shape；
5. 运行第 7 节完整门禁并核对净新增/删除账本；只有 T0 从 `DESIGNED / PENDING IMPLEMENTATION` 变为
   `PASS` 后该单元才可交付。

不得增加 forwarding property、compatibility alias 或临时双写让中间提交过门禁。

### Phase 1：不改变 compiled/recovery shape 的 P1

按独立 change unit 实施 S13、S23A、S18、S08、S09、S10、S11、S17、S20、S23B。

### Phase 2：compiled/recovery shape 的 P1

按 change unit 实施 S03、S04、S05+S06、S14。每个 change unit 都必须在同一提交完成 producer/consumer、
normative 文档和 exact-shape tests 迁移；S05、S06 不得再拆成两个 implementation manifest、提交或回滚边界。

### Phase 3：engine 内部 P2

S07 已按 3.2.2 完成；S01 已按 3.1.2 完成 implementation。S02、S12、S15、S16、S19 仍须单项满足
`GSP-A06` 并通过设计复审；未通过的单元保持现状。S12 不继承 S01、S07 或 P1 的批准状态。

### Phase 4：facade/transaction P2

S21、S22 只有满足 `GSP-A06` 并通过单项设计复审后才能实施，不得与其他单元混合。三个 public `run()` overload 及 transaction error timing 必须逐字保持。

### Phase 5：账本外重构

第 4 节方向需另立 requirements 和实施方案，不继承本文批准状态。

### 6.1 P1 原子依赖约束

Phase 1/2 的列表是建议执行顺序；下列同时标明真正的硬依赖和仅为降低交叉触碰的推荐顺序。约束只适用
于已获 `GSP-A05` 的单元，不能被用作提前实施的理由。

| 前置单元 | 后置单元 | 级别 | 依赖理由 | 未满足时 |
| --- | --- | --- | --- | --- |
| S03 | S04、S05+S06 | 推荐顺序 | 先从 compiled topology 去掉分类镜像，再收窄 transition/publication/input projection，减少旧 consumer 被重复触碰 | 若 owner 证明无交叉，可在 change-unit 复审后调整；不得双写 |
| S04 | S05+S06（S06 子账本） | 硬依赖 | publication consumer 必须在 S04 一次迁移到 `graph.transition.publications[...]`；联合单元中的 S06 只验证 zero-use | 任一 consumer 仍走旧 projection 即停止 |
| S08 | S09、S10、S11 | 硬依赖 | routing facts 与 join owner 先闭合，后续 projection/availability 才能消费唯一 compiled truth | 不得并行改 recovery/resume consumer |
| S09 | S10、S11 | 推荐顺序 | 先固定 facts → `ResolutionCommand` 的唯一 projection，再删除镜像 output/input diagnostics | 不得保留 `RoutingResolution`/`plan_routing()` 兼容路径 |
| S13 | S14 | 推荐顺序 | 先缩窄 `_initial_children()` 输入，再删除 `_NestedOutcome` 的 boundary 镜像，避免两个 recovery shape 同时漂移 | 若无法证明独立，保持两项现状 |
| S23A | S23B | 推荐顺序 | 先统一 advance 的 `None` 返回，再合并 awaiting-result 有序 projection，保持 family-driver 分支边界清晰 | 不得合并两个 sentinel/scan 变更 |

其余 P1（S17、S18、S20）无生产字段依赖，但仍必须各自等待同一 `GSP-A05`，并按单元 manifest 独立交付；
除已明确授权的 S05+S06 外，“无硬依赖”不等于可以合并提交或跳过完整门禁。

## 7. 验收矩阵与可复现命令

### 7.1 当前基线

- 代码基线：`7944159`（`feat(kernel): support outputs for skipped failures`）；本轮未修改 production/tests；
- `make check` 历史基线（2026-08-19，本工作区）：full suite 817 passed，coverage 100%，Pyright strict 0 errors，Ruff/build/Twine 通过；
- 第九次复审吸收后复跑 `make check`（2026-08-20）：817 passed in 44.85s，coverage 100%，Pyright 0 errors，Ruff/build/Twine 通过；该结果仍只证明当前 baseline，不能替代尚未落地的 target gate；
- State/持久化范围审计（2026-08-20）：24 个 target ledger ID 的 production location 均属于 `execution/**`；
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
断言。S03 已删除既有 owner field-set 中的两个分类镜像并以 source review 闭合，不新增 private shape 的 legacy AST 断言；S04 已通过更新既有 owner field-set、既有行为 case 与 source review 闭合，同样不新增 legacy AST 断言；S08 已通过更新既有 owner 集合断言闭合，不新增 private helper/import/comprehension 的 legacy AST 断言；
S05+S06 联合单元复用既有 compiled-lowering owner case、compiler/admission/resource/output/recovery 行为边界，并以 actual
diff/source review 确认 graph-input mirror、recovery wrapper、convenience projection 与旧 consumer 读取归零；同样
不新增 legacy/private-shape AST 断言。S09 已迁移既有行为 case，并以 actual diff/source review 确认 DTO、wrapper 和镜像字段归零；S10 已迁移既有
output/recovery/resume 行为边界，并以 actual diff/source review 确认 canonical diagnostic 与单次 full scan；S11
继续复用既有 target/recovery/resume 行为边界，并以 actual diff/source review 确认每个 unique target 的单次 binding
scan、typed cache 与三字段 fact；S17 复用既有 pure-skip/substitution、exact successor 与零 commit 行为边界，
并以 actual diff/source review 确认六字段 candidate、command-action 单次派生与 typed coordinate 差集。同样不新增
针对已删除 private shape 的 legacy AST 断言。S20 复用既有 failed-retry/default/override 与 missing-publication
行为边界，并以 actual diff/source review 确认唯一窄 materializer、replacement projection 归零及最终 simulation/
validation 保留。S23B 复用既有 failure/interrupt identity 行为，并新增 public mixed root/child-scope Result case
冻结 canonical root→child ordering 与 payload；单次 scoped-state scan、两个 typed tuple accumulator 和旧 projection helper
归零只由 actual diff/source review 闭合，不新增 legacy/private-shape AST 断言。S14 复用既有 terminal、awaiting、
malformed-child recovery 行为边界，并以 actual diff/source review 确认 `_NestedOutcome` 三个镜像字段、旧
state-based child-control projection 与全部旧 consumer 读取归零；同样不新增 private-shape AST 断言。已完成
production-only 的 S13、S18、S23A 保持
`PRODUCTION IMPLEMENTED / T0 DEFERRED`，不能把尚不存在的未来测试写成已通过。
每个 baseline case 均须有
可核对的成功与失败/边界路径；target gate 还须写明断言对象和失败条件。下表中的 `PASS` 只表示本轮
`make check` 覆盖的当前测试已经通过，不表示未来 target 已获准。

本轮按表中当前 baseline nodeids 复跑 31 个 case（含参数化展开），第九次复审替换 S23B interrupt nodeid 后
结果为 `31 passed in 0.29s`
（2026-08-20，代码基线 `7944159`）；15 个 target gate 的设计已形成。随后 S03/S04 更新既有 owner field-set，S08 复用并收窄既有 architecture
owner case，S09–S11、S17、S20 与 S23B 复用既有行为 gate 和一次性 source review；S23B 另新增一条 public
mixed root/child-scope ordering case；S14 也复用既有 recovery behavior gate 和一次性 source review；这些单元均未新增 exact-shape test，
其他 target 仍按各自当前状态处理。

矩阵各行的 evidence profile 固定如下：`B0` 是当前 baseline case 命令；`T0` 在 Phase 0 是已固定的 target
path、断言、失败条件和预期 manifest 类别；S03–S06 的既有 owner/behavior gate 与一次性 source review 已通过，
S08 的既有 owner gate 已通过单一集合收窄，S09–S11、S14、S17 与 S20 的既有行为 gate 和一次性 source review 已通过，
S23B 的 public behavior gate 与一次性 source review 也已通过，但这些 change unit 的完整交付仍被工作树中的独立
complexity unit 阻断；已落地 production-only 的 S13、S18、S23A 状态为
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
| S10 | malformed graph-output selection、historical output 在 gap 前的稳定保留 | DIRECT PASS / SOURCE REVIEW PASS | 既有行为 case + S10 actual diff/source review 已闭合 canonical output diagnostic 与单次 full scan；不新增 legacy AST test |
| S11 | historical target gap、direct/join 重叠 target 去重、sibling scope、repeated superstep | DIRECT PASS / SOURCE REVIEW PASS | 既有行为 case + 本次 actual diff/source review 已闭合单次 typed scan、cache 首次访问和首个 unavailable identity；不新增 legacy AST test |
| S14 | completed/aborted、awaiting child boundary 及 malformed child control 均由 `engine/recovery.py` 路径覆盖 | DIRECT PASS / SOURCE REVIEW PASS | 既有行为 case + S14 actual diff/source review 已闭合 boundary-owned kind/availability/control 与两字段 outcome；不新增 legacy AST test |
| S18 | public duplicate skip 在首个 commit 前由 `plan_resumes()` 拒绝；non-tuple/noncanonical/unknown scope 保持 invocation 错误顺序；resume-admission coordinate cases 由 B0/B1 覆盖 | DIRECT PASS | 两个 owner 的 no-`.count()`/no-double-enumeration AST gate 仍待 T0 |
| S23A | final settlement → `ReadyToResolve`、facade nested success/fail-closed 均经过现有 driver | BEHAVIOR PASS（INDIRECT OWNER COVERAGE） | `_AdvancedFrontier` 归零、return annotation、advance/non-boundary `None` 与 nested boundary/error 分类由 7.2.2 的 direct target gate 验证 |

S23A 删除的是 private `_AdvancedFrontier` marker；Phase 0 需要冻结的是 `drive_root()` 的外部循环行为，而不是先为
即将删除的 private return variant 新增 characterization。现有 end-to-end cases 作为 B0/B1 behavior evidence
已经足够；direct exact-shape/return-class assertion 与 S23A production 在 `GSP-A05` 后原子落地。不得把它提前
写成当前 PASS，也不得把 direct baseline 缺失继续当作 A05 blocker。

| 单元 | 全部适用 requirements | baseline 成功路径与断言（当前） | baseline 失败/边界路径与断言（当前） | baseline 状态 |
| --- | --- | --- | --- | --- |
| S03 | `GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_facade_drives_nested_graph_through_the_same_execution_owner`；完成结果通过同一 facade/engine，提交中存在 child scope | `tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly`；child boundary 不匹配在 compile 前抛 `GraphValidationError` | B0 DIRECT PASS / PRODUCTION IMPLEMENTED / OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S04 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_skip_failed_substitution_publishes_exact_output_for_downstream_materialization`；replacement publication 被下游消费并得到 `accepted:replacement` | `tests/execution/engine/test_output_projection.py::test_output_projection_reports_a_missing_confirmed_publication`；缺失 confirmed publication 抛 `GraphValueAdmissionError` | B0 DIRECT PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S05 | `GSP-P01`、`GSP-P03`、`GSP-P04`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input`；admitted graph input 按声明投影为 renamed output | `tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly`；nested boundary mismatch 保持 compile-time rejection | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S06 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/graph/test_compiler.py::test_compilation_normalizes_node_requirements_by_graph_resource_order`；graph、node、resource tuple 均保持 canonical FIFO 顺序 | `tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order`；顺序 tamper 在 admission 前抛 `ResourceTransitionError` | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S08 | `GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_routing.py::test_join_fires_only_after_all_sources_arrive_across_supersteps`；首步保留 join progress，全部 source 到达后才 advance target | `tests/execution/engine/test_routing.py::test_duplicate_recovered_join_progress_fails_closed`；重复 recovered progress 抛 `JoinProgressError` | B0 PASS / PRODUCTION IMPLEMENTED / OWNER GATE PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S09 | `GSP-P02`、`GSP-P05`、`GSP-P06`、`GSP-P08` | `tests/execution/engine/test_routing.py::test_direct_conditional_and_terminal_routing_use_one_contribution_model`；direct/conditional/terminal 产生既有 `AdvanceGraphFrontier`/`CompleteGraphFrontier` | `tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance`；缺失 control input 先产生 `AbortGraphRun`，不 advance | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S10 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_graph_output_can_project_and_rename_an_admitted_graph_input`；可用 graph output 正常完成并保留投影值；B1 historical-output case 固定 gap 前已见 output 的顺序 | `tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable`；completion output 不可用时 abort；B1 malformed selection 保持 `InvalidRoutingCommandError` 优先 | B0 PASS / B1 DIRECT PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S11 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_resume_admission.py::test_resume_admission_accepts_triggered_data_target_with_complete_inputs`；完整 input 被 admission；B1 覆盖重叠 target、sibling scope、repeated superstep 与 gap 前 present input | `tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_triggered_data_target_with_an_unavailable_input`；缺失 input 在 admission 前抛 `GraphValueUnavailableError`，首个 binding/target 顺序由 B1 锁定 | B0 PASS / B1 DIRECT PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S13 | `GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children`；completed/aborted child 映射到预期 boundary status | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding`；run/parent control tamper 抛 `SnapshotMismatchError` | PASS（31-case run） |
| S14 | `GSP-P04`、`GSP-P05`、`GSP-P06`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children` 与 `::test_recovery_preflight_propagates_an_awaiting_child_boundary`；recovery owner 保持 completed/aborted/awaiting boundary control | `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_rejects_each_malformed_child_control_binding`；run/parent/control tamper 保持 `SnapshotMismatchError` | B0 PASS / B1 DIRECT PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S17 | `GSP-P02`、`GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_pure_skip_future_proof_accepts_a_substitution_candidate_path`；pure skip + replacement 完成，consumer 看到 replacement | `tests/execution/test_graph_api.py::test_pure_skip_future_proof_rejects_output_lost_after_a_runnable_step_before_commit`；历史 output 丢失抛 `ValueUnavailableError`，commit 仍为空 | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S18 | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/engine/test_resume_admission.py::test_resume_admission_keeps_distinct_scope_coordinates_isolated` 及 repeated-superstep B1 case；不同 coordinate 保持隔离 | `tests/execution/test_graph_api.py::test_duplicate_public_skip_candidates_are_rejected_before_commit` 直接覆盖 `plan_resumes()` duplicate action coordinate；resume-admission duplicate/confirmed collision 保持 `GraphValuePublicationError` | B0 PASS / B1 DIRECT PASS / PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S20 | `GSP-P02`、`GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_executor.py::test_resume_projection_covers_override_default_skip_and_interrupt_input_guards`；override/default/skip/interrupt 各自保留既有 input guard | `tests/execution/engine/test_resume_input_contract.py::test_materialization_reports_missing_confirmed_publication`；materialization 缺失 node output 抛 `GraphValueUnavailableError` | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |
| S23A | `GSP-P03`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | facade nested success 与 `test_final_settlement_recovers_as_ready_to_resolve_without_reexecution` 间接证明 root loop/resolve 可继续 | facade nested coordination case 保持 fail closed；private return shape 已落地，direct T0 因不新增测试而 deferred | B0/B1 BEHAVIOR PASS（indirect owner coverage）；PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S23B | `GSP-P01`、`GSP-P04`、`GSP-P05`、`GSP-P07`、`GSP-P08` | `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run`；failure actions canonicalize 且结果共享 run | `tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run` 保持 interrupt identity 与 stale-ID fail closed；新增 `::test_awaiting_result_views_preserve_canonical_root_to_child_scope_order` 通过 public Result 冻结 mixed root/child scope ordering、failure 文本和 interrupt payload | B0 PASS / PRODUCTION IMPLEMENTED / BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT |

上述 15 行的 baseline behavior 均为 `PASS`；S23A 的 owner coverage 是 indirect，但足以冻结外部循环语义。
每行对应的 `T0` target path、断言和失败条件均已设计；当前 15 个已批准 P1 的 production target 均已落地。
S03–S06、S09–S11、S14、S17、S20 与 S23B 已按最终裁决以 public/既有行为 gate 和一次性 source review 闭合，不新增
legacy AST test；S13、S18 与 S23A 则按后续 owner writeback 标记为
`PRODUCTION IMPLEMENTED / T0 DEFERRED`。requirements 第 7 节已依据本矩阵只批准这 15 个 P1；其余单元的
T0 仍须在对应单元获准后随 production 原子落地并通过后才可交付。

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
| S17 resume candidate mirrors | `P02`（exact command/reducer successor）；`P03`（commit 前 admission）；`P04`（publication/substitution coordinate identity）；`P05`（pure skip/future proof）；`P07`（scope/superstep isolation）；`P08`（六字段 candidate 与 owner-local typed derivation） | `P01`、`P06`：不改 public API，也不改 recovery traversal/equality/budget |
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
| S03 | 更新既有 `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering`，仅从既有 exact field set 删除 `callable_node_ids`/`nested_node_ids` 两项；compiler 的两个 tuple producer 删除和 consumer 向 `nested_graphs` keys / `CallableNodeDefinition` nominal check 的迁移由本次 actual diff/source review 闭合，不新增 producer、consumer、私有 symbol 或表达式形状的 legacy AST 断言。nested facade、compile mismatch、frontier、resource admission 与 nested recovery 继续由既有行为用例覆盖 | BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S04 | 更新既有 `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering`，只删除 `outcomes` field expected 并收窄 `publications` value expected 为 `FrameDescriptor[...]`；`MaterializationPlan.node_id`、两个 DTO、旧 projection 读取与 `.descriptor` 二次访问由 actual diff/source review 核对；同一 review 确认 conditional route 解释只在 routing owner，scheduler 复用 `validate_routing_contribution()`、recovery 复用 routing owner 的 `_success_routes()`，不新增 legacy AST/private-symbol 子断言。malformed publication 继续由 `tests/execution/test_continuation_integrity.py::test_complete_continuation_rejects_a_malformed_publication_record` fail closed | BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S05 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S05.a`：`CompiledGraph` 不定义 `graph_inputs` field/property/alias；`S05.b`：compiler/runtime 只读取 `graph_input_descriptor.declarations`；`S05.c`：`tests/execution/graph/test_compiler_contract.py::test_compiler_requires_nested_inputs_to_match_child_boundary_exactly` 仍在 compile 前拒绝 boundary mismatch。该 grouped owner case 只更新既有 exact-shape 断言，不新增 legacy AST case | BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S06 | `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering` 的 `S06.a`：`recovery` wrapper/storage 不存在；`S06.b`：`transition` 是 direct dataclass field，不是 property；`S06.c`：`entries`/`materializations`/`publications`/`graph_outputs`/`resource_order` 不在 `CompiledGraph` 定义；resource-order tamper 仍由 `tests/execution/engine/test_admission.py::test_admission_rejects_snapshot_with_noncompiled_resource_order` 拒绝。该 grouped owner case 只更新既有 exact-shape 断言，不新增 legacy AST case | BEHAVIOR + OWNER + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S08 | 更新既有 `tests/architecture/test_graph_execution_ownership.py::test_compiled_routing_is_interpreted_only_by_routing_and_snapshot_guard`，只把 `joins_by_source` 的 production direct-owner 集合从 routing + snapshot guard 收窄为 routing。`_declared_joins` 的 import/call、helper symbol 和 comprehension 形状只作为本次 actual diff/source review，不新增永久 legacy AST 断言；重复/malformed progress 继续由既有行为测试 fail closed | OWNER GATE PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S09 | 更新既有 `tests/execution/engine/test_routing.py::test_selected_control_target_with_missing_input_aborts_before_advance` 与 `tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable`，直接断言既有 abort command/reason；DTO、forwarding wrapper、镜像字段和全部 consumer 迁移只作为本次 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S10 | 复用既有 `tests/execution/engine/test_output_projection.py::test_routing_aborts_when_completion_output_is_unavailable`、`tests/execution/engine/test_recovery_identity.py::test_recovery_historical_output_scan_retains_present_outputs_before_the_gap` 和 completed-continuation output boundary cases，直接保持 abort/error、historical gap 与 malformed selection 顺序；canonical tuple、单次 full scan 和两个 bool 删除只作为 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S11 | 复用既有 `tests/execution/engine/test_recovery_identity.py::test_recovery_historical_target_scan_retains_present_inputs_before_the_gap`、`tests/execution/engine/test_routing.py::test_completed_joins_and_direct_arrivals_deduplicate_targets`、resume-admission complete/unavailable input、scope 与 repeated-superstep cases，保持 historical gap、overlap、首错和 coordinate isolation；三字段 fact、每个 unique target 单次 binding scan、typed cache 及 control → join → data 首次访问只作为 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S13 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_initial_children_signature_contains_only_consumed_inputs`：`_initial_children()` 不再接受未使用 availability 参数或构造 phantom input；malformed child binding 仍 fail closed | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S14 | 复用既有 `tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children`、`::test_recovery_preflight_propagates_an_awaiting_child_boundary`、`::test_recovery_preflight_linearizes_completed_and_aborted_child_possibilities` 与 `::test_recovery_preflight_rejects_each_malformed_child_control_binding`，直接保持 completed/aborted/awaiting 及 malformed control 行为；两字段 `_NestedOutcome`、boundary-owned kind/availability、control-only disposition 与旧 consumer 归零由 actual diff/source review 闭合，不新增 legacy/private-shape AST test | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-22） |
| S17 | 复用既有 `tests/execution/test_graph_api.py::test_pure_skip_future_proof_accepts_a_substitution_candidate_path`、`::test_pure_skip_future_proof_rejects_output_lost_after_a_runnable_step_before_commit`、resume-admission exact successor/substitution evidence 与 runtime boundary cases，保持 mixed pure/replacement、typed coordinate identity、首错与零 commit 边界；六字段 candidate、command-action 单次派生和 coordinate 差集只作为 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S18 | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_resume_duplicate_indexes_are_owner_local_and_linear`，承载 `S18.a` invocation/admission 的两个 typed count dict、每 owner 一个 index、`S18.b` 无 `.count()`、`S18.c` 无先 `any` 后重扫且 duplicate-before-collision；`tests/execution/engine/test_resume_admission.py::test_resume_admission_rejects_duplicate_and_confirmed_substitution_coordinates`、`::test_resume_admission_keeps_repeated_superstep_coordinates_isolated` 和 `tests/execution/test_graph_api.py::test_duplicate_public_skip_candidates_are_rejected_before_commit` 继续证明行为、错误 identity 和 coordinate isolation | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S20 | 复用既有 `tests/execution/test_executor.py::test_resume_projection_covers_override_default_skip_and_interrupt_input_guards`、resume-input scope/missing-publication cases，并更新 `tests/execution/engine/test_resume_input_contract.py::test_failed_retry_materialization_requires_a_current_failed_node` 直接保持 failed/pending nominal guard；窄 keyword、replacement State/frontier 归零及最终 simulation/validation 只作为 actual diff/source review，不新增永久 legacy AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |
| S23A | 待新增 `tests/architecture/test_graph_execution_ownership.py::test_family_driver_uses_none_for_advance_without_marker`：`S23A.a` return annotation 无 `_AdvancedFrontier`；`S23A.b` AdvanceGraphFrontier、普通 non-boundary 均返回 `None`；`S23A.c` nested coordination boundary/error 分类不变 | PRODUCTION IMPLEMENTED / T0 DEFERRED |
| S23B | 复用 `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run`、`::test_interrupt_resume_is_an_exact_action_inside_run`，并新增 `::test_awaiting_result_views_preserve_canonical_root_to_child_scope_order`，直接冻结 public failure/interrupt identity、payload、stale-ID fail closed 和 mixed root→child scope order；单次 `_scoped_states()` scan、两个 typed tuple accumulator 与旧 helper 删除只作为 actual diff/source review，不新增永久 legacy/private-shape AST 断言 | BEHAVIOR + SOURCE REVIEW PASS / DELIVERY BLOCKED BY INDEPENDENT WORKTREE UNIT（2026-08-21） |

上述 target gate 是已完成的 Phase 0 设计，不冒充当前已存在的测试，也不要求在 `GSP-A05` 前落地。
requirements 已依据 baseline behavior、这张 target 设计表及 `GSP-A01`–`GSP-A04` evidence 完成 `GSP-A05`
授权；每个 gate 必须与对应 production 原子落地，T0 未通过的单元不得交付或进入下一单元。

S03 已从 `FrontierTransitionPlan` 删除两个 classification 镜像 field，并原子删除 compiler 的两个 tuple
producer。frontier/recovery 的 nested 分类现只消费 `CompiledGraph.nested_graphs` keys，frontier/admission 的
callable 分类现只消费 `CompiledGraph.nodes` 中的 `CallableNodeDefinition` nominal variant；既有 compiled-lowering
owner case 只删除两个旧 expected field，没有新增 producer、consumer、private symbol 或具体表达式形状的 legacy
AST 断言。nested facade、compile mismatch、frontier、resource admission 与 nested recovery 的既有行为用例保持
通过，actual source review 确认 active production/tests 中两个旧 symbol、两个 compiler tuple producer 与全部旧
projection 读取均为 0。因此 S03 的 behavior/owner/source target 已通过，但完整工作树仍受独立 complexity unit
阻断，当前不记为零负债整体交付 `PASS`。

S04 已完成 publication/outcome 编译计划收敛。`graph/ports.py` 删除 `OutcomeAdmissionPlan`、`PublicationPlan` 和
`MaterializationPlan.node_id`；`FrontierTransitionPlan` 删除 `outcomes`，其 `publications` map 直接保存
`FrameDescriptor`，`CompiledGraph.outcomes`/`publications` projection 同步删除。scheduler 通过 routing owner 的既有
`validate_routing_contribution()` 校验 route，recovery 使用从 routing owner 迁移的既有 `_success_routes()`；因此
conditional route 的直接解释仍只有 `routing.py`。publication descriptor declarations 负责构造 node output；frontier、admission、routing、recovery、resume、executor、family driver 与 invocation 全部直接读取
`graph.transition.publications[node_id]`，不再经过 wrapper 或 `.descriptor` 二次访问。既有 owner field-set 仅删除
`outcomes` expected 并收窄 publication value expected，没有新增 legacy/private-shape AST 断言；actual source review
确认两个 DTO、旧字段、旧 projection 和旧 consumer 读取均为 0，既有 publication/output/recovery/nested 行为保持
通过。因此 S04 的 behavior/owner/source target 已通过，但完整工作树仍受独立 complexity unit 阻断，当前不记为零
负债整体交付 `PASS`。

S05 已完成 graph-input declaration owner 收敛。`CompiledGraph.graph_inputs` 已删除；nested compiler 与 runtime
admission 直接读取 `graph_input_descriptor.declarations`，没有新增 property、alias、adapter 或第二 declaration
source。联合 owner case 在既有 grouped test 中断言完整 `CompiledGraph` field set 与纯 annotated-field class body，
其中不含 `graph_inputs`，且没有新增 legacy AST/private-shape test；nested
boundary mismatch、graph-input admission、graph-output passthrough 与 recovery frame identity 的行为/source
证据均通过。该结果作为 S05 子账本并入 7.21 的 S05+S06 联合 delivery change unit，不生成独立 implementation
manifest、提交或回滚边界。

S06 已完成 compiled transition lowering 收敛。`RecoveryAvailabilityPlan`、`CompiledGraph.recovery`、transition
property 与剩余 convenience projections 均已删除，`CompiledGraph.transition` 直接持有
`FrontierTransitionPlan[GraphValueT]`；所有 entries/materializations/output/resource consumers 已迁移到
`graph.transition.*`。同一 grouped exact-shape assertion 确认 `transition` 是 direct field，且 `recovery` 与全部
forwarding members 不存在；没有新增 legacy AST/private-shape test。actual source review 确认旧 wrapper/import/read
为 0。该结果作为 S06 子账本并入 7.21 的同一联合 manifest、
提交与回滚边界。S05+S06 targeted suite 共 331 passed，但独立 complexity unit 仍阻断完整 change-unit 交付。

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

S10 已把 `RoutingFacts` 的 graph-output completion diagnostics 收敛为单一
`unavailable_graph_outputs: tuple[str, ...]`：resolver 内只执行一次完整 diagnostic scan，删除
`completion_output_available` 与 `completion_output_history_missing` 两个镜像 bool；`graph_outputs_available()` 的
首次缺失短路仍由 completed continuation、nested boundary 和 invocation validation 独立使用。recovery 与
resume admission 均从 target work + canonical tuple 推导 historical output 缺口。既有 output projection、historical
recovery、pure-skip、completed-continuation 和 malformed-selection 行为保持 fail closed；本次没有新增 legacy AST
test。actual source review 确认 resolver 内 `graph_outputs_available` 调用为 0、`unavailable_graph_outputs` 调用为 1，
且活跃 consumer 中两个旧 bool 已归零。因此 S10 的行为/source target 已通过，但完整工作树仍受独立 complexity
unit 阻断，当前不记为零负债整体交付 `PASS`。

S11 已把 target availability 与 historical-gap 解释合并为每个 unique `GraphNodeId` 一次 binding scan，并在一次
resolver invocation 内用唯一 `dict[GraphNodeId, RequiredTarget]` 按 control → completed join → data 的首次访问
顺序复用重叠 target。`RequiredTarget` 删除 `inputs_available` 镜像字段，只保留
`node_id/historical_inputs_missing/unavailable_inputs`；routing、recovery 与 resume admission 全部从 canonical
`unavailable_inputs` 推导 availability。既有 direct/join overlap、historical gap、data-target admission、sibling
scope 与 repeated-superstep 行为保持原顺序和错误边界；本次没有新增 legacy AST test。actual source review 确认
两个旧扫描函数与 `RequiredTarget.inputs_available` active consumer 均为 0，cache 与唯一 binding loop 各为 1。
因此 S11 的行为/source target 已通过，但完整工作树仍受独立 complexity unit 阻断，当前不记为零负债整体交付
`PASS`。

S14 已把 `_NestedOutcome` 从 `node_id/kind/availability/disposition/boundary` 五字段收敛为
`node_id/boundary` 两字段。所有 planning/settlement consumer 直接读取 canonical boundary 的
`kind/availability/control`；`_child_disposition_from_control()` 逐字段投影 equality-participating control，并删除旧
state-based `_child_control()`；失去复用价值的单调用 `_child_disposition()` adapter 也同步删除，nested outcome
不再从 `compare=False` 的 `boundary.state` 重建 identity。既有
completed/aborted、awaiting、mixed outcome、missing output 与 malformed child control 行为保持通过；本次只做
actual diff/source review，不新增 legacy/private-shape AST test。因此 S14 的 behavior/source target 已通过，但
完整工作树仍受独立 complexity unit 阻断，当前不记为零负债整体交付 `PASS`。

S23B 已把 failure/interrupt Result view 收敛为 family-driver 内唯一 `_project_result_views()`：它按既有
`_scoped_states()` root→child canonical 顺序只扫描一次，在同一 traversal 中填充两个 typed accumulator。
public failure/interrupt identity、payload、stale-ID fail closed 和 mixed root→child scope ordering 均由行为用例直接
冻结；旧 `_failure_views()`/`_interrupt_views()` 已原子删除，具体 helper 名称与循环形状不写入 legacy AST test。
因此 S23B 的 behavior/source target 已通过，但完整工作树仍受独立 complexity unit 阻断，当前不记为零负债
整体交付 `PASS`。

S13 的 `PRODUCTION IMPLEMENTED / T0 DEFERRED` 不是 `PASS`：production 已完成，但本次明确不新增
exact-shape architecture test，因此该单元仍不满足 T0 交付条件。S05+S06 联合单元与 S14 已完成既有行为/owner
gate 和 source review；S14 没有借机补写 private-shape architecture test。

S23A 同样标记为 `PRODUCTION IMPLEMENTED / T0 DEFERRED`：production 已完成，但本次按约束不新增
`test_family_driver_uses_none_for_advance_without_marker` exact-shape architecture test，因此不将其写成
T0 `PASS`。S23B 已作为独立后置单元完成 public behavior gate 与 source review；没有借 S23A 的 deferred
shape gate 自动放行，也没有补写 legacy AST 断言。

S18 也标记为 `PRODUCTION IMPLEMENTED / T0 DEFERRED`：两个 owner 的 production 检查已完成，但本次按约束
不新增 `test_resume_duplicate_indexes_are_owner_local_and_linear` exact-shape architecture test，因此不将
S18 写成 T0 `PASS`。

#### 7.2.3 Source/AST 子断言（R4 可执行口径）

以下谓词必须由对应 architecture owner gate 或等价的静态检查直接执行；只检查文件名、注释或测试名称
不算通过。S03–S06、S08–S11、S14、S17、S20 与 S23B 按 7.2.2 的最终裁决复用 public/既有行为/owner gate，并以各自
delivery actual diff/source review 闭合；其中 S05+S06 共用一份联合 actual diff，不为已删除 private symbol 或
具体表达式形状新增永久断言。除这十二个 target ledger ID 外，每个 predicate 的可复现
`path::test_case` 由 7.2.2 同一 target 行注册：S05–S06 共用
`test_frontier_transition_plan_is_the_single_compiled_execution_lowering`，并在联合 change unit 中一次更新完整
`CompiledGraph` exact-shape 分组断言，
S18 使用待新增的 `test_resume_duplicate_indexes_are_owner_local_and_linear`；本节的 predicate 表不能脱离这些
nodeid 单独充当 `GSP-A03` evidence。谓词针对目标提交的最终 source shape，不改变当前 baseline，也不允许通过
兼容 alias 绕过：

| 单元 | 必须为真的 source/AST 谓词 | 失败条件 |
| --- | --- | --- |
| S03 | 既有 owner gate 的 exact field set 不含 `callable_node_ids`/`nested_node_ids`；本次 actual diff/source review 确认 compiler 两个 classification tuple producer 与 active consumer 的旧 projection 读取均为 0，consumer 只按 `nested_graphs` keys 或 `CallableNodeDefinition` nominal variant 分类 | owner field set 仍登记旧字段，source review 发现 producer/旧读取残留，或行为用例不再保持 nested/frontier/resource/recovery 边界 |
| S04 | 既有 owner gate 的 exact field set 不含 `outcomes`，且 `publications` value exact 为 `FrameDescriptor[...]`；本次 actual diff/source review 确认两个 DTO、`MaterializationPlan.node_id`、`CompiledGraph` 旧 projection、旧 graph publication/outcome 读取与 `.descriptor` 二次访问均为 0，publication consumer 直接读取 `graph.transition.publications[node_id]`，conditional route 解释只由 routing owner 持有 | DTO/import/property/重复 key/旧 projection/非 routing route interpretation 残留，或 consumer 未迁到 canonical descriptor map |
| S05 | 既有 owner gate 的完整 `CompiledGraph` field set 不含 `graph_inputs`，且 class body 只含 annotated direct fields；nested compiler/admission 只读取 `graph_input_descriptor.declarations` | field、property、assignment alias 或第二 graph-input declaration source |
| S06 | 同一完整 field set 含 direct `transition: FrontierTransitionPlan[GraphValueT]`，不含 `recovery`/`entries`/`materializations`/`publications`/`graph_outputs`/`resource_order`，且 class body 只含 annotated direct fields；`RecoveryAvailabilityPlan` 无 production import | wrapper/forwarding property/method/assignment alias、direct-field 反转或 publication projection 回归 |
| S08 | 既有 owner gate 只允许 routing 直接读取 `joins_by_source`；snapshot guard 对 routing owner 的复用由本次 actual diff/source review 核对，不新增 private helper/import/comprehension AST 断言 | owner gate 仍登记 snapshot guard 或其他 production 模块为 `joins_by_source` direct reader；行为用例不再对 malformed/duplicate progress fail closed |
| S09 | 本次 actual diff/source review 确认 `RoutingResolution` class 和 `plan_routing` definition/import 数为 0；`project_routing_facts` 与 `resolve_routing` return annotation 均为 `ResolutionCommand`；recovery 只保留同一 local facts | forwarding DTO/wrapper 残留、command projection 分叉或第二 facts scan |
| S10 | 本次 actual diff/source review 确认 `resolve_routing_facts` 内 `graph_outputs_available` 调用数为 0、`unavailable_graph_outputs` 调用数为 1；`RoutingFacts` 只含 canonical diagnostic tuple，不含两个 completion bool；独立 `graph_outputs_available` 短路 consumers 保持原有 owner | full diagnostic 重复扫描、短路 helper 被错误删除或 bool mirror 回归 |
| S11 | 本次 actual diff/source review 确认 `RequiredTarget` 精确为 `node_id/historical_inputs_missing/unavailable_inputs`；`unavailable_target_inputs` 与 `_target_has_historical_gap` symbol/reference 为 0；local cache annotation 精确为 `dict[GraphNodeId, RequiredTarget]`，唯一 binding loop 按 control → completed join → data 首次调用 | field/scan/cache key 漂移、首错顺序改变或新增 display identity |
| S14 | 本次 actual diff/source review 确认 `_NestedOutcome` field 精确为 `node_id/boundary`，旧 `kind`/`availability`/`disposition` consumer 为 0；`_child_disposition_from_control(control: ScopeControlStateCoordinate)` 只逐字段消费 equality-participating control，nested outcome 不读取 `boundary.state` 重建 identity；`_completed_child_outcome` 也只从 `boundary.control.scope_run` 取 coordinate；旧 `_child_control` 与单调用 `_child_disposition` definition/reference 均为 0；既有 terminal/awaiting/malformed-control 行为 case 通过 | 镜像字段/consumer、compare=False state identity 重建、旧 helper/coordinate 参数或第二 child projection 残留，或 recovery 行为边界改变 |
| S17 | `ScopedResumeCandidate.__dataclass_fields__` 不含 `skip_actions`/`has_pure_skip`；每个 candidate 的 command-action tuple 与 action-publication/substitution coordinate difference 只保留一个 local derivation 生命周期 | mirror field、重复 action scan、忽略 descriptor/scope coordinate 或纯 skip 通过独立 bool 恢复 |
| S20 | `materialize_node_input` 是唯一 materializer symbol，签名只允许 `failed_retry_input: UseStepRequestInput \| None = None`；`GraphNodeInputBinding`/`OverrideGraphNodeInput` 不进入该 keyword；`executor.py` 的 failed-retry materialization 分支不构造 `replace(state, frontier=...)` 或 replacement `GraphFrontierState`；最终 `simulated = GraphFrontierState(...)` 和 `validate_graph_frontier(state, simulated)` 各存在且调用一次 | wide union、同义 wrapper、第二 materializer、删除/后移 final simulation 或 validation、materialization-only replacement State/frontier 残留 |
| S23A | `_AdvancedFrontier` symbol/union member/constructor/reference 数为 0；`_advance_scope_quantum` annotation 为 `GraphBoundary \| None`，`drive_root` 无 marker `isinstance` | sentinel、第二 disposition 或 loop 分支语义漂移 |
| S23B | 本次 actual diff/source review 确认 `_project_result_views` 只调用一次 `_scoped_states`，以两个 typed list accumulator 返回两个 tuple；`_failure_views`/`_interrupt_views` symbol/reference 为 0；public mixed root/child-scope case 直接验证两类 view 的 canonical root→child ordering 与 payload | 两次 scoped scan、旧 helper、Result mirror 残留，或 public identity/payload/mixed-scope ordering 改变 |
| S18 | `invocation.py` 只有 `dict[tuple[tuple[str, ...], GraphNodeId], int]` action-count index；`engine/resume_admission.py` 只有 `dict[PublicationAvailabilityCoordinate[GraphValueT], int]` publication-count index；admission 在同一次 canonical enumeration 中收集 duplicate/collision；source AST 不出现 `.count(` 或先 `any(...)` 后再次完整枚举 | 第三个 index、O(n²) count、双扫描、跨 owner generic helper、duplicate/collision 报错顺序或 identity 顺序改变 |

除 7.2.2 已明确采用 public/既有行为/owner gate 加 actual source review 的十一个 target ledger ID 外，其余 `Sxx.a/b/c`
子断言都必须在同一 owner 测试中可单独定位。静态检查发现
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
随后对各 delivery change unit 的 manifest 执行同一种策略：

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
# 从 monorepo root 执行；<unit-files> 是本 delivery change unit 完整的 repo-relative path 列表
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
- monorepo pre-commit、每个 delivery change unit 的 manifest gate，以及 scoped `git diff --check`/`git diff --cached --check` 必须在交付前通过。

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

### 7.14 S10 implementation owner writeback（2026-08-21）

S10 已完成 graph-output availability diagnostic 收敛：`resolve_routing_facts()` 删除两次短路/历史 bool
路径，只执行一次完整 `unavailable_graph_outputs()` 扫描并保存 canonical tuple；`RoutingFacts` 不再存储
`completion_output_available` 或 `completion_output_history_missing`。`project_routing_facts()`、recovery 和
resume admission 按 target work 与 canonical tuple 推导 completion/historical 语义；独立
`graph_outputs_available()` 保留首次缺失短路，继续服务 completed continuation、nested boundary 和 invocation
validation，不形成第二 resolver。

本次 S10 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/src/mote_kernel/execution/engine/resume_admission.py
mote-kernel/docs/skip-failed-output-implementation.zh-CN.md
```

本次没有修改 State/State tests、公共 API、protocol、持久化或 commit/install 时序；没有新增 helper、DTO、cache、
alias、兼容路径或 legacy/private-shape AST test。既有 output projection、historical recovery、pure-skip、
completed-continuation 和 malformed-selection 行为继续由原有 tests 覆盖；actual source review 确认 resolver 内
`graph_outputs_available` 调用数为 0、`unavailable_graph_outputs` 调用数为 1，两个旧 completion bool 的活跃
consumer 数为 0。

scoped 验证结果为：受影响 routing、output、resume admission、recovery、runtime boundary、pure-skip 与 completed
continuation 用例 113 passed；architecture gate 53 passed；排除独立 complexity gate 的完整套件 817 passed、
coverage 100%；Ruff、格式检查与严格 Pyright 通过，build 与 Twine 通过。独立 complexity ratchet 未出现 regression，只报告尚未锁入
该独立 unit 的改进：top-level definitions `511 → 508`、type definitions `293 → 292`、dataclass types
`182 → 181`、dataclass fields `526 → 519`、decision points `1350 → 1342`、thin single-use helpers `18 → 17`。
完整工作树仍混有独立 complexity framework change unit，因此完整门禁不能作为 S10 的零负债证据；S10 不修改
该独立 unit 的 limits、tests 或 hook 配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.15 S11 implementation owner writeback（2026-08-21）

S11 已完成 target input facts 收敛：删除 `RequiredTarget.inputs_available`、`unavailable_target_inputs()` 和
`_target_has_historical_gap()`；现有 resolver-local `required()` 在一个 binding loop 中同时生成 ordered
`unavailable_inputs` 与 `historical_inputs_missing`。一次 resolver invocation 只维护一个
`dict[GraphNodeId, RequiredTarget]`，按 control → completed join → data 的既有 group 构造顺序首次填充，同一
target 被 direct/join/data 多种 contribution 命中时直接复用相同 immutable fact。

本次 S11 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/src/mote_kernel/execution/engine/resume_admission.py
mote-kernel/docs/skip-failed-output-implementation.zh-CN.md
```

本次没有修改 tests、State/State tests、公共 API、protocol、持久化或 commit/install 时序；没有新增 top-level
helper、DTO、property、跨 invocation cache、display identity、alias、兼容路径或 legacy/private-shape AST test。
routing、recovery 与 resume admission 只以 `bool(target.unavailable_inputs)` 判断缺失，并继续保持 binding declaration
顺序、control → completed join → data 的 target 首次访问顺序、首个 unavailable identity 与原错误文本。

scoped 验证结果为：routing、resume admission、historical recovery 与 runtime boundary 用例 98 passed；排除独立
`tests/architecture/test_complexity_gate.py` 后的完整行为套件 817 passed、coverage 100%；Ruff、格式检查、严格
Pyright、build、Twine 与 `git diff --check` 通过。monorepo exact-file pre-commit 的所有适用 hook 通过，按独立
change-unit 边界跳过 `kernel-complexity`。完整 `make check` 只在该独立 ratchet 中断：6 passed、1 failed，失败原因
不是 regression，而是要求把尚未锁入该独立 unit 的改进写回 limits：top-level definitions `511 → 506`、type
definitions `293 → 292`、dataclass types `182 → 181`、dataclass fields `526 → 518`、decision points
`1350 → 1339`、logical clone pairs `12 → 11`、thin single-use helpers `18 → 17`。完整工作树仍混有独立
complexity framework change unit，因此完整门禁不能作为 S11 的零负债证据；S11 不修改该独立 unit 的 limits、
tests 或 hook 配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.16 S17 implementation owner writeback（2026-08-21）

S17 已完成 resume candidate 镜像事实收敛：`ScopedResumeCandidate` 删除 `skip_actions` 与 `has_pure_skip`，只保留
`graph/scope_run/previous/successor/substitutions/command` 六个字段；`invocation.plan_resumes()` 不再从 request 与
prepared command 预计算这两个事实。`admit_resume_candidates()` 在 exact reducer successor 验证后，每个 candidate
只从 `command.actions` 派生一次 ordered `SkipFailedNode` tuple，并在 substitution evidence、诊断和 future proof 中
复用。

pure skip 不再由 bool 表示。admission 用 scope、superstep、node 与 descriptor identity 完整构造 skip action 的
`PublicationAvailabilityCoordinate` 集合，再减去 candidate substitution coordinates；只有 typed 差集非空，且既有
“无 control/data/remaining-join work、graph output 仍 unavailable”条件同时成立时，才执行原有 fail-closed 分支。
因此 mixed pure skip + replacement、全 substitution、跨 scope 与 repeated-superstep identity 均继续由同一坐标
语义区分。

本次 S17 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/resume_admission.py
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/engine/test_resume_admission.py
mote-kernel/tests/execution/engine/test_runtime_boundaries.py
mote-kernel/docs/skip-failed-output-implementation.zh-CN.md
```

本次没有修改 State/State tests、公共 API、reducer、protocol、持久化、commit/install 时序或 recovery traversal；
没有新增 helper、DTO、property、cache field、alias、兼容路径或 legacy/private-shape AST test。旧
`test_resume_admission_rejects_skip_facts_not_bound_to_the_exact_command` 只允许伪造现已删除的镜像字段，随非法状态
一起删除；exact-command successor、substitution evidence、pure-skip historical loss 与零 commit 行为测试继续保留。

scoped 验证结果为：resume admission、runtime boundary 与 public Graph API 用例 129 passed；排除独立
`tests/architecture/test_complexity_gate.py` 后的完整行为套件 816 passed、coverage 100%；Ruff、格式检查与严格
Pyright、build、Twine 与 `git diff --check` 通过。monorepo exact-file pre-commit 的所有适用 hook 通过，按独立
change-unit 边界跳过 `kernel-complexity`。完整 `make check` 只在该独立 ratchet 中断：6 passed、1 failed，失败原因
不是 regression，而是要求把改善写回独立 limits。S17 相对 S11 后 shape 再减少 2 个 dataclass fields 与 2 个
decision points；当前 report 相对未更新 limits 为 dataclass fields `526 → 516`、decision points `1350 → 1337`，
其余尚未锁入改善保持 top-level definitions `511 → 506`、type definitions `293 → 292`、dataclass types
`182 → 181`、logical clone pairs `12 → 11`、thin single-use helpers `18 → 17`。该独立 ratchet/health unit 不属于
S17，本单元不修改其 limits、tests 或 hook 配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.17 S20 implementation owner writeback（2026-08-21）

S20 已完成 failed-retry materialization 收敛。`engine.resume_input.materialize_node_input()` 仍是唯一 owner，唯一
新增面是 keyword-only `failed_retry_input: UseStepRequestInput | None = None`。默认 `None` 继续只接受当前
`PendingGraphNode` 并读取其 State-owned input；显式 nominal value 只接受当前 `FailedGraphNode`，把该 value 作为
本次 effective input。两种合法组合由同一 nominal match 选择，scope/run/node、codec、descriptor 与 availability
校验顺序不变，`OverrideGraphNodeInput` 继续只走既有 codec decode 路径。

`GraphExecutor.resume()` 的 default failed retry 现在把同一个 `UseStepRequestInput` 同时交给 command projection 与
materializer，不再为 materialization 临时 `replace(state, frontier=...)`，也不再构造 replacement
`GraphFrontierState` 或重复枚举 frontier。resume 全部 actions 处理完成后的唯一 `simulated = GraphFrontierState(...)`
与紧随其后的 `validate_graph_frontier(state, simulated)` 各保留一次，仍是最终 admission safety boundary。

本次 S20 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/tests/execution/engine/test_resume_input_contract.py
```

本次没有修改 State/State tests、公共 API、command/reducer、protocol、持久化、commit/install 时序或 recovery；
没有新增 wrapper、helper、DTO、wide union、alias、兼容路径或 legacy/private-shape AST test。新增的
`test_failed_retry_materialization_requires_a_current_failed_node` 只验证 runtime nominal guard；既有 executor
override/default/skip/interrupt、scope、missing graph input/publication 与最终 frontier validation 行为继续保留。

scoped 验证结果为：resume-input contract 与 executor 用例 51 passed；排除独立
`tests/architecture/test_complexity_gate.py` 后的完整行为套件 817 passed、coverage 100%；Ruff、格式检查与严格
Pyright、build、Twine 与 `git diff --check` 通过。monorepo exact-file pre-commit 的所有适用 hook 通过，按独立
change-unit 边界跳过 `kernel-complexity`。完整 `make check` 只在该独立 ratchet 中断：6 passed、1 failed，失败原因
不是 regression，而是要求把改善写回独立 limits。S20 相对 S17 后删除一对 materialization-only State/frontier
projection，production decision points `1337 → 1335`；当前 report 相对未更新 limits 为 decision points
`1350 → 1335`，其余尚未锁入改善保持 top-level definitions `511 → 506`、type definitions `293 → 292`、
dataclass types `182 → 181`、dataclass fields `526 → 516`、logical clone pairs `12 → 11`、thin single-use helpers
`18 → 17`。该独立 ratchet/health unit 不属于 S20，本单元不修改其 limits、tests 或 hook 配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.18 S23B implementation owner writeback（2026-08-21）

S23B 已完成 awaiting Result projection 收敛。`family_driver._project_result_views()` 现在是唯一 owner：它只调用
一次 `_scoped_states()`，按既有 root→canonical child scope 顺序和各 frontier 的既有 node 顺序遍历，在同一次
traversal 中分别填充 `list[GraphFailureView]` 与 `list[GraphInterruptView]`，最后返回两个 typed tuple。旧
`_failure_views()`、`_interrupt_views()` 及第二次 scoped-state full pass 已删除；`project_graph_result()` 只在
awaiting branch 调用该 owner，completed/aborted branch、`_awaiting_result()` seal 与 public Result shape 均未改变。

新增 public behavior case 在同一个 awaiting Result 中同时放置 root failure/interrupt 与按 `right → left` 注册的
两个 child graph，并直接断言 failure/interrupt views 均按 canonical `root → left → right` scope 输出，同时保持
failure 文本与 interrupt payload。既有 failure resume case 继续冻结
node ordering，既有 interrupt resume case 继续用投影出的 stable interrupt ID 完成 exact resume 并让 stale ID fail
closed。本次没有新增 architecture/private-shape/AST test，也没有把旧 helper 名称或具体循环表达式固化为 legacy
contract。反向 mutation 把 scoped traversal 临时改为 `children → root` 后，该 public behavior case 在第一个
failure view 即失败；恢复 `root → children` 后通过，证明门禁能够杀死本次复审指出的顺序回归。

本次 S23B implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/family_driver.py
mote-kernel/tests/execution/test_graph_api.py
```

本次没有修改 State/State tests、公共 Result API、execution loop、command/reducer、commit/install 时序、recovery、
protocol 或持久化；没有新增 Result mirror、DTO、cache、wrapper、alias、兼容路径或第二 projection owner。scoped
Graph API/nested/recovery/interrupt/resource 用例为 149 passed；排除独立
`tests/architecture/test_complexity_gate.py` 后的完整行为套件为 818 passed、coverage 100%；Ruff、格式检查、严格
Pyright、build、Twine 与 `git diff --check` 通过。

S23B 相对 S20 后把 scoped-state full pass `2 → 1`、private result projection helper `2 → 1`，production
top-level definitions `506 → 505`、decision points `1335 → 1334`；dataclass fields `516`、logical clone pairs
`11` 均未增加。完整 `make check` 仍只会被独立 complexity ratchet 的“改善未写回 limits”中断；当前 health
仍有 logical clone `11`、record-shape clone `21`、thin single-use helper `17` 等既有全仓项，不能冒充 S23B
回归，也不能作为“全仓零负债”的证明。该独立 complexity unit 不属于 S23B，本单元不修改其 limits、tests、
hook 或配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.19 S03 implementation owner writeback（2026-08-21）

S03 已完成 compiled node classification 镜像收敛。`FrontierTransitionPlan` 不再存储
`callable_node_ids`/`nested_node_ids`；compiler 同步删除两个 classification tuple producer 与 constructor
实参。frontier 和 recovery 现在只用 `task.node_id in graph.nested_graphs` 判定 nested activation，frontier 和
resource admission 只用 `CompiledGraph.nodes` 中的 `CallableNodeDefinition` nominal variant 判定 ordinary
callable。`prepare_frontier()` 的 parent lookup 直接保存 canonical nested task，再以同一 `task.node_id` 读取 child
graph，删除了对已经由 `nested_graphs` key 证明过的 nested definition 的第二次 guard 与 definition mirror。

既有 compiled-lowering architecture owner case 只从 exact field set 删除两个旧 expected field，没有新增 compiler
producer、consumer、private symbol、import/call 或具体表达式形状的 legacy AST 断言。actual source review 确认
active production/tests 中两个旧 field symbol、两个 compiler tuple producer 与全部旧 projection 读取均为 0；
nested facade、nested compile mismatch、frontier projection、resource admission 和 nested recovery 继续由既有行为
case 直接覆盖。

本次 S03 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/graph/topology.py
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/src/mote_kernel/execution/engine/frontier.py
mote-kernel/src/mote_kernel/execution/engine/admission.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/tests/architecture/test_graph_execution_ownership.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

本次没有修改 State/State tests、公共 API、Graph facade、command/reducer、recovery semantics、protocol、持久化或
commit/install 时序；没有新增 field、scan、helper、cache、DTO、alias、compatibility path 或第二 classification
owner。scoped 行为/owner/negative gate 为 16 passed；Ruff、格式检查、严格 Pyright、exact-file pre-commit 与
`git diff --check` 通过。按用户明确约束未运行全量 pytest 或 `make check`，因此不把 scoped 结果冒充完整门禁。

S03 的 mirrored classification field `2 → 0`、compiler classification tuple `2 → 0`、新增 scan `0 → 0`，批准
账本的结构净变化为 `Δ=-4`；production dataclass fields `516 → 514`，top-level definitions `505` 不变。独立
complexity ratchet/health unit 仍不属于 S03，本单元不修改其 limits、tests、hook 或配置，也不把当前全仓 health
冒充“全仓零负债”证明。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.20 S04 implementation owner writeback（2026-08-22）

S04 已完成 publication/outcome 编译计划收敛。`graph/ports.py` 删除 `OutcomeAdmissionPlan`、`PublicationPlan` 和
`MaterializationPlan.node_id`；`FrontierTransitionPlan` 删除 `outcomes`，其 `publications` map 直接保存
`FrameDescriptor[GraphValueT]`，`CompiledGraph.outcomes`/`CompiledGraph.publications` projection 同步删除。
compiler 只生成 keyed publication descriptor map；scheduler 通过 routing owner 的既有
`validate_routing_contribution()` 校验 `ContinueGraphRouting`/`SelectGraphRoute`，不直接解释
`conditional_targets`；recovery 使用从 routing owner 迁移的既有 `_success_routes()`，因此 conditional route 的直接
解释仍只有 `routing.py`。frontier、admission、routing、recovery、resume、executor、family driver 与 invocation
全部直接读取 `graph.transition.publications[node_id]`，不再经过 wrapper 或 `.descriptor` 二次访问。

既有 compiled-lowering architecture owner case 只删除 `outcomes` expected 并收窄 publication value expected，
没有新增 helper（`_success_routes()` 只是从 recovery 原位迁移到 routing owner），也没有新增 DTO、producer/consumer/private-symbol
或具体表达式形状的 legacy AST 断言。actual source review 确认
两个旧 DTO、`MaterializationPlan.node_id`、旧 `CompiledGraph` projection、旧 graph publication/outcome 读取与
`.descriptor` 二次访问均为 0；既有 publication/output/recovery/nested 行为保持通过。

本次 S04 implementation change unit 的 exact repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/graph/ports.py
mote-kernel/src/mote_kernel/execution/graph/topology.py
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/src/mote_kernel/execution/engine/scheduler.py
mote-kernel/src/mote_kernel/execution/engine/frontier.py
mote-kernel/src/mote_kernel/execution/engine/admission.py
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/engine/resume_admission.py
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/src/mote_kernel/execution/family_driver.py
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/architecture/test_graph_execution_ownership.py
mote-kernel/tests/execution/engine/test_recovery_identity.py
mote-kernel/tests/execution/engine/test_resume_admission.py
mote-kernel/tests/execution/engine/test_runtime_boundaries.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

本次没有修改 State/State tests、公共 API、command/reducer、recovery semantics、protocol、持久化或 commit/install
时序；没有新增 field、helper、cache、DTO、alias、compatibility path 或第二 publication/outcome owner。指定的
compiler/routing/output/resume/recovery/executor/continuation/graph-api 与 architecture owner 定向套件共 296 passed；
Ruff、格式检查、严格 Pyright 与 `git diff --check` 通过。按用户约束未运行全量 pytest 或 `make check`；独立
complexity hook 不属于 S04，未纳入 manifest，也未修改其 limits/configuration。

S04 的 outcome/publication DTO `2 → 0`、keyed-plan `node_id` fields `3 → 0`、CompiledGraph projection `2 → 0`，
批准账本结构净变化为 `Δ=-7`；publication map value 收窄为 descriptor，所有 consumer 访问 hop 同时减少一层。
该结构收益不把独立 complexity health 当作全仓零负债证明。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.21 S05+S06 联合 implementation owner writeback（2026-08-22）

2026-08-22 用户明确批准 S05、S06 一起处理。两项因此只形成一个联合原子 delivery change unit：S05、S06
仍分别保留 target ID、行为 evidence 与净删除子账本，但 production、tests、normative 同步、T0、actual manifest、
提交和回滚边界均不可拆分。

S05 子账本已完成 graph-input declaration owner 收敛。`CompiledGraph.graph_inputs` 已删除；compiler 的 nested
boundary 比较与 runtime `admit_graph_input()`/`admit_child_graph_input()` 均直接读取
`graph_input_descriptor.declarations`，没有新增 property、alias、adapter 或第二 declaration source。nested
boundary mismatch、root/child graph-input admission、graph-output passthrough 与 recovery frame identity 行为保持
通过，actual source review 确认旧 field 读取为 0。

S06 子账本已完成 compiled transition lowering 收敛。`RecoveryAvailabilityPlan`、`CompiledGraph.recovery`、
transition property 以及 `entries`、`materializations`、`graph_outputs`、`resource_order` forwarding properties 均已
删除；`CompiledGraph.transition: FrontierTransitionPlan[GraphValueT]` 现在是唯一 direct dataclass field。graph-run
start、resume materialization、routing/output projection、resource admission、snapshot guard、executor 与
continuation validation 全部直接读取 `graph.transition.*`，actual source review 确认旧 wrapper、旧 convenience
reads 和 production `RecoveryAvailabilityPlan` import 均为 0。

既有 `tests/architecture/test_graph_execution_ownership.py::test_frontier_transition_plan_is_the_single_compiled_execution_lowering`
现在在同一个 grouped case 中断言 `CompiledGraph` 的完整字段集合，并要求 class body 只含 annotated direct fields；
因此 `graph_inputs`/`recovery` field、forwarding property/method 或 assignment alias 任一回归都会失败。这里没有新增
test case，也没有为已删除 private symbol、consumer 或具体 comprehension 增加 legacy AST 断言。

本次 S05+S06 联合 implementation change unit 的 actual repo-relative manifest 为以下 16 个路径：

```text
mote-kernel/src/mote_kernel/execution/graph/topology.py
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/src/mote_kernel/execution/graph_run.py
mote-kernel/src/mote_kernel/execution/engine/admission.py
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/src/mote_kernel/execution/engine/snapshot_guard.py
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/architecture/test_graph_execution_ownership.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/engine/test_output_projection.py
mote-kernel/tests/execution/engine/test_recovery_identity.py
mote-kernel/tests/execution/engine/test_resume_input_contract.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

联合单元没有修改 State/State tests、公共 API、command/reducer、recovery semantics、protocol、持久化或
commit/install 时序；没有新增 wrapper、property、helper、cache、DTO、alias、兼容路径、第二 declaration owner 或
第二 lowering owner。S05 的 graph-input mirror field `1 → 0`、alias/property `0 → 0`，子账本 `Δ=-1`；S06 的
wrapper `1 → 0`、`recovery` storage field `1 → 0`、forwarding properties `5 → 0`、direct transition field
`0 → 1`，子账本 `Δ=-6`；联合结构净变化为 `Δ=-7`。

production targeted suite 共 331 passed；完整 exact-shape gate 落地后，联合 manifest 涉及的 6 个 test files 又
定向复跑 89 passed。Ruff、目标文件严格 Pyright、exact-file pre-commit（按联合 change-unit 边界跳过
`kernel-complexity`）与 `git diff --check` 通过；完整 pytest/`make check` 按用户约束未运行。独立 complexity unit
不属于 S05+S06，本联合单元不修改其 limits、tests、hook 或配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.22 S14 implementation owner writeback（2026-08-22）

S14 已完成 nested recovery outcome 的 boundary-owner 收敛。`_NestedOutcome` 从
`node_id/kind/availability/disposition/boundary` 五字段收窄为 `node_id/boundary` 两字段；planning、settlement、
execution-limit 与 awaiting-resume consumer 全部直接读取 `outcome.boundary.kind/availability/control`。旧
state-based `_child_control()` 与失去复用价值的单调用 `_child_disposition()` adapter 已删除；唯一
`_child_disposition_from_control(control: ScopeControlStateCoordinate)`
先拒绝缺失 parent 的 control，再逐字段构造 `ChildControlStateCoordinate`；nested outcome 不读取
`boundary.state` 重建 identity。completed child 增加 boundary availability 时复用既有 boundary kind/control，
并直接使用 `boundary.control.scope_run`，同步删除可由 control 推导的 `coordinate` 参数；concrete State 仍只保留在
`_ScopeBoundary.state` 的 `compare=False` simulation payload 中。

本次没有新增或修改测试文件。既有
`tests/execution/engine/test_recovery_identity.py::test_recovery_preflight_projects_existing_terminal_children`、
`::test_recovery_preflight_propagates_an_awaiting_child_boundary`、
`::test_recovery_preflight_linearizes_completed_and_aborted_child_possibilities`、
`::test_recovery_preflight_rejects_completed_child_without_output_history` 与
`::test_recovery_preflight_rejects_each_malformed_child_control_binding` 继续直接冻结 completed/aborted、awaiting、
mixed outcome、missing history 与 malformed control 行为。两字段 private shape、旧 consumer/helper 归零只由本次
actual diff/source review 闭合，不新增 legacy/private-shape AST test。

本次 S14 implementation change unit 的 actual repo-relative manifest 为：

```text
mote-kernel/src/mote_kernel/execution/engine/recovery.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

S14 没有修改 State/State tests、公共 API、command/reducer、recovery semantics、protocol、持久化或 commit/install
时序；没有新增 field、DTO、cache、alias、compatibility path、第二 child projection 或第二 recovery owner。
`_NestedOutcome` fields `5 → 2`、state-derived nested identity path `1 → 0`，新 control projection helper 替换旧
state-based helper，同时删除单调用 optional-state adapter，相关 helper definitions `2 → 1`；批准字段账本净变化
`Δ=-3`，实际 helper surface 额外 `Δ=-1`，completed-outcome 派生参数/调用实参再减少 `1 → 0`。

`test_recovery_identity.py` 与既有 recovery architecture owner case 定向复跑共 19 passed；Ruff、目标文件严格
Pyright、exact-file pre-commit（按独立 change-unit 边界跳过 `kernel-complexity`）与 `git diff --check` 通过。完整
pytest/`make check` 按用户约束未运行。独立 complexity unit 不属于 S14，本单元不修改其 limits、tests、hook 或
配置。

本节 owner writeback 自身的独立 manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

### 7.23 S07 implementation owner writeback（2026-08-23）

S07 的批准来源已固定为本对话中的用户原文“补上吧，我是批准的”，并由 approval commit
`8b6785e docs(kernel): approve S07 coordinate ownership` 记录；该提交晚于 design commit
`a0ee588 docs(kernel): design S07 coordinate ownership`，早于 implementation commit
`dd15084 refactor(kernel): centralize S07 availability coordinates`。因此 Git 历史能够证明 design → approval →
implementation 的先后关系，不再把批准追认到 production diff 之后。

implementation commit `dd15084` 将 GraphInput、NodeOutput binding 和 resume-input 的重复 coordinate assembly
收敛到三个 source-specific typed owner：`engine/routing.py` 的 `_graph_input_coordinate()` 与
`_node_output_coordinate()`，以及 `engine/resume_input.py` 的 `_resume_input_coordinate()`。调用边界继续持有
selection/error validation；没有改变公共 API、State/command、recovery semantics、protocol、持久化或
commit/install 时序；没有新增字段、DTO、cache、alias、compatibility path、第二 frame store 或 generic adapter。

本次 implementation change unit 的 actual repo-relative manifest 只有：

```text
mote-kernel/src/mote_kernel/execution/engine/admission.py
mote-kernel/src/mote_kernel/execution/engine/resume_input.py
mote-kernel/src/mote_kernel/execution/engine/routing.py
mote-kernel/docs/graph-node-input-output-contract-implementation.zh-CN.md
```

approval unit 的 manifest 只有 requirements 文件；本 owner-writeback unit 的 manifest 只有本文：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

三个 manifest 互不重叠，也没有把当前工作树中 unrelated README、Makefile、pyproject、pre-commit、example 或
complexity unit 纳入 S07。未新增 legacy/private-shape AST test；复用的三个行为 characterization 定向测试为
`3 passed`。Ruff、目标 production 文件严格 Pyright、exact-file monorepo pre-commit（`kernel-complexity` 按范围跳过）
和 scoped `git diff --check` 均通过；完整 pytest/`make check` 未运行，符合 WSL 约束。

### 7.24 S01 implementation owner writeback（2026-08-23）

S01 已按 3.1.2 完成 compiler invocation 内重复事实与无语义转交的收敛。Git 历史顺序为 design/review
`d34c117` → requirements-only approval `785c796` → production + behavior implementation
`0f34aa2 refactor(kernel): simplify graph compiler facts`；因此批准、实现和本次 owner writeback 没有互相追认。

实际 production 结果如下：`ActivationGate` 直接拥有 route-aware `(source, route)` shape，`activation_gates` 是
non-terminal activation 的唯一事实；`_all_single_source_gates()` 是唯一新增且被 controlled-producer proof 与
input-publication selection 两个 production consumer 复用的窄 predicate。`_guaranteed_sets()` 继续消费完整
gate source，joint-activation proof 继续保留 route identity；`direct_targets` 直接承担 non-END membership，duplicate
data/direct 检查不再维护 `direct_pairs`。`control_gates`、`direct_pairs`、`RouteCause` 以及
`input_descriptor`、`output_descriptor`、`resource_order` 三项转交局部 alias 均已归零；`node_outputs` 保持完整 generic
invocation-local `dict`，最终只在
`FrontierTransitionPlan`/`CompiledGraph` representation 边界 freeze。public Graph、CompiledGraph/transition shape、
State、command、reducer、protocol、Store/no-Store、callback 与 runtime/recovery owner 均未改变。

新增/补强的既有 public behavior cases 已全部通过：

- `test_compiler.py` 的 direct target public index 断言为 `("b", "c")`；
- `test_compiler_contract.py::test_compiler_uses_relative_selection_for_same_source_conditional_routes` 验证同一
  self-loop source 的两个 conditional routes 指向同一 target 时仍为 `PublicationSelectionKind.RELATIVE / 1`；
- `test_nested_graph.py::test_nested_compilation_preserves_definition_order_error_priority` 以两个 validation 可通过、
  compile phase 分别失败的 child 验证 `definition.nodes` 原始顺序决定首错类型与文本。

本次 implementation change unit 的 actual repo-relative manifest 只有：

```text
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/graph/test_nested_graph.py
```

限定验证结果为：graph compiler/contract/nested/join/topology 套件 `56 passed`；resume-input/runtime boundary 套件
`15 passed`；generic/source/dependency/ownership architecture 套件 `37 passed`；四文件 Ruff check 与 format check
通过；严格 Pyright `0 errors, 0 warnings, 0 informations`；四文件 exact-file monorepo pre-commit 的所有适用 hook
通过，按批准边界跳过 `kernel-complexity`；implementation commit `git show --check` 通过。按用户的 WSL 约束未运行
全量 pytest、coverage、`make check`、build 或 Twine；这些未运行项目不冒记为 S01 证据。

本次没有新增 legacy、AST、private-source-shape 或测试 helper；没有修改 State/State tests、normative Node I/O、
protocol、README、Makefile、`pyproject.toml`、pre-commit 配置或 complexity framework。当前工作树中的 unrelated
complexity/治理 dirty files 不属于 S01 manifest；独立 complexity health 也不作为本单元“全仓零负债”的证明。

本节 owner writeback unit 的独立 manifest 只有：

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

Phase 0 和各 delivery change unit 中仍必须完成：

1. 维护唯一 requirements 文件 `docs/graph-semantics-preserving-simplification-requirements.zh-CN.md` 及 `README.zh-CN.md`、`README.md` 的稳定链接/owner 导航；requirements 不复制具体 target shape，README 不枚举动态增长的 review 列表；requirements 独立拥有准入状态，本文只提交 evidence；
2. 本文是 target shape、原子迁移账本、实施顺序、复杂度账本和 characterization 计划的唯一 owner，不创建第二份 target-shape proposal；各轮 review/response 只记录裁决、异议和整改，不拥有 requirements、当前行为或目标 shape；
3. S03–S07、S09–S12、S14、S17 所在的 delivery change unit 必须同时修订对应 frozen internal shape 的 normative implementation；S05+S06 在同一联合单元中同步一次。S09/S10/S11/S17 必须同步 `skip-failed-output-implementation.zh-CN.md`，S03/S04/S05/S06/S07/S12/S14 必须同步 `graph-node-input-output-contract-implementation.zh-CN.md`；不得先形成 production-only 或 docs-only 的长期中间状态；
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
| 当前 Node I/O shape | Node I/O normative source | `docs/graph-node-input-output-contract-implementation.zh-CN.md` | 已随 S05+S06 联合单元同步 graph-input canonical descriptor/direct compiled transition，随 S14 同步 boundary-owned nested outcome/control projection，并随 S07 同步三类 nominal-source coordinate constructor owner |
| 当前 skip-output shape | skip-output normative source | `docs/skip-failed-output-implementation.zh-CN.md` | 已随 S09 同步唯一 facts → command projection、随 S10 同步 canonical output diagnostic/单次 full scan、随 S11 同步 target 单次 typed scan/三字段 fact/invocation-local cache，并随 S17 同步六字段 candidate 与 typed pure-skip coordinate 差集 |
| 文档导航 | package README | `README.zh-CN.md`、`README.md` | Phase 0 已加入稳定文档链接和 owner 关系；不复制正文或枚举 review 历史 |
| 评审裁决/回复 | review record | 本文第 1 节“关联记录”逐条列出 exact path，包括第五次复审、第五次回复和 requirements 再次复审 | 只记录裁决、接受/不接受理由和验证，不拥有 requirements、当前行为或 target shape |
| 第六至第十次复审 | review record | `docs/graph-semantics-preserving-simplification-implementation-sixth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-seventh-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-eighth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-ninth-review.zh-CN.md`、`docs/graph-semantics-preserving-simplification-implementation-tenth-review.zh-CN.md` | 只记录 R1–R16/C1–C2 审计结论；不拥有 target shape、State 或准入批准 |
| Execution / State / Frontier 调用链 | non-normative draft / 独立整改项 | `docs/execution-state-frontier-call-chain.zh-CN.md` | 只作导航草稿；不是 State/commit/recovery/持久化事实源或 A05 blocker，不进入稳定 README normative 导航 |
| 历史调研 | history record | `example/graph-two-commits-simplification-review.zh-CN.md` | 只供溯源，不是 normative 或实施事实源 |

requirements、稳定 README 导航、本文 target 设计和 per-change manifest 规则均已形成。requirements 第 7 节
只对当前 15 个 P1 显式批准 `GSP-A05`；`GSP-A06` 已分别对 S07、S01 单项闭合，其余 7 个 P2 不继承批准。

## 9. 明确排除的方向

本轮非目标由 requirements 的 `GSP-N01`–`GSP-N06` 唯一拥有，见
[requirements 第 4 节](graph-semantics-preserving-simplification-requirements.zh-CN.md#4-非目标)。
第 4 节另列的是可能具有独立价值、但不属于当前 24 个 target ledger ID 的后续重构候选；两者都不得混入本轮实施。
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

本次审查保留 23 个历史 ID，并把 S23 拆成两个 target ledger ID，共得到 24 个 target ledger ID：15 个 P1、
9 个 P2（S12 保持 P2）。S05+S06 按明确授权合并为一个 delivery change unit，因此交付边界共 23 个：14 个 P1、
9 个 P2。15 个 P1 target 的范围、owner、删除对象、最多新增面、before→after 计数和 exact target 均已唯一化，
目标 shape 已按实施方案固定。S08 已完成 production、既有 owner gate 收窄和独立 owner writeback；S09–S11、
S14、S17、S20、S23B、S03、S04、S05+S06 联合单元以及 S01 已完成 production、public/既有行为/owner gate、一次性 source
review、所需 normative 同步和对应 owner writeback。当前工作树仍混有未独立
审核的 complexity unit，完整门禁未绿，因此这些 change unit 都不能记为零负债整体交付；S13、S18、S23A 已分别完成
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
迁移既有行为测试并以 source review 闭合，不新增 legacy AST 断言；S10 又删除 output completion 镜像 bool、
合并 full diagnostic scan，并以既有行为测试/source review 闭合；S11 进一步删除 target availability 镜像 bool、
双 binding scan 和重叠 target 重算，以 invocation-local typed cache 收敛；S17 删除 resume candidate 的
`skip_actions`/`has_pure_skip` 镜像及 producer 双算，改由 exact command 与 substitutions 的 typed coordinate 差集
派生；S20 删除 failed-retry materialization-only replacement State/frontier，以唯一窄 keyword 复用现有
materializer，同时保留最终 simulated frontier validation；S23B 再把 failure/interrupt Result projection 合并为
一次 canonical scoped-state scan，并以 public mixed root/child-scope case 冻结两类 view 顺序；S03 随后删除
transition 中的两个 node-classification 镜像及 compiler producer，让 nested/callable 分类只消费 canonical graph
maps/nominal definitions；S04 随后删除 outcome/publication DTO 与 compiled projection，让所有 publication consumer
直接读取 canonical descriptor map；S05+S06 联合单元同时删除 graph-input mirror、recovery wrapper 与全部
CompiledGraph convenience projection，让 direct transition 成为唯一 lowering owner；S14 最后删除 nested outcome
的 kind/availability/disposition 镜像与 state-based child-control projection，让 boundary 成为唯一 outcome owner。
上述单元均不新增 legacy AST 断言，scoped gate 已通过，但混合工作树的独立 complexity hook 阻断完整交付。
15 个已批准 P1 的 production target 至此全部落地；S07 随后在用户明确批准后首个完成 P2 单项 `GSP-A06`
设计、production、既有 behavior/owner gate 与 normative 同步，三类目标 source 的重复 coordinate assembly 已归零。
S01 随后按 3.1.2 完成 implementation commit `0f34aa2`、四文件 actual manifest、behavior/source evidence 与 owner
writeback；其余 7 个未获批 P2 继续逐项受 `GSP-A06` 约束，State/no-persistence HARD KEEP 保持不变。

### 11.1 Requirements owner 已接受的实施证据

| 条件 | 本文提交的可核对 evidence | 位置 |
| --- | --- | --- |
| `GSP-A01` | requirements/implementation/review 分工明确；本轮最小 source precedence、State HARD KEEP 与 non-normative 调用链边界固定 | 1.2、2.4、5.1–5.3、8.1 |
| `GSP-A02` | 24 个 execution-only target ledger ID 映射到 23 个 delivery change unit（14 个 P1、9 个 P2）；15 个 P1 exact target 无条件式分支，S18/S20/S23B 已收口 | 3、3.6、6.1 |
| `GSP-A03` | 15 个 P1 均映射行为 requirement 和现有成功/失败或边界 case；15 个 T0 均有 exact behavior/owner/source gate、断言和失败条件；S03、S04、S05+S06、S08–S11、S14、S17、S20 与 S23B 复用 public/既有行为/owner gate 与 actual source review、不新增 legacy AST 断言，S20 final simulation、S23A indirect baseline 与 S23B mixed root→child ordering 口径明确 | 7.2.1–7.2.3 |
| `GSP-A04` | actual change unit manifest、owner/review 分离规则、State/no-persistence negative gate 与可复现命令固定 | 7.3–7.8 |
| `GSP-A05` | Phase 0 设计 → 显式批准 → production + target test 原子落地 → T0 PASS 后交付的时序无循环 | 6、7.2.2 |
| `GSP-A06` | S07、S01 已分别完成；S01 按 exact signature/nominal type、删除/新增上限、结构净删除、behavior/source evidence 与四文件 actual manifest 闭合，并由 approval commit `785c796` 授权、implementation commit `0f34aa2` 落地；其余 P2 不继承批准 | 3.1.2、3.2.2、7.23–7.24 |

Phase 0 到此终局闭合，不需要再创建评审轮次证明本轮裁决存在。S03、S04、S05+S06、S08–S11、S14、S17、S20 与
S23B 的 production/scoped gate 已完成，但在独立 complexity unit 与各 implementation manifest 分离并重跑完整
门禁前，不把它们记为零负债整体交付。Phase 1 已按批准顺序完成，Phase 2 的 S03、S04、S05+S06、S14 也已完成；
当前没有剩余的已批准 P1 delivery change unit；S01 这一已批准 P2 也已完成。其余 7 个未获批 P2 仍须逐项满足
`GSP-A06`，不得重新发现第 25 个简化点、提前实施其余 P2、把独立文档治理放回关键路径，或触及 State/持久化。
