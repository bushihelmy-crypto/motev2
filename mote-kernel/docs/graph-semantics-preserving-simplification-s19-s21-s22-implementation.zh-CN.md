# S19 / S21 / S22 Graph 执行尾项语义保持型简化实施方案

## 1. 文档信息与裁决

- 状态：`S19 IMPLEMENTED / VERIFIED; S21 CLOSED / KEEP; S22 CLOSED / KEEP`
- 日期：2026-08-25
- 单元：S19、S21、S22（P2；共用一份设计 owner，保持三个独立 `GSP-A06` disposition unit；只有 S19 有 implementation unit）
- 源码基线：Git `2709a59e8f5e83e6de32610faf546ef33f030fad`（S19 implementation commit）
- production 基线：
  - `src/mote_kernel/execution/executor.py`，SHA256
    `98f0a1725c9fd618cbd28bd6a8d28ef0985106915208b5a197ff202de4d66ebb`
  - `src/mote_kernel/execution/facade.py`，SHA256
    `d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9`
  - `src/mote_kernel/execution/invocation.py`，SHA256
    `5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4`
- primary behavior 基线：`tests/execution/test_graph_api.py`，SHA256
  `10798170335b53157c7b3825e5b0ab6f8d62d0439ef54e81ec791be18bfdb18b`
- public typing 基线：`tests/execution/test_graph_public_typing.py`，SHA256
  `a24427da3d363323b04c18a45a61216014b9068d35706e27094fccc0480efff2`
- typing fixture runner 基线：`tests/architecture/test_graph_typing_fixtures.py`，SHA256
  `012f35218b8d4e396a91a7136c9438b3c8eaf3d7f2f73b2834cc5ffba8875138`
- executor behavior 基线：`tests/execution/test_executor.py`，SHA256
  `2b37f8ffb28cb1e409816c920b0f03fc947839e1725413210c60c400ed0bb103`
- resume-input owner behavior 基线：`tests/execution/engine/test_resume_input_contract.py`，SHA256
  `56dcd49e4de114e465579596a6cba3c32328ccad634e6363c46a89fb0961c5ff`
- execution owner behavior 基线：`tests/architecture/test_graph_execution_ownership.py`，SHA256
  `832002901820f8b147f09f1c03747b9fff369f4461e562db52ff671f564231b3`
- full-suite baseline（排除用户明确排除的 automated complexity gate）：`833 passed`、line/branch coverage
  `100.00%`；S19/S21/S22 scoped baseline：`160 passed`
- State / persistence：`HARD KEEP`；不实现持久化，不修改 State、command、reducer、revision、durable protocol、Store 或 backend。
- error recovery：`HARD KEEP`；S19 只去重既有 resume override value admission，不新增恢复能力；S21、S22 production
  均零 diff。既有 recovery admission、fence 与 partial-prefix handoff 逐字保持，不新增 retry、fallback、checkpoint、
  failover、补偿事务或第二 recovery runner。
- automated complexity gate：用户明确排除；不读取、不修改、不执行、不以其作为批准或交付证据。
- legacy/private-shape test gate：用户明确排除；不增加冻结 helper 名、局部变量、源码行数、AST 布局或旧 private path 的测试。

关联 owner：

- [requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)唯一拥有 `GSP-P01`–`GSP-P08`、
  `GSP-A06` 和批准状态；当前 S19 已获批准，S21/S22 已按 KEEP 关闭。
- [主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)拥有总账、阶段顺序和 P2 候选边界。
- [首次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review.zh-CN.md)只拥有对前版
  SHA256 `d8161e0b2e3e22349411091b9a5c28927998d356ad01bb74886d075fd6c95bf0` 的裁决与证据，不拥有本文修订后的 target。
- [首次评审回复](graph-semantics-preserving-simplification-s19-s21-s22-implementation-review-response.zh-CN.md)只拥有
  R1–R8 disposition 与整改索引，不复制本文 target shape 或 requirements 批准状态。
- [第二次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review.zh-CN.md)只拥有对前版
  SHA256 `d3ef4eabcd6e109fa0a6f08d8e175400719c8d12a41c49e535cea85f0da9459a` 的裁决与 R9 证据，不拥有本文修订后的 target。
- [第二次评审回复](graph-semantics-preserving-simplification-s19-s21-s22-implementation-second-review-response.zh-CN.md)只拥有
  R9 disposition、可观察断言边界与整改索引，不复制本文 target 或 requirements 批准状态。
- 本文唯一拥有 S19、S21、S22 的 current-production audit、exact target、等价证明、actual manifest、行为证据、
  实施门禁和停止条件；评审记录不得复制 target shape。
- `execution.executor.GraphExecutor.resume()` 继续唯一拥有 scoped resume action validation 与最终 simulated frontier validation。
- `execution.facade.Graph.run()` 继续是唯一 public lifecycle owner。
- `execution.family_driver.commit_transition()` 继续唯一拥有 reducer candidate、commit callback 与 exact confirmation。
- `execution.invocation.install_confirmed_resume_frames()` 继续唯一拥有 confirmed resume frame installation validation。
- `execution.run_context.GraphRunContext` 与唯一 `ScopedFrameIndex` 保持现有 owner，不新增平行 mutable context 或 frame store。

设计阶段本文及评审回复只修改 docs；第三次独立技术评审绑定本文 SHA256 `07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`，
requirements owner 随后按单元分别批准 S19 并关闭 S21/S22。implementation-owner writeback 已记录已授权 S19 的实际两文件
变更、focused verification 和负向 manifest；S21/S22 production/test manifest 为空，不创建空 implementation commit。批准不得跨
单元、target 或 SHA 继承。

## 2. 总结论

