# `skip_failed` 可选替代输出需求评审

## 1. 评审对象

- 需求文档：`docs/skip-failed-output-requirements.zh-CN.md`
- 评审基线：`feat/kernel-graph-node-io-contract@8980e6f`
- 评审日期：2026-08-17
- 范围：需求完整性、零负债、唯一真相、基础设施复用、严格泛型与质量门禁

## 2. 评审结论

**需求方向正确，但暂不批准进入代码实施。**

需求已正确坚持：唯一 Graph.skip_failed() 公共入口、concrete output 不进入 State、不伪造 execution token、commit 确认后才安装 publication、state-only recovery fail closed，以及不新增第二 runner、routing evaluator、publication store 或 compatibility alias。

这些原则与当前 Kernel 架构一致，并可复用 compiled transition plan、canonical Graph.Values admission、ScopedFrameIndex、recovery worklist 和 commit_transition()。

初审提出 5 个待澄清项：

1. 多 scope resume 的 durable 原子性边界未定义；
2. replacement publication 是否触发 data target 未定义；
3. 无 output skip 的 admission/proof 未指定唯一 owner；
4. closed publication provenance 尚未闭合；
5. aborted nested child 的替代 output 语义有歧义。

经需求方回复，其中 4 项需要补充需求边界；跨 scope durable transaction 不属于本需求，不能作为实施阻塞项。直接编码前仍须把接受的结论回写需求正文，避免产生双重语义或第二 publication path。

### 2.1 需求方回复与处置

评审指出的 data-trigger、共享 admission/proof owner 和 provenance 不变量成立，应回写需求。其余意见不能原样作为实施阻塞项，处置如下：

| 评审意见 | 处置 | 回复 |
| --- | --- | --- |
| 多 scope 必须具备 durable 原子性 | 部分接受 | 本需求只保证所有 action 在首个 commit 前完成 admission/proof；任何 admission 失败均为零提交。现有 commit port 不提供跨 scope 事务，因此不承诺后续外部 commit 中途失败时回滚已确认 scope。跨 scope durable transaction 属于独立协议变更，不纳入本需求。 |
| replacement publication 必须参与 data-trigger | 接受 | 替代 publication 建立与执行 publication 相同的数据可用性并触发 data target；但节点 settlement 仍是 `SkippedGraphNode`，control routing 仍取自 skip routing，不能把节点伪装为成功执行。 |
| runtime 与 recovery 需要唯一 admission/proof owner | 接受 | 两条入口必须调用同一纯、typed resolver；recovery 只能扩展 whole-invocation proof，不能复制 skip/data-flow 规则。 |
| 必须采用文中列出的 provenance 类型 | 部分接受 | 接受 closed nominal provenance、禁止 nullable/fake token、禁止字符串 discriminator 和第二 store。`ExecutionPublication`、`RecoverySubstitutionPublication` 只是示意命名，具体类型拆分属于实施设计，不在需求阶段锁定。 |
| aborted nested child 的替代 output 一律有歧义或应禁止 | 不接受该前提，需澄清边界 | child run 的 terminal identity 不得恢复或改写；但 parent graph 中 nested node 是独立的失败 boundary activation。对该 parent activation 执行纯 skip 或提供符合 parent nested-node output descriptor 的替代值，不等于重启 child。允许 parent-level boundary substitution，保留 child terminal snapshot，且 publication 不得绑定 child 内部失败 leaf。 |

因此，真正需要关闭的语义问题是：data-trigger、共享 admission/proof、provenance 不变量，以及 nested parent boundary 的精确定义。跨 scope durable transaction 和 provenance 的具体类名不是本需求的通过条件。

## 3. 已满足的架构约束

### 3.1 唯一入口与零兼容负债

只扩展 Graph.skip_failed(..., output=Graph.values(...))，并禁止 skip_failed_with()、substitute_failed() 和兼容别名。该决定符合 Graph 作为唯一公共 execution facade 的规则。

### 3.2 State 与 concrete value 分离

