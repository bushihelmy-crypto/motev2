# Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案第二次独立评审

> **结论：`CHANGES REQUESTED / R1–R4、R7 RESOLVED / R5–R6 PARTIAL / R8–R11 OPEN / KEEP CURRENT PUBLIC SURFACE / NO PRODUCTION AUTHORIZATION`。**
> 整改版已经关闭 breaking rename、`SettledNodeResult` 误导、public/internal alias 混用和 union/runtime narrowing 等核心方向问题；
> 现有 owner、public aliases、执行路径、State 与 Store 均保持不变，方向正确。但 callable plain-success 路径仍被生命周期图遗漏，Error
> owner 与相邻异常语义尚未精确分开，docs-only closure 状态及证据又前后冲突。以下事项闭合前，不能把本 docs-only change unit 标记为完成。

## 1. 评审对象与冻结边界

- 评审日期：2026-08-26
- 整改版实施方案：[Graph Outcome、Node Result、Run Result 与 Error 公共类型表面收敛实施方案](graph-outcome-result-error-surface-implementation.zh-CN.md)
- 整改版 SHA256：`be747243b7419604dc8d9cdffa268efbeb368a7aeaeaa043c6bcc3ac3866a5d6`
- 首次评审：[独立评审](graph-outcome-result-error-surface-implementation-review.zh-CN.md)
- 首次评审 SHA256：`a8bd4b3e88aa9e89e3e0fee6a1e029f1cca9edf6bc1c072ce3817d4b4e663668`
- 评审回复：[评审回复](graph-outcome-result-error-surface-implementation-review-response.zh-CN.md)
- 评审回复 SHA256：`efc3a8d5cdae46016626f9f4796da1a637f6396d302288e0fab34f0c6056b18b`
- production baseline：Git `563a45124311f11e870d0627461102baeffdf7ad`
- 当前规范事实源：[Graph 节点显式多端口输入/输出与参数绑定实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 本文性质：第二次独立 docs-only design review；只拥有本轮裁决和整改要求，不拥有 public API、runtime、State、Store 或 production 授权。
- 本轮只新增本文，不修改整改版、首次评审、评审回复、production、tests、README、State 或 Store。

本轮继续采用相同硬标准：唯一事实源、复用既有 infrastructure、零已知负债、严格类型、完整且必要的改动、不引入持久化或 failover，
并避免为了文档闭环制造动态索引、兼容 alias、wrapper 或第二规范 owner。

## 2. 首次评审 R1–R7 复核

| 首次 finding | 二审裁决 | 复核结果 |
| --- | --- | --- |
| R1：错误假设 public API 尚未发布 | **RESOLVED** | 整改版明确 KEEP `Graph.Outcome`、`Graph.Result` 及全部既有 concrete aliases；未来 rename 独立立项 |
| R2：新旧 canonical alias 未闭合 | **RESOLVED** | 不新增 `Graph.NodeOutcome`、`Graph.RunResult`、`Graph.SettledNodeResult`，也不保留双轨 |
| R3：`SettledNodeResult` 暗示确认完成 | **RESOLVED** | 该名称不再是 public target；`Graph.Transition.result` 明确为 callback 前的 admitted candidate evidence |
| R4：持久化/failover/recovery 越界 | **RESOLVED** | 只把现有 callback 顺序与 exact successor 作为 HARD KEEP；未新增 Store、journal、checkpoint、failover 或恢复算法 |
| R5：Error public/internal 清单矛盾 | **PARTIAL** | public alias 集合已与 facade 对齐，但 exception owner、`PlanningError` 分类和 publication/installation 语义仍不精确 |
| R6：验收矩阵与范围不匹配 | **PARTIAL** | 已收窄为三文件 docs-only manifest；完成状态、链接口径和当前 change-unit 证据仍未闭合 |
| R7：union alias 与 runtime narrowing 混淆 | **RESOLVED** | union 只用于 annotation；runtime 只按 concrete aliases narrow；没有新增 wrapper/base/tag/registry |

已关闭事项无需重新设计，也不得为了响应二审而恢复 rename、production test、typing fixture、State、Store 或 execution-path 工作项。

## 3. 已确认正确且应保持的目标

以下内容已经与当前代码和规范事实一致：

1. `Graph` 仍是唯一 public graph facade，`execution.__all__ == ["Graph"]`；
2. facade aliases 直接引用 outcome/result/error canonical implementations，不复制 DTO；
3. `Graph.Outcome`、`Graph.Transition.result`、`Graph.Result` 和 raised exceptions 保持不同生命周期与 construction capability；
4. outcome、commit result、transition 与 run-result variants 继续使用各自 owner-private seal；
5. `Graph.Error` 不进入 `Graph.Result`，`Graph.failure()` 不是 raised exception；
6. `Graph.Transition.result` 在 callback 前产生，不是 receipt；`commit=None` 时不存在 external confirmation；
7. `Graph.PartialCommitError` 只保持既有类型身份和行为，不扩张为 checkpoint/failover protocol；
8. 本 change unit 不新增 alias、exception、factory、store、runner、兼容路径或 hidden mutable state。

这部分是对既有实现的准确说明，不构成 production implementation。

## 4. 二审新增 findings

### R8（MAJOR）：生命周期图遗漏 plain `Graph.Values` success

整改版第 1、2 节把 callable return 概括为：

```text
callable -> NodeOutcome（概念） -> GraphOutcome -> TaskResult
```

但当前唯一 `NodeCallable` 契约实际是：

```python
Graph.Values[GraphValueT] | Graph.Outcome[GraphValueT]
```

`engine/scheduler.py::_project_outcome()` 也把 exact `_GraphValues` 直接投影为 `TaskSuccess`。因此 plain success 不会先变成
`GraphOutcome`；整改版虽然在第 2.1 节单独承认 plain `Graph.Values`，第 1 节生命周期图、第 1.2 节分层表和第 2 节 canonical chain
仍共同遗漏这条合法主路径。

这不是措辞偏好。本文的目标正是澄清 Outcome/Result 生命周期；把最普通的 callable success 放在模型之外，会让 `NodeOutcome` 概念标签
重新成为一个比 canonical `Graph.Outcome` 更宽、但没有精确定义的第二概念 union。

**整改要求：**三个位置统一写成：

```text
callable node return
  -> Graph.Values | Graph.Outcome
  -> TaskResult
  -> GraphCommitResult
  -> GraphTransition.result
```

`Graph.Values` 应明确表示 plain success；`Graph.Outcome` 只表示 explicit success/failure/interrupt union。不得为修正文档新增
`NodeReturn` public alias、`PlainSuccessOutcome`、wrapper 或额外 factory。

### R9（MAJOR）：exception hierarchy 的“唯一 owner”表述仍不真实

整改版第 2 节把整个 exception hierarchy 唯一归给 `execution/errors.py`，D3 又要求以该模块为唯一继承树 owner。但当前 public
`Graph.PartialCommitError` 的 canonical `_PartialCommitError` class 实际定义在 `execution/result.py`，只继承
`errors.py::ExecutionError`；facade 再提供 direct public alias。

准确 owner 关系应是：

| 事实 | 唯一 owner |
| --- | --- |
| shared graph execution error taxonomy | `execution/errors.py` |
| sealed partial-prefix exception 与其 state/continuation/cause 字段 | `execution/result.py::_PartialCommitError` |
| public Graph-namespaced exception aliases | `execution/facade.py::Graph` |

**整改要求：**修正第 2 节 owner 表和 D3，不再声称完整 hierarchy 只在 `errors.py`。这只是记录现有 ownership；不得移动
`_PartialCommitError`、新建 error module、复制 subclass、增加 re-export 或重构运行时。

### R10（MAJOR）：Error matrix 仍混合了相邻但不同的异常边界

第 3 节的 alias 集合已经完整，但语义矩阵仍有四处需要精确收口：

1. `PlanningError` 是 `SnapshotMismatchError`、`InvalidExecutionSnapshotError`、`ExecutionLimitError` 的 internal intermediate base，
   不是第 3.2 节标题所称的 “leaf”。该节应称为 internal exception classes。
2. `Graph.ValuePublicationError` 当前负责 duplicate/colliding graph-input、publication、resume-input、child-boundary coordinate，以及
   candidate/confirmed publication source 冲突；它不拥有 post-commit frame installation invariant。
3. `FrameInstallationInvariantError` 是单独的 internal `Graph.Error` subclass，负责 confirmed successor 与 pre-admitted frame 安装不一致；
   不能被 `ValuePublicationError` 行的 “publication/frame installation invariant” 合并掉。
4. `Graph.ExecutionLimitError` 同时拒绝非法 public limit 参数并表示执行超过 explicit bound；只写“到达预算边界”遗漏 fail-fast 参数校验。

此外，`Graph.Error` 只覆盖 graph-owned faults。ordinary node callable、commit callback 或其他外部能力抛出的任意 exception 不会自动
变成 `Graph.Error`；现有执行链会保留其 identity，只有既有 confirmed-prefix 场景按当前契约形成 `Graph.PartialCommitError`。Error surface
文档至少应明确这一条负边界，避免调用方误以为 `except Graph.Error` 可以捕获所有外部异常。

**整改要求：**只修正语义描述和标题，不新增 public aliases，不把 `FrameInstallationInvariantError` 提升到 facade，也不新增异常包装路径。

### R11（MAJOR）：docs-only closure 状态、链接口径和证据互相冲突

当前三份文档同时存在以下状态：

```text
implementation header        = Revised
implementation final ledger  = docs-only writeback REQUIRED
response disposition         = REVIEW RESPONSE COMPLETE
response final ledger        = docs-only writeback COMPLETE
```

因此完成状态没有唯一 owner。D1–D5 和 response 已被写成完成事实，但 implementation 仍保留未来式步骤和 `REQUIRED`；二者不能同时作为
当前裁决。

链接证据也需要精确表述。当前实际有：

```text
implementation -> first review, response
response       -> implementation, first review
first review   -> implementation
```

首次评审是冻结输入，没有 response backlink。response 所称 “target/review/response 互链 = 已写入” 若按 pairwise mutual links 理解并不成立；
也不应为了补一个动态导航链接修改首次评审并使其已记录 SHA 失效。

最后，implementation 第 7.1 节的 `make check`/all-files pre-commit 只被标为 docs writeback 前的 baseline，却没有日期、cutoff 或当前
docs-unit command record；第 7.2 节仍只有“必须满足”。`git diff --check` 还不会检查未跟踪 docs 文件，不能单独证明当前三个新文件的
whitespace/EOF。当前仓库另有用户拥有的 README 等 dirty changes，因此也不能用全局 “worktree clean” 代替 exact scoped manifest。

**整改要求：**

1. 由 implementation owner 在 `REQUIRED` 与 `COMPLETE` 中选择一个当前状态；只有当前 docs hooks 和 scoped evidence 通过后才能写
   `CLOSED / KEEP / DOCS-ONLY / NO PRODUCTION AUTHORIZATION`。
2. 把 “互链” 改成上面的 exact directed link graph，保持首次评审冻结，不为导航制造循环 writeback。
3. 将历史 `make check`/all-files 结果明确标成 baseline snapshot，并为当前 docs unit 记录 target/response hashes、link check、EOF/whitespace、
   scoped root pre-commit 和 `src/**`/`tests/**` 零 diff；不得冒充 production authorization。
4. 本第二次评审是独立 review-only unit，不加入原三文件 implementation manifest，也不要求 production/tests 变化。

## 5. 完整且必要的收口顺序

1. 先修正 plain `Graph.Values | Graph.Outcome` 的完整 callable return chain，消除未定义的宽 “NodeOutcome” 概念。
2. 再修正 Error owner 表和 exception matrix，保持 `errors.py`、`result.py`、facade 三者现有职责，不改代码。
3. 最后统一 docs-only status 与验证账本；保留冻结首次评审，使用精确有向链接而非泛称互链。
4. 对最终 docs manifest 运行适用 hooks；确认 production/tests/State/Store 没有本 change unit 的差异。
5. 完成上述工作后进行一次只验 R8–R11 的 docs acceptance；不得借 closure 扩大到 public rename、typing/runtime implementation、
   persistence 或 failover。

## 6. 当前裁决

```text
R1 public API keep decision                         RESOLVED
R2 canonical alias ownership                       RESOLVED
R3 candidate evidence naming                       RESOLVED
R4 no persistence / no failover scope              RESOLVED
R5 exact Error surface                              PARTIAL / R9–R10 OPEN
R6 docs-only manifest and acceptance                PARTIAL / R11 OPEN
R7 union annotation vs concrete narrowing           RESOLVED
R8 callable plain-success lifecycle                 OPEN

conceptual separation                               PASS
existing owner/infrastructure reuse                 PASS
public aliases / production / tests / State / Store KEEP / NO CHANGE
docs-only implementation closure                    CHANGES REQUESTED
production authorization                            NONE
```

本二审不授权任何 production、test、State、Store、public alias、exception 或 execution-path 修改。整改应严格限制为现有 docs owner 的
事实修正与 closure ledger；若实现需要触碰上述边界，必须停止并另行立项。

## 7. 本轮验证记录

- 静态读取整改版 384 行、首次评审 236 行、评审回复 172 行，并核对当前 facade、outcome、node callable、scheduler、result、
  family-driver 和 errors owners。
- SHA256 已按第 1 节冻结；response 中记录的整改版与首次评审 hashes 均与当前文件一致。
- 当前三个既有 docs 文件的所有相对 Markdown targets 均存在；实际 directed link graph 见 R11。
- `git diff --name-only -- src tests pyproject.toml CHANGELOG.md` 为空；README 与 monorepo 其他 dirty files 是本轮开始前已经存在的用户变更，
  不纳入本 review change unit，也未被修改。
- `make check` **PASS**：Ruff/format、strict Pyright `0 errors`、complexity gate/health、全量 `850 passed`、statement/branch
  coverage `100%`、build 与 twine package check 全部通过。
- 从 monorepo root 对 outcome/result/error 四个 docs 文件运行 scoped pre-commit，全部适用 hooks **PASS**；EOF、mixed line ending、
  trailing whitespace 与 detect-secrets 均通过。
- 新二审的四个相对 Markdown targets 全部存在，trailing-whitespace 扫描无结果；这些门禁只验证 review/docs 和当前 baseline，
  不构成 production authorization，也不关闭 R8–R11 的文档事实问题。

**第二次评审结论：核心方向已通过，但 R8–R11 尚未闭合；整改版暂不能标记 docs-only 完成。**
