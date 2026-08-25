#!/usr/bin/env python3
"""Swap real identifiers for synthetic stand-ins, so ONE generator can produce
both the private page and a publicly-shareable variant.

🔴 WHY A FLAG AND NOT A SECOND GENERATOR. The alternative — a "public build"
script beside the real one — is two renderers that must be kept in sync, and the
public one is the copy nobody runs, so it is the copy that rots. A flag means the
sanitized page is the same page with a substitution applied at the last possible
moment.

WHAT IT IS AND IS NOT
---------------------
It is a REDACTION AID for a page that is about to be shown to someone. It is NOT
a security boundary and it must never be described as one:

  * it substitutes the identifier CLASSES this repo's own content gates name —
    home paths, usernames, IPv4 literals, FQDNs, nix store hashes, and the scope
    names read out of the local index store;
  * it cannot know that an identifier it has never seen is sensitive. A new
    class of identifier walks straight through, silently, and the only defence
    is reading the output.

`test_present_sanitize.py` drives both directions: that every known class is
substituted, AND that the substitution is STABLE (the same input maps to the
same stand-in every time), because an unstable map makes two builds of the same
page uncomparable.

🔴 THE MAP IS BUILT AT RUN TIME FROM LOCAL STATE. No real scope name, hostname
or address is written down in this file or in any committed fixture — that is
the `CLAUDE.md` PUBLIC-repo rule, and a fixture is exactly the arrival path its
gates were written for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Patterns
#
# Ordered most-specific-first. A store hash must be replaced before the path it
# sits in is touched, or the generic path rule eats the token the specific rule
# was going to match.
# --------------------------------------------------------------------------- #

STORE_PATH = re.compile(r"/nix/store/[a-z0-9]{32}-([A-Za-z0-9._+-]+)")
#: A PATH-MANGLED PROJECT SLUG — an absolute path with its separators rewritten
#: as dashes, which is how the harness names a per-project state directory.
#:
#: 🔴 THIS RULE EXISTS BECAUSE THE CLASS LEAKED THROUGH A PASSING SUITE. The
#: home-path rule matches `/home/<user>/…` and the scope rule matches store
#: scope names; a slug is NEITHER, so client and repo names walked straight into
#: a sanitized page while every substitution test stayed green. It is the worked
#: instance of `test_the_sanitizer_cannot_see_an_unknown_identifier_class` — read
#: that test before trusting this module with a value it has not been shown.
PROJECT_SLUG = re.compile(r"-home-[a-z0-9][a-z0-9._-]{4,}")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
#: A dotted host with a real-looking TLD. `.test`, `.example`, `.invalid` and
#: `.localhost` are RFC 6761 reserved and are already synthetic, so they are left
#: alone — rewriting them would make a sanitized page look less honest, not more.
FQDN = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.){1,3}(?:[a-z]{2,})\b", re.I)
RESERVED_TLDS = {"test", "example", "invalid", "localhost"}
#: Hosts that name no third party and carry no topology.
SAFE_HOSTS = {"localhost", "example.com", "example.test"}
#: Addresses that identify nothing.
SAFE_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "10.0.0.0"}
#: Common dotted tokens that are NOT hostnames — file names, module paths.
_NOT_A_HOST_TLD = {
    "py", "sh", "md", "json", "jsonl", "nix", "mjs", "js", "ts", "html", "css",
    "txt", "yaml", "yml", "toml", "lock", "svg", "png", "conf", "tsv", "log",
    "service", "timer", "socket", "sops", "gitignore", "envrc", "zshrc",
}


@dataclass
class Sanitizer:
    """Stable, run-time-built identifier substitution.

    `enabled=False` is the identity transform — the private build calls exactly
    the same code path, so the sanitized build is never a differently-shaped
    program.
    """

    enabled: bool = False
    home: str = ""
    user: str = ""
    scopes: tuple[str, ...] = ()
    _map: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def _next(self, kind: str) -> int:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return self._counters[kind]

    def _stand_in(self, kind: str, real: str, fmt) -> str:
        """One real value -> one stand-in, forever, per Sanitizer instance."""
        key = f"{kind}:{real}"
        if key not in self._map:
            self._map[key] = fmt(self._next(kind))
        return self._map[key]

    # -- the individual classes ---------------------------------------------- #

    def _scopes(self, text: str) -> str:
        # Longest first: a scope named `foo` must not eat the prefix of `foo-bar`.
        for real in sorted(self.scopes, key=len, reverse=True):
            if not real or len(real) < 3:
                continue
            stand = self._stand_in("scope", real, lambda n: f"scope-{n:02d}")
            text = re.sub(rf"(?<![\w-]){re.escape(real)}(?![\w-])", stand, text)
        return text

    def _slugs(self, text: str) -> str:
        return PROJECT_SLUG.sub(
            lambda m: self._stand_in("project", m.group(0), lambda n: f"project-{n:02d}"),
            text,
        )

    def _home(self, text: str) -> str:
        if self.home:
            text = text.replace(self.home, "/home/operator")
        if self.user and len(self.user) >= 3:
            text = re.sub(rf"(?<![\w-]){re.escape(self.user)}(?![\w-])", "operator", text)
        return text

    def _store(self, text: str) -> str:
        return STORE_PATH.sub(lambda m: f"/nix/store/<hash>-{m.group(1)}", text)

    def _ips(self, text: str) -> str:
        def sub(m):
            v = m.group(0)
            if v in SAFE_IPS:
                return v
            if not all(0 <= int(p) <= 255 for p in v.split(".")):
                return v          # a version string, not an address
            return self._stand_in("ip", v, lambda n: f"198.51.100.{n}")
        return IPV4.sub(sub, text)

    def _hosts(self, text: str) -> str:
        def sub(m):
            v = m.group(0)
            low = v.lower()
            tld = low.rsplit(".", 1)[-1]
            if tld in RESERVED_TLDS or tld in _NOT_A_HOST_TLD or low in SAFE_HOSTS:
                return v
            if IPV4.fullmatch(v):
                return v          # already handled, and handled better
            return self._stand_in("host", low, lambda n: f"host-{n:02d}.example.test")
        return FQDN.sub(sub, text)

    # -- the public surface --------------------------------------------------- #

    def text(self, value: str | None) -> str | None:
        """Sanitize one string. `None` in, `None` out — never an empty string.

        Collapsing `None` to `""` would turn "this row was never measured" into
        "this row measured empty", which is the silent-zero this whole page is
        built against.
        """
        if value is None or not self.enabled:
            return value
        out = self._store(value)
        out = self._slugs(out)      # before _home: a slug has no `/` to match on
        out = self._home(out)
        out = self._scopes(out)
        out = self._ips(out)
        out = self._hosts(out)
        return out

    @property
    def substitutions(self) -> int:
        return len(self._map)

    def legend(self) -> tuple[tuple[str, str], ...]:
        """(kind, stand-in) for everything substituted — WITHOUT the real value.

        🔴 The real side is deliberately absent. A legend that printed both
        columns would re-publish, in the sanitized artefact, exactly the values
        the sanitized artefact exists to remove.
        """
        counts: dict[str, int] = {}
        for key in self._map:
            counts[key.split(":", 1)[0]] = counts.get(key.split(":", 1)[0], 0) + 1
        return tuple(sorted(counts.items()))


def build(enabled: bool, env, measurements=None) -> Sanitizer:
    """Construct a Sanitizer from LOCAL STATE — never from a committed literal."""
    import os

    scopes: list[str] = []
    if measurements is not None:
        store = measurements.by_key("index.store")
        if store is not None and store.measured:
            scopes = [row[0] for row in store.rows if row and row[0]]
    home = str(env.home).rstrip("/")
    return Sanitizer(
        enabled=enabled,
        home=home,
        user=os.path.basename(home),
        scopes=tuple(scopes),
    )


def apply(measurements, san: Sanitizer):
    """Return a new MeasurementSet with every string field sanitized.

    Every field is passed through, including `reason` and `settle` — an
    UNMEASURED row's reason is as likely to name a real path as a measured row's
    value, and the row that gets skipped is the one that leaks.
    """
    from dataclasses import replace

    from present import measure as _m

    out = _m.MeasurementSet()
    for item in measurements:
        out.items.append(replace(
            item,
            value=san.text(item.value),
            detail=san.text(item.detail) or "",
            source=san.text(item.source) or "",
            reason=san.text(item.reason),
            settle=san.text(item.settle),
            rows=tuple(tuple(san.text(c) or "" for c in row) for row in item.rows),
        ))
    return out
