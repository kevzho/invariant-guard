FROM python:3.13-slim
# Explicit setup step: network is used ONLY here, at image build time.
# crosshair-tool has no wheel for this platform (arm64), so a C compiler is needed to build it, then removed.
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
 && pip install --no-cache-dir crosshair-tool==0.0.111 && pip freeze > /opt/freeze.txt \
 && apt-get purge -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
