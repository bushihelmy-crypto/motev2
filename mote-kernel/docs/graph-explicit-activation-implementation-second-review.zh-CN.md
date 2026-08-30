# Graph 输入绑定与显式执行激活解耦实施方案第二次独立评审

> **结论：`PASS / R1–R6 CLOSED / READY FOR EXPLICIT IMPLEMENTATION AUTHORIZATION`。**
> 修订方案已经移除 definition version、部署、持久化与 failover 越界，复用现有 `GraphValidationError`，
> 补齐 compiler 中三条 dormant implicit-activation fallback 的删除，修正 explicit START 的错误优先级，
> 并准确区分 canonical declaration 与 derived lowering。当前五文件 production manifest 与源码实际消费点一致，
> 可以只靠现有 compiler/control/materialization/routing infrastructure 完成，未发现需要新类型、helper、State、
> compatibility path 或第二 runner 的技术问题。本评审通过只表示设计已具备实施条件；用户未明确授权前，
> 不修改 production/tests。

## 1. 评审对象与冻结版本

- 评审日期：2026-08-26
- 评审对象：[Graph 输入绑定与显式执行激活解耦实施方案](graph-explicit-activation-implementation.zh-CN.md)
- 本轮 reviewed SHA256：`5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6`
- 第一轮评审：[Graph 输入绑定与显式执行激活解耦实施方案独立评审](graph-explicit-activation-implementation-review.zh-CN.md)
- 第一轮评审 SHA256：`0a0dcccc0f7c0187b3714b625107d3d46395eb9fb41202cd67b470d1d2191532`
- 第一轮 reviewed implementation SHA256：`ecc4bb6e885407d0ee15135ea1e8a09c63f2fa317f9800af79ca294ad2240f7b`
- production source baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 本文性质：第二次独立 technical review；只拥有本轮裁决和复核证据，不拥有 target 或 implementation 授权
- 本轮 change unit：只新增本文，不修改实施方案、production、tests、State 或其他用户文件

## 2. 第一轮 R1–R6 关闭情况

| Review item | 状态 | 第二轮复核结论 |
| --- | --- | --- |
| R1 persistence/version/failover 越界 | **CLOSED** | §3.6、§5.3、§6、§11 已明确禁止 State、Store、version、deployment、failover 与 rollback；recovery只删除一处 stale read |
| R2 新增 `MissingActivationError` | **CLOSED** | §3.4 固定复用现有 `GraphValidationError` / `Graph.ValidationError`；`execution/errors.py` 明确零 diff |
| R3 三条 dormant fallback 漏删 | **CLOSED** | §4.3 分别冻结 `_guaranteed_sets()`、`_validate_joint_activation_paths()`、`_input_publication_selection()` 的精确删除 |
| R4 explicit START 错误优先级 | **CLOSED** | §3.4、§4.1 把 explicit START dependency error 固定在 missing-control 之前，且 START 不伪装成 activation gate |
| R5 canonical/derived owner 混淆 | **CLOSED** | §3.1 以 `GraphDefinition.nodes[*].inputs` 和 entries/edges 为 canonical truth，locals/compiled plan只作 derived lowering |
| R6 complexity/test/gate 未闭合 | **CLOSED** | §7 聚焦新 contract，§8 固定 before/after report 与 ratchet下调，§10.4 固定全量门禁且禁止新 waiver |

修订版本没有用另一套设计绕开评审，而是在原有正确方向上逐项收窄；六项关闭均可由当前 source 机械验证。

## 3. Compiler target 复核

### 3.1 Phase 与首错顺序正确

当前 source 中 explicit START dependency check读取 `definition.entries`，而 ordinary incoming gates只来自
direct/conditional/join。修订方案的目标顺序为：

```text
definition/edge shape
→ source/type/scope/data-cycle
→ build control indexes
→ explicit START dependency error
→ missing incoming control
→ entry/reachability
→ joint-route/guarantee/publication/output proof
```

这保持了现有 explicit START错误，同时让无 START、无 incoming gate 的 node-output consumer得到更精确的
`GraphValidationError`。unknown/self source、unknown output与data cycle仍先于新规则，错误优先级无反转。

missing-control check直接消费既有 typed locals `data_dependencies` 与 `activation_gates`；内联在
`_compile_graph()` 即可，不需要新 helper、record或 error subtype。producer集合本身已经是 set，canonical sort 后消息
天然去重且确定。

### 3.2 三条 fallback 删除语义完备

三条计划删除的 branch 都只服务当前 implicit activation：

1. `_guaranteed_sets()` 的 `elif data_dependencies[node_id]` 只在没有 gates 时把 producer传播为 guarantee；
2. `_validate_joint_activation_paths()` 的 `alternatives.append(data_requirement)` 只在没有 control alternative时让
   data requirement独立形成 path；
