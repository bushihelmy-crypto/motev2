# Mote 平台架构设计

> 状态：目标架构草案。本文固定 Mote 的长期职责边界与演进方向，不代表所有组件已经实现，也不推导尚未设计的公共装配 API。

## 1. 核心结论

Mote 不是一个“模型加工具”的 Agent 框架，而是一套面向 Agent 与 Agent Swarm 的计算架构：

> Product 声明 Agent，Control 管理身份与分配，Resource 注册、发现并解析 Container/Embodiment 资源，Invocation 通过本地或远程实现完成调用，Kernel 组装并驱动 Agent，Runtime Services 实现领域能力，Persistence 提供持久化与可靠执行机制。

六个职责边界按语义所有权划分，而不是按部署进程划分：

```text
┌─────────────────────────────────────────────────────────────────┐
│ Product · TypeScript                                            │
│ Agent/Role 配置 · 用户策略 · UI · API · 结果呈现                 │
└──────────────────────────────┬──────────────────────────────────┘
                               │ 声明、输入、展示
┌──────────────────────────────▼──────────────────────────────────┐
│ Control · Go                                                    │
│ Agent 身份 · Spawn · lineage · authority · 通信 · 生命周期 · 放置 │
└──────────────────────────────┬──────────────────────────────────┘
                               │ 可信身份与放置结果
┌──────────────────────────────▼──────────────────────────────────┐
│ Resource                                                        │
│ Container 宿主 + Embodiment 本体：注册 · 发现 · 句柄解析         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ Container 注入窄 ctx / Embodiment handle
┌──────────────────────────────▼──────────────────────────────────┐
│ Kernel · Python                                                 │
│ Role 声明式组装 · Agent Flow · Graph/StateMachine · Typed Ports  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ 类型化能力请求与结果
┌──────────────────────────────▼──────────────────────────────────┐
│ Runtime Services                                                │
│ Context · Router · Gateway · Approval · Terminal · EventBus ...  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ 状态、执行、事务、工作空间机制
┌──────────────────────────────▼──────────────────────────────────┐
│ Persistence · deployment-specific                              │
│ CAS · atomic commit · operation receipt · lease/fence · attempt │
│ durable execution · workspace · SQLite/storage adapters         │
└─────────────────────────────────────────────────────────────────┘

                 conformance/ 横跨所有语言边界
```

这不是一条强制的同步调用链。Control、Resource、Kernel 和 Runtime 可以通过进程内调用、RPC、队列或远程 worker 部署；图中表达的是职责与权威方向。所有具体调用基础设施统一归 `mote-infra/invocation`，所有具体存储基础设施统一归 `mote-infra/persistence`。

## 2. 设计目标

Mote 要建立以下长期不变量：

1. 一个 Agent 的流程语义只有一个权威来源：Kernel。
2. 每个事实只有一个权威状态位置，不因服务调用而复制成多份真相。
3. Runtime 服务可以互相调用；调用关系不决定状态所有权。
4. 状态转换与外部副作用分离，崩溃后可以判断“尚未执行、已经执行或结果未知”。
5. 所有 Flow、Workflow、Failover、Think、Act 等语义复用同一 Graph 与 StateMachine 基础设施，不创建私有 runner。
6. Rust 统一实现状态、事务、lease/fence、attempt 和工作空间等机械能力，但不解释 Agent 业务语义。
7. Control 决定 Agent assignment、authority、placement 与 lifecycle；Resource 只解析绑定并提供窄能力。Invocation 调用解析后的宿主或能力，Embodiment 提供物理本体能力；两者共置只是 Control 约束，不实体化为 `robot-edge`。
8. Agent 可以动态 Spawn Agent；Control 提供可信的群体协作机制，但不替模型决定协作语义。
9. 模型可见能力最终可以收敛为熟悉、可组合、可持久执行的命令环境，而不是不断扩张的碎片化工具集合。

## 3. 六个职责边界

### 3.1 Product：声明和呈现 Agent

Product 使用 Kernel 拥有的声明类型定义具体 Agent，而不定义第二套 Agent 循环。它负责：

- Agent、Role、提示词、策略和能力选择；
- 用户、组织或场景级配置；
- CLI、Web、IDE、移动端等交互界面；
- 输入适配、事件投影和结果展示；
- 向用户请求授权并展示可审计信息。

