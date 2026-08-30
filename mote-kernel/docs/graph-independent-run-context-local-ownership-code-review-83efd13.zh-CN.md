# `83efd13` 父子图 GraphRun 本地 ownership 再次验收

> **结论：`CHANGES REQUESTED / NOT READY TO MERGE`。**
> `83efd13` 已关闭上一轮剩余的 owner-handoff blocker，原 R-01、R-02 均可关闭；但删除
> live owner sequence 时同时删除了 transition-plan 完整消费校验，形成 1 个新的 P1
> correctness blocker。除该项外，未发现其他阻塞问题。

本次以 `edb0c98e49a44f9c94ac9cae787f65038e245060..83efd13dec7e244ccaa0d7538efba4383e835189`
为固定评审边界，继续执行 GRC-LO-001 的窄目标：每个 `GraphRun` 只拥有自己的
identity/state/frame/session/commit；parent 只持 opaque child handle、只接收 typed result；
不新增 persistence、failover、child-ID-only recovery、registry、第二 runner 或兼容路径。
复杂度门禁按用户指示排除，不参与本次裁决。

## 1. 评审对象

| 项目 | 值 |
| --- | --- |
| reviewed commit | `83efd13dec7e244ccaa0d7538efba4383e835189` |
| baseline | `edb0c98e49a44f9c94ac9cae787f65038e245060` |
| implementation SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| review date | `2026-08-30` |

提交修改 4 个文件，共 `453 insertions / 409 deletions`：`family_driver.py`、两份执行测试和
complexity ratchet 数值。工作树中的 README、其他文档、example 和 monorepo 文件均视为用户
已有内容，本次未修改；本次只新增本文。

## 2. 阻塞问题

### R-03（P1）未被 admission 的 descendant fence plan 会被静默丢弃

**位置：**

- `src/mote_kernel/execution/invocation.py:187-225`
- `src/mote_kernel/execution/family_driver.py:1076-1143`
- `src/mote_kernel/execution/family_driver.py:1145-1163`
- 本提交删除的基线校验：`edb0c98:family_driver.py:1136-1149`
- 本提交删除且未等价替换的测试：
  `test_continuation_transition_plan_requires_a_constructed_owner`

`plan_fences()` 会为 lineage 中每个带 active execution lease 的 state 生成
`PlannedFence`。新的 continuation setup 只沿当前可 admission 的 parent-child 链递归：某个
ancestor 已 terminal 或不再是 current activation 时，不会继续构造其 descendant owner。
这是正确的 owner-lifecycle 方向，但 setup 成功返回前没有再确认所有 `fences`/`resumes` 已被
精确交给一个 owner。于是未到达 owner 的 plan 被无声忽略。

已用 public `Graph.run()` 做临时负向探针复现：

1. 先取得合法的 `root -> child -> grandchild` 完成态 continuation；
2. 将 terminal child 下的 grandchild binding 置为结构合法的 `RUNNING + execution lease`，
   并移除只与 grandchild terminal 状态冲突的 frame evidence；
3. 当前 `validate_context()` 接受该 snapshot，`plan_fences()` 生成 grandchild fence；
4. setup 因 terminal ancestor 不构造 grandchild owner，也不消费该 fence；
5. `Graph.run()` 正常返回 `CompletedResult`，commit 观察到 **0 次 transition**，active lease
   没有 fence，原 RUNNING descendant evidence 继续被导出。

基线的 `_validate_owner_transition_plans()` 会对此抛出 `SnapshotMismatchError`。该旧实现依赖
live owner sequence，确实应删除；问题是它承担的 fail-closed invariant 也一起消失了。这会让
一个已经规划的 owner-local transition 静默丢失，违反 continuation admission、exact
transition 和 stale/foreign evidence 必须 fail-closed 的既有语义，因此不能作为非阻塞清理项。

**最小闭合方向：**

- 在任何 setup commit 前，仅基于 immutable continuation lineage 校验 active owner tree；
- 要求每个 planned fence/resume coordinate 精确对应一个可构造的 current RUNNING owner，
  未匹配、重复或位于 terminal/non-current ancestor 下时立即 `SnapshotMismatchError`；
- 或采用等价的 plan-consumption proof，但必须在 root handoff 前证明没有剩余 plan；
- 增加“terminal ancestor + leased RUNNING descendant”负向回归测试，并断言零 transition 后
  fail-closed；
