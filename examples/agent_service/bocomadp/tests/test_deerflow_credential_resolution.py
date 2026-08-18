# -*- coding: utf-8 -*-
"""deerflow 模型解析链单测：约定 credential id 直传 + ELLM 空 api_key 放行。

Contract under test:

- ``is_deerflow_credential_id`` 识别 ``/api/deerflow/models`` 返回的两种
  约定 id 形态（``deerflow-<user>-<provider>`` /
  ``deerflow-default-<provider>``）并解析出 provider_id；模型名 / 任意
  uuid / 他人凭证 id 返回 None。
- ``_resolve_chat_model_config`` 中约定 credential id 直传等价于命中该
  provider 的 config 条目，继续走「用户优先、默认复制」：default 凭证
  id → 复制出用户维度凭证并引用（复制保留 scene_code / api_key_url 等
  刷新元数据）；本用户凭证 id → 直接引用既有凭证，不重复复制。
- 模型名 / provider_id hint 走既有 config.yaml 双键匹配路径不变；场景
  绑定 provider 兜底不变。
- ELLM 条目 api_key 空放行（key 动态获取），非 ELLM 条目 api_key 空仍
  返回 None。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from agentscope.app.storage import ChatModelConfig, CredentialRecord

from bocomadp.config.app_config import AgentEntry, ModelEntry
from bocomadp.credential import (  # noqa: F401 — 导入即注册，from_dict 反序列化需要
    ELLMCredential,
)
from bocomadp.deerflow.credentials import is_deerflow_credential_id
from bocomadp.deerflow.routers import deerflow_chat as chat_mod

_ELLM_PROVIDER = "ellm-deepseek"
_DEEPSEEK_PROVIDER = "deepseek"


def _ellm_entry(api_key: str = "") -> ModelEntry:
    """内网 ELLM 模型条目（api_key 留空，key 由刷新机制动态获取）。"""
    return ModelEntry(
        provider_id=_ELLM_PROVIDER,
        provider_type="bocom_ellm",
        model_name="deepseek-v4-flash",
        api_key=api_key,
        base_url="http://ellm.example/v1",
    )


def _deepseek_entry(api_key: str) -> ModelEntry:
    """外网静态 key 供应商条目。"""
    return ModelEntry(
        provider_id=_DEEPSEEK_PROVIDER,
        provider_type="deepseek",
        model_name="deepseek-chat",
        api_key=api_key,
    )


def _agent_entry(provider_id: str = _ELLM_PROVIDER) -> AgentEntry:
    """场景条目，model_provider 绑定 provider（hint 未命中时兜底用）。"""
    return AgentEntry(
        agent_id="assistant",
        name="assistant",
        model_provider=provider_id,
        model_name=(
            "deepseek-v4-flash"
            if provider_id == _ELLM_PROVIDER
            else "deepseek-chat"
        ),
    )


def _ellm_record_data(
    credential_id: str | None = None,
    api_key: str = "stored-key",
) -> dict:
    """ELLM 凭证 payload（凭证接口插入形态，含刷新元数据）。"""
    return {
        "id": credential_id or f"deerflow-default-{_ELLM_PROVIDER}",
        "type": "bocom_ellm_credential",
        "api_key": api_key,
        "base_url": "http://ellm.example/v1",
        "organization": None,
        "scene_code": "P2024146",
        "api_key_url": "http://ellm.example/createSceneApiKey.do",
        "inject_think_tag": False,
        "apikey_expires_at": None,
        "model": "deepseek-v4-flash",
    }


def _deepseek_record_data() -> dict:
    """DeepSeek 默认凭证 payload（lifespan 入库形态）。"""
    return {
        "id": f"deerflow-default-{_DEEPSEEK_PROVIDER}",
        "type": "deepseek_credential",
        "api_key": "sk-default",
        "base_url": "https://api.deepseek.com",
    }


class _FakeStorage:
    """按 (user_id, credential_id) 命名空间的内存凭证存储。"""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CredentialRecord] = {}
        self.upserts: list[tuple[str, object]] = []

    def seed(
        self,
        user_id: str,
        credential_id: str,
        data: dict,
    ) -> None:
        self._records[(user_id, credential_id)] = CredentialRecord(
            user_id=user_id,
            data=data,
            updated_at=datetime.now(),
        )

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord | None:
        return self._records.get((user_id, credential_id))

    async def upsert_credential(
        self,
        user_id: str,
        credential: object,
    ) -> str:
        self.upserts.append((user_id, credential))
        return credential.id


class _FakeRequest:
    """``app.state.provider_manager`` 为 None 的最小 Request stand-in。"""

    def __init__(self) -> None:
        class _State:
            provider_manager = None

        class _App:
            state = _State()

        self.app = _App()


def _resolve(
    storage: _FakeStorage,
    user_id: str,
    hint: str = "",
) -> ChatModelConfig | None:
    return asyncio.run(
        chat_mod._resolve_chat_model_config(
            storage,
            _FakeRequest(),
            user_id,
            "assistant",
            hint,
        ),
    )


class TestIsDeerflowCredentialId:
    """约定 credential id 识别（纯函数）。"""

    def test_user_credential_id(self) -> None:
        assert (
            is_deerflow_credential_id(
                f"deerflow-user-1-{_ELLM_PROVIDER}",
                "user-1",
            )
            == _ELLM_PROVIDER
        )

    def test_default_credential_id(self) -> None:
        assert (
            is_deerflow_credential_id(
                f"deerflow-default-{_ELLM_PROVIDER}",
                "user-1",
            )
            == _ELLM_PROVIDER
        )

    def test_default_user_matches_own_prefix(self) -> None:
        """user_id 恰为 default 时，用户前缀分支先行命中。"""
        assert (
            is_deerflow_credential_id(
                f"deerflow-default-{_ELLM_PROVIDER}",
                "default",
            )
            == _ELLM_PROVIDER
        )

    def test_model_name_is_not_credential_id(self) -> None:
        assert is_deerflow_credential_id("deepseek-chat", "user-1") is None

    def test_other_users_credential_id_rejected(self) -> None:
        """他人凭证 id 不解析（用户隔离：不跨用户引用）。"""
        assert (
            is_deerflow_credential_id(
                f"deerflow-user-2-{_ELLM_PROVIDER}",
                "user-1",
            )
            is None
        )

    def test_empty_hint(self) -> None:
        assert is_deerflow_credential_id("", "user-1") is None

    def test_missing_provider_segment(self) -> None:
        assert is_deerflow_credential_id("deerflow-user-1-", "user-1") is None


class TestResolveChatModelConfig:
    """``_resolve_chat_model_config`` 的解析优先级与复制语义。"""

    def _patch_loaders(
        self,
        monkeypatch,
        models: list[ModelEntry],
        agents: list[AgentEntry],
    ) -> None:
        monkeypatch.setattr(chat_mod, "load_models_from_yaml", lambda: models)
        monkeypatch.setattr(chat_mod, "load_agents_from_yaml", lambda: agents)

    def test_default_credential_id_copied_to_user(self, monkeypatch) -> None:
        """hint 为 default 凭证 id → 解析 provider → 复制出用户维度凭证
        并引用；复制保留刷新元数据（scene_code / api_key_url / key）。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_ELLM_PROVIDER}",
            _ellm_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_ellm_entry(api_key="")],
            agents=[_agent_entry()],
        )

        config = _resolve(
            storage,
            "user-1",
            hint=f"deerflow-default-{_ELLM_PROVIDER}",
        )

        assert config is not None
        assert config.credential_id == f"deerflow-user-1-{_ELLM_PROVIDER}"
        assert config.type == "bocom_ellm"
        assert config.model == "deepseek-v4-flash"
        assert len(storage.upserts) == 1
        user_id, credential = storage.upserts[0]
        assert user_id == "user-1"
        assert isinstance(credential, ELLMCredential)
        assert credential.id == f"deerflow-user-1-{_ELLM_PROVIDER}"
        assert credential.api_key.get_secret_value() == "stored-key"
        assert credential.scene_code == "P2024146"
        assert credential.api_key_url == (
            "http://ellm.example/createSceneApiKey.do"
        )

    def test_user_credential_id_references_existing(self, monkeypatch) -> None:
        """hint 为本用户凭证 id → 直接引用既有凭证，不重复复制。"""
        storage = _FakeStorage()
        storage.seed(
            "user-1",
            f"deerflow-user-1-{_ELLM_PROVIDER}",
            _ellm_record_data(
                credential_id=f"deerflow-user-1-{_ELLM_PROVIDER}",
                api_key="user-custom-key",
            ),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_ellm_entry(api_key="")],
            agents=[_agent_entry()],
        )

        config = _resolve(
            storage,
            "user-1",
            hint=f"deerflow-user-1-{_ELLM_PROVIDER}",
        )

        assert config is not None
        assert config.credential_id == f"deerflow-user-1-{_ELLM_PROVIDER}"
        assert storage.upserts == []

    def test_model_name_hint_matches_config_entry(self, monkeypatch) -> None:
        """hint 为模型名 → 既有 config.yaml 双键匹配路径不变。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_DEEPSEEK_PROVIDER}",
            _deepseek_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_deepseek_entry("sk-test")],
            agents=[_agent_entry(_DEEPSEEK_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint="deepseek-chat")

        assert config is not None
        assert config.credential_id == (
            f"deerflow-user-1-{_DEEPSEEK_PROVIDER}"
        )
        assert config.type == "deepseek"
        assert config.model == "deepseek-chat"
        assert len(storage.upserts) == 1  # 从 default 复制

    def test_provider_id_hint_matches_config_entry(self, monkeypatch) -> None:
        """hint 为 provider_id → 同样命中 config 条目。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_DEEPSEEK_PROVIDER}",
            _deepseek_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_deepseek_entry("sk-test")],
            agents=[_agent_entry(_DEEPSEEK_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint=_DEEPSEEK_PROVIDER)

        assert config is not None
        assert config.credential_id == (
            f"deerflow-user-1-{_DEEPSEEK_PROVIDER}"
        )

    def test_non_ellm_empty_api_key_returns_none(self, monkeypatch) -> None:
        """非 ELLM 条目 api_key 空 → 仍返回 None，且不产生 upsert。"""
        storage = _FakeStorage()
        self._patch_loaders(
            monkeypatch,
            models=[_deepseek_entry("")],
            agents=[_agent_entry(_DEEPSEEK_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint="deepseek-chat")

        assert config is None
        assert storage.upserts == []

    def test_scenario_provider_fallback(self, monkeypatch) -> None:
        """hint 为空时回退场景绑定 provider（现有逻辑不动）。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_DEEPSEEK_PROVIDER}",
            _deepseek_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_deepseek_entry("sk-test")],
            agents=[_agent_entry(_DEEPSEEK_PROVIDER)],
        )

        config = _resolve(storage, "user-1")

        assert config is not None
        assert config.credential_id == (
            f"deerflow-user-1-{_DEEPSEEK_PROVIDER}"
        )

    def test_no_hint_no_provider_returns_none(self, monkeypatch) -> None:
        """hint 空 + 无场景 provider + 无 active → None（原生 404 兜底）。"""
        storage = _FakeStorage()
        self._patch_loaders(monkeypatch, models=[], agents=[])

        config = _resolve(storage, "user-1")

        assert config is None
        assert storage.upserts == []

    def test_dynamic_hint_routes_to_bound_ellm(self, monkeypatch) -> None:
        """hint 为真实模型 ID（未静态命中）+ 场景绑定 ELLM → 动态路由
        到 ELLM，config.model == hint，default 凭证复制正常。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_ELLM_PROVIDER}",
            _ellm_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_ellm_entry(api_key="")],
            agents=[_agent_entry(_ELLM_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint="Qwen3-235B-A22B")

        assert config is not None
        assert config.type == "bocom_ellm"
        assert config.model == "Qwen3-235B-A22B"
        assert config.credential_id == f"deerflow-user-1-{_ELLM_PROVIDER}"
        assert len(storage.upserts) == 1  # 从 default 复制

    def test_dynamic_hint_routes_to_unique_ellm_entry(
        self,
        monkeypatch,
    ) -> None:
        """hint 为真实模型 ID + 无场景绑定（provider_manager=None）+
        config.yaml 唯一 ELLM 条目 → 动态路由，config.model == hint。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_ELLM_PROVIDER}",
            _ellm_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_ellm_entry(api_key="")],
            agents=[],
        )

        config = _resolve(storage, "user-1", hint="Qwen3-235B-A22B")

        assert config is not None
        assert config.type == "bocom_ellm"
        assert config.model == "Qwen3-235B-A22B"
        assert config.credential_id == f"deerflow-user-1-{_ELLM_PROVIDER}"

    def test_dynamic_hint_creates_entry_credential(
        self,
        monkeypatch,
    ) -> None:
        """hint 为真实模型 ID + default 凭证缺失 → 条目回退入库成功，
        ELLMCredential.model == hint（credentials.py 补 model 生效）。"""
        storage = _FakeStorage()
        self._patch_loaders(
            monkeypatch,
            models=[_ellm_entry(api_key="")],
            agents=[_agent_entry(_ELLM_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint="Qwen3-235B-A22B")

        assert config is not None
        assert config.model == "Qwen3-235B-A22B"
        assert len(storage.upserts) == 1
        user_id, credential = storage.upserts[0]
        assert user_id == "user-1"
        assert isinstance(credential, ELLMCredential)
        assert credential.model == "Qwen3-235B-A22B"

    def test_dynamic_hint_keeps_non_ellm_binding(self, monkeypatch) -> None:
        """hint 为真实模型 ID + 场景绑定 deepseek（非 ELLM）→ 维持现状
        （model 用静态条目名，hint 丢弃）。"""
        storage = _FakeStorage()
        storage.seed(
            "default",
            f"deerflow-default-{_DEEPSEEK_PROVIDER}",
            _deepseek_record_data(),
        )
        self._patch_loaders(
            monkeypatch,
            models=[_deepseek_entry("sk-test")],
            agents=[_agent_entry(_DEEPSEEK_PROVIDER)],
        )

        config = _resolve(storage, "user-1", hint="Qwen3-235B-A22B")

        assert config is not None
        assert config.type == "deepseek"
        assert config.model == "deepseek-chat"
        assert config.credential_id == (
            f"deerflow-user-1-{_DEEPSEEK_PROVIDER}"
        )
