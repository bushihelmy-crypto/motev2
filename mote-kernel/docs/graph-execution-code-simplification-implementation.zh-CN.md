# Graph execution 代码简化候选 A-v1 历史处置索引

## 1. 文档状态

- 状态：`RETIRED / KEEP / NO IMPLEMENTATION / NON-NORMATIVE HISTORY`
- 日期：2026-08-26
- 历史 target：`ScopedStateIndex` / `ScopedStateBinding`
- 当前 target owner：[候选 A-v2 实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)

该历史 target 已证明没有真实净删除面：它不消除 confirmed state 的 lookup、replacement、projection 或 allocation，反而增加
type、field、wrapper allocation 与第二 owner/API 风险。因此 A-v1 永久退休，不得从本路径推导 production target、批准状态或
compatibility 要求。

## 2. 历史记录边界

本路径曾承载 A-v1 的可变 docs-only closure 内容；第五次评审记录的 SHA256
`bcd84c237dd6e46af27d6085804fd7abda80a672dedf16439b08bc47c9a8e621` 在当前仓库没有对应 blob。该哈希只标识当时的评审输入，
不能据此伪造或反向重建一个“看似相同”的快照。

本文件只为历史链接提供稳定的 A-v1 落点，不复制已退休方案正文，也不冒充上述 SHA 的归档对象。A-v1 的 finding、整改与最终
`KEEP` 裁决由以下独立记录保留：

- [首轮评审](graph-execution-code-simplification-implementation-review.zh-CN.md)
- [首轮评审回复](graph-execution-code-simplification-implementation-review-response.zh-CN.md)
- [第二次独立评审](graph-execution-code-simplification-implementation-second-review.zh-CN.md)
- [第二次评审回复](graph-execution-code-simplification-implementation-second-review-response.zh-CN.md)
- [第三次独立评审](graph-execution-code-simplification-implementation-third-review.zh-CN.md)
- [第三次评审回复](graph-execution-code-simplification-implementation-third-review-response.zh-CN.md)
- [第四次独立评审](graph-execution-code-simplification-implementation-fourth-review.zh-CN.md)
- [第四次评审回复](graph-execution-code-simplification-implementation-fourth-review-response.zh-CN.md)
- [第五次独立评审](graph-execution-code-simplification-implementation-fifth-review.zh-CN.md)

A-v2 首次评审发生在路径分代之前，因此其冻结表仍显示本历史路径；该次评审后的 current owner 与 finding disposition 只见
[versioned A-v2 实施方案](graph-execution-code-simplification-implementation-v2.zh-CN.md)和
[A-v2 评审回复](graph-execution-code-simplification-implementation-v2-review-response.zh-CN.md)。

## 3. 当前 disposition

```text
A-v1 = REJECTED / RETIRED / KEEP / NO IMPLEMENTATION
A-v2 = SEPARATE VERSIONED OWNER / NOT APPROVED
production/tests = NO CHANGE
```
