"""Local validation gates — declared per-project in the naml config.

Same contract as v1: run gates in order; the first one to fail stops the
chain. Every gate's output is appended to the slice's run log so a human
can `cat` the log and see exactly what the lane saw.

No CI minutes are spent — every gate runs on the user's machine, in the
slice's worktree.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import Gate


_DEFAULT_GATE_TIMEOUT_SECONDS = 20 * 60


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failed_gate: str | None
    summary: str
    tail: str               # last lines of the failing gate's output


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_gates(
    gates: Sequence[Gate],
    *,
    cwd: Path,
    log_path: Path,
    timeout_seconds: int = _DEFAULT_GATE_TIMEOUT_SECONDS,
) -> GateResult:
    """Run every gate in order. Return as soon as one fails."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        for gate in gates:
            header = (
                f"\n{'=' * 60}\nGATE: {gate.name}  ({' '.join(gate.argv)})\n"
                f"{'=' * 60}\n"
            )
            log.write(header)
            log.flush()
            try:
                result = subprocess.run(  # noqa: S603
                    list(gate.argv),
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except FileNotFoundError:
                msg = (
                    f"gate '{gate.name}' could not run: '{gate.argv[0]}' "
                    f"not on PATH"
                )
                log.write(msg + "\n")
                return GateResult(False, gate.name, msg, msg)
            except subprocess.TimeoutExpired:
                msg = f"gate '{gate.name}' timed out after {timeout_seconds // 60} min"
                log.write(msg + "\n")
                return GateResult(False, gate.name, msg, msg)

            output = (result.stdout or "") + (result.stderr or "")
            log.write(output)
            log.write(f"\n[gate '{gate.name}' exit {result.returncode}]\n")
            log.flush()

            if result.returncode != 0:
                return GateResult(
                    passed=False,
                    failed_gate=gate.name,
                    summary=f"{gate.name} failed",
                    tail=_tail(output),
                )

    return GateResult(passed=True, failed_gate=None, summary="all gates passed", tail="")
