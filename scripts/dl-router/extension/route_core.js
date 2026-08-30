// route_core.js -- the pure half of the download router. No chrome.* here, so
// `node --test` exercises every branch of the parts that must not fail.
//
// Three things live here:
//
//   1. correlateCapture()   the three-tier download <-> page-context ladder.
//      A DownloadItem carries no tabId, so the context a content script captured
//      on click has to be matched back to the download by URL, then referrer,
//      then recency-on-the-active-tab.
//
//   2. localDecide()        a reduced, SYNCHRONOUS copy of the sidecar's matcher
//      that runs off the cached /dirs snapshot. It is the fallback when the
//      sidecar is slow or down -- deliberately a subset (alias + exact key +
//      token containment), because a wrong-but-instant answer beats a hung
//      download, and the sidecar remains authoritative when it answers.
//
//   3. handleDetermining()  the suggest() ladder. The hard requirement is that
//      suggest() is called EXACTLY ONCE on every path and never hangs: Chrome
//      silently falls back to the default filename if the listener is slow, so
//      a timer races the sidecar and whichever finishes first wins. `finish()`
//      is idempotent, which is what makes "exactly once" true by construction.

import { baseName, sanitizeDirName, sanitizeFileName } from "./sanitize.js";

export const DEFAULT_MATCH_TIMEOUT_MS = 400;
export const DEFAULT_CAPTURE_WINDOW_MS = 15000;

// Mirrors matcher.py's constants -- kept in sync by tests/test_matcher.py's
// contract assertions and route_core.test.mjs.
export const SCORE_ALIAS_SITE = 1.0;
export const SCORE_ALIAS_GLOBAL = 0.9;
export const SCORE_TAG_EXACT = 0.85;
export const SCORE_CONTAIN_MIN = 0.6;
export const SCORE_CONTAIN_MAX = 0.8;
export const MIN_SINGLE_TOKEN_LEN = 4;

export const STOPWORDS = new Set([
  "the", "and", "a", "an", "of", "in", "on", "at", "to", "for", "with",
  "video", "videos", "movie", "movies", "clip", "clips", "download",
  "downloads", "watch", "free", "hd", "full", "part", "scene", "new",
  "com", "net", "org", "www", "mp4", "mkv", "webm", "mov", "jpg", "jpeg",
  "png", "gif", "webp", "source", "original", "final",
]);

/** NFKD -> strip combining marks -> lowercase -> alphanumerics only. */
export function normKey(text) {
  if (typeof text !== "string") return "";
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "");
}

