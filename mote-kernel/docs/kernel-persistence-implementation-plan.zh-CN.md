# Kernel 持久化闭环实施计划

## 0. 状态与评审约定

- 日期：2026-09-11。
- 调研提交基线：`e4ef968`；工作树另有用户正在进行的 routing/recovery 治理改动。
- 当前阶段：**P1、P2、P3 已验收；P4 全链路复核与最终门禁已完成，等待 code review。详见第 11 节。**
- 范围：只修改 `mote-kernel/`。不实现或修改 Rust、Cloudflare、数据库、网络传输和部署代码。
- 恢复装配唯一入口：`src/mote_kernel/agent.py`。
- 执行唯一入口：`mote_kernel.execution.Graph`；Agent 不实现 runner、scheduler 或 reducer。
- 本文件是本项持久化工作的阶段进度与验收记录 owner。Graph 主线治理总账只链接本文件，不复制进度。

每个 P 都执行“设计复核 → 一次性迁移 → 测试与门禁 → 记录 → 等待 code review”。
**未经用户确认，不开始下一个 P。** 阶段代码写完、测试通过和用户验收是三个不同状态，不能互相代替。
P0 之后按用户的“继续吧”进入 P1；该授权不跨过 P1 完成后的评审停点。

状态使用：`未开始`、`实施中`、`待评审`、`已验收`。发现阻塞或评审修改意见时，留在当前阶段处理。
未经用户明确授权，任何阶段不得提交 Git commit 或 push；任何情况下都不得覆盖用户改动或放宽门禁来掩盖设计问题。

## 1. 目标与完成定义

完成 Kernel 自己负责的持久化语义、类型化 Port、完整值证据恢复以及 Agent 装配，使调用者只需调用
`Agent.run(request)`，不用自行传 state、continuation、commit callback、恢复 revision 或后端种类。

最终完整链路为：

```text
Agent.run(request)
  → 获得本次运行的有效执行权限
  → 通过后端无关 Port 读取一致的持久化快照
  → 明确区分从未创建、已有记录及读取失败
  → 恢复精确 Config，使用 Graph 门面装配同版本图
  → 解码并准入 root / child 的 state 与完整值证据
  → Graph.run(...)，复用现有 compiler / planner / routing / reducer / family driver
      → reducer candidate + 完整 typed write-set
      → 原子提交 Port / 必要时对账该持久化提交
      → 精确确认
      → 替换 Python snapshot，安装已确认 frame
  → 所有执行任务收敛后投影业务结果、释放本次执行权限
```

必要验收场景：节点 A 的结果已提交，进程真正退出；全新进程创建全新的 Agent/Graph，只读取持久材料，
A 不重跑，B 使用 A 原先的输出完成。相同要求扩展到并发 frontier、循环、Join、interrupt 和 nested family。

这里的完成是 **Kernel 对所声明 Port 契约的完整实现与验证**，不是宣称某个外部数据库、Rust 服务或
Cloudflare Adapter 已经满足这些契约。外部实现仍必须独立通过对应契约测试。

## 2. 现状与文档裁决

### 2.1 已有基础必须复用

- `execution/commit.py` 已拥有 sealed transition、完整 typed write-set 和 exact acknowledgement。
- `family_driver.py` 已在提交确认后更新 state/frame，并管理 scoped owner、child handoff 和任务清理。
- `GraphRunState`、command、纯 reducer、revision/token 校验已有唯一 owner。
- compiler 已拥有不可变图、descriptor 和绑定；planner/routing 已拥有值可用性及控制流推导。
- `ScopedFrameIndex` 是已确认值的运行时投影；`Graph.Continuation` 是进程内、不可序列化的交接凭据。
- Config 已有 immutable snapshot、精确 key/digest、snapshot store 和 resolver；不得另做一套 Config 协议。
- `agent.py` 当前为空，应在此建立 Agent 装配，而不是在 Container、示例或另一个 executor 中补 runner。

### 2.2 已确认缺口

- 当前 state-only 调用从空 frame/child evidence 开始，不会读取完整持久化恢复材料。
- 通用 graph input/publication 尚无完整的 Kernel 持久值编码、解码和回读准入闭环。
- 没有 Agent 层统一处理 load、create-if-absent、原子提交、未知持久化提交结果对账与执行权限。
- 恢复示例中的内存 store 不能证明进程退出后的恢复能力。

### 2.3 文档适用范围

| 文档 | 本次采用方式 |
| --- | --- |
| 当前 Kernel 架构、源码、测试 | 现有行为和 owner 的基线；扩展而不另起路径 |
| Graph 内存语义收口计划 | “不做持久化”是该历史阶段的范围，不是本次禁令 |
| 平台架构目标草案 | 采用原子性、CAS、receipt、fencing 原则；不照搬多状态模型、Rust 分包或开发阶段 |
| Cloudflare Resident Agent 设计 | 采用 Agent 统一入口、后端无关 Port、冷恢复和持久化提交对账要求；不引入平台生命周期逻辑 |
| Graph 主线治理总账 | 保护已有 routing/recovery 改动；只在真实依赖需要时修改对应 owner |

Latest Config 不能被当成恢复时静默覆盖旧版本的理由。已有运行先恢复 state 引用的精确配置；后续配置变更
复用已有 Observe/Config 入口，并走同一 command/reducer/commit。Agent 不另建自动采用 latest 的配置推进路径。

## 3. 硬不变量与 owner

| 事实或规则 | 唯一 owner | 禁止事项 |
| --- | --- | --- |
| 图定义、绑定、descriptor、静态合法性 | Graph compiler | loader 或 Agent 再解释拓扑、另建 operation registry |
| 激活、frontier、settlement、Join、执行 token、Config cursor | `GraphRunState` 与纯 reducer | 新建 AgentState/DomainState、镜像 lifecycle |
| 输入可用性、路由、恢复可执行性 | 既有 planner/routing/recovery admission | durable-only planner、猜测最新 publication、隐式重跑补值 |
| candidate、完整写集、确认后的安装顺序 | execution commit owner | Agent 另做 reducer、提交后补写业务值 |
| graph input/publication 的具体值 | 同一提交绑定的 typed frame evidence | 独立更新 value store、第二套 revision、未确认值进入运行输入 |
| Config payload、key、digest、能力解析 | Config owner | 在 Agent 或恢复包内复制 Config 状态机 |
| Agent load/装配/执行/持久化提交对账协调 | `agent.py` | Container 或业务调用方自行拼恢复参数 |
| ReAct 到 END 后的新任务接入、续轮与新 run 调度 | 上层驱动 | Kernel 自建任务调度、自动续轮或据终态自动创建新 run |
| CAS、存储事务、执行权限的实现与仲裁 | 注入的外部 Port 实现 | Kernel 持有 SQL/文件/平台 storage handle 或自行实现租约存储 |
| 工具执行记录、工具崩溃恢复与工具执行结果对账 | Runtime | Kernel 建立工具执行账本、工具 receipt store 或工具对账流程 |

持久化读响应可以包住多个现有 `GraphRunState`、值证据和提交元数据，但它只是**一致性读响应**，不是第二个
runtime state。root 和每个 child 都使用同一种 `GraphRunState`；各 scoped run 保留自己的 revision，
不能假设整棵 family 中所有 state 的 revision 相等。

物理存储可以分表、分记录或使用 blob；对 Kernel 的语义必须是：同一 scoped transition 的 state、input/
publication 与 receipt 不可撕裂，family 回读来自一个有一致性保证的读取边界。

派生 child boundary 从已确认 child state/output 重建，不另外赋予它独立 lifecycle。已由 state 保存的
resume payload 继续使用现有恢复输入规则，不建立平行的 resume store。

## 4. 后端无关契约

### 4.1 Agent 的构造与调用边界

- 构造时注入明确的 Agent 身份、Graph 装配能力、值 codec 和必需的 persistence/authority capabilities。
- Config 相关 capability 复用 Config owner；使用 Config 的装配缺少必需解析能力时失败，不在执行中兜底。
- `Agent.run(request)` 只接业务初始输入或明确的恢复输入，不接 State、continuation、存储地址或 backend kind。
- Agent 结果只投影业务输出、失败、中断和终止信息；运行状态交接仍留在 Kernel 内。
- 同一 Agent 实例并发调用的准入必须显式、确定，不依赖共享可变 Graph state。
- Kernel 只负责本次请求所属 run 的创建、恢复、推进和终态投影。ReAct 到 END 后如何接收新任务、何时
  发起新 run 由上层驱动，不属于本次 Kernel 架构设计或实现范围。
- 已有终态只回放的约束针对同一个 run，不意味着同一逻辑 Agent 永远不能执行新任务；Kernel 不替上层
  制定 Agent 业务生命期、任务去重或续轮策略。

### 4.2 Persistence Port 必须表达的语义

| 操作 | 必须保证的语义 |
| --- | --- |
| load | 在有效权限下返回完整一致的 family 材料，或明确的“从未创建”；不能混合不同读取时刻的 state/value |
| commit | 接收一次不可变的提交请求；原子验证 expected revision/不存在条件、权限及提交身份，并写入全部事实与 receipt |
| reconcile | 对同一提交身份及内容给出已应用、明确未应用或未知；不得将未知降级为不存在 |

这里的 receipt 与 reconcile **只对应 Kernel 状态及值证据的持久化提交**，不对应工具执行或外部副作用。
工具执行情况由 Runtime 自己记录与对账，不能复用这个 Persistence Port 去查询或恢复工具任务。

创建复用 `StartGraphRun` 和同一个 commit 操作，以 expected-absent 表达。不得再建单独的初始化 runner 或
另一套“创建成功即更新内存”的路径。

