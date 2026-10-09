"""Cooperative cancellation for long-running verifier work (T52, docs/09 MCP section).

A ``CancelToken`` is bound to the worker thread that runs a verification
(``with bound(token): ...``). Engine stages call ``check()`` at safe points: after
capture, after lowering, per analyzed symbol, before every runner process start and
while waiting for one (``execution/process.run_argv`` kills the process group).
Cancellation raises ``Cancelled``.

``Cancelled`` derives from ``BaseException`` on purpose. Per-symbol and per-contract
error isolation catches ``Exception`` and turns it into an ERROR result; a
cancellation must not be converted into a result. It unwinds to the command, which
finalizes the run as failed so its pending cache entries are never published, and
records no contract evidence (docs/02 "Invalidate unfinished runs after interruption").

Without a bound token (every CLI command), ``check()`` is a no-op.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class Cancelled(BaseException):
    """The bound token was cancelled (client cancellation or a server-side timeout)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason: str | None = None
        self._lock = threading.Lock()

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled(self._reason or "cancelled")


_local = threading.local()


def current() -> CancelToken | None:
    return getattr(_local, "token", None)


def check() -> None:
    tok = current()
    if tok is not None:
        tok.check()


@contextmanager
def bound(token: CancelToken | None) -> Iterator[CancelToken | None]:
    """Bind ``token`` to the current thread for the duration of the block."""
    prev = current()
    _local.token = token
    try:
        yield token
    finally:
        _local.token = prev


__all__ = ["Cancelled", "CancelToken", "bound", "check", "current"]