Product 可以选择能力和配置，但不能决定 Think 完成后应进入 Act、恢复哪个节点或何时结束一次 Agent Flow。这些属于 Kernel。

### 3.2 Control：管理 Agent

Control 是 Go 实现的控制面，负责：

- 稳定 `AgentId`、incarnation/epoch 和可信 authority；
- Spawn、注册、父子 lineage 与生命周期；
- Agent 间寻址、mailbox、消息投递和订阅；
- placement、ownership、配额与集群级调度机制；
- 取消、终止和授权等控制请求的可靠传递；
- 对 Agent 树和通信关系提供权威控制面事实。

模型决定为什么 Spawn、委托什么任务以及何时收敛；Control 只保证这个决定被可信、可靠地执行。Agent 身份与 authority 必须由 Control 注入，不能由模型或 Runtime 自报。

### 3.3 Resource：提供宿主与本体资源

`mote-resource` 是资源 provider umbrella，下设并列的
`mote-resource/container` 与 `mote-resource/embodiment`。它只负责注册、发现、
定位、能力描述和句柄解析，不拥有 Agent Flow、Runtime 领域状态或持久化事务。

#### 3.3.1 Container：注册、定位并暴露具体容器句柄

Container 是 Control assignment 与具体运行环境之间的窄边界，负责：

- 注册 local、Docker、Cloudflare 等具体 Container 实现；
- 根据 Control 已决定的 Container binding 定位宿主；
- 向 Invocation 暴露所选宿主的窄类型句柄和必要上下文；
- 提供运行平台要求的 Worker/DO 入口、binding 和 Kernel hosting glue；
- 向 Port resolver 暴露平台能力，例如 Durable Object `ctx.storage`。

Container 不签发 `AgentId`，不拥有 lineage、placement 决策、lifecycle authority 或调用实现，也不解释 Kernel Flow。调用 contract、resolver、本地实现与 RPC 实现统一归 `mote-infra/invocation`。Container 与 Persistence 是两个正交的配置维度：Kernel Port 配置独立选择 `Commit` backend；Cloudflare Container 可以把 `ctx.storage` 作为可选平台能力提供给 resolver，也可以使用远端 Persistence。Container 可以声明 Cloudflare 要求的 `storage: "sqlite"` 部署 binding，但这不等于选中该 backend；SQL、schema、migration 和 transaction 实现始终归 Persistence。

#### 3.3.2 Embodiment：解析物理本体能力

Embodiment 表示物理身体资源，例如移动机器人、机械臂或无人机，负责：

- 注册本体资源及其能力描述；
- 根据 `EmbodimentBinding` 解析稳定句柄；
- 向需要它的 Port/Runtime 注入最小、类型化的能力上下文。

Embodiment 不拥有实时调度、传感器/驱动状态或动作循环；这些属于
`mote-runtime` 的具体 provider。机器人是 Embodiment 的一种实现，不需要另设
`robot` 或 `physical` 资源层。若要求脑和身体共置，由 Control 保存约束，不新增
`robot-edge` 包。

### 3.4 Kernel：组装并驱动 Agent

Kernel 是 Agent 语义中心，负责：

- 提供受控的 Role/Flow 声明形式；
- 根据 Product 配置与可用 Port 声明式组装唯一 Graph；
- 定义 Observe、Think、Act、Failover 等状态机及组合关系；
- 定义 `GraphState`、`DomainState` 和纯 reducer；
- 解释 Runtime 返回的 typed result；
- 决定继续、分支、循环、中断、恢复、跳过、取消或完成；
- 通过窄 typed Port 请求外部能力。

Kernel 不直接操作数据库、journal、CAS、文件系统或远程 worker。它产生合法的状态变化和执行请求，由 Port 配置选择的 Persistence backend 提交或执行。Kernel 可以持有当前不可变内存快照，但持久状态始终权威；只有 durable commit 确认后才能替换内存快照。

Kernel 的装配语义归 Kernel 所有，但本文不推导当前尚未设计的默认公共 composition entry point。

### 3.5 Runtime Services：实现领域能力

Runtime 不是单体进程，也不是 Agent Host。它是一组可以独立部署、扩缩和演进的领域能力服务，例如：

