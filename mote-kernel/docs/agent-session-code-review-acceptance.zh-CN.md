# AgentSession 代码验收清单

这份清单用于 review `AgentSession` 跨节点、跨 run 和持久化改动。reviewer 应先看设计不变量，再看调用链，最后运行门禁；不能只因为测试通过就判定设计合格。

## 1. 验收范围

本批改动只涉及 `mote-kernel`，不需要复制或修改整个 monorepo。隔离 worktree 和基线以实际提交为准：

```text
worktree: /tmp/mote-kernel-agent-session-recovery/mote-kernel
baseline: 9dc65ec
implementation: 28999d2 (family Session owner closure; isolated worktree)
```

重点文件：

- `src/mote_kernel/session.py`：`AgentSession`、Session codec 和持久化 envelope；
- `src/mote_kernel/agent.py`：Start/Resume/Result、跨 run 交接和 Agent wiring；
- `src/mote_kernel/execution/persistence.py`：Graph commit/checkpoint 的原子 Session 边界；
- `src/mote_kernel/persistence.py`：PersistencePort 的原子提交契约；
- `tests/agent/test_session.py`：Session、恢复、跨 run 和异常边界；
- `docs/agent-session-persistence-requirements.zh-CN.md`：需求基线。

## 2. 必须成立的不变量

### 2.1 唯一 owner

- `AgentSession` 只有三个字段：`hook_state`、`context`、`config`。
- 不得出现 `domain_state`、`cursor`、`graph_state`、`continuation` 或第二份 Session 镜像。
- Session 由 Runtime/Agent 调用方持有；Graph、Hook、Agent 实例不得把它偷偷缓存为可变成员。
- `GraphRunState` 仍是 Graph 执行位置和 Graph 事实的唯一 owner；Session 不复制 Graph 状态。
- Observe 的 cursor 仍由 queue provider 持有；本批不把 cursor 放进 Session。

### 2.2 更新责任

- Kernel 不从 Graph output 猜测或合并 `hook_state`、`context`、`config`。
- 同一 run 内，节点只能通过 `Graph.success(..., session=...)` 或 typed
  `Graph.SessionActivation(value, session)` 显式产生完整 Session successor；Kernel 不 merge 三个业务字段。
- `AgentResume` 不接受外部 Session 覆盖，只恢复 checkpoint 中已确认的 Session。
- 跨 run 只有 Runtime 显式把上一次确认的 `AgentResult.session` 传给下一次 `AgentStart` 才会复用。
- 一次 run 内没有隐式的跨 run 全局缓存，也没有第二条执行路径或 `ReActChain`。

### 2.3 持久化边界

- 每个 Graph commit 的 `GraphRunState` 和 Session envelope 属于同一个 `GraphPersistenceCommit`、同一个 CAS/reconcile 请求和同一个 receipt。
- commit 未确认前，内存中的运行结果不能被当作新的已确认 Session。
- `CommitUnknown` 必须沿用现有 reconcile 机制确认原请求；不得另起一笔 Session 写入。
- checkpoint 恢复时，Graph 状态和 Session 必须来自同一个已确认边界；child reread 不能只比较 Graph 状态而漏掉 Session。
- 恢复时历史 frame 只保留业务值与其历史 Config provenance；当前 owner Session 由执行请求注入节点输入，历史
  frame 不得覆盖或伪造当前 Session。
- 当前实现采用完整快照，不要求实现 `AgentSessionDelta`、patch merge 或历史重放。

## 3. Durable 表示和 codec 边界

内存中的 `AgentSession.config` 是已解析的运行能力，不能直接把包含 Port/Callable 的 Config 序列化到 durable store。

当前约定是：

```text
AgentSession(hook_state, context, config)
        │ AgentSessionCodec.encode
        ▼
EncodedAgentSession(codec_id, version, payload, config_cursor)
        │ 与 GraphRunState 一起进入 GraphPersistenceCommit
        ▼
持久化 checkpoint
```

review 时确认：

