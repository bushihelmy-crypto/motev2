# Graph execution 代码简化候选 A 实施方案评审回复

## 1. 回复信息

- 日期：2026-08-25
- 回复对象：[候选 A 实施方案评审](graph-execution-code-simplification-implementation-review.zh-CN.md)
- 评审对象原 SHA256：`1f5fca41eb2cde31ae4240978d87449e17a68dee874ee0a0351103c0b18ceec3`
- 整改 owner：[候选 A 实施准入与关闭记录](graph-execution-code-simplification-implementation.zh-CN.md)
- 后续复审：[候选 A 实施方案第二次评审](graph-execution-code-simplification-implementation-second-review.zh-CN.md)
- 状态：`REVIEW DISPOSITION COMPLETE / R2–R8 ACCEPTED / R1 CONCERN RESOLVED WITH PARTIAL ACCEPTANCE / A CLOSED — KEEP`

总体 `CHANGES REQUESTED / NOT APPROVED` 裁决成立。评审正确指出原稿预设了一个与 `GraphRunContext` 重叠的 index/API，却没有
证明真实删除面。整改没有进入 production，而是完成 A0 并裁决 `KEEP / NO IMPLEMENTATION`。

本回复只记录评审意见的接受/异议和理由，不拥有 A 的 target、requirements 或批准状态。

## 2. Disposition 总表

| 评审项 | 回复裁决 | Owner writeback |
| --- | --- | --- |
| R1 target/owner 登记 | **部分接受，问题已闭合** | 明确 A0/disposition 唯一 owner并同步 research/main 导航；不为 KEEP 创建空 requirement ID |
| R2 context/index API 重叠 | **接受** | 删除预设 index shape，列出四种 owner 方案均不成立 |
| R3 未证实重复 storage | **接受** | actual inventory 证明只有一份 confirmed storage，其他是 typed projection |
| R4 complexity 未闭合 | **接受** | 补齐十项 ratchet lower bound；至少四项确定回退，禁止改 baseline/reviewed inventory |
| R5 continuation 首错 | **接受** | 固定 raw snapshot → context → validator 的现有阶段和 exact cases |
| R6 child start/replacement | **接受** | 记录当前 insert-or-replace mechanics 与 `replace_state()` acknowledged check；不擅自新增 contract |
| R7 case-level evidence | **接受** | 登记 exact nodeid、错误/顺序/mutation 目标 |
| R8 B 与 manifest 过宽 | **接受** | B 从 A 文档移除；production/test/recovery manifest 为空 |

## 3. R1 部分接受、部分不接受

### 3.1 接受部分

原稿确实没有把文档 owner 关系写清楚，同时在未完成 A0 时预设 exact target shape。整改后：

- 当前 implementation 文档唯一拥有候选 A 的 A0 evidence 和 `KEEP` disposition；
- requirements 继续唯一拥有 `GSP-Pxx`、`GSP-Axx` 和任何批准状态；
- 调研文档只保留历史候选来源并链接 closure；主实施方案只保留账本外候选索引并链接 closure；
- review/response 只拥有评审记录，不反向成为 target owner；
- A1/A2、production 和 behavior tests 均未获授权。

### 3.2 不接受“先新增 requirement ID 才允许 A0/KEEP”

该顺序与现有 requirements 第 6 节不一致。`GSP-A06` 已明确覆盖“后续新增 P2”：候选必须先提交 exact signature、删除对象、净
复杂度和 behavior evidence，之后才能申请 requirements 单项批准。也就是说，设计/A0 evidence 逻辑上必须先于批准；否则
requirements owner 没有可审查对象。

本次 A0 又得出 `KEEP / NO IMPLEMENTATION`，不存在 production target、manifest 或批准范围。为一个已经关闭的非目标新增
requirement ID，只会制造空账本和第二 disposition 记录，不符合唯一真相与零负债。

因此采用以下边界：

```text
docs-only proposal / A0 / KEEP  → 不要求新增空 requirement ID
未来若重新提出 A1 target      → 必须先按 GSP-A06 形成 evidence，再由 requirements owner 明确批准
production/tests                → 无批准不得修改
```

