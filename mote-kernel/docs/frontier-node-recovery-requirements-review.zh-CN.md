# Frontier 节点统一恢复需求第三轮复核

## 1. 评审对象

- 需求文档：`docs/frontier-node-recovery-requirements.zh-CN.md`
- 基线需求：`docs/frontier-node-resume-requirements.zh-CN.md`
- 关联方案：`docs/frontier-node-resume-implementation.zh-CN.md`
- 评审轮次：第三轮
- 评审范围：第二轮阻塞项关闭情况、interrupt identity owner、稳定 snapshot 可验证性、唯一真相与范围控制

## 2. 评审结论

第二轮提出的 `interrupt_history` 与单 activation interrupt 限制已经正确删除：

- 当前 interrupt identity 只存在于 `InterruptedGraphNode`；
- resume 后旧 identity 随当前 settlement 一起消费；
- 同一 activation 的新 attempt 可以再次 interrupt；
- 新 identity 继续由 activation coordinates 与新 execution generation 派生；
- GraphState 不保存 interrupt history、counter、index 或 resume-value tape。

同时，`Succeeded + Skipped` 已明确为 reducer 内部瞬时 settled candidate，codec 也补充了 deterministic、side-effect-free 合同。

当前只剩 1 个 owner 边界问题：需求把“interrupt identity 的 generation 来自生成该 settlement 的精确 lease token”列为稳定 snapshot validator
不变量，但 lease 在 settlement 提交后已被清除。在不保存 lease history/journal 的前提下，恢复出的稳定 snapshot 无法重新证明该历史来源。

该问题只需修正 reducer、snapshot validator 与 resume reducer 的责任表述，不需要改变核心状态模型。修正后本需求可以正式通过。

## 3. 第二轮问题关闭情况

### 3.1 Interrupt history 已删除

当前状态模型已经收敛为：

```python
@dataclass(frozen=True, slots=True)
class GraphNodeInterrupt:
    identity: GraphNodeInterruptIdentity
    request_payload: GraphInterruptPayload


@dataclass(frozen=True, slots=True)
class InterruptedGraphNode:
    interrupt: GraphNodeInterrupt
```

`GraphFrontierNode` 只保存 `node_id` 与当前 settlement，不再保存额外的 interrupt lifecycle/history 字段。因此当前 outstanding interrupt 只有一份
authoritative representation。

### 3.2 多轮 interrupt 状态流已闭环

当前状态流为：

```text
Pending
  -> Interrupted(identity from current execution generation)
  -> Pending(override)
  -> Succeeded | Failed | Interrupted(new generation identity)
```

旧 identity 不保留，新 identity 由新 attempt generation 派生。该模型既能拒绝 stale resume/settlement，又没有引入历史列表或第二套 counter。

### 3.3 Settled snapshot 表述已修正

`Succeeded + Skipped` 已从合法长期稳定状态中移除，并明确为 reducer 内部瞬时 settled candidate。产生最后一个 skip 的同一
`ResumeGraphNodes` command 必须原子 advance/complete，不能提交长期 `SETTLED` GraphState。

### 3.4 Codec 合同已补全

唯一 resume input codec 已明确要求：

- deterministic；
- side-effect-free；
- 相同 codec identity/version 与等价输入产生相同 payload；
- 相同 payload 解码为等价 `InputT`；
- 不读取隐藏可变状态；
- 不执行业务 IO、外部副作用或协调。

这保证 claim 前预校验与 claim 后 committed snapshot 重新物化具有相同语义。

## 4. 当前阻塞项：历史 lease 来源不能由稳定 snapshot 重新证明

### 4.1 问题

需求当前要求：

```text
Interrupted identity 的 execution generation
必须来自生成该 settlement 的精确 lease token
```

该条件在处理 `SettleGraphExecution` 时可以严格证明，因为 reducer 当时同时持有：

- 当前 `GraphRunState.execution`；
- exact execution token；
- lease 的 claimed node subset；
- `InterruptedGraphNodeOutcome.identity`；
- 当前 run ID、superstep 与 node ID。

但 settlement 原子提交后：

- active lease 被清除；
- resources 被清除；
- 后续 attempt 可能继续增加 `execution_sequence`；
- GraphState 不保存 lease history、attempt journal 或 interrupt history。

因此，未来从稳定 `GraphRunState` snapshot 恢复时，validator 只能看到 identity 中记录的 generation 和当前累计
`execution_sequence`，无法重新证明该 generation 在历史上确实属于生成该 interrupt settlement 的精确 lease。

如果要求稳定 validator 完成该证明，就必须增加 lease history/journal，违反本需求的非目标和零额外历史原则。

### 4.2 正确的责任划分

#### A. Settlement reducer

`SettleGraphExecution` reducer 必须进行最强校验：

1. command execution token 与当前 active lease token 精确匹配；
2. outcomes 精确且唯一覆盖 lease node IDs；
3. interrupt outcome 只更新 lease 中当前 Pending activation；
4. 使用 state-owned 纯函数从以下坐标重新派生 identity：

```text
(run_id, superstep, outcome.node_id, active_lease.token.generation)
```

5. 重新派生的 identity 与 `InterruptedGraphNodeOutcome.identity` 完全一致；
6. 只有通过上述校验后，才把 identity 写入 `InterruptedGraphNode` 并原子清除 lease/resources。

精确 lease 来源证明只发生在这里，不能委托给 execution guard，也不能延迟到稳定 snapshot validation。

#### B. 稳定 snapshot validator

