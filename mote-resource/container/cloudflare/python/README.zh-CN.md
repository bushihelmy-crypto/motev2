# Mote Cloudflare Python Container

本项目是 Mote 面向 Cloudflare 的 Python Container 实现。它提供“一个逻辑 Agent 对应一个 Python Durable Object”的部署基础，同时把 Agent 身份和血缘留在 `mote-control`，把 Agent Flow 语义留在 `mote-kernel`，把持久化 backend 选择留给 Kernel Port 配置。

项目目前处于脚手架阶段。Python Worker 与 Durable Object 类已经完成配置，但尚未固定 Agent 协议、持久化状态表、Product 路由或 Kernel 集成方式。这些边界应由第一个真实消费方及其 conformance 用例共同驱动。

## 运行模型

- `Default` 是公开的 Python Worker 入口。
- 共享身份协议确定后，由 Control 签发的稳定 Agent 身份将选择一个 `AgentDurableObject`。
- Durable Object 将在同一 Python 运行环境内调用 Kernel 契约。
- 持久状态通过 Port 配置选择的 `Commit` backend 写入；该 backend 可以使用对象私有的 Cloudflare SQLite，也可以使用远端存储。
- Worker 当前返回 `404`，Durable Object 返回 `501`，避免脚手架无意中固定公共 API。

Worker 使用 Cloudflare 的 `python_workers` compatibility flag，并通过声明式 Durable Object `exports` 配置声明 `storage: "sqlite"`。这个字段只暴露可选的平台存储能力，并不选择持久化 backend。只有 Port 配置选择对象私有存储时，SQL、schema 和事务代码才由 `mote-infra/persistence/cloudflare/python` 提供。Python Workers 当前仍处于 Beta。

## 开发

固定工具链为 Python 3.13、uv 0.12.3、pnpm 10.30.3 和 Wrangler 4.125.0。Python 依赖安装到本项目 `.venv`，Wrangler 安装到本项目 `node_modules`。

```bash
uv sync --locked
pnpm install --frozen-lockfile
make check
```

启动本地 Worker：

```bash
uv run pywrangler dev
```

仅构建部署产物：

```bash
make worker-build
```

完成 Wrangler 认证并确认目标 Cloudflare 账户后才能部署：

```bash
make deploy
```

## Kernel 与 Persistence Port 配置

第一个纵向集成会把 `mote-kernel` 加入真实 Python 依赖。Kernel Port 配置独立解析 `Commit` backend：选择 `mote-infra/persistence/cloudflare/python` 时，resolver 向它提供 Durable Object storage capability；选择远端 backend 时则完全不走本地 SQL。本 Container 只承载 Kernel 并暴露平台能力，不 import、不选择、也不构造持久化实现。

## 包状态

本包处于 pre-alpha。它会构建为带类型信息的 Python 包用于验证，但发布产物是部署后的 Cloudflare Worker，而不是 PyPI 包。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。
