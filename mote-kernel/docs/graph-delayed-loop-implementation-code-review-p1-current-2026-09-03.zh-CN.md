# Graph 延迟回环 P1 当前代码评审（2026-09-03）

状态：**P1 设计与代码审查通过；完整交付门禁通过**

评审对象：当前工作树中的 Graph 延迟反馈回环 P1，包括 declaration/compiler、compiled topology、
`GraphRunState`/reducer、live routing、state-led recovery、nested family driver、公开 facade 边界、测试、示例和全部质量门禁。

本文是新的独立评审台账。旧评审文档不修改、不覆盖；本轮只记录由当前代码、确定性复现或门禁输出支持的事实。
当前工作树同时包含 events、failover、logging 和 monorepo 其他目录的用户改动，本评审不改写、不归因这些无关改动。

## 1. 最高判定标准

1. 先审代码结构本身是否满足唯一真相、基础设计复用、完整调用链最简、一次性迁移、零技术债和可直接读懂；
2. 再审 P1 声明、编译、执行、State 提交与恢复是否共享同一规则且保留各自异常边界；
3. 最后以类型、测试、覆盖率、复杂度、架构和 pre-commit 门禁证明没有回归；门禁通过不替代设计判断；
4. legacy 测试只迁移仍有价值的语义、错误优先级和恢复边界，不允许生产代码保留旧 API alias、wrapper 或第二执行路径；
5. 复杂度指标只触发真实调用链复审，不以拆薄 helper、宽 context 或状态机碎片化换取数字下降。

## 2. 冻结的 P1 边界

- P1 只证明进程内 typed direct self-feedback：单 scope、单 callable target、一个 feedback input、
  `GraphInputRef` seed、target 自身 publication repeat、一个 feedback route、一个 terminal route和一个 graph output；
- 普通 data self-cycle、nested feedback、generic conditional feedback、multi-feedback、cyclic Join 和公开 durable feedback API
  必须保持关闭；
- Graph 不拥有失败重试；Failover 是唯一 retry owner；
- 当前 frontier 的 Pending sibling 必须排空，终局优先级为 `Pending > Failed > Interrupted > Settled`，Failed 不可
  resume/skip/retry；
- P2 才补 durable evidence reader、原子 State+publication commit、retention/release、codec、跨语言 conformance 和公开
  durable API。本评审不会把这些 P2 能力倒灌为 P1 缺陷，但 P1 已有的 state-led 入口必须在调用 callable 前 fail closed。

## 3. 上轮阻断项回归台账

| 项目 | 当前状态 | 本轮证据 |
| --- | --- | --- |
| historical Join arrival 必须来自 authoritative settlement/selected route | 已核对通过（真实 reducer/live 路径；持久化信任边界见 3.2） | `settle_graph_node()` 只在成功提交时写入带 route 的 `ActivationReference`；`_validate_join_progress_delta()` 只允许保留旧 arrival 或追加当前 settled success；`test_advance_rejects_injecting_a_historical_join_arrival`、`test_routing_snapshot_rejects_join_progress_without_settlement_evidence` 通过 |
| direct self-feedback 与普通 routed cause 必须命中真实前驱和 compiled gate | 已核对通过 | `require_feedback_activation_cause()`、`_frontier_gate_error()` 和 `_post_advance_error()` 在 claim/routing 前检查紧邻前驱、route、settlement evidence 与唯一 gate；`test_self_feedback_rejects_a_forged_routed_predecessor`、`test_feedback_admission_requires_committed_predecessor_evidence` 通过 |
| 同一 Join source 的不同 activation occurrence 不得静默拼接 | 已核对通过 | compiler 的 `_reject_cyclic_joins()` / `_reject_repeatable_join_sources()` 关闭 P1 无 occurrence identity 的拓扑，routing 仍对重复 source fail closed；`test_join_rejects_a_second_activation_occurrence_of_one_source`、`test_compiler_rejects_cross_superstep_join_without_occurrence_identity` 通过 |
| 非法 target、零/多 gate、漏 successor、非法 START 必须 fail closed | 已核对通过 | `frontier_admission_error()` 统一检查 graph membership、START/routed cause、compiled gate 与完整 successor；`test_post_advance_rejects_unmatched_and_unexpected_successors`、`test_frontier_admission_rejects_unknown_and_provenance_inconsistencies` 通过 |
| `join_progress` 只能保留、合法新增或在同次完成时消费 | 已核对通过 | `_validate_join_progress_delta()` / `_validate_join_consumption()` 强制 old partial 保留、当前成功追加、合法完成后消费；`test_advance_rejects_dropping_unrelated_partial_join_progress`、`test_advance_consumes_partial_join_progress_only_with_the_complete_join_activation` 通过 |

