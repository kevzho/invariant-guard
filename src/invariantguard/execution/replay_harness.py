"""InvariantGuard differential-replay harness (protocol ig-replay-1).

Runs INSIDE the isolation boundary only. Like ``probe_harness.py`` it is not imported by
the engine: the runner copies its bytes into the read-only harness mount and executes it
with the same interpreter flags and container profile. Standalone (stdlib only).

Protocol: one JSON request on stdin, one JSON response on stdout.

* ``identity``: interpreter identity and, when the profile enables the image's
  site-packages, the installed distributions (name, version) for the environment digest.
* ``replay``: for each probe, call a target and observe the outcome:
  - targets: module functions, classes (constructor calls), static and class methods,
    instance methods and properties on a receiver built inside the probe;
  - inputs: tagged built-in values, or repository-authored expressions (from the
    snapshot's tests and doctests; the host checks that each expression text occurs in
    the snapshot) evaluated with a small builtin namespace;
  - outcomes: the returned value (rich encoding, iterators materialized to a bounded
    prefix including a lazily raised exception), or the raised exception, plus mutated
    mutable arguments and the receiver's state after the call.

Process structure: the top-level process never imports target code. A zygote child
imports the target modules once; probes run in grandchildren forked from it, in chunks of
probes of one target (fresh post-import state per chunk) with a per-probe interval timer
and RLIMIT_CPU. A chunk that hangs in C code or crashes is re-run one probe per child,
hard-killed at the wall limit, so a timeout is attributed to exactly one probe. Each
encoded outcome larger than ``max_observation_bytes`` is replaced by its content digest
(still exactly comparable), so one large value cannot lose the batch.

Everything a probe returns is produced by untrusted target code. Encoding objects calls
target code (``__iter__``, ``isoformat``) inside the probe child only; the host treats
the whole response as untrusted data.
"""

import json
import os
import re
import resource
import select
import signal
import sys
import time
import traceback

PROTOCOL = "ig-replay-1"
MAX_DEPTH = 24
MAX_NODES = 20000
MAX_STR = 8192
MAX_INT_BITS = 13000
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{6,}")

SAFE_BUILTINS = (
    "abs all any bool bytearray bytes callable chr complex dict divmod enumerate filter float frozenset hash int isinstance "
    "issubclass iter len list map max min next object ord pow range repr reversed round set slice sorted str sum tuple type zip "
    "None True False Ellipsis NotImplemented "
    "ArithmeticError AssertionError AttributeError Exception IndexError KeyError LookupError OverflowError RuntimeError "
    "StopIteration TypeError ValueError ZeroDivisionError"
).split()


# -- value encoding ----------------------------------------------------------------------------
# Core tags are those of invariantguard.contracts.values; the rest are replay extensions.
# invariantguard.replay.values carries the host-side copy (agreement is tested).
def _qn(t):
    return t.__qualname__ if t.__module__ == "builtins" else "%s.%s" % (t.__module__, t.__qualname__)


def exc_summary(exc):
    t = type(exc)
    try:
        msg = str(exc)[:400]
    except BaseException:  # noqa: BLE001 - str() of an exception runs target code
        msg = "<unprintable>"
    return {"type": _qn(t), "message": ADDR_RE.sub("0x?", msg)}


def _sort_key(node):
    return json.dumps(node, sort_keys=True, separators=(",", ":"))