- hook/context 由调用方提供的 `AgentSessionCodec` 编解码；
- Config durable 表示只有既有 `GraphConfigCursor`；
- codec identity/version、payload 类型和 Config cursor 都在边界重新 admission；
- decode 后必须通过确定性的 canonical round-trip 校验；
- 恢复先按 checkpoint cursor 解析 Config，再用同一个 codec 还原 Session；
- `AgentSession.config` 引用的 immutable Config snapshot 必须由 Config owner 在首次使用前保存；Kernel/Agent
  不替 Config owner 执行 save，也不把一次成功的 Graph commit 当作 Config snapshot 已保存的证明；
- 缺少所需 codec、错误 codec、错误 cursor 或损坏 payload 都产生明确的 typed error；
- 不得新增 pickle/json 全局隐式序列化路径，也不得把 codec 放进 Session 三个字段中。

## 4. 逐条行为验收

| 编号 | 场景 | 预期结果 | 证据 |
| --- | --- | --- | --- |
| A1 | `AgentStart` 不带 Session | 保持既有无 Session 行为，不改变 Graph 执行路径 | 既有 Agent 测试全绿 |
| A2 | `AgentStart(..., session)` | Session 先 admission，再参与本次 Graph 的每个 durable commit；节点 successor 只在确认后推进，结果携带最后一份已确认 Session；嵌套 child 的 completed/failed/aborted/awaiting-resume 也沿同一边界交接 | `test_agent_session_is_written_with_each_graph_commit_and_returned`、`test_node_session_successor_is_committed_and_reused_after_recovery`、`test_parallel_child_successor_cannot_be_overwritten_by_parent_inherited_session`、`test_parallel_explicit_successors_follow_durable_confirmation_order`、`test_nested_child_completion_preserves_its_last_session_successor`、`test_nested_child_failure_preserves_its_last_session_successor`、`test_nested_child_abort_preserves_its_last_session_successor`、`test_nested_child_interrupt_returns_its_last_session_successor` |
| A3 | 同一 run `AgentResume` | 不允许外部替换 Session；从 checkpoint 恢复 hook/context，并解析同一 Config cursor | `test_agent_session_is_recovered_from_the_same_graph_checkpoint` |
| A4 | run1 → run2 | Runtime 显式传 `run1_result.session` 后，run2 才复用；不传就不共享 | `test_runtime_can_reuse_the_confirmed_session_for_a_later_run` |
| A5 | Config 存在 | durable envelope 只有 cursor；恢复得到新的已解析 Config；普通 scoped commit 的 cursor 与其 Session 一致，嵌套等待恢复时 Session cursor 必须属于 family 的某个已确认 state | `test_session_persists_only_the_config_cursor_and_resolves_capabilities_on_resume`、`test_nested_child_interrupt_returns_its_last_session_successor` |
| A6 | commit/reconcile 未确认 | 不提前推进 durable/内存事实；reconcile 针对完全相同的 Graph+Session 请求，并保持 family owner 的串行顺序 | `test_parallel_child_successor_cannot_be_overwritten_by_parent_inherited_session`（含 Unknown 参数）、既有 commit unknown/reconcile 测试及 `GraphPersistenceCommit` admission |
| A7 | forged/malformed Session | 在 authority、Graph 执行或持久化写入前失败；不允许子类/伪造对象绕过 exact admission | `test_session.py` 的 admission/error tests |
| A8 | codec 非确定或 cursor 不匹配 | 首次 durable write 前或恢复时拒绝，不静默接受另一份 Session 或 Config | `test_agent_rejects_a_non_decodable_session_before_the_first_durable_write`、codec round-trip、commit/checkpoint binding tests |
| A9 | 并发不同 run | 不因共用 Agent/Graph 实例而共享 Session；每个 run 只使用自己的显式输入 | 既有并发 Agent/Graph 测试 + 静态检查 Agent 无 runtime cache |
| A10 | Observe cursor | cursor 仍由 Observe queue provider 保管，Session/Agent/Graph 不接管 | Observe cursor 测试和 `rg` owner 检查 |

