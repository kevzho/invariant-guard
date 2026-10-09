"""Differential replay of changed functions between two snapshots (spike orchestrator)."""

from __future__ import annotations

import ast
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import drrunner
from changes import REPOS, Change, changed_functions, inventory, module_of, pkg_info, repo, snapshot
from harvest import Input, default_variants, harvest, literal_value

CAP_HARVEST = 60
CAP_DEFAULTS = 30
CAP_GENERATED = 60
GEN_N = 30
SHAPE_N = 15
STUB_IMPORT_MODULES = {"typing", "collections.abc", "typing_extensions", "collections", "numbers", "datetime", "re"}


# -- rendering ------------------------------------------------------------------------------------
def render(n: Any, depth: int = 0) -> str:
    if not isinstance(n, dict):
        return repr(n)
    t = n.get("t")
    v = n.get("v")
    if t == "none":
        return "None"
    if t in ("bool",):
        return repr(v)
    if t == "int":
        return v
    if t == "float":
        return repr(float.fromhex(v))
    if t == "str":
        return repr(v)
    if t in ("list", "tuple", "set", "frozenset"):
        inner = ", ".join(render(x, depth + 1) for x in v[:12]) + (", ..." if len(v) > 12 else "")
        return {"list": f"[{inner}]", "tuple": f"({inner}{',' if len(v) == 1 else ''})", "set": f"{{{inner}}}", "frozenset": f"frozenset({{{inner}}})"}[t]
    if t in ("dict", "xdict"):
        items = ", ".join(f"{repr(k) if t == 'dict' else render(k)}: {render(x)}" for k, x in v[:10])
        return "{" + items + "}"
    if t == "iter":
        s = "<iter " + n.get("type", "") + ": [" + ", ".join(render(x) for x in v[:12]) + (", ..." if n.get("truncated") or len(v) > 12 else "") + "]"
        if n.get("raised"):
            s += f" then raised {n['raised']['type']}: {n['raised']['message'][:80]}"
        return s + ">"
    if t == "object":
        bits = []
        if "items" in n:
            bits.append("items=" + render({"t": "list", "v": n["items"] if not n["items"] or not isinstance(n["items"][0], list) else [x[1] for x in n["items"]]}))
        if "state" in n:
            bits.append(", ".join(f"{k}={render(x)}" for k, x in n["state"][:6]))
        if "iter" in n:
            bits.append("iter=[" + ", ".join(render(x) for x in n["iter"][:8]) + "]")
        return f"<{n['type']} {'; '.join(bits)}>"
    if t == "dt":
        return f"{n['type']}({n.get('v')}{', tz=' + str(n['tz']) if n.get('tz') else ''})"
    if t in ("bytes", "bytearray"):
        return f"{t}.fromhex({v!r})"
    if t == "exception":
        return f"{n['type']}{render(n['args'])}"
    if t in ("callable", "class"):
        return f"<{t} {n.get('name')}>"
    if t == "range":
        return f"range{tuple(v)}"
    if t == "opaque":
        return f"<opaque {n.get('type')}: {n.get('why')}>"
    return json.dumps(n)[:120]


def render_obs(o: dict[str, Any] | None) -> str:
    if o is None:
        return "<no observation>"
    if o.get("status") != "ok":
        d = o.get("detail") or {}
        return f"<{o.get('status')}{': ' + d.get('type', '') + ': ' + str(d.get('message', ''))[:100] if d else ''}>"
    r = o["result"]
    s = ("returned " + render(r["value"])) if r["kind"] == "returned" else f"raised {r['exception']['type']}: {r['exception']['message'][:160]}"
    if o.get("mutated"):
        s += " ; mutated " + ", ".join(f"{k}->{render(v)}" for k, v in o["mutated"].items())
    return s


def render_input(i: Input, qualname: str = "") -> str:
    def a(spec):
        return spec["x"] if "x" in spec else render(spec["v"])

    parts = [a(x) for x in i.args] + [f"{k}={a(v)}" for k, v in i.kwargs.items()]
    s = "(" + ", ".join(parts) + ")"
    if i.receiver:
        s = qualname.split(".")[0] + "(" + ", ".join([a(x) for x in i.receiver["args"]] + [f"{k}={a(v)}" for k, v in i.receiver["kwargs"].items()]) + ")." + qualname.split(".")[-1] + s
    elif qualname:
        s = qualname + s
    return s


