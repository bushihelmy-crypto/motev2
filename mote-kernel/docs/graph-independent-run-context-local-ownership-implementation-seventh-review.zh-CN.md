# 父子图 GraphRun 本地 ownership 实施规范第七次独立评审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮的修改方向已经明显收敛：`RUNNING + AWAITING_RESUME` handoff、三类取消谓词、
> child-owned identity、root collector、confirmed-prefix handoff 和 direct-consumer manifest
> 都比上一版完整。但是文档仍有若干会迫使实现者自行选择 owner、唯一 caller 或失败后事实的
> 缺口，尚未达到可以安全编码的唯一 typed contract。因此本轮不开始 production/test 编码。

本文件只做 docs-only 独立评审，不修改 requirements、production、State、Store、protocol、
public API 或 tests；不引入 persistence、failover、worker handoff、child-ID-only 跨
invocation recovery、overlap gate 或第二 runner。

## 1. 评审对象与基线

| 对象 | 内容 |
| --- | --- |
| implementation target | [父子图 GraphRun 本地 ownership 实施规范](graph-independent-run-context-local-ownership-implementation.zh-CN.md) |
| target SHA256 | `415bef2f66adef11fbda35367130891e27e7c64deda44d8176438ac5a556350d` |
| target 行数 | 1928 |
| requirements | [父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md) |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| 上一轮评审 | [第六次独立评审](graph-independent-run-context-local-ownership-implementation-sixth-review.zh-CN.md) |
| production 基线 | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| 评审日期 | 2026-08-29 |

评审口径仍是已冻结的窄范围：每个 `GraphRun` 独占自己的 `run_id`、state、transition、
commit、session 和 local frame；parent 不拥有 child authoritative state 或 child ID；当前
调用只用 opaque wait/abort handle；child 先完成/投影、parent 再结算；调用级取消沿当前
调用链 child-first，由各 owner 使用既有 `AbortGraphRun` 独立提交；typed failure 和
ordinary exception 不广播 sibling；State/reducer、result/request、continuation/frame ABI
和既有 owner-local Store 语义保持不变。

## 2. 本轮已确认的改进

- §3.2、§7.1、§8.2 已补出 `RUNNING + AWAITING_RESUME` 的 child-canonical record、relay、
  root export、release 和下一次 `from_snapshot()` admission 的方向，LO-IR25 的主要语义已
  被吸收。
- §3.1、§6.1 已给出 coordinator 的字段草图、root caller 图和 deepest-child-first 的
  组织方式，LO-IR26 从完全缺失改善为可审查的候选方案。
- §1.2、§3.3、§5.1、§8.3 已把 fresh child 的 identity 生成与 `StartGraphRun` 交接放入
  factory，并把 projector 改写为结构校验方向；LO-IR27、LO-IR28 的边界意图清楚很多。
- §7.3 已将 confirmed-prefix 独立成 `handoff_confirmed_prefix()`，并区分 ordinary error
  与既有 `_PartialCommitError`；§7.2 也已区分 standalone、nested/no-live-child、live-child
  三类取消。
- §3.2、§6.1 已规定空 relay batch 为合法 no-op，并写出 self-sink、immediate-parent relay
  和 root collector 的顺序；`Awaitable[None]` 已参数化，没有发现裸泛型问题。

这些是方向性通过，不等于可以编码。以下阻塞项是对当前 target 仍然存在的可执行性问题，
不是要求扩大用户范围。

## 3. 阻塞项

### LO-IR33 — coordinator 仍持有非 opaque 的 child 编排资料

requirements §3.5 明确规定：若存在 invocation coordinator，它只能持有 opaque handle 对象
本身，不能把 child `run_id`/`coordinate` 展开为 parent/coordinator 的编排字段。当前 target
§3.1 却把 `nested_cells`、`nested_factories`、`nested_entries` 作为 coordinator 字段，且
§3.2/§6.1 允许 coordinator 通过 cell、factory、entry 读取生命周期并执行 seal、relay、
release。`_ChildScopeCell(owner_scope)`、factory 捕获的 parent owner/cell 和 entry source
也不是 opaque wait/abort handle。

“没有 coordinate map”不能消除这个边界冲突：coordinator 仍拥有一组可操作的 child
生命周期引用，足以间接访问 child owner/evidence。若实现者照此编码，就会违反 requirements
中 coordinator 只能持有 opaque handle 的硬规则；若把这些字段继续保留为隐藏闭包，又无法
审查其是否构成第二编排 owner。

