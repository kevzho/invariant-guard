"""Launch the real `guard` CLI with test-only fault injection (used by test_mcp_server.py).

IG_TEST_MCP_MODE (comma-separated):
  noisy  every analyzed function prints to Python stdout, writes to sys.__stdout__ and runs a child
         process that writes to the inherited fd 1, so protocol separation is exercised for real.
  slow   every analyzed function sleeps IG_TEST_SLOW_S seconds (default 0.4), so a run can be cancelled.
  nonet  any socket connect/bind/listen in the server process raises and prints NETWORK-ATTEMPT to stderr
         (stdio transport only; the SDK's HTTP dependencies must stay unused).
Production code is unchanged; this only wraps orchestration.check_function before main() runs.
"""

import os
import socket
import subprocess
import sys
import time

from invariantguard import orchestration
from invariantguard.cli import main

MODE = set(filter(None, os.environ.get("IG_TEST_MCP_MODE", "").split(",")))
_real = orchestration.check_function


def _wrapped(*args, **kwargs):
    if "noisy" in MODE:
        print("NOISE-FROM-PRINT")
        sys.__stdout__.write("NOISE-FROM-DUNDER-STDOUT\n")
        subprocess.run(["/bin/echo", "NOISE-FROM-CHILD"], check=False)  # inherits fd 1
    if "slow" in MODE:
        time.sleep(float(os.environ.get("IG_TEST_SLOW_S", "0.4")))
    return _real(*args, **kwargs)


orchestration.check_function = _wrapped

if "nonet" in MODE:
    def _refuse(self, *args, **kwargs):
        sys.stderr.write(f"NETWORK-ATTEMPT {args!r}\n")
        raise OSError("network access is not allowed in this test")

    for _name in ("connect", "connect_ex", "bind", "listen"):
        setattr(socket.socket, _name, _refuse)
    socket.create_connection = lambda *a, **k: _refuse(None, *a)
raise SystemExit(main(sys.argv[1:]))
