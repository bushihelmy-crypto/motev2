# 架构

Mote Kernel 将领域流程、图执行、统一状态与外部能力实现分离。

- Domain Flow 定义业务流程拓扑。
- Execution 是所有流程复用的唯一图执行底座。
- State transition 统一决定图推进、节点结果和业务变化的合法转换。
- Port 提供可替换的外部能力，不拥有 Kernel 状态。

当前唯一的权威状态类型是不可变的 `GraphRunState`。现阶段它记录 graph run 的执行事实（frontier、settlement、
routing、lease、resource、恢复坐标和 revision）。以后增加节点/Hook 结果或业务事实时，继续扩展这个类型，
不另建状态模型；状态只有一个 owner 和一个原子提交边界。

同一并发 frontier 中的所有节点接收同一个不可变输入快照。节点和 Port 必须只读该快照，通过类型化结果表达变化，Kernel 不会隐式复制任意领域 DTO；DTO 所有者必须将其定义为不可变值。

`mote_kernel.execution.Graph` 是唯一公开的图构建与执行门面。它在第一次 `run()` 前是 topology builder，第一次运行时完成校验并冻结为 immutable compiled runtime。门面实例不保存 run snapshot、session 或 transient output，因此同一张已组装图可以驱动相互独立的 run，而不会成为第二份状态真相。

`Graph.run()` 从显式传入的 authoritative `GraphRunState` 启动或继续运行。failure、interrupt、skip、节点结果和
Hook 变化都通过同一个 `GraphRunCommand` 入口处理，不存在第二个状态或 resume runner。未传 commit 回调时，
`run()` 只在进程内应用纯状态转换；传入回调时，每条 command、candidate 和完整 typed write-set 都交给回调
完成统一状态的原子提交，且仅以回调精确返回的 candidate 继续执行。这是提交边界，不是具体 Store 或
durability 承诺。

提交边界的 typed `GraphTransition`、`GraphCommitWriteSet`、exact acknowledgement 和确认后的 frame staging 由
`execution/commit.py` 作为一个完整 owner 管理；`execution/family_driver.py` 只负责 family owner 的驱动、child handoff、
并发清理和结果投影，不复制提交规则或建立第二个 runner。两者通过窄的内部调用连接，公共入口仍只有 `Graph`。

## State 包与所有权

`src/mote_kernel/state/` 是状态事实与状态转换的唯一 owner。当前具体实现位于 `state/graph_state/`；这只是
代码路径，不代表第二种状态。对外设计保持最小且统一：

- `state/graph_state/model.py` 定义不可变的 `GraphRunState` 及其值对象；
- `state/graph_state/command.py` 定义封闭的类型化 `GraphRunCommand` union；
- `state/graph_state/reducer.py`（`reduce_graph_run`）是唯一纯 dispatch 入口；
- validation、identity、frontier/resource/routing 值对象和 transition result 都仍属于同一个 `state` owner。

这些文件只是按职责组织实现，并不代表多个运行时状态。节点或 Hook 只能返回类型化 result/command；只有
`reduce_graph_run` 能生成下一个 `GraphRunState`。任何 flow、execution session 或 extension 都不得维护平行快照、
第二个 reducer 或另一条状态存储路径。

底层存储可以分别加载执行记录和结果记录，但必须通过共同的 `state_version` / `commit_id` 合并：

```text
执行记录 loader ─┐
                 ├─ 校验同一版本 ─> GraphRunState 内存投影 ─> 节点 / Hook
结果记录 loader ─┘
```

不能让两份独立快照分别对外可见。Role 配置仍由 Role/Config owner 管理，不是统一状态中的第二个状态模型。

## Graph Frontier 执行

统一 `GraphRunState` 中的执行字段是 Frontier 结算、资源所有权和 active execution token 的唯一 durable truth。一次原子的
`ClaimGraphExecution` 转换安装 token-only lease，并在需要时同时安装初始 `ResourceSnapshot`。

门面内部的 `GraphExecutor.issue_session()` 是唯一受支持的 session 创建入口。它线性消费 prepared claim 后签发单消费者
`GraphExecutionSession`；内部 session contract 是不可直接构造的协议。每次 `next(authoritative_state)` 先确认上一条 reducer command
产生的精确后继已经提交，再至多交付一个 typed node completion 和一个 `SettleGraphNode`。并发 `next()` 在进入 scheduler 前 fail closed；`aclose()`
幂等，并等待所有 live task 停止。
取消 `next()` 会先完成 close 再传播 cancellation；cleanup 期间再次取消同一 task 也不能中断 close。

`SettleGraphNode` 在同一个新 `GraphRunState` 中原子记录该节点 settlement、节点确认结果、释放该节点资源并推进确定性 resource waiter。资源要求只影响
唯一 scheduler 当前可以选择哪些 Pending node。调用方应用 settlement 并确认 successor state 后，即使已有 typed sibling completion
排队，刚 admitted 的 waiter 也会在该次 session step 中立即提交；已经观察到 ordinary error 时则停止全部新 activation。

最后一个节点先持久形成稳定的 `RUNNING + SETTLED` Frontier。Routing 只能基于这个已提交屏障解析，再单独产生
`AdvanceGraphFrontier` 或 `CompleteGraphFrontier` 转换。Session queue 与 task handle 都是 transient runtime facts，不构成 Store、retry
策略、exactly-once 保证或第二套 durable state。

Frame/publication 只保存执行所需的值或引用以及对应的 `state_version` / activation 坐标，不拥有第二份事实。
本文件记录稳定架构方向；权威类型与公共契约随实现同步维护。
