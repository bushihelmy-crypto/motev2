# 49 个复杂度热点的可化简性审查

## 审查与治理原则

本次审查和后续治理以以下原则为最高判定标准：

- **唯一真相**：每项规则、状态和事实只能有一个权威 owner，不建立别名、镜像状态或第二执行路径。
- **复用基础设计**：相同规则集中维护并复用已有 compiler、planner、routing、reducer 和 typed frame 设计，同时保留不同领域的异常边界。
- **逻辑最简**：以完整调用链的总复杂度判断设计，不用机械拆函数、转发 helper 或宽 context 掩盖复杂度。
- **一次成功**：先完整设计调用链、不变量、迁移和删除范围，再一次性迁移生产代码、测试和示例；不留下临时双路径。
- **零技术债**：对无意义抽象、重复路径、兼容层、隐性状态和未明确归属的规则零容忍。
- **代码优美**：代码本身应简洁、易懂、易维护，读者可以直接从结构看出 owner、状态转换、异常边界和行为顺序。
- **满足全部门禁**：类型、测试、覆盖率、复杂度、架构和 pre-commit 门禁必须全部通过；但“门禁通过”不等于“代码已经高质量”。
- **不污染生产代码以兼容 legacy test**：只迁移 legacy 测试中仍有价值的行为语义、错误优先级和恢复边界，不保留旧 API alias、wrapper 或兼容执行路径。

最终判断顺序是：先判断设计和代码本身是否足够简单、清晰且唯一，再用门禁证明实现没有回归；不能反过来以门禁通过替代质量判断。

## 结论先行

本次审查的对象是 `src/mote_kernel` 基线复杂度报告列出的 49 个定义。结论不是“门禁通过所以无需处理”：

- 基线的 `complexity_hotspots=49` 确实成立，但它是“任一指标超阈值”的定义数量，不是 49 个互相独立的缺陷；#14 完成后降为 48，#33 完成后降为 47。
- 49 项中有 **8 项存在可以现在就描述清楚的更简单目标设计**（表中标为 `A`），现已全部完成并验收。
- `A` 的编号是：**3、11、12、14、20、38、39、40（均已完成）**。#15/#16 复核后改判为 `K`。
- 另有 **7 项有局部整理方向**（标为 `B`）；#27/#28/#33 已完成并验收，剩余 4 项只有证明能净删除且保留唯一事实源后才实施。
- 原始 49 项中现有 **34 项判为保留**（标为 `K`）：复杂度直接表达固定点/图算法、资源 FIFO、异步状态机、原子 reducer、领域异常优先级或有意统一的友好公共 API。#1/#2 复核后确认资源阶段边界清楚，不为共享 view 增加额外 wiring；#7/#10 复核后确认恢复阶段顺序和证据封口各有独立语义，不为阶段化增加 helper；#13 独立看无需机械包装，但随后作为 #14 重复路径的一部分被净删除；#18 复核后只做局部 guard-clause 净化，不再列为独立 B 项。

`A` 不是“可以偷偷加一层适配器”的许可。所有改动都必须满足：一个 owner、一份事实、纯 state transition、持久化确认后才替换内存快照、无第二 runner、无 legacy 兼容别名。

本文件以设计审查为主，并同步记录实施状态。截至 2026-09-01，#3、#11、#12、#14、#18、#20、#27、#28、#33、#38、#39、#40 已完成并验收；#1、#2、#7、#10、#15、#16、#19、#34、#41 复核后保留现状。

## 门禁基线与判定口径

复杂度热点条件（任一满足即计入）为：

| 指标 | 阈值 |
| --- | ---: |
| cyclomatic (`cc`) | > 10 |
| cognitive (`cog`) | > 15 |
| nesting (`nest`) | > 4 |
| parameters (`params`) | > 6 |
| semantic nodes (`nodes`) | > 150 |

基线（2026-08-31，当前提交 `515e468`）：

```text
make complexity                 PASS
complexity_hotspots             49  (configured ceiling 49)
semantic-node hotspots          39
cyclomatic hotspots             38
cognitive hotspots              34
nesting hotspots                 5
parameter hotspots               4
make complexity-ratchet         21 passed
make check                      926 passed, coverage 100%
```

报告中的指标顺序固定为 `cc / cog / nest / params / nodes`。`pyproject.toml` 的 ratchet 当前几乎全部等于实际值；真正降低指标后必须同步下调上限，不能只让门禁继续接受旧数字。函数拆分也可能增加定义数，因此要看净变化，不把“多了几个 helper”当作简化。

判定含义：

- `A 明确可化简`：可以给出具体目标结构、事实 owner、异常边界和行为顺序；实施时应删除旧路径，而不是并存。
- `B 条件可化简`：有合理方向，但需要先做小型设计/原型，证明不会引入第二事实源、宽 context 或更高的总复杂度。
- `K 保留`：当前复杂度是必要不变量的可读表达；没有更简单且同样安全的设计证据。

`A` 表示设计目标已经明确，不等于每个 ratchet 数值必然下降；实际合并仍要有“删除了什么、增加了什么、总量是否净下降”的账本。

## 逐项清单（49 项全部覆盖）

### A. admission / recovery / resume / routing（1–16）

