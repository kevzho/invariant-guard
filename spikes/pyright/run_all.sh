#!/bin/sh
# Reproduce everything (from spikes/pyright/). Needs node on PATH and spikes/corpus/{more-itertools,boltons,dateutil}.
set -e
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
for r in more-itertools boltons dateutil; do .venv/bin/python measure.py $r 30 > log_$r.txt & done; wait
.venv/bin/python mutate.py          # 15 seeded mutations -> results_mutations.json
.venv/bin/python sample_pre.py      # pre-existing sample + rule pool