## 5. 明确不属于本批的内容

以下任一项被实现或被偷偷引入，都应要求返工：

- `ReActChain`、第二个 runner、第二个 reducer 或第二个 Graph state owner；
- `AgentSessionDelta`、patch/replay、自动 merge 或隐式 successor 推断；
- 把 Session 放进 Graph/Hook/Agent 成员作为可变缓存；
- 把 `cursor` 加回 Session，或让 kernel 代替 Observe provider 推进 cursor；
- 为旧 API 增加 alias、wrapper、双执行路径或仅为 legacy test 保留兼容分支；
- 将 `GraphRunState`、continuation、execution lease 塞进 AgentSession；
- 直接持久化含 Port/Invocation 的运行时 Config；
- 只更新 Python 对象、不更新 authoritative store，或在 commit receipt 确认前暴露 successor。

节点显式更新 Session 已纳入本批，但仍不允许 Kernel 从普通 Graph output 猜测或自动合并 successor；未提供 successor
的节点只继承当前 owner Session。

## 6. 代码 review 顺序

1. 先检查 `AgentSession` 的三个字段和 owner 是否唯一；
2. 沿 `Agent.run → persistence.load → config/session recovery → Graph.run → DurableGraphCommit → PersistencePort.commit/reconcile → AgentResult` 走一遍成功、失败和恢复路径；
3. 对照 A1–A10 检查测试是否验证了行为，而不是只验证字段存在；
4. 检查异常边界：输入 admission、Config resolution、codec、Graph snapshot、persistence conflict/unknown 不得互相吞错；
5. 最后检查 diff 是否只改了本需求相关文件，没有顺手修改主工作树或引入无关兼容层。

## 7. 建议执行的门禁

在本 worktree 执行：

```bash
make check
pre-commit run --all-files
```

最低通过标准：

- Ruff lint/format 通过；
- Pyright 无错误；
- architecture complexity/semantic gate 通过且没有新增未解释 owner；
- 全量测试通过，覆盖率保持 100%；
- package build/twine check 通过；
- 与 monorepo 相关的 hook 若因只复制 kernel 无法执行，必须在 review 结论中明确记录，不能冒充已通过。

## 8. Review 结论模板

```text
[ ] 设计 owner 唯一，Session 恰好三个字段
[ ] Graph 状态与 Session 使用同一 commit/reconcile 边界
[ ] AgentResume 只恢复 checkpoint Session，跨 run 只接受显式交接
[ ] Config 只按 cursor durable 化，未序列化运行能力
[ ] codec/admission/error/recovery 边界完整
[ ] 无 cursor/ReActChain/隐式缓存/delta/legacy 双路径
[ ] 测试和全部可执行门禁通过
[ ] 无未解释的额外复杂度或无关 diff

结论：PASS / NEEDS_CHANGES / BLOCKED
问题（按严重度排序）：
1.
2.
```

## 9. 历史整改记录（2026-09-11；不作为当前结论）

本节保留首轮 successor/recovery 整改的历史证据。它描述的是当时的中间实现和门禁结果；当前 family
Session owner 的最终语义、问题关闭状态和最新门禁，以第 12 节为准。

首轮 review 的三个阻断项已按同一调用链收口：节点 successor 进入现有 `TaskSuccess → GraphTransition →
DurableGraphCommit`；`GraphRecovery` 同时绑定 checkpoint/commit 的 Session envelope，并用 codec 做完整
payload canonical round-trip；Session codec 在首次 durable write 前即完成 decode/re-encode admission。
恢复 materialization 不再把当前 Session 写入历史 frame，而是由 scoped owner 注入当前 Session。

### 首轮问题及关闭证据

#### P0-1：Session successor 链（已关闭）