```text
Context Runtime     模型上下文与对话事实
Router Runtime      模型/供应商/成本/质量路由
Gateway Runtime     模型调用、规范化结果与调用 receipt
Approval Runtime    用户或策略授权
Terminal Runtime    持久命令环境与 effect dispatch
EventBus Runtime    领域事件投递与订阅
Browser Runtime     浏览器会话与交互语义（也可由 Terminal 承接）
Device Runtime      Android、桌面或其他设备能力
```

每个 Runtime：

- 拥有自身领域类型与领域不变量；
- 通过 Persistence Port 持久化自己的权威状态；
- 在 durable 结果建立之后才返回成功；
- 使用稳定 `OperationId` 支持查询、重放与幂等；
- 不能直接修改 Kernel 的 `GraphState` 或替 Kernel 决定流程。

Runtime 服务可以互相调用。禁止的是多份权威事实，而不是服务间调用。

### 3.6 Persistence：统一可靠机制

Persistence 按 backend 实现持久化与可靠执行机制。本地实现以 Rust 为主；Cloudflare 的 Python 与 TypeScript Adapter 使用各自运行时提供的 Durable Object SQLite API：

- revisioned aggregate state 与 CAS；
- 原子 commit；
- operation identity、fingerprint 和 durable receipt；
- lease、heartbeat、fencing token 与 stale-owner 拒绝；
- attempt、claim、complete、fail、fence 等可靠执行机制；
- durable queue、定时器和机械重试；
- workspace、文件事务、blob 与授权路径；
- SQLite、PostgreSQL 等存储适配器；
- gRPC/Unix socket 等语言无关边界。

Persistence 只能理解机制类型和 opaque/versioned payload，不能理解 `Think`、`Act`、`Context`、`GraphFrontier`、`BrowserClick` 或“是否应该 Spawn”之类的业务语义。Persistence 是最低层实现，不 import Kernel、Resource、Control 或 Product。Port 配置选择 backend：选中 Cloudflare SQLite 时向 Adapter 提供 `ctx.storage`；选中远端 backend 时则通过 `mote-infra/invocation/rpc` 提供相应 RPC client。Resource 不选择、不构造 Persistence Adapter。

## 4. 调用关系与事实所有权

服务互调本身不会破坏一致性。真正决定一致性的，是边界事实存在哪里、由什么转换协议提交。

### 4.1 允许的协作

```text
Kernel ──▶ Runtime A ──▶ Runtime B

Runtime A ─────────────▶ Persistence Commit
Runtime B ─────────────▶ Persistence Commit
```

Runtime A 可以调用 Runtime B；Kernel 也可以分别调用 A、B。调用拓扑可以根据性能、延迟和封装需要变化，不应成为事实所有权的依据。

### 4.2 同一一致性边界

若 A 与 B 的边界数据必须原子一致，它们必须操作同一权威 aggregate，并通过同一 schema、transition contract 和 `CommitBatch` 提交：

```text
Runtime A ─┐
           ├──▶ Boundary Aggregate ──▶ one atomic commit
Runtime B ─┘
```

这里的“相同持久化位置”不是“恰好连接同一个数据库”或“各自有一张表”。它必须同时意味着：

- 一份权威记录；
- 一个明确的 schema/transition owner；
- 同一个 revision 与 fencing 规则；
- 一个不可撕裂的事务提交边界；
- 本地缓存或 projection 只能重建，不能反向成为真相。

参与写入的服务可以有多个，但合法变化必须由同一 typed transition contract 约束。

### 4.3 跨 aggregate 或跨存储

如果事实不在同一原子提交边界，就不能伪装成 exactly-once。应使用：

```text
stable OperationId
  + durable receipt
  + idempotent replay
  + unknown-outcome reconciliation
```

共享物理数据库不能自动消除领域边界；不同存储也不要求引入分布式事务。只有真正需要不可撕裂的事实才进入同一 aggregate，其余关系通过 receipt 和可恢复 Graph 编排完成。

## 5. 状态模型

Mote 存在三类不同的权威状态，不能混为一体。

### 5.1 AgentState

```text
AgentState
├── GraphState     可恢复的执行位置
└── DomainState    已建立的 Agent 业务事实
```

Kernel 拥有其语义和纯转换，Persistence 拥有其 durable commit 机制。`GraphState` 与对应 `DomainState` 变化必须作为一个 `AgentState` 原子提交。

