# Observe 验收复审（2026-09-06）

## 结论

**Observe-only scope：Approve，已达到 Observe 自身的 commit 条件。**

本轮只判断 `src/mote_kernel/observe`、`tests/observe` 及其直接 Graph/Hook 接线；复审范围是本地
typed boundary、故障/取消、重试、并发、恢复和分布式一致性防御，没有进行网络攻击、渗透或远程
未授权测试。本轮没有修改 Observe 生产代码或测试，保留工作树中其他包的既有改动。

旧复审正文仍保留在本文后半段，作为历史快照；其中旧的 P0/P1 `Request changes` 结论不适用于当前
工作树。当前实现已完成 Hook 适配后的 Observe 接线，以下包内阻断均已关闭：

- 两个业务节点使用 Graph typed-node contract（`Graph.bind`、typed materializer 和 `output_type`），
  不再保留节点内部的 legacy mapping 执行路径；
- `ObserveNode` 在第一次 `Graph.add_node()` 前检查共享 Hook 的 exact value/state/command binding、
  slot identity 和同一个 `ObservePayloadAdmission.transition_admission`；错误装配 fail-closed；
- Hook P3 结果统一由 `output_ref("hook", "result")` 绑定，Observe 不读取 Hook 私有 publication，
  也不制造第二份 output descriptor；
- 有界 rewrite 规则已统一：payload 可在当前 business stage 内改写，stage、只读 Hook state、
  `node_id` 和 command concrete class 不可改；每次 transition 和最终 result 都重新经过 Observe
  DTO admission；
- queue/frame/boundary、FIFO family projection、settlement receipt、cursor、revision、完整
  `blocking_tasks` snapshot 和 ACK reference 的不变量均有包内校验。

因此，按“先判断设计/owner 唯一且调用链简单，再用门禁证明无回归”的顺序，Observe 包自身没有
新的 P0/P1/P2 阻断。

仓库整体仍不能据此直接提交：当前工作树的 `make typecheck`/complexity ratchet 和并行测试改动均在
Observe 范围之外。它们只影响全仓门禁，不改变本次 Observe-only 判定。

## 历史复审记录（不再作为当前判定）

这次确认 Hook 改动不是空壳，但它只提供了通用能力：

- `HookTransitionAdmission` 已在 [`hooks/contract.py:33`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/contract.py:33)
  定义，`HookPayloadAdmission.transition_admission` 是可选字段（`:95`），并由
  [`hooks/port.py:60`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/port.py:60) 在每个优先级
  结果之后调用。没有绑定该字段时，Hook 仍按结构 admission 接受 value rewrite。
- `HookNode.payload_admission`、`result_output()` 和 `output_ref()` 已公开（分别见
  [`hooks/node.py:220`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/node.py:220)、
  [`hooks/node.py:228`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/node.py:228) 和
  [`hooks/node.py:239`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/node.py:239)）。Observe
  现在复用 `hook.output_ref("hook")`（[`observe/node.py:397`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:397)、
  [`observe/node.py:436`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:436)），这确实闭合了
  nested output descriptor 的 identity 问题；Hook 专项测试 66 项通过。
- Observe 装配仍只检查 exact `HookNode` 和 slot（[`observe/node.py:366`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:366)），
  没有读取 `hook.payload_admission`，也没有要求其 `transition_admission` 是 Observe 的唯一
  transition owner。Observe 的 `ObservePayloadAdmission` 本身没有 `admit_transition`；其
  [`observe/admission.py:600`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:600)
  仍只验证 outer class、stage、concrete command 和 predecessor `node_id`。

用本地一次性 provider/Hook 替身（不涉及网络或远程系统）复核后的最小结果如下：

```text
合法 User frame -> Hook 换成 Config frame：Graph Completed；Config=[evil]，Context=[]
第二次 Hook -> 自洽但不同 delivery 的 Config ObserveResult：最终 result=CONFIG，delivery=hook-replaced
Hook 返回同一 concrete state class 的新值：最终 hook_state=REWRITTEN
错误 Hook concrete binding：Observe assembly succeeded，首次运行才报 HookContractError
同 revision、不同 blocking_tasks 的 snapshot：ObserveResult admission 仍可通过
```

