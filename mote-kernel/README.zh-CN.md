# Mote Kernel

Mote Kernel 是一个以状态机为核心、支持持久恢复的 Agent Kernel。图控制执行，状态机控制事实。

项目目前处于初始架构与实现阶段。`mote_kernel.execution.Graph` 是唯一公开的图构建与执行门面；executor、session、request/result、拓扑和状态 command 均为内部基础设施，不作为并列公共入口。

```python
from mote_kernel.execution import Graph


async def normalize(values: Graph.Values[str]) -> Graph.Values[str]:
    return Graph.values(text=values["raw"].strip().lower())


graph = Graph[str]("example.normalize")
graph.add_node(
    "normalize",
    normalize,
    inputs={"raw": Graph.graph_input("raw", str)},
    outputs={"text": str},
)
graph.set_outputs({"text": Graph.node_output("normalize", "text")})

result = await graph.run(Graph.values(raw="  MOTE  "), run_id="example-run")
assert isinstance(result, Graph.CompletedResult)
assert result.outputs["text"] == "mote"
```

Callable node 通过 `add_node()` 直接声明具名输入绑定与 exact 具名输出类型。输入绑定是 value source/readiness
的唯一事实源；direct、conditional 与 join edge 是 activation 的唯一事实源。`Graph.node_output()` 绑定不会创建
执行边，因此每个 node-output consumer 还必须声明 incoming control edge。仅依赖 graph input 或没有输入的 root
仍是 automatic entry；`set_outputs()` 只投影结果，不激活节点。`Graph.values()` 构造 immutable concrete frame。

`Graph.node_output()` 有两个 typed 重载：`Graph.node_output("producer", "name")` 固定读取某个 producer，
`Graph.node_output("name")` 读取实际激活本次节点的唯一控制前驱。因果输入节点也可以显式声明为 START entry；
compiler 会把首轮 graph input case 和后续 routed predecessor cases 一并写入 immutable binding。Join target
仍不能隐式挑选某一个前驱值；多个入边只有在 control-flow proof 能证明互斥时才会被准入。runtime 只根据
State-owned activation cause 选择编译好的 graph input 或 exact predecessor publication，不扫描“最新值”。
瞬时 frame 与后端无关的持久值证据复用同一套准入规则；Kernel 不内置具体持久化后端。

`Graph.run()` 支持 new run、进程内 continuation 与 control-only state recovery；其 owner-internal durable recovery
接缝将完整 checkpoint 准入到同一执行路径，不另建 runner。Completed、failed、aborted 与 awaiting-resume result 都携带
authoritative state 和 non-optional opaque continuation；选择性恢复动作仍由同一个门面创建。可选异步 commit callback
逐条收到 scoped reducer candidate 与完整写集，只有精确确认后才安装 state/value。仅传 state 不会读取缺失值，
continuation 也不可序列化。所有 continuation（包括 partial handoff）保留原 commit capability：省略或传 `None`
都继承原对象，改绑在执行前拒绝。持久恢复的读写共用绑定 commit 的同一个 codec；换能力必须重新读取权威 checkpoint，
不能降级为内存提交。每个持久值只使用一份 State-owned evidence commitment，统一绑定 availability coordinate、
descriptor、birth commit、codec、payload、Config cursor（含缺席）以及 publication settlement provenance。
`Agent` 将权威读取、精确 Config 解析、执行权限和提交对账接入同一 seam；具体后端实现不进入 Kernel。
阶段状态与验证记录以[实施计划](docs/kernel-persistence-implementation-plan.zh-CN.md)为准。

传入仍带 active execution lease 的 state，等价于调用方明确确认旧 attempt 已停止或丢失；此时 `run()` 才会 fence 并 reclaim 该 lease。这个边界不负责并发存活 worker 的仲裁，也不保证外部 Port 副作用 exactly-once。

公共执行异常同样收敛在门面命名空间：`Graph.Error` 是统一基类；`Graph.ValidationError`、`Graph.SnapshotMismatchError`、`Graph.ExecutionLimitError` 以及 value admission/unavailability/publication errors 用于精确捕获。

## 持久 Agent 入口

`mote_kernel.Agent` 只保存不可变接线，不驻留 runtime state，也不是第二个 runner。构造时提供 `agent_id`、Graph
装配函数、typed frame codec、`PersistencePort` 和 `AuthorityPort`。每次 `Agent.run(request)` 都按排他权限获取、
权威读取、Graph 装配/准入、执行、业务结果投影、权限释放的顺序完成；执行任务全部收敛后才释放权限。

- `AgentStart(run_id, values)` 只创建从未存在的 run；已有 run 必须报冲突。
- `AgentResume(run_id, answers=())` 读取并继续已有 run，也用于终态回放。`AgentAnswer` 将返回的精确 interrupt
  问题与 typed 业务回答配对；调用者不接触 state、continuation 或逐次替换 commit 的入口。
- 可选 `AgentConfig` 提供 Config store/resolver 及精确初始 key；恢复只解析 state/frame 引用的历史快照。
  Config 更新仍只由 Observe 消费并持久化；这里的 Config snapshot cursor 与已删除的 Observe 调用方 cursor 无关。
- 未知 Graph 提交仅对账同一不可变请求；只有已证明 `NotApplied` 才在显式 `max_commit_attempts` 上限内重发。
  权限失效、冲突、错误 receipt 或结果仍未知直接停止，不基于旧内存写 cleanup。工具执行对账属于 Runtime。

[持久 Agent 导入示例](example/graph/durable_agent_import.py)复用原有业务拓扑和 codec，只注入 Ports，不选择数据库、
传输协议或 Container。ReAct END 后的新任务仍由上层驱动。

## 文档导航

- [可运行图示例](example/graph/README.md)通过公开 `Graph` 门面完整演示拓扑、循环、嵌套作用域、并行 run、全部恢复 action、检查点、预算、取消、部分提交交接与版本化部署；
- [架构说明](docs/architecture.zh-CN.md)记录当前公共门面、execution/state owner 与持久化边界；
- [Execution / State / Frontier 核心调用链](docs/execution-state-frontier-call-chain.zh-CN.md)说明当前 command、reducer、commit 与 frontier 流程；
- [跨模块运行时调用耦合审查](docs/complexity-cross-module-runtime-call-review.zh-CN.md)说明额外高召回指标的统计口径、人工审核方法与边界。

## 开发

```bash
python -m pip install -e '.[dev]'
pre-commit install
make check
```

`pre-commit install` 和 `pre-commit run --all-files` 应在 monorepo 根目录执行。

仓库级 AST 门禁以多个独立探测器召回整函数、语句子树和近似重复，并分析符号/字段使用率、函数内部复杂度与
副作用、调用链、已解析的跨模块运行时调用、模块依赖环和异步所有权。跨模块运行时耦合是高召回的人工审核线索，
不自动判定依赖错误。`make complexity` 对确定性违规执行无例外清单的零债务门禁；
`make complexity-ratchet` 阻止每一项高召回指标增长，并要求在指标改善后立即下调上限。`make complexity-report`
输出指标对应的全部候选。两道门禁都会由 `make check` 执行。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
