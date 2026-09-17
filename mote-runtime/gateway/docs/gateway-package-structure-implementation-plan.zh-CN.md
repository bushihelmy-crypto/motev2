# Gateway 包结构收敛实施需求

> 状态：本轮结构治理已落地；`internal/application` 的真实上游执行接口仍留待执行链投产后单独评估。
>
> 本文同时记录已落地的目录、owner 和 Catalog 边界；它不宣称尚未接入生产的具体协议/服务执行器已经投产。

## 0. 本轮落地状态

- 具体协议、服务和 upstream 的实现目录已迁入各自 `internal` owner；旧顶层目录已删除，目录迁移不代表真实执行器已经投产。
- `internal/capability`、`internal/plan` 以及两个 registry 文件已删除。
- admission 直接读取 protocol/service 各自的事实接口，并返回不可变的
  `AdmittedLLM` / `AdmittedMedia`。
- `ports.ModelCatalogSource`、完整记录转换和串行原子刷新已实现；生产构造必须先成功加载
  数据库目录，gzip 目录仅作为 `internal/model` 测试夹具编译。
- 架构门禁按 owner 和真实依赖方向检查，不再按目录深度阻止 sibling 使用 `internal`。

## 1. 目标

在不改变 Gateway 业务边界的前提下，删除重复抽象，收回不需要公开的实现包，并为后续“用户 CRUD 模型、数据库保存模型”留出清楚的接入点。

Gateway 仍然只负责执行已经选好的模型调用，不负责选择模型、协议、服务商，也不负责 fallback、计费、MCP 或 agent loop。

## 2. 最终决策

| 事项 | 决策 |
| --- | --- |
| `internal/capability` | 删除。能力事实由模型、协议、服务各自维护，能力交集由 `admission` 直接判断。 |
| Protocol Adapter | 保留，负责协议报文和流事件的编码、解码。 |
| Service Connector | 保留并补全，负责地址、部署信息、凭据、云签名和服务级错误。 |
| Application Adapter | 不纳入本轮。它是调用入口/注入 seam，是否需要收敛等真实执行链落地后再判断。 |
| `plan` | 删除。`admission` 完成审核、规范化并返回一次性已审核调用封装，application 随即执行。 |
| Protocol/Service Registry | 两个 Registry 层都删除；只在装配入口使用简单静态映射。 |
| 模型包 | 保留 `internal/model`，不新增顶层 `model/`，也不增加每个模型一个 Adapter。 |
| 模型数据 | 用户 CRUD 是唯一写入口，数据库是生产环境唯一事实来源；Gateway 只读取最新完整数据。 |
| Catalog 版本 | 不做 revision、历史版本或回滚机制；每次调用只持有自己的已审核请求和局部执行上下文。 |
| 具体实现目录 | `protocols/`、`connectors/`、顶层 `upstream/` 全部迁入 `internal/`。 |
| cache/usage/receipt/telemetry | 本轮保持四个独立 owner，不统一并入 observability。 |

## 3. 目标目录

```text
src/
├── api/                         # 唯一中立请求、响应、事件 DTO 边界
├── ports/                       # 数据库、密钥、时钟等外部依赖契约
├── cmd/gateway/                 # 进程启动和依赖装配
├── integration/                 # integration build tag 下的集成测试
├── internal/
│   ├── application/             # 一次调用的用例编排（接口形态待验证）
│   ├── admission/               # 模型 × 协议 × 服务审核、规范化和一次性调用封装
│   ├── model/                   # 模型定义、规则、校验和内存 Catalog
│   ├── protocol/                # Adapter 契约、协议能力和具体协议实现
│   │   ├── anthropic/messages/
│   │   ├── bedrock/converse/
│   │   ├── gemini/generatecontent/
│   │   └── openai/
│   │       ├── chatcompletions/
│   │       ├── realtime/
│   │       └── responses/
│   ├── service/                 # Connector 契约、服务配置和具体服务实现
│   │   ├── azure/
│   │   ├── bedrock/
│   │   ├── generic/
│   │   ├── huggingface/
│   │   └── vertex/
│   ├── upstream/                # 出站网络契约与实现
│   │   ├── eventstream/
│   │   ├── httpclient/
│   │   ├── pool/
│   │   ├── sse/
│   │   └── websocket/
│   ├── cache/
│   │   ├── prompt/
│   │   └── result/
│   ├── usage/
│   ├── receipt/
│   ├── telemetry/
│   ├── architecture/
│   ├── buildinfo/
│   ├── complexity/
│   └── testkit/
├── doc.go
├── gateway.go                    # Gateway 公共构造门面
└── invocation.go                 # 公共调用入口
```

