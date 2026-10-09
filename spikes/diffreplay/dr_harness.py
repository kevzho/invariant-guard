"""Differential-replay harness (spike). Runs INSIDE the isolation boundary only.

It is copied next to the trusted probe harness by the spike runner and executed with the
same ``python -I -S -B`` flags in the same container profile. It speaks the
``ig-probe-1`` framing (one JSON request on stdin, one JSON response on stdout) because
``invariantguard.execution.runner._parse_response`` hard-codes that protocol name.

Modes:

* ``generate``: draw inputs with Hypothesis (derandomized, no database) from
  type annotations, stub annotations or value-shaped strategies, and return them as
  *encoded built-in values*, so the host can replay byte-identical inputs on both sides.
* ``replay``: for each probe, call the target (module function, static/class method,
  or instance method on a freshly constructed receiver) and encode return value /
  exception / argument mutation, materializing a bounded prefix of iterators.

Trust split (same as probe_harness.py): the top-level process never imports target
code. A "zygote" child imports the target modules once; each probe runs in a
grandchild forked from the zygote (post-import state, fresh per probe), with a wall
limit enforced by the zygote and an overall limit enforced by the parent.
"""

import json
import os
import re
import select
import signal
import sys
import time
import traceback

PROTOCOL = "ig-probe-1"
MAX_DEPTH = 24
MAX_NODES = 20000
MAX_STR = 8192
ADDR_RE = re.compile(r"0x[0-9a-fA-F]{6,}")
SITE = "/usr/local/lib/python%d.%d/site-packages" % sys.version_info[:2]

SAFE_BUILTINS = (
    "abs all any bool bytearray bytes callable chr complex dict divmod enumerate filter float frozenset hash int isinstance "
    "issubclass iter len list map max min next object ord pow range repr reversed round set slice sorted str sum tuple type zip "
    "None True False Ellipsis NotImplemented "
    "ArithmeticError AssertionError AttributeError Exception IndexError KeyError LookupError OverflowError RuntimeError "
    "StopIteration TypeError ValueError ZeroDivisionError"
).split()


