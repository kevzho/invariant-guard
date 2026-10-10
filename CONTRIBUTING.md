# Contributing

InvariantGuard is an alpha research preview. Contributions are welcome, but the evidence rules matter more than features.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e . --no-deps
.venv/bin/python -m pytest -q
```

The suite runs offline: any in-process network connection fails a test. Docker-backed tests skip when no daemon is reachable. Real-model tests are opt-in (`IG_REAL_MODEL=1`).

## Rules for changes

Read [CLAUDE.md](CLAUDE.md) (the evidence rules, written for both humans and coding agents) and [docs/03_EVIDENCE_AND_POLICY.md](docs/03_EVIDENCE_AND_POLICY.md). In short:

- Never weaken an outcome to make a result look better. Unsupported behavior must produce `UNKNOWN`, which makes the run `INCOMPLETE`, never `PASS`.
- Extending the modeled Python subset needs semantic fixtures with safe, unsafe and unknown cases. They must agree with real CPython behavior; `tests/differential/` has the harness.
- Static code paths must never import, execute or modify the repository under analysis.
- Record new design decisions in `docs/DECISIONS.md`. Record what you ran and its outcome in `docs/IMPLEMENTATION_STATUS.md`.
- Keep changes small and reviewable.

Unless you state otherwise, any contribution you intentionally submit for inclusion is dual-licensed under MIT OR Apache-2.0, without additional terms or conditions.
