# 额外高召回指标：跨模块运行时调用耦合审查

## 文档定位

`complexity-49-simplification-review.zh-CN.md` 主要审查函数、类、数据结构和调用链本身的复杂度。本文件补充本轮新增的
**跨模块运行时调用耦合**指标，记录它为什么存在、如何统计、哪些地方需要人工看，以及它不能替代什么。

这条线的目标不是证明“模块之间不应该互相调用”，而是尽量不漏掉以下变化：一个模块开始依赖更多模块、同一边界上的
具体符号越来越多，或者高层模块开始直接触碰低层实现。所有候选都先由门禁圈出，再由代码评审决定保留、收敛还是重构。

## 审查与治理原则

- **唯一事实**：模块边界由语义索引中的真实 source/target symbol 推导，不另建一套依赖注册表。
- **高召回优先**：宁可把合理的调用也列出来，不用过强的命名规则提前过滤。
- **人工定性**：指标是审核线索，不是“跨模块调用 = 错误”的自动裁决。
- **保持唯一执行路径**：为了降低数字，不能增加第二 runner、兼容 wrapper、宽泛 `Context` 或隐藏状态。
- **按完整链路判断**：一次调用是否合理，要结合 owner、输入输出、异常边界、提交顺序和调用方职责判断，不能只看一行代码。
- **ratchet 不放大债务**：已有基线可以暂时保留；新增或增长的边必须触发审核，实际减少后立即下调上限。
- **行为由测试证明**：边界耦合门禁不验证业务结果、并发时序或多个调用组合，这些仍由单元、集成和恢复测试负责。

最终判定顺序仍然是：先看设计是否清楚、单一且必要，再用指标证明没有新增耦合；不能反过来把数字当成架构质量结论。

## 结论先行

本轮新增了三个互相独立、同时进入 `complexity-ratchet` 的指标：

1. `cross_module_call_edges`：跨模块的不同 source-symbol → target-symbol 边数。
2. `cross_module_call_pairs`：不同 source-module → target-module 组合数。
3. `max_runtime_module_fan_out`：单个 source module 调用的不同 target module 的最大数量。

启动时审查基线（2026-09-01，排除后来出现的 events 开发文件）为：

```text
cross_module_call_edges       715
cross_module_call_pairs       216
max_runtime_module_fan_out     24
```

本轮分批审查进行期间，工作树又出现了尚未纳入上述基线的 `mote_kernel.events` 生产模块及其测试。当前实时报告为
`cross_module_call_edges=730`、`cross_module_call_pairs=221`；本文件先继续按启动时的 `715/216` 基线审查，新增的 5 个
事件模块对另列批次，不直接调高 ratchet 上限。这样可以把“已有用户改动”与“本轮审查结论”分开记账。

这些数字说明内核确实存在较多跨模块协作，但**不说明有 715 个问题**。例如 `Graph` facade、family driver 和错误类型
模块天然会有较多边；它们是否合理，要看依赖方向和职责，而不是看是否跨过了文件边界。

当前还有两个正面信号：报告中统计到的跨模块调用站点共 893 个，来自 37 个 source module、指向 52 个 target module；没有发现
成对的反向运行时模块边（A → B 与 B → A），同时静态 import cycle 也是 0。它们只能降低一部分架构风险，不能代替逐项审查。

## 门禁基线与判定口径

### 三项指标的含义

| 指标 | 统计单位 | 当前值 | 大白话 |
|---|---:|---:|---|
| `cross_module_call_edges` | 去重后的 symbol 边 | 715 | 具体哪个函数/方法在调用另一个模块的哪个 callable |
| `cross_module_call_pairs` | 去重后的模块对 | 216 | 两个模块之间是否形成了运行时依赖边界 |
| `max_runtime_module_fan_out` | 单模块的目标模块数 | 24 | 哪个模块像“总调度台”一样依赖了最多其他模块 |

`complexity-report` 的每一行还会显示两个辅助数字：

```text
source_module -> target_module symbol_edges=<不同符号边数> call_sites=<AST 调用点数>
```

`symbol_edges` 是 ratchet 使用的去重边数；`call_sites` 用来判断某个边界是偶尔调用，还是在很多位置重复穿透。

### 纳入范围

语义索引已经静态解析出调用目标时，以下 target kind 会纳入：

- 普通函数、方法和嵌套函数；
- 类构造调用；
- `NewType` 调用。

调用必须来自生产代码，source 与 target 的 module 必须不同。相同模块内的调用留给原有 call graph 指标统计；测试代码不混入生产
基线，以免测试布局改变就造成门禁噪声。

### 排除范围

- 无法静态解析目标的动态分发不会伪造一条跨模块边，而由 `ambiguous_internal_dispatches` 单独报告；
- import、继承、注解、字段读取和普通属性访问不算运行时 callable call，分别由 import/语义引用分析覆盖；
- 外部第三方包不属于本生产模块图；
- 这条指标不计算传递闭包，不把 A 调 B、B 调 C 自动展开成 A 调 C。

这种口径保持了“已解析边尽量全收、未知边另行报警”的高召回策略，同时避免把静态猜测当成事实。

## 统计流程

```text
生产源码 AST
  -> SemanticIndex 解析每个调用的 source / target
  -> 保留 runtime-capable target
  -> 排除测试、同模块和未解析调用
  -> 按 (source module, target module) 分组
     -> 去重 symbol edges
     -> 统计 call sites
     -> 计算 source module fan-out
  -> complexity-report 输出候选
  -> complexity-ratchet 阻止指标增长
```

统计实现集中在 [quality_analysis.py](/home/longert/motev2/mote-kernel/tests/architecture/quality_analysis.py:704)，快照和报告接线在
[complexity_rules.py](/home/longert/motev2/mote-kernel/tests/architecture/complexity_rules.py:358)，上限位于
[pyproject.toml](/home/longert/motev2/mote-kernel/pyproject.toml:138)。没有新增生产运行时依赖；这是一条测试侧的静态治理线。

## 当前基线的人工审核入口

### 高 fan-out 模块（按不同目标模块数排序）

下表只是审核入口，不是风险排名。`facade` 的高 fan-out 很大程度上是它作为唯一公开门面的正常代价；真正要问的是新增边
是否绕过了既有 owner。

| source module | 目标模块数 | symbol edges | call sites | 初步审核问题 |
|---|---:|---:|---:|---|
| `execution.facade` | 24 | 96 | 117 | 是否仍由唯一 public facade 编排，还是开始承载领域规则？ |
| `execution.family_driver` | 19 | 107 | 117 | owner、child handoff、cleanup 是否仍集中在同一 owner？ |
| `execution.invocation` | 16 | 65 | 91 | admission/recovery 的边界是否被调用方绕开？ |
| `execution.engine.recovery` | 15 | 49 | 59 | proof-only 路径是否误变成第二执行路径？ |
| `execution.engine.resume_admission` | 13 | 34 | 45 | resume 证据和唯一 reducer owner 是否仍然清楚？ |

其余模块的完整模块对清单由 `make complexity-report` 输出。每个 PR 不需要重新解释全部 216 对，先筛选本次 diff 新增或触碰的
行，再查看它所在 source module 的 fan-out 变化。

### 调用密度较高的模块对

当前 `call_sites` 较多的边包括：

| 模块对 | symbol edges | call sites | 审核提示 |
|---|---:|---:|---|
| `execution.invocation → execution.errors` | 14 | 37 | 多个异常出口可能是必要的；确认没有把错误策略散落到调用方。 |
| `execution.family_driver → execution.errors` | 21 | 29 | 清理、child 和 owner 错误是否保持既定优先级。 |
| `execution.facade → execution.graph.ports` | 20 | 28 | facade 是否只做声明/编排，还是开始重复 port 规则。 |
| `state.graph_state.execution_transitions → state.graph_state.validation` | 15 | 26 | transition 与 validation 的 owner 是否被交叉调用。 |
| `execution.graph.compiler → execution.graph.ports` | 19 | 25 | 编译阶段是否只消费 typed port facts，没有重新解释一遍。 |

错误、ports、validation 这类基础模块通常会自然形成高密度边，因此不能只按数量排序就要求拆分；应结合方向、调用内容和是否新增
第二事实源来定性。

## 分批逐项审查记录

本节记录按 source module 分批进行的人工设计审查。判断标签与原 49 项文档相同：`K` 表示当前没有证据支持修改，
`A` 表示已经能描述出安全且更简单的目标，`B` 表示有方向但必须先完成小型设计/原型。表中的 `edges/sites` 分别是
`symbol_edges/call_sites`。

### 第 1 批：`execution.facade` 发出的 24 个模块对