def encode(value, iter_prefix=100, materialize=True):
    count = [0]

    def items_of(it, depth, active):
        out, err, trunc = [], None, False
        try:
            for i, x in enumerate(it):
                if i >= iter_prefix:
                    trunc = True
                    break
                out.append(enc(x, depth + 1, active))
        except BaseException as exc:  # noqa: BLE001 - lazy failure during materialization
            err = exc_summary(exc)
        return out, err, trunc

    def enc(x, depth, active):
        count[0] += 1
        if count[0] > MAX_NODES:
            return {"t": "opaque", "type": _qn(type(x)), "why": "too many nodes"}
        if depth > MAX_DEPTH:
            return {"t": "opaque", "type": _qn(type(x)), "why": "nesting too deep"}
        t = type(x)
        if x is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": x}
        if t is int:
            if x.bit_length() > MAX_INT_BITS:
                return {"t": "opaque", "type": "int", "why": "integer too large"}
            return {"t": "int", "v": str(x)}
        if t is float:
            return {"t": "float", "v": x.hex()}
        if t is str:
            if len(x) > MAX_STR:
                return {"t": "opaque", "type": "str", "why": "string longer than %d" % MAX_STR}
            return {"t": "str", "v": x}
        if t in (list, tuple, dict, set, frozenset) and id(x) in active:
            return {"t": "opaque", "type": t.__name__, "why": "cyclic value"}
        if t in (list, tuple):
            active.add(id(x))
            try:
                return {"t": t.__name__, "v": [enc(v, depth + 1, active) for v in x]}
            finally:
                active.discard(id(x))
        if t is dict:
            active.add(id(x))
            try:
                if all(type(k) is str for k in x):
                    return {"t": "dict", "v": [[k, enc(v, depth + 1, active)] for k, v in x.items()]}
                return {"t": "xdict", "v": [[enc(k, depth + 1, active), enc(v, depth + 1, active)] for k, v in x.items()]}
            finally:
                active.discard(id(x))
        if t in (set, frozenset):
            active.add(id(x))
            try:
                return {"t": t.__name__, "v": sorted((enc(v, depth + 1, active) for v in x), key=_sort_key)}
            finally:
                active.discard(id(x))
        if t in (bytes, bytearray):
            return {"t": t.__name__, "v": bytes(x).hex()}
        if t is complex:
            return {"t": "complex", "v": [x.real.hex(), x.imag.hex()]}
        if t is range:
            return {"t": "range", "v": [x.start, x.stop, x.step]}
        if t is slice:
            return {"t": "slice", "v": [enc(x.start, depth + 1, active), enc(x.stop, depth + 1, active), enc(x.step, depth + 1, active)]}
        mod = t.__module__
        if mod == "datetime" and t.__name__ in ("datetime", "date", "time", "timedelta", "timezone"):
            out = {"t": "dt", "type": t.__name__}
            try:
                out["v"] = x.isoformat() if hasattr(x, "isoformat") else repr(x)
                tz = getattr(x, "tzinfo", None)
                if tz is not None:
                    out["tz"] = {"type": _qn(type(tz)), "offset": str(x.utcoffset()) if t.__name__ == "datetime" else None,
                                 "name": x.tzname() if t.__name__ == "datetime" else None}
            except BaseException as exc:  # noqa: BLE001 - tzinfo methods are target code
                out["error"] = exc_summary(exc)
            return out
        if mod in ("decimal", "fractions") and t.__name__ in ("Decimal", "Fraction"):
            return {"t": "num", "type": t.__name__, "v": str(x)}
        if isinstance(x, BaseException):
            return {"t": "exception", "type": _qn(t), "args": enc(tuple(x.args), depth + 1, active)}
        if isinstance(x, type):
            return {"t": "class", "name": _qn(x)}
        if t.__name__ in ("function", "builtin_function_or_method", "method", "method-wrapper", "wrapper_descriptor", "partial"):
            return {"t": "callable", "name": str(getattr(x, "__qualname__", t.__name__))}
        if id(x) in active:
            return {"t": "opaque", "type": _qn(t), "why": "cyclic value"}
        active.add(id(x))
        try:
            if hasattr(t, "__next__") and hasattr(t, "__iter__"):
                if not materialize:
                    return {"t": "iter", "type": _qn(t), "materialized": False}
                v, err, trunc = items_of(x, depth, active)
                out = {"t": "iter", "type": _qn(t), "v": v, "truncated": trunc}
                if err:
                    out["raised"] = err
                return out
            out = {"t": "object", "type": _qn(t)}
            if isinstance(x, dict):
                out["items"] = [[enc(k, depth + 1, active), enc(v, depth + 1, active)] for k, v in dict.items(x)]
            elif isinstance(x, (list, tuple)):
                out["items"] = [enc(v, depth + 1, active) for v in (list.__iter__(x) if isinstance(x, list) else tuple.__iter__(x))]
            elif isinstance(x, (set, frozenset)):
                out["items"] = sorted((enc(v, depth + 1, active) for v in x), key=_sort_key)
            try:
                d = object.__getattribute__(x, "__dict__")
            except AttributeError:
                d = None
            state = []
            if isinstance(d, dict):
                for k in sorted(k for k in d if type(k) is str):
                    state.append([k, enc(d[k], depth + 1, active)])
            for klass in t.__mro__:
                slots = klass.__dict__.get("__slots__", ())
                for s in (slots,) if isinstance(slots, str) else (slots if isinstance(slots, (tuple, list)) else ()):
                    try:
                        state.append([s, enc(object.__getattribute__(x, s), depth + 1, active)])
                    except BaseException:  # noqa: BLE001 - unset slot
                        pass
            if state:
                out["state"] = state
            if "items" not in out and materialize and hasattr(t, "__iter__"):
                try:
                    it = iter(x)
                    if it is not x:
                        v, err, trunc = items_of(it, depth, active)
                        out["iter"] = v
                        out["iter_truncated"] = trunc
                        if err:
                            out["iter_raised"] = err
                except BaseException as exc:  # noqa: BLE001
                    out["iter_error"] = exc_summary(exc)
            return out
        finally:
            active.discard(id(x))

    return enc(value, 0, set())


