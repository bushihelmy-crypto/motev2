# `ac17820` 父子图 GraphRun 本地 ownership 代码独立评审

> **结论：`CHANGES REQUESTED / NOT READY TO MERGE`。**
> **方向：`PASS`；实现闭合度：`FAIL`。**

本文件只评审提交 `ac17820` 的 production/test 实现，不修改源码，不替代用户授权，
也不重新扩大 `GRC-LO-001` 的范围。评审按已经冻结的最简原则进行：每个图的
`GraphRun` 独占自己的 state/frame/session/executor/commit；parent 不持有 child 的
state 或 child `run_id`；父子只交换当前 invocation 的 opaque handle 与既有 typed
结果；不新增 persistence、failover、跨 invocation child-ID recovery、registry、
overlap gate、第二 runner 或 public API。

## 1. 评审对象与基线

| 项目 | 值 |
| --- | --- |
| reviewed commit | `ac17820b8aa60ec2eb93feec757234a8241522ab` |
| direct parent | `43b0ea96caf0ce5f85e797bbd764d0237f1828ff`（实施文档提交） |
| production/code baseline | `ebcd043fdfe324c610328a08cb1a3e8a14b37e10` |
| implementation target SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| review date | `2026-08-30` |

提交实际修改 17 个 source/test 文件；本轮没有修改任何 production 或 test 文件，
只新增本评审文档。工作树中其他用户修改和未跟踪文档均保持不动。

## 2. 已执行检查

| 检查 | 结果 |
| --- | --- |
| `python -B -m ruff check src tests` | PASS |
| `python -B -m ruff format --check src tests` | PASS（154 files） |
| `pyright` | PASS（0 errors） |
| `python -B -m pytest -q --ignore=tests/architecture/test_complexity_gate.py --tb=short -p no:cacheprovider` | PASS（881 passed） |
| `git diff --check ac17820^ ac17820` | PASS |
| `make check` | **未完整通过**：Ruff/format/Pyright 通过，complexity-ratchet 的 2 个测试失败后 Make 停止 |
| complexity health | FAIL；按用户授权标记为 `USER-EXCLUDED / NOT PASSED`，不作为本轮架构裁决 |

complexity 失败不是本轮 blocker：当前 target/库存仍有未审核候选和结构指标回退，
但用户已经明确要求忽略该门禁。不能因此把 `make check` 写成完整通过。

## 3. 总体判断

以下方向已经与需求对齐，本轮不重新打开：

- `_GraphRun` 为 root/child 分别持有 state、transition、commit、executor、session 和
  local frame；child identity 的确定性投影测试通过。
- typed child failure、ordinary node exception 不广播 sibling；基本 nested success、
  awaiting/resume、重复 superstep 和三层嵌套行为测试通过。
- State schema、reducer、Graph public API、既有 continuation/frame 形状没有新增协议。
- cross-parent overlap、持久化扩展、failover、仅凭 child ID 恢复均没有作为本轮要求。

但下面四项是实现层的确定性 ownership/validation 缺口；第五项是现有
`GraphCommit` 边界上的 cancellation 缺口。它们使提交不能按当前 target 的 READY 结论
合入。

## 4. 阻塞问题

### F-01（P1）`_ChildHandle` 不是 opaque handle，parent 可直接读取 child state/frame

**位置：**

- `src/mote_kernel/execution/family_driver.py:163-191`
- `src/mote_kernel/execution/family_driver.py:491-505`
- `src/mote_kernel/execution/family_driver.py:617-627`
- `src/mote_kernel/execution/family_driver.py:818-896`

当前 `_ChildHandle` 是五元 tuple：`drive`、`abort`、`release`、`consume`、`evidence`。
parent 的 `_drive_child()` 直接调用 `handle[3]()`，`_descendant_evidence()` 和
`release()` 直接调用 `handle[4]()`；该 reader 返回的 `_OwnerEvidence` 又包含
`ChildStateBinding`（含 child `GraphRunState`）和 child frame index。`_ChildCall` 还把
evidence reader 单独存进 parent 的 child record。

这不是“immutable continuation evidence 不能存在”的问题；需求允许 sealed continuation
在入口/出口适配中携带只读 evidence。问题是**live parent record/handle 直接拥有并读取
该 evidence**，因此 parent 实际上可以检查 child state/frame，而 handle 也暴露了
wait/abort/release 之外的 consume/export 操作，违反 requirements §3.5、§4.2、§5.3
以及 implementation target §2.1 的 record 约束。

动态探针结果：

```text
handle_len=5
slot_types=('function', 'function', 'function', 'function', 'function')
handle[4]()[0][0].state.status = RUNNING
```

