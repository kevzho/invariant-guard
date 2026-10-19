"""InvariantGuard trusted probe harness (protocol ig-probe-1).

This file is *not* imported by the engine. The runner copies its bytes into a read-only
mount and executes it with ``python -P -s -S -B`` inside the isolation boundary (a
container, or an explicitly unisolated subprocess). It is standalone (stdlib only).
``-I`` is not used: it implies ``-E``, which ignores ``PYTHONHASHSEED``, so string
hashing (set and dict iteration order of str keys) would be randomized per run. The
environment is fully constructed by the runner instead, and the ``interpreter_env``
capability fails closed on any other interpreter variable (e.g. from the image).

Protocol: one JSON request on stdin, one JSON response on stdout.

* ``identity``: interpreter identity.
* ``capabilities``: checks, from inside the boundary, what the run profile claims:
  network denied, read-only source, writes only to scratch, no host credentials,
  resource limits, interpreter version. Nothing here sends traffic: the connect test
  runs only when no interface is up and no route exists, and targets TEST-NET-1.
* ``probe``: for each probe, fork a child in a new session that imports the target
  module from the copied snapshot, calls the allowlisted function with the decoded
  inputs (fresh copies), snapshots the arguments before/after the call, encodes the
  result or exception, and writes it to a pipe. The parent never imports target code,
  enforces the per-probe wall limit (SIGKILL to the child's process group) and the
  result size limit, and returns structured observations.

Values use the tagged encoding of ``invariantguard.contracts.values`` (copied below and
checked for agreement by ``tests/unit/test_values.py``). Only exact built-in types are
encoded, so encoding never calls a method defined by the target.
"""

import errno
import importlib
import json
import os
import select
import signal
import sys
import time
import traceback
import types

PROTOCOL = "ig-probe-1"
MAX_REQUEST = 4 * 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100_000
MAX_INT_DIGITS = 4000
MAX_STR = 65_536


# -- value encoding (keep identical to invariantguard/contracts/values.py) --------------------
def encode(value, max_depth=MAX_DEPTH, max_nodes=MAX_NODES):
    count = [0]

    def enc(x, depth, active):
        count[0] += 1
        if count[0] > max_nodes:
            return {"t": "opaque", "type": type(x).__name__, "why": "too many nodes"}
        if depth > max_depth:
            return {"t": "opaque", "type": type(x).__name__, "why": "nesting too deep"}
        t = type(x)
        if x is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": x}
        if t is int:
            s = str(x) if x.bit_length() < 13_000 else None
            if s is None or len(s.lstrip("-")) > MAX_INT_DIGITS:
                return {"t": "opaque", "type": "int", "why": "integer too large"}
            return {"t": "int", "v": s}
        if t is float:
            return {"t": "float", "v": x.hex()}
        if t is str:
            if len(x) > MAX_STR:
                return {"t": "opaque", "type": "str", "why": "string too long"}
            if not _scalar_text(x):
                return {"t": "opaque", "type": "str", "why": "lone surrogate (not Unicode text)"}
            return {"t": "str", "v": x}
        if t in (list, tuple, dict):
            if id(x) in active:
                return {"t": "opaque", "type": t.__name__, "why": "cyclic value"}
            active.add(id(x))
            try:
                if t is dict:
                    items = []
                    for k, v in x.items():
                        if type(k) is not str:
                            return {"t": "opaque", "type": "dict", "why": "non-string key"}
                        if not _scalar_text(k):
                            return {"t": "opaque", "type": "dict", "why": "key with a lone surrogate"}
                        items.append([k, enc(v, depth + 1, active)])
                    return {"t": "dict", "v": items}
                return {"t": t.__name__, "v": [enc(v, depth + 1, active) for v in x]}
            finally:
                active.discard(id(x))
        return {"t": "opaque", "type": "%s.%s" % (t.__module__, t.__qualname__), "why": "unsupported type"}

    return enc(value, 0, set())


def _scalar_text(s):
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _text(s):
    """Diagnostic text (exception messages, frame names) as Unicode scalar values only."""
    return s if _scalar_text(s) else s.encode("utf-8", "backslashreplace").decode("utf-8")


