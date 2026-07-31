"""Config loading, env overrides, validation, and the token file's mode.

The shipped defaults must contain NO library path — that is the public-repo
constraint, asserted here so a future edit cannot quietly leak one.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402


def write(tmp_path, text) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- defaults -------------------------------------------------------------- #
def test_defaults_contain_no_library_path():
    assert config_mod.DEFAULTS["library_root"] == ""


def test_default_port_avoids_the_taken_one():
    assert config_mod.DEFAULT_PORT == 8791


def test_missing_file_yields_defaults(tmp_path):
    cfg = config_mod.load(tmp_path / "absent.toml", env={})
    assert cfg.port == 8791
    assert cfg.host == "127.0.0.1"
    assert cfg.library_root is None


def test_require_library_root_raises_when_unset(tmp_path):
    cfg = config_mod.load(tmp_path / "absent.toml", env={})
    with pytest.raises(config_mod.ConfigError, match="not configured"):
        cfg.require_library_root()


# --- file loading ---------------------------------------------------------- #
def test_values_from_the_file_override_defaults(tmp_path):
    path = write(tmp_path, 'library_root = "/srv/library"\nport = 9000\n'
                           'auto_threshold = 0.9\n')
    cfg = config_mod.load(path, env={})
    assert cfg.library_root == Path("/srv/library")
    assert cfg.port == 9000
    assert cfg.auto_threshold == 0.9


def test_nested_tables_merge_rather_than_replace(tmp_path):
    path = write(tmp_path, '[qbt]\nusername = "admin"\n')
    cfg = config_mod.load(path, env={})
    assert cfg.section("qbt")["username"] == "admin"
    # untouched defaults survive
    assert cfg.section("qbt")["url"] == "http://127.0.0.1:30880"


def test_site_rules_round_trip(tmp_path):
    path = write(tmp_path, '[site_rules."example-site.test"]\n'
                           'subject = ["a.subject"]\ntags = [".tag a"]\n')
    cfg = config_mod.load(path, env={})
    rules = cfg.section("site_rules")["example-site.test"]
    assert rules["subject"] == ["a.subject"]


def test_malformed_toml_is_fatal(tmp_path):
    path = write(tmp_path, "this is not = = toml")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(path, env={})


def test_tilde_in_library_root_is_expanded(tmp_path):
    path = write(tmp_path, 'library_root = "~/media"\n')
    cfg = config_mod.load(path, env={})
    assert str(cfg.library_root).startswith(str(Path.home()))


# --- env overrides --------------------------------------------------------- #
def test_env_overrides_win_over_the_file(tmp_path):
    path = write(tmp_path, 'library_root = "/from/file"\nport = 1234\n')
    cfg = config_mod.load(path, env={"DL_ROUTER_LIBRARY_ROOT": "/from/env",
                                     "DL_ROUTER_PORT": "9999",
                                     "DL_ROUTER_HOST": "::1"})
    assert cfg.library_root == Path("/from/env")
    assert cfg.port == 9999
    assert cfg.host == "::1"


def test_state_and_token_paths_come_from_env(tmp_path):
    cfg = config_mod.load(tmp_path / "absent.toml", env={
        "DL_ROUTER_STATE_DIR": str(tmp_path / "state"),
        "DL_ROUTER_TOKEN_FILE": str(tmp_path / "tok"),
    })
    assert cfg.state_dir == tmp_path / "state"
    assert cfg.token_file == tmp_path / "tok"
    assert cfg.db_path == tmp_path / "state" / "dl-router.sqlite3"


def test_config_path_comes_from_env(tmp_path):
    path = write(tmp_path, 'port = 4321\n')
    cfg = config_mod.load(env={"DL_ROUTER_CONFIG": str(path)})
    assert cfg.port == 4321


def test_bad_env_port_is_fatal(tmp_path):
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(tmp_path / "absent.toml",
                        env={"DL_ROUTER_PORT": "not-a-number"})


# --- validation ------------------------------------------------------------ #
@pytest.mark.parametrize("body", [
    "auto_threshold = 1.5",
    "auto_threshold = -0.1",
    'auto_threshold = "high"',
    "tie_margin = 2",
    "port = 0",
    "port = 70000",
    'site_rules = "not a table"',
])
def test_invalid_values_are_rejected(tmp_path, body):
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(write(tmp_path, body + "\n"), env={})


def test_require_library_root_rejects_a_relative_path(tmp_path):
    cfg = config_mod.load(write(tmp_path, 'library_root = "relative/dir"\n'),
                          env={})
    with pytest.raises(config_mod.ConfigError, match="absolute"):
        cfg.require_library_root()


# --- token ----------------------------------------------------------------- #
def test_token_is_created_0600_and_is_stable(tmp_path):
    path = tmp_path / "cfgdir" / "token"
    first = config_mod.load_or_create_token(path)
    assert first
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, oct(mode)
    assert config_mod.load_or_create_token(path) == first


def test_token_directory_is_not_world_readable(tmp_path):
    path = tmp_path / "cfgdir" / "token"
    config_mod.load_or_create_token(path)
    mode = stat.S_IMODE(os.stat(path.parent).st_mode)
    assert mode & 0o077 == 0, oct(mode)


def test_existing_token_is_reused_verbatim(tmp_path):
    path = tmp_path / "token"
    path.write_text("  pre-existing-token \n")
    assert config_mod.load_or_create_token(path) == "pre-existing-token"


def test_empty_token_file_is_regenerated(tmp_path):
    path = tmp_path / "token"
    path.write_text("\n")
    assert config_mod.load_or_create_token(path).strip()


# --- config.toml holds the qBittorrent password ---------------------------- #
def test_loading_tightens_a_world_readable_config(tmp_path):
    """The documented setup is `cp config.example.toml ~/.config/...`, which
    lands 0644 -- world-readable credentials. The token file was already 0600;
    the config was not."""
    path = tmp_path / "config.toml"
    path.write_text('library_root = "/tmp/lib"\n', encoding="utf-8")
    os.chmod(path, 0o644)
    config_mod.load(path, env={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_loading_leaves_an_already_private_config_alone(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('library_root = "/tmp/lib"\n', encoding="utf-8")
    os.chmod(path, 0o400)
    config_mod.load(path, env={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o400


def test_a_group_readable_config_is_tightened_too(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('library_root = "/tmp/lib"\n', encoding="utf-8")
    os.chmod(path, 0o640)
    config_mod.load(path, env={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --- a malformed config must not become a restart loop --------------------- #
def test_load_degraded_returns_an_unconfigured_config_and_the_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = valid = toml [[[", encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load(path, env={})
    cfg, err = config_mod.load_degraded(path, env={})
    assert err
    assert cfg.library_root is None
    assert cfg.host == config_mod.DEFAULT_HOST
    assert cfg.port == config_mod.DEFAULT_PORT


def test_load_degraded_still_honours_the_units_bind_environment(tmp_path):
    """The typo is in config.toml; the unit's own environment is still good,
    and the sidecar must come up on the right loopback port to be diagnosable."""
    path = tmp_path / "config.toml"
    path.write_text("[[[", encoding="utf-8")
    cfg, err = config_mod.load_degraded(
        path, env={"DL_ROUTER_HOST": "127.0.0.1", "DL_ROUTER_PORT": "8799"})
    assert err
    assert (cfg.host, cfg.port) == ("127.0.0.1", 8799)


def test_load_degraded_is_a_no_op_for_a_valid_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('library_root = "/tmp/lib"\n', encoding="utf-8")
    cfg, err = config_mod.load_degraded(path, env={})
    assert err is None
    assert str(cfg.library_root) == "/tmp/lib"
