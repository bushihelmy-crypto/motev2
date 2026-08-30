# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案第三次评审回复

> **Disposition：THIRD REVIEW WRITEBACK COMPLETE / R12–R15 ACCEPTED / KEEP CURRENT PUBLIC SURFACE / DOCS-ONLY / NO PRODUCTION AUTHORIZATION / INDEPENDENT ACCEPTANCE REQUIRED。**
>
> 本回复接受第三次评审的事实性整改，并把它们回写到唯一 implementation target；不采纳的过宽建议在第 6 节逐项记录。整个 change
> unit 不修改生产代码、测试、State、Store、protocol、persistence、failover、README 或历史评审稿。

## 1. 回复信息

- 日期：2026-08-26
- 当前 `HEAD`：`d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`
- 第三次独立评审：[graph-outcome-result-error-surface-implementation-third-review.zh-CN.md](graph-outcome-result-error-surface-implementation-third-review.zh-CN.md)
- 第三次评审 SHA256：`292522ee8a6a5aabb496e1f706af040dcbd2f8f6483d1b94f5395d7515d5c66a`
- 当前实施方案（唯一 target）：[graph-outcome-result-error-surface-implementation.zh-CN.md](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 本次回写后的 target SHA256：`5aebe973409b6bdd97aee41cb8fbf40f6baa933ecc51368403bf5c2254b8559a`
- 二审评审（冻结历史）：[graph-outcome-result-error-surface-implementation-second-review.zh-CN.md](graph-outcome-result-error-surface-implementation-second-review.zh-CN.md)，SHA256：`4f30fcf23cb5009ee0ba5fcd46cb27891f0158395cdc66eaa411744414ae0c3f`
- 二审回复（冻结历史）：[graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md](graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md)，SHA256：`4676194388ff428e1f4aed571bd2b83cd7f251f03b772fd4c90271effbe43ba6`
- 首次评审/回复（冻结历史）：`a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668` /
  `efc3a8d5cdae46016626f9f4796da1a637f6396d302288e0fab34f0c6056b18b`
- 本文性质：第三次 disposition / docs-only audit record；本文不拥有 public API、production、tests、State、Store、API version 或
  requirements 批准状态。

`T0` 实施方案链接本文，但不嵌入本文自身 hash；这样避免 target/response 互相引用造成自引用 digest。本文自身 hash 由交付时独立
执行 `sha256sum` 计算。规范 API 事实源仍是
[`graph-node-input-output-contract-implementation.zh-CN.md`](graph-node-input-output-contract-implementation.zh-CN.md) 与现有 owner 代码。

## 2. 总体裁决

第三次评审的 R12–R15 均接受为 docs-only 精度整改：

```text
R10 ValuePublicationError / external exception wording = ACCEPTED / WRITTEN BACK
R12 round-and-role manifest ledger                   = ACCEPTED / WRITTEN BACK
R13 negative-scope command coverage                  = ACCEPTED / WRITTEN BACK
R14 reproducible docs evidence                       = ACCEPTED / WRITTEN BACK
R15 existing scheduler.NodeReturn wording             = ACCEPTED / WRITTEN BACK
public surface / owner / sealed construction          = KEEP
production / tests / State / Store / protocol         = NO CHANGE
persistence / failover / retry / second runner        = NOT IN THIS CHANGE UNIT
final no-finding closure                              = INDEPENDENT FOLLOW-UP REVIEW REQUIRED
```

因此，实施方案状态从不恰当的 `CLOSED` 收窄为“R12–R15 writeback complete，independent acceptance pending”。这不是回退技术整改，
而是避免 target 自己宣称尚未由独立 review 持有的最终 `PASS/CLOSED`。

## 3. 逐项接受并回写

| finding | Disposition | target writeback |
| --- | --- | --- |
| R10：`ValuePublicationError` 边界过宽，顶层 exception 图遗漏 external identity | **ACCEPTED** | 第 1 节把生命周期图收窄为 graph-owned fault -> `Graph.Error`；external callable/callback/capability exception 保留原 identity。第 3.1 节限定 `ScopedFrameIndex.add_*()` 四类 coordinate duplicate、resume substitution duplicate/collision；post-commit installation 冲突由 `invocation.py` 转换为 `FrameInstallationInvariantError`。 |
| R12：manifest 的时间/职责 owner 不唯一 | **ACCEPTED** | 第 5.1 节新增 `T0/R1/A1/R2/A2/R3/A3` ledger，明确 exact path、owner、冻结输入/current writeback 与 normative target manifest 归属；当前唯一 target 是 `T0`。 |
| R13：negative scope 未覆盖 index/untracked/monorepo sibling | **ACCEPTED** | 第 7.2 节记录从 monorepo root 运行的 `status`、unstaged diff、staged diff、untracked 列举四件套，并将结论限定为 change-unit attribution，不声称全局 clean。 |
| R14：docs evidence 缺 exact command/cwd/time/hash binding | **ACCEPTED** | 第 7 节分开 historical production baseline 与 current docs-unit verification，记录命令、cwd、scope、HEAD/target/review hash 绑定；显式 scanner 覆盖 untracked docs，`git diff --check` 只作 tracked 补充。 |
| R15：现有 internal `NodeReturn` 表述易与源码冲突 | **ACCEPTED** | 第 2.1 节明确保留 `scheduler.NodeReturn` internal `TypeAlias`，仅禁止新增 `Graph.NodeReturn`、`Graph.NodeOutcome`、`PlainSuccessOutcome`、wrapper 或 factory。 |

## 4. 保持的唯一真相与最合适范围

本轮只在实施文档中复用已有 owner 和证据，不复制 runtime 定义：

- `execution/graph/values.py`、`execution/graph/outcome.py` 继续拥有 callable values/outcome factories 与 seals；
- `execution/result.py` 继续拥有 `TaskResult`、`GraphCommitResult`、run projection 和 sealed `_PartialCommitError`；
- `execution/family_driver.py` 继续拥有 transition/exact successor contract；
- `execution/errors.py` 继续拥有 shared graph execution taxonomy；
- `execution/facade.py::Graph` 继续是唯一 public namespace。

不新增 public alias、DTO、tag、registry、wrapper、compatibility path、第二 runner、异常包装路径或持久化/failover/retry 协议。`Graph.Transition.result`
仍是 callback 前的 admitted candidate evidence，不是 receipt；`Graph.Result` 也不承诺跨进程 durable snapshot。

## 5. R12–R14 的证据和 scope 处理

### 5.1 唯一 ledger

`T0` 是唯一 current normative target。`R1`/`R2`/`R3` 是冻结的 review-only inputs，`A1`/`A2`/`A3` 是独立 response-only records；
它们通过 target 中的 directed link graph 和 hash binding 提供审计导航，但不会被拼成一个“规范 implementation manifest”。历史文件不回写，
也不为 pairwise backlink 重算历史 hash。

### 5.2 negative scope 的归因规则

从 `/home/longert/motev2` 运行第 7.2 节四条 exact scoped commands 后，结论只能是：
`本 change unit 无 source/test/State/Store/protocol/persistence/execution-path diff`。若发现 README、其他 docs 或 sibling persistence
等用户已有 dirty path，它们单独标为 unrelated baseline；本回复不清理、不重置、不覆盖这些文件，也不把“空输出”外推成全局 worktree clean。

### 5.3 evidence 的层级

`make check` 与 monorepo 全量 pre-commit 的既有结果只作为 historical production baseline；它们不是本 docs-only unit 的授权条件。current
docs-unit 只需复跑 link/EOF/CRLF/trailing-whitespace scanner、适用 docs hooks、四条 scoped git commands、`git diff --check` 和 hashes，
并记录执行时间、cwd、scope、退出码/摘要。未跟踪文件不由 `git diff --check` 代替，必须显式扫描。

## 6. 不采纳的意见与理由

以下意见没有采纳，因为它们会把本次 docs-only 精度整改扩大成新的事实源、全局清理或 runtime 变更：

| 建议 | Disposition | 理由 |
| --- | --- | --- |
| 将全部 review/response 文件追加为 normative implementation manifest | **REJECTED** | review/disposition 与 target 的 owner 不同；ledger 已提供唯一 target 和完整审计关系，追加会模糊时间边界并制造第二事实源。 |
| 为 pairwise backlink 回写/重算冻结历史 review 和 response | **REJECTED** | 有向链接图已足够复算；冻结历史的 hash 是审计输入，导航便利不能证明技术正确性，不能破坏历史绑定。 |
| 要求全局 worktree clean，或清理 README/其他 docs/sibling persistence dirty files | **REJECTED** | 这些是用户已有改动，不在授权范围；本任务只证明 scoped attribution，不能以破坏用户变更换取“clean”。 |
| 把 `make check`/全量 pre-commit 作为本轮 docs-only 的 production authorization | **REJECTED** | 它们只能说明历史源码 baseline 健康；本 change unit 不授权 production/tests/State/Store，docs evidence 与 production gate 必须分层。 |
| 新增 public `Graph.NodeReturn`/wrapper/DTO，或把 external exception 包装成 `Graph.Error` | **REJECTED** | 现有 `scheduler.NodeReturn` internal alias 与 `Graph.Values | Graph.Outcome` 已是唯一合法路径；包装/alias 会破坏 owner、identity 和零负债目标。 |
| 新增 Store、journal、checkpoint、failover、retry、第二 runner 或跨进程 value recovery | **REJECTED** | 明确超出本 change unit；现有 candidate/confirmation 语义只读复用，不引入持久化或恢复协议。 |

## 7. 当前验证记录

以下表格在 target 回写后复跑；它只证明 docs snapshot 的可交付性，不授予 production authorization。命令正文、cwd 和 exact path list
同步写入 target §7.2；本批次完成时间为 `2026-08-26T20:40:09+08:00`（cwd=`/home/longert/motev2`，`HEAD` 为
`d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`）。

| 检查 | 结果 |
| --- | --- |
| directed Markdown links + EOF/CRLF/trailing whitespace | **PASS**：exact scanner 输出 `files=8 links=31 errors=0`，exit `0` |
| scoped root pre-commit | **PASS**：适用 docs hooks 通过，源码 hooks 因路径过滤 skipped，exit `0` |
| tracked/index/untracked negative scope | **PASS（change-unit attribution）**：status/unstaged 只显示用户已有的 `mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed`；staged 与 scoped untracked 均为空，四条命令 exit `0` |
| `git diff --check` | **PASS**：exit `0`；与 untracked 显式 scanner 分开 |
| `HEAD`、target、review、normative source hashes | **PASS**：`HEAD=d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`；target=`23884ae5453b1086a61ea863e6b353c357fffd5c1ae48b9248d703ee1661a479`；R3=`292522ee8a6a5aabb496e1f706af040dcbd2f8f6483d1b94f5395d7515d5c66a`；normative source=`233ba6be90d9ae3d7d7c3817c584ca44dfd2d9a76dff3a36968cbda136043f09` |

## 8. 最终状态

```text
R10 / R12 / R13 / R14 / R15       = ACCEPTED / WRITTEN BACK
current public surface             = KEEP
canonical owner / infrastructure   = REUSE EXISTING ONLY
production/tests/State/Store       = NO CHANGE
persistence/failover               = NOT IN THIS CHANGE UNIT
docs-only target writeback         = COMPLETE
independent no-finding acceptance  = REQUIRED
production authorization            = NONE
```

本回复明确完成第三次 disposition，并把不采纳项留在独立 response 文档中。后续独立验收若发现新 finding，只能新增 review/response record；
不得回写冻结历史，也不得借此引入 public rename、wrapper、异常包装、持久化或 failover。
