# Failover P1 代码评审（2026-09-05）

状态：**代码 review 通过；本轮未运行测试和门禁**

本记录对应当前工作树相对 `HEAD=1b46801` 的 Failover P1 增量。判定顺序是先看设计、owner
和完整调用链是否足够简单且唯一，再把测试/复杂度等门禁作为实现回归证据；不以复杂度数字替代设计判断。

## 1. 范围和排除项

纳入本轮的改动：

- `src/mote_kernel/failover/contract.py`：单次调用和 reconcile capability 的 typed contract；
- `src/mote_kernel/failover/plan.py`：删除 profile 内策略表/策略 ID，保留参数化 profile，移除
  `RetryContext` 中复制的 budget；
- `src/mote_kernel/failover/policy.py`：固定 `(status_code, error_hint)` 映射、路由、预算检查和
  typed decision；
- 对应的 Failover 测试、设计文档和复杂度 ratchet 注释/基线。

明确排除：

- `port`、Graph、Hooks、Think 及其它并行工作树改动；
- 当前尚未实现的 `assembly.py` 运行时组装、具体 provider、持久化适配器、durable loader/reconcile
  和 crash-safe durable concrete-value recovery；
- hostile 内部对象、手工伪造状态以及纯理论网络攻击面；
- 仅由复杂度热点命中的机械拆 helper 或提高 ratchet 的建议。

当前 `assembly.py` 仍只有职责说明，因此本轮评审的是已交付的 contract/plan/policy 基础层，
不把它误报为完整的 Failover runtime 已经交付。

## 2. 实际调用链

```text
Role/Flow config snapshot + Port binding
    -> resolve_plan()
    -> immutable FailoverPlan(profile parameters, plan_revision)

typed Port result
    -> FailureEvidence / PortOutcome
    -> observe_and_route()
       ├─ Rejected -> route_rejected()
       │              -> policy.py fixed exact pair lookup
       │              -> plan budget/cursor guard
       │              -> typed preparation or terminal route
       ├─ Unknown/InProgress -> route_uncertain()
       │              -> handle/reconcile-mode/budget guard
       │              -> reconcile or return-to-model
       └─ Completed -> completed terminal route

immutable FailoverDecision
    ->（后续由 Graph/assembly owner 推进下一次 activation）
```

这条基础层调用链只做观察和纯决策：不调用 Port、不 sleep、不读热配置、不修改 cursor，也没有
第二个 runner、reducer 或策略执行路径。

## 3. 评审台账

| 检查项 | 结论 | 依据 |
| --- | --- | --- |
| 固定策略唯一 owner | 通过 | `policy.py` 的 `_FIXED_RULES` / `_fixed_strategy()` 是唯一生产映射；`FailoverProfile` 与 override 已删除 `FailureRule`、`FailoverPolicyId` 和可配置规则表 |
| 策略键精确性 | 通过 | 只比较 `FailureEvidence.signal` 的完整 `(status_code, error_hint)`；`FailureClass`、message、provider code 不参与匹配；无精确项直接回到模型 |
| profile/override 权限边界 | 通过 | profile/override 只携带 budget、timing、semantics、reconcile 和 typed request-transform instruction，不能增删或替换策略映射 |
| Transform 路径 | 通过 | 固定 `400 + bad_payload` 只在 profile 提供 `TransformRequest` 时进入 prepare；缺少 instruction 时 fail closed 回到模型，不伪造 action |
| plan/context revision | 通过 | `route_rejected()`、`route_uncertain()` 和 `observe_and_route()` 均要求 context 与 plan 的 captured revision 精确相等，错配不继续执行 |
| budget 单一归属 | 通过 | limit 只由 `FailoverPlan.profile.budget` 持有；`RetryContext` 只保存 strategy usage/cursor；策略次数和 wire-attempt ceiling 在准备动作前检查 |
| strategy usage 更新 | 通过 | `RetryContext.with_strategy_use()` 返回新 immutable context，不就地修改旧快照；usage 仍按 strategy 去重 |
| Unknown / InProgress 边界 | 通过 | Unknown 没有 handle 时只回到模型；有 handle 且允许 reconcile 才进入 reconcile；路径不重新 submit；InProgress 走同一 reconcile 分支 |
| terminal 边界 | 通过 | 固定 forbidden/conflict 映射分别落到 return-to-model/abort；终端策略不绕过预算制造下一次 invocation |
| 单次 capability | 通过 | `SingleAttempt` 和 `ReconcileAttempt` 仍是窄的异步 Protocol；本增量没有引入 decorator 内 retry loop 或第二执行器 |
| immutable value / secret 边界 | 通过（当前层） | contract、profile、plan、context、decision 均为 frozen/slots 值；具体 receipt/codec 和持久化安全仍由后续 owner 负责，未在本轮伪装实现 |
| legacy 迁移 | 通过 | 生产 Python 路径已无 `FailureRule`、`FailoverPolicyId`、`FailureRoute`、`fixed_rules()`、`find_rule()` 或旧 context budget 的残留调用；测试已迁移到唯一新路径 |
| 复杂度 ratchet | 通过（不作为质量证明） | 删除重复规则和 context budget 后基线相应下降；没有用薄转发、宽 context 或兼容 alias 掩盖调用链 |

