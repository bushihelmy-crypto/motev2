# 父子图独立 GraphRun 状态实施方案（边界修订版）

> **状态：REVISED / ARCHITECTURE BOUNDARY / NOT READY FOR IMPLEMENTATION。**
> 本版接受上一轮独立评审的技术结论，并撤回尚未具备 requirements、Store、codec、
> transaction、lease 和 budget 前置条件的“独立 child invocation”实施目标。本文现在
> 只拥有边界、复用原则和未来准入条件；不授权 production、State、Store、protocol、
> persistence、public API 或测试变更。

## 1. 文档信息与事实分工

- 修订日期：2026-08-27
- 原目标：[父子图独立 GraphRun 状态实施方案](graph-independent-run-context-implementation.zh-CN.md)
- 原目标 SHA256：2bfa6876305317d0b0b375b476c17a1c47e5798c912e472c80a87f804f307bd5
- 上一轮评审：[独立技术评审](graph-independent-run-context-implementation-review.zh-CN.md)
- 上一轮评审 SHA256：9f6a27958432153b6aed31994e705fa2060c52ae16d09d5ce3358ee7ff71bcb3
- 本轮回复：[评审回复](graph-independent-run-context-implementation-review-response.zh-CN.md)
- 关联调研：[GraphRun 独立状态可行性调研](graph-independent-run-context-feasibility-research.zh-CN.md)
- 当前行为规范：[Graph 节点输入/输出契约实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 架构参考：[架构说明](architecture.zh-CN.md)、[GraphRun resume requirements](frontier-node-resume-requirements.zh-CN.md)
- requirements 参考：[Graph 执行语义保持型简化需求](graph-semantics-preserving-simplification-requirements.zh-CN.md)
- production Git HEAD：d35b74f214e70faf6fe93b13fe9a165a3fa0f0d5

事实 owner 固定如下：

| 内容 | 唯一 owner |
| --- | --- |
| 当前 public/API、State、frame、continuation、nested、resource 和错误行为 | 当前 normative source |
| requirement ID、批准状态和阶段准入 | requirements owner |
| 本文的未来能力边界、撤回项和准入前置条件 | 本文 |
| 评审裁决、异议和验证记录 | 独立 review/response；不拥有行为或 target |

若本文与当前 normative source 冲突，以 normative source 为准；未来能力在独立
requirements 和批准前，不得被本文文字追认为当前行为。本文不把“有一个 Python
对象”写成“可跨 invocation 或进程恢复”。

当前工作树含用户既有的 production、README、examples 和大量 docs 未提交修改。它们
被保留且不属于本次修订；本次只修改本文并随后新增独立 response。

## 2. 修订决定

### 2.1 撤回的 implementation target

本版明确撤回以下当前实施承诺：

- child 仅凭 child_run_id 在新 invocation 中 load/drive/recover；
- child terminal output 的 durable publish/read/ack；
- 跨 run CAS、幂等 receipt、read-after-commit 和 crash recovery；
- state/recovery material 的 Store、codec、checkpoint 或 wire representation；
- definition resolver、worker handoff、lease arbitration 和 aggregate budget；
- 跨 invocation cancellation、child lifetime protocol 和新的 public overload；
- GraphRunRef、ChildRunInvoker、ChildBoundary、start/ack receipt 等新增 production
  类型、port、adapter 或 owner。

这些不是“暂时未写完的细节”，而是当前规范明确排除的新 State/Store/execution
boundary。没有独立 requirements、owner 和批准，本文不定义它们的字段、算法、错误
文本或 phase。

### 2.2 当前仍有效的 contract

当前唯一可实施的 contract 仍是：

1. Graph 是唯一 public 图构建和执行 facade，execution 是唯一 execution engine；
2. Graph.run() 接受当前规范规定的 values、显式 GraphRunState、continuation 和 resume
   输入，不增加按 ID load 的隐式入口；
3. GraphRunState 是 control truth；concrete input/output frame、publication、
   continuation 和 Store handle 不进入 State；
4. pure reducer、typed command、revision/token、durable-first 和 exact successor
   继续由现有 owner 负责；
5. nested projection 使用现有 MissingChild、ActiveChild、CompletedChild、
   AbortedChild 语义，child blocked 时保持 RUNNING，parent nested node 保持 Pending；
6. continuation 继续遵守当前 complete/recovered snapshot contract，不在本文偷偷
   改为可序列化的 per-run checkpoint；
7. 没有独立 persistence/recovery contract 时，结果只具有当前 invocation/state
   contract 的语义，不宣称 crash 或跨进程恢复。

### 2.3 本版的完成标准

本次 docs-only 修订的完成标准不是“模拟一个未来 Store”，而是：

- 当前行为与未来目标不再共用一套含糊的 truth；
- 没有未决定 owner 的新类型、字段、port、fallback 或 phase；
- 复用基础设计的边界可直接核对；
- implementation、requirements、review 和 response 的职责不重叠；
- 不修改 production、State、Store、protocol、persistence 或 tests；
- 不新增或扩写 legacy/private-source-shape/AST test；
- complexity gate 明确排除，其他适用质量检查仍如实记录。

## 3. 当前实现基线

以下事实来自当前工作树，不是未来 target：

| 位置 | 当前事实 | 本版处理 |
| --- | --- | --- |
| execution/facade.py:564-613 | Graph.run() 有 values、state、continuation、resume、commit 和 limits 路径；没有按 run_id load/material port | 保持现有 public shape，不推导新 overload |
| execution/run_context.py:315-421 | context 同时持有 root_state、child_states 和一个 ScopedFrameIndex；state_at()/replace_state() 在 family envelope 中切换 scope | 作为当前 continuation/frame contract 的事实，不在本版删除或改名 |
| execution/run_context.py:452-469 | complete/recovered continuation snapshot 携带 child bindings 和 shared frames；复制/序列化被拒绝 | 保持 invocation-local 语义，不宣称独立 checkpoint |
| execution/family_driver.py:123-147 | commit_transition() 生成 GraphTransition，调用单一可选 GraphCommit，并要求 exact reducer successor | 继续复用，不扩展为万能 Store、load、publish 或 ack |
| execution/family_driver.py:193-211、301-330、387-432 | parent 从 shared context 读取 child projection；child start/input/frame 与 parent 共用 invocation 生命周期 | 记录为未来边界缺口，不以新增 wrapper 遮掩 |
| execution/engine/frontier.py:47-106 | CompletedChild output 投影为 parent TaskSuccess，要求 child projection 精确覆盖 pending activation | 保留现有 typed projection 和 SettleGraphNode 路径 |
| state/graph_state/model.py:57-71 | GraphRunState 已有 run/definition/status/frontier/lease/resources/parent/revision | 保持 State schema；不添加 concrete output/handoff 字段 |
| state/graph_state/identity.py:36-49 | child_graph_run_id() 由 parent run、superstep、node deterministic 派生 | 可作未来候选 identity，但不等于 start/terminal 幂等协议 |
| execution/executor.py:82-96、identity.py:37-52 | compiled definition scope、parent activation 和完整 child coordinate 有严格校验 | 当前完整 path 是既有事实，不在本版延期或改成 local root |

当前流程可以准确描述为：

~~~text
一次 root Graph.run
  = root GraphRunState
  + 多个 child GraphRunState binding
  + 一个 GraphRunContext / ScopedFrameIndex
  + 一个 family-driver 调度循环
  + 一个 GraphCommit callback
~~~

这表示当前实现的 family invocation 形态，不表示已经存在可跨 invocation 的独立
GraphRun。本文不把两者混为一谈。

## 4. 可复用的基础设计与硬约束

未来若另立独立 run 需求，以下基础设计必须直接复用，不得复制成第二套实现：

- Graph facade、CompiledGraph、GraphExecutor、session、scheduler 和 routing owner；
- GraphRunState、GraphRunCommand、pure reduce_graph_run()、GraphTransition 和 exact
  successor；
- ParentGraphActivation、child_graph_run_id()、ScopeRunCoordinate 及其 validator；
- MissingChild、ActiveChild、CompletedChild、AbortedChild 与现有 parent settlement；
- durable-first、revision/token fence、session close 和现有 error taxonomy。

无论未来是否引入持久化，以下约束都不变：

- 不把 concrete frame、publication value、continuation snapshot 或 Store handle 写入
  GraphRunState；
- 不创建第二 scheduler、第二 routing interpreter、第二 public runner 或 global
  mutable registry；
- 不使用 Any、裸字典、反射、隐式 fallback、兼容 alias 或 generic-erasing wrapper；
- 不把 callback 返回成功误写成跨 Store 原子事务；
- 不承诺任意外部副作用 exactly-once；
- 不以重跑 child 取代丢失 output 的 recovery protocol；
- 不用 legacy/private-source-shape test 固定内部名字、循环或源码布局。

## 5. 未来独立 run 需求的准入前置条件

以下是未来 requirements 的检查表，不是本文新增的 type/port 设计，也不授权任何
phase。每项都必须有唯一 owner、输入/输出 nominal contract、错误和 fault evidence：

| 前置条件 | 必须回答的事实 | 当前状态 |
| --- | --- | --- |
| state load/commit | 按 run_ref 读取哪个 authoritative State；revision/token、definition/version、parent 如何校验 | 未立项 |
| input/recovery material | graph input、publication、resume input、child input 的 owner、codec、版本、大小和安全限制 | 未立项 |
| terminal boundary | completed output、aborted reason、awaiting metadata 的 durable representation、可见性和 retention | 未立项 |
| definition resolver | 新 invocation 如何取得 compiled definition、codec、权限和 port；禁止隐藏 registry 的替代方案 | 未立项 |
| transaction/replay | child terminal、boundary publish、parent settlement、publication install 的原子性或 outbox/replay 事实 | 未立项 |
| idempotency | start、transition、publish、read、settle、ack 的 same/different payload、stale 和 unknown-commit 结果 | 未立项 |
| coordinate | child local root 或完整 path 的唯一选择，以及 diagnostics、grandchild、frame 和 validator 迁移 | 未立项 |
| lease/budget | worker owner、expiry、fence、reclaim、aggregate superstep/task budget 和 crash 返还 | 未立项 |
| cancellation | parent/child lifetime、cancel command、receipt、close/fence 顺序和重放行为 | 未立项 |
| API/security | 是否公开按 ID load；caller 权限、definition selection、结果类型和兼容策略 | 未立项 |
| evidence/gates | exact case-level behavior/fault matrix、changed-file manifest、非复杂度 gates 和批准记录 | 未立项 |

任何一项未闭合时，唯一允许的结论是 NOT READY；不能以“建议默认值”、内存
test double、空 boundary 或未来 phase 名称代替事实。

## 6. phase 与授权规则

本版不开放原文 Phase 0–4。避免把“计划”误读为批准，未来只允许按以下顺序另立
change unit：

1. requirements owner 先登记独立 requirement ID、source precedence、owner 和
   approval 状态；
2. 设计 owner 提交一份不重复现有类型/owner 的 target，包含 exact input/output
   contract、删除闭包、producer/consumer call graph 和 per-change manifest；
3. 独立 review 固定 target SHA、case-level evidence、fault windows 和适用
   GSP-P01–GSP-P08；complexity gate 仍按用户范围单独排除；
4. requirements owner 和用户明确批准后，才可讨论 production/test implementation；
5. 每个实际 implementation unit 必须只修改其 manifest 内文件，且不得引入 legacy
   test、第二 owner 或双 authoritative path。

在上述条件满足前：

~~~text
independent child recovery       NOT AUTHORIZED
new state/material/boundary port NOT AUTHORIZED
public child Graph.run()         NOT AUTHORIZED
production / State / Store       NO CHANGE
tests                            NO CHANGE
~~~

## 7. 测试与门禁边界

本版不新增测试。未来若 requirements 获批，测试只能证明 public behavior、typed
protocol、异常顺序、mutation-free 和 crash/fault semantics；不能测试 private class
名字、helper 数量、AST、源码布局或 legacy symbol absence。

complexity gate、health/baseline/ratchet 属于用户明确排除的 automated complexity
范围，本版不运行、不修改、不新增 waiver。Ruff、format、Pyright、现有 behavior
tests、coverage、build/package 和适用 pre-commit 仍须如实记录；make check 不能在
排除 complexity 时被写成“完整通过”。

## 8. 当前快照验证

这些结果只说明修订前后当前工作树的基线，不是未来独立 run 的实现证据：

| 检查 | 结果 |
| --- | --- |
| python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py | PASS：841 passed in 99.10s |
| python -B -m ruff check src tests | PASS |
| python -B -m ruff format --check src tests | PASS：152 files already formatted |
| pyright | PASS：0 errors, 0 warnings, 0 informations |
| python -B -m build --no-isolation | PASS |
| python -B -m twine check dist/* | PASS |
| env COVERAGE_FILE=/tmp/mote-independent-run-context-final.coverage python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py --cov=mote_kernel --cov-report=term-missing | 841 passed；99.94%，既有 dirty family_driver.py:515、result.py:164 未达到 fail-under=100 |
| docs link/EOF/CRLF/trailing-whitespace scan | PASS |
| git diff --check / git diff --cached --check | PASS（tracked workspace；untracked docs 由上一行覆盖） |
| docs scoped pre-commit | PASS；complexity hook 无适用文件 |
| make check | 未运行：聚合目标包含本版排除的 complexity gate |

覆盖率缺口来自评审开始前已有的用户修改，本版没有通过新增测试或修改 production
掩盖它；目标文档修订不能被误归因于该缺口。

## 9. 最终状态与本版 manifest

~~~text
implementation target                  = WITHDRAWN / RESEARCH-ONLY / NOT READY
current Graph/State/reducer contract   = KEEP
future independent recovery            = SEPARATE REQUIREMENTS / NOT AUTHORIZED
new production type/port/field         = 0
second execution/storage path          = 0
single truth / owner boundary          = PASS FOR THIS REVISED DOC
zero new debt                          = PASS FOR THIS REVISED DOC
infrastructure reuse                   = HARD KEEP
complexity gate                        = USER-EXCLUDED / NOT RUN
non-complexity baseline                = PARTIAL (coverage baseline not green)
production / State / Store / protocol  = NO CHANGE / NO AUTHORIZATION
tests / legacy tests                   = NO CHANGE / UNCHANGED
~~~

本次 implementation-doc writeback 的唯一 manifest：

~~~text
mote-kernel/docs/graph-independent-run-context-implementation.zh-CN.md
~~~

本版不再是可直接执行的 production implementation baseline。只有未来独立 requirements
和 review 明确批准后，才可以重新创建一个新的 implementation target；任何新 SHA
不继承本版或上一轮的“建议类型”和 phase 文字。
