"""Regression tests for private tool login and read retry."""

import asyncio
import unittest

import httpx

from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.tool_login import (
    LoginConflict, LoginEnded, LoginFailed, LoginField, LoginRejected, LoginStep,
    LoginSuccess, ToolLogin, tool_login_scope,
)
from agentscope.business._password_auth import PasswordConnection, PasswordProfile


class ToolLoginTest(unittest.IsolatedAsyncioTestCase):
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