迁移完成后不再存在：

```text
src/protocols/
src/connectors/
src/upstream/
src/internal/capability/
src/internal/plan/
src/internal/protocol/registry.go
src/internal/service/registry.go
```

`cmd/gateway` 和 `integration` 是启动入口与测试入口，不属于可复用业务实现包，因此可以与 `internal` 同级。

## 4. 各层职责

### 4.1 `internal/model`

它保存“模型本身是什么”：模型名称、支持的 operation、模态、功能、参数范围、默认值和生命周期。

它负责：

- 将数据库记录转换成经过严格校验的模型定义；
- 参数规范化和模型能力规则；
- 构造不可变的内存 Catalog；
- 按完整 `BaseModel` 精确查找。

它不负责 SQL、ORM、数据库迁移、CRUD API、服务地址、凭据、协议报文或模型选择。

模型是数据，不是执行器，因此不增加 `ModelAdapter`，也不为每个模型建立一个子包。
`ports.ModelCatalogSource` 返回公开的 `ports.ModelRecord`，由 model 严格转换；数据库接入不要求新增顶层 `model/`。

### 4.2 `internal/protocol`

Protocol Adapter 就是“协议翻译器”：

- 把中立请求编码成 OpenAI、Anthropic、Gemini、Bedrock 等协议报文；
- 把响应和流事件解码回 `api` 类型；
- 解析协议自己的错误 body；
- 声明该协议能表达哪些 operation、delivery mode 和 feature。

它不拼云平台地址，不取凭据，也不做 AWS/GCP/Azure 鉴权。

### 4.3 `internal/service`

Service Connector 就是“知道请求发到哪里、怎么获得授权的人”：

- 解析 endpoint、region、project、deployment、ARN 等寻址信息；
- 放置凭据引用并完成 Bearer、OAuth、Managed Identity、SigV4 等鉴权；
- 声明该服务实际开放的协议和 operation；
- 分类鉴权、限流和服务可用性等服务级错误。

它不生成或解析协议 JSON。普通兼容服务可以共用 `generic` Connector；只有确有平台逻辑的服务才保留专用实现。

Protocol Adapter 和 Service Connector 职责不同，不要求做成同一个接口，也不合并成 `Provider`。

### 4.4 `internal/admission`

`admission` 在联网前回答一句话：这次已经选好的“模型 + 协议 + 服务 + 请求功能”能不能执行。

模型、协议、服务分别提供自己的事实，`admission` 直接比较它们。它同时完成请求规范化（例如补齐模型唯一 operation、解析模型默认参数），然后返回一个只供当前调用使用的“已审核请求封装”。这个封装可以带上规范化请求、已选模型定义、协议/服务身份和安全配置引用，但不是公共 DTO，也不落库。

不得再增加公共 `capability.Matrix`、通用能力 Registry 或只做转交的中间接口。

审核失败返回稳定的 typed error，不换模型、不换协议、不换服务商，也不访问上游。

### 4.5 不设 `internal/plan`

每次请求都“创建 plan、执行一次、马上丢弃”的做法没有独立价值：它只是把规范化请求和选择结果重新装进一个结构体，增加一层跳转。

因此不建立 `plan` 包、`CallPlan` 类型或 plan 生命周期。`admission` 返回的已审核请求封装由 application 直接消费；需要跨步骤保存的值放在当前调用的局部变量或私有 session/task 上下文中即可。

这个封装不保存明文凭据、不包含 fallback 候选，也不作为持久化工单。后续执行仍不得重新接收原始请求后再做一次选择或审核。

Catalog 刷新后，进行中的调用继续使用自己当前上下文中的已审核模型定义；刷新后的新调用使用新 Catalog。

排队、延迟执行、重试或进程恢复也不自动要求 `plan`。这些场景由队列自己的任务消息/任务记录保存所需数据；只有将来明确需要跨场景复用独立“执行工单”时，才重新评估是否引入该对象。

### 4.6 `internal/application`

`application` 负责把一次调用串起来。它接收 admission 返回的已审核请求封装，直接编排协议、服务和网络传输：

```text
typed frame
→ admission
→ 已审核请求封装
→ protocol.Adapter.Encode
→ service.Connector.Resolve/Authorize
→ upstream.Transport
→ protocol.Adapter.Decode
→ cache/usage 收尾
→ receipt
→ telemetry
```

