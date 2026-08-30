# 父子图 GraphRun 本地 ownership 实施方案评审回复复审

> **结论：`CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION`。**
> 本轮严格先审逐项回复，再审修订实施文档。回复已经正确撤回了 admission guard、
> opaque continuation slot、per-run recovery protocol、持久化/ failover 和 overlap
> gate 等越界承诺，也保留了当前 projection 与 sealed snapshot 的方向；但实施 target
> 仍没有把 family evidence、local frame owner、post-commit 失败窗口和 cancellation
> 边界落成可执行的唯一 typed contract。它们不是复杂度问题，不能在编码时凭经验补齐。

本复审只新增本文件，不修改 requirements、implementation target、production、State、
Store、protocol、public API 或 tests；不新增或扩写 legacy/private-source-shape/AST-only
test。complexity gate 按用户要求排除且不运行。

## 1. 复审顺序与冻结输入

已先完整阅读逐项回复，再完整阅读其声明的修订 target，并以当前源码和现有测试作为
normative evidence：

- 逐项回复：[本地 ownership 实施方案继续独立评审回复](graph-independent-run-context-local-ownership-implementation-review-response.zh-CN.md)
  - SHA256：`3017d0298e8315dd5342107e88e1dbf183faffecf54b8dfd5f20723cf33701ce`
- 修订 target：[父子图独立 GraphRun 本地 ownership 实施方案](graph-independent-run-context-local-ownership-implementation.zh-CN.md)
  - SHA256：`d99e367637390757a1c87a6cbe3541612c1710e1abab8087f375026c82fec470`
- requirements：[GRC-LO-001 本地 ownership 拆分需求](graph-independent-run-context-local-ownership-requirements.zh-CN.md)
  - SHA256：`8a91cf520650fd127d756aab311714fc084e42e41f76723c56b0df6065b96a1e`
- 上一轮评审：[本地 ownership 实施方案继续独立评审](graph-independent-run-context-local-ownership-implementation-review.zh-CN.md)
  - SHA256：`304ed4d9a904734896d9fc8d7513e9a3ebc070fb4b05ff2fac56b7069d03f4bf`
- production baseline：`ebcd043fdfe324c610328a08cb1a3e8a14b37e10`
- 复审日期：2026-08-28

## 2. 回复中已合理关闭的范围项

以下 disposition 与 requirements 的当前文字一致，可以保留；它们不等于实现已获批：

| 项目 | 复审结果 | 依据 |
| --- | --- | --- |
| LO-R1 重复 root `run_id` admission | **范围关闭** | 回复 §3 LO-R1；requirements 允许 caller precondition，不新增 guard |
| LO-R2 source precedence | **关闭** | 回复 §3 LO-R2；target §2 以 normative source/requirements/target 分工 |
| LO-R3 continuation shape | **方向关闭** | 回复 §3 LO-R3；target §7 保留 `child_states` 与 family frames |
| LO-R4 projection/request/frontier | **方向关闭** | 回复 §3 LO-R4；target §8 保留现有 closed types |
| LO-R9 cross-parent overlap | **范围关闭** | requirements §4.3/§6/§7；target §1.2、§9.3 删除 `LO-B17` |

特别是 requirements 第 130–137 行的 identity 数学不变量（不同 parent `run_id` 的纯投影
必须不同）可以复用；它不能被当作 fresh-run admission 或并发安全证明。

## 3. 仍未闭合的 implementation blocker

### LO-RR1 — family evidence 没有唯一的 typed 输入/输出 owner

**证据。** 回复接受保留 family-shaped proof（回复第 132–150 行），target 又同时要求：

- runtime `GraphRunContext` 删除 `child_states`（target 第 328–343 行）；
- `lineage_states()`、`_validate_frame_index()`、`_validate_complete_context()` 的
  family 校验继续保留（target 第 394–408 行）；
- `RecoveryInvocationSeed` 保持 family-shaped（target 第 440–458 行）。

当前这些函数的实际签名仍以 `GraphRunContext` 及其 `root_state/child_states` 为输入：
`invocation.py:203-213,450-619`；`_GraphContinuation.admit()` 仍直接返回 family
`GraphRunContext`（`run_context.py:353-370`）。修订 target 的 coordinator 字段只有
family identity、root、children、recovered（target 第 142–159 行），没有
`ContinuationSnapshot`/validated family evidence 的 nominal 输入，也没有说明
`admit()`、lineage、frame validation、recovery seed 和 export 的精确 producer/consumer
签名与生命周期。

