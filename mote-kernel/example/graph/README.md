# Graph examples / 图示例

These runnable examples use only `mote_kernel.execution.Graph`, the public graph composition and execution facade.
The node callables are pure: execution decisions and recoverable values travel through typed graph inputs, outcomes,
state, and resume actions rather than process-local mutable objects.

这些可运行示例只使用公开门面 `mote_kernel.execution.Graph`。节点不修改进程内隐藏状态；执行决定与可恢复值均通过
有类型的 graph input、outcome、state 和 resume action 显式传递。

| Module | Scenario |
| --- | --- |
| `linear_treasure_hunt` | Direct linear activation / 线性直接激活 |
| `conditional_mood_radio` | Conditional route followed by a shared continuation / 条件分支后汇入统一后继 |
| `parallel_detectives` | Parallel fan-out and join / 并行扇出与汇合 |
| `nested_space_mission` | Parent and independently owned child graph runs / 父子图各自拥有运行上下文 |
| `human_in_the_loop` | Interrupt, graph reassembly, and state-only resume / 中断、重新装配与仅凭状态恢复 |

Run a module from the Kernel repository root, for example:

```bash
python -m example.graph.linear_treasure_hunt
python -m example.graph.human_in_the_loop
```

The caller supplies only the root `run_id`. Nested child run identities are internal to their child owners.
