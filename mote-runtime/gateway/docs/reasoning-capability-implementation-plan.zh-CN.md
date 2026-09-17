# Reasoning / Thinking 能力实施方案

状态：Gateway 侧 groundwork 已完成，尚未宣称投产。本文落实了中立 DTO、模型能力、
admission 和 immutable admitted-request seam；inbound invocation owner 与具体 protocol/service
adapter 尚未在本仓库接入生产部署，不能把测试 runner 的行为当成线上行为。

## 先说结论

`operation` 仍然只回答“这次调用做什么”（`generate`、`realtime` 等），不承载
thinking 配置。

请求只增加一个 LLM 专属的中立配置：

```json
{
  "reasoning": {
    "thinking": "adaptive",
    "effort": "high"
  }
}
```

字段含义：

| 字段 | 允许值 | 人话含义 |
| --- | --- | --- |
| `thinking` | `disabled` / `enabled` / `adaptive` | 关闭思考、开启固定模式、让模型自适应思考深度 |
| `effort` | `minimal` / `low` / `medium` / `high` / `xhigh` / `max` | 希望模型投入多少推理力度；不是 token 预算 |

`reasoning`、`thinking`、`effort` 都可以省略：Gateway 只使用模型目录明确登记的默认值；
没有登记时保持省略，把最终默认交给上游接口。
`disabled` 现在就支持，不留到以后。公共层不提供 `budget_tokens`，也不提供
厂商原样的 `reasoning_effort`、`thinkingBudget` 或 `includeThoughts`。

`adaptive` 和 `effort` 不冲突：前者说“深度由模型动态调整”，后者说“调整时希望偏向
哪一档”。因此 `adaptive + high` 是合法组合，但 `high` 不是硬上限，也不是 token
预算；某个模型如果不能同时表达这两个意图，就由能力检查拒绝该组合。

公共 `effort` 不再另设 `none`：关闭思考只有一个表达，即
`thinking: "disabled"`。例如 OpenAI 的 `none` 由适配器内部映射，不让用户有两种
表达同一件事的方式。

## 1. 请求层怎么放

只放在 `src/api.LLMInput`，不放在 `LLMRequest.Operation`，也不放在 LLM/媒体共用的
`GenerationParameters`：

```text
LLMInput
├── messages / tools / response_format
├── reasoning?       ← 新增，LLM 专属
└── parameters       ← temperature、top_p、max_output_tokens 等通用生成参数
```

实际采用的 Go 类型如下：

```go
type ThinkingMode string // disabled, enabled, adaptive
type ReasoningEffort string // minimal, low, medium, high, xhigh, max

type ReasoningConfig struct {
    Thinking ThinkingMode    `json:"thinking,omitempty"`
    Effort   ReasoningEffort `json:"effort,omitempty"`
}
```

`LLMInput.Reasoning` 使用指针，区分“没有用户偏好”和“明确要求
`thinking=disabled`”。媒体请求不接收这个字段；实时请求是否可用由模型、协议、服务
三者的能力交集决定，不能一概放行。

## 2. 模型目录记录什么

模型目录记录抽象能力，不记录供应商字段、endpoint 或协议 JSON。建议采用“按
thinking 模式列出 effort”的结构，而不是两个平行数组，这样不会误放行无效组合：

```json
"reasoning": {
  "thinking_modes": [
    {"thinking": "disabled"},
    {
      "thinking": "enabled",
      "efforts": ["low", "medium", "high"],
      "default_effort": "medium"
    },
    {
      "thinking": "adaptive",
      "efforts": ["low", "medium", "high"],
      "default_effort": "medium"
    }
  ],
  "default_thinking": "adaptive"
}
```

拟定的模型层结构：

```text
CapabilityConfig
├── operation
├── input/output modalities
├── generation
├── embedding
└── reasoning?               ← 仅 generate/realtime 能声明
    ├── mode profiles
    │   ├── thinking
    │   ├── supported efforts
    │   └── optional default effort
    └── optional default thinking
```

规则：

1. 目录只填有官方或服务方资料证明的模型；不能根据模型名字猜“这是推理模型”。
2. `reasoning` 缺失表示“目录不知道/不承诺”，不是“模型一定不支持”。请求未指定
   reasoning 时照常调用；请求明确指定时应拒绝，直到能力被登记。
3. `disabled` 模式不能配置 `default_effort`；没有思考就没有 effort。
4. 目录未知的默认值保持未知，不在 Gateway 里发明一个全局默认。
5. reasoning 只随 `ModelCatalogSource` 的完整模型记录进入 Catalog，不提供请求级
   override 或局部 patch 语义。

模型默认值的优先级只在已知时生效：

