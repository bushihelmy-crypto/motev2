# `skip_failed` 可选替代输出实施设计评审

## 1. 评审结论

- 评审对象：`docs/skip-failed-output-implementation.zh-CN.md`
- 需求基线：`docs/skip-failed-output-requirements.zh-CN.md`
- 结论：**方向通过，但当前版本不批准开始 production 编码。**
- 阻塞项：2 个 P1、3 个 P2。

设计已经正确确定单一公共 `skip_failed()`、单一 `SkippedGraphNode`、单一 publication store、closed nominal provenance、泛型 request、首个 commit 前 duplicate admission，以及 per-scope durable-first 顺序。它没有引入第二 runner、第二取值路径或 concrete State mirror，整体符合零负债和唯一真相方向。

开始编码前必须关闭第 3 节的两个 P1，并把第 4 节的实施边界写成可验证的明确设计。

## 2. 已通过项

### 2.1 用户语义与 State

设计明确 State 不区分 pure skip 和 substitution skip，唯一 durable settlement 仍是 `SkippedGraphNode`。用户只看到同一个 `Graph.skip_failed(..., output=None)`，没有第二 public action、settlement、result 或 lookup path。

该决定比需求允许的“必要时内部区分”更严格，且符合当前基础设施：output 是否存在是 publication availability 事实，不需要 durable State bit。

### 2.2 唯一 concrete truth

replacement frame 与 provenance 只进入 `ConfirmedPublication`，并继续由 `ScopedFrameIndex.publications` 持有。candidate overlay 仅用于 invocation-local planning，不进入 continuation 或 State。只要按第 4.2 节收紧 read protocol，它不是第二事实源。

### 2.3 泛型与公共边界

`SkipFailedNodeRequest[GraphValueT]`、`PreparedResume[GraphValueT]`、candidate 和 provenance 设计保持同一个 graph universe，没有使用 `Any`、`object`、bare container、字符串 discriminator 或 generic-erasing cast。

public namespace仍只暴露 `Graph` facade；provenance、candidate 和 request 保持 owner-internal。negative typing fixture 覆盖 cross-universe 是正确门禁。

### 2.4 Admission 与 durable-first

设计正确要求：

- 全 invocation candidate 与既有 publication、candidate 彼此的冲突都在首个 commit 前检查；
- 正常 collision 抛 `Graph.ValuePublicationError` 且 commit 次数为 0；
- commit 后 `add_publication()` 仅是 defensive invariant gate；
- exact commit 后先替换 memory State，再安装已 admitted frame；
- 不伪造跨 scope transaction，不回滚已确认 scope。

### 2.5 基础设施复用

设计复用 compiled output descriptor、`execution.graph.values` admission、`PublicationAvailabilityCoordinate`、`StableActivation`、`ScopedFrameIndex`、`commit_transition()`、existing materialization 和 recovery worklist。新增 `resume_admission.py` 是窄 engine owner，不是 generic `utils/shared/helpers`。

## 3. P1 阻塞项

### P1-1：shared resume proof 没有覆盖所有 continuation invocation

实施文档第 6.4、7.1 节把 whole-future recovery proof限定为 `recovered invocation`：

```text
shared routing/input proof
recovered invocation 再做 whole-future recovery proof
```

当前 facade 中 `context.recovered` 只表示没有 continuation 的 state-only recovery；携带合法 continuation 的 resume 不运行 `preflight_recovery()`。因此，普通 continuation 上执行 pure skip 时，当前方案只描述了一次 current-frontier routing/input proof，无法证明未来 graph output、nested boundary 和后续必达路径不需要该 publication。

这违反需求的以下硬约束：

- 未提供 output 时，提交前证明所选 continuation 不需要该 output；
- proof 至少覆盖 graph outputs、nested boundary 及同 invocation action 影响；
- continuation resume 与 state-only recovery 共用 proof owner；
- 任一非法 proof 必须在首个 commit 前失败。

