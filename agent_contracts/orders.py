"""Shared demo order lookup used by application and MCP tool surfaces."""

from __future__ import annotations

from typing import Any

_ORDERS: dict[str, dict[str, Any]] = {
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

_STATUS_ICON = {
    "shipped": "local_shipping",
    "processing": "pending",
    "delivered": "check_circle",
    "not_found": "error",
}


def lookup_order_status(order_id: str) -> dict[str, Any]:
    """Return the normalized demo order status without requiring user context."""
    key = order_id.strip().upper()
    order = _ORDERS.get(key)
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
