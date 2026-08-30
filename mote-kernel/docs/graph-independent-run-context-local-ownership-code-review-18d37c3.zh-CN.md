# `18d37c3` 父子图 GraphRun 本地 ownership 代码复审

> **结论：`CHANGES REQUESTED / NOT READY TO MERGE`。**
> **相对 `ac17820` 已明显收敛：5 个原 finding 中 4 个按原问题闭合，1 个仅部分闭合；**
> **本轮仍有 2 个 P1 blocker。**

本文件独立复审提交 `18d37c3` 的 production/test 实现。评审继续以冻结的
`GRC-LO-001` 窄范围为准：每个图的 `GraphRun` 自己拥有 state、frame、session、executor、
identity 和 commit cursor；parent 只持有当前 invocation 的 opaque child-call handle 并接收
typed result；不新增 persistence、failover、仅凭 child ID 的跨 invocation recovery、
registry、第二 runner、public API 或兼容路径。复杂度门禁依用户授权排除，不作为本轮架构
裁决依据。

## 1. 评审对象与基线

| 项目 | 值 |
| --- | --- |
| reviewed commit | `18d37c3848c859c39e348e83df9613cf7c7e748f` |
| direct parent | `ac17820b8aa60ec2eb93feec757234a8241522ab` |
| implementation target SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| review date | `2026-08-30` |

提交修改 3 个 production 文件和 6 个 test 文件。本轮评审不修改 production、test 或既有
需求/实施文档，只新增本复审文件；工作树中原有用户改动保持不动。

## 2. 上一轮 finding 复核

| 上轮 finding | 本轮状态 | 依据 |
| --- | --- | --- |
| F-01：五能力 handle 暴露 evidence/consume | **CLOSED（原问题）** | `_ChildHandle` 现仅有 drive/wait、abort、release 三个 capability；parent record 不再保存 evidence reader。更深的 child owner 落点问题见 R-01。 |
| F-02：mixed missing/active canonicality | **CLOSED** | `WaitingForChildren` 分别校验 missing 与 active 的组内 canonical 顺序；新增 active-before-missing 回归覆盖。 |
| F-03：terminal coordinate 未完整校验 | **CLOSED（行为）** | 已比较完整 `ScopeRunCoordinate`；但校验和 identity 派生仍发生在 parent owner 内，归入 R-01。 |
| F-04：existing admission 接受 stale/future active child | **CLOSED** | running binding 必须精确对应当前 pending nested activation；future/foreign/coordinate/state-parent mismatch 均 fail-closed。 |
| F-05：caller cancellation 的 unknown-ack stale cleanup | **PARTIAL** | 外部 caller cancellation 已 shield 到 exact acknowledgement 并更新 owner state；commit task 自身的 `CancelledError` 仍会进入旧 state cleanup，见 R-02。 |

`_evidence_adapter` 在当前调用图中只收集 owner 已交出的 immutable continuation/frame evidence，
reader 只由 facade 的 result projection 使用，不参与 prepare、claim、settlement、identity lookup
或 state transition。因此本轮不把它判为重新引入 authoritative family context；后续实现仍须
保持这条单向出口边界。

## 3. 阻塞问题

### R-01（P1）child construction、transition 和 continuation admission 尚未落到 child owner

**位置：**

- `src/mote_kernel/execution/family_driver.py:449-475`
- `src/mote_kernel/execution/family_driver.py:477-515`
- `src/mote_kernel/execution/family_driver.py:722-865`
- `src/mote_kernel/execution/family_driver.py:974-1000`
- `src/mote_kernel/execution/facade.py:682-789`
- `src/mote_kernel/execution/invocation.py:226-269`
- `src/mote_kernel/execution/invocation.py:348-424`

handle 的可观察形状已经缩成三项，但 child owner 的 construction/admission boundary 尚未真正
建立：

1. parent owner 的 `_start_child()` 亲自调用
   `child_scope_run_for_activation(self._scope_run, parent)`，取得 child coordinate/run ID；
