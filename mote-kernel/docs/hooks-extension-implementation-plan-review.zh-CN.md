# Hooks 扩展实现综合代码评审

状态：**通过（Hooks-only；泛型 payload admission 已整改）**

> 本文保留初始审计的证据和外部边界记录；“不通过”只针对 admission 回改前的版本。
> 当前复审结论见第 10 节，代码和测试以当前工作树为准。

评审日期：2026-09-02

评审对象：

- `src/mote_kernel/hooks/`
- `tests/hooks/`
- 与 Hooks 契约直接相关的设计/实施文档

评审原则：0 负债、唯一真相、复用基础设施、代码直白清晰。测试通过只说明当前测试
覆盖的行为成立，不等于所有边界都已经闭合。

## 1. 最终结论

初始审计发现的唯一 Hooks-only 阻断——泛型 payload 在运行时可能穿透——已经通过一份
具体的 `HookPayloadAdmission` contract 解决。当前 Hook **通过 Hooks-only 复审**；command
归集和 state 所有权仍按已拍板的最小契约执行，没有被 admission 回改改变。

已经成立的部分：

- `Plan → P1 → P2 → P3` 的固定顺序和 value 链成立；
- 一次调用只读取一次 config snapshot、只生成一次 plan；
- P1/P2/P3 使用同一份 plan 和同一份只读 state 输入；
- 普通异常、取消和非法 stage result 会停止后续 priority；
- definition ID 的长度前缀编码已经消除了已知点号碰撞；
- Hook 能复用现有 `execution.Graph` 作为 nested graph；
- `HookNode.slot` 当前已经是只读 property，公开 slot 与构造时 identity 不再能通过正常 API
  分裂；
- `mote_kernel.invocation.Invocation` 作为 Python 侧适配接缝是合理的：实际 invocation
  机制由 Rust `mote-infra/invocation` 提供，Hook 只依赖这个窄协议；
- command 只在 P3 形成最终 `HookResult`，当前没有 stage command delivery API。

已完成的整改：

1. `HookPayloadAdmission` 在 composition root 明确声明 `config`、`priority config`、
   `value`、`state` 和 `command` 五个具体 nominal class；Plan、Port 和最终结果边界统一
   复用，错误 payload 不再穿透；
2. 新增错误 value/state、snapshot config、priority config、stage value/command、错误
   wrapper 和擦除 descriptor 的负例，并验证非法结果不会进入下一 priority。

仍需记录但不阻断本轮 Hooks-only 的外部边界：

3. 首次 Graph compile 前，继承的 builder API 仍可以改写固定子图；
4. `HookSlotId` 与实际挂载 parent/node 的交叉校验属于 assembly/Graph owner；
5. 当前 priority 由各自 `HookPriorityPlan.config` 完整表达，暂不另增 invocation 字段；
6. Rust binding 尚无本仓库 Hooks-only 消费者，Python `Invocation` 只保留窄适配接缝，真实
   跨语言映射待对应 infra consumer 出现后由其 owner 验收。

本评审不把 failover、重启恢复、GraphRunState schema、主图挂载或 Store 实现塞回 Hooks。
这些边界继续由各自 owner 负责。

## 2. 已拍板的最终契约

本节覆盖历史评审中相反的草案意见：不再把 Hook-owned apply/去重、每个 stage 的 command
对外交付，或“P1 command 必须进入 P2”当作本 Hook 的要求或阻断项。

### 2.1 Hook 的运行链

```text
读取一次 config snapshot
  → 生成一次 HookPlan
  → P1(current value, 同一份只读 state)
  → P2(P1.value, 同一份只读 state)
  → P3(P2.value, 同一份只读 state)
  → 返回 P3.value + P1/P2/P3 的有序 command delta
```

P1 的 command **不直接传给 P2**。P2 只看 P1 返回的 `value` 和原始只读 `state`。
如果某个具体 Hook 的 patch 必须影响 P2，具体 invocation/runtime 应先把 patch 应用到
Hook-specific value，再返回新的 `value`；通用 HookNode 不解释 `CommandT`，也不把它变成
第二份 state。

### 2.2 command 归集规则

