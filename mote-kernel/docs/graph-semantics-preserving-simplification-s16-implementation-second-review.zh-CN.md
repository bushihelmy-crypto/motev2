# S16 GSP-A06 单项实施设计第二次独立技术评审

> **结论：CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL。** 首轮 R2、R3、R4、R5 的整改已核对通过，七个 planned public case 也确实足以覆盖四个新增 canonicality raise 分支；但当前 owner 文档和 response 对 case-to-branch 的映射有一处可机械复现的错误，且 R6 仍未把 __cause__ 结果写入 exact behavior predicate。两项都是可在 docs-only unit 中修正的证据问题；本记录不授权 production、tests、requirements、State、Store、protocol 或任何持久化/错误恢复变更。

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- 本次绑定 owner SHA256：f20fd204a89c231a6fccb04d7a2e1e53469b6870515c2bef3601b0b0a25d411a
- 首轮独立评审：[S16 GSP-A06 单项实施设计独立技术评审](graph-semantics-preserving-simplification-s16-implementation-review.zh-CN.md)，SHA256：962c4686e9d0c33315e727030fb2085b89241d46effbb952c0f84b5ea99b5702
- 本轮评审回复：[S16 GSP-A06 实施设计评审回复](graph-semantics-preserving-simplification-s16-implementation-review-response.zh-CN.md)，SHA256：1be7cb350ebaa3891ea43de34557f8d415e94211f7b3eaf9301f45719d3b0bb6
- 声明源码基线：Git f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497
- production 基线 SHA256：src/mote_kernel/execution/invocation.py → 4165f689af384f9b91080b432328ce3003f9e4b4308bcf34960e1d3db0550f5d
- frame owner 基线 SHA256：src/mote_kernel/execution/run_context.py → bf196695bce1687f0bd9554d3a8615e9afc5dbfa1bedbc859cd199e8ff54f648
- behavior 基线 SHA256：tests/execution/test_continuation_integrity.py → d900f3b812f9618587182ed4e86974cfc7dc0d3fa0de647ad9f797693bdaa17e
- 本文性质：第二次独立 technical review record；只拥有本轮裁决、问题和验证证据，不拥有 S16 target、requirements 批准状态、production shape 或测试 shape
- 本轮 change unit：只新增本文；保留工作树中所有既有用户修改，不把它们纳入 S16 manifest

## 2. 审核边界与总体裁决

本轮继续遵守用户约束：唯一事实源、复用现有 execution/frame 基础设施、零新增结构/所有权负债；不实现持久化，不新增 retry、fallback、checkpoint、failover、第二 recovery runner 或其他错误恢复能力；automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均不参与本轮裁决，也没有新增或扩写这些 gate。

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、command、reducer、Store、protocol、持久化 | 通过 / HARD KEEP | owner writeback 与 response 均保持 no-State/no-persistence；planned manifest 仍只包含 invocation.py 与既有 continuation-integrity test |
| 错误恢复范围 | 通过 / 不扩展 | 仍只有既有 continuation admission 与 recovered preflight；没有新增 retry、fallback、checkpoint、failover 或第二 runner |
| pairwise exact target 与 Ruff 格式 | 通过 | §5.3 代码块与隔离 target 源码均通过 Ruff 0.16.2 format check；未新增 helper、DTO、第二 validator 或额外 import |
| nominal-domain 等价性 | 通过（有界） | compiler-produced、hashable、total-order 一致的 exact nominal coordinate 域内等价证明成立；forged unhashable/mixed inner field 明确不在 contract |
| R1 coverage branch set | 设计上闭合，文档映射未闭合 | 七个 case 确实能覆盖四个 raise，但 §8.2/§13.3 把会被更早 segment 短路的 case 误列为另一个 canonicality branch 的覆盖证据（R1′） |
| R2 architecture nodeid | 通过 | 路径已修正为 tests/architecture/test_source_discipline.py，9 个 nodeid 实际 9 passed |
| R3 exact format | 通过 | canonical output 已回写，stdin 片段与隔离完整 invocation.py 都已格式化 |
| R4 tamper 边界 | 通过（带边界） | listed scalar probe 的 baseline/target type、text、cause 一致；unhashable divergence 保留为 contract 外停止条件 |
| R5 结构账本 | 通过 | sorted-result tuple 与 dynamic label 已标为上一行的非计数说明，13 个旧构造/分派点可机械复算 |
| R6 public behavior evidence | 部分闭合 | response 合理复用既有跨层 contract、拒绝新增 fixture；但 __cause__ 仍未在 M0/exact predicate 中明确（R6′） |
| GSP-A06 / 是否可实施 | 未批准 | R1′、R6′ 修正并重新绑定 owner SHA 后才可再次评审；本轮不授权生产/测试实现 |

