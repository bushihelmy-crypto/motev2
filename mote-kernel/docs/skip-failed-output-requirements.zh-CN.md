# `skip_failed` 可选替代输出需求

## 1. 文档信息

- 状态：Implemented / verification complete
- 日期：2026-08-17
- 公共入口：`mote_kernel.execution.Graph.skip_failed()`
- 本文定义最终行为、边界与验收条件；对应实施设计已评审通过，生产实现与交付门禁已完成验证。

## 2. 背景

当前 `skip_failed()` 允许失败节点贡献控制路由，但不产生 output publication。如果所选下游或 graph outputs 仍依赖该节点输出，skip 会先提交，随后因缺值进入 abort。

本需求解决两个问题：

1. 必然无法满足数据依赖的 skip 应在 durable mutation 前拒绝。
2. Map/Reduce 等容错场景应允许调用方在不重跑失败节点的情况下提供合法降级结果。

## 3. 目标 API

只保留一个 `skip_failed()`，增加可选 `output`：

```python
graph.skip_failed(
    "map_b",
    "operator accepted failure",
    route="continue",
    output=Graph.values(result=fallback_result),
)
```

目标签名：

```python
def skip_failed(
    self,
    node_id: str,
    reason: str,
    *,
    route: str | None = None,
    output: Graph.Values[GraphValueT] | None = None,
    scope: tuple[str, ...] = (),
) -> Graph.ResumeAction[GraphValueT]: ...
```

不新增 `skip_failed_with()`、`substitute_failed()` 或兼容别名。

## 4. 核心行为

### 4.1 提供 `output`

Kernel 必须：

1. 验证目标节点当前为 failed。
2. 按失败节点自己的 compiled output descriptor 做 exact admission：不得缺 key、多 key或类型不匹配。
3. 独立验证 route 合法性。
4. 在任何 durable mutation 前完成全部校验。
5. 提交既有 skip State transition。
6. 只有 commit 精确确认 reducer successor 后，才安装替代 publication。
7. 下游继续通过 `Graph.node_output(node_id, output_name)` 读取，不建立第二取值路径。
8. 替代 publication 必须建立与 execution publication 相同的数据 availability，并激活由该节点 output 建立的 data target。
9. State settlement 仍为 `SkippedGraphNode`，control routing 仍只来自该 skip settlement 的 routing；不得把节点伪装为 execution success。
10. 失败节点不得重新执行。

### 4.2 未提供 `output`

Kernel 必须在提交前证明所选 continuation 不需要该节点输出。证明范围至少包括：

- direct/conditional control target；
- 已满足或将满足的 join target；
- data target；
- graph completion outputs；
- nested boundary；
- 同一 resume 调用中其他 action 对候选 frontier 和 value availability 的影响。

若任一必达 consumer 或 graph output 需要该节点 publication，必须抛出 `Graph.ValueUnavailableError`，并保证：

- 不提交 `ResumeGraphNodes`；
- State 保持原 `FailedGraphNode`；
- 不创建 `SkippedGraphNode`；
- 不推进 frontier；
- 不产生 publication。

若所选 continuation 完全不依赖该节点输出，则保持现有无输出 skip 行为。

纯 skip 不贡献该失败节点的 data trigger。仅存在由 `Graph.node_output()` 编译出的 data dependency，并不自动使对应 consumer 成为必达节点，因此不得仅因该依赖存在而拒绝纯 skip。

如果同一 consumer 因其他已经 admitted 的 contribution 必然进入候选 frontier，则它仍是必达 consumer。其他 contribution 包括：

- selected direct edge；
- selected conditional route；
- 本 invocation 将完成的 join；
- 另一个 source 的 candidate 或 confirmed data trigger。

此时 Kernel 必须在首个 commit 前验证该 consumer 的完整 input availability；若缺少被 skip 节点的 publication，必须抛出 `Graph.ValueUnavailableError`。带 replacement output 的 skip 则由 candidate substitution publication 在 planning 中贡献候选 data trigger，并在 exact commit 后由 confirmed substitution publication 建立数据可用性。

