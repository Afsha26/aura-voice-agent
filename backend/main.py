from fastapi import FastAPI, HTTPException

from orders import get_order_details
from policies import get_policy_details, is_cancellation_eligible

app = FastAPI(title="Aura Skincare Support API")


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/orders/{order_id}")
def read_order(order_id: str) -> dict[str, object]:
    order = get_order_details(order_id)
    if order is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Order not found", "order_id": order_id},
        )

    return {
        **order,
        "cancellation_eligible": is_cancellation_eligible(order),
    }


@app.get("/policies")
def read_policies() -> dict[str, object]:
    return get_policy_details()
