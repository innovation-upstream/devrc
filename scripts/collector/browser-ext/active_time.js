// active_time.js — PURE active-time accounting for the browser collector.
//
// Problem this solves: previously active_ms was `now - since` over the whole
// span a tab was the focused tab — INCLUDING time the browser was blurred or
// the user was idle/locked. A chrome://newtab "focused" for 22 min while the
// user was elsewhere logged 22 min of fake engagement. The harness
// per_host_hour_active_cap caught it (68.9 min "active" in a 42-min window).
//
// Fix: an accumulator that ONLY accrues wall-clock while the browser is focused
// AND the user is not idle/locked. Idle/blur banks the elapsed active time and
// PAUSES; active/focus resumes by re-anchoring `since` to now. When a tab
// change emits a nav event, take() returns banked + (active ? now-since : 0),
// clamped to a 1-hour cap, and resets for the next tab.
//
// PURE: no chrome.*, no Date.now() — every method takes `now` (ms epoch) from
// the caller. State is a plain serializable object so service_worker.js can
// persist it across MV3 worker suspension via chrome.storage.session.

// Belt-and-suspenders per-event cap: even an edge-case miss (e.g. a missed
// idle/blur transition) can never log an absurd active_ms.
export const ACTIVE_CAP_MS = 3_600_000; // 1 hour

// Fresh accumulator state.
//   banked: ms of active time accrued and "banked" (paused spans summed)
//   since:  epoch ms anchor of the in-progress active span, or null if paused
//           (browser blurred or user idle/locked)
export function freshState(now = 0, active = false) {
  return { banked: 0, since: active ? now : null };
}

// Normalize an arbitrary stored object back into a valid state (defensive
// against a partially-written / legacy chrome.storage.session blob).
export function fromStored(o) {
  if (!o || typeof o !== "object") return freshState();
  const banked = Number.isFinite(o.banked) ? Math.max(0, o.banked) : 0;
  const since = Number.isFinite(o.since) ? o.since : null;
  return { banked, since };
}

// Currently accruing? (true between an onActive and the next onIdle/onBlur)
export function isActive(state) {
  return state.since !== null;
}

// Transition to active/focused: start (or continue) accruing. If already
// accruing this is a no-op — we must NOT reset `since` (that would drop the
// in-progress span). Idempotent so duplicate active/focus signals are safe.
export function onActive(state, now) {
  if (state.since === null) state.since = now;
  return state;
}

// Transition to idle / locked / blurred: bank the in-progress span and pause.
// Idempotent: if already paused, nothing to bank.
export function onInactive(state, now) {
  if (state.since !== null) {
    state.banked += Math.max(0, now - state.since);
    state.since = null;
  }
  return state;
}

// Aliases for the two inactive causes — same accounting, clearer call sites.
export const onIdle = onInactive;
export const onBlur = onInactive;

// Current accumulated active ms WITHOUT mutating (banked + in-progress span),
// uncapped — useful for inspection/tests.
export function activeMs(state, now) {
  const live = state.since !== null ? Math.max(0, now - state.since) : 0;
  return state.banked + live;
}

// Bank everything up to `now`, return the capped active_ms, and RESET the
// accumulator for the next tab. If still active at `now`, the new span is
// re-anchored to `now` (so continuous engagement across a tab switch keeps
// accruing); if paused, it stays paused.
export function take(state, now) {
  const total = activeMs(state, now);
  const capped = Math.min(ACTIVE_CAP_MS, Math.max(0, Math.round(total)));
  const stillActive = state.since !== null;
  state.banked = 0;
  state.since = stillActive ? now : null;
  return capped;
}