| ID | Exact 裁决 | Production 结果 | 理由 |
| --- | --- | --- | --- |
| S19 | 提取一个 executor-owner 的窄 typed override-input admission method | 只删除 failed override 与 interrupt resume 中重复的 encode → decode/frame-admission pair；保留各分支 validation 和现有唯一 admitted-coordinate construction | 两条路径在 action-local validation 之后的 codec mechanics 完全相同，输入与输出均有既有 nominal type |
| S21 | `KEEP / NO PRODUCTION CHANGE` | 不新增 private run path、DTO、context、dispatcher 或第二 runner | 当前 new/state/continuation 三路只有 lifecycle 编排相邻，没有第二份可删除的相同 mechanics；机械提取只会转移代码并增加协议 |
| S22 | `KEEP / NO PRODUCTION CHANGE` | 不提取 confirmation helper，不 import/export `_PlannedFence` / `_PlannedResume`，不修改 transaction loops | 完整探针表明 helper 必须新增跨模块 type surface 和 nominal dispatch，不能形成净简化；现有差异化 exception/install boundary 更直接 |

S21、S22 的 no-op 都是完成 exact audit 后的正向裁决，不是待补实现。若未来出现新的 lifecycle dispatch 或不扩大 type surface
即可删除的 transaction mechanics，必须以新基线重新设计，不能把本文 `KEEP` 当作预授权。本轮唯一 production 候选是 S19；
它不以减少行数为目标，只有唯一事实、依赖方向、错误优先级和 State-command/frame 语义全部闭合时才可实施。

## 3. 全局硬边界

### 3.1 唯一真相与复用基础设施

| 事实 / 能力 | 唯一 owner | 本轮处理 |
| --- | --- | --- |
| compiled node materialization 与 resume codec | `CompiledGraph` + `engine.resume_input` | S19 只调用既有 `_require_node_materialization()`、`encode_resume_input()`、`decode_resume_input()` |
| action settlement / interrupt identity / skip routing validation | `GraphExecutor.resume()` 的 nominal branch | `KEEP`；不得搬入共用 helper |
| authoritative successor | `reduce_graph_run()` 经 `commit_transition()` 暴露和 exact-confirm | `HARD KEEP`；S22 零 production diff |
| confirmed State | caller commit 返回且与 reducer candidate exact equal 的 `GraphRunState` | `KEEP`；不得缓存 candidate、confirmed pair |
| confirmed frames | `ScopedFrameIndex` | `KEEP`；resume frame 必须由 existing installer 预计算后一次替换 |
| partial-prefix public handoff | `_partial_commit_error()` + `_continuation(context)` | `KEEP`；不新增 error wrapper 或 recovery result |
| public invocation lifecycle | `Graph.run()` | `HARD KEEP`；S21/S22 不新增 helper，S19 method 不调用 compile/commit/drive/project |

禁止新增 compatibility alias、forwarding property、generic `utils/common/shared/helpers` 模块、bare dict、`Any`、`object`、反射、
字符串 kind、mutable context bag、callback adapter、第二 frame index、第二 commit path、runner/session/transaction manager 或持久化端口。

### 3.2 明确不属于本轮

- 不设计或实现数据库、journal、checkpoint、repository、Store、事务后端或跨进程恢复。
- 不改变 commit callback 的接口、调用次数、顺序、transition shape 或 exact-confirmation 规则。
- 不改变 continuation sealing、序列化边界、recovered/complete snapshot shape 或 State 权威性。
- 不设计错误重试、自动补偿、fallback 或 failover；existing partial-prefix handoff 只做语义保持。
- 不修改 `src/mote_kernel/state/**`、`tests/state/**`、durable/conformance protocol。
- 不执行或维护 automated complexity gate；不增加 legacy/private-source-shape test。

## 4. S19 exact implementation design

### 4.1 当前行为与重复面

`GraphExecutor.resume()` 先完成全局 admission：State、scope、quiescent status、resume codec binding，以及 action tuple 的 non-empty、
canonical、distinct、scoped 校验。每个 action 再按原 tuple 顺序处理。

进入每个 action 后，现有公共首错顺序先执行一次 `frontier_node(state.frontier, requested.node_id)`；unknown node 立即失败，
随后才构造一次 `StableActivation`。failed override 与 interrupt resume 接着各自执行一次完全相同的 value-admission pair：

1. 已经取得当前 node 的 `MaterializationPlan`；
2. 分支本地 settlement validation 已通过；interrupt 分支还必须先通过 exact interrupt ID validation；
3. `encode_resume_input(self._graph, requested.input.values)` 生成 durable command binding；
4. `decode_resume_input(self._graph, requested.node_id, bytes(binding.payload))` 以同一 codec 回读；其 `_admit_override()`
   内部有意再次执行 `_require_node_materialization()`，再做 concrete frame admission；
5. 离开 failed/interrupt nominal branch 后，现有唯一公共代码才以同一 `StableActivation`、同一 materialization descriptor
   identity 和本分支产生的 frame 构造并 append `AdmittedResumeInput`。

可删除的重复面只有第 3–4 步；第 5 步当前已经只有一个 owner，必须留在 caller，不得搬入 helper。每个 override action 的
materialization lookup 当前是两次，target 仍是两次：第一次由 executor
取得 `plan` 并拥有 coordinate，第二次由 decoder owner 防御性取得 declaration。第二次不是待删除缓存负债；不得把 `plan` 传入
decoder、绕过 `_admit_override()`、缓存 declaration 或新增 coordinate owner。failed materialized retry、skip routing、skip
substitution、settlement validation、interrupt identity、command variant 和 replacement settlement 都不相同，必须继续由各自
nominal branch 拥有。

### 4.2 Exact private signature

在 `GraphExecutor` 内新增且仅新增一个 private method：

```python
def _admit_override_resume_input(
    self,
    node_id: GraphNodeId,
    override: OverrideNodeInput[GraphValueT],
) -> tuple[OverrideGraphNodeInput, NodeInputFrame[GraphValueT]]:
    binding = encode_resume_input(self._graph, override.values)
    frame = decode_resume_input(self._graph, node_id, bytes(binding.payload))
    return binding, frame
```

`OverrideNodeInput[GraphValueT]` 是 request owner 已有的单字段 nominal port，已在 executor import block；`NodeInputFrame[GraphValueT]`
与 `OverrideGraphNodeInput` 复用既有 nominal types，并加入 module-header import block。不得把完整 resume action/request、activation、
plan、State、frames 或 accumulator 传入 method，不得新增返回 DTO、type alias、protocol 或 context 参数。method 不读取 settlement、
不修改任何对象，不捕获异常，不改变 exception cause。

