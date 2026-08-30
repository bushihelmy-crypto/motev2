# 父子图独立 GraphRun 状态实施方案评审回复（范围撤回版）

> **Disposition：REVIEW RESPONSE COMPLETE / R1–R14 ACCEPTED AS SCOPE AND OWNER
> CORRECTIONS / TARGET WITHDRAWN / RESEARCH-ONLY / NOT READY FOR IMPLEMENTATION。**
>
> 本回复关闭的是评审记录的 disposition，不是把未来能力标记为已实现。它不批准
> production、State、Store、protocol、persistence、public API 或测试变更，也不把
> 任一评审 blocker 伪装成“已由临时实现解决”。

## 1. 回复信息与事实分工

- 回复日期：2026-08-27
- 本轮评审：[父子图独立 GraphRun 状态实施方案独立技术评审](graph-independent-run-context-implementation-review.zh-CN.md)
- 评审 SHA256：9f6a27958432153b6aed31994e705fa2060c52ae16d09d5ce3358ee7ff71bcb3
- 原实施目标 SHA256：2bfa6876305317d0b0b375b476c17a1c47e5798c912e472c80a87f804f307bd5
- 修订后的边界文档：[父子图独立 GraphRun 状态实施方案（边界修订版）](graph-independent-run-context-implementation.zh-CN.md)
- 修订目标 SHA256：b996e47b077ba360125a786a20a5cdfcf5c10eb2256eb10ce23cd890e98bb2c3
- 当前行为规范：[Graph 节点输入/输出契约实施方案](graph-node-input-output-contract-implementation.zh-CN.md)
- 关联调研：[GraphRun 独立状态可行性调研](graph-independent-run-context-feasibility-research.zh-CN.md)

本文是 review disposition/audit record。它只拥有本轮评审项的接受、范围撤回、
证据和验证记录；不拥有当前 API、State、frame、continuation、nested、resource、
error behavior，也不拥有未来 requirements 的 ID、批准状态或 production target。

事实的唯一优先级固定为：

| 事实 | 唯一 owner | 本回复的关系 |
| --- | --- | --- |
| 当前可观察行为与既有类型 | 当前 normative source（production 仅提供实现证据） | 只读引用，不改写 |
| requirement ID、owner、approval、phase admission | requirements owner | 本回复不代行批准 |
| 未来独立 run 的候选边界与准入条件 | 修订后的 implementation boundary doc | 只由目标文档拥有 |
| R1–R14 disposition、SHA、验证和 manifest | 本回复 | 不反向拥有 target shape |

原目标的 14 个 blocker 对原始“跨 invocation 独立 child”目标均成立。整改采取
**scope reduction**：撤回整个未具备前置条件的 implementation target，而不是用
DTO、test double、隐式 fallback 或模糊 phase 把 blocker 改名。因而本回复中的
“接受/关闭”均表示“通过范围撤回或事实回写处理”，不表示未来协议已经可实施。

## 2. 总体裁决

修订后的唯一结论是：

~~~text
current Graph/State/reducer/continuation contract = KEEP
future independent child recovery                = SEPARATE REQUIREMENTS
implementation target                            = WITHDRAWN / RESEARCH-ONLY
implementation phase                             = NONE AUTHORIZED
production / State / Store / protocol / API      = NO CHANGE / NO AUTHORIZATION
tests / legacy tests                             = NO CHANGE / NO AUTHORIZATION
~~~

本次边界修订保留、且只保留以下当前事实和基础设计：

- Graph 是唯一 public 图 facade，execution 是唯一 execution engine；
- GraphRunState、typed command、pure reducer、GraphTransition、exact successor、
  revision/token fence 和现有错误分类继续由各自 owner 负责；
- MissingChild、ActiveChild、CompletedChild、AbortedChild 与 SettleGraphNode
  继续是当前 nested projection/settlement 的语义来源；
- ParentGraphActivation、child_graph_run_id()、ScopeRunCoordinate 和现有
  full-scope validator 继续复用；
- complete/recovered continuation 保持当前 opaque、invocation-local、不可序列化
  contract；不偷偷切换成 per-run checkpoint；
- 不新增 State 字段/status/command，不新增第二 scheduler、runner、frame truth、
  registry、compatibility alias 或 generic-erasing wrapper。

下列原始交付承诺已经明确撤回：

