# Graph 输入绑定与显式执行激活解耦实施方案第三次独立评审

> **结论：`PASS / SECOND-REVIEW DECISION RECONFIRMED / NO TARGET DELTA / READY FOR EXPLICIT IMPLEMENTATION AUTHORIZATION`。**
> 被评审实施方案与第二次评审绑定的版本逐字节一致。本轮重新核对唯一事实、源码消费点、删除闭包、错误顺序、
> 范围边界和门禁要求后，未发现 blocker、major 或 minor；R1–R6 继续保持关闭。本结论只确认当前设计可实施，
> 不自动授权修改 production 或 tests。

## 1. 评审对象与冻结版本

- 评审日期：2026-08-26
- 评审对象：[Graph 输入绑定与显式执行激活解耦实施方案](graph-explicit-activation-implementation.zh-CN.md)
- reviewed implementation SHA256：`5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6`
- 上一轮评审：[第二次独立评审](graph-explicit-activation-implementation-second-review.zh-CN.md)
- 上一轮评审 SHA256：`0c36d54225292000d889173b710468fb7b9d667c80fd7f5410ac7d6e99ede390`
- production source baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- target delta：**无**；本轮 SHA 与第二次评审的 reviewed implementation SHA 完全相同
- 本文性质：只拥有本轮评审裁决和验证证据，不拥有或复制 graph 目标语义
- 本轮 change unit：只新增本文；不修改实施方案、production、tests、State 或其他用户文件

## 2. 独立复核结论

| 评审维度 | 结论 | 复核依据 |
| --- | --- | --- |
| 唯一事实 | **PASS** | canonical value source仍是 immutable node input declarations；canonical activation source仍是 entries/direct/conditional/join declarations；compiler locals、compiled plan与routing facts仅为derived lowering |
| 基础设施复用 | **PASS** | 缺失显式control在现有compiler phase内直接抛现有`GraphValidationError`；继续复用control indexes、guarantee proof、materialization、routing与共享recovery resolution |
| 零新增负债 | **PASS** | 不新增error subtype、helper、DTO、field、flag、alias、cache、public API或第二runner；删除`DataTriggerPlan`、`data_triggers`、`data_targets`及三条dormant fallback |
| 改动边界 | **PASS** | production严格限定compiler、topology、routing、resume admission、recovery五文件；recovery/resume只做stale-field机械删除，不做顺手重构 |
| 语义简洁性 | **PASS** | `inputs=`只负责值绑定，direct/conditional/join只负责激活；删除data dependency对control reachability和runtime successor生成的越权 |
| no persistence/failover | **PASS** | 明确排除State、Store、checkpoint、definition version、deployment、rollback、active-execution recovery与failover协议 |
| 门禁闭合 | **PASS** | 要求确定性contract/错误顺序/零副作用/回归测试、complexity before/after writeback与ratchet下调，禁止新增waiver或提高limit |

### 2.1 Compiler 与错误顺序

目标phase顺序仍保证unknown/self source、unknown output、data cycle和explicit START dependency error先于新的
missing-control error。新判断直接消费既有typed `data_dependencies` 与 `activation_gates`，无需创建新owner。

三条仅服务隐式激活的fallback仍被明确要求删除：

1. `_guaranteed_sets()` 的data-only guarantee传播；
2. `_validate_joint_activation_paths()` 的no-control data alternative；
3. `_input_publication_selection()` 的`not gates` relative-coordinate fallback。

这使非法data-only consumer在compiler fail closed，同时不改变已有direct、conditional或join gate的合法图。

### 2.2 Production manifest 与runtime闭包

对当前`src/mote_kernel/execution/**`重新检索，隐式data-trigger的producer/consumer仍严格落在方案列出的五个文件：

```text
execution/graph/compiler.py
execution/graph/topology.py
execution/engine/routing.py
execution/engine/resume_admission.py
execution/engine/recovery.py
```

未发现第六个production owner。`execution/errors.py`已有`GraphValidationError`，无需修改；`facade.py`、State、Store、
persistence与public export也不消费待删除shape。删除runtime publication-trigger scan后，routing只依据显式control target、
completed join、partial join与graph-output availability作出`Advance`、`Deadlock`、`Abort`或`Complete`，没有第二执行路径。

