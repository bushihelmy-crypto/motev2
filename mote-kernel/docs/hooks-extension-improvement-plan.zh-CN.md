# Hooks 扩展综合改进方案（Pi 风格归集，评审回改版）

> 状态：**已通过评审，Hooks-only 编码已完成**
>
> 本文依据：
>
> - [`hooks-extension-design.zh-CN.md`](./hooks-extension-design.zh-CN.md)；
> - [`hooks-extension-implementation-plan.zh-CN.md`](./hooks-extension-implementation-plan.zh-CN.md)；
> - [`architecture.zh-CN.md`](./architecture.zh-CN.md)；
> - [`hooks-extension-improvement-plan-review.zh-CN.md`](./hooks-extension-improvement-plan-review.zh-CN.md)；
> - `run_rollout/pi/packages/coding-agent/src/core/extensions/runner.ts` 中的
>   `emitContext`、`emitBeforeAgentStart`、`emitToolResult` 和
>   `emitResourcesDiscover`。

## 1. 结论

评审提出的判断全部采纳：

- Hook definition ID 的点号拼接确实会碰撞；
- 固定子图在首次编译前确实仍可被外部 builder API 改写；
- required capability 只检查 `None`，装配校验不够；
- 阶段结果和最终结果分开，有助于消除 `HookResult` 的语义混用。

本次回改删除以下内容：

- Hook-owned `policy.apply()`；
- 最终 `HookResult.state`；
- 通用 `HookCommandPolicy`、identity index 和 Kernel 级 generic merge/dedupe；
- 让 generic Hook command 在本轮直接改变 P2/P3 `request.state` 的设计。

在当前 Hooks-only 范围内，Hook 的正确最小语义是：

```text
读取一次 config snapshot
  → 生成一次 HookPlan
  → P1(current value, read-only state)
  → P2(P1 value, 同一份 read-only state)
  → P3(P2 value, 同一份 read-only state)
  → 返回 P3 value + P1/P2/P3 的有序 command delta
```

HookNode 只按 `P1 → P2 → P3` 追加 stage commands，不做去重、覆盖、apply、冲突判断或
短路。`HookStageResult.commands` 已由 invocation runtime 完成单个 priority 内的 handler
归集；外部 command owner 负责最终业务消费。Graph+State 继续按普通节点边界处理执行、
settlement、状态推进和持久化，Hooks 不复制这条路径。

本轮没有额外的 State/Execution 架构待确认项。

工程判断原则：复杂度门禁只做高召回提示，不以预设数值替代设计评审。命中项要回到
唯一真相、零负债、基础设施复用、代码直白和可维护性逐条判断；有意义的 nominal contract
不因计数而合并，无用途包装和测试专用 legacy 则应直接删除。

## 2. 所有权边界

| 能力 | Hook owner | invocation/runtime owner | Graph/State owner | 其他 owner |
| --- | --- | --- | --- | --- |
| 固定 `Plan → P1 → P2 → P3` 拓扑 | 负责 | 不负责 | 提供 Graph 底座 |  |
| P1/P2/P3 调用顺序 | 负责 |  |  |  |
| 单个 priority 内 handler 的发现、排序、串并行 |  | 负责 |  |  |
| 单个 priority 内 handler 结果合并 |  | 负责 |  |  |
| priority 之间的 value 链和 command 顺序追加 | 负责 |  |  |  |
| Hook command 的真实业务语义 |  | 可提供 stage result |  | 外部 command owner |
| `GraphRunState` 归约和统一提交 |  |  | 负责 |  |
| failover、重试、失败转移 |  |  |  | failover |
| 重启恢复 |  |  |  | state/graph |
| 主图何时挂载 Hook |  |  |  | 业务 Graph owner |

边界规则：

1. invocation 只返回一个 priority 的最终 stage result；
2. Hook 只负责三个 priority 之间的 value 链和 command 顺序追加；
3. `HookRequest.state` 在本轮只读，不在 Hook 内替换；
4. Hook 不调用 Store、不调用 `reduce_graph_run`、不维护第二个 runtime state；
5. Hook 不实现 failover、恢复、业务 Graph 集成或第二套 runner。

