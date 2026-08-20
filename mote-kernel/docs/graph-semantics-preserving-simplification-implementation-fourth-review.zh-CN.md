# Graph 执行代码语义保持型简化实施方案第四次复审

> **结论：可继续完成 Phase 0 文档收口，但仍不得进入 Phase 1。** 第三次复审的主要 production target 已经回写；当前剩余问题集中在文档唯一事实源、S04/S06 原子边界和 Phase 0 实际产物，不需要重新打开已经闭合的 routing/recovery 设计。

## 1. 复审信息

- 复审日期：2026-08-19
- 代码基线：`feat/kernel-graph-node-io-contract@7944159`
- 复审对象：[再次修订后的实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 前序结论：[第三次复审](graph-semantics-preserving-simplification-implementation-third-review.zh-CN.md)
- 范围：只审查文档、现有 production shape、consumer 和门禁；不修改 production code 或 tests
- 原则：零增复杂度、唯一事实源、复用既有 owner、完整门禁、泛型关系不擦除、导包位于连续模块头、变量不保存可推导事实、逻辑保持最短闭环

## 2. 第三次复审条件核验

| 条件 | 结果 | 说明 |
| --- | --- | --- |
| S06 删除全部 forwarding projections | 已闭合目标 shape | direct `transition` field、删除对象和最终 `graph.transition.*` 路径均已写死 |
| S06 完整 producer/consumer/test 清单 | 基本闭合 | 当前代码交叉搜索未发现遗漏；但与 S04 的 publication 迁移边界重叠，见 R2 |
| Phase 0 文档 owner 与 exact paths | 部分闭合 | owner 表和路径已增加，但 requirements 与实施稿仍将拥有重复事实，见 R1 |
| changed-file manifest 门禁 | 基本闭合 | 不再硬编码四个历史文件，tracked/untracked 检查可执行；有两处措辞需收紧，见 N2 |
| requirements 与 README navigation 实际产物 | 未完成 | requirements 文件不存在，两个 README 尚未加入导航 |
| S08 固定复用现名 | 已闭合 | 固定 `_declared_joins()`，只增加 module-scope import，不再保留 rename 选项 |

P1/P2 计数仍正确：24 个原子单元中 15 个 P1、9 个 P2。S12 保持 P2，泛型、equality、malformed seed 和 normative migration 前置条件没有回退。

## 3. 阻断问题

### R1. requirements 的唯一 owner 声明与实施稿现有内容重叠

Phase 0 现在规定 requirements 唯一拥有“不可变行为、停止条件和准入条件”，但实施稿仍完整保存同一批事实：

- 第 2.1 节列出 public signature、State、commit、Result/Continuation、recovery equality/budget 等不可变行为；
- 第 5 节再次列出架构和事务边界；
- 第 10 节保存完整停止条件；
- 第 11 节保存准入约束。

如果按第 6、8 节直接新建 requirements 并复制这些内容，会立即形成第二事实源；如果 requirements 只链接本文，又不再是 owner 表所称的唯一 owner。现有 Node I/O/skip-output normative 文档还继续拥有具体行为 shape，因此“不可变行为 owner”的层级也需要区分。

Phase 0 必须先固定无重叠分工：

1. Node I/O、skip-output 和 architecture normative source 继续拥有具体当前行为与 shape；
2. 新 requirements 只拥有本轮重构的 requirement ID、行为保持义务、非目标和外部语义停止条件，并通过链接引用具体 normative truth；
3. 本实施方案只拥有 S01–S23 target shape、原子迁移边界、顺序、复杂度账本和实施门禁；
4. 第 2.1、5、10、11 节中由 requirements 拥有的事实应改为 requirement ID/链接，不再逐条复制；仅实现特有的“新增认知面不下降”“consumer 必须原子迁移”等停止条件留在本文；
5. requirements 不复制本文的 target dataclass、consumer 清单、阶段顺序或命令。

这样才能同时满足“normative behavior 不提前改写”“requirements 唯一”“实施 target 唯一”三项约束。

### R2. S04 与 S06 同时拥有 publication consumer 迁移

S04 要求所有 `.publications[node_id].descriptor` 访问在该原子单元归零，并称最终路径为 `graph.transition.publications[node_id]`；S06 又把同一组 production/test consumers 列为 `graph.publications` → `graph.transition.publications` 的迁移对象。

当前边界会产生二义性：

- 若 S04 只改 value shape，consumer 会先从 `graph.publications[id].descriptor.identity` 改成 `graph.publications[id].identity`，再在 S06 改成 `graph.transition.publications[id].identity`，同一行被无意义修改两次；
- 若 S04 已直接迁移到 `graph.transition.publications`，S06 的 publication consumer 清单就不再是待迁移清单。

按“变量、导包、逻辑和提交触碰次数最少”原则，建议固定为：

1. S04 在收窄 `publications` value type 时同时删除 `CompiledGraph.publications` property；
2. S04 的全部 publication consumers 一次改到 `graph.transition.publications[node_id]`，同时删除 `.descriptor` 层；
3. S06 只删除 `RecoveryAvailabilityPlan`、`transition` property 和剩余 `entries`/`materializations`/`graph_outputs`/`resource_order` projections；
4. S06 对 publication 只做 zero-use/exact-shape 验证，不再声称迁移同一 consumers；
5. 24 个原子单元和 P1/P2 计数不变。

另一种可接受方案是合并 S04/S06，但不能继续让两个独立单元共同拥有同一 consumer edit。

### R3. Phase 0 实际产物尚未形成，不能授权 production

当前工作区不存在：

```text
docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

`README.zh-CN.md` 和 `README.md` 也尚未包含本系列文档的 owner/navigation。实施稿已经如实记录这一状态，因此这不是 production 设计回退，但它仍是明确的阶段门槛。

导航还应避免要求 README 永久枚举“全部 review/response”：每新增一轮 review，导航和 owner 表就立即过期，并导致评审产物反过来要求重新评审导航。最小闭环是 README 只链接稳定的 requirements、implementation 和 normative source；历史 review 由实施稿的“关联记录”或一个稳定索引负责，不把“必须包含正在生成的本轮 review”作为 Phase 0 通过条件。

完成 R1 的 owner 迁移、创建 requirements、更新稳定导航并实际通过文档 manifest gate 后，才能申请 Phase 1 准入。

## 4. 必须收紧但不单独阻断 Phase 0

### N1. S09 不需要把现有 owner-internal projection 改成下划线名称

现有 `project_routing_facts()` 已位于 internal routing module、没有 public re-export，并被 recovery 作为跨模块 owner-internal projection 消费。目标改名为 `_project_routing_facts()` 不删除事实或路径，却增加 definition/import/test churn，而且该 rename 没有进入 S09 的删除/新增账本。

最小目标应保留现名 `project_routing_facts(state, facts) -> ResolutionCommand`，只改变返回类型并删除 `RoutingResolution`、`plan_routing()` 和镜像字段。若坚持下划线名称，必须把 rename 明列为变更并说明收益；不能把跨模块协议称为 module-private 后又由 recovery 导入。

### N2. 门禁和 architecture test owner 有两处措辞不精确

1. 第 11 节应写完整命令名 `pre-commit run --files`，不能写不存在的 `pre-commit --files`；
2. changed-file manifest 只应包含“该单元实际新增/修改的” requirements/navigation/review 文件，不能暗示每个 production 单元都重复加入未变文件；
3. topology exact-shape 与唯一访问路径断言应只由 `test_graph_execution_ownership.py` 拥有；`test_source_discipline.py` 继续作为必须运行的连续模块头/Any/reflection gate，不应再复制 topology owner 断言。

现有 no-index whitespace 状态判断、module-scope import 要求、generic/dependency/typing/full `make check` 门禁可以保留。

## 5. 本轮准入裁决

本轮不要求重新设计已经闭合的 S03、S05、S08、S10–S18、S20、S23A/S23B，也不改变 S12 的 P2 身份。下一次只需核验：

1. requirements 与实施稿不再双写行为、停止条件和准入事实；
2. S04/S06 只有一个 publication consumer 迁移 owner；
3. requirements 和稳定 README navigation 已实际创建；
4. S09 保留现名或明确记录 rename；
5. 门禁命令名、manifest 适用范围和 architecture/source-discipline test owner 已收紧；
6. Phase 0 的 exact changed-file manifest 和轻量文档门禁有可复现记录。

完成上述内容后，可以对 15 个 P1 做最终准入；9 个 P2 仍需逐项设计与评审。

## 6. 本次验证

本轮继续采用克制的静态审查：

| 检查 | 结果 |
| --- | --- |
| 实施方案全文 | 已复核 485 行 |
| 第三次复审六项条件 | 已逐项对照 |
| S03–S06 producer/consumer/test | 已与当前 production/tests 静态交叉搜索 |
| routing/recovery/generic shape | 已对照现有实现与 normative 引用 |
| requirements | 不存在 |
| README navigation | 尚未更新 |
| production/tests | 未修改 |
| pytest、Pyright、`make check` | 未重复运行；历史基线不作为本轮新证据 |

本轮只对新增复审 Markdown 执行 whitespace/diff 静态检查。完整测试、Pyright、build 和 pre-commit 留给实际 Phase 0/production 原子交付，并需按实施稿的 manifest 规则报告。

## 7. 最终结论

最新稿已经从“topology owner 未闭合”推进到“production target 基本闭合”，第三次复审的 S06、S08 和 changed-file gate 方向均已实质吸收。

当前剩余问题不会阻止继续撰写 Phase 0 文档，但会阻止把它们作为唯一事实源和可执行原子边界：requirements 与本文仍会双写，S04/S06 仍共同拥有 publication consumer edit，且 requirements/navigation 尚不存在。

**第四次复审裁决：允许继续完成 Phase 0 文档收口；不授权 Phase 1，不授权任何 production 修改。**
