# 架构

Mote Kernel 将领域流程、图执行、状态转换与外部能力实现分离。

- Domain Flow 定义业务流程拓扑。
- Execution 是所有流程复用的唯一图执行底座。
- StateMachine 决定 GraphState 与 DomainState 的合法转换。
- Port 提供可替换的外部能力，不拥有 Kernel 状态。

同一并发 frontier 中的所有节点接收同一个不可变输入快照。节点和 Port 必须只读该快照，通过类型化结果表达变化，Kernel 不会隐式复制任意领域 DTO；DTO 所有者必须将其定义为不可变值。

本文件暂时只固定架构方向；权威类型与公共契约将在实现时同步补充。
