# S19 / S21 / S22 Graph 执行尾项实施方案独立技术评审

> **结论：`CHANGES REQUESTED / NOT READY FOR GSP-A06 APPROVAL`。** S19 的重复 admission 提取方向基本成立，S21 的 `KEEP / NO PRODUCTION CHANGE` 裁决方向成立；但 S22 的 exact target 与 strict Pyright、private owner 边界直接冲突，当前无法在既定 manifest 和零负债约束下实施。本文不批准 `GSP-A06`，不授权修改 production、tests、requirements、normative source、State、Store、protocol、持久化或错误恢复能力。

## 1. 评审信息

- 评审日期：2026-08-24
- 评审对象：[S19 / S21 / S22 Graph 执行尾项语义保持型简化实施方案](graph-semantics-preserving-simplification-s19-s21-s22-implementation.zh-CN.md)
- 评审对象 SHA256：`d8161e0b2e3e22349411091b9a5c28927998d356ad01bb74886d075fd6c95bf0`
- 声明源码基线：Git `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad`
- 本文性质：独立 technical review record；只拥有本轮裁决、问题和验证证据，不拥有 S19/S21/S22 target shape、requirements 批准状态、production shape 或测试 shape
- 本轮边界：保持唯一事实源、复用现有 execution/frame/commit 基础设施、零新增结构与所有权负债；不实现持久化，不新增 retry、fallback、checkpoint、failover 或第二 recovery runner；automated complexity 与 legacy/private-source-shape gate 按用户指令排除
- 本轮 actual change unit：只新增本文，保留工作树中的其他用户修改，不将其纳入本评审 manifest

## 2. 总体裁决

| 维度 | 裁决 | 复核结论 |
| --- | --- | --- |
| State、command、reducer、Store、protocol、持久化 | **通过 / HARD KEEP** | 方案明确禁止相关 diff；未发现新增持久化端口、backend、第二 State/frame store 或 durable protocol 变化 |
| 错误恢复范围 | **有条件通过** | 没有新增 recovery 能力；但文档需明确 S22 只是既有 recovery confirmation path 的语义保持型重构，不是新增恢复机制 |
| 唯一事实与基础设施复用 | **S19/S21 通过设计；S22 待整改** | S19 复用既有 codec/materialization/frame owner，S21 保留 Graph 唯一 lifecycle owner；S22 的跨模块 private plan 类型边界尚未闭合 |
| S19 exact target | **方向通过，证据待补** | 两个 override 分支的 encode → decode → admitted-frame mechanics 确实重复，caller-local validation 可继续保留 |
| S21 exact audit | **方向通过，准入证据不足** | 当前只有一个 `Graph.run()` lifecycle owner；但 P2 `GSP-A06` 的 no-op 关闭口径尚未定义 |
| S22 exact target | **阻断** | facade 直接使用 invocation private plan types 会触发 strict Pyright `reportPrivateUsage`，与“invocation.py 零 diff、不得 suppression/alias”互相矛盾 |
| `GSP-A06` | **未满足、未批准** | 缺少 per-unit exact-shape/tamper applicability、净复杂度/结构证据闭合和完整可复现验证矩阵 |

## 3. 阻断项

### R1 — S22 private nominal plan import 无法通过 strict Pyright

实施方案 §6.2 要求在 `facade.py` module scope 增加：

```python
async def _confirm_recovery_plan(
    context: GraphRunContext[GraphValueT],
    planned: _PlannedFence | _PlannedResume[GraphValueT],
    commit: GraphCommit[GraphValueT] | None,
    *,
    confirmed_prefix: bool,
) -> None: ...
```

但 `_PlannedFence`、`_PlannedResume` 当前定义在 [invocation.py](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/invocation.py:94)，名称是 private，且该模块的 `__all__` 为空（见 [invocation.py](/home/longert/motev2/mote-kernel/src/mote_kernel/execution/invocation.py:622)）。

我用与文档相同的 union、`isinstance` 分支和 helper body，在同一 `mote_kernel.execution` package 下运行 strict Pyright，得到：

```text
"_PlannedFence" is private and used outside of the module in which it is declared (reportPrivateUsage)
"_PlannedResume" is private and used outside of the module in which it is declared (reportPrivateUsage)
```

这不是测试或 legacy/private-shape gate，而是仓库当前 strict typing contract。它同时与以下文档硬边界冲突：

