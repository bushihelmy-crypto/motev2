# Mote Cloudflare Container

本项目是 Mote 的纯 TypeScript Cloudflare Container 实现。它提供“一个逻辑 Agent 对应一个 Durable Object”的部署与测试基础，同时把 Agent 身份和血缘留在 `mote-control`，把 Agent 流程语义留在 `mote-kernel`，把持久化 backend 选择留给 Port 配置。

项目目前处于脚手架阶段。Durable Object 类和绑定可以构建、测试和部署，但尚未固定 Agent 请求协议、持久化状态表结构或 Product 路由。这些接口应由第一个真实消费方及其 conformance 用例共同驱动。

## 运行模型

- `AgentDurableObject` 是 Cloudflare 侧承载单个逻辑 Agent 的容器。
- 共享身份协议确定后，由 Control 签发的稳定 Agent 身份将映射到一个 Durable Object 身份。
- Container 调用 Kernel 契约，但不解释 Agent 流程语义。
- 持久状态通过 Port 配置选择的 `Commit` backend 写入；该 backend 可以使用对象私有的 Cloudflare SQLite，也可以使用远端存储。
- 默认 Worker 当前返回 `404`，Durable Object 返回 `501`，避免脚手架无意中固定 Product API。

Durable Object namespace 使用 Cloudflare 当前的声明式 `exports` 配置，并设置 `storage: "sqlite"`。它只暴露可选的平台存储能力，并不选择持久化 backend。只有 Port 配置选择对象私有存储时，SQL、schema 和事务代码才由 `mote-infra/persistence/cloudflare/ts` 提供。这是一个全新的 Worker，因此不采用旧的 `migrations[].new_sqlite_classes` 形式。

Container 与 Persistence 是两个正交选择。本包只承载选定的 Kernel runtime 并暴露平台能力，不 import、不选择、也不构造持久化实现。

## 开发

Node 24 是主要开发和完整质量门禁版本。CI 还会在 Node 22.19 与 Node 26 上运行测试。包管理器版本固定在 `package.json` 中。

```bash
pnpm install --frozen-lockfile
pnpm run types
pnpm run check
```

启动本地 Worker：

```bash
pnpm run dev
```

只构建部署产物而不发布：

```bash
pnpm run build
```

只有在 Wrangler 已完成认证并确认目标 Cloudflare 账户后才执行部署：

```bash
pnpm run deploy
```

## 包状态

本包设为私有，因为它的发布产物是部署后的 Cloudflare Worker，而不是 npm 库。依赖通过 `pnpm-lock.yaml` 锁定；格式检查、lint、严格类型检查、基于 workerd 的测试、覆盖率与 Wrangler dry-run 构建都由项目脚本和 CI 复现。

`src/worker-configuration.d.ts` 由 Wrangler 生成，但只保留本项目的绑定声明。完整 Workers Runtime 类型固定在 `node_modules` 中的 `@cloudflare/workers-types` 依赖里，不向仓库加入上万行生成代码。

## 许可证

Apache License 2.0，见 `LICENSE`。
