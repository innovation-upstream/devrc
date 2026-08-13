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
 * Rejection reasons that are PERMANENT: decided from the STORED BYTES alone,
 * and never decided differently on a later run.
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
 * Each reason here is a property of a record that no longer changes:
 *
 *   missing-signature  ClickUp signs every delivery; a stored request with no
 *                      X-Signature was not one, and no signature will appear.
 *   no-webhook-id      nothing identifies which secret would verify it, ever.
 *   unknown-webhook    no registered secret for that id — it is not from a
 *                      webhook this skill owns, or the registry that held the
 *                      secret is gone. Either way it is unverifiable forever.
 *   bad-signature      the body and the secret are both fixed; the digest will
 *                      not start matching.
 *   invalid-json       the stored body is not a JSON object, and stored bytes
 *                      do not change: it will not start being one.
 *   malformed-record   the API returned something that is not a request object.
 *
 * 🔴 `invalid-json` IS ON THIS LIST, and the first version of this file is why.
 * It made `invalid-json` undecidable — stop the walk, leave the cursor — on the
 * reasoning that "we could not parse it, so we cannot say what we are skipping".
 * That reasoning does not survive contact with the input: webhook.site records
 * an empty `content` for a bare **GET**, which is what a crawler, a link
 * preview, or the user opening their own webhook URL in a browser produces. One
 * such request — plus a `null` content, a JSON scalar, HTML, or a form-encoded
 * body — parked the cursor permanently, because `SINCE` defaults to
 * `last-seen.txt` and the same page re-blocks on every subsequent run until
 * somebody edits state by hand. Measured: a page holding [bare GET, genuine
 * signed event] delivered NOTHING on two consecutive runs, where the code
 * before it delivered the genuine event on run 1. It replaced a rare
 * ten-request wedge with a permanent ONE-request wedge that anyone who can
 * reach the URL can trigger — and the premise of the whole file is that anyone
 * can. It was also internally inconsistent: `bad-signature` was decidable
 * because "the body and the secret are both fixed", and a body that is not JSON
 * is exactly as fixed.
 *
 * 🔴 WHAT IS ACTUALLY UNDECIDABLE IS AN **UNDATABLE** RECORD. `decidable` means
 * one thing — may the cursor move past this? — and the cursor is a TIMESTAMP.
 * A record with no usable `created_at` gives nothing to move the cursor TO, so
 * advancing would be guessing; that, and only that, stops the walk. It cannot
 * be triggered by the body of a request: webhook.site stamps what it stores.
 *
 * The cost of advancing is stated plainly rather than hidden: `date_from` has
 * one-second granularity, so moving the cursor past a rejected request also
 * moves it past anything stored in the SAME second. The live listener is
 * unaffected — it authenticates every delivery as it arrives.
 *
 * This set is a LEDGER, checked both ways by the tests: a reason
 * `authenticateDelivery()` can return that is missing here defaults to
 * undecidable — i.e. to the wedge above — so growth has to be deliberate.
 */
export const PERMANENT_REJECTIONS = new Set([
  'missing-signature',
  'no-webhook-id',
  'unknown-webhook',
  'bad-signature',
  'invalid-json',
  'malformed-record',
]);

/**
 * May the cursor move past a REJECTED stored request?
 *
 * Exported so the fail-closed default is reachable from a test: no reason the
 * current authenticator returns is unlisted, so the `PERMANENT_REJECTIONS.has()`
 * clause has no observable effect until one is — which is precisely when it
 * matters. Deleting it is otherwise a mutation nothing can catch.
 *
 * @param {string} reason
 * @param {string|null} createdAt  a usable timestamp, or null
 */
export function isDecidable(reason, createdAt) {
  return PERMANENT_REJECTIONS.has(reason) && createdAt !== null;
}

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
  const isRecord = !!req && typeof req === 'object';
  const createdAt =
    isRecord && typeof req.created_at === 'string' && req.created_at.trim() !== ''
      ? req.created_at
      : null;

  // ONE expression for decidability, used by every rejection path: a permanent
  // reason AND something to move the cursor to. Written once rather than per
  // branch so a case cannot quietly acquire different rules — the previous
  // version's `invalid-json` wedge was exactly that kind of special case.
  const reject = (reason) => ({
    accepted: false,
    reason,
    decidable: isDecidable(reason, createdAt),
    createdAt,
  });

  // A non-object has no `created_at` to read, so this is undecidable in
  // practice today. It goes through the same expression anyway: if the API ever
  // returns a datable record shape this module does not recognise, the walk
  // should skip it, not wedge on it.
  if (!isRecord) return reject('malformed-record');

  const raw = typeof req.content === 'string' ? req.content : '';
  const result = authenticateDelivery(raw, headerLookup(req.headers, 'x-signature'), lookupSecret);

  if (result.accepted) {
    const event = result.event;
    if (createdAt) event._wh_created_at = createdAt;
    return { accepted: true, reason: result.reason, decidable: true, createdAt, event };
  }
  return reject(result.reason);
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
 * @param {(info: object) => void} [deps.onBlocked]       UNDATABLE — the walk stops, cursor unchanged
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
      // 🔴 Stop, do not continue. Reaching here means the record is UNDATABLE
      // (see PERMANENT_REJECTIONS): there is no timestamp to move the cursor
      // to. Continuing would deliver a LATER event, and because the cursor is a
      // TIMESTAMP that advances past this record anyway — the silent version of
      // the thing we are refusing to do. This is not reachable from the BODY of
      // a stored request; a body we cannot parse is skipped, not blocking.
      onBlocked(r);
      return { delivered: false, considered, skipped, filtered, blocked: r };
    }

    skipped += 1;
    onSkipped(r);
  }

  return { delivered: false, considered, skipped, filtered, blocked: null };
}
