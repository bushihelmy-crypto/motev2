# Agent latest state/session 恢复实施方案

状态：Kernel contract-only 已完成（Port/Agent 接线、测试适配器与回归测试）；生产持久化后端交付延期，本文不把测试适配器当作 durable 生产语义证明。
调研基线：`mote-kernel`，`HEAD 488097a`；本文档中的“当前”均指该基线。

## 1. 先给结论

本方案把 `run_id` 保留为 Graph/ReAct run 的稳定身份，并让 `(agent_id, run_id)` 同时成为 Graph
状态和 AgentSession 的 durable 归属键。新增的只有一个**持久化 latest head 索引**，不新增
`session_id`、`conversation_id`、第二份 State 或新的执行 runner。

首期冻结的公共语义如下：

```text
AgentResume(run_id="r-17")  -> 精确恢复 (agent-A, r-17)
AgentResume()               -> 读取 agent-A 的 latest head，再精确恢复该 run
AgentResume(None)           -> 与 AgentResume() 相同
AgentStart(run_id="r-18", values, session?)
                             -> 仍是显式创建新 run；成功后 latest head 指向 r-18
```

`AgentResume` 省略 `run_id` 时，**一次 `GraphCheckpoint` 读取同时提供 state、值证据和
`EncodedAgentSession`**；不能分别读取“最新 state”和“最新 session”。结果中的 `run_id` 永远是
实际解析出的字符串（例如 `r-17`），不会返回 `None`。

首期不把 `AgentStart(run_id=None, ...)` 模糊解释成“恢复或创建”。新任务是否要自动继承 latest
Session、以及由谁分配新 run ID，放入文末的 P2 扩展；在该能力获批前，`AgentStart` 继续要求显式
`run_id`，跨新 run 的 Session 仍须由调用方显式传入。这一边界避免把一次重试变成不可幂等的随机新 run。

## 2. 目标、范围和不变式

### 2.1 目标

1. 普通恢复调用不再要求业务方保存当前 run ID；`AgentResume()` 自动恢复该 Agent 的 latest
   state 与 Session。
2. 需要审计、回放或修复历史时，`AgentResume("旧 key")` 仍能精确定位历史 run。
3. state、值 evidence、Config provenance 和 Session 继续处于同一个 Graph checkpoint/commit
   一致性边界。
4. 在目标后端契约中，latest 指针移动只能发生在完整 durable commit 被确认之后；进程崩溃、Unknown
   或 tombstone 不能制造悬空 latest。当前仓库只交付该 Kernel contract 和测试适配器，未交付生产事务实现。
5. 适配器负责 latest 元数据；`Agent` 仍是无状态的 frozen capability holder，不缓存热态快照。

### 2.2 不变式

- `(agent_id, run_id)` 是唯一 durable family key。`agent_id` 隔离 Agent 命名空间，`run_id` 在一
  个 Graph/ReAct loop 的所有 superstep/revision 中不变。
- `GraphRunState.revision` 是 CAS/提交版本，不是新的 run ID；`superstep` 是执行位置，也不是
  Session ID。
- latest head 只保存一个 `AgentRunKey`（及防 ABA 的 generation），不复制 GraphRunState 或
  Session payload。
- latest head 指向的 family 必须能返回一份完整、相互一致的 `GraphCheckpoint`；不能把两个
  revision 的 state 和 session 拼接起来。
- explicit `run_id` 路径完全绕过 latest 查询；latest 路径解析一次后也必须绑定到解析出的 exact
  key，不能在执行中途静默切换到另一个 run。
- Config 仍按 checkpoint 中的 exact `GraphConfigCursor` 恢复；latest head 不是 Config head。
- 终态恢复是只读回放，不会因为调用了 `AgentResume()` 而自动创建新任务。

## 3. 调研结论：当前实现到底保存了什么

### 3.1 Agent 入口和当前调用链

`src/mote_kernel/agent.py:80-128` 定义了当前请求：`AgentStart.run_id` 仍必填，
`AgentResume.run_id` 可省略；省略时由 Agent 解析 latest head。`AgentResult` 暴露结果和可选
Session，但不暴露 Graph state/continuation。

`Agent.run()` 当前在 `src/mote_kernel/agent.py:463-520` 对请求执行两步路由：省略 ID 时先调用
`load_latest(agent_id)`，随后对解析出的 key 走与显式 ID 相同的 exact authority 路径。

```text
AgentRunKey(self.agent_id, GraphRunId(request.run_id))
    -> authority.acquire(exact key)
    -> _run_authorized()
    -> authority.release()
```

`_run_authorized()`（`agent.py:319-417`）的实际顺序是：

```text
load(exact authority, expected_latest=head when latest is selected)
  -> NeverCreated / GraphCheckpoint 分流
  -> 按 checkpoint 的 Config cursor 精确恢复 Config
  -> 从同一 checkpoint 解码 AgentSession
  -> 组装 Graph
  -> Graph.run(recovery=...) 或 Graph.run(values, run_id=...)
  -> 投影 AgentResult
```

当前 Agent 不保存 state、Session、continuation 或 latest 权限缓存；这正是 latest 逻辑应该继续保持的
边界。

### 3.2 当前 durable 主键与权限

`src/mote_kernel/persistence.py:52-69` 的 `AgentRunKey` 是：

