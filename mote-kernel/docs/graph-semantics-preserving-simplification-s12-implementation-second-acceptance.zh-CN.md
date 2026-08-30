# S12 `GSP-A06` 二次实施代码验收评审

> **结论：`CHANGES REQUESTED / NOT READY FOR ZERO-DEBT HANDOFF`。当前七文件代码实现与行为证据已通过；`__all__` 按用户本轮补充的“包外唯一 owner 可接受”规则核验通过，materialization 账本也已闭合。唯一阻断项是 S12 唯一实施方案仍保留实施前的状态、数据流和 baseline 文字，尚未完成 implementation-owner writeback；在该唯一事实源回写前，不能称为 `IMPLEMENTED / VERIFIED`。**

## 1. 范围与批准边界

- 评审日期：2026-08-24
- 评审对象：requirements 已批准的 S12 exact design SHA256
  `1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7`
- 本轮只核对批准的七文件 implementation unit；review 文件本身是独立 audit unit，不加入 production manifest。
- State、command/reducer、revision、commit、protocol、Store、persistence、第二 runner 和 failover policy 均不在范围内；Graph/Kernel failover 仅按用户已批准边界核对。
- complexity gate、baseline、ratchet 与 legacy/private-source-shape gate 按用户授权不作为准入条件；current behavior、strict typing、active owner/dependency 与质量检查仍要求通过。

批准 manifest 与实际 S12 diff 一致：

```text
src/mote_kernel/execution/graph/topology.py
src/mote_kernel/execution/engine/resume_input.py
src/mote_kernel/execution/executor.py
src/mote_kernel/execution/engine/recovery.py
src/mote_kernel/execution/invocation.py
tests/execution/engine/test_recovery_identity.py
docs/graph-node-input-output-contract-implementation.zh-CN.md
```

七文件实际统计：`341 insertions, 114 deletions`；未新增测试文件，未修改持久化相关 owner。

## 2. Findings

### F1（阻断）：唯一实施方案没有回写实际 implementation 状态

[S12 实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md) 当前仍是设计阶段内容：

- 第 5 行仍写 `PENDING FOURTH INDEPENDENT TECHNICAL REVIEW / GSP-A06 NOT APPROVED`；
- 第 59 行仍写“未获 GSP-A06、不得修改代码或测试”；
- 第 100--148 行仍把已删除的 `resume_input_availability`、旧 `_resume_facts()` scan 和旧 direct-read 账本描述为“当前 production”；
- 第 995--1005 行仍写 `FOURTH ... PENDING`、`PRODUCTION / TEST IMPLEMENTATION: NOT AUTHORIZED`；
- 当前 SHA256 仍为设计批准 SHA，未记录 actual manifest、actual structural ledger、source inventory、测试/typing/quality 结果或 `IMPLEMENTED / VERIFIED`。

这与 requirements 已批准且工作树已有七文件实现互相矛盾。代码本身不需要扩大范围；需要由 implementation owner 只回写该方案，更新实际数据流、调用账本、manifest、门禁结果和最终状态，并保留历史 review/response 原文。若 requirements owner 的生命周期文字仍需把“尚未实施”改为“已验证”，应另行执行 requirements-only 文档单元；本评审不代改 requirements。

### F2（非阻断记录）：`__all__` 导出按用户新规则通过，但 owner 文档应明确例外

当前可观察到：

```text
topology.__all__       = ("_compiled_graph_at_scope",)
resume_input.__all__   = ("_require_node_materialization", "_resume_input_coordinate")
mote_kernel.execution.__all__ = ("Graph",)
```

三个 helper 各只有一个模块 owner，且没有进入 `mote_kernel.execution` 的 public facade；因此按用户“只要对包外唯一即可”的补充规则通过。本轮不要求删除这些 `__all__`。为避免设计文字与实现再次分叉，owner writeback 应记录该 export 例外及唯一 owner 证据。

## 3. 已通过的代码核对

一次性 source inventory：

```text
`_require_node_materialization(`：7 处
  1 definition + 6 production consumers
transition.materializations：3 个 direct read
  resume_input shared query / invocation continuation validator / routing owner
recovery.py direct transition.materializations：0
`ResumeInputAvailabilityCoordinate(`：production 仅 1 个 constructor（resume_input.py）
def _compiled_at：0
resume_input_availability、AdmittedResumeFact[GraphValueT]、_RecoveryFamily[GraphValueT]：production 均无
```

语义核对通过：

- `AdmittedResumeFact` 已为五字段 non-generic；resume presence 只由 `RecoveryAvailabilityCoordinates.resume_inputs` 拥有；
- `preflight_recovery()` 在 availability projection 后、family/proof 前，对每个 non-skip action 做 exact scope/materialization/coordinate membership，skip 正确绕过 current-input invariant；
- executor non-skip 分支共享一次 plan lookup，skip 不查找 materialization；recovery 不直接解释 compiled map；
- `_compiled_graph_at_scope()` 取代 invocation-local `_compiled_at()`，没有第二 traversal/cache/runner；
- 七文件 diff 未触及 State、Store、protocol、commit、revision、persistence 或 Graph-owned retry/backoff/failover。

## 4. 验证证据

当前工作树结果：

```text
tests/execution/engine/test_recovery_identity.py  → 20 passed
tests/execution                                  → 563 passed
tests（排除 complexity）                         → 826 passed, 7 deselected
tests/architecture（排除 complexity）            → 56 passed, 7 deselected
pyright                                           → 0 errors, 0 warnings, 0 informations
ruff check（七文件 Python）                       → passed
ruff format --check（六个 Python 文件）          → passed
python -B -m build --no-isolation                  → sdist/wheel built
python -B -m twine check dist/*                    → both PASSED
SKIP=kernel-complexity pre-commit（七文件）        → passed
git diff --check（七文件）                        → passed
```

完整 `git diff --check` 仍会命中工作树中与 S12 无关的两个 Cloudflare `LICENSE` EOF 空行；完整 monorepo pre-commit 也曾因同一类只读无关文件的 EOF fixer 报 `OSError: [Errno 30] Read-only file system`。这些是环境/既有 dirty workspace 限制，不冒记为全量通过，也不影响上面的 S12 scoped 证据。

## 5. 最终裁决

```text
S12 production behavior:           PASS
S12 current-contract gates:        PASS (complexity/legacy excluded by user scope)
S12 materialization ledger:        CLOSED (7 occurrences / 6 consumers)
S12 export uniqueness:              PASS under explicit user rule
STATE / STORE / PERSISTENCE:        HARD KEEP / untouched
GRAPH/KERNEL FAILOVER:              approved boundary / no S12 policy implementation
S12 implementation-owner writeback: OPEN / BLOCKING
S12 overall delivery:               CHANGES REQUESTED / NOT READY
```

关闭条件只有：implementation owner 回写 S12 唯一实施方案的实际状态、source inventory、actual manifest、结构账本和本轮验证结果；不修改生产范围、不引入持久化、不改 failover policy。回写后可据此重新验收并升级为 `IMPLEMENTED / VERIFIED`。

本文件是独立 review audit unit，唯一新增路径为：

```text
docs/graph-semantics-preserving-simplification-s12-implementation-second-acceptance.zh-CN.md
```