“把 snapshot 交给 coordinator factory”“输入改为 root + handle/snapshot evidence”
（target 第 428–433、405–408 行）是叙述，不是可编译的 contract。若实现者把 child map
留在 context，违反 target；若偷偷增加 tuple/第二 DTO，违反唯一真相；若直接从
coordinator 反射 handle，破坏 family proof 的 owner 边界。

**必须整改。** 只需复用现有 sealed `ContinuationSnapshot`/family recovery 证据，不要求
新增 persistence 或 per-run recovery protocol，但必须冻结：

1. `_GraphContinuation.admit()` 返回什么 typed evidence、谁验证 seal/root pairing；
2. `lineage_states`、frame validators、`recovery_seed` 接收的 exact 类型和输出；
3. local owner 建立前后 evidence 的存活范围；
4. state-only pending child 缺 evidence 时仍在任何 fence/start/claim/resource/node call
   前抛现有 `GraphValueUnavailableError`。

在此之前，Step 2/4（target 第 606–626 行）的实施顺序无法落地，不能声称 proof 与
mutation-before-proof 已保留。

### LO-RR2 — confirmed state 与 frame/handle 安装之间的失败窗口没有契约

**证据。** target 第 186–193、214–228、360–392 行规定“先 commit、再安装 frame、再加入
handle”，并在失败时声称没有 run/handle/frame；但 `GraphCommit` 在
`family_driver.py:123-147` 返回 exact successor 后，外部确认事实不可回滚。当前生产顺序
也明确存在这些窗口：

- child start 先确认 `StartGraphRun`，再写 child binding、graph-input frame
  (`family_driver.py:301-330`)；
- task completion 先替换 state，再安装 publication frame
  (`family_driver.py:241-263`)；
- completed child boundary 先从 state/frame 投影，再写 boundary
  (`family_driver.py:167-180`)。

因此下列情形都可能发生，target 没有裁决：

- `StartGraphRun` 已确认但 local input frame 安装失败；
- child state 已确认但 publication/boundary 安装失败；
- 某个 sibling 已成功 start，后一个 sibling start/frame 失败；
- frame 已安装但 canonical handle 加入失败；
- child terminal 已确认而 parent `SettleGraphNode` callback 抛错或返回 unknown。

现有测试已经把 post-commit frame failure 归类为 `FrameInstallationInvariantError`，并对
confirmed prefix 保留精确 partial handoff（`tests/execution/test_graph_api.py:1093-1163`
和 `1961-1996`）。target §7.3/§10.3 却只允许 recovery fence/resume 的
`_PartialCommitError`，没有说明上述新窗口应传播何种现有错误、保留哪个 owner/frame、
是否允许同 invocation 重试、如何避免重复 publication/boundary。

**必须整改。** 写出逐窗口的顺序表：confirmed 前失败、confirmed 后 frame 失败、handle
插入失败、sibling partial start、parent settlement failure；明确 state/frame/handle 的
保留或丢弃、现有 error type、partial handoff 是否适用及下一次调用的唯一行为。不得用
“原子加入 tuple”掩盖外部 commit 不可回滚，也不要求引入 persistence/failover。

### LO-RR3 — cancellation 后的 phase 与现有 engine 互相矛盾

**证据。** target §4.4（第 261–278 行）把“`RUNNING` 且无 active session”归入
`running/idle`，允许 prepare/resume/claim/child invoke；target §10.2（第 564–569 行）
又规定 cancellation close/quiesce session、保留 active execution token、不 fence、
不产生 continuation。现有 engine 对该组合没有 running/idle 语义：

- `prepare_superstep()` 在 `state.execution is not None` 时抛
  `ResultCollectionError`（`engine/superstep.py:61-68`）；
- `GraphExecutor.resume()` 拒绝 active execution（`executor.py:123-129`）；
- claim/fence reducer 只允许 exact lease（`state/graph_state/execution_transitions.py:79-103`）；
- `GraphExecutionSession.next()` cancellation 会 close 后重新抛出
  (`engine/session.py:268-305`)。

现有 public behavior 明确要求 cancellation 后保存 active state，再由下一次 state-only
调用先 fence 后恢复（`tests/execution/test_graph_api.py:2408-2451`）。所以“无 session +
active token”不是可继续 drive 的 idle phase，而是需要明确隔离/收敛的 orphaned-claim
边界。target 没有定义对该状态再次 `drive_quantum()`、`confirm()`、resume 或 child
invoke 的 exact error，也没有定义 coordinator 何时释放 handles、谁负责下一次 fence。

