# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案第四次独立验收

> **结论：`PASS / FINDINGS = 0 / R10、R12–R15 RESOLVED / KEEP CURRENT PUBLIC SURFACE / DOCS-ONLY / NO PRODUCTION AUTHORIZATION`。**
> 本记录只验收已完成的 docs-only writeback；不回写 target，不扩大 production、tests、State、Store、protocol、persistence 或 legacy test 范围。

## 1. 验收对象与冻结输入

- 验收日期：2026-08-27
- implementation target：[graph-outcome-result-error-surface-implementation.zh-CN.md](graph-outcome-result-error-surface-implementation.zh-CN.md)
- target SHA256：`5aebe973409b6bdd97aee41cb8fbf40f6baa933ecc51368403bf5c2254b8559a`
- 第三次独立评审：[graph-outcome-result-error-surface-implementation-third-review.zh-CN.md](graph-outcome-result-error-surface-implementation-third-review.zh-CN.md)，SHA256：`292522ee8a6a5aabb496e1f706af040dcbd2f8f6483d1b94f5395d7515d5c66a`
- 第三次评审回复：[graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-third-review-response.zh-CN.md)，SHA256：`1bde08af0f805af2e5f990b4607f0f8779a7d1a843c06c4b8a39980e28a35a25`
- normative source：[graph-node-input-output-contract-implementation.zh-CN.md](graph-node-input-output-contract-implementation.zh-CN.md)，SHA256：`233ba6be90d9ae3d7d7c3817c584ca44dfd2d9a76dff3a36968cbda136043f09`
- 当前源码 `HEAD`：`d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`

本记录是一个独立的单文件 review unit，只拥有最终验收裁决。`T0` target、R1–R3 review/response 历史文件和 normative source 均保持冻结；本记录不要求 target 反向加入导航链接，因此不改变 target SHA，也不制造循环 hash 依赖。

## 2. 最终复核矩阵

| 维度 | 结果 | 复核结论 |
| --- | --- | --- |
| callable / outcome / task / commit / run 生命周期 | **PASS** | 合法 callable 返回固定为 `Graph.Values | Graph.Outcome`；plain values 由 scheduler 直接投影为 `TaskSuccess`，显式 outcome 再进入 `TaskResult -> GraphCommitResult -> Graph.Transition.result`；`Graph.Result` 独立表示 run disposition。 |
| canonical owner 与基础设施复用 | **PASS** | values/outcome 由 `execution/graph/values.py`、`outcome.py` 持有；task/commit/run result 由 `execution/result.py` 持有；transition/commit contract 由 `family_driver.py` 持有；shared error taxonomy 由 `errors.py` 持有；唯一 public namespace 仍是 `facade.py::Graph`。 |
| public Error surface | **PASS** | `Graph` aliases 与 canonical classes 保持 direct identity；`_PartialCommitError` 继续由 `result.py` sealed 持有；internal leaves 不提升为新 alias；外部 callable/callback/capability exception 保留原 identity。 |
| candidate / confirmation 边界 | **PASS** | `_commit_result()` 在可选 callback 前构造；`Graph.Transition.result` 是 admitted candidate evidence，不是 receipt；`commit=None` 不被文档虚构为 external confirmation。 |
| 唯一真相与零新增负债 | **PASS** | 未新增 `Graph.NodeOutcome`、`Graph.RunResult`、`Graph.SettledNodeResult`、wrapper、DTO、tag、registry、factory、exception、compatibility path、第二 runner 或隐藏状态；没有 legacy test 改动。 |
| R12–R15 closure 与可复算证据 | **PASS** | target 已有按轮次/职责 ledger；negative scope 同时覆盖 worktree、index、untracked 与 monorepo sibling；current docs evidence 记录 exact command、cwd、scope、时间与 hash。 |

## 3. Owner、类型与错误边界复核

### 3.1 唯一生命周期链

当前源码中的实际链路为：

```text
NodeCallable return
  -> _GraphValues | GraphOutcome
  -> TaskSuccess / TaskFailure / TaskInterrupt
  -> GraphCommitResult
  -> GraphTransition.result

family-driver boundary
  -> GraphResult
  -> Graph.run() return
```

`Graph.Values` 不先包装成 `Graph.Outcome`；`Graph.Outcome` 只承载显式 success/failure/interrupt。`Graph.Transition.result` 在 start、claim、fence、resume、resolve、abort 等无节点结算的 transition 上保持 `None`，与 target 的分层描述一致。

### 3.2 Owner 与 public identity

源码静态复核和 runtime identity probe 得到：

```text
Graph.Values             is _GraphValues             = True
Graph.Outcome            is GraphOutcome             = True
Graph.SuccessOutcome     is _GraphSuccessOutcome     = True
Graph.FailureOutcome     is _GraphFailureOutcome     = True
Graph.InterruptOutcome   is _GraphInterruptOutcome   = True
Graph.SuccessResult      is _GraphSuccessResult      = True
Graph.FailureResult      is _GraphFailureResult      = True
Graph.InterruptResult    is _GraphInterruptResult    = True
Graph.Transition         is GraphTransition          = True
Graph.Result             is GraphResult              = True
Graph.Error              is ExecutionError           = True
Graph.PartialCommitError is _PartialCommitError      = True
```

