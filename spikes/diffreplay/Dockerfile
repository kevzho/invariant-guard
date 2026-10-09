# Spike image for differential replay (explicit setup step: the ONLY network use).
# Derived from the runner's base image (python:3.13-slim), pinned by digest.
FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f
# Hypothesis runs INSIDE the isolation boundary; six is a runtime dependency of dateutil.
RUN pip install --no-cache-dir hypothesis==6.165.0 attrs==26.1.0 sortedcontainers==2.4.0 six==1.17.0 pytest==9.1.1 freezegun==1.5.5 \
 && pip freeze > /opt/diffreplay-freeze.txt
