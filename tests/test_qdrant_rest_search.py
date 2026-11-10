"""tool-router runs on every prompt and spent about 1 s importing qdrant_client (0.76 s measured) only to run one
similarity query (goal 2dc0b760). The query now goes over Qdrant's REST API with the standard library, and importing
the qdrant package no longer loads the client library."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from empirica.core.qdrant.rest import RestQdrant


def test_importing_the_package_and_its_connection_module_does_not_load_qdrant_client():
    code = (
        "import sys; import empirica.core.qdrant, empirica.core.qdrant.connection, empirica.core.qdrant.rest, "
        "empirica.core.bootstrap.suggested_links; print('qdrant_client' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "False"


def test_persona_registry_is_still_importable_from_the_package():
    from empirica.core.qdrant import PersonaRegistry
    from empirica.core.qdrant.persona_registry import PersonaRegistry as direct

    assert PersonaRegistry is direct


class _Qdrant(BaseHTTPRequestHandler):
    seen: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Qdrant.seen.append((self.path, body))
        if "missing" in self.path:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"status":{"error":"Not found: Collection missing"}}')
            return
        points = [{"id": 7, "score": 0.91, "payload": {"artifact_id": "a1", "type": "finding", "text": "x"}}]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"result": {"points": points}, "status": "ok"}).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def qdrant_url():
    server = HTTPServer(("127.0.0.1", 0), _Qdrant)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Qdrant.seen = []
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_query_points_posts_the_query_api_and_returns_scored_points(qdrant_url):
    response = RestQdrant(qdrant_url).query_points(
        collection_name="proj_memory", query=[0.1, 0.2], limit=20, score_threshold=0.65, with_payload=True
    )
    ((path, body),) = _Qdrant.seen
    assert path == "/collections/proj_memory/points/query"
    assert body == {"query": [0.1, 0.2], "limit": 20, "score_threshold": 0.65, "with_payload": True}
    (point,) = response.points
    assert (point.id, point.score, point.payload["artifact_id"]) == (7, 0.91, "a1")


def test_a_missing_collection_raises_so_the_caller_skips_it(qdrant_url):
    with pytest.raises(OSError):
        RestQdrant(qdrant_url).query_points(collection_name="missing", query=[0.1], limit=5)


def test_the_suggester_searches_over_rest(qdrant_url, monkeypatch):
    from empirica.core.bootstrap.suggested_links import suggest_links_for_artifact
    from empirica.core.qdrant import connection

    monkeypatch.setattr(connection, "qdrant_search_client", lambda: RestQdrant(qdrant_url))
    monkeypatch.setattr(connection, "_get_embedding_safe", lambda text: [0.1] * 4)
    out = suggest_links_for_artifact("proj-1", "some text", "self-id")
    assert [h["id"] for h in out] == ["a1"] and len(_Qdrant.seen) == 3  # memory, assumptions, decisions