因此 Hook 通用适配不能关闭 Observe 的 P0-1/P1-1；它只把正确的 owner 接入点暴露出来。对照
Act 的装配期检查（[`act/node.py:103`](/home/longert/motev2/mote-kernel/src/mote_kernel/act/node.py:103)–`:112`）已经
同时核对 value/state/command 和 transition identity，Observe 尚未达到同一边界。现有 Observe fixture
也仍以不带 transition admission 的 Hook 构造（[`tests/observe/test_nodes.py:274`](/home/longert/motev2/mote-kernel/tests/observe/test_nodes.py:274)），
所以 235 项绿测不能证明该接线存在。

## 复审判定方法与调用链

按用户给出的最高原则，先判断 owner、调用链和不变量，再用门禁证明没有回归。当前实际调用链为：

```text
Graph.run
  -> get_observation
       ObserveRequest admission
       -> ObservationQueuePort.read_after
       -> BackgroundTaskPort.snapshot
       -> ObserveFrame / AFTER_GET HookRequest
  -> shared Hook (Plan -> P1 -> P2 -> P3)
  -> write_observation
       -> ConfigObservationPort.apply（存在 Config 时）
       -> ContextObservationPort.append（存在非 Config 时）
       -> ObservationBatchReceipt / ObserveResult
       -> AFTER_WRITE HookRequest
  -> shared Hook
  -> nested Graph publication / parent durable commit
  -> caller invokes ObserveNode.acknowledge()
```

空队列路径则先调用 `register_wait`，再返回 `Graph.interrupt`；恢复路径由调用方提供
`hook_state`，并把覆盖输入交给 Graph 的 resume codec。相关实现分别见
[`node.py:141`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:141)、
[`node.py:236`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:236)、
[`node.py:452`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:452) 和
[`node.py:467`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:467)。

## 阻断项（P0）

### P0-1：共享 Hook 的 observation provenance 没有闭合

实现计划要求两次 Hook 激活不得改变 observation 的 concrete class/字段、delivery、cursor、
boundary、task snapshot、`ObservationKind` 或 exact Hook state/value，见
[`implementation-plan:675`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:675)
和 [`implementation-plan:772`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:772)。

当前 `HookPort` 只有在通用 Hook admission 自己提供了可选
`transition_admission` 时才调用 transition 检查：
[`hooks/port.py:60`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/port.py:60)、
[`hooks/contract.py:168`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/contract.py:168)。
Observe 的 `admit_hook_result` 只检查 outer class、stage、concrete command 和前驱 `node_id`，
没有把返回 envelope 绑定到进入本次 activation 的 frame/result/state：
[`observe/admission.py:600`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:600)。
`ObserveNode` 也没有核对注入 Hook 的 `payload_admission` 与自己的 admission；它只核对 exact
`HookNode` 和 slot：[`observe/node.py:366`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:366)。

本地替身可以稳定得到以下结果：

1. 第一次 Hook 将合法 User frame 换成同 stage、同 outer class 但包含 Config batch 的 frame。
   Graph 正常完成，调用了 Config Port 而没有调用 Context Port，原始 User delivery 被错误结算。
2. 第二次 Hook 构造一个自洽的 Config `ObservationBatchReceipt` 和 `ObserveResult`（delivery id、
   boundary、`current_state` 彼此一致，但都没有与原始 frame 绑定），结果被接受并发布给父图。
3. Hook 返回同一 concrete state class 的不同值时，`admit_hook_result` 仍接受；后续 Hook
   activation 会携带被改写的 state。
4. `ObserveResult` 只比较 task snapshot 的 revision，不比较完整 snapshot；同 revision 但不同
   `blocking_tasks` 的结果仍可通过 admission。

这不是网络攻击场景，而是一个本地 Hook/provider adapter 的结构合法返回值。它会把另一条事实带入
settlement 或父图路由，违反唯一真相原则，故为 P0。