/** Ordered alphanumeric tokens, VERBATIM (stopwords kept). */
export function allTokens(text) {
  if (typeof text !== "string") return [];
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

/** Ordered alphanumeric tokens, minus stopwords. */
export function contentTokens(text) {
  return allTokens(text).filter((t) => !STOPWORDS.has(t));
}

export function passesFuzzyGuard(tokens) {
  if (tokens.length >= 2) return true;
  return tokens.length === 1 && tokens[0].length >= MIN_SINGLE_TOKEN_LEN;
}

// --- identity signals ------------------------------------------------------ //
// Mirrors matcher.py's half of the same name. The shared fixture table
// tests/fixtures/url_cases.json is asserted against BOTH, so these cannot
// drift the way two hand-copied lists did.
//
// WHY THIS EXISTS. On the first evening of real traffic six of nine downloads
// came from a Discord CDN, where the page is a SPA and the captured context was
// completely empty -- no tags, no title, not even a page URL. There is nothing
// to scrape. But the attachment URL carries the channel id, so the routing key
// is structural and survives any Discord UI change.
export const DISCORD_SITE = "discord.com";
export const KEY_PREFIX_DISCORD = "discord:";
export const KEY_PREFIX_THREAD = "thread:";

const DISCORD_CDN_HOSTS = new Set(["cdn.discordapp.com", "media.discordapp.net"]);
const DISCORD_ATTACHMENT_SEGMENTS = new Set([
  "attachments", "ephemeral-attachments",
]);
const SNOWFLAKE = /^[0-9]{5,25}$/;

// The path segments that INTRODUCE a thread. A slug is the segment
// immediately after one of these, and nowhere else. See matcher.py's
// _THREAD_ANCHORS for the full reasoning: the previous "skip the chrome, take
// the deepest segment that qualifies" rule handed a forum SECTION name a 1.00
// identity alias whenever the thread's own slug was a single word.
const THREAD_ANCHORS = new Set([
  "threads", "thread", "showthread.php", "viewtopic.php",
  "showthread", "viewtopic",
]);

// `t`, `topic` and `topics` are DELIBERATELY ABSENT -- see matcher.py. Gating
// them on an adjacent numeric id does not work: a PAGINATED index
// (`/topics/general-discussion/2`) is structurally identical to a Discourse
// thread, so no adjacency rule can separate them. No slug is fine; a wrong
// slug is not.
const TRAILING_ID = /[.\-_]\d{2,}$/;
const LEADING_ID = /^\d{2,}[.\-_]/;
// Title separators. Escaped rather than literal: every source file here is
// plain ASCII on purpose (see tests/source_hygiene.test.mjs).
const TITLE_SPLIT
  = /\s*[|\u2013\u2014\u00b7\u2022\u00bb\u00ab]\s*|\s+[-\u2010]\s+|\s*::\s*/;

/**
 * Hostname of an http(s) URL, normalised. "" for anything else.
 *
 * The scheme filter and the bracket strip are the contract, not the parser's
 * defaults: these hosts become the SCOPE of an alias, `file:`/`data:`/`blob:`
 * have no meaningful one, and `new URL` brackets an IPv6 literal where
 * Python's `urlsplit` does not. Mirrors matcher.host_of; pinned by
 * fixtures/url_cases.json.
 */
export function hostOf(url) {
  if (typeof url !== "string" || !url) return "";
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
  const host = parsed.hostname.toLowerCase();
  return host.startsWith("[") && host.endsWith("]")
    ? host.slice(1, -1) : host;
}

/**
 * THE STABLE IDENTITY OF AN EMBEDDED PLAYER. Scheme + host + path, nothing else.
 *
 * This is the single most consequential decision in the player-button feature,
 * so it is written where both callers can read it.
 *
 * The media URL an embed host hands its `<video>` is SIGNED AND ROTATING: it
 * carries `?exp=<unix>&token=<...>` and is re-signed roughly hourly, in place,
 * on the same element. `source_url_key` (store.py) deliberately keeps the query
 * string, because on a file host the query IS the asset identity -- so keying
 * the ledger on the media URL would mint a NEW row every time the signature
 * rotated. The "already have this" badge would then never light, and a badge
 * that never lights is worse than no badge: it reads as "you do not have this".
 *
 * The embed PAGE url (`https://<embed host>/embed/<id>`) is the stable name for
 * the same asset: it is what the forum links to, it is what the frame's own
 * `location.href` is, and its path carries the embed id. The query is dropped
 * because playback parameters (`?autoplay=1`, `?t=30`) do not change WHICH
 * asset this is, and keeping them would split one asset across several rows --
 * the same failure as the signature, only slower.
 *
 * Both halves of the feature go through here so they cannot drift: the WRITE
 * (`sourceKey` on the /match payload, which the sidecar records instead of the
 * signed URL) and the READ (`GET /have?url=`). The normalisation lives in the
 * service worker rather than the content script for the same reason -- one
 * implementation, called twice.
 *
 * Returns "" for anything that is not an http(s) URL, which the callers treat
 * as "no stable key", never as a key of its own.
 */
export function playerSourceKey(url) {
  if (!hostOf(url)) return "";
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "";
  }
  // `host`, not `hostname`: a non-default port is part of the identity, and
  // URL already omits the default one -- which is exactly what
  // store.source_url_key does on the Python side, so the two agree on the key
  // for the same input string.
  return `${parsed.protocol}//${parsed.host.toLowerCase()}${parsed.pathname || "/"}`;
}

function pathSegments(url) {
  if (!hostOf(url)) return [];
  let raw;
  try {
    raw = new URL(url).pathname.split("/").filter(Boolean);
  } catch {
    return [];
  }
  // PERCENT-DECODED: `pathname` is the encoded form here while Python's
  // `urlsplit().path` is the raw one, so a non-latin slug produced `%D0%BF...`
  // tokens on this side and real words on the other. See matcher.py.
  return raw.map((seg) => {
    try {
      return decodeURIComponent(seg);
    } catch {
      return seg;
    }
  });
}

/** The channel id from a Discord attachment URL, else "". */
export function discordChannelId(url) {
  if (!DISCORD_CDN_HOSTS.has(hostOf(url))) return "";
  const segments = pathSegments(url);
  if (segments.length < 3) return "";
  if (!DISCORD_ATTACHMENT_SEGMENTS.has(segments[0].toLowerCase())) return "";
  return SNOWFLAKE.test(segments[1]) ? segments[1] : "";
}

