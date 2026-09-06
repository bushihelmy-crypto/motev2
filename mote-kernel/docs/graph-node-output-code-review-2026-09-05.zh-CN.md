# Graph feedback 删除与 node output 因果前驱改造代码评审（2026-09-05）

状态：**代码 review 完成；发现 1 项文档迁移 finding；本轮未运行测试或门禁**

评审基线：当前提交 `3a375c0 feat(graph): add causal predecessor output bindings`。本记录只审查该提交带来的 Graph
声明、编译、routing/materialization、State/recovery 接缝；工作树中的其它领域改动不改变本结论。

本记录针对当前工作树中 Graph 的增量：删除旧 `feedback` 声明/编译路径，并把一参数
`Graph.node_output("name")` 收敛为“本次实际控制前驱的 output”，同时保留两参数固定 producer
形式。评审按“先设计与完整调用链、后门禁证明”的顺序进行；只记录能由正常公开 Graph 调用链
支持的真实问题，不把纯理论 hostile snapshot 或尚未承诺的持久化实现直接升级为 finding。

## 1. 范围与排除项（初始记录）

纳入：

- `execution.Graph` facade 的 `node_output` 重载及 `feedback` 删除；
- graph ports/compiler/topology 的 predecessor binding lower；
- routing、resume materialization/admission 对实际 predecessor cause 的解析；
- 对应 Graph/State/typing 测试与旧 feedback 测试删除是否保留有效语义。

当前工作树中 Failover、外部 port 领域和无关文档迁移不作为本轮 Graph 结论；本次公开 API 对应的根 README
与 Graph 示例说明纳入审查，因为它们直接构成迁移后的用户契约。具体 durable store、crash-safe durable
concrete-value recovery 仍按既定边界留给后续持久化适配器，本轮不以此要求生产代码增加第二路径。按用户要求，
本轮不执行测试、coverage、`make check` 或 pre-commit。

## 2. 已确认的实际调用链

```text
Graph.node_output(one/two args)
  -> normalize_input_bindings
  -> compile_graph
       -> activation gates + control proof
       -> CompiledPredecessorInput(target, local input, allowed NodeOutputPort[])
  -> planner/frontier materialization
       -> state frontier cause -> predecessor_source_for_cause
       -> exact predecessor publication frame
  -> node settlement / reducer
  -> GraphTransition + typed write set
  -> Graph.Commit exact candidate acknowledgement
```

`GraphRunState.frontier[*].cause` 仍是本轮实际 predecessor 的唯一运行时事实；compiler 只保存
可能的 source 集合，materialization 不维护 latest-value、cursor 或 feedback 第二状态。

## 3. 审查台账

| 检查项 | 当前结论 | 证据 |
| --- | --- | --- |
| 旧 feedback 类型、符号、执行路径是否从生产代码删除 | 生产代码通过；中文文档存在 finding | `src`/`tests` 中未发现旧符号；`README.md` 与 `example/graph/README.md` 已同步新 API，`README.zh-CN.md` 仍把已删除的 `Graph.feedback(...)` 写成当前 API |
| 一参数/两参数 `node_output` owner 与 graph-output 边界 | 通过 | facade 的两个 overload 分别产生 `PredecessorOutputRef`/`NodeOutputRef`；causal ref 只接受为 node input，graph-output normalizer 明确拒绝 |
| compiler 对所有可能 predecessor 的 output/type/gate 证明 | 通过 | `_resolve_predecessor_output` 枚举每个 activation gate 的 source，要求同名 output 和 exact nominal type；ambiguous gate 继续由既有 control-flow proof 拒绝 |
| causal input 没有控制前驱时是否会绕过编译期边界 | 通过 | 普通 node-output 依赖在无 incoming control edge 时先被 compiler 拒绝；只含 causal binding 的节点会被识别为 automatic entry，随后由 START-target 规则拒绝；正常公开构图不会进入空 predecessor 集合 |
| runtime cause 是否只能选择上一 superstep 的已提交 publication | 通过 | `predecessor_source_for_cause` 要求 routed、非 Join、单 reference、`target-1` 坐标、State ledger evidence 和 compiler source 集合；只生成 exact publication coordinate |
| 普通固定 node output 与 predecessor input 混用 | 通过 | fixed binding 继续走 `PublicationSelection`；causal binding 单独从 State-owned cause 选 source；两者在同一 materialization frame 合并，不共享隐式 latest-value |
| nested graph、conditional branch、loop、Join 异常边界 | 通过 | nested boundary、互斥 conditional route、单/多节点 loop、Join target/下游和 missing publication 均沿同一 compiler/routing/materialization 链复核；Join 本身不被隐式当作单值 predecessor |
| State/reducer/GraphTransition 唯一提交切口 | 通过 | 本增量未新增 State owner、runner、reducer 或 commit 路径；candidate、settlement、publication 仍经 `GraphTransition`/`Graph.Commit` exact acknowledgement 后前移 |
| 测试删除/新增是否保留有价值语义 | 通过 | 旧 feedback 私有结构白盒测试随类型和第二路径删除；immediate publication、cause、missing evidence、override、nested、lost acknowledgement、failure/limit 等行为已迁移到 predecessor 契约测试；无 legacy alias 或双执行路径 |

