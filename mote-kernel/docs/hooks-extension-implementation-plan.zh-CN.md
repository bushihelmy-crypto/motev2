# Hooks 扩展开发实施计划（已批准并完成实施）

> 状态：已通过 [`hooks-extension-implementation-plan-review.zh-CN.md`](./hooks-extension-implementation-plan-review.zh-CN.md)，
> Hooks-only 编码已完成；本文件是实施执行稿，评审文件只记录意见，不修改生产代码。

本计划以以下文档为基线：

- [Hooks 扩展设计共识](./hooks-extension-design.zh-CN.md)；
- [Hooks 扩展综合改进方案](./hooks-extension-improvement-plan.zh-CN.md)；
- [综合改进方案评审](./hooks-extension-improvement-plan-review.zh-CN.md)；
- [本实施计划评审](./hooks-extension-implementation-plan-review.zh-CN.md)；
- [架构](./architecture.zh-CN.md)。

目标是交付一个 Hooks-only 的最小实现：固定子图、一次 snapshot/Plan、P1/P2/P3 的
value 链和有序 command delta。Hook 仍负责固定子图的定义/编排、阶段间的 value 链和有序
delta 收集；priority 内 handler 的发现、执行和归集由 invocation runtime 负责。实际节点
运行继续复用 Graph engine。这里禁止的是在 Hooks 包中另造 `GraphRunState` owner、通用
command reducer 或第二套执行器。

## 0. 当前结论

本轮评审已经确认以下规则和工程项：

- 修复 Hook definition ID 碰撞；
- 固定子图继续复用 Graph 的懒编译；首次成功编译后由 Graph 冻结；
- 在装配期拒绝明显错误的 required capability；
- 分开 priority stage result 和 Hook final result；
- `Plan → P1 → P2 → P3` 固定顺序，任何 command 或结果都不能短路。
- 由 composition root 注入一份 `HookPayloadAdmission`，在所有 Hook payload 边界执行具体
  nominal admission；错误 payload 不得进入下一 priority。

这里的“禁止短路”是指成功返回不能跳过后续 priority；异常、取消或非法结果按当前图节点
失败边界传播，不把失败误写成另一种 command 语义。

本计划直接按现有 Graph+State 边界实施：

- `HookRequest.state` 是 owner 提供的只读输入；
- P1/P2/P3 使用同一份 state；
- Hook 链式传递 hook-specific `value`，只按 `P1 → P2 → P3` 追加 stage commands；
- `HookStageResult.commands` 已由 invocation runtime 完成单个 priority 内的 handler 归集；
- 本轮不做 Hook 层去重、覆盖、apply、冲突判断或短路；
- Graph+State 只按既有节点边界负责执行、settlement、状态推进和持久化；generic Hook
  commands 的业务消费由外部 command owner 负责。

## 1. 目标与改动边界

### 1.1 本轮目标

- 用唯一的 `HookNode` 表达固定 `Plan → P1 → P2 → P3` 子图；
- 每次进入 HookNode 只读取一次 config snapshot，并只生成一次 HookPlan；
- P1、P2、P3 共用同一份 HookPlan；
- 每个 priority 通过同一个内部薄 Port 发出一次 invocation；
- invocation 返回一个 typed `HookStageResult`；
- `HookPayloadAdmission` 一次声明 `ConfigT`、`PriorityConfigT`、`ValueT`、`StateT` 和
  `CommandT` 的具体 nominal class，并由 Plan/Port/final-result 边界复用；
- P1 的 value 传给 P2，P2 的 value 传给 P3；
- P1/P2/P3 的 commands 仅按固定顺序追加为 typed delta；Hook 不做去重、覆盖、apply、
  冲突判断或短路；
- 由 HookNode 产生唯一的最终 `HookResult(value, ordered_commands)`；
- 固定子图在构造期间完成组装，冻结沿用 Graph 首次成功编译边界；
- definition identity 使用无歧义的结构化编码，Graph version 只保留一个来源；
- required capability 在装配期拒绝明显错误对象；
- 为以上行为和边界补齐确定性测试。

### 1.2 允许修改的范围