export function discordAliasKey(channelId) {
  return KEY_PREFIX_DISCORD + String(channelId);
}

// Discord serves one attachment from two hosts: the resizing proxy goes in the
// <img src>, downscaled to whatever the client asked for and usually
// re-encoded to webp, while the original sits on the wrapping <a href>. So
// `info.srcUrl` on an image names A THUMBNAIL, not the file that was posted.
// (A <video> is unaffected -- its src is already the origin.)
const DISCORD_PREVIEW_HOST = "media.discordapp.net";
const DISCORD_ORIGIN_HOST = "cdn.discordapp.com";

/**
 * The stable ledger identity of a Discord attachment, else "".
 *
 * Discord signs every CDN URL (`ex`/`is`/`hm`) and re-signs it on each page
 * load, so the query names THE REQUEST rather than the asset -- precisely the
 * case `sourceKey` was introduced for. The path
 * `/attachments/<channel>/<message>/<name>` is globally unique and never
 * rotates, so dropping the query is what makes "have I got this already"
 * answerable at all.
 *
 * Without it every re-download of one attachment writes a NEW ledger row and a
 * lookup can only ever miss -- the failure `haveUrl` already names in its own
 * words: "a badge that never lights actively asserts you do not have this".
 * `store.source_url_key` keeps the query on purpose, and is right to: on the
 * sites it was written for the query IS the asset identity. Discord is the
 * exception, so the exception is expressed here, once, rather than by
 * weakening that rule for everyone.
 *
 * THE HOST IS FOLDED, and it is not decoration. `playerSourceKey` keys on
 * `scheme://host + path`, so the proxy copy and the original -- the SAME
 * attachment path served from two hosts -- would key differently and file as
 * two assets. Save an image once from Chrome's own "Save image as..." and once
 * from this extension's menu and the ledger holds two rows that never
 * accumulate a hit: exactly the miss this key exists to remove. The docstring
 * above says the PATH is the identity; folding the host is what makes the
 * implementation as wide as that sentence.
 */
export function discordSourceKey(url) {
  if (!discordChannelId(url)) return "";
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "";
  }
  return `https://${DISCORD_ORIGIN_HOST}${parsed.pathname || "/"}`;
}

/**
 * Given the two URLs the browser offers for one right-clicked element, the one
 * a "save this" action should actually fetch.
 *
 * NARROW ON PURPOSE. It swaps only when both URLs are Discord attachment URLs
 * with the SAME path -- i.e. provably the same asset at two resolutions.
 * A link that merely happens to wrap an image is not evidence of anything, and
 * preferring `linkUrl` in general would turn every linked thumbnail on every
 * site into a download of wherever the link pointed.
 *
 * KNOWN UNHANDLED VARIANT, stated rather than guessed at: if Discord ever
 * puts a FULL-SIZE PROXY url on the anchor (`media.discordapp.net?width=4096`)
 * instead of the origin, this returns `srcUrl` and the downscaled copy is still
 * what gets saved. Widening to "prefer the anchor whenever the paths match"
 * would cover it, but nothing in the live corpus shows that shape, and a
 * URL cannot be read for pixel size -- so the narrow rule stands until a real
 * instance turns up.
 *
 * NOT OBSERVABLE: nothing records whether this ever fires. The extension has
 * no logging facility and inventing one is out of scope here, so the next
 * reader cannot answer "is this a no-op in production?" from data. Say so
 * rather than implying it has been seen to work.
 */
export function preferOriginalUrl(srcUrl, linkUrl) {
  if (!srcUrl) return linkUrl || "";
  if (!linkUrl) return srcUrl;
  if (hostOf(srcUrl) !== DISCORD_PREVIEW_HOST) return srcUrl;
  if (hostOf(linkUrl) !== DISCORD_ORIGIN_HOST) return srcUrl;
  // Both must be attachments, not just Discord-hosted: an avatar or an emoji
  // shares the hosts and must never be swapped for something else.
  if (!discordChannelId(srcUrl) || !discordChannelId(linkUrl)) return srcUrl;
  let src;
  let link;
  try {
    src = new URL(srcUrl);
    link = new URL(linkUrl);
  } catch {
    return srcUrl;
  }
  return src.pathname === link.pathname ? linkUrl : srcUrl;
}

