# Differential-replay spike: results and recommendation

Saved by the parent session from the spike agent's hand-back (its subagent Write was refused by harness policy). Nothing under `src/` or `tests/` was modified; nothing committed; corpus clones read only through Git objects.

Parent-session verification notes (2026-10-09):
- Corpus clones are genuine GitHub clones (origins `mahmoud/boltons`, `dateutil/dateutil`, `more-itertools/more-itertools`); newest commits are 2026-09-23 / 09-26 / 10-07, all before the machine date. The CrossHair report's "dates later than machine date" caveat does not hold.
- The `caaec94` probable regression is corroborated upstream: `dee22c9` (2026-09-06, "Remove special case logic for cwr") reverses that exact behavior.
- Gap 5 confirmed: `runner.py` passes `PYTHONHASHSEED=0` but runs `python -I -S -B`; `-I` ignores `PYTHON*` env vars, and two `PYTHONHASHSEED=0 python3 -I -c "print(hash('abc'))"` runs print different values.

**Recommendation: GO, with conditions.** Make differential replay the main source of dynamic evidence, reported as CHANGED / INFERRED / WARN with an "is this intended?" question to the agent. Not an automatic regression gate.

## 1. Summary

| Question | Result |
| --- | --- |
| Changed functions found | 134 modified or added in 90 commits. Existing impact layer (`lower_snapshot` + `match_symbols`) finds only **53/134**; misses methods, generators, decorated functions (fingerprint ignores the body). |
| Modified functions with ≥1 executable input | **90/118 (76%)**; 75 directly, 15 only via same-module callers or constructor. |
| Inputs generated / executable | 4,041 / 4,034. Hypothesis shape variants of harvested literals 2,712; tests 829 (+52 from patch-added tests); Hypothesis `from_type` on `.pyi` stubs 201; default perturbation 137; doctests 109 (+1). Inline annotations: **0** usable. |
| False differences on correct commits intending no behavior change | **0/27** |
| Noise: correct commits where agent sees ≥1 non-regression WARN | **18/58 (31%)** if every difference WARNs; **9/58 (15.5%)** with message-only → INFO and patch-test-acknowledged → INFO |
| Hand-labeled difference clusters (38, `labels.json`) | 30 intended, 3 intended consequences, 2 harness artifacts, 3 unintended (1 probable real regression; 2 = one cosmetic lost error message seen through two symbols) |
| Real unintended change in upstream history | more-itertools `caaec94`: `random_ordered_combs_with_replacement([], r>0)` yields `[()]`, contradicting `combinations_with_replacement`. Found by a Hypothesis input from the `.pyi` stub. |
| Seeded regressions (20) | Diff replay **18/20**; repo tests in same container 15/20; union 19/20; **4 caught only by diff replay** (M04, M08, B03, B04). Median 14 s/mutant. |
| Recall on real intended behavior changes | 18/46 commits (18/36 with an executable input) |
| Nondeterminism | 13 probes in 8 functions caught by running base twice; none leaked. 341/341 differing probes reproduced in fresh-container confirmation. 233 private-`_attr`-only probes suppressed. |
| Cost | ~20 ms CPU/probe; median 15 s/commit (p90 32 s, max 70 s); 1,340 s for 90 commits (prepare 519 / generate 367 / replay 388 / confirm 53). Shared host (load 90–160), so pessimistic. |

Main problem is intent, not artifacts: 33/38 differences on correct commits are what the author wanted. Output must say "changed: was this intended?" with a cheap, auditable "yes".

## 2. What was built

Pipeline: immutable snapshots via `capture_git_revision` + `diff_files` → AST change detector (`changes.py`) incl. same-module callers → host-side AST harvester (`harvest.py`, no imports) → in-container Hypothesis generation against patch snapshot returning encoded values (byte-identical inputs both sides) → same probes run base ×2, patch ×1 → classify (public view only) → re-run every difference in fresh containers (base ×1, patch ×2) → cluster into agent findings.

| File | Role |
| --- | --- |
| `Dockerfile`, `setup_image.sh` | Explicit setup (only network use). `python:3.13-slim@sha256:3dd7cc…`, `hypothesis==6.165.0`, `six==1.17.0`, `attrs`, `sortedcontainers`; later `pytest==9.1.1`, `freezegun==1.5.5`. |
| `drrunner.py` | Subclasses existing `DockerBackend`, reuses `Workspace`; capability probes still use trusted `probe_harness.py`. |
| `dr_harness.py` | In-container `generate`/`replay`; import zygote + forked probe chunks; per-probe timer with hard kill; extended encoding; argument/receiver mutation capture. |
| `dr_pytest.py` | Repo's own pytest selection inside the same boundary. |
| `changes.py`, `harvest.py`, `replay.py` | Change detection; input harvesting (parametrize, local assignment inlining, class and bound-method aliases, receivers, dunders via operator syntax, default perturbation); probe building/classify/confirm/aggregate. |
| `run_corpus.py`, `survey_changes.py`, `seeded.py`, `seeded_tests.py`, `gap_checks.py` | Experiments. |
| `analyze.py`, `noise.py`, `commit_judgements.py`, `findings.py`, `labels.json`, `mutants.json` | Metrics, labels, mutants, agent output. |

