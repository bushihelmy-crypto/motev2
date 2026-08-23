# Mote Cloudflare Python Infra

本项目是 Mote Infra 面向 Cloudflare 的 Python 实现。它提供“一个逻辑 Agent 对应一个 SQLite-backed Python Durable Object”的部署基础，同时把 Agent Flow 语义继续留在 `mote-kernel`。

项目目前处于脚手架阶段。Python Worker 与 Durable Object 类已经完成配置，但尚未固定 Agent 协议、持久化状态表、Product 路由或 Kernel 集成方式。这些边界应由第一个真实消费方及其 conformance 用例共同驱动。

## 运行模型

- `Default` 是公开的 Python Worker 入口。
- 共享身份协议确定后，稳定的 Agent 身份将选择一个 `AgentDurableObject`。
- Durable Object 将在同一 Python 运行环境内调用 Kernel 契约，并用 Cloudflare 存储实现其 Infra Port。
- 需要跨驱逐或部署保留的状态必须写入对象私有且强一致的 SQLite 数据库。
- Worker 当前返回 `404`，Durable Object 返回 `501`，避免脚手架无意中固定公共 API。

Worker 使用 Cloudflare 的 `python_workers` compatibility flag，并通过声明式 Durable Object `exports` 配置选择 `storage: "sqlite"`。Python Workers 当前仍处于 Beta。

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

## Kernel 集成

第一个真实 Kernel 集成会把 `mote-kernel` 加为 Python 依赖，并注入 Cloudflare-backed Infra Port。Kernel 到 Infra 的调用是 Durable Object 内的普通 Python 函数调用；只有 Worker 到 Durable Object 的通信经过 Cloudflare RPC。

## 包状态

本包处于 pre-alpha。它会构建为带类型信息的 Python 包用于验证，但发布产物是部署后的 Cloudflare Worker，而不是 PyPI 包。

## 许可证

Apache License 2.0，见 [LICENSE](LICENSE)。