### 3.1 本轮已复核的可复核事实（2026-09-03）

- 针对 P1 compiler、live routing、State transition/validation 的回归集已实测通过：
  `tests/execution/graph/test_feedback_compiler.py`、
  `tests/execution/test_feedback_runtime.py`、
  `tests/execution/engine/test_routing.py`、
  `tests/state/graph_state/test_execution_transitions.py`、
  `tests/state/graph_state/test_state_validation.py`，共 **212 passed**（0.27s）。
- `git diff --check` 对 Graph/State/本评审文档相关路径无格式错误。
- 上述结果与后续 recovery/family 复核共同证明当前生产调用链的设计约束和回归行为；它们不把恶意手工 State 自动升级成生产缺陷，信任边界见 3.2。
- 当前实施计划的预写数字，以及旧 acceptance/re-review 文档的历史状态，都不替代本轮代码和命令实测；旧文档保持不修改。

### 3.2 边界审计：历史 ledger 的真实性依赖持久化信任模型（暂不作为阻断）

**状态：条件风险，暂不单独判定 P1 不通过。**

复现方式（只使用当前仓库代码；脚本在 `/tmp/p1_probe.py`，未修改生产代码）：

1. 直接构造一个合法形状的 `GraphRunState`：`superstep=1`、当前 frontier 只有 `target`，其 routed cause 指向 `source@0`；
2. 将同一个坐标复制到 `settled_activations`，但本次恢复进程从未执行 `source`；
3. `require_snapshot_matches_graph()` 返回成功，`frontier_admission_error()` 返回 `None`；
4. 通过公开 `Graph.run(state=forged_state)` 运行一个无输入的 `source -> target` 图，结果为 `_CompletedGraphResult`，调用计数为
   `{'source': 0, 'target': 1}`。

同样的伪造对 `a,b -> Join(c)` 成功：`a`、`b` 均未执行，仅把两条坐标放入 ledger 和 `c` 的 routed cause，公开 state-only recovery 仍执行 `c`（计数 `{'a': 0, 'b': 0, 'c': 1}`）。

最小实测输出：

```text
public forged state admission RETURNED None
public forged state run RETURNED _CompletedGraphResult {'source': 0, 'target': 1} 1
public forged join admission RETURNED None
public forged join run RETURNED _CompletedGraphResult {'a': 0, 'b': 0, 'c': 1} 1
```

根因在 `state/graph_state/validation.py` 的 `_validate_settled_activations()` 和
`execution/engine/routing.py` 的 `_post_advance_error()`：历史 entry 只验证坐标、唯一性和当前引用关系，未验证其来自已提交的
`SettleGraphNode`/publication provenance。`reduce_graph_run()` 不接收 compiled topology，因此真实性检查也无法由 reducer 单独完成。
这不是把任意手工构造对象自动视为生产攻击：当前架构把持久化 State 视为 authoritative，P2 才补 durable evidence reader 与原子
State/publication commit。因此本轮将它记录为**必须明确的信任边界和测试盲区**，而不是仅凭 hostile snapshot 直接退回 P1。
只有当实际 persistence adapter 可以写入/恢复未由 reducer 产生的 State，或 acknowledgement-loss 重建路径确实能生成这种 ledger 时，
它才升级为阻断；届时应在 reader/admission 统一验证 commit-linked provenance，而不是增加兼容层。

另一个边界反例（同样是信任边界审计，不单独作为阻断）：历史 ledger 混入 canonical 但不属于 compiled graph 的 `ghost` activation 时，
`frontier_admission_error()` 直接抛出原始 `KeyError('ghost')`（来自 `_post_advance_error()` 对
`graph.transition.conditional_targets[source]` 的索引），而不是统一的 `InvalidExecutionSnapshotError`/
`InvalidRoutingCommandError`。这既不能向调用者表达“快照拒绝”，也说明历史 ledger 的 graph-membership 校验缺口独立存在。

