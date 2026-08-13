#!/usr/bin/env node

/**
 * Webhook receiver gate (hermetic — no credentials, no ClickUp, no network
 * beyond 127.0.0.1).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * listen.mjs had ZERO tests. That is not a coincidence next to its four
 * defects — it is the cause. A script that reads config at module load and
 * `process.exit(1)`s without a token cannot be imported, so nothing in it was
 * reachable from a test, so nothing in it was ever exercised:
 *
 *   1. the signature was computed into a `verified` FIELD that no branch read.
 *      A forged or unsigned POST was 200-acked, appended to webhooks.jsonl,
 *      advanced the last-seen cursor, and was printed as the payload an agent
 *      then acts on. A field in a DTO is not a guard.
 *   2. `server.listen(PORT)` bound 0.0.0.0 — the LAN and nebula addresses too.
 *   3. an unauthenticated GET returned `webhook_url`, i.e. the webhook.site
 *      capability token.
 *   4. the digest comparison was `===`.
 *
 * The handling moved to lib/webhook-server.mjs so all four are testable. These
 * are the BEHAVIOURAL checks — request in, response and side effects out. The
 * companion listen-integration.test.mjs runs the real listen.mjs process,
 * because a correct library wired up wrongly is the seam defect this pair
 * exists to close.
 *
 * Usage:
 *   node --test test/webhook-server.test.mjs
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import http from 'http';
import crypto from 'crypto';
import {
  LOOPBACK_HOST,
  safeEqualHex,
  signBody,
  headerValue,
  authenticateDelivery,
  createWebhookServer,
  listenLoopback,
} from '../lib/webhook-server.mjs';

const WEBHOOK_ID = 'wh-fixture-0001';
const SECRET = 'fixture-secret-not-a-real-clickup-signing-key';
const OTHER_SECRET = 'a-different-fixture-secret';
const secrets = { [WEBHOOK_ID]: SECRET };
const lookupSecret = (id) => secrets[id] || null;

const EVENT = { event: 'taskStatusUpdated', task_id: '868fixture', webhook_id: WEBHOOK_ID };
const BODY = JSON.stringify(EVENT);
const GOOD_SIG = signBody(BODY, SECRET);

// ── 1. The digest comparison ──────────────────────────────────────────────

describe('safeEqualHex', () => {
  test('equal digests compare equal', () => {
    assert.equal(safeEqualHex(GOOD_SIG, signBody(BODY, SECRET)), true);
  });

  test('a same-length but different digest compares unequal', () => {
    const other = signBody(BODY, OTHER_SECRET);
    assert.equal(other.length, GOOD_SIG.length, 'fixture: both are sha256 hex');
    assert.notEqual(other, GOOD_SIG, 'fixture: the two digests must differ');
    assert.equal(safeEqualHex(GOOD_SIG, other), false);
  });

  test('UNEQUAL LENGTHS return false instead of throwing', () => {
    // crypto.timingSafeEqual THROWS on a length mismatch. A truncated
    // signature is the cheapest thing an attacker sends, and an uncaught
    // throw inside the request handler is a 500 at best.
    assert.doesNotThrow(() => safeEqualHex(GOOD_SIG, GOOD_SIG.slice(0, 10)));
    assert.equal(safeEqualHex(GOOD_SIG, GOOD_SIG.slice(0, 10)), false);
    assert.equal(safeEqualHex(GOOD_SIG, ''), false);
  });

  test('non-string input returns false instead of throwing', () => {
    assert.doesNotThrow(() => safeEqualHex(GOOD_SIG, undefined));
    assert.equal(safeEqualHex(GOOD_SIG, undefined), false);
    assert.equal(safeEqualHex(undefined, GOOD_SIG), false);
    assert.equal(safeEqualHex(GOOD_SIG, { toString: () => GOOD_SIG }), false);
  });

  test('it is crypto.timingSafeEqual, not ===', () => {
    // A behavioural tell rather than a spelling check: `===` on two equal
    // strings and timingSafeEqual on two equal buffers agree, so the only
    // observable difference is that timingSafeEqual exists and is called.
    // Break it and this goes red.
    const calls = [];
    const real = crypto.timingSafeEqual;
    crypto.timingSafeEqual = (a, b) => { calls.push([a, b]); return real(a, b); };
    try {
      safeEqualHex(GOOD_SIG, GOOD_SIG);
    } finally {
      crypto.timingSafeEqual = real;
    }
    assert.equal(calls.length, 1,
      'safeEqualHex() did not call crypto.timingSafeEqual — a `===` comparison of an ' +
        'HMAC leaks the digest one byte at a time to anyone who can time the response');
  });
});

// ── 2. The authentication predicate ───────────────────────────────────────

describe('authenticateDelivery', () => {
  test('a correctly signed delivery is ACCEPTED', () => {
    const r = authenticateDelivery(BODY, GOOD_SIG, lookupSecret);
    assert.equal(r.accepted, true, `expected accepted, got reason=${r.reason}`);
    assert.equal(r.reason, 'verified');
    assert.equal(r.event.task_id, '868fixture');
  });

  test('a MISSING signature is rejected 401', () => {
    const r = authenticateDelivery(BODY, undefined, lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'missing-signature');
    assert.equal(r.status, 401);
  });

  test('an empty signature header is rejected 401', () => {
    const r = authenticateDelivery(BODY, '', lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'missing-signature');
  });

  test('a WRONG signature is rejected 401', () => {
    const r = authenticateDelivery(BODY, signBody(BODY, OTHER_SECRET), lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'bad-signature');
    assert.equal(r.status, 401);
  });

  test('a signature over a DIFFERENT body is rejected (replay with edited payload)', () => {
    const tampered = JSON.stringify({ ...EVENT, task_id: '868attacker' });
    const r = authenticateDelivery(tampered, GOOD_SIG, lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'bad-signature');
  });

  test('a TRUNCATED signature is rejected, not an exception', () => {
    let r;
    assert.doesNotThrow(() => {
      r = authenticateDelivery(BODY, GOOD_SIG.slice(0, 8), lookupSecret);
    });
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'bad-signature');
  });

  test('🔴 an UNKNOWN webhook id is rejected — unverifiable is not "skip the check"', () => {
    // This is the `verified === null` case. The old code logged it as
    // 'skipped' and delivered the event anyway; every webhook this skill
    // creates records its secret at creation time, so "no secret" means the
    // event is not from a webhook this skill owns.
    const body = JSON.stringify({ ...EVENT, webhook_id: 'wh-never-registered' });
    const r = authenticateDelivery(body, signBody(body, SECRET), lookupSecret);
    assert.equal(r.accepted, false,
      'an event from a webhook with NO registered secret was accepted — that is the ' +
        'unverifiable case, and accepting it means anyone who can reach the port can ' +
        'make the agent act on a payload they wrote');
    assert.equal(r.reason, 'unknown-webhook');
  });

  test('🔴 an event with NO webhook_id is rejected', () => {
    const body = JSON.stringify({ event: 'taskStatusUpdated', task_id: '1' });
    const r = authenticateDelivery(body, 'anything', lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'no-webhook-id');
  });

  test('a non-JSON body is rejected 400', () => {
    const r = authenticateDelivery('not json at all', GOOD_SIG, lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'invalid-json');
    assert.equal(r.status, 400);
  });

  test('a JSON scalar body is rejected 400 (not treated as an event object)', () => {
    const r = authenticateDelivery('"a string"', GOOD_SIG, lookupSecret);
    assert.equal(r.accepted, false);
    assert.equal(r.reason, 'invalid-json');
  });

  test('a signature arriving as an ARRAY header is understood', () => {
    // webhook.site's stored-request API returns headers as arrays; node's http
    // gives a string. The catch-up path and the live path share this predicate,
    // so it has to read both.
    assert.equal(headerValue([GOOD_SIG]), GOOD_SIG);
    assert.equal(headerValue(GOOD_SIG), GOOD_SIG);
    assert.equal(headerValue(undefined), undefined);
    const r = authenticateDelivery(BODY, [GOOD_SIG], lookupSecret);
    assert.equal(r.accepted, true, `expected accepted, got reason=${r.reason}`);
  });

  test('POSITIVE + NEGATIVE control in one: the same body flips on the secret alone', () => {
    const good = authenticateDelivery(BODY, GOOD_SIG, lookupSecret);
    const bad = authenticateDelivery(BODY, GOOD_SIG, () => OTHER_SECRET);
    assert.equal(good.accepted, true);
    assert.equal(bad.accepted, false,
      'the predicate accepted the same body under a DIFFERENT secret — it is not ' +
        'actually verifying anything');
  });
});

// ── 3. The HTTP surface, over a real loopback server ──────────────────────

function request(port, { method = 'GET', body, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { host: LOOPBACK_HOST, port, method, path: '/', headers },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => resolve({ status: res.statusCode, body: data }));
      }
    );
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

/** Start a server with recording callbacks, run `fn`, always close. */
async function withServer(fn, overrides = {}) {
  const accepted = [];
  const rejected = [];
  const server = createWebhookServer({
    lookupSecret,
    onAccepted: (event, raw) => accepted.push({ event, raw }),
    onRejected: (result, raw) => rejected.push({ result, raw }),
    ...overrides,
  });
  const addr = await listenLoopback(server, 0);
  try {
    return await fn({ port: addr.port, addr, accepted, rejected });
  } finally {
    await new Promise((r) => server.close(r));
  }
}

