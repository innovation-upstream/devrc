// format.js -- display formatters shared by the popup and the injected widget.
//
// These two surfaces show the SAME numbers in different chrome, so the rules
// for turning a stored record into text live here once. popup.js re-exports
// them so its own callers and tests keep a stable surface; the widget imports
// them directly. A second copy in the widget would be a predicate duplicated
// across call sites, which is how the same rounding/currency bug gets fixed
// twice and stays wrong in one place.
//
// Pure, total, and never throws: every input shape the API or a stale stored
// record can produce must yield a string or null, never an exception -- the
// widget renders inside the operator's claude.ai tab and must not be able to
// break the page.

/** Integer percent with a "?" for unknown. */
export function formatPct(p) {
  return typeof p === "number" && Number.isFinite(p) ? `${Math.round(p)}%` : "?";
}

/** chrome.storage.local key holding the per-account display-name overrides,
 * `{orgUuid -> string}`.
 *
 * 🔴 THE POPUP WRITES IT; THE WIDGET ONLY READS IT. The in-page card floats
 * over claude.ai's composer corner, and a text input there is both the wrong
 * place for an edit affordance and a direct route back to the pointer-events
 * bug that shipped in round 1 (the widget swallowing clicks meant for the
 * page). The editor lives in popup.js. */
export const ACCOUNT_LABELS_KEY = "accountLabels";

/**
 * What to CALL one account: the operator's override, else the API's org name,
 * else a plain placeholder. The API's names are long and near-identical
 * ("user@example.com's Organization"), which is unreadable in a 232px card
 * and useless for deciding which account to switch to.
 *
 * A blank or whitespace-only override falls through to the org name rather
 * than rendering an empty row -- clearing the box in the popup is how you
 * REMOVE an override, so it must never produce a nameless account.
 */
export function accountLabel(record, labels) {
  const rec = record !== null && typeof record === "object" ? record : null;
  const map = labels !== null && typeof labels === "object" ? labels : null;
  const uuid = rec && typeof rec.orgUuid === "string" && rec.orgUuid ? rec.orgUuid : null;
  if (map && uuid) {
    // hasOwnProperty, for the reason lib/severity.js's SEVERITY_TONES lookup
    // documents: a bare read of `map["constructor"]` returns something
    // truthy off Object.prototype. The typeof check below would already
    // reject a function, but the guard states the intent rather than relying
    // on a second check's side effect.
    const own = Object.prototype.hasOwnProperty.call(map, uuid) ? map[uuid] : null;
    if (typeof own === "string" && own.trim()) return own.trim();
  }
  const name = rec && typeof rec.orgName === "string" ? rec.orgName.trim() : "";
  return name || "unknown account";
}

const CURRENCY_SYMBOLS = { USD: "$", EUR: "€", GBP: "£" };

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