| # | 位置（指标） | 当前职责 | 判断与目标 |
|---:|---|---|---|
| 1 | `execution/engine/admission.py:97` `admit_tasks`（16/23/3/3/232） | 校验资源快照、按 canonical task 重放 acquisition、分类 admitted/waiting。 | **K（评审后保留，2026-09-01）**：claim 生成待提交的资源快照，selector 读取已提交且会变化的快照；两者的 nested-task 与错误边界不同。引入共享 `ResourceAdmissionView` 需要额外传递和过滤，不能净化简逻辑。 |
| 2 | `execution/engine/admission.py:153` `select_executable_tasks`（12/16/3/6/151） | runtime 与 recovery 共用的 slot、started-node、resource admission 选择策略。 | **K（评审后保留，2026-09-01）**：runtime 与 recovery 已经共用这一条选择规则；重复的只是静态 node/resource 查找，不是第二套策略。为消除几行查找而引入 typed view 会增加调用链复杂度。 |
| 3 | `execution/engine/frontier.py:37` `prepare_frontier`（14/17/3/2/216） | 规划 task、校验 child projection 覆盖、投影 nested 结果、决定是否物化 callable 输入。 | **A（已完成，2026-08-31）**：发现 `MissingChild`/`ActiveChild` 后直接返回 `WaitingForChildren`，只在 child 全部终结后物化 callable 输入；删除 `FrontierPreparation` 中重复的 child 状态字段，不新增 helper 或第二 planner。 |
| 4 | `execution/engine/recovery.py:494` `recovery_traversal_key`（20/21/2/1/367） | 将 transfer state 的所有会影响可达性的字段编码为确定性 worklist key。 | **K**：这是去重和确定性证明的算法输入；改成 `repr`、对象 hash 或删字段都会改变可达分支语义。 |
| 5 | `execution/engine/recovery.py:661` `_child_outcomes`（8/10/2/6/217） | 为一个 nested activation 建立/校验 child state，并处理 completed、aborted、递归 proof 边界。 | **K**：分支对应 child 生命周期边界；拆分只能隐藏递归算法，不能减少不变量。 |
| 6 | `execution/engine/recovery.py:729` `_nested_outcome_plans`（15/20/2/6/227） | 组合多个 nested child 的 completed/aborted/waiting/limit 替代路径。 | **K**：组合爆炸是恢复证明本身，不是重复代码；不能以单一路径或隐式 fallback 取代。 |
| 7 | `execution/engine/recovery.py:807` `_expand_quiescent_executable`（16/22/2/4/274） | 从 quiescent frontier 规划 task、证明 child outcome、检查历史输入、claim resource 并生成 successor。 | **K（评审后保留，2026-09-01）**：child proof、历史输入、resource claim、settlement 的顺序和错误边界各自不同；拆阶段只会增加跳转，不能减少规则，也不能复制 `_prove_scope`。 |
| 8 | `execution/engine/recovery.py:933` `_resolve_quiescent`（12/15/2/4/178） | 将 routing facts 投影为 advance/complete/abort，并区分历史值缺失与正常 abort。 | **K**：错误优先级和 `GraphValueUnavailableError` 边界是恢复契约；额外 helper 不会使规则更简单。 |
| 9 | `execution/engine/recovery.py:978` `_prove_scope`（12/25/3/5/319） | 有界 worklist、seen transfer state、terminal boundary 收集和递归 successor 入队。 | **K**：这是恢复状态空间算法的唯一 owner；不能按分支复制 runner。 |
| 10 | `execution/engine/recovery.py:1082` `preflight_recovery`（21/33/3/2/315） | 校验 seed/binding/action 证据，构造 availability，再启动全 scope proof。 | **K（评审后保留，2026-09-01）**：这些检查分别封住 seed 身份、action 与模拟 state 的对应关系、frame availability 和 proof 输入；顺序还决定异常优先级。拆成两个 helper 不会删掉重复规则，只会增加跳转，且不能复制 `_prove_scope`。 |
| 11 | `execution/engine/resume_admission.py:76` `prepare_resume`（21/40/4/2/471） | 校验单 scope resume action，构造 resume command、resume input 和 skip substitution。 | **A（已完成，2026-08-31）**：删除手工 `replacements`、模拟 `GraphFrontierState` 和重复 frontier validation；`plan_resumes` 继续只用唯一 reducer 生成 exact successor，`admit_resume_candidates` 在证据边界用同一 reducer 复核。动作分支与错误顺序不变。 |
| 12 | `execution/engine/resume_admission.py:190` `admit_resume_candidates`（40/50/3/2/499 → 31/38/3/2/419） | 跨 scope 校验候选 successor/substitution，检查 duplicate、publication collision、routing availability。 | **A（已完成，2026-08-31）**：每个 candidate 只保留一次生成的当前 skip 节点 ID 集合；删除逐 substitution 查找完整 action、reducer 已证明的 settlement/reason/routing 二次核对，以及只为判断 pure skip 重建 publication 坐标。三组组合测试锁定 **evidence → duplicate publication → confirmed collision → unavailable** 的异常优先级。 |
| 13 | `execution/engine/resume_input.py:121` `_publication_value`（2/1/1/7/73） | 根据一个 compiled binding 和 activation selection 读取单个 publication。 | **K（随 #14 净删除，2026-08-31）**：独立看不应为降参数机械包装；#14 直接复用已有 `ResolvedInputBinding` 后，这条单用途读取路径不再需要，也没有引入 context bag。 |
| 14 | `execution/engine/resume_input.py:194` `materialize_node_input`（9/10/3/6/208 → 9/9/2/6/223） | 校验当前 settlement，优先 override/resume frame，否则逐 binding 读取 graph input/publication 并构造 NodeInputFrame。 | **A（已完成，2026-08-31）**：新增一个消费 compiled binding 的窄坐标解析步骤，由 `node_inputs_available` 与 materializer 共用；删除 `_publication_value` 及重复坐标计算。materializer 仍独占具体 frame 读取和 `GraphValueUnavailableError` 包装，缺失 publication selection 仍抛 `SnapshotMismatchError`。 |
| 15 | `execution/engine/routing.py:176` `resolve_routing_facts`（15/20/3/4/283） | 校验 join progress、收集 control/join arrivals、计算 required targets 和 graph-output diagnostics。 | **K（评审后保留，2026-08-31）**：现有函数已经按 arrival accumulation、cached required target、output diagnostics 三个连续区块表达；拆 helper 不能删除规则或 owner，只会增加跳转并分散校验顺序。 |
| 16 | `execution/engine/routing.py:213` `resolve_routing_facts.required`（10/14/3/1/180） | 为一个 target 计算历史输入缺失、可用 publication 和诊断名称。 | **K（随 #15 评审后保留，2026-08-31）**：局部函数和局部缓存已经是最窄的 target index，同一 target 被 direct/join 同时引用时只扫描一次；提取新 class/helper 不会净删逻辑，且 routing 与 materialization 的异常边界不同。 |

### B. session / facade / family driver（17–28）

| # | 位置（指标） | 当前职责 | 判断与目标 |
|---:|---|---|---|
| 17 | `execution/engine/session.py:180` `_GraphExecutionSession.next`（12/21/3/2/176） | ack 上一条 reducer successor、排空 scheduler、处理 node-origin cancellation、一次交付一个 completion。 | **K**：这是单消费者 session 协议和 cancellation 时序；把循环拆散会掩盖 ack/close 顺序。 |
| 18 | `execution/engine/snapshot_guard.py:22` `require_snapshot_matches_graph`（22/21/2/2/212） | 校验 state 自身、compiled identity、join/routing、resume codec 和 active resource participants。 | **K（局部净化完成，2026-09-01）**：无 active execution 时直接返回，资源参与者改为通过“节点 → 所需资源”映射一次比较；保留原有校验顺序、异常类型和 `validate_graph_run_state` 的唯一 owner，不再拆阶段 helper。 |
| 19 | `execution/facade.py:298` `Graph.add_node`（10/15/2/6/216 → 9/13/2/6/201） | 一个 API 同时接受 callable node 和 nested graph，并用 `None`/运行时类型决定 outputs/resources 规则。 | **K（评审后保留，2026-08-31）**：现有两个 `@overload` 已分别表达 callable 与 nested graph 的合法参数，对外保留统一的 `add_node` 更友好；union 分支是 Python 动态调用所需的运行时校验，不拆公共入口，也不为降指标增加转发 helper。#33 顺带删除资源 order 的重复构造，但不改变这个公共 API 判断。 |
| 20 | `execution/facade.py:594` `Graph.run`（28/42/3/9/444 → 22/34/3/9/356） | 新运行、state-only recovery、continuation admission、preflight、owner drive、取消、partial commit 和 cleanup 全部串在一个公共方法。 | **A（已完成，2026-08-31）**：公开 `run` 与三个 overload 不变；fresh/continued 分支只生成各自的 root admission，再共用一次 owner-task 等待，驱动、结果投影和取消矩阵收进局部 `drive_project_finish` 阶段。没有新增 context、模块级 helper 或第二 runner，abort → release 与 cleanup 错误优先级不变。 |
| 21 | `execution/family_driver.py:269` `_frames_for_owner`（12/14/2/3/126） | 将全局 continuation frame index 投影到一个 owner，并验证 child binding。 | **K（指标误报型）**：是短而清晰的 scoped projection；抽象成泛型过滤器会丢掉 child-boundary 领域检查。 |
| 22 | `execution/family_driver.py:352` `_GraphRun.__init__`（1/0/0/11/134） | 注入一个 live owner 真正需要的 graph、state、frames、commit、child constructor、position 和 evidence publisher。 | **K（参数误报型）**：11 个依赖是 ownership wiring，不应为了参数门禁引入宽 `RunContext`。 |
| 23 | `execution/family_driver.py:558` `_GraphRun._drive_child`（12/12/2/2/139） | 读取 child handle 结果，检查 awaiting/terminal 类型对应关系并安装 boundary。 | **K**：这些分支是 terminal projection 不变量，不是可合并的重复路径。 |
| 24 | `execution/family_driver.py:593` `_GraphRun._consume_session`（9/17/3/3/164） | 逐 completion 提交 settlement、安装 publication、处理 node-origin cancellation、retire nested child。 | **K**：transaction 和 state/publication 先后顺序必须集中在此 owner。 |
| 25 | `execution/family_driver.py:656` `_GraphRun.drive_quantum`（11/28/4/1/121） | 在 resolve、execute、start child、drive child、awaiting-resume 之间推进一个 scope。 | **K**：这是唯一 scope state machine；按分支复制驱动器会违反 execution 单引擎。 |
| 26 | `execution/family_driver.py:754` `_GraphRun.abort`（13/21/3/2/133） | 按 child/session/fence/abort 顺序清理并聚合首个错误。 | **K**：清理顺序和错误优先级是资源安全契约，不能用通用 finally 包装隐藏。 |
| 27 | `execution/family_driver.py:917` `admit_continued_root`（9/11/3/10/278 → 9/11/3/10/263） | 重建 root/child owners，按 scope 应用 fence/resume，处理 partial commit 和失败清理。 | **B（已完成并验收，2026-09-01）**：复用唯一 owner 构造与 cleanup 路径，保留显式的 `confirmed_prefix`、`transition_attempted` 和 `failed_scope`，没有新增 context 或第二 admission owner。 |
| 28 | `execution/family_driver.py:1017` `admit_continued_root.admit_children`（8/13/3/4/221 → 7/11/3/4/185） | 过滤当前 activation、重建 terminal child 或构造 running child，并完成 handoff。 | **B（随 #27 已完成并验收，2026-09-01）**：terminal child 直接重建 phase，running child 统一走 owner 构造、admission 和 handoff；child handle 仍只由同一个 `_GraphRun` owner 管理。 |