评审第 1 节记录的是首轮起点：用户当时给出
`graph-execution-explosive-simplification-implementation.zh-CN.md`，该 exact path 当时不存在，因此首轮 review 改以实际形成的
`graph-execution-code-simplification-implementation.zh-CN.md` 为对象。本次后续请求明确要求完善 implementation 并新建本回复。
两次路径事实都应保留，但 path 本身不能证明 owner；owner 由 requirements、research、主实施方案和 closure 的单向链接关系闭合。

## 4. R2–R4 接受后的技术结论

actual call graph 显示：

- `GraphRunContext.root_binding` 与 `child_states` 是唯一 confirmed storage；
- `lineage_states()`、`_scoped_states()`、`recovery_seed()` 分别生成 planned、Result、proof nominal projection；
- tuple-based index 不减少 child scan、filter、sort 或 allocation；
- context delegate 会生成 single-use wrapper，consumer 直读会生成第二 operation owner；
- 被评审的最小新 dataclass 在没有另行删除时使 `top_level_definitions`、`type_definitions`、`dataclass_types`、
  `dataclass_fields` 分别 `+1/+1/+1/+2`；把既有 projection 搬进 method 不能算 mechanics 删除，且仍无法抵消
  type/dataclass/field 回退。

因此无需继续寻找一个“更巧妙”的 `ScopedStateIndex` shape。当前约束下它不是 Pareto 改进。

R4 的逐指标要求成立，因为当前 `make complexity-ratchet` 明确对每一个 configured metric 拒绝增长，并要求已下降指标同步下调
baseline。整改不会上调 `pyproject.toml`，也不会把新 smell 登记为 reviewed 以绕过 health gate。

## 5. R5–R7 接受后的行为边界

整改文档已经把以下行为分成“现有 executable assertion”“source review”“future reopening 必须补齐”三层：

- continuation malformed shape → canonicality → content 的既有首错阶段；
- duplicate child、unknown scope、run ID mismatch、parent activation mismatch 的现有 type/text-fragment assertion；
- unknown child read/replace 的现有 type/text-fragment assertion；
- acknowledged child start、existing child replacement 与 graph-input frame installation 顺序；
- multi-scope resume partial prefix 的 state/frame install 集合及原始 `__cause__`；
- continuation snapshot immutability、root→child Result order、repeated superstep 与 repeated child activation 隔离；
- architecture owner、strict typing 和 module-scope import gates。

其中 unknown/duplicate/tamper case 尚未显式断言的完整 mutation-free、exact cause 或完整文本，只能作为 source-reviewed/future
characterization，不能借本轮通过数声称已经冻结。这些边界不新增永久 AST/private-source-shape gate；由于 A 关闭，本轮不修改
或新增 tests。

## 6. R8 接受后的范围

其他候选的设计、probe 和阶段已从 A 文档删除，不复用 A 的 target owner。`engine/recovery.py` 也从 manifest 删除；A0 只读确认
`RecoveryStateBinding` 是 existing nominal proof boundary，没有修改该算法或文件，production/test manifest 为空。

docs manifest 分成 A0 owner writeback、research/main navigation sync 和 target 固定后的独立 second review；首轮 review 在本 change
unit 前已经存在且保持未修改。其余能力不在候选 A 范围，本回复不展开。

## 7. 最终回复裁决

```text
first-review verdict = CHANGES REQUESTED / NOT APPROVED (ACCEPTED AS HISTORICAL)
response disposition = COMPLETE
R1 = PARTIALLY ACCEPTED
R2–R8 = ACCEPTED
EX-A0 = COMPLETE / KEEP
EX-A1 = NOT APPLICABLE
EX-A2 = NOT APPLICABLE
production/tests = NO CHANGE
```

评审要求回答的核心问题是“在不新增第二 owner、第二事实源或结构负债的前提下，A 删除哪段真实代码”。A0 的答案是：**当前没有
可删除段。** 因此保持现状是本轮唯一满足唯一真相、零负债和复用现有基础设施的结论。