```text
docs/hooks-extension-design.zh-CN.md
docs/hooks-extension-implementation-plan.zh-CN.md
docs/hooks-extension-improvement-plan.zh-CN.md
src/mote_kernel/hooks/**
src/mote_kernel/invocation.py
tests/hooks/**
tests/architecture/test_package_structure.py
```

本轮不修改 `execution`、`state`、`failover`、`observe`、`role`、`loop`、`events`、
`think`、`act` 或其他生产目录。不在构造期主动调用私有 `_compile()`，也不增加 Hooks
自己的 `seal/finalize` 或 mutation guard；构造期冻结属于后续 execution/Graph 加固。

### 1.3 明确不在本轮实现

- Observe 配置更新、订阅或热加载注册表；
- 主图路由、HookSlot 挂载和业务 Graph 集成；
- `GraphRunState` schema、reducer、提交、持久化或恢复的改造；现有 Graph+State 节点边界直接复用；
- generic Hook command 的应用、去重、冲突处理和业务消费；
- Hook 自己调用 Store 或另建状态提交路径；
- failover、重试、降级和失败转移；
- handler 级 settlement、恢复和持久化；
- EventBus、outbox 或第二套存储；
- 第二套图执行器或 Hooks 私有 runner；
- Rust/FFI/wire schema 的预实现。

这些机制只是外部前提，不是本计划的阶段任务。

## 2. 当前基线与调整项

| 文件/类型 | 当前问题 | 本轮处理 |
| --- | --- | --- |
| `identity.py` | definition ID 用点号拼接，存在真实碰撞 | 改为长度前缀/结构化编码；不重复编码 Graph version |
| `contract.py` | `HookResult` 同时表示 stage delta 和 final outcome；Python 泛型运行时会擦除 | 增加/明确 `HookStageResult` 与 final `HookResult` 边界；由一份 `HookPayloadAdmission` 做具体 nominal admission；不增加 `state` |
| `contract.py` | command 只有泛型 tuple，没有通用语义 | 保留 typed、有序 delta；删除 generic policy/identity index |
| `port.py` | `HookPort` 与 HookNode 拓扑混在同一个模块 | 将 `HookPort` 单独放入 `hooks/port.py`；保持一次 strict invocation 适配，不纳入包级公共 API |
| `node.py` | 固定 Graph 在首次 compile 前可被外部改写 | 本轮接受该边界；沿用 Graph 首次成功编译后的冻结，不主动构造期封图 |
| `node.py` | 只检查 capability 是否为 `None` | 构造期检查必需成员存在且可调用 |
| `node.py` | 只拼接结果但语义不够显式 | 提取清楚的 value 链和固定顺序 command append 步骤 |
| `plan.py` | 只需提供浅不可变 snapshot/Plan | 保持职责，不加入 reducer、调度、重试或恢复字段 |
| `tests/hooks` | 缺少上述负例 | 增加归集、拓扑、identity、装配和 nominal boundary 测试 |

旧的 `HookManager`、binding、generation、activation context、cycle、PlanMarker 和
兼容别名继续删除，不恢复第二执行路径。

## 3. 目标契约

### 3.1 HookRequest

```text
HookRequest
  value       # 当前 priority 的 hook-specific value
  state       # owner 提供的只读输入快照；Hook 不自行修改或推进
```

如果某个具体 Hook 需要让“当前事件状态”随 priority 变化，应把该状态放入具体的
`value` 类型，并由 invocation 返回新的 value；Hook 不另建 state reducer。

### 3.2 HookStageResult

```text
HookStageResult
  value       # 当前 priority 完成后的 value
  commands    # invocation runtime 已归集的当前 priority 有序 typed delta
```

每个 priority 只返回一个 stage result。handler 的串行、并行、排序、事件特化合并以及
必要的 Pi 风格语义都由 invocation runtime 在返回前完成；Hook 不解释这些内部细节。

`HookStageResult` 与 `HookResult` 都是 `frozen dataclass(slots=True)`，`commands` 必须是
exact `tuple`。`HookPort` 在 invocation 返回处做 exact nominal 校验；结果中不携带
priority、run、superstep、activation、retry、command policy、identity 或 state 字段。
runtime 只能返回 `HookStageResult`，最终 `HookResult` 只能由 P3 节点构造。

