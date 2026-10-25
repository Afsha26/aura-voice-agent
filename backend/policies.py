from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Optional


RETURN_WINDOW_DAYS = 7
FREE_DELIVERY_THRESHOLD_INR = 499
BELOW_THRESHOLD_SHIPPING_FEE_INR = 50
COD_MAXIMUM_ORDER_AMOUNT_INR = 2500
SHIPPING_BOUNDARY_NOTE = (
    "The policy does not specify the shipping fee for an order of exactly ₹499."
)

POLICY_INFO: dict[str, object] = {
    "delivery": {
        "free_delivery_for_orders_above_inr": FREE_DELIVERY_THRESHOLD_INR,
        "shipping_fee_inr_for_orders_below_inr": FREE_DELIVERY_THRESHOLD_INR,
        "shipping_fee_inr": BELOW_THRESHOLD_SHIPPING_FEE_INR,
        "exact_threshold_note": SHIPPING_BOUNDARY_NOTE,
        "standard_delivery": "3–5 business days",
    },
    "returns": {
        "window_days_from_delivery": RETURN_WINDOW_DAYS,
        "conditions": [
            "Product must be unopened.",
            "Product must be unused.",
            "Product must be in its original packaging.",
        ],
    },
    "damaged_or_defective_products": {
        "report_within_hours_of_delivery": 48,
        "photo_evidence_required": True,
        "resolution": "Report with photos for replacement.",
    },
    "cancellation": {
        "allowed_only_while_status_is": "Processing",
        "shipped_or_out_for_delivery": (
            "Cannot be cancelled; the customer may refuse delivery at the doorstep."
        ),
    },
    "cash_on_delivery": {
        "maximum_order_amount_inr": COD_MAXIMUM_ORDER_AMOUNT_INR,
        "payment_methods": ["cash", "UPI"],
        "payment_due": "at the doorstep",
    },
    "assistant_constraints": [
        "Follow these policies; do not override them.",
        "Do not promise unsupported refunds, replacements, or cancellations.",
    ],
}


def get_policy_details() -> dict[str, object]:
    """Return policy data for the API without exposing mutable shared state."""
    return deepcopy(POLICY_INFO)


def get_return_policy_details() -> dict[str, object]:
    """Return complete return and damaged/defective product policies."""
    policies = get_policy_details()
    return {
        "returns": policies["returns"],
        "damaged_or_defective_products": policies["damaged_or_defective_products"],
    }


def get_shipping_policy(order_amount_inr: int) -> dict[str, object]:
    """Apply the stated strict bounds; the exact ₹499 boundary is unspecified."""
    if order_amount_inr > FREE_DELIVERY_THRESHOLD_INR:
        return {"status": "free", "shipping_fee_inr": 0}
    if order_amount_inr < FREE_DELIVERY_THRESHOLD_INR:
        return {
            "status": "fee_applies",
            "shipping_fee_inr": BELOW_THRESHOLD_SHIPPING_FEE_INR,
        }
    return {
        "status": "unspecified_boundary",
        "shipping_fee_inr": None,
        "message": SHIPPING_BOUNDARY_NOTE,
    }


def is_cod_eligible(order_amount_inr: int) -> bool:
    """COD is available through and including the configured maximum amount."""
    if not isinstance(order_amount_inr, int) or isinstance(order_amount_inr, bool):
        return False
    if order_amount_inr < 0:
        return False
    return order_amount_inr <= COD_MAXIMUM_ORDER_AMOUNT_INR


def is_cancellation_eligible(order: Optional[Mapping[str, object]]) -> bool:
    """Cancellation is allowed only while an order is Processing."""
    return bool(order and order.get("status") == "Processing")


def is_return_eligible(
    order: Optional[Mapping[str, object]],
    *,
    days_since_delivery: Optional[int] = None,
    unopened: bool = False,
    unused: bool = False,
    original_packaging: bool = False,
) -> bool:
    """Check the return window and all product-condition requirements."""
    if not order or order.get("status") != "Delivered":
        return False

    if days_since_delivery is None:
        order_age = order.get("delivered_days_ago")
        if not isinstance(order_age, int):
            return False
        days_since_delivery = order_age

    return (
        0 <= days_since_delivery <= RETURN_WINDOW_DAYS
        and unopened
        and unused
        and original_packaging
    )


def check_policy(question: str) -> str:
    """Return the policy data as JSON for callers of the original placeholder."""
    del question
    return json.dumps(get_policy_details(), ensure_ascii=False)
