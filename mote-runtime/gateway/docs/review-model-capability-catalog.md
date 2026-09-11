# 模型能力目录代码评审指南

这份文档给 reviewer 使用。它不是设计说明的替代品，也不是“测试通过就算
质量合格”的证明；目标是帮助评审者先判断设计是否足够简单、清晰、唯一，再用
测试和门禁确认实现没有回归。

## 评审范围

本次变更只实现模型层，不实现具体上游网络调用。重点文件是：

| 文件 | 评审重点 |
| --- | --- |
| [`src/internal/model/definition.go`](../src/internal/model/definition.go) | 模型定义、配置校验、Kernel override 的一次性合并、不可变性 |
| [`src/internal/model/capability.go`](../src/internal/model/capability.go) | operation 能力、默认参数、参数过滤、已知边界 clamp、Embedding 维度 |
| [`src/internal/model/catalog.go`](../src/internal/model/catalog.go) | 内置目录加载、来源校验、精确 ID 查找、typed error |
| [`src/api/operation.go`](../src/api/operation.go) | 中立的 operation/modality/mode/feature 词汇 |
| [`src/cmd/update-model-catalog`](../src/cmd/update-model-catalog) | 从 new-api/Bifrost 快照生成纯模型目录，并复用 runtime 的 typed operation-shape owner |
| `src/internal/model/catalog_data.json.gz` | 生成后的模型事实快照，不应被手工编辑 |
| `src/internal/model/catalog_rejections.json` | 同一输入快照中被 fail-closed 拒绝的模型集合，不是第二份能力目录 |

本目录采用“真实模型能力优先”的语义：目录记录来源能够证明的完整模型能力，
不因为当前 `gateway_invocation` v1 的请求 DTO 或某个调用 profile 暂时较窄就删掉
能力。v1 的 admission/调用入口负责拦截尚未发布的请求形状；目录不能把“当前入口
不会调用”改写成“模型本身不会”。

明确不在本次范围内：

- 服务商选择、协议选择、路由、fallback、重试和 agent 流程；
- endpoint、credential、云签名、连接池和具体网络请求；
- 模型价格、按价格计算金额、用户账单或 billing ledger；
- 带 `Chat()`/`Embed()` 网络行为的 `BaseLLM`；
- Embedding 的跨语言 invocation DTO。当前只把 Embedding 建模为内部
  capability，公开 DTO 必须先有独立的 conformance 契约。

如果评审意见要求把上述内容加入本次变更，应先重新确认 owner、生命周期、失败语义
和契约版本，而不是在现有文件中顺手扩展第二条路径。

## 先看唯一 owner

评审时可以用下面的表逐项定位规则。一个规则如果在两个地方都执行，通常就是需要
要求修改的信号。

| 规则/事实 | 唯一 owner | 评审应看到的证据 |
| --- | --- | --- |
| 模型 ID 的存在与精确查找 | `Catalog` | `Lookup` 只按完整字符串查 map，不 trim、alias、prefix、fallback |
| 跨来源模型身份归一化 | `compileModels` 的 `identityIndex` | 只在构建期去掉明确的服务包装（包括 `openai/`）；模型命名空间如 `mistral/...`、`cohere/...` 保留；runtime 不再做别名查找 |
| 默认配置与 Kernel 配置的合并 | `newCatalog` / `applyOverride` | 构造时合并一次；调用期间不再次 patch |
| 配置合法性和集合规范化 | `normalizeConfig` / `normalizeOperationConfig` | enum、重复 operation、重复集合、边界和 operation 形状集中校验 |
| 生成参数支持状态、默认值和边界 | 选中 operation 的 `GenerationPolicy` | `Capability.ResolveGenerationParameters` 只有一条解析路径 |
| structured-output 模型兼容性证据 | 选中 operation 的 `Features` | `supports_response_schema` / `supports_native_structured_output` 只投影为中立的 `structured_output` 证据；协议和服务准入仍必须再次通过 |
| Embedding 维度 | `EmbeddingPolicy` | fixed 与 adjustable 互斥；默认值和边界不复制到别处 |
| operation shape（mode/modality/policy 组合） | `src/internal/model/shape.go` 的 `ValidateOperationShape` | 生成器和 runtime 复用同一个 typed 校验，不各自维护规则表 |
| 来源抽取和快照生成 | `src/cmd/update-model-catalog` | 输入 revision/hash 只记录在 catalog；输出不携带 service/protocol/pricing 字段；rejection 写入机器可读 sidecar |
| 服务、协议、凭据、价格 | 各自的 service/protocol/receipt owner | `internal/model` 和 catalog 中不存在这些执行事实 |