提交身份复用现有 scoped run、run id、revision 和 activation 设计，不引入随机 retry identity。
相同 key、不同内容是冲突，不是幂等重放；比较范围必须包含完整值证据，不能只比较 candidate state。

权限、网络、格式、版本、缺失证据和 tombstone 错误均保留为类型化失败。只有契约明确确认的 never-created
可以走新运行；不能 `except Exception: return None`。

### 4.3 Execution authority

执行权限由窄 Port 提供。取得权限后再读取其约束下的快照，每次提交携带相同有效权限。
获得新权限必须代表外部仲裁已经允许本次 owner 推进，而不是“读到了 active lease，所以猜旧 worker 死了”。

Kernel 复用现有 `FenceGraphExecution` 转换收回快照中的旧 attempt；跨实例存活判断、仲裁、租约时钟及
存储级 fencing 实现不进入 Kernel。旧 authority 不能提交，即使旧进程仍持有 Python state。

释放发生在本地任务全部收敛之后。释放失败不能覆盖原始执行/提交错误，取消不能遗留无人负责的获取或释放任务。
获取操作无法返回 grant 时，获取结果的不确定性由 Authority Port 自行收敛；Kernel 没有可以代为释放的能力。

### 4.4 不冻结具体 wire

本次定义 Kernel 的类型化 Port 和可观察语义，不定义 SQL schema、JSON/RPC envelope、HTTP endpoint、
gRPC service、Cloudflare binding 或 Rust FFI。Port 实现需要跨进程调用时，由 composition 注入既有
Invocation 能力；Graph 不直接持有 transport。

值 codec 的 identity/version 属于领域值编码契约，不等于选中了某种 persistence 协议。
未来出现跨语言 wire/durable 格式变更时，仍必须由 monorepo `conformance/` 同步承接，不能由 Kernel 私定
第二份跨语言 schema。本轮不越界修改 conformance、Rust 或 Cloudflare 文件。

## 5. 值证据与恢复准入

### 5.1 编码边界

- 对 graph input 与每次成功 publication 提供显式、版本化、保持 GraphValueT 关系的 codec。
- codec 由对应值/装配 owner 提供；Kernel 不用 pickle、动态 import、反射或无类型 JSON 猜测业务 DTO。
- Config 的持久 snapshot 与已解析的 runtime capability 分开；不序列化 callable、Port、task、lock 或 session。
- 编码规则集中维护；与 resume codec 共用确有相同不变量的基础规则，但保留不同输入域和异常边界。
- 不能以任意业务 DTO 的 `__eq__`、`repr` 或进程内地址充当持久身份、内容指纹或重放证明。
- 编码、合法性检查及将来安装所需材料的准备必须在提交前完成；不能确认后才发现无法安装值。

### 5.2 必须准入的内容

1. 根身份、scope/run、graph definition/version 以及 state 的合法性。
2. child 的 parent activation、确定性 child run identity、历史/当前 child 的归属。
3. input/publication 对应的精确 compiled descriptor、具名字段和 nominal type。
4. codec identity/version、内容完整性与确定性编解码约束。
5. publication 与 settlement ledger、提交 revision、execution provenance 的绑定。
6. graph input、历史 publication、state-owned resume input 和 completed output 的完整性。
7. Config snapshot key/digest 与 state/frame 所引用版本的一致性。
8. 一致性读返回的 family 成员关系；不能把已存在 child 的遗漏伪装成未启动 child。

正常的“child 尚未创建”必须与“必需 child 记录丢失”区分；判定复用现有 lineage/planner，不凭
loader 自己的重跑策略决定。存储 Port 对一致性读及完整成员集合负责，Kernel 对返回材料的关联和合法性负责。

准入失败发生在任何 fence、resume command、claim、node call 或新 durable write 之前。
恢复后的 frame index 和 continuation 由 execution owner 创建；持久化材料不绕过 sealed constructor，
也不反序列化 `_GraphContinuation`。

### 5.3 唯一运行链

Agent 完成 load/解码装配后，进入 Graph 的既有准入和 `run()`。如需恢复材料接缝，它是 Graph 内部的
“准入并构造现有运行投影”，不提供另一套 public recover runner。

standalone Graph 的进程内能力继续有效，不是需要伪造的 durable 模式；但 Agent 缺失必需 Port 时不能退回
`commit=None`。不保留一条旧 Agent 恢复路径与一条新 Agent 恢复路径并存。

## 6. 提交、异常与取消

### 6.1 Kernel 持久化提交

提交顺序固定为：

```text
pure reduce → 编码并准备完整提交材料 → atomic commit → exact acknowledgement → 安装 state/frame
```

- 每条 command 仍独立提交；最后一个 settlement 与随后 routing 保留为两个独立 durable transition。
- 请求一旦形成，在不确定结果的对账/重试期间不得重新编码、重新 reduce 或换 commit key。
- `Applied` 必须精确确认原 candidate 及完整写集，才能继续执行。
- `NotApplied` 仅在权限仍有效时重发同一不可变请求；次数由显式不可变策略限制，不增加通用 retry 框架。
- `Unknown`、ack mismatch、失效权限或无法证明结果时停止推进，不隐式重新执行节点。
- 提交来源错误由 commit owner 分类，family driver 保持对应错误边界；Agent 不解析错误字符串。
- 未知提交后不基于旧内存 state 再写 fence/abort，不让 sibling cleanup 掩盖或扩大不确定结果。
- 普通 node failure、node-origin cancellation、调用方 cancellation 与 commit-origin failure 保留各自语义。
- 本地 task/session/child 清理由现有 execution owner 完成；Agent 只在完成收敛后处理自己的 authority 生命周期。

### 6.2 工具执行情况由 Runtime 记录与对账

工具调用崩溃、执行进程退出、工具响应丢失或外部任务结果未知时，**Runtime 是工具执行记录和对账的唯一
owner**。这不是 Kernel 暂缓实现的功能，而是明确不属于 Kernel 的职责。

- Runtime 记录工具执行情况，并根据自己的执行记录、receipt 和工具能力完成恢复与对账。
- Kernel 通过现有工具 Port/Invocation 消费 Runtime 返回的类型化结果，不直接查询工具执行日志、轮询
  外部任务、维护工具 receipt，或推断副作用是否已经发生。
- 必要的调用关联身份沿既有类型化调用契约传递，不为本计划另建工具执行身份体系。
- Kernel 恢复后，同一逻辑工具调用再次抵达 Runtime 时，由 Runtime 按其执行记录及幂等/对账契约处理；
  Kernel 不能因 Graph 尚无 settlement 就推断工具从未执行，或自行决定重复执行副作用。
- Runtime 返回的结果进入 Kernel 后，仍由同一 Graph command/reducer/commit 建立 Kernel 自己的事实；
  这不把 Runtime 的工具执行状态变成 Kernel 的镜像状态。
- 工具通信仍经过既有 Invocation 入口；本次既不新增 Kernel 工具 runner，也不要求 Runtime 另建传输层。

两种不确定结果必须严格区分：

| 场景 | 对账 owner | Kernel 行为 |
| --- | --- | --- |
| 工具已执行但响应丢失，或工具执行崩溃后结果未知 | Runtime | 消费 Runtime 契约提供的结果，不发起工具执行对账 |
| Runtime 已返回结果，但保存 Graph state/value 的 commit 确认丢失 | Kernel 协调 Persistence Port | 只对账该 Graph 提交，不查询或重跑工具 |

State CAS 不是外部副作用 exactly-once。Kernel 不建立工具 receipt、业务 effect store 或工具对账状态机，
也不能将未知工具副作用宣称为可安全重跑。

## 7. 实施阶段与 review 停点

### P0：设计与施工计划

- **交付**：本文件、owner/调用链/异常边界、迁移范围、阶段验收与进度模板。
- **不做**：生产实现、持久化协议落地、门禁参数调整。
- **停点**：等待用户评审完整设计和阶段划分。

### P1：完整值证据、提交契约与统一恢复准入

- 在 execution owner 内完成持久化值材料、codec 接缝、完整写集映射和恢复材料准入。
- 复用现有 state、scope/run/activation、descriptor、frame 和 Config；不新增 runtime state 模型。
- 收口提交来源错误和 exact acknowledgement 的规则，为 Agent 接线提供一个完整的、可验证的边界。
- 修改现有规则时，同时迁移其所有生产调用点、测试和示例；不添加兼容 alias 或保留替代实现。
- **验收**：非空值、多 payload 类型、版本/descriptor/身份错误、缺值、值证据重放冲突、提交前失败、确认后安装，
  以及现有 Graph 状态转换、恢复错误优先级、取消与 family 行为全部验证。
- **停点**：记录实际 API、完整调用链 diff、删除范围、测试/覆盖率/门禁，等待 review。

P1 不能只堆 DTO、Protocol、未接入的框架或 TODO。新类型必须被本阶段的写集准备/恢复准入真实消费，
不先冻结一套无人使用的 store framework。Agent 的外部读取入口仍留给 P2，不在示例里另造入口过渡。

### P2：`agent.py` 统一装配与运行闭环

- 实现后端无关的必需 Port 装配、执行权限、load、新建与已有运行分流。
- 完成精确 Config 恢复、Graph 装配、P1 材料准入以及同一 `Graph.run()` 调用。
- 完成完整提交、create-if-absent 冲突处理、持久化提交的 Applied/NotApplied/Unknown 对账及权限失效处理。
- 完成 Agent 业务请求/结果、interrupt 恢复、终态重读和异常/取消清理。
- **验收**：同一 Port 契约由不同测试适配器承载；新进程恢复不依赖旧 Agent、Graph、codec 缓存或 continuation。
- **停点**：记录创建/恢复/提交/退出四条实际调用链及失败矩阵结果，等待 review。