### 4.3 Exact caller order

failed override 分支顺序固定为：

```text
frontier_node lookup → reject unknown node
→ derive StableActivation
→ require materialization plan（lookup #1）
→ require FailedGraphNode
→ call _admit_override_resume_input
  → encode resume input
  → decode resume input → require materialization plan（lookup #2）→ admit frame
→ append ResumeFailedNode
→ write PendingGraphNode replacement
→ common construct/append AdmittedResumeInput from lookup #1 plan + returned frame
```

interrupt 分支顺序固定为：

```text
frontier_node lookup → reject unknown node
→ derive StableActivation
→ require materialization plan（lookup #1）
→ require InterruptedGraphNode
→ derive and validate exact interrupt ID
→ call _admit_override_resume_input
  → encode resume input
  → decode resume input → require materialization plan（lookup #2）→ admit frame
→ append ResumeInterruptedNode
→ write PendingGraphNode replacement
→ common construct/append AdmittedResumeInput from lookup #1 plan + returned frame
```

因此 executor-owned `_require_node_materialization()` lookup #1 仍早于 action-local settlement validation，decoder-owned lookup #2
仍发生在 encode 成功之后；codec encode/decode 仍晚于 settlement/interrupt-ID validation，accumulator mutation 仍晚于全部 codec/
frame admission。helper 不能构造 `StableActivation` / resume coordinate / `AdmittedResumeInput`，不能自行 append，也不能把
`actions`、`replacements`、`admitted_inputs` 作为参数。

failed `UseMaterializedInput` 路径保持原样：`UseStepRequestInput()` → existing `materialize_node_input(...,
failed_retry_input=binding)`。skip 路径保持原样。循环结束后的 `GraphFrontierState` simulation 与
`validate_graph_frontier(state, simulated)` 必须各保留一次，位置不前移、不后移。

### 4.4 S19 结构账本

| 结构 | Before | Target | Delta |
| --- | ---: | ---: | ---: |
| override encode call site | 2 | 1 helper-owned | -1 duplicate site |
| override decode/frame admission call site | 2 | 1 helper-owned | -1 duplicate site |
| common admitted coordinate/frame construction | 1 | 1 caller-owned | 0；不得复制进 helper |
| materialization lookup / override action | 2 | 2 | 0；executor coordinate owner + decoder declaration owner |
| `frontier_node()` / `StableActivation` derivation per action | 1 / 1 | 1 / 1 | 0；caller-owned |
| action-local settlement / interrupt-ID / skip validation owner | 3 nominal branches | 3 nominal branches | 0 |
| new method | 0 | 1 | +1 narrow typed owner |
| new nominal imports | 0 | 2 | +`NodeInputFrame` / `OverrideGraphNodeInput`；既有 owner，无新 type surface |
| new DTO / alias / callback / context / cache / field / export | 0 | 0 | 0 |
| final simulated frontier validation | 1 | 1 | 0 |

Target 探针为 `13 insertions / 5 deletions`（净增 8 行）；该数字只说明 Python typed signature 与两处 call formatting，不是源码行数
门禁。决定性净收益是 encode/decode pipeline owner 从两个 source sites 收敛为一个 two-consumer method，同时 branch、lookup、coordinate、
command、frame 与 public surface 均不增长。本表是人工可核对的零负债结构账本，不是 automated complexity gate。

### 4.5 S19 case-level behavior、shape/tamper 证据与 planned manifest

Baseline 必须逐项复跑，不能以文件级 pytest 代替 case 级登记：

| 义务 | Exact baseline `path::test_case` | Target 断言 / 失败条件 |
| --- | --- | --- |
| failed override、materialized retry、skip、interrupt success | `tests/execution/test_executor.py::test_resume_projection_covers_override_default_skip_and_interrupt_input_guards` | 四个 nominal variant 仍产生 exact command/input shape；任一 action/input 数量或 settlement 改变即失败 |
| action tuple lifecycle/canonical/scope/unknown | `tests/execution/test_executor.py::test_resume_projection_validates_each_action_variant_and_lifecycle` | 原 `SnapshotMismatchError` 分类/文本保持，codec 与 accumulator 不得越过 validation |
| wrong settlement variants | `tests/execution/test_executor.py::test_resume_rejects_non_failed_and_invalid_input_variants` | failed/interrupt/skip 的 action-local first error 保持 |
| public mixed failed retry + override | `tests/execution/test_graph_api.py::test_failure_resume_actions_are_canonicalized_and_share_run` | command action canonical order及最终 `initial|override` 保持 |
| public interrupt identity | `tests/execution/test_graph_api.py::test_interrupt_resume_is_an_exact_action_inside_run` | stale ID 原错，valid ID 输出 `approved` |
| encoder exact bytes / exception | `tests/execution/engine/test_resume_input_contract.py::test_resume_encoder_requires_exact_bytes`；`tests/execution/engine/test_resume_input_contract.py::test_resume_encoder_exception_is_normalized_at_admission` | error type/text/cause boundary 保持 |
| decoder exception / nominal-owner result | `tests/execution/engine/test_resume_input_contract.py::test_resume_decoder_exception_is_normalized_before_state_mutation`；`tests/execution/engine/test_resume_input_contract.py::test_resume_decoder_must_return_graph_values` | mutation 前失败；非 `Graph.Values` 继续拒绝；owner-produced `Graph.Values` 仍必须继续经过 compiled descriptor admission |
| failed retry State/frontier guard | `tests/execution/engine/test_resume_input_contract.py::test_failed_retry_materialization_requires_a_current_failed_node` | failed retry 的 State settlement guard 不被 override helper吸收 |
| public strict typing | `tests/execution/test_graph_public_typing.py::test_graph_namespace_exposes_precise_public_execution_errors`；`tests/architecture/test_graph_typing_fixtures.py::test_invalid_public_generic_programs_remain_rejected[cross_universe_resume_action.py]` | public errors 与 cross-universe resume typing 均保持 |

