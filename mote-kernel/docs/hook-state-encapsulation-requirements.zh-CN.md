# Hook 状态封装改造需求

状态：待实施

## 1. 背景

当前共享 Hook 的请求模型为：

```python
HookRequest(value, state, node_id)
```

业务领域（Think、Act、Observe）的 `hook_state` 同时出现在领域业务 envelope 和通用
`HookRequest.state` 中，因此 Hook 的 Invocation、P1/P2/P3 以及 Hook admission 都可以读取
该状态。虽然现有实现通过冻结对象和相等性校验约束 Hook 不应修改状态，但“禁止修改、允许读取”
仍然暴露了不必要的能力。

目标是：Hook 只处理业务 value 和 commands，不接触领域运行状态；领域节点自己持有并恢复
`hook_state`。

## 2. 目标

1. 共享 Hook 无法读取或替换 Think、Act、Observe 的 `hook_state`。
2. Hook Invocation 的 request DTO 不包含领域状态字段。
3. Hook P1/P2/P3 只允许修改业务 value，不承担领域状态传递。
4. Think、Act、Observe 三个领域采用一致的 Hook 边界语义。
5. Graph 仍是唯一组图和执行引擎，不新增 runner、state model、registry 或兼容路径。
6. 保持现有 Hook route、commands、typed descriptor、nested Graph 和并发隔离语义。

## 3. 非目标

- 不实现持久化、恢复、checkpoint、重试、Failover 或非幂等副作用。
- 不改变 Hook 的 Plan → P1 → P2 → P3 拓扑。
- 不改变 Hook command 的解释、提交或投递职责。
- 不把领域状态迁移到全局变量、缓存、隐藏 mutable field 或第二套 state model。
- 不保留旧 `HookRequest(value, state, node_id)` 兼容构造路径。

## 4. 目标 API 形状

### 4.1 通用 Hooks contract

将通用请求改为只携带业务 value 和来源节点：

```python
@dataclass(frozen=True, slots=True)
class HookRequest(Generic[ValueT]):
    value: ValueT
    node_id: GraphNodeId | None = None
```

Invocation request 也只包含 priority config 和上述 HookRequest：

```python
@dataclass(frozen=True, slots=True)
class HookInvocationRequest(Generic[PriorityConfigT, ValueT]):
    config: PriorityConfigT
    request: HookRequest[ValueT]
```

`HookStageResult` 和 `HookResult` 只返回业务 value、commands 及最终来源 node_id。通用
Hooks contract 不再声明或校验 `state`、`hook_state`、`HookStateT`。

### 4.2 HookNode

- `_HookProgress` 只保存当前 HookRequest、Plan 和 commands。
- P1/P2/P3 传递 `HookRequest(result.value, original.node_id)`。
- Hook Invocation 永远无法通过 request 读取领域状态。
- 删除通用 Hook transition 中的 state equality 校验。
- 保留 value、command、node_id、descriptor 和 route 校验。
- `HookNode` 的公开 API 仍只有包级 `HookNode`；`HookPort` 继续是实现内部适配器。

## 5. 领域迁移要求

### 5.1 Think

ThinkRequest 继续持有 `hook_state`，但 `ThinkFrame` 不再作为 Hook value 携带 state。建议
将 ThinkFrame 改为只包含 `step`，各阶段节点通过 graph input 的 ThinkRequest 保留状态，
构造下一次 HookRequest 时由 Think 节点边界单独恢复状态。

每次 Hook 返回后：

1. exact-admit HookResult 和 ThinkFrame/step；
2. 不从 Hook value 读取 state；
3. 使用当前 activation 的 ThinkRequest.hook_state 构造下一阶段 DTO；
4. 继续保留 prompt/context/compact/inference/command 五阶段 route 语义。

Think 的 Hook state 类型 admission 仍可保留在 Think assembly，用于校验初始请求及领域
输入类型，但不再作为通用 Hook payload 字段暴露给 Hook。

### 5.2 Act

ActRequest 可以继续持有领域 `hook_state`。`ActHookEnvelope` 不应把 state 放进 Hook value；
Resolve/Authorize/Execute/Settle 节点从 ActRequest 或领域内部 envelope 保存状态，并在 Hook
返回后重新构造领域值。

