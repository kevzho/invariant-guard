# 15. Ordered implementation backlog

Use this file as the implementation checklist. Check an item only after its completion evidence exists in IMPLEMENTATION_STATUS.md. Dependencies follow the milestone sequence.

## M0: foundation

- [x] T00 Create pyproject, src layout, provisional guard entrypoint and local test setup. Done: clean local install and CLI help.
- [x] T01 Create versioned report/evidence/contract/snapshot/IR schema models. Done: valid and invalid payload tests; assumptions/coverage required.
- [x] T02 Implement source spans and content digests. Done: Unicode-column and exclusive-end cases correct.
- [x] T03 Create fixture manifests and IMPLEMENTATION_STATUS.md. Done: each fixture identifies domain, expected evidence and ground truth.
- [x] T04 Create decision/update convention and dependency lock. Done: tested host version and selected dependencies recorded.

## M1: Python static slice

- [x] T10 Parse real Python AST without imports. Done: top-level side-effect sentinel remains untouched.
- [x] T11 Resolve supported local symbols and lower typed minimal CFG/IR. Done: branch/source mappings validated.
- [x] T12 Implement integer constant/interval/nonzero states. Done: joins, bottom and safe/unsafe boundary tests.
- [x] T13 Implement division obligations and domain assumption tracking. Done: ratio safe/unsafe/annotation-only cases distinct.
- [x] T14 Add optional typed Z3 adapter, budgets and exact witness validation. Done: SAT/UNSAT/UNKNOWN/unavailable cases accurate.
- [x] T15 Implement unsupported effects/coverage. Done: external-transform case is UNKNOWN and required incomplete.
- [x] T16 Implement guard check JSON/human output and exit policy. Done: stdout schema valid, stderr logs separate.
- [x] T17 Pass M1 acceptance. Done: real source -> real findings; no mocked solver or hard-coded demonstration outputs.

## M2: change-aware verification