# -- value encoding --------------------------------------------------------------------------
# Superset of invariantguard.contracts.values: the 'core' tags are identical; 'ext' tags are
# spike extensions (marked so the host can count how much evidence depends on them).
def encode(value, iter_prefix=100, materialize=True):
    count = [0]
    ext = [False]

    def qn(t):
        return t.__qualname__ if t.__module__ == "builtins" else "%s.%s" % (t.__module__, t.__qualname__)

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
            return {"t": "opaque", "type": type(x).__name__, "why": "too many nodes"}
        if depth > MAX_DEPTH:
            return {"t": "opaque", "type": type(x).__name__, "why": "nesting too deep"}
        t = type(x)
        if x is None:
            return {"t": "none"}
        if t is bool:
            return {"t": "bool", "v": x}
        if t is int:
            s = str(x) if x.bit_length() < 13000 else "<huge>"
            return {"t": "int", "v": s}
        if t is float:
            return {"t": "float", "v": x.hex()}
        if t is str:
            return {"t": "str", "v": x if len(x) <= MAX_STR else x[:MAX_STR] + "...<truncated %d>" % len(x)}
        if t in (list, tuple):
            if id(x) in active:
                return {"t": "opaque", "type": t.__name__, "why": "cyclic value"}
            active.add(id(x))
            try:
                return {"t": t.__name__, "v": [enc(v, depth + 1, active) for v in x]}
            finally:
                active.discard(id(x))
        if t is dict:
            if id(x) in active:
                return {"t": "opaque", "type": "dict", "why": "cyclic value"}
            active.add(id(x))
            try:
                if all(type(k) is str for k in x):
                    return {"t": "dict", "v": [[k, enc(v, depth + 1, active)] for k, v in x.items()]}
                ext[0] = True
                return {"t": "xdict", "v": [[enc(k, depth + 1, active), enc(v, depth + 1, active)] for k, v in x.items()]}
            finally:
                active.discard(id(x))
        ext[0] = True
        if t in (set, frozenset):
            vs = [enc(v, depth + 1, active) for v in x]
            vs.sort(key=lambda n: json.dumps(n, sort_keys=True))
            return {"t": t.__name__, "v": vs}
        if t in (bytes, bytearray):
            return {"t": t.__name__, "v": bytes(x).hex()}
        if t is complex:
            return {"t": "complex", "v": [x.real.hex(), x.imag.hex()]}
        if t is range:
            return {"t": "range", "v": [x.start, x.stop, x.step]}
        if t is slice:
            return {"t": "slice", "v": [enc(x.start, depth + 1, active), enc(x.stop, depth + 1, active), enc(x.step, depth + 1, active)]}
        mod = t.__module__
        if mod in ("datetime",):
            out = {"t": "dt", "type": t.__name__}
            try:
                out["v"] = x.isoformat() if hasattr(x, "isoformat") else str(x)
                tz = getattr(x, "tzinfo", None)
                if tz is not None:
                    out["tz"] = {"type": qn(type(tz)), "offset": str(x.utcoffset()) if t.__name__ == "datetime" else None,
                                 "name": x.tzname() if t.__name__ == "datetime" else None}
            except BaseException as exc:  # noqa: BLE001
                out["error"] = exc_summary(exc)
            return out
        if mod in ("decimal", "fractions"):
            return {"t": "num", "type": t.__name__, "v": str(x)}
        if isinstance(x, BaseException):
            return {"t": "exception", "type": qn(t), "args": enc(tuple(x.args), depth + 1, active)}
        if isinstance(x, type):
            return {"t": "class", "name": qn(x)}
        if t.__name__ in ("function", "builtin_function_or_method", "method", "method-wrapper", "wrapper_descriptor"):
            return {"t": "callable", "name": str(getattr(x, "__qualname__", t.__name__))}
        if id(x) in active:
            return {"t": "opaque", "type": qn(t), "why": "cyclic value"}
        active.add(id(x))
        try:
            # Iterators / generators: materialize a bounded prefix (consumes them).
            is_iter = hasattr(t, "__next__") and hasattr(t, "__iter__")
            if is_iter:
                if not materialize:
                    return {"t": "iter", "type": qn(t), "why": "not materialized"}
                v, err, trunc = items_of(x, depth, active)
                out = {"t": "iter", "type": qn(t), "v": v, "truncated": trunc}
                if err:
                    out["raised"] = err
                return out
            out = {"t": "object", "type": qn(t)}
            # Container subclasses: their built-in contents.
            if isinstance(x, dict):
                out["items"] = [[enc(k, depth + 1, active), enc(v, depth + 1, active)] for k, v in dict.items(x)]
            elif isinstance(x, (list, tuple)):
                out["items"] = [enc(v, depth + 1, active) for v in (list.__iter__(x) if isinstance(x, list) else tuple.__iter__(x))]
            elif isinstance(x, (set, frozenset)):
                vs = [enc(v, depth + 1, active) for v in x]
                vs.sort(key=lambda n: json.dumps(n, sort_keys=True))
                out["items"] = vs
            # Instance state without calling __getattr__ / __repr__ of the target.
            try:
                d = object.__getattribute__(x, "__dict__")
            except AttributeError:
                d = None
            state = []
            if isinstance(d, dict):
                for k in sorted(k for k in d if type(k) is str):
                    state.append([k, enc(d[k], depth + 1, active)])
            for klass in t.__mro__:
                for s in klass.__dict__.get("__slots__", ()) if isinstance(klass.__dict__.get("__slots__", ()), (tuple, list)) else ():
                    try:
                        state.append([s, enc(object.__getattribute__(x, s), depth + 1, active)])
                    except BaseException:  # noqa: BLE001
                        pass
            if state:
                out["state"] = state
            if "items" not in out and hasattr(t, "__iter__") and materialize:
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

    node = enc(value, 0, set())
    return node, ext[0]