```python
@dataclass(frozen=True, slots=True)
class AgentRunKey:
    agent_id: str
    run_id: GraphRunId
```

`ExecutionAuthority` 绑定这个 key。当前 `AuthorityPort` 仍只有
`acquire(run)`/`release(authority)`；latest 路径先把 head 解析成精确 `run_id`，再复用这条 exact
authority 语义，本方案不向 AuthorityPort 增加业务级 ID 查询。

`PersistencePort`（`persistence.py:179-228`）当前提供 `load_latest`、带可选 `expected_latest` 的 `load`、
`commit` 和 `reconcile`。`load`
返回完整 `GraphCheckpoint` 或明确的 `NeverCreated`，而不是“没读到就当作新建”。

### 3.3 Checkpoint 已经把 state 和 Session 绑在一起

`src/mote_kernel/execution/persistence.py:647-746` 的 `GraphCheckpoint` 字段为：

```text
root_state
child_runs
graph_inputs
publications
agent_session
```

它不是第二套 runtime state，而是一次一致性 persistence read。`GraphPersistenceCommit`
（`execution/persistence.py:423-503`）把 candidate state、完整写集和可选
`EncodedAgentSession` 放入同一个 immutable commit request；`DurableGraphCommit`
（`:552-644`）只在 exact receipt 确认后让 Graph 安装 successor。

因此“恢复最新 state + 最新 session”正确的实现单位是**同一个 checkpoint/family read**，不是新增
SessionStore。

### 3.4 Session 和 Config 的现状

`src/mote_kernel/session.py:59-90` 的 `AgentSession` 逻辑上只有三个字段：

```text
hook_state / context / config
```

`EncodedAgentSession`（`session.py:235-266`）只编码 hook/context，并保存精确 Config cursor。
`AgentSessionCodec.decode()` 会校验 codec、payload 和 Config cursor（`:212-232`）。

`Agent._recover_configs()`（`agent.py:286-302`）遍历 checkpoint 的全部 cursor，并通过
`ConfigSnapshotStore.load(exact key)` 恢复；`ConfigSnapshotStore` 的契约（`config.py:400-412`）
明确没有 latest fallback。因此 latest head 绝不能改成“取最新 Config”。

### 3.5 现有测试已经证明的边界

以下测试是迁移时必须保留的回归基线：

- `tests/agent/test_lifecycle.py:35-210`：执行预算后恢复、终态回放、缺失 run、interrupt 单次
  消费、authority 排他、Agent/run 命名空间隔离。
- `tests/agent/test_session.py:61-174`：每次 commit 携带完整 Session、同一 checkpoint 恢复、
  Config cursor 精确解析；`:376-820` 覆盖 nested/parallel successor 的确认顺序。
- `tests/agent/test_cancellation.py`：acquire/load/commit/release 取消边界和 Unknown 处理。
- `tests/execution/test_persistence_recovery.py` 与 `test_persistence_admission.py`：完整值证据、
  child/family 一致性、缺证据拒绝和终态冷恢复。

`tests/agent/persistence_fixtures.py` 的内存 authority/store 现已按 `AgentRunKey` 保存 family，
并以测试专用 `AgentLatestHead` 索引模拟 latest fence；这只用于 Kernel contract 验收，不是生产内存
fallback 或 durable backend。

## 4. 身份模型（冻结）

```text
稳定 Agent 命名空间
agent_id = "support-agent"
       │
       ├── latest head（持久化索引，不是 State）
       │       └── AgentRunKey(agent_id, run_id="r-17")
       │
       └── run family（由 key 定位）
               ├── GraphRunState（revision 0..N、superstep、frontier）
               ├── child state / graph input / publication evidence
               └── AgentSession envelope（hook/context payload + Config cursor）
```

| 名称 | 当前类型/位置 | 语义 | 是否由调用方保存 |
| --- | --- | --- | --- |
| `agent_id` | `str`，`Agent` 字段 | Agent 命名空间/租户边界 | 是（装配配置） |
| `run_id` | `str`，边界处转为 `GraphRunId` | 一个 Graph/ReAct loop 的稳定身份 | 创建时提供；结果始终返回 |
| `GraphRunId` | `NewType(str)`，`state/graph_state/identity.py:6` | 类型层区分，运行时底层仍是字符串 | 不代表 loop 次数 |
| `AgentRunKey` | `persistence.py:52-69` | state、值证据、Session 的 durable 主键 | 内部由 Agent 组合 |
| `revision` | `GraphRunState.revision` | 同一 run 的 CAS 版本 | 不作为外部恢复 ID |
| `superstep` | `GraphRunState.superstep` | 同一 run 的执行位置/循环进度 | 不作为外部恢复 ID |
| `AgentLatestHead` | 本方案新增的索引记录 | `agent_id -> AgentRunKey` 的当前指针 | 由 persistence 管理 |
| Session | `AgentSession` 三字段 | 与 checkpoint 同一边界的调用方快照 | 不单独建 key |

`GraphRunId` 不是“第几个 ReAct 迭代”；同一个 loop 的多次节点推进、interrupt 答复和恢复都使用
同一个 `run_id`。要开始新任务，仍创建新 `run_id`；要恢复旧 loop，使用其 key 或 latest head。

