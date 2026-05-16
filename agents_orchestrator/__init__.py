"""agents-orchestrator — project-agnostic Claude-agent orchestrator.

Picks up triaged GitHub issues, drives Claude sessions to implement them,
runs local validation gates, opens PRs, merges when green. Configured
per-target-repo via .agents-orchestrator.toml.
"""
