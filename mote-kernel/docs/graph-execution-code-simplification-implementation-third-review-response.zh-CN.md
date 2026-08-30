# Graph execution 代码简化候选 A 实施方案第三次评审回复

## 1. 回复信息

- 日期：2026-08-26
- 回复对象：[候选 A 实施方案第三次评审](graph-execution-code-simplification-implementation-third-review.zh-CN.md)
- 后续账本复核：[候选 A 实施方案第四次评审](graph-execution-code-simplification-implementation-fourth-review.zh-CN.md)
- disposition owner：[候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md)
- 状态：`R9–R11 ACCEPTED AND ADDRESSED / R12 MANIFEST CORRECTION APPLIED / KEEP UPHELD / RE-REVIEW PENDING`

受审版本绑定由第三次评审记录唯一拥有；本回复不复制 SHA。本文只记录 finding disposition、整改结果和本轮 actual manifest，
不拥有 A0 evidence、requirements、批准状态或未来 target。

## 2. Disposition 总表

| Finding | 回复裁决 | 整改 |
| --- | --- | --- |
| R9 review/response 索引缺口 | **接受** | 主实施方案关联记录新增二审回复、三审和三审回复；navigation sync 独立记账 |
| R10 state-only trace 漏 early rejection | **接受** | 在 `_new_context()` 前补入 resume scope/substitution 分类与无 context、无 commit 的拒绝分支 |
| R11 二审输入 provenance 不可复核 | **接受** | 改为“writeback 前已存在且未修改”，删除人物归因 |

第三次评审没有推翻候选 A 的技术结论。R9–R11 都是文档准确性问题，应全部整改；没有需要保留异议的 finding。

## 3. R9：唯一动态索引

主实施方案第 1 节现在按 exact path 登记：

- 第二次评审回复；
- 第三次独立评审；
- 本第三次评审回复。

README 继续只链接稳定主实施方案，不枚举动态 review 历史。implementation owner 将本轮拆为 owner/response writeback 与
navigation sync 两个 docs-only change unit，没有把新索引追写进历史二审 unit，也没有修改 requirements 批准状态。

## 4. R10：state-only actual lifecycle

implementation 的 state-only trace 已与 `Graph.run()` 实际顺序一致：

```text
classify resumed scopes and output-carrying substitutions
  → multiple scopes + substitution: fail before context creation and commit
  → otherwise: create root-only recovered context
  → validate / plan / preflight / confirm / drive
```

这使 actual lifecycle 与既有
`test_state_only_multi_scope_substitution_is_rejected_before_first_commit` evidence 一致，不新增 behavior contract 或 tests。

## 5. R11 与 SHA 归属

第二次评审回复只保留可复核事实：第二次评审在该 disposition writeback 前已经存在，且该 change unit 没有修改它。因此 review
不进入当时的 writeback manifest。

同时按唯一真相原则清理了重复 SHA：

- review record 唯一拥有它所评审版本的指纹；
- implementation owner 只记录当前 disposition 和 review/response 链；
- response 只记录接受/异议、整改和 manifest。

这项清理不改变任何历史裁决，只消除同一版本绑定在多文档中的维护负担。

## 6. Actual manifests

### 6.1 Owner/response writeback unit

```text
docs/graph-execution-code-simplification-implementation.zh-CN.md
docs/graph-execution-code-simplification-implementation-second-review-response.zh-CN.md
docs/graph-execution-code-simplification-implementation-third-review-response.zh-CN.md
```

### 6.2 Navigation sync unit

```text
docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

第三次评审在上述两个整改 unit 前已独立形成，由 implementation 的累计账本登记为自己的单文件 review unit，不混入这两个
manifest。production/test manifest 为空。

## 7. 验证记录

| 验证 | 结果 |
| --- | --- |
| implementation 第 6.1 节 15 个 exact behavior nodeid + 两个 architecture files | `42 passed in 0.87s` |
| `make check` | Ruff/format、Pyright（0 errors）、complexity gate（9 passed）、health（`51 reviewed / 0 unreviewed / 0 stale`）、全量 tests（843 passed、100% coverage）、build/twine 全部通过 |
| 上述两个 unit 合计四个 changed files 的相对 Markdown 链接 | 全部存在 |
| monorepo root scoped `pre-commit` 与 `git diff --check` | 通过 |

## 8. 最终回复裁决

```text
R1–R8 = REMAIN RESOLVED
R9 = ADDRESSED
R10 = ADDRESSED
R11 = ADDRESSED
EX-A0 technical disposition = COMPLETE / KEEP
document closure = RE-REVIEW PENDING
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE
```

**最终结论：完整接受并整改第三次评审，候选 A 继续保持 `KEEP / NO IMPLEMENTATION`；本回复不冒充后续复审 PASS。**
