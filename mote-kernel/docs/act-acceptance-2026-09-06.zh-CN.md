# Act / Tool Use 验收单（2026-09-06）

## 1. 用途和当前结论

这份文档给 code reviewer 使用。验收对象是 `mote_kernel.act` 与它依赖的共享
`HookNode` 接缝；以当前工作树的源码和测试为准，不以早期实施稿中的历史拓扑为准。

当前结论分两层：

- **Act/Hook 专项：PASS。** 210 个专项测试通过，Act/Hook 语句与分支覆盖率均为
  100%，项目入口 `make typecheck`、专项 Ruff 和架构子集均通过；当前未发现未关闭的
  Act/Hook P0。
- **整个仓库：BLOCKED，不能宣称可提交。** 当前 `make check` 和 monorepo
  `pre-commit run --all-files` 都只阻断在全局 structural-complexity ratchet；Ruff、格式、
  typecheck 和 complexity health 均通过。该 ratchet 反映当前混合 Graph/Think/Observe/
  Failover 工作树相对基线的整体增长，不能直接归因于 Act，但在未隔离或由对应 owner
  处理前，仓库级门禁仍未满足。

本验收不包含：具体工具目录和授权策略、外部 ToolResult/模型文案序列化、Port provider
的事务/幂等实现、Failover 策略内部、统一持久化和跨进程恢复。

## 2. 生效的拓扑

Act 只有四个业务节点和一个共享 nested Hook，没有 route 节点、`via`、selector 或每阶段
复制的 Hook：

```text
START
  -> resolve
  -> hook       -- route="resolve"   -> authorize
  -> hook       -- route="authorize" -> execute
  -> hook       -- route="execute"   -> settle
  -> hook       -- route="settle"    -> END
```

图上的四条业务节点到 `hook` 的边是互斥控制路径，不是 Join。每个业务节点成功时发布同一
个名为 `hook_request` 的 typed output，并把自己的 `GraphNodeId` 放入 `HookRequest.node_id`。
共享 Hook 的 P3 完成值带回这个身份，返回 `Graph.success(..., route=node_id)`；Act 在建图时
用既有 `Graph.add_edge(source, route, target)` 声明四条静态 conditional edge。路由规则属于
外层 Graph，Hook 不认识 Act 的下一个节点。

审核时应确认：

1. `ActNode` 的 builder 节点恰为 `resolve`、`authorize`、`execute`、`settle`、`hook`；
2. `hook` 是一个真实 `HookNode`，slot 为 `hook/AFTER_NODE`；
3. 没有 `RouteAfterHook`、`ActHookRoute`、`next` 字段或 per-stage Hook 参数；
4. `Settle` 成功也必须经过共享 Hook 后才到 `END`。

## 3. 四个业务节点的验收边界

| 节点 | 成功职责 | 停止/等待语义 | 明确不做的事 |
| --- | --- | --- | --- |
| `ResolveNode` | 调 `ResolvePort.resolve()`，发布 `ResolveStageValue` | `ResolutionStopped` 转为 Graph failure；不进入 Hook | 不把 registry、callable 或模型内容放进 Graph value |
| `AuthorizeNode` | 首次为已解析 invocation 请求授权；Allow 恢复后发布 `AuthorizedInvocation` | 首次只 interrupt；Deny 转为 Graph failure，后续 Hook/Execute/Settle 均不运行 | 不自动放行、不新增 ApprovalPort、不重复请求授权 |
| `ExecuteNode` | 仅消费 Allow 后的 `AuthorizedInvocation`，调 `ExecutePort.execute()` | `ExecutionStopped`、异常和取消沿 Graph 边界停止 | 不解析工具、不重试整图；它是 `ToolExecutionResult` 唯一生产者 |
| `SettleNode` | 依次 `SettlementPort.project()`、`ToolExchangeWriter.write()`，形成 `SettledActResult` | 任一调用失败/取消即停止；成功后仍进入共享 Hook | 不执行工具、不查询工具、不从 writer 读取“结果”、不提供 Settle-only 恢复入口 |

正常成功调用次数应为：`resolve=1`、`request_authorization=1`、`execute=1`、
`project=1`、`write=1`；共享 Hook 对四次成功业务 activation 各执行 P1/P2/P3 一次。

## 4. Typed value 和共享 Hook

### 4.1 Graph carrier

