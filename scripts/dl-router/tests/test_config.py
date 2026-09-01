"""Config loading, env overrides, validation, and the token file's mode.

The shipped defaults must contain NO library path — that is the public-repo
constraint, asserted here so a future edit cannot quietly leak one.
"""
from __future__ import annotations

import os
import re
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


# --- the two-layer site-rule table ------------------------------------------ #
#
# Structural validation only: types and required fields. The SELECTOR GRAMMAR is
# enforced where it is consumed (player_buttons.normalisePlayerRule), which
# fails closed to "no button" so a bad selector costs one player rather than
# the whole sidecar. A wrong SHAPE is different -- it is a typo that would
# otherwise produce a rule the extension silently discards with nothing
# anywhere saying why.

PLAYER = {"container": "#video-container",
          "media": {"element": "video#main-video", "attr": "src"}}


def _load(tmp_path, rules):
    p = tmp_path / "config.toml"
    body = ["library_root = \"/tmp/lib\""]
    for host, entry in rules.items():
        body.append(f"[site_rules.\"{host}\"]")
        body.append(_toml(entry, f"site_rules.\"{host}\""))
    p.write_text("\n".join(body), encoding="utf-8")
    return config_mod.load(p)


def _toml_str(text) -> str:
    """A TOML basic string with `"` and `\\` escaped."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_value(value) -> str:
    """One TOML value, emitted with its OWN type preserved.

    A bool, an int, a float and a nested array must NOT come out as strings --
    the validator's type checks are exactly what several tests below assert,
    and a stringifying emitter would satisfy every one of them vacuously.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{k} = {_toml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return _toml_str(value)


def _toml(entry, prefix):
    """Just enough TOML emitter for these cases."""
    # 🔴 EVERY VALUE GOES THROUGH `_toml_value`. ONE RULE, ONE PLACE.
    #
    # An earlier version wrapped scalars in quotes, so `element = 1` reached the
    # validator as the STRING "1" and was accepted -- a type-rejection test
    # written through it would have passed vacuously, testing the emitter rather
    # than the validator, which is the exact inversion this helper's docstring
    # promises not to do. The first fix routed only the array-of-inline-tables
    # branch through `_toml_value` and left the other two stringifying, so the
    # inversion survived on the SINGLE-TABLE form -- which is the back-compat
    # shape every deployed config uses. Measured: `element = "1"` there while
    # the list branch emitted `element = 1`.
    #
    # `"` is escaped rather than assumed absent: an attribute selector like
    # `a[href^="https://x/"]` is the whole reason the list form exists, and an
    # emitter producing unclosed inline tables for it would make every list test
    # fail on the FIXTURE rather than on the validator.
    lines, tables = [], []
    for k, v in entry.items():
        if isinstance(v, dict):
            tables.append((k, v))
        else:
            lines.append(f"{k} = {_toml_value(v)}")
    for k, v in tables:
        lines.append(f"[{prefix}.{k}]")
        lines.append(_toml(v, f"{prefix}.{k}"))
    return "\n".join(lines)


def test_a_valid_two_layer_rule_table_loads(tmp_path):
    cfg = _load(tmp_path, {
        "forum.example.test": {"context": {"subject": ["h1.thread-title"]}},
        "embedhost.example.test": {"player": PLAYER},
    })
    rules = cfg.section("site_rules")
    assert rules["forum.example.test"]["context"]["subject"] == [
        "h1.thread-title"]
    assert rules["embedhost.example.test"]["player"]["media"]["attr"] == "src"


def test_the_flat_legacy_context_form_still_loads(tmp_path):
    cfg = _load(tmp_path, {"example-site.test": {"subject": ["a.performer"],
                                                 "tags": [".tag-list a"]}})
    assert cfg.section("site_rules")["example-site.test"]["tags"] == [
        ".tag-list a"]


@pytest.mark.parametrize("entry,needle", [
    ({"player": {"media": PLAYER["media"]}}, "container is required"),
    ({"player": {"container": "#c"}}, "player.media must be a table"),
    ({"player": {"container": "#c", "media": {"element": "v"}}},
     "media.attr must be a non-empty string"),
    ({"player": {"container": "#c", "media": {"element": "", "attr": "src"}}},
     "media.element must be a non-empty string"),
    ({"subject": "a.one"}, "subject must be a list of selector strings"),
    ({"context": {"tags": "a.one"}}, "tags must be a list of selector strings"),
])
def test_a_malformed_rule_is_reported_rather_than_silently_dropped(
        tmp_path, entry, needle):
    with pytest.raises(config_mod.ConfigError) as exc:
        _load(tmp_path, {"embedhost.example.test": entry})
    assert needle in str(exc.value)


def test_a_malformed_rule_degrades_the_sidecar_instead_of_crash_looping(
        tmp_path):
    """The unit is Restart=always, so a raise on load is a silent restart loop.

    `load_degraded` is what makes the typo visible in /healthz and
    `dl-route status` instead.
    """
    p = tmp_path / "config.toml"
    p.write_text('library_root = "/tmp/lib"\n'
                 '[site_rules."h.test".player]\ncontainer = "#c"\n',
                 encoding="utf-8")
    cfg, err = config_mod.load_degraded(p)
    assert err and "player.media must be a table" in err
    assert cfg.library_root is None, "and nothing is routed by accident"


# --- an ORDERED LIST of media accessors ------------------------------------ #
# The sidecar is the FIRST gate: it validates site_rules before the snapshot
# ever reaches the extension, so a list it rejects can never render a button no
# matter what player_buttons.js accepts. These pin that the two agree.

def _player(media):
    return {"h.test": {"player": {"container": "#c", "media": media}}}


def test_a_single_media_table_is_still_accepted(tmp_path):
    """Back-compat. Every deployed config today uses this shape."""
    cfg = _load(tmp_path, _player({"element": "video", "attr": "src"}))
    assert cfg.section("site_rules")["h.test"]["player"]


def test_a_list_of_media_accessors_is_accepted(tmp_path):
    cfg = _load(tmp_path, _player([
        {"element": 'a[href^="https://cdn.test/"]', "attr": "href"},
        {"element": "video", "attr": "src"},
    ]))
    media = cfg.section("site_rules")["h.test"]["player"]["media"]
    assert [m["attr"] for m in media] == ["href", "src"], \
        "order must survive the round trip -- it is the contract"


def test_an_empty_media_list_is_refused(tmp_path):
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player([]))
    assert "empty list" in str(e.value)


