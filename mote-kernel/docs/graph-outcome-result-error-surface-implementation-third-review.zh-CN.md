# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案第三次独立复核

> **结论：`CHANGES REQUESTED / R8–R10 技术整改复核通过 / R11 技术方向保持但 closure 证据未闭合 / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION`。**
> 当前实施方案已经正确补齐 plain `Graph.Values` success 路径、三方 exception owner 和主要 Error 边界；本轮没有发现需要修改源码、测试、State、Store 或执行路径的技术问题。
> 但 `CLOSED` 仍不能作为最终文档 closure：manifest 的时间/职责边界、负向 scope 证据的覆盖范围和验证记录的可复现性尚不精确，且 `ValuePublicationError`/既有 internal `NodeReturn` 仍有两处容易误读的表述。

## 1. 评审对象与冻结版本

- 评审日期：2026-08-26
- 评审对象：[Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 当前实施方案 SHA256：`9ed092e6cdfb35de252c2fdf6511a3e54e80e11524ecde73ca269757cd2a2d02`
- 第二次独立评审：[第二次独立评审](graph-outcome-result-error-surface-implementation-second-review.zh-CN.md)，SHA256：`4f30fcf23cb5009ee0ba5fcd46cb27891f0158395cdc66eaa411744414ae0c3f`
- 第二次评审回复：[第二次评审回复](graph-outcome-result-error-surface-implementation-second-review-response.zh-CN.md)，SHA256：`4676194388ff428e1f4aed571bd2b83cd7f251f03b772fd4c90271effbe43ba6`
- 首次评审（冻结历史）：[首次评审](graph-outcome-result-error-surface-implementation-review.zh-CN.md)，SHA256：`a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668`
- 首次评审回复（冻结历史）：[首次评审回复](graph-outcome-result-error-surface-implementation-review-response.zh-CN.md)，SHA256：`efc3a8d5cdae46016626f9f4796da1a637f6396d302288e0fab34f0c6056b18b`
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)，当前 SHA256：`233ba6be90d9ae3d7d7c3817c584ca44dfd2d9a76dff3a36968cbda136043f09`
- 当前源码 `HEAD`：`d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5`；实施方案所称 `563a45124311f11e870d0627461102baeffdf7ad` 是较早的历史 baseline，不与当前 `HEAD` 混同。
- 本文性质：第三次独立 docs-only review record；只拥有本轮裁决、发现和验证记录，不拥有 implementation target、normative source、批准状态或 production/test shape。
- 本轮 change unit：只新增本文；不回写实施方案、二审评审、二审回复、源码、测试、README、State、Store、protocol、persistence 或执行路径。

评审按既有硬边界进行：保留现有 public aliases 和唯一 `Graph` facade；不新增 `NodeOutcome`、`SettledNodeResult`、`RunResult`、error alias、wrapper、DTO、Store、runner、兼容路径或持久化/failover 协议。

## 2. 总体裁决

| 评审维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| callable / outcome / task / commit / run 生命周期 | **PASS** | `Graph.Values | Graph.Outcome -> TaskResult -> GraphCommitResult -> Graph.Transition.result` 与 `Graph.Result` 分层现在完整；plain values 直接投影为 `TaskSuccess`。 |
| canonical owner 与 sealed construction | **PASS** | `outcome.py`、`result.py`、`family_driver.py`、`errors.py`、`facade.py` 的职责与源码一致；不需要搬移或复制 runtime。 |
| public Error surface | **PASS WITH WORDING FOLLOW-UP** | public aliases、internal classes、`Graph.Error` 负边界已基本正确；`ValuePublicationError` 的 source/phase 描述仍需再收窄。 |
| public rename / persistence / failover | **PASS / KEEP** | 不新增 alias、wrapper、Store、State 字段、第二 runner 或恢复协议。 |
| manifest 与 scope closure | **CHANGES REQUESTED (R12–R14)** | 原三文件历史 manifest、当前 response artifact 和本次 review unit 没有用独立账本明确分开；负向 diff 命令也不能证明其声称的全部路径。 |
| evidence reproducibility | **CHANGES REQUESTED (R14)** | 结论写成 `COMPLETE`，但缺 exact command、scope、时间和当前 HEAD/output 绑定；未跟踪 docs 不受 `git diff --check` 覆盖。 |
| production/tests/State/Store authorization | **NONE** | 本轮不授权任何 production、tests、State、Store、protocol、persistence 或执行路径修改。 |

