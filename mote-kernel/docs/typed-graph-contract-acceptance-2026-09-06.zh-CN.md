# Graph 强类型 Contract 改造验收记录（2026-09-06）

状态：**本轮 Graph/State code review 已完成；通用 nested typed output boundary 与 recovery preflight 标记缺口已修复；专项门禁已运行。**

本文验收 `mote_kernel.execution.Graph` 的 typed contract、frame admission、唯一 typed
adapter、通用 nested output handle，以及 recovery preflight 结果投影。Act、Think、Observe、
Hook、Failover、Invocation 的业务语义和持久化实现仍由各自 owner 负责；本轮只验收它们通过
Graph 通用 output boundary 的接入。

本轮同时复审 typed contract 改动与其直接相连的 Graph/State 调用链。复审遵循“先判断设计和
完整调用链，再用门禁证明没有回归”的顺序；不把复杂度热点、恶意伪造内部对象、网络攻击式极端
场景或当前明确留给持久化适配器的能力扩大成 finding。

## 0. 本轮 Graph/State 复审记录

### 范围与调用链

纳入本轮的真实公开路径是：

```text
Graph facade
  -> typed Graph.bind()/add_node()
  -> make_typed_node_assembly()
  -> compile_graph()
  -> planner / materialization / typed frame
  -> scheduler._execute_task()
  -> node_adapter.invoke_node()
  -> exact input/output admission
  -> routing / settlement / reducer
  -> GraphTransition(candidate State + input + settlement + publication)
  -> Graph.Commit(exact candidate acknowledgement)
  -> confirmation 后安装内存 GraphRunState 和 frame
```

terminal route 的链路仍是：

```text
child terminal settlement
  -> completion_route（唯一归属 GraphRunState）
  -> CompleteGraphFrontier
  -> reducer / exact commit
  -> CompletedChild projection
  -> parent routing
```

### 复审台账

| 检查项 | 结论 | 复审依据 |
| --- | --- | --- |
| Graph facade、execution engine、scheduler、reducer、`GraphRunState` 的唯一 owner | 通过 | 未发现第二个公开 facade、runner、scheduler、reducer、state model 或 latest-value 镜像 |
| typed 节点是否只能经唯一 adapter 进入执行 | 通过 | scheduler 统一调用 `execution.node_adapter.invoke_node()`；typed operation 不接收宽泛 `Graph.Values` |
| candidate State、settlement、publication 与 frame 的提交边界 | 通过 | `GraphTransition` 一次携带完整写集；commit 失败、错误 State 或非 exact candidate 时旧 snapshot/frame 不前移 |
| `completion_route` 状态不变量 | 通过 | 仅 completed state 保留；running/failed/aborted 的 validation 明确拒绝该字段，advance 也会清除 |
| nested terminal route | 通过 | child route 经 frontier、routing、settlement、completed projection 传给父图，父图只按自身已编译 conditional domain 消费 |
| recovery / 分布式边界 | 通过（保守） | 已确认 route 直接复用 State；route evidence 不足时只展开父图声明域并 fail closed；preflight 返回值保留 `completion_route_known`；CAS/revision、lease/fence、ack 丢失和 partial commit 没有发现真实旁路 |
| legacy 双路径与兼容层 | 通过 | 未发现旧 API alias、wrapper、第二执行路径或为 legacy test 污染生产代码的改动 |

### 本轮 finding

没有发现可由正常公开 `Graph` 调用触发、并会造成错误提交、错误路由、状态分叉、owner 重复或
分布式恢复不一致的阻塞问题。

以下仅作后续整理观察，不改变本轮通过结论：

1. `NodeOutputRef` 的固定 typed handle 使用 descriptor object identity，而 `GraphInputRef`/
   predecessor ref 的 compiler 校验主要比较 `value_type` identity；正常公开调用不会产生可观察
   错误，文档不宜把两者写成完全相同的 identity 规则。