2. 同一 parent method 亲自构造 child `StartGraphRun` 并调用 child-scoped commit；child
   `_GraphRun` 是在 start exact acknowledgement 之后才创建的；
3. parent owner 的 `_install_terminal()` 再次派生 expected child coordinate 并检查其中的
   `graph_run_id`；
4. continuation 路径把完整 family `bindings/frames` 传给 root
   `_GraphRun.admit_existing_children()`；该 parent method 直接读取
   `ChildStateBinding.state/status/parent/run_id`，校验 child state，并据此构造 child owner；
5. 在 child `_GraphRun` 创建之前，facade 还对 family-wide `lineage` 调用 `plan_fences()` /
   `plan_resumes()`，并逐个对 child scope 直接执行 `FenceGraphExecution` / `ResumeGraphNodes`
   commit。也就是说，正常 child resume 的 authoritative transition 仍由 facade setup frame
   推进，而不是由对应 child owner 推进。

commit-pinned 顺序探针在一个 nested child awaiting 后只 resume child scope，得到：

```text
('commit', ('nested',), 'ResumeGraphNodes')
('construct', (), '')
('construct', ('nested',), '')
('commit', ('nested',), 'ClaimGraphExecution')
('commit', ('nested',), 'SettleGraphNode')
```

第一条 child resume commit 明确早于 root/child `_GraphRun` construction。这不是 continuation
的只读 transport/validation；它已经推进了 child authoritative state。

这不是“method-local 变量没有写进 parent State”即可满足的问题。`_GraphRun` 是语义上的 parent
state owner，而冻结 requirements/target 明确要求：

- parent 不计算、保存或按 ID 查找 child `run_id`；
- child construction frame 创建/校验自己的 identity，并完成自己的 start commit；
- child fence/resume transition 也必须由对应 owner 或其唯一 construction/admission frame
  基于自己的 last-exact state 提交；facade 只能做只读 transport validation 和路由；
- `ChildStateBinding/frame` 只由 continuation 入口/出口适配读取，parent 永远不读取 child
  state；
- parent 只能得到 opaque handle 和 typed outcome/output/status。

当前实现因此仍把 parent/facade setup 同时当成 child factory、child transition owner 和
continuation child-state admission coordinator。功能测试可以通过，但“各管各的 run ID/state”
尚未在代码 ownership 上闭合。

**最小修复方向：**

- 把 fresh child 的 coordinate 派生、start commit、owner construction 和 local input/frame
  安装移入一个独立的 child construction frame；parent 只传自己的 activation metadata 和
  typed input，只接收三能力 opaque handle；
- continuation 入口适配先精确校验/分区 binding 和 frame，再直接构造对应 child owner；parent
  只登记 activation、typed phase 与 opaque handle，不接收 `ChildStateBinding`；
- 把 child-scope fence/resume 的 candidate、commit 和 exact acknowledgement 一并交给对应
  child admission/construction owner；不得继续由 facade 遍历 child lineage 后代为提交；
- terminal coordinate 的完整校验在 child construction/owner 或 sealed transport adapter
  一侧完成，parent 只消费已 admitted 的 typed result/boundary，不再派生 child run ID；
- 不新增 public type、family context、registry、Store/load、第二 runner 或持久化协议。

### R-02（P1）commit-origin `CancelledError` 仍被误判为 invocation cancellation

**位置：**

- `src/mote_kernel/execution/family_driver.py:90-103`
- `src/mote_kernel/execution/family_driver.py:432-444`
- `src/mote_kernel/execution/facade.py:819-840`

`wait_for_owner_task()` 已能识别“当前 waiter 正在取消”并等待 commit task 完成，这正确修复了
外部 caller cancellation 的 acknowledgement 窗口。但当 **commit task 自身** 抛出
`CancelledError` 时：

1. helper 在 `current.cancelling() == 0` 时跳出，并由 `task.result()` 原样抛出该异常；
2. `_GraphRun._transition()` 没有 exact acknowledgement，因此不会更新 `self._state`；
3. facade 对所有未标记的 `CancelledError` 一律执行 invocation abort；
4. abort 随后基于旧 revision/state 发出 fence/`AbortGraphRun`。

