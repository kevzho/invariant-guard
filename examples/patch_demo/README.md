# Patch demo (M2): changed callee, unchanged caller

`run_demo.py` builds a throwaway Git repository in a temporary directory, commits the docs/13 Fixture D baseline, changes only the callee and runs `guard verify --base <commit> --format json`. It prints values read from the real report: the invalidated symbols, the finding, its witness and how the same witness behaves in the base model.

```sh
.venv/bin/python examples/patch_demo/run_demo.py            # temporary repository, removed afterwards
.venv/bin/python examples/patch_demo/run_demo.py --keep /tmp/ig-demo   # keep it to inspect or rerun guard
```

Expected shape (from an actual run; your snapshot IDs differ): verdict BLOCK, exit 1; `calc.py::scaled` invalidated because it calls the changed `calc.py::denominator`; an INTRODUCED, EXACT_MODEL `ZeroDivisionError` finding with witness `{'x': 0}`; the same witness PASSES in the base model. The integration test `tests/integration/test_verify.py::test_fixture_d_changed_callee_invalidates_unchanged_caller` also replays that witness against both real file versions in isolated CPython processes.