2. `Graph.output_ref(node_id, output_name)` 已覆盖普通 callable 与任意深度 nested Graph；它只
   解析已声明 boundary 并复用原 descriptor，不依据运行时值猜类型。
3. `preflight_recovery()` 的返回 `RecoveryTransferState` 已显式携带
   `boundary.completion_route_known`；已知 `None` route 与尚未确定 route 不再折叠。

具体 persistence adapter、durable loader/reconcile 和 crash-safe durable concrete-value recovery
仍未实现，按既定范围由后续持久化适配器负责，不是本轮阻塞项。

## 1. 验收结论

- `mote_kernel.execution.Graph` 仍是唯一公开的组图和执行 facade；没有新增 `TypedGraph`、
  domain builder、第二 runner 或第二 state owner。
- typed 节点的 contract、slot、descriptor、output ref 和 publisher 由同一个 assembly factory
  一次生成；编译器不通过字符串猜类型，也不允许 foreign descriptor 混入。
- `Graph.Values` 仍只是不可变的“名称 + 实际对象”运行时载体。泛型参数只存在于静态类型
  视图，不被写入或依赖于运行时对象；具体运行时 class 由 compiled
  `NominalTypeDescriptor` 保存。
- scheduler 调用节点时只有一个 typed seam：`execution.node_adapter.invoke_node()`。
  typed 节点不会绕过 adapter 直接接收宽泛 `Graph.Values`。
- 输入在 operation 前做端口级 exact admission 和完整 DTO admission；普通输出在 publication
  前做 exact admission；已有 success/failure/interrupt outcome 按现有 Graph 语义透传。
- direct、conditional、predecessor、Join、nested graph、route、资源、取消、异常、settlement
  和 commit 的既有 Graph 路径未另建一套实现。

因此，本阶段可以交给其他 agent 做 Graph code review。这里的“完成”指 Graph typed contract
阶段，不表示其他领域已经迁移完毕，也不表示未承诺的 durable 能力已经实现。

## 2. 实际调用链

```text
Graph facade
  -> Graph.bind()/Graph.add_node(typed overload)
  -> make_typed_node_assembly()
       ├─ NodeContract
       ├─ typed input bindings / slots
       ├─ output declaration + NodeOutputRef（共享同一 descriptor）
       └─ typed output publisher
  -> compile_graph()
       ├─ source/target/edge/gate 校验
       ├─ typed descriptor identity 校验
       ├─ publication/predecessor/Join/nested boundary 解析
       └─ immutable compiled topology
  -> planner/admission 产生当前 NodeInputFrame
  -> TaskScheduler._execute_task()
  -> execution.node_adapter.invoke_node()
       1. NodeInputs 绑定 identity 校验
       2. input_materializer(NodeInputs)
       3. admit_exact(完整 InputDTO)
       4. operation(InputDTO)
       5. 现有 Graph outcome 原样透传，或 output DTO exact admission
       6. output_publisher(OutputDTO) -> Graph.Values
  -> _project_outcome()
  -> 现有 settlement / GraphTransition / reducer / commit
```

adapter 不写 `GraphRunState`、不启动额外 task、不重试、不 apply command。节点异常直接回到
现有 scheduler 的异常边界；adapter 不通过 `__cause__`、错误文本或包装层级猜异常来源。

## 3. 实现台账