补充一点：`GraphRunId` 不是带字段的 struct/dataclass，而是 `typing.NewType` 对 `str` 的静态
类型标注（`GraphRunId("r-17")` 在运行时仍是字符串）。真正带有两个字段、参与 durable 主键寻址
的是 `AgentRunKey(agent_id, run_id)`。

## 5. 公共 API 变化

### 5.1 首期唯一必改 API：`AgentResume.run_id` 可选

当前实现的 `AgentResume` 为：

```python
@dataclass(frozen=True, slots=True)
class AgentResume(Generic[GraphValueT]):
    run_id: str | None = None
    answers: tuple[AgentAnswer[GraphValueT], ...] = ()
```

规则：

1. `None` 只表示“按 latest head 解析一个已有 run”，不是首次创建。
2. 非 `None` 必须通过现有 canonical identity admission；空串、空白和非法类型在取得 authority
   前失败。
3. `answers` 的精确 interrupt 坐标仍由 Graph 恢复准入校验；省略 ID 不放宽 answer 的身份检查。
   如果携带 answer 的无 ID Resume 解析出的 latest 已不是产生该 interrupt 的 run，必须在 Graph
   执行前以 head fence/interrupt identity 拒绝；调用方可改用结果中记录的实际 `run_id` 重试。
4. 显式 `AgentResume("r-17")` 继续表示 exact historical resume；它不经过 latest lookup。

`AgentStart` 首期保持：

```python
AgentStart(run_id: str, values: Graph.Values[T], session: AgentSession | None = None)
```

不能把 `AgentStart(None, ...)` 解释成 latest 恢复，也不能用 latest run 的 key 覆盖已有 family。
这保留了当前“Start 只 create、Resume 只 existing”的幂等边界。

### 5.2 结果和错误

- `_project_result()` 必须接收解析后的 `actual_run_id`，而不是直接读取
  `request.run_id`；`AgentResume()` 的结果例如为 `AgentCompleted(run_id="r-17", ...)`。
- latest 不存在时抛出既有 `AgentRunNotFoundError`，并保证没有 Graph/node/commit 调用。
- latest 元数据不可用、head 指向 tombstone、checkpoint 损坏或 Config 缺失时，保留对应的
  `PersistenceUnavailableError`、`PersistenceTombstoneError`、`PersistenceContractError` 或
  `ConfigContractError`；这些情况都不能降级为 Start。
- 解析后 head 在读取前发生变化时使用明确的
  `LatestHeadMovedError(PersistenceConflictError)`；释放 authority 后由调用方重新发起一次无 ID Resume。
  不能在同一次调用中把旧 answers 套到新 run。

### 5.3 典型调用

```python
# 日常继续：只需要知道 Agent，不需要保存每个历史 ID
result = await agent.run(AgentResume())

# 需要回答当前 interrupt 时仍可省略 run_id
result = await agent.run(AgentResume(answers=(answer,)))

# 审计/历史回放：显式 key 完全绕过 latest
old = await agent.run(AgentResume("r-03"))

# 新任务：首期仍显式产生一个新 durable key
new = await agent.run(AgentStart("r-18", Graph.values(job=job), session=old.session))
```

业务方正常只需保存最近一次结果里的一个 `run_id`（或直接使用无 ID Resume）；无需维护所有
历史 ID。历史 ID 的保留由 durable store 负责。

## 6. Durable latest head 设计

### 6.1 类型和存储记录

`src/mote_kernel/persistence.py` 已增加 owner-internal typed record `AgentLatestHead`：

```python
@dataclass(frozen=True, slots=True)
class AgentLatestHead:
    key: AgentRunKey
    generation: int
```

准入规则：

- `key` 必须是 exact `AgentRunKey`，并且 `key.agent_id` 与查询的 `agent_id` 一致；
- `generation` 是正整数，只在 head 目标 run 改变时递增，用于检测 A→B→A 的 ABA；同一 key 的
  revision 推进不需要改 generation；
- 该记录不包含 state、Session、Config payload 或 continuation；它只是索引/栅栏元数据。

后端等价 schema：

```text
agent_latest_head
------------------
agent_id    PRIMARY KEY
run_id      NOT NULL
generation  NOT NULL
```

`(agent_id, run_id)` 的 family 表仍是唯一 state/session owner。可以增加数据库外键或完整性检查，
但不得把 head 表设计成第二份 State。

### 6.2 “latest”定义

首期把 latest 定义为“该 Agent 最近一次**成功创建的 root run**”，而不是字符串最大值、时钟最大
值或最近一次历史回放：

- root `AgentStart` 的 revision-0 commit（`scope == ()` 且 candidate run 等于 authority run）
  与 head upsert 在同一事务中完成；
- 同一 run 后续 revision 只更新该 family 的 checkpoint，head 目标 key 不变；读取该 key 自然
  得到该 run 的最新 state/session；
- 对旧 run 的显式 `AgentResume("r-old")` 只操作旧 family，不把 latest 倒退到旧 key；
- terminal replay、只读 load、NotApplied 和最终 Unknown 都不移动 head；
- 如果产品后来需要“显式历史 run 成功后提升为当前 run”，应增加独立的 promote 操作和审计规则，
  不能让普通 Resume 隐式改变指针。

这个定义既保留历史 run，又避免一次修复旧 run 把当前会话切回过去。若评审选择“最近一次成功
commit（而不是最近创建）”作为 latest，只需改变 head 更新条件和测试矩阵，读取/原子性规则不变。

