from collections.abc import Mapping
from typing import Any

from google.genai import types

from orders import get_order_details, normalize_order_id
from policies import (
    RETURN_WINDOW_DAYS,
    get_policy_details,
    get_return_policy_details,
    get_shipping_policy,
    is_cod_eligible,
    is_cancellation_eligible,
    is_return_eligible,
)


def _object_schema(
    properties: dict[str, types.Schema],
    required: list[str],
) -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        properties=properties,
        required=required,
    )


def _string_schema(description: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=description)


def _boolean_schema(description: str) -> types.Schema:
    return types.Schema(type=types.Type.BOOLEAN, description=description)


def _integer_schema(description: str) -> types.Schema:
    return types.Schema(type=types.Type.INTEGER, description=description)


LIVE_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_order_details",
                description=(
                    "Look up a mock Aura Skincare order. Use when the customer asks "
                    "about order status, tracking, items, delivery, or all order details. "
                    "Accept shorthand such as 'order 101' and normalize it to ORD-101. "
                    "For a specific order, use and disclose every field returned by "
                    "the mock record, including customer_name when present."
                ),
                parameters=_object_schema(
                    {
                        "order_id": _string_schema("Order ID, for example ORD-101."),
                    },
                    ["order_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_policy_information",
                description=(
                    "Retrieve configured Aura Skincare delivery, return, damage, "
                    "cancellation, and cash-on-delivery policies for policy questions."
                ),
            ),
            types.FunctionDeclaration(
                name="get_all_sample_orders",
                description=(
                    "List all currently configured mock orders when the customer asks "
                    "for all available sample orders. Return only order ID, product, "
                    "amount, and status. This is a general listing, not identification "
                    "of the customer's own order."
                ),
                parameters=_object_schema({}, []),
            ),
            types.FunctionDeclaration(
                name="get_shipping_policy",
                description=(
                    "Check the configured shipping fee for an order amount. "
                    "Use this for questions about delivery fees."
                ),
                parameters=_object_schema(
                    {
                        "order_amount_inr": _integer_schema(
                            "Order subtotal in Indian rupees."
                        ),
                    },
                    ["order_amount_inr"],
                ),
            ),
            types.FunctionDeclaration(
                name="check_cash_on_delivery_eligibility",
                description=(
                    "Check whether cash on delivery is available for an order amount. "
                    "The configured maximum amount is inclusive."
                ),
                parameters=_object_schema(
                    {
                        "order_amount_inr": _integer_schema(
                            "Order amount in Indian rupees."
                        ),
                    },
                    ["order_amount_inr"],
                ),
            ),
            types.FunctionDeclaration(
                name="check_payment_status",
                description=(
                    "Check whether a mock order record contains payment-status data. "
                    "Do not infer or claim a payment status when it is absent."
                ),
                parameters=_object_schema(
                    {"order_id": _string_schema("The order ID to check.")},
                    ["order_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="check_cancellation_eligibility",
                description=(
                    "Check whether an existing order is eligible for cancellation. "
                    "This only checks eligibility and never cancels an order."
                ),
                parameters=_object_schema(
                    {"order_id": _string_schema("The order ID to check.")},
                    ["order_id"],
                ),
            ),
            types.FunctionDeclaration(
                name="check_return_eligibility",
                description=(
                    "Check whether a delivered order is within the return window and "
                    "meets the customer's unopened, unused, original-packaging claims."
                ),
                parameters=_object_schema(
                    {
                        "order_id": _string_schema("The delivered order ID."),
                        "unopened": _boolean_schema(
                            "Whether the customer confirms the product is unopened."
                        ),
                        "unused": _boolean_schema(
                            "Whether the customer confirms the product is unused."
                        ),
                        "original_packaging": _boolean_schema(
                            "Whether the customer confirms original packaging."
                        ),
                    },
                    ["order_id"],
                ),
            ),
        ]
    )
]


