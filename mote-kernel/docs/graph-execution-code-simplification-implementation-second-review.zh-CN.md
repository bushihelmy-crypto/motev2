# Graph execution 代码简化候选 A 实施方案第二次评审

> **结论：`PASS / A0 CLOSURE ACCEPTED / KEEP / NO PRODUCTION AUTHORIZATION`。首轮 R1–R8 已全部闭合。当前代码只有一个 confirmed root/child runtime owner：`GraphRunContext`；其余结构是不同生命周期的 nominal projection。被评审的 `ScopedStateIndex` dataclass target 没有可证明的 mechanics 删除面，却至少增加一个 type、一个 dataclass、两个 fields 和一次 wrapper allocation。关闭候选 A、保持当前实现，是唯一同时满足唯一真相、零负债、复用既有基础设施与规范代码边界的裁决。**

## 1. 评审对象与冻结版本

- 日期：2026-08-25
- 范围：仅候选 A（`ScopedStateIndex` / `ScopedStateBinding`）的 A0 evidence、首轮整改与 `KEEP` disposition
- 首轮评审：[候选 A 实施方案评审](graph-execution-code-simplification-implementation-review.zh-CN.md)
- 首轮回复：[候选 A 实施方案评审回复](graph-execution-code-simplification-implementation-review-response.zh-CN.md)
- production source baseline：Git `f4f1f7df0a4bdda2dc05a93b6dd29a13d4fd0644`
- production/test diff：空

本次评审冻结以下完整文件内容：

| Target | SHA256 | 本次评审的职责 |
| --- | --- | --- |
| [候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md) | `2d24ba79cc9dee63a24a928827bf13a629565ba9a0979dd5f9dd0f1c929d154f` | A0 evidence 与 disposition 唯一 owner |
| [首轮评审回复](graph-execution-code-simplification-implementation-review-response.zh-CN.md) | `a7ffffdb039e60198535aea186c6cdbc62e4fa180908390ac33ceeaa5426a7b6` | R1–R8 接受/异议记录 |
| [候选调研](graph-execution-explosive-simplification-research.zh-CN.md) | `ea414d510443ef77099569a4b6ca36db4446ab27da0631f4559a6beb2c4b1ac6` | 历史候选来源与 closure 指针 |
| [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md) | `73527660a897bfc94cbef06f75448997b64947f8df7395e34b4d800b31e93e74` | 账本外候选索引与稳定导航；只评审其中候选 A 相关增量 |
| [首轮评审](graph-execution-code-simplification-implementation-review.zh-CN.md) | `db6e5b503cb8b436a034f156180eee287087d6904cc76920082da1cf77d10296` | 历史 finding 基线；本轮未修改 |

本文在上述 target 固定后单独形成，只记录复审裁决，不拥有 A 的 evidence、requirements 或未来 target。

## 2. 原则性裁决

| 原则 | 裁决 | 复核结果 |
| --- | --- | --- |
| 唯一真相 | **PASS** | confirmed state 只由 `GraphRunContext.root_binding/child_states` 持有；requirements、closure、research、主方案与 review 的 owner 方向单向且无重复 disposition |
| 0 负债 | **PASS** | 没有为了“可能有收益”创建空 requirement、compatibility bridge、wrapper、cache、第二 index 或 reviewed smell；production/test 均无 diff |
| 复用基础设施 | **PASS** | 继续直接使用 `GraphRunContext`、scope coordinate constructors、continuation validator、`ScopedFrameIndex` 与既有 commit/install path |
| 优美代码 | **PASS** | confirmed/planned/snapshot/proof nominal boundary 保留；没有用统一宽 record 换取表面缩行 |
| 合理且规范 | **PASS** | exact producer/caller、生命周期、错误证据、复杂度下界、manifest 和 reopening 条件均可复算；strict typing 与架构门禁通过 |

## 3. 首轮 R1–R8 逐项复审

| Finding | 二审裁决 | 闭合证据 |
| --- | --- | --- |
| R1 target/owner 未登记 | **RESOLVED** | closure 是唯一 A0/disposition owner；research 改为历史目录并单向链接 closure；主方案只索引 closure；README 已稳定链接主方案。对“先创建空 requirement ID”的异议成立，不构成遗留 blocker |
| R2 context/index API 重叠 | **RESOLVED** | operation caller inventory 明确 `replace_root()` 只有 `replace_state()` 一个 production caller，`replace_child()` 只有 `replace_state()` 与 child-start 两类 caller；四种 index owner 方案均产生 wrapper、第二入口或纯改名 |
| R3 未证明重复 storage | **RESOLVED** | `_RootStateBinding`/`ChildStateBinding` 的 production constructor 与 snapshot transport 已分开；continuation admission 只运输既有 binding，不再被误记为 producer；planned、Result、proof projection 各有不同生命周期 |
| R4 complexity 未闭合 | **RESOLVED** | 十项 metric 均给出 baseline/下界；原 dataclass target 在无另行删除时至少使 definitions/types/dataclasses/fields 为 `+1/+1/+1/+2`，且没有 scan/filter/sort/projection 减少 |
| R5 continuation 首错阶段 | **RESOLVED** | lifecycle 明确 existing snapshot → context → validator；shape-before-canonicality 与 canonicality-before-content 两个 exact case 冻结 exact text、cause 与 mutation-free 结果；未显式断言的 case 被诚实列为 future characterization gap |
| R6 child start/replacement contract | **RESOLVED** | child start 的唯一 production constructor、commit 后安装顺序、existing-child replacement 的 acknowledged check 与 `parent_activation` 保留均已写清；没有虚构新的 public/private contract |
| R7 缺 case-level evidence | **RESOLVED** | 15 个 exact behavior nodeid 覆盖 state-only、continuation、root/child、partial prefix、canonical Result、repeated superstep/activation；两组架构文件补充 owner/type discipline；文档不把 source review 冒充 test assertion |
| R8 范围与 manifest 过宽 | **RESOLVED** | production/test manifest 为空；docs 分为 owner writeback、navigation sync、独立二审三个 change unit；首轮 review 是既有且未修改的历史输入，不被误计为 writeback |