| 文件 | 已落地职责 | review 要点 |
| --- | --- | --- |
| `src/mote_kernel/execution/facade.py` | `Graph.bind`、`Graph.output_ref`、typed `add_node` overload、`Graph.Inputs/InputBinding/OutputRef`、统一 `add_edge` | typed 和 legacy mapping 都进入同一个 Graph facade；`output_ref` 递归解析 nested boundary 并复用 child descriptor，没有旁路 runner |
| `src/mote_kernel/execution/graph/ports.py` | `NominalTypeDescriptor[T]`、typed refs/slot/binding、concrete nominal 门禁 | source/destination 共享 descriptor；拒绝 top type、Protocol、abstract class、可变容器 |
| `src/mote_kernel/execution/graph/node.py` | `NodeInputs`、`NodeContract`、`TypedNodeAssembly`、`TypedNodeInvoker`、assembly factory | binding 以对象 identity 约束；contract/slot/ref/publisher 原子生成 |
| `src/mote_kernel/execution/graph/values.py` | `admit_exact`、`_frame_value_typed`、frame/value admission | `_admit_entries` 和端口读取共用同一 exact-class 原语 |
| `src/mote_kernel/execution/node_adapter.py` | 唯一 `Graph.Values ↔ DTO` typed adapter | operation 前后 admission；现有 outcome 精确变体透传；publisher 必须返回 canonical Graph.Values |
| `src/mote_kernel/execution/graph/compiler.py` | typed output descriptor identity、predecessor descriptor、publication/gate/nested 编译 | 编译期拒绝 foreign/stale typed handle，不猜类型 |
| `src/mote_kernel/execution/engine/scheduler.py` | 统一调用 `invoke_node` | 不再直接把 typed 节点当作宽泛 values callable 调用 |

`src/mote_kernel/execution/__init__.py` 只导出 `Graph`，`execution.graph` 包不提供平行 facade。
typed declaration 类型是 execution owner 的内部组成；面向组图的入口仍只有 `Graph` 及其
typed helper aliases。

## 4. Canonical typed API

```python
from dataclasses import dataclass
from typing import TypeAlias

from mote_kernel.execution import Graph


@dataclass(frozen=True, slots=True)
class Source:
    text: str


@dataclass(frozen=True, slots=True)
class Request:
    text: str


@dataclass(frozen=True, slots=True)
class Result:
    text: str


Value: TypeAlias = Source | Request | Result
graph = Graph[Value]("review.linear")
source = Graph.bind("source", Graph.graph_input("source", Source))


async def convert(request: Request) -> Result:
    return Result(request.text.upper())


result_ref = graph.add_node(
    "convert",
    convert,
    inputs=(source,),
    input_type=Request,
    materialize=lambda values: Request(values.get(source).text),
    output_name="result",
    output_type=Result,
)
graph.add_edge("convert", Graph.END)
graph.set_outputs({"result": result_ref})
```

`Graph.bind()` 不重复填写类型；类型来自 `GraphInputRef[T]`、`NodeOutputRef[T]` 或
`PredecessorOutputRef[T]` 自己携带的 descriptor。`add_node` 返回带具体 `OutputT` 的
`NodeOutputRef[OutputT]`，可直接作为后续 `Graph.bind` 或 graph output 来源。

固定 producer 与实际控制前驱仍是两种不同语义：

```python
fixed = Graph.node_output("producer", "value")  # 固定 producer 地址
previous = Graph.node_output("value")            # 当前 activation 的实际前驱
previous_typed = Graph.node_output(result_ref)    # 从 typed ref 保留 descriptor
```

它们都由现有 compiler/materializer 解析；不会引入 latest-value 缓存或额外 route 节点。

## 5. 不可降级的运行时/编译时门禁

### 5.1 Descriptor 与 exact admission

- `canonical_nominal_type()` 只接受 concrete nominal class。
- `object`、`typing.Any`、Protocol、abstract class、`list`/`dict`/`set`/`bytearray`/
  `memoryview` 等 mutable container 在声明处拒绝。
- 运行时规则是 `type(value) is descriptor.value_type`；子类或结构相同对象不会隐式通过。
- malformed descriptor（错误对象、top type、mutable container）在 `admit_exact()` 处 fail closed。
- `_admit_entries()` 与 `_frame_value_typed()` 复用 `admit_exact()`，没有第二份 `type()` 算法。

### 5.2 单一 contract 来源

`make_typed_node_assembly()` 同时产生：

1. `NodeContract` 及 input/output descriptor；
2. 编译器使用的 `InputBindings`/`OutputDeclarations`；
3. 返回给调用方的 typed `NodeOutputRef`；
4. 负责发布并再次 admission 的 output publisher。

