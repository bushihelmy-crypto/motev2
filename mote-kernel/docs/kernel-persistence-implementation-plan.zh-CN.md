# Kernel 持久化闭环实施计划

## 0. 状态与评审约定

- 日期：2026-09-10。
- 调研提交基线：`e4ef968`；工作树另有用户正在进行的 routing/recovery 治理改动。
- 当前阶段：**P1 已验收；停在 P2 前等待用户明确授权，状态与验收记录见第 11 节。**
- 范围：只修改 `mote-kernel/`。不实现或修改 Rust、Cloudflare、数据库、网络传输和部署代码。
- 恢复装配唯一入口：`src/mote_kernel/agent.py`。
- 执行唯一入口：`mote_kernel.execution.Graph`；Agent 不实现 runner、scheduler 或 reducer。
- 本文件是本项持久化工作的阶段进度与验收记录 owner。Graph 主线治理总账只链接本文件，不复制进度。

每个 P 都执行“设计复核 → 一次性迁移 → 测试与门禁 → 记录 → 等待 code review”。
**未经用户确认，不开始下一个 P。** 阶段代码写完、测试通过和用户验收是三个不同状态，不能互相代替。
P0 之后按用户的“继续吧”进入 P1；该授权不跨过 P1 完成后的评审停点。

状态使用：`未开始`、`实施中`、`待评审`、`已验收`。发现阻塞或评审修改意见时，留在当前阶段处理。
任何阶段不得提交 Git commit、push、覆盖用户改动或放宽门禁来掩盖设计问题。

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
| P2 Agent 统一装配 | 未开始 | P1 review 通过后开始 |
| P3 组合故障验收 | 未开始 | P2 review 通过后开始 |
| P4 最终复核交接 | 未开始 | P3 review 通过后开始 |

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
- frame 只保留一个完整性摘要，覆盖 codec identity/version、payload 和 Config cursor（包括明确的无 Config）。
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
   `EncodedFrame.frame_digest` 统一覆盖 codec identity/version、payload 和 Config cursor（含缺席）。Config 只保存
   cursor，能力对象不进入 codec。resume 保留其既有 Config 继承和异常边界。
2. `prepare_transition` 仍由 reducer 产生唯一 candidate 与 typed write-set。`DurableGraphCommit` 投影出
   `GraphPersistenceCommit(scope, expected_revision, candidate_state, writes)`；**没有 reducer command**，后端不执行图规则。
3. root、child、普通 transition 都先准备不可变 frame 安装结果，再 await commit。只有 receipt 重新准入且与整个请求
   精确相等，才更新运行中的 state/frame。比较 durable bytes/facts，不调用业务对象 equality。
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
| payload 篡改沿用旧 digest | 实际读取与 receipt 确认边界重新执行 envelope/record/frame owner 准入；解码前重新计算 digest | `test_persistence_integrity.py` |
| frame Config 超前或串 definition/version | `GraphConfigCursor.admit_history` 统一历史关系；同 revision 必须同 digest；resolved Config 按唯一 `ConfigSnapshotKey` 索引并精确验 digest | `test_persistence_config.py` |
| completed publication 宽松超集 | completion 保留既有 `settled_activations`；全部生命周期的 publications 与完整成功账本精确相等 | `test_persistence_integrity.py`、`test_continuation_integrity.py` |
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
  single digest 的 canonical metadata 明确编码 Config 缺席，不从当前 state 镜像历史 Config。
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
- 本阶段恢复验证使用内存测试 Port 和新 Graph 对象，**不冒称跨进程、真实后端或 Agent 已打通**。P2 才实现
  `agent.py` 的 load/Config/authority/commit reconciliation，P3 才进行独立进程退出与组合故障验证。

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

**P1 已验收并停在 P2 前；未经用户明确授权，不进入 P2。**