## 3. R8–R11 技术整改复核

### 3.1 R8：plain `Graph.Values` success 路径已闭合

当前 `src/mote_kernel/execution/graph/node.py::NodeCallable.__call__` 的真实契约是：

```python
_GraphValues[GraphValueT] | GraphOutcome[GraphValueT]
```

`src/mote_kernel/execution/engine/scheduler.py::_project_outcome()` 对 exact `_GraphValues` 直接建立 `TaskSuccess`，对显式 outcome 分别建立 success/failure/interrupt task result；`result.py::_commit_result()` 再投影为 commit result。实施方案第 1、1.2、2、2.1 和 D2 已统一表达该链路，没有把 plain values 包装成 outcome，也没有新增 public `NodeReturn` 或 `PlainSuccessOutcome`。

**裁决：R8 RESOLVED / CLOSED。**

### 3.2 R9：exception owner split 与源码一致

当前三方 ownership 可由源码直接复核：

| 事实 | owner |
| --- | --- |
| shared execution exception taxonomy | `src/mote_kernel/execution/errors.py` |
| sealed partial-prefix exception、`state`/`continuation`/`cause`/`failed_scope` | `src/mote_kernel/execution/result.py::_PartialCommitError` |
| Graph-namespaced public aliases | `src/mote_kernel/execution/facade.py::Graph` |

`Graph.PartialCommitError is _PartialCommitError`、private seal 和现有 invocation-local propagation 均未被方案要求迁移。该部分与二审要求一致。

**裁决：R9 RESOLVED / CLOSED。**

### 3.3 R10：主要 Error 边界已正确，但 wording 仍有一项待收口

以下内容已与 `errors.py`、`resume_admission.py`、`run_context.py` 和 `invocation.py` 对齐：

- `PlanningError` 被标为 internal intermediate base，而非 leaf；
- `ExecutionLimitError` 同时覆盖 limits 参数 fail-fast 与 planner/recovery 的 explicit bound；
- `FrameInstallationInvariantError` 与 `GraphValuePublicationError` 分开；前者负责 exact commit 后的 pre-admitted frame installation invariant；
- ordinary callable、commit callback 和其他 external capability 的异常不自动包装成 `Graph.Error`，已有 confirmed-prefix 场景才按既有契约形成 `Graph.PartialCommitError`。

然而，`ValuePublicationError` 行仍把语义写成“candidate/confirmed publication source 冲突”。源码能证明的窄边界是：

1. `ScopedFrameIndex.add_*()` 对四类 frame coordinate 的重复加入抛出 `GraphValuePublicationError`；
2. `resume_admission.py` 对重复 resume substitution publication coordinate，或 substitution coordinate 与**已确认 publication** 冲突抛出该错误；
3. post-commit installation 阶段若同一冲突被捕获，会由 `invocation.py` 转换为 `FrameInstallationInvariantError`。

“publication source conflict”若不限定为 resume substitution/candidate coordinate，调用方会误以为所有 candidate/confirmed 安装失败都应捕获 `ValuePublicationError`。应在下一次 target writeback 中改为上述 coordinate/phase 精确措辞，并把顶层“任何阶段都可能抛出 `Graph.Error`”改成“graph-owned fault 可能抛出 `Graph.Error`；外部异常保留其自身 identity”，与第 3.2 节负边界完全一致。

**裁决：R10 技术上 RESOLVED；wording follow-up OPEN（MINOR）。**

