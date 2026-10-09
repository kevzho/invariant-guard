# SENTINEL: importing or executing this module writes a marker file and exits.
# guard check must analyze it from source bytes only.
import pathlib
import sys

pathlib.Path(__file__).with_name("SENTINEL_TOUCHED").write_text("this module was executed\n")
import invariantguard_sentinel_dependency_that_does_not_exist  # noqa: E402,F401

sys.exit("side_effect_sentinel must never be executed by static analysis")


def ratio(a: int, b: int):
    d = a - b
    return 100 / d