replacement output 不进入 GraphRunState。State 继续只保存 recoverable control position 和 skip fact，concrete value 只进入 invocation-local opaque continuation/frame index。不得增加 value tape、journal、默认值推导或 State/frame 双写。

### 3.3 Exact admission 复用正确

现有 execution/graph/values.py 已提供完整 owner：

- _require_graph_values() 验证 canonical Graph.values()；
- _make_node_output_frame() 按失败节点自己的 compiled descriptor 构造 output frame；
- _admit_entries() 精确验证 key、canonical order 和 concrete nominal type。

实现必须直接复用这些设施，不得新增 fallback validator，也不得按 downstream input contract 拼装替代值。

### 3.4 Publication identity 复用正确

现有 identity 已闭合为 ScopeRunCoordinate(scope, graph_run_id) + superstep + node_id + output descriptor identity。它能隔离 loop、nested scope 和 repeated child activation。ScopedFrameIndex.add_publication() 已拒绝同 coordinate 的重复 publication，应继续作为唯一去重 owner。

### 3.5 Durable-first 顺序正确

replacement publication 必须沿用：完整 preflight -> commit exact successor -> 更新 memory state -> 安装 confirmed publication。commit 抛错或返回非 exact successor 时，不得更新 memory state 或 frame index。

## 4. 待澄清项一：多 scope 原子性

当前 planning 会在首个 commit 前验证全部 actions；同一 scope 的 actions 合并成一个 `ResumeGraphNodes`。但不同 scope 会形成多个 `_PlannedResume` 并逐个 commit。

因此当前可以保证：

- 任一 admission 非法时零 commit；
- 同 scope 多 action 原子 reduce；
- 所有 scope 都在首个 durable mutation 前完成 planning。

当前不能保证 scope A 已 commit、scope B commit 失败后，scope A 自动回滚。commit port 不是跨 scope transaction coordinator。

需求应明确采用下列第一种边界：

1. admission 原子性：任一校验非法时首个 commit 前失败；外部 commit 中途失败不承诺回滚已确认 scope；
2. 跨 scope durable transaction：所有 scope 一次提交或全部不提交。

第二种语义需要新的 batch commit/transaction protocol，必须另做设计评审，不能在 facade 中做补偿写。本需求确定采用第一种语义，并精确修订“整次调用不得提交”的表述。跨 scope commit 中途失败不要求补偿回滚。

## 5. 待澄清项二：data-trigger 语义

当前 shared routing resolver 只让 SucceededGraphNode 贡献 data triggers；SkippedGraphNode 只贡献 control routing。仅安装 replacement frame 不足以让 data-only consumer 进入下一 frontier。

需求必须明确：

- replacement output 是否激活该 node output 建立的 data target；
- 无 output 的纯 skip 是否不激活 data-only target；
- selected direct/conditional/join target 需要该值时是否 preflight 拒绝；
- nested parent output substitution 是否具有相同 data-trigger 语义。

确定采用：substitution publication 对 availability 和 data-trigger 具有与 execution publication 相同的语义；节点在 State 中仍是 SkippedGraphNode；control routing 仍只由 SkippedGraphNode.routing 决定。

即 State settlement 是 control truth，confirmed publication availability 是 data truth。必须复用 shared routing resolver，不得增加 replacement-specific routing path。

## 6. 待澄清项三：唯一 admission/proof owner

state-only recovery 会运行 whole-invocation preflight；正常 continuation resume 不一定运行该 proof。如果只修改 recovery worklist，将造成 state-only 路径 commit 前拒绝，而 continuation 路径可能先 commit skip 再由 runtime abort。

实施设计必须建立一个 pure、typed、无副作用的 shared resume admission/proof owner，输入至少包括 compiled transition plan、simulated scoped states、candidate frame availability 和 all resume actions。

它必须同时被 continuation resume 与 state-only recovery 调用，并复用同一 routing/materialization/output-availability resolver。Recovery 可以在其基础上证明未来路径，但不能复制 skip/data-flow 规则。