与 production 同一 implementation unit 必须新增：

```text
tests/execution/test_executor.py::test_override_resume_admission_preserves_validation_and_codec_order
```

该 target case 使用 typed tracking codec，只断言 public/internal-owner behavior：wrong settlement 与 stale interrupt ID 失败时 encoder/
decoder 调用记录为空；valid failed override 与 valid interrupt override 各保持 `encode → decode` 一次且只在 validation 后发生；返回的
`PreparedResume.command`、input coordinate/frame 与最终 frontier validation 结果保持。case 内还必须加入以下两个通过同一 valid failed
override validation 后执行的 typed decoder tamper subcase：

- decoder 返回 owner-produced `Graph.values(other="input")` 时，抛出 `GraphValueAdmissionError`，exact message 为
  `node input names do not match the compiled descriptor: expected ('value',), got ('other',)`，且 `__cause__ is None`；
- compiled descriptor 要求 exact `str`、decoder 返回 owner-produced `Graph.values(value=True)` 时，抛出
  `GraphValueAdmissionError`，exact message 为 `node input value for 'value' does not have its exact declared type`，且
  `__cause__ is None`。

两个 tamper subcase 均必须记录 validation 后恰好一次 `encode → decode`，不得返回 `PreparedResume`，并证明输入的 authoritative
`request.state` 与 `request.frames` 在失败后保持对象和值不变。测试不得直接读取或冻结 invocation-local `actions`、`replacements`、
`admitted_inputs` 的中间形状；这些 accumulator 在 frame admission 后才更新的顺序由第 4.3 节 exact caller order 与 actual production
diff review 证明。禁止断言 helper 名、helper 调用次数、AST、源码行数或局部变量；codec 调用记录属于本项明确保护的外部 callable
时序，不是 private-source-shape gate。上述 subcase 保持在同一 planned nodeid 内，因此 target 仍只新增一个 case，full-suite target
仍为 `834 passed`。

S19 不删除 public、State、command、frame、DTO 或 dataclass shape，因此 public exact-shape deletion gate 为 `N/A`；理由不是豁免
`GSP-A06`，而是 target 只去除两个 private duplicate call sites。替代证据固定为：strict Pyright、上述 command/input nominal
assertion、malformed codec/tamper cases、actual diff 人工结构账本和 production manifest negative diff。requirements owner 必须在对
本文 reviewed SHA 的 S19 `GSP-A06` 裁决中显式接受该 applicability；本文不得自批准，也不得新增 legacy/private-source-shape test。

S19 implementation unit 的最大 manifest：

```text
src/mote_kernel/execution/executor.py
tests/execution/test_executor.py
```

`test_graph_api.py`、`engine/resume_input.py`、State、request/result/run_context、facade 和 invocation 必须零 diff。若 target behavior
只能通过修改上述文件才可证明，S19 停止并重新评审，不临时扩大 manifest。

## 5. S21 exact audit：KEEP / no production implementation

### 5.1 当前 lifecycle 只有一个 owner

`Graph.run()` 当前按固定顺序完成：

1. 构造并验证 `ExecutionLimits`；
2. admission public invocation shape：new values 或 state invocation；
3. 编译 graph family 并建立 scoped executors；
4. new path：admit graph input → start exact-confirm → 建立 context → 安装 graph-input frame；
5. state path：state-only substitution precheck → 建立/恢复 context → 完整 validate → lineage/fence/resume planning → recovery preflight
   → fence/resume confirmation；
6. 两路汇合后只调用一次 `drive_root()`，再只调用一次 `project_graph_result()`。

三个 public overload 与一个 implementation 是同一 facade 的 typing surface，不是三套 runner。new/state/continuation 分支共享的只是
相邻控制位置与局部变量名；它们没有第二份相同 producer、consumer、scan、commit loop 或 frame installation 可以删除。

### 5.2 拒绝的候选

| 候选 | 裁决 | 原因 |
| --- | --- | --- |
| `_run_new()` / `_run_state()` | 拒绝 | 形成两个 private lifecycle runner，并扩大 owner surface |
| `_admit_invocation()` 仅返回 `_GraphValues | GraphRunState` | 拒绝 | 只把现有短分支搬到 helper，零重复删除且增加 wide dispatch boundary |
| `_RunContext` / `_RunAdmission` DTO | 拒绝 | 镜像 values/state/continuation/resume/limits/commit，形成 mutable 或 forwarding context bag |
| enum/string kind dispatcher | 拒绝 | 用 discriminator 替代现有 nominal `_GraphValues` / `GraphRunState`，违反 strict internal boundary |
| 把 compile/drive/project 移入 helper | 拒绝 | helper 成为第二 runner，`Graph.run()` 不再是可审计 lifecycle owner |
| 合并三个 overload | 拒绝 | 破坏 public strict typing 与调用者可见 contract |
| 把 state recovery path 移入 `invocation.py` | 拒绝 | invocation 将拥有 commit callback 与 public lifecycle，逆转 facade ownership |

### 5.3 S21 closure evidence 与 `GSP-A06` applicability

S21 的 exact target 是保持当前 production。设计评审需确认：

- 三个 overload 逐字保持；
- `_MissingRunValues` sentinel、new/state admission error type/text/order 保持；
- compile 仍晚于 limits 与 invocation-shape admission，早于 new/state execution mechanics；
- `Graph.run()` 中仍只有一次 `drive_root()` 和一次 `project_graph_result()`；
- 没有新 runner、context、DTO、dispatcher、alias、callback 或 export。

人工结构账本全部为 `0 → 0`：production/test changed files、helper、DTO、field、property、type alias、protocol、export、branch、scan、
cache 和 runner 均不增不减。S21 不删除 shape，也不实施 target shape，因此 exact-shape/tamper target test 为 `N/A — KEEP`；替代
evidence 是第 1 节 `facade.py` SHA、空 actual manifest、strict Pyright、三个 overload 的 public calling behavior，以及：

