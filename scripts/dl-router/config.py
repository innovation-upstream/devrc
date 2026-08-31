"""dl-router configuration loader.

Everything host-specific lives OUTSIDE the repo:

    ~/.config/dl-router/config.toml   settings (library root, qBittorrent creds,
                                      per-site capture rules)
    ~/.config/dl-router/dirs.toml     directory kinds (performer/category) —
                                      see dirkinds.py
    ~/.config/dl-router/token         bearer token, 0600, auto-created
    ~/.local/share/dl-router/         SQLite store + backfill manifests

The repo ships only neutral defaults and NO paths into anybody's media library.
`library_root` has no default on purpose: the sidecar refuses to serve routing
endpoints until it is configured.

Env overrides (all optional, mainly for tests + the systemd unit):

    DL_ROUTER_CONFIG        path to config.toml
    DL_ROUTER_DIRS_FILE     path to dirs.toml (directory kinds)
    DL_ROUTER_STATE_DIR     state dir (SQLite, manifests)
    DL_ROUTER_TOKEN_FILE    bearer-token path
    DL_ROUTER_LIBRARY_ROOT  library root (overrides config.toml)
    DL_ROUTER_HOST          bind host (default 127.0.0.1 — keep loopback)
    DL_ROUTER_PORT          bind port (default 8791; 8790 is taken on this host)
"""
from __future__ import annotations

import copy
import os
import secrets
import tomllib
from pathlib import Path

# Port 8790 is already in use on the workbench (see the design spec, hazard 5).
DEFAULT_PORT = 8791
DEFAULT_HOST = "127.0.0.1"

# How many media accessors one player rule may list. A cap, not a design
# target: every accessor is a selector query run inside the CLICK handler, and
# a config typo that turned into a 500-entry list would stall the click on the
# operator's own page. Two covers the case this exists for (an anchor-linked
# image plus a direct video); the headroom is for a site that needs a poster or
# a <source> fallback as well. Mirrored in player_buttons.js -- both sides must
# agree, or a config the sidecar accepts renders no button.
MAX_MEDIA_ACCESSORS = 8

# The catch-all directory name. Created on demand under the library root; it is
# also the terminal fallback of the extension's suggest() ladder.
DEFAULT_OTHER_DIR = "other"

DEFAULTS: dict = {
    # Absolute path to the media library root. Empty = unconfigured.
    "library_root": "",
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    # Matching
    "auto_threshold": 0.75,
    "tie_margin": 0.05,
    "other_dir": DEFAULT_OTHER_DIR,
    # Extension timing
    "match_timeout_ms": 400,
    "capture_window_s": 15,
    "toast_ms": 8000,
    # Index caching
    "dir_cache_ttl_s": 5.0,
    "file_cache_ttl_s": 60.0,
    "file_index_max": 200000,
    # Duplicate handling. A duplicate is never acted on automatically: the file
    # is kept and filed normally, and the toast offers `delete` and `keep`.
    "dedupe": {
        # What `delete` does. "trash" renames the newly-downloaded copy into a
        # hidden `.dl-router-trash/` inside the library root -- an atomic
        # same-filesystem move, skipped by both index scans, inspectable with
        # `ls`, and reversible with `mv`. "unlink" really removes it.
        #
        # The default is the reversible one ON PURPOSE. This is the only
        # destructive operation in the subsystem, it runs next to a live
        # qBittorrent seeding target, and the honest limit of the confirmation
        # behind it is that the head+tail digest samples 256 KiB, not the whole
        # file. Nothing here is worth an irreversible default.
        "delete_mode": "trash",
    },
    # qBittorrent WebUI. Used ONLY by the backfill; new downloads never touch it.
    "qbt": {
        "url": "http://127.0.0.1:30880",
        "username": "",
        "password": "",
        "timeout_s": 8.0,
        # Extra host-side mount roots to consider when deriving the
        # host<->container path mapping. The ancestors of library_root are
        # always tried; this is an escape hatch for unusual layouts.
        "host_roots": [],
    },
    # yt-dlp path for HLS/DASH sources.
    "ytdlp": {
        "bin": "yt-dlp",
        # e.g. "brave:Profile 2" — passed to --cookies-from-browser. Empty = off.
        "cookies_from_browser": "",
        "output_template": "%(title).150B [%(id)s].%(ext)s",
        "timeout_s": 3600.0,
        "max_jobs": 4,
    },
    # Per-site context-capture rules, keyed by hostname. Adding a site is
    # CONFIG, not code. Example (synthetic):
    #
    #   [site_rules."example-site.test"]
    #   subject = ["a.model-name", ".performer-link"]
    #   tags    = [".tag-list a"]
    #
    "site_rules": {},
}


