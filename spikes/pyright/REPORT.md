# Spike: pyright as a patch-scoped static engine for InvariantGuard

Date 2026-10-09. Nothing under `src/` or `tests/` was touched; nothing was committed; the corpus clones' working trees were never modified (snapshots come from `git archive`).

## Verdict (short)

**GO, conditionally, as an optional external-analyzer adapter that can emit at most WARN by default.** It must never produce PROVED_*, never contribute to PASS by silence, and BLOCK only under an explicit opt-in policy or with corroboration (see section 5). Reasons:

- Differential, patch-scoped output is quiet: 13 introduced diagnostics over 90 real commits (0.14 per commit; 7 of 90 commits had any).
- Detection of seeded type-level bugs is high where types are inferable: 14/15.
- But real-commit precision was **0/13** (every introduced diagnostic was noise, in 7 root-cause clusters), so it is advisory-only. Bounds: 95% upper limit about 23% on the true-positive rate (rule of three, 3/13).
- Cost is fine for scoped runs (median 2.8-6.2 CPU-s) but heavy on some files (79 CPU-s worst case; about 88 CPU-s for all of dateutil).
- Open risk: real commits by maintainers are a poor proxy for agent patches. A follow-up on agent-generated patches is needed before trusting the precision figure.

## 1. Setup

| Item | Value |
| --- | --- |
| pyright (PyPI wrapper) | 1.1.408 (bundles the pyright 1.1.408 JS `dist/`), pinned in `requirements.lock` |
| node | v26.5.0 (/opt/homebrew/bin/node, system) |
| venv python | 3.14.5 (`spikes/pyright/.venv`) |
| Checked-code settings | `pythonVersion 3.12`, `pythonPlatform Linux`, default `standard` mode, per-snapshot `pyrightconfig.json` |
| Corpus HEADs | more-itertools 81c21a8, boltons 4e5faa3, dateutil 2642afa (already present in `spikes/corpus/`, reused) |

Commands:

```
cd spikes/pyright
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
# invoked directly through node, not through the python wrapper:
node .venv/lib/python3.14/site-packages/pyright/dist/index.js --outputjson --pythonpath .venv/bin/python -p <snapshot>
./run_all.sh     # measure.py x3 repos, mutate.py, sample_pre.py (full reproduction)
```

**Does pyright import or execute target code?** No. It parses and type-checks. I confirmed in the bundled `pyright-internal.js` that the only subprocess it spawns is the *interpreter used for search-path discovery*, with a fixed snippet (`python -I -c "import sys, json; ... json.dump(dict(path=..., prefix=...))"`; `-I` isolates cwd/user site). Consequences for us:

1. The interpreter we pass via `--pythonpath` must be a trusted one. Its own `.pth` files and `sitecustomize` run. Never point it at the target's venv.
2. `pyrightconfig.json` / `[tool.pyright]` are data only, no plugins. Still, we should generate our own config rather than honor the repo's.
3. I did not sandbox-verify "no network" with a tracer. I only know the node run has no download step.

**Downloads / network.** `pip install pyright` downloads the wheel (which contains `dist/`, so nothing else is fetched at run time). The Python *wrapper* (`pyright` console script) tries to query the latest npm release on every start (it failed here with a certificate error, harmless), and falls back to `nodeenv` to download node when no node is on PATH. To honor "no network during verification" the adapter must call `node dist/index.js` directly (as these scripts do) and require a pre-installed node.

## 2. Method (patch-scoped measurement)

`measure.py REPO 30` for each repo:

