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
    home paths, usernames, IP literals (v4 AND v6), FQDNs, path-mangled project
    slugs, nix store hashes, and the scope names read out of the local index
    store;
  * it cannot know that an identifier it has never seen is sensitive. A new
    class of identifier walks straight through, silently, and the only defence
    is reading the output.

🔴 EVERY CLASS BELOW IS CASE-INSENSITIVE AND HYPHEN-BLIND, AND THAT IS A FIX,
NOT A STYLE CHOICE. Both asymmetries shipped and both leaked, into a page whose
whole purpose is being handed to an outsider:

  * the username rule was case-SENSITIVE while the hostname rule was not, so the
    operator's given name survived in a sentence that capitalised it while the
    same name in a path was replaced. Same asymmetry in the scope rule.
  * the word boundary was `(?![\\w-])`, which treats a hyphen as part of the
    word. A username or scope name in the MIDDLE of a hyphenated token — a
    systemd unit named after a client, a git-remote org, a pytest temp dir —
    was therefore invisible to a rule that had been shown the exact name.

🔴 RECOGNISING A HOST IS AN ALLOW-LIST, NEVER A DENY-LIST. The first cut treated
any dotted lowercase token as a hostname unless its suffix was in a hand-listed
set of "not a TLD" extensions. That list can only ever enumerate the collisions
somebody already hit, so an attribute chain, a config key or a database table
name became a fake hostname the moment it was written: a shareable page rendered
`git config --get host-07.example.test`, a command that cannot work, on a page
whose thesis is "run the settle command yourself". `HOST_TLDS` inverts it: a
token is a host only if its last label is a suffix this module recognises. The
failure direction is now a host that is NOT rewritten — which the module already
tells you to look for — rather than prose that is silently corrupted.

`test_present_sanitize.py` drives both directions: that every known class is
substituted, AND that the substitution is STABLE (the same input maps to the
same stand-in every time), because an unstable map makes two builds of the same
page uncomparable. It also drives the CORRUPTION direction — a set of dotted
tokens that must survive untouched.

🔴 A CLASS THAT DEGRADES MUST SAY SO. Scope substitution is only as good as the
local index store; on a host with no store there are no scope names to swap and
the page would still print `mode SANITIZED`. Every such degradation is recorded
on the `Sanitizer` and surfaced twice — in the page's own legend, and on stderr
at build time — because a silent zero here reads exactly like a clean run.

