# S16 Continuation frame segment 规范序校验简化实施方案

## 1. 文档信息

- 状态：`IMPLEMENTED / VERIFIED / IMPLEMENTATION-OWNER WRITEBACK COMPLETE / GSP-A06 APPROVED — reviewed exact target SHA only`
- 日期：2026-08-25
- 单元：Graph 语义保持型简化 S16（P2）
- 源码基线：Git `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad`（S16 production commit）
- S16 目标文件历史基线：Git `7247a93485f30746638a5168e06be8766a64a120`；在 S15 implementation delta
  (`4b8f3727644687ffd63d6bdd7e0dc4159274b892`) 中只改动 `execution/engine/recovery.py`，下列三个 S16 目标/证据文件与该历史基线逐字一致
- S15 predecessor：production commit `4b8f3727644687ffd63d6bdd7e0dc4159274b892`、验收 commit
  `9a5ec8353721fab5c0fcba2a03581faadd92feeb` 及其后续 docs-only writeback `c0e8026`/`f9182fa` 均不在 S16 manifest；
  S16 只消费其已接受的 shared recovery owner
- production 基线：`src/mote_kernel/execution/invocation.py`
- production 基线 SHA256：`4165f689af384f9b91080b432328ce3003f9e4b4308bcf34960e1d3db0550f5d`
- frame owner 基线：`src/mote_kernel/execution/run_context.py`
- frame owner 基线 SHA256：`bf196695bce1687f0bd9554d3a8615e9af5cdbfa1bedbc859cd199e8ff54f648`
- behavior 基线：`tests/execution/test_continuation_integrity.py`
- behavior 基线 SHA256：`d900f3b812f9618587182ed4e86974cfc7dc0d3fa0de647ad9f797693bdaa17e`
- 唯一 production target：`invocation._validate_frame_index()` 的四段 coordinate canonicality phase
- `run_context.py` 裁决：`KEEP`；继续唯一拥有四类 record、coordinate segment 与 `ScopedFrameIndex`
- State/持久化边界：`HARD KEEP`；不修改 State、command、reducer、commit、protocol、Store 或 persistence
- Error recovery 边界：保持现有 continuation fail-closed 与 recovered invocation admission；不新增 retry、fallback、
  checkpoint、failover 或第二 recovery runner
- Complexity/ratchet：用户明确排除；automated complexity gate、health、complexity baseline、ratchet、limit 与 hook 均不属于 S16
  准入或交付证据
- Legacy/private-shape gate：用户明确排除；不新增、扩写或依赖冻结 private helper、局部变量、源码行数、AST 布局或
  `pairwise` 具体写法的测试

关联 owner：

- [Graph 语义保持型简化 requirements](graph-semantics-preserving-simplification-requirements.zh-CN.md)：唯一拥有
  `GSP-P01`–`GSP-P08`、`GSP-A06` 与批准状态；S16 已限定批准为本文 reviewed exact target SHA。
- [Graph 语义保持型简化主实施方案](graph-semantics-preserving-simplification-implementation.zh-CN.md)：拥有总账、阶段顺序和
  S16 候选边界；不拥有本文 exact target。
- [Graph Node I/O normative implementation](graph-node-input-output-contract-implementation.zh-CN.md)：唯一拥有当前
  `ScopedFrameIndex`、continuation integrity、complete/recovered snapshot 与 fail-closed 行为。
- [S15 implementation acceptance](graph-semantics-preserving-simplification-s15-implementation-acceptance.zh-CN.md)：只证明
  predecessor recovery commit 的 manifest 与门禁，不拥有 S16 target 或批准状态。
- `execution.run_context`：唯一拥有四类 typed frame record、四类 coordinate 与唯一 immutable `ScopedFrameIndex`。
- `execution.invocation`：唯一拥有 continuation 对 compiled graph、lineage State、descriptor、revision、provenance 与 concrete
  frame content 的 admission 编排。
- [S16 独立技术评审](graph-semantics-preserving-simplification-s16-implementation-review.zh-CN.md)：只记录本轮裁决与证据，不拥有
  target shape 或批准状态。
- [S16 评审回复](graph-semantics-preserving-simplification-s16-implementation-review-response.zh-CN.md)：只记录 owner 对评审项的
  接受/拒绝及理由，不拥有 target shape 或批准状态。
- [S16 第二次独立技术评审](graph-semantics-preserving-simplification-s16-implementation-second-review.zh-CN.md)：只记录二审裁决、
  case-to-branch 复核与 evidence，不拥有 target shape 或批准状态。
- [S16 第二次评审回复](graph-semantics-preserving-simplification-s16-implementation-second-review-response.zh-CN.md)：只记录二审
  owner disposition，不拥有 target shape 或批准状态。
- [S16 第三次独立技术评审](graph-semantics-preserving-simplification-s16-implementation-third-review.zh-CN.md)：只记录第三次
  technical review 裁决，不拥有 target 或批准状态。

本文是 S16 exact target、nominal 输入、结构净删除账本、等价证明、error precedence、behavior/tamper evidence、actual
manifest、门禁和停止条件的**唯一 owner**。本次按“单独写”只新增本文，不修改主实施方案、requirements、production 或 tests。
实施前必须取得 requirements owner 对独立技术评审所绑定本文 SHA256 的显式 `GSP-A06` 批准；该批准已完成，实际实现与验收结果见第 16 节及独立验收记录。
主实施方案第 3.4 节的 S16 行仍是候选账本和“每段至多一个 validator”的上限；本文第 2 节的 exact audit 将其收敛为
“零新增 validator、consumer owner 内直接相邻校验”。该收敛只在本文生效，不能反向改写主方案或 requirements 的批准状态。

## 2. 结论

S16 不提取四个 module-level validator。当前 `_validate_frame_index()` 的行为不是四段互相独立的“各自从头校验”，而是一个
固定的 lineage preflight 加三阶段 fail-closed frame 协议：

0. 先由 `lineage_states(context)` 建立 canonical lineage，并构造 admitted scope-run coordinate set；
1. 按 graph input → publication → resume input → child boundary 检查四段 record/coordinate nominal shape；
2. 仍按上述顺序检查四段 coordinate 唯一且 canonical；
3. 最后才按上述顺序校验 compiled descriptor、revision/provenance 和 concrete frame content。

把每段完整提取为一个 validator 会把顺序改成“graph input 的 shape/canonical/content 全部完成后再看 publication”，从而让
graph-input content error 抢在 publication malformed/canonical error 前出现。若为保持三阶段顺序而增加 phase enum、callback、
generator coroutine、context bag 或四组中间 DTO，则会新增控制协议和第二事实，不满足零负债。

Exact target 因而保持 `_validate_frame_index()` 为唯一编排 owner，只简化第二阶段：

- 删除四个 invocation-local coordinate tuple 投影；
- 删除把四类 coordinate 加宽后统一分派的 `(name, segment)` 临时 tuple；
- 删除每段 `set(segment)` 与 `tuple(sorted(segment))` 的重复全量构造；
- 直接复用 Python 3.11 标准库 `itertools.pairwise()` 和四类 coordinate 已有的 total ordering；
- 对每个 exact nominal segment 独立检查相邻 coordinate 是否严格递增；
- shape phase、canonical segment 顺序、content phase、错误 type/text 与相对时点全部保持。

