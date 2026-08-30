# S12 `GSP-A06` 实施代码验收评审

> **结论：`CHANGES REQUESTED / NOT READY`。当前七文件实现的行为、类型、无持久化边界和限定门禁均通过；但 owner-internal 查询被错误加入模块导出面，materialization query 的实际调用点与已批准结构账本不一致，且 S12 唯一实施方案尚未回写实际交付状态。修复/回写完成前，不能称为零已知负债交付。**

## 1. 评审信息与范围

- 评审日期：2026-08-24
- 批准依据：requirements 已将 S12 `GSP-A06` 标记为 `SATISFIED / APPROVED`，仅限实施方案 SHA256
  `1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7` 对应的 exact target。
- 本轮审核对象：该批准 target 的实际 production、behavior test 与 Node I/O normative diff。
- State/持久化边界：`GraphRunState`、command/reducer、revision、commit、protocol、Store 和 persistence 不在 S12 范围内；本轮未发现触及。
- Graph/Kernel failover：按用户已批准范围仅作边界核对；未发现 Graph-owned retry/backoff/error policy、第二 runner、registry、Port 实现或持久化。
- complexity gate、baseline、ratchet 与 legacy/private-source-shape gate 按用户授权不参与本轮准入；current behavior、strict typing、active owner/dependency 与质量检查仍要求通过。

实际 S12 manifest 与批准计划一致：

```text
src/mote_kernel/execution/graph/topology.py
src/mote_kernel/execution/engine/resume_input.py
src/mote_kernel/execution/executor.py
src/mote_kernel/execution/engine/recovery.py
src/mote_kernel/execution/invocation.py
tests/execution/engine/test_recovery_identity.py
docs/graph-node-input-output-contract-implementation.zh-CN.md
```

七文件合计 `311 insertions, 78 deletions`；没有新增测试文件或持久化文件。

## 2. Findings

### R1（高）：owner-internal 查询被加入 `__all__`，扩大了导出面

涉及位置：

- [src/mote_kernel/execution/graph/topology.py:96](../src/mote_kernel/execution/graph/topology.py:96)
- [src/mote_kernel/execution/engine/resume_input.py:258](../src/mote_kernel/execution/engine/resume_input.py:258)

批准 target 明确要求 `_compiled_graph_at_scope()` 不加入 package export；resume-input query 和 coordinate constructor 也属于
execution owner-internal helper。当前实现却把它们写入：

```python
__all__ = ["_compiled_graph_at_scope"]
__all__ = ["_require_node_materialization", "_resume_input_coordinate"]
```

这不是必要的内部导入机制：`from module import name` 不需要 `__all__`。它改变了原先空 `__all__` 的星号导出行为，当前可观察为：

```text
from mote_kernel.execution.graph.topology import *
→ _compiled_graph_at_scope
from mote_kernel.execution.engine.resume_input import *
→ _require_node_materialization, _resume_input_coordinate
```

因此违反“唯一 public `Graph` facade、owner-internal pure typed query、不新增 public entry point”和批准方案第 4.1 节的“不加入
package export”。这会把一个本应内部的实现细节变成模块导出契约，属于范围扩大与已知结构负债。

**要求：**删除两处新增 `__all__` 导出并恢复原有空导出列表；保留直接的 owner-internal import，不新增兼容 alias 或其他 export。

### R2（中）：materialization query 的实际调用点为 8 处，批准账本声明为 7 处

批准实施方案第 8.3 节把 source inventory 固定为“1 个 definition + 6 个 production consumers”，但当前实际结果为：

```text
definition                                   resume_input.py:47
_admit_override                              resume_input.py:105
node_inputs_available                        resume_input.py:158
pending_node_input_available                resume_input.py:190
materialize_node_input                      resume_input.py:224
GraphExecutor.resume / failed               executor.py:138
GraphExecutor.resume / interrupted          executor.py:163
preflight_recovery                           recovery.py:1146
```

也就是 8 个调用/定义位置。语义上仍是 6 个 consumer（executor 的 `resume()` 只有一个 consumer），没有产生第二个 query 或第二份
compiled truth；但当前 exact ledger 的可核验命令与实际 source inventory 不相符，不能在“零已知负债”状态下静默保留。

**要求：**优先在 `GraphExecutor.resume()` 的 non-skip 分支共享一次 plan lookup，使 source inventory 与批准账本的 7 处目标一致；若
确有理由保留两个调用点，必须先更新唯一 target owner 的 structural ledger 并重新取得该 exact target 的批准，不能把一次性 source
review 的矛盾留给交付记录。

本项不是新增 legacy/private-source-shape 门禁，而是对当前批准的 active owner/zero-debt 账本进行一次性实现核对。

### R3（中，交付闭环）：S12 唯一实施方案仍写着“未批准/未授权”，没有 implementation-owner writeback

当前 requirements 已记录 S12 已批准并且代码已出现，但唯一 target owner [S12 实施方案:5](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md:5)、
`:59`、`:995-1005` 仍写着：

