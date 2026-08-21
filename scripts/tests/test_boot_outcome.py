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


# SYNTHETIC, like ABRUPT_TAIL below — regenerated rather than pasted from this
# host's journal, for the same public-repo reason.
CLEAN_TAIL = [
    "Jan 01 00:00:01.000000 host systemd[1]: Shutting down.",
    "Jan 01 00:00:02.000000 host systemd-journald[100]: Received SIGTERM from PID 1 (systemd-shutdow).",
    "Jan 01 00:00:03.000000 host systemd-journald[100]: Journal stopped",
]

# SYNTHETIC, matching the SHAPE of a real freeze tail: ordinary application
# chatter, then silence — no shutdown sequence. Regenerated rather than pasted
# from this host's journal: CLAUDE.md forbids committing captured text to this
# public repo, and the gates that enforce it cover .json/.html but not .py, so
# the rule has to be honoured by hand here.
ABRUPT_TAIL = [
    "Jan 01 00:00:01.000000 host app[1000]: fetched 3 items",
    "Jan 01 00:00:02.000000 host app[1000]: cache warm, 12 entries",
    "Jan 01 00:00:03.000000 host app[1001]: worker heartbeat ok",
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
    assert boot_outcome.ended_cleanly([f"Jan 01 00:00:00 host systemd[1]: {marker}"]) is True


def test_render_output_ends_with_a_newline():
    """node_exporter's textfile parser rejects a file with no trailing newline
    ('unexpected end of input stream') and then drops EVERY metric in it — the
    freeze metrics vanish silently, which looks just like a host that has not
    frozen. Measured against node_exporter 1.12.1."""
    out = boot_outcome.collect(FakeJournal([-1, 0], {-1: ABRUPT_TAIL}))
    assert boot_outcome.render(out).endswith("\n")


def test_a_previous_boot_read_failure_is_reported_as_unmeasured():
    """If the previous boot cannot be read we know nothing about the event we
    most care about, so scrape_ok must drop — silently omitting the metric while
    still claiming success would read as 'no freeze'."""

    class OnlyPreviousFails(FakeJournal):
        def tail(self, offset, n=12):
            if offset == -1:
                raise RuntimeError("boom")
            return CLEAN_TAIL

    out = boot_outcome.collect(OnlyPreviousFails([-2, -1, 0]))
    assert out.scrape_ok == 0
    assert out.previous_clean is None
    assert "host_boot_previous_clean" not in parse(boot_outcome.render(out))


# ---- the real journalctl adapter ----------------------------------------- #
# Previously monkeypatched away in every test, so none of the argv or parsing
# below was covered: mutating the `index` field name, or `-n <n>` to `-n 1`,
# left the suite green while the deployed adapter was broken.


def _recording_journal(responses):
    calls = []

    def runner(argv):
        calls.append(argv)
        for needle, out in responses.items():
            if needle in argv:
                return out
        return ""

    return boot_outcome.SystemdJournal(runner=runner), calls


def test_boot_offsets_parses_the_index_field_from_json():
    journal, calls = _recording_journal({"--list-boots": '[{"index":-1},{"index":0}]'})
    assert journal.boot_offsets() == [-1, 0]
    assert ["journalctl", "--list-boots", "-o", "json"] == calls[0]


def test_tail_requests_the_configured_number_of_lines_for_that_boot():
    journal, calls = _recording_journal({"-b": "line1\nline2\n"})
    journal.tail(-3, 12)
    argv = calls[0]
    assert argv[argv.index("-b") + 1] == "-3"
    assert argv[argv.index("-n") + 1] == "12", "must request N lines, not a fixed 1"


def test_tail_defaults_to_a_dozen_lines():
    """LITERAL, not `str(boot_outcome.TAIL_LINES)`.

    Asserting against the constant makes both sides of the comparison move
    together, so it cannot fail — measured: shrinking TAIL_LINES to 1 SURVIVED a
    fully green run. Reading one line is a real defect: systemd's shutdown
    markers are not reliably the FINAL journal entry (udev and journald
    teardown lines routinely follow 'Journal stopped'), so a one-line tail
    misclassifies clean shutdowns as freezes.
    """
    journal, calls = _recording_journal({"-b": ""})
    journal.tail(-1)
    argv = calls[0]
    assert argv[argv.index("-n") + 1] == "12"
    assert boot_outcome.TAIL_LINES >= 10, "too few lines to span a shutdown sequence"


def test_last_timestamp_parses_the_short_unix_format():
    journal, calls = _recording_journal({"-b": "1787263349.153604 host app[1]: msg\n"})
    assert journal.last_timestamp(-1) == pytest.approx(1787263349.153604)
    assert "short-unix" in calls[0]


def test_last_timestamp_returns_none_rather_than_raising_on_junk():
    journal, _ = _recording_journal({"-b": "not-a-timestamp\n"})
    assert journal.last_timestamp(-1) is None
