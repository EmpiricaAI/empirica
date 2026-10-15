"""The listener's bearer token must not appear on curl's command line.

``_open_stream`` passed every header as ``-H 'Authorization: Bearer tk_...'``. A process's argv is readable through ``ps`` and
``/proc/<pid>/cmdline`` by any local user and by anything that records process lists (the services scanner did), and the ten
listeners live for days. Headers now travel on curl's config channel (``-K -`` on stdin), so no header value reaches argv.
These tests run a real curl against a real local server, because the claim is about what the operating system shows.
"""

from __future__ import annotations

import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from empirica.core.loop_scheduler import listener

TOKEN = "tk_abcdefghijklmnopqrstuvwxyz0123456789"  # noqa: S105 - a made-up token with the real shape

pytestmark = pytest.mark.skipif(shutil.which("curl") is None, reason="curl not installed")


class _Server:
    def __init__(self):
        self.seen: dict[str, str] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen = {k.lower(): v for k, v in self.headers.items()}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"event":"open"}\n')
                self.wfile.flush()
                time.sleep(2)  # hold the connection open like ntfy does, so the curl process is observable

            def log_message(self, *_a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/topic/json"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


def _cmdline(pid: int) -> str:
    return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")


def _wait_for(predicate, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_the_server_still_receives_the_authorization_header(server):
    proc = listener._open_stream(server.url, {"Authorization": f"Bearer {TOKEN}"})
    try:
        assert listener._read_http_status(proc) == 200
        assert server.seen.get("authorization") == f"Bearer {TOKEN}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="needs /proc")
def test_the_token_is_not_in_the_process_command_line(server):
    proc = listener._open_stream(server.url, {"Authorization": f"Bearer {TOKEN}"})
    try:
        assert _wait_for(lambda: bool(server.seen)), (
            "curl never reached the server, so the cmdline check proves nothing"
        )
        cmdline = _cmdline(proc.pid)
        assert "curl" in cmdline and server.url in cmdline, "positive control: we read the right process"
        assert TOKEN not in cmdline and "tk_" not in cmdline and "Bearer" not in cmdline
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_several_headers_all_arrive(server):
    proc = listener._open_stream(server.url, {"Authorization": f"Bearer {TOKEN}", "X-Probe": "one two"})
    try:
        assert listener._read_http_status(proc) == 200
        assert server.seen.get("x-probe") == "one two"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_a_header_value_with_quotes_and_backslashes_survives_the_config_channel(server):
    value = 'a"b\\c d'
    proc = listener._open_stream(server.url, {"X-Odd": value})
    try:
        assert listener._read_http_status(proc) == 200
        assert server.seen.get("x-odd") == value
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.parametrize("bad", ["x\r\nInjected: 1", "x\nInjected: 1", "x\ry"])
def test_a_header_value_cannot_carry_a_line_break(server, bad):
    with pytest.raises(ValueError, match="header"):
        listener._open_stream(server.url, {"X-Probe": bad})


def test_a_header_name_cannot_carry_a_line_break_or_a_colon(server):
    for bad in ("X\r\nInjected", "X:Y"):
        with pytest.raises(ValueError, match="header"):
            listener._open_stream(server.url, {bad: "v"})


def test_no_headers_still_opens_a_stream(server):
    proc = listener._open_stream(server.url, {})
    try:
        assert listener._read_http_status(proc) == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)
