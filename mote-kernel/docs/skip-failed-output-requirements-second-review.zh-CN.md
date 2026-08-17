# `skip_failed` 可选替代输出需求第二轮评审

## 1. 评审对象

- 需求文档：`docs/skip-failed-output-requirements.zh-CN.md`
- 前序评审：`docs/skip-failed-output-requirements-review.zh-CN.md`
- 前序回复：`docs/skip-failed-output-requirements-review-response.zh-CN.md`
- 评审基线：`feat/kernel-graph-node-io-contract@8980e6f`
- 评审日期：2026-08-17
- 评审范围：前序问题关闭情况、data-trigger、publication admission、State 内部表达、错误契约、唯一真相和实施准入

## 2. 评审结论

前序评审提出的主要架构问题已经正确回写需求正文：

- 采用 admission 原子性，不要求跨 scope durable transaction；
- substitution publication 参与数据 availability 和 data-trigger；
- control truth 继续由 `SkippedGraphNode.routing` 拥有；
- continuation resume 与 state-only recovery 共用 pure、typed admission/proof owner；
- provenance 收敛到唯一 publication record 内的 closed nominal union；
- exact skip successor 的历史来源只在 transition-time 证明；
- stable continuation 不重新证明已被后续 frontier 消费的历史 settlement；
- nested aborted child 已明确为 parent-level boundary substitution；
- 不新增 runner、frame map、routing evaluator、publication store 或兼容入口。

当前仍有 2 个 P1 语义边界需要补充：

1. 无 output 的纯 skip 与 data target 的精确关系；
2. candidate substitution publication 的 duplicate admission 必须发生在首个 commit 前。

State 是否在内部区分“纯 skip”和“带替代 output 的 skip”不作为本轮阻塞项。需求方已明确：**对用户不区分两类 skip。** 实施设计可以保留内部表达选择，但必须满足第 5 节的封闭约束。

因此当前判定为：**核心需求通过，仍有 2 个 P1 需回写；回写后可以进入短实施设计评审，但仍不批准直接编码。**

## 3. 已关闭问题复核

### 3.1 Admission 原子性已闭合

需求已明确：

- 所有 action、scope、route、output admission 和缺值 proof 在首个 commit 前完成；
- 任一 admission/proof 非法时 commit 调用次数为 0；
- 同一 scope 继续由一个 `ResumeGraphNodes` 原子 reduce；
- 不同 scope 的 commit 不构成 durable transaction；
- 后续 scope commit 失败不回滚已确认 scope，也不发送补偿 transition；
- 每个 scope 独立遵守 durable-first。

该语义与现有 commit port 能力一致，没有要求 facade 模拟事务。

### 3.2 Provenance 的分阶段证明已闭合

需求已正确区分：

#### Transition-time

- 验证 exact scoped failed activation；
- exact admission replacement output；
- 独立 admission route；
- commit 返回 exact reducer successor；
- successor 包含预期 `SkippedGraphNode` reason/routing；
- 使用 exact coordinate 和 acknowledged revision 安装 publication。

#### Stable continuation

- 验证 owner seal、family、lineage、coordinate、descriptor 和 frame；
- 验证 provenance nominal shape 与 revision 不引用未来；
- 不从当前 frontier 重新证明历史 skipped settlement；
- 不增加 prior State、skip history、journal 或 concrete output State mirror。

该边界符合 durable-first 与零历史负债原则。

### 3.3 Nested boundary 已闭合

需求已明确 parent-level substitution：

- terminal child snapshot 保留；
- child run 不恢复、不重启、不替换、不改写；
- parent 对已投影为 failed 的 nested node执行 skip；
- replacement output 按 parent nested-node output descriptor admission；
- publication 绑定 parent nested node activation，不绑定 child 内部失败 leaf。

该语义不会复用 aborted child identity 执行失败 leaf。

## 4. P1：纯 skip 与 data target 的关系仍需精确定义

### 4.1 当前歧义

需求要求无 output proof 检查 data target，同时又规定：

```text
纯 skip 不激活缺少 publication 的 data target。
```

当前 shared routing resolver 的既有语义是：

- `SucceededGraphNode` 贡献自身 data triggers；
- `SkippedGraphNode` 不贡献自身 data triggers。

因此，仅存在 `Graph.node_output(failed_node, ...)` 编译出的 data dependency，并不自动意味着该 consumer 在纯 skip 后必达。

但同一个 consumer 仍可能因为其他已 admitted contribution 而必然进入候选 frontier，例如：

