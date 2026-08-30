# Graph 输入绑定与显式执行激活解耦实施方案独立评审

> **结论：`CHANGES REQUESTED / CORE DIRECTION ACCEPTED / R1–R6 OPEN / NO PRODUCTION AUTHORIZATION`。**
> 将 `NodeOutputRef` 从隐式激活中拆出、要求 node-output consumer 使用显式 direct/conditional/join control gate，
> 方向正确，也能复用现有 compiler、`FrontierTransitionPlan`、routing 与 materialization 基础设施完成。
> 但当前方案把 definition version、state-only recovery、部署排空和回滚纳入本期，新增了没有独立语义的
> `MissingActivationError`，且遗漏 compiler 中三条 implicit-activation fallback 的删除；错误优先级与 explicit
> START 现有行为也冲突。R1–R6 完成并重新冻结方案前，不应实施 production/tests。

## 1. 评审对象与边界

- 评审日期：2026-08-26
- 评审对象：[Graph 输入绑定与显式执行激活解耦实施方案](graph-explicit-activation-implementation.zh-CN.md)
- 评审对象 SHA256：`ecc4bb6e885407d0ee15135ea1e8a09c63f2fa317f9800af79ca294ad2240f7b`
- production source baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 本文性质：独立 design review；只拥有本次裁决与整改要求，不拥有 target 语义或 production 授权
- 本次 change unit：只新增本文，不修改被评审方案、production、tests、State 或其他用户文件

评审采用用户明确边界：

1. canonical declaration 只有一份，compiled/runtime 只保存职责明确的派生 lowering；
2. 优先复用现有 infrastructure，不增加单用途类型、helper、DTO、flag、index 或兼容路径；
3. 删除隐式激活必须连同 dormant fallback 一次归零，不能只让旧逻辑不可达；
4. 做完整且必要的原子改动，不扩大到持久化、failover、部署迁移或无关重构；
5. 保持 Python 3.11+ strict typing、现有 owner/dependency/source-discipline 与 complexity 门禁；
6. 代码与测试应直接表达语义，不冻结无意义的 private implementation shape。

## 2. 总体复核

| 维度 | 裁决 | 说明 |
| --- | --- | --- |
| input binding / activation 解耦 | **通过方向审查** | 解决 conditional 泄漏、END 穿透和隐式副作用扇出，根因判断准确 |
| 唯一公共门面与执行引擎 | **通过** | `Graph` 与现有 `execution` engine 保持唯一 owner |
| data/direct same-pair | **通过** | value binding 与 control edge 职责不同，不应继续判 duplicate |
| automatic entry / natural completion | **通过** | 保留现有边界，避免顺带强制 START/END |
| compiled/runtime trigger 删除 | **通过方向审查** | 删除 `DataTriggerPlan`、`data_triggers`、`data_targets` 是正确结构目标 |
| 唯一事实表述 | **需整改** | 当前把 derived `ResolvedInputBinding`、`activation_gates` 写成 owner，canonical/derived 边界不够准确 |
| 零新增负债 | **不通过** | 新 error type 无必要，三条 implicit fallback 未列入删除 |
| no-persistence / no-failover | **不通过** | version、state-only recovery、部署和回滚形成明显越界 |
| phase/error precedence | **不通过** | missing activation 会抢占 explicit START 的既有错误 |
| complexity 与工程门禁 | **需整改** | 未登记净结构下降与 ratchet writeback，测试矩阵过度扩张到 recovery/version |

核心语义不需要推倒重做；问题集中在范围、owner 精度和完整删除账本。

## 3. R1 — MAJOR：持久化、version、failover 与部署内容越界

当前方案明确要求：

