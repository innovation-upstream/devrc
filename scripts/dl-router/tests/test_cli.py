"""The `dl-route` CLI -- 287 lines that had ZERO coverage.

It is the only interface to the backfill, the alias table and the token, so
"it parses" is not enough: these drive each subcommand end to end against temp
config, a temp library and a stub sidecar.

The module has no `.py` suffix (it is an executable on PATH), so it is loaded
by explicit path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import config as config_mod  # noqa: E402
import server as S  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from store import Store  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_loader(
        "dl_route_cli",
        importlib.machinery.SourceFileLoader("dl_route_cli",
                                             str(HERE.parent / "dl-route")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


@pytest.fixture
def cli_env(tmp_path, library, dirs_file, monkeypatch):
    """Point the CLI's config loader entirely at temp paths."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'library_root = "{library}"\nport = 8799\n',
                        encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg_path))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(state))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DL_ROUTER_DIRS_FILE", str(dirs_file))
    return {"config": cfg_path, "state": state, "library": library,
            "dirs_file": dirs_file}


def call(argv):
    return cli.main(argv)


# --- parser ---------------------------------------------------------------- #
def test_every_documented_subcommand_parses():
    parser = cli.build_parser()
    for argv in (
        ["status"], ["dirs"], ["token"],
        ["match", "--filename", "x.mp4", "--tag", "Jane Doe"],
        ["log", "-n", "5"],
        ["alias", "list"],
        ["alias", "set", "jd", "Jane Doe", "--site", "example-site.test"],
        ["alias", "rm", "jd"],
        ["backfill", "plan"],
        ["backfill", "plan", "--seed-aliases"],
        ["backfill", "apply", "--manifest", "/tmp/m.tsv", "--dry-run"],
        ["fetch", "https://example-site.test/v", "--dir", "other"],
    ):
        assert parser.parse_args(argv) is not None, argv


def test_the_phantom_flag_is_gone():
    """`dl-route`'s docstring advertised `--apply-safe-dirs`, which the parser
    never had."""
    assert "--apply-safe-dirs" not in cli.__doc__
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["backfill", "plan", "--apply-safe-dirs"])


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_apply_requires_a_manifest():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["backfill", "apply"])


# --- status / dirs / token ------------------------------------------------- #
def test_status_reports_a_down_sidecar_without_raising(cli_env, capsys):
    assert call(["status"]) == 0
    out = capsys.readouterr().out
    assert "sidecar    DOWN" in out
    assert "dirs       6" in out