Target 新增一个 module-scope 标准库 import，不新增 function、validator、DTO、dataclass、type alias、protocol、field、property、
cache、index、callback、context、State fact 或 public export。`run_context.py` 原样保持；四类 segment 和 canonical add path 继续只有
一个 owner。主方案“每个 segment 至多一个 validator”是上限而非必须新增 helper 的要求；S16 exact audit 证明新增零个 validator
即可得到更小闭环。

### 2.1 明确拒绝的替代方案

| 候选 | 裁决 | 原因 |
| --- | --- | --- |
| 四个完整 segment validator | 拒绝 | 若逐个调用会改变三阶段首错顺序；若按 phase 重入则新增隐式协议和重复 scan |
| 一个 generic canonical validator | 拒绝 | 需要 wide union、coordinate protocol、callback/key function 或错误 label 参数，违反 S16 明确边界 |
| `_FrameSegment`/`_CanonicalSegment` DTO | 拒绝 | 与 `ScopedFrameIndex` 四个 canonical tuple 平行保存同一事实，形成第二 owner |
| 在 `ScopedFrameIndex.__post_init__()` 校验 | 拒绝 | 会把错误从 `Graph.run()` continuation admission 前移到 private snapshot tamper/构造时，并改变 error boundary |
| 复用 `add_*()` 重建 index | 拒绝 | 重复排序和 duplicate scan，错误分类是 publication/admission error，且会构造第二 index |
| 删除 continuation canonicality 校验 | 拒绝 | sealed continuation 仍有明确 tamper/fail-closed contract；existing `add_*()` 不能替代 admission 时的防御性验证 |
| 把 compiled/content admission 移到 `run_context.py` | 拒绝 | `run_context` 不拥有 compiled topology、lineage State 或 values admission，移动会逆转依赖与复制 invocation owner |
| `zip(segment, segment[1:])` | 拒绝 | tuple slicing 为每段增加一次完整副本；标准库 `pairwise()` 已是 Python 3.11 基础设施 |
| sort 后直接接受 | 拒绝 | continuation admission 不能修复、重排或 normalize caller 提供的 snapshot；非 canonical 输入必须 fail closed |

## 3. `GSP-P01`–`GSP-P08` applicability

本表只引用 requirements ID，不复制 requirement 正文：

| Requirement | S16 裁决 | Exact target / evidence 责任 |
| --- | --- | --- |
| `GSP-P01` | 适用 | `Graph.run()` surface 与 `Graph.SnapshotMismatchError` 分类、文本和 public strict typing 不变 |
| `GSP-P02` | 不触及 / `HARD KEEP` | 不读取或修改 State shape、command、reducer、revision、codec 或 protocol；State tests 原样复跑 |
| `GSP-P03` | 适用 | continuation 全量 admission 仍早于 fence/resume/claim/child start/resource/node/commit；失败保持零 mutation |
| `GSP-P04` | 适用，核心 | 四类 frame、coordinate identity、唯一 index、complete/recovered continuation integrity 与 malformed fail-closed 保持 |
| `GSP-P05` | 适用 | publication provenance、resume-input 与 skip substitution continuation 仍按同一 segment/content owner 校验 |
| `GSP-P06` | 适用 | recovered continuation 仍先通过 exact frame validation，再投影 recovery seed；不改变 boundary、budget 或错误映射 |
| `GSP-P07` | 适用 | 四段 canonical ordering、nested child scope-run 与 child boundary identity 保持 |
| `GSP-P08` | 适用 | `Graph`/execution 唯一 owner、完整 generic、module-scope import、依赖方向和无类型擦除保持 |

`GSP-P02` 的“不触及”不是免除约束。任何 State/Store/protocol/persistence diff 都立即停止 S16。技术评审通过也不能替代
requirements owner 的显式 `GSP-A06`；本文只提交单项设计证据，不自批准。

## 4. 当前 production 审计与唯一事实

### 4.1 Owner inventory

| 事实/行为 | 当前唯一 owner | S16 处理 |
| --- | --- | --- |
| 四类 record/coordinate nominal shape | `execution.run_context` | `KEEP`，不搬迁、不包装、不新增 segment type |
| concrete frame canonical content admission | `execution.graph.values` 的四个 `_admit_*` owner | 原样调用，不增加 callback/generic adapter |
| compiled descriptor/materialization/publication truth | `CompiledGraph`/`FrontierTransitionPlan` | 原样读取，不缓存第二 projection |
| root/child lineage 与 exact scope-run State | `invocation.lineage_states()`/`_planned_state()` | 原样复用，不增加 state index |
| continuation integrity 总编排 | `invocation._validate_frame_index()` | 保持唯一 owner；只替换 canonicality 内部算法 |
| complete snapshot completeness | `invocation._validate_complete_context()` | `KEEP`，调用时点不变 |
| confirmed frame store | 唯一 `ScopedFrameIndex` | `KEEP`，不新增 dict/index/store |
| recovery seed availability | existing `recovery_seed()` + shared recovery engine | `KEEP`，validation 成功后才消费同一 frames |
| public invocation lifecycle | `Graph.run()`/existing family driver | `KEEP`，不新增执行入口或 recovery runner |

`run_context.py` 被主方案列为 S16 审计位置，是因为它定义 segment/record/index truth；这不等于 S16 必须修改该文件。Exact audit
确认 canonicality simplification 可以完全在 consumer owner 内完成。把 `run_context.py` 纳入 production manifest只会制造无必要 diff。

### 4.2 当前三阶段 error precedence

`_validate_frame_index()` 当前先调用 `lineage_states(context)`，再构造 admitted lineage 的 scope-run coordinate set。其后的固定顺序是：

| Phase | 顺序 | 当前失败边界 |
| --- | --- | --- |
| 0 | lineage → coordinate set | duplicate/invalid child lineage 保持由 existing lineage owner 抛错 |
| 1：nominal shape | graph input → publication → resume input → child boundary | `continuation <segment> segment contains a malformed record` |
| 2：canonicality | graph input → publication → resume input → child boundary | `continuation <segment> coordinates are not unique and canonical` |
| 3：content | graph input → publication → resume input → child boundary | existing unknown scope/descriptor/revision/provenance/frame-content error |

因此，同时存在多个 corruption 时也有明确首错。例如 publication malformed 必须早于 graph-input duplicate；publication duplicate 必须
早于 graph-input unknown-scope content error。S16 不把“输入本来就非法”当作重排错误的许可。

### 4.3 当前 canonicality mechanics

当前第二阶段先生成四个 coordinate tuples：

```text
graph_input_coordinates
publication_coordinates
resume_coordinates
boundary_coordinates
```

随后把四种不同 nominal tuple 放入一个带动态 `name` 的临时 tuple，并对每段执行：

```python
len(segment) != len(set(segment)) or segment != tuple(sorted(segment))
```