- `tests/execution/test_graph_api.py::test_graph_is_the_single_public_execution_facade_and_runs_plain_node_outputs`
- `tests/execution/test_graph_api.py::test_run_rejects_resume_without_state_and_unsupported_resume_variants`
- `tests/execution/test_graph_api.py::test_run_rejects_a_continuation_bound_to_another_root_state`
- `tests/execution/test_graph_public_typing.py::test_graph_namespace_exposes_precise_public_execution_errors`
- `tests/architecture/test_graph_execution_ownership.py::test_graph_facade_delegates_private_runtime_orchestration`
- `tests/architecture/test_graph_typing_fixtures.py::test_invalid_public_generic_programs_remain_rejected[cross_universe_resume_action.py]`

S21 implementation manifest 为空；不创建空提交，不改测试，不以 legacy/private-shape test 固化当前源码。独立 technical review 只能确认
上述 `N/A — KEEP` evidence 是否闭合，不能批准 production change；requirements owner 必须对本文 reviewed SHA 显式裁决
在 requirements owner disposition 之前，本文和 review 均不得自行追认 `GSP-A06`，也不得把 probe/空 diff 写成 `IMPLEMENTED` 或声称获得结构下降；当前 disposition 见第 12 节。

## 6. S22 exact audit：KEEP / no production implementation

### 6.1 当前表面重复与必须保留的差异

state path 先得到 canonical `fences` 和 `planned_resumes`，随后按“全部 fences → 全部 resumes”执行两个循环。两循环表面重复：

- 从 `context.state_at(scope_run)` 取得当前 acknowledged State；
- 调用 existing `commit_transition(scope_run, current, command, None, commit)`；
- commit/install 失败时：`confirmed_prefix is False` 原异常直抛；为 `True` 才用当前 root State、当前 continuation、原 cause、
  失败 scope 构造 `_PartialCommitError`，并保持 `raise ... from cause`；
- 成功后才把 `confirmed_prefix` 置为 `True`。

差异必须保留：

| 行为 | Fence | Resume |
| --- | --- | --- |
| command | `planned.command` | `planned.prepared.command` |
| post-confirm frame work | 无 | `install_confirmed_resume_frames(context.frames, planned, confirmed)` |
| `replace_state` 所在 exception boundary | commit try-block 之后；replace error 不包装 partial handoff | 与 commit/install 同一 try-block；replace error沿用现有 partial-prefix规则 |
| in-memory 安装 | 只替换 State | 先预计算 frames，再替换 State，最后替换 frames |

这些不是一个 callback 参数即可抹平的偶然差异；它们共同编码 current transaction error boundary。尤其 fence `replace_state()`
发生在 try-block 外，而 resume 的 frame pre-install、State replacement 和 frames replacement 位于同一个 try-block 内。把两个 plan
加宽后再 dispatch，只会把显式的两个 nominal loops 变成 helper 内的第二套 variant interpretation。

### 6.2 被否决 target 的 strict typing 与净结构证据

首次方案要求 facade 直接 import invocation-owned `_PlannedFence` / `_PlannedResume`。同 package strict Pyright 探针得到两个
`reportPrivateUsage` errors；当前 `invocation.__all__` 为空，architecture owner evidence 也固定两个 type 只由 invocation 定义。
因此原 exact target 不可实施，不能用 suppression、`Any`、`object` 或字符串 discriminator 绕过。

评审整改时还验证了最窄的替代方案：仅把两个既有 private type 加入 `invocation.__all__`，不重命名、不新增 alias，再实现原
helper。该探针确实达到 Pyright `0 errors`，且当前 `tests/execution/test_graph_api.py` 为 `73 passed`；但这只证明语义候选可运行，
不证明它是简化。人工结构账本如下：

| 结构 | Current | 显式 internal export + helper 候选 | Delta / 裁决 |
| --- | ---: | ---: | --- |
| duplicated exact commit sequence | 2 | 1 | -1 |
| duplicated partial-prefix handoff block | 2 | 1 | -1 |
| top-level confirmation helper | 0 | 1 | +1 |
| invocation → facade explicit plan-type exports | 0 | 2 | +2；扩大跨模块 type surface |
| helper nominal plan dispatch | 0 | 3 | +3；command/resume-install/fence-install |
| caller ordered loops | 2 | 2 | 0 |
| State/frame/persistence owner | 现状 | 现状 | 0 |

候选 diff 还会形成 `53 insertions / 45 deletions`（净增 8 行）；该数字只作设计探针记录，不是源码行数门禁或批准依据。决定性
证据是：为删除两段 mechanics duplication，需要增加一个 helper owner、两个显式 type ports 和三处 variant dispatch，真实接口/
决策面净增长。移动 helper 到 invocation 又会让 invocation 拥有 commit callback 与 partial public handoff；新增 protocol/DTO/callback
或提升 plan types 为公共入口也违反 owner 与零负债边界。因此 S22 exact target 收敛为 `KEEP`。

### 6.3 必须保持的 exception 与 in-memory 时序

| 场景 | Current / KEEP 必须保持的结果 |
| --- | --- |
| 第一个 fence commit 失败 | 原 exception identity/type/message/cause 直抛；无 `_PartialCommitError`；context 未替换 |
| fence exact-confirm 成功 | 先返回 confirmed State，后在 try-block 外替换 context State；frames 不赋值 |
| 第一个 resume commit 失败且无 fence prefix | 原 exception 直抛；无 partial handoff |
| 第一个 resume non-exact confirmation | existing `SnapshotMismatchError` 原样直抛；context State/frames 未替换 |
| resume commit 成功、frame pre-install 失败、无 prefix | existing installer exception 原样直抛；context State/frames 未替换 |
| 已有 prefix 后任一 commit/install/resume replace 失败 | `_PartialCommitError` 保存当前 root State、当前 continuation、原 cause identity 和 failed scope；`__cause__ is cause` |
| resume 全成功 | installer 读取旧 frames 和 confirmed State；完成预计算后 State 先替换，frames 后替换 |
| fence `replace_state` 自身失败 | 保持现状：发生在 exception handoff try-block 外，不包装 partial handoff |