LLM 和媒体调用共用这套三维执行骨架：都要把“已选模型 + 已配置协议 + 已配置服务商”交给 admission 检查，再分别进入对应的 Adapter、Connector 和 upstream。差别只在 `LLMRequest`/`MediaRequest`、operation 能力以及 unary、流式、realtime、异步等生命周期；媒体生成仍由 Execution 发起，不从 Kernel Think 发起。

当前代码里的 `application` 还只是这条链的前置管道：持有 Validator、统一调用入口、检查 delivery mode，再把已审核结果交给注入的执行接口；它尚未真正调用协议、服务或 transport。这个现状不代表最终接口设计。

是否保留 `internal/application` 现有的 LLM/Media Adapter 接口不属于本轮结构决策，先不强制删除。需要支持 unary、stream、realtime、async 时，仍由 application 用例编排，并保持每条流只有一个取消和终结路径。若后续证明该接口只是重复转发，再单独收敛。

### 4.7 `internal/upstream`

只做网络机械工作：HTTP、连接池、TLS、代理、deadline、取消、SSE、WebSocket、EventStream 和背压。

它不理解模型能力，不选择协议或服务，也不负责中立 DTO。

### 4.8 `api`、`ports` 和 Gateway 根包

- `api` 是唯一的服务商/协议中立 DTO 边界。
- `ports` 是 Gateway 对数据库、密钥、时钟、缓存存储等外部能力的窄依赖，不包含数据库实现。
- Gateway 根包只提供必要的公共构造与调用门面。
- `cmd/gateway` 负责创建具体 Adapter、Connector、Transport 和外部 Port 实现并完成注入。

## 5. 模型 CRUD、数据库和 Catalog 刷新

### 5.1 数据所有权

- 用户 CRUD 是模型定义的唯一写入口。
- 数据库是生产环境唯一事实来源，不再从其他项目导入模型。
- Gateway 不自建数据库、迁移系统或 CRUD 管理面，只通过 `ports.ModelCatalogSource` 读取。
- `ports.ModelCatalogSource` 的公共签名不得暴露 `internal/model` 类型；Port 返回公开、强类型的持久化边界记录，再由 `internal/model` 转换和校验。
- 具体 CRUD 字段应与数据库契约一起确定，不为了占位提前扩大公共 API。
- 当前内置 gzip 数据最终只作为开发或测试数据，生产环境不得把它作为数据库失败时的第二数据源。

### 5.2 刷新流程

不维护 revision，也不做增量 patch。每次刷新都读取数据库中最新的完整数据：

```text
用户 CRUD 提交成功
→ 发出“模型目录已变化”信号
→ Gateway 读取最新完整模型数据
→ internal/model 严格校验并构造新 Catalog
→ 原子替换当前内存 Catalog
```

刷新规则：

1. Gateway 启动时必须先加载一次最新完整数据；没有可用 Catalog 时不得进入 ready 状态。
2. 正常调用只读内存 Catalog，不在每次模型调用时查询数据库。
3. 刷新必须串行或合并触发；刷新期间再次收到变化信号，完成后至少再加载一次最新数据，不能让较早的结果覆盖较新的结果。
4. 新数据必须整批校验成功后才能替换；校验或读取失败时继续使用当前 Catalog，并告警。
5. 定时全量加载可作为变化信号丢失后的兜底；它仍然只读取最新完整数据，不建立版本历史。
6. Catalog 不得原地修改 map。替换前后的 Catalog 都保持不可变，确保进行中的调用上下文不受影响。
7. Catalog 和 receipt 均不增加 revision 字段。

变化信号使用消息、通知还是进程内回调属于部署实现选择，不改变上述语义。

## 6. 删除 Registry 层

模型 Catalog 是会被数据库刷新替换的动态数据；Adapter 和 Connector 是编译进程序的静态代码，两者不能混成同一种 Registry。

在 `cmd/gateway` 的装配代码中使用普通、只读的映射即可：

```text
protocol ID    → Protocol Adapter
service kind   → Service Connector factory
```

要求：

- 不提供全局可变 Registry；
- 不提供运行时注册、发现、插件热加载或调用中替换；
- 在启动或用户配置装配时解析一次并注入 application；
- 未找到明确报错，不 fallback；
- 新增静态协议或 Connector 时，修改装配映射并重新构建程序。

这两个映射只是装配代码，不是新的架构层。

## 7. cache、usage、receipt、telemetry 保持独立

