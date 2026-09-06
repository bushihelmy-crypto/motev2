# Observe 实现验收记录（2026-09-06）

## 1. 验收结论

本次验收对象仅为 `src/mote_kernel/observe` 及 `tests/observe`。

**Observe 包内实现已经按当前职责边界完成。** 它是可嵌入父 Graph 的 nested Graph，包含两个
业务状态节点和一个共享 Hook；负责取得 observation、将 observation 写入对应 capability、返回
当前观察类型及结算证据，不负责 ReAct 的顶层路由和结束判断。

**仓库级最终门禁尚不能签为全绿。** 当前 `execution/node_adapter.py` 导入了
`execution/graph/values.py` 中不存在的 `_ExactValueAdmission` 和 `_admit_exact`，导致 Observe
测试在 collection 阶段即被 execution 导入链阻断。该错误不在 Observe 包内，本次没有越界修复。
Graph 侧同步完成后，需要重跑第 7 节列出的门禁，才能补上当前精确工作树的最终验证记录。

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
- Config/Tool/User/Assistant 各自的 concrete immutable payload class。

不存在默认未绑定模式。Queue、Hook、settlement、result 和 ACK 边界都复用同一 admission，并对从
provider/deserializer 回来的值重跑构造不变量。实现没有使用 `Any`、裸字典、字符串 discriminator
或反射来猜测类型。

节点输出及 Graph assembly 使用精确泛型：

- `GetObservationNode` 返回 `Graph.Values[HookRequest[ObserveHookEnvelope, HookStateT]]`；
- `WriteObservationNode` 返回同一精确类型；
- `admit_request`、`admit_hook_request` 保留 concrete Hook state 泛型；
- Graph input/output descriptor 保留 request 与 Hook request 的具体类型。

这使用了 Graph 最新的 `Values` 泛型贯穿能力，没有在 Observe 中增加兼容 cast facade 或平行 value
容器。

### 3.6 一个共享 Hook 与路由身份

两个业务节点都进入同一个注入的 `HookNode`。Observe 验证 Hook 是 exact `HookNode`，且 slot 的
definition id、version、node id=`hook`、stage=`AFTER_NODE` 与 Observe definition 一致。

业务节点构造 `HookRequest` 时写入自己的 `GraphNodeId`：

- `get_observation` 写入 `get_observation`；
- `write_observation` 写入 `write_observation`。

Hook 的 P1/P2/P3 脚本只能返回 `HookStageResult.value` 与 commands；最终 `HookResult.node_id` 由
`HookNode` 使用原始 request 的 `node_id` 构造，脚本没有修改该身份的入口。Observe 用这个不可由
脚本替换的前驱身份选择自己已经声明好的两条内部边，因此 Hook 不会绕过 Graph 流程。

`HookStageResult.value` 的改写能力被保留。Observe 不再要求 Hook 返回的 envelope/value 与进入 Hook
前逐字段相等；只对返回值的 exact class、closed stage/payload 组合、业务 node identity 以及内部
DTO 不变量做 admission。原因是 Hook 的正式语义本来就允许 P1/P2/P3 改写 value，强制等值会把合法
Hook 退化成只允许 pass-through。

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
- delivery、cursor、snapshot 和 ACK reference 必须与完整 receipt 一致。

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

`docs/observe-code-review-2026-09-06.zh-CN.md` 是当时工作树的评审快照。本次没有篡改历史评审，
而是在这里记录最终处理结论。

