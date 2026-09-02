# Hooks 扩展综合改进方案评审

状态：**历史评审：不通过（针对回改前版本）**

> 本文件只评审回改前的综合改进方案，记录其中已关闭的 state/command 所有权问题。
> 当前实施状态以 [`hooks-extension-implementation-plan-review.zh-CN.md`](./hooks-extension-implementation-plan-review.zh-CN.md)
> 和 [`hooks-extension-implementation-plan.zh-CN.md`](./hooks-extension-implementation-plan.zh-CN.md) 为准。

评审日期：2026-09-02

评审对象：[`hooks-extension-improvement-plan.zh-CN.md`](./hooks-extension-improvement-plan.zh-CN.md)

本文件只记录方案评审结论，不修改方案正文、生产代码或测试。

## 1. 结论

方案发现的三个工程问题是成立的，应当保留：

- Hook graph definition ID 的点号拼接存在真实碰撞；
- 固定 `Plan → P1 → P2 → P3` 子图在首次编译前仍能被外部 builder API 改写；
- required capability 只检查 `None`，无法在装配期拒绝明显错误的对象。

把 invocation 的阶段结果与整个 Hook 的最终结果分开，也有助于消除当前
`HookResult` 同时表示 stage delta 和 final outcome 的语义混用。

但是，方案的核心归集设计当前不能落地：

1. Hook 通过 `policy.apply()` 产生下一份 state，越过了唯一的 State owner 和
   `reduce_graph_run`；
2. 最终结果同时返回 `state` 与 `commands`，没有指定唯一提交依据，形成双真相；
3. `merge()` 在旧 command 已经应用后再替换它，无法保证最终 state 与规范化 commands
   可相互重放；
4. 当前 Graph commit 只提交封闭的 `GraphRunCommand`，generic Hook command 不会进入
   `GraphRunState`，所以“P1 command 权威更新 P2 state”在本轮范围内不可实现；
5. Pi 的实现是按具体事件分别定义 chain、append、patch、first 等规则，不存在一个跨所有
   command family 的通用 policy。方案把参考实现过度泛化成了没有真实 consumer 的 Kernel
   reducer。

因此，本轮判定为：**身份、封图和装配校验方向通过；state/command 归集方案必须回改，整份方案暂不批准进入实施。**

## 2. 评审依据

本次评审以仓库当前实现和既定原则为准：