该证明必须由 continuation resume 与 state-only recovery 共用的 pure、typed admission/proof owner 执行。两条路径必须复用相同的 compiled transition、materialization、routing 和 publication-availability 规则；recovery 可以扩展 future-path traversal，但不得复制一套 skip/data-flow 语义。

必达 target 和完整 input availability 必须由该 shared resolver 计算。不得在 facade 中扫描 references、把所有 compiled data dependency 一律视为必达、在 recovery 中复制 target 判定，或把 `SkippedGraphNode` 伪装成 success 以触发 data target。

### 4.3 Route 与数据独立

- `route` 只决定控制流。
- `output` 只满足数据流。
- 合法 route 不代表允许缺失 required output。
- 提供 output 不代表可以省略 conditional route。

## 5. State、commit 与 publication

### 5.1 State

Concrete replacement output 不得写入 `GraphRunState`。State 继续只记录 recoverable control position 和 skip 事实。

用户可见语义不得区分“纯 skip”和“带替代输出的 skip”：两者共用唯一 `Graph.skip_failed()`、public action、public settlement、control routing 与错误体系，调用方只通过可选 `output=` 决定是否提供 replacement value。State 默认也不区分两者；output 是否存在属于唯一 publication record 拥有的数据可用性事实。

只有实施设计证明存在无法由 publication provenance 表达、且不可替代的 recoverable control/integrity 必要性时，才可论证 State 内部使用 closed nominal typed distinction。该内部选择必须同时满足：

- 不保存 concrete replacement value，不形成 State/publication 双写；
- 不增加第二 public method、action、settlement、store 或 replacement-only lookup path；
- 不改变 control routing，不让 runtime/recovery 分叉解释；
- 不使用 nullable 字段、字符串 discriminator、sentinel 或 compatibility alias；
- 不要求 stable continuation 重新证明已经消费的历史 settlement。

实现便利不足以构成增加 durable distinction 的理由。若唯一 publication provenance 已满足 runtime、continuation 和 recovery，应保持单一 skip settlement。

### 5.2 Publication 来源

替代 output 不是节点 execution settlement 的产物，不能伪造 execution token。实施设计必须为 confirmed publication 建立明确的 closed provenance，例如：

```text
ExecutionPublication
RecoverySubstitutionPublication
```

具体命名可调整，但必须满足：

- 来源不可为空或使用伪 token；
- provenance 参与 continuation 完整性校验；
- 同一 activation 不允许重复 publication；
- identity 继续使用 exact scope-run、superstep、node ID 和 descriptor。

上述类型名仅为示意。需求固定的是 closed nominal provenance 及其不变量，不在需求阶段固定具体类名或拆分方式。provenance 必须保存在现有唯一 publication record 内，不得使用 nullable/sentinel provenance、字符串 discriminator 或平行 substitution store。

#### 5.2.1 Transition-time 来源证明

substitution publication 的历史来源只在 skip transition 被 exact commit 确认时证明。candidate substitution frame、coordinate 和 provenance candidate 必须在 planning 阶段构造。首个 commit 前必须完成下列 admission：

- 每个 candidate coordinate 与已有 publications 不冲突；
- 同一 invocation 的所有 candidate publications 彼此不冲突，包括不同 scope；
- 任一 collision 抛出 `Graph.ValuePublicationError`，且 commit 调用次数为 0。

完成上述 pre-commit admission 后，安装 publication 前仍必须验证：

1. action 指向 exact scoped failed activation；
2. replacement output 已按该节点 compiled output descriptor 完成 exact admission；
3. route 已独立完成 admission；
4. reducer candidate 是既有 skip command 产生的唯一 successor；
5. commit 返回值与 reducer candidate exact equal；
6. confirmed successor 包含目标 activation 上预期的 `SkippedGraphNode` 及其 reason/routing；
7. publication coordinate 使用 exact scope-run、原 superstep、node ID 和 output descriptor；
8. acknowledged revision 来自该 confirmed successor；
9. 待安装 record 与 planning 时已经 admitted 的 candidate exact equal。