### C. compiler / graph validation / values（29–36）

| # | 位置（指标） | 当前职责 | 判断与目标 |
|---:|---|---|---|
| 29 | `execution/graph/compiler.py:190` `_guaranteed_sets`（11/27/5/3/160） | 计算每个节点在所有 activation gate 下都保证已经发生的 producer 集合。 | **K**：交集固定点是拓扑证明本身；`nest=5` 不表示可以改成一次线性遍历。 |
| 30 | `execution/graph/compiler.py:316` `_validate_joint_activation_paths`（38/57/3/6/435） | 建依赖图、去除 cycle-reachable 节点、按拓扑顺序合并 route requirements，并验证 join 可共同到达。 | **B（高优先级设计）**：存在把 route requirement 建模成窄 lattice/phase result 的机会，但必须先独立设计；禁止用宽 bag、字符串 discriminator 或第二套 compiler。 |
| 31 | `execution/graph/compiler.py:411` `_absolute_activation_levels`（13/23/4/3/142） | 对非循环节点求绝对 activation level，供 publication selection 使用。 | **K**：这是确定性 level fixed point；拆 helper 不会改变算法复杂度。 |
| 32 | `execution/graph/compiler.py:464` `_compile_graph`（74/120/4/2/1238） | 递归 nested compile、解析 ports、建 data/control topology、验证可达性、计算 guarantees/levels、构造所有 descriptors。 | **B（最高设计收益）**：应考虑“输入解析 → 拓扑证明 → publication/materialization → immutable plan”四个纯 phase，以窄的 `CompilationFacts` 传递；只有能删除当前大段交叉循环才实施，不能机械拆成十几个 forwarding helper。 |
| 33 | `execution/graph/validation.py:44` `_validate_resources`（11/14/2/1/133 → 9/12/2/1/110） | 校验 graph resource catalog 的 identity、声明顺序和 node requirements。 | **B（已完成并验收，2026-09-01）**：删除 `ResourceDefinition.order`，让 `GraphDefinition.resources` tuple 顺序成为静态资源顺序的唯一来源；保留 identity、唯一 ID、节点要求和运行时 snapshot 的各自校验边界。 |
| 34 | `execution/graph/validation.py:64` `_validate_edges`（23/33/3/2/293） | 校验 direct/conditional/join edge 的 endpoint、重复、route 和 nested-source 规则。 | **K（评审后保留，2026-08-31）**：当前单次循环按声明顺序就地校验三种边，错误类型和优先级清晰且已有确定性测试保护；拆成三个 validator 只会增加参数传递和跳转，不删除规则，也不共享 join 的不同错误语义。 |
| 35 | `execution/graph/validation.py:109` `_validate_definition`（14/18/2/2/232） | 递归校验 graph identity/version、节点、资源、边、codec，并区分递归与 identity collision。 | **B**：可把“visit 状态/递归”与“当前 definition shape”分成两个纯 phase；必须保留 duplicate-definition 与 recursive-definition 的不同错误。 |
| 36 | `execution/graph/values.py:152` `_admit_entries`（11/11/2/3/144） | 所有 frame wrapper 共用的 canonical entry/name/exact-type 校验。 | **K（已是共享基础设计）**：它正是唯一 entry rule owner；四个外层 wrapper 只保留 graph-input/node-input/node-output/graph-output 的异常边界，不再泛化。 |

### D. invocation / frame index / state reducer（37–49）

| # | 位置（指标） | 当前职责 | 判断与目标 |
|---:|---|---|---|
| 37 | `execution/invocation.py:166` `lineage_states`（9/8/1/2/151 → 9/8/1/2/150） | 校验 child binding 的 canonical order、唯一 scope/activation，并形成 root + descendants lineage。 | **K（指标误报型）**：canonicality guard 本身保持直白；#38/#39 只消费它生成的窄索引，不改变 lineage 的状态语义。 |
| 38 | `execution/invocation.py:189` `plan_fences`（10/16/3/2/193 → 10/16/3/2/186） | 校验每个 scoped state 的 parent/coordinate，并计算 active execution 的 fence successor。 | **A（已完成并验收，2026-09-01）**：消费只保存 canonical bindings 的不可变 `_PlannedLineage` 索引；父子校验仍按原顺序，fence 仍是独立 command。替换 state 时按已知位置更新，不再整表扫描并排序。 |
| 39 | `execution/invocation.py:311` `plan_resumes`（13/16/2/4/376 → 10/12/2/4/356） | 按 scope 排序 action、生成 candidate state/frame/facts，并调用 resume admission。 | **A（已完成并验收，2026-09-01）**：canonical action 仍只做一次；直接迭代 `groupby`，每次只物化当前 scope 的 actions，并复用同一个 `_PlannedLineage`；duplicate 优先级不变。 |
| 40 | `execution/invocation.py:427` `_validate_frame_index`（45/49/2/3/529） | 校验四种 nominal frame segment 的类型、坐标 canonicality、descriptor/provenance 和 scope membership。 | **A（已完成，2026-08-31）**：主函数保留全 segment 的 nominal shape → canonical order 检查，再按固定顺序调用四个 typed record validator；未使用 generic callback/reflection，原有错误优先级不变。 |
| 41 | `execution/invocation.py:525` `_validate_complete_context`（16/34/5/3/215） | 在非 recovered continuation 中检查每个成功 publication、pending input、completed output 和 child boundary。 | **K（复核后纠正，2026-08-31）**：`validate_context` 已先执行 frame validation，再只对非 recovered continuation 执行 completeness audit；初审描述的分层原本就存在。当前单次 state 顺序遍历集中表达缺失数据的错误优先级，不再拆 helper。 |
| 42 | `execution/run_context.py:227` `ScopedFrameIndex.lookup`（12/36/5/2/80） | 按四种 coordinate 的 nominal 类型查找对应 frame record。 | **K**：四个分支代表四个类型安全边界；改成一个裸 map/字符串 tag 会丢失 overload 和领域错误。 |
| 43 | `state/graph_state/execution_transitions.py:150` `settle_graph_node`（17/23/3/2/247） | 校验 execution lease，转换 success/failure/interrupt，释放资源并原子更新 frontier。 | **K**：一个 command 必须原子完成 settlement + resource release + lease 清理；不能拆成可独立提交的路径。 |
| 44 | `state/graph_state/recovery_transitions.py:29` `resume_graph_nodes`（22/34/5/2/258） | 校验三种 resume action，按 node identity 更新 frontier，并保留 interrupt/codec 约束。 | **K**：action variant 与失败顺序就是 State 的领域异常边界；Execution 不能复制一份 reducer。 |
| 45 | `state/graph_state/reducer.py:30` `reduce_graph_run`（12/27/6/2/154） | 唯一 GraphRunCommand dispatch、revision 校验与 revision +1。 | **K**：这是唯一 reducer 入口；改成动态表或多个 reducer 会破坏单一真相。 |
| 46 | `state/graph_state/resource_reducer.py:24` `_validate_snapshot`（29/50/3/1/356） | 校验 resource lock/waiter/acquisition 的形状、顺序、所有权和队列一致性。 | **B（谨慎）**：可分出“静态 shape”与“replay consistency”两个阶段，但两者都必须由 State resource owner 维护；不得把校验移到 Execution，也不能省略 replay。 |
| 47 | `state/graph_state/resource_reducer.py:123` `_release`（14/14/2/2/163） | 释放 admitted acquisition，按全局资源顺序推进 FIFO waiter。 | **K**：资源 FIFO 和多资源 prefix 是核心算法；任何泛化都容易改变 waiter 顺序。 |
| 48 | `state/graph_state/validation.py:71` `validate_graph_frontier`（18/23/4/2/205） | 校验每个 frontier settlement、routing、interrupt payload 和 codec 必要性。 | **K**：这是 durable frontier 的总不变量，分支对应 nominal settlement 类型。 |
| 49 | `state/graph_state/validation.py:125` `validate_graph_run_state`（33/43/2/1/357） | 校验 parent identity、join progress、resource/execution lease 和三种 lifecycle status。 | **K**：状态合法性矩阵必须集中在 State owner；拆开会产生状态真相分裂。 |