该表达式语义正确，但对每个 segment 重复 coordinate tuple、set、sorted list、sorted tuple 和 tuple equality traversal。临时
`name + segment` 分派只服务一次错误投影，不是独立 domain fact；四个 coordinate projection 也不被后续 content validation 复用。

### 4.4 Exact nominal domain

S16 只对当前声明并由 strict typing/constructors 支持的 exact nominal domain 证明等价：

| Segment | Exact record tuple | Exact ordered coordinate |
| --- | --- | --- |
| graph input | `tuple[AdmittedGraphInput[GraphValueT], ...]` | `GraphInputAvailabilityCoordinate[GraphValueT]` |
| publication | `tuple[ConfirmedPublication[GraphValueT], ...]` | `PublicationAvailabilityCoordinate[GraphValueT]` |
| resume input | `tuple[AdmittedResumeInput[GraphValueT], ...]` | `ResumeInputAvailabilityCoordinate[GraphValueT]` |
| child boundary | `tuple[ConfirmedChildBoundary[GraphValueT], ...]` | `ChildBoundaryAvailabilityCoordinate[GraphValueT]` |

四类 coordinate 及其组成的 `ScopeRunCoordinate`、`StableActivation`、`FrameDescriptorIdentity` 都是 existing frozen、`order=True`
nominal dataclass，字段继续是 canonical scalar/nominal identities。等价证明、accepted/rejected 集合和公共 canonicality 错误契约
仅适用于 compiler-produced、hashable 且 total-order 一致的 exact nominal coordinate domain。Target 不比较、hash、排序或 repr concrete
user frame/value；通过反射伪造的 unhashable/mixed inner field 不属于新增 contract，不能用来扩大或缩小该 nominal 域。

### 4.5 基线复现与工作树隔离

S16 的“源码基线”必须以第 1 节的三个文件 SHA256 和当前工作树 commit 同时核对。不能用 monorepo 的整体 `HEAD`、用户其他
未提交改动或 S15 `recovery.py` 的 diff 推导 S16 漂移。批准前复核者从 `mote-kernel` 目录执行：

```bash
git diff --quiet f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497 -- \
  src/mote_kernel/execution/invocation.py \
  src/mote_kernel/execution/run_context.py \
  tests/execution/test_continuation_integrity.py
sha256sum \
  src/mote_kernel/execution/invocation.py \
  src/mote_kernel/execution/run_context.py \
  tests/execution/test_continuation_integrity.py
```

期望 SHA256 分别是第 1 节列出的三个全值；第一条命令必须无输出且退出码为 0。若任一文件漂移，即使只来自前置 S15 的间接
consumer 变更，也必须重新计算 S16 behavior/error-order 影响并重新绑定 review SHA，不能把漂移静默纳入本文。该核对只读，不修改
Git index，也不把工作树中的其他文档、State、persistence 或 complexity 文件纳入 S16。

## 5. Exact target shape

### 5.1 保持不变的 signatures

```python
def _validate_frame_index(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None: ...


def validate_context(
    graph: CompiledGraph[GraphValueT],
    context: GraphRunContext[GraphValueT],
) -> None: ...
```

`GraphValueT` 继续从 compiled graph、run context、四类 records 到 concrete frames 保持同一 invariant universe。不新增 TypeVar、
TypeAlias、Protocol、overload 或 cast。

### 5.2 唯一新增 import

在连续 module-header 标准库 import block 中增加：

```python
from itertools import pairwise
```

不允许 function-local import、fallback implementation 或自定义 pairwise helper。项目最低 Python 为 3.11，直接复用标准库。

### 5.3 Exact canonicality phase

Phase 1 的四个 exact-type guards保持原文和顺序；Phase 3 的四个 content loops保持原文和顺序。两者之间的 Phase 2 精确替换为
Ruff 0.16.2 的 canonical output（以下布局本身是 reviewed exact target）：

```python
if any(previous.coordinate >= current.coordinate for previous, current in pairwise(context.frames.graph_inputs)):
    raise SnapshotMismatchError("continuation graph input coordinates are not unique and canonical")
if any(previous.coordinate >= current.coordinate for previous, current in pairwise(context.frames.publications)):
    raise SnapshotMismatchError("continuation publication coordinates are not unique and canonical")
if any(previous.coordinate >= current.coordinate for previous, current in pairwise(context.frames.resume_inputs)):
    raise SnapshotMismatchError("continuation resume input coordinates are not unique and canonical")
if any(
    previous.coordinate >= current.coordinate for previous, current in pairwise(context.frames.child_boundaries)
):
    raise SnapshotMismatchError("continuation child boundary coordinates are not unique and canonical")
```

`>=` 是 exact requirement：`>` 会放过 duplicate coordinate，`!=` 不能证明排序，`<=` 会反转 canonical direction。不得先 sort、
不得自动修复 snapshot、不得把四段放入 dict/set 后迭代，也不得抽成接收 wide union 或 key callback 的 helper。

### 5.4 新增上限与禁止漂移

Exact production target 上限固定为：

```text
new module-level/local function or validator: 0
new class/dataclass/DTO/type alias/protocol/enum: 0
new field/property/cache/index/stored fact: 0
new callback/context bag/string discriminator: 0
new TypeVar/overload/cast/ignore: 0
new import: 1 (`itertools.pairwise` only)
new public export/API/error class: 0
new State/command/reducer/protocol/persistence artifact: 0
modified production file: 1 (`execution/invocation.py` only)
modified behavior test file: at most 1 (existing continuation-integrity file only)
new test file: 0
new/modified normative behavior document: 0
new/modified complexity or legacy gate artifact: 0
```

若 implementation 需要修改 `run_context.py`、新增 validator/helper，或无法用 exact nominal comparisons通过 strict Pyright，立即停止并
重新评审；不得用 `Any`、`object`、bare tuple、generic-erasing cast 或 compatibility path 兜底。

## 6. 语义等价证明

### 6.1 Unique + sorted 与 strict adjacent order 等价

对任一 exact coordinate tuple `C = (c0, ..., cn)`，现有 predicate 接受当且仅当：

```text
len(C) == len(set(C))
and C == tuple(sorted(C))
```

Target 接受当且仅当：

```text
for every adjacent pair (ci, ci+1): ci < ci+1
```

在第 4.4 节 total-order nominal domain 中：

- `sorted + unique -> strict adjacent`：canonical 非降序中若任一相邻值相等就违反 unique，因此每一对严格递增；
- `strict adjacent -> sorted + unique`：严格相邻递增经传递性得到全序递增，因此原 tuple 已排序且任意两项不相等；
- 空 tuple 与 singleton 的 `pairwise()` 均为空，和当前 set/sorted predicate 一样接受；
- 反证方向也成立：若原 tuple 不是“唯一且已排序”，却不存在任何 `previous >= current`，则所有相邻项严格递增；由
  `order=True` coordinate 的传递性，整个 tuple 必然严格递增，反而同时满足唯一与已排序。因此 duplicate（无论原位置是否相邻）
  或任一逆序都必然出现至少一个 `previous >= current` 位置，target 同样拒绝。

因此四段 accepted/rejected set 完全相同。Target 不改变 coordinate equality/order definition，只删除重复 materialization。

