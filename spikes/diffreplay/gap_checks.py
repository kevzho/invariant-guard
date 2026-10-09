"""Reproducible checks of limitations in the existing probe harness (used unchanged)."""
import json

from changes import recent_commits, snapshot
from invariantguard.contracts.values import encode
from invariantguard.execution.profiles import RunProfileConfig
from invariantguard.execution.runner import DockerBackend, Workspace

prof = RunProfileConfig(name="gapcheck", backend="docker", allowed_targets=["*::*"], image="invariantguard-diffreplay:spike")
b = DockerBackend(prof)
assert b.status().available
out = {}
# 1) dateutil (needs six) under the trusted harness, which runs python -I -S -B
c = recent_commits("dateutil", 1)[0]
files = {f.path: f.raw for f in snapshot("dateutil", c["commit"]).files}
ws = Workspace(files, "gap")
try:
    req = b.probe_request(ws, {"module": "dateutil.easter", "function": "easter", "positional": ["year"], "keyword_only": [], "python_path_root": "src",
                               "probes": [{"id": "p0", "args": {"year": encode(2024)}}]})
    r = b.run(ws, req)
    out["dateutil_easter_result_encoding"] = r.response["observations"][0]["results"] if r.response else r.error
    req = b.probe_request(ws, {"module": "dateutil.parser._parser", "function": "parse", "positional": ["timestr"], "keyword_only": [],
                               "python_path_root": "src", "probes": [{"id": "p0", "args": {"timestr": encode("2024-01-01")}}]})
    r = b.run(ws, req)
    o = r.response["observations"][0] if r.response else r.error
    out["dateutil_parser_import_under_trusted_harness"] = {"status": o.get("status"), "detail": o.get("detail")} if isinstance(o, dict) else o
finally:
    ws.close()
# 2) generators are opaque in the trusted encoding: two different generators encode identically
files = {"g.py": b"def g(n):\n    yield from range(n)\n"}
ws = Workspace(files, "gap2")
try:
    req = b.probe_request(ws, {"module": "g", "function": "g", "positional": ["n"], "keyword_only": [], "python_path_root": ".",
                               "probes": [{"id": "p0", "args": {"n": encode(1)}}, {"id": "p1", "args": {"n": encode(5)}}]})
    r = b.run(ws, req)
    obs = r.response["observations"]
    out["generator_results"] = [o["results"][0] for o in obs]
    out["generator_results_equal"] = obs[0]["results"][0] == obs[1]["results"][0]
finally:
    ws.close()
print(json.dumps(out, indent=1))
