# Frontier 节点级结算当前实现代码验收报告

> 再次验收更新（2026-08-15）：本节是当前结论，覆盖下文首次验收时的“不通过”判定；下文原记录保留为问题发现与修复依据。

## 0. 再次验收结论

**三个代码 correctness 阻断项均已关闭，非阻断 cancellation 加固已吸收，当前实现与全部交付门禁通过验收。**

本轮以当前工作树重新检查实现、确定性交错测试、文档唯一真相关系和完整项目门禁。未发现仍会阻止 node-level settlement、linear session
capability、single-consumer completion、资源即时调度或恢复屏障成立的新代码缺陷。首次验收第 3 章的三项阻断均有对应生产修复和独立回归测试，
不是仅通过调整文档或覆盖率口径关闭。

当前环境已安装 `cargo 1.85.0` 与 `rustfmt 1.8.0-stable`。未设置 `SKIP`、未注入临时 PATH 的标准 monorepo
`pre-commit run --all-files --show-diff-on-failure` 已完整通过，包括 `rustfmt`；`make check` 与 whitespace gate 同样通过。因此首次复验保留的
环境条件已经关闭。

### 0.1 首次验收阻断项关闭情况

| 首次阻断项 | 再次验收 | 代码与行为证据 |
| --- | --- | --- |
| 公开 Session 构造器绕过 linear claim | 已关闭 | 公开 `GraphExecutionSession` 已变为不可实例化的 runtime-checkable `Protocol`，concrete `_GraphExecutionSession` 不再公开；`GraphExecutor.execute()` 先验证 request/task scope，再 owner-check 消费 claim；`ConsumedExecutionClaim.issue()` 只允许签发一次 session |
| 同一 session 并发 `next()` 破坏 scheduler | 已关闭 | `_next_in_progress` 在 acknowledgment 和 scheduler 之前拒绝第二个消费者；scheduler 使用 await 前冻结的 `by_handle` 映射收集完成项，不再从已被并发修改的 `_live` 反查；并发 `aclose()` 由 `_close_lock` 串行化 |
| queued completion 延迟 newly admitted waiter | 已关闭 | session 先提取 queued `TaskRaised`；若剩余为 typed completion，则基于刚确认的 authoritative successor 补满 scheduler slot，并在返回 completion 前让新 task 实际开始；queued ordinary error 仍优先进入 `ERROR_DRAINING`，不会越过错误启动 waiter |

对应关键位置：

- `src/mote_kernel/execution/claim.py:37-61`、`:97-111`；
- `src/mote_kernel/execution/engine/session.py:55-76`、`:249-317`、`:320-333`；
- `src/mote_kernel/execution/engine/scheduler.py:71-108`；
- `src/mote_kernel/execution/executor.py:106-115`。

对应确定性回归包括：

- public protocol 不可构造、一次性 consumed receipt 和同一 prepared claim 并发消费只有一个 winner；
- 一个 `next()` 等待 completion 时，第二个 `next()` 在 scheduler 前得到 `ResultCollectionError`，node invocation 保持一次；
- active `next()` 与两个 `aclose()` 并发只取消/清理一次，不再泄漏 `StopIteration` 或 collection race；
- 同一个 `next()` task 在 cancellation cleanup 期间再次被 `cancel()`，仍等待独立 close task 和 worker cleanup 完成后再传播 cancellation；
- owner、resource-free sibling 同时完成时，应用 owner settlement 后，下一次 `next()` 返回 queued sibling 前 waiter 已启动；
- 同一交错下若 queued sibling 是 ordinary error，waiter 不启动。

本轮单独执行 `test_session.py`、`test_runtime_boundaries.py` 和 `test_executor.py`，结果为 **93 passed**。

### 0.2 文档与迁移证据复核

首次验收第 4.2 节的文档问题也已关闭：

1. `docs/architecture.md` 与 `docs/architecture.zh-CN.md` 已记录 token-only lease、atomic claim、state-acknowledged session、单节点
   settlement、资源 release/admit 和 stable `SETTLED` barrier；
