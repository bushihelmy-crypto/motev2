# Frontier Node Resume / Recovery 实现方案评审

## 1. 评审对象

实现方案：

- `docs/frontier-node-resume-implementation.zh-CN.md`

对照需求：

- `docs/frontier-node-recovery-requirements.zh-CN.md`
- `docs/frontier-node-resume-requirements.zh-CN.md`

评审重点：

- 是否同时满足两份需求文档；
- GraphState 是否保持唯一事实源；
- 是否复用现有 execution、resource、routing、join、nested 与 fencing 基础设施；
- 是否避免兼容路径、重复 owner 和额外持久化负担；
- 完成门禁是否合理。

## 2. 总体结论

**有条件通过。**

核心状态机和架构方向已经与最新需求对齐，满足“零负债、唯一真相、复用基础设施”的主要目标，也没有把本期 node resume/recovery 延伸成通用持久化工程。

但当前实现方案还不能视为最终收敛版本，存在两个必须修正项：

1. 删除所有针对历史 legacy 符号的永久测试和专项门禁；
2. 修正 resource participant 与全部 Pending nodes 精确相等的错误约束。

此外，`MissingChild` 与 `ActiveChild` 的 prepare 结果必须明确为严格类型，不能让一个 DTO 依靠可选字段表达两种不同动作。

完成上述修正后，方案可以进入实施。

## 3. 必须修正项

### 3.1 删除 legacy 专项门禁

实现方案仍计划增加以下门禁：

- 在 `tests/architecture/` 增加 forbidden-symbol assertions；
- Phase 5 运行 forbidden-symbol architecture assertions；
- 维护一份 `Forbidden production symbols` 历史符号黑名单；
- 将 forbidden-symbol `rg` 和 architecture assertions 列为完成门禁。

这些内容不应进入本次实现。

本次协调替换仍然必须直接删除旧文件、旧 commands、旧 exports、旧 tests、兼容 alias、fallback 和重复路径，但不应新增永久测试或门禁来记录已经删除的历史名称。否则测试体系会反向持有 legacy 知识，形成新的维护负担。

正确处理方式：

- 直接删除旧实现及其引用，不保留兼容层；
- 通过正常代码评审、变更 diff、类型检查、现有测试和质量检查确认删除完整；
- 不增加 legacy 名称黑名单测试、forbidden-symbol architecture assertion 或永久 `rg` gate；
- 可以保留只描述当前稳定架构事实的测试，例如：state 不依赖 execution、routing 只有一个 owner、只有一个 execution engine、没有第二 codec 或第二 reducer path。

这不改变“最终代码不得残留 legacy 路径”的要求，只是不让测试和门禁永久保存历史符号知识。

### 3.2 Resource participant 约束与现有基础设施冲突

实现方案的 Claim 规则要求：

> resource admission 若存在，其 participants 精确对应 Pending nodes 且均已 admitted。

该约束过强，与现有 resource admission 模型不一致。

现有基础设施中：

- 只有声明了资源需求的可执行节点才建立 resource acquisition；
- 无资源的普通节点不会建立 acquisition；
- nested Pending node 也不是 resource acquisition participant；
- 但无资源节点和 nested node 仍属于完整 Pending batch，并最终进入同一个 batch lease。

因此需要明确区分两个集合：

```text
lease.node_ids
    == 当前 Frontier 的全部 Pending node IDs

resource acquisition participants
    == 当前需要资源的可执行 Pending node IDs
    ⊆ lease.node_ids
```

建议将规则修正为：

1. batch lease 的 `node_ids` 精确覆盖全部 Pending nodes；
2. resource participant 必须属于当前 Pending set；
3. execution/resource guard 根据 compiled node definition，证明 acquisition participants 精确对应当前需要资源的可执行 Pending nodes；
4. claim 前所有存在的 resource acquisitions 必须 admitted，不得残留 waiting acquisition；
5. 无资源 Pending 和 nested Pending 不需要伪造 resource acquisition；
6. state reducer 只验证 state-owned membership、snapshot 结构、admitted 状态及 lease/resource 生命周期组合，不解释 compiled resource requirements。

若保持原文，state owner 将被迫理解 compiled resource topology，既破坏 owner 边界，也无法原样复用当前 admission 基础设施。

## 4. 需要收紧的类型边界

### 4.1 `MissingChild` 与 `ActiveChild` 不应由模糊 payload 区分

Recovery 需求中，`WaitingForChildren` 的语义是等待已经存在的 `ActiveChild`。实现方案则让它同时携带：

- `MissingChild` 对应的待提交 child start commands；
- `ActiveChild` 对应的当前 child projections。

这两种结果的调用方动作不同：

```text
MissingChild -> 提交确定性的 StartGraphRun command
ActiveChild  -> 不提交 start，等待已有 child 继续运行或恢复
```

因此不能使用两个 optional 字段、空 tuple 或其他隐式组合让调用方猜测状态。实现时应选择一种严格形态：

- 拆成 `ChildrenToStart | WaitingForChildren` 两个 prepare dispositions；或
- 让 `WaitingForChildren.action` 成为 `StartMissingChildren | WaitForActiveChildren` 的严格 union，并验证两个分支互斥且 payload 非空。

如果调整 disposition 名称，应同步更新需求与实现文档，确保两者使用相同语义。该修正只是关闭 DTO 歧义，不扩大产品范围，也不要求新增 store lookup。

## 5. 已通过的核心设计

### 5.1 唯一事实源