/**
 * The stored key for a thread slug. NEAR-VERBATIM: `allTokens`, not
 * `contentTokens`. Stopword stripping belongs to fuzzy matching, never to an
 * identity -- it collided distinct threads (`aster-vale-new-set` and
 * `aster-vale-set` both folded to `thread:aster-vale-set`) and upsert_alias
 * re-points on conflict, so the collision was silent.
 */
export function threadAliasKey(slug) {
  const toks = allTokens(slug);
  return toks.length ? KEY_PREFIX_THREAD + toks.join("-") : "";
}

/**
 * The forum thread subject carried by a URL path, as a phrase. ANCHORED: the
 * slug is the segment immediately following a thread route, and nowhere else.
 *
 *   /forums/some-section/threads/subject-name.12345/page-2 -> "subject name"
 *   /forums/some-section/                                  -> ""
 *   /members/some-poster.4321/                             -> ""
 *   /uploads/dsc-0123.jpg                                  -> ""
 */
export function threadSlug(url) {
  let best = [];
  const segments = pathSegments(url);
  for (let i = 1; i < segments.length; i += 1) {
    if (!THREAD_ANCHORS.has(segments[i - 1].toLowerCase())) continue;
    const toks = allTokens(
      segments[i].replace(LEADING_ID, "").replace(TRAILING_ID, ""));
    // The thread route already proves this is a thread, so a ONE-word slug is
    // legitimate here; the old >=2-token guard existed only to stop an opaque
    // file-host id being minted as a subject, and no anchor introduces one.
    // `\p{Nd}`, not `\d`: a bare id is not a subject in any script, and
    // Python's `isdigit()` covers every Unicode decimal. See matcher.py.
    const meaningful = toks.filter(
      (t) => !STOPWORDS.has(t) && !/^\p{Nd}+$/u.test(t));
    if (!meaningful.length || !passesFuzzyGuard(meaningful)) continue;
    best = toks;
  }
  return best.join(" ");
}

function hostTokens(site) {
  return new Set(contentTokens(site).filter((t) => t.length > 1));
}

/** True when `phrase` is the SITE's own name rather than a subject. */
export function isSiteBranding(phrase, site) {
  if (!site) return false;
  const toks = contentTokens(phrase);
  if (!toks.length) return false;
  const hostToks = hostTokens(site);
  if (hostToks.size && toks.every((t) => hostToks.has(t))) return true;
  const key = normKey(phrase);
  return key.length >= MIN_SINGLE_TOKEN_LEN && normKey(site).includes(key);
}

/**
 * The subject segment of a page title, CORROBORATED against the URL slug.
 *
 * Neither position nor size can do this job and both were tried: "longest"
 * returned the SITE name for a one-word subject, and "first" then broke every
 * template that does not put the subject first (`'Some Forum - View topic -
 * <subject>'`, `'Section | <subject> | Some Forum'`). phpBB puts the subject
 * last, XenForo puts it first, and both are common.
 *
 * So it asks the anchored slug, which is independent and positionally proven,
 * and takes the segment sharing the most tokens with it. WITH NO SLUG THERE IS
 * NO ANSWER -- "" rather than a guess. See matcher.title_subject.
 */
export function titleSubject(title, site, slug) {
  if (typeof title !== "string" || !title.trim()) return "";
  const slugTokens = new Set(contentTokens(slug));
  if (!slugTokens.size) return "";
  let best = ""; let bestOverlap = 0;
  for (const raw of title.split(TITLE_SPLIT)) {
    const part = (raw || "").trim();
    const toks = contentTokens(part);
    if (!toks.length || isSiteBranding(part, site)) continue;
    const unique = new Set(toks);
    const overlap = [...unique].filter((t) => slugTokens.has(t)).length;
    // ONE shared token is a coincidence, not corroboration, and the whole
    // segment is emitted verbatim -- see matcher.title_subject.
    if (overlap < 1 || (overlap < 2 && unique.size > 1)) continue;
    if (overlap * 2 < unique.size) continue;
    // Strictly greater, so the FIRST segment wins a tie.
    if (overlap > bestOverlap) { best = part; bestOverlap = overlap; }
  }
  return bestOverlap ? best : "";
}

/** Thread-subject phrases from a context's URLs, strongest first. */
export function threadSlugs(ctx) {
  const out = [];
  const seen = new Set();
  for (const url of [ctx?.pageUrl, ctx?.url, ctx?.finalUrl, ctx?.referrer,
    ctx?.referrerUrl]) {
    const slug = threadSlug(url);
    const key = normKey(slug);
    if (slug && !seen.has(key)) { seen.add(key); out.push(slug); }
  }
  return out;
}

