# Graph execution 代码简化候选 A-v2 独立评审回复

## 1. 回复信息

- 日期：2026-08-26
- 回复对象：[A-v2 实施方案独立评审](graph-execution-code-simplification-implementation-v2-review.zh-CN.md)
- 修订后 target owner：[A-v2 versioned 实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)
- requirements owner：[语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 状态：`R1 PARTIALLY ACCEPTED / R2 ACCEPTED AND ADDRESSED / R3 ACCEPTED WITH EVIDENCE CORRECTION / RE-REVIEW REQUIRED`
- production/test diff：空

接受评审对 A-v2 技术方向的判断：删除 `_RootStateBinding` 与单调用者 `replace_root()` 是真实净删除，现有
`GraphRunContext`、`state_at()`、`replace_state()` 与 continuation infrastructure 足以完成，不需要新抽象、兼容层或第二 owner。

## 2. Findings disposition

| Finding | 回复裁决 | 处理 |
| --- | --- | --- |
| R1：A-v1 哈希无仓库归档对象 | **PARTIALLY ACCEPTED** | 接受 target generation 必须分路径；不接受伪造或复制不可恢复的旧 SHA 快照 |
| R2：requirements / `GSP-A06` 未闭合 | **ACCEPTED AND ADDRESSED** | requirements 登记 A-v2 pending；实施方案补齐 `GSP-P01`–`GSP-P08` 与 `GSP-A06` 映射 |
| R3：recovered 与 exact-shape evidence 不完整 | **ACCEPTED WITH CORRECTION** | 使用真实 existing recovered 成功 case；补充 recovered tamper case 与 planned 窄 shape gate |

上述整改只形成新的可复审设计，不把 `CHANGES REQUESTED` 自行改写为 PASS，也不构成实施批准。

## 3. R1：接受版本化 owner 分离，不接受恢复不存在的旧快照

### 3.1 接受项

评审指出“一个可变路径同时代表 A-v1 与 A-v2”会让历史链接指向错误 generation，这一点成立。已采取以下整改：

1. A-v2 exact target 迁到独立 versioned owner：
   `graph-execution-code-simplification-implementation-v2.zh-CN.md`；
2. 原路径只保留 A-v1 的退休处置索引，为旧 review/response 链接提供稳定的 A-v1 落点；
3. research、requirements 与主实施导航只把 versioned path 作为当前 A-v2 target owner；
4. A-v1 的 `KEEP` 不传递给 A-v2，A-v2 的设计也不反向改写历史评审裁决。

这关闭了“同一路径代表两代 active target”的 owner 歧义，且没有复制两份 A-v2 正文。

### 3.2 不接受项

不接受“恢复一个必须计算出 `bcd84c…` 的 A-v1 implementation 快照”作为当前准入条件，理由如下：

- 当前 Git 与工作树没有该内容对象，SHA256 只能证明当时输入的身份，不能恢复原文；
- 根据 review 文本重新拼装一份内容并追求相同哈希既不可行，也会伪造审计证据；
- A-v1 已因没有净删除面而永久退休，不是 normative source、当前 target 或可实施资产；
- 为已拒绝方案长期复制完整正文会新增历史文档事实源和维护负债，与唯一真相、零负债原则冲突。

因此保留历史 review 中的冻结哈希作为审计指纹，同时由原路径的 A-v1 处置索引明确披露“仓库内无对应 blob”；不声称已经
恢复或可复现旧正文。该分歧由本回复持有，后续评审不得把 tombstone 冒充 `bcd84c…` 快照。

## 4. R2：完整接受并回写 requirements owner

已完成：

- requirements owner 将 A-v2 登记为新增 P2：`GSP-A06 PENDING / NOT APPROVED`；
- versioned 实施方案逐项映射 `GSP-P01`–`GSP-P08` 及对应 stop conditions；
- `GraphRunState`、command、reducer、revision、identity 与 protocol 明确为 `HARD KEEP`；
- `GSP-A06` 的 signature、nominal type、删除/新增账本、复杂度、behavior、exact-shape 与 manifest 分别指向实施方案唯一章节；
- 明确独立复审、requirements reviewed-SHA 绑定、用户显式批准、production unit 四个阶段不得合并。

requirements 只拥有 pending/approval disposition，不复制 `_RootStateBinding` 删除算法或 target shape。

## 5. R3：接受证据缺口，纠正具体 nodeid 解释

### 5.1 Existing recovered 成功证据

评审建议的
`test_recovered_continuation_readmits_existing_frame_content` 实际会替换错误 graph-input frame，并断言
`SnapshotMismatchError`；它是 recovered frame-content 的**负向 tamper case**，不是成功 readmission。不能为了闭合表格而把其
测试语义写反。

实施方案改用现有真实成功 case：

```text
tests/execution/test_graph_recovery_contract.py::
test_repeated_nested_path_keeps_distinct_child_runs_and_latest_boundary[recovered]
```

该 case 从 state-only seed 建立 recovered lineage，随后使用返回的 state/continuation 成功 readmit，继续执行 nested repeated
generation，并验证 root/child snapshots 与 latest boundary。原 `test_recovered_continuation_readmits_existing_frame_content` 同时作为
负向 tamper evidence 保留，明确其失败断言。

### 5.2 Target exact-shape gate

接受 shape 删除必须与 production 原子落地。planned implementation 将在既有
`tests/architecture/test_graph_execution_ownership.py` 中新增单一窄测试，复用已有 AST helpers，精确验证：

- `GraphRunContext.__slots__` 的唯一 root storage 为 `root_state`；
- root replacement 只由 `replace_state()` 直接完成；
- complete/recovered snapshot 都持有 `root_state: GraphRunState`；
- `_RootStateBinding`、`root_binding`、`replace_root` 全部归零。

不新增通用 private-source framework、production hook 或 compatibility path。该 test 文件已加入 planned implementation
manifest；当前 docs-only 整改不提前修改 tests，也不冒充 target gate 已通过。

## 6. Actual change-unit manifests

本 response 是独立单文件 audit unit：

```text
docs/graph-execution-code-simplification-implementation-v2-review-response.zh-CN.md
```

owner/requirements 整改与主方案导航同步的 exact manifests 分别由 versioned 实施方案第 7.1 节登记，不与本 response 合并。

## 7. 验证记录

| 验证 | 结果 |
| --- | --- |
| 13 个 existing exact nodeid + 两个 architecture files | `40 passed in 0.81s` |
| 六份 writeback 文档的 relative Markdown links | `104 checked / 0 missing` |
| `make check` | Ruff/format、Pyright `0 errors`、complexity gate `9 passed`、health `51/0/0`、全量 `843 passed`、100% coverage、build/twine 全部通过 |
| monorepo root scoped pre-commit | 全部适用 hooks 通过 |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml`、`Makefile` 无 diff |

## 8. 最终回复裁决

```text
A-v2 technical direction = ACCEPTED IN PRINCIPLE
R1 path-generation ambiguity = ADDRESSED
R1 exact bcd84c snapshot restoration = NOT ACCEPTED
R2 requirements / GSP-A06 applicability = ADDRESSED
R3 recovered behavior evidence = ADDRESSED WITH CORRECTED NODEID
R3 target exact-shape gate = PLANNED IN PRODUCTION ATOMIC UNIT

independent re-review = REQUIRED
requirements approval = NOT GRANTED
production/tests authorization = NOT GRANTED
```

**最终回复：接受所有会提高 A-v2 可实施性且不制造新负债的整改；不接受伪造或复制不可恢复的 A-v1 快照。当前只完成
docs-only 设计整改，必须以 versioned A-v2 新 SHA 独立复审后，再进入 requirements owner 与用户显式批准阶段。**
