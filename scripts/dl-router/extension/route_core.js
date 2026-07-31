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

/** Subject strings from a captured context, strongest signal first. */
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
  for (const tag of ctx?.tags || []) add(tag);
  const og = ctx?.og || {};
  for (const k of ["video:actor", "video:tag", "og:video:actor",
    "article:author", "author", "title", "og:title", "site_name"]) add(og[k]);
  add(ctx?.linkText);
  add(ctx?.alt);
  add(ctx?.pageTitle);
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
  return { ...top, auto: top.confidence >= threshold, source: "cache" };
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
export function buildMatchPayload(item, capture) {
  const c = capture || {};
  return {
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
    },
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
