# Graph execution 代码简化候选 A-v2 实施方案独立评审

> **结论：`CHANGES REQUESTED / TECHNICAL TARGET ACCEPTED IN PRINCIPLE / R1–R3 OPEN / NO PRODUCTION AUTHORIZATION`。删除 `_RootStateBinding` 与单调用者 `replace_root()` 的方向具有真实净删除面，且可以继续由现有 `GraphRunContext`、`state_at()`、`replace_state()` 与 continuation infrastructure 完成，不需要新抽象或兼容层。但 A-v1 冻结对象尚未形成可复现归档，A-v2 尚未闭合 requirements owner/`GSP-A06` 映射，两个 continuation snapshot variant 的 recovered 与 exact-shape evidence 也不完整。三项整改完成并复审前，不得修改 production/tests。**

## 1. 评审对象与冻结版本

- 日期：2026-08-26
- 范围：只评审候选 A-v2 root-state de-wrapper；A-v1 保持退休，候选 B 不进入本轮
- production source baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- production/test diff：空

本次评审冻结：

| Target | SHA256 | 职责 |
| --- | --- | --- |
| [A-v2 实施方案](graph-execution-code-simplification-implementation.zh-CN.md) | `3fa7a8d32f9ccc9e10ed06902d3662c2b98a8192e82d64919a8f8fcaa1963c0b` | 当前 exact target、复杂度账本、evidence 与 planned manifest owner |
| [候选调研](graph-execution-explosive-simplification-research.zh-CN.md) | `e1220bc39f6733d3e14dd16563821dd07f64df6c9a1e47eceb053a3b6cc2a7e2` | A-v1/A-v2/B 状态目录；不拥有 target |
| [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md) | `a34be0145a40b0bee9a21de8b24f69831e80e6493888f99f781f165030872544` | 账本外候选导航与未批准状态 |
| [requirements owner](graph-semantics-preserving-simplification-requirements.zh-CN.md) | `4bc0f50dc461501e8e312a8ab3593c4936a424e8961aed77f6a6f6d8bb31fd5d` | `GSP-Pxx`、`GSP-A06` 与批准状态；本轮未修改 |
| [A-v1 第五次评审](graph-execution-code-simplification-implementation-fifth-review.zh-CN.md) | `aa63996646a39e7656efbc26584df7c324d957f6e5393c742c192d4402613672` | A-v1 最终历史裁决；不向 A-v2 传递批准 |

本文只拥有对上述冻结版本的独立裁决，不拥有 A-v2 target 或批准状态。

## 2. 技术 target 复核

### 2.1 真实删除面成立

当前 source 与实施方案 inventory 一致：

| 项目 | Source review | 裁决 |
| --- | --- | --- |
| `_RootStateBinding` | 单字段 frozen dataclass；只有 `state: GraphRunState`，无 validation、identity、ordering 或 lifecycle invariant | **可删除** |
| wrapper constructor | `_new_context()` 与 `replace_root()` 共两处 | **可归零** |
| `replace_root()` production caller | 仅 `GraphRunContext.replace_state()` | **单调用转交，可内联删除** |
| root storage | context 一个 field；snapshot variant 各一个同值 field | **字段改名，不增加 storage** |
| production consumer | `run_context.py`、`invocation.py`、`family_driver.py`、`facade.py` | **planned manifest 完整** |
| test fixture edit | 旧符号只出现在 `tests/execution/test_graph_api.py` | **planned manifest 完整** |
| normative wording | Node I/O implementation 中四处 `_RootStateBinding` | **同步范围准确** |

production token 计数也可复算：`_RootStateBinding = 6`、`root_binding = 18`、`replace_root = 2`。`GraphRunState` 本身是 frozen、slots dataclass；snapshot admission 由 `snapshot.root_binding.state != state` 改为 `snapshot.root_state != state` 后，structural equality、错误类型/文本与首错位置不变。

### 2.2 复杂度与 owner 方向成立

删除一个 private dataclass 及其唯一 field，使 `top_level_definitions/type_definitions/dataclass_types/dataclass_fields` 从 `504/288/178/500` 精确下降为 `503/287/177/499`。将 root branch 直接写入既有 `replace_state()` 不新增 decision；其余指标无增长理由。

`replace_state()` 只能成为唯一 **root replacement** entry；`replace_child()` 仍负责 acknowledged child start 与 child tuple replacement，不得因“唯一入口”措辞被删除或改义。当前方案已保持这一区分。

因此 A-v2 不是 A-v1 index 的变体，也不是把 wrapper 改名；它是一个可以原子完成的 owner-internal de-wrapper。技术方向本身通过本轮审查。

## 3. R1 — MAJOR：A-v1 只有哈希声明，没有可复现归档对象

A-v1 第五次评审冻结 implementation SHA256 `bcd84c237dd6e46af27d6085804fd7abda80a672dedf16439b08bc47c9a8e621`，但原路径现已被 A-v2 内容覆盖为 `3fa7a8d…`。对当前仓库全部 Markdown 文件计算 SHA256，没有任何文件匹配 `bcd84c…`；该路径又是未跟踪文件，当前 Git `HEAD` 无法提供旧 blob。

因此“A-v1 五轮评审已归档”目前不可复现：历史 review 链接打开的是 A-v2，只有哈希而没有可验证内容。哈希不能替代归档对象。

**整改要求：**

