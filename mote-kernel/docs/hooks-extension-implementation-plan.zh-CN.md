# Hooks 扩展开发实施计划

本计划以 [Hooks 扩展设计共识](./hooks-extension-design.zh-CN.md) 为唯一设计基线。
目标是实现足够工作的最小 Hooks 模型，不提前加入没有当前用途的身份、版本或恢复
协议。

## 1. 目标与改动边界

### 1.1 本轮目标

- 用唯一的 HookNode 表达固定 Plan → P1 → P2 → P3 子图。
- HookNode 每次进入时只读取一次配置快照。
- 由该快照生成一个不可变 HookPlan，供整个 HookNode 共用。
- HookPriorityPlan 只承载当前优先级配置。
- 每个优先级节点通过 HookNode 内部薄 Port 发出一次统一 invocation，并接收一个
  最终类型化结果。
- 串行、并行、内部排序和结果合并完全由 runtime 的具体调用实现负责。
- 删除旧 binding registry/manager 模型，不保留兼容别名。
- 为以上行为和类型边界补齐确定性测试。

### 1.2 允许修改的范围

~~~text
docs/hooks-extension-design.zh-CN.md
docs/hooks-extension-implementation-plan.zh-CN.md
src/mote_kernel/hooks/**
tests/hooks/**
~~~

生产代码严格限制在 src/mote_kernel/hooks/。测试只验证 Hooks owner 的行为，
不得为了让测试通过而修改其他包。

### 1.3 明确不在本轮实现

- Observe 的配置更新逻辑；
- Role、loop 或动态扩展发现/卸载；
- Graph 编译器、路由和核心节点挂载；
- GraphRunState schema、reducer、提交或恢复；
- failover、重试、降级和失败转移；
- EventBus、事件投递或 outbox；
- Observe、Think、Act 的具体业务实现；
- 第二套图执行器或 Hooks 私有 runner。

这些内容只作为 Hooks 契约的外部前提，不是本计划的阶段任务，也不得产生对应
目录的代码改动。

## 2. 当前基线与删除项

| 文件/类型 | 当前情况 | 本轮处理 |
| --- | --- | --- |
| identity.py | HookSlotId 可复用；仍包含 HookBindingGeneration | 保留 slot，增加 priority，删除 generation |
| contract.py | HookInvocation 携带 run、superstep 和 generation | 删除 invocation/context，保留最小请求、结果及配置/Plan 协议 |
| manager.py | 实现 bind/unbind/reload 和 binding snapshot | 删除文件及模型，不保留别名 |
| node.py | HookManagerNode 执行单个 binding | 替换为 HookNode 与三个优先级节点 |
| plan.py | 尚不存在 | 增加 config snapshot、HookPlan 和 HookPriorityPlan |
| __init__.py | 导出 HookManagerNode | 改为最小 HookNode 公共导出 |
| tests/hooks | 验证旧 manager/binding 行为 | 改为验证 snapshot、Plan、Port 和节点边界 |

以下类型和概念全部删除：

- HookManager；
- HookBinding、HookBindingSnapshot、HookBindingGeneration；
- HookManagerNode；
- HookInvocation、HookActivationContext；
- StableActivation；
- cycle；
- PlanMarker 或独立 Plan 版本。

Plan 本身就是本次执行使用的完整值，不需要再增加一层标记。

## 3. 目标运行流程

每次 HookNode 调用遵循同一条路径：

~~~text
进入 HookNode
→ Plan 节点读取一次当前 config，取得不可变 snapshot
→ Plan 节点根据 snapshot 生成一次 HookPlan
→ 使用 HookPlan.p1 发出一次 invocation，取得 P1 最终结果
→ 使用 HookPlan.p2 发出一次 invocation，取得 P2 最终结果
→ 使用 HookPlan.p3 发出一次 invocation，取得 P3 最终结果
→ 返回 P3 最终结果
~~~

整个流程中：

- config 只读取一次；
- HookPlan 只生成一次；
- P1/P2/P3 共享同一 HookPlan；
- 不在优先级节点内部展开 handler；
- 不在 Hooks 中实现重试；
- failover 若重试内部 Port，只复用当前 priority plan 和请求，不重新读取配置或生成 Plan。

## 4. 分阶段开发任务

### 阶段 0：删除过度设计

**任务**

- 删除固定“同层并行”的 Kernel 规则。
- 删除 Kernel reducer、通用结果合并器和 handler barrier 假设。
- 删除 activation context、cycle 和 PlanMarker。
- 删除 binding generation、binding snapshot、Manager reload 和 activation snapshot。
- 固定独立 Plan 节点和 P1/P2/P3 的图节点顺序，优先级节点内部执行对 Kernel 不透明。
- 固定节点是最小持久化单元，不设计 handler 级恢复。

**验收**

- Hooks 契约中不存在 SERIAL/PARALLEL 枚举。
- Hooks 契约中不存在通用 handler 结果合并器。
- Hooks 契约中不存在 HookActivationContext、cycle 或 PlanMarker。
- 本计划不要求修改 Hooks 目录外的生产代码。
- 没有为兼容旧代码保留 Manager API。

### 阶段 1：最小身份和调用契约

**建议文件**

~~~text
src/mote_kernel/hooks/identity.py
src/mote_kernel/hooks/contract.py
~~~

**任务**

- 保留编译期使用的 HookSlotId。
- 定义只表示 P1/P2/P3 的 HookPriority。
- 定义类型化 Hook 请求和最终结果。
- 定义读取配置和生成 Plan 所需的窄类型化协议。
- HookNode 接收统一 invocation capability，内部 Port 每次只接收当前
  HookPriorityPlan、上一节点的值和必需的只读状态输入。
- 内部 Port 将 `plan.config` 和请求交给 invocation，并校验其最终类型化结果。
- 使用 Generic、dataclass、Protocol 和显式类型别名，不使用 Any、裸字典、反射
  或字符串判别。

具体协议可以按单一职责拆分，但不能引入以下内容：

- Graph run、superstep 或 activation 坐标；
- HookActivationContext；
- cycle 或 PlanMarker；
- handler 列表；
- Kernel 可解释的串并行模式；
- retry/recovery 参数。

**验收测试**

- HookSlotId 和 HookPriority 的合法/非法值测试；
- 请求和结果只能使用装配时绑定的具体类型；
- 必需 invocation capability 缺失时 HookNode 装配失败；
- 调用契约中没有 Any、裸字典和执行引擎坐标；
- 不存在 activation context 或额外版本字段。

### 阶段 2：配置快照与动态 Plan

**建议文件**

~~~text
src/mote_kernel/hooks/plan.py
~~~

**任务**

- 定义不可变且泛型化的 config snapshot。
- 定义不可变 HookPlan，包含 P1、P2、P3 三个 HookPriorityPlan。
- HookPriorityPlan 只承载从 snapshot 装载出的当前优先级配置，内部 Port 将该配置
  原样交给 invocation。
- 每次进入 HookNode 都读取当前配置并生成一个新 HookPlan。
- 一次 HookNode 内只允许读取一个 snapshot、生成一个 HookPlan。
- 三个 priority plan 必须来自同一个 snapshot。
- Plan 不包含 execution mode、handler 调度图、Kernel reducer、barrier、版本 marker
  或 handler 恢复信息。

配置 payload 必须保持具体类型。不同扩展需要不同配置形状时，通过泛型协议绑定，
不能退化成 dict[str, object] 或 Any。

**验收测试**

- 一个 HookNode 调用只读取一次配置；
- 一个 HookNode 调用只生成一次 HookPlan；
- 三个 priority plan 来自同一不可变 snapshot；
- 下一次 HookNode 调用可以读取更新后的配置并生成新 Plan；
- 当前 HookNode 执行期间的外部配置更新不会改变已取得的 snapshot；
- Plan 中不存在串并行模式、Kernel 合并规则或额外版本字段。

### 阶段 3：实现 HookNode、Plan 节点与三个优先级节点

**建议文件**

~~~text
src/mote_kernel/hooks/node.py
~~~

**任务**

- 使用现有 mote_kernel.execution.Graph 组合固定 Plan → P1 → P2 → P3，不创建私有
  runner。
- HookNode 实例持有编译期 slot 和必需的类型化 invocation capability，并在内部
  构造薄 Port。
- 独立 Plan 节点取得 config snapshot 并生成 HookPlan；配置事实仍由 Observe 持久化，
  Hooks 不增加配置存储或恢复协议。
- P1、P2、P3 使用同一个 `_PlannedPriorityNode` 实现，P1 不混入配置读取和 Plan
  装载职责。
- P1、P2、P3 各使用同一个 HookPlan 中对应的 HookPriorityPlan，通过内部 Port
  发出一次 invocation。
- 上一优先级节点的最终结果传给下一优先级节点。
- runtime 内部 handler 数量、顺序、串并行方式和合并过程完全不可见。
- invocation 返回成功时内部 Port 只转交最终类型化结果；异常作为当前优先级节点
  失败传播。
- 不捕获错误做 Hook 内重试，不记录 handler 进度，不生成 handler settlement。
- 对外公共入口收敛为 HookNode。

**验收测试**

- Plan 节点在 P1 前运行，config 读取和 HookPlan 生成各只有一次；
- P1、P2、P3 严格按顺序收到调用；
- 三个调用使用同一个 HookPlan 中对应的 priority plan；
- P1 输出成为 P2 输入，P2 输出成为 P3 输入；
- 串行实现的假 runtime 和并行实现的假 runtime 都使用同一 invocation 契约；
- Kernel 不观察两种 runtime 的内部执行方式；
- invocation 返回的单一最终结果原样进入下一节点；
- invocation 异常向外传播，Hooks 不发起第二次调用；
- 单个 handler 不出现在图节点或持久化坐标中。

### 阶段 4：删除旧路径并收口公共 API

**任务**

- 删除 manager.py 以及旧 Manager/binding 类型。
- 删除 HookManagerNode、HookInvocation 和旧 activation 字段。
- 清理 __init__.py，只导出确认需要的 HookNode。
- 删除外部 HookPort SPI；薄 Port 留在 `node.py` 内部，不建立单独 `port.py`。
- 删除旧测试，补齐新模型的正反例和依赖方向测试。
- 检查 Hooks 没有创建第二套 runner、状态模型或隐式可变注册表。
- 更新本文档中的实现状态。

**验收测试**

- 导入旧 HookManagerNode、HookManager、HookBindingSnapshot 或 HookInvocation 均失败；
- 包中不存在兼容别名和隐藏旧执行路径；
- Hooks 生产代码差异没有越出 src/mote_kernel/hooks/；
- 架构测试确认 execution.Graph 仍是唯一图组合与执行门面。

## 5. 外部边界如何影响 Hooks

以下规则需要在 Hooks 测试替身中体现，但不由本轮实现。

### 5.1 Observe 与配置

Observe 更新配置。HookNode 只在入口读取当前值，随后固定 snapshot。Hooks 不轮询、
订阅或接收更新通知，也不修改 Observe。

### 5.2 Failover

Failover 包装内部 Port 调用。Hooks 只把已经生成的 HookPriorityPlan 和同一请求交给
内部 Port。失败后的重试次数、退避和替代实现由 failover owner 决定；Hooks 不重新
读取 config，也不重新生成 HookPlan。

### 5.3 节点持久化

Graph 节点是最小持久化单元。P1/P2/P3 之外没有 handler 级持久化。Hooks 不新增
GraphRunState 字段或自建恢复存储。

### 5.4 路由与核心节点接入

HookSlot 如何编译、什么时候进入 HookNode、Observe/Think/Act 如何挂载都留给后续
owner 集成。本轮只保证 HookNode 是可由现有 Graph 组合的类型化节点。

### 5.5 Rust invocation 实现

统一 invocation 引擎未来由 Rust 实现时，Hooks 不直接导入 Rust trait，也不把
`HookRequest`、`HookResult` 复制到 Rust。创建 HookNode 的 composition root 注入
一个符合 Hooks 私有调用接缝的 invocation binding，由 binding 对接 Rust 引擎。
跨语言 wire DTO、版本、错误和编解码规则必须在出现首个真实消费者时进入
`conformance/`；本轮不预造 schema、PyO3 包或 FFI 层。

## 6. 每阶段必须保持的约束

1. 生产代码只修改 src/mote_kernel/hooks/。
2. mote_kernel.execution.Graph 是唯一图组合和执行门面。
3. Plan/P1/P2/P3 是 Kernel 固定节点顺序，扩展 handler 的内部调度由 runtime 独占。
4. 一个 HookNode 只读取一个 config snapshot、只生成一个 HookPlan。
5. P1/P2/P3 共用同一个 HookPlan。
6. HookPriorityPlan 只承载配置，不表达串并行和合并策略。
7. 每个优先级只发出一次 invocation；Kernel 不合并 handler 结果。
8. 节点是最小持久化单元；Hooks 不做 handler 级恢复。
9. failover 直接作用于 Port；Hooks 不实现重试或重新生成 Plan。
10. 不引入 HookActivationContext、cycle、PlanMarker 或其他无当前用途的字段。
11. 不使用 Any、裸字典、反射、字符串判别、隐藏可变状态或兼容别名。

## 7. 验证与完成定义

### 7.1 针对性验证

~~~text
python -m pytest tests/hooks -q
python -m ruff check src/mote_kernel/hooks tests/hooks
python -m ruff format --check src/mote_kernel/hooks tests/hooks
pyright
~~~

### 7.2 仓库门禁

~~~text
make check
cd /home/longert/motev2
pre-commit run --all-files
~~~

若门禁被本轮范围外的既有未提交改动阻塞，交付时精确记录失败项，不修改其他包
来绕过门禁。

### 7.3 完成定义

当且仅当以下条件全部满足，本轮 Hooks 实现才算完成：

- 生产代码差异只位于 src/mote_kernel/hooks/；
- 对应测试只位于 tests/hooks/；
- HookNode 按 Plan → P1 → P2 → P3 运行；
- 整个 HookNode 共用一份 config snapshot 和一个 HookPlan；
- runtime 的具体调用实现独占串行、并行、内部排序及合并语义；
- Hook Port 是 `node.py` 内部薄层，不存在外部 HookPort SPI；
- Hook 契约没有 activation context、cycle、PlanMarker 或执行引擎坐标；
- 节点内没有 handler 持久化、恢复或重试；
- 旧 Manager/binding 执行路径已删除；
- 针对性测试和可运行的仓库门禁通过。