节点完成时，提交的不是“把 Python 对象记在内存里”，而是：

```text
expected AgentState revision
+ execution fencing precondition
+ GraphState settlement
+ node-owned DomainState facts/result reference
────────────────────────────────────────────
= one durable AgentState commit
```

大体积或服务专属结果可以保存在 Runtime aggregate/artifact 中，AgentState 保存严格 typed 的值或 receipt 引用。这样既能恢复节点，又不需要一个无边界的“任意 Python output store”。

### 5.2 Runtime Domain State

Context、Gateway、Terminal 等 Runtime 各自持有领域状态：

```text
ContextState
GatewayOperation / ModelReceipt
TerminalSession / CommandReceipt
ApprovalDecision
```

Kernel 通过 Port 获取所需的不可变值或 projection，不直接加载其数据库内部模型。Runtime 状态的语义由相应 Runtime 拥有，持久化机制复用 Persistence。

### 5.3 Control State

Control 拥有 Agent identity、lineage、mailbox、authority、placement 和 lifecycle 等控制面事实。它可以复用 Persistence 状态机制，但 Rust Persistence 不解释这些事实的控制面含义。

## 6. 节点边界

Graph 中的 node 不是任意函数切片，而是：

> 需要独立状态、恢复、重试、取消、资源控制或审计的最小业务块。

因此：

- 一个纯粹的本地转换通常不需要单独 node；
- 一个昂贵模型调用通常需要独立 node；
- 一个具有外部副作用的命令需要独立 settlement/receipt 边界；
- 服务内部的短调用可以隐藏在一个 node 内；
- 一旦某个子步骤需要单独恢复或观测，就应提升为独立 node 或独立 Runtime operation。

Failover、ReAct、Think、Act、Tool Parallel、声明式 Workflow 都定义为 Graph + StateMachine，并复用 Kernel 唯一 execution 语义。Failover 可以表现为节点内部装饰器或与 Flow 协作的状态机，但不能创建第二个 runner。

## 7. Think 与 Act 的持久化闭包

### 7.1 Think

```text
Think node
  ├── Context Runtime：读取只读 ModelContext
  ├── Router Runtime：选择模型/供应商策略
  ├── Gateway Runtime：调用模型并持久化 ModelReceipt
  ├── Kernel：解析规范化 ModelResult
  └── AgentState commit：结算 Think node 与其 typed result
```

Kernel 最终解析出例如：

```text
FinalAnswer | TerminalProgram | SpawnIntent | ...
```

如果 Think 产生工具调用，它可以作为 Think node 的可恢复结果保存，但此时不能立即追加为已完成的 Context 对话事实，否则会留下没有 tool result 的孤立 tool call。

### 7.2 Act

```text
Act node
  ├── Approval Runtime：建立授权事实
  ├── Terminal Runtime：执行并持久化 TerminalReceipt
  ├── Kernel：解析 typed ActResult
  ├── Context Runtime：一次写入配对的 tool_call + tool_result
  └── AgentState commit：结算 Act node
```

Context 不是 Graph 执行日志。只有 Act 获得结果后，`tool_call + tool_result` 才作为一个配对事实进入 Context。

典型故障闭包：

```text
Terminal effect 已成功
        │ 响应丢失或 Context 写入失败
        ▼
Act node 尚未结算
        │ 使用相同 OperationId 查询/重放
        ▼
Terminal 返回已有 durable receipt，不重复副作用
        │
        ▼
重试 Context 配对提交
        │
        ▼
结算 Act node
```

Kernel Graph 提供恢复位置，Runtime receipt 证明领域结果，Persistence fencing 排除旧执行者。三者缺一不可。

## 8. Runtime 调用协议

具有状态或副作用的调用应携带统一 envelope，具体 wire schema 归 `conformance/`：

```text
AgentId
AgentIncarnation / ActivationEpoch
NodeActivationId
OperationId
RequestFingerprint
Authority / CapabilityGrant
ExpectedRevision（需要 CAS 时）
Deadline / Cancellation context
Typed Request
```

响应必须是严格 union，例如：

```text
Completed(receipt, typed_result)
Rejected(typed_reason)
InProgress(receipt)
Unknown(reconciliation_handle)
Conflict(existing_fingerprint)
```

