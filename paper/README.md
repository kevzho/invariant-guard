# Paper: reproducing every number

`paper/invariantguard.tex` is a workshop-style empirical report on the InvariantGuard prototype in this
repository. No number in the paper is typed by hand. The text uses LaTeX macros, and the tables and
figure come from `paper/generated/`. `paper/make_tables.py` writes that directory, reading only
`benchmarks/results/paper_*.json`. Those files come from `benchmarks/run_all.py`.

```
benchmarks/run_all.py  ->  benchmarks/results/paper_*.json  ->  paper/make_tables.py  ->  paper/generated/*  ->  latexmk
```

## 1. Environment

The reference host is listed in `benchmarks/results/paper_manifest.json` (`last_invocation.host`): an
Apple M4 with 16 GB RAM, macOS 15.6, CPython 3.13.9, z3 5.1.0, Docker Desktop 29.5.2 and Ollama 0.40.1. The Ollama app had updated itself from the 0.35.1 recorded in docs/IMPLEMENTATION_STATUS.md; the model digest is unchanged.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e . --no-deps   # solver, dev, mcp, paper extras (matplotlib 3.11.2)
docker info                                      # Docker Desktop must be running
.venv/bin/guard runner setup --run-profile isolated-python   # or: docker pull python:3.13-slim (image id is recorded)
ollama list                                      # qwen2.5-coder:7b, digest dae161e27b0e...; guard never pulls a model
```

Static benchmarks need no network. The contract checks run in the `python:3.13-slim` Docker image with
networking disabled. The model and transport parts talk to Ollama on 127.0.0.1 only. No hosted model
provider is contacted anywhere.

## 2. Benchmarks (one command)

```bash
.venv/bin/python benchmarks/run_all.py                      # all parts, about 5-6 h on the reference host under load
.venv/bin/python benchmarks/run_all.py --parts semantic,seeded   # a subset (the manifest keeps the other parts)
```

| Part | Results file | What it runs | Paper |
| --- | --- | --- | --- |
| `tests` | `paper_test_suite.json` | `.venv/bin/python -m pytest -q -p no:cacheprovider` | Implementation |
| `semantic` | `paper_semantic_fixtures.json` | `benchmarks/bench_semantic.py`: every case of `tests/semantic_fixtures/manifest.json` through `guard check`, with and without the solver. Ground truth comes from `benchmarks/manifests/semantic_ground_truth.json` and is executed by `benchmarks/gt_oracle.py` | RQ1 (semantic table) |
| `seeded` | `paper_seeded_patches.json` | `benchmarks/bench_seeded.py`: 35 baseline/patch pairs (`benchmarks/fixtures/seeded/`, labels in `benchmarks/manifests/seeded_patches.json`). Each pair runs in a throwaway Git repository with `guard verify` in quick, quick without the solver, full, warm-cache and `--execute` modes | RQ2-RQ4 (seeded tables), appendix per-case table |
| `agent` | `paper_agent_eval_run{1,2,3}.json` | `benchmarks/agent_eval/score.py` on the recorded proposals in `benchmarks/agent_eval/proposals/run{1,2,3}/`. It never contacts a model | RQ5 (proposer table) |
| `latency` | `paper_latency.json` | `benchmarks/bench_latency.py`: synthetic repositories of about 100, 1,000 and 10,000 functions; leaf, hub and callee changes; quick and full modes, cold and warm; 30 repeats (5 at 10,000) | appendix (first-run table) |
| `latency_repeat` | `paper_latency_repeat.json` | the same latency benchmark again, last in the pipeline; pre-registered as the primary latency run (DECISIONS I-111) | RQ4 (consistency, latency, phase tables; figure) |
| `model` | `paper_model_eval.json` | `benchmarks/model_eval.py --repeats 3 --template-repeats 3`: templates and the local model on the 13 held-out functions (`benchmarks/fixtures/m4_heldout`, `benchmarks/manifests/m4_heldout.json`) | RQ5 (proposer table) |
| `transport` | `paper_transport_openai_compat.json` | `benchmarks/model_eval.py --provider openai_compatible --base-url http://127.0.0.1:11434/v1 --repeats 1 --template-repeats 0`: the bring-your-own-key OpenAI-compatible adapter against the same local Ollama server | RQ5 (transport check only) |

Host load. `benchmarks/results/logs/host_load.log` holds one sample per minute: 1-minute load average,
swap in use, free memory percentage and the number of running containers. It was sampled with a shell loop
from the middle of the first latency part to the end of the latency repeat; that loop is not part of
`run_all.py`, and there is a gap of a few minutes before the repeat. `make_tables.py` reads it for the
host-load sentences. The other load numbers come from the per-run `load_avg` that `bench_latency.py`
records after every run.

`paper_manifest.json` records the host and software identities: the lock digest, whether `pip freeze`
equals the lock, the sha256 of the source tree, the Docker image id, and the load average at start.
For each part it also records the exact command, start and finish times, exit status and the sha256
of the results file. Raw per-run data (every report summary, every timing) is kept in the results
files. The console logs are in `benchmarks/results/logs/`.

## 3. Tables, figure and compile

```bash
.venv/bin/python paper/make_tables.py           # writes paper/generated/{numbers.tex,tab_*.tex,fig_latency.pdf}
cd paper && latexmk -pdf invariantguard.tex
```

## 4. Inputs that `run_all.py` does not regenerate

* **Agent proposals** (`benchmarks/agent_eval/proposals/run{1,2,3}/`). Each run is one batch file per
  held-out function. Three separate Claude Code subagents wrote them on 2026-10-08 between 23:02 and
  23:04 UTC. The session transcripts record the model as `claude-sonnet-5-5`. Each subagent got the same
  instruction: read only `benchmarks/agent_eval/packets/` and `benchmarks/agent_eval/README.md`, do not
  run `guard`, and write one CandidateBatch per packet (at most 8 candidates). The instruction also
  asked for "properties that a maintainer would plausibly intend, including ones that might expose
  bugs". The transcripts show that each subagent read only those files. Scoring is deterministic given
  the files, apart from Docker falsification, which uses fixed seeds. Agent outputs are not
  deterministic, so a rerun of the subagents would give different proposals.
* **Labels.** The seeded labels were frozen at `2026-10-08T17:44:28Z`, before any tool run on those
  cases. The first run was at 17:48Z, and the manifest has not changed since. Its sha256 is in
  `paper_seeded_patches.json`. The semantic ground-truth manifest was written at 17:45Z. After a
  smoke run, one field was added: `site_line` for `floor-semantics-safe-const`, so that the safe-label
  grid check counts failures only at the obligation's own line. The function has a second division
  site that can fail. No label changed.

## 5. What the numbers are not

The fixtures, seeded patches and held-out functions were written by the people who built the tool,
and the agent proposer is the same model family. Everything ran on one machine under uncontrolled
background load, with one open-weight model. Section "Threats to validity" in the paper lists the
consequences. Precision and recall are reported only where an execution-confirmed or hand label
exists, and abstentions stay in every denominator.
