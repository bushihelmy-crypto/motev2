# Graph 输入绑定与显式执行激活解耦实施验收

> **结论：`CODE PASS / IMPLEMENTED / VERIFIED`；`ZERO-DEBT DELIVERY CLOSED`。**
> production实现严格落在批准的五文件manifest内，隐式data activation的type、field、lowering、runtime scan与三条
> dormant fallback已经完整删除；值绑定与显式control activation成为正交事实源。未发现blocker、major或minor。
> `make check`、聚焦测试、五个示例和实施清单scoped pre-commit全部通过。仓库级`pre-commit run --all-files`
> 与 implementation-owner writeback 均已通过：`py.typed` 基线已规范为空文件，方案实际状态已回写。因此代码验收与零负债的
> monorepo交付闭环均已完成。

## 1. 验收对象与边界

- 验收日期：2026-08-26
- 实施依据：[Graph 输入绑定与显式执行激活解耦实施方案](graph-explicit-activation-implementation.zh-CN.md)
- reviewed implementation-plan SHA256（实施目标版本）：`5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6`
- current implementation-plan SHA256（owner writeback 后）：`07f1f4b37ac6871fbea2348b959ff40379cc87e92a09d0507078440200b2693d`
- 批准评审：[第三次独立评审](graph-explicit-activation-implementation-third-review.zh-CN.md)
- production baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- production commit：**未创建**；本验收针对 baseline 上的当前 working-tree implementation diff
- implementation-owner writeback：**COMPLETE**（2026-08-26）；实施方案已回写为 `IMPLEMENTED / VERIFIED / ACCEPTED FOR INTEGRATION`，并记录实际 manifest、结构账本和门禁证据
- production authorization：**GRANTED FOR THIS VERIFIED CHANGE UNIT**；仅限 reviewed target 与五文件 production manifest
- monorepo EOF baseline：**FIXED**；`mote-infra/persistence/cloudflare/python/.../py.typed` 已规范为 0 字节 PEP 561 marker
- 本文只拥有代码验收裁决和验证证据，不拥有目标语义；方案实际状态由实施方案第 13 节唯一回写
- dirty worktree中原有README、其他实施文档、历史评审和未跟踪文件均保持用户所有权，不纳入本验收改写

## 2. Production manifest复核

实际production diff严格只有批准的五个文件：

```text
src/mote_kernel/execution/graph/compiler.py
src/mote_kernel/execution/graph/topology.py
src/mote_kernel/execution/engine/routing.py
src/mote_kernel/execution/engine/resume_admission.py
src/mote_kernel/execution/engine/recovery.py
```

| Owner | 验收结果 |
| --- | --- |
| compiler | inline复用`GraphValidationError`；explicit START优先于missing-control；允许binding/direct same-pair；删除data-target lowering和三条fallback |
| topology | 删除`DataTriggerPlan`与`FrontierTransitionPlan.data_triggers`，未增加替代field或compatibility property |
| routing | 删除publication-trigger scan、`RoutingFacts.data_targets`和ready-data merge；target只来自direct、selected conditional、completed join |
| resume admission | 只删除两处`facts.data_targets`消费，既有candidate和availability contract不变 |
| recovery | 只删除`_resolve_quiescent()`一处stale field read，没有新算法、State或recovery contract |

禁止路径均无diff：

```text
src/mote_kernel/execution/errors.py
src/mote_kernel/execution/facade.py
src/mote_kernel/state/**
Store / persistence / checkpoint / version / deployment / failover owners
```

## 3. 语义验收

### 3.1 唯一事实与基础设施复用

实现后的owner关系为：

```text
GraphDefinition.nodes[*].inputs
    = value source + exact type/readiness requirement

entries + direct/conditional/join declarations
    = activation eligibility

acknowledged publication frame
    = concrete value availability
```

`data_dependencies`与`activation_gates`仍只是一次compiler invocation内的typed local proof index；runtime只解释唯一
`CompiledGraph.transition`。没有新增reverse binding index、第二runner、hidden mutable state、public entry point或持久化truth。

### 3.2 值来源不要求与激活来源直接相连