/**
 * Every identity signal derivable from a context's URLs, strongest first.
 * Returns [{ key, site, kind }]. Deliberately URL-only: page DOM content is
 * not an identity, and treating it as one is what the mislearning was made of.
 */
export function identitySignals(ctx) {
  const out = [];
  const seen = new Set();
  const add = (key, site, kind) => {
    const id = `${key}@${site}`;
    if (!key || seen.has(id)) return;
    seen.add(id);
    out.push({ key, site, kind });
  };
  for (const url of [ctx?.url, ctx?.finalUrl, ctx?.referrer]) {
    const channel = discordChannelId(url);
    if (channel) add(discordAliasKey(channel), DISCORD_SITE, "discord-channel");
  }
  for (const url of [ctx?.pageUrl, ctx?.url, ctx?.finalUrl, ctx?.referrer,
    ctx?.referrerUrl]) {
    const key = threadAliasKey(threadSlug(url));
    if (key) add(key, hostOf(url), "thread-slug");
  }
  return out;
}

function containsSequence(haystack, needle) {
  if (!needle.length || needle.length > haystack.length) return false;
  for (let i = 0; i <= haystack.length - needle.length; i += 1) {
    let ok = true;
    for (let j = 0; j < needle.length; j += 1) {
      if (haystack[i + j] !== needle[j]) { ok = false; break; }
    }
    if (ok) return true;
  }
  return false;
}

/**
 * Subject strings from a captured context, strongest signal first.
 *
 * ORDER CHANGED after the first evening of real traffic, and matches
 * matcher.py's. It used to be tags-first; on the one forum download where
 * context WAS captured, the tag list was the forum's own section names and
 * other posters' usernames while the subject sat in the URL thread slug and in
 * the page title. The slug leads, the de-branded title follows, tags trail.
 */
export function subjectPhrases(ctx) {
  const out = [];
  const seen = new Set();
  const add = (value) => {
    if (typeof value !== "string") return;
    const v = value.trim();
    if (!v || v.length > 300) return;
    const key = normKey(v);
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(v);
  };
  const slugs = threadSlugs(ctx);
  for (const slug of slugs) add(slug);
  const ownSlug = threadSlug(ctx?.pageUrl) || slugs[0] || "";
  add(titleSubject(ctx?.referrerTitle, hostOf(ctx?.referrerUrl),
    threadSlug(ctx?.referrerUrl)));
  add(titleSubject(ctx?.pageTitle, ctx?.site, ownSlug));
  for (const tag of ctx?.tags || []) add(tag);
  const og = ctx?.og || {};
  for (const k of ["video:actor", "video:tag", "og:video:actor",
    "article:author", "author", "title", "og:title", "site_name"]) add(og[k]);
  add(ctx?.linkText);
  add(ctx?.alt);
  add(ctx?.pageTitle);
  add(ctx?.referrerTitle);
  return out;
}

/**
 * Synchronous fallback decision from the cached snapshot.
 * `snapshot` = { dirs: [{name, key, tokens}], aliases: [{key, site, dir}],
 *                threshold, otherDir }
 * Returns { dir, confidence, reason, auto } or null when nothing scores.
 */
