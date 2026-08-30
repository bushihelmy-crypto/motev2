# S02 \`GSP-A06\` 单项实施设计第二次评审

> **结论：第二次技术复审通过，没有发现新的技术阻碍。主实施方案已经具备提交 requirements owner 单项批准的设计证据；但本记录不代替 \`GSP-A06\` 批准，也不授权修改 production/tests。S02 继续保持 \`PENDING REQUIREMENTS APPROVAL / NOT APPROVED\`，四个 target behavior case 必须在批准后的同一 production + behavior 原子 change unit 中落地。**

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[主实施方案第 3.1.3 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#s02-gsp-a06)
- 当前主实施方案 SHA256：\`4cd3c3c8c4b778dbee22aa0181830ddd9d5040afbdc3ba3bafd8a24b4bb2115f\`
- 前次记录：[S02 单项实施设计评审](graph-semantics-preserving-simplification-s02-implementation-review.zh-CN.md)
- 前次记录 SHA256：\`10e7b9fa2b6bff76b5c6a3de917a31779c55fbacb25cbd3eb99f42277894b3b2\`
- 复核源码基线：\`35e7c95\`
- 依据：S02 主实施方案、requirements 第 2–7 节、当前 \`validation.py\` 与既有 graph validation/join/nested/owner tests
- 本文性质：独立 second-review record；只记录本轮裁决、理由和验证，不拥有 target shape、requirements 批准状态、production shape 或测试 shape
- 本轮不实施代码，不修改主实施文档、requirements、production 或 tests

## 2. 评审范围和裁决口径

本轮继续按用户确定的边界审核：

- 当前不实现持久化，State、Store、protocol、command、reducer、commit 和 recovery 边界保持 HARD KEEP；
- S02 只复用现有 validation owner、GraphDefinition/GraphNode nominal 类型、typed local lookup 和现有 edge variants；
- 删除重复 node projection、重复 traversal、per-edge END 临时 tuple 以及 join source 的重复 sort/set assembly；
- 不新增 helper、DTO、context、cache、第二 index、compatibility alias、runner 或 parallel execution path；
- 错误分类、错误文本、phase precedence、definition/edge/nested 原始顺序保持；
- behavior evidence 只使用 public/compiled observable behavior，不把 private local/source shape 变成永久门禁。

automated complexity/ratchet unit 仍按主实施方案标为 S02 \`NOT APPLICABLE\`。这只是不把该独立治理单元作为本轮
S02 设计批准条件，不能把它的结果写成 S02 通过，也不能放宽 S02 的结构净删除和零新增负债约束。

## 3. 总体裁决

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State / Store / 持久化边界 | **通过** | target 只改变 validation invocation-local facts；不进入 State、CompiledGraph、runtime、recovery 或 durable store |
| 唯一事实与 owner | **通过** | \`GraphDefinition.nodes\` 继续是定义事实；\`nodes_by_id\` 仅是当前 definition scope 的临时 typed lookup；validation 继续拥有 phase 和首错顺序 |
| 复用基础设计 | **通过** | 复用已有 \`GraphNode[GraphValueT]\`、\`NestedGraphNodeDefinition\`、dict、edge variants 和 existing exception types |
| node lookup 简化 | **通过** | 每个 \`_validate_definition()\` scope 一个 map；不跨 nested family 共享、不返回、不缓存、不冻结、不与旧 projection 双存 |
| edge/error 等价性 | **通过设计** | endpoint、conditional nested-source、route precedence、edge declaration order 和 phase order 可逐项保持 |
| join canonicalization | **通过（声明的 nominal domain）** | \`frozenset[GraphNodeId]\` 保留重复、known、self-target 和 unordered duplicate invariants；不扩大 forged input 契约 |
| 逻辑清晰 / 零新增负债 | **通过设计** | 不机械拆三个 variant helper；新增 production abstraction 为零，结构账本的删除面可核对 |
| A06 characterization / manifest | **通过** | exact signature、nominal type、target nodeid、失败条件、shape/tamper baseline 和三文件 planned manifest 已固定 |
| target implementation evidence | **待实施** | 四个 target case 当前仍不存在；baseline 绿色不能冒充 implementation verification |
| 是否可实施 | **不可直接实施** | 仍需 requirements-only 的显式 \`GSP-A06\` approval unit |

## 4. 相对前次评审的整改复核

### 4.1 scope-local index 的语义已闭合

主文档已把“一个 index”明确限定为每次 \`_validate_definition()\`、每个 definition validation scope 一个：

- parent 与 nested definition 各自拥有 local namespace；
- 递归调用不接收 parent map，不合并 graph-family map；
- map 只在 validation 调用栈内存在，不写回 \`GraphDefinition\`、\`CompiledGraph\`、State 或 runtime；
- parent/nested 同时活跃时可以各有自己的 local map，但不能共用或互相覆盖。

这消除了“全 graph family 只有一个 map”的错误解释，同时保留唯一 immutable definition truth。该项 **CLOSED**。

### 4.2 nominal domain 和 conditional identity 已明确

主文档把 dict membership 与 join \`frozenset\` 的等价承诺限定在声明的 \`GraphNodeId\`、
\`GraphNode[GraphValueT]\` 和 \`JoinEdge.sources\` nominal domain；没有借简化扩大宽输入、coercion 或 forged/untyped
Python object 的公共行为。

\`conditional_seen\` 保留当前的 \`set[tuple[GraphNodeId, str]]\`，不顺带迁移到 \`GraphRouteId\`，也不新增 wrapper 或
string discriminator。这是范围收敛，不构成 typing debt。若 future change 要收窄 route type，应另立 change unit。
该项 **CLOSED**。

### 4.3 evidence 与批准时序已区分

主文档现在明确区分：

1. 当前 production baseline（89 cases、单文件 Pyright）；
2. 尚未落地的四个 target behavior case；
3. 技术设计评审无阻断；
4. requirements owner 尚未批准 \`GSP-A06\`。

因此“技术评审通过”没有被写成“production 已实施”或“requirements 已批准”。四个 target case 的 exact path、
断言目标、失败条件和同一原子 implementation manifest 已固定，但在批准前不修改 tests。该项 **CLOSED**。

### 4.4 owner/writeback 与 review record 分工正确

主实施方案拥有 S02 target shape、结构账本、planned manifest 和实施顺序；review record 只拥有本轮裁决和验证。
requirements 第 7 节仍拥有批准状态。当前主文档的技术评审接受项回写没有把 review 记录变成第二 target，也没有
改变 \`GSP-A06\` 的未批准状态。该项 **CLOSED**。

## 5. 技术等价性复核

### 5.1 node identity loop 与 duplicate precedence

目标在原 node identity loop 中直接建立：

~~~python
nodes_by_id: dict[GraphNodeId, GraphNode[GraphValueT]] = {}
for node in definition.nodes:
    node_id = node.node_id
    require_graph_identity(node_id, kind="node")
    if node_id in (START, END):
        raise InvalidGraphIdentityError("START and END cannot be concrete graph nodes")
    nodes_by_id[node_id] = node
if len(nodes_by_id) != len(definition.nodes):
    raise DuplicateNodeError("graph definition contains duplicate node identities")
~~~

duplicate 判断留在整个 identity loop 之后，仍保证后续非法 node identity 先于 earlier duplicate 报错；duplicate 通过后才
允许 resources、entries、edges 和 nested recursion 消费该 map。由于声明的 identity 是严格字符串，dict key equality 与
原 tuple/frozenset membership 等价。该替换不会引入第二 definition truth。

### 5.2 edge order 和局部 precedence

目标保留完整顺序：

~~~text
graph identity → version → definition collision/recursion
→ 全部 node identity/reserved checks → duplicate node → resources
→ entries → 单遍 definition.edges → resume binding → definition.nodes 原始顺序的 nested recursion
~~~

edge loop 仍按 \`definition.edges\` 原始顺序处理：

- direct/conditional endpoint membership 先于 duplicate；
- conditional route identity 先于 endpoint，endpoint unknown 先于 nested-source；
- join 先做最小 source 数量、source duplicate、known source、target/self-target，再做 duplicate join；
- 不按 variant 分组，不排序 edge，不改变 exception class/text。

target 用 \`edge.target == END\` 分支替代 \`(*known, END)\` 临时 tuple；在声明的 nominal string domain 内，首错类型和文本
不变。conditional nested-source 通过同一 \`nodes_by_id\` 的 nominal node value 做 \`isinstance\`，不再建立
\`nested_ids\` 第二 projection。

### 5.3 join identity

目标只构造一次 \`frozenset(edge.sources)\`，用长度比较保留 duplicate-source 检查，再复用该值完成 known-source、
self-target 和 \`join_seen\` key。当前 \`JoinEdge.sources\` 的语义是无序 identity；在严格
\`tuple[GraphNodeId, ...]\` domain 内，source declaration order 不进入 compiled topology 或错误文本，因此：

- \`(a, b)\` 与 \`(b, a)\` 仍是同一个 unordered join；
- duplicate source 仍 fail closed；
- unknown source、unknown target、self-target 和 too-few-sources 的错误类别不变；
- 不保存或回写 frozenset，不改变 \`JoinEdge\` 的 tuple shape。

这项等价性只覆盖 typed nominal domain。若实现希望对 unhashable、混合类型或其他 forged object 保持额外错误行为，
必须另行制定并评审；S02 不可静默扩大范围。该项 **CLOSED（nominal domain）**。

### 5.4 nested recursion、resource 和 public owner

map 只服务当前 definition 的 node membership/nominal lookup；resource validation 仍由
\`_validate_resources()\` 拥有，definition-family visit/collision/recursion 仍由现有 \`visits\` 和
\`_DefinitionVisit\` 拥有，nested recursion 仍按 \`definition.nodes\` 原始顺序发生。public facade 仍只有
\`mote_kernel.execution.Graph\`，validation 不创建 runner/session/store。

这保持了既有 phase owner 和 nested sibling first-error behavior，不需要新增 phase helper、context 或 callback。

## 6. Evidence 复核

### 6.1 当前 HEAD baseline

本轮在源码未实施 S02 target 的 \`35e7c95\` 上实际运行：

~~~text
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/graph/test_validation.py \
  tests/execution/graph/test_join.py \
  tests/execution/graph/test_nested_graph.py \
  tests/execution/test_executor.py::test_nested_conditional_source_is_rejected_at_compile_time \
  tests/execution/test_executor.py::test_nested_invalid_completion_enters_error_draining \
  tests/architecture/test_generic_integrity.py \
  tests/architecture/test_source_discipline.py \
  tests/architecture/test_dependency_direction.py \
  tests/architecture/test_graph_execution_ownership.py
→ 89 passed

pyright src/mote_kernel/execution/graph/validation.py
→ 0 errors, 0 warnings, 0 informations

git diff --check
→ passed
~~~

这证明当前 baseline 与 owner/type/import 边界，不能证明 target implementation 已经发生。

### 6.2 target case 状态

以下四个 exact nodeid 在当前 tests 中仍不存在，符合主文档的 \`TARGET — PENDING IMPLEMENTATION\` 状态：

- \`test_validation_checks_all_node_identities_before_duplicate_nodes\`
- \`test_validation_preserves_edge_declaration_order_across_nominal_variants\`
- \`test_conditional_endpoint_error_precedes_nested_source_error\`
- \`test_nested_validation_preserves_definition_order_error_priority\`

它们的职责划分合理：分别冻结 identity/duplicate precedence、跨 variant edge declaration order、conditional endpoint
precedence 和 sibling nested definition order。获批后必须在同一个 production + behavior unit 中加入，且只能断言
compile/public observable behavior；不能以当前 89-case baseline 冒充绿色 target evidence。

### 6.3 shape、manifest 和 complexity 边界

S02 不改变 dataclass/public field shape；既有 topology immutability、nested recursion/collision、invalid join、
unknown endpoint、generic、owner 和 dependency cases足以作为 shape/tamper baseline。主文档没有新增 S02-specific
AST、legacy、private-source-shape 或 source-layout gate，符合零新增门禁要求。

批准后的 production + behavior planned manifest 仍精确为：

~~~text
mote-kernel/src/mote_kernel/execution/graph/validation.py
mote-kernel/tests/execution/graph/test_validation.py
mote-kernel/tests/execution/graph/test_nested_graph.py
~~~

当前工作树没有 S02 production/test implementation diff；本次 second review 的 actual changed-file 只有本文。
复杂度 gate 本轮不作为 S02 裁决；当前独立复杂度草案的 ratchet limit 与现状 improvement 尚未单独 writeback，不能
把完整 \`make check\` 或“所有 gate 通过”写入 S02 证据。

## 7. 非阻断但必须保留的约束

1. 前次 review record 中的 \`PENDING REVIEW / NOT APPROVED\` 是当时的历史状态；本记录确认技术复审通过，但不修改历史记录。
2. “一个 map”始终表示每个 active definition validation scope 一个，绝不能改成 family-global map。
3. nominal-domain 限定不是实现时放宽错误边界的许可；若 actual diff 触及 forged/untyped malformed behavior，应停止并重新评审。
4. 四个 target case 在批准前仍不得先行修改；implementation 后必须与 production 同一原子 diff 落地并复跑适用 checks。
5. requirements-only approval unit 必须单独把 S02 从未批准 P2 迁移为仅限本节 exact target；本 review 不代替该批准。
6. actual implementation 若保留旧 projection、增加 helper/DTO/cache/第二 index、改变 exception/phase/order、触及 State/Store/
   protocol/persistence 或越出三文件 manifest，均立即停止。

## 8. 最终状态

~~~text
S02 TECHNICAL DESIGN REVIEW: PASS / NO NEW BLOCKER
S02 GSP-A06 DESIGN EVIDENCE: READY FOR REQUIREMENTS OWNER APPROVAL
S02 REQUIREMENTS APPROVAL: PENDING
S02 TARGET BEHAVIOR CASES: PENDING IMPLEMENTATION
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
~~~

本轮没有需要驳回的外部评审意见，因此不另写 response MD；该结论不改变 requirements 的批准状态。

## 9. 本次 review change unit

本文件是本次第二次独立评审的唯一 actual changed-file：

~~~text
mote-kernel/docs/graph-semantics-preserving-simplification-s02-implementation-second-review.zh-CN.md
~~~

本轮未修改主实施文档。主实施方案第 3.1.3 节仍是 S02 target shape 的唯一 owner，requirements 第 7 节仍是
\`GSP-A06\` approval status 的唯一 owner。