### 6.3 Head 更新的原子性

Persistence adapter 的一次 root Start commit 应等价于：

```text
BEGIN
  校验 authority、family absence/CAS、完整 state/value/Session/receipt
  写入 (agent_id, run_id) family
  若这是 root revision-0：在 agent_latest_head 中插入或更新目标 key/generation
COMMIT
返回 CommitApplied（确认上述全部事实）
```

不能先写 head 再写 state/session，也不能 state 成功后用一个无法对账的异步 best-effort 更新
head。后端不支持同事务时，adapter 必须提供等价的 durable transaction/outbox，并在无法证明两者
都完成时返回 `CommitUnknown`；Kernel 不得自行把两次写拼成“原子”。

同一 commit key/content 的重放必须保持幂等：

- 已存在完全相同 receipt 时，不重复创建 run，也不递增 generation；
- 同 key 不同 content 仍是 `PersistenceConflictError`；
- `CommitUnknown` 的 reconcile 必须核对原 request、family receipt 和 head 结果，确认完整成功后
  才返回 `CommitApplied`。

对账不能把“当前 head 仍指向该 run”当成历史成功的唯一证据：后续合法的 root run 可以把 latest
推进到另一个 key。Adapter 必须保留原 root 与其 head upsert 的 durable receipt，并同时确认当前
head 链没有缺失或损坏；这样既能确认历史 A 已成功，也不会把丢失的 head 误报为成功。

本批测试适配器对这条链采用唯一重建规则：每个 Agent 的 root receipt generation 必须从 `1`
开始连续递增且不重复，当前 latest 只能取该链的末端。journal 的物理追加顺序不是判定依据；
历史 receipt 与当前 head 分开校验；回退、缺口或无回执的当前 head 保持 `CommitUnknown`，
重复 receipt 或损坏 journal 则在读取边界报告 `PersistenceContractError`。

### 6.4 Head 与 checkpoint 的一致读取

本批已扩展 `PersistencePort`：

```python
async def load_latest(self, agent_id: str, /) -> AgentLatestHead | NeverCreated: ...

async def load(
    self,
    authority: ExecutionAuthority,
    /,
    *,
    children: tuple[ScopeRunCoordinate, ...] = (),
    expected_latest: AgentLatestHead | None = None,
) -> GraphCheckpoint[GraphValueT] | NeverCreated: ...
```

`expected_latest` 是 latest 路径的 read fence：adapter 在同一一致性读中核对 head 的 key/generation
仍等于解析结果，再返回该 key 的完整 checkpoint。显式 key 路径传 `None`，保持现有 exact load。
该 fence 要作为本次 invocation 的不可变材料保留；第一次 family load 之后如需按
`Graph.recovery_child_reads()` 做 child reread，每一次 reread 也要携带同一个
`expected_latest`（或由 authority lease 等价地钉住同一 generation），不能只保护第一次读取。
`load_latest` 本身也必须是线性一致（或明确报告 unavailable）的索引读取；允许短暂陈旧的
eventual-consistency 结果会让默认恢复选择错误的 run。

如果 head 在 `load_latest` 与 `acquire` 之间变化，`load(... expected_latest=...)` 必须失败；Agent
释放当前 authority 后重新发起一次全新的 latest Resume。禁止：

```text
旧 head 的 answers + 新 head 的 state       # 不允许
旧 head 的 state + 新 head 的 session       # 不允许
旧 head 读失败后静默 fallback 到另一个 key    # 不允许
```

用户说明 React 不并发，首期可采用“resolve → exact acquire → fenced load”的窄实现；这只是降低
业务竞争概率，不能替代 adapter 的 generation 检查。若未来需要多 writer 的严格原子准入，可增
加 `acquire_latest(agent_id)`，由 authority/persistence adapter 一步返回绑定实际 key 的
`ExecutionAuthority`，不增加任何业务 ID。

## 7. Agent 运行时调用链

### 7.1 显式 run_id（保持现有路径）

```text
admit AgentResume("r-17")
  → key = AgentRunKey(agent_id, GraphRunId("r-17"))
  → authority.acquire(key)
  → persistence.load(authority, expected_latest=None)
  → exact Config cursor recovery
  → decode checkpoint.agent_session
  → GraphRecovery + 同一个 Graph.run()
  → project result(run_id="r-17")
  → Graph task cleanup 完成后 release
```

显式 key 不调用 `load_latest`，因此可以稳定读取历史 run，即使当前 latest 已经是另一个 key。

### 7.2 省略 run_id（新增路径）

```text
admit AgentResume(None)
  → persistence.load_latest(agent_id)
  → NeverCreated ? AgentRunNotFoundError : 得到 AgentLatestHead(key, generation)
  → authority.acquire(key)
  → persistence.load(authority, expected_latest=head)
  → 如需 child reread：仍携带 expected_latest=head
  → head fence 失败 ? release + LatestHeadMovedError
  → exact Config cursor recovery
  → 从同一个 checkpoint 解码 Session
  → GraphRecovery + 同一个 Graph.run()
  → project result(actual key.run_id)
  → cleanup 后 release
```

重点是：latest lookup 只解析 key，不读取或缓存 state/session；真正的 state/session 仍由受
authority 保护的完整 `load` 返回。

### 7.3 创建新 run（首期）

