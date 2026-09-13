# AgentSession 持久化需求

状态：已实现；Kernel 与仓库级门禁通过，待 code review

最后更新：2026-09-11

## 1. 背景

当前一次 Agent 执行涉及多类运行信息：Hook state、上下文和 Config。它们需要在同一个 Agent
的连续节点和连续 run 之间继续使用，也需要在节点执行成功后和 Graph 的运行状态一起落盘。

本需求将这些信息收敛为一个由 Runtime/Agent 持有的 `AgentSession` 快照。`AgentSession` 是
调用方和节点之间的统一状态载体，不新增第二套 Graph 执行引擎、reducer 或状态仓库。

## 2. 当前固定结构

`AgentSession` 当前只包含以下三个字段：

```text
AgentSession
├── hook_state
├── context
└── config
```

字段规则：

- 字段名固定使用 `hook_state`，不引入 `domain_state` 或其他别名；
- 当前不增加 `graph_state`、`continuation` 或其他预留字段；
- 以后需要扩展时，由单独需求明确增加字段；
- `cursor` 不属于 `AgentSession`，继续由 `ObservationQueuePort` provider 持有和推进。

## 3. 持久化边界

每个 Graph transition 都携带一份完整的 `AgentSession` 快照，并与该 transition 产生的
`GraphRunState` successor 在同一个持久化提交中落盘。一次 invocation/family 只有一个 Session owner，root、
child 和 sibling 不各自保存可写回 family 的 Session 镜像：

```text
旧 GraphRunState + 旧 AgentSession
        ↓
节点执行；节点可明确返回完整 successor
        ↓
新 GraphRunState + commit 边界选定的完整 AgentSession
        ↓
一次原子提交
        ↓
提交确认后替换内存快照
```

必须满足：

1. Graph 状态和 AgentSession 要么一起提交成功，要么都不对外生效；
2. 持久化确认前，Runtime 不得把内存中的 session 或 Graph 状态推进到 successor；
3. 提交结果未知时，沿用现有 reconcile 机制确认同一个提交，不另起 Session 提交；
4. 恢复时读取同一个已确认边界对应的 Graph 状态和 AgentSession，不能分别选择不同 revision；
5. 持久化只保留每个已确认节点边界的当前完整快照，不建立 delta 链或要求重放历史 patch。

这里的“全量”指每个确认边界写入当前完整 `AgentSession`，不指保存所有历史版本。无 successor 的节点可以
继续使用激活时看到的输入，但在真正提交时必须重新读取 family owner 的当前值；只有显式完整 successor 才推进
owner，多个 successor 按 durable receipt 的确认顺序生效。

当前实现把 `AgentSession` 的 hook/context 交给调用方注入的
`AgentSessionCodec` 编成不透明 payload；Config 只写入 `GraphConfigCursor`。编码后的
`EncodedAgentSession` 作为 `GraphPersistenceCommit.agent_session` 的一部分，与
`GraphRunState` 使用同一个 commit receipt。恢复时先按 checkpoint 的 Config cursor
解析 Config，再由同一个 codec 还原 hook/context。

`AgentSession.config` 所引用的 immutable Config snapshot 必须由 Config owner 在首次 `AgentStart` 或节点产生
Config successor 之前保存到 `ConfigSnapshotStore`；Kernel/Agent 不代为保存，也不把 Graph commit 成功视为该
snapshot 已存在。缺少快照时，恢复按既有 Config store 契约失败，不走 latest 或隐式补存路径。

## 4. 更新责任

`AgentSession` 的字段更新由具体节点或 Runtime 明确负责，Kernel 不根据节点输出猜测或自动合并
业务字段。节点若要推进 Session，必须显式返回完整 successor；Kernel 只搬运和提交这份 successor。

- 普通 callable 节点通过 `Graph.success(..., session=AgentSession(...))` 返回完整 successor；typed
  节点通过 `Graph.SessionActivation(value, session)` 返回完整 successor；不带 successor 的节点只继承当前
  owner Session，不会隐式修改三个字段；
- Config successor 由产生它的节点或 Runtime 明确写入完整下一份 Session；在会改变该 scoped Graph
  状态的 transition 中，`session.config` 必须与该 transition 的 Config cursor 一致。嵌套 child
  可以先确认自己的新 Config，再在等待恢复时把这份 Session 交给父 owner；此时父 root state 仍可保留旧
  cursor，checkpoint 只要求 Session cursor 能在整个 family 的某个已确认 scope state 中找到。不能只返回
  `ConfigActivation` 让 Kernel 猜测 hook/context；