**必须修订：** 在不修改 requirements 的前提下，明确 coordinator 是否只保存 opaque handle
（并由 handle 完成允许的 wait/abort），以及 root cleanup、relay、export 所需的 owner-local
闭包由谁持有、谁调用；给出不可读取 child identity 的 nominal contract。不能用“无 map”或
“lexical tuple”替代 opaque 边界，也不能在实现阶段自行裁决。

### LO-IR34 — child drive caller 与递归 factory contract 仍未闭合

target 声明了 `_GraphRun.drive_quantum()`、`_OpaqueChildCallHandle.await_or_project()` 和
`_ChildScopeFactory.prepare_start()/admit_existing()`，但没有一条唯一、可调用的递归路径：

- 谁实际调用 child 的 `drive_quantum()`，`await_or_project()` 是推进一个 quantum、等待整个
  child，还是只做 projection，没有定义；
- `GraphBoundary` 如何转成 `ActiveChild`、`CompletedChild`、`AbortedChild`，以及
  `AWAITING_RESUME` 如何结束当前 invocation，没有 typed 转换契约；
- child 内再次出现 grandchild 时，谁创建 nested factory/cell/entry，如何把它注册到
  coordinator，parent-child 的递归 owner 关系如何保持，没有构造签名；
- §7.1 从 factory 直接跳到 child 执行和 parent settlement，缺少唯一的 session/quantum/
  projection caller。

仅有方法名和一张箭头图不足以保证不出现第二 runner 或 parent 代驱动 child。

**必须修订：** 冻结 handle 的唯一调用闭包、一个 quantum 的推进/等待语义、每种
`GraphBoundary` 的 typed 映射，以及 grandchild 的递归 factory/coordinator 构造与登记顺序；
说明每个 operation 的 owner 和唯一 caller 后才能编码。

### LO-IR35 — self-sink 与 awaiting cell handoff 的原子性/失败窗口未闭合

target §3.2/§5.2 同时涉及 child cell、parent-canonical cell 和 self-sink，但没有冻结
`seal_and_remove()`/`abort_awaiting_to_terminal_once()` 究竟作用于哪一个 cell，以及两次
pointer replacement 的线性顺序。特别是：

- `_SealedChildEvidenceSink` 注释称出错时 old pointer unchanged（§3.2），§6.1 又规定
  destination replacement 已发生但 callback 抛出未知异常时 evidence/alias 保留；两者对
  callback-after-replacement 的事实描述不一致；
- `abort_awaiting_to_terminal_once()` 先把 child-canonical awaiting source 替换为 terminal，
  再交给 parent self-sink；若第二步失败，terminal record 的 canonical owner、awaiting lease
  是否保留、后续 finalizer 是否还能处理均未定义；
- sink 成功而 entry remove 失败、entry remove 成功而 sink 失败、destination replacement
  成功但 callback 失败等窗口没有一个统一的 old/new pointer 与 caller-visible error 表。

跨两个 cell 的“原子”不能靠文字保证，也不能用本需求禁止的 persistence/retry 补齐。

**必须修订：** 给出每个 handoff 的单 cell replacement 边界、跨 cell 顺序、每个失败窗口的
last-exact state/evidence/entry 保留位置、canonical owner 和后续一次性处理；统一
`NOT_WRITTEN` 与 `UNKNOWN_AFTER_COMMIT` 的含义，确保不需要实现者猜测或新增协议。

### LO-IR36 — continuation ABI 文字与现有 nominal contract 直接矛盾

§8.1 声称 continuation ABI/exact shape 不变，但 §8.3 写成：

```text
_GraphContinuation.admit(...) -> ContinuationSnapshot[T]
```

基线 `src/mote_kernel/execution/run_context.py` 中该方法实际返回 `GraphRunContext`，
`_context_from_continuation()` 和 facade 也直接依赖这一返回值；现有 continuation/typing tests
同样覆盖该闭包。target 没有说明这是有意的私有签名变更，还是文档笔误，也没有给出：