即使不替换 observation family，当前结果 admission 也只把
`background_task_snapshot.observation_revision` 与 receipt revision 相比（
[`observe/contract.py:819`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:819)–`:822`），
没有比较完整的 `blocking_tasks` 集合；同 revision 的不同 task facts 可以被发布。这说明问题不是
“是否允许 Hook 做业务 rewrite”的偏好，而是 rewrite/事实的来源、版本和值都没有被同一 activation
evidence 封存。

此外，ActNode 已经通过公开的 Hook admission、state/command/value 和 transition identity 做
装配期比较，可作 owner 设计对照：[`act/node.py:103`](/home/longert/motev2/mote-kernel/src/mote_kernel/act/node.py:103)。

**要求：** 由 Hooks/Observe 的真实 owner 定义一个不可伪造的 activation evidence/transition
admission，先明确并统一“哪些领域允许 value rewrite、哪些 observation facts 必须 exact 保持”；无论
最终选择 pass-through 还是有界 rewrite，都必须把来源、版本、完整 snapshot 和 exact state/value
绑定到同一 activation，并在每次 Hook 输出进入下一节点及最终 publication 前复用它。不要在 Observe
内增加影子 provenance state、第二 reducer 或兼容执行路径。

### P0-2：外部 settlement 在 Graph commit 前发生，没有统一失败/重试/并发闭包

`write_observation` 先依次调用 Config 和 Context provider，再构造 receipt/result：
[`observe/node.py:236`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:236)、
[`observe/node.py:244`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:244)、
[`observe/node.py:280`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:280)。Graph
随后才转移/发布完成结果；异常路径只关闭 session、执行 fence 并重新抛出：
[`family_driver.py:602`](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/family_driver.py:602)。

因此以下本地故障没有单一提交闭包：

```text
Config.apply 成功
Context.append 抛异常或被取消
=> Config effect 已发生，但没有 ObserveResult/Graph candidate
=> 重试未 ACK 的 batch，Config effect 可能再次发生
```

并发时两个 run 可以用同一 cursor 读取同一 delivery；Port 签名没有共同 activation key、claim 或
lease，见 [`observe/port.py:42`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:42)
及 [`observe/port.py:58`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:58)。
`_settlement_id` 直到 provider effect 返回后才组合出来：
[`observe/node.py:161`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:161)，
不能防止调用本身重复。现有并发测试让两个 mock 返回不同 batch，并未覆盖同一 cursor/同一
delivery 的竞争：[`tests/observe/test_graph.py:153`](/home/longert/motev2/mote-kernel/tests/observe/test_graph.py:153)。

两个子 Port 只收到各自的 family batch，不携带完整 outer boundary、Observe activation identity 或
共同 idempotency key。现有测试替身只能通过共享可变的 `_last_boundary` 把 read boundary 传给
provider（[`tests/observe/test_nodes.py:152`](/home/longert/motev2/mote-kernel/tests/observe/test_nodes.py:152)–`:205`），
这不是可恢复的提交证据。`_settlement_id` 反而依赖 provider 已返回的 receipt，且没有把完整
delivery-id 序列和 run/activation 坐标固定在 effect 入口，故不能作为入口 claim 或重复调用保护。

若 provider effect 已提交但响应在取消/超时中丢失，Observe 只会传播异常；没有 durable pending
effect 或 unknown-outcome reconcile。即使 receipt 成功返回，`ObservationBatchReceipt` 只保存每个
family 的相对 FIFO 约束，无法从类型恢复 Config/User 等 family 之间的原始交错顺序；ACK 入口可
收到结构合法但顺序相反的 delivery-id 序列。

现有 `GraphCommitWriteSet` 只承载 Graph input/settlement/publication 证据，没有 Observe provider
effect 或 pending ACK/wait 字段：[`execution/commit.py:62`](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/commit.py:62)。
因此“由父 commit owner 负责”目前仍是未实现的集成前提，而不是可从类型得到的保证。

