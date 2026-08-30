# Graph execution 代码简化候选 A 实施方案第四次评审回复

## 1. 回复信息

- 日期：2026-08-26
- 回复对象：[候选 A 实施方案第四次评审](graph-execution-code-simplification-implementation-fourth-review.zh-CN.md)
- disposition owner：[候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md)
- 状态：`R9–R11 RESOLVED / R12 ACCEPTED AND ADDRESSED / CHANGES REQUESTED UNTIL FINAL REVIEW / KEEP`

受审版本绑定由第四次评审记录唯一拥有；本回复不复制 SHA。本文只记录 R12 disposition、per-unit manifests 和终止规则，
不拥有 A0 evidence、requirements、批准状态或未来 target。

## 2. 回复裁决

第四次评审全部接受，无异议：

| 裁决 | 回复 |
| --- | --- |
| R9–R11 `RESOLVED` | **接受**；不再改写已验收的索引、state-only trace 或 provenance 内容 |
| R12 `OPEN — ATOMIC CHANGE-UNIT LEDGER` | **接受并已整改**；补齐独立三审/四审 unit，并拆分三审回复 manifests |
| `KEEP / NO IMPLEMENTATION` | **接受**；R12 不改变候选 A 的技术 disposition |
| `NO PRODUCTION AUTHORIZATION` | **接受**；production/test manifest 继续为空 |

## 3. R12 累计 unit ledger

第四次评审指出的受审快照从六个 unit 补入 independent third review 后应为七个。进入本回复阶段后又客观形成：

1. independent fourth review；
2. fourth-review response / owner writeback；
3. post-fourth-review navigation sync。

因此 implementation 当前累计账本如实登记为十个 unit，而不是把计数停留在整改前的七个。每轮 independent review 都是单文件
unit；response/writeback 与 navigation sync 继续分离，不因文件集合相同或相邻发生而合并。

## 4. 三审回复 manifest 拆分

第三次评审回复第 6 节已经从一个未分组的四文件集合改为两个 exact manifest：

```text
# owner/response writeback
docs/graph-execution-code-simplification-implementation.zh-CN.md
docs/graph-execution-code-simplification-implementation-second-review-response.zh-CN.md
docs/graph-execution-code-simplification-implementation-third-review-response.zh-CN.md

# navigation sync
docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

其验证表只描述“两个 unit 合计四个 changed files”，不再把总集合称作 per-unit manifest。第三次评审本身由累计账本另列为独立
单文件 unit。

## 5. 本轮 actual manifests

第四次评审在本轮 response/writeback 前已独立形成，其单文件 review unit 已登记到 implementation 累计账本，不进入以下整改
manifest。

### 5.1 Fourth-review response / owner writeback unit

```text
docs/graph-execution-code-simplification-implementation.zh-CN.md
docs/graph-execution-code-simplification-implementation-third-review-response.zh-CN.md
docs/graph-execution-code-simplification-implementation-fourth-review-response.zh-CN.md
```

### 5.2 Post-fourth-review navigation sync unit

```text
docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
```

两个 unit 合计四个 changed files。requirements、production 与 tests 均不在 manifest 中。

## 6. 终止规则

R12 整改后只允许再做一次独立复审：

1. 若复审仍有 finding，按 finding 精确整改；
2. 若复审为 `PASS / findings=0`，最终裁决只由该 review record 持有；
3. 不再为了复述 PASS 修改 implementation owner；
4. 最终 review path 可作为单独 navigation-only unit 加入主实施方案，不改变已通过的 implementation 内容。

该规则终止“PASS 后回写导致 SHA 变化，再因 SHA 变化复审”的循环。

## 7. 验证记录

| 验证 | 结果 |
| --- | --- |
| implementation 第 6.1 节 15 个 exact behavior nodeid + 两个 architecture files | `42 passed in 0.89s` |
| `make check` | Ruff/format、Pyright（0 errors）、complexity gate（9 passed）、health（`51 reviewed / 0 unreviewed / 0 stale`）、全量 tests（843 passed、100% coverage）、build/twine 全部通过 |
| 本轮两个 unit 合计四个 changed files 的相对 Markdown 链接 | 全部存在 |
| monorepo root scoped `pre-commit` 与 `git diff --check` | 通过 |

## 8. 最终回复裁决

```text
R1–R8 = REMAIN RESOLVED
R9–R11 = RESOLVED
R12 = ADDRESSED
EX-A0 technical disposition = COMPLETE / KEEP
current document closure = CHANGES REQUESTED / FINAL REVIEW PENDING
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE / NO AUTHORIZATION
```

**最终结论：R12 已完成 docs-only 整改；候选 A 保持 `KEEP / NO IMPLEMENTATION`，当前仍等待最后一次独立复审。**
