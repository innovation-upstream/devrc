// popup.js -- the all-accounts dashboard.
//
// The RENDER functions are pure and exported for the node tests; the only
// side-effectful part is init(), which reads chrome.storage.local and paints.
// Countdowns are computed at RENDER time from the persisted raw `resetsAt`
// values (never persisted as pre-rendered strings), so a popup opened hours
// after the last snapshot still shows a live countdown.

import { formatCountdown, isStale, stalenessLabel } from "./lib/timefmt.js";
import { ACCOUNT_LABELS_KEY, accountLabel, creditsLine, formatPct } from "./lib/format.js";
import { FREE, availability } from "./lib/availability.js";

// formatPct/creditsLine moved to lib/format.js when the injected widget needed
// the same rules (2026-09-19). Re-exported rather than relocated outright: the
// popup's tests and renderRow() address them here, and one implementation with
// two names beats two implementations.
export { ACCOUNT_LABELS_KEY, accountLabel, creditsLine, formatPct };

/**
 * The label map after one edit. Pure, so the editor's RULE is testable
 * without a DOM: the popup's click handler does nothing but call this and
 * hand the result to storage.
 *
 * Clearing the box REMOVES the override (the row falls back to the API's org
 * name) rather than storing an empty string, which would render a nameless
 * account everywhere. Whitespace is trimmed first, so a box holding one space
 * is a clear, not a rename.
 *
 * 🔴 THE POPUP IS THE ONLY WRITER. The in-page widget reads this map and
 * never edits it: a text input in a card floating over claude.ai's composer
 * is both the wrong affordance and a direct route back to the pointer-events
 * bug that shipped in round 1.
 */
export function nextLabels(labels, orgUuid, text) {
  const src = labels !== null && typeof labels === "object" ? labels : {};
  const out = {};
  for (const k of Object.keys(src)) {
    if (typeof src[k] === "string" && src[k].trim()) out[k] = src[k];
  }
  if (typeof orgUuid !== "string" || !orgUuid) return out;
  const t = typeof text === "string" ? text.trim() : "";
  if (t) out[orgUuid] = t;
  else delete out[orgUuid];
  return out;
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

/**
 * Session headline for one record, e.g. "Session 9% · resets 4h46m".
 *
 * 🔴 THE ELAPSED-RESET VERDICT IS `availability()`, NOT `formatCountdown()`.
 * This line rendered "Session 92% · resets soon" for an account whose window
 * had already closed -- the single state the switch-accounts workflow depends
 * on, reported backwards -- and it kept doing so for a round AFTER the in-page
 * widget was fixed, so the two surfaces showed opposite answers for the same
 * stored record at the same `now`. One rule, one place: lib/availability.js
 * decides, both surfaces read it, and nothing here re-derives it from a
 * countdown string. `formatCountdown()`'s "resets soon" branch is now
 * unreachable from this function, which is the point.
 *
 * `isActive` is availability.js's documented exemption, applied here because
 * this is where the popup knows which row is the active one. content_probe.js
 * fetches with the CURRENT session cookie, so the active account is the one
 * account that really will be re-measured within seconds; for it an elapsed
 * reset is a pending correction and the countdown stays. The in-page widget
 * applies the same exemption by keeping the active record on its own card and
 * off the other-accounts list, so the two surfaces agree row for row.
 */
export function sessionLine(rec, now, isActive) {
  const v = availability(rec, now);
  if (v.state === FREE && isActive !== true) {
    // The verdict, then the evidence for it. The last MEASURED percentage
    // stays visible so an inference can never read as a fresh reading, and
    // there is never a fabricated 0%. Its AGE is already on the row (renderRow
    // puts stalenessLabel(rec.asOf) in `asOf`), which is the one thing the
    // widget's longer meta adds and this one does not need to repeat.
    return `Session AVAILABLE · reset ${stalenessLabel(v.resetsAt, now)}`
      + ` (was ${formatPct(v.sessionPct)})`;
  }
  const s = rec.session && rec.session.utilization;
  const cd = formatCountdown(rec.session && rec.session.resetsAt, now);
  // "resets soon" already carries the verb; everything else (a real
  // countdown, or "unknown") gets it. It survives only for the active
  // account, where it is true.
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

/** One row's display model. Pure; the DOM paint is init()'s only job.
 *
 * `labels` is optional: omitted, every row falls back to the API's org name,
 * which is what this returned before per-account renaming existed. */
export function renderRow(rec, now, isActive, labels) {
  const stale = isStale(rec.asOf, now) || rec.staleSince !== null;
  const credits = creditsLine(rec.credits);
  const points = sparkline(rec.history);
  return {
    name: accountLabel(rec, labels),
    orgUuid: typeof rec.orgUuid === "string" ? rec.orgUuid : null,
    isActive: Boolean(isActive),
    session: sessionLine(rec, now, Boolean(isActive)),
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

/**
 * The per-account rename affordance. Clicking it swaps the name for a text
 * box; Enter or blur saves, Escape cancels, an empty box clears the override.
 *
 * The RULE it applies is `nextLabels()`, which is pure and tested. Everything
 * here is glue: read the current map, call it, write the result back, repaint.
 * `paint()` re-runs from the storage change anyway, but it is called directly
 * too so the edit lands even if the onChanged round-trip is slow.
 */
function renameButton(row) {
  const btn = document.createElement("button");
  btn.className = "rename";
  btn.type = "button";
  btn.title = `Rename ${row.name}`;
  btn.setAttribute("aria-label", `Rename ${row.name}`);
  btn.textContent = "✎";
  btn.addEventListener("click", () => {
    const input = document.createElement("input");
    input.className = "labelinput";
    input.type = "text";
    input.value = row.name;
    input.setAttribute("aria-label", "Account label");
    let done = false;
    const commit = (save) => {
      if (done) return;
      done = true;
      if (!save) { paint(); return; }
      const text = input.value;
      try {
        chrome.storage.local.get([ACCOUNT_LABELS_KEY]).then((got) => {
          const next = nextLabels(got[ACCOUNT_LABELS_KEY], row.orgUuid, text);
          return chrome.storage.local.set({ [ACCOUNT_LABELS_KEY]: next });
        }).then(paint).catch(() => { /* storage gone; nothing to save into */ });
      } catch { /* worker unreachable */ }
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit(true);
      else if (e.key === "Escape") commit(false);
    });
    input.addEventListener("blur", () => commit(true));
    const parent = btn.parentElement;
    if (!parent) return;
    parent.textContent = "";
    parent.append(input);
    if (typeof input.focus === "function") input.focus();
    if (typeof input.select === "function") input.select();
  });
  return btn;
}

function paint() {
  const host = document.getElementById("accounts");
  const empty = document.getElementById("empty");
  if (!host || !empty) return;
  chrome.storage.local.get(["accounts", "lastActiveOrg", ACCOUNT_LABELS_KEY]).then((got) => {
    const accounts = got.accounts && typeof got.accounts === "object" ? got.accounts : {};
    const now = Date.now();
    const labels = got[ACCOUNT_LABELS_KEY];
    const ordered = orderAccounts(accounts, got.lastActiveOrg);
    empty.hidden = ordered.length > 0;
    host.textContent = "";
    for (const rec of ordered) {
      const row = renderRow(rec, now, rec.orgUuid === got.lastActiveOrg, labels);
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
      if (row.orgUuid) row1.append(name, renameButton(row), asof);
      else row1.append(name, asof);

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