- 仅凭 child_run_id 在新 invocation 中 load/drive/recover；
- durable terminal output、boundary publish/read/ack、output codec 或 Store；
- 跨 run CAS、幂等 receipt、read-after-commit、crash/replay；
- definition resolver、worker handoff、lease、aggregate budget、failover；
- 跨 invocation cancellation、child lifetime protocol 和新的 public overload；
- GraphRunRef、ChildRunInvoker、ChildBoundary 及其他平行 production owner。

因此，本文不把“未来可以研究”写成“当前可以实现”，也不把现有 family invocation
中的多个 state binding 重新命名为已经独立的 public GraphRun。

## 3. R1–R14 逐项 disposition

所有条目均**接受其技术风险和 owner 边界判断**。表中的“处理”只描述文档边界
如何关闭原始 target 的错误承诺；任何未立项的未来能力仍保持 NOT READY。

| 评审项 | disposition | 修订处理与唯一 owner |
| --- | --- | --- |
| R1：与当前 normative source 的 Store/recovery 范围冲突 | **ACCEPTED / SCOPE WITHDRAWN** | 删除跨 invocation、durability、CAS、lease、budget 的本期交付；当前规范继续优先，未来能力必须另立 requirements。 |
| R2：requirements owner、ID、approval 和 target truth 缺失 | **ACCEPTED / OWNER BOUNDARY FIXED** | 目标文档补充 source precedence、owner、准入顺序和“无 phase”；本回复不创建或批准新的 requirement ID。 |
| R3：context、child ownership 与 continuation 互相矛盾 | **ACCEPTED / CURRENT CONTRACT KEPT** | 不删除或重解释当前 continuation snapshot，不把它拆成可序列化 per-run checkpoint；任何 ownership 重构都必须另立 target。 |
| R4：typed GraphOutputView 不能成为 durable terminal boundary | **ACCEPTED / DURABILITY REMOVED** | GraphOutputView、NodeOutputFrame 和 CompletedChild 仅保留当前 invocation-local 语义；codec、Store、visibility、retention 留待独立设计。 |
| R5：terminal state、publish、settlement 的 crash window 未闭合 | **ACCEPTED / NO TRANSACTION CLAIM** | 保留当前 projection → SettleGraphNode 顺序，但不声称跨写入原子性、outbox、replay 或重启恢复。 |
| R6：child start intent 与 input admission 非原子 | **ACCEPTED / NO INDEPENDENT START** | 不增加 digest、pending intent 或跨 Store admission；当前 deterministic child construction 只属于现有 invocation。 |
| R7：按 ID 新 invocation 缺少 definition resolver/port assembly | **ACCEPTED / ID LOAD REMOVED** | 不增加按 ID load、隐藏 registry、resolver 或 public overload；当前 child definition 只来自已编译 family。 |
| R8：scope coordinate 与 local-root 方案未裁决 | **ACCEPTED / CURRENT FACT FROZEN** | 现有 full node-ID path 继续用于 identity、validator、diagnostic、frame 和 grandchild；未来若改 coordinate，必须另立规范和迁移。 |
| R9：新增 type/port 与现有 owner 重叠 | **ACCEPTED / ZERO NEW SURFACE** | 复用既有 identity、projection、result、frame、transition owner；新增 production type/port/field/adapter 数为 0。 |
| R10：idempotency/CAS 只有 key 表述 | **ACCEPTED / CROSS-RUN PROTOCOL REMOVED** | 只保留现有 revision/token、deterministic identity 和 exact successor；不新增 receipt、ack、unknown-commit replay 或 exactly-once。 |
| R11：worker lease、aggregate budget 和 reclaim 未定义 | **ACCEPTED / CONCURRENCY REMOVED** | 不开放跨 worker、handoff、lease、reclaim 或 aggregate budget；这些未来能力没有本期 phase。 |
| R12：cancellation、exception 与 child lifetime 没有 typed protocol | **ACCEPTED / INVOCATION LIFETIME KEPT** | 不增加跨 run cancel/receipt/fence/reclaim；现有 session close、cleanup 和 exception fence 不被扩写成 failover 协议。 |
| R13：assembly fallback 语义矛盾 | **ACCEPTED / NO IMPLICIT FALLBACK** | 本文不定义新 capability mode；当前 contract 不因缺少未来 port 而静默变更，未来 mode 必须显式装配并 fail closed。 |
| R14：测试、门禁、phase 与授权口径不一致 | **ACCEPTED / DOCS-ONLY BOUNDARY** | 本轮不新增/扩写测试或 legacy gate；complexity 按用户范围排除，其他适用检查只如实记录，不把 make check 写成完整通过。 |