- **不得**恢复 `_GraphRun` owner tuple、`owner.coordinate`、live coordinate router、family
  context 或 `_abort_unowned_graph_runs()`。校验属于 continuation transport/admission 层，
  不取得 child state 提交权。

## 3. 上轮 blocker 复核

| finding | 状态 | 结论 |
| --- | --- | --- |
| R-01：continuation setup 保留 owner sequence/coordinate-based live coordinator | **CLOSED** | `_GraphRun.coordinate`、`_construct_existing_child()` owner tuple、`_construct_continued_root()` owner sequence、`_validate_owner_transition_plans()` 的 live-owner router 和 `_abort_unowned_graph_runs()` 均已删除。existing child 在 construction frame 内自行 fence/resume/递归 admission，最后只返回 `_ChildHandle`。 |
| R-02：commit-origin cancellation 被误判为 caller cancellation | **CLOSED** | root/child start、claim 以及 continuation child fence/resume 均保持 strict-CAS：commit-origin `CancelledError` 不从未确认 snapshot 猜测 abort。 |

R-03 要补回的是 immutable evidence/plan 的完整性验证，不是 R-01 所禁止的 live owner 集合或
coordinate-based owner 控制通道；两者不能混为一谈。

## 4. 已确认正确的实现

- `_GraphRun` 不再公开 child routing 用的 coordinate/is-root 属性；parent 的 child-call record
  只保存 position、parent activation、typed phase 和 opaque handle。
- fresh child 在自己的 construction frame 内派生 identity、提交 `StartGraphRun`、安装 local
  state/frame/executor，并只返回 drive/abort/release 三能力 handle。
- continued child 在 opaque handle 交给 parent 前，由该 child owner 自己完成 local
  fence/resume；测试已明确锁定 `resume commit -> handle handoff` 顺序。
- child construction/handoff 失败由当前 candidate 或 opaque handle 清理；没有恢复由 parent
  遍历 binding 并代写 child state 的 family-wide abort 路径。
- 已确认 transition 的 setup 前缀继续通过 partial continuation 交回；未确认/commit-origin
  cancellation 不执行 stale abort。
- child abort/release 仍只沿 opaque handle 向下传播；parent 不持有 child owner、state、frame、
  session 或 commit capability。
- State/reducer、Graph public API、continuation/frame ABI 和 Store/commit protocol 没有新增字段、
  variant 或兼容路径。

## 5. 检查结果

| 检查 | 结果 |
| --- | --- |
| `git diff --check edb0c98 83efd13` | PASS |
| 非 complexity 全量测试 + coverage | PASS：`896 passed`，`100.00%`（5230 statements / 1650 branches） |
| 本次相关执行测试 | PASS：`126 passed` |
| continuation child fence/resume strict-CAS cancellation 临时探针 | PASS：`2 passed`；均未产生 `AbortGraphRun` |
| orphan descendant fence-plan 临时负向探针 | **FAIL as finding**：调用正常完成、0 次 transition；复现 R-03。探针文件已删除 |
| `python -B -m ruff check src tests` | PASS |
| `python -B -m ruff format --check src tests` | PASS：154 files |
| `pyright` | PASS：0 errors |
| build + `twine check` | PASS：sdist/wheel 均通过 |
| live owner/handle source scan | PASS：无 `_GraphRun` owner collection、`owner.coordinate` router、`_abort_unowned_graph_runs()`；child constructor 只交出 opaque handle |
| complexity gates | `USER-EXCLUDED / NOT RUN` |

未直接运行 `make check`，因为它固定包含本轮明确排除的 complexity health/ratchet；其余 lint、
typecheck、非 complexity 全量测试、coverage、build 和 package check 已分别执行。未运行 monorepo
`pre-commit --all-files`：当前 monorepo 有大量与本提交无关的用户修改和未跟踪文件，全树 hook
可能改写这些内容。

## 6. 裁决

```text
R-01 owner/handle boundary          = CLOSED
R-02 commit-origin cancellation     = CLOSED
R-03 plan-consumption invariant     = OPEN / P1 BLOCKER
functional regression suite        = PASS
persistence/failover/ID recovery    = OUT OF SCOPE
complexity gate                     = USER-EXCLUDED
review result                       = CHANGES REQUESTED / NOT READY TO MERGE
```

方向已经收敛，原 ownership 结构性问题本轮确实闭合。只需在 immutable continuation admission
层补回 R-03 的 fail-closed invariant；不需要恢复任何 family context，也不需要扩大到持久化、
failover 或 child-ID recovery。该项修复并新增对应回归测试后，可再次做最终验收。
