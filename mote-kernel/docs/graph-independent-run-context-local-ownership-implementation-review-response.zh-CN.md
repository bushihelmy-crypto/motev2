# 父子图 GraphRun 本地 ownership 实施方案继续独立评审回复

> **回复结论：10 项 blocker 的有效核心均已吸收，修订 target 已可重新评审。**
> 本回复同时拒绝把整改扩张为 persistence、跨 invocation child-ID recovery、opaque
> continuation slot、per-run recovery protocol、public/result type migration或global
> admission guard。本轮只修改 docs，production/tests 未改。

## 1. 回复对象与修订输入

- 被回复评审：[父子图 GraphRun 本地 ownership 实施方案继续独立评审](graph-independent-run-context-local-ownership-implementation-review.zh-CN.md)
- 修订 requirements：[GRC-LO-001 本地 ownership 拆分需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
- requirements SHA256：`8a91cf520650fd127d756aab311714fc084e42e41f76723c56b0df6065b96a1e`
- 修订 target：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
- implementation target SHA256：`d99e367637390757a1c87a6cbe3541612c1710e1abab8087f375026c82fec470`
- production baseline：`ebcd043fdfe324c610328a08cb1a3e8a14b37e10`
- 回复日期：2026-08-28

评审没有被改写。本回复按其提供的窄整改分支处理：现有 observable contract 能保留的
全部保留，只调整 runtime mutable ownership。

## 2. 总体裁决

评审对“旧 target 内部自相矛盾”的证据成立，尤其是：

- pure identity 被误写成 admission guarantee；
- opaque continuation slot 与当前 sealed snapshot冲突；
- `ChildProjection` KEEP 与删除 `child_state` evidence冲突；
- family recovery proof被无定义地改成per-run proof；
- `_GraphRun` lifecycle、cancellation、partial handoff和limits没有闭合；
- manifest漏掉真实private consumers。

整改不采用评审中可能扩张范围的另一分支。最终选择是：

```text
runtime mutable ownership
  -> split: each _GraphRun owns and advances only its own state/frames

sealed continuation/recovery evidence
  -> keep: root + ChildStateBinding tuple + family ScopedFrameIndex

parent preparation/result
  -> keep: existing ChildProjection / StepRequest / frontier / Graph.Result
```

这里的 `ChildStateBinding`/`ChildProjection.child_state` 都是 child owner输出的immutable、
transient、read-only evidence，不是parent authoritative state mirror。

## 3. Blocker逐项回复

### LO-R1 — 接受核心问题；拒绝隐式 admission guard

**接受。** `child_graph_run_id()`只能证明tuple投影injective，不能证明相同explicit root
`run_id`的第二次fresh invocation会被拒绝。Requirements和target已统一为：

- fresh same-root-ID reuse是caller/authoritative commit owner precondition；
- 不承诺runtime检测或拒绝；
- 不再把“伪装不同但复用同一parent ID必须fail closed”放进 `LO-B02`；
- `LO-B02`只验证different-parent-ID pure projection、same-tuple stability和现有mismatch
  validation。

**拒绝的扩张。** 本target不新增admission registry/global lock/definition guard。评审自己
给出的第一条窄整改路径已足以关闭该blocker；第二条guard requirement不是本需求的一部分。

关闭位置：requirements §4.3/§5.4/§6/§7；target §1.2/§9/§13 `LO-B02`。

### LO-R2 — 接受

旧target的“若冲突以前者为准”确实有歧义。修订target不再排列可互相覆盖的事实优先级，
而是按owner分工：

- normative source拥有现有behavior；
- requirements只拥有新增scope/acceptance；
- target只拥有implementation plan/manifest/tests；
- 有意改变现有behavior必须停止并另立同一change unit，不允许target覆盖。

关闭位置：target §2。

### LO-R3 — 接受contract冲突；选择保留exact snapshot

**接受。** 旧target提出的opaque slots、terminal slot disposal和continuation fork没有现行
nominal protocol，且会改变malformed admission、terminal retention和family frame行为。

修订target选择评审给出的第一条窄路径：

- `_CompleteContinuationSnapshot.child_states` KEEP；
- `_RecoveredContinuationSnapshot.child_states` KEEP；
- family-wide `ScopedFrameIndex` export/admission shape KEEP；
- `ChildStateBinding`只作为immutable continuation/recovery evidence；
- no slot、no live capability、no fork API、no used flag；
- runtime可以在完整family validation后partition local frames，export时canonical merge，
  但observable snapshot exact shape/retention不变。

**拒绝的扩张。** 不定义sealed `ChildContinuationSlot`，因为本change unit无需改变
continuation ABI；新增该协议反而超出用户范围。

关闭位置：target §6/§7/§15。

### LO-R4 — 接受；保留现有projection/request/frontier

**接受。** 旧target一边把 `result.py/request.py/frontier.py`列为KEEP，一边又删除其所需的
child state evidence，无法实施；`ChildResult`也没有definition。

修订target作出唯一选择：

- `result.py` KEEP；
- `request.py` KEEP；
- `engine/frontier.py` KEEP；
- 复用 `MissingChild/ActiveChild/CompletedChild/AbortedChild`；
- `child_state`是child `_GraphRun`产生、parent prepare一次性消费的immutable validation
  evidence，不是parent可提交或替换的state；
- 删除target中的undefined `ChildResult`设计，不新增private/public宽DTO。

关闭位置：target §4.3 projection、§8、§14。

### LO-R5 — 接受；已冻结可执行private lifecycle

修订target新增并闭合：

- `_GraphRun`、`_ChildRunHandle`、invocation coordinator三个nominal roles；
- `start()`与`admit()`两个factory path；
- exact `confirm()`唯一memory replacement point；
- child creation/duplicate activation/canonical handle tuple；
- one-quantum drive、awaiting park、terminal projection、export retention；
- method-local session ownership和close顺序；
- no lifecycle enum/duplicate status、no second runner、no global registry。

Handle由invocation coordinator持有，不写入parent State/context。Terminal/historical handle
保留到continuation/result export，以保持当前snapshot行为。

关闭位置：target §4/§5/§10。

### LO-R6 — 接受proof缺口；保留family-shaped proof

**接受。** 旧target写“per-run seed + recursive opaque slots”却没有定义evidence、traversal、
order或availability merge，无法保持state-only pending child和grandchild的早期拒绝。

修订target选择评审给出的第一条窄路径：

- `engine/recovery.py` KEEP；
- `RecoveryInvocationSeed`和 `_RecoveryFamily.bindings` KEEP；
- whole-invocation preflight、canonical traversal/dedup/order和mutation-before-proof KEEP；
- family frame/child snapshot完整validate后，可构造detached local owners/partitions；任何
  state/frame mutation仍在proof通过后；
- proof之后planned scoped command才dispatch给对应 `_GraphRun`自己confirm。

**拒绝的推论。** 保留family-shaped proof不等于保留parent runtime state authority。
Recovery evidence的只读family envelope与runtime mutable owner是两个职责；无需为拆state
ownership发明per-run recovery protocol。

关闭位置：target §6.3/§7/§11 Step 4。

### LO-R7 — 接受；以现有closed types闭合结果投影

修订target不再使用 `ChildResult`。Child drive沿用existing boundary/disposition，parent只
接收existing `ChildProjection`；public result仍为当前三个closed variants。

Failure/interrupt view由coordinator读取root及child owners的immutable states，按canonical
`ScopeRunCoordinate`生成root first、child/grandchild afterward顺序。Awaiting child handle
与terminal/historical handles保留到snapshot/result export；foreign/duplicate/status mismatch
继续走existing validation。

关闭位置：target §4.3 projection、§5.2、§8、§12 producer/consumer。

### LO-R8 — 接受；生命周期、partial、cancellation和limits已裁决

修订target明确：

- start callback失败不产生run/handle/frame；
- ordinary `executor.execute()` exception保持fence → propagate；`session.next()` exception保持
  close/quiesce → fence → propagate；completion commit exception只close session，不猜测
  callback结果后自行fence；
- `CancelledError`先close/quiesce，但保留active token、直接传播，不自动fence或造continuation；
- child terminal → projection validation → parent settlement → routing顺序；
- parent settlement失败不重跑child、不造receipt、不扩张partial handoff；
- `_PartialCommitError`只保留现有fence/resume confirmed-prefix用途；
- 一个 `ExecutionLimits` instance原样传入all scopes；superstep按run检查、parallelism按
  scope/session解释，无aggregate family budget。

**拒绝的扩张。** 不为进程退出、callback unknown或runtime crash新增retry/replay/
failover contract。

关闭位置：target §4.4/§7.3/§10/§13 `LO-B08/B14`。

### LO-R9 — 接受；runtime overlap case完全移除

Requirements已把cross-parent overlap改为caller precondition，并明确不进入
acceptance/gate。Target删除原条件性 `LO-B17`，不再要求“检测到则fail closed”，也不再
写harness-dependent fixture。

Pure identity invariant单独留在 `LO-B02`，不用于证明overlap admission或concurrent
execution。

关闭位置：requirements §6/§7；target §1.2/§9.3/§13。

### LO-R10 — 接受manifest缺口；拒绝错误的全局删除门槛

修订manifest由真实producer/consumer反向生成。

Production MODIFY仅：

```text
src/mote_kernel/execution/run_context.py
src/mote_kernel/execution/family_driver.py
src/mote_kernel/execution/facade.py
src/mote_kernel/execution/invocation.py
```

Production KEEP明确包含：

```text
execution/result.py
execution/request.py
execution/engine/frontier.py
execution/engine/recovery.py
execution/identity.py
execution/graph_run.py
executor/claim/session/scheduler/state/public exports
```

真实private test consumers已纳入：

```text
tests/execution/engine/test_runtime_boundaries.py
tests/execution/test_graph_api.py
tests/execution/test_continuation_integrity.py
tests/execution/test_frame_index_contract.py
tests/architecture/test_graph_execution_ownership.py
```

Behavior evidence允许新增/修改owner、identity、recovery、resource tests；
`tests/execution/driver.py`、`tests/architecture/test_source_discipline.py`、executor/recovery
boundary tests因其nominal contract保持而列为KEEP。

**拒绝的门槛。** `ChildStateBinding/child_states`不能做production全局归零，因为本次选择
保留canonical continuation ABI。正确的删除闭包是：从runtime `GraphRunContext`和
cross-scope mutation path移除，只允许存在于sealed continuation/recovery evidence边界。

关闭位置：target §6.4/§14/§15。

## 4. 不接受的范围扩张汇总

以下不是关闭本轮blocker所必需的内容，且明确不进入target：

| 扩张 | 裁决 | 理由 |
| --- | --- | --- |
| global/fresh-run/definition overlap admission guard | 拒绝 | caller precondition；无现有owner，需独立requirement |
| opaque child continuation slot/live capability/fork | 拒绝 | 改变current sealed snapshot且无必要 |
| per-run recovery seed/evidence | 拒绝 | current family proof可与runtime local ownership并存 |
| 修改ChildProjection/StepRequest/frontier | 拒绝 | immutable state evidence可复用，不是authoritative mirror |
| duplicate lifecycle enum/status field | 拒绝 | phase可从existing State/session method scope派生 |
| 扩张 `_PartialCommitError` | 拒绝 | 用户不要求runtime crash recovery/failover |
| persistence/Store/checkpoint/ID-only restore | 拒绝 | 用户明确留待后续单独设计 |
| overlap success或conditional rejection test | 拒绝 | 不属于runtime acceptance |

评审末尾的 `CODE NOT AUTHORIZED` 是流程状态，不是技术设计blocker。Target本身不替用户
授权，也不能由reviewer自行增加requirements-owner/user双重批准机制。本轮按用户当前
指令只完善docs；是否进入代码实施由用户的明确开发指令和最终窄manifest决定。

## 5. 回复后ledger

```text
LO-R1   = CLOSED: admission claim removed; pure identity retained
LO-R2   = CLOSED: source ownership unambiguous
LO-R3   = CLOSED: exact continuation snapshot retained
LO-R4   = CLOSED: existing projection/request/frontier retained
LO-R5   = CLOSED: private run/handle/coordinator lifecycle frozen
LO-R6   = CLOSED: family-shaped recovery proof retained
LO-R7   = CLOSED: existing closed result/projection path defined
LO-R8   = CLOSED: error/cancel/partial/limits semantics frozen
LO-R9   = CLOSED: overlap removed from runtime acceptance
LO-R10  = CLOSED: real consumer manifest complete

persistence / failover / ID-only restore = OUT OF SCOPE
production / tests                       = UNCHANGED IN THIS DOCS TURN
revised target                           = READY FOR RE-REVIEW
```

本回复请求下一轮只按修订后的窄contract复核：runtime中每个 `_GraphRun`是否只推进自己的
state，以及现有continuation/recovery/result behavior是否被原样保留；不得再以未授权的
persistence、failover或global admission能力作为通过条件。