ActPayloadAdmission 保留领域 state type admission，但删除通用 Hook request/result 的 state
一致性检查。Act 的 pairing、stage、identity、command 和 transition 约束必须保持不变。

### 5.3 Observe

ObserveRequest 可以继续持有领域 `hook_state`。`ObserveHookEnvelope` 不应把 state 放进 Hook
value；GetObservation/WriteObservation 节点从 ObserveRequest 或领域 frame 保存状态，并在
Hook 返回后重新构造领域值。

ObservePayloadAdmission 保留领域 state type admission，但删除通用 Hook request/result 的
state 一致性检查。Observe 的 stage、cursor、delivery、receipt、family 和 command 约束必须
保持不变。

## 6. 实施顺序

1. 修改 `hooks/contract.py`：移除通用 HookRequest/HookInvocationRequest 的 state 泛型和字段。
2. 修改 `hooks/node.py`、`hooks/port.py`：清理 progress、Invocation adapter 和 transition
   中对 state 的依赖。
3. 先迁移 Think，确保五阶段图、route、nested output 和 typed materialize 正常。
4. 迁移 Act，保持四阶段拓扑和现有领域 admission。
5. 迁移 Observe，保持双 Hook activation、cursor/receipt 和 resume 边界。
6. 删除旧测试中依赖 `request.state` 或 envelope 内 Hook state 的断言，改为领域边界测试。
7. 增加通用负例：Invocation 实现无法从 Hook request 读取 state（请求对象无该属性）。
8. 更新 Think/Hook/Act/Observe 实施与评审文档，删除旧 API 描述。

## 7. 必须保留的行为

- Hook 可以修改业务 value。
- Hook commands 顺序、重复项和终端透传语义不变。
- Hook route token 仍由 Hook completion 返回并由父图消费。
- Hook priority 失败、Invocation 异常和取消仍按现有语义传播。
- typed Invocation request/result exact admission 仍由 `invocation.py` 统一负责。
- 各领域仍使用 immutable DTO；不得引入裸字典、`Any`、隐式转换或隐藏状态。
- Think/Act/Observe 仍只能通过 Graph 执行，不创建私有 runner。

## 8. 验收标准

### 8.1 静态与 API

- `HookRequest`、`HookInvocationRequest` 的类型参数和实例属性中不存在 state/hook_state。
- Hook Invocation、HookPort、HookNode 内部无 `request.state` 或 `value.hook_state` 读取。
- `mote_kernel.hooks.__all__` 仍只导出 `HookNode`。
- 三个领域的包级公共 API 不新增兼容 alias 或第二个 runner。

### 8.2 功能测试

- Hook P1/P2/P3 可修改业务 value，且 route/node_id/commands 行为不变。
- Think 五阶段完整链路通过，五次共享 Hook activation 通过。
- Act 四阶段完整链路、授权 interrupt/resume 边界通过。
- Observe 双 Hook activation、cursor/receipt/family 约束通过。
- Think、Act、Observe 的领域 state 由各自 graph/领域边界继续正确传递。
- 并发运行之间无状态串线。
- 普通异常、Invocation admission error、取消和 nested Graph 语义不回归。

### 8.3 负例

- 旧的 `HookRequest(value, state, node_id)` 构造方式失败。
- Hook Invocation 试图访问 `request.state` 时得到明确属性不存在结果。
- Hook 返回 value 中伪造领域 state 不会被通用 Hook 接受；由领域节点边界按自身 DTO 规则拒绝。
- Think/Act/Observe 的错误领域输入、错误 step/stage、错误 route 继续 fail closed。

### 8.4 门禁命令

```text
python -m pyright src/mote_kernel/hooks src/mote_kernel/think src/mote_kernel/act src/mote_kernel/observe
python -m pytest tests/hooks tests/think tests/act tests/observe tests/test_invocation.py -q
python -m ruff check src tests
python -m ruff format --check src tests
git diff --check
```

若全仓 complexity ratchet 因并行工作树失败，必须单独报告，不得修改 ratchet 掩盖问题。

## 9. 完成定义

只有在通用 Hook 看不到领域 state、Think/Act/Observe 三者迁移完成、旧 API 不再存在、专项
测试和 Pyright 全部通过后，才可宣称本需求完成。