Reported difference classes: different return, different mutation, new/removed exception, different exception type, message-only, one-sided timeout. Never reported: same, nondeterministic, internal-state-only, unconfirmed.

## 3. Commands (repo root, project venv CPython 3.13.9, Docker 29.5.2)

```sh
spikes/diffreplay/setup_image.sh
(cd spikes/diffreplay && ../../.venv/bin/python survey_changes.py)
(cd spikes/diffreplay && for r in more-itertools boltons dateutil; do ../../.venv/bin/python run_corpus.py $r 30; done)
(cd spikes/diffreplay && ../../.venv/bin/python analyze.py)
(cd spikes/diffreplay && ../../.venv/bin/python noise.py)
(cd spikes/diffreplay && ../../.venv/bin/python commit_judgements.py)
(cd spikes/diffreplay && ../../.venv/bin/python seeded.py)
(cd spikes/diffreplay && ../../.venv/bin/python seeded_tests.py)
(cd spikes/diffreplay && ../../.venv/bin/python gap_checks.py)
(cd spikes/diffreplay && ../../.venv/bin/python findings.py more-itertools caaec94)
```

Commit selection: `git log --no-merges -n 30 -- <pkg>/*.py`, first parent as base. Images: corpus/seeded replays on `sha256:6960a70e1922…`; `seeded_tests.py` on `sha256:bff8706cdb5e…`.

## 4. Reused vs. missing in the current codebase

Reused unchanged: `GitRepo`, `capture_git_revision`, `diff_files`, `ast_fingerprint`, `DockerBackend` (all isolation flags, capability probing, `--pull never`, wall limit), `Workspace`, `process.run_argv`, `RunProfileConfig`, `lower_snapshot`/`snapshot_keys`/`match_symbols`.

Gaps (each worked around in the spike; each needs a product change):
1. Probe harness only calls plain module-level functions; methods, decorated functions, `*args` rejected. 74/134 changed functions are methods.
2. Value encoding makes generators, iterators, datetimes, sets, objects all `opaque`, so every more-itertools and dateutil change would compare "same".
3. Inputs limited to JSON built-ins; >half of harvested args are lambdas, `count()`, `iter(...)`, `BytesIO(...)`, `range(...)`.
4. `-S` hides image site-packages (`dateutil.parser` → `No module named 'six'`); no target dependency environment concept.
5. `PYTHONHASHSEED=0` is ineffective under `-I`; runner's determinism claim is false (affects existing repeatability contracts).
6. Harness name and protocol hard-coded (`probe_harness.py`, `ig-probe-1`).
7. Batch output all-or-nothing over `max_output_bytes` (hit boltons `e00c10a8`); spike caps per observation at 64 KB with digest replacement.
8. Change detection misses 81/134: `UnsupportedSymbol.fingerprint` hashes kind, qualname, reason and division nodes, not the body; validity keys for `ichunked`, `Bits.__getitem__`, `_parse_isotime` don't change — also a staleness risk for the static incremental cache.
9. Snapshots capture only `.py`; `.pyi` stubs and data files invisible. `GitRepo` has no log API.
10. Changes through module-level data missed (`bytes2human` via a constant table); `changed_globals` not mapped to reader functions.
11. `shrink_candidates` handles only four numeric input profiles.
12. No base-vs-patch compare mode: no equivalence property, no `OBSERVED_DIFFERENCE`, no `behavior_change` finding kind.

## 5. Measurements

### 5.1 Coverage

| Repo | Commits | With replayed fn | Modified fns | Executable (any / direct) | Inputs | Wall (sum / median) |
| --- | --- | --- | --- | --- | --- | --- |
| more-itertools | 30 | 27 | 44 | 41 / 37 | 1,776 | 636 s / 18 s |
| boltons | 30 | 25 | 46 | 34 / 29 | 1,175 | 354 s / 11 s |
| dateutil | 30 | 22 | 28 | 15 / 9 | 1,090 | 349 s / 15 s |
| **total** | 90 | 74 | 118 | **90 / 75** | 4,041 | 1,340 s / 15 s |