def decode(n, depth=0):
    """Decode an input node (built-in values only; never constructs target types)."""
    if depth > MAX_DEPTH or type(n) is not dict:
        raise ValueError("bad value node")
    t = n.get("t")
    v = n.get("v")
    if t == "none":
        return None
    if t == "bool":
        return bool(v)
    if t == "int":
        return int(v)
    if t == "float":
        return float.fromhex(v)
    if t == "str":
        return v
    if t == "list":
        return [decode(x, depth + 1) for x in v]
    if t == "tuple":
        return tuple(decode(x, depth + 1) for x in v)
    if t == "dict":
        return {k: decode(x, depth + 1) for k, x in v}
    if t == "xdict":
        return {decode(k, depth + 1): decode(x, depth + 1) for k, x in v}
    if t == "set":
        return {decode(x, depth + 1) for x in v}
    if t == "frozenset":
        return frozenset(decode(x, depth + 1) for x in v)
    if t == "bytes":
        return bytes.fromhex(v)
    if t == "bytearray":
        return bytearray.fromhex(v)
    if t == "complex":
        return complex(float.fromhex(v[0]), float.fromhex(v[1]))
    if t == "range":
        return range(*v)
    raise ValueError("not a decodable input node %r" % (t,))


def _cap(node, limit):
    """Replace an encoding larger than ``limit`` bytes by its content digest."""
    import hashlib

    data = json.dumps(node, sort_keys=True, separators=(",", ":"))
    if len(data) <= limit:
        return node
    return {"t": "digest", "sha256": hashlib.sha256(data.encode()).hexdigest(), "bytes": len(data), "type": node.get("type") or node.get("t")}


# -- inputs and targets ------------------------------------------------------------------------
def build_ns(spec, cache):
    import builtins
    import importlib

    key = json.dumps(spec, sort_keys=True)
    if key in cache:
        return cache[key]
    bi = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    bi["__import__"] = builtins.__import__  # C-level helpers (e.g. strftime) import internally
    ns = {"__builtins__": bi}
    if spec.get("module_globals"):
        mod = importlib.import_module(spec["module_globals"])
        ns.update({k: v for k, v in vars(mod).items() if not k.startswith("__")})
    for local, modname, attr in spec.get("imports", []):
        try:
            m = importlib.import_module(modname)
            ns[local] = getattr(m, attr) if attr else m
        except BaseException:  # noqa: BLE001 - a missing name makes the expression fail (input_error)
            pass
    cache[key] = ns
    return ns


