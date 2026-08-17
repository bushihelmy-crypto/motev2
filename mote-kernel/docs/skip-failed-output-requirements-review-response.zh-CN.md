# `skip_failed` 可选替代输出需求复核回复

## 1. 回复信息

- 回复对象：`docs/skip-failed-output-requirements-review.zh-CN.md` 的本轮复核意见
- 关联需求：`docs/skip-failed-output-requirements.zh-CN.md`
- 回复日期：2026-08-17
- 当前状态：接受 3 项复核意见；需求正文回写和实施设计完成前不批准编码

## 2. 总体回复

接受本轮复核的核心结论。特别接受以下修正：

1. stable continuation 不能重新证明已经被后续 frontier 消费的历史 `SkippedGraphNode` settlement；
2. 已确定的 admission 原子性、data-trigger、shared proof owner 和 nested parent boundary 语义必须回写需求正文，评审回复不能成为第二规范事实源；
3. 跨 scope commit 的验收条件必须从“待选择语义”改成已确定、可执行的 admission 原子性语义。

本回复不把历史证明责任推给 continuation validator，不要求新增 State 字段、settlement history、transition journal 或第二 publication store。

修正后的准确状态是：**需求语义基本闭合；仍需把已确定结论回写需求，并用短实施设计闭合 provenance 的 transition-time/stable-continuation 责任边界。完成并重新评审前不进入编码。**

## 3. 接受：provenance 的历史证明责任应留在 transition-time

### 3.1 对复核问题的确认

原评审要求 continuation integrity 验证 substitution provenance 与“对应 acknowledged settlement”一致。该表述过强。

在 skip resume commit 后，graph 可以继续 advance 或 complete；后续 continuation 中的当前 frontier 未必仍保留原 `SkippedGraphNode`。仅凭当前 stable State，validator 无法重新证明某个历史 revision 上曾存在对应 skipped settlement。

如果坚持重新证明，就必须引入下列至少一种历史结构：

- prior State snapshot；
- skip settlement history；
- transition journal；
- durable substitution marker；
- concrete output 的 State mirror。

这些方案均违反本需求的 State/concrete value 分离、无 journal、无第二事实源和零负债约束。因此接受复核意见，修正责任边界。

### 3.2 Transition-time 的唯一证明责任

substitution publication 只允许在 skip transition 被 exact commit 确认后安装。安装点必须验证当时可证明的全部事实：

1. action 指向 exact scoped failed activation；
2. replacement output 已按该节点 compiled output descriptor 完成 exact admission；
3. route 已独立完成 admission；
4. reducer candidate 是提交既有 skip command 得到的唯一 successor；
5. commit 返回值与该 reducer candidate exact equal；
6. confirmed successor 在目标 activation 上包含预期 `SkippedGraphNode` 及其 reason/routing；
7. publication coordinate 使用该 scope-run、原 superstep、node ID 和 output descriptor；
8. acknowledged revision 来自该 confirmed successor；
9. coordinate 尚未存在 publication；
10. 只有完成以上验证后，才更新 Python memory state 并安装 substitution publication。

commit 抛错、拒绝、返回非 exact successor，或 successor 中的 skip fact 不匹配时，均不得安装 publication。

### 3.3 Stable continuation 只验证当前可证明事实

stable continuation integrity 只验证 sealed snapshot 当前可证明的事实：

1. continuation 由 owner seal 构造，属于同一 graph family；
2. publication provenance 是 closed nominal union 的合法成员；
3. coordinate 的 scope-run 属于 continuation lineage；
4. scope、graph run ID、superstep、node ID 和 descriptor 与 compiled family 一致；
5. frame 按 coordinate descriptor 重新通过 exact admission；
6. acknowledged revision 为合法正数，且不引用对应 scoped State 的未来 revision；
7. publication coordinates 唯一且 canonical；
8. execution provenance 继续验证其当前可验证的真实 token 结构约束；
9. substitution provenance 验证其 sealed、nominal、coordinate/revision-consistent 结构；
10. 不从当前 frontier 重新推断历史 skipped settlement。

`acknowledged_revision <= current scoped state revision` 只证明 provenance 不引用未来 revision，不等于重新证明历史 transition。文档和测试必须明确这一区别。

### 3.4 禁止为了历史证明扩张模型

不得为 stable continuation validator 增加：

- skip history；
- settlement journal；
- historical State list；
- replacement flag in `GraphRunState`；
- fake execution token；
- nullable/sentinel provenance；
- 第二 publication record/store。

transition-time exact acknowledgement 是历史来源证明的唯一时点。opaque continuation 的 owner seal、closed provenance 和完整坐标负责防伪与隔离，不负责重演历史。

## 4. 接受：需求正文必须成为唯一规范事实源

复核正确指出，当前部分确定语义只存在于评审文档，原需求仍保留模糊表述。这不符合唯一真相。

应将以下已确定结论回写 `docs/skip-failed-output-requirements.zh-CN.md`：

### 4.1 Admission 原子性

- 同一 resume invocation 的所有 action、scope、route、output admission 和缺值 proof 必须在首个 commit 前完成；
- 任一 admission/proof 非法时 commit 调用次数为 0；
- 同一 scope 的 actions 继续由一个 `ResumeGraphNodes` 原子 reduce；
- 不要求不同 scope 的外部 commit 组成 durable transaction；
- 后续 scope commit 失败不回滚此前已 exact-confirmed scope，也不得发出补偿 transition。

### 4.2 Data-trigger

- substitution publication 建立与 execution publication 相同的数据 availability；
- 它触发由该 node output 建立的 data target；
- State settlement 仍为 `SkippedGraphNode`；
- control routing 只来自 skip routing；
- runtime/recovery 必须复用同一 compiled resolver。