def decode(n, depth=0):
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


def exc_summary(exc):
    t = type(exc)
    name = t.__qualname__ if t.__module__ == "builtins" else "%s.%s" % (t.__module__, t.__qualname__)
    try:
        msg = str(exc)[:400]
    except BaseException:  # noqa: BLE001
        msg = "<unprintable>"
    return {"type": name, "message": ADDR_RE.sub("0x?", msg)}


# -- namespaces for harvested expressions ---------------------------------------------------------
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
        except BaseException:  # noqa: BLE001 - a missing name makes the expression fail (counted)
            pass
    cache[key] = ns
    return ns


def materialize_arg(spec, namespaces, cache):
    if "v" in spec:
        return decode(spec["v"])
    ns = build_ns(namespaces[spec["ns"]], cache)
    return eval(compile(spec["x"], "<harvested>", "eval"), dict(ns))  # repository-authored test/doctest text only


def resolve(target):
    import importlib

    mod = importlib.import_module(target["module"])
    parts = target["qualname"].split(".")
    owner = mod
    for p in parts[:-1]:
        owner = getattr(owner, p)
    name = parts[-1]
    if owner is mod:
        return "function", getattr(mod, name), None
    import inspect

    raw = inspect.getattr_static(owner, name)
    if isinstance(raw, type):
        return "function", getattr(owner, name), None
    if isinstance(raw, (staticmethod, classmethod)):
        return "static", getattr(owner, name), owner
    if isinstance(raw, property):
        return "property", raw, owner
    return "method", raw, owner


# -- probe execution ------------------------------------------------------------------------------
MAX_OBS_BYTES = 64 * 1024


def _cap(node):
    """Large encodings are replaced by their content digest: still exactly comparable, bounded output."""
    import hashlib

    data = json.dumps(node, sort_keys=True, separators=(",", ":"))
    if len(data) <= MAX_OBS_BYTES:
        return node
    return {"t": "digest", "sha256": hashlib.sha256(data.encode()).hexdigest(), "bytes": len(data), "type": node.get("type") or node.get("t")}


def run_probe(req, probe, targets, namespaces):
    cache = {}
    import random

    random.seed(0)
    t = targets[probe["tid"]]
    try:
        kind, fn, owner = resolve(t)
    except BaseException as exc:  # noqa: BLE001
        return {"status": "resolve_error", "detail": exc_summary(exc)}
    try:
        args = [materialize_arg(a, namespaces, cache) for a in probe.get("args", [])]
        kwargs = {k: materialize_arg(a, namespaces, cache) for k, a in probe.get("kwargs", {}).items()}
        recv = None
        if kind in ("method", "property") and probe.get("receiver") is not None:
            r = probe["receiver"]
            rargs = [materialize_arg(a, namespaces, cache) for a in r.get("args", [])]
            rkw = {k: materialize_arg(a, namespaces, cache) for k, a in r.get("kwargs", {}).items()}
            recv = owner(*rargs, **rkw)
    except BaseException as exc:  # noqa: BLE001 - the input could not be built (not a target outcome)
        return {"status": "input_error", "detail": exc_summary(exc)}
    if kind in ("method", "property") and recv is None:
        return {"status": "input_error", "detail": {"type": "NoReceiver", "message": "instance method without a receiver"}}
    mutable = {}
    for i, a in enumerate(args):
        if type(a) in (list, dict, set, bytearray):
            mutable["arg%d" % i] = a
    for k, a in kwargs.items():
        if type(a) in (list, dict, set, bytearray):
            mutable[k] = a
    before = {k: encode(v)[0] for k, v in mutable.items()}
    recv_before = encode(recv, materialize=False)[0] if recv is not None else None
    try:
        if kind == "property":
            value = fn.__get__(recv, owner)
        elif kind == "method":
            value = getattr(recv, t["qualname"].split(".")[-1])(*args, **kwargs)
        else:
            value = fn(*args, **kwargs)
        node, ext = encode(value, int(req.get("iter_prefix", 100)))
        res = {"kind": "returned", "value": node, "ext": ext}
    except BaseException as exc:  # noqa: BLE001 - the target raised
        res = {"kind": "raised", "exception": exc_summary(exc)}
    after = {k: encode(v)[0] for k, v in mutable.items()}
    out = {"status": "ok", "result": res}
    if res["kind"] == "returned":
        res["value"] = _cap(res["value"])
    mut = {k: _cap(after[k]) for k in after if after[k] != before[k]}
    if mut:
        out["mutated"] = mut
    if recv is not None:
        ra = encode(recv, materialize=False)[0]
        if ra != recv_before:
            out["receiver_after"] = _cap(ra)
    return out


