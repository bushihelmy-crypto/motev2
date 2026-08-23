# S01 `_compile_graph()` 阶段边界收敛实施方案审查

> **结论：暂不批准并要求重新设计 target。当前不实现持久化、State/Store 不变、无第二 execution owner 等范围边界已经闭合；但本方案仍存在第二份 target-shape 事实源、不可同时满足的复杂度门禁、`FrozenMap` 工作索引导致的查询复杂度退化，以及未闭合的 `GSP-A06` evidence。整改不得以 diff 最小、保留现稿结构或勉强通过门禁为目标；必须以零新增负债、唯一真相、正确复用基础设计和最清晰的最终逻辑为唯一取舍依据。同时禁止新增或扩写冻结 private symbol、局部变量、helper 数量、扫描表达式或源码布局的 legacy/AST 门禁测试。完成 R1–R6 前，不得更新 requirements 中 S01 的批准状态，也不得修改 production。**

## 1. 审查信息

- 审查日期：2026-08-23
- 审查对象：[S01 实施方案](graph-semantics-preserving-simplification-s01-implementation.zh-CN.md)，387 行
- 对象 SHA256：`1de85cc8b4a8c92cfc9480ab9d81059d74f5400fde22280d1638a6e2b9e45b4b`
- 交叉依据：[requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)、[主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)、当前 `compiler.py`、complexity gate 与相关 compiler characterization
- 审查目标：当前不实现持久化；本单元零新增负债；target、owner 与 compiled lowering 保持唯一事实源；正确复用现有基础类型和 owner；以最终结构质量而不是最小改动面为准，使逻辑、错误顺序与验证边界简单清晰
- 本文性质：review record，只记录裁决、异议、证据和整改条件；不拥有 S01 target shape，不记录或替代 requirements owner 的批准状态

## 2. 总体裁决

| 维度 | 裁决 | 说明 |
| --- | --- | --- |
| 当前不实现持久化 | **通过** | implementation manifest 仅允许修改 `compiler.py`，明确排除 State、Store、protocol 与 persistence backend |
| Graph/execution 唯一 owner | **设计方向通过，phase 边界未闭合** | 不增加 runner 或 compiled owner；但 nested helper 实际接管子图递归与局部首错顺序 |
| compiled lowering 唯一真相 | **方向通过** | 删除 `control_gates` 与 `direct_pairs` 的方向成立，`FrontierTransitionPlan` 字段语义保持 |
| 文档唯一真相 | **不通过** | 新 S01 文件与主实施方案同时声明 target-shape owner |
| 零新增复杂度负债 | **不通过** | 预期新增 5 个 top-level definitions，却同时禁止 global metric 增长和 ratchet 回写 |
| 复用基础设计 | **部分通过** | 复用既有 nominal/generic 类型正确；提前把工作索引变成线性查询的 `FrozenMap` 不正确 |
| 行为与 exact evidence | **不通过** | 缺少适用 `GSP-Pxx` 映射、case-level nodeid、关键 route-insensitive predicate characterization 和可复现 exact-shape evidence |
| legacy 门禁负债 | **必须为零** | 不新增或扩写 private-shape/source-layout/AST gate；持久测试只覆盖可观察行为和既有公共 owner 契约 |
| 交付门禁 | **不通过** | 缺少 monorepo-root pre-commit；当前 complexity ratchet 本身也未闭合 |

因此，本轮裁决是：**方案有成立的删除方向，但当前 target 不满足 `GSP-A06`，不得局部修补后批准；必须按本文目标状态重新设计后再审。**

## 3. 阻断问题

### R1：产生第二份 target-shape 事实源

S01 文档第 1 节声明“本文定义可执行的 target shape”，第 4–11 节继续拥有 exact helper、删除面、复杂度账本、characterization、manifest 和交付定义。

但现有 owner 关系已经明确：

1. requirements 的“对应实施方案”只链接
   `graph-semantics-preserving-simplification-implementation.zh-CN.md`；
