# Terminal completion route / frontier proof 验收与 Code Review 指引（2026-09-12）

状态：**已修复独立 reviewer 的 P1/P2 findings，完整门禁已通过，等待独立复审**。

这份文档是给 reviewer/agent 的审查入口，不替代代码审查，也不替代
[`graph-mainline-simplification-governance.zh-CN.md`](./graph-mainline-simplification-governance.zh-CN.md) 中的治理总账。
治理总账仍是状态和候选项的唯一事实来源；本文只说明本次 terminal completion route 改动应如何沿完整调用链验收。

记录生成时的仓库事实：

- Kernel 工作目录：`/home/longert/motev2/mote-kernel`；记录时 `HEAD=562755e`；
- 本次 route 改动尚未单独提交；审查应针对当前工作树中列出的 route 相关路径；
- 工作树同时存在同事的 gateway、persistence、codec、`session.py` 等并发改动，这些不属于本文验收范围，不能因为它们的
  diff 变化而给本次 route 设计背书或定罪；
- 本文不扩大已有 Graph success/conditional route 语义，不验收新的持久化后端、crash-safe loader 或业务能力。

## 1. 审查范围

### 1.1 纳入本次验收的生产代码

| 路径 | 唯一职责 | reviewer 应核对的重点 |
| --- | --- | --- |
| `src/mote_kernel/execution/facade.py` | `Graph.add_node()` 的 `exported_routes` 声明与入口规范化 | 只在公开 facade 收集/校验声明；nested Graph 不接受父侧伪造的 route 域 |
| `src/mote_kernel/execution/graph/node.py` | `CallableNodeDefinition.exported_routes` 不可变声明 | 空集合与非空集合的语义是否清楚，是否没有运行时镜像状态 |
| `src/mote_kernel/execution/graph/compiler.py` | route domain lowering、terminal-frontier proof、nested domain 校验 | 所有静态事实是否由 compiler 一次生成；proof 是否保留真实异常边界 |
| `src/mote_kernel/execution/graph/topology.py` | `FrontierTransitionPlan.route_options`、`CompiledGraph.completion_routes` | 两个字段分别表示节点局部贡献域和图对外暴露域，不能互相冒充 runtime state |
| `src/mote_kernel/execution/graph/validation.py` | route/resource identity 的 definition 校验 | 非 canonical route 是否在定义边界拒绝 |
| `src/mote_kernel/execution/engine/routing.py` | contribution、settled ledger、completion transition 的运行时复核 | runtime 是 fail-closed 防线，不得另选一条 route 或重算静态拓扑 |
| `src/mote_kernel/execution/engine/recovery.py` | recovery 对 compiler route domain 的消费 | 不得回退到未声明 route、latest-value 猜测或第二 runner |
| `src/mote_kernel/execution/node_adapter.py` | typed node route 声明的唯一 lowering 接口 | typed 与普通 callable 是否进入同一 `CallableNodeDefinition` 形状 |
| `src/mote_kernel/hooks/node.py`、`act/node.py`、`observe/node.py`、`think/node.py` | 已有 domain Hook/业务节点声明其 terminal route 域 | 声明只在 assembly 时进入 Graph，不建立领域私有 routing 执行路径 |

### 1.2 纳入本次验收的测试

优先审查下列测试，它们锁定设计不变量而不只是 happy path：

- `tests/execution/graph/test_compiler.py`：terminal frontier、cycle exit、callable export、route domain 编译拒绝；
- `tests/execution/graph/test_nested_graph.py`：child/parent route domain、direct successor、Join 和 nested completion；
- `tests/execution/engine/test_routing.py`：贡献校验、settled ledger、completion route 和 transition admission；
- `tests/execution/engine/test_recovery_identity.py`：authoritative completed-child route 的 recovery projection；
- `tests/execution/engine/test_completion_projection.py`、`test_runtime_boundaries.py`：完成结果与运行时错误边界；
- `tests/execution/test_graph_facade_boundaries.py`、`tests/execution/test_typed_node_contract.py`：公开声明和 typed lowering 边界；
- `tests/architecture/test_graph_execution_ownership.py`：compiled lowering 和 owner 结构门禁。

其它测试文件可能同时包含同事的 Config/persistence 改造；reviewer 应按测试名称和调用链判断，不把测试数量当成设计证明。

