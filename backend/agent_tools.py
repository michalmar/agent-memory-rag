"""Agent tools (vertical slice).

Only `get_order_status` is implemented here — it uses pure mock data and requires no
Azure access, so it works in both the mock and (future) real runner. Later phases add
`check_memory`, `update_user_profile`, and the RAG tools.
"""
from __future__ import annotations

# Mock order data (PRD §8 / §18).
_MOCK_ORDERS: dict[str, dict] = {
    "ORD-001": {
        "status": "shipped",
        "trackingNumber": "1Z999AA1",
        "eta": "Jan 25, 2026",
    },
    "ORD-002": {
        "status": "processing",
        "trackingNumber": None,
        "eta": "Jan 23, 2026",
    },
    "ORD-003": {
        "status": "delivered",
        "trackingNumber": None,
        "eta": "Delivered Jan 20, 2026",
    },
}

# status -> Material Symbols icon (PRD §B6).
_STATUS_ICON = {
    "shipped": "local_shipping",
    "processing": "pending",
    "delivered": "check_circle",
    "not_found": "error",
}


def get_order_status(order_id: str) -> dict:
    """Look up the status of an order by its ID (e.g. ORD-001)."""
    key = (order_id or "").strip().upper()
    order = _MOCK_ORDERS.get(key)
    if order is None:
        return {
            "order_id": key,
            "status": "not_found",
            "message": "Order not found",
            "currentStepIcon": _STATUS_ICON["not_found"],
        }
    return {
        "order_id": key,
        "status": order["status"],
        "trackingNumber": order["trackingNumber"] or "Not yet assigned",
        "eta": order["eta"],
        "currentStepIcon": _STATUS_ICON.get(order["status"], "help"),
    }