因此调用者不能靠 `Graph.node_output("node", "name")` 猜一个 `T`，也不能另外维护名称到
类型的表。compiler 会确认 typed output ref 与节点声明是同一个 descriptor 对象。

### 5.3 Adapter 边界

- `NodeInputs.get(binding)` 只接受 assembly 中**同一个 binding 对象**；equal-but-foreign
  binding 也拒绝，避免 materializer 伪造未声明 slot。
- 每个 slot 先通过 `_frame_value_typed()`，再由 adapter 对完整 InputDTO 做一次 exact admission。
- operation 返回普通 DTO 时，adapter 先 admission，再交给 publisher；publisher 非
  `_GraphValues` 会被拒绝。
- operation 返回现有 `_GraphSuccessOutcome`、`_GraphFailureOutcome` 或
  `_GraphInterruptOutcome` 时不改写对象、route、failure 或 interrupt payload。
- operation 抛出的异常实例保持原样；取消仍由 scheduler 的既有取消边界处理。

## 6. Graph 组合与 nested output/route 回归

本次没有增加 route 节点。route 仍是 success outcome 的 token，由既有 Graph edge 选择器
消费；nested child completion 的 route 由父图按 compiled conditional edge 选择下一跳。
typed contract 只负责 node input/output 的 DTO 边界，不偷偷改变拓扑。

已覆盖/回归的组合包括：

- 线性 direct edge、fan-out、conditional route；
- predecessor output 按真实 activation cause 读取已提交 publication；
- Join 的 source/target 坐标和 publication selection；
- nested child completion route 被 parent 消费；
- `Graph.output_ref(node_id, output_name)` 对普通 callable、单层/多层 nested child 和
  Graph-input boundary 返回 child 声明的 exact descriptor；descriptor-less legacy boundary
  可被补全，foreign descriptor、未知 boundary、重复 node 和递归 composition fail closed；
- resource waiter、interrupt、failure、普通异常、取消和 Graph settlement/commit；
- compile 后 mutation guard、foreign/stale publication、错误 descriptor、并发 run 隔离；
- typed output 的错误 input/output、duplicate slot、publisher 错误和 outcome 透传。

## 7. 本轮测试与门禁记录

以下命令在本轮实现后执行；共享工作树的仓库级 complexity ratchet 仍可能受其他 agent 的并行
改动影响，详见末尾范围声明：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/execution -q --tb=short` | **833 passed**（含 nested output ref） |
| `python -m pytest tests/execution/graph tests/execution/test_typed_node_contract.py -q --tb=short` | **173 passed** |
| `python -m pytest tests/architecture/test_graph_execution_ownership.py tests/architecture/test_graph_typing_fixtures.py tests/architecture/test_generic_integrity.py -q --tb=short` | **69 passed** |
| `python -m pytest tests/execution tests/hooks tests/think tests/observe tests/act -q --tb=short` | **1413 passed** |
| `pyright --project pyproject.toml` | **0 errors, 0 warnings, 0 informations** |
| `python -m ruff check src/mote_kernel/execution src/mote_kernel/hooks src/mote_kernel/think src/mote_kernel/observe src/mote_kernel/act tests/execution tests/hooks tests/think tests/observe tests/act` | **All checks passed** |
| `git diff --check` | **通过** |
| `rg -n '^\s*#\s*pyright' src/mote_kernel/execution tests/execution` | **无输出；未新增 `# pyright` 指令** |
| 仓库根目录 `pre-commit run --all-files` | **本轮未运行**；共享工作树含其他 agent 的未交付改动，避免对其文件做自动改写；合并前由仓库 owner 运行 |

重点新增测试位置：

- `tests/execution/test_typed_node_contract.py`：DTO materialize/publish、输入/输出 exact
- `tests/execution/test_nested_output_ref.py`：普通/nested/deep boundary、Graph-input、descriptor
  identity、legacy source、非法 composition 和 compile 后 handle；