本批结论：**K=24，A=0，B=0**。`Graph` 是唯一 public facade；这些边分别承担输入校验、拓扑构建、resume action、
编译、owner 生命周期和结果投影。为了让 fan-out 变小而拆出第二个入口、宽 `Context` 或转发 wrapper，不能证明总调用链更简单，
反而会破坏唯一 owner。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.cancellation` | 2/3 | K | `Graph.run` 的取消等待和 root cleanup 是 invocation 边界。 |
| 2 | `execution.engine.admission` | 1/1 | K | graph input admission 的唯一入口。 |
| 3 | `execution.engine.recovery` | 1/1 | K | recovery proof 必须先于 continued owner admission。 |
| 4 | `execution.errors` | 14/22 | K | 公共 API 的显式错误边界；不能用泛化异常包装换取少几条边。 |
| 5 | `execution.family_driver` | 3/3 | K | fresh、continued 和结果投影统一走唯一 owner。 |
| 6 | `execution.graph.compiler` | 1/1 | K | facade 首次运行时编译一次，不形成第二 compiler。 |
| 7 | `execution.graph.definition` | 2/2 | K | builder 物化 typed definition。 |
| 8 | `execution.graph.edge` | 3/3 | K | 三类拓扑边的公开构建入口。 |
| 9 | `execution.graph.node` | 1/1 | K | callable node 的 typed definition 构造。 |
| 10 | `execution.graph.outcome` | 3/3 | K | 公共 success/failure/interrupt 工厂。 |
| 11 | `execution.graph.ports` | 20/28 | K | 输入、输出、名称和类型规范化的窄边界。 |
| 12 | `execution.graph.resume_input` | 1/1 | K | resume codec binding 的单一构造点。 |
| 13 | `execution.graph.validation` | 1/1 | K | 创建 Graph 时的 identity 早期校验。 |
| 14 | `execution.graph.values` | 5/5 | K | immutable values 的公共工厂和校验。 |
| 15 | `execution.identity` | 1/1 | K | root run identity 构造。 |
| 16 | `execution.invocation` | 6/6 | K | state/context/fence/resume 规划 owner。 |
| 17 | `execution.limits` | 1/1 | K | run limit 的 typed 校验。 |
| 18 | `execution.request` | 7/7 | K | resume action 的公共 typed 构造。 |
| 19 | `execution.resource.definition` | 1/1 | K | builder 自动维护资源 catalog。 |
| 20 | `execution.run_context` | 4/4 | K | continuation/frame 的内部 owner。 |
| 21 | `state.graph_state.frontier_model` | 1/1 | K | `GraphResumeInputCodecId` nominal identity 构造。 |
| 22 | `state.graph_state.identity` | 15/19 | K | `GraphNodeId` 等 nominal identity 构造；不应为降数改掉。 |
| 23 | `state.graph_state.model` | 1/1 | K | `GraphAbortReason` 属于 state owner。 |
| 24 | `state.graph_state.resource_model` | 1/1 | K | `ResourceId` 属于 resource/state identity owner。 |

第 21、22、24 项是 `NewType`/typed identity 的构造调用，属于高召回口径下的可预期噪声，不是应当删除或绕开的代码。
本批没有发现明确的 `A` 或 `B` 项；后续若 facade 新增指向业务实现的边，仍需重新按同一矩阵审核，不能沿用本批结论自动放行。

### 第 2 批：`execution.family_driver` 发出的 19 个模块对

本批逐项查看了 `family_driver.py` 中的 107 条去重 symbol edge 和 117 个调用站点，并沿着 child handoff、session
消费、state commit、frame/evidence 更新、结果投影和 cleanup 的完整路径核对 target owner。结论：**K=19，A=0，B=0**。
`_GraphRun` 是每个 scope 的唯一 live owner，`GraphExecutor` 是唯一执行引擎，`reduce_graph_run` 是唯一纯 reducer；这些边
没有形成第二 runner、第二事实源或绕过提交边界的 shortcut。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.cancellation` | 7/7 | K | `_transition`、child 构造、root admission 和 cleanup 都必须通过 cancellation-safe join 等待 owner task；直接 `await` 或再包一层通用 wrapper 都可能改变取消归因和收敛顺序。 |
| 2 | `execution.engine.admission` | 3/3 | K | child 输入 admission 与 terminal graph-output projection 都是纯的 graph/port 边界；family driver 只编排，不重复解释声明或启动另一条 admission 路径。 |
| 3 | `execution.engine.resume_input` | 1/1 | K | `_start_child` 读取 authoritative state/frame 后调用唯一的 node-input materializer；缺失值和 codec 校验仍由 resume-input owner 负责。 |
| 4 | `execution.engine.session` | 5/5 | K | session completion、node-origin cancellation 和 `aclose` 的调用顺序共同构成单消费者协议；把 close/fence 移到别处会掩盖 ack 边界。 |
| 5 | `execution.engine.snapshot_guard` | 1/1 | K | `_GraphRun` 构造时先验证 scoped snapshot 与 compiled graph 相符，再保存 state；这是一个窄的构造前 guard，不是重复校验阶段。 |
| 6 | `execution.errors` | 21/29 | K | `SnapshotMismatchError`、`ResultCollectionError` 和 `FrameInstallationInvariantError` 分别标出 identity、child projection、frame handoff 等不变量；多处显式构造保持错误优先级，不能用一个泛化异常或集中 wrapper 换数字。 |
| 7 | `execution.executor` | 3/3 | K | `_GraphRun` 创建唯一 `GraphExecutor`，并由它准备 frontier、签发 session；family driver 没有直接调用 scheduler 或复制执行策略。 |
| 8 | `execution.graph.values` | 1/1 | K | `project_graph_result` 在 public boundary 处调用 `_public_values` 做一次不可变值投影；这是结果出口的必要净化，不是重复转换。 |
| 9 | `execution.graph_run` | 2/2 | K | root 和 child 都通过 `project_start_graph_command` 生成 canonical `StartGraphRun`；共享同一个 command projection，保留两处调用只反映两个 owner scope。 |
| 10 | `execution.identity` | 5/5 | K | child scope、stable activation、root scope 和最终结果投影都需要同一套 deterministic identity 函数；把 identity 拼接散落到 driver 会产生第二事实源。 |
| 11 | `execution.invocation` | 2/2 | K | resume frame 投影和 current-child activation 判断是 admission/frame 的两个窄查询；它们不驱动执行，也没有把 recovery 规则复制到 family driver。 |
| 12 | `execution.request` | 1/1 | K | `drive_quantum` 用当前 state、scope、frames、child projection 和 limits 构造 typed `StepRequest`，是 executor 的明确输入边界。 |
| 13 | `execution.result` | 17/17 | K | child phase、terminal projection、partial-commit error 及三类最终 view 是不同协议结果；合成一个宽 result 或字典会丢失 awaiting/terminal/abort 语义。 |
| 14 | `execution.run_context` | 19/20 | K | frame index、availability coordinate、publication provenance、child binding 和 continuation 都在 evidence handoff 处集中更新；这些是不可变运行证据，不是第二份 `GraphRunState`。 |
| 15 | `state.graph_state.command` | 6/6 | K | fence/abort 均先构造 typed command，再交给唯一 `commit_transition`；driver 不直接修改 durable state，调用边正好标出提交边界。 |
| 16 | `state.graph_state.frontier_model` | 1/1 | K | `pending_node_ids` 只是从 authoritative frontier 得到 child projection 所需的纯查询，不承载新的 frontier 状态。 |
| 17 | `state.graph_state.identity` | 1/1 | K | 最终结果 view 通过 `graph_interrupt_id` 生成稳定 interrupt identity；ID 规则应由 state identity owner 统一维护。 |
| 18 | `state.graph_state.model` | 10/11 | K | `ParentGraphActivation` 和 `GraphAbortReason` 是 child 生命周期、handoff 和 cleanup 需要的 typed state facts；其中 `NewType` 构造被高召回统计纳入，不应为降指标改成裸字符串。 |
| 19 | `state.graph_state.reducer` | 1/1 | K | `commit_transition` 唯一调用 `reduce_graph_run` 生成 candidate successor，再等待 commit 返回 exact state；不能把 reducer 逻辑搬进 family driver 或建立第二 transition owner。 |

本批没有发现需要直接删除的重复边，也没有需要先做原型的边。尤其是 `execution.errors`、`execution.result` 和
`execution.run_context` 的边数较高，但它们分别对应错误优先级、结果协议和 frame/evidence 生命周期；减少数字的机械拆分会让
调用链更长、owner 更模糊。至此已完成两批共 **43 个 module pair：K=43，A=0，B=0**；剩余批次仍按 source module
逐批检查，不能把本批的 K 结论自动套到后续新增边。

### 第 3 批：`execution.invocation` 发出的 16 个模块对

