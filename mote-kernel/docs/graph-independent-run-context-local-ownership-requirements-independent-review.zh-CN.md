# 父子图 GraphRun 本地 ownership 需求独立审核

> **结论：`PASS WITH IMPLEMENTATION PRECONDITIONS / REQUIREMENTS APPROVED FOR TARGET DESIGN`。**
> 本轮只批准修订后的 `GRC-LO-001` 需求范围和行为边界，不批准复审意见本身，也不等同于
> implementation target、production、State、Store、API 或 tests 已获实施授权。需求已经
> 正确收口到：每个 `GraphRun` 自己拥有状态；父图不持有 child `run_id`；调用级中断向下
> 传播并由父、child 各自执行既有 `AbortGraphRun`；普通节点失败不广播 sibling。

本文件是对主需求的独立审核记录。用户指定的上一轮复审意见只作为检查清单和证据入口，
不是本轮的批准对象；本轮不修改 production、State、Store、protocol、public API 或 tests，
也不引入 persistence、failover、跨 invocation child-ID recovery 或 global admission guard。

## 1. 评审对象与冻结输入

- 主需求：[父子图 GraphRun 本地 ownership 拆分窄范围需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
  - 当前 SHA256：`1ff31e956d1799bdc2b62ee7cbf7fc6e0d62aedb74786c9bc0850671a74b12d6`
- 审核依据（不单独批准）：[需求与复审回复审批](graph-independent-run-context-local-ownership-implementation-review-response-review-response-review.zh-CN.md)
  - 当前 SHA256：`992d72a9c3eee970a0b55b3c3cabe877837ee7b7dd3e89b3944207419161333e`
- 下游 target（不在本轮批准对象内）：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
  - 当前 SHA256：`e343cfd93c504314b969474e997f5c06a61b7cabcf530e961fbc2fb76ca4da18`
- 评审日期：2026-08-28

主需求标注为 `REVISED AFTER LO-A1–LO-A5 / READY FOR INDEPENDENT REVIEW`，本轮按该最新
内容审核，不沿用其修订前的 hash 或结论。

## 2. 审核口径

本轮只判断需求是否形成可供 implementation target 使用的唯一窄 contract：

1. owner 必须唯一：live state/frame/continuation/transition/commit 归对应 `GraphRun`；
   family snapshot 只能是 immutable、transient 的 transport/validation evidence。
2. 调用关系必须不越权：父只发起当前 child call、等待/消费结果和结算自己的节点；父不得
   通过 child ID 查找、恢复、取消或写入 child。
3. 取消与失败必须分层：调用级 cancellation/abort 可向 live child call 传播并触发各自
   abort；typed node/child failure 和 ordinary node exception 不自动广播 sibling。
4. 只排除新增能力：不新增 persistence/Store 协议、failover 或 child-ID-only recovery；
   既有 owner-local authoritative state 读写语义继续有效。
5. 需求批准不替代实现批准：target 必须据此更新其 private contract、manifest 和测试，
   再做独立 implementation review。

## 3. 总体裁决

| 检查项 | 结果 | 依据 |
| --- | --- | --- |
| 每个 GraphRun 独占 state/transition/commit | **通过** | 需求 §3.2、§4.1、§8 |
| 父不维护 child authoritative state | **通过** | 需求 §4.1、§4.2、§5.1、§8 |
| 父不持有 child `run_id` | **通过** | 需求 §3.3–§3.5、§4.2、§5.4、§8、§11 |
| opaque handle 的生命周期与权限 | **通过** | 仅 invocation-local、不可暴露 identity、不可按 ID 重建 |
| child result 与 parent settlement 分离 | **通过** | 需求 §4.2、§4.4、§6 |
| 调用级 cancellation/abort 向下传播 | **通过** | 需求 §3.6、§4.1、§4.4、§6、§8 |
| 父、child 使用同一 `AbortGraphRun` 逻辑 | **通过** | 需求 §4.1、§4.4、§6、§8、§11 |
| typed failure / ordinary exception 不连带 sibling | **通过** | 需求 §3.6、§4.4、§6、§8、§11 |
| awaiting/resume 不伪造终态 | **通过** | 需求 §4.4、§6 |
| family evidence 不成为 live shared owner | **通过** | 需求 §4.4、§5.3、§8、§9 |
| 既有 persistence 保持 owner-local | **通过** | 需求 §1、§6、§7、§8、§11 |
| no failover / no ID-only recovery | **通过** | 需求 §1、§7、§11 |
| State/public API/engine 不扩张 | **通过** | 需求 §5.1–§5.2、§7、§11 |

## 4. 前一轮意见的独立关闭核对

### 4.1 LO-A1：parent 不持有 child `run_id`

已关闭。主需求不再把 child ID 当作 parent/coordinator 的必要资料：

- §3.5 把 handle 定义为 opaque call/wait/abort 对象，禁止暴露或保存 child `run_id`、
  lookup key 和可重建 identity；
- §4.2 明确 parent 不计算、保存、查询或控制 child `run_id`，只提供自己的 activation
  metadata；
- §5.4 和 §8 将 child ID 的创建、校验和使用归 child owner，parent 只能使用 opaque handle；
- §6、§9、§11 将“handle 不含 child ID”列为可验收 gate。

这保留了必要的内部 identity 不变量：同一 compiled child 在不同 parent `run_id` 下由
child owner 派生的 child `run_id` 必须不同。该纯投影不再承担 parent lookup、恢复、控制或
并发 admission 职责，符合“各个图维护自己的 run ID”的原则。

### 4.2 LO-A2：调用级取消与双边 abort

已关闭。主需求 §4.4 明确了完整顺序：

```text
调用级 cancellation/abort/error
  -> 沿当前调用链传播到 live child call
  -> child 关闭自己的 session，必要时完成 owner-local fence 前置条件
  -> child 在自己的 state 上执行既有 AbortGraphRun 并独立 commit
  -> parent 关闭自己的 session，按同一逻辑执行自己的 AbortGraphRun 并独立 commit
```

同时规定：

- 已确认 terminal run 不重复 abort；未确认 candidate 不伪造 `ABORTED`；
- 两个 commit 不跨图原子化，一方失败不回滚或猜测另一方；
- 该路径不产生 orphaned-claim recovery，不凭 child ID 接管；
- 不新增 status、field、command 或错误 taxonomy。

因此主需求已纠正上一版“取消后保留 `RUNNING + orphaned claim`、下次再 fence”的错误方向。

### 4.3 LO-A3：sibling 失败隔离

已关闭。§3.6、§4.4、§6 和 §8 做了必要的三分：

- typed node/child failure：只结算失败节点，其他 sibling 不取消；
- ordinary node exception：停止新的 activation，按既有 session 规则排空已启动任务，
  不广播 sibling abort；
- invocation-level cancellation/abort：才传播到 live child call，并由各 owner 独立 abort。

这与当前 session 的“错误后不再新调度、已启动任务完成清理”边界一致，没有把一个业务
节点失败误写成全 family `ABORTED`。

### 4.4 LO-A4：既有 persistence 与本期非目标

已关闭。主需求现在使用“不新增 persistence/Store 协议”的准确表述，同时保留：

- parent 只读写自己的 authoritative state；
- child 只读写自己的 authoritative state；
- 持久化持续失败沿既有 Store/commit 错误传播；
- 不增加 child-ID lookup、checkpoint、receipt、replay、failover 或跨 owner recovery。

因此“不做持久化”被正确解释为“不在本 target 新设计持久化”，而不是禁止既有 owner
读取自己的状态。

### 4.5 LO-A5：frame/continuation evidence

已关闭。§5.3、§8、§9 明确：

- 每个 GraphRun 的 live frame、session 和 continuation 资料由各自 owner 负责；
- 现有 family-shaped `ContinuationSnapshot`、`ChildStateBinding` 和合并 frame index
  只作 immutable/transient transport/validation evidence；
- evidence 不可驱动、替换或成为 parent 的 child state/identity owner；
- child identity 若因既有 sealed contract 出现，只能作为 evidence，不是 lookup key。

这使“每图独立运行上下文”与“保留现有 sealed snapshot ABI”可以同时成立，未引入第二
frame/value truth。

## 5. 仍需在 implementation target 阶段闭合的前置条件

以下不是主需求的 blocker，而是 target 获准前必须落实的同步条件：

### 5.1 target 必须同步新 handle contract

当前下游 target 仍在 owner 图中列出带 `activation`/`run` 的 `_ChildRunHandle` 和
canonical child handle tuple，并在多个步骤使用 child coordinate。实现时必须把这些降为
child owner 内部的 opaque routing detail；parent/coordinator 的 durable 或 reusable 资料
不得出现 child `run_id`。若为当前调用等待结果确实需要对象，只能验证其 opaque 生命周期和
wait/abort 两种权限，不能恢复成 parent identity map。

### 5.2 target 必须同步双边 abort，不能保留 orphaned claim 旧文案

下游 target §4.4、§10.2、§11 Step 4 仍写着 active token 保留、取消后 orphaned claim、
下一次 state-only invocation 再 fence。实现 target 必须改为主需求 §4.4/§6 的 quiesce →
必要时 fence → 各自 `AbortGraphRun` 顺序，并明确 child commit 失败不阻止 parent 按自身
owner 语义处理。

源码证据显示 `AbortGraphRun` 当前只接受 quiescent running state（
`src/mote_kernel/state/graph_state/lifecycle_transitions.py`），因此 fence 是该既有
transition 的前置步骤，不是新增 recovery/failover 协议。

### 5.3 需由 target 处理“当前单图 CancelledError 回归”归属

现有测试 `tests/execution/test_graph_api.py::test_cancelled_run_quiesces_workers_retains_the_claim_and_recovers_from_authoritative_state`
记录了单图取消后保留 active claim、后续 state-only recovery 的旧行为。主需求现在明确的
双边 abort 是新的调用级 ownership 边界，target 必须在实现审查时明确其适用范围：

- 若该语义只针对 nested parent→child 调用，需在 target 中限定范围，单图既有 regression
  保持不变；
- 若该语义覆盖所有 `Graph.run()` caller cancellation，则应在新的 normative behavior/test
  change 中同步更新该回归，并删除“observable behavior 完全不变”的冲突表述。

在范围写清前，不应把当前 target 的旧 orphaned-claim 文案当作已批准行为。本条件不改变
主需求的 ownership 结论，只要求实现阶段明确 normative source 与测试责任。

### 5.4 失败优先级与独立 commit

target 还需冻结：child abort commit 失败时仍如何尝试 parent abort、错误传播优先级如何沿
现有 commit contract 分类，以及 terminal/未确认 candidate 的边界。需求已经规定“不跨图
原子化、不回滚、不猜测”，不要求新增事务或 retry 协议。

## 6. 主需求可批准的范围

本轮批准以下内容进入 implementation target 设计：

- same-invocation 的 per-GraphRun state/context/frame ownership split；
- parent 只调用 child、消费 typed result、结算自己的 nested node；
- child ID 仅由 child owner 内部维护，parent 不持有/查找/控制；
- invocation-local opaque handle 的 wait/abort 传递；
- child/parent 独立 transition/commit，调用级 cancellation 的双边 `AbortGraphRun`；
- typed failure 与 ordinary exception 的 sibling 隔离；
- awaiting/continuation 的既有 opaque evidence 语义；
- 既有 owner-local persistence read/commit 的保留；
- 不新增 public API、State schema、第二 engine、persistence、failover 或 overlap gate。

本轮不批准：

- 下游 target 当前未同步的 handle/coordinate 编排形状；
- orphaned-claim continuation/recovery 作为取消路径；
- 任何 child-ID-only restore、global registry、persistent lock、worker handoff 或自动 retry；
- 以本需求审批替代 implementation review 或代码授权。

## 7. 最终审批 ledger

```text
GRC-LO-001 scope                         = APPROVED / READY FOR TARGET DESIGN
per-GraphRun state ownership             = APPROVED
parent child-state mirror                = FORBIDDEN
parent-held child run_id                 = FORBIDDEN
opaque child call handle                 = ALLOWED / INVOCATION-LOCAL / WAIT-OR-ABORT ONLY
child-owned run_id/state                 = REQUIRED (INTERNAL)
child/parent AbortGraphRun               = REQUIRED / SAME EXISTING LOGIC / INDEPENDENT COMMITS
typed node/child failure                 = LOCAL / NO SIBLING BROADCAST
ordinary node exception                  = STOP NEW ACTIVATION / NO SIBLING ABORT
family evidence                          = IMMUTABLE TRANSIENT TRANSPORT/VALIDATION ONLY
existing owner-local persistence         = KEEP / NO NEW PROTOCOL
child_run_id-only recovery               = OUT OF SCOPE
failover / worker handoff                = OUT OF SCOPE
implementation target                    = NOT YET APPROVED / MUST SYNC §5
review opinion used as basis             = YES / NOT A SEPARATE APPROVAL OBJECT
production / State / Store / API / tests = NO CHANGE IN THIS DOCS-ONLY REVIEW
```

主需求本轮可以作为 implementation target 的 scope 输入；target 完成第 5 节同步并重新
审核后，才可进入用户已授权范围内的代码实施。无论如何，不得从本文件推导出跨 invocation
child recovery、持久化扩张或 failover 能力。

## 8. 验证记录

- 三份相关文档执行 repository pre-commit 的文件级 hooks：通过（大文件、冲突、行尾、混合
  换行、secrets；Python hooks 对 Markdown 自动跳过）。
- 相关文档的 12 个相对链接逐一解析：目标文件全部存在，无断链。
- 直接相关的现有行为测试：`40 passed, 65 deselected`（identity、reducer、frame、result
  boundary、nested/continuation/cancellation 选择集）。本轮没有修改 production、State、Store、
  API 或 tests。
- `make check`：ruff、format、pyright 通过；在既有 `complexity-ratchet` 阶段失败，原因是
  当前仓库基线已有 5 个未登记 complexity candidate，且 `decision_points` 为 `1314`、配置上限
  为 `1312`。该失败不由本轮 docs-only 文件引入；后续 `complexity`、全量行为测试和打包阶段因此
  未被该命令执行。