export function localDecide(ctx, snapshot) {
  if (!snapshot || !Array.isArray(snapshot.dirs) || !snapshot.dirs.length) {
    return null;
  }
  const threshold = typeof snapshot.threshold === "number"
    ? snapshot.threshold : 0.75;
  const byName = new Set(snapshot.dirs.map((d) => d.name));
  const byKey = new Map();
  for (const d of snapshot.dirs) if (!byKey.has(d.key)) byKey.set(d.key, d);
  const aliasSite = new Map();
  const aliasGlobal = new Map();
  for (const a of snapshot.aliases || []) {
    (a.site ? aliasSite : aliasGlobal).set(`${a.key}\u0000${a.site || ""}`, a.dir);
  }

  const site = ctx?.site || "";
  const phrases = subjectPhrases(ctx);
  const best = new Map();
  const bump = (dir, score, reason) => {
    if (!byName.has(dir)) return;
    const cur = best.get(dir);
    if (!cur || score > cur.confidence) best.set(dir, { dir, confidence: score, reason });
  };

  // Identity signals first -- the Discord path. A Discord attachment carries
  // no page context at all, so this is the ONLY rule that can score it.
  for (const sig of identitySignals(ctx)) {
    const hit = aliasSite.get(`${sig.key}\u0000${sig.site}`);
    if (hit) bump(hit, SCORE_ALIAS_SITE, `alias(${sig.kind}) '${sig.key}'`);
  }

  for (const phrase of phrases) {
    const key = normKey(phrase);
    if (!key) continue;
    if (site) {
      const hit = aliasSite.get(`${key}\u0000${site}`);
      if (hit) bump(hit, SCORE_ALIAS_SITE, `alias(site:${site}) '${phrase}'`);
    }
    const g = aliasGlobal.get(`${key}\u0000`);
    if (g) bump(g, SCORE_ALIAS_GLOBAL, `alias(global) '${phrase}'`);
    const exact = byKey.get(key);
    if (exact) bump(exact.name, SCORE_TAG_EXACT, `tag=='${exact.name}'`);

    const ptoks = contentTokens(phrase);
    if (!ptoks.length) continue;
    for (const d of snapshot.dirs) {
      const dtoks = d.tokens || [];
      if (!dtoks.length) continue;
      let inner = null; let outer = null;
      if (containsSequence(ptoks, dtoks)) { inner = dtoks; outer = ptoks; } else if (containsSequence(dtoks, ptoks)) { inner = ptoks; outer = dtoks; } else continue;
      if (!passesFuzzyGuard(inner)) continue;
      const coverage = inner.length / outer.length;
      bump(d.name, SCORE_CONTAIN_MIN + (SCORE_CONTAIN_MAX - SCORE_CONTAIN_MIN) * coverage,
        `contains '${inner.join(" ")}' (cached)`);
    }
  }
  if (!best.size) return null;
  const ranked = [...best.values()].sort(
    (a, b) => (b.confidence - a.confidence) || a.dir.localeCompare(b.dir));
  const top = ranked[0];
  // THE SAME AUTO-FILE GATE THE SIDECAR APPLIES. Only a `performer` directory
  // may auto-file; a category always confirms and an unclassified one does
  // too. Without this the cached fallback would auto-file into a directory the
  // sidecar itself would have asked about -- and the fallback runs precisely
  // when the sidecar is slow or down, so the divergence would be invisible.
  const kind = kindOf(snapshot, top.dir);
  const auto = top.confidence >= threshold && kind === KIND_PERFORMER;
  const reason = auto ? top.reason
    : `${kind === KIND_CATEGORY ? "category" : "unclassified"} directory - ${top.reason}`;
  return { ...top, reason, auto, source: "cache" };
}

export const KIND_PERFORMER = "performer";
export const KIND_CATEGORY = "category";
export const KIND_UNKNOWN = "unknown";

/** The kind the /dirs snapshot recorded for a directory. */
export function kindOf(snapshot, name) {
  for (const d of snapshot?.dirs || []) {
    if (d && d.name === name) {
      return d.kind === KIND_PERFORMER || d.kind === KIND_CATEGORY
        ? d.kind : KIND_UNKNOWN;
    }
  }
  return KIND_UNKNOWN;
}

/**
 * The forum thread that linked to this file host, when the link is PROVABLE.
 *
 * A download from a paired file host has no context of its own: the page is a
 * download button and nothing else. The thread that sent the user there does
 * have one -- but only if we can PROVE the two are connected.
 *
 * There is exactly ONE proof, and it is a link the user was observed
 * following: a captured click on another page whose `href`/`mediaSrc` is this
 * page (or is the download's referrer).
 *
 * THE OPENER-TAB BRANCH WAS DELETED, not tightened. It took the newest usable
 * capture whose `tabId` matched `openerTabId`, with nothing binding that
 * capture to the navigation that opened the tab and no time bound at all --
 * so it was "the last thread I saw in that tab" wearing the word "provable".
 * It went wrong on the ordinary forum pattern: open a file host in a new tab,
 * keep browsing the opener tab, and by the time the download fires the newest
 * capture from that tab is a DIFFERENT thread. The href proof cannot always
 * cover for it (a `?ref=` parameter or any URL normalisation breaks the
 * equality), and the wrong answer was not merely used for one match -- it was
 * LEARNED, as a 1.00 identity alias.
 *
 * `openerTabId` is still recorded, and now only ever NARROWS the href proof:
 * when we know which tab this page was opened from, the donor must be in it.
 * That can only reject a candidate, never invent one.
 *
 * No time window either: a window is a guess about how fast someone browses.
 * An unprovable link goes to the picker, which is the whole point.
 *
 * Returns `{ pageUrl, pageTitle }` or null.
 */