### 组合调用链

评审应能从代码直接读出这一条单向链路：

```text
new-api tree + Bifrost parameter snapshot
        │
        ▼
src/cmd/update-model-catalog
        │  deterministic sort + provenance
        ▼
catalog_data.json.gz
        │
        ▼
NewCatalog(defaults, Kernel overrides)
        │  normalize + validate + deep copy
        ▼
immutable Definition
        │
        ▼
Capability(operation)
        │  model default → explicit request → known-bound clamp/filter
        ▼
Protocol adapter encodes the resolved neutral request
```

代码中不应出现从 protocol/service 反向修改 `Definition`、在请求失败后重新查找模型，
或绕过 `Capability` 另写一套参数过滤逻辑。

## 不变量与正确性检查

### Definition 和 Catalog

- `Definition` 建立后，调用方修改构造输入的 slice、pointer 或 stop 列表，不得改变
  已建立的定义。
- 每个模型的同一 operation 唯一；一个真实模型可以同时发布多个独立 operation，集合
  排序且不含重复项；所有枚举值都来自 `api` 的中立词汇。
- `Lookup("model ")` 不得命中 `Lookup("model")`；不允许 alias、大小写折叠、
  家族推断或隐式 fallback。
- 已有模型的 `nil` override 字段继承默认值；非 nil 的 operation 集合整体替换，
  不能同时存在“逐项 patch”和“整体替换”两种语义。
- 未知模型只有在 override 提供完整、可校验的 operation 集合时才可加入目录。
- 生命周期、token limit、默认值和 numeric bounds 在构造时失败即返回 typed error，
  不能为了“尽量可用”在运行时偷偷修正。生成阶段以模型为发布单元：非法记录不会
  进入目录，并输出确定性的 rejection；一条坏的服务别名不会阻断其余独立模型。

### Generation 参数

- 解析顺序必须是：模型默认值 → Kernel 显式值；显式值存在时覆盖默认值，显式空
  列表可以清空默认 stop。
- Gateway 运行时的 `max_output_tokens` 目标默认值统一为 4096；如果模型已知最大输出小于
  4096，`Capability` 解析出的有效值会夹到该模型上限。4096 不写入目录，因为它是
  Gateway 策略而不是 provider/model 事实。
- 模型明确不支持的、但已经存在于中立 DTO 的可选字段，结果中应为 nil/缺省，
  不得传给 protocol adapter。
- 已知 minimum/maximum 时，低于 minimum 映射到 minimum，高于 maximum 映射到
  maximum；边界未知时原值保留，不能凭经验发明边界。
- 默认值越界、minimum 大于 maximum、非有限浮点值必须在目录构造时拒绝。
- tool call、structured output 等改变请求语义的 required feature 不是普通可选
  参数，不能静默删除；不支持时应由 Admission 拒绝。
- 解析结果不得继续引用调用方传入的可变 pointer 或 slice。

### Embedding

- Embedding 是独立 operation，不得继承 generation policy。
- 输出 modality 只能是 `embedding`；输入 modality 可以按模型事实声明 text、
  image、audio、video。
