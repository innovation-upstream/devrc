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
def cli_env(tmp_path, library, monkeypatch):
    """Point the CLI's config loader entirely at temp paths."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'library_root = "{library}"\nport = 8799\n',
                        encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg_path))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(state))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    return {"config": cfg_path, "state": state, "library": library}


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


def test_dirs_lists_the_routing_targets(cli_env, capsys):
    assert call(["dirs"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "Jane Doe\tjanedoe" in lines
    assert "john-smith\tjohnsmith" in lines


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
    assert call(["alias", "set", "JD", "Jane Doe",
                 "--site", "example-site.test"]) == 0
    capsys.readouterr()
    assert call(["alias", "list"]) == 0
    listed = capsys.readouterr().out
    assert "jd\texample-site.test\tJane Doe" in listed

    # ...and the matcher now uses it.
    assert call(["match", "--tag", "JD", "--site", "example-site.test"]) == 0
    assert json.loads(capsys.readouterr().out)["confidence"] == 1.0

    assert call(["alias", "rm", "JD", "--site", "example-site.test"]) == 0
    capsys.readouterr()
    call(["alias", "list"])
    assert "jd\t" not in capsys.readouterr().out


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
