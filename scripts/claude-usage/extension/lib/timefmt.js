// timefmt.js -- pure time formatting for resets and staleness.
//
// Everything here computes from epoch-millisecond DIFFERENCES. No Date
// getter that reads the host timezone is ever used, so a countdown rendered
// on any machine in any timezone is byte-identical (pinned by the UTC-pinned
// tests).
//
// `resets_at` values are the raw ISO strings persisted at snapshot time;
// they are parsed only here, at display time.

/** ISO string | epoch ms | null -> epoch ms | null. Never throws. */
export function parseWhen(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.length > 0) {
    const t = Date.parse(v);
    return Number.isNaN(t) ? null : t;
  }
  return null;
}

/**
 * Countdown until a reset: "59s", "1m", "4h46m", "2d3h".
 *   <=0         -> "resets soon"  (expired; the next snapshot will correct it)
 *   null/garbage -> "unknown"
 * Boundaries (pinned): 59s stays "59s", 60s becomes "1m"; 23h59m59s stays
 * "23h59m", 24h becomes "1d0h".
 */
export function formatCountdown(resetsAt, now) {
  const t = parseWhen(resetsAt);
  if (t === null) return "unknown";
  const ms = t - now;
  if (ms <= 0) return "resets soon";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const mTotal = Math.floor(s / 60);
  if (mTotal < 60) return `${mTotal}m`;
  const hTotal = Math.floor(mTotal / 60);
  if (hTotal < 24) return `${hTotal}h${mTotal % 60}m`;
  const d = Math.floor(hTotal / 24);
  return `${d}d${hTotal % 24}h`;
}

/** "just now" | "5m ago" | "3h ago" | "2d ago" | "unknown". */
export function stalenessLabel(asOf, now) {
  if (typeof asOf !== "number" || !Number.isFinite(asOf)) return "unknown";
  const ms = now - asOf;
  if (ms < 60 * 1000) return "just now";
  const m = Math.floor(ms / (60 * 1000));
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** A snapshot older than STALE_AFTER_MS is stale (badge goes gray). */
export const STALE_AFTER_MS = 6 * 60 * 60 * 1000;

export function isStale(asOf, now) {
  if (typeof asOf !== "number" || !Number.isFinite(asOf)) return true;
  return now - asOf > STALE_AFTER_MS;
}
