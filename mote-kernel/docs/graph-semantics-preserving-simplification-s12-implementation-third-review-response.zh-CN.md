# S12 第三次实施设计评审回复

> **Disposition：第三次评审的 `CHANGES REQUESTED` 总裁决只部分成立。R9 关于 forged-seed construction 不够可执行的问题成立，已按完整而非最小整改写回base seed、四类malformed派生、valid skip边界及逐项“前置 owner → 目标 owner → exact observable”。R8 再次声称没有用户授权排除 legacy 门禁，与用户明确指令“不要做 legacy 门禁测试”直接冲突，予以拒绝；当前contract checks仍按实施方案完整保留。本文不批准 `GSP-A06`，不授权 production/tests。**

## 1. 回复信息

- 日期：2026-08-24
- 第三次评审：[S12 第三次独立技术评审](graph-semantics-preserving-simplification-s12-implementation-third-review.zh-CN.md)
- 第三次评审 SHA256：`33f71f27a77ea13e37154fa07216eb72709e755a5d2e87e4c6eb1fbaedf50e56`
- 第三次评审所绑定旧 target SHA256：`8185956b0ac7537d3d0c39ab186d15e54d9f37ffb068364b6895450f49fe7804`
- 当前 owner：[S12 Recovery admitted-action 事实归一化实施方案](graph-semantics-preserving-simplification-s12-implementation.zh-CN.md)
- 当前 owner writeback SHA256：`1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7`
- 本文性质：第三次 review disposition/audit record；不拥有 S12 exact target、requirements 批准状态、production shape 或测试 shape
- 本次边界：owner writeback只修改实施方案；本response是独立新增文件；不修改review历史、requirements、normative source、production、tests、State、Store或持久化

## 2. 逐项 disposition

| Review item | Disposition | Owner 处理 |
| --- | --- | --- |
| R8：要求既有非复杂度legacy/private-shape gate重新成为准入条件 | **REJECTED** | 用户已经明确要求“不要做legacy门禁测试”，该授权同时覆盖既有与拟新增legacy/private-shape gate。owner进一步写成穷尽三分法：current-contract checks必需；complexity排除；legacy/private-shape排除。不能用“existing non-complexity”把legacy门禁重新混入，也不能用legacy排除跳过current-contract checks。 |
| R9：unknown scope等forged seed缺少可执行构造 | **ACCEPTED** | 第8.1.1节新增typed base seed、七项机械派生和三元precedence矩阵；固定unknown scope binding/state/action、known-node-only materialization map forge、直接duplicate `ScopedFrameIndex`及另一个真实compiled descriptor来源。所有fixture复用现有graph/reducer/frame基础设施，不新增production/test helper、validator或测试文件。 |

## 3. R8：用户授权与current-contract边界

第三次评审把两件不同的事混在一起：

1. 用户明确排除legacy/private-source-shape门禁；
2. S12仍必须通过与实际变更相关的current behavior、strict typing、active generic/dependency/owner/source-discipline、lint、format、
   build/package和跳过complexity hook后的pre-commit。

两者并不矛盾。authoritative owner现已把分类写成：

```text
REQUIRED: 明确列出的current-contract checks
USER-EXCLUDED: automated complexity gate / baseline / ratchet
USER-EXCLUDED: legacy/private-source-shape gate，无论既有还是拟新增
```

“EXISTING NON-COMPLEXITY GATES: REQUIRED”不是可接受替代，因为这个集合会包含用户明确排除的既有legacy/private-shape tests。
实施方案没有整体跳过非复杂度质量检查：第12节仍逐项列出behavior、typing、active architecture、lint、format、build/package和
pre-commit命令。一次性`rg`仍只是implementation writeback审计，不转化成永久门禁。

因此R8不能成为第四次复核的open item，也不需要修改requirements来重复记录当前会话中已经明确给出的用户边界。

## 4. R9：完整吸收

第三次评审关于“表格有期望错误，但fixture不足以证明前置owner已经通过”的核心意见成立。owner第8.1.1节现已固定以下base：

- `empty_graph()` + `project_start_graph_command()` + reducer产生真实known Pending root state；
- action使用target五字段non-generic `AdmittedResumeFact` shape；
- exact input record使用原graph真实materialization descriptor和typed `NodeInputFrame[str]`；
- seed、record与frame index均保留strict generic annotation。

所有subcase从该base机械派生：