2. `docs/frontier-node-recovery-requirements.zh-CN.md` 已明确为历史需求基线，并声明旧 batch settlement 口径由当前节点级方案替代；
3. `docs/frontier-node-resume-implementation.zh-CN.md` 已明确为被替代的历史实施基线；
4. 旧 `requires_atomic_completion` / `atomically_advance` 测试名称已不存在；
5. 当前工作树实际收集 **522 tests**，与实施方案和评审回复的终版数字一致；未发现新增 legacy symbol/file/import absence 或全仓字符串扫描门禁；
6. 本次变化仍是 Python internal execution/state contract，`conformance/` 无需更新，符合 monorepo owner boundary。

### 0.3 再次验收门禁

执行 `make check`：通过。

- Ruff lint：通过；
- Ruff format check：109 files already formatted；
- Pyright strict：0 errors、0 warnings、0 informations；
- Pytest：522 passed；
- Coverage：2,105 statements、0 missed；676 branches、0 partial；statement/branch 100%；
- sdist/wheel build：成功；
- Twine package check：两个产物均通过。

执行标准 monorepo `pre-commit run --all-files --show-diff-on-failure`：全部通过。large file、case conflict、merge conflict、TOML、YAML、
EOF、line ending、trailing whitespace、Ruff、Ruff format、`rustfmt` 和 detect-secrets 均未跳过。

`git diff --check`：通过。

### 0.4 非阻断加固建议处置

该建议已吸收。`next()` 捕获 cancellation 后创建独立 close task，并用 shielded wait 抵抗 cleanup 期间的再次 cancellation；只有 close task
完成、scheduler live handles 清空后才重新传播原 cancellation。新增确定性测试让 node 在 cancellation cleanup 中受控阻塞，对同一 `next()` task
执行第二次 `cancel()`，证明 task 不提前结束、`session.quiescent` 在清理完成前保持 false，释放 cleanup 后 session 才进入 closed/quiescent。

这一加固只强化现有 execution-local cancellation lifecycle，没有新增 durable receipt、Store、Port 补偿或 exactly-once 语义。

### 0.5 当前最终意见

首次验收提出的代码修复、行为测试和 authoritative 文档同步要求均已满足。当前结论更新为：

**代码、测试、文档与未跳过任何 hook 的完整交付门禁均通过，可以提交。**

## 1. 验收信息

- 验收对象：`docs/frontier-node-settlement-implementation.zh-CN.md` 对应的当前工作树实现；
- 关联评审：`docs/frontier-node-settlement-implementation-review.zh-CN.md`；
- 关联回复：`docs/frontier-node-settlement-implementation-review-response.zh-CN.md`；
- 验收日期：2026-08-15；
- 验收范围：`state.graph_state`、唯一 `execution` graph engine、resource、nested、routing、recovery、公开 API、测试迁移与交付门禁；
- 验收方式：只读代码审核、当前与 `origin/main` 测试收集对比、本地确定性最小反例、完整项目门禁；
- 变更边界：未修改被验收的 production code 或 tests；本文件只记录验收结果。

## 2. 总体结论

**代码验收不通过，当前实现不能认定为“Implemented（复审通过，终版代码与验证已完成）”。**

State/reducer 主链已经完成了本次模型替换的大部分核心目标：token-only lease、atomic claim、单节点 settlement、资源释放与 waiter
推进、stable `SETTLED`、standalone routing resolution、nested terminal projection、resource canonical `None` 和 ordinary error draining
均已落在唯一 owner 中。旧 batch collector、resource wave executor 和 settlement 内联 resolution 也已删除，未发现第二 runner、兼容 alias、Store、
retry、output persistence 或 multi-worker lease 等越界实现。

但是，验收发现三个可稳定复现的 session/scheduler correctness 缺口：