以下行为均已由代码和测试证明：

- B读取A且声明`A -> B`：合法，同一pair的binding与edge分别lower为materialization和一个direct target；
- B读取A但由`coordinator -> B`激活：只要所有coordinator路径保证A先完成即合法，不要求`A -> B`；
- join(A, C)激活B且B读取A/C：合法，继续复用既有join和guaranteed-before proof；
- B读取A但由不能保证A完成的controller激活：保持compile error；
- B含`NodeOutputRef`但完全没有incoming direct/conditional/join gate：deterministic `GraphValidationError`；
- conditional未选择B的route时，A的publication不会泄漏激活B；`A -> END`也不会穿透到隐藏consumer；
- `set_outputs()`继续只是result projection，不要求虚假consumer edge。

因此这里约束的是“显式有向control路径保证值先可用”，而不是要求每个value producer与consumer直接连边。

### 3.3 Compiler删除闭包与错误顺序

三条旧隐式语义均已归零：

1. `_guaranteed_sets()`不再从data-only dependency传播activation guarantee；
2. `_validate_joint_activation_paths()`不再让no-control data requirement独立形成activation alternative；
3. `_input_publication_selection()`不再为`not gates`创建relative publication coordinate。

missing-control检查位于ordinary data cycle之后、automatic entry/reachability之前；explicit START dependency检查仍优先。
unknown/self source、unknown output、nested mismatch和data cycle不会被新错误遮蔽。首次compile失败不会安装
`_compiled_owner`，commit和node call均为零，补充合法edge后同一builder可以成功运行。

### 3.4 Runtime收口

`project_routing_facts()`当前顺序为：

```text
显式control target缺值 -> Abort
存在direct/selected-conditional/completed-join target -> Advance
仅剩partial join -> Deadlock
completion缺graph output -> Abort
否则 -> Complete
```

publication与materialization只提供value evidence，不再发现successor。resume admission与recovery继续共享同一个
`resolve_routing_facts()`结果，没有publication fallback、latest-value读取或第二activation路径。

### 3.5 唯一实施方案的 owner writeback（已完成）

代码、行为与唯一实施方案的实际状态均已完成闭环：

- 实施方案状态已更新为 `IMPLEMENTED / VERIFIED / IMPLEMENTATION-OWNER WRITEBACK COMPLETE / ACCEPTED FOR INTEGRATION`；
- 方案第 13 节已记录批准实施版本、实际五文件 production manifest、supporting files、实施前后结构指标、source deletion ledger、owner 边界与门禁结果；
- 本验收文档与实施方案保持交叉引用，但不复制目标语义，不形成第二规范事实源。

本项 writeback 已在不扩大 graph production scope、不触及 Kernel State/Store/persistence/failover 语义的前提下完成；
production commit 仍未创建，当前验收针对 Git `563a45124311f11e870d0627461102baeffdf7ad` 上的已验证 working-tree change unit。

## 4. 零负债与结构验收

active production/tests中的以下名称全部归零：

```text
DataTriggerPlan
data_triggers
data_targets
implicit_targets
publication_consumers
trigger_on_data
```

结构指标实际下降并已收紧ratchet：

| Metric | Before | After |
| --- | ---: | ---: |
| top-level definitions | 503 | 502 |
| type definitions | 287 | 286 |
| dataclass types | 177 | 176 |
| dataclass fields | 499 | 496 |
| decision points | 1326 | 1312 |

complexity health为`51 reviewed / 0 unreviewed / 0 stale`。只同步了routing行号移动后的既有reviewed identity，
没有提高limit、增加waiver、新helper、DTO、type、flag、index、alias或compatibility branch。

## 5. Tests、definitions与规范同步

tracked target manifest除五个production文件外，包含：

```text
pyproject.toml
README.md
README.zh-CN.md
docs/graph-node-input-output-contract-implementation.zh-CN.md
tests/architecture/test_graph_execution_ownership.py
tests/execution/engine/test_resume_admission.py
tests/execution/engine/test_resume_input_contract.py
tests/execution/engine/test_runtime_boundaries.py
tests/execution/graph/test_compiler_contract.py
tests/execution/test_continuation_integrity.py
tests/execution/test_graph_api.py
tests/execution/test_graph_recovery_contract.py
```

