"""Explicitly enabled dynamic execution (M3, docs/10): run profiles, isolation backends, probe harness.

Nothing in this package runs unless a command was given ``--execute`` with a run profile
enabled in configuration (or the explicit ``guard runner setup|probe`` commands).
"""
