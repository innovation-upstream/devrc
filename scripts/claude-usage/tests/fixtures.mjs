// Synthetic fixtures shared by the claude-usage suites.
//
// EVERY value in here is invented: fake org uuids, example.com emails,
// invented percentages. The proposal's recon section describes the response
// SCHEMA; nothing here is a capture (gated by test_no_captured_text.py like
// everything else in this public repo).

export const ORG_A = "11111111-1111-4111-8111-111111111111";
export const ORG_B = "22222222-2222-4222-8222-222222222222";
export const ORG_C = "33333333-3333-4333-8333-333333333333";
export const ORG_D = "44444444-4444-4444-8444-444444444444";
export const NAME_A = "user@example.com's Organization";
export const NAME_B = "Work Org <work@example.com>";
export const NAME_C = "third@example.com's Organization";
export const NAME_D = "fourth@example.com's Organization";

const ISO = "2026-09-19T18:00:00Z";

/** A full-schema usage payload: every recon'd field present and non-null. */
export function fullUsage(overrides = {}) {
  return {
    five_hour: { utilization: 9, resets_at: ISO, locked_reason: null },
    seven_day: { utilization: 47, resets_at: "2026-09-24T00:00:00Z" },
    seven_day_opus: { utilization: 12, resets_at: "2026-09-24T00:00:00Z" },
    seven_day_sonnet: null,
    nimbus_quill: null,
    limits: [
      { kind: "session", percent: 9, severity: "low", resets_at: ISO, is_active: true },
      { kind: "weekly_all", percent: 47, severity: "medium", resets_at: "2026-09-24T00:00:00Z", is_active: true },
      {
        kind: "weekly_scoped", percent: 12, severity: "low",
        resets_at: "2026-09-24T00:00:00Z", is_active: false,
        scope: { model: { display_name: "Opus" } },
      },
    ],
    extra_usage: {
      is_enabled: true, monthly_limit: 10000, used_credits: 2500,
      currency: "USD", disabled_reason: null,
    },
    seven_day_breakdown: {
      rows: [
        { key: "chat", display_name: "Chat", percent: 60 },
        { key: "claude_code", display_name: "Claude Code", percent: 31 },
      ],
    },
    ...overrides,
  };
}

/** The all-nulls variant: every recon-observed null field, actually null. */
export function nullUsage() {
  return {
    five_hour: { utilization: null, resets_at: null, locked_reason: null },
    seven_day: { utilization: null, resets_at: null },
    seven_day_opus: null,
    seven_day_sonnet: null,
    nimbus_quill: null,
    limits: null,
    extra_usage: {
      is_enabled: null, monthly_limit: null, used_credits: null,
      currency: null, disabled_reason: null,
    },
    seven_day_breakdown: null,
  };
}

export const NOW = Date.parse("2026-09-19T13:00:00Z");