2. requirements 第 2 节规定 S01–S23 target shape、实施顺序、复杂度账本和门禁唯一归该对应实施方案；
3. 主实施方案第 8 节明确写明“本文是 target shape ... 的唯一 owner，不创建第二份 target-shape proposal”；
4. 主实施方案已经在第 3.1 节拥有 S01 的 target ledger 条目。

因此，新文件不是被 owner 链接的下位规范，而是与现有 owner 并行的第二份 target。它也没有在 design manifest 中同步主实施方案的 owner 指针，不能依靠后续 requirements approval 自动消除冲突。

**目标整改：**

- 把重新设计后的 S01 单项 target、复杂度账本、evidence、manifest 和门禁完整并入主实施方案，形成类似 S07 的 `GSP-A06` 专节；
- 删除独立 S01 target-shape proposal，不保留“主文概述 + 子文细节”两层可漂移规范；
- requirements 继续只拥有批准状态并只链接主实施方案；README 继续只导航唯一 owner；
- 本 review record 作为历史审查证据保留，但不得被引用为 target shape。

R1 闭合前，本文件只能视为待审 proposal，不能成为实施依据。

### R2：复杂度目标与门禁互相矛盾

方案第 8 节记录：

```text
top-level definitions: 504 -> 509
new private functions:  0 -> 5
```

同一节和第 11 节又要求：

- global complexity gate 不得增长；
- 新增 thin helper/clone 或任何全局指标增长立即停止；
- implementation 不修改 `pyproject.toml`；
- implementation change unit 只包含 `compiler.py`。

当前 complexity ratchet 不是“只拒绝增长”的宽松门禁。它同时拒绝：

```text
actual > configured  # regression
actual < configured  # improvement 未回写
```

因此：

- 若先把 ratchet 同步为当前实际值 504，S01 的 509 会作为增长失败；
- 若保留当前配置 511，S01 的 509 会作为未回写 improvement 失败；
- 只有把配置改为 509 才可能通过，但 manifest 明确禁止修改 `pyproject.toml`，且这仍违反“global metric 不增长”的停止条件。

这是不可通过实施细节修复的合同矛盾。

**目标整改：**

1. 先由独立 complexity owner unit 把当前实际 snapshot 向下锁回配置；S01 不继承宽松的 511 baseline；
2. S01 candidate 的每一项全局 ratchet metric 都必须小于或等于已锁定 baseline，不允许上调任一 limit 为新增定义或 indirection 放行；
3. 不预设 helper 数量。只有确实形成独立不变量、删除跨阶段事实或被多个 production consumer 复用的函数才保留；单纯搬移一段只调用一次的代码不是 phase abstraction；
4. 删除 `control_gates`、`direct_pairs`、无语义 alias、重复 freeze/sort 和重复 predicate 后，必须同时证明 `_compile_graph()` 局部认知面下降以及全局 definition/decision/clone/thin-helper 指标不增长；
5. 对 complexity health candidate 做 identity-level diff，不能只比较总数；S01 不得以删除一个旧 smell 换入一个新 smell后宣称零负债；
6. 如果清晰的 phase split 无法在上述条件下成立，应保留一个按注释和依赖顺序清楚分段的 orchestration owner，而不是为了“看起来拆过”引入 helper；
7. 最终账本记录 actual candidate 数字，不保留事先指定的 `504 -> 509` 或“必须五个函数”结论。

### R3：`FrozenMap` 被误用为编译期工作索引

方案要求以下 helper 提前返回 `FrozenMap`：

```python
_compile_nested_graphs(...) -> FrozenMap[GraphNodeId, CompiledGraph[GraphValueT]]
_collect_node_outputs(...) -> FrozenMap[GraphNodeId, OutputDeclarations[GraphValueT]]
```

并为此把 `_resolve_source().node_outputs` 从 `dict[...]` 改为 `Mapping[...]`。

当前 `FrozenMap` 的基础实现是 tuple-backed immutable representation：

