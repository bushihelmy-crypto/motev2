# Think 代码评审（2026-09-05）

> 状态更新（2026-09-05）：本文前半部分记录的是编码前 Prompt 增量快照；在后续扁平模块
> 订正和 v1 实现完成后，最终验收以文末第 12 节为准。

## 1. 评审结论

**原始快照不能按完整 Think v1 通过；只能认定为 Prompt 阶段的增量实现。**

实施计划已经明确写出 Prompt 已完成，而 Context、Compact、Inference、Command 和完整
`ThinkNode` 仍待后续阶段。因此，下文把“尚未实现的阶段”作为完整交付门禁记录，不把它们
误报成 Prompt 小阶段的局部回归。

当前结论是：Prompt 阶段的顺序调用、异常/取消传播和 `HookRequest` 组装基本符合边界；
完整 Think 图尚不能运行、不能作为 nested Graph 接入，也不能宣称已完成。

评审范围只包含 `src/mote_kernel/think/` 和 `tests/think/`。Invocation 的实现不属于
Think：Think 只保留正式 typed boundary，不能在本包复制 adapter、stub 或 shim。Failover
的 decorator、重试、receipt、identity 和策略也不在本评审范围；这里只确认 Port 的注入边界。

## 2. P0：完整 Think 图尚不存在

### 证据

- [`think/__init__.py`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/__init__.py:3)
  的 `__all__` 为空；
- [`think/node.py`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/node.py:3) 只有
  总装配的占位文档，没有 `ThinkNode`、共享 `HookNode` 或 route；
- `context/`、`compact/`、`inference/`、`command/` 只有空包边界；
- `contract.py` 目前只有 `ThinkRequest`、`PromptFrame`、`PromptStep`、`ThinkFrame` 和
  `PromptPort`，缺少后续阶段所需的 request/result、step、`ModelBinding` 和 `ThinkRoute`。

实际导入验证：

```text
from mote_kernel.think import ThinkNode
ImportError: cannot import name 'ThinkNode'
```

因此当前没有以下能力：

- 五个业务节点、一个共享 Hook 和一个 route 的七节点拓扑；
- predecessor-bound `hook_request` value 链；
- 五次共享 Hook 激活和最终 `result` output；
- 父图只看到 Think nested node 的边界。

这不是 Prompt 阶段的意外回归，但在完整 v1 合并前必须关闭。

## 3. P1：`ThinkStep` 没有真正封闭

[`ThinkStep`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/contract.py:59) 的文档
声明它是 closed set，但 [`ThinkFrame`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/contract.py:86)
只使用 `isinstance(self.step, ThinkStep)`。

任意外部子类都能被接受：

```python
class FakeStep(ThinkStep):
    __slots__ = ()

ThinkFrame(FakeStep(), state)  # 当前会成功
```

后续 route 若用同样的宽检查，未知 step 可能被默认推进。进入 route 实现前必须建立明确的
已知 nominal variants admission，并让未知 subclass 失败；不能靠字符串 tag 或默认分支。

## 4. P1/P2：Graph value 的内层不变量尚未落地

[`ThinkRequest`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/contract.py:31)
没有字段 admission，`PromptFrame` 目前只拒绝 `None`
（[`contract.py`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/contract.py:50)）。
实际验证表明 list、dict、function 都可以进入 `PromptFrame`，例如：

```python
PromptFrame([], "placeholder", "user")
PromptFrame({}, "placeholder", "user")
PromptFrame(lambda: None, "placeholder", "user")
```

这会让可变容器或运行时 capability 进入后续 Graph publication、Hook value 或持久化边界。
解决方式不是在 Think 内复制 Invocation 的通用 validator，而是为具体 DTO/Port owner 明确
并执行 concrete admission；泛型注解、`frozen=True` 和 `cast()` 都不能证明递归 data-only。

在此门禁关闭前，至少需要覆盖：

- `ThinkRequest` 的 payload/state owner admission；
- Prompt 三项结果的具体类型和不可变性责任；
- 错误 subclass、mutable built-in、callable 和运行时句柄的负例。

其中字段递归/运行时句柄的具体 admission 不由本节点补做；上列只作为对应 DTO、Port 或
Invocation owner 的验收责任记录，详见第 11 节。