同一 `OperationId` 的规则：

- 相同 fingerprint：返回已有结果或继续同一 operation；
- 不同 fingerprint：确定性冲突；
- 响应丢失：允许查询 durable receipt；
- stale fencing token：拒绝写入；
- 外部系统不支持幂等时：进入 `Unknown + reconcile`，不得假称 exactly-once。

## 9. Mote Terminal

### 9.1 定位

Mote Terminal 是 Runtime 中的持久化命令环境。它可以单独通过 MCP 等适配器服务其他 Harness，但在 Mote 内部通过 typed protocol 和 Persistence 工作。

模型侧能力可以最终收敛为一个熟悉的 Terminal 表面：

```text
Shell source
  ↓
Rust Bash-compatible parser
  ↓
Shell AST / typed effect plan
  ↓
Native | Browser | Android | App | Linux | Agent provider
  ↓
durable receipt / normalized result
```

例如：

```bash
rg -l GraphExecutor execution/ | xargs -P 16 view --lines 1:120
image generate --prompt-file prompt.md --output hero.png
browser click --role button --name '登录'
agent spawn --goal-file audit-prompt.md --map files.txt
```

命令名是模型熟悉的交互语言；底层不必真的启动同名 Linux 子进程。Parser 可以把已知命令直接提升为 typed effect，经过审批、资源控制、持久化和结构化结果返回。

### 9.2 为什么不需要独立 Workflow Tool

Shell 已经提供：

- pipe 与数据流；
- `&&`、`||` 与条件；
- loop/map；
- `xargs -P` 与并行；
- 变量、文件和脚本；
- 可复用 CLI 包。

因此声明式 Workflow 可以由 Shell AST 表达，再映射到统一可靠执行机制，无需再给模型一套并行的 Workflow DSL。复杂计算写 Python，组合与交互写 Shell，状态和副作用由 Rust 承接。

`Read`、`Search`、`Edit`、`Browser`、`Android`、`Image` 等不必成为零散模型工具；它们可以成为高质量 CLI 命名空间。Agent/Spawn 的语义仍由 Control 和 Kernel 拥有，即使模型通过 `agent spawn` 命令调用，也不能让 Terminal 成为控制面事实 owner。

### 9.3 Terminal 状态

Terminal session/tab 是持久实体，并与 Agent 生命周期解耦：

- Agent 可以连接、离开或重新连接某个 tab；
- tab 的 owner、共享者、lease 和 authority 必须明确；
- Agent 重启不要求进程环境随之消失；
- Terminal 崩溃后从 durable session/operation state 恢复。

恢复能力分级：

```text
Mote 原生命令/typed effect    细粒度状态与 receipt 恢复
受控 Linux 命令              默认命令边界恢复
任意不透明 Linux 程序        不能承诺任意指令级恢复
外部不可控副作用             idempotency key 或 reconcile
```

Mote 不需要重写 Linux 内核。Rust Agent 用户态层复用 Linux 的进程、文件、网络和驱动能力，在其上增加 Agent 所需的状态、权限、执行计划和恢复协议。

### 9.4 Workspace 与远程执行

远端执行应尽量保持用户看到的路径语义，但不能默认把整个宿主机无边界挂载给 worker。Workspace 层负责：

- 按用户授权暴露目录；
- 保持与用户目录结构对应的逻辑路径；
- 将授权文件同步、按需读取或安全挂载到远端；
- 文件变更 staging、事务提交和冲突检测；
- 授权扩展与撤销；
- 隔离 Agent、执行环境和用户宿主机。

`ls` 等命令应看到授权命名空间中的一致视图。要实现“远端与本地对等”，依赖受控文件虚拟化和设备/应用 provider，而不是把远端机器伪装成拥有整个本地系统。

## 10. Mote Infra Persistence 分包

本地 Persistence 应是一个 Rust workspace。crate 按不变量和事实类型拆分，不按未来微服务数量拆分：

```text
mote-infra/persistence/local/
├── Cargo.toml
├── crates/
│   ├── mote-state/
│   ├── mote-operation/
│   ├── mote-coordination/
│   ├── mote-commit/
│   ├── mote-execution/
│   ├── mote-workspace/
│   ├── mote-store-sqlite/
│   └── mote-protocol/
└── apps/
    └── mote-persistenced/
```