## 2. 先看设计，再看门禁

审查顺序必须是：

1. 先确认静态声明、编译事实、动态状态和 recovery proof 各自只有一个 owner；
2. 再沿 `compile -> plan -> settle -> reduce -> commit -> project` 检查 route 的因果链；
3. 再检查异常类型、错误优先级和 nested/recovery 边界；
4. 最后用类型、测试、覆盖率、复杂度、架构和 pre-commit 证明没有回归。

复杂度热点只表示需要复审。不能为了降低 `_completion_routes()` 或 `_collect_control_topology()` 的局部指标，把 proof
拆成转发 helper、宽 context、镜像状态或第二执行路径。

## 3. 完整调用链

### 3.1 新建 Graph 与静态 lowering

```text
Graph.add_node(..., exported_routes=...)
  -> canonical route declaration
  -> CallableNodeDefinition.exported_routes
  -> GraphCompiler._collect_control_topology()
       -> conditional_targets
       -> transition.route_options
       -> nested child domain checks
  -> GraphCompiler._completion_routes()
       -> canonical reachable-frontier fixed point
       -> relative compiled Join progress
       -> exact successful-frontier route domain
       -> CompiledGraph.completion_routes
  -> immutable FrontierTransitionPlan / CompiledGraph
```

`route_options` 是每个节点在当前父图中可以贡献的完整 route 域；`completion_routes` 是整个已编译图在声明契约下真正可达的
successful frontier 可以向父 nested node 暴露的精确 route 域。二者都是 compiler 产物，不能被节点、scheduler 或 recovery
重新推导；早期出现但必然被后续 superstep 清除的 route 不属于 `completion_routes`。

### 3.2 正常执行与完成提交

```text
node operation
  -> Graph.SuccessOutcome(route)
  -> validate_routing_contribution()
  -> settled activation / routing facts
  -> _completion_route()                         # terminal frontier only
  -> CompleteGraphFrontier(completion_route)
  -> reduce_graph_run()
  -> transition_admission_error()                # replay previous frontier
  -> exact commit acknowledgement
  -> completed GraphRunState.completion_route
```

提交前的 `completion_route` 必须来自当前 settled terminal frontier；提交后它只保存在
`GraphRunState` 的完成态中。持久化或 Python snapshot 不得先于确认的 commit 更新。

### 3.3 Nested child 透传

```text
child terminal settlement
  -> CompleteGraphFrontier
  -> completed child GraphRunState.completion_route
  -> completed-child projection
  -> parent transition.route_options[child_node]
  -> parent conditional/direct/Join routing
```

completed child route 只使用 state-owned 值。尚未执行的 recovery 分支由 `_expand_live()` 在 compiler-owned
`route_options` 中逐项证明；一旦形成 completed child state，就不存在第二种“route evidence 未知”的完成态。

## 4. 唯一 owner 与不可变契约

| 事实 | 唯一 owner | 合法消费者 | 明确禁止 |
| --- | --- | --- | --- |
| callable 可导出的 terminal route 声明 | `CallableNodeDefinition`（由 Graph facade 规范化） | compiler | operation 自己登记第二份 route 表 |
| conditional edge 的 route 到 target 映射 | `FrontierTransitionPlan.conditional_targets` | compiler、routing | runtime 从字符串重新查拓扑 |
| 节点局部可接受贡献域 | `FrontierTransitionPlan.route_options` | compiler、routing、recovery | 用 `conditional_targets or (None,)` 形成 fallback |
| 图对外 completion domain | `CompiledGraph.completion_routes` | parent compiler | 写入 `GraphRunState` 或 frame 作为镜像 |
| 某次实际选择的 completion route | `GraphRunState.completion_route` | reducer、commit、parent projection | 从 output payload/latest value 猜测 |
| 一次调用内的派生 routing facts | `RoutingFacts` | 当前 invocation 的 routing/admission/recovery | 跨 revision、frame 或 work item 缓存 |
特殊 sentinel：`None` 只表示 ordinary no-route completion。它不是一个可由 recovery 随意补上的 route，也不是“未知”的别名。
Recovery 对尚未执行节点的所有选择直接来自 compiler route domain；completed snapshot 的实际选择只由
`GraphRunState.completion_route` 拥有。

## 5. 编译期验收规则

### 5.1 声明和节点形状

