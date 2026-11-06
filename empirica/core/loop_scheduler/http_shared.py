"""One SSL context per process, for every HTTPS call the listener makes.

Measured by mesh-support on 2026-11-06 (prop_2ufpbpincbcltiedfd7icbjvim): the liveness probe built a new
``ssl.create_default_context()`` and, by passing it to ``urlopen``, a new opener every 60 seconds. Each context costs about
0.77 MB of native memory (OpenSSL 3.5.5); openers and their handlers reference each other, so only the cyclic collector frees
them, 66 were still alive after three hours, and glibc does not return the memory even then. A listener grew by about
16 MB an hour, all of it outside Python's allocator.

The other listener calls used ``urlopen`` without a context, which makes ``http.client`` create a default context for every
HTTPS connection: the same native allocation on every call. Every call goes through :func:`open_url` now, which passes one
context, made once for the life of the process.
"""

from __future__ import annotations

import ssl
import threading
import urllib.request
from typing import Any

_LOCK = threading.Lock()
_CONTEXT: ssl.SSLContext | None = None


def shared_context() -> ssl.SSLContext:
    """The process's one default SSL context, created on first use."""
    global _CONTEXT
    with _LOCK:
        if _CONTEXT is None:
            _CONTEXT = ssl.create_default_context()
        return _CONTEXT


def open_url(request: urllib.request.Request | str, timeout: float) -> Any:
    """``urlopen`` with the shared context. Same return value and errors as ``urlopen``.

    ``urlopen`` still builds a small opener per call when given a context. That opener is ordinary Python garbage, freed by the
    cyclic collector; the native cost is the context, and that is now made once. ``urllib.request.urlopen`` is looked up at call
    time, so a test that replaces it still intercepts every listener call.
    """
    return urllib.request.urlopen(request, timeout=timeout, context=shared_context())