def _forked(fn, wall):
    """Run fn() in a forked grandchild; return its JSON result or a timeout/crash record."""
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(rfd)
        try:
            os.setsid()
            devnull = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(devnull, fd)
            try:
                out = fn()
            except BaseException as exc:  # noqa: BLE001
                out = {"status": "harness_error", "detail": exc_summary(exc), "tb": traceback.format_exc()[-800:]}
            try:
                data = json.dumps(out, separators=(",", ":"), allow_nan=False).encode()
            except (TypeError, ValueError) as exc:
                data = json.dumps({"status": "harness_error", "detail": {"type": "EncodeError", "message": str(exc)[:200]}}).encode()
            data = len(data).to_bytes(8, "big") + data
            view = memoryview(data)
            while view:
                view = view[os.write(wfd, view):]
        finally:
            os._exit(0)
    os.close(wfd)
    t0 = time.monotonic()
    chunks, size, expected, timed_out = [], 0, None, False
    while expected is None or size < expected:
        rem = t0 + wall - time.monotonic()
        if rem <= 0:
            timed_out = True
            break
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
    os.close(rfd)
    if timed_out:
        for k in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                k()
            except OSError:
                pass
    os.waitpid(pid, 0)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
    ms = round((time.monotonic() - t0) * 1000, 2)
    if timed_out:
        return {"status": "timeout", "elapsed_ms": ms}
    raw = b"".join(chunks)
    try:
        obs = json.loads(raw[8:8 + int.from_bytes(raw[:8], "big")].decode())
    except (ValueError, RecursionError):
        return {"status": "crashed", "elapsed_ms": ms}
    obs["elapsed_ms"] = ms
    return obs