# -- comparison -------------------------------------------------------------------------------------
def public_view(n: Any) -> Any:
    """Drop private instance state (``_name`` attributes) and private type names: what a caller can observe."""
    if isinstance(n, list):
        return [public_view(x) for x in n]
    if not isinstance(n, dict):
        return n
    out = {}
    is_iter = n.get("t") == "iter"
    for k, v in n.items():
        if is_iter and k == "type":
            continue  # map / generator / list_iterator: the concrete iterator type is an implementation detail
        if k == "state" and isinstance(v, list):
            out[k] = [[a, public_view(b)] for a, b in v if not str(a).startswith("_")]
        elif k == "ext":
            continue
        else:
            out[k] = public_view(v)
    return out


def _canon(o: dict[str, Any], public: bool = True) -> str:
    keep = {k: o.get(k) for k in ("status", "result", "mutated", "receiver_after", "detail")}
    if keep.get("result") and "ext" in keep["result"]:
        keep["result"] = {k: v for k, v in keep["result"].items() if k != "ext"}
    if public:
        keep = public_view(keep)
    return json.dumps(keep, sort_keys=True)


NOT_EXEC = ("input_error", "import_error", "resolve_error", "harness_error", "crashed", None)


def classify(b1: dict | None, b2: dict | None, p: dict | None) -> str:
    sb, sp = (b1 or {}).get("status"), (p or {}).get("status")
    if sb in NOT_EXEC or sp in NOT_EXEC:
        if sb == "input_error" and sp == "input_error":
            return "not_executable"
        if sb == "resolve_error" or sb == "import_error":
            return "no_baseline"
        return "unknown"
    if sb == "timeout" and sp == "timeout":
        return "timeout_both"
    if sp == "timeout" or sb == "timeout":
        return "timeout"
    if b2 is not None and b2.get("status") == "timeout":
        return "unconfirmed"  # the repeat base run hit the wall limit (load), not evidence of nondeterminism
    if b2 is not None and (b2.get("status") != sb or _canon(b1) != _canon(b2)):
        return "nondeterministic"
    if _canon(b1) == _canon(p):
        return "same" if _canon(b1, False) == _canon(p, False) else "internal_state_only"
    rb, rp = public_view(b1["result"]), public_view(p["result"])
    if rb["kind"] == "returned" and rp["kind"] == "returned":
        if json.dumps(rb["value"], sort_keys=True) != json.dumps(rp["value"], sort_keys=True):
            return "different_return"
        return "different_mutation"
    if rb["kind"] == "returned" and rp["kind"] == "raised":
        return "new_exception"
    if rb["kind"] == "raised" and rp["kind"] == "returned":
        return "removed_exception"
    eb, ep = rb["exception"], rp["exception"]
    if eb["type"] != ep["type"]:
        return "different_exception"
    if eb["message"] != ep["message"]:
        return "exception_message"
    return "different_mutation"


DIFF_CLASSES = (  # observable differences (internal_state_only and nondeterministic are reported separately)
   "different_return", "different_mutation", "new_exception", "removed_exception", "different_exception", "exception_message", "timeout")


# -- stubs ---------------------------------------------------------------------------------------
def stub_spec(name: str, commit: str, stub_path: str, fn_name: str) -> dict[str, Any] | None:
    r = repo(name)
    entries = {e.path: e for e in r.ls_tree(commit)}
    e = entries.get(stub_path)
    if e is None:
        return None
    src = r.read_blobs([e.oid])[e.oid]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    imports, typevars = [], []
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module in STUB_IMPORT_MODULES:
            for a in st.names:
                imports.append([a.asname or a.name, st.module, a.name])
        elif isinstance(st, ast.Import):
            for a in st.names:
                if a.name in STUB_IMPORT_MODULES:
                    imports.append([a.asname or a.name, a.name, None])
        elif isinstance(st, ast.Assign) and isinstance(st.value, ast.Call) and ast.unparse(st.value.func) in ("TypeVar", "typing.TypeVar"):
            typevars.append(st.targets[0].id)
    for st in tree.body:
        if isinstance(st, ast.FunctionDef) and st.name == fn_name:
            a = st.args
            req = a.posonlyargs + a.args
            req = req[: len(req) - len(a.defaults)]
            if not req or any(p.annotation is None for p in req):
                return None
            return {"params": [[p.arg, ast.unparse(p.annotation)] for p in req], "imports": imports, "typevars": typevars}
    return None


