"""Naml — sprint-driven orchestrator for Claude Code agents.

V2 entry points:

- ``naml.config.load_config`` — reads ``.naml/config.toml`` (preferred) or
  the legacy ``.agents-orchestrator.toml`` and returns a ``NamlConfig``.
- ``naml.package.load_sprint`` — reads a sprint package directory
  (``.naml/sprints/<id>/``) and returns a ``Sprint`` dataclass with the
  manifest, overview, and validated DAG of slices.
- ``naml.cli`` — the ``naml`` command-line entry point (``naml migrate-config``).
"""

__version__ = "0.2.0-dev"