def zygote(req, wfd):
    """Imports target code once, then forks one grandchild per probe."""
    sys.path[:0] = [os.path.normpath(os.path.join(req["src_root"], req.get("python_path_root", ".")))]
    sys.path.append(SITE)  # pinned image dependencies (hypothesis, six); the runner passes -S
    sys.dont_write_bytecode = True
    import importlib

    results = {}
    import_errors = {}
    for tid, t in req["targets"].items():
        try:
            importlib.import_module(t["module"])
        except BaseException as exc:  # noqa: BLE001
            import_errors[t["module"]] = exc_summary(exc)
    wall = float(req.get("probe_wall_seconds", 2.0))
    if req["mode"] == "replay":
        chunk_size = int(req.get("chunk", 10))
        pending = []
        for p in req["probes"]:
            t = req["targets"][p["tid"]]
            if t["module"] in import_errors:
                results[p["id"]] = {"status": "import_error", "detail": import_errors[t["module"]]}
            else:
                pending.append(p)
        # Chunks of probes of the same target share one forked child (post-import state is fresh per chunk);
        # a chunk that times out or crashes is re-run one probe per child, so a hang is attributed exactly.
        chunks = []
        for p in pending:
            if chunks and chunks[-1][0]["tid"] == p["tid"] and len(chunks[-1]) < chunk_size:
                chunks[-1].append(p)
            else:
                chunks.append([p])

        class _ProbeTimeout(BaseException):
            pass

        def _alarm(signum, frame):
            raise _ProbeTimeout()

        def run_chunk(ch):
            out = {}
            signal.signal(signal.SIGALRM, _alarm)
            for p in ch:
                t0 = time.monotonic()
                try:
                    signal.setitimer(signal.ITIMER_REAL, wall)
                    o = run_probe(req, p, req["targets"], req.get("namespaces", {}))
                except _ProbeTimeout:
                    o = {"status": "timeout"}
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                o["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 2)
                if o.get("status") == "timeout" or o.get("elapsed_ms", 0) > wall * 1000:
                    o = {"status": "timeout", "elapsed_ms": o["elapsed_ms"]}
                    out[p["id"]] = o
                    # the target may have left state behind (half-run generator, locks): stop this chunk here
                    return {"status": "partial", "chunk": out}
                out[p["id"]] = o
            return {"status": "ok", "chunk": out}

        for ch in chunks:
            while ch:
                r = _forked(lambda ch=ch: run_chunk(ch), wall * len(ch) + 2) if len(ch) > 1 else None
                if r is not None and r.get("status") in ("ok", "partial") and isinstance(r.get("chunk"), dict) and r["chunk"]:
                    results.update(r["chunk"])
                    ch = [p for p in ch if p["id"] not in r["chunk"]]
                    continue
                for p in ch:  # chunk child died or hung in C code: one child per probe, hard-killed at the wall limit
                    results[p["id"]] = _forked(lambda p=p: run_probe(req, p, req["targets"], req.get("namespaces", {})), wall)
                ch = []
    else:
        try:
            import hypothesis  # noqa: F401 - trusted pinned library, imported once before forking jobs
        except ImportError:
            pass
        for job in req["jobs"]:
            t = req["targets"][job["tid"]]
            if t["module"] in import_errors:
                results[job["id"]] = {"status": "import_error", "detail": import_errors[t["module"]]}
                continue
            results[job["id"]] = _forked(lambda job=job: generate_job(job, t), float(req.get("gen_wall_seconds", 20.0)))
    data = json.dumps({"results": results, "import_errors": import_errors}, separators=(",", ":")).encode()
    data = len(data).to_bytes(8, "big") + data
    view = memoryview(data)
    while view:
        view = view[os.write(wfd, view):]


# -- generation (Hypothesis, inside the boundary) --------------------------------------------------
def _shape_strategy(st, node, depth=0):
    """A strategy shaped like an observed built-in example value (typed spec, no eval)."""
    t = node.get("t")
    if t == "none":
        return st.none()
    if t == "bool":
        return st.booleans()
    if t == "int":
        v = int(node["v"])
        lo, hi = min(-3, v - 3, -abs(v)), max(10, v + 3, abs(v))
        return st.integers(lo, hi)
    if t == "float":
        return st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6) | st.sampled_from([0.0, -0.0, 1.0, 0.5])
    if t == "str":
        alphabet = sorted(set(node["v"])) or ["a"]
        return st.text(alphabet=st.sampled_from(alphabet + [" ", "-"]), max_size=max(8, 2 * len(node["v"])))
    if t in ("list", "tuple", "set", "frozenset") and depth < 3:
        elems = node["v"]
        if elems:
            inner = st.one_of(*[_shape_strategy(st, e, depth + 1) for e in elems[:3]])
        else:
            inner = st.integers(-3, 10)
        lists = st.lists(inner, max_size=max(6, len(elems) + 2))
        return {"list": lists, "tuple": lists.map(tuple), "set": lists.map(set), "frozenset": lists.map(frozenset)}[t]
    if t == "dict" and depth < 3:
        vals = [v for _, v in node["v"]]
        keys = [k for k, _ in node["v"]] or ["a"]
        inner = st.one_of(*[_shape_strategy(st, v, depth + 1) for v in vals[:3]]) if vals else st.integers()
        return st.dictionaries(st.sampled_from(keys + ["z"]), inner, max_size=len(keys) + 1)
    return None


