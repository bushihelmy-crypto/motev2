# Observe 实现验收记录（2026-09-06）

## 1. 验收结论

本次验收对象仅为 `src/mote_kernel/observe` 及 `tests/observe`。

**Observe 包内实现已经按当前职责边界完成。** 它是可嵌入父 Graph 的 nested Graph，包含两个
业务状态节点和一个共享 Hook；负责取得 observation、将 observation 写入对应 capability、返回
当前观察类型及结算证据，不负责 ReAct 的顶层路由和结束判断。

复审指出的 Observe 包内问题已经闭合：两个业务节点已迁移到 Graph typed-node contract，
共享 Hook 的 concrete binding 与有界 rewrite 规则在 assembly/admission 处固定，Hook P3 结果则
统一通过 Graph 的 output descriptor 绑定。当前精确工作树的 Observe 范围全量测试、分支覆盖率、
lint、format 和严格类型检查均已通过；仓库级门禁中的非 Observe 问题只记录，不在本次越界修复。

本结论只验收 Observe 自己的契约和实现，不代替以下系统集成验收：

- ReAct 对 `ObserveResult` 的顶层 Think/Act/Wait/End 路由；
- ReAct 在仍有后台任务时废弃 END 候选、等待新消息并重新 Observe；
- Port provider 的持久化、幂等、事务和故障对账实现；
- 父 Graph durable commit 与 delivery ACK 的编排。

## 2. 最终结构

Observe 的直接 Graph 节点固定为三个，其中业务状态节点恰好两个：

| 节点 | 类型 | 职责 |
| --- | --- | --- |
| `get_observation` | 业务状态节点 | 从 Queue Port 取得完整 FIFO window，并取得同 observation revision 的后台任务快照 |
| `write_observation` | 业务状态节点 | 通过 Config/Context Port 写入本次 observation，生成 receipt 和 `ObserveResult` |
| `hook` | 一个共享 `HookNode` | 分别在两个业务节点之后执行；不是第二套 Observe 业务状态 |

成功路径固定为：

```text
START
  -> get_observation
  -> hook                    # node_id = get_observation
  -> write_observation
  -> hook                    # node_id = write_observation
  -> Observe Graph.END
```

没有增加 receive、validate、project、route 或 wait 业务节点。它们要么是上述两个状态节点内部的
确定性步骤，要么属于父 ReAct。只有需要独立持久化、恢复、重试、取消或审计的业务边界才应提升为
Graph 节点。

## 3. 已改内容

### 3.1 唯一公共入口

`mote_kernel.observe` 包级只导出 `ObserveNode`：

```python
__all__ = ["ObserveNode"]
```

内部 identity、DTO、admission、Port 和业务节点没有被并列导出为第二套公共 facade。这样仍由
`execution.Graph` 作为唯一图组合和执行入口，Observe 只提供一个领域 nested Graph。

### 3.2 四类 observation 与观察策略

实现了四个 closed nominal observation family：

- `ConfigObservation`
- `ToolObservation`
- `UserObservation`
- `AssistantObservation`

Queue Port 一次返回一个完整、连续的 FIFO window，Observe 不按类型截断。最终规则如下：

| 完整 window | 写入行为 | 返回的 `ObservationKind` |
| --- | --- | --- |
| 只有一个或多个 Config | Config batch 按 FIFO 交给 Config Port，最后一个 Config 是 effective value | `CONFIG` |
| Config + Tool | Config 正常处理；全部 Tool 一次按 FIFO 加入 Context | `TOOL` |
| Config + User | Config 正常处理；全部 User 一次按 FIFO 加入 Context | `USER` |
| Config + Assistant | Config 正常处理；全部 Assistant 一次按 FIFO 加入 Context | `ASSISTANT` |
| 同类多个 Tool/User/Assistant | 整批按 FIFO 一起加入 Context | 对应的单个 enum |
| 同时出现两种或以上非 Config family | 返回 typed conflict/failure；不写 Config/Context，不 ACK | 无成功结果 |