def execute_tool_call(name: str, arguments: Mapping[str, Any]) -> dict[str, object]:
    """Execute one declared read-only order or policy tool."""
    if name == "get_order_details":
        order_id = arguments.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            return {"error": "A valid order_id string is required."}
        normalized_order_id = normalize_order_id(order_id)
        order = get_order_details(normalized_order_id)
        if order is None:
            return {
                "found": False,
                "order_id": normalized_order_id,
                "error": "Order not found. Ask the customer to verify the order ID.",
            }
        return {
            "found": True,
            "requested_order_id": normalized_order_id,
            "order": order,
            "cancellation_eligible": is_cancellation_eligible(order),
            "payment_status_available": "payment_status" in order,
            "payment_status_message": (
                "Payment status is not available in the sample data."
                if "payment_status" not in order
                else None
            ),
        }

    if name == "get_policy_information":
        return {"policies": get_policy_details()}

    if name == "get_all_sample_orders":
        return {
            "orders": [
                {
                    "order_id": order["order_id"],
                    "product": order["product"],
                    "amount_inr": order["amount_inr"],
                    "status": order["status"],
                }
                for order in (get_order_details(order_id) for order_id in (
                    "ORD-101",
                    "ORD-102",
                    "ORD-103",
                ))
                if order is not None
            ]
        }

    if name == "get_shipping_policy":
        amount = arguments.get("order_amount_inr")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return {"error": "order_amount_inr must be a non-negative integer."}
        return {
            "order_amount_inr": amount,
            "policy": get_shipping_policy(amount),
        }

    if name == "check_cash_on_delivery_eligibility":
        amount = arguments.get("order_amount_inr")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            return {"error": "order_amount_inr must be a non-negative integer."}
        policies = get_policy_details()
        cod_policy = policies["cash_on_delivery"]
        eligible = is_cod_eligible(amount)
        return {
            "order_amount_inr": amount,
            "eligible": eligible,
            "maximum_order_amount_inr": cod_policy["maximum_order_amount_inr"],
            "maximum_is_inclusive": True,
            "payment_methods": cod_policy["payment_methods"],
            "payment_due": cod_policy["payment_due"],
        }

    if name == "check_payment_status":
        order_id = arguments.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            return {"error": "A valid order_id string is required."}
        normalized_order_id = normalize_order_id(order_id)
        order = get_order_details(normalized_order_id)
        if order is None:
            return {
                "found": False,
                "order_id": normalized_order_id,
                "error": "Order not found.",
            }
        if "payment_status" not in order:
            return {
                "found": True,
                "order_id": normalized_order_id,
                "payment_status_available": False,
                "message": "Payment status is not available in the current order records.",
            }
        return {
            "found": True,
            "order_id": normalized_order_id,
            "payment_status_available": True,
            "payment_status": order["payment_status"],
        }

    if name == "check_cancellation_eligibility":
        order_id = arguments.get("order_id")
        if not isinstance(order_id, str) or not order_id.strip():
            return {"error": "A valid order_id string is required."}
        normalized_order_id = normalize_order_id(order_id)
        order = get_order_details(normalized_order_id)
        if order is None:
            return {
                "found": False,
                "order_id": normalized_order_id,
                "error": "Order not found. No cancellation action was taken.",
            }
        eligible = is_cancellation_eligible(order)
        if eligible:
            message = (
                "The order is eligible to request cancellation while Processing. "
                "No cancellation has been performed."
            )
        elif order["status"] in ("Shipped", "Out for Delivery"):
            message = (
                "The order cannot be cancelled because it has shipped or is out for "
                "delivery. The customer may refuse delivery at the doorstep. "
                "No cancellation has been performed."
            )
        else:
            message = (
                "The order cannot be cancelled at its current status. "
                "No cancellation has been performed."
            )
        return {
            "found": True,
            "order_id": normalized_order_id,
            "status": order["status"],
            "cancellation_eligible": eligible,
            "action_taken": False,
            "message": message,
        }

    if name == "check_return_eligibility":
        order_id = arguments.get("order_id")
        condition_values = (
            arguments.get("unopened"),
            arguments.get("unused"),
            arguments.get("original_packaging"),
        )
        if not isinstance(order_id, str) or not order_id.strip():
            return {"error": "A valid order_id string is required."}
        normalized_order_id = normalize_order_id(order_id)
        return_policy = get_return_policy_details()
        order = get_order_details(normalized_order_id)
        if order is None:
            return {
                "found": False,
                "order_id": normalized_order_id,
                "error": "Order not found.",
                "policy": return_policy,
            }
        if order.get("status") != "Delivered":
            return {
                "found": True,
                "order_id": normalized_order_id,
                "status": order.get("status"),
                "return_eligible": False,
                "message": "Returns are only considered for delivered orders.",
                "policy": return_policy,
            }
        delivered_days_ago = order.get("delivered_days_ago")
        if not isinstance(delivered_days_ago, int):
            return {
                "found": True,
                "order_id": normalized_order_id,
                "status": order.get("status"),
                "eligibility_checked": False,
                "message": "The delivery date is not available to verify the return window.",
                "policy": return_policy,
            }
        if delivered_days_ago > RETURN_WINDOW_DAYS:
            return {
                "found": True,
                "order_id": normalized_order_id,
                "status": order["status"],
                "delivered_days_ago": delivered_days_ago,
                "return_eligible": False,
                "message": (
                    f"The order was delivered outside the {RETURN_WINDOW_DAYS}-day return window. "
                    "No return or refund has been processed."
                ),
                "policy": return_policy,
            }
        if not all(type(value) is bool for value in condition_values):
            return {
                "found": True,
                "order_id": normalized_order_id,
                "status": order["status"],
                "delivered_days_ago": delivered_days_ago,
                "eligibility_checked": False,
                "message": (
                    "Ask the customer to confirm the product is unopened, unused, "
                    "and in its original packaging before checking return eligibility."
                ),
                "policy": return_policy,
            }
        eligible = is_return_eligible(
            order,
            unopened=condition_values[0],
            unused=condition_values[1],
            original_packaging=condition_values[2],
        )
        return {
            "found": True,
            "order_id": normalized_order_id,
            "status": order["status"],
            "delivered_days_ago": order.get("delivered_days_ago"),
            "return_eligible": eligible,
            "message": (
                "The order meets the configured return eligibility checks. "
                "No return or refund has been processed."
                if eligible
                else "The order does not meet the configured return eligibility checks. "
                "No return or refund has been processed."
            ),
            "policy": return_policy,
        }

    return {"error": f"Unknown tool: {name}"}


async def respond_to_tool_calls(
    session: Any,
    tool_call: Any,
) -> list[dict[str, object]]:
    """Execute all calls in one Live tool event and send their results to Gemini."""
    function_responses = []
    executed_results = []
    for function_call in tool_call.function_calls or []:
        if not function_call.id:
            raise ValueError("Gemini tool call did not include a response ID.")

        arguments = function_call.args or {}
        if not isinstance(arguments, Mapping):
            result = {"error": "Tool arguments must be a JSON object."}
        else:
            result = execute_tool_call(function_call.name or "", arguments)

        executed_results.append(
            {
                "name": function_call.name or "unknown_tool",
                "result": result,
            }
        )
        function_responses.append(
            types.FunctionResponse(
                id=function_call.id,
                name=function_call.name or "unknown_tool",
                response={"result": result},
            )
        )

    if function_responses:
        await session.send_tool_response(function_responses=function_responses)
    return executed_results
