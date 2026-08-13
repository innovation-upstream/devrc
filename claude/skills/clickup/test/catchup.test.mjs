#!/usr/bin/env node

/**
 * The webhook.site CATCH-UP path (hermetic — no token, no network, no state dir).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The catch-up path claimed, in a comment, that it authenticates stored events
 * with the same predicate as the live POST path. Nothing exercised it: the
 * branch is gated on `SINCE && MODE === 'wait'` and the integration test runs
 * `--mode server`, so it was structurally unreachable from the suite. Two
 * mutations that reinstate the ORIGINAL defect passed all 73 tests:
 *
 *   1. `authenticateDelivery(...)` -> `{ accepted: true, event: JSON.parse(raw) }`
 *      — deliver whatever webhook.site stored, which is whatever anyone POSTed
 *      to a public URL. This is the whole bug #438 existed to fix, one function
 *      over.
 *   2. reading the signature from a header key that never matches — the
 *      opposite failure: every genuine event rejected, silently, forever.
 *
 * So this runs THE SAME FOUR CASES the live path is held to
 * (webhook-server.test.mjs "the HTTP receiver"): unsigned, wrongly-signed,
 * unregistered-webhook, correctly-signed — through the catch-up seam, with the
 * side effects recorded. Plus the CURSOR policy, which is the second defect in
 * #444: ten consecutive unverifiable stored requests used to be a permanent
 * blind spot.
 *
 * Usage:
 *   node --test test/catchup.test.mjs
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { signBody, forwarderArgs, forwarderTarget, LOOPBACK_HOST } from '../lib/webhook-server.mjs';
import { catchUp, classifyStoredRequest, DECIDABLE_REJECTIONS } from '../lib/catchup.mjs';

const WEBHOOK_ID = 'wh-fixture-0001';
const SECRET = 'fixture-secret-not-a-real-clickup-signing-key';
const OTHER_SECRET = 'a-different-fixture-secret';
const secrets = { [WEBHOOK_ID]: SECRET };
const lookupSecret = (id) => secrets[id] || null;

const EVENT = { event: 'taskStatusUpdated', task_id: '868fixture', webhook_id: WEBHOOK_ID };
const BODY = JSON.stringify(EVENT);
const GOOD_SIG = signBody(BODY, SECRET);

/** A webhook.site stored request, in the shape its API returns one. */
function stored(content, signature, created_at = '2026-08-13 10:00:00') {
  const req = { uuid: 'fixture-uuid', content, created_at, headers: {} };
  // webhook.site returns header VALUES AS ARRAYS, and echoes the case the
  // sender used. Both are why the header is read through a helper.
  if (signature !== undefined) req.headers['x-signature'] = [signature];
  return req;
}

/** Run the whole walk, recording every side effect it asks the caller to make. */
function run(requests, { matches = () => true, lookup = lookupSecret } = {}) {
  const log = [];       // what would be appended to webhooks.jsonl
  const delivered = []; // what would be printed for the agent
  const filtered = [];
  const cursor = [];    // every last-seen.txt write, in order
  const blocked = [];
  const outcome = catchUp(requests, {
    lookupSecret: lookup,
    matches,
    onAccepted: (event) => {
      log.push(event);
      cursor.push(event._wh_created_at || null);
      return { entry: true, ...event };
    },
    onDeliver: (entry, event) => delivered.push(event),
    onFiltered: (event) => filtered.push(event),
    onSkipped: (info) => cursor.push(info.createdAt),
    onBlocked: (info) => blocked.push(info),
  });
  return { outcome, log, delivered, filtered, cursor, blocked };
}

// ── 1. THE FOUR CASES, the same ones the live path is held to ─────────────

