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
