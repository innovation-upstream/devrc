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

/** Ordered alphanumeric tokens, minus stopwords. */
export function contentTokens(text) {
  if (typeof text !== "string") return [];
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter((t) => t && !STOPWORDS.has(t));
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
export const MIN_SLUG_TOKENS = 2;

const DISCORD_CDN_HOSTS = new Set(["cdn.discordapp.com", "media.discordapp.net"]);
const DISCORD_ATTACHMENT_SEGMENTS = new Set([
  "attachments", "ephemeral-attachments",
]);
const SNOWFLAKE = /^[0-9]{5,25}$/;

// Structural route/verb segments, not a vocabulary of subject words.
const PATH_CHROME = new Set([
  "threads", "thread", "topic", "topics", "forum", "forums", "board",
  "boards", "index.php", "showthread.php", "viewtopic.php", "viewforum.php",
  "t", "f", "p", "post", "posts", "page", "pages", "attachment",
  "attachments", "download", "downloads", "view", "watch", "media", "album",
  "gallery", "comments", "s", "goto", "print",
]);
const TRAILING_ID = /[.\-_]\d{2,}$/;
const LEADING_ID = /^\d{2,}[.\-_]/;
// Title separators. Escaped rather than literal: every source file here is
// plain ASCII on purpose (see tests/source_hygiene.test.mjs).
const TITLE_SPLIT
  = /\s*[|\u2013\u2014\u00b7\u2022\u00bb\u00ab]\s*|\s+[-\u2010]\s+|\s*::\s*/;

export function hostOf(url) {
  if (typeof url !== "string" || !url) return "";
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function pathSegments(url) {
  if (typeof url !== "string" || !url) return [];
  try {
    return new URL(url).pathname.split("/").filter(Boolean);
  } catch {
    return [];
  }
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

export function threadAliasKey(slug) {
  const toks = contentTokens(slug);
  return toks.length ? KEY_PREFIX_THREAD + toks.join("-") : "";
}

/**
 * The forum thread subject carried by a URL path, as a phrase.
 * `/forums/some-section/threads/subject-name.12345/page-2` -> "subject name"
 *
 * TWO tokens minimum, not the usual fuzzy guard: a file host's path is
 * `/d/AbCdEf`, one opaque token, and minting that as a thread identity would
 * invent a subject for a page that has no thread.
 */
export function threadSlug(url) {
  let best = [];
  for (const segment of pathSegments(url)) {
    if (PATH_CHROME.has(segment.toLowerCase())) continue;
    const toks = contentTokens(
      segment.replace(LEADING_ID, "").replace(TRAILING_ID, ""));
    if (toks.length < MIN_SLUG_TOKENS) continue;
    if (toks.length <= 2 && PATH_CHROME.has(toks[0])) continue;   // `page-2`
    if (toks.every((t) => /^\d+$/.test(t))) continue;
    best = toks;                                  // deepest qualifying wins
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

/** The subject half of a page title, with the site's branding dropped. */
export function titleSubject(title, site) {
  if (typeof title !== "string" || !title.trim()) return "";
  const kept = [];
  for (const raw of title.split(TITLE_SPLIT)) {
    const part = (raw || "").trim();
    const toks = contentTokens(part);
    if (!toks.length || isSiteBranding(part, site)) continue;
    kept.push({ n: toks.length, part });
  }
  if (!kept.length) return "";
  kept.sort((a, b) => b.n - a.n);
  return kept[0].part;
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
  for (const slug of threadSlugs(ctx)) add(slug);
  add(titleSubject(ctx?.referrerTitle, hostOf(ctx?.referrerUrl)));
  add(titleSubject(ctx?.pageTitle, ctx?.site));
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
 * have one -- but only if we can prove the two are connected. Two proofs, and
 * deliberately no third:
 *
 *   1. a captured click on another page whose `href` IS this page (or is the
 *      download's referrer);
 *   2. the tab this one was OPENED FROM (`openerTabId`).
 *
 * There is NO time window and no "the last thread I saw", because both are
 * guesses that get it wrong exactly when the user has several tabs open --
 * which is how anyone browses a forum. An unprovable link goes to the picker.
 *
 * Returns `{ pageUrl, pageTitle }` or null.
 */
export function carryReferrer(item, capture, captures) {
  // Only for a page with NOTHING of its own. A page that produced tags is
  // describing its own content, and importing a thread subject over the top of
  // that would be the tag-list mistake in a new costume.
  if (capture && Array.isArray(capture.tags) && capture.tags.length) return null;

  const here = new Set([capture?.pageUrl, item?.referrer].filter(Boolean));
  const openerTabId = capture?.openerTabId;
  const list = (captures || []).slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));

  const usable = (c) => c && c.pageUrl && !here.has(c.pageUrl)
    && (threadSlug(c.pageUrl) || c.pageTitle);

  for (const c of list) {
    if (!usable(c)) continue;
    if ((c.href && here.has(c.href)) || (c.mediaSrc && here.has(c.mediaSrc))) {
      return { pageUrl: c.pageUrl, pageTitle: c.pageTitle || "" };
    }
  }
  if (openerTabId !== undefined && openerTabId !== null) {
    for (const c of list) {
      if (usable(c) && c.tabId === openerTabId) {
        return { pageUrl: c.pageUrl, pageTitle: c.pageTitle || "" };
      }
    }
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