def materialize_arg(spec, namespaces, cache):
    if "v" in spec:
        return decode(spec["v"])
    ns = build_ns(namespaces[spec["ns"]], cache)
    # Repository-authored test/doctest text (the host verified it occurs in the snapshot);
    # evaluated inside the isolation boundary only.
    return eval(compile(spec["x"], "<repository input>", "eval"), dict(ns))


def resolve(target):
    import importlib
    import inspect

    mod = importlib.import_module(target["module"])
    parts = target["qualname"].split(".")
    owner = mod
    for p in parts[:-1]:
        owner = getattr(owner, p)
    name = parts[-1]
    if owner is mod:
        return "function", getattr(mod, name), None
    raw = inspect.getattr_static(owner, name)
    if isinstance(raw, type):
        return "function", getattr(owner, name), None
    if isinstance(raw, (staticmethod, classmethod)):
        return "static", getattr(owner, name), owner
    if isinstance(raw, property):
        return "property", raw, owner
    return "method", raw, owner


def _mutables(args, kwargs):
    out = {}
    for i, a in enumerate(args):
        if type(a) in (list, dict, set, bytearray):
            out["arg%d" % i] = a
    for k, a in kwargs.items():
        if type(a) in (list, dict, set, bytearray):
            out[k] = a
    return out


def run_probe(req, probe):
    import random

    random.seed(int(probe.get("seed", 0)))
    t = req["targets"][probe["tid"]]
    namespaces = req.get("namespaces", {})
    cache = {}
    try:
        kind, fn, owner = resolve(t)
    except BaseException as exc:  # noqa: BLE001
        return {"status": "resolve_error", "detail": exc_summary(exc)}
    try:
        args = [materialize_arg(a, namespaces, cache) for a in probe.get("args", [])]
        kwargs = {k: materialize_arg(a, namespaces, cache) for k, a in probe.get("kwargs", {}).items()}
        recv = None
        r = probe.get("receiver")
        if r is not None:
            if "construct" in r:
                c = r["construct"]
                recv = owner(*[materialize_arg(a, namespaces, cache) for a in c.get("args", [])],
                             **{k: materialize_arg(a, namespaces, cache) for k, a in c.get("kwargs", {}).items()})
            else:
                recv = materialize_arg(r, namespaces, cache)
    except BaseException as exc:  # noqa: BLE001 - the input could not be built (not a target outcome)
        return {"status": "input_error", "detail": exc_summary(exc)}
    if kind in ("method", "property") and recv is None:
        return {"status": "input_error", "detail": {"type": "NoReceiver", "message": "instance method or property without a receiver"}}
    limit = int(req.get("max_observation_bytes", 65536))
    prefix = int(req.get("iter_prefix", 100))
    mutable = _mutables(args, kwargs)
    before = {k: encode(v) for k, v in mutable.items()}
    recv_before = encode(recv, materialize=False) if recv is not None else None
    try:
        if kind == "property":
            value = fn.__get__(recv, owner)
        elif kind == "method":
            value = getattr(recv, t["qualname"].split(".")[-1])(*args, **kwargs)
        else:
            value = fn(*args, **kwargs)
        res = {"kind": "returned", "value": _cap(encode(value, prefix), limit)}
    except BaseException as exc:  # noqa: BLE001 - the target raised
        res = {"kind": "raised", "exception": exc_summary(exc)}
    out = {"status": "ok", "result": res}
    mut = {}
    for k, v in mutable.items():
        after = encode(v)
        if after != before[k]:
            mut[k] = _cap(after, limit)
    if mut:
        out["mutated"] = mut
    if recv is not None:
        ra = encode(recv, materialize=False)
        if ra != recv_before:
            out["receiver_after"] = _cap(ra, limit)
    return out