def test_status_reports_an_unset_library_root(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("port = 8799\n", encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    assert call(["status"]) == 0
    assert "unset" in capsys.readouterr().out


def test_dirs_lists_the_routing_targets_with_their_kinds(cli_env, capsys):
    assert call(["dirs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    # name / normalisation key / kind -- the kind is the third column because
    # an unclassified directory never auto-files, so "which are unclassified?"
    # has to be answerable from the listing.
    assert "Jane Doe\tjanedoe\tperformer" in lines
    assert "john-smith\tjohnsmith\tperformer" in lines


def test_token_prints_a_stable_token_and_creates_it_0600(cli_env, capsys):
    import stat
    assert call(["token"]) == 0
    first = capsys.readouterr().out.strip()
    assert len(first) > 20
    token_file = Path(cli._cfg().token_file)
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert call(["token"]) == 0
    assert capsys.readouterr().out.strip() == first, "must be stable"


# --- match / log / alias --------------------------------------------------- #
def test_match_dry_runs_the_matcher_and_prints_the_contract(cli_env, capsys):
    assert call(["match", "--tag", "Jane Doe",
                 "--site", "example-site.test"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dir"] == "Jane Doe"
    assert out["auto"] is True
    assert set(out) == {"dir", "confidence", "reason", "auto", "candidates",
                        "suggestNew", "dup", "ttlMs"}


def test_match_on_an_opaque_filename_lands_on_the_catch_all(cli_env, capsys):
    assert call(["match", "--filename", "0hv9783sdgne5ur3xh53n.mp4"]) == 0
    assert json.loads(capsys.readouterr().out)["dir"] == "other"


def test_alias_set_list_and_rm_round_trip(cli_env, capsys):
    assert call(["alias", "set", "JDoe", "Jane Doe",
                 "--site", "example-site.test"]) == 0
    capsys.readouterr()
    assert call(["alias", "list"]) == 0
    listed = capsys.readouterr().out
    assert "jdoe\texample-site.test\tJane Doe" in listed

    # ...and the matcher now uses it.
    assert call(["match", "--tag", "JDoe", "--site", "example-site.test"]) == 0
    assert json.loads(capsys.readouterr().out)["confidence"] == 1.0

    assert call(["alias", "rm", "JDoe", "--site", "example-site.test"]) == 0
    capsys.readouterr()
    call(["alias", "list"])
    assert "jdoe\t" not in capsys.readouterr().out


def test_log_prints_recent_routes(cli_env, capsys):
    store = Store(cli._cfg().db_path)
    store.log_route(dir_name="Jane Doe", confidence=0.85, reason="tag",
                    auto=True)
    store.log_route(dir_name="other", confidence=0.1, reason="none")
    store.close()
    assert call(["log", "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert "auto 0.85 -> Jane Doe" in out
    assert "ask  0.10 -> other" in out


def test_log_on_an_empty_store_is_not_an_error(cli_env, capsys):
    assert call(["log"]) == 0
    assert capsys.readouterr().out == ""


# --- config errors --------------------------------------------------------- #
def test_a_malformed_config_is_reported_as_exit_2(tmp_path, monkeypatch,
                                                  capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is not = valid toml [[[", encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    assert call(["dirs"]) == 2
    assert "config error" in capsys.readouterr().err


def test_an_unset_library_root_is_reported_not_a_traceback(tmp_path,
                                                           monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text("port = 8799\n", encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    assert call(["dirs"]) == 2
    assert "library_root is not configured" in capsys.readouterr().err


# --- backfill -------------------------------------------------------------- #
def test_backfill_plan_is_read_only_and_writes_a_manifest(cli_env, capsys):
    (cli_env["library"] / "Jane Doe.mp4").write_text("x")
    before = sorted(p.name for p in cli_env["library"].iterdir())
    assert call(["backfill", "plan"]) == 0
    captured = capsys.readouterr()
    assert sorted(p.name for p in cli_env["library"].iterdir()) == before

    manifests = sorted((cli_env["state"] / "manifests").glob("*.tsv"))
    assert len(manifests) == 1
    assert "Jane Doe.mp4" in manifests[0].read_text()
    # The user is pointed at the TSV -- the artefact they are told to edit.
    assert manifests[0].name in captured.err
    assert "--manifest" in captured.err and ".tsv" in captured.err
    # ...and no aliases were persisted.
    store = Store(cli._cfg().db_path)
    assert store.alias_count() == 0
    store.close()


def test_backfill_plan_seed_aliases_persists(cli_env, capsys):
    (cli_env["library"] / "Jane Doe.mp4").write_text("x")
    assert call(["backfill", "plan", "--seed-aliases"]) == 0
    capsys.readouterr()
    store = Store(cli._cfg().db_path)
    assert store.alias("janedoe") == "Jane Doe"
    store.close()


def test_backfill_plan_says_every_row_is_skip_without_credentials(cli_env,
                                                                  capsys):
    (cli_env["library"] / "Jane Doe.mp4").write_text("x")
    call(["backfill", "plan"])
    err = capsys.readouterr().err
    assert "credentials not configured" in err
    manifest = sorted((cli_env["state"] / "manifests").glob("*.tsv"))[0]
    body = [ln for ln in manifest.read_text().splitlines()
            if ln and not ln.startswith("#") and not ln.startswith("action\t")]
    assert all(ln.startswith("SKIP\t") for ln in body), body


def test_backfill_apply_refuses_a_manifest_for_another_root(cli_env, tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"root": "/somewhere/else", "created_at": 0, "rows": []}),
        encoding="utf-8")
    with pytest.raises(SystemExit, match="refusing"):
        call(["backfill", "apply", "--manifest", str(manifest)])


def test_backfill_apply_refuses_without_qbt_credentials(cli_env, tmp_path,
                                                        capsys):
    """apply re-validates every row against live qBittorrent, so credentials
    are required whenever ANYTHING would move -- not just for `qbt` rows."""
    (cli_env["library"] / "Jane Doe.mp4").write_text("x")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "root": str(cli_env["library"]), "created_at": 0,
        "rows": [{"relpath": "Jane Doe.mp4", "size": 1,
                  "proposed_dir": "Jane Doe", "confidence": 0.9,
                  "reason": "", "action": "fs", "move": "fs",
                  "torrent_hash": "", "signal": "alias"}],
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="re-validates"):
        call(["backfill", "apply", "--manifest", str(manifest)])
    assert (cli_env["library"] / "Jane Doe.mp4").exists()


def test_backfill_apply_on_an_all_skip_manifest_is_a_no_op(cli_env,
                                                          tmp_path, capsys):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "root": str(cli_env["library"]), "created_at": 0,
        "rows": [{"relpath": "x.mp4", "size": 1, "proposed_dir": "",
                  "confidence": 0.0, "reason": "", "action": "SKIP",
                  "move": "unknown", "torrent_hash": "", "signal": "none"}],
    }), encoding="utf-8")
    assert call(["backfill", "apply", "--manifest", str(manifest)]) == 0
    assert "moved=0 skipped=1" in capsys.readouterr().out


# --- the API-calling subcommands, against a REAL stub sidecar -------------- #
@pytest.fixture
def live_sidecar(cli_env, library, monkeypatch):
    """A real dl-router server on an ephemeral port, with the CLI pointed at
    it. Exercises _api()'s auth header and error mapping for real."""
    cfg = config_mod.load()
    token = config_mod.load_or_create_token(cfg.token_file)
    app = S.App(cfg, store=Store(":memory:"),
                fetcher=Fetcher(library, runner=lambda *a, **k: _FakeProc()))
    server = S.build_server("127.0.0.1", 0, app, token)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    real_cfg = cli._cfg

    def patched():
        c = real_cfg()
        c.data["port"] = port
        return c

    monkeypatch.setattr(cli, "_cfg", patched)
    yield port
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class _FakeProc:
    def poll(self):
        return None

    def terminate(self):
        pass


def test_status_against_a_live_sidecar(live_sidecar, capsys):
    assert call(["status"]) == 0
    out = capsys.readouterr().out
    assert "sidecar    up" in out
    assert '"configured": true' in out


def test_fetch_queues_a_job_through_the_sidecar(live_sidecar, capsys):
    assert call(["fetch", "https://example-site.test/v/1",
                 "--dir", "other"]) == 0
    # The stub sidecar runs in-process, so its structured journal line shares
    # this stdout; take the JSON document the CLI printed.
    raw = capsys.readouterr().out
    out = json.loads(raw[raw.index("{"):])
    assert out["ok"] is True
    assert out["dir"] == "other"
    assert out["state"] == "running"


def test_fetch_surfaces_a_refusal_as_a_clean_exit(live_sidecar):
    """The sidecar's allowlist refusal must reach the user, not a traceback."""
    with pytest.raises(SystemExit, match="HTTP 400"):
        call(["fetch", "https://example-site.test/v/1", "--dir", "Not Indexed"])


def test_fetch_refuses_a_non_http_url(live_sidecar):
    with pytest.raises(SystemExit, match="HTTP 400"):
        call(["fetch", "file:///etc/passwd", "--dir", "other"])


def test_an_unreachable_sidecar_gives_an_actionable_message(cli_env):
    with pytest.raises(SystemExit, match="systemctl --user status dl-router"):
        call(["fetch", "https://example-site.test/v/1", "--dir", "other"])


# --- the alias round-trip bug ---------------------------------------------- #
def test_a_global_alias_can_be_removed_with_the_star_that_list_prints(cli_env,
                                                                      capsys):
    """`alias list` printed `*` for a global alias and `alias rm --site '*'`
    could not remove it: the site is stored as `''`, so it looked for a
    literal-asterisk site, removed nothing, and printed "alias removed" anyway.
    The most dangerous alias in the table was the one that could not be removed
    the obvious way."""
    store = Store(cli._cfg().db_path)
    store.upsert_alias("astervale", "Jane Doe", "", source="manual")
    store.close()

    assert call(["alias", "list"]) == 0
    assert "astervale\t*\tJane Doe" in capsys.readouterr().out

    assert call(["alias", "rm", "Aster Vale", "--site", "*"]) == 0
    capsys.readouterr()
    call(["alias", "list"])
    assert "astervale" not in capsys.readouterr().out


def test_an_empty_site_still_means_global(cli_env, capsys):
    store = Store(cli._cfg().db_path)
    store.upsert_alias("astervale", "Jane Doe", "")
    store.close()
    assert call(["alias", "rm", "Aster Vale", "--site", ""]) == 0


def test_removing_an_alias_that_is_not_there_says_so(cli_env, capsys):
    """It used to print "alias removed" whether or not a row existed, which is
    how the un-removable global alias stayed un-removed."""
    assert call(["alias", "rm", "Aster Vale", "--site", "*"]) == 1
    assert "no alias" in capsys.readouterr().err


def test_alias_set_refuses_a_key_that_looks_like_a_mistake(cli_env):
    with pytest.raises(SystemExit, match="shorter than"):
        call(["alias", "set", "JD", "Jane Doe", "--site", "example-site.test"])


def test_alias_set_force_writes_it_anyway(cli_env, capsys):
    assert call(["alias", "set", "JD", "Jane Doe", "--site",
                 "example-site.test", "--force"]) == 0
    assert "warning" in capsys.readouterr().err


def test_a_structured_alias_key_round_trips_through_set_list_and_rm(cli_env,
                                                                    capsys):
    channel = "119283746551234567"
    assert call(["alias", "set", f"discord:{channel}", "Jane Doe",
                 "--site", "discord.com"]) == 0
    capsys.readouterr()
    call(["alias", "list"])
    assert f"discord:{channel}\tdiscord.com\tJane Doe" in capsys.readouterr().out
    assert call(["alias", "rm", f"discord:{channel}",
                 "--site", "discord.com"]) == 0


# --- alias review ----------------------------------------------------------- #
def test_alias_review_shows_evidence_and_hits_and_flags_the_global_one(cli_env,
                                                                       capsys):
    """Nothing surfaced the four bad rows; that is why this command exists."""
    store = Store(cli._cfg().db_path)
    store.upsert_alias("astervale", "Jane Doe", "example-site.test",
                       source="title-subject", evidence="Aster Vale")
    store.upsert_alias("poster1988", "Jane Doe", "",
                       source="tag", evidence="poster_1988")
    store.close()
    assert call(["alias", "review"]) == 0
    out = capsys.readouterr().out
    assert "title-subject" in out and "Aster Vale" in out
    # The global one is flagged, and it is listed FIRST (riskiest first).
    assert out.index("poster1988") < out.index("astervale")
    assert "! poster1988" in out
    assert "handle" in out or "global scope" in out
    assert "dl-route alias rm" in out


def test_alias_review_json_is_machine_readable(cli_env, capsys):
    store = Store(cli._cfg().db_path)
    store.upsert_alias("astervale", "Jane Doe", "example-site.test",
                       source="title-subject", evidence="Aster Vale")
    store.close()
    assert call(["alias", "review", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["key"] == "astervale"
    assert rows[0]["source"] == "title-subject"
    assert rows[0]["hits"] == 1
    assert rows[0]["suspect"] is None


def test_alias_review_on_an_empty_table_is_not_an_error(cli_env, capsys):
    assert call(["alias", "review"]) == 0
    assert "no aliases" in capsys.readouterr().out


# --- the directory-kind draft ---------------------------------------------- #
def test_dirs_classify_drafts_a_reviewable_file(cli_env, capsys, tmp_path):
    out = tmp_path / "draft.toml"
    assert call(["dirs", "classify", "--out", str(out)]) == 0
    import tomllib
    parsed = tomllib.loads(out.read_text(encoding="utf-8"))
    assert set(parsed) == {"performer", "category"}
    assert "Jane Doe" in parsed["performer"]
    # ...and it explains itself rather than emitting a bare list.
    assert "review" in out.read_text(encoding="utf-8").lower()


def test_dirs_classify_prints_to_stdout_by_default(cli_env, capsys):
    assert call(["dirs", "classify"]) == 0
    assert "performer = [" in capsys.readouterr().out


def test_dirs_classify_refuses_to_clobber_a_reviewed_file(cli_env, tmp_path):
    out = tmp_path / "draft.toml"
    out.write_text("performer = []\ncategory = []\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="--force"):
        call(["dirs", "classify", "--out", str(out)])


def test_status_names_the_unclassified_directories_as_the_reason_it_asks(
        cli_env, capsys, tmp_path):
    """`dirs       26` with everything asking and no explanation is the shape
    of the evening this batch came from."""
    (tmp_path / "dirs.toml").write_text('performer = []\ncategory = []\n',
                                        encoding="utf-8")
    assert call(["status"]) == 0
    out = capsys.readouterr().out
    assert "unclassified=6" in out
    assert "dl-route dirs classify" in out


def test_dirs_classify_force_refuses_to_discard_an_unparseable_file(cli_env,
                                                                    tmp_path):
    """--force is safe only because an existing classification is carried into
    the draft. When the file cannot be PARSED there is nothing to carry, so
    --force would silently reset every decision in it — the one destructive act
    available from a command whose whole job is to be read-only."""
    out = tmp_path / "broken.toml"
    out.write_text("performer = [ oops", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot be parsed"):
        call(["dirs", "classify", "--out", str(out), "--force"])
    assert out.read_text(encoding="utf-8") == "performer = [ oops"


def test_dirs_classify_force_does_overwrite_a_readable_file(cli_env, tmp_path):
    out = tmp_path / "ok.toml"
    out.write_text('performer = ["Jane Doe"]\ncategory = []\n', encoding="utf-8")
    assert call(["dirs", "classify", "--out", str(out), "--force"]) == 0
    import tomllib
    # ...and the existing decision survived the regeneration.
    assert "Jane Doe" in tomllib.loads(out.read_text(encoding="utf-8"))["performer"]


def test_status_does_not_create_the_state_dir(cli_env, capsys):
    """A read-only command must not have a side effect on disk."""
    state = Path(cli_env["state"])
    assert not state.exists()
    assert call(["status"]) == 0
    assert not state.exists(), "status created the state directory"


def test_alias_review_shows_backfill_seeds_as_the_global_rows_they_are(cli_env,
                                                                      capsys):
    """`backfill plan --seed-aliases` is the ONE place a global alias is
    written. It is explicit and user-invoked, but it must be visible as such."""
    store = Store(cli._cfg().db_path)
    store.upsert_alias("janedoe", "Jane Doe", "", source="backfill-seed",
                       evidence="janedoe")
    store.close()
    assert call(["alias", "review"]) == 0
    out = capsys.readouterr().out
    assert "backfill-seed" in out
    assert "! janedoe" in out