严格 revision-CAS 探针让 commit 先写入 claim candidate、再以 `CancelledError` 结束，稳定得到：

```text
CancelledError commit cancelled after write
cause RuntimeError stale cleanup transition
authoritative RUNNING 1 True
```

也就是 commit-origin cancellation 被 facade 当作 caller cancellation，cleanup 发出 stale
transition；authoritative run 留在带 active execution 的 `RUNNING`。这仍是上一轮 F-05 所限定
的“禁止基于旧 revision 猜测提交”问题。

**范围说明：** 修复不需要 load、receipt、retry、persistence、failover 或跨 invocation
recovery。只需在现有 invocation 内保留取消来源：

- waiter/caller cancellation：等待 exact acknowledgement，写回 exact owner state，再按现有
  child-first cancellation 流程 abort；
- commit-task cancellation：按 commit error/unknown acknowledgement 原样传播，不进入基于旧
  owner snapshot 的 invocation-abort 分支。

可以使用 private one-shot marker 或 helper 的内部 tagged outcome，但不得新增 public error
variant，也不得让 cleanup error 覆盖原始 commit/cancellation error。

## 4. 已确认正确且不重新打开的范围

- 三能力 opaque handle 不再让 parent 读取 evidence reader 或 child live state/frame。
- mixed missing/active、完整 terminal coordinate、current existing-child admission 均已有确定性
  regression coverage。
- 外部 caller cancellation 会等待 start/transition exact acknowledgement，然后使用确认后的
  owner state 做 child-first abort；重复 cancellation 不会打断 cleanup。
- State/reducer、Graph public API、continuation/frame ABI、Store/commit protocol 均未新增字段
  或 variant。
- 不要求 overlapping parent invocation gate、child-ID-only recovery、持久化、failover、worker
  handoff 或 rollback。

## 5. 已执行检查

| 检查 | 结果 |
| --- | --- |
| 定向 execution/continuation/API 测试 | PASS（209 passed） |
| commit-pinned `python -B -m pytest -q --ignore=tests/architecture/test_complexity_gate.py --tb=short -p no:cacheprovider` | PASS（888 passed） |
| `python -B -m ruff check src tests` | PASS |
| `python -B -m ruff format --check src tests` | PASS（154 files） |
| `pyright` | PASS（0 errors） |
| `git diff --check ac17820 18d37c3` | PASS |
| child resume commit/construction order probe | **FAIL**（child `ResumeGraphNodes` commit 早于 child owner construction，纳入 R-01） |
| strict-CAS commit-origin cancellation probe | **FAIL**（复现 R-02） |
| `make check` | 未完整通过：Ruff/format/Pyright 通过，complexity-ratchet 2 项失败后停止 |
| complexity health | `USER-EXCLUDED / NOT PASSED`，不参与本轮裁决 |

未运行 monorepo `pre-commit --all-files`：本轮是只读代码评审，monorepo 工作树存在大量与本提交
无关的用户修改/未跟踪文件；全树 hook 可能改写这些文件。本目录的相关 Ruff、format、Pyright、
非 complexity 全量测试与 diff whitespace 已单独完成。

## 6. 裁决

```text
F-01 original handle evidence leak      = CLOSED
F-02 mixed waiting canonicality         = CLOSED
F-03 terminal coordinate exactness      = CLOSED (placement covered by R-01)
F-04 existing active admission exactness= CLOSED
F-05 caller cancellation linearization  = PARTIAL
R-01 child identity/state owner boundary = OPEN / P1 BLOCKER
R-02 commit-origin cancellation          = OPEN / P1 BLOCKER
persistence/failover/ID-only recovery    = OUT OF SCOPE
complexity gate                          = USER-EXCLUDED / NOT PASSED
review result                            = CHANGES REQUESTED / NOT READY TO MERGE
```
