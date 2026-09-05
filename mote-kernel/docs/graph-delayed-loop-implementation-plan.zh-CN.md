# Graph 因果前驱输出实施说明

状态：实现与 Kernel 全量门禁已完成，待代码评审；本文取代旧的 seed/repeat feedback 方案。

本文只定义 Graph 如何读取“本次实际控制前驱”的输出。失败重试策略仍只属于
`mote_kernel.failover`，Graph 不重试失败节点，也不创建 loop 专用 runner、State 或缓存。

## 1. 最终公共 API

`mote_kernel.execution.Graph` 保留一个 `node_output` 名称，用重载表达两种清楚的读取方式：

```python
Graph.node_output("invoke", "hook_request")  # 固定读取 invoke 的 hook_request
Graph.node_output("hook_request")            # 读取本次实际控制前驱的 hook_request
```

一参数形式中的参数是前驱的输出名，不是 consumer 的本地输入名；二者可以不同：

```python
graph.add_node(
    "hook",
    hook,
    inputs={"request": Graph.node_output("hook_request")},
    outputs={"result": HookResult},
)
```

不公开 `OneOf`、source map、route map 或另一套 loop API。旧 `Graph.feedback(initial=..., repeat=...)`
及其兼容入口全部删除。

## 2. 为什么不再建模 seed/repeat

第一次输入不是循环读取规则的一部分，而是图中的一个普通初始化步骤：

```text
START -> Initialize -> Loop -> Loop -> ... -> END
```

`Initialize` 产出第一次值；此后每个节点都只读取真正激活自己的前驱。这一模型同时覆盖：

- 单节点循环：`Initialize -> A -> A`；
- 多节点循环：`Initialize -> A -> B -> C -> A`；
- 互斥分枝汇入共享节点：`Left -> Hook` 或 `Right -> Hook`；
- nested Graph 作为共享节点；
- Failover 中多个互斥步骤汇入同一个 Hook 子图节点。

初始化、循环和分枝继续使用普通 Graph 节点与边，不在 value binding 中复制一份控制流程。

## 3. 唯一真相与 owner

```text
Graph.node_output("name") declaration
             |
             v
compiler 根据 activation gate 枚举合法前驱并核对输出类型
             |
             v
CompiledPredecessorInput(target, local_input, allowed_output_ports)
             |
             v
GraphRunState.frontier[*].cause 中的唯一 ActivationReference
             |
             v
对应 activation 的唯一 ConfirmedPublication
```

- compiler 拥有“哪些前驱可能合法激活 target”的静态事实；
- `GraphRunState` 拥有“本轮究竟是哪一个前驱激活 target”的运行事实；
- publication descriptor 和 activation identity 共同确定具体值；
- materialization 只组合这三项事实，不维护最新值、source cursor 或第二份路由状态。

## 4. 编译期规则

一参数 `node_output` 必须同时满足：

1. target 不能从 `START` 激活；第一次值必须来自显式初始化节点；
2. target 至少有一个控制前驱；
3. target 的每个 activation gate 都必须恰好包含一个前驱；显式 Join cause 不能隐式选一个值；
4. 每个可能前驱都声明同名输出；
5. 这些输出的 nominal Python type 必须完全相同；
6. 多条入边必须已由现有 control-flow proof 证明互斥，否则仍要求显式 Join 并编译失败；
7. 普通两参数 `node_output` 仍建立固定 producer 的数据依赖，普通 data cycle 仍编译失败。

编译产物只保存内部 `CompiledPredecessorInput`：target、本地 input 名和排序后的合法
`NodeOutputPort`。它不保存 initial/repeat 分区、相对偏移配置或另一份 activation rule。

### Join 边界

```text
A -> C
B -> C
```

若 A/B 是同一次路由决策的互斥分支，现有控制证明允许 C，并由实际 cause 选择值。若 A/B
可能同时到达，编译器要求显式 Join；而 Join 只负责控制等齐，不猜测 C 应读取 A 还是 B。
需要两个值时应显式写两个固定 producer binding：

```python
inputs={
    "left": Graph.node_output("a", "value"),
    "right": Graph.node_output("b", "value"),
}
```

## 5. 运行时规则

`predecessor_source_for_cause` 只接受下列精确事实：

- target superstep 是正整数；
- cause 是 `RoutedActivationCause`；
- cause 不是 Join，并且只含一个 `ActivationReference`；
- reference 与当前 run 相同，且其 superstep 精确等于 target superstep 减一；
- reference 已存在于 `GraphRunState.settled_activations`；
- reference 的 node 是 compiler 列出的合法 source。

满足后，运行时按以下 exact coordinate 读取：

```text
scope/run
+ cause.reference(GraphRunId, predecessor_superstep, predecessor_node)
+ predecessor publication descriptor
```

禁止扫描“最新同名输出”、退回 graph input、采用更老 publication 或按 node id 猜测。多个历史
activation 同时存在时，cause 仍只指向一个具体版本。

## 6. 恢复与输入覆盖边界

因果输入必须可从权威 State cause 重新推导，因此：

- 缺少 authoritative `GraphRunState` 时不能判断可用性；
- target 不在当前 frontier 时拒绝；
- START、wrong run、wrong superstep、未提交 predecessor、非法 source 或 Join cause 均 fail closed；
- 缺少 exact publication 时返回 typed value-unavailable，不搜索其他历史值；
- resume input override 不能覆盖因果输入；
- 已缓存的 node input frame 不能替代 cause 指向的 publication。

