"""The one place in the execution package that starts processes (docs/10).

``run_argv`` starts an argument array (never a shell string) with an explicit, minimal
environment, writes the request to stdin, reads stdout/stderr with hard size caps, and
enforces a wall-clock limit by killing the whole process group. A caller-supplied
``on_timeout`` hook runs after the kill (the Docker backend uses it to remove the named
container and confirm it is gone). Nothing here interprets the output.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .. import cancellation


@dataclass
class ProcessResult:
    argv0: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    elapsed_ms: float
    start_error: str | None = None


def _reader(stream, cap: int, out: list[bytes], flag: list[bool]) -> None:
    size = 0
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            if size < cap:
                out.append(chunk[: cap - size])
            size += len(chunk)
            if size > cap:
                flag[0] = True
    except (OSError, ValueError):
        pass


def run_argv(argv: list[str], *, stdin: bytes, env: dict[str, str], timeout_s: float, max_stdout: int, max_stderr: int = 65536,
             cwd: str | None = None, on_timeout: Callable[[], None] | None = None) -> ProcessResult:
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings")
    cancellation.check()  # never start a process for a cancelled run
    token = cancellation.current()
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
                                start_new_session=True, close_fds=True)
    except OSError as exc:
        return ProcessResult(argv[0], None, b"", b"", False, False, 0.0, start_error=f"{type(exc).__name__}: {exc}")
    out: list[bytes] = []
    err: list[bytes] = []
    out_flag, err_flag = [False], [False]
    threads = [threading.Thread(target=_reader, args=(proc.stdout, max_stdout, out, out_flag), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, max_stderr, err, err_flag), daemon=True)]
    for t in threads:
        t.start()
    try:
        if proc.stdin is not None:
            try:
                proc.stdin.write(stdin)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        timed_out = False
        deadline = t0 + timeout_s
        try:
            while proc.poll() is None:
                if token is not None:
                    token.check()  # raises Cancelled: the handler below kills the group and cleans up
                if time.monotonic() >= deadline or out_flag[0]:
                    timed_out = time.monotonic() >= deadline
                    _kill_group(proc)
                    if on_timeout is not None:
                        on_timeout()
                    break
                time.sleep(0.01)
        except BaseException:
            # Cancellation (KeyboardInterrupt or cancellation.Cancelled): the child runs in its own session and would
            # not see the signal, so kill its process group and run the cleanup hook, then re-raise.
            _kill_group(proc)
            if on_timeout is not None:
                try:
                    on_timeout()
                except Exception:  # noqa: BLE001 - best-effort cleanup during cancellation
                    pass
            raise
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.wait(timeout=10)
    finally:
        for t in threads:
            t.join(timeout=5)
    return ProcessResult(argv[0], proc.returncode, b"".join(out), b"".join(err), timed_out, out_flag[0], round((time.monotonic() - t0) * 1000, 3))


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except OSError:
            pass


def minimal_env(extra: dict[str, str] | None = None, passthrough: tuple[str, ...] = ()) -> dict[str, str]:
    """An explicit environment: nothing from the caller's environment unless named."""
    env: dict[str, str] = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}
    for k in passthrough:
        v = os.environ.get(k)
        if v is not None:
            env[k] = v
    env.update(extra or {})
    return env


__all__ = ["run_argv", "ProcessResult", "minimal_env"]