## 3. 已闭合的首轮问题

### 3.1 R2：architecture nodeid

owner §13.2 现在使用真实 nodeid：

~~~text
tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner
~~~

按 owner 给出的九个 nodeid 逐字执行得到 9 passed。不存在的 test_graph_execution_ownership.py 路径已不再出现在命令中。

### 3.2 R3：Ruff canonical output

§5.3 的四个 guard 现在是 Ruff 0.16.2 的 canonical layout。将代码块作为 stdin 检查，以及将等价 target 应用于隔离的 invocation.py，均得到 format check 通过；前三个 guard 为单行，child-boundary guard 按 formatter 保持生成器表达式折行。该修正没有改变算法、错误 literal、phase 顺序或 owner。

### 3.3 R4：exact nominal-domain limit

owner 已明确把等价证明、accepted/rejected 集合和 canonicality error contract 限定为 compiler-produced、hashable、total-order 一致的 exact nominal coordinate domain。一次性 baseline-vs-target probe 的结果如下（probe 运行于隔离副本，不进入 manifest）：

| inner scalar tamper | baseline | target | cause |
| --- | --- | --- | --- |
| descriptor identity | SnapshotMismatchError: continuation graph input descriptor does not match its scope | 相同 | None / None |
| scope/run identity | SnapshotMismatchError: continuation graph input belongs to an unknown scoped run | 相同 | None / None |
| activation superstep | SnapshotMismatchError: continuation publication has inconsistent coordinates | 相同 | None / None |
| node identity | SnapshotMismatchError: continuation publication has inconsistent coordinates | 相同 | None / None |
| enum/int field | SnapshotMismatchError: continuation graph input descriptor does not match its scope | 相同 | None / None |

把 inner definition_id 伪造成不可 hash 的 list 时，baseline 为 TypeError，target 为 typed SnapshotMismatchError；这正是 owner 文档已声明的 forged unhashable/mixed outside-contract 边界，不得借此要求 production 新增 catch/normalizer。listed scalar probe 的结果与第 14 节停止条件一致；owner 可以继续把该 probe 标为实施前一次性证据，而不能把 outside-contract 行为宣称为公共契约。

### 3.4 R5：结构账本

§7 现在只把四个 coordinate projection、一个 heterogeneous dispatch tuple、四个 set(...) 与四个 tuple(sorted(...)) 计入 13 个旧构造/分派点；sorted-result tuple 与 dynamic label 明确标为不可加总的说明。该口径不会引入 complexity gate，也没有发现新的重复事实。

## 4. R1′（阻断）：case-to-branch evidence 映射仍不准确

owner §8.2 与 §13.3，以及 response 第 3 节，都写成“graph input 由首行与两个 precedence case 覆盖、publication 由两个 precedence case 与 recovered case 覆盖”。按表中七个 case 的实际首错逐项展开，映射应是：

| canonicality raise branch | 实际会到达该 raise 的 case | 当前文字的问题 |
| --- | --- | --- |
| graph input | test_complete_continuation_rejects_descending_frame_coordinates；test_continuation_validation_keeps_canonical_segment_order | canonicality_before_content_precedence 的 graph-input segment 只有一个 canonical coordinate，不会到达 graph-input canonicality raise |
| publication | test_continuation_validation_keeps_canonicality_before_content_precedence；test_recovered_continuation_rejects_noncanonical_frame_coordinates | canonical_segment_order 同时破坏 graph input 与 publication，但按固定 segment 顺序先在 graph input raise；不能算 publication branch evidence |
| resume input | test_complete_continuation_rejects_descending_resume_input_coordinates | 映射正确 |
| child boundary | test_complete_continuation_rejects_descending_child_boundary_coordinates | 映射正确 |

test_continuation_validation_keeps_shape_before_canonicality_precedence 先命中 publication shape error，也不是任何 canonicality raise 的 coverage。因而七个 case 的集合本身是 branch-complete（四个 raise 都有到达路径），但当前“两个 precedence case”表述不是可机械复现的 evidence matrix。必须在 owner 文档和 response 的对应说明中把映射改为上表，或为每个 branch 明确列出真正到达的 case；不需要因此增加测试、production 文件或 legacy/AST gate。