```text
AgentStart("r-18", values, session?)
  → exact acquire(AgentRunKey(agent_id, "r-18"))
  → load -> NeverCreated
  → 可选显式 Session / exact initial Config
  → Graph.run(values, run_id="r-18", ...)
  → root revision-0 commit 与 latest upsert 同事务确认
  → 后续 state/session commit 继续写 r-18 family
  → result.run_id == "r-18"
```

已有 key 的 Start 仍报 `PersistenceConflictError`；缺失 authority、tombstone、未知提交都不
能通过重新选 latest 来掩盖。

## 8. State、Session、Config 的恢复规则

### 8.1 State 和 Session 必须从同一个 checkpoint 来

选定 `AgentLatestHead.key` 后只允许一次 family-consistent read：

```text
GraphCheckpoint.root_state
GraphCheckpoint.child_runs / graph_inputs / publications
GraphCheckpoint.agent_session
```

Session 解码仍走现有 `AgentSessionCodec`；没有 envelope 就得到 `session=None`。不能因为 latest
run 没有 Session，就去上一 run 查询一个“可用”的 Session；这会把不同 durable 身份拼接起来。

Graph 历史 frame 仍保留自己的 Config provenance，当前 family owner Session 仍由
`GraphRecovery` 注入后续节点；不把 latest Session 倒灌进历史 frame。

### 8.2 Config 继续 exact，不引入 latest Config

latest head 选中的是 `AgentRunKey`，不是 Config。恢复流程仍为：

1. 遍历 checkpoint state/frame/Session 的全部 `GraphConfigCursor`；
2. 对每个 cursor 构造 exact `ConfigSnapshotKey`；
3. `ConfigSnapshotStore.load(key)`，校验 digest；
4. 通过 `ConfigResolver` 解析能力；
5. 用 Session envelope 的 cursor 选择同一份 resolved Config。

缺 snapshot、digest 不匹配、definition/version 不匹配或 Session cursor 无法 join 时 fail closed。
即使 Config store 有一个更“新”的 snapshot，也不能覆盖旧 run 实际引用的 revision。

### 8.3 跨新 run 的 Session（首期边界）

`AgentResume()` 只恢复 latest 所指向的**同一个 run**。`AgentStart` 创建新 run 时：

- `session` 显式提供则按现有规则编码并与新 run 的 root commit 原子提交；
- `session=None` 仍表示没有 Session，不隐式读取 latest，避免把不同业务任务的上下文泄漏到一起；
- 需要连续任务时，调用方传入上一个已确认结果的 `session`，例如
  `AgentStart("r-18", values, previous.session)`。

如果产品要求“新任务也自动沿用 latest Session”，见第 12 节的 `AgentStartLatest`，它必须是
明确的新操作，而不是改变 `AgentResume` 的含义。

## 9. 失败、重试和生命周期语义

| 场景 | 允许的动作 | latest/head 结果 | 是否执行节点 |
| --- | --- | --- | --- |
| `load_latest` 返回 `NeverCreated` | 抛 `AgentRunNotFoundError` | 不变 | 否 |
| latest metadata 不可用 | 原样报告 unavailable | 不变 | 否 |
| head 指向 tombstone/损坏 family | 原样报告 tombstone/contract error | 不变 | 否 |
| head 在 resolve 到 fenced load 间改变 | 抛 head-moved/conflict，重新发起新请求 | 不变 | 否 |
| exact run 不存在 | 现有 `AgentRunNotFoundError` | 不变 | 否 |
| exact run 已 `COMPLETED/FAILED/ABORTED` | 从 checkpoint 做终态回放 | 不变 | 否 |
| interrupt 无 answer | 返回同一 interrupt | 不变 | 否（不重复 producer） |
| `CommitNotApplied` | 在既有有限次数内重发同一 immutable request | 不变，直到 Applied | 不重跑节点 |
| `CommitUnknown` | reconcile 同一 request；仍未知则停止 | 不移动 | 不重跑节点 |
| root revision-0 `CommitApplied` | 同事务写 family + upsert head | 指向新 key | 已执行的 root commit |
| 后续 revision `CommitApplied` | 更新该 key 的 checkpoint | key 不变 | 按 Graph 正常推进 |
| explicit 历史 run 成功继续 | 只更新历史 family | latest 不倒退 | 按请求执行 |
| Start 使用已存在 key | 抛 conflict | 不变 | 否 |
| authority 失效/释放失败 | 维持现有异常优先级与清理规则 | 不因错误移动 | 视执行阶段而定 |

终态规则特别重要：`AgentResume()` 是恢复/回放，不是“再开启一个新的 ReAct loop”。新任务
必须使用新的 `AgentStart` key（或未来明确的 StartLatest 操作）。

## 10. 实施分阶段计划

### P0：本文档评审（已完成，生产后端能力仍待确认）

- 确认 `AgentResume(None)` 是首期 API；
- 确认 latest 定义为最近成功创建的 root run，还是最近成功 commit；
- 确认首期不做无 ID 新任务创建；
- 冻结 family commit 与 head upsert 必须具备同事务或等价 durable 证明的契约门槛；本批不声称目标后端已满足该门槛。

评审结论：采用 `AgentResume(None)`、最近成功创建的 root run 作为 latest，且不在首期改变
`AgentStart` 的显式 `run_id` 约束；Kernel contract 与测试适配器已实施，生产 adapter/schema/migration
留待后续项目。

