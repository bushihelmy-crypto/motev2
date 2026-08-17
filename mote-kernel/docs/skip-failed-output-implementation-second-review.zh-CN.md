# `skip_failed` 可选替代输出实施设计第二轮评审

## 1. 评审结论

- 评审对象：`docs/skip-failed-output-implementation.zh-CN.md`（2026-08-17 19:58 更新版）
- 需求基线：`docs/skip-failed-output-requirements.zh-CN.md`
- 前轮评审：`docs/skip-failed-output-implementation-review.zh-CN.md`
- 结论：**前轮 2 个 P1、3 个 P2 均已在设计层关闭；实施设计通过，可以开始 production 编码。**

本结论只批准按当前设计实施，不批准偏离设计的第二路径。编码中触发第 7 节任一停止条件，必须立即停止并重新评审。最终合入仍以测试、类型、覆盖率、`make check` 和 monorepo pre-commit 实际通过为准。

## 2. 前轮问题关闭情况

| 前轮问题 | 状态 | 关闭证据 |
| --- | --- | --- |
| P1-1：continuation pure skip 未运行 whole-future proof | 已关闭 | 第 6.5、6.6、7.1 节规定凡 invocation 含 pure skip，无论 continuation 或 state-only，都在首个 commit 前运行同一 future-path traversal |
| P1-2：candidate 未绑定 provenance 与 expected revision | 已关闭 | `PreparedSubstitution` 固定 coordinate/frame/provenance；reducer 后机械提升为绑定 `successor.revision` 的 `AdmittedSubstitution` |
| P2-1：shared resolver owner/API 不明确 | 已关闭 | `routing.py` 被确定为唯一 topology/routing facts owner，并给出 `resolve_routing_facts()` typed contract及三类消费者 |
| P2-2：candidate overlay read contract 未决定 | 已关闭 | overlay明确只实现 presence-only `ScopedFrameAvailability`，禁止 `lookup()`；需要读取 concrete candidate frame即停止重审 |
| P2-3：post-commit invariant与frame安装边界不清 | 已关闭 | 明确 `FrameInstallationInvariantError`；同一scope全部frame records先在局部构造一个immutable index，再一次替换 |

## 3. 架构复审

### 3.1 State 与用户语义：通过

- public API 仍只有 `Graph.skip_failed(..., output=None)`；
- durable settlement 仍只有 `SkippedGraphNode`；
- pure skip和substitution skip不形成两种public action/result/error体系；
- `GraphRunState` 不增加replacement value、marker、history或journal；
- output是否存在由publication availability表达，不形成第二control truth。

这满足“用户不区分两类skip”，同时避免在State中建立可与publication漂移的重复事实。

### 3.2 唯一真相与基础设施复用：通过

- concrete frame和closed provenance只进入唯一`ConfirmedPublication`；
- confirmed records只进入`ScopedFrameIndex.publications`；
- candidate overlay只用于一次invocation的proof，既不持久化也不提供读取路径；
- runtime materialization、graph output和nested boundary只读取confirmed index；
- execution success与skip substitution复用同一publication record/store/lookup；
- existing descriptor、values admission、coordinate、reducer、commit和recovery worklist均被复用。

`PreparedSubstitution`与`AdmittedSubstitution`是不同阶段的typed evidence，不是两个publication store。前者不能安装，后者只能在exact commit后机械提升，所有权边界清楚。

### 3.3 Routing/data-flow唯一 owner：通过

设计现在固定`routing.py`的`resolve_routing_facts()`为唯一解释下列事实的owner：

- direct/conditional control targets；
- join arrivals、completed targets与remaining progress；
- 由confirmed/candidate publication availability触发的data targets；
- 必达target完整input availability；
- graph completion output availability。

`plan_routing()`、resume admission和recovery只能对相同facts做不同投影，不能各自扫描topology。这关闭了runtime/recovery语义漂移风险。

### 3.4 泛型与封闭类型：通过

- `SkipFailedNodeRequest[GraphValueT]`保存`_GraphValues[GraphValueT] | None`；
- `ResumeNodeRequest[GraphValueT]`的variants保持同一universe；
- prepared/admitted candidate、availability overlay与publication均贯穿`GraphValueT`；
- provenance使用closed nominal union，不使用nullable/fake token或字符串discriminator；
- negative typing fixture覆盖cross-universe、heterogeneous与empty `Never`。

按当前设计不需要`Any`、`object`、bare container、reflection或generic-erasing cast。

### 3.5 Admission与commit：通过

设计明确区分：

