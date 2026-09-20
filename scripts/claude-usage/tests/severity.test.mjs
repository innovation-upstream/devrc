// severity.test.mjs -- the ONE rule for "how bad is this usage right now".
//
// This module exists because the badge and the in-page widget each decided it
// independently and disagreed: the badge coloured from the API's `severity`
// string, the widget banded the raw percentages. The pins here are therefore
// less about any single mapping than about the COMBINATION rule -- that a
// severity string can only ever escalate a percentage band, never calm one --
// because that is the direction the old badge got wrong.
import test from "node:test";
import assert from "node:assert/strict";

const S = await import("../extension/lib/severity.js");
const { isStale } = await import("../extension/lib/timefmt.js");
const { NOW } = await import("./fixtures.mjs");

const record = (over = {}) => ({
  session: { utilization: null, resetsAt: null, lockedReason: null },
  weekly: { utilization: null, resetsAt: null, lockedReason: null },
  severity: null,
  asOf: NOW,
  staleSince: null,
  ...over,
});

// --- the severity string alone ---------------------------------------------- //

test("severityTone maps the recon'd strings", () => {
  assert.equal(S.severityTone("critical"), "crit");
  assert.equal(S.severityTone("high"), "crit");
  assert.equal(S.severityTone("elevated"), "warn");
  assert.equal(S.severityTone("medium"), "warn");
  assert.equal(S.severityTone("low"), "ok");
  assert.equal(S.severityTone("none"), "ok");
});

test("an ABSENT severity is null -- no vote -- and specifically not 'ok'", () => {
  // Half the bug, in one assertion. The old severityColor(null) -> green is
  // what made a 99%-utilized account look calm. `null` here means "this
  // source said nothing"; the percent band then decides alone.
  for (const absent of [null, undefined, "", 123, {}]) {
    assert.equal(S.severityTone(absent), null, `${JSON.stringify(absent)}`);
    assert.notEqual(S.severityTone(absent), "ok");
  }
});

test("an UNREADABLE severity is 'unknown' -- a real vote, not silence", () => {
  // The other half. A severity the API invented later is information we
  // cannot interpret, so it must colour amber rather than be dropped.
  assert.equal(S.severityTone("sev_from_a_future_release"), "unknown");
  assert.equal(S.severityTone("CRITICAL"), "unknown", "the map is case-sensitive on purpose");
});

test("an unreadable severity is NOT dropped when the percentages are calm", () => {
  // The distinction above has to survive the combination, or it is decorative:
  // a quiet source yields, a loud-but-unreadable one does not.
  const quiet = record({ session: { utilization: 10 }, weekly: { utilization: 10 }, severity: null });
  assert.equal(S.toneForRecord(quiet, NOW, isStale), "ok", "silence yields to a real reading");

  const unreadable = record({
    session: { utilization: 10 }, weekly: { utilization: 10 },
    severity: "sev_from_a_future_release",
  });
  assert.equal(S.toneForRecord(unreadable, NOW, isStale), "unknown",
    "an uninterpretable severity must not be silently discarded");
});

// --- the percent band alone ---------------------------------------------------- //

test("percentTone takes the HIGHER of session and weekly", () => {
  assert.equal(S.percentTone(5, 97), "crit");
  assert.equal(S.percentTone(97, 5), "crit", "either side can be the binding one");
  assert.equal(S.percentTone(5, 85), "warn");
  assert.equal(S.percentTone(5, 40), "ok");
});

test("percentTone thresholds are inclusive at exactly WARN_PCT and CRIT_PCT", () => {
  assert.equal(S.percentTone(S.WARN_PCT - 0.6, null), "ok");
  assert.equal(S.percentTone(S.WARN_PCT, null), "warn");
  assert.equal(S.percentTone(S.CRIT_PCT - 0.1, null), "warn");
  assert.equal(S.percentTone(S.CRIT_PCT, null), "crit");
});

test("no usable number at all is null -- no vote -- and specifically not 'ok'", () => {
  for (const [s, w] of [[null, null], [undefined, undefined], ["85", null], [NaN, Infinity]]) {
    assert.equal(S.percentTone(s, w), null, `${String(s)}/${String(w)}`);
    assert.notEqual(S.percentTone(s, w), "ok");
  }
  assert.equal(S.percentTone(null, 40), "ok", "one usable side is enough");
});

test("a record with NO signal from either source is 'unknown', never 'ok'", () => {
  const silent = record({ severity: null });
  assert.equal(S.toneForRecord(silent, NOW, isStale), "unknown",
    "we do not know, and not-knowing is amber");
});