Python 泛型参数在运行时不可见，因此不能把 `HookStageResult[str, Patch]` 当成运行时证明。
composition root 必须为一个 Hook 注入同一份 `HookPayloadAdmission`，明确提供五个具体
nominal class：`config_type`、`priority_config_type`、`value_type`、`state_type` 和
`command_type`。这份 contract 在以下边界统一复用：

- Plan 入口的 `HookRequest.value/state`；
- config source 返回的 `HookConfigSnapshot.config`；
- plan loader 返回的三个 `HookPriorityPlan.config`；
- Port 组装的 `HookInvocationRequest` 及 invocation 返回的 `HookStageResult.value`；
- stage/final result 的 exact `tuple` commands 及每一个 command 元素。

Admission 复用现有 `canonical_nominal_type`，只接受具体 class 并按 `type(value) is
declared_type` 做 exact 检查；`object`、`Any`、Union 等擦除/非 nominal descriptor 在装配期
拒绝。不做深层反射或通用 schema 推断；嵌套 payload 的业务 schema 仍由具体 owner/adaptor
负责。这样既闭合跨 Python/Rust binding 的外壳类型漏洞，又不建立第二套 validator owner。

### 3.3 HookResult

```text
HookResult
  value       # P3 完成后的最终 value
  commands    # P1 → P2 → P3 保序收集的 typed delta
```

最终结果不携带第二份 `state`。HookNode 只把各 priority 的 commands 按 `P1 → P2 → P3`
顺序追加；不去重、不覆盖、不 apply、不检测冲突，也不宣称 commands 已进入
`GraphRunState`。外部 command owner 如何消费最终结果，不属于本计划。

`mote_kernel.invocation` 提供 Kernel domain 共用的最小 `Invocation[RequestT, ResultT]`
协议。`hooks.contract` 定义 `HookRequest`、`HookInvocationRequest`、`HookStageResult`、
`HookResult` 及配置/Plan 契约；`mote_kernel.hooks` 包级公共导出仍只保留 `HookNode`，
本轮不提供外部 `HookPort` SPI 或第二套 result DTO。

### 3.4 Pi 风格的归集规则

Pi 的可复用原则只用于说明 runtime 的阶段内归集，不在 Kernel Hooks 中预造通用 policy。
本轮固定以下最小规则：

| 内容 | 本轮规则 |
| --- | --- |
| `value` | P1 → P2 → P3 链式替换，后一步看到前一步结果 |
| `state` | 同一只读输入贯穿三个 priority；Hook 不自行推进 |
| `commands` | HookNode 按 priority 和 stage 内顺序追加，保留重复 |
| patch/set | 本轮不由 Hook 解释；未来由真实 command owner 定义窄契约 |
| first/short-circuit | 本固定子图明确禁止；任何 command 或结果都不能跳过 P2/P3，不能作为 Hook 可选规则 |

本轮固定顺序，不存在 Hook 层的隐式短路；不能用对象相等、`repr()`、裸字符串
discriminator 或未经定义的 `set` 推断 command 语义。

## 4. 目标运行流程

```text
进入 HookNode
→ Plan 节点读取一次 config snapshot
→ Plan 节点生成一次 HookPlan
→ P1 使用 plan.p1 + (input.value, input.state) 调用一次 invocation
→ 追加 P1 的 value 和 commands
→ P2 使用 plan.p2 + (P1.value, input.state) 调用一次 invocation
→ 追加 P2 的 value 和 commands
→ P3 使用 plan.p3 + (P2.value, input.state) 调用一次 invocation
→ 追加 P3 的 value 和 commands
→ 返回 HookResult(P3.value, ordered_commands)
```

每个 priority 的调用边界：

```text
HookPort.execute(priority_plan, request)
→ invocation.invoke(HookInvocationRequest(priority_plan.config, request))
→ exact 校验 HookStageResult
→ 返回给 HookNode 做固定顺序追加
```

异常、取消和非法 stage result 在当前边界失败；Hooks 不重试、不吞错、不写 Store。

## 5. 分阶段实施

### 阶段 0：删除越界设计并冻结契约

任务：