- `HookStageResult.commands` 是当前 priority 的 typed delta；
- HookNode 只按 `P1 → P2 → P3` 顺序追加到最终结果；
- 重复 command 原样保留；
- 当前没有 stage command delivery API，Graph 内部 settlement/frame 不是 Hook command
  交付契约；
- Hook 不做 command 的 apply、去重、冲突判断、持久化或权威 state 更新；
- command 的业务消费、幂等和冲突规则由外部 command owner 定义；
- `HookResult` 只在 P3 生成，外部只能把它作为一次最终 typed node output 消费。

因此，当前的 `progress.commands + result.commands` 是“最终有序 delta 收集”，不是重复
交付。不能因为 Graph 内部每个节点都有 settlement，就推导出 P1 command 会被对外交付三次。

`tests/hooks/test_hooks.py` 中的
`test_hook_preserves_stage_command_order_and_duplicates` 是有意义的契约测试，必须保留；
不能为了引入通用幂等 policy 删除它。

### 2.3 Rust invocation 与 Python 适配接缝

`mote-infra/invocation` 的实际通信/执行机制由 Rust 提供。Kernel 侧必须保留一个轻量的
Python `Invocation` 适配协议，供 Hook、Think、Act、Context 等 Port 复用；这不是第二个
invocation owner，也不是要求把 Rust 类型泄漏进 Hook。

建议的唯一最小契约：

```python
class Invocation(Protocol[RequestT_contra, ResultT_co]):
    async def invoke(self, request: RequestT_contra, /) -> ResultT_co: ...
```

这里的“一份”指一份**协议**，不是一个万能 request/result，也不是一个全局单例。

- Rust invocation 负责实际 transport、resolution 和执行机制；Python 适配层只暴露统一的
  `Invocation` 形状；
- Hook 继续拥有 `HookRequest`、`HookStageResult`、`HookResult`；
- Hook 把 priority config 和 `HookRequest` 组装成自己的 typed request，再调用公共
  `Invocation`；
- Think、Act、Context 等 Port 各自拥有 request/result，但复用同一个 Invocation 形状；
- local/RPC 是 Invocation 的实现选择，不改变 Port 的业务类型；
- Invocation 不负责 retry、failover、command 语义、状态提交或 Graph 推进；
- 日志和 Observability 当前是同步旁路能力，不要为了“统一”强行变成异步 Invocation；
- Python adapter 必须把一次调用明确委托给 Rust binding，不能在 Kernel 再实现一套 retry、
  transport、resolver 或 command 语义。

当前把 `Invocation` 放在 `mote_kernel.invocation` 作为 Python adapter seam 不构成问题；
复审时要验证它确实接到 Rust binding，而不是出现一个平行的纯 Python invocation 实现。

## 3. 历史问题与复审状态

### 3.1 Invocation 适配层（方向正确，集成时需验收）

当前代码已经删除了 `hooks.contract.HookInvocation`，并用一个两参数泛型协议承载 Python
适配形状；该协议定义在 [`src/mote_kernel/invocation/contract.py:1-15`](../src/mote_kernel/invocation/contract.py:1)，
由 `HookNode` 导入（[`node.py:19`](../src/mote_kernel/hooks/node.py:19)）。在 Rust
基础设施方案下，这个位置是合理的 Python 接缝，不应把它误判为重复 owner。

当前 Hook-specific envelope 是：

```text
invoke(HookInvocationRequest) -> HookStageResult
```

验收要求（不是要在 Hooks 内重做 infra）：

- 保留 Hook 自己的 `HookInvocationRequest`，由 Python adapter 实现唯一
  `Invocation[RequestT, ResultT]`；
- 公共方法只收一个 typed request，Hook 的 config 放进 Hook 自己的 request envelope；
- `_HookPort` 只负责组装 request、调用公共 Invocation、校验 Hook stage result；
- Rust binding 负责编码/解码和实际调用，不能把 Hook 类型反向放进 Rust，也不能把 transport
  DTO 暴露给 Hook；
- failover 若需要“单次调用”保证，只包裹 Invocation，不把 retry 语义塞进 Invocation。

### 3.2 泛型 payload admission（已解决）