## 3. Pi 风格的归集原则

Pi 的关键不是某个容器，而是每种结果有明确的 owner 规则：

| Pi 实现 | 归集语义 | 本方案对应语义 |
| --- | --- | --- |
| `emitContext` | 当前结果作为下一 handler 输入 | P1/P2/P3 的 `value` 链式传递 |
| `emitBeforeAgentStart` | messages 保序追加，system prompt 链式替换 | runtime 在具体事件内处理；HookNode 只追加 stage delta |
| `emitToolResult` | 具体字段逐项 patch，后续 handler 看到前一版 | 真实 hook-specific value 由 invocation 返回完整新值 |
| `emitToolCall` / `user_bash` | first/early-stop 由事件契约显式声明 | 当前固定 Hook 禁止短路 |
| 工具/注册项汇总 | 由明确 identity 决定首个、覆盖或重命名 | 真实 command owner 自己定义 identity，不由 Kernel 猜 |

因此本轮固定：

- `value`：P1 的新值传给 P2，P2 的新值传给 P3；
- `state`：同一份只读输入贯穿 P1/P2/P3；
- `commands`：HookNode 按 `P1 → P2 → P3` 和每个 stage 返回顺序追加，保留重复；
- Hook 本轮不做去重、覆盖、apply、冲突判断或短路；
- 不用对象相等、`repr()` 或字符串 discriminator 推断 command 语义；
- 未来具体 command family 的 patch、identity 或幂等规则，由外部 command owner 定义。

Python 泛型参数不提供运行时证明。为闭合跨 binding 的 payload 边界，composition root 为
每个 Hook 注入一份 `HookPayloadAdmission`，一次声明五个具体 nominal class：config、
priority config、value、state 和 command。Plan 入口、snapshot/plan loader、invocation
envelope、stage result 以及最终 result 都复用它做 exact admission；错误 payload 在进入
下一 priority 前失败。该 contract 只校验外壳和元素的 nominal 类型，不做深层反射或业务
schema 推断，嵌套 schema 仍由具体 invocation/command owner 负责。

这保留了 Pi 的核心性质：结果规则跟着具体事件/结果 schema 走，而不是把所有结果强行
抽象成一个 Kernel reducer。

## 4. 目标契约

### 4.1 阶段结果和最终结果分开

当前 `HookResult` 同时表示“一个 priority 的返回值”和“整个 Hook 的最终输出”。建议拆成：

```text
HookStageResult
  value       # 当前 priority 完成后的 hook-specific value
  commands    # 当前 priority 已由 invocation runtime 归并好的有序 delta

HookResult
  value       # P3 后的最终 value
  commands    # P1/P2/P3 保序收集的 command delta
```

两者都不携带 `state`。两者都是 `frozen dataclass(slots=True)`，`commands` 必须是 exact
`tuple`。`HookStageResult` 由 invocation capability 返回，`HookResult` 只能由 HookNode
在 P3 后产生；runtime 不能直接伪造 final result。

如果为了减少改名仍保留一个类型名，也必须在代码注释、协议和测试中明确阶段结果与最终
结果的边界，不能把原始 tuple 称为“已经完成通用去重/应用的结果”。

### 4.2 HookRequest

```text
HookRequest
  value       # 当前阶段的 hook-specific value
  state       # owner 提供的只读输入快照，本轮不由 Hooks 推进
```

如果某类 Hook 需要“当前事件状态”随阶段变化，应把它建模为 `value` 的具体类型，并让
invocation 返回新的 value；不能在 Hooks 里用一个泛型 `StateT` 模拟第二个 authoritative
state。

### 4.3 command 语义

本轮 command 只是 typed delta：

- runtime 可以在一个 priority 内按自己的事件语义合并 handler 结果；
- HookNode 在 priority 之间只按 `P1 → P2 → P3` 做确定性、有序追加，不去重、覆盖、
  apply 或检测冲突；
- Hook 不宣称 command 已经进入 `GraphRunState`；
- 外部 command owner 取得最终 `HookResult` 后再定义业务消费契约。

## 5. 目标执行流程

