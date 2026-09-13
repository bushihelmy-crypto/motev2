# Cloudflare Resident Agent 生命周期设计

状态：设计结论待实施
日期：2026-09-08
修订：Invocation 先走 `mote-infra/invocation`，Persistence backend 与 Container 解耦；Cloudflare
部署统一为扁平的 TypeScript Container 项目。

## 1. 一句话结论

**Kernel 不实现 TTL，也不主动按空闲时间释放 Graph。承载 Agent 的 Container 工作进程活着时保留同一个
`Agent` 热态；该进程被 Cloudflare 停止、回收或重启后，内存自然消失，新实例再通过 `Agent.run()` 从持久化
断点恢复。**

需要同时区分两件事：

- 运行上下文默认留在活着的进程内，用于加速下一次调用；
- Agent 状态不能只放内存，每个已确认 transition 仍然必须先持久化。

## 2. 边界归属

| 层 | 负责 | 明确不负责 |
| --- | --- | --- |
| Kernel | `Agent.run()`、Graph 执行、持久化 Port/提交语义、断点恢复、热上下文复用 | TTL、定时器、LRU、Cloudflare API、容器停止策略、具体通信/数据库实现 |
| Container | AgentId 到实例的稳定映射、承载一个 `Agent` 实例、配置平台 idle timeout、启动和停止 | Graph 状态机、恢复语义、Config 版本选择、持久化 schema |
| Invocation infra | 按显式配置解析目标和通信实现（local、Unix socket、HTTP、gRPC 等），传递 typed request/result | Graph/Agent 语义、State、持久化事务 |
| Persistence infra | durable aggregate、CAS、receipt、reconcile，以及具体 backend（Cloudflare、local、remote） | 内存驻留时间、容器调度、通信选择 |
| Cloudflare | 实例放置、平台回收、主机迁移和运行时重启 | Agent 状态正确性 |

因此 Kernel 中不增加以下内容：

- `idle_ttl` 或 `WarmRuntimePolicy`；
- 内存扫描器、LRU、`max_entries`；
- Cloudflare lifecycle Port；
- 到期后主动 `release()` 的后台任务。

Container 层只决定宿主何时结束，不解释 Kernel 内的 ReAct、State、frontier 或恢复证据。

### 2.1 关键纠正：Invocation 先于 Persistence

本仓库不是 `Container → Cloudflare Persistence` 的直连结构。持久化调用必须先经过 Kernel 的调用接缝，再由
`mote-infra/invocation` 选择通信实现，最后才到达配置选中的 Persistence backend：

```text
Kernel owner Port（例如 Graph.Commit / load / latest-config）
  → mote_kernel.invocation.Invocation（只做 typed 调用接缝）
  → mote-infra/invocation resolver
  → local / Unix socket / HTTP / gRPC / WebSocket transport
  → 目标侧 composition/resolver
  → mote-infra/persistence 的选定 backend
  → typed confirmation/result
  → Kernel admission
  → 只有确认后才替换内存 snapshot
```

这里的几个边界必须保持清楚：

- `src/mote_kernel/invocation.py` 不 import Cloudflare、socket、HTTP 或数据库；它只定义 `Invocation` 和
  typed admission/error policy。具体实现对象由 composition 注入。
- 不要把 `src/mote_kernel/execution/invocation.py` 和它混为一谈：后者是 Kernel 内部的 recovery/fence
  planning 模块，不是通信层，也不负责寻找 Persistence。
- `mote-infra/invocation` 负责“怎么把请求送到目标”，不负责 State、CAS 或事务。
- `mote-infra/persistence` 负责“目标收到请求后怎样读写 durable state”。Cloudflare DO SQLite 只是其中一个
  backend，也可以换成 local Rust、远端数据库或其他实现。
- Container 只提供宿主能力、目标句柄和平台上下文；它不能绕过 Invocation 直接构造或调用 Persistence。

因此，若 Python Kernel 通过 Unix socket 访问外部服务，正确关系是：

```text
Python Kernel
  → mote_kernel.invocation.Invocation
  → mote-infra/invocation/local 或 rpc（Unix socket adapter）
  → 对端 invocation endpoint
  → persistence resolver
  → 当前配置的 Persistence backend
```

`container_id`、DO stub、socket 路径等只属于 transport/adapter 的目标绑定，不进入 Kernel 的公共
`Agent.run()` 或 `GraphRunState`。