Doc-only control: 15 doc-only functions replayed; only `bytes2human` differed (real change via module data). No-input causes: private helpers with no harvestable callers, test-defined objects, starred loop args, factory closures, tooling code.

### 5.2 Labels and noise (selected; all 38 in `labels.json`)

| # | Commit, function | Witness: base → patch | Origin | Label |
| --- | --- | --- | --- | --- |
| 0 | mi f89d7a3 `ichunked` | `([1,2,3], 0)`: lazy ValueError → `[]` | test | INTENDED |
| 3 | mi caaec94 `random_ordered_combs_with_replacement` | `({}, 591764)`: `[]` → `[()]` | stub | **UNINTENDED, probable regression** |
| 4 | mi dee22c9, same | `[()]` → `[]` | stub | INTENDED |
| 5 | mi ce676d2 `split_before` | `([], pred, maxsplit=0)`: `[[]]` → `[]` | defaults | INTENDED |
| 7 | boltons 4e5faa3 `SpooledBytesIO.write` | `None` → `7` | test | INTENDED |
| 12 | boltons 1216b46 `reverse_iter_lines` | UnicodeDecodeError → LookupError | shape | INTENDED_CONSEQUENCE |
| 13 | boltons 1216b46 `bytes2human` | `1024**8`: `'1024Z'` → `'1Y'` | patch doctest | INTENDED |
| 16 | boltons 5eb6cc0 `Bits.__getitem__` | `Bits('10')[-3]`: `False` → IndexError | patch test, shape | INTENDED |
| 17 | boltons 970051b `namedtuple` | `('', '')`: IndexError → ValueError | shape | INTENDED |
| 23 | boltons d45cc41 `parse_qsl` | returned → LookupError | shape | INTENDED_CONSEQUENCE |
| 26 | boltons c1c25da `Bits` ctor | `Bits(4, 2)`: object → ValueError | patch test | INTENDED |
| 27 | boltons 3e9983d `xfrange` | `(1, 5, step=-1)`: infinite → `[]` | patch test | INTENDED |
| 30/31 | boltons e00c10a `BarrelList.pop` | message `'pop from empty list'` → `''` | test, shape | UNINTENDED (cosmetic) |
| 32/33 | boltons e00c10a `IndexedSet` updates | same contents; internal tombstones | patch test | **ARTIFACT** |
| 35 | dateutil 34906dc `_parse_isotime` via `isoparse` | datetime → ValueError | test | INTENDED |
| 37 | dateutil 79d2e48 `parser.parse` via `parse` | TypeError → ParserError | patch test | INTENDED |

5/38 clusters seen only via a caller or constructor; 11 only by generated inputs, 11 only by harvested, 16 by both.

| Policy | WARN clusters | Correct commits with ≥1 WARN (N=58) | Non-regression share | caaec94 still WARN? |
| --- | --- | --- | --- | --- |
| P0: every difference WARN | 38 | **18 (31.0%)** | 37/38 | yes |
| P1: message-only INFO; ack if all witnesses from patch-added tests | 26 | 14 (24.1%) | 25/26 | yes |
| P2: message-only INFO; ack if any witness from patch-added tests/doctests | 14 | **9 (15.5%)** | 13/14 | yes |

18/33 intended clusters have a witness from a test or doctest the patch added: the cheapest automatic intent signal. Noise fixed in-spike: private-state comparison, concrete iterator type, load-induced one-sided timeouts (now fresh-container confirmation).

### 5.3 Seeded regressions

| Category | Diff replay | Repo tests |
| --- | --- | --- |
| off-by-one | 5/6 | 5/6 |
| dropped edge case | 4/5 | 3/5 |
| changed exception type | 3/3 | 3/3 |
| wrong default | 3/3 | 2/3 |
| swapped condition | 2/2 | 2/2 |
| mutated input | 1/1 | 0/1 |
| **total** | **18/20** | **15/20** (union 19/20) |

Only diff replay: M04, M08, B03, B04. Only tests: M02. Neither: M09 (no `numeric_range` receiver harvestable). Flaky `TestConcurrentTee::test_concurrent_consumers` excluded.

### 5.4 Recall on real intended changes

18/46 behavior-changing commits. Misses: stateful call sequences (bucket, seekable, BarrelList); test-defined objects as inputs; change masked by nondeterministic output; aware datetimes never generated; private methods without harvestable callers.

## 6. Evidence and policy mapping (docs/03)