- 删除 Hook-owned `policy.apply()`、`HookResult.state` 和 generic `HookCommandPolicy`；
- 删除 generic command identity index 和通用 merge/dedupe 假设；
- 明确 stage result/final result 的 nominal 类型边界；
- 明确 `HookRequest.state` 只读且贯穿 P1/P2/P3；
- 明确 HookNode 只按 P1 → P2 → P3 追加 stage commands，不做去重、覆盖、apply、冲突
  判断或短路；
- 明确 invocation runtime 在返回 stage result 前完成 priority 内 handler 归集；
- 保留不做 failover、恢复、主图集成和第二 runner 的边界。

验收：

- 文档和代码中不再出现 Hook-owned state reducer；
- final result 没有 `state` 字段；
- 没有第二个 state owner、identity index、通用 command policy 或第二条提交路径；
- 不引入 `GraphRunCommand` 别名、执行坐标或兼容 API。

### 阶段 1：最小身份和调用契约

文件：

```text
src/mote_kernel/hooks/identity.py
src/mote_kernel/hooks/contract.py
src/mote_kernel/invocation.py
```

任务：

- 保留 `HookSlotId`、`HookStage`、`HookPriority`；
- 增加/明确 `HookStageResult`；
- 让最终 `HookResult` 只表达 value 和有序 commands；
- 在 `mote_kernel.invocation` 定义共用的 `Invocation` 协议，在 `hooks.contract` 定义
  Hook-specific request/result 契约；
- invocation capability 返回 `HookStageResult`，最终 `HookResult` 只能由 HookNode 的 P3 节点构造；
- composition root 必须注入同一份 `HookPayloadAdmission`；Port 在调用前后和最终结果边界
  复用它做 exact nominal admission；
- required invocation/config/plan capability 使用窄 typed 接缝；
- 不加入 handler 列表、执行模式、retry/recovery、activation 坐标或 state reducer。

验收测试：

- 非法 stage result 在进入下一 priority 前失败；
- `HookRequest.state` 可被三个 priority 读取且身份不变；
- 契约没有 `Any`、裸字典、字符串判别或 Graph 坐标；
- `HookStageResult` 与 `HookResult` 都是 frozen、slots、exact tuple 的 nominal 类型；
- `mote_kernel.hooks` 包级公共导出仍只有 `HookNode`；
- 明显错误的 required capability 在构造期失败。

### 阶段 2：配置快照和动态 Plan

文件：

```text
src/mote_kernel/hooks/plan.py
```

任务：

- 保持 `HookConfigSnapshot`、`HookPriorityPlan`、`HookPlan` 的冻结外壳；
- 一次进入只读取一次 snapshot、只生成一次 Plan；
- 三个 priority plan 来自同一 snapshot；
- Plan 不承载 command reducer、handler 调度、重试、恢复或版本 marker；
- 文档明确冻结外壳不提供任意 payload 的深冻结，payload owner 负责不可变性。

验收测试：

- snapshot/Plan 调用次数各为一次；
- 调用期间配置更新只影响下一次 HookNode；
- P1/P2/P3 使用同一 Plan 对象及对应 priority plan。

### 阶段 3：HookNode 和 value/command 链

文件：

```text
src/mote_kernel/hooks/port.py
src/mote_kernel/hooks/node.py
```

任务：

- 将 `HookPort` 放在 `hooks/port.py`，只负责组装 `HookInvocationRequest`、调用 shared
  `Invocation` 的 strict 路径并校验 `HookStageResult`；它仍是 HookNode 的私有实现，不是
  外部 SPI；
- 使用现有 `mote_kernel.execution.Graph` 组合固定四节点子图；
- `_HookProgress` 保存当前 request、固定 Plan 和有序 command tuple；
- P1/P2/P3 只替换 current value，不在 HookNode 内手工改写 state；
- 用显式内部追加步骤替换裸 tuple 拼接，严格按 P1 → P2 → P3 保留顺序和重复；
- P3 构造唯一 final `HookResult`；
- invocation 异常、取消和 nominal admission error 直接传播；
- P1/P2/P3 不允许任何 command 或结果短路；
- 不创建第二 runner、第二 state、command registry 或跨调用缓存。

