#!/bin/sh
# Explicit environment preparation for the diffreplay spike (network used here only).
set -eu
cd "$(dirname "$0")"
docker build -t invariantguard-diffreplay:spike .
docker image inspect invariantguard-diffreplay:spike --format '{{.Id}}'
docker run --rm --network none --entrypoint cat invariantguard-diffreplay:spike /opt/diffreplay-freeze.txt
