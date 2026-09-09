# Mote v2 架构关系图

> 这张图把目前讨论的设计理念放在一起。它表达的是“职责和能力的关系”，
> 不是一条必须从上到下经过的调用流水线。

## 总图：分层，但不是单向链路

~~~mermaid
flowchart TB
    product["Product / Evaluation<br/>Agent 定义 · 配置 · UI · 评估平台"]
    control["Control（控制面）<br/>Agent 身份 · authority · lineage<br/>spawn · 生命周期 · 通信 · placement"]

    subgraph RESOURCE["Resource（资源层）"]
        container["Container<br/>镜像 / 运行宿主 / 启动 Kernel<br/>注入平台上下文"]
        body["Embodiment<br/>具身身份 · 设备能力 · 窄句柄"]
    end

    config["ConfigSnapshot<br/>一次 activation 使用一版完整配置"]

    kernel["Kernel（语义中心）<br/>Graph + State<br/>Observe · Think · Act · ReAct · Failover<br/>Typed Port · Hook · Events"]

    subgraph RUNTIME["Runtime Services（能力实现层）"]
        context["Context Runtime"]
        router["Router Runtime<br/>只选择模型"]
        gateway["Model Gateway<br/>校验并执行一次模型调用"]
        approval["Approval / Policy Runtime"]
        terminal["Terminal / Browser / Device Runtime"]
        eventbus["Event / Notification Runtime"]
    end

    subgraph INFRA["mote-infra（可靠机制层）"]
        invocation["Invocation<br/>本地 / Unix / HTTP / gRPC / WebSocket"]
        persistence["Persistence<br/>State / CAS / atomic commit"]
        reliable["Operation · Receipt<br/>Lease · Fence · Attempt · Queue"]
    end

    conformance["conformance/<br/>跨语言 schema · vectors · scenarios · traces"]

    product -->|"声明、配置、组装、展示"| kernel
    product -->|"assignment / policy"| control
    product --> config

    control <-->|"绑定、宿主、具身资源"| container
    control <-->|"身份、权限、血缘、消息事实"| body
    container -->|"承载并启动"| kernel
    body -. "按授权提供能力句柄" .-> kernel

    config -->|"activation config"| kernel
    kernel <-->|"typed Port request / result"| context
    kernel <-->|"ModelSelection"| router
    kernel <-->|"授权请求 / 结果"| approval
    kernel <-->|"命令 / receipt / 设备结果"| terminal

    router -->|"选定模型"| gateway
    kernel -. "已配置的 service / protocol / credential ref" .-> gateway
    gateway -->|"InferenceResult + ModelReceipt"| kernel

    kernel <-->|"Invocation capability"| invocation
    kernel <-->|"AgentState / transition / recovery"| persistence
    control <-->|"identity / lineage / mailbox / lease"| reliable
    context -.-> invocation
    gateway -.-> invocation
    terminal -.-> invocation
    terminal -.-> persistence

    kernel -->|"Hook：参与当前因果过程"| hook["Hook<br/>检查 / 有界改写 value<br/>产生 typed command；不直接改 State"]
    hook -->|"继续同一 Graph transition"| kernel
    kernel -->|"Events：确认提交后的事实引用"| events["Events<br/>stable reference / outbox<br/>不是第二套 State"]
    events --> persistence
    persistence -. "确认后异步投递" .-> eventbus

    persistence -->|"confirmed State / receipt"| kernel
    reliable -->|"幂等、重试、fence、reconcile 机制"| kernel

    conformance -. "约束所有语言和远程边界的可观察行为" .-> product
    conformance -.-> control
    conformance -.-> kernel
    conformance -.-> invocation
    conformance -.-> persistence

    classDef core fill:#e8f1ff,stroke:#2563eb,stroke-width:3px;
    classDef boundary fill:#fff7e6,stroke:#d97706,stroke-width:2px;
    classDef infra fill:#eefcf3,stroke:#16a34a,stroke-width:2px;
    classDef cross fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px;
    class kernel core;
    class product,control,container,body,router,gateway boundary;
    class invocation,persistence,reliable infra;
    class config,conformance,hook,events cross;
~~~

### 怎么读这张图

Product → Control → Container → Kernel → Runtime → Infra 可以作为理解顺序，
但不是严格的单向依赖链：

- Kernel 需要 mote-infra 的 Invocation、Persistence、CAS、恢复等能力。
- Control 也需要 mote-infra 保存身份、血缘、mailbox、lease 和消息投递事实。
- Runtime 同样使用 Invocation、Operation、Receipt 和 Persistence。
- Container 负责宿主和平台上下文；它不替 Kernel 决定流程，也不强行决定持久化后端。
- Resource 下的 Container 和 Embodiment 是并列维度：一个回答“代码在哪里跑”，
  一个回答“Agent 被授权操作哪个身体/设备”。

因此，这是一张“职责网”，不是一条“请求必须逐层穿过”的流水线。

## 每层到底负责什么

