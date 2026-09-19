// popup.js -- the all-accounts dashboard.
//
// The RENDER functions are pure and exported for the node tests; the only
// side-effectful part is init(), which reads chrome.storage.local and paints.
// Countdowns are computed at RENDER time from the persisted raw `resetsAt`
// values (never persisted as pre-rendered strings), so a popup opened hours
// after the last snapshot still shows a live countdown.

import { formatCountdown, isStale, stalenessLabel } from "./lib/timefmt.js";

/** Integer percent with a "?" for unknown. */
export function formatPct(p) {
  return typeof p === "number" && Number.isFinite(p) ? `${Math.round(p)}%` : "?";
}

const CURRENCY_SYMBOLS = { USD: "$", EUR: "\u20ac", GBP: "\u00a3" };

/**
 * Credits remaining, from minor units -> a display string. Null when the
 * numbers are unknown; the disabled reason verbatim when credits are off.
 */
export function creditsLine(credits) {
  if (!credits || typeof credits !== "object") return null;
  if (credits.enabled === false) {
    return credits.disabledReason ? `Credits disabled: ${credits.disabledReason}` : "Credits disabled";
  }
  const { limit, used, currency } = credits;
  if (typeof limit !== "number" || !Number.isFinite(limit)) return null;
  const remaining = limit - (typeof used === "number" && Number.isFinite(used) ? used : 0);
  const major = remaining / 100;
  const sym = CURRENCY_SYMBOLS[currency] || (currency ? `${currency} ` : "");
  return `${sym}${major.toFixed(2)} credits left`;
}

/**
 * Popup order: the ACTIVE account first, then by snapshot freshness
 * (freshest first). `accounts` is the stored {uuid -> record} map; total on
 * null/garbage input.
 */
export function orderAccounts(accounts, lastActiveOrg) {
  const map = accounts && typeof accounts === "object" ? accounts : {};
  const list = Object.values(map);
  const active = lastActiveOrg && map[lastActiveOrg] ? [map[lastActiveOrg]] : [];
  const rest = list.filter((r) => r !== map[lastActiveOrg]);
  const byFreshness = (a, b) => (b.asOf || 0) - (a.asOf || 0);
  return [...active, ...rest.sort(byFreshness)];
}

/** Session/weekly headline for one record, e.g. "Session 9% · resets 4h46m". */
export function sessionLine(rec, now) {
  const s = rec.session && rec.session.utilization;
  const cd = formatCountdown(rec.session && rec.session.resetsAt, now);
  // "resets soon" already carries the verb; everything else (a real
  // countdown, or "unknown") gets it.
  const resetPart = cd === "resets soon" ? cd : `resets ${cd}`;
  return `Session ${formatPct(s)} · ${resetPart}`;
}

export function weeklyLine(rec) {
  return `Weekly ${formatPct(rec.weekly && rec.weekly.utilization)}`;
}

/**
 * Weekly SESSION-utilization sparkline from the history ring buffer, as an
 * SVG polyline `points` string in a 100x24 box, or null under 2 samples.
 * y is inverted (0% at the bottom). Null samples are skipped.
 */
export function sparkline(history) {
  if (!Array.isArray(history)) return null;
  const pts = history.filter((s) => Array.isArray(s) && s.length >= 3
    && typeof s[0] === "number" && typeof s[1] === "number");
  if (pts.length < 2) return null;
  const t0 = pts[0][0];
  const t1 = pts[pts.length - 1][0];
  const span = t1 - t0 > 0 ? t1 - t0 : 1;
  const out = pts.map(([ts, sess]) => {
    const x = ((ts - t0) / span) * 100;
    const y = 24 - (Math.max(0, Math.min(100, sess)) / 100) * 24;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return out.join(" ");
}

/** One row's display model. Pure; the DOM paint is init()'s only job. */
export function renderRow(rec, now, isActive) {
  const stale = isStale(rec.asOf, now) || rec.staleSince !== null;
  const credits = creditsLine(rec.credits);
  const points = sparkline(rec.history);
  return {
    name: rec.orgName,
    isActive: Boolean(isActive),
    session: sessionLine(rec, now),
    weekly: weeklyLine(rec),
    codeWeekly: typeof rec.codeWeeklyPercent === "number"
      ? `Claude Code ${formatPct(rec.codeWeeklyPercent)}` : null,
    credits,
    asOf: stale ? `${stalenessLabel(rec.asOf, now)} · stale` : stalenessLabel(rec.asOf, now),
    stale,
    severity: rec.severity || null,
    sparkPoints: points,
  };
}

export const EMPTY_STATE_TEXT = "No accounts yet — open claude.ai once and this fills in.";

// --- DOM paint (not under test) ---------------------------------------------- //

function paint() {
  const host = document.getElementById("accounts");
  const empty = document.getElementById("empty");
  if (!host || !empty) return;
  chrome.storage.local.get(["accounts", "lastActiveOrg"]).then((got) => {
    const accounts = got.accounts && typeof got.accounts === "object" ? got.accounts : {};
    const now = Date.now();
    const ordered = orderAccounts(accounts, got.lastActiveOrg);
    empty.hidden = ordered.length > 0;
    host.textContent = "";
    for (const rec of ordered) {
      const row = renderRow(rec, now, rec.orgUuid === got.lastActiveOrg);
      const div = document.createElement("div");
      div.className = `account${row.isActive ? " active" : ""}${row.stale ? " stale" : ""}`;

      const row1 = document.createElement("div");
      row1.className = "row1";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = row.name;
      const asof = document.createElement("span");
      asof.className = "asof";
      asof.textContent = row.asOf;
      row1.append(name, asof);

      const metrics = document.createElement("div");
      metrics.className = "metrics";
      const sess = document.createElement("span");
      sess.textContent = row.session;
      const wk = document.createElement("span");
      wk.textContent = row.weekly;
      metrics.append(sess, wk);
      if (row.codeWeekly) {
        const cc = document.createElement("span");
        cc.textContent = row.codeWeekly;
        metrics.append(cc);
      }
      if (row.credits) {
        const cr = document.createElement("span");
        cr.className = `credits${row.credits.startsWith("Credits disabled") ? " off" : ""}`;
        cr.textContent = row.credits;
        metrics.append(cr);
      }

      div.append(row1, metrics);

      if (row.sparkPoints) {
        const wrap = document.createElement("div");
        wrap.className = "spark";
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("width", "100%");
        svg.setAttribute("height", "24");
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("viewBox", "0 0 100 24");
        const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        poly.setAttribute("points", row.sparkPoints);
        poly.setAttribute("fill", "none");
        poly.setAttribute("stroke", "#83a598");
        poly.setAttribute("stroke-width", "1.5");
        svg.append(poly);
        wrap.append(svg);
        div.append(wrap);
      }
      host.append(div);
    }
  }).catch(() => { /* storage gone; the popup just stays empty */ });
}

const refreshBtn = typeof document !== "undefined"
  ? document.getElementById("refresh") : null;
if (refreshBtn) {
  refreshBtn.addEventListener("click", () => {
    try {
      const sending = chrome.runtime.sendMessage({ type: "cu:probe-request" });
      if (sending && typeof sending.catch === "function") sending.catch(() => {});
    } catch { /* worker unreachable */ }
  });
}

if (typeof document !== "undefined" && document.getElementById("accounts")) {
  paint();
}
