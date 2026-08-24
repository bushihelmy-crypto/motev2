# Mote Cloudflare TypeScript Persistence

本项目是 Mote 面向 Cloudflare Durable Object 的 TypeScript 持久化实现。具体 `storage.sql`、schema、序列化、migration 和事务行为都归这里所有。

它与 `mote-container/cloudflare/ts` 明确分层：

- Container 负责 Worker 与 Durable Object 入口、注册、定位、调用和部署绑定；
- 上层负责 Agent transition 语义和可观察持久化契约；
- 本包使用对象私有的 Cloudflare SQLite Storage API 实现该契约。

Container 与持久化 backend 是两个独立选择。只有 Port 配置选择 Cloudflare 对象私有 SQLite 时，才会使用 Durable Object storage handle 构造本包唯一导出的 `Commit` callable；同一个 Cloudflare Container 也可以选择远端 backend。本包不 import 任何上层。`storage: "sqlite"` 仍位于 Container 的 `wrangler.jsonc`，因为它是 Cloudflare 部署元数据；所有 `storage.sql` 与 `transactionSync()` 调用都必须留在这里。测试直接使用 workerd 的真实 Durable Object storage，绝不使用宿主机本地 SQLite。

本包不导出其他公共 API。构造 `Commit` 时注入 state accessor 与 encoder，因此最低层不依赖 Kernel 类型。

## 开发

Node 24 是主要开发版本；支持的其他 Node 版本也由 CI 检查。pnpm 版本固定在 `package.json` 中。

```bash
pnpm install --frozen-lockfile
pnpm run check
```

## 包状态

本包是私有 Worker 依赖，仅在 Port 配置选中它时打包，不单独发布。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。