- Act 继承 `Graph[HookGraphValue]`；所有 Act DTO 直接或间接使用这个 carrier。
- 不存在独立的 `ActGraphValue`，也不能用 `object`、`Any`、裸字典或开放 Union 代替。
- `Graph.Values ↔ Act DTO` 的转换只在 execution typed adapter/节点 materializer 边界发生。
- `HookNode` 的 `result_output` 保存 P3 声明时生成的 descriptor；`output_ref(parent_node_id)`
  只替换父图地址并复用同一个 descriptor。这样 nested consumer 不会现场制造“同类但不同
  descriptor”的伪类型。

### 4.2 Hook admission 和 provenance

共享 Hook 必须绑定 concrete `HookPayloadAdmission`：

- `value_type is ActHookEnvelope`；
- state 和 command 类型与 Act admission 的 concrete class 完全一致；
- 每个 P1/P2/P3 返回值经过 `HookTransitionAdmission`；
- `stage` 与 stage payload、`HookRequest.state` 与 envelope state、pairing、binding、
  arguments digest、execution result 和 settled receipt 均保持一致；
- Hook 可以产生其 owner 定义的 immutable command，但 Act 不解释、累计或 apply command；
- Hook 失败、异常或取消不能旁路下一个业务节点。

`HookResult.node_id` 必须由原始 request 的业务节点身份产生，脚本不能借此伪造下一跳。
Act 的 admission 在下游调用前拒绝跨阶段、错误 producer、错误 state/command 和已写入
`SettledActResult` 的改写。

## 5. Authorize、Deny 和 resume

### 5.1 首次授权

每个合法工具调用都必须走同一个 `AuthorizePort`：

```text
Resolve -> shared Hook -> AuthorizePort.request_authorization()
       -> AuthorizePort.encode_interrupt()
       -> Graph.interrupt(bytes)
```

没有授权配置时，由外部 composition 提供不需要等待的符合该 Port contract 的实现；Act 不
凭空增加 auto-bypass 分支。

### 5.2 恢复

公开 helper 只从 `awaiting.interrupts` 的公开字段投影 Act-owned、immutable 的
`AuthorizationInterruptView`，再交给 `AuthorizePort.build_resume_input()`。冻结字段为：

- `scope: tuple[str, ...]`
- `node_id: str`（必须是 `authorize`）
- `interrupt_id: str`
- `request_payload: bytes`

codec 元数据是同一 `AuthorizePort` 的 `codec_id: str` 和 `codec_version: int`，只在 assembly
时读取并安装一次。Port 只关联 opaque handle 并组装 `Allow()` 或 `Deny()`；重复 resume、旧
state、错误 scope/interrupt/codec 的权威拒绝属于 Graph，不在 Act helper 或 Port 中复制。

### 5.3 Deny

拒绝顺序是外部 owner 的责任：

1. Runtime/authorization owner 认证 response、关联原始 opaque handle；
2. protocol owner 先按原 `tool_call_id` 写入配对的拒绝 ToolResult；模型可见内容由该 owner
   以英文序列化；
3. owner 把 exact `Deny()` resume decision 交给 Act；
4. Act 返回 Graph failure/stop，不运行额外 Hook、Execute 或 Settle。

Kernel 不生成拒绝文案、审批摘要、错误码翻译或默认 ToolResult，也不读取外部写入的
ToolResult。配对写入未确认时不得伪装成 Deny 成功。

## 6. Execute、Settle 和 Failover

`ToolExecutionResult` 只能由 `ExecutePort` 产生。`SettleNode` 必须验证：

```text
execution.identity == projection.source_identity
```

然后按固定顺序 project、write，并以 writer receipt 构造 `SettledActResult`。该结果经同一
共享 Hook 后由 `route="settle"` 到 `END`；Hook 不能替换 projection 或 receipt。

故障恢复的边界在 composition：启用恢复的每个 concrete `ResolvePort`、`AuthorizePort`、
`ExecutePort`、`SettlementPort` 和 `ToolExchangeWriter`，必须先套上 Port-level Failover，
再以原 typed Port surface 注入 Act。Act 不包整图、不实现 retry loop、不复制 Execute/Hook，
也不创建第二个 state/store。Failover 的 attempt、幂等和 unknown-outcome 策略由其 owner
另行验收。

## 7. State、nested completion 和版本范围

- `Graph` 与 `GraphRunState` 是唯一执行位置、activation、interrupt/resume、nested child
  projection、reducer 和 commit owner；Act 不定义 `ActState`。
- nested Act 的 resume action 必须提交给持有 awaiting result 的 root Graph。
- nested Hook 的成功 route 通过 Graph completion projection 传给父图；父图只接受自己声明
  的 conditional route。
