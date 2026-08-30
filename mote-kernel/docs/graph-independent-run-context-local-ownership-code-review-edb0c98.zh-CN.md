# `edb0c98` 父子图 GraphRun 本地 ownership 再次验收

> **结论：`CHANGES REQUESTED / NOT READY TO MERGE`。**
> **相对 `18d37c3` 已从 2 个 P1 blocker 收敛为 1 个：R-02 已关闭，R-01 部分关闭。**

本次以 `18d37c3..edb0c98` 为固定评审范围，继续执行 GRC-LO-001 的窄目标：每个
`GraphRun` 只拥有自己的 identity/state/frame/session/commit；parent 只持 opaque child
handle 并接收 typed result；不新增 persistence、failover、child-ID-only recovery、registry、
第二 runner 或兼容路径。复杂度门禁按用户指示排除，不参与本次裁决。

## 1. 评审对象

| 项目 | 值 |
| --- | --- |
| reviewed commit | `edb0c98e49a44f9c94ac9cae787f65038e245060` |
| baseline | `18d37c3848c859c39e348e83df9613cf7c7e748f` |
| implementation SHA256 | `113f7bda4ed7423a67cf1b14a881563a31802f4bcaae0750e2300e2d6feeeb0d` |
| requirements SHA256 | `1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6` |
| review date | `2026-08-30` |

提交修改 4 个 production 文件、1 个配置文件和 6 个测试文件，共 `965 insertions / 623
deletions`。production 文件没有未提交覆盖；工作树中的 README、其他文档和 monorepo 文件
改动均视为用户已有内容，本次未修改。

## 2. 上轮 blocker 复核

| finding | 状态 | 结论 |
| --- | --- | --- |
| R-01：child construction/transition/admission 尚未落到 child owner | **PARTIAL** | fresh child 的 identity/start 已进入独立 construction frame；terminal coordinate 由 child 校验；fence/resume commit 也改由 `_GraphRun` 执行。但 continuation setup 又建立了完整 child owner sequence，并按 child coordinate 直接路由 owner，仍有 handle 之外的第二条 family 控制路径。 |
| R-02：commit-origin `CancelledError` 被误判为 caller cancellation | **CLOSED** | commit task cancellation 现在有 owner-local one-shot marker；root、child start/claim、continuation fence/resume 均不会再从未确认 snapshot 猜测 abort。 |

## 3. 唯一阻塞问题

### R-01（P1）continuation setup 保留了 owner sequence 和 coordinate-based coordinator

**位置：**

- `src/mote_kernel/execution/family_driver.py:951-1076`
- `src/mote_kernel/execution/family_driver.py:1136-1196`
- `src/mote_kernel/execution/family_driver.py:1258-1319`
- `src/mote_kernel/execution/family_driver.py:925-948`

fresh 路径已经符合目标：`_construct_fresh_child()` 在自己的 frame 内派生 identity、提交
`StartGraphRun`、构造 child owner，并只向 parent 返回三能力 opaque handle。

但 existing/continuation 路径仍另外建立了一条 live owner 通道：

1. `_construct_existing_child()` 除 opaque handle 外，还返回 `(child, *descendants)` 的
   `_GraphRun` tuple；
2. `_admit_existing_children()` 聚合并排序全部 descendant owners；
3. `_construct_continued_root()` 返回 `(root, *descendants)` 的完整 owner sequence；
4. `admit_continued_root()` 遍历该 sequence，读取每个 `owner.coordinate`，按 coordinate 查找
   fence/resume plan，再直接调用 `owner.apply_admission_fence/resume()`；
5. owner 尚未构造时，`_abort_unowned_graph_runs()` 还会遍历 family bindings，并用
   `binding.coordinate/state` 直接提交 child abort。

