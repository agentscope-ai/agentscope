# -*- coding: utf-8 -*-
"""外部 skill hub 扩展（目录查询 / 我的上传 / 下载安装）。

- :class:`ExternalSkillHub`: 对接外部 skillhub 的 ``SkillHubBase`` 实现
- 端点路由见 :mod:`bankcomm_adp.routers.skill_router`
"""
from .external_hub import ExternalSkillHub

__all__ = ["ExternalSkillHub"]
