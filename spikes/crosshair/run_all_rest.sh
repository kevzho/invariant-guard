#!/bin/sh
cd "$(dirname "$0")"
export IG_IMAGE_ID=$(cat IMAGE_ID_six)
for T in 10 30; do IG_TAG=_six IG_REPO=dateutil python3 run_batch.py $T 5; done
export IG_IMAGE_ID=$(cat IMAGE_ID)
for T in 10 30; do python3 run_seeded.py $T 5; done
python3 run_check.py
echo ALLDONE