| 层 | 大白话职责 | 不应该负责 |
| --- | --- | --- |
| Product | 定义 Agent、选择能力和策略、做配置和评估、把结果展示给人 | 不实现第二套 Agent 循环 |
| Control | 管 Agent 是谁、和谁有关系、谁有权限、消息发给谁、跑在哪里 | 不替模型决定思考内容和协作目的 |
| Container | 管镜像、进程宿主、启动环境、平台 binding | 不拥有 Agent 身份、流程语义或持久化选择 |
| Embodiment | 管机器人/设备的身份和能力句柄 | 不拥有 Agent Flow 或实时业务循环 |
| Kernel | 管 Graph、State、合法转换、恢复、重试/Failover、Hook 和流程下一步 | 不直接操作数据库、模型 SDK 或宿主机 |
| Runtime | 具体实现 Context、Router、Gateway、Terminal、Approval 等能力 | 不偷偷改变 Kernel State 或流程语义 |
| Infra | 管调用传输、持久化、CAS、事务、receipt、lease、fence、durable queue | 不解释 Think、Act、Spawn 等业务含义 |
| Conformance | 固定跨语言、跨进程、跨版本的可观察契约 | 不成为某一种语言的实现副本 |

## Kernel 是中心，但不是“所有事情都在 Kernel 里”

Kernel 的世界模型可以概括成：

~~~text
Graph = 允许流程怎么走
State = 已经确认发生了什么
Port  = 需要外部完成什么能力
~~~

一次 Think 大致是：

~~~text
Think
  ├─ Context Port       → Context Runtime
  ├─ Router Port        → Router Runtime（只选模型）
  ├─ Inference Port     → Model Gateway（执行模型调用）
  └─ Command Port       → 形成下一步 typed command
~~~

每个 activation 使用一个确定的 ConfigSnapshot。各节点读取自己的窄
ConfigSlice，不能让 Prompt、Router、Inference 在同一轮偷偷使用不同版本。
配置更新应生成新快照，在受控边界原子切换到下一次 activation。

## Router、Model Gateway 和服务商的关系

这三个概念不是一层套一层，而是三个独立维度的一次组合：

~~~text
Router Runtime
  → 选择 ModelSelection（调用哪个模型）

用户/Product 配置
  → ServiceConfig（发到哪个服务商、凭据是什么）
  → ProtocolId（请求长什么样）

Model Gateway
  → 校验 Model × Protocol × Service 是否兼容
  → 通过 ProtocolAdapter + ServiceConnector 发起一次调用
  → 返回统一结果和 ModelReceipt
~~~

Gateway 发现组合不兼容时，必须在访问上游前返回 typed error；它不能擅自换模型、
换服务商或静默改协议。Kernel 决定是否重试、Failover 或结束流程。

## Hook 和 Events：一前一后的扩展面

~~~text
Hook
  = 事情确认前，允许插件参与当前因果过程
  = strict，失败可以阻断本次节点
  = 可检查、有界改写业务 value、产生 typed command
  = 不直接改 GraphRunState，不拥有第二套 runner

Events
  = 事情确认后，发布已经成立的事实
  = State + event/outbox reference 先走原子提交
  = 提交确认后再通知外部，通知失败不推翻已确认 State
  = 事件是稳定引用，不复制一份业务真相
~~~

如果事件消费者想再次影响 Agent，应发起一个新的、可审计的输入或命令，
而不是回头修改旧 State。

## 最重要的正交关系

~~~text
Agent 身份       ≠ Container 身份
运行镜像/宿主    ≠ Embodiment 身份
Embodiment       ≠ Persistence 后端
Persistence      ≠ Runtime 实现
Model            ≠ Service Provider
Router           ≠ Model Gateway
Event            ≠ 第二套 State
~~~

所以同一个 Agent 可以：

~~~text
换 Container / 换镜像 / 换部署位置
        +
换 Embodiment 或暂时离开某个设备
        +
换 Persistence backend
        +
换 Runtime 实现或把本地调用改成 RPC
        =
保持同一个 Kernel Flow 和 Agent 身份
~~~

前提是 Control 的 authority、Infra 的 fencing/CAS、Runtime 的 receipt 和
Kernel 的恢复协议都闭合。

## 设计最终想带来的组织方式

~~~text
算法工程师
  → 实现 Runtime 能力和策略
  → 声明版本化配置
  → 在 Evaluation 平台组合实验
  → 跑效果 / 成本 / 延迟 / 稳定性
  → 灰度发布 ConfigSnapshot

基础设施
  → 保证同一套 Kernel 语义
  → 保证状态、恢复、幂等、通信和观测
~~~

评估平台应该调用与生产相同的 Kernel、Runtime 和配置快照，不另造一套
“评测专用 Agent 引擎”；否则评测结果无法代表生产行为。

## 一句话总结

> Mote v2 不是把所有模块串成一条固定流水线，而是让 Product、Control、
> Resource、Kernel、Runtime 和 Infra 各自拥有清晰事实与变化边界；Kernel 用
> Graph + State 维护 Agent 语义，Runtime 提供可替换能力，Infra 保证这些能力
> 在本地或远程环境中可靠地执行、持久化和恢复。