- root/local `GraphRunContext` 何时由 snapshot 构造；
- `_context_from_continuation()`、facade、recovery 和 result wrapper 的迁移顺序；
- `ContinuationSnapshot` 与 `_GraphContinuation` 的完整泛型/封装调用闭包；
- 删除 `PreparedNestedRun` 后 `WaitingForChildren`、`StartMissingChildren` 的确切新形状。

这不是可由实现者自由调整的内部细节：它会改变 public result/recovery/typing 的调用链。

**必须修订：** 选择并写明“`admit()` 保持现有返回类型”或“改为 snapshot 后由哪个 owner
构造 local context”，列出所有 direct consumer 与 exact signature；若要改 ABI，先重新审核，
不能同时宣称 exact shape 不变。

### LO-IR37 — root coordinator/root finalizer 的 owner contract 仍不完整

虽然 §3.1 增加了 `root_owner/root_cell/root_entry/root_finish`，但仍缺少可构造性证明：

1. `_ScopeFinalizerEntry.source` 的 union 只列 provisional/slot/historical/awaiting/claim，
   没有 root owner 的 source 变体；root entry 的 anchor/source 如何合法建立未定义；
2. root 没有 `parent_activation`，root finalizer 与 nested finalizer 的具体差异、claim 与
   seal/discard 条件未写成 typed contract；
3. `nested_cells/entries` 是平坦 creation-order tuple，无法单凭字段重建
   deepest-child-first 的树形关系；grandchild、historical scope 和 sibling 的 traversal
   关系没有唯一表示；
4. `from_new_root/from_state_only/from_snapshot` 没有说明 root anchor、root cell source、
   root frame/entry 的 exact construction 和 ownership 校验。

`exported` 虽被称为 method-local guard，但当前草图把它列为 coordinator 字段；其存活范围和
“不得成为 lifecycle flag”的边界也需明确。

**必须修订：** 给出 root entry 的合法 nominal source、root finalizer 的最小操作、递归
parent/child traversal 表示和 `from_*` 的逐步构造/校验顺序；不能让实现者通过隐式 root
特例或第二集合补齐。

### LO-IR38 — invocation cancellation 没有真实 caller 与 `CancelledError` 语义

target 多处说“invocation boundary”调用 coordinator，但没有给出 `Graph.run()` 的
`try/finally`、外层 `asyncio.CancelledError` 捕获和最终返回/重抛路径。基线 facade 当前在
`drive_root()` 后直接构造 result；session 则在 `next()`/close 路径自行处理并重新抛出
`CancelledError`。因此以下问题仍未冻结：

- 外层 invocation cancellation 与 node 自己抛出的 `CancelledError` 如何区分；
- cancellation 与 `StartGraphRun`/claim/settlement exact acknowledgement 竞争时，哪个操作
  被 shield、何时登记 provisional、何时生成 `_InvocationAbortSignal`；
- child-first cleanup 完成后是保持既有 `CancelledError` public 行为，还是返回 `AbortedResult`；
- result-boundary 线性化点前后的取消如何与 facade/session 的现有处理组合，避免重复 close、
  abort 或错误覆盖。

没有真实 caller，三类 cancellation 只是状态图，无法验证 public behavior 与既有测试一致。

**必须修订：** 写出从 `Graph.run()` 入口到 coordinator unwind 的唯一异常/取消调用图，明确
`CancelledError` 的来源分类、shield 范围、signal 生成、最终错误形状和 exact commit 竞争
规则；保留 node-initiated cancellation 与 invocation cancellation 的既有区别。

### LO-IR39 — relay canonical order 与 historical relay 未形式化

§6.1 的多层示例把 root batch 写成 `(r_G, r_C, r_S)`，但现有 `ScopedFrameIndex` 按
`ScopeRunCoordinate` 排序；父 scope 与 descendant scope 的前缀顺序未必得到该排列，示例
不能作为规范。当前 target 还没有统一的 heterogeneous terminal/awaiting record 排序 key，
也没有定义 destination 已有 record 时如何保持 object identity、拒绝 prefix/乱序/拼接并
保证一次 merge。

此外，文档说 historical terminal scope“不另建 relay closure”；当 immediate parent 自身
也是 historical 或多层 snapshot 混合时，descendant record 如何进入 root batch 没有完整 caller
路径，存在丢失或重复 relay 的风险。

