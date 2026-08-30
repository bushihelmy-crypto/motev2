# `d8bf2b1` 父子图 GraphRun 本地 ownership 最终验收

> **结论：`PASS / READY TO MERGE`。**
> 上轮唯一 blocker R-03 已在 immutable continuation admission 阶段闭合；此前 R-01、R-02
> 继续保持关闭。本轮未发现新的阻塞问题。

本次以 `83efd13dec7e244ccaa0d7538efba4383e835189..d8bf2b1130e1e8d91a560c43c5c3ddd24b9fae35`
为固定评审边界，继续执行 GRC-LO-001 的窄目标：每个 `GraphRun` 只拥有自己的
identity/state/frame/session/commit；parent 只持 opaque child handle、只接收 typed result；
不新增 persistence、failover、child-ID-only recovery、registry、第二 runner 或兼容路径。
复杂度门禁按用户指示排除，不参与本次裁决。

## 1. 评审对象

| 项目 | 值 |
| --- | --- |
| reviewed commit | `d8bf2b1130e1e8d91a560c43c5c3ddd24b9fae35` |
| baseline | `83efd13dec7e244ccaa0d7538efba4383e835189` |
| implementation SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| review date | `2026-08-30` |

提交修改 6 个文件，共 `153 insertions / 177 deletions`。production 净减少 29 行：
`family_driver.py` 删除 60 行、增加 10 行，`invocation.py` 增加 26 行、删除 5 行；其余为测试和
complexity ratchet 更新。工作树中的 README、其他文档、example 和 monorepo 文件均视为用户
已有内容，本次未修改；本次只新增本文。

## 2. Finding

**无 open finding。**

## 3. R-03 闭合验证

### 3.1 校验位于正确 owner

`src/mote_kernel/execution/invocation.py:165-173,199-245` 现在只基于 immutable lineage 做
pre-commit validation：

- 每个 child binding 必须精确引用 lineage 中的 parent `ScopeRunCoordinate`；
- child state、binding coordinate 与 parent activation 必须一致；
- future parent frontier 直接拒绝；
- 任何 `RUNNING` child 必须是 parent 当前 superstep 中的 pending activation；
- 校验发生在 `admit_continued_root()` 和任何 setup commit 之前。

因此，每个带 execution lease 的 state（state invariant 已要求它必须为 `RUNNING`）都位于从
root 可达的 current active tree 上。`plan_fences()` 生成的每个 fence 必然对应随后可构造的
owner，不再存在 terminal/non-current ancestor 下的 orphan plan。resume scope 仍由既有
`_resolve_scope_run()` 沿同一 current chain 解析。

这补回了旧 live-owner 校验承担的 fail-closed invariant，但没有补回旧结构：校验只读取 sealed
transport evidence，不持有 `_GraphRun`、不提交 child state，也不成为 runtime coordinator。

### 3.2 Family driver 保持单一路径

`src/mote_kernel/execution/family_driver.py:1031-1093` 删除了重复的
`_validate_existing_child()`，改为消费已预检的 binding，并以 exact
`parent_activation.scope_run == owner_scope_run` 选择 direct child。child 仍在自己的
construction frame 内完成 local fence/resume 和 recursive admission，最后只向 parent 交出
opaque `_ChildHandle`。

源码检查确认仍不存在：

- `_GraphRun` owner tuple/list/sequence；
- `owner.coordinate` live router；
- `_construct_continued_root()` 第二 setup 路径；
- `_abort_unowned_graph_runs()` family-wide child 代写；
- `GraphRunContext` 或兼容 alias；
- child constructor 返回 handle 之外的 owner capability。

### 3.3 回归用例有效

`tests/execution/test_graph_api.py:1345-1424` 直接覆盖上一轮 R-03 的 public 复现：terminal
ancestor 下放入 `RUNNING + execution lease` descendant 后，`Graph.run()` 在首个 transition
前抛出 `SnapshotMismatchError`，并断言 commit log 为空。

future activation、state/coordinate/parent mismatch、malformed terminal state 和 resume-codec
不一致等验证继续由 `plan_fences()` 的 owner 测试覆盖。原 family-driver 重复验证测试已迁移到
invocation/runtime boundary 与 public API 层，没有通过删除测试掩盖行为。

## 4. 全部 finding 状态

| finding | 状态 | 结论 |
| --- | --- | --- |
| R-01：continuation owner sequence / coordinate-based live coordinator | **CLOSED** | owner collection、live coordinate router 和 family-wide child 代写路径均不存在；parent 只持 opaque handle。 |
| R-02：commit-origin cancellation 触发 stale abort | **CLOSED** | root/child start、claim、continuation child fence/resume 均保持 strict-CAS，不从未确认 snapshot 猜测 abort。 |
| R-03：orphan descendant transition plan 被静默丢弃 | **CLOSED** | immutable lineage 在任何 commit 前拒绝非 current 的 RUNNING descendant；public 回归测试断言零 transition。 |

## 5. 检查结果

| 检查 | 结果 |
| --- | --- |
| `git diff --check 83efd13 d8bf2b1` | PASS |
| 非 complexity 全量测试 + coverage | PASS：`904 passed`；5212 statements、1640 branches，`100.00%` |
| 本次相关 execution 定向测试 | PASS：`170 passed` |
| continuation child fence/resume strict-CAS cancellation 临时探针 | PASS：`2 passed`；均未产生 `AbortGraphRun` |
| 上轮 orphan descendant public 负向场景 | PASS：commit 前 `SnapshotMismatchError`，零 transition |
| `python -B -m ruff check src tests` | PASS |
| `python -B -m ruff format --check src tests` | PASS：154 files |
| `pyright` | PASS：0 errors |
| build + `twine check` | PASS：sdist/wheel 均通过 |
| live owner/handle source scan | PASS |
| complexity gates | `USER-EXCLUDED / NOT RUN` |

未直接运行 `make check`，因为它固定包含本轮明确排除的 complexity health/ratchet；其余 lint、
typecheck、非 complexity 全量测试、coverage、build 和 package check 已分别执行。未运行 monorepo
`pre-commit --all-files`：当前 monorepo 有大量与本提交无关的用户修改和未跟踪文件，全树 hook
可能改写这些内容。

## 6. 最终裁决

```text
R-01 owner/handle boundary          = CLOSED
R-02 commit-origin cancellation     = CLOSED
R-03 plan-consumption invariant     = CLOSED
functional regression suite        = PASS
persistence/failover/ID recovery    = OUT OF SCOPE
complexity gate                     = USER-EXCLUDED
review result                       = PASS / READY TO MERGE
```

`d8bf2b1` 已满足当前授权范围的编码与合并条件。该批准不扩展到 persistence、failover、
child-ID-only recovery、跨 invocation child 控制或 compiled-child overlap gate。
