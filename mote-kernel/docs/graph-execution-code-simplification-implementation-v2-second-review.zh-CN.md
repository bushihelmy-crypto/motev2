# Graph execution 代码简化候选 A-v2 实施方案第二次独立评审

> **结论：`PASS / FINDINGS = 0 / R1–R3 RESOLVED / READY FOR EXPLICIT APPROVAL / NO CURRENT PRODUCTION AUTHORIZATION`。A-v2 已形成版本化唯一 target owner；A-v1 的不可恢复历史输入由透明 tombstone 隔离，不再冒充可复现快照；`GSP-P01`–`GSP-P08`、`GSP-A06`、complete/recovered behavior 与 target exact-shape gate 均已闭合。删除 `_RootStateBinding` 和单调用者 `replace_root()` 的设计可以进入用户显式批准及 requirements owner 的 reviewed-SHA writeback 阶段，但本评审本身不授权修改 production/tests。**

## 1. 评审对象与冻结版本

- 日期：2026-08-26
- 范围：候选 A-v2 首次评审 R1–R3 整改与最终设计准入
- production source baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- production/test diff：空

本次独立复审冻结：

| Target | SHA256 | 职责 |
| --- | --- | --- |
| [A-v2 versioned 实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md) | `7f14810b66e3ff5a97585d6828d3cf715ad10db3951f87671fe8a6f6b93dc9c1` | exact target、复杂度账本、evidence 与 planned manifest 唯一 owner |
| [A-v2 首次评审回复](graph-execution-code-simplification-implementation-v2-review-response.zh-CN.md) | `627ad31e5f6b9781b279fe4a581dada4c4f343a090203e87bc0ecbdc65ba807c` | R1–R3 disposition 与整改 manifest |
| [A-v1 历史处置索引](graph-execution-code-simplification-implementation.zh-CN.md) | `2aa1197ff26035c0761cb455ed23a4c36ec8101399cf8c95c0dea918c2642a1a` | 退休 target tombstone；不拥有 A-v2 |
| [requirements owner](graph-semantics-preserving-simplification-requirements.zh-CN.md) | `a890677592757020789d3f571e4a08f080123822e4eed4798add8979beec2618` | A-v2 `GSP-A06 PENDING / NOT APPROVED` 与既有保持义务 |
| [候选调研](graph-execution-explosive-simplification-research.zh-CN.md) | `864c6ef68bf4681add48278c7a5d2eb2d746cc51299b71e1026b4a60b8ba363d` | 历史与候选状态目录 |
| [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md) | `b0f3af63ac55f404f9726b33659e67d109de41651eb3300df4807d6bd4beebf7` | A-v1/A-v2 导航及未批准状态 |
| [A-v2 首次独立评审](graph-execution-code-simplification-implementation-v2-review.zh-CN.md) | `a68536c860a38d6ac3e38b5f6b44392095ee98ec5e099204688446db92644cf8` | R1–R3 finding 基线；本轮未修改 |

本文只拥有本次独立复审裁决。requirements owner 仍唯一拥有批准状态；本 `PASS` 不等于 production authorization。

## 2. R1–R3 验收

### 2.1 R1 — RESOLVED：target generation 已隔离

A-v2 已迁至 versioned owner path；原路径只保留 A-v1 的 `RETIRED / KEEP / NON-NORMATIVE HISTORY` tombstone。research、requirements 与主方案均只把 versioned path 作为当前 target owner，不再让一个可变路径同时代表两个 generation。

首次评审要求恢复 `bcd84c…` 正文的部分不再作为准入条件。当前仓库确实没有该内容对象；根据 review 摘要重建并声称得到原 SHA 会制造伪证据。tombstone 已明确披露该取证限制，既不冒充旧 blob，也不向 A-v2 传递历史 `PASS/KEEP`。在旧 target 已退休且不再承担规范职责的前提下，这是比伪造快照更符合唯一真相与零负债的处理。

### 2.2 R2 — RESOLVED：requirements 与 `GSP-A06` 已接通

requirements owner 已将 A-v2 登记为新增 P2：`GSP-A06 PENDING / NOT APPROVED`。versioned implementation 第 2.5 节逐项映射 `GSP-P01`–`GSP-P08` 及相应 `GSP-Sxx`，第 2.6 节把 `GSP-A06` 的 signature、nominal type、删除/新增面、复杂度、behavior、exact-shape 与 manifest 指向唯一章节。

`GraphRunState`、command、reducer、revision、identity 与 protocol 明确为 `HARD KEEP`。review、requirements pending registration、用户批准与 production implementation 被分成不同阶段，没有继承既有 P1/P2 或 A-v1 的批准。

