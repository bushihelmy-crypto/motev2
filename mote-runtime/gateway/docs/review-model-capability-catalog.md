# 模型层代码评审指南

这份指南用于评审 Gateway 的模型层。评审顺序是：先看 owner 和调用链是否
唯一、简单，再用测试和门禁确认没有回归。门禁通过不能替代设计判断。

## 本次边界

Gateway 的模型层只描述模型本身：

- `BaseModel` 是唯一模型身份，必须是完整、裸的名称（不含 `/`）。
- `CapabilityConfig` 描述一个模型的语义 operation、输入/输出 modality、
  模型原生 feature，以及模型支持的 generation/embedding 参数。
- `TokenLimits` 描述模型明确声明的上下限；未知就是未知。
- `Lifecycle` 是模型目录状态。

模型层不保存服务商、协议、endpoint、credential、routing、fallback、价格、
账单、用户或 agent 流程。delivery mode、batch、streaming、usage 的执行事实
由协议/服务/调用路径 owner 处理。`structured_output` 在这里最多表示模型兼容
性证据；是否能通过某个接口产生严格 JSON，仍由选定的协议和服务能力共同决定。

当前目录是一个静态初始 seed：[`catalog_data.json.gz`](../src/internal/model/catalog_data.json.gz)。
它不再从外部项目或运行时导入器生成。未来 CRUD 建立后，CRUD 会成为唯一写入
入口；在此之前，Kernel 只能通过 `Override` 一次性覆盖或增加完整模型定义。

## 文件职责

| 文件 | 唯一职责 |
| --- | --- |
| [`definition.go`](../src/internal/model/definition.go) | `Config`/`Override`/`Definition` 类型、身份和配置校验、一次性 override 合并 |
| [`capability.go`](../src/internal/model/capability.go) | 参数支持状态、默认值、已知边界 clamp、stop 和 embedding 维度解析 |
| [`shape.go`](../src/internal/model/shape.go) | operation 与输入/输出 modality、generation/embedding policy 的唯一 shape 规则 |
| [`catalog.go`](../src/internal/model/catalog.go) | 静态 seed 严格解码、不可变目录构造、精确 `BaseModel` 查找 |
| `catalog_data.json.gz` | 当前发布的模型事实 seed，不是第二份运行时状态 |

调用链只有一条：

```text
静态 seed + Kernel Override
        -> NewCatalog / applyOverride
        -> normalizeConfig（含 shape 校验和深拷贝）
        -> immutable Definition
        -> Capability(operation)
        -> ResolveGenerationParameters / ResolveEmbeddingDimensions
```

不应出现 alias、prefix 猜测、fallback 查找、请求期间再次 patch，或另一份参数
过滤实现。

## 必查不变量

### 身份和目录

- `Lookup` 只按完整 `BaseModel` map key 查找，不 trim、不改大小写、不去前缀。
- 空名称、带 `/`、两端有空白的名称拒绝。
- 每个 `BaseModel` 只有一个 capability；重复模型拒绝。
- 未知模型只有在 override 提供完整且可验证的 capability 时才能加入。
- 构造完成后，调用方修改输入 slice、pointer 或 stop 列表不能改变目录。
- seed 使用严格 JSON 解码：未知字段、空模型、错误 schema、多个 JSON 文档都
  必须失败。

### 能力和参数

- 输入/输出 modality 是模型事实，不能为了当前某个调用 profile 擅自缩窄。
- operation shape 由 `shape.go` 唯一校验；embedding 输出只能是 `embedding`，
  且不能把 embedding 当输入。
- 模型不支持的可选参数解析为缺省；不能传给协议适配器。
- 解析顺序是模型默认值，再覆盖 Kernel 显式值。
- `max_output_tokens` 的 Gateway 默认是 4096；它不写入模型 seed。已知模型
  上下限只负责 clamp，未知边界不能凭经验发明。
- minimum 大于 maximum、越界默认值、非有限浮点数、fixed 与 adjustable
  embedding dimensions 同时存在，都必须在构造时拒绝。
- structured output 和 tool calls 是模型兼容性证据，不是服务/协议能力的替代品。

### Embedding

Embedding 是独立 operation，不继承 generation policy。模型可以声明 text、
image、audio 或 video 输入；输出只能是 embedding。固定维度过滤用户的
`dimensions`，可调维度只由一个 `NumericParameter` 持有默认值和已知边界。

## 评审问题

- 规则是否只有一个 owner？
- 是否新增了别名、wrapper、兼容分支或隐式全局状态？
- 模型层是否出现 service/protocol/routing/pricing 字段？
- 是否把“当前接口不支持”误写成“模型不支持”？
- 未知事实是否被静默补全、清空或 clamp？
- 失败是否在正确的边界返回 typed error？
- 新测试验证的是不变量，还是只复制当前目录数量？

## 验证

```bash
go -C src test ./...
go -C src vet ./...
make architecture
make complexity
make fmt-check
make lint
make test-integration
git diff --check
```

Embedding 的公开 invocation DTO 尚未发布；不要为了让目录看起来完整而绕过
conformance 契约增加第二条 wire 路径。