空队列也有同型窗口：先建立 durable wait registration，再返回 interrupt，registration receipt
随后被丢弃：[`observe/node.py:141`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:141)。
若 Graph transition/commit 失败或取消，接口没有 unregister、pending registration 或 reconcile
入口，无法证明不会留下孤儿或重复 wake。

这直接违反实现计划规定的“先 durable commit、再 ACK”和 effect 已确认时的 receipt-based
reconcile：[`implementation-plan:842`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:842)、
[`implementation-plan:934`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:934)。

**要求：** 由唯一 Graph/commit owner 提供跨 provider 的 typed write-set、预先固定的 settlement
identity、claim/idempotency、pending ACK/wait 和 unknown-outcome reconcile；或明确装配到已有的
原子事务 owner。Observe 不应私建事务协调器、retry loop、store 或第二恢复路径。

## 高优先级（P1）

### P1-1：错误 concrete Hook binding 未在 assembly fail-closed

`HookNode` 已公开 immutable `payload_admission`：
[`hooks/node.py:220`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/node.py:220)，但
Observe assembly 没有读取和比较它，只检查 slot：
[`observe/node.py:366`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:366)。
本地构造同 slot、但绑定 `OtherState`/`OtherCommand` 的 Hook，Observe assembly 成功，直到首次
运行才可能暴露不匹配。对照 Act 的装配期比较见
[`act/node.py:103`](/home/longert/motev2/mote-kernel/src/mote_kernel/act/node.py:103)。

**要求：** 在第一次 `Graph.add_node()` 前比较 Hook 的 exact value/state/command class 和
Observe transition admission identity；失败时不返回半成品 Graph。

### P1-2：Port 的 async、arity、return contract 仍只由 callable 证明

`runtime_checkable Protocol` 加 `callable()` 只检查属性存在：
[`observe/port.py:113`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:113)、
[`observe/port.py:130`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:130)。
一个同步 `apply` 或参数数量错误的对象都能通过 assembly；本地结果为：

```text
sync apply assembly succeeded
wrong arity assembly succeeded
```

实际运行到 `await`/调用时才失败。节点中还重复做同一类 callable narrowing：
[`observe/node.py:122`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:122)、
[`observe/node.py:210`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:210)，形成
额外的重复 owner/复杂度，而不是一个可审计的 typed invocation contract。

**要求：** 复用已有 typed invocation/port contract，由 capability owner 在 assembly 提供固定的
async、arity、return、取消和 receipt/revision descriptor；节点只消费窄 capability，不用反射猜签名。

### P1-3：ACK API 没有 durable commit evidence 或 unknown-result pending

`acknowledge()` 只重新 admission result，然后调用 ACK provider：
[`observe/node.py:452`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:452)。它没有
sealed Graph commit evidence、run identity 或 pending reference。一个全新的 ObserveNode 可以接收
另一次 run 的结构合法 `ObserveResult` 并 ACK；ACK 返回未知时也没有供恢复 owner 对账的本地/持久
记录。验收记录把这一前置条件全部写成调用方约定，见
[`acceptance:194`](/home/longert/motev2/mote-kernel/docs/observe-acceptance-2026-09-06.zh-CN.md:194)，
但类型和测试没有证明该约定。

**要求：** ACK 要么完全回到唯一 Graph commit owner，要么只接受由该 owner 产生的 sealed commit
evidence；跨 provider 时在 `GraphRunState`/commit write-set 中保留 pending ACK，直到明确确认。

### P1-4：resume 可由调用方替换 state，codec 只验证形状而非语义 provenance

`resume_observation()` 直接接受 caller-provided `hook_state` 并构造新 request：
[`observe/node.py:467`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:467)、
[`observe/node.py:494`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:494)。它从
wait payload 取出 `after_cursor`，但没有把 stream/revision/wake condition 与 authoritative
Graph/queue state 绑定。Graph 通用 decoder 只要求返回 `Graph.Values`：
[`resume_input.py:111`](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/engine/resume_input.py:111)、
[`resume_input.py:120`](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/engine/resume_input.py:120)。