本批逐项检查了 invocation 的 lineage、fence、resume、frame validation 和 complete-context audit 路径，共 **65 条
symbol edge、91 个调用站点**。重点核对了两个容易看起来像重复的地方：`plan_fences`/`plan_resumes` 都调用 reducer，
以及多个 frame validator 都查找 scoped graph。前者分别生成 fence 和 resume 的不同 exact successor，后者分别消费不同
descriptor 并保留各自的错误顺序；都不是重复 owner。结论：**K=16，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.recovery` | 3/6 | K | `_resume_facts` 和 `recovery_seed` 只把已通过 invocation admission 的 action/state facts 交给 recovery proof；没有复制恢复 worklist 或另建状态。 |
| 2 | `execution.engine.resume_admission` | 3/3 | K | `plan_resumes` 按 scope 调用单 scope `prepare_resume`，再调用跨 scope `admit_resume_candidates`；这是既有两层 admission 契约，不是两套 resume 实现。 |
| 3 | `execution.engine.resume_input` | 2/2 | K | state-owned override 的 materialization 与 complete continuation 的 pending-input availability 分别是“验证具体 frame”和“审计证据是否齐全”，不能合并成万能检查器。 |
| 4 | `execution.engine.routing` | 1/1 | K | 完整 continuation 才调用 `graph_outputs_available` 检查已完成 graph output；它不同于 runtime routing 或 output projection，错误边界保持在 routing owner。 |
| 5 | `execution.engine.snapshot_guard` | 1/1 | K | `plan_fences` 在每个 scope 生成 fence 前验证 compiled/state identity；`prepare_resume` 的再次 guard 针对 fence 后的 candidate state，是 admission 边界防护而非无效重复。 |
| 6 | `execution.errors` | 14/37 | K | 37 个站点对应 lineage、坐标、frame shape、descriptor、provenance 和 completeness 的不同失败条件；保留 `SnapshotMismatchError`/publication error 的区分和先后顺序，不能集中成一个泛化异常。 |
| 7 | `execution.graph.topology` | 1/9 | K | 各类 frame/lineage 校验都需要按 scope 取得对应 immutable `CompiledGraph`；重复的是窄 lookup，不是 topology 重算或第二 compiler。为缓存成宽 context 会增加生命周期和失效规则。 |
| 8 | `execution.graph.values` | 4/4 | K | 四种 frame segment 分别调用 graph-input、node-output、node-input、graph-output 的 typed admission；不同 descriptor/错误语义不适合抽成反射式通用 validator。 |
| 9 | `execution.identity` | 7/7 | K | root/child scope、stable activation 和坐标都由同一 identity owner 计算；invocation 只组合它们，不自行拼接字符串或保存副本。 |
| 10 | `execution.request` | 1/1 | K | `plan_resumes` 构造 typed `ResumeRequest` 交给单 scope planner，明确划分公共 request 与内部 candidate。 |
| 11 | `execution.run_context` | 10/10 | K | resume inputs、skip substitutions、publication provenance 和 availability 都写入不可变 frame index；这是 invocation admission 的证据投影，不是第二运行状态。 |
| 12 | `state.graph_state.command` | 1/1 | K | `plan_fences` 只构造 `FenceGraphExecution` command，随后仍由唯一 reducer 计算 successor；没有绕过 command/commit 边界。 |
| 13 | `state.graph_state.frontier_model` | 2/2 | K | `pending_node_ids` 与 `frontier_node` 是对 authoritative frontier 的纯查询，用于判断当前 child activation 和解析 scope，不改变 frontier。 |
| 14 | `state.graph_state.identity` | 1/1 | K | `_resume_facts` 将可选 route 转为 nominal `GraphRouteId`；这是高召回统计纳入的 typed identity 构造，不应改成裸字符串。 |
| 15 | `state.graph_state.model` | 4/4 | K | `ParentGraphActivation` 在 parent/child coordinate 校验和 complete-context audit 中表达同一 typed 事实；不应复制一份 invocation-local parent record。 |
| 16 | `state.graph_state.reducer` | 2/2 | K | fence 与 resume 各自调用 `reduce_graph_run` 生成 exact candidate，保证 planned lineage 与最终 commit 使用同一个纯 transition owner；两次调用对应两种 command，不是重复 reducer。 |

本批没有发现明确的 `A` 或 `B`。`execution.invocation` 自身仍有较高的 frame/completeness 复杂度，但 #38/#39/#40 已按原 49
项文档完成过内部化简；本批的跨模块边是这些窄 owner 之间的正常协议，不应为了降低 fan-out 再拆出第二套 continuation、宽
`InvocationContext` 或重复 frame 状态。至此三批共 **59 个 module pair：K=59，A=0，B=0**；后续若新增边越过
`Graph`、`family_driver`、唯一 reducer 或 typed frame owner，仍需重新定性。

### 第 4 批：`execution.engine.recovery` 发出的 15 个模块对

本批逐项沿着 recovery 的 live、quiescent、nested child、routing、resource 和 bounded-worklist 分支检查，共 **49 条
symbol edge、59 个调用站点**。这里最容易误判的是：恢复 proof 看起来像又实现了一套 runtime。实际代码复用了 planner、resource
admission、routing、settlement 和唯一 reducer 的纯函数，只在 proof 中枚举可能的 successor；`RecoveryTransferState` 等类型是
不可变的证明投影，不是第二份可提交的 `GraphRunState`。结论：**K=15，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.admission` | 2/2 | K | `_select_live` 复用统一的 slot/resource 选择，`_expand_quiescent_executable` 复用 resource snapshot claim；恢复不会另写一套 admission policy。 |
| 2 | `execution.engine.claim_stage` | 1/1 | K | `project_claim_command` 只把 proof 中的资源参与者投影成 typed claim command，仍由同一个 state reducer 解释。 |
| 3 | `execution.engine.planner` | 2/2 | K | live 和 quiescent 分支都调用同一个 `plan_tasks`；恢复只模拟任务候选，不启动 scheduler 或执行用户 callable。 |
| 4 | `execution.engine.resume_input` | 3/3 | K | pending input availability、materialization plan 和 resume coordinate 直接复用 runtime 的输入事实，避免 proof 与实际 materializer 漂移。 |
| 5 | `execution.engine.routing` | 5/5 | K | `_success_routes`、`resolve_routing_facts`、`project_routing_facts` 和 output availability 都是纯 routing 规则；proof 枚举结果，不把 routing decision 搬到另一 owner。 |
| 6 | `execution.engine.settlement` | 3/3 | K | nested outcome 与 live success 通过统一的 success/failure settlement command projection，再交给 reducer；没有直接改 frontier。 |
| 7 | `execution.errors` | 12/20 | K | `ExecutionLimitError` 表示 proof budget，`GraphValueUnavailableError` 表示历史证据缺失，`SnapshotMismatchError` 表示 scope/状态不一致；错误类别和优先级不能用一个 proof exception 合并。 |
| 8 | `execution.graph.topology` | 1/1 | K | `preflight_recovery` 按 action scope 取得对应 immutable compiled graph；只做一次 scoped lookup，不重编 topology。 |
| 9 | `execution.graph_run` | 1/1 | K | 缺少 child snapshot 时用同一 `project_start_graph_command` 构造模拟初始 state；这是 proof candidate，不是实际 commit 或第二启动入口。 |
| 10 | `execution.identity` | 3/3 | K | publication、child scope 和 nested parent activation 都用统一 deterministic coordinate；proof 不自行拼接 run/节点身份。 |
| 11 | `execution.run_context` | 3/3 | K | publication、child-boundary 和 graph-input availability coordinate 只描述证据可用性；不保存可变 frame 或替代 runtime state。 |
| 12 | `state.graph_state.frontier_model` | 4/4 | K | `pending_node_ids`、`frontier_status`、`frontier_node` 是 authoritative frontier 的纯查询，供 worklist 分支选择，不改变 settlement。 |
| 13 | `state.graph_state.identity` | 2/2 | K | `graph_interrupt_id` 与 `GraphExecutionAttemptId("recovery-preflight")` 都是 nominal identity；后者属于高召回 `NewType` 构造噪声，不应改成裸字符串。 |
| 14 | `state.graph_state.model` | 2/2 | K | `_initial_children`/`_child_outcomes` 用 `ParentGraphActivation` 表示同一 parent-child 坐标事实，避免 proof 自建平行 activation 结构。 |
| 15 | `state.graph_state.reducer` | 5/7 | K | child start、nested settlement、claim、live success 和 routing advance/complete 都通过唯一 `reduce_graph_run` 生成模拟 successor；多次调用对应不同 command 分支，不是第二 reducer。 |

本批没有发现真实的重复执行路径。特别是 recovery 自己维护的 control/availability/worklist 记录虽然增加了类型和调用边，
但它们的用途是“证明所有可能结果”，不是持有或提交运行状态；把它们强行合并进 `GraphRunState` 反而会污染唯一事实模型。
至此四批共 **74 个 module pair：K=74，A=0，B=0**；后续继续检查时，若某条边开始调用用户 callable、直接写 state，或绕过
planner/reducer，则应改判，而不能因为 recovery 模块当前整体为 K 就自动放行。

### 第 5 批：`execution.engine.resume_admission` 发出的 13 个模块对

本批逐项检查了单 scope resume preparation 与跨 scope candidate admission，共 **34 条 symbol edge、45 个调用站点**。
重点复核了 `prepare_resume` 是否又建立了 reducer/frontier 的第二套证明，以及 `admit_resume_candidates` 是否只是机械重复
校验。实际情况是：前者只生成 typed command、输入 frame 和 substitution，后者只做跨 scope evidence/collision/routing
availability；successor 仍由唯一 reducer 产生和复核。结论：**K=13，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.resume_input` | 6/6 | K | override 的 encode→decode round-trip、codec binding、materialization 和 resume coordinate 都复用同一输入 owner；不另写 payload 或 frame 规则。 |
| 2 | `execution.engine.routing` | 2/2 | K | 单 scope 先校验 routing contribution，跨 scope 再用 successor 计算 routing facts/availability；一个校验声明、一个校验证据，不能合成无边界的万能 routing helper。 |
| 3 | `execution.engine.snapshot_guard` | 1/1 | K | `prepare_resume` 在任何 action 分支前确认 scoped graph/state identity；这是可独立调用的 admission 函数必须守住的入口 guard。 |
| 4 | `execution.errors` | 4/15 | K | `SnapshotMismatchError`、`GraphValuePublicationError`、`GraphValueUnavailableError` 分别对应 action/state 不一致、duplicate/collision 和历史值不可用；15 个站点体现固定错误优先级，不是重复异常包装。 |
| 5 | `execution.graph.values` | 1/1 | K | skip output 只通过 `_make_node_output_frame` 按 publication descriptor 生成 typed frame，不能把裸 values 直接塞进 candidate。 |
| 6 | `execution.identity` | 1/1 | K | 每个 action 用 `StableActivation` 固定 scope/superstep/node 坐标；identity 由统一 owner 生成，admission 不自行拼坐标。 |
| 7 | `execution.result` | 1/1 | K | `prepare_resume` 返回既有 `PreparedResume` 协议，明确区分 command、admitted inputs 和 substitutions，不引入宽字典结果。 |
| 8 | `execution.run_context` | 6/6 | K | `AdmittedResumeInput`、`PreparedSubstitution`、candidate availability 和 publication coordinate 都是 frame/evidence 类型；它们不拥有 durable state。 |
| 9 | `state.graph_state.command` | 4/4 | K | failed、interrupted、skip 三种 action 分别投影成 typed resume command，保留 State 的 variant 语义和错误边界。 |
| 10 | `state.graph_state.frontier_model` | 3/3 | K | `frontier_node` 读取当前 settlement，`UseStepRequestInput`/`GraphSkipReason` 表达 nominal input/reason；没有在 admission 内复制 frontier model。 |
| 11 | `state.graph_state.identity` | 2/2 | K | interrupt ID 和 route ID 均使用 state identity 的 canonical 规则；`GraphRouteId` 构造属于高召回 typed identity 噪声，不应改成字符串。 |
| 12 | `state.graph_state.reducer` | 1/1 | K | candidate admission 用 `reduce_graph_run(previous, command)` 复核 exact successor，确保计划状态和实际 commit 共享同一 reducer。 |
| 13 | `state.graph_state.routing` | 2/2 | K | `ContinueGraphRouting` 与 `SelectGraphRoute` 是 state-owned routing facts；resume admission 只构造/校验，不重新解释 route。 |

本批没有发现明确的 `A` 或 `B`。原 49 项中的 #11/#12 已删除手工 frontier 模拟和重复 successor 证明，本批剩余跨模块边正好
体现那个简化后的分工。特别是 encode→decode、snapshot guard 和 reducer 复核看起来会“多做一次”，但分别封住 codec round-trip、
独立 admission 入口和 candidate exactness，删除它们会降低恢复安全性。按启动时 `715/216` 基线，五批累计已审 **87 个
module pair：K=87，A=0，B=0**；实时新增的 5 个 `mote_kernel.events` pair 仍未计入，待其独立设计完成后再审查。

### 第 6 批：`execution.engine.session` 发出的 10 个模块对

本批检查了 session 的 claim receipt、scheduler、completion、ack、取消和 close 全路径，共 **18 条 symbol edge、21 个
调用站点**。`GraphExecutionSession` 的职责是“只消费一个 execution claim，并一次交付一个 completion”；它不拥有 durable
state，也不直接执行 routing。结论：**K=10，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.cancellation` | 1/1 | K | `next` 被调用方取消时，通过 `wait_for_owner_task` 等待 `aclose` 完成再重新抛出取消；不能改成未等待的后台 close。 |
| 2 | `execution.claim` | 1/1 | K | `issue_execution_session` 只能从已消费的 claim receipt 取得 state/preparation，保持“一次 claim → 一个 session”的所有权。 |
| 3 | `execution.engine.admission` | 1/1 | K | `_select_ordinary` 复用统一的 slot/resource admission；session 只把 executable task 交给 scheduler，不重复筛选规则。 |
| 4 | `execution.engine.scheduler` | 5/5 | K | scheduler 的 submit、completion、error drain 和 aclose 都集中在同一个 session owner；拆出第二 consumer 会破坏单消费者和 close 顺序。 |
| 5 | `execution.engine.settlement` | 1/1 | K | `_project` 只把 task result 投影成 `SettleGraphNode` command，状态仍由外层 family driver 提交，不在 session 内修改。 |
| 6 | `execution.engine.snapshot_guard` | 1/1 | K | 首次 `next` 校验 claim successor 与 compiled graph 相符；后续 acknowledgement 改由 reducer exact-successor 校验，顺序是必要的。 |
| 7 | `execution.errors` | 4/7 | K | closed/quiescent、并发 `next`、非法 acknowledgement、无可调度 pending node 分别对应 session protocol 违规；不能合并成普通 `RuntimeError`。 |
| 8 | `execution.result` | 1/1 | K | `_project` 返回 typed `ExecutedGraphNode`，把 result 与待提交 command 绑定，避免调用方自行拼装 settlement。 |
| 9 | `state.graph_state.frontier_model` | 2/2 | K | `pending_node_ids` 只查询 authoritative frontier，用于筛选和 StopAsyncIteration 判断，不复制 frontier state。 |
| 10 | `state.graph_state.reducer` | 1/1 | K | `_acknowledge` 用唯一 `reduce_graph_run` 验证上一条 command 的 exact successor；这是提交确认屏障，不是 session 自己的 reducer。 |

