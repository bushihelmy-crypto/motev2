# Frontier 节点失败恢复需求评审回复

## 1. 回复信息

- 回复对象：`frontier-node-resume-requirements-review.zh-CN.md`
- 回复日期：2026-08-13
- 结论：接受评审的核心结论；已将认可意见合入需求文档，并对部分意见作限定或修正

## 2. 总体回复

认可“有条件通过”的结论。评审正确确认了以下核心方向：

- node settlement 属于特定 activation，不属于静态 `GraphDefinition`；
- frontier 继续作为同一 superstep 的完整结算边界；
- lease 保持 batch 粒度；
- node failure 从 GraphRun 终态中分离；
- revision、execution token、resource admission、interrupt receipt、join progress 和 stable task identity 可以原位复用；
- 本次变更必须替换旧 authoritative model，不得增加兼容路径或第二套 runner。

需求文档已经根据评审合入以下内容：

1. Input binding 必须先于任何可调用的 resume 路径。
2. GraphState reducer、execution guard 和 routing 的校验责任边界。
3. Lease 只持久化 claimed `node_ids`，task identity 由 activation 坐标派生。
4. ABORTED 保留 diagnostic frontier，并使用独立 termination record。
5. Nested child 使用明确的非终态 typed projection。
6. 明确删除旧 FAILED、FailGraphExecution、FailTransition、flat frontier 和旧 lease identity 路径。
7. 调整实施顺序，使 input consistency 在 resume 暴露前得到保护。

此外，评审遗漏了“部分成功节点的业务 output / DomainState facts 如何跨 attempt 持久化”的阻塞问题。该问题已作为新的 P1
补入需求文档。

## 3. 逐条回复

### 3.1 接受：动态 node 状态与静态 GraphDefinition 分离

接受，无修改意见。

`GraphDefinition + NodeId` 继续描述静态 topology；`(run_id, superstep, node_id)` 描述一次动态 activation。循环进入
相同 NodeId 会创建新 superstep，resume 则保持同一 activation，只更换 execution attempt。

### 3.2 接受：Frontier 是统一结算边界

接受，无修改意见。

成功 sibling 的 routing contribution 可以跨 attempt 保留，但只有原始 frontier 全部成功后才能统一应用。Frontier status
继续由 node settlement 推导，不额外持久化第二份状态。

### 3.3 接受：GraphRun lifecycle 与 node failure 分离

接受。

最终模型删除 `GraphRunStatus.FAILED`：

```text
RUNNING + frontier BLOCKED    node execution failure，可显式 resume
ABORTED                       GraphRun terminal termination，不可 resume
COMPLETED                     GraphRun successful terminal state
```

### 3.4 接受原则、修正具体方案：Input binding

接受其 P1 优先级，也接受“没有 binding 就不能暴露可调用 resume 路径”。

但不直接接受“advance 到新 superstep 时原子建立新的 binding”这一具体表述。当前 `advance` 发生时并未持有下一次
`StepRequest.node_input`，因此不能凭空建立可信的新 binding。调用者自行提交任意 binding ID 也不足以证明 payload 相同。

需求文档已改为先确认以下二选一或等价 owner 模型：

1. 一个 GraphRun 使用固定 input binding；或
2. 每个 frontier 在 admission/claim 前通过显式 durable `BindGraphFrontierInput` 建立 binding。

Binding 必须由可信 payload owner 产生或验证，可以是 durable payload identity、owner revision 或受信任 content digest；它不能是
调用者可随意声明的字符串。

在该决策落地前，允许提交内部状态模型重构，但必须使用不可绕过的 gate 禁止 resume command、resume claim 和 resume execution。

### 3.5 接受：Reducer 与 topology validation 的责任边界

完全接受，并已将评审中的 owner 表合入需求正文。

GraphState reducer 验证 state-owned concurrency 和 lifecycle invariants；execution guard 验证 compiled graph membership、task
identity 和 contribution 合法性；routing engine 负责 topology 计算。State 不得为了复算 topology 依赖 execution。

### 3.6 接受：Lease identity 唯一化

接受评审倾向的第一种方案：

```python
GraphExecutionLease(
    token=...,
    node_ids=(...),
)
```

`node_ids` 是 claimed activation subset 的唯一 durable truth。`task_id` 由 `(run_id, superstep, node_id)` 在 execution
projection 中派生。不会同时保存旧 `task_ids` 和新 `tasks`，也不会提供 fallback。

### 3.7 接受：ABORTED diagnostic model

接受并关闭该待决事项：

```text
GraphRunState
├── status = ABORTED
├── frontier = abort 时的完整 frontier，仅供诊断
└── termination = GraphAbortRecord
```

补充约束：

