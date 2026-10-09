"""Opt-in: the real local model through the real CLI (M4 gate demonstration).

Runs only with IG_REAL_MODEL=1 (a call takes minutes on the reference host). Requires
Ollama on 127.0.0.1:11434 with qwen2.5-coder:7b at the recorded digest. The test never
downloads anything; a missing model or endpoint is a skip with the reason.

    IG_REAL_MODEL=1 .venv/bin/python -m pytest -q tests/integration/test_real_model.py
"""

import json
import os

import pytest

from invariantguard.models.schema import validation_errors

from ..conftest import run_cli
from ..model_helpers import loopback_only, semantic_config  # noqa: F401
from .contract_helpers import exec_config, repo

DIGEST = "sha256:dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364"
SRC = '''\
def clamp_scores(scores: list[float]) -> list[float]:
    """Clamp every score into [0, 100]."""
    return [min(max(s, 0.0), 100.0) for s in scores]
'''

pytestmark = pytest.mark.skipif(os.environ.get("IG_REAL_MODEL") != "1", reason="real-model test is opt-in (IG_REAL_MODEL=1; takes minutes)")


def test_real_model_candidates_pass_the_mechanical_pipeline(tmp_path, loopback_only):
    from invariantguard.semantic.endpoint import validate_loopback_endpoint
    from invariantguard.semantic.ollama import OllamaReasoner

    caps = OllamaReasoner(validate_loopback_endpoint("http://127.0.0.1:11434"), "qwen2.5-coder:7b", pinned_digest=DIGEST).capabilities()
    if not caps.available:
        pytest.skip(f"local model unavailable: {caps.reason}")
    cfg = exec_config("unisolated") + semantic_config("http://127.0.0.1:11434", "qwen2.5-coder:7b", digest=DIGEST, timeout=900)
    root, _ = repo(tmp_path, {"demo/lib.py": SRC}, cfg)
    code, out, err = run_cli(["contracts", "discover", "--model", "local", "--execute", "--path", str(root)])
    assert code == 0, err
    d = json.loads(out)
    assert d["model"]["status"] == "AVAILABLE"
    ident = d["model"]["capabilities"]["identity"]
    assert ident["revision"] == DIGEST and ident["license"].startswith("apache-2.0")
    (f,) = d["model"]["functions"]
    assert f["status"] == "ANSWERED" and f["output_tokens"] > 0
    model = [c for c in d["candidates"] if c["source"] == "local_model"]
    assert model, "the model proposed nothing"
    for c in model:  # whatever it said: classified by the pipeline, never above INFERRED
        assert c["status"] in ("proposed", "rejected", "refuted", "duplicate")
        if c["status"] == "proposed":
            assert c["falsification"]["status"] in ("TESTED", "UNKNOWN")
    for b in d["model_candidate_batches"]:
        assert validation_errors("candidate_batch", b) == []
    code, listed, _ = run_cli(["contracts", "list", "--path", str(root)])
    assert {c["authority"] for c in json.loads(listed)["contracts"]} <= {"INFERRED"}
    assert all(a[0] == "127.0.0.1" for a in loopback_only)


def test_real_model_through_the_openai_compatible_adapter(tmp_path, loopback_only):
    """The OpenAI-compatible adapter against Ollama's real /v1 endpoint (loopback: remote_allowed is not needed)."""
    from invariantguard.semantic.remote import OpenAICompatibleReasoner, validate_provider_url

    caps = OpenAICompatibleReasoner(validate_provider_url("http://127.0.0.1:11434/v1", remote_allowed=False), "qwen2.5-coder:7b").capabilities()
    if not caps.available:
        pytest.skip(f"OpenAI-compatible endpoint unavailable: {caps.reason}")
    cfg = exec_config("unisolated") + ("[semantic]\nenabled = true\nprovider = \"openai_compatible\"\nbase_url = \"http://127.0.0.1:11434/v1\"\n"
                                       "model_id = \"qwen2.5-coder:7b\"\nrequest_timeout_s = 900\n")
    root, _ = repo(tmp_path, {"demo/lib.py": SRC}, cfg)
    code, out, err = run_cli(["contracts", "discover", "--model", "local", "--execute", "--path", str(root)])
    assert code == 0, err
    d = json.loads(out)
    m = d["model"]
    assert m["status"] == "AVAILABLE" and m["source"] == "local_model"
    assert m["capabilities"]["identity"]["artifact_id"] == "openai_compatible:127.0.0.1/qwen2.5-coder:7b"
    assert m["data_egress"]["context_left_machine"] is False and m["data_egress"]["packets_sent"] == 1
    (f,) = m["functions"]
    assert f["status"] == "ANSWERED" and f["output_tokens"] > 0 and f["request_options"]["served_model"] == "qwen2.5-coder:7b"
    assert f["request_options"]["structured_output"] in ("json_schema", "json_object")
    model = [c for c in d["candidates"] if c["source"] == "local_model"]
    assert model, "the model proposed nothing"
    for c in model:
        assert c["status"] in ("proposed", "rejected", "refuted", "duplicate")
        if c["status"] == "proposed":
            assert c["falsification"]["status"] in ("TESTED", "UNKNOWN") and c["provenance"][0]["kind"] == "local_model"
    for b in d["model_candidate_batches"]:
        assert validation_errors("candidate_batch", b) == []
    code, listed, _ = run_cli(["contracts", "list", "--path", str(root)])
    assert {c["authority"] for c in json.loads(listed)["contracts"]} <= {"INFERRED"}
    assert all(a[0] == "127.0.0.1" for a in loopback_only)