# -- process structure -------------------------------------------------------------------------
def _write_frame(fd, obj):
    try:
        data = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        data = json.dumps({"status": "harness_error", "detail": {"type": "EncodeError", "message": str(exc)[:200]}}).encode()
    data = len(data).to_bytes(8, "big") + data
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _detach_stdio():
    os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)


def _read_frame(rfd, deadline):
    chunks, size, expected = [], 0, None
    while expected is None or size < expected:
        rem = deadline - time.monotonic()
        if rem <= 0:
            return None, True
        r, _, _ = select.select([rfd], [], [], rem)
        if not r:
            continue
        c = os.read(rfd, 1 << 16)
        if not c:
            break
        chunks.append(c)
        size += len(c)
        if expected is None and size >= 8:
            expected = 8 + int.from_bytes(b"".join(chunks)[:8], "big")
    raw = b"".join(chunks)
    if len(raw) < 8 or len(raw) < 8 + int.from_bytes(raw[:8], "big"):
        return None, False
    try:
        return json.loads(raw[8:8 + int.from_bytes(raw[:8], "big")].decode()), False
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None, False


def _kill(pid):
    for k in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
        try:
            k()
        except OSError:
            pass


def _forked(fn, wall, cpu_seconds):
    """Run fn() in a forked child with RLIMIT_CPU; return its JSON frame, or a timeout/crash record."""
    rfd, wfd = os.pipe()
    t0 = time.monotonic()
    pid = os.fork()
    if pid == 0:
        os.close(rfd)
        try:
            _detach_stdio()
            # Only this child's own result pipe stays open: target code cannot write into
            # the zygote's pipe (and forge other probes' results).
            os.closerange(3, wfd)
            os.closerange(wfd + 1, 1 << 16)
            try:
                _, hard = resource.getrlimit(resource.RLIMIT_CPU)
                soft = cpu_seconds if hard == resource.RLIM_INFINITY else min(cpu_seconds, hard)
                resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))  # never above the boundary's own limit
                out = fn()
            except BaseException as exc:  # noqa: BLE001
                out = {"status": "harness_error", "detail": exc_summary(exc), "tb": traceback.format_exc()[-800:]}
            _write_frame(wfd, out)
        finally:
            os._exit(0)
    os.close(wfd)
    obs, timed_out = _read_frame(rfd, t0 + wall)
    os.close(rfd)
    if timed_out:
        _kill(pid)
    os.waitpid(pid, 0)
    _kill(pid)  # anything the target left in the process group
    ms = round((time.monotonic() - t0) * 1000, 2)
    if timed_out:
        return {"status": "timeout", "elapsed_ms": ms}
    if not isinstance(obs, dict):
        return {"status": "crashed", "elapsed_ms": ms}
    obs.setdefault("elapsed_ms", ms)
    return obs


class _ProbeTimeout(BaseException):
    pass


def _alarm(signum, frame):
    raise _ProbeTimeout()


