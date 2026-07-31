"""Deterministic match scoring — no LLM, no network, no I/O.

The primary signal is PAGE CONTEXT, not the filename: downloaded filenames from
file-sharing/tube sites are routinely opaque (`8f3a1c92b_source.mp4`), while the
page almost always names its subject in a tag/heading/og field.

Directory naming in a real library is inconsistent (`Title Case`, `lower-kebab`,
`snake_Case` all coexist). The normalisation key folds all three to the same
string, which is why existing directories are never renamed (decision D7):

    NFKD -> strip combining marks -> casefold -> drop every non-alphanumeric

    "Jane Doe" / "jane-doe" / "Jane_Doe"  ->  "janedoe"

Scoring (highest rule wins per directory):

    identity-signal alias hit (Discord channel, forum thread)   1.00
    exact alias hit, site-scoped                                1.00
    exact alias hit, global                                     0.90
    normalised page tag/subject == dir key                      0.85
    token-sequence containment, scaled by coverage         0.60-0.80
    filename token match                                       <=0.50
    host prior (last dir used on this host)              +0.05 ranking only

Guards
    * A fuzzy hit (containment or filename) needs >=2 tokens, or a single token
      of >=4 characters. Without this a 3-letter directory name matches random
      page prose.
    * The host prior is a RANKING nudge and NEVER decisive. It cannot create a
      candidate, cannot carry a directory over the auto-file threshold, cannot
      change which candidate wins, and cannot suppress the tie-break: ordering,
      the threshold and the tie margin all read `base`, the pre-bonus score.
      The prior only breaks a tie between candidates that already have an
      identical `base` -- and such a pair is inside the tie margin by
      definition, so the picker opens anyway.
    * Top two within `tie_margin` -> no auto-file, show the picker.
    * DIRECTORY KIND gates auto-filing. Only a `performer` directory may
      auto-file. A `category` directory always opens the picker regardless of
      score, and an UNCLASSIFIED directory does too -- unknown is "ask", never
      "probably fine". See dirkinds.py.

IDENTITY SIGNALS (the deterministic half)

The evening this module met real traffic, eight of nine downloads scored 0.00
"no signal in page context or filename": six came from a Discord CDN, where the
page is a SPA and the captured context was completely empty. Page scraping
cannot fix that. But the Discord attachment URL carries its channel id, and a
forum attachment URL carries its thread slug -- structured, in the URL, immune
to any UI change. `identity_signals()` turns those into `(key, site)` alias
pairs, and it is the SAME function the matcher looks up and the learner writes,
so the two can never drift apart.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# Score constants — named so tests assert against the spec table, not magic
# numbers scattered through the scorer.
SCORE_ALIAS_SITE = 1.00
SCORE_ALIAS_GLOBAL = 0.90
SCORE_TAG_EXACT = 0.85
SCORE_CONTAIN_MIN = 0.60
SCORE_CONTAIN_MAX = 0.80
SCORE_FILENAME_MAX = 0.50
HOST_PRIOR_BONUS = 0.05

MIN_FUZZY_TOKENS = 2
MIN_SINGLE_TOKEN_LEN = 4

# Directory kinds. The library is NOT purely subject-keyed: unattributed
# material is filed by category, and the two need opposite learning rules and
# opposite auto-file rules. See dirkinds.py for where the classification lives.
KIND_PERFORMER = "performer"
KIND_CATEGORY = "category"
KIND_UNKNOWN = "unknown"
KINDS = frozenset({KIND_PERFORMER, KIND_CATEGORY})

# Tokens that carry no subject information and would otherwise create
# accidental containment hits. Deliberately tiny and generic (no site- or
# library-specific words live in this repo).
STOPWORDS = frozenset({
    "the", "and", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "video", "videos", "movie", "movies", "clip", "clips", "download",
    "downloads", "watch", "free", "hd", "full", "part", "scene", "new",
    "com", "net", "org", "www", "mp4", "mkv", "webm", "mov", "jpg", "jpeg",
    "png", "gif", "webp", "source", "original", "final",
})

# Resolution/quality noise commonly embedded in opaque filenames.
_FILENAME_NOISE = frozenset({
    "1080p", "720p", "480p", "2160p", "4k", "hevc", "h264", "h265", "x264",
    "x265", "aac", "avc", "60fps", "30fps",
})

# --- identity signals ------------------------------------------------------ #
# Structured alias keys. They are stored VERBATIM (not folded through
# `norm_key`) so that `dl-route alias list` prints exactly what `alias rm`
# accepts, and so a channel id can never be confused with a subject phrase.
KEY_PREFIX_DISCORD = "discord:"
KEY_PREFIX_THREAD = "thread:"
KEY_PREFIXES = (KEY_PREFIX_DISCORD, KEY_PREFIX_THREAD)

# The site an identity signal is scoped to. Discord attachments are served from
# a CDN host that is NOT the site the user was on, and the SPA gives us no page
# context at all, so the scope is pinned to this constant rather than derived.
DISCORD_SITE = "discord.com"
_DISCORD_CDN_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
# Both hosts serve `/attachments/<channel>/<message>/<file>`; ephemeral (slash
# command) uploads use the same shape under a different first segment.
_DISCORD_ATTACHMENT_SEGMENTS = frozenset({"attachments", "ephemeral-attachments"})
# A snowflake is 17-19 digits today. Bounded loosely, but bounded: an unbounded
# "any digits" rule would turn a stray numeric path segment into an alias key.
_SNOWFLAKE = re.compile(r"^[0-9]{5,25}$")

# The path segments that INTRODUCE a thread. A slug is the segment immediately
# after one of these, and nowhere else.
#
# THIS USED TO BE A "SKIP THE CHROME, TAKE THE DEEPEST SEGMENT THAT QUALIFIES"
# RULE, AND THAT RULE WAS WRONG IN THE ONE CASE THAT MATTERS. When a thread's
# own slug is a single word, it failed the >=2-token test and the SECTION name
# one level up won instead:
#
#     /forums/general-discussion/threads/aster.99/  ->  "general discussion"
#     /forums/general-discussion.12/                ->  "general discussion"
#     /members/some-poster.4321/                    ->  "some poster"
#
# Those became `thread:` identity keys, which score 1.00 and auto-file -- so
# one correction taught the router that an entire forum SECTION, or another
# member's profile, meant one subject directory. That is precisely the
# mislearning this whole change exists to remove, relocated from the tag list
# into the URL path and made worse, because an identity key outranks everything
# else.
#
# Anchoring is a POSITIONAL rule, not a superlative over candidates: a
# superlative ("deepest that qualifies", "longest that survives") picks the
# wrong thing exactly when the subject is short, and short subjects are common.
# A segment that no forum route introduced is not a thread, full stop.
_THREAD_ANCHORS = frozenset({
    "threads", "thread", "topic", "topics", "t",
    "showthread.php", "viewtopic.php", "showthread", "viewtopic",
})

# `slug.123456` (xenforo) and `123456-slug` (vbulletin) both carry a numeric id
# beside the slug. Stripping it is what makes the two shapes fold to one key.
_TRAILING_ID = re.compile(r"[.\-_]\d{2,}$")
_LEADING_ID = re.compile(r"^\d{2,}[.\-_]")

# Title separators. A page title is routinely "<subject> | <site>"; the site
# half is chrome and is dropped by comparing against the host's own tokens.
_TITLE_SPLIT = re.compile(r"\s*[|–—·•»«]\s*|\s+[-‐]\s+|\s*::\s*")

# A learned alias key shorter than this is refused: two characters match far
# too much page prose to be a subject. Three is deliberate rather than four:
# an initialised stage name folds to three (`M.I.A.` -> `mia`), and refusing a
# real subject is not free just because the refusal is quiet.
MIN_ALIAS_KEY_LEN = 3
# A phrase seen on this many DISTINCT directories is site chrome (a forum
# section, an uploader's username), not a subject. Data-driven: it is measured
# from the labelled examples, not read off a word list.
CHROME_DIR_SPREAD = 2


def _ascii_host(host: str) -> str:
    """A hostname in the one form BOTH implementations produce.

    JS's `new URL().hostname` punycodes an internationalised host and wraps an
    IPv6 literal in brackets; Python's `urlsplit().hostname` does neither and
    strips the brackets. Both are right for their own spec, which is exactly
    why neither can be the contract -- so the rule is written out here and
    mirrored in route_core.js, the same treatment `is_http_url` already gets in
    safety.py. An un-encodable host is refused rather than guessed at.
    """
    host = (host or "").lower()
    if not host:
        return ""
    if "%" in host:
        # `new URL` percent-DECODES the host before punycoding it; `urlsplit`
        # hands back the raw escapes. Without this the two produce
        # `xn--e1afmkfd.test` and `%d0%bf....test` for the same URL.
        from urllib.parse import unquote
        try:
            decoded = unquote(host, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return ""
        if any(ch in decoded for ch in "/\\?#@[]:"):
            return ""       # an escape that smuggles authority syntax
        host = decoded
    if all(ord(ch) < 128 for ch in host):
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return ""


def host_of(url) -> str:
    """Hostname of an http(s) URL, normalised. `''` for anything else.

    The scheme filter matters: these hosts become the SCOPE of an alias, and
    `file:`, `data:` and `blob:` URLs have no meaningful one. `urlsplit`
    cheerfully parses all three; `new URL` parses them too but reports a
    different hostname, so without an explicit gate the two implementations
    disagreed on 9 of 30 hostile inputs.
    """
    if not isinstance(url, str) or not url:
        return ""
    try:
        split = urlsplit(url)
        if split.scheme.lower() not in ("http", "https"):
            return ""
        return _ascii_host(split.hostname or "")
    except ValueError:
        return ""


def url_path_segments(url) -> tuple:
    """Non-empty path segments of an absolute http(s) URL. `()` otherwise.

    Gated on `host_of` for two reasons. `urlsplit` happily parses
    `"not a url"` as a relative path and hands back `["not a url"]`, which the
    slug extractor then read as a two-word thread subject, while JS's
    `new URL` throws. And `data:`/`file:`/`blob:` URLs have paths but no site
    to scope an alias to. Pinned by fixtures/url_cases.json.
    """
    if not host_of(url):
        return ()
    try:
        path = urlsplit(url).path
    except ValueError:
        return ()
    return tuple(seg for seg in path.split("/") if seg)


def discord_channel_id(url) -> str:
    """The channel id from a Discord attachment URL, else `''`.

    `https://cdn.discordapp.com/attachments/<channel>/<message>/<file>`

    This is the whole point of the Discord path: the id is IN THE URL, so no
    DOM scraping is involved and a Discord UI change cannot break it.
    """
    if host_of(url) not in _DISCORD_CDN_HOSTS:
        return ""
    segments = url_path_segments(url)
    if len(segments) < 3 or segments[0].lower() not in _DISCORD_ATTACHMENT_SEGMENTS:
        return ""
    channel = segments[1]
    return channel if _SNOWFLAKE.match(channel) else ""


def discord_alias_key(channel_id: str) -> str:
    return KEY_PREFIX_DISCORD + str(channel_id)


def _slug_tokens(segment: str) -> tuple:
    """VERBATIM tokens of one path segment, with any adjacent numeric id gone.

    `tokens`, not `content_tokens`. Stopword stripping belongs to fuzzy
    matching, never to an identity: it made distinct threads collide, and
    `upsert_alias` re-points on conflict, so the collision was silent --

        /threads/aster-vale-new-set.222/  ->  thread:aster-vale-set
        /threads/aster-vale-set.223/      ->  thread:aster-vale-set

    ...and the second correction quietly repointed a key the first thread hits
    at 1.00. `new` is a stopword here; so are `part`, `scene`, `full`, `hd`,
    `clip`, `video`, `source`, `original` and `final`, which is a lot of real
    thread titles.
    """
    seg = str(segment or "")
    seg = _LEADING_ID.sub("", seg)
    seg = _TRAILING_ID.sub("", seg)
    return tokens(seg)


def thread_slug(url) -> str:
    """The forum thread subject carried by a URL path, as a phrase.

    ANCHORED: the slug is the segment immediately following a thread route
    (`/threads/`, `/topic/`, `/t/`, `showthread.php`, ...) and nowhere else.

        /forums/some-section/threads/subject-name.12345/page-2 -> "subject name"
        /forums/some-section/                                  -> ""
        /members/some-poster.4321/                             -> ""
        /uploads/dsc-0123.jpg                                  -> ""

    See `_THREAD_ANCHORS` for why this is positional rather than a superlative
    over candidate segments. The last anchored segment wins when a URL somehow
    contains two, which is deterministic without being a "best of" rule.

    Preferred over the tag list (on a forum that is section names and other
    posters' usernames) and over the page title (site branding).
    """
    best = ()
    segments = url_path_segments(url)
    for i in range(1, len(segments)):
        if segments[i - 1].lower() not in _THREAD_ANCHORS:
            continue
        toks = _slug_tokens(segments[i])
        # The thread route already proves this is a thread, so a ONE-word slug
        # is legitimate here in a way it never was under the old rule -- the
        # >=2-token guard existed only to stop an opaque file-host id
        # (`/d/AbCdEf`) being minted as a subject, and no anchor introduces one.
        meaningful = tuple(t for t in toks
                           if t not in STOPWORDS and not t.isdigit())
        if not meaningful or not passes_fuzzy_guard(meaningful):
            continue
        best = toks
    return " ".join(best)


def thread_alias_key(slug: str) -> str:
    """The stored key for a thread slug. Near-verbatim — see `_slug_tokens`."""
    toks = tokens(slug)
    return (KEY_PREFIX_THREAD + "-".join(toks)) if toks else ""


def _host_tokens(site: str) -> frozenset:
    """Tokens of a hostname, minus the parts every hostname has."""
    return frozenset(t for t in tokens(site)
                     if t not in STOPWORDS and len(t) > 1)


def is_site_branding(phrase, site: str) -> bool:
    """True when `phrase` is the SITE's own name rather than a subject.

    Two checks, because a hostname concatenates what a title spaces out:
    `"Some Forum"` on `someforum.test` shares no TOKEN with the host, but its
    normalisation key `someforum` is right there inside `someforumtest`.

    Known and accepted cost: a subject whose name is a substring of the host
    they publish on (`Jane Doe` on `janedoe.test`) reads as branding here. It
    loses one weak title-derived alias; the identity signals, which are the
    ones that actually carry that site, are unaffected.
    """
    if not site:
        return False
    toks = content_tokens(phrase)
    if not toks:
        return False
    host_toks = _host_tokens(site)
    if host_toks and all(t in host_toks for t in toks):
        return True
    key = norm_key(phrase)
    return len(key) >= MIN_ALIAS_KEY_LEN and key in norm_key(site)


def title_subject(title, site: str = "") -> str:
    """The subject half of a page title, with the site's branding dropped.

    `"Subject Name | Some Forum"` on `someforum.test` -> `"Subject Name"`.

    THE FIRST surviving segment, not the longest.

    "Longest wins" was a superlative over candidates, and like the old slug
    rule it picked the wrong thing exactly when the subject was short. The
    branding test can only recognise a site whose display name resembles its
    hostname, so on a forum where those differ it fires for neither segment --
    and then the longer one wins, which is the SITE name whenever the subject
    is a single word:

        title_subject('Aster | Some Forum', 'forum.test')  ->  'Some Forum'

    That is a 1.00 site-scoped alias for the site's own name: every page on
    that forum with a short subject then auto-files into one directory.

    Position is the reliable signal. `"<subject> | <site>"` is the dominant
    convention by a wide margin, and the reverse order is still handled
    whenever the branding test CAN see it, because that segment is dropped
    before position is consulted. When neither is knowable, taking the first
    segment is at worst a weak alias for one page's real title -- the failure
    mode is a useless alias, not a wrong one.
    """
    if not isinstance(title, str) or not title.strip():
        return ""
    for part in _TITLE_SPLIT.split(title):
        part = (part or "").strip()
        if not part or not content_tokens(part):
            continue
        if is_site_branding(part, site):
            continue
        return part
    return ""


@dataclass(frozen=True)
class IdentitySignal:
    """A structured, site-scoped alias key derived from the URL.

    ONE function produces these and BOTH the matcher and the learner consume
    it, which is what stops "what we match on" and "what we learn" drifting.
    """
    key: str
    site: str
    kind: str        # provenance, surfaced by `dl-route alias review`
    evidence: str


def identity_signals(ctx) -> list:
    """Every identity signal derivable from a context's URLs, strongest first.

    Deliberately URL-only. Page DOM content is not an identity: it is what the
    mislearning incident was made of.
    """
    out: list = []
    seen = set()

    def add(key, site, kind, evidence):
        if not key or (key, site) in seen:
            return
        seen.add((key, site))
        out.append(IdentitySignal(key=key, site=site, kind=kind,
                                  evidence=evidence))

    for url in (ctx.url, ctx.final_url, ctx.referrer):
        channel = discord_channel_id(url)
        if channel:
            add(discord_alias_key(channel), DISCORD_SITE, "discord-channel",
                f"channel {channel}")

    # Order matters: the page the download was clicked ON first, then the
    # download's own URL, then a PROVEN cross-host referrer (see the extension's
    # carryReferrer -- never a time window, never "the last thread I saw").
    for url in (ctx.page_url, ctx.url, ctx.final_url, ctx.referrer,
                ctx.referrer_url):
        slug = thread_slug(url)
        key = thread_alias_key(slug)
        if key:
            add(key, host_of(url), "thread-slug", slug)
    return out


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def norm_key(text) -> str:
    """The convention-folding key: alphanumerics only, casefolded, unaccented."""
    if not isinstance(text, str):
        return ""
    return "".join(ch for ch in strip_diacritics(text).casefold() if ch.isalnum())


def tokens(text) -> tuple:
    """Ordered alphanumeric tokens, casefolded and unaccented (stopwords kept —
    callers strip them where appropriate, so `tokens` stays a pure lexer)."""
    if not isinstance(text, str):
        return ()
    out, cur = [], []
    for ch in strip_diacritics(text).casefold():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return tuple(out)


def content_tokens(text) -> tuple:
    """Tokens with stopwords and pure-noise tokens removed."""
    return tuple(t for t in tokens(text)
                 if t not in STOPWORDS and t not in _FILENAME_NOISE)


def title_case(phrase: str) -> str:
    """Title Case for a NEW directory name (decision D7).

    Existing directories are never renamed; only proposals go through here.
    """
    parts = [p for p in " ".join(str(phrase).split()).split(" ") if p]
    out = []
    for part in parts:
        # Capitalise the first alphabetic character, leave the rest as typed so
        # "McBride" / "o'neal" -> "McBride" / "O'neal" rather than being mangled.
        idx = next((i for i, c in enumerate(part) if c.isalpha()), None)
        if idx is None:
            out.append(part)
        else:
            out.append(part[:idx] + part[idx].upper() + part[idx + 1:])
    return " ".join(out)


def passes_fuzzy_guard(toks) -> bool:
    """>=2 tokens, or one token of >=4 characters."""
    toks = tuple(toks)
    if len(toks) >= MIN_FUZZY_TOKENS:
        return True
    return len(toks) == 1 and len(toks[0]) >= MIN_SINGLE_TOKEN_LEN


@dataclass(frozen=True)
class DirEntry:
    """One directory under the library root, pre-normalised."""
    name: str
    key: str
    tokens: tuple

    @staticmethod
    def of(name: str) -> "DirEntry":
        return DirEntry(name=name, key=norm_key(name), tokens=content_tokens(name))


@dataclass
class MatchContext:
    """Everything the extension captured about the download and its page."""
    url: str = ""
    final_url: str = ""
    referrer: str = ""
    filename: str = ""
    mime: str = ""
    size: int = 0
    site: str = ""          # hostname of the page the download came from
    title: str = ""
    tags: tuple = ()        # subject/performer/category tags scraped from the page
    link_text: str = ""
    alt: str = ""
    og: dict = field(default_factory=dict)
    page_url: str = ""      # URL of the page the download was clicked on
    # A PROVEN cross-host referrer: the forum thread that linked to this file
    # host. Only ever populated when the extension could prove the link (an
    # opener-tab chain or a captured click whose href is this page) -- never
    # from a time window or "the last thread seen".
    referrer_url: str = ""
    referrer_title: str = ""

    @staticmethod
    def from_payload(payload: dict) -> "MatchContext":
        payload = payload or {}
        page = payload.get("page") or {}
        og = page.get("og") if isinstance(page.get("og"), dict) else {}
        raw_tags = page.get("tags")
        tags = tuple(str(t) for t in raw_tags
                     if isinstance(t, str) and t.strip())[:64] \
            if isinstance(raw_tags, list) else ()
        page_url = str(page.get("url") or "")
        url = str(payload.get("url") or "")
        final_url = str(payload.get("finalUrl") or "")
        referrer = str(payload.get("referrer") or "")
        site = str(page.get("site") or "").strip().lower()
        if not site:
            site = host_of(page_url or referrer)
        if not site and any(discord_channel_id(u)
                            for u in (url, final_url, referrer)):
            # Discord is a SPA: the captured page context is EMPTY (no title,
            # no tags, no page URL), so there is nothing to derive a site from.
            # The attachment URL still proves which site this is, and pinning it
            # keeps everything learned here SITE-SCOPED instead of falling into
            # the global-alias branch -- which is how a username became a
            # library-wide alias the first evening this ran.
            site = DISCORD_SITE
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return MatchContext(
            url=url,
            final_url=final_url,
            referrer=referrer,
            filename=str(payload.get("filename") or ""),
            mime=str(payload.get("mime") or ""),
            size=size,
            site=site,
            title=str(page.get("title") or ""),
            tags=tags,
            link_text=str(page.get("linkText") or ""),
            alt=str(page.get("alt") or ""),
            og={str(k): str(v) for k, v in og.items()
                if isinstance(v, (str, int, float))},
            page_url=page_url,
            referrer_url=str(page.get("referrerUrl") or ""),
            referrer_title=str(page.get("referrerTitle") or ""),
        )

    def thread_slugs(self) -> list:
        """Thread-subject phrases from the URLs, deepest-first per URL."""
        out, seen = [], set()
        for url in (self.page_url, self.url, self.final_url, self.referrer,
                    self.referrer_url):
            slug = thread_slug(url)
            key = norm_key(slug)
            if slug and key not in seen:
                seen.add(key)
                out.append(slug)
        return out

    def subject_phrases(self) -> list:
        """Ordered candidate subject strings, strongest signal first.

        ORDER CHANGED after the first evening of real traffic. It used to be
        tags-first; on the one forum download where context WAS captured, the
        tag list was the forum's own section names and other posters'
        usernames, while the subject sat in the URL's thread slug and in the
        page title. The URL slug is the cleanest carrier of a subject there is
        -- it cannot be polluted by other users' content -- so it leads, the
        de-branded title follows, and the tag list is demoted behind both.
        """
        out: list = []
        seen = set()

        def add(value):
            if not isinstance(value, str):
                return
            value = value.strip()
            if not value or len(value) > 300:
                return
            key = norm_key(value)
            if not key or key in seen:
                return
            seen.add(key)
            out.append(value)

        for slug in self.thread_slugs():
            add(slug)
        add(title_subject(self.referrer_title, host_of(self.referrer_url)))
        add(title_subject(self.title, self.site))
        for tag in self.tags:
            add(tag)
        for og_key in ("video:actor", "video:tag", "og:video:actor",
                       "article:author", "author", "title", "og:title",
                       "site_name"):
            add(self.og.get(og_key))
        add(self.link_text)
        add(self.alt)
        add(self.title)
        add(self.referrer_title)
        return out


# Back-compat alias for the private name this module used to expose.
_host_of = host_of


def filename_stem(filename: str) -> str:
    """The filename minus its extension (used as a weak matching signal, and as
    the PRIMARY signal by the backfill, where no page context exists)."""
    base = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    return stem if dot and len(ext) <= 5 else base


# Back-compat alias for the private name used inside this module.
_filename_stem = filename_stem


def _contains_sequence(haystack: tuple, needle: tuple) -> bool:
    """True iff `needle` occurs as a CONTIGUOUS subsequence of `haystack`."""
    n, h = len(needle), len(haystack)
    if n == 0 or n > h:
        return False
    return any(haystack[i:i + n] == needle for i in range(h - n + 1))


@dataclass
class Candidate:
    dir: str
    base: float          # score BEFORE the host-prior bonus (threshold reads this)
    score: float         # ranking score (base + bonus)
    reason: str

    def as_dict(self) -> dict:
        return {"dir": self.dir, "score": round(self.score, 4),
                "base": round(self.base, 4), "reason": self.reason}


@dataclass
class MatchResult:
    dir: str
    confidence: float
    reason: str
    candidates: list
    auto: bool
    suggest_new: str | None = None
    dup: dict | None = None

    def as_dict(self, ttl_ms: int = 0) -> dict:
        return {
            "dir": self.dir,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "auto": self.auto,
            "candidates": [c.as_dict() for c in self.candidates],
            "suggestNew": self.suggest_new,
            "dup": self.dup,
            "ttlMs": ttl_ms,
        }


class Matcher:
    """Scores a `MatchContext` against the directory index + alias table.

    `aliases` maps `(key, site)` -> directory name, with `site=""` for a
    global alias. Injected, so the scorer stays pure and unit-testable.

    `dir_kinds` maps a directory name to `performer` / `category`. Anything
    absent is UNKNOWN, and only a `performer` directory may ever auto-file.
    """

    def __init__(self, dirs, aliases=None, *, threshold: float = 0.75,
                 tie_margin: float = 0.05, other_dir: str = "other",
                 dir_kinds=None):
        self.dirs = [d if isinstance(d, DirEntry) else DirEntry.of(str(d))
                     for d in (dirs or [])]
        self.by_name = {d.name: d for d in self.dirs}
        self.by_key: dict = {}
        for d in self.dirs:
            # First writer wins so the result is deterministic when two
            # directories fold to the same key ("Jane Doe" vs "jane-doe").
            self.by_key.setdefault(d.key, d)
        self.aliases = dict(aliases or {})
        self.threshold = float(threshold)
        self.tie_margin = float(tie_margin)
        self.other_dir = other_dir
        # Keyed by norm_key so a dirs.toml written in a different naming
        # convention than the directory on disk still classifies it -- the same
        # convention-folding that stops directories ever needing a rename.
        self.dir_kinds = {norm_key(k): str(v)
                          for k, v in dict(dir_kinds or {}).items()
                          if norm_key(k) and v in KINDS}

    def kind_of(self, dir_name: str) -> str:
        return self.dir_kinds.get(norm_key(dir_name), KIND_UNKNOWN)

    # --- individual rules -------------------------------------------------- #
    def _identity_hits(self, ctx) -> list:
        """Structured URL identities -> full-confidence alias hits.

        This is the Discord path. The first download from a channel has no
        signal at all and opens the picker; confirming it writes
        `discord:<channel id>` scoped to Discord, and every later download from
        that channel lands here at 1.00 with nothing scraped from any page.
        """
        out = []
        for sig in identity_signals(ctx):
            target = self.aliases.get((sig.key, sig.site))
            if target and target in self.by_name:
                out.append(Candidate(target, SCORE_ALIAS_SITE, SCORE_ALIAS_SITE,
                                     f"alias({sig.kind}) '{sig.key}'"))
        return out

    def _alias_hits(self, phrases, site: str) -> list:
        out = []
        for phrase in phrases:
            key = norm_key(phrase)
            if not key:
                continue
            if site:
                target = self.aliases.get((key, site))
                if target and target in self.by_name:
                    out.append(Candidate(target, SCORE_ALIAS_SITE,
                                         SCORE_ALIAS_SITE,
                                         f"alias(site:{site}) '{phrase}'"))
            target = self.aliases.get((key, ""))
            if target and target in self.by_name:
                out.append(Candidate(target, SCORE_ALIAS_GLOBAL,
                                     SCORE_ALIAS_GLOBAL,
                                     f"alias(global) '{phrase}'"))
        return out

    def _tag_exact(self, phrases) -> list:
        out = []
        for phrase in phrases:
            key = norm_key(phrase)
            entry = self.by_key.get(key) if key else None
            if entry is not None:
                out.append(Candidate(entry.name, SCORE_TAG_EXACT,
                                     SCORE_TAG_EXACT,
                                     f"tag=='{entry.name}' via '{phrase}'"))
        return out

    def _containment(self, phrases) -> list:
        out = []
        for phrase in phrases:
            ptoks = content_tokens(phrase)
            if not ptoks:
                continue
            for entry in self.dirs:
                dtoks = entry.tokens
                if not dtoks:
                    continue
                if _contains_sequence(ptoks, dtoks):
                    inner, outer = dtoks, ptoks
                elif _contains_sequence(dtoks, ptoks):
                    inner, outer = ptoks, dtoks
                else:
                    continue
                if not passes_fuzzy_guard(inner):
                    continue
                coverage = len(inner) / len(outer)
                score = SCORE_CONTAIN_MIN + (
                    SCORE_CONTAIN_MAX - SCORE_CONTAIN_MIN) * coverage
                out.append(Candidate(
                    entry.name, score, score,
                    f"contains '{' '.join(inner)}' ({len(inner)}/{len(outer)} tokens)"))
        return out

    def _filename(self, filename: str) -> list:
        ftoks = content_tokens(_filename_stem(filename))
        if not ftoks:
            return []
        fset = set(ftoks)
        out = []
        for entry in self.dirs:
            dtoks = entry.tokens
            if not dtoks:
                continue
            matched = tuple(t for t in dtoks if t in fset)
            if not matched or not passes_fuzzy_guard(matched):
                continue
            score = SCORE_FILENAME_MAX * (len(matched) / len(dtoks))
            out.append(Candidate(entry.name, score, score,
                                 f"filename tokens {list(matched)}"))
        return out

    # --- assembly ---------------------------------------------------------- #
    def match(self, ctx: MatchContext, *, host_prior: str | None = None,
              dup: dict | None = None) -> MatchResult:
        phrases = ctx.subject_phrases()
        hits: list = []
        hits += self._identity_hits(ctx)
        hits += self._alias_hits(phrases, ctx.site)
        hits += self._tag_exact(phrases)
        hits += self._containment(phrases)
        hits += self._filename(ctx.filename)

        best: dict = {}
        for cand in hits:
            cur = best.get(cand.dir)
            if cur is None or cand.base > cur.base:
                best[cand.dir] = cand

        # Host prior: RANKING ONLY, and only among candidates that are ALREADY
        # tied on `base`.
        #
        # It used to add its bonus to `score` and then rank on `score`, which
        # made it decisive twice over: with two 0.85 tag-exact candidates it
        # promoted the runner-up to 0.90, which both re-ordered the pair AND
        # opened a 0.05 gap that defeated the tie-break -- so a download
        # auto-filed into a different directory than the evidence chose, with
        # no picker and nothing in the reason string to say why. matcher.py's
        # own docstring and README both promised the opposite.
        #
        # Now `base` alone decides the order and the threshold, and the prior is
        # only a tiebreak between equal `base` values -- where the tie-margin
        # check below then fires anyway (a gap of 0 is always inside the
        # margin), so the picker still opens. The bonus survives on `score`
        # purely so the candidate list and the reason string can show that the
        # prior was consulted.
        if host_prior and host_prior in best:
            cand = best[host_prior]
            cand.score = min(1.0, cand.base + HOST_PRIOR_BONUS)
            cand.reason += " +host-prior"

        def rank(c: Candidate):
            return (-c.base, 0 if c.dir == host_prior else 1, c.dir)

        candidates = sorted(best.values(), key=rank)

        if not candidates:
            return MatchResult(dir=self.other_dir, confidence=0.0,
                               reason="no signal in page context or filename",
                               candidates=[], auto=False,
                               suggest_new=self.propose_new(phrases), dup=dup)

        top = candidates[0]
        auto = top.base >= self.threshold
        reason = top.reason
        if auto and len(candidates) > 1:
            runner = candidates[1]
            # `base`, not `score`: comparing bonused scores let the prior
            # manufacture the margin that suppressed this very check.
            if (top.base - runner.base) < self.tie_margin:
                auto = False
                reason = (f"tie: '{top.dir}' {top.base:.2f} vs "
                          f"'{runner.dir}' {runner.base:.2f} — {top.reason}")
        # DIRECTORY KIND. Applied LAST and unconditionally, so no score, alias
        # or prior can route around it.
        #
        #   * category  -- a tag legitimately identifies the directory, but a
        #     tag is a weak claim about any ONE file, so a category always gets
        #     confirmed. (Known consequence: a confirmed Discord channel alias
        #     pointing at a category directory keeps asking forever. That is
        #     the rule as specified; see the PR body for the exception.)
        #   * unknown   -- absence of a classification is not permission. An
        #     unclassified directory is one `dl-route dirs classify` away from
        #     being usable, and until then it asks.
        if auto:
            kind = self.kind_of(top.dir)
            if kind == KIND_CATEGORY:
                auto = False
                reason = f"category directory — always confirm — {reason}"
            elif kind != KIND_PERFORMER:
                auto = False
                reason = (f"unclassified directory '{top.dir}' — set its kind "
                          f"(dl-route dirs classify) — {reason}")
        # `confidence` is the pre-bonus score: it is what the threshold was
        # tested against, so reporting the bonused number would be a lie.
        return MatchResult(dir=top.dir, confidence=top.base, reason=reason,
                           candidates=candidates[:8], auto=auto,
                           suggest_new=self.propose_new(phrases), dup=dup)

    def propose_new(self, phrases) -> str | None:
        """A Title Case directory proposal for the picker's top entry (D6/D7).

        Only for a subject phrase that matches NO existing directory and looks
        like a name. Never creates anything — the picker still needs a keypress.
        """
        from safety import is_safe_dir_name
        for phrase in phrases:
            key = norm_key(phrase)
            if not key or key in self.by_key:
                continue
            toks = content_tokens(phrase)
            if not passes_fuzzy_guard(toks) or len(toks) > 5:
                continue
            proposal = title_case(" ".join(phrase.split()))
            if is_safe_dir_name(proposal) and proposal not in self.by_name:
                return proposal
        return None


def is_structured_key(key) -> bool:
    """True for `discord:`/`thread:` keys — an identity, not a word."""
    return isinstance(key, str) and key.startswith(KEY_PREFIXES)


def alias_key(phrase) -> str:
    """The stored key for a phrase, as typed at the CLI or learned.

    Structured keys keep their prefix and are canonicalised inside it, so
    `dl-route alias rm 'discord:123'` removes exactly the row
    `dl-route alias list` printed. Everything else folds through `norm_key`.
    """
    raw = str(phrase or "").strip()
    lowered = raw.lower()
    if lowered.startswith(KEY_PREFIX_DISCORD):
        rest = raw[len(KEY_PREFIX_DISCORD):].strip()
        return discord_alias_key(rest) if _SNOWFLAKE.match(rest) else ""
    if lowered.startswith(KEY_PREFIX_THREAD):
        return thread_alias_key(raw[len(KEY_PREFIX_THREAD):])
    return norm_key(raw)


def suspicious_alias_key(phrase, *, key: str = "", dir_names=(), site: str = "",
                         spread: int = 0):
    """Why `phrase` must not become an alias, or None if it is fine.

    This is the generalisation of the four rows that had to be deleted by hand
    after the first evening: a forum section name, two other posters' usernames
    and one of them at GLOBAL scope. Catching those four specific strings would
    have been a word list; these rules catch the FAILURE CLASS, and every one of
    them is measured from data the router already has rather than from a
    vocabulary that would need maintaining (and could never be committed to a
    public repo anyway).

    STRUCTURED KEYS ARE SCREENED TOO. They used to return None immediately, on
    the reasoning that a channel id is an identity rather than a word. The
    reasoning held; the exemption did not, because a BADLY DERIVED identity is
    still an identity as far as the store is concerned, and it lands at 1.00
    with auto-file rather than at 0.85 without. A section name mis-read as a
    thread slug was the worst row the router could possibly write, and it was
    the one row nothing checked.

    What structured keys are exempt from is only the two rules that describe a
    WORD rather than a source: minimum length (a channel id is digits; a
    one-word thread slug is legitimately short) and the handle shape (a channel
    id is nothing but digits). Chrome spread, site branding and shared library
    vocabulary all still apply, and all three are exactly what catches a
    section name.
    """
    key = key or alias_key(phrase)
    if not key:
        return "empty key"
    structured = is_structured_key(key)
    if not structured and len(key) < MIN_ALIAS_KEY_LEN:
        return (f"key {key!r} is shorter than {MIN_ALIAS_KEY_LEN} characters — "
                "it would match unrelated page prose")
    if spread >= CHROME_DIR_SPREAD:
        return (f"seen on {spread} different directories — that is site chrome "
                "(a section name, an uploader's username), not a subject")
    ptoks = content_tokens(phrase)
    if is_site_branding(phrase, site):
        return f"it is part of the site name ({site})"
    if ptoks:
        shared = 0
        for tok in ptoks:
            hits = sum(1 for name in dir_names if tok in content_tokens(name))
            if hits >= CHROME_DIR_SPREAD:
                shared += 1
        if shared == len(ptoks):
            return ("every word of it already appears in "
                    f"{CHROME_DIR_SPREAD}+ library directories — it reads as a "
                    "category word, not a subject")
    if not structured and _looks_like_a_handle(ptoks):
        return "it reads as a handle (a word with a number stuck on the end)"
    return None


def _looks_like_a_handle(ptoks) -> bool:
    """`poster1988`, `uploader42` — a word with a number stuck on the end.

    NARROWED from "a single token containing any digit", which refused
    legitimate single-word stage names that merely contain a numeral. The
    remaining false positive is a name that genuinely ends in digits; it is
    reported rather than silently dropped, and `--force` still writes it.
    """
    if len(ptoks) != 1:
        return False
    tok = ptoks[0]
    return (len(tok) >= 6 and tok[-1].isdigit() and tok[-2].isdigit()
            and not tok[0].isdigit())


def find_duplicate(index, target_dir: str, filename: str, size: int = 0):
    """Dedupe check against the target dir and the whole-tree file index.

    `index` exposes `by_name_key(key)` -> [(relpath, size), ...]. Returns a
    `{where, relpath, size, kind}` dict or None. NEVER blocks or overwrites —
    the caller only warns; `conflictAction: "uniquify"` handles real collisions.
    """
    if index is None:
        return None
    key = norm_key(_filename_stem(filename))
    if not key:
        return None
    matches = index.by_name_key(key) or []
    if not matches:
        return None
    in_target = [m for m in matches
                 if str(m[0]).split("/", 1)[0] == target_dir]
    chosen = in_target[0] if in_target else matches[0]
    relpath, existing_size = chosen[0], (chosen[1] if len(chosen) > 1 else 0)
    kind = "name+size" if size and existing_size == size else "name"
    return {"where": "target-dir" if in_target else "library",
            "relpath": relpath, "size": existing_size, "kind": kind}
