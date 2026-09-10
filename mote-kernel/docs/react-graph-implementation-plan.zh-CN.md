# ReAct 顶层 Graph 实施计划：Observe、Think、Act 三节点闭环

最后更新：2026-09-07

状态：**设计已收敛，可作为源码实施依据**

本文只设计 ReAct 顶层 Graph。实现方式与现有 `ActNode`、`ThinkNode`、`ObserveNode`
一致：定义 typed contract 和 admission，在构造函数中完成能力校验，然后通过
`mote_kernel.execution.Graph` 装配节点、输入、边和输出。

ReAct 不实现私有 runner、循环、状态仓库或 reducer。它只是一个包含三个 nested Graph
节点的 Graph。

## 1. 固定拓扑

ReAct 顶层直接节点只有三个：

```text
observe
think
act
```

正常拓扑固定为：

```text
START -> observe

think -> observe
act   -> observe

observe -- config    -> END
observe -- assistant -> END
observe -- think     -> act
observe -- act       -> think
```

含义如下：

| Observe route | 目标 | 说明 |
| --- | --- | --- |
| `config` | `END` | Config 结束结果 |
| `assistant` | `END` | Assistant 结束结果 |
| `think` | `act` | Observe 到 Think 产出的动作请求，进入 Act |
| `act` | `think` | Observe 到 Act 产出的执行结果，回到 Think |

`think` 和 `act` 完成后不自行选择下一跳，只走直接边回到 `observe`。下一跳始终由新的
Observe 结果决定。

`START`、`END` 和 route token 都不是节点。不得增加 `route`、`policy`、`wait`、
`pass_through` 等顶层辅助节点。

### 1.1 blocking task 下的原生重入

现有 `ObserveResult.background_task_snapshot.has_blocking_tasks` 明确服务于父 ReAct 的结束
gate。因此 `config` 或 `assistant` 只有在不存在 blocking task 时才能提交 `END`。

存在 blocking task 时，父图使用一个额外 route 回到现有 `observe` 节点：

```text
observe -- observe -> observe
```

这是第五个 route，不是第四个节点。新的 Observe activation 从上一次确认的
`cursor_range.after` 继续读取；若队列为空，沿用 Observe 已有的 interrupt/resume，不创建
顶层等待节点或轮询任务。

## 2. route 与返回值是两条通道

Graph success outcome 已经同时支持 `output` 和 `route`。ReAct 保持两者职责分离：

```text
Observe completion
    ├── route  -> 父图选择 conditional edge
    └── output -> 目标节点的 typed input source
```

- route 只决定走哪条边；
- Observe 的具体返回值传给被选中的下一节点；
- `ObservationKind` 只参与 route policy，不作为下一节点 request；
- 不把 route 塞进业务 payload，也不因为选路而丢弃 output。

当前 Observe nested output 的 exact Graph boundary 是最终 `HookResult`，其中包含：

```text
HookResult
└── ObserveHookEnvelope
    └── WriteObservationStageValue
        └── ObserveResult
```

ReAct 使用 `ObservePayloadAdmission.admit_hook_result()` 校验这个 boundary，再取得
`ObserveResult`。不使用 `cast` 伪装类型，也不让 execution 理解 Observe 的 envelope。

## 3. Observe 具体返回值

当前 `ObserveResult` 已返回以下已确认事实：

```text
current_state
delivery_ids
cursor_range
background_task_snapshot
observation_receipt
```

但原始 observation payload 只存在于 `ObserveFrame.batch`，当前最终 `ObserveResult` 不携带
它。仅靠 delivery id、cursor 和 receipt，无法纯函数地构造需要动态 selector、arguments 或
模型输入的 `ActRequest` / `ThinkRequest`。

因此 ReAct 实施需要把已结算的实际观察值保留到最终结果：

```text
ObserveResult
├── batch: ObservationBatch
├── current_state: ObservationKind
├── delivery_ids: tuple[DeliveryId, ...]
├── cursor_range: CursorRange
├── background_task_snapshot: BackgroundTaskSnapshot
└── observation_receipt: ObservationBatchReceipt
```

`batch` 与现有字段在同一次 `write_observation` 中构造，不重新读取 queue。admission 至少校验：

- `batch.delivery_ids == delivery_ids`；
- `batch` 的 delivery boundary 等于 `observation_receipt.read_boundary`；
- `observation_kind(batch) == current_state`；
- receipt 覆盖 batch 中的全部 delivery；
- settlement boundary、cursor 和 background-task revision 保持现有一致性。