class ConfigError(RuntimeError):
    """Raised for a malformed or unusable configuration."""


def default_config_path() -> Path:
    env = os.environ.get("DL_ROUTER_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".config" / "dl-router" / "config.toml"


def default_state_dir() -> Path:
    env = os.environ.get("DL_ROUTER_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "dl-router"


def default_token_file() -> Path:
    env = os.environ.get("DL_ROUTER_TOKEN_FILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "dl-router" / "token"


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Loaded configuration. Attribute access for the hot fields."""

    __slots__ = ("data", "path", "state_dir", "token_file", "_dirs_file")

    def __init__(self, data: dict, *, path: Path | None = None,
                 state_dir: Path | None = None, token_file: Path | None = None,
                 dirs_file: Path | None = None):
        self.data = data
        self.path = path
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        self.token_file = Path(token_file) if token_file else default_token_file()
        self._dirs_file = Path(dirs_file) if dirs_file else None

    @property
    def dirs_file(self) -> Path:
        """The directory-kind classification (see dirkinds.py).

        Host-specific and never committed, so it lives beside config.toml
        rather than in the repo. Imported lazily to keep this loader free of a
        dependency on the matcher.
        """
        if self._dirs_file is not None:
            return self._dirs_file
        from dirkinds import default_dirs_file
        return default_dirs_file()

    # --- hot fields ------------------------------------------------------- #
    @property
    def library_root(self) -> Path | None:
        raw = (self.data.get("library_root") or "").strip()
        return Path(raw).expanduser() if raw else None

    @property
    def host(self) -> str:
        return str(self.data.get("host") or DEFAULT_HOST)

    @property
    def port(self) -> int:
        """`0` is NOT "unset".

        This was `int(data.get("port") or DEFAULT_PORT)`, so a configured `0`
        silently became 8791 -- while `_validate` rejects `0` as out of range.
        Two places disagreeing about what `0` means is the exact class of
        divergence this round exists to eliminate, and on the degraded path
        (which skips validation) it was reachable: `DL_ROUTER_PORT=0` bound the
        live sidecar's port instead of failing.
        """
        raw = self.data.get("port")
        if raw is None or raw == "":
            return DEFAULT_PORT
        return int(raw)

    @property
    def auto_threshold(self) -> float:
        return float(self.data["auto_threshold"])

    @property
    def tie_margin(self) -> float:
        return float(self.data["tie_margin"])

    @property
    def other_dir(self) -> str:
        return str(self.data.get("other_dir") or DEFAULT_OTHER_DIR)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "dl-router.sqlite3"

    def get(self, key, default=None):
        return self.data.get(key, default)

    def section(self, name: str) -> dict:
        val = self.data.get(name)
        return val if isinstance(val, dict) else {}

    def require_library_root(self) -> Path:
        root = self.library_root
        if root is None:
            raise ConfigError(
                "library_root is not configured — set it in "
                f"{self.path or default_config_path()}")
        if not root.is_absolute():
            raise ConfigError(f"library_root must be absolute: {root}")
        return root


def _tighten_permissions(path: Path) -> None:
    """config.toml is where the qBittorrent password lives.

    The setup instructions are `cp config.example.toml ~/.config/...`, which
    lands 0644 — world-readable credentials. The token file is already created
    0600 by `load_or_create_token`; this brings the config in line. Best effort:
    a config on a filesystem that cannot represent the mode is not fatal.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            os.chmod(path, 0o600)
    except OSError:
        pass


def load(path: Path | None = None, *, env=None) -> Config:
    """Load config.toml over DEFAULTS, then apply env overrides.

    A missing file is fine (all-defaults). A malformed one is fatal — silently
    falling back to defaults would route downloads to the wrong place.
    """
    env = os.environ if env is None else env
    path = Path(path) if path is not None else (
        Path(env["DL_ROUTER_CONFIG"]) if env.get("DL_ROUTER_CONFIG")
        else default_config_path())

    raw: dict = {}
    if path.exists():
        _tighten_permissions(path)
        try:
            with open(path, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc

    data = _deep_merge(DEFAULTS, raw)

    if env.get("DL_ROUTER_LIBRARY_ROOT"):
        data["library_root"] = env["DL_ROUTER_LIBRARY_ROOT"]
    if env.get("DL_ROUTER_HOST"):
        data["host"] = env["DL_ROUTER_HOST"]
    if env.get("DL_ROUTER_PORT"):
        try:
            data["port"] = int(env["DL_ROUTER_PORT"])
        except ValueError as exc:
            raise ConfigError("DL_ROUTER_PORT must be an integer") from exc

    state_dir = Path(env["DL_ROUTER_STATE_DIR"]) if env.get("DL_ROUTER_STATE_DIR") \
        else default_state_dir()
    token_file = Path(env["DL_ROUTER_TOKEN_FILE"]) if env.get("DL_ROUTER_TOKEN_FILE") \
        else default_token_file()
    dirs_file = Path(env["DL_ROUTER_DIRS_FILE"]) \
        if env.get("DL_ROUTER_DIRS_FILE") else None

    _validate(data)
    return Config(data, path=path, state_dir=state_dir, token_file=token_file,
                  dirs_file=dirs_file)


def _validate(data: dict) -> None:
    for key in ("auto_threshold", "tie_margin"):
        try:
            val = float(data[key])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be a number") from exc
        if not 0.0 <= val <= 1.0:
            raise ConfigError(f"{key} must be within [0, 1] (got {val})")
    try:
        port = int(data["port"])
    except (TypeError, ValueError) as exc:
        raise ConfigError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"port out of range: {port}")
    rules = data.get("site_rules")
    if rules is not None and not isinstance(rules, dict):
        raise ConfigError("site_rules must be a table keyed by hostname")
    _validate_site_rules(rules or {})


def _validate_site_rules(rules: dict) -> None:
    """Structural checks on the two-layer rule table.

    Deliberately STRUCTURAL only -- types and required fields, not the selector
    grammar. The grammar is enforced where it is consumed
    (player_buttons.normalisePlayerRule), which fails closed to "no button" so
    a bad selector costs one player rather than the sidecar.

    A wrong SHAPE is different: `media = "video"` instead of a table is a typo
    that would otherwise produce a rule the extension silently discards, with
    nothing anywhere saying why. Raising here surfaces it in `/healthz` and
    `dl-route status` via the degraded-config path, which is the one place the
    operator looks.
    """
    for host, entry in rules.items():
        where = f"site_rules.{host!r}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        for layer, table in (("", entry), ("context", entry.get("context"))):
            if table is None:
                continue
            if not isinstance(table, dict):
                raise ConfigError(f"{where}.context must be a table")
            label = f"{where}.{layer}" if layer else where
            for key in ("subject", "tags"):
                val = table.get(key)
                if val is None:
                    continue
                if not isinstance(val, list) or not all(
                        isinstance(s, str) for s in val):
                    raise ConfigError(
                        f"{label}.{key} must be a list of selector strings")
        player = entry.get("player")
        if player is None:
            continue
        if not isinstance(player, dict):
            raise ConfigError(f"{where}.player must be a table")
        for key in ("container", "mount", "label"):
            if key in player and not isinstance(player[key], str):
                raise ConfigError(f"{where}.player.{key} must be a string")
        if not player.get("container"):
            raise ConfigError(f"{where}.player.container is required")
        media = player.get("media")
        # ONE accessor or an ORDERED LIST of them. The list exists because a
        # single {element, attr} pair cannot express "read the image's ANCHOR
        # href, but the video's own src" -- `attr` is one name, so a rule that
        # covers both media kinds is impossible without it. Tried in order,
        # first http(s) hit wins (player_buttons.readMediaUrl).
        accessors = media if isinstance(media, list) else [media]
        if not isinstance(media, (dict, list)):
            raise ConfigError(
                f"{where}.player.media must be a table, or a list of them "
                # SINGLE braces. This half is a plain adjacent string, not an
                # f-string, so `{{` is never consumed and the operator was
                # shown a literal `{{ ... }}` they cannot paste into TOML.
                '(e.g. media = { element = "video#main", attr = "src" })')
        if not accessors:
            raise ConfigError(
                f"{where}.player.media must not be an empty list -- a rule "
                "with no accessor can never resolve a URL, and would render a "
                "button that always fails")
        if len(accessors) > MAX_MEDIA_ACCESSORS:
            raise ConfigError(
                f"{where}.player.media may list at most "
                f"{MAX_MEDIA_ACCESSORS} accessors, got {len(accessors)}")
        for idx, acc in enumerate(accessors):
            # The index is only shown for a list, so the single-table message
            # is byte-identical to what it has always been.
            at = f"{where}.player.media" if not isinstance(media, list) \
                else f"{where}.player.media[{idx}]"
            if not isinstance(acc, dict):
                raise ConfigError(f"{at} must be a table")
            for key in ("element", "attr"):
                if not isinstance(acc.get(key), str) or not acc[key]:
                    raise ConfigError(
                        f"{at}.{key} must be a non-empty string")


def load_degraded(path: Path | None = None, *, env=None):
    """`load()`, but a malformed config yields an UNCONFIGURED Config instead
    of raising.

    The systemd unit is `Restart=always` + `RestartSec=10`, so a fatal
    ConfigError on a typo'd config.toml was a silent 6-restarts-per-minute
    loop with nothing listening. Degrading to "library_root unset" reuses the
    behaviour that already exists for an unconfigured host: /healthz answers
    and reports the problem, every routing endpoint returns 503, and nothing
    is routed anywhere by accident.

    Returns `(config, error_or_None)`.
    """
    try:
        return load(path, env=env), None
    except ConfigError as exc:
        env = os.environ if env is None else env
        data = copy.deepcopy(DEFAULTS)
        # config.toml is unreadable, so nothing from it can be trusted -- but
        # DL_ROUTER_LIBRARY_ROOT is set by the unit, not by the broken file, so
        # a host configured entirely through the environment still works.
        data["library_root"] = ""
        # The bind address still has to come from somewhere the typo cannot
        # have broken: the unit's own environment, else the loopback defaults.
        # The typo is in config.toml; the unit's own environment is still
        # good. Anything invalid there falls back to the default rather than
        # being silently reinterpreted.
        if env.get("DL_ROUTER_LIBRARY_ROOT"):
            data["library_root"] = env["DL_ROUTER_LIBRARY_ROOT"]
        if env.get("DL_ROUTER_HOST"):
            data["host"] = env["DL_ROUTER_HOST"]
        if env.get("DL_ROUTER_PORT"):
            try:
                port = int(env["DL_ROUTER_PORT"])
            except ValueError:
                port = DEFAULT_PORT
            data["port"] = port if 1 <= port <= 65535 else DEFAULT_PORT
        resolved = Path(path) if path is not None else (
            Path(env["DL_ROUTER_CONFIG"]) if env.get("DL_ROUTER_CONFIG")
            else default_config_path())
        state_dir = Path(env["DL_ROUTER_STATE_DIR"]) \
            if env.get("DL_ROUTER_STATE_DIR") else default_state_dir()
        token_file = Path(env["DL_ROUTER_TOKEN_FILE"]) \
            if env.get("DL_ROUTER_TOKEN_FILE") else default_token_file()
        dirs_file = Path(env["DL_ROUTER_DIRS_FILE"]) \
            if env.get("DL_ROUTER_DIRS_FILE") else None
        return Config(data, path=resolved, state_dir=state_dir,
                      token_file=token_file, dirs_file=dirs_file), str(exc)


def load_or_create_token(path: Path | None = None) -> str:
    """Read the bearer token, creating a 0600 one on first run.

    Stable across restarts so a loaded extension never needs re-pairing (same
    contract as browser-bridge's token file).
    """
    path = Path(path) if path is not None else default_token_file()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tok = secrets.token_urlsafe(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(tok + "\n")
    os.chmod(path, 0o600)
    return tok