- [x] T20 Capture immutable base/candidate snapshots, Git and non-Git. Done: staged/unstaged/untracked/deleted paths included.
- [x] T21 Map diffs to symbols and conservative before/after graph. Done: renames/deletions unresolved cases disclosed.
- [x] T22 Implement direct call summaries and reverse invalidation. Done: changed-callee fixture catches unchanged caller.
- [x] T23 Track global/type/config/lock/contract dependencies. Done: dependent summary invalidates. (Global/type/config/lock: tested in M2. Contract dependencies: tested in M3 by `test_contract_revision_invalidates_only_the_target_summary`; each symbol's key carries the digest of its active contract revisions.)
- [x] T24 Implement cache validity and interruption handling. Done: stale or partial evidence never reused as pass.
- [x] T25 Implement list-bounds and narrow None-flow checks. Done: negative Python indexes, empty list and unknown aliases tested.
- [x] T26 Classify introduced/pre-existing/resolved/changed findings. Done: same witness checked against both models.
- [x] T27 Implement verify quick/full, coverage and baseline lifecycle. Done: no Git checkout or silent baseline update.
- [x] T28 Compare incremental/full fixtures and measure cold/warm. Done: no required-obligation mismatch; actual timing recorded.

## M3: behavioral contracts

- [x] T30 Implement typed DSL validation/evaluator and bounded input profiles. Done: invalid, vacuous and malicious expressions rejected.
- [x] T31 Implement contract lifecycle, authority and immutable revisions. Done: approval separate from tested/proved result.
- [x] T32 Add deterministic candidate templates and provenance. Done: sources and assumptions retained.
- [x] T33 Implement run profiles and capability-checked isolation. Done: disabled/static default and fail-closed backend behavior.
- [x] T34 Implement isolated allowlisted import/invocation. Done: resource/network/host-write tests match claimed capabilities.
- [x] T35 Add differential replay and shrinking. Done: positive-sum normalize witness passes base and fails patch.
- [x] T36 Add supported mutation/repeatability templates. Done: observations remain finite; domain-preserving shrink.
- [x] T37 Implement evidence store and contract gate policy. Done: inferred change warns; approved replayed regression blocks.
- [x] T38 Validate stale evidence and intentional revision path. Done: new predicate/domain cannot inherit old passing evidence.

## M4: local semantic discovery

- [x] T40 Implement ContextPacket and LocalReasoner protocol. Done: bounded source selection and candidate schema.
- [x] T41 Implement validated loopback adapter. Done: redirect/non-loopback destinations rejected; no cloud fallback.
- [x] T42 Integrate one real open-weight artifact. Done: revision/license/runtime/hardware recorded and actual inference demonstrated.
- [x] T43 Validate/refute candidate properties mechanically. Done: model output cannot produce proof/approval on its own.
- [x] T44 Evaluate templates, no-model and local-model candidates. Done: checkability/usefulness/time measured on held-out examples. (One model only: a second needs an unauthorized download. Usefulness is measured mechanically, via seeded-bug exposure and intended-behavior validity; developer intent review is not done. See IMPLEMENTATION_STATUS "M4 measurements".)
- [x] T45 Test offline/failure/context-retention behavior. Done: deterministic path survives model absence.

## M5: agent integration

- [x] T50 Implement stdio MCP over shared orchestration API. Done: protocol output/log separation and schema compatibility. (`guard mcp serve --stdio`, mcp==2.3.0; see IMPLEMENTATION_STATUS "M5".)
- [x] T51 Restrict repository roots, tools and execution profiles. Done: default tools cannot approve contracts/baselines.
- [x] T52 Implement cancellation and evidence fetching. Done: cancelled/stale runs cannot become passing cache entries. (Safe points are between stages and symbols; there is none inside one snapshot's lowering or one function's analysis.)
- [x] T53 Configure one real agent client. Done: exact tested integration instructions. (Claude Code project `.mcp.json` in docs/MCP_INTEGRATION.md; the test launches that exact command with the SDK client. A live Claude Code session was not tested.)
- [x] T54 Record complete repair loop. Done: actual unsafe patch, generated evidence, real repair and final snapshot report. (examples/demo_repo/recorded_run/; the agent client is the local-model harness over MCP, not Claude Code.)
- [x] T55 Assemble alpha release checklist. Done: M0-M5 gates, accurate limits, offline and model/no-model paths. (docs/ALPHA_CHECKLIST.md; it records that two alpha release criteria are PARTIAL.)

## M6: Kotlin

- [ ] T60 Spike typed compiler integration. Done: written decision on pinned Kotlin/JVM adapter boundary. (DEFERRED: the written feasibility decision is DECISIONS I-101, but the spike itself could not run because the host has no JDK or Kotlin toolchain and a download was not authorized. Left unchecked.)
- [ ] T61 Implement versioned sidecar/IR exchange. Done: unsupported/version mismatch explicit.
- [ ] T62 Add integer-division, array-bounds and null-assertion rules. Done: semantics match isolated compiled fixtures.
- [ ] T63 Reuse impact/report/policy interfaces. Done: no parallel incompatible finding format.
- [ ] T64 Validate overflow, platform types and compiler settings. Done: assumptions and invalidation correct.
- [ ] T65 Pass beta gates. Done: shared core works across Python and Kotlin with disclosed coverage.

## M7: expansion and evaluation

- [ ] T70 Prioritize TypeScript against user demand. Done: documented adapter subset and Number/BigInt/undefined tests.
- [ ] T71 Add selected versioned tensor/DataFrame/schema models. Done: real trusted library summaries and boundary fixtures.
- [ ] T72 Build labeled held-out patch benchmark. Done: safe negatives, independent ground truth and licensed inputs.
- [x] T73 Run ablations and publish measured internal report. Done: actual precision/recall/abstention/latency with denominators.
- [ ] T74 Review public naming/licenses/install path. Done: no unverified branding or model redistribution rights.
- [ ] T75 Consider more languages and research features. Done: evidence-based priorities; no unsupported feature claims.

## First task to perform

T00 through T04, then T10. Keep the first iteration small enough to inspect. The first useful checkpoint is unsafe/safe/unknown ratio analysis, before every other promised feature.
