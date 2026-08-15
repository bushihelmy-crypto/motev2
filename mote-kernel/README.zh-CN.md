# Mote Kernel

Mote Kernel 是一个以状态机为核心、支持持久恢复的 Agent Kernel。图控制执行，状态机控制事实。

项目目前处于初始架构与实现阶段。默认公共组合入口尚未开始设计和实现；图执行与状态原语目前是内部开发接口。

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