**必须修订：** 明确复用的既有 canonical order/key、terminal 与 awaiting 的统一排序、
destination old tuple 的精确合并/失败事实，以及 historical parent、grandchild、sibling
和 leaf 的每条 immediate-parent edge 调用顺序。示例必须与该 key 一致，不能用“具体顺序由
coordinate 决定”留给实现者。

### LO-IR40 — private nominal contract 仍不能通过 strict typing

target 声称所有 private contract 都是 nominal/strict typed，但存在直接的类型和伪代码缺口：

- `_ScopeFinalizerClaim` 定义为非泛型，却在 `_ScopeFinalizerEntry.source` 中写成
  `_ScopeFinalizerClaim[T]`；
- `_EntryCleanupFacts` 声明了无默认值字段，却在可执行伪代码中直接调用
  `_EntryCleanupFacts()`；
- `_SealAttempt = SEALED | NOT_WRITTEN | UNKNOWN_AFTER_COMMIT` 没有 nominal type/value
  定义；
- `owner_local_finish()` 使用未声明的 `entry`、`awaiting_record`、`aborted_record`、
  `SEALED`、`NOT_WRITTEN`、`owner is terminal`；`shield_step`、
  `seal_preconditions_are_confirmed` 和 ledger error propagation 也没有 typed signature。

这些不是格式问题。若按 strict typing 实现，开发者必须自行发明类型；若按字面实现则无法
通过类型检查。

**必须修订：** 为每个 nominal value、字段默认/构造方式、helper 的输入输出和异常语义给出
唯一声明；伪代码中的变量必须来自已声明的 lexical scope。不得以 `Any`、裸字典或运行时
反射补洞。

### LO-IR41 — confirmed-prefix handoff 尚未接入实际 recovery commit loop

target §7.3/§8.3 声称 `handoff_confirmed_prefix()` 是 recovery loop 的唯一 caller，但
当前 facade 的 recovery 路径仍在 `fences`/`planned_resumes` 循环中直接 `commit_transition()`，
失败后直接构造 `_partial_commit_error()`；recovery module 负责 planning，二者之间没有
confirmed-cut 的 typed 交接。

文档没有说明：

- facade 还是 `engine/recovery.py` 改为唯一 caller；
- 如何记录 exact-confirmed owner/cell prefix、排除 callback-after-replacement 的 unknown
  candidate；
- frame installation 已成功但 relay 未完成时 cut 的冻结点；
- handoff 失败时与既有 `_PartialCommitError` 的优先级、root state/continuation 来源。

如果保留现有 loop，新的 operation 就是未使用的第二路径；如果移动 loop，又会影响现有
`_PartialCommitError`、state-only recovery 和 typing consumer。

**必须修订：** 给出 facade、recovery、coordinator 三者的唯一 caller 图和 typed confirmed-cut
表示，明确替换现有 loop 的位置、调用次数、失败优先级与 direct tests；不得以“recovery
loop 唯一 caller”一句话代替迁移闭包。

### LO-IR42 — module ownership 冲突，存在 import cycle/重复实现风险

实施顺序 §9 step 1 把 partition/merge 放入 `execution/run_context.py`；§8.3 又把
`_owner_for_record()`、partition、merge 列为 evidence handoff contract，§9 step 3 则说
`execution/invocation.py` 调用并拥有这些操作。当前 `invocation.py` 已依赖 `run_context.py`，
若按 step 1 增加 graph-aware partition/merge，容易形成反向 import；若按 invocation 拥有，
又与 step 1/唯一 owner 表述冲突。

**必须修订：** 冻结每个 operation 的唯一 module owner（必要时提取只含 typed protocol 的
窄模块），写明 import 方向和 production/test consumer；禁止在两个模块复制 partition、
merge 或 `_owner_for_record()`。

### LO-IR43 — direct-consumer/test manifest 仍与 source scan 不一致

§9.1/§9.2 已扩充不少文件，但仍没有逐文件覆盖所有会被 signature、frontier、cancellation、
continuation 或 admission 变化触及的 consumer。当前 scan 至少发现下列文件未在 manifest
中明确 KEEP/MODIFY 及原因：

```text
tests/execution/driver.py
tests/execution/test_interrupt_flow.py
tests/execution/engine/test_completion_projection.py
tests/execution/engine/test_output_projection.py
tests/execution/engine/test_resume_input_contract.py
tests/execution/engine/test_admission.py
tests/execution/engine/test_settlement.py
tests/typing_negative/invariant_continuation.py
tests/architecture/test_graph_typing_fixtures.py
src/mote_kernel/execution/engine/admission.py
src/mote_kernel/execution/engine/resume_input.py
src/mote_kernel/execution/engine/routing.py
src/mote_kernel/execution/engine/resume_admission.py
```