export function carryReferrer(item, capture, captures) {
  // Only for a page with NOTHING of its own. A page that produced tags is
  // describing its own content, and importing a thread subject over the top of
  // that would be the tag-list mistake in a new costume.
  if (capture && Array.isArray(capture.tags) && capture.tags.length) return null;

  const here = new Set([capture?.pageUrl, item?.referrer].filter(Boolean));
  if (!here.size) return null;
  const openerTabId = capture?.openerTabId;
  const list = (captures || []).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));

  for (const c of list) {
    if (!c || !c.pageUrl || here.has(c.pageUrl)) continue;
    if (!threadSlug(c.pageUrl) && !c.pageTitle) continue;
    // The proof itself: this donor page linked to the page we are on.
    if (!((c.href && here.has(c.href)) || (c.mediaSrc && here.has(c.mediaSrc)))) {
      continue;
    }
    // ...and, when we know it, the donor must be the tab we came from.
    if (openerTabId !== undefined && openerTabId !== null
        && c.tabId !== openerTabId) {
      continue;
    }
    return { pageUrl: c.pageUrl, pageTitle: c.pageTitle || "" };
  }
  return null;
}

/**
 * Three-tier context correlation. Returns { capture, tier } with tier 0 when
 * nothing correlates.
 *
 *   1  exact URL match on the download's url/finalUrl
 *   2  the download's referrer equals a captured page URL
 *   3  most recent capture from the active tab inside the time window
 */
export function correlateCapture(item, captures, opts = {}) {
  const now = typeof opts.now === "number" ? opts.now : Date.now();
  const windowMs = opts.windowMs ?? DEFAULT_CAPTURE_WINDOW_MS;
  const activeTabId = opts.activeTabId;
  const list = (captures || []).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));

  const urls = new Set([item?.url, item?.finalUrl].filter(Boolean));
  if (urls.size) {
    for (const c of list) {
      if ((c.href && urls.has(c.href)) || (c.mediaSrc && urls.has(c.mediaSrc))) {
        return { capture: c, tier: 1 };
      }
    }
  }
  if (item?.referrer) {
    for (const c of list) {
      if (c.pageUrl && c.pageUrl === item.referrer) return { capture: c, tier: 2 };
    }
  }
  if (activeTabId !== undefined && activeTabId !== null) {
    for (const c of list) {
      if (c.tabId === activeTabId && now - (c.ts || 0) <= windowMs) {
        return { capture: c, tier: 3 };
      }
    }
  }
  return { capture: null, tier: 0 };
}

/** The JSON body POSTed to the sidecar's /match. */
export function buildMatchPayload(item, capture, carried) {
  const c = capture || {};
  const ref = carried || {};
  return {
    // The browser's DownloadItem id. The sidecar records it against the
    // routing decision, and that record is the ONLY thing that later lets
    // /relocate prove a file under the library root was written by this router
    // rather than by qBittorrent.
    downloadId: item?.id ?? null,
    url: item?.url || "",
    finalUrl: item?.finalUrl || "",
    referrer: item?.referrer || "",
    filename: baseName(item?.filename || ""),
    mime: item?.mime || "",
    size: item?.fileSize || item?.totalBytes || 0,
    // THE LEDGER'S KEY, when the download has a stable name that its own URL
    // is not (see playerSourceKey). Empty for every ordinary download, and the
    // sidecar then falls back to `url` exactly as it did before.
    //
    // A Discord attachment BEATS the capture's own key, and the order matters.
    // The capture's key is `playerSourceKey(embedUrl)` -- the page the media
    // was embedded in. On the sites that feature was built for one embed page
    // carries one video, so the page identifies the asset; on Discord one
    // channel URL carries thousands, so using it would collapse an entire
    // channel into a single ledger row. The attachment path identifies the
    // asset exactly, so it wins wherever it exists.
    sourceKey: discordSourceKey(item?.url)
      || (typeof c.sourceKey === "string" ? c.sourceKey : ""),
    page: {
      title: c.pageTitle || "",
      url: c.pageUrl || "",
      site: c.site || "",
      tags: Array.isArray(c.tags) ? c.tags : [],
      og: c.og || {},
      linkText: c.linkText || "",
      alt: c.alt || "",
      // A PROVEN cross-host referrer only (see carryReferrer). The sidecar
      // treats these as identity evidence, so an unprovable guess here would
      // be learned as one.
      referrerUrl: ref.pageUrl || "",
      referrerTitle: ref.pageTitle || "",
    },
  };
}

