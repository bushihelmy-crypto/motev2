# Hooks 扩展设计共识

本文记录 Mote Kernel 当前确认的 Hooks 最小模型。文中会同步 Hook 所依赖的外部
设计边界，用来消除开发疑虑；这些说明不属于本轮实现范围。

具体开发步骤见 [Hooks 扩展开发实施计划](./hooks-extension-implementation-plan.zh-CN.md)。

## 0. 本轮范围

本轮生产代码修改 `src/mote_kernel/hooks/` 以及 Kernel 级通用调用契约
`src/mote_kernel/invocation.py`；Hook 行为测试放在 `tests/hooks/`，包结构门禁同步更新。
除本设计文档和实施计划外，不修改 execution、state、failover、observe、role、
loop、events、think、act 或其他目录。

以下机制只是 Hooks 必须遵守的外部前提：

- Observe 负责更新配置；
- execution Graph 负责节点编排和节点级持久化；
- failover 包在 Port 调用上，负责重试与失败转移；
- 路由、统一状态和核心节点接入由各自 owner 负责。

Hooks 不实现、代理或改造这些能力。

## 一句话结论

HookNode 每次进入时只读取一次当前配置，由这份不可变快照生成一个 Plan，并让
P1、P2、P3 全程使用同一个 Plan。Kernel 固定 Plan → P1 → P2 → P3 的节点顺序；
每个优先级节点通过内部薄 Port 发出一次统一调用。扩展内部多个 handler 如何执行、
串行还是并行、怎样排序和合并，全部由 runtime 的具体调用实现决定。

## 1. 最小运行模型

~~~text
进入 HookNode
→ Plan 节点读取一次当前 config，得到不可变 snapshot
→ Plan 节点根据 snapshot 生成一次 HookPlan
→ P1 使用 HookPlan.p1 通过内部 Port 发出一次 invocation
→ P2 使用 HookPlan.p2 通过内部 Port 发出一次 invocation
→ P3 使用 HookPlan.p3 通过内部 Port 发出一次 invocation
→ 返回 P3 的最终结果
~~~

这条路径只需要三个核心概念：

- config snapshot：本次 HookNode 固定使用的配置值；
- HookPlan：由该 snapshot 生成的本次执行计划；
- invocation capability：根据当前优先级配置完成一次调用并返回当前 priority 的
  `HookStageResult`；最终 `HookResult` 由 HookNode 生成。

不需要以下附加概念：

- HookActivationContext；
- cycle；
- PlanMarker 或额外 Plan 版本；
- StableActivation；
- binding snapshot 或 binding generation；
- Hook Manager、bind/unbind/reload 注册表。

Plan 本身已经完整表达本次要交给 Port 的配置，不需要再用 marker 标识它。
Graph 的执行坐标和恢复信息也不需要复制进 Hook 契约。

## 2. 配置快照与动态 Plan

配置更新和配置消费分属不同 owner：

- Observe 更新配置；
- HookNode 只读当前配置；
- Hooks 不观察变化、不订阅更新，也不维护热加载注册表。

每次进入 HookNode 时，HookNode 读取一次当前配置并生成一次 HookPlan。取得
snapshot 后，在整个 HookNode 结束前不再读取“最新配置”。P1、P2、P3 必须使用
同一个 snapshot 生成的同一个 Plan。

外部在此期间发生的配置更新，只会被下一次进入 HookNode 时看到。这里不需要
版本比较，也不需要在 Hook 内处理配置更新事件。

### 2.1 HookPlan

HookPlan 是本次 HookNode 使用的不可变值，包含 P1、P2、P3 对应的
HookPriorityPlan：

~~~text
HookPlan
  p1: HookPriorityPlan
  p2: HookPriorityPlan
  p3: HookPriorityPlan
~~~

HookPlan 每次进入 HookNode 都可以根据最新 snapshot 重新生成，因此支持动态配置；
它不是图编译时永久固定的 handler 清单。

### 2.2 HookPriorityPlan

HookPriorityPlan 只承载从同一 snapshot 装载出的当前优先级配置。内部 Port 将
`plan.config` 原样交给统一 invocation capability；Hooks 不解释配置，也不包含以下
Kernel 策略：

- SERIAL / PARALLEL 执行模式；
- handler 列表的执行语义；
- barrier；
- Kernel reducer 或通用结果合并器；
- handler 级重试、恢复或持久化信息。