describe('the catch-up path authenticates exactly like the live path', () => {
  test('🔴 an UNSIGNED stored request is rejected — nothing logged, nothing delivered', () => {
    const r = run([stored(BODY, undefined)]);
    assert.equal(r.delivered.length, 0,
      'a stored request with NO signature was delivered to the agent. A webhook.site URL ' +
        'accepts a POST from anyone, so this is the forged-event path in full.');
    assert.equal(r.log.length, 0, 'a rejected stored request was written to webhooks.jsonl');
    assert.equal(r.outcome.skipped, 1);
  });

  test('🔴 a WRONGLY-signed stored request is rejected', () => {
    const r = run([stored(BODY, signBody(BODY, OTHER_SECRET))]);
    assert.equal(r.delivered.length, 0, 'a forged stored request was delivered to the agent');
    assert.equal(r.log.length, 0);
  });

  test('🔴 a stored request from an UNREGISTERED webhook is rejected', () => {
    const body = JSON.stringify({ ...EVENT, webhook_id: 'wh-never-registered' });
    const r = run([stored(body, signBody(body, SECRET))]);
    assert.equal(r.delivered.length, 0,
      'an event from a webhook with no registered secret was delivered — that is the ' +
        'unverifiable case, and the agent acts on the payload');
  });

  test('a CORRECTLY-signed stored request IS delivered (the check is not just "reject")', () => {
    const r = run([stored(BODY, GOOD_SIG)]);
    assert.equal(r.delivered.length, 1,
      'a correctly signed stored event was not delivered — hardening that rejects ' +
        'everything is an outage, not hardening');
    assert.equal(r.delivered[0].task_id, '868fixture');
    assert.equal(r.log.length, 1, 'the delivered event was not recorded');
  });

  test('the signature is checked against the STORED RAW BODY, not a re-serialisation', () => {
    // Pretty-printed on purpose: whitespace is the property a JSON round-trip
    // does NOT preserve, so this is what tells "verified req.content" apart from
    // "verified JSON.stringify(JSON.parse(req.content))". ClickUp signs the bytes.
    const pretty = JSON.stringify(EVENT, null, 2);
    assert.notEqual(pretty, JSON.stringify(JSON.parse(pretty)), 'fixture precondition');

    const wrong = run([stored(pretty, signBody(JSON.stringify(JSON.parse(pretty)), SECRET))]);
    assert.equal(wrong.delivered.length, 0,
      'a signature over a RE-SERIALISED body was accepted — the catch-up path is verifying ' +
        'its own reconstruction of the payload, not what the sender signed');

    const right = run([stored(pretty, signBody(pretty, SECRET))]);
    assert.equal(right.delivered.length, 1, 'a signature over the exact stored bytes was rejected');
  });

  test('a stored request whose body is not JSON is never delivered', () => {
    const r = run([stored('not json at all', GOOD_SIG)]);
    assert.equal(r.delivered.length, 0);
    assert.equal(r.log.length, 0);
  });

  test('POSITIVE + NEGATIVE control in one: the same stored request flips on the secret alone', () => {
    const good = run([stored(BODY, GOOD_SIG)]);
    const bad = run([stored(BODY, GOOD_SIG)], { lookup: () => OTHER_SECRET });
    assert.equal(good.delivered.length, 1);
    assert.equal(bad.delivered.length, 0,
      'the catch-up path delivered the same body under a DIFFERENT secret — it is not ' +
        'verifying anything');
  });

  test('the header is read case-INSENSITIVELY (webhook.site echoes the sender\'s case)', () => {
    // Both spellings must work. That is what makes a hard-coded bracket access —
    // in EITHER case — a mutant that dies: one of these two goes red.
    const lower = { uuid: 'u', content: BODY, created_at: '2026-08-13 10:00:00',
      headers: { 'x-signature': [GOOD_SIG] } };
    const upper = { uuid: 'u', content: BODY, created_at: '2026-08-13 10:00:00',
      headers: { 'X-Signature': GOOD_SIG } };
    assert.equal(run([lower]).delivered.length, 1,
      'a lowercase x-signature header was not read — every stored event rejects and ' +
        'catch-up silently delivers nothing');
    assert.equal(run([upper]).delivered.length, 1,
      'an X-Signature header was not read — same silent outage, other spelling');
  });
});

// ── 2. The CURSOR: the permanent blind spot ───────────────────────────────

