# S02 GSP-A06 单项实施设计第三次评审

> **结论：第三次复核通过，没有发现新的技术阻碍。第 3.1.3 节的二审回写、owner 分工、exact target、no-State/no-persistence 边界和三文件 planned manifest 均保持闭合；S02 现在具备提交 requirements owner 批准的条件，但仍未获 GSP-A06，不能直接修改 production/tests。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：主实施方案第 3.1.3 节（S02 GSP-A06）
- 当前主实施方案 SHA256：a386534d3657c15485842bf63657f296805614f7c3bea43505f161f5e14d66b2
- 前次记录：S02 单项实施设计第二次评审
- 前次记录 SHA256：0424a7b5464a3bc8d7b119251ef594cf65c47920cd10c29f3094abba3d01db46
- 复核源码基线：35e7c95
- 依据：S02 主实施方案、requirements 第 2–7 节、当前 validation.py、既有 validation/join/nested/topology/owner tests
- 本文性质：独立 third-review record；只记录本轮裁决和验证，不拥有 target shape、requirements approval 或 production/test shape
- 本轮不实施代码，不修改主实施文档、requirements、production 或 tests

## 2. 总体裁决

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、Store、protocol、持久化 | 通过 | target 仅改变 validation invocation-local 工作事实；State/no-persistence 继续 HARD KEEP |
| 唯一事实与 owner | 通过 | GraphDefinition.nodes 仍是唯一定义事实；nodes_by_id 只在当前 definition scope 内作 typed lookup |
| 基础设计复用 | 通过 | 复用已有 GraphNode[GraphValueT]、NestedGraphNodeDefinition、dict 和现有 edge/exception 类型 |
| 错误顺序与行为等价 | 通过设计 | identity、resource、entries、edges、resume、nested phase 和 edge 内局部 precedence 均有明确保持约束 |
| join identity | 通过（nominal domain） | 单一 frozenset 保留 unordered duplicate、duplicate-source、known/source 和 self-target 语义 |
| 零新增负债 | 通过设计 | 不新增 helper、DTO、context、cache、第二 index、兼容层、runner 或额外永久门禁 |
| A06 设计证据 | 通过 | exact signature/type、删除/新增上限、characterization、shape/tamper baseline 和 planned manifest 已固定 |
| target implementation evidence | 待实施 | 四个 target case 仍不存在，当前 baseline 不冒充 implementation verification |
| GSP-A06 / 是否可执行 | 未批准 | requirements 第 7 节仍只批准 S07、S01；S02 需独立 approval unit |

## 3. 对本次 owner 回写的复核

### 3.1 状态和历史记录一致

第 3.1.3 节现在使用：

~~~text
SECOND TECHNICAL REVIEW PASS / READY FOR REQUIREMENTS OWNER APPROVAL / NOT APPROVED
~~~

该状态准确区分了三件事：

1. 技术设计没有阻断；
2. 设计证据可以提交 requirements owner 审批；
3. requirements 尚未将 S02 记为 GSP-A06 satisfied，因此 production/tests 仍未获授权。

主文档记录的二审 target SHA 是二审发生时的主文档 SHA，而不是当前 owner writeback 后的 SHA；这是可审计的历史
对象引用，不是 hash 不一致。二审 record SHA 与当前独立文件一致，第一、二次 review 的历史记录也没有被倒写。

### 3.2 owner 分工和导航闭合

当前主文档已经链接两份独立 S02 review，并明确：

- 主实施方案第 3.1.3 节拥有 target shape、净删除账本、characterization、planned manifest 和实施顺序；
- review record 只拥有裁决、理由和验证；
- requirements 第 7 节拥有 GSP-A06 approval status；
- review/owner writeback/approval/implementation 各自使用独立 change unit。

这没有建立第二 target truth，也没有把 review pass 写成 approval。该项通过。

### 3.3 二审接受项没有改变 target 方向

本次回写只确认了以下既有约束，没有增加新的实现面：

- nodes_by_id 是每个 active definition validation scope 一个，不是 graph-family global map；
- frozenset/dict 等价只承诺声明的 nominal domain；
- conditional_seen 保留当前 tuple[GraphNodeId, str] shape；
- 四个 target case 仍是 TARGET — PENDING IMPLEMENTATION；
- complexity/ratchet 仍是独立治理单元，不作为 S02 的隐式批准或完整 make check 证据。

因此 owner 回写没有引入新的 helper、field、property、DTO、cache、第二 index 或 compatibility bridge。

## 4. 技术复核

### 4.1 node identity 与 duplicate precedence

目标在同一 node identity loop 中建立 typed nodes_by_id，并把 duplicate check 延后到完整 loop 之后。这样在
“较早 duplicate + 后续非法 identity”同时出现时，后续 InvalidGraphIdentityError 仍先于 DuplicateNodeError；
map 在 duplicate 检查通过前没有 consumer，重复 key 的最后一次赋值不会改变可观察错误。

