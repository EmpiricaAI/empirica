"""A similarity query over Qdrant's REST API, with the standard library only.

The hot paths that only read (tool-router on every prompt, the link suggester after every ``*-log``) paid 0.76 s to
import ``qdrant_client`` for one ``query_points`` call (goal 2dc0b760). This speaks the same call over HTTP and returns
the same shape (``.points``, each with ``id``, ``score``, ``payload``), so a caller swaps the client and nothing else.
Writes and collection management stay on ``qdrant_client``.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from types import SimpleNamespace
from typing import Any


class RestQdrant:
    def __init__(self, url: str, *, timeout: float = 3.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        limit: int = 10,
        score_threshold: float | None = None,
        with_payload: bool = True,
    ) -> SimpleNamespace:
        """POST /collections/<name>/points/query. Raises OSError (HTTPError, URLError, timeout) on any failure, a
        missing collection included, so the caller decides whether to skip it; it is never an empty success."""
        body: dict[str, Any] = {"query": list(query), "limit": int(limit)}
        if score_threshold is not None:
            body["score_threshold"] = float(score_threshold)
        body["with_payload"] = bool(with_payload)
        req = urllib.request.Request(
            f"{self.url}/collections/{urllib.parse.quote(collection_name, safe='')}/points/query",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            result = json.loads(resp.read()).get("result") or {}
        points = result.get("points", result) if isinstance(result, dict) else result
        return SimpleNamespace(
            points=[
                SimpleNamespace(id=p.get("id"), score=float(p.get("score") or 0.0), payload=p.get("payload") or {})
                for p in points or []
                if isinstance(p, dict)
            ]
        )
