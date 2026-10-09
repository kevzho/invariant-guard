# CrossHair diffbehavior spike: report

Saved by the parent session from the spike agent's hand-back (its subagent Write was refused by harness policy). `python3 analyze.py` regenerates every number. Nothing under `src/` or `tests/` was touched; nothing committed; corpus clones unmodified.

## Recommendation

**NO-GO** as a gate engine or as a source of BLOCK verdicts. **Conditional GO** only as an opt-in, advisory witness generator (conditions in section 9).

| Metric | Result |
|---|---|
| Changed functions run (90 commits, 3 repos) | 118 comparable, plus 16 added/removed that cannot be diffed |
| Ran to a result (no crash or unsupported) | 98/118 at 10 s, 95/118 at 30 s |
| Difference found | 26/118 at both budgets |
| Of those 26, replay-confirmed on both versions | 12 (10% of all changed functions) |
| Of those 26, unreplayable | 11 |
| Of those 26, replay-refuted | 3 |
| Hand-labeled difference-found functions (27, union of both budgets) | 18 intended change, 1 unintended but out of domain, 8 tool artifact, **0 real regressions** |
| Noise vs intentional change | 18/27 (67%) were the commit's intended change; 8/27 (30%) artifacts |
| Seeded regressions found (20 mutants) | 7/20 (35%) at both budgets; 5 replay-confirmed at 10 s, 6 at 30 s |
| Seeded mutants given a false "all paths exhausted, likely the same" | **9/20**, wrong every time |
| Crash or unsupported | 19-22/118 (16-19%) |
| Wall time per function | mean 17.4 s (10 s budget), 51.2 s (30 s budget), incl. container start |

## 1. Setup

**Isolation.** Every dynamic step runs through `docker run` with the same flags as `src/invariantguard/execution/runner.py`: `--network none --read-only`, `--tmpfs /tmp:rw,noexec,nosuid,nodev,size=128m`, source and driver bind-mounted read-only, `--user 65534:65534 --cap-drop ALL --security-opt no-new-privileges`, `--pids-limit 128 --memory 1024m --memory-swap 1024m --cpus 1 --ipc none --log-driver none`, `--pull never`, image by id, env limited to `HOME`, `LANG`, `PYTHONHASHSEED`, `PYTHONDONTWRITEBYTECODE`. `probe_isolation.py` (`isolation_probe.json`) confirms: outbound connect denied, writes to `/case` and `/usr` denied, only `/tmp` writable, uid 65534, only image-baked env vars. The host never imports corpus code (`export_cases.py` uses `ast` and `git archive`; `seeded.py` edits text in exported copies).

