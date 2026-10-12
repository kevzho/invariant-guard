# InvariantGuard

> **Status: alpha / research preview (engine 0.5.0).** Python only. Interfaces, report schemas and storage formats may change without notice. Nothing has been released to PyPI. Read [What it does not do](#what-it-does-not-do-yet) before relying on a verdict.

InvariantGuard is a local-first verifier for patches written by coding agents. It checks only what a patch can affect, and it returns machine-readable evidence that an agent can use to repair the patch:

- **Modeled runtime-failure analysis.** It lowers Python source (never importing it) to a small typed IR. Interval and path reasoning, plus an optional Z3 query, then decides whether operations such as division, indexing or attribute access on `None` can fail. Each concrete witness is re-validated before it is reported.
- **Behavioral contracts.** It checks repository-specific properties written in a typed DSL. They run in an isolated Docker probe runner, where the same witness runs against the base and the patch.
- **Differential replay.** It replays inputs harvested from tests and doctests (plus Hypothesis-generated inputs) on the base and the patch. Any difference is reported as a question ("was this intended?"). Maintainers can record the answer with `guard behavior accept|pin`.
- **Agent interface.** `guard mcp serve --stdio` exposes eight MCP tools over the same engine as the CLI.

Proof status, execution evidence, coverage, authority and severity are kept separate. `PROVED_SAFE` holds only for the declared property, the modeled semantics and the stated assumptions. Passing probes count as `TESTED`, not as proof. Missing required coverage makes a run `INCOMPLETE`, never `PASS`. Model output is never evidence. See [docs/03_EVIDENCE_AND_POLICY.md](docs/03_EVIDENCE_AND_POLICY.md).

## Example

```python
# examples/python_demo/candidate/ratio.py
def ratio(a: int, b: int):
    d = a - b
    return 100 / d
```

```console
$ guard check examples/python_demo/candidate
InvariantGuard check  verdict=BLOCK  run_outcome=COMPLETE  policy=default-v1
coverage: 1/1 symbols analyzed, 1/1 required obligations resolved

candidate/ratio.py:3:12  PROVED_UNSAFE [EXACT_MODEL]  `100 / d`  (ratio, division.zero_denominator, SMT)
    witness: {'a': 0, 'b': 0}
    path: d == a - b AND d == 0
    domain: a, b: exact Python built-in integers
    assumes: domain explicitly admitted in configuration: demo public API admits exact built-in integer inputs
    why: SMT: SAT; witness {'a': 0, 'b': 0} re-validated by the IR evaluator: ZeroDivisionError at this operation
```

Add `if d == 0: return 0.0` and the same obligation becomes `PROVED_SAFE [EXACT_MODEL]`. On a larger tree, the text report lists every proved or possible failure and groups unresolved (`UNKNOWN`) obligations by cause with examples; `--verbose` lists each one with its full reason. Use `--format json` for the stable, schema-validated report.

## Install

Requires Python 3.11+. The core has no runtime dependencies.

```sh
git clone https://github.com/kevzho/invariant-guard && cd invariant-guard
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e . --no-deps   # pinned, as tested
# or, minimal:  .venv/bin/pip install -e '.[solver]'
```

Optional extras: `solver` (z3), `mcp` (the MCP server), `dev` (tests and report self-validation). Dynamic checks (contracts, replay) need a local Docker daemon. Local contract proposals need a separately installed [Ollama](https://ollama.com) model; `guard` never downloads models.

## Commands

| Command | What it does | Executes repository code? |
| --- | --- | --- |
| `guard check PATH` | Static analysis of one snapshot | No |
| `guard verify --base REF` | Change-aware static verification against a Git ref or a saved baseline, including unchanged callers of changed callees | No |
| `guard verify --base REF --execute --run-profile NAME [--replay]` | Adds contract probes and differential replay in an isolated runner | Yes, in Docker (`--network none`) |
| `guard contracts list\|show\|declare\|approve\|revise\|retire\|discover` | Contract lifecycle and authority; `discover --model local` uses a loopback model | Only with `--execute` |
| `guard behavior accept\|pin\|retire\|list` | Maintainer decisions about replayed behavior changes (CLI only) | No |
| `guard baseline create\|list\|show` | Explicit, immutable baselines | No |
| `guard mcp serve --stdio --root NAME=PATH` | MCP server for coding agents ([docs/MCP_INTEGRATION.md](docs/MCP_INTEGRATION.md)) | Only through a preauthorized Docker profile |
| `guard explain FINDING_ID`, `guard capabilities` | Evidence retrieval and the configured capabilities | No |

Exit codes: `0` PASS, `1` BLOCK, `2` INCOMPLETE/UNKNOWN, `3` ENGINE ERROR, `4` usage error.

Static commands never import the repository, install dependencies or run its hooks or tests. InvariantGuard never modifies the repository it checks.

## Demos

```sh
.venv/bin/python examples/patch_demo/run_demo.py                          # guard verify on a throwaway Git repo
.venv/bin/python examples/contract_demo/run_demo.py --setup               # contracts in Docker (pulls python:3.13-slim explicitly)
.venv/bin/python examples/contract_demo/run_demo.py --backend unisolated  # same, explicitly unisolated (no Docker)
.venv/bin/python examples/demo_repo/run_demo.py --out /tmp/ig-run         # MCP repair loop (local model + Docker)
```

`examples/demo_repo/recorded_run/` holds a recorded repair loop: BLOCK, then one model repair, then PASS.

## What it does not do (yet)

- **Most real-world Python is outside the modeled subset.** Closures, most attribute access, calls to methods, decorated methods, loops, `try`, generators and unmodeled library types give `UNKNOWN`, so a whole-repository `guard check` usually ends `INCOMPLETE`. `guard verify` limits required work to what a patch touches, and `--replay` limits it to changed public symbols. Both reduce this but do not remove it. The exact subset is in [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) ("Supported Python subset", "Known gaps").
- **No enforcement.** MCP is advisory. There is no shipped pre-commit hook or CI gate yet.
- **No effectiveness claims.** The benchmarks are author-written and seeded, not a held-out set of real agent patches.
- **Python only.** The Kotlin adapter (M6) is deferred.
- **A subprocess is not a sandbox.** The Docker capability checks describe what the profile claims as observed from inside the container. They are not a claim of containment against kernel exploits.

## Optional: remote contract proposers

Contract *proposals* can come from a bring-your-own-key remote model. This is opt-in and never a fallback. Proposals are recorded as `INFERRED` at most and go through the same validation and falsification as any other candidate. In `.guard/config.toml`:

```toml
[semantic]
enabled = true
provider = "anthropic"                 # or "openai_compatible" with base_url = "https://HOST/v1"
model_id = "claude-sonnet-5-5"
api_key_env = "ANTHROPIC_API_KEY"      # the NAME of the variable; the key is never written to config, logs or reports
remote_allowed = true                  # required before any non-loopback destination is contacted
```

**Privacy warning:** with a remote provider, bounded context packets (target function, selected callers, callees and tests) leave the machine. Use the local model or agent proposals (`guard contracts context` / `guard contracts propose`) when code must not leave the machine. Verification itself never uses the network.

## Documentation

- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md): exactly what exists, the commands that verified it, and the known gaps.
- [docs/ALPHA_CHECKLIST.md](docs/ALPHA_CHECKLIST.md): release gates and their evidence.
- `docs/01`-`docs/15`: the original design specification (product, architecture, evidence policy, IR, engines, roadmap). Commands in these documents describe the intended interface, not necessarily what is implemented.
- [docs/DECISIONS.md](docs/DECISIONS.md): the implementation decision log.
- `paper/`: a research write-up of the engine and its benchmarks.
- `docs/build_kit/`: the original build specification. [CLAUDE.md](CLAUDE.md) holds the evidence rules any contributor (human or agent) must follow.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Report security problems privately; see [SECURITY.md](SECURITY.md).

## License

Dual-licensed under either of [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option. Unless you state otherwise, any contribution you intentionally submit for inclusion is dual-licensed in the same way, without additional terms or conditions.