| Axis | Confirmed difference |
| --- | --- |
| property_id | `behavior.base_equivalence` (implicit, per symbol) |
| property_result | **`OBSERVED_DIFFERENCE`** (proposed); `OBSERVED_REGRESSION` only with a declared/approved property the base satisfies and patch violates; same outcomes → `TESTED` |
| scope_validity | `EXECUTED` |
| authority | `INFERRED` |
| patch_relation | `CHANGED` |
| severity | WARNING; INFO for message-only and patch-test-acknowledged |
| verdict | WARN at most, never BLOCK |
| coverage | "each changed public symbol has ≥1 executable input"; unmet → **INCOMPLETE** |

Nondeterministic → UNKNOWN. Only fresh-run-reproduced differences reported.

Raising authority: (1) unchanged doctest whose output no longer matches → DECLARED → OBSERVED_REGRESSION → BLOCK; patch-edited example → CHANGED/INFO. (2) unchanged test assertions DECLARED where they assert; patch-added/edited test (by enclosing test function, not input text) → INFO. (3) `guard behavior pin SYMBOL --input W` → APPROVED DSL contract; later differences BLOCK.

Intent: `guard behavior accept FINDING_ID --reason TEXT`; over MCP agents only propose, maintainers approve. Record: symbol, both snapshot IDs and semantic digests, encoded witness and origin, both outcomes, difference class, reason, approver. Matching later runs → INFO; new witness or outcome → WARN again. Evidence never deleted.

Agent-facing finding (real samples `results/findings_caaec94.json`, `results/findings_d45cc41.json`):

```json
{"finding_id":"BD-e09612061aac","kind":"behavior_change",
 "changed_symbol":"more_itertools/more.py::random_ordered_combs_with_replacement",
 "observed_through":{"target":"…::random_ordered_combs_with_replacement","relation":"direct"},
 "property_id":"behavior.base_equivalence","property_result":"OBSERVED_DIFFERENCE","scope_validity":"EXECUTED",
 "authority":"INFERRED","patch_relation":"CHANGED","severity":"WARNING","difference_class":"different_return",
 "witness":{"call":"random_ordered_combs_with_replacement({}, 591764)","input_origin":"stub",
            "base":"returned <iter generator: []>","patch":"returned <iter generator: [()]>"},
 "cluster":{"examples_in_cluster":2,"input_origins":["stub"]},
 "question":"Your patch changed the behavior of random_ordered_combs_with_replacement on (...): base returned []; patch returned [()]. Was this intended?",
 "if_intended":"say so in the patch rationale and record it: guard behavior accept BD-e09612061aac --reason TEXT",
 "if_not_intended":"fix the patch; re-run verify_patch to replay the same witness",
 "acknowledged_by_patch_tests":false}
```

## 7. Design changes (dependency order)

1. **Fix change detection first** (blocks everything): full-body fingerprint for every symbol incl. unsupported; separate doc-only flag; `.pyi` and module-data dependencies (`changed_globals` → readers).
2. **Runner/harness v2 (`ig-probe-2`)**: methods/constructors/`*args` with receiver construction; extended encoding (iterator prefix + lazy exceptions, datetimes, sets, protocol views, digests); repository-authored expression inputs evaluated only in-container; import zygote + forked chunks + per-probe timer; per-observation cap; declared dependency environment in env digest; honest hash-seed handling; versioned protocol, pluggable harness.
3. **Observable-equivalence policy**: drop `_private` state and iterator concrete types; compare objects via protocol; message-only → INFO.
4. **Input generation module**: harvesting + default perturbation; derandomized in-container Hypothesis with recorded seeds (shape variants, `from_type` on stubs/annotations); **call sequences on one receiver** (main recall gap).
5. **Impact through callers**: same-module then cross-module public callers.
6. **Evidence model**: `OBSERVED_DIFFERENCE`, `behavior_change` kind, implicit `behavior.base_equivalence`; fresh-run confirmation; clustering + generic shrinker; INCOMPLETE coverage rule.
7. **Intent workflow**: `guard behavior accept` / `pin`; auto-INFO for patch-added-test witnesses; DECLARED doctests by running them.
8. **Keep the static engine as second signal** (only source of proofs; witnesses can confirm or rule out its POSSIBLY_UNSAFE).

## 8. Threats to validity

Single labeler (justifications in `labels.json`); clusters from ≤6 stored examples per function/class, so counts are lower bounds; mutant selection bias (real-change recall 18/46 is the honest figure); shared-host wall times; harness evolved during the spike (numbers from final code); repository-authored test/doctest expressions execute in-container and need the same `--execute` + run-profile authorization as any dynamic check.

Key files: `labels.json`, `results/metrics.json`, `results/noise.json`, `results/commit_recall.json`, `results/seeded_vs_tests.json`, `results/diff_cases.md`, `results/gap_checks.json`, `results/findings_caaec94.json`.
