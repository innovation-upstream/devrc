"""Unit tests for reconciliation diff logic + the no-data-skipped path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import reconcile as R  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


# --------------------------------------------------------------------------- #
# Pure diff logic
# --------------------------------------------------------------------------- #
def test_reconcile_sets_perfect_match():
    matched, missing, extra = R.reconcile_sets({"a", "b", "c"}, {"a", "b", "c"})
    assert (matched, missing, extra) == (3, 0, 0)


def test_reconcile_sets_missing_detected():
    # reference has d that collected lacks -> missing (collector gap)
    matched, missing, extra = R.reconcile_sets({"a", "b"}, {"a", "b", "d"})
    assert (matched, missing, extra) == (2, 1, 0)


def test_reconcile_sets_extra_detected():
    # collected has z that reference lacks -> extra
    matched, missing, extra = R.reconcile_sets({"a", "b", "z"}, {"a", "b"})
    assert (matched, missing, extra) == (2, 0, 1)


def test_reconcile_sets_both():
    matched, missing, extra = R.reconcile_sets({"a", "z"}, {"a", "d"})
    assert (matched, missing, extra) == (1, 1, 1)


def test_reconcile_counts():
    assert R.reconcile_counts(5, 5) == (5, 0, 0)
    assert R.reconcile_counts(3, 5) == (3, 2, 0)   # 2 missing
    assert R.reconcile_counts(8, 5) == (5, 0, 3)   # 3 extra


# --------------------------------------------------------------------------- #
# Per-source wiring with a mocked CH client
# --------------------------------------------------------------------------- #
class FakeClient:
    def __init__(self, scalar_val=0, rows_val=None):
        self._scalar = scalar_val
        self._rows = rows_val or []

        class _Conn:
            fq_table = "activity.events"
            url = "http://fake"
        self.conn = _Conn()

    def scalar(self, sql):
        return self._scalar

    def rows(self, sql):
        return self._rows


def test_reconcile_claude_no_data_skipped():
    # no collected claude events AND no jsonl -> skipped, not failed
    client = FakeClient(scalar_val=0)
    r = R.reconcile_claude(client, Path("/nonexistent/projects"), since_epoch=0)
    assert r.skipped is True
    assert "no claude data" in r.reason


def test_reconcile_claude_not_emitting_yet_skipped():
    # reference has prompts but collector emits nothing -> skipped w/ note
    client = FakeClient(scalar_val=0)
    r = R.reconcile_claude(client, FIX, since_epoch=0)
    assert r.skipped is True
    assert "not yet emitting" in r.reason


def test_reconcile_claude_counts_when_both_present():
    # collected 3 claude events; reference jsonl has 3 real prompts -> matched
    client = FakeClient(scalar_val=3)
    r = R.reconcile_claude(client, FIX, since_epoch=0)
    assert r.skipped is False
    assert r.collected == 3
    assert r.matched == 3
    assert r.missing == 0 and r.extra == 0


def test_reconcile_zsh_missing_detected():
    # reference (plain histfile) has commands; collector recorded only a subset.
    client = FakeClient(rows_val=[{"text": "git status"}])
    r = R.reconcile_zsh(client, FIX / "zsh_history_plain.txt", since_epoch=0)
    assert r.skipped is False
    assert r.matched == 1
    assert r.missing >= 1  # other histfile commands not collected


def test_reconcile_zsh_no_data_skipped():
    client = FakeClient(rows_val=[])
    r = R.reconcile_zsh(client, Path("/nonexistent/.zsh_history"), since_epoch=0)
    assert r.skipped is True


def test_reconcile_browser_with_fixture():
    # reference DB has 3 urls (1 old, filtered by since); collected matches one.
    client = FakeClient(rows_val=[{"text": "https://example.com/a"}])
    r = R.reconcile_browser(client, FIX / "chrome_history.sqlite", since_epoch=0)
    assert r.skipped is False
    assert r.matched == 1
    assert r.missing == 2  # b and old.x present in ref, not collected


def test_reconcile_browser_query_error_skipped():
    class BoomClient(FakeClient):
        def rows(self, sql):
            raise RuntimeError("refused")
    r = R.reconcile_browser(BoomClient(), FIX / "chrome_history.sqlite", since_epoch=0)
    assert r.skipped is True
    assert "error" in r.reason


def test_recon_line_formatting():
    r = R.Recon("zsh", collected=5, reference=7, matched=5, missing=2, extra=0)
    line = r.line()
    assert "zsh" in line and "matched=5" in line and "missing=2" in line
    s = R.Recon("claude", skipped=True, reason="no data")
    assert "SKIPPED" in s.line()
