import pytest

from invariantguard.config import ConfigError, INIT_TEMPLATE, parse_config


def test_init_template_parses_and_omits_endpoint():
    cfg = parse_config(INIT_TEMPLATE.encode())
    assert cfg.analysis.per_query_timeout_ms == 2000 and cfg.analysis.per_query_rlimit == 500_000 and cfg.domains == []
    assert "endpoint" not in cfg.semantic


def test_domains_parse():
    cfg = parse_config(b'[[domains]]\nsymbol = "a.py::f"\nprofile = "python_exact_builtin_ints"\nauthority = "DECLARED"\nreason = "r"\nparams = ["x"]\n')
    (d,) = cfg.domains
    assert d.symbol == "a.py::f" and d.params == ("x",)


@pytest.mark.parametrize(
    "text",
    [
        b"bogus = 1\n",
        b"[analysis]\nsolver_enabled = \"yes\"\n",
        b"[analysis]\nper_query_timeout_ms = 0\n",
        b"[analysis]\nlanguages = [\"kotlin\"]\n",
        b"[analysis]\nunknown_key = 1\n",
        # M2 rejected `enabled = true` outright; M3 accepts it but still rejects malformed or unauthorized execution settings.
        b"[execution]\nenabled = \"yes\"\n",
        b"[execution]\nenabled = true\nprofile = \"missing\"\n",
        b"[execution.profiles.p]\nbackend = \"unisolated\"\nallowed_targets = [\"a.py::f\"]\n",
        b"[execution.profiles.p]\nbackend = \"docker\"\nallowed_targets = [\"a.py::f\"]\n",
        b"[execution.profiles.p]\nbackend = \"docker\"\nimage = \"python:3.13-slim\"\nallowed_targets = [\"a.py::f\"]\nnetwork = \"host\"\n",
        b"[execution.profiles.p]\nbackend = \"chroot\"\nallowed_targets = [\"a.py::f\"]\n",
        b"[semantic]\nenabled = true\n",
        b"[privacy]\ntelemetry = true\n",
        b'schema_version = "2.0"\n',
        b"[[domains]]\nsymbol = \"a.py::f\"\nprofile = \"python_any\"\nauthority = \"DECLARED\"\nreason = \"r\"\n",
        b"[[domains]]\nsymbol = \"a.py::f\"\nprofile = \"python_exact_builtin_ints\"\nauthority = \"INFERRED\"\nreason = \"r\"\n",
        b"[[domains]]\nsymbol = \"a.py::f\"\nprofile = \"python_exact_builtin_ints\"\nauthority = \"DECLARED\"\n",
        b"[[domains]]\nsymbol = \"f\"\nprofile = \"python_exact_builtin_ints\"\nauthority = \"DECLARED\"\nreason = \"r\"\n",
        b"not toml at all [[[",
    ],
)
def test_invalid_config_rejected(text):
    with pytest.raises(ConfigError):
        parse_config(text)