本批没有发现可直接删除的边。scheduler 的 5 条边和 protocol error 的 7 个站点虽然密集，但正好覆盖一个单消费者 session
的完整生命周期；把它们拆到 facade 或通用 task manager 会增加隐藏状态和取消竞态。按原始基线累计已审 **97 个 module pair：
K=97，A=0，B=0**（事件模块的 5 个新增 pair 仍排除）。

### 第 7 批：`execution.engine.settlement` 发出的 8 个模块对

本批检查了 task result 的变体校验、execution lease、routing contribution、interrupt payload 和 settlement command
投影，共 **19 条 symbol edge、21 个调用站点**。这个模块的关键边界是：它只把一个已确认的 task result 转成一个 typed
`SettleGraphNode`，不提交、不推进 frontier，也不拥有 session。结论：**K=8，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.routing` | 1/1 | K | success result 的 route 先由 `validate_routing_contribution` 按 compiled topology 校验，避免把非法 route 交给 State。 |
| 2 | `execution.engine.snapshot_guard` | 1/1 | K | `settle_result` 只在 acknowledgement 后校验 graph/state identity；它是 result 进入 command 前的必要 guard。 |
| 3 | `execution.engine.task` | 1/1 | K | `task_identity` 检查 result 的 run/superstep/node 坐标，确保 completion 不能冒充其他 task。 |
| 4 | `execution.errors` | 3/5 | K | 没有 execution lease、unsupported result、非法坐标或非 pending node 都是不同的 settlement protocol 错误；不能合并成普通异常。 |
| 5 | `state.graph_state.command` | 6/6 | K | success/failure/interrupt 分别构造对应的 `SettleGraphNode` 与 outcome variant；typed command 是唯一跨入 State 的边界。 |
| 6 | `state.graph_state.frontier_model` | 4/4 | K | `GraphFailure`、interrupt identity/payload 和 `frontier_node` 只读取/包装 authoritative frontier facts，不在 settlement 中更新状态。 |
| 7 | `state.graph_state.identity` | 1/1 | K | conditional route 使用 nominal `GraphRouteId`，由 state identity 统一约束，不改成裸字符串。 |
| 8 | `state.graph_state.routing` | 2/2 | K | `ContinueGraphRouting` 与 `SelectGraphRoute` 是 State-owned routing contribution；settlement 只传递已验证的值。 |

本批没有发现可直接化简的边。`settle_result` 的 guard → task identity → pending node → result variant → command projection
顺序决定了错误优先级；把校验下沉到 reducer 或把三种 outcome 合成一个结构，都会让异常边界变模糊。按原始基线累计已审 **105
个 module pair：K=105，A=0，B=0**（事件开发中的 5 个 pair 仍排除）。

### 第 8 批：`execution.engine.admission` 发出的 8 个模块对

本批检查了 graph input/child input admission、graph output projection、task resource claim 和 executable-task selection，
共 **16 条 symbol edge、25 个调用站点**。这些调用都发生在纯 planning/projection 阶段；admission 不持有 live task、不提交
GraphRunState。结论：**K=8，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.routing` | 2/2 | K | graph output projection 复用 routing 的 graph-input/node-output coordinate 规则，避免再拼一套 activation 坐标。 |
| 2 | `execution.errors` | 2/3 | K | output source 缺失、publication selection 不完整和 resource admission 无 participant 分别属于 value 与 resource 边界，错误类型不能合并。 |
| 3 | `execution.graph.ports` | 1/1 | K | `require_publication_selection` 在 output projection 处确认 compiled binding 的 activation selection；ports 规则仍由 ports owner 解释。 |
| 4 | `execution.graph.values` | 5/6 | K | graph input、child input 和 output view 分别通过 typed frame factory/value lookup；不同 frame descriptor 不能用裸 mapping 代替。 |
| 5 | `execution.run_context` | 1/2 | K | `ScopedFrameIndex.lookup` 读取对应 graph-input/publication frame，frame 的坐标与重复检查继续由 run-context owner 负责。 |
| 6 | `state.graph_state.resource_command` | 1/1 | K | `admit_tasks` 只生成 `AcquireResources` command，资源状态仍交给唯一 resource reducer。 |
| 7 | `state.graph_state.resource_model` | 2/2 | K | `initial_resource_snapshot` 从 compiled resource order 构造 immutable locks，是 claim replay 的唯一初始快照来源。 |
| 8 | `state.graph_state.resource_reducer` | 2/8 | K | admission 先校验 graph/task 与 resource order 的对应关系，再调用 `reduce_resources` 计算 acquisition；前者是 task/graph 边界，后者是资源快照不变量，不是重复校验。 |

本批没有发现明确的 `A` 或 `B`。尤其 `project_graph_outputs` 与 routing 的 availability 检查看起来相近，但一个读取并构造
typed output view，另一个只判断证据是否存在；保留两个 owner 才能维持不同的错误语义。按原始基线累计已审 **113 个 module
pair：K=113，A=0，B=0**（events 开发中的 5 个 pair 继续排除）。

### 第 9 批：`execution.engine.superstep` 发出的 8 个模块对

本批检查了一个 frontier attempt 的状态分派、child 等待、routing resolve 和 claim preparation，共 **13 条 symbol edge、
13 个调用站点**。`prepare_superstep` 只做顺序明确的纯 preparation：先 guard，再处理 terminal/awaiting/settled，再准备
frontier 和 claim；它不创建 session，也不提交 state。结论：**K=8，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.admission` | 1/1 | K | 只有 frontier 已准备好后才调用 `claim_resource_snapshot`，资源 claim 与 task preparation 的阶段顺序清楚。 |
| 2 | `execution.engine.claim_stage` | 1/1 | K | `prepare_claim` 将准备好的 frontier 封装成一次 typed execution claim，superstep 不直接修改 lease。 |
| 3 | `execution.engine.frontier` | 1/1 | K | `prepare_frontier` 是唯一 child projection/executable materialization owner；遇到 missing/active child 直接返回等待。 |
| 4 | `execution.engine.routing` | 1/1 | K | settled frontier 只通过 `resolve_routing` 生成下一条 resolution command，不把 routing 逻辑复制到 superstep。 |
| 5 | `execution.engine.snapshot_guard` | 1/1 | K | 所有状态分支前先验证 scoped graph/state identity，防止 preparation 在错误 scope 上运行。 |
| 6 | `execution.errors` | 1/1 | K | active execution 没有原 session 时抛出明确的 `ResultCollectionError`，保护 session ownership。 |
| 7 | `execution.result` | 4/4 | K | completed、aborted、ready-to-resolve、awaiting-resume 是不同的 typed disposition，避免用布尔值/字符串分派。 |
| 8 | `state.graph_state.frontier_model` | 3/3 | K | `frontier_status`、failed/interrupted node 查询只读取 authoritative frontier，superstep 不维护副本。 |

本批没有发现可直接化简的边。这里的少量 fan-out 正好对应一个 scope attempt 的状态机入口；把这些分支拆成多个 runner 或让
`GraphExecutor`/session 各自重新判断状态，会重复 owner 和错误顺序。按原始基线累计已审 **121 个 module pair：K=121，A=0，
B=0**（events 开发中的 5 个 pair 继续排除）。

### 第 10 批：`execution.engine.frontier` 发出的 7 个模块对

本批逐个核对 `prepare_frontier` 的 child projection、task planning、输入物化和结果 disposition，共 **9 条 symbol edge、9 个调用站点**。
该函数只负责一个已确认 frontier 的纯 preparation；遇到 child 未完成时返回等待，child 完成后才交给唯一 materializer 生成 executable。
结论：**K=7，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.planner` | 1/1 | K | `plan_tasks` 是 pending task 的唯一规划入口；frontier 不复制 superstep、limit 或 snapshot 校验。 |
| 2 | `execution.engine.resume_input` | 1/1 | K | child 阻断解除后才调用 `materialize_node_input`，输入 frame 的 codec、坐标和缺失值错误仍由 resume-input owner 负责。 |
| 3 | `execution.engine.task` | 1/1 | K | `ExecutableTask` 只把 canonical `GraphTask` 与有效 input frame 绑定，不让 scheduler 自己拼装 task。 |
| 4 | `execution.errors` | 1/1 | K | child projection 必须按 pending nested activation 精确覆盖；`ResultCollectionError` 是 preparation 的边界错误，不应改成静默过滤。 |
| 5 | `execution.graph.values` | 1/1 | K | completed child 的 output view 通过 `_node_output_from_view` 按声明转换成 node-output frame，不能把 view 裸传给执行器。 |
| 6 | `execution.result` | 3/3 | K | `TaskSuccess`、`TaskFailure`、`WaitingForChildren` 分别表达终结、失败和阻断三种 typed disposition；合并成布尔状态会丢失协议语义。 |
| 7 | `state.graph_state.model` | 1/1 | K | `ParentGraphActivation` 是 child projection 的稳定坐标事实；frontier 不保存第二份 parent/child 状态。 |

