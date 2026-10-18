# MCP integration (M5)

`guard mcp serve --stdio` serves eight tools over the Model Context Protocol (six since M5; `get_context_packet` and `propose_contracts` since I-106). They call the same orchestration code as the CLI (`src/invariantguard/service.py` → `run_verify` / `run_check` / `contracts.commands`) and return versioned JSON (`schema_version` "1.0").

| Tool | What it does | Writes |
| --- | --- | --- |
| `verify_patch(repository, base, mode?, path?, run_profile?, detail?, timeout_s?, no_cache?)` | `guard verify` of the working tree against an explicit base. It returns the verdict, exit code, coverage and compact findings. `detail="full"` adds the schema-valid report. | Only its own state under `<root>/.guard/`: cache, contract evidence and the report index |
| `check_scope(repository, path?, detail?, timeout_s?)` | `guard check` (single snapshot, no patch attribution) | Nothing. Its report is kept only in the server session. |
| `get_contracts(repository)` | `guard contracts list` (authority, lifecycle, latest evidence) | Nothing. In a repository without `.guard/state.sqlite` it returns an empty list and creates nothing (audit A5-01). |
| `get_finding(repository, finding_id, run_id?)` | Full finding, obligation or contract result, evidence records, run identity, and freshness | Nothing |
| `explain_finding(repository, finding_id, run_id?)` | A deterministic explanation from stored evidence: what was established, its limits, and what would resolve it. No model is used. | Nothing |
| `get_context_packet(repository, symbol)` | The exact bounded ContextPacket a model would receive for one contract target, plus its digest, the per-packet candidate JSON schema, the allowed DSL nodes and the proposer protocol | Nothing (no `.guard` state is created) |
| `propose_contracts(repository, symbol, context_packet_digest, candidates, run_profile?, dry_run?, timeout_s?)` | Validates up to 8 agent-proposed candidates with the same strict pipeline as model output (schema, typed DSL, source references, non-vacuity, duplicates). It falsifies them in the runner only with a preauthorized docker `run_profile`. It returns per-candidate accept/reject reasons and evidence ids. A batch written against another packet is `STALE_CONTEXT`. | INFERRED contract revisions with `agent_proposed` provenance (the client's self-reported `clientInfo`) and `discovery_falsification` artifacts under `<root>/.guard/`; nothing with `dry_run` |
| `get_capabilities()` | Engine, solver, execution and model capabilities, plus the configured roots, preauthorized profiles and each root's configured model proposer (including whether its context would leave the machine) | Nothing |

Every response has a `status`:

| Status | Meaning |
| --- | --- |
| `OK` | The tool ran. For verify/check, the verdict is in `verdict`; a BLOCK verdict still has status `OK`. |
| `STALE_SNAPSHOT` | The evidence does not describe the current working tree. Either the tree changed during the run (after one restart), or `get_finding`/`explain_finding` found that the tree changed after the evidence was produced. The data is returned and flagged. It is never attributed to the current code. |
| `TIMEOUT` | The call stopped at the server limit. The run is finalized as failed: no cache entry, contract evidence or report from it is published. |
| `INVALID_REQUEST`, `NOT_AUTHORIZED`, `NOT_FOUND`, `ENGINE_ERROR` | Explicit failures. MCP `isError` is true for these. |
| Cancellation | A client `notifications/cancelled` stops the run at its next safe point. Per protocol, the cancelled request gets no response. The run is finalized as failed, with note `cancelled: client cancelled the request`. |

## What the server refuses

- **Repositories**: only the roots given with `--root [ALIAS=]PATH` when the server starts. A tool names a root by its alias or by its exact path. The `path` argument is a relative scope; `..`, absolute paths and symlinks that leave the root are rejected. Configuration is read only from `<root>/.guard/config.toml`, never from a parent directory.
- **Tools**: there are no tools to approve, declare, revise, reject or retire contracts. `propose_contracts` can only add INFERRED candidates; a proposal equal to a recorded contract is reported as `exists`/`duplicate` and never changes it, and its arguments have no authority, approval, provenance or identity field. There is no tool to create a baseline, set up the runner, run model discovery (local or remote) or execute commands. These stay maintainer CLI commands (`get_capabilities` lists them under `not_exposed`).
- **Execution**: `verify_patch(run_profile=NAME)` runs dynamic checks only if all three of these hold:
  - NAME was passed as `--allow-profile NAME` at startup.
  - The profile's resolved definition is identical to the one recorded at startup. If a repository edit changes it, the profile is no longer authorized until a maintainer restarts the server. The digest is checked twice: when the call arrives, and again inside `verify` against the very configuration object the run uses, so an edit between the two checks is refused with NOT_AUTHORIZED before anything executes (audit A5-02).
  - It uses the isolated `docker` backend.

  There is no unisolated execution through MCP.
- **Output**: stdout carries only JSON-RPC frames. While serving, fd 1 points at stderr and `sys.stdout` is rebound to stderr, and buffered output is flushed before fd 1 is restored. Logs go to stderr. The SDK's OpenTelemetry middleware is removed.

MCP does not enforce anything. It cannot force an agent to call `verify_patch` or to respect a BLOCK. Enforced acceptance needs a client hook or CI job that runs `guard verify` and checks its exit code. That enforcement is not part of this milestone.

## Install

```sh
.venv/bin/pip install -e '.[mcp]'      # the official MCP Python SDK, pinned: mcp==2.3.0
.venv/bin/guard capabilities           # "mcp: available (explicit)"
```

## Claude Code: project-scope configuration

This is the tested client configuration. Put it in a file named `.mcp.json` at the root of the project in which Claude Code runs. Claude Code reads project-scope servers from that file and asks for approval before first use. Nothing here installs it into any user configuration; copy it yourself. Replace the two absolute placeholder paths:

```json
{
  "mcpServers": {
    "invariantguard": {
      "type": "stdio",
      "command": "/ABSOLUTE/PATH/TO/invariant-guard/.venv/bin/guard",
      "args": ["mcp", "serve", "--stdio", "--root", "app=/ABSOLUTE/PATH/TO/your-repository", "--timeout-s", "600"],
      "env": {}
    }
  }
}
```

To let agents run dynamic contract checks, add `"--allow-profile", "NAME"` for an isolated docker profile that is already defined and enabled in `/ABSOLUTE/PATH/TO/your-repository/.guard/config.toml`. Prepare its image once with `guard runner setup --run-profile NAME`; this is the only networked command. An equivalent CLI registration is `claude mcp add --scope project invariantguard -- /ABSOLUTE/PATH/TO/invariant-guard/.venv/bin/guard mcp serve --stdio --root app=/ABSOLUTE/PATH/TO/your-repository`.

Suggested agent convention, for the project's agent instructions file. It is a convention only, not enforcement:

> Optional: to propose behavioral contracts for a function you changed, call `get_context_packet(repository="app", symbol="path/to/mod.py::fn")`, write candidates that follow its `candidate_schema`, and submit them with `propose_contracts` using the returned `context_packet_digest`. Accepted candidates are INFERRED proposals for a maintainer to review. Never treat them as approved.
>
> Before declaring a change complete, call the `invariantguard` tool `verify_patch` with `repository="app"` and `base=<the commit you started from>`. If the verdict is BLOCK, read `get_finding` / `explain_finding` for each ERROR finding, fix the code (never the contract, domain or baseline), and verify again. INCOMPLETE is not a pass.

### What was tested, and what was not

- **Tested**: `tests/integration/test_mcp_server.py::test_documented_claude_code_config_launches_and_verifies` reads the JSON block above from this file and substitutes the two placeholders. It then launches exactly that `command` and `args` with the official SDK stdio client, using the initialize handshake that stdio clients such as Claude Code use. It performs the handshake, then `tools/list`, then `verify_patch` on a fixture repository (BLOCK with an exact witness), then `get_finding`. Other tests in the same file cover:
  - the modern (2026-07-28) protocol path;
  - stdout purity under deliberate stray output;
  - client cancellation and server timeouts (no run is published);
  - Docker contract replay through MCP.
  - agent proposals: `test_agent_proposals_over_mcp` (legacy and 2026-07-28 protocols) runs `get_context_packet` and then `propose_contracts`, and records the client's `clientInfo` as `agent_proposed` provenance. Vacuous and authority-smuggling candidates are rejected, and a re-proposal against the old packet is `STALE_CONTEXT`.
- **Not tested**: a live Claude Code session loading this `.mcp.json`. Claude Code was not driven from the test suite, and the configuration was not installed into any user or project configuration. The repair loop recorded in `examples/demo_repo/recorded_run/` used a different agent client: a local-model harness (`examples/demo_repo/run_demo.py`) that talks to the same server through the SDK client.