`TaskSuccess.session` 现在明确区分“继承输入 Session”和“节点产生的完整 successor”。
`_GraphRun` 只有在 `confirm_transition` 成功后才替换 owner snapshot；`DurableGraphCommit` 对 successor
重新编码，并将其放入同一个 `GraphPersistenceCommit`。Config successor 也必须由节点写入完整
`AgentSession.config`，因此不会再出现旧 Session 与新 Graph Config cursor 的错绑。

现已沿用现有 Graph reducer/commit 路径加入 typed successor 通道；每个 candidate state 与同一边界的完整
Session 一起提交、reconcile，`AgentResult.session` 从最后确认的 owner snapshot 投影。没有增加 reducer、
隐式 merge 或 Graph 外部缓存。

#### P0-2：`GraphRecovery` Session 绑定（已关闭）

`GraphRecovery.__post_init__()` 现在先分别 admission，再要求 checkpoint 与 bound commit 的
`agent_session` envelope 完全相等；存在 envelope 时还必须有 codec，并将传入 decoded Session 重新编码后
逐字节比较。

现已在 recovery admission 对两个 envelope（包括 `None` 组合）做 exact 相等绑定，并对 decoded Session
执行 codec re-encode 全 payload 比较；新增 A/B、A/None、None/B、同 cursor 不同 payload 负例。

#### P1-1：首次 codec admission（已关闭）

`AgentSessionCodec.encode()` 现在在返回 envelope 前完成 encoder → decoder → re-encoder 的 canonical
round-trip；`AgentStart` 的首次编码因此会提前暴露坏 decoder、非互逆或非确定 codec。

canonical admission 已放到首次 `GraphPersistenceCommit` 之前；坏 decoder、非互逆 encoder、非确定 encoder
均在任何 durable write 前失败。

### 复审结论与长期前置条件

- `Agent` 的 `session_codec` 已明确设为 keyword-only；没有为旧 positional 调用增加 alias 或 wrapper。
- Agent request/result 的泛型只用于静态关联，运行时 admission 仍要求 exact `AgentSession`；不要把泛型参数
  当成第二份运行状态。
- A6/A8 的 Session bytes 复用同一个 `GraphPersistenceCommit` equality、Unknown/reconcile 和 receipt 机制；
  新增 recovery envelope 负例证明它不是独立写入。后续若新增后端实现，必须复用同一 commit 测试矩阵。
- `AgentSession.config` 的 immutable snapshot 保存由 Config owner 负责，且必须先于
  `AgentStart` 或节点产生 Config successor；这是已经写入 contract 的前置条件，不是 Kernel 的隐式 save
  路径。恢复缺快照时按既有 Config store 错误失败，不静默降级。
- `pyproject.toml` 的 complexity ratchet 记录的是本次完整调用链复审后的实测值：decision points
  `3782→3866`、cognitive complexity `4797→4915`（其余指标同步为实测值）。这不是备用预算，也不是用阈值
  证明设计质量；人工复审确认没有为压指标新增薄转发、别名或第二执行路径。

### 门禁证据（整改后更新）

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `make check` | **通过** | Ruff、Pyright、architecture/complexity gate、3058 tests、100% 行/分支覆盖、sdist/wheel 与 Twine 全部通过。 |
| `make test` | **通过** | 3058 passed，100.00% coverage（4530 branches，0 partial）。 |
| Session/Agent 专项与相关子集 | **通过** | successor、recovery envelope、首次 codec admission、child failure/abort 负例均已加入。 |
| `pre-commit run --all-files`（隔离 monorepo 根） | **通过** | 基础、Kernel、Rust、Local Execution、Cloudflare static 与 secrets hooks 全部通过；Cloudflare 依赖由本机离线 pnpm cache 安装到隔离 worktree。 |
| `git diff --check` | **通过** | 无空白错误。 |

当时的历史结论：`PASS`。这不是当前提交的独立 review 结论；当前提交应按第 12 节重新复审。

三个首轮阻断项均已关闭；owner、完整调用链、异常边界与复杂度已人工复审，Kernel `make check` 和隔离
monorepo 根目录 `pre-commit run --all-files` 均已通过（历史记录）。门禁结果只作为无回归证据，不替代上述设计判断。