## 4. Findings

本轮**没有发现**能由正常公开 Failover 调用触发、并改变路由结果、预算边界、状态唯一性或异常/恢复语义的真实 finding。

特别复核但不升级为问题的点：

- 固定表中的 terminal 和 transform 条目仍由同一个 policy owner 管理，不存在 profile 覆盖或 fallback 第二表；
- 未知 status/hint 不会被类别或自由文本猜成 retry，保持 fail-closed；
- revision 校验覆盖三个公开 route 入口，旧 context 不会在当前 policy 层静默套用新 plan；
- `RetryContext` 不再复制 budget，避免恢复或热加载时出现两份限额真相；
- `@runtime_checkable` 只扩展 capability 的声明形式，当前没有用它建立反射式执行或旁路 runner；
- 复杂度数字降低是删除重复结构的结果，不构成“指标通过即设计正确”的替代证明。

## 5. 未承诺能力和后续边界

本轮没有把以下内容写成缺陷，也没有据此宣称 Failover 已具备生产级持久恢复：

- 固定图的具体 Graph assembly、Port decorator 和真实 provider 接入；
- durable plan/context/receipt 存储、跨进程加载、ack-lost read/reconcile；
- crash-safe durable concrete-value recovery、外部副作用 exactly-once 或数据库事务实现；
- timeout/deadline/hard-cap 在实际 scheduler/adapter 中的执行。

这些能力应由后续 Graph、Runtime 或 persistence owner 在同一 typed/commit 边界实现；当前基础层没有偷偷
建立兼容路径或第二状态 owner。

## 6. 测试和门禁

按本轮明确要求，评审完成后**未运行测试、覆盖率、`make check` 或 pre-commit**。因此本文只给出静态代码
review 结论，不把历史门禁输出冒充当前工作树的门禁证明。

## 7. 最终结论

**Failover P1 当前增量通过代码 review；没有真实 finding，不需要修改 Failover 生产代码或测试，至此停止扩张审查。**

从代码 review 角度可以作为 commit candidate；是否满足提交流程的完整门禁，仍需在单独允许的门禁轮次中验证。
该结论只覆盖本记录第 1 节的基础 contract/plan/policy 改动，不等同于完整 Failover assembly 或 durable recovery 已经完成。

## 8. 本轮复核追加（2026-09-05）

按后续复审要求重新核对当前工作树：

- 逐项对照 `contract.py`、`plan.py`、`policy.py` 的实际 diff，确认策略表、预算、cursor 和 route
  没有重新出现第二 owner 或兼容执行路径；
- 以仓库范围检索确认旧的 `FailureRule`、`FailoverPolicyId`、`FailureRoute`、`fixed_rules()`、
  `find_rule()` 没有生产调用残留；
- 复核 `Rejected`、`Unknown`、`InProgress`、预算耗尽和 plan revision 错配的正常调用链；没有发现
  可由正常公开调用触发的错误路由、重复提交或状态越界；
- 未把 `assembly.py`、provider、持久化和 crash-safe durable concrete-value recovery 的未实现能力
  升级为本次 P1 finding，仍按既定 owner 和范围留给后续实现。

本次仍不运行测试或门禁，也不修改 Failover 生产代码；结论维持第 7 节。