一句大白话：Invocation resolver 只回答“请求往哪儿、用什么方式送”；Persistence resolver 才回答“落到哪种
存储”。两者都由显式配置装配，不做隐式发现或偷偷 fallback。

在 Cloudflare 这一种部署里，具体可以读成两段：

```text
Linux Container（Python）                         Worker/DO（TypeScript 控制面）
┌──────────────────────────────┐                 ┌──────────────────────────────┐
│ Agent / Kernel               │                 │ invocation resolver          │
│   → Invocation               │                 │   → persistence resolver     │
│   → infra Unix-socket gateway│ ─────────────→  │   → CF Commit(ctx.storage)  │
└──────────────────────────────┘                 └──────────────────────────────┘
                                                        │
                                                        ▼
                                                  Durable Object SQLite
```

这只是 Cloudflare 的一组 composition；把右侧换成本地 Rust daemon、Postgres 服务或别的持久化实现时，左侧
Kernel 和 `Agent.run()` 不变。

## 3. Cloudflare 的实际情况

Cloudflare 有两个容易混淆的运行模型。

### 3.1 Durable Object 实例

仓库当前的 `mote-resource/container/cloudflare` 是唯一、扁平的 TypeScript Cloudflare 部署项目，使用的是
自定义的原生 `DurableObject`（还不是 `@cloudflare/containers` 的高层 `Container` 子类）：

- 一个逻辑 Agent 计划映射到一个 Durable Object identity；
- 当前类只是返回 `501` 的脚手架；
- 当前没有引入 `@cloudflare/containers`，也没有 `sleepAfter` 配置。

按照 Cloudflare 当前官方生命周期说明：

- 满足 hibernation 条件时，Durable Object 目前会在无活动约 10 秒后休眠并丢弃内存；
- 不满足 hibernation 条件时，通常会在无活动约 70–140 秒后被逐出；
- 部署、平台更新和放置决策也可以随时重启实例；
- Durable Object 没有可靠的 shutdown hook，不能等回收通知才保存状态。

所以自定义 Durable Object 可以“活着时顺便复用内存”，但仅有当前的 storage 配置时不会自动提供
`@cloudflare/containers` 那套 `sleepAfter` 高层属性。给这个 Durable Object 增加 Container binding 后，
适配器就可以通过低层 `ctx.container` API 加 Durable Object alarm 自己实现同等的空闲停止策略；当前仓库只是
还没有接上这个 binding 和策略。Python Kernel 未来作为 Linux Container 镜像中的工作进程运行，不再维护
Python Cloudflare Worker/Container 适配器。

### 3.2 Cloudflare Containers

真正的 Cloudflare Containers 由 Durable Object 管理，容器镜像运行在独立的 Linux VM 中，镜像语言不限（包括
Python）。Worker 侧最常用的是 `@cloudflare/containers` 提供的 JavaScript/TypeScript `Container` 高层类；
也可以直接使用 Durable Object 的低层 `ctx.container` API。高层类提供：

- `sleepAfter`：无活动多久后停止容器，默认值为 `"10m"`，也可以显式配置；
- 新请求自动重置 inactivity timer；后台活动可以调用 `renewActivityTimeout()`；
- `onActivityExpired()`：超时后的容器层回调，默认调用 `stop()`；
- 停止时先向容器主进程发送 `SIGTERM`，最多等待 15 分钟，再发送 `SIGKILL`；
- 容器磁盘是临时的，睡眠后不能用本地磁盘恢复 Agent。

`sleepAfter` 只是正常空闲策略，不是存活保证。OOM、rollout、主机重启或平台决策仍可能提前停止容器。

若目标是“可配置 Cloudflare TTL，到期才回收整个 Agent 进程”，应由
`mote-resource/container/cloudflare` 在 Container 层接入上述 API；不能把 Kernel 的内存策略或原生 Durable
Object 的逐出行为包装成 Kernel TTL。唯一需要维护的 Cloudflare 适配器是 TypeScript Worker：它负责启动和
管理运行 Python Kernel 的 Linux Container 镜像，并通过预留的 Invocation/Unix-socket 接缝通信。Python
代码本身不需要、也不应再实现一套 Cloudflare Worker 适配器。

官方依据：

