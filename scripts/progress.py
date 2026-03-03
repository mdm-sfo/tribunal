"""
Tribunal progress display — stderr output for host agent visibility.

Usage:
    from progress import Progress
    p = Progress("tribunal-20260228-211400", "THOROUGH")
    p.session_start()
    p.phase(2, "Independent work — dispatching to 4 advocates...")
    p.model_success("Claude Sonnet", 3.2, 1450)
    p.model_fail("DeepSeek R1", 120.0, "timeout")
    p.phase(6, "Judicial review — 3 judges evaluating...")
    p.session_done("./conclave-sessions/tribunal-20260228-211400/")
"""

import sys
import time


class Progress:
    """Writes structured progress to stderr so host agents can display status."""

    PREFIX = "[tribunal]"

    def __init__(self, session_id: str, depth: str):
        self.session_id = session_id
        self.depth = depth
        self._start_time = time.time()
        self._total_cost: float = 0.0

    def _write(self, msg: str):
        sys.stderr.write(f"{self.PREFIX} {msg}\n")
        sys.stderr.flush()

    def session_start(self):
        self._write(f"Session {self.session_id} started ({self.depth} depth)")

    def sacred_college(self, bishops: list[str], priests: list[str], deacons: list[str]):
        parts = []
        for b in bishops:
            parts.append(f"Justice {b}")
        for p in priests:
            parts.append(f"Appellate Judge {p}")
        for d in deacons:
            parts.append(f"Magistrate Judge {d}")
        self._write(f"The Bench: {', '.join(parts)}")

    def phase(self, number: int, description: str):
        self._write(f"Phase {number}: {description}")

    def model_success(self, name: str, elapsed: float, tokens: int = 0, cost: float = 0.0):
        self._total_cost += cost
        tok = f", {tokens} tok" if tokens else ""
        cost_str = f", ${cost:.4f}" if cost else ""
        running = f" — total: ${self._total_cost:.4f}" if self._total_cost else ""
        self._write(f"  ✓ {name} ({elapsed:.1f}s{tok}{cost_str}){running}")

    def model_fail(self, name: str, elapsed: float, reason: str):
        self._write(f"  ✗ {name} failed ({elapsed:.1f}s): {reason}")

    def agreement_score(self, score: float, target: float, continuing: bool):
        status = "continuing..." if continuing else "consensus reached!"
        self._write(f"  Agreement score: {score:.2f} (target: {target:.2f}) — {status}")

    def cardinal_verdict(self, name: str, verdict: str):
        self._write(f"  ✓ {name}: {verdict}")

    def cardinal_remand(self, name: str, reason: str):
        self._write(f"  ⚠ {name}: REMAND — {reason}")

    def session_done(self, output_dir: str):
        elapsed = time.time() - self._start_time
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        self._write(f"Phase 8: Done. ({minutes}m {seconds}s, ${self._total_cost:.4f} total) Files → {output_dir}")

    def info(self, msg: str):
        self._write(f"  {msg}")

    def warn(self, msg: str):
        self._write(f"  ⚠ {msg}")

    def error(self, msg: str):
        self._write(f"  ✗ ERROR: {msg}")