本地自定义 decoder 将 cursor `0` 改为合法 cursor `99` 后，resume 仍从 `99` 读取并在该 cursor
结算；同类 state 也可由调用方替换。这会跳过 durable delivery，违反 wake 后旧 candidate 作废和
revision fence 约束。

**要求：** 由 Graph/recovery owner 从持久化 continuation 产生 sealed request，绑定 scope、run、
interrupt、stream、cursor、revision 和 Hook state provenance；codec 必须做 owner-defined semantic
round-trip。Observe 不接受任意 caller state。

### P1-5：payload 只绑定 concrete class，未证明深不可变

当前 admission 确实要求四类 payload 是 assembly 绑定的 exact concrete class：
[`observe/admission.py:260`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:260)、
[`observe/admission.py:296`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:296)。
但 `ObservationPayload` 只有空 slots，wrapper 的 `frozen=True` 也不会冻结 payload 内部字段：
[`observe/contract.py:39`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:39)、
[`observe/contract.py:61`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:61)。

本地可定义一个可变 dataclass subclass：

```python
@dataclass
class Mutable(ObservationPayload):
    data: list[int]
```

将 `Mutable` 作为 `user_payload_type` 绑定后，它能通过完整 admission，随后
`payload.data.append(...)` 可原地修改。故当前 admission 只证明 exact class，不证明 deep immutable；验收
记录中“concrete immutable payload class 已修复”的表述过强：
[`acceptance:222`](/home/longert/motev2/mote-kernel/docs/observe-acceptance-2026-09-06.zh-CN.md:222)。

**要求：** 在 ingress/composition root 绑定由 owner 证明的 immutable concrete DTO（包括内部
字段），并在 queue、Hook、settlement 边界复用同一 admission；不以裸 dict/list 或宽 `object` 兜底。

### P1-6：Observe 业务节点仍走 legacy `Graph.Values`/mapping 边界，未复用 typed node compiler

Observe 在 [`observe/node.py:130`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:130) 和
`:217` 以 `Graph.Values` 接收/返回值，在 [`observe/node.py:406`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:406)–`:427`
以 `inputs={...}`、`outputs={...}` 的旧 callable 形式加入两个业务节点，并用 `cast` 恢复泛型（`:135`、
`:394`–`:395`）。这条路径绕过了已经由 compiler/typed frame owner 提供的
`Graph.bind()`、`input_type`、`materialize`、`output_type` 和 descriptor identity 约束；Act/Think
已经采用该 typed 形式（例如 [`act/node.py:150`](/home/longert/motev2/mote-kernel/src/mote_kernel/act/node.py:150)）。

这不只是复杂度指标命中：同一个 Observe boundary 同时存在静态 cast、mapping admission 和 typed
Hook descriptor 三套类型证明，调用链无法从结构看出唯一的 input/output owner；错误会延迟到节点执行
或 provider 调用。Observe 尚未完成统一 typed frame/compiler 迁移，因而不满足“复用基础设计、一次
迁移、不留第二路径”的 commit 条件。

**要求：** 使用现有 typed node/compiler contract 一次性迁移 `get_observation`、`write_observation`
的输入 materializer 与单一输出 descriptor，删除 legacy mapping/cast 执行路径；不要为此再包一层
转发 helper 或兼容 API。

## 已复核并确认关闭的旧项

以下项目本轮没有重复报为缺陷：

- `ObserveResult` 现在检查 `current_state`、delivery、cursor 和 receipt/revision 的结构一致性：
  [`observe/contract.py:793`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:793)。完整
  snapshot equality 和跨 activation provenance 仍是 P0-1，不在此项中宣称关闭。
- revision successor 的每个 child receipt 都要求匹配 snapshot，组合 receipt 还检查共同 boundary
  和 snapshot；旧的“一个 receipt 隐式补齐另一个缺失 snapshot”反例不能由正常构造路径通过：
  [`observe/contract.py:661`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:661)、
