# Events 实施计划复审

状态：**方向可行；采用 State/evidence/outbox 原子提交，events 侧只按切口实施；计划仍需把 owner 边界和 P0 契约写清**

审查日期：2026-09-02

审查对象：[`events-implementation-plan.zh-CN.md`](./events-implementation-plan.zh-CN.md)

本文件只记录对实施计划的复审结论，不修改实施计划、生产代码或测试。

## 1. 先说结论

大白话：**不是让 events 去实现持久化。**持久化、outbox 和 dispatcher 本来就应该由 `infra/persistence` 负责；当前计划的问题是没有把“events 只提供切口”和“infra 实现能力”分开写，几个关键接口也没有定死。

可以保留的方向有：

- 只复用 `execution.Graph` 和现有唯一 commit 边界，不另建 Graph、runner、reducer 或 EventBus；
- events 放在 commit 链内层，logging 放在外层，顺序由 assembly 决定；
- 并发节点只保证事件身份和值配对，不保证全局到达顺序；
- 参数和结果只保留一个权威来源，不在 events 中偷偷维护缓存。

按当前范围，events 本期不写数据库、不实现 outbox/dispatcher，但要把可供 `infra/persistence` 接入的 typed 切口摆出来。
最终架构采用“State/evidence/outbox 同一事务提交，之后由 infra 异步可靠投递”。计划要先解决以下 P0：

1. 把 events 的装饰器职责和 `infra/persistence` 的事务/恢复/投递职责拆开；
2. 选定唯一的 commit/evidence 接口，消除 `Graph.Transition` 与 `commit request` 的双轨描述；
3. 闭合“节点名称 + 实际访问参数 + 执行结果”的数据来源和 typed schema；
4. 闭合事件接收/持久化切口的异步边界；
5. 把 atomic persistence capability 明确标为 `infra/persistence` 的实现，不在 events 包里复制一套。

因此本轮判定是：**State/evidence/outbox 的架构方向通过；events 侧可先实现切口，整份计划在切口、数据来源和跨 owner 交付清单补齐前，不能直接开工。**

## 2. 评审口径和现状依据

本次按项目已有原则审核：