## 4. KEEP 技术结论复算

### 4.1 Canonical fact 与 projections

[run_context.py](../src/mote_kernel/execution/run_context.py)中只有一份 invocation-time confirmed storage：一个 root binding 加一个
canonical child tuple。`_snapshot()` 把它冻结到 continuation；admission 原样取回，不构造第二组 binding。

[invocation.py](../src/mote_kernel/execution/invocation.py)中的 `_PlannedState` 允许 fence/resume successor 在确认前仅存在于 invocation；
`recovery_seed()` 输出已有 proof boundary。[family_driver.py](../src/mote_kernel/execution/family_driver.py)中的 `_scoped_states()` 只生成
Result view。三者若被合并，会丢失 evidence strength，而不是删除同一个 confirmed fact。

### 4.2 Operation 与结构下界

| 维度 | 当前 | 原 index target | 净结论 |
| --- | --- | --- | --- |
| confirmed storage | root binding + child tuple | 相同 payload 嵌入新 dataclass | 零删除、多一次包装 |
| child lookup | 一个 tuple scan | 仍是 tuple scan | 零删除 |
| child replacement | filter + sort + tuple allocation | 相同 mechanics + index allocation | 回退 |
| lineage/Result/proof | 三种 nominal output | 三种 output 仍需生成 | 零删除 |
| top-level/type/dataclass/field | `504/288/178/500` | 至少 `505/289/179/502` | 四项确定回退 |

把 projection 搬成 index method 只能移动代码位置，不能计为 mechanics 删除；让 context 转发会产生 single-use wrapper，让 consumer
直读则产生第二 operation owner。没有第五种 owner arrangement 能同时保留现有 contract 并获得净删除。

## 5. Evidence integrity 复核

实施文档现在明确区分：

1. **existing executable assertion**：只写对应 case 实际检查的 type、text/fragment、identity、ordering、cause 或 mutation；
2. **source review**：记录 constructor/caller 与 lifecycle，但不记入 pytest assertion；
3. **future characterization**：只有未来 exact target 与 gap 相交时才补齐，不为已关闭 A 新增 private-shape test。

这一分层修正了原稿最关键的证据夸大风险。例如 unknown child scope 的当前 case 只冻结异常类型和文本片段；underlying cause 与
完整 snapshot/state mutation-free 被列为未来补强项。相反，两个 precedence case 通过公共 helper 实际冻结 exact text、
`__cause__ is None`、state equality 与 snapshot identity。`42 passed` 因此只证明已列 executable assertions，不被用来覆盖 gap。

## 6. 文档 owner 与原子边界

当前单向关系为：

```text
README stable navigation
  → 主实施方案：账本外候选索引
      → research：历史来源
      → implementation：唯一 A0 evidence / disposition owner
          ← review / response / second review：只记录裁决

requirements：只拥有 requirement 与 approval 状态
```

候选 A 以 docs-only A0 得出 `KEEP`，没有 implementation approval 范围。此时创建一个没有 production target 的 requirement ID 会
制造空账本；不创建该 ID 是唯一真相与零负债的正确应用。未来若有新 exact target，仍须重新提交 evidence 并取得明确批准。

## 7. 验证记录

本轮在冻结 target 上执行：

| 验证 | 结果 |
| --- | --- |
| 实施文档第 6 节 15 个 exact behavior nodeid + 两个 architecture files | `42 passed in 0.79s` |
| `make check` | Ruff/format 通过；Pyright `0 errors`；complexity gate `9 passed`；health `reviewed=51 / unreviewed=0 / stale=0`；全量 `843 passed`、100% coverage；build/twine 通过 |
| source review | `run_context.py`、`facade.py`、`family_driver.py`、`invocation.py` 的 constructor/caller/lifecycle 与文档一致 |

二审没有修改 production、tests、complexity baseline 或 reviewed inventory。

## 8. Findings 与最终裁决

```text
blocker = 0
major = 0
minor = 0
R1–R8 = RESOLVED
EX-A0 = COMPLETE / KEEP
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE
```

上表中 future characterization gap 是有意披露的重新立项条件，不是当前 A0/KEEP blocker。当前 evidence 足以回答首轮核心问题：
候选 A 没有可删除的第二 confirmed fact，也没有能抵消新增 type/field/allocation 的 mechanics 删除面。

**最终裁决：接受候选 A 的 docs-only A0 closure，保持 `KEEP / NO IMPLEMENTATION`。本裁决不授权任何 production 变更。**
