"""A similarity query over Qdrant's REST API, with the standard library only.

The hot paths that only read (tool-router on every prompt, the link suggester after every ``*-log``) paid 0.76 s to
import ``qdrant_client`` for one ``query_points`` call (goal 2dc0b760). This speaks the same call over HTTP and returns
the same shape (``.points``, each with ``id``, ``score``, ``payload``), so a caller swaps the client and nothing else.
Writes and collection management stay on ``qdrant_client``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace
from typing import Any


class RestQdrant:
    def __init__(self, url: str, *, timeout: float = 3.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _post(self, collection_name: str, verb: str, body: dict) -> Any:
        req = urllib.request.Request(
            f"{self.url}/collections/{urllib.parse.quote(collection_name, safe='')}/points/{verb}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read()).get("result") or {}

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
        missing collection included, so the caller decides whether to skip it; it is never an empty success.

        Qdrant before 1.10 has no ``/points/query``: every call raised, and link suggestions were silently empty there
        (1.15.0 sweep, H5b). A 404 or 405 is retried once on ``/points/search``, which every version serves; when that
        fails too (a missing collection), its error is raised."""
        rest: dict[str, Any] = {"limit": int(limit), "with_payload": bool(with_payload)}
        if score_threshold is not None:
            rest["score_threshold"] = float(score_threshold)
        try:
            result = self._post(collection_name, "query", {"query": list(query), **rest})
        except urllib.error.HTTPError as e:
            if e.code not in (404, 405):
                raise
            result = self._post(collection_name, "search", {"vector": list(query), **rest})
        points = result.get("points", result) if isinstance(result, dict) else result
        return SimpleNamespace(
            points=[
                SimpleNamespace(id=p.get("id"), score=float(p.get("score") or 0.0), payload=p.get("payload") or {})
                for p in points or []
                if isinstance(p, dict)
            ]
        )