```text
PENDING FOURTH INDEPENDENT TECHNICAL REVIEW / GSP-A06 NOT APPROVED
S12 PRODUCTION / TEST IMPLEMENTATION: NOT AUTHORIZED
```

这使 requirements、实际七文件 diff 与唯一实施方案互相矛盾，也遗漏了 actual manifest、结构账本和本轮门禁结果。代码本身不应因此
被重新扩大范围，但文档唯一真相尚未闭合。

**要求：**在 R1/R2 关闭并重跑适用检查后，实施 owner 只能回写上述 S12 实施方案，记录 actual manifest、actual structural ledger、
source inventory、测试/typing/quality 结果和 `IMPLEMENTED / VERIFIED` 状态；不得借 writeback 修改 State、Store、requirements
批准范围或新增门禁。本文保持为独立 review audit，不代替 owner writeback。

## 3. 已通过的实现与行为证据

### 3.1 结构与唯一事实

- `AdmittedResumeFact` 已从六字段变为五字段，`AdmittedResumeFact[GraphValueT]` 与 `_RecoveryFamily[GraphValueT]` 的 phantom generic
  已清除；真实承载 `GraphValueT` 的 transfer/seed/frame/availability 泛型仍保留。
- `RecoveryAvailabilityCoordinates.resume_inputs` 成为 resume-input presence 的唯一 equality/hash 事实；新 invariant 位于
  `preflight_recovery()` 的 frame projection 之后、family/proof 之前，skip 正确绕过 current-input requirement。
- `_compiled_at()` 已删除并由 topology-owned `_compiled_graph_at_scope()` 复用；recovery 没有 direct
  `transition.materializations` 读取。
- `_resume_input_coordinate()` 是 production 中唯一的 `ResumeInputAvailabilityCoordinate(...)` constructor；continuation validator 与
  routing 继续保留各自既有 direct read 和错误/解释 owner。
- seven-file diff 未触及 State、Store、protocol、reducer、commit、persistence 或 failover implementation。

### 3.2 Behavior / malformed evidence

`tests/execution/engine/test_recovery_identity.py` 新增的两个 target case 均可执行并通过，覆盖 exact equality、missing/wrong
descriptor、unknown scope、unknown materialization、duplicate-publication precedence 和 valid skip + historical coordinate。
既有 recovery、nested、continuation、resume、routing、frame 与 public Graph behavior 也全部通过。

## 4. 实际门禁结果

以下结果是在当前工作树、未运行复杂度 gate 的前提下取得：

```text
python -B -m pytest tests/execution/engine/test_recovery_identity.py -q --tb=short -p no:cacheprovider
→ 20 passed

python -B -m pytest tests/execution -q --tb=short -p no:cacheprovider
→ 563 passed

python -B -m pytest tests/architecture -k 'not complexity' -q --tb=short -p no:cacheprovider
→ 56 passed, 7 deselected

pyright
→ 0 errors, 0 warnings, 0 informations

ruff check（七文件中的 Python 文件）
→ passed

ruff format --check（七文件中的 Python 文件）
→ 6 files already formatted

python -B -m build --no-isolation
→ built sdist and wheel

python -B -m twine check dist/*
→ both artifacts PASSED

SKIP=kernel-complexity pre-commit run --files <seven-file manifest>
→ passed

git diff --check
→ passed
```

完整 monorepo `pre-commit run --all-files --show-diff-on-failure` 未能作为 clean 全量证据：其 end-of-file hook 尝试写入与 S12 无关的
只读工作树文件并因 `OSError: [Errno 30] Read-only file system` 失败；S12 七文件的 scoped pre-commit 已独立通过。该环境限制不应被
冒记为全量 hook 通过，也不改变上述 scoped 结果。

## 5. 最终裁决与复审条件

```text
S12 production behavior:       PASS within approved seven-file target
S12 current-contract gates:    PASS (complexity/legacy excluded by user scope)
S12 R1 export boundary:        OPEN / CHANGES REQUESTED
S12 R2 structural ledger:       OPEN / CHANGES REQUESTED
S12 R3 owner writeback:         PENDING
S12 overall delivery:           NOT READY
STATE / STORE / PERSISTENCE:   HARD KEEP / untouched
GRAPH/KERNEL FAILOVER:         USER-APPROVED BOUNDARY / no S12 implementation
```

关闭 R1 后至少重跑 scoped pre-commit、Ruff、Pyright、`tests/execution` 和相关 architecture checks；若 R2 通过 consolidation 修改了
production，重跑全部上述行为证据。随后只更新 S12 实施方案的 owner writeback，才可将本轮记录升级为 `IMPLEMENTED / VERIFIED`。

本验收记录是独立 review change unit，实际新增文件只有：

```text
docs/graph-semantics-preserving-simplification-s12-implementation-acceptance.zh-CN.md
```