```python
def __getitem__(self, key: KeyT) -> ValueT_co:
    for candidate, value in self.entries:
        if candidate == key:
            return value
    raise KeyError(key)
```

它适合最终 compiled plan 的确定性 immutable shape，不是高频工作索引。方案会造成：

- `_collect_node_outputs()` 对每个 nested node 查询一次 `nested_graphs[node_id]`，最坏 O(n²)；
- binding scan 中每个 `NodeOutputRef` 都通过 `Mapping.get()` 线性查询 `node_outputs`；
- graph-output scan 再次线性查询 `node_outputs`；
- 在已经建立 `node_ids = tuple(sorted(nodes))` 后，`frozen_map(node_outputs)` 还会执行一次无外部消费者需要的排序。

当前 typed `dict` 工作索引提供近似 O(1) 查询，并只在构造 `CompiledGraph`/`FrontierTransitionPlan` 时冻结。这个模式才是当前基础设计。

**目标整改：**

- `nested_graphs`、`node_outputs` 和 proof indexes 在编译过程中继续使用完整泛型标注的 typed `dict`；
- `FrozenMap` 只出现在最终 compiled representation 边界；
- `_resolve_source()` 保持只依赖高效、完整泛型的 node-output lookup；是否标注为 `dict` 或只读 `Mapping` 由 capability boundary 决定，但实际 owner 必须是单一 `dict` 工作索引，不得因抽象标注掩盖线性实现；
- 不把“提前 immutable”或“少写一次 `frozen_map(...)`”计为简化收益。

### R4：`GSP-A06` evidence 未闭合

Requirements 要求 P2 单项在批准前提交：

- 全部适用 `GSP-P01`–`GSP-P08` 的映射；
- 成功与失败/边界的 exact `path::test_case`；
- exact-shape/tamper 的目标、断言和失败条件；
- 净复杂度证据和 exact changed-file manifest。

当前 S01 文档存在以下缺口：

1. 没有列出适用的 `GSP-Pxx`，也没有解释排除项；
2. 第 9 节除 resource case 外，大部分证据只到文件级，不能复现为单个 nodeid；
3. “源码审查、strict Pyright、source-discipline 和既有 architecture gate 覆盖 exact shape”的结论不成立：
   - strict Pyright 检查类型关系，不证明旧局部索引已经删除；
   - source-discipline 只检查连续 module imports、`Any`、反射和动态导入；
   - graph-execution-ownership gate 检查 `FrontierTransitionPlan`/`CompiledGraph` fields，不检查五个 private helper 的签名、数量或调用结构；
   - complexity gate 只锁定聚合指标，不能证明 `control_gates`、`direct_pairs`、DTO/context bag 或重复 freeze 的具体归零；
4. 文档一面要求 exact helper/signature 和旧索引归零，一面禁止任何同步 test/source gate，最终只能靠不可复现的人工作者判断。

这里的整改不能转向新增 legacy gate。S01 不改变 `FrontierTransitionPlan` 或 `CompiledGraph` 的公共/owner-internal字段
shape，因此没有理由新增 architecture case 去冻结 compiler private helper、局部变量、调用次数、具体 loop/comprehension
或 source text。`GSP-A06` 所需 exact evidence 应由“既有 owner gate 保持不变 + 可复现 actual diff/source review +
可观察行为 characterization”共同完成，而不是把本次实现形状永久写进测试。

S01 至少应映射并解释：

| requirement | 适用理由 |
| --- | --- |
| `GSP-P01` | compiler validation error 分类和首错时点可通过 public `Graph` surface 暴露 |
| `GSP-P04` | materialization/publication descriptor assembly 被重排 |
| `GSP-P05` | direct、conditional、join、data/control eligibility 和 publication selection proof 被重排 |
| `GSP-P06` | runtime 与 recovery 必须继续消费同一 compiled transition truth |
| `GSP-P07` | nested recursion、resource first-seen order、canonical node/order 被触及 |
| `GSP-P08` | execution owner、nominal generic、module-scope import 和无第二 lowering owner 是核心边界 |

