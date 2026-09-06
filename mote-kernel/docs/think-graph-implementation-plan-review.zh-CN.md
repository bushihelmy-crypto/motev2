# Think 图实施计划评审（合理完整 v1）

当前状态：**拓扑订正复审见第 19 节，PromptPort 单 Port 订正见第 20 节；可以授权 Think owner 按边界编码。交付目标是职责范围内
合理完整的 v1，不接受裁剪版或占位实现。`mote_kernel.invocation` typed boundary 是外部只读前置契约，
不由 Think 实施者开发；阶段 A–C 可立即开展，阶段 D 的 adapter 集成及其最终端到端验收待该正式 API 落地。**

> 第 1–19 节保留历次复审的历史证据；其中“仍有 P0/P1”、十节点拓扑、七个 Port 或要求
> Think 修改 Invocation 的内容只描述当时版本，当前结论以第 20 节和实施计划当前版本为准。

评审日期：2026-09-04

评审对象：[`think-graph-implementation-plan.zh-CN.md`](./think-graph-implementation-plan.zh-CN.md)

评审范围：Think 图的拓扑、类型边界、Hook 接入和 Graph 执行边界。

范围决议：本轮不实现持久化与恢复；外部 Port 的非幂等副作用和重试由外部统一能力负责；
Hook command 只由 Think 随 `HookResult` 接收/传递，不在 Think 内投递、执行或聚合。
因此本文不评审上述外部能力的拓扑、策略、预算或交付契约。

## 1. 结论

**总体方向合理，可以按第 19 节的 owner 边界授权编码。** 共享 Hook + route 拓扑、Graph
唯一执行入口、Hook value 链以及外部能力边界可以保留。Think 负责固定 DTO、具名 Port、Graph 装配和专项测试；
核心节点只调用 Port，Invocation 仅由 Port adapter 在外部 API 就绪后消费；
Invocation、Hooks 内部、Failover 内部以及 persistence/recovery 仍由各自 owner 负责。

有效拓扑包含七个直接节点（五个 Think 核心节点、一个共享 `HookNode` 和一个 route 控制节点）；
`Graph` 唯一执行入口、单一 `GraphRunState`、Hook value 链和 nested Graph 边界都与 Kernel
当前架构一致。主要风险不在业务阶段划分，而在真实 Hook kind 与外层 nested boundary 之间
目前没有自动绑定的细节。

当前版本已完成以下文档修订（Prompt 节点已开始编码，其余 Think 图仍在实施中）：

1. 修正 Hook child boundary 的 exact 类型；
2. 将“异常导致节点失败”改成与现有 Graph 一致的失败矩阵；
3. 为多参数 Port 增加 nominal request envelope，并在 assembly 阶段确定 model binding；
4. 明确 ThinkNode 在首次成功 compile 前的可变窗口，沿用 Graph 的 mutation guard；
5. 明确 Think 只调用 Hook 子图并接收 `HookResult`，不处理其 command；
6. 将 Hook 的内部 admission、canonical topology 和其 owner 语义收口给 Hooks/composition
   owner；Think 完整交付其范围内的真实 `HookNode` kind/公开 slot admission 与外层 boundary 连接；
7. 增加 Graph value 的 data-only/递归不可变约束、closed concrete type binding 门禁和父图
   exact-class adapter 规则；
8. 将通用 DTO request/result exact admission 归入 `mote_kernel.invocation`，并把 Think-owned
   payload 的字段校验收口为 concrete DTO 构造自校验；明确终端 Hook 内层 admission/pre-commit
   由外部 owner 负责。
9. 第九次复审进一步取消 `ThinkTypeBinding`，固定 Think v1 outer DTO 和五个业务 Port（其中
   PromptPort 提供三个具名方法）；
   将 Invocation 明确为只读依赖，并把 model 配置值与 Graph definition version 分开。
10. Prompt 收集收敛为一个 `PromptPort`（同一对象提供 system、placeholder、user 三个方法）；
    外部 assembly 必须先对该 Port 对象套 Failover，再把它与另外四个阶段 Port 一并注入。

第五次复审提出的 Hook descriptor 绑定、topology seal 和 pre-commit 议题，按范围决议由
Hooks/父图 owner 负责，不作为 Think P1；第六次复审新增的 real-`HookNode` kind admission
已写入实施稿第 14 节，第七次复审补充了 model binding、公开 slot 和 scope 取消的可验证
要求（详见第 15 节），第八次复审再将通用 DTO 校验算法收敛到 `mote_kernel.invocation`
（详见第 16 节）。数据纯值和具体 DTO schema 仍需随实现落地，但不改变已通过的固定拓扑
方向；通用 DTO 校验算法不再作为 Think 的实现职责。

以下原评审项已由范围决议明确延期或外置，不纳入本轮 Think 完成定义：

- 持久化、durable commit、continuation 和恢复；后续由统一 persistence/recovery 任务处理；
- 外部 Port 的非幂等副作用、receipt/reconcile、invocation identity 和重试保证；由外部
  Port/Failover 统一处理；
- Hook command 的部分交付、投递、幂等和失败策略；Think 只接收并传递 typed output；
- `FailoverPlan` 装饰器的实现与测试；Think 只接受装配方注入的、已套 Failover 的 typed Port，不定义
  Failover SPI。

除上述外置项外，拓扑层没有需要产品层再拍板的新增决策；kind admission 和 slot-specific
assembly 约定、Invocation typed boundary、success-only exception-only 传播和 command 可见性，
仍须由对应 contract owner 明确记录，不能用默认行为代替。

## 2. 已通过的设计

本节的逐节点表述属于拓扑订正前的历史记录；其中五个独立 Hook 的内容由第 19 节共享
Hook + route 结论替代，其余 owner 边界仍然有效。

以下部分可以直接保留：

- 拓扑固定为 `Prompt → PromptHook → Context → ContextHook → Compact → CompactHook →
  Inference → InferenceHook → Command → CommandHook`；Hook 内部继续复用现有
  `Plan → P1 → P2 → P3`，不复制实现；
- `mote_kernel.execution.Graph` 是唯一构图/执行门面，Think 可直接运行，也可作为父图的
  一个 nested node；不新增 Think runner、executor、reducer 或平行状态模型；
- 后续核心节点只消费前一 Hook 的 `HookResult.value`，没有旁路读取原始 stage output；
- Hook 的 value 与 commands 分开处理，Think 只接收 `HookResult` 并沿 value 链继续，不 apply、
  reduce 或处理业务 command；
- Prompt、Context、Compact、Inference、Command 的职责拆分清楚，Interpret/Act 留在图外；
- 能力通过窄的 typed Port 注入；Think-owned payload 和 real-`HookNode` kind admission
  按第 15 节门禁落实，Hook 终端内层 payload 仍由 Hooks/父图 owner 负责；
- 外部 Port/Failover 的 retry owner 不属于 Think；Think 只接收装配方提供的 typed Port，不
  实现自己的重试器或 Failover 适配器。

## 3. 契约复核结果

### 3.1 Hook child boundary 类型（已修订）

