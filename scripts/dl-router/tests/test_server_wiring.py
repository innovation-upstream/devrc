"""`server.main()` and the PRODUCTION `App` wiring.

Every other server test injects a store, a dir index, a file index and a
fetcher, so the branches that build the REAL ones (server.py's App constructor)
and the process entry point had no coverage at all -- including the
unconfigured branches that decide whether the sidecar is inert or live.

Nothing here binds a fixed port or touches a real library.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402
import server as S  # noqa: E402
from dirindex import DirIndex, FileIndex  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from store import Store  # noqa: E402


def free_port() -> int:
    """A port nothing is listening on. `port = 0` is rejected by config
    validation (correctly -- it is not a usable configured value), so tests
    that go through the real loader need a real one."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def prod_env(tmp_path, library, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'library_root = "{library}"\nhost = "127.0.0.1"\n'
                   f'port = {free_port()}\n', encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    return {"config": cfg, "library": library, "tmp": tmp_path}


# --- App builds the real collaborators -------------------------------------- #
def test_app_builds_a_real_store_dirindex_fileindex_and_fetcher(prod_env):
    app = S.App(config_mod.load())
    assert isinstance(app.store, Store)
    assert isinstance(app.dirs, DirIndex)
    assert isinstance(app.files, FileIndex)
    assert isinstance(app.fetcher, Fetcher)
    assert app.configured is True
    assert app.dirs.name_set() >= {"Jane Doe", "john-smith", "other"}


def test_app_honours_the_ytdlp_section(tmp_path, library, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'library_root = "{library}"\n'
        '[ytdlp]\nbin = "/usr/bin/false"\n'
        'cookies_from_browser = "browser:Profile 9"\n'
        'output_template = "%(id)s.%(ext)s"\nmax_jobs = 2\n',
        encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    app = S.App(config_mod.load(cfg, env={}))
    assert app.fetcher.ytdlp_bin == "/usr/bin/false"
    assert app.fetcher.cookies_from_browser == "browser:Profile 9"
    assert app.fetcher.output_template == "%(id)s.%(ext)s"
    assert app.fetcher.max_jobs == 2


def test_an_unconfigured_app_builds_nothing_that_needs_a_root(tmp_path,
                                                              monkeypatch,
                                                              closed_port):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"port = {closed_port}\n", encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    app = S.App(config_mod.load(cfg, env={}))
    assert app.configured is False
    assert app.dirs is None and app.files is None and app.fetcher is None
    assert isinstance(app.store, Store)      # the store never needs a root
    health = app.healthz()
    assert health["configured"] is False
    assert "dirs" not in health


def test_the_store_lands_in_the_state_dir(prod_env):
    app = S.App(config_mod.load())
    assert app.store.path == prod_env["tmp"] / "state" / "dl-router.sqlite3"
    assert app.store.path.exists()


# --- main() ----------------------------------------------------------------- #
def test_main_binds_serves_and_shuts_down_cleanly(prod_env, monkeypatch,
                                                  capsys):
    """Drive the real entry point: it must construct everything, log its
    listening line, run the loop and close the socket."""
    built = {}
    real_build = S.build_server

    def spy_build(host, port, app, token):
        server = real_build(host, port, app, token)
        built["server"] = server
        built["app"] = app
        built["token"] = token
        built["addr"] = server.server_address

        def serve_forever(*a, **kw):
            built["served"] = True
            raise KeyboardInterrupt

        monkeypatch.setattr(server, "serve_forever", serve_forever)
        return server

    monkeypatch.setattr(S, "build_server", spy_build)
    assert S.main([]) == 0
    assert built["served"] is True
    assert built["addr"][0] == "127.0.0.1"
    assert built["app"].configured is True
    assert len(built["token"]) > 20
    out = capsys.readouterr().out
    assert "dl-router listening" in out
    assert "configured=True" in out
    # The socket must be closed, not leaked.
    with pytest.raises(OSError):
        built["server"].socket.getsockname()


def test_main_creates_the_token_file_0600(prod_env, monkeypatch):
    import stat
    monkeypatch.setattr(S, "build_server", _immediate_server(monkeypatch))
    assert S.main([]) == 0
    token_file = prod_env["tmp"] / "token"
    assert token_file.exists()
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600


def test_main_warns_but_still_serves_without_a_library_root(tmp_path,
                                                            monkeypatch,
                                                            capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'host = "127.0.0.1"\nport = {free_port()}\n',
                   encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(S, "build_server", _immediate_server(monkeypatch))
    assert S.main([]) == 0
    out = capsys.readouterr().out
    assert "configured=False" in out
    assert "library_root unset" in out


def test_main_does_not_exit_on_a_malformed_config(tmp_path, monkeypatch,
                                                  capsys):
    """`Restart=always` + `RestartSec=10` + a fatal ConfigError was a silent
    6-restarts-per-minute loop with nothing listening."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is not = valid toml [[[", encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DL_ROUTER_HOST", "127.0.0.1")
    # A real free port, not 0: `Config.port` is `int(data.get("port") or
    # DEFAULT)`, so a 0 falls through to the DEFAULT -- which on this host is
    # the LIVE sidecar's port.
    monkeypatch.setenv("DL_ROUTER_PORT", str(free_port()))

    captured = {}
    real_build = S.build_server

    def spy_build(host, port, app, token):
        captured["app"] = app
        server = real_build(host, port, app, token)

        def serve_forever(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(server, "serve_forever", serve_forever)
        return server

    monkeypatch.setattr(S, "build_server", spy_build)
    assert S.main([]) == 0, "it must serve, not crash"
    assert captured["app"].configured is False
    assert captured["app"].config_error
    out = capsys.readouterr().out
    assert "dl-router config_error" in out
    assert "503" in out


def test_main_refuses_a_non_loopback_bind_from_the_environment(tmp_path,
                                                               library,
                                                               monkeypatch):
    """There is no override: this process holds a token that grants filesystem
    writes under the library root."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'library_root = "{library}"\n', encoding="utf-8")
    monkeypatch.setenv("DL_ROUTER_CONFIG", str(cfg))
    monkeypatch.setenv("DL_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DL_ROUTER_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("DL_ROUTER_HOST", "0.0.0.0")
    monkeypatch.setenv("DL_ROUTER_PORT", "0")
    with pytest.raises(ValueError, match="non-loopback"):
        S.main([])


def _immediate_server(monkeypatch):
    real_build = S.build_server

    def spy_build(host, port, app, token):
        server = real_build(host, port, app, token)

        def serve_forever(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(server, "serve_forever", serve_forever)
        return server

    return spy_build


# --- the wired-up server actually answers ----------------------------------- #
def test_a_fully_wired_app_serves_a_real_request(prod_env):
    """No injected collaborators anywhere: the production constructor, a real
    socket, a real token."""
    import json
    import urllib.request

    cfg = config_mod.load()
    token = config_mod.load_or_create_token(cfg.token_file)
    app = S.App(cfg)
    server = S.build_server("127.0.0.1", 0, app, token)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/dirs",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        assert {d["name"] for d in body["dirs"]} >= {"Jane Doe", "other"}
        assert body["root"] == str(prod_env["library"])
        assert body["threshold"] == 0.75
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