必须修订为：**凡本 invocation 包含 pure skip，都在首个 commit 前执行 shared future-path proof，不以 `context.recovered` 为条件。** state-only recovery 可以在相同 owner 上增加无 continuation history 的 fail-closed 约束，但不能成为唯一运行 future proof 的路径。

若设计认为 current-frontier resolver 已足以证明所有未来路径，必须给出形式化不变量和 nested output/graph-output 证明；当前文档没有该证明。

必须增加验收：

1. continuation + pure skip，当前 frontier 可推进但未来 graph output 缺该 publication，零 commit；
2. continuation + pure skip，当前 frontier 可推进但未来 nested boundary 缺该 publication，零 commit；
3. continuation + substitution skip 的 candidate frame 参与同一 future proof 并通过；
4. state-only 与 continuation 对相同可证明路径得到相同 target/availability 结论。

### P1-2：candidate 不是需求要求的最终 publication candidate

需求要求 planning 阶段构造 candidate frame、coordinate 和 provenance candidate，并在安装前证明“待安装 record 与 planning 时已经 admitted 的 candidate exact equal”。

当前 `AdmittedSubstitution` 只有：

```python
coordinate
frame
```

而 `SkipSubstitutionProvenance()` 及 `acknowledged_revision` 在 commit 后才临时构造。这样 pre-commit admission 的对象与 post-commit 安装的 `ConfirmedPublication` 不是同一个 closed candidate shape，无法落实文档自己第 7.2 节所要求的 exact plan/install invariant，也没有明确 evidence把 candidate绑定到 expected successor revision。

不能在 planning 阶段伪造 confirmed revision，但可以并且必须预构造完整的安装计划。例如使用窄 typed model：

```python
@dataclass(frozen=True, slots=True)
class AdmittedSubstitution(Generic[GraphValueT]):
    coordinate: PublicationAvailabilityCoordinate[GraphValueT]
    frame: NodeOutputFrame[GraphValueT]
    provenance: SkipSubstitutionProvenance
    expected_revision: int
```

其中 `expected_revision` 来自 pure reducer 模拟的 exact successor。commit 后只允许验证 confirmed State exact equal、revision等于 expected revision，并将该 admitted evidence提升为 `ConfirmedPublication`；不得重新决定 provenance、coordinate、frame 或 revision。具体 shape可调整，但必须满足：

1. provenance candidate在 planning 构造并参与 nominal admission；
2. candidate绑定 exact expected successor/revision；
3. post-commit conversion是唯一、机械、可比较的提升；
4. 安装 record 的 coordinate、frame、provenance、revision全部能与 admitted plan逐项 exact 验证；
5. candidate仍不进入 State或 continuation。

## 4. P2 必须收紧项

### P2-1：routing shared resolver 的 API 和唯一 owner 仍不够具体

文档说“从 routing.py 抽取 shared resolver”，同时又新增 `resume_admission.py` 并让 recovery worklist消费它，但没有给出 resolver 的 typed input/output、谁负责 join arrivals、谁负责 candidate data trigger、谁负责 graph completion，以及 recovery如何调用同一 owner。

这留下实现时产生三套近似逻辑的空间：`plan_routing()`、resume admission、recovery transfer。实施前应固定一个最小 pure resolver契约，至少返回：

- selected control targets；
- completed join targets与remaining join progress；
- selected data targets，来源基于 publication availability而非 settlement kind；
- 每个必达 target 的完整 input availability；
- completion output availability。

runtime、resume admission和recovery可以投影不同 disposition/error，但拓扑和availability事实必须来自该唯一函数族。architecture test应禁止 recovery重新扫描 `transition.data_triggers`、direct/conditional targets或joins。

### P2-2：candidate overlay 的 read contract 必须在设计阶段确定

当前文档只说“若 proof materialization需要读取 candidate frame，应抽取窄 protocol”。这里不能留到编码时再决定。future proof和nested/graph-output验证是否只需要 `has_*`，还是需要读取frame值，直接决定 overlay是不是第二 lookup路径。

实施设计必须二选一并固定：