/** The context shape localDecide expects, from a /match payload. */
export function localContext(payload) {
  const page = payload?.page || {};
  return {
    tags: page.tags,
    og: page.og,
    linkText: page.linkText,
    alt: page.alt,
    pageTitle: page.title,
    pageUrl: page.url,
    site: page.site,
    // The URLs are what carry a Discord channel or a thread slug -- without
    // them the cached fallback cannot score the very downloads the sidecar
    // now can.
    url: payload?.url,
    finalUrl: payload?.finalUrl,
    referrer: payload?.referrer,
    referrerUrl: page.referrerUrl,
    referrerTitle: page.referrerTitle,
  };
}

/** Human-readable dedupe line for the toast. Null when there is no duplicate. */
export function formatDup(dup) {
  if (!dup || !dup.relpath) return null;
  const where = dup.where === "target-dir" ? "already in this folder"
    : "already in the library";
  return `Possible duplicate (${dup.kind}): ${where} -- ${dup.relpath}`;
}

/**
 * The onDeterminingFilename ladder.
 *
 * `deps`:
 *   knownDirs    Set of directory names the extension may file into
 *   otherDir     terminal fallback directory name
 *   timeoutMs    how long to wait for the sidecar
 *   localDecision()  -> decision|null   (synchronous, from the cache)
 *   requestMatch()   -> Promise<decision>
 *   setTimeout / clearTimeout   injectable timers
 *   onDecision(info)            post-suggest side effects (toast/picker)
 *
 * Returns true, the value Chrome requires for an ASYNCHRONOUS suggest().
 */
export function handleDetermining(item, suggest, deps) {
  const otherDir = deps.otherDir || "other";
  // FAIL CLOSED. This used to be `: null`, and sanitizeDirName(name, null)
  // skips the allowlist entirely -- so a knownDirs that was an Array, or
  // undefined, or null (a snapshot that had not loaded, a caller passing the
  // wrong shape) meant ANY syntactically valid directory name coming back from
  // the sidecar was accepted and Chrome created whatever was suggested. The
  // catch-all is always allowed because it is the terminal fallback of this
  // very ladder; nothing else is, unless the snapshot vouched for it.
  const known = deps.knownDirs instanceof Set
    ? new Set([...deps.knownDirs, otherDir])
    : new Set([otherDir]);
  const timeoutMs = deps.timeoutMs ?? DEFAULT_MATCH_TIMEOUT_MS;
  const setT = deps.setTimeout || setTimeout;
  const clearT = deps.clearTimeout || clearTimeout;

  let done = false;
  let timer = null;

  const finish = (decision, source) => {
    if (done) return;
    done = true;
    if (timer !== null) clearT(timer);
    const wanted = decision && decision.auto !== false ? decision.dir : otherDir;
    // A below-threshold answer files into `other/` on purpose: an unconfirmed
    // guess must never quietly pollute a subject directory, and the picker's
    // Esc path is then a no-op instead of a move.
    const safeDir = sanitizeDirName(wanted, known) || otherDir;
    const safeName = sanitizeFileName(baseName(item?.filename || ""));
    suggest({ filename: `${safeDir}/${safeName}`, conflictAction: "uniquify" });
    if (typeof deps.onDecision === "function") {
      try {
        deps.onDecision({
          dir: safeDir,
          filename: safeName,
          source,
          decision: decision || null,
          auto: Boolean(decision && decision.auto !== false
            && sanitizeDirName(decision.dir, known)),
          item,
        });
      } catch {
        // A failing toast must never affect the download.
      }
    }
  };

  const cached = (() => {
    try {
      return deps.localDecision ? deps.localDecision() : null;
    } catch {
      return null;
    }
  })();

  timer = setT(() => {
    timer = null;
    finish(cached, cached ? "cache-timeout" : "other-timeout");
  }, timeoutMs);

  let pending;
  try {
    pending = Promise.resolve(deps.requestMatch ? deps.requestMatch() : null);
  } catch (err) {
    pending = Promise.reject(err);
  }
  pending.then((res) => {
    if (res && typeof res.dir === "string") finish(res, "sidecar");
    else finish(cached, cached ? "cache" : "other");
  }).catch(() => {
    finish(cached, cached ? "cache" : "other");
  });

  return true;
}
