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

    exact alias hit, site-scoped                            1.00
    exact alias hit, global                                 0.90
    normalised page tag/subject == dir key                  0.85
    token-sequence containment, scaled by coverage     0.60-0.80
    filename token match                                   <=0.50
    host prior (last dir used on this host)          +0.05 ranking only

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
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

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

    @staticmethod
    def from_payload(payload: dict) -> "MatchContext":
        payload = payload or {}
        page = payload.get("page") or {}
        og = page.get("og") if isinstance(page.get("og"), dict) else {}
        raw_tags = page.get("tags")
        tags = tuple(str(t) for t in raw_tags
                     if isinstance(t, str) and t.strip())[:64] \
            if isinstance(raw_tags, list) else ()
        site = str(page.get("site") or "").strip().lower()
        if not site:
            site = _host_of(str(page.get("url") or payload.get("referrer") or ""))
        try:
            size = int(payload.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return MatchContext(
            url=str(payload.get("url") or ""),
            final_url=str(payload.get("finalUrl") or ""),
            referrer=str(payload.get("referrer") or ""),
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
        )

    def subject_phrases(self) -> list:
        """Ordered candidate subject strings, strongest signal first.

        Tags are the designed signal; og/link/alt/title are the fallbacks that
        make the matcher work on sites with no tag markup at all.
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

        for tag in self.tags:
            add(tag)
        for og_key in ("video:actor", "video:tag", "og:video:actor",
                       "article:author", "author", "title", "og:title",
                       "site_name"):
            add(self.og.get(og_key))
        add(self.link_text)
        add(self.alt)
        add(self.title)
        return out


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


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

    `aliases` maps `(norm_key, site)` -> directory name, with `site=""` for a
    global alias. Injected, so the scorer stays pure and unit-testable.
    """

    def __init__(self, dirs, aliases=None, *, threshold: float = 0.75,
                 tie_margin: float = 0.05, other_dir: str = "other"):
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

    # --- individual rules -------------------------------------------------- #
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