describe('the cursor advances past decidable rejections and stops at undecidable ones', () => {
  test('🔴 ten consecutive unverifiable stored requests no longer wedge catch-up', () => {
    // The realistic trigger is a lost or reset watchers.json while the ClickUp
    // webhooks keep firing: EVERY stored event is unknown-webhook, per_page is
    // 10, and date_from is the cursor — so before this the first ten blocked
    // catch-up forever and a genuine event behind them was unreachable.
    const junk = [];
    for (let i = 0; i < 10; i++) {
      const body = JSON.stringify({ ...EVENT, webhook_id: `wh-gone-${i}` });
      junk.push(stored(body, signBody(body, SECRET), `2026-08-13 10:00:${String(i).padStart(2, '0')}`));
    }
    const real = stored(BODY, GOOD_SIG, '2026-08-13 10:00:10');

    const r = run([...junk, real]);
    assert.equal(r.delivered.length, 1,
      'a genuine event behind ten unverifiable stored requests was never reached — that is ' +
        'the permanent blind spot: the cursor could not move past them, so every subsequent ' +
        'run re-read the same ten');
    assert.equal(r.outcome.skipped, 10);
    assert.equal(r.log.length, 1, 'a rejected stored request reached the log');
    assert.deepEqual(
      r.cursor,
      [...junk.map((q) => q.created_at), '2026-08-13 10:00:10'],
      'the cursor did not advance once per skipped request, in order');
  });

  test('a skipped request advances the cursor to ITS timestamp and logs NOTHING', () => {
    const r = run([stored('{"webhook_id":"wh-gone"}', 'deadbeef', '2026-08-13 11:22:33')]);
    assert.deepEqual(r.cursor, ['2026-08-13 11:22:33'],
      'the cursor did not advance past a permanently unverifiable request');
    assert.equal(r.log.length, 0,
      'a rejected body was written to the log — rejected bodies are attacker-controlled');
  });

  test('🔴 an UNPARSEABLE request STOPS the walk and does NOT move the cursor', () => {
    const r = run([
      stored('<html>not a webhook at all</html>', GOOD_SIG, '2026-08-13 10:00:00'),
      stored(BODY, GOOD_SIG, '2026-08-13 10:00:05'),
    ]);
    assert.equal(r.blocked.length, 1, 'an unparseable stored request did not block the walk');
    assert.equal(r.blocked[0].reason, 'invalid-json');
    assert.deepEqual(r.cursor, [],
      'the cursor moved past a request we could not parse — that is the silent version of ' +
        'skipping a possibly-genuine event');
    assert.equal(r.delivered.length, 0,
      'the walk continued past the blocked request; because the cursor is a TIMESTAMP, ' +
        'delivering a LATER event advances past the unparseable one anyway');
    assert.equal(r.outcome.considered, 1, 'the walk examined requests behind the blocker');
  });

  test('a rejection with NO usable timestamp is undecidable — nothing to advance TO', () => {
    for (const createdAt of ['', '   ', undefined]) {
      const r = run([{ content: BODY, headers: {}, created_at: createdAt }]);
      assert.equal(r.blocked.length, 1,
        `created_at=${JSON.stringify(createdAt)}: a rejection with no timestamp was treated ` +
          'as decidable, so the caller would advance the cursor to nothing');
      assert.deepEqual(r.cursor, []);
    }
  });

  test('a non-object stored record is undecidable, not a crash', () => {
    for (const junk of [null, 'a string', 42]) {
      let r;
      assert.doesNotThrow(() => { r = run([junk]); });
      assert.equal(r.blocked[0].reason, 'malformed-record');
      assert.deepEqual(r.cursor, []);
    }
  });

  test('the decidable set is exactly the PERMANENT rejections', () => {
    // Pinned as a set, not spot-checked: adding a reason here is a decision to
    // let the cursor skip past that case, and it should be deliberate.
    assert.deepEqual([...DECIDABLE_REJECTIONS].sort(),
      ['bad-signature', 'missing-signature', 'no-webhook-id', 'unknown-webhook']);
    assert.ok(!DECIDABLE_REJECTIONS.has('invalid-json'),
      'invalid-json was made decidable — we could not parse the body, so we cannot say what ' +
        'the cursor would be stepping over');
  });

  test('classifyStoredRequest reports decidability per reason', () => {
    const cases = [
      ['missing-signature', stored(BODY, undefined), true],
      ['bad-signature', stored(BODY, signBody(BODY, OTHER_SECRET)), true],
      ['no-webhook-id', stored(JSON.stringify({ event: 'x' }), 'sig'), true],
      ['unknown-webhook', (() => {
        const b = JSON.stringify({ ...EVENT, webhook_id: 'wh-nope' });
        return stored(b, signBody(b, SECRET));
      })(), true],
      ['invalid-json', stored('{{{', 'sig'), false],
    ];
    for (const [reason, req, decidable] of cases) {
      const r = classifyStoredRequest(req, lookupSecret);
      assert.equal(r.accepted, false, `${reason}: expected a rejection`);
      assert.equal(r.reason, reason, `expected reason ${reason}, got ${r.reason}`);
      assert.equal(r.decidable, decidable,
        `${reason}: decidable=${r.decidable}, expected ${decidable}`);
    }
  });

  test('an accepted event carries the stored timestamp as the cursor value', () => {
    const r = classifyStoredRequest(stored(BODY, GOOD_SIG, '2026-08-13 09:08:07'), lookupSecret);
    assert.equal(r.accepted, true, `expected accepted, got ${r.reason}`);
    assert.equal(r.event._wh_created_at, '2026-08-13 09:08:07',
      'the accepted event lost its webhook.site timestamp, so the cursor would advance to ' +
        'the RECEIVE time instead — re-reading everything in between on the next run');
  });
});