- [`architecture.zh-CN.md`](./architecture.zh-CN.md#L10-L22) 规定 `GraphRunState` 是唯一权威
  状态，所有 Hook 变化必须进入同一个原子提交边界；
- [`architecture.zh-CN.md`](./architecture.zh-CN.md#L24-L36) 进一步规定
  `reduce_graph_run` 是唯一纯 dispatch 入口，Hook 不得维护平行快照或第二个 reducer；
- [`GraphTransition`](../src/mote_kernel/execution/family_driver.py#L102-L153) 只接受封闭的
  `GraphRunCommand`，commit 必须精确返回 Kernel 已计算出的 candidate；
- [`GraphRunCommand`](../src/mote_kernel/state/graph_state/command.py#L132-L141) 当前只包含 graph
  lifecycle、execution、settlement、resume 和 frontier command，没有 generic Hook command；
- 当前 [`HookNode`](../src/mote_kernel/hooks/node.py#L93-L123) 只链式传递 value、保持 state
  只读并按顺序收集 commands，没有第二条状态推进路径；
- 当前 [`HookRequest` / `HookResult`](../src/mote_kernel/hooks/contract.py#L25-L42) 使用具体泛型和
  冻结 tuple，没有 `Any`、裸字典或字符串判别；
- Pi 的 [`emitToolResult`](../../../run_rollout/pi/packages/coding-agent/src/core/extensions/runner.ts#L812)、
  [`emitContext`](../../../run_rollout/pi/packages/coding-agent/src/core/extensions/runner.ts#L914)、
  [`emitBeforeAgentStart`](../../../run_rollout/pi/packages/coding-agent/src/core/extensions/runner.ts#L980)
  和 [`emitResourcesDiscover`](../../../run_rollout/pi/packages/coding-agent/src/core/extensions/runner.ts#L1046)
  都由具体事件 owner 直接定义结果规则，没有通用 command reducer。

评审时运行当前 Hooks 基线：

```text
python -m pytest tests/hooks -q
18 passed
```

## 3. 已通过、应保留的内容

以下方向符合唯一真相、复用基础设施和最小设计原则：

- 保留一个 `HookNode`，继续复用 `execution.Graph`，不创建第二个 runner；
- 一次 Hook 调用只读取一个 config snapshot、只生成一个 `HookPlan`；
- P1、P2、P3 严格按序，各通过同一个 invocation 接缝调用一次；
- invocation runtime 继续独占单个 priority 内 handler 的发现、排序、串并行和内部结果处理；
- invocation、取消和普通异常直接沿 Graph 节点失败边界传播，Hooks 不重试、不吞错；
- 不把 run、superstep、activation、retry 或恢复坐标复制进 Hook 契约；
- 用结构化、带 domain/version 前缀的编码消除 definition ID 拼接碰撞；
- 固定 Hook 子图在构造完成后不允许外部修改；
- required capability 在构造期拒绝缺失或明显不可调用的能力；
- `HookConfigSnapshot` 和 `HookPlan` 只提供浅层不可变外壳，内部 payload 的不可变性仍由其
  owner 保证；
- 不在本轮加入 failover、业务 Graph 挂载、恢复协议、EventBus 或第二套持久化路径。

## 4. P0 阻断项

### 4.1 `policy.apply()` 建立了第二个状态推进 owner

方案第 3、4、5 节要求 Hook 在每个 priority 后执行：

```text
current_state = policy.apply(current_state, command)
```

并把这份 state 放入下一优先级的 `HookRequest`，最终再通过 `HookResult.state` 返回，见
[`方案第 73-95 行`](./hooks-extension-improvement-plan.zh-CN.md#L73-L95)、
[`第 108-128 行`](./hooks-extension-improvement-plan.zh-CN.md#L108-L128) 和
[`第 170-212 行`](./hooks-extension-improvement-plan.zh-CN.md#L170-L212)。

这与当前 State 所有权存在无法靠注释消除的冲突：

- 如果 `StateT` 表示 Graph/Hook/业务权威事实，它只能成为 `GraphRunState` 的字段，并通过
  `GraphRunCommand → reduce_graph_run → exact commit candidate` 推进；Hook 不能自行生成下一份；
- 如果 `StateT` 只是一次 Hook 调用内的临时变换值，它就不是 runtime state，应合并进 hook-specific
  `value`，不能再以 `state` 名义作为最终状态和提交依据；
- `_HookProgress` 是普通 Graph 节点输出，会参与 publication/frame 和恢复证据流。把它声明为
  “局部 state”并不能阻止它成为一份可恢复的平行状态投影。

方案准备向 [`architecture.zh-CN.md`](./architecture.zh-CN.md) 增加“Hook 可以通过局部 reducer
计算自己的状态”的说明，见
[`方案第 393-398 行`](./hooks-extension-improvement-plan.zh-CN.md#L393-L398)。这不是对现有原则的补充，
而是对“只有 `reduce_graph_run` 能生成下一份状态”的反向放宽，不能采用。

P0 回改要求：

- 删除 Hook-owned `policy.apply()`；
- 删除最终 `HookResult.state`；
- `HookRequest.state` 在本轮只能是只读输入，P1、P2、P3 不得自行替换它；
- 如果该值实际是 Hook 事件的当前变换结果，将它命名并建模为 `value`，由 invocation 返回更新后的
  value；
- 不修改架构文档来为第二个 reducer 开例外。

### 4.2 `state + commands` 是两份真相，当前提交边界无法闭合

方案把最终结果定义成：

```text
HookResult
  value
  state
  commands
```

但没有说明下游究竟以 `state` 还是 `commands` 作为权威提交输入，也没有要求验证：

```text
result.state == replay(initial_state, result.commands)
```

即使增加这个等式，当前 Graph commit 也无法提交它。真实的
[`GraphTransition`](../src/mote_kernel/execution/family_driver.py#L102-L153) 已经携带唯一
`GraphRunCommand` 和由 `reduce_graph_run` 计算出的 candidate；外部 commit 返回任何不同 state 都会失败。
`HookResult.commands` 只是 typed node result payload，不会自动变成 `GraphRunCommand`，也不会修改
candidate。

因此，[`方案第 89-95 行`](./hooks-extension-improvement-plan.zh-CN.md#L89-L95) 所说的“交回普通 Graph
节点提交边界”不足以让 Hook state 成为权威事实。它只会让 commit callback 同时看到 graph settlement 和一个
包含 generic commands 的节点结果，State reducer 仍然不会处理后者。

P0 必须二选一：

1. **保持本轮 Hooks-only 范围**：Hook 只返回 value 和有序 commands，不声称 commands 已进入权威
   state，也不让 P1 command 改变 P2 的 `request.state`；或
2. **扩大到 State/Execution owner**：先定义真实 Hook/业务事实字段、封闭的 `GraphRunCommand` variant、
   `reduce_graph_run` 转换、settlement/commit 接入和恢复测试，再让下一 priority 读取已确认的唯一 state。

不能在 Hooks 内用 generic `StateT` 和 policy 模拟第二种路径。

### 4.3 `merge()` 契约与算法不一致，无法维持重放不变量

方案建议的接口是：

```python
def merge(existing: CommandT, incoming: CommandT, /) -> CommandT | None: ...
```

伪代码调用的却是：

```text
policy.merge(acc.commands, command)
```

即一个地方接收单个同 identity command，另一个地方接收整个 tuple，见
[`方案第 141-168 行`](./hooks-extension-improvement-plan.zh-CN.md#L141-L168) 和
[`第 190-210 行`](./hooks-extension-improvement-plan.zh-CN.md#L190-L210)。此外还缺少：

- append-only command 如何绕过 `key()`；
- 一个 `CommandT` union 内不同 family 如何分类且避免 key 空间碰撞；
- 返回 command 表示“替换 existing”“追加 incoming”还是“追加合并结果”；
- 替换旧 command 时，旧 command 已经对 state 产生的效果如何撤销；
- policy 是 required capability 还是 optional capability，缺失时采用什么唯一规则。

更关键的是，当前顺序无法保证 state 与最终 commands 一致。设初始状态为 `S0`：

```text
接受 E：S1 = apply(S0, E)
收到同 key 的 I：M = merge(E, I)
按方案继续：S2 = apply(S1, M)
最终 commands：只保留 M
```

最终 command 序列重放得到的是 `apply(S0, M)`，一般不等于
`apply(apply(S0, E), M)`。对于累加、配额、revision-sensitive command 等类型会直接重复应用；
字段覆盖型 command 只是偶然满足相等，不能作为通用保证。

要修复这一点，必须由真实 command owner 定义一个原子的规范化/归约契约，并证明 canonical commands 与
successor state 的不变量。但当前没有真实 command family，也不能让 Hooks 成为第二个 State reducer。因此本轮
最小且正确的处理是删除通用 `HookCommandPolicy`，不要在 Kernel 中预造 merge 框架。

### 4.4 Pi 的事件特化语义不能推出通用 Kernel command policy

Pi 的实际行为是：

| Pi 方法 | 真实归集方式 |
| --- | --- |
| `emitContext` | 当前 messages 作为下一 handler 输入 |
| `emitToolResult` | `content/details/isError` 三个具体字段逐项覆盖 |
| `emitBeforeAgentStart` | message 追加，system prompt 链式替换 |
| `emitToolCall` | 遇到明确的 `block` 提前返回 |
| `emitUserBash` | 第一个非空结果返回 |
| `emitResourcesDiscover` | 三类具体 path 分别保序追加 |

这些规则由具体事件 schema 直接表达。Pi 没有先把结果变成一个无差别 command tuple，再通过
`key/apply/merge` 恢复事件语义。

Mote 当前只有 generic `ValueT/StateT/CommandT` 和一个 `AFTER_NODE` slot，没有首个真实 command family、
identity、冲突规则或 Graph state consumer。在这个阶段引入通用 policy，会把尚未出现的业务语义提前固化在
Kernel Hooks owner 中。

正确借鉴方式应是：

- hook-specific 可变换内容通过 `value` 链式传递；
- stage 内 handler 的归集继续由 invocation runtime 负责；
- priority 之间的 commands 暂按有序 delta 收集，重复是否有意义由真实 command owner 决定；
- 首个 set-like、patch 或 early-stop Hook 出现时，在该事件/command owner 中定义窄 typed 规则；
- 不用对象相等、`repr()`、裸字符串 discriminator 或 Kernel 全局 policy 猜测语义。

## 5. P1 回改项

### 5.1 固定拓扑应只有一个冻结 owner

“构造完成后禁止外部 builder 改图”是正确要求，但方案推荐的 Hook-local `construction/sealed` 标记会与
[`Graph._compiled_owner`](../src/mote_kernel/execution/facade.py#L211-L230) 形成两套可变性判断。

方案需要先选定唯一机制：

- 若复用 Graph 现有“成功编译即冻结”语义，应由 execution owner 提供一个可支持的 finalize/seal 接缝，或明确
  HookNode 如何在构造结束时通过同一机制冻结；
- 若坚持 Hooks-only 的本地封装，必须只有一个集中 mutation guard，所有 `add_node`、`add_edge`、
  `add_conditional_edge`、`add_join`、`set_outputs`、`set_resume_codec` 都走它，不能复制六套容易漂移的判断；
- 测试必须在首次 run/parent compile 前验证所有 mutation API 都失败，并确认原拓扑未发生部分写入。

不要让 `_sealed` 与 `_compiled_owner` 分别成为“是否还能改图”的两个权威答案。

### 5.2 结构化 identity 不应重复编码 Graph version

长度前缀编码方向正确，但方案示例同时把 `definition_version` 放进 encoded definition ID，并继续把同一个值传给
`Graph(..., version=...)`。Graph 当前用
`(definition_id, version)` 作为定义身份，见
[`graph validation`](../src/mote_kernel/execution/graph/validation.py#L108-L127)。重复编码 version 会制造两个必须
同步的来源，也会让 definition ID 在版本变化时无谓改变。

建议固定为：

```text
hook_definition_id = encode(
  "mote.hook.v1",
  parent_definition_id,
  node_id,
  stage,
)
hook_definition_version = parent_definition_version
```

编码 helper 留在 `hooks/identity.py`，增加 exact vector、Unicode、分隔符和已知碰撞反例测试，不新建
`utils/common/helpers`。

### 5.3 runtime-checkable Protocol 不等于完整 capability 校验

`@runtime_checkable` 只能做浅层结构存在性判断，不能验证参数签名、泛型绑定或返回值类型，也不应被写成完整的
运行时类型证明。装配边界至少要覆盖：

- 完全缺少 `snapshot/load/invoke`；
- 同名成员存在但不可调用；
- invocation 调用后返回的不是 exact `HookStageResult`；
- snapshot 和 plan 返回的不是 exact nominal value。

实现可使用窄、明确的 callable 检查和现有 nominal result admission，不做深层反射，不尝试在运行时复制静态类型
系统。异步调用最终返回值仍在 Port 边界验证。

## 6. 建议的最小目标契约

在 State/Execution 尚未出现真实 Hook command consumer 前，建议把本轮契约收敛为：

```text
HookRequest
  value
  state       # 同一次 Hook 调用内只读，不由 Hooks 推进

HookStageResult
  value       # 本 priority 完成后的 hook-specific 当前值
  commands    # 本 priority 已由 invocation owner 处理后的有序 delta

HookResult
  value       # P3 后的最终值
  commands    # P1 → P2 → P3 保序收集，不宣称已做通用去重
```

执行流程保持简单：

```text
读取一次 snapshot
  -> 生成一次 HookPlan
  -> P1(current value, read-only state)
  -> P2(P1 value, same read-only state)
  -> P3(P2 value, same read-only state)
  -> HookResult(P3 value, ordered commands)
```

如果某类 Hook 需要 patch/overwrite，它应把“当前事件值”放进 `value` 并由该 hook-specific invocation 返回新值；
如果某类 command 必须改变权威状态，则先由 State owner 增加真实 command/reducer/commit 契约，再接入 Hooks。

这并不阻止未来增加 richer Hook 语义，只是避免在没有第一个消费者时提前建立通用 reducer、identity index、
merge decision 和第二份 state。

## 7. 方案回改清单

方案作者需要完成以下修改后再提交复审：

1. 删除“Hook 通过 policy 推进 state”的结论、算法、测试和完成定义；
2. 删除最终 `HookResult.state`，明确唯一权威 State 仍只由 `reduce_graph_run` 生成；
3. 删除通用 `HookCommandPolicy`、identity index 和 generic merge/dedupe 设计；
4. 将 command 语义改为本轮保序 delta，具体去重/冲突留给首个真实 command owner；
5. 明确 P1/P2/P3 只链式传递 value，state 在本轮保持同一只读快照；
6. 删除准备加入 architecture 的“Hook local reducer”例外；
7. 保留并落细 stage/final result 分离、结构化 identity、固定拓扑和 capability 校验；
8. identity 不重复编码 Graph version；
9. 固定拓扑选择一个唯一冻结机制，并更新允许修改范围；
10. 更新 P0 测试，不再使用 `Counter` 模拟第二份 state reducer；补充 value chain、ordered commands、
    全 builder 封锁、identity exact vector 和非法 capability 测试；
11. 如果产品明确要求 P1 command 权威影响 P2 state，则另起跨 State/Execution owner 的设计，不得在本方案中
    用 generic policy 绕过提交边界。

## 8. 复审门槛

满足以下条件后，方案才可批准实施：

- 文档不再定义 Hook-owned state reducer 或平行 state snapshot；
- final result 只有一个事实来源，不同时输出互相可能不一致的 state 与 commands；
- P1/P2/P3 的 value、只读 state 和 command 传递语义明确且可由当前 Graph 实现；
- 不再把 Pi 的事件特化归集误写为通用 Kernel policy；
- 固定拓扑只有一个冻结 owner；
- definition identity 与 version 各有唯一表示；
- required capability 和 nominal result 的失败边界可测试；
- Hooks 仍不拥有 failover、恢复、Store、第二个 runner 或业务 Graph 装配；
- Hooks 专项测试、ruff、format、pyright、`make check` 和 monorepo pre-commit 按仓库规则执行，范围外阻断精确记录。

当前最终判定：**退回修改，暂不实施。**
