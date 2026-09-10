# Mote 本地执行运行时

所有工具执行统一走 `Kernel → mote-infra/invocation → execution/local`。
Invocation 负责入口协议和显式解析；`local` 先校验调用，再读取执行路由
配置，决定由本地 handler 执行，还是沿 `local → remote` 交给远端能力。
Unix socket/RPC 的分帧、地址解析、重试、超时和传输错误仍由
`mote-infra/invocation` 负责。

当前提交只建立工程边界和质量门禁，尚未冻结执行 DTO。DTO 必须先在仓库根
目录的 `conformance/` 中形成版本化契约，再由本包实现。路由配置只决定本地
执行还是转发，不负责 Hub/provider 发现。MCP 工具调用和 Skill 读取属于执行
操作，但提供方 schema、发现和端点选择不属于本包；首个 Skill 切片只允许只读
`read`。

运行 `make check` 可执行完整的本地确定性门禁，`make security` 追加依赖和
秘密扫描。