def decode(n, depth=0):
    """Inputs come from the trusted host; decoding is still strict."""
    if depth > MAX_DEPTH or type(n) is not dict:
        raise ValueError("bad value node")
    t = n.get("t")
    if t == "none":
        return None
    if t == "bool" and type(n.get("v")) is bool:
        return n["v"]
    if t == "int" and type(n.get("v")) is str and len(n["v"]) <= MAX_INT_DIGITS + 1:
        return int(n["v"])
    if t == "float" and type(n.get("v")) is str:
        return float.fromhex(n["v"])
    if t == "str" and type(n.get("v")) is str:
        return n["v"]
    if t in ("list", "tuple") and type(n.get("v")) is list:
        items = [decode(x, depth + 1) for x in n["v"]]
        return items if t == "list" else tuple(items)
    if t == "dict" and type(n.get("v")) is list:
        return {k: decode(v, depth + 1) for k, v in n["v"]}
    raise ValueError("unsupported input node %r" % (t,))


# -- I/O ------------------------------------------------------------------------------------------
def _read_request():
    data = b""
    while len(data) <= MAX_REQUEST:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        data += chunk
    if len(data) > MAX_REQUEST:
        raise ValueError("request too large")
    return json.loads(data.decode("utf-8"))


def _emit(obj, fd=1):
    data = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def _identity():
    import platform

    return {"implementation": sys.implementation.name, "version": platform.python_version(),
            "version_info": list(sys.version_info[:3]), "platform": sys.platform, "machine": platform.machine(),
            "executable_flags": {"isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site),
                                 "dont_write_bytecode": bool(sys.flags.dont_write_bytecode)},
            "hash_seed": os.environ.get("PYTHONHASHSEED"), "hash_randomization": sys.flags.hash_randomization,
            "ignore_environment": bool(sys.flags.ignore_environment)}


# Interpreter variables the runner sets on purpose, and metadata variables of the official
# python images that CPython does not read. Any other PYTHON* variable can change how the
# interpreter behaves (PYTHONPATH, PYTHONHOME, PYTHONWARNINGS, PYTHON_GIL, ...).
_EXPECTED_INTERPRETER_ENV = frozenset({"PYTHONHASHSEED", "PYTHON_VERSION", "PYTHON_SHA256", "PYTHON_PIP_VERSION",
                                       "PYTHON_SETUPTOOLS_VERSION", "PYTHON_GET_PIP_URL", "PYTHON_GET_PIP_SHA256"})


def _cap_interpreter_env():
    """Deterministic string hashing, and no interpreter variable beyond the runner's own."""
    unexpected = sorted(k for k in os.environ if k.startswith("PYTHON") and k not in _EXPECTED_INTERPRETER_ENV)
    seed = os.environ.get("PYTHONHASHSEED")
    deterministic = seed is not None and seed.isdigit() and not sys.flags.ignore_environment and sys.flags.hash_randomization == 0
    return {"verified": deterministic and not unexpected, "hash_seed": seed, "hash_randomization": sys.flags.hash_randomization,
            "ignore_environment": bool(sys.flags.ignore_environment), "unexpected_interpreter_env": unexpected}


# -- capability checks (inside the boundary) -------------------------------------------------------
def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read()
    except OSError:
        return None


