# Observe 代码实现评审（2026-09-06）

## 结论

**Request changes：当前不能提交。**

Observe 的三节点拓扑（`get_observation -> hook -> write_observation -> hook -> END`）、
窄 Port 和 immutable DTO 方向是正确的；但完整调用链仍没有证明唯一真相和分布式恢复闭包。最严重的
问题是：共享 Hook 可以返回结构合法但来源错误的 frame/result，两个外部 settlement effect 在
Graph commit 之前发生却没有 pending receipt/reconcile，ACK 和 resume 又可以脱离已确认提交证据。
这些不是网络攻击场景；用一个本地返回错误 DTO、失败或取消的 provider/Hooks adapter 就能复现。

本评审遵循以下判定顺序：先看 owner、调用链和不变量是否唯一且简单，再看门禁是否证明没有回归。
复杂度命中只作为调用链复审雷达，不单独判定缺陷；也不建议通过增加第二个 runner、state、reducer
或 legacy wrapper 来“修复”问题。

## 已核对的调用链

```text
Graph.run
  -> get_observation
       request admission
       -> ObservationQueuePort.read_after
       -> BackgroundTaskPort.snapshot
       -> ObserveFrame / AFTER_GET HookRequest
  -> shared Hook (Plan -> P1 -> P2 -> P3)
  -> write_observation
       -> ConfigObservationPort.apply (when Config family is present)
       -> ContextObservationPort.append (when non-Config family is present)
       -> ObservationBatchReceipt / ObserveResult
       -> AFTER_WRITE HookRequest
  -> shared Hook (second activation)
  -> Graph terminal publication / parent commit
  -> caller invokes ObserveNode.acknowledge()
```

当前代码确实没有私有 runner、ObserveState 或第二个 Graph 执行引擎；`GraphRunState`/commit 仍由
execution owner 管理。这些是保留项，但不能掩盖下面的边界缺口。

## 阻断项（P0）

### P0-1：共享 Hook 的跨阶段 provenance 没有被验证，可把合法 DTO 换成另一条事实

位置：
[`src/mote_kernel/observe/node.py:219`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:219)、
[`src/mote_kernel/observe/node.py:226`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:226)、
[`src/mote_kernel/observe/admission.py:634`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:634)、
[`src/mote_kernel/observe/node.py:274`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:274)、
[`src/mote_kernel/observe/node.py:281`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:281)、
以及共享 Hook P3 直接封装 invocation value 的
[`src/mote_kernel/hooks/node.py:87`](/home/longert/motev2/mote-kernel/src/mote_kernel/hooks/node.py:87)
至 `:101`。

调用链中，`get_observation` 先创建原始 `ObserveFrame`，但第一次 Hook 返回的 envelope 会直接被
`write_observation` 当作新的 frame：

```text
queue -> F_original -> Hook -> F_returned -> Config/Context Port
```

`ObservePayloadAdmission.admit_hook_result()` 只验证 outer class、stage、node id 和结构，
没有把返回值与进入 Hook 的 batch、delivery、cursor、boundary、snapshot 做 equality/provenance
绑定。一个本地 Hook adapter 将 `UserObservation("original")` 替换为结构完全合法的
`ConfigObservation("replacement")` 时，实际结果是 Config Port 被调用，Context Port 没有调用；
原始用户 delivery 被错误结算。

第二次 Hook 还有同样问题：最终 Graph output 是
`Graph.node_output("hook", "result")`，见
[`src/mote_kernel/observe/node.py:443`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:443)。
第二次 Hook 可返回一个 `ObserveResult(current_state=CONFIG, receipt=USER receipt)`；它满足现有
`HookPayloadAdmission` 的 generic shape，却会把错误的 `current_state` 暴露给父图。
[`src/mote_kernel/observe/contract.py:760`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:760)
到 `:778` 只检查字段类型和 receipt/cursor 形状，没有验证 `current_state == observation_kind`。

同一缺口也适用于 `hook_state`：`admit_hook_result()` 只验证返回 state 是配置的 concrete
subclass，并没有把它与进入本次 activation 的 state/value 绑定。Hook invocation 返回同类但
不同值的 state 会被完整发布；本地替身可让最终结果的 state marker 从 `state` 变成
`CHANGED` 而不触发错误。这直接违反实施稿要求的“不得改变 `hook_state` 的 exact class/value”，
也会把未经过 Graph state owner 提交的事实带入父图。