Config 覆盖只发生在 Config 自己的语义内，不删除、不降级、不跳过 Tool/User/Assistant。只要存在一个
合法的非 Config family，parent-facing enum 就返回该 family；只有 Config 时才返回 `CONFIG`。

`ObserveResult` 不包含 Think/Act 节点名和父图跳转计划。它只携带：

- `current_state: ObservationKind`
- 本批 delivery identity 与 cursor range
- 同边界后台任务快照
- 完整 settlement/ACK receipt

Graph 层只发布共享 Hook 第二次激活的 `HookResult`；其
`WriteObservationStageValue.result` 是上述唯一领域结果。该 P3 publication descriptor 由 Graph
统一绑定，Observe 不另设平行输出路径。

### 3.3 FIFO、identity 与不可变证据

新增并收紧了以下 typed identity/value：

- stream、cursor、cursor range、delivery id；
- observation boundary 与 observation revision；
- blocking task 的 task id、incarnation、completion policy；
- durable wait coordinate 与 wait registration；
- settlement identity 与 ACK reference。

完整 batch 以 `deliveries` 作为唯一存储表示；Config/Tool/User/Assistant 子 batch 都从它按需派生，
没有保存四份可能漂移的镜像 projection。同一 family 保持原 FIFO 顺序，完整 window 必须 cursor 连续、
stream/revision 一致且 delivery id 不重复。

后台任务快照会按稳定 key canonicalize，并拒绝重复 task incarnation，使同一集合只有一种稳定表示。

### 3.4 窄 Port 与 owner 边界

Observe 只接收以下六个 capability：

| Port | Observe 使用的能力 | 事实 owner |
| --- | --- | --- |
| `ObservationQueuePort` | `read_after`、`register_wait` | Queue provider |
| `BackgroundTaskPort` | 按 observation boundary 取得 snapshot | Task provider |
| `ConfigObservationPort` | 应用完整 Config batch 并返回 receipt | Config provider |
| `ContextObservationPort` | 追加一个完整非 Config batch 并返回 receipt | Context provider |
| `ObservationAckPort` | 确认已提交结果覆盖的 deliveries | ACK/queue provider |
| `ObservationResumePort` | 编解码 durable Graph resume input | Resume provider |

消息队列、Context 内容、Config 状态和后台任务注册表均不在 Role 或 Observe 中保存。Role 只负责把
这些 capability 装配给 Observe。Observe 没有创建 provider 实现、第二个 mailbox、内存队列、轮询
task 或隐藏可变状态。

所有 required Port、Hook、admission 和 resume codec 都在 Graph assembly 前做 fail-closed 基础校验；
异步返回值进入节点后还会由 Observe admission 按 exact nominal DTO 重新校验。

### 3.5 Admission 与泛型贯穿

`ObservePayloadAdmission` 现在必须显式绑定：

- concrete Hook state class；
- concrete Hook command class；
- Config/Tool/User/Assistant 各自的 exact concrete payload class。

不存在默认未绑定模式。Queue、Hook、settlement、result 和 ACK 边界都复用同一 admission，并对从
provider/deserializer 回来的值重跑构造不变量。实现没有使用 `Any`、裸字典、字符串 discriminator
或反射来猜测类型。

exact nominal class 约束不等于自动证明 payload 对象的深不可变性；字段及其内部对象是否不可变，
由各 concrete payload owner 在 composition root 绑定时保证。Observe 不用运行时反射猜测任意对象的
深不可变性，也不以 `object`、裸容器或宽 union 规避 nominal binding。

两个业务节点已经直接使用 Graph typed-node contract：

- `GetObservationNode` 的 operation 是
  `ObserveRequest[HookStateT] -> HookRequest[ObserveHookEnvelope, HookStateT] | Graph.Outcome`；
- `WriteObservationNode` 的 operation 是
  `HookResult[ObserveHookEnvelope, HookCommandT] -> HookRequest[ObserveHookEnvelope, HookStateT]`；