1. 公开的 `GraphExecutionSession` 构造器可以绕过 claim 的 owner 与一次性消费，使用同一个 claim 建立多个 session，并对同一 node 执行两次；
2. 同一 session 并发调用 `next()` 没有线性化保护，会破坏 scheduler 内部状态并暴露非协议异常；
3. scheduler 中已有 sibling completion 排队时，新 authoritative state 中刚 admitted 的 resource waiter 不会在下一次 session 调用立即启动。

前两项破坏“唯一 execution entry point”和显式 session capability；第三项直接不满足实施方案完成定义第 5 项。它们都不应以 100% statement/branch
coverage 或全部现有测试通过为理由放行。

## 3. 验收阻断项

### 3.1 高：公开 Session 构造器绕过 linear claim，允许同一 attempt 重复执行

涉及代码：

- `src/mote_kernel/execution/__init__.py:6`、`:48`；
- `src/mote_kernel/execution/engine/session.py:72`；
- `src/mote_kernel/execution/engine/session.py:116`；
- `src/mote_kernel/execution/executor.py:106`；
- `src/mote_kernel/execution/claim.py:47`。

`GraphExecutor.execute()` 会调用 `PreparedExecutionClaim.consume()`，以 executor owner、request attempt、committed token、revision 和 resources
证明 claim 只能消费一次。但 `GraphExecutionSession` 同时被顶层 `mote_kernel.execution` 公开导出，其公开构造器只保存 claim；首次 `next()`
只核对 state token，没有调用 `consume()`，也没有证明：

- claim 属于当前 executor owner；
- claim 已由唯一 `GraphExecutor.execute()` 消费；
- 当前 session 是该 claim 唯一创建的 session；
- claim task scope 与 session preparation 一致。

因此可以直接跳过唯一 entry point：

```python
one = GraphExecutionSession(graph, request, prepared.claim)
two = GraphExecutionSession(graph, request, prepared.claim)
result_one, result_two = await asyncio.gather(
    one.next(claimed_state),
    two.next(claimed_state),
)
```

本地最小反例的稳定结果为：

```text
node invocation count = 2
result_one.command == result_two.command = True
prepared.claim.consumed = False
```

两个 session 对同一 node、同一 execution token 和同一 expected revision 各自产生一个 command。Reducer 的 stale revision guard 只能拒绝第二条
command，不能撤销此前已经发生的第二次 Port 副作用。这不是 crash 后的新 attempt at-least-once，而是同一个未消费 claim 在同一进程内被并行执行。

#### 必须修复

Session 实例必须只能由 executor 完成 owner-checked linear consumption 后创建。可采用私有 concrete session + 公开只读协议、executor-issued 私有
factory capability 或等价的不可绕过设计；仅检查 `claim.consumed is True` 不够，因为同一个已消费 claim 仍可能被重复传给多个公开构造器。

至少增加以下反例测试：

1. 公共 API 无法直接从 prepared/consumed claim 建立第二个 session；
2. 同一 claim 的并发 `execute()` 仍只有一个成功；
3. 另一个 executor owner、错误 task scope 和已消费 claim 不能通过任何 session creation surface 启动 node；
4. 拒绝发生在 node invocation 之前。

### 3.2 高：`GraphExecutionSession.next()` 没有单消费者/重入保护

涉及代码：

- `src/mote_kernel/execution/engine/session.py:217`；
- `src/mote_kernel/execution/engine/scheduler.py:82`；
- `src/mote_kernel/execution/engine/scheduler.py:93`。

`next()` 会修改 `_awaiting_ack`、`_state`、`_started`、`_nested`、`_errors` 和同一个 `_scheduler`，但没有 session-level lock 或
in-flight gate。`TaskScheduler.next_completion()` 也假定只有一个消费者：多个 waiter 可以同时等待同一 `_live` handle；第一个消费者删除 handle 后，
第二个消费者仍处理同一 done set，并在 `_live` 中找不到对应 executable。

本地对同一 session、同一 claimed state 并发调用两次 `next()`，稳定结果为：