验收测试：

- P1 value 成为 P2 输入，P2 value 成为 P3 输入；
- 三个 priority 收到同一只读 state；
- commands 顺序为 P1、P2、P3 及各自 stage 顺序；
- commands 重复项原样保留，不做去重、覆盖、apply 或冲突判断；
- 每个 priority 只调用一次；
- 第一个失败 priority 后不调用后续 priority；成功结果不能跳过后续 priority；
- serial/parallel runtime 都符合同一 invocation 接缝。

### 阶段 4：沿用 Graph 的拓扑冻结边界

本轮只在构造期间完成 `Plan → P1 → P2 → P3` 的节点、边和 outputs 组装，不主动调用
私有 `_compile()`，不增加 `seal/finalize` 或第二套 mutation guard。

拓扑冻结沿用 `Graph.run()` 的现有规则：首次成功编译后安装 `_compiled_owner`，之后由
Graph 自己拒绝 builder mutation。构造完成到首次成功编译之间仍存在可被外部 builder API
修改的已知窗口，本轮不假装消除它。

必须验证：

- HookNode 能作为 nested Graph 被父图组合并沿用父图的首次成功编译；
- 首次成功编译/运行后，Graph 现有 mutation guard 生效；
- 本轮不对“首次 run 前 mutation 必须失败”作断言；
- 失败不会留下 Hook 自己的部分拓扑或状态写入（Hook 不调用 Store）。

若未来产品要求构造完成即不可修改，应另开 execution/Graph 加固任务；不得在 Hooks 内
复制冻结状态机。

### 阶段 5：结构化 identity 和装配 admission

identity：

```text
domain = "mote.hook.v1"
stage_token = {AFTER_NODE: "after_node"}[stage]
fields = (domain, parent_definition_id, node_id, stage_token)
hook_definition_id = "".join(f"{len(field)}:{field}" for field in fields)
hook_definition_version = parent_definition_version
```

- 字段顺序固定为 domain、parent definition id、node id、稳定 stage token；
- 长度单位沿用现有 identity helper 的 `len(str)` Unicode code-point 长度，不使用 UTF-8
  字节长度或视觉 grapheme 长度；
- stage 只使用显式稳定 token（例如 `after_node`），不使用 Enum 的 `auto()` 数值、`repr()`
  或对象字符串；
- 用 exact vectors 覆盖点号、冒号、Unicode、空白和已知碰撞反例；
- 不把 Graph version 重复编码进 definition ID；
- 编码逻辑只放在 `hooks/identity.py`。

代码入口固定为 `hooks.identity.hook_definition_id(slot)`；它只生成 definition ID，版本仍由
HookNode 传给 Graph 的 `version` 参数。

实施和测试必须固定以下可直接比对的向量（`hook_definition_version` 单独取父图版本，
不拼进 ID）：

| fields `(domain, parent_definition_id, node_id, stage_token)` | 期望 `hook_definition_id` |
| --- | --- |
| `("mote.hook.v1", "parent.graph", "node", "after_node")` | `12:mote.hook.v112:parent.graph4:node10:after_node` |
| `("mote.hook.v1", "a", "b.hook.c", "after_node")` | `12:mote.hook.v11:a8:b.hook.c10:after_node` |
| `("mote.hook.v1", "a.hook.b", "c", "after_node")` | `12:mote.hook.v18:a.hook.b1:c10:after_node` |
| `("mote.hook.v1", "父图:α", "节点 空", "after_node")` | `12:mote.hook.v14:父图:α4:节点 空10:after_node` |

上表第二、三行在旧的点号拼接下都会得到
`a.hook.b.hook.c.after_node`，在长度前缀编码下必须不同；第四行同时覆盖 Unicode、冒号
和内部空白。另加一个带内部空格的 ASCII 向量，确认空格按普通字符计数，首尾空白仍由
`is_canonical_identity` 拒绝。

admission：

- `snapshot`、`load`、`invoke` 成员必须存在且可调用；
- 缺失成员、`None` 和 `object()` 均在构造期拒绝；
- `HookPayloadAdmission` 的五个 descriptor 必须是具体 nominal class；`object`、`Any`、
  Union 等擦除类型在构造期拒绝；