duplicate 通过后，entries、resources、edges 和 nested recursion 才读取该 map。map 不写入 GraphDefinition、CompiledGraph、
State、runtime、recovery 或全局 cache。parent/nested scope 各自拥有 local map，定义声明顺序仍由原 tuple 递归。

### 4.2 edge order 和局部错误 precedence

主文档保持以下完整 phase 顺序：

~~~text
graph identity → version → definition collision/recursion
→ all node identity/reserved checks → duplicate node → resources
→ entries → edges → resume binding → nested recursion
~~~

edge loop 仍按 definition.edges 原始顺序，且保持：

- direct/conditional endpoint membership 在 duplicate 判断之前；
- conditional route identity 在 endpoint 之前；
- endpoint unknown 在 nested-source 检查之前；
- join 的 source count/duplicate/known/target/self-target 在 duplicate join 之前；
- 不按 variant 分组、排序或新增 helper。

在严格声明的 GraphNodeId 字符串域内，dict membership 与原 known tuple membership 等价；异常类型和文本不需要改变。

### 4.3 join canonicalization

JoinEdge.sources 是无序 source identity。单次 frozenset 构造同时服务 duplicate-source、known-source、self-target 和
unordered duplicate key；source tuple 不被改写，frozenset 不进入 compiled topology 或持久化事实。

该等价性明确限定在 tuple[GraphNodeId, ...] nominal domain。对于 unhashable、混合类型或其他 forged object，S02 不
新增或扩大行为契约；若 actual implementation 触及这些边界，必须停止并重新评审。

### 4.4 no-persistence、owner 和 public boundary

target 不读取或修改 GraphRunState、command、reducer、revision、commit callback、protocol、Store 或 persistence backend；
不创建 session/runner/repository/journal/checkpoint。Graph 仍是唯一公共 facade，validation 仍是唯一 validation owner。

S02 不改变 dataclass/public field shape，不需要新增 architecture AST、legacy、private-source-shape 或 source-layout gate。

## 5. Evidence 复核

### 5.1 当前 baseline

本轮在源码基线 35e7c95 上复跑：

~~~text
scoped validation/join/nested/executor/architecture suite
→ 89 passed

pyright src/mote_kernel/execution/graph/validation.py
→ 0 errors, 0 warnings, 0 informations

git diff --check
→ passed
~~~

当前没有 S02 production/test implementation diff。

### 5.2 target cases

以下四个 target nodeid 仍未在 tests 中实现：

- test_validation_checks_all_node_identities_before_duplicate_nodes
- test_validation_preserves_edge_declaration_order_across_nominal_variants
- test_conditional_endpoint_error_precedes_nested_source_error
- test_nested_validation_preserves_definition_order_error_priority

它们的职责和失败条件已经在主文档固定，且位于现有两个测试文件的 planned manifest 内。批准前保持不存在是当前流程
要求；批准后必须与 validation.py 同一原子 implementation unit 落地，才能把 S02 记为 IMPLEMENTED / VERIFIED。

### 5.3 complexity boundary

主文档明确将 complexity/ratchet draft 排除在 S02 gate 之外。本次复核不使用该独立治理单元批准或否决 S02，也不把
make check 的结果写成 S02 evidence。该排除不允许实现保留旧 projection、增加新抽象或扩大 manifest。

## 6. 非阻断约束

1. requirements approval 仍是唯一未完成的准入步骤；本 review 不代替显式 approval。
2. 四个 target case 尚未实施，不能提前声称行为证据闭合。
3. 实际 diff 触及 State/Store/protocol/persistence、保留 node_ids/known/nested_ids 双存、增加 helper/DTO/cache/
   第二 index、改变错误 precedence，或越出三文件 manifest 时，必须停止并重新评审。
4. nominal-domain 限定不是放宽 malformed boundary 的许可。

## 7. 最终状态

~~~text
S02 THIRD TECHNICAL REVIEW: PASS / NO NEW BLOCKER
S02 DESIGN EVIDENCE: READY FOR REQUIREMENTS OWNER APPROVAL
S02 GSP-A06: PENDING REQUIREMENTS APPROVAL / NOT APPROVED
S02 TARGET CASES: PENDING IMPLEMENTATION
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
~~~

本轮没有需要驳回的外部评审意见，因此不另写 response MD。

## 8. 本次 review change unit

本文件是本次第三次独立评审的唯一 actual changed-file：

~~~text
mote-kernel/docs/graph-semantics-preserving-simplification-s02-implementation-third-review.zh-CN.md
~~~

本轮未修改主实施文档。主实施方案第 3.1.3 节仍拥有 S02 target，requirements 第 7 节仍拥有批准状态。