这些边没有形成第二个 planner、materializer 或 runner；尤其 `WaitingForChildren` 的早返回已经把阻断 owner 收在 frontier preparation
内。按原始基线累计已审 **128 个 module pair：K=128，A=0，B=0**（events 仍排除）。

### 第 11 批：`execution.engine.resume_input` 发出的 7 个模块对

本批覆盖 override 编解码、普通 binding 的 availability、frame materialization 和 pending-node 查询，共 **30 条 symbol edge、34 个调用站点**。
重点确认 routing 只提供坐标事实，resume-input 自己负责具体 frame 读取及异常包装。结论：**K=7，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.routing` | 2/2 | K | `_graph_input_coordinate` 与 `_node_output_coordinate` 是共享的 typed 坐标规则；resume-input 不重新推导 activation level 或 route。 |
| 2 | `execution.errors` | 10/13 | K | codec 不匹配、compiled binding 缺失、frame 不可用和 payload 类型错误分别对应 snapshot/admission/unavailable 边界；保留异常优先级。 |
| 3 | `execution.graph.ports` | 1/1 | K | `require_publication_selection` 只确认 compiler 已产出的 selection，resume-input 不解释 ports 声明。 |
| 4 | `execution.graph.values` | 6/6 | K | `NamedValue`、`_frame_value`、`_make_node_input_frame` 和 entries 读取共同完成 typed frame 物化；不能用裸 mapping 绕过 exact-type admission。 |
| 5 | `execution.identity` | 2/2 | K | `StableActivation` 统一 resume coordinate 的 scope/superstep/node 身份，调用方不自行拼接坐标字符串。 |
| 6 | `execution.run_context` | 5/6 | K | availability 查询和 `ScopedFrameIndex.lookup` 只读取不可变证据；resume-input 不拥有 durable state 或 continuation。 |
| 7 | `state.graph_state.frontier_model` | 4/4 | K | `frontier_node`、pending input variant 和 opaque payload 都是 State-owned facts；materializer 只消费并验证，不改变 frontier。 |

`node_inputs_available`、`pending_node_input_available` 与 `materialize_node_input` 看起来都在检查输入，但前者只做短路可用性判断，后者
负责逐 binding 取值和 `GraphValueUnavailableError`，职责和错误边界不同，不能合并成万能 helper。累计已审 **135 个 module pair：K=135，A=0，B=0**。

### 第 12 批：`execution.engine.routing` 发出的 7 个模块对

本批逐点检查 routing contribution 校验、join accumulation、availability diagnostics 和 resolution command projection，共 **25 条 symbol edge、29 个调用站点**。
`resolve_routing_facts` 是唯一纯 routing 解释器，`project_routing_facts` 只把已解释事实投影为 State command。结论：**K=7，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 7/9 | K | unknown route、非法 contribution、join deadlock 和 progress 错误各有明确边界；不能用一个泛化 routing exception 隐藏优先级。 |
| 2 | `execution.graph.ports` | 3/3 | K | graph/node output 的 publication selection 由 ports/compiler owner 提供，routing 只解析坐标并检查证据。 |
| 3 | `execution.identity` | 1/1 | K | `StableActivation` 用于 publication 坐标，routing 不重复维护 node activation identity。 |
| 4 | `execution.run_context` | 8/8 | K | graph-input/publication availability protocol 是不可变 frame evidence；routing 不把 availability 变成自己的状态表。 |
| 5 | `state.graph_state.command` | 3/4 | K | `AdvanceGraphFrontier`、`CompleteGraphFrontier`、`AbortGraphRun` 是 routing 的唯一输出边界，实际提交仍由 reducer 完成。 |
| 6 | `state.graph_state.frontier_model` | 1/1 | K | `routing_contributions` 只读取 authoritative frontier 的 settled facts，不在 routing 内维护副本。 |
| 7 | `state.graph_state.model` | 2/3 | K | `GraphJoinProgress` 和 `GraphAbortReason` 是 typed projection facts；routing 不重新定义 join/abort 数据结构。 |

虽然 routing 的 `required` 局部函数会被 direct target 和 completed join 共同使用，但它带有同一 target 的局部缓存，避免重复扫描；抽成更宽
的 context 只会搬运事实。累计已审 **142 个 module pair：K=142，A=0，B=0**。

### 第 13 批：`execution.engine.scheduler` 发出的 6 个模块对

本批检查用户 callable 的执行、outcome 投影、route 校验、completion 顺序和 scheduler close，共 **13 条 symbol edge、13 个调用站点**。
`TaskScheduler` 是唯一动态 task pool，session 只消费它交付的结果。结论：**K=6，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.routing` | 1/1 | K | callable 返回的 route 先经 `validate_routing_contribution` 检查，非法 route 不会进入 settlement。 |
| 2 | `execution.errors` | 4/4 | K | 不支持的 node outcome、错误的 contract 返回值均在 scheduler 边界抛出 `NodeExecutionContractError`，不下沉到 State。 |
| 3 | `execution.graph.values` | 2/2 | K | `_public_node_input` 是进入用户 callable 的唯一净化，`_make_node_output_frame` 是返回值进入内核的唯一 frame admission。 |
| 4 | `execution.result` | 3/3 | K | success/failure/interrupt 映射为三种 typed `TaskResult`，保持取消与失败的后续归因。 |
| 5 | `state.graph_state.identity` | 1/1 | K | `GraphRouteId` 是 route 的 nominal identity；不应为减少一条构造边改成裸字符串。 |
| 6 | `state.graph_state.routing` | 2/2 | K | `ContinueGraphRouting` 与 `SelectGraphRoute` 是 State-owned contribution variants，scheduler 只投影不解释 frontier。 |

`submit`、`next_completion`、`aclose` 的 5 条 scheduler 边共同形成单消费者生命周期；拆出通用 task manager 会增加隐藏 handle 和取消竞态。
累计已审 **148 个 module pair：K=148，A=0，B=0**。

### 第 14 批：`execution.graph.compiler` 发出的 6 个模块对

本批逐项核对递归 compile、ports resolution、topology 构造和静态 validation，共 **36 条 symbol edge、58 个调用站点**。其中对
`execution.graph.ports` 的 19 条边全部是 typed declaration/descriptor 的消费或构造；compiler 没有再解释一遍 ports 规则。结论：**K=6，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 11/19 | K | unknown node、missing entry、unreachable、duplicate boundary 和 graph validation 失败按编译阶段保留不同错误类型。 |
| 2 | `execution.graph.edge` | 1/1 | K | `JoinEdge` 只把声明规范化为编译期 topology fact，不启动新的 edge interpreter。 |
| 3 | `execution.graph.node` | 1/1 | K | `CallableNodeDefinition` 是编译后节点的 typed wrapper，compiler 不复制 node operation 或 runtime runner。 |
| 4 | `execution.graph.ports` | 19/25 | K | `FrameDescriptor`、binding、publication selection 等由 compiler 组装成 immutable plan；ports 的 canonical name/type 规则仍只有 ports owner。 |
| 5 | `execution.graph.topology` | 3/11 | K | `CompiledGraph`、`FrontierTransitionPlan` 和 `frozen_map` 是 compiler 的唯一产物；不会再生成平行 topology/cache。 |
| 6 | `execution.graph.validation` | 1/1 | K | `compile_graph` 先调用静态 `validate_graph`，再做绑定和路径证明，阶段顺序避免把 malformed definition 带入 compiler。 |

`_validate_joint_activation_paths`、`_absolute_activation_levels` 等算法虽然产生很多 typed 中间对象，但它们都归 compiler 的同一证明 owner；
为降低 module pair 而引入 `CompilationContext` 或第二次遍历会增加总复杂度。累计已审 **154 个 module pair：K=154，A=0，B=0**。

### 第 15 批：`execution.engine.snapshot_guard` 发出的 5 个模块对