🔴 THE MAP IS BUILT AT RUN TIME FROM LOCAL STATE. No real scope name, hostname
or address is written down in this file or in any committed fixture — that is
the `CLAUDE.md` PUBLIC-repo rule, and a fixture is exactly the arrival path its
gates were written for.
"""
from __future__ import annotations

import ipaddress
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

#: Directory names a path-mangled slug can be rooted at.
#:
#: 🔴 `-home-` ALONE WAS NOT THE CLASS. The first rule matched `-home-…` only,
#: so every project rooted outside the operator's home — a worktree under
#: `/tmp`, a pytest temp dir carrying `pytest-of-<user>` — passed through
#: verbatim while the page announced itself sanitized. `run` is deliberately
#: ABSENT: `-run-tests…` is ordinary prose in this repo and a slug rooted at
#: `/run` is not a thing, so including it would trade a leak for a corruption.
_SLUG_ROOTS = ("home", "Users", "tmp", "var", "mnt", "media", "opt", "srv",
               "data", "root", "usr")
#: A PATH-MANGLED PROJECT SLUG — an absolute path with its separators rewritten
#: as dashes, which is how the harness names a per-project state directory.
#:
#: 🔴 THIS RULE EXISTS BECAUSE THE CLASS LEAKED THROUGH A PASSING SUITE. The
#: home-path rule matches `/home/<user>/…` and the scope rule matches store
#: scope names; a slug is NEITHER, so client and repo names walked straight into
#: a sanitized page while every substitution test stayed green. It is the worked
#: instance of `test_the_sanitizer_cannot_see_an_unknown_identifier_class` — read
#: that test before trusting this module with a value it has not been shown.
#:
#: 🔴 AND IT LEAKED A SECOND TIME IN THE SAME SHAPE. The character class was
#: `[a-z0-9._-]`, lowercase-only, so an uppercase path component TRUNCATED the
#: match and the deepest — most identifying — component survived beside a
#: stand-in that made the output look sanitized. The class is now case-blind and
#: the whole rule carries `re.I`. At least two dash-separated components must
#: follow the root, which is what keeps ordinary hyphenated prose out.
PROJECT_SLUG = re.compile(
    r"(?<![\w-])-(?:" + "|".join(_SLUG_ROOTS) + r")(?:-[A-Za-z0-9._]*){2,}",
    re.I,
)

IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
#: A CANDIDATE v6 literal — hex digits and colons only. Deliberately loose,
#: because `ipaddress` (not a regex) decides whether it is an address: a
#: hand-rolled v6 grammar is how `10:30:00` and `aa:bb:cc` become "addresses".
IPV6_CANDIDATE = re.compile(r"(?<![\w:.])[0-9A-Fa-f:]{3,45}(?![\w:.])")

#: A dotted host, recognised by its LAST LABEL.
FQDN = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.){1,3}(?:[a-z]{2,})\b", re.I)
#: 🔴 THE ALLOW-LIST. A dotted token is a hostname only if it ends in one of
#: these. It is deliberately INCOMPLETE, and every exclusion is the same
#: judgement: the collision cost is a CORRUPTED COMMAND in a document written
#: for an outsider, the miss cost is a hostname the reader can see and redact.
#: So a suffix is out if it is also
#:
#:   * a common file extension — `sh` (Saint Helena), `md` (Moldova), `rs`,
#:     `pl`, `so`, `cc`, `zip`;
#:   * a common English word — `is`, `it`, `in`, `to`, `no`, `at`, `by`;
#:   * 🔴 a common English NOUN sold as a new gTLD — `store`, `page`, `link`,
#:     `live`, `team`, `email`, `tools`, `systems`, `services`, `network`,
#:     `digital`, `site`, `online`, `shop`, `space`, `tech`, `cloud`, `app`,
#:     `wiki`, `blog`, `xyz`, `int`, `name`.
#:
#: That last group is not hypothetical caution. It was MEASURED: with `store` on
#: the list, this page's own registry key `index.store` rendered as
#: `host-01.example.test` — the identical failure the deny-list produced, walked
#: straight back in through the replacement. A dotted lowercase token in this
#: document is far more often an attribute, a config key, a database table or a
#: measurement key than a host, and the new-gTLD namespace is precisely the set
#: of words those are named after.
#:
#: A host whose TLD is missing here is the module's documented unknown-class
#: limitation, not a surprise — `test_the_tld_allowlist_is_a_documented_
#: incompleteness_not_a_promise` pins it, and the operator is told to read the
#: output either way.
HOST_TLDS = frozenset({
    # classic gTLDs
    "com", "net", "org", "edu", "gov", "mil", "info", "biz",
    # the three new ones this repo actually hosts on, and which are not nouns
    "io", "ai", "dev",
    # ccTLDs that are not English words
    "co", "me", "tv", "uk", "de", "fr", "nl", "eu", "ca", "au", "nz",
    "jp", "br", "za", "ie", "ch", "se", "dk", "fi",
})
#: `.test`, `.example`, `.invalid` and `.localhost` are RFC 6761 reserved and are
#: already synthetic, so they are left alone — rewriting them would make a
#: sanitized page look less honest, not more. They are absent from `HOST_TLDS`
#: too; this set exists so the intent is stated rather than inferred from a gap.
RESERVED_TLDS = {"test", "example", "invalid", "localhost"}
#: Hosts that name no third party and carry no topology.
#:
#: 🔴 `www.w3.org` IS LOAD-BEARING. It is the SVG XML namespace, the one
#: external-looking token `generate.self_contained()` allows. Rewriting it would
#: break every inlined diagram AND make the page fail its own self-containment
#: check, because the allowed-URI subtraction would no longer match.
SAFE_HOSTS = {"localhost", "example.com", "example.test", "www.w3.org"}
#: Addresses that identify nothing.
SAFE_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "10.0.0.0"}
#: RFC 3849 — the v6 documentation prefix. Already synthetic; also what this
#: module SUBSTITUTES INTO, so leaving it alone is what makes `text()` stable
#: under a second application.
DOC_V6 = ipaddress.IPv6Network("2001:db8::/32")

#: Identifiers this module deliberately does NOT substitute.
#:
#: 🔴 SANITIZE BOTH OR NEITHER. The page's `<title>`, masthead, nav and footer
#: name this repository in plain text, and the repository is PUBLIC. Rewriting
#: the same name in the BODY produced an artefact that half-identified itself —
#: it left the owning organisation legible while obscuring the repo name that
#: organisation publicly owns. This is the "neither" branch, and it needs no
#: coordination with the renderer.
NOT_SUBSTITUTED = frozenset({"devrc"})


#: Below this length, a scope name is matched only in its EXACT form.
#:
#: 🔴 THE LADDER EXISTS BECAUSE THE FIX FOR THE LEAK CREATED A CORRUPTION. Going
#: case-insensitive and hyphen-blind on every scope rewrote the English word
#: "CLI" — an index-store scope really is named `cli` — into a stand-in, in
#: prose, on the shareable page. Short scope names are acronyms and acronyms are
#: words. So: a name of any length is substituted where it appears exactly; only
#: a name long enough to be a real identifier gets the aggressive treatment. The
#: weaker case is COUNTED, never silent — a capitalised or embedded occurrence
#: of a short scope walks through, and the legend says so.
SCOPE_AGGRESSIVE_MIN = 5


def _word(literal: str) -> str:
    """A boundary that treats a HYPHEN as a separator, not as part of the word.

    🔴 The `-`-inclusive boundary this replaces is why a name the module had
    been shown survived inside `pytest-of-<user>-pytest-0`, inside a systemd
    unit named `<client>-sync.timer`, and inside a git-remote org.
    """
    return rf"(?<!\w){re.escape(literal)}(?!\w)"


def _token(literal: str) -> str:
    """The CONSERVATIVE boundary: a hyphen counts as part of the word.

    Used where matching inside a hyphenated token would do more damage than the
    occurrence it would catch.
    """
    return rf"(?<![\w-]){re.escape(literal)}(?![\w-])"


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
    #: Dotless machine names read from local state. See `_hosts` for why only
    #: some of them can safely be substituted.
    hostnames: tuple[str, ...] = ()
    keep: frozenset[str] = NOT_SUBSTITUTED
    #: Why a class is weaker than it claims on THIS build. Set by `build()`.
    degraded: tuple[str, ...] = ()
    _map: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    #: kind -> how many real values this run declined to substitute.
    _skipped: dict[str, int] = field(default_factory=dict)

    def _next(self, kind: str) -> int:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return self._counters[kind]

    def _stand_in(self, kind: str, real: str, fmt) -> str:
        """One real value -> one stand-in, forever, per Sanitizer instance."""
        key = f"{kind}:{real}"
        if key not in self._map:
            self._map[key] = fmt(self._next(kind))
        return self._map[key]

    def _skip(self, kind: str, real: str) -> None:
        """Record a value this run KNEW about and chose not to substitute.

        🔴 A declined substitution is a hole in the redaction, and a hole nobody
        is told about is indistinguishable from no hole. Counted here, printed
        in the legend, echoed on stderr by the generator.
        """
        key = f"{kind}:{real}"
        if key not in self._skipped:
            self._skipped[key] = 1

    # -- the individual classes ---------------------------------------------- #

    def _scopes(self, text: str) -> str:
        # Longest first: a scope named `foo` must not eat the prefix of `foo-bar`.
        for real in sorted(self.scopes, key=len, reverse=True):
            if not real:
                continue
            if real.lower() in self.keep:
                continue
            if len(real) < 3:
                # A one- or two-character name matches inside half the words on
                # the page. Declining is the right call; declining SILENTLY is
                # not, so it is counted.
                self._skip("scope-too-short-to-match-safely", real)
                continue
            stand = self._stand_in("scope", real, lambda n: f"scope-{n:02d}")
            if len(real) >= SCOPE_AGGRESSIVE_MIN:
                text = re.sub(_word(real), stand, text, flags=re.I)
            else:
                # Exact form only. Recorded, because "matched in one casing" is
                # a hole in the redaction and a hole nobody is told about is
                # indistinguishable from no hole.
                self._skip("scope-matched-in-its-exact-form-only", real)
                text = re.sub(_token(real), stand, text)
        return text

    def _slugs(self, text: str) -> str:
        return PROJECT_SLUG.sub(
            lambda m: self._stand_in("project", m.group(0).lower(),
                                     lambda n: f"project-{n:02d}"),
            text,
        )

    def _home(self, text: str) -> str:
        if self.home:
            text = re.sub(re.escape(self.home), "/home/operator", text, flags=re.I)
        if self.user and len(self.user) >= 3:
            text = re.sub(_word(self.user), "operator", text, flags=re.I)
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

    def _ips6(self, text: str) -> str:
        """v6 literals, validated by `ipaddress` rather than by a grammar."""
        def sub(m):
            v = m.group(0)
            if ":" not in v:
                return v
            try:
                addr = ipaddress.IPv6Address(v)
            except ValueError:
                return v          # a timestamp, a MAC, a `host:port` fragment
            if addr.is_loopback or addr.is_unspecified or addr in DOC_V6:
                return v
            return self._stand_in("ip6", v, lambda n: f"2001:db8::{n:x}")
        return IPV6_CANDIDATE.sub(sub, text)

    def _hosts(self, text: str) -> str:
        def sub(m):
            v = m.group(0)
            low = v.lower()
            tld = low.rsplit(".", 1)[-1]
            if low in SAFE_HOSTS or tld in RESERVED_TLDS:
                return v
            if IPV4.fullmatch(v):
                return v          # already handled, and handled better
            if tld not in HOST_TLDS:
                return v          # not a host this module can recognise
            return self._stand_in("host", low, lambda n: f"host-{n:02d}.example.test")
        text = FQDN.sub(sub, text)
        return self._bare_hosts(text)

    def _bare_hosts(self, text: str) -> str:
        """Dotless machine names — the class that CANNOT be recognised by shape.

        🔴 A bare hostname is structurally identical to an ordinary word, so
        this substitutes only names read from local state, and only those that
        look like a machine name rather than a word: they must carry a hyphen or
        a digit. A generic nodename (`nixos`, `laptop`) is DECLINED and counted,
        because rewriting it would turn every occurrence of that word on the
        page into a fake hostname — the exact corruption `HOST_TLDS` exists to
        stop, arriving through a different door.
        """
        for real in sorted(self.hostnames, key=len, reverse=True):
            if not real or len(real) < 4 or real.lower() in self.keep:
                continue
            if not re.search(r"[-\d]", real):
                self._skip("hostname-indistinguishable-from-a-word", real.lower())
                continue
            stand = self._stand_in("host", real.lower(),
                                   lambda n: f"host-{n:02d}.example.test")
            text = re.sub(_word(real), stand, text, flags=re.I)
        return text

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
        out = self._ips6(out)
        out = self._hosts(out)
        return out

    @property
    def substitutions(self) -> int:
        return len(self._map)

    def legend(self) -> tuple[tuple[str, str], ...]:
        """(kind, count) for everything substituted — WITHOUT the real value.

        🔴 The real side is deliberately absent. A legend that printed both
        columns would re-publish, in the sanitized artefact, exactly the values
        the sanitized artefact exists to remove.

        Declined substitutions and degraded classes are counted here too, under
        their own kinds. They are the part of the redaction that did NOT happen,
        and the legend is the only place the reader of the page sees it.
        """
        counts: dict[str, int] = {}
        for source in (self._map, self._skipped):
            for key in source:
                kind = key.split(":", 1)[0]
                counts[kind] = counts.get(kind, 0) + 1
        if self.degraded:
            counts["NOT-SUBSTITUTED-see-build-log"] = len(self.degraded)
        return tuple(sorted(counts.items()))

    def warnings(self) -> tuple[str, ...]:
        """Every reason this run redacted LESS than the flag implies.

        Printed by the generator on stderr. `--sanitize` that quietly did almost
        nothing must not look like `--sanitize` that worked.
        """
        out = list(self.degraded)
        for key in sorted(self._skipped):
            kind, _, _real = key.partition(":")
            out.append(f"a value was NOT substituted: {kind.replace('-', ' ')}")
        return tuple(out)


def build(enabled: bool, env, measurements=None) -> Sanitizer:
    """Construct a Sanitizer from LOCAL STATE — never from a committed literal."""
    import os
    import socket

    scopes: list[str] = []
    degraded: list[str] = []
    if measurements is None:
        degraded.append(
            "no measurements were handed to sanitize.build(), so NO scope name "
            "is substituted — repo and client names in skill descriptions pass through")
    else:
        store = measurements.by_key("index.store")
        if store is None:
            degraded.append(
                "no `index.store` row is registered, so the scope-name class has "
                "no source and NO scope name is substituted")
        elif not store.measured:
            degraded.append(
                "the index store came back UNMEASURED "
                f"({store.reason or 'no reason given'}), so NO scope name is "
                "substituted — repo and client names in skill descriptions pass through")
        else:
            scopes = [row[0] for row in store.rows if row and row[0]]
            if not scopes:
                degraded.append(
                    "the index store measured ZERO scopes, so no scope name is "
                    "substituted — an empty scope list is not a clean one")
    home = str(env.home).rstrip("/")
    try:
        node = socket.gethostname()
    except OSError:
        node = ""
    hostnames = tuple(sorted({n for n in (node, node.split(".")[0]) if n}))
    return Sanitizer(
        enabled=enabled,
        home=home,
        user=os.path.basename(home),
        scopes=tuple(scopes),
        hostnames=hostnames,
        degraded=tuple(degraded),
    )


def apply(measurements, san: Sanitizer):
    """Return a new MeasurementSet with every string field sanitized.

    Every field is passed through, including `reason` and `settle` — an
    UNMEASURED row's reason is as likely to name a real path as a measured row's
    value, and the row that gets skipped is the one that leaks.

    🔴 `settle` IS SANITIZED ON EVERY ROW, INCLUDING MEASURED ONES, AND THAT IS
    DELIBERATE EVEN THOUGH THE RENDERER ONLY PRINTS IT FOR ABSENCES. The
    alternative is a field whose safety depends on a rendering decision made in
    another module: the day someone prints `settle` beside a measured row, a
    real path ships. The cost is a handful of extra entries in the legend count;
    the cost of the other order is a leak nobody can see coming.
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