- assembly 使用 `Graph.bind`、`input_type`、`materialize`、`output_type` 绑定输入和唯一输出；
- 共享 Hook 的 P3 `result` publication 由 Graph 的 `output_ref("hook", "result")` 统一解析，
  Observe 不调用 Hook 私有 P3/result API，也不自行制造第二份 result descriptor。

因此 `Graph.Values` 只留在 Graph input/resume 的统一容器边界，不再是两个 Observe 业务 operation
自行拆装的 legacy mapping API；request、Hook request/result 和 concrete Hook 泛型可以沿 typed
descriptor 贯穿。

### 3.6 一个共享 Hook 与路由身份

两个业务节点都进入同一个注入的 `HookNode`。Observe 在第一次 `Graph.add_node()` 前验证 Hook 是
exact `HookNode`，slot 的 definition id、version、node id=`hook`、stage=`AFTER_NODE` 与 Observe
definition 一致，并检查 Hook admission 的 value/state/command concrete class 与 Observe admission
完全相同，`transition_admission` 还是同一个 Observe admission 实例。错误 binding 在 assembly
fail-closed，不留下半成品 Graph。

业务节点构造 `HookRequest` 时写入自己的 `GraphNodeId`：

- `get_observation` 写入 `get_observation`；
- `write_observation` 写入 `write_observation`。

Hook 的 P1/P2/P3 脚本只能返回 `HookStageResult.value` 与 commands；最终 `HookResult.node_id` 由
`HookNode` 使用原始 request 的 `node_id` 构造，脚本没有修改该身份的入口。Observe 用这个不可由
脚本替换的前驱身份选择自己已经声明好的两条内部边，因此 Hook 不会绕过 Graph 流程。

`HookStageResult.value` 的改写能力被保留，但不是无边界替换。每次 Hook activation 都由同一个
`ObservePayloadAdmission.admit_transition` 固定以下规则：payload 可以改写，并作为下游业务事实；
business stage 不可改变；只读 Hook state 不可改变；commands 必须是绑定的 exact concrete class。
`node_id` 不在脚本可写的 `HookStageResult` 中，继续由 `HookNode` 从原 request 原样构造，因此也不可
被脚本替换。这样既保留正式 rewrite 语义，也不会把 stage/state/父图路由权交给 Hook。

### 3.7 空队列、interrupt 与恢复

Queue 返回 `Empty` 时，`get_observation`：

1. 校验 empty boundary 没有推进 cursor；
2. 要求 provider 原子 recheck/register wait；
3. 校验 registration 与 wait coordinate 一致；
4. 使用可持久化的 `ObservationWait` bytes 返回 `Graph.interrupt`。

resume helper 只接受能唯一指向 `get_observation` 的 interrupt id，严格解码 wait payload，并强制从
wait 的 `after_cursor` 重新读取。wake payload 不是业务 observation，恢复后不能把它直接写入 Context。
codec id/version 在 assembly 时只读取并验证一次，再把捕获值交给 Graph，消除了“验证 A、安装 B”的
metadata 双读窗口。

### 3.8 settlement、result 与 ACK

`write_observation` 对 Config 和 Context receipt 做了以下闭合检查：

- receipt 必须覆盖该 family 的全部 delivery id；
- read boundary 必须与取得 observation 的 frame 一致；
- Config/Context 同时存在时，二者必须得到同一个 settlement boundary；
- revision 前进时，每个相应 receipt 都必须带匹配新 revision 的 task snapshot；
- 多个 snapshot 不得互相冲突；
- `ObserveResult.current_state` 必须能从 settlement receipt 唯一推导；
- delivery、cursor、完整 task snapshot（包括 `blocking_tasks`）和 ACK reference 必须与完整 receipt
  一致。

ACK 保持显式的提交后动作：

```python
await observe.acknowledge(observe_result)
```