- fixed width 时，用户传入 `dimensions` 必须被过滤，固定宽度由单一字段记录。
- adjustable width 时，默认值、minimum、maximum 只由 `Dimensions` 持有，并按已知
  边界 clamp；fixed 和 adjustable 同时出现必须拒绝。
- 模型目录中有 Embedding capability，不代表当前 `gateway_invocation` v1 已经
  发布 Embedding wire DTO；reviewer 不应要求为了“看起来完整”而绕过 conformance。

## 生成器和目录数据的重点风险

生成器必须保持可重现、可解释，而不是把上游资料原样搬进 Gateway。请重点检查：

1. `compileModels` 是否先形成一个稳定的 canonical model identity，再按 semantic
   operation 分桶；同一模型的不同 operation 必须并存，不能互相覆盖。精确 canonical
   记录拥有 token、参数、维度和生命周期；明确的 `:batch` 变体只补充同 operation
   的交付模式，冲突 batch 只拒绝该变体。模态 supplemental fact 不得扩大主记录；主
   记录缺失时只能确定性采用一致的观察，冲突记录写入 rejection manifest，不能静默
   丢弃或 union 出来源未声明的能力。
2. `sortRecords` 的优先级是否稳定；同一输入重复生成时，输出顺序和字段应一致。
3. `inferOperation`、图像/视频/Embedding 名称判定只是完全没有来源记录时的保守
   补足；有精确来源记录时，显式 `mode` 直接拥有 operation，模型名称不能覆盖它；
   `mode` 缺失或未知时必须 rejection，不能回退到名称推断。新增启发式必须有针对性
   测试，不能用一个名称规则覆盖整个服务商。
4. batch-only 记录是否只暴露 `async`；普通模型有 batch 能力时是否保留 `unary`
   并额外声明 `async`；`:batch` 行在 canonical 分组后只能补充同 operation，不能成为
   另一个模型 ID，也不能把不同 operation 的主模型拒绝掉。
5. `compileTokenLimits`、参数默认值和维度信息是否来自模型事实，而不是价格行、
   provider 字段或 protocol endpoint。`model_parameters` 中的 output-token UI 默认值
   不被消费；Gateway 的 4096 运行时策略是唯一默认 owner，只采纳来源声明的边界。
6. 生成的 gzip 文件是否经过 `gzip -t`，来源 revision/hash 是否与输入一致，且目录
   不含 `provider`、`protocol`、`endpoint`、`credential`、`price`、`pricing`、
   `family` 等越权字段。

固定 revision/hash 的当前快照发布 9120 个 canonical 模型；其中 25 个模型有多个
独立 semantic operation。下面的 operation 数量用于人工核对；它们不是可替代模型
事实的第二份能力状态：

| operation | 数量 |
| --- | ---: |
| `generate` | 8510 |
| `embedding` | 159 |
| `rerank` | 31 |
| `image_generation` | 250 |
| `audio_generation` | 40 |
| `audio_transcription` | 72 |
| `music_generation` | 0 |
| `video_generation` | 72 |
| `realtime` | 11 |

原始快照中的 `:batch` 请求变体只补充基础模型的 `async` 能力，不发布为模型。当前
rejection manifest 有 87 条：15 条是顶层 output maximum 与参数 range maximum 冲突，
4 条是非法 output-token range（minimum 大于 maximum），8 条是来源声明了 Gateway
未知的 `code` modality，16 条是 Gateway 不拥有的 `search`/`ocr`/`moderation`/`3d`
等未知 operation，另有 22 条 supplemental modality 冲突；这些记录都不会静默消失，
完整集合写入 `src/internal/model/catalog_rejections.json`。无 authoritative source
的模型才允许按明确的名称规则推断 operation；有 source row 但 mode 缺失或未知时
直接 rejection。`fallback_generalizations` 是 Bifrost 的规则元数据，不是模型记录，
按 source adapter 规则排除，不计入模型或 rejection。
数量变化本身不一定是缺陷，但必须能由来源 revision、生成器规则或明确的模型事实
解释；不能只改测试期望值。