这项修正很小，但属于 GSP-A06 的 case-level evidence，而不是可由总 coverage 100% 替代的文字润色。错误映射若保留，会让后续复核者错误地认为某个 precedence case 已覆盖一个它实际永远到达不了的 branch。

## 5. R6′（阻断）：__cause__ 仍未写入 exact behavior predicate

response 第 7.1 节接受了完整 str(error)、phase/segment 和 mutation-free 证据，也合理拒绝把新的 CommitLog、node/resource call-counter fixture 搬入 S16 文件；既有四个跨层 contract test 足以作为 owner-level 交叉证据。这一取舍符合零新增负债与基础设施复用边界。

但首轮 R6 明确要求固定 __cause__，而当前 §8.2/M0 只要求 str(error)，没有写明 canonicality/precedence 的公开 Graph.run() 异常应为 error.__cause__ is None，也没有把该结果放入一次性 source/evidence predicate。当前 baseline 与隔离 target probe 都显示 listed scalar case 的 cause 为 None；因此无需新增 fixture，只需在 owner 文档的 M0/表格中补一个精确断言或等价 source-review predicate：

~~~python
assert raised.value.__cause__ is None
~~~

该断言应只针对直接 canonicality/precedence SnapshotMismatchError，不改变既有 content-admission raise ... from error 的 cause contract。若实际 target 改变 cause chaining，仍必须按第 14 节停止并保持 production 现状。

## 6. Verification evidence

### 6.1 当前 baseline（production/tests 未修改）

三份基线文件 SHA256 与 owner 第 1 节一致，且相对 Git f9182fa... 无 diff。当前工作树只读复跑结果：

~~~text
tests/execution/test_continuation_integrity.py → 34 passed
tests/execution                              → 563 passed
tests/state/graph_state                       → 206 passed
9 active architecture nodeids                 → 9 passed
all tests excluding complexity gate            → 826 passed, coverage 100.00%
pyright                                       → 0 errors, 0 warnings, 0 informations
ruff check / ruff format --check               → passed
~~~

### 6.2 Isolated target planning probe

在 /tmp/mote-s16-probe... 隔离副本中仅应用 pairwise target，并加入 publication、resume-input、child-boundary 三个 branch probes，得到：

~~~text
tests/execution (complexity gate excluded) → 829 passed
coverage                                   → 100.00%
invocation.py coverage                    → 100.00%
Ruff format                               → already formatted
~~~

这证明四个 target raise 分支可在当前 nominal/public owner 边界内闭合，不冒充七个最终 behavior case 已实施，也不修改仓库 manifest。

### 6.3 门禁范围

make check 未运行，因为其无条件包含用户明确排除的 complexity-ratchet；本轮没有运行、添加或修改 complexity/legacy/private-source-shape gate。current behavior、strict typing、active owner/dependency、lint、format 和 coverage 复核仍按文档原口径执行。

## 7. 当前状态与后续顺序

~~~text
S16 FIRST REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S16 SECOND REVIEW: CHANGES REQUESTED
S16 R1 COVERAGE SET: BRANCH-COMPLETE / MAPPING WRITEBACK REQUIRED
S16 R2: CLOSED
S16 R3: CLOSED
S16 R4: CLOSED WITH NOMINAL-DOMAIN LIMIT
S16 R5: CLOSED
S16 R6: CAUSE PREDICATE WRITEBACK REQUIRED
S16 GSP-A06: NOT APPROVED
S16 PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
S16 STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
S16 AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SHAPE GATES: USER-EXCLUDED
~~~

整改只应发生在 S16 owner 文档的独立 docs-only unit 中，并同步修正 response 对应的 branch mapping；重新计算 owner SHA 后需进行第三次独立评审。requirements owner 在新 SHA 获得 PASS / READY FOR REQUIREMENTS OWNER APPROVAL 前不得记录 GSP-A06，也不得修改：

~~~text
src/mote_kernel/execution/invocation.py
tests/execution/test_continuation_integrity.py
src/mote_kernel/execution/run_context.py
State / Store / protocol / persistence artifacts
~~~

## 8. 本次 review change unit

本文件是本次第二次独立评审的唯一 actual changed-file：

~~~text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-second-review.zh-CN.md
~~~

它不修改 owner、response、首轮 review、requirements、主实施方案、production、tests、State、Store、protocol、persistence 或任何门禁 artifact；不把 review record 变成 target 或批准状态的第二事实源。