上一版计划示例在第 164 行把 `hook_request_type` 写成不存在的 `HookRequestType`。这与现有
[`HookNode`](../src/mote_kernel/hooks/node.py#L137-L140) 不一致：Hook 的 graph input
descriptor 必须是运行时 `HookRequest`，而 compiler 对 nested child input 做 exact type
匹配（[`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1174-L1181)）。

当前实施稿已改成下面的语义：

```text
Think graph input       : ThinkRequestT
核心节点 plumbing output: HookRequest
Hook child output       : HookResult
```

`HookRequest` 的 `value/state`、每个 slot 的 stage value/command，以及最终
`HookResult` 的外层/直接元素由对应的 `HookPayloadAdmission` 校验；`ThinkCoreResult` 等
Think-owned 内层字段在其构造/Port 返回边界校验。不能把 `ThinkRequest` 当作 Hook child
boundary，也不能用 cast 让不同 nominal class 通过 compiler。

### 3.2 异常、业务拒绝和 Graph failure（已修订）

上一版计划“任一异常使当前节点失败”的说法会误导实现。现有 Graph 对下列情况有不同语义；
当前实施稿第 8.2 节已按此矩阵改写：

| 情况 | 当前 Graph 语义 | Think 约束 |
| --- | --- | --- |
| typed 成功结果 | 继续进入后续节点 | 通过对应 Port/Hook 的 nominal admission；不自行改写结果 |
| 业务拒绝 | 由 Graph 的 failure 机制形成 terminal failed result | 不把业务拒绝伪装成成功值；不自行猜测错误类别 |
| Port/用户 callable 普通异常 | 未被 Port contract 归一化时为 `TaskRaised`，session 关闭、execution fence，异常传播；不自动形成 `FailedGraphNode`（见 [`session.py`](../src/mote_kernel/execution/engine/session.py#L203-L220) 和 [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L694-L700)） | Think 不捕获或改写已进入 Graph 的异常；外部 Port/Failover 可按自身 contract 在边界前归一化，未归一化时下游不再调用 |
| `CancelledError` | 走现有取消和 cleanup 边界 | 不吞取消、不把取消伪装成业务结果 |
| `Graph.interrupt`（Think v1 无 codec） | 在 settlement 处因缺少 resume-input codec 以 `ResultCollectionError` 拒绝（[`settlement.py`](../src/mote_kernel/execution/engine/settlement.py#L66-L73)），不产生 awaiting result | Think v1 不支持 interrupt/resume；以负例固定该边界，未来须由 execution owner 提供 typed codec |

因此正文应使用“terminal business failure”或“exception propagation/fence”等精确措辞，
而不是把所有异常统称为失败 settlement。Port 的重试、Failover 和不确定副作用不在本评审
范围内。

### 3.3 Hook command 只接收，不处理（已闭合）

Think 本轮只负责调用现有 `HookNode` 子图并接收其 exact typed `HookResult`。后续核心节点
只读取 `HookResult.value`；`commands` 不在 Think 内累计、解释、投递、去重、执行或 apply，
终端 `CommandHook` 的结果随 Think graph output 原样返回。这里的“原样”表示不转换/投递，
不表示绕过 `HookPayloadAdmission` 或 Think-owned payload 构造校验；终端 Hook 内层 admission
及 pre-commit 语义由外部 owner 负责。command 的部分交付和失败策略不属于 Think 契约，留给
外部 owner。

### 3.4 持久化与恢复（延期）

本轮不实现或承诺 Think 专用持久化、durable commit、continuation、跨进程/进程重启恢复。
Think 只遵循当前 `Graph.run()` 的一次运行语义，不新增 checkpoint、resume 或 recovery
API；相关能力后续由统一的 persistence/recovery 任务处理。

## 4. Port 和执行身份的补充要求

### 4.1 每个 Port 使用单一 typed request envelope（已修订）

上一版计划的 `ContextPort(ThinkRequest, PromptHook.value)`、
`CompactPort(PromptHook.value, ContextHook.value)` 是多参数形状，而共享
`Invocation` 只接受一个 typed request。当前实施稿已增加：

```text
ContextRequest[RequestT, PromptT]
CompactRequest[PromptT, ContextT]
```

二者必须是 frozen/slots 的 nominal Graph value；不能退化为 tuple、`dict`、`object` 或
`Any`。`InferenceRequest` 已有同样方向，应统一所有 Port 的 async、取消、异常和 admission
约定。这是实现约束，不是待用户选择的 API 形状。

Inference 节点采用 assembly 阶段生成不可变的 typed model binding，InferencePort 只执行已
定稿请求；不在 Think 内增加隐式 resolver capability。这样模型选择属于装配职责，Inference
只负责请求组装和调用。

### 4.2 Port invocation identity 与非幂等处理（外置）

外部 Port 的非幂等副作用、receipt/reconcile、invocation identity 和重试保证不属于 Think。
本轮 Think 只要求每个节点按 typed Port contract 完成一次 activation 调用；具体身份、
重试和副作用语义由外部 Port/Failover 统一定义，不能反向扩展 Think 的 DTO 或公共 API。

Prompt 是一个 Port 而不是三个 capability：`PromptPort` 的三个方法属于同一个装配对象，
其 Failover 装饰也以该对象为边界，不能把三个方法拆成三个独立 capability 或分别注入。

## 5. Failover 与外部 Port（非本轮职责）

Failover 的拓扑、重试、非幂等副作用、调用身份和装饰器均由外部任务统一定义。Think 不
创建或依赖 `FailoverPlan` 专用 API，也不判断 Port 是否幂等；它只接收符合自身 typed
contract 且已装饰的 Port。注入顺序固定为：先构造带 admission 的基础 node-local typed
Port（Prompt 为一个三方法 `PromptPort`），再由 Role/Flow assembly 装饰该具体 Port，最后把
装饰后的对象注入 `ThinkNode`；装饰器必须保留 request/result、异步 contract 和基础 admission，并保持调用方取消沿 Graph 边界传播，不创建脱离 owner
的后台 task。若要覆盖 Hook priority invocation，应在构造
`HookNode` 前装饰其 `Invocation`，不能包住 `HookNode`、ThinkNode、Hook 子图或
`Graph.run()`。该包装不进入 `mote_kernel.think` 的公共面；本节不评价 Failover 的内部
retry/receipt/policy。

## 6. 固定拓扑和公共 API 的边界（已确定）

当前 `Graph` 的真实冻结点是**第一次成功 compile**；在此之前，继承的 builder API 仍可被
调用（[`facade.py`](../src/mote_kernel/execution/facade.py#L222-L225)）。本计划采用 canonical
assembly 口径：首次成功 compile 前保留 Graph 的既有 builder 窗口，首次成功 compile 后沿用
Graph mutation guard；Think 不复制一套 `seal/finalize` 状态机，也不宣称构造后绝对不可变。

## 7. 已确定的实施顺序

| 阶段 | 必须交付 |
| --- | --- |
| A：边界冻结 | 冻结固定 outer DTO、具名 Port、真实 `HookNode` kind/slot、Graph 异常/取消/无 codec interrupt 边界；明确 Think 只调用 Port，不直接调用 Invocation |
| B：DTO/Protocol | 实现固定 DTO、字段构造契约、一个三方法 PromptPort 和四个阶段 Port；不复制通用 Invocation admission，不创建本地 helper/stub |
| C：Think 拓扑 | 按七节点固定拓扑组装五个核心节点、一个共享 Hook 和一个 route，完成 kind/slot admission、value 链和唯一 output |
| D：Port adapter | Invocation owner 的正式 typed API 就绪后，实现五个 adapter（一个 PromptPort adapter、四个阶段 adapter）；核心节点只调用具名 Port，adapter 才消费外部 Invocation boundary |
| E：nested/验收 | 完成父 Graph nested 与端到端测试、调用次数/失败取消/公共面门禁，并运行专项和仓库级检查；D 的外部依赖未就绪时只报告阻塞 |

## 8. 实施测试清单

### 8.1 拓扑和类型

- `HookNode` graph input descriptor exact type 为 `HookRequest`；错误的 `ThinkRequest` descriptor
  在 compiler 阶段拒绝；
- 七个 Think direct node、一个共享 Hook slot、唯一 `result` output 和所有 control edge 与计划一致；
- 共享 Hook 参数必须是实际 `HookNode`（或 Hooks owner 提供的 sealed 等价 descriptor）；
  仅有相同 `HookResult` 外壳的普通 `Graph` 不得通过装配；
- `ContextRequest`、`CompactRequest`、`InferenceRequest` 的错误 nominal type、子类、裸字典、
  `list`/`set` 等可变内建 descriptor 和 `Any` 均在 admission 边界拒绝；现有
  `canonical_nominal_type()` 单独使用不足以提供这项保证；
- assembly 生成的 typed model binding 能被 InferencePort 消费；Think 不隐式解析 resolver。

### 8.2 Port 边界

- 每个核心 activation 最多调用已注入 typed Port 一次；
- 普通异常、`CancelledError`、非法 result 的传播与 Graph 既有边界一致；外部 Port/Failover
  在边界前归一化 provider 错误时，不把该归一化误记为 Think 的异常改写；
- `runtime_checkable Protocol` 只能检查成员存在，不能证明参数个数、异步/awaitable 或精确
  返回类型；这些必须由静态类型或显式 async adapter 保证，不能把 `isinstance` 当成装配期
  签名验证；
- Think 不创建第二 runner，不在 Port adapter 中写 Graph state 或实现重试；外部包装由其
  所属任务测试。

### 8.3 Hook、失败和范围边界

- Think 正确重复激活同一个共享 Hook 子图五次并接收 exact typed `HookResult`；后续核心节点只读取
  `.value`，不处理 `.commands`；
- 共享 slot 的五次激活复用同一个 activation 的 `hook_state` projection；slot/stage 绑定由
  Hooks/composition owner 的可验证装配约定保证，Think 不读取私有 admission 或复制 slot seal；
- 终端 `CommandHook` 的 `HookResult` 外壳检查不能替代其内层 payload admission，但该内层
  admission 和 pre-commit 语义属于 Hooks/父图 owner；Think-owned `ThinkCoreResult` 必须在
  自身构造/Port 返回边界完成字段校验；
- `Graph.failure` 仅用于节点明确产生的 terminal business failure；success-only Port 的拒绝/异常
  以 `TaskRaised` 或 contract error 传播，`CancelledError` 按现有 Graph 边界传播；Think v1 不安装
  resume-input codec，`Graph.interrupt` 必须覆盖“被 settlement 以 `ResultCollectionError`
  拒绝、没有 awaiting result”的负例；
- Hook command 的交付、部分失败、幂等和外部消费不在 Think 测试范围；
- 持久化、continuation、跨进程恢复、Port invocation identity 和 Failover 重试不在 Think
  测试范围。

### 8.4 公共面和拓扑 mutation

- `from mote_kernel.think import *` 只得到 `ThinkNode`；
- 首次成功 compile 后所有 inherited mutation API 仍被 Graph guard 拒绝；compile 前窗口按
  Graph 既有行为处理；
- Think 作为 nested node 运行时，父图不能绑定内部核心节点、Hook 或 Port 实例。

## 9. 完成定义

只有同时满足以下条件，才能把 Think 计划标为完成：

- 七节点拓扑和 Hook child boundary exact 类型通过 compiler/runtime 测试；
- 五个核心节点只使用 assembly 注入的、已套 Failover 的具名 typed Port；Prompt 使用同一
  PromptPort 的三个方法，其他 success-only Port 的成功返回、
  contract error、普通异常和取消路径均已定义为 exception-only；Think 不实现 Failover、
  幂等或重试；
- 共享 Hook 参数通过 Think 的必要且完整的 real-`HookNode` kind admission（默认 exact runtime type，
  或 Hooks owner 提供的 sealed kind descriptor）；外层 boundary mismatch 继续由 compiler
  拒绝；slot/stage 的对应关系由 Hooks/composition owner 的可验证装配约定保证，Hook
  内层 admission 不在 Think 完成门禁中重复实现；
- `ThinkCoreResult` 在 Think-owned 构造/Port 返回边界完成字段校验；终端 `HookResult` 的
  内层字段和 pre-commit/command 语义由 Hooks/父图 owner 负责，不能只凭外层 class 推断；
- Graph 异常/取消边界、HookResult 接收语义与当前 Graph 实现一致；
- 持久化、恢复、Port invocation identity 和 command delivery 明确标记为外部/后续任务，
  不在 Think 内重复实现；
- `ThinkNode` 是唯一 Think 公共图入口，没有 ThinkState、第二 runner、第二 reducer 或
  通用 registry；
- 通过 Think/nested 选择集、类型检查、格式检查和本目录 `make check`。全仓 pre-commit
  若因工作树中的其他用户改动无法运行，应在交付记录中明确说明，不得将历史结果当作本次
  证据。

**上一轮判定（已由第 10 节二次复审更新）：范围已收口，架构方向通过；保留的文档契约已
修订，Think 计划可进入实施，外置项由后续统一任务处理。**

## 10. 二次复审（2026-09-04）

### 10.1 已闭合的上轮意见

| 上轮意见 | 当前状态 | 复核依据 |
| --- | --- | --- |
| 把持久化、durable commit、continuation 和跨进程恢复误列入 Think 交付 | 已闭合 | 计划第 7–8 节明确本轮不实现；评审第 3.4 节同步延期 |
| `ContextPort`/`CompactPort` 使用多参数边界 | 已闭合 | 计划增加 `ContextRequest`、`CompactRequest`，并在节点契约中要求 frozen nominal envelope |
| Inference 隐式承担未声明的 resolver | 已闭合 | 计划改为 assembly 阶段注入 immutable model binding |
| Think 处理 Hook command delivery | 已闭合 | 计划明确只接收 `HookResult`，沿 value 链继续，不在 Think 内处理 commands |
| ThinkNode 构造后绝对不可变的表述过强 | 已闭合 | 计划和评审均明确首次成功 compile 才沿用 Graph mutation guard，compile 前窗口仍按 Graph 行为处理 |
| Failover/非幂等副作用被倒灌到 Think | 按范围排除 | 两份文档均将其交给外部 Port/Failover owner；本复审不检查其内部实现 |

### 10.2 P0：伪代码引用 `HookRequestType`（已闭合）

上一版计划第 164 行曾写着：

```python
hook_request_type = HookRequestType
```

当前实施稿已改为从 `mote_kernel.hooks.contract` 导入 `HookRequest`，并使用
`hook_request_type = HookRequest`。正文同时明确 `HookRequest` 是唯一运行时 child
boundary 类型，不新增 `HookRequestType` alias，也不使用 cast 绕过 exact descriptor 检查。
实施测试应保留 `HookRequest` 正例以及 `ThinkRequest`/未定义名称负例。

### 10.3 P1：把异常矩阵直接写进实施计划（已闭合）

实施稿第 8.2 节已加入明确规则：

```text
Graph.failure(...) 只表示显式 terminal business failure；
未被 Port contract 归一化的 callable/Port 普通异常产生 TaskRaised 并传播/fence；CancelledError 走取消边界；
Think 不把异常转换成成功值，也不自行实现 interrupt/resume。
```

这只是把现有 Graph 语义写清，不扩大 Think 范围，也不涉及 Failover 的错误分类策略。

### 10.4 P1：FailoverPlan 装饰器的注入点（已按范围收口）

本复审不评审 Failover 的实现、策略或测试。实施稿现已明确：需要 Failover 的 node-local
Port，由 Role/Flow assembly 在构造 `ThinkNode` 前完成外部 typed 装饰，且装饰后仍保持
Port 的 nominal request/result contract；Think 不依赖 `FailoverPlan` API、不创建装饰器、
不执行重试，也不维护外部调用状态。

装饰范围锁定为具体 Port，不包 Graph、ThinkNode、整个 callable 或 Hook 子图。Failover
的 decorator API、retry loop、cursor、receipt 和 identity 仍由外部任务定义，不属于本
评审；Think 计划只记录注入层级和 contract 保持要求。

### 10.5 二次复审结论

- **拓扑、nested Graph、Hook value 链、DTO envelope、model binding 和范围收口：通过。**
- **`HookRequestType`：已修正为现有 `HookRequest`，通过。**
- **异常矩阵：已写入实施稿，按现有 Graph 语义通过。**
- **Failover decorator 注入点：已明确由外部 assembly 负责，Think 仅保留 Port contract
  边界，通过。**

Think 计划可进入实现；持久化、恢复、幂等、Hook command delivery 和 Failover 内部能力
继续按当前外置安排推进。没有需要用户进一步拍板的 Think 架构决策。

## 11. 三次复审（2026-09-04）

### 11.1 复审基线

本次以实施计划当前工作树版本为准，重新逐项核对了：

- 十节点拓扑、自动 entry、nested child 的 `HookRequest`/`HookResult` 外层 descriptor；
- `HookNode` 的 P3 最终结果语义、value 链和 commands 边界；
- `Graph.failure`、普通异常、取消和 interrupt 的传播矩阵；
- compile 前 builder 窗口与首次成功 compile 后 mutation guard；
- `ThinkRequest`、Context/Compact/Inference envelope、异步 Port 和 admission 约定；
- 外部 Failover 装饰 node-local Port 的注入位置（不审查 Failover 内部策略、重试图或实现）。

### 11.2 本轮已闭合的口径

| 事项 | 结论 | 依据 |
| --- | --- | --- |
| Hook child 的 exact boundary | 通过 | 计划明确五个 child 的输入 descriptor 统一为现有 `HookRequest`，输出为现有 `HookResult`；这与 [`HookNode`](../src/mote_kernel/hooks/node.py#L137-L140) 和 compiler 的 nested exact type 检查（[`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1174-L1181)）一致。 |
| `HookRequestType`/泛型记号 | 通过 | 计划把 `ThinkRequestT`、`ThinkCoreResultT`、`HookCommandT` 明确为类型变量记号，要求 descriptor 使用 composition root 提供的实际 class object，不再引用未定义名称或 typing alias。 |
| CommandHook 最终 value | 通过 | 计划现已说明 Command 产生初始 value，CommandHook 只能在同一个 `ThinkCoreResultT` nominal class 内修订；因此顶层输出描述不再暗示 Hook 必须原样保留 Command 对象。 |
| 成功结果与失败路径 | 通过 | 计划明确只有 Hook 成功执行到 P3 才有最终 `HookResult`；失败、取消或 interrupt 不伪造空结果。现有 [`HookNode`](../src/mote_kernel/hooks/node.py#L87-L97) 的实现与此一致。 |
| 异常矩阵 | 通过 | `Graph.failure(...)`、callable/Port 普通异常 (`TaskRaised`)、`CancelledError` 和无 codec 时被拒绝的 `Graph.interrupt` 已分开描述，并与 [`session.py`](../src/mote_kernel/execution/engine/session.py#L203-L223) / [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L694-L700) 的边界一致。Compact 的 hard-limit/计算错误也已要求显式选择 business failure 或 exception，不得静默成功。 |
| 固定拓扑的冻结时点 | 通过（保留既有窗口） | 计划把“固定”限定为首次成功 compile 后，并显式接受 compile 前的 Graph builder 窗口；这与 [`facade.py`](../src/mote_kernel/execution/facade.py#L222-L225) 一致，不复制第二套 seal。 |
| Port/Failover 接入层级 | 通过 | 计划要求 Role/Flow assembly 在构造 ThinkNode 前，对五个具体 node-local Port（一个三方法 PromptPort 和四个阶段 Port）逐一做 typed 装饰，保持 request/result 及异步形状；不得包装 Graph、ThinkNode、Hook 子图或 `Graph.run()`。Failover 内部仍不在本评审范围。 |
| Graph.Result 输出读取 | 通过 | 直接运行示例已区分 Graph.Result wrapper 与 `outputs["result"]`，并禁止在 failed/aborted/awaiting-resume variant 上 cast 成 Think result。 |

### 11.3 P1：slot-specific Hook admission 的装配期证明仍需落地

这是第三次复审时唯一保留的实现前 P1。计划第 4.0 节已经列出五个 slot 的 value/state/command
nominal 类型矩阵，并要求“HookPayloadAdmission 不匹配在 ThinkNode 装配期失败”；但当前
[`HookNode`](../src/mote_kernel/hooks/node.py#L106-L141) 的 Hook-specific 公开只读面只有
`slot`，其
`HookPayloadAdmission` 被放在内部 `HookPort`/priority callable 中，未提供可供 Think
装配层逐项比较的 descriptor。现有 compiler 只能比较 `HookRequest`/`HookResult` 外壳，
不能证明泛型内部 payload。

在进入阶段 D 前必须二选一：

1. 由 Hooks owner 提供一个不可变、owner-internal 的 payload descriptor（或只读 admission
   property），ThinkNode 按五个具名 slot 与计划矩阵逐项比对；或
2. 由 composition root 将五份已验证的 admission descriptor 作为具名装配事实传给
   ThinkNode，并在构造期比对。

如果两种方式都不采用，最多只能把内层 value/state/command class 的失败降级为“首次
activation 时由 `HookPayloadAdmission` 拒绝”；公开的 `HookNode.slot` 仍必须在装配期校验
definition/version、node_id 和 stage，不能把 slot identity mismatch 留到 activation。若连
slot identity 也无法取得，应直接拒绝替代 descriptor。无论采用哪条路径，都不得读取私有
闭包/字段、使用反射或以未经审计的 `cast` 伪造内部类型。这一项不涉及 Failover。

### 11.4 实施时必须保留的集成测试

以下不是新的架构决策，而是本轮文字修订对应的确定性验收：

- 正常成功链路中十个 activation 的顺序、每个 P3 `HookResult` 的 value/commands，以及
  P1/P2 失败时“不产生最终 HookResult”；
- `ThinkRequest`/`ContextRequest`/`CompactRequest`/`InferenceRequest` 使用实际 nominal
  class，错误子类、`object`、`Any`、Union、裸字典在下游调用前被拒绝；`ModelBindingT`
  也必须经过同一 admission；
- 所有注入 Source/Port 使用单参数 async/awaitable contract；若实现同步能力，只能走
  显式 typed async adapter，不能阻塞事件循环或以 `callable()` 猜测；普通异常、取消和
  非法 result 按 Graph 既有边界传播；无 codec 的 `Graph.interrupt` 按现有
  `ResultCollectionError` 负例处理；
- CommandHook value 的替换仍保持同一个 `ThinkCoreResultT`，终端 commands 原样返回；
  Think 不把它们变成 `GraphRunCommand`；
- ReAct 父图只能绑定 `think.result`。由于 compiler 只检查外层 `HookResult`，父图下游
  在消费 result 前必须复用 final admission，拒绝错误的内层 value/command；
- 带 `Graph.Commit`/事件/持久化的 smoke test 必须证明无效内层 payload 在写集确认前被拒绝，
  包括前四个 Hook 的中间 settlement；
- compile 前/后的 mutation 行为、五个 slot identity（parent 是 Think 自身 Graph，不是
  外层 ReAct graph）和 duplicate/wrong-slot assembly 均有负例；
- 对需要故障能力的 Port，验证 assembly 传入的是 decorated Port 且 contract 未变；只
  统计 Think 对该 decorated Port 的一次 activation 调用，不把 Failover 内部 attempt
  计入 Think 测试。Failover 的 retry/receipt/策略测试仍由其 owner 负责。

### 11.5 门禁记录

- 两份 Markdown 均通过尾随空白检查；本轮 `git diff --check` 应作为交付前必跑门禁。
- 当前 `src/mote_kernel/think/node.py` 仍只有模块文档、没有可用的 `ThinkNode` 组装实现，且
  仓库没有 `tests/think`；因此不能把计划评审当作实现通过。阶段 B/D 完成后必须按计划运行
  Think 专项 pytest、Ruff、pyright 和 nested smoke test。
- 根目录 `make check`/monorepo pre-commit 若被工作树中既有的 execution/failover/feedback
  改动阻断，交付时须记录实际失败位置，不得将历史通过结果当作本轮证据。

### 11.6 三次复审结论

**总体方向通过，条件实施。** 拓扑、Hook exact 外壳、异常/取消边界、Graph 冻结窗口、
异步 typed Port 以及外部 Failover 装饰注入点已经与当前 Kernel 代码和用户划定的范围
一致。当时尚未闭合的是 slot 内层 payload admission 的装配期证明；在选择 descriptor
传递方式（或同步降级为 activation admission）并补齐对应负例前，不应宣称阶段 D 已完成。

持久化、continuation、恢复、幂等、Hook command delivery 和 Failover 内部策略继续按
前文范围外置；本复审不要求也不修改这些 owner 的实现。

## 12. 四次复审（2026-09-04）

### 12.1 本次核对的实现事实

本次以两份文档当前工作树版本和现有 Kernel 源码为准，重点复核上一轮遗留的“类型看起来
正确、运行时却无法证明”的边界：

- [`HookNode`](../src/mote_kernel/hooks/node.py#L106-L141) 的 Hook-specific 公开只读面只有
  `slot`；其
  `HookPayloadAdmission` 实际保存在内部 `HookPort`/priority callable，不能从公开面取得
  五个 slot 的 value/state/command descriptor；
- Graph compiler 对 nested child 只做输入/输出**外层 exact class** 比较（输入边界检查见
  [`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1174-L1181)），不会递归验证
  `HookResult.value` 中 `ThinkCoreResult`、`PromptFrame` 等字段；终端 `CommandHook` 后没有
  一个现成的 Think 业务节点可以自动补做这次检查；
- `Graph.interrupt` 只有在 compiled graph 配置 resume-input codec 时才能结算；当前实现对
  无 codec 的 interrupt 在 [`settlement.py`](../src/mote_kernel/execution/engine/settlement.py#L66-L73)
  抛出 `ResultCollectionError`；
- `runtime_checkable Protocol` 和现有 `Invocation` 检查只能证明成员存在，不能证明参数个数、
  `async`/awaitable 或返回值的 exact 类型；
- [`canonical_nominal_type()`](../src/mote_kernel/execution/graph/ports.py#L28-L35) 会接受
  `dict`、`list`、`set` 这些可变内建 class，不能单独承担 Think 的“不可变 nominal payload”
  门禁；
- Graph 仍在第一次成功 compile 前保留 builder mutation window，之后才由
  [`facade.py`](../src/mote_kernel/execution/facade.py#L222-L225) 拒绝修改；这不是本轮新增问题，
  但构造参数和测试不能声称更早冻结。

### 12.2 当前门禁与处理结论

| 等级 | 发现 | 当前结论与必须动作 |
| --- | --- | --- |
| P1 | slot-specific `HookPayloadAdmission` 无法在 Think 装配期取得。只比较 `HookRequest`/`HookResult` 外壳会放过错误的 value、state 或 command 类型。 | 必须由 Hooks owner 暴露不可变 owner-internal descriptor，或由 composition root 以五个具名 descriptor 传入，并逐项比较 slot、value、state、command 和 parent definition/version。若不扩展 API，最多把内层 class mismatch 降级为首次 activation admission；公开 `HookNode.slot` 的 identity mismatch 仍须装配期拒绝，无法取得 slot identity 的替代 descriptor 直接拒绝；不得读取私有字段、反射或未经审计的 `cast` 猜测。 |
| P1 | 终端 `CommandHook` 后缺少可执行的 `ThinkCoreResult` 内层 admission。Graph output projection 不会调用 `ThinkPayloadAdmission`。同样，后续核心节点的 `.value` 检查无法追溯已提交的前四个 Hook settlement。 | 在阶段 D 前选择并落实一条路径：payload 构造时用具体 descriptor 自校验，或由直接运行方/父图下游在消费 `result` 前显式调用 `admit_final_result(...)`。若 `Graph.Commit`/父图 settlement/事件 owner 会先看到 `GraphTransition.result`，则必须在确认或持久化前 admission；事后 consumer 太晚。前四个 Hook 也应由 slot owner 或 commit/boundary owner 在写集前校验内层字段。示例、类型签名和负例必须落到同一位置；“原样返回”不等于跳过 admission。 |
| P1/P2 | Admission 若包含隐式修复、Port 调用或状态写入，会把纯边界检查变成第二执行/状态路径。 | `ThinkPayloadAdmission` 及各 slot admission 必须是纯函数式校验：只返回原值或抛 contract error，不提交 command、不写 `GraphRunState`/Store；同一 descriptor 才能安全复用于直接运行、commit 前和父图消费。 |
| P1 | `CompactPort` 等 success-only Port 的业务拒绝通道尚未统一。若让 Port 随意返回 `Graph.failure`/Union，Port contract 和 Graph callable 边界会分裂。 | 每个 Port 在实现前选择具体 typed rejection→`Graph.failure` 的映射，或明确只允许异常 (`TaskRaised`)；禁止未声明的 outcome、`None`、裸错误字典或隐式空成功。该选择应写入 B 阶段 contract 和测试。 |
| P1（文档已修正） | 早期文字把 `Graph.interrupt` 描述成可传播的 Think outcome，但 v1 不安装 codec。 | 当前计划已改为 v1 不支持 interrupt/resume，并要求无 codec 的 `ResultCollectionError` 负例；不得把该异常或 `AwaitingResumeResult` 写成 Think 成功/恢复能力。 |
| P1/P2 | 仅有相同 `HookResult` 输出的普通 `Graph` 不能保证 `Plan → P1 → P2 → P3`、slot identity 和 Hook admission。 | `ThinkNode` 构造参数只接受真实 `HookNode`，或 Hooks owner 明确 sealed 的等价 descriptor；默认按精确运行时类型拒绝普通 Graph/未授权子类，并增加 wrong-kind 负例。 |
| P2 | `runtime_checkable Protocol` 不验证异步性、arity 或 awaitable 返回；构造期 `isinstance` 通过不代表 Port 可运行。 | 静态类型检查或显式 typed async adapter 必须承担这些保证；未适配的错误实现允许在首次 activation 以明确 `TypeError`/contract error 失败，文档不能承诺可靠的构造期发现。 |
| P2 | 现有 nominal helper 会接受可变内建 descriptor，也无法递归证明 frozen envelope 的字段不可变。 | Think admission 增加对 `dict`/`list`/`set` 及 `object`/`Any`/Union 的拒绝；payload owner 负责字段级不可变性，不能把浅层 `frozen=True` 当成深冻结。 |
| P2 | `Graph[HookGraphValue]` 的 value universe 主要由静态泛型表达；现有 descriptor admission 不会自动检查声明的 class 是否属于该 carrier。 | Think-owned graph boundary DTO 必须继承 `HookGraphValue`，并由静态检查或显式 admission/负例保证；不能只凭 `canonical_nominal_type()` 接受任意普通 class。 |
| P2 | Python 运行时会擦除泛型 class 参数；严格 pyright 可能需要把 `HookRequest` 等具体运行时 class 桥接为 `type[HookGraphValue]`。全面禁止桥接会与现有 Hooks 实现的类型边界冲突。 | 允许一个经过审计的 owner-internal descriptor factory 做窄化（先验证实际 class/继承关系）；禁止用 `cast` 推断 payload、掩盖 slot mismatch 或强行接受不匹配 class。 |
| P2 | 五个 Hook 若各自声明不同 state class，当前 value 链无法证明同一 activation 的 state 语义；反之若要求同一 projection，构造约束必须明确。 | 计划当前选择五个 slot 使用同一个 exact `HookStateT`；装配 descriptor 和测试逐项断言，未来要分 slot state 必须另行变更 contract/version。 |
| P2 | 前四个非终端 Hook 的 `commands` 在 Think 唯一 `result` 中不可见；若产品误以为它们会自动交付，会形成静默丢失。 | 保持当前“Think 不累计/投递/apply”的设计可以实施，但必须在 API/测试中确认这是有意语义；需要观察时由外部 Graph commit/event owner 提供 typed projection，不能在 Think 内偷偷累加。 |
| P2 | topology、boundary、slot 映射、Hook admission 或 Port contract 变更若复用旧 definition version，会污染 Graph identity/未来恢复。 | 计划已要求上述结构变化提升 Think definition version；实现和 assembly 测试必须覆盖 version 不变与变更两种情况。 |

上述 P1 不是 Failover 内部策略问题。Failover 只需在外部 assembly 按“基础 typed Port →
装饰具体 Port → 注入 Think”的顺序工作；若覆盖 Hook，则装饰构造 `HookNode` 时的
`Invocation`。本复审不评价 retry、receipt、identity、reconcile 或策略实现。

### 12.3 必须同步到实施计划的内容

本轮已将以下约束写入实施稿：

- `ThinkNode` 构造参数包含 `ThinkPayloadAdmission`、五个真实 `HookNode` 以及可取得的
  五份 slot admission descriptor，并在构造期做逐项比较；
- 终端 `admit_final_result(...)` 的实际消费位置必须在直接运行示例和父图接入测试中出现；
  使用 `Graph.Commit`/事件或持久化时还必须在写集确认前执行，不能等 Graph 完成后再补校验；
- success-only Port 的 rejection-vs-exception 选择属于 B 阶段的必交付 contract；
- `ThinkPayloadAdmission` 与 slot admission 作为不可变、纯校验 descriptor，不调用 Port 或写
  state/store；
- provider 错误只有在外部 Port contract 已归一化时才会变成 typed `InferenceResult`，否则按
  Graph 的 `TaskRaised` 边界传播；Think 不同时承诺“原样 provider 异常”和“统一脱敏”；
- Graph boundary descriptor 使用 `Graph[HookGraphValue]` carrier 的 Think-owned DTO 继承关系，
  不能只依赖 `canonical_nominal_type()`；
- 对运行时泛型 class 的静态桥接采用单一、可审计的 owner-internal descriptor factory，不以
  任意 `cast` 绕过 admission；
- 无 codec 的 interrupt、真实 HookNode 检查、可变 descriptor 拒绝、Protocol async/arity
  限制、同一 `HookStateT` 和前四个 Hook command 可见性均进入测试矩阵；
- topology/boundary/slot/Port contract 变化提升 definition version。

### 12.4 四次复审结论

**总体方向通过，条件实施。** 固定十节点拓扑、Graph 唯一执行入口、Hook value 链、外部
node-local Port 装饰边界和单一 `GraphRunState` 仍然合理；Failover 内部继续不在本评审范围。
但在 slot admission descriptor、终端内层 final admission、success-only Port 拒绝通道三个
P1 未落实前，不能宣称 Think 阶段 D 或整个计划完成。interrupt 口径已经修正为 v1 负例，
其余 P2 门禁必须随实现测试落地。完成后再运行 Think 专项测试、类型/格式检查和 `make check`，
并单独记录工作树中既有改动造成的失败。

## 13. 五次复审（2026-09-04）

### 13.1 本次复审的新增事实

本次继续以实施计划工作树版本和 Kernel 当前源码为准，重点检查“文档已经写了保证，但现有
对象/API 是否真的能证明该保证”以及“校验是否发生在 Graph 写集可见之前”：

- [`HookNode`](../src/mote_kernel/hooks/node.py#L106-L141) 的公开 Hook-specific 面目前只有
  `slot`；构造时收到的 `HookPayloadAdmission` 被封装进内部 `HookPort`（见
  [`hooks/port.py`](../src/mote_kernel/hooks/port.py#L23-L46)），没有供 Think 读取
  实际 admission descriptor 的公开/owner-internal binding property。因而单独传入一份
  descriptor 和一个 `HookNode`，无法证明二者对应同一 slot 和同一内部 admission。
- `HookNode` 继承 `Graph`，在首次成功 compile 前仍可调用 `add_node`/`add_edge` 等 builder
  API；Graph 只有在 [`facade.py`](../src/mote_kernel/execution/facade.py#L222-L225) 的
  `_compiled_owner` 已建立后才拒绝 mutation。仅检查 `type`、`slot` 或节点数量不能证明 child
  仍是 canonical `Plan → P1 → P2 → P3`；当前 compile 入口仍是 Graph 的内部
  [`_compile()`](../src/mote_kernel/execution/facade.py#L510-L526)，没有可供 Hooks owner 在不执行
  Graph 的公开封存入口。
- [`hook_definition_id(slot)`](../src/mote_kernel/hooks/identity.py#L51-L62) 的字符串字段
  不包含 `definition_version`；version 仍由 `HookSlotId` 和 Hook Graph 的 `version` 独立
  携带。因此必须逐项比较 version，不能把 helper 返回字符串单独当作跨版本唯一身份。
- Graph 的执行驱动先在 [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L133-L162)
  中 reduce 并构造 `GraphTransition`，再把 transition 交给 `Graph.Commit`；该路径没有
  Think-specific nested payload admission hook。Graph output projection 也不会自动调用
  Think 的 final admission，所以 `Graph.run()` 返回后的 consumer 检查不能保护此前已经被
  commit/父图 settlement 看到的值。
- nested child 的终端节点（包括 `CommandHook`）仍先以本 child scope 的成功 settlement/transition
  暴露，再由父图接收 child boundary 并继续自己的 completed 流程；因此终端
  `CommandHook.commands` 也不能仅凭 child settlement 被视为已确认的业务交付。
- Graph 的 publication/frame/continuation 会保留 `Graph.Values` 中的业务值（参见
  [`run_context.py`](../src/mote_kernel/execution/run_context.py#L127-L348)）。因此 runtime
  handle 一旦进入 `ThinkRequest`、Hook state、frame、result 或 command 的递归字段，就会成为
  隐式状态/不可安全恢复的能力泄漏，而不只是普通 DTO 设计问题。
- [`canonical_nominal_type()`](../src/mote_kernel/execution/graph/ports.py#L28-L35) 只排除
  `object`/`Any` 等少数情况，仍会接受 `dict`、`list`、`set`，也不判断 descriptor 是否为
  closed generic、`Protocol` 或抽象类；Think 需要独立的 concrete-class admission。
- nested compiler 对 child boundary 比较的是 exact class identity（[`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1174-L1181)）。因此真实 ReAct parent
  若有不同 request/result nominal class，必须在 Think 外放置显式 typed adapter，不能靠
  `cast`、宽 envelope 或裸字典“兼容”。
- [`Graph.failure()`](../src/mote_kernel/execution/facade.py#L285-L290) 最终只接受 canonical
  `str`（具体校验见 [`outcome.py`](../src/mote_kernel/execution/graph/outcome.py#L68-L77)）；
  typed rejection 若直接字符串化，既丢失类型，也可能把 payload、secret 或 provider 文本写进
  failure view/嵌套失败聚合。
- 当前 [`think/node.py`](../src/mote_kernel/think/node.py) 只有模块文档，没有可运行的
  `ThinkNode` 组装实现，且尚无 `tests/think`；本次结论是实施计划/契约复审，不是 Think
  代码已通过的结论。

### 13.2 当前问题分级与必须动作

| 等级 | 发现 | 结论与实施前动作 |
| --- | --- | --- |
| **范围外（Hooks owner）** | slot-specific admission descriptor 与真实 `HookNode` 没有结构绑定。 | 这属于 Hook 内部契约，不是 Think 的职责。Think 只接收 composition root 已装配的 `HookNode`，按现有 `HookRequest`/`HookResult` 外层 boundary 连线；Hooks owner 自行保证其 admission。 |
| **范围外（Hooks owner）** | canonical Hook topology 没有可验证的 seal。 | 这属于 Hook 子图的构造和所有权，不是 Think 的职责。Think 不读取私有 builder state、不复制 `Plan → P1 → P2 → P3`、不增加 seal；Hook topology 由 Hooks owner 的既有实现和测试负责。 |
| **已收口** | Think-owned `ThinkCoreResult` 的字段校验。 | `ThinkCoreResult` 在 Think 自己的构造/Port 返回边界校验；终端 `HookResult` 的内层字段和 pre-commit 语义由 Hooks/父图 owner 负责，Think 不提供 `admit_final_result` consumer。 |
| **已收口** | success-only Port 的 rejection 映射和错误暴露。 | v1 统一 exception-only：成功返回具体 typed result，拒绝/异常按 `TaskRaised` 传播；不引入 rejection Union、`None`、裸错误字典或 `Graph.failure` 映射。 |
| **实现门禁** | Graph value 可携带 runtime capability，且 `frozen=True` 不能递归冻结。 | 仅对 Think-owned Graph value/frame/settlement DTO 及递归字段执行 data-only immutable 约束；Hook payload 由 Hooks owner 负责。 |
| **P2** | descriptor factory 可能把 generic template、`Protocol`、抽象类当 Think-owned concrete descriptor。 | 只接受 Think-owned 的 closed concrete nominal class；拒绝未绑定 generic、`typing` alias、`Protocol`、abstract/template class 与 mutable built-in。由显式 DTO 构造 API 和静态负例完成证明，不能通过反射/宽泛 `cast` 猜测“已闭合”。Hook 外壳及其泛型内层由 Hooks owner 负责。 |
| **实现约束** | 多个 Hook 需要共享本轮 `hook_state`。 | 核心节点直接复用同一 `ThinkRequest.hook_state` 对象，不重新读取或改写；其内部 identity/内容语义由 composition root/Hooks owner 负责。 |
| **P2** | 父图 request/result 可能与 Think boundary 不同。 | 父图 assembly 增加显式 typed adapter，使用 `HookGraphValue` carrier 和 data-only DTO；不能用 `cast`、`object`、宽 envelope 或隐藏转换。adapter 不计入 Think 十个直接节点，也不创建第二 runner。 |
| **范围外（Hooks/父图 owner）** | Hook settlement 与 `commands` 的观察、交付和时序。 | Think 不累计、投递或 apply Hook commands；中间/终端 settlement 的业务语义由外部 owner 负责，不纳入 Think 测试。 |
| **实现约束** | Think definition/version 与 Hook 自身 identity 的边界。 | Think 只为自己的节点、边、Graph boundary 和 Port contract 管理 version；Hook slot/Hook definition identity 由 Hooks owner 管理。 |

上述 Think 侧门禁属于 Think assembly、Graph boundary 或数据契约；标为“范围外”的 Hook/
父图事项不纳入本计划。Failover 仍只按既定边界由外部 assembly 装饰具体 node-local Port
（若覆盖 Hook，则装饰其 `Invocation`），本次不评审 retry、policy、receipt、identity 或
reconcile。

### 13.3 实施计划同步核对

本次已将以下内容同步到实施稿，并要求在阶段 B/D/E 的测试中落地：

- 第 4.0 节明确 Think 只接收已装配的 `HookNode` 并按外层 boundary 连线；Hook descriptor、
  slot identity、内部拓扑和 admission 不纳入 Think；
- 第 5.1 节把 data-only 约束限定在 Think-owned Graph value 的递归字段，Hook payload 由
  Hooks owner 负责；
- 第 5.3 节拒绝 Think-owned 未闭合 generic、`Protocol`、抽象类和 mutable built-in descriptor，
  并固定 Think payload 在构造边界自校验；不增加终端 Hook 的 pre-commit consumer；
- 第 7 节规定不同父图 request/result 必须使用外部显式 typed adapter；
- 第 8.2 节冻结 success-only Port 的 exception-only 传播，禁止把 rejection/exception 文本
  当作 Graph value；
- 第 10、11 节增加 Think-owned 运行时句柄/可变容器、父图 adapter、边界 mismatch 和
  Think payload 构造校验的正/负例；Hook 内部测试由 Hooks owner 负责。

### 13.4 第五次复审结论

**总体方向通过，可直接实施。** 十节点 Think 拓扑、现有 Hook 子图复用、`Graph` 唯一执行
门面、单一 `GraphRunState`、Hook value 链以及外部 node-local Port 装饰边界仍然合理；本轮
没有发现需要把 Hooks 内部或 Failover 内部实现倒灌进 Think 的理由。

本轮确认的范围收口如下：Hook 的 admission、slot identity、`Plan → P1 → P2 → P3` 拓扑、
Hook command 和其 pre-commit/交付语义均由 Hooks/父图 owner 负责，不是 Think 的实现前置
条件。Think 只装配五个已提供的 `HookNode`、按外层 `HookRequest`/`HookResult` 连线，并
接收终端结果。

Think 侧保留的实现门禁是：Think-owned DTO 的构造校验和 data-only 约束，以及 success-only
Port 的 exception-only 传播。Failover retry/policy/receipt/identity/reconcile 继续不在本
复审范围。完成这些 Think 自身门禁后再运行 Think 专项测试、类型/格式检查和 `make check`；
若被工作树中既有改动阻断，应记录实际失败位置，不得把文档评审误报为代码实现通过。

### 13.5 本轮检查记录（2026-09-04）

- 两份评审对象的 Markdown 尾随空白与相对链接检查：通过；当前工作树已有差异的
  `git diff --check`：通过（评审文档仍是未跟踪文件，已由前述 Markdown 检查单独覆盖）。
- 本目录 `make check`：Ruff、格式检查和 pyright（0 errors）通过；随后
  `complexity-ratchet` 失败（`1 failed, 21 passed`）。失败来自当前混合工作树的结构复杂度
  超过既有 ratchet（例如 `top_level_definitions 695 → 703`、`decision_points 2166 → 2253`、
  `cognitive_complexity 2918 → 3071`），不是 Think 文档或尚不存在的 Think 实现新增的测试失败；
  因门禁按顺序停止，后续完整 pytest/package 检查未由本次 `make check` 执行。
- monorepo 根目录 `pre-commit run --all-files`：除 `kernel-complexity` 外，已执行的通用文件检查、
  Ruff、格式、Rust/Cloudflare 静态检查和 secrets 检查通过；同一 complexity ratchet 失败。

上述门禁结果只说明当前工作树的可复现状态，不改变第 13 节当时的“方向通过、可直接实施”
架构结论；第 14 节已针对 real-`HookNode` kind admission 补充新的条件。Think 实现落地后
仍须在隔离后的目标改动上运行专项测试、类型检查和完整门禁。

## 14. 六次复审（2026-09-04）

### 14.1 本次复审基线与新增证据

本次以实施计划当前工作树版本为准，重新核对“Think 要求真实 `HookNode`”这一条是否能由
现有 API 和 compiler 实际证明。结论如下：

- [`Graph.add_node()`](../src/mote_kernel/execution/facade.py#L334-L359) 对 nested child 的
  运行时分支只判断 `isinstance(operation, Graph)`，不会判断 child 是否为 `HookNode`；
- nested compiler 对 child boundary 的显式检查（[`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1172-L1181)）
  只比较输入名称和 descriptor `value_type`（输出只是作为普通 publication 解析），不会递归检查
  `Plan → P1 → P2 → P3`、slot 或 Hook payload admission；
- 当前 [`HookNode`](../src/mote_kernel/hooks/node.py#L100-L141) 的 Hook-specific 公开只读面
  只有 `slot`，`HookPayloadAdmission` 保存在内部 `HookPort`，因此 Think 不应通过读取私有
  字段来推断内部契约；
- 已用针对性运行探针验证：一个普通 `Graph[HookGraphValue]` 只声明 `HookRequest` 输入和
  `HookResult` 输出时，可以作为 nested child 编译并成功运行，compiler 不会把它拒绝为“非
  Hook”。这直接否定了计划中“普通 Graph 由参数类型和现有 compiler 自动拒绝”的表述。

### 14.2 P1：Think 自身必须有完整 real-`HookNode` kind admission

计划要求五个参数是“真实的 `HookNode`”，但 Python 参数注解只服务静态检查，不能在运行时
阻止一个外层 boundary 完全相同的普通 `Graph`。因此，ThinkNode 构造期必须增加一个只验证
节点种类的显式门禁：

```text
默认：type(hook) is HookNode
受控扩展：Hooks owner 签发、且与该 child/bundle 绑定的不可变、不可伪造 sealed kind descriptor
```

该门禁应在 Think assembly 阶段失败，并明确指出对应 slot/参数；五个 kind check 应在任何
child 写入 Think builder state 前完成；它只证明 child 是允许的
Hook kind 以及可以接入 nested Graph，不读取私有 admission、泛型参数或内部 topology，也不
复制 `Plan → P1 → P2 → P3`。若允许 `HookNode` 子类，必须由 Hooks owner 提供 sealed descriptor
并测试其来源，且 descriptor 不能作为与 child 脱钩的可由调用方自行构造的标记；不能用宽泛
`isinstance(..., Graph)`、`runtime_checkable Protocol`、反射或
未经审计的 `cast` 代替 kind admission。

这不是把 Hook 内部契约倒灌给 Think：Hook 的 payload admission、slot 内部语义、canonical
topology 和 command/pre-commit 仍由 Hooks/父图 owner 负责。Think 只需拒绝普通 Graph（以及
未授权的伪造子类），并让错误 child boundary 继续由现有 compiler 拒绝；kind admission 不等同
于 slot/definition identity 或内部 topology 校验。

### 14.3 外部前置条件：五个具名 Hook 的 slot 绑定

即便增加 kind admission，五个同型 `HookNode` 仍可能被调用方交换位置；当前 outer boundary
不会揭示 `HookSlotId` 的泛型 payload。由于本轮已决定不让 Think 读取 Hook 私有 admission 或
复制 slot seal，Hooks owner/composition root 必须提供可验证的 assembly 约定（例如具名、不可
交换的 factory/bundle），至少保证：

- `prompt_hook`、`context_hook`、`compact_hook`、`inference_hook`、`command_hook` 与预期
  slot/stage 对应；
- parent definition、definition version 和 node id 的绑定由 owner 逐项确认；
- HookNode 交给 Think 后到首次成功 compile 前不得再调用 builder mutation；若没有 owner seal，
  这只能作为 composition convention 记录，不能由 Think kind check 推导 canonical topology；
- 若无法提供上述可验证事实，文档只能称其为受信任的 composition convention，不能声称
  Think 单独保证 slot identity。

这项是 Hooks/composition owner 的外部前置条件，不要求 Think 新增 Hook topology seal、
admission descriptor 或第二状态机；Failover 的 retry、policy、receipt、identity、reconcile
和装饰器内部实现仍完全不在本次复审范围。

### 14.4 已同步到实施计划的修改

本次已把以下口径写入实施稿第 4.0、6.1、10.1、10.3 和 11 节：

- 普通 Graph 与真实 `HookNode` 的区分由 Think 构造期 kind admission 负责；不能归因于参数
  注解或现有 compiler；
- 默认采用 exact runtime kind check，允许扩展时必须有 Hooks owner 签发且与 child/bundle
  绑定的 sealed kind descriptor；
- 错误 child boundary 仍由 Graph compiler 检查，Hook 内部 slot/admission/topology 继续
  由 Hooks owner 负责；
- 五个具名 Hook 的 slot 绑定作为 Hooks/composition owner 的装配前置条件记录，并增加普通
  Graph 同 boundary 负例、未授权子类负例和 slot 交换装配负例的验收要求。
- `ThinkNode` 显式使用 `__slots__`，只保存初始化后不再重绑的 assembly 引用，不产生动态
  `__dict__` 或 run-local task/cache 字段，以满足 Kernel 的无隐藏可变状态约束。

### 14.5 第六次复审结论

**方向合理，条件实施。** 固定十节点拓扑、Graph 唯一执行入口、单一 `GraphRunState`、
Hook value 链、Think-owned DTO/admission 以及 node-local Port 的外部装饰边界均可保留；按
用户范围，Hook 内部契约和 Failover 内部能力不重新纳入 Think。

当前不能再写成“普通 Graph 会被参数类型/compiler 自动拒绝”。完成 Think 自身的完整
real-`HookNode` kind admission，并由 Hooks/composition owner 提供五个具名 slot 的可验证
装配及 handoff 不变性约定后，计划即可进入实现；在这两项落地前，不应把阶段 D 或整个 Think
计划标为完成。
本次仍是计划评审，不代表 `think/node.py` 已实现或专项测试已通过。

### 14.6 本轮检查记录（2026-09-04）

- 针对性 nested probe 复现了“普通 Graph + 相同 `HookRequest`/`HookResult` 外层 boundary 可
  成功运行”的事实；因此新增 kind admission 建议有直接运行时依据；
- 现有 Hooks 回归集 `python -m pytest -q tests/hooks` 通过（57 passed）；该结果只覆盖当前
  `HookNode` 契约，不证明尚未实现的 ThinkNode；
- 评审对象和实施稿的 Markdown 尾随空白、相对链接及 `git diff --check` 在本轮编辑后重新
  检查；工作树中的既有 execution/failover/feedback 改动仍按第 13.5 节记录的 complexity
  ratchet 结果处理，不把文档评审误报为代码实现通过；
- `make check` 与根目录 pre-commit 的既有 complexity-ratchet 阻断仍有效：Ruff、格式、
  pyright 以及其余已执行检查通过，complexity ratchet 失败；本次实际为
  `top_level_definitions 695→701`、`decision_points 2166→2260`、
  `cognitive_complexity 2918→3081`（完整失败集合见命令输出）；完整 pytest/package 阶段未因
  该门禁继续执行。根目录 `pre-commit run --all-files` 的通用检查、Ruff/格式、Rust/Cloudflare
  静态检查和 secrets 检查通过，仅同一 `kernel-complexity` hook 失败。

## 15. 七次复审（2026-09-04）

### 15.1 复审基线与新增事实

本次以实施计划当前工作树版本和 Kernel 当前源码为准，重点核对第 14 节修改是否已经把
“类型看起来成立、运行时却能被绕过”的路径收口。Think 代码仍未实现；本节是实施前的
计划复核，不把文档约束当成代码证据。

- `InferenceRequest.model` 在现有执行引擎中没有可自动推导的来源。若不把模型选择事实放在
  `ThinkNode` 构造契约中，实施者很容易从 request 未声明字段、Port 闭包或全局 registry
  偷渡一个 resolver。当前计划已改为显式 immutable `model_binding`，并要求它是
  `InferenceRequest.model` 的唯一来源；这条规则还需要 concrete class、构造校验和调用记录
  测试才能闭合。
- [`Graph.add_node()`](../src/mote_kernel/execution/facade.py#L334-L359) 的 nested 分支只按
  `isinstance(operation, Graph)` 处理 child；nested compiler 的边界检查只比较输入名称和
  外层 descriptor 的 exact class（[`compiler.py`](../src/mote_kernel/execution/graph/compiler.py#L1172-L1179)）。
  已用针对性运行探针确认：普通 `Graph[HookGraphValue]` 只声明 `HookRequest` 输入和
  `HookResult` 输出时，可以作为 child 编译并运行。因此“参数注解或现有 compiler 会自动
  拒绝普通 Graph”不成立，计划新增 exact `type(hook) is HookNode` admission 是必要的。
- 同样，真实 `HookNode` 的公开 `slot` 允许构造出属于 `other/othernode` 的 child；若直接把
  它挂到 `think/hook`，现有 compiler 仍只看外层 `HookRequest`/`HookResult`，不会检查父
  definition、version、core node id 或 `AFTER_NODE`。计划现已要求在任何 `Graph.add_node()`
  前逐项比较公开 slot，并拒绝交换、重复复用和错误 parent/version。
- 取消语义不能用一句“取消原样传播”概括。调用方取消 `Graph.run()` 和根 scope 节点自身抛出
  `CancelledError` 会沿 root owner 的 cleanup/传播边界处理；嵌套 child（包括 Think 内的
  Hook）节点自身抛出时，family driver 会先 abort/fence child，再向父 scope 投影
  `nested graph node was cancelled` 的 typed failure，且不会产生 `HookResult`。计划第 8.2、
  10.4 已按 scope 区分这三条路径。
- `frozen=True`、泛型模板和 `runtime_checkable Protocol` 不能单独证明 concrete DTO 的字段、
  递归不可变性、闭合 generic、async/arity 或 exact return type。计划已增加 schema freeze、
  closed concrete descriptor 和静态/显式 adapter 要求，但阶段 B 仍必须给出真正可导入的
  concrete class 与构造入口；不能以“未来用反射扫描”作为实现方案。
- `HookNode` 在首次成功 compile 前仍可使用继承的 builder API。Think 的 kind/slot check
  能证明传入对象的种类和公开挂载事实，不能证明 handoff 后 child 的 `Plan → P1 → P2 → P3`
  没有被 owner 再次 mutation。该不变性必须明确为 Hooks/composition owner 的 handoff 前置
  条件；如果产品要求 Think 单独提供强保证，则需要另行设计可验证的 seal/compile handoff，
  不应在本计划中暗中读取私有 builder 状态。

### 15.2 当前问题分级与处理结论

| 等级 | 发现 | 当前结论与必须动作 |
| --- | --- | --- |
| **P1（实现门禁）** | `model_binding` 的来源已写清，但 `ModelBindingT` 仍是模板记号，尚未有具体 class、字段和递归 data-only 构造校验。 | 阶段 B 必须冻结一个可实例化的 immutable nominal model binding，并在 `ThinkNode` 构造期 exact-admit；正例要证明每次 `InferenceRequest.model` 都来自该对象，负例要拒绝隐式 resolver、运行时句柄和可变嵌套成员。 |
| **P1（实现门禁）** | 外层 boundary 相同的普通 Graph、错误 slot 的真实 Hook 都能绕过现有 compiler。 | 阶段 D 必须先完成五个 exact `HookNode` kind check，再完成公开 `HookNode.slot` 对 Think definition/version、core node id 和 `AFTER_NODE` 的逐项检查，最后才写入 Think builder；这些检查要有 wrong-kind、wrong-slot、交换和重复复用负例。 |
| **P1/P2（契约冻结）** | DTO/Port/admission 的泛型记号和“名称可微调”尚不足以生成严格实现；通用反射也不能可靠判断 Protocol、抽象类或闭合 generic。 | 阶段 B 交付 concrete DTO 字段、exact class、构造校验、不可变集合规则及单一 descriptor factory；Protocol 名称/async 单参数签名也要冻结。可审计的窄桥接只用于 Think-owned DTO，不能用宽泛 `cast` 绕过边界。 |
| **P2（外部前置）** | Hook handoff 到首次成功 compile 之间存在公开 builder mutation 窗口。 | Hooks/composition owner 必须保证交给 Think 后不再 mutation，并把它作为装配约定和测试前置；Think 不复制 Hook topology 或新增第二 seal。若无法接受信任约定，先暂停阶段 D，另行评审 execution/Hook owner 的封存协议。 |
| **P2（测试门禁）** | nested child cancellation 的结果取决于发生 scope；若测试只覆盖调用方取消，会误把 child typed failure 当作 `CancelledError` 原样抛出。 | 测试矩阵必须分别覆盖 caller cancellation、root node-origin cancellation、nested Hook node-origin cancellation，并断言 cleanup/fence、父 scope failure 和无 `HookResult`。 |
| **范围外** | Hook 内层 payload admission、`Plan → P1 → P2 → P3` topology、command delivery/pre-commit，以及 Failover retry/policy/receipt/identity/reconcile。 | 继续由 Hooks/父图/Failover owner 负责。本次只确认 Think 的外部边界：注入已装饰的 node-local Port；不评审或实现这些内部能力。 |

### 15.3 与实施计划的同步核对

本轮确认实施稿已经覆盖以下方向，后续实现不得回退：

- 第 4.0、6.1、10.1、10.3、11 节要求 exact `HookNode` kind admission 和公开 slot binding，
  且所有检查在 child 写入 builder 前完成；普通 Graph 同 boundary 不再被视为可接受 Hook；
- 第 4.4、5.1、6.3、9、10、11 节把 immutable `model_binding` 固定为
  `InferenceRequest.model` 的唯一来源，禁止未声明 request 字段、Port 闭包和 registry resolver；
- 第 5.1–5.3、9、10、11 节要求阶段 B 冻结 concrete DTO schema、closed descriptor、carrier
  继承关系、递归 data-only 约束和窄的静态/显式 adapter；不能把泛型名称或浅层 `frozen=True`
  当作完成证明；
- 第 8.2、10.4 节按 Graph scope 区分取消路径，并把无 codec 的 `Graph.interrupt` 固定为
  负例；Think 不吞取消、伪造成功值或创建脱离 owner 的 task；
- 第 7、10.5、11 节保留父图不同 boundary 时的显式 typed adapter，并继续禁止第二 runner、
  第二 state/reducer 或 Think 内部的 command delivery。

### 15.4 第七次复审结论

**方向合理，条件实施。** 固定十节点拓扑、`Graph` 唯一执行门面、Hook value 链、
`Graph[HookGraphValue]` family 以及外部 node-local Port 装饰边界仍然与 Kernel 架构一致。
本轮没有发现需要把 Hooks 内部或 Failover 内部实现倒灌进 Think 的理由。

进入阶段 D/宣称计划完成前，仍须满足四项可验证条件：

1. 实现并测试 exact `HookNode` kind + public slot admission（包括错误 parent/version、
   node/stage、交换和重复复用）；
2. 以显式 immutable `model_binding` 完成 `InferenceRequest.model` 的唯一来源和 exact
   admission；
3. 在阶段 B 冻结 concrete DTO/Protocol schema 与 descriptor factory，落实递归 data-only
   约束，不以反射或宽泛 `cast` 代替；
4. 按 Graph scope 完成取消、nested boundary、compile 前 handoff 约定和对应负例测试。

Failover 仍只保留“基础 typed node-local Port → 外部装饰 → 注入 Think”的边界说明；本节不
评价 retry、policy、receipt、identity、reconcile 或装饰器内部行为。当前 `think/node.py`
仍无 `ThinkNode` 实现，故本结论不是代码通过结论。

### 15.5 本轮检查记录（2026-09-04）

- `python -m pytest -q tests/hooks`：57 passed；该回归只证明现有 `HookNode` 契约，不能
  证明尚未实现的 ThinkNode。
- 计划和评审 Markdown 的尾随空白、相对链接检查：通过；当前工作树 `git diff --check`：
  通过。评审文档为未跟踪文件，故另以脚本检查其内容。
- 已完成普通 Graph/错误 slot nested probe，结果与第 15.1 节一致；没有把 probe 当作 Think
  实现测试。
- `make check` 与 monorepo 根 `pre-commit run --all-files` 的可复现门禁结果沿用第 13.5、
  14.6 节记录：Ruff、格式、pyright 及其余通用检查通过，`kernel-complexity` 因当前混合
  工作树的既有 execution/failover/feedback 改动超出 ratchet 而失败；Think 尚未产生代码，
  因此不能把该失败归因于 Think 计划，也不能把历史通过结果当作 Think 实现证据。

## 16. 八次复审（2026-09-04）

### 16.1 关于 DTO 校验归属的决策

本次确认：**Think 不实现通用 DTO 校验算法。** 之前第 15 节所说的
“Think-owned descriptor/admission”需按以下分层理解，本文后续实施以本节为准：

| 层 | 唯一职责 | 不负责的内容 |
| --- | --- | --- |
| `mote_kernel.invocation` | 为需要 DTO 的 strict Invocation 提供统一 typed boundary：调用前 request exact class、调用一次、调用后 result exact class，并保留异常/取消语义 | 不解释 Think/Hook 字段，不反射扫描递归对象，不做重试、状态提交或业务错误映射 |
| `think.contract` 及 concrete DTO owner | 声明 concrete DTO 字段、构造入口和字段/递归 data-only 不变量；提供不可变 type binding 元数据 | 不复制 Invocation 调用/校验算法，不检查 Hook 内层 payload |
| `think.port` | 组装节点 request，调用 `mote_kernel.invocation` 的 typed helper，将已准入结果交回节点 | 不另建 validator、resolver、registry 或第二 runner |
| `hooks.contract` / Hooks owner | 维护 `HookPayloadAdmission` 及 Hook 内层 value/state/command 语义 | Think 不读取其私有 admission 或重做 topology |
| Graph compiler | 维护 `Graph.Values`/frame 的声明、carrier 和 nested boundary exact admission | 不替代领域 DTO 字段校验 |

实施基线可采用 `InvocationTypeContract[RequestT, ResultT]` 与一个 strict typed helper（具体
函数名由代码约定确定）。该 contract 只保存由静态类型或 composition owner factory 证明的
concrete class；helper 按“request admission → `Invocation.invoke` 一次 → result admission”的
顺序运行。它不是 Graph value，也不是万能业务 validator，不通过反射推断 typing 对象或扫描
递归字段。日志等非 DTO 诊断调用继续使用现有 `invoke_strict`/`invoke_best_effort`。

因此，实施稿不再要求 `ThinkNode` 接收或实现 `ThinkPayloadAdmission`；若 Graph builder 需要
运行时 class，传入的只是不可变 `ThinkTypeBinding` 元数据。`model_binding`、各 frame 和
`ThinkCoreResult` 的字段合法性由其 concrete DTO 构造入口保证；Think 只负责按图顺序连接和
传递。Hook command、Hook 内层 admission、Failover 和 persistence 的范围结论不变。

实施稿已同步给出 v1 的固定、可实例化 `ModelBinding` concrete baseline（provider/model identity 与
positive revision）及其构造不变量；这解决的是契约可实例化问题，实际代码和负例仍需在阶段
B/E 验收。

### 16.2 对阶段和测试的影响

- 阶段 B/C 必须在 `mote_kernel.invocation` 落地一次通用 typed boundary，并为 Think 各 Port
  绑定 concrete `InvocationTypeContract`；不得在 `think/node.py`、`think/port.py` 或每个
  Port 中复制 exact-type 算法；
- Think 专项测试验证 helper 的调用前后边界、调用次数、异常/取消传播和下游不调用；DTO
  字段与递归不可变性测试归 concrete DTO/contract owner，Hook payload 测试归 Hooks owner；
- 仍保留 exact `HookNode` kind + public slot admission、显式 `model_binding` 来源、三类
  cancellation scope、nested boundary 和 handoff convention 四项实施门禁；只是把其中的
  DTO 校验算法从 Think 移到共享 Invocation 层；
- 不因这次分层新增 `Think` runner、state、reducer、registry 或终端 Hook final-admission
  consumer。

### 16.3 最新结论

该决策是工程分层修订，不需要产品再拍板：通用 Invocation boundary 统一，领域 DTO 规则由
其 owner 保证，Think 只做节点组装。计划仍为**条件实施**，待上述四项门禁和专项代码测试
完成后方可宣称阶段 D/计划完成；本节不表示 `think/node.py` 已实现。

## 17. 九次复审（2026-09-04）

### 17.1 本次结论

**可以授权编码，但授权对象严格限定为 Think owner 的交付。** 授权目标是职责范围内合理完整的
v1，不接受裁剪版或占位实现：`src/mote_kernel/think/` 范围内的固定 DTO、五个业务 Port
contract/adapter（其中一个 PromptPort 提供三个方法）、五核心 + 一个共享 Hook + route 拓扑、装配门禁、失败/取消边界和确定性测试必须全部交付。Invocation、Failover、
Hooks 内部和 persistence/recovery 被排除是 owner 分工，不是删减质量或留下临时实现。

当前 [`invocation.py`](../src/mote_kernel/invocation.py) 仍只有 `Invocation`、`invoke_strict`、
`invoke_best_effort`，且 `__all__ == ["Invocation"]`；计划依赖的 typed contract/helper 尚未在
当前工作树出现。该事实不授权 Think 实施者补写它：`mote_kernel.invocation` 及
`tests/test_invocation.py` 由 Invocation owner 独立开发。Think 只在阶段 D 通过正式接口接入；
接口未就绪时报告依赖阻塞，不得创建本地 helper、stub、compat alias 或修改其导出面。

### 17.2 本轮发现及修订结果

| 问题 | 修订后的唯一口径 |
| --- | --- |
| 开头声称只改 `think/`，阶段任务却要求修改 `invocation.py` | 已从 Think 文件清单、阶段任务、测试和完成门禁中移除 Invocation 实现；第 5.3 节只记录只读依赖语义。 |
| 把范围描述成裁剪版会诱发降级 | 明确为既定 owner 边界内合理完整的 v1；范围内契约、实现和测试必须全部交付，不得用临时路径代替。 |
| 固定 v1 DTO 与 composition 自定义 DTO family 并存 | v1 固定 `ThinkRequest`、各 frame/request/result 和 `ModelBinding` runtime outer class；产品只替换 typed 内层值和 Port，不替换 outer class。 |
| `ThinkTypeBinding` 字段不完整，且固定 DTO 下没有用途 | 删除该装配概念。Graph descriptor 直接使用固定 `ThinkRequest`/`HookRequest`/`HookResult`；每个 Invocation contract 只由对应 adapter 持有。 |
| 结构性 Port 都使用 `__call__`，装配期无法区分或证明内部 helper | 冻结五个业务 Port 的具名 async 方法（PromptPort 内含三个方法）和五个 Think-owned Invocation adapter；Think 只证明自有 adapter 使用正式 helper，composition 对装饰 wrapper 的委托/provenance 负责。 |
| `invoke_typed` 的实现责任和 API 细节混在 Think 计划内 | API 名称、签名、错误和导出策略归 Invocation owner；Think 只执行只读依赖门禁，不修改共享模块。 |
| constructor 被要求阻止 `object.__new__` 伪造 | 改为正常 constructor/factory 校验自身职责；绕过 constructor 的不受支持对象在下一读取/admission 边界失败，不再承诺 Python 无法创建它。 |
| provider/model/revision 配置值变化被错误等同于 definition 变化 | 普通配置值变化不提升 Think definition version；只有固定 DTO schema/解释语义、topology、Graph boundary 或 Port contract 变化才提升。 |

### 17.3 编码授权边界

| 状态 | 文件/工作 |
| --- | --- |
| **已授权** | `src/mote_kernel/think/contract.py`、`node.py`、`__init__.py`；`tests/think/**`；为 Think 公共面/依赖方向增加的窄 `tests/architecture/**` 门禁；两份 Think 文档。 |
| **依赖就绪后授权** | `src/mote_kernel/think/port.py` 中五个 adapter 及其集成测试；只能消费 Invocation owner 已发布的 typed API。 |
| **未授权** | 修改 `src/mote_kernel/invocation.py`、`tests/test_invocation.py`、`src/mote_kernel/failover/**`、Hooks 内部、execution/state，或增加 Think runner/state/reducer/registry/兼容层。 |

阶段顺序已相应调整：A 冻结边界，B 实现固定 DTO/Protocol，C 实现核心节点与七节点 Graph，
D 在外部 Invocation API 就绪后实现五个 adapter，E 完成 nested/端到端测试。阶段 B/C 可使用
严格 typed test double 验证 Think 自身行为，但不得伪造 Invocation API；整个交付只有在 D/E
和全部门禁通过后才算完成。

### 17.4 保留的硬门禁

1. 构造期先完成共享 `type(hook) is HookNode` 和公开 `slot` 的
   definition/version/node/stage 检查，全部通过后才能第一次 `Graph.add_node()`。
2. `InferenceRequest.model` 只使用构造时传入的 exact `ModelBinding` 同一引用；无 resolver、
   registry 或 Port 闭包补值。
3. Graph 仍只有固定七个直接节点、一个 `request` 输入和一个终端 `HookResult` 输出；没有第二
   runner/state/reducer，也不处理 Hook commands。
4. 三类 caller/root/nested cancellation 按现有 Graph scope 分开测试；不把 nested child
   cancellation 错写成原样 `CancelledError`。
5. Think-owned adapter 使用正式 Invocation typed boundary；结构性 Protocol 只证明形状，不被
   当成 async 签名或内部实现 provenance 的证明。
6. Failover 只保留“基础 node-local Port → 外部 typed 装饰 → 注入 Think”的位置约定；本次不
   评审或实现 retry/policy/receipt/identity/reconcile。

### 17.5 最终判定

计划在上述修订后已具备 **Think-owned 编码授权条件**。当前不是“整仓依赖均已实现”的状态：
Invocation typed API 尚未落地，所以 Port adapter 的最终集成仍有明确外部前置门。实施者可以
立即开始阶段 A–C，不需要再等待产品决策；到阶段 D 若依赖仍缺失，只报告 owner blocker，不能
越界开发。完成后必须运行 Think 专项测试、Ruff/format/pyright、`make check` 和 monorepo 根
`pre-commit run --all-files`，并精确记录任何由既有混合工作树导致的失败。

## 18. 十次复审（2026-09-04：合理完整实现收口；拓扑订正前历史）

### 18.1 本轮要求与结论

本轮针对实施稿的交付标准作最终收口。**Think 的目标是既定 owner 边界内合理完整的 v1，
不接受裁剪版、试验版或占位版。** 这意味着下列范围内的
内容必须一起实现并测试：

- 固定的五个核心节点、一个共享真实 `HookNode` 以及七节点 control/data 拓扑；
- 固定 outer DTO、字段构造契约、五个业务 Port（PromptPort 为三方法）和对应 adapter；
- real-`HookNode` kind 与公开 slot admission、`ModelBinding` 唯一来源、nested Graph 边界；
- success-only Port 的异常/取消传播、Hook value 链、终端 output 和确定性正/负例；
- 公共面、并发无隐藏 run-local 状态以及 `make check`/类型/格式/仓库级门禁。

上述清单中的任一项都不得因阶段安排或范围缩减而延期，也不得用宽类型、临时 shim、隐式
resolver、第二 runner 或未测试路径替代。A–E 只是处理外部依赖的施工顺序，不是降低 v1
范围的分期方案。

### 18.2 核心节点与 Invocation 的准确边界

实施稿现统一采用以下调用方向：

```text
Think 核心节点
  → 对应的具名 Think Port
  →（assembly 必须：外部 Failover 装饰具体 Port）
  → Think-owned Invocation adapter
  → 外部 `mote_kernel.invocation` typed boundary
```

因此，Prompt/Context/Compact/Inference/Command 节点只调用各自的具名 Port；它们不直接
import 或调用 `mote_kernel.invocation`。Command 节点同样只调用 `CommandPort`，不会在节点
内部直接调用 Invocation。只有 `think.port` 的 adapter 在 Invocation owner 正式 API 已发布
后，才消费该 API 并执行 request/result admission。

Invocation 的 API、实现、测试、导出和错误层次仍由 Invocation owner 负责；Think 实施者不得
修改 `src/mote_kernel/invocation.py` 或 `tests/test_invocation.py`，不得在 `think/` 创建本地
helper、stub、shim 或兼容 alias。API 未就绪时，阶段 D 记录依赖阻塞；阶段 A–C 可以用严格
typed test double 验证 Think 自身拓扑和节点行为，但不能伪造“Invocation 已就绪”。

Failover 只定义装配位置：基础 typed Port 先完成，再由 Failover owner 提供的装饰器包住
节点持有的具体 node-local Port，最后注入 ThinkNode；这里“套住节点内的 Port”指 capability
对象，不是包住核心 callable 或整个节点。Think 不包 Graph、ThinkNode、Hook 子图或
`Graph.run()`，也不实现或评审 Failover 的 retry/policy/receipt/identity/reconcile。

### 18.3 授权矩阵（最终）

| 状态 | 授权内容 |
| --- | --- |
| **现在可做** | `think/contract.py`、`think/node.py`、`think/__init__.py`；七节点拓扑、固定 DTO/Protocol、kind/slot admission；对应 `tests/think/**`、窄架构门禁和文档更新。 |
| **外部 API 就绪后做** | `think/port.py` 的五个 Invocation adapter 及集成测试；只消费 Invocation owner 发布的正式 typed API。 |
| **始终不在本任务** | `invocation.py` 及其测试、Failover 内部、Hooks 的 Plan/P1/P2/P3 和 payload admission、execution/state 改造，以及 Think runner/state/reducer/registry/兼容层。 |

“现在可做”不等于只交付这一栏：最终 v1 仍必须在外部 API 就绪后完成第二栏，并通过全部
门禁；缺失第二栏时只能称为阶段性进展，不能称为 Think 计划完成。

### 18.4 最终判定

计划通过本轮收口，可以按上述矩阵授权编码。授权标准是**合理完整 v1**：范围内的契约、
实现和测试必须完整落地；owner 分工导致的外置项不构成偷减功能的理由。Invocation 仍是
明确的只读前置依赖，Failover 仍只保留 node-local Port 装饰边界。实现交付时若任一外部
前置未满足，应报告具体 blocker，不得越权补实现或以临时路径宣称完成。

## 19. 拓扑订正复审（共享 Hook 路由）

### 19.1 订正事实

用户确认的拓扑不是“每个核心节点后新增一个 HookNode”。现有 Graph 已支持
predecessor-bound output，因此五个核心节点应把同名、同类型的 `hook_request` 汇入**同一个**
共享 `HookNode`：

```text
prompt/context/compact/inference/command → hook → route
```

`route` 是 Graph 条件边所需的最小 callable 控制节点，按共享 Hook 返回的 nominal frame
选择下一个核心节点或 `END`。线性成功路径为：

```text
prompt → hook → route(context)
context → hook → route(compact)
compact → hook → route(inference)
inference → hook → route(command)
command → hook → route(finish) → END
```

因此实现节点数是七个（五个业务节点、一个共享 Hook、一个 route），业务阶段仍然只有五个；
共享 Hook 在一次运行中激活五次，不是五个 Hook 实例。实施稿已用
`Graph.node_output("hook_request")` predecessor reference 和 `route.hook_result` 终端
output 固化这条边界。

### 19.2 取舍与职责

- ThinkNode 只接收一个真实 `HookNode` 参数，slot 为 `hook/AFTER_NODE`，并在第一次
  `Graph.add_node()` 前校验 kind、parent definition/version、node id 和 `AFTER_NODE` stage；
- Hook 的 `Plan → P1 → P2 → P3`、payload admission、command 语义和 handoff 后 mutation
  仍由 Hooks/composition owner 负责；Think 不复制这些实现；
- route 只做 frame/step 到固定 Graph route token 的映射，并原样传递 `HookResult`，不读取、
  聚合、投递或 apply Hook commands；
- 后续核心节点通过一参数 `Graph.node_output("hook_result")` 消费实际 route predecessor
  的 HookResult，不能旁路读取前一核心节点的原始 output；
- 其余已通过的 owner 边界不变：Invocation typed boundary 统一负责通用 DTO request/result
  admission，持久化/恢复、Failover、非幂等外部行为继续外置。

### 19.3 当前结论

**拓扑订正通过，无新增产品决策。** 实施稿中的五个独立 Hook、十节点计数、五个 Hook
构造参数和逐 slot value matrix 均以本节为准删除；实现只需按共享 Hook + route 方案执行，
无需再次讨论 Hook 内部或外部 owner 能力。

### 19.4 节点模块约定（编码阶段补充）

根据后续实施订正，五个业务节点不再使用子包，改为 `think/` 下的职责模块：
`prompt.py`、`context.py`、`compact.py`、`inference.py` 和 `command.py`。每个模块的
唯一公共导出分别是 `PromptNode`、`ContextNode`、`CompactNode`、`InferenceNode` 和
`CommandNode`，不增加第二个 runner 或 state owner；`think/node.py` 保留为最终
`ThinkNode` 总装配 owner。共享 outer DTO 仍由 `think/contract.py` 所有；该目录扁平化
不改变第 19.1 节的七节点运行拓扑。

## 20. PromptPort 单 Port 订正复审（2026-09-05）

### 20.1 最新架构决策

Prompt 的三个收集动作属于**一个** capability，而不是三个独立 Port。Think-owned contract
固定为：

```python
class PromptPort(Protocol[PayloadT, SystemPromptT, PlaceholderT, UserPromptT]):
    async def load_system_prompt(self, payload: PayloadT, /) -> SystemPromptT: ...
    async def load_placeholder(self, payload: PayloadT, /) -> PlaceholderT: ...
    async def load_user_prompt(self, payload: PayloadT, /) -> UserPromptT: ...
```

因此 Think 的业务 Port 总数是五个：`PromptPort`、`ContextPort`、`CompactPort`、
`InferencePort` 和 `CommandPort`。Prompt 节点只保存一个 `prompt_port`，并在一次 activation
内对同一对象按 `system → placeholder → user` 顺序各调用一次；不再存在
`SystemPromptSource`、`PlaceholderSource` 或 `UserPromptSource` 这三个 contract/字段。

该订正只改变 capability 的聚合边界，不改变七个直接图节点、共享 Hook、route、DTO owner 或
Invocation owner 分工。`think.prompt.__all__` 仍只有 `PromptNode`；`PromptPort` 留在
`think.contract` 作为节点使用的 typed contract，不成为职责模块的第二个公共 API。

### 20.2 Failover 注入顺序（必须遵守）

五个业务 Port 都必须在外部 Role/Flow assembly 注入 Think 前套上 Failover：

```text
基础 PromptPort（一个对象、三个方法）
  → 外部 typed Failover decorator
  → PromptNode(prompt_port=装饰后的对象)

基础 Context/Compact/Inference/CommandPort
  → 各自外部 typed Failover decorator
  → 对应业务节点
```

Failover 的装饰边界是 Port capability 对象本身。对于 Prompt，装饰器必须继续暴露同一个
三方法 `PromptPort` 形状，不能将其拆成三个 source 或只包装其中一个方法。Think 不创建
Failover、不调用 `FailoverPlan`、不实现 retry/policy/receipt/identity/reconcile，也不包住
核心节点、共享 Hook、Think 图或 `Graph.run()`。Failover 的非幂等和重试责任仍完全由外部
owner 负责。

### 20.3 对实施阶段和测试的影响

- 阶段 B 冻结一个三方法 `PromptPort` 与四个阶段 Port，业务 Port 计数为五；
- 阶段 D 若 Invocation typed API 已就绪，实现五个 adapter，其中 Prompt 只有一个保持三
  方法边界的 adapter；不得恢复三个 source adapter 或兼容别名；
- Prompt 正例必须证明同一 Port 对象收到同一 payload，调用顺序严格为
  `load_system_prompt → load_placeholder → load_user_prompt`，每个方法一次；
- 缺失 Port、缺失/不可调用的任一方法必须在 PromptNode 装配期拒绝；方法异常或取消必须
  原样沿现有 Graph 边界传播，后续方法不得调用；
- 装饰后的 Failover Port 只需通过同一 Protocol 形状即可被节点消费；Failover 的重试次数、
  policy 和副作用语义不进入 Think 测试；
- 两份文档中第 1–19 节出现的“七个 Port”“三个 Prompt source”或“Prompt source adapter”
  均属于历史版本记录，按本节统一解释为“五个业务 Port + 一个三方法 PromptPort”；实现和
  验收不得引用旧计数。

### 20.4 结论

**订正通过，无需新的产品决策。** PromptPort 的聚合和 Failover 注入位置是明确的工程边界：
外部先装饰 Port，Think 只接收并按固定顺序调用。除上述 contract/字段收敛外，已有共享
Hook 拓扑、Hook command 只接收、DTO 校验由对应 owner 负责、持久化/恢复外置等评审结论全部
保持不变。

## 21. 扁平模块实施复核（2026-09-05）

用户在编码阶段将目录方案订正为“子包都不留，按职责平铺为 py 文件”。当前实现已按该
决定收敛：`think/prompt.py`、`context.py`、`compact.py`、`inference.py` 和 `command.py`
分别只导出对应图节点；`think/node.py` 只承担共享 Hook、route 和 `ThinkNode` 总装配；
`think/contract.py` 承担共享 DTO/Port contract；`think/__init__.py` 只导出 `ThinkNode`。

本订正不引入新的执行路径、state owner、runner 或兼容子包，且不改变五个业务节点、一个
共享 Hook、一个 route 的七节点拓扑。旧章节中关于 `think/<stage>/` 子包的文字均为历史
评审记录，当前实施和验收以本节的平铺文件布局为准。

## 22. v1 实现复核（2026-09-05）

五个阶段节点、共享 Hook value 链、route 分支、终端 `result` output 及 nested Graph smoke
test 已实现。实现未修改 `invocation.py`、Graph/State 执行引擎或 Failover；Port 仍由外部
assembly 先装饰后注入，Hook command 仍只接收并由外部 owner 解释。专项测试覆盖正常链路、
调用顺序/次数、错误 outer type、未知 step、Hook slot 负例、异常和 nested 边界。