**P1 补防（2026-09-03）**：这一项已经在当前实现中收口。compiled-graph admission 现在会先扫描完整
`settled_activations` ledger，拒绝不属于当前图的节点、条件节点未声明的 route，以及普通节点携带 route 的记录；
`Graph.run(state=...)` 和 routing 都在调用 callable 前走同一条 typed admission 边界，未知节点不再泄漏原始 `KeyError`。
对应回归覆盖了 ledger-only ghost、非法历史 route、直接 routing 和公开 state recovery（callable 调用数为零）。
这只解决“记录不属于图”的确定性拓扑问题；图内坐标但没有真实提交 provenance 的伪造，仍按上文约定留给 P2 的
commit-linked durable evidence reader，不用可复制的 `verified` 标记冒充证明。

## 4. 设计与代码审查记录

### 4.1 唯一 owner 与完整调用链

- declaration/compiler 的 owner 是 `compile_graph()`：它一次性生成 immutable `CompiledGraph`，其中集中保存 node、direct/
  conditional/Join successor、activation gate、materialization 和 publication selection。运行时不再重新声明同一规则。
- `Graph` 是唯一公开 facade；调用链是 `Graph.run()` → owner-local `_GraphRun`/`GraphExecutor` →
  `prepare_superstep()` → planner/frontier/materialization/session → typed settlement command →
  `reduce_graph_run()` → settled frontier 的 `resolve_routing_facts()`。没有私有 runner 或平行 public entry point。
- `GraphRunState` 是唯一 runtime snapshot owner，`reduce_graph_run()` 是唯一状态转换入口。cause、settlement、Join progress、
  success ledger 和执行位置在同一 State/同一 revision 边界内，不另设 feedback state 或 recovery state 真相。
- live 与 recovery 复用同一 compiled plan、`plan_tasks()`、resource admission、settlement projection 和 reducer；
  `preflight_recovery()` 只做无副作用的有界 proof，不执行 callable、不提交第二份状态，也不成为第二 runner。

### 4.2 cause、gate 与 feedback 边界

- `StartGraphRun` / `AdvanceGraphFrontier` 只携带完整 `GraphFrontierActivation`；首轮只能 `StartActivationCause`，后续只能
  `RoutedActivationCause`。routing 在 target collapse 前保留每个 `(target, cause)` candidate，direct、conditional、Join
  和 P1 feedback 统一要求恰好一个 compiled gate。
- Join arrival 保存完整 `ActivationReference`（activation 坐标和 selected route）。旧 partial 只能按差分规则保留或追加，
  完成时由同一批事实消费；P1 对 cyclic/repeatable occurrence 拓扑直接 compile fail，是明确的能力边界，不是为了降复杂度
  人为拆出的路径。
- direct self-feedback 的 compiler 白名单为单 scope、单 callable target、一个 `GraphInputRef` seed、target 自身
  publication repeat、一条 feedback route、一条 terminal route和一个 graph output；普通 `NodeOutputRef` self-cycle、
  generic/nested/multi-feedback 和 cyclic Join 继续关闭。
- feedback materialization 只读取 State cause 指定的紧邻前驱 publication，不回退 seed、不扫描“最新 publication”，也不接受
  input override；override 被拒绝是 P1 的异常边界，不是兼容缺口。

### 4.3 State、提交顺序与 recovery admission

- `_GraphRun._transition()` 先由 reducer 生成 candidate 并执行 frontier admission；`commit_transition()` 随后才把 typed transition
  暴露给 commit port。只有 commit port 返回 exact authoritative successor 才替换 Python snapshot 和 frame；异常时不把未确认
  candidate 当成已提交事实。
- `Pending > Failed > Interrupted > Settled` 在 frontier/model、reducer、live session 与 recovery proof 中一致。Graph failure 是
  terminal；failed-node resume/skip/retry 路径已删除，Failover 保持唯一 retry owner。
