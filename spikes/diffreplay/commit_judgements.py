"""Per-commit judgement: does the commit intend an observable behavior change of a changed function?
(read from message + diff); joined with replay outcome -> recall on real intended changes.

usage: python commit_judgements.py -> results/commit_recall.json
"""
import json
from pathlib import Path

from analyze import changed_fn_view, load

# prefix -> (intends observable change?, note). Only commits with >=1 replayed changed function are judged.
J = {
 # more-itertools
 "67fcf640": (True, "bucket: a falsy validator (e.g. a callable whose truth value is False) is honoured"),
 "f89d7a33": (True, "ichunked n=0 / negative n"),
 "ca0586d5": (False, "redundant cache check removed"),
 "ed1bd2d4": (False, "optimization"),
 "3eb4053e": (False, "internal storage bound (memory)"),
 "262dbb98": (True, "maxlen detected via getattr: duck-typed deques now honoured"),
 "288d6da0": (True, "bucket lookup should not invent a key (less aggressive fix)"),
 "0c518386": (False, "line length only"),
 "2b8d5cdd": (True, "bucket lookup should not invent a key"),
 "def2dabe": (True, "falsy user exception honoured by one/only"),
 "feaddee0": (False, "optimization"),
 "e426d256": (False, "reuse iterators (performance)"),
 "752799b5": (True, "numeric_range reverse iteration values preserved"),
 "d032cab7": (True, "nonpositive limits rejected"),
 "6b1907de": (True, "seekable.peek/bool with maxlen 0"),
 "0e6dce3a": (False, "clarification refactor"),
 "1b74b56d": (True, "fix/simplify combination_with_replacement_index (message says fix)"),
 "1fd388bc": (True, "window sizes validated through __index__ (accepts index-like objects)"),
 "b8e0cbb8": (False, "docstring only"),
 "ddd67820": (False, "API removal; changed private helper batch size only"),
 "caaec943": (False, "adds functions; the modified function was not meant to change on empty input"),
 "dee22c93": (True, "special case for cwr removed"),
 "ce676d24": (True, "no empty list for empty iterable with maxsplit=0"),
 "7847d432": (True, "TypeError raised while iterating is no longer masked"),
 "63ee5830": (False, "docstring only"),
 "237388cc": (False, "simplify/speed up"),
 "dc2664b4": (True, "numeric_range consistency"),
 # boltons
 "4e5faa3d": (True, "write() return value, backwards text seeks"),
 "1216b467": (True, "PR sweep of fixes"),
 "73e22c90": (True, "Pearson zero-denominator"),
 "1cdc0e9d": (True, "zero IQR histograms"),
 "51508029": (True, "explicit EOF relative seek accepted"),
 "a180020c": (True, "IndexedSet.update flattens"),
 "e00c10a8": (True, "BarrelList bounds fixes + perf"),
 "efff866a": (True, "empty Bits conversions"),
 "5eb6cc04": (True, "negative Bits indices"),
 "8789df2c": (True, "aware datetimes with omitted comparison time"),
 "f5f5cd86": (True, "error message text"),
 "970051b6": (True, "empty names rejected with ValueError"),
 "9decba3e": (True, "no identifying lookups when scrubbing"),
 "a1ea1030": (True, "addlist with iterators"),
 "d45cc414": (True, "parse_qsl encoding"),
 "91b2bb47": (True, "cross-barrel deletion"),
 "448b1db1": (True, "slice bounds/reverse steps"),
 "1a04b637": (True, "relative seeking in binary streams"),
 "5df26592": (True, "sep argument used"),
 "580a9c2d": (False, "coverage + duplicated NotImplemented checks dropped"),
 "ec1ff9f6": (True, "issupserset typo fix"),
 "b5c495be": (True, "keyword-only args forwarded"),
 "c1c25da3": (True, "Bits length off-by-one"),
 "3e9983db": (True, "xfrange semantics"),
 "f1034b07": (True, "nontermination at EOF / negative rel_seek"),
 # dateutil
 "7c5c9895": (False, "docstring only"),
 "7d2e7f63": (False, "typo in docstring"),
 "5cf3e3cb": (True, "error message decoding"),
 "1376b599": (False, "refactor"),
 "1426acd8": (False, "simplify bytes handling"),
 "34906dcd": (True, "fail on inconsistent separators"),
 "7e1ffa23": (False, "zoneinfo rebuild tooling (not replayable)"),
 "1fb2ea99": (False, "property test added; function change is incidental"),
 "bd69e8e9": (True, "gettz('') behavior"),
 "ab9e6102": (False, "docstring typo"),
 "7a3f5c47": (True, "decorator preserves docstring (wrapped function metadata)"),
 "d6820442": (False, "docstring"),
 "40c41c0f": (False, "docstring"),
 "579cd769": (False, "docstring"),
 "ea7f441b": (True, "ParserError repr"),
 "79d2e486": (True, "TypeError in wrapper logic"),
 "6e92f211": (False, "locking (not observable single-threaded)"),
 "d8cc4ccc": (True, "helpful message for bytes in gettz"),
 "b7668eaf": (False, "docstring typos"),
 "d77c0c74": (False, "refactor tzutc() -> UTC"),
 "6588716b": (False, "refactor"),
 "4d5ddce3": (False, "refactor"),
}

labels = json.loads((Path(__file__).parent / "labels.json").read_text())
diff_commits = {l["commit"] for l in labels if l["class"] != "exception_message" or True}
rows = []
for repo in ["more-itertools", "boltons", "dateutil"]:
    for r in load(repo):
        v = changed_fn_view(r)
        if not v:
            continue
        p = r["commit"][:8]
        intends, note = J[p]
        exe = sum(a["executable"] for a in v.values())
        rows.append({"repo": repo, "commit": p, "subject": r["subject"], "intends_change": intends, "note": note, "executable_inputs": exe,
                     "difference_reported": p in diff_commits})
tot = {
    "commits_with_replayed_fn": len(rows),
    "intending_change": sum(r["intends_change"] for r in rows),
    "intending_change_with_executable_input": sum(r["intends_change"] and r["executable_inputs"] > 0 for r in rows),
    "intending_change_detected": sum(r["intends_change"] and r["difference_reported"] for r in rows),
    "not_intending_change": sum(not r["intends_change"] for r in rows),
    "not_intending_change_with_difference": [r["commit"] for r in rows if not r["intends_change"] and r["difference_reported"]],
}
tot["recall_all"] = f"{tot['intending_change_detected']}/{tot['intending_change']}"
tot["recall_given_executable"] = f"{tot['intending_change_detected']}/{tot['intending_change_with_executable_input']}"
out = {"totals": tot, "rows": rows}
(Path(__file__).parent / "results" / "commit_recall.json").write_text(json.dumps(out, indent=1))
print(json.dumps(tot, indent=1))