## 4. Findings

已确认一项迁移 finding；其余项目已沿公开调用链复核通过，不能仅凭指标或理论构造升级：

### GNO-1（中文公开文档契约漂移，需在交付前修正）

`README.zh-CN.md:34-37` 仍示范并描述 `Graph.feedback(initial=..., repeat=...)`，而当前 `Graph` facade 已删除该方法，
生产类型和 compiler 也不再接受 `FeedbackInputBinding`。这是普通用户直接遵循中文 README 即可触发的确定性 API 失败。
当前 `README.md` 已改为说明一参数/两参数 `node_output`，`example/graph/README.md:76-142` 也已补齐两种形式、Join/START
边界与因果读取语义，因此不再把它们列为 finding。中文根 README 仍未同步，违反本次“一次性迁移生产代码、测试和示例、
零 legacy alias/误导入口”的交付约束。

这不是网络攻击面，也不是要求引入兼容层；应只更新 `README.zh-CN.md`，移除旧 `feedback` 当前 API 叙述，并与已同步的
英文 README/示例保持“两参数固定 producer、一参数实际前驱”的说明，同时保留“绑定不创建控制边”的正确边界。

在中文 README 修正前，本轮 Graph 迁移 review 不能宣称“文档与公开 API 完整一致”；该 finding 不要求修改生产执行路径。

以下边界已核对但不升级为 finding：

- 显式 `None` 作为第二参数时，当前实现按一参数形式处理；这是静态 overload 已排除的低级调用，不是正常公开契约中的可达路径；
- positional-only overload 对未声明的 keyword 调用没有兼容承诺；仓库没有该公开用法；
- malformed internal compiled objects、hostile snapshot、伪造 durable ledger、ghost activation 和纯理论分布式攻击面不纳入本轮；
- 具体 persistence adapter、durable loader/reconcile，以及 crash-safe durable concrete-value recovery 尚未实现，但按既定边界由后续持久化适配器负责，不阻塞本轮内存 Graph review；
- 不因复杂度指标命中而机械拆 helper、增加转发层或建立第二状态机。

## 5. 测试迁移审计

本次迁移把旧 `feedback(initial, repeat)` 的有价值行为改写为显式初始化节点加普通控制边，再由一参数
`Graph.node_output("name")` 读取实际前驱。保留的语义包括：

- 初始化 publication 与后续每一拍 immediate predecessor publication 的精确读取；
- 多节点循环、互斥分支共享节点、同一 source 的互斥 route 和 nested Graph 的局部 scope；
- failure terminal、execution limit、missing exact evidence、cached frame/override 不得遮蔽 cause，以及 lost acknowledgement 后的 transient frame 边界；
- compiler 对缺失 output、exact type 冲突、START target、Join 隐式单值选择和可并存多入边的 fail-closed 错误优先级。

只验证已删除 `FeedbackInputBinding`/`CompiledActivationRule`/seed-repeat partition 私有结构的旧白盒测试没有保留；没有为
legacy test 增加 alias、wrapper 或兼容执行分支。

## 6. 评审结论（代码与文档分开）

从设计和完整调用链看，Graph 生产代码满足当前“一项规则一个 owner、一个 execution engine、一个 State/cause、一个
publication/commit 接缝”的要求。本轮没有发现会由正常公开 Graph 调用触发的 runtime correctness、状态唯一性、异常边界或
恢复路径问题。

但完整交付仍有 GNO-1：根目录中文 README 必须删除已不存在的 `Graph.feedback(...)` 当前 API 描述，并与已同步的英文
README/示例保持一参数/两参数 `node_output` 说明。这个 finding 不应通过 legacy alias 修复；在中文文档同步前，不能宣称
“公开契约与迁移示例完全一致”。
