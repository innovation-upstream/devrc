"""dl-router configuration loader.

Everything host-specific lives OUTSIDE the repo:

    ~/.config/dl-router/config.toml   settings (library root, qBittorrent creds,
                                      per-site capture rules)
    ~/.config/dl-router/token         bearer token, 0600, auto-created
    ~/.local/share/dl-router/         SQLite store + backfill manifests

The repo ships only neutral defaults and NO paths into anybody's media library.
`library_root` has no default on purpose: the sidecar refuses to serve routing
endpoints until it is configured.

Env overrides (all optional, mainly for tests + the systemd unit):

    DL_ROUTER_CONFIG        path to config.toml
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

    __slots__ = ("data", "path", "state_dir", "token_file")

    def __init__(self, data: dict, *, path: Path | None = None,
                 state_dir: Path | None = None, token_file: Path | None = None):
        self.data = data
        self.path = path
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        self.token_file = Path(token_file) if token_file else default_token_file()

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
        return int(self.data.get("port") or DEFAULT_PORT)

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

    _validate(data)
    return Config(data, path=path, state_dir=state_dir, token_file=token_file)


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
