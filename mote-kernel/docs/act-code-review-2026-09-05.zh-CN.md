# Act 代码实现评审（2026-09-05）

## 结论

**Request changes：当前不能 commit。**

四阶段的主拓扑和 owner 方向是对的，但实现还没有证明最重要的边界：共享 Hook 的
concrete admission/provenance，以及“任何工具都必须经过 Authorize interrupt”。此外，
Act 自身已经有硬门禁失败（类型擦除、只写不读字段、覆盖率），当前工作树还有仓库级
门禁失败。因此不能用 happy-path 测试通过或 `pyright` 通过替代提交条件。

本评审只覆盖 `mote_kernel.act` 及其与 Graph/Hooks 的接缝。Failover 的策略、重试、
装饰器和固定图内部实现不在范围内；只检查它应在 composition 期包住具体 Port 后再注入。

## 已核对的调用链

```text
Graph.run
  -> ResolveNode -> shared HookNode -> RouteAfterHook
  -> AuthorizeNode
       initial: AuthorizePort.request_authorization -> Graph.interrupt(bytes)
       resume:  Allow/Deny only
  -> shared HookNode -> RouteAfterHook
  -> ExecuteNode -> ExecutePort.execute (once)
  -> shared HookNode -> RouteAfterHook
  -> SettleNode -> SettlementPort.project -> ToolExchangeWriter.write
  -> shared HookNode -> RouteAfterHook -> END
```

这些部分与已拍板边界一致：Act 没有私有 runner、State、Store、retry loop 或
Settle-only recovery path；`ExecutePort` 是 `ToolExecutionResult` 的唯一正常生产入口；
正常结果经过同一个 Hook，Deny 直接是 Graph stop；外部 Port/协议 owner 负责具体业务值和
model-facing English serialization。

## 阻断项

### P0-1：共享 Hook 可以绕过 Authorize interrupt，且可改写跨阶段事实

位置：[`src/mote_kernel/act/authorize.py:81-97`](../src/mote_kernel/act/authorize.py)、
[`src/mote_kernel/act/admission.py:195-241`](../src/mote_kernel/act/admission.py)。

`ActPayloadAdmission` 目前只验证 `HookResult`/`ActHookEnvelope` 的 exact 外壳、stage 和
固定 route；它没有验证 concrete Hook 的 admission，也没有验证跨阶段 provenance。
`AuthorizeNode` 只按 `authorization.phase` 分支。因而一个返回合法外壳、但把 Resolve
payload 改成 `ResumedAuthorization(Allow)` 的 Hook，可以让首轮执行直接完成：

```text
_CompletedGraphResult
request_authorization_calls = 0
execute_calls = 1
```

同样的边界允许 Hook 在 Authorize 阶段替换 `ResolvedInvocation.binding`，或在 Settle
完成写入后把 receipt 换成另一个值；现有 admission 仍会把它们送到下游/最终 output。

这违反了以下不可变不变量：

- 每个工具调用必须先经过一次 AuthorizePort 和 interrupt；
- pairing、binding、arguments digest、resolved invocation 和 Hook state 必须跨阶段保持；
- writer 已确认的 `SettledActResult` 不能被 Hook 改写。

**关闭条件：** Hooks/composition owner 必须提供一个可审计的 concrete
`HookPayloadAdmission`/invocation contract，并有 deterministic negative tests，证明
错误的 phase、stage payload、state、pairing、binding、digest 和 settled receipt 在进入
下游前失败。Act 不应读取 Hook 私有字段，也不能为此增加第二份 state、runner 或业务
对账表；若当前 owner 接缝无法提供该证据，本 MR 不能宣称闭环。

### P0-2：错误 concrete Hook 在 assembly 期被接受

位置：[`src/mote_kernel/act/node.py:128-139`](../src/mote_kernel/act/node.py)。

构造器只检查 `isinstance(hook, HookNode)` 和 `HookSlotId`。Python 运行时会擦除
`HookNode` 的泛型参数，因此 value/state/command 不匹配的 Hook 仍能完成
`ActNode(...)`；错误 state 的最小反例实际得到 `ASSEMBLY_OK`，首次运行才抛
`HookContractError: hook state has an unexpected payload type`。

这不符合“required capability 在 assembly 期失败”和实施稿要求的 concrete static binding。
应由 Hooks owner 暴露窄的、不可变且可验证的 descriptor/contract，或由 composition 在
组装边界提供同等证据；不能用反射、读取私有字段或 `Any` 猜测类型。另有同样的严格性
问题：Act 使用 `isinstance(hook, HookNode)`，而 Think 已使用 exact `type(...) is`，应统一
明确是否允许 subclass，避免 subclass 伪装成固定 Hook。

### P1-1：生产边界存在 `object` 类型擦除

