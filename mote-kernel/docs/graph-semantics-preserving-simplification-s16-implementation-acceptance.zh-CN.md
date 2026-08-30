# S16 Continuation frame segment 规范序校验简化代码验收

> **最终裁决：`PASS / IMPLEMENTED / VERIFIED`。** 本记录只验收 requirements owner 已明确批准的 S16 reviewed exact target；不扩大 `GSP-A06` 批准范围，不批准任何 State、Store、持久化、自动错误恢复或第二执行路径变更。

## 1. 验收对象与授权边界

- 验收日期：2026-08-24
- 单元：Graph 语义保持型简化 S16（P2）
- 设计 owner：[S16 Continuation frame segment 规范序校验简化实施方案](graph-semantics-preserving-simplification-s16-implementation.zh-CN.md)
- approved design SHA256：`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402`
- 第三次独立技术评审：[S16 implementation third review](graph-semantics-preserving-simplification-s16-implementation-third-review.zh-CN.md)，裁决 `PASS / READY FOR REQUIREMENTS OWNER APPROVAL`
- requirements 授权：2026-08-24，`GSP-A06 SATISFIED / APPROVED`，仅限上述 reviewed SHA 与两文件 implementation manifest
- 声明源码基线 Git：`f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497`
- 实际工作树基准 commit：`f328c4303dd74a09b95aece7efbcc35bb180e270`；本次验收针对其上未提交的 S16 两文件实现差异，未创建或冒记 production commit

S16 actual implementation manifest 与批准 manifest 完全一致：

```text
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/test_continuation_integrity.py
```

以下文件在 S16 中保持 untouched：

```text
mote-kernel/src/mote_kernel/execution/run_context.py
mote-kernel/src/mote_kernel/state/**
mote-kernel/tests/state/**
State command/reducer/commit/protocol/Store/persistence artifacts
```

工作树中的其他用户修改（包括文档、示例和 complexity 文件）不纳入本验收 manifest。

## 2. 实际差异与 exact target 核对

相对声明基线，实际差异为：

- `invocation.py`：`10 insertions / 11 deletions`；仅在 `_validate_frame_index()` 增加一个 `itertools.pairwise` 标准库 import，并以四个 direct adjacent-order guards 替换四个 coordinate projection、一个异构 dispatch、四个 `set(...)` 与四个 `tuple(sorted(...))` 构造；
- `test_continuation_integrity.py`：新增 7 个 public `Graph.run()` continuation behavior cases 与共享 mutation-free 断言 helper，共 `246` 行新增；
- `run_context.py`：零差异。

一次性 source review（`S16.a`–`S16.j`）结果全部通过：

- `_validate_frame_index()` signature、唯一调用入口、lineage preflight、四个 shape guards、四个 canonical guards 和四个 content loops 的 owner/顺序保持；
- canonical guard 顺序仍为 graph input → publication → resume input → child boundary，比较谓词为 `previous.coordinate >= current.coordinate`；
- 15 个 module-level function definitions 不增长；旧 13 个 coordinate construction/dispatch sites 全部归零；新增面只有一个标准库 import 与四个 direct scans；
- 不存在 helper、DTO、alias、protocol、cache、index、第二 admission path、类型擦除、public export 或 recovery runner；
- State、Store、protocol、persistence、complexity artifact 与 legacy/private-source-shape gate 均不在 actual manifest。

`invocation.py`、`run_context.py`、behavior baseline 的实际 SHA256 分别为：

```text
5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4
bf196695bce1687f0bd9554d3a8615e9af5cdbfa1bedbc859cd199e8ff54f648
c161c0c64184badc0c7b7d4fd6c129a7f70f263c40b0a43e20f7355484a6a72b
```

## 3. 行为、错误与 tamper 验收

新增的 7 个 case 均通过公开 `Graph.run()` 边界，覆盖四个 canonicality raise branch、shape-before-canonicality、canonicality-before-content 以及 complete/recovered 两条路径。每个直接 shape/canonicality `Graph.SnapshotMismatchError` 均断言：

- 完整错误 literal 与设计表逐字一致；
- `raised.value.__cause__ is None`；
- `completed.state` 保持不变；
- continuation 内部 snapshot identity 保持不变，未发生 admission 后生命周期推进。

一次性 baseline-vs-target inner-scalar probe（不提交为永久测试）覆盖 descriptor identity、scope/run identity、activation superstep、node identity 和 enum/int 字段；baseline 与实际 target 的 exception type、完整 text、cause 均一致：

| inner scalar tamper | baseline = target | cause |
| --- | --- | --- |
| descriptor identity | `SnapshotMismatchError: continuation graph input descriptor does not match its scope` | `None` |
| scope/run identity | `SnapshotMismatchError: continuation graph input belongs to an unknown scoped run` | `None` |
| activation superstep | `SnapshotMismatchError: continuation publication has inconsistent coordinates` | `None` |
| node identity | `SnapshotMismatchError: continuation publication has inconsistent coordinates` | `None` |
| enum/int field | `SnapshotMismatchError: continuation graph input descriptor does not match its scope` | `None` |

forged unhashable/mixed inner fields仍属于 reviewed nominal-domain contract之外；本单元没有新增 catch、normalizer、fallback 或错误恢复行为。

## 4. Verification gates

在上述实际工作树实现上运行：

```text
tests/execution/test_continuation_integrity.py          41 passed
9 active architecture/source/owner nodeids              9 passed
tests/execution                                         570 passed
tests/state/graph_state                                  206 passed
all tests excluding tests/architecture/test_complexity_gate.py 833 passed
coverage                                                100.00% (4734 statements / 1470 branches)
pyright                                                 0 errors, 0 warnings, 0 informations
ruff check (two-file manifest)                           passed
ruff format --check (two-file manifest)                  2 files already formatted
python -B -m build --no-isolation                        succeeded
python -B -m twine check dist/*                          both artifacts PASSED
SKIP=kernel-complexity pre-commit (two-file manifest)    passed
git diff --check / git diff --cached --check             passed
```

`make check` 未运行：其 `check` target 无条件包含用户明确排除的 `complexity-ratchet`，不能将包含该门禁的命令冒记为 S16 通过证据。automated complexity/health/baseline/ratchet/limit/hook 与 legacy/private-source-shape gate 均按用户范围 `USER-EXCLUDED`，没有新增、修改或依赖这些 gate；其余 current behavior、strict typing、active owner/dependency、coverage、lint、format、build/package、no-persistence 与 scoped repository checks 均为 `REQUIRED / PASS`。

## 5. 最终交付状态

```text
S16 GSP-A06: SATISFIED / APPROVED (requirements owner; reviewed SHA only)
S16 IMPLEMENTATION: IMPLEMENTED / VERIFIED
S16 PRODUCTION + TEST MANIFEST: invocation.py + existing continuation-integrity test file
RUN_CONTEXT / STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
NEW ERROR-RECOVERY / RETRY / FALLBACK / CHECKPOINT / FAILOVER: NONE
COMPLEXITY / LEGACY-PRIVATE-SHAPE GATES: USER-EXCLUDED
```

本验收记录是独立 docs-only audit unit，实际新增文件只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-acceptance.zh-CN.md
```

它不替代 owner writeback，不把当前工作树其他修改累计进 S16 manifest，也不代表已创建 production commit。