# -- per-commit pipeline ----------------------------------------------------------------------------------
def _test_paths(files: dict[str, bytes]) -> list[str]:
    return sorted(p for p in files if p.endswith(".py") and ("tests/" in p or "/test/" in p or p.startswith("test")) and "/_common" not in p)


def build_inputs(name: str, commit: str, ch: Change, pf: dict[str, bytes], bf: dict[str, bytes], pkg: str, pyroot: str, modules: set[str],
                 fn_node: ast.AST) -> tuple[list[Input], list[dict[str, Any]], dict[str, Any]]:
    module = module_of(ch.path, pyroot)
    h = harvest(module, ch.path, ch.qualname, "function" if ch.kind == "constructor" else ch.kind, pkg.split("/")[-1], modules, pf, bf, _test_paths(pf), _test_paths(bf))
    harvested = h.inputs[:CAP_HARVEST]
    defaults = default_variants(fn_node, harvested, CAP_DEFAULTS)
    jobs: list[dict[str, Any]] = []
    a = fn_node.args
    params = a.posonlyargs + a.args
    is_method = "." in ch.qualname
    if (is_method and ch.kind not in ("staticmethod",)) or ch.kind == "constructor":
        params = params[1:]  # self / cls
    required = params[: len(params) - len(a.defaults)] if a.defaults else params
    recv = h.receivers[0] if h.receivers else None
    needs_recv = is_method and ch.kind in ("method", "property")
    if required and all(p.annotation is not None for p in required) and (not needs_recv or recv):
        jobs.append({"source": "annotation", "params": [[p.arg, "pos", {"k": "annotation"}] for p in required], "n": GEN_N, "receiver": recv})
    if not is_method and ch.kind != "constructor":
        ss = stub_spec(name, commit, ch.path + "i", ch.qualname)
        if ss:
            jobs.append({"source": "stub", "params": [[pn, "pos", {"k": "stub", "ann": ann, "imports": ss["imports"], "typevars": ss["typevars"]}]
                                                       for pn, ann in ss["params"]], "n": GEN_N, "receiver": None})
    shaped = 0
    for inp in harvested:
        if shaped >= 4 or not inp.origin.startswith(("test", "doctest")):
            continue
        lits = [x for x in list(inp.args) + list(inp.kwargs.values()) if "v" in x]
        if not lits:
            continue
        ps = [[f"a{i}", "pos", {"k": "shape", "value": x["v"]} if "v" in x else {"k": "fixed", "arg": x}] for i, x in enumerate(inp.args)]
        ps += [[f"k:{k}", "kw", {"k": "shape", "value": x["v"]} if "v" in x else {"k": "fixed", "arg": x}] for k, x in inp.kwargs.items()]
        jobs.append({"source": "shape", "params": ps, "n": SHAPE_N, "receiver": inp.receiver})
        shaped += 1
    meta = {"harvest_stats": h.stats, "harvested_total": len(h.inputs), "receivers": len(h.receivers), "namespaces": h.namespaces}
    return harvested + defaults, jobs, meta


def generated_inputs(job: dict[str, Any], res: dict[str, Any] | None) -> list[Input]:
    out = []
    if not res or res.get("status") != "ok":
        return out
    for ex in res.get("examples", []):
        args, kwargs = [], {}
        for pname, how, _ in job["params"]:
            if how == "kw":
                kwargs[pname[2:]] = ex[pname]
            else:
                args.append(ex[pname])
        out.append(Input(job["source"], args, kwargs, job.get("receiver"), ""))
    return out


MIN_DIRECT = 3
PROBE_WALL_S = 5.0
MAX_CALLERS = 4