- selected direct edge；
- selected conditional route；
- 本 invocation 完成的 join；
- 另一个 source 的 confirmed data trigger。

此时即使 failed node 自己不贡献 data trigger，该 consumer 仍是必达节点，必须验证其完整 input availability。

### 4.2 必须回写的规则

建议需求正文明确：

```text
无 output 的纯 skip 不贡献该节点的 data trigger。
仅存在 compiled data dependency 不构成必达 consumer。

如果同一 target 因其他 admitted control、join 或 data contribution
必然进入候选 frontier，则它是必达 consumer，Kernel 必须在首个
commit 前验证其完整 input availability；缺少被 skip 节点的 publication
时抛出 Graph.ValueUnavailableError。

带 replacement output 的 skip 通过 candidate/confirmed substitution
publication 贡献该节点的 data trigger。
```

### 4.3 实施约束

该规则必须由 shared routing/admission resolver 计算，不能：

- 在 facade 中扫描 references；
- 把所有 data dependency 一律当成必达；
- 在 recovery 中复制一套 target 判定；
- 通过把 `SkippedGraphNode` 伪装成 success 来触发 data target。

## 5. State 内部是否区分两类 skip

### 5.1 用户可见语义已经确定

对用户而言，纯 skip 与带 replacement output 的 skip 不形成两个概念：

- 唯一公共入口仍是 `Graph.skip_failed()`；
- 不增加 `skip_failed_with()`、`substitute_failed()` 或其他别名；
- 两者都表示 operator 接受失败并选择 skip；
- control routing 语义相同；
- 下游统一通过 `Graph.node_output()` 取值；
- 不暴露两种 public result、public settlement、public action 或错误体系；
- 用户只通过可选 `output=` 决定是否提供 replacement value。

因此，State 的默认设计也应只有一个 skip settlement。`output=` 是否存在属于 publication/data availability 事实，不应自然升级为第二种 durable skip 类型。

### 5.2 允许实施设计保留内部选择

只有在实现者能够证明存在不可由唯一 publication provenance 表达的 recoverable control/integrity 需求时，实施设计才可以论证 durable State/settlement 是否需要在内部区分：

```text
纯 skip
带 confirmed replacement output 的 skip
```

但若选择区分，必须同时满足：

1. 区分仅服务于 recoverable control/integrity，不保存 concrete replacement value；
2. 不形成第二 public method、第二 public action 或第二 public settlement；
3. 不改变用户的 control routing 语义；
4. 不建立第二 publication store 或 replacement-only lookup path；
5. concrete value 和 data availability 仍由唯一 publication record 拥有；
6. runtime 与 recovery 仍调用同一 resolver；
7. 不通过 nullable字段、字符串 discriminator、sentinel 或 compatibility alias 表达；
8. 使用 closed nominal typed model；
9. 必须证明该 durable distinction 是 recovery/integrity 所必需，而非为方便实现引入 mirror；
10. 必须证明它不会要求 stable continuation 重新证明已消费的历史 settlement。

### 5.3 与当前“禁止 durable substitution marker”的协调

需求当前同时允许“实施设计证明是否需要区分”，又禁止为 stable continuation 历史证明增加 durable substitution marker。两者不冲突，但实施设计必须明确：

- 禁止的是为了重新证明历史 transition 而增加 marker；
- 若 State 内部 distinction 有其他不可替代的 recoverable control/integrity 用途，必须单独证明；
- 不能仅以“区分两类 skip 更方便”为理由增加 durable bit；
- 如果唯一 publication provenance 已足以满足 runtime、continuation 与 recovery，则不应重复写入 State。

本轮结论是：**用户语义绝不区分；State 默认不区分。** 只有不可替代的 recovery/integrity 必要性证明才能推翻 State 默认方案。无论内部最终如何表达，“公共面不分叉、control semantics 不分叉、concrete truth 不双写”都是硬约束。

## 6. P1：duplicate publication 必须在首个 commit 前 admission

### 6.1 风险

需求已要求同一 publication coordinate 不得重复，但 transition-time 列表可能被理解为：skip commit 成功后，才调用 `ScopedFrameIndex.add_publication()` 检查重复。

这会产生非法顺序：

```text
skip 已 durable commit
-> add_publication 发现 coordinate collision
-> replacement publication 未安装
```

该结果违反 admission 原子性和 durable-first。

### 6.2 必须回写的规则

需求应明确：

