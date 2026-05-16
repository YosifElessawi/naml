"""Local validation gates — declared per-project in ``.agents-orchestrator.toml``.

All output is appended to the run log. The first gate to fail stops the chain;
the orchestrator never burns time on a later gate when an earlier one is red.
No CI minutes are spent — every gate runs on this machine.

Gates are project-defined: e.g. pnpm-based JS, pytest-based Python, cargo for
Rust. See ``templates/.agents-orchestrator.toml.example`` for the schema.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

from . import config


class ValidationResult(NamedTuple):
    passed: bool
    failed_gate: str | None    # None when every gate passed
    summary: str               # one-line human summary
    tail: str                  # last lines of the failing gate's output


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_gates(log_path: Path) -> ValidationResult:
    """Run every gate in order, appending output to ``log_path``.

    Returns as soon as a gate fails. A green run reports
    ``passed=True, failed_gate=None``.
    """
    cfg = config.load()
    with log_path.open("a", encoding="utf-8") as log:
        for gate in cfg.gates:
            name = gate.name
            argv = gate.argv
            header = f"\n{'=' * 60}\nGATE: {name}  ({' '.join(argv)})\n{'=' * 60}\n"
            log.write(header)
            log.flush()
            try:
                result = subprocess.run(
                    argv,
                    cwd=str(cfg.repo_root),
                    capture_output=True,
                    text=True,
                    timeout=20 * 60,
                )
            except FileNotFoundError:
                msg = f"gate '{name}' could not run: '{argv[0]}' not on PATH"
                log.write(msg + "\n")
                return ValidationResult(False, name, msg, msg)
            except subprocess.TimeoutExpired:
                msg = f"gate '{name}' timed out after 20 min"
                log.write(msg + "\n")
                return ValidationResult(False, name, msg, msg)

            output = (result.stdout or "") + (result.stderr or "")
            log.write(output)
            log.write(f"\n[gate '{name}' exit {result.returncode}]\n")
            log.flush()

            if result.returncode != 0:
                tail = _tail(output)
                return ValidationResult(
                    passed=False,
                    failed_gate=name,
                    summary=f"{name} failed",
                    tail=tail,
                )

    return ValidationResult(passed=True, failed_gate=None, summary="all gates passed", tail="")