### 3.1 对“接受”的限定

R1–R14 的接受并不表示这些协议已经设计完成。它们在本 change unit 中的关闭方式
是：删除原目标中的越界承诺、保留当前可观察 contract，并把所有未闭合能力登记为
未来 requirements 的 NOT READY 前置条件。没有一项接受条目单独授予 production
或 tests authorization。

## 4. 唯一真相、复用和零债务账本

### 4.1 当前 contract 不变

修订目标不修改以下事实：

1. Graph.run() 仍只接受当前规范定义的 values、显式 GraphRunState、
   continuation 和 resume 输入；没有按 ID load 入口。
2. GraphRunState 是 control truth；concrete frame、publication value、
   continuation snapshot 和 Store handle 不进入 State。
3. child blocked 仍是 child RUNNING、parent nested node Pending；只有 typed
   CompletedChild 才能进入 parent SettleGraphNode success。
4. callback 的 exact candidate/confirmed successor 检查继续复用；callback 成功不被
   解释为跨 Store 原子事务。
5. current complete/recovered continuation 的 state pairing、sealed admission、
   same-process 和不可序列化边界不变。

### 4.2 复用清单

未来若独立 requirements 获批，只能沿用这些 canonical owners，不得复制：

| 语义 | 复用 owner |
| --- | --- |
| 图组合、编译和执行 | Graph、CompiledGraph、GraphExecutor、现有 session/scheduler |
| 控制状态和转换 | GraphRunState、GraphRunCommand、reduce_graph_run()、GraphTransition |
| 父子 identity | ParentGraphActivation、child_graph_run_id()、ScopeRunCoordinate |
| child projection 与 parent settlement | MissingChild/ActiveChild/CompletedChild/AbortedChild、SettleGraphNode |
| frame/value 与结果 | GraphInputFrame、NodeOutputFrame、GraphOutputView、现有 task/run result |
| 提交与生命周期 | GraphCommit、revision/token fence、session close、现有 error taxonomy |

本轮账本固定为：

~~~text
new production type/port/field       = 0
duplicate scheduler/runner/path     = 0
parent child-state mirror           = 0
new State/Store/protocol surface    = 0
new test / legacy test               = 0
implicit fallback / compatibility alias = 0
~~~

“零债务”在这里是本 change unit 的新增面账本，不是对工作树中用户既有修改的
归因或对未来协议复杂度的保证。任何未来 target 都必须重新给出 producer/consumer
call graph、删除闭包和 per-change manifest。

## 5. 未来能力的唯一准入顺序

修订后的 implementation 文档没有开放原文 Phase 0–4。未来若重新提出 independent
run，requirements owner 必须先回答并批准以下事实：

- state load/commit 的 authoritative owner、revision/token、definition/version 和
  parent 校验；
- input、publication、resume、child material 的 nominal type、codec、版本、大小和
  安全边界；
- terminal output/reason/awaiting metadata 的 durable representation、可见性和
  retention；
- compiled definition resolver、权限和显式 port assembly；
- child terminal、publish、parent settlement、publication install 的原子性或
  outbox/replay；
- start/transition/publish/read/settle/ack 的幂等状态机、same/different payload、
  stale 和 unknown-commit 结果；
- coordinate universe、grandchild/frame/diagnostic/validator 迁移；
- worker lease、fence、reclaim、aggregate budget 和 crash 返还；
- cancellation、close/fence 顺序、parent/child lifetime 和重放；
- API/security、caller 权限、结果类型、compatibility policy；
- exact case-level behavior/fault evidence、适用非复杂度 gates、批准记录和 manifest。

这些事实任何一项未闭合时，唯一合法状态都是：

~~~text
requirements = NOT APPROVED
implementation = NOT READY
production/tests = NOT AUTHORIZED
~~~

允许的顺序是 requirements → target → independent review → explicit user approval →
implementation。新 target 不继承本版或原目标的类型、phase、错误文本或 SHA。

## 6. 测试与门禁口径

本轮是 docs-only：