`execution.__all__` 仍精确为 `['Graph']`；`Graph` 上不存在 `NodeOutcome`、`RunResult`、`SettledNodeResult`、`NodeReturn` 或 `PlainSuccessOutcome`。现有 `scheduler.NodeReturn` 仍是 internal alias，target 对此已有明确限定。

### 3.3 Error 负边界

target 的 Error matrix 与源码一致：`Graph.ValidationError`、`SnapshotMismatchError`、`ExecutionLimitError`、`RoutingError`、value admission/unavailability/publication aliases 保持现有 direct alias；`FrameInstallationInvariantError` 和其他 internal leaves 不进入 facade。`ValuePublicationError` 的描述限定在 typed frame coordinate duplicate、resume substitution duplicate/collision 和相应 publication phase；post-commit installation 冲突由既有 `FrameInstallationInvariantError` 处理。普通外部异常不自动包装为 `Graph.Error`，confirmed-prefix 场景才按既有契约形成 `Graph.PartialCommitError`。

## 4. 范围与变更归因

```text
review unit                     = 本文件单文件
target writeback                = none
production manifest             = empty
tests / typing fixtures         = empty
State / Store / protocol        = empty
persistence / failover / retry = empty
legacy test scope               = unchanged
```

从 monorepo root 对以下 exact path 做了 tracked、staged、untracked 分层核对：

```text
mote-kernel/src/**
mote-kernel/tests/**
mote-kernel/pyproject.toml
mote-kernel/CHANGELOG.md
mote-infra/persistence/**
conformance/**
```

唯一输出是既有用户 dirty baseline：`mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed`；它不属于本 review unit，未清理、未覆盖、未归因给本次文档验收。源码、tests、State、Store、protocol、persistence 与执行路径没有本 unit 的差异。

## 5. 当前快照验证

以下命令均在 2026-08-27 当前快照执行；本批次起始时间为 `2026-08-27T10:42:30+08:00`（cwd=`/home/longert/motev2/mote-kernel`）；review record 只记录结果，不把历史绿色门禁转写成 production authorization。

| 检查 | exact scope / 结果 |
| --- | --- |
| `make check` | cwd=`/home/longert/motev2/mote-kernel`；PASS：Ruff、format、Pyright `0 errors`、complexity `9 passed`、health `51 reviewed / 0 unreviewed / 0 stale`、全量 `850 passed`、coverage `100.00%`、build 与 twine check 通过。 |
| Markdown link/EOF/CRLF/trailing-whitespace scanner | cwd=`/home/longert/motev2`；8 个 outcome/result/error 主题文档 + 1 个 normative source；输出 `files=9 links=35 errors=0`，exit `0`。 |
| root scoped `pre-commit run --files` | 同上 9 个 exact Markdown paths；适用 docs hooks 全部通过，源码 hooks 因路径过滤 skipped，exit `0`。 |
| tracked/index/untracked negative scope | cwd=`/home/longert/motev2`；四条 `status`/unstaged `diff`/staged `diff`/`ls-files --others` exact pathspec 命令均 exit `0`；除既有 sibling `py.typed` 外无本 unit source/test/State/Store/protocol/persistence diff。 |
| `git diff --check` 与 `git diff --cached --check` | cwd=`/home/longert/motev2/mote-kernel`；均 exit `0`；untracked Markdown 另由显式 scanner 覆盖。 |
| public identity / forbidden-name probe | cwd=`/home/longert/motev2/mote-kernel`；全部 listed identity 为 `True`，forbidden Graph attributes 为空，`execution.__all__ == ['Graph']`。 |
| snapshot hashes | `HEAD=d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`；target/R3/A3/normative source 分别为第 1 节 hashes；本 review 自身 hash 在交付时独立计算，不回写 target。 |

`make check` 仅证明未修改源码基线健康；本验收的授权结论仍是 docs-only、无 production/tests/State/Store/protocol/persistence 变更。未新增、未执行、未扩大任何 legacy 测试范围。

## 6. 最终裁决与终止规则

```text
blocker = 0
major = 0
minor = 0

R8 / R9 / R10                       = RESOLVED / CLOSED
R12 / R13 / R14 / R15               = RESOLVED / CLOSED
canonical owner / infrastructure   = PASS / EXISTING ONLY
public surface                      = KEEP
new alias / wrapper / second path   = NONE
persistence / failover / retry      = NOT IN THIS UNIT
production / tests / legacy scope    = NO CHANGE / NO AUTHORIZATION
docs-only target writeback           = COMPLETE
independent acceptance               = PASS
```

最终 `PASS` 由本独立 review record 单独持有。按既有终止规则，不再为了复述 PASS 回写 target、历史 review/response 或 normative source；若未来要引入 public rename、兼容层、异常包装、持久化或 failover，必须另立有版本边界的 change unit 并重新评审。

## 7. 本次 change unit

```text
mote-kernel/docs/graph-outcome-result-error-surface-implementation-fourth-review.zh-CN.md
```

除本文件外未修改任何文件。