- [`architecture.zh-CN.md`](./architecture.zh-CN.md#L10-L22) 规定 `GraphRunState` 是唯一状态模型，
  `execution.Graph` 是唯一执行门面，`Graph.Commit` 只是调用方提供的提交边界，并不自动等于持久化；
- [`execution-state-frontier-call-chain.zh-CN.md`](./execution-state-frontier-call-chain.zh-CN.md#L278-L304) 规定
  `commit_transition()` 生成 candidate，外部 callback 必须返回 exact candidate；
- [`AGENTS.md`](../AGENTS.md) 要求复用基础设施、保持 owner 边界、严格类型、不建重复路径，并在交付前跑完整门禁；
- 当前 `Graph.Commit` 仍是一个参数的异步 callable，只有 `transition`、`candidate_state` 和可选的旧式
  `result`，见 [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L102-L153)；
- 有效访问参数只在执行准备阶段的 `ExecutableTask.effective_input` 中出现，见
  [`task.py`](../src/mote_kernel/execution/engine/task.py#L17-L33) 和 [`frontier.py`](../src/mote_kernel/execution/engine/frontier.py#L63-L75)；
- 节点结果在 `TaskResult` 中产生，并在 settlement 确认后才安装 publication，见
  [`result.py`](../src/mote_kernel/execution/result.py#L79-L168) 和 [`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L619-L635)；
- 现有 Cloudflare persistence adapter 只保存 Graph State 的 CAS 行，没有 execution evidence、outbox 或读取/重放协议，见
  [`_commit.py`](../../mote-infra/persistence/cloudflare/python/src/mote_infra_persistence_cloudflare/_commit.py#L53-L130) 和
  [`index.ts`](../../mote-infra/persistence/cloudflare/src/index.ts#L22-L106)；
- `conformance` 当前没有 events/durable protocol suite，manifest 仍为空，见
  [`manifest.json`](../../conformance/manifest.json)。

## 3. 已通过、应保留的部分

以下内容符合“零重复责任、复用基础设施、唯一真相、代码简洁”的原则：

- 计划没有引入第二张 Graph、第二个 scheduler、EventBus、全局 registry 或隐藏线程；
- 明确 `GraphRunState` 不承载 `pending/delivered`，投递状态不反向污染 Graph 状态；
- 明确 events 内层、logging 外层，示例顺序与已确认的 assembly 约定一致：

  ```python
  commit = LoggedGraphCommit(log_sink)(
      EventingGraphCommit(...)(commit_callback)
  )
  ```

- 明确并发 settlement 不需要全局顺序；`revision` 作为状态版本而不是全局事件序号，这一点正确；
- 明确不从“最新 State”猜历史参数，也不在 Event 记录中复制一份业务 DTO，这一点符合单一真相；
- 明确远端 transport 不应在 Graph commit 中直接等待，这个责任应由 `infra/persistence` 的 dispatcher/adapter 承担；
- 明确远程发送是事务提交后的异步阶段；Graph 不等待发送，dispatcher 失败不改变已经确认的 Graph 结果；
- 明确 events 只定义事件投影和持久化接缝，不实现 Store、事务、outbox、恢复或 dispatcher；
- 分阶段、故障注入、clean checkout 和门禁意识是对的，问题在于阶段依赖和接口细节还不够落地。

“events 本期不实现持久化”本身**不是扣分项**；真正的问题是计划没有把外部持久化能力的输入、返回值和 owner 写成可执行契约。
outbox 不是旁路：它和 State/evidence 一起由 `infra/persistence` 在同一事务中写入；只有事务之后的远程发送才是异步阶段。

## 4. P0 阻断项

### 4.1 owner 边界没有写清：events 只摆切口，持久化由 infra/persistence 实现

计划第 1、2、4、5、8、12 节把一次持久化 commit 定义为：

```text
Graph State + execution facts + pending Event/outbox
```

并要求同一 persistence transaction、dispatcher、lease、重试和 delivered 状态（见
[`计划第 20-56 行`](./events-implementation-plan.zh-CN.md#L20-L56)、[`第 375-426 行`](./events-implementation-plan.zh-CN.md#L375-L426)）。
这些内容属于 `infra/persistence` 的实现范围，不应被写成 `mote_kernel.events` 自己要完成的代码。

正确的职责划分应是：

- `mote_kernel.events`：从 execution 提供的 typed evidence 组装不可变事件，并定义唯一的持久化/提交切口；
- `infra/persistence`：实现这个切口，在自己的事务中保存 State、execution 事实和事件记录，并负责恢复、发送、重试、确认和留存；
- `logging`：只在外层记录 commit 生命周期，不读取或修改事件记录。

唯一组装关系保持为：

```python
commit = LoggedGraphCommit(log_sink)(
    EventingGraphCommit(persistence_commit)
)
```

这里的 `persistence_commit` 是 `infra/persistence` 提供的实现；events 不创建数据库连接、不直接写 outbox，
也不启动 dispatcher。Graph 只等待本地持久化 commit 返回；远程发送在 persistence owner 的异步流程中完成，
发送失败不改变已经确认的 Graph 结果。

如果某个运行环境没有提供持久化 capability，由 assembly 决定不安装 Events；不能在 events 内偷偷复制一条
“自己确认 State”的 fallback 路径。

`execution facts` 只保存一份，Event 记录只保存稳定引用和 `event_id`；同一事务如何落盘、如何恢复和如何发送，
由 `infra/persistence` 按这个契约实现。该 owner 边界必须写入计划，而不是要求 events 阶段先做一个临时 Store。

### 4.2 commit 接口仍有两种说法，必须收敛成一个持久化切口

计划第 6.1 节说要把 evidence 放进 `Graph.Transition`，第 6.2 节又说内层 persistence port 接收一个包含
transition、evidence 和 Event/outbox 的 immutable `commit request`（[`计划第 202-263 行`](./events-implementation-plan.zh-CN.md#L202-L263)）。
同时计划又要求 `EventingGraphCommit` 对外表现为普通单参数 `Graph.Commit`。

这三句话不能同时成立，除非把“对 execution 的入口”和“给 infra 的事务切口”明确分层：

- execution 对外仍只经过一个 `Graph.Commit(transition)` 入口；
- events 可以在这个入口内构造一个唯一的、不可变的事务请求，交给 infra 提供的 persistence capability；
- `Graph.Transition` 是 execution 的事实载体，事务请求只是把它和事件引用交给 infra 的适配器，不得再出现第二套等价
  evidence/result 结构。

当前真实接口只有一个 transition 参数，见 [`GraphCommit`](../src/mote_kernel/execution/family_driver.py#L119-L139)。计划给出的
`GraphCommitEvidence` 也只有概念形状：

```text
admitted_frames
invocation_references
settlement_result | None
```

没有明确每个字段的类型、owner、坐标、版本、值还是引用，也没有说明 `publication`、child boundary、resume frame 如何进入同一闭合 union。

实施前必须冻结下面这一条 canonical 路径：

- `Graph.Transition` 携带唯一 typed execution evidence；events 从它投影事件引用，并通过唯一的
  `AtomicCommitRequest(transition + evidence + event_reference)` 切口调用 infra persistence；对外仍返回普通
  `Graph.Commit` 形状；
- 删除旧 `result` 的等价入口，更新 `commit_transition`、`scoped_commit`、logging wrapper、所有 adapter 和测试；
- 给出完整 immutable typed shape（包括 scope/run、activation/generation、frame/result reference、版本和空值规则）；
- 写出 exact candidate、CAS 冲突、取消和 acknowledgement-lost 的返回/错误契约；
- 不使用 `Any`、裸字典、反射或字符串 discriminator。

`AtomicCommitRequest` 是 infra 的接缝，不是 events 自己的持久化实现；在这一步完成前，三个 owner 仍无法保证使用的是同一条提交路径。

### 4.3 事件内容仍没有闭合，无法满足“节点名称-访问参数-执行结果”

需求要的是：

```text
节点名称 + 实际访问参数 + 执行结果
```

但计划第 5.3 节规定 Event/outbox 只保存引用字段（[`计划第 169-189 行`](./events-implementation-plan.zh-CN.md#L169-L189)），实际值由
`infra/persistence` 的 dispatcher 按引用读取。
这只描述了“以后从哪里读”，没有形成现在可用的事件契约：

- 没有明确“节点名称”是 `node_id` 还是另一个 display name；
- 没有参数字段的名称、顺序、类型及多来源合并规则；
- 没有成功 output、失败 failure、中断 payload 的统一 typed result variant；
- 没有 invocation reference/result reference 的具体读端 Port、版本校验和缺失错误；
- 没有规定不可编码、过大、敏感值和底层 DTO 被修改时怎么办。

当前有效 input 是 transient `ExecutableTask.effective_input`，而成功 output 在 settlement 确认后才加入
`ScopedFrameIndex`；当前 `project_event()` 只看 command 和 candidate metadata，不读取 input/result，见
[`projection.py`](../src/mote_kernel/events/projection.py#L35-L59)。因此不能只给 Event 再加几个字段，也不能从 State 猜值。

必须先确定唯一数据流：

```text
execution canonical input/result
        -> typed immutable evidence
        -> event projection
        -> AtomicCommitRequest
        -> infra/persistence 的事务与 dispatcher
```

events 只负责 evidence 到事件引用的不可变投影，不读取 State 猜值，也不保存参数/结果副本。`infra/persistence` 在
同一事务中保存这些 canonical facts 和事件引用；dispatcher 再按引用读取事实并组装“节点名称、实际访问参数、执行结果”。
如果某个环境只需要进程内观测，可由 assembly 另接明确的非阻塞、允许丢失的接收端，但不能把它冒充成 durable persistence。

### 4.4 事件接收端的非阻塞契约仍未写清

计划删除旧的 post-commit `EventSink` 路径是可以的，但必须说明“事务提交”和“远程发送”如何分层。当前实现直接
`await event_sink(event)`，所以接收端的慢、异常或取消仍会占用 Graph 的 commit 调用链，见
[`events/commit.py`](../src/mote_kernel/events/commit.py#L49-L70)。

这里要锁定的是架构契约，而不是再造一个事件运行时：

1. `Graph.Commit` 仍是唯一的异步、权威提交接口；它的异常和取消仍按 execution/commit 契约传播。
2. events 只把 `AtomicCommitRequest` 交给 infra 的 persistence capability；这一步不是远程发送，也不是 events 自己
   写 Store。
3. 远程发送、缓冲、重试、确认和丢弃策略留在 `infra/persistence` 的 dispatcher/adapter；不在 Graph 内等待远程发送。
   这些阶段的异常不能变成已经确认的 Graph 失败，也不能因此重试 Graph commit。
4. 如果另有纯进程内观测接收端，它必须是非阻塞 handoff；其普通异常、取消和未处理 rejection 都只代表观测丢失。
5. 非法配置（缺能力、错误签名）仍应由 assembly/静态类型边界尽早发现，但 events 不负责探测或实现 persistence。

项目规则不允许靠泛化反射、试调用或兼容分支掩盖接口不清。至少要增加正向/反向 typing fixture 和运行时边界测试，
但测试的主断言应分别是“事务切口只调用一次、Graph exact candidate 不变”和“远程发送不影响已确认 Graph”；
纯观测接收端则验证“Graph 结果不变、事件可以丢”。

### 4.5 atomic persistence capability 属于 infra/persistence owner

计划第 3 阶段要求 State、frame/result、Event/outbox 共用一个 transaction，第 4 阶段要求按引用读取、领取 lease、重试和 retention。
这些是 `infra/persistence` 的实现工作；当前 adapter 只围绕 `mote_graph_state_v1` 做 State CAS，说明该 owner 还需要
扩展能力，但不应要求 events 包自己补一套临时实现。

“复用基础设施”不能等同于“基础设施已经具备所需能力”。events 侧需要摆出的只是窄契约；infra 侧最终必须具备：

- 一个明确的 Kernel-owned typed capability/port，描述 atomic write set 和 exact result；
- 一个由 `infra/persistence` 维护的 reference adapter，实现 State + evidence + event record 的真实事务；
- 一个按引用加载同版本 facts 的 read/reconcile 接口；
- capability 缺失时的 fail-fast assembly 行为；
- 不把数据库类型、SQL、transport 或 runtime code 反向搬进 `mote_kernel.events`。

如果 infra 实现不在本轮交付，就应在计划中标为外部依赖和后续交付，不应把它伪装成 events 内部代码，也不应在
events 中留下第二条 fallback 提交路径。

## 5. P1：方向合理但协议还没研究完

### 5.1 dispatcher 的失败、取消和重试状态机由 infra/persistence 负责

计划已经选了 at-least-once 和同一 `event_id` 重发，这是合理方向，但还不是可执行协议。以下情况没有明确答案：

| 场景 | 计划已有的说法 | 仍缺什么 |
| --- | --- | --- |
| transaction 写入失败 | 全部回滚 | 错误分类、取消时是否可能已提交、调用方如何处理未知结果 |
| commit 成功但确认丢失 | 重新恢复 | 用什么 commit key/read-reconcile 判断已提交；直接重试会不会被 stale CAS 拒绝 |
| dispatcher 领取后进程退出 | lease 过期可接管 | lease token、fence、领取并发、过期边界 |
| 发送成功但 ack 丢失 | 同一 `event_id` 重发 | delivered 更新的原子性、重复发送窗口、消费者幂等契约 |
| 永久 codec/敏感值/大小错误 | 显式失败并保留状态 | retryable 与 terminal 的区分、死信/人工处理、状态和诊断字段 |
| 普通网络失败 | 保留 pending 重试 | backoff、最大次数、下一次时间、毒消息和停机策略 |

建议 `infra/persistence` owner 冻结一个最小状态机，例如：

```text
pending -> claimed -> delivered
                    \-> retryable_failed -> pending
                    \-> terminal_failed
```

状态机需要 immutable event identity、lease/fence、attempt、next-attempt、错误类别和 retention 规则；不能把这些字段塞回 `GraphRunState`，也不能让 Kernel 自己做后台重试。

上表全部属于 `infra/persistence` 的 dispatcher，不是 events 包的实现内容。events 只需要把事件引用和
execution evidence 通过唯一切口交给 persistence；Graph 不等待远程发送、不因发送失败重试 commit，也不改变已经确认的
成功、失败或取消结果。若另有纯观测接收端，它仍是被动旁路，允许丢失；不能把两套语义混在一个模糊的 sink 契约里。

这个判断与 Pi 的 observability 设计一致：Pi 的 `emit()` 是无结果的观测出口（见
[`observability.md`](/home/longert/run_rollout/pi/packages/agent/docs/observability.md:81)），并明确要求观测绝不能影响主执行、
subscriber 错误必须吞掉或隔离（见
[`observability.md`](/home/longert/run_rollout/pi/packages/agent/docs/observability.md:299)）。Pi 还把只读
`observe()` 与会参与控制语义的 `on()` 分开（见
[`hooks.md`](/home/longert/run_rollout/pi/packages/agent/docs/hooks.md:83)）；Mote 的事件接收端应采用前者语义。

### 5.2 schema、codec、安全和留存还只是口号

计划提到 `schema_version`、固定 codec 和 retention，但没有给出可审查的 schema。事件包含访问参数和结果后，至少要冻结：

- typed success/failure/interrupt variant 及节点名称字段；
- schema/version 演进、未知字段/未知版本的 fail-closed 行为；
- codec 输入输出类型、字节上限、不可编码值的处理；
- secret-safe / redaction 规则，错误信息不能泄露原始 payload；
- pending 引用在何时可 GC、升级时旧 codec 保留多久。

根规则要求跨语言 durable/wire DTO 使用严格、版本化的 `conformance` schema；不能用 generic event bag 或裸业务字典代替。

### 5.3 execution 影响面被计划低估

计划第 8.1 节说阶段 1“目标不是改 Graph 调度”，但要让 evidence 真正成为唯一事实，至少要改动当前几个明确的时序：

- `fresh_root()` 现在先提交 `StartGraphRun`，再 `install_graph_input()`（[`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L1135-L1163)）；
- child graph 也是先提交 start，再安装 child input（[`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L809-L843)）；
- resume frame 在 commit 确认后才替换 execution-local frame index（[`family_driver.py`](../src/mote_kernel/execution/family_driver.py#L482-L492)）；
- effective input 在 frontier prepare 阶段物化，claim/settlement 当前没有稳定 invocation reference（[`frontier.py`](../src/mote_kernel/execution/engine/frontier.py#L36-L75)）。

所以这不是只改 events projection；需要把 evidence 的 admission、提交、确认、恢复和内存安装顺序一起改，并为 root/child/resume/claim/settlement 各写故障测试。计划应明确这些是 execution owner 的交付，而不是笼统写“暴露 evidence”。

### 5.4 跨 owner 和 conformance 交付没有具体清单

计划提到“同一 typed commit/conformance 契约”，但没有列出要新增的 schema、case、版本、reference adapter 和受影响 runner。
根据根规则，任何 durable protocol 或跨语言 DTO 变化都必须在同一变更中更新 `conformance/` 和所有受影响 runner；当前 manifest 为空并不等于可以跳过。

至少要在 `infra/persistence` 的交付清单中列明：

- protocol/schema 的 owner、版本和字段表；
- atomic commit、ack-lost、outbox replay、lease takeover 的 canonical cases；
- Python/TypeScript/Rust adapter 各自的 runner 和故障注入测试；
- 旧 schema 的迁移/退役方式；
- clean checkout 如何从零重现。

如果本期只做 Kernel 内存观测，则明确不触发 durable conformance；不要在计划里同时写“跨 owner 交付”和“本期不做持久化”。

## 6. 文档、范围和门禁问题

### 6.1 现有文档仍与新计划互相矛盾

实施计划要求删除旧的 `EventSink`、post-commit sink，并把持久化交给 atomic capability（[`计划第 288-315 行`](./events-implementation-plan.zh-CN.md#L288-L315)）。这个方向本身可以成立，
但必须明确 atomic capability 由 `infra/persistence` 实现，不能让读者误以为 events 包承担事务；同时，纯观测旁路仍可按
“允许丢失、Graph 不受影响”的语义单独存在。当前
[`events-design.zh-CN.md`](./events-design.zh-CN.md#L18-L31) 和 [`logging-observability-design.zh-CN.md`](./logging-observability-design.zh-CN.md#L72-L74)
仍描述“先 commit、再通知 sink”的观测模型。

阶段 0 应把两条路径写清：`EventingGraphCommit` 只把 typed 请求交给 infra 的事务 capability；远程发送由 infra
异步完成；若启用纯观测接收端，则明确它是非阻塞、允许丢失的旁路。仍应把文档同步设为前置闸门，并保留历史评审记录
而不让旧建议继续充当实现基线。

### 6.2 当前门禁快照不能证明可交付

已有定向 events 检查通过（20 个测试、events pyright 0 错误、ruff/format 通过），这只能证明当前原型健康，不能证明新计划已经实现。

当前工作树仍有大量并行改动、目标文件未跟踪；此前全仓快照中：

- `make check` 的全仓 pyright 未通过；
- `complexity-ratchet` 的 hotspot 超过配置上限（57 对 47）；
- 根 pre-commit 仍有 kernel structural complexity ratchet 失败；
- clean checkout 无法复现当前 events 设计、源码和测试集合。

这些不是“把门禁上限调高”或加白名单就能解决的问题。交付前应隔离 intended diff、纳入版本控制、从 clean checkout 重跑定向和全仓门禁；与 events 无关的既有失败要单独记账并可审计。

## 7. 建议的可实施拆分

### A. 本期：events 侧只实现切口（不实现持久化）

events 侧只保留当前真正需要的范围：

1. execution 在 settlement 边界提供一次、唯一、typed 的 invocation/result evidence；
2. events 从该 evidence 纯投影出“节点名称、实际访问参数、执行结果”；
3. 通过唯一的 typed `AtomicCommitRequest` 切口把 transition、execution evidence 和 event reference 交给
   `infra/persistence` 提供的 persistence commit；events 不写 Store；
4. 使用已确认的 assembly：events 内层、logging 外层；events 不发现、不重排 logging；
5. 不在 events 中引入 Store/outbox/dispatcher/后台任务；远程发送和恢复由 infra owner 实现；
6. 覆盖 success/failure/interrupt、root/child/resume、并发身份配对、事务切口只调用一次，以及远程发送不影响已确认
   Graph 的组合边界。若另有进程内观测接收端，再单独验证“接收端失败时 Graph 结果不变、当前事件允许丢失”。

本阶段的完成定义应是“events 产出的 typed 请求与 execution 事实一致、能被 infra 接入”，而不是 events 自己完成
“State + evidence + outbox 已原子落盘”。

### B. infra/persistence 侧：实现可靠投递 vertical slice（独立 owner）

在 A 的 evidence、schema 和切口稳定后，由 `infra/persistence` 单独交付（不由 events 包实现）：

1. 冻结 `AtomicCommit`/recovery read 的唯一 typed contract；
2. 由 persistence owner 实现 State/evidence/outbox 的同事务写入、幂等和 reconcile；
3. 在 `conformance` 增加严格版本化 schema 与故障向量；
4. 实现 dispatcher 状态机、lease、重试、ack-lost、retention；
5. 至少一个 reference adapter 和受影响语言 runner 全绿后，再由 assembly 安装 durable capability。

这样既复用基础设施，也不会为了当前一个观察事件把数据库和 transport 责任搬进 events 包。

## 8. 最终通过条件

要把本计划改成“可实施并通过”，至少要满足：

- 明确 events 与 `infra/persistence` 的边界；events 只提供 typed 切口，atomic transaction/outbox/dispatcher 由 infra owner 实现；
- `Graph.Transition` / `Graph.Commit` / atomic capability 只有一条 canonical typed 路径，没有旧 `result` 与新 evidence 双轨；
- 事件确实包含节点名称、实际访问参数和执行结果，且值来自 execution 唯一权威事实；
- 参数/result 的 variant、版本、codec、大小、敏感值和无法投影行为有明确规则；
- 事件接收端是非阻塞旁路；接收端普通异常、取消或丢失不会改变 Graph 的提交结果，且有测试锁定；
- persistence 的提交失败、确认丢失、dispatcher 重试和 terminal failure 由 infra owner 定义状态机和故障测试，events 只验证
  请求/返回边界；
- events 内层、logging 外层的组合有回归测试，但 events 不负责重排或验证外层；
- 并发测试只验证身份和值配对，不引入全局顺序要求；
- `conformance`、reference adapter、受影响 runner、文档和 clean checkout 交付清单明确；
- intended files 已跟踪，定向门禁和全仓门禁通过，或对无关既有失败提供隔离、可审计说明。

**最终结论：State/evidence/outbox 原子提交、事务后异步可靠投递的架构选择通过；events 只实现 typed 切口，
`infra/persistence` 实现事务和 dispatcher。当前计划仍需关闭 canonical 接口、事件内容 schema 和跨 owner 交付清单
这几个 P0，关闭后即可按两侧 owner 并行落地。**