## 5. P2：PromptPort 装配检查和结果失败边界偏弱

[`PromptNode.__post_init__`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/prompt.py:31)
只检查成员存在且 `callable`。`runtime_checkable Protocol` 不能证明：

- 方法是否为 async/返回 awaitable；
- 参数数量是否正确；
- 返回值是否为对应的 concrete result。

因此同步方法或错误 arity 目前会在 activation 的 `await` 处才失败。若这是有意的结构性
边界，应由静态检查或显式 typed async adapter 固定，并补充确定性负例；不能把
成员存在/可调用检查当作完整签名验证。

此外，三个方法全部 await 完成后才构造 `PromptFrame`
（[`prompt.py`](/home/longert/motev2/mote-kernel/src/mote_kernel/think/prompt.py:60)）。
如果首个结果已经违反当前 `None` 约束，后两个方法仍会被调用；具体 result admission
应明确是否要求在首个非法结果处 fail-fast。该语义由外部 result admission owner 决定，
不在 PromptNode 内增加第二套校验路径。

## 6. P2：测试目前会固定“未实现”状态

[`tests/think/test_prompt.py`](/home/longert/motev2/mote-kernel/tests/think/test_prompt.py:64)
断言顶层和四个阶段子包的 `__all__` 为空。这对当前增量阶段可以接受，但完整实现后必须
替换为：

- 顶层只导出 `ThinkNode`；
- 五个阶段子包分别导出自己的节点；
- 七节点拓扑、共享 Hook、route、nested boundary 和最终 output 测试。

否则测试会在 Think 图始终不存在时继续通过。

## 7. 当前已通过的部分

Prompt 现有实现满足以下行为：

- 同一个 `PromptPort` 按 `system → placeholder → user` 顺序各调用一次；
- 三次调用收到同一个 payload 对象；
- 中间方法异常或取消时不调用后续方法；
- 输入外层类型不是精确 `ThinkRequest` 时拒绝；
- 输出使用同一 `hook_state` 组装 `ThinkFrame` 和 `HookRequest`；
- 没有创建第二 runner、状态模型或私有 Graph 执行路径。

当前 Think 代码也没有 import/call `mote_kernel.invocation`，没有实现 Failover，符合两者
均由外部 owner 提供的边界。

## 8. 检查记录

已运行：

```text
python -m pytest --cov=mote_kernel.think -q tests/think  19 passed, 100% coverage
python -m ruff check src/mote_kernel/think tests/think  passed
python -m ruff format --check src/mote_kernel/think tests/think  passed
pyright src/mote_kernel/think                           0 errors
相关 Hook/nested/predecessor 回归                        113 passed
```

`tests/think` 与 package structure 合跑时，package structure 有 1 个失败，原因是当前工作树
另有用户删除了 `src/mote_kernel/loop/react/__init__.py` 和
`src/mote_kernel/operations/__init__.py`；该失败不是 Think 代码引入的。

本轮 `make check` 在全仓 Ruff 阶段被范围外的 Act 改动阻断：
`src/mote_kernel/act/{authorize,execute,resolve,route,settle}.py` 的导入排序/未使用
导入错误；未修改这些无关文件。

## 9. 继续编码前的门禁

1. 在阶段 C 前实现 `ThinkNode`、五个阶段节点、共享 Hook、route 和唯一 `result` output；
2. ~~收口 closed step admission，并为未知 step 增加负例~~（本轮已完成）；
3. 为 Think-owned DTO/Port owner 固定 concrete data-only admission，不使用宽泛 `cast` 绕过；
4. 固定 PromptPort 的 async/arity/result 失败边界和 fail-fast 测试；
5. 完整实现后移除“空公共面”测试，改为公共 API、拓扑、nested 和失败/取消验收；
6. 当前 Think 文件多数仍是 untracked，提交时必须把它们纳入同一变更集。

## 10. 最终判定

**Prompt 增量可以继续开发；当前不能作为完整 Think v1 合并或授权父图接入。**

Invocation 和 Failover 继续保持外置：Invocation 只提供正式 typed boundary，Failover 只在
外部 assembly 中装饰具体 Port，Think 不实现二者的内部机制。

## 12. v1 完成复核（2026-09-05）

