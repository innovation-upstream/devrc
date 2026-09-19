// normalize.js -- raw claude.ai usage JSON -> the stored account record.
//
// Pure and shared by the service worker and the node tests. The API is
// cookie-auth'd and its schema is only recon-observed, not contracted, so this
// module has one rule above all others: NEVER THROW. Any input -- null, a
// string, a number, a half-formed object, a field that recon saw as null --
// must produce a valid record, with unknown fields dropped rather than passed
// through (chrome.storage.local must never become a dump for whatever the API
// starts returning).
//
// Field semantics follow the measured recon (claudedocs/proposal-claude-usage-tracker.md):
//   five_hour  {utilization, resets_at, locked_reason}   session window
//   seven_day  {utilization, resets_at}                  weekly all-models
//   seven_day_opus / seven_day_sonnet / codename keys    named windows, mostly null
//   limits[]   {kind, percent, severity, resets_at, scope.model.display_name, is_active}
//   extra_usage {is_enabled, monthly_limit (minor units), used_credits, currency, disabled_reason}
//   seven_day_breakdown.rows[]  per-surface split incl. the claude_code row
//
// `resets_at` is persisted RAW (ISO string) -- countdowns are computed at
// display time so a wrong clock at snapshot time cannot bake a wrong reset
// into storage.

/** Keys that are NOT named model windows. Anything else object-shaped on the
 * raw payload is treated as an opaque extra window row (recon saw codenames
 * like `nimbus_quill`, mostly null). */
const RESERVED_KEYS = new Set([
  "five_hour",
  "seven_day",
  "limits",
  "extra_usage",
  "seven_day_breakdown",
]);

const KNOWN_WINDOW_LABELS = {
  seven_day_opus: "Opus (7-day)",
  seven_day_sonnet: "Sonnet (7-day)",
};