证明范围至少覆盖 selected direct/conditional target、将完成的 join、data target、graph completion output、nested boundary、同 invocation 其他 actions，以及 loop 的 exact superstep selection。

## 7. 待澄清项四：closed publication provenance

当前 ConfirmedPublication 强制保存真实 GraphExecutionToken。replacement output 并非 execution settlement 产物，不能复用或伪造 token。

实施设计应把 provenance 收敛到现有唯一 publication record 内，形成 nominal closed union。`ExecutionPublication | RecoverySubstitutionPublication` 仅为示意命名，不是需求固定的类型名。

- execution provenance 携带真实 token；
- substitution provenance 携带已确认 skip transition 的 state-owned evidence；
- 两者共享 PublicationAvailabilityCoordinate、frame、acknowledged revision；
- ScopedFrameIndex.publications 仍是唯一 store。

禁止 nullable/fake/sentinel token、string kind discriminator、平行 replacement publication tuple、replacement-only lookup path，以及从 SkippedGraphNode 推导 concrete value。

Continuation integrity 必须验证 provenance 与 exact scope、superstep、node、descriptor、revision 和对应 acknowledged settlement 一致，且不得为历史证明增加 journal。

## 8. 待澄清项五：nested aborted child

当前系统禁止用 resume_failed*() 对 terminal aborted child 复用同一 identity 重启，但允许 parent 对已投影为 FailedGraphNode 的 nested node 执行纯 skip。需求中的“aborted nested child 不允许以当前 identity 执行该操作”存在歧义。

需求边界确定为：

1. parent 对 aborted child 的纯 skip 继续允许；
2. parent 可提供严格匹配 parent nested-node output descriptor 的 replacement output；
3. publication 绑定 parent nested node activation，而不是 child 内部失败 leaf；
4. 无论是否带 output，都不得创建、重启、替换或改写 child run；
5. retained terminal child snapshot 不得删除。

该语义是 parent-level boundary substitution，不是恢复 aborted child，也不复用 child identity 执行失败 leaf。

## 9. 错误语义

建议固定：

| 场景 | 公共错误 |
| --- | --- |
| output 非 canonical Graph.Values | Graph.ValueAdmissionError |
| output missing/extra key 或 exact type 错误 | Graph.ValueAdmissionError |
| route 缺失、不适用或未知 | 现有 Graph.RoutingError 子类 |
| scope/node 不存在或 settlement 非 failed | Graph.SnapshotMismatchError |
| 无 output 且必达 consumer 需要 publication | Graph.ValueUnavailableError |
| 同 activation 重复 publication | Graph.ValuePublicationError |
| provenance/coordinate 不一致 | Graph.SnapshotMismatchError |

错误消息至少包含 action node ID；缺值错误在可唯一确定时还应包含 consumer node/input、graph output name 或 nested boundary identity。

## 10. 泛型约束

目标公共签名保留了 GraphValueT 从 Graph、Values 到 ResumeAction 的关系，方向正确。

加入 output 后，当前非泛型的 SkipFailedNodeRequest 必须变为 SkipFailedNodeRequest[GraphValueT]，并让 Graph.skip_failed -> ResumeNodeRequest -> ResumeRequest -> PreparedResume -> candidate publication frame -> ScopedFrameIndex 全程保持同一 GraphValueT。

不得使用 Any、object、bare container、generic-erasing cast、非泛型 request 加宽 union 或 string discriminator 恢复 provenance 类型。

必须增加 negative typing fixtures，证明 cross-universe output/action 被拒绝、heterogeneous factory inference 不出现 Unknown、empty Never 不绕过 invariance，且 public API 不泄漏 internal request。

## 11. 唯一 owner 与基础设施复用

| 事实/行为 | 唯一 owner |
| --- | --- |
| public action construction | execution.facade.Graph |
| canonical request shape | execution.request |
| failed settlement、route、scope admission | GraphExecutor.resume() |
| concrete output exact admission | execution.graph.values + compiled descriptor |
| candidate resume planning | execution.invocation |
| control/data target 解析 | shared execution.engine.routing resolver |
| future recovery proof | execution.engine.recovery，基于 shared admission/resolver |
| durable confirmation | family_driver.commit_transition() |
| confirmed concrete frames | 唯一 ScopedFrameIndex |
| continuation integrity | execution.run_context + invocation validation |
| control facts | GraphRunState reducer |

