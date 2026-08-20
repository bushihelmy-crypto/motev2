# Graph 执行代码语义保持型简化实施方案第五次复审

> **结论：尚未完全闭合，不批准进入 Phase 1。** production target、文档 owner、S04/S06 原子边界和 Phase 0 文档门禁已经闭合；当前只剩 `GSP-A03` characterization 证据与 Phase 0/P2 时序两项文档内生矛盾。

## 1. 复审信息

- 复审日期：2026-08-19
- 代码基线：`feat/kernel-graph-node-io-contract@7944159`
- 复审对象：[最新实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)
- 需求依据：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 前序结论：[第四次复审](graph-semantics-preserving-simplification-implementation-fourth-review.zh-CN.md)
- 范围：只审查 Phase 0 文档、现有 production shape、测试证据映射和门禁；不修改 production code 或 tests
- 原则：零增复杂度、唯一事实源、复用既有 owner、完整门禁、泛型关系不擦除、导包位于连续模块头、变量不保存可推导事实、逻辑保持最短闭环

## 2. 已闭合事项

第四次复审的大部分条件已经实质完成：

1. requirements、implementation 和 normative source 的 owner 分工已经分层：requirements 只保存 `GSP-*` 义务，实施方案只保存 target/ledger/order/gate，具体当前行为继续由 architecture、Node I/O 和 skip-output 文档拥有；
2. 实施方案第 2、5、9、10、11 节已改为 requirement ID/链接和 implementation-specific 约束，不再复制完整外部行为、非目标、停止条件或准入清单；
3. S04 现在唯一拥有 `CompiledGraph.publications`、publication value shape 和 consumer 迁移；S06 只处理剩余 projections 并验证 publication zero-use，同一 consumer 不再跨两个单元重复编辑；
4. S09 保留现有跨模块 owner-internal 名称 `project_routing_facts()`，只删除 `RoutingResolution`、`plan_routing()` 和镜像字段，没有无收益 rename；
5. topology exact-shape/访问路径只由 `test_graph_execution_ownership.py` 拥有；`test_source_discipline.py` 回到 import/`Any`/reflection 纪律；
6. `docs/graph-semantics-preserving-simplification-requirements.zh-CN.md` 已存在，`README.zh-CN.md`/`README.md` 已增加稳定 owner 导航且未枚举动态 review 列表；
7. changed-file gate 使用完整命令 `pre-commit run --files`，只纳入该单元实际改动，tracked/untracked whitespace 判定和 index 边界清楚；
8. 24 个原子单元、15 个 P1、9 个 P2 的计数闭合；S12 继续是 P2，equality、malformed seed、泛型迁移和 normative 同步前置条件未被放宽。

实施方案第 7.4 节所述五文件 Phase 0 文档门禁，本轮已按 exact paths 从 monorepo root 复现：pre-commit 全部通过，两个 tracked README 的 staged/unstaged whitespace 检查无诊断，三个 untracked Markdown 的 no-index 检查均为 exit 1 且无输出；Git index 未修改。

## 3. 阻断问题

### R1. `GSP-A03` 的 characterization 证据仍未闭合

requirements 的 `GSP-A03` 要求每个拟实施单元：

1. 映射全部适用的 `GSP-P01`–`GSP-P08`；
2. 已有确定性的成功路径和失败或边界路径 characterization；
3. shape 删除具有 exact-shape/tamper 证据。

实施方案第 7.2.1 节目前只列“测试文件 owner”，并明确把 exact test path 与具体成功/失败 case 推迟到“每个单元实施前”。一个包含大量 case 的文件名不能证明所要求的成功/边界 pair 已存在，也不能形成可复现证据。这与 `GSP-A03` 是 Phase 0 准入条件的定义冲突。

requirement ID 映射也至少遗漏以下直接关系：

- S06 迁移 `resource_order`，必须映射显式拥有 resource first-seen/FIFO/canonical ordering 的 `GSP-P07`；
- S09 改造 `ResolutionCommand` projection，必须映射显式拥有 State command/revision 的 `GSP-P02`；
- S23B 合并 failure/interrupt Result view projection，必须映射显式拥有 Result shape/identity 的 `GSP-P04`。