扩展可以在配置中声明串行、并行或其他方式。runtime 的具体调用实现负责解释和
执行，Kernel 不需要知道扩展最终采用了哪一种方式。

## 3. HookNode 的固定外壳

一个 HookNode 代表固定的四节点顺序子图：

~~~text
HookNode
  └─ Plan ─> P1 ─> P2 ─> P3
~~~

Kernel Hooks 只固定以下事实：

- Plan 是独立节点，只读取一次 config snapshot 并生成一次 HookPlan；
- P1、P2、P3 是三个确定的优先级节点；
- P1、P2、P3 使用同一个 `_PlannedPriorityNode` 实现并严格按顺序推进；
- 上一优先级节点的最终类型化结果是下一优先级节点的输入；
- 任何 command 或结果都不能让 P2/P3 短路，P3 才能结束该子图；
- 四个节点都是普通图节点。

四个节点在构造期间完成组装；拓扑冻结沿用 Graph 首次成功编译后的既有边界。本轮不在
构造期主动调用私有 `_compile()`，不增加 Hooks 自己的 `seal/finalize` 或 mutation guard。

配置事实的持久化属于 Observe。Plan 节点不建设第二套配置存储、订阅或恢复协议；
它只读取 Observe 已经持久化并暴露的当前配置。把 Plan 装载独立成节点，是为了不把
配置读取职责混进 P1，而不是为配置增加新的 owner。

扩展内部 handler 不是图节点。Kernel 不为单个 handler 建立 settlement、游标、
持久化、恢复或重试边界。runtime 内部错误若最终从 invocation 逸出，对 Kernel
来说就是当前优先级节点失败。

主图如何决定是否进入 HookNode、HookNode 挂在哪个核心节点之后，属于图装配与
路由设计，不属于本轮 Hooks 实现。

## 4. 内部 Hook Port 与统一调用边界

Hook Port 是 HookNode 内部的薄调用层，不是扩展需要实现或可以替换的 SPI，也不
单独建立 `port.py`。统一的 `Invocation` 协议位于 `mote_kernel.invocation`；HookNode
装配时接收一个符合该协议的 capability，然后在内部构造同一个 Port 供 P1、P2、P3
使用；缺少 invocation capability 时立即装配失败。

每个优先级节点只执行以下路径：

~~~text
_HookPort.execute(priority_plan, request)
→ invocation.invoke(HookInvocationRequest(priority_plan.config, request))
→ 校验返回值是 HookStageResult
→ 返回
~~~

Python 泛型参数在运行时会擦除。composition root 必须为一个 Hook 注入同一份
`HookPayloadAdmission`，声明 `config_type`、`priority_config_type`、`value_type`、
`state_type` 和 `command_type` 五个具体 nominal class。Plan 节点、内部 Port 和 P3
最终结果都复用这份 contract：按 `type(payload) is declared_type` 检查 value、state、
config、priority config 及每个 command 元素，并要求 stage/final commands 为 exact
`tuple`。`object`、`Any`、Union 等擦除或非 nominal descriptor 在装配期拒绝；嵌套业务
schema 仍由具体 invocation owner 负责，不在 Hooks 内引入深层反射或第二套 validator。

每个优先级节点只发出一次统一调用。调用最终落到本地、HTTP、gRPC 或其他传输，
由 invocation capability 的具体实现解析，Hook Port 不知道也不判断。当前只提供
可导入的窄 `Invocation` 协议，不建设 Resolver、注册表或传输占位实现。

统一 invocation 引擎未来可以由 Rust 实现，但 Rust 不直接依赖或实现 Python 的
`HookRequest`、`HookStageResult` 或 `HookResult`。装配关系是：创建 HookNode 的 composition root 注入
一个符合 `Invocation` 协议的 invocation binding；binding 负责编解码并调用 Rust invocation
引擎；Rust 只处理通用调用引用、跨语言 payload 和传输。真正需要跨语言稳定的调用
引用、payload 和错误协议时，应由其 owner 与 `conformance/` 一起定义，不能把
Python Hook 类型复制成 Rust 私有协议。

runtime 的具体调用实现拥有以下语义：

