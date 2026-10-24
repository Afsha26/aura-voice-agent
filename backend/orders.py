from copy import copy
from typing import Optional


ORDERS: dict[str, dict[str, object]] = {
    "ORD-101": {
        "order_id": "ORD-101",
        "customer_name": "Priya Sharma",
        "product": "Vitamin C Serum (30ml)",
        "amount_inr": 699,
        "status": "Out for Delivery",
        "carrier": "BlueDart",
        "tracking_number": "BD-982103",
        "expected_delivery": "by 6 PM today",
    },
    "ORD-102": {
        "order_id": "ORD-102",
        "customer_name": "Rahul Verma",
        "product": "Hydrating Sunscreen SPF 50",
        "amount_inr": 499,
        "status": "Delivered",
        "delivered_days_ago": 14,
        "carrier": "Delhivery",
        "tracking_number": "DL-441029",
    },
    "ORD-103": {
        "order_id": "ORD-103",
        "customer_name": "Ananya Patel",
        "product": "Green Tea Face Wash + Toner",
        "amount_inr": 850,
        "status": "Processing",
        "ordered_hours_ago": 3,
    },
}


def get_order_details(order_id: str) -> Optional[dict[str, object]]:
    """Return mock order details, or None when the ID is not found."""
    order = ORDERS.get(order_id)
    return copy(order) if order else None


def lookup_order(order_id: str) -> Optional[dict[str, object]]:
    """Backward-compatible alias for the original placeholder function."""
    return get_order_details(order_id)