3. `_input_publication_selection()` 的 `not gates and data_dependencies[target] == {source.node_id}` 只为 data-only
   consumer产生 relative coordinate。

新 missing-control rule 生效后它们虽不可达，但方案仍要求删除 branch与不再需要的参数，符合零 dormant compatibility
debt。对已有显式 gate 的合法 graph：

- `_guaranteed_sets()` 当前本就优先走 gates branch；
- joint-route proof仍把 data requirement与每个 control alternative合并；
- publication selection仍保留 absolute selection与 `_all_single_source_gates()` relative selection。

因此删除不会削弱 explicit direct/conditional/join 的 guarantee、route satisfiability 或 loop-coordinate proof。

### 3.3 Control reachability 与 data proof 分离正确

方案只删除 data dependency 对 `successors`/`reachability_successors` 的注入，保留：

- entries、direct、conditional提供 ordinary control reachability；
- `_reachable()` 的现有 join-AND fixed point；
- control successors用于cycle/activation-level proof；
- `data_dependencies`继续用于data cycle、joint-route requirement、required-producer guarantee与publication binding。

这不是删除data validation，而是删除data declaration对control successor的越权。same-pair binding+direct edge因此合法，
真实重复direct edge仍由现有validation owner拒绝。

## 4. Runtime 与 manifest 复核

### 4.1 Production 五文件 manifest 完整

当前 production symbol inventory为：

| Owner | 当前 implicit-trigger职责 | Target |
| --- | --- | --- |
| `execution/graph/compiler.py` | 生产 `data_targets` / `DataTriggerPlan`，包含三条fallback和same-pair conflict | 删除生产端并新增inline generic validation |
| `execution/graph/topology.py` | 定义 `DataTriggerPlan` / `data_triggers` | 删除类型与field |
| `execution/engine/routing.py` | 扫描publication、生成/合并data targets | 删除field、scan、merge与stale completion branch |
| `execution/engine/resume_admission.py` | 两处读取 `facts.data_targets` | 机械删除两处读取 |
| `execution/engine/recovery.py` | 一处读取 `facts.data_targets` | 机械删除一处读取 |

对 `src/mote_kernel/execution/**` 的实际搜索没有发现第六个 producer/consumer。`graph/__init__.py` 不导出
`DataTriggerPlan`，facade与errors也不消费该shape，因此§6.1的五文件production manifest准确。

### 4.2 Routing 简化保持现有边界

删除data targets后，routing顺序收敛为：

```text
显式control target缺值 → Abort
存在direct/selected-conditional/completed-join target → Advance
仅剩partial join → Deadlock
completion缺graph output → Abort
否则 → Complete
```

`required(target)`、join progress、graph-output availability和typed frame coordinates继续由原owner负责；publication仍是
materialization/output projection的value evidence，不再产生successor。resume admission与recovery继续共享同一个
`resolve_routing_facts()`，删除field不会创建第二 lowering或分叉路径。

### 4.3 No-persistence / no-failover 边界成立

修订方案没有再要求：

- definition version升级或旧State迁移；
- Store、checkpoint、journal或跨进程frame recovery；
- state-only/active-execution recovery新语义；
- deployment排空、worker仲裁、rollback或exactly-once协议。

保留 `recovery.py` 的单行机械消费删除是compiled shape变化的必要闭包，不是新增failover工作。§11还把任何超出该
stale read的recovery改动设为停止条件，范围清晰。

## 5. 唯一事实与零新增负债复核

修订后的owner边界正确：

| 事实 | 唯一canonical owner | Derived form |
| --- | --- | --- |
| parameter value source | immutable node input declarations | `data_dependencies`、resolved binding、materialization plan |
| activation eligibility | entries与direct/conditional/join declarations | local gates与compiled control indexes |
| concrete output presence | acknowledged publication frame | typed availability coordinate |

目标不新增type、dataclass、field、helper、DTO、flag、index、property、alias、cache或public API；删除面包括：

- `DataTriggerPlan` top-level dataclass/type；
- `DataTriggerPlan.targets`、`FrontierTransitionPlan.data_triggers`、`RoutingFacts.data_targets` 三个fields；
- compiler data-target maps/loops/fallbacks；
- runtime publication-trigger scan与ready-data merge。

inline missing-control branch是必要新判断，但整体结构必然净下降。方案正确地要求以actual complexity report决定最终
decision-point ratchet，而不是在设计阶段伪造数值；同时禁止提高limit或新增reviewed waiver。

## 6. Definition、tests 与 docs 范围复核

当前 `Graph.node_output()` inventory覆盖：

- 15个 `tests/execution/**` 文件；
- 5个 `example/graph/**` 模块；
- 双语README；
- 当前Node I/O规范。