- §6.2：facade 直接 import invocation 的 private nominal types；
- §6.4：若需要 private-use suppression，S22 必须停止；
- §6.5：`invocation.py` 必须零 diff；
- §3.1：不得新增 alias、protocol、DTO、callback、`Any` 或 `object`。

因此当前 S22 exact target 没有可通过的实现路径。必须先重新裁决 owner/type surface，例如建立一个明确的窄 typed internal export 并同步扩大 manifest，或重新安置 confirmation owner；不得用 `# pyright: ignore`、类型擦除或字符串 discriminator 绕过。

### R2 — `GSP-A06` exact-shape/tamper 与净复杂度证据未闭合

Requirements 的 `GSP-A06` 要求每个 P2 单项提交目标签名、nominal 输入/输出、删除对象、最多新增对象、净复杂度证据、成功/失败或边界 characterization、exact-shape/tamper 证据和 changed-file manifest（见 [requirements §6](/home/longert/motev2/mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md:102)）。

当前实施方案没有 exact-shape/tamper 章节，也没有为 S19、S21、S22 分别声明“不适用”的理由或替代证据；文档中没有 `exact-shape` 或 `tamper` 证据登记。S21 §5.3 还提出评审后可直接把 `GSP-A06` 标为 `SATISFIED / CLOSED`，但没有给出 P2 所要求的 case-level 准入证明。

用户明确排除 legacy/private-source-shape 门禁和 automated complexity gate；这意味着不能为了补证据新增旧 private path、AST、行数或复杂度 ratchet 测试，但不等于 requirements 的证据义务自动豁免。整改应：

1. 为每个单元增加 applicability matrix，明确 exact-shape/tamper 是 `N/A` 还是由 public behavior、strict typing、owner/dependency evidence 替代；
2. 由 requirements owner 记录该范围例外或更新 `GSP-A06` 口径，不在 review record 中自行追认；
3. 扩充人工结构账本，使其包含新增 union dispatch、跨模块 type surface、helper/调用点和真实净认知面，而不只统计删除的重复代码。

### R3 — S19 的 `GSP-P02` 映射过窄

实施方案 §7 将 S19 的 `GSP-P02` 标为“不触及”。但 S19 正在重排 `ResumeGraphNodes` command 中的 `OverrideGraphNodeInput`、resume codec binding 和 payload admission；requirements 的最低映射规则要求，只要触及 State command、revision 或 durable control projection，就必须映射 `GSP-P02`（见 [requirements](/home/longert/motev2/mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md:58)）。

这不表示 S19 要修改 State；应把 `GSP-P02` 改为适用，并补充“command shape、codec identity、revision 和 reducer projection 逐字保持”的 negative evidence，或由 owner 给出可审计的“不触及 command semantics”裁决。

## 4. 验证计划问题

### R4 — 格式化工具与仓库依赖不一致

实施方案 §9.1 推荐 `python -B -m black --check`，但项目 dev 依赖只声明 Ruff（[pyproject.toml](/home/longert/motev2/mote-kernel/pyproject.toml:26)），仓库 Makefile 也只执行 `ruff format --check`（[Makefile](/home/longert/motev2/mote-kernel/Makefile:10)）。

应改用：

```bash
python -B -m ruff check ...
python -B -m ruff format --check ...
```

否则干净的 `.[dev]` 环境无法复现文档门禁，且可能把 Black 与 Ruff 的格式差异误报为 production 问题。

### R5 — behavior matrix、命令和 coverage 口径不一致

S19 §4.5 要求复跑 encoder/decoder、decoded frame shape、frontier 和错误优先级证据，但 §9.1 推荐命令只列 `test_graph_api.py`、public typing 和 typing fixture，没有列出 `tests/execution/engine/test_resume_input_contract.py` 等直接覆盖 codec/frame admission 的路径。

同时，文档要求 coverage 不下降，却没有记录 baseline 数值，也没有在推荐命令中运行 coverage。应固定每个 required `path::test_case`、coverage baseline/target 和 full-suite/scoped-suite 的计算口径；不要用一个未带 coverage 的局部 pytest 命令代替。

`python -B -m build` 也应与仓库的 `build --no-isolation` / `twine check` 口径统一，避免把网络或隔离构建环境差异混入评审结果。

### R6 — 聚合 `make check` 与用户排除项需拆开记账