P2 不为 Agent 增加热态镜像、后端 selector、TTL 或 park/unpark。不可变编译产物可以复用，但不能为了驻留
优化再建状态 owner；现有 Graph 的任务收敛与 release 行为不机械拆开。上层的新任务接入与续轮调度不作为
P2 的待实现项，也不在 `agent.py` 中增加跨 run 的业务调度循环。

### P3：跨进程与组合故障验收

- 用隔离的新进程和测试专用持久适配器覆盖真实退出/重开，而不只重建同进程对象。
- 覆盖并发 frontier、循环/Join、nested family、多级 parent/child 与 Config 的组合恢复。
- 按第 9 节故障矩阵补齐组合边界；发现问题必须回到其唯一 owner 修复，不能添加测试专用生产分支。
- 工具崩溃场景只验证 Kernel 消费 Runtime 类型化结果及不越界对账；不在测试迁移中实现 Runtime 执行账本。
- 明确外部 Port 实现必须满足的行为断言；不替外部项目宣称通过。
- **停点**：记录每个故障注入点的期望与实测、无效旧 owner 拒绝证据及所有剩余限制，等待 review。

P3 是组合证明，不是把 P1/P2 的基本分支测试和覆盖率推迟到最后；每个阶段新增行为必须当阶段验证。

### P4：全链路复核与最终交接

- 从 Agent 入口审查 load → decode → admit → drive → commit → project → cleanup，逐项确认唯一 owner。
- 复查示例和中英文文档，清除把 in-memory callback/state-only 示例描述成完整 durable recovery 的说法。
- 审查未使用类型、重复 codec/validation、兼容入口、隐藏缓存和可删除旧路径；不借复杂度指标制造抽象。
- 完成全部类型、测试、覆盖率、架构、复杂度、打包和仓库 pre-commit 门禁。
- **停点**：提交最终证据与限制清单，等待最终 code review；用户验收前不标为完成。

文档/API/示例迁移与相应 P1/P2 代码在同一阶段完成。P4 是复核，不允许把 legacy 清理长期拖到 P4。

## 8. 修改、复用与删除范围

| 范围 | 允许的工作 |
| --- | --- |
| `src/mote_kernel/agent.py` | 唯一恢复装配入口、后端无关 Port 接线、运行协调及业务结果投影 |
| `execution/commit.py` | 唯一提交规则、完整写集绑定、确认和提交来源错误分类 |
| `execution/run_context.py`、`execution/invocation.py` | 复用 frame/lineage 投影和统一准入，不建立第二 snapshot |
| `execution/graph_result.py` | sealed result、partial handoff 和 exact commit continuation provenance 的唯一 owner |
| `execution/facade.py` | 保持 Graph 唯一门面，承接必要的 owner-internal 恢复材料准入 |
| `execution/graph/*` | 只在真实 codec/descriptor 需求下修改；不复制 compiler 规则 |
| `execution/family_driver.py` | 只修改提交错误、已确认安装和清理边界，不建立 Agent 专用 runner |
| `state/graph_state/*` | 只为确有 state-owned 事实的必要变化修改，仍经唯一 reducer |
| `config.py` | 优先复用；仅修正本次闭环实际暴露的 owner 问题，不新增 latest 回退 |
| `tests/`、`example/`、`docs/` | 当阶段同步迁移有价值的行为、示例与说明 |

新增模块只能按已经确认的实际职责建立，不预先创建 persistence 包树、models/common/helpers 或 backend
子目录。具体文件增删必须记入对应 P 的实测记录，不将此表理解成任意重构授权。

必须删除或修正的内容：

- 本次迁移中被替代的 codec、准入或恢复实现及全部调用点，不保留 wrapper/alias。
- 示例中把仅保存 control state 说成完整进程恢复的行为与描述。
- 新 Agent 实现中任何 fallback 到内存确认、重跑补值、直接持有 SQL/文件/平台句柄的代码。
- 只为 legacy test 保留的构造器、旧参数、反射通道和第二执行路径。

当前用户已有的 routing/recovery 治理改动必须保留。每次编辑前重新检查相关 diff；不将并行新增的测试或
代码误认作本任务可以回滚的内容，也不直接重放调研时保存的旧 patch。

## 9. 验收矩阵

| 边界 | 必须观察到的结果 |
| --- | --- |
| 必需 Port 缺失或错误 | assembly/admission 失败，不创建 state，不调用节点 |
| 明确 never-created | 只有同一 Start/commit 路径可以创建 |
| load 超时、权限错误、tombstone、损坏 | 不当成新建，不清空旧状态 |
| 编码、解码、字段类型或 descriptor 不匹配 | 写前或恢复准入失败，无 durable mutation |
| root/child/scope/run/parent 交叉污染 | 在执行、fence 和 resume 前拒绝 |
| state 存在但 input/publication 缺失 | 不通过重跑 settled node 或读取“最新值”弥补 |
| 错误 codec/version/Config digest | fail closed，无版本回退 |
| 事务提交前失败或回滚 | 内存保持旧状态，不能读到半份写集 |
| Graph 持久化提交成功、确认丢失 | 只用原 key/原内容对账该提交，确认 Applied 后才安装，不查询或重跑工具 |
| 明确 NotApplied | 在有效权限与有限策略下重发原请求，不重跑节点 |
| Unknown 或错误 acknowledgement | 停止推进；不基于旧 state 写 cleanup transition |
| 同 key 同内容 / 同 key 不同内容 | 前者精确重放，后者冲突；不能只比较控制状态 |
| CAS 竞争、旧 authority、旧 execution token | 旧 owner 不能提交；不自动 rebase candidate |
| A 已结算后进程退出 | 新进程不执行 A，B 读取原输出 |
| 部分 sibling 已提交 | 只推进未结算工作，保留已确认部分 |
| settlement 与 routing 之间退出 | 保留已提交屏障，从同一 routing 规则继续 |
| child 已完成、parent 尚未结算 | 从已确认 child 结果完成 parent，不重复执行 child |
| 循环和重复 nested 路径 | 按 activation/child run 精确关联，不取路径上的最新结果 |
| interrupt 回答重放或坐标错误 | 原有精确身份、消费一次及错误优先级保持 |
| 对同一 completed/failed/aborted run 再次调用 | 投影已有终态，不自动开新 run；新任务由上层驱动 |
| Config 更新后重启 | 使用 state 实际引用的 snapshot，历史 frame 不串配置 |
| 等待者取消、node 取消、commit 取消 | 保留各自边界，任务全部收敛，无孤儿任务或错误覆盖 |
| 多种不可变 payload 类型 | 从 codec 到写集、load 和结果不擦除泛型关系 |
| 工具调用崩溃或工具响应丢失 | 工具执行情况由 Runtime 记录和对账；Kernel 不发起工具日志查询、轮询或执行对账 |
| Graph 尚未记录工具结果 | 不据此推断工具未执行；恢复后的调用仍由 Runtime 按其执行契约处理 |

测试适配器可以位于 `mote-kernel/tests/`，用于故障注入和跨进程证据，不是生产存储实现。不能以一个
内存字典或同进程对象重建，替代真正的进程退出/重开测试。

## 10. 门禁与阶段记录规则

每个实现 P 必须完成：

1. 完整调用链与 owner 人工复核；说明为什么新增抽象必要、哪些重复路径被删除。
2. 受影响的确定性测试，以及新增转换、恢复和异常边界的测试。
3. strict Pyright、Ruff/format、architecture、全量 pytest 与项目要求的 100% 覆盖率。
4. complexity health 零已证技术债；逐项审查高召回指标，不按热点机械拆函数。
5. 设计通过人工审查后才更新精确 ratchet 值；不能放宽 health 或增加排除项掩盖问题。
6. `make check` 和从 monorepo 根目录运行的仓库级 pre-commit。

失败须区分本阶段新增、已有用户改动和环境限制；只记录事实，不把“以前通过”记成“本阶段通过”。
为保护用户修改，自动修复型检查须控制影响范围；必要时在等价当前工作树的隔离副本验证并记录方法。
任何未通过或未执行的门禁，均不能将实现阶段标为已验收。

### 每个 P 的必填记录

```text
阶段 / 日期 / 提交与工作树基线：
状态：实施中 / 待评审 / 已验收
本阶段实际交付：
完整调用链和 owner 的变化：
实际修改文件与删除的旧路径：
确定性用例与故障注入结果：
实际运行的命令、通过/失败/未运行及原因：
类型、覆盖率、复杂度、架构、打包和 pre-commit 结果：
已知限制与未解决问题：
用户 review 意见与处理结果：
下一阶段启动条件：
```

## 11. 阶段进度

| 阶段 | 状态 | 记录 |
| --- | --- | --- |
| P0 设计与计划 | 已验收 | 用户明确上层调度边界，并以“继续吧”授权进入 P1 |
| P1 值证据与恢复准入 | 已验收 | 两轮复审意见均已闭环；独立复审与完整门禁通过，按用户“通过则提交”的授权验收 |
| P2 Agent 统一装配 | 已验收 | 两轮证据契约复审、整改和完整门禁通过；用户明确验收并授权进入 P3 |
| P3 组合故障验收 | 已验收 | P1/P2 边界审计、跨进程组合故障矩阵和全部门禁完成；用户明确授权继续 P4 |
| P4 最终复核交接 | 待评审 | 完整调用链、公开说明、重复路径与全部最终门禁已复核 |

### P0 / 2026-09-10

- 交付：本计划；明确 Kernel-only、后端/协议无关、Agent 唯一恢复装配入口及逐 P review 停点。
- 调研：核对现有 commit、state、frame、Config、recovery 和示例；确认 `agent.py` 为空。
- 生产变更：无。未修改、回滚或代为收口用户现有 routing/recovery 改动。
- 删除范围：本阶段不删除生产代码；后续替代项必须与调用点、测试和示例在相同阶段一起迁移。
- 文档检查：`git diff --check` 及两份文档的 `git diff --no-index --check` 通过。
- 仓库检查：在 monorepo 根目录对本阶段两份文档执行 `pre-commit run --files` 通过；代码类 hook 因无适用
  文件跳过，不记作代码门禁通过。