`acknowledge()` 重新接收 `ObserveResult`，验证 result 和 provider 返回的 `DeliveryAck.reference` 精确
匹配。它没有继续要求 Observe 自己的 `Graph.CompletedResult`，原因是 Observe 是 nested Graph：正式
组合中 commit owner 获得的是父 Graph completion，而不是一个可单独交给 ACK 的 Observe completion。
将 ACK 绑定到 Observe definition 的 `CompletedResult` 会让正确的父图提交后 ACK 无法调用，因此该
尝试及其错误测试已撤回。

ACK 的调用时序仍由包含 Observe 的父级 durable commit owner 保证；Observe 不为此建立一份本地
run/commit 状态表。

## 4. 针对代码评审项的处理结果

`docs/observe-code-review-2026-09-06.zh-CN.md` 与
`docs/observe-acceptance-rereview-2026-09-06.zh-CN.md` 是各自当时工作树的评审快照。本次没有
篡改历史评审，而是在这里记录最终处理结论。
| 原评审项 | 处理 | 理由 |
| --- | --- | --- |
| P0-1：Hook rewrite provenance | **按有界 rewrite 规则关闭** | payload 可改写；stage、只读 state 和 `node_id` 不可改。每次 transition 复用 Observe admission，完整 result snapshot 也与 settlement evidence 一致；不采用会破坏正式 Hook 语义的全量 pass-through |
| P0-2：Port effect 与 Graph commit 的跨系统闭包 | **未在 Observe 内实现事务协调器** | provider 幂等/事务及 Graph pending-reconcile 属于 Port/commit owner；Observe 只产生并校验 typed receipt，私建 runner/store 会形成第二状态 owner |
| P1-1：concrete Hook binding | **已在 assembly fail-closed** | 比较 Hook admission 的 exact value/state/command class，并要求同一个 Observe transition admission；在第一次 `Graph.add_node()` 前完成 |
| P1-2：Port async/arity/return | **Protocol + assembly callable check + 返回值 admission** | strict typing 证明签名，运行时验证 capability 形状及 exact result；不使用函数签名反射，也不把 provider invocation engine 复制进 Observe |
| P1-3：ACK commit evidence | **明确移交父 commit owner；保留 result/reference admission** | nested Observe 无法以自己的 completion 代表父图 durable commit；pending ACK 必须进入唯一 commit owner，而不是 Observe 私有状态 |
| P1-4：resume state/codec provenance | **Observe 只校验自己的 interrupt/wait 边界** | durable continuation 和 Hook state 来源属于父 Graph/recovery owner；Observe 不创建第二份恢复状态 |
| P1-5：payload 可为宽/可变对象 | **收紧并修正文档保证** | 四类 payload 必须是 admission 绑定的 exact concrete `ObservationPayload` subclass；其深不可变性由 concrete payload owner 保证，generic Observe 不用反射伪造证明 |
| 旧 P1-6：successor snapshot 可由另一 receipt 补齐 | **已修复** | 每个推进 revision 的 receipt 独立要求 snapshot；组合时还要求 boundary/snapshot 一致 |
| 复审 P1-6：业务节点仍使用 legacy `Graph.Values` | **已修复** | 两个 operation 已迁移到 `Graph.bind` + typed materializer/output contract，删除节点内部 mapping 拆装路径 |
| P1-7：未使用的 `check_fence` | **已删除** | Observe 不执行 ReAct 的最终 commit fence；保留一个从不消费的 Port 方法会制造虚假保证 |
| P2-1：batch 镜像 projection | **已修复** | 只存 authoritative `deliveries`，family batch 全部按需派生 |
| P2-2：task tuple 非 canonical | **已修复** | snapshot 构造时稳定排序并拒绝重复 incarnation |
| P2-3：codec metadata 双读 | **已修复** | assembly 返回一次捕获的 resume binding，安装时不重新读取 provider 属性 |
| P2-4：重复规则 | **按 owner 收口，保留必要 closed-family 分支** | 不为减少表面重复而引入宽 helper、反射或 `utils/common/shared` 包 |

