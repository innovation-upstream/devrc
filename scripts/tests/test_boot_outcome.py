"""Coverage for scripts/obs/boot_outcome.py.

The metric this emits is the reason we know the laptop froze six times rather
than the two that were noticed. Its failure mode is therefore NOT "crashes" —
it is "confidently reports zero". Most of what is asserted below is that an
unmeasurable state produces `scrape_ok 0` and OMITS the count, rather than
emitting a reassuring `host_unclean_boots_observed 0`.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts" / "obs" / "boot_outcome.py"

_spec = importlib.util.spec_from_file_location("boot_outcome", MODULE_PATH)
boot_outcome = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec_module: @dataclass resolves its annotations through
# sys.modules[cls.__module__], which is None for an unregistered dynamic module
# once `from __future__ import annotations` makes them strings. Omitting this
# fails at import with a bare AttributeError on NoneType.
sys.modules["boot_outcome"] = boot_outcome
_spec.loader.exec_module(boot_outcome)


CLEAN_TAIL = [
    "Aug 07 17:29:05.017654 nixos systemd[1]: Shutting down.",
    "Aug 07 17:29:05.062434 nixos systemd-journald[758]: Received SIGTERM from PID 1 (systemd-shutdow).",
    "Aug 07 17:29:05.062497 nixos systemd-journald[758]: Journal stopped",
]

# Taken from the shape of the real Aug 20 freeze: ordinary activity, then nothing.
ABRUPT_TAIL = [
    "Aug 20 17:02:27.363269 nixos xsession[3412331]: [312B blob data]",
    "Aug 20 17:02:27.367734 nixos xsession[3412332]: jq: error (at <stdin>:0)",
    "Aug 20 17:02:29.153604 nixos xsession[393625]: DidStartWorkerFail: 15",
]


class FakeJournal:
    def __init__(self, boots, tails=None, timestamps=None, fail_on=()):
        self._boots = boots
        self._tails = tails or {}
        self._timestamps = timestamps or {}
        self._fail_on = fail_on

    def boot_offsets(self):
        if "boot_offsets" in self._fail_on:
            raise RuntimeError("journalctl unavailable")
        return self._boots

    def tail(self, offset, n=12):
        if offset in self._fail_on:
            raise RuntimeError(f"cannot read boot {offset}")
        return self._tails.get(offset, ABRUPT_TAIL)

    def last_timestamp(self, offset):
        return self._timestamps.get(offset)


def parse(text):
    """Prometheus text -> {name: value}, ignoring HELP/TYPE."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, value = line.partition(" ")
        out[name] = value
    return out


# ---- classification ------------------------------------------------------


def test_a_shutdown_sequence_reads_clean():
    assert boot_outcome.ended_cleanly(CLEAN_TAIL) is True


def test_ordinary_activity_then_silence_reads_unclean():
    assert boot_outcome.ended_cleanly(ABRUPT_TAIL) is False


def test_counts_the_real_august_pattern():
    """Three clean, three abrupt — the actual shape of the investigated window."""
    tails = {-6: ABRUPT_TAIL, -5: CLEAN_TAIL, -4: CLEAN_TAIL,
             -3: ABRUPT_TAIL, -2: ABRUPT_TAIL, -1: ABRUPT_TAIL}
    out = boot_outcome.collect(FakeJournal([-6, -5, -4, -3, -2, -1, 0], tails))
    assert out.unclean_count == 4
    assert out.boots_examined == 6
    assert out.previous_clean == 0
    assert out.scrape_ok == 1


def test_the_current_boot_is_not_classified():
    """Boot 0 has not ended; counting it would score every running host unclean."""
    out = boot_outcome.collect(FakeJournal([-1, 0], {-1: CLEAN_TAIL}))
    assert out.boots_examined == 1


# ---- the "confident zero" failure mode -----------------------------------


def test_zero_examined_boots_never_reports_zero_unclean():
    out = boot_outcome.collect(FakeJournal([0]))
    assert out.scrape_ok == 0
    assert out.unclean_count is None, "a scan that walked nothing must not report 0"
    metrics = parse(boot_outcome.render(out))
    assert "host_unclean_boots_observed" not in metrics


def test_journalctl_failure_reports_unmeasured_not_clean():
    out = boot_outcome.collect(FakeJournal([-1, 0], fail_on=("boot_offsets",)))
    assert out.scrape_ok == 0
    assert out.unclean_count is None
    metrics = parse(boot_outcome.render(out))
    assert metrics["host_boot_outcome_scrape_ok"] == "0"
    assert "host_boot_previous_clean" not in metrics


def test_an_unreadable_boot_is_skipped_not_counted_clean():
    """Counting an unreadable boot as clean would hide the events we are counting."""
    tails = {-1: ABRUPT_TAIL, -3: ABRUPT_TAIL}
    out = boot_outcome.collect(FakeJournal([-3, -2, -1, 0], tails, fail_on=(-2,)))
    assert out.boots_examined == 2
    assert out.unclean_count == 2
    assert any("tail(-2)" in e for e in out.errors)


def test_examined_count_is_always_emitted_beside_the_unclean_count():
    """The two numbers are only interpretable together."""
    out = boot_outcome.collect(FakeJournal([-1, 0], {-1: ABRUPT_TAIL}))
    metrics = parse(boot_outcome.render(out))
    assert "host_unclean_boots_observed" in metrics
    assert "host_boots_examined" in metrics


# ---- rendering / writing -------------------------------------------------


def test_render_emits_help_and_type_for_every_metric():
    out = boot_outcome.collect(FakeJournal([-1, 0], {-1: ABRUPT_TAIL}, {-1: 1787263349.0}))
    text = boot_outcome.render(out)
    for name in parse(text):
        assert f"# HELP {name} " in text, f"{name} missing HELP"
        assert f"# TYPE {name} " in text, f"{name} missing TYPE"


def test_timestamp_is_rendered_as_integer_seconds_not_scientific_notation():
    """Prometheus rejects a bare float rendered as 1.787263349e+09."""
    out = boot_outcome.collect(FakeJournal([-1, 0], {-1: ABRUPT_TAIL}, {-1: 1787263349.0}))
    metrics = parse(boot_outcome.render(out))
    assert metrics["host_boot_previous_end_timestamp_seconds"] == "1787263349"


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "sub" / "boot_outcome.prom"
    boot_outcome.write_atomically(str(target), "host_boots_examined 3\n")
    assert target.read_text() == "host_boots_examined 3\n"
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_rewrite_replaces_content_rather_than_appending(tmp_path):
    target = tmp_path / "boot_outcome.prom"
    boot_outcome.write_atomically(str(target), "host_boots_examined 1\n")
    boot_outcome.write_atomically(str(target), "host_boots_examined 2\n")
    assert target.read_text() == "host_boots_examined 2\n"


def test_main_writes_the_textfile(tmp_path, monkeypatch):
    target = tmp_path / "out.prom"
    monkeypatch.setattr(
        boot_outcome, "SystemdJournal",
        lambda *a, **k: FakeJournal([-1, 0], {-1: ABRUPT_TAIL}, {-1: 1787263349.0}),
    )
    assert boot_outcome.main(["--textfile", str(target)]) == 0
    assert parse(target.read_text())["host_boot_previous_clean"] == "0"


@pytest.mark.parametrize("marker", boot_outcome.CLEAN_MARKERS)
def test_each_declared_clean_marker_actually_classifies_clean(marker):
    """A marker in the tuple that never matches is dead config that reads as coverage."""
    assert boot_outcome.ended_cleanly([f"Aug 07 17:29:05 nixos systemd[1]: {marker}"]) is True