## 10. 历史复审整改记录（2026-09-11；不作为当前结论）

本节保留嵌套 child Config successor 整改的历史证据。并行 family Session owner 覆盖问题及其修复不在本节
结论中，统一以第 12 节为准。

本轮针对后续复审发现的嵌套 child Config successor 边界，在 `3a850bc` 收口：

- child 在进入 `AwaitingResume` 前已确认的 Session successor 会交给父 owner；不会因 child 暂停而把父
  owner 退回旧 Session；
- checkpoint 的 Session Config cursor 现在只要求属于整个 family 的某个已确认 scoped state；父 root
  state 暂时保留旧 cursor 的等待恢复形态仍可恢复；
- 恢复 child boundary 时显式使用当前已确认 Session，历史 frame 只保留自己的业务值和 Config provenance；
- 初始 revision 仍严格要求 Session Config 与 candidate state 一致，避免放宽初始绑定；
- 新增 child Config successor、child interrupt + resume、family cursor admission 和缺失 Config resolution
  测试，没有增加 merge、alias、wrapper 或第二执行路径。

本轮实际门禁证据：

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `make check` | **通过** | Ruff、Pyright、architecture/complexity gate、3063 tests、100% 行/分支覆盖、sdist/wheel 与 Twine 全部通过。 |
| `pre-commit run --all-files`（隔离 monorepo 根） | **通过** | Kernel structural ratchet、Rust、Local Execution、Cloudflare static 和 detect-secrets 全部通过。 |
| `git diff --check` | **通过** | 无空白错误。 |

当前状态：实现提交已完成，等待独立 code review；本节只记录实现和门禁证据，不替 reviewer 预先给出最终设计结论。

### 并行 child Session 规则（需求方已确认）

需求方已明确接受以下语义：本轮保持单一完整 Session successor 路径，不做字段级自动 merge，也不引入
delta、patch 或冲突合并状态机。若同一 superstep 的多个并行 child 同时产生不同的完整 Session successor，
各份 successor 仍分别沿现有 Graph commit/reconcile 路径确认；确认顺序中的最后一份完整快照是唯一有效的
Session，并成为后续节点输入和 `AgentResult.session`。

这不是把两份 Session 拼字段，而是沿既有持久化确认顺序确定唯一 owner；节点仍必须返回完整 successor，Kernel
不猜测哪些字段“没改”。

## 11. 需求方确认的整改设计与当前实现（2026-09-12）

首轮 `NEEDS_CHANGES` 指出的 family checkpoint 旧 Session 覆盖问题，已按需求方确认的设计在隔离 worktree
收口。以下语义是本轮独立 review 的判定标准：一次 Graph invocation/family 只有一份当前
`AgentSession`；root、child、sibling 共享它，不各自持有可以回写 family checkpoint 的 Session 镜像。普通节点
可以继续使用激活时看到的旧 Session，但无 successor 的 transition 在进入提交边界时必须重新读取 family owner
当前值；只有节点显式返回的完整 successor 才推进 owner。多个完整 successor 不做字段 merge，按实际 durable
confirmation 顺序最后确认的一份生效。`CommitApplied`、`CommitUnknown` 的 reconcile、明确未应用、异常和取消
均经过同一串行边界；恢复只从 checkpoint/commit 绑定的事实构造 owner。

当前实现的关键收口：

- `family_driver.py` 的 `_FamilySessionOwner` 是唯一 live Session owner，并持有 family 内唯一 lock。Start、
  普通 settlement、fence、resume、abort、construction cleanup 都统一经过“读取当前 owner → 准备
  transition/frame → durable commit/reconcile → 确认后更新 owner → 锁外安装 state/frame”；
- `_GraphRun` 不再保存 `_agent_session`，child terminal 不再转发 Session 镜像；无 successor 的 parent/sibling
  提交会在锁内读取已经确认的 child successor；