最后一行虽不是正常 public path，却是当前明确的 exception boundary。不得用“内部不应失败”作为改变它的理由。resume 的
`replace_state` 当前位于 try-block 内，也必须继续受 partial-prefix handoff 规则覆盖。

首个 successful fence 已经是 confirmed prefix；后续第一项 resume 失败必须交付 fenced continuation。首个 successful resume 也已经是
confirmed prefix；后续 scope 失败必须交付只含已成功 scope State/frames 的 continuation。任何提前设置 prefix、在 commit 前替换 State、
边 commit 边写 frames、或失败后回滚内存 snapshot 都改变现有 contract，必须停止。

### 6.4 S22 closure evidence、shape/tamper applicability 与 manifest

S22 的 production/test changed-file manifest 为空；helper、type export、alias、protocol、DTO、callback、branch、State/frame owner 和
runner 全部 `0 → 0`。不创建空提交，不新增 fault-injection target test。以下 existing cases 保持 transaction/tamper evidence：

- `tests/execution/test_graph_api.py::test_multi_scope_resume_keeps_first_confirmed_install_when_second_commit_fails`
- `tests/execution/test_graph_api.py::test_multi_scope_resume_keeps_first_install_when_second_confirmation_is_non_exact`
- `tests/execution/test_graph_api.py::test_second_scope_frame_install_failure_hands_off_only_the_first_installed_scope`
- `tests/execution/test_graph_api.py::test_root_resume_then_child_commit_failure_hands_off_a_pairable_latest_root_snapshot`
- `tests/execution/test_graph_api.py::test_first_resume_scope_failure_propagates_original_error_without_partial_handoff`
- `tests/execution/test_graph_api.py::test_failure_after_exact_fence_explicitly_hands_off_the_fenced_snapshot`
- `tests/execution/test_graph_api.py::test_first_fence_failure_propagates_original_error_without_partial_handoff`
- `tests/execution/test_graph_api.py::test_same_scope_resume_input_and_substitution_install_as_one_frame_snapshot`
- `tests/execution/test_graph_api.py::test_normal_resume_never_mutates_the_input_continuation_snapshot`
- `tests/execution/test_graph_api.py::test_shared_input_continuation_is_not_modified_by_independent_invocations`
- `tests/execution/test_graph_api.py::test_run_requires_exact_authoritative_commit_confirmation`
- `tests/architecture/test_graph_execution_ownership.py::test_graph_facade_delegates_private_runtime_orchestration`

S22 不删除或替代任何 shape，故 target exact-shape test 为 `N/A — KEEP`；tamper applicability 由 non-exact confirmation、frame install
failure、partial-prefix continuation pairing 与 owner test 覆盖。requirements owner 必须对本文 reviewed SHA 显式裁决
在 requirements owner disposition 之前，probe 通过、review 通过或空 diff 均不得写成 `IMPLEMENTED`；当前 KEEP disposition 与 writeback 见第 12 节。

用户的“不实现错误恢复”在 S22 上采用最严格解释：本轮不触碰 production recovery confirmation code。现有 fence、resume、
partial-prefix handoff 只是冻结的 baseline 语义，不是本文新增或重构的恢复能力；任何相关 production diff 均使 S22 停止。

## 7. Requirement applicability

本表只映射 requirements ID，不复制 requirement 正文：

| Requirement | S19 | S21 | S22 |
| --- | --- | --- | --- |
| `GSP-P01` public facade/typing/error | resume public behavior保持 | 三 overload 与 invocation errors `HARD KEEP` | partial error type/fields/cause保持 |
| `GSP-P02` State authority | **适用**；`OverrideGraphNodeInput` command binding shape、codec identity/version、State revision 与 reducer projection 逐字保持；helper 不构造 command、不修改 State | 不触及 / `HARD KEEP` | 不触及 / `HARD KEEP` |
| `GSP-P03` commit/admission ordering | codec admission 早于 accumulator mutation | lifecycle 顺序 `KEEP` | 核心：exact-confirm/prefix/error timing保持 |
| `GSP-P04` Result/Continuation/frame | admitted input coordinate/frame保持 | `KEEP` | partial continuation 与 frame install order保持 |
| `GSP-P05` routing/publication | skip branch `KEEP` | `KEEP` | substitution frame installation保持 |
| `GSP-P06` recovery | 只去重既有 resume value admission；preflight/recovery seed/runner 零 diff | 不触及 / `KEEP` | production recovery confirmation 零 diff / `KEEP` |
| `GSP-P07` canonical order/nesting | action tuple order保持 | family lifecycle order保持 | fences→resumes 与 scoped order保持 |
| `GSP-P08` owner/typing | 完整 generic、一个 executor method | Graph 唯一 owner、零新增 | invocation private types不 export、facade零 helper、无第二 runner |

### 7.1 Per-unit `GSP-A06` closure matrix

| 义务 | S19 | S21 | S22 |
| --- | --- | --- | --- |
| exact target signature / nominal I/O | 第 4.2 节唯一 method signature；输入 `GraphNodeId` + `OverrideNodeInput[GraphValueT]`；输出 `tuple[OverrideGraphNodeInput, NodeInputFrame[GraphValueT]]` | `N/A — KEEP`；三个 overload和 implementation逐字保持 | `N/A — KEEP`；两个 transaction loops逐字保持 |
| 删除对象 | 两份 encode/decode pipeline 中的一份 mechanics owner；公共 admitted-coordinate construction不删除 | 无 | 无；原 helper candidate撤销 |
| 最多新增对象 | 一个 executor private method；零 DTO/alias/export/cache/branch owner | 零 | 零 |
| 净结构证据 | 第 4.4 节：两个 duplicate mechanics sites各减一，一个 two-consumer method新增；lookup/branch/coordinate/State-command/frame owner不增 | 全部 `0 → 0` | 第 6.2 节证明候选净增长，故 target 收敛为全部 `0 → 0` |
| 成功/失败/边界 characterization | 第 4.5 节 exact nodeids + 一个 behavior target case | 第 5.3 节 existing public/owner/typing nodeids | 第 6.4 节 existing transaction/tamper/owner nodeids |
| exact-shape/tamper applicability | 无 shape deletion；由 command/input nominal assertions、codec exception/non-owner result、owner-produced decoded values 的 descriptor name/exact-type tamper、strict typing、manual actual diff替代；禁止 legacy source-shape test | `N/A — KEEP`；SHA + empty manifest + existing behavior | `N/A — KEEP`；empty manifest + non-exact/frame/partial tamper behavior |
| changed-file manifest | `executor.py` + `test_executor.py` | empty | empty |
| requirements owner required disposition | reviewed SHA 的 S19 explicit `GSP-A06 APPROVED` 后才能实施 | explicit `SATISFIED / CLOSED — KEEP` | explicit `SATISFIED / CLOSED — KEEP` |