本批检查 snapshot identity、routing/join、resume codec、resource participant 和 scoped coordinate 全路径，共 **8 条 symbol edge、14 个调用站点**。
snapshot guard 是运行进入 planner/executor 前的兼容性边界，不拥有任何 transition。结论：**K=5，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.resume_input` | 1/1 | K | `require_resume_input_binding` 只核对 compiled codec 与 durable codec identity/version，避免 materializer 在错误 graph 上运行。 |
| 2 | `execution.engine.routing` | 2/2 | K | `_declared_joins` 和 contribution validation 复用 routing owner 的规则；guard 不另建 join/routing 表。 |
| 3 | `execution.errors` | 3/9 | K | state/graph identity mismatch 与 unknown node/resource snapshot 分别是 snapshot 错误和 invalid snapshot 错误，保留显式优先级。 |
| 4 | `state.graph_state.frontier_model` | 1/1 | K | `routing_contributions` 只读取当前 frontier；guard 不复制 settlement 或 pending 集合。 |
| 5 | `state.graph_state.validation` | 1/1 | K | 先调用唯一 `validate_graph_run_state` 再做 compiled-graph 对照，避免 guard 变成第二 state validator。 |

无 active execution 时的早返回、resource-free 与 active participant 分支是同一 guard 的连续不变量；拆成多个入口会让调用者承担错误顺序。
累计已审 **159 个 module pair：K=159，A=0，B=0**。

### 第 16 批：`state.graph_state.execution_transitions` 发出的 5 个模块对

本批逐个查看 start、claim、fence、settle、advance、complete 六类纯 transition，共 **42 条 symbol edge、53 个调用站点**。
这些边全部位于 State 的原子 successor 计算内；`validation` 只验证候选状态，`resource_reducer` 只维护资源快照，均没有反向拥有
GraphRunState。结论：**K=5，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.frontier_model` | 21/21 | K | 各 transition 构造/读取 pending、succeeded、failed、interrupted frontier variant；frontier model 是唯一节点 settlement owner。 |
| 2 | `state.graph_state.model` | 3/3 | K | `GraphRunState`、`GraphExecutionLease`、`GraphExecutionToken` 构成同一 successor 的 typed state facts，不拆成 execution-local 状态。 |
| 3 | `state.graph_state.resource_command` | 1/1 | K | settle 时只投影一个 `ReleaseResources` command，资源释放仍在同一原子 transition 中完成。 |
| 4 | `state.graph_state.resource_reducer` | 2/2 | K | `validate_resource_snapshot` 与 `reduce_resources` 分别验证既有快照、释放已完成节点资源；不把资源规则复制到 frontier transition。 |
| 5 | `state.graph_state.validation` | 15/26 | K | `validated_graph_run_state`/`validate_graph_frontier` 是候选 successor 的统一不变量入口，错误类型由 State validation owner 维护。 |

`settle_graph_node` 中“校验 lease → 转换 outcome → 释放资源 → 决定是否保留 lease → 原子验证”的顺序不能拆成多次提交；否则会出现
半结算或资源与 frontier 不一致。累计已审 **164 个 module pair：K=164，A=0，B=0**。

### 第 17 批：`execution.claim` 发出的 4 个模块对

本批检查 linear claim 的 owner 校验、exact successor 复核和一次性 session 授权，共 **7 条 symbol edge、8 个调用站点**。claim
模块只消费已提交 state，不持有可变运行快照。结论：**K=4，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.task` | 2/2 | K | `GraphTask` 与 `task_identity` 从 committed pending node 生成 canonical task 集合，防止 claim receipt 自己重建另一套 task identity。 |
| 2 | `execution.errors` | 3/4 | K | claim 重复消费、错误 owner 和 committed state 不匹配都抛 `ResultCollectionError`；这些是线性授权协议错误，不能静默重试。 |
| 3 | `state.graph_state.frontier_model` | 1/1 | K | `pending_node_ids` 读取 authoritative frontier，claim 不保存 pending 节点副本。 |
| 4 | `state.graph_state.reducer` | 1/1 | K | 通过唯一 `reduce_graph_run` 复核 claim command 的 exact successor，确保“规划状态 = 已提交状态”。 |

这里的 reducer 调用是提交确认屏障，不是第三个 transition owner；删除它会让 forged/stale claim 能进入 session。累计已审 **168 个 module pair：K=168，A=0，B=0**。

### 第 18 批：`execution.engine.planner` 发出的 4 个模块对

本批检查 pending task 的 snapshot guard、limit、identity 和 frontier 查询，共 **5 条 symbol edge、5 个调用站点**。planner
只输出 immutable `GraphTask` tuple，不执行 callable。结论：**K=4，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.engine.snapshot_guard` | 1/1 | K | 每次规划先确认 compiled graph 与 authoritative state 相容，planner 不另写 identity/resource 检查。 |
| 2 | `execution.engine.task` | 2/2 | K | `GraphTask` 和 `task_identity` 是 task projection 的唯一构造路径，保持确定性排序。 |
| 3 | `execution.errors` | 1/1 | K | superstep limit 超限在 planning 边界抛 `ExecutionLimitError`，不让 scheduler 承担 policy。 |
| 4 | `state.graph_state.frontier_model` | 1/1 | K | `pending_node_ids` 只查询当前 frontier；planner 不改变 state 或提前 claim。 |

planner 与 frontier preparation 的两层调用分别代表“列出任务”和“绑定 child/input 后准备可执行任务”，不是重复规划。累计已审 **172 个 module pair：K=172，A=0，B=0**。

### 第 19 批：`execution.executor` 发出的 4 个模块对

本批核对 `GraphExecutor` 的四条 owner wiring：claim owner、scope snapshot guard、superstep preparation 与 session issuance，共 **5 条
symbol edge、5 个调用站点**。它是唯一 assembled graph executor，不自行实现执行循环。结论：**K=4，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.claim` | 2/2 | K | `ExecutionClaimOwner` 和 `PreparedExecutionClaim.consume` 将 claim 绑定到一个 executor 实例，阻止跨 owner 复用。 |
| 2 | `execution.engine.session` | 1/1 | K | 只有 `issue_execution_session` 能把 consumed claim 变成单消费者 session。 |
| 3 | `execution.engine.snapshot_guard` | 1/1 | K | issue 前通过 `require_scoped_snapshot_matches_graph` 再做 scoped identity guard，避免错误 state 进入 session。 |
| 4 | `execution.engine.superstep` | 1/1 | K | `prepare_superstep` 是唯一 frontier-wide preparation；executor 不复制状态分派或资源 admission。 |

这四条边是组装关系而非额外控制流；引入第二 executor 或通用 runner 只会把 owner 约束移到隐式状态。累计已审 **176 个 module pair：K=176，A=0，B=0**。

### 第 20 批：`execution.graph_run` 发出的 4 个模块对

本批检查 root/child `StartGraphRun` projection 的 identity、codec 和 frontier entry 构造，共 **4 条 symbol edge、4 个调用站点**。该
模块是纯 command projection，不负责提交或启动执行。结论：**K=4，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 1/1 | K | parent activation 与 child run identity 不一致时抛 `SnapshotMismatchError`，不能让 reducer 接收伪造 parent。 |
| 2 | `state.graph_state.command` | 1/1 | K | `StartGraphRun` 是进入 State 的唯一 typed command；projection 不直接构造 `GraphRunState`。 |
| 3 | `state.graph_state.frontier_model` | 1/1 | K | `GraphResumeInputCodec` 只记录编译 binding 的 codec identity/version，属于 state snapshot fact。 |
| 4 | `state.graph_state.identity` | 1/1 | K | `child_graph_run_id` 统一 parent→child deterministic identity，避免 facade/driver 各自拼接。 |

root 和 child 共用同一个 projection 函数，差异只在 parent 参数和 identity 校验；这比各建一条启动路径更简单。累计已审 **180 个 module pair：K=180，A=0，B=0**。

### 第 21 批：`state.graph_state.reducer` 发出的 4 个模块对

本批逐项核对 command variant dispatch、revision 检查和 successor validation，共 **10 条 symbol edge、13 个调用站点**。`reduce_graph_run`
是唯一 GraphRunCommand reducer。结论：**K=4，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.execution_transitions` | 6/6 | K | start/claim/fence/settle/advance/complete 六种执行 transition 由一个 dispatcher 选择，避免并行 reducer。 |
| 2 | `state.graph_state.lifecycle_transitions` | 1/1 | K | `abort_graph_run` 是 terminal abort 的唯一 transition，和执行 settlement 分开保持生命周期语义。 |
| 3 | `state.graph_state.recovery_transitions` | 1/1 | K | `resume_graph_nodes` 只处理 recovery action variant，不让 execution transition 解释 resume。 |
| 4 | `state.graph_state.validation` | 2/5 | K | reducer 在输入与输出边界调用统一 state validation，并递增 revision；validation 不反向 dispatch command。 |

把 dispatcher 改成动态表、或让各 transition 自己递增 revision，都会分裂 revision/variant 的单一事实。累计已审 **184 个 module pair：K=184，A=0，B=0**。

### 第 22 批：`execution.engine.claim_stage` 发出的 3 个模块对

本批检查 claim preparation 与 command projection，共 **3 条 symbol edge、3 个调用站点**。claim stage 只把 frontier preparation
封装成 typed、一次性的 claim，不解释或提交 state。结论：**K=3，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.claim` | 1/1 | K | `PreparedExecutionClaim` 保存 preparation 与 owner token，后续只能由对应 executor consume。 |
| 2 | `state.graph_state.command` | 1/1 | K | `ClaimGraphExecution` 是 claim 进入 durable reducer 的唯一 command 形状，stage 不直接改 lease。 |
| 3 | `state.graph_state.identity` | 1/1 | K | `GraphExecutionAttemptId` 为每次 claim 提供 nominal attempt identity；随机值生成和 state identity 仍各有 owner。 |

没有第二次 resource admission 或 claim replay；`prepare_claim` 与 `project_claim_command` 是 preparation/投影的窄分工。累计已审 **187 个 module pair：K=187，A=0，B=0**。

### 第 23 批：`state.graph_state.recovery_transitions` 发出的 3 个模块对

本批逐项核对 failed/interrupted/skip action 对 frontier 的原子更新，共 **8 条 symbol edge、15 个调用站点**。这是 State-owned
recovery transition，Execution 层只构造 command，不复制这里的 settlement 规则。结论：**K=3，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.frontier_model` | 4/5 | K | `PendingGraphNode`、`SkippedGraphNode` 和 `GraphFrontier*` 直接表达 action 后的 canonical frontier，避免平行 recovery state。 |
| 2 | `state.graph_state.identity` | 1/1 | K | `graph_interrupt_id` 统一校验 interrupted node 的 resume identity，不能在 transition 内自行计算字符串。 |
| 3 | `state.graph_state.validation` | 3/9 | K | action 更新后调用 `validate_graph_frontier`/`validated_graph_run_state`，确保恢复结果与普通 state transition 使用同一不变量入口。 |

三类 action 的 variant 匹配、interrupt ID 校验、codec 要求和 unknown node 错误顺序必须在一个纯 transition 中完成；拆到 Execution 会
形成第二 reducer。累计已审 **190 个 module pair：K=190，A=0，B=0**。