[`observe/node.py:179`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:179)。
- authoritative `deliveries`、FIFO family projection、task snapshot canonicalization、codec
  metadata 单次读取和无私有 runner/store 的方向成立；它们仍不能替代上述 provenance/commit owner。

## 文档与集成状态

### 1. 旧验收记录有过时门禁描述

验收记录仍写着 Observe collection 被 `_ExactValueAdmission` 导入错误阻断：
[`acceptance:11`](/home/longert/motev2/mote-kernel/docs/observe-acceptance-2026-09-06.zh-CN.md:11)、
[`acceptance:327`](/home/longert/motev2/mote-kernel/docs/observe-acceptance-2026-09-06.zh-CN.md:327)。本轮
已确认该描述过时：Observe 测试可以收集并通过；但这不改变仓库级门禁失败。

### 2. Hook rewrite 规则存在唯一真相冲突

验收记录的“P0-1 撤回”结论（[`acceptance:214`](/home/longert/motev2/mote-kernel/docs/observe-acceptance-2026-09-06.zh-CN.md:214)–`:223`）
与实现计划的 exact-provenance 清单相互矛盾。不能同时把 value rewrite 当作无条件合法、又要求
两次 Hook 原样保留 observation facts；当前测试只覆盖 pass-through，不覆盖这项策略裁决。提交前应
由 Hooks/Observe owner 选定一条规则，更新两份文档、admission 和反例测试；在此之前 P0-1 仍然打开。

### 3. 实现计划仍有未完成的系统 owner

实现计划的验收清单仍未勾选 ReAct 顶层结束 gate、wake 后废弃旧 completion candidate、revision
fence 以及完整门禁记录：[`implementation-plan:1181`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:1181)、
[`implementation-plan:1188`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:1188)、
[`implementation-plan:1192`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:1192)。
当前源码/测试中也没有对应的生产 ReAct route/END owner 证据；Observe 不能替父图作此决定。

另有文档不一致：生产 `BackgroundTaskPort` 已删除 `check_fence`，见
[`observe/port.py:48`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:48)，但实现计划
仍在 Port 表中列出它：[`implementation-plan:966`](/home/longert/motev2/mote-kernel/docs/observe-graph-implementation-plan.zh-CN.md:966)。
提交前必须由真实 owner 统一这一事实，不要保留无调用点的兼容字段。

## 历史门禁快照（旧工作树，不作为当前结论）

| 检查 | 当前结果 | 证据/说明 |
| --- | --- | --- |
| `PYTHONPATH=src python -B -m pytest tests/observe -q --cov=mote_kernel.observe --cov-branch` | **通过** | 235 passed；Observe branch coverage 100.00%（1281 statements/464 branches 全覆盖） |
| `python -B -m ruff check src/mote_kernel/observe src/mote_kernel/hooks tests/observe` | **通过** | All checks passed |
| `python -B -m ruff format --check src/mote_kernel/observe src/mote_kernel/hooks tests/observe` | **通过** | 19 files already formatted |
| `make typecheck` | **通过** | 0 errors、0 warnings、0 informations |
| `make check` | **失败** | lint 与 typecheck 通过；随后 complexity-ratchet 的 1 个 test 失败，故未到 package-check |
| `make complexity` | **通过（health）** | Zero-debt health target PASS；ratchet 仍报告多项超基线指标 |
| `make complexity-ratchet` | **失败** | 1 个 ratchet test 失败；top-level/type/clone/hotspot 等指标超过基线 |
| `make test` | **失败** | 2048 passed、1 complexity failure；总 coverage 99.29%（要求 100%） |
| monorepo `pre-commit run --all-files` | **失败** | 常规、ruff、rust/TS、Cloudflare、detect-secrets 通过；`kernel-complexity` ratchet 失败 |
| `git diff --check` + 本文件 trailing-whitespace 检查 | **通过** | 当前源码/已有改动及本文件均无 whitespace error |

`make test` 的 Observe 测试本身全部通过，但仓库总测试与 100% coverage 未通过；门禁通过也不能
替代前述设计裁决。

## 历史待办（已由当前实现关闭或移交真实 owner）