- runtime-checkable Protocol 只做浅层检查，不伪装成签名/泛型证明；
- request、snapshot、plan、invocation envelope、stage result 和 final result 均在对应边界
  执行 exact nominal 校验；非法 value/state/config/command 不进入下一 priority；
- 错误信息指向具体 capability 或结果类型。

### 阶段 6：删除旧路径、补齐文档和门禁

- 删除旧 Manager/binding/activation 路径和兼容别名；
- `hooks/__init__.py` 保持最小公共导出；
- 同步设计文档中关于 stage/final result、command 归集、短路和 Graph 懒编译边界的表述；
- 更新测试和完成定义；
- 不为通过门禁修改范围外的 failover/state/execution 代码。

## 6. 测试矩阵

Hook 行为测试只放在 `tests/hooks/`；包结构门禁由架构测试维护。

### 6.1 归集

- value chain：P1 → P2 → P3；
- read-only state：三阶段身份相同；
- ordered commands：P1、P2、P3 及各 stage 内顺序和重复原样保留；
- Hook 不做去重、覆盖、apply、冲突判断或短路；
- 非法 stage result 不进入下一阶段；
- 成功的 P1/P2 结果不能跳过后续 priority，P3 才能结束固定子图；
- 普通异常和 cancellation 直接传播。

### 6.2 拓扑

- 构造期间完成固定节点和边组装；
- HookNode 可被父图嵌套并由父图首次成功编译；
- 首次成功编译/运行后，Graph 现有 mutation guard 拒绝 builder mutation；
- 不断言首次 run 前 mutation 必须失败，也不在构造期主动安装 `CompiledGraph`；
- 无部分拓扑写入，P1/P2/P3 不重复执行。

### 6.3 identity

- 已知点号碰撞反例生成不同 ID；
- ASCII、Unicode、点号、冒号、空白 exact vectors；
- version 只由 Graph version 提供；
- 父图可同时嵌入原本会碰撞的两个 Hook。

### 6.4 装配和边界

- `None`、缺失成员、同名但不可调用的成员以及 `object()` 均构造期失败；
- 非法 snapshot/plan/stage result 在对应边界失败；
- 错误消息不泄露 payload 内容，只指出类型/端口。

### 6.5 原有行为

- snapshot/Plan 次数和共享身份；
- 动态配置只影响下一次调用；
- runtime 内部串行/并行对 Hook 外壳不可见；
- 没有 handler 级持久化、恢复、重试或 execution 坐标；
- Hook result 作为普通节点结果交回既有 Graph settlement，不由 Hooks 自己提交 state。

## 7. 外部边界

以下内容只作为测试替身或文档前提，不在本轮实现：

### 7.1 Observe

Observe 更新配置；HookNode 入口读取一次并固定 snapshot。Hooks 不订阅、不轮询、不修改
Observe。

### 7.2 Failover

Failover 可以包装 Port 调用，负责重试、退避和失败转移。Hooks 不重新读取配置、不重新
生成 Plan，也不实现 failover。

### 7.3 Graph/State

Graph+State 负责普通图节点的执行、settlement、唯一 `GraphRunState`、状态推进、提交和
持久化。Hook result 作为普通 typed node output 交回既有节点边界；Graph 不解释 generic
Hook commands，外部 command owner 负责结果消费。Hook 不调用 Store，也不复制 Graph+State
的路径。

### 7.4 主图集成

HookSlot 的编译、进入条件、挂载位置和业务 value/state 投影交给业务 Graph owner。

### 7.5 Rust invocation

Rust binding 只在有真实跨语言消费者时定义 wire payload、错误和 conformance；Hooks 不复制
Python `HookRequest`/`HookResult`，不提前添加 FFI。

## 8. 约束清单

1. 生产代码只修改 `src/mote_kernel/hooks/` 和 Kernel 级通用调用契约
   `src/mote_kernel/invocation.py`；
2. Hook 行为测试只修改 `tests/hooks/`；包结构门禁同步修改
   `tests/architecture/test_package_structure.py`；
