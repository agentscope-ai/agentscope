"""deer-flow 模型名对接单测（credentials + 模型解析 + models 端点）。

覆盖计划测试矩阵：

- ``_resolve_requested_model_name``：四通道优先级与空回退；
- ``ensure_default_credentials``：default 用户维度、幂等 upsert、失败不阻断；
- ``_resolve_chat_model_config``：按 model_name / provider_id 匹配、
  未命中回退 agent 绑定 provider、用户凭证优先引用、默认凭证复制入库、
  default 凭证缺失回退 config.yaml 条目；
- ``_ensure_session``：首次 backfill、模型切换更新、一致不更新；
- ``GET /api/deerflow/models``：默认凭证 id 返回、用户凭证优先、
  X-User-ID 缺省 default、default 凭证缺失跳过。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentscope.app.storage import ChatModelConfig, SessionConfig
from agentscope.app.storage._utils import _dump_with_secrets

from bocomadp.config.app_config import AgentEntry, ModelEntry
from bocomadp.deerflow.credentials import (
    DEFAULT_CREDENTIAL_OWNER,
    default_credential_id,
    ensure_default_credentials,
    user_credential_id,
)
from bocomadp.deerflow.routers import models as models_module
from bocomadp.deerflow.routers.deerflow_chat import (
    CreateRunRequest,
    _ensure_session,
    _resolve_chat_model_config,
    _resolve_requested_model_name,
)
from bocomadp.deerflow.routers.models import deerflow_models_router

USER_ID = "u1"
AGENT_ID = "lead_agent"


# ── 测试数据构造 ──────────────────────────────────────────────────────


def _make_model_entry(
    provider_id: str = "ds",
    model_name: str = "deepseek-chat",
    api_key: str = "sk-ds",
    is_active: bool = True,
    **overrides: Any,
) -> ModelEntry:
    kwargs: dict[str, Any] = {
        "provider_id": provider_id,
        "display_name": f"DS {provider_id}",
        "provider_type": "deepseek",
        "model_name": model_name,
        "api_key": api_key,
        "base_url": "https://api.deepseek.com",
        "is_active": is_active,
        "supports_thinking": True,
    }
    kwargs.update(overrides)
    return ModelEntry(**kwargs)


def _make_agent_entry(
    agent_id: str = AGENT_ID,
    model_provider: str = "ds",
    model_name: str = "deepseek-chat",
) -> AgentEntry:
    return AgentEntry(
        agent_id=agent_id,
        name=agent_id,
        model_provider=model_provider,
        model_name=model_name,
    )


def _credential_data(
    credential_id: str,
    api_key: str = "sk-x",
    base_url: str = "https://api.deepseek.com",
) -> dict[str, Any]:
    """手写 DeepSeek credential dump（含 type 判别字段）。"""
    return {
        "type": "deepseek_credential",
        "id": credential_id,
        "name": "",
        "api_key": api_key,
        "base_url": base_url,
    }


class FakeCredential:
    """storage 中的 credential 记录最小实现。"""

    def __init__(self, credential_id: str, user_id: str, data: dict) -> None:
        self.id = credential_id
        self.user_id = user_id
        self.data = data


class FakeStorage:
    """get/upsert credential 与 session 的内存实现（带失败注入）。"""

    def __init__(self) -> None:
        self.credentials: dict[tuple[str, str], FakeCredential] = {}
        self.sessions: dict[tuple[str, str, str], Any] = {}
        self.upsert_counts: dict[tuple[str, str], int] = {}
        # id 含该 marker 的 credential upsert 时抛错（失败不阻断测试用）
        self.fail_marker: str | None = None

    def seed_credential(
        self,
        user_id: str,
        credential_id: str,
        api_key: str = "sk-x",
    ) -> None:
        self.credentials[(user_id, credential_id)] = FakeCredential(
            credential_id,
            user_id,
            _credential_data(credential_id, api_key=api_key),
        )

    async def get_credential(self, user_id: str, credential_id: str):
        return self.credentials.get((user_id, credential_id))

    async def upsert_credential(self, user_id: str, credential) -> None:
        if self.fail_marker and self.fail_marker in credential.id:
            raise RuntimeError(f"injected failure for {credential.id}")
        key = (user_id, credential.id)
        self.upsert_counts[key] = self.upsert_counts.get(key, 0) + 1
        self.credentials[key] = FakeCredential(
            credential.id,
            user_id,
            _dump_with_secrets(credential)
            if hasattr(credential, "model_dump")
            else dict(credential.data),
        )

    async def get_session(self, user_id: str, agent_id: str, session_id: str):
        return self.sessions.get((user_id, agent_id, session_id))

    async def upsert_session(
        self,
        user_id,
        agent_id,
        config,
        session_id,
    ) -> None:
        self.sessions[(user_id, agent_id, session_id)] = SimpleNamespace(
            config=config,
        )

    async def get_agent(self, user_id: str, agent_id: str):
        return None

    async def upsert_agent(self, user_id: str, record) -> None:
        return None


class FakeActiveModel:
    def __init__(self, provider_id: str, model_name: str) -> None:
        self.provider_id = provider_id
        self.model_name = model_name


class FakeRequest:
    """最小 Request 替身：app.state.provider_manager.get_active_model。"""

    def __init__(self, active: FakeActiveModel | None = None) -> None:
        manager = SimpleNamespace(get_active_model=lambda: active)
        self.app = SimpleNamespace(state=SimpleNamespace(provider_manager=manager))


class FakeWorkspaceManager:
    def assign_workspace_id(self, **kwargs) -> str:
        return "ws-test"


# ── _resolve_requested_model_name ─────────────────────────────────────


def test_resolve_requested_model_name_priority() -> None:
    """四通道按优先级取第一个非空：context > config > body > custom_params。"""
    # context 最高
    body = CreateRunRequest(
        context={"model_name": "from-context"},
        config={"configurable": {"model_name": "from-config"}},
        model_name="from-body",
        custom_params={"llm_model_name": "from-custom"},
    )
    assert _resolve_requested_model_name(body, body.custom_params) == (
        "from-context"
    )

    # config 次之
    body = CreateRunRequest(
        config={"configurable": {"model_name": "from-config"}},
        model_name="from-body",
        custom_params={"llm_model_name": "from-custom"},
    )
    assert _resolve_requested_model_name(body, body.custom_params) == (
        "from-config"
    )

    # body.model_name 再次
    body = CreateRunRequest(
        model_name="from-body",
        custom_params={"llm_model_name": "from-custom"},
    )
    assert _resolve_requested_model_name(body, body.custom_params) == (
        "from-body"
    )

    # custom_params 兜底
    body = CreateRunRequest(custom_params={"llm_model_name": "from-custom"})
    assert _resolve_requested_model_name(body, body.custom_params) == (
        "from-custom"
    )


def test_resolve_requested_model_name_empty_fallback() -> None:
    """全部缺失返回空串；空白值视为缺失。"""
    assert _resolve_requested_model_name(CreateRunRequest()) == ""
    body = CreateRunRequest(
        context={"model_name": "  "},
        config={"configurable": {"model_name": ""}},
        custom_params={},
    )
    assert _resolve_requested_model_name(body, body.custom_params) == ""


# ── ensure_default_credentials ────────────────────────────────────────


def test_ensure_default_credentials_upserts_under_default_user(
    monkeypatch,
) -> None:
    """每个条目以 deerflow-default-<provider_id> 归属 default 用户入库。"""
    entries = [_make_model_entry("ds"), _make_model_entry("ds-r1", "r1")]
    monkeypatch.setattr(
        "bocomadp.deerflow.credentials.load_models_from_yaml",
        lambda: entries,
    )
    storage = FakeStorage()

    import asyncio

    asyncio.run(ensure_default_credentials(storage))

    for entry in entries:
        record = storage.credentials[
            (DEFAULT_CREDENTIAL_OWNER, default_credential_id(entry.provider_id))
        ]
        assert record is not None
        assert record.id == default_credential_id(entry.provider_id)
    assert len(storage.credentials) == 2


def test_ensure_default_credentials_idempotent(monkeypatch) -> None:
    """重复调用走 upsert：记录不增、最新参数覆盖。"""
    entry = _make_model_entry("ds", api_key="sk-v2")
    monkeypatch.setattr(
        "bocomadp.deerflow.credentials.load_models_from_yaml",
        lambda: [entry],
    )
    storage = FakeStorage()

    import asyncio

    asyncio.run(ensure_default_credentials(storage))
    asyncio.run(ensure_default_credentials(storage))

    key = (DEFAULT_CREDENTIAL_OWNER, default_credential_id("ds"))
    assert len(storage.credentials) == 1
    assert storage.upsert_counts[key] == 2


def test_ensure_default_credentials_failure_does_not_block(
    monkeypatch,
) -> None:
    """单条目失败仅跳过该条，其余条目照常入库。"""
    entries = [_make_model_entry("bad"), _make_model_entry("good")]
    monkeypatch.setattr(
        "bocomadp.deerflow.credentials.load_models_from_yaml",
        lambda: entries,
    )
    storage = FakeStorage()
    storage.fail_marker = default_credential_id("bad")

    import asyncio

    asyncio.run(ensure_default_credentials(storage))

    assert (DEFAULT_CREDENTIAL_OWNER, default_credential_id("good")) in (
        storage.credentials
    )
    assert (DEFAULT_CREDENTIAL_OWNER, default_credential_id("bad")) not in (
        storage.credentials
    )


# ── _resolve_chat_model_config ────────────────────────────────────────


def _patch_config_loader(
    monkeypatch,
    models: list[ModelEntry],
    agents: list[AgentEntry] | None = None,
) -> None:
    monkeypatch.setattr(
        "bocomadp.deerflow.routers.deerflow_chat.load_models_from_yaml",
        lambda: models,
    )
    monkeypatch.setattr(
        "bocomadp.deerflow.routers.deerflow_chat.load_agents_from_yaml",
        lambda: agents
        if agents is not None
        else [_make_agent_entry()],
    )


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_resolve_chat_model_config_by_model_name(monkeypatch) -> None:
    """hint 命中条目 model_name：使用该条目的 provider/model。"""
    entries = [_make_model_entry("ds"), _make_model_entry("ds-r1", "r1")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()

    config = _run(
        _resolve_chat_model_config(
            storage,
            FakeRequest(),
            USER_ID,
            AGENT_ID,
            model_name_hint="r1",
        ),
    )

    assert config is not None
    assert config.model == "r1"
    assert config.credential_id == user_credential_id(USER_ID, "ds-r1")
    assert config.type == "deepseek"


def test_resolve_chat_model_config_by_provider_id(monkeypatch) -> None:
    """hint 未命中 model_name 时按 provider_id 匹配（前端传 name）。"""
    entries = [_make_model_entry("ds"), _make_model_entry("ds-r1", "r1")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()

    config = _run(
        _resolve_chat_model_config(
            storage,
            FakeRequest(),
            USER_ID,
            AGENT_ID,
            model_name_hint="ds-r1",
        ),
    )

    assert config is not None
    assert config.model == "r1"
    assert config.credential_id == user_credential_id(USER_ID, "ds-r1")


def test_resolve_chat_model_config_unmatched_falls_back_to_agent_provider(
    monkeypatch,
) -> None:
    """hint 未命中告警后回退场景绑定 provider（与 deer-flow 一致）。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()

    config = _run(
        _resolve_chat_model_config(
            storage,
            FakeRequest(),
            USER_ID,
            AGENT_ID,
            model_name_hint="nope",
        ),
    )

    assert config is not None
    assert config.model == "deepseek-chat"
    assert config.credential_id == user_credential_id(USER_ID, "ds")