```text
first  -> ExecutedGraphNode
second -> RuntimeError: coroutine raised StopIteration
```

这不是定义过的 `ResultCollectionError`、ordinary node error 或 cancellation，也没有经过 session disposition 协议。Session 会留下由调度时序决定的
局部状态，`next()`/`aclose()` 并发时也继续共享同一组无保护的 scheduler collections。

#### 必须修复

`GraphExecutionSession` 必须明确实行单消费者线性协议：并发 `next()` 应在触碰 scheduler 前确定性 fail closed，或被安全串行化并在取得锁后重新验证
acknowledgment。`next()`、`aclose()` 和 disposition 转换必须共享同一个生命周期协调边界，不能只给 scheduler 的某个方法局部加锁。

至少增加：

1. 同一 session 并发 `next(claimed_state)` 的确定性反例；
2. 一次调用等待 completion 时，第二次 `next()` 的确定性行为；
3. `next()` 与 `aclose()` 并发时只完成一次 cleanup，且不泄漏内部 `StopIteration`/collection race；
4. 任意失败分支均不从同一 revision 交付第二条 command。

### 3.3 高：排队 completion 阻止 newly admitted waiter 在下一次调用启动

涉及代码：

- `src/mote_kernel/execution/engine/session.py:233`；
- `src/mote_kernel/execution/engine/scheduler.py:87`；
- `src/mote_kernel/execution/engine/scheduler.py:91`；
- `tests/execution/engine/test_session.py:151`、`:177`、`:211`。

Session 当前只在 scheduler 没有 pending events 时运行 selector：

```python
if not self._scheduler.has_pending_events:
    selected = self._select_ordinary()
    self._scheduler.submit(selected)
```

Scheduler 在多个 handle 同时完成时，会一次 harvest 全部 done handles，把 canonical 第一项返回，并把其余 completion 放入 `_events`。考虑以下
确定性场景，`max_parallel_tasks=2`：

```text
A  持有资源 R
B  等待资源 R
X  resource-free

A 与 X 同时完成
next() 交付 A，scheduler 将 X 留在 _events
apply SettleGraphNode(A) -> B 在 authoritative state 中 admitted
next(after_A) -> 直接交付 X，不运行 selector，B 仍未启动
```

本地反例输出：

```text
second completion = X
B.started = False
```

这与实施方案以下硬约束直接冲突：

- §8.3：session 下一次收到 successor state 时 selector 必须立即返回 B；
- §9.4：确认 state 后使用 selector 找到新可执行但未启动的 ordinary nodes；
- §13.6 第 4 项：应用 A command 后的下一次 session step 必须启动 B；
- §16 第 5 项：newly admitted waiter 在最新 state 被确认后立即进入同一 scheduler。

同一问题也会让无资源的第三个 Pending node 在已有 completion 排队时空置可用 slot。它不是 resource reducer 问题；B 已经在正确的 durable revision
中 admitted，延迟发生在 session completion queue 与 selector 的编排边界。

#### 必须修复

下一次 acknowledged state 到达后，session 必须同时正确处理已有 completion 与新 runnable selection。实现需要先区分 typed ready event 与
`TaskRaised`：ordinary error 必须在启动新 activation 前转入 `ERROR_DRAINING`；若 ready event 是 typed completion，则可在返回该 completion 前按
最新 authoritative state 补满 scheduler slots。不得为了即时调度而越过尚未观察的 ordinary error。

必须新增一个同时完成的 owner/sibling/waiter 测试，并明确断言：第二次 `next(after_A)` 返回前 B 已启动，而 A command 应用前 B 未启动。

## 4. 测试与文档完成度问题

### 4.1 中：现有 100% coverage 没有覆盖三项 session 交互边界

当前 `test_session.py` 覆盖了顺序调用下的 waiter admission、max parallel、close、cancellation、acknowledgment 和 error draining，但没有覆盖：

