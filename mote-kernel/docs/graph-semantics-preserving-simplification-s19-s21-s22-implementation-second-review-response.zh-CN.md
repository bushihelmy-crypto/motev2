# S19 / S21 / S22 实施方案第二次独立技术评审回复

## 1. 回复信息

- 日期：2026-08-24
- 状态：`R9 ACCEPTED / IMPLEMENTATION OWNER UPDATED / PENDING THIRD INDEPENDENT REVIEW / NOT APPROVED`
- 第二次评审：[S19 / S21 / S22 实施方案第二次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review.zh-CN.md)
- 第二次评审 SHA256：`9a964c3b25338a5b5c9d5f32da0302b099015126de447ab291e50140f8c97a81`
- 第二次评审绑定的旧实施方案 SHA256：`d3ef4eabcd6e109fa0a6f08d8e175400719c8d12a41c49e535cea85f0da9459a`
- 历史回复：[首次独立技术评审回复](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review-response.zh-CN.md)，SHA256
  `c888b80966c5ceee846914bdc0ecf7e056070f0c1957b78789e637e371b09548`
- 整改后的唯一 implementation owner：
  [S19 / S21 / S22 实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- 整改后 implementation owner SHA256：`07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`
- requirements owner：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)；继续唯一拥有逐单元
  `GSP-A06` 批准/关闭状态

本文只拥有 R9 disposition、只读复现证据、断言范围澄清和整改索引，不拥有 S19/S21/S22 的 current target shape。signature、
caller order、case matrix、manifest、门禁与停止条件只以整改后的实施方案为准。第二次评审仍只裁决其绑定的旧 SHA；本回复和文档
整改均不构成 `GSP-A06` 批准。

## 2. 总体回复

R9 有道理，完整接受其证据缺口判断。decoder 返回非 `Graph.Values`，与 decoder 返回 owner-produced `Graph.Values`、但字段名称或
exact nominal value type 不匹配 compiled node-input descriptor，是两条不同的 admission boundary。旧 §4.5 只直接覆盖前者，却把
后者笼统计入 `malformed codec/tamper`，case-level 证据不足。

整改后的 disposition：

```text
R9 EVIDENCE GAP: ACCEPTED / WRITTEN BACK
S19: PENDING THIRD INDEPENDENT REVIEW / NOT APPROVED / NOT IMPLEMENTED
S21: READY FOR EXPLICIT SATISFIED / CLOSED — KEEP DISPOSITION
S22: READY FOR EXPLICIT SATISFIED / CLOSED — KEEP DISPOSITION
PRODUCTION / TEST / REQUIREMENTS / STATE / STORE / PROTOCOL: UNTOUCHED
PERSISTENCE / ERROR-RECOVERY FEATURES: NOT IMPLEMENTED
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED / NOT RUN
```

## 3. R9 disposition

### 3.1 接受：补齐 owner-produced decoded values 的 descriptor admission 证据

实施方案第 4.5 节已把两类 typed decoder tamper 纳入原计划的同一个 behavior case：

- owner-produced decoded values 的字段名称集合不匹配 compiled descriptor；
- owner-produced decoded values 的字段值不满足 descriptor 的 exact nominal type。

该节现在固定两类失败的 `GraphValueAdmissionError` 分类、消息、`__cause__ is None`、validation 后 `encode → decode` 时序、无
`PreparedResume` 返回以及输入 authoritative State/frame snapshot 不变。它也明确 nominal failed/interrupt override、wrong
settlement、stale interrupt ID 与最终 frontier validation 的原有证据继续保留。

这些 subcase 没有拆出新 nodeid。因此一个 planned behavior case、full-suite target count 和两文件 maximum manifest 都保持不变；
没有修改 `engine/resume_input.py`、`graph/values.py`，也没有新增 validator、codec port、测试文件或 production owner。规范性的
exact nodeid、error assertion、target count 和 manifest 统一见实施方案第 4.5、7.1、9.1–9.2 节；本文第 4 节的错误文本只记录
只读复现事实，不成为第二份 target owner。