### 10.1 `mote-state`

定义通用 aggregate 状态机制：

- `AggregateKey`；
- `SchemaId` / `SchemaVersion`；
- opaque immutable payload envelope；
- revision、checksum 和稳定校验；
- snapshot query 与 CAS precondition。

它不知道 payload 是 `AgentState`、`ContextState` 还是 `TerminalSession`。

### 10.2 `mote-operation`

定义一次逻辑调用的 durable identity 与 receipt：

- `OperationId`；
- request fingerprint；
- operation lifecycle；
- result/failure receipt；
- duplicate、conflict、query 与 reconcile handle。

Operation 不应藏在 Terminal 或 execution 内，因为 Gateway、Context、Control 和其他 Runtime 都需要同一套响应丢失语义。

### 10.3 `mote-coordination`

定义并发所有权：

- lease key、holder 与 deadline；
- acquire、renew、release、expire；
- 单调 fencing token；
- incarnation/epoch guard；
- stale owner 的确定性拒绝。

它只知道某个资源当前由谁持有，不知道这个资源是不是 Graph node 或浏览器 tab。

### 10.4 `mote-commit`

定义唯一写入事务语言：

```text
CommitBatch
├── revision preconditions
├── fencing preconditions
├── state mutations
├── operation receipt transitions
├── attempt transitions
└── optional durable event/outbox records
```

所有需要原子性的写入通过同一个 `CommitPort` 完成。不能让 state repository、operation repository 和 lease repository 分别提交，再由调用方假定三次提交等于一个事务。

例如节点结算可以在一次事务内完成：

```text
verify AgentState revision
verify execution fencing token
write new AgentState
complete operation receipt
settle execution attempt
```

### 10.5 `mote-execution`

定义语言无关的可靠 attempt 机制：

- enqueue、claim、heartbeat；
- complete、typed fail、fence；
- resource admission；
- deadline/timer；
- crash 后重新领取；
- 机械 retry/reconcile 调度。

它不拥有 Graph topology、frontier、routing、Think/Act 或业务 retry 决策。Kernel 决定“下一步是什么”，Persistence 只可靠推进已经声明的 attempt。

当前 Python `execution` 中属于 Graph 语义的部分继续归 Kernel；可语言无关化的 claim、lease、fence、resource、attempt 等机制逐步迁到 Rust。迁移期间不能长期保留两套 authoritative runner，切换行为必须由 `conformance/` 固定并完成旧路径删除。

### 10.6 `mote-workspace`

负责文件世界的专用状态机制：

- workspace manifest；
- content-addressed blob；
- staged file mutation；
- commit/rollback 与冲突；
- path capability 与授权边界；
- 本地/远端视图同步。

文件状态的体量、并发和安全属性不同于普通 aggregate，因此独立成包，不塞入 `mote-state`。

### 10.7 `mote-store-sqlite`

第一阶段唯一存储适配器建议采用 SQLite WAL：

- 在一个数据库事务中实现 `CommitPort`；
- 验证 CAS、lease/fence、operation replay 与 crash recovery；
- 提供确定性故障注入测试；
- 先固定语义，再按相同 conformance 增加 PostgreSQL 或分布式实现。

不要预先设计万能数据库插件框架。适配器必须服从核心事务语义，而不是让最低公分母数据库 API 反向决定架构。

### 10.8 `mote-protocol`

负责 Rust 内部类型与跨语言 wire DTO 的严格映射：

- 从根目录 `conformance/` 生成或验证 DTO；
- Protobuf/gRPC 或 Unix socket transport；
- wire version、错误码和兼容拒绝；
- internal type 与 wire type 的显式转换。

wire DTO 不得直接渗入核心 crate。PyO3 可以作为局部优化，但不应成为唯一主边界，否则 Persistence 会重新绑定 Python 进程。

### 10.9 `mote-persistenced`

唯一组合根负责：

- 配置与密钥加载；
- 打开存储；
- 装配 commit、execution 和 workspace；
- 暴露 RPC/Unix socket；
- telemetry 与 graceful shutdown。

第一阶段使用一个进程即可。crate 边界不等于微服务边界，只有出现明确的隔离、扩缩或故障域需求时才拆部署。

### 10.10 依赖方向