不是每个命中都需要修改：`set_outputs()`只做projection，已有conditional/join gates的consumer已合法。方案要求按
direct、conditional、join、coordinator、output projection逐项分类，避免机械codemod，迁移方法正确。

示例判定与当前源码一致：linear与nested各缺两条direct edges；parallel已有join；conditional已有route gates；
human-in-the-loop只有automatic entry和output projection。

测试矩阵覆盖新contract而没有重新定义recovery：missing-control、same-pair、conditional/END、join/coordinator、START
precedence、builder retry和graph-output projection均有直接证据。现有resume/nested/recovery只迁移fixtures并回归，
不产生新version/failover contract。

实施时§7.2的零副作用断言应通过共享的parametrized/public boundary evidence或既有测试基础设施表达，不能为了分别观察
child/claim/resource而新增production hook、通用spy framework或四套重复测试。不存在child/resource的case中“调用为0”
只是边界一致性，不应冒充独立深层覆盖；node call、commit与compiled-owner未安装是本变更最直接的非空证据。

## 7. 非阻断实施约束

以下事项无需修改当前设计，但implementation review必须核对：

1. Phase 0完成后，把实际changed test/example/doc files记录在handoff manifest；“inventory命中”不能自动等于“允许改动”。
2. 删除routing publication scan后同步清理只由该scan使用的imports，不留下ruff发现的stale symbol。
3. architecture gate只更新exact compiled fields/owners，不新增冻结compiler local名称、源码行号或branch layout的测试。
4. symbol归零查询只覆盖production与active tests；implementation/review/history文档中的删除说明不能造成假失败。
5. complexity reviewed identity若因行号移动而更新，必须确认候选逻辑未变化且没有新增identity。

这些约束均已由修订方案的manifest、architecture、complexity和停止条件覆盖，不构成新的R item。

## 8. 复审准入与授权边界

本轮review通过绑定当前SHA。以下任一变化都会使通过失效并要求重新评审：

- production五文件manifest扩大；
- missing-control改为新error/helper/DTO或public API；
- 三条fallback未全部删除或留下compatibility field/property；
- explicit START/error precedence改变；
- recovery/resume出现新算法或新contract；
- State、Store、version、deployment、persistence或failover进入scope；
- complexity需要提高ratchet或新增reviewed waiver；
- automatic entry、natural completion或graph-output projection语义改变。

技术通过不自动授权implementation。下一合法步骤是用户明确授权后，按方案§9的原子单元实施production、tests、
definitions与当前规范；不得只落compiler或只迁移definitions。

## 9. 验证记录

| 验证 | 结果 |
| --- | --- |
| reviewed implementation SHA256 | `5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6` |
| first review SHA256 | `0a0dcccc0f7c0187b3714b625107d3d46395eb9fb41202cd67b470d1d2191532` |
| production/test worktree | 本轮评审前 `src/**`、`tests/**`、`pyproject.toml`、`Makefile` 无diff |
| source consumer inventory | implicit-trigger production producer/consumer严格落在方案列出的5个文件 |
| current complexity | `503/287/177/499/1326`；`51 reviewed / 0 unreviewed / 0 stale` |
| `make check` | Ruff/format通过；Pyright `0 errors`；complexity ratchet `9 passed`；health `51 reviewed / 0 unreviewed / 0 stale`；全量 `843 passed`、coverage 100%；build/twine通过 |
| monorepo root scoped pre-commit | 对本文运行全部适用hooks，全部通过 |
| whitespace | `git diff --no-index --check /dev/null <second-review>` 无诊断 |

## 10. 最终裁决

```text
blocker = 0
major = 0
minor = 0

R1 persistence/version/failover scope = CLOSED
R2 unnecessary error type = CLOSED
R3 dormant implicit fallbacks = CLOSED
R4 explicit START precedence = CLOSED
R5 canonical-vs-derived ownership = CLOSED
R6 complexity/test/gate scope = CLOSED

core semantic target = ACCEPTED
single-truth / infrastructure reuse = PASS
zero-new-debt design = PASS
no-persistence / no-failover boundary = PASS
independent technical review = PASS
implementation readiness = READY FOR EXPLICIT USER AUTHORIZATION
production/tests authorization = NOT YET GRANTED
```

**最终结论：修订方案已经达到“完整删除隐式激活，而不是增加新激活系统”的最合适边界。它复用现有canonical
definition、control indexes、guarantee proof、materialization、routing与recovery consumer，只增加一个必要的inline
validation判断，同时净删除类型、字段、索引和分支。当前设计可以进入显式实施授权阶段。**

## 11. 本次 review change unit

本轮只新增：

```text
mote-kernel/docs/graph-explicit-activation-implementation-second-review.zh-CN.md
```

未修改被评审实施方案、第一轮评审、production、tests、State、Store、protocol、persistence、complexity配置或其他用户文件。