def _mounts():
    out = []
    for line in (_read("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            out.append({"source": parts[0], "target": parts[1], "fstype": parts[2], "options": parts[3].split(",")})
    return out


def _try_write(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        return {"path": path, "written": False, "errno": errno.errorcode.get(exc.errno, str(exc.errno))}
    os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        pass
    return {"path": path, "written": True, "errno": None}


def _cap_network():
    ifaces, up = [], []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []
    for name in names:
        flags = _read("/sys/class/net/%s/flags" % name)
        if flags is None:
            continue
        ifaces.append(name)
        if name != "lo" and int(flags.strip(), 16) & 0x1:
            up.append(name)
    routes = [l for l in (_read("/proc/net/route") or "").splitlines()[1:] if l.strip()]
    v6 = [l for l in (_read("/proc/net/ipv6_route") or "").splitlines() if l.split() and l.split()[-1] != "lo"]
    connect = "not attempted (an interface or route exists)"
    ok = not up and not routes and not v6
    if ok:
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1; unreachable by construction here
            connect, ok = "connected (network reachable)", False
        except OSError as exc:
            connect = "refused: %s" % errno.errorcode.get(exc.errno, str(exc.errno))
            ok = exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EACCES, errno.EPERM)
        finally:
            s.close()
    return {"verified": ok, "interfaces_up": up, "interfaces": ifaces, "ipv4_routes": len(routes), "ipv6_routes_off_lo": len(v6), "connect_test": connect}


def _cap_readonly_source(src_root):
    w = _try_write(os.path.join(src_root, ".ig-write-probe"))
    m = [x for x in _mounts() if x["target"] == src_root]
    ro = bool(m) and "ro" in m[-1]["options"]
    return {"verified": (not w["written"]) and ro, "write_attempt": w, "mount_read_only": ro}


def _cap_scratch(src_root, scratch):
    attempts = [_try_write(p) for p in ("/ig-write-probe", "/etc/ig-write-probe", "/usr/ig-write-probe", os.path.dirname(src_root) + "/ig-write-probe")]
    scratch_ok = _try_write(os.path.join(scratch, "ig-write-probe"))["written"]
    root_ro = any(x["target"] == "/" and "ro" in x["options"] for x in _mounts())
    virtual = {"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "tmpfs", "devtmpfs", "securityfs", "debugfs"}
    managed = {"/etc/hosts", "/etc/hostname", "/etc/resolv.conf"}
    rw_other = [x["target"] for x in _mounts() if "rw" in x["options"] and x["fstype"] not in virtual and x["target"] not in managed]
    rw_tmpfs = sorted({x["target"] for x in _mounts() if "rw" in x["options"] and x["fstype"] == "tmpfs"})
    ok = not any(a["written"] for a in attempts) and scratch_ok and root_ro and not rw_other
    return {"verified": ok, "write_attempts": attempts, "scratch_writable": scratch_ok, "root_read_only": root_ro,
            "writable_non_virtual_mounts": rw_other, "writable_tmpfs": rw_tmpfs,
            "container_managed_files": sorted(managed & {x["target"] for x in _mounts()})}


def _cap_credentials(allowed_env):
    keys = sorted(os.environ)
    unexpected = [k for k in keys if k not in allowed_env]
    paths = ["/var/run/docker.sock", "/run/docker.sock", "/root/.ssh", "/root/.aws", "/root/.docker", "/root/.config", "/run/secrets",
             os.path.expanduser("~/.ssh"), os.path.expanduser("~/.netrc")]
    present = [p for p in paths if os.path.exists(p)]
    status = _read("/proc/self/status") or ""
    cap_eff = next((l.split()[1] for l in status.splitlines() if l.startswith("CapEff:")), None)
    uid = os.getuid()
    ok = not unexpected and not present and uid != 0 and cap_eff is not None and int(cap_eff, 16) == 0
    return {"verified": ok, "env_keys": keys, "unexpected_env": unexpected, "credential_paths_present": present, "uid": uid,
            "effective_capabilities": cap_eff}


def _cap_limits(expect):
    import resource

    mem = (_read("/sys/fs/cgroup/memory.max") or "").strip()
    pids = (_read("/sys/fs/cgroup/pids.max") or "").strip()
    cpu = (_read("/sys/fs/cgroup/cpu.max") or "").strip()
    cpu_soft, _ = resource.getrlimit(resource.RLIMIT_CPU)
    checks = {
        "memory_bytes": [mem, str(expect.get("memory_bytes"))],
        "pids": [pids, str(expect.get("pids"))],
        "cpu_max": [cpu, expect.get("cpu_max")],
        "rlimit_cpu_seconds": [str(cpu_soft), str(expect.get("cpu_seconds"))],
    }
    ok = all(a == b for a, b in checks.values())
    return {"verified": ok, "observed_vs_expected": checks}


def _capabilities(req):
    src_root = req["src_root"]
    exp = req.get("expect", {})
    ver = list(sys.version_info[:2])
    return {
        "deny_network": _cap_network(),
        "readonly_source": _cap_readonly_source(src_root),
        "scratch_only_writes": _cap_scratch(src_root, req.get("scratch", "/tmp")),
        "no_host_credentials": _cap_credentials(set(req.get("allowed_env", []))),
        "resource_limits": _cap_limits(exp),
        "interpreter": {"verified": ver == exp.get("python_minor"), "observed": ver, "expected": exp.get("python_minor")},
        "interpreter_env": _cap_interpreter_env(),
    }


# -- probes ---------------------------------------------------------------------------------------
def _exc_summary(exc, src_prefix):
    t = type(exc)
    name = _text(t.__qualname__ if t.__module__ == "builtins" else "%s.%s" % (t.__module__, t.__qualname__))
    try:
        msg = _text(str(exc)[:500])
    except BaseException:  # noqa: BLE001 - str() of an exception runs target code
        msg = "<unprintable>"
    frames = []
    try:
        stack = traceback.StackSummary.extract(traceback.walk_tb(exc.__traceback__), lookup_lines=False)
        for fs in list(stack)[-12:]:
            if fs.filename.startswith(src_prefix):
                frames.append({"path": _text(fs.filename[len(src_prefix):]), "line": fs.lineno, "function": _text(fs.name)})
    except BaseException:  # noqa: BLE001
        pass
    return {"type": name, "message": msg, "frames": frames}


def _resolve_callable(module, req):
    """(function, leading bound arguments, None) or (None, None, why). Reads only raw
    namespaces (module and class ``__dict__``), so no descriptor or metaclass hook of the
    target runs while the callable is located."""
    name, owner = req["function"], req.get("owner")
    if owner is None:
        fn = module.__dict__.get(name)
        if type(fn) is not types.FunctionType:
            return None, None, "%s is %s, not a plain function" % (name, type(fn).__name__)
        return fn, [], None
    cls = module.__dict__.get(owner)
    if not issubclass(type(cls), type):
        return None, None, "%s is %s, not a class" % (owner, type(cls).__name__)
    raw = type.__dict__["__dict__"].__get__(cls, type(cls)).get(name)
    want = {"staticmethod": staticmethod, "classmethod": classmethod}.get(req.get("binding"))
    if want is None or type(raw) is not want:
        return None, None, "%s.%s is %s in the class namespace, not a %s" % (owner, name, type(raw).__name__, req.get("binding"))
    fn = raw.__func__
    if type(fn) is not types.FunctionType:
        return None, None, "%s.%s wraps %s, not a plain function" % (owner, name, type(fn).__name__)
    if fn.__qualname__ != "%s.%s" % (owner, name) or fn.__module__ != req["module"]:
        return None, None, "%s.%s wraps %s.%s, not the analyzed def" % (owner, name, fn.__module__, fn.__qualname__)
    return fn, ([cls] if want is classmethod else []), None


def _child(req, probe, wfd):
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    os.closerange(3, wfd)
    os.closerange(wfd + 1, 4096)
    try:
        import resource

        cpu = int(req.get("probe_cpu_seconds", 5))
        soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
        if hard == resource.RLIM_INFINITY or cpu < hard:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu if hard == resource.RLIM_INFINITY else min(cpu + 1, hard)))
    except (ValueError, OSError):
        pass
    src_root = req["src_root"]
    import_root = os.path.normpath(os.path.join(src_root, req.get("python_path_root", ".")))
    sys.path[:0] = [import_root]
    sys.dont_write_bytecode = True
    out = {"status": "ok", "results": [], "before": None, "after": None}
    try:
        import random

        random.seed(int(probe.get("seed", 0)))
        try:
            module = importlib.import_module(req["module"])
        except BaseException as exc:  # noqa: BLE001 - import runs target code
            out = {"status": "import_error", "detail": _exc_summary(exc, src_root.rstrip("/") + "/")}
            raise _Done()
        fn, bound, why = _resolve_callable(module, req)
        if why is not None:
            out = {"status": "target_error", "detail": {"type": "TargetError", "message": why, "frames": []}}
            raise _Done()
        repeats = int(probe.get("repeats", 1))
        for i in range(repeats):
            args = {k: decode(v) for k, v in probe["args"].items()}
            if i == 0:
                out["before"] = {k: encode(v) for k, v in args.items()}
            pos = bound + [args[n] for n in req["positional"]]
            kw = {n: args[n] for n in req["keyword_only"]}
            try:
                value = fn(*pos, **kw)
                res = {"kind": "returned", "value": encode(value)}
            except BaseException as exc:  # noqa: BLE001 - the target raised
                res = {"kind": "raised", "exception": _exc_summary(exc, src_root.rstrip("/") + "/")}
            out["results"].append(res)
            if i == 0:
                out["after"] = {k: encode(v) for k, v in args.items()}
    except _Done:
        pass
    except BaseException as exc:  # noqa: BLE001
        out = {"status": "harness_error", "detail": {"type": type(exc).__name__, "message": str(exc)[:300], "frames": []}}
    try:
        data = json.dumps(out, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError):
        data = b'{"status":"harness_error","detail":{"type":"EncodeError","message":"unencodable observation","frames":[]}}'
    limit = int(req.get("max_result_bytes", 1 << 20))
    if len(data) > limit:
        data = json.dumps({"status": "output_limit", "detail": {"type": "OutputLimit", "message": "observation of %d bytes exceeds %d" % (len(data), limit), "frames": []}}).encode()
    # Length-prefixed frame: the parent stops reading once the frame is complete, even if
    # processes forked by the target still hold the pipe open.
    data = len(data).to_bytes(8, "big") + data
    view = memoryview(data)
    while view:
        n = os.write(wfd, view)
        view = view[n:]
    os._exit(0)


class _Done(Exception):
    pass


def _run_probe(req, probe):
    rfd, wfd = os.pipe()
    t0 = time.monotonic()
    pid = os.fork()
    if pid == 0:  # child
        try:
            os.close(rfd)
            os.setsid()
            _child(req, probe, wfd)
        finally:
            os._exit(70)
    os.close(wfd)
    limit = int(req.get("max_result_bytes", 1 << 20))
    deadline = t0 + float(req.get("probe_wall_seconds", 5))
    chunks, size, timed_out, too_big = [], 0, False, False
    expected = None
    while True:
        if expected is not None and size >= expected:
            break  # complete frame received
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([rfd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(rfd, 65536)
        if not chunk:
            break
        size += len(chunk)
        if size > limit + 4096 + 8:
            too_big = True
            break
        chunks.append(chunk)
        if expected is None and size >= 8:
            expected = 8 + int.from_bytes(b"".join(chunks)[:8], "big")
            if expected > limit + 4096 + 8:
                too_big = True
                break
    os.close(rfd)
    if timed_out or too_big:
        for target in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                target()
            except (ProcessLookupError, PermissionError):
                pass
    _, status = os.waitpid(pid, 0)
    # Reap anything left in the child's session (best effort; the container is discarded).
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    elapsed = round((time.monotonic() - t0) * 1000, 3)
    if timed_out:
        return {"id": probe["id"], "status": "timeout", "elapsed_ms": elapsed,
                "detail": {"type": "Timeout", "message": "probe exceeded %ss wall time and was killed" % req.get("probe_wall_seconds", 5), "frames": []}}
    if too_big:
        return {"id": probe["id"], "status": "output_limit", "elapsed_ms": elapsed, "detail": {"type": "OutputLimit", "message": "observation too large", "frames": []}}
    raw = b"".join(chunks)
    try:
        if len(raw) < 8 or len(raw) < 8 + int.from_bytes(raw[:8], "big"):
            raise ValueError("incomplete frame")
        obs = json.loads(raw[8:8 + int.from_bytes(raw[:8], "big")].decode("utf-8"))
        if type(obs) is not dict or "status" not in obs:
            raise ValueError("not an observation")
    except (ValueError, RecursionError):  # a forged, deeply nested frame is not an observation
        sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
        code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else None
        return {"id": probe["id"], "status": "crashed", "elapsed_ms": elapsed,
                "detail": {"type": "Crashed", "message": "probe process ended without an observation (exit %s, signal %s)" % (code, sig), "frames": []}}
    obs["id"] = probe["id"]
    obs["elapsed_ms"] = elapsed
    return obs


def _probe(req):
    probes = req["probes"]
    if len(probes) > 512:
        raise ValueError("too many probes in one request")
    return [_run_probe(req, p) for p in probes]


def main():
    try:
        req = _read_request()
        mode = req.get("mode")
        if mode == "identity":
            resp = {"protocol": PROTOCOL, "mode": mode, "identity": _identity()}
        elif mode == "capabilities":
            resp = {"protocol": PROTOCOL, "mode": mode, "identity": _identity(), "capabilities": _capabilities(req)}
        elif mode == "probe":
            resp = {"protocol": PROTOCOL, "mode": mode, "identity": _identity(), "observations": _probe(req)}
        else:
            resp = {"protocol": PROTOCOL, "mode": "error", "error": "unknown mode %r" % (mode,)}
    except Exception as exc:  # noqa: BLE001 - reported to the host as a harness error
        resp = {"protocol": PROTOCOL, "mode": "error", "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    _emit(resp)


if __name__ == "__main__":
    main()