1. The 30 most recent non-merge commits touching `.py` under the package (`more_itertools/`, `boltons/`, `src/dateutil/` or the older `dateutil/` layout) excluding tests. Parent and commit must both contain the package dir. Result: 30/30/30, 0 skipped in the final run.
2. `git archive` of parent (base) and commit (patch), package dir only, into `work/REPO/SHA/{base,patch}`.
3. Scope: changed functions (via `git diff -U0` hunks mapped onto `ast` def ranges, both sides, with `<module>` for top-level changes) plus **direct callers** found in the package by name (any def whose body calls a Name or Attribute with the same simple name as a changed function; this over-approximates, so it is a name match, not call-graph resolution).
4. Pyright is run on base and patch with `include` = changed files + caller files (a scoped run; unchanged other files are still read on demand for imports). Diagnostics are keyed by (file, enclosing function qualname, rule, message with digits normalized), so line shifts do not matter. The multiset difference gives INTRODUCED / RESOLVED / PRE_EXISTING, then filtered to the scope.
5. Timing: wall and CPU (`/usr/bin/time -p`). **The host load average was 40-200 (other spikes running), so wall times are inflated and noisy; CPU time is the usable number.** Base and patch were run concurrently in two threads.

## 3. Measurements

Denominator: 90 commits (30 per repo), 101 changed files, 169 changed functions (incl. `<module>`), 482 caller defs matched by name.

| | more-itertools | boltons | dateutil | total |
| --- | --- | --- | --- | --- |
| commits | 30 | 30 | 30 | 90 |
| commits with >=1 INTRODUCED in scope | 0 | 5 | 2 | **7 (7.8%)** |
| INTRODUCED diagnostics in scope | 0 | 10 | 3 | **13** |
| INTRODUCED outside scope files/functions | 0 | 0 | 0 | 0 (scoped run cannot see them; see failure modes) |
| RESOLVED in scope | 0 | 5 | 1 | 6 |
| PRE_EXISTING in scope (summed over commits, not unique) | 17 | 142 | 171 | 330 |
| baseline diagnostics per scoped run, median | 22 | 23 | 48 | |
| scope files per run, median | 1 | 1 | 1 | |
| patch-run CPU seconds, median / max | 6.2 / 7.3 | 2.8 / 8.9 | 3.9 / 78.9 | |
| patch-run wall seconds, median / p90 / max (loaded host) | 8.3 / 12.3 / 13.7 | 5.0 / 10.0 / 15.5 | 5.4 / 100.6 / 378.1 | |

- The differential removes almost everything: 330 pre-existing + 13 introduced diagnostics in scope; a non-differential report would be 96% noise by volume before any labeling.
- **Cold vs cache.** The pyright CLI has no persistent cache; I confirm there is none to configure. "Warm" = immediate repeat (OS file cache only): 3 repeats per repo showed no systematic speedup (e.g. 10.7 -> 6.0 s, 6.0 -> 7.2 s, 6.4 -> 4.9 s, 1.5 -> 2.4 s), i.e. load noise dominates. Incremental behavior would require the language-server/watch mode, not tested.
- Full-package (not scoped) dateutil run, observed once: 88 CPU-s user for 17 files (230 errors, 23 warnings). The first attempt at full-package runs for all 3 repos was aborted for exactly this cost; scoping took median CPU to 3-6 s.
- Rule breakdown of the 13 INTRODUCED: reportArgumentType 4, reportIncompatibleMethodOverride 3, reportMissingModuleSource 2, reportCallIssue 1, reportOperatorIssue 1, reportOptionalMemberAccess 1, reportAssignmentType 1.
- Rule breakdown of the pool of PRE_EXISTING diagnostics in changed functions (137 instances, for context): reportMissingModuleSource 33, reportAssignmentType 32, reportAttributeAccessIssue 23, reportArgumentType 11, reportPossiblyUnbound 8, reportUnsupportedDunderAll 7, reportCallIssue 5, reportIncompatibleMethodOverride 4, reportOptionalMemberAccess 4, reportUndefinedVariable 4, reportMissingImports 3, reportOperatorIssue 2, reportGeneralTypeIssues 1.

Raw data: `results_{more-itertools,boltons,dateutil}.json` (per commit, incl. every introduced diagnostic with message), `log_*.txt`, `pre_existing_pool.json`.

### Hand-labeled sample (25 total: all 13 INTRODUCED + 12 seeded-random PRE_EXISTING)

"Real" means a plausible runtime bug. Labels are mine, from reading the code at the cited commit.