这违反实施稿规定的“Hook 两次激活不得改变 observation concrete class/字段、delivery、cursor、
boundary、`BackgroundTaskSnapshot` 和 `ObservationKind`”以及“Port 不得替换 payload”。风险是错误
settlement 和错误父图路由，不需要任何网络或恶意输入。

**修复方向：** provenance/invariant 应由 Hooks owner 与 Observe 的 typed frame owner 共同提供一个
可审计、不可变的 admission（例如 sealed activation evidence 或 owner-provided invariant），在
每次 Hook 输出进入下一节点和最终 publication 前验证；`ObservationKind` 也必须绑定到同一批次证据。
Observe 不应读取 Hook 私有字段，也不应新增一张本地 provenance 状态表或第二条执行路径。

### P0-2：外部 settlement 发生在 Graph commit 之前，失败/取消后没有统一 receipt 对账闭包

位置：
[`src/mote_kernel/observe/node.py:228`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:228)
至
[`src/mote_kernel/observe/node.py:288`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:288)，
以及读取/等待与结算 Port 的
[`src/mote_kernel/observe/node.py:124`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:124)、
[`src/mote_kernel/observe/port.py:41`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:41)、
[`src/mote_kernel/observe/node.py:149`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:149)，
[`src/mote_kernel/observe/port.py:56`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:56)，
[`src/mote_kernel/observe/port.py:62`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:62)。

`write_observation` 顺序执行 `ConfigObservationPort.apply()`，再执行
`ContextObservationPort.append()`，两者均返回后才构造 receipt/result；Graph 随后才有机会提交
节点 publication。因而以下本地故障是确定的：

```text
apply(config) 成功
append(user) 抛异常或被取消
=> config effect 已发生，但没有 ObserveResult/Graph candidate
=> 重试仍从未 ack 的 batch 读取，再次 apply(config)
```

同样，即使两个 settlement 都成功，after-write Hook 或 Graph commit 在父图结果发布前失败，外部
effect 也已经发生；`ObservationBatchReceipt` 最多留在当前节点的 publication/frame 中，没有被
明确纳入统一 pending/reconcile/ack write-set，调用方和恢复 owner 仍没有完整闭包。当前 Graph
worker 失败路径只关闭 session/fence（[`src/mote_kernel/execution/family_driver.py:602`](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/family_driver.py:602)
至 `:605`），不会把 Observe provider effect 加入统一 commit write-set 或 pending recovery。

Port 调用没有接收共同的 activation/idempotency key（[`src/mote_kernel/observe/port.py:56`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:56)
至 `:66`）；`_settlement_id()` 直到两个 provider 返回之后才在
[`src/mote_kernel/observe/node.py:149`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:149)
至 `:167` 组合出来，不能用于防止调用本身的重复。只校验返回 receipt 的字符串并不能证明 effect
已绑定到 exact batch。

并发重入同样没有 claim/lease 边界：`read_after()` 只有 caller cursor，没有 run/activation
身份，两个 Graph run 可以同时读取同一个未 ack batch；随后两个 `apply/append` 也没有共同 key
可让 provider 在调用入口做 CAS。即使两次 Graph publication 都提交成功，外部 effect 仍会执行两次。
现有“并发”测试只是让 mock 在两个调用中返回两个不同 batch，未覆盖同一 cursor/同一 delivery 的
竞争，因此不能证明分布式幂等。

Empty 分支还有同型的 wait-registration 窗口：[`src/mote_kernel/observe/node.py:125`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:125)
至 `:131` 先让 provider 建立 durable registration，再由 Graph 提交 interrupt；若后一步失败或
取消，registration 已经存在但没有 `unregister/reconcile` 入口。返回的 `WaitRegistration`
只被校验后丢弃，重试可能留下重复/孤儿 wake。这个副作用也不能靠“注册是 typed DTO”代替原子
commit 闭包。

这违反“一次成功提交闭包”和“外部 effect 已确认而 Hook/Graph commit 失败时必须 receipt-based
reconcile”的要求。在 provider 恰好自带跨 Port 原子事务时，当前接口也没有表达或测试这一保证；
因此不能把它当作隐含前提。