### 第 24 批：`state.graph_state.resource_reducer` 发出的 3 个模块对

本批检查资源快照结构校验、Acquire/Release command 和 FIFO acquisition replay，共 **7 条 symbol edge、7 个调用站点**。resource
reducer 是资源事实的唯一 owner，Graph frontier transition 只通过它消费结果。结论：**K=3，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.identity` | 1/1 | K | `_require_identity` 复用 canonical identity 规则，资源 reducer 不维护另一套 node/resource 字符串校验。 |
| 2 | `state.graph_state.resource_command` | 1/1 | K | `validate_resource_snapshot` 重放 `AcquireResources` command，验证快照确实来自 typed command 序列。 |
| 3 | `state.graph_state.resource_model` | 5/5 | K | `ResourceSnapshot`、`ResourceLock`、`ResourceAcquisition` 是同一资源 reducer 的 immutable model；`_acquire`/`_release` 不创建第二模型。 |

结构校验与 replay 校验是两层不同事实：前者检查形状/队列关系，后者证明 acquisition 历史；合并或省略 replay 会降低恢复可靠性。累计已审 **193 个 module pair：K=193，A=0，B=0**。

### 第 25 批：`state.graph_state.validation` 发出的 3 个模块对

本批逐项核对 durable state 的 frontier、identity 和 resource invariants，共 **5 条 symbol edge、6 个调用站点**。validation 只
拒绝非法 state，不产生 successor。结论：**K=3，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.frontier_model` | 2/3 | K | `pending_node_ids`/`frontier_status` 提供 authoritative frontier 查询，validation 不复制 node settlement 集合。 |
| 2 | `state.graph_state.identity` | 2/2 | K | `is_canonical_identity` 与 `child_graph_run_id` 统一 parent/child identity 规则，避免各 transition 自行拼接。 |
| 3 | `state.graph_state.resource_reducer` | 1/1 | K | resource snapshot 的详细结构和 replay 由 resource reducer 负责；validation 只把其错误转成 graph-state 边界错误。 |

`validate_graph_frontier`、`validate_graph_run_state` 和 `validated_graph_run_state` 形成一个清晰的验证层次；没有反向调用 transition 或
持有可变 state。累计已审 **196 个 module pair：K=196，A=0，B=0**。

### 第 26 批：`execution.graph.outcome` 发出的 2 个模块对

本批检查三个 public outcome factory 对 values 和 contract error 的边界，共 **7 条 symbol edge、7 个调用站点**。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 6/6 | K | success/failure/interrupt 的 seal、route、reason、payload 校验都属于 node outcome contract；不能用通用异常或 scheduler 校验替代。 |
| 2 | `execution.graph.values` | 1/1 | K | `_require_graph_values` 确保 success output 来自唯一 `Graph.values` owner，避免 outcome 携带未经 admission 的 mapping。 |

factory 的错误检查发生在用户 callable 返回值进入 scheduler 之前，和 scheduler 的 task/result 投影是相邻但不同的边界。累计已审 **198 个 module pair：K=198，A=0，B=0**。

### 第 27 批：`execution.graph.ports` 发出的 2 个模块对

本批核对 canonical port/name 和 declaration normalization，共 **7 条 symbol edge、11 个调用站点**。ports 是 graph declaration 的
唯一 owner；compiler/runtime 只消费 typed declarations。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 6/10 | K | input/output/graph-output 名称、类型和 binding 形状错误统一在 declaration 边界报告 `GraphValidationError`，不下沉到 runtime。 |
| 2 | `state.graph_state.identity` | 1/1 | K | `canonical_port_name` 复用 state 的 canonical identity predicate；保持一套名称规则而非 ports 私有副本。 |

`normalize_input_bindings`、`normalize_output_declarations` 和 `normalize_graph_output_declarations` 虽有相似循环，但分别代表三种
public declaration 语义；当前不应为消除静态 clone 或跨模块边引入泛型反射 helper。累计已审 **200 个 module pair：K=200，A=0，B=0**。

### 第 28 批：`execution.graph.validation` 发出的 2 个模块对

本批逐项检查 definition identity、edge/resource 形状和错误映射，共 **12 条 symbol edge、21 个调用站点**。验证发生在 compile 前，
不读取运行时 state。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 11/20 | K | duplicate node/edge/definition、invalid join/resource/identity 和 recursive definition 各自保留明确错误类型与声明顺序。 |
| 2 | `state.graph_state.identity` | 1/1 | K | `is_canonical_identity` 是 graph/node/route/resource 的共享 predicate，validation 不重新实现字符串规则。 |

`_validate_definition` 的 DFS 状态（VISITING/VALIDATED）与 `_validate_edges` 的单次声明遍历都属于静态 definition owner；拆成运行时
validator 会扩大边界并重复扫描。累计已审 **202 个 module pair：K=202，A=0，B=0**。

### 第 29 批：`execution.graph.values` 发出的 2 个模块对

本批检查 immutable values/frame admission 与 canonical name 规则，共 **15 条 symbol edge、19 个调用站点**。四种 frame wrapper
共享同一个 entries 校验，但各自保留语义标签。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 13/17 | K | malformed frame、descriptor 名称不匹配和 exact nominal type 错误均由 values owner 转成 `GraphValueAdmissionError`。 |
| 2 | `execution.graph.ports` | 2/2 | K | `canonical_port_name` 保证 values、ports declaration 和 frame entry 使用同一名称规范。 |

`_admit_entries` 是唯一 entry rule owner；`_admit_*_frame` 只提供四种 nominal frame 的窄包装。把它们改成一个带字符串 kind 的通用
函数会削弱类型边界，虽然数字可能下降。累计已审 **204 个 module pair：K=204，A=0，B=0**。

### 第 30 批：`execution.identity` 发出的 2 个模块对

本批检查 scoped coordinate 的构造和 child identity 派生，共 **4 条 symbol edge、4 个调用站点**。execution identity 只组合运行坐标，
不保存 state。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 3/3 | K | 非 canonical scope/run、非法 activation 和 identity mismatch 都在 coordinate 边界抛 `SnapshotMismatchError`，避免错误坐标继续流入 runtime。 |
| 2 | `state.graph_state.identity` | 1/1 | K | `child_graph_run_id` 是 parent→child deterministic identity 的唯一规则；execution 不复制 hash/拼接实现。 |

root/child coordinate 与 State identity 各自拥有不同事实：前者描述运行位置，后者定义稳定 ID。合并成一个宽 record 会模糊 owner。累计已审 **206 个 module pair：K=206，A=0，B=0**。

### 第 31 批：`execution.result` 发出的 2 个模块对

本批核对 task result 到 public/commit result 的封印、变体和 node-output projection，共 **9 条 symbol edge、9 个调用站点**。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 8/8 | K | partial commit、非法 result variant、错误 seal 和 snapshot lineage 不匹配分别属于 result/continuation contract，不能统一包装。 |
| 2 | `execution.graph.values` | 1/1 | K | `_public_node_output` 只在 commit projection 处把 admitted frame 转成 public immutable values，避免用户或 State 直接持有 frame。 |

`TaskResult`、`GraphCommitResult` 和三种 `GraphResult` 是不同阶段的协议类型；减少跨模块边而合成一个宽 result 会让 settlement/continuation
边界更不清楚。累计已审 **208 个 module pair：K=208，A=0，B=0**。

### 第 32 批：`mote_kernel.hooks.node` 发出的 2 个模块对

本批逐个查看 HookNode 的 graph-facing 构造、P1/P2/P3 priority chain 与 contract adapter，共 **11 条 symbol edge、19 个调用站点**。
这里重点确认 HookNode 没有创建私有 runner：它只是使用唯一 `Graph` facade 定义 topology，实际执行仍交给 kernel executor。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.facade` | 4/8 | K | `Graph.graph_input`、`Graph.node_output`、`Graph.values` 是 domain graph 进入唯一 public facade 的窄构造口；HookNode 不绕过 facade 或另建 execution path。 |
| 2 | `hooks.contract` | 7/11 | K | config snapshot、plan loader、request/result 和 contract error 由 hooks contract owner 定义；`_HookPort` 只做一次 typed invocation adapter。 |

P1→P2→P3 的线性 topology 是 HookNode 的业务顺序，不是跨模块冗余调用；将其改成通用 hook runner 会引入第二执行语义。累计已审 **210 个 module pair：K=210，A=0，B=0**。

### 第 33 批：`state.graph_state.lifecycle_transitions` 发出的 2 个模块对

本批检查 quiescent running graph 的 abort transition，共 **3 条 symbol edge、3 个调用站点**。结论：**K=2，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.model` | 1/1 | K | `GraphAbort` 是 durable terminal diagnostic 的唯一 model，lifecycle transition 不复制造 abort record。 |
| 2 | `state.graph_state.validation` | 2/2 | K | abort 前检查 status/lease，结果通过 `validated_graph_run_state` 统一验证，确保 terminal state 符合同一矩阵。 |

abort 与 execution settlement 分开是生命周期语义边界，不是重复 reducer；合并会允许 active lease 被错误终止。累计已审 **212 个 module pair：K=212，A=0，B=0**。

### 第 34 批：`execution.graph.topology` 发出的 1 个模块对

本批检查 scoped compiled graph lookup 的唯一跨模块调用，共 **1 条 symbol edge、1 个调用站点**。结论：**K=1，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 1/1 | K | `_compiled_graph_at_scope` 找不到 nested segment 时抛 `SnapshotMismatchError`；topology 只读 immutable plan，不接管 state 校验。 |

这是 recovery/invocation 需要的窄 scoped lookup，不能为少一条边把 compiled graph 复制到各调用方。累计已审 **213 个 module pair：K=213，A=0，B=0**。

### 第 35 批：`execution.limits` 发出的 1 个模块对

本批检查执行上限的 typed constructor 校验，共 **1 条 symbol edge、1 个调用站点**。结论：**K=1，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 1/1 | K | 非正整数或错误类型的 limits 在构造边界抛 `ExecutionLimitError`；limit policy 不应散落到 planner/scheduler。 |

单独的 limits→errors 边表达清晰的 policy ownership，不值得通过内联异常或泛化 `ValueError` 降指标。累计已审 **214 个 module pair：K=214，A=0，B=0**。