- `GraphInputFrame`、`NodeOutputFrame`、`GraphOutputView` 和历史持久 frame 不再保存 Session 镜像。只有临时
  `NodeInputFrame` 注入 family 当前 Session；历史 frame 继续只拥有业务值和 Config provenance；
- `Graph.success(..., session=...)` 和 typed `Graph.SessionActivation` 是显式 successor 边界；即使 successor
  与激活输入值相等，也不会被当成“只是继承”。显式 `config=None` 会清除历史 Config provenance；
- `GraphRecovery`、codec、Config cursor 和同一 `GraphPersistenceCommit` 路径保持不变，Session owner 从已绑定的
  checkpoint Session 初始化；未引入 alias、wrapper、delta/patch merge 或第二 runner。

新增的 snapshot/journal 参数化回归固定了“child 已确认 B → parent 仍以 A 激活但无 successor”的交错，并断言
后续 commit、checkpoint、AgentResume 和最终 AgentResult 全部保持 B；同时覆盖 CommitUnknown→reconcile、两个
并行显式 successor 的确认顺序、显式同值 successor 和清除历史 Config。相关专项测试在当前隔离 worktree 已通过。

当前整改门禁状态（独立复审前必须重新执行）：

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 相关 Session/Agent 专项测试 | **通过** | 169 passed（含 snapshot/journal、Unknown/reconcile、并行 successor、同值 successor）。 |
| `make typecheck` | **通过** | Pyright 0 errors。 |
| `make check` | **通过** | Ruff、Pyright、complexity/semantic gate、3076 个全量测试、100% 行/分支覆盖、sdist/wheel 与 Twine 全部通过。 |
| `pre-commit run --all-files`（隔离 monorepo 根） | **通过** | 基础、Kernel、Rust、Local Execution、Cloudflare static 与 detect-secrets hooks 全部通过；Cloudflare 依赖使用锁文件离线安装到隔离 worktree。 |
| `git diff --check` | **通过** | 无空白错误。 |

本节记录需求方已确认的设计和当前实现，不预先替独立 reviewer 给出最终 `PASS`；门禁结果只用于证明无回归，不能替代
owner 唯一性和完整调用链的人工判断。

## 12. 2026-09-13 P1 durable-boundary 收口

本轮只收口两个分布式提交边界，不增加 hostile-network/authenticity 层，也不改变 Graph/State owner：

- `GraphSessionReceipt` 是 Session 与 scoped `GraphCommitKey` 的唯一 durable provenance。`GraphPersistenceCommit` 要求
  encoded Session 与 receipt 成对出现；receipt evidence 的 canonical commitment 同时覆盖 scope、run/revision、
  codec/config cursor 与 payload，不能通过重标记字段复用。`GraphCheckpoint` 还要求 receipt
  指向实际包含的 scoped state 和相同 revision。child 已确认新 Session、parent 暂存旧 state 的语义仍通过 scoped
  receipt 保留。
- 有活动 Session 的 scoped Graph Config transition 必须由同一 transition 携带完整 Session successor，且 successor
  cursor 与 candidate state 一致；durable request 同时携带前一 scoped Config cursor，只有真实 cursor successor 才触发
  该检查；只返回 `ConfigActivation`/普通值而不带 successor 会在 durable commit 前失败。无
  Session 的 family 不改变原有路径，显式清除 Session Config 的既有语义保留。

新增回归覆盖：旧 Session + 新 Graph checkpoint、Session/receipt 缺失或错绑、malformed receipt、decoded Session
payload 不一致、Config transition 缺 successor、显式 successor 原子提交，以及直接持久化请求的 Config/Session
不一致。所有测试复用现有 `GraphTransition → DurableGraphCommit → PersistencePort` 链，没有兼容 alias、wrapper 或
第二执行路径。

本节的门禁数字须以当前工作树最后一次 `make check` 为准；文档不以测试全绿替代对 receipt provenance 和完整调用链
的人工复核。
