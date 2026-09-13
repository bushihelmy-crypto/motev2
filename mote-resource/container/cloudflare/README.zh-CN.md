# Mote Cloudflare Container

这是 Mote 唯一的 TypeScript Cloudflare 部署项目。Worker/Durable Object Container 宿主和对象私有的 Durable Object SQLite 持久化 Adapter 位于同一个部署包；Agent 身份仍归 `mote-control`，Agent 流程语义仍归 `mote-kernel`，backend 仍由 Port 配置选择。

项目目前处于脚手架阶段。Durable Object 类、binding 和已有的 CAS `Commit` Adapter 已经可以一起构建、测试和部署，但尚未固定 Agent 请求 endpoint 或 Product 路由。这些接口应由第一个真实消费方及其 conformance 用例共同驱动。

## 运行模型

- `AgentDurableObject` 是 Cloudflare 侧承载单个逻辑 Agent 的容器。
- 共享身份协议确定后，由 Control 签发的稳定 Agent 身份将映射到一个 Durable Object 身份。
- Container 调用 Kernel 契约，但不解释 Agent 流程语义。
- 持久状态通过 Port 配置选择的 backend 写入；可以选择同包的 Cloudflare SQLite Adapter，也可以选择远端存储。
- 默认 Worker 当前返回 `404`，Durable Object 返回 `501`，避免脚手架无意中固定 Product API。

Durable Object namespace 使用 Cloudflare 当前的声明式 `exports` 配置，并设置 `storage: "sqlite"`。它只暴露可选的平台存储能力，并不自动选择 backend。只有 Port 配置选择对象私有存储时，同一个 Durable Object 才把运行时注入的 `ctx.storage` 交给 `src/persistence.ts`；SQL、schema、序列化和事务都隔离在该模块中。这是一个全新的 Worker，因此不采用旧的 `migrations[].new_sqlite_classes` 形式。

Container 与 Persistence 仍是两个正交选择，只是 Cloudflare 实现必须物理同包。选择远端 backend 时完全绕过对象私有 SQLite；`ctx.storage` 和 Cloudflare 存储类型都不会穿过 Kernel Port 或 Invocation contract。

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

这个展平后的包设为私有，因为它的发布产物是一个部署后的 Cloudflare Worker，而不是 npm 库；不再维护 Python 或嵌套语言子包。依赖通过 `pnpm-lock.yaml` 锁定；格式检查、lint、严格类型检查、基于 workerd 的 Container/持久化测试、覆盖率与 Wrangler dry-run 构建都由项目脚本和 CI 复现。

`src/worker-configuration.d.ts` 由 Wrangler 生成，但只保留本项目的绑定声明。完整 Workers Runtime 类型固定在 `node_modules` 中的 `@cloudflare/workers-types` 依赖里，不向仓库加入上万行生成代码。

## 许可证

Apache License 2.0，见 `LICENSE`。
