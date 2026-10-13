"""try/except/else/finally (I-133), checked against CPython.

The ground truth traces CPython (``sys.settrace`` exception events), so it records where an
operation raised even when a handler then caught it. Every PROVED_SAFE obligation must never
raise its exception there, over an exhaustive run. Every PROVED_UNSAFE witness must raise it
there when replayed. For a failure marked handled, the replayed call must not propagate it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..conftest import check_json

CORPUS = '''
def handled(a: int, b: int):
    try:
        return a // b
    except ZeroDivisionError:
        return 0


def handled_by_parent(a: int, b: int):
    try:
        return a // b
    except ArithmeticError:
        return 0


def handled_in_tuple(a: int, b: int):
    try:
        return a // b
    except (KeyError, ZeroDivisionError):
        return 0


def reraised(a: int, b: int):
    try:
        return a // b
    except ZeroDivisionError:
        raise


def wrong_handler(a: int, b: int):
    try:
        return a // b
    except KeyError:
        return 0


def unknown_handler_type(a: int, b: int, err):
    try:
        return a // b
    except err:
        return 0


def nested_try(a: int, b: int):
    try:
        try:
            return a // b
        except KeyError:
            return 1
    except ZeroDivisionError:
        return 0


def after_try(n: int):
    d = 0
    try:
        d = n + 1
    except Exception:
        d = 1
    return 10 // d


def else_zero(n: int):
    try:
        k = n + 1
    except Exception:
        k = 1
    else:
        k = 0
    return 10 // k


def handler_zero(n: int):
    try:
        k = 10 // n
    except ZeroDivisionError:
        k = 0
    return 10 // k


def handler_reads_body_var(n: int):
    k = 1
    try:
        k = 0
        x = 10 // n
    except ZeroDivisionError:
        return 10 // k
    return 0


def raise_in_body(n: int):
    d = 0
    try:
        if n > 0:
            raise ValueError
        d = 1
    except ValueError:
        pass
    return 10 // d


def guard_in_try(a: int, b: int):
    try:
        if b == 0:
            return 0
        return a // b
    except ValueError:
        return 1


def finally_runs(n: int):
    d = 1
    try:
        d = n
    finally:
        x = 10 // d
    return 0


def finally_after(n: int):
    d = 2
    try:
        d = d - n
    finally:
        pass
    return 10 // d


def finally_with_handler(n: int):
    d = 1
    try:
        d = 10 // n
    except ZeroDivisionError:
        d = 0
    finally:
        e = 5
    return 10 // (d + 1)


def try_in_loop(n: int):
    t = 0
    for i in range(-2, n):
        try:
            t += 10 // i
        except ZeroDivisionError:
            continue
    return t


def break_in_try(n: int):
    d = 1
    for i in range(n):
        try:
            d = 0
            break
        except Exception:
            pass
    return 10 // d


def index_handled(xs: list):
    try:
        return xs[0]
    except IndexError:
        return None
'''

TRACER = r"""
import sys
path = sys.argv[1]
raised, seen, keep = set(), set(), []

def tracer(frame, event, arg):
    if event == "exception" and frame.f_code.co_filename == path and id(arg[1]) not in seen:
        seen.add(id(arg[1]))
        keep.append(arg[1])  # keep alive: ids must stay unique
        raised.add((frame.f_code.co_name, frame.f_lineno, type(arg[1]).__name__))
    return tracer
"""

GROUND_TRUTH = TRACER + r"""
import inspect, itertools, json, runpy
ns = runpy.run_path(path)
domains = {int: list(range(-3, 4)), list: [[], [0], [1, 2]], None: [None, KeyError, ZeroDivisionError, ArithmeticError]}
for name, fn in ns.items():
    if not inspect.isfunction(fn) or fn.__module__ != "<run_path>":
        continue
    params = list(inspect.signature(fn).parameters.values())
    for args in itertools.product(*[domains[p.annotation if p.annotation in (int, list) else None] for p in params]):
        sys.settrace(tracer)
        try:
            fn(*[list(a) if isinstance(a, list) else a for a in args])
        except Exception:
            pass
        finally:
            sys.settrace(None)