def test_resolve_chat_model_config_reuses_user_credential(
    monkeypatch,
) -> None:
    """用户维度凭证已存在：直接引用，不重新 upsert。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()
    own_id = user_credential_id(USER_ID, "ds")
    storage.seed_credential(USER_ID, own_id, api_key="sk-custom")

    config = _run(
        _resolve_chat_model_config(storage, FakeRequest(), USER_ID, AGENT_ID),
    )

    assert config is not None
    assert config.credential_id == own_id
    assert (USER_ID, own_id) not in storage.upsert_counts


def test_resolve_chat_model_config_copies_from_default_credential(
    monkeypatch,
) -> None:
    """用户凭证缺失：从 default 凭证复制参数入库后引用。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()
    storage.seed_credential(
        DEFAULT_CREDENTIAL_OWNER,
        default_credential_id("ds"),
        api_key="sk-default",
    )

    config = _run(
        _resolve_chat_model_config(storage, FakeRequest(), USER_ID, AGENT_ID),
    )

    own_id = user_credential_id(USER_ID, "ds")
    assert config is not None
    assert config.credential_id == own_id
    assert storage.upsert_counts[(USER_ID, own_id)] == 1
    copied = storage.credentials[(USER_ID, own_id)]
    assert copied.data["api_key"] == "sk-default"