## 代码质量问题的高信号问法

评审不要只看函数是否短，可以直接问：

- 读者能否从类型和调用顺序看出谁拥有这条规则？
- 是否为了通过复杂度门禁增加了薄 wrapper、转发 helper 或第二份状态？
- 是否有任何 `map[string]any`、反射或未声明的字段通道绕过 typed boundary？
- 是否有 public field、wire tag 或 durable ID 没有 owner、生命周期和第一消费者？
- 是否把“服务商暂时不支持”误写成“模型本身不支持”，或者反过来？
- 失败时是否返回边界 owner 的 typed error，且不泄漏 credential、签名 header、完整
  payload 或 cache key？
- 调用方是否能在构造后继续改变目录状态？是否存在隐式全局缓存或刷新线程？
- 新增测试是在验证行为不变量，还是仅仅把当前实现的数字复制成断言？
- 删除旧 alias、wrapper、兼容分支后，是否仍残留另一条执行路径？

## 已知限制（不应被误报为本次回归）

- 当前 `api.GenerationParameters` 只有 conformance v1 已接受的中立字段。Bifrost
  中 protocol-specific 的字段不是通过无界 map 偷渡进来；需要新契约时另行设计。
- Embedding 目前是内部模型能力和目录数据；Embedding invocation、请求/结果 DTO
  及 conformance vector 尚未发布。
- 项目尚未实现具体 protocol adapter、service connector 和真实上游网络调用；本次
  变更不能以“还不能调用 API”作为模型层缺陷，也不能为此提前混入网络逻辑。
- 平台把 Gateway 根目录的 `.git` 挂载为只读 tmpfs，直接执行结构检查可能报告
  `nested Git repository found under gateway`。这是环境问题；在排除该挂载点的临时
  副本上执行同一结构脚本应通过。不要为规避挂载而放宽生产结构检查。

## 可复现的验证清单

在评审分支执行以下检查，并把失败命令和原因写进评审结论：

```bash
pre-commit run --all-files
make test-unit
make architecture
make complexity
make fmt-check
make vet
make lint
make module-hygiene
make license-check
make secret-scan
make test-integration
make coverage
make build
make vulncheck
```

还应检查：

```bash
go -C src test ./...
go -C src vet ./cmd/update-model-catalog
git diff --check
```

门禁结果只能证明没有发现已编码的回归，不能替代对 owner、调用链和数据来源的设计
判断。特别是 complexity baseline 的变更，必须能在 review 中解释为什么是一次有意的
模型目录实现，而不是为了让指标通过而拆碎代码。

## Reviewer 结论模板

```text
结论：Approve / Request changes / Block

设计：唯一 owner 是否清晰；是否出现第二执行路径？
边界：是否有 service/protocol/routing/pricing 越权？
正确性：override、过滤、clamp、Embedding 维度不变量是否成立？
数据：目录来源、分类、快照可重现性是否有证据？
安全：是否泄漏凭据、签名、完整 payload 或 cache key？
门禁：通过了哪些命令；未通过的命令是否为已记录环境限制？
必须修改：...
可后续处理：...
```

只有在设计本身清晰且唯一、关键不变量有测试、所有适用门禁通过，并且剩余限制已被
明确记录时，才建议批准。

本次复杂度基线记录为 163 个 decision points / 52 个函数：新增的
`ValidateOperationShape` 是 runtime 与 build-time compiler 共用的唯一 typed shape
owner；它替代了两套分散规则，因此增加的是一份真实规则，而不是 wrapper 或兼容路径。
`cmd/update-model-catalog` 是构建期 compiler，虽位于同一 Go module 以便导入该 owner，
但不属于部署后的 runtime call path，runtime complexity ratchet 明确不把它混入；它由
自己的 deterministic unit tests 和生成产物校验保护。