- `tests/execution/engine/test_recovery_identity.py`：`completion_route_known` false/true projection；
  admission、descriptor identity、duplicate/foreign binding、publisher/outcome/异常、
  conditional predecessor 和 nested route；
- `tests/execution/graph/test_values_contract.py`：`_frame_value_typed()`、统一
  `admit_exact()`、foreign frame、malformed descriptor 和 exact-class 边界；
- `tests/execution/graph/test_ports_contract.py` 及 compiler/predecessor 测试：concrete
  nominal 门禁、typed ref/slot、publication 和 predecessor 编译约束；
- `tests/typing_positive/typed_node_contract.py`、`tests/typing_negative/typed_node_wrong_*.py`：
  静态正例和错误输入/输出负例。

### `make check` 的历史边界

实施阶段的 `make check` 曾执行，但在 complexity ratchet 阶段停止，不能标记为全绿。失败是共享工作树中
Act/Think/Observe/Failover 等并行改动造成的仓库级复杂度增长，而不是 Graph typed contract
专项测试失败。记录到的门禁摘要：

```text
tests/architecture/test_complexity_gate.py::test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked
top_level_definitions: configured 727, actual 990
type_definitions:       configured 432, actual 612
decision_points:        configured 2247, actual 2930
complexity_hotspots:    configured 89, actual 110
max_cognitive_complexity: configured 122, actual 130
```

本阶段不修改 complexity ratchet，也不为通过该门禁重构其他领域；在干净或各领域合并后的
工作树上应由仓库 owner 重新运行完整 `make check`。本轮复审不重跑该命令，也不把历史结果
冒充当前全绿证据。

## 8. 范围声明与 review checklist

### 明确未做

- 未恢复已删除的 `src/mote_kernel/operations`；
- 未实现新的 persistence/checkpoint/跨进程 recovery 入口；
- 未实现 Failover/retry、非幂等 receipt 或 Hook command delivery；
- 未改动 Act、Think、Observe、Hook 的业务语义；仅把它们的 nested output 取值迁移到
  `Graph.output_ref()`；
- 未新增第二 Graph facade、第二 scheduler、第二 reducer、第二 State 或隐式 cache；
- 未加入 `# pyright` 注释/指令。

### Reviewer 应逐项确认

1. typed 节点是否只能从 `Graph.add_node` 的 typed overload 进入，且 assembly 是唯一 contract 来源；
2. `Graph.Values` 是否仍只保存名称和对象，所有具体 class 检查是否集中在 descriptor/admission；
3. scheduler 是否始终经 `invoke_node`，typed operation 是否绝不会收到宽泛 values；
4. operation 的 Graph outcome、普通异常和取消是否保持原有 identity/route/错误边界；
5. compiler 是否拒绝 descriptor identity 不一致、错误 predecessor、nested boundary 和重复 slot；
6. direct/conditional/predecessor/Join/nested route 及 settlement/commit 是否复用既有 Graph 路径；
7. 是否误把本记录的专项通过扩大成 Act/Think/Observe 或持久化已经完成。

## 9. 实施阶段判断（历史记录）

Graph typed contract 的生产代码、唯一 adapter、编译期门禁和专项测试已落地，实施阶段没有发现
需要在 Graph 范围内阻断 code review 的问题。`make check` 的唯一记录性阻断来自共享工作树的
复杂度 ratchet；它不改变上述 Graph 专项证据，也不应通过修改其他 agent 的领域代码来规避。

## 10. 本轮复审最终判断

Graph/State 设计和完整调用链保持单一 owner、单一提交入口和确认后前移的不变量；typed frame、
routing、reducer、nested projection 与 recovery 没有形成第二执行路径。本轮没有真实 blocker，
因此本轮 code review **通过**。

本轮运行了上述专项测试、Pyright、Ruff 和 diff 检查；没有运行完整 `make check` 或 pre-commit。
因此“代码 review 通过”不等同于“当前工作树仓库门禁全绿”。提交前若需要仓库级门禁结论，应在
合并全部领域改动后由仓库 owner 统一运行并单独记录结果。