## 明确可化简项目的目标设计

下面展开 `A` 项的目标设计，并保留讨论后改判为 `K` 的决策记录；实施项必须可以据此写出明确设计和测试，不能停留在泛泛的“可以重构”。

### 1. Resume admission：一个 owner、五个阶段（#11/#12/#38/#39 已完成）

目标调用链：

```text
canonical actions
  -> current settlement / resume-input or skip-substitution evidence
  -> exact reducer successor
  -> publication duplicate / confirmed collision
  -> routing availability
  -> PlannedResume + CandidateFrameAvailability + facts
```

具体约束：

1. `plan_resumes` 只负责按 scope 编排并调用唯一 reducer 生成 exact successor；`prepare_resume` 只负责一个 scope 的 action 与 evidence；`admit_resume_candidates` 只负责跨 scope 证据和 availability。#11 已删除 `prepare_resume` 中手工模拟 frontier 的第二套表达；#12 已让 reducer proof 继续作为 successor 唯一事实，只保留 substitution 授权和 pure-skip 判断真正需要的当前 skip 节点 ID。#38/#39 又把 lineage lookup 与 action 分组收敛到一次准备结果。
2. canonical action、duplicate、successor、substitution、publication、routing 的检查顺序固定，不以“更早返回”换取更短代码。
3. `SnapshotMismatchError`（状态/坐标/证据）、`GraphValuePublicationError`（duplicate/collision）和 `GraphValueUnavailableError`（历史值不可得）继续各自归属原边界。
4. 删除旧的重复 normalization/lookup 路径后，才允许增加少量窄 record；不得把所有 action 或 frame 塞进一个宽 `InvocationContext`。

最小行为测试集：多 scope canonical order、重复 action、重复 substitution、已确认 publication collision、不可用历史 input、skip 无 output、skip 有 output、interrupt ID 错误，以及每一种错误的优先级。

#38/#39 的实现只新增一个私有、不可变的 `_PlannedLineage` 索引记录；记录里仍是原有 `GraphRunState`，没有第二套运行时状态。它只保存 canonical bindings，fence/resume/continuation 校验共用同一份查找规则；恢复 action 先按 canonical 顺序逐组迭代 `groupby`，再交给现有 reducer。实现已验收，错误顺序、恢复语义和唯一 reducer 边界不变。

### 2. Routing 与 materialization：共享坐标事实，不合并解释器（#14 已完成，#15/#16 保留）

已为 node input 建立一个纯的窄坐标解析步骤（输入是 compiled binding、scope 和 anchor superstep，输出是明确的 graph-input 或 publication coordinate）。不同 owner 继续各自消费：

- materializer 查找具体 frame，缺失时包装为 `GraphValueUnavailableError`；
- routing owner 计算 `RequiredTarget` 和完整 unavailable diagnostics；
- recovery 复用同一坐标事实，但不复制 runtime materialization 流程。

不能把 `node_inputs_available` 和 `graph_outputs_available` 合成一个“万能 available”函数：前者是 pending node 的短路判断，后者是 graph output 检查，调用方和异常边界不同。`unavailable_graph_outputs` 仍需完整扫描并保留诊断顺序。

#15/#16 复核后保留：`resolve_routing_facts` 已用连续区块表达 arrival、required target 和 output diagnostics，内部 `required` 也已用局部缓存避免 direct/join 对同一 target 重复扫描。继续拆分只会增加函数跳转或新 owner；强行与 materialization 共用更宽 helper 还会混合 `InvalidRoutingCommandError` 与 `SnapshotMismatchError` 的边界。

### 3. Frame validation：先结构、后语义（#40 已完成、#41 保留）

目标总入口顺序固定为：

```text
nominal record type
  -> coordinate type and canonical order
  -> scope/descriptor/provenance membership
  -> complete-context semantic completeness (only !recovered)
```

#40 已按此顺序完成：结构与 canonicality 仍由 `_validate_frame_index` 统一检查，四类 record 的 scope/descriptor/provenance/frame 内容分别由窄 typed validator 校验；没有下沉到 `ScopedFrameIndex`，也没有引入 `object`、反射、字符串 discriminator 或通用 callback。#41 复核后改判为保留：`validate_context` 原本就将完整性检查限制在非 recovered continuation，继续保持这一条清晰路径。

### 4. Graph facade 与生命周期：保留统一 builder，简化生命周期（#19 保留、#20 已完成）

`Graph` 继续是唯一 public facade，`_GraphRun` 继续是唯一 live owner。目标只是让职责显式：

- builder 侧保留统一的 `add_node`，由现有两个 `@overload` 分别约束 callable 与 nested graph；运行时 union 分支继续为动态调用提供明确校验，不拆公共入口；
- `Graph.run` 保留统一的 overload 与 mode dispatch；fresh/continued 分支分别准备 root admission，随后共用 owner-task 等待和 `drive_project_finish` 流程；
- cleanup 仍按 abort → release，setup cancellation、node-origin cancellation、commit-origin cancellation 和 partial commit 的观察顺序不变；
- authoritative state 仍在 commit 返回 exact successor 后才写回 `_GraphRun`，不能为了简化把 candidate 先写入内存。

### 5. Frontier preparation 与 edge validation：直接表达阻断与错误顺序（#3、#34）

**#3 已完成。** `prepare_frontier` 校验并投影 child 后，遇到 missing/active child 直接返回已有的 `WaitingForChildren`；只有 child 全部终结才物化 executable。这样阻断结果由发现它的 owner 直接表达，也删除了 `FrontierPreparation` 中仅供上层转发的重复字段。

**#34 评审后保留。** `_validate_edges` 在同一次声明顺序遍历中就地表达 direct、conditional、join 的不同错误边界；endpoint 文本相似，但 join 必须保留 `InvalidJoinError`，而 direct/conditional 使用 `UnknownNodeError`。拆 helper 不会删除规则，反而分散错误顺序。

### 6. Resource validation：tuple 顺序是唯一来源（#33）

**#33 已完成并验收。** `Graph.add_node` 本来就按资源首次出现顺序追加到 `GraphDefinition.resources`；原来的 `ResourceDefinition.order` 只是再存一份同样的数字，而编译器和运行时实际使用的是 tuple 派生出的 `transition.resource_order`。现在 `ResourceDefinition` 只保留 `resource_id`，校验器也不再检查无意义的 numeric order；重复 ID、非法 identity、重复或未声明的节点资源要求仍保持原有错误边界。`ResourceSnapshot` 及其 State 校验没有移动或合并。