### 4.3 Shared admission/proof owner

- continuation resume 与 state-only recovery 调用同一 pure、typed skip-output admission/proof；
- proof 使用 shared compiled transition/materialization/routing rules；
- recovery 可扩展 future-path traversal，但不得复制 skip/data-flow 规则。

### 4.4 Nested parent boundary

- aborted child terminal snapshot 保留；
- 不允许恢复、重启、替换或改写该 child run；
- parent 可对已投影为 failed 的 nested node进行纯 skip；
- parent 可提供符合 parent nested-node output descriptor 的 replacement output；
- publication 绑定 parent nested node activation，不绑定 child 内部失败 leaf。

### 4.5 Closed provenance 不变量

- provenance 是唯一 publication record 内的 closed nominal union；
- execution source 必须携带真实 token；
- substitution source 不携带或伪造 execution token；
- provenance 具体类型名由实施设计决定；
- transition-time 证明 acknowledged skip successor；
- stable continuation 不重新证明历史 settlement。

完成回写后，评审和回复只作为历史决策记录，需求正文是唯一规范事实源。

## 5. 接受：跨 scope commit 验收应改成确定语义

原评审中的“明确测试选定的跨 scope commit 原子性语义”仍像待决事项。接受复核意见，改为以下确定性验收：

1. 所有 scope 的 action admission/proof 在首个 commit 前完成；
2. 任一 action、route、output 或 proof 非法时，commit port 调用次数为 0；
3. scope A exact commit 后、scope B commit 抛错时，不要求回滚 A；
4. B 的 Python memory snapshot 不得替换；
5. B 的 substitution publication 不得安装；
6. 不发送 A 或 B 的补偿 transition；
7. graph.run 传播原始 commit failure；
8. 若 B commit 返回非 exact successor，行为与抛错相同；
9. 使用 continuation 重试时，以调用方提供的 authoritative State/continuation 重新做完整 validation，不依赖失败 invocation 的隐藏内存。

该语义是 admission atomicity + per-transition durable-first，不宣称跨 scope distributed transaction。

## 6. 文档结构调整意见

接受将“待澄清项”按状态重命名，以避免已决议事项看起来仍开放：

- 多 scope：**已决议——采用 admission 原子性**；
- data-trigger：**已决议——substitution 参与 data availability/trigger**；
- shared proof：**实施设计约束——唯一 admission/proof owner**；
- provenance：**实施设计约束——closed provenance 与分阶段验证**；
- nested child：**已决议——允许 parent-level boundary substitution**。

需求正文回写后，评审文档的“通过条件”应只保留尚未完成的实施设计事项，不再把已决议语义列为待选择问题。

## 7. 对错误语义、泛型与门禁的回复

本轮复核没有推翻原评审在这些方面的结论，继续接受：

- non-canonical/malformed replacement output 使用 `Graph.ValueAdmissionError`；
- route 错误复用现有 `Graph.RoutingError` 子类；
- scope/node/current settlement mismatch 使用 `Graph.SnapshotMismatchError`；
- 无 output 且必达 consumer 缺值使用 `Graph.ValueUnavailableError`；
- duplicate publication 使用 `Graph.ValuePublicationError`；
- `SkipFailedNodeRequest` 必须泛型化并保持 `GraphValueT` 全链路；
- 不允许 `Any`、`object`、bare containers、reflection、string discriminator 或 generic-erasing cast；
- 增加 cross-universe negative typing fixtures；
- 保持 `make check`、100% branch coverage 与 monorepo `pre-commit run --all-files`；
- 如不改变 shared durable protocol，应记录无需 conformance 更新的证据。

## 8. 更新后的实施前通过条件

需求进入实施设计前必须：

1. 将第 4 节全部已决议语义回写需求正文；
2. 删除或修正需求中暗示跨 scope durable transaction 的表述；
3. 修正 aborted nested child 的歧义错误语义；
4. 把 provenance 验证拆分为 transition-time exact proof 与 stable-continuation current-fact validation；
5. 固化第 5 节的跨 scope commit 验收；
6. 保持唯一 public method、唯一 publication store 和唯一 routing/proof rules。

实施设计进入编码前还必须：

1. 给出 closed nominal provenance 的具体 typed model；
2. 给出 shared admission/proof owner 的模块和函数边界；
3. 给出 replacement output candidate frame 的 admission 时点；
4. 给出 commit 后 memory replacement/publication installation 的精确顺序；
5. 给出 continuation validation 的可证明不变量，明确不证明历史 settlement；
6. 给出 runtime/recovery/data-trigger 共用 resolver 的调用关系；
7. 给出 complete tests、negative typing fixtures 和 architecture gates；
8. 重新完成实施设计评审。

## 9. 最终结论

本轮复核意见全部接受。原评审的总体方向仍成立，但 provenance integrity 的表述必须修正：

```text
exact skip successor 的历史来源证明属于 transition-time；
stable continuation 只验证 sealed provenance、完整坐标、descriptor、
frame exact admission、revision 非未来和 family/lineage consistency；
不得为重新证明历史 settlement 增加 State 或 journal。
```

同时，已确定的 admission 原子性、data-trigger、shared proof owner 和 nested parent boundary substitution 必须进入需求正文，避免评审文档成为第二规范。

更新后的状态：**需求语义基本闭合，需求正文与短实施设计尚待更新；在二者重新评审通过前，不批准 production 编码。**

本回复仅记录评审处置，未修改需求正文、production code 或 tests，未运行 `make check` 和 monorepo pre-commit。
