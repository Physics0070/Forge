"""Tavily web search. Every result is recorded as evidence (source, url, query, timestamp, snippet, agent)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from forge.tools.base import ToolContext, ToolError

TAVILY_URL = "https://api.tavily.com/search"


def tavily_search(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not ctx.tavily_api_key:
        raise ToolError("not_configured", "TavilySearch is not configured (TAVILY_API_KEY missing). Report this limitation.")
    query = str(args["query"])[:400]
    n = min(int(args.get("max_results") or 5), 8)
    try:
        r = ctx.client().post(
            TAVILY_URL, headers={"Authorization": f"Bearer {ctx.tavily_api_key}"},
            json={"query": query, "max_results": n, "search_depth": "basic", "include_answer": False},
        )
    except httpx.HTTPError as exc:
        raise ToolError("search_unavailable", f"Could not reach Tavily: {type(exc).__name__}") from exc
    if r.status_code in (401, 403):
        raise ToolError("not_configured", "Tavily rejected the API key.")
    if r.status_code == 429:
        raise ToolError("rate_limited", "Tavily rate limit reached.")
    if r.status_code >= 400:
        raise ToolError("search_unavailable", f"Tavily returned HTTP {r.status_code}.")
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for item in r.json().get("results", [])[:n]:
        snippet = (item.get("content") or "")[:700]
        results.append({"title": item.get("title", ""), "url": item.get("url", ""), "snippet": snippet, "score": item.get("score")})
        ctx.evidence.append({"source": "tavily", "url": item.get("url", ""), "query": query, "timestamp": now,
                             "snippet": snippet, "agent": ctx.agent_id, "finding_ids": []})
    return {"query": query, "results": results, "retrieved_at": now}