- 未运行：本阶段未运行 `make check`、全量测试/覆盖率和 `pre-commit run --all-files`；P0 没有生产变更，
  本记录不宣称现有工作树或未来实现已经通过这些门禁。
- 评审：用户明确 Runtime 工具对账和上层新任务调度边界后，以“继续吧”授权进入 P1。
- 评审补充：按用户要求，将 Kernel 持久化提交对账与 Runtime 工具执行对账明确分开；后者不是 Kernel
  的待实现项。同步更新 owner、Port 范围、异常说明、P2/P3 验收和故障矩阵，未启动生产实现。
- 评审补充：用户明确 ReAct 到 END 后的新任务接入属于上层驱动。已从 Kernel 待决事项中移除该问题，
  将终态回放限定为同一 run，并明确 Kernel 不实现续轮或新任务调度；不改动上层、Rust 或 Cloudflare。
- 架构复核：Config 恢复使用精确历史版本，配置推进复用现有 Observe/Config 入口；不另建 Agent 自动升级
  路径。当前没有其他需要用户裁决的 Kernel 架构取舍。
- 收尾检查：2026-09-10 两份阶段文档的空白检查及根目录定向 pre-commit 再次通过。

### P1 / 2026-09-10 / 二轮复审通过，已验收

#### 二轮复审与修复计划

- 两项均已实测成立：初次 durable run 和 cold recovery 的 continuation 都能在 `commit=None` 时把内存 revision
  从 2 推进到 6、持久 revision 留在 2；删除 graph input/publication 的 Config cursor 后也能恢复并把 `None` 交给节点。
  先前 2499 tests 和门禁通过未覆盖这些契约，不能作为验收依据。
- continuation 的唯一 owner 保存原 commit capability；省略 commit 或传 `None` 都继承该 capability，显式替换必须拒绝。
  transient continuation 不得中途改为 durable；需要换提交能力时重新读取 checkpoint，使用新的 `GraphRecovery`。
  所有正常结果和 partial handoff 都遵循同一契约，不以仅在当前调用绑定 recovery commit 代替跨调用约束。
- 每个持久值只保留一个 State-owned evidence commitment，覆盖 availability coordinate、descriptor、birth commit、
  codec identity/version、payload 和 Config cursor（包括明确的无 Config）；publication 还覆盖 settlement provenance。
  在构造、读取和 receipt 准入时统一重验；历史上合法的无 Config frame 不根据当前 state 被补写 Config。
  Config 更新仍只由 Observe 消费并随其提交持久化；不新增更新入口、持久化路径或后端认证机制。
- 先补初次/恢复/partial continuation 与 graph input/publication cursor 删除的确定性边界回归，再完成 owner 迁移；
  最后重跑类型、测试、完整覆盖、复杂度、架构和根目录 pre-commit。完成后记录本轮结果，停在 P1 等待 review。

#### 基线与范围

- 保留用户现有 routing/recovery/Config 传播、已暂存内容、文档删除及门禁修改；没有 stage、commit 或创建分支。
- 对 `e4ef968` 加原有用户改动的隔离基线运行 `make check`：**2258 tests、100% coverage 全通过**。
  实施期间出现的测试失败及 ratchet 失败属于 P1 迁移，不能归因于原有基线。
- 只修改 Kernel。`agent.py` 仍未实现；没有 Rust、Cloudflare、数据库/传输适配器或 Runtime 工具对账改动。

#### 实际调用链与唯一 owner

1. `FrameCodec` 统一 resume 与持久 frame 的版本化编解码基础。持久值验证字段、精确类型、确定性往返；
   `GraphEvidenceCommitment` 由 commit owner 统一覆盖完整 availability coordinate、descriptor、birth commit、codec、
   payload、Config cursor（含缺席）及 publication provenance。Config 只保存 cursor，能力对象不进入 codec。
   resume 保留其既有 Config 继承和异常边界。
2. `prepare_transition` 仍由 reducer 产生唯一 candidate 与 typed write-set。`DurableGraphCommit` 投影出
   `GraphPersistenceCommit(scope, expected_revision, candidate_state, writes)`；**没有 reducer command**，后端不执行图规则。
3. root、child、普通 transition 都先准备不可变 frame 安装结果，再 await commit。只有 receipt 重新准入且与整个请求
   精确相等，才更新运行中的 state/frame。比较 durable bytes/facts，不调用业务对象 equality。
   Session 选择也在同一 family 串行边界内完成：root/child/sibling 共享一个 family Session owner；无 successor
   的 transition 在提交前选取 owner 当前值，显式完整 successor 在其 receipt 确认后推进 owner。历史 frame 不
   保存 Session 镜像。
4. `GraphRecovery(checkpoint, commit, configs)` 强制绑定 durable commit；读取和后续提交只用 `commit.codec`，
   `run(recovery=...)` 拒绝另传 commit。二轮将跨调用绑定收敛到 `graph_result.py`：所有 continuation 和 partial handoff
   保存原 commit，省略或传 `None` 都继承，显式异对象在执行前拒绝；不按 concrete durable 类型分支。
   `result.py` 仅保留 task/settlement/disposition，`run_context.py` 仅保留 frame/evidence，旧定义与 snapshot 别名已删除。
5. 恢复材料经 envelope 重新准入、compiler descriptor、scoped state、typed frame、lineage、Config history 和完整性证明，
   自底向上重建 completed child boundary，再进入原有 fence/resume/preflight/family driver。没有第二个 runner。
6. 外部 load、Config resolver、执行权限和未知 **Graph 持久化提交** 对账仍由 P2 在 `agent.py` 接线；工具执行记录和
   崩溃对账属于 Runtime。上层决定 ReAct END 后的新任务/新 run。

#### 五项 review 缺口的处理

五项均在复核时成立，没有以既有测试通过为理由忽略。

| 缺口 | 最终契约与 owner | 主要回归 |
| --- | --- | --- |
| 恢复无 commit / codec A 读、B 写 | `GraphRecovery` 绑定必需的 `DurableGraphCommit`，不能覆写；连续恢复后的持久 state 与结果一致 | `test_persistence_admission.py`、`test_persistence_recovery.py` |
| payload 篡改沿用旧 commitment | 实际读取与 receipt 确认边界重新执行 envelope/record/frame owner 准入；解码前重新计算完整 evidence commitment | `test_persistence_integrity.py` |
| frame Config 超前或串 definition/version | `GraphConfigCursor.admit_history` 统一历史关系；同 revision 必须同 digest；resolved Config 按唯一 `ConfigSnapshotKey` 索引并精确验 digest | `test_persistence_config.py` |
| completed publication 宽松超集 | completion 保留 `settled_publications`；全部生命周期的 publications 与完整成功账本精确相等 | `test_persistence_integrity.py`、`test_continuation_integrity.py` |
| child 从未创建与记录丢失混淆 | `child_runs` 为 `ScopedStateBinding | UncreatedGraphRun`；省略不证明不存在；state 的历史/current nested activation 决定必需集合 | `test_persistence_children.py` |

不新增终态 manifest、压缩模式、child lifecycle 或状态镜像。`UncreatedGraphRun` 仅是外部一致读取提供的负证据，
不是运行状态；已确认 child 创建通过既有 family evidence owner 替换它。缺少证明、外来/重复/冲突证明、已结算 child
被声称不存在均拒绝。伪造未创建证明也不能越过原有持久化冲突检查重跑一个已执行 child。

额外边界复审还收口了 receipt 的 scope 标量准入（包括伪造 equality 的 tuple 子类）和同一个历史 Config revision
返回冲突快照。持久化 digest 是完整性检查，不是对恶意后端的认证；Port 的一致读、原子写和权威身份保证仍不可省略。

#### 边界验证，而非只补覆盖率

- root/child 的 Start、Claim、第一节点结算、Advance、第二节点结算、Complete：提交前/提交后失去确认 × I/O error /
  commit-origin cancellation，**48 个组合**。冷恢复只复用已确认事实；未持久结算的调用可能再次抵达 Runtime，
  不冒充工具 exactly-once。成功恢复后再次冷读终态不执行节点。
- 等待者取消 root/child 的 Start 或结算：已发出的原子提交仍收敛，再按调用方取消规则终止；已提交值不丢失，冷读不续跑。
- 单层和三层 child 未创建、已有 child 快照遗漏、false negative、completed child 在 parent 结算前退出，以及 partial fence
  handoff 携带未创建 sibling。partial fence 测试用同一个 reducer 和提交契约构造合法旧 lease，不增加测试专用生产路径。
- completed root 与 nested scope 缺失中间 publication / 注入从未结算 publication；重复坐标、错误 scope/descriptor、
  future activation/revision/token、非 canonical payload、错误 codec、伪造 metadata 和 receipt 均 fail closed。
- 历史 N 与当前 N+1 Config、超前/外来/同 revision 冲突、缺少 capability、同 key 异 digest、重复历史解析、codec 偷带能力。
- state-owned interrupt override、部分 sibling success 与 Join、failed/aborted/completed 终态、业务对象禁止 equality，
  以及所有原有恢复、错误优先级、任务清理和取消测试一同验证。

#### 删除与文档迁移

- 删除旧 `execution/graph/resume_input.py` 的独立 codec binding；生产、测试导入全部迁移到 `FrameCodec`，无 alias。
- 删除旧 missing-child disposition 类型；continuation/recovery/family 一次性迁移到 `child_runs` 闭合 union，无旧字段别名。
- 删除 completed scope 的 publication 宽松分支；没有终态投影与完整历史并存的双路径。
- 保留 standalone Graph 的进程内执行/控制态恢复，不把它们描述为完整 durable recovery。中英文架构文档、README 和
  `checkpointed_import` 示例均明确：只保存 state、重建 Graph 或携带 continuation 都不能证明跨进程持久恢复。

