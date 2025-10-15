# Agents Guide (Jump)

本仓库的所有规则、流程与标准均以 `docs/SOP.md` 为唯一权威来源。

- 全局总纲：请阅读 `docs/SOP.md`。
- 模块级规范：见 `docs/<module>/SOP.md`。
- 程序记忆：参考 `CLAUDE.md`。

本文件仅作跳转，不再重复阐述具体规范。

## 验收与映射（Formal Acceptance Mapping）

- 形式化验收映射
  - 建“规范→不变量→测试→示例→代码”五段表；新增/修改必须给出链路映射与破坏性分析。
  - 把 `todo.md` 变为“证明义务清单”：每一条对应至少一个性质测试或 E2E 见证。