这是一次内部结构 breaking change：直接构造 `ResourceDefinition(resource_id, order)` 或读取 `.order` 的代码必须迁移为单参数构造，并把期望的获取顺序放在 `GraphDefinition.resources` tuple 中。旧字段、兼容 alias、wrapper 和双路径均不保留。当前仓库没有持久化 GraphDefinition 编解码格式；未来若引入外部或持久化定义，必须在其独立的 schema/version migration 中把旧 `order` 字段转换为 tuple 顺序后再加载，不能把旧字段重新带回 Kernel。

## 从 `Graph.run` 出发的完整调用链审查

这一节审查的是“读者能否沿着一次真实运行走完所有分支”，而不是只看单个函数的指标。结论先说：**调用链的状态机和提交边界总体已经足够清楚；#20 已收拢 `Graph.run` 的上游编排，剩余可研究点是 fresh/continued owner admission 和 cleanup 的重复 wiring。** 因此不能把 49 个热点或 34 个 `K` 项一概视为调用链问题。

### 0. 覆盖口径：什么叫“全部遍历”

本节采用的是生产代码可达闭包，而不是抽几个 happy path：

1. 以 `execution.facade.Graph.run` 为根，使用仓库 semantic index 展开所有能静态解析的内部调用，得到 **232 个函数/方法定义**。
2. 对 Python 静态索引无法解析的 protocol、闭包和局部 receiver 调用逐点补边，包括 `root.*`、`current.*`、`handle.*`、`session.*`、`scoped_commit.confirm`、child constructor、evidence reader/publisher、resume codec wrapper 和 nested `graph._definition`；补边后的显式内部闭包为 **296 个定义、37 个模块**。
3. 构造器、property 和 dataclass `__post_init__` 不一定表现为 call-graph 函数边，因此另行检查 `_GraphRun`、session/scheduler、coordinate、frame、result 和 transition 的隐式构造/封印校验。
4. 49 个热点中，**47 个位于显式闭包内，`_GraphRun.__init__` 是隐式构造可达；只有 `Graph.add_node` 不从 `Graph.run` 可达**，因为它是运行前 builder API。也就是说，从运行入口应审的 48 个热点全部纳入本节；#1/#2/#19 已在前面的逐项审查中评审为保留。
5. 调用链在四类真正的外部边界停止：用户 node callable、用户 commit port、用户 resume encoder/decoder，以及 Python/`asyncio` 标准库。审查覆盖内核对这些返回值、异常和取消的全部处理，但不声称审查用户实现或标准库内部。

完整覆盖层次如下：

| 层次 | 已遍历的分支 |
|---|---|
| compile/cache | cache hit/miss、nested definition、静态 validation、递归 compile、family installation |
| invocation | fresh、state-only recovered、complete/recovered continuation、resume/skip/substitution、fence、preflight 条件 |
| owner admission | fresh root/child、continued root/child、terminal child reconstruction、partial commit、handoff failure |
| scope state machine | completed、aborted、settled routing、awaiting resume、active lease error、executable、children waiting |
| callable execution | claim、resource admission、session ack、scheduler completion/error/cancel、success/failure/interrupt settlement |
| nested execution | missing/start、active/drive、completed/aborted/awaiting、boundary installation、retirement、evidence handoff |
| recovery proof | live/quiescent、routing、resource selection、nested combinations、terminal/limit、worklist 去重/预算 |
| state | start、claim、fence、settle 三变体、resume 三变体、advance、complete、abort、resource acquire/release/replay |
| exit | completed/aborted/awaiting result、continuation、node/commit/invocation cancellation、abort/release cleanup |

因此，“完整”在这里指所有内核语义分支和异常出口，不指把每个 `tuple()`、`sorted()` 或 `asyncio` 内部实现展开。

### 1. 顶层入口：#20 已把准备与公共生命周期分段

当前实际路径如下（箭头表示调用，不表示可以跨越提交边界）：

```text
Graph.run
├─ 解析 invocation mode
│  ├─ values -> 新运行输入校验 -> _compile
│  └─ state -> state-only / continuation 校验 -> _compile
├─ 新运行（fresh）
│  ├─ 生成/规范化 run id
│  ├─ admit_graph_input
│  └─ fresh_root
│     ├─ StartGraphRun commit
│     ├─ 构造唯一 _GraphRun
│     └─ 安装 graph-input frame
├─ 已有 state（continued/recovered）
│  ├─ continuation admission，或建立 recovered empty evidence
│  ├─ lineage_states -> validate_context
│  ├─ plan_fences -> plan_resumes
│  ├─ admit_state_owned_overrides
│  ├─ 条件执行 preflight_recovery
│  └─ admit_continued_root
│     ├─ 构造 root _GraphRun
│     ├─ 按 scope 应用 fence/resume
│     └─ 递归重建 child owners
└─ 公共生命周期
   ├─ setup cancellation 观察
   ├─ root.drive_quantum
   ├─ project_graph_result
   └─ finish：必要时 abort，再 release
```

#20 实施前，入口同时承担 **mode dispatch、恢复准备、owner admission、驱动、结果投影和清理**，读者必须回看 fresh/continued 分支才能判断后面的 cleanup 语义。现已让两个入口分支只生成各自的 `root_admission`，再共用一次 cancellation-safe owner-task 等待；后半段由局部 `drive_project_finish` 独占驱动、投影和 cleanup 矩阵。`Graph.run` 的指标从 28/42/3/9/444 降至 22/34/3/9/356，新增的两个局部阶段分别为 6/7/2/1/65 与 7/8/2/0/90，均未形成新热点。

入口的分支清单也可以逐项对照：

| 入口分支 | 当前行为 | 是否是偶然复杂度 |
|---|---|---|
| `values` 与 `state/continuation/resume` 同时出现 | 立即 `SnapshotMismatchError` | 否；这是 API mode 的互斥边界 |
| 缺省 `values` + `state`，但带 `run_id` | 立即拒绝 | 否；state 是已有 run，不能重新指定 identity |
| fresh | 规范化 run id、admit graph input、提交 `StartGraphRun`、构造 root | 语义必要；但可从 `run` 主体移到 admission phase |
| state-only（无 continuation） | 建立空 evidence，标记 `recovered=True`；带 output 的跨 scope substitution 先拒绝 | 语义必要；恢复证据不完整，不能伪装成 complete continuation |
| continuation | 验证 family identity/state、读取 child/frame evidence，再区分 recovered/complete | 语义必要；可把验证阶段从入口移出 |
| resume 非空 | canonical order、fence、candidate successor、override、可选 recovery proof | 规则必要；目前由多个窄 owner 分担，入口只应编排 |
| setup 完成但 caller 已取消 | 先等 owner task 收敛，再交由 cleanup 矩阵处理 | 否；不能在 commit 尚未确认时直接返回 |

公共出口的取消矩阵如下；这些分支不应为了减少 `except` 数量而合并：

| 观察到的取消/错误 | root 状态的处理 | 对外动作 |
|---|---|---|
| setup cancellation | admission task 已收敛，检查是否属于 node/commit-origin | 若是 origin，保留已确认 state；否则按 invocation cancellation abort |
| node-origin cancellation | session 已按 node 语义 fence；nested node 可能已转为 child abort | `finish(None)`，保留状态，再重新抛出原取消 |
| commit-origin cancellation | commit task 的确认/失败边界已由 owner 记录 | `finish(None)`，不额外 abort，重新抛出原取消 |
| 普通 invocation cancellation | root 尚未有可归因的内部取消 | `abort`（若仍 running）后 `release`，再抛出；cleanup 错误作为 cause |
| 其他异常 | 下层已负责 fence/transition 的领域清理 | 只 `release`，保留首个异常 |
| 正常 boundary | state/evidence 已冻结并投影 | `release`，返回 result |

