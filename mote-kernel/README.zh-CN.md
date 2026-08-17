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

Callable node 通过 `add_node()` 直接声明具名输入绑定与 exact 具名输出类型。输入绑定是 data dependency 的唯一事实源；edge 与 join 只声明 control flow。`Graph.values()` 构造 immutable concrete frame，`set_outputs()` 将图结果边界绑定到已 admission 的 graph input 或已确认的 node publication。

`Graph.run()` 只有 new run、transient continuation 和 control-only state recovery 三类 closed 入口。Completed、aborted 与 awaiting-resume result 都携带 authoritative state 和 non-optional opaque continuation；选择性恢复动作同样由该 `Graph` 门面创建。可选异步 commit callback 会逐条收到 scoped reducer candidate，包括每一个 node settlement；只有 callback 精确确认的 state 才能继续执行。本项目不内置具体 Store，也不提供跨进程 concrete value recovery。

传入仍带 active execution lease 的 state，等价于调用方明确确认旧 attempt 已停止或丢失；此时 `run()` 才会 fence 并 reclaim 该 lease。这个边界不负责并发存活 worker 的仲裁，也不保证外部 Port 副作用 exactly-once。

公共执行异常同样收敛在门面命名空间：`Graph.Error` 是统一基类；`Graph.ValidationError`、`Graph.SnapshotMismatchError`、`Graph.ExecutionLimitError` 以及 value admission/unavailability/publication errors 用于精确捕获。

详细设计见 [架构说明](docs/architecture.zh-CN.md)。

## 开发

```bash
python -m pip install -e '.[dev]'
pre-commit install
make check
```

`pre-commit install` 和 `pre-commit run --all-files` 应在 monorepo 根目录执行。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
