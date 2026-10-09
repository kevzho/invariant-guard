# 07. Incremental analysis and baselines

## Snapshot contract

A snapshot is a content-addressed source manifest with repository identity, file paths/digests, relevant configuration, analysis profiles and dependency lock digests. Results always point to two immutable identities: base and candidate. A commit hash alone does not capture uncommitted candidate changes.

Read the file bytes once and analyze those copies. Before finalizing, confirm source manifest consistency; if files changed mid-capture, restart once or return INCOMPLETE. Never combine symbols read from different working-tree moments.

## Git and non-Git operation

`guard check` accepts a directory without Git. Patch verification requires an explicit saved baseline or Git reference. Never infer a base from an unstated merge base.

For Git-backed repos include tracked modifications, staged and unstaged changes, untracked supported files, deletions and renames. Snapshot lookup reads objects without changing the working tree. Use Git command argument arrays; never build shell strings from filenames or references.

A non-Git repository can use `guard baseline create --label NAME`. Store a source manifest plus explicit replay-source retention only when configured. Without retained source or an accessible revision, dynamic baseline replay may be unavailable and must say so.

Exclude .git, .guard, virtual environments, caches, vendor directories and configured paths. Avoid following symlinks outside the root. Report excluded changed files when they affect requested scope.

## Graph model

Nodes: modules, symbols, globals, types, contracts and library models. Edges: calls, imports, references, writes, type use, contract dependencies and dynamic-resolution uncertainty. Store base and candidate graphs; removals can matter as much as additions.

Seed the affected slice with changed/deleted/new symbols, relevant module-level initializers, configuration/lock changes and approved contract revisions. Walk dependency edges in both versions. A changed callee can invalidate callers even when their text is unchanged.

Direct import graphs alone are insufficient. Effects on shared state and unresolved dynamic edges require conservative widening. Include affected strongly connected components for recursion. When exact slicing is impossible, reanalyze the whole containing module or configured repository scope.

## Soundness versus speed

A smaller slice is useful only if dependencies are not missed. The first implementation should prefer too much reanalysis over unsound reuse. Compare incremental obligations with a full run on semantic fixtures as a release test. If a widening exceeds the quick budget, report INCOMPLETE and offer a full mode.

Global configuration, environment locks, compiler flags, modelled library versions or semantics-profile changes can invalidate all summaries. Do not reuse an old proof because the function body did not change.

## Cache key

Use a digest of normalized relevant IR, raw source snapshot references, transitive summary dependencies, contract revision, semantic profile, analyzer/solver version, relevant configuration and environment identity. Analysis modes and bounds are part of the key.

Separate parse, graph, summary, solver and probe caches. A successful parse is not a passing verification cache entry. Reuse only completed evidence whose assumptions and coverage remain valid. Interrupted or failed entries do not count.

Do not put source line numbers in semantic identities. Remap current spans after reuse while keeping historical evidence spans tied to the original snapshot.

## Matching before and after

Match symbols by qualified name/module plus signatures and conservative similarity for renames. Match obligations by property kind, operation and structural anchor. If a match is uncertain, keep both findings and mark the relation UNDETERMINED.

For a suspected introduced failure, evaluate the candidate witness in the base model as well as the candidate model. For execution evidence, replay it in both snapshots. Report newly unsafe caller paths even when a helper already had a potential failure.

## Baseline lifecycle

A baseline is an explicit immutable revision, not the last scan result. A passing scan cannot automatically replace it. Creating or updating a baseline records the chosen revision, reason and contract set. The agent integration should have read/check access by default; changing approved baselines/contracts is a separate explicit command.

Keep baseline probes and learned candidates with their original domains. Do not train discovery on the candidate and call the candidate behavior a baseline.

## Required change cases

Function body, signature, shared constant, library-version lock, module initializer, new file, removed file, rename, direct caller/callee, unresolved callback, recursion, contract revision, changed analyzer version and mid-run edits.

Fixture acceptance: changing a divisor returned by a callee must invalidate an unchanged caller. Changing a global limit must recheck a dependent index. Changing only a comment may reuse semantic analysis after span remapping, but docstring changes can affect candidate discovery.