回改前确实存在运行时穿透：Python 泛型参数会擦除，单检查 `HookRequest`/`HookStageResult`
外壳不能证明内部 value、state、config 或 command 元素。当前实现已在 Hook contract 中
增加唯一的 `HookPayloadAdmission`，由 composition root 一次提供五个具体 nominal class：

```text
HookPayloadAdmission
  config_type
  priority_config_type
  value_type
  state_type
  command_type
```

同一份 admission 在以下边界复用：

- Plan 入口检查 `HookRequest` 的 `value/state`；
- config source 检查 `HookConfigSnapshot.config`；
- plan loader 检查 `HookPlan` 及 P1/P2/P3 的 `HookPriorityPlan.config`；
- Port 组装并检查 `HookInvocationRequest`，再检查 invocation 返回的 exact
  `HookStageResult`、value、commands tuple 和每个 command 元素；
- P3 构造 `HookResult` 后再次检查最终 value、commands tuple 和 command 元素。

descriptor 复用现有 `canonical_nominal_type`，装配期拒绝 `object`、`Any`、Union 等擦除或
非 nominal 类型；payload 使用 `type(payload) is declared_type` 的 exact 规则，与 Graph
现有值 admission 对齐。错误 payload 在进入下一 priority 前抛出 `HookContractError`，后续
priority 不会被调用。

这解决的是跨 Python/Rust binding 的外壳和元素类型 admission，不是深层业务 schema 验证。
嵌套 payload 的 schema 仍由具体 invocation/command owner 负责；没有引入反射、`Any`、裸
dict、字符串 discriminator、deep-copy 或第二套 validator owner。

负例已覆盖：错误初始 value/state、snapshot config、三个 priority config、stage value、
stage command element、错误 nominal wrapper、malformed result，以及 `object` descriptor。

### 3.3 wrapper 冻结不是本轮问题

当前 `HookRequest`、snapshot、Plan、stage/final result 使用浅层 `frozen` 外壳，可以保留
作为结构字段的误改保护，也可以在确认没有调用方依赖后统一去掉；两者都不改变本轮的
snapshot 语义。配置更新是否在一次 Hook 调用中可见，取决于“只读取一次并固定该 snapshot”，
不取决于 `frozen=True`。

本轮不要求 deep-freeze、`deepcopy` 或只读代理，也不把“payload 可能可变”列为 Hook 阻断项。
若后续选择移除外壳冻结，应同步移除仅验证 `FrozenInstanceError` 的测试和文档表述，并保持
P1 → P2 → P3 的 value/command 规则不变。

HookNode 本身没有 run-local mutable field，这一点应保留；注入的 Invocation、source 和
loader 必须可并发复用或由 composition root 按调用隔离。当前 `slot` 只读测试已经存在
（[`tests/hooks/test_hooks.py:944-952`](../tests/hooks/test_hooks.py:944)），不再是整改项。

## 4. 已记录的外部边界（非本轮 Hooks-only 阻断）

### 4.1 首次 compile 前仍可改写固定子图

`HookNode` 继承 `Graph` 的 builder API。首次成功 compile 前，外部仍可以调用
`add_node`、`add_edge`、`set_outputs`、`set_resume_codec` 等方法，污染原本应固定的
`Plan → P1 → P2 → P3` 子图。

当前 Graph 的真实规则是“首次成功编译后冻结”，不是“Hook 构造结束即冻结”。本轮明确
接受这个窗口，不把它伪装成已经解决；因此：

- 如果固定子图是硬安全要求，应由 execution/Graph owner 提供构造期 seal 或不可变组合
  API；不要在 Hooks 内复制一套 mutation guard；
- 如果本轮不扩大 execution 范围，必须把这个窗口明确记为已知边界，不能在完成定义中写成
  已经固定；
- 首次成功 compile/run 后的现有 Graph mutation guard 必须继续通过。

### 4.2 HookSlotId 与实际挂载位置没有校验（assembly owner 责任）

`HookSlotId` 声称自己对应某个 parent definition/node，但 `HookNode` 被加入父图时没有
验证 slot 是否真的匹配该 parent 和 node。错误装配可能产生“运行位置是 A、身份声明是 B”
的记录。