- public session construction 不能绕过 `GraphExecutor.execute()`；
- 同一 session 的 concurrent/reentrant `next()`；
- queued typed completion 与 newly admitted waiter 同时存在；
- `next()`/`aclose()` 共享 scheduler 时的单消费者保证。

因此现有 2,047 statements、664 branches 的 100% 覆盖率成立，但不能证明跨 coroutine interleaving 或 capability linearity。上述反例不需要 monkeypatch
production code，也不依赖随机压力测试，均可用 `asyncio.Event` 和固定 task order 确定性复现。

### 4.2 中：authoritative 文档与当前交付证据未同步

实施方案 §12.3 和 Phase 5 明确要求更新 architecture 文档，但 `docs/architecture.md` 与 `docs/architecture.zh-CN.md` 没有随本次工作树修改，也没有记录
node settlement、stable `SETTLED`、state-acknowledged session 或 standalone resolution。

同时，`docs/frontier-node-recovery-requirements.zh-CN.md` 仍显示 `Ready for re-review`，并继续把以下旧模型写成有效要求：

- `GraphExecutionLease.node_ids` 精确覆盖 Pending nodes；
- `SettleGraphExecution` batch outcomes；
- final settlement 与 routing resolution 原子提交。

当前只把 `frontier-node-resume-implementation.zh-CN.md` 标记为历史实施基线，没有把仍呈现为有效状态的旧 requirement 口径与新 authoritative
specification 关系说明清楚，形成两套互相冲突的文档真相。

交付数字也已漂移：实施方案和评审回复记录“终版 509 tests”，当前工作树实际收集并通过 514 项；`origin/main:mote-kernel` 的独立归档实测为
504 项。增加测试本身不是问题，但“终版验证已完成”的证据必须对应当前工作树。另有两个迁移后的测试仍使用
`requires_atomic_completion` / `atomically_advance` 名称，实际断言已经是两个 revisions，容易继续传播旧 atomic-resolution 语义。

#### 必须处置

1. correctness 阻断项关闭前，将实施方案和评审回复的“Implemented / 已完成”状态改为待复验，或由本验收报告明确覆盖该结论；
2. 更新中英文 architecture 文档，记录真正稳定的 owner 与 durable barrier；
3. 明确旧 requirements 的历史/被替代状态，避免与唯一 authoritative specification 冲突；
4. 使用当前最终工作树重新记录测试数量与门禁，不手工保留旧数字；
5. 修正仍表达旧 atomic resolution 的测试名称。

## 5. 完成定义逐项复判

| 完成目标 | 判定 | 验收说明 |
| --- | --- | --- |
| 每个 typed completion 独立形成 `SettleGraphNode` | 通过 | 顺序 session path 与 reducer 均成立 |
| settlement 原子更新 Frontier、resources、execution | 通过 | reducer 同 revision release/admit，最后 Pending 清 lease/resources |
| resource release 后 waiter 在 next state admitted | 通过 | authoritative resource reducer 正确 |
| newly admitted waiter 下一次调用立即进入 scheduler | **不通过** | queued sibling completion 会跳过 selector |
| 有/无资源节点使用唯一 scheduler | 部分通过 | 只有一个 scheduler owner，但 queued-event 编排未满足动态选择语义 |
| final settlement 与 resolution 分为两个 revisions | 通过 | stable `RUNNING + SETTLED` 与 `ReadyToResolve` 成立 |
| recovered `SETTLED` 可独立 resolve | 通过 | routing projection 与恢复测试成立 |
| Frontier/resources/token 无重复 durable truth | 通过 | durable model 已收敛；问题位于 runtime capability，不是第二 durable DTO |
| `GraphExecutionSession` close/cancel/quiescence 闭合 | **不通过** | 公开构造绕过与 concurrent `next()` 未形成线性 lifecycle |
| nested terminal 使用唯一 completion/settlement path | 通过 | precomputed completion 不占普通 task slot |
| `CompleteGraphFrontier` 保留 join guard | 通过 | non-empty `join_progress` 原子拒绝 |
| 无 acquisition 只有 durable `resources=None` | 通过 | stable validator、claim 和 final release 均闭合 |
| ordinary error 停止未启动 activation且确定性暴露 | 顺序路径通过 | 现有 error-draining tests 成立；修复 queued event 时必须保持此保证 |
| 旧 batch/wave/inline-resolution path 完整删除 | 通过 | 未发现 compatibility wrapper 或第二 runner |
| 测试、文档与完整门禁证明当前终版 | **不通过** | 缺少三项行为测试，文档数字漂移，root pre-commit 未完整执行 |
| 未实现 Store/retry/exactly-once/output persistence 等越界能力 | 通过 | 范围控制成立 |