// ── 3. Filters do not weaken the cursor ───────────────────────────────────

describe('filters', () => {
  test('an authentic but FILTERED event still advances the cursor and is not delivered', () => {
    const r = run([stored(BODY, GOOD_SIG, '2026-08-13 12:00:00')],
      { matches: () => false });
    assert.equal(r.delivered.length, 0);
    assert.equal(r.filtered.length, 1);
    assert.deepEqual(r.cursor, ['2026-08-13 12:00:00'],
      'a filtered event did not advance the cursor, so every later run re-reads it');
  });

  test('the walk stops at the FIRST matching event and reports what it examined', () => {
    const second = stored(BODY, GOOD_SIG, '2026-08-13 12:00:02');
    const r = run([stored(BODY, GOOD_SIG, '2026-08-13 12:00:01'), second]);
    assert.equal(r.delivered.length, 1);
    assert.equal(r.outcome.considered, 1,
      'the walk kept going after delivering — the process exits there, and a second ' +
        'delivery would overwrite webhook-latest.json on the way out');
  });
});

// ── 4. The forwarder targets what the receiver BINDS ──────────────────────

describe('the whcli forwarder target', () => {
  test('🔴 the target is the literal 127.0.0.1, never `localhost`', () => {
    const args = forwarderArgs({ token: 'fixture-token', port: 3458 });
    const target = args.find((a) => a.startsWith('--target='));
    assert.equal(target, '--target=http://127.0.0.1:3458',
      `whcli would forward to ${target}. \`localhost\` resolves through the host's name ` +
        'lookup and can yield ::1 first; the receiver binds 127.0.0.1 ONLY, so every ' +
        'delivery would be refused by a listener that reports healthy.');
    assert.ok(!args.some((a) => a.includes('localhost')), `\`localhost\` in ${args.join(' ')}`);
  });

  test('the target and the bind address are the SAME constant, not two literals', () => {
    assert.equal(forwarderTarget(1234), `http://${LOOPBACK_HOST}:1234`,
      'the forwarder target stopped being derived from LOOPBACK_HOST — the two can now ' +
        'drift, which is the whole failure mode');
    assert.equal(LOOPBACK_HOST, '127.0.0.1');
  });

  test('the port is the one passed (the BOUND port, not the requested one)', () => {
    assert.ok(forwarderArgs({ token: 't', port: 45678 }).includes('--target=http://127.0.0.1:45678'));
  });

  test('the api key is included only when there is one', () => {
    assert.ok(!forwarderArgs({ token: 't', port: 1 }).some((a) => a.startsWith('--api-key=')));
    assert.ok(forwarderArgs({ token: 't', port: 1, apiKey: 'k' }).includes('--api-key=k'));
  });
});