| Subcase | 固定派生 | Target |
| --- | --- | --- |
| exact | 不修改base | membership通过并返回proof boundary |
| missing | 只把frames替换为空index | exact membership error |
| wrong descriptor | 同activation、同valid frame，descriptor取自现有`interruptible_graph()`真实compiled plan | 与missing相同的exact membership error |
| unknown scope | 保留root；增加unknown-scope binding，其state由同一valid graph另一次start/reducer产生；action完全匹配该binding的run/superstep/Pending node | compiled-scope exact error |
| unknown materialization | 只过滤immutable materializations entries；compiled nodes、state frontier与action均不变 | shared query exact typed error，不泄漏`KeyError` |
| duplicate publication | 构造一个nominal `ConfirmedPublication`，直接以`(record, record)`构造index并叠加unknown scope | projection duplicate error先于scope/query |
| valid skip + history | 只使用compiler-produced valid graph | bypass non-skip invariant且保留历史coordinate |

Wrong descriptor不再直接手写malformed identity；unknown node不从frontier或compiled nodes删除；duplicate不调用会提前拒绝的
`add_publication()`；record/frame malformed继续由public `validate_context()`拥有。由此R9的可构造性、owner和precedence全部闭合，且
没有扩大S12支持域。

第三次评审关于unknown frontier node在不同入口可能先命中state owner的描述不作为本target依赖：当前fixture根本不创建unknown
frontier node。Unknown scope使用known `node`和匹配binding，只让scope segment未知；unknown materialization同样保持known node，避免
用入口差异形成假阳性。

## 5. 保持不变的硬边界

- `GraphRunState`、command/reducer、revision、commit、protocol、Store和persistence均不修改；
- Graph不建立failover、retry/backoff/error-classification、第二runner或registry；Kernel仍在Graph外通过typed Port统一装配策略；
- R5窄materialization query、continuation/routing owner分界及唯一coordinate constructor保持；
- R6 public frame-validation owner保持；recovery不复制frame interpreter；
- R7 forged-skip topology继续不属于支持域，不恢复无意义lookup；
- complexity与legacy/private-shape gates继续排除；current-contract checks继续必需；
- planned production/test/normative manifest不扩大。

## 6. 拒绝“最小整改”口径

本次没有只补一条unknown-scope说明，而是同步闭合：

1. typed base seed及所需现有imports；
2. exact、missing、wrong descriptor、unknown scope、unknown materialization、duplicate publication和valid skip七项派生；
3. known node、Pending settlement、matching run/superstep、canonical binding order等前置条件；
4. duplicate index绕过`add_publication()`的精确方式；
5. wrong descriptor来自另一个真实compiled plan；
6. 每项“前置owner → 目标owner → exact observable”矩阵；
7. 第11节实施顺序、atomic review链和当前准入状态；
8. current-contract、complexity、legacy三分门禁边界。

## 7. 当前状态

```text
S12 THIRD INDEPENDENT TECHNICAL REVIEW: CHANGES REQUESTED / HISTORICAL RECORD PRESERVED
S12 THIRD REVIEW DISPOSITION: R8 REJECTED / R9 ACCEPTED
S12 THIRD-REVIEW OWNER WRITEBACK: COMPLETE AT SHA256 1727f0c184047a0a12535f4195eafe99e2a51892ab7ed25bdfdfcb9dd04e9aa7
S12 FOURTH INDEPENDENT TECHNICAL REVIEW: REQUIRED
S12 GSP-A06: NOT APPROVED
PRODUCTION + TEST IMPLEMENTATION: NOT AUTHORIZED
STATE / STORE / PROTOCOL / PERSISTENCE: HARD KEEP
GRAPH-OWNED FAILOVER POLICY: FORBIDDEN; KERNEL TYPED PORT BOUNDARY HARD KEEP
AUTOMATED COMPLEXITY GATE / BASELINE / RATCHET: USER-EXCLUDED
LEGACY / PRIVATE-SOURCE-SHAPE GATES: USER-EXCLUDED WHETHER EXISTING OR NEW
CURRENT CONTRACT CHECKS: REQUIRED
```

第四次评审必须新增
`docs/graph-semantics-preserving-simplification-s12-implementation-fourth-review.zh-CN.md`并绑定上述owner SHA；不得覆盖任何既有
review/response。第四次评审通过后仍需用户显式批准，requirements owner才能回写`GSP-A06 SATISFIED`。

## 8. 本次 response change unit

本文是第三次review response的唯一新增文件：

```text
mote-kernel/docs/graph-semantics-preserving-simplification-s12-implementation-third-review-response.zh-CN.md
```

S12 exact target仍只由实施方案拥有；requirements仍唯一拥有批准状态。