- §3.1.10：受影响的 durable definition 提升 `GraphDefinitionVersion`；
- §7.4：设计 state-only recovery worklist 行为；
- §9 Phase 0/3：盘点 durable state/checkpoint 并预分配、提升 version；
- §10.3：新增 recovery/fence/token/revision 专项矩阵；
- §11.2/11.3：定义 version migration 与部署排空顺序；
- §12.3/§14：把 version、旧 State 与服务回滚纳入风险和回滚协议。

这些内容不是删除 implicit data activation 的必要 kernel 实现步骤，且与用户“不做持久化和 failover”直接冲突。
Graph version、State identity 和运维排空即使在其他上下文有意义，也应由独立需求与部署方案拥有，不能借本次
compiler/runtime 语义收敛进入代码单元。

**整改要求：**

1. 删除 §3.1.10、§11.2、§11.3、§12.3、§14 以及 Phase 0/3 中的 version/durable inventory；
2. 删除新增 state-only recovery、active execution recovery、fence/token/revision 的专项测试义务；
3. 在非目标中明确：不修改 Store、State、definition version、deployment、checkpoint、failover 或 rollback protocol；
4. `execution/engine/recovery.py` 当前直接读取 `facts.data_targets`，因此 topology field 删除时必须机械删除该 stale
   read；这只是保持现有 consumer 可编译和 routing truth 唯一，不授权新增或重构 recovery 算法；
5. `resume_admission.py` 同理只删除被移除字段的消费，保留既有 skip/resume 行为和回归测试，不扩展能力。

若实施中发现 `recovery.py` 除删除 `facts.data_targets` 外需要新 State、coordinate、work item 或 traversal 算法，
本变更必须停止并重新评审，而不是扩大 manifest。

## 4. R2 — MAJOR：`MissingActivationError` 是不必要的新类型

方案 §4.6、§5.2、§8.1、§9 和测试矩阵均要求新增
`MissingActivationError(GraphValidationError)`，并修改 `execution/errors.py`。

当前基础设施已经满足全部需要：

- `GraphValidationError` 统一拥有静态 graph invariant；
- public facade 通过 `Graph.ValidationError` 暴露唯一必要 catch surface；
- missing incoming control 不需要 caller 分支处理、序列化、恢复或独立生命周期；
- compiler 已经对 data cycle、guaranteed-before、publication coordinate 等相邻不变量直接使用
  `GraphValidationError`。

新增 subclass 只产生一个单用途 top-level/type definition，并要求额外 import、`__all__`、测试名称与文档 owner，
没有增加可观察语义。它还会抵消删除 `DataTriggerPlan` 带来的结构下降，不符合“复用基础设施、零负债”。

**整改要求：**

1. 直接抛现有 `GraphValidationError`；
2. 固定清晰、确定性的消息，例如：

   ```text
   node 'B' consumes node outputs from ('A', 'C') but has no incoming control edge
   ```

   producer tuple 必须 canonical sort、去重；
3. public behavior 只断言 `Graph.ValidationError` 与关键消息，不导出或导入新的 internal error type；
4. 从 production manifest 删除 `execution/errors.py`，该文件应保持零 diff。

## 5. R3 — BLOCKER：compiler 中三条 implicit fallback 未纳入删除

方案要求删除 data trigger，但 §5.2 只写“复用现有 joint activation path、route requirement 和 guaranteed set proof”。
当前 proof 本身包含 implicit activation 语义；只增加 missing-control precheck 会让它们暂时不可达，却会留下完整兼容分支，
未来 phase order 或校验变化即可重新生效。

必须删除的现有逻辑是：

1. `execution/graph/compiler.py:_guaranteed_sets()`：

   ```python
   elif data_dependencies[node_id]:
       ...
   ```

   它在无 control gate 时把 data producers 当作 activation guarantee。目标中 `_guaranteed_sets()` 应删除
   `data_dependencies` 参数，guarantee 只从 entries 与 `activation_gates` 传播。