这不是本轮 Hooks-only 的阻断项。后续由 assembly/Graph owner 选择以下唯一做法之一：

- 最好由 parent/assembly factory 根据实际 definition、node 和 stage 生成 slot；或
- 由 Graph/assembly owner 在挂载时校验 `slot.definition_id` 与 parent、`slot.node_id` 与
  mount node 一致；
- 如果当前 Graph API 无法做此校验，必须把它记录为 assembly owner 的 required invariant，
  不能把“调用方自觉传对”当成唯一真相。

### 4.3 priority 身份由 priority config 表达

当前 invocation request 携带的是 Hook-specific `HookInvocationRequest`，其中的 config
就是当前 priority 的 `HookPriorityPlan.config`。本轮约定并测试该 config 完整表达
priority（示例中的 `rank`），因此不再向公共 `Invocation` 或 request 额外塞入一个重复的
`HookPriority` 字段。

如果未来出现不能由 config 完整表达 priority 的真实 Hook，应由该 Hook owner 先扩展自己
的 typed request；不得让 runtime 偷看 `repr`、对象身份或引入 Kernel 全局 discriminator。

## 5. 不要误改的现有行为

以下行为是最终契约，整改时必须保留：

- P1/P2/P3 严格按序执行；
- P1/P2/P3 只链式传递 value，state 始终是同一份只读输入；
- commands 按 P1、P2、P3 和各 stage 内顺序追加；
- commands 的重复项原样保留；
- P1 command 不自动进入 P2，除非具体 invocation/runtime 已把它反映到返回 value；
- HookNode 不解释 `CommandT`，不做通用 apply、去重、冲突判断或持久化；
- HookResult 只在 P3 生成，Graph settlement/frame 不等于 command delivery；
- 不新增 Hook-owned state reducer、command identity index 或第二条提交路径；
- 不把 failover、恢复、GraphRunState 或 Store 逻辑塞回 Hook。

## 6. 测试整改与复审证据

### 6.1 已完成的新增与改写

- 用最终 `HookResult` 断言 P1/P2/P3 的 ordered delta，明确没有 stage command delivery API；
- 保留并强化重复 command 原样保留测试；
- 测试 P1/P2/P3 的 value 链，明确 P2 输入来自 P1.value，而不是 P1.commands；
- 测试具体 invocation/runtime 若应用 patch 后返回新 value，P2 能看到该 value；
- 错误的 value、state、config、stage command 元素在相应边界失败（已完成）；
- 尝试修改 `HookNode.slot` 失败；
- slot 与实际 parent/node 不匹配时，assembly 失败或由 owner 明确拒绝；
- 公共 `Invocation` 的 request/result 接缝，以及 Hook adapter 的 exact stage result admission
  （已完成；真实 Rust binding 集成待 infra consumer）；
- 同一个 HookNode 并发运行时没有共享的 run-local accumulator 或隐藏状态；
- 如果拓扑冻结要求纳入本轮，补首次 compile 前 mutation 负例；否则只测试 compile 后
  guard，不要假装两者等价。

不要增加以下错误测试：

- 要求 P1/P2 stage 对外分别交付 command；
- 要求 P1 command 自动出现在 P2 request；
- 要求 HookNode 对 generic command 做幂等 apply、去重或冲突判断；
- 因为 command 重复而删除现有契约测试。

### 6.2 应保留的原有测试

- snapshot/plan 调用次数与同一 plan 身份；
- value 链、固定 priority 顺序、异常和 cancellation；
- nested graph 组合；
- identity exact vectors 和旧碰撞反例；
- required capability 的明显错误 admission；
- stage result 与 final result 的 nominal 区分；
- `test_hook_preserves_stage_command_order_and_duplicates`。

### 6.3 应删除或迁移的测试

- `mote_kernel.invocation.Invocation` 的 Python adapter 接口测试和测试替身保留；有真实
  Rust binding 后补一条“调用一次并正确映射结果/错误”的集成测试；
- 只为保留旧 Manager、旧 binding 或旧 invocation 名称而存在的测试，直接删除；
- 有意义的错误传播、value 链、重复保留和 identity 测试保留并迁移，不做机械删测试。