3. `mote_kernel.execution.Graph` 是唯一图组合/执行门面；
4. HookNode 只有固定 `Plan → P1 → P2 → P3` 外壳，任何 command/结果都不能短路；
5. 一次调用只读取一个 snapshot、生成一个 Plan；
6. P1/P2/P3 各只调用一次 invocation；
7. 后续 priority 只看到前一 priority 的 value 和同一只读 state；
8. HookNode 只按顺序追加 stage commands，不去重、不覆盖、不 apply、不检测冲突；
9. 不引入第二 state、第二 reducer、第二 runner、隐藏 registry 或兼容 alias；
10. 不使用 `Any`、裸字典、反射、字符串判别或 execution 坐标；
11. failover、恢复、主图集成和全局提交保持在各自 owner；
12. 复杂度门禁是高召回审查提示，不是必须追逐的数值目标；每条命中回到代码逻辑、
    owner 边界和维护成本逐项判断，不为降低指标删除有意义契约或增加包装层。

## 9. 验证和完成定义

专项门禁：

```text
python -m pytest tests/hooks -q
python -m ruff check src/mote_kernel/hooks tests/hooks
python -m ruff format --check src/mote_kernel/hooks tests/hooks
pyright src/mote_kernel/hooks
```

仓库门禁：

```text
make check
cd /home/longert/motev2
pre-commit run --all-files
```

Hooks-only 版本完成条件：

- 固定子图在构造期间完成组装，冻结沿用 Graph 首次成功编译边界；本轮不承诺构造期不可修改；
- definition ID 无歧义，Graph version 不重复编码；
- config snapshot/Plan 次数正确且 P1/P2/P3 共享 Plan；
- value 链、只读 state 和 ordered command delta 语义有确定测试；
- final result 只有 value 和 ordered commands，没有第二份 state 真相；
- P1/P2/P3 固定执行，成功结果不能短路，P3 才能结束子图；
- Hook 不解释或应用 generic commands，外部 command owner 负责后续消费；
- required capability 和 nominal result admission 在正确边界失败；
- 具体 `HookPayloadAdmission` 覆盖 value、state、config、priority config、stage value 和
  command element；错误 payload 在下一 priority 前失败；
- invocation、异常、取消和失败后停止语义稳定；
- Hooks 不拥有 failover、恢复、Store、第二 runner 或业务 Graph 集成；
- 正确性、类型、格式、构建等权威门禁通过，范围外阻断精确记录；复杂度报告逐条审阅，
  最终以唯一真相、零负债、基础设施复用和代码简洁易维护为准，而不是机械满足 ratchet 数值。

## 10. 后续复审材料（如扩展到其他 owner）

如果未来把范围扩展到 State/Execution 或真实 command owner，再提交复审时应同时提供：

- 本文件和 `hooks-extension-design.zh-CN.md` 的差异；
- 新增测试名称及覆盖的 value/command/拓扑/identity/装配边界；
- HookNode nested Graph 由父图首次成功编译并冻结的实测结果；
- 首次成功编译/运行后的 Graph mutation guard 实测结果；
- Hooks 专项门禁输出；
- Graph+State 普通节点跳转后的状态推进/持久化实测结果（不把 generic Hook command 语义归给 Graph）。

这些材料不是当前 Hooks-only 完成条件。本计划没有额外的 State/Execution 架构待确认项；
Hook 直接复用现有 Graph+State 节点边界。

当前验证记录：

- Hooks 专项：36 passed，覆盖率 100%；ruff、format、pyright、构建和 metadata 检查通过；
- 全量 pytest：1135 passed，3 failures；其中 complexity ratchet 测试 2 项（工作区整体高召回
  指标，包含有意保留的 stage/final nominal contract），另 1 项是 `failover/policy.py` 的
  generic integrity；这些不构成 Hooks 正确性阻断；
- `make check`：被工作区既有 5 个非 Hooks 文件的 format 差异阻断；单独运行的 complexity
  ratchet 也提示同一工作区范围外问题；未为 Hooks 任务修改这些 owner 的文件；
- monorepo 针对 Hooks 文件的 pre-commit：基础检查、ruff、format、detect-secrets 通过，
  仅高召回 complexity ratchet 提示失败；该提示按本计划不替代代码逻辑和可维护性评审。
