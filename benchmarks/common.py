"""Shared benchmark helpers: environment capture, timing, and CSV output.

Every results file records the machine and date it came from, because a runtime
without the hardware it ran on is not a reproducible measurement.
"""

from __future__ import annotations

import csv
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).parent / "results"
WARMUP_RUNS = 1
REPEAT_RUNS = 5


def cpu_model() -> str:
    """Best effort CPU model name for the current machine."""
    if sys.platform == "darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


@dataclass(frozen=True, slots=True)
class Environment:
    """The machine and software a benchmark ran on."""

    cpu: str
    machine: str
    os_name: str
    python_version: str
    run_date: str

    @classmethod
    def capture(cls) -> Environment:
        """Record the current environment."""
        return cls(
            cpu=cpu_model(),
            machine=platform.machine(),
            os_name=f"{platform.system()} {platform.release()}",
            python_version=platform.python_version(),
            run_date=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )

    def as_rows(self) -> list[dict[str, str]]:
        """Render as key and value rows for a CSV header block."""
        return [
            {"key": "cpu", "value": self.cpu},
            {"key": "machine", "value": self.machine},
            {"key": "os", "value": self.os_name},
            {"key": "python", "value": self.python_version},
            {"key": "run_date", "value": self.run_date},
        ]


@dataclass(frozen=True, slots=True)
class Timing:
    """Timings from one warmup plus REPEAT_RUNS measured repeats."""

    samples_ms: tuple[float, ...]

    @property
    def median_ms(self) -> float:
        """Median of the measured repeats."""
        return statistics.median(self.samples_ms)

    @property
    def p95_ms(self) -> float:
        """Nearest-rank 95th percentile of the measured repeats."""
        ordered = sorted(self.samples_ms)
        return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]

    @property
    def min_ms(self) -> float:
        """Fastest measured repeat."""
        return min(self.samples_ms)


def time_repeats(
    action: Callable[[], Any], repeats: int = REPEAT_RUNS, warmups: int = WARMUP_RUNS
) -> Timing:
    """Run action once to warm up, then time it repeats times with perf_counter_ns.

    The warmup exists to pay one-off costs such as import, allocation, and branch
    prediction before anything is recorded.
    """
    for _ in range(warmups):
        action()

    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - started) / 1e6)

    return Timing(samples_ms=tuple(samples))


def write_csv(filename: str, rows: Sequence[dict[str, Any]], environment: Environment) -> Path:
    """Write rows to benchmarks/results/filename with an environment header block.

    The environment lines are prefixed with '#' so the file stays readable as a
    comment header followed by a normal CSV table.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / filename

    with path.open("w", newline="") as handle:
        for row in environment.as_rows():
            handle.write(f"# {row['key']}: {row['value']}\n")

        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    return path


def read_csv(filename: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Read a results CSV written by write_csv, returning (rows, environment)."""
    path = RESULTS_DIR / filename
    environment: dict[str, str] = {}
    data_lines: list[str] = []

    for line in path.read_text().splitlines():
        if line.startswith("# "):
            key, _, value = line[2:].partition(": ")
            environment[key] = value
        else:
            data_lines.append(line)

    return list(csv.DictReader(data_lines)), environment


def spread(values: Iterable[float]) -> tuple[float, float, float]:
    """Return (mean, min, max) for a set of per-seed observations."""
    collected = list(values)
    if not collected:
        return 0.0, 0.0, 0.0
    return sum(collected) / len(collected), min(collected), max(collected)
