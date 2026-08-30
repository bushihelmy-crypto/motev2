# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案第二次评审回复

> **Disposition：SECOND REVIEW WRITEBACK COMPLETE / R8–R10 ACCEPTED / R11 PARTIALLY ACCEPTED AND CLOSED / KEEP CURRENT PUBLIC SURFACE / DOCS-ONLY / NO PRODUCTION AUTHORIZATION。**
>
> 二审提出的四个技术缺口已通过 docs-only writeback 关闭。对“必须把 review artifacts 追加进原 implementation manifest”“所有文档必须
> pairwise mutual-link”以及“把全量 production 门禁当作本轮 docs evidence”等过宽解释不予采纳；这些裁决不会改变二审冻结输入，也不会
> 授权源码、测试、State、Store、协议或执行路径修改。

## 1. 回复信息

- 日期：2026-08-26
- 第二次独立评审：[graph-outcome-result-error-surface-implementation-second-review.zh-CN.md](graph-outcome-result-error-surface-implementation-second-review.zh-CN.md)
- 第二次评审 SHA256：`4f30fcf23cb5009ee0ba5fcd46cb27891f0158395cdc66eaa411744414ae0c3f`
- 二审绑定的旧实施方案 SHA256：`be747243b7419604dc8d9cdffa268efbeb368a7aeaeaa043c6bcc3ac3866a5d6`
- 当前实施方案：[graph-outcome-result-error-surface-implementation.zh-CN.md](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 当前实施方案 writeback SHA256：`9ed092e6cdfb35de252c2fdf6511a3e54e80e11524ecde73ca269757cd2a2d02`
- 首次评审（冻结历史）：[graph-outcome-result-error-surface-implementation-review.zh-CN.md](graph-outcome-result-error-surface-implementation-review.zh-CN.md)，SHA256：`a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668`
- 首次评审回复（冻结历史）：[graph-outcome-result-error-surface-implementation-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-review-response.zh-CN.md)，SHA256：`efc3a8d5cdae46016626f9f4796da1a637f6396d302288e0fab34f0c6056b18b`
- 本文性质：二审 disposition / docs-only audit record；不拥有 production、tests、State、Store、API version 或 requirements 批准状态。
- 本文是本轮新增的 response artifact；二审评审文件保持冻结，不被本回复反向修改。

由于实施方案链接本文，本文只绑定实施方案的最终 snapshot SHA，不把本文自身 SHA 写回 target；这样可以避免互相引用造成自引用
digest。本文自身 SHA 由交付时的 `sha256sum` 命令独立计算，不作为 target 的输入。

## 2. 总体裁决

二审的 `CHANGES REQUESTED` 技术结论成立，但它同时明确了若干不应扩张的边界。最终裁决如下：

```text
R8 plain Graph.Values success lifecycle       = ACCEPTED / CLOSED
R9 exception owner split                      = ACCEPTED / CLOSED
R10 Error matrix and external-exception edge  = ACCEPTED / CLOSED
R11 docs-only closure                         = PARTIALLY ACCEPTED / CLOSED
public aliases / runtime / tests / State      = KEEP / NO CHANGE
production authorization                      = NONE
```

实施方案当前状态为 `CLOSED / docs-only / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION`。这表示文档事实和范围账本已经
闭合，不表示进行了任何 production implementation。

## 3. 逐项 disposition

| 二审 finding | Disposition | 实施方案处理 |
| --- | --- | --- |
| R8：生命周期图遗漏 plain `Graph.Values` success | **ACCEPTED** | 第 1、1.1、1.2、2、2.1、D2 统一写成 `Graph.Values | Graph.Outcome -> TaskResult -> GraphCommitResult -> Graph.Transition.result`；明确 plain values 直接投影为 `TaskSuccess`，不新增 `NodeReturn` 或 `PlainSuccessOutcome`。 |
| R9：exception hierarchy 的唯一 owner 表述不真实 | **ACCEPTED** | 第 2 节 owner 表拆为 shared taxonomy=`errors.py`、sealed `_PartialCommitError`=`result.py`、Graph aliases=`facade.py::Graph`；不移动 class、不建新 module、不复制 subclass。 |
| R10：Error matrix 混合相邻边界 | **ACCEPTED** | 第 3.2 节改为 internal exception classes；说明 `PlanningError` 是 intermediate base，分离 `ValuePublicationError` 与 `FrameInstallationInvariantError`，补足 `ExecutionLimitError` 的 fail-fast 参数校验和外部 exception 负边界。 |
| R11：closure 状态、链接和 evidence 冲突 | **PARTIALLY ACCEPTED / CLOSED** | 接受唯一 `CLOSED` 状态、exact directed link graph、baseline/current evidence 分层和 scoped manifest；不接受 pairwise mutual-link、把二审 artifacts 塞入原三文件 manifest 或把全量 production gate 冒充当前 docs evidence。 |

## 4. R8：plain-success 路径的 writeback

二审指出当前 `NodeCallable` 的真实契约是：

```python
Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]
```

该意见完全接受。实施方案现在固定以下链路：

```text
callable node return
  -> Graph.Values（plain success） | Graph.Outcome（explicit outcome）
  -> TaskResult
  -> GraphCommitResult
  -> Graph.Transition.result
```

`Graph.Values` 不会先被包装为 `Graph.Outcome`；scheduler 对 exact `_GraphValues` 直接创建 `TaskSuccess`。显式 failure/interrupt
则分别形成 `TaskFailure`/`TaskInterrupt`，再由既有 `_commit_result()` 投影为 transition result。这样既保留了完整合法路径，也没有
把历史概念词 `NodeOutcome` 变成新的 public union、wrapper 或 factory。

## 5. R9：owner 事实与不搬移 runtime

接受二审的三方 ownership：

| 事实 | owner |
| --- | --- |
| shared graph execution error taxonomy | `execution/errors.py` |
| sealed partial-prefix exception、state/continuation/cause 字段 | `execution/result.py::_PartialCommitError` |
| public Graph-namespaced aliases | `execution/facade.py::Graph` |

`Graph.PartialCommitError` 继续是现有 direct alias；它的 class 身份、seal、字段和 invocation-local confirmed-prefix 行为均保持不变。
本次没有移动 `_PartialCommitError`、新建 error module、增加 re-export、复制 subclass 或改写异常传播。

## 6. R10：Error matrix 的精确边界

接受并写回以下四项：

1. `PlanningError` 归入 internal exception classes，并注明它是 `SnapshotMismatchError`、`InvalidExecutionSnapshotError` 与
   `ExecutionLimitError` 的 intermediate base；
2. `Graph.ValuePublicationError` 只描述 graph-input、publication、resume-input、child-boundary coordinate 的 duplicate/collision
   以及 candidate/confirmed publication source 冲突；
3. `FrameInstallationInvariantError` 单独描述 confirmed successor 与 pre-admitted frame 安装不一致，不提升为 facade alias；
4. `Graph.ExecutionLimitError` 同时覆盖非法 public limit 参数的 fail-fast 拒绝和执行/恢复超过 explicit bound。

同时写入负边界：ordinary node callable、commit callback 或其他外部 capability 抛出的 exception 不会自动变成 `Graph.Error`，执行链
保留其 identity；只有已有 confirmed-prefix 场景按当前契约构成 `Graph.PartialCommitError`，原始异常位于 `cause`。因此调用方不能把
`except Graph.Error` 当作所有外部异常的总捕获器。

## 7. R11：closure ledger、manifest 与链接裁决

### 7.1 接受的 closure 要求

- 实施方案由 `REVISED/REQUIRED` 统一改为唯一当前状态 `CLOSED`；D1–D5 改为完成记录；
- 历史 `make check` 与 all-files pre-commit 标注为 2026-08-26 baseline snapshot，不冒充当前 docs-only authorization；
- 当前记录增加 directed link graph、target binding、EOF/whitespace、scoped pre-commit、link check 与负向 source/test manifest；
- 二审仍是独立 review-only unit，不修改二审历史，也不授权 production/tests/State/Store。

### 7.2 当前 exact directed link graph

```text
implementation
  -> first review, first response, second review, second response, normative source
first review
  -> implementation
first response
  -> implementation, first review
second review (frozen input)
  -> implementation, first review, first response
second response
  -> implementation, second review, first review, first response
```

“互链”在本回复中只表示上述可审计的有向边集合，不表示每一对文件都必须有反向链接。首次评审是冻结输入，不为补导航 backlink
而回写它或重新计算其历史 SHA。

### 7.3 manifest 与 evidence

首轮整改沿用的 normative implementation manifest 仍是：

```text
docs/graph-outcome-result-error-surface-implementation.zh-CN.md
docs/graph-outcome-result-error-surface-implementation-review.zh-CN.md
docs/graph-outcome-result-error-surface-implementation-review-response.zh-CN.md
```

二审 review 是冻结输入；本回复是独立 response artifact。实际 writeback 只修改实施方案 target，不吸收仓库中既有 README、其他
docs、源码或测试变更。

当前 target SHA 在本节 §1 绑定；二审旧 target SHA、首轮 review/response SHA 和二审 SHA 也全部保留，便于区分历史输入与最终 target。

## 8. 不采纳的意见与理由

以下明确记录本轮没有采纳的过宽建议；它们不改变已接受的 R8–R10 技术修正。

| 建议 | Disposition | 不采纳理由 |
| --- | --- | --- |
| 将二审 review 和二审 response 追加到原三文件 normative implementation manifest | **REJECTED** | review/disposition artifact 与 implementation target 的 owner 不同；追加会改变首轮已冻结的 manifest 语义，且二审明确要求 review-only unit 不纳入原 manifest。二审 response 通过独立 artifact 记录，不伪装成 target 文件。 |
| 为满足“互链”而回写首次评审，补 pairwise backlink 并重算历史 SHA | **REJECTED** | 首次评审是冻结输入；当前 directed link graph 已足够审计。为导航制造循环 writeback 会破坏历史绑定，不能增加技术证据。 |
| 把 `Graph.Values` 另命名为 public `NodeReturn`，或新增 `PlainSuccessOutcome`/wrapper | **REJECTED** | 这会重新引入二审禁止的第二 union/alias 和 construction path；现有 `NodeCallable` annotation 已精确表达合法返回。 |
| 为让 `except Graph.Error` 捕获外部 callable/callback 异常而新增包装路径或 facade aliases | **REJECTED** | `Graph.Error` 只拥有 graph-owned faults；保留外部 exception identity 和既有 `PartialCommitError.cause` 是当前契约，包装会改变异常语义并扩大 scope。 |
| 将 `FrameInstallationInvariantError` 提升为新的 public alias，或把它并入 `ValuePublicationError` | **REJECTED** | 两者分别属于 post-commit installation invariant 与 publication collision；合并会丢失现有 owner 和首错边界，且违反“不新增 alias”的冻结决定。 |
| 把全量 `make check`/all-files pre-commit 重新执行结果当作本轮 docs closure 的必要 production gate，或以全局 worktree clean 代替 scoped manifest | **REJECTED** | 源码基线已在本轮前通过；当前任务只改 docs，且仓库存在用户拥有的 unrelated dirty files。当前 closure 使用 scoped docs hooks、显式 EOF/whitespace/link 检查和负向 source/test manifest，足以证明本 change unit 范围，不冒充 production authorization。 |

## 9. 验证记录

本回复绑定的验证层级如下：

| 检查 | 裁决 |
| --- | --- |
| target/review/response directed links | 通过；所有相对 Markdown targets 存在 |
| target、review、response 的 EOF、mixed line ending、trailing whitespace | 通过 |
| scoped root `pre-commit`（适用 docs hooks） | 通过 |
| `git diff --check` | 通过 |
| `git diff --name-only -- src tests pyproject.toml CHANGELOG.md` | 空；无本 change unit 的 source/test/execution diff |
| `make check`、monorepo all-files pre-commit | 仅作为 2026-08-26 baseline snapshot；不作为本 docs-only unit 的 production 授权 |

## 10. 最终状态

```text
second review verdict                 = CHANGES REQUESTED / CLOSED BY WRITEBACK
R8                                     = ACCEPTED / CLOSED
R9                                     = ACCEPTED / CLOSED
R10                                    = ACCEPTED / CLOSED
R11                                    = PARTIALLY ACCEPTED / CLOSED
current public surface                = KEEP
production/tests/State/Store          = NO CHANGE
docs-only implementation              = CLOSED
production authorization              = NONE
future rename or new error alias      = SEPARATE CHANGE UNIT REQUIRED
```

本回复完成二审 disposition，并明确记录了不采纳项。后续若要引入 `Graph.NodeOutcome`、`Graph.RunResult`、新的 error alias、wrapper、
兼容路径或 persistence/failover 能力，必须按实施方案第 5.3 节另行立项、绑定新 target、重新评审并取得独立授权。