**修复方向：** 由统一 commit/settlement owner 提供 sealed settlement identity、幂等写入和
receipt/reconcile/pending-ack/pending-wait 持久化；或把相关 Port 装配成一个已有事务 owner 的窄
typed capability。wait registration 也必须绑定 Graph interrupt/commit identity，不能在 Observe
内另建取消表。
重试、补偿和恢复仍应复用 Graph 的 commit/reducer/恢复设计，不在 Observe 内增加 retry loop、私有
store 或第二 reducer。

## 高优先级（P1）

### P1-1：错误 concrete Hook binding 在 assembly 期不失败

位置：[`src/mote_kernel/observe/node.py:380`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:380)
至 `:392`，以及
[`src/mote_kernel/observe/admission.py:113`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:113)
至 `:137`。

Observe 只检查 `type(hook) is HookNode` 和 `HookSlotId`。`HookNode` 的 generic value/state/command
binding 由其内部 `HookPayloadAdmission` 持有，未作为不可变 descriptor 暴露给 Observe；因此一个绑定
`OtherEnvelope/OtherState/OtherCommand` 的 Hook 可以完成 assembly，首次运行才抛
`HookContractError`。此外 `ObservePayloadAdmission()` 默认允许 state/command type 为 `None`，形成
未绑定的宽路径。

这违反“所有 capability、slot、admission 在第一次 `Graph.add_node()` 前 fail-closed”。

**修复方向：** Hooks owner 暴露窄的 immutable concrete-binding descriptor/admission，composition
在装配期比较 exact classes；不要读取 Hook 私有字段、用反射或 `Any` 猜测泛型。

### P1-2：required Port 的 async/arity/return 契约只检查 `callable()`

位置：[`src/mote_kernel/observe/port.py:121`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:121)
至 `:149`，以及节点重复检查的
[`src/mote_kernel/observe/node.py:101`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:101)
至 `:110`、[`src/mote_kernel/observe/node.py:200`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:200)
至 `:208`。

`runtime_checkable Protocol` 加 `callable()` 不能证明 async、参数个数或返回 concrete receipt。一个
签名看似正确但同步返回 `ConfigSettlementReceipt` 的 provider 可通过 assembly，运行时在
`await config_port.apply(...)` 才抛 `TypeError`；错误 arity 也延迟到首次调用。required 字段统一
标成 `Port | None`，让静态调用者可以构造宽值并迫使每个节点重复做运行时 narrowing；虽然当前
构造器会拒绝 `None`，签名仍不能表达 required capability，也扩散了宽路径。

**修复方向：** 复用已有 typed invocation/port contract，由 Port owner 在 assembly 提供已经固定的
async、arity、return、取消和 receipt/revision descriptor；节点只消费该窄 capability，不以
`callable()` 作为异步协议证明。

### P1-3：ACK API 没有 commit evidence，也没有 unknown-result/pending reconcile

位置：[`src/mote_kernel/observe/node.py:459`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:459)
至 `:473`。

`acknowledge()` 的注释要求调用方“已经确认 Graph commit”，但运行时只做 result/ack reference
形状校验，然后立即调用 `ObservationAckPort`。一个从未运行过的全新 ObserveNode 可以接受另一个
run 的结构合法 result 并 ACK；`current_state` 即使被伪造成另一枚 enum 也不会被发现。ACK 失败或
结果未知时，Observe/Graph state 没有 pending reference 可供恢复 owner 对账。

这违反 ack 必须在 durable commit 之后、未知结果必须 receipt-based reconcile 的不变量。

**修复方向：** 将 ACK 编排放回统一 commit owner，或让该方法只接受由 commit owner 产生的 sealed
commit evidence；跨 provider 时在唯一 `GraphRunState`/commit write-set 中保留 pending ack，直到
明确确认。不要在 Observe 内建内存状态表。

### P1-4：resume 接受调用方替换 state，且 codec 只做形状 round-trip，不做语义绑定

位置：[`src/mote_kernel/observe/node.py:475`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:475)
至 `:509`，[`src/mote_kernel/observe/port.py:82`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:82)
至 `:101`。

