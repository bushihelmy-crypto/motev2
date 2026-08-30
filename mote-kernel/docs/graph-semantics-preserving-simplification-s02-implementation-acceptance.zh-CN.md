# S02 `GSP-A06` 实施验收

> **结论：S02 三文件 production/test 改动验收通过，代码状态为 `IMPLEMENTED / VERIFIED`。未发现语义、owner、State 或持久化边界阻碍。本记录提出的 implementation-owner writeback 已在主实施方案第 3.1.3 节完成；S02 当前为 `IMPLEMENTED / VERIFIED / DELIVERY RECORD CLOSED`。**

## 1. 验收信息与范围

- 验收日期：2026-08-24
- 验收对象：实施方案第 3.1.3 节 S02 `GSP-A06` exact target
- 批准依据：requirements 第 7 节已将 S02 标为 `GSP-A06 SATISFIED`，且只授权第 3.1.3 节目标
- 复核基线：`35e7c95`
- 本次 production/test implementation actual manifest（仅三文件）：

  ```text
  mote-kernel/src/mote_kernel/execution/graph/validation.py
  mote-kernel/tests/execution/graph/test_validation.py
  mote-kernel/tests/execution/graph/test_nested_graph.py
  ```

- 本文件是独立验收记录，不拥有 target shape、requirements approval 或 production owner；不把验收写回主实施方案。
- 未将全仓测试作为 S02 的验收前置条件；以下只使用 S02 exact target、既有 graph/owner scoped behavior 和适用 source/lint gate。

## 2. 代码审核结论

| 审核项 | 结果 | 证据 |
| --- | --- | --- |
| 唯一事实源 | 通过 | `GraphDefinition.nodes` 仍是 immutable definition truth；`nodes_by_id` 只在每次 `_validate_definition()` 的当前 definition scope 内存在，不写回 definition、compiled graph、runtime 或 State |
| node lookup 简化 | 通过 | identity loop 同时建立 `dict[GraphNodeId, GraphNode[GraphValueT]]`；`node_ids`、`known`、`nested_ids` 三份 node projection 已删除 |
| duplicate 错误优先级 | 通过 | 完整 node identity loop 结束后才检查 map 长度，后续非法 identity 仍先于 earlier duplicate 报错 |
| edge 顺序与错误分类 | 通过 | 仍按 `definition.edges` 单遍、声明顺序处理；direct/conditional endpoint、route、nested-source 的局部 precedence 保持 |
| join identity | 通过 | 单次 `frozenset(edge.sources)` 同时承担 source duplicate、known、self-target 和 unordered duplicate key；未改变 `JoinEdge` tuple shape |
| nested definition 顺序 | 通过 | nested recursion 仍按 `definition.nodes` 原始顺序；每个 nested scope 使用自己的 local map |
| 零新增负债 | 通过 | 未新增 helper、DTO、field/property、context、cache、第二 index、compatibility alias、runner 或 public export；未新增 scan/sort/freeze |
| State / Store / protocol / persistence | 通过 | actual implementation manifest 未触及 `state/**`、Store、command/reducer、commit、protocol 或任何持久化事实；当前仍不实现持久化 |

一次性 source review 查询结果：

```text
旧 projection / END 临时 tuple / join sort-set pattern：无输出（exit 1）
nodes_by_id、direct_seen、conditional_seen、join_seen：仅出现于 validation owner 内
```

该查询只作为本次审核证据，不新增永久 source-shape 或 AST 门禁。

## 3. S02 行为证据

四个 planned target case 均已落地，并通过 public `compile_graph()` observable behavior：

| Target case | 验收语义 | 结果 |
| --- | --- | --- |
| `test_validation_checks_all_node_identities_before_duplicate_nodes` | 完整 identity loop 后才报告 duplicate；后续非法 identity 保持优先 | PASS |
| `test_validation_preserves_edge_declaration_order_across_nominal_variants` | direct/conditional/join 混排时由首个声明 edge 决定错误 | PASS |
| `test_conditional_endpoint_error_precedes_nested_source_error` | unknown endpoint 先于 nested conditional source 错误 | PASS |
| `test_nested_validation_preserves_definition_order_error_priority` | sibling nested definition 按声明顺序决定首错 | PASS |

精确复跑结果：

```text
4 target cases                         → 4 passed
S02 实施方案登记的 graph/owner scoped suite → 96 passed
```

既有 validation、join、nested、topology、executor nested-boundary 及 generic/source/dependency/ownership cases 均包含在上述 scoped suite 中；没有新增测试文件，也没有把 private local shape 写成测试契约。

## 4. 适用工程门禁

```text
ruff check src tests                  → passed
ruff format --check src tests          → passed
pyright src/mote_kernel/.../validation.py → 0 errors
S02 三文件 scoped pre-commit           → passed（kernel-complexity skipped）
git diff --check                       → passed
```

requirements 与实施方案已明确：独立 complexity/ratchet unit 对 S02 `NOT APPLICABLE`。因此本验收不把 `make check` 或 complexity limit 的 ratchet 结果冒记为 S02 evidence，也不因该独立治理单元要求修改 `pyproject.toml`、Makefile 或新增门禁。全仓 dirty worktree 中与 S02 无关的文件保持原样，不纳入本 actual manifest。

## 5. Implementation-owner writeback（已闭合）

requirements 第 7 节已经记录 S02 `GSP-A06 SATISFIED`；主实施文档第 3.1.3 节现已回写为 `IMPLEMENTED / VERIFIED`，并记录：

```text
S02 GSP-A06: SATISFIED / APPROVED — reviewed exact target only
S02 IMPLEMENTATION: IMPLEMENTED / VERIFIED
```

本次 owner writeback 只更新主实施文档，回写：

- `IMPLEMENTED / VERIFIED` 状态；
- 三文件 actual manifest；
- 四个 target case 的 PASS 结果；
- source review、no-State/no-persistence 和适用 gate 结果；
- complexity gate 的 `NOT APPLICABLE` 口径。

该项是文档闭环，现已完成；不构成 S02 production 技术阻碍，也不扩大批准到其他 target/SHA。

## 6. 最终裁决

```text
S02 production code:             ACCEPTED / VERIFIED
S02 target behavior:             PASS (4/4)
S02 scoped behavior/owner gate:  PASS (96 passed)
unique truth / reuse / zero debt: PASS within approved S02 ledger
State / Store / persistence:     HARD KEEP / untouched
implementation-doc writeback:    COMPLETE
overall delivery record:         CLOSED / VERIFIED
```

本轮没有需要驳回的技术项，不另写 response MD。验收记录本身的 actual changed-file manifest 只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s02-implementation-acceptance.zh-CN.md
```