commit 抛错、拒绝、返回非 exact successor，或 confirmed successor 的 skip fact 不匹配时，不得更新 Python memory snapshot 或安装 substitution publication。commit 后的 `add_publication()` 只安装已经 admitted 的 record，不承担首次 duplicate admission；此时若仍发现 collision，表示 plan/install invariant 被破坏，必须作为 internal invariant failure，而不是正常业务 admission。

#### 5.2.2 Stable continuation 完整性

后续 stable continuation 只验证 sealed snapshot 当前可证明的事实：

- continuation 由 owner seal 构造并属于同一 graph family；
- provenance 是 closed nominal union 的合法成员；
- coordinate 属于 continuation lineage，且 scope-run、superstep、node ID、descriptor 与 compiled family 一致；
- frame 按 coordinate descriptor 重新通过 exact admission；
- acknowledged revision 是合法正数，且不引用对应 scoped State 的未来 revision；
- publication coordinates 唯一且 canonical；
- execution provenance 继续验证真实 token 的当前结构约束；
- substitution provenance 验证 sealed、nominal 及 coordinate/revision consistency。

stable continuation 不得从当前 frontier 重新推断历史 `SkippedGraphNode` settlement。`acknowledged_revision <= current scoped state revision` 只证明 provenance 不引用未来 revision，不重新证明历史 transition。不得为此增加 prior State snapshot、skip history、transition journal、durable substitution marker 或 concrete output 的 State mirror。

### 5.3 原子性

一次 resume 可包含多个 action 和 scope。所有 action、scope、route、output admission、candidate publication collision admission 和缺值证明必须在首个 commit 前完成；任一 admission/proof 非法时，commit 调用次数必须为 0。

同一 scope 的 actions 继续通过一个 `ResumeGraphNodes` 原子 reduce。不同 scope 的外部 commit 不构成一个 durable transaction：后续 scope commit 失败时，不回滚此前已经 exact-confirmed 的 scope，也不得发送补偿 transition。每个 scope 独立遵守 durable-first 顺序。

commit 拒绝或返回非 exact successor 时，不得安装替代 publication或更新 Python memory snapshot。

调用方提供的 continuation 是不可变 opaque snapshot。`Graph.run()` 不得原地修改输入 continuation；历史 Result 的 `state + continuation` 必须永久保持稳定、自洽。跨 scope 调用在部分 durable confirmation 后失败时，必须通过显式的 owner-sealed `Graph.PartialCommitError[GraphValueT]` 交付新的 immutable snapshot：

- `state` 是当前最新 root State；
- `continuation` 包含所有已完整确认 scope 的 State、resume input和substitution publication；
- `cause` 保留原始异常，且同一对象作为 `__cause__` 链式传播；
- `failed_scope` 精确标识失败 scope；
- 失败 scope 的 memory State、resume input和publication不得进入该 continuation。

若首个 scope/fence 即失败，原始异常对象原样传播，不包装 `PartialCommitError`。non-exact successor 在已有 exact-confirmed prefix 时，以对应 `Graph.SnapshotMismatchError` 作为 `cause` 按同一显式交付协议包装。

state-only 调用没有可承载部分确认 concrete frame 的原始 lineage。无 continuation、跨多个 resume scope且包含 replacement output 时，必须在首个 commit 前以 `Graph.ValueUnavailableError` fail closed。不得把 replacement frame 写入 State、Graph 实例、全局缓存或第二 store。

## 6. Recovery

### 6.1 带 continuation

已确认替代 publication 必须进入 opaque continuation，并仅供同一 graph family、同一 scoped activation 使用。

opaque continuation 中的 substitution provenance 必须遵守 5.2.2 的当前事实验证边界，不承担重新证明历史 skip settlement 的责任。

### 6.2 State-only recovery

替代 concrete output 不进入 State。若 continuation 丢失且后续需要该值，whole-invocation recovery preflight 必须 fail closed。

不得从 skipped settlement 推测默认值、重跑失败节点、或复用其他 activation 的同名 output。

