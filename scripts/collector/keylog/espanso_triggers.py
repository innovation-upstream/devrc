"""espanso_triggers — parse the live espanso config into a trigger model.

espanso, on firing a trigger, BACKSPACES the trigger away and inserts the
replacement via a CLIPBOARD paste — so BOTH the trigger and its expansion are
ERASED from the keystroke stream the chunker reconstructs. Espanso usage is
therefore invisible in stored `text` and must be detected AT CAPTURE TIME from
the user's real keystrokes (which arrive before espanso reacts). This module
loads the trigger set the detector matches against.

Pure + testable: `load_triggers` accepts file PATHS or pre-loaded dicts. It is
robust to home-manager's `replace:`-before-`trigger:` key ordering, to snippets
missing a `label`/`search_terms`, and to a MISSING/unparseable config — in which
case it returns an empty TriggerSet (never raises), so the keylogger's detector
is inert and typing capture behaves exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

try:  # PyYAML is added to the keylog python env in nix/home.nix; degrade if absent.
    import yaml as _yaml
except Exception:  # pragma: no cover - exercised only where PyYAML is missing
    _yaml = None

# espanso's default search shortcut on this host is CTRL+SPACE (see
# ~/.config/espanso/config/default.yml). Fall back to it when unspecified.
_DEFAULT_SHORTCUT = (True, 0x20)  # (ctrl_required, keysym)

# Named keys → X11 keysym, for parsing the `search_shortcut` value.
_KEY_KEYSYMS = {
    "SPACE": 0x20,
    "TAB": 0xFF09,
    "ENTER": 0xFF0D,
    "RETURN": 0xFF0D,
    "ESC": 0xFF1B,
    "ESCAPE": 0xFF1B,
}
_MODIFIERS = {"CTRL", "CONTROL", "ALT", "SHIFT", "META", "SUPER", "CMD"}


@dataclass
class TriggerSet:
    """The parsed espanso model the detector consumes.

    triggers        — every trigger string (e.g. ":date", "dashbaord").
    meta            — trigger -> {label, search_terms, replace}.
    search_shortcut — (ctrl_required: bool, keysym: int) for the Ctrl+Space
                      search UI; keysym is an X11 keysym so keylog can compare it
                      against a KeyPress at the keysym level.
    """

    triggers: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    search_shortcut: tuple = _DEFAULT_SHORTCUT

    @property
    def max_len(self) -> int:
        return max((len(t) for t in self.triggers), default=0)


def parse_search_shortcut(value) -> tuple:
    """Parse a `search_shortcut` string like "CTRL+SPACE" → (ctrl, keysym)."""
    if not value or not isinstance(value, str):
        return _DEFAULT_SHORTCUT
    parts = [p.strip().upper() for p in value.split("+") if p.strip()]
    if not parts:
        return _DEFAULT_SHORTCUT
    ctrl = any(p in ("CTRL", "CONTROL") for p in parts)
    keysym = None
    for p in parts:
        if p in _MODIFIERS:
            continue
        if p in _KEY_KEYSYMS:
            keysym = _KEY_KEYSYMS[p]
        elif len(p) == 1:
            keysym = ord(p.lower())  # Latin-1 keysym == code point
    if keysym is None:
        return _DEFAULT_SHORTCUT
    return (ctrl, keysym)


def _load_yaml(source, *, is_path: bool):
    """Return a parsed dict from a path or an already-loaded dict; {} on any error."""
    if source is None:
        return {}
    if isinstance(source, dict):
        return source
    if not is_path:
        return {}
    if _yaml is None:
        return {}
    try:
        with open(source, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_matches(base: dict) -> list[dict]:
    out = []
    for m in base.get("matches") or []:
        if isinstance(m, dict):
            out.append(m)
    return out


def load_triggers(base_yml_path=None, default_yml_path=None) -> TriggerSet:
    """Load the espanso trigger model from the match + config files.

    Both args accept a path (str/PathLike) OR a pre-loaded dict (for tests). A
    missing/unparseable file yields an empty contribution — never raises.
    """
    base = _load_yaml(base_yml_path, is_path=not isinstance(base_yml_path, dict))
    default = _load_yaml(default_yml_path, is_path=not isinstance(default_yml_path, dict))

    triggers: list[str] = []
    meta: dict = {}
    seen: set = set()

    for m in _extract_matches(base):
        # A match may carry `trigger` (str) or `triggers` (list); handle both,
        # regardless of key order (home-manager emits `replace:` before `trigger:`).
        trigs = []
        if isinstance(m.get("trigger"), str):
            trigs.append(m["trigger"])
        raw_multi = m.get("triggers")
        if isinstance(raw_multi, list):
            trigs.extend([t for t in raw_multi if isinstance(t, str)])
        if not trigs:
            continue
        label = m.get("label") if isinstance(m.get("label"), str) else ""
        st = m.get("search_terms")
        search_terms = [s for s in st if isinstance(s, str)] if isinstance(st, list) else []
        replace = m.get("replace") if isinstance(m.get("replace"), str) else ""
        for t in trigs:
            if t in seen:
                continue
            seen.add(t)
            triggers.append(t)
            meta[t] = {"label": label, "search_terms": search_terms, "replace": replace}

    shortcut = parse_search_shortcut(default.get("search_shortcut"))
    return TriggerSet(triggers=triggers, meta=meta, search_shortcut=shortcut)


def standard_config_paths():
    """The live espanso config paths (rendered by home-manager)."""
    import os

    base = os.path.expanduser("~/.config/espanso/match/base.yml")
    default = os.path.expanduser("~/.config/espanso/config/default.yml")
    return base, default
