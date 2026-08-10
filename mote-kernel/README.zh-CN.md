# Mote Kernel

Mote Kernel 是一个以状态机为核心、支持持久恢复的 Agent Kernel。图控制执行，状态机控制事实。

项目目前处于初始架构与实现阶段。默认公共入口将保持为单一 `Role`；图执行、领域状态、持久化和可替换服务均作为内部机制。

详细设计见 [架构说明](docs/architecture.zh-CN.md)。

## 开发

```bash
python -m pip install -e '.[dev]'
pre-commit install
make check
```

`pre-commit install` 和 `pre-commit run --all-files` 应在 monorepo 根目录执行。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE)。