**影响：** family-level state exporter 和 child evidence reader 仍在 parent 的 live
ownership 边界内；后续实现很容易重新形成 parent 的 child-state 镜像或按 evidence
反查 owner。

**最小修复方向：** parent-facing handle 只能有 wait/abort/release 三类能力；terminal
typed result 和出口所需 immutable evidence 必须在单一 owner-local handoff 中交给既有
result/continuation adapter，不能以 reader/consume slot 或第二个 record 字段暴露给
parent。不得新增 public type、State 字段、registry、persistence 或第二 runner。

### F-02（P1）`WaitingForChildren` 对合法 mixed missing/active 顺序误报 canonical

**位置：**

- `src/mote_kernel/execution/engine/frontier.py:42-74`
- `src/mote_kernel/execution/engine/superstep.py:64-68`
- `src/mote_kernel/execution/result.py:195-205`
- `src/mote_kernel/execution/family_driver.py:349-366`

`prepare_frontier()` 先按 parent frontier 的 canonical 顺序收到 projections，再分别
填充 `missing` 和 `active`。但 `WaitingForChildren.__post_init__()` 把字段固定拼为
`(*missing, *active)` 后，要求这个拼接结果整体仍按 parent 排序。

因此当 canonical parent 顺序为 `a,b`，而 `a` 已 active、`b` missing 时，合法的
`WaitingForChildren(missing=(b,), active=(a,))` 被拒绝：

```text
canonical parents = (a, b)
WaitingForChildren((MissingChild(b),), (ActiveChild(a),))
=> ValueError: children to drive must be non-empty, distinct, and canonical
```

这与 target/requirements 允许的 mixed metadata 以及“missing 优先、runnable 继续驱动”的
语义冲突。当前测试只覆盖 `missing(a), active(b)`，没有覆盖 active 排在 missing 前面的
反向合法情况。

**影响：** continuation admission 或 setup 形成一个 active sibling 与一个 missing
sibling 时，parent 在 claim 前直接失败，无法继续驱动或正确返回 awaiting。

**最小修复方向：** 统一“parent canonical 顺序”和“missing 优先”的表示/校验规则；可以
分别校验两个分组的相对顺序并在 drive loop 中执行优先级，但不能用 missing-first 的
字段拼接伪装成全局 canonical。补一个 active-before-missing 的 deterministic regression
case；不新增 result variant 或 scheduler。

### F-03（P1）terminal child boundary 未校验完整 child coordinate

**位置：** `src/mote_kernel/execution/family_driver.py:397-421`

`_install_terminal()` 只检查：

```python
availability.child_scope_run.scope == child_graph.definition_scope
availability.descriptor == child_graph.graph_output_descriptor.identity
```

没有检查 `availability.child_scope_run` 是否等于当前 parent activation 的唯一预期坐标：

```python
child_scope_run_for_activation(self._scope_run, parent)
```

动态构造“scope 相同、descriptor 相同、`graph_run_id='other'`”的 forged boundary，当前
会被接受并写入 parent frame：

```text
accepted ChildBoundaryAvailabilityCoordinate(... graph_run_id='other' ...)
```

**影响：** foreign child output 可以越过 owner boundary 进入 parent frame；之后的 frame
lookup/continuation export 可能携带错误 run identity，违反 requirements §5.4 的 identity
注入性和 fail-closed admission。即使正常 `_opaque_handle.terminal_boundary()` 目前会
生成正确坐标，boundary 是跨 owner 的 typed evidence，接收方仍必须做完整校验。

**最小修复方向：** 在任何 `add_child_boundary()` 之前，用现有
`child_scope_run_for_activation()` 计算 expected coordinate，并要求整个
`ScopeRunCoordinate`（包含 scope 与 `graph_run_id`）精确相等；失败继续使用现有
`SnapshotMismatchError`，不新增 identity/lookup API。

### F-04（P1）existing-child admission 接受不属于当前 parent frontier 的 stale/future binding

**位置：** `src/mote_kernel/execution/family_driver.py:677-788`

`admit_existing_children()` 的 direct candidate 只按
`binding.parent_activation.scope_run == self._scope_run` 筛选，并检查 node 是否存在
nested definition。它没有在 owner admission 边界再次确认：

1. activation 的 `run_id/superstep` 是当前 owner 的当前 state；
2. 当前 owner frontier 中该 node 仍是 pending nested node；
3. `binding.coordinate` 等于 `child_scope_run_for_activation(self._scope_run, parent)`。

动态构造当前 parent state 为 superstep 0、但 binding 为 superstep 1 的 child state，直接
调用该 admission 会成功创建 child record：

```text
accepted ParentGraphActivation(run_id='parent', superstep=1, node_id='nested')
```

