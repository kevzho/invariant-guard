"""HOST-SIDE static: build seeded-regression pairs. base = the commit's patched tree (unmodified), patch = same tree with ONE
realistic mutation applied inside the target function's line range. Output work_seeded/<id>/{base,patch}, seeded_cases.json."""
import json, ast, os, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
cases = [x for x in json.load(open(os.path.join(HERE, "cases.json"))) if "only_in" not in x]
def idx(repo, qn): return next(i for i, c in enumerate(cases) if c["repo"] == repo and c["qualname"] == qn)
# (id, repo, qualname, kind, old, new)   case chosen = first case matching repo+qualname unless 'short' given
M = [
 ("S01","boltons","bytes2human","off-by-one","abs_bytes < next_size","abs_bytes <= next_size"),
 ("S02","boltons","bytes2human","dropped edge case (sign)","float(nbytes) / size","float(abs_bytes) / size"),
 ("S03","boltons","bytes2human","wrong default","ndigits=0","ndigits=1"),
 ("S04","more-itertools","one","falsy-guard regression","if too_short is not None:","if too_short:"),
 ("S05","more-itertools","only","falsy-guard regression","if too_long is not None:","if too_long:"),
 ("S06","more-itertools","only","wrong default","    return default","    return None"),
 ("S07","dateutil","within_delta","off-by-one (closed->open interval)","-delta <= difference <= delta","-delta < difference < delta"),
 ("S08","dateutil","within_delta","dropped edge case (abs)","delta = abs(delta)","pass"),
 ("S09","dateutil","default_tzinfo","swapped condition","if dt.tzinfo is not None:","if dt.tzinfo is None:"),
 ("S10","dateutil","easter","off-by-one at boundary","if y > 1600:","if y >= 1600:"),
 ("S11","dateutil","easter","dropped validation","if not (1 <= method <= 3):","if not (1 <= method <= 4):"),
 ("S12","boltons","Bits.__getitem__","off-by-one","k >= self.len","k > self.len"),
 ("S13","boltons","Bits.__init__","off-by-one","val >= 2 ** len_","val > 2 ** len_"),
 ("S14","boltons","_ComplementSet.__le__","swapped condition","return self._included.issubset(inc)","return self._included.issuperset(inc)"),
 ("S15","boltons","args2cmd","dropped edge case (empty arg)",' or not arg',''),
 ("S16","more-itertools","constrained_batches","off-by-one (generator)","if max_size <= 0:","if max_size < 0:"),
 ("S17","more-itertools","split_before","dropped edge case (generator)","if pred(item) and buf:","if pred(item):"),
 ("S18","boltons","xfrange","dropped rounding (generator)","int(math.ceil((stop - start) / step))","int((stop - start) / step)"),
 ("S19","more-itertools","ichunked","off-by-one (generator)","n < 0","n <= 0"),
 ("S20","more-itertools","one","mutated argument","    iterator = iter(iterable)\n","    if isinstance(iterable, list):\n        iterable.append(None)\n    iterator = iter(iterable)\n"),
]
out = []
root = os.path.join(HERE, "work_seeded"); shutil.rmtree(root, ignore_errors=True)
for sid, repo, qn, kind, old, new in M:
    c = cases[idx(repo, qn)]
    src_case = os.path.join(HERE, "work", c["repo"], c["short"])
    d = os.path.join(root, sid)
    shutil.copytree(os.path.join(src_case, "patch"), os.path.join(d, "base"))
    shutil.copytree(os.path.join(src_case, "patch"), os.path.join(d, "patch"))
    fpath = os.path.join(d, "patch", c["file"]); text = open(fpath).read(); tree = ast.parse(text)
    def find(body, parts):
        for n in body:
            if getattr(n, "name", None) == parts[0]:
                return n if len(parts) == 1 else find(n.body, parts[1:])
    fn = find(tree.body, qn.split(".")); lines = text.splitlines(keepends=True)
    seg = "".join(lines[fn.lineno - 1: fn.end_lineno])
    assert old in seg, (sid, old)
    seg2 = seg.replace(old, new, 1)
    open(fpath, "w").write("".join(lines[:fn.lineno - 1]) + seg2 + "".join(lines[fn.end_lineno:]))
    out.append(dict(id=sid, repo=repo, qualname=qn, module=c["module"], syspath=c["syspath"], kind=kind, old=old, new=new, file=c["file"], short=c["short"]))
json.dump(out, open(os.path.join(HERE, "seeded_cases.json"), "w"), indent=1)
print(len(out), "seeded pairs")