### 3.4 二审回复中的不采纳项复核

二审回复所列“不采纳”项与二审原文的边界总体对应，未发现借回复偷偷扩大 runtime scope 的情形：不把二审 artifacts 追加进首轮 manifest、不为冻结首轮评审补 pairwise backlink、不新增 `NodeReturn`/wrapper、不包装外部 exception、不提升 `FrameInstallationInvariantError`，以及不把全量 production gate 当作 docs-only authorization，均与二审 R11 的 review-only 和 no-production 约束一致。本轮没有把这些裁决重新打开；R12–R14 只要求把其历史职责、证据范围和 current unit 记录得可复算。

## 4. 新增 findings

### R12 — MAJOR：manifest 的历史范围、当前 writeback 和 review artifact 没有唯一可复算的 owner

实施方案第 5.1 节以“本 change unit 的文件范围”列出首轮三文件（implementation、首次 review、首次 response），随后又说明二审 review/response 是独立 artifact；第 7.2 节则把二审文件纳入当前 directed link/evidence。二审回复又称“实际 writeback 只修改实施方案 target”，并把二审 response 作为独立 artifact。

这些说法可以分别成立，但没有给出一个按时间和职责分开的 actual ledger，因而无法从文档回答以下问题：

- 首轮三文件是“首轮历史 audit set”还是当前 changed-file manifest？
- 二审 response 是独立 response-only unit，还是 target writeback 的同一 unit？
- 当前新增的 third-review file 应登记在哪里？

这不是要求把所有 review 文件塞进 target manifest，也不是要求回写冻结历史；问题在于 `CLOSED` 的 manifest owner 不唯一。最小整改是新增一张不改变历史 SHA 的 ledger，至少分列：

```text
historical first-round target/writeback
first review (single review unit)
first response (single response unit)
second review (frozen review unit)
second response + target writeback (exact unit, if so)
third review (this single-file unit)
```

每项明确 `target / review / response / navigation` 职责和 exact paths；不要用“本次只维护三个文件”覆盖不同轮次。该整改仍是 docs-only，不改变 public/runtime scope。

### R13 — MAJOR：负向 scope 证据的命令覆盖范围不足，且与当前 dirty worktree 的事实混淆

实施方案第 7.2 节及二审回复第 9 节使用：

```bash
git diff --name-only -- src tests pyproject.toml CHANGELOG.md
```

该命令只查看指定路径下的**未暂存 tracked diff**：

- 不读取 `git diff --cached`；
- 不显示 untracked files（而本组 docs 文件当前正是 untracked）；
- 从 `mote-kernel` 工作目录也不会检查 monorepo sibling 的 `mote-infra/persistence/**`、`conformance/**` 等路径；
- 它不能单独证明“State / Store / protocol / persistence / execution path 无 diff”。

当前工作树还存在一个用户拥有的 sibling persistence dirty file（`mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/py.typed`），以及 README/其他 docs 等 unrelated changes。不能把“命令输出为空”写成全局 persistence clean，也不能用全局 clean 代替 change-unit attribution。

整改应明确“本 change unit 无 source/test/state/store diff”而非声称全局无 diff，并对 exact scope 同时记录：

```bash
git status --short --untracked-files=all -- <exact paths>
git diff --name-only -- <exact paths>
git diff --cached --name-only -- <exact paths>
git ls-files --others --exclude-standard -- <exact paths>
```

如果要覆盖 monorepo sibling，必须从 monorepo root 列出 `mote-kernel/src/**`、`mote-kernel/tests/**`、`mote-kernel/src/mote_kernel/state/**`、`mote-infra/persistence/**`、`conformance/**` 等 exact path；否则把未检查的类别从结论中删除。该 finding 不要求清理或覆盖用户的 unrelated dirty file。

### R14 — MAJOR：`COMPLETE` evidence 没有 exact command/output/time binding，untracked docs 的 check 口径不可复现