#### 复杂度人工复核

- 先审完整链，再更新 `pyproject.toml` 的**精确实测值**，没有增加余量、排除项或放宽 health；所有零健康目标保持为零。
- 新增成本来自被生产调用实际消费的编码 envelope、原子确认、读取边界准入及 child 负证据。`Graph` 增加恢复互斥分支，
  family/planner/reducer 仍各自只有一条推进路径，没有新的 task、可变状态写或 import cycle。
- 命中的 statement/near-clone 主要是不同 typed collection、graph-input 与 publication envelope 的准入形状；后者额外拥有
  activation/receipt provenance。保留各自异常边界，不用 generic validator、动态类型派发或转发 helper 隐藏这些差异。
- `restore_checkpoint` 只重建读证据；`_resolved_config` 只做 state/frame 共同需要的精确 capability join；
  `_validate_child_run_evidence` 从编译 topology 和 state 推导集合，不维护第二份生命周期。
- 相对保留用户改动后的基线：definitions 1091→1107、dataclass 425→433、fields 1076→1105；logical clone 50→50、
  statement clone 100→123、near clone 57→58；thin helper 0→0、attribute write 107→107、task creation 16→16、import cycle 0→0。
  low-usage 218→216、linear private chain 40→39 的下降同时锁紧。指标是复审雷达，不作为设计正确性的裁决。
- 二轮把 result/continuation 从 frame 与 task disposition owner 中迁出，是为了让 continuation 直接保存窄的
  `GraphCommit`，而非用 `object` token、反射或循环导入规避类型约束；没有增加 dataclass、runner、task 或可变状态写。
  Config frame 的校验规则仍只在 envelope owner；decoder 只判断与绑定 codec 是否匹配，不重复格式类型准入。
  single evidence commitment 的 canonical metadata 明确编码 Config 缺席，不从当前 state 镜像历史 Config。
- 二轮实测 type definitions 688→687（删除 snapshot alias）、fields 1104→1105（原 commit）、decision points
  3475→3477、semantic nodes 71401→71552；max CC 48、max cognitive 58、call-chain depth 17、clone 数均不变。
  import edges 682→689、runtime module pairs 419→422 是 owner 分离后的依赖重排；import cycle 仍为 0，health 仍全为 0。

#### 首轮门禁记录（历史，不代表二轮验收）

- Kernel `make check`：**通过**。Ruff/format、strict Pyright（0 errors / 0 warnings）、architecture/complexity ratchet
  及 health、**2499 tests**、**100% 行与分支覆盖率**、sdist/wheel build、Twine 全部通过。
- 仓库根 `pre-commit run --all-files`：**通过**。首次运行因沙箱只读阻挡根仓库文件/构建缓存访问，授权重跑后全部
  hooks 通过，包括仓库要求的 Rust/Cloudflare 检查；未修改这些项目的实现或基线。
- `--all-files` 不含未跟踪文件，因此对所有 Kernel 新文件另跑根目录 `pre-commit run --files ...`：**通过**。
  其中固定测试 SHA 曾触发 secrets 误报，改成从测试 payload 计算 digest；没有新增 allowlist 或修改 secrets baseline。
- `git diff --check`：**通过**；本阶段没有修改 Kernel 之外的文件。保留用户原有 staged/unstaged 内容，没有 stage 或 commit。
- P1 当阶段恢复验证使用内存测试 Port 和新 Graph 对象，**不冒称跨进程、真实后端或 Agent 已打通**。P1 当时
  尚未实现 `agent.py` 的 load/Config/authority/commit reconciliation；独立进程与组合故障后来由 P2、P3 闭环。

#### 二轮回归与最终门禁

- 两项缺口先复现，再补回归；首批 8 例中 6 fail / 2 pass，修复后全部通过。没有将首轮全绿当作契约已完整的依据。
- commit 绑定覆盖初次 durable/cold recovery、省略/显式 `None`/原对象、多次交接、同字段异对象、不同
  codec identity/version/实现、不同 writer、transient 替换、custom commit、Store 不可用和恢复、nested resume partial
  与 fence partial、completed/failed/aborted 结果，以及重新读取权威 checkpoint 后的合法换绑。
- Config cursor 覆盖 graph input/publication × root/child × pending/completed 的删除，以及构造时各字段篡改、
  无 Config 时注入、receipt 中删除后拒绝安装、历史上合法的无 Config、Observe 前后恢复、二进制 payload 和 Unicode
  metadata 的固定摘要向量。合法摘要但不匹配绑定 codec 的 frame 也必须在该 frame 解码、节点调用和写入前拒绝。
- 旧测试与示例从开始就绑定同一个 commit port，通过该 port 的故障状态验证恢复；不为测试保留中途替换 callback
  或改走成功回调的生产兼容分支。`partial_commit_recovery` 示例不再误称其内存故障注入器是 durable backend。
- 首次本轮全量执行：**2558 tests 全通过**，但覆盖门禁发现“合法摘要、错误 codec”的拒绝路径缺测；补齐四个
  graph input/publication × identity/version 场景，没有降低 coverage 门槛。定向持久化套件现为 **295 tests 通过**，
  `execution/persistence.py` 行与分支覆盖 **100%**。
- 最终 Kernel `make check`：**通过**。Ruff/format、strict Pyright（0 errors / 0 warnings）、architecture、
  complexity ratchet 与 health、**2562 tests**、**100% 行与分支覆盖率**、sdist/wheel build 和 Twine 全部通过。
  全量覆盖包含 **13075 statements、4146 branches，零遗漏**；本轮相对首轮增加 63 个确定性边界场景。
- 仓库根 `pre-commit run --all-files`：**全部通过**；对未被 `--all-files` 覆盖的 **16 个 Kernel 新文件**另跑
  `pre-commit run --files ...`：**通过**。没有 skip 适用 hook、修改 secrets baseline、增加 allowlist 或放宽门禁。
- `git diff --check`：**通过**。只修改 Kernel 文件，保留用户原有 staged/unstaged 和其它目录的改动；没有 stage、
  commit、push 或创建分支，没有修改 Rust/Cloudflare 实现。
- 本轮验证日志：`/tmp/mote-kernel-p1-review-check.log`、
  `/tmp/mote-kernel-p1-review-persistence-coverage.log`、`/tmp/mote-kernel-p1-review-root-precommit.log`、
  `/tmp/mote-kernel-p1-review-new-files-precommit.log`。命令和结果以本节最终记录为准，首轮记录保留为历史。
- 两项二轮意见均已落实；Config 更新仍只由 Observe 消费，Runtime 工具执行对账边界不变。当前仍只交付 P1
  execution seam，不宣称 Agent 装配、真实后端或跨进程验收完成；这些分别留在 P2/P3，未启动。
- 最终复审未发现新的 P1 阻断；durable commit capability、完整 frame digest、child 负证据和 completed ledger
  均有唯一 owner，恢复仍汇入既有 `Graph.run()`、compiler/planner/routing/reducer/family-driver 调用链。按用户
  “审核通过则提交”的明确授权，将 P1 标记为已验收；该授权不启动 P2。

**P1 验收时停在 P2 前；后续用户已明确授权 P2，以下记录承接该授权。**

### P2 / 统一 Agent 接线 / 已验收

#### 开工基线与授权

- 用户明确表示“review通过，继续p2开发”；本次授权仅覆盖 P2，不跨过 P2 的评审停点。
- Kernel 开工基线为 `e05d3cd`，Kernel 工作树干净；其它项目的用户改动保留不动。
- 开工重新执行 Kernel `make check`：2562 tests 全通过，100% 行与分支覆盖，类型、架构、复杂度、构建和
  Twine 均通过。日志：`/tmp/mote-kernel-p2-baseline-check.log`。本阶段的失败不得归咎于原有基线。

#### 开工设计与不变量

- `Agent` 只持有不可变装配参数。每次调用都 acquire → load → 精确 Config 解析 → Graph 装配/准入 →
  `Graph.run` → 业务结果投影 → release；不缓存运行 state、continuation、Config latest 或执行权限。
- 请求明确区分创建和继续：创建已有 run 是冲突，继续不存在的 run 是不存在错误；不静默丢弃新输入、自动
  rebase 或创建替代 run。run identity 由上层明确给出；已有终态通过继续请求回放，不负责业务续轮。
- 同一 `(agent_id, run_id)` 的并发准入由外部 authority Port 排他仲裁；每次读、提交和对账都携带同一权限。
  不在 Agent 内另建锁、busy 状态、租约时钟或 fencing 存储。取得新权限后才允许 Graph 使用既有 fence 转换。
- 后端无关 Port 与权限值放在一个根级 `persistence.py` 中；已有 `execution/persistence.py` 继续独占
  Graph checkpoint、完整 frame 编码和精确 receipt 准入。新增模块不是第二个 State、runner 或后端包树。
- 初始 Config 只读取装配指定的 immutable key；恢复读取 state/frame 引用的全部精确历史 Config。
  Config store/resolver 作为一个明确的可选装配组，启用时二者都必须存在。Agent 不写 Config、不消费更新，
  不为历史无 Config 的 frame 填值；更新仍只由 Observe 消费并持久化。
- family 第一次 load 返回一致的全部已有记录。若 current pending nested activation 尚无记录，由 Graph
  的同一 child-lineage 规则推导精确查询坐标，再在同一有效权限下重读并取得 `UncreatedGraphRun` 负证据。
  两次读的已有 state/value 必须完全相同；不能由 Agent 猜不存在，也不能要求后端复制 compiler/topology。
  已结算 child 的缺失直接拒绝，不能变成创建。
