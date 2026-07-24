"""Unit tests for scripts/initiatives/recap.py — the LLM recap cache (Phase B2), split
into an independently-sourced + independently-cached IDENTITY ("what it is", from the
handoff description) and STATUS ("what's happening now", from recent activity).

Fully HERMETIC: no live vLLM, no live Postgres, no kubectl. The LLM client is a fake that
routes by system prompt (identity vs status), the DB is a fake psycopg2-shaped
connection/cursor that records the SQL, and the handoff read is either injected
(`blob_reader`) or exercised against a tmp file. Covers: identity-blob extraction, the two
contexts, the two INDEPENDENT cache keys, the two prompts, the per-field cached/
regenerate-on-change orchestration, best-effort fallback, the additive DDL + upsert SQL,
config, and the best-effort wrapper's rollback-on-failure. Includes the remix regression
(handoff says "video platform", recent messages say "cloudflare" → identity is the
platform, cloudflare only in status)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recap  # noqa: E402


# A handoff doc whose durable head describes a video-remix PLATFORM (never "cloudflare"),
# with a volatile `## Status` section that must NOT leak into the identity blob.
REMIX_HANDOFF = """# Handoff — remix-session, 2026-07-22

**App:** a video-remix platform (containers/remix/…), public at aigeum.com, where users
Explore clips, Stash favourites, and run the render loop; includes moderation.

## Status — recent work

Active development focusing on cloudflare reliance in the render pipeline.