实施方案第 7.2 节将当前 docs-only evidence 标为 `COMPLETE`，二审回复也将 link、EOF/whitespace、scoped pre-commit 和 `git diff --check` 全部写成“通过”，但没有记录：

- 每个检查的 exact command、cwd 和 path list；
- 执行时间与对应 `HEAD`/target snapshot；
- link checker 是否检查 normative source；
- 未跟踪文件使用的 EOF/whitespace 命令及其退出码；
- `make check`/all-files pre-commit 是历史 baseline 还是当前 snapshot 的明确边界。

`git diff --check` 对当前 untracked implementation/review/response 文件不会产生任何结果；二审回复虽声称“未跟踪 docs 另经显式扫描”，却没有给出可复跑命令。当前实际可以复跑并通过门禁，但这不能把缺失的历史 command record 追溯写成 target 已经记录的 evidence。

最小整改是把 evidence 分成 `reported historical baseline` 与 `current docs-unit verification`，为后者保存 exact command、cwd、scope、时间、exit/output，并绑定 target/review/response/normative-source hashes；或者在补齐前把 `COMPLETE` 降级为 `REPORTED / REPRODUCIBILITY PENDING`。这仍不要求重新执行 production implementation，也不改变 `NO PRODUCTION AUTHORIZATION`。

### R15 — MINOR：现有 internal `NodeReturn` 与“不新增 NodeReturn”的表述未区分

`src/mote_kernel/execution/engine/scheduler.py` 已存在 internal `NodeReturn: TypeAlias`，其内容是 `_GraphValues` 加三个 concrete outcome classes。实施方案第 2.1 节写“不新增 `NodeReturn` 或 `PlainSuccessOutcome`”，若按字面理解会与现有源码 symbol 冲突；若意图只是“不新增 public `Graph.NodeReturn` 或第二 construction path”，则应明确写出该限定。

建议改为：

```text
保留现有 scheduler.NodeReturn internal alias；本 change unit 不新增 Graph.NodeReturn、NodeOutcome、PlainSuccessOutcome、wrapper 或 factory。
```

这只是文档精度问题，不要求删除或重命名现有 internal alias。

## 5. 已确认不构成 finding 的事项

本轮没有重新打开二审已关闭的技术方向：

- 不新增 `Graph.NodeOutcome`、`Graph.SettledNodeResult` 或 `Graph.RunResult`；
- 不把 `Graph.Error` 放入 `Graph.Result`，不把 `Graph.failure()` 说成 raised exception；
- `Graph.Transition.result` 仍是 callback 前的 admitted candidate evidence，不是 durable receipt；
- `_PartialCommitError` 仍由 `result.py` 持有，`Graph` 只提供 direct alias；
- plain values、explicit outcome、task result、commit result 和 run result 的 construction seals/owner 不合并；
- `FrameInstallationInvariantError` 不提升为新的 facade alias，不与 `ValuePublicationError` 合并；
- State、Store、protocol、persistence、failover、retry、第二 runner 和执行路径不进入本 change unit。

当前源码相关 aliases 的直接 identity 复核为：

```text
Graph.Error is ExecutionError                       = True
Graph.PartialCommitError is _PartialCommitError    = True
Graph.ValuePublicationError is GraphValuePublicationError = True
Graph.ValidationError is GraphValidationError      = True
Graph.ExecutionLimitError is ExecutionLimitError    = True
```

## 6. 必须的收口顺序

1. 先由 implementation owner 建立按轮次/职责分开的 docs ledger，不回写冻结的首次/二审历史文件；
2. 修正 negative scope command，区分 worktree、index、untracked 和 monorepo sibling，不把 unrelated dirty state 归因给本 change unit；
3. 补齐 current docs-unit exact command/output/time/hash record，并收窄 `ValuePublicationError` 与 `NodeReturn` wording；
4. 对回写后的 target 做一次只验 R12–R15 的独立 docs review；若 findings=0，最终 `PASS` 只由该 review record 持有，不再为复述 PASS 改写 target；
5. 全过程保持 `KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION`，不得借 closure 修改源码、测试、State、Store、protocol、persistence 或执行路径。