INTRODUCED (13; 7 root-cause clusters; **0 real**):

| # | repo@commit, location | rule | label | justification |
| --- | --- | --- | --- | --- |
| 1-3 | boltons@4e5faa3d `ioutils.py` `SpooledBytesIO.write`, `SpooledStringIO.write/seek` | reportIncompatibleMethodOverride | noise | Patch fixes these to return `int`; the base stub `SpooledIOBase.write` returns inferred `None`. Pyright flags the (correct) override. It is an interface observation, not a failure. |
| 4-5 | boltons@e00c10a8 `listutils.py:149` `BarrelList.insert` | reportCallIssue + reportArgumentType | noise (guarded) | `_translate_index` returns inferred `(int \| None, ...)`; the patch clamps `index` first so the None path is unreachable. Class invariant unseen by the type model. |
| 6 | boltons@efff866a `mathutils.py:242` `Bits.from_hex` -> `cls('')` | reportArgumentType | noise | `Bits.__init__(self, val=0)` has param type inferred from the default (`int`), while the body handles str/bytes/list. |
| 7-9 | boltons@1a04b637 `jsonutils.py:182,190` `_align_to_newline` | reportOperatorIssue, reportArgumentType x2 | noise | `newline` is bytes or str chosen once from the file mode; pyright loses the correlation and checks `bytes\|str` against `bytes` and `str` separately. |
| 10 | boltons@ec1ff9f6 `setutils.py:827` `_ComplementSet.__le__` | reportOptionalMemberAccess | noise | Branch is reached only when `_included is None`, which implies `_excluded` is a set (class invariant). |
| 11-12 | dateutil@6e92f211 `tz/_factories.py:5` | reportMissingModuleSource x2 | noise (environment) | `six` stubs resolved but sources not installed; new import line in the patch made it "introduced". |
| 13 | dateutil@980e5aa6 `tz/tz.py:1826` | reportAssignmentType | noise | `try: from contextlib import nullcontext as _nullcontext / except ImportError: class _nullcontext` compat shim. |

PRE_EXISTING sample (12, seed 0, from `sample_pre.py`; shown to illustrate what a non-differential report would contain; **0 clearly real, 1 uncertain**):

| id | location | rule | label |
| --- | --- | --- | --- |
| P1, P5, P12 | dateutil `tz/tz.py:859,356` `__hash__ = None` | reportAssignmentType | noise (idiom) |
| P2 | dateutil@b7668eaf `rrule.py:17` `from fractions import gcd` | reportAttributeAccessIssue | version-conditional (removed in 3.9; correct on 3.12, but repo has a fallback path); noise at our config |
| P3 | more-itertools `numeric_range.index(self, value)` | reportIncompatibleMethodOverride | noise (intentional narrowing of `Sequence.index`) |
| P4 | dateutil `parser/isoparser.py:146` `datetime(*components)` | reportArgumentType | noise (heterogeneous tuple star-arg) |
| P6, P9, P10, P11 | dateutil `tz/tz.py` `import six...` | reportMissingModuleSource | noise (environment) |
| P7 | dateutil `parser/_parser.py:430` `self[self.mstridx]` | reportArgumentType (`int\|None` index) | **uncertain**: index may be None if the month string was not found; I could not confirm a reachable path |
| P8 | dateutil `tz/tz.py:1649` `WindowsError` | reportUndefinedVariable | platform-conditional (we set Linux); noise |

## 4. Seeded positives (`mutate.py`, 15 mutations)