print(json.dumps(sorted(raised)))
"""

REPLAY = TRACER + r"""
import inspect, json, runpy, traceback
name, args = sys.argv[2], json.loads(sys.argv[3])
fn = runpy.run_path(path)[name]
call = {p: args.get(p) for p in inspect.signature(fn).parameters}
sys.settrace(tracer)
try:
    fn(**call)
    escaped = None
except Exception as e:
    frames = [t for t in traceback.extract_tb(e.__traceback__) if t.filename == path]
    escaped = [type(e).__name__, frames[-1].lineno if frames else None]  # where the escaping exception was raised
finally:
    sys.settrace(None)
print(json.dumps({"raised": sorted(raised), "escaped": escaped}))
"""

EXC = {"division.zero_denominator": "ZeroDivisionError", "index.out_of_range": "IndexError"}

# (function, operation text) -> (property_result, handled)
EXPECTED = {
    ("handled", "a // b"): ("PROVED_UNSAFE", True),
    ("handled_by_parent", "a // b"): ("PROVED_UNSAFE", True),
    ("handled_in_tuple", "a // b"): ("PROVED_UNSAFE", True),
    ("reraised", "a // b"): ("PROVED_UNSAFE", False),
    ("wrong_handler", "a // b"): ("PROVED_UNSAFE", False),
    ("unknown_handler_type", "a // b"): ("PROVED_UNSAFE", False),
    ("nested_try", "a // b"): ("PROVED_UNSAFE", True),
    ("after_try", "10 // d"): ("PROVED_UNSAFE", False),
    ("else_zero", "10 // k"): ("PROVED_UNSAFE", False),
    ("handler_zero", "10 // n"): ("PROVED_UNSAFE", True),
    ("handler_zero", "10 // k"): ("POSSIBLY_UNSAFE", False),
    ("handler_reads_body_var", "10 // n"): ("PROVED_UNSAFE", True),
    ("handler_reads_body_var", "10 // k"): ("POSSIBLY_UNSAFE", False),
    ("raise_in_body", "10 // d"): ("POSSIBLY_UNSAFE", False),
    ("guard_in_try", "a // b"): ("PROVED_SAFE", False),
    ("finally_runs", "10 // d"): ("PROVED_UNSAFE", False),
    ("finally_after", "10 // d"): ("PROVED_UNSAFE", False),
    ("finally_with_handler", "10 // n"): ("PROVED_UNSAFE", True),
    ("try_in_loop", "10 // i"): ("PROVED_UNSAFE", True),
    ("break_in_try", "10 // d"): ("PROVED_UNSAFE", False),
    ("index_handled", "xs[0]"): ("PROVED_UNSAFE", True),
}


def _child(script: str, *argv: str) -> object:
    p = subprocess.run([sys.executable, "-I", "-c", script, *argv], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_try_against_cpython(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()  # stop config discovery at this root
    path = root / "m.py"
    path.write_text(CORPUS, encoding="utf-8")
    code, rep, _ = check_json(root)
    assert rep["errors"] == [], rep["errors"]
    raised = {tuple(r) for r in _child(GROUND_TRUTH, str(path))}
    got: dict[tuple[str, str], tuple[str, bool]] = {}
    for o in rep["obligation_results"]:
        fn = o["symbol_id"].split("::")[1]
        line, result, exc = o["source_span"]["start_line"], o["property_result"], EXC[o["property_id"]]
        handled = not o["required"] and any("handled" in r for r in o["reasons"])
        got[(fn, o["operation_text"])] = (result, handled)
        if result == "PROVED_SAFE":
            assert (fn, line, exc) not in raised, f"unsound PROVED_SAFE: CPython raises {exc} in {fn} at line {line}"
        if result == "PROVED_UNSAFE":
            assert o["witness_validated"] is True
            out = _child(REPLAY, str(path), fn, json.dumps(o["witness"] or {}))
            assert [fn, line, exc] in out["raised"], f"{fn}: witness {o['witness']} does not raise {exc} at line {line}: {out}"
            if handled:
                assert out["escaped"] != [exc, line], f"{fn}: marked handled, but the {exc} from line {line} escapes the call"
    assert {k: got.get(k) for k in EXPECTED} == EXPECTED
    finding = {f["obligation_id"]: f["severity"] for f in rep["findings"]}
    for o in rep["obligation_results"]:
        if o["property_result"] == "PROVED_UNSAFE" and not o["required"]:
            assert finding[o["obligation_id"]] == "INFO"