**必须整改。** 在不增加 status/field 的前提下，明确由现有
`state.execution` + session presence 派生一个不可运行边界：允许的唯一入口、错误优先级、
handle 清理和 state-only recovery 顺序必须写死，并保持上述 cancellation regression。

### LO-RR4 — local frame ownership 与 child-boundary producer 没有可执行 invariant

**证据。** target §6.3（第 358–392 行）要求 family frame 分区到 local index，并说
child boundary 属于 parent-local index；但当前 `ScopedFrameIndex` 只有四个 typed segments，
没有 owner coordinate，也没有 foreign-coordinate admission（`run_context.py:170-300`）。
其 `add_*` 方法只检查同段重复，所有 KEEP consumer（`request.py`、`admission.py`、
`resume_input.py`、`resume_admission.py`、`engine/recovery.py`）仍把它当作无 owner 的
family/frame availability。

更具体的矛盾是：`ChildBoundaryAvailabilityCoordinate` 只带 `child_scope_run`，而 target
第 378–385 行规定该 record 的 owner 是 matching binding 的
`parent_activation.scope_run`。没有 parent activation 字段或 partition API，按 coordinate
分区会把 boundary 放到 child index；按 parent 分区又无法由当前 index 自行验证 owner。
当前唯一 producer `_ensure_child_boundary()` 仍把 boundary 写进共享 family context
（`family_driver.py:167-180`），而 target 的 `project_child()` 只返回 projection
（第 244–257 行），没有定义何时、由哪个 owner 安装 parent-local boundary。

**必须整改。** 选择并写出一个唯一 typed 方案（例如复用 family index 加受控 local
partition API，或定义不改变 sealed family payload 的 private local view），并明确：

- owner 坐标如何编码/校验，foreign/duplicate 的 exact error 与 precedence；
- candidate resume overlay 如何跨 local owner 提供只读 availability；
- child completion 如何一次生成 parent boundary，何时进入 export merge；
- `project_graph_outputs()`/continuation validation 如何消费该 view。

不能靠约定把同一个无 owner 的 `ScopedFrameIndex` 叫作 local truth，也不能产生第二份
可变 frame/value truth。

### LO-RR5 — changed-file/test manifest 仍漏掉真实 consumer

**证据。** 回复第 220–232 行声称 manifest 已由真实 producer/consumer 反向生成，target
第 722–758 行列出五个 private consumer；但 source scan 发现
`tests/execution/test_result_boundary_contract.py:6-36`：

- 直接 import `project_graph_result`；
- 直接构造 `_new_context`/`GraphRunContext`；
- 依赖当前 boundary subclass rejection。

target 明确要改变 runtime context shape（第 328–343 行）并把 result projection 交给
coordinator（第 493–505 行），因此该测试要么迁移，要么必须给出不变的 exact API 证明。
它没有出现在 §14.3/§14.4，和“manifest complete”及“不留 compatibility path”相矛盾。

**必须整改。** 以最终 typed contract 重新执行 source/test consumer scan，把该文件及
任何因 signature/owner 变化而受影响的文件逐项列入 manifest；不得用 forwarding alias、
旧 helper 或 legacy test 隐藏漏改。

### LO-RR6 — “不新增 AST-only test”与 LO-B15/architecture manifest 冲突

**证据。** target Step 6（第 635–639 行）明文禁止新增或扩写 AST-only、private-helper-count
和源码布局测试；但 §13 将 `LO-B15`（第 683 行）交给
`tests/architecture/test_graph_execution_ownership.py`，§14.3（第 724–737 行）又要求
修改该文件。该文件本身从 `ast.parse` 读取全部 production module
（`tests/architecture/test_graph_execution_ownership.py:8-16`），并以 symbol owner、class
fields、调用者和 source shape 断言架构（例如第 375–450 行），不是 public/typed behavior
test。

这与用户要求“不加 legacy/private-source-shape/AST-only test”以及 target 自己的禁止项
不能同时成立。若修改该文件以增加 `_GraphRun`/cross-scope mutation 的 source assertions，
就是扩写 AST gate；若不修改，LO-B15 的 acceptance 不能依赖它。

**必须整改（二选一）。**

1. 保持该既有 AST test 不变，并从本 change unit 的新增 acceptance/manifest 中移除它，
   以既有 public/typed behavior 证据证明 ownership；或