## 7. 本轮验证记录

本轮独立复核实际执行了以下检查；它们证明当前快照可运行，不追溯替代二审文档缺失的历史 command record：

| 检查 | exact command / scope | 结果 |
| --- | --- | --- |
| 当前源码/全量工程门禁 | `make check`；cwd=`/home/longert/motev2/mote-kernel` | **PASS**：Ruff/format、Pyright `0 errors`、complexity `9 passed`、health `51 reviewed / 0 unreviewed / 0 stale`、`850 passed`、coverage `100%`、build/twine passed。 |
| root scoped pre-commit | `pre-commit run --files`；六个 outcome/result/error docs（含本文）；cwd=`/home/longert/motev2` | **PASS**：适用 hooks 全部通过；源码/complexity hooks 因路径过滤 skipped。 |
| Markdown links + EOF/CR/whitespace | Python read-only scan；六个 outcome/result/error docs，23 条 relative links；cwd=`/home/longert/motev2` | **PASS**：`files=6 links=23 errors=0`。 |
| target/response hashes | `sha256sum`；cwd=`/home/longert/motev2/mote-kernel` | 与第 1 节记录一致。 |
| source/test exact worktree scope | `git status --short --untracked-files=all`、`git diff --name-only`、`git ls-files --others --exclude-standard`；`mote-kernel/src`、`mote-kernel/tests`、`pyproject.toml`、`CHANGELOG.md` | **PASS（当前 unit 归因范围）**：无 source/test/pyproject/CHANGELOG tracked 或 untracked diff；不能外推为全局 worktree clean。 |
| owner/source spot check | `rg`/源码静态读取 `errors.py`、`result.py`、`facade.py`、`graph/node.py`、`engine/scheduler.py`、`resume_admission.py`、`run_context.py`、`invocation.py` | **PASS**：R8–R10 的 canonical symbols、exception inheritance、projection 和 collision/install phase 与文档主方向一致。 |

本轮已将本文纳入六文件 root scoped pre-commit 与 link/EOF scan；这些检查只证明当前 docs snapshot 的可交付性，不改变 R12–R15 的 owner/manifest 缺口。`make check` 是当前 `HEAD=d35b74f…` baseline 复跑，不是 docs-only production authorization。

## 8. 最终裁决

```text
R8 plain Graph.Values lifecycle                 = RESOLVED / CLOSED
R9 exception owner split                        = RESOLVED / CLOSED
R10 Error matrix                               = TECHNICALLY RESOLVED / MINOR WORDING OPEN
R11 closure direction                           = UPHELD / EVIDENCE NOT YET CLOSED
R12 manifest owner                             = OPEN / MAJOR
R13 negative scope proof                        = OPEN / MAJOR
R14 reproducible evidence                      = OPEN / MAJOR
R15 NodeReturn wording                         = OPEN / MINOR

blocker = 0
major = 3
minor = 2 (R10 wording + R15)
public surface                                  = KEEP
production/tests/State/Store/protocol           = NO CHANGE
docs-only target status                         = CHANGES REQUESTED
production authorization                        = NONE
```

**最终结论：当前 `9ed092e…` 实施方案的技术方向正确，二审 R8–R10 的实质整改成立；但在 R12–R15 回写并完成一次独立 docs review 前，不批准其 `CLOSED` 作为无 finding 的最终 closure，也不授权任何 production 或测试实施。**

## 9. 本次 review change unit

本轮只新增：

```text
mote-kernel/docs/graph-outcome-result-error-surface-implementation-third-review.zh-CN.md
```

未修改被评审实施方案、二审评审、二审回复、normative source、production、tests、README、State、Store、protocol、persistence、complexity 配置或其他用户文件。