- nested child 的 missing/active/awaiting readiness 按节点处理；普通 callable/resource sibling 仍可 claim。只有普通 Pending 排空
  且 Failed 已成为 terminal disposition 后，才清理 awaiting child。live driver 与 recovery proof 对顺序、scope 和诊断边界一致。
- continuation admission、recovery preflight 和 callable 前 materialization 都先验证 scope/run、cause、publication/frame、
  child boundary 与 exact successor；无法证明时在 claim 前 fail closed。

### 4.4 复杂度与可读性复审

复杂度工具命中的热点仅作为高召回复审入口。本轮沿完整调用链核对后，未发现需要通过薄转发 helper、宽 context、重复 manager 或
第二状态机才能解释的真实缺陷；保留 recovery fixed-point/worklist 是因为它表达有界 proof 的必要状态。没有为满足数字而再拆分
owner 或制造兼容路径。

## 5. 测试、示例与迁移审查记录

### 5.1 测试覆盖

- compiler/runtime/routing/State 定向回归集实测 **212 passed**；recovery 相关集 **96 passed**；核心 recovery/family/graph
  contract **101 passed**；`tests/execution/test_graph_examples.py` **16 passed**。
- 综合 P1/recovery/continuation 集 **125 passed**，recovery contract + mixed child recovery **25 passed**；本轮再跑的
  recovery/continuation/mixed 选择集为 **109 passed, 52 deselected**。
- 额外反馈恢复探针确认 acknowledgement-loss 后，缺 publication frame 时 `pending_node_input_available()` 为 false，
  不会错误 claim；正常 feedback 恢复调用序列为 `[0, 1, 2]`。
- State transition、routing、materialization、failure priority、resource waiter、nested sibling、partial commit 和 exact
  acknowledgement 均有确定性测试；测试只验证有价值的语义、错误优先级和恢复边界，没有为 legacy API 保留生产分支。

### 5.2 示例与一次性迁移

- `example/graph/README.md`、`polling_loop` 和 `partial_commit_recovery` 已按现行 interrupt/terminal-failure 语义更新；
  `partial_commit_recovery` 使用精确 interrupt identity 和 typed codec，不再示范 failed-node 替换。
- `same_input_retry.py`、`retryable_payment.py`、`skip_failed_delivery.py`、`skip_failed_route.py` 及对应 README 入口已删除；
  public facade、request/result、recovery admission 中没有旧 API alias、wrapper 或第二执行路径。需要重试时由显式拓扑/Failover
  表达，而不是污染 Graph 状态 owner。
- 旧评审文档和跨项目用户改动均未修改、未归因；本台账是当前代码的独立记录。

## 6. 门禁实测

### 6.1 本目录 `make check`（2026-09-03）

已实测完成并通过：

```text
ruff check / ruff format --check       PASS（234 files）
pyright                                 PASS（0 errors, 0 warnings）
complexity ratchet + semantic index    PASS（22 passed）
complexity health                       PASS
pytest + coverage                       1323 passed；6980 statements / 2406 branches；100.00%
build --no-isolation                    PASS（sdist + wheel）
twine check                             PASS（2 artifacts）
```

复杂度实际值与当前配置上限相等（cognitive complexity 2794、max cyclomatic 78、max cognitive 131、hotspots 72）；这只是
门禁事实，不是“提高 ratchet 后设计自动合格”的证明。根目录 `pre-commit run --all-files` 已实测通过；Cloudflare
TypeScript Persistence 因迁移目录没有匹配文件按规则跳过，其余 hook 全部通过。

## 7. 最终结论

**结论：P1 设计与生产代码 review 通过；本目录 `make check` 全部通过。**

本轮没有发现新的、能改变真实生产调用链结果的 P1 缺陷；3.2 中 ghost 的拓扑准入缺口已补齐，仍需保留的是“图内坐标但没有真实
提交 provenance”的条件信任边界。它依赖 P2 的 commit-linked durable evidence reader 与 State/publication 原子提交，不能用
可复制的 State 字段替代。P2 的 retention/release、codec、跨语言 conformance 和公开 durable feedback API 不属于本次 P1 判定。

完整交付门禁现为**无条件通过**：本目录 `make check` 与 monorepo 根目录 `pre-commit run --all-files` 均已实测通过。
工作树中 Cloudflare 迁移改动仍未纳入本次 kernel 提交。