### 第 36 批：`execution.run_context` 发出的 1 个模块对

本批检查四类 frame index 查找、重复 publication 和 continuation lineage 错误，共 **10 条 symbol edge、11 个调用站点**。结论：**K=1，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `execution.errors` | 10/11 | K | `SnapshotMismatchError` 与 `GraphValuePublicationError` 分别覆盖坐标缺失/lineage 不符和重复证据；frame/evidence owner 不应把它们降为普通 lookup error。 |

`ScopedFrameIndex` 的四种 coordinate 分支和 add_* 的 duplicate guard 是同一个不可变 evidence boundary；拆成多个 map 或宽字符串 key 会引入第二事实源。累计已审 **215 个 module pair：K=215，A=0，B=0**。

### 第 37 批：`mote_kernel.hooks.identity` 发出的 1 个模块对

本批检查 HookSlotId 对 graph/state identity predicate 的唯一依赖，共 **1 条 symbol edge、2 个调用站点**。结论：**K=1，A=0，B=0**。

| # | 目标模块 | edges/sites | 判定 | 审查结论 |
|---:|---|---:|:---:|---|
| 1 | `state.graph_state.identity` | 1/2 | K | Hook slot 的 definition/node identity 复用 kernel 的 canonical predicate；hooks 不建立一套相容但不同的字符串规则。 |

至此启动时基线的 **216/216 个 module pair 全部逐项审完：K=216，A=0，B=0**。剩余没有证据支持直接删除跨模块边，
也没有需要先做原型的边；所有结论都建立在当前 owner、typed boundary、错误优先级和纯 transition 保持不变的前提上。

## 逐项审查矩阵

每条新增或增长的模块边，按下表提问。判断标签沿用原审查文档的 `A / B / K`，但这里的对象是“边界关系”，不是单个函数。

| 审查信号 | 需要回答的问题 | `K` 保留条件 | `A` / `B` 处理方向 |
|---|---|---|---|
| 新的 module pair | 为什么必须跨这个边界？是否已有窄 typed port？ | 依赖方向符合 owner，调用语义属于 source 的职责。 | `A` 删除重复穿透；`B` 先画边界和 owner，再做原型。 |
| 已有 pair 的 symbol edges 增长 | 是同一规则的多个入口，还是把内部实现扩散给更多调用方？ | 每条边对应不同且必要的领域事实/异常边界。 | 把共享规则收回唯一 owner，不能只加 wrapper。 |
| source fan-out 增长 | 一个模块是否开始知道太多下游细节？ | 它确实是受约束的编排 owner，且依赖方向仍单向。 | 研究窄 port 或阶段边界；不直接引入宽 `Context`。 |
| 高层 → 低层直接调用 | 是否跳过了 facade、service 或 state owner？ | 低层能力就是该 source 的明确 typed port。 | 迁移调用到正确 owner，并删除旧边。 |
| 低层 → 高层反向调用 | 是否形成隐性循环或反向控制？ | 只有在明确的协议回调边界且没有反向状态拥有权时才保留。 | 优先提取窄 protocol；若只是通知，考虑由上层驱动。 |
| `call_sites` 很多但 symbol 边很少 | 是否把同一依赖散落在许多位置？ | 重复位置各自有清楚的错误/生命周期语义。 | 先确认能否集中编排，再比较完整调用链总复杂度。 |
| 只触发 ambiguous dispatch | 静态索引是否看不清真正 target？ | 动态边界是明确的外部扩展点，有测试和文档保护。 | 补窄 protocol/类型信息，或把动态分发集中在一个 owner。 |

### 一条边的最小审核记录

人工审核至少记录四件事：

```text
source -> target
调用者拥有的事实 / 被调用者拥有的事实
为什么必须跨边界，以及是否存在更窄的 typed port
决定：K 保留 / A 直接化简 / B 先做设计原型
```

如果判为 `K`，记录“为什么合理”比增加例外清单更有价值；如果判为 `A`，必须能指出要删除的旧边或重复规则；如果判为 `B`，先证明
总调用链会变短且不会出现第二事实源，再改生产代码。

## 与原有门禁的关系

| 现有指标 | 它回答的问题 | 新指标补上的空白 |
|---|---|---|
| `internal_call_edges`、`max_call_chain_depth` | 具体 callable 之间有多少边、链有多深？ | 把边聚合到模块边界，观察架构方向和边界宽度。 |
| `internal_import_edges`、import cycles | 文件/模块静态依赖是否存在、是否成环？ | import 可能存在但不运行，也可能通过已导入符号形成实际调用；新指标看后者。 |
| `ambiguous_internal_dispatches` | 哪些调用无法确认 target？ | 新指标只收已确认 target，未知情况仍单独报警，避免混为一谈。 |
| clone、thin、transparent、linear chain | 局部代码是否重复或可能只是转发？ | 新指标检查“跨模块的边界关系”，即便每个局部函数都很短也能提示。 |
| cohesion、class structure | 一个类是否拆成多个职责组件？ | 模块边界是类之外的另一层结构，不替代 class cohesion。 |
| async ownership、stateful async | await 与状态/任务所有权是否安全？ | 新指标只提示调用方向；取消、提交和并发行为仍由这些检查与测试负责。 |

因此，“最长调用链没有增长”不代表没有新的架构耦合；反过来，“模块对很多”也不代表设计错误。几条线要合并看，但每条线只维护
自己的事实和判定边界。

## 对架构简洁性的可用结论

这条指标可以帮助回答四个问题，但不能单独给出“架构简洁/不简洁”的结论：

1. **边界是否变宽**：module pair、symbol edges 和 call sites 是否同时增长？
2. **依赖是否变散**：fan-out 是否由一个明确编排 owner 以外的模块承担？
3. **依赖方向是否清楚**：是否出现反向 runtime edge，或绕过既有 facade/state/reducer owner？
4. **局部合理是否掩盖整体冗余**：每个函数都短，但是否把同一规则分散到多个模块？

当前没有反向 runtime pair 是好信号，但不应据此宣称架构已经最简；例如一个单向模块图仍可能有过多层转发或过宽的 public
facade。判断这些问题需要结合原有调用链报告、代码阅读和行为测试。

## 误报与漏报边界

### 常见误报

- 唯一 facade 调用多个内部模块；
- family driver 编排 child、session、result 和 cleanup；
- 多个模块统一调用领域错误类型；
- compiler/validation 之间共享 typed 声明；
- 为保持异常优先级而存在的多处显式调用。

这些情况可以判为 `K`，但应在审查记录中写清 owner 和顺序，避免下一次评审重复争论。

### 仍可能漏掉的情况

- `getattr`、反射、字符串路由、动态 import、插件注册表；
- 通过 `Callable`、容器或外部依赖注入后才确定的 target；
- 第三方包内部的调用链；
- 不是函数调用的运行时数据耦合，例如共享全局、隐式事件名或协议字段；
- 多个“各自合理”的调用在特定输入、取消或恢复时序下组合出错误。

最后一类不属于复杂度门禁的职责，仍必须由单元、集成、恢复和并发测试覆盖。高召回不等于对动态语言的行为做到完备证明。

## 人工审核流程

每个涉及生产代码的 PR 建议按以下顺序执行：

1. 运行 `make complexity-report`，只筛选 diff 新增/修改的 source 或 target symbol。
2. 先看新的 module pair，再看既有 pair 的 symbol edge 和 call-site 增长，最后看 fan-out 是否变化。
3. 沿完整调用链确认 source/target 各自的事实 owner、输入输出和异常边界。
4. 对照原有 clone、thin、linear chain、hotspot 和 async 报告，检查是否是同一重复问题的另一种表现。
5. 记录 `K / A / B` 结论；`A` 要给出删除范围，`B` 要给出原型和净复杂度证明。
6. 若确认是合理新增边，保留代码和测试即可，不添加静默白名单；ratchet 基线应随有意的架构决策显式更新。

推荐优先级是：

```text
diff 命中的新增边
  -> 反向边 / 高层直达低层边
  -> fan-out 增长
  -> 高 call-site 密度
  -> 其余已有基线边
```

这样可以维持高召回，又不会要求评审者每次从头阅读全部 216 个模块对。

## 实施状态与验证

- 指标模型：`RuntimeModuleCallPair` / `RuntimeModuleCallMetrics`；
- 统计入口：`runtime_module_call_metrics()`；
- 报告标题：`Resolved runtime calls crossing module boundaries`；
- 确定性测试：覆盖函数调用重复计数、类构造、多个模块对和 `call_sites` 聚合；
- ratchet 上限：已写入 `pyproject.toml`，当前值与实测值一致；
- 文档入口：本文件与 README 的复杂度门禁说明并列。

启动基线在 events 文件出现前已完成 `make check`、全量 pre-commit，960 个测试通过且覆盖率 100%。当前工作树包含你明确要求
暂不纳入本轮的 events 开发文件，实测验证如下：

```text
make lint                 PASS
pyright                   PASS
make complexity           PASS
make test                 965 passed, 1 failed (complexity ratchet)
coverage                 100%
git diff --check          PASS
```

唯一失败是 `test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked`：events 使全树观测值暂时变为
`cross_module_call_edges=730`、`cross_module_call_pairs=221`（基线仍是 `715/216`）。按约定没有修改 events、测试或 ratchet 上限；
events 独立开发完成后，应先单独审核其新增边，再决定是否更新基线。

日常查看完整候选：

```bash
make complexity-report
```

门禁入口仍然是：

```bash
make complexity-ratchet
make complexity
```

## 后续治理账本

这条指标的成功标准不是把 `715` 机械降到某个漂亮数字，而是：

- 新增跨模块边都有明确的 owner 和理由；
- 依赖方向保持可读，避免隐性反向控制；
- 重复规则回到唯一事实源，不用 wrapper 或宽 context 掩盖；
- 合理的 facade/orchestrator 复杂度有人工记录，不被误报驱动成第二入口；
- 真实简化后同步下调 ratchet，确保改进不会回退；
- 动态边界和组合行为由类型、测试、恢复和并发验证补齐。

这使跨模块耦合成为一条稳定的高召回人工审核线，而不是另一套自动架构裁判。