### 3.2 接受语义边界；不接受把 invocation-local accumulator 变成 direct test shape

第二次评审要求 frame admission 先于 command、replacement、admitted input 或 authoritative State/frame mutation，其语义要求成立，
并已保留为实施方案第 4.3 节的 exact caller order 和第 4.5 节的 case predicate。

但若把这句话解释成测试必须直接读取或冻结 `actions`、`replacements`、`admitted_inputs` 的中间状态，则不采纳。它们是一次
`resume()` 调用内的局部 accumulator，没有独立 owner port 或可观察生命周期；直接断言只能依赖源码/AST/private instrumentation，
或者为测试新增暴露面，都会制造用户明确排除的 legacy/private-source-shape gate 或新的测试缝隙负债。

采用的无负债证据边界是：typed codec 记录 validation 与 `encode → decode` 的可观察顺序；tamper 抛出既有精确错误且不返回
`PreparedResume`；调用输入的 authoritative State 和 frame snapshot 保持不变。局部 accumulator 只能在完整 frame admission 后更新，
则由 exact caller-order 审计和 actual production diff review 证明。这里拒绝的是不恰当的 direct-test 机制，不是拒绝 mutation-order
语义。

## 4. 只读复现证据

在未修改 production/tests 的前提下，以 compiled node-input descriptor 要求单字段 `value: str` 的 graph 复现：

```text
decoder result: Graph.values(other="input")
error: GraphValueAdmissionError
message: node input names do not match the compiled descriptor: expected ('value',), got ('other',)
cause: None

decoder result: Graph.values(value=True)
error: GraphValueAdmissionError
message: node input value for 'value' does not have its exact declared type
cause: None
```

两条路径都先通过 decoded-value nominal-owner 检查，再由既有 node-input frame admission 拒绝。这证明 R9 指向的是当前真实且独立的
边界，不是重复底层 values-contract 测试，也不要求新增 production validation。

探针完成后，三个相关 production owner 仍为原 SHA，且相对 Git baseline 零 diff：

```text
src/mote_kernel/execution/executor.py
98f0a1725c9fd618cbd28bd6a8d28ef0985106915208b5a197ff202de4d66ebb

src/mote_kernel/execution/facade.py
d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9

src/mote_kernel/execution/invocation.py
5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4
```

## 5. 实施方案回写索引

- 第 4.5 节：补充 decoded descriptor name/exact-type tamper subcase、错误/cause、codec 时序、无返回值与 State/frame
  immutability predicate，并明确 local accumulator 的非 private-shape 断言边界；
- 第 7.1 节：把两类 decoded descriptor mismatch 纳入 S19 per-unit exact-shape/tamper applicability；
- 第 7.1、9.3 节：把后续绑定对象更新为第三次独立评审；
- 第 1 节：登记第二次评审及本回复的 owner 边界，状态保持 `NOT APPROVED / NOT IMPLEMENTED`。

第二次评审文件、首次评审/回复、requirements、主实施方案均保持原样，历史裁决没有被静默改写。

## 6. 本次 docs-only change unit 与后续顺序

本次 exact changed-file manifest：

```text
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review-response.zh-CN.md
```

合法后续顺序固定为：

1. 第三次独立技术评审绑定 implementation owner SHA256
   `07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`；
2. requirements owner 对三个 disposition unit 分别作显式裁决；
3. S21/S22 只允许 `SATISFIED / CLOSED — KEEP` owner writeback，不创建空 implementation commit；
4. S19 只有获得其 exact reviewed SHA 的 `GSP-A06 APPROVED` 后，才可按实施方案的两文件 manifest 实施和验收；
5. 在此之前，production 和 tests 保持不变。

第三次 review 通过、R9 回复完成、只读探针通过或 S21/S22 empty manifest，均不等于 S19 已获批准。
