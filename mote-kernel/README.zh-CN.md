# Mote Kernel

Mote Kernel 是一个以状态机为核心、支持持久恢复的 Agent Kernel。图控制执行，状态机控制事实。

项目目前处于初始架构与实现阶段。`mote_kernel.execution.Graph` 是唯一公开的图构建与执行门面；executor、session、request/result、拓扑和状态 command 均为内部基础设施，不作为并列公共入口。

```python
from mote_kernel.execution import Graph


async def normalize(value: str) -> str:
    return value.strip().lower()


graph = Graph[str, str]("example.normalize")
graph.add_node("normalize", normalize)
graph.set_entry("normalize")
graph.add_edge("normalize", Graph.END)

result = await graph.run("  MOTE  ", run_id="example-run")
assert result.completed
assert result.outputs[0].output == "mote"
```

`Graph.run()` 同时接收上一次调用返回的 authoritative state，以及由同一门面创建的选择性恢复动作。可选的异步 commit 回调会逐条收到 reducer candidate，包括每一个节点 settlement；只有回调精确确认的 state 才能继续执行。本项目不内置具体 Store。

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