后续编码已关闭原始 P0：`think/` 现在提供五个平铺职责模块、一个共享 `HookNode`、一个
route 和唯一 `ThinkNode` 顶层入口。七节点拓扑、predecessor-bound `hook_request`、
`route.hook_result` 终端 output、五次共享 Hook 激活及 nested Graph 接入均已有实现和
专项测试。

目录订正为：

```text
think/
├── __init__.py
├── node.py
├── contract.py
├── prompt.py
├── context.py
├── compact.py
├── inference.py
└── command.py
```

每个职责模块只导出自己的图节点，`mote_kernel.think.__all__` 只导出 `ThinkNode`；不再
保留五个阶段子包。新增实现没有引入第二 state/runner、Invocation shim、Failover、Hook
command delivery 或 Think 持久化。原始第 2–6 节中的“尚未实现/空子包”结论属于快照记录，
不再作为当前 v1 判定依据。

当前专项验收通过项：正常七节点链路、Port 调用顺序与次数、Hook value 链和终端 commands
原样传递、错误 outer type/未知 step、共享 Hook slot 负例、Port 异常传播以及父图 nested
边界。全仓检查若受范围外 Act/既有包删除改动阻断，仍按实际输出单独记录，不归因于 Think。

## 11. 本轮处理记录（2026-09-05）

### 11.1 与已确认要求的核对

| 评审项 | 与当前要求的关系 | 处理结论 |
| --- | --- | --- |
| P0：完整 Think 图尚不存在 | 这是编码前快照问题；后续已完成五个阶段、共享 Hook、route 和唯一 `ThinkNode` | 已由第 12 节的 v1 复核关闭，不再作为当前阻断 |
| P1：`ThinkStep` 可被任意子类接受 | 与 nominal closed-step 要求一致，属于当前可修复的契约缺口 | 已在 `contract.py` 增加显式 closed variant 集合；未知外部子类现在被拒绝 |
| P1/P2：Think 内递归 data-only/DTO 通用 admission | 若要求 Think 自己实现递归 validator，会违反已确认的 Invocation/DTO owner 分层 | 不在 Think 增加通用 validator、反射或 registry；具体 DTO owner/Invocation boundary 负责相应 admission，Think 只保留自身必要的 nominal 构造检查 |
| P2：PromptPort async/arity/result 不能由 runtime Protocol 完整证明 | 与当前结构性 Port 边界一致，不需要引入反射签名检查 | 保持静态类型 + 外部 typed adapter + activation 时的自然异常；不在 PromptNode 复制 Invocation 校验算法 |
| P2：首个非法 Prompt 结果是否立即 fail-fast | 属于 Port result admission 语义，不是 Prompt 图控制逻辑 | 由外部 Port/Invocation admission 决定；PromptNode 不增加第二套结果 validator 或隐藏调用路径 |
| P2：空阶段公共面测试 | 这是编码前快照问题；阶段已改为 `think/*.py` 平铺模块 | 已替换为五个平铺模块各自唯一节点导出测试；不保留阶段子包 |
| Failover 注入 | 必须由外部 assembly 对五个业务 Port（PromptPort 为一个三方法对象）先装饰，再注入 | Think 不引入 Failover 图或重试实现；现有契约和实施文档已明确该顺序 |

### 11.2 已完成的代码修正

`ThinkFrame` 不再使用宽泛的 `isinstance(step, ThinkStep)` 作为 closed-set 证明，而是只接受
`think.contract` 明确列出的五个 nominal variant：`PromptStep`、`ContextStep`、
`CompactStep`、`InferenceStep` 和 `CommandStep`。不允许消费方子类或运行时注册扩展；route
对未知 step 直接抛出契约错误。

同时保留一个 `PromptPort` 对象承载三个方法，PromptNode 的装配检查继续只验证必需成员和
可调用性；它不检查 Failover 内部，也不直接依赖 `mote_kernel.invocation`。

### 11.3 本轮判断

没有需要用户进一步讨论的架构决策。第 12 节已经记录完整 v1 的实现复核；本轮只继续补齐
专项负例和扁平模块文档，不改变已确认的 Graph、Hook、Invocation、Failover 或持久化边界。
按用户要求，本轮未进行 Git 检查，也不处理评审中关于变更集/提交的建议。