`current_state` 是控制事实，`batch` 是数据事实。`TOOL`、`USER` 等
`ObservationKind` 不直接进入 Think/Act request；目标 request 从已确认 batch 中的 exact
payload 构造。

## 4. ReAct typed contract

### 4.1 Route

`mote_kernel.react.contract` 定义封闭 route：

```text
ReActRoute.CONFIG    = "config"
ReActRoute.ASSISTANT = "assistant"
ReActRoute.THINK     = "think"
ReActRoute.ACT       = "act"
ReActRoute.OBSERVE   = "observe"
```

ReAct route policy 是构造时必须提供的同步纯函数：

```text
ObserveRoutePolicy(ObserveResult) -> ReActRoute
```

policy 负责根据 exact observation payload 形成 `think` 或 `act` route，并执行统一 END gate：

```text
candidate = CONFIG or ASSISTANT
    + no blocking task -> 原 route
    + blocking task    -> OBSERVE
```

Observe domain 仍不知道 Think/Act 节点名；route policy 由 ReAct owner 持有。缺少 policy、
返回未知 route 或返回值类型错误时，assembly/admission fail closed。

### 4.2 Cycle state

ReAct 不增加第二个运行时状态模型。跨节点必须保留的 cursor 放在现有 Hook frame 的 concrete
state projection 中：

```text
ReActHookState
└── cursor: ObservationCursor
```

该值是 immutable Graph value，不是 `ReActState` 或 store。它作为 Observe、Think、Act
装配时绑定的同一个 concrete Hook state type：

1. ReAct admission 要求初始 `ObserveRequest.cursor == hook_state.cursor`；
2. Observe 完成后，以 `ObserveResult.cursor_range.after` 生成下一份 state；
3. Think/Act 按现有实现原样携带 `hook_state` 到最终 `HookResult`；
4. 回到 Observe 时，从已确认的 Think/Act completion 中取得 state.cursor，构造
   `ObserveRequest`。

因此恢复不需要搜索“最近一次 Observe output”，也不需要在 `GraphRunState` 旁增加 cursor
缓存。

### 4.3 Edge projectors

ReAct assembly 需要四个 exact、同步、无副作用的 projector：

| 来源 | 目标 input | 责任 |
| --- | --- | --- |
| Observe completion | `ActRequest` | 从已确认 observation payload 构造 pairing、selector、arguments、caller 和新 hook state |
| Observe completion | `ThinkRequest` | 从已确认 observation payload 构造 Think payload 和新 hook state |
| Think completion | `ObserveRequest` | admission final Think boundary，从 hook state 取得 cursor |
| Act completion | `ObserveRequest` | admission final Act boundary，从 hook state 取得 cursor |

projector 只处理 DTO 转换。它不能调用 Port、读取 Context/queue、修改 state 或启动 child Graph。
目标 request 仍由 Think/Act/Observe 各自 admission 做最终 exact 校验。

ReAct 初始 graph input 直接使用 `ObserveRequest[ReActHookState]`，不再定义没有额外语义的
`ReActStartInput`。

## 5. ReActNode 装配方式

`ReActNode` 直接继承 `Graph[HookGraphValue]`，构造顺序模仿现有三个 domain Graph：

1. 校验 `ObserveNode`、`ThinkNode`、`ActNode`、route policy、projector 和 concrete state type；
2. 在任何 `Graph.add_node()` 之前完成全部 capability/admission 检查；
3. 调用 `super().__init__(definition_id, version=version)`；
4. 声明初始 `ObserveRequest` graph input；
5. 添加 `observe`、`think`、`act` 三个 nested node；
6. 声明直接边和 Observe conditional edge；
7. 将最终 Observe boundary 声明为 ReAct graph output。

概念代码如下，具体 API 名称以 execution 的最终 typed overload 为准：

