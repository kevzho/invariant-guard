"""Brief `crosshair check` experiment in the same isolation flags. Modules: those containing assert statements in package code."""
import subprocess, json, os, time, sys
HERE = os.path.dirname(os.path.abspath(__file__)); IMAGE = open(os.path.join(HERE, "IMAGE_ID_six")).read().strip()
cases = json.load(open(os.path.join(HERE, "cases.json")))
def newest(repo): return next(c for c in cases if c["repo"] == repo)
TARGETS = [("more-itertools", "more_itertools.recipes"), ("boltons", "boltons.listutils"), ("boltons", "boltons.setutils"), ("boltons", "boltons.timeutils"),
           ("boltons", "boltons.funcutils"), ("dateutil", "dateutil.relativedelta"), ("dateutil", "dateutil.parser._parser")]
res = []
for kind in ("default", "asserts"):
  for repo, mod in TARGETS:
    c = newest(repo); d = os.path.join(HERE, "work", repo, c["short"])
    sp = os.path.normpath(os.path.join("/case/patch", c["syspath"]))
    args = ["crosshair", "check", "--per_condition_timeout=5", "--per_path_timeout=2"] + (["--analysis_kind=asserts"] if kind == "asserts" else []) + [mod]
    argv = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m",
            "--mount", f"type=bind,source={d},target=/case,readonly", "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "1", "--ipc", "none", "--log-driver", "none", "--workdir", "/tmp",
            "--env", "HOME=/tmp", "--env", f"PYTHONPATH={sp}", "--env", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", args[0], IMAGE] + args[1:]
    t = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, timeout=900); rc, out = r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired: rc, out = "timeout", ""
    res.append(dict(kind=kind, module=mod, rc=rc, wall_s=round(time.time() - t, 1), n_messages=len([l for l in out.splitlines() if ": error:" in l or ": warning:" in l]), out=out[-1500:]))
    print(kind, mod, rc, res[-1]["wall_s"], res[-1]["n_messages"], flush=True)
json.dump(res, open(os.path.join(HERE, "check_results.json"), "w"), indent=1)
