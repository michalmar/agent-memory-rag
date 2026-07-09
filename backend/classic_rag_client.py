"""ClassicRAGClient — hybrid search over the orders index (PRD §F5, classic path).

Computes the query embedding app-side (keeps AI Search fully private — no
Search->OpenAI vectorizer link needed) and issues a keyword+vector+semantic
hybrid query. Returns {content, citations[]} per the citation contract (§B6).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from config import get_settings

logger = logging.getLogger("classic_rag")

_SEARCH_API_VERSION = "2024-07-01"


def _derive_source_name(chunk: str, ref_id: str) -> str:
    """Label a citation source from its chunk/ref_id (PRD §B6)."""
    first_line = (chunk or "").strip().splitlines()[0] if chunk else ""
    m = re.match(r"^([A-Z][^:]{2,40}):", first_line)
    if m:
        return m.group(1)
    parts = (ref_id or "").split("-")
    if parts and parts[0] == "ord" and len(parts) >= 3:
        return f"Order ORD-{parts[1]} {' '.join(p.title() for p in parts[2:])}".strip()
    if parts and parts[0] == "policy" and len(parts) >= 2:
        return f"Policy: {' '.join(p.title() for p in parts[1:])}"
    joined = " ".join(p.title() for p in parts)[:40]
    return joined or f"Source {ref_id}"


class ClassicRAGClient:
    def __init__(self) -> None:
        self._enabled = get_settings().search_configured

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def _headers(self) -> dict:
        s = get_settings()
        if s.search_api_key:
            return {"api-key": s.search_api_key, "Content-Type": "application/json"}
        from azure_clients import get_credential

        token = await get_credential().get_token("https://search.azure.com/.default")
        return {"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"}

    async def search(self, query: str) -> dict:
        if not self._enabled:
            return {"content": "", "citations": []}
        import httpx

        from azure_clients import embed_text

        s = get_settings()
        embedding = await embed_text(query)
        url = f"{s.search_endpoint}/indexes/{s.search_orders_index}/docs/search"
        payload = {
            "search": query,
            "vectorQueries": [
                {
                    "kind": "vector",
                    "vector": embedding,
                    "fields": "page_embedding",
                    "k": 5,
                }
            ],
            "queryType": "semantic",
            "semanticConfiguration": "semantic_config",
            "select": "id, order_id, category, page_chunk",
            "top": 5,
        }
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(3):
                resp = await client.post(
                    url, params={"api-version": _SEARCH_API_VERSION},
                    json=payload, headers=headers,
                )
                if resp.status_code == 502:  # integrated-vectorizer cold start
                    logger.warning("search 502, retry %d", attempt + 1)
                    continue
                resp.raise_for_status()
                break
            else:
                return {"content": "", "citations": []}

        docs = resp.json().get("value", [])
        chunks: list[str] = []
        citations: list[dict] = []
        for i, doc in enumerate(docs):
            chunk = doc.get("page_chunk", "") or ""
            ref_id = doc.get("id", f"doc-{i}")
            source_name = _derive_source_name(chunk, ref_id)
            annotation = f"【{i}:{ref_id}†{source_name}】"
            chunks.append(chunk)
            citations.append(
                {
                    "search_idx": i,
                    "ref_id": ref_id,
                    "source_name": source_name,
                    "content": chunk[:300],
                    "annotation": annotation,
                }
            )
        return {"content": "\n\n".join(chunks), "citations": citations}