外层 facade 的某些 recovery proof 目前会先挡住一部分 malformed 输入，但 owner admission
本身仍是跨边界的内部入口，不能依赖调用者恰好先完成 proof。

**影响：** 可以为当前 parent 从未激活的 node 创建 phantom child owner；其 state/frame
会被后续 drive、export、fence 或 abort 纳入，破坏 continuation 的 exact admission 和
parent/child ownership 隔离。

**最小修复方向：** 在创建 `_GraphRun` 前复用已有 frontier/state/identity validator，
精确确认当前 superstep 的 pending nested activation 与 deterministic coordinate；
stale、future、foreign 或重复 activation 以现有 `ResultCollectionError`/
`SnapshotMismatchError` fail-closed。不要新增 child-ID lookup、recovery、registry 或
持久化。

### F-05（P1）owner transition 的 cancellation/commit 未确认窗口会用 stale state 清理

**位置：**

- `src/mote_kernel/execution/family_driver.py:385-392`
- `src/mote_kernel/execution/family_driver.py:790-815`
- `src/mote_kernel/execution/facade.py:639-654`
- `src/mote_kernel/execution/facade.py:713-765、815-821`

`_GraphRun._transition()` 只有在 `await commit(...)` 返回 exact successor 后才写回
`self._state`。如果既有 `GraphCommit` callback 已把 candidate 写入 authoritative store，
随后在 acknowledgement 返回前收到 cancellation，owner 仍持有旧 revision。facade 捕获
cancellation 后调用 `root.abort()`，会基于旧 state 生成 stale fence/abort transition。

以严格 revision-CAS 的现有 commit 形状做动态探针，结果为：

```text
raised RuntimeError: stale expected=0 actual=1
authoritative state: RUNNING revision=1
```

也就是说，原始 `CancelledError` 被 cleanup 错误替换，authoritative run 留在
`RUNNING`，而不是完成本需求声明的 owner-local cancellation 顺序。相同的边界还存在于：

- fresh root 的 `StartGraphRun` commit 位于 facade setup try 之外；
- fence/resume partial loop 只捕获 `Exception`，Python 3.11 的 `CancelledError` 属于
  `BaseException`，会绕过已确认 prefix 的既有 handoff。

**范围说明：** 本 finding 不要求新增 load、receipt、retry、failover 或跨 invocation
恢复；这些仍明确 out of scope。它只要求当前实现不要用已知 stale memory snapshot
猜测/覆盖 authoritative state。如果本 change unit 刻意不承诺
“callback 已写入但 acknowledgement 未返回”的 unknown-ack 语义，则必须从 target 的
cancellation candidate/commit 验收和 READY 声明中明确删去该承诺，而不能当前代码一面
声称 cancellation-safe、一面继续发出 stale transition。

**最小修复方向：** 在既有单参数 `GraphCommit` 边界内固定 cancellation linearization：
要么让现有 commit await 在取消时完成并取得 exact acknowledgement，再进行 owner-local
cleanup；要么把 unknown acknowledgement 按既有 commit 错误语义原样传播，禁止基于旧
revision 发出新的 transition。无论选择哪一种，都不能引入新的 Store/load/failover
协议，也不能让 cleanup 错误覆盖更早的 caller-visible cancellation/commit 错误。

## 5. 不重新打开的范围

本轮明确不把下列事项作为 finding 或修复要求：

- 两个不同 parent invocation 重叠复用 immutable `CompiledGraph` 的 runtime 检测/拒绝；
  这是 caller precondition。
- 仅凭 `child_run_id` 的跨 invocation load/recovery、checkpoint、terminal publish/ack、
  failover、worker handoff、retry 或 rollback。
- State/reducer/public API/continuation ABI 的新增字段、variant、第二 runner 或兼容 alias。
- complexity gate 的两个失败；它们已按用户授权标记 `USER-EXCLUDED / NOT PASSED`。

## 6. 裁决

```text
per-GraphRun state ownership direction = PASS
F-01 opaque handle boundary           = OPEN / P1 BLOCKER
F-02 mixed waiting canonicality       = OPEN / P1 BLOCKER
F-03 terminal coordinate validation   = OPEN / P1 BLOCKER
F-04 existing admission exactness     = OPEN / P1 BLOCKER
F-05 cancellation commit linearization= OPEN / P1 BLOCKER (or target claim must be narrowed)
State/reducer/Store schema change     = NONE requested here
persistence/failover/ID-only recovery = OUT OF SCOPE
review result                         = CHANGES REQUESTED / NOT READY TO MERGE
production changes in this review     = NONE
```

修复只需围绕上述 ownership、canonicality、exact coordinate、admission 和既有 commit
边界完成下一次窄范围变更；不需要再增加架构层。修复后应补齐对应 deterministic
regression cases，再进行一次独立代码复审。