- v1 只承诺进程内执行；不承诺持久化、跨进程恢复、exactly-once 或 writer 已确认后的
  Settle-only 恢复。后续统一 persistence owner 处理。
- `src/mote_kernel/operations` 是已删除内容，不得恢复或新增兼容入口。

## 8. 公共 API 和 owner 检查

| 包/模块 | 验收口径 |
| --- | --- |
| `mote_kernel.execution` | 唯一 Graph 组合/执行 facade；Act 不创建 runner/executor |
| `mote_kernel.act` | `__all__ == ["ActNode"]`；根包不并列导出 DTO、Port 或阶段节点 |
| `mote_kernel.hooks` | `__all__ == ["HookNode"]`；根包不暴露 Hook Port/内部 runner |
| `mote_kernel.act.*` 内部模块 | 可按职责放置 DTO、admission、Port 和四个节点；不形成第二套公共 facade |
| 外部 Runtime/protocol owner | 授权策略、Deny ToolResult pairing、英文模型序列化 |
| 外部 provider/composition owner | Port 实现、writer receipt、Failover 装饰和持久化 |

禁止通过兼容 alias、第二执行路径、隐藏可变缓存、默认中文文案或私有 recovery API 绕过上述
owner 边界。

## 9. 测试证据和复核命令

### 9.1 当前已执行结果

| 检查 | 当前结果 |
| --- | --- |
| `python -B -m pytest tests/act tests/hooks -q --no-cov` | **210 passed** |
| Act/Hook branch coverage（见下方命令） | **100.00%，1142 statements，248 branches** |
| `make typecheck`（项目虚拟环境入口） | **0 errors, 0 warnings, 0 informations** |
| Act/Hook Ruff check + format check | **通过** |
| Graph typed/nested completion + Act/Hook 组合测试（见下方命令） | **278 passed** |
| generic/package/source/dependency architecture 子集 | **23 passed** |
| `python -B -m tests.architecture.complexity_rules --check-health` | **PASS** |
| `git diff --check` | **通过** |
| `make check` | **BLOCKED：全局 structural-complexity ratchet** |
| monorepo `pre-commit run --all-files` | **BLOCKED：同一全局 structural-complexity ratchet；其余 hooks 通过** |

### 9.2 Reviewer 应重跑

```bash
python -B -m pytest tests/act tests/hooks -q --no-cov

python -B -m pytest \
  --cov=mote_kernel.act \
  --cov=mote_kernel.hooks \
  --cov-branch \
  --cov-report=term-missing \
  tests/act tests/hooks -q

make typecheck
python -B -m ruff check src/mote_kernel/act src/mote_kernel/hooks tests/act tests/hooks
python -B -m ruff format --check src/mote_kernel/act src/mote_kernel/hooks tests/act tests/hooks

python -B -m pytest \
  tests/execution/engine/test_completion_projection.py \
  tests/execution/test_predecessor_output_runtime.py \
  tests/execution/test_typed_node_contract.py \
  tests/execution/graph/test_predecessor_output_compiler.py \
  tests/act tests/hooks -q --no-cov

python -B -m pytest \
  tests/architecture/test_generic_integrity.py \
  tests/architecture/test_package_structure.py \
  tests/architecture/test_source_discipline.py \
  tests/architecture/test_dependency_direction.py -q

git diff --check
make check
```

### 9.3 测试矩阵定位

- `tests/act/test_contract.py`：opaque/identity 上限、frozen DTO、stage/payload、pairing 和
  digest 不变量；
- `tests/act/test_port.py`：五类 required capability、callable/codec/版本和唯一 resume binding；
- `tests/act/test_nodes.py`：五节点 builder、共享 Hook 四次 activation、typed binding、
  Allow/Deny、首次 interrupt、重复/旧/wrong-scope resume、root/nested resume、停止、异常、
  取消、mutation guard、receipt/result 篡改和调用次数；
- `tests/hooks/test_hooks.py`：P1/P2/P3 transition admission、node provenance、nested
  completion route、`result_output/output_ref` descriptor 复用和 Hook 失败边界；
- `tests/execution/engine/test_completion_projection.py`、
  `tests/execution/test_predecessor_output_runtime.py`：Graph terminal route、nested output
  和实际 predecessor publication 的通用语义。

## 10. Reviewer 最终检查清单