### 6.3 Loop 与 nested graph

替代 publication 必须绑定：

```text
ScopeRunCoordinate(scope, graph_run_id)
+ superstep
+ node_id
+ output descriptor
```

循环中的不同 superstep、重复 child activation 和不同 nested scope 不得串用替代 output。

对于已终止并投影为 parent `FailedGraphNode` 的 nested child：

- child terminal snapshot 必须保留；
- 不得恢复、重启、替换或改写 child run；
- parent 可对该 nested node activation 执行纯 skip；
- parent 可提供严格匹配 parent nested-node output descriptor 的 replacement output；
- substitution publication 绑定 parent nested node activation，不绑定 child 内部失败 leaf。

这是 parent-level boundary substitution，不是以原 identity 恢复 aborted child。

## 7. 错误语义

公共错误分类固定如下：

| 场景 | 公共错误 |
| --- | --- |
| output 非 canonical `Graph.Values` | `Graph.ValueAdmissionError` |
| output missing/extra key 或 exact type 错误 | `Graph.ValueAdmissionError` |
| route 缺失、不适用或未知 | 现有 `Graph.RoutingError` 子类 |
| scope/node 不存在或 current settlement 非 failed | `Graph.SnapshotMismatchError` |
| 必达 consumer、graph output 或 nested boundary 缺 publication | `Graph.ValueUnavailableError` |
| pre-commit candidate 与既有 publication 或同 invocation candidate publication 冲突 | `Graph.ValuePublicationError` |
| post-commit install 发现未预检 collision | internal invariant failure，非公共业务错误 |
| 已有 exact-confirmed scope 后，后续 scope commit 抛错、non-exact 或 frame installation invariant失败 | `Graph.PartialCommitError[GraphValueT]`；原异常保存在 `cause`/`__cause__` |
| 首个 scope/fence commit失败或首 scope frame installation invariant失败 | 原始异常对象原样传播 |
| malformed continuation provenance、coordinate 或 lineage mismatch | `Graph.SnapshotMismatchError` |
| nested substitution 指向 child 内部失败 leaf 或尝试改写 child run | `Graph.SnapshotMismatchError` |

所有正常业务 admission 错误必须在 durable mutation 前抛出。错误消息至少包含 action node ID；可唯一定位时还应包含 consumer node/input、graph output 或 nested boundary identity。commit 后才发现 duplicate publication 属于 internal invariant failure，不重新分类为正常的 `Graph.ValuePublicationError` admission。

## 8. 非目标

- 不自动生成 fallback。
- 不接受 fallback callable。
- 不按下游 input contract 拼装替代值。
- 不允许部分 output publication。
- 不为缺失字段填充 `None`、默认值或 sentinel。
- 不把 skip 伪装成普通 execution success。
- 不新增第二 runner、第二 frame map 或第二 routing evaluator。
- 不承诺丢失 continuation 后恢复 concrete replacement output。

## 9. 验收条件

至少覆盖：