对已经提交的 `GraphRunState`，validator 只验证当前可证明的 state-owned 事实：

1. `InterruptedGraphNode` 同时保存 identity 与合法 request payload；
2. identity 的 run ID、superstep、node ID 与当前 GraphRun/node activation 坐标一致；
3. identity generation 为正数；
4. identity generation 不大于当前 `execution_sequence`；
5. 当前 Frontier 内所有 outstanding interrupt identity 及其派生 `GraphInterruptId` 唯一；
6. terminal/ABORTED 生命周期与 retained diagnostic Frontier 组合合法；
7. 不尝试重新证明历史 lease token，不要求存在 lease history。

`generation <= execution_sequence` 只证明 identity 没有引用未来 generation，不等价于历史 lease 来源证明；文档应明确这一边界。

#### C. Resume reducer

`ResumeGraphNodes` reducer 必须验证：

1. expected revision 精确匹配；
2. scheduler quiescent，无 active lease/resources；
3. `ResumeInterruptedNode` 只作用于当前 `InterruptedGraphNode`；
4. action node ID 与当前 activation 一致；
5. action 的 `interrupt_id` 等于当前 `InterruptedGraphNode.identity` 通过唯一 state-owned 投影函数得到的 ID；
6. stale、wrong 或已消费 identity 均被拒绝；
7. 成功后将 settlement 转为 `Pending(OverrideGraphNodeInput)`，不保留旧 identity。

#### D. Execution projection/guard

Execution 继续负责：

- compiled graph definition/version 匹配；
- frontier node membership；
- routing contribution/topology；
- codec identity/version；
- task identity 与 activation coordinates 的派生；
- resume request 的 typed input encode。

Execution guard 不能代替 reducer 对 exact lease、outcome coverage 和 identity generation 的 authoritative transition validation。

### 4.3 文档应修改的位置

建议同步修订：

1. 第 10.1 节 interrupt settlement：明确 exact lease 来源证明只属于 settlement reducer；
2. 第 14 节状态不变量：将“generation 必须来自精确 lease”改为稳定 snapshot 可验证条件；
3. 第 16 节验收标准：分别测试 transition-time exact lease validation 与 recovered snapshot validation；
4. 第 17 节实施约束：禁止为稳定 validator 增加 lease history、interrupt history 或 journal。

建议的稳定不变量表述：

```text
Interrupted identity 的 run/superstep/node 坐标必须匹配当前 activation；
generation 必须为正且不得大于 GraphRun execution_sequence。
identity 与生成它的 exact lease token 的一致性只在
SettleGraphExecution transition 中由 reducer 验证。
```

## 5. 验收补充建议

至少增加或明确以下测试：

1. interrupt outcome generation 与当前 exact lease token 不一致时，整批 settlement 被拒绝；
2. interrupt outcome 的 run/superstep/node 坐标错误时，整批 settlement 被拒绝；
3. recovered Interrupted identity generation 为 0、负数或大于 execution sequence 时，snapshot 被拒绝；
4. recovered identity generation 小于当前 execution sequence 时，只要其他当前不变量合法，snapshot 可以通过；
5. 后续 attempt 增加 execution sequence 后，先前 sibling 的 current Interrupted identity 仍可合法保留；
6. stale interrupt ID 无法 resume 当前 Interrupted node；
7. 不存在为了验证历史来源而新增的 lease history、interrupt history 或 journal。

## 6. 其余架构结论

除上述责任边界外，需求已经满足核心架构目标：

- Mixed `Pending/Failed/Interrupted` Frontier 支持选择性恢复；
- Frontier 仍是唯一 routing/join/superstep barrier；
- current interrupt identity 只存在于当前 Interrupted settlement；
- failure 与 interrupt 使用唯一 `ResumeGraphNodes` command；
- skip 只作用于 Failed，并复用唯一 routing validator；
- batch lease 精确覆盖全部 Pending nodes；
- revision、exact token fence、resource admission、nested 与唯一 execution engine继续复用；
- activation-scoped input override 明确覆盖基线中的窄 input-binding 非目标；
- codec 由唯一 versioned encoder/decoder binding 负责；
- operator GraphRun pause 与旧顶层 interrupt authoritative path明确删除；
- 没有引入 store、journal、private runner、node lease、compatibility alias、双写或 fallback。

## 7. 通过条件

满足以下条件后需求可以正式通过：

1. 将 exact lease 来源校验明确归 `SettleGraphExecution` reducer；
2. 将稳定 snapshot validator 收敛为当前 snapshot 可证明的坐标、generation 范围和唯一性校验；
3. 明确 `generation <= execution_sequence` 不等于重新证明历史 lease 来源；
4. Resume reducer 只对当前 Interrupted settlement 的唯一投影 ID 做精确校验；
5. 补齐 transition-time 与 recovered snapshot 两组确定性测试；
6. 明确禁止为了历史证明新增 lease history、interrupt history 或 journal；
7. 后续实施方案直接替换旧模型，不叠加 alias、双写或临时迁移结构。

## 8. 最终意见

当前状态：**核心方案通过，仍有 1 个责任边界阻塞项。**

该问题不要求修改 settlement 模型、identity 结构或恢复协议，只需把“精确历史来源证明”放回它唯一可被证明的
`SettleGraphExecution` transition，并让稳定 snapshot validator 只验证当前 snapshot 可证明的事实。完成后，本需求可以正式评审通过。

本次为需求与现有架构的静态复核，未运行代码检查或测试。