```text
请求显式值
  > 模型目录已知默认值
  > 保持省略，由上游接口决定
```

没有任何一层知道默认值时就省略字段，让上游决定；绝不填一个猜测值。

## 3. 一次请求的处理顺序

```text
Router 已选 exact BaseModel
        ↓
读取模型 reasoning policy
        ↓
补模型默认（仅补已知值）
        ↓
校验 thinking/effort 组合
        ↓
与 protocol + service 的可表达能力求交集
        ↓
冻结到 admission-owned immutable admitted request
        ↓
adapter 翻译成厂商字段
```

校验行为：

| 情况 | 结果 |
| --- | --- |
| 未传 `reasoning` | 使用已知默认，否则保持省略 |
| 只传 `effort` | 保留模型默认 thinking；若默认模式未知且组合有歧义则拒绝 |
| `thinking=enabled/adaptive`，未传 effort | 使用该模式的已知默认，否则交给上游 |
| `thinking=disabled` | 明确关闭；不带 effort |
| `thinking=disabled` 且带 effort | `INVALID_REQUEST`，不能静默丢字段 |
| 模型不支持显式模式或 effort | `UNSUPPORTED_CAPABILITY` |
| 协议/服务无法无损表达 | `UNSUPPORTED_CAPABILITY`，不换模型、不换服务、不降级 |

错误要在 admission 阶段返回 typed error，网络请求之前完成。未知值是请求错误；已知
但当前模型/协议/服务不支持是能力错误。不得因为不兼容而让 Router 重新选候选模型。

## 4. 各家适配方式

这是 adapter 的映射表，不是公共 DTO：

| 厂商/协议 | `disabled` | `enabled` | `adaptive` | `effort` |
| --- | --- | --- | --- | --- |
| OpenAI Responses | 映射为 `reasoning.effort=none`（模型支持时） | 映射为非 `none` 的 effort | OpenAI 原生按 effort 自适应；descriptor 明确声明与 `enabled` 的等价/差异 | `reasoning.effort` |
| OpenAI Chat Completions | 同上，字段是 `reasoning_effort=none` | 同上 | 同上 | `reasoning_effort` |
| Anthropic Messages（新模式） | `thinking.type=disabled` | `thinking.type=enabled` | `thinking.type=adaptive` | `output_config.effort` |
| Anthropic 旧 extended thinking | 不适用 | 通常要求 `budget_tokens` | 不适用 | 本方案不提供 `budget_tokens`，该组合拒绝 |
| Gemini | 只有模型/接口明确支持关闭时才映射；不能把 `MINIMAL` 冒充关闭 | 用 `thinkingLevel` 等价映射 | 只有接口明确支持时才映射 | `thinkingLevel`；`thinkingBudget` 不向公共层暴露 |
| DeepSeek | `thinking=false` | `thinking=true` | 仅在该接口明确支持时接受 | `reasoning_effort` |

同一模型经不同服务商托管时，最终是否可用由选定的 protocol/service descriptor 决定。
适配器不能“看起来差不多”就吞掉 `adaptive` 或把 `disabled` 降级成低 effort；若没有
明确的等价声明就拒绝。

`includeThoughts`、Anthropic 的显示开关等是“是否返回思考内容”，不是“是否思考”。
本阶段不加入公共字段。现有 `usage.reasoning_tokens` 继续保留；不新增或持久化隐藏的
完整思维链。

## 5. 本轮落地范围与后续边界

### 公共 DTO 和契约（本轮已实现的未投产 groundwork）

- `src/api/reasoning.go`：新增枚举和 `ReasoningConfig`，集中做值校验。
- `src/api/inference.go`：`LLMInput` 增加 `Reasoning *ReasoningConfig`。
- `../../conformance/schemas/protocol/gateway_invocation.v1.schema.json`：在 LLM
  input 的 generate/realtime 分支增加可选 `reasoning`，并禁止未知/厂商字段。
- `../../conformance/spec/gateway-invocation.md`：补充语义、默认和错误规则。

当前 invocation 契约尚未投产，直接在现有 v1 定义上补齐该可选字段并新增
reasoning 通过/拒绝 vector；不新增另一套版本，也不保留兼容迁移分支。

### 模型层（本轮已实现的未投产 groundwork）

- `src/internal/model/capability.go`：增加 `ReasoningPolicy`、模式矩阵、默认解析和
  深拷贝。
- `src/internal/model/definition.go` / `shape.go`：校验 reasoning 只出现在允许的
  LLM operation，校验默认和模式组合。
- `src/internal/model/catalog.go`：把 `ModelCatalogSource` 的完整记录严格转换成
  Catalog；`reasoning` 是可选模型能力，没有该字段时按“未声明”处理，不伪造默认值。