这张矩阵说明：`Graph.run` 不是单纯的“调用一下 runner”。取消分支本身有清晰的 ownership 语义；#20 将矩阵原样收进一个可读的局部 lifecycle 阶段，没有删除或合并这些分支。

实施后的边界是：

```text
Graph.run
  -> mode/compile/admission preparation（fresh/continued 语义仍显式分开）
  -> root_admission（共用 cancellation-safe owner-task 等待）
  -> drive_project_finish (唯一驱动、投影和生命周期出口)
```

实现没有新增 `InvocationContext` 或 typed wrapper，而是直接复用 fresh/continued 现有返回类型；“新建 state”和“从已有 state 恢复”的不同语义仍留在相邻分支中。setup cancellation 仍在 admission 完成后、drive 开始前单独观察，没有降级为普通 invocation cancellation。

### 2. 编译链：全部可达，但 `_compile_graph` 仍是最高优先级 B

`Graph.run` 在 fresh 和 continued 两种模式下都会先取得 compiled owner；cache miss 的完整路径是：

```text
Graph._compile
  ├─ cache hit -> 直接返回同一 _CompiledOwner
  └─ cache miss
     -> Graph._definition（递归 materialize nested builder family）
        ├─ 已 materialize definition -> 复用
        ├─ visiting -> 拒绝递归 composition
        ├─ outputs 缺失 -> 拒绝
        └─ callable/nested node -> immutable GraphDefinition
     -> 对 family definitions 调用 compile_graph
        -> validate_graph
           ├─ identity/version/node uniqueness
           ├─ resource catalog/requirements
           ├─ direct/conditional/join/START edge
           ├─ resume codec
           └─ nested identity collision/recursion
        -> _compile_graph（递归 scoped child）
           ├─ graph-input 与 node-output declarations
           ├─ input source resolve / exact nominal types / data cycle
           ├─ direct/conditional/join activation gates
           ├─ explicit/automatic entries 与 reachability
           ├─ joint activation path proof
           ├─ guarantees / terminal gates / activation levels
           ├─ materialization/publication/graph-output descriptors
           └─ resource-order canonicalization / immutable plan
     -> 所有 owner 编译成功后统一安装 family identity + CompiledGraph
```

validation 的错误边界清楚，family installation 也保持“全部成功才冻结 owner”的原子语义。这里没有第二 compiler，也没有 runtime 重新解释 topology。

真正的问题仍是 #32：`_compile_graph` 把 scope-independent topology proof 与 scope-dependent port/descriptor materialization 交叉在一个 1,238-node 函数中。完整调用链还暴露出同一个 nested definition 会以“可独立运行的 root scope”和“父图中的 nested scope”分别编译；scope-dependent descriptor 不能直接共用，但 identity、edge、reachability 和 activation proof 不应重复成为两份事实。B 级目标应进一步明确为：

```text
validated definition
  -> scope-neutral topology/proof facts（每个 definition 一份）
  -> scoped port/descriptor materialization（每个安装 scope 一份）
  -> immutable CompiledGraph
```

只有原型证明这个结构能删除交叉循环、且不会新增宽 `CompilationContext` 或缓存一致性协议，才将 #32 升为实施项。当前不能因它数值最高就机械拆 helper。

### 3. continued/recovered admission：顺序必要，lookup/normalization 可收敛

已有 state 的完整 admission 顺序是：

```text
state-only
  -> empty child/frame evidence + recovered=True
  -> 先拒绝无法保留 partial checkpoint 的跨-scope output substitution
或 continuation
  -> exact continuation nominal type/seal
  -> family identity + root state identity
  -> complete/recovered snapshot variant
共同路径
  -> lineage_states（scope/parent/canonicality）
  -> validate_context
     -> 四类 frame nominal/canonical/descriptor/provenance validation
     -> complete continuation 才检查 input/publication/output/child-boundary completeness
  -> plan_fences（先模拟清除遗留 execution lease）
  -> plan_resumes
     -> action canonical/duplicate/scope resolution
     -> failed/interrupted/skip exact successor
     -> resume input / substitution evidence
     -> cross-scope collision 与 routing availability
  -> admit_state_owned_overrides
  -> recovered 或 skip-without-output 时 preflight_recovery
  -> admit_continued_root（按 scope 真正 commit fence/resume）
```

`plan_fences` 必须先于 `plan_resumes`，frame structural proof 必须先于 completeness，candidate successor 必须由同一 reducer 重算，preflight 必须先于真实 admission。这些都是必要顺序。#11 已删除手工 frontier 模拟，#12 已删除 skip action 的重复证明；#38/#39 已让 canonical lineage lookup 与 scope action grouping 各做一次，同时保留原有顺序。#40 已完成，#41 复核后确认现有 recovered/complete 分层已足够清楚。不能把两种 continuation 合成一个“缺 frame 就忽略”的路径。

### 4. `prepare_superstep`：分支树清楚，复杂度是状态机本身

每一轮 `_GraphRun.drive_quantum` 先调用 `GraphExecutor.prepare`，再进入 `prepare_superstep`：

```text
prepare_superstep
  -> scoped snapshot guard
  -> state.status == COMPLETED       -> CompletedGraph
  -> state.status == ABORTED         -> AbortedGraph
  -> frontier == SETTLED             -> resolve_routing -> ReadyToResolve
  -> frontier == AWAITING_RESUME     -> AwaitingResume(failed, interrupted)
  -> execution != None               -> ResultCollectionError
  -> EXECUTABLE frontier
       -> prepare_frontier
          ├─ missing child / active child -> WaitingForChildren
          └─ 无 child 阻断
             -> claim_resource_snapshot
             -> prepare_claim
             -> ExecutableFrontier
```

这棵树的每个叶子都是一个持久状态或一个明确的领域错误，顺序也体现了 terminal、routing、resume、lease 的优先级。它目前**足够简单，应保留**。重复的 snapshot guard/`plan_tasks` 检查是分层 fail-closed 边界：入口、executor 和 reducer 各自验证自己接收的证据，不能仅因文本相似就删除。

`prepare_frontier` 的 #3 已完成：它先验证并投影 nested child，遇到 `missing/active` 时直接返回 `WaitingForChildren`，不再物化 callable input；上述状态机分支和行为语义没有改变。

### 5. `drive_quantum`：唯一 scope runner，循环形状已经是最小可读状态机

```text
_GraphRun.drive_quantum
  while
    ├─ ReadyToResolve       -> commit routing command -> 下一轮
    ├─ ExecutableFrontier   -> _execute_frontier -> 下一轮
    ├─ WaitingForChildren
    │  ├─ missing           -> 逐个 _start_child -> 下一轮
    │  ├─ active            -> 逐个 _drive_child -> 下一轮
    │  └─ 没有可推进 child   -> AwaitingResume((), ())
    └─ Completed/Aborted/AwaitingResume -> 返回 boundary
```

这是整个 execution engine 唯一的 live scope state machine。把每个叶子拆成一个 dispatcher、再造一个 recovery/runtime runner，都会让状态转移的 owner 分裂，反而不易维护。唯一值得记录的可读性风险是 `AwaitingResume((), ())` 同时表示“所有 child 暂时等待恢复”的内部信号和对外 boundary 类型；若未来要改，应先引入一个明确的内部 `ChildrenQuiescent` 变体并证明不会扩大 boundary union，当前列为 `B`，不是本轮的 `A`。

### 6. callable、scheduler 与 resource：协议分支完整且 owner 清楚

```text
_execute_frontier
  -> _transition(ClaimGraphExecution)
     -> commit_transition
        -> reduce_graph_run
        -> commit port
        -> 确认 exact reducer successor
  -> GraphExecutor.issue_session
     -> scoped snapshot guard
     -> claim.consume（唯一 claim owner）
     -> issue_execution_session
  -> _consume_session
     -> session.next（ack 上一条 command，再交付一个 completion）
     -> settle_result
     -> _transition(SettleGraphNode)
     -> 确认 state 后才安装 publication frame
     -> nested node 则 _retire_child
```