因此，parent `_GraphRun._children` 本身虽然只保存 opaque handle，但 invocation setup 同时持有
child owner 引用、child identity 和 family-wide transition routing。它是 handle 之外的第二条
控制路径，也正是冻结规范明确要求删除的 owner sequence/invocation coordinator。requirements
3.5 还明确规定：即使只在当前 invocation 内，coordinator 也不得把 child
`run_id`/coordinate 展开成编排字段。

这不是 persistence 或跨 invocation recovery 问题，也不是复杂度问题；它直接影响“每个图各管
各的 run_id/state，父子只通过 opaque handle/typed result 传播”是否在代码 ownership 上成立。

**最小闭合方向：**

- continuation adapter 只把某个 binding/frame 及其精确 local fence/resume plan 交给对应的
  construction frame；
- child construction frame 在 handoff 前让该 child owner 自己完成自己的 admission
  transition；
- `_construct_existing_child()` 只返回 opaque handle，递归 admission 不再返回 owner tuple；
- `_construct_continued_root()` 只返回既有 `OwnerHandoff`，删除 owner sequence、
  `_validate_owner_transition_plans()` 和按 `owner.coordinate` 的两轮调度；
- setup cleanup 由当前 candidate construction frame 和已经交接的 handles 负责，删除基于
  family bindings 直接代写 child state 的 `_abort_unowned_graph_runs()` 路径。

无需扩大到 Store/load、持久化、failover、registry、公共 API 或新 handle capability。

## 4. 已确认关闭与保持项

- fresh child identity/start/commit 已不由 parent `_GraphRun` 执行，factory 只返回 opaque handle；
- child terminal boundary 的 parent activation/coordinate 校验由 child owner 完成；
- child fence/resume 的 reducer candidate、exact commit 和 local frame 安装均在对应
  `_GraphRun` 内完成；
- commit-origin cancellation 在 root claim、child start、child claim、continuation child fence
  和 child resume 场景均不触发 stale abort；
- caller cancellation 仍等待 exact acknowledgement，再按 confirmed owner state child-first
  abort/release；
- State/reducer、Graph public API、continuation/frame ABI、Store/commit protocol 未新增字段或
  variant；
- 不要求 persistence、failover、overlap gate 或凭 child ID 跨 invocation 恢复。

## 5. 检查结果

| 检查 | 结果 |
| --- | --- |
| `git diff --check 18d37c3 edb0c98` | PASS |
| 非 complexity 全量测试 + coverage | PASS：`894 passed`，`100.00%` |
| ownership/cancellation/partial-confirmation 定向测试 | PASS：`10 passed` |
| continuation child fence/resume strict-CAS cancellation 验收探针 | PASS：`2 passed` |
| `python -B -m ruff check src tests` | PASS |
| `python -B -m ruff format --check src tests` | PASS：154 files |
| `pyright` | PASS：0 errors |
| build + `twine check` | PASS：sdist/wheel 均通过 |
| owner/handle source ownership scan | **FAIL：仍存在 `_GraphRun` owner sequence 与 coordinate router，见 R-01** |
| complexity gates | `USER-EXCLUDED / NOT RUN` |

未直接运行 `make check`，因为它必然包含本轮明确排除的 complexity health/ratchet；其余 lint、
typecheck、非 complexity 全量测试、coverage、build 和 package check 已分别执行。未运行 monorepo
`pre-commit --all-files`：当前 monorepo 存在大量与本提交无关的用户修改和未跟踪文件，全树 hook
可能改写这些内容。

## 6. 裁决

```text
R-01 owner/handle boundary          = PARTIAL / P1 BLOCKER
R-02 commit-origin cancellation     = CLOSED
functional regression suite        = PASS
persistence/failover/ID recovery    = OUT OF SCOPE
complexity gate                     = USER-EXCLUDED
review result                       = CHANGES REQUESTED / NOT READY TO MERGE
```

本轮没有发现第二个新的独立 blocker。完成 R-01 所述的单一路径收口后，可以再次做最终验收。