def test_resolve_chat_model_config_falls_back_to_entry_without_default(
    monkeypatch,
) -> None:
    """default 凭证亦缺失：回退 config.yaml 条目参数创建用户凭证。"""
    entries = [_make_model_entry("ds", api_key="sk-entry")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()

    config = _run(
        _resolve_chat_model_config(storage, FakeRequest(), USER_ID, AGENT_ID),
    )

    own_id = user_credential_id(USER_ID, "ds")
    assert config is not None
    assert config.credential_id == own_id
    created = storage.credentials[(USER_ID, own_id)]
    assert created.data["api_key"] == "sk-entry"


def test_resolve_chat_model_config_unknown_entry_returns_none(
    monkeypatch,
) -> None:
    """agent 未绑定 provider 且无 active：返回 None 不阻断。"""
    _patch_config_loader(
        monkeypatch,
        [],
        agents=[_make_agent_entry(model_provider="", model_name="")],
    )
    config = _run(
        _resolve_chat_model_config(
            FakeStorage(),
            FakeRequest(active=None),
            USER_ID,
            AGENT_ID,
        ),
    )
    assert config is None


# ── _ensure_session ───────────────────────────────────────────────────


def _run_ensure_session(
    storage: FakeStorage,
    hint: str = "",
    request: FakeRequest | None = None,
) -> None:
    _run(
        _ensure_session(
            storage,
            FakeWorkspaceManager(),
            request or FakeRequest(),
            USER_ID,
            AGENT_ID,
            "s1",
            model_name_hint=hint,
        ),
    )


def _session_key() -> tuple[str, str, str]:
    return (USER_ID, AGENT_ID, "s1")


def test_ensure_session_creates_with_backfilled_model_config(
    monkeypatch,
) -> None:
    """首次创建：session 自动补齐 chat_model_config。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()

    _run_ensure_session(storage)

    session = storage.sessions[_session_key()]
    assert session.config.workspace_id == "ws-test"
    assert session.config.chat_model_config.model == "deepseek-chat"


def test_ensure_session_updates_config_on_model_switch(
    monkeypatch,
) -> None:
    """模型切换（三元组不一致）：已有 session 的 config 被更新。"""
    entries = [_make_model_entry("ds"), _make_model_entry("ds-r1", "r1")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()
    storage.sessions[_session_key()] = SimpleNamespace(
        config=SessionConfig(
            workspace_id="ws1",
            chat_model_config=ChatModelConfig(
                type="deepseek",
                credential_id=user_credential_id(USER_ID, "ds"),
                model="deepseek-chat",
                parameters={},
            ),
        ),
    )

    _run_ensure_session(storage, hint="r1")

    session = storage.sessions[_session_key()]
    assert session.config.chat_model_config.model == "r1"
    assert session.config.chat_model_config.credential_id == user_credential_id(
        USER_ID,
        "ds-r1",
    )


def test_ensure_session_no_update_when_config_matches(monkeypatch) -> None:
    """一致三元组（HITL 续跑同 thread 模型名）：不触发更新。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()
    storage.sessions[_session_key()] = SimpleNamespace(
        config=SessionConfig(
            workspace_id="ws1",
            chat_model_config=ChatModelConfig(
                type="deepseek",
                credential_id=user_credential_id(USER_ID, "ds"),
                model="deepseek-chat",
                parameters={},
            ),
        ),
    )
    # 记录原始 config 引用，upsert 后 sessions 字典会替换条目
    before = storage.sessions[_session_key()]

    _run_ensure_session(storage)

    assert storage.sessions[_session_key()] is before


def test_ensure_session_backfills_missing_model_config(
    monkeypatch,
) -> None:
    """已有 session 但 chat_model_config 为空：backfill。"""
    entries = [_make_model_entry("ds")]
    _patch_config_loader(monkeypatch, entries)
    storage = FakeStorage()
    storage.sessions[_session_key()] = SimpleNamespace(
        config=SessionConfig(workspace_id="ws1"),
    )

    _run_ensure_session(storage)

    session = storage.sessions[_session_key()]
    assert session.config.chat_model_config.model == "deepseek-chat"


# ── GET /api/deerflow/models ──────────────────────────────────────────


def _make_models_app(storage: FakeStorage) -> FastAPI:
    api = FastAPI()
    api.state.storage = storage
    api.include_router(deerflow_models_router)
    app = FastAPI()
    app.mount("/api", api)
    return app


def test_list_models_returns_default_credential_id(monkeypatch) -> None:
    """无用户凭证时返回 default 凭证 id；X-User-ID 缺省 default 用户。"""
    entries = [_make_model_entry("ds"), _make_model_entry("ds-r1", "r1")]
    monkeypatch.setattr(
        models_module,
        "load_models_from_yaml",
        lambda: entries,
    )
    storage = FakeStorage()
    for entry in entries:
        storage.seed_credential(
            DEFAULT_CREDENTIAL_OWNER,
            default_credential_id(entry.provider_id),
        )

    with TestClient(_make_models_app(storage)) as client:
        response = client.get("/api/deerflow/models")

    assert response.status_code == 200
    body = response.json()["models"]
    assert len(body) == 2
    assert body[0]["id"] == default_credential_id("ds")
    assert body[0]["name"] == "ds"
    assert body[0]["model"] == "deepseek-chat"
    assert body[0]["supports_thinking"] is True
    assert body[0]["supports_reasoning_effort"] is False


def test_list_models_prefers_user_credential(monkeypatch) -> None:
    """用户 credential 存在：返回本用户的 id（重复则使用本用户的）。"""
    entries = [_make_model_entry("ds")]
    monkeypatch.setattr(
        models_module,
        "load_models_from_yaml",
        lambda: entries,
    )
    storage = FakeStorage()
    storage.seed_credential(
        DEFAULT_CREDENTIAL_OWNER,
        default_credential_id("ds"),
    )
    storage.seed_credential(USER_ID, user_credential_id(USER_ID, "ds"))

    with TestClient(_make_models_app(storage)) as client:
        response = client.get(
            "/api/deerflow/models",
            headers={"X-User-ID": USER_ID},
        )

    assert response.status_code == 200
    models = response.json()["models"]
    assert len(models) == 1
    assert models[0]["id"] == user_credential_id(USER_ID, "ds")


def test_list_models_skips_entry_without_any_credential(
    monkeypatch,
) -> None:
    """default 凭证亦不存在：跳过该条目并告警。"""
    entries = [_make_model_entry("ds")]
    monkeypatch.setattr(
        models_module,
        "load_models_from_yaml",
        lambda: entries,
    )
    storage = FakeStorage()

    with TestClient(_make_models_app(storage)) as client:
        response = client.get("/api/deerflow/models")

    assert response.status_code == 200
    assert response.json() == {"models": []}