2. `execution/graph/compiler.py:_validate_joint_activation_paths()`：

   ```python
   if alternatives:
       ...
   else:
       alternatives.append(data_requirement)
   ```

   data requirement 仍须与每个 control alternative 合并，以证明 route 可共同满足；但无 control alternative 时不得
   单独形成 activation path，`else` fallback 必须删除。

3. `execution/graph/compiler.py:_input_publication_selection()`：

   ```python
   directly_causal = _all_single_source_gates(...) or (
       not gates and data_dependencies[target] == {source.node_id}
   )
   ```

   relative publication selection 必须只由显式 control cause 证明；删除 `not gates` fallback 和
   `data_dependencies` 参数。

此外还应一次删除：

- data/direct same-pair conflict loop；
- `data_targets` local map；
- data dependency 对 `successors` 与 `reachability_successors` 的注入；
- `DataTriggerPlan` import/assembly；
- routing publication scan、ready-data merge和相关 completion condition；
- resume/recovery 对 `facts.data_targets` 的全部读取。

**整改要求：**把上述内容写入 exact production deletion ledger。不得保留 unreachable branch、empty property、alias、
feature flag、legacy compiler 或 reverse binding index。

## 6. R4 — MAJOR：missing-control phase 会覆盖 explicit START 既有错误

方案 §5.2 的顺序是：

```text
建立 activation_gates
→ data dependency 非空且 activation_gates 为空时报 MissingActivationError
→ 推导 automatic/explicit entries
```

但 `Graph.START -> node` 在现有 definition 中规范化为 `definition.entries`，不会进入 `activation_gates`。因此一个
读取 node output 的 explicit START target 会先命中 missing-control，而不是当前明确的：

```text
an explicit START target cannot require a node output
```

这与方案 §4.5、测试 EACT-C11 的“保持非法”以及既有错误优先级不一致。

**整改要求：**每个 definition scope 的 phase 顺序固定为：

```text
validate definition/edge shape
→ resolve value source/type/scope
→ reject ordinary data cycle
→ build direct/conditional/join control indexes
→ reject explicit START target with node-output dependency
→ reject node-output consumer without incoming direct/conditional/join gate
→ derive automatic/explicit entries and check reachability
→ joint-route / guaranteed-before / publication-coordinate / output guarantee
→ assemble topology without data triggers
```

不应通过把 START 伪装成 `activation_gates` 修复顺序；START entries 与 ordinary incoming control gate 的 owner
边界应保持现状。

## 7. R5 — MAJOR：canonical truth 与 derived lowering 的 owner 表述不准确

方案 §5.1/§5.3 把 `ResolvedInputBinding`、`direct_targets` 和 `activation_gates` 描述为各自 truth owner。
这些对象都是 compiler 从 immutable definition 派生的 proof/runtime lowering，不是 canonical declaration。

正确边界应写为：

| 事实 | canonical declaration | derived compile/runtime form |
| --- | --- | --- |
| consumer 参数取值 | `GraphDefinition.nodes[*].inputs` 中的 `GraphInputRef | NodeOutputRef` | `ResolvedInputBindings` / `MaterializationPlan` |
| 节点激活资格 | `GraphDefinition.entries` 与 direct/conditional/join edges | compiler-local `activation_gates`；compiled direct/conditional/join indexes |
| concrete output 是否存在 | exact acknowledged publication frame | typed availability coordinate与 materialization lookup |

`data_dependencies` 与 `activation_gates` 可以作为一次 compile invocation 的 typed local indexes，但不得持久化、导出、
缓存或称为第二份 declaration truth。`FrontierTransitionPlan` 是 runtime 唯一 compiled lowering，也不是另一个 graph
definition owner。

**整改要求：**重写 owner 表和 same-pair 说明，明确 canonical declaration 与 derived lowering。当前规范文档继续是
落地后的唯一规范事实源；本实施方案只记录 delta、manifest 与验收，不应重复完整 persistence/recovery/runtime 规范。

## 8. R6 — MAJOR：复杂度 writeback 与测试范围未闭合