当前复审基线：`python -m pytest tests/hooks -q` 为 **57 passed**，并以
`python -m pytest --cov=mote_kernel.hooks --cov-report=term-missing tests/hooks -q` 验证
Hooks 覆盖率 100%。专项测试已证明 value/state/config/command 的 nominal admission 和
非法结果在下一 priority 前失败；Rust binding 的真实调用/错误映射仍属于 infra consumer
出现后的集成验收，不是当前 Hooks-only 阻断。

## 7. 文档同步结果

已同步的文档内容仅限公共调用 owner 相关部分：

- `hooks-extension-design.zh-CN.md` 明确 `mote_kernel.invocation.Invocation` 是 Python
  适配接缝，Rust `mote-infra/invocation` 是实际实现，不再写成旧的 `HookInvocation`；
- 实施计划和改进方案中“commands 按 P1 → P2 → P3 追加、重复保留、Hook 不 apply/去重”
  的表述是正确的，不要改成 Hook-owned policy；
- 明确 P2 只接收 P1.value 和同一只读 state；如果 patch 要影响 P2，由具体 runtime 返回
  新 value；
- 明确只有 P3 产生最终 HookResult，Graph settlement/frame 不是 command delivery；
- 旧的“Hook 做幂等 patch/阶段交付”的评审文字应标为错误草案或删除，不能继续充当设计真相。

不要为了这次 Hook 修改给 `architecture.zh-CN.md` 增加第二个 state owner 例外。

## 8. 实施顺序与当前状态

1. 保持唯一的 Python `Invocation` 适配协议；真实 Rust binding 由 infra consumer 接入，
   Hooks 不复制 invocation 机制；
2. 已在 `HookPayloadAdmission` 和 `_HookPort` 边界补齐 value/state/config/result
   payload admission；
3. 已明确 priority 由各自 `HookPriorityPlan.config` 表达，不向公共 Invocation 重复塞
   `HookPriority`；
4. 构造期拓扑 mutation 窗口和 slot 挂载校验继续由 Graph/assembly owner 处理；
5. 已迁移测试替身并保留重复 command 契约，补齐 value 链和 nominal admission 负例；
6. 已同步 Hooks 文档并完成专项门禁。

实现时不要顺手修改 failover、recovery、GraphRunState、主图路由或 Store。若公共 Invocation
需要跨语言 wire schema，只在有真实消费者时由对应 owner 与 `conformance/` 一起定义。

## 9. 验收门禁

Hook 重新评审至少要提供：

- `python -m pytest tests/hooks -q`；
- `python -m ruff check src/mote_kernel/hooks tests/hooks`；
- `python -m ruff format --check src/mote_kernel/hooks tests/hooks`；
- `pyright src/mote_kernel/hooks`；
- `make check` 和 monorepo pre-commit 的实际结果；
- Python Invocation adapter 的 request/result 接缝、错误 payload、slot 不可变和 value 链的
  测试证据；真实 Rust binding 的调用/错误映射待对应 infra consumer；
- 证明最终 HookResult 的 command 顺序和重复保留没有被改坏。

复杂度指标只用于发现候选问题，不得通过增加包装、保留测试专用 legacy 或机械合并
nominal contract 来“过数”。

## 10. 最终判定

**Hooks-only 复审通过。**

泛型 payload admission 的阻断已经关闭：五个具体 nominal descriptor 由一份
`HookPayloadAdmission` 统一声明，Plan/Port/final-result 边界统一检查，错误 value、state、
config、priority config 和 command 元素不会穿透到下一 priority；57 项 Hooks 测试及
100% Hooks 覆盖率通过。

首次 compile 前的拓扑 mutation 窗口、slot 与实际挂载位置的交叉校验，以及真实 Rust
binding 的调用映射仍是外部 Graph/assembly/infra owner 的边界，不构成当前 Hooks-only
阻断。command 仍按已确认契约作为有序 typed delta 返回，不在 Hook 内 apply、去重、冲突
处理或持久化。

failover、重启恢复以及 P1 command 何时进入权威 GraphRunState 不在本次 Hook 评审范围内，
也不应通过 Hook 私有 policy 绕开既有 owner。
