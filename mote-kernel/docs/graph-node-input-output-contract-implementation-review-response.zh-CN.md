# Graph 节点显式多端口输入/输出实施方案第七次评审回复

> **历史设计批准记录，非规范事实源。** 当前 production 契约与验收仅以 `graph-node-input-output-contract-implementation.zh-CN.md` 为准；该正文已于 2026-08-17 实施。

## 1. 本轮范围与结论

- 评审对象：`docs/graph-node-input-output-contract-implementation.zh-CN.md`
- 当前正文：1596 行，日期 2026-08-16。
- 本轮重新读取全部正文，复核第六次评审的 scoped-run frame identity 与 `Graph.Values` canonical owner/direct-constructor contract，并再次交叉检查原始需求、现有 execution/state owner、nested/recovery、严格类型和文件级实施账本。
- 本轮只更新本回复，不修改实施正文、production code、tests 或 State。

**结论：第六次评审的 1 个 P0 和 1 个 P1 均已完整关闭；本轮未发现新的 P0/P1 或已知架构负债。当前实施规格评审通过，可以按本文进入 production 实施。**

本结论只批准实施规格；production diff 仍必须逐项满足第 11、13 节以及仓库完整质量门禁，不能以本次文档通过替代代码评审和行为证据。

## 2. 第六次评审问题关闭情况

### 2.1 Repeated nested activation 的 runtime frame identity 已闭合（原 P0，关闭）

正文已经明确分离 schema identity 与 runtime identity：

```text
ScopeRunCoordinate(scope, graph_run_id)

StableActivation(scope_run, superstep, node_id)

GraphInputAvailabilityCoordinate(scope_run, descriptor)
PublicationAvailabilityCoordinate(activation, descriptor)
ResumeInputAvailabilityCoordinate(activation, descriptor)
ChildBoundaryAvailabilityCoordinate(child_scope_run, descriptor)
```

- `FrameDescriptorIdentity` 只表示 canonical schema，不再充当 concrete frame key。
- Root 使用 root State 的 run ID；child 使用完整 scope path 与现有 `child_graph_run_id(parent_run_id, parent_superstep, nested_node_id)`。
- `StableActivation` 直接嵌入 `ScopeRunCoordinate`，publication、resume input、child boundary 和 recovery disposition 不再按 path/definition 隐式补 generation。
- New root/child graph-input candidate 只在 matching `StartGraphRun` successor acknowledgement 后按同一 final coordinate 安装。
- `ScopedFrameIndex`、complete/recovered continuation、`RecoveryTransferState`、family driver、迁移账本和验收矩阵全部使用同一 coordinate universe。
- 测试矩阵已明确覆盖 parent 在 superstep 1、3 两次激活同一 nested path，C1/C2 输入与 passthrough output 不覆盖、不复用、不在 recovery 中合并。

因此同一路径、同一 definition、同一 descriptor 的 repeated child runs 已由 exact child run identity 区分，不再存在上一轮的 frame collision 或旧 generation 串用问题。

### 2.2 `Graph.Values` canonical owner 与 construction contract 已闭合（原 P1，关闭）

正文已经冻结唯一低层 owner：`execution/graph/values.py`。

该模块唯一拥有：

- `NamedValue`、`_GraphValues`；
- `GraphInputFrame`、`NodeInputFrame`、`NodeOutputFrame`、`GraphOutputView`；
- shared immutable normalization；
- module-level `FactoryValueT`/`GraphValueT_co`；
- keyword-only `_ValuesConstruction`、owner-private `_ValuesSeal` 与 canonical factories。

Public contract 也已唯一化：

- `Graph.Values` 是 canonical `_GraphValues` 的 exact class alias，只用于 annotation、read 与 `isinstance`；
- 用户只能通过 `Graph.values(...)` 构造 concrete values；
- `Graph.Values(...)`、raw mapping、`NamedValue` entries、伪 token/seal 均不能形成合法实例；
- facade 直接声明 zero/variadic 两个 `@staticmethod` overload，再单向委托 values owner，不复制 normalization；
- outcome、resume、request/result、run context、engine 与 facade 直接单向依赖该 owner，不经 `graph.__init__` 回流，也不定义 shadow frame DTO/factory；
- public typing、behavior、constructor-negative 与 import-architecture tests 已全部列入验收矩阵。