当前 production complexity baseline 为：

```text
top_level_definitions: 503
type_definitions: 287
dataclass_types: 177
dataclass_fields: 499
decision_points: 1326
health: 51 reviewed / 0 unreviewed / 0 stale
```

目标会删除 `DataTriggerPlan`、`FrontierTransitionPlan.data_triggers`、`RoutingFacts.data_targets` 和多处分支；
理论上应形成可测量净下降。方案却新增 error type，且没有把 `make complexity-report`、ratchet 下调和 reviewed identity
检查纳入 manifest。

同时，§10.3 新增十个 resume/recovery/nested cases，并要求 versioned recovery、active execution recovery 等证据，
已经超出本次语义变更。最合适的新增行为证据应集中在真正新 contract：

1. missing incoming control 使用 `Graph.ValidationError`；
2. data binding + direct same-pair 合法且只有一个 control target；
3. conditional 未选择 route 时 publication 不泄漏激活；
4. `A -> END` 不被隐藏 consumer 穿透；
5. join/coordinator 继续复用现有 guaranteed-before proof；
6. public compile failure 发生在 node call 与 commit callback 前，builder 补 edge 后可重新 compile；
7. graph-output projection 不需要 consumer control edge。

现有 resume、skip、nested、recovery tests 应迁移依赖 implicit activation 的 fixtures并全量回归，但不为本变更新建
failover contract。

**整改要求：**

- production 落地前后运行 `make complexity-report`；
- 实际下降同步下调 `pyproject.toml` ratchet，不提高 limit、不新增本变更产生的 reviewed waiver；
- 若既有 reviewed identity 仅因行号移动而变化，可同步 exact identity，但不能把新 smell 标为 reviewed；
- 保留 exact compiled-field/owner architecture gate；不增加冻结 local 变量、helper 名称或源码行数的 private-shape test；
- 收窄新增测试矩阵，其余只作为既有全量回归。

## 9. 已通过且应保留的设计

以下内容无需重做：

- `inputs=` 只拥有 value source/type/materialization，control edge拥有 activation eligibility；
- data/direct same-pair 不再视为 duplicate，真实重复 `DirectEdge` 继续由 `graph/validation.py` 拒绝；
- data dependency 不再提供 reachability或runtime successor；
- automatic entry 与 natural completion 保留，不顺带强制 START/END；
- graph output binding不是 node activation，不增加虚假 edge；
- multi-source AND barrier使用现有 join，不把多个 direct edges误当 join；
- coordinator-controlled consumer复用现有 guaranteed-before proof；
- 不引入 latest-value、delayed feedback、按 node ID 读取最近 publication；
- 不增加 strict mode、feature flag、warning fallback、compatibility alias或第二 runner；
- compile failure必须先于 compiled-owner安装、Start commit、claim、child start和node side effect；
- definitions 按 direct、conditional、join、coordinator 四类业务意图迁移，不机械按每个 producer生成 edge；
- production、tests、examples、README和当前规范在一个原子交付单元同步。

## 10. 建议的精确实施 manifest

整改后的 production manifest 应只有：

| 文件 | 必要改动 |
| --- | --- |
| `execution/graph/compiler.py` | generic missing-control validation；删除 same-pair conflict、implicit lowering和三条 fallback |
| `execution/graph/topology.py` | 删除 `DataTriggerPlan` 与 `data_triggers` field |
| `execution/engine/routing.py` | 删除 publication-trigger collection、`RoutingFacts.data_targets`、ready-data merge并简化 completion |
| `execution/engine/resume_admission.py` | 只删除两处 `facts.data_targets` 消费 |
| `execution/engine/recovery.py` | 只删除一处 `facts.data_targets` 消费 |

明确禁止 production diff：

```text
execution/errors.py
execution/facade.py
state/**
Store / persistence / checkpoint / failover / version migration owners
```

Tests/examples/docs 只按 actual inventory 修改：

