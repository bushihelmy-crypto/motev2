# S19 / S21 / S22 Graph 执行尾项代码实现验收

> **最终裁决：`PASS`。S19 为 `IMPLEMENTED / VERIFIED`；S21、S22 为 `CLOSED / KEEP / VERIFIED NO DIFF`。** 本记录只验收 requirements owner 已批准的 reviewed exact target 与两个 KEEP disposition；不扩大 `GSP-A06` 范围，不批准任何 State、Store、持久化、错误恢复能力、第二执行路径或账本外变更。

## 1. 验收对象与授权边界

- 验收日期：2026-08-25
- 单元：S19、S21、S22（P2；三个独立 disposition unit）
- 设计 owner：[S19 / S21 / S22 Graph 执行尾项语义保持型简化实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- approved design SHA256：`07c739485e9d6f24a0dc17ca092f884eb2aeca7532220bc59a67f969b735a3f9`
- 第三次独立技术评审：[S19 / S21 / S22 第三次独立技术评审](graph-semantics-preserving-simplification-s19-s21-s22-implementation-third-review.zh-CN.md)，SHA256 `bcbd38f96e4ae051b6a49d87f50accabdd3646839b5ca140af0df2c9061a8b09`，裁决 `PASS / READY FOR REQUIREMENTS OWNER PER-UNIT DISPOSITION`
- requirements 授权：S19 为 `GSP-A06 SATISFIED / APPROVED`，只授权上述 reviewed SHA 的两文件 implementation unit；S21、S22 为 `GSP-A06 SATISFIED / CLOSED — KEEP`，production/test manifest 为空
- 声明源码基线与当前 `HEAD`：Git `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad`
- 当前 implementation-owner writeback SHA256：`5e8edcab2c112cbed0617c7df0d5a4777c2898b2de10ed5c48a0b5acdf539374`
- 当前 requirements owner SHA256：`239785997a1f0291e86bad8ae47e377f3ee2bd121921e79462bbbfc3209664bd`

S19 批准 manifest 与实际 production/test diff 完全一致：

```text
mote-kernel/src/mote_kernel/execution/executor.py
mote-kernel/tests/execution/test_executor.py
```

S21、S22 的实际 production/test manifest 均为空。工作树中的 Makefile、README、requirements、主实施方案、其他 review/acceptance 文档、示例和 complexity 文件属于既有用户修改，不纳入本次 S19 implementation manifest。

## 2. S19 生产实现质量验收

### 2.1 Exact target 与结构账本

`executor.py` 的实际差异为 `13 insertions / 5 deletions`，与 reviewed target 探针一致。新增且仅新增一个 private typed method：

```python
def _admit_override_resume_input(
    self,
    node_id: GraphNodeId,
    override: OverrideNodeInput[GraphValueT],
) -> tuple[OverrideGraphNodeInput, NodeInputFrame[GraphValueT]]:
    binding = encode_resume_input(self._graph, override.values)
    frame = decode_resume_input(self._graph, node_id, bytes(binding.payload))
    return binding, frame
```

实际结构复核结果：

- override `encode_resume_input()` source site：`2 → 1`；
- override `decode_resume_input()` / frame-admission source site：`2 → 1`；
- 两个 consumer 分别是 failed override 与 interrupt override，且都在原 action-local validation 之后调用同一 method；
- common `AdmittedResumeInput` coordinate/frame construction 保持 caller 唯一 owner；
- executor materialization lookup 与 decoder-owned defensive lookup 仍为每个 override action 两次，职责和顺序不变；
- settlement、interrupt ID、skip routing/substitution、command/replacement、simulated frontier validation 均仍由原 nominal branch/caller 拥有；
- 没有新增 DTO、alias、protocol、callback、cache、field、context bag、mutable store、public export、compatibility path 或第二 runner。

新增 `NodeInputFrame` 与 `OverrideGraphNodeInput` 均是既有 nominal owner type；imports 位于 module scope。实现不使用 `Any`、bare dictionary、reflection、string discriminator、cast、typing suppression 或异常捕获，不改变 exception identity/cause。

实际 production SHA256：

```text
src/mote_kernel/execution/executor.py
d967415f746e72043be73a6d31bbf74386aa26f7bfa9d885eb9bf54abac2131b
```

### 2.2 首错顺序与 State-command/frame 语义

逐行 diff 确认两个 call site 只将原有相邻 encode/decode pair 替换为 method call；以下顺序保持：

```text
frontier lookup
→ StableActivation
→ materialization lookup #1
→ failed settlement / exact interrupt-ID validation
→ encode
→ decode / materialization lookup #2 / frame admission
→ command + replacement
→ common admitted coordinate/frame construction
→ simulated frontier validation
```

因此：

- wrong settlement 与 stale interrupt ID 仍在 codec 前失败；
- encoder/decoder/frame admission 仍在 command、replacement 和 admitted-input accumulator 更新前完成；
- helper 不读取或修改 State、frames、settlement、request、accumulator 或 frontier；
- `OverrideGraphNodeInput` payload、`ResumeGraphNodes` action、codec identity/version、State revision 与 reducer projection 均保持；
- public `Graph` facade、commit/recovery、continuation/frame installation 和持久化边界均未触及。

## 3. S19 测试实现质量验收

`test_executor.py` 新增唯一 nodeid：

```text
tests/execution/test_executor.py::test_override_resume_admission_preserves_validation_and_codec_order
```

该 case 与 reviewed evidence matrix 一致，覆盖：

- wrong failed settlement 时 encoder/decoder event 为空；
- valid failed override 为恰好一次 `encode → decode`，产生 command、admitted input frame，并可由 reducer 得到 pending frontier；
- owner-produced decoded values 的 wrong-name tamper 产生 exact `GraphValueAdmissionError` text 且 `__cause__ is None`；
- owner-produced decoded values 的 wrong-exact-type tamper产生 exact `GraphValueAdmissionError` text 且 `__cause__ is None`；
- 两个 tamper 路径均保持一次 `encode → decode`，不返回 `PreparedResume`，输入 request 的 State/frame identity 不变；这些对象及其 record 均为 frozen nominal values，source diff 也不存在 mutation path；
- stale interrupt ID 在 codec 前失败；valid interrupt override 为恰好一次 `encode → decode`，并保留 admitted frame value。

`_TrackingCodec` 只存在于 test owner，用于记录外部 callable 时序和构造明确 tamper；wrong-type `cast` 也只用于伪造运行时 malformed fixture。production 没有新增 stringly dispatch、cast、suppression、test seam 或 private-source instrumentation。测试不冻结 helper 名、AST、源码行数、局部 accumulator 或 private call count，不形成 legacy/private-source-shape gate。

实际 behavior test SHA256：

```text
tests/execution/test_executor.py
8e4ac64c07d013dad4981d9a808d060d44e237d492a525fd76537301ef28a597
```

## 4. S21 / S22 KEEP 验收

S21 与 S22 的关闭不是空实现或延期负债，而是 reviewed audit 后的正向 KEEP disposition：

| 单元 | 验收结果 | 负向证据 |
| --- | --- | --- |
| S21 | `CLOSED / KEEP / VERIFIED NO DIFF` | `facade.py` 无 diff；未新增 `_run_new()` / `_run_state()`、DTO、dispatcher、context bag、callback、export 或第二 lifecycle runner |
| S22 | `CLOSED / KEEP / VERIFIED NO DIFF` | `facade.py` / `invocation.py` 无 diff；未 import/export `_PlannedFence` / `_PlannedResume`，未提取 confirmation helper，未改写 transaction loops 或 recovery tests |

关键 owner SHA 保持设计基线：

```text
src/mote_kernel/execution/facade.py
d1cf6e7fd33ca6ab70ad0ce4a82ba0ae8eae844ccd3baac162d8dbbb674ea5d9

src/mote_kernel/execution/invocation.py
5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4
```

`invocation.__all__` 仍为空，`mote_kernel.execution.__all__` 仍只有 `Graph`。State、Store、protocol、persistence、family driver、result、run context 与 resume-input owner 均为零 diff；没有新增错误恢复、retry、fallback、checkpoint、failover、补偿事务或第二执行/存储路径。

## 5. Verification gates

在当前实际工作树实现上运行：

```text
target behavior case                                      1 passed
six-file owner / behavior / public typing run             161 passed
all tests excluding tests/architecture/test_complexity_gate.py
                                                         834 passed
coverage                                                 100.00%
                                                         4,736 statements / 1,470 branches
                                                         0 missing / 0 partial
full strict Pyright                                      0 errors, 0 warnings, 0 informations
Ruff check src tests                                     passed
Ruff format --check src tests                            152 files already formatted
make package-check                                       passed
                                                         sdist + wheel built
                                                         twine check: both artifacts PASSED
SKIP=kernel-complexity pre-commit（S19 两文件 manifest）  passed
git diff --check / git diff --cached --check             passed
```

`make check` 未作为聚合命令运行，因为其 target 无条件包含用户明确排除的 `complexity-ratchet`。除该排除项外，`make check` 对应的 lint、typecheck、full coverage test 与 package-check components 已分别全部通过。automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均按用户范围 `USER-EXCLUDED / NOT RUN`；没有新增、修改或依赖这些 gate。

## 6. 最终交付状态

```text
S19 GSP-A06:
SATISFIED / APPROVED（requirements owner；仅限 reviewed SHA）

S19 IMPLEMENTATION:
PASS / IMPLEMENTED / VERIFIED

S19 PRODUCTION + TEST MANIFEST:
executor.py + existing test_executor.py

S21:
SATISFIED / CLOSED — KEEP / VERIFIED NO DIFF

S22:
SATISFIED / CLOSED — KEEP / VERIFIED NO DIFF

STATE / STORE / PROTOCOL / PERSISTENCE:
HARD KEEP / UNTOUCHED

NEW ERROR RECOVERY / RETRY / FALLBACK / CHECKPOINT / FAILOVER:
NONE

AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES:
USER-EXCLUDED / NOT RUN
```

本验收记录是独立 docs-only audit unit，实际新增文件只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-acceptance.zh-CN.md
```

它不替代 requirements 或 implementation-owner writeback，不覆盖历史 review/response，不把当前工作树其他修改累计进 S19 manifest，也不代表已创建 production commit。
