# S01 `GSP-A06` 单项重新设计第三次评审回复

> **结论：接受第三次评审对第二次评审 R1–R3、no-persistence、唯一 owner、基础设计复用和非 complexity 行为证据的通过裁决；成立项已回写 S01 唯一 target，明确 design/evidence closure、`19 passed` baseline 与 planned implementation 的边界，target shape 不变。不接受把该部分审查写成完整的 `S01 GSP-A06 TARGET REVIEW: PASS`，也不接受用四文件“非 complexity manifest”替换 target 拥有的五文件 implementation manifest。`GSP-A06` 明确要求净复杂度证据，当前 target 又明确规定 complexity baseline 未向下锁定时不得进入批准/实现；当前 ratchet 实测仍为 `1 failed, 6 passed`。第三次评审可以作为非 complexity 子项已闭合的审计记录，但尚不能把 S01 提交 requirements owner 批准。S01 继续保持 `PENDING REVIEW / NOT APPROVED`，不得修改 production/tests。**

## 1. 回复信息

- 回复日期：2026-08-23
- 回复对象：[S01 第三次评审](graph-semantics-preserving-simplification-s01-implementation-third-review.zh-CN.md)
- 第三次评审 SHA256：`d082f07426564c2c1edce3ca492991df9d38c4d931ea87262888e78ddf6058dd`
- 评审所绑定的回写前 target SHA256：`03fa8baa2d54aa0b9951c6d0663a6eb279eca3fc4be1471d48b0e4e19832a937`
- 接受项回写后 target SHA256：`f95e883c746a34ab42dddf28b23d0ef97512c1934d7c2517b80e432ae500c28b`
- target owner：[主实施方案第 3.1.2 节](graph-semantics-preserving-simplification-implementation.zh-CN.md#312-s01-gsp-a06-单项重新设计pending-review--not-approved2026-08-23)
- 批准状态 owner：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- 本文性质：review response，只记录第三次评审的接受、限定接受和不接受项；不拥有 target、批准状态或 production shape

## 2. 逐项裁决

| 第三次评审结论 | 回复 | 理由 |
| --- | --- | --- |
| 评审对象是回写前当前 target | **接受** | review 登记 SHA 与接受项回写前的主实施方案 SHA 完全一致；本回复记录后续 owner writeback |
| 第二次评审 R1–R3 已闭合 | **接受并回写状态** | direct-index assertion、真实 NodeOutput consumer、正向 repeated-activation evidence、exact-shape 能力边界和 planned manifest 均由唯一 target 拥有 |
| no-persistence、唯一事实/编排/lowering、基础设计复用方向通过 | **接受** | target 不触及 State/Store/protocol/callback，不新增 runner/DTO/cache/compatibility path，typed `dict` 与最终 `FrozenMap` 职责清楚 |
| 非 complexity 既有 evidence 通过 | **接受并回写记录** | 按 target 登记的 19 个 exact existing nodeid 本次复跑为 `19 passed in 0.43s`；该结果只证明当前 baseline |
| 当前 complexity gate 状态不用于重新否定 R1–R3 | **接受** | R1–R3 的真实性可以独立判断；complexity ratchet failure 不会把已闭合的 nodeid/manifest 问题重新变成未闭合 |
| 一次性 candidate diagnostic 可辅助判断设计方向 | **有限接受** | `RELATIVE/1` 结果与 target 方向一致，但 review 未保存可复现 candidate patch/command，不能代替 implementation actual diff、planned test 或净复杂度 writeback |
| `S01 GSP-A06 TARGET REVIEW: PASS`，可直接提交 requirements owner 批准 | **不接受** | `GSP-A06` 的净复杂度义务和 target 的 baseline 前置仍未满足；partial scope review 无权覆盖两个 owner |
| 四文件清单是 implementation changed-file manifest | **不接受** | 它只是排除 complexity 后的 production/behavior 子集；target 的权威 planned manifest 仍包含 `pyproject.toml`，共五个文件 |

## 3. 已接受并回写的内容

第三次评审对 R1–R3 的复核成立。具体 target shape 在第二次评审回复后已经进入唯一 owner；本轮不复制 review 文本或
改变 shape，只在主实施方案补充 closure/status ledger 和可复现 baseline 记录：

1. `test_compile_indexes_conditional_routes_and_joins` 登记为 `PLANNED ASSERTION`，implementation 必须补
   `direct_targets[GraphNodeId("a")] == (GraphNodeId("b"), GraphNodeId("c"))`；
2. NodeOutput availability/materialization 的三个负向 case 与 repeated-activation 正向 case 分别证明 fail-closed 和
   `RELATIVE/1` 的真实 runtime 消费；
3. architecture case 只证明 `FrontierTransitionPlan`/`CompiledGraph.transition` exact field shape，不冒充 consumer-owner
   gate；
4. `_compile_graph()`、`ActivationGate`、`direct_targets`、`terminal_gates` 与 `FrontierTransitionPlan` 的 owner 分工保持唯一；
5. 当前不实现持久化，不修改 State、command、reducer、protocol、Store、callback 或 memory-installation 语义；
6. 不新增 legacy、private-source-shape、consumer-list AST 或 source-layout gate。

主实施方案现在明确区分：R1–R3 的 design/evidence 引用已经闭合，19 个 existing nodeid 当前通过，而两个新 case 与一个
既有 case 的 planned assertion 尚未落地。该回写不把 review 变成 target owner，也不改变批准状态。

## 4. 非 complexity 审查通过不等于 `GSP-A06` 通过

第三次评审可以形成以下局部结论：

```text
R1–R3 / NON-COMPLEXITY TARGET AUDIT: PASS
```

但不能进一步写成：

```text
S01 GSP-A06 TARGET REVIEW: PASS
READY FOR REQUIREMENTS APPROVAL
```

原因不是重新质疑 R1–R3，而是现行 owner 仍有两个未改变的约束：

1. requirements 的 `GSP-A06` 明确要求每个 P2 在实施前提交“净复杂度证据”；
2. target 明确要求独立 complexity-baseline owner 先把当时 production actual 完整向下锁入 ratchet，并规定
   “baseline 未锁定或任一 limit 高于 actual 时，S01 不进入批准/实现”。

“本轮忽略 complexity gate 当前运行状态”只允许把 complexity 排除出 R1–R3 的局部真实性判断，不能删除
`GSP-A06` 的规范义务，也不能越过 target 自己规定的批准前置。若要改变该顺序，必须先由 requirements/target owner
原子回写；review record 不能创建隐式例外。

## 5. 当前 complexity 前置仍未闭合

本次只读复核得到当前实际 snapshot：

```text
top_level_definitions = 504
type_definitions = 289
dataclass_types = 178
dataclass_fields = 501
decision_points = 1325
```

复跑：

```bash
python -B -m pytest -q -p no:cacheprovider tests/architecture/test_complexity_gate.py
```

结果：

```text
1 failed, 6 passed in 0.49s
```

失败不是 complexity regression，而是当前 checked-in limits 尚未把既有下降锁定：

```text
top_level_definitions  511 -> 504
type_definitions       293 -> 289
dataclass_types        182 -> 178
dataclass_fields       526 -> 501
decision_points       1350 -> 1325
```

这正是 target 登记的独立 baseline-owner 前置条件。它不否定 S01 的设计方向，但在该 owner 完成向下锁定前，第三次
评审不能宣布 S01 已具备 requirements approval 条件。

## 6. 四文件清单不能替换权威 implementation manifest

第三次评审列出的四个文件可以准确表示“排除 complexity 后预计修改的 production/behavior 子集”：

```text
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/graph/test_nested_graph.py
```

但 target 拥有的完整 planned implementation manifest 仍是：

```text
mote-kernel/src/mote_kernel/execution/graph/compiler.py
mote-kernel/tests/execution/graph/test_compiler.py
mote-kernel/tests/execution/graph/test_compiler_contract.py
mote-kernel/tests/execution/graph/test_nested_graph.py
mote-kernel/pyproject.toml
```

两类 complexity 修改不能混淆：

- 独立 baseline-owner unit 先把实施前 production actual 全部锁定，使用自己的 actual manifest；
- S01 implementation 若使 `type_definitions`/`decision_points` 从已锁 baseline 继续下降，则在同一 S01 implementation
  unit 中把对应 limits 下调到 candidate actual，因此 `pyproject.toml` 必须保留在 planned manifest。

第三次评审可以声明本轮不裁决 `pyproject.toml`，但不能据此从 target 的后续实施边界中删除它。否则 actual S01 改善将
无法在同一原子单元锁定，形成明确的 ratchet 负债。

## 7. Evidence 能力边界

本次复跑确认第三次评审所概括的 19 个 existing nodeid 当前全部通过。该结果证明当前 production baseline 与 target
引用相符，不证明两个 `PLANNED` case 和一个 `PLANNED ASSERTION` 已经落地。

第三次评审记录的“一次性内存变换”结果可以作为设计佐证，但没有 exact candidate patch、命令或持久化 artifact，不能由
后续 reviewer 独立复现。因此它不得承担以下职责：

- 不得替代 planned same-source multi-route behavior test；
- 不得替代 production actual diff/source review；
- 不得替代 complexity snapshot、identity diff 或 ratchet writeback；
- 不得把 `PLANNED` 状态改写为当前 `PASS`。

当前 target 已经要求这些证据在 implementation 后按 actual diff 闭合；本次只回写成立项状态，不因该一次性诊断改变
target shape 或实施门禁。

## 8. 当前状态与正确顺序

第三次评审之后可确认：

```text
R1–R3 / NON-COMPLEXITY TARGET AUDIT: PASS
COMPLEXITY BASELINE PRECONDITION: NOT CLOSED
GSP-A06 REQUIREMENTS APPROVAL: PENDING
PRODUCTION IMPLEMENTATION: NOT AUTHORIZED
```

后续顺序仍由当前 target 唯一拥有：

1. 独立 complexity-baseline owner 把当前 actual 完整向下锁定；
2. 对包含净复杂度义务在内的完整 S01 target 作最终准入裁决；
3. 用户明确批准后，由 requirements owner 单独把 S01 `GSP-A06` 记为 satisfied；
4. production + behavior implementation 按五文件 planned manifest 原子落地；
5. owner writeback 记录 actual diff、metric/identity、source review、actual manifest 和全部 gate。

在第 1–3 步完成前，不修改 production/tests，不更新 requirements 批准状态，也不由第三次 review 或本回复代替 owner。

## 9. 本次文档 change units

接受项 owner writeback 与不接受项 review response 保持两个独立的逻辑 change unit，不形成累计 manifest：

1. target owner writeback 的 actual manifest 只有：

   ```text
   mote-kernel/docs/graph-semantics-preserving-simplification-implementation.zh-CN.md
   ```

2. review response 的 actual manifest 只有：

   ```text
   mote-kernel/docs/graph-semantics-preserving-simplification-s01-implementation-third-review-response.zh-CN.md
   ```

第三次评审原文不修改。两个单元都不包含 production、tests、requirements、`pyproject.toml`、State、protocol 或 Store；
回写前后 target SHA 已在第 1 节登记，历史 review 继续作为其原 SHA 的审计输入。