- proof只验证 availability：`CandidateFrameAvailability` 仅实现现有 `ScopedFrameAvailability`，不提供 lookup；或
- proof确实需要frame：将现有 materialization参数泛化为一个统一、窄、typed `ScopedFrameReader`，由 confirmed index与candidate overlay实现同一个 `lookup()`；不得增加 substitution-specific lookup。

推荐优先保持 proof只依赖 coordinate availability；真正执行/materialize仍只读取 confirmed `ScopedFrameIndex`。

### P2-3：post-commit invariant failure 的具体类型与原子安装边界不明确

文档允许“缺少时增加窄 internal exception”，但没有指定现有 `add_publication()` 如何避免继续抛公共 `GraphValuePublicationError`。同时 State 已替换、resume inputs已安装后，publication defensive gate才失败，会形成 invocation-local部分更新。durable State不能回滚是正确的，但内存安装顺序和错误类型必须明确。

应在实施前固定：

1. pre-commit admission 使用公共 `GraphValuePublicationError`；
2. post-commit installation 使用只接受 admitted plan 的内部入口，collision转换为明确的 owner-internal invariant error；
3. 对同一 scope 的 resume inputs与substitution publications先构造新的 immutable `ScopedFrameIndex`，全部成功后一次替换 `context.frames`，避免内存 frame segments半安装；
4. State仍先于frame snapshot替换，符合 durable-first；若frame invariant失败，传播internal failure且不做补偿。

## 5. 零负债、唯一真相与门禁判断

| 维度 | 当前判断 | 说明 |
| --- | --- | --- |
| 用户只见一种 skip | 通过 | 单一 API、action、settlement和routing语义 |
| State 单一事实 | 通过 | 不增加marker或concrete value |
| concrete publication唯一真相 | 条件通过 | store唯一；需关闭P1-2和P2-2 |
| execution engine唯一 | 通过 | 沿用Graph facade与execution engine |
| routing/data-flow唯一真相 | 未通过 | shared resolver契约尚未闭合，见P1-1/P2-1 |
| 泛型约束 | 通过 | 全链路保留GraphValueT及negative fixtures |
| admission atomicity | 条件通过 | duplicate已覆盖；future proof与安装边界仍需修订 |
| recovery一致性 | 未通过 | continuation invocation未明确运行future proof |
| 零兼容债务 | 通过 | 无alias、旧路径或dual representation迁移 |
| 门禁完整性 | 条件通过 | make check/pre-commit正确；需补本评审验收 |

## 6. 修订后必须具备的测试与架构门禁

除实施文档现有矩阵外，至少增加：

1. continuation pure skip 的future graph-output与nested-boundary零commit测试；
2. candidate provenance与expected successor revision在首个commit前构造的白盒测试；
3. confirmed publication是admitted candidate机械提升、字段逐项exact的测试；
4. 同一scope多个frame record post-commit以一个immutable frame snapshot安装的测试；
5. post-commit defensive collision抛internal invariant error，绝不映射为`Graph.ValuePublicationError`；
6. architecture test确保runtime/resume/recovery只有一个target/data-contribution owner；
7. architecture test确保candidate overlay没有substitution-only lookup或第二frame map；
8. negative typing fixture覆盖`SkipFailedNodeRequest[UniverseA]`不能进入`Graph[UniverseB]`；
9. `make check`、100% branch coverage与monorepo root `pre-commit run --all-files`。

## 7. 准入结论

当前实施设计已经选对核心架构：State不区分两类skip，publication是数据唯一真相，公共面与execution engine不分叉，类型关系可保持严格泛型。

但 P1-1 会让带continuation的pure skip绕过需求要求的future-path证明，P1-2则未完成pre-commit publication candidate到post-commit confirmed record的exact证据闭环。这两个问题都可能造成durable skip已提交后才发现缺值或安装事实无法由admitted plan唯一推出，属于编码前阻塞项。

关闭两个P1、明确三个P2的typed owner/API和新增验收后，可再次评审并批准production编码。