def test_a_list_longer_than_the_cap_is_refused(tmp_path):
    over = [{"element": "video", "attr": "src"}] * (
        config_mod.MAX_MEDIA_ACCESSORS + 1)
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player(over))
    assert str(config_mod.MAX_MEDIA_ACCESSORS) in str(e.value)


def test_the_cap_itself_is_accepted(tmp_path):
    """The boundary, so the cap cannot drift off by one."""
    at_cap = [{"element": "video", "attr": "src"}] * \
        config_mod.MAX_MEDIA_ACCESSORS
    cfg = _load(tmp_path, _player(at_cap))
    assert len(cfg.section("site_rules")["h.test"]["player"]["media"]) == \
        config_mod.MAX_MEDIA_ACCESSORS


def test_a_bad_entry_in_a_list_names_ITS_INDEX(tmp_path):
    """A long list with one typo must say WHICH entry, or the operator has to
    bisect their own config by hand."""
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player([
            {"element": "video", "attr": "src"},
            {"element": "a", "attr": ""},
        ]))
    assert "media[1].attr" in str(e.value)


def test_the_SINGLE_table_message_is_pinned_WHOLE(tmp_path):
    """🔴 THE WHOLE NORMALISED STRING, not a substring.

    This is a back-compat contract: an operator with a single-table rule must
    see exactly what they saw before the list form existed. A substring check
    (`"media.attr" in msg and "[0]" not in msg`) is walkable by rewording
    everything around it -- and a reworded message is precisely the drift this
    guards. A cosmetic reword now fails this test, which is the price of a
    machine-readable claim.
    """
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player({"element": "video", "attr": ""}))
    assert str(e.value) == (
        'site_rules.\'h.test\'.player.media.attr must be a non-empty string')


def test_a_LIST_entry_message_names_its_index_and_is_pinned_whole(tmp_path):
    """The mirror of the test above: the list form differs from the single
    form ONLY by the `[idx]`, so both are pinned whole and the pair is what
    makes 'byte-identical except for the index' checkable."""
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player([{"element": "video", "attr": "src"},
                                 {"element": "video", "attr": ""}]))
    assert str(e.value) == (
        'site_rules.\'h.test\'.player.media[1].attr must be a non-empty string')