这里的复杂度来自一个不可省略的原子顺序：**claim 先确认、session 才能发放；settlement 先持久化确认、publication 才能进入内存 frame index。** `GraphExecutor` 是薄边界，但它持有 claim/session 的唯一 owner；`settle_result -> commit_transition -> reduce_graph_run` 是唯一权威提交链。这条链目前足够简单，任何“提前写 frame”“让 session 自己改 state”或“把 settlement 与 publication 合成一个快捷路径”都会制造技术债。

`session.next` 内部的 ack、scheduler drain、queued completion、node-origin cancellation 和 close 顺序同样是协议，不是重复分支。#17 保留是调用链审查后的结论，而不只是指标判断。

继续展开 session/scheduler 的所有叶子：

```text
session.next
  -> acknowledge previous settlement（必须是 exact reducer successor）
  -> drain scheduler events
     ├─ canonical first error / node-origin cancellation
     └─ 已完成结果按 task sort key 入队
  -> queued completion -> 一次只 project 一个 SettleGraphNode
  -> select_executable_tasks
     ├─ parallel slot 已满 -> 等待 live task
     ├─ started node -> 跳过
     ├─ resource 未 admitted -> 跳过
     └─ submit ordinary callable task
  -> scheduler
     ├─ user callable 返回 values/success/failure/interrupt -> strict outcome projection
     ├─ user callable 抛普通异常 -> TaskRaised，先 drain 已在途任务
     ├─ user callable 自发取消 -> node-origin cancellation
     └─ scheduler close cancellation -> 只用于收敛 live handles
  -> 无 live task
     ├─ 有 canonical error -> 抛该错误
     ├─ 无 pending node -> StopAsyncIteration
     └─ 仍 pending -> ResultCollectionError（planner/session 不一致）
```

resource 路径同样只有一份规则：`admit_tasks` 按 canonical task 顺序调用 `reduce_resources(AcquireResources)`，runtime/recovery 共同消费 `select_executable_tasks`；settlement 再由 State reducer 原子 `ReleaseResources` 并按 FIFO 推进 waiter。重复的 snapshot validate/replay 是 durable input、claim admission 和 state transition 各自的 fail-closed 边界，不是三套资源算法。#1/#2 已复核保留；#46 仍只能在不离开 State owner 的前提下研究阶段化或复用已验证 view，#47 的 FIFO/prefix 算法保留。

外部 callable、commit 和 codec 是端口边界。内核已对它们分别执行 strict outcome admission、exact successor confirmation、bytes/`Graph.Values` nominal validation；进一步“遍历”端口实现会越过本项目 ownership scope。

### 7. routing 与 reducer：两个提交屏障、一份 state truth

```text
最后一个 node settlement
  -> SettleGraphNode commit（frontier 变为 SETTLED）
  -> 下一轮 prepare_superstep
     -> resolve_routing
        -> resolve_routing_facts
           ├─ 校验/累计 join arrivals
           ├─ 计算 direct 与 completed-join targets
           ├─ 缓存 RequiredTarget
           └─ 计算 graph-output diagnostics
        -> project_routing_facts
           ├─ unavailable controlled input -> AbortGraphRun
           ├─ 有 control target -> AdvanceGraphFrontier
           ├─ 残留 join 无后继 -> RoutingDeadlockError
           ├─ unavailable graph output -> AbortGraphRun
           └─ 否则 -> CompleteGraphFrontier
  -> routing command commit
  -> drive_quantum 下一轮
```

settlement 和 routing 分成两个 command/commit 是恢复屏障：前者记录事实，后者依据已提交事实作决定。不能为了少一个函数或少一次循环，把 routing decision 塞进 `settle_graph_node`。#15/#16 已经在 routing owner 内按 accumulation、required-target 和 diagnostics 三个连续区块表达；继续拆 helper 不会净删规则，因此评审后保留。

所有 durable command 最终只进入一次 `reduce_graph_run` dispatch：

| command | reducer 叶子 | 必须保留的原子事实 |
|---|---|---|
| `StartGraphRun` | `start_graph_run` | identity、initial frontier、parent、resume codec |
| `ClaimGraphExecution` | `claim_graph_execution` | lease generation 与 resource snapshot 同时建立 |
| `FenceGraphExecution` | `fence_graph_execution` | exact token，lease/resource 同时清除 |
| `SettleGraphNode` | success/failure/interrupt | settlement、resource release、lease 清除原子完成 |
| `ResumeGraphNodes` | retry/override/skip/interrupt | settlement variant 和 interrupt/codec 约束原子更新 |
| `AdvanceGraphFrontier` | `advance_graph_frontier` | superstep、canonical nodes、join progress 同时推进 |
| `CompleteGraphFrontier` | `complete_graph_frontier` | 清空 frontier，拒绝遗留 join |
| `AbortGraphRun` | `abort_graph_run` | 只允许 quiescent running state，保留诊断 frontier |

dispatch 前统一验证旧 state 和 revision，叶子构造新 state，最终只在 reducer 中 `revision + 1`。把分支拆成多个 public reducer 或让 execution 直接写 `GraphRunState` 都会破坏唯一真相；#43–#49 的多数 K 结论经完整链路复核后不变。

### 8. child 调用链：fresh 与 continued 的语义不同，但 wiring 有重复

新 child 的路径：

```text
_GraphRun._start_child
  -> 校验 MissingChild 的当前 run/superstep
  -> materialize_node_input
  -> admit_child_graph_input
  -> child_position
  -> _make_child_constructor
     -> StartGraphRun commit（child scope）
     -> 构造 child _GraphRun
     -> install graph-input frame
     -> 返回 opaque child handle
  -> parent.accept_child_call(ActiveChild, handle)
```

continued child 的路径：

```text
admit_continued_root.admit_children
  -> 只取当前 activation，并按 node id 排序
  ├─ terminal child
  │  -> 从 boundary frame 重建 CompletedChild，或读取 AbortedChild
  │  -> parent.accept_child_call(..., handle=None)
  └─ running child
     -> _frames_for_owner
     -> 构造已有 state 的 child _GraphRun
     -> apply_admission(fence/resume)
     -> 递归 admit_children
     -> 返回 opaque handle 并 handoff
```

两条路径都在做 owner wiring、scoped commit、evidence publisher、child handle 和失败清理，所以有真实的链级重复；但 fresh 是“先 StartGraphRun 再创建 owner”，continued 是“从已确认 state 重建并可能产生 partial commit”。把它们强行塞进一个宽构造函数会丢掉 `confirmed_prefix`、`transition_attempted`、`failed_scope` 的 partial-commit 契约。正确的 B 方向是共享一个窄的 owner-construction primitive 或 typed admission plan，并让 fresh/continued 各自保留 admission 语义；不是复制第二个 runner，也不是兼容 wrapper。

child drive/handoff 本身目前足够清楚：

```text
parent._drive_child
  -> opaque_handle.drive
     -> child.drive_quantum（递归）
     ├─ AwaitingResume -> handoff evidence 一次 -> parent 标记等待
     └─ terminal
        -> terminal_projection
        -> terminal_boundary（completed 才允许 output）
        -> handoff evidence 一次
  -> parent 校验 disposition 与 terminal projection 类型一致
  -> 安装 child boundary frame
```

terminal 类型检查、一次性 handoff 和 completed/aborted 的边界都是不变量，不应以宽 union、字符串 tag 或通用 callback 换取更短代码。

### 9. recovery 调用链：证明器，不是第二执行路径

```text
Graph.run(existing state)
  -> plan_fences
  -> plan_resumes
  -> admit_state_owned_overrides
  -> 条件 preflight_recovery
     -> 校验 seed / binding / action evidence
     -> _prove_scope
        -> bounded worklist + seen transfer state
        -> quiescent routing resolution
        -> executable frontier expansion
        -> live execution expansion
        -> nested outcome plans（递归 child proof）
        -> terminal boundaries / successor states
  -> proof 成功后才 admit_continued_root 并真实 drive
```