1. 提供完整、类型正确的 output，skip 后下游成功消费。
2. output 缺 key、多 key、类型错误均在 commit 前拒绝。
3. 无 output 且 direct target 需要值时拒绝。
4. 无 output 且 conditional target 需要值时拒绝。
5. 无 output 且 join target 需要值时拒绝。
6. 无 output 且 graph output 需要值时拒绝。
7. 无 output且安全路径不依赖该值时允许。
8. route 与 output admission 独立校验。
9. 多 action 中任一非法，整批不提交。
10. 所有 scope 的 admission/proof 都在首个 commit 前完成；任一非法时 commit port 调用次数为 0。
11. scope A exact commit 后 scope B commit 抛错或返回 non-exact successor，不回滚 A、不发送补偿 transition、不替换 B 的 memory snapshot、不安装 B 的 substitution publication；抛 `Graph.PartialCommitError[GraphValueT]` 显式交付 A 的最新 State/continuation，原始 failure 作为 `cause`/`__cause__` 保留。
12. commit 拒绝或返回非 exact successor 时不产生 publication。
13. exact commit 后先替换对应 memory state，再安装 substitution publication。
14. replacement publication 激活 data-only consumer；纯 skip 自身不贡献该节点的 data trigger。
15. 未选 branch 引用该 output 时不误拒绝；selected direct/conditional target 缺值时在 commit 前拒绝。
16. 将由本 invocation 完成的 join 缺值时拒绝；本 invocation 不会完成的 join 不误判为必达。
17. continuation 保留已确认替代 publication及 closed provenance。
18. stable continuation 验证当前可证明事实，不要求从当前 frontier 重新证明历史 skipped settlement。
19. state-only recovery 缺值时 fail closed。
20. parent nested-node replacement 能满足 boundary consumer，同时保留且不改写 terminal child snapshot。
21. loop、nested scope、repeated child activation 和 sibling scope 不串值。
22. 同一 activation 的 execution/substitution duplicate publication 被拒绝。
23. malformed provenance、错误 coordinate/descriptor 或 future revision 被 integrity validation 拒绝。
24. strict typing 不引入 `Any`、`object`、bare container、string discriminator 或 generic-erasing cast，并以 negative typing fixture 拒绝 cross-universe action/output。
25. 既有安全 skip、retry、interrupt resume 和 execution publication 不回归。
26. architecture tests 防止第二 store、resolver、runner、routing path 或 State/frame 双写。
27. `make check`、100% branch coverage 与 monorepo pre-commit 全部通过。
28. 纯 skip 面对仅有 compiled data dependency、但未被其他 contribution 触发的 consumer 时允许。
29. 同一 consumer 被 selected direct edge、selected conditional route 或本 invocation 完成的 join 触发且缺 replacement publication 时，在首个 commit 前拒绝。
30. 同一 consumer 被另一个 source 的 data trigger 激活，但缺少 skipped source value 时，在首个 commit 前拒绝。
31. candidate coordinate 与 continuation 既有 publication 冲突时，commit 调用次数为 0。
32. 同 invocation 两个 candidate publications 冲突时，commit 调用次数为 0，且 collision proof 覆盖不同 scope。
33. commit 后 publication installation 不承担首次 duplicate admission；plan/install collision 作为 internal invariant failure。
34. public typing/API 不暴露纯 skip 与 substitution skip 两种 public 类型。
35. 若实施选择 State 内部 distinction，architecture tests 证明其必要性，并证明没有 concrete mirror、第二 store、公共分叉或字符串 discriminator；若不区分，continuation/recovery 仍满足全部 provenance 和 fail-closed 验收。
36. 输入 continuation 永不被调用原地修改；历史 Result 始终保持稳定、自洽，两个共享旧 continuation 的 invocation互不污染。
37. root scope exact-confirmed、child scope失败时，`PartialCommitError.state` 与新 continuation可配对，并可只重试 child。
38. scope A完整安装后 scope B frame installation invariant失败时，显式 checkpoint只包含 A，不虚报 B State/input/publication。
39. 首 scope/fence失败时原异常对象原样传播；已有 exact-confirmed prefix 后失败时才使用 `PartialCommitError`。
40. state-only多scope replacement invocation在首个commit前拒绝，commit调用次数为0。

## 10. 实施前停止条件

出现以下任一情况，必须停止编码并补充设计评审：

- 需要把 concrete replacement output 写入 State；
- 需要伪造 execution token；
- runtime 与 recovery 各自解释一套 skip/data-flow 规则；
- 无法在 commit 前证明无 output skip 的候选 continuation；
- 需要第二 public method、compatibility alias 或第二 publication store；
- 无法在首个 commit 前完成所有 scope/action 的 admission/proof；
- 无法在首个 commit 前完成已有 publication 与 invocation 内全部 candidate publication 的 collision admission；
- 实现要求跨 scope 补偿回滚或在 facade 中模拟 durable transaction；
- stable continuation 验证要求新增历史 State、settlement journal 或 durable substitution marker；
- State 内部 distinction 只能以实现便利为理由，无法证明不可替代的 recoverable control/integrity 必要性。