## 5. 明确没有改的内容及原因

### 5.1 没有修改 Graph、Hook、ReAct、Think 或 Act

- Graph 是唯一 execution/state/commit owner，Observe 不创建或修改执行器、session、reducer、
  `GraphRunState` 或编译器语义。
- Hook 是独立 nested Graph owner。Observe 只注入一个真实 Hook 并消费其公开契约，不修改
  `HookNode` 的节点名或 P1/P2/P3 pipeline；P3 publication 通过 Graph 统一 `output_ref` 绑定。
- ReAct 是顶层图 owner。Observe 不认识 Think/Act 节点名，也不生成父图 jump plan。
- Think/Act 的结果由外部 adapter 映射为四类 observation 之一；Observe 不读取它们的私有 frame。

### 5.2 没有在 Observe 内实现“后台任务存在时阻塞结束”

Observe 负责把与 observation boundary 同 revision 的 `BackgroundTaskSnapshot` 放入
`ObserveResult`。是否正在尝试 END、是否因 blocking task 改为等待、消息唤醒后是否废弃旧 END
候选，只有父 ReAct 知道。因此该 gate 必须由 ReAct 实现。

把它放进 Observe 会产生两个问题：Observe 必须认识父图 END/route，且会把“观察事实”和“顶层策略”
混成一个 owner。当前 Observe 只在自己的 Queue 为空时进入 durable interrupt，不替父 ReAct 决定
整个运行何时结束。

### 5.3 没有保留 `BackgroundTaskPort.check_fence` 或 Observe `RevisionFence`

这两个值在 Observe 内没有合法消费点。最终 END 前的 CAS/fence 属于父 Graph 的 commit candidate；
在 Observe 中仅声明但不调用会让接口看似安全、实际没有保证。因此删除未使用接口，仅返回真实取得的
snapshot/revision evidence。

### 5.4 没有实现 Queue、Context、Config、Task 和 ACK provider

这些均通过窄 Port 注入。Observe 不能持有消息池、Context 或后台任务注册表，也不能假设所有 provider
位于同一数据库。跨 Port 原子性、幂等键、unknown outcome reconcile 和 durable pending ACK 必须由
真实 provider/统一 commit assembly 定义。

### 5.5 没有禁止 Hook 改写 value

曾尝试在 Observe 中回读原 publication 并要求 Hook 返回 envelope 与原值相等，该方案已完整撤回。
它既违反 `HookStageResult` 的改写语义，也会要求 Graph 为这条额外读取路径提供不必要的 producer
激活证明。最终锁定的是有界 rewrite：payload 可改，stage、只读 state、`node_id` 不可改，并始终
执行 exact typed/stage/DTO admission。

### 5.6 没有让 ACK 接受 Observe 自己的 `CompletedResult`

该方案也已撤回。nested Graph 的外层提交者持有父 Graph completion，强制要求 definition id 等于
Observe 会拒绝真实集成路径。Observe 接受已经结算的 `ObserveResult`；父 commit owner 负责保证
“先 durable commit，后 ACK”。

### 5.7 没有恢复 `src/mote_kernel/operations`

该目录是用户主动删除的内容，与 Observe 实现无关，本次没有恢复、替代或新增兼容入口。

## 6. 测试资产

`tests/observe` 已按职责拆分为：

| 文件 | 覆盖范围 |
| --- | --- |
| `test_identity.py` | cursor/delivery/task/wait/ACK identity 与 durable codec |
| `test_contract.py` | 四类 observation、FIFO、conflict、receipt、snapshot、result 不变量 |
| `test_admission.py` | exact type、forged DTO、payload binding、数量/长度边界 |
| `test_port.py` | 六个 required Port、缺失/非 callable capability、codec metadata |
| `test_nodes.py` | 两业务节点、共享 Hook concrete/transition binding、写入策略、interrupt/resume、ACK、取消 |
| `test_graph.py` | 三节点 topology、typed materializer、bounded Hook rewrite、nested parent、terminal route、并发隔离及失败停止 |