```python
class ReActNode(Graph[HookGraphValue]):
    def __init__(self, *, observe, think, act, admission, ...):
        admission.validate_assembly(observe, think, act, ...)
        super().__init__(definition_id, version=version)

        initial_request = Graph.graph_input("request", ObserveRequest)

        self.add_node(
            "observe",
            observe,
            activation_inputs=(
                Graph.on_entry(initial_request),
                Graph.on_predecessor("result", project_to_observe_request),
            ),
            route_by=project_observe_route,
        )
        self.add_node(
            "think",
            think,
            activation_inputs=(
                Graph.on_predecessor("result", project_to_think_request),
            ),
        )
        self.add_node(
            "act",
            act,
            activation_inputs=(
                Graph.on_predecessor("result", project_to_act_request),
            ),
        )

        self.add_edge(Graph.START, "observe")
        self.add_edge("think", "observe")
        self.add_edge("act", "observe")
        self.add_edge("observe", "config", Graph.END)
        self.add_edge("observe", "assistant", Graph.END)
        self.add_edge("observe", "think", "act")
        self.add_edge("observe", "act", "think")
        self.add_edge("observe", "observe", "observe")
        self.set_outputs({"result": self.output_ref("observe", "result")})
```

`activation_inputs`、`on_entry`、`on_predecessor` 和 `route_by` 在这里表达需要的 contract，
不是要求照抄的最终命名。

## 6. execution 只补两个 composition seam

ReAct 的业务实现简单；当前 Graph API 只缺两个通用装配能力。

### 6.1 Nested activation input materializer

当前 callable node 已支持 `TypedInputBinding + materialize`，nested Graph 只支持 source 与
child graph input exact 直连。ReAct 的 entry activation 与循环 activation 来源不同，且需要
构造不同 request，因此 nested node 需要同样的 typed materializer，并按 activation cause
选择唯一 input case。

最小要求：

- entry case 可以从 parent graph input 构造 child graph input；
- predecessor case 使用现有 state-owned actual predecessor，不做 latest-value scan；
- compiler 证明每个 activation gate 恰好匹配一个 case；
- materializer 返回 canonical child `Graph.Values`，再由现有 child graph-input descriptor
  做 exact admission；
- materializer 是同步纯函数；
- child start 继续使用现有 `GraphInputEvidence` 和 `ScopedFrameIndex`。

不新增 projection store、projection revision、第二 frame index 或 ReAct 专用 runner。
graph definition/version 已经负责冻结 materializer 所属拓扑；child start transition 已经保存
materialized graph input evidence。

### 6.2 Nested completion route selector

execution 已能把 child `completion_route` 传给 parent；但 Observe 当前 child completion route
是内部 Hook route `write_observation`，不是 ReAct 的业务 route。

Nested node 因此增加可选 typed completion selector：

```text
confirmed child GraphOutputView
    -> synchronous route selector
    -> parent GraphRouteId
```

最小要求：

- selector 只在 child 完成且 output boundary confirmed 后执行；
- selector 读取 exact child output，并由 ReAct admission 解出 `ObserveResult`；
- 返回 route 必须在该 parent node 已声明的 conditional routes 中；
- 选出的 route 写入现有 parent node settlement/transition；
- recovery 从 confirmed child boundary 重新计算并校验同一 route；
- 未配置 selector 的 nested node 保持现有 child completion route 语义。

不新增 terminal candidate state、route evidence store 或 execution 对 ReAct 类型的依赖。

## 7. 运行与恢复

### 7.1 正常闭环

```text
ObserveRequest
    -> observe
    -> route + confirmed Observe completion
       ├── config/assistant -> END
       ├── think -> ActRequest -> act -> ObserveRequest -> observe
       └── act   -> ThinkRequest -> think -> ObserveRequest -> observe
```

数据沿 confirmed output 和 typed projector 传递；route 只选边。

### 7.2 等待

```text
END candidate + blocking task
    -> route observe
    -> ObserveRequest(cursor_range.after)
    -> observe
    -> queue empty
    -> Observe 自身 interrupt
    -> resume 同一 nested activation
```

不保存可复用的旧 END candidate。resume 后必须得到新的 Observe completion，再重新选路。

### 7.3 恢复依据

恢复只使用已有 authoritative evidence：

- `GraphRunState`：frontier、settled activation、route 和 child state；
- `ScopedFrameIndex`：graph input、publication、resume input 和 child boundary；
- graph definition/version：typed materializer 和 route selector 所属拓扑；
- domain admission：Observe/Think/Act exact boundary。

source、descriptor、activation cause、child input 或 route 不一致时 fail closed，不回退到旧
publication，也不重新调用 provider。

## 8. 文件实施顺序

### Phase 1：补 Graph composition seam

- `src/mote_kernel/execution/facade.py`：nested typed input cases 和 completion selector 的唯一公开
  facade；