recovery 只做无副作用 reachability proof，复用 `plan_tasks`、`resolve_routing_facts`、settlement projection 和 reducer；它没有另一套状态写入或 session。worklist、fixed point、递归 child outcome 和错误优先级是算法必要复杂度，当前**足够简单且应保留**。把 recovery 与 runtime 粗暴合并，会同时破坏“先证明、后提交”和唯一 execution engine 两条架构约束。

逐叶检查还包括：已有/新 child state、completed output history、aborted child、awaiting-resume、execution-limit、nested completed/aborted variations、callable success route 分叉、resource-selected live set、routing abort 中“真实业务不可达”与“历史 frame 缺失”的区别，以及 4,096 transfer-state budget。没有发现可以用一个线性流程等价替换这些状态空间分支的设计。

### 10. boundary、结果和 cleanup：出口清楚，准备阶段的 cleanup 仍重复

```text
drive boundary
  -> project_graph_result
     -> freeze_root_evidence（拒绝 active child 未 handoff）
     -> 构造 continuation
     ├─ CompletedGraph -> project_graph_outputs -> public values
     ├─ AbortedGraph  -> 要求 canonical abort -> abort view
     └─ AwaitingResume -> 汇总 root/child failure 与 interrupt views
  -> finish
     ├─ 有 abort reason -> root.abort
     └─ 总是 root.release
```

这个出口顺序是可读的：结果只从冻结后的 evidence 投影，`finish_root` 先 abort 后 release，并保留首个 cleanup 错误。#20 已把 facade 的公共出口收拢，但构造失败 cleanup 仍分别存在于 `fresh_root`、`admit_continued_root` 和 child constructor；它们的政策并不完全相同，因此不能抽成 `cleanup(ignore_errors=True)`。可研究一个显式 typed cleanup policy（是否已尝试 transition、是否允许 abort、是否必须保留 partial commit），列为 B。

### 11. 调用链级结论矩阵

| 调用链 | 当前是否足够简单 | 结论 | 对应项目 |
|---|---|---|---|
| compile/cache/family install | 部分否 | validation/install 清楚；scope-neutral proof 与 scoped materialization 值得原型 | #30/#32 B |
| `Graph.run` mode + preparation + lifecycle | 是 | 已分为 admission preparation 与统一 drive/project/finish；保留一个 `Graph` facade | #20 已完成 |
| continuation/frame/resume admission | 是 | 顺序保留；共享不可变 canonical lineage index，并按需形成 scope action groups，frame 先结构后语义 | #11/#12/#38/#39/#40 已完成；#41 K |
| `prepare_superstep` 状态分支 | 是 | 状态叶子和错误顺序清晰，不再泛化 dispatcher | #4、#8、#17、#25 等 K |
| `drive_quantum` 唯一 runner | 是（sentinel 语义可再评估） | 保留循环；仅研究 `ChildrenQuiescent` 内部变体 | #25 K，B 候选 |
| claim/session/scheduler/resource | 是 | ack/error-drain/FIFO 是必要协议，资源 claim 与 selector 的阶段边界也不合并 owner | #1/#2/#17/#24/#47 K |
| settlement/reducer/state validation | 是 | 原子提交和 snapshot guard 是必要边界 | #43–#49 多数 K |
| settlement -> routing -> next frontier | 是 | 两个 commit 屏障不可合并；routing owner 内三个连续阶段已足够清楚 | #15/#16 K |
| fresh/continued root/child admission | 是 | continued owner 构造、admission、handoff 和 cleanup 已收敛，fresh/continued 语义仍分开 | #27/#28 已完成 |
| child drive/terminal handoff | 是 | 类型检查和一次性 evidence 是必要不变量 | #23 K |
| recovery proof | 是 | bounded worklist/fixed point 是算法本体，不建第二 runner | #4–#10 多数 K/B |
| result projection + final cleanup | 是；owner 构造失败 cleanup 仍可研究 | facade 结果出口已收拢，owner admission policy 继续独立评审 | #20 已完成，#26 K，#27 B |

所以，对“整个调用链是否足够简单”的直接回答是：**主状态推进链、统一入口和 continued owner admission 现在都足够清楚。** 剩余 B 项是 compiler 与 validation 的独立设计课题；后续只在能净删 wiring 时继续，不为让每个函数都低于阈值而拆散状态机、reducer 或 recovery 证明器。

## 条件可化简项目：先做设计，不把希望写进生产代码

`B` 项的共同风险是“看起来拆开了，实际增加了 owner/record/调用链”。优先研究顺序如下：

| 组 | 项目 | 需要证明的净收益 |
|---|---|---|
| snapshot/definition/resource validation | #35、#46 | 结构检查、compiled compatibility、durable replay 各有唯一 owner，异常边界不交叉。 |
| compiler proof | #30、#32 | typed phase result 比当前交叉循环更短、更易读，并且 ratchet 的总定义/节点数下降。 |

特别是 #32（`_compile_graph`）虽然数值最高，但不能据此直接拆函数。先画出 phase 输入/输出和所有事实 owner，再用一个小图覆盖 direct、conditional、join、data dependency、nested graph、graph output 五类组合；没有这个证明时，保留现状比引入 `CompilationContext` 更简单。

## 复用与 clone 审查

扫描器报告了 15 个 logical clone pairs、8 个 statement clone pairs、10 个 near-clone pairs 和 20 个 record-shape pairs。逐项判断如下：

- `node_inputs_available` 与 `graph_outputs_available`：不合并。一个是 pending input 的短路可用性，一个是 completion output 的检查；调用方和错误边界不同。只复用窄 coordinate construction。
- `normalize_input_bindings`、`normalize_output_declarations`、`normalize_graph_output_declarations`：循环形状相似，但输入 nominal 类型和错误信息不同；提取一个泛化 mapping helper 的收益不足以抵消抽象。
- 四个 frame admission wrapper：已经共同调用 `_admit_entries`。外层保留四种领域错误文本是有意义的，不再加兼容层。
- `ScopedFrameIndex.add_graph_input/add_publication/add_resume_input/add_child_boundary`：机械形状相似，但 record 类型、坐标和 publication 错误边界不同；不做宽泛 generic `add`。
- 形状相同的 dataclass（例如 execution coordinate、frame record、nested definition）：形状相同不代表事实相同；除非能证明同一 owner，否则不能合并。
- `Graph` 的低 cohesion 命中（15 methods、32 fields、5 components）：它是唯一 public facade 的代价。拆成多个 facade、mixin 或第二 runner 会违反架构目标；应通过内部阶段化降低方法内分支，而不是分裂入口。

legacy 测试只迁移其有价值的行为语义（尤其是错误顺序、恢复边界和 cancellation 时序）；不为旧调用方式保留生产 alias、wrapper 或双路径。

## 推荐实施顺序与门禁账本

1. 先为 A 组补/确认状态转换、恢复边界、frame provenance、异常优先级和 cancellation 的确定性测试。
2. resume admission 与 routing/materialization（#11–#16、#38–#40）已完成治理；#11/#12/#14/#38/#39/#40 已验收，#15/#16/#41 保留。
3. `Graph.run` 生命周期（#20）已完成；builder API（#19）保留现状。
4. #3 frontier preparation 已完成；#34 edge validation 评审后保留现状。
5. #33 resource validation 已完成并验收；剩余 B 组特别是 compiler/recovery，任何没有净删除的拆分都退回设计阶段。
6. 每个可合并提交运行 `make complexity-report`、`make complexity-ratchet`、`git diff --check`；实际下降后立即降低 `pyproject.toml` ratchet 上限。

成功标准不是单纯把 49 改成更小的数字，而是同时满足：唯一事实 owner、无重复执行路径、异常边界稳定、状态先持久化确认再更新内存、生产代码没有 legacy 兼容债务，并且代码阅读者能从函数结构直接看出这些不变量。