Each mutation edits exactly one function (asserted unique inside the function's line range) of the *patch snapshot of a real commit*; it is applied to an exported copy. Detected = a new diagnostic (vs the unmutated patch snapshot) located in the mutated function.

| id | kind | repo / function | mutation | result |
| --- | --- | --- | --- | --- |
| M01 | wrong attribute | mit `seekable.peek` | `self._cache` -> `self._cach` | DETECTED reportAttributeAccessIssue |
| M02 | wrong arg count | mit `seekable.peek` | `next(self, None, 0)` | DETECTED reportCallIssue |
| M03 | possibly unbound | mit `bucket._get_values` | `except StopIteration: return` -> `pass` | DETECTED reportPossiblyUnbound |
| M04 | wrong arg count | mit `bucket._get_values` | `deque([], 1, 2)` | DETECTED reportCallIssue |
| M05 | wrong attribute | mit `numeric_range.__reversed__` | `self._len` -> `self._length` | DETECTED reportAttributeAccessIssue |
| M06 | wrong arg count | mit `ichunked` | `islice(iterator, n-1, 1, 1, 1)` | DETECTED reportCallIssue |
| M07 | wrong return type | boltons `Stats._get_bin_bounds` | add `-> list`, `return None` | DETECTED reportReturnType (+1 other introduced in file) |
| M08 | Optional misuse | boltons `decimal_relative_time` | unpack `next(gen, None)` | DETECTED reportGeneralTypeIssues |
| M09 | wrong attribute | boltons `decimal_relative_time` | `d.tzinfo` -> `d.tz_info` | **MISSED** (`d` is an unannotated parameter, so type Unknown) |
| M10 | wrong arg count | boltons `Bits.as_hex` | `int(x, 2, 3)` | DETECTED reportCallIssue |
| M11 | possibly unbound | boltons `IndexedSet.update` | `else:` -> `elif len(others)==2:` | DETECTED reportPossiblyUnbound |
| M12 | wrong attribute | boltons `JSONLIterator.__init__` | `_init_relseek` | DETECTED reportAttributeAccessIssue (self attribute) |
| M13 | Optional misuse | boltons `JSONLIterator.__init__` | `int(os.environ.get('X').strip())` | DETECTED reportOptionalMemberAccess |
| M14 | Optional misuse | mit `ichunked` | param `n: int \| None`, then `n - 1` | DETECTED reportOptionalOperand |
| M15 | wrong return type | boltons `Bits.as_hex` | add `-> str`, `return self.val` (int) | DETECTED reportReturnType |

**14/15 detected (93%; Wilson 95% interval about 70-99%).** By kind: ATTR 3/4, ARGS 4/4, UNBOUND 2/2, OPT 3/3, RET 2/2. Caveats, stated plainly: I authored the mutations and chose functions where types are inferable; M07, M14, M15 add annotations as part of the mutation (they only test the case where annotations exist); M13 uses a stdlib Optional source. The miss (M09) is the typical pattern for unannotated parameters in boltons and dateutil, so recall on real unannotated code will be lower than 93%. The 15 mutations are a detection-sanity test, not a recall estimate.

## 5. Evidence mapping (proposal)

Principles: pyright output is a **type-model claim, conditional on annotations, inferred types from defaults, bundled typeshed stubs (version-pinned), `pythonVersion` and `pythonPlatform`**. Pyright diagnostics are sound-ish in neither direction (it is neither a sound checker nor a bug-finder with a no-false-positive guarantee). Hence:

- `property_result`: **POSSIBLY_UNSAFE** for every diagnostic. Never PROVED_UNSAFE, never EXACT_MODEL (docs/03: SAT on an approximate model is a possible failure). It becomes OBSERVED_FAILURE / OBSERVED_REGRESSION only when our dynamic replay reproduces it, and PROVED_UNSAFE only when our own exact-model engine confirms the same location.
- `scope_validity`: **CONDITIONAL** for rules whose message involves only declared/stub types; **OVERAPPROXIMATE** when the message contains `Unknown`, a union produced by inference (`int | None` from a helper's inferred return, `bytes | LiteralString`), or a type inferred from a parameter default. Record in `assumptions`: "annotations and typeshed stubs of pyright 1.1.408 for py3.12/linux hold; no runtime narrowing outside pyright's flow analysis".
- `authority`: **INFERRED** (the property "no pyright type error here" was never declared or approved; it is tool-implied, with `source.engine=pyright`, `engine_version`, `rule_id`, `trust_level=external_type_model`, `precision_limits`). It can be upgraded to DECLARED only by an explicit repo policy (see BLOCK below), not by a model or agent.
- `patch_relation`: from the base/patch diff: INTRODUCED, PRE_EXISTING, RESOLVED; CHANGED when the same (file, function, rule) exists with a different message; UNDETERMINED when the base run failed or the function cannot be matched (renames/moves).
- `severity` (default policy): ERROR for tier 1 below on INTRODUCED in a changed function; WARNING otherwise; INFO for tier 3; coverage-only for tier 4.
- No diagnostics = **no evidence** (UNVERIFIED/UNKNOWN for the obligation, never PROVED_SAFE and never "pyright says clean => PASS"). Coverage: record files analyzed, `# type: ignore` lines, unresolved imports, `Unknown` counts.

Rule tiers:

| Tier | Rules | property_result / scope | Default gate | Notes |
| --- | --- | --- | --- | --- |
| 1 likely-crash | reportCallIssue, reportAttributeAccessIssue, reportIndexIssue, reportOptional{MemberAccess,Subscript,Iterable,Call,Operand}, reportPossiblyUnbound, reportUndefinedVariable, reportAbstractUsage, reportNoOverloadImplementation | POSSIBLY_UNSAFE / CONDITIONAL (OVERAPPROXIMATE if Unknown or inferred-union involved) | **WARN** (severity ERROR); *eligible for BLOCK only under the conditions below* | All 14 detected seeds fell here or in tier 2. Real-commit false positives still occurred here (Optional member access, call issue on guarded None). |
| 2 type-contract mismatch | reportArgumentType, reportReturnType, reportAssignmentType, reportOperatorIssue, reportGeneralTypeIssues | POSSIBLY_UNSAFE / CONDITIONAL or OVERAPPROXIMATE | **WARN** (severity WARNING) | Highest observed noise: 5 of the 13 introduced. M07/M15 shows true value when the author annotated. |
| 3 interface | reportIncompatibleMethodOverride, reportIncompatibleVariableOverride | UNVERIFIED, not a failure; surface as `contract_impact` hint | **INFO** | 3 of 13 introduced; relevant to cross-file impact (signature/return change for subclasses) but never a runtime-failure claim. |
| 4 environment/coverage | reportMissingImports, reportMissingModuleSource, reportMissingTypeStubs, reportUnsupportedDunderAll, rule-less syntax errors | not a finding | Coverage: unsupported/unresolved (run INCOMPLETE only if the plan declared those modules required); otherwise WARN + capability disclosure | 2 of 13 introduced and 43 of the 137 pre-existing pool. Rule-less syntax errors defer to our own `ast` parse (exact). |
| ignore | all style/unused rules (off in `standard`), anything not in the table | | drop | |

**BLOCK vs WARN under docs/03.** Default table rows are: BLOCK needs an exact modeled failure, a replayed regression of a DECLARED/APPROVED contract, or an engine of equal strength. Pyright satisfies none of them, so **by default nothing BLOCKs; tier 1 and 2 are WARN** ("Introduced conditional failure whose preconditions are unconfirmed at real callers" row, plus "SAT on overapproximate model" row). Allowed BLOCK paths, each needing an explicit policy decision (and a docs/03 amendment, because they are not in the default table):

1. **Corroboration**: tier 1 pyright finding at the same symbol and location as our own PROVED_UNSAFE (exact), or reproduced by our execution evidence (OBSERVED_FAILURE with a baseline-pass/patch-fail witness, i.e. OBSERVED_REGRESSION). The block is justified by the corroborating evidence; pyright only supplies the hypothesis and location.
2. **Declared typed boundary**: the maintainer declares (authority DECLARED) that modules X are strictly annotated and the annotations are a trusted boundary. Then tier 1 INTRODUCED errors in X may BLOCK, with scope_validity still CONDITIONAL and the assumption "annotations enforced at callers" printed in the finding. Without annotation coverage this would misfire: on this corpus 10 of the 13 introduced diagnostics were tier 1/2, and all 10 were noise, so it must be opt-in per module and only after the maintainer vouches for annotation quality.
3. Never BLOCK on tiers 2-4.

## 6. Failure modes

1. **Precision on real commits is poor in this sample: 0/13 introduced were real.** Dominant causes: (a) parameter types inferred from defaults (`val=0`), (b) loss of correlated variables (bytes-or-str chosen once), (c) class invariants (`_excluded` is not None when `_included is None`), (d) inter-procedural Optional returns of helpers that the caller guards, (e) environment (missing `six` source), (f) compat shims. The differential keeps volume low (0.14/commit); it does not fix precision.
2. **Recall depends on annotations**: unannotated parameters give Unknown and silence (M09). boltons and dateutil are mostly unannotated.
3. **Scoped runs hide cross-file consequences**: caller selection is by simple name (over-approximate, but misses dynamic dispatch/aliasing/`getattr`). A changed return type affecting an uncaught caller in a file outside the scope would be missed. Whole-package runs catch it but cost up to about 88 CPU-s on dateutil.
4. **Cost**: scoped CPU median 2.8-6.2 s (dateutil max 78.9 s); no persistent cache; each run re-checks the scoped files and re-parses typeshed. Cold start dominates small patches. Not measured: the pyright language server / watch mode.
5. **Version and config sensitivity**: results depend on pyright version (bundled typeshed), `pythonVersion`, `pythonPlatform` (e.g. `WindowsError`, `fractions.gcd`) and strictness mode. These must be in the evidence record and the run-profile digest; upgrading pyright invalidates cross-run diffs (base and patch must always be run with the same pinned version in the same run).
6. **Wrapper network behavior** (update check, node download) and the interpreter-spawn trust issue (section 1).
7. **Identity of diagnostics across the diff** is heuristic (qualname + rule + normalized message). Renamed/moved functions yield spurious INTRODUCED+RESOLVED pairs; M07 showed one extra introduced diagnostic in the same file that is a legitimate inference ripple. Must be marked UNDETERMINED when function matching is ambiguous.
8. **Stdout is JSON but diagnostics have no stable ID**; the adapter must hash (rule, normalized message, function, snippet) itself.

## 7. Recommendation

**GO (conditional)** as an optional adapter named e.g. `external.pyright`:

- Differential, scoped to changed functions and name-matched callers; emit POSSIBLY_UNSAFE / CONDITIONAL-or-OVERAPPROXIMATE / INFERRED / INTRODUCED; default gate WARN; tier 3 as INFO hints; tier 4 as coverage records.
- Make it advisory for the agent repair loop, where a diagnostic with a pointer (file, function, rule, message) is cheap context, and as a hypothesis generator for our dynamic replay (corroboration path 1), which is where its value could be converted into BLOCK-grade evidence.
- Record `pyright` and `node` versions, config digest, interpreter path used for discovery, and the exact `node dist/index.js` command in each evidence item; call node directly, offline.
- Do not let "no pyright findings" contribute to PASS or PROVED_SAFE.

Why not NO-GO: the cost is acceptable, the differential noise is low (7.8% of commits flagged), and detection of the exact bug classes docs/03 cares about (Optional misuse, unbound variables, call arity, bad attributes) is high where type info exists, which our own analyzer decides on 1-2% of obligations. Why not unconditional GO: 0/13 precision on real introduced diagnostics, a recall that collapses on unannotated code, and unmeasured behavior on agent patches. **Next step before committing implementation effort:** run the same harness on a set of agent-generated patches (where the introduced-bug base rate is higher) and re-measure precision with at least 50 labeled introduced diagnostics.

## Files

`common.py` (snapshot export, runner, AST mapping), `measure.py`, `mutate.py`, `show.py`, `body.py` (inspection helpers), `sample_pre.py`, `run_all.sh`, `requirements.lock`, `results_*.json`, `log_*.txt`, `pre_existing_pool.json`, `work/` (exported snapshots, about 113 MB, disposable).
