# 父子图独立 GraphRun 状态实施方案独立技术评审

> **结论：CHANGES REQUESTED / NOT READY FOR IMPLEMENTATION。**
> 目标文档有正确的 ownership 方向，但把当前规范明确排除的跨 invocation 恢复、durable
> terminal boundary、跨 run 幂等/CAS、worker lease 和 aggregate budget 作为交付能力；
> 这些协议尚未有 requirements owner、唯一事实源或可执行事务定义。当前存在 14 个
> blocker，不能标记为 implementation baseline，也不能进入其 Phase 0–1。
> **本评审不授权 production、State、Store、protocol、persistence 或测试改动；不新增或扩写
> legacy test。**

## 1. 评审对象与冻结输入

- 评审日期：2026-08-27
- 评审对象：[父子图独立 GraphRun 状态实施方案](graph-independent-run-context-implementation.zh-CN.md)
- 目标 SHA256：2bfa6876305317d0b0b375b476c17a1c47e5798c912e472c80a87f804f307bd5
- 关联调研：[可行性调研](graph-independent-run-context-feasibility-research.zh-CN.md)
- 调研 SHA256：d0068b0c72558cef0945102a87b00a5a84ee6339aa19b00233370c3b5e07dc7f
- 当前规范事实源：[Graph 节点输入/输出契约实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 规范源 SHA256：233ba6be90d9ae3d7d7c3817c584ca44dfd2d9a76dff3a36968cbda136043f09
- 参考架构：[架构说明](architecture.zh-CN.md)、[GraphRun resume requirements](frontier-node-resume-requirements.zh-CN.md)
- production Git HEAD：d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5

目标、调研和规范源是三个不同职责的文档：目标只应拥有已获批准的 target shape，
调研只拥有可行性分析，规范源拥有当前行为。本文只拥有本轮 review 裁决，不取代其中
任何一个 owner，也不把 review 文字写回规范源。

当前工作树有用户既有修改（包括 family_driver.py、result.py、README、多个历史
review/docs、examples 以及 monorepo sibling 的 py.typed）。这些修改被保留，不归因
于本 review；目标文件本身是未跟踪文件，本轮只新增本 review 文件。

## 2. 审核边界与门禁口径

本 review 是 docs-only change unit：

- 不修改目标实施方案，不修改 production、State schema、command/reducer、Store、
  protocol、persistence、public API 或现有测试；
- automated complexity/health/baseline/ratchet gate 按用户要求**不参与本轮裁决**，
  不修改其配置，不新增 complexity 豁免或门禁；
- complexity 排除不等于放宽其他质量要求：现有 lint、format、strict typing、
  behavior tests、build/package 和文档完整性仍应通过；
- 不新增、扩写或依赖 legacy/private-source-shape/AST test。行为测试只能在之后取得
  requirements 与实施授权后，按 public contract 增加；
- make check 不作为本轮聚合命令：其无条件包含 complexity-ratchet 和 complexity，
  与本用户范围不一致，必须拆开记录组件结果。

## 3. 总体复核矩阵

| 维度 | 结果 | 结论 |
| --- | --- | --- |
| ownership 方向 | **部分通过** | “parent 不持有 child authoritative state”是合理目标，但实现边界未闭合 |
| 与当前 normative source 对齐 | **不通过** | 新增能力直接落在当前明确排除的 Store/recovery/幂等范围 |
| 唯一事实源与 requirements owner | **不通过** | 无 requirement ID、批准状态、target owner 或 source precedence |
| 复用既有基础设计 | **部分通过** | pure reducer、exact successor、Graph facade、typed projection 可复用；新协议未证明可复用 |
| State/frame/continuation 语义 | **不通过** | 删除 child snapshot 与保持现有 continuation contract 不能同时成立 |
| terminal output 边界 | **不通过** | typed in-memory view 不是 durable、可重读的 wire/storage representation |
| 事务、崩溃与幂等 | **不通过** | 多个写入窗口只有叙述，没有 authoritative replay 协议 |
| resolver、scope、lease、budget | **不通过** | 新 invocation 的装配、coordinate、worker 和总预算未定义 |
| 测试与门禁计划 | **不通过** | 没有 case-level evidence；make check 与 complexity 排除相冲突 |
| 实施准入 | **不通过** | NOT READY，无 production/test authorization |

## 4. Blockers

### R1 — 目标与当前规范事实源直接冲突

**证据。** 目标 §2.1（第 63–70 行）、§5.2–§5.5 和 §6.3 把按 child_run_id 加载
state/material、durable terminal output、跨 run CAS/幂等和崩溃恢复列为交付。当前规范源
第 60–74 行明确规定：

- State 不保存 concrete frame、publication、continuation 或 Store handle；
- Kernel 不实现 Store、数据库、journal、checkpoint、result reference、output codec；
- 不承诺进程重启 transient-value recovery、retry/backoff、exactly-once、
  multi-worker arbitration 或新的 nested scheduler。

架构说明第 12–14 行也把 Graph.run() 限定为显式 state/continuation 输入，并说明
callback 是提交边界而非 durability 承诺；resume requirements 第 588–603 行明确说
child projection 由调用方提供、不新增 state-store lookup port，且不承诺跨调用 input
consistency。

**影响。** 这不是 GraphRunContext 的局部 ownership refactor，而是新的
State/Store/execution boundary。目标不能在现有规范和批准范围下称为
PROPOSED / READY FOR REVIEW implementation baseline。

**必须整改。** 二选一并在唯一 owner 中写死：

1. 将本文降级为 research/architecture proposal，删除跨 invocation、durability、
   CAS、lease/budget 的实施承诺；或
2. 新建独立 requirements/change unit，明确 source precedence、owner、批准状态和
   新协议，再重新提交 implementation review。未完成前不得实施任何 Phase。

### R2 — 没有唯一 requirements owner，形成第二份 target truth

**证据。** 目标文档只登记 PROPOSED / READY FOR REVIEW 和调研链接（第 3–9 行），
没有 requirement ID、owner、approval、与规范源的正式关系。关联调研第 8 行的结论仍是
CONDITIONALLY FEASIBLE / NOT READY FOR IMPLEMENTATION。现行 requirements 的 owner
分工在第 24–41 行，当前登记的新增 P2 只有 A-v2（第 166–204 行），没有本目标。

目标第 210 行还明确写着 GraphRunRef 的 owner “需在 Phase 2 评审确定”。核心
identity 在实施开始后才决定 owner，不能满足唯一真相或零债务。

**必须整改。** 先登记独立 requirement ID、唯一 target/requirements owner、reviewed
SHA、批准状态、适用的 GSP-P01–GSP-P08（若属于该治理范围）和 per-change manifest。
所有未决 owner 必须在 implementation target 内冻结，不能以“建议名称”延后。

### R3 — GraphRunContext、continuation 与“切断 child ownership”互相矛盾

**证据。** 目标第 13–16、104–108、453–467 行要求移除 aggregate child_states、
让 continuation 只覆盖一个 run，同时声称现有 nested、grandchild、awaiting/resume
语义不变。当前源码：

- run_context.py:315-328 的 complete/recovered snapshot 都包含 child_states 和统一
  frames；
- run_context.py:364-370 从 continuation 恢复这些 child bindings；
- run_context.py:382-421 让同一个 context 的 state_at()/replace_state() 在 root/child
  之间切换；
- family_driver.py:193-211 从该共享 context 读取 child state/frame。

规范源第 440–455 行又冻结了 complete continuation 必须携带 root/child input、
publication、nested state/frame snapshot，并明确没有序列化协议。

**影响。** 删除 child snapshot 会改变现有 continuation/resume 可观察行为；继续把
child state/frame 放入 parent continuation 又没有切断 parent authoritative ownership。
“不改 public API、Phase 1 不改 Store”与“新 invocation 独立恢复”不能同时成立。

**必须整改。** 明确这是哪一种 contract：

- 若保持当前 contract，只做同一 invocation 的 local ownership 重构，并删除所有
  cross-invocation recovery 声明；
- 若引入 per-run continuation/recovery，先单独批准新的 public/internal contract，
  更新规范源、requirements、codec 和迁移规则。

禁止以兼容 alias 或双 authoritative path 规避选择。

### R4 — GraphOutputView[GraphValueT] 不能证明 durable terminal boundary

**证据。** 目标第 257–282 行把 CompletedChildBoundary.output 定义为任意
GraphOutputView[GraphValueT]，同时第 245–251 行要求 codec，但没有 payload/encoding、
codec identity/version、大小/敏感性限制、decode/admission owner 或 durable representation。
规范源第 71–73 行明确不实现 output codec 和进程重启 transient-value recovery。

**影响。** 一个含任意 Python concrete value 的 immutable typed object 只能说明进程内
类型边界，不能说明跨 invocation 可写入、可读回或 read-after-commit。仅有
terminal_revision 不能补足序列化、权限和版本语义。

**必须整改。** 若确实需要 durable output，另立并批准 terminal wire/storage
contract：codec identity/version、编码失败、大小/安全限制、owner、可见性和 retention
全部固定；否则把 boundary 限定为 invocation-local projection，并移除 §2.1、§6、§13
的 durability/重启承诺。

### R5 — terminal state/boundary 事务和 parent publication crash window 未闭合

**证据。** 目标第 350–380 行承认 terminal state commit 与 boundary publish 可能是
两个物理写入，但只要求“显式建模”；第 382–390 行的重启表没有定义 outbox/pending
事实、谁在 child 退出后重试 publish、原子性、read-after-commit 或 retention owner。
第 637–650 行仍把这些裁决留给后续。

当前 parent 路径在 family_driver.py:241-263 先确认 SettleGraphNode，再安装
publication frame；目标第 365–370 行沿用同一顺序。若进程在 parent state commit
成功后、frame installation 前崩溃，parent state 已显示 nested success，但 concrete
output 不在 state。reducer execution_transitions.py:150-163 要求 settlement 目标仍是
当前 Pending node，不能简单重复 settle；目标没有重新安装 publication 的 authoritative
replay 路径。

**影响。** 无法证明“child terminal 后 parent 一定能重读并继续 routing”，也无法证明
重复 settlement/ack 不会产生第二 publication。

**必须整改。** 选定一个 authoritative transaction owner，并定义 terminal publish、
parent settlement、publication install 的原子事务或 durable pending/outbox/replay
协议；固定失败后的 owner、receipt、visibility、ack/retention 和 exact retry response，
再用可重复 fault case 覆盖每个窗口。未闭合前不得声称 Phase 3/4 可实施。

### R6 — child start intent 与 input admission 不是原子事实

**证据。** 目标第 352–358 行把 start state commit 与 input/material commit 分开；
第 384–390 行允许在二者之间崩溃。当前 family_driver.py:301-330 先在 invocation
内 materialize input，提交 StartGraphRun，随后才把 child state 和 input frame
安装到共享 context。崩溃会留下已提交的 RUNNING child，却没有可加载的 input。

child_graph_run_id() 目前只由 parent run、superstep、nested node 组成
（state/graph_state/identity.py:36-49），不含 input identity/digest；目标没有定义
start-intent record、codec/version 或同一 ID 的不同 input 冲突规则。

**影响。** 重试可能在同一个 deterministic child ID 下 materialize 不同 input，或在
缺 input 时错误地重跑带副作用节点；“不重复执行 child”没有可验证基础。

**必须整改。** 为 start intent 固定 parent activation、definition/version、input
identity/digest、codec 和 owner，并使 admission 原子化，或持久化可重放的 pending
事实；定义 same/different payload 的精确结果。若不引入这些能力，必须保留当前
invocation-local input 语义。

### R7 — definition resolver 与 port injection path 缺失

**证据。** 目标第 41–49、191–210、469–489、508–528 行要求新 invocation 只凭
child_run_id/GraphRunRef load/drive child。当前 facade.py:546-562 的 _compile()
只从当前 Graph 实例在内存中收集/安装 compiled graph；facade.py:564-613 的
Graph.run() 只有 values/state/continuation、commit 和 limits 参数，没有
state-load/material/definition-resolver/invoker 参数。

架构说明第 12–14 行还禁止 Graph 持有 run snapshot、session 或 transient output，
目标也禁止 global registry。故仅凭 GraphRunRef 无法找到 compiled definition、
codec、recovery material、权限 owner 或跨 worker 的装配入口。

**必须整改。** 明确一个不依赖隐藏 registry 的 definition resolver、port assembly、
权限/ownership 和 worker handoff contract；或者删除“按 ID 新 invocation 恢复”的目标，
将 child-call 限定为当前 invocation 内部实现。任何新增 public overload 都必须另行 API
review，不能从现有 Graph.run() 语义隐式推导。

### R8 — scope coordinate 仍未裁决，且 local-root 方案与现有 validator 冲突

**证据。** 目标第 641–646 行把 child 使用 scope=() 还是完整外部 path 留到后续；
第 210 行也把 identity owner 留空。当前 execution/identity.py:37-52 的
child_scope_run_for_activation() 派生完整 scope path，executor.py:82-96 要求
scope_run.scope == compiled_graph.definition_scope，非空 scope 的 state.parent
必须匹配 scope 尾部。

规范源第 42、53 行已冻结 root 使用空 scope、child 使用完整 node-ID path 与
child_graph_run_id()。因此 local-root 不是“内部细节”，而是会改变 diagnostics、
frame coordinate、grandchild derivation 和 validator contract 的协议变更。

**必须整改。** 在任何 Phase 1 前冻结一个 coordinate universe，并同步
identity/executor/diagnostics/frame/continuation/grandchild 的 owner 与测试。若采用
规范源当前的完整 path，应明确它不等于共享 state ownership；若改为 local root，必须
另立迁移和规范变更，不能在 §12 延期。

### R9 — 新类型/port 与现有 owner 重叠，零债务没有结构账本

**证据。** 目标第 187–210、253–300、530–560 行预设新增 GraphRunRef、三种 boundary、
start/ack receipt、ChildRunInvoker、多个 port 和 aggregate budget owner，却没有
producer/consumer call graph、删除闭包、最多新增面或 canonical owner ledger。现有
owner 已有：

- GraphRunState、ParentGraphActivation、ScopeRunCoordinate；
- MissingChild/ActiveChild/CompletedChild/AbortedChild（execution/result.py:167-191）；
- AwaitingResume、GraphTransition、GraphResult 和 ScopedFrameIndex。

目标 §5.4 的 AwaitingChildBoundary、§5.5 的 ChildAwaitingResume 与现有
AwaitingResume 也没有唯一 variant owner；“名称可调整”不足以证明职责不重复。
当前 GraphRunStatus 只有 RUNNING、COMPLETED、ABORTED；child awaiting 是 RUNNING
frontier 的 blocked/resume 事实，不是新的 GraphRunStatus。目标没有给出这些 boundary
variant 与既有 frontier projection 的一一映射。

**影响。** 直接实现会产生平行 DTO、adapter、projection 或第二 frame/result truth，
违反零新增负债和复用基础设计。

**必须整改。** 先逐项说明已有类型为何不能复用，并给出唯一 owner、producer、
consumer、删除对象和 exact call graph；能复用的继续复用，不能以通用 Store/Context
DTO 或兼容 wrapper 补洞。特别要先裁决 GraphRunRef 和 awaiting variant 的 owner。

### R10 — idempotency/CAS 只有 key 表，没有可执行协议

**证据。** 目标第 304–316 行只列出 deterministic key 和“冲突处理”短语。当前
GraphCommit 仍只是 family_driver.py:115-120 的单 callback；commit_transition()
只在 family_driver.py:123-147 检查 callback 返回的 successor 是否与 reducer
candidate 相等。State reducer 在 execution_transitions.py:150-163 要求
SettleGraphNode 针对当前 Pending node 和 active execution lease。

**影响。** parent 已 settlement 后再次收到同一 key，现有 reducer 会因不再 Pending
而拒绝；key 表没有规定“same key/same payload”返回哪个 successor、“same key/different
payload”抛什么稳定错误、callback 已写入但响应丢失时如何 load 判定，或 boundary read
与 parent state 如何一致。key 本身不是 receipt、CAS 或 exactly-once 语义。

**必须整改。** 为 start、transition、publish、read、settle、ack 各自定义状态机、
authoritative owner、receipt、same/different payload 结果、stale/revision/token
冲突错误和 callback-unknown 重试规则；外部副作用仍不得被 kernel 宣称 exactly-once。

### R11 — worker lease 与 aggregate budget 未闭合

**证据。** 目标第 413–419、637–663 行要求跨 invocation/并发 child 不超过 aggregate
预算并避免两个 worker 同时驱动，但现有 GraphExecutionLease 只有 token
（state/graph_state/model.py:32-40），没有 worker identity、expiry、renewal、
stale fencing 或 crash reclaim。规范源第 71–74 行明确不实现 multi-worker arbitration。

当前 family_driver.py:333-339、387-432 把同一个 ExecutionLimits 传给每个 child
quantum；planner.py:15-27 只按单个 state/superstep 检查上限。目标没有定义 parent
max_supersteps 跨 child 如何累计、sibling 顺序、重试/崩溃后的返还或并发开启条件。

**必须整改。** 若目标仍要跨 worker/并发，另立 lease 与 aggregate-budget owner，定义
acquire/renew/expiry/fence/reclaim、预算账本和 fault semantics；若第一阶段只能串行，
把它冻结为唯一语义并删除“现有 limits 不变/未来并发可恢复”的笼统 DoD，不得用每个
child 的本地默认值冒充 aggregate 上界。

### R12 — cancellation、exception 和 child lifetime 没有 typed protocol

**证据。** 目标第 405–411 行写明父取消是否传 child “必须是显式 protocol”，但没有
command、receipt、owner、重放结果或 parent/child 终态转换；commit port 异常只被标为
“outcome unknown”。架构说明第 21–25 行的现有 contract 要求 session aclose()
先收敛、取消不能中断 cleanup，且没有跨 run cancellation。

**影响。** child 可以超出 parent invocation 存活，但没有谁负责 fence/close/回收；
异常发生在 state commit 前后时 parent 也没有可观察、可重放的 typed disposition。宣称
“existing cancellation/exception semantics 保持不变”没有证据。

**必须整改。** 定义 child-cancel command、receipt、lease/fence owner、parent pending/
terminal mapping、unknown-commit recovery 和 idempotent replay；或者把 child lifetime
严格限制在现有 invocation，并删除独立 child cancellation 声明。

### R13 — assembly fallback 语义相互矛盾

**证据。** 目标第 483–487 行同时要求“没有 state port 时保留 invocation-local mode”、
“required port 缺失时 assembly fail”和“optional capability 缺失时移除步骤”；第
245–251 行又规定没有 codec 只能 invocation-local，第 670–679 行和第 14 节则要求
required typed port 缺失必须 fail closed。

**影响。** 同一个调用可能因装配能力缺失而静默切换为另一种语义：调用方无法知道自己
得到的是仅进程内执行还是可恢复 child，违反 deterministic behavior、fail-closed 和
唯一 owner。

**必须整改。** 把模式分成明确的 capability：

- independent mode 缺少任何 required state/material/boundary/codec port 就 assembly
  fail；
- invocation-local mode 是单独、显式选择的现有语义，不由缺失能力隐式触发；
- optional port 的“移除步骤”只能用于不改变结果契约的可选步骤。

### R14 — 测试/门禁证据不足，且文档内部授权口径自相矛盾

**证据。**

1. 文档信息第 8 行说本轮不改测试，但 Phase 0 第 425–442 行计划新增
   tests/execution/ contract/fault tests。
2. §10 第 562–603 行大多是文件级或行为族级清单，没有 exact path::test_case、
   输入构造、异常 type/text/cause、mutation-free 断言、commit 次数/顺序和
   crash-window 重现方法。
3. 当前 requirements 的 GSP-A03/A06（requirements 第 97–102 行）要求 case-level
   evidence、exact-shape/tamper（适用时）和 per-change manifest；目标没有
   GSP-P01–GSP-P08 applicability matrix，也没有独立 requirement approval。
4. §10.4 第 603 行、DoD 第 666 行要求完整 make check，但 Makefile:14-18,39
   无条件包含 complexity-ratchet/complexity；这与用户明确“无视复杂度门禁”冲突。
5. §12 把 coordinate/codec/transaction/owner 等核心决定留到以后，第 681 行却宣布
   “先实施 Phase 0–1”，无法形成确定的准入顺序。

**必须整改。** 在取得 requirements/target approval 后，补齐每个 public behavior/
protocol 的 exact case matrix 和适用 gate mapping；新增测试只验证可观察 contract，
不得冻结 private source shape 或成为 legacy gate。将 Definition of Done 改为分别列出
非复杂度组件（Ruff、format、Pyright、behavior tests、coverage、build/package、
适用 pre-commit）并明确 complexity 为 USER-EXCLUDED / NOT RUN，不要声称
make check 完整通过。解决 §1、§2、§5、§12、§14 的顺序矛盾后再提交复审。

## 5. 可以保留的基础方向

以下方向与当前架构一致，但只能作为后续 target 的候选，不构成实施授权：

- Graph 继续是唯一 public facade，execution 继续是唯一 execution engine；
- 纯 reduce_graph_run()、GraphTransition、exact successor、durable-first 和
  revision/token 校验继续复用；
- GraphRunState 保持 control truth，不把 concrete output/frame 塞进 State；
- ParentGraphActivation、现有 deterministic child_graph_run_id() 和 ScopeRunCoordinate
  可作为 identity 基础，但 deterministic ID 本身不等于完整 start/settlement idempotency；
- 现有 MissingChild/ActiveChild/CompletedChild/AbortedChild 投影和 parent
  SettleGraphNode 路径可作为语义来源；child blocked 仍保持 RUNNING、parent nested
  保持 Pending；
- parent 不直接写 child state、child terminal 不直接修改 parent frontier；
- 若只做 Phase 1，范围应收窄为同一 invocation 内的 frame ownership 隔离，并以现有
  continuation/nested behavior 为硬约束，不宣称跨 invocation recovery。

## 6. 最小整改路径

### 路径 A：回到当前需求范围

将目标状态改为 research/architecture proposal，保留 ownership 分析和可行性结论，
删除或明确延期所有 state load、material codec、durable terminal、cross-run CAS、
worker lease、aggregate budget、独立 cancellation 和 public child-call 承诺。若要做
local ownership refactor，单独形成不改变现有 Graph.run()/continuation contract 的
小 change unit；本轮不改 production/tests。

### 路径 B：确实需要独立 child invocation

先创建新的 requirements/change unit，按以下顺序冻结后再写 implementation target：

1. 唯一 requirement/target/approval owner、source precedence、scope 和 public/internal
   API 边界；
2. definition resolver、state/recovery/boundary port、codec wire representation、
   权限和 assembly mode；
3. child start/input、terminal publish、parent settlement/publication install 的
   原子性或 outbox/replay 协议；
4. 每个操作的 CAS/idempotency receipt、unknown-commit/read-after-commit/retention；
5. coordinate universe、lease/worker fencing、aggregate budget、cancellation；
6. 现有类型复用/删除账本、producer/consumer call graph、case-level evidence 和
   非复杂度 gate matrix。

完成这些 requirements 和独立技术评审前，不能把新能力写入当前 normative source，
不能进入 Phase 1，也不能新增测试。

## 7. 当前快照验证记录

以下检查只证明当前用户 dirty baseline 的健康状况，不证明目标设计已经可实施；本轮
没有运行 complexity gate，也没有运行或修改 legacy/private-source-shape gate。

| 检查 | 结果 |
| --- | --- |
| python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py | **PASS**：841 passed in 98.09s |
| python -B -m ruff check src tests | **PASS**：All checks passed |
| python -B -m ruff format --check src tests | **PASS**：152 files already formatted |
| pyright | **PASS**：0 errors, 0 warnings, 0 informations |
| env COVERAGE_FILE=/tmp/mote-independent-run-context-review.coverage python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py --cov=mote_kernel --cov-report=term-missing | **841 passed**；coverage 99.94%，因既有 dirty source 的 family_driver.py:515、result.py:164 未达到项目 fail-under=100，命令按基线事实失败 |
| python -B -m build --no-isolation | **PASS**：sdist 与 wheel 构建成功 |
| python -B -m twine check dist/* | **PASS**：wheel 与 sdist 均 PASSED |
| git diff --check / git diff --cached --check | **PASS** |
| make check | **未运行**：其聚合目标无条件包含本轮排除的 complexity gate |
| legacy/private-source-shape/AST tests | **未新增、未扩写、未作为准入条件** |

coverage 的两个未覆盖分支来自评审开始前已存在的用户修改（family_driver.py、result.py），
本 review 没有修改或用测试掩盖它们；因此该结果是当前 dirty baseline 的事实，不归因于本
docs-only change unit。

## 8. 最终裁决与 change-unit manifest

~~~text
target implementation                 = CHANGES REQUESTED / NOT READY
blocker                                = 14
concept direction                     = PARTIAL PASS
normative alignment                   = FAIL
single truth / requirements owner     = FAIL
zero new debt                         = FAIL
infrastructure reuse                  = PARTIAL / INCOMPLETE
terminal durability / recovery        = NOT PROVEN
idempotency / lease / budget          = NOT PROVEN
complexity gate                       = USER-EXCLUDED / NOT RUN
non-complexity baseline               = PARTIAL (behavior/typing/lint pass; coverage baseline not green)
production / State / Store / protocol = NO CHANGE / NO AUTHORIZATION
tests                                  = NO CHANGE / NO AUTHORIZATION
legacy test scope                     = UNCHANGED
~~~

本评审的唯一 change-unit manifest：

~~~text
mote-kernel/docs/graph-independent-run-context-implementation-review.zh-CN.md
~~~

目标实施方案、调研、规范源、production、State、Store、protocol 和 tests 均未被本
change unit 修改。只有在 R1–R14 全部关闭、requirements owner 完成批准、target SHA
重新冻结并通过新的独立 review 后，才可讨论任何实现授权。