### P1：类型和 Persistence Port（已完成）

涉及：

- `src/mote_kernel/persistence.py`
  - 增加 `AgentLatestHead`；
  - 增加 `PersistencePort.load_latest()`；
  - 给 `load()` 增加 `expected_latest` read fence；
  - 更新 commit/reconcile 文档契约，明确 root Start 成功时 head 一起确认；
  - 新增 typed head moved/conflict（如需要），不使用字符串 discriminator。
- `src/mote_kernel/execution/persistence.py`
  - 保持 `GraphCheckpoint` 和 `GraphPersistenceCommit` 的唯一 owner；
  - 仅补充 commit confirmation 文档/必要的 owner-internal metadata，不创建第二个 checkpoint。

验收：`AgentLatestHead` 的 exact admission、正整数 generation、latest read fence 以及同 key 重放均有
确定性测试；不同 key/content、tombstone、Unknown/reconcile 继续复用既有持久化契约测试。

### P2：Agent latest 路由（已完成）

涉及：

- `src/mote_kernel/agent.py`
  - `AgentResume.run_id` 改为可选；
  - 在 acquire 前区分 explicit/latest 两条解析路径；
  - latest 路径执行 resolve → exact acquire → fenced load；
  - `_run_authorized` 接受已解析 key/head，不再次从 `None` 猜 ID；
  - `_project_result` 使用实际 run ID；
  - 保持 release/cancellation/GraphRecovery 原调用链。
- `src/mote_kernel/persistence.py` 的 AuthorityPort 不增加业务级 ID；仍按 exact
  `AgentRunKey` 排他。

验收：running/interrupt/terminal、显式历史、head race、无 head、跨 Agent 隔离、Session/Config 精确恢复
均已覆盖；所有恢复最终进入同一个 `Graph.run(recovery=...)`。

### P3：测试适配器和组合故障（contract-only 已完成）

- 扩展 `tests/agent/persistence_fixtures.py`：维护 heads、generation、原子 commit **契约模拟**、head
  fault injection；snapshot/journal 两种表示使用相同契约。它们只验证 Kernel 边界，不证明生产原子事务。
- `tests/agent/subprocess_worker.py` 从原子替换的 keyed commit/receipt journal 中按 root revision-0 创建证据
  推导各 Agent namespace 的 latest head，并由恢复进程通过 `AgentResume()` 重新解析；它覆盖多 Agent、多
  run 和跨进程 Session 的顺序场景，但仍是测试适配器，不是生产数据库事务实现。
- 增加独立进程测试：写入 root state（以及同一 commit 中存在的 Session envelope）后在 acknowledgement
  前退出，重新进程调用 `AgentResume()`；确认测试 journal 的 keyed family 不会指向半份 checkpoint。
- 覆盖不同 run、nested child、parallel family、Config revision、取消和 Unknown 的组合。
- 迁移现有示例、README、中文/英文架构文档和持久化计划；不要保留旧兼容 wrapper/alias。

以下项目明确延期，不属于本批交付：生产数据库/分布式 persistence adapter、latest head schema、旧数据
backfill、真实线性一致 `load_latest`、root state/value evidence/Session/head 的生产原子事务，以及
断电/fsync/网络分区下的 durable 证明。

### P4：生产 adapter、迁移、上线和清理（待后续项目）

1. 先实现并部署 schema/adapter 和一次性 backfill；
2. 校验每个 head 的 family、root state、值 evidence、Session envelope、Config cursor 完整；
3. 通过真实线性一致读与原子提交验收后，才把已完成的 `AgentResume(None)` Kernel contract
   路由开放给生产 adapter；
4. 观察 head-moved、missing-head、tombstone、Unknown、Config-missing 指标；
5. 删除临时 dual-read/feature flag，仅保留正式 Port 契约；
6. 完成 `make check` 与 monorepo 根目录 pre-commit。

## 11. 数据迁移和回滚

### 11.1 Backfill 规则

旧存储没有 latest 表时，适配器必须依据**真实 durable 创建顺序/创建 receipt**为每个
`agent_id` 选出一个 head：

- 不能按 `run_id` 字典序、字符串最大值或当前时间猜测；
- 如果旧数据没有可证明的创建顺序，迁移应输出待人工确认的 agent 列表并 fail closed，不能
  自动选一个可能错误的 Session；
- backfill 后逐个验证目标 family 能返回完整 checkpoint，且 `root_state.run_id` 与 head key
  一致；
- head generation 从 1 开始，历史 run 记录不删除。

### 11.2 部署顺序

```text
新增 head schema（不改变 exact historical API）
  → backfill + 一致性审计
  → adapter 实现 load_latest/expected_latest/原子 upsert
  → 将 AgentResume(None) 路由切换到已验收的生产 adapter
  → 逐步放量并观察
```

若 latest 读路径发现问题，可暂时要求调用方使用显式 `AgentResume(run_id)`；这不是生产代码中
长期保留的第二条执行路径，而是发布回滚开关。任何已确认的 state/session commit 都不可回滚
或删除；只回滚“默认选择方式”。

### 11.3 Tombstone 和保留策略

- 删除/保留任务前必须先处理 latest：将 head 原子移动到另一个合法 key，或写入明确的 absent
  状态；不能留下指向 tombstone 的 head。