`GSP-P02/P03` 可因 State/command 与 commit/install transaction 均不修改而明确排除，但必须写出排除理由。

**目标整改：**

- 把行为矩阵全部写成 exact nodeid，并分别说明 assertion target 与 failure condition；
- 对 private/local deletion 在 owner writeback 中记录可复现的一次性 source-review 命令、actual diff 和 AST 计数输出；这些命令是交付证据，不新增、生成或扩写 repository legacy/AST test；
- 对所有受影响的可观察 compiler 行为补齐 deterministic public/owner characterization，不以“现有测试大致覆盖”代替目标分支证据；
- public topology shape 未变化，既有 graph-execution-ownership gate 原样复跑，不增加 S01-specific private/source assertion；
- implementation manifest 必须如实包含为可观察行为实际修改的现有 behavior test 文件，但不得包含新增或扩写的 legacy/AST/private-shape gate。

### R5：route-insensitive single-source 语义缺少直接 characterization

`_all_single_source_gates()` 是本方案唯一新增的共享语义 predicate。它必须证明：

1. 空 gates 返回 `False`；
2. 同一 source 通过多个 conditional route 指向同一 target 时仍返回 `True`；
3. direct 与 conditional gate 都来自同一 source 时仍返回 `True`；
4. 任一 join gate 包含第二 source 时返回 `False`；
5. route identity 不进入 producer guarantee 或 relative publication selection；
6. route-aware joint-activation proof 仍读取完整 `ActivationGate`。

现有测试分别覆盖：

- 多个 conditional route 可共享 target；
- loop producer 的 relative publication selection；
- join consumer 的 ambiguous publication 拒绝。

但没有一个 case 把“同一 loop source、多个 route、controlled target 的 data binding、relative publication selection”组合起来。该组合正是从 `control_gates` 切换到 `activation_gates` 后最容易回归的可观察行为。

**目标整改：**形成完整的 source-only gate 行为矩阵。优先复用已经精确命中的现有 public cases；缺失场景在现有 `tests/execution/graph/test_compiler_contract.py` 中补齐，不测试 private helper 名称。矩阵至少包含：

| 场景 | 期望 |
| --- | --- |
| 无 control gate、唯一 data producer | data-only activation 与既有 relative selection 保持 |
| 同一 source 的两个 conditional routes 指向同一 target | route 被 source-only proof 忽略，编译成功 |
| 同一 source 同时通过 direct 和 conditional gate 指向 target | 仍是唯一 causal source |
| join gate 含两个不同 source | 不得判为 single-source causal |
| route mutually exclusive / partial join | route-aware joint-activation proof 继续 fail closed |
| terminal join/output | terminal source guarantee 与 publication selection 不变 |

其中必须新增或明确补强的核心 positive case是：

```text
loop source
  + two conditional routes from the same source to one target
  + target input bound to that source output
  -> compilation succeeds
  -> target materialization uses RELATIVE, superstep=1
```

既有 join-consumer negative case继续证明多 source gate 不被误判为直接因果。新增行为 case不是 legacy/private AST debt，而是 changed semantic projection 的必要 deterministic evidence。每个矩阵项都必须在 target 文档中登记 exact nodeid、断言目标和失败条件。

### R6：nested phase helper 与“唯一错误顺序 owner”表述冲突

方案第 2 节规定 helper 不拥有编译流程或错误优先级；但 `_compile_nested_graphs()` 契约实际拥有：

- `definition.nodes` 的 child 遍历；
- recursive `_compile_graph()` 调用；
- sibling child 的编译先后；
- 第一个 child compile error 的选择和抛出时点。

这不是单纯的 immutable projection。主函数仍拥有“nested phase 先于 parent binding scan”的大阶段顺序，但 helper 已拥有 phase 内部错误优先级。因此当前文案和 owner 定义不能同时成立。

