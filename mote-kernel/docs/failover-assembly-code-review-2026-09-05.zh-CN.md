# Failover 最新代码评审（2026-09-05）

状态：**静态代码 review 通过；未发现真实 finding；本轮未运行测试或门禁**。

本记录以当前工作树的 Failover 实现为准，补充并更新早期只覆盖基础
`contract/plan/policy` 的评审记录。README、`port` 改动以及其它并行领域不在本轮范围。
评审先判断 owner、调用链和异常边界是否足够简单且唯一，再用既有 Graph 提交语义检查分布式边界；
不把复杂度热点、恶意伪造对象或纯理论网络攻击面直接当成缺陷。

## 1. 评审范围

- `src/mote_kernel/failover/assembly.py`
- `src/mote_kernel/failover/contract.py`
- `src/mote_kernel/failover/plan.py`
- `src/mote_kernel/failover/policy.py`
- 对应 Failover 测试和设计约束（只作静态对照）

具体持久化适配器、跨进程 durable loader、以及 crash-safe durable concrete-value recovery 按已确认的边界留给后续
persistence owner，本轮不把它们误报为阻塞项。

## 2. 实际公开调用链

```text
Failover(...)(single_attempt_port)
  -> 一个固定 nested Graph
     START -> load_plan -> invoke -> observe
                         ^             |
                         |             +--[prepare]--> prepare --+
                         |                                      |
                         +--------------------------------------+
                                       |
                                       +-- finish -> END
```

运行时的值流为：

```text
FailoverCall
  -> LoadPlanOnce：读取一次 config snapshot，生成 FailoverPlan + RetryContext
  -> InvokeOnce：一次 activation 至多调用一次 SingleAttempt
  -> ObserveAndRoute：纯 policy 决策
       Rejected -> 固定策略/预算检查 -> Prepare 或 Finish
       Unknown / InProgress -> Finish，原样返回调用方
       Completed -> Finish
       ABORT -> Graph.failure
  -> PrepareNextAttempt：执行一个 typed PreparationAction，生成下一次 request/context
  -> Finish：投影唯一 FailoverResult
```

`invoke` 的 `Graph.node_output("frame")` 是 compiler 解析的实际控制前驱绑定：首次来自
`load_plan`，循环来自 `prepare`。`observe`、`prepare`、`finish` 只读取其唯一固定的
`observe`/`invoke` publication。没有 decorator 内部 `while`、私有 runner、第二 reducer 或旁路状态。

## 3. 评审台账

| 检查项 | 结论 | 依据 |
| --- | --- | --- |
| 固定策略唯一 owner | 通过 | `policy.py` 的 `_FIXED_RULES`/`_fixed_strategy()` 唯一维护 `(status_code, error_hint)` 映射；profile/override 不能增删策略 |
| 配置与 operation 隔离 | 通过 | `LoadPlanOnce` 每个 operation 只读一次 snapshot；plan 和 context 捕获同一 revision，后续 retry 不重新读热配置 |
| typed frame 与状态 owner | 通过 | `_FailoverFrame` 不可变地携带 plan、request、step 和 `RetryContext`；Graph/`GraphRunState` 仍是唯一执行与提交 owner |
| 前驱 publication 选择 | 通过 | causal predecessor 由 compiler 和 state-owned activation cause 精确选择，不扫描“最新值”，循环两条路径不会混用 frame |
| 原子提交/分布式边界 | 通过 | 每个节点 settlement 和 publication 进入现有 `GraphTransition`/`Graph.Commit`；只有 exact candidate 确认后内存 frame/state 才前移，旧 worker 不能越过 execution token |
| 预算与 cursor | 通过 | budget 只在 `FailoverPlan.profile.budget`；`RetryContext` 只保存 strategy usage、attempt、endpoint/credential/request cursor；策略和 wire-attempt 上限在准备前检查 |
| preparation 归属 | 通过 | 所有下一次调用前动作共用唯一 `AttemptPreparation.prepare_next` typed contract；没有按策略复制执行路径，也没有隐式 retry loop |
| Unknown / InProgress | 通过 | 两者都进入 terminal `Finish` 并原样携带 Port-owned 内容；不 poll、不 reconcile、不重新 submit，不伪造 durable ledger 或 ghost activation |
| Completed / Rejected / ABORT | 通过 | Completed 只能形成成功终态；未知 rejection fail-closed 返回模型；固定 forbidden/conflict 分别回模型/Graph failure，终态不再创建 attempt |
| 取消边界 | 通过 | `CancelledError` 从单次 Port 调用向 Graph 既有取消路径传播，不由 Failover 自行启动下一次调用 |
| nested Graph 组合 | 通过 | 装饰结果就是普通 `Graph` nested node；父图负责 child state/evidence，Failover 不创建第二执行引擎 |
| 外部 capability admission | 通过 | 单次 Port 及 preparation capability 在装配期按窄 Protocol 检查，运行时至少拒绝非声明的 outer outcome/PreparedRequest |
| legacy 与唯一入口 | 通过 | 生产 Failover 包仅导出 `Failover`；旧 reconcile route/handle 执行路径已删除，没有兼容 alias 或 wrapper |

## 4. Findings

本轮**没有发现**能够由正常公开 Failover 调用触发，并改变以下任一语义的真实问题：

- 路由结果或固定策略归属；
- retry budget、attempt/cursor 或 plan revision 边界；
- publication 与实际控制前驱的对应关系；
- Graph settlement/commit 后的状态唯一性；
- Unknown/InProgress、ABORT、取消和 nested Graph 的公开行为。

## 5. 复核后不升级为 finding 的边界

- **具体持久化与 crash-safe durable concrete-value recovery**：当前明确由后续 persistence adapter 实现，不能据此否定本轮代码。
- **timeout/deadline/wait 的真实调度**：当前只生成 typed action/参数，scheduler/adapter 执行属于后续 owner。
- **generic payload 的运行时深度校验**：当前公开 contract 依赖具体 Port 的静态泛型契约；没有把构造恶意内部值当成正常调用缺陷。
- **`OperationSemantics` 的路由使用**：确定性 `Rejected` 才允许进入固定准备策略；不确定结果已经终止返回，外部 Port 仍拥有业务幂等/版本判断，未出现可观察的重复提交路径。
- **ABORT 的证据投影**：当前 Graph failure contract 只承载统一字符串，现有公开行为与该 contract 一致；没有另造第二种错误出口。

## 6. 测试与门禁

遵照本轮要求，评审完成后**未运行测试、coverage、`make check` 或 pre-commit**，因此本记录只给出静态设计/代码结论，不把门禁通过当作质量证明。

## 7. 最终结论

**Failover 最新代码通过 review，无真实问题，不需要修改生产代码或测试；本轮到此停止。**

从代码 review 角度可以作为 commit candidate；完整提交流程是否满足类型、测试、覆盖率、复杂度、架构和 pre-commit 门禁，需在另一个明确允许运行门禁的轮次确认。
