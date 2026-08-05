# -*- coding: utf-8 -*-
"""企业内部智能体平台扩展包。

基于 AgentScope 官方 ``agent_service`` 示例，只承载企业特有能力：
    - ``middlewares``: 企业管控（审计留痕）
    - ``tools``:       企业内部工具占位（HR / 文档库 / ITSM）
    - ``routers``:     平台自有路由（健康检查）

说明：本精简版去掉了企业版中的 ``auth``（JWT + SSO）与 ``org``（组织架构 /
跨用户资源共享），回归 AgentScope 官方的 ``X-User-ID`` 头认证方式，便于直接
复用官方 ``web_ui`` 与 ``Docker-agentscope`` 启动脚本。
"""

__version__ = "0.1.0"