### 6.2 Error type、text 与 phase precedence

lineage preflight 通过后，Phase 1 保持原四个 guard，所以既有 malformed record/outer coordinate type 边界仍最先按相同 segment 顺序
抛同一 `SnapshotMismatchError`；这部分继续由现有 public shape contract 拥有。在第 4.4 节 exact nominal domain 内，Phase 2 的四个
显式 guard按当前临时 tuple完全相同的顺序排列，且错误 literal 与当前 f-string 展开结果逐字相同。Phase 3 不移动，因此：

```text
lineage preflight errors
    before all shape errors
        before all canonicality errors
            before all descriptor/revision/provenance/frame-content errors
```

保持不变。Target 不增加 `try/except`，不捕获/映射 `SnapshotMismatchError`、`GraphValueAdmissionError` 或 recovery errors。

### 6.3 Traversal 与 side-effect 等价

现有 set/sorted 实现会完整遍历 coordinate tuple；target `any(pairwise(...))` 可在首个 inversion短路。该 traversal 次数差异在第 4.4
节 exact nominal domain 内不可观察：coordinate/record 是 immutable internal values，比较不调用 user callback，不读取 concrete
frame，不提交 State，不安装 frame，也不执行 node/resource/commit。两者都在任何 Phase 3 content admission 与 lifecycle mutation前抛同一
错误。

S16 不为通过 cast、`object.__setattr__` 或其他反射手段伪造的 inner dataclass field建立新的 public 行为契约。§8.2 的
baseline-vs-target inner-tamper probe 仍是 `PENDING / NOT EVIDENCE COMPLETE`，设计阶段不把它宣称为已完成证据；probe 只审计列出的
descriptor/scope/activation/node/enum-int scalar 场景。forged unhashable/mixed inner field 不在第 4.4 节 nominal contract 内，不新增
catch、normalizer 或 malformed 行为范围；若已列出的 characterized scalar probe 在 target 中改变 exception type/text/cause，则按第 14
节停止并保留现状或另立需求。

### 6.4 Content、complete/recovered 与 recovery 等价

Target 不修改四个 content loops，因而以下 owner、调用次数与错误保持：

- graph input：known scope-run、graph input descriptor与 `_admit_graph_input_frame()`；
- publication：planned State、compiled publication descriptor、superstep、ack revision、closed provenance、execution generation与
  `_admit_node_output_frame()`；
- resume input：planned State、materialization descriptor、superstep与 `_admit_node_input_frame()`；
- child boundary：child State completed status、graph output descriptor与 `_admit_graph_output_view()`；
- complete continuation：`_validate_complete_context()` 仍只在 `not context.recovered` 时随后执行；
- recovered continuation：合法历史缺失仍交给 existing recovery availability proof，extra/inconsistent frame仍在 seed构造前 fail closed。

不改变 `lineage_states()`、`_planned_state()`、`ScopedFrameIndex`、recovery seed、frontier proof、budget、fence/resume/commit 或
memory-frame installation。

### 6.5 成本等价与改进

对四段总记录数 `N`：

| 项目 | 当前 | Target |
| --- | --- | --- |
| canonicality 时间 | `O(N log N)`，由每段 `sorted` 主导 | `O(N)`，每段至多一次相邻 scan |
| canonicality 额外空间 | `O(N)` coordinate tuple + set + sorted list/tuple | `O(1)` iterator state |
| concrete frame访问 | 0 | 0 |
| user callback/State/commit side effect | 0 | 0 |
| accepted/rejected nominal inputs | 当前集合 | 完全相同 |

这里的 `O(N)` 以四类 coordinate 的既有固定字段宽度为常数；性能不是批准理由本身，其价值在于同时删除四份不可复用投影和
异构分派，而不新增抽象或改变 owner。

## 7. 零新增负债与结构净删除账本

本账本是 reviewed target 的一次性结构证明，不建立 automated complexity 或 legacy gate：

| 结构项 | Before | Target | 净变化 |
| --- | ---: | ---: | ---: |
| `invocation.py` module-level functions | 15 | 15 | 0 |
| 新 segment validator/helper | 0 | 0 | 0 |
| 新 DTO/dataclass/alias/protocol/context | 0 | 0 | 0 |
| invocation-local coordinate tuple projections | 4 | 0 | -4 |
| heterogeneous `(name, segment)` dispatch tuples | 1 | 0 | -1 |
| canonicality `set(...)` constructions | 4 | 0 | -4 |
| canonicality `sorted(...)` constructions | 4 | 0 | -4 |
| canonicality sorted-result tuple copies（`sorted(...)` 结果说明，不另计） | — | — | 已包含在上一行，不可加总 |
| dynamic segment label/f-string projection（heterogeneous dispatch 的说明，不另计） | — | — | 已包含在 dispatch 行，不可加总 |
| direct nominal adjacent scans | 0 | 4 | +4 |
| module-scope imports | baseline | baseline + 1 | +1，标准库 `pairwise` |
| State/store/cache/index/stored fact | 0 | 0 | 0 |
| public API/export/error type | 0 | 0 | 0 |

Target 有意把一个异构 loop body展开为四个 exact nominal guards，因此不以 decision-point 数或源码行数作为门禁。用户明确排除的
complexity ratchet不能否定该设计，也不能被更新来“放行”设计。按复合表达式计数，删除的 13 个 invocation-local 构造/分派点是：
四个 coordinate projection、一个 heterogeneous dispatch tuple、四个 `set(...)` 与四个 `tuple(sorted(...))`；表中的
“sorted-result tuple copies”与“dynamic segment label/f-string projection”都是上一项的说明，不再重复计数。零负债由以下事实闭合：顶层定义零增长、stored facts零增长、
四类 owner不合并、上述 13 个旧构造/分派点归零、只增加一个标准库 primitive和四个直接消费点、算法空间/时间下降。

若 actual diff保留任一旧 coordinate projection、set/sorted path或异构 dispatch，形成 old/new双路径，则不满足本账本。若为了少写四个
guard引入 generic helper/callback/wide union，同样不满足零负债。

## 8. Behavior、error-order 与 tamper evidence

### 8.1 当前 baseline

2026-08-24 在工作树源码基线 `f9182fa7689ceb51ca7d562f0e5d80c1dc7d5497` 上原样运行；S15 已接受的 `recovery.py` 变更不在 S16 target 文件内：

```text
tests/execution/test_continuation_integrity.py       → 34 passed
tests/execution                                      → 563 passed
9 个 active architecture/source/owner nodeids        → 9 passed
tests/state/graph_state                               → 206 passed
all tests (excluding complexity gate)                 → 826 passed, coverage 100.00%
pyright                                               → 0 errors, 0 warnings, 0 informations
ruff check (invocation.py + continuation test)        → passed
ruff format --check (invocation.py + continuation test) → already formatted
```

Baseline 只证明当前行为，不冒充 target 已实施、技术评审通过或 `GSP-A06` 批准。上述计数来自第 13 节 scoped 命令；若基线文件
SHA256或当前 commit变化，计数必须重新运行。现有 case-level evidence至少包括：

