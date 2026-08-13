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
 * 🔴 And the cursor policy's OWN regression, which the first fix introduced:
 * treating `invalid-json` as undecidable made ONE non-JSON stored request a
 * permanent wedge — worse than the ten it replaced, and triggerable by anyone
 * who can GET the public URL. The pair of tests marked "🔴 a body that is not a
 * JSON object" and "🔴 the same page, TWICE" are what would have caught it: the
 * first over every non-object body webhook.site actually stores, the second by
 * re-walking one page the way consecutive runs do.
 *
 * Usage:
 *   node --test test/catchup.test.mjs
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { dirname, join, resolve } from 'path';
import { fileURLToPath } from 'url';
import { signBody, forwarderArgs, forwarderTarget, LOOPBACK_HOST } from '../lib/webhook-server.mjs';
import { catchUp, classifyStoredRequest, isDecidable, PERMANENT_REJECTIONS } from '../lib/catchup.mjs';
import { resolveApiBase, DEFAULT_API_BASE } from '../lib/webhook-site.mjs';

const SKILL = resolve(dirname(fileURLToPath(import.meta.url)), '..');

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

  // 🔴 THE REGRESSION THIS PAIR EXISTS FOR. The first version of the cursor
  // policy made `invalid-json` UNDECIDABLE — stop the walk, leave the cursor —
  // which turned one non-JSON stored request into a PERMANENT wedge: `SINCE`
  // defaults to last-seen.txt, so the same page re-blocked on every subsequent
  // run until somebody edited state by hand. The trigger is not exotic:
  // webhook.site records an empty `content` for a bare GET, i.e. a crawler, a
  // link preview, or the user opening their own webhook URL in a browser.
  const NOT_A_JSON_OBJECT = [
    ['an empty body — what webhook.site records for a bare GET', ''],
    ['a null content field', null],
    ['a JSON scalar', '42'],
    ['a JSON string', '"a string"'],
    ['HTML', '<html>not a webhook at all</html>'],
    ['a form-encoded body', 'a=1&b=2'],
    ['a JSON array', '[{"webhook_id":"x"}]'],
    ['truncated JSON', '{{{'],
  ];

  test('🔴 a body that is not a JSON object is SKIPPED, and the genuine event behind it is delivered', () => {
    for (const [label, content] of NOT_A_JSON_OBJECT) {
      const r = run([
        { uuid: 'u', content, created_at: '2026-08-13 10:00:00', headers: {} },
        stored(BODY, GOOD_SIG, '2026-08-13 10:00:05'),
      ]);
      assert.equal(r.blocked.length, 0,
        `${label}: it BLOCKED the walk. The cursor is a timestamp and SINCE defaults to ` +
          'last-seen.txt, so this page re-blocks on every subsequent run — permanently, and ' +
          'anyone who can reach the URL can put such a request there.');
      assert.equal(r.delivered.length, 1,
        `${label}: the genuine event behind it was never delivered`);
      assert.deepEqual(r.cursor, ['2026-08-13 10:00:00', '2026-08-13 10:00:05'],
        `${label}: the cursor did not advance past the unparseable request and then onto the ` +
          'genuine event');
      assert.equal(r.log.length, 1, `${label}: a rejected body reached the log`);
    }
  });

  test('🔴 the same page, TWICE: a wedge would show up as run 2 behaving like run 1', () => {
    // The shape of the audit's end-to-end reproduction, at the unit level: the
    // walk is re-run over the SAME stored page with the cursor carried over, as
    // it is in life. A blocked run leaves the cursor untouched, so run 2 is
    // identical to run 1 — forever.
    const page = [
      { uuid: 'bare-get', content: '', created_at: '2026-08-13 09:00:01', headers: {} },
      stored(BODY, GOOD_SIG, '2026-08-13 09:00:02'),
    ];
    const first = run(page);
    assert.equal(first.delivered.length, 1, 'run 1 delivered nothing');
    const cursorAfterFirst = first.cursor[first.cursor.length - 1];
    assert.equal(cursorAfterFirst, '2026-08-13 09:00:02',
      `run 1 left the cursor at ${JSON.stringify(cursorAfterFirst)}. If it did not move, the ` +
        'next run re-reads this page and blocks identically — that is the wedge.');

    // Only what is newer than the cursor comes back on run 2.
    const second = run(page.filter((p) => p.created_at > cursorAfterFirst));
    assert.equal(second.outcome.considered, 0,
      'run 2 still had requests to examine, so run 1 did not make progress');
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

  test('the permanent set is pinned, invalid-json INCLUDED', () => {
    // Pinned as a set, not spot-checked: a reason here is a decision to let the
    // cursor skip past that case, and one MISSING is a decision to wedge on it.
    assert.deepEqual([...PERMANENT_REJECTIONS].sort(),
      ['bad-signature', 'invalid-json', 'malformed-record', 'missing-signature',
        'no-webhook-id', 'unknown-webhook']);
    assert.ok(PERMANENT_REJECTIONS.has('invalid-json'),
      'invalid-json was made undecidable again. Stored bytes do not change: a body that is ' +
        'not JSON will not start being JSON, exactly as a bad signature will not start ' +
        'matching. Removing it turns one bare GET into a permanent wedge.');
  });

  test('🔴 LEDGER: every reason authenticateDelivery can return is classified', () => {
    // A reason the authenticator grows that nobody adds to PERMANENT_REJECTIONS
    // defaults to UNDECIDABLE — i.e. to the wedge. Read out of the source, so
    // this fails when the set grows there and not here.
    const src = readFileSync(join(SKILL, 'lib/webhook-server.mjs'), 'utf8');
    const declared = new Set(
      [...src.matchAll(/reason:\s*'([a-z-]+)'/g)].map((m) => m[1]).filter((r) => r !== 'verified'));
    assert.ok(declared.size >= 5,
      `only ${declared.size} rejection reason(s) found in lib/webhook-server.mjs — the scan ` +
        'is wired to nothing, so "all classified" means nothing');
    const unclassified = [...declared].filter((r) => !PERMANENT_REJECTIONS.has(r));
    assert.deepEqual(unclassified, [],
      `authenticateDelivery can return ${unclassified.join(', ')}, which PERMANENT_REJECTIONS ` +
        'does not list. Unlisted means undecidable means the walk STOPS on it and the cursor ' +
        'never moves — decide deliberately, do not inherit it.');
    const stale = [...PERMANENT_REJECTIONS].filter(
      (r) => r !== 'malformed-record' && !declared.has(r));
    assert.deepEqual(stale, [],
      `PERMANENT_REJECTIONS lists ${stale.join(', ')}, which no longer exists in ` +
        'lib/webhook-server.mjs — the ledger has to shrink with it too');
  });

  test('🔴 an UNKNOWN reason is undecidable even when dated — the fail-closed default', () => {
    // Reachability for the `PERMANENT_REJECTIONS.has()` clause. Every reason the
    // authenticator returns today is listed, so nothing OBSERVABLE depends on
    // that clause — deleting it passes every other test in this file. It exists
    // for the reason someone adds tomorrow, and this is the case that says so.
    assert.equal(isDecidable('transient-registry-unavailable', '2026-08-13 10:00:00'), false,
      'a rejection reason nobody classified was treated as permanent, so the cursor would ' +
        'skip past a case that might resolve on the next run. Unlisted must mean STOP.');
    assert.equal(isDecidable('invalid-json', '2026-08-13 10:00:00'), true,
      'a listed reason with a timestamp is decidable — otherwise the fail-closed default has ' +
        'swallowed everything and the walk wedges on the first rejection');
    assert.equal(isDecidable('invalid-json', null), false,
      'a listed reason with NO timestamp is not decidable: there is nothing to advance to');
  });

  test('classifyStoredRequest reports decidability per reason', () => {
    // Every rejection is decidable WHEN DATED — the reason is a property of
    // bytes that are already stored and will not change.
    const cases = [
      ['missing-signature', stored(BODY, undefined), true],
      ['bad-signature', stored(BODY, signBody(BODY, OTHER_SECRET)), true],
      ['no-webhook-id', stored(JSON.stringify({ event: 'x' }), 'sig'), true],
      ['unknown-webhook', (() => {
        const b = JSON.stringify({ ...EVENT, webhook_id: 'wh-nope' });
        return stored(b, signBody(b, SECRET));
      })(), true],
      ['invalid-json', stored('{{{', 'sig'), true],
      ['invalid-json', stored('', undefined), true],
    ];
    for (const [reason, req, decidable] of cases) {
      const r = classifyStoredRequest(req, lookupSecret);
      assert.equal(r.accepted, false, `${reason}: expected a rejection`);
      assert.equal(r.reason, reason, `expected reason ${reason}, got ${r.reason}`);
      assert.equal(r.decidable, decidable,
        `${reason}: decidable=${r.decidable}, expected ${decidable}`);
    }
  });

  test('🔴 the SAME reason is undecidable without a timestamp and decidable with one', () => {
    // The pair that proves the rule is "datable", not "this reason". Same body,
    // same headers, one field different.
    for (const reason of ['invalid-json', 'missing-signature']) {
      const content = reason === 'invalid-json' ? '' : BODY;
      const dated = classifyStoredRequest(
        { content, headers: {}, created_at: '2026-08-13 10:00:00' }, lookupSecret);
      const undated = classifyStoredRequest({ content, headers: {} }, lookupSecret);
      assert.equal(dated.reason, reason);
      assert.equal(undated.reason, reason);
      assert.equal(dated.decidable, true, `${reason}: a DATED rejection was undecidable`);
      assert.equal(undated.decidable, false,
        `${reason}: an UNDATED rejection was decidable, so the caller would advance the ` +
          'cursor to null and lose its place entirely');
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

// ── 5. Where stored requests may be fetched FROM ──────────────────────────
//
// 🔴 CLICKUP_WEBHOOK_SITE_API_BASE is a credential-egress knob, not just a test
// seam: the webhook.site token is in the request PATH and WEBHOOK_SITE_API_KEY
// rides as an `Api-Key` header, so an unconstrained base hands both to whatever
// host it names — over cleartext if it says http. It shipped with a comment
// clearing it of a risk it never had (delivery integrity: every fetched request
// is still authenticated) and silent about the one it has.

describe('the webhook.site API base is gated', () => {
  const REFUSED = [
    ['a third-party host', 'https://evil.example.com'],
    ['a third-party host over http', 'http://evil.example.com'],
    ['a lookalike suffix', 'https://notwebhook.site'],
    ['a lookalike prefix', 'https://webhook.site.evil.example.com'],
    ['🔴 a PLAINTEXT downgrade of the real destination', 'http://webhook.site'],
    ['a non-http scheme', 'ftp://webhook.site'],
    ['a file URL', 'file:///etc/passwd'],
    ['not a URL at all', 'webhook.site'],
    ['garbage', ':::::'],
  ];

  test('🔴 a base that is neither loopback nor webhook.site is REFUSED', () => {
    for (const [label, raw] of REFUSED) {
      const r = resolveApiBase(raw);
      assert.equal(r.base, DEFAULT_API_BASE,
        `${label} (${raw}) was accepted as the API base. The webhook.site token is in the ` +
          'URL path, so that host receives a capability for this workspace\'s entire event ' +
          'stream.');
      assert.ok(r.warning, `${label}: refused SILENTLY — a refusal nobody sees is a mystery`);
    }
  });

  test('🔴 a refused base never gets the Api-Key either', () => {
    // Belt and braces: the fallback is webhook.site itself, so sendApiKey is
    // true — what must be impossible is the KEY going to the named host.
    for (const [, raw] of REFUSED) {
      const r = resolveApiBase(raw);
      assert.equal(r.base, DEFAULT_API_BASE, `${raw} survived as the base`);
    }
  });

  test('🔴 a loopback base is allowed and gets NO Api-Key', () => {
    for (const raw of ['http://127.0.0.1:8123', 'http://localhost:8123',
      'https://127.0.0.1:8123', 'http://[::1]:8123', 'http://127.9.9.9:1']) {
      const r = resolveApiBase(raw);
      assert.ok(r.base.startsWith(new URL(raw).origin),
        `${raw} was refused — the catch-up path then has no way to be exercised at all`);
      assert.equal(r.sendApiKey, false,
        `${raw} would be sent the webhook.site account API key. A test stub has no use for ` +
          'one, and not sending it is what keeps this override out of the credential path.');
      assert.equal(r.warning, null, `${raw}: warned about a legitimate loopback stub`);
    }
  });

  test('webhook.site over https is allowed, WITH the Api-Key', () => {
    for (const raw of ['https://webhook.site', 'https://webhook.site/', 'https://eu.webhook.site']) {
      const r = resolveApiBase(raw);
      assert.equal(r.sendApiKey, true, `${raw}: the real destination lost its Api-Key`);
      assert.equal(r.warning, null);
      assert.ok(!r.base.endsWith('/'), `${raw}: a trailing slash survived into ${r.base}`);
    }
  });

  test('an unset or empty base is the default, with the Api-Key', () => {
    for (const raw of [undefined, null, '', '   ']) {
      const r = resolveApiBase(raw);
      assert.equal(r.base, DEFAULT_API_BASE);
      assert.equal(r.sendApiKey, true);
      assert.equal(r.warning, null, `${JSON.stringify(raw)}: warned about the default`);
    }
  });

  test('the default destination is https', () => {
    assert.ok(DEFAULT_API_BASE.startsWith('https://'),
      'the default base is cleartext — the token in the path travels in the clear');
  });

  test('listen.mjs asks this module rather than reading the env itself', () => {
    // A second copy of the policy in the script is how a gate stops applying.
    const src = readFileSync(join(SKILL, 'listen.mjs'), 'utf8');
    const reads = [...src.matchAll(/CLICKUP_WEBHOOK_SITE_API_BASE/g)].length;
    assert.equal(reads, 1,
      `listen.mjs names CLICKUP_WEBHOOK_SITE_API_BASE ${reads} times; it must read it once, ` +
        'and only to hand it to resolveApiBase()');
    assert.ok(/resolveApiBase\(process\.env\.CLICKUP_WEBHOOK_SITE_API_BASE\)/.test(src),
      'listen.mjs no longer routes the override through resolveApiBase()');
    assert.ok(/API\.sendApiKey/.test(src),
      "listen.mjs attaches the Api-Key without consulting the gate's sendApiKey decision");
  });
});