def zygote(req, wfd):
    """Imports target code once, then forks one child per chunk of probes."""
    sys.path[:0] = [os.path.normpath(os.path.join(req["src_root"], req.get("python_path_root", ".")))]
    if req.get("site_packages"):
        import site

        for d in site.getsitepackages():
            if d not in sys.path:
                sys.path.append(d)
    sys.dont_write_bytecode = True
    import importlib

    results = {}
    import_errors = {}
    for t in req["targets"].values():
        if t["module"] in import_errors:
            continue
        try:
            importlib.import_module(t["module"])
        except BaseException as exc:  # noqa: BLE001
            import_errors[t["module"]] = exc_summary(exc)
    wall = float(req.get("probe_wall_seconds", 2.0))
    cpu = max(1, int(req.get("probe_cpu_seconds", 2)))
    chunk_size = max(1, int(req.get("chunk", 10)))
    pending = []
    for p in req["probes"]:
        mod = req["targets"][p["tid"]]["module"]
        if mod in import_errors:
            results[p["id"]] = {"status": "import_error", "detail": import_errors[mod]}
        else:
            pending.append(p)
    chunks = []
    for p in pending:
        if chunks and chunks[-1][0]["tid"] == p["tid"] and len(chunks[-1]) < chunk_size:
            chunks[-1].append(p)
        else:
            chunks.append([p])

    def run_chunk(ch):
        out = {}
        signal.signal(signal.SIGALRM, _alarm)
        for p in ch:
            t0 = time.monotonic()
            try:
                signal.setitimer(signal.ITIMER_REAL, wall)
                o = run_probe(req, p)
            except _ProbeTimeout:
                o = {"status": "timeout"}
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            o["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
            if o.get("status") == "timeout" or o["elapsed_ms"] > wall * 1000:
                out[p["id"]] = {"status": "timeout", "elapsed_ms": o["elapsed_ms"]}
                return {"status": "partial", "chunk": out}  # the target may have left state behind: stop this chunk
            out[p["id"]] = o
        return {"status": "ok", "chunk": out}

    for ch in chunks:
        while ch:
            r = _forked(lambda ch=ch: run_chunk(ch), wall * len(ch) + 2, cpu * len(ch)) if len(ch) > 1 else None
            if r is not None and r.get("status") in ("ok", "partial") and isinstance(r.get("chunk"), dict) and r["chunk"]:
                results.update(r["chunk"])
                ch = [p for p in ch if p["id"] not in r["chunk"]]
                continue
            for p in ch:  # the chunk child died or hung in C code: one child per probe, hard-killed at the wall limit
                results[p["id"]] = _forked(lambda p=p: run_probe(req, p), wall, cpu)
            ch = []
    _write_frame(wfd, {"results": results, "import_errors": import_errors})


def _distributions():
    try:
        from importlib import metadata

        return sorted({"%s==%s" % (d.metadata["Name"], d.version) for d in metadata.distributions() if d.metadata["Name"]})
    except BaseException as exc:  # noqa: BLE001
        return ["<error: %s>" % type(exc).__name__]


def _identity(req):
    import platform

    out = {"implementation": sys.implementation.name, "version": platform.python_version(), "version_info": list(sys.version_info[:3]),
           "platform": sys.platform, "machine": platform.machine(), "hash_seed": os.environ.get("PYTHONHASHSEED"),
           "hash_randomization": sys.flags.hash_randomization}
    if req.get("site_packages"):
        import site

        sys.path.extend(d for d in site.getsitepackages() if d not in sys.path)
        out["distributions"] = _distributions()
    return out


def main():
    try:
        req = json.loads(sys.stdin.buffer.read().decode())
        mode = req.get("mode")
        if mode == "identity":
            resp = {"protocol": PROTOCOL, "mode": mode, "identity": _identity(req)}
        elif mode == "replay":
            rfd, wfd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(rfd)
                try:
                    _detach_stdio()
                    zygote(req, wfd)
                finally:
                    os._exit(0)
            os.close(wfd)
            body, timed_out = _read_frame(rfd, time.monotonic() + float(req.get("batch_wall_seconds", 600)))
            os.close(rfd)
            if timed_out:
                _kill(pid)
            os.waitpid(pid, 0)
            _kill(pid)
            if not isinstance(body, dict):
                body = {"results": {}, "batch_error": "zygote timed out" if timed_out else "zygote ended without a result"}
            resp = {"protocol": PROTOCOL, "mode": mode, "identity": _identity(req), **body}
        else:
            resp = {"protocol": PROTOCOL, "mode": "error", "error": "unknown mode %r" % (mode,)}
    except Exception as exc:  # noqa: BLE001
        resp = {"protocol": PROTOCOL, "mode": "error", "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    sys.stdout.write(json.dumps(resp, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