不得增加 replacement-specific runner、frame map、routing evaluator、facade 临时 dependency scan、recovery-only skip semantics、publication 双表或 State/frame 双写。

## 12. 验收条件增补

在原需求验收条件基础上，至少增加：

1. replacement publication 激活 data-only consumer；
2. 无 output 的未选 branch 引用不误拒绝；
3. selected direct/conditional target 缺值在 commit 前拒绝；
4. 将完成的 join target 缺值在 commit 前拒绝；
5. 未完成且本 invocation 不会完成的 join 不误判为必达；
6. ordinary node 与 nested node replacement 分别覆盖；
7. 同 scope 多 action 任一非法时 ResumeGraphNodes 零提交；
8. 跨 scope 所有 admission 在首个 commit 前完成；
9. 明确测试选定的跨 scope commit 原子性语义；
10. commit 抛错或返回非 exact successor 时不更新 memory、不安装 publication；
11. exact commit 后先替换 state，再安装 publication；
12. 同 activation 重复 execution/substitution publication 被拒绝；
13. continuation 保留 closed substitution provenance 和 concrete frame；
14. state-only recovery 需要 replacement value 时 fail closed；
15. loop、repeated child activation 和 sibling nested scope 不串值；
16. malformed provenance、wrong descriptor/revision 被 integrity validation 拒绝；
17. 既有纯 skip、retry、interrupt 和 execution publication 不回归；
18. architecture tests 防止第二 store/resolver/runner；
19. negative typing fixtures 防止 cross-universe action；
20. make check、100% branch coverage 与 monorepo pre-commit 全部通过。

## 13. 门禁

当前 `make check` 已覆盖 Ruff、strict Pyright、Pytest branch coverage 100%、build 和 Twine package check。它不包含 monorepo pre-commit，交付时还必须从 monorepo root 运行：

```bash
pre-commit run --all-files
```

如任务范围要求安全检查，还应运行 `make security`。不得降低 strict type mode、missing stubs error、100% coverage、generic erasure gate、internal `Any` gate、sole execution owner 或 package structure gates。

如改动 shared durable protocol，必须同步 conformance/；若只改变 Python opaque continuation/internal request，应记录“不改变跨语言 durable protocol”的证据。

## 14. 通过条件

满足以下条件后才可批准进入实施设计：

1. 将已确定的 admission 原子性边界回写需求，不要求跨 scope durable transaction；
2. 明确 substitution publication 的 data-trigger 语义；
3. 指定 continuation/runtime 与 state-only recovery 共用的唯一 admission/proof owner；
4. 将 provenance 闭合为唯一 publication record 内的 nominal union，不固定示意类名；
5. 回写已确定的 nested aborted child parent-level boundary substitution 规则；
6. 固化错误类型和稳定诊断；
7. 补充 cross-universe、provenance、batch、commit rejection、nested/loop 验收项；
8. 明确不新增 State value、fake token、第二 store/resolver/runner 或 alias；
9. 形成短实施设计，列出 owner、typed structures、preflight 顺序和 commit 后 publication 顺序；
10. 实施设计重新评审通过后再编码。

## 15. 最终意见

当前状态：**需求方向通过，实施批准暂缓。**

该需求不需要新增 execution engine。正确路径是扩展 typed skip request、复用 node output exact admission、把无 output proof 收敛到 shared admission、为唯一 publication record 增加 closed provenance、在 commit 精确确认后安装 substitution frame，并让 shared routing/recovery 使用同一 availability 语义。

将上述处置结论回写需求并补充短实施设计后，可以在不引入兼容负债、双路径或泛型擦除的前提下实施。当前需求文档应继续保持 Draft / requirements only。

本次为静态评审，未修改生产代码，未运行 make check 或 monorepo pre-commit。