- tombstone 记录不能重新解释成 `NeverCreated`，也不能用同一 `(agent_id, run_id)` 重建。
- 本方案不定义历史清理策略；清理需要独立 retention/purge 设计和审计。

## 12. 可选 P2：无 ID 开启新任务并继承 latest Session

这不是首期 `AgentResume(None)` 的一部分，但它正面解决“新任务不想维护许多 ID”的 UX。若产品
确认需要，建议增加语义明确的 owner-internal/public request，例如：

```python
@dataclass(frozen=True, slots=True)
class AgentStartLatest(Generic[GraphValueT, AgentHookStateT, AgentContextT]):
    values: Graph.Values[GraphValueT]
    # 可选：明确要求覆盖，而不是隐式猜测
    session: AgentSession[AgentHookStateT, AgentContextT] | None = None
```

其调用必须是一个单独的 durable create protocol：

```text
读取 latest head + 完整 checkpoint/session
  → 在 persistence 中分配一个新的稳定 run_id
  → 以 inherited/显式 Session 创建新 root run
  → root state + values + Session + 新 latest head 原子提交
  → 返回实际新 run_id
```

不能在每次网络重试时简单生成一个随机 UUID；需要 provider 的稳定 create request identity 或
事务内 ID 分配，否则一次超时可能创建多个任务。也不能让 `AgentStart(None)` 同时承担“恢复
latest”和“创建新 run”，因为两者对 `values`、幂等、错误和审计的语义完全不同。

在该扩展获批前，本文第 5 节的首期边界有效：新 run 显式 `run_id`，Session 显式交接。

## 13. 测试与验收矩阵

### 13.1 本批新增测试与验收矩阵

本批在 `tests/agent/test_latest_recovery.py`、`tests/agent/test_process_recovery.py` 中落实了以下项目：
测试只调用正式的 `PersistencePort.load(..., expected_latest=...)` 签名；没有保留旧 `load` 签名的
兼容分支或仅为 legacy 测试服务的适配测试。

| 测试 | 断言 |
| --- | --- |
| `test_resume_without_run_id_recovers_latest_state_and_session` | `AgentResume()`/`AgentResume(None)` 只读 head，恢复同一 checkpoint 的 state/session，结果返回实际 run ID |
| `test_explicit_historical_resume_bypasses_latest_and_does_not_retrograde_head` | 显式旧 key 不调用 latest；旧 run 真实继续并提交后，latest 不倒退且省略 ID 仍恢复新 run |
| `test_latest_resume_can_answer_the_latest_interrupt_without_an_id` | 省略 ID 的 interrupt 恢复与回答仍绑定解析出的 exact run |
| `test_missing_latest_is_not_treated_as_a_start` | 无 head 抛 not-found，无 commit/node 调用 |
| `test_latest_head_move_between_lookup_and_family_load_fails_closed`、`test_latest_head_race_after_lookup_before_acquire_fails_closed` | generation/key 不一致时不执行 Graph、不混合答案 |
| `test_latest_head_pointing_to_a_missing_family_fails_closed`、`test_latest_head_tombstone_is_not_reinterpreted_as_a_missing_run` | dangling/missing 或 tombstone 不 fallback 到其他 run 或 Start |
| `test_unapplied_root_start_does_not_move_latest`、`test_root_family_and_latest_head_reconcile_as_one_applied_fact`、`test_reconcile_unknown_after_durable_root_write_remains_unresolved` | root/head 只在完整 Applied 后可见；Unknown 保持明确状态 |
| `test_root_commit_replay_does_not_increment_latest_generation` | 精确重放幂等，不重复推进 generation |
| `test_latest_resume_uses_the_checkpoint_config_cursor_not_config_latest` | Session 只解析 checkpoint cursor，不读 latest Config |
| `test_latest_heads_are_isolated_by_agent_namespace` | 不同 agent_id 不能互读 head/family |
| `test_latest_resume_replays_failed_and_aborted_terminal_results_without_an_id` | 省略 ID 的 Failed/Aborted 终态只读回放且保留实际结果 ID |
| `test_root_reconcile_requires_latest_head_confirmation` | root family receipt 存在但 latest head 无法证明时保持 `CommitUnknown`，不制造假 `AgentCompleted` |
| `test_historical_root_reconcile_survives_a_later_latest_head` | A 的 root/head 已确认后，即使 B 成为 latest，A 的延迟对账仍返回 `CommitApplied`，且 latest 保持 B |
| `test_root_reconcile_rejects_an_invalid_current_latest_chain` | 回退 head、generation 缺口或无回执 head 都不能把历史 root 误报为 `CommitApplied` |
| `test_answer_from_an_old_run_cannot_be_applied_to_a_new_latest_run` | 两个 run 使用同一 interrupt Graph；旧 run 的 answer 因 run-scoped interrupt identity 被拒绝 |
| `test_process_journal_isolates_agents_runs_and_sessions_across_processes`、`test_process_journal_keeps_multiple_families_in_one_persistence_instance` | 进程 journal 精确隔离 Agent/run/Session，不覆盖历史 family |
| `test_process_journal_latest_uses_the_generation_chain_not_append_order` | journal 乱序时仍由唯一、连续的 generation 链决定 latest，不由最后一条记录决定 |

### 13.2 既有回归必须保持