def test_the_wrong_SHAPE_message_is_pastable_TOML(tmp_path):
    """It carries an example, and an example an operator cannot paste is
    worse than none: the f-string/plain-string split rendered `{{ ... }}`."""
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player("video"))
    msg = str(e.value)
    assert "{{" not in msg and "}}" not in msg, msg
    assert 'media = { element = "video#main", attr = "src" }' in msg


def test_a_scalar_media_is_still_refused(tmp_path):
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player("video"))
    assert "must be a table" in str(e.value)


def test_a_list_entry_that_is_not_a_table_is_a_ConfigError_not_a_CRASH(
        tmp_path):
    """🔴 THE GUARD THIS PINS IS WHAT STOPS A RESTART LOOP.

    `load_degraded` catches `ConfigError` and nothing else, and the unit is
    `Restart=always` + `RestartSec=10`. Without the isinstance check, a
    `media = ["video"]` typo raises AttributeError ('str' has no attribute
    'get'), which `load_degraded` does NOT catch, so the sidecar crash-loops
    six times a minute with nothing listening -- the exact failure
    `load_degraded`'s own docstring exists to prevent.
    """
    with pytest.raises(config_mod.ConfigError) as e:
        _load(tmp_path, _player(["video"]))
    assert "must be a table" in str(e.value)
    # ...and the degraded path must produce a config, not propagate.
    path = tmp_path / "config.toml"
    cfg, err = config_mod.load_degraded(path, env={})
    assert err and cfg.library_root is None


def test_a_non_string_accessor_field_is_refused(tmp_path):
    """The emitter preserves types on purpose; if it stringified them this
    would pass vacuously against `element = "1"`."""
    for bad in ({"element": 1, "attr": "src"},
                {"element": "video", "attr": True}):
        with pytest.raises(config_mod.ConfigError) as e:
            _load(tmp_path, _player([bad]))
        assert "must be a non-empty string" in str(e.value)


def test_THE_TWO_LANGUAGES_AGREE_ON_THE_ACCESSOR_CAP():
    """🔴 A HAND-COPIED CONSTANT IN TWO LANGUAGES, PINNED.

    MEASURED: bumping only `config.py`'s cap to 9 leaves BOTH suites fully
    green (48/48 python, 536/536 node). The sidecar would then accept a 9-entry
    rule that `normalisePlayerRule` rejects, so the host gets NO BUTTON and no
    error anywhere -- the silent failure both files' comments name.

    This repo has already paid for this shape once: `fixtures/name_cases.json`
    exists because two hand-copied cross-language tables agreed with each other
    while the implementations disagreed on 991 inputs.
    """
    js = (Path(__file__).resolve().parent.parent
          / "extension" / "player_buttons.js").read_text(encoding="utf-8")
    found = re.findall(r"var MAX_MEDIA_ACCESSORS = (\d+);", js)
    assert len(found) == 1, \
        f"expected exactly one JS declaration, found {found!r}"
    assert int(found[0]) == config_mod.MAX_MEDIA_ACCESSORS, (
        f"player_buttons.js caps at {found[0]} but config.py caps at "
        f"{config_mod.MAX_MEDIA_ACCESSORS} -- a rule between the two sizes is "
        "accepted by the sidecar and silently renders no button")


def test_the_SINGLE_table_form_refuses_non_strings_TOO(tmp_path):
    """🔴 THE BACK-COMPAT SHAPE, not just the new one.

    The list form got a type test first; the single table -- which is what
    every deployed config actually uses -- did not, and the emitter was
    stringifying that branch, so `element = 1` arrived as "1" and was accepted.
    A test written here BEFORE the emitter was fixed would have passed while
    asserting nothing.
    """
    for bad in ({"element": 1, "attr": "src"},
                {"element": "video", "attr": True},
                {"element": 2.5, "attr": "src"}):
        with pytest.raises(config_mod.ConfigError) as e:
            _load(tmp_path, _player(bad))
        assert "must be a non-empty string" in str(e.value), bad


def test_the_emitter_preserves_types_on_EVERY_branch(tmp_path):
    """The guard on the guard. `_toml` has three value branches and the fix
    initially covered one; this asserts the helper itself, so a future edit
    that re-stringifies any branch fails here rather than silently making some
    other test vacuous."""
    emitted = _toml({"a": 1, "b": True, "c": [1, True], "d": "x"}, "p")
    assert "a = 1" in emitted and 'a = "1"' not in emitted
    assert "b = true" in emitted and 'b = "True"' not in emitted
    assert "c = [1, true]" in emitted
    assert 'd = "x"' in emitted, "strings must still be quoted"