1. 首个commit前完成所有scope/action/output/route/future-path/duplicate admission；
2. reducer模拟产生exact successor与expected revision；
3. commit必须返回exact successor；
4. confirmed publication只能由admitted plan机械提升；
5. memory State按durable-first替换；
6. 同一scope的frame records先完整构造，再一次替换frame snapshot；
7. 不模拟跨scope durable transaction，也不补偿已确认scope。

pre-commit collision仍是`Graph.ValuePublicationError`；post-commit defensive collision明确为不公开的`FrameInstallationInvariantError`，错误边界一致。

## 4. 对需求基线的逐项判断

| 需求维度 | 结论 |
| --- | --- |
| 单一Graph facade与execution engine | 满足 |
| State不保存concrete output | 满足 |
| 用户不区分两类skip | 满足 |
| pure skip不贡献自身data trigger | 满足 |
| substitution publication贡献data trigger | 满足 |
| compiled dependency不自动成为必达target | 满足 |
| 其他control/join/data contribution激活后校验完整inputs | 满足 |
| continuation与state-only共用proof truth | 满足 |
| graph output、nested boundary、loop与join future proof | 满足 |
| duplicate admission在首个commit前覆盖跨scope candidates | 满足 |
| provenance在planning阶段固定 | 满足 |
| plan/install exact evidence闭环 | 满足 |
| stable continuation不倒推历史settlement | 满足 |
| parent nested boundary substitution不改写child | 满足 |
| public error mapping固定 | 满足 |
| strict generic与negative typing | 满足 |
| no aliases/second store/runner/resolver/lookup | 满足 |

## 5. 门禁充分性

当前测试矩阵能够覆盖本功能的主要风险：

- API shape、canonical values与descriptor exact admission；
- pure/substitution data trigger和各类必达target；
- continuation/state-only future graph-output与nested-boundary proof；
- existing/candidate/cross-scope duplicate零commit；
- exact/non-exact/throw commit及跨scope部分确认；
- candidate到confirmed record逐字段exact提升；
- frame snapshot安装与internal invariant分类；
- continuation provenance、future revision、loop/nested/sibling identity；
- architecture gates阻止第二store、lookup、resolver、runner及public export；
- cross-universe negative typing。

交付门禁固定为：

```bash
# mote-kernel
make check

# monorepo root
pre-commit run --all-files
```

还必须满足仓库既有100% branch coverage。若任何门禁未运行或失败，交付说明必须精确列出，不能把设计评审通过等同于实现验收通过。

## 6. 实施时的非阻塞注意事项

以下不是当前设计阻塞项，但编码时必须原样落实：

1. `resolve_routing_facts()`的data contribution必须按exact activation publication availability判断，不能退回`SucceededGraphNode`类型判断。
2. `RequiredTarget`若同一node同时由control、join和data触发，投影前必须canonical去重；不得重复产生frontier node。
3. `completion_output_available`只在没有next target且确实进入completion判定时具有决策意义，保持与现有routing语义一致。
4. `PreparedSubstitution`到`AdmittedSubstitution`只能增加expected revision，不得重建frame、coordinate或provenance。
5. internal install helper可以复用`ScopedFrameIndex.add_*()`，但不得把post-commit defensive error重新暴露成`Graph.ValuePublicationError`。
6. architecture gate应约束runtime读取，而不要误禁compiler在构造immutable topology时写入direct/conditional/join/data-trigger maps。
7. `FrameInstallationInvariantError`不得挂到`Graph` namespace，也不得成为正常调用方分支协议。

## 7. 编码停止条件

实施中出现以下任一情况，设计批准立即失效：

- 需要修改`GraphRunState`或durable command保存replacement marker/value；
- future proof需要读取candidate concrete frame或新增candidate `lookup()`；
- recovery/runtime无法只通过`resolve_routing_facts()`解释routing/data contribution；
- duplicate admission无法在首个commit前覆盖所有scope；
- strict typing需要`Any`、`object`或erasing cast；
- 需要第二publication store、resolver、runner、public method或compatibility alias；
- parent substitution需要恢复或改写terminal child；
- 需要跨scope compensation或facade transaction coordinator；
- stable continuation需要历史State、journal或durable substitution marker。

## 8. 最终准入

第二轮实施设计已关闭前轮所有阻塞项，满足零负债、唯一真相、基础设施复用、State单一skip语义、严格泛型及门禁约束。

**批准按`docs/skip-failed-output-implementation.zh-CN.md`当前版本开始production编码。**

批准范围仅限文档确定的owner、typed model、proof与commit顺序，以及只通过`resolve_routing_facts()`解释routing/data contribution。实现完成后仍需以完整测试和仓库门禁结果做最终验收。