```text
mote-state       mote-operation       mote-coordination
      \                |                    /
       \               |                   /
                    mote-commit
                         ↑
                   mote-execution

mote-workspace ───────▶ commit/state/operation（按需）
mote-store-sqlite ────▶ 实现统一 query/commit ports
mote-protocol ────────▶ 边界映射，不成为领域 owner
mote-persistenced ──────────▶ 最终装配
```

禁止出现 owner 不明的 `common`、`shared`、`utils`、`helpers` 或万能 `models` crate。若多个包需要一个类型，应找到它表达的不变量并放到唯一 owner，而不是建立杂物包。

## 11. Shell AST 与唯一 execution 的关系

Shell 是 Workflow 前端，但 Shell AST 不能发展成第二套 Agent Graph 真相：

```text
Kernel Graph
  拥有 Agent Flow 语义、node settlement 与 routing

Terminal Shell AST
  拥有命令组合、pipe、process/effect 依赖语义

Persistence execution
  统一提供 attempt、resource、lease/fence 与 durable receipt
```

Kernel Graph 与 Terminal Shell AST 是两个不同领域的状态投影，共用一套可靠执行机制。Terminal 不能用 Shell plan 决定 Agent 下一步；Kernel 也不应重新实现 pipe、进程和 Browser command 的内部执行。

## 12. Agent Swarm

Agent Spawn Agent 是 Mote 的一等能力：

```text
Parent Agent / Model
  ├── 产生 SpawnIntent
  ├── Kernel 验证 Flow 语义
  ├── Control 建立 child identity、lineage 与 authority
  ├── child Kernel 独立驱动自己的 Agent Flow
  └── 通过 Control mailbox 返回 typed result/event
```

这种结构天然支持模型路由：不同 child 可以根据任务成本、能力和风险选择不同模型，但路由策略由 Router Runtime 实现，协作目的仍由 Agent 决定。

Swarm 不是架构预设的 supervisor/researcher/reviewer 工作流。Control 保存群体存在、关系和通信事实；模型动态形成、调整和收敛协作结构。

## 13. 跨语言契约

根目录 `conformance/` 是所有跨语言可观察行为的唯一 owner：

```text
conformance/
├── schemas/     wire DTO 与严格 union
├── vectors/     codec、identity、CAS、reducer 固定向量
├── scenarios/   crash/replay/fence/reconcile 多步场景
├── traces/      标准可观察轨迹
└── spec/        runner 与版本规则
```

跨语言 durable protocol 改动必须在同一变更中更新 schema、行为向量和受影响 runner。禁止复制 Python、Go 或 Rust 实现来假装复用；复用发生在稳定 schema、identity 算法和行为向量上。

优先覆盖以下 conformance 场景：

- stale revision CAS 被拒绝；
- `AgentState` 不可撕裂提交；
- lease 到期、抢占与 stale token 拒绝；
- 同 `OperationId` 相同/不同 fingerprint；
- commit 前、commit 中、commit 后响应前崩溃；
- effect 成功但响应丢失；
- `Unknown` outcome 的 reconcile；
- Terminal receipt 成功、Context 配对失败后的恢复；
- Control Spawn 响应丢失后的幂等查询。

## 14. 目标仓库结构

长期目标可以按以下边界组织：

```text
motev2/
├── mote-product/          TypeScript：Agent 定义、配置与 UI
├── mote-control/          Go：控制面
├── mote-resource/         资源注册、发现与句柄解析
│   ├── container/         Agent/Kernel 容器宿主
│   │   ├── local/
│   │   ├── docker/
│   │   └── cloudflare/
│   │       ├── python/    Python Worker 与 Durable Object Container
│   │       └── ts/        TypeScript Worker 与 Durable Object Container
│   └── embodiment/        物理本体能力（机器人等）
├── mote-kernel/           Python：Agent Flow 语义
├── mote-runtime/
│   ├── context/
│   ├── router/
│   ├── gateway/
│   ├── approval/
│   ├── eventbus/
│   └── terminal/          Rust 为主，可含 provider adapters
├── mote-infra/            基础设施适配器
│   ├── invocation/        唯一调用基础设施
│   │   ├── contract/      窄类型调用契约
│   │   ├── resolver/      显式实现解析
│   │   ├── local/         本地调用实现
│   │   └── rpc/           远程调用实现
│   │       ├── http/
│   │       ├── grpc/
│   │       └── websocket/
│   ├── persistence/       部署相关的持久化与可靠执行机制
│   │   ├── local/         Rust：本地与宿主机原生实现
│   │   └── cloudflare/    Cloudflare Durable Object SQLite Adapters
│   │       ├── python/    Python persistence Adapter
│   │       └── ts/         TypeScript persistence Adapter
└── conformance/           跨语言 observable contracts
```