- `GraphFrontierState` 保存完整 node settlement，是可恢复执行位置的唯一事实源；
- execution snapshot 只是 GraphState 的只读投影，不是第二份可恢复状态；
- reducer 只依赖 state-owned value objects，不依赖 compiled graph；
- durable state 先提交，执行层只能从 committed snapshot 重建；
- 没有引入 mutable cache、attempt journal 或隐藏 history 来补证明链。

### 5.2 Claim、settlement 与 resume

- 保留单一 batch lease，不引入 node-level lease或 partial claim；
- lease 保存 authoritative `node_ids`，task ID 仅为 execution-local deterministic projection；
- claim 精确覆盖当前全部 Pending nodes；
- settlement outcomes 精确覆盖 lease node IDs；
- `ResumeGraphNodes` 是 Failed/Interrupted 恢复的唯一 command；
- `SettleGraphExecution` 是一次 attempt node outcome 提交的唯一 command；
- selective resume 不覆盖已经 Succeeded/Skipped 的 sibling；
- 最后一个 settlement 或 skip 与 advance/complete resolution 在同一 revision 原子提交。

### 5.3 Interrupt identity 与 fencing

- interrupt identity 由 state owner 根据 run、superstep、node 和 active generation 派生；
- reducer 能独立证明 interrupt outcome 与当前 Pending activation、exact lease 的对应关系；
- 当前 interrupt identity 只存在于 `InterruptedGraphNode`，消费后不保留 history；
- stable snapshot validator 只验证当前 snapshot 可证明的事实，不反推历史 lease；
- stale resume ID、stale revision 和 stale fence 不得影响新 generation。

### 5.4 异常阶段划分

方案已正确区分 claim 前和 claim 后异常：

- encode、guard、prepare、claim 提交前 decode/input validation 失败：没有 committed lease，不谈 fence；
- claim 后 guard、decode、contract、routing、scheduler 或 infrastructure error：不生成 settlement，保留 exact lease，外部停止完成后使用 exact token fence；
- typed node failure/interrupt 才能形成 node settlement；
- ordinary exception 不伪装为可 resume 的 typed failure。

### 5.5 Routing、join、nested 与 loop

- routing contribution 只由唯一 compiled-topology validator 解释；
- retained Succeeded/Skipped contribution 与本次 success contribution 统一参与最终 resolution；
- Frontier 未完全 settled 时不应用 routing/join，也不推进 superstep；
- child identity 改为 parent activation coordinates 派生，不再以 task ID 作为 authoritative parent identity；
- child Failed/Interrupted 保持原 child run，由 child 自己 resume；
- loop/new superstep 创建全新 Pending activation，不继承旧 override、settlement 或 interrupt identity。

### 5.6 输入绑定和持久化边界

- resume input codec 只有一个 definition/compiled binding；
- GraphState 只保存 codec identity/version 和 node-scoped encoded override；
- 每个 Pending node 独立物化 effective input，不广播 override；
- typed settlement 消费对应 Pending binding，异常未 settlement 时 binding 保留；
- 未引入通用 durable input/output、state store、journal、event log、history、retry policy或跨进程恢复承诺。

## 6. 基础设施复用结论

方案总体复用了现有基础设施：

- revision CAS；
- batch execution lease；
- exact token fence；
- resource ordered acquisition 与 admission reducer；
- routing contribution validation；
- join resolution；
- nested graph execution；
- 唯一 graph execution engine。

需要注意，复用 resource infrastructure 的前提正是修正第 3.2 节的 participant invariant。不能为了让 resource participant 等于全部 Pending nodes，而人为给无资源或 nested 节点创建虚假 acquisitions。

## 7. 术语收敛建议

文件清单中的“node lease”容易被理解为 node-level lease，但方案实际设计仍是：

```text
one batch lease containing canonical node_ids
```

建议统一写为“包含 `node_ids` 的 batch lease”，避免与“不实施 node lease、partial claim、multi-worker split”的明确边界冲突。

## 8. 完成门禁评审

以下完成门禁合理，应保留：

- state、execution、resource、nested 和稳定 owner-boundary tests；
- Python 3.11+ strict typing；
- Ruff、format、import 与现有 package architecture checks；
- deterministic transition/recovery/public behavior coverage；
- 项目现有 coverage 要求；
- `make check`；
- monorepo root `pre-commit run --all-files`；
- package build 与 metadata validation；
- conformance impact 检查及必要的同步更新；
- `git diff --check` 和变更范围检查。

以下完成门禁应删除：

- legacy forbidden-symbol architecture assertions；
- legacy symbol blacklist；
- 以历史符号名称为内容的永久 `rg` gate；
- 任何为了证明“曾经存在的接口已经不存在”而新增的永久测试。

## 9. 最终判定

| 评审维度 | 判定 | 说明 |
| --- | --- | --- |
| Recovery 需求对齐 | 有条件通过 | 核心状态机对齐；nested prepare DTO 需收紧 |
| Resume 需求对齐 | 通过 | selective resume、lease、settlement、fence 边界成立 |
| 唯一事实源 | 通过 | GraphState authoritative，execution snapshot 仅投影 |
| 基础设施复用 | 有条件通过 | resource participant invariant 必须修正 |
| 零兼容路径 | 通过 | 直接替换和删除方向正确，不保留 alias/fallback |
| 零额外持久化负担 | 通过 | 未引入 store、journal、history 或通用 input/output persistence |
| 完成门禁 | 不通过 | 必须删除 legacy 专项测试和门禁 |

最终结论：**修正 resource participant 规则、严格化 nested prepare DTO，并删除 legacy 专项门禁后通过。**

## 10. 评审说明

本次为需求与实现方案的静态架构评审，未修改实现代码，也未运行测试。
