"""Unit tests for scripts/initiatives/recap.py — the LLM recap cache (Phase B2).

Fully HERMETIC: no live vLLM, no live Postgres, no kubectl. The LLM client is a fake
object with `.generate()`, and the DB is a fake psycopg2-shaped connection/cursor that
records the SQL. Covers: input-hash stability + change detection, recap_context
extraction, the anti-confabulation prompt, the cached/regenerate-on-change orchestration
(cache hit → no call; change → regenerate + upsert; client failure/empty → cached recap
untouched), the recaps DDL + upsert SQL, config resolution, and the best-effort wrapper's
rollback-on-failure so a model outage can never break the snapshot write."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import recap  # noqa: E402


def _fixture_ini(**over):
    ini = {
        "repo": "/home/zach/workspace/devrc",
        "slug": "initiatives-consolidation",
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
# input_hash — stability + order-independence + change detection
# --------------------------------------------------------------------------- #
def test_input_hash_is_stable_across_identical_context():
    a = recap.input_hash(recap.recap_context(_fixture_ini()))
    b = recap.input_hash(recap.recap_context(_fixture_ini()))
    assert a == b
    assert len(a) == 64  # sha256 hexdigest


def test_input_hash_is_order_independent_for_set_like_fields():
    base = recap.recap_context(_fixture_ini(
        open_investigations=["alpha question", "beta question"],
        open_prs=[{"number": 1, "title": "a"}, {"number": 2, "title": "b"}],
    ))
    reordered = recap.recap_context(_fixture_ini(
        open_investigations=["beta question", "alpha question"],
        open_prs=[{"number": 2, "title": "b"}, {"number": 1, "title": "a"}],
    ))
    assert recap.input_hash(base) == recap.input_hash(reordered)


def test_input_hash_changes_when_a_message_changes():
    before = recap.input_hash(recap.recap_context(_fixture_ini()))
    after = recap.input_hash(recap.recap_context(_fixture_ini(
        recent_messages=[{"text": "a brand new prompt about scope", "ts": 1783944001.0}],
    )))
    assert before != after


def test_input_hash_changes_on_momentum_summary_or_next_step():
    base = recap.input_hash(recap.recap_context(_fixture_ini()))
    assert base != recap.input_hash(recap.recap_context(_fixture_ini(momentum="stalled")))
    assert base != recap.input_hash(recap.recap_context(_fixture_ini(summary="different")))
    assert base != recap.input_hash(recap.recap_context(_fixture_ini(next_step="different")))


def test_input_hash_ignores_message_timestamps_only_text_matters():
    # ts differs but the text is identical → same hash (ts is not "what/where it stands").
    a = recap.input_hash(recap.recap_context(_fixture_ini(
        recent_messages=[{"text": "same text", "ts": 1.0}])))
    b = recap.input_hash(recap.recap_context(_fixture_ini(
        recent_messages=[{"text": "same text", "ts": 999.0}])))
    assert a == b


# --------------------------------------------------------------------------- #
# input_hash — the cache key also folds in the PROMPT and the served MODEL, so a
# prompt edit or a model swap busts the cache and forces a regenerate on next sync.
# --------------------------------------------------------------------------- #
def test_prompt_fingerprint_derives_deterministically_from_system_prompt():
    # The fingerprint is a stable sha256 prefix of SYSTEM_PROMPT — so ANY edit to the
    # prompt text changes the fingerprint (and therefore every input_hash).
    import hashlib
    expected = hashlib.sha256(recap.SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]
    assert recap._PROMPT_FINGERPRINT == expected


def test_input_hash_changes_when_the_prompt_changes(monkeypatch):
    # (a) A change to SYSTEM_PROMPT (surfaced via its fingerprint) changes input_hash for
    # the SAME context → the next sync sees a hash mismatch and regenerates.
    ctx = recap.recap_context(_fixture_ini())
    before = recap.input_hash(ctx)
    monkeypatch.setattr(recap, "_PROMPT_FINGERPRINT", "0000tightenedfp")
    after = recap.input_hash(ctx)
    assert before != after


def test_input_hash_is_stable_when_prompt_and_model_unchanged():
    # (b) Unchanged prompt + model + context → identical hash (a cache hit, no regen).
    a = recap.input_hash(recap.recap_context(_fixture_ini()), model="qwen")
    b = recap.input_hash(recap.recap_context(_fixture_ini()), model="qwen")
    assert a == b


def test_input_hash_changes_when_the_model_changes():
    # A model swap must also bust the cache → regenerate under the new served model.
    ctx = recap.recap_context(_fixture_ini())
    assert recap.input_hash(ctx, model="qwen-7b") != recap.input_hash(ctx, model="qwen-14b")


def test_input_hash_default_model_is_empty_string():
    # No model given == model="" (stable default; the two calls agree).
    ctx = recap.recap_context(_fixture_ini())
    assert recap.input_hash(ctx) == recap.input_hash(ctx, model="")


# --------------------------------------------------------------------------- #
# recap_context — extraction / normalization
# --------------------------------------------------------------------------- #
def test_recap_context_extracts_message_text_and_formats_prs():
    ctx = recap.recap_context(_fixture_ini())
    assert ctx["recent_messages"][0] == "add the recap generator and cache it"
    assert ctx["open_prs"] == ["#146 feat: initiatives recap"]
    assert ctx["momentum"] == "active"


def test_recap_context_tolerates_missing_and_empty_fields():
    ctx = recap.recap_context({})
    assert ctx["summary"] == ""
    assert ctx["open_investigations"] == []
    assert ctx["recent_messages"] == []
    assert ctx["open_prs"] == []


def test_recap_context_caps_messages_and_commits():
    many = [{"text": f"msg {i}", "ts": float(i)} for i in range(20)]
    commits = [f"commit {i}" for i in range(20)]
    ctx = recap.recap_context(_fixture_ini(recent_messages=many, recent_commits=commits))
    assert len(ctx["recent_messages"]) == recap.RECAP_MAX_MESSAGES
    assert len(ctx["recent_commits"]) == recap.RECAP_MAX_COMMITS


# --------------------------------------------------------------------------- #
# build_messages — the anti-confabulation prompt, provided-context-only
# --------------------------------------------------------------------------- #
def test_build_messages_carries_anti_confabulation_contract():
    msgs = recap.build_messages(recap.recap_context(_fixture_ini()))
    assert msgs[0]["role"] == "system"
    sys_text = msgs[0]["content"]
    assert "ANTI-CONFABULATION CONTRACT" in sys_text
    # the non-negotiable prohibitions
    assert "MUST NOT invent" in sys_text
    assert "PR number" in sys_text
    # the shape/style contract
    assert "ONE to TWO sentences" in sys_text
    assert "present tense" in sys_text
    assert "do NOT open with meta" in sys_text


def test_prompt_forbids_describing_the_handoff_doc_not_the_work():
    # (c) The tightened prompt must instruct the model to describe the WORK, never the
    # handoff/doc/markdown file — the fix for recaps like "Supersedes handoff-….md".
    sys_text = recap.build_messages(recap.recap_context(_fixture_ini()))[0]["content"]
    low = sys_text.lower()
    assert "never" in low
    assert "handoff" in low
    assert "supersedes" in low          # the specific meta phrasing to suppress
    assert "documentation" in low
    assert "the work itself" in low     # positive framing: describe the work


def test_build_messages_user_content_includes_only_provided_context():
    ctx = recap.recap_context(_fixture_ini())
    msgs = recap.build_messages(ctx)
    user = msgs[1]["content"]
    assert msgs[1]["role"] == "user"
    # provided context values are present…
    assert "Consolidate the scan output into a durable Postgres store." in user
    assert "add the recap generator and cache it" in user
    assert "#146 feat: initiatives recap" in user
    # …and nothing outside the context object is smuggled in (no repo/slug/title keys
    # that recap_context deliberately omits — the prompt sees ONLY the salient fields).
    assert "initiatives-consolidation" not in user  # slug is NOT in the context
    assert '"repo"' not in user


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

    def fetchone(self):
        return (self._conn.recaps_regclass,)

    def fetchall(self):
        # Only fetch_recaps calls fetchall (the recaps SELECT).
        return list(self._conn.recaps_rows)


class _FakeConn:
    def __init__(self, recaps_rows=(), recaps_regclass="initiatives.recaps"):
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.recaps_rows = recaps_rows
        self.recaps_regclass = recaps_regclass

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeClient:
    def __init__(self, text="A durable Postgres store now backs the initiatives viewer.",
                 raises=None):
        self.text = text
        self.raises = raises
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.text


def _upsert_sqls(conn):
    return [s for s, _ in conn.executed if "INSERT INTO initiatives.recaps" in s]


# --------------------------------------------------------------------------- #
# The recaps DDL + upsert SQL
# --------------------------------------------------------------------------- #
def test_recaps_ddl_creates_table_with_composite_pk():
    ddl = " ".join(recap.RECAPS_DDL.split())
    assert "CREATE TABLE IF NOT EXISTS initiatives.recaps" in ddl
    assert "PRIMARY KEY (repo, slug)" in ddl
    for col in ("repo text", "slug text", "recap text", "input_hash text",
                "model text", "generated_at timestamptz"):
        assert col in ddl


def test_create_recaps_table_executes_the_ddl():
    conn = _FakeConn()
    with conn.cursor() as cur:
        recap.create_recaps_table(cur)
    assert any("CREATE TABLE IF NOT EXISTS initiatives.recaps" in s
               for s, _ in conn.executed)


def test_upsert_recap_uses_on_conflict_do_update():
    conn = _FakeConn()
    with conn.cursor() as cur:
        recap.upsert_recap(cur, "/repo", "slug", "the recap", "deadbeef", "model-x")
    sql, params = conn.executed[-1]
    assert "INSERT INTO initiatives.recaps" in sql
    assert "ON CONFLICT (repo, slug) DO UPDATE" in sql
    assert params == ("/repo", "slug", "the recap", "deadbeef", "model-x")


# --------------------------------------------------------------------------- #
# sync_recaps — cached / regenerate-on-change / best-effort
# --------------------------------------------------------------------------- #
def test_sync_recaps_cache_hit_skips_the_model_and_upsert():
    ini = _fixture_ini()
    ihash = recap.input_hash(recap.recap_context(ini), model="m")
    conn = _FakeConn(recaps_rows=[(ini["repo"], ini["slug"], "cached recap", ihash)])
    client = _FakeClient()
    stats = recap.sync_recaps(conn, [ini], client=client, model="m")
    assert client.calls == 0                 # unchanged hash → no model call
    assert _upsert_sqls(conn) == []          # …and nothing upserted
    assert stats["cached"] == 1
    assert stats["regenerated"] == 0
    assert conn.commits == 1


def test_sync_recaps_regenerates_when_hash_differs():
    ini = _fixture_ini()
    # a stored recap under a STALE hash → must regenerate
    conn = _FakeConn(recaps_rows=[(ini["repo"], ini["slug"], "old recap", "stalehash")])
    client = _FakeClient(text="fresh recap text")
    stats = recap.sync_recaps(conn, [ini], client=client, model="m")
    assert client.calls == 1
    ups = _upsert_sqls(conn)
    assert len(ups) == 1
    assert stats["regenerated"] == 1
    # the upsert carries the freshly-computed hash + the model + the generated text
    _, params = next((s, p) for s, p in conn.executed
                     if "INSERT INTO initiatives.recaps" in s)
    assert params[0] == ini["repo"] and params[1] == ini["slug"]
    assert params[2] == "fresh recap text"
    # the stored hash folds in the served model (the same model sync_recaps ran with)
    assert params[3] == recap.input_hash(recap.recap_context(ini), model="m")
    assert params[4] == "m"


def test_sync_recaps_generates_when_no_cached_recap_exists():
    conn = _FakeConn(recaps_rows=[])  # empty cache
    client = _FakeClient(text="brand new recap")
    stats = recap.sync_recaps(conn, [_fixture_ini()], client=client, model="m")
    assert client.calls == 1
    assert stats["regenerated"] == 1
    assert len(_upsert_sqls(conn)) == 1


def test_sync_recaps_client_failure_leaves_cache_untouched():
    ini = _fixture_ini()
    conn = _FakeConn(recaps_rows=[(ini["repo"], ini["slug"], "old recap", "stalehash")])
    client = _FakeClient(raises=RuntimeError("vllm down"))
    stats = recap.sync_recaps(conn, [ini], client=client, model="m")
    assert client.calls == 1
    assert _upsert_sqls(conn) == []          # NO upsert → cached recap untouched
    assert stats["failed"] == 1
    assert stats["regenerated"] == 0
    assert conn.commits == 1                 # still commits (a no-op set of upserts)


def test_sync_recaps_empty_completion_is_a_failure_not_an_upsert():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(text="   ")          # whitespace-only → treated as failure
    stats = recap.sync_recaps(conn, [_fixture_ini()], client=client, model="m")
    assert _upsert_sqls(conn) == []
    assert stats["failed"] == 1


def test_sync_recaps_skips_rows_missing_repo_or_slug():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient()
    stats = recap.sync_recaps(conn, [{"repo": "/r"}, {"slug": "s"}],
                              client=client, model="m")
    assert stats["skipped"] == 2
    assert client.calls == 0


# --------------------------------------------------------------------------- #
# recap_config — defaults + env overrides + master switch
# --------------------------------------------------------------------------- #
def test_recap_config_defaults_are_disabled_with_placeholders():
    cfg = recap.recap_config(env={})
    assert cfg["enabled"] is False
    assert cfg["namespace"] == recap.RECAP_NAMESPACE
    assert cfg["service"] == recap.RECAP_SERVICE
    assert cfg["model"] == recap.RECAP_MODEL
    assert cfg["service_port"] == recap.RECAP_SERVICE_PORT


def test_recap_config_env_overrides_and_enable():
    cfg = recap.recap_config(env={
        "INITIATIVES_RECAP_ENABLED": "1",
        "RECAP_NAMESPACE": "infer",
        "RECAP_SERVICE": "svc/qwen",
        "RECAP_SERVICE_PORT": "8001",
        "RECAP_MODEL": "Qwen/Qwen2.5-7B-Instruct",
        "RECAP_BASE_URL": "http://10.0.0.5:30080",
        "RECAP_TIMEOUT": "12.5",
    })
    assert cfg["enabled"] is True
    assert cfg["namespace"] == "infer"
    assert cfg["service"] == "svc/qwen"
    assert cfg["service_port"] == 8001
    assert cfg["model"] == "Qwen/Qwen2.5-7B-Instruct"
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
    assert conn.executed == []   # no fetch, no upsert — the model service isn't touched
    assert conn.commits == 0


def test_maybe_sync_recaps_runs_when_enabled_with_injected_client():
    conn = _FakeConn(recaps_rows=[])
    client = _FakeClient(text="a recap")

    class _Factory:
        def __init__(self, cfg):
            self.cfg = cfg
        def __enter__(self):
            return client
        def __exit__(self, *exc):
            return False

    stats = recap.maybe_sync_recaps(
        conn, [_fixture_ini()],
        env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_Factory)
    assert stats["status"] == "ok"
    assert client.calls == 1
    assert len(_upsert_sqls(conn)) == 1


def test_maybe_sync_recaps_swallows_client_construction_failure_and_rolls_back():
    conn = _FakeConn(recaps_rows=[])

    def _boom(cfg):
        raise RuntimeError("port-forward failed")

    stats = recap.maybe_sync_recaps(
        conn, [_fixture_ini()],
        env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_boom)
    assert stats["status"] == "error"
    assert "RuntimeError" in stats["error"]
    assert conn.rollbacks == 1   # connection cleaned so write_snapshot is unaffected


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
        conn, [_fixture_ini()],
        env={"INITIATIVES_RECAP_ENABLED": "1"},
        client_factory=_BadEnter)
    assert stats["status"] == "error"
    assert conn.rollbacks == 1


# --------------------------------------------------------------------------- #
# format_recap_note — the sync's stdout summary fragment
# --------------------------------------------------------------------------- #
def test_format_recap_note_variants():
    assert recap.format_recap_note({"status": "disabled"}) == ", recap off"
    assert "error" in recap.format_recap_note({"status": "error"})
    ok = recap.format_recap_note({"status": "ok", "regenerated": 2, "cached": 9})
    assert ok == ", recap 2 new/9 cached"
    with_fail = recap.format_recap_note(
        {"status": "ok", "regenerated": 1, "cached": 3, "failed": 2})
    assert with_fail == ", recap 1 new/3 cached/2 failed"