- 不修改 src/**、state/**、Store、protocol、persistence、README 或 API；
- 不新增、扩写或依赖 legacy/private-source-shape/AST test；
- 不以 test double、内存 registry 或新测试证明未来 recovery；
- complexity、health、baseline、ratchet 属于用户明确排除的范围，本轮不运行、不改
  配置、不新增 waiver；
- Ruff、format、Pyright、现有 behavior tests、coverage、build/package、文档完整性
  和适用 pre-commit 只作为事实记录，不改变上述授权边界；
- make check 含被排除的 complexity gate，不能在本轮写成“完整通过”。

当前工作树存在评审前已有的用户 dirty changes。它们不属于本 response 或 target
writeback；验证结论仅按下述 scoped manifest 归因。

## 7. 当前验证记录

以下结果是当前工作树的基线和本轮文档检查，不是未来 independent recovery 的证据：

| 检查 | 结果 |
| --- | --- |
| 非 complexity behavior tests：python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py | **PASS：841 passed in 99.10s** |
| python -B -m ruff check src tests | **PASS** |
| python -B -m ruff format --check src tests | **PASS：152 files already formatted** |
| pyright | **PASS：0 errors, 0 warnings, 0 informations** |
| python -B -m build --no-isolation | **PASS** |
| python -B -m twine check dist/* | **PASS** |
| docs link/EOF/CRLF/trailing-whitespace scan | **PASS** |
| git diff --check / git diff --cached --check | **PASS**（tracked workspace；untracked docs 由上一行覆盖） |
| docs scoped pre-commit | **PASS**；complexity hook 无适用文件 |
| env COVERAGE_FILE=/tmp/mote-independent-run-context-final.coverage python -B -m pytest -q -p no:cacheprovider --ignore=tests/architecture/test_complexity_gate.py --cov=mote_kernel --cov-report=term-missing | **841 passed；99.94%**，未达项目 fail-under=100；缺口来自评审前已存在的 dirty family_driver.py:515、result.py:164 |
| make check | **未运行**：聚合目标无条件包含本轮排除的 complexity gate |
| 本轮新增/扩写 legacy tests | **0 / 0** |

coverage 缺口没有通过修改 production 或新增测试掩盖，也不能被归因于这两个
docs-only 文件。若后续工作树变化，验证结果必须重新绑定新的 target/review SHA。

## 8. 两个独立 manifest

为避免把 review 记录误当成 target truth，本轮保留两个不重叠的 manifest：

### 8.1 target writeback manifest

~~~text
mote-kernel/docs/graph-independent-run-context-implementation.zh-CN.md
~~~

该文件只拥有边界修订、撤回项、复用原则、未来准入条件和当前快照；它不授权
production 或 tests。

### 8.2 response-only manifest

~~~text
mote-kernel/docs/graph-independent-run-context-implementation-review-response.zh-CN.md
~~~

该文件只拥有 R1–R14 disposition、证据分层、验证记录和本 manifest；它不回写
requirements 批准状态，也不复制 target 的未来字段或算法。

负向 manifest（本轮无授权、无归因）：

~~~text
src/** / tests/** / state/** / Store / protocol / persistence / public API = 0
README / CHANGELOG / pyproject.toml = 0
~~~

## 9. 最终裁决

~~~text
R1–R14                              = ACCEPTED AS SCOPE/OWNER CORRECTIONS
original implementation target     = CHANGES REQUESTED / WITHDRAWN
revised target                     = RESEARCH-ONLY / ARCHITECTURE BOUNDARY / NOT READY
current Graph/State/reducer path   = KEEP
future persistence/recovery        = SEPARATE REQUIREMENTS / NOT AUTHORIZED
new production type/port/field     = 0
new test / legacy test             = 0
complexity gate                    = USER-EXCLUDED / NOT RUN
non-complexity baseline            = PARTIAL (coverage baseline not green)
production/State/Store/protocol/API = NO CHANGE / NO AUTHORIZATION
~~~

本回复完成后，原目标的“立即实施”路径被撤回；没有任何 Phase 获得批准。下一次
若要推进，必须由 requirements owner 先创建独立 requirement/change unit，冻结
source precedence、owner、nominal contracts、fault evidence 和 manifest，再提交
新的 target 与独立 review。该新 target 不继承本回复中的未决设计，也不得通过
legacy test、隐式 fallback、第二执行路径或临时 State 字段补洞。
