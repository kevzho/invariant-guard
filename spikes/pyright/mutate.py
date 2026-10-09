"""Seeded-mutation detection. Each mutation edits ONE function in the patch snapshot of a real commit
(exported copy; the clone is never touched) and checks whether pyright reports a new diagnostic in it."""
import json, os, re, shutil, sys
from collections import Counter
from common import *
M = [
 # id, kind, repo, sha10, file, qualname, old, new
 ("M01","ATTR","more-itertools","262dbb9805","more_itertools/more.py","seekable.peek","self._index = len(self._cache)","self._index = len(self._cach)"),
 ("M02","ARGS","more-itertools","262dbb9805","more_itertools/more.py","seekable.peek","peeked = next(self)","peeked = next(self, None, 0)"),
 ("M03","UNBOUND","more-itertools","0c5183863a","more_itertools/more.py","bucket._get_values","except StopIteration:\n                        return\n                    item_value","except StopIteration:\n                        pass\n                    item_value"),
 ("M04","ARGS","more-itertools","0c5183863a","more_itertools/more.py","bucket._get_values","self._cache.setdefault(value, deque())","self._cache.setdefault(value, deque([], 1, 2))"),
 ("M05","ATTR","more-itertools","feaddee0ff","more_itertools/more.py","numeric_range.__reversed__","reversed(range(self._len))","reversed(range(self._length))"),
 ("M06","ARGS","more-itertools","f89d7a3331","more_itertools/more.py","ichunked","rest = islice(iterator, n - 1)","rest = islice(iterator, n - 1, 1, 1, 1)"),
 ("M07","RET","boltons","1cdc0e9d36","boltons/statsutils.py","Stats._get_bin_bounds","        return bins","        return None"),   # plus annotation, see ANNOT
 ("M08","OPT","boltons","8789df2cc3","boltons/timeutils.py","decimal_relative_time","bbound, bunit, bname = _BOUNDS[b_idx]","bbound, bunit, bname = next((b for b in _BOUNDS if b[0] == abs_diff), None)"),
 ("M09","ATTR","boltons","8789df2cc3","boltons/timeutils.py","decimal_relative_time","other = datetime.now(d.tzinfo)","other = datetime.now(d.tz_info)"),
 ("M10","ARGS","boltons","efff866a3b","boltons/mathutils.py","Bits.as_hex","(self.len // 8 + ((self.len % 8) != 0))","(int(self.len // 8, 2, 3) + ((self.len % 8) != 0))"),
 ("M11","UNBOUND","boltons","a180020c76","boltons/setutils.py","IndexedSet.update","        else:\n            other = chain.from_iterable(others)","        elif len(others) == 2:\n            other = chain.from_iterable(others)"),
 ("M12","ATTR","boltons","5150802911","boltons/jsonutils.py","JSONLIterator.__init__","self._init_rel_seek()","self._init_relseek()"),
 ("M13","OPT","boltons","5150802911","boltons/jsonutils.py","JSONLIterator.__init__","        self._blocksize = 4096\n","        import os as _os\n        self._blocksize = int(_os.environ.get('BLOCKSIZE').strip())\n"),
 ("M14","OPT","more-itertools","f89d7a3331","more_itertools/more.py","ichunked","def ichunked(iterable, n):","def ichunked(iterable, n: 'int | None'):"),
 ("M15","RET","boltons","efff866a3b","boltons/mathutils.py","Bits.as_hex","        return ret","        return self.val"),
]
ANNOT = {"M07": ("def _get_bin_bounds(self, count=None, with_max=False):", "def _get_bin_bounds(self, count=None, with_max=False) -> list:"),
         "M15": ("def as_hex(self):", "def as_hex(self) -> str:")}
out = []
for mid, kind, repo, sha, f, q, old, new in M:
    src_patch = f"{WORK}/{repo}/{sha}/patch"
    dst = f"{WORK}/mut/{mid}"
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src_patch, dst)
    p = os.path.join(dst, f); lines = open(p).read().split("\n")
    fn = next(x for x in func_table(p) if x["q"] == q)
    # edit restricted to function lines (incl. def line for annotations)
    seg = "\n".join(lines[fn["s"]-1:fn["e"]])
    if mid in ANNOT:
        a, b = ANNOT[mid]; assert seg.count(a) == 1, (mid, "annot"); seg = seg.replace(a, b)
    assert seg.count(old) == 1, (mid, "old string count", seg.count(old)); seg = seg.replace(old, new)
    lines[fn["s"]-1:fn["e"]] = seg.split("\n"); open(p, "w").write("\n".join(lines))
    cfg = json.load(open(dst + "/pyrightconfig.json"))   # scoped include from the measurement run
    base_d, _, _, _ = run_pyright(src_patch); mut_d, t, _, _ = run_pyright(dst)
    btab = func_table(os.path.join(src_patch, f)); mtab = func_table(p)
    def key(d, tab): return (d["file"], enclosing(tab, d["line"]) if d["file"] == f else "?", d["rule"], re.sub(r"\d+", "N", d["msg"]))
    bk = Counter(key(d, btab) for d in base_d); mk = Counter(key(d, mtab) for d in mut_d)
    intro = mk - bk
    hit = [k for k in intro if k[0] == f and k[1].split(".<locals>")[0] == q]
    other = [k for k in intro if k not in hit]
    out.append(dict(id=mid, kind=kind, repo=repo, sha=sha, fn=q, detected=bool(hit),
                    hit_rules=sorted({k[2] for k in hit}), hit_msgs=[k[3][:110] for k in hit], other_introduced=len(other), t=round(t, 1)))
    print(mid, kind, "DETECTED" if hit else "MISSED", sorted({k[2] for k in hit}), "other:", len(other), flush=True)
json.dump(out, open("results_mutations.json", "w"), indent=1)
n = sum(o["detected"] for o in out); print("detected", n, "/", len(out))
for k in ["ATTR","ARGS","UNBOUND","OPT","RET"]:
    xs = [o for o in out if o["kind"] == k]; print(k, sum(o["detected"] for o in xs), "/", len(xs))