关键确定性场景包括：

- 四个 observation family 及 Config-only/Config+non-Config；
- 同类多消息完整 FIFO 写入与 interleaved Config projection；
- 两种非 Config family 冲突时零 settlement/零 ACK；
- queue/task/receipt boundary 与 revision 不一致时 fail-closed；
- successor snapshot 缺失或冲突；
- 空队列 wait registration、interrupt、resume 重新读取；
- malformed/oversized/non-canonical durable wait payload；
- 两次共享 Hook activation、最终 route 及父 nested Graph 消费；
- 合法 payload rewrite 贯穿完整 Graph，stage/state/command rewrite 越界及伪造 transition 被拒绝；
- 错误 Hook value/state/command/transition binding 在 assembly fail-closed；
- typed materializer 缺少输入时在调用任何 capability 前失败；
- 同 revision 但 settlement task facts 不同的结果被拒绝；
- 并发 run 的 frame/hook state 隔离；
- Port/Hook 失败和 cancellation 不继续进入后续阶段；
- ACK 显式调用及 provider 返回错误 reference。

为错误 `CompletedResult` ACK 设计增加的两个测试已删除，因为它们锁定的是无法用于 nested Graph 的错误
所有权模型，而不是应保留的业务不变量。

## 7. 门禁记录

### 7.1 当前精确工作树结果

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -B -m ruff check src/mote_kernel/observe tests/observe` | 通过 | Observe 源码与测试 lint 通过 |
| `python -B -m ruff format --check src/mote_kernel/observe tests/observe` | 通过 | 13 files already formatted |
| Observe pytest + branch coverage | 通过 | 244 passed；1301 statements、482 branches，均 100.00% |
| `pyright src/mote_kernel/observe tests/observe` | 通过 | 0 errors、0 warnings、0 informations |
| `make typecheck` | 最终复跑失败（非 Observe） | 本轮初次运行曾为 0 errors；随后出现并行的 `tests/hooks/test_hooks.py` 改动，最终复跑报其中 2 个未使用导入，本轮不修改该文件 |
| `git diff --check`（Observe 源码、测试及两份文档） | 通过 | 本轮范围无 whitespace error |
| `make check` | 失败（非 Observe） | 在上述并行改动出现前运行：lint、format、typecheck 通过；仓库 `complexity-ratchet` 失败后停止。失败包含 Graph/Think/Act 等全仓指标，本轮不调整基线或其他包 |
| monorepo `pre-commit run --all-files` | 失败（非 Observe） | 除 `kernel-complexity` 外全部通过；该 Hook 复现同一个全仓 complexity-ratchet 失败 |

本次没有为了让门禁表面变绿而修改 execution、Graph、Hook、ReAct 或无关 CI。

### 7.2 最终交付命令

```bash
python -B -m pytest tests/observe -q \
  --cov=mote_kernel.observe \
  --cov-report=term-missing
python -B -m ruff check src/mote_kernel/observe tests/observe
python -B -m ruff format --check src/mote_kernel/observe tests/observe
make typecheck
git diff --check -- src/mote_kernel/observe tests/observe \
  docs/observe-acceptance-2026-09-06.zh-CN.md
make check
cd .. && pre-commit run --all-files
```

通过标准仍是 Observe branch coverage 100%、Pyright 0 errors，并且没有由 Observe 引入的仓库级
lint、测试、构建或 pre-commit 回归。

## 8. 最终边界判定

当前不再有需要通过增加 Observe 节点、状态、route、runner、store 或公共 API 解决的已知包内事项。
尚未闭合的系统集成内容均依赖其真实 owner：父 ReAct 路由与结束 gate、真实 Port provider 事务性、
Graph/commit 的 durable continuation 与父 commit 后 ACK 编排。

若后续为了这些集成项必须修改 Observe 之外的包，应先由对应 owner 确认设计，不在 Observe 内用
兼容层、影子状态或重复执行路径绕过。
