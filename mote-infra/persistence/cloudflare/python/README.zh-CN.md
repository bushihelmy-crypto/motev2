# Mote Cloudflare Python Persistence

本项目是 `mote-infra/persistence` 下的 Cloudflare Durable Object SQLite 持久化 Adapter。Kernel 持久化 Port 背后的具体 SQL、schema、序列化、migration 和事务实现都归这里所有。

它与 `mote-container/cloudflare/python` 明确分层：

- Container 负责 Worker 与 Durable Object 入口、注册、定位、调用和部署绑定；
- Kernel 负责 Agent Flow 语义与持久化 Port 契约；
- 本包使用对象私有的 Cloudflare SQLite Storage API 实现该 Port。

Container 与持久化 backend 是两个独立选择。只有 Port 配置选择 Cloudflare 对象私有 SQLite 时，才会使用 Durable Object storage handle 构造本 Adapter；同一个 Cloudflare Container 也可以选择远端 backend。本最低层包既不 import Kernel，也不 import Container。`storage: "sqlite"` 仍位于 Container 的 `wrangler.jsonc`，因为它是 Cloudflare 部署元数据；所有 SQL 与事务 API 调用都必须留在这里。

本包只暴露 `Commit`。它通过结构类型兼容 Kernel 的 callable Commit Port，不 import `mote-kernel`；实现只使用注入的 Cloudflare Durable Object `storage.sql` 与 `transactionSync()` API，绝不打开本地 SQLite。持久状态字节由 Port 配置注入的版本化 encoder 提供。

## 开发

工具链固定为 Python 3.13 和 uv 0.12.3。依赖安装在本项目内：

```bash
uv sync --locked
make check
```

## 包状态

本包处于 pre-alpha，仅在 Port 配置选中它时作为依赖打入 Cloudflare Python Worker，目前不发布到 PyPI。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。