def _sub_targets(ch: Change, inv: dict, src: bytes = b"") -> list[tuple[Change, str]]:
    """The changed symbol itself (or its class, for a constructor) plus same-module callers (impact)."""
    from dataclasses import replace

    from changes import callers

    parts = ch.qualname.split(".")
    out: list[tuple[Change, str]] = []
    if parts[-1] in ("__init__", "__new__") and len(parts) == 2:
        out.append((replace(ch, qualname=parts[0], kind="constructor"), "constructor"))
    elif parts[-1] not in ("__init__", "__new__"):
        out.append((ch, "direct"))
    n = 0
    from harvest import module_instances

    try:
        instances = {k: v[0] for k, v in module_instances(ast.parse(src)).items()} if src else {}
    except SyntaxError:
        instances = {}
    for q, d in callers(inv, ch.qualname, instances=instances):
        fd = inv[q]
        if fd.kind == "async" or "#" in q:
            continue
        qp = q.split(".")
        if qp[-1] in ("__init__", "__new__"):
            if len(qp) != 2:
                continue
            sub = replace(ch, qualname=qp[0], kind="constructor", params_base=None, params_patch=fd.params)
        else:
            sub = replace(ch, qualname=q, kind=fd.kind, params_base=None, params_patch=fd.params)
        if any(o.qualname == sub.qualname for o, _ in out):
            continue
        out.append((sub, f"caller_d{d}"))
        n += 1
        if n >= MAX_CALLERS:
            break
    return out