// --- the combination rule -- the half the badge got wrong ----------------------- //

test("worstTone is a total order and symmetric in its arguments", () => {
  assert.equal(S.worstTone("ok", "crit"), "crit");
  assert.equal(S.worstTone("crit", "ok"), "crit");
  assert.equal(S.worstTone("warn", "unknown"), "warn");
  assert.equal(S.worstTone("unknown", "ok"), "unknown");
  assert.equal(S.worstTone("stale", "crit"), "stale");
  assert.equal(S.worstTone("ok", "ok"), "ok");
  // An unrecognised tone yields to the one that IS recognised rather than
  // poisoning the result.
  assert.equal(S.worstTone("nonsense", "warn"), "warn");
  assert.equal(S.worstTone("warn", "nonsense"), "warn");
});

test("a severity string ESCALATES a calm percentage", () => {
  const r = record({ session: { utilization: 5 }, weekly: { utilization: 5 }, severity: "critical" });
  assert.equal(S.toneForRecord(r, NOW, isStale), "crit");
});

test("🔴 a severity string can NEVER calm a loud percentage", () => {
  // Both spellings of the old hole: an explicitly-low severity, and no
  // severity row at all. Either one painted a green badge at 99% before.
  const low = record({ session: { utilization: 99 }, weekly: { utilization: 99 }, severity: "low" });
  assert.equal(S.toneForRecord(low, NOW, isStale), "crit", "'low' at 99%");

  const absent = record({ session: { utilization: 99 }, weekly: { utilization: 99 }, severity: null });
  assert.equal(S.toneForRecord(absent, NOW, isStale), "crit", "no severity row at 99%");

  const none = record({ session: { utilization: 85 }, weekly: { utilization: 85 }, severity: "none" });
  assert.equal(S.toneForRecord(none, NOW, isStale), "warn", "'none' at 85%");
});

test("stale outranks every other signal, however loud", () => {
  const old = record({
    session: { utilization: 99 }, weekly: { utilization: 99 },
    severity: "critical", asOf: NOW - 7 * 60 * 60 * 1000,
  });
  assert.equal(S.toneForRecord(old, NOW, isStale), "stale");

  const auth = record({ session: { utilization: 99 }, severity: "critical", staleSince: NOW - 1000 });
  assert.equal(S.toneForRecord(auth, NOW, isStale), "stale", "a 401/403 is stale even when fresh");
});

test("toneForRecord is total over garbage", () => {
  for (const bad of [null, undefined, "nonsense", 42, []]) {
    assert.equal(S.toneForRecord(bad, NOW, isStale), "stale",
      `${JSON.stringify(bad)} must not throw and must claim nothing about usage`);
  }
  // A record missing its window objects entirely (an older stored shape).
  assert.equal(S.toneForRecord({ asOf: NOW, staleSince: null, severity: null }, NOW, isStale),
    "unknown");
});

// --- the colour map ------------------------------------------------------------- //

test("every tone has a colour, and unknown tones fall back to amber not green", () => {
  const seen = new Set();
  for (const tone of S.TONE_ORDER) {
    const c = S.toneColor(tone);
    assert.match(c, /^#[0-9a-f]{6}$/, `${tone} -> ${c}`);
    seen.add(tone);
  }
  assert.deepEqual([...seen].sort(), ["crit", "ok", "stale", "unknown", "warn"]);
  assert.equal(S.toneColor("not_a_tone"), S.toneColor("unknown"),
    "an unmapped tone must not render as OK");
  assert.notEqual(S.toneColor("not_a_tone"), S.toneColor("ok"));
});

test("TONE_ORDER is ordered worst-first -- worstTone's correctness rests on it", () => {
  assert.deepEqual(S.TONE_ORDER, ["stale", "crit", "warn", "unknown", "ok"]);
  // Pin the property, not just the array: every tone must outrank the next.
  for (let i = 0; i < S.TONE_ORDER.length - 1; i += 1) {
    assert.equal(S.worstTone(S.TONE_ORDER[i], S.TONE_ORDER[i + 1]), S.TONE_ORDER[i],
      `${S.TONE_ORDER[i]} must outrank ${S.TONE_ORDER[i + 1]}`);
  }
});

test("the toast threshold IS the warn band, structurally", async () => {
  // Not "a test asserts these two numbers are equal" -- service_worker.js
  // assigns ALERT_THRESHOLD_PCT = WARN_PCT, so they cannot drift apart. This
  // pins that the assignment is still what it claims.
  globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;
  const SW = await import("../extension/service_worker.js");
  assert.equal(SW.ALERT_THRESHOLD_PCT, S.WARN_PCT);
});
