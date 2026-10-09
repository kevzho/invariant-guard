"""Run a repository's pytest selection inside the isolation boundary (spike; ig-probe-1 framing).

Executed by the same runner flags as the probe harness (python -I -S -B, read-only source,
no network). Only the outcome counts and failing node ids are returned.
"""
import json
import os
import sys

SITE = "/usr/local/lib/python%d.%d/site-packages" % sys.version_info[:2]


def main():
    req = json.loads(sys.stdin.buffer.read().decode())
    root = req["src_root"]
    sys.path[:0] = [os.path.normpath(os.path.join(root, req.get("python_path_root", "."))), root]
    sys.path.append(SITE)
    sys.dont_write_bytecode = True
    import pytest

    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    failed = []

    class Collector:
        def pytest_runtest_logreport(self, report):
            if report.when == "call" or report.outcome != "passed":
                if report.outcome == "failed":
                    key = "failed" if report.when == "call" else "error"
                    counts[key] += 1
                    failed.append(report.nodeid)
                elif report.when == "call" or report.outcome == "skipped":
                    counts[report.outcome] = counts.get(report.outcome, 0) + 1

    saved = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        os.chdir("/tmp")
        rc = pytest.main(["-q", "-p", "no:cacheprovider", "--rootdir", root, "-o", "addopts=", "-x" if req.get("exitfirst") else "-q",
                          *[os.path.join(root, a) for a in req["tests"]]], plugins=[Collector()])
    finally:
        sys.stdout.flush()  # pytest's buffered terminal output goes to /dev/null; fd 1 stays redirected
    data = json.dumps({"protocol": "ig-probe-1", "mode": "pytest", "exit_code": int(rc), "counts": counts, "failed": failed[:20]}).encode()
    while data:
        data = data[os.write(saved, data):]


if __name__ == "__main__":
    main()