def replay_commit(name: str, commit: str, parent: str, *, only: set[str] | None = None, patch_override: dict[str, bytes] | None = None,
                  base_commit_as_patch_base: bool = False, include_added: bool = False) -> dict[str, Any]:
    """Replay every changed function of ``parent -> commit``.

    For seeded mutants: base = ``commit``, patch = ``commit`` with ``patch_override`` files.
    """
    t0 = time.perf_counter()
    if patch_override is not None:
        bsnap = snapshot(name, commit)
        bf = {f.path: f.raw for f in bsnap.files}
        pf = dict(bf)
        pf.update(patch_override)
        from invariantguard.snapshots.capture import CapturedFile, Snapshot, finalize
        from invariantguard.models.spans import sha256_bytes

        psnap = finalize(Snapshot(bsnap.root, [CapturedFile(p, raw, sha256_bytes(raw)) for p, raw in sorted(pf.items())], [], None, bsnap.semantics_profile, bsnap.scope))
    else:
        bsnap, psnap = snapshot(name, parent), snapshot(name, commit)
        bf = {f.path: f.raw for f in bsnap.files}
        pf = {f.path: f.raw for f in psnap.files}
    pkg, pyroot = pkg_info(name, pf)
    modules = {module_of(p, pyroot) for p in pf if p.startswith(pkg + "/") and p.endswith(".py")}
    changes, fmeta = changed_functions(bsnap, psnap, pkg, REPOS[name]["exclude"])
    rec: dict[str, Any] = {"repo": name, "commit": commit, "parent": parent, "pkg": pkg, "pyroot": pyroot, "functions": [], "skipped": []}
    targets: dict[str, dict[str, Any]] = {}
    fn_inputs: dict[str, list[Input]] = {}
    fn_meta: dict[str, dict[str, Any]] = {}
    all_jobs: list[dict[str, Any]] = []
    inv_cache: dict[str, dict] = {}
    for ch in changes:
        sid = f"{ch.path}::{ch.qualname}"
        if only is not None and sid not in only:
            continue
        if ch.status == "deleted":
            rec["skipped"].append({"symbol": sid, "why": "deleted in patch"})
            continue
        if ch.status == "added" and not include_added:
            rec["skipped"].append({"symbol": sid, "why": "added in patch (no baseline behavior to compare)"})
            continue
        if ch.kind == "async":
            rec["skipped"].append({"symbol": sid, "why": "async function (not supported by the spike)"})
            continue
        if "#" in ch.qualname or ch.qualname.split(".")[-1] == "__del__":
            rec["skipped"].append({"symbol": sid, "why": "redefinition (property setter / duplicate def) or finalizer"})
            continue
        inv = inv_cache.setdefault(ch.path, inventory(pf[ch.path]))
        subs = _sub_targets(ch, inv, pf[ch.path])
        n_direct = 0
        for k, (sub, relation) in enumerate(subs):
            if relation.startswith("caller") and n_direct >= MIN_DIRECT:
                break
            fdef = (inv.get(sub.qualname + ".__init__") or inv.get(sub.qualname + ".__new__")) if sub.kind == "constructor" else inv.get(sub.qualname)
            if fdef is None:
                continue
            node = fdef.node
            ins, jobs, meta = build_inputs(name, commit, sub, pf, bf, pkg, pyroot, modules, node)
            n_harv = sum(1 for i in ins if i.origin.startswith(("test", "doctest")))
            if relation in ("direct", "constructor"):
                n_direct = n_harv
            elif n_harv == 0:
                continue
            tid = f"t{len(targets)}"
            targets[tid] = {"module": module_of(ch.path, pyroot), "qualname": sub.qualname}
            fn_inputs[tid] = ins
            fn_meta[tid] = {"symbol": sid, "target": f"{ch.path}::{sub.qualname}", "relation": relation, "change": ch.__dict__,
                            "signature_changed": ch.params_base != ch.params_patch and ch.params_base is not None, **meta}
            for j in jobs:
                j["tid"] = tid
                j["id"] = f"g{len(all_jobs)}"
                all_jobs.append(j)
    rec["t_prepare_ms"] = round((time.perf_counter() - t0) * 1000)
    if not targets:
        rec["t_total_ms"] = rec["t_prepare_ms"]
        return rec
    ws_b = drrunner.workspace(bf, "drb")
    ws_p = drrunner.workspace(pf, "drp")
    try:
        # 1) generation inside the boundary (against the patch snapshot)
        t1 = time.perf_counter()
        gen_results: dict[str, Any] = {}
        gen_error = None
        if all_jobs:
            run = drrunner.run(ws_p, {"mode": "generate", "python_path_root": pyroot, "targets": targets, "jobs": all_jobs, "gen_wall_seconds": 30,
                                      "batch_wall_seconds": 800})
            if run.response is None:
                gen_error = run.error
            else:
                gen_results = run.response.get("results", {})
        rec["t_generate_ms"] = round((time.perf_counter() - t1) * 1000)
        rec["generation_error"] = gen_error
        gen_by_tid: dict[str, list[Input]] = {}
        gen_status: dict[str, list[dict[str, Any]]] = {}
        for j in all_jobs:
            r = gen_results.get(j["id"])
            gen_status.setdefault(j["tid"], []).append({"source": j["source"], "status": (r or {}).get("status"), "n": len((r or {}).get("examples", [])),
                                                         "dropped_unencodable": (r or {}).get("dropped_unencodable"), "detail": (r or {}).get("detail"),
                                                         "unsupported": (r or {}).get("unsupported")})
            gen_by_tid.setdefault(j["tid"], []).extend(generated_inputs(j, r))
        # 2) assemble identical probes for both sides
        probes, pmap = [], {}
        for tid, ins in fn_inputs.items():
            seen = set()
            allin = ins + gen_by_tid.get(tid, [])[:CAP_GENERATED]
            for i in allin:
                k = i.key()
                if k in seen:
                    continue
                seen.add(k)
                pid = f"p{len(probes):05d}"
                probes.append({"id": pid, "tid": tid, "args": i.args, "kwargs": i.kwargs, "receiver": i.receiver})
                pmap[pid] = (tid, i)
        namespaces = {}
        for tid in fn_inputs:
            namespaces.update(fn_meta[tid]["namespaces"])  # ids are unique per target harvest; merge with prefix below
        # namespace ids collide across targets: re-key per target
        probes2 = []
        nsall: dict[str, Any] = {}
        for p in probes:
            tid = p["tid"]
            nsmap = {nid: f"{tid}.{nid}" for nid in fn_meta[tid]["namespaces"]}
            for nid, spec in fn_meta[tid]["namespaces"].items():
                nsall[nsmap[nid]] = spec

            def rk(spec, nsmap=nsmap):
                return {**spec, "ns": nsmap[spec["ns"]]} if "x" in spec else spec

            q = {**p, "args": [rk(a) for a in p["args"]], "kwargs": {k: rk(v) for k, v in p["kwargs"].items()}}
            if p["receiver"]:
                q["receiver"] = {"args": [rk(a) for a in p["receiver"]["args"]], "kwargs": {k: rk(v) for k, v in p["receiver"]["kwargs"].items()}}
            probes2.append(q)
        req = {"mode": "replay", "python_path_root": pyroot, "targets": targets, "probes": probes2, "namespaces": nsall, "probe_wall_seconds": PROBE_WALL_S,
               "iter_prefix": 100, "batch_wall_seconds": 850}
        # 3) base twice (hash seeds 0 and 1) and patch once, concurrently, same inputs
        t2 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=3) as pool:
            fb1 = pool.submit(drrunner.run, ws_b, req, "0")
            fb2 = pool.submit(drrunner.run, ws_b, req, "1")
            fp = pool.submit(drrunner.run, ws_p, req, "0")
            rb1, rb2, rp = fb1.result(), fb2.result(), fp.result()
        rec["t_replay_ms"] = round((time.perf_counter() - t2) * 1000)
        rec["runner_errors"] = {k: r.error for k, r in (("base", rb1), ("base_repeat", rb2), ("patch", rp)) if r.response is None}
        ob1 = (rb1.response or {}).get("results", {})
        ob2 = (rb2.response or {}).get("results", {})
        op = (rp.response or {}).get("results", {})
        rec["import_errors"] = {"base": (rb1.response or {}).get("import_errors"), "patch": (rp.response or {}).get("import_errors")}
        # 3b) confirmation: every differing probe is re-run in fresh containers (base once, patch twice, hash seeds 0/1)
        pre = {p["id"]: classify(ob1.get(p["id"]), ob2.get(p["id"]), op.get(p["id"])) for p in probes2}
        to_confirm = [p for p in probes2 if pre[p["id"]] in DIFF_CLASSES]
        confirmed: dict[str, bool] = {}
        t3 = time.perf_counter()
        if to_confirm:
            creq = {**req, "probes": to_confirm}
            with ThreadPoolExecutor(max_workers=3) as pool:
                fcb = pool.submit(drrunner.run, ws_b, creq, "0")
                fcp1 = pool.submit(drrunner.run, ws_p, creq, "0")
                fcp2 = pool.submit(drrunner.run, ws_p, creq, "1")
                cb, cp1, cp2 = ((f.result().response or {}).get("results", {}) for f in (fcb, fcp1, fcp2))
            for p in to_confirm:
                pid = p["id"]
                c = pre[pid]
                confirmed[pid] = classify(cb.get(pid), ob1.get(pid), cp1.get(pid)) == c and classify(cb.get(pid), ob1.get(pid), cp2.get(pid)) == c
        rec["t_confirm_ms"] = round((time.perf_counter() - t3) * 1000)
        rec["confirmation"] = {"differing": len(to_confirm), "confirmed": sum(confirmed.values())}
    finally:
        ws_b.close()
        ws_p.close()
    # 4) classify
    per_fn: dict[str, dict[str, Any]] = {tid: {**fn_meta[tid], "inputs_by_source": {}, "classes": {}, "examples": {}, "probe_ms": 0.0,
                                                "ext_encoded": 0, "generation": gen_status.get(tid, [])} for tid in targets}
    for p in probes2:
        tid, inp = pmap[p["id"]]
        f = per_fn[tid]
        src = inp.origin
        f["inputs_by_source"][src] = f["inputs_by_source"].get(src, 0) + 1
        b1, b2, pp = ob1.get(p["id"]), ob2.get(p["id"]), op.get(p["id"])
        c = pre[p["id"]]
        if c in DIFF_CLASSES and not confirmed.get(p["id"]):
            c = "unconfirmed"  # did not reproduce in fresh runs: flaky / load-dependent, never reported as a difference
        f["classes"][c] = f["classes"].get(c, 0) + 1
        f.setdefault("classes_by_source", {}).setdefault(src, {}).setdefault(c, 0)
        f["classes_by_source"][src][c] += 1
        for o in (b1, pp):
            if o and o.get("status") == "ok" and o["result"].get("ext"):
                f["ext_encoded"] += 1
        f["probe_ms"] += sum(float((o or {}).get("elapsed_ms") or 0) for o in (b1, b2, pp))
        ex = f["examples"].setdefault(c, [])
        if c != "same" and len(ex) < 6 or c == "same" and len(ex) < 1:
            ex.append({"origin": src, "input": render_input(inp, targets[tid]["qualname"]), "base": render_obs(b1), "patch": render_obs(pp),
                       **({"base_repeat": render_obs(b2)} if c == "nondeterministic" else {})})
    for tid, f in per_fn.items():
        f.pop("namespaces", None)
        tot = sum(f["classes"].values())
        exe = tot - sum(f["classes"].get(k, 0) for k in ("not_executable", "unknown", "no_baseline", "timeout_both"))
        f["inputs_total"] = tot
        f["executable"] = exe
        f["differences"] = sum(f["classes"].get(k, 0) for k in DIFF_CLASSES)
        f["nondeterministic"] = f["classes"].get("nondeterministic", 0)
        f["probe_ms"] = round(f["probe_ms"], 1)
        rec["functions"].append(f)
    rec["probes"] = len(probes2)
    rec["t_total_ms"] = round((time.perf_counter() - t0) * 1000)
    return rec
