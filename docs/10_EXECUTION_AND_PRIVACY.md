# 10. Execution boundary and privacy

## Static mode is the default

Parsing, graph construction, abstract interpretation and solver queries operate on source bytes and trusted analyzer code. They must not import repository modules, run package installation, invoke build scripts, execute Git hooks, or evaluate model-generated expressions.

Compiler-backed adapters require an explicit capability boundary because compiler plugins or build systems can execute code. A Kotlin sidecar should compile configured isolated sources with a trusted version-pinned compiler rather than automatically run arbitrary Gradle builds.

## Dynamic operation contract

A dynamic command requires both an explicit `--execute` request and a named run profile enabled in local configuration. MCP can use only preauthorized profiles; it cannot make a new permission decision or create one.

A profile specifies:
- allowed target modules/functions and typed input templates;
- pinned interpreter/compiler and environment lock digest;
- isolation backend and verified capabilities;
- network policy, writable paths, CPU/memory/time/process limits;
- working-directory mounts and environment-variable allowlist;
- maximum input/output/artifact sizes;
- baseline/candidate environment mapping;
- source-retention and witness-storage settings.

Do not run a target import until the isolation boundary has been established. Importing Python code is already execution.

## Isolation requirement

Preferred runner: a verified container/OS isolation backend with read-only source copies, temporary writable scratch, denied network and no host credentials. A container by itself does not automatically provide every guarantee; probe and report each capability.

If the backend cannot enforce the required profile, return RUNNER_UNAVAILABLE or INCOMPLETE. A timeout-killed subprocess provides resource control but not filesystem/network isolation.

An explicit trusted-repository profile may enable unisolated execution only through `allow_unisolated=true` plus the execution command. Label the evidence with actual capabilities. Never call this execution sandboxed.

The product should not add repeated prompts during an already authorized configured workflow. The configuration is the explicit reusable execution authorization.

## Probe implementation

A trusted runner imports a copied snapshot in the configured environment, invokes allowlisted function targets with typed serialized inputs, captures supported results/exceptions, evaluates predicates and returns structured observations.

Model-produced text cannot select shell commands, imports, environment secrets or mount paths. Validate generated values against the input schema and resource limits.

Use argument arrays for process invocation. Reject path traversal, symlink escapes, unauthorized targets and oversized/cyclic values. Kill the process group on timeout where supported; confirm termination before reusing the environment.

## Baseline comparability

Both sides need suitable pinned dependencies and semantically comparable run profiles. Do not automatically install packages while replaying. Prepare environments through an explicit setup workflow.

If the patch changes dependencies, record both lock files and assess whether a witnessed difference is a intended dependency/API change. Do not silently force the baseline to run with the patch environment. An unavailable historical dependency is UNDETERMINED comparison.

## Data handling

No cloud inference or telemetry by default. Local model requests use only bounded necessary context. Reject non-loopback endpoints and redirects in the local adapter. Verification should work offline after installation.

Reports, exception text, witnesses, source snippets and contract provenance can contain sensitive data. Limit captured output, exclude environment secrets, and support redaction plus configurable witness/source retention. A redacted witness may become nonreplayable; state that honestly.

Raw model context is off by default. Store hashes and minimal source references. Source retention for baseline replay is also off by default; Git revisions may provide baseline source without a second retained copy.

## Repository integrity

Never modify a checked snapshot or working tree. Runner writes only to temporary locations. Do not reset Git, stash user changes, edit dependencies or install hooks without an explicit setup command. Concurrent user edits result in snapshot restart or incomplete comparison.

`guard init` writes only its declared configuration/metadata and prints suggested ignore rules. CI/hook installation is a separate operation.

## Operational acceptance

Test that static scans of a module with a top-level file write do not execute it. Test that dynamic isolation denies outbound network, host writes and credential access under its stated profile. Test cancellation, output limits, malicious candidate nodes and malicious filenames. If platform isolation is unavailable, tests must confirm fail-closed behavior.

This boundary supports the product's local/private claim. It is not a claim to solve arbitrary malicious-program containment on every operating system.