**目标整改：**把 nested recursion loop 留在 `_compile_graph()`，由同一个 owner直接决定 parent/child phase 次序、sibling 顺序和首错边界。通过清楚的 phase comment、局部变量生命周期和 exact nested characterization表达边界，不把递归控制迁移到 leaf transformation helper。该选择依据是唯一 orchestration/error owner，而不是改动面大小。

## 4. 非阻断但必须同步修正的问题

### C1：`direct_targets` 的 owner 表述过宽

`direct_targets` 不保存以 `END` 为 target 的 direct edge；terminal direct edge 仍进入 `gates_to_end`。因此它不是“全部 direct edge 的 canonical owner”，而是：

```text
non-END direct target membership 的唯一编译期事实
```

删除 `direct_pairs` 的等价性仍成立，因为 data dependency target 必然是 concrete node；文档只需收窄 owner 描述，避免掩盖 terminal gate 的独立事实。

### C2：“第二次扫描 node ids”措辞不准确

当前 compiler 和目标伪代码本来就会多次遍历 `node_ids`，分别用于 binding、proof、descriptor 和 canonical assembly。停止条件应写成“不得新增额外 node-id full scan”，不能写成“不得第二次扫描 node ids”。

### C3：交付门禁遗漏 monorepo-root pre-commit

仓库 `AGENTS.md` 要求 handoff 前同时执行：

```text
make check                                      # mote-kernel
pre-commit run --all-files                      # monorepo root
```

当前 `Makefile::check` 只包含 lint、typecheck、complexity-ratchet、test 和 package-check，不包含 repository-level pre-commit。S01 第 9 节和 writeback 字段必须显式加入 monorepo-root pre-commit 结果或精确未运行原因。

## 5. 整改后必须继承的基础设计

整改不是重写基础架构。以下现有 owner、类型和语义必须原样继承，不能为了拆函数再造一套模型：

1. `_compile_graph()` 继续是唯一 graph-family compiler 和顶层 phase-order owner；
2. `FrontierTransitionPlan` 继续是 runtime/recovery 共享的唯一 compiled lowering；
3. `control_gates` 是 `activation_gates` 丢弃 route 后的重复常驻投影，应删除并按需派生 source；
4. `direct_pairs` 对 non-END data/direct duplicate membership 是 `direct_targets` 的重复索引，应删除；
5. State、command、reducer、protocol、Store、persistence backend 和 State tests 均不进入本单元；
6. 不新增 DTO、context bag、wide map tuple、compatibility alias、forwarding property、双写、cache 或第二 runner；
7. 继续复用 `GraphDefinition`、`GraphNode`、`ResolvedInputBindings`、`MaterializationPlan`、`FrameDescriptor`、`ActivationGate` 与现有 generic type flow；
8. module-scope imports、strict Pyright、no-`Any`/reflection/type-erasing cast 等基础纪律保持不变；
9. 不新增或扩写冻结 private helper 名称、局部变量、helper 数量、扫描次数、loop/comprehension 或 source text 的 legacy/AST tests；新增测试只验证可观察 compiler behavior，既有 architecture/source-discipline gate 原样复跑。

## 6. 最满足目标的完整整改方案

目标方案不保留“五个 helper 必须存在”的前提，也不以原文能复用多少为取舍依据。最终 compiler 结构固定遵循以下原则：

```text
GraphDefinition + scope
  -> _compile_graph owns nested recursion and the complete phase/error order
  -> typed dict work indexes own mutable compilation facts
  -> one edge scan produces direct/conditional/join/activation facts
  -> proof stages consume those same facts without mirrored projections
  -> descriptor assembly consumes resolved bindings and existing descriptor constructors
  -> final boundary alone freezes CompiledGraph/FrontierTransitionPlan maps
```

### 6.1 文档和批准 owner

1. 把完整 S01 target 写入主实施方案；
2. 删除独立 S01 target proposal，review record 保留但不拥有 target；
3. requirements 只记录 `GSP-A06` 状态，且必须晚于 target/evidence 审查；
4. 不复制 target、复杂度账本或 evidence matrix 到 README、requirements 或 review。