```text
Plan 节点
  1. snapshot = config_source.snapshot()（一次）
  2. plan = plan_loader.load(snapshot)（一次）

P1 节点
  3. stage1 = invocation.invoke(
       HookInvocationRequest(plan.p1.config, HookRequest(input.value, input.state))
     )
  4. current_value = stage1.value
  5. commands += stage1.commands

P2 节点
  6. stage2 = invocation.invoke(
       HookInvocationRequest(plan.p2.config, HookRequest(current_value, input.state))
     )
  7. current_value = stage2.value
  8. commands += stage2.commands

P3 节点
  9. stage3 = invocation.invoke(
       HookInvocationRequest(plan.p3.config, HookRequest(current_value, input.state))
     )
 10. commands += stage3.commands
 11. return HookResult(stage3.value, commands)
```

伪代码中的 `+=` 只表达不可变 tuple 的顺序拼接；实现不得原地修改 Graph value 或
HookRequest。每个 priority 只发出一次 invocation；异常和取消直接沿当前 Graph 节点边界
传播，不在 Hooks 内重试或吞错。

## 6. 具体改进项

### 6.1 `contract.py`

- 增加 `HookStageResult`，或者等价地明确 stage/final 的 nominal 边界；
- 删除最终 `HookResult.state`；
- 保留 `HookRequest.state` 的只读语义；
- 删除通用 `HookCommandPolicy`、identity index 和 generic merge/dedupe 协议；
- 保持 frozen dataclass、具体泛型和 tuple；
- `hooks.contract` 作为 HookNode 与 invocation binding 的内部集成契约；包级公共导出仍只保留
  `HookNode`，不提供包级公共 `HookPort` SPI；`HookPort` 实现在 `hooks/port.py`，不在包级
  导出；
- invocation 返回值在 Port 边界执行 exact nominal admission；
- 由 `HookPayloadAdmission` 集中声明并复用五个具体 nominal descriptor，覆盖 request、
  snapshot、plan、stage/final result 的 value/state/config/command 元素；
- `object`、`Any`、Union 等擦除或非 nominal descriptor 在装配期拒绝；错误 payload 不进入
  下一 priority；
- 修正文档注释，去掉“already-merged”但实际未归集的表述。

### 6.2 `port.py`：Invocation 适配边界

- `HookPort` 只组装 `HookInvocationRequest`、调用 shared `Invocation` 的 strict 路径并校验
  `HookStageResult`；
- Port 保持 HookNode 的包内实现，不提供公共 `HookPort` SPI，不拥有重试、状态提交或 transport
  解析；
- 独立模块只表达文件职责边界，不建立第二条调用路径。

### 6.3 `node.py`：value/command 链

- `_HookProgress` 只保存当前 request、固定 plan 和有序 command tuple；
- P1/P2/P3 只更新当前 value，不替换 state；
- 用一个明确的内部收集步骤替代裸 `combined_commands`，确保顺序和失败边界可读；
- P3 产生唯一最终 `HookResult`；
- 不建立 command map、state reducer、跨调用缓存或隐式 registry。

### 6.4 `node.py`：沿用 Graph 的拓扑冻结边界

本轮只在构造期间完成四节点和边的全部组装，不主动调用私有 `_compile()`，不增加
`seal/finalize` 或第二套 mutation guard。

拓扑冻结沿用 `Graph.run()` 的现有规则：首次成功编译后安装 `_compiled_owner`，之后由
Graph 自己拒绝 builder mutation。构造完成到首次成功编译之间仍存在可被外部 builder API
修改的已知窗口，本轮不假装消除它。

需要验证：HookNode 可作为 nested Graph 被父图组合并由父图首次成功编译；首次成功编译/运行
后 Graph 现有 mutation guard 生效。若未来产品要求构造完成即不可修改，应另开
execution/Graph 加固任务，不在 Hooks 内复制冻结状态机。

### 6.5 `identity.py`：无歧义但不重复 version

definition ID 与 Graph version 必须各自只有一个来源：

```text
domain = "mote.hook.v1"
stage_token = {AFTER_NODE: "after_node"}[stage]
fields = (domain, parent_definition_id, node_id, stage_token)
hook_definition_id = "".join(f"{len(field)}:{field}" for field in fields)
hook_definition_version = parent_definition_version
```