| 义务 | Exact existing case |
| --- | --- |
| duplicate coordinate fail closed | `tests/execution/test_continuation_integrity.py::test_complete_continuation_rejects_duplicate_frame_coordinates` |
| 四类 malformed record | 同文件 `::test_complete_continuation_rejects_a_malformed_{graph_input,publication,resume_input,child_boundary}_record` 四个 case |
| graph-input scope/descriptor/content | 同文件 `::test_complete_continuation_rejects_a_foreign_graph_input_coordinate`、`::test_complete_continuation_rejects_a_wrong_graph_input_descriptor`、`::test_complete_continuation_readmits_graph_input_frame_names_and_values`、`::test_complete_continuation_rejects_the_wrong_graph_input_frame_nominal` |
| publication coordinate/provenance/content | 同文件 `::test_complete_continuation_rejects_an_inconsistent_publication`、`::test_complete_continuation_rejects_invalid_execution_publication_provenance`、`::test_complete_continuation_readmits_publication_frame_content` |
| skip substitution integrity | 同文件 `::test_valid_historical_substitution_continuation_survives_frontier_advance` 与 `::test_substitution_continuation_rejects_each_integrity_violation[...]` |
| resume-input coordinate/content | 同文件 `::test_complete_continuation_rejects_an_inconsistent_resume_input`、`::test_complete_continuation_readmits_resume_input_frame_content` |
| child-boundary coordinate/content | 同文件 `::test_complete_continuation_rejects_an_inconsistent_child_boundary`、`::test_complete_continuation_readmits_child_boundary_frame_content` |
| complete snapshot completeness | 同文件 `::test_complete_continuation_requires_every_scoped_graph_input`、`::test_complete_continuation_requires_each_current_success_publication`、`::test_complete_continuation_requires_historical_graph_output_publication`、`::test_complete_continuation_requires_completed_child_boundary` |
| recovered snapshot content admission | `tests/execution/test_continuation_integrity.py::test_recovered_continuation_readmits_existing_frame_content` |
| child lineage先于frame validation | 同文件 `::test_recovered_continuation_rejects_an_unknown_child_scope`、`::test_recovered_continuation_rejects_duplicate_child_run_coordinates`、`::test_recovered_continuation_rejects_a_child_run_id_mismatch`、`::test_recovered_continuation_rejects_inconsistent_parent_coordinates` |

### 8.2 与 production 原子新增的 behavior cases

现有 suite没有完整冻结“descending但不duplicate”与跨 phase simultaneous corruption 的首错。批准后必须在同一个 implementation unit
中向现有 `tests/execution/test_continuation_integrity.py` 增加以下 **7 个** public-run tamper cases；名称可因测试文件既有命名规范做
机械调整，但断言目标不得缩减。四个 canonicality production branches 必须各有 public behavior evidence；实际会到达对应 `raise`
的 case-to-branch 映射如下，不把更早被短路的 case 计入错误分支：

| canonicality raise branch | 实际到达该 raise 的 case |
| --- | --- |
| graph input | `test_complete_continuation_rejects_descending_frame_coordinates`；`test_continuation_validation_keeps_canonical_segment_order` |
| publication | `test_continuation_validation_keeps_canonicality_before_content_precedence`；`test_recovered_continuation_rejects_noncanonical_frame_coordinates` |
| resume input | `test_complete_continuation_rejects_descending_resume_input_coordinates` |
| child boundary | `test_complete_continuation_rejects_descending_child_boundary_coordinates` |

`test_continuation_validation_keeps_shape_before_canonicality_precedence` 只覆盖 Phase 1 publication shape error，不覆盖任何 canonicality
`raise`；不通过 coverage omit、pragma 或 private-source-shape test 掩盖缺口。

测试构造可以复用该文件已有的 `ContinuationEditor`/`dataclasses.replace` tamper harness，但最终断言必须通过 `Graph.run()` 的公开
行为边界。由于 `ScopedFrameIndex.add_*()` 会主动排序，descending case 必须在 test-only harness 中安装原始 tuple；不能借助
`add_*()` 先 normalize 再声称覆盖 canonicality。该 harness 只制造输入，不加入 production API、测试专用 validator 或永久 source-shape
门禁。每个 case 都必须落实 `M0`：通过公开 `Graph.run()` 观察首个 `Graph.SnapshotMismatchError`，`str(error)` 与表中完整 literal
逐字相等，并直接断言 admission failure 发生在任何生命周期推进前、输入 State 与 continuation snapshot 保持未修改。对于表中直接
由 shape/canonicality precedence 抛出的 `Graph.SnapshotMismatchError`，还必须断言 `raised.value.__cause__ is None`；该断言不套用
Phase 3 content-admission 既有的 `raise ... from error` cause contract。
fence/resume/claim/child start/resource/node/commit 均不发生 mutation 的结论复用既有
`M0` 跨层 mutation-free 交叉证据，
`tests/execution/test_graph_recovery_contract.py::test_recovered_plain_skip_rejects_a_missing_graph_output_before_commit`、
`::test_recovered_control_target_rejects_a_lost_graph_input_before_mutation`、
`tests/execution/test_graph_api.py::test_state_only_multi_scope_substitution_is_rejected_before_first_commit` 与
`::test_normal_resume_never_mutates_the_input_continuation_snapshot`；不把新的 `CommitLog`、node/resource call counter 或 callback
计数器塞入 S16 continuation test 文件，也不把 recovery code 纳入 S16 manifest。