- `Applied` 仍交给 P1 的唯一 exact-ack owner 校验；`Unknown` 只对账同一不可变提交一次。
  `NotApplied` 才允许在显式有限次数内重发原对象；每次发送仍由 Port 原子检查权限。最终 Unknown、CAS
  冲突、错误 receipt 或权限失效直接停止，不重跑节点、不重新编码、不基于旧 state 写 cleanup transition。
- acquire/release 使用已有 cancellation-safe owner-task join；Graph 任务由 Graph 自己收敛后才 release。
  释放失败不能覆盖原始错误。Agent 不向调用者交付 state/continuation，也不把部分提交变成第二恢复入口。

#### 一次性实现与验证范围

1. 增加必要的 typed Port/权限/结果契约，补齐 checkpoint 的 Config 引用和 child 查询投影；复用原有准入规则。
2. 在 `agent.py` 完成请求/业务投影、装配、创建/继续、提交对账与权限退出，保持同一 Graph 执行链。
3. 增加两种测试适配器及确定性失败注入，覆盖 CAS、失效权限、三态对账、历史 Config、父子负证据、
   interrupt、终态、并发和各 await 边界的取消；新增行为当阶段达到全部覆盖门禁。
4. 同步迁移示例和中英文入口说明，完成 `make check` 与根 pre-commit，记录实际调用链、实测及限制。
   P3 的独立进程组合故障验收不冒充已完成；P2 完成后停止等待 code review。

#### 已落实的 owner 与调用链

- `agent.py` 的 frozen `Agent` 只保存装配能力；`AgentStart` 明确 create-only，`AgentResume` 明确 existing-only。
  `AgentAnswer` 保留精确 interrupt 问题与 typed 回答；completed/failed/interrupted/aborted 只投影业务结果。
  completed output 不携带 activation Config，任何结果或异常都不提供 Agent state/continuation 接口。
- 根级 `persistence.py` 定义 `AgentRunKey`、opaque `ExecutionAuthority`、`AuthorityPort`、泛型
  `PersistencePort`、明确不存在证据和提交三态；不定义 wire、数据库、Container、调度或第二份 State。
  每次读取、写入和对账都携带本次 grant；同 key 的并发准入完全由外部 Port 排他仲裁。
- `AgentConfig` 只组合既有 snapshot store/resolver 与可选的精确初始 key。创建只读取该 key；恢复按 checkpoint
  引用逐个读取精确 revision/digest 并解析能力。Agent 不消费更新、不保存 Config，不对合法的无 Config 历史补值。
- `GraphCheckpoint.config_cursors` 只投影原有 state/frame 中的引用；`Graph.recovery_child_reads` 通过同一个
  compiler/lineage owner 推导负读取坐标。二次 load 后由 `GraphCheckpoint.admit_child_reads` 检查已有事实完全一致、
  负证据精确覆盖查询；没有后端 topology 副本、latest 查询或“没读到就是没创建”的回退。
- `_AuthorizedGraphWriter` 将同一请求交给 Port；Unknown 只对账原对象一次，NotApplied 才能在显式有限次数内重发。
  `DurableGraphCommit` 继续独占编码及 exact receipt 准入，Agent 不重复 reduce、编码或校验 candidate 的另一套规则。
- `confirm_transition` 用 typed `GraphCommitError` 区分提交来源失败。family owner 收敛全部 worker 后按该来源决定
  是否允许 durable cleanup；构造、child handoff、fence、abort 中的提交失败同样不能被普通错误吞掉。已有发出提交
  先完成确认/对账，发现失败后不再合成祖先或 sibling 的 cleanup write；Graph 边界再还原原始提交异常。
- acquire/release 复用 `wait_for_owner_task`。caller 反复取消时仍等待权限任务完成；取得 grant 后必定进入释放边界。
  Graph 自己收敛 session/child/task，Agent 不另建 runner 或任务回收器，release 错误不覆盖原执行错误。

```text
Start
  → admit business request → acquire exact run authority → load NeverCreated
  → optional exact initial Config → assemble Graph → codec-bound durable Graph.run
  → project business result → release authority after Graph cleanup

Resume / terminal replay
  → admit exact answers → acquire new authority → load complete family
  → resolve all referenced Config snapshots → assemble Graph → Graph-owned child read projection
  → if needed: consistent reread + exact negative-evidence admission
  → GraphRecovery + same Graph.run → project business result → release

One transition
  → pure reducer + complete immutable frame write set → encode once
  → authority-constrained atomic commit → optional same-request reconciliation / bounded NotApplied retry
  → exact receipt admission → install authoritative state/frame
```

#### 实测发现与一次性迁移

1. 开工基线没有失败。新增失败注入暴露了一个真实提交分类缺口：普通 commit 异常进入 worker 普通失败分支后，
   会从未确认的内存 state 再写 fence。修正位于 commit owner 和 family fan-in，不在 Agent 中识别异常字符串。
2. 收尾复审继续发现嵌套清理缺口：child 的 fence/abort 提交失败可能被旧的“保留第一个普通错误”分支吞掉，
   使祖先再次发 cleanup。先建立复现，再令提交来源在 worker、构造和 handoff 清理中保持 typed 传播。
   普通 cleanup/admission 错误仍保留原始异常优先级；只有不能确认的提交阻断后续 durable 操作。
3. 删除 `_commit_origin_cancellation` mutable marker、mark/consume 方法、owner-task join 的取消回调参数，
   以及 child drive 把取消异常作为返回值的旁路。所有提交异常和取消统一经过同一个 typed 来源边界。
4. 删除 session consumer 中重复的普通失败 fence；由收敛后的 family fan-in 管理清理。保留并复用并行 G5 的
   `_start_fresh_owner` 收敛，不重建 root/child 启动事务；本阶段在同一 owner 上闭环提交失败传播。
5. Config capability validator 原地更名供 Agent 复用，child evidence validator 原地扩展成查询投影；旧名不留 alias。
   既有 compiler、planner、routing、reducer、typed frame 和 continuation provenance 不增加第二执行路径。
6. 测试同步迁移错误来源契约，不为旧断言添加生产 wrapper。补测中误用 frontier 字段以及把“child 已 abort”
   误认为“root 已 abort”的断言均按既有 State/frontier owner 修正；child abort 仍由父节点投影为 failure，
   不为了测试改写领域语义。

#### P2 复审意见与契约闭环

复审指出的五类证据问题均成立，且都属于既有 owner 应闭合的 Kernel 契约；没有以首轮门禁全绿替代设计判断。
最终迁移没有增加 manifest、镜像 State、第二恢复路径、通用 validator 或后端协议字段：

1. graph input/publication 的唯一 `GraphEvidenceCommitment` 改由 execution commit owner 从完整 canonical evidence
   生成，统一绑定 scope/run、activation、descriptor、birth `GraphCommitKey`、codec identity/version、Config cursor
   （含缺席）和 payload；publication 额外绑定真实 execution provenance。payload 交换后即使重建合法 frame，
   也不能改变 value fact 的归属。
2. `GraphRunState.settled_publications` 成为 publication birth/settlement 的唯一权威账本；每项保存 activation
   reference、真实 commit revision、`GraphExecutionToken` 和 evidence commitment。运行 continuation、持久写入与
   cold recovery 都必须与该项精确关联，不接受形式合法但来自其它 revision/attempt 的 provenance。
3. `DurableGraphCommit` 在调用 writer 前保存独立的完整 admitted baseline。writer 返回后，原请求与 receipt 都和
   baseline 比较；即使外部实现通过 `object.__setattr__` 同步修改 frozen 请求及其嵌套对象，也不能重定义 exact ack。
4. Agent request/result、authority、Graph commit/receipt、checkpoint、frame、coordinate、provenance、Config cursor、
   `GraphRunState` 与 `_GraphValues` 均在各自外部 typed boundary 完整重新准入。exact class 但缺字段、字段类型错误、
   subclass 或嵌套伪造统一进入既有 typed contract error，不泄漏原始 `AttributeError`。
5. Config revision/digest 不变量由 `GraphConfigCursor`/State owner 一次性闭合：successor revision 必须有 digest，
   frame cursor 必须属于同一 definition/version 且不超前。Agent 不复制 Config 检查。这里的 Config snapshot cursor
   与已删除的 Observe 调用方 cursor 是不同概念；本次没有恢复 Observe request/node cursor。

复审建议中的“unknown publication node”由既有 compiled snapshot admission 更早且唯一地拒绝；因此删除了
`restore_checkpoint` 中永远晚于该 owner 的重复分支，而不是再增加一份 topology 检查。相同地，删除 successor
Config digest、非 Start graph-input write 及 settlement 二次排序等已被前置 canonical admission 证明不可达的检查。
其余缺口均保留在各自领域 owner，没有用覆盖率驱动薄转发或状态机碎片化。

#### 边界验证范围