- 字段顺序固定为 domain、parent definition id、node id、稳定 stage token；
- 长度单位沿用现有 identity helper 的 Unicode 字符长度，不使用字节长度；
- stage 只使用显式稳定 token，不使用 Enum 的 `auto()` 数值、`repr()` 或对象字符串；
- exact vectors 覆盖点号、冒号、Unicode、空白和已知碰撞反例；
- 不把 Graph version 重复编码进 definition ID；编码逻辑放在 `hooks/identity.py`，不新建
  `utils/common/helpers`。

### 6.6 装配校验

required capability 在构造期至少检查：

- `config_source.snapshot` 成员存在且可调用；
- `plan_loader.load` 成员存在且可调用；
- `invocation.invoke` 成员存在且可调用；
- 明显错误对象不能装配成功；
- stage result、snapshot、plan 在运行边界做 exact nominal 校验。

`runtime_checkable Protocol` 只能做浅层结构检查，不能伪装成完整的签名/泛型证明；不做
深层反射，不把静态类型检查搬到运行时。

### 6.7 `plan.py`

- 保留 `HookConfigSnapshot`、`HookPlan`、`HookPriorityPlan` 的不可变外壳；
- Plan 不增加 command reducer、handler 调度、重试或恢复字段；
- 明确这是浅层不可变包装，内部 payload 的不可变性由 config owner 保证。

## 7. 测试计划

Hook 行为测试仍只放在 `tests/hooks/`；模块/包结构断言由
`tests/architecture/test_package_structure.py` 维护。

### P0：value 和 command 归集

- P1 value 进入 P2，P2 value 进入 P3；
- P1/P2/P3 收到同一个只读 state 对象/快照；
- commands 按 P1/P2/P3 和 stage 内顺序追加，重复原样保留；
- Hook 不做去重、覆盖、apply、冲突判断或短路；
- 非法 stage result 在进入下一 priority 前失败；
- P1/P2/P3 各调用一次，成功结果不能跳过后续 priority，调用失败后后续节点不再调用；
- cancellation 原样传播。

### P0：固定拓扑

- 构造期间完成固定节点和边组装；
- HookNode 可被父 Graph 嵌套并由父图首次成功编译；
- 首次成功编译/运行后，Graph 现有 mutation guard 拒绝 builder mutation；
- 不断言首次 run 前 mutation 必须失败，也不在构造期主动安装 `CompiledGraph`；
- P1/P2/P3 不会因正常 Graph 执行而重复执行。

### P1：identity

- 已知点号碰撞反例生成不同 definition ID；
- exact vector 覆盖 ASCII、Unicode、点号、冒号和空格边界；
- definition version 只来自 Graph version，不在 ID 中重复编码；
- 父图可同时嵌入两个原本会碰撞的 Hook。

### P1：装配和 nominal boundary

- `object()` 作为每个 required capability 均在构造期失败；
- 同名但不可调用的成员在构造期失败；
- `HookPayloadAdmission` 的 descriptor 必须是具体 nominal class，拒绝 `object`、`Any`、
  Union 等擦除类型；
- 非法 snapshot、plan、stage result 在对应边界失败；
- 错误的初始 value/state、config、priority config 和 stage command element 在进入下一
  priority 前失败；
- 错误消息指出具体 capability/结果类型。

### P2：原有行为回归

- 一次调用只读取一个 snapshot、生成一个 Plan；
- 当前调用内配置更新不影响已生成 Plan；
- serial/parallel runtime 都符合同一个 invocation 接缝；
- runtime 内部 handler 不出现在 Hook 图节点或持久化坐标中；
- Hook 不创建私有 runner、第二 state 或隐藏 registry；
- Hook result 作为普通节点结果交回既有 Graph settlement，不由 Hooks 自己提交 state。

## 8. 文档改动计划

### `hooks-extension-design.zh-CN.md`

修正 owner 描述：