describe('the HTTP receiver', () => {
  test('🔴 binds 127.0.0.1, not every interface', async () => {
    await withServer(({ addr }) => {
      assert.equal(addr.address, LOOPBACK_HOST,
        `the receiver bound ${addr.address} — 0.0.0.0/:: makes a webhook sidecar ` +
          'reachable from the LAN and from nebula. It must bind loopback.');
    });
  });

  test('🔴 GET does not disclose webhook_url', async () => {
    await withServer(async ({ port }) => {
      const res = await request(port);
      assert.equal(res.status, 200);
      const json = JSON.parse(res.body);
      assert.equal(json.status, 'listening');
      assert.ok(!('webhook_url' in json),
        `GET returned webhook_url (${res.body}). That URL embeds the webhook.site ` +
          'capability token, and this endpoint is unauthenticated by construction.');
      assert.ok(!/webhook\.site/.test(res.body),
        `GET leaked a webhook.site URL in its body: ${res.body}`);
    });
  });

  test('🔴 an UNSIGNED POST is rejected, and nothing is delivered', async () => {
    await withServer(async ({ port, accepted, rejected }) => {
      const res = await request(port, { method: 'POST', body: BODY });
      assert.equal(res.status, 401,
        `an unsigned POST was answered ${res.status}. A 200 is the ack that tells the ` +
          'sender it was accepted, and in the old code it was accompanied by a log ' +
          'append, a cursor advance and delivery to the agent.');
      assert.equal(accepted.length, 0,
        'onAccepted ran for an UNSIGNED delivery — that callback is where the log ' +
          'append, the cursor advance and the agent delivery live');
      assert.equal(rejected.length, 1);
      assert.equal(rejected[0].result.reason, 'missing-signature');
    });
  });

  test('🔴 a WRONGLY-signed POST is rejected, and nothing is delivered', async () => {
    await withServer(async ({ port, accepted, rejected }) => {
      const res = await request(port, {
        method: 'POST',
        body: BODY,
        headers: { 'x-signature': signBody(BODY, OTHER_SECRET) },
      });
      assert.equal(res.status, 401, `a forged POST was answered ${res.status}`);
      assert.equal(accepted.length, 0, 'onAccepted ran for a FORGED delivery');
      assert.equal(rejected[0].result.reason, 'bad-signature');
    });
  });

  test('🔴 a POST from an UNREGISTERED webhook is rejected', async () => {
    await withServer(async ({ port, accepted }) => {
      const body = JSON.stringify({ ...EVENT, webhook_id: 'wh-never-registered' });
      const res = await request(port, {
        method: 'POST',
        body,
        headers: { 'x-signature': signBody(body, SECRET) },
      });
      assert.equal(res.status, 401);
      assert.equal(accepted.length, 0);
    });
  });

  test('a CORRECTLY-signed POST is still accepted (the fix did not just break it)', async () => {
    await withServer(async ({ port, accepted, rejected }) => {
      const res = await request(port, {
        method: 'POST',
        body: BODY,
        headers: { 'x-signature': GOOD_SIG },
      });
      assert.equal(res.status, 200,
        `a correctly signed POST was answered ${res.status} (${res.body}) — hardening ` +
          'that rejects everything is not hardening, it is an outage');
      assert.deepEqual(JSON.parse(res.body), { ok: true });
      assert.equal(rejected.length, 0, `unexpected rejection: ${JSON.stringify(rejected)}`);
      assert.equal(accepted.length, 1, 'the verified event was not delivered');
      assert.equal(accepted[0].event.task_id, '868fixture');
      assert.equal(accepted[0].raw, BODY,
        'onAccepted received a re-serialised body; the signature is over the RAW bytes');
    });
  });

  test('the signature is checked against the RAW BYTES, not a re-serialisation', async () => {
    // 🔴 The fixture is PRETTY-PRINTED on purpose. An earlier version of this
    // test used a key-REORDERED compact body, and a mutant that signed
    // `JSON.stringify(event)` instead of `rawBody` SURVIVED it: JSON.stringify
    // of a freshly-parsed compact object reproduces those bytes exactly, so the
    // two implementations were indistinguishable. Whitespace is the property
    // that round-tripping does NOT preserve, and ClickUp signs what it sent.
    const pretty = JSON.stringify(EVENT, null, 2);
    assert.notEqual(pretty, JSON.stringify(JSON.parse(pretty)),
      'fixture precondition: the body must NOT survive a JSON round-trip unchanged, or ' +
        'this test cannot tell "signed the raw bytes" from "signed a re-serialisation"');
    assert.deepEqual(JSON.parse(pretty), EVENT, 'fixture: same object, different bytes');

    await withServer(async ({ port, accepted }) => {
      // Signed as the compact re-serialisation → must be rejected.
      const wrong = await request(port, {
        method: 'POST',
        body: pretty,
        headers: { 'x-signature': signBody(JSON.stringify(JSON.parse(pretty)), SECRET) },
      });
      assert.equal(wrong.status, 401,
        'a signature computed over a RE-SERIALISED body was accepted — the receiver is ' +
          'verifying its own reconstruction of the payload, not what the sender signed');
      assert.equal(accepted.length, 0);

      // Signed as the bytes actually sent → must be accepted.
      const ok = await request(port, {
        method: 'POST',
        body: pretty,
        headers: { 'x-signature': signBody(pretty, SECRET) },
      });
      assert.equal(ok.status, 200,
        'a signature over the EXACT bytes sent was rejected — ClickUp signs the body it ' +
          'transmits, whitespace included');
      assert.equal(accepted.length, 1);
      assert.equal(accepted[0].raw, pretty);
    });
  });

  test('a malformed body is a 400, and is not delivered', async () => {
    await withServer(async ({ port, accepted, rejected }) => {
      const res = await request(port, {
        method: 'POST',
        body: '{not json',
        headers: { 'x-signature': 'whatever' },
      });
      assert.equal(res.status, 400);
      assert.equal(accepted.length, 0);
      assert.equal(rejected[0].result.reason, 'invalid-json');
    });
  });

  test('an over-large body is refused without being buffered whole', async () => {
    await withServer(async ({ port, accepted }) => {
      const res = await request(port, {
        method: 'POST',
        body: 'x'.repeat(4096),
        headers: { 'x-signature': 'whatever' },
      }).catch((err) => ({ status: `socket:${err.code}`, body: '' }));
      assert.ok(res.status === 413 || String(res.status).startsWith('socket:'),
        `expected a 413 or a destroyed socket, got ${res.status}`);
      assert.equal(accepted.length, 0);
    }, { maxBodyBytes: 512 });
  });

  test('a method that is neither GET nor POST is a 404', async () => {
    await withServer(async ({ port, accepted }) => {
      const res = await request(port, { method: 'PUT', body: BODY });
      assert.equal(res.status, 404);
      assert.equal(accepted.length, 0);
    });
  });

  test('the secret is looked up FRESH per delivery, not snapshotted at startup', async () => {
    // A watcher registered while the listener runs must work. A snapshot taken
    // at process start would make every event from it unverifiable, and under a
    // fail-closed policy that silently drops them all.
    const late = {};
    await withServer(async ({ port, accepted }) => {
      const body = JSON.stringify({ ...EVENT, webhook_id: 'wh-registered-later' });
      const sig = signBody(body, 'late-secret');
      const before = await request(port, { method: 'POST', body, headers: { 'x-signature': sig } });
      assert.equal(before.status, 401, 'fixture precondition: unknown before registration');

      late['wh-registered-later'] = 'late-secret';

      const after = await request(port, { method: 'POST', body, headers: { 'x-signature': sig } });
      assert.equal(after.status, 200,
        'a webhook registered AFTER the listener started was still rejected — the secret ' +
          'lookup is a startup snapshot, so fail-closed turns into a silent outage');
      assert.equal(accepted.length, 1);
    }, { lookupSecret: (id) => late[id] || null });
  });
});