resume helper 解码 interrupt 的 `ObservationWait` 后，只使用 `wait.after_cursor`，丢弃
`observation_revision`/`wake_condition`，并直接把 caller 提供的 `hook_state` 塞进新
`ObserveRequest`。只要 concrete class 合法，替换成另一个同类 state 就会传播到全部 Hook activation。
同时 `ObservationResumePort` 只要求 codec 可调用；Graph 的通用 resume admission 仅验证解码结果是
合法 `Graph.Values`/声明类型。一个 decoder 将 cursor 0 的请求改成合法 cursor 99，resume 仍会从 99
读取，可能跳过 durable delivery。

这违反 exact Hook state/value、wake 后旧 candidate 作废及 revision fence 的恢复不变量。

**修复方向：** 由 Graph/queue recovery owner 从 durable continuation 产生 sealed request，绑定
interrupt id、scope、stream、cursor、revision 和 state provenance；codec 必须有 owner-defined 的
语义 round-trip/fence。Observe 不接受任意 caller state，也不另建恢复状态模型。

### P1-5：observation payload 没有 concrete class/深不可变绑定

位置：[`src/mote_kernel/observe/contract.py:49`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:49)
至 `:52`、`:55` 至 `:92`，以及
[`src/mote_kernel/observe/admission.py:258`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:258)
至 `:287`。

四个 observation wrapper 的 payload 类型是无约束的 `ObservationPayloadT`；admission 重新构造 wrapper
仍只拒绝 `None`。`ConfigObservation({"mutable": 1})` 可以通过 admission，随后字典仍可被调用方
原地修改。`frozen=True` 只冻结 wrapper，不冻结 payload；这也让裸 dict/list/function 能跨 typed
boundary 进入 Graph。

这违反四个 exact payload class、不可变 nominal DTO 和“不用裸字典/Any 作为内部边界”的规则。

**修复方向：** 在 queue/config adapter 与 composition root 绑定四个 concrete immutable payload
class，并让同一 typed frame/admission owner 在 ingress、Hook 和 settlement 复用；Observe 不解析 wire
payload，也不接受一个 `object`/union 的兜底类型。

### P1-6：revision successor 的 snapshot 证据可被一个 receipt 缺失而另一个 receipt 掩盖

位置：[`src/mote_kernel/observe/contract.py:498`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:498)
至 `:515`、`:517` 至 `:534`，以及
[`src/mote_kernel/observe/node.py:170`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:170)
至 `:188`。

`ConfigSettlementReceipt`/`ContextAppendReceipt` 的 `background_task_snapshot` 在 successor revision
时仍可为 `None`；`_snapshot_for_settlement()` 只要从另一个 receipt 找到一个 snapshot 就接受整个
settlement。一个 Config receipt 带 revision 2 的 snapshot、Context receipt 同样推进 revision 2
但不带 snapshot 的组合，当前 `ObserveNode` 会成功返回 revision 2 的结果。这样无法证明两个外部
effect 使用同一时间点的 task evidence。

**修复方向：** 在统一 settlement owner 明确定义 snapshot 的唯一 owner：要么每个推进 revision
的 receipt 都必须携带并相互验证 successor snapshot，要么由一个 sealed atomic receipt 同时覆盖
两个 effect；不能用“任一 receipt 有值”隐式补齐证据。

### P1-7：`check_fence` 只是装配字段，没有接入任何提交路径

位置：[`src/mote_kernel/observe/port.py:50`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/port.py:50)
至 `:52`；生产代码中没有 `BackgroundTaskPort.check_fence()` 的调用点，而
[`src/mote_kernel/observe/contract.py:754`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:754)
至 `:778` 的 `ObserveResult` 只携带 snapshot，不携带已确认的 fence/commit evidence。

实施稿要求父图在任何正常 END 前以 observation revision 做 CAS/fence；当前 Observe assembly
反而把 `check_fence` 当成必需 Port 并只检查它可调用，却没有把结果交给 Graph commit 或父 ReAct
owner。若父图沿普通成功结果直接提交 END，后台任务在 snapshot 之后更新时就没有可执行的
拒绝点。由于 ReAct 顶层 gate 属于父 owner，这里是集成阻断而非要求 Observe 自建第二个 gate；
在宣称 v1 完成前必须有父 owner 的真实调用点和 stale-revision deterministic test。

**修复方向：** 让现有 Graph/lifecycle commit owner 消费 sealed `RevisionFence`，把 fence/CAS
作为同一 `GraphRunState` candidate 的提交前置条件；Observe 只提供其 typed evidence，不轮询
或维护本地 fence 状态。