- compiler contract：missing control、same-pair、START priority、guarantee/coordinate；
- public API：conditional/END不泄漏、compile前零副作用、失败后可补 edge；
- resume admission：原 data-trigger fixture改成显式 control target；
- architecture：删除 compiled data-trigger field/owner；
- active definitions：按业务意图补 direct/conditional/join；`set_outputs()` refs不补 edge；
- current normative implementation 与双语 README：同步唯一目标语义；
- `pyproject.toml`：只按实际 complexity下降收紧ratchet或修正移动后的reviewed identity。

## 11. 复审准入条件

R1–R6 全部关闭后，新的 reviewed implementation SHA 才可进入复审。复审至少核对：

```text
R1 no persistence/version/deployment/failover scope
R2 no new error/helper/DTO/flag/index
R3 all implicit trigger fields, branches and fallbacks removed
R4 explicit START and missing-control precedence fixed
R5 canonical declaration vs derived lowering ownership accurate
R6 focused tests + measured complexity ratchet + full gates
```

计划实施完成后的 required gates：

```bash
python -m pytest \
  tests/execution/graph/test_compiler_contract.py \
  tests/execution/engine/test_resume_admission.py \
  tests/execution/test_graph_api.py \
  tests/architecture/test_graph_execution_ownership.py -q
python -m tests.architecture.complexity_rules
make check
cd .. && pre-commit run --all-files
git diff --check
```

一次性 source review应确认 production/active tests 中以下名称归零：

```text
DataTriggerPlan
data_triggers
data_targets
implicit_targets
publication_consumers
trigger_on_data
```

历史/评审文档中的删除说明不计入归零查询。绿色门禁不能替代 R1–R6 的设计闭合。

## 12. 最终裁决

```text
blocker = 1
major = 5
minor = 0

core semantic direction = ACCEPTED
single-truth / infrastructure reuse target = ACHIEVABLE WITH EXISTING OWNERS
R1 persistence/failover scope = OPEN
R2 unnecessary error type = OPEN
R3 dormant implicit fallbacks = OPEN / BLOCKER
R4 explicit START precedence = OPEN
R5 canonical-vs-derived ownership = OPEN
R6 complexity/test/gate scope = OPEN

independent design review = CHANGES REQUESTED
production/tests authorization = NOT GRANTED
```

**最终结论：显式激活方向成立，而且最优实现不是增加一套 activation infrastructure，而是完整删除 data-trigger
lowering并复用现有 control indexes、guarantee proof、materialization与routing。当前方案先移除持久化/failover越界，
改用现有 `GraphValidationError`，补齐三条 dormant fallback删除和正确phase顺序，再以净结构下降和聚焦测试复审。**

## 13. 验证记录

| 验证 | 结果 |
| --- | --- |
| reviewed implementation SHA256 | `ecc4bb6e885407d0ee15135ea1e8a09c63f2fa317f9800af79ca294ad2240f7b`；评审前后未变化 |
| production/test worktree | 本轮未修改 `src/**`、`tests/**`、`pyproject.toml` 或 `Makefile` |
| complexity report | `503/287/177/499/1326`；`51 reviewed / 0 unreviewed / 0 stale` |
| `make check` | Ruff/format通过；Pyright `0 errors`；complexity ratchet `9 passed`；全量 `843 passed`、coverage 100%；build/twine通过 |
| monorepo root scoped pre-commit | 对本文运行全部适用hooks，全部通过 |
| whitespace | `git diff --no-index --check /dev/null <review>` 无诊断 |

绿色基线证明当前仓库未漂移，不关闭 R1–R6，也不构成 production 授权。

## 14. 本次 review change unit

本次评审只新增：

```text
mote-kernel/docs/graph-explicit-activation-implementation-review.zh-CN.md
```

未修改被评审实施方案、production、tests、State、Store、protocol、persistence、complexity配置或其他用户文件。