`N/A` 不自动豁免 `GSP-A06`，也不要求修改 requirements 的全局文字。requirements owner 对第三次 review 所绑定本文 SHA 逐项
接受 applicability，即构成可审计范围裁决；这是批准前的停止条件，若 owner 不接受，单元继续 `NOT APPROVED`，不得通过新增 legacy/private-shape gate
或复制 requirements 来补票。

## 8. 原子实施顺序、审批与回滚

1. 冻结第 1 节 production/behavior SHA；任一目标文件漂移即重新审计并重绑本文 SHA。
2. 对本文做独立技术评审，逐项验证 exact signatures、error precedence、typing 和 zero-debt ledger。
3. requirements owner 分别裁决 S19、S21、S22；不得一次批准文本模糊覆盖三个单元。
4. S19 获批后，以第 4.5 节 manifest 独立实现、验证、提交和 owner writeback。
5. S21 获批后只做 requirements/owner 状态 writeback；production/tests 为空，不创建空 implementation commit。
6. S22 获 requirements owner 明确接受 `KEEP` 后只做 owner 状态 writeback；production/tests 为空，不创建空 implementation commit。
7. 任一单元未获相应裁决即保持现状；不得恢复 compatibility path、双写或用后续单元修复前一单元。

本轮只有 S19 production implementation unit。S21/S22 的 docs-only owner writeback 与 S19 production/acceptance 仍是独立 change
units；禁止把 requirements approval、production implementation、KEEP closure 和独立 acceptance 混成不可审计的单一状态。

## 9. 实施门禁

### 9.1 固定门禁状态与 coverage 口径

```text
AUTOMATED COMPLEXITY / BASELINE / RATCHET: USER-EXCLUDED / NOT RUN
LEGACY / PRIVATE-SOURCE-SHAPE GATE: USER-EXCLUDED / NOT RUN
CURRENT BEHAVIOR / TYPING / OWNER / LINT / FORMAT / COVERAGE / PACKAGE: REQUIRED
STATE / PERSISTENCE / ERROR-RECOVERY FEATURE IMPLEMENTATION: OUT OF SCOPE / ZERO DIFF
```

Coverage baseline 是第 1 节同一源码 SHA 下、排除整个 `tests/architecture/test_complexity_gate.py` 后的 full suite：`833 passed`、
line/branch `100.00%`。S19 只新增第 4.5 节一个 target case，因此 target 是其余收集面不变、`834 passed`、line/branch
`100.00%`；不得以不带 coverage 的 scoped pytest 冒充 full-suite coverage。S21/S22 无 production/test diff，只复用 baseline，
不制造 target test count。

### 9.2 S19 implementation unit 必须通过

- baseline SHA 与 actual manifest 核对；无用户其他改动被吸收；
- 第 4.5 节全部 exact nodeids 与 target case 全通过；
- strict Pyright 对 actual production/test manifest 零错误；
- Ruff lint 与 format check；
- full non-complexity suite 为 `834 passed` 且 line/branch coverage `100.00%`；
- package build/import 与 public typing fixtures；
- `git diff --check`；
- State、persistence、facade、invocation、family driver、result、run_context、protocol 和非本单元文件均为零 diff；
- monorepo pre-commit 中除用户明确排除的 automated complexity 与 legacy/private-shape 项外，其余适用 hooks 全通过。

可复现命令固定为：

```bash
python -B -m pytest \
  tests/execution/test_executor.py \
  tests/execution/engine/test_resume_input_contract.py \
  tests/execution/test_graph_api.py \
  tests/execution/test_graph_public_typing.py \
  tests/architecture/test_graph_execution_ownership.py \
  tests/architecture/test_graph_typing_fixtures.py \
  -q --tb=short -p no:cacheprovider
pyright src/mote_kernel/execution/executor.py tests/execution/test_executor.py
python -B -m ruff check src/mote_kernel/execution/executor.py tests/execution/test_executor.py
python -B -m ruff format --check src/mote_kernel/execution/executor.py tests/execution/test_executor.py
python -B -m pytest \
  --ignore=tests/architecture/test_complexity_gate.py \
  --cov=mote_kernel --cov-report=term-missing
make package-check
git diff --check
```

`make package-check` 复用仓库 `python -m build --no-isolation` + `twine check dist/*`，不另写 isolation/network 口径。仓库工程规则仍
要求记录 `make check` 与 monorepo-root pre-commit：由于当前 `make check` 内嵌被排除的 `complexity-ratchet`，其整体不得冒记为通过；
若运行后在该子目标停止，必须按上表单独完成 lint、typecheck、full coverage tests 和 package-check。monorepo pre-commit 必须运行
全部适用非排除 hooks；若 actual S19 files 触发 `kernel-complexity`，以 hook 的标准 skip 机制仅跳过该 ID，并把 skip 记录为
`USER-EXCLUDED / NOT RUN`。不得因跳过该项而跳过 detect-secrets、whitespace、Ruff 或其他适用 hooks。

### 9.3 S21/S22 docs-only closure gate

- 第三次独立 review 绑定修订后本文 SHA256，并逐项确认 empty production/test manifest；
- `git diff --check` 与对本文/回复文件的 monorepo pre-commit 非排除 hooks 通过；
- 第 5.3、6.4 节 exact baseline nodeids 可复现；scoped baseline 仍为 `160 passed`；
- `facade.py` / `invocation.py` SHA 与第 1 节一致，`invocation.__all__` 仍为空，execution public export 仍只有 `Graph`；
- requirements owner 分别记录 `SATISFIED / CLOSED — KEEP`；不创建空 implementation/acceptance commit。