### 2.3 适当范围而非过度或最小改动

只在compiler增加missing-control判断而保留runtime兼容字段，会留下不可达分支和双重语义，属于欠改；扩展到State、
version、persistence、deployment或failover则属于过改。当前方案同时完成validation、compiled shape、routing consumers、
definition/tests/docs迁移和complexity writeback，恰好闭合语义删除，没有引入相邻架构工程。

## 3. R1–R6 状态

| Review item | 状态 | 本轮结论 |
| --- | --- | --- |
| R1 persistence/version/failover越界 | **CLOSED** | 排除项和停止条件仍完整 |
| R2 新增`MissingActivationError` | **CLOSED** | 固定复用现有`GraphValidationError` |
| R3 三条dormant fallback漏删 | **CLOSED** | 三条均有精确删除要求 |
| R4 explicit START错误优先级 | **CLOSED** | START dependency error仍先于missing-control |
| R5 canonical/derived owner混淆 | **CLOSED** | declaration与lowering边界清晰 |
| R6 complexity/test/gate未闭合 | **CLOSED** | focused tests、writeback、ratchet与全量门禁完整 |

本轮新增发现：`blocker = 0 / major = 0 / minor = 0`。

## 4. 实施复核约束

第二次评审中的非阻断约束继续有效，不升级为新设计项：

1. Phase 0后按实际diff记录test/example/doc manifest，inventory命中不等于自动授权修改；
2. 零副作用证据复用现有测试设施，不新增production hook、通用spy framework或重复fixture体系；
3. 删除publication scan时清除其专属import，不留下stale symbol；
4. symbol归零检查只扫描production与active tests，不把历史/评审文档中的删除说明算作命中；
5. complexity identity若因行号移动而writeback，必须确认逻辑身份未变化且没有新增reviewed item。

以下任一情况会使本次通过失效：production manifest扩大；三条fallback未全部删除；新增兼容层或第二路径；错误优先级、
automatic entry、natural completion或graph-output projection语义改变；recovery出现新算法；State、Store、version、
persistence、deployment或failover进入scope；complexity需要提高limit或新增waiver。

## 5. 验证记录

| 验证 | 结果 |
| --- | --- |
| implementation SHA复核 | 与第二次评审绑定的`5195194b...c5ad96c87e3c6`一致 |
| source/worktree复核 | 本轮评审前`src/**`、`tests/**`、`pyproject.toml`、`Makefile`无diff |
| source consumer inventory | 五文件manifest完整，无第六个producer/consumer |
| `make check` | **PASS**：Ruff/format通过；Pyright `0 errors`；complexity `9 passed`且`51 reviewed / 0 unreviewed / 0 stale`；全量`843 passed`、coverage 100%；build/twine通过 |
| monorepo root scoped pre-commit | **PASS**：对本文运行全部适用hooks，全部通过 |
| implementation文档未改动 | **PASS**：门禁后SHA仍为`5195194b4652c0def54eb13248d456b56e81b1132653a47f0fbc5ad96c87e3c6` |

## 6. 最终裁决

```text
target delta = NONE
blocker = 0
major = 0
minor = 0

R1–R6 = CLOSED
single truth / infrastructure reuse = PASS
zero-new-debt / deletion completeness = PASS
appropriate implementation scope = PASS
no-persistence / no-failover boundary = PASS
gate design = PASS
implementation readiness = READY FOR EXPLICIT USER AUTHORIZATION
production/tests authorization = NOT GRANTED BY THIS REVIEW
```

**最终结论：第二次评审裁决有效且本轮独立复核通过。当前方案以既有Graph/compiler/control/routing基础设施为唯一实现路径，
完整删除隐式激活的类型、字段、索引与fallback，范围不欠缺也不外溢，可以进入用户显式授权后的原子实施阶段。**

## 7. 本次 review change unit

本轮只新增：

```text
mote-kernel/docs/graph-explicit-activation-implementation-third-review.zh-CN.md
```

未修改被评审实施方案、既有评审、production、tests、State、Store、protocol、persistence、complexity配置或其他用户文件。