位置：[`src/mote_kernel/act/node.py:47`](../src/mote_kernel/act/node.py#L47)。

`_require_callable(value: object, ...)` 被仓库的
`test_production_boundaries_preserve_generic_types` 明确拒绝。请改成保留关系的窄类型
写法，或删除这层重复检查；不能以放宽架构规则、`Any` 或新的宽 context 绕过。

### P1-2：`ActNode` 保存五个从未读取的 capability 字段

复杂度健康门禁报告：

```text
ActNode._exchange_writer
ActNode._execute_port
ActNode._failure_reason
ActNode._resolve_port
ActNode._settlement_port
```

这些引用真正由各 stage node 持有，`ActNode` 只在 resume helper 中需要
`_authorize_port`，在 property 中需要 `_hook`，并需要 `_admission`。当前保存方式制造了
重复 owner/隐性状态，并直接触发 `unread_private_fields != 0`。删除冗余字段和赋值，不要
为了通过指标把它们改成别的转发字段。

### P1-3：专项测试没有覆盖实施稿的失败/恢复矩阵

`tests/act` 只有 28 个测试，均通过，但 branch coverage 为 **89.03%**，仓库硬门槛为
100%。未覆盖的关键分支包括：

- assembly 缺 Port/Hook、错误 codec、错误 slot；
- Resolve/Execute stop、Port 异常和取消；
- Deny、重复/旧/wrong-scope resume；
- projection/writer 失败和 receipt/settled-result 篡改；
- route/Hook 非法结果、nested/root resume 和 Graph mutation guard；
- concrete Hook value/state/command mismatch 及跨阶段 provenance。

补测试时应验证真实行为和调用次数，不要通过 `# pragma: no cover`、空转发测试或兼容
wrapper 抬高数字。

### P1-4：重复校验和限制存在多个 owner，增加无意义复杂度

三个业务节点各自有几乎相同的 `_hook_result` helper；长度上限同时出现在
`identity.py`、`contract.py` 和 `admission.py`，其中 `admission.py` 多数常量并未参与校验。
这既被复杂度扫描命中，也让“一项规则一个 owner”难以保证。应把限制和 exact admission
集中在唯一责任处，节点直接使用该边界；不要新增 `common/utils/helpers` 包来包裹重复逻辑。

### P1-5：公开 required Port 的静态形状仍可被 `None`/错误 callable 弱化

`ActNode.__init__` 和各 stage node 的字段标注为 `Port | None`，运行时再用
`isinstance`/`callable` 检查。required capability 的公开签名应与实施稿一致，使用非空
Protocol；动态负例可在测试中通过窄的 cast 构造。对错误 arity、同步返回非 awaitable 和
取消/普通异常的边界也要有明确 contract test，不能把 `callable()` 当作异步协议证明。

## 门禁证据

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -m pytest tests/act -q --no-cov` | 通过 | 28 passed |
| `python -m pytest --cov=mote_kernel.act --cov-branch tests/act -q` | **失败** | 89.03%，要求 100% |
| `make typecheck` | 通过 | 0 errors；不能抵消 AST generic gate |
| Act `ruff check` / `ruff format --check` | 通过 | Act 专项文件格式/lint 正常 |
| `tests/architecture/test_generic_integrity.py` | **失败** | `act/node.py:47 object erases the boundary type` |
| `tests/architecture/test_complexity_gate.py` | **失败** | Act unread fields=5；全局 ratchet 也超基线 |
| `git diff --check` | 通过 | 无 whitespace 错误 |
| `make check` | **失败** | 首先被仓库已有 `tests/architecture/test_graph_execution_ownership.py` 格式问题拦截 |
| `make test` | **失败** | 1570 passed，但 complexity/generic/package structure 4 项失败；总覆盖率 99.01% |
| monorepo `pre-commit run --all-files` | **失败** | ruff-format 会改动一个非 Act 文件，kernel complexity ratchet 失败；为保持用户改动已恢复该非 Act 格式改动 |

全仓 complexity/package failures 还包含当前并行 Think/Graph/包删除改动，不能全部归咎于
Act；但在这些改动被隔离或修复前，整个工作树同样不具备 commit 条件。`make typecheck`
通过只说明静态类型检查没有报错，不能替代 generic integrity、覆盖率和零技术债门禁。

## 不应通过错误方式关闭的问题

- 不要把 Failover 包进 Act Graph、增加整图 retry、Settle-only runner 或第二个 state。
- 不要让 Act 读取 Hook 私有 admission，也不要复制一份 provenance/state 来“补校验”。
- 不要为 legacy test 保留旧 API alias、wrapper 或第二执行路径。
- 不要在 Kernel 生成 ToolResult、拒绝原因、审批摘要或任何 model-facing 文案；外部
  protocol/presentation owner 仍须对最终 serialization 提供 English-only deterministic test。

## Commit 前置条件

只有同时满足以下条件，才可重新评审 commit：

1. 关闭 P0-1/P0-2，并附 concrete Hook owner 的 provenance/assembly 负例证据；
2. 删除 `object` 边界和五个 unread fields，收敛重复 admission/limit owner；
3. 补齐 Act 失败、取消、Deny、resume、nested、writer/settle 和 mutation 测试，使 Act
   分支覆盖率达到仓库要求的 100%；
4. 在隔离无关工作树改动后，`make check`、全仓测试和 monorepo pre-commit 全部通过；
5. 外部 Runtime/protocol owner 提供最终 model-facing English serialization 证据，且
   composition 证明每个启用恢复的具体 Port 先套 Failover 再注入。

在上述条件完成前，建议 MR 状态保持 **Request changes**，而不是“有条件通过”或
“测试已绿”。