- `Graph.add_node()` 只接受 route 名称集合；字符串本身不被当作字符集合接受；重复 canonical route 立即报错；
- route identity 必须是非空、trim 后不变且无换行的 canonical 字符串；
- callable 没有 conditional edge、没有控制后继时，空 `exported_routes` 的域为 `{None}`；
- callable 有非空 `exported_routes` 时，它必须是 terminal callable；有 conditional edge、direct successor 或 Join source 时拒绝；
- conditional node 的域由 conditional edges 唯一决定，不能再用 `exported_routes` 叠加第二个域；
- nested graph 不允许父侧通过 `exported_routes` 覆盖 child 的 completion domain。

### 5.2 Nested domain 一致性

对一个 nested node：

- 有 parent conditional edges 时，child 的 `completion_routes` 必须与 parent conditional route keys 精确相等；缺失或多出的 label 都在 compiler 拒绝；
- 没有 conditional edges、也没有 direct/Join control successor 时，parent 直接继承 child 的 completion domain；
- 仍有 direct successor 或作为 Join source 参与后继时，child 只能暴露 ordinary `None`；任何非空 child route 都会在 compiler 拒绝，避免 route 被吞掉；
- parent 的 direct/Join 拓扑仍由既有 activation gate/reducer 处理，route declaration 不创建额外 successor。

### 5.3 Terminal-frontier proof

`_completion_routes()` 必须同时满足以下性质：

1. successful frontier 的发现、route 冲突判断和 `completion_routes` 生成由同一次 fixed point 完成，不存在并行的 terminal-event 扫描；
2. proof state 只有 canonical frontier node tuple 与相对当前 superstep 的 compiled Join progress；不复制业务值、runtime state 或 scheduler；
3. 下一 frontier 只由 `direct_targets`、一次 conditional route 选择和 `CompiledJoin` occurrence offset 产生；同一 target 出现两个 activation cause 时立即拒绝；
4. 只有在没有 successor、也没有剩余 Join progress 时才检查 completion route；因此早期 terminal event 是否被清除由实际 frontier 推进决定，不由全局最大 level 猜测；
5. 每次 frontier 推进都会形成新的 activation occurrence；循环回到相同 node 不会把前后两次 route 当作同一选择；
6. canonical proof state 是有限集合，cycle 通过 fixed point 收敛，不依赖 `max_supersteps` 或隐式 loop counter；
7. 同一最终 frontier 只要存在两个不同 route 的合法组合就在 compiler 报错；同一 node 的互斥 route 或不同 frontier 的 route 不误杀；
8. 没有任何可达 successful terminal frontier 时拒绝 definition；runtime deadlock guard 仍保留为外部 snapshot/command 防线。

推荐用下表逐项复核：

| 形状 | 预期结果 |
| --- | --- |
| 一个普通 terminal callable，无 route 声明 | 编译成功，图域包含 `None` |
| 一个 terminal callable 声明 `done` | 编译成功，图域包含 `done`；运行时必须返回 `SelectGraphRoute(done)` |
| 一个 source 的 `left/right` conditional edge | 编译成功；一次 activation 只能选择一个已声明 route |
| 两个独立 entry 在同一 terminal frontier 暴露 `left/right` | compiler 拒绝 `conflicting completion routes` |
| 两条 route 位于不同、可证明互斥的 control branch | 编译成功，图域保留所有可达 successful frontier 的 label 并集 |
| 早期 terminal 与 conditional tail 同层，tail 可直接结束 | 若早期 route 与直接结束 route 冲突，compiler 拒绝 |
| 早期 terminal 与 unconditional tail 同层 | tail 推进后清除早期 event，编译和运行成功 |
| cycle 中同一 node 的前后 occurrence 分别选择不同 route | 不合并 occurrence；若最终 frontier 冲突则 compiler 拒绝 |
| control cycle 没有静态成功出口 | compiler 拒绝 `no statically viable successful completion` 或 cycle-exit 错误 |
| child 暴露 `done`，parent 只声明 `other` | compiler 拒绝 child/parent domain mismatch |
| child 暴露非空 route，同时 parent 有 direct successor | compiler 拒绝 route 可能在 control successor 前被暴露 |

### 5.4 独立 reviewer findings 的闭环