compiler/public tests覆盖canonical producer tuple、same-pair、conditional、END、join、coordinator、START优先级、
零node/commit副作用、compiled owner未安装以及失败后builder重试。依赖旧implicit activation的resume、nested、
continuation与recovery fixtures按业务意图补了显式control，没有新增recovery语义。

`example/graph/`在本次实施前已是未跟踪目录，Git不能提供可靠的per-file归因；本轮只验收当前内容，不声称拥有这些
用户文件。当前五个示例均已实际运行：linear与nested包含所需direct edges，parallel复用join，conditional复用route gates，
human-in-the-loop保持单automatic entry和output projection。

README双语说明与当前规范事实源均已同步“binding=value、edge=activation”；历史评审未被改造成第二规范事实源。

## 6. 验证记录

| 验证 | 结果 |
| --- | --- |
| 聚焦suite | **PASS**：compiler、resume admission、public API、ownership共`138 passed` |
| 五个example实际执行 | **PASS**：linear、nested、parallel、conditional完成；human-in-loop正确进入awaiting resume |
| `make complexity-report` | **PASS**：actual ratchet匹配；`0 unreviewed / 0 stale` |
| `make check` | **PASS**：Ruff/format；Pyright `0 errors`；complexity `9 passed`；全量`850 passed`、coverage 100%；build/twine通过 |
| target-manifest scoped pre-commit | **PASS**：从monorepo root运行全部适用hooks，全部通过 |
| `git diff --check` | **PASS**：无诊断 |
| implementation-plan SHA复核 | **PASS**：实施目标版本`5195194b…c87e3c6`；owner writeback后文档`07f1f4b3…0b2693d` |
| coordinator/conditional/join one-shot probes | **PASS**：间接coordinator取值、条件未选路由不执行consumer、join多源只激活一次 |
| implementation-owner writeback | **PASS**：实施方案第 13 节已记录实际交付状态、manifest、结构账本、source inventory与门禁结果 |
| production authorization | **PASS**：仅对本 verified change unit 与批准五文件 manifest 生效 |
| monorepo `pre-commit run --all-files` | **PASS**：全部 hooks 通过；`py.typed` 已从单换行规范为空文件 |
| monorepo all-files（隔离环境复核） | **PASS**：临时 cache 下全部 hooks 通过，未跳过任何 hook |

EOF 修复是用户明确授权的独立基础设施基线修正，仅删除 marker 文件中的一个换行；它不改变 persistence 语义，也未把任何
State、Store、schema、transaction、deployment 或 failover 行为混入 graph implementation change unit。

## 7. 最终裁决

```text
implementation findings:                 blocker=0 / major=0 / minor=0
production five-file manifest:            PASS
single truth / infrastructure reuse:      PASS
value-source / activation-source split:   PASS
non-direct coordinator binding:           PASS
fallback and compatibility deletion:      PASS
no persistence / no failover:             PASS
complexity / typing / tests / coverage:    PASS
implementation-scoped repository hooks:   PASS

production implementation code:           ACCEPTED / IMPLEMENTED / VERIFIED
implementation-owner writeback:           PASS / COMPLETE
monorepo all-files delivery gate:          PASS
overall delivery record:                  CLOSED
```

代码本身已经达到批准方案：增加或删除`NodeOutputRef`只改变value contract，增加或删除direct/conditional/join
declaration只改变activation topology；compiler在执行前证明二者联合可满足。当前没有代码整改项，两个交付阻断项均已解决。

## 8. 本次 acceptance change unit

本轮 closure change unit 包含：

```text
mote-kernel/docs/graph-explicit-activation-implementation.zh-CN.md       # implementation-owner writeback
mote-kernel/docs/graph-explicit-activation-implementation-acceptance.zh-CN.md
mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed  # EOF baseline normalization
```

graph production/test/README/normative implementation diff保持此前已验收的 manifest；未修改 State、Store、schema、transaction、deployment、failover 或其他未授权语义。
