"""Agent tools + registration (PRD §8, §B6).

Tools: get_order_status (mock data), check_memory (pgvector search),
update_user_profile (Cosmos merge-patch, silent), do_classic_rag (AI Search hybrid).

The active user_id flows through a contextvar set per request; store handles are
injected at app startup via set_stores().
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

logger = logging.getLogger("tools")

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default=""
)

# ---------------------------------------------------------------- mock orders
_MOCK_ORDERS: dict[str, dict] = {
    "ORD-001": {"status": "shipped", "trackingNumber": "1Z999AA1", "eta": "Jan 25, 2026"},
    "ORD-002": {"status": "processing", "trackingNumber": None, "eta": "Jan 23, 2026"},
    "ORD-003": {"status": "delivered", "trackingNumber": None, "eta": "Delivered Jan 20, 2026"},
}

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


# ---------------------------------------------------------------- store wiring
class ToolStores:
    memory_store: Any = None
    profile_store: Any = None
    classic_rag: Any = None


_stores = ToolStores()


def set_stores(memory_store=None, profile_store=None, classic_rag=None) -> None:
    _stores.memory_store = memory_store
    _stores.profile_store = profile_store
    _stores.classic_rag = classic_rag


# ---------------------------------------------------------------- async tools
async def check_memory(query: str) -> dict:
    """Search past conversation memories for the current user (top 3)."""
    user_id = current_user_id.get()
    store = _stores.memory_store
    if not store or not getattr(store, "enabled", False) or not user_id:
        return {"memories": [], "message": "No memories available."}
    from azure_clients import embed_text

    embedding = await embed_text(query)
    rows = await store.search(user_id, embedding, limit=3)
    return {
        "memories": [
            {"summary": r["summary"], "similarity": round(float(r.get("similarity", 0)), 3)}
            for r in rows
        ]
    }


async def update_user_profile(
    basic_info: dict | None = None,
    interests: list | None = None,
    habits: list | None = None,
    preferences: dict | None = None,
    status: dict | None = None,
    facts: list | None = None,
) -> dict:
    """Silently persist durable user facts (RFC 7396 merge patch)."""
    user_id = current_user_id.get()
    store = _stores.profile_store
    updates = {
        k: v
        for k, v in {
            "basic_info": basic_info,
            "interests": interests,
            "habits": habits,
            "preferences": preferences,
            "status": status,
            "facts": facts,
        }.items()
        if v is not None
    }
    if not store or not getattr(store, "enabled", False) or not user_id or not updates:
        return {"message": "Profile updated: (none)"}
    await store.patch_profile(user_id, updates)
    return {"message": f"Profile updated: {', '.join(updates.keys())}"}


async def do_classic_rag(query: str) -> dict:
    """Hybrid search over the orders knowledge base index."""
    rag = _stores.classic_rag
    if not rag or not getattr(rag, "enabled", False):
        return {"content": "", "citations": []}
    return await rag.search(query)


# ---------------------------------------------------------------- OpenAI schemas
def _tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


_SCHEMA_ORDER = _tool(
    "get_order_status",
    "Look up the status of an order by its ID (e.g. ORD-001).",
    {"order_id": {"type": "string", "description": "Order ID like ORD-001"}},
    ["order_id"],
)
_SCHEMA_CHECK_MEMORY = _tool(
    "check_memory",
    "Search past conversation memories. Use ONLY when the user explicitly asks to "
    "recall or reference a previous conversation.",
    {"query": {"type": "string"}},
    ["query"],
)
_SCHEMA_UPDATE_PROFILE = _tool(
    "update_user_profile",
    "Silently record durable, explicitly-stated facts about the user. Pass only "
    "changed fields; arrays must be the full desired array. Never mention calling this.",
    {
        "basic_info": {"type": "object"},
        "interests": {"type": "array", "items": {"type": "string"}},
        "habits": {"type": "array", "items": {"type": "string"}},
        "preferences": {"type": "object"},
        "status": {"type": "object"},
        "facts": {"type": "array", "items": {"type": "string"}},
    },
)
_SCHEMA_CLASSIC_RAG = _tool(
    "do_classic_rag",
    "Search the orders knowledge base for product/shipping details in the user's orders.",
    {"query": {"type": "string"}},
    ["query"],
)

_ASYNC_TOOLS = {
    "check_memory": check_memory,
    "update_user_profile": update_user_profile,
    "do_classic_rag": do_classic_rag,
}


def for_rag_mode(mode: str) -> list[dict]:
    """OpenAI tool schemas for the given rag_mode (PRD §F5 registration)."""
    tools = [_SCHEMA_ORDER, _SCHEMA_CHECK_MEMORY, _SCHEMA_UPDATE_PROFILE]
    if mode in ("classic", "agentic"):
        # Agentic MCP KB is not wired in the private-network build; classic RAG covers both.
        tools.append(_SCHEMA_CLASSIC_RAG)
    return tools


async def execute_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool call by name; returns a JSON-serialisable dict."""
    if name == "get_order_status":
        return get_order_status(arguments.get("order_id", ""))
    fn = _ASYNC_TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    return await fn(**arguments)
