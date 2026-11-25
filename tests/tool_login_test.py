"""Regression tests for private tool login and read retry."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from agentscope.app._router._tool_login import tool_login_router
from agentscope.app.deps import get_storage
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.tool_login import (
    LoginConflict, LoginEnded, LoginFailed, LoginField, LoginRejected, LoginStep,
    LoginSuccess, ToolLogin, tool_login_scope,
)
from agentscope.business._password_auth import PasswordConnection, PasswordProfile


class ToolLoginTest(unittest.IsolatedAsyncioTestCase):
    def test_password_connection_requires_https_off_loopback(self):
        settings = dict(broker=ToolLogin(InMemoryMessageBus()), service="test",
                        title="Test", previous_password=lambda: "", context={})
        for url in ("http://localhost:8080", "http://[::1]/api", "https://erp.example/api"):
            PasswordConnection(base_url=url, **settings)
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            PasswordConnection(base_url="http://erp.example/api", **settings)

    async def test_another_worker_serves_the_private_login_routes(self):
        bus = InMemoryMessageBus()
        owner, gateway = ToolLogin(bus, timeout=3), ToolLogin(bus, timeout=3)
        app = FastAPI()
        app.include_router(tool_login_router)
        app.state.tool_login = gateway
        app.dependency_overrides[get_storage] = lambda: AsyncMock()
        scope = ("user", "agent", "session")
        path = "/sessions/session/tool-login"
        step = LoginStep(title="Login", description="", submit_label="Continue",
                         fields=[LoginField(name="password", label="Password", kind="secret")])
        started = asyncio.Event()

        async def complete(_, values):
            if values["password"] == "step-one":
                return step
            if values["password"] == "wait":
                started.set()
                await asyncio.Future()
            if values["password"] == "mistyped":
                raise LoginRejected("Try again")
            return LoginSuccess("private-session")

        async def wait_for_form(client):
            async with asyncio.timeout(2):
                while True:
                    form = (await client.get(path)).json()
                    if form:
                        return form["id"]
                    await asyncio.sleep(0)

        with tool_login_scope(*scope), patch.object(
            bus, "registry_set", wraps=bus.registry_set,
        ) as stored, patch.object(bus, "log_append", wraps=bus.log_append) as replayed:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test", params={"agent_id": "agent"},
                                         headers={"X-User-ID": "user"}) as client:
                run = asyncio.create_task(owner.request("erp", step, complete))
                form = path + "/" + await wait_for_form(client)

                async def submit(value, revision=0):
                    return await client.post(form, json={"revision": revision, "values": {"password": value}})

                self.assertIsNone((await client.get(path, headers={"X-User-ID": "other"})).json())
                self.assertEqual((await client.delete(form, headers={"X-User-ID": "other"})).status_code, 404)
                self.assertEqual((await submit("step-one")).status_code, 204)
                self.assertEqual((await client.get(path)).json()["revision"], 1)
                self.assertEqual((await submit("old-step")).status_code, 409)
                self.assertEqual((await submit("mistyped", 1)).status_code, 400)
                self.assertEqual((await submit("secret-fixture", 1)).status_code, 204)
                self.assertEqual(await run, "private-session")
                self.assertIsNone((await client.get(path)).json())

                run = asyncio.create_task(owner.request("erp", step, complete))
                form = path + "/" + await wait_for_form(client)
                slow = asyncio.create_task(submit("wait"))
                await asyncio.wait_for(started.wait(), 1)
                self.assertEqual((await submit("duplicate")).status_code, 409)
                self.assertEqual((await client.delete(form)).status_code, 204)
                with self.assertRaises(LoginEnded):
                    await run
                self.assertEqual((await slow).status_code, 404)
        for private in ("secret-fixture", "private-session"):
            self.assertNotIn(private, str(stored.await_args_list))
            self.assertNotIn(private, str(replayed.await_args_list))

    async def test_steps_and_session_isolation(self):
        broker = ToolLogin(InMemoryMessageBus())
        scope = ("user", "agent", "session")
        step = LoginStep(
            title="Service", description="Sign in", submit_label="Next",
            fields=[LoginField(name="account", label="Account")],
        )
        password_step = step.model_copy(update={
            "fields": [LoginField(name="password", label="Password", kind="secret")],
        })

        async def complete(current, values):
            if current is step:
                return password_step
            if values["password"] == "incorrect":
                raise LoginRejected("Incorrect password.")
            return LoginSuccess("session token")

        with tool_login_scope(*scope):
            operation = asyncio.create_task(broker.request("service", step, complete))
            await asyncio.sleep(0)
            view = broker.pending(scope)
            self.assertIsNotNone(view)
            foreign = ("someone else", *scope[1:])
            self.assertIsNone(broker.pending(foreign))
            with self.assertRaises(LoginEnded):
                await broker.submit(foreign, view.id, 0, {"account": "test"})
            await broker.submit(scope, view.id, 0, {"account": "test"})
            self.assertEqual(broker.pending(scope).revision, 1)
            with self.assertRaises(LoginConflict):
                await broker.submit(scope, view.id, 0, {"account": "test"})
            with self.assertRaises(LoginRejected):
                await broker.submit(scope, view.id, 1, {"password": "incorrect"})
            self.assertFalse(operation.done())
            self.assertEqual(broker.pending(scope).revision, 1)
            await broker.submit(scope, view.id, 1, {"password": "test-only"})
            self.assertEqual(await operation, "session token")
            self.assertIsNone(broker.pending(scope))

    async def test_cancelled_tool_removes_pending_challenge(self):
        broker = ToolLogin(InMemoryMessageBus())
        scope = ("user", "agent", "session")
        step = LoginStep(title="Service", description="", submit_label="Continue", fields=[])

        async def complete(_step, _values):
            self.fail("A cancelled form should not call the service")

        with tool_login_scope(*scope):
            operation = asyncio.create_task(broker.request("service", step, complete))
            await asyncio.sleep(0)
            view = broker.pending(scope)
            operation.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await operation
            self.assertIsNone(broker.pending(scope))
            with self.assertRaises(LoginEnded):
                await broker.submit(scope, view.id, 0, {})

    async def test_adapter_configuration_failure_returns_control_to_tool(self):
        broker = ToolLogin(InMemoryMessageBus())
        scope = ("user", "agent", "session")
        step = LoginStep(title="Service", description="", submit_label="Continue", fields=[])

        async def complete(_step, _values):
            raise LoginFailed("Provider returned an unrecognized session structure.")

        with tool_login_scope(*scope):
            operation = asyncio.create_task(broker.request("service", step, complete))
            await asyncio.sleep(0)
            view = broker.pending(scope)
            with self.assertRaises(LoginFailed):
                await broker.submit(scope, view.id, 0, {})
            with self.assertRaisesRegex(LoginFailed, "unrecognized session structure"):
                await operation
            self.assertIsNone(broker.pending(scope))

    async def test_service_controls_expiry_and_only_get_is_retried(self):
        calls = []
        logins = iter(["stale", "current"])

        async def upstream(request):
            calls.append((request.method, str(request.url), request.headers.get("authorization")))
            if request.method == "POST":
                token = next(logins)
                return httpx.Response(201, json={"session": {"key": token}})
            if request.headers["authorization"] == "Bearer stale":
                return httpx.Response(200, json={"state": "refresh"})
            return httpx.Response(200, json={"state": "success", "items": [1]})

        profile = PasswordProfile.model_validate({
            "login_path": "/session", "request_fields": {"secret": "password"},
            "accepted": {"status": 201}, "rejected": [{"status": 422}],
            "expired": [{"status": 200, "fields": {"state": "refresh"}}],
            "token_path": "session.key", "session_path": "session",
            "client_route": "/list", "query_path": "/items",
            "evidence": [{"path": "app.ts", "start": 1, "end": 10}] * 3,
        })
        connection = PasswordConnection(
            broker=ToolLogin(InMemoryMessageBus()), service="example", title="Example",
            base_url="https://example.test/api", context={},
            previous_password=lambda: "fixture",
            transport=httpx.MockTransport(upstream),
        )
        with tool_login_scope("user", "agent", "session"):
            result = await connection.read(profile, {"page": 3})
        self.assertEqual(result.json()["items"], [1])
        self.assertEqual([method for method, _, _ in calls], ["POST", "GET", "POST", "GET"])
        self.assertEqual(calls[1][1], calls[3][1])
        self.assertFalse(profile.session_expired(httpx.Response(403, json={"state": "denied"})))
        self.assertTrue(profile.credentials_rejected(httpx.Response(422)))