其中不要求全部 MODIFY；但必须逐文件说明 KEEP（为何不受影响）或 MODIFY（哪些旧调用/断言
迁移），并与最终 source scan、测试保留规则一致。当前 manifest 同时声称 direct-consumer
迁移完整，证据不足。

**必须修订：** 重新运行 production/test scan，补齐唯一 manifest，保留所有既有 case/assertion；
不得通过删测试、改名、兼容 alias 或弱化断言来消除差异。

## 4. 不因本轮修订而扩大的范围

关闭上述问题不授权或要求：

- 新 State/status/command、public `GraphRun`/handle、第二 scheduler/runner；
- 新 persistence/checkpoint/journal/receipt、跨 invocation child-ID load/recovery、retry、
  failover 或 worker handoff；
- global registry、overlap detector、乐观锁或持久化锁；
- 修改 `ChildProjection`、`StepRequest`、`ScopedFrameIndex`、`ChildStateBinding` 或 public
  `Graph.run()` ABI；
- 新 legacy、compatibility、AST-only 或 private-helper-count test。

## 5. 验证记录

| 检查 | 结果 |
| --- | --- |
| target / requirements hash | target `415bef2f…`；requirements `1ff31e95…` |
| baseline behavior | `python -m pytest -q tests/execution tests/state/graph_state tests/architecture/test_graph_execution_ownership.py -p no:cacheprovider` → **804 passed** |
| source spot-check | `graph_run.py:18-24` 仍由 `project_start_graph_command()` 调用 `child_graph_run_id()`；`facade.py:682-730` 仍直接运行 recovery fence/resume commit loop；现有 continuation/session cancellation consumer 与 LO-IR36/38/43 对应 |
| `make check` | Ruff、format、pyright 通过；complexity-ratchet 的 2 个测试失败（既有候选清单未登记、`decision_points` 为 1314 而门槛为 1312），按用户范围记为 `USER-EXCLUDED`，不宣称整体通过 |
| production/State/Store/API/tests changes | 本轮无修改；仅新增本评审文档 |
| complexity gate | `USER-EXCLUDED / NOT PASSED`；本轮不把复杂度门禁结果当作实施授权 |

## 6. 最终 ledger

```text
per-GraphRun state ownership             = PASS IN DIRECTION
parent authoritative child state/ID      = FORBIDDEN / PASS IN INTENT
frontier waiting-before-claim            = PASS IN TEXT
RUNNING/awaiting handoff                 = DIRECTION ABSORBED / CALLER+FAILURE OPEN (LO-IR34/35)
coordinator opaque boundary              = LO-IR33 OPEN
child drive/factory recursion            = LO-IR34 OPEN
self-sink/cell handoff                  = LO-IR35 OPEN
continuation/frame ABI                   = LO-IR36 OPEN
root coordinator/finalizer               = LO-IR37 OPEN
invocation cancellation                  = LO-IR38 OPEN
relay order/historical                   = LO-IR39 OPEN
strict private typing                    = LO-IR40 OPEN
confirmed-prefix recovery integration    = LO-IR41 OPEN
module ownership                         = LO-IR42 OPEN
direct consumer/test manifest            = LO-IR43 OPEN
finalizer shielded cleanup               = PASS IN DIRECTION / depends on owner contract
terminal historical projection           = PASS IN DIRECTION / depends on awaiting/relay contract
persistence / Store                      = KEEP EXISTING / NO NEW PROTOCOL
child-ID-only recovery / failover        = OUT OF SCOPE
cross-parent overlap                     = CALLER PRECONDITION / NO RUNTIME GATE
implementation target                    = CHANGES REQUESTED / NOT READY
production / State / Store / API / tests = NO CHANGE IN THIS REVIEW
implementation authorization             = NOT GRANTED BY THIS REVIEW
```

请先按第 3 节补齐唯一 typed owner/caller/failure contract，重新运行 source/test scan 并更新
target hash，再进行下一次独立 implementation review；在 blockers 关闭且用户另行明确授权
前，不开始编码。