2. 在 requirements owner/用户明确批准“不新增/不扩写 AST gate”的例外后，单独更新其
   scope/manifest，不能继续宣称 target 的 AST 禁止项仍有效。

本复审不要求新增任何替代 legacy/private test。

## 4. 不应借复审引入的范围扩张

关闭上述 blocker 不需要、也不授权加入：

- persistence、Store、checkpoint、codec、cross-invocation ID load、terminal receipt、
  replay、failover、worker handoff 或全局 admission registry；
- opaque child slot/live capability/fork API 或 per-run recovery protocol；
- 新 public `GraphRun`/`GraphRunRef`、State field/status/command、第二 runner/scheduler；
- cross-parent overlap 成功/拒绝测试，或任何 legacy/private-source-shape/AST-only 新测试。

整改目标只是把现有 family evidence、local frame partition、failure/cancellation phase 和
完整 consumer manifest 写成单一可执行 contract。

## 5. 达到开发条件的必要顺序

1. 按 LO-RR1 冻结既有 snapshot/evidence 的 typed admission、validation、recovery 和
   export 接口；
2. 按 LO-RR2 给出 post-confirm frame/handle/boundary 失败矩阵，并与现有
   `FrameInstallationInvariantError`/confirmed-prefix 语义对齐；
3. 按 LO-RR3 固定 cancellation 后 active token 的不可运行边界与 state-only fence 入口；
4. 按 LO-RR4 固定 local frame owner、parent boundary producer 和 merge/overlay 算法；
5. 按 LO-RR5 反向补齐最终 source/test manifest；按 LO-RR6 解决 AST test policy 冲突，
   不新增或扩写 legacy/private test；
6. requirements owner 批准 `GRC-LO-001` 及修订 target；随后取得用户明确的 production/test
   implementation authorization；
7. 获批后才按窄 manifest 实施并运行 strict typing、lint、format、behavior、coverage、
   build/package、适用 pre-commit 和 Markdown 检查。complexity gate 继续单列
   `USER-EXCLUDED / NOT RUN`，不得把未运行的完整 `make check` 写成通过。

## 6. 本轮验证记录

| 检查 | 结果 |
| --- | --- |
| 回复/target/requirements/prior-review SHA256 | 与第 1 节冻结值一致 |
| source consumer scan | **发现遗漏**：`test_result_boundary_contract.py` 未列入 target manifest |
| local frame owner scan | **发现缺口**：`ScopedFrameIndex` 无 owner admission invariant |
| targeted baseline behavior | **3 passed**：nested facade、独立并发 ordinary runs、缺失 child boundary |
| Markdown/代码改动 | 本轮尚未修改 target、production 或 tests；只新增本复审文档 |
| complexity gate | **USER-EXCLUDED / NOT RUN** |
| legacy/private-source-shape/AST-only test | **未新增、未扩写、未运行为本轮 gate** |

本轮不把 dirty worktree 的全量 `make check`、coverage 或 build 当作 target 证据；尤其不把
包含 complexity gate 的聚合目标写成通过。

## 7. 最终 ledger 与授权状态

```text
LO-RR1 family evidence typed boundary       = OPEN / BLOCKER
LO-RR2 post-confirm failure windows         = OPEN / BLOCKER
LO-RR3 cancellation lifecycle phase         = OPEN / BLOCKER
LO-RR4 local frame/boundary ownership       = OPEN / BLOCKER
LO-RR5 changed-file/test manifest           = OPEN / BLOCKER
LO-RR6 AST-only policy contradiction         = OPEN / BLOCKER

scope corrections LO-R1/2/3/4/9             = ACCEPTED (not implementation proof)
technical blockers                           = 6
implementation target                       = CHANGES REQUESTED / NOT READY
requirements owner approval                 = PENDING
user production/test authorization          = PENDING
production / State / Store / API / tests    = NO CHANGE IN THIS REVIEW
persistence / failover / overlap gate       = OUT OF SCOPE
complexity gate                              = USER-EXCLUDED / NOT RUN
legacy tests                                 = NO NEW / NO EXPANSION
```

**最终结论：** 逐项回复解决了上一轮的范围和事实源冲突，但没有给出足以实现的 family
evidence handoff、local frame owner、post-commit 失败语义和 cancellation phase。上述
问题关闭并取得 requirements owner 与用户的明确批准前，不能进入开发；本复审也不授予
任何 production/test 修改权限。

本复审唯一 change-unit manifest：

```text
mote-kernel/docs/graph-independent-run-context-local-ownership-implementation-review-response-review.zh-CN.md
```
