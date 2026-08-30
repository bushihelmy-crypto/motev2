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

`Graph.run()` 只有 new run、transient continuation 和 control-only state recovery 三类 closed 入口。Completed、aborted 与 awaiting-resume result 都携带 authoritative state 和 non-optional opaque continuation；选择性恢复动作同样由该 `Graph` 门面创建。可选异步 commit callback 会逐条收到 scoped reducer candidate，包括每一个 node settlement；只有 callback 精确确认的 state 才能继续执行。本项目不内置具体 Store，也不提供跨进程 concrete value recovery。

传入仍带 active execution lease 的 state，等价于调用方明确确认旧 attempt 已停止或丢失；此时 `run()` 才会 fence 并 reclaim 该 lease。这个边界不负责并发存活 worker 的仲裁，也不保证外部 Port 副作用 exactly-once。

公共执行异常同样收敛在门面命名空间：`Graph.Error` 是统一基类；`Graph.ValidationError`、`Graph.SnapshotMismatchError`、`Graph.ExecutionLimitError` 以及 value admission/unavailability/publication errors 用于精确捕获。

## 文档导航

- [架构说明](docs/architecture.zh-CN.md)记录当前公共门面、execution/state owner 与持久化边界；
- [Graph Node I/O 实施规范](docs/graph-node-input-output-contract-implementation.zh-CN.md)记录当前 Node I/O、compiled topology、frame、continuation 与 recovery shape；
- [`skip_failed` 需求](docs/skip-failed-output-requirements.zh-CN.md)及其[实施规范](docs/skip-failed-output-implementation.zh-CN.md)记录当前 skip-output 行为；
- [语义保持型简化需求](docs/graph-semantics-preserving-simplification-requirements.zh-CN.md)只拥有本轮行为保持义务、非目标与阶段准入条件；
- [语义保持型简化实施方案](docs/graph-semantics-preserving-simplification-implementation.zh-CN.md)只拥有 target shape、原子迁移账本、实施顺序与验证计划。

各轮评审和历史调研由实施方案的“关联记录”统一索引，README 不复制动态评审列表或规范正文。

## 开发

```bash
python -m pip install -e '.[dev]'
pre-commit install
make check
```

`pre-commit install` 和 `pre-commit run --all-files` 应在 monorepo 根目录执行。

结构复杂度扫描基于 AST 而非源码文本，因此改变量名或常量不能掩盖逻辑重复。`make complexity-ratchet`
是阻止生产类型、字段、分支和结构异味继续增长的 CI 棘轮；`make complexity` 是阻断式审查门禁：它将当前候选
身份与 `pyproject.toml` 中显式维护的 `complexity_reviewed` 清单比较，只有不存在未审查或过期身份时才通过。
已审查、为保持 nominal owner 边界而保留的候选仍会显示在报告中；任何新增候选都会使门禁失败。`make complexity-report`
输出每个候选及其审查状态。棘轮不等于代码健康；债务下降时必须同步下调基线，锁住改进。
两道门禁都会由 `make check` 执行。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