### 6.2 `_compile_graph()` 的唯一编排职责

以下逻辑保留在 `_compile_graph()`，因为它们共享 mutable facts、错误优先级或 phase ordering：

- nested graph 的 definition-order recursion；
- node/binding scan 与 nested boundary validation；
- edge lowering；
- entry、successor、reachability、joint activation、guarantee、terminal/output proof；
- `FrontierTransitionPlan` 和最终 `CompiledGraph` 的装配顺序。

主函数用清楚、固定的 phase comments 和局部变量生命周期表达阶段，不通过 context DTO、多 map tuple、闭包或
single-use phase wrapper 隐藏数据依赖。只有不拥有跨阶段顺序、能用窄 typed 输入/输出完整表达、并产生实际全局
复杂度收益的 leaf transformation 才允许成为 helper。

### 6.3 唯一事实删除

整改后的 production 必须达到：

| 事实 | 唯一 owner | 必须删除 |
| --- | --- | --- |
| route-aware non-terminal activation gates | `activation_gates` | `control_gates` 常驻投影 |
| non-END direct target membership | `direct_targets` | `direct_pairs` |
| terminal control gates | `gates_to_end` / derived `terminal_gates` | 把 `direct_targets` 错称为 END owner 的第二解释 |
| compiled execution lowering | `FrontierTransitionPlan` | 第二 compiled/recovery projection |
| compile-time lookup | owner-local typed `dict` | 提前 `FrozenMap`、dict/FrozenMap 双份工作索引 |
| resource order | `transition.resource_order` | `resource_order` 一次性 alias |
| descriptor construction | 既有 `_frame_descriptor()` 与 resolved declarations | `input_descriptor`/`output_descriptor` 无语义转交 alias |

source-only causal 判断必须直接由 `ActivationGate` 派生。若保留共享 predicate，它必须至少有两个真实 production
consumer、只接受 exact source/gate 类型、无 cache/index/DTO，并直接使用 validated gate invariant；不得为每次判断
构造临时 source map 或把 route-aware proof 改成 source-only proof。helper 是否存在由 candidate 的实际清晰度和全局
账本决定，不在设计阶段为了凑 phase 数量强制指定。

### 6.4 基础容器和冻结边界

- `dict[GraphNodeId, ...]` 继续承担 compiler invocation 内的 lookup/index owner；
- 所有 dict 都完整标注 key/value generic，不使用 bare container、`Any`、`object` 或 cast；
- helper 若只读输入，可用精确 `Mapping[K, V]` 表达 capability，但不得把线性 `FrozenMap` 伪装成等价工作索引；
- `frozen_map()` 只在构造最终 `FrontierTransitionPlan`、`CompiledGraph.nodes`、`nested_graphs` 和 resources 时调用；
- 同一事实不同时保留 mutable 与 frozen 两份供后续 phase 交替消费。

### 6.5 零负债验收

S01 不能通过上调 complexity baseline 交付。独立 baseline 差异先向当前实际值收紧；随后 candidate 必须满足：

```text
top-level definitions                  不增长
decision points                        不增长，目标净下降
logical/record clone candidates        不新增、不换入
thin single-use helpers                不新增、不换入
single-use private records             不新增、不换入
test-only private definitions          不新增、不换入
duplicate mutable facts/indexes        归零
extra full scans/sorts/freezes          不新增
lookup complexity                      不退化
```

局部 `_compile_graph()` 指标、全局 snapshot、health candidate identity diff 和 lookup/scan analysis 必须一起记录。
任何一项不满足都退回重新设计，不允许修改 ratchet 上限、增加 compatibility layer 或新增 legacy gate 追认。

### 6.6 行为与 evidence