当前工作树的 [Makefile](/home/longert/motev2/mote-kernel/Makefile:17) 将 `complexity-ratchet` 纳入 `make check`。在用户明确排除 complexity gate 的前提下，不能把聚合命令写成“通过”，也不能因跳过该子目标而声称所有其他检查已运行。

实施文档应固定三种状态：

```text
AUTOMATED COMPLEXITY / BASELINE / RATCHET: USER-EXCLUDED / NOT RUN
LEGACY / PRIVATE-SOURCE-SHAPE GATE: USER-EXCLUDED / NOT RUN
CURRENT BEHAVIOR / TYPING / OWNER / LINT / FORMAT / COVERAGE / PACKAGE: REQUIRED
```

## 5. 范围与表述整改

### R7 — “不实现错误恢复”与 S22 recovery refactor 的语义需明确

若用户意图是“不新增 retry、fallback、checkpoint、failover 或第二 runner”，S22 可以作为既有 recovery confirmation mechanics 的语义保持型重构继续评审；若意图是“不触碰任何 recovery code”，S22 则超出范围，必须移除。

同理，用户排除的是新增/维护 complexity 与 legacy gate，不是所有当前质量检查。文档应避免同时写“门禁不属于本轮”和“每单元必须通过全部聚合门禁”，改为明确的 scoped required checks。

### R8 — S19 exact order 和 lookup 账本需要更精确

S19 的 caller-order 表没有列出 `frontier_node()` 和 `StableActivation` 派生，且 `decode_resume_input()` 内部仍会再次执行 materialization lookup。当前行为可以保持，但“唯一 materialization fact / 完整 mechanics”表述没有说明这一次重复 lookup 是有意保留还是遗漏。

整改时应把完整首错顺序和 lookup 次数写入账本；不要为消除它新增第二 coordinate owner、cache、context bag 或改变 `engine.resume_input` owner。

## 6. 已确认通过的设计部分

- State、command、reducer、Store、protocol、persistence 文件均被列为 HARD KEEP；未发现新增持久化或跨进程恢复承诺。
- S19 的 failed override 与 interrupt override 在 action-local validation 之后共享相同 encode → decode → frame/coordinate admission mechanics，保留各自 settlement、interrupt-ID、skip 和 frontier validation owner 的方向成立。
- S21 当前只有一个 `Graph.run()` lifecycle owner；没有发现应当新增 `_run_new()`、`_run_state()`、第二 runner 或 context bag 的证据。
- S22 保留 fence/resume 的 command 差异、`replace_state` exception boundary、partial-prefix handoff 和 State → frames 安装顺序的证明方向成立；阻断点是 type/owner surface，不是该时序证明本身。

## 7. 只读验证证据

本轮未实施 target，仅复核当前 baseline：

```text
all tests excluding tests/architecture/test_complexity_gate.py
→ 833 passed, coverage 100.00%

tests/execution/test_graph_api.py + tests/execution/test_graph_public_typing.py
→ 75 passed

tests/architecture/test_graph_typing_fixtures.py
→ 16 passed

Pyright (executor.py / facade.py / graph API test)
→ 0 errors, 0 warnings, 0 informations

Ruff check / Ruff format --check / git diff --check
→ passed
```

`make check` 和 monorepo-root pre-commit 未在本轮运行：`make check` 当前包含用户明确排除的 complexity-ratchet，且本轮是 docs-only review；未将未运行项冒记为通过。

## 8. 当前状态与后续授权

```text
S19: DIRECTION SOUND / GSP-A06 NOT APPROVED
S21: KEEP DIRECTION SOUND / GSP-A06 EVIDENCE NOT CLOSED
S22: BLOCKED BY STRICT TYPING + OWNER BOUNDARY
GSP-A06: NOT APPROVED
PRODUCTION / TEST / STATE / STORE / PROTOCOL / PERSISTENCE: UNTOUCHED
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED
```

合法下一步是先由 owner 解决 R1、补齐 R2–R6 的文档证据并重新计算目标 SHA，再进行一次独立技术复审；在新的 exact target 获得 requirements owner 的显式 `GSP-A06` 批准前，不得修改 production 或 tests。

## 9. 本次 review change unit

本文是本轮唯一新增文件：

```text
docs/graph-semantics-preserving-simplification-s19-s21-s22-implementation-review.zh-CN.md
```

本文不修改实施方案、requirements、normative source、production、tests、State、Store、protocol、持久化或任何 complexity/legacy gate artifact。
