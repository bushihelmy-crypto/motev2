# 架构

Mote Kernel 将领域流程、图执行、状态转换与外部能力实现分离。

- Domain Flow 定义业务流程拓扑。
- Execution 是所有流程复用的唯一图执行底座。
- StateMachine 决定 GraphState 与 DomainState 的合法转换。
- Port 提供可替换的外部能力，不拥有 Kernel 状态。

同一并发 frontier 中的所有节点接收同一个不可变输入快照。节点和 Port 必须只读该快照，通过类型化结果表达变化，Kernel 不会隐式复制任意领域 DTO；DTO 所有者必须将其定义为不可变值。

`mote_kernel.execution.Graph` 是唯一公开的图构建与执行门面。它在第一次 `run()` 前是 topology builder，第一次运行时完成校验并冻结为 immutable compiled runtime。门面实例不保存 run snapshot、session 或 transient output，因此同一张已组装图可以驱动相互独立的 run，而不会成为第二份状态真相。

`Graph.run()` 从显式传入的 authoritative `GraphRunState` 启动或继续运行。failure、interrupt 与 skip 的选择性恢复动作也是同一方法的输入，不存在第二个 resume runner。未传 commit 回调时，`run()` 只在进程内应用 pure reducer；传入回调时，每条 command、reducer candidate 与可选 typed node result 都会交给回调完成 GraphState/DomainState 原子提交，且仅以回调精确返回的 candidate 继续执行。这是提交边界，不是具体 Store 或 durability 承诺。

## Graph Frontier 执行

`GraphRunState` 是 Frontier 结算、资源所有权和 active execution token 的唯一 durable truth。一次原子的
`ClaimGraphExecution` 转换安装 token-only lease，并在需要时同时安装初始 `ResourceSnapshot`。

门面内部的 `GraphExecutor.issue_session()` 是唯一受支持的 session 创建入口。它线性消费 prepared claim 后签发单消费者
`GraphExecutionSession`；内部 session contract 是不可直接构造的协议。每次 `next(authoritative_state)` 先确认上一条 reducer command
产生的精确后继已经提交，再至多交付一个 typed node completion 和一个 `SettleGraphNode`。并发 `next()` 在进入 scheduler 前 fail closed；`aclose()`
幂等，并等待所有 live task 停止。
取消 `next()` 会先完成 close 再传播 cancellation；cleanup 期间再次取消同一 task 也不能中断 close。

`SettleGraphNode` 在同一个新 `GraphRunState` 中原子记录该节点 settlement、释放该节点资源并推进确定性 resource waiter。资源要求只影响
唯一 scheduler 当前可以选择哪些 Pending node。调用方应用 settlement 并确认 successor state 后，即使已有 typed sibling completion
排队，刚 admitted 的 waiter 也会在该次 session step 中立即提交；已经观察到 ordinary error 时则停止全部新 activation。

最后一个节点先持久形成稳定的 `RUNNING + SETTLED` Frontier。Routing 只能基于这个已提交屏障解析，再单独产生
`AdvanceGraphFrontier` 或 `CompleteGraphFrontier` 转换。Session queue 与 task handle 都是 transient runtime facts，不构成 Store、retry
策略、exactly-once 保证或第二套 durable state。

本文件记录稳定架构方向；权威类型与公共契约随实现同步维护。