| 原评审项 | 处理 | 理由 |
| --- | --- | --- |
| P0-1：Hook 可替换 frame/result | **撤回该缺陷判定** | `HookStageResult.value` 可改写是正式 Hook 语义；保护的是 Hook 外部的 `node_id` 与 typed/stage 边界，不是强制 value 等值 |
| P0-2：Port effect 与 Graph commit 的跨系统闭包 | **未在 Observe 内实现事务协调器** | provider 幂等/事务及 Graph pending-reconcile 属于 Port/commit owner；Observe 只产生并校验 typed receipt，私建 runner/store 会形成第二状态 owner |
| P1-1：concrete Hook binding | **收紧 Observe admission 与静态泛型边界** | concrete payload/state/command 必须绑定；Hook 自身 admission 负责 Hook 内边界。Observe 不读取 Hook 私有字段或反射泛型 |
| P1-2：Port async/arity/return | **Protocol + assembly callable check + 返回值 admission** | strict typing 证明签名，运行时验证 capability 形状及 exact result；不使用函数签名反射，也不把 provider invocation engine 复制进 Observe |
| P1-3：ACK commit evidence | **明确移交父 commit owner；保留 result/reference admission** | nested Observe 无法以自己的 completion 代表父图 durable commit；pending ACK 必须进入唯一 commit owner，而不是 Observe 私有状态 |
| P1-4：resume state/codec provenance | **Observe 只校验自己的 interrupt/wait 边界** | durable continuation 和 Hook state 来源属于父 Graph/recovery owner；Observe 不创建第二份恢复状态 |
| P1-5：payload 可为宽/可变对象 | **已修复** | 四类 payload 必须是 admission 绑定的 exact concrete `ObservationPayload` subclass |
| P1-6：successor snapshot 可由另一 receipt 补齐 | **已修复** | 每个推进 revision 的 receipt 独立要求 snapshot；组合时还要求 boundary/snapshot 一致 |
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
  `HookNode` 的节点名或 P1/P2/P3 pipeline。
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
激活证明。最终只锁定不可由 Hook 脚本修改的 `node_id` 路由身份和 typed contract。

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
| `test_nodes.py` | 两业务节点、共享 Hook、写入策略、interrupt/resume、ACK、取消 |
| `test_graph.py` | 三节点 topology、nested parent、terminal route、并发隔离及失败停止 |

关键确定性场景包括：

- 四个 observation family 及 Config-only/Config+non-Config；
- 同类多消息完整 FIFO 写入与 interleaved Config projection；
- 两种非 Config family 冲突时零 settlement/零 ACK；
- queue/task/receipt boundary 与 revision 不一致时 fail-closed；
- successor snapshot 缺失或冲突；
- 空队列 wait registration、interrupt、resume 重新读取；
- malformed/oversized/non-canonical durable wait payload；
- 两次共享 Hook activation、最终 route 及父 nested Graph 消费；
- 并发 run 的 frame/hook state 隔离；
- Port/Hook 失败和 cancellation 不继续进入后续阶段；
- ACK 显式调用及 provider 返回错误 reference。

为错误 `CompletedResult` ACK 设计增加的两个测试已删除，因为它们锁定的是无法用于 nested Graph 的错误
所有权模型，而不是应保留的业务不变量。

## 7. 门禁记录与待补动作

### 7.1 已有历史完整结果

在当前 execution 导入链失配出现前，Observe 全量测试曾达到：

- `237 passed`
- Observe branch coverage `100%`
- Pyright `0 errors`

该记录证明当时版本的主体实现，但不能替代当前 ACK API 回退后的精确工作树复验。

### 7.2 当前精确工作树结果

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -B -m ruff check src/mote_kernel/observe tests/observe` | 通过 | Observe 源码与测试 lint 通过 |
| `python -B -m ruff format --check src/mote_kernel/observe tests/observe` | 通过 | 13 files already formatted |
| Observe pytest + branch coverage | 阻塞 | collection 导入 `execution/node_adapter.py` 时找不到 `_ExactValueAdmission` / `_admit_exact` |
| `make typecheck` | 阻塞 | 当前报告 8 个非 Observe 错误，来自 `execution/node_adapter.py` 与 `hooks/contract.py` |
| `make check` | 未继续 | pytest/typecheck 的已知外部阻断尚未消除，继续运行不能形成有效验收证据 |
| monorepo pre-commit | 未继续 | 同上，且当前 worktree 有大量非 Observe 并行改动，不能归因到 Observe |

本次没有为了让门禁表面变绿而修改 execution、Graph、Hook、ReAct 或无关 CI。

### 7.3 Graph 侧同步完成后必须执行

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
尚未闭合的内容均依赖其真实 owner：Graph/Hook 当前工作树同步、父 ReAct 路由与结束 gate、真实 Port
provider 事务性以及父 commit 后 ACK 编排。

若后续为了这些集成项必须修改 Observe 之外的包，应先由对应 owner 确认设计，不在 Observe 内用
兼容层、影子状态或重复执行路径绕过。