## Next steps
1. reduce cloudflare reliance
"""


def _fixture_ini(**over):
    ini = {
        "repo": "/home/zach/workspace/devrc",
        "slug": "initiatives-consolidation",
        "current_doc": "/home/zach/workspace/devrc/claudedocs/handoff-x.md",
        "momentum": "active",
        "summary": "Consolidate the scan output into a durable Postgres store.",
        "next_step": "wire the recap generator into the sync",
        "open_investigations": ["does the router want a JOIN view?"],
        "recent_messages": [
            {"text": "add the recap generator and cache it", "ts": 1783944000.0},
            {"text": "eyeball the dry-run before writing", "ts": 1783857600.0},
        ],
        "recent_commits": ["feat: recaps table", "fix: dedupe pooled turns"],
        "open_prs": [{"number": 146, "title": "feat: initiatives recap"}],
    }
    ini.update(over)
    return ini


# --------------------------------------------------------------------------- #
# identity_blob — durable head extraction (stops at the first volatile heading)
# --------------------------------------------------------------------------- #
def test_identity_blob_keeps_title_and_opening_stops_at_status():
    blob = recap.identity_blob(REMIX_HANDOFF)
    assert "video-remix platform" in blob
    assert "aigeum.com" in blob
    # the volatile Status / Next-steps sections (with "cloudflare") are EXCLUDED
    assert "cloudflare" not in blob.lower()
    assert "Status" not in blob
    assert "Next steps" not in blob


def test_identity_blob_keeps_durable_section_before_status():
    text = (
        "# Title — 2026-07-22\n\n"
        "One-line intro.\n\n"
        "## Overview\n\nThe enduring system does X and Y.\n\n"
        "## Status\n\nthis week we did Z.\n"
    )
    blob = recap.identity_blob(text)
    assert "The enduring system does X and Y." in blob   # durable ## Overview kept
    assert "this week we did Z." not in blob             # volatile ## Status dropped


def test_identity_blob_caps_length():
    text = "# T\n\n" + ("word " * 5000)
    blob = recap.identity_blob(text, max_chars=200)
    assert len(blob) <= 200


def test_identity_blob_empty_for_blank():
    assert recap.identity_blob("") == ""
    assert recap.identity_blob("   \n  \n") == ""


def test_identity_blob_handles_doc_opening_with_status_heading():
    # A doc that opens straight into a status heading: don't return empty — keep scanning
    # for the real description that follows.
    text = "## Status\n\nblah\n\n# Real Title\n\nThe real durable description.\n"
    blob = recap.identity_blob(text)
    assert "The real durable description." in blob


# --------------------------------------------------------------------------- #
# contexts — identity sees ONLY the blob; status sees ONLY activity
# --------------------------------------------------------------------------- #
def test_identity_context_is_only_the_handoff_blob():
    ctx = recap.identity_context("the durable description")
    assert ctx == {"handoff": "the durable description"}
    assert "recent_messages" not in ctx and "momentum" not in ctx


def test_status_context_excludes_handoff_and_summary_includes_activity():
    ctx = recap.status_context(_fixture_ini())
    assert ctx["momentum"] == "active"
    assert ctx["next_step"] == "wire the recap generator into the sync"
    assert ctx["recent_messages"][0] == "add the recap generator and cache it"
    assert ctx["recent_commits"] == ["feat: recaps table", "fix: dedupe pooled turns"]
    assert ctx["open_prs"] == ["#146 feat: initiatives recap"]
    # the durable "what it is" fields are NOT part of status
    assert "handoff" not in ctx
    assert "summary" not in ctx


def test_status_context_caps_messages_and_commits():
    many = [{"text": f"msg {i}", "ts": float(i)} for i in range(20)]
    commits = [f"commit {i}" for i in range(20)]
    ctx = recap.status_context(_fixture_ini(recent_messages=many, recent_commits=commits))
    assert len(ctx["recent_messages"]) == recap.RECAP_MAX_MESSAGES
    assert len(ctx["recent_commits"]) == recap.RECAP_MAX_COMMITS


def test_status_context_tolerates_missing_and_empty_fields():
    ctx = recap.status_context({})
    assert ctx["momentum"] == ""
    assert ctx["recent_messages"] == []
    assert ctx["open_prs"] == []


# --------------------------------------------------------------------------- #
# hashes — the two cache keys are INDEPENDENT (the core fix)
# --------------------------------------------------------------------------- #
def test_identity_hash_depends_only_on_the_blob():
    a = recap.identity_hash(recap.identity_context("blob one"))
    b = recap.identity_hash(recap.identity_context("blob one"))
    c = recap.identity_hash(recap.identity_context("blob two"))
    assert a == b and a != c and len(a) == 64


def test_identity_hash_ignores_activity_changes():
    # Same handoff blob → identical identity hash regardless of recent activity. This is
    # what keeps identity CACHED across prompt churn.
    blob = recap.identity_blob(REMIX_HANDOFF)
    h = recap.identity_hash(recap.identity_context(blob), model="m")
    # (identity_hash has no way to even SEE activity — it takes only the blob context)
    assert h == recap.identity_hash(recap.identity_context(blob), model="m")


def test_status_hash_changes_when_a_message_changes():
    before = recap.status_hash(recap.status_context(_fixture_ini()))
    after = recap.status_hash(recap.status_context(_fixture_ini(
        recent_messages=[{"text": "a brand new prompt about scope", "ts": 1.0}])))
    assert before != after


def test_status_hash_ignores_the_handoff_blob():
    # Status is computed from the row's activity fields only — it has no blob input, so a
    # handoff edit can NEVER move it. This keeps status CACHED across handoff edits.
    a = recap.status_hash(recap.status_context(_fixture_ini()), model="m")
    b = recap.status_hash(recap.status_context(_fixture_ini()), model="m")
    assert a == b


def test_status_hash_is_order_independent_for_set_like_fields():
    base = recap.status_hash(recap.status_context(_fixture_ini(
        open_investigations=["alpha", "beta"],
        open_prs=[{"number": 1, "title": "a"}, {"number": 2, "title": "b"}])))
    reordered = recap.status_hash(recap.status_context(_fixture_ini(
        open_investigations=["beta", "alpha"],
        open_prs=[{"number": 2, "title": "b"}, {"number": 1, "title": "a"}])))
    assert base == reordered


def test_status_hash_ignores_message_timestamps_only_text_matters():
    a = recap.status_hash(recap.status_context(_fixture_ini(
        recent_messages=[{"text": "same text", "ts": 1.0}])))
    b = recap.status_hash(recap.status_context(_fixture_ini(
        recent_messages=[{"text": "same text", "ts": 999.0}])))
    assert a == b


def test_hashes_fold_prompt_fingerprint_and_model(monkeypatch):
    blob = recap.identity_blob(REMIX_HANDOFF)
    id_ctx, st_ctx = recap.identity_context(blob), recap.status_context(_fixture_ini())
    # model swap busts both caches
    assert recap.identity_hash(id_ctx, "a") != recap.identity_hash(id_ctx, "b")
    assert recap.status_hash(st_ctx, "a") != recap.status_hash(st_ctx, "b")
    # a prompt edit (surfaced via its fingerprint) busts the corresponding cache
    before_id = recap.identity_hash(id_ctx)
    monkeypatch.setattr(recap, "_IDENTITY_PROMPT_FINGERPRINT", "0000tightenedfp")
    assert recap.identity_hash(id_ctx) != before_id


def test_prompt_fingerprints_derive_from_their_system_prompts():
    import hashlib
    assert recap._IDENTITY_PROMPT_FINGERPRINT == \
        hashlib.sha256(recap.IDENTITY_SYSTEM_PROMPT.encode()).hexdigest()[:16]
    assert recap._STATUS_PROMPT_FINGERPRINT == \
        hashlib.sha256(recap.STATUS_SYSTEM_PROMPT.encode()).hexdigest()[:16]
    # the two prompts are distinct → distinct fingerprints
    assert recap._IDENTITY_PROMPT_FINGERPRINT != recap._STATUS_PROMPT_FINGERPRINT


# --------------------------------------------------------------------------- #
# prompts — identity from the handoff (not prompts); status from activity
# --------------------------------------------------------------------------- #
def test_identity_prompt_states_what_it_is_and_forbids_tangential_and_doc_meta():
    sys_text = recap.IDENTITY_SYSTEM_PROMPT
    low = sys_text.lower()
    assert "one to two sentences" in low
    assert "fundamentally is" in low
    # the core fix: don't mistake a recent/tangential workstream for the purpose
    assert "tangential" in low
    # keep the anti-confab + anti-doc-meta discipline
    assert "ANTI-CONFABULATION CONTRACT" in sys_text
    assert "handoff" in low and "supersedes" in low and "the work itself" in low


def test_status_prompt_states_current_status_and_not_what_it_is():
    sys_text = recap.STATUS_SYSTEM_PROMPT
    low = sys_text.lower()
    assert "current status" in low
    assert "in progress" in low and "blocked" in low
    assert "do not restate what the project is" in low
    assert "ANTI-CONFABULATION CONTRACT" in sys_text


def test_build_identity_messages_uses_the_blob_only():
    msgs = recap.build_identity_messages(recap.identity_context("A durable description."))
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == recap.IDENTITY_SYSTEM_PROMPT
    assert msgs[1]["role"] == "user"
    assert "A durable description." in msgs[1]["content"]
    # recent prompts are NOT smuggled into the identity call
    assert "recent_messages" not in msgs[1]["content"]


def test_build_status_messages_uses_activity_json_not_the_blob():
    msgs = recap.build_status_messages(recap.status_context(_fixture_ini()))
    assert msgs[0]["content"] == recap.STATUS_SYSTEM_PROMPT
    user = msgs[1]["content"]
    assert "add the recap generator and cache it" in user  # activity present
    assert "#146 feat: initiatives recap" in user
    assert "handoff" not in user.lower()                   # no blob


# --------------------------------------------------------------------------- #
# read_identity_blob — the on-box, size-capped, traversal-guarded handoff read
# --------------------------------------------------------------------------- #
def test_read_identity_blob_reads_and_extracts(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    doc = repo / "claudedocs" / "handoff.md"
    doc.write_text("# Title — 2026-07-22\n\n**Goal:** build the thing.\n\n## Status\n\nz\n")
    blob = recap.read_identity_blob(str(repo), str(doc))
    assert "build the thing" in blob
    assert "Status" not in blob and "z" not in blob


def test_read_identity_blob_rejects_traversal(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    outside = tmp_path / "secret.md"
    outside.write_text("secret material")
    assert recap.read_identity_blob(str(repo), str(outside)) == ""


def test_read_identity_blob_missing_file_returns_empty(tmp_path):
    repo = tmp_path / "repo"
    (repo / "claudedocs").mkdir(parents=True)
    assert recap.read_identity_blob(str(repo), str(repo / "claudedocs" / "nope.md")) == ""
    assert recap.read_identity_blob("", "") == ""


# --------------------------------------------------------------------------- #
# Fakes for the DB + client (no psycopg2, no port-forward, no HTTP)
# --------------------------------------------------------------------------- #
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return list(self._conn.recaps_rows)


class _FakeConn:
    def __init__(self, recaps_rows=()):
        # rows are (repo, slug, identity, identity_hash, status, status_hash)
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.recaps_rows = recaps_rows

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeClient:
    """Routes generate() by the system prompt so ONE client serves both calls; records
    what it saw. `raises` (if set) makes every call fail."""

    def __init__(self, identity="IDENTITY text.", status="STATUS text.", raises=None):
        self.identity = identity
        self.status = status
        self.raises = raises
        self.identity_calls = 0
        self.status_calls = 0
        self.identity_user = None
        self.status_user = None

    def generate(self, messages):
        if self.raises is not None:
            raise self.raises
        if messages[0]["content"] == recap.IDENTITY_SYSTEM_PROMPT:
            self.identity_calls += 1
            self.identity_user = messages[1]["content"]
            return self.identity
        self.status_calls += 1
        self.status_user = messages[1]["content"]
        return self.status

    @property
    def calls(self):
        return self.identity_calls + self.status_calls


def _upserts(conn):
    return [(s, p) for s, p in conn.executed if "INSERT INTO initiatives.recaps" in s]


def _blob(text):
    """A blob_reader that returns a fixed blob regardless of (repo, current_doc)."""
    return lambda repo, doc: text


# --------------------------------------------------------------------------- #
# The additive DDL + upsert SQL + fetch shape
# --------------------------------------------------------------------------- #
def test_recaps_ddl_has_identity_status_columns_and_additive_alters():
    ddl = " ".join(recap.RECAPS_DDL.split())
    assert "CREATE TABLE IF NOT EXISTS initiatives.recaps" in ddl
    assert "PRIMARY KEY (repo, slug)" in ddl
    # additive columns present in the CREATE …
    for col in ("identity text", "identity_hash text", "status text", "status_hash text"):
        assert col in ddl
    # … AND as idempotent ADD COLUMN IF NOT EXISTS for pre-existing installs
    for col in ("identity", "identity_hash", "status", "status_hash"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in ddl
    # the legacy columns are KEPT for back-compat
    assert "recap text" in ddl and "input_hash text" in ddl
    # NO view migration — this is a standalone table (no view DDL here)
    assert "CREATE VIEW" not in ddl and "CREATE OR REPLACE VIEW" not in ddl


def test_upsert_recap_writes_identity_status_and_mirrors_recap():
    conn = _FakeConn()
    with conn.cursor() as cur:
        recap.upsert_recap(cur, "/repo", "slug", identity="what it is",
                           identity_hash="idh", status="what's now", status_hash="sth",
                           model="m")
    sql, params = conn.executed[-1]
    assert "INSERT INTO initiatives.recaps" in sql
    assert "ON CONFLICT (repo, slug) DO UPDATE" in sql
    # (repo, slug, recap, input_hash, identity, identity_hash, status, status_hash, model)
    assert params == ("/repo", "slug", "what it is", "idh", "what it is", "idh",
                      "what's now", "sth", "m")
    # legacy recap/input_hash MIRROR identity/identity_hash
    assert params[2] == params[4] and params[3] == params[5]


def test_fetch_recaps_returns_identity_status_hashes():
    conn = _FakeConn(recaps_rows=[("/r", "s", "ident", "idh", "stat", "sth")])
    out = recap.fetch_recaps(conn)
    assert out[("/r", "s")] == {"identity": "ident", "identity_hash": "idh",
                                "status": "stat", "status_hash": "sth"}


# --------------------------------------------------------------------------- #
# sync_recaps — per-field cached / regenerate-on-change / best-effort
# --------------------------------------------------------------------------- #
def test_sync_recaps_both_cache_hit_skips_the_model():
    ini = _fixture_ini()
    blob = recap.identity_blob(REMIX_HANDOFF)
    id_h = recap.identity_hash(recap.identity_context(blob), model="m")
    st_h = recap.status_hash(recap.status_context(ini), model="m")
    conn = _FakeConn(recaps_rows=[(ini["repo"], ini["slug"], "ID", id_h, "ST", st_h)])
    client = _FakeClient()
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(blob))
    assert client.calls == 0
    assert _upserts(conn) == []
    assert stats["cached"] == 1
    assert stats["identity_new"] == 0 and stats["status_new"] == 0
    assert conn.commits == 1


def test_sync_recaps_message_change_regens_status_keeps_identity_cached():
    # THE independence test: a changed recent message busts status_hash but NOT
    # identity_hash → status regenerates, identity is served from cache (no model call).
    ini = _fixture_ini(recent_messages=[{"text": "a NEW prompt", "ts": 9.0}])
    blob = recap.identity_blob(REMIX_HANDOFF)
    id_h = recap.identity_hash(recap.identity_context(blob), model="m")
    stale_st_h = recap.status_hash(recap.status_context(_fixture_ini()), model="m")  # OLD
    conn = _FakeConn(recaps_rows=[
        (ini["repo"], ini["slug"], "cached identity", id_h, "old status", stale_st_h)])
    client = _FakeClient(status="fresh status")
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(blob))
    assert client.identity_calls == 0        # identity CACHED across prompt churn
    assert client.status_calls == 1          # status regenerated
    assert stats["identity_new"] == 0 and stats["status_new"] == 1
    ups = _upserts(conn)
    assert len(ups) == 1
    _, p = ups[0]
    # identity + identity_hash carried through UNCHANGED; status is the fresh text
    assert p[4] == "cached identity" and p[5] == id_h
    assert p[6] == "fresh status" and p[7] == recap.status_hash(
        recap.status_context(ini), model="m")


def test_sync_recaps_handoff_change_regens_identity_keeps_status_cached():
    # The mirror: a changed handoff blob busts identity_hash but NOT status_hash → identity
    # regenerates, status is served from cache.
    ini = _fixture_ini()
    new_blob = "A brand new durable description of the system."
    stale_id_h = recap.identity_hash(recap.identity_context("OLD blob"), model="m")
    st_h = recap.status_hash(recap.status_context(ini), model="m")
    conn = _FakeConn(recaps_rows=[
        (ini["repo"], ini["slug"], "old identity", stale_id_h, "cached status", st_h)])
    client = _FakeClient(identity="fresh identity")
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(new_blob))
    assert client.identity_calls == 1        # identity regenerated on the handoff change
    assert client.status_calls == 0          # status CACHED across the handoff edit
    assert stats["identity_new"] == 1 and stats["status_new"] == 0
    _, p = _upserts(conn)[0]
    assert p[4] == "fresh identity"
    assert p[6] == "cached status" and p[7] == st_h   # status carried unchanged


def test_sync_recaps_generates_both_when_no_cache():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(identity="new id", status="new st")
    stats = recap.sync_recaps(conn, [_fixture_ini()], client=client, model="m",
                              blob_reader=_blob("a durable description"))
    assert client.identity_calls == 1 and client.status_calls == 1
    assert stats["identity_new"] == 1 and stats["status_new"] == 1
    assert len(_upserts(conn)) == 1


def test_sync_recaps_no_current_doc_skips_identity_generates_status():
    # No handoff blob (empty read) → identity is NOT generated (viewer falls back to
    # summary); status still generates. The sync still writes.
    ini = _fixture_ini()
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(status="just the status")
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(""))   # empty blob
    assert client.identity_calls == 0
    assert client.status_calls == 1
    assert stats["identity_new"] == 0 and stats["status_new"] == 1
    _, p = _upserts(conn)[0]
    assert p[4] is None                # identity stays None → viewer uses summary
    assert p[6] == "just the status"


def test_sync_recaps_client_failure_leaves_cache_untouched():
    ini = _fixture_ini()
    blob = recap.identity_blob(REMIX_HANDOFF)
    conn = _FakeConn(recaps_rows=[
        (ini["repo"], ini["slug"], "old id", "staleidh", "old st", "stalesth")])
    client = _FakeClient(raises=RuntimeError("vllm down"))
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(blob))
    assert _upserts(conn) == []              # NO upsert → cached values untouched
    assert stats["failed"] == 2              # both identity + status attempts failed
    assert stats["identity_new"] == 0 and stats["status_new"] == 0
    assert conn.commits == 1


def test_sync_recaps_identity_failure_still_persists_status():
    # Identity model call fails but status succeeds → we still upsert (to store status);
    # identity keeps its last-good (or None) so the next sync retries it.
    ini = _fixture_ini()
    blob = recap.identity_blob(REMIX_HANDOFF)

    class _IdFailsClient(_FakeClient):
        def generate(self, messages):
            if messages[0]["content"] == recap.IDENTITY_SYSTEM_PROMPT:
                raise RuntimeError("identity gen failed")
            return super().generate(messages)

    conn = _FakeConn(recaps_rows=[])
    client = _IdFailsClient(status="the status held")
    stats = recap.sync_recaps(conn, [ini], client=client, model="m",
                              blob_reader=_blob(blob))
    assert stats["failed"] == 1 and stats["status_new"] == 1
    _, p = _upserts(conn)[0]
    assert p[4] is None                 # identity absent → viewer falls back to summary
    assert p[6] == "the status held"


def test_sync_recaps_empty_completion_is_a_failure_not_an_upsert():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(identity="   ", status="   ")   # whitespace-only → failures
    stats = recap.sync_recaps(conn, [_fixture_ini()], client=client, model="m",
                              blob_reader=_blob("a durable description"))
    assert _upserts(conn) == []
    assert stats["failed"] == 2


def test_sync_recaps_skips_rows_missing_repo_or_slug():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient()
    stats = recap.sync_recaps(conn, [{"repo": "/r"}, {"slug": "s"}],
                              client=client, model="m", blob_reader=_blob("x"))
    assert stats["skipped"] == 2
    assert client.calls == 0


# --------------------------------------------------------------------------- #
# Regression — the remix case (handoff = platform; recent messages = cloudflare)
# --------------------------------------------------------------------------- #
def test_regression_remix_identity_is_the_platform_cloudflare_only_in_status():
    ini = _fixture_ini(
        slug="remix-session",
        summary="Active development focusing on cloudflare reliance.",
        recent_messages=[{"text": "reduce cloudflare reliance in the render path",
                          "ts": 1.0}],
        recent_commits=["chore: cloudflare tweak"])
    blob = recap.identity_blob(REMIX_HANDOFF)   # about the video-remix platform
    client = _FakeClient(
        identity="A video-remix platform where users explore, stash, and render clips.",
        status="reducing cloudflare reliance in the render pipeline.")
    conn = _FakeConn(recaps_rows=[])
    recap.sync_recaps(conn, [ini], client=client, model="m", blob_reader=_blob(blob))

    # the IDENTITY model call saw the platform blob and NOT the cloudflare prompts
    assert "video-remix platform" in client.identity_user
    assert "cloudflare" not in client.identity_user.lower()
    # the STATUS model call saw the cloudflare activity
    assert "cloudflare" in client.status_user.lower()

    _, p = _upserts(conn)[0]
    identity_written, status_written = p[4], p[6]
    assert "video-remix platform" in identity_written
    assert "cloudflare" not in identity_written.lower()   # identity is the PLATFORM
    assert "cloudflare" in status_written.lower()          # cloudflare only in STATUS


# --------------------------------------------------------------------------- #
# recap_config — defaults + env overrides + master switch (unchanged)
# --------------------------------------------------------------------------- #
def test_recap_config_defaults_are_disabled_with_placeholders():
    cfg = recap.recap_config(env={})
    assert cfg["enabled"] is False
    assert cfg["namespace"] == recap.RECAP_NAMESPACE
    assert cfg["service"] == recap.RECAP_SERVICE
    assert cfg["model"] == recap.RECAP_MODEL


def test_recap_config_env_overrides_and_enable():
    cfg = recap.recap_config(env={
        "INITIATIVES_RECAP_ENABLED": "1",
        "RECAP_NAMESPACE": "promptver",
        "RECAP_SERVICE": "svc/vllm-recap",
        "RECAP_SERVICE_PORT": "8000",
        "RECAP_MODEL": "recap",
        "RECAP_BASE_URL": "http://10.0.0.5:30080",
        "RECAP_TIMEOUT": "12.5",
    })
    assert cfg["enabled"] is True
    assert cfg["namespace"] == "promptver"
    assert cfg["service"] == "svc/vllm-recap"
    assert cfg["model"] == "recap"
    assert cfg["base_url"] == "http://10.0.0.5:30080"
    assert cfg["timeout"] == 12.5


def test_recap_config_enabled_truthy_variants():
    for val in ("1", "true", "YES", "on", "True"):
        assert recap.recap_config(env={"INITIATIVES_RECAP_ENABLED": val})["enabled"]
    for val in ("", "0", "false", "no", "off"):
        assert not recap.recap_config(env={"INITIATIVES_RECAP_ENABLED": val})["enabled"]


# --------------------------------------------------------------------------- #
# maybe_sync_recaps — the best-effort wrapper (never breaks the sync)
# --------------------------------------------------------------------------- #
def test_maybe_sync_recaps_disabled_by_default_is_a_noop():
    conn = _FakeConn()
    stats = recap.maybe_sync_recaps(conn, [_fixture_ini()], env={})
    assert stats["status"] == "disabled"
    assert conn.executed == []
    assert conn.commits == 0


def test_maybe_sync_recaps_runs_when_enabled_with_injected_client(monkeypatch):
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(identity="an identity", status="a status")
    # avoid touching the disk in the wrapper path (no blob_reader arg on maybe_sync_recaps)
    monkeypatch.setattr(recap, "read_identity_blob", lambda repo, doc: "a durable desc")

    class _Factory:
        def __init__(self, cfg):
            self.cfg = cfg

        def __enter__(self):
            return client

        def __exit__(self, *exc):
            return False

    stats = recap.maybe_sync_recaps(
        conn, [_fixture_ini()], env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_Factory)
    assert stats["status"] == "ok"
    assert client.identity_calls == 1 and client.status_calls == 1
    assert len(_upserts(conn)) == 1


def test_maybe_sync_recaps_swallows_client_construction_failure_and_rolls_back():
    conn = _FakeConn(recaps_rows=[])

    def _boom(cfg):
        raise RuntimeError("port-forward failed")

    stats = recap.maybe_sync_recaps(
        conn, [_fixture_ini()], env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_boom)
    assert stats["status"] == "error"
    assert "RuntimeError" in stats["error"]
    assert conn.rollbacks == 1


def test_maybe_sync_recaps_swallows_context_enter_failure():
    conn = _FakeConn(recaps_rows=[])

    class _BadEnter:
        def __init__(self, cfg):
            pass

        def __enter__(self):
            raise TimeoutError("vLLM not ready")

        def __exit__(self, *exc):
            return False

    stats = recap.maybe_sync_recaps(
        conn, [_fixture_ini()], env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_BadEnter)
    assert stats["status"] == "error"
    assert conn.rollbacks == 1


# --------------------------------------------------------------------------- #
# format_recap_note — the sync's stdout summary fragment (new id/st shape)
# --------------------------------------------------------------------------- #
def test_format_recap_note_variants():
    assert recap.format_recap_note({"status": "disabled"}) == ", recap off"
    assert "error" in recap.format_recap_note({"status": "error"})
    ok = recap.format_recap_note(
        {"status": "ok", "identity_new": 2, "status_new": 3, "cached": 9})
    assert ok == ", recap 2 id/3 st new, 9 cached"
    with_fail = recap.format_recap_note(
        {"status": "ok", "identity_new": 1, "status_new": 0, "cached": 3, "failed": 2})
    assert with_fail == ", recap 1 id/0 st new, 3 cached, 2 failed"
