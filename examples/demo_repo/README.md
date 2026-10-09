# M5 repair-loop demo (docs/13 demonstration sequence)

`run_demo.py` runs the full patch -> verify -> evidence -> repair -> verify loop with the real CLI, the real MCP server and the real local model. It works in a disposable Git repository in a temporary directory, never this one, which is removed afterwards unless `--keep DIR` is given.

```sh
.venv/bin/pip install -e '.[solver,dev,mcp]'
guard runner setup --run-profile isolated-python   # only if python:3.13-slim is not present yet (the one networked step; the script never pulls)
.venv/bin/python examples/demo_repo/run_demo.py --out /tmp/ig-m5-run [--keep /tmp/ig-m5-repo] [--max-attempts 3] [--skip-discovery]
```

Requirements: Docker with `python:3.13-slim`, and Ollama on `127.0.0.1:11434` serving `qwen2.5-coder:7b` at digest `sha256:dae161e2…4364`. The digest is pinned: any other artifact is refused, and nothing is downloaded. If the model is unavailable, the agent step is recorded as NOT_RUN; the verifier steps still run.

## Who the agent is

**The agent client in the recorded loop is the local-model harness in this script, not Claude Code.** The harness:

- starts `guard mcp serve --stdio --root demo=<tmp repo> --allow-profile isolated-python`;
- talks to it with the official MCP Python SDK client (initialize handshake), calling `verify_patch`, `get_contracts`, `get_finding` and `explain_finding`;
- for every function with an ERROR finding, sends the evidence packet and the function's current source to `qwen2.5-coder:7b`, through the M4 loopback transport (`invariantguard.semantic.endpoint`, validated 127.0.0.1 endpoint, temperature 0, JSON-schema-constrained output).

The model must return one whole-function replacement. The harness accepts it only if all of these hold:

- it is exactly one function definition, with the same name and parameters;
- it has no imports, decorators, `global`/`nonlocal`, classes, dunder attributes, or eval/exec/open/getattr-like calls;
- it differs from the current function, and the module still parses.

An accepted replacement is applied only inside the temporary repository. Then `verify_patch` runs again. This repeats for at most 3 rounds, and every round is recorded as it happened, including wrong or rejected repairs. Claude Code can use the same server through the `.mcp.json` documented in `docs/MCP_INTEGRATION.md`, but that path was tested with the SDK client, not with a live Claude Code session.

## Sequence

1. Baseline commit: `ratio` with its zero guard, and `normalize` with `sum(xs)` (docs/13 Fixtures A and B).
2. `guard init`; the maintainer configuration declares:
   - the ratio domain (exact built-in ints);
   - an isolated docker profile `isolated-python`;
   - the pinned local model;
   - the `normalize.sum.v1` contract, through `guard contracts declare --reason ...`, giving authority DECLARED. That is the maintainer path; policy treats DECLARED like APPROVED.

   The configuration and contract are committed, and that commit is BASE_REF.
3. `guard runner probe --run-profile isolated-python` (isolation capabilities from inside the boundary; runs no repository code).
4. `guard baseline create --label demo-base --rev BASE_REF`. Then the candidate patch is applied to the working tree: the unguarded `ratio`, and `sum(xs) + 1.0`.
5. `guard verify --base BASE_REF --format json --execute --run-profile isolated-python`.
6. Confirmations read from that report: `ratio` gives INTRODUCED PROVED_UNSAFE [EXACT_MODEL] with a validated witness, and `normalize.sum.v1` gives an OBSERVED_REGRESSION on the same admitted input (base pass, candidate fail).
7. The agent loop over MCP (above).
8. `guard verify ... --execute` on the final snapshot.
9. A separate no-model static run (`guard check`).
10. Actual local-model contract discovery: `guard contracts discover --model local --execute --dry-run`. Nothing is recorded in the registry. Use `--skip-discovery` to skip it; it takes minutes on the reference host.

## Recorded run

`recorded_run/` holds one complete run, produced by `run_demo.py --out examples/demo_repo/recorded_run`. Every file was written once by the script (exclusive create) and has not been edited. `NN_summary.json` lists the files and the values read from the reports; `run_log.txt` is the timestamped transcript. Numbers and outcomes are discussed in `docs/IMPLEMENTATION_STATUS.md` ("M5 repair loop"). Latency definitions:

- **loop**: from receipt of the first failing `verify_patch` result to receipt of the last one (agent wall clock);
- **verifier**: the server's `verifier_wall_ms` for each `verify_patch`;
- **model**: wall time of each model request;
- **other**: loop minus model, minus verify round trips, minus evidence fetches.

What the recorded run shows (values from `recorded_run/`; discussed in the status file):

- **Candidate:** BLOCK. `ratio` is INTRODUCED PROVED_UNSAFE with witness `{a: 0, b: 0}`. `normalize.sum.v1` shows an OBSERVED_REGRESSION at `xs=[1]`: base `[1.0]`, candidate `[0.5]`.
- **One repair attempt:** the model removed `+ 1.0` and added a zero guard to `ratio`.
- **MCP re-verify:** PASS. The pre-repair findings are then reported STALE_SNAPSHOT with `superseded_by_run`.
- **Final CLI verify:** PASS on the same final snapshot.
- **Loop timing:** 50.2 s in total; the model took 43.5 s and the verifier 6.2 s.

Limits visible in the run:

- The `ratio` repair returns `None` when a == b, where the baseline returned `0.0`. The division obligation is proved safe, but no declared property covers the return value, so the verifier does not object. This is the scope of the declared properties, not a verified equivalence.
- `normalize`'s division sits inside a list comprehension, which is outside the supported static subset, so the whole-snapshot `guard check` is INCOMPLETE (exit 2). The final quick verify is PASS only because the restored `normalize` equals the base and is outside the patch slice; its verdict reasons say so.
- One run on two fixtures with a 7B model demonstrates the workflow; it is not a repair-rate measurement.
