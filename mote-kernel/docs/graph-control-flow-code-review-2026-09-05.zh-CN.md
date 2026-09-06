# Graph ordinary control-flow 改造代码评审（2026-09-05）

状态：**代码 review 通过；本轮未执行测试和门禁**

本记录对应当前工作树中尚未提交的 Graph 增量。评审遵循唯一真相、复用既有 compiler/planner/routing/reducer/typed frame、逻辑最简和
不做代码攻击式扩张的原则；先判断设计和真实调用链，再谈门禁证据。

## 1. 范围和排除项

纳入本轮的生产改动只有：

- `src/mote_kernel/execution/graph/compiler.py` 中 `_ControlFlowProof`、ordinary event-pair 闭包、Join 影响边界，以及它在
  activation-gate / feedback partition / activation-rule 编译中的接入；
- `tests/execution/graph/test_compiler.py` 中对应的编译器回归。

当前工作树中的 failover、port、其它文档和测试改动不属于本轮 Graph review。`HEAD` 已有的 atomic `GraphTransition` / write-set /
exact acknowledgement 设计沿用此前的 Graph 状态评审结论，本轮只确认新增 compiler 证明没有绕开该边界。

明确不把以下内容列为本轮 finding：

- 纯理论攻击面、hostile 内部对象或手工伪造 State；
- 当前未承诺的 persistence adapter、durable loader/reconcile，以及 crash-safe durable concrete-value recovery；
- 仅由复杂度指标命中的拆 helper 建议；
- `port` 改动和与本增量无关的并行迁移。

## 2. 实际调用链

```text
公开 Graph facade
  -> compile_graph()
       -> route requirement proof
       -> _control_flow_proof()（仅 compiler owner）
       -> immutable CompiledGraph.transition.activation_gates / activation rules
  -> Graph execution planner / materialization
  -> routing._resolve_control()
       -> 保留每个 (target, cause) candidate
       -> 唯一 candidate 后生成下一 frontier command
  -> reducer / GraphTransition
  -> Graph.Commit(exact candidate)
       -> 确认后才前移 GraphRunState 和 typed frame
```

`_ControlFlowProof` 不创建 runtime runner、State 字段、缓存或第二条 routing 路径。runtime 仍只消费 compiler 已生成的 gate、cause、
Join occurrence 和 publication selection。

## 3. 新证明的不变量

1. `_ControlEvent` 是一个 concrete node activation 加其 conditional route（非 conditional node 使用 `None`）。
2. `_ordinary_event_successors()` 与实际 `_resolve_control()` 保持同一规则：一个 conditional activation 只选一条 route，但该
   activation 的 direct successors 全部保留。
3. `_control_flow_proof()` 从 initial entries、单 activation fan-out 和已共存 event pair 的 successor Cartesian product 计算
   有限 fixed point。它表达的是 ordinary 同步 frontier 的 pair projection，而不是把每条入边机械当成独立 START。
4. 同一 node 的 event pair 被丢弃符合 `GraphRunState` 的 frontier node-identity 唯一不变量；真正的两来源 convergence 仍会在目标
   activation-gate 检查处被捕获。相同 source 的不同 conditional route 仍由单次 route selection 互斥。
5. Join target 及其 ordinary descendants 由 `_join_affected_nodes()` 标成 unknown。涉及这些节点时回退既有保守 gate 逻辑，不用
   ordinary proof 猜测 pending Join arrival、occurrence 或 completion。
6. 所有集合、successor 和 event option 都按 canonical node/route 顺序展开；proof 本身不引入声明顺序或运行时顺序依赖。

## 4. 审查台账

| 检查项 | 结论 | 依据 |
| --- | --- | --- |
| 同一 activation 的 direct fan-out 与 conditional route | 通过 | `_ordinary_event_successors()` 同时保留 direct targets 和选中的 conditional target；与 routing 的 candidate 生成一致 |
| 同一条件节点的不同 route | 通过 | event pair 不把同一 node 的不同 route 当成并发；gate 层仍拒绝 direct+conditional 重复 candidate，独立 route 只允许一次选择 |
| sequential conditional branch 回到 shared node | 通过 | 新增 `test_sequential_conditional_branches_may_return_to_one_shared_node`；不同轮次 source pair 不被误判为同一 frontier 并发 |
| 独立 entry 与 branch 回到同一 target | 通过 | 新增 `test_shared_node_still_requires_join_for_an_independent_entry`；可共存 source pair 仍要求显式 Join |
| 不同 path length 的 convergence | 通过 | 新增 `test_different_path_lengths_do_not_create_a_same_frontier_collision`；只在实际同步 frontier 重合时判定冲突 |
| same-source conditional routes 共享 target | 通过 | 新增 `test_same_source_conditional_routes_may_share_one_target`；复用单 source route 互斥，不生成第二执行路径 |
| Join 边界 | 通过 | Join target 和 ordinary descendants 均回退 conservative；multi-source gate 也不会被 singleton proof 放行 |
| cyclic ordinary flow | 通过 | pair worklist 在有限 event universe 上收敛；不增加 loop counter 或隐式 State |
| feedback gate partition | 通过 | initial/repeat gate 继续共享同一个 compiler proof；不可证明或 Join-affected 形状仍按原边界拒绝 |
| routing / reducer / commit owner | 通过 | 本增量没有修改 State、reducer、candidate collapse 或 `Graph.Commit`；exact candidate 确认边界保持唯一 |
| determinism / idempotence | 通过 | 新增排序只用于 proof 遍历；既有 `test_compiling_the_same_definition_is_idempotent` 保持覆盖 |
| 复杂度与代码结构 | 通过 | pair closure 是本次“同一 frontier”语义所需的单一 compiler 证明；没有因指标命中而拆成薄转发 helper、宽 context 或第二 owner |

## 5. Findings

本轮没有发现可由正常公开 `Graph` 调用触发、且会改变正确性、异常边界、State 唯一性或执行结果的真实问题。

特别核对但未升级为 finding 的点：

- pair projection 不保留同 node pair，不会绕过 convergence gate；有效 frontier 本身禁止同一 node identity 重复；
- ordinary proof 不读取 Join edge 的运行时 pending 状态，但所有 Join 产生的 target/下游都显式进入 conservative boundary；
- 证明可能对相关路径做保守 over-approximation，最多收紧未能证明的图，不构成错误放行；没有看到正常公开调用链中的 under-approximation；
- 新增约束没有改变 GraphTransition 的原子提交、失败停留旧快照或 frame 确认后前移语义。

## 6. 测试和持久化状态

按本轮明确要求，评审完成后**未运行测试、覆盖率、`make check` 或 pre-commit**。因此本文不把历史门禁输出冒充当前增量的门禁证明；
本记录只给出静态代码 review 结论。

具体 persistence 实现、durable concrete-value recovery 和 crash-safe restart 仍未实现，后续由持久化适配器单独负责；它们不属于本轮
ordinary control-flow 改造的阻塞项，也没有被本增量偷偷承诺。

## 7. 最终结论

**Graph 新增 ordinary control-flow proof 通过代码 review，无需修改生产代码或测试；本轮未发现真实 finding，至此停止扩张审查。**

这表示设计和调用链满足当前内存语义的 review 判断，不等于本轮重新取得全部门禁证据；提交前若需要完整门禁证明，应在合适的干净工作树中
另行执行既定检查。
