"""Offline tests for discovering published tools through normal text chat."""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from utils import MockModel

from agentscope.agent import Agent, ReActConfig
from agentscope.business import BusinessRegistry, SaveBusinessRegistry
from agentscope.event import RequireUserConfirmEvent
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.permission import PermissionMode, PermissionRule
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolGroup, Toolkit

SEARCH = "search_business_tools"
QUERY = "erp_listPurchaseReceipts"
SAVE = "erp_createPurchaseReceipt"
READBACK = "erp_readPurchaseReceipt"
SALES = "erp_listSalesOrders"


def names(schemas):
    return [schema["function"]["name"] for schema in schemas]


async def search(toolkit, state, query):
    search_tool = await toolkit.check_tool_available(
        SEARCH, state.tool_context.activated_groups
    )
    result = await search_tool(query=query, _agent_state=state)
    return [
        match["name"]
        for match in json.loads(result.content[0].text)["matches"]
    ]


def call(name, **arguments):
    return ChatResponse(
        content=[
            ToolCallBlock(
                id=f"call-{name}",
                name=name,
                input=json.dumps(arguments),
            )
        ],
        is_last=True,
    )


class BusinessToolSearchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.spec = self.root / "spec.json"
        self.spec.write_text("{}")
        self.network = AsyncMock(
            side_effect=AssertionError("Unexpected ERP access")
        )
        self.queries = BusinessRegistry(
            self.root,
            self.spec,
            self.root / "queries",
            "https://erp.invalid",
            self.network,
            self.network,
        )
        self.saves = SaveBusinessRegistry(
            self.root,
            self.spec,
            self.root / "saves",
            self.network,
            self.network,
            target_label="离线账套",
        )
        self.publish_query(
            QUERY, "采购入库单", "supplierBatch", "按送货凭证核对"
        )
        self.publish_query(SALES, "销售订单", "customerNumber", "列出销售订单")
        self.publish_query(
            "erp_privatePayroll", "工资单", "salary", "", owner="other"
        )
        self.publish_query(
            "erp_otherAgent", "差旅报销", "cost", "", agent="other"
        )
        directory = (
            self.saves.storage / self.queries._directory("owner", "agent").name
        )
        directory.mkdir(parents=True)
        self.save_path = directory / "purchase.json"
        self.save_path.write_text(
            json.dumps(
                {
                    "plan": {
                        "business": "采购入库单",
                        "saveTool": SAVE,
                        "readTool": READBACK,
                        "targetLabel": "离线账套",
                        "fields": ["purpose"],
                        "detailRoute": "/purchase/{id}",
                        "ruleHashes": {},
                        "apiSha256": hashlib.sha256(b"{}").hexdigest(),
                    },
                    "verification": {"version": 3, "registered": True},
                }
            )
        )
        self.toolkit = self.kit()
        self.state = AgentState()

    def tearDown(self):
        self.network.assert_not_called()

    def publish_query(
        self, name, title, field, description, owner="owner", agent="agent"
    ):
        directory = self.queries._directory(owner, agent)
        directory.mkdir(parents=True, exist_ok=True)
        child = {
            "type": "object",
            "properties": {
                field: {"type": "string", "examples": ["咖啡豆烘焙"]}
            },
        }
        schema = {
            "type": "object",
            "properties": {"filters": {"type": "array", "items": child}},
        }
        (directory / f"{name}.json").write_text(
            json.dumps(
                {
                    "artifact": {
                        "operation": {
                            "name": name,
                            "description": description,
                        },
                        "inputSchema": schema,
                    },
                    "verification": {"business": title},
                }
            )
        )

    def kit(self):
        return Toolkit(
            tools=self.queries.tools("owner", "agent")
            + self.saves.tools("owner", "agent")
        )

    async def test_employee_phrasing_and_no_match(self):
        cases = [
            ("帮我查一下最近的采购入库单", QUERY),
            ("看一下销售订单", SALES),
            ("给我开一张采购入库单", SAVE),
            ("按操作 ID 回查采购入库单", READBACK),
            ("恢复上次保存的采购入库单", READBACK),
            ("查已保存的采购入库单", QUERY),
            ("别新增，只查采购入库单", QUERY),
            ("find sales orders", SALES),
            ("按 supplier_batch 筛选", QUERY),
            ("按 operation_id 回查", READBACK),
            ("按送货凭证核对", QUERY),
            (SAVE, SAVE),
        ]
        for phrase, expected in cases:
            with self.subTest(phrase=phrase):
                matches = await search(self.toolkit, self.state, phrase)
                self.assertEqual(matches[0], expected)
                self.assertLessEqual(len(matches), 3)
                if expected != SAVE:
                    self.assertNotIn(SAVE, matches)
        for phrase in ("工资单", "差旅报销", "咖啡豆烘焙"):
            self.assertEqual(
                await search(self.toolkit, self.state, phrase), []
            )
        self.assertEqual(self.state.tool_context.selected_business_tools, [])

    async def test_schemas_follow_session_permissions_group_and_publication(
        self,
    ):
        initial = names(await self.toolkit.get_tool_schemas(state=self.state))
        self.assertCountEqual(
            initial,
            [SEARCH, "register_business_tool", "register_save_business_tool"],
        )
        eager = names(await self.toolkit.get_tool_schemas())
        self.assertIn(QUERY, eager)
        self.assertNotIn(SEARCH, eager)
        await search(self.toolkit, self.state, QUERY)
        restored = AgentState.model_validate_json(self.state.model_dump_json())
        self.assertIn(
            QUERY, names(await self.kit().get_tool_schemas(state=restored))
        )
        self.assertNotIn(
            QUERY, names(await self.kit().get_tool_schemas(state=AgentState()))
        )
        self.state.permission_context.deny_rules[QUERY] = [
            PermissionRule(
                tool_name=QUERY,
                rule_content=None,
                behavior="deny",
                source="test",
            )
        ]
        self.assertNotIn(
            QUERY, names(await self.toolkit.get_tool_schemas(state=self.state))
        )
        self.assertNotIn(QUERY, await search(self.toolkit, self.state, QUERY))
        for mode in (PermissionMode.EXPLORE, PermissionMode.DONT_ASK):
            self.state.permission_context.mode = mode
            self.assertNotIn(
                SAVE, await search(self.toolkit, self.state, SAVE)
            )
        self.state.permission_context.mode = PermissionMode.DEFAULT
        group = ToolGroup(
            name="purchase",
            description="采购单据",
            tools=self.saves.tools("owner", "agent")[1:],
        )
        grouped = Toolkit(
            tools=self.queries.tools("owner", "agent"), tool_groups=[group]
        )
        self.assertNotIn(SAVE, await search(grouped, self.state, SAVE))
        self.state.tool_context.activated_groups = ["purchase"]
        self.assertEqual(await search(grouped, self.state, SAVE), [SAVE])
        self.assertIn(
            SAVE, names(await grouped.get_tool_schemas(state=self.state))
        )
        self.assertNotIn(
            SAVE, names(await grouped.get_tool_schemas([], state=self.state))
        )
        self.spec.write_text("changed")
        self.assertNotIn(
            SAVE, names(await self.kit().get_tool_schemas(state=self.state))
        )
        self.assertEqual(
            await search(self.kit(), self.state, READBACK), [READBACK]
        )
        self.saves.target_label = "另一账套"
        self.assertNotIn(
            READBACK,
            names(await self.kit().get_tool_schemas(state=self.state)),
        )
        self.assertNotIn(
            READBACK, await search(self.kit(), self.state, READBACK)
        )

    async def test_text_agent_calls_the_original_query_with_discovered_parameters(
        self,
    ):
        model = MockModel(context_size=16000)
        agent = Agent(
            name="worker",
            system_prompt="查询业务",
            model=model,
            toolkit=self.toolkit,
        )
        get_token = AsyncMock(return_value="fake-token")
        lookup = AsyncMock(
            return_value={"status": 200, "result": ["离线订单"]}
        )
        self.queries.get_token = get_token
        filters = [{"customerNumber": "100"}]
        model.set_responses(
            [
                call(SEARCH, query="查销售订单"),
                call(SALES, filters=filters),
                ChatResponse(
                    content=[TextBlock(text="已找到订单")], is_last=True
                ),
            ]
        )
        with (
            patch(
                "agentscope.business._registry.as_tool", return_value=lookup
            ),
            patch.object(model, "_call_api", wraps=model._call_api) as request,
        ):
            await agent.reply(UserMsg("owner", "查销售订单"))
        offered = [item.kwargs["tools"] for item in request.await_args_list]
        self.assertNotIn(SALES, names(offered[0]))
        schema = next(
            s["function"] for s in offered[1] if s["function"]["name"] == SALES
        )
        self.assertIn(
            "customerNumber",
            schema["parameters"]["properties"]["filters"]["items"][
                "properties"
            ],
        )
        get_token.assert_awaited_once_with()
        lookup.assert_awaited_once_with(filters=filters)

    async def test_text_agent_searches_then_asks_for_bound_save_confirmation(
        self,
    ):
        model = MockModel(context_size=16000)
        agent = Agent(
            name="worker",
            system_prompt="办理业务",
            model=model,
            toolkit=self.toolkit,
        )
        model.set_responses(
            [call(SEARCH, query="新建采购入库单"), call(SAVE, purpose="样例")]
        )
        with patch.object(
            model, "_call_api", wraps=model._call_api
        ) as request:
            prompts = [
                evt
                async for evt in agent.reply_stream(
                    UserMsg("owner", "新建采购入库单")
                )
                if isinstance(evt, RequireUserConfirmEvent)
            ]
        offered = [
            names(item.kwargs["tools"]) for item in request.await_args_list
        ]
        self.assertIn(SEARCH, offered[0])
        self.assertNotIn(SAVE, offered[0])
        self.assertIn(SAVE, offered[1])
        self.assertNotIn(SALES, offered[1])
        self.assertEqual(len(prompts), 1)
        pending = prompts[0].tool_calls[0]
        self.assertTrue(pending.confirmation_id)
        self.assertIn("离线账套", pending.confirmation_message)

    async def test_search_counts_toward_the_reply_cap(self):
        model = MockModel(context_size=16000)
        agent = Agent(
            name="worker",
            system_prompt="办理业务",
            model=model,
            toolkit=self.toolkit,
            react_config=ReActConfig(
                max_iters=1, auto_continue=True, max_total_iters=2
            ),
        )
        model.set_responses(
            [
                call(SEARCH, query="新建采购入库单"),
                ChatResponse(
                    content=[TextBlock(text="已找到工具")], is_last=True
                ),
            ]
        )
        with patch.object(
            model, "_call_api", wraps=model._call_api
        ) as request:
            await agent.reply(UserMsg("owner", "新建采购入库单"))
        self.assertEqual(
            agent.state.tool_context.selected_business_tools[0], SAVE
        )
        self.assertEqual(agent.state.reply_context.total_iters, 2)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args.kwargs["tool_choice"].mode, "none")

    async def test_schema_cost_is_bounded_across_repeated_searches(self):
        for i in range(26):
            self.publish_query(
                f"erp_report_{i}",
                f"费用报表{i}",
                f"accountNumber{i}",
                "查询费用汇总",
            )
        toolkit = self.kit()
        model = MockModel()
        eager = await toolkit.get_tool_schemas()
        initial = await toolkit.get_tool_schemas(state=self.state)
        matches = await search(toolkit, self.state, "查费用报表")
        self.assertEqual(len(matches), 3)
        self.assertEqual(
            matches, await search(toolkit, self.state, "查费用报表")
        )
        await search(toolkit, self.state, SALES)
        selected = await toolkit.get_tool_schemas(state=self.state)
        self.assertEqual(set(names(selected)) - set(names(initial)), {SALES})
        eager_cost = await model.count_tokens([], eager)
        for schemas in (initial, selected):
            self.assertLess(
                await model.count_tokens([], schemas), eager_cost / 2
            )

    async def test_search_does_not_shadow_an_existing_tool(self):
        def existing():
            return "original"

        standalone = FunctionTool(existing, name=SEARCH)
        toolkit = Toolkit(
            tools=[standalone, *self.queries.tools("owner", "agent")]
        )
        self.assertIs(await toolkit.get_tool(SEARCH), standalone)
        self.assertIn(
            f"{SEARCH}_2",
            names(await toolkit.get_tool_schemas(state=self.state)),
        )
