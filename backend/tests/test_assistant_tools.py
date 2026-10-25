import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from assistant_tools import LIVE_TOOLS, execute_tool_call, respond_to_tool_calls
from google.genai import types
from main import LIVE_SYSTEM_INSTRUCTION
from orders import get_order_details
from policies import (
    get_policy_details,
    get_return_policy_details,
    get_shipping_policy,
    is_cod_eligible,
)


class AssistantToolTests(unittest.TestCase):
    def test_order_101_details_include_every_available_field(self) -> None:
        result = execute_tool_call("get_order_details", {"order_id": "101"})

        self.assertTrue(result["found"])
        self.assertEqual(result["requested_order_id"], "ORD-101")
        self.assertEqual(
            result["order"],
            {
                "order_id": "ORD-101",
                "customer_name": "Priya Sharma",
                "product": "Vitamin C Serum (30ml)",
                "amount_inr": 699,
                "status": "Out for Delivery",
                "carrier": "BlueDart",
                "tracking_number": "BD-982103",
                "expected_delivery": "by 6 PM today",
            },
        )
        self.assertFalse(result["payment_status_available"])
        self.assertIn("not available in the sample data", result["payment_status_message"])

    def test_order_102_shorthand_returns_available_fields(self) -> None:
        result = execute_tool_call("get_order_details", {"order_id": "order 102"})

        self.assertTrue(result["found"])
        self.assertEqual(
            result["order"],
            {
                "order_id": "ORD-102",
                "customer_name": "Rahul Verma",
                "product": "Hydrating Sunscreen SPF 50",
                "amount_inr": 499,
                "status": "Delivered",
                "delivered_days_ago": 14,
                "carrier": "Delhivery",
                "tracking_number": "DL-441029",
            },
        )

    def test_order_103_shorthand_returns_processing_and_cancellation_eligibility(self) -> None:
        result = execute_tool_call("get_order_details", {"order_id": "Order 103"})

        self.assertTrue(result["found"])
        self.assertEqual(result["order"]["status"], "Processing")
        self.assertEqual(result["order"]["ordered_hours_ago"], 3)
        self.assertTrue(result["cancellation_eligible"])

    def test_ord_101_tracking_question_uses_current_order_data(self) -> None:
        result = execute_tool_call("get_order_details", {"order_id": "ORD-101"})
        self.assertTrue(result["found"])
        self.assertEqual(result["order"]["tracking_number"], "BD-982103")
        self.assertEqual(result["order"]["carrier"], "BlueDart")
        self.assertFalse(result["cancellation_eligible"])

    def test_unknown_order_returns_a_clear_not_found_result(self) -> None:
        result = execute_tool_call("get_order_details", {"order_id": "ORD-999"})
        self.assertEqual(result["found"], False)
        self.assertEqual(result["order_id"], "ORD-999")
        self.assertIn("not found", result["error"].lower())

    def test_ord_103_cancellation_question_reports_eligibility_without_action(self) -> None:
        result = execute_tool_call(
            "check_cancellation_eligibility",
            {"order_id": "ORD-103"},
        )
        self.assertTrue(result["cancellation_eligible"])
        self.assertFalse(result["action_taken"])
        self.assertIn("No cancellation has been performed", result["message"])

    def test_ord_101_cancellation_question_is_not_eligible(self) -> None:
        result = execute_tool_call(
            "check_cancellation_eligibility",
            {"order_id": "ORD-101"},
        )
        self.assertFalse(result["cancellation_eligible"])
        self.assertFalse(result["action_taken"])
        self.assertIn("refuse delivery at the doorstep", result["message"])

    def test_ord_102_return_question_fails_14_day_window(self) -> None:
        result = execute_tool_call(
            "check_return_eligibility",
            {"order_id": "ORD-102"},
        )
        self.assertEqual(result["delivered_days_ago"], 14)
        self.assertFalse(result["return_eligible"])
        self.assertIn("No return or refund has been processed", result["message"])

    def test_in_window_return_requires_customer_condition_confirmation(self) -> None:
        with patch(
            "assistant_tools.get_order_details",
            return_value={"order_id": "ORD-104", "status": "Delivered", "delivered_days_ago": 2},
        ):
            result = execute_tool_call(
                "check_return_eligibility",
                {"order_id": "ORD-104", "unopened": True},
            )
        self.assertFalse(result["eligibility_checked"])
        self.assertIn("Ask the customer to confirm", result["message"])

    def test_general_policy_question_uses_configured_policy_data(self) -> None:
        result = execute_tool_call("get_policy_information", {})
        self.assertEqual(result["policies"], get_policy_details())

    def test_cod_maximum_is_inclusive(self) -> None:
        self.assertTrue(is_cod_eligible(2499))
        self.assertTrue(is_cod_eligible(2500))
        self.assertFalse(is_cod_eligible(2501))

        result = execute_tool_call(
            "check_cash_on_delivery_eligibility",
            {"order_amount_inr": 2500},
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(
            result["maximum_order_amount_inr"],
            get_policy_details()["cash_on_delivery"]["maximum_order_amount_inr"],
        )
        self.assertTrue(result["maximum_is_inclusive"])

    def test_return_tool_result_includes_complete_return_and_damage_rules(self) -> None:
        result = execute_tool_call("check_return_eligibility", {"order_id": "ORD-102"})
        policy = result["policy"]
        self.assertEqual(policy, get_return_policy_details())
        self.assertEqual(policy["returns"]["window_days_from_delivery"], 7)
        self.assertEqual(
            policy["returns"]["conditions"],
            [
                "Product must be unopened.",
                "Product must be unused.",
                "Product must be in its original packaging.",
            ],
        )
        self.assertEqual(
            policy["damaged_or_defective_products"]["report_within_hours_of_delivery"],
            48,
        )
        self.assertTrue(policy["damaged_or_defective_products"]["photo_evidence_required"])
        self.assertFalse(result["return_eligible"])

    def test_payment_status_is_reported_unavailable_for_mock_orders(self) -> None:
        result = execute_tool_call(
            "check_payment_status",
            {"order_id": "ORD-101"},
        )
        self.assertTrue(result["found"])
        self.assertFalse(result["payment_status_available"])
        self.assertNotIn("payment_status", result)
        self.assertIn("not available", result["message"])

    def test_all_sample_orders_remain_a_general_listing(self) -> None:
        result = execute_tool_call("get_all_sample_orders", {})
        self.assertEqual(
            [order["order_id"] for order in result["orders"]],
            ["ORD-101", "ORD-102", "ORD-103"],
        )
        self.assertEqual(
            set(result["orders"][0]),
            {"order_id", "product", "amount_inr", "status"},
        )
        self.assertNotIn("customer_name", result["orders"][0])

    def test_order_details_prompt_discloses_returned_mock_fields(self) -> None:
        self.assertIn("including customer_name when present", LIVE_SYSTEM_INSTRUCTION)
        self.assertIn("Normalize shorthand like 'order 101' to ORD-101", LIVE_SYSTEM_INSTRUCTION)
        self.assertNotIn("without customer names", LIVE_SYSTEM_INSTRUCTION)

        declarations = LIVE_TOOLS[0].function_declarations
        order_tool = next(tool for tool in declarations if tool.name == "get_order_details")
        self.assertIn("disclose every field returned", order_tool.description)
        self.assertIn("customer_name", order_tool.description)

    def test_persona_requires_explicit_language_changes(self) -> None:
        self.assertIn("brief acknowledgment such as “Sí”", LIVE_SYSTEM_INSTRUCTION)
        self.assertIn("continue in Hindi until an explicit language change", LIVE_SYSTEM_INSTRUCTION)

    def test_shipping_fee_boundary_is_explicitly_unspecified(self) -> None:
        self.assertEqual(get_shipping_policy(500)["shipping_fee_inr"], 0)
        self.assertEqual(get_shipping_policy(498)["shipping_fee_inr"], 50)
        boundary = get_shipping_policy(499)
        self.assertEqual(boundary["status"], "unspecified_boundary")
        self.assertIsNone(boundary["shipping_fee_inr"])

    def test_order_lookup_tool_is_registered_with_order_id_string(self) -> None:
        declarations = LIVE_TOOLS[0].function_declarations
        order_tool = next(tool for tool in declarations if tool.name == "get_order_details")
        self.assertEqual(order_tool.parameters.properties["order_id"].type, types.Type.STRING)
        self.assertIn("order_id", order_tool.parameters.required)


class ToolResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_tool_calls_are_returned_to_the_same_session(self) -> None:
        session = SimpleNamespace(send_tool_response=AsyncMock())
        tool_call = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    id="call-order",
                    name="get_order_details",
                    args={"order_id": "ORD-101"},
                ),
                SimpleNamespace(
                    id="call-policy",
                    name="get_policy_information",
                    args={},
                ),
            ]
        )

        await respond_to_tool_calls(session, tool_call)

        session.send_tool_response.assert_awaited_once()
        responses = session.send_tool_response.await_args.kwargs["function_responses"]
        self.assertEqual([response.id for response in responses], ["call-order", "call-policy"])
        self.assertEqual(
            responses[0].response["result"]["order"]["tracking_number"],
            get_order_details("ORD-101")["tracking_number"],
        )
        self.assertEqual(
            responses[1].response["result"]["policies"],
            get_policy_details(),
        )

    async def test_tool_error_is_returned_without_skipping_response(self) -> None:
        session = SimpleNamespace(send_tool_response=AsyncMock())
        tool_call = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    id="call-unknown",
                    name="not_a_tool",
                    args={},
                )
            ]
        )

        await respond_to_tool_calls(session, tool_call)

        response = session.send_tool_response.await_args.kwargs["function_responses"][0]
        self.assertIn("Unknown tool", response.response["result"]["error"])

    async def test_missing_call_id_is_reported_and_not_sent(self) -> None:
        session = SimpleNamespace(send_tool_response=AsyncMock())
        tool_call = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    id=None,
                    name="get_order_details",
                    args={"order_id": "ORD-101"},
                )
            ]
        )

        with self.assertRaisesRegex(ValueError, "did not include a response ID"):
            await respond_to_tool_calls(session, tool_call)
        session.send_tool_response.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