### 9.4 明确排除的门禁

- `tests/architecture/test_complexity_gate.py`、complexity baseline/ratchet/health/limit 及对应 Make/pre-commit target；
- 任何只冻结 private helper 名称、源码行数、AST 数量、局部变量或旧 private execution path 的 legacy test；
- 持久化/Store backend 测试和新 error-recovery feature 测试，因为本轮不实现这些能力。

排除 automated complexity 不排除第 4.4、6.2 节的人工结构账本；排除 legacy test 不排除 public behavior、strict typing、
State authority、transaction ordering 和 continuation integrity。

## 10. 停止条件

任一条件成立即停止对应单元，保持 production 不变并重新评审：

- S19 helper 需要读取 settlement/State/request/frames 或接收 accumulator/context bag；
- S19 无法保持 materialization-plan、settlement、interrupt-ID、codec 与 frontier validation 的现有首错顺序；
- S19 改变 `OverrideGraphNodeInput`、`ResumeGraphNodes`、codec identity/version、State revision 或 reducer projection；
- S21 出现任何 production diff，或候选 private path 调用 compile/commit/drive/project；
- S22 出现任何 production/test diff，包括 helper、private plan import/export、`invocation.__all__`、alias/protocol/DTO/callback 或
  transaction loop 改写；
- 首次失败不再原异常直抛，或已有 prefix 后 handoff 的 state/continuation/cause/scope 任一变化；
- resume frame 未在 State replacement 前完整预计算，或成功安装顺序不是 State → frames；
- actual manifest 触及 State、persistence、family driver、result、run_context、protocol 或新增执行入口；
- strict typing、public behavior、coverage、lint、format、build、applicable pre-commit 任一失败；
- 需要 compatibility alias、双写、临时旧路径或 legacy/private-shape test 才能通过。

## 11. 完成定义

本文现已完成三个尾项的 exact disposition 设计、implementation、验收与 implementation-owner writeback：

- S19 有一个可证明等价、完整泛型、无 context bag 的唯一 admission target；
- S21 明确关闭伪重复，保持 `Graph.run()` 唯一 lifecycle owner且零 production diff；
- S22 以 strict typing + owner + 净结构探针否决 confirmation helper，保持现有 fence/resume transaction loops 且零 production diff；
- 三项共享唯一设计真相，但 approval/closure 相互独立；只有 S19 具有 implementation manifest；S21/S22 保持空 manifest，不创建空提交；
- 不实现持久化或错误恢复能力，不消费 automated complexity gate，不新增 legacy/private-shape gate；
- S19 的实际变更与 focused verification 见下节；S21/S22 的 production/tests 继续保持基线状态。

## 12. Implementation-owner writeback（2026-08-25）

### 12.1 Per-unit disposition 与实际 manifest

本节 implementation-owner writeback 只记录对已批准 reviewed design SHA `07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9` 的实际结果，不扩大
S19 target、manifest 或任何 S21/S22 disposition。

| 单元 | 当前状态 | 实际 production/test manifest | 说明 |
| --- | --- | --- | --- |
| S19 | `IMPLEMENTED / VERIFIED` | `src/mote_kernel/execution/executor.py`；`tests/execution/test_executor.py` | 仅把 failed override 与 interrupt override 的重复 `encode → decode/frame admission` 收敛到 executor 内一个窄 typed method；公共坐标/State/command/frame owner 不变；production commit `2709a59` |
| S21 | `CLOSED / KEEP` | 空 | `Graph.run()` lifecycle owner 保持唯一；不新增 helper、runner、DTO、dispatcher 或测试 |
| S22 | `CLOSED / KEEP` | 空 | fence/resume/partial-prefix transaction loops 保持原样；不提取 confirmation helper，不改 recovery production/test |

本次 S19 implementation unit 没有吸收工作树中的其他用户改动；`facade.py`、`invocation.py`、`engine/resume_input.py`、`run_context.py`、
State、Store、protocol、persistence、family driver、result 与任何 public export 均为零 diff。S21/S22 没有 production/test diff。

实际 source SHA256：`src/mote_kernel/execution/executor.py`
`d967415f746e72043be73a6d31bbf74386aa26f7bfa9d885eb9bf54abac2131b`；
`tests/execution/test_executor.py`
`8e4ac64c07d013dad4981d9a808d060d44e237d492a525fd76537301ef28a597`。

### 12.2 实际结构账本与行为证据

- override encode call sites：`2 → 1`；override decode/frame-admission call sites：`2 → 1`；新增一个 executor private method。
- common `AdmittedResumeInput` coordinate/frame construction、两次 materialization lookup、frontier/settlement/interrupt-ID/skip
  validation、simulated frontier validation：均保持 `1 → 1` 或 `2 → 2`，没有新增 DTO、alias、protocol、cache、field、export、branch owner。
- 既有 `tests/execution/test_executor.py` 新增唯一 nodeid
  `test_override_resume_admission_preserves_validation_and_codec_order`；其中覆盖 wrong settlement、stale interrupt ID、两条 valid
  override 路径、wrong-name 与 wrong-exact-type decoded `Graph.Values` tamper、exact error/cause、codec 顺序及 State/frame 不变性。
- 未新增 legacy/private-source-shape、AST、helper-name、源码行数或 complexity 门禁测试。

### 12.3 本轮已执行验证

```text
target test: 1 passed
tests/execution/test_executor.py: 38 passed
six-file focused owner/behavior/typing run: 161 passed
Pyright (src/mote_kernel): 0 errors, 0 warnings, 0 informations
Ruff check: passed
Ruff format --check: passed
git diff --check: passed
```

本轮 S19 focused implementation evidence 已通过；automated complexity/legacy/private-source-shape gate 仍按批准范围
排除，不把其结果写入 S19 证据。S19 之外的仓库级完整门禁由当前 change set 统一执行并单独记录，不改变 S19 的 two-file manifest。
