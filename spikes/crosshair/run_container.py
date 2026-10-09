"""Host-side orchestration. Every dynamic step goes through `docker run` with the same flags as
src/invariantguard/execution/runner.py (network none, read-only rootfs, noexec tmpfs, ro bind mounts,
nobody user, cap-drop ALL, no-new-privileges, pids/mem/cpu limits, empty env allowlist). Host never imports corpus code."""
import subprocess, json, os, uuid, time
HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.environ.get("IG_IMAGE_ID") or open(os.path.join(HERE, "IMAGE_ID")).read().strip()   # sha256 image id: --pull never, run by id

def docker_run(case_dir, payload, hard_timeout, extra_argv=()):
    name = "igx-" + uuid.uuid4().hex[:10]
    argv = ["docker", "run", "--rm", "--name", name, "--pull", "never", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m",
            "--mount", f"type=bind,source={case_dir},target=/case,readonly",
            "--mount", f"type=bind,source={HERE}/driver.py,target=/driver/driver.py,readonly",
            "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "1",
            "--ulimit", "core=0", "--ulimit", "nofile=256:256", "--ipc", "none", "--log-driver", "none",
            "--workdir", "/tmp", "--hostname", "ig-probe",
            "--env", "HOME=/tmp", "--env", "LANG=C.UTF-8", "--env", "PYTHONHASHSEED=0", "--env", "PYTHONDONTWRITEBYTECODE=1",
            *extra_argv,
            "--entrypoint", "python", IMAGE, "/driver/driver.py", json.dumps(payload)]
    t = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, timeout=hard_timeout)
        out, err, rc, killed = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"), r.returncode, False
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        out, err, rc, killed = (e.stdout or b"").decode("utf-8", "replace"), (e.stderr or b"").decode("utf-8", "replace"), None, True
    res = None
    if "@@RESULT@@" in out:
        try: res = json.loads(out.rsplit("@@RESULT@@", 1)[1].strip().splitlines()[0])
        except Exception: res = None
    if res is None:
        res = {"status": "hard_timeout" if killed else "container_error", "detail": (err or out)[-400:], "rc": rc}
    res["host_wall_s"] = round(time.time() - t, 2)
    return res
