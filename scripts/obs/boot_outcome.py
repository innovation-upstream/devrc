#!/usr/bin/env python3
"""Emit a Prometheus metric for whether the PREVIOUS boot ended cleanly.

Why this exists
---------------
On 2026-08-20 the laptop was believed to have "hard-frozen twice in the past
month". It had actually stopped uncleanly SIX times since 2026-07-29 (Jul 29,
Jul 31, Aug 06, Aug 10, Aug 17, Aug 20). Nobody was wrong on purpose — the rate
was simply never measured, because the only detector was a human noticing that
work had been interrupted. A freeze that happens while you are away from the
machine leaves no impression at all.

So this turns "it froze a couple of times" into a timestamped series.

A boot is CLEAN if its final journal entries contain systemd's shutdown
sequence, and UNCLEAN otherwise. That is the same classification used in the
original investigation.

Honest limits of the classification
-----------------------------------
"Unclean" means "never reached systemd's shutdown sequence". That covers a hard
lockup, but ALSO a battery running flat, a forced power-button hold, and a
kernel panic. It is a symptom counter, not a diagnosis, and the metric name says
`unclean`, not `frozen`, for exactly that reason.

It is also blind past journal retention: a boot that has been rotated away
cannot be classified. That is why `boots_examined` is emitted BESIDE the unclean
count — a `0` from a scan that walked nothing is a failure, not an all-clear,
and the two numbers are only interpretable together.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# Markers that prove the boot reached systemd's shutdown path. "Journal stopped"
# is the load-bearing one (journald logs it on SIGTERM from PID 1, on both
# poweroff and reboot); the other two are corroborating.
CLEAN_MARKERS = ("Journal stopped", "System Power Off", "Shutting down")

TAIL_LINES = 12


@dataclass
class Outcome:
    previous_clean: int | None = None
    previous_end_timestamp: float | None = None
    unclean_count: int | None = None
    boots_examined: int = 0
    scrape_ok: int = 1
    errors: list[str] = field(default_factory=list)


def ended_cleanly(tail: list[str]) -> bool:
    """True if these final journal lines show systemd's shutdown sequence."""
    return any(marker in line for line in tail for marker in CLEAN_MARKERS)


def collect(journal) -> Outcome:
    """Classify every retained boot. `journal` supplies the raw facts.

    Deliberately tolerant of a partial journal: a boot whose tail cannot be read
    is skipped and recorded as an error rather than silently counted as clean,
    because counting an unreadable boot as clean is the failure mode that would
    hide the very events this exists to count.
    """
    out = Outcome()
    try:
        offsets = journal.boot_offsets()
    except Exception as exc:  # noqa: BLE001 - any failure means we know nothing
        out.scrape_ok = 0
        out.errors.append(f"boot_offsets: {exc}")
        return out

    # The current boot (offset 0) has not ended, so it is not classifiable.
    past = [o for o in offsets if o < 0]

    unclean = 0
    for off in past:
        try:
            tail = journal.tail(off, TAIL_LINES)
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"tail({off}): {exc}")
            continue
        out.boots_examined += 1
        if not ended_cleanly(tail):
            unclean += 1

    if out.boots_examined == 0:
        # A zero here would read as "no unclean boots", which is precisely the
        # reassuring-but-empty answer this guard exists to refuse to emit.
        out.scrape_ok = 0
        out.errors.append("no boots examined")
        return out

    out.unclean_count = unclean

    if -1 in past:
        try:
            tail = journal.tail(-1, TAIL_LINES)
            out.previous_clean = 1 if ended_cleanly(tail) else 0
            out.previous_end_timestamp = journal.last_timestamp(-1)
        except Exception as exc:  # noqa: BLE001
            out.errors.append(f"previous boot: {exc}")
            out.scrape_ok = 0

    return out


def render(out: Outcome) -> str:
    """Prometheus text exposition. Unknown values are OMITTED, never zeroed."""
    lines: list[str] = []

    def metric(name: str, help_text: str, mtype: str, value):
        if value is None:
            return
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {value}")

    metric(
        "host_boot_previous_clean",
        "Whether the previous boot ended with systemd's shutdown sequence (1) or stopped abruptly (0).",
        "gauge",
        out.previous_clean,
    )
    metric(
        "host_boot_previous_end_timestamp_seconds",
        "Unix time of the last journal entry of the previous boot.",
        "gauge",
        None if out.previous_end_timestamp is None else f"{out.previous_end_timestamp:.0f}",
    )
    metric(
        "host_unclean_boots_observed",
        "Unclean boot endings across the retained journal. Only meaningful beside host_boots_examined.",
        "gauge",
        out.unclean_count,
    )
    metric(
        "host_boots_examined",
        "Past boots actually classified. A zero unclean count from zero examined boots is not an all-clear.",
        "gauge",
        out.boots_examined,
    )
    metric(
        "host_boot_outcome_scrape_ok",
        "Whether the boot-outcome classifier ran successfully (1) or could not measure (0).",
        "gauge",
        out.scrape_ok,
    )
    return "\n".join(lines) + "\n"


def write_atomically(path: str, text: str) -> None:
    """node_exporter's textfile collector reads whole files; a partial write is
    a parse error that poisons the whole scrape. Write-then-rename is required,
    not a nicety."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".boot_outcome.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SystemdJournal:
    """Real journalctl-backed facts."""

    def __init__(self, journalctl: str = "journalctl"):
        self.journalctl = journalctl

    def _run(self, args: list[str]) -> str:
        return subprocess.run(
            [self.journalctl, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    def boot_offsets(self) -> list[int]:
        raw = self._run(["--list-boots", "-o", "json"])
        return [int(entry["index"]) for entry in json.loads(raw)]

    def tail(self, offset: int, n: int = TAIL_LINES) -> list[str]:
        # -b <offset> is scoped to that boot; -n <n> takes its final lines.
        raw = self._run(["-b", str(offset), "-n", str(n), "--no-pager", "-o", "short-precise"])
        return raw.splitlines()

    def last_timestamp(self, offset: int) -> float | None:
        raw = self._run(["-b", str(offset), "-n", "1", "--no-pager", "-o", "short-unix"])
        line = raw.strip().split("\n")[-1] if raw.strip() else ""
        if not line:
            return None
        try:
            return float(line.split(" ", 1)[0])
        except ValueError:
            return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--textfile",
        default=os.path.expanduser("~/.local/state/node-exporter-textfile/boot_outcome.prom"),
        help="node_exporter textfile-collector target",
    )
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)

    out = collect(SystemdJournal())
    text = render(out)

    if args.stdout:
        sys.stdout.write(text)
    else:
        write_atomically(args.textfile, text)

    for err in out.errors:
        print(f"boot_outcome: {err}", file=sys.stderr)

    # Exit 0 even when we could not measure: scrape_ok=0 carries that fact to
    # Prometheus, and a failing unit would only add noise on top of a metric
    # that already says "unknown".
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
