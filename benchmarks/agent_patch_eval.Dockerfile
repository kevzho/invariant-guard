# Runner image for benchmarks/agent_patch_eval.py on sympy (explicit setup step; the only network use).
# The replay image (spikes/diffreplay/Dockerfile) plus sympy's one runtime dependency.
FROM invariantguard-diffreplay:spike
RUN pip install --no-cache-dir mpmath==1.3.0 && pip freeze > /opt/agent-eval-freeze.txt