## 设计债（P2）

### P2-1：`ObservationBatch` 同时保存权威 deliveries 和四份派生 projection

位置：[`src/mote_kernel/observe/contract.py:289`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:289)
至 `:314`。

代码声明 `deliveries` 是 authoritative，却把 `_config/_tool/_user/_assistant` 作为第二份存储表示；
admission 还要重建 canonical batch 再逐项比较（[`src/mote_kernel/observe/admission.py:309`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/admission.py:309)
至 `:333`）。这不是状态值错误，但制造镜像表示、反序列化 stale projection 风险和额外 owner 判断，
与“唯一真相、无镜像状态”不一致。

**修复方向：** 保留 `deliveries` 为唯一存储 owner，由一个 canonical projection 逻辑按需派生；若
需要缓存，也应由同一不可变 index owner 明确定义并禁止第二条 admission 语义。

### P2-2：task snapshot 语义是集合，但 tuple 顺序没有 canonicalize

位置：[`src/mote_kernel/observe/contract.py:496`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/contract.py:496)
至 `:512`，[`src/mote_kernel/observe/node.py:177`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:177)
至 `:188`。

文档把 `blocking_tasks` 定义为集合元素，但 dataclass equality 对 tuple 顺序敏感；两个 provider
返回相同 task incarnation 的不同顺序会被判断为 conflicting snapshot，造成可重试的分布式 liveness
失败。应在唯一 identity owner 中定义排序或 set-equivalence，并保留唯一序列化表示。

### P2-3：codec metadata 验证与安装读取了两次

位置：[`src/mote_kernel/observe/node.py:368`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:368)
至 `:379`、[`src/mote_kernel/observe/node.py:412`](/home/longert/motev2/mote-kernel/src/mote_kernel/observe/node.py:412)
至 `:416`。

构造器先把 `codec_id`/`codec_version` 读入局部变量并验证，安装时却重新读取 provider 属性。动态
provider 可以出现“验证 A、安装 B”，恢复时产生不可解释的 codec mismatch。应把已验证的局部值传给
Graph；不要引入第二个 metadata owner。

### P2-4：重复规则和节点转发增加总调用链复杂度

复杂度扫描命中了 `admit_observation` 的四段近同分支、Observe/Act 重复的 `_hook_result`、
`identity.py` 的 `_require_text` 与 `contract.py` 的 `_require_identifier`，以及 Config/Context
receipt 的对称校验。四个 closed family 分支本身是必要的，不能为了指标机械抽成宽 helper；但相同的
canonical text、Hook result admission 和 receipt identity 规则应回到各自唯一 owner，保留领域异常边界。

## 本地可复现证据

使用测试替身运行 `/tmp/probe_observe.py`（`PYTHONPATH=.`，不访问网络），并用同一组本地替身
补充 Hook-state 与同 cursor 并发 probe，得到：

```text
configs [(DeliveryId(value='evil'),)] contexts []
wrong assembly succeeded
sync assembly succeeded
sync run error TypeError object ConfigSettlementReceipt can't be used in 'await' expression
partial effects configs [(DeliveryId(value='c'),), (DeliveryId(value='c'),)]
post-settlement effects configs [] contexts [('post',)]
ack without local run [(DeliveryId(value='ack'),)] forged kind config
resume state markers ('REPLACED', 'REPLACED', 'REPLACED', 'REPLACED', 'REPLACED', 'REPLACED')
bad codec requested cursors (0, 99)
final rewritten kind config
hook state replacement CHANGED
same-cursor concurrent effects [('x',), ('x',)]
```

这些输出分别对应 P0-1/P0-2、P1-1/P1-2、P1-3、P1-4；新增的 `hook state replacement` 和
`same-cursor concurrent effects` 分别证明 Hook state provenance 与同一 delivery 的并发重复写入。
`partial effects` 和
`post-settlement effects` 是 provider 成功后再失败/取消的恢复边界，不是攻击流量或远程未授权测试。
两个补充 probe 都只替换本地 adapter：前者让 Hook 返回同 concrete class 的新 state，后者让两个
并发 `read_after(cursor=0)` 返回同一 immutable `Available`；没有网络、未授权或渗透步骤。

## 已有的正向部分