1. candidate substitution frame、coordinate 和 provenance candidate 在 planning 阶段构造；
2. 在首个 commit 前检查它与已有 publications 不冲突；
3. 在首个 commit 前检查同 invocation 所有 candidate publications 互不冲突；
4. 任一 collision 抛出 `Graph.ValuePublicationError`，commit 调用次数为 0；
5. commit 后 `add_publication()` 只安装已经 admitted 的 record；
6. commit 后若仍发生 collision，表示 plan/install invariant 被破坏，应作为 internal invariant failure，不是正常业务 admission。

### 6.3 多 scope 关系

所有 scope 的 candidate publication collision proof 必须与 action/route/output proof 一样，在首个 scope commit 前完成。不能在执行到后续 scope 时才首次检查该 scope 的 collision。

## 7. P2：错误类型应固定

需求已列出错误场景，但应固定公共错误分类，避免实施设计自行选择：

| 场景 | 公共错误 |
| --- | --- |
| output 非 canonical `Graph.Values` | `Graph.ValueAdmissionError` |
| output missing/extra key 或 exact type 错误 | `Graph.ValueAdmissionError` |
| route 缺失、不适用或未知 | 现有 `Graph.RoutingError` 子类 |
| scope/node/current settlement 错误 | `Graph.SnapshotMismatchError` |
| 必达 consumer 缺 publication | `Graph.ValueUnavailableError` |
| candidate/confirmed duplicate publication | `Graph.ValuePublicationError` |
| malformed continuation provenance | `Graph.SnapshotMismatchError` |

错误消息至少包含 action node ID；可唯一定位时还应包含 consumer input、graph output 或 nested boundary。

## 8. 验收条件增补

除当前 27 项外，至少补充：

1. 纯 skip 面对仅有 compiled data dependency 的未触发 consumer时允许；
2. 同一 consumer 被 selected direct edge 触发且缺 replacement publication 时，首个 commit 前拒绝；
3. 同一 consumer 被 selected conditional route 触发且缺值时拒绝；
4. 同一 consumer 被本 invocation 完成的 join 触发且缺值时拒绝；
5. 同一 consumer 被另一个 source 的 data trigger 激活且缺 skipped source value 时拒绝；
6. 带 replacement output 的 skip 激活自身 data target；
7. candidate coordinate 与 continuation 既有 publication 冲突时 commit 调用次数为 0；
8. 同 invocation 两个 candidate publications 冲突时 commit 调用次数为 0；
9. collision 检查覆盖不同 scope；
10. commit 后 publication install 不承担首次 duplicate admission；
11. public typing/API 不暴露纯 skip与 substitution skip 两种类型；
12. 若实施选择 State 内部 distinction，architecture tests 证明没有 concrete mirror、第二 store 或字符串 discriminator；
13. 若实施不区分，continuation/recovery 仍满足全部 provenance 和 fail-closed 验收。

## 9. 通过条件

需求批准进入实施设计前必须：

1. 回写第 4.2 节的 pure-skip/data-target 精确规则；
2. 回写第 6.2 节的 pre-commit duplicate admission；
3. 固定或确认第 7 节的公共错误分类；
4. 保留第 5 节确定的用户不区分约束；
5. 明确 State 内部 distinction 只能由实施设计证明，且不得形成 concrete truth 双写或公共分叉；
6. 补充第 8 节验收矩阵。

进入 production 编码前还必须：

1. 提交短实施设计；
2. 给出 shared admission/proof owner 的具体模块边界；
3. 给出 candidate publication typed model 与 pre-commit planning 顺序；
4. 给出 closed provenance model；
5. 给出 State 内部是否区分的明确结论及必要性证明；
6. 给出 runtime/recovery/shared routing 调用关系；
7. 通过实施设计评审。

## 10. 最终意见

当前状态：**核心需求通过，仍有 2 个 P1 和 1 个 P2 文档项。**

用户侧不区分纯 skip 与带 replacement output 的 skip。State 也默认不区分；只有实施设计证明存在不可由 publication provenance 承担的 recovery/integrity 必要性时，才允许采用封闭的内部区分。硬性要求是公共 API 不分叉、control semantics 不分叉、concrete value 不进入 State、publication truth 不双写、runtime/recovery 不产生第二解释路径。

修正 pure-skip/data-target 和 pre-commit duplicate admission 后，需求可以进入短实施设计评审。实施设计明确 State 内部 representation、closed provenance 和 shared proof owner，并重新评审通过后，才可开始 production 编码。

本次为静态需求评审，未修改需求正文、production code 或 tests，未运行 `make check` 或 monorepo pre-commit。
