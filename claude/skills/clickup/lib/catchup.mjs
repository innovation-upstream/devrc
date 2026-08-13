/**
 * The webhook.site CATCH-UP path: stored requests in, deliveries out.
 *
 * WHY THIS IS A MODULE AND NOT A FUNCTION IN listen.mjs
 * ----------------------------------------------------
 * It was a function in listen.mjs, and it was CORRECT BY READING — it called
 * the same `authenticateDelivery()` predicate as the live POST path, and said so
 * in a comment. Nothing exercised it. The branch is gated on
 * `SINCE && MODE === 'wait'`, the integration test runs `--mode server`, so it
 * was structurally unreachable from the suite and two mutations that reinstate
 * the ORIGINAL defect passed all 73 tests:
 *
 *   * replacing the `authenticateDelivery(...)` call with
 *     `{ accepted: true, event: JSON.parse(raw) }` — i.e. deliver anything
 *     webhook.site stored, which is anything anyone POSTed to a public URL;
 *   * reading the signature from a header key that never matches, which rejects
 *     every genuine event instead (a silent outage rather than a hole).
 *
 * A comment is a claim. This module is the seam that lets the claim be tested.
 *
 * 🔴 An event read back out of webhook.site is EXACTLY as forgeable as one
 * POSTed to the local receiver: a webhook.site URL accepts a POST from anyone.
 * So catch-up runs the same predicate over the STORED RAW BODY (`req.content`),
 * which is the byte string ClickUp signed.
 */

import { authenticateDelivery, headerLookup } from './webhook-server.mjs';

/**
 * Rejection reasons that are PERMANENT, and therefore safe to move the cursor
 * past after recording the skip.
 *
 * 🔴 WHY THE CURSOR MAY ADVANCE AT ALL. `date_from` is the cursor and the API
 * is paged at 10, so before this the FIRST ten consecutive unverifiable stored
 * requests were a permanent blind spot: catch-up re-read the same ten every
 * run, printed ten stderr lines, and could never reach a genuine event behind
 * them. The realistic trigger is not an attacker — it is a lost or reset
 * `watchers.json` while the ClickUp webhooks keep firing, at which point EVERY
 * stored event is `unknown-webhook` and stays that way forever: the secrets
 * needed to verify them are gone.
 *
 * Each reason here is a decision this process can make from the request alone
 * and will never make differently later:
 *
 *   missing-signature  ClickUp signs every delivery; a stored request with no
 *                      X-Signature was not one, and no signature will appear.
 *   no-webhook-id      nothing identifies which secret would verify it, ever.
 *   unknown-webhook    no registered secret for that id — it is not from a
 *                      webhook this skill owns, or the registry that held the
 *                      secret is gone. Either way it is unverifiable forever.
 *   bad-signature      the body and the secret are both fixed; the digest will
 *                      not start matching.
 *
 * 🔴 `invalid-json` is deliberately NOT here, and neither is a stored record
 * with no usable `created_at`. Those are the cases where we cannot say WHAT we
 * would be skipping: we could not parse the body, or we have no timestamp to
 * move the cursor to. Fail closed — stop catch-up, leave the cursor where it
 * is, say so on stderr — rather than step silently over something that might
 * have been a real event. That trades the old "any ten unverifiable requests
 * wedge catch-up" for "one UNPARSEABLE request stops this run", which is both
 * far rarer (ClickUp sends JSON) and recoverable with an explicit `--since`.
 *
 * The cost of advancing is stated plainly rather than hidden: `date_from` has
 * one-second granularity, so moving the cursor past a rejected request also
 * moves it past anything stored in the SAME second. The live listener is
 * unaffected — it authenticates every delivery as it arrives.
 */
export const DECIDABLE_REJECTIONS = new Set([
  'missing-signature',
  'no-webhook-id',
  'unknown-webhook',
  'bad-signature',
]);

/**
 * Authenticate ONE request webhook.site stored.
 *
 * @param {object} req  a webhook.site stored request ({content, headers, created_at})
 * @param {(webhookId: string) => (string|null|undefined)} lookupSecret
 * @returns {{accepted: boolean, reason: string, decidable: boolean,
 *            createdAt: string|null, event?: object}}
 *   `decidable` answers ONE question: may the cursor move past this request?
 *   It is meaningless for an accepted event (the cursor moves with the event).
 */
export function classifyStoredRequest(req, lookupSecret) {
  if (!req || typeof req !== 'object') {
    return { accepted: false, reason: 'malformed-record', decidable: false, createdAt: null };
  }
  const createdAt =
    typeof req.created_at === 'string' && req.created_at.trim() !== '' ? req.created_at : null;
  const raw = typeof req.content === 'string' ? req.content : '';

  const result = authenticateDelivery(raw, headerLookup(req.headers, 'x-signature'), lookupSecret);

  if (result.accepted) {
    const event = result.event;
    if (createdAt) event._wh_created_at = createdAt;
    return { accepted: true, reason: result.reason, decidable: true, createdAt, event };
  }
  return {
    accepted: false,
    reason: result.reason,
    // No timestamp means nothing to advance the cursor TO, so a rejection we
    // could otherwise decide is still undecidable in practice.
    decidable: DECIDABLE_REJECTIONS.has(result.reason) && createdAt !== null,
    createdAt,
  };
}

/**
 * Walk the stored requests oldest-first, delivering the first authentic event
 * that passes the filters.
 *
 * Every side effect is a callback the caller owns — this module writes no
 * files, reads no config and knows nothing about the state dir.
 *
 * @param {object[]} requests
 * @param {object} deps
 * @param {(webhookId: string) => (string|null|undefined)} deps.lookupSecret
 * @param {(event: object) => boolean} [deps.matches]     the CLI filters
 * @param {(event: object) => object} deps.onAccepted     record it (log + cursor); returns the entry
 * @param {(entry: object, event: object) => void} deps.onDeliver   hand it to the agent; the walk stops
 * @param {(event: object) => void} [deps.onFiltered]     authentic, but not what we are waiting for
 * @param {(info: object) => void} [deps.onSkipped]       decidable rejection — advance the cursor
 * @param {(info: object) => void} [deps.onBlocked]       undecidable — the walk stops, cursor unchanged
 * @returns {{delivered: boolean, considered: number, skipped: number, filtered: number,
 *            blocked: object|null}}
 */
export function catchUp(requests, {
  lookupSecret,
  matches = () => true,
  onAccepted,
  onDeliver,
  onFiltered = () => {},
  onSkipped = () => {},
  onBlocked = () => {},
}) {
  let considered = 0;
  let skipped = 0;
  let filtered = 0;

  for (const req of requests || []) {
    considered += 1;
    const r = classifyStoredRequest(req, lookupSecret);

    if (r.accepted) {
      // The cursor advances for EVERY authentic event, matching or not —
      // otherwise a filtered event is re-read on every subsequent run.
      const entry = onAccepted(r.event);
      if (matches(r.event)) {
        onDeliver(entry, r.event);
        return { delivered: true, considered, skipped, filtered, blocked: null };
      }
      filtered += 1;
      onFiltered(r.event);
      continue;
    }

    if (!r.decidable) {
      // 🔴 Stop, do not continue. The cursor is a TIMESTAMP: advancing past a
      // later request advances past this one too, so "skip it and carry on"
      // would be the silent version of the thing we are refusing to do.
      onBlocked(r);
      return { delivered: false, considered, skipped, filtered, blocked: r };
    }

    skipped += 1;
    onSkipped(r);
  }

  return { delivered: false, considered, skipped, filtered, blocked: null };
}