- runtime 负责单个 priority 内 handler 的执行和内部合并；
- HookNode 只负责 priority 之间的 value 链和 command 顺序追加；
- Hook 不做 generic state apply，不返回 `HookResult.state`；
- Graph/State 是唯一权威 state/reducer/commit owner；
- Pi 的阶段内归集由 invocation runtime 负责；Hook 本轮禁止 patch、first、short-circuit
  等语义。

### `hooks-extension-implementation-plan.zh-CN.md`

同步以下实施阶段：

1. 冻结 stage/final result 契约；
2. 实现 value 链和固定顺序 command append；
3. 沿用 Graph 首次成功编译后的拓扑冻结，不做构造期封图；
4. 修复无歧义 definition identity；
5. 加强 required capability admission；
6. 补齐 value/command/拓扑/identity/装配测试。

原有“不做 failover、恢复、业务 Graph 集成、第二 runner”的条目继续保留。

### `architecture.zh-CN.md`

本轮不为 Hook 增加第二 reducer 例外，也不修改唯一 `GraphRunState` owner 原则。
Graph+State 的既有节点执行、settlement、状态推进和持久化边界直接复用；generic Hook
commands 的业务消费不在 Hooks 内定义。

## 9. 实施顺序

### 阶段 A：契约和文档

- 先删除 `HookResult.state`、generic policy 和“Hook-owned state”描述；
- 明确 `HookStageResult` 与最终 `HookResult`；
- 明确 command 是有序 delta，Hook 不解释或应用它们；
- 明确 P1/P2/P3 固定执行，任何结果不能短路；
- 同步 design/implementation/architecture 文档。

### 阶段 B：HookNode 行为

- 改 `_HookProgress` 为 value + read-only state + ordered commands；
- P1/P2/P3 链式传 value；
- 保持 state 不变；
- P3 输出最终结果。

### 阶段 C：结构安全

- 沿用 Graph 首次成功编译后的冻结边界，不主动构造期编译；
- 替换 definition ID 编码；
- 补齐构造期 capability 检查。

### 阶段 D：验证

```text
python -m pytest tests/hooks -q
python -m ruff check src/mote_kernel/hooks tests/hooks
python -m ruff format --check src/mote_kernel/hooks tests/hooks
pyright src/mote_kernel/hooks
make check
cd /home/longert/motev2
pre-commit run --all-files
```

范围外已有错误要单独记录，不得通过修改 failover 或其他包来绕过。

## 10. 完成定义

满足以下条件，Hooks-only 版本才算过关：

- 固定子图在构造期间完成组装，冻结沿用 Graph 首次成功编译边界；本轮不承诺构造期不可修改；
- 一次调用只读取一份 snapshot、生成一份 Plan；
- P1/P2/P3 严格按序且每个只调用一次；
- 成功结果不能让后续 priority 短路，P3 才能结束子图；
- 后续 priority 看到前一 priority 的 value；
- P1/P2/P3 使用同一份只读 state；
- commands 按顺序作为 typed delta 追加，Hook 不做去重、覆盖、apply 或冲突判断；
- final result 只有 `value + ordered commands`，没有第二份 state 真相；
- definition ID 无碰撞，version 不重复编码；
- required capability 在装配期拒绝明显错误对象；
- invocation、nominal admission、异常和取消边界可测试；
- Hooks 不实现 failover、恢复、Store、第二 runner 或业务 Graph 装配；
- Hooks 专项正确性、类型、格式和构建门禁通过；仓库门禁按规则执行并准确记录范围外阻断，
  复杂度数值不作为单独否决条件。

## 11. 当前实施基线

- `python -m pytest tests/hooks -q`：57 passed，Hooks 覆盖率 100%；新增负例覆盖具体
  nominal payload admission 及下一 priority 前失败边界；
- Hooks 目录 ruff、format、pyright，以及包构建和 metadata 检查：通过；
- 全仓 `make check` 或格式门禁若被工作区既有改动阻断，不能把该阻断归因于 Hooks；复杂度
  ratchet 仅作为高召回提示，不能要求为凑数改坏清晰设计；
- 构造期主动封图属于后续 execution/Graph 加固，不属于本轮完成条件。

最终判断：按第二轮评审清单回改后，Hooks-only 实施已完成；无需另开 State/Execution
架构方案。