## 6. 质量门禁记录

### 6.1 `mote-kernel` 项目门禁

执行：

```bash
cd /home/longert/motev2/mote-kernel
make check
```

结果：通过。

- Ruff lint：通过；
- Ruff format check：109 files already formatted；
- Pyright strict：0 errors、0 warnings、0 informations；
- Pytest：514 passed；
- Coverage：2,047 statements、0 missed；664 branches、0 partial；statement/branch 100%；
- sdist/wheel build：成功；
- Twine package check：两个产物均通过。

独立从本地 `origin/main:mote-kernel` 归档并执行 collect-only：504 tests。当前相对基线净增 10 collected items；该数字只用于核对交付记录，不能替代
逐 case 语义审计。

### 6.2 Monorepo pre-commit

执行：

```bash
cd /home/longert/motev2
pre-commit run --all-files --show-diff-on-failure
```

结果：未完整通过，退出码 1。

- large file、case conflict、merge conflict、TOML、YAML、EOF、line ending、trailing whitespace、Ruff、Ruff format、detect-secrets：通过；
- `rustfmt`：未执行成功，原因是当前环境找不到 `cargo`；
- 这是验证环境缺口，不单独判定为本次 Python correctness 缺陷，但在有 `cargo` 的交付环境重跑前，不能声称 monorepo pre-commit 已由本次验收完整通过。

新增本验收报告后，以 `SKIP=rustfmt` 重跑同一全仓命令，其余 hooks 全部通过；因此当前唯一未执行的 hook 仍是缺少 `cargo` 的 `rustfmt`。

`git diff --check`：通过。

## 7. 已确认成立的设计方向

以下目标经代码与测试确认成立，不应在修复阻断项时回退：

- `GraphFrontierState.nodes[].settlement` 是 node status/result 的唯一 durable truth；
- `GraphRunState.resources` 是 resource owner/waiter/admission 的唯一 durable truth；
- `GraphExecutionLease` 只保存 exact token；
- `ClaimGraphExecution` 在一个 revision 原子安装 token 与 optional resources；
- `SettleGraphNode` 是唯一 settlement command，并由 reducer 原子 release resources 与推进 FIFO waiter；
- stable `RUNNING + SETTLED` 是合法恢复边界；
- `AdvanceGraphFrontier` / `CompleteGraphFrontier` 是 standalone commands；
- `ResumeGraphNodes` 不再内联 resolution；
- nested terminal child、ordinary node 和 resource node 最终汇入同一个 `SettleGraphNode` path；
- 已删除 batch collector、resource wave executor、独立 resource transition module和旧 batch result surface；
- state 不依赖 compiled graph，execution 不调用 reducer或持有 Store；
- 未新增 legacy symbol/file/import/string-scan 门禁。

## 8. 最终验收意见

当前实现完成了 durable state 模型替换，但 execution session 还不是一个不可绕过、单消费者、按 authoritative successor 即时调度的线性 capability。
这三点正好位于本方案相对旧 batch runtime 新增的核心边界，不能作为普通后续优化处理。

关闭 §3 的三个阻断项、补充对应确定性测试、同步 authoritative 文档，并在具备 `cargo` 的环境重跑完整 monorepo pre-commit 后，方可重新申请代码验收。

当前结论：**不通过，不建议提交或标记实施完成。**