- 数据库目录只录入有官方或服务方资料核实的 reasoning 事实；没有可靠资料的模型
  保持未声明，后续通过用户 CRUD 更新完整模型记录。
- 不提供 reasoning 专用 override、局部 patch 或第二目录来源。

### Admission、协议、服务和调用链（本轮已实现的 Gateway 侧 groundwork）

- `src/internal/admission/validator.go`：统一完成精确模型查找、operation 默认、请求
  shape、reasoning，以及 model × protocol × service 能力交集校验；返回 typed error，
  不做 fallback。
- `mote-infra/invocation`：唯一 inbound wire/schema owner，严格校验 canonical key、
  类型、未知字段、`null`、尾随 JSON，并把 operation/reasoning 的 presence 和 typed
  frame 一起交给 Gateway。Gateway 不再解析 inbound JSON。
- `src/internal/protocol/descriptor.go`：协议声明能无损表达的 operation、mode、feature
  和 thinking/effort 组合。具体 adapter 仍只在自身边界生成厂商字段。
- `src/internal/service/descriptor.go`：服务声明同一组中立能力上的额外限制；endpoint、
  credential 和签名仍归 service connector，不进入模型目录。
- `src/internal/admission/admitted.go`：按单次请求所有权约定封装规范化请求、精确模型定义、
  protocol id 和 service kind；frame 交给 Gateway 后调用方不再修改请求。
- `src/internal/application/invoke.go`：建立唯一的 typed frame → admission → admitted request →
  adapter 链；所有 unary、stream、duplex、async 入口共用这条链，adapter 不再接收未
  校验的原始请求。
- `src/invocation.go`：正式装配要求 composition root 显式注入一个 `InvocationConfig`，其中
  包含已经选定的模型目录、协议和服务 descriptor。生产路径没有全能力放行默认；全能力
  descriptor 只存在于 `internal/testkit`。同一个 config 构造唯一 `Invocation` admission
  pipeline，各 delivery mode 仅传入自己需要的 adapter。真正投产前仍须由
  `mote-infra/invocation` 把 canonical typed frame 接入，并由具体 protocol/service
  adapter 消费 admitted request；本仓库的 conformance runner 只验证契约和 admission，不替代
  那条外部生产链。

## 6. 测试和验收清单

模型/Admission 单测覆盖：

- 三种 thinking 模式、每种 effort 值及未知值；
- 省略配置、只传 effort、模型默认和显式值覆盖；
- `disabled + effort` 被拒绝；
- 模型支持模式但协议不支持时被拒绝；
- 未声明 reasoning 的模型拒绝显式 reasoning，但普通请求不受影响；
- 不切换 BaseModel、不切换 service、不偷偷补 `budget_tokens`；
- source record 冻结、整批校验和 Catalog 刷新失败保留旧值。

conformance vector 已新增：

- `reasoning_disabled`；
- `reasoning_enabled_effort`；
- `reasoning_adaptive_effort`；
- `reasoning_omitted`；
- 未知 thinking、未知 effort、`disabled + effort` 拒绝；
- 空对象、显式空字符串和 `null` 不会被当成省略；
- `budget_tokens`、`reasoning_effort`、`thinkingBudget`、`includeThoughts` 拒绝；
- media 请求携带 reasoning 拒绝。

具体厂商 adapter 尚未实现；实现时必须用本地 fixture 验证字段映射，不依赖在线厂商
API。本轮门禁使用现有 deterministic unit/integration/conformance 测试。

## 7. 本阶段明确不做

- 不做 `budget_tokens` 或任何 token 预算控制；
- 不把供应商原始字段暴露到 `src/api`；
- 不自动从模型名推断 reasoning 能力；
- 不增加“思考内容是否返回”的公共开关；
- 不把 reasoning 放进 `operation`、媒体参数或 Router 选择逻辑；
- 不因某个协议不支持而静默换协议、换服务或换模型。

## 调研依据（官方文档）

- OpenAI Reasoning：<https://developers.openai.com/api/docs/guides/reasoning>
- Anthropic Thinking：<https://platform.claude.com/docs/en/build-with-claude/thinking>
- Anthropic Extended Thinking：<https://platform.claude.com/docs/en/build-with-claude/extended-thinking>
- Gemini Thinking：<https://ai.google.dev/gemini-api/docs/thinking>
- DeepSeek Thinking Mode：<https://api-docs.deepseek.com/guides/thinking_mode>

厂商参数和模型支持会变化；真正编码时要再次核对目标模型的官方页面，并把核对结果
写进对应 catalog/adapter 的评审记录，不凭历史记忆填值。