- handler 的发现和选择；
- 串行、并行、混合或其他调度方式；
- handler 顺序；
- 内部结果合并与冲突处理，并在返回前形成有序 stage commands；
- 内部取消和可容忍失败策略。

内部 Hook Port 不做以下事情：

- 不重新读取配置或生成 Plan；
- 不解释 handler、串并行或结果合并；
- 不实现 Resolver、注册表、序列化或传输选择；
- 不重试、降级或吞掉 invocation 异常。

Kernel Hooks 拥有以下语义：

- P1 → P2 → P3 的节点顺序；
- Plan、invocation 输入输出的类型边界；
- `HookStageResult.value` 向下一优先级节点传递；
- 各 priority 的 commands 只按顺序追加为最终 `HookResult.commands`，不去重、不覆盖、
  不 apply、不检测冲突；
- 错误的 value、state、config、priority config 或 command 元素在对应边界失败，不进入下一
  priority；
- invocation 异常作为当前节点失败向外传播。

因此不存在 Kernel 级通用 command policy。若扩展内部并行执行，怎样合并由扩展声明，
并由 runtime 的具体调用实现在返回 stage result 前完成。

## 5. 状态、持久化与失败边界

runtime 调用只能通过类型化结果和 command 表达变化，不能直接修改统一运行状态。
GraphRunState 的归约、提交和恢复由 execution/state owner 负责，本轮不修改；generic Hook
commands 的业务消费由外部 command owner 负责。

边界如下：

- Plan、P1、P2、P3 各自遵循普通 Graph 节点边界；
- 节点内部 handler 没有独立持久化记录；
- Hooks 不实现节点内部恢复；
- failover 直接包装 Port 调用；
- failover 重试复用当前节点已经收到的 HookPriorityPlan 和请求；
- 重试不会重新读取 config，也不会重新生成 HookPlan；
- invocation 最终抛出的错误使当前优先级节点失败。

runtime 可以把某些内部错误处理成成功结果；一旦返回成功，Kernel 只认最终类型化
结果，不解释 runtime 内部发生过什么。

## 6. 身份与坐标

Hooks 最小模型只保留 Kernel 确实需要的坐标：

| 类型 | 用途 |
| --- | --- |
| HookSlotId | 编译时固定 HookNode 所在的图槽位 |
| HookPriority | 固定 P1/P2/P3 节点身份，不描述节点内部调度 |

具体扩展或 handler 的身份属于 runtime 内部。Graph 执行坐标属于 execution，
均不复制到 Hook 请求中。

HookNode 使用 `hooks.identity.hook_definition_id(slot)` 生成自己的嵌套 Graph definition
ID；该函数采用稳定的长度前缀字段编码，Graph definition version 仍单独沿用 slot 的版本。

## 7. 包职责和公共 API

目标结构：

~~~text
invocation.py   # Kernel 级通用单 request/result 调用协议
hooks/
  __init__.py   # 最小公共导出
  identity.py   # slot 和 priority
  contract.py   # 请求、阶段/最终结果及配置读取/Plan 装载协议
  plan.py       # config snapshot、HookPlan 和 HookPriorityPlan
  node.py       # HookNode、Plan 节点、内部薄 Port 和 P1/P2/P3 节点组合
~~~

不建立单独的 `port.py`：当前 Port 只有一次转发和一次结果校验，只服务于 HookNode，
放在 `node.py` 能准确表达其内部所有权。`mote_kernel.invocation` 维护跨 Kernel
domain 共用的最小 `Invocation` 协议；`hooks.contract` 维护 Hook-specific request、
阶段/最终结果以及配置/Plan 契约。`mote_kernel.hooks` 包级公共入口仍只有 HookNode，
不提供外部 HookPort SPI。

旧的 manager.py、HookManager、HookBindingSnapshot、HookBindingGeneration、
HookManagerNode，以及携带 activation/执行坐标的旧 invocation 值模型均已放弃，
不保留兼容别名或第二条执行路径。当前 `mote_kernel.invocation.Invocation` 只是
Kernel domain 共用的最小 typed capability 协议；`HookInvocationRequest` 是 Hook 自己
的 request envelope，不是旧模型的恢复。

HookNode 使用现有 mote_kernel.execution.Graph 进行图组合，不创建私有 runner。
Hooks 不拥有配置更新、路由、EventBus、统一状态 reducer、模型路由或 failover
实现。