| Target case | 构造 | 首错 phase/segment、完整错误 literal与 `M0` | 可杀死的错误变体 |
| --- | --- | --- | --- |
| `test_complete_continuation_rejects_descending_frame_coordinates` | 使用至少两个合法 graph-input coordinates 的 complete nested continuation，仅反转该 segment | Phase 2 / graph input；`"continuation graph input coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | 只拒绝 duplicate、比较方向写反、自动 sort/修复 |
| `test_continuation_validation_keeps_shape_before_canonicality_precedence` | 同一 snapshot 同时含 graph-input duplicate 与 malformed publication record | Phase 1 / publication；`"continuation publication segment contains a malformed record"`；direct `__cause__ is None`；落实 `M0` | 每段完整 validator 串行化、canonicality 抢在 later shape 前 |
| `test_continuation_validation_keeps_canonicality_before_content_precedence` | 同一 snapshot 同时含 foreign graph input 与 duplicate publication | Phase 2 / publication；`"continuation publication coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | graph-input content 提前、phase 2/3 交错 |
| `test_continuation_validation_keeps_canonical_segment_order` | 同一 snapshot 同时使 graph-input 与 publication coordinate noncanonical | Phase 2 / graph input；`"continuation graph input coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | dict/set 无序 dispatch 或 segment 次序变化 |
| `test_recovered_continuation_rejects_noncanonical_frame_coordinates` | 复用已有 recovered nested fixture，反转其已有两个 publication coordinates | Phase 2 / publication；`"continuation publication coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | 只覆盖 complete path、recovered path绕过 frame validation |
| `test_complete_continuation_rejects_descending_resume_input_coordinates` | 使用至少两个合法 resume-input coordinates 的 complete continuation，仅在 test harness 中反转该 segment | Phase 2 / resume input；`"continuation resume input coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | 漏掉 resume-input branch、比较方向写反、自动 sort/修复 |
| `test_complete_continuation_rejects_descending_child_boundary_coordinates` | 使用至少两个合法 completed child-boundary coordinates 的 complete nested continuation，仅反转该 segment | Phase 2 / child boundary；`"continuation child boundary coordinates are not unique and canonical"`；direct `__cause__ is None`；落实 `M0` | 漏掉 child-boundary branch、比较方向写反、自动 sort/修复 |

这些是通过 `Graph.run()` 观察的 continuation integrity行为测试，不是 legacy/private-source-shape gate。测试不得读取源码、AST、helper名、
局部变量、行数、import文本或断言实现必须使用 `pairwise`；任何能保持同一行为和本文结构上限的未来实现都应通过。
对于 exact outer record/coordinate type 但通过 `object.__setattr__` 伪造 inner scalar 的输入，实施验收完成了一次性 baseline-vs-target
probe，覆盖 descriptor identity、scope/run identity、activation superstep、node identity 与 enum/int 字段；结果与第二次评审记录的
baseline 一致，详见独立验收记录。该 probe 不转化为永久测试或 source-shape gate。若 target 将已列出的 characterized scalar probe 的
`TypeError`/`SnapshotMismatchError` 或其 message/cause 改变，按第 14 节停止；forged
unhashable/mixed inner field 不属于第 4.4 节 nominal contract，不以“private tamper”扩大 malformed 行为，也不新增 catch/normalizer。
该 probe 不提交为测试，也不把 forged object 扩大为新的 nominal contract。

### 8.3 一次性 target predicates

Implementation actual diff/source review只核对以下 predicates，不把它们写成永久 pytest：

| ID | Target predicate | 失败含义 |
| --- | --- | --- |
| `S16.a` | `_validate_frame_index()` 仍是唯一 frame-index validation入口，signature/call site不变 | 新 validator owner、第二 admission path或public surface漂移 |
| `S16.b` | 四个 exact-type guards完整保留且先于所有 canonical guards | malformed error priority改变 |
| `S16.c` | 四个 canonical guards按 graph input → publication → resume input → child boundary排列 | segment首错或determinism改变 |
| `S16.d` | 四个 content loops原文留在全部 canonical guards之后 | descriptor/frame error priority改变 |
| `S16.e` | 四个 coordinate projections、set/sorted与异构 dispatch全部归零 | 双路径、重复 scan或旧派生事实残留 |
| `S16.f` | 顶层定义零增长，唯一新增import为`itertools.pairwise`，`run_context.py`零diff | helper/DTO/第二 owner/typing debt |
| `S16.g` | State/protocol/persistence/complexity/legacy artifacts零diff | 越界或用治理改动掩盖target |
| `S16.h` | `lineage_states()`、scope-run coordinate set、四个 shape guards 与四个 content loops 的调用顺序/调用次数保持 | lineage 首错、shape/content phase 交错或 recovery admission 漂移 |
| `S16.i` | complete 与 recovered 两条 public `Graph.run()` 路径都经过同一 `_validate_frame_index()`；failure 仍在任何 mutation 前传播 | 只验证 complete、recovered 绕过 canonicality 或错误被 fallback/重试吞掉 |
| `S16.j` | 7 个 case 中直接由 shape/canonicality precedence 抛出的 `SnapshotMismatchError` 满足 `raised.value.__cause__ is None`；Phase 3 既有 `raise ... from error` cause contract 不变 | cause chaining 被新增 guard 意外引入或 content-admission cause 被改写 |

## 9. State、持久化与错误恢复硬边界

S16 planned manifest遵守：

- 不修改 `src/mote_kernel/state/**`、`tests/state/**`、`GraphRunState`、command、reducer、validation、revision或codec identity；
- 不新增 Store、repository、journal、event log、checkpoint、snapshot service、database、persistence port/backend、retry queue或
  exactly-once层；
- 不把 frame、coordinate、validation result、canonicality cache或continuation写入 State、Graph instance、global registry或第二 store；
- 不改变 commit callback、candidate structural acknowledgement、durable-first/memory-install顺序，且不把 callback描述为持久化保证；
- 不新增 `Graph.resume()`、recovery runner、automatic retry/backoff、Kernel failover policy或错误分类策略；
- 不捕获、包装或互相映射 `SnapshotMismatchError`、`GraphValueAdmissionError`、`GraphValueUnavailableError`、
  `ExecutionLimitError`；
- 不扩大 recovered continuation可合法缺失历史frame的范围，也不把 complete snapshot缺失降级为 recovery availability问题；
- 不把 continuation、State或frame index增加 serialization/copy/pickle/跨进程恢复协议。

当前 error recovery含义保持为：调用方显式提供authoritative State与同 lineage合法 continuation时，existing `Graph.run()`先完成
family/lineage/frame validation；recovered lineage随后才进入shared availability proof。S16只替换 fail-closed canonicality phase的
内部纯检查；这里的“保持”只表示不改变既有 admission 行为，不表示本单元实现任何新的错误恢复能力。不新增“修复损坏snapshot”、
fallback到state-only、自动重试或跨进程恢复能力。

## 10. 唯一真相与 normative 同步裁决

S16不改变四类 record/coordinate/index shape、continuation variant、错误文本、admission owner或公共行为。因此 production implementation
**不修改** architecture、Node I/O、skip-output、State/protocol或README normative文档；修改这些文件会制造无必要diff或第二真相。

文档 owner固定为：

| 内容 | 唯一 owner |
| --- | --- |
| `GSP-Pxx`/`GSP-A06`与批准状态 | requirements |
| S16 exact future target、结构账本、evidence、manifest、gate、writeback | 本文 |
| 总账、阶段顺序与S16生命周期链接 | 主实施方案，只作索引 |
| 当前 continuation/frame行为与shape | Node I/O normative source + current production/characterization |
| review裁决 | S16 review record；owner disposition 由独立 response record 记录 |

若实施时发现必须改变 normative behavior才能完成，不得通过同步文档追认；直接触发停止条件并保持production现状。

## 11. Atomic change units 与 exact manifests

### 11.1 当前独立 design unit

本次“单独写”docs-only unit的exact actual manifest只有：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation.zh-CN.md
```

不得把主实施方案、requirements、production、tests、历史review、README、complexity或用户工作树中其他已修改文件列入本单元。

### 11.2 独立技术评审 units

每次独立技术评审的 manifest 只能包含该次实际新增的 S16 review record；首轮、二审与三审已经分别形成独立 unit，后续评审仍
必须沿用同一边界：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-review.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-second-review.zh-CN.md
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation-third-review.zh-CN.md
```

每个 review 必须绑定当时 owner SHA256并裁决 exact target、nominal-domain proof、error precedence、test plan与manifest；不复制
target、不修改 requirements、不自批准。对应 response 也是独立 docs-only unit，只记录 disposition，不拥有 target 或批准状态。若需
整改本文，整改是只包含本文的独立 docs-only unit，之后重新绑定新 SHA 评审。

### 11.3 Requirements approval unit

技术评审通过且用户显式批准后，requirements owner才可用独立unit记录：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-requirements.zh-CN.md
```

只记录`GSP-A06 SATISFIED / APPROVED — 仅限reviewed S16 exact target SHA`，不复制本文算法。批准前production/tests保持不变。

### 11.4 Production + behavior implementation unit

批准后的exact planned manifest只有：

```text
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/test_continuation_integrity.py
```

两个文件形成一个原子change unit：production target与第8.2节error-order cases同时落地，不先改tests制造红门禁，也不先改production后补
证据。不修改`run_context.py`、normative文档、complexity framework、State、protocol、README或本设计文档。若actual diff需要第三个文件，
先停止并重新评审manifest。

### 11.5 Implementation owner writeback与总账索引

Production与全部适用门禁通过后，implementation owner writeback是独立docs-only unit：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s16-implementation.zh-CN.md
```

它只记录implementation commit、actual manifest/ledger、source review和gate结果。主实施方案如需增加S16链接或更新生命周期，另以主方案
自身一个文件形成独立index unit。不得把design、review、approval、production、writeback与index累计成同一manifest。

## 12. 批准后的实施顺序

1. 按第 4.5 节校验`invocation.py`、`run_context.py`与behavior文件仍等于第1节baseline；任何漂移先重做diff影响分析并重新评审，
   不静默并入（S15 `recovery.py` 的 accepted diff 不纳入该核对）。
2. 原样运行第8.1节continuation integrity baseline与第13节active owner/typing gates。
3. 在现有test文件加入第8.2节七个public-run behavior cases，和production改动保留在同一未提交原子unit；其中新增 resume-input 与
   child-boundary descending cases，publication branch由既有 precedence/recovered cases共同覆盖。
4. 在`invocation.py`连续module-header import block增加`from itertools import pairwise`。
5. 一次删除四个coordinate projection与完整异构canonical loop，直接放入第5.3节四个nominal guards；不保留old/new双路径。
6. 不移动Phase 1 exact-type guards或Phase 3 content loops，不改`validate_context()`调用关系。
7. 先运行focused continuation tests、single-file Ruff和strict Pyright，关闭错误文本/precedence与typing问题。
8. 按第7节逐项核对actual结构账本，按第8.3节完成一次性source review；不生成legacy/private-shape test。
9. 运行第13节全部适用门禁并核对第11.4节two-file actual manifest。任一失败整体撤回本implementation unit。
10. Production单独提交后再做第11.5节owner writeback；不得amend混入design/review/approval。

## 13. Verification gates

### 13.1 Gate 分类

```text
REQUIRED: current continuation/complete/recovered behavior、exact error type/text/cause/phase/segment precedence、strict typing、
          active generic/dependency/owner/source-discipline、lint、format、coverage、build/package、
          State/no-persistence negative gate、scoped monorepo pre-commit与whitespace checks
REQUIRED: 第7节manual structural ledger与第8.3节一次性actual diff/source review
USER-EXCLUDED: automated complexity gate、health、complexity baseline、ratchet、limit与hook，无论既有还是拟新增
USER-EXCLUDED: legacy/private-source-shape gate，无论既有还是拟新增
```

完整`make check`当前无条件包含用户明确排除的`complexity-ratchet`，因此不属于S16 gate；不得运行后忽略失败再冒记为通过。
下面显式执行其余适用质量检查。排除automated complexity不放宽第7节任何结构上限。

### 13.2 Focused behavior 与 active architecture gates

```bash
python -B -m pytest -q -p no:cacheprovider \
  tests/execution/test_continuation_integrity.py

python -B -m pytest -q -p no:cacheprovider \
  tests/architecture/test_generic_integrity.py::test_production_boundaries_preserve_generic_types \
  tests/architecture/test_dependency_direction.py::test_execution_does_not_depend_on_domain_packages \
  tests/architecture/test_dependency_direction.py::test_graph_definition_layer_does_not_depend_on_runtime_execution_modules \
  tests/architecture/test_graph_execution_ownership.py::test_graph_state_and_execution_contracts_have_single_owners \
  tests/architecture/test_graph_execution_ownership.py::test_executor_does_not_apply_state_or_own_persistence \
  tests/architecture/test_source_discipline.py::test_imports_form_a_contiguous_module_header \
  tests/architecture/test_source_discipline.py::test_dynamic_import_and_reflection_escape_hatches_are_forbidden \
  tests/architecture/test_source_discipline.py::test_internal_any_is_forbidden \
  tests/architecture/test_source_discipline.py::test_execution_is_the_only_generic_executor_owner

python -B -m pytest -q -p no:cacheprovider tests/execution
python -B -m pytest -q -p no:cacheprovider tests/state/graph_state
python -B -m ruff check \
  src/mote_kernel/execution/invocation.py \
  tests/execution/test_continuation_integrity.py
python -B -m ruff format --check \
  src/mote_kernel/execution/invocation.py \
  tests/execution/test_continuation_integrity.py
pyright
```

这些architecture nodeids验证仍有效的generic、dependency、single execution/State owner、no-persistence和source discipline；不冻结
S16局部变量、guard数量、错误literal布局或`pairwise`实现。
其中 `test_execution_is_the_only_generic_executor_owner` 的唯一可复现路径是
`tests/architecture/test_source_discipline.py`；上述九个 nodeid 逐字执行应得到 `9 passed`。

### 13.3 Coverage、build 与 package gates

从`mote-kernel`目录运行排除唯一complexity gate文件的完整coverage suite，再构建和检查包：

`--cov-fail-under=100` 的 target 条件必须在第 8.2 节七个 public cases 全部落地后复核，并以 §8.2 的实际到达矩阵为 evidence：graph
input branch 由 `test_complete_continuation_rejects_descending_frame_coordinates` 与
`test_continuation_validation_keeps_canonical_segment_order` 覆盖，publication branch 由
`test_continuation_validation_keeps_canonicality_before_content_precedence` 与
`test_recovered_continuation_rejects_noncanonical_frame_coordinates` 覆盖，resume-input 与 child-boundary branch 分别由 descending
cases 覆盖；shape precedence case 不计入 canonicality branch。直接 shape/canonicality error 还必须满足 `__cause__ is None`。
评审在 `/tmp` 临时副本中的 planning probe 记录了未补齐分支时 `826 passed / 99.90%`、补齐 publication/resume/child probes 后
`829 passed / 100.00%`；这些数字不是 implementation result，也不进入 S16 manifest。不得修改 coverage 配置、omit 列表或测试发现规则
来取得绿色结果。

```bash
python -B -m pytest -q -p no:cacheprovider \
  --ignore=tests/architecture/test_complexity_gate.py \
  --cov=mote_kernel --cov-report=term-missing --cov-fail-under=100
python -B -m build --no-isolation
python -B -m twine check dist/*
```

### 13.4 Repository、manifest 与 whitespace gates

从monorepo root只对第11.4节two-file manifest运行；显式跳过用户排除的complexity hook：

```bash
cd ..
SKIP=kernel-complexity pre-commit run --files \
  mote-kernel/src/mote_kernel/execution/invocation.py \
  mote-kernel/tests/execution/test_continuation_integrity.py
git diff --check -- \
  mote-kernel/src/mote_kernel/execution/invocation.py \
  mote-kernel/tests/execution/test_continuation_integrity.py
git diff --cached --check -- \
  mote-kernel/src/mote_kernel/execution/invocation.py \
  mote-kernel/tests/execution/test_continuation_integrity.py
```

当前docs-only design unit只对第11.1节实际新增文档运行scoped pre-commit与whitespace检查。门禁不修改Git index，不把用户现有未提交
文件纳入S16 manifest；未跳过complexity的pre-commit或完整`make check`结果不得写成S16通过条件。

## 14. 停止条件

除requirements `GSP-S01`–`GSP-S08`外，出现任一条件立即停止并保持production现状：

1. 无法保持shape → canonicality → content三阶段顺序，或四段内部graph input → publication → resume input → child boundary顺序；
2. 第 4.4 节 exact nominal public domain 内任一 existing `SnapshotMismatchError`/admission error 的 type、展开后 text、cause chaining
   或相对时点变化；尤其是直接 shape/canonicality precedence error 不再满足 `__cause__ is None`；
3. strict-adjacent predicate在第4.4节exact nominal domain与current unique+sorted accepted set不等价；
4. §8.2 已列出的 characterized inner-scalar probe 从当前错误变成不同 exception，或只能缩小 nominal/malformed contract 才能实施；
5. Phase 1 exact-type guards、Phase 3 content loops、`_validate_complete_context()`或`validate_context()`需要移动/改写；
6. 需要新增validator/helper、DTO、alias、protocol、enum、callback、context bag、cache、index、field、property或public export；
7. 需要`Any`、`object`、bare container、cast、ignore、reflection、string discriminator或dynamic import；
8. 旧coordinate projections、set/sorted或异构dispatch不能全部删除，形成兼容/双路径；
9. actual production manifest不再是`invocation.py + existing continuation test file`，或需要修改`run_context.py`/normative/complexity artifact；
10. 需要新增或依赖legacy/private-source-shape gate才能证明正确；
11. 触及State、State tests、command/reducer、commit、protocol、Store/persistence或第二execution/recovery runner；
12. 把continuation validation、callback acknowledgement或in-memory frames写成durability、automatic crash recovery或Kernel failover保证；
13. 任一current behavior、strict typing、active owner/dependency/source-discipline、coverage、lint/build/package或repo gate失败且不能在
    exact target内修复；
14. requirements owner尚未对independent review绑定的S16 exact SHA显式授予`GSP-A06`。

## 15. 当前交付状态

S16 已完成第三次独立技术评审、requirements owner 的 `GSP-A06` 单项批准、两文件原子 implementation、七个 public behavior cases、
direct `__cause__ is None` predicate、inner-scalar baseline-vs-target probe、一次性 `S16.a`–`S16.j` source review 以及第 13 节适用
门禁。当前状态不扩大 approved SHA、manifest 或用户明确排除的 complexity/legacy gate 范围。

```text
DESIGN / THIRD-REVIEW WRITEBACK COMPLETE
GSP-A06 SATISFIED / APPROVED — reviewed exact target SHA only
PRODUCTION + BEHAVIOR IMPLEMENTATION: IMPLEMENTED / VERIFIED
PRODUCTION MANIFEST: invocation.py + existing continuation-integrity test file
RUN_CONTEXT / STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP / UNTOUCHED
NEW ERROR-RECOVERY / RETRY / FALLBACK / CHECKPOINT / FAILOVER: NONE
AUTOMATED COMPLEXITY + LEGACY/PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED
```

完整验收证据见独立 [S16 implementation acceptance](graph-semantics-preserving-simplification-s16-implementation-acceptance.zh-CN.md)。

## 16. Implementation owner writeback（2026-08-24）

### 16.1 授权与实际 manifest

requirements owner 已依据第三次独立技术评审和用户显式授权，将 `GSP-A06` 限定批准到 reviewed design SHA256
`abbdb198cb9eb76f5342bc70fd9e9377f6fc781dfe7b8e1f1d116f69a6461402`。本次 writeback 不改变 requirements、review 或 exact target，
只记录实际 implementation 与验收结果。

实际 implementation manifest 与批准 manifest 完全一致：

```text
mote-kernel/src/mote_kernel/execution/invocation.py
mote-kernel/tests/execution/test_continuation_integrity.py
```

本次实现已在 production commit `f9854e1dbc68cfe79e1201095ccfd7f4a18a6aad` 落地；该 commit 只包含批准的两文件 implementation
manifest，本记录不把其他工作树修改累计进 S16。
实际 production/test SHA256 分别为：

```text
invocation.py                 5ba0e67ce3562f3e8dceb05a55aa6c9e974e587b758cc77c523ad9303c571be4
test_continuation_integrity.py c161c0c64184badc0c7b7d4fd6c129a7f70f263c40b0a43e20f7355484a6a72b
run_context.py (unchanged)    bf196695bce1687f0bd9554d3a8615e9af5cdbfa1bedbc859cd199e8ff54f648
```

### 16.2 Actual structural ledger 与 source review

相对声明基线，`invocation.py` 为 `10 insertions / 11 deletions`，behavior test 为 `246` 行新增。一次性复核确认：顶层函数定义
`15 → 15`；四个 coordinate projection、一个 heterogeneous dispatch、四个 `set(...)` 与四个 `tuple(sorted(...))` 共 13 个旧
构造/分派点归零；新增仅一个 `itertools.pairwise` import 与四个 direct scans。shape → canonicality → content phase、四段 segment
顺序、错误 type/text/cause、complete/recovered admission、State/no-persistence 与既有 recovery boundary 均保持。

该 source review 是一次性交付证据，不构成 complexity 或 legacy/private-source-shape 永久门禁。

### 16.3 Verification record

实际工作树上第 13 节适用门禁全部通过：continuation integrity `41 passed`、execution `570 passed`、graph-state `206 passed`、9 个
active architecture/source/owner nodeids `9 passed`、完整套件（排除 complexity gate）`833 passed` 且 coverage `100.00%`；Pyright 无错误，
Ruff check/format、build、twine、`SKIP=kernel-complexity` scoped pre-commit、unstaged/staged diff checks 均通过。五项 inner-scalar
baseline-vs-target probe 的 exception type、text、cause 与既有 baseline 一致；七个新 public cases 均断言 direct `__cause__ is None` 和
mutation-free admission。

完整 `make check` 未运行，因为其无条件包含用户明确排除的 `complexity-ratchet`；不能将该命令或任何 complexity/legacy gate 结果写为
S16 交付条件。

### 16.4 交付与工作树状态

S16 implementation 现为 `IMPLEMENTED / VERIFIED`，production commit 为 `f9854e1`。生产/test、独立验收记录与本 writeback 是独立
change unit；本次只回写本文与新增验收记录，不 amend production，不累计 review/approval 历史 manifest。工作树中的其他用户既有修改保持不纳入 S16；没有执行 reset、stash、
删除用户文件或新增 State/Store/persistence/recovery path。