- [Cloudflare Containers 生命周期](https://developers.cloudflare.com/containers/concepts/architecture/)
- [Container class 与 sleepAfter](https://developers.cloudflare.com/containers/reference/container-class/)
- [Durable Object 低层 Container API](https://developers.cloudflare.com/durable-objects/api/container/)
- [Durable Object 生命周期](https://developers.cloudflare.com/durable-objects/concepts/durable-object-lifecycle/)

## 4. 目标运行方式

```text
request(agent_id)
  → Worker/Control 按稳定 AgentId 定位同一个 Container
  → Container 工作进程注入已配置的 Invocation/Port capabilities
  → 工作进程取得自己唯一的 `Agent` 实例
  → Agent.run(request)
       ├─ resident context 可用：校验后直接继续
       └─ 新进程或热态失效：通过 Invocation load → latest Config → assemble → recover
  → 每个 transition 先 durable commit，再更新内存 snapshot
  → 返回结果
  → Container 继续存活：Agent/Graph 热态原样保留
  → Cloudflare 停止 Container：进程退出，内存自然释放
```

Container 宿主只暴露或转发 `Agent` 入口，注入通信目标/平台 capability，不能接触或保存 `GraphRunState`、
continuation、Graph owner 等内部对象，也不能把请求直接改写成 Cloudflare SQL。

一个逻辑 Agent 必须使用稳定的 Container/Durable Object identity。不能用面向无状态分流的随机实例，
否则内存复用、串行调用和单 Agent 并发边界都会失效。

## 5. Kernel 内需要准备什么

### 5.1 唯一公共 API

应用和 Container 只调用：

```python
result = await agent.run(request)
```

调用方不传 State、continuation、Config revision、commit callback 或恢复模式。

### 5.2 Resident context

`Agent` 内部可以持有至多一个 sealed resident context，包含：

- compiled Graph family；
- 已解析且已验证的 Config snapshot；
- root/child owner 与 frame/evidence 的内存投影；
- 最近一次已经 durable confirmation 的 `GraphRunState` snapshot；
- 用于校验的 aggregate revision、Config digest、family digest 和 fence observation。

它只能是持久事实的可丢弃投影，不能包含：

- 未确认 candidate；
- active execution lease；
- 仍在运行的 session/task/child；
- 结果未知的外部 effect；
- 只存在内存、无法从 durable evidence 重建的业务事实。

### 5.3 调用结束不主动释放

当前 `Graph.run()` 最终会无条件 `release()` owner。Agent 模式需要把生命周期动作从执行算法中拆出来：

- standalone Graph 调用仍可在结果返回后 release；
- `Agent.run()` 到达安全、已提交的 quiescent boundary 后保留 owner；
- 下一次 `Agent.run()` 仍交给同一个 `family_driver` 继续；
- 不能复制 executor、scheduler、reducer 或 recovery runner。

这里可以有 Kernel 内部的 `park/unpark` 能力，但它只表达 owner 是否可继续使用，不带时间概念，也不感知
Cloudflare。Kernel 默认 park；何时整个 `Agent` 对象消失，由 Container 生命周期决定。

### 5.4 热调用仍然要校验

复用内存不能跳过正确性检查。每次 `Agent.run()` 至少要通过已配置的 Invocation 确认：

- durable aggregate head 没有被其他 worker 改写；
- 自动取得的 latest Config head/digest 与当前 resident context 一致；
- compiled family、descriptor、codec 和 schema 仍兼容；
- authority/fencing 仍允许当前实例推进。

可以增加轻量的 `DurableHead` 和 `ConfigHead` 查询；这两个查询也必须走同一条 Invocation → Persistence
路径，避免热调用重新下载、解码全部证据。它们只是版本摘要，不是第二份 State。任一检查不一致，就丢弃
resident context，走完整恢复或按兼容性规则 fail closed。

## 6. Container 层需要实现什么

### 6.1 实例字段

一个逻辑 Container 运行环境只保留一个 `Agent`。这里必须区分 Cloudflare 的两个进程：

```text
Worker/Durable Object（控制层）   → 路由、alarm、storage、Container handle
Linux Container process（工作层） → Python Kernel、Agent、resident Graph context
Durable storage                 → 唯一权威状态
```

如果要求“只在 Cloudflare Container 被回收时释放 Graph 内存”，推荐把 `Agent` 放在 Linux Container
process 内，外层 Worker/DO 只持有稳定路由和 Container handle；容器停止时该进程的内存自然消失。若把
`Agent` 放在外层 DO instance field，`sleepAfter` 只保证停止子容器，DO 自己仍可能继续驻留或稍后才
hibernate/evict，因此不能把它宣称为精确的 Agent 内存释放边界。

首次请求通过部署 composition root 在工作进程中创建 `Agent`，后续请求复用同一实例。composition root 把
Kernel Port 绑定到 Invocation capability；Invocation resolver 再把请求送到目标侧 Persistence resolver。
Port 配置独立选择 durable/config/authority 实现；外层 Container adapter 只暴露平台 handle 和通信目标，
不选择也不实现持久化 backend，不另外缓存一份 State，也不实现恢复分支。

若暂时采用“Agent 在 DO、Container 作为其下游执行进程”的过渡形态，必须把 DO hibernation/eviction 当作
另一条独立的内存边界，并在验收中明确这不是 `sleepAfter` 的直接结果。

### 6.2 Cloudflare 生命周期配置

在 Container adapter 中显式设置 idle timeout，不要依赖 Kernel 配置：

- TypeScript adapter 直接继承 `@cloudflare/containers` 的 `Container`，设置 `sleepAfter`，并负责承载运行
  Python Kernel 的 Linux 镜像；
- 普通请求让适配器自动续期；只有确实属于同一 Agent 的后台工作才续期；
- 到期回调只处理 Container 是否 stop，不调用或复制 Kernel 状态机；
- 停止回调只用于日志、指标和运维动作。

建议首版在 TypeScript adapter 中显式使用 `10m`，与 Cloudflare 高层类当前默认值对齐；后续根据冷启动耗时、
命中率和费用在 Container 配置中调整。这个数值不进入 Kernel contract、Agent Config 或 durable snapshot。

### 6.3 停止时不依赖主动内存释放

正常 `sleepAfter` 停止时，容器进程退出会自然回收 Agent/Graph 内存。可以响应 `SIGTERM` 尽力关闭连接和
本地句柄，但不能把它当作正确性步骤：

- 不等 SIGTERM 才提交状态；
- 不等 `onStop()` 才写 checkpoint；
- 不假设 SIGTERM 或回调一定执行；
- `SIGKILL`、OOM 和主机故障后也必须能得到相同的恢复结果。

## 7. 持久化恢复闭环（Invocation-first）

### 7.1 唯一分流

每次冷启动或 resident context 失效时，`Agent.run(request)` 内部只走这一条流程。下面的
`invoke(persistence_request)` 是概念表示，具体 transport 由 `mote-infra/invocation` 显式配置：

```text
Agent.run(request)
  → Kernel owner Port 组装 typed persistence request
  → mote_kernel.invocation.Invocation
  → mote-infra/invocation resolver + transport
  → 对端 persistence resolver
  → 选定 backend 执行 load/latest-config/commit
  → 返回 typed result/confirmation
  → Kernel admission
  ├─ None
  │    → project initial input
  │    → 再走同一条 Invocation → Persistence 路径执行 atomic create-if-absent
  │    → winner 继续执行；竞争失败则 reload winner
  └─ Existing aggregate
       → decode and validate complete evidence
       → latest Config 已由同一条 Invocation 路径取得
       → rebuild root/children/frames
       → recover from confirmed checkpoint
       → 有 resume input 才执行 resume command
```

只有 `load()` 明确返回 `None` 才表示从未创建。权限错误、网络错误、未知 schema、digest 不符、缺证据或
已 purge 都不能被解释成 `None`。已有终态只投影原结果，不自动创建新 run。

### 7.2 Kernel 先冻结的 Port，具体实现经 Invocation

Kernel 先定义窄 typed Port；Port 的实现可以包住一个 `Invocation`，但不把 transport 或 backend 类型暴露给
Kernel。下面只表示职责，不冻结公共类名：

```python
class AgentDurablePort(Protocol[GraphValueT]):
    async def load(self, identity: AgentIdentity, /) -> DurableAgentAggregate[GraphValueT] | None: ...
    async def create_if_absent(
        self, request: CreateAgentCommit[GraphValueT], /
    ) -> DurableCommitConfirmation[GraphValueT]: ...
    async def commit(
        self, request: AgentCommitRequest[GraphValueT], /
    ) -> DurableCommitConfirmation[GraphValueT]: ...
    async def reconcile(self, commit_id: DurableCommitId, /) -> ReconcileResult[GraphValueT]: ...


class LatestConfigPort(Protocol):
    async def load_latest(self, identity: AgentIdentity, /) -> ConfigSnapshot: ...


class ExecutionAuthorityPort(Protocol):
    async def acquire(
        self, identity: AgentIdentity, observed: ExecutionLeaseObservation | None, /
    ) -> ExecutionAuthority: ...
```

这些 Port 的实现关系应当是：

```text
AgentDurablePort / LatestConfigPort / ExecutionAuthorityPort
  → typed Invocation capability
  → mote-infra/invocation resolver/transport
  → 对应 owner 的 endpoint
  → persistence/authority backend
```

以 `Graph.Commit` 为例，Kernel 侧适配器的形状应当是“把 Port 请求交给 Invocation”，而不是持有
`ctx.storage`：

```python
class CommitAdapter:
    def __init__(self, invocation: Invocation[PersistenceRequest, PersistenceReply]) -> None:
        self._invocation = invocation

    async def __call__(self, transition: Graph.Transition[ValueT], /) -> Graph.State:
        request = PersistenceRequest.from_graph_transition(transition)
        reply = await invoke_typed(self._invocation, request, PERSISTENCE_CONTRACT)
        return reply.confirmed_state
```

这里的 `CommitAdapter` 只做 Kernel Port 与 typed invocation DTO 的映射；`PersistenceRequest` 到底由哪个
endpoint 接收、endpoint 再构造 Cloudflare/local/remote backend，均由 infra composition 决定。上面的名称是
实施示意，不是现在要冻结的公共 API。

Port 使用具体 immutable DTO 和 sealed result/error variant，不使用 `Any`、裸字典、反射或字符串
discriminator。Kernel 不为 Cloudflare 增加特殊 Port；Cloudflare storage 只在选中的 persistence adapter
内部出现，`sleepAfter` 只在 Container adapter 中出现。`mote-infra/invocation` 当前是 scaffold，具体
wire/request 只有在这条真实纵向切片落地时才冻结，不能在 Kernel 里偷偷再造一套通用 RPC。

对 `Agent` 组装而言，持久化 Invocation/Commit 是必需 Port：缺失时 assembly 失败，不能沿用
`Graph.run(commit=None)` 的进程内确认作为 Agent 的隐式持久化替代。

### 7.3 Durable aggregate

Durable aggregate 是持久化 envelope，不是第二种 runtime State。它必须原子覆盖：

- root 和所有 child 的 `GraphRunState`；
- graph input、成功 publication 和 resume input；
- child lineage/boundary 与 invocation binding；
- settlement、commit receipt、effect intent/receipt；
- Config revision/digest、compiled family digest、codec/schema version；
- persistence storage revision 和 authority/fencing coordinate。

`GraphRunState` 仍是唯一 runtime state model，所有状态变化仍走同一个 command/reducer。frame index、receipt、
outbox 和 durable envelope 只是恢复证据或持久化元数据，不能各自发展出第二套 lifecycle。

### 7.4 Durable-first commit

无论容器是否热驻留，每个 transition 都遵守同一顺序：

1. reducer 产生 immutable candidate 和完整 write-set；
2. durable Port 用 expected revision、stable commit id 和 fencing token 做原子 CAS；
3. 同一事务写 candidate、evidence 和 receipt；
4. 返回与 candidate/digest 完全一致的 confirmation；
5. Graph 收到 exact confirmation 后才替换 resident memory snapshot。

提交超时或连接断开时，不能猜测是否成功。Agent 仍通过同一条 Invocation 路径使用 stable commit id 调
`reconcile()`：`Applied` 后 reload 并继续，`NotApplied` 在 authority 仍有效时重试同一提交，`Unknown` 则
暂停并暴露 typed indeterminate error。Transport 重试和 Persistence reconcile 是两个不同层次，不能在
Invocation 层偷偷把一次未知提交改成重复业务提交。

### 7.5 Latest Config

调用方和 Agent Config 都不指定恢复 revision。每次 `Agent.run()` 自动通过 Invocation 调用
`load_latest(agent_id)`：

- resident context 与 latest revision/digest 相同：继续复用；
- latest 发生兼容变化：通过同一个 State command/reducer/commit 原子采用；
- graph identity、descriptor、codec、schema 或 durable evidence 不兼容：fail closed；
- 不回退旧 Config，不清空已有状态重建。

State 可以记录实际采用的 Config revision/digest 用于审计和恢复证明，但它不是 public 恢复参数。

### 7.6 外部副作用

State CAS 不能单独保证外部副作用 exactly-once。effect-capable Port 必须使用稳定 effect key，并提供
intent、receipt 和 reconcile；provider 不能幂等或查询时，未知结果必须暂停，不能盲目重跑节点。

### 7.7 数据保留

Cloudflare `sleepAfter` 不删除 durable aggregate。若以后要清理终态数据，必须另做 retention/purge；purge 后
返回 typed tombstone，不能返回 `None`，避免把旧 Agent 误当成首次创建。Container 临时磁盘也不能作为
checkpoint 来源。

## 8. 并发规则

- Container 内可以对同一 `Agent.run()` 做 single-flight，减少同进程重复调用；
- 正确性仍由 durable CAS 和 execution authority/fencing 保证；
- 容器停止不等于 lease 已转移；新实例必须完成 takeover/fence admission；
- 旧实例即使尚未完全退出，也不能用旧 epoch 提交 transition 或执行 effect；
- `sleepAfter`、execution lease TTL 和 durable retention 是三个独立概念。

## 9. 实施顺序

### K1：Kernel resident execution seam

- 从现有 `Graph.run()` 的 drive 与 unconditional release 中拆出唯一 owner 生命周期接缝；
- Agent 到达 quiescent boundary 后默认保留 resident context；
- 不新增 public Graph API、第二 runner 或 TTL 配置；
- 保持 durable-first 和 exact confirmation 不变。

### K2：Agent durable recovery

- 完成 `load == None` 才新建，否则恢复；
- 每次自动读取 latest Config；
- 所有 load/latest-config/commit/reconcile 都通过 Kernel Port → `mote_kernel.invocation` →
  `mote-infra/invocation` → Persistence resolver；Kernel 不直连 backend；
- 完成 root/child/frame/resume/effect evidence 的跨进程恢复；
- 热态 head 校验失败时确定性切换到冷恢复。

### I1：Invocation 到 Persistence 的第一条纵向切片

- 先在 `mote-infra/invocation` 落地一个真实 transport（首选 Container 内 Unix socket），只传输 owner-defined
  typed request/result；
- 在目标侧把 invocation endpoint 显式绑定到一个 Persistence adapter；resolver 根据 Port 配置选择 Cloudflare
  DO SQLite、local Rust 或远端 backend；
- 用同一套 conformance/contract 覆盖 `load`、`latest`、`create-if-absent`、CAS commit、ack-lost 和
  reconcile；
- 不把 `container_id`、Cloudflare storage handle、SQL cursor 或 wire DTO 带进 Kernel Port。

### C1：Cloudflare Container adapter

- TypeScript adapter 使用 `@cloudflare/containers` 的 `Container` 高层类，在 Container 层配置 idle timeout，
  并启动运行 Python Kernel 的 Linux 镜像；
- 用稳定 AgentId 定位实例，并在对应运行进程内只保留一个 `Agent` 实例；
- 只向 Kernel 注入 Invocation/Port resolver 和平台 capability；不在 Container 里直接绕过 Invocation 调用
  Persistence；Cloudflare SQLite Adapter 已与该 Container 物理合并，位于
  `mote-resource/container/cloudflare/src/persistence.ts`，由 DO composition root 传入 `ctx.storage`；
- 接入启动、activity、停止、rollout 和异常退出测试。

### C2：纵向验证

- 连续请求证明复用同一 resident context；
- Cloudflare 停止实例后证明从同一 checkpoint 冷恢复；
- warm/cold 两条入口产生相同 public result 和 durable state；
- Config 更新、CAS race、ack-lost、SIGTERM、SIGKILL 和 OOM 均 fail closed 或确定性恢复。

## 10. 验收标准

1. Kernel 源码和 public API 中没有 TTL、Cloudflare、LRU 或后台淘汰器。
2. Container 活着时，连续 `Agent.run()` 不重复完整 assembly 和 evidence decode。
3. `Agent.run()` 返回后没有 active task/lease/未知 effect，但 resident context 仍可继续使用。
4. Cloudflare 回收、进程崩溃或 rollout 后，下一实例自动 `load → latest Config → recover`。
5. 不依赖 shutdown hook、SIGTERM 或 `onStop()` 保存最后状态。
6. 每个 transition 仍先持久化再更新内存，热态不成为事实源。
7. 一个逻辑 Agent 始终映射到稳定实例 identity；并发由 CAS/fence 最终裁决。
8. Container 的 `sleepAfter` 可以独立调参，不修改 Kernel、Agent Config 或 durable schema。
9. 代码中不存在 `Container → Cloudflare Persistence` 直连；替换 Persistence backend 时，Kernel、Agent API、
   Invocation contract 和 Container 生命周期无需改动。
