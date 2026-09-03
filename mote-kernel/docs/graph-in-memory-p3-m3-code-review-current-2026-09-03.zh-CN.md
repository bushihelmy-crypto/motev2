# Graph 内存语义 P3-M3 当前代码评审（2026-09-03）

状态：**通过（达到 commit 条件）**

评审基线：`50613a8`（P3-M1 partial Join proof gaps 收口）。

评审对象：当前未提交的 P3-M3 Join occurrence identity / cyclic Join 实现，包括 compiler/topology、唯一
`GraphRunState`、command/reducer/validation、routing/snapshot admission、family driver，以及对应测试、示例和复杂度门禁。

本文是独立评审台账。P3-M1/P3-M2 已完成评审；仅含测试的 P2-M 不再复审。工作树中的 Cloudflare 路径迁移、旧 P1
评审文档和实施计划均不修改、不归因。

## 1. 判定边界

1. 只审公开 `Graph` 可达、进程内的真实生产调用链，不把 durable/distributed、手工伪造 hostile 内部对象或几乎不可达的
   理论组合升级为 finding；
2. occurrence identity 必须由 compiler 唯一证明并进入同一个 `GraphRunState` 原子边界；partial、完成消费、admission 和
   routing 必须消费同一个 typed key；
3. 无法唯一归属的 cyclic Join 必须 compile fail closed；不能引入 loop counter owner、Join runner、隐藏缓存、第二
   reducer 或 runtime 猜测；
4. 每个 occurrence 只允许每个 source 到达一次，只产生一个 target activation；跨 occurrence 混入、重复、过期及多候选在
   现有领域异常边界失败；
5. 先判断设计和代码是否简单、清晰且唯一，再用门禁证明没有回归；没有真实问题就停止。

## 2. 审查台账

| 项目 | 当前状态 | 证据 |
| --- | --- | --- |
| compiler 唯一证明 occurrence 归属并保守放行 cyclic Join | 已核对通过 | `CompiledJoin` 由 compiler 一次生成 source→target offset；repeatable Join 只接受全部 source 都可重复且 activation cohort 完全相同的形状，不同 cohort、混合 repeatability 和无唯一 absolute coordinate 均 compile fail closed |
| occurrence identity 与 arrival 由同一 State owner 持有 | 已核对通过 | `GraphJoinOccurrenceIdentity(join, run_id, target_superstep)` 和完整 `ActivationReference` 直接进入既有 `GraphRunState.join_progress` / `RoutedActivationCause`；旧 definition-only key 已删除，无 parallel loop counter 或 mirror State |
| routing、partial、完成消费和 target activation 使用同一 key | 已核对通过 | `_pending_join_arrivals()`、`_resolve_control()`、reducer delta/consumption、state validation、frontier/snapshot admission 都按同一 occurrence；非 END target 由 cause 隐式消费，END 由 typed command 显式消费，且 commit 前复用同一 control resolution 校验完成态 |
| 普通非循环 Join、feedback、recovery/family 行为无回归 | 已核对通过 | P3-M3 相关 compiler/topology/routing/State/recovery/family 定向集 384 passed；公开 lost-acknowledgement state recovery 探针完成；完整测试 1370 passed、覆盖率 100%，无 transient/durable 补偿路径 |
| 无第二 runner/state/cache/compatibility path | 已核对通过 | production diff 只扩展 compiler plan、同一 State/command/reducer、唯一 routing resolver 和 commit admission；未新增 runner、scheduler、cache、public alias 或 persistence 路径 |

## 3. Findings

当前未发现真实 finding。审查未扩展到 P3-M4 nested/resource 组合、P3-M5 压力收口或 durable/distributed 场景。

## 4. 测试与门禁

当前定向实测：

```text
384 passed in 1.46s
```

覆盖 compiler、compiled topology、routing、State identity/model/transition/validation、recovery identity、family commit boundary
和公开 cyclic Join 执行。公开恢复探针在 Join target advance acknowledgement 丢失后，以捕获的 authoritative candidate 走
`Graph.run(state=...)`，最终得到 Completed、两个 Join occurrence 各执行一次，证明 occurrence cause 可通过现有 state-led
recovery 调用链。

完整门禁实测：

```text
make check
  Ruff lint / format (234 files): passed
  Pyright: 0 errors, 0 warnings
  complexity / semantic index: 22 passed
  zero-debt health: PASS
  pytest: 1370 passed in 249.70s
  coverage: 100.00% (7172 statements, 2508 branches)
  build: sdist + wheel succeeded
  twine check: PASSED

monorepo scoped pre-commit（P3-M3 production / tests）: all passed
git diff --check: passed
```

## 5. 最终结论

当前实现达到 commit 条件。未发现需要阻止提交的设计、实现或门禁问题；结论仅覆盖本文列出的 P3-M3 范围，不替工作树中
无关的 Cloudflare 路径迁移、旧 P1 评审文档或实施计划背书。