- Graph 在同一个原子 commit 中提交 Graph state 和 Session。family owner 在同一串行边界内完成 Session 选择、
  commit/reconcile、owner 替换和 state/frame 安装；只有 receipt 精确确认后，`AgentResult.session` 才投影这份最后
  确认的快照；
- 持久化层只负责接收已形成的完整 session，并把它和 Graph 状态放入同一提交边界。

`AgentStart.session` 是一次新 run 的 session 输入；已确认的 session 通过
`AgentResult.session` 交还给 Runtime。`AgentResume` 不接受调用方覆盖 session，而是只使用
checkpoint 中已确认的那一份。Runtime 若要推进 hook/context/config，先构造新的完整
`AgentSession`，再交给下一次 run；同一 run 内则由节点显式返回 successor。Kernel 不从 Graph output 猜测字段更新。

## 5. 与现有 Graph 机制的关系

以下机制保持不变：

- `execution.Graph` 仍是唯一 Graph 组合和执行入口；
- `ReActNode` 继续承担现有 Observe、Think、Act 拓扑；
- 不新增 `ReActChain` 或另一条执行路径；
- Graph 的 compiler、scheduler、reducer、commit、recovery 和 authority 机制保持原有职责；
- `GraphRunState` 仍由 execution/state owner 负责，不在 `AgentSession` 内复制一份；
- Hook 不获得完整 `AgentSession` 的任意访问权，仍遵守领域 Hook 的 typed boundary；
- Observe 的 queue cursor 仍由 queue provider 自己维护，不迁移到 session。

Session 的作用是把调用方需要跨节点、跨 run 保存的三个值收敛成一个快照；它不是新的 Graph
状态模型，也不是隐藏在 Graph 成员中的可变缓存。

恢复时，历史 graph input/publication frame 只恢复业务值及其历史 Config provenance，不把当前 Session
倒灌到历史 frame；当前 family owner Session 由 Runtime/GraphRecovery 注入后续节点输入。这保证 Config v1 的历史
证据不会与当前 Session 的 Config v2 发生伪冲突，同时仍由同一个 checkpoint/commit envelope 绑定完整 Session。

## 6. Config 的持久化表示

内存中的 `AgentSession.config` 仍是当前运行所需的完整 Config。持久化时不得直接序列化包含
Port、Invocation 等运行能力的 Config 对象；持久化使用现有 Config snapshot 的 identity、revision
和 digest。恢复时通过现有 Config store/resolver 重新得到内存 Config，并校验它与已确认 Graph
状态引用的是同一 Config snapshot。

这不改变 `AgentSession` 的逻辑字段，只规定 Config 的 durable representation。

## 7. 跨 run 语义

Runtime 持有当前已确认的 `AgentSession`：

```text
run1 使用 session-1
run1 节点提交 session-2
Runtime 接收已确认的 session-2
run2 使用 session-2
```

同一个 Agent 的后续 run 只有在 Runtime 传入上一已确认快照时才继续使用该状态。Graph 或 Hook
不得因为对象实例相同而私自复用上一 run 的 session。

## 8. 非目标

- 不实现 `AgentSessionDelta`、patch merge、delta replay 或第二套 session reducer；
- 不把 session 放进 Graph/Hook 成员作为隐式可变共享状态；
- 不让 Graph 自动推断节点应该如何更新 `hook_state`、`context` 或 `config`；
- 不把 GraphRunState、continuation 或恢复协议提前塞进 AgentSession；
- 不修改 queue provider 的 cursor owner 设计；
- 不为本需求创建 `ReActChain`。

## 9. 验收标准

实现完成后至少验证：

1. 节点成功提交时，GraphRunState 和完整 AgentSession 使用同一 commit identity 原子落盘；
2. 任一方提交失败或提交结果未知未被确认时，内存快照不提前推进；
3. 恢复加载的 Graph 状态和 AgentSession 来自同一已确认边界；
4. 连续 run 使用 Runtime 交接的上一份完整 session；
5. 同一 Graph 的并发 run 不共享隐式 session；
6. 不存在 delta merge、旧兼容路径、ReActChain 或第二套状态 owner。
7. 并行 root/child transition 在 snapshot、journal、明确失败和 Unknown/reconcile 间隙下，都恢复 family
   owner 最后一份已确认 Session；陈旧 inherited Session 不得覆盖显式 successor。