**Images.** Network used only at `pip install` during build.
- `ig-crosshair-spike:0.0.111`: `FROM python:3.13-slim` (`sha256:3dd7cc10…db5f`) + `crosshair-tool==0.0.111` (z3-solver 5.1.0.0, Python 3.13.16, aarch64). Id `sha256:3b96a713…37fc`, 345 MB. gcc installed for build then purged. Freeze at `/opt/freeze.txt`.
- `ig-crosshair-spike:0.0.111-six` adds `six==1.17.0`. Id `sha256:736f3ea9…c2a0173`. On the base image 24/28 dateutil functions crashed with `ModuleNotFoundError: six` (doc 10's "environment must be prepared explicitly"). dateutil numbers and seeded runs use `-six`.
- Ids are local image ids, not registry digests; a real adapter needs a content-addressed image or environment lock digest.

## 2. Method and commands

```
docker build -t ig-crosshair-spike:0.0.111 .
docker build -f Dockerfile.six -t ig-crosshair-spike:0.0.111-six .
python3 -I export_cases.py
python3 run_batch.py 10 5
python3 run_batch.py 30 5
IG_IMAGE_ID=$(cat IMAGE_ID_six) IG_TAG=_six IG_REPO=dateutil python3 run_batch.py 10 5   # and 30
python3 seeded.py && python3 run_seeded.py 10 5 && python3 run_seeded.py 30 5
python3 run_check.py
python3 probe_isolation.py
python3 analyze.py
```

**Selection.** Per repo, the 30 most recent non-merge commits touching package `.py` outside tests. Changed function = qualified name on both sides with differing body or signature. 118 comparable functions (more-itertools 44, boltons 46, dateutil 28).

**Deviation from the `crosshair diffbehavior` CLI.** The CLI loads both functions from one `sys.path`, so two versions of one module can't coexist. `driver.py` loads the trees in sequence, purging `sys.modules`, and calls `crosshair.diff_behavior.diff_behavior` with CLI defaults (`per_path_timeout=30`, `TYPE_AND_MESSAGE` exception equivalence, auditwall, pure-python imports). Lazy imports inside a function body resolve against the patch tree.

**Budget** is `per_condition_timeout`, split across two argument orderings; wall time overshoots it.

**Replay.** Witnesses parsed with `ast.literal_eval` only and run on deep copies of both versions. Non-literal reprs (`self`, classes, instances) are unreplayable.

## 3. Results on real changed functions

| Repo (n) | Budget | found | exhausted | budget-out | crashed | unsupported |
|---|---|---|---|---|---|---|
| more-itertools (44) | 10 s | 9 | 8 | 18 | 8 | 1 |
| more-itertools (44) | 30 s | 8 | 8 | 16 | 11 | 1 |
| boltons (46) | 10 s | 15 | 10 | 18 | 3 | 0 |
| boltons (46) | 30 s | 16 | 10 | 17 | 3 | 0 |
| dateutil (28, +six) | 10 s | 2 | 3 | 15 | 8 | 0 |
| dateutil (28, +six) | 30 s | 2 | 3 | 15 | 8 | 0 |
| **All (118)** | 10 s | **26** | 21 | 51 | 19 | 1 |
| **All (118)** | 30 s | **26** | 21 | 48 | 22 | 1 |

A longer budget bought nothing; the found set also shifted (`numeric_range.index` found at 10 s, crashed at 30 s; `SpooledStringIO.write` the reverse). Not monotone in budget, not repeatable.

**Crash causes (30 s):** 8x `cannot pickle 'generator' object` (arg deep-copy); 6x keyword-only-before-variadic signature error; 4x `NotFound: __get_gettz` (name-mangled); 2x `SymbolicBoundedInt.__init__() missing 'typ'`; 1x `CrossHairInternal: Unexpected LazyIntSymbolicStr initializer`; 1x private-method lookup; 1x "cannot determine signature".

### Hand labels (27 functions; justifications in `labels.json`)

| Label | Count | Functions |
|---|---|---|
| INTENDED | 18 | `one`, `only`, `running_max/min/mean`, `SpooledBytesIO.write`, `SpooledStringIO.seek`, `bytes2human`, `BarrelList._translate_index`, `BarrelList.insert`, `Bits.from_hex`, `namedlist`, `namedtuple`, `parse_qsl`, `BarrelList.iter_slice`, `args2cmd`, `args2sh`, `FunctionBuilder.get_invocation_str` |
| UNINTENDED, out of domain | 1 | `combination_with_replacement_index`: element='' with `iterable=0` now raises TypeError instead of returning 0. Replay-confirmed; invalid input. |
| ARTIFACT, replay-refuted | 2 | `running_statistics`, `nth_permutation` |
| ARTIFACT, noise | 3 | `SpooledStringIO.write`, `rebuild` (CrossHair `SideEffectDetected` with random `/tmp` names); `ParserError.__str__` (commit only moves dead code) |
| ARTIFACT, cross-version `self` | 3 | `numeric_range.index`, `BarrelList.pop`, `BarrelList.del_slice` |
| Real regressions | 0 | Expected for maintainer-reviewed history. |

**Good witness.** `bytes2human` ("append infinity bound"): `nbytes=1024**8, ndigits=6`, base `'1024.000000Z'`, patch `'1.000000Y'`. Replay-confirmed; exactly the commit's boundary.

**Right branch, wrong value.** `parse_qsl` (honor `encoding`): witness `encoding=0` (int) → patch TypeError, not `encoding='latin-1'`. Typical on unannotated code: wrong-typed, out-of-domain inputs.

**Cross-version `self`.** For methods the symbolic `self` is a base-class instance (first function's signature used for both orderings). Patch code calling a newly added helper fails (`AttributeError: '_index_near'`), so any commit adding a private helper yields spurious differences for its callers.

## 4. Seeded regressions (20 single mutations, each confirmed to change behavior)

| Result | 10 s | 30 s |
|---|---|---|
| Difference found | 7/20 | 7/20 |
| Replay-confirmed | 5/20 | 6/20 |
| False "exhausted, likely the same" | 9/20 | 9/20 |
| Budget used, nothing found | 4/20 | 4/20 |

**Found:** `bytes2human` off-by-one; `one`/`only` falsy-guard; `only` dropped default (unreplayable); `within_delta` interval and `abs`; `easter` dropped validation (replay-confirmed only at 30 s).

**Missed:** `bytes2human` sign and default; `default_tzinfo` swapped condition (exhausted in 3 s after 6 paths); `easter` year boundary; `Bits.__getitem__`/`__init__` off-by-one; `_ComplementSet.__le__` swapped condition; `args2cmd`; all four generator mutants (`constrained_batches`, `split_before`, `xfrange`, `ichunked`); mutated argument.

Cause of false exhaustion: unannotated parameters are opaque objects; CrossHair explores 2-6 paths in 1-5 s and prints "All paths exhausted, functions are likely the same!" — wrong for 9/9 exhausted mutants.

## 5. `crosshair check`

Corpus has zero PEP 316 conditions, zero icontract/deal, 19 package `assert`s. Default `check` on 7 modules analyzed zero conditions. `--analysis_kind=asserts` gave 1 finding (`recipes.py:1219` type-confused call to a private helper, not a defect). `check` has nothing to work with until our contract workflow produces conditions.

## 6. Proposed evidence mapping

CrossHair is a heuristic bounded symbolic search: treat it as an input generator. Evidence comes only from our own replay of its witness, in the same container, on both versions.

| CrossHair outcome | Our evidence |
|---|---|
| Difference, literal witness replays and differs | `EXECUTED`, `INFERRED`, `CHANGED`, severity INFO/WARN by policy; record both executions, image id, interpreter, lock digest, isolation, witness, budget, seed. Not `OBSERVED_FAILURE`, not a regression. |
| Same, and a DECLARED/APPROVED contract requires base behavior on that input | `OBSERVED_REGRESSION`, `EXECUTED`, `DECLARED`/`APPROVED`, `INTRODUCED`; property evaluated on both runs, baseline pass confirmed, witness passes contract input schema (most here would not). |
| Difference, witness unreplayable | `UNVERIFIED`/`UNKNOWN`, `BOUNDED`, coverage note. Never promote. 11/26. |
| Difference, replay shows none | Drop; count in tool-health stats. 3/26. |
| Replay differs only in exception text, `SideEffectDetected`, addresses, temp names | Normalize/suppress, else `UNKNOWN`. |
| `no_diff_budget` | `UNVERIFIED`, `BOUNDED`, with budget/seed/path count. Never `PROVED_SAFE`. |
| `no_diff_exhausted` | At most `UNVERIFIED/BOUNDED`; never `EXACT_MODEL` (wrong 9/9 on untyped mutants). |
| Crash, unsupported, timeout, container failure | `UNKNOWN` + coverage gap → `INCOMPLETE` if required; engine crash → `ERROR`; never `PASS`. |
| Baseline not importable | `UNDETERMINED`, comparison unknown (doc 10). |

Soundness caveats (from CrossHair docs as recalled, not re-fetched; observations from this run in parentheses): best-effort, no completeness; concretization and C extensions reduce coverage silently; side effects blocked (`SideEffectDetected` noise); depends on annotations (false exhaustion); witnesses are reprs (11/26 unreplayable); run-to-run variance.

**Gating consequence:** a difference is the expected outcome of any behavior-changing commit (67% of findings), so CrossHair alone can never BLOCK; only through a contract independently requiring the old behavior.

## 7. Failure modes

1. False "paths exhausted" on unannotated params (9/9 wrong).
2. Type-confused, out-of-domain witnesses.
3. Cross-version `self` for methods.
4. Generators/iterators: 8 deep-copy crashes, 0/4 generator mutants found.
5. Repr witnesses: methods, classes, objects unreplayable.
6. Tool-internal noise: random `/tmp` names, addresses.
7. Internal crashes/unsupported signatures: 16-19%.
8. Non-repeatable results.
9. Time overshoot: 10 s budget → 17.4 s mean (median 15.8 s); 30 s → 51.2 s (median 44 s). Five 30 s containers showed ~410 s in-driver wall (suspected host/Docker VM stall, unconfirmed).
10. Manual environment preparation (`six` for dateutil, `gcc` for arm64 build).

## 8. Cost

Image 345 MB. Per function: 17.4 s (10 s budget) / 51.2 s (30 s budget), 5 parallel containers on a 10-CPU Docker Desktop VM at 1 CPU each; container start + import ~5-10 s. Corpus: 34 min serial / 6 min 39 s elapsed at 10 s; 101 min serial / 18 min 15 s elapsed at 30 s. Typical patch: 5 functions ≈ 1.5 min serial, 50 ≈ 15 min. Acceptable for opt-in deep checks, not the default fast path.

## 9. Conditions for any adapter

1. Opt-in, container-only, explicit run profile, never in static scans; image pinned by digest; dependencies prepared explicitly.
2. Only fully annotated plain-data functions: no `self`, no generator args. Skip methods until each version's own class builds `self`.
3. CrossHair only generates witnesses; evidence only from our replay harness, only for literal-replayable witnesses that pass the declared input schema.
4. Default mapping `CHANGED`/`EXECUTED`/`INFERRED`; `OBSERVED_REGRESSION` requires a DECLARED/APPROVED contract; never `PROVED_*`.
5. Suppress `SideEffectDetected` text, addresses, temp names before comparing.

Given the static analyzer's 1-2% decided rate, a better next step is reusing the doc 10 probe runner with our own typed-property generators, with CrossHair as one optional witness source rather than a wrapped `diffbehavior` engine.

## 10. Limits

- One run per budget; run-to-run variance only inferred from 10 s vs 30 s differences.
- 10 s/30 s main runs for more-itertools and boltons used the base image.
- Labels by one reviewer from commit messages and diffs.
- Corrected by the parent session (2026-10-09): the agent suspected the corpus was synthetic because of commit dates, but the clones are genuine GitHub clones and every commit predates the machine date (newest 2026-10-07). Intent labels rely on messages and diffs.
- `work/` and `work_seeded/` hold ~130 MB of regenerable exported trees.
- Docker Desktop 29.5.2, 10-CPU, 8 GB VM.

**Main files:** `driver.py`, `run_container.py`, `export_cases.py`, `seeded.py`, `analyze.py`, `labels.json`, `cases.json`, `seeded_cases.json`, `results_T{10,30}[_six].jsonl`, `seeded_T{10,30}.jsonl`, `check_results.json`, `isolation_probe.json`, `Dockerfile`, `Dockerfile.six`.