- 复用全部精确命中的现有 compiler、nested、join、resource 和 runtime behavior cases；
- 在现有 behavior test 文件中补齐第 3 节 R5 的缺失场景；
- 明确加入包含两个同时非法条件的 deterministic error-priority characterization，证明 phase 重排没有改变首错；
- public topology shape 不变，因此现有 owner gate 原样运行，不新增 S01-specific architecture assertion；
- private/local 删除通过 writeback 中的 actual diff、可复现一次性 source-review 命令和复杂度输出证明；
- 不新增或扩写任何 legacy/AST/private-source-shape test。

### 6.7 原子交付

最终 change-unit 顺序应为：

1. 主实施方案中的 S01 target/evidence redesign；
2. 用户明确批准后，由 requirements owner 单独更新 S01 `GSP-A06` 状态；
3. production 与必要 behavior characterization 同一原子 implementation unit 落地；
4. 主实施方案 owner writeback 记录 actual diff、manifest、复杂度、scoped/full gate；
5. 独立 review audit 只复核，不修改 target 或批准状态。

任何步骤都不修改 State、State tests、protocol 或 persistence；任何步骤都不新增 legacy gate。

## 7. 重新申请批准的完成条件

只有同时满足以下条件，S01 才可重新提交 `GSP-A06` 审查：

1. target shape 只保留一个文档 owner；
2. 不再提前把 `FrozenMap` 用作高频工作索引；
3. candidate 全局 complexity snapshot 相对批准基线不增长，ratchet 配置与 change-unit manifest 可实际通过；
4. 明确列出全部适用/排除的 `GSP-Pxx`；
5. 成功、失败、边界 evidence 全部固定为 exact `path::test_case`；
6. route-insensitive single-source behavior 有直接 public characterization；
7. 旧重复事实、额外 scan/freeze、DTO/context/alias 的归零有可复现 source-review 记录；
8. nested recursion/error-order owner 只有一个且文案与实现一致；
9. 没有新增或扩写任何 S01-specific legacy/AST/private-source-shape 门禁测试；
10. implementation manifest 包含全部实际 production/behavior-test 文件，不预先漏列；
11. scoped checks、`make check` 和 monorepo-root pre-commit 均记录实际结果或精确未运行原因；
12. requirements owner 只在上述 evidence 闭合并获得用户明确批准后更新 S01 状态。

## 8. 本轮验证记录

执行了：

```text
python -B -m tests.architecture.complexity_rules
python -B -m pytest -q tests/architecture/test_complexity_gate.py --tb=short -p no:cacheprovider
```

结果：

- 当前 production snapshot：top-level definitions 504、decision points 1325、dataclass fields 501、thin single-use helpers 18；与 S01 文档记录的 current snapshot 一致；
- complexity health 当前仍为 FAIL：logical clones 12、record-shape clones 21、thin single-use helpers 18、single-use private dataclasses 1、test-only private definitions 1；这些是当前全仓既有项，不能由 S01 冒充清零；
- complexity ratchet：`1 failed, 6 passed`；失败原因是当前 `pyproject.toml` 配置仍为 511/293/182/526/1350，而实际为 504/289/178/501/1325，属于独立工作树 baseline 尚未回写；
- 即使独立 baseline 差异先被修复，S01 预期的 `504 -> 509` 仍会触发 top-level-definition growth，故 R2 不是当前 dirty worktree 独有问题。

未执行：

- `make check`：本轮只新增 review 文档，且其必经 complexity-ratchet 已有上述确定失败；不把未运行项记为通过；
- monorepo-root pre-commit：review 请求不授权格式化或修改整个 dirty monorepo，未运行；
- compiler behavior suites、Pyright、Ruff、build/package：production 尚未修改，本轮不重复运行历史结果。

本轮未修改 production、tests、State、requirements、主实施方案或 S01 proposal；只新增本 review record。

**最终裁决：保留“删除 `control_gates`、删除 `direct_pairs`、不实现持久化、继续复用唯一 `FrontierTransitionPlan`”四个方向；拒绝当前第二 target owner、五 helper 固定形状、提前 `FrozenMap` 化和不可执行 complexity contract。R1–R6 闭合前，S01 保持未批准。**