- `observe/__init__.py` 只导出 `ObserveNode`，没有平行公共 facade。
- `get_observation` 对 Empty/Conflict 的处理不写 settlement、不 ack，且 wait registration 是 typed
  DTO；冲突 batch 不被静默截断。
- `ObservationBatch`/receipt admission 对 cursor 连续性、delivery-id 去重、revision successor 形状
  做了较多 fail-closed 检查。
- 节点使用 predecessor-bound `Graph.node_output("result")`，没有为第二次 Hook 复制 runner。
- cancellation 会沿 async Port 传播；问题在于外部 effect 已发生后的 pending/reconcile，而不是需要吞掉
  取消异常。

这些优点不能抵消 P0 的 effect/provenance 闭包缺口。

## 门禁证据（当前工作树）

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| `python -B -m pytest tests/observe/test_contract.py tests/observe/test_nodes.py -q --tb=short -p no:cacheprovider` | 通过 | 105 passed；只覆盖 happy/局部 failure path |
| Observe branch coverage | **失败** | 105 passed，但 `--cov=mote_kernel.observe` 总覆盖率 83.57%，项目 `fail_under=100` |
| `python -B -m pytest tests/observe tests/typing_negative -q --tb=short -p no:cacheprovider` | 通过 | 197 passed；未覆盖上述 provenance/reconcile 语义负例 |
| `make typecheck` | 通过 | 0 errors；不能替代运行时 concrete/provenance admission |
| `make check` | **失败** | lint/typecheck 通过，`complexity-ratchet` 失败；全局指标超 pyproject 基线 |
| `make test` | **失败** | 1855 passed、1 failed（complexity ratchet）；总覆盖率 97.78%，低于 100% |
| `git diff --check` | 通过 | 无 whitespace 错误 |
| monorepo `pre-commit run --all-files` | **失败** | 普通 lint、format、secret、Cloudflare、rustfmt 等检查通过；`kernel-complexity` 失败（当前工作树有大量跨包未提交改动，结果不能归因于 Observe 单包） |

复杂度 ratchet 的当前实际值包括：top-level definitions 948（基线 727）、type definitions 591
（432）、decision points 2918（2247）、cognitive complexity 3733（3012）、internal call edges
1165（788）、complexity hotspots 117（89）。这些是高召回复审信号；其中 Observe 的具体命中已在
P2-4 关联到真实重复规则，不能单独作为 P0 结论。

## 范围边界

- ReAct 顶层 Completion gate、blocking-task wait、revision fence 以及四类 ingress provider 的
  最终实现属于父图/后续 phase；本评审不把尚未进入 Observe package 的功能全部归罪于 Observe。
  但 Observe 不能因此宣称完整 v1，尤其是其 receipt/ACK API 必须与这些 owner 有明确接缝。
- Graph 首次 compile 前的 builder mutation window 是 execution 的既有设计，不以此机械新增 finding。
- Observe 按计划不解释、累计、去重或 apply Hook commands；本评审不把 command 未落地误报为缺陷。
- 所有动态负例均为本地 typed boundary、provider 故障/取消、重试或并发场景，不涉及网络攻击、渗透或
  远程未授权行为。

## 重新提交前置条件

1. 由 Hooks/Graph/commit owner 关闭 P0-1/P0-2，并提供 frame/result provenance、unknown outcome 和
   receipt reconcile 的 deterministic tests。
2. 让 Hook concrete binding、Port async/arity/return、resume codec/state provenance 在 assembly
   期 fail-closed；删除默认未绑定的宽路径。
3. 使 ACK 只能基于 sealed durable commit evidence，或把 pending ACK 纳入唯一 `GraphRunState`；同时
   将 `BackgroundTaskPort.check_fence` 接入现有 Graph/lifecycle commit owner，并覆盖 stale-revision 拒绝。
4. 绑定四类 immutable payload class，定义 successor snapshot 和 task 集合的唯一 canonical owner。
5. 删除镜像 projection/重复规则/metadata 双读等非必要表示；不新增 alias、wrapper、兼容执行路径或
   Observe 私有 state/runner。
6. 补齐上述故障、取消、重试、并发和恢复测试，达到 100% 覆盖率；在隔离无关工作树改动后，使
   `make check` 与 monorepo pre-commit 全部通过。

在这些条件完成前，建议保持 **Request changes**，不能以局部测试通过、pyright 通过或复杂度指标
“解释掉”上述设计问题。