`test_lifecycle.py` 的 explicit Start/Resume、终态、interrupt、authority 并发测试；
`test_session.py` 的 successor/Config/nested family 测试；`test_cancellation.py` 的所有取消和
release 优先级；execution persistence 的完整 evidence admission。新增 latest 分支不能复制一套
Graph recovery runner，必须最终进入同一个 `Graph.run(recovery=...)`。

### 13.3 Definition of Done

- [x] `AgentResume(None)` 的 typed admission、latest lookup、expected-head fence 和实际结果 ID 已实现；
- [x] state/value/session/head 的原子提交**契约**及 Unknown/reconcile 有测试适配器边界测试（不等同生产事务）；
- [x] explicit key 完全绕过 latest，exact historical path 回归通过；
- [x] Config 仍 exact cursor，未引入 latest Config；
- [x] 无 head、不可用、tombstone、损坏、race 都 fail closed；
- [x] latest 不复制 GraphRunState、Session、continuation 或业务 DTO；
- [x] snapshot/journal/独立进程测试通过；
- [x] 中英文 README、架构和示例已同步；Session/persistence 计划继续沿用同一 checkpoint 契约；
- [x] `make check` 与 monorepo 根目录的 pre-commit 在本轮最终修改后重新通过；
- [x] 没有兼容 alias、字符串 discriminator、第二 runner、隐藏内存 latest cache。

以下是生产交付门禁，明确保持未完成：

- [ ] 生产 persistence adapter 实现 `load_latest` 的线性一致读和 `expected_latest` fence；
- [ ] root state、value evidence、Session、receipt 与 latest head 的同事务（或可证明等价）提交；
- [ ] Unknown/reconcile、迁移/backfill、tombstone/retention 和断电/网络故障验收；
- [ ] 真实 backend 的 schema、观测、回滚和上线演练。

## 14. 已同步修订的文档和示例

以下入口已补充 latest head 语义；跨新 run 的 Session 仍按首期边界要求显式交接：

- `docs/architecture.zh-CN.md`、`docs/architecture.md`：Agent 权限/恢复调用链；
- `docs/kernel-persistence-implementation-plan.zh-CN.md`：补充 latest head 与 checkpoint 的 owner 边界；
- `docs/agent-session-persistence-requirements.zh-CN.md`：保留同一 run 的 checkpoint 绑定，补充
  latest 选 key；跨新 run 的显式交接规则按首期边界说明；
- `docs/agent-session-code-review-acceptance.zh-CN.md`：新增 latest acceptance；
- `README.zh-CN.md`、`README.md`、`example/graph/README.md`：入口示例和 run boundary；
- `example/graph/durable_agent_import.py`：增加 `AgentResume()` 示例，并明确新 run 的 Session
  仍显式传入；
- `docs/cloudflare-resident-agent-lifecycle-design.zh-CN.md`：其中的 `DurableHead`/latest 是
  概念设计，不能直接当作当前 Port 实现；若同步，标注本方案的 `(agent_id, run_id)` 语义和
  exact Config 规则。

## 15. 风险和明确不做的事项

### 风险

1. **head 与 family 的事务能力**：没有原子或等价 durable 证明时，latest 可能指向半份数据；
   适配器必须返回 Unknown，而不是由 Agent 猜测。
2. **resolve/acquire 间竞态**：无并发 React 是业务假设，不是安全证明；generation fence 必须
   保留，未来可替换为原子 `acquire_latest`。
3. **旧数据没有创建顺序**：不能靠最大 run_id 猜 Session；需要人工映射或停用默认恢复。
4. **历史 run 修复的语义**：首期不自动 promote，防止当前会话回退；若业务需要须单独设计。
5. **跨新 run 的数据泄漏**：首期不隐式继承 Session；自动继承只能通过明确的 StartLatest 操作。
6. **Config 误用**：latest head 不得被实现为 latest Config；所有恢复仍以 checkpoint cursor 为准。

### 明确不做

- 不删除 `run_id`，也不把它降级成 transient React 计数器；
- 不新增 `conversation_id`、`session_id`、`react_id` 或第二套主键；
- 不在 Agent/Graph 成员中缓存 latest state/session；
- 不分别查询最新 state 和最新 Session；
- 不按字符串、时间或内存插入顺序猜 latest；
- 不把终态 Resume 变成新任务，不重跑已确认 producer；
- 不在 Config store 增加 latest fallback；
- 不改变 Graph 唯一执行门面、GraphRunState 唯一状态 owner、reducer 或 durable evidence 规则；
- 不添加兼容 alias、隐式 merge、第二执行路径或后端具体实现到 Kernel。

## 16. 首期评审门槛（默认值已给出）

为避免实现阶段重新发明语义，以下默认值即本文的施工基线；评审若要改变它们，应先修改本文档
和验收矩阵：

1. latest 默认采用“最近成功创建的 root run”，而不是最近成功 commit 的 run；
2. 首期只实现 `AgentResume(None)`，新任务仍由显式 `AgentStart(run_id, ..., session)` 创建；
3. 目标 persistence backend 必须能把 root commit、Session envelope、值 evidence 和 latest
   head 放入同一个事务或可证明的 reconcile 边界。

前两项（latest 定义和 `AgentResume(None)` API 边界）已由 Kernel contract 实现并通过测试；第三项
是生产 adapter 的上线门槛，尚未在本批交付。不得通过修改 `AgentStart` 签名或随机生成 ID 来绕过
幂等和身份设计。