| 边界 | 本阶段证明 |
| --- | --- |
| 创建/继续身份 | 新建冲突、继续不存在、tombstone、不可用、不同 Agent/run 命名空间、终态只读回放 |
| 权限 | 同 key 并发拒绝、同实例/新实例、异 key 独立、错 key/损坏 grant、逐次提交失权、释放不覆盖主错误 |
| 完整提交 | linear graph 的 revision 0–6 全部覆盖：NotApplied、Unknown→Applied、Unknown→NotApplied、最终 Unknown 的已写/未写、失权 |
| 幂等与失败 | 同 key 同完整内容重放、不同内容冲突、错误 receipt、非法 outcome、有限重试耗尽、原请求对象不变、节点不因重试重跑 |
| Config | 初始精确 key、恢复全部历史 cursor、digest/definition/revision 错误、缺能力/缺快照、无 latest 回退、Agent 从不 save |
| Child | Start 未提交的负证据、深层 child、已结算记录丢失、两次读取 state/value 分裂、错误/重复/缺失负证据、非法 checkpoint variant |
| Interrupt | 精确问题身份、答案重放/错误 scope、部分 child resume 已提交后从新权威读取继续，不使用旧 continuation |
| 取消与清理 | acquire/load/Config/reconcile/release、caller/node/commit 来源、反复取消、join 后释放、构造/handoff/fence/abort 的失败传播 |
| 未知 cleanup 后恢复 | fence/abort 已写与未写都停止后续清理；新 Agent 重读后区分仍可执行 child 与已 abort child，不抹掉 durable 事实 |
| 预算 | 执行预算耗尽保留已确认 publication；新 Agent 用足够预算恢复，不重跑已提交 producer |
| 类型与架构 | 泛型 request/store 不可交叉、Agent 无 state/commit override、冻结接线、唯一 child lookup/commit 来源、无第二 runner |
| canonical evidence | sibling payload 交换、coordinate/descriptor/birth/codec/Config/provenance 任一篡改、State 与 record commitment 分裂均在执行前拒绝 |
| typed re-admission | exact-but-incomplete、subclass、嵌套缺字段、bool/int 混淆、非 canonical 集合顺序及重复坐标统一映射到 owner 契约错误 |
| writer mutation | scope/state/key/frame 的无效原地 mutation 与保持请求内部一致的原地 mutation均不能越过独立 baseline |

snapshot 与 receipt-journal 两种测试存储表示执行同一套 Agent 行为；前者保留快照，后者从完整 receipt 序列重建。
`durable_agent_import` 直接复用 import 的 DTO、拓扑与 codec，通过注入 Ports 演示新 Agent 的 interrupt 恢复，
并在两种适配器上验证；中英文 README、architecture 与示例入口同时迁移，不把伪后端写进生产示例。

独立进程基本证明已在 P2 完成：capture 子进程写入完整 publication 后、返回 acknowledgement 前 `os._exit(23)`，
另一个进程以新 Agent/Graph/codec 恢复，再由第三个进程回放终态。测试核对不同 PID、producer 只执行一次、恢复输出
精确相同和权限 generation 前进。此证明使用 test-only 顺序进程文件适配器，不证明真实数据库、断电/fsync、并发租约
服务或工具副作用 exactly-once；它在 P2 当时只是一项基本证明，后续 P3 组合故障验收记录见下文。

#### 复杂度复核结论

- `Agent._run_authorized` 保留完整权威读取到 Graph 准入的线性链路；创建/继续的分支是不同前置条件，不拆成
  薄转发或宽 context。`_recover_configs` 只管理本次调用的精确快照集合，不是 Config cache 或新的 Config owner。
- `_AuthorizedGraphWriter` 是绑定 Port、权限和尝试上限的窄不可变 callable；它处理三态与原请求身份，
  不重新实现编码、receipt 校验、reducer 或通用重试框架。
- `AgentResume` 的 tuple/type 准入命中 statement-clone 雷达，但它与 State 写集、Config、Failover 有不同输入和
  异常边界，保留显式本地检查，不为降低指标增加 generic validator。外部 Adapter 抛出的 typed Port 异常被
  “生产内无引用”雷达命中，不通过虚假内部调用消除这些合法外部契约。
- family fan-in 保留集中 join、提交来源判别、清理顺序；不机械拆分状态机。复审整改后按整合工作树精确锁定 ratchet：
  `top_level_definitions=1156`、`type_definitions=721`、`semantic_nodes=75445`、`attribute_writes=105`、
  `exception_handlers=223`、`internal_call_edges=1559`。最大圈/认知复杂度仍为 `48/58`，最大 nesting 为 `6`，
  最大调用链深度为 `19`；stateful async hotspot 仍为 `10`。
- 全部 zero-debt health 指标仍为零：无 import cycle、仅测试使用的私有生产定义、未使用私有定义、未读取私有字段、
  未消费 async call、无 owner 的 coroutine handle 或孤儿 task handle。没有 health 豁免或 ratchet 浮动余量。
- 精确指标包含已存在的并行 G5 fresh-owner 收敛；其文档、已暂存变更及其它项目改动均保留，不声称这些是 P2 独立改动。

#### 首轮门禁（P2 复审前历史）

以下结果是收到 evidence 复审意见前的历史记录，不能作为本轮整改验收证据；再次评审只采用后续最新门禁记录。

- Kernel `make check` 全通过：Ruff/format、strict Pyright（0 errors）、complexity ratchet/semantic index
  （22 tests）、zero-debt health、全部架构和正负类型 fixture、完整行为测试、sdist/wheel 构建与 Twine 检查。
  完整测试 **2910 passed**；生产代码 **100% 行与分支覆盖**（13374 statements、4254 branches，无遗漏）。
  日志：`/tmp/mote-kernel-p2-final-check.log`。
- 最终提交/清理补测 **124 passed**，覆盖全部七个 revision 的三态结果与失权，以及未知 child fence/abort
  已写/未写后的权威恢复。日志：`/tmp/mote-kernel-p2-final-boundaries.log`。
- monorepo 根目录 `pre-commit run --all-files` 全通过，包含 Kernel complexity、仓库基础检查、secret 检查与既有
  Rust/Cloudflare 静态 hooks；只执行门禁，不修改这些项目的实现。
  日志：`/tmp/mote-kernel-p2-root-precommit.log`。
- `--all-files` 不覆盖 untracked 文件，因此额外从根目录执行 `pre-commit run --files ...`，显式包含全部新增
  Agent、Port、示例、测试和负类型 fixture；所有适用 hook 通过。
  日志：`/tmp/mote-kernel-p2-new-files-precommit.log`。
- `git diff --check` 通过。没有为通过门禁增加生产兼容 API、放宽 health、添加 coverage 排除或第二条执行路径。
- P2 仅完成 Kernel 范围实现与本地验证，尚未获得用户验收。未暂存或提交 P2 变更；既有并行暂存项保持不动。
  未实现任何 Rust、CF 或其它具体持久化后端，不承担 Runtime 工具执行对账，也不处理 END 后的新任务调度。

#### 复审整改后门禁（再次评审依据）

- Kernel `make check` 全通过：Ruff/format、strict Pyright（0 errors / 0 warnings）、complexity、zero-debt health、
  架构和正负类型 fixture、完整行为测试、sdist/wheel 构建与 Twine 检查均通过。
- 完整测试 **2994 passed**；生产代码 **100% 行与分支覆盖**（13772 statements、4370 branches，无遗漏）。
- complexity **22 passed**；最大圈复杂度 `48`、最大认知复杂度 `58`、最大调用链深度 `19`，zero-debt health PASS。
- monorepo 根目录 `pre-commit run --all-files` 全通过；由于该命令不覆盖 untracked 文件，另对 Kernel 全部
  **21 个 untracked 文件**执行定向 pre-commit，所有适用 hook 全通过。
- 最终计划文档的仓库级定向 pre-commit 与 `git diff --check` 均通过。
- 本记录只证明 P2 复审整改后的实现与门禁状态；后续验收由用户独立裁决。

#### P2 验收

- 2026-09-11，用户明确“P2 已通过，开始 P3”，并要求 P3 同时复审、补齐 P1/P2 仍有价值的边界条件。
- P2 据此标记为已验收；该授权只启动 P3，不跨过 P3 的评审停点。

### P3 / 2026-09-11 / 跨进程与组合故障验收 / 待评审

- 复用 P1/P2 的唯一 commit、State、Config、routing、family 与 Agent owner；测试适配器只提供隔离进程和故障注入，
  不进入生产 API，也不形成第二恢复路径。
- 先审计现有边界矩阵，已有精确证明不机械复制；新增测试优先覆盖边界交叉、故障时序和多级关联。
- Runtime 仍唯一负责工具执行记录与对账；Kernel 仅验证类型化工具结果进入 Graph 后的持久化边界，不查询工具账本。
- P3 完成实现、全量门禁和实测记录后停止，等待用户 code review；未验收前不进入 P4。

#### 边界审计与迁移范围

- P1/P2 的单项契约、恶意 typed re-admission、全部 commit revision 三态、Config cursor、publication settlement、
  continuation binding、family cleanup 和取消矩阵已经完整参数化，不为增加测试数量机械复制同一证明。
- 将 P2 的单一 linear 子进程脚本原地迁移为一个统一 process-fault harness；仍只通过公开 `Agent`、`Graph`、
  `PersistencePort`、`AuthorityPort`、Config Port 和既有 `invoke_typed` 运行。没有新增生产 API、runner、状态 owner
  或后端 selector。
- P3 只修改 `tests/agent/subprocess_worker.py`、`tests/agent/test_process_recovery.py` 和本计划文档；生产代码零修改。
  当前其它 Observe/session、Failover 和 Runtime 工作树改动均由用户并行拥有，本阶段未修改或回滚。
- test-only journal 每次跨 pickle 边界后先把容器收窄为 `tuple[object, ...]`，再逐项要求 exact
  `GraphPersistenceCommit` 并调用其 owner admission。它没有复制 checkpoint、State 或 evidence 校验。
- P1/P2/P3 持久化相关测试族合并执行 **704 passed**；新增的 16 个 P3 case 不是替代既有单边界测试，
  而是证明真实进程退出时多个边界交叉后仍由同一 owner 得出结果。

#### 跨进程故障矩阵实测

