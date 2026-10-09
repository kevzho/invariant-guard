"""Runs INSIDE the container only (never on the host). Loads base and patch versions of one function from two
read-only source trees, runs CrossHair's diff_behavior (same call as `crosshair diffbehavior`), then replays
each reported input concretely on both versions. Replay inputs are parsed with ast.literal_eval only (no eval).
argv[1] = JSON {case_dir, syspath, module, qualname, timeout, mode: diff|check, tag?}"""
import sys, os, json, time, copy, ast, signal, importlib, traceback, collections, io, contextlib

cfg = json.loads(sys.argv[1])
CASE, SP, MOD, QN, T = cfg["case_dir"], cfg["syspath"], cfg["module"], cfg["qualname"], float(cfg["timeout"])
out = {"module": MOD, "qualname": QN, "timeout": T, "status": None}
t0 = time.time()

def emit():
    out["wall_s"] = round(time.time() - t0, 2)
    sys.stdout.write("\n@@RESULT@@" + json.dumps(out) + "\n"); sys.stdout.flush()

def load_side(side):
    top = MOD.split(".")[0]
    for m in [m for m in sys.modules if m == top or m.startswith(top + ".")]:
        del sys.modules[m]
    for p in [p for p in sys.path if p.startswith(CASE)]:
        sys.path.remove(p)
    sys.path.insert(0, os.path.normpath(os.path.join(CASE, side, SP)))
    from crosshair.fnutil import walk_qualname
    mod = importlib.import_module(MOD)
    assert os.path.realpath(mod.__file__).startswith(os.path.realpath(os.path.join(CASE, side))), mod.__file__
    return walk_qualname(mod, QN)

try:
    import crosshair.core_and_libs  # registers CrossHair's patches (as the CLI does)
    from crosshair.auditwall import engage_auditwall, disable_auditwall
    from crosshair.pure_importer import prefer_pure_python_imports
    from crosshair.options import DEFAULT_OPTIONS, AnalysisOptionSet
    from crosshair.diff_behavior import diff_behavior, ExceptionEquivalenceType
    from crosshair.fnutil import FunctionInfo
    if cfg.get("mode") == "check":
        pass
    else:
        with prefer_pure_python_imports():
            base = load_side("base"); patch_ = load_side("patch")
        out["sig_base"] = str(base.get_callable()[1]) if base.get_callable() else None
        out["sig_patch"] = str(patch_.get_callable()[1]) if patch_.get_callable() else None
        if not isinstance(base, FunctionInfo) or not isinstance(patch_, FunctionInfo):
            out["status"] = "unsupported"; out["detail"] = "not a function"; emit(); sys.exit(0)
        if base.get_callable() is None or patch_.get_callable() is None:
            out["status"] = "unsupported"; out["detail"] = "cannot determine signature"; emit(); sys.exit(0)
        opts = DEFAULT_OPTIONS.overlay(AnalysisOptionSet(per_path_timeout=30.0)).overlay(AnalysisOptionSet(per_condition_timeout=T, timeout=T))
        opts.stats = collections.Counter()
        nondet = []
        engage_auditwall()
        diffs = diff_behavior(base, patch_, opts, ExceptionEquivalenceType.TYPE_AND_MESSAGE, on_nondeterminism=lambda: nondet.append(1))
        out["analysis_s"] = round(time.time() - t0, 2)
        out["stats"] = {k: v for k, v in opts.stats.items()}
        out["nondeterminism_seen"] = bool(nondet)
        if isinstance(diffs, str):
            out["status"] = "crashed"; out["detail"] = diffs[:500]; emit(); sys.exit(0)
        out["exhausted"] = opts.stats.get("exhaustion", 0) > 0
        out["num_paths"] = opts.stats.get("num_paths", 0)
        if not diffs:
            out["status"] = "no_diff_exhausted" if out["exhausted"] else "no_diff_budget"
            emit(); sys.exit(0)
        disable_auditwall()
        out["status"] = "diff_found"
        out["diffs"] = []
        fb, fp = base.callable()[0], patch_.callable()[0]
        def alarm(*a): raise TimeoutError("replay timeout")
        signal.signal(signal.SIGALRM, alarm)
        def run(fn, kwargs):
            kw = copy.deepcopy(kwargs)
            r = {}
            signal.alarm(5)
            try:
                v = fn(**kw)
                if hasattr(v, "__next__"):
                    v = ("<iterator>", list(__import__("itertools").islice(v, 50)))
                r["ret"] = repr(v)[:300]
            except BaseException as e:
                r["exc"] = f"{type(e).__name__}: {e}"[:300]
            finally:
                signal.alarm(0)
            r["post_args"] = repr(kw)[:300]
            return r
        for d in diffs:
            rec = {"args": d.args, "base": d.result1.describe(d.result1.get_differing_arg_mutations(d.result2)),
                   "patch": d.result2.describe(d.result1.get_differing_arg_mutations(d.result2))}
            try:
                kwargs = {k: ast.literal_eval(v) for k, v in d.args.items()}
                rb, rp = run(fb, kwargs), run(fp, kwargs)
                rec["replay"] = {"base": rb, "patch": rp, "differs": rb != rp}
            except Exception as e:
                rec["replay"] = {"unreplayable": f"{type(e).__name__}: {e}"[:200]}
            out["diffs"].append(rec)
        emit()
except SystemExit:
    raise
except BaseException as e:
    out["status"] = out["status"] or "crashed"
    out["detail"] = f"{type(e).__name__}: {e}"[:500]
    out["tb"] = traceback.format_exc()[-800:]
    emit()