function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** A number, or a string that parses as one. Everything else -> null. */
function normNum(v) {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function normPct(v) {
  return normNum(v);
}

/** Raw ISO string persisted verbatim; anything else -> null. */
function normIso(v) {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function normStr(v) {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** one window object {utilization, resets_at, locked_reason?} -> fields */
function normWindow(v) {
  if (!isObj(v)) return { utilization: null, resetsAt: null, lockedReason: null };
  return {
    utilization: normPct(v.utilization),
    resetsAt: normIso(v.resets_at),
    lockedReason: normStr(v.locked_reason),
  };
}

/** limits[] rows -> normalized rows. ALL kinds are kept (session and
 * weekly_all carry the severity signal); the caller decides which become
 * extra display rows. A row that is not an object is skipped. */
function normLimitRows(limits) {
  if (!Array.isArray(limits)) return [];
  const out = [];
  for (const row of limits) {
    if (!isObj(row)) continue;
    const model = isObj(row.scope) && isObj(row.scope.model)
      ? normStr(row.scope.model.display_name)
      : null;
    out.push({
      kind: normStr(row.kind),
      label: model || normStr(row.kind) || "scoped",
      percent: normPct(row.percent),
      severity: normStr(row.severity),
      resetsAt: normIso(row.resets_at),
      isActive: row.is_active === true,
    });
  }
  return out;
}

/** Named model windows (seven_day_opus, codenames) -> extra rows. Null
 * windows produce nothing -- recon saw them mostly null. */
function normNamedWindows(raw) {
  const out = [];
  for (const key of Object.keys(raw)) {
    if (RESERVED_KEYS.has(key)) continue;
    const v = raw[key];
    if (!isObj(v)) continue;
    // Only object-shaped values with at least one recon'd window field count;
    // a random object the API added under an unknown key is dropped, not
    // inventoried.
    if (!("utilization" in v) && !("resets_at" in v)) continue;
    out.push({
      label: KNOWN_WINDOW_LABELS[key] || key,
      percent: normPct(v.utilization),
      resetsAt: normIso(v.resets_at),
    });
  }
  return out;
}

function normExtraUsage(v) {
  if (!isObj(v)) {
    return { enabled: null, used: null, limit: null, currency: null, disabledReason: null };
  }
  return {
    enabled: typeof v.is_enabled === "boolean" ? v.is_enabled : null,
    used: normNum(v.used_credits),
    limit: normNum(v.monthly_limit),
    currency: normStr(v.currency),
    disabledReason: normStr(v.disabled_reason),
  };
}

function normBreakdown(v) {
  if (!isObj(v) || !Array.isArray(v.rows)) return null;
  const row = v.rows.find((r) => isObj(r) && r.key === "claude_code");
  return row ? normPct(row.percent) : null;
}

/** The most severe severity among ACTIVE limit rows, by highest percent.
 * Severity strings are the API's (uncontracted); the badge maps them
 * defensively. No active row -> null (rendered as "ok"). */
function normSeverity(rows) {
  let best = null;
  for (const row of rows) {
    if (!row.isActive) continue;
    if (best === null || (row.percent ?? -1) > (best.percent ?? -1)) best = row;
  }
  return best ? best.severity : null;
}

const EMPTY_HISTORY = Object.freeze([]);

/**
 * raw usage JSON -> normalized account record. Total: any input yields a
 * record; unknown fields never survive the WRITE path (the record is built
 * field-by-field, never spread). The storage READ-BACK path
 * (normalizeStoredRecord) is likewise field-by-field, so nothing unknown
 * round-trips storage either.
 */
export function normalizeUsage(raw, orgUuid, orgName, now) {
  const src = isObj(raw) ? raw : {};
  const session = normWindow(src.five_hour);
  const weekly = normWindow(src.seven_day);
  const limitRows = normLimitRows(src.limits);
  // session/weekly_all duplicate the two headline fields above; every other
  // kind (weekly_scoped + unknown future kinds) becomes an extra row.
  const extraRows = [
    ...limitRows.filter((r) => r.kind !== "session" && r.kind !== "weekly_all"),
    ...normNamedWindows(src),
  ];
  const credits = normExtraUsage(src.extra_usage);

  return {
    orgUuid: normStr(orgUuid),
    orgName: normStr(orgName) || "unknown account",
    session,
    weekly,
    extraRows,
    credits,
    codeWeeklyPercent: normBreakdown(src.seven_day_breakdown),
    severity: normSeverity(limitRows),
    isActiveLimit: limitRows.some((r) => r.isActive),
    asOf: typeof now === "number" && Number.isFinite(now) ? now : Date.now(),
    // Set when a fetch for this account 401/403'd; cleared by the next
    // successful snapshot. Display + badge treat it as stale immediately.
    staleSince: null,
    history: EMPTY_HISTORY.slice(),
  };
}

/**
 * Merge a fresh record into a stored one. Stored records come from
 * chrome.storage.local and may predate a field (extension update, partial
 * write) -- every missing field gets its default instead of a throw.
 * The history ring buffer is owned here: new sample appended only when the
 * previous one is >=15 min old, 7-day eviction, hard cap.
 */
export const MIN_SAMPLE_GAP_MS = 15 * 60 * 1000;
export const HISTORY_TTL_MS = 7 * 24 * 60 * 60 * 1000;
// 7 days at the 15-minute cadence is the theoretical max.
export const HISTORY_MAX = 7 * 24 * 4;

export function mergeStored(prev, fresh, now) {
  const base = prev && typeof prev === "object" ? prev : {};
  const history = Array.isArray(base.history) ? base.history.filter(saneSample) : [];
  const last = history.length ? history[history.length - 1] : null;
  if (!last || now - last[0] >= MIN_SAMPLE_GAP_MS) {
    history.push([fresh.asOf, fresh.session.utilization, fresh.weekly.utilization]);
  }
  const cutoff = now - HISTORY_TTL_MS;
  let kept = history.filter((s) => s[0] >= cutoff);
  if (kept.length > HISTORY_MAX) kept = kept.slice(kept.length - HISTORY_MAX);

  return {
    ...fresh,
    history: kept,
    staleSince: null,
    // asOf stays the FRESH snapshot time; a merge never backdates freshness.
  };
}

function saneSample(s) {
  return Array.isArray(s) && s.length >= 3
    && typeof s[0] === "number" && Number.isFinite(s[0]);
}

/**
 * Defensive read-back of one stored account record. Anything missing or of
 * the wrong shape gets a default; the record that comes out always has the
 * full field set (popup + badge may run on a worker's cold read).
 */
export function normalizeStoredRecord(raw) {
  const src = isObj(raw) ? raw : {};
  const rec = normalizeUsage({}, src.orgUuid, src.orgName, src.asOf);
  rec.session = isObj(src.session)
    ? {
        utilization: normPct(src.session.utilization),
        resetsAt: normIso(src.session.resetsAt),
        lockedReason: normStr(src.session.lockedReason),
      }
    : rec.session;
  rec.weekly = isObj(src.weekly)
    ? {
        utilization: normPct(src.weekly.utilization),
        resetsAt: normIso(src.weekly.resetsAt),
        lockedReason: normStr(src.weekly.lockedReason),
      }
    : rec.weekly;
  rec.extraRows = Array.isArray(src.extraRows) ? src.extraRows.filter(isObj) : [];
  rec.credits = isObj(src.credits)
    ? {
        enabled: typeof src.credits.enabled === "boolean" ? src.credits.enabled : null,
        used: normNum(src.credits.used),
        limit: normNum(src.credits.limit),
        currency: normStr(src.credits.currency),
        disabledReason: normStr(src.credits.disabledReason),
      }
    : rec.credits;
  rec.codeWeeklyPercent = normPct(src.codeWeeklyPercent);
  rec.severity = normStr(src.severity);
  rec.isActiveLimit = src.isActiveLimit === true;
  rec.asOf = typeof src.asOf === "number" && Number.isFinite(src.asOf) ? src.asOf : 0;
  rec.staleSince = typeof src.staleSince === "number" ? src.staleSince : null;
  rec.history = Array.isArray(src.history) ? src.history.filter(saneSample) : [];
  return rec;
}