1. 恢复可计算出 `bcd84c…` 的 A-v1 implementation 快照；
2. 最优边界是让历史原路径继续指向 A-v1，并把 A-v2 移到明确版本化的新 owner path；若采用其他方案，必须保证所有 A-v1 review 的 target 可从仓库内稳定定位且不改写历史裁决；
3. research、主方案和 A-v2 后续 review 只链接新的 A-v2 owner，禁止一个可变路径同时代表两个 target generation。

## 4. R2 — MAJOR：requirements owner 与适用义务尚未闭合

当前 requirements 第 6 节要求实施方案把每个原子单元映射到适用 `GSP-P01`–`GSP-P08`，账本外/P2 target 还必须逐项满足 `GSP-A06`。其当前裁决仍写“后续新增 P2：`NONE`”，没有 A-v2 pending entry；A-v2 实施方案也没有任何 `GSP-Pxx` applicability matrix。

这不否定技术 target，但意味着独立技术评审不能代替 requirements owner 的准入与批准。

**整改要求：**

1. 在 requirements owner 以独立 docs-only unit 登记 A-v2 为 `PENDING / NOT APPROVED`，只链接 versioned target，不复制 shape；
2. implementation 明确映射至少涉及的 public/type、transaction/partial handoff、Result/Continuation、settlement/result、recovered snapshot、nested ordering 与 architecture 边界，并对未修改的 durable State 写明 `HARD KEEP`；
3. 逐项引用对应 `GSP-Pxx/GSP-Sxx`，不要在 implementation 中创建第二套要求；
4. 整改复审通过后，再由 requirements owner 绑定 reviewed SHA 并记录用户的显式批准；批准 unit 与 production unit 分离。

## 5. R3 — MAJOR：recovered variant 与 exact-shape evidence 未完整闭合

target 同时把 `_CompleteContinuationSnapshot.root_binding` 和 `_RecoveredContinuationSnapshot.root_binding` 改为 direct `root_state`，但第 6.1 节的 11 个 nodeid 没有成功 readmit recovered continuation 的 case。当前 `38 passed` 证明 baseline 绿色，不能证明两种 snapshot variant 都被目标 evidence 覆盖。

同时，第 6.3 节把旧 symbol 归零只定义为一次性 source-level acceptance。`GSP-A03/GSP-A06` 对 shape 删除要求 target exact-shape/tamper evidence 与 production 原子落地；现有 architecture files 并未断言 direct root-state owner 或旧 wrapper/entry 不存在。

**整改要求：**

1. 把 `tests/execution/test_continuation_integrity.py::test_recovered_continuation_readmits_existing_frame_content` 或等价成功 case 加入 matrix，明确它证明 recovered variant 的 root state、frame/child snapshot 与 readmission 不变；
2. 为 direct `root_state`、两个 snapshot variant 和旧 `_RootStateBinding/root_binding/replace_root` 归零指定一个现有或新增 exact-shape test，列明断言目标与失败条件；
3. 若修改 architecture 或 continuation-integrity test file，将其加入 planned changed-file manifest；不得把 aggregate complexity 数字或一次性 `rg` 冒充 exact target gate；
4. 更新 exact nodeid 数量与基线运行结果，但不为测试建立通用反射框架、compatibility path 或 production hook。

## 6. 已通过边界

以下内容无需重做：

- A-v2 不新增 type、field、helper、property、cache、alias、wrapper 或第二 owner；
- `GraphRunContext` 仍是唯一 confirmed root/child state owner；
- `ChildStateBinding`、planned state、proof binding、frame index 和 snapshot variants 均保留；
- public `Graph`、Result/Continuation surface、commit/install 顺序与 error contract 不变；
- State、Store、command、protocol、持久化和 failover 均不进入 target；
- planned production consumer、test fixture、normative source 与 complexity config 范围没有发现漏项。

## 7. 验证记录

| 验证 | 结果 |
| --- | --- |
| 实施方案第 6.1 节 11 个 exact nodeid + 两个 architecture files | `38 passed in 0.82s` |
| 三份 design writeback 文档相对 Markdown links | `70 checked / 0 missing` |
| production/test worktree | `src/**`、`tests/**`、`pyproject.toml`、`Makefile` 无 diff |
| source inventory | `6 / 18 / 2` token count、2 constructor sites、4 production consumer files与方案一致 |
| A-v1 frozen implementation SHA lookup | `bcd84c…`：仓库 Markdown 中无匹配对象 |
| `make check` | Ruff/format 通过；Pyright `0 errors`；complexity gate `9 passed`；health `51/0/0`；全量 `843 passed`、100% coverage；build/twine 通过 |
| monorepo root 对本 review unit 运行 scoped `pre-commit` | 全部适用 hooks 通过 |
| `git diff --check` | 通过 |

绿色门禁不会替代 R1–R3 的准入闭合。

## 8. 最终裁决

```text
blocker = 0
major = 3
minor = 0

A-v2 technical direction = ACCEPTED IN PRINCIPLE
R1 immutable A-v1 archive = OPEN
R2 requirements / GSP-A06 applicability = OPEN
R3 recovered + exact-shape evidence = OPEN

independent design review = CHANGES REQUESTED
requirements approval = NOT GRANTED
production/tests authorization = NOT GRANTED
```

**最终结论：A-v2 已经找到了值得实施的真实删除对象，但当前仍不能授权编码。先完成 R1–R3 并对新冻结 SHA 复审；通过后再由 requirements owner 与用户显式授权 production implementation。**
