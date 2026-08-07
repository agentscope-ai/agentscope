# -*- coding: utf-8 -*-
"""自定义供应商凭证（ELLM）。

- :class:`ELLMCredential`: 自研 ELLM 供应商（模型 DeepSeek-V4-Flash，
  OpenAI 兼容端点）。
- 导入本包即完成 :class:`CredentialFactory` 注册（副作用），
  ``GET /credential/schemas`` 会自动包含它，前端无需改动。
"""
from agentscope.credential import CredentialFactory

from .ellm import ELLMCredential

# 注册自定义供应商：导入 bocomadp.credential 即注册
CredentialFactory.register_credential(ELLMCredential)

__all__ = ["ELLMCredential"]
