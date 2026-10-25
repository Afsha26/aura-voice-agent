import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketState

import main
from call_summary import (
    CallSummary,
    SummaryGenerationError,
    TranscriptEntry,
    _generate_summary_text,
    generate_call_summary,
    summary_response_json_schema,
)


def transcript(role: str, text: str) -> TranscriptEntry:
    return TranscriptEntry(
        role=role,
        text=text,
        timestamp="2026-10-25T00:00:00+00:00",
    )


def valid_summary(
    *,
    customer_intent: str = "Check order status",
    order_id: str | None = "ORD-101",
    resolution_status: str = "resolved",
    call_summary: str = "The customer asked about order ORD-101.",
) -> str:
    return json.dumps(
        {
            "customer_intent": customer_intent,
            "order_id": order_id,
            "resolution_status": resolution_status,
            "call_summary": call_summary,
        }
    )


class CallSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch("call_summary.dotenv_values", return_value={"GEMINI_API_KEY": "test-key"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_normal_conversation_includes_a_verified_order(self) -> None:
        entries = [
            transcript("user", "Where is order ORD-101?"),
            transcript("assistant", "It is out for delivery with BlueDart."),
        ]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(),
        ):
            summary = generate_call_summary(entries, [])

        self.assertEqual(summary.order_id, "ORD-101")
        self.assertEqual(summary.resolution_status, "resolved")

    def test_multiple_order_ids_are_limited_to_verified_sample_orders(self) -> None:
        entries = [
            transcript("user", "Check ORD-101 and ORD-103, please."),
            transcript("assistant", "ORD-103 is still processing."),
        ]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(order_id="ORD-999"),
        ) as generate_text:
            summary = generate_call_summary(entries, [])

        self.assertEqual(summary.order_id, "ORD-101")
        prompt = generate_text.call_args.args[1]
        self.assertIn("['ORD-101', 'ORD-103']", prompt)

    def test_conversation_without_an_order_id_returns_null(self) -> None:
        entries = [transcript("user", "What is your delivery policy?")]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(order_id="ORD-101"),
        ):
            summary = generate_call_summary(entries, [])

        self.assertIsNone(summary.order_id)

    def test_general_sample_order_listing_does_not_assign_an_order_to_the_call(self) -> None:
        entries = [transcript("user", "What sample products are available?")]
        tool_results = [
            {
                "name": "get_all_sample_orders",
                "result": {
                    "orders": [
                        {"order_id": "ORD-101"},
                        {"order_id": "ORD-102"},
                        {"order_id": "ORD-103"},
                    ]
                },
            }
        ]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(order_id=None),
        ):
            summary = generate_call_summary(entries, tool_results)

        self.assertIsNone(summary.order_id)

    def test_empty_call_uses_safe_summary_without_api_call(self) -> None:
        with patch("call_summary._generate_summary_text") as generate_text:
            summary = generate_call_summary([], [])

        self.assertEqual(summary.resolution_status, "unresolved")
        self.assertIsNone(summary.order_id)
        self.assertIn("No conversational transcript", summary.call_summary)
        generate_text.assert_not_called()

    def test_cancellation_eligibility_is_not_reported_as_an_action(self) -> None:
        entries = [transcript("user", "Can you cancel ORD-103?")]
        tool_results = [
            {
                "name": "check_cancellation_eligibility",
                "result": {
                    "order_id": "ORD-103",
                    "cancellation_eligible": True,
                    "action_taken": False,
                },
            }
        ]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(
                customer_intent="Ask whether ORD-103 can be cancelled",
                resolution_status="partially_resolved",
                call_summary=(
                    "The order is eligible to request cancellation, but no cancellation "
                    "was performed."
                ),
            ),
        ) as generate_text:
            summary = generate_call_summary(entries, tool_results)

        self.assertEqual(summary.resolution_status, "partially_resolved")
        self.assertIn("Eligibility checks are not actions", generate_text.call_args.args[1])

    def test_tool_result_can_verify_an_order_missing_from_transcription(self) -> None:
        entries = [transcript("user", "Can you check my order?")]
        tool_results = [
            {
                "name": "get_order_details",
                "result": {
                    "found": True,
                    "order": {"order_id": "ORD-103", "status": "Processing"},
                },
            }
        ]
        with patch(
            "call_summary._generate_summary_text",
            return_value=valid_summary(order_id="ORD-103"),
        ):
            summary = generate_call_summary(entries, tool_results)

        self.assertEqual(summary.order_id, "ORD-103")

    def test_invalid_summary_json_is_reported(self) -> None:
        with patch("call_summary._generate_summary_text", return_value="not json"):
            with self.assertRaisesRegex(SummaryGenerationError, "valid call summary"):
                generate_call_summary([transcript("user", "Hello")], [])

    def test_model_generation_failure_is_reported(self) -> None:
        with patch(
            "call_summary._generate_summary_text",
            side_effect=SummaryGenerationError("Gemini quota error."),
        ):
            with self.assertRaisesRegex(SummaryGenerationError, "quota"):
                generate_call_summary([transcript("user", "Hello")], [])

    def test_schema_restricts_resolution_status_and_extra_fields(self) -> None:
        CallSummary(
            customer_intent="Check an order",
            order_id="ORD-101",
            resolution_status="resolved",
            call_summary="The order status was provided.",
        )
        with self.assertRaises(ValidationError):
            CallSummary(
                customer_intent="Check an order",
                order_id="ORD-101",
                resolution_status="cancelled",
                call_summary="An invalid status is rejected.",
            )

    def test_duplicate_and_overlapping_streamed_fragments_are_merged(self) -> None:
        merge = main._merge_transcription_text
        text = merge("", "Where is")
        text = merge(text, "Where is")
        text = merge(text, "is my order ORD-101?")
        self.assertEqual(text, "Where is my order ORD-101?")
        self.assertEqual(merge("Hello", " world."), "Hello world.")
        self.assertEqual(merge("Hello", ","), "Hello,")

    def test_gemini_json_schema_uses_supported_option_without_response_schema(self) -> None:
        schema = summary_response_json_schema()
        config = main.types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=schema,
        )
        request_config = config.model_dump(by_alias=True, exclude_none=True)

        self.assertIn("responseJsonSchema", request_config)
        self.assertNotIn("responseSchema", request_config)
        self.assertNotIn("additionalProperties", json.dumps(schema))
        self.assertEqual(
            schema["properties"]["resolution_status"]["enum"],
            ["resolved", "partially_resolved", "unresolved"],
        )
        self.assertTrue(schema["properties"]["order_id"]["nullable"])

    def test_summary_request_sends_supported_response_json_schema_field(self) -> None:
        response = SimpleNamespace(text=valid_summary())
        generate_content = Mock(return_value=response)
        models = SimpleNamespace(generate_content=generate_content)
        client = SimpleNamespace(models=models)
        with patch("call_summary.genai.Client", return_value=client):
            result = _generate_summary_text("test-key", "Summarize this call.")

        self.assertEqual(result, response.text)
        config = generate_content.call_args.kwargs["config"]
        serialized = config.model_dump(by_alias=True, exclude_none=True)
        self.assertIn("responseJsonSchema", serialized)
        self.assertNotIn("responseSchema", serialized)
        self.assertNotIn("additionalProperties", json.dumps(serialized))


class TranscriptTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_fragments_are_one_timestamped_role_turn(self) -> None:
        websocket = SimpleNamespace(
            client_state=WebSocketState.CONNECTED,
            send_json=AsyncMock(),
        )
        context = {
            "transcript": [],
            "active_turns": {},
            "updated_at": 0,
        }

        await main._record_transcript_fragment(
            websocket,
            context,
            "user",
            "Where is ORD-101",
        )
        await main._record_transcript_fragment(
            websocket,
            context,
            "user",
            "Where is ORD-101?",
        )
        await main._record_transcript_fragment(
            websocket,
            context,
            "assistant",
            "Your order is out for delivery.",
        )
        await main._finish_transcript_turn(websocket, context, "assistant")

        self.assertEqual(
            [(entry["role"], entry["text"]) for entry in context["transcript"]],
            [
                ("user", "Where is ORD-101?"),
                ("assistant", "Your order is out for delivery."),
            ],
        )
        self.assertTrue(context["transcript"][0]["timestamp"].endswith("+00:00"))
        self.assertEqual(
            len(
                {
                    call.args[0]["turn_id"]
                    for call in websocket.send_json.await_args_list
                }
            ),
            2,
        )


class CallSummaryEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.call_id = "test-call-id"
        main.CALL_CONTEXTS[self.call_id] = {
            "transcript": [
                {
                    "role": "user",
                    "text": "Where is ORD-101?",
                    "timestamp": "2026-10-25T00:00:00+00:00",
                }
            ],
            "tool_results": [],
            "active_turns": {},
            "updated_at": 9999999999,
            "finished": True,
        }

    def tearDown(self) -> None:
        main.CALL_CONTEXTS.pop(self.call_id, None)

    def test_endpoint_returns_structured_json(self) -> None:
        expected = CallSummary(
            customer_intent="Check order status",
            order_id="ORD-101",
            resolution_status="resolved",
            call_summary="The customer asked about ORD-101.",
        )
        with patch("main.generate_call_summary", return_value=expected):
            response = self.client.post(f"/calls/{self.call_id}/summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected.model_dump())
        self.assertNotIn(self.call_id, main.CALL_CONTEXTS)

    def test_transcript_context_survives_summary_failure(self) -> None:
        with patch(
            "main.generate_call_summary",
            side_effect=SummaryGenerationError("Summary model failed."),
        ):
            response = self.client.post(f"/calls/{self.call_id}/summary")

        self.assertEqual(response.status_code, 502)
        self.assertIn(self.call_id, main.CALL_CONTEXTS)
        self.assertEqual(len(main.CALL_CONTEXTS[self.call_id]["transcript"]), 1)

    def test_unknown_or_expired_call_returns_not_found(self) -> None:
        response = self.client.post("/calls/unknown-call/summary")
        self.assertEqual(response.status_code, 404)

    def test_sample_order_endpoint_returns_all_three_current_records(self) -> None:
        response = self.client.get("/sample-orders")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "order_id": "ORD-101",
                    "product": "Vitamin C Serum (30ml)",
                    "total_inr": 699,
                    "status": "Out for Delivery",
                },
                {
                    "order_id": "ORD-102",
                    "product": "Hydrating Sunscreen SPF 50",
                    "total_inr": 499,
                    "status": "Delivered 14 days ago",
                },
                {
                    "order_id": "ORD-103",
                    "product": "Green Tea Face Wash + Toner",
                    "total_inr": 850,
                    "status": "Processing",
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