| 注入点 | 期望 | 实测 |
| --- | --- | --- |
| linear `first` publication 原子写前退出 | 新进程只重做未提交 `first`，再执行 `second` | 输出精确；`first` 共调用两次，不存在伪 durable fact |
| linear `first` publication 原子写后、ack 前退出 | 新进程不重做 `first` | `first` 只调用一次；从持久 publication 执行 `second` |
| 并发 sibling 中 `left` publication 写前退出 | 只恢复未确认 frontier；Join 等待两侧 | `left` 重做，`right`/Join 各一次，输出按 descriptor 关联 |
| 并发 sibling 中 `left` publication 写后退出 | 已确认 sibling 不重做 | `left`、`right`、Join 各一次，终态 replay 无调用和写入 |
| causal loop 第二轮 settlement 写前退出 | 恢复上一轮 publication，重做当前 activation | 只重复输入为 `1` 的第二轮；随后按同一 routing 进入 fan-out/Join |
| causal loop 第二轮 settlement 写后、routing 前退出 | 从已结算 activation 继续 routing | 第二轮不重做；两轮历史 publication 未被“最新值”覆盖 |
| 两级 nested family 的 middle→leaf settlement 写前/写后退出 | child、parent 边界按 scoped run 精确恢复 | 两种时序均不重做已完成 leaf；middle/root 各自只结算一次 |
| nested Observe 把 Config 1 推进到 2 后退出 | 新 Agent 精确加载历史 1、2，不采用可用的 latest 9 | recover/replay 均只 load 1、2；后续 child/parent state 最终引用 revision 2 |
| 删除 nested history 的 Config 2 | Config owner 在节点和 durable write 前 fail closed | 新进程失败；journal、调用日志和结果文件均不改变 |
| child StartGraphRun 原子写前退出 | 完整 family reread给出同一 child 的 never-created 负证据后才创建 | child 只在恢复进程执行；带一次明确 child read |
| child StartGraphRun 原子写后退出 | 已存在 child 不产生负证据、不重复创建 | 新进程沿已有 child state 完成；业务节点仍各执行一次 |
| 两个 interrupt child 分三次新进程回答 | 每个精确问题只消费一次，partial 后只保留另一问题 | left/right 各恢复一次；第四个新进程只读终态，无调用或写入 |
| journal 非 pickle / tuple 内错误 record | 在 Graph assembly、decode、node 和 write 前拒绝 | 两种 corruption 均 fail closed，原 bytes 和调用记录不变 |
| live 旧 owner 节点返回前，新进程取得 generation 2 | 旧 generation 1 在提交前被 fencing 拒绝 | successor 完成并持久化；旧进程记录 typed rejection，journal 不被覆盖 |
| typed Runtime invocation 后、Graph publication 前进程退出 | Kernel 不推断工具执行状态，恢复后仍把调用交给 Runtime | 复用 `InvocationTypeContract`/`invoke_typed`；Kernel 只 load/commit Graph fact，从不 reconcile 或查询工具账本；终态 replay 不再调用 |

每个完成场景都再启动一个全新 Agent 进程做终态 replay，并逐 byte 核对 journal 不变；执行过业务节点的进程 PID
必须不同，authority generation 必须按调用次数单调前进。写前/写后退出都由 `os._exit` 实际终止进程，不用异常模拟。

#### Port 行为断言与限制

- 外部 Authority Port 必须在每次 load/commit/reconcile 原子校验当前 grant；旧 grant release 不得覆盖 successor。
- 外部 Persistence Port 必须提供同一 authority 下完整一致的 family read，原子约束 absent/revision、完整写集与 receipt，
  并对相同 key/content 精确重放、不同内容冲突、未知结果返回 `CommitUnknown`。Kernel 不接受 adapter 猜测 absence。
- Config Store 必须按 exact key 提供不可变 snapshot 并保留历史；Resolver 必须返回同一 snapshot 的完整 Config。
- 文件 journal、generation 和信号只属于顺序可控的测试适配器。P3 不宣称真实数据库事务、断电/fsync、网络分区、
  多主租约服务或具体 Rust/Cloudflare 适配器已经通过；这些外部实现必须用上述 Port 断言自行验收。
- Runtime 工具副作用 exactly-once 仍不属于 Kernel。测试只证明“没有 Graph publication 时 Kernel 不伪造工具事实，
  再次抵达工具调用仍交给 Runtime”；工具是否已经执行、如何查询 receipt 和是否重试只由 Runtime 契约决定。

#### P3 门禁

- 新增跨进程组合矩阵 **16 passed**；P1/P2/P3 持久化相关测试族 **704 passed**。
- Kernel `make check` 全通过：Ruff/format、strict Pyright（0 errors / 0 warnings）、architecture、正负类型 fixture、
  complexity ratchet、zero-debt health、全量行为测试、sdist/wheel 构建与 Twine 均通过。
- 完整测试 **3009 passed**；生产代码 **100% 行与分支覆盖**（13772 statements、4370 branches，无遗漏）。
- complexity **22 passed**；结构 ratchet 保持 `top_level_definitions=1156`、`type_definitions=721`、
  `semantic_nodes=75445`、`attribute_writes=105`、`exception_handlers=223`、`internal_call_edges=1559`；最大圈/认知
  复杂度 `48/58`、最大 nesting `6`、最大调用链深度 `19`，zero-debt health PASS。测试 harness 未进入生产指标。
- monorepo 根目录 `pre-commit run --all-files` 在默认沙箱首次仅因 Kernel 之外路径只读而失败；获得写权限后原命令
  全部通过，包括 Kernel complexity、Rust/Local Execution、Cloudflare 静态检查和 detect-secrets。这里只记录门禁，
  未修改或验收这些项目的实现。日志：`/tmp/mote-kernel-p3-root-precommit.log`。
- 最终 `make check` 日志：`/tmp/mote-kernel-p3-final-check.log`。对本阶段三份文件执行的仓库级定向 pre-commit
  与最终 `git diff --check` 均通过。

**P3 在此停止等待 code review；只有用户 review 通过并明确授权，才进入 P4。**

#### P3 验收

- 2026-09-11，用户在 P3 实现、故障矩阵和完整门禁记录后明确要求继续完成 P4，并授权完成后提交。
- P3 据此标记为已验收；该授权启动 P4 并允许提交本项变更，不授权修改或代为提交用户并行的 Observe/session、
  Failover、Runtime、Rust 或 Cloudflare 工作树。

### P4 / 2026-09-11 / 全链路复核与最终交接 / 待评审

- 从 `Agent.run()` 入口按 acquire → load → checkpoint/Config admission → Graph assembly → child reread →
  `Graph.run()` → durable commit/reconcile → business projection → task convergence → authority release 顺序复核；
  每一步继续由 P1/P2 已有唯一 owner 承担，没有新增生产抽象、状态、缓存、runner 或恢复路径。
- 复查中英文 README、架构说明和全部 Graph 示例。进程内 state/continuation 示例已经明确不等于持久冷恢复；
  `durable_agent_import` 只演示后端无关 Port 装配，不选择数据库、协议或 Runtime 工具对账路径。
- 未使用类型、重复 codec/validation、兼容入口、隐藏 backend selector/latest fallback 与具体存储依赖审计未发现
  待删除生产路径。P4 不以复核名义重写已经闭合的 P1/P2 调用链，也不机械复制已有单边界测试。
- 发现并修正的唯一过时事实是中英文架构文档仍把跨进程测试描述成 P3 之前的单场景证明；统一更新为已经完成的
  组合故障矩阵，同时继续明确测试适配器不代表真实生产后端验收。

#### P4 最终证据与限制

- 本阶段实际修改仅为 `docs/architecture.md`、`docs/architecture.zh-CN.md` 和本计划；连同 P3 尚未提交的
  `tests/agent/subprocess_worker.py`、`tests/agent/test_process_recovery.py` 构成本次提交的完整五文件集合。
  P4 没有生产代码变更，也未修改、暂存或提交用户并行的 Observe/session、Failover、Runtime、Rust、Cloudflare 文件。
- P1/P2 单边界测试继续覆盖 exact typed re-admission、完整 evidence commitment、Config 历史、continuation commit
  binding、child 负证据、全部 commit outcome、权限/取消/family cleanup 与错误优先级；P3 用 16 个跨进程 case
  补齐写前/写后和组合时序。P4 再次运行该 16-case 矩阵，结果 **16 passed**。
- Kernel `make check` 最终全通过：Ruff、format、strict Pyright（0 errors / 0 warnings）、architecture、正负类型 fixture、
  complexity ratchet、zero-debt health、全量测试、100% 覆盖率、sdist/wheel 和 Twine 均通过。完整测试
  **3009 passed**；生产代码覆盖 **13772 statements / 4370 branches，均无遗漏**。日志：
  `/tmp/mote-kernel-p4-final-check.log`。
- complexity **22 passed**；结构 ratchet 仍为 `top_level_definitions=1156`、`type_definitions=721`、
  `semantic_nodes=75445`、`attribute_writes=105`、`exception_handlers=223`、`internal_call_edges=1559`；最大圈/认知
  复杂度 `48/58`、最大 nesting `6`、最大调用链深度 `19`，zero-debt health PASS。热点均按真实调用链复核，
  没有新增薄转发、豁免或排除项。
- monorepo 根目录 `pre-commit run --all-files` 全通过，包含 Kernel、Rust/Local Execution、Cloudflare 静态检查与
  detect-secrets；这里只记录仓库门禁，不认领或验收 Kernel 外实现。最终五文件定向 pre-commit 与
  `git diff --check` 也通过。
- 已知边界保持不变：Kernel 只冻结后端无关 Port 和恢复语义，不证明任何具体数据库的 fsync、网络分区、租约服务
  或部署适配器；Runtime 独占工具执行记录与对账；ReAct END 后的新任务由上层驱动。这些不是 Kernel P4 遗留债务。
- P4 至此为待评审状态。用户已明确授权创建本次 Git commit；commit 不等于最终验收，最终 review 前不把 P4 标为
  已验收，也不进入新的持久化阶段。
