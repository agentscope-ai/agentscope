# -*- coding: utf-8 -*-
"""Tests for :class:`ELLMCredential` — the BOCOM self-hosted ELLM
provider credential.

Covers the distinct discriminator, persistence of the runtime fields via
``_dump_with_secrets``, and round-trip deserialization through
:class:`CredentialFactory` (the class is registered on package import).
"""

from __future__ import annotations

from agentscope.app.storage._utils import _dump_with_secrets
from agentscope.credential import CredentialFactory

from bocomadp.credential import ELLMCredential
from bocomadp.providers.ellm_chat_model import EllmChatModel


class TestELLMCredential:
    def test_type_is_distinct(self) -> None:
        cred = ELLMCredential(api_key="k", model="deepseek-v4-flash")
        assert cred.type == "bocom_ellm_credential"

    def test_runtime_fields_defaults(self) -> None:
        cred = ELLMCredential(api_key="k", model="deepseek-v4-flash")
        assert cred.base_url is None
        assert cred.organization is None
        assert cred.scene_code is None
        assert cred.api_key_url is None
        assert cred.inject_think_tag is False
        assert cred.apikey_expires_at is None

    def test_dump_persists_runtime_fields(self) -> None:
        cred = ELLMCredential(
            api_key="k",
            base_url="http://x",
            organization="org-1",
            scene_code="P2024146",
            api_key_url="http://ellm/createSceneApiKey.do",
            model="deepseek-v4-flash",
            inject_think_tag=True,
            apikey_expires_at=1234567890.0,
        )
        dumped = _dump_with_secrets(cred)
        assert dumped["scene_code"] == "P2024146"
        assert dumped["api_key_url"] == "http://ellm/createSceneApiKey.do"
        assert dumped["model"] == "deepseek-v4-flash"
        assert dumped["inject_think_tag"] is True
        assert dumped["apikey_expires_at"] == 1234567890.0

    def test_get_chat_model_class_resolves_to_bocomadp(self) -> None:
        assert ELLMCredential.get_chat_model_class() is EllmChatModel

    def test_factory_round_trip(self) -> None:
        data = _dump_with_secrets(
            ELLMCredential(
                api_key="k",
                base_url="http://x",
                scene_code="P2024146",
                api_key_url="http://ellm/key",
                model="deepseek-v4-flash",
            ),
        )
        cred = CredentialFactory.from_dict(data)
        assert isinstance(cred, ELLMCredential)
        assert cred.scene_code == "P2024146"