- `src/mote_kernel/execution/graph/definition.py`：immutable nested node contract；
- `src/mote_kernel/execution/graph/compiler.py`：activation case、child input descriptor 和 route
  coverage 校验；
- `src/mote_kernel/execution/engine/resume_input.py`：按 state-owned cause materialize child input；
- `src/mote_kernel/execution/family_driver.py`：confirmed child output 上执行 route selector；
- 对应 execution compiler/runtime/recovery 测试。

### Phase 2：完善 Observe result

- `src/mote_kernel/observe/contract.py`：`ObserveResult.batch` 及一致性校验；
- `src/mote_kernel/observe/node.py`：从当前 confirmed `ObserveFrame.batch` 构造 result；
- `src/mote_kernel/observe/admission.py`：batch、boundary、receipt exact admission；
- `tests/observe/`：正常值和伪造/错配反例。

### Phase 3：实现 ReAct

建议保持与现有 domain 相同的包形状：

```text
src/mote_kernel/react/__init__.py
src/mote_kernel/react/contract.py
src/mote_kernel/react/admission.py
src/mote_kernel/react/node.py
```

- `contract.py`：route、cycle hook state、route policy/projector protocols；
- `admission.py`：assembly、completion、route 和 request exact admission；
- `node.py`：三节点 Graph 装配；
- `__init__.py`：只暴露 ReAct domain 的最小入口，不重导出 execution 内部类型。

### Phase 4：一次性迁移

- 不恢复 `src/mote_kernel/loop/react/`；
- legacy import 迁移到 `mote_kernel.react`；
- 删除旧 runner、route wrapper 和兼容 alias；
- 新测试放入 `tests/react/`；
- 更新 architecture package inventory 和依赖方向测试；
- 不修改或覆盖工作树中无关的用户文件。

## 9. 验收标准

### 9.1 ReAct contract 与拓扑

- 顶层直接节点集合严格等于 `observe`、`think`、`act`；
- 固定四条业务 route 正确，blocking task 只增加 `observe -> observe` route；
- route 和 output 同时保留，选路不丢失 Observe completion；
- Think/Act 只沿直接边回 Observe；
- 最终输出只来自终止本次 run 的 Observe activation；
- 不存在 route/wait/pass-through 顶层节点。

### 9.2 Typed data flow

- 初始 `ObserveRequest` exact 进入 Observe；
- 初始及循环中的 `ObserveRequest.cursor` 始终等于其 hook state cursor；
- Observe completion 分别能 materialize exact `ThinkRequest` 和 `ActRequest`；
- Think/Act completion 能从 preserved hook state materialize 下一次 `ObserveRequest`；
- 下一次 cursor 等于上一次 confirmed `ObserveResult.cursor_range.after`；
- `ObservationKind` 只参与 route policy，实际 payload 从 `ObserveResult.batch` 传递；
- 错误 envelope、payload、request、route 或 predecessor 均在 child activation 前失败。

### 9.3 Recovery 与并发

- entry 和循环 activation 在 fresh run/recovery 中选择相同 input case；
- route selector 在 fresh run/recovery 中得到相同 route；
- interrupt/resume 不丢 delivery、不复用旧 END candidate；
- 不读取 latest publication，不跨 run/scope 取 frame；
- Observe、Think、Act failure/cancel 沿唯一 Graph execution path 返回；
- 并发 run 的 cursor、Hook state、route 和 child boundary 不串扰。

### 9.4 工程门禁

源码完成后运行：

```text
cd /home/longert/motev2/mote-kernel && make check
cd /home/longert/motev2 && pre-commit run --all-files
```

若门禁受工作树中无关用户改动影响，只报告具体失败，不修改或回滚无关文件。

## 10. 明确不吸收的扩展

以下内容不是实现这个三节点 ReAct 所必需，不进入本计划：

- ReAct 私有 runner、scheduler、reducer、state model 或 store；
- 顶层 route、wait、adapter、pass-through 节点；
- 单独的 projection provenance store、projection revision 或 effect write-set；
- ReAct 对 Config、Context、Model、Tool、ACK provider 的直接调用；
- 为 ReAct 重做 provider 跨存储事务或宣称物理 exactly-once；
- `ReActResult`、`ReActStartInput`、`ReActCycleContext` 等没有新增语义的 wrapper；
- legacy compatibility alias 或第二执行路径。

ReAct 只消费各 child 已确认的 typed boundary。child 内部 effect、receipt 和 reconcile 继续由
Observe、Think、Act 及其 provider owner 负责。