publication history window 对这类 binding 保留相对一拍的读取需求。这只是进程内 retention 计算，
不宣称数据库恢复已经实现。

## 7. 原子提交接缝

当前 Graph 已将 candidate State 与 publication 放进同一个 typed `GraphCommitWriteSet`，并遵守：

```text
构造 candidate State + evidence write set
  -> 调用 Graph.Commit
  -> exact candidate 确认成功
  -> 才把 State 和 publication 安装进 Python snapshot
```

所以 causal input 的 State 身份和具体值共享同一个原子提交切口。仓库当前没有 concrete persistent
Store；未来接数据库时必须让后端原子确认同一 write set，并为 state-led recovery 提供 exact
publication reader。不能在 State 已提交后另存值，也不能增加一套 durable-only 执行路径。

## 8. Failover 共享 Hook 的目标形状

Failover 后续组图可以只定义一个 Hook 子图节点。每个互斥业务节点结束后进入 Hook，Hook 通过
一参数 `node_output` 读取实际前驱产出的统一 `hook_request`：

```text
Initialize -> InvokeOnce ------> Hook
                ^                 |
                |                 v
PrepareNextAttempt <---------- ObserveAndRoute
                |                 |
                +-------> Hook <--+---- Reconcile
```

Hook 后继续使用普通 router edge；router 根据 Hook 前驱携带的 typed request/result 决定下一目标。
这些路径必须由拓扑证明互斥。若构图真的允许多个前驱同时到达 Hook，则仍要求显式 Join，而不是
让一参数 output 随机选值。

## 9. 迁移与删除范围

本次一次性迁移包括：

- facade：删除 `Graph.feedback`，为 `Graph.node_output` 增加一参数/两参数重载；
- declaration：以 `PredecessorOutputRef` 取代 `FeedbackInputBinding`；
- compiler/topology：删除 feedback gate partition、activation rules 和 selection offset；
- routing/materialization：统一按 State-owned cause 选择 exact predecessor publication；
- resume admission：拒绝对 predecessor-bound activation 使用 override；
- 测试与 typing fixtures：迁移有效的恢复和伪造防御，删除只验证旧私有结构的白盒断言；
- 复杂度 ratchet：按真实净结构更新，不用 helper 或 suppressions 隐藏变化。

不保留 alias、wrapper、legacy test 门禁或双执行路径。

### 9.1 测试迁移审计

2026-09-05 对工作树中的测试删除逐项复核。tracked diff 删除了 86 个旧测试函数，同时在已有文件中
增加 36 个新契约测试，并在两个新的 predecessor-output 测试文件中增加 23 个测试函数；另有四个
Pyright 正负 fixture。测试函数净减少 27 个不代表行为覆盖减少，原因分为三类：

1. `resume_input`、resume admission、routing、State cause、公开 facade 和 typing 行为已按新名称或新模型迁移；
2. 只验证 `FeedbackInputBinding`、`CompiledActivationRules`、seed/repeat gate partition 及其私有 helper 的白盒测试随
   生产类型和第二执行路径一起删除，不能作为 legacy 门禁恢复；
3. 审计发现的真实缺口已按新契约补回：无入口图、显式入口不可达节点、exact causal publication 的 availability、
   Join 产物与普通来源不得隐式汇入、同一前驱的多个互斥 route、nested Graph 作为 causal consumer 和 producer，
   以及丢失 advance acknowledgement 后的 exact publication 边界。

固定 producer 经显式 Join 的行为没有随 feedback 测试删除；公共运行测试仍覆盖普通 Join 值读取、不同 sibling
完成顺序、interrupt/resume、falsy publication 和 retained arrival。没有为旧 API 保留生产兼容代码或 legacy-only 测试。

## 10. 验收矩阵

必须覆盖：

- 一参数/两参数公共重载的正负类型检查；
- 显式 initializer + 单节点循环；
- 多节点循环每一跳读取实际前驱；
- 多个互斥分支共享一个 Hook；
- 同一前驱的多个互斥 route 共享一个 exact causal publication；
- consumer 本地 input 名与 predecessor output 名不同；
- nested Graph 作为 causal consumer 与 causal producer；
- missing output、类型冲突、START target、Join cause 和非互斥多入边的编译拒绝；
- 无入口、不可达节点，以及 Join 产物和普通来源隐式汇入的编译拒绝；
- wrong run/superstep/route、ghost settlement、旧 publication、missing publication 的运行拒绝；
- availability、materialization 都只认 exact publication，override/cached frame 不得遮蔽它；
- failure terminal、terminal route、execution limit 和 lost acknowledgement 边界；
- Ruff、Pyright strict、复杂度、全量测试、coverage、build 和仓库 pre-commit。

本轮 Kernel 验证结果：1455 项测试通过，statement/branch coverage 均为 100%；Pyright strict 无错误，
complexity ratchet 与 zero-debt health 通过，wheel/sdist 构建和 `twine check` 通过。

最终完成标准不是仅让门禁变绿，而是公共 API 只剩一个清楚的 output 概念，控制来源只有 compiler
和 State cause 两个正交 owner，具体值只有 canonical publication 一个 owner。
