# Python demo fixtures (M1 + M2)

Small, hand-written fixtures for the `division.zero_denominator`, `index.out_of_range` and `none.attribute_access` properties. Static analysis reads these files as bytes and never imports them.

- `baseline/ratio.py` and `candidate/ratio.py`: Fixture A from docs/13 (guarded and unguarded ratio). Domains are declared in `.guard/config.toml`.
- `unknown/imported_ratio.py`: Fixture C (unknown external call; expected UNKNOWN and an INCOMPLETE run).
- `adversarial/`: the docs/05 adversarial fixtures. Expected outcomes and ground truth are in `tests/semantic_fixtures/manifest.json`. `adversarial/syntax_error.py` is deliberately unparsable. M2 added `empty_list.py`, `guarded_index.py`, `alias_mutation.py` (`index.out_of_range`), `none_flow.py` (`none.attribute_access`) and `summary_caller.py` (a call summary: Fixture D's candidate in one snapshot).

```sh
.venv/bin/guard check examples/python_demo/baseline --format json                 # exit 0, PASS
.venv/bin/guard check examples/python_demo/candidate --format json                # exit 1, BLOCK, validated witness
.venv/bin/guard check examples/python_demo/unknown/imported_ratio.py --format json  # exit 2, INCOMPLETE
.venv/bin/guard check examples/python_demo --format human                          # everything
```

`guard check` has no patch attribution (relation UNDETERMINED). Baseline/candidate comparison is `guard verify`; see `examples/patch_demo/` for a throwaway-Git-repository demonstration of Fixture D.
