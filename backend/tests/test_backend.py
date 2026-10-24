import unittest

from fastapi.testclient import TestClient

from main import app
from orders import get_order_details, lookup_order
from policies import (
    get_policy_details,
    is_cancellation_eligible,
    is_return_eligible,
)


class OrderLookupTests(unittest.TestCase):
    def test_all_sample_orders_have_the_requested_details(self) -> None:
        self.assertEqual(
            get_order_details("ORD-101"),
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
        self.assertEqual(
            get_order_details("ORD-102"),
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
        self.assertEqual(
            get_order_details("ORD-103"),
            {
                "order_id": "ORD-103",
                "customer_name": "Ananya Patel",
                "product": "Green Tea Face Wash + Toner",
                "amount_inr": 850,
                "status": "Processing",
                "ordered_hours_ago": 3,
            },
        )

    def test_unknown_order_returns_none(self) -> None:
        self.assertIsNone(get_order_details("ORD-999"))

    def test_original_lookup_name_remains_available(self) -> None:
        self.assertEqual(lookup_order("ORD-103"), get_order_details("ORD-103"))


class PolicyEligibilityTests(unittest.TestCase):
    def test_cancellation_is_only_allowed_while_processing(self) -> None:
        self.assertTrue(is_cancellation_eligible(get_order_details("ORD-103")))
        self.assertFalse(is_cancellation_eligible(get_order_details("ORD-101")))

    def test_return_window_and_product_conditions_are_enforced(self) -> None:
        delivered_order = get_order_details("ORD-102")
        self.assertFalse(
            is_return_eligible(
                delivered_order,
                unopened=True,
                unused=True,
                original_packaging=True,
            )
        )

        recently_delivered_order = {"status": "Delivered"}
        self.assertTrue(
            is_return_eligible(
                recently_delivered_order,
                days_since_delivery=7,
                unopened=True,
                unused=True,
                original_packaging=True,
            )
        )
        self.assertFalse(
            is_return_eligible(
                recently_delivered_order,
                days_since_delivery=8,
                unopened=True,
                unused=True,
                original_packaging=True,
            )
        )
        self.assertFalse(
            is_return_eligible(
                recently_delivered_order,
                days_since_delivery=3,
                unopened=True,
                unused=False,
                original_packaging=True,
            )
        )

    def test_policy_details_include_the_assignment_rules(self) -> None:
        details = get_policy_details()
        self.assertEqual(details["delivery"]["free_delivery_for_orders_above_inr"], 499)
        self.assertEqual(details["delivery"]["shipping_fee_inr"], 50)
        self.assertEqual(details["returns"]["window_days_from_delivery"], 7)
        self.assertEqual(
            details["damaged_or_defective_products"]["report_within_hours_of_delivery"],
            48,
        )
        self.assertEqual(details["cancellation"]["allowed_only_while_status_is"], "Processing")
        self.assertEqual(details["cash_on_delivery"]["maximum_order_amount_inr"], 2500)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_order_endpoint_reports_cancellation_eligibility(self) -> None:
        processing_order = self.client.get("/orders/ORD-103")
        out_for_delivery_order = self.client.get("/orders/ORD-101")
        self.assertEqual(processing_order.status_code, 200)
        self.assertTrue(processing_order.json()["cancellation_eligible"])
        self.assertEqual(out_for_delivery_order.status_code, 200)
        self.assertFalse(out_for_delivery_order.json()["cancellation_eligible"])

    def test_unknown_order_returns_clear_404(self) -> None:
        response = self.client.get("/orders/ORD-999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            {"error": "Order not found", "order_id": "ORD-999"},
        )

    def test_policy_endpoint_returns_policy_information(self) -> None:
        response = self.client.get("/policies")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), get_policy_details())


if __name__ == "__main__":
    unittest.main()