因此实现者不再需要在编码时选择 Values 所属模块、public constructor、frame identity 或 import direction。

## 3. 原始需求对齐复核

当前方案没有偏离本轮最初收敛的用户操作方式：

1. Callable node 只在 `add_node(inputs=..., outputs=...)` 声明具名输入绑定与输出类型；没有 `Graph.field(s)`、`Graph.Value[T]`、contract ID、枚举 token、node handle 或额外 `Graph.node()` wrapper。
2. Graph input 首次通过 `Graph.graph_input(name, type)` 声明类型；node output 首次在 producer `outputs={name: type}` 声明，后续只用 `Graph.node_output(node_id, parameter_name)` 引用，不重复类型，也不拼接字符串 path。
3. 真实运行值统一由 `Graph.values(...)` 承载，并实际进入 graph input、destination-local callable input、node output、nested boundary 与 completed graph output；不是只给变量起名或继续传 shared raw input。
4. `inputs` binding 是普通 data dependency/readiness 的唯一事实源；`add_edge()`、conditional edge 与 join 继续使用字符串 node ID，但只表达 control declaration。
5. `add_node()` body 是 callable/child `Graph` 的 closed union；nested output 直接来自 child `set_outputs()`，不让 parent 重复声明，也不建立第二 runner。
6. Compiler 在任何 compiled cache、freeze、Start commit、resource claim 或 node call 前统一校验 reference、scope、方向、exact descriptor、nested boundary、data/control topology、entry/completion 与 guaranteed-before；concrete input/output 在各自最早可观察 admission 点做 exact type/key 校验。
7. Pyright 负责 public generic/API shape，不伪造 runtime string key 的逐端口静态推导；逐端口契约由唯一 compiled descriptor 与 runtime admission 保证。
8. Resource 保持 `resources=("database",)` 字符串 tuple 和既有 first-seen semantics，不与 typed value ports 对齐为复杂声明机制。
9. State 继续只拥有 recoverable control position；concrete frames 留在 invocation-local run context/sealed continuation，不增加 Store、journal、output persistence、State value mirror 或跨进程恢复承诺。

文档范围虽然覆盖 continuation、recovery、loop、nested、acknowledgement 和 builder transaction，但这些内容都是为了让真实多输入/多输出在现有引擎中闭合，不改变最终用户的 node/parameter 声明方式。

## 4. 本轮交叉审计结果

本轮再次检查以下边界，均未发现需要回写正文的新阻断：

- direct mapping、structured references、callable/nested overload 与 graph-output binding 是单一 public declaration truth；
- public mappings 在边界立即转为 immutable nominal definitions，builder mutators 使用 detached candidate/single commit，失败不污染 resource order/cache/freeze state；
- `Graph.Values`、outcome、commit result、transition 与 continuation 均有唯一 nominal owner及 owner-only construction；
- runtime publication 只在 exact settlement successor acknowledgement 后安装，State 继续是 activation/resolution authority；
- graph-input passthrough 与 node-publication output 使用同一 dual-source projection，不复制第二 value map；
- control loop 使用 exact superstep activation，nested loop 使用 exact child run，recovery equality 与 traversal ordering不再混用；
- state-only/recovered availability、limits、resource selector、frontier quiescence与 nested barrier 复用同一 compiled transition lowering，没有第二解释器；
- production、tests、docs、architecture gate 和 `state/graph_state/** = KEEP` 账本完整，没有 compatibility 双轨或未裁决候选。

## 5. 最终判定

第六次评审要求的 exact scoped-run/parent-activation frame identity，以及 `Graph.Values` canonical module、factory-only construction、import direction和测试，均已在正文的原则、公共 API、canonical model、runtime index、continuation、recovery、nested driver、文件账本、迁移顺序和验收条件中一致闭合。

此前已关闭的 recovery full semantic equality、factory generic/`Values[Never]`、outcome/result/transition seals、builder transaction、limits、Result/continuation invariance、resource semantics、dual-source output、nested routing/recovery与 State KEEP 决策继续成立。

**当前实施文档评审通过，可以进入 production 实施。本轮无遗留 P0/P1；后续若实现需要违反第 13.2 节任一停止条件，必须停止编码并重新进行需求/架构评审。**
