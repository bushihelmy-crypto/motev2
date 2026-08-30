# Graph execution 代码简化候选调研

## 1. 文档信息

- 状态：**RESEARCH CATALOG / A-v1 RETIRED / A-v2 REVISED — RE-REVIEW PENDING / CANDIDATE B NOT APPROVED**
- 日期：2026-08-26
- 范围：`src/mote_kernel/execution/**` 的内部逻辑收敛
- 公共入口：`mote_kernel.execution.Graph`
- 执行 owner：现有 execution engine
- 事实源：当前 production、既有 behavior/architecture tests，以及[语义保持型简化实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 术语说明：文件路径沿用历史名称；“高改动半径”只描述风险，实际目标是 execution 代码简化
- 本文性质：候选方向调研，不拥有 requirements、target shape、批准状态或 implementation manifest
- 候选 A-v1 历史处置：[A-v1 历史处置索引](graph-execution-code-simplification-implementation.zh-CN.md)
- 候选 A 当前方案：[A-v2 root state 去包装实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)

本文只记录“改动面很大，但理论上可能减少重复事实和重复链路”的候选。它不是对当前代码的直接授权，也不把
复杂度扫描器发现的相似代码自动升级为重构目标。

以下范围无条件排除：

- `state/**` 的定义、command、reducer、revision 和 protocol；
- State/Store/schema/protocol 演进、持久化、checkpoint、journal、event log；
- failover、retry policy、补偿事务、跨进程 recovery 或第二 runner/interpreter；
- public `Graph` API、Result/Continuation 的既有 shape；
- 通过 `Any`、裸字典、反射、字符串 discriminator、兼容 alias 或隐藏 mutable state 换取表面缩行。

## 2. 结论先行

当前复杂度审核已经把 51 个 structural candidates 逐项审查为 intentional/KEEP；`make complexity` 的结果是
`reviewed=51`、`unreviewed=0`、`stale=0`。因此本文不是“继续清空扫描器数字”的待办列表。

在审计之外，候选 A 已经历一次方向替换；下表区分已退休假设与当前方案：

| 候选 | 可能减少的重复 | 爆炸半径 | 当前裁决 |
| --- | --- | --- | --- |
| `ScopedStateIndex` / `ScopedStateBinding`（A-v1） | 原假设为 root/child、planned、recovery 之间存在重复 lookup/projection/replacement | 很高：跨 `run_context`、`invocation`、`recovery`、`family_driver`、continuation integrity | **已否决并退休；不得重新引入 index** |
| 删除 `_RootStateBinding` 与 `replace_root()`（A-v2） | 删除同一 root state 的 wrap/unwrap、第二 replacement entry 与测试重复分派 | 中：四个 execution consumer、一个既有 test fixture、一个 normative source | **首次评审原则接受技术方向；已整改，待独立复审，未批准** |
| sealed `AdmittedResumePlan` | `PreparedResume`、`_PlannedResume`、`ScopedResumeCandidate` 之间重复的 action/successor/substitution evidence | 中高：跨 executor、invocation、resume admission、frame installation 和 Result/State 边界 | **可以研究，但未批准；与 A-v1/A-v2 相互独立** |

“可简化”在本文中只表示一个待验证假设，不表示字段相似就存在重复事实。A-v1 的 A0 已证伪 index target；A-v2 改为删除
无独立 invariant 的单字段 wrapper，并已给出净删除账本。A-v2 与候选 B 都必须独立评审和批准。

### 2.1 候选计数口径

这里必须区分两个层级，不能把它们混成一个数字：

| 层级 | 独立方向 | 来源 | 当前状态 |
| --- | --- | --- | --- |
| 旧方案领域级 | Compiler、validation 与 compiled topology | 主实施方案 S01–S06 | 已完成/已验证；其 owner 与证明可复用 |
| 旧方案领域级 | Routing、join 与 availability facts | 主实施方案 S07–S11 | 已完成/已验证；其 owner 与证明可复用 |
| 旧方案领域级 | Recovery 与 continuation validation | 主实施方案 S12–S15 | 已完成/已验证；exact algorithm 只在原 owner 内保留 |
| 旧方案领域级 | Invocation、resume admission 与 executor | 主实施方案 S16–S20 | 已完成/已验证；其 owner 与证明可复用 |
| 新增跨领域 | `ScopedStateIndex` / `ScopedStateBinding` | 候选 A-v1 | 已否决并退休 |
| 新增跨领域 | root state 去包装 | 候选 A-v2 | exact proposal 已整改，待独立复审 |
| 新增跨领域 | sealed `AdmittedResumePlan` | 本文候选 B | 未立项，后置独立评估 |

因此，按“旧文档可复用结论 + 历史/当前新增方向”的**研究目录总数是 7 个**；A-v2 等待独立复审与 `GSP-A06` 批准，
候选 B 仍等待首次独立裁决。
A-v1 只作历史溯源，不计入可推进方向。

主实施方案的 S21/S22 是明确 KEEP 的 facade/transaction 边界，S23A/S23B 已完成的局部收敛；它们不应被虚增为第五个待研究的爆炸候选。
复杂度审核中的 51 个 reviewed identities 同样是已审查的结构项，不是额外方向。

主实施方案的四个领域级方向不是待重新实施的任务；它们已经形成可以迁移到两个新候选中的 execution-only 简化模式。本文第 10 节单独回收这些模式，避免把旧 target、旧 manifest 或旧批准状态复制成第二份事实源。新候选虽然跨越旧领域，仍必须分别建立自己的 owner、target 和 evidence，不能因为有重叠就合并成一个候选。

## 3. 何谓“值得立项的代码简化”

一个候选只有同时满足以下条件，才值得进入独立设计：

1. 多个模块确实在表达同一个 canonical fact，而不是仅仅字段形状相同；
2. 可以指定一个现有 owner，不新增第二个解释器或第二个 truth source；
3. 输入/输出可以用窄 nominal type 表达，不依赖宽 union、类型擦除或字符串分派；
4. 能冻结错误优先级、排序、commit/install 顺序和 tamper 行为；
5. 删除的字段、扫描或 projection 明显多于新增的 type、参数、分支和适配层；
6. 改动可以被拆成一个可回滚的 execution-only change unit。

反之，只要候选需要同时修改 State shape、public continuation、concrete frame ownership 和 recovery proof，它就不是普通
复杂度重构，而是新的架构设计。

## 4. 候选 A-v1：`ScopedStateIndex` / `ScopedStateBinding`（历史假设，已否决）

本节保留 A0 前的研究假设用于溯源，不拥有当前 target。候选 A-v2 的证据、复杂度账本与 planned manifest 只见
[候选 A-v2 新实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)。

### 4.1 当前事实分布

A0 前观察到“scope → state”关系在不同生命周期以不同 nominal shape 出现：

| 生命周期 | 当前 owner/type | 语义 |
| --- | --- | --- |
| 已确认 runtime context | `GraphRunContext` 的 `_RootStateBinding` + `ChildStateBinding`（`run_context.py`） | 已确认、可被下一步执行消费的 root/child snapshot |
| invocation planning | `_PlannedState`（`invocation.py`） | fence/resume reducer 得到的进程内候选 successor；尚未确认 |
| recovery admission | `RecoveryStateBinding`（`engine/recovery.py`） | 给 bounded recovery proof 消费的 state coordinate + state |
| continuation | `_CompleteContinuationSnapshot` / `_RecoveredContinuationSnapshot` | 已冻结的 root/child binding 集合及 concrete frame index |

A0 原先怀疑重复最明显的地方不是 record 字段本身，而是以下链路：

- `GraphRunContext.state_at()` 与 `child_state()` 通过 coordinate 查找 state；
- `lineage_states()` 把 root/child context 重新投影为 `_PlannedState`；
- `_planned_state()`、`_replace_planned_state()` 再次查找和替换同一 scope；
- `recovery_seed()` 再把 planned state 投影为 root/children 两组 `RecoveryStateBinding`；
- recovery 继续把 state 转成 coordinate-only control/proof。

完成 exact inventory 后确认：这些链路共享 coordinate identity，但分别拥有 confirmed、planned、boundary snapshot 与 proof
生命周期；它们不是同一 confirmed fact 的重复 storage。原“重复包装”判断因此不能成立为 implementation target。

### 4.2 原 target hypothesis（已拒绝）

研究目标可以限定为：让一个窄的、execution-owned typed index 负责 canonical scope lookup 和 lineage projection，同时保留
不同生命周期的 nominal boundary。目标不是创建一个拥有所有字段的 `ScopedState` 大 DTO。

一个可接受的目标假设至少应满足：

- canonical key 只有既有 `ScopeRunCoordinate`；root coordinate 仍由 `state.run_id` 推导，不能额外存一份 run identity；
- root 与 child 的 scope identity、parent activation 和 replacement 语义仍可被类型系统区分；
- planned successor 是 invocation-local、未确认事实，不能提前写入 `GraphRunContext`、continuation 或 recovery seed；
- recovery 只接触其现有的 typed state/control coordinate，不接触 concrete frame/value；
- lookup/index 不形成第二份 mutable truth，也不复制 `ScopedFrameIndex`；
- 已确认 context 的 `replace_root`、`replace_child`、`replace_state` 时序和异常边界不变；
- continuation 的 complete/recovered variant 仍保持 sealed nominal shape，不改成 optional field 或 boolean discriminator。

### 4.3 为什么爆炸半径很高

| 影响面 | 需要保持的事实 | 误合并的后果 |
| --- | --- | --- |
| root identity | `root_scope_run(state.run_id)` 是唯一推导关系 | 多一份 run identity，root tamper 可能被错误接受 |
| child start | child 只能在 acknowledged start 后进入 context | 把 child 当普通 replacement 会改变事务边界 |
| planned successor | reducer successor 只属于当前 invocation planning | 未确认状态泄漏到 continuation/recovery |
| durable-first/install | commit confirmation 先于 Python snapshot replacement | 顺序倒置，产生错误的 memory handoff 语义 |
| recovery | recovery 是 availability proof，不消费 concrete frame | proof 与 runtime value 混淆，可能引入第二 interpreter |
| error precedence | duplicate、parent mismatch、unknown scope、tamper 各有既定首错 | 统一 index 的校验顺序改变外部错误 |
| continuation | complete/recovered snapshot 是已冻结契约 | 用一个可选字段替代 variant，削弱 nominal boundary |

### 4.4 A0 probe（已完成）

A0 已在没有 production/test edit 的前提下完成以下只读清单：

1. **producer/consumer inventory**：列出所有 state binding 的构造、lookup、replacement、snapshot 和 recovery projection；
2. **lifecycle trace**：逐条记录 `planned → commit confirmation → context replacement → frame installation → continuation` 的调用顺序；
3. **identity matrix**：覆盖 root、child start、existing child replacement、nested parent mismatch、duplicate scope 和 unknown scope；
4. **malformed/error matrix**：记录每个同时非法输入的首个错误类型、文本和 `__cause__` 行为；
5. **nominal target sketch**：明确哪些 variant 保留、哪些字段真正删除，禁止先写 generic container 再寻找语义；
6. **complexity delta**：给出 definition、field、decision、scan、allocation 和 import 的 before→after，而不是只报告代码行数；
7. **behavior characterization**：复用现有 continuation integrity、recovery identity、Graph API 和 nested boundary tests，不新增永久
   private-source-shape gate。

### 4.5 停止条件

出现任一情况立即停止并保持当前实现：

- 需要同时保留旧 index 和新 index 的双写/同步；
- 需要把 root/child/planned/recovery 都塞进一个带 optional/boolean discriminator 的 record；
- 需要把 concrete `GraphRunState` 或 frame 放进 recovery-only proof；
- 无法证明未确认 successor 不会逃逸 invocation；
- `replace_child` 与 `replace_state` 必须共用同一个没有生命周期语义的操作；
- 首错顺序、parent coordinate 校验、commit/install 时序或 continuation shape 改变；
- 新增的 helper、adapter、cache、protocol 或 public export 抵消删除收益。

**A-v1 历史裁决：`CLOSED / KEEP / NO IMPLEMENTATION`。** A0 没有发现可删除的第二 confirmed storage；原 dataclass target
只会增加 type/field/allocation 或 forwarding owner。若以后出现新证据，只能按 closure 中的 reopening 条件另立 proposal。

## 5. 候选 B：sealed `AdmittedResumePlan`

### 5.1 当前三段生命周期

resume 链路目前有三个相似但不等价的 record：

| 阶段 | 当前 type/owner | 能看到的事实 |
| --- | --- | --- |
| executor action-local preparation | `PreparedResume`（`result.py`） | command、concrete admitted inputs、concrete prepared substitutions |
| invocation planning | `_PlannedResume`（`invocation.py`） | scope、精确 reducer successor、prepared payload、admitted substitutions |
| whole-invocation admission | `ScopedResumeCandidate`（`engine/resume_admission.py`） | graph、scope、previous/successor、command、substitutions；用于跨 candidate availability proof |
| candidate overlay | `CandidateFrameAvailability`（`run_context.py`） | confirmed frame index + substitution presence-only overlay |

它们共享 `scope / command / successor / substitutions` 的部分信息，但拥有不同的证据等级：

```text
executor concrete preparation
        ↓
invocation exact successor planning
        ↓
whole-invocation admission
        ↓
post-commit frame installation
```

### 5.2 潜在收敛点

可以研究一个**sealed、分阶段的** `AdmittedResumePlan` 概念，用于减少重复的 action/successor/substitution projection。
但它必须是生命周期状态机或 nominal variants，而不能是一个“所有字段都可选”的宽 DTO。

可接受的目标假设：

- `PreparedResume` 继续由 executor 拥有，concrete frame 不跨越 executor preparation boundary；
- exact successor 仍由 invocation 通过现有 reducer/command 关系确认；
- whole-invocation admission 仍独立校验 duplicate publication、confirmed collision、availability 和 error order；
- `CandidateFrameAvailability` 继续是 presence-only overlay，不被替换成 concrete frame store；
- post-commit `install_confirmed_resume_frames()` 仍是唯一安装 owner，不能在 admission 阶段偷偷写入 context；
- 新 sealed plan 不进入 public API、State、protocol 或 continuation serialization；
- 不新增第二份 action interpretation 或第二个 resume runner。

### 5.3 为什么不能直接合并

直接把三个 record 合成一个类型会带来四个问题：

1. **concrete value 泄漏**：executor 准备出的 `NodeInputFrame`/`NodeOutputFrame` 可能被 admission 或 recovery 过早消费；
2. **evidence 混淆**：prepared、planned、admitted、confirmed 是不同阶段，字段相同不代表证明强度相同；
3. **owner/import 扩张**：`result.py`、`invocation.py`、`resume_admission.py` 和 `run_context.py` 会互相依赖；
4. **seal 反而增复杂度**：为了防止错误阶段构造，必须新增 seal、variant 或 transition methods，净收益可能为负。

### 5.4 进入设计前必须完成的 probe

- 逐字段标记 provenance：哪个字段由 executor 产生、哪个字段由 reducer 产生、哪个字段由 admission 重新验证；
- 画出 concrete frame 的最早可见点和最晚安装点；
- 固定 duplicate/collision/availability 的检查顺序及异常边界；
- 对 override、default retry、interrupt、pure skip、substitution 和 mixed root/child scope 做 exact behavior matrix；
- 计算合并前后的 type/field/import/branch 数，特别检查是否新增宽 union 或 seal protocol；
- 证明计划对象不会绕过 `install_confirmed_resume_frames()` 或 commit confirmation。

### 5.5 停止条件

- 新 plan 必须携带 concrete frame 才能工作；
- prepared 与 admitted 状态只能靠 `None`、bool 或字符串区分；
- 为了合并而移动 reducer successor、commit 或 frame installation 的 owner；
- 需要修改 public Result/Continuation 或 State command；
- 只能通过保留旧 record 作为 compatibility bridge 才能迁移。

**当前裁决：研究候选，暂不接受直接实施。** 候选 B 与 A-v1/A-v2 均保持独立，不继承彼此的 owner、证据或裁决。

## 6. 明确不纳入“爆炸但可简化”的方向

以下方向看似能一次消除更多重复，但语义层次不同，当前明确拒绝：

- 合并 `ScopedFrameIndex`、`CandidateFrameAvailability` 和 `RecoveryAvailabilityCoordinates`：分别是 concrete value store、presence-only
  overlay、coordinate-only proof；
- 建立 generic canonical-index 内核：会引入 wide union、callback/key abstraction 或类型擦除；
- 合并 `ScopeControlStateCoordinate` 与 `ChildControlStateCoordinate`：会丢失 root/child recovery nominal boundary；
- 让 runtime 与 recovery 共用一个大 interpreter：runtime 消费 concrete value，recovery 只做 bounded availability proof；
- 合并 `_ScopeBoundary`、`_RecoveryWorkItem`、`RecoveryTransferState`：terminal result、可变 work item 和 equality/dedup transfer state 生命周期不同；
- 把 complete/recovered continuation snapshot 合成一个 optional/boolean 结构；
- 把本轮复杂度问题转移到 State、Store、持久化或 failover。

这些方向不是“以后一定不能做”的抽象判断，而是说明它们必须另立更高层 architecture requirements，不能借本文候选调研直接落地。
删除单字段 `_RootStateBinding` 不再属于本节：A-v2 已证明它不承担 snapshot variant、family identity 或 child binding invariant，
并由独立实施方案约束为不新增类型、字段、入口或兼容层的原子删除。

## 7. 历史实施顺序与当前边界

候选 A 的实际演进是：

1. **A-v1 probe — COMPLETE / RETIRED**：证明 ScopedStateIndex 没有净删除面，production/tests 不变；
2. **A-v2 design — REVISED / RE-REVIEW PENDING**：删除 `_RootStateBinding` 与 `replace_root()`，形成 exact target、净删除账本、requirements 映射与 behavior/exact-shape evidence；
3. **A-v2 implementation — NOT AUTHORIZED**：独立评审与显式批准前不得修改 production/tests；
4. 其他研究候选只有另立 requirements、owner 与 evidence 才能评估，不继承 A-v1/A-v2 的阶段或裁决；
5. 任一候选不能证明净认知面下降，就保持现状，不以代码行数替代证明。

## 8. 统一验收门槛

任何未来实施单元至少需要提供：

- 唯一 owner 与 exact producer/consumer manifest；
- before→after 的定义、字段、分支、扫描、import 和类型表；
- root/child、planned/confirmed、runtime/recovery 的 nominal boundary 证明；
- 成功、失败、malformed、duplicate、tamper、nested 和 repeated-superstep characterization；
- commit confirmation、memory replacement、frame installation 的调用顺序证据；
- State/Store/protocol/persistence/failover 零改动证明；
- `make complexity` 的 reviewed/unreviewed/stale 结果与 `make complexity-ratchet` 非回退结果；
- 失败时可直接回滚到当前已验证基线，不保留 compatibility bridge 或双路径。

## 9. 当前基线与最终 disposition

当前复杂度基线为：

- `top_level_definitions=504`
- `type_definitions=288`
- `dataclass_types=178`
- `dataclass_fields=500`
- `decision_points=1327`
- `logical_clone_pairs=12`
- `record_shape_clone_pairs=21`
- `thin_single_use_helpers=17`
- `single_use_private_dataclasses=1`
- `test_only_private_definitions=0`
- `reviewed=51`、`unreviewed=0`、`stale=0`

这些数字中的已审查候选不是当前负债；它们是为保持 nominal owner、错误边界和 construction boundary 而保留的结构。
`ScopedStateIndex` 只作为 A-v1 历史方向保留；A-v2 的 exact target 是
`503/287/177/499`，其余六项不增长。sealed `AdmittedResumePlan` 仍只是未批准目录项。

**当前裁决：** A-v1 已否决；A-v2 技术方向已获原则接受并完成首次整改，但仍待独立复审与显式实施批准；当前代码保持不变。

## 10. 主实施方案可复用项回收审计

本节只审计
[`graph-semantics-preserving-simplification-implementation.zh-CN.md`](graph-semantics-preserving-simplification-implementation.zh-CN.md)
中已经写明并落地的建议。这里的“复用”指复用其设计规律或既有 owner/infrastructure，不指复制旧 dataclass、旧 helper、旧
manifest 或旧测试的私有形状。

### 10.1 总体结论

旧方案仍有可复用价值，而且主要集中在“唯一事实源 + 局部 typed derivation + 生命周期边界”三条主线：

| 复用层级 | 主实施方案来源 | 结论 |
| --- | --- | --- |
| 跨模块逻辑模式 | S03–S06、S08–S11、S17、S20、S23B | **可复用**；适合大范围收敛，但必须重新绑定新 owner 和新 behavior evidence |
| owner-local 实现模式 | S01、S02、S07、S13、S14、S16、S18、S19、S23A | **可复用方法**；只能在同一语义 owner 内使用，不能抽成 generic framework |
| 领域证明方法 | S12、S15 | **原则可复用，exact algorithm 不可复制**；必须保留各自 recovery/equality/worklist 边界 |
| facade/transaction 结论 | S21、S22 | **不可复用为抽象 helper**；文档结论就是 KEEP |

因此，旧文档没有留下“直接拿来再改一遍”的未完成 target；留下的是可以指导新候选设计的约束和实现手法。

### 10.2 可跨模块复用的逻辑模式

#### A. Canonical producer → derived projection（S03–S06、S08、S09）

可复用规则：一个事实只在拥有它的 owner 中构造，其他模块直接消费该事实或 owner 的窄 projection。

- compiled classification 直接从 `nested_graphs` keys 和 nominal node variant 推导，不保存分类镜像（S03）；
- publication/output/input declaration 直接从 descriptor/transition canonical map 读取，不保留 `CompiledGraph` forwarding
  projection 或 keyed plan 的重复 `node_id`（S04–S06）；
- `joins_by_source` 只由 routing owner 解释，snapshot guard 复用 routing 的 `_declared_joins()`（S08）；
- routing facts 只投影一次为现有 `ResolutionCommand`，不保留 `RoutingResolution`/`plan_routing()` wrapper（S09）。

这套模式适合跨模块复用，因为它减少的是 producer/consumer 之间的第二真相，而不是把不同领域类型强行合并。
新候选必须先回答“谁拥有 canonical fact”，再决定是否删除 mirror；不能先写一个通用 projection helper。

#### B. 一次扫描、局部 typed index、从 facts 推导状态（S01、S02、S10、S11、S17、S18）

可复用规则：在一个明确的 invocation/owner 生命周期内，建立一个窄 typed index，完成一次有序 enumeration，之后从已确认
fact 推导布尔值或诊断，不再保存镜像字段。

- compiler/validation 使用 scope-local `nodes_by_id`，不跨 nested scope 共享第二 index（S01/S02）；
- graph-output diagnostic 保留一次完整 scan 的 canonical tuple，同时保留有独立短路语义的 helper（S10）；
- 每个 unique target 只做一次 binding scan，`RequiredTarget` 直接作为 cache value，首次访问顺序固定为
  control → completed join → data（S11）；
- pure-skip 由 command actions 与 substitutions 的 typed coordinate 差集推导，不保存 `skip_actions`/`has_pure_skip`
  镜像（S17）；
- duplicate/collision 在各自 owner 的同一次 canonical enumeration 中收集，每个 owner 至多一个 count index，并保持
  duplicate 优先于 collision（S18）。

可复用的不是“任何地方都建一个 dict”，而是以下四个条件的组合：scope-local、typed key/value、固定访问顺序、生命周期结束
即释放。跨 owner generic index、全局 cache、无序 set 或第二次完整 scan 都不属于该模式。

#### C. 复用既有唯一基础设施，而不是再包一层（S04–S06、S07、S20、S23B）

可复用规则：已有 owner 已能表达目标事实时，迁移 consumer 直接使用它，删除 wrapper/adapter；不要为了“统一接口”再加一层。

- compiled lowering 直接使用 `graph.transition.*`；
- graph input declaration 直接使用 `graph_input_descriptor.declarations`；
- coordinate assembly 只保留 GraphInput、NodeOutput、resume 三个 source-specific typed constructor（S07）；
- failed retry 继续调用唯一 `materialize_node_input()`，不创建第二 materializer 或 materialization-only State/frontier（S20）；
- failure/interrupt Result view 由 family-driver 的单次 canonical scoped-state scan 投影（S23B）。

这条规则可支持大范围迁移，但每次必须由原 owner 进行 consumer migration；不能通过 compatibility bridge 让旧路径和新路径长期并存。

### 10.3 只能在原语义 owner 内复用的模式

| 来源 | 可复用内容 | 复用边界 |
| --- | --- | --- |
| S01 | 删除无语义 alias、保留 phase/error order、只抽有两个真实 production consumer 的窄 predicate | 仅限 compiler invocation；不能把 phase orchestration 抽成 DTO/helper |
| S02 | scope-local typed lookup、`frozenset` join identity、保持首错顺序 | 仅限 validation；不能推广成 family-global index |
| S07 | 每个 nominal source 一个 coordinate constructor | 不能用 wide union/generic adapter 合并三类 source |
| S13 | 删除未使用参数和调用实参 | 仅限已证明 dead 的 plumbing；不能借机改变调用顺序 |
| S14 | boundary 拥有 kind/availability，control 负责 equality-participating child disposition | 不能从 `compare=False` concrete state 反推 identity |
| S16 | 同一入口内用 `pairwise` 做相邻 canonicality 检查 | 仅限同一 exact nominal domain；不能新增第二 validator |
| S19 | 只提取完全相同的 encode/decode/frame-admission mechanics | 不得形成 action context bag 或吞掉 action-specific error owner |
| S23A | 普通进展用 `None`，删除 sentinel | 仅限当前 advance boundary；不能把所有 terminal disposition 合并 |

这些模式可以指导新代码，但不能把原实现中的 private symbol、参数顺序或测试断言当作跨模块 API。

### 10.4 领域原则可以借鉴，exact algorithm 不可搬运

S12 和 S15 的可复用部分是证明纪律，而不是具体 recovery 数据结构：

- valid-domain equality/hash 必须只在声明的 nominal domain 内成立；
- recovery seed、worklist successor 和 availability proof 必须各自拥有清晰生命周期；
- malformed seed、parent/coordinate mismatch、budget 和首错顺序必须有独立 evidence；
- runtime concrete value 与 recovery coordinate-only proof 不能共用一个大解释器。

因此，候选 A 可以借鉴 S12 的 coordinate/scope owner 和 S15 的纯 worklist successor 纪律，但不能直接复制
`RecoveryStateBinding`、`_RecoveryWorkItem` 或 recovery transfer record。

### 10.5 明确不可复用的旧建议

以下内容在主实施方案中已经明确为 KEEP，不能作为新候选的“现成抽象”：

- S21：不能把 `Graph.run()` lifecycle 拆成两个 private runner、宽 `_RunContext` 或 enum dispatcher；
- S22：不能把 fence/resume 两个 transaction loop 合成通用 confirmation helper；
- 不能把 `ScopedFrameIndex`、`CandidateFrameAvailability`、`RecoveryAvailabilityCoordinates` 统一成一个 index；
- 不能把 root/child control、complete/recovered continuation、runtime/recovery frame/value record 合并为 optional/bool 结构；
- 不能把任何旧建议转移到 State、Store、持久化或 failover。

### 10.6 旧方案对两个新候选的直接贡献

| 新候选 | 可以复用的旧模式 | 必须拒绝的旧模式 |
| --- | --- | --- |
| `ScopedStateIndex` / `ScopedStateBinding`（A-v1、已退休） | S02 的 scope-local typed lookup；S08 的 single owner；S14 的 boundary-owned projection；S12/S15 的 staged proof discipline | S02 式 family-global map；合并 runtime/recovery record；把 planned successor 提前写入 context |
| root state 去包装（A-v2、待复审） | S08 的 single owner；S14 的 boundary-owned projection；S23A 的 sentinel/wrapper 直接删除纪律 | 新 index、兼容 property、第二 replacement entry、合并 child/planned/proof binding |
| sealed `AdmittedResumePlan` | S17 的 derived facts；S19 的窄 typed admission mechanics；S20 的唯一 materializer；S23B 的 single-pass projection | 合并 concrete frame 与 availability proof；S21/S22 式 lifecycle helper；宽 DTO/optional stage fields |

这张表说明旧方案仍然有直接指导价值，但不能把“可复用模式”误读为“可以沿用原类型”。A-v2 只复用既有
GraphRunContext/replace_state owner 并删除 wrapper；所有当前候选仍需独立建立 requirements、exact target、behavior matrix 和
complexity delta。

## 11. 回收后的实施判断

从主实施方案回收出的真正可复用资产，按优先级是：

1. **优先复用 canonical owner + derived projection**：这是收益最大、风险最低的跨模块模式；
2. **其次复用 scope-local typed index + single enumeration**：只在一个 owner/lifecycle 内建立，禁止抽成通用内核；
3. **再复用既有 materializer/coordinate constructor/Result projection**：优先删除第二路径，而不是新增统一入口；
4. **最后才考虑 record 合并**：S12/S15/S16 已证明 nominal boundary 通常比字段数量更重要。

如果新候选的设计只能通过复制旧 DTO、保留双路径、改变 S21/S22 的显式 transaction loop 或扩大 State 范围完成，
则旧文档没有提供可复用的许可，应立即停止。

**最终 disposition：** 主实施方案中的可复用模式已完成回收；A-v1 不重新实施。A-v2 已完成首次评审整改，保持独立、待复审且未批准；
其他候选同样保持独立、未批准。