本轮不调整以下四个 owner：

```text
internal/cache
internal/usage
internal/receipt
internal/telemetry
```

- `cache`：prompt/context cache 和结果缓存策略，属于执行语义。
- `usage`：收集并归一化实际用量。
- `receipt`：形成可审计的调用记录，并经 Port 持久化。
- `telemetry`：metrics、trace 和受控日志，才是真正的可观测性。

usage 和 receipt 可以向 telemetry 提供观测信息，但不能因此被收进 telemetry。

上游缓存语义不只存在于 Claude。厂商专有字段由相应 Protocol Adapter 编解码，通用缓存策略和归一化结果仍由 `internal/cache` 管理；连接池、云 token 和 Connector 元数据缓存继续归各自 owner。

## 8. 依赖和架构测试调整

取消“只要目录在外层，就一律不能 import `internal`”这种按目录深度判断的规则。Gateway 根包、`cmd/gateway` 和 application 必须能够装配内部实现。

架构测试改为检查真实 owner 边界：

- `api` 不依赖内部实现；
- `ports` 不暴露 `internal` 类型，也不包含外部实现；
- model、protocol、service 三个事实 owner 不互相吞并职责；
- application 可以编排 admission、protocol、service、upstream、cache、usage、receipt、telemetry；
- 具体 Protocol Adapter 和 Service Connector 可以依赖完成各自接口所必需的 `api` 及本 owner 契约，但不得反向依赖 application 或 admission；
- 不允许 Go import cycle；
- 不允许 `common`、`shared`、`util` 等无 owner 公共包；
- 生产代码不得依赖 `internal/testkit`。

架构测试应保护职责方向，不能为了形式上的“外层/内层”阻止正常装配。

## 9. 实施顺序

1. 调整架构测试和结构检查脚本，使其描述本文目标目录和依赖规则。
2. 删除 `internal/capability`，把模型、协议、服务事实归还各自 owner，由 admission 直接完成交集判断。
3. 删除 `internal/plan`，让 admission 返回一次性已审核请求封装，application 直接消费。
4. 保留当前 application 的 LLM/Media Adapter seam；真实 Service Connector/Transport 执行链投产前再单独评估其参数形态，不在本轮凭空增加第二套执行接口。
5. 将具体协议、服务和网络实现迁入对应 `internal` owner，修正 import path。
6. 删除两个 Registry 文件，在 composition root 建立只读装配映射。
7. 接入 `ModelCatalogSource` 和最新完整 Catalog 的原子刷新；生产路径停用内置 seed 和 override 作为第二数据源。
8. 同步更新 `README.md`、`architecture.md`、`docs/package-boundaries.md`、包注释和复杂度基线。
9. 运行 `make check`；任何不可用工具都记录具体命令和原因，不降低门禁。

每一步都必须保持可编译、可测试，不以一次大搬家掩盖职责变化。

## 10. 验收标准

- 目标目录中不再存在顶层 `protocols/`、`connectors/`、`upstream/`。
- 不再存在 `internal/capability`、`internal/plan`、Protocol Registry 或 Service Registry 抽象。
- admission 直接使用模型、协议、服务各自声明的事实，并在联网前完成审核。
- application 的职责是调用用例编排；现有 LLM/Media Adapter 接口是否保留，不作为本轮验收条件。
- Protocol Adapter 不负责 endpoint 和鉴权；Service Connector 不负责编解码协议 body。
- admission 返回的已审核请求只服务当前调用；调用中不能重新选择或 fallback。
- `internal/model` 继续拥有模型规则和不可变 Catalog；不存在顶层 `model/` 或 Model Adapter。
- 生产模型只来自数据库最新完整数据；没有 catalog revision、版本历史或生产 seed fallback。
- Catalog 刷新整批校验、原子替换，失败保留旧 Catalog；进行中调用不受刷新影响。
- cache、usage、receipt、telemetry 仍为四个独立 owner。
- 架构测试检查 owner 和依赖方向，不再检查没有业务含义的目录层级禁令。
- `make check` 全部通过。

## 11. 非目标

本次调整不引入：

- 模型、协议或服务商路由；
- fallback 候选和语义重试；
- 价格、预算、用户治理或账单；
- MCP discovery、工具执行或 agent loop；
- Gateway 自有数据库、ORM、迁移和 CRUD 管理界面；
- 动态插件系统；
- 对外发布的 Protocol/Connector SDK；
- 新的通用 `common/shared/util` 层。