- 原先按全局最大 absolute level 删除早期 event，会丢失“更晚节点只在部分 route 激活”的分支相关性；该算法已整体删除，
  conditional early exit 的公开 Graph 复现现在在 compile 阶段拒绝，unconditional tail 则只导出最终的 `None` route；
- 原先按 node 合并 route requirement，会把 cycle 中不同 superstep 的 activation occurrence 错当成同一次互斥选择；该 pairwise
  proof 已整体删除，fixed point 每推进一个 frontier 就自然形成新的 occurrence，reviewer 的 `{d:x, c:z}` 复现现在在 compile 阶段拒绝；
- `completion_route_known=False` 没有生产入口；相关 transfer/boundary/work-item/cycle-signature/nested-outcome 字段、unknown 分支和
  synthetic 私有测试已整链删除，completed child 只读取 authoritative `GraphRunState.completion_route`；
- Join source 去重、offset 对齐和 deadline 完整性由 `_compile_join_occurrence_plans()` 的 synchronized cohort / absolute coordinate
  构造保证，持久化运行时证据仍由 routing/state 边界独立复核；compiler proof 不再复制四个生产不可达的二次防御分支。

## 6. 运行时、提交和 recovery 验收规则

### 6.1 Runtime routing

- `validate_routing_contribution()` 先确认 node 和 contribution 是已知的 typed variant；
- conditional node 的 `ContinueGraphRouting` 必须拒绝，`SelectGraphRoute` 必须属于 `route_options`；
- terminal exported callable 的 `ContinueGraphRouting` 必须拒绝；
- 非 terminal node 不能用 non-`None` route 丢弃 direct/Join successor；
- settled activation ledger 的 route 必须属于编译域；未知 node、缺失 conditional route、non-terminal route 和 undeclared exported route
  保持各自错误文本/优先级，不被统一成模糊异常；
- `_completion_route()` 只接受 settled terminal frontier：无贡献或多个不同 route 都 fail closed；一个 frontier 的多个贡献必须产生同一 label。

### 6.2 Transition admission

`transition_admission_error()` 必须在 candidate 已完成时回放 `previous_state` 的控制决定，并同时核对：

- 没有被丢弃的 successor 或 partial Join progress；
- consumed Join occurrence 与 command 完全一致；
- command 的 `completion_route` 与 previous terminal frontier 计算结果完全一致；
- candidate 的错误 state 不会因为 completion 字段而绕过 admission；
- candidate/previous 是不同 authoritative snapshot，不能跨 snapshot 复用 invocation-local evaluation。

### 6.3 Recovery

- `_expand_live()` 只遍历 `graph.transition.route_options[node_id]`；没有默认 `(None,)` fallback；
- `_completed_child_outcomes()` 只透传 completed `GraphRunState.completion_route`；
- `RecoveryTransferState`、scope boundary、work item 和 cycle signature 不保留不可达的 known/unknown route mirror；
- recovery 不读 output payload 猜 route，不创建第二 scheduler/runner，不直接修改 `GraphRunState`；最终仍通过既有 reducer command 投影；
- live routing 与 recovery 可以保留不同错误边界，但不能有不同的 route universe。

## 7. 独立 reviewer 的检查清单

### 7.1 设计和 owner

- [ ] `Graph` 仍是唯一公开 graph facade；没有新增 domain runner、route runner 或第二 reducer。
- [ ] `GraphRunState.completion_route` 是实际选择的唯一 owner；`CompiledGraph.completion_routes` 只是静态域，不是 state mirror。
- [ ] `RoutingFacts` 仅为一次调用的只读投影；没有跨 revision/frame/work item 缓存。
- [ ] `route_options` 与 `conditional_targets` 的差异是“局部贡献域”与“route->target 映射”，不是两个同义 owner。
- [ ] 没有宽 `RunContext`、兼容 alias、旧 API wrapper、latest-value route 推断或第二执行路径。

### 7.2 Compiler proof

- [ ] successful frontier、Join source、direct successor 和 conditional route 的推进只由同一次 fixed point 处理。
- [ ] finality 来自 canonical frontier fixed point，没有全局 `final_level` 剪枝或 gate-pair 替代路径。
- [ ] Join progress 使用 compiler 已生成的 occurrence offset，并以相对坐标进入 proof identity。
- [ ] repeatable node 的不同 activation occurrence 不共享 route requirement。
- [ ] 同一 terminal frontier 的 route 冲突在 compile 阶段拒绝；不同 frontier 的合法互斥 route 不被误杀。
- [ ] cycle exit 校验不依赖执行次数或 `max_supersteps`。