- [ ] Act 只有四个业务节点和一个共享 Hook；无 route node、`via`、selector 或 per-stage Hook。
- [ ] 所有 Act/Hook Graph 使用 `Graph[HookGraphValue]`，typed descriptor 来自真实声明而非现场伪造。
- [ ] Hook 每个 priority 都执行 concrete transition admission，不能改写跨阶段事实或 settled receipt。
- [ ] 每个工具调用先经过 Authorize interrupt；Allow 不重复授权，Deny 先由外部 owner 配对写 ToolResult 后停止。
- [ ] Execute 是唯一 `ToolExecutionResult` producer；Settle 只 project/write，并在成功后再次经过 Hook。
- [ ] 异常、取消和 typed stop 不被改写成成功、Deny 或隐式重试。
- [ ] Failover 在 concrete Port 注入前装饰，Act 没有整图 retry、第二 state/store 或 Settle-only 入口。
- [ ] 根包公共 API 只有 `ActNode`/`HookNode`，没有恢复 `operations` 或兼容 alias。
- [ ] Kernel 不生成任何模型可见文本；外部最终 serialization 有英文和 pairing 证据。
- [ ] 专项测试和覆盖率达到 100%；仓库 `make check` 的非 Act 阻断已被对应 owner 处理或明确记录。

## 11. 本轮复审结论（2026-09-06）

### 11.1 代码与设计判断

当前 Act/Hook 调用链满足本验收的设计门槛：

- Act 只有 `resolve`、`authorize`、`execute`、`settle` 四个业务节点和一个共享
  `HookNode`；没有第二个 route callable、第二个 runner、平行 state/store 或整图 retry；
- `Graph[HookGraphValue]`、typed node adapter、compiler 的 predecessor output、Hook 的
  concrete transition admission 和 Graph 的 terminal route 是各自唯一 owner；没有为 Act
  新增镜像状态或兼容执行路径；
- 每个工具调用首次必经 `AuthorizePort` 的 interrupt；resume 只有 `Allow`/`Deny`，Deny
  不会旁路执行；`ExecutePort` 是唯一 `ToolExecutionResult` producer，Settle 只负责
  project/write；
- Hook 的 P1/P2/P3 均经过 Act concrete admission，跨阶段 envelope、state、pairing、
  binding、digest 和 settled receipt 的篡改会在下游前失败；
- Kernel 不生成 ToolResult、拒绝文案或其他模型可见文本；具体 Port 的 Failover 仍由
  composition 在注入前装饰，本验收不替其 owner 审策略内部。

因此，**Act/Hook 专项可给 PASS**。这是专项代码质量结论，不等于当前混合工作树可以
commit。

### 11.2 门禁与提交判定

本轮实际证据：

- `python -B -m pytest -q --no-cov`：**2048 passed, 1 failed**；唯一失败是全局复杂度
  ratchet `test_structural_complexity_does_not_grow_and_improvements_are_ratchet_locked`；
- `make check`：Ruff、format、typecheck 和 semantic/health 子集通过，仍被同一 ratchet
  阻断；
- `pre-commit run --all-files`：除同一 kernel-complexity hook 外全部通过；
- 裸执行 `python -B -m pyright src/mote_kernel/act src/mote_kernel/hooks tests/act tests/hooks`
  会因为没有加载项目虚拟环境而报告 pytest 类型包缺失（451 个误报），不作为代码失败
  证据；权威入口是 `make typecheck`。

复杂度指标只作为高召回复审雷达；本轮已检查 Act 的真实调用链和 zero-debt health，未以
指标驱动额外抽象。但仓库规则要求所有门禁通过，所以当前最终判定仍是：

```text
Act/Hook 专项：PASS
P0/P1：无未关闭的 Act/Hook P0；验收文档旧证据已在本轮修正
专项测试：210 passed
专项覆盖率：100.00%（1142 statements，248 branches）
仓库级门禁：BLOCKED（全局 structural-complexity ratchet；来自混合工作树）
是否允许合入：NO
```

在全局 ratchet 由对应 owner 处理、或将本变更隔离到可全绿的提交边界之前，不应把此 MR
标记为可 commit；不需要为了绕过该门禁给 Act 增加 helper、兼容层、第二路径或修改业务
语义。

## 12. Reviewer 结论格式

```text
Act/Hook 专项：PASS / REQUEST CHANGES
P0/P1：无 / <列出文件与复现命令>
专项测试：<实际结果>
专项覆盖率：<实际结果>
仓库级门禁：PASS / BLOCKED（说明是否为本变更引入）
是否允许合入：YES / NO
```

只有在清单中的 Act/Hook 事实均有源码和 deterministic test 证据、且没有未关闭的 P0 时，
才能给 Act 专项 `PASS`。`make check` 仍失败时，不得把整个 MR 标成仓库级全绿。