1. 由 Hooks/Observe owner 闭合 Hook frame/result/state provenance，并加入本地 malformed rewrite、
   wrong binding 和跨 run 反例测试。
2. 由唯一 Graph/commit owner 闭合 Config/Context effect、claim/idempotency、取消、重试、同 cursor
   并发、wait registration、ACK unknown outcome 的 durable write-set/reconcile；不得在 Observe
   内增加第二状态 owner。
3. 将 Hook concrete binding、Port async/arity/return、resume semantic binding 和 payload 深不可变
   证明固定在 assembly/typed boundary。
4. 完成 ReAct END/wake/revision-fence 集成及实现计划未勾选项，并统一实现计划与验收文档中的
   `check_fence`/门禁描述。
5. 修复当前工作树的 typecheck、lint、complexity、全量测试/coverage 和 pre-commit 问题；再完整
   重跑 `make check` 与 monorepo pre-commit。

以上 `Request changes` 是历史工作树的结论；当前 Observe-only 结论以本文开头及下述最终记录为准。

## 当前 Observe-only 门禁与 owner 交接

### 包内复核结果

| 项目 | 当前结果 | 证据 |
| --- | --- | --- |
| Observe 全量测试 + 分支覆盖率 | **通过** | `244 passed`；1301 statements、482 branches，100% coverage |
| Observe Ruff | **通过** | `python -B -m ruff check src/mote_kernel/observe tests/observe` |
| Observe format | **通过** | `python -B -m ruff format --check src/mote_kernel/observe tests/observe`；13 files already formatted |
| Observe 生产代码 Pyright | **通过** | `python -B -m pyright src/mote_kernel/observe`；0 errors/warnings/informations |
| Observe 范围 diff check | **通过** | `git diff --check` 无 whitespace error |

关键接线位于 [`node.py:350`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:350)–
[`node.py:439`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:439)：assembly 先验证
Hook admission/slot，再用 `Graph.bind`、typed materializer 和 `output_type` 装配两个业务节点，
并以 `output_ref("hook", "result")` 绑定唯一 P3 publication。`admission.py` 的
[`admit_transition`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:616) 固定
same-stage、read-only-state、exact-command 边界；`contract.py` 的 result/receipt admission 校验
delivery、cursor、boundary、完整 `blocking_tasks` snapshot 和 ACK reference 的一致性。

### 非 Observe 门禁与集成前提（不阻断本结论）

| 项目 | 当前状态 | 真实 owner |
| --- | --- | --- |
| `make typecheck` | 当前被并行修改的 `tests/execution/test_nested_output_ref.py` 报错（12 项），不涉及 Observe | execution/Graph 测试 owner |
| `make check` / complexity ratchet | 全仓指标失败；不把 Graph/Think/Act 热点归因给 Observe | 仓库/各包 owner |
| monorepo pre-commit | `kernel-complexity` 仍失败；其余既有记录通过 | 仓库 owner |
| provider 事务、幂等、unknown-outcome reconcile | Observe 仅消费 typed receipt，不持有 provider 状态 | Queue/Config/Context/ACK provider 或统一 commit owner |
| 父 Graph durable commit、pending ACK/wait | Observe 不创建第二状态 owner | Graph/commit owner |
| ReAct END/wake/revision fence | Observe 只返回 snapshot/result，不拥有顶层路由 | ReAct owner |
| concrete payload 深不可变性 | Observe 只绑定 exact nominal class；字段内部不可变性由 composition root 证明 | payload owner |

这些事项必须在各自 owner 的提交中闭合，不应通过 Observe 兼容 alias、影子状态、重试循环或第二
执行路径绕过。它们不改变 Observe 包当前的 commit 结论。

## 最终判定

**Observe 改动可以提交；本次复审不要求修改 Observe 生产代码或测试。**

这不是对整个工作树的提交许可：若提交范围包含 Graph、Hook 测试、Think/Act、ReAct、provider 或
其他并行文件，仍须由对应 owner 处理其门禁。Observe 复审本身到此结束。