### 7.3 Nested / runtime / recovery

- [ ] child/parent domain mismatch、child route + parent successor 的错误都在 compiler 发生。
- [ ] wrong route、missing conditional route、non-terminal route 和 unsupported contribution 在 runtime 保持明确错误边界。
- [ ] completion command 不能伪造 route；transition admission 会重放 previous frontier。
- [ ] recovery future branch 只枚举声明域；completed child 只透传 state-owned route，没有 unknown-route 状态。
- [ ] 所有 state 改变仍经过纯 reducer 和确认后的 commit。

### 7.4 代码质量

- [ ] 新增 proof 的复杂度来自真实 terminal-frontier 算法，而非重复扫描、转发 helper、防御性镜像或 terminal-event 第二事实。
- [ ] 注释描述实际不变量和异常边界，没有把 conservative proof 写成“运行时保证”。
- [ ] 本次 route diff 没有新增 `# pyright` 抑制、`Any`、裸 dict 边界或字符串 discriminator。
- [ ] 测试覆盖每个拒绝条件和每个成功 route 形状，而不是只断言指标下降。

## 8. 可复现实证

在当前 Kernel 工作目录执行：

```bash
cd /home/longert/motev2/mote-kernel

# route 主链定向回归
python -m pytest -q \
  tests/execution/graph/test_compiler.py \
  tests/execution/graph/test_nested_graph.py \
  tests/execution/engine/test_routing.py \
  tests/execution/engine/test_recovery_identity.py \
  tests/execution/engine/test_completion_projection.py

# 完整 Kernel 门禁（类型、复杂度、覆盖率、架构、build/package-check）
make check

# 结构报告；指标只用于定位，先审设计再审数值
make complexity-report

# 仓库级门禁（在 monorepo 根目录）
cd /home/longert/motev2
pre-commit run --all-files
```

本轮已经得到的结果：

| 证据 | 结果 |
| --- | --- |
| Graph/compiler/routing/recovery 定向测试 | `182 passed` |
| 全量 Kernel pytest | `3031 passed` |
| 行覆盖率 / 分支覆盖率 | `100.00% / 100.00%` |
| Strict Pyright | `0 errors, 0 warnings, 0 informations` |
| complexity ratchet | `22 passed` |
| zero-debt complexity health | `PASS` |
| build 与 package check | `PASSED` |
| monorepo `pre-commit run --all-files` | 全部 `Passed` |
| `git diff --check` | 通过 |

terminal proof 的精确指标变化已写入 `pyproject.toml` 的 ratchet；没有提高最大圈复杂度、最大认知复杂度或 zero-debt health
上限。指标增长不能替代上述 owner/proof 审查，也不能单独作为拒绝理由。

## 9. 通过标准与签字模板

只有同时满足以下条件才能标记通过：

1. 静态 route domain、动态 selected route 和 invocation-local facts 的 owner 分离清晰；
2. compiler 能拒绝非法 terminal frontier、非法 cycle exit、non-terminal export 和 nested domain mismatch；
3. runtime/recovery 只消费 compiler 事实，保留各自异常边界，不引入 fallback 或第二执行路径；
4. reducer/commit 的原子边界和现有 Graph/State 行为没有回归；
5. 类型、测试、覆盖率、复杂度、架构、构建和 pre-commit 门禁全部通过；
6. reviewer 没有发现为“化简指标”而牺牲唯一真相、可读性或错误优先级的改动。

Reviewer 可在此留下结论：

```text
审查范围：terminal completion route / terminal-frontier proof
设计与 owner：PASS / REQUEST CHANGES
compiler proof：PASS / REQUEST CHANGES
runtime / recovery：PASS / REQUEST CHANGES
门禁证据：PASS / 未运行（说明原因）
阻塞 finding：无 / <列出文件、符号、行为和复现>
最终结论：ACCEPT / REQUEST CHANGES
Reviewer：
日期：
```

任何 reviewer finding 都应写出完整调用链、可触发输入、实际错误或状态偏差，以及为什么现有测试没有覆盖；仅凭文件行数、
单个热点数或理论上的极端网络攻击假设，不足以否定本次验收。
