# Frontier Node Resume 代码审核回复复审报告

## 1. 复审信息

- 复审对象：`docs/frontier-node-resume-implementation-code-review-response.zh-CN.md`
- 对照基线：`docs/frontier-node-resume-implementation.zh-CN.md`
- 复审日期：2026-08-14
- 验证环境：用户已激活的 `metagpt` Conda 环境，Python 3.11.15
- 最终状态：复审发现已处置，完整门禁重新通过

## 2. 最终结论

原审核指出的三个 correctness 缺陷、一个 `ExecutionSnapshot` 镜像债务和 fence/abort surface 口径均已关闭。复审期间发现的
`state/domain_state` package contract 不一致也已按用户明确决定处置：保留删除空 package marker，并同步撤销 architecture test 中“该具体 Python
package 已经实现”的断言。

最终工作树重新满足：

- Frontier node resume correctness 闭合；
- GraphRunState 唯一事实源和唯一 execution engine；
- package architecture contract 与实际生产树一致；
- 504 项测试全部通过；
- strict typing、100% branch coverage、构建、包检查和 monorepo hooks 全绿。

因此，本复审最终判定为：**通过，可以提交。**

## 3. 复审阻断项的处置

### 3.1 发现时的真实问题

复审时，工作树已经删除：

```text
src/mote_kernel/state/domain_state/__init__.py
```

但 `tests/architecture/test_package_structure.py` 仍把 `state/domain_state` 列为确认存在的生产包，导致当时的 `make check` 出现 503 passed、1 failed。
该报告在发现时成立，不能用更早的 504 passed 结果覆盖后来发生变化的工作树。

### 3.2 用户决定与最终实现

用户确认该文件是有意删除，不要求恢复。全仓检查证明该 package 只有一行 docstring marker，没有：

- DomainState model、command 或 reducer；
- production import、export 或运行时消费者；
- durable protocol 或 conformance contract；
- package-specific test 或构建逻辑。

因此最终处理为：

1. 保留 `src/mote_kernel/state/domain_state/__init__.py` 删除；
2. 从 `REQUIRED_PACKAGES` 中移除 `state/domain_state`，使该清单只声明实际落地的 Python packages；
3. 不增加反向黑名单，不阻止未来在真实需求下实现 DomainState package；
4. 保留 GraphState 与 DomainState 的概念 owner 边界：GraphState 记录可恢复执行位置，未来 DomainState 记录已建立业务事实，二者独立演进并原子提交；
5. 不在本次 Frontier change 中虚构 DomainState API、AgentState transaction、store 或 composition entry point。

这关闭的是“空目录是否代表已实现 package”的 contract 不一致，不是否定 DomainState 的长期架构职责。

### 3.3 环境命令口径

复审还证明，在已经激活的 metagpt shell 中再次嵌套 `conda run -n metagpt` 会使 Pyright 的第三方包发现出现环境差异，并产生 407 个 `pytest` 未解析
派生错误。该现象不是生产代码缺陷，但使原先记录的嵌套命令不够可复现。

最终验证改为在已激活环境中直接执行。运行前确认：

```text
python   = /home/longert/anaconda3/envs/metagpt/bin/python
pyright  = /home/longert/anaconda3/envs/metagpt/bin/pyright
Python   = 3.11.15
```

没有修改 Pyright 配置、降低 strict rule 或用忽略项掩盖错误。

## 4. 原审核项独立复判

| 审核项 | 最终证据 | 判定 |
| --- | --- | --- |
| 非 executable Frontier 可提交 resource admission | transition 要求 `EXECUTABLE`；stable validator 要求至少一个 Pending 且 participants 为 Pending 子集 | 已关闭 |
| Nested child durable identity 可分叉 | stable validator、start projection、child projections 与 claim 后重建共用 `child_graph_run_id()`；executor 区分当前 composition root/family child | 已关闭 |
| ABORTED 跳过 compiled node membership | membership guard 位于 terminal return 前，RUNNING-only decode/routing/join 仍在 return 后 | 已关闭 |
| `ExecutionSnapshot` 无消费者镜像 | DTO、projection、export 和镜像测试已删除；execution 直接读取 `GraphRunState` | 已关闭 |
| Fence/abort 缺少 executor wrapper | direct state-owned command construction 已定义为正式 surface | 非缺陷，口径已关闭 |
| DomainState package marker 与 package contract 不一致 | 保留用户删除并同步已实现 package 清单 | 已关闭 |

未发现新的 Frontier node resume correctness blocker，也未发现 store、journal、history、第二 runner、compatibility alias、fallback、第二 identity/codec 或
跨语言 protocol 扩张。

## 5. 测试数量

使用同一 metagpt 环境收集：

```text
本地 origin/main 1f8a426ce1e9bb2cff298951919592a82edb96e5：461 tests
当前工作树：504 tests
净增：43 collected items
```

该差值是 collected items 净变化，不等同于 43 个新增测试函数。新增和重写的有效覆盖集中在 Frontier settlement、selective resume、interrupt、nested
projection、deterministic identity、resource admission、routing/join、exception/fence、codec 和 architecture owner boundaries。

## 6. 最终验证记录

在已经激活的 metagpt 环境直接执行：

```bash
cd /home/longert/motev2/mote-kernel
make check
```

结果：

```text
Ruff check                         PASS
Ruff format                       PASS (109 files)
Pyright strict                    PASS (0 errors, 0 warnings)
Pytest                            PASS (504 passed)
Coverage                          PASS (1943 statements, 634 branches, 100%)
sdist/wheel build                 PASS
Twine check                       PASS (2 artifacts)
```

并从 monorepo root 执行：

```bash
pre-commit run --all-files --show-diff-on-failure
git diff --check
```

最终提交前以这两个命令的实际结果为准；若 hooks 自动改写文件，则重新运行相应门禁后再提交。

## 7. 最终复审意见

准确的最终表述为：

> 原代码审核指出的 resource admission、nested durable identity、ABORTED membership 和 `ExecutionSnapshot` 镜像债务已经按 owner 边界关闭；
> fence/abort API 口径已经明确。用户有意删除的空 DomainState package marker 已与 package architecture contract 同步，且不改变未来 DomainState 的
> 概念 owner 职责。当前实现保持单一 GraphRunState truth、唯一 execution engine，并通过 504 项测试和完整项目门禁。

复审结论：**通过。**
