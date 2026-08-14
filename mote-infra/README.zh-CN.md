# Mote Infra

Mote Infra 是 Mote 中由 Rust 所有的可靠性底座。它将提供持久状态、原子提交、operation receipt、并发协调、可靠 execution attempt、workspace 状态和存储适配器，但不解释 Agent Flow 业务语义。

项目目前处于初始化阶段，尚未固定公共 Commit、wire、RPC 或 daemon 启动 API。

## 当前源码骨架（待定）

    src/
    ├── durable/   内部可靠性语义与存储 Port
    ├── protocol/  conformance wire value 与 Rust value 的严格映射
    └── infrad/    配置与独立服务装配

    tests/
    └── package.rs   外部包导入 smoke test

这三个目录是用于推进讨论的候选职责，不是已经稳定的分包契约。第一条由真实调用方驱动的存储纵切完成前，不用架构测试固化目录或依赖方向，也不承诺外部调用方可以直接依赖这些模块。

## 工程基线

项目初始化阶段不添加数据库或 RPC 依赖。具体依赖将在第一条纵向切片及其 conformance cases 明确后引入。

工程准备参考成熟 Rust 项目的固定工具链、格式化、Clippy、聚焦测试、文档、打包和依赖审计方式。Turso 的内存 IO、可复现 seed、确定性模拟与故障注入值得后续参考，但其 SQL 存储 trait、模块结构和类型擦除方式不定义 Mote 接口。

## 开发

项目固定使用 Rust 1.85.0，并以 `Cargo.toml` 中的 Rust 1.85 作为 MSRV。进入目录后，rustup 会根据 `rust-toolchain.toml` 准备工具链。

    make format
    make check

依赖许可证、来源和漏洞检查需要先安装 cargo-deny：

    cargo install cargo-deny --version 0.19.7 --locked
    make security

根目录 conformance 是跨语言和 durable protocol 的唯一 owner。本项目的协议变更必须同时更新对应 conformance schema 与 cases。

整体愿景和 owner 边界见 [Mote 平台架构](../docs/mote-platform-architecture.zh-CN.md)。

## 状态

Pre-alpha。当前源码只提供可调整的讨论骨架和工程基线，尚未冻结内部边界。

## 许可证

Apache License 2.0，见 LICENSE。