### 2.3 R3 — RESOLVED：两个 snapshot variant 与 exact shape 均有目标证据

修订正确区分：

- `test_repeated_nested_path_keeps_distinct_child_runs_and_latest_boundary[recovered]` 是 recovered lineage 成功建立、readmit 并继续执行的正向证据；
- `test_recovered_continuation_readmits_existing_frame_content` 实际是替换错误 frame 后 fail-closed 的负向 tamper 证据；
- complete snapshot、partial handoff、root mismatch、input immutability 与 validation precedence 继续由原 exact cases 覆盖。

planned production unit 还将在既有 architecture test 文件中落地单一窄 gate，精确约束 direct `root_state`、两个 snapshot variant、`replace_state()` root branch，并要求 `_RootStateBinding/root_binding/replace_root` 归零。该 test 已加入 planned manifest，当前文档没有预先声称它已经通过。

## 3. 技术设计再确认

| 评审项 | 裁决 | 依据 |
| --- | --- | --- |
| 唯一 owner | **PASS** | `GraphRunContext` 继续唯一持有 confirmed root/child state；只把 root payload 从单字段 wrapper 中取出 |
| 真实净删除 | **PASS** | 删除 1 type、1 dataclass、1 field、1 root-only method、2 constructor sites 和测试重复分派 |
| 复用基础设施 | **PASS** | 继续使用现有 `state_at()`、`replace_state()`、`replace_child()`、snapshot variants、validator 与 commit/install path |
| 类型与行为 | **PASS** | `GraphRunState` 为 frozen slots dataclass；direct field 不改变 structural equality、family seal、错误文本或首错阶段 |
| 复杂度 | **PASS** | target `503/287/177/499` 可由删除 `_RootStateBinding` 精确推出；其他六项不得增长，health 保持 `51/0/0` |
| 原子范围 | **PASS** | 四个 production consumer、一个 Graph API fixture、一个 architecture gate、一个 normative source 与 complexity config 均已列入 |
| 非目标 | **PASS** | 不修改 public Graph、State/Store/protocol、持久化、failover、child/planned/proof/frame owner |

`replace_state()` 是唯一 **root replacement** entry；`replace_child()` 仍保留 acknowledged child start 与 child tuple mechanics。实施不得把“唯一入口”扩大解释为删除 child-specific operation。

## 4. 批准与实施边界

本评审完成的是技术设计准入，不直接改变 requirements 状态。下一步顺序必须是：

1. 用户明确授权本次受审 A-v2 SHA `7f14810b…` 及其八文件 exact target；
2. requirements owner 以独立 writeback 记录该授权、绑定 reviewed SHA，并将 A-v2 从 `PENDING` 改为 `GSP-A06 SATISFIED / APPROVED`；
3. 随后才能按 versioned implementation 第 7.2 节的 manifest 原子修改 production/tests/normative source/config；
4. 任一 target、SHA、manifest 或停止条件变化都必须重新评审，不能继承本次 `PASS`。

批准 writeback 与 production implementation 必须是不同 change unit。本评审不授权提前修改代码，也不授权任何持久化或 failover 工作。

## 5. 验证记录

| 验证 | 结果 |
| --- | --- |
| versioned implementation 第 6.1 节 13 个 existing nodeid + 两个 architecture files | `40 passed in 0.81s` |
| 六份整改文档的相对 Markdown links | `104 checked / 0 missing` |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml`、`Makefile` 无 diff |
| source inventory | `_RootStateBinding/root_binding/replace_root = 6/18/2`；2 constructor sites；4 production consumer files |
| target complexity | `504/288/178/500 → 503/287/177/499`，其余六项不增长 |
| `make check` | Ruff/format 通过；Pyright `0 errors`；complexity gate `9 passed`；health `51/0/0`；全量 `843 passed`、100% coverage；build/twine 通过 |
| monorepo root 对本 review unit 运行 scoped `pre-commit` | 全部适用 hooks 通过 |
| `git diff --check` | 通过 |

## 6. Findings 与最终裁决

```text
blocker = 0
major = 0
minor = 0

R1 target generation / historical tombstone = RESOLVED
R2 requirements applicability / GSP-A06 pending registration = RESOLVED
R3 recovered behavior / exact-shape target = RESOLVED

A-v2 design = PASS
requirements approval = READY / NOT YET GRANTED
production/tests authorization = NOT YET GRANTED
```

**最终裁决：候选 A-v2 技术方案通过独立复审，可以进入 requirements owner 与用户显式批准阶段；在该批准完成前仍不得开始 production/tests 编码。**