def generate_job(job, target):
    import typing

    from hypothesis import HealthCheck, Phase, given, settings
    from hypothesis import strategies as st

    params = job["params"]  # [[name, kind('pos'|'kw'), spec], ...]
    strats = {}
    for name, _, spec in params:
        k = spec["k"]
        if k == "annotation":
            kind, fn, owner = resolve(target)
            f = fn.__func__ if isinstance(fn, (staticmethod, classmethod)) else fn
            hints = typing.get_type_hints(f)
            strats[name] = st.from_type(hints[name])
        elif k == "stub":
            ns = {}
            for local, modname, attr in spec.get("imports", []):
                try:
                    m = __import__(modname, fromlist=["_"])
                    ns[local] = getattr(m, attr) if attr else m
                except (ImportError, AttributeError):  # stub-only names (type_check_only, ...)
                    ns[local] = (lambda f: f) if attr == "type_check_only" else typing.Any
            for tv in spec.get("typevars", []):
                ns[tv] = typing.TypeVar(tv)
            ann = eval(spec["ann"], ns)  # stub annotation text from the repository
            strats[name] = st.from_type(ann)
        elif k == "shape":
            s = _shape_strategy(st, spec["value"])
            if s is None:
                return {"status": "ok", "examples": [], "unsupported": "shape %s" % spec["value"].get("t")}
            strats[name] = s
        elif k == "fixed":
            strats[name] = st.just(spec["arg"])  # an arg spec passed through unchanged
        else:
            return {"status": "ok", "examples": [], "unsupported": k}
    examples = []
    dropped = [0]

    @settings(max_examples=int(job.get("n", 30)), derandomize=True, database=None, deadline=None, phases=[Phase.generate],
              suppress_health_check=list(HealthCheck))
    @given(st.fixed_dictionaries(strats))
    def collect(d):
        out = {}
        for name, _, spec in params:
            if spec["k"] == "fixed":
                out[name] = d[name]
                continue
            node, ext = encode(d[name], materialize=False)
            try:
                decode(node)
            except (ValueError, TypeError, KeyError):
                dropped[0] += 1
                return
            out[name] = {"v": node}
        examples.append(out)

    collect()
    uniq, seen = [], set()
    for e in examples:
        key = json.dumps(e, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return {"status": "ok", "examples": uniq, "dropped_unencodable": dropped[0]}


def main():
    try:
        req = json.loads(sys.stdin.buffer.read().decode())
        mode = req.get("mode")
        if mode not in ("replay", "generate"):
            raise ValueError("unknown mode %r" % (mode,))
        rfd, wfd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(rfd)
            try:
                os.setsid()
                devnull = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(devnull, fd)
                zygote(req, wfd)
            finally:
                os._exit(0)
        os.close(wfd)
        chunks = []
        deadline = time.monotonic() + float(req.get("batch_wall_seconds", 600))
        timed_out = False
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                timed_out = True
                break
            r, _, _ = select.select([rfd], [], [], rem)
            if not r:
                continue
            c = os.read(rfd, 1 << 16)
            if not c:
                break
            chunks.append(c)
        os.close(rfd)
        if timed_out:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        os.waitpid(pid, 0)
        raw = b"".join(chunks)
        if timed_out or len(raw) < 8:
            body = {"results": {}, "batch_error": "zygote timed out" if timed_out else "zygote ended without a result"}
        else:
            body = json.loads(raw[8:8 + int.from_bytes(raw[:8], "big")].decode())
        resp = {"protocol": PROTOCOL, "mode": mode, "identity": {"version": sys.version.split()[0], "hash_seed": os.environ.get("PYTHONHASHSEED")}, **body}
    except Exception as exc:  # noqa: BLE001
        resp = {"protocol": PROTOCOL, "mode": "error", "error": "%s: %s" % (type(exc).__name__, str(exc)[:300])}
    sys.stdout.write(json.dumps(resp, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