- termination 只属于 ABORTED；
- abort reason 不复用 node failure；
- execution/resources 必须清理；
- prior join progress 可以保留作诊断，但不得再次参与 routing；
- interrupt 必须按终止语义写入 cancellation receipt；
- ABORTED frontier 不得投影为可执行或可恢复位置。

### 3.8 接受语义、保留传递机制决策：Nested child projection

接受四种 typed child projection：

```text
MissingChild
ActiveChild
CompletedChild
AbortedChild
```

也接受“未提供 terminal result”不能同时表示 missing 和 active。

暂不接受将其直接定为新的 lookup port，因为 authoritative state store 的装配边界尚未实现。Projection 可以先由调用方随 typed
request 提供，也可以未来由 state-store lookup port 加载。需求现在锁定状态语义，不提前锁定尚无 owner 的传递机制。

### 3.9 接受：Legacy 清理

接受列出的删除项。本变更是模型替换，不保留双 authoritative path。

对“architecture tests 直接断言旧符号不存在”部分接受。应验证：

- public exports 不包含旧类型；
- reducer dispatch 不接受旧 command；
- lifecycle enum 不包含旧状态；
- production imports 不引用旧路径；
- 行为测试证明旧失败语义不存在。

不建议只用全仓字符串搜索作为主要 architecture contract，因为注释、迁移文档或 changelog 可以合法提到旧符号。

### 3.10 接受：基础设施复用与非目标

接受第 7 节。不会在本需求中新增 node-level lease、多 worker frontier splitting、Python 私有 durable store、第二套 journal、
默认 composition entry point 或无 consumer 的 conformance schema。

### 3.11 部分接受：每个中间提交不得同时存在新旧模型

接受“每个可合入提交必须只有一个 authoritative runtime path，并通过完整检查”。

不把“源码中绝不能短暂出现新旧类型”作为机械要求。大型类型迁移可能需要在本地工作期间暂时共存；真正禁止的是：

- 一个提交中存在两条可运行 authoritative path；
- compatibility alias 或 fallback 让新旧状态均可进入 reducer；
- 中间提交暴露缺少 input binding 的 resume；
- 合入分支的提交无法通过完整检查。

若无法保持提交边界内部一致，应合并成更大的原子提交，而不是发布过渡兼容层。

## 4. 评审遗漏：新增 P1

### 4.1 部分成功 output / DomainState 的 durable ownership

当前节点成功结果不仅包含 routing，还包含泛型 output，并可能产生 DomainState command、nested child output 或 effect receipt。

例如：

```text
attempt 1:
  a -> success(output-a)
  b -> failure
  c -> success(output-c)

attempt 2:
  b -> success(output-b)
```

Resume 不会重新执行 a、c，因此系统必须能在进程重载后恢复 `output-a`、`output-c` 或它们已经建立的业务事实。仅在
GraphState 中保存 routing contribution 只能恢复控制流，不能恢复完整 node semantics。

需求已增加以下约束：

- 成功 activation 使用 `(run_id, superstep, node_id)` 作为稳定关联身份；
- GraphState 保存 routing contribution；
- 业务 output、DomainState facts 或窄 durable result reference 由明确 owner 保存；
- GraphState、DomainState、result reference 与 journal records 作为一次 AgentState 变更原子提交；
- Resume 后必须能从 durable owner 加载成功 sibling 的必要结果；
- 不得依赖 executor 进程内存。

具体采用 `SucceededGraphNode.result_ref`，还是仅通过 activation identity 从 DomainState/result store 加载，仍需在实现前确认。

## 5. 更新后的阻塞项

实现前必须关闭三个 P1：

1. Input binding 的可信 owner、生命周期和校验协议。
2. GraphState reducer、execution guard 与 routing 的 validation boundary。
3. 部分成功 output / DomainState 的 durable ownership 与原子提交语义。

其中第 2 项的责任划分已经在需求层确认；第 1、3 项仍需完成具体 owner 设计。

实现前还需关闭或落实以下 P2：

1. Lease 只保存 claimed `node_ids`：已确认。
2. ABORTED diagnostic frontier + termination：已确认。
3. Nested child 四态 projection：状态语义已确认，传递机制待 store 边界确认。

## 6. 最终结论

评审的核心结论被接受，需求文档已经据此更新。没有拒绝其架构方向；未直接采纳的部分仅限于：

- 不能假设 advance 自动建立下一 frontier input binding；
- 不能在 store owner 尚未确定时强制 nested projection 必须由 lookup port 提供；
- 不使用纯字符串搜索代替有语义的 legacy architecture assertions；
- 不把开发过程中的源码短暂共存等同于提交中存在双 authoritative runtime path。

加入部分成功业务结果的 durable ownership P1 后，需求才同时覆盖控制流恢复、输入一致性和业务事实恢复三个必要维度。