这是目标所有权图。Cloudflare Container 脚手架位于
`mote-resource/container`，Embodiment provider 按实际消费者逐步落地；
`mote-infra/invocation` 与 `mote-infra/persistence` 是平行且唯一的调用/存储基础设施 owner，对应 CI 必须保持独立。Invocation 的 local/RPC 实现按实际协议消费者逐步落地。空目录或单纯重命名不算完成分层。

## 15. Persistence 的并行开发顺序

Persistence 可以独立于上层具体业务并行开发，但应从不变量开始，不从完整 Terminal 或重写 Graph 开始。

### Phase 0：协议与故障矩阵

- 定义 aggregate/revision、operation、lease/fence、commit 的最小 schema；
- 编写固定 identity vectors；
- 列出每个 commit crash point 的预期结果；
- 建立 Rust conformance runner 骨架。

### Phase 1：State + Commit + SQLite

- 实现 versioned opaque aggregate；
- 实现 revision CAS；
- 实现一个数据库事务内的 `CommitBatch`；
- 做进程崩溃与 reopen 测试。

### Phase 2：Operation + Coordination

- durable operation receipt；
- request fingerprint conflict；
- lease/renew/fence；
- stale worker 无法提交。

### Phase 3：Execution Attempt

- enqueue/claim/heartbeat/complete/fail/fence；
- resource admission；
- crash 后重领；
- 不引入 Graph/Think/Act 语义。

### Phase 4：Kernel 纵向切片

```text
Python Kernel reducer
  → typed AgentState change
  → Rust Commit(expected_revision, fencing_token, operation_id)
  → durable receipt
  → Python 替换内存 snapshot
```

只有这个切片通过 conformance 后，才开始把现有 Python 中的机械 execution 能力逐项下沉。

### Phase 5：Workspace 与 Terminal

- durable tab/session；
- 原生命令 typed effect；
- 文件事务；
- Shell AST 的可恢复执行；
- Browser/Android/App provider；
- 不透明 Linux 进程的命令边界恢复。

## 16. 明确不做

当前 Persistence 初期不应同时追求：

- Rust 重写 Kernel Graph reducer；
- 任意 Linux 程序任意指令级无感恢复；
- 完整 Bash 全兼容；
- 多数据库插件市场；
- 通用事件溯源框架；
- 为每个 crate 单独部署微服务；
- 跨所有 Runtime 的隐式分布式事务；
- 第二套 Workflow DSL、runner 或 hidden fallback。

这些目标要么属于其他 owner，要么需要核心不变量稳定后再评估。

## 17. 架构验收原则

后续设计与代码评审至少回答以下问题：

1. 这个事实的唯一 owner 和权威 aggregate 是谁？
2. 失败发生在任意边界时，系统能否判断下一步是查询、重放、reconcile 还是终止？
3. 是否存在两套 identity、runner、状态副本或 validation owner？
4. 旧 worker 是否可能绕过 fencing 再次提交？
5. Runtime 成功响应是否已经有 durable receipt？
6. Kernel 是否只解释 typed result，而没有直接操作持久化实现？
7. 需要原子一致的数据是否真的经过同一个 `CommitBatch`？
8. 不需要原子一致的数据是否错误引入了分布式事务？
9. 新能力能否复用唯一 execution/state 基础设施？
10. 跨语言行为是否由 `conformance/` 固定？

## 18. 一句话总结

> Mote 允许能力与调用自由组合，但要求事实单点归属、状态统一提交、执行统一恢复；模型通过 Kernel 驱动 Agent，通过 Control 组成 Swarm，Resource 解析 Container/Embodiment 绑定，Invocation 完成本地或远程调用，Runtime 提供领域能力，Port 配置独立选择 Persistence backend 提供持久而可靠的计算基础。