应在 Phase 0 表中为每个 P1 写出：

```text
单元 -> 全部适用 GSP-Pxx -> exact test path::test_case（成功）
                           -> exact test path::test_case（失败/边界）
                           -> exact-shape/tamper case（适用时）
```

可以引用现有 case，不要求为了填表新增重复测试；若现有 case 不足，只记录待补 characterization，并保持该单元未批准。不能一边把 exact case 延后，一边声明 Phase 0 只剩 `GSP-A05`。

### R2. Phase 0 仍要求完成九个 P2 的详细设计，但正文没有完成

实施方案 Phase 0 第 4 项要求对所有 P2 给出：

- 目标函数签名；
- 输入/输出 nominal type；
- 删除/新增计数；
- 复杂度变化；
- S12 的额外证明计划。

当前 S01、S02、S07、S12、S15、S16、S19、S21、S22 仍只有候选方向、上限和停止条件，没有上述逐项完整设计。与此同时，requirements 的 `GSP-A02` 和实施方案 Phase 3/4 又正确规定 P2 保持未批准并逐项再评审。

按最简闭环，不应为批准 P1 提前设计九个 P2。应把 Phase 0 第 4 项移动为 Phase 3/4 的单项准入条件，并明确：

- Phase 0 只确认 P2 列表、owner、未批准状态和不得继承 P1 批准；
- 每个 P2 申请实施时再提交签名、nominal type、增删计数、复杂度证据和 characterization；
- S12 继续额外满足现有 equality/malformed/generic 条件。

若保留现有 Phase 0 第 4 项，则必须现在补齐九项设计；不能同时声称“Phase 0 已形成、只剩 A05”。

## 4. 准入条件

下一版无需重新打开已经闭合的 S03–S20 production target，只需：

1. 补齐 15 个 P1 的全部 requirement ID 与 exact success/boundary/shape case 映射，或将证据不足的单元降为未批准；
2. 把 P2 详细设计要求移到 Phase 3/4 单项准入，或者现在逐项补齐；推荐前者；
3. 更新 Phase 0 当前状态，只有 `GSP-A01`–`GSP-A04` 真正满足后才能请求 `GSP-A05`；
4. 将本轮修订和下一轮 review 纳入实际 changed-file manifest，复跑轻量文档门禁。

完成后可以对闭合证据的 P1 做最终准入；P2 继续逐项评审。

## 5. 本次验证

本轮遵循“测试悠着点”的要求：

| 检查 | 结果 |
| --- | --- |
| 实施方案 | 已复核 508 行 |
| requirements 与 README owner/navigation | 已静态核对 |
| S04/S06、S09 target 与当前 consumer | 已静态交叉核对 |
| P1 requirement/characterization 表 | 15 项均存在文件级映射；未提供 exact case 映射，且发现至少三项 requirement ID 漏映射 |
| Phase 0 五文件 pre-commit | 本轮复现通过 |
| tracked/untracked whitespace | 本轮复现无诊断 |
| production/tests | 未修改 |
| pytest、Pyright、`make check` | 未重复运行；历史基线不作为本轮新证据 |

本复审文件生成后，已把第 7.4 节五个 paths 与本文件组成六文件 expanded Phase 0 manifest：`pre-commit run --files ...` 全部通过；两个 tracked README 的 staged/unstaged whitespace 检查无诊断；implementation、requirements、第四次复审和本文件四个 untracked Markdown 的 no-index 检查均为 exit 1 且无输出。验证未修改 Git index，也不把未运行的 production gate 冒充新证据。

## 6. 最终结论

最新稿的类型/owner/导包/forwarding/变量最小化目标已经基本闭合，第四次复审的具体整改也已正确吸收。剩余问题不是 production 设计缺陷，而是方案自己定义的 Phase 0 证据与时序尚未满足。

**第五次复审裁决：未完全闭合，不批准 `GSP-A05`，不得进入 Phase 1 或修改 production/tests。** 修正 R1、R2 后做一次聚焦准入复核即可。
