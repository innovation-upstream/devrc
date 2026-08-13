#!/usr/bin/env node

/**
 * listen.mjs, END TO END (hermetic — a fixture token, a scratch state dir, no
 * ClickUp, no network beyond 127.0.0.1).
 *
 * WHY THIS EXISTS SEPARATELY FROM webhook-server.test.mjs
 * ------------------------------------------------------
 * "Verified in isolation" is the vacuous green: lib/webhook-server.mjs can be
 * perfect and listen.mjs can still call `server.listen(PORT)` itself, log the
 * event before authenticating it, or write webhook-url.txt 0644. The defect
 * lives in the SEAM. So this runs the REAL script as a REAL process and asserts
 * on the REAL state directory it writes:
 *
 *   * it binds 127.0.0.1 (read back from what the process reports it bound,
 *     not from what it was asked to bind)
 *   * GET discloses no webhook_url
 *   * an unsigned and a wrongly-signed POST are rejected AND leave no trace:
 *     no webhooks.jsonl, no last-seen.txt cursor, no webhook-latest.json
 *   * a correctly-signed POST is accepted and produces exactly ONE log line —
 *     which is also the proof that the two rejected ones never appended
 *   * webhook-url.txt lands 0600, because the URL embeds the webhook.site
 *     capability token
 *
 * The token here is a fixture string. Nothing in this file may print a
 * webhook.site URL: `redact()` scrubs the child's output before it can reach an
 * assertion message.
 *
 * Usage:
 *   node --test test/listen-integration.test.mjs
 */

import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'child_process';
import { createHmac } from 'crypto';
import http from 'http';
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  existsSync,
  statSync,
  rmSync,
} from 'fs';
import { tmpdir } from 'os';
import { dirname, join, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SKILL = resolve(__dirname, '..');
const LISTEN = join(SKILL, 'listen.mjs');

const FIXTURE_TOKEN = 'fixture-token-0000-not-a-real-webhook-site-uuid';
const WEBHOOK_ID = 'wh-integration-0001';
const SECRET = 'fixture-integration-secret-not-a-real-signing-key';

const EVENT = { event: 'taskStatusUpdated', task_id: '868integration', webhook_id: WEBHOOK_ID };
const BODY = JSON.stringify(EVENT);
const sign = (body, secret) => createHmac('sha256', secret).update(body).digest('hex');

// ── Scratch state dir, pre-seeded with a watcher ──────────────────────────

const SCRATCH = mkdtempSync(join(tmpdir(), 'clickup-listen-test-'));
const XDG = join(SCRATCH, 'xdg');
const STATE = join(XDG, 'clickup');
mkdirSync(STATE, { recursive: true });
writeFileSync(
  join(STATE, 'watchers.json'),
  JSON.stringify([{ webhookId: WEBHOOK_ID, secret: SECRET, kind: 'fixture' }], null, 2)
);

const statePath = (name) => join(STATE, name);
const LOG = statePath('webhooks.jsonl');
const LATEST = statePath('webhook-latest.json');
const CURSOR = statePath('last-seen.txt');

/** Never let a webhook.site URL reach an assertion message. */
const redact = (s) => String(s).replace(/webhook\.site\/[^\s"']+/g, 'webhook.site/<redacted>');

// ── Start the real process ────────────────────────────────────────────────

let stderrBuf = '';
const child = spawn(process.execPath, [LISTEN, '--mode', 'server', '--port', '0'], {
  env: {
    ...process.env,
    HOME: SCRATCH,
    XDG_STATE_HOME: XDG,
    CLICKUP_WEBHOOK_SITE_TOKEN: FIXTURE_TOKEN,
    // Do NOT inherit a real key: nothing here may reach webhook.site.
    WEBHOOK_SITE_API_KEY: '',
    // whcli is not installed; the forwarder is expected to fail loudly and be
    // irrelevant. PATH is left alone so `node` and `sh` still resolve.
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
child.stdout.setEncoding('utf8');
child.stderr.setEncoding('utf8');
child.stderr.on('data', (d) => { stderrBuf += d; });
child.stdout.on('data', () => {});

after(() => {
  try { child.kill('SIGKILL'); } catch { /* already gone */ }
  rmSync(SCRATCH, { recursive: true, force: true });
});

/** Wait for the process to report the address it actually bound. */
async function waitForListening(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    // `(\S+)` not `([^\s:]+)`: an all-interfaces bind reports `::`, and a
    // pattern that cannot match the WRONG answer turns a real defect into a
    // "never reported a bound address" timeout attributed to nothing.
    const m = /Listening on (\S+):(\d+)/.exec(stderrBuf);
    if (m) return { address: m[1], port: Number(m[2]) };
    if (child.exitCode !== null) {
      throw new Error(
        `listen.mjs exited ${child.exitCode} before listening:\n${redact(stderrBuf)}`
      );
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`listen.mjs never reported a bound address:\n${redact(stderrBuf)}`);
}

const bound = await waitForListening();

function request(method, { body, headers = {} } = {}) {
  return new Promise((res, rej) => {
    const req = http.request(
      { host: '127.0.0.1', port: bound.port, method, path: '/', headers },
      (r) => {
        let data = '';
        r.on('data', (c) => (data += c));
        r.on('end', () => res({ status: r.statusCode, body: data }));
      }
    );
    req.on('error', rej);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Poll for a file to appear — the child writes it just after responding. */
async function waitForFile(p, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (existsSync(p)) return true;
    await sleep(25);
  }
  return false;
}

// ── 1. The bind ───────────────────────────────────────────────────────────

test('🔴 the real listener binds 127.0.0.1, not every interface', () => {
  assert.equal(bound.address, '127.0.0.1',
    `listen.mjs reported binding ${bound.address}. 0.0.0.0 (or ::) puts this webhook ` +
      'receiver on the LAN address and the nebula address of whichever host runs it.');
});

// ── 2. The unauthenticated GET ────────────────────────────────────────────

test('🔴 GET does not disclose the webhook.site capability URL', async () => {
  const res = await request('GET');
  assert.equal(res.status, 200, `GET returned ${res.status}: ${redact(res.body)}`);
  const json = JSON.parse(res.body);
  assert.ok(!('webhook_url' in json),
    'GET returned webhook_url — that URL embeds the webhook.site token, and this ' +
      'endpoint has no authentication at all');
  assert.ok(!res.body.includes(FIXTURE_TOKEN),
    'GET leaked the webhook.site token in its response body');
  assert.ok(!/webhook\.site/.test(res.body),
    `GET leaked a webhook.site URL: ${redact(res.body)}`);
});

// ── 3. Rejected deliveries leave NO trace ─────────────────────────────────

test('🔴 an UNSIGNED POST is rejected', async () => {
  const res = await request('POST', { body: BODY });
  assert.equal(res.status, 401,
    `an unsigned POST was answered ${res.status} — a 200 is an ack, and in the old code ` +
      'it came with a log append, a cursor advance and delivery to the agent');
});

test('🔴 a WRONGLY-signed POST is rejected', async () => {
  const res = await request('POST', {
    body: BODY,
    headers: { 'x-signature': sign(BODY, 'the-attackers-guess') },
  });
  assert.equal(res.status, 401, `a forged POST was answered ${res.status}`);
});

test('🔴 a POST from an UNREGISTERED webhook is rejected (the unverifiable case)', async () => {
  const body = JSON.stringify({ ...EVENT, webhook_id: 'wh-not-in-watchers-json' });
  const res = await request('POST', {
    body,
    headers: { 'x-signature': sign(body, SECRET) },
  });
  assert.equal(res.status, 401,
    'an event whose webhook is not in watchers.json was accepted. That is the case the ' +
      "old code logged as verified:'skipped' and delivered anyway.");
});

test('🔴 the three rejected deliveries wrote NOTHING to the state dir', async () => {
  await sleep(250); // any write would already have happened, synchronously
  assert.ok(!existsSync(LOG),
    `webhooks.jsonl exists after only REJECTED deliveries:\n${
      existsSync(LOG) ? redact(readFileSync(LOG, 'utf8')) : ''}`);
  assert.ok(!existsSync(CURSOR),
    'last-seen.txt exists after only REJECTED deliveries — the cursor advanced past ' +
      'events that were never authenticated');
  assert.ok(!existsSync(LATEST),
    'webhook-latest.json exists after only REJECTED deliveries — that file is what the ' +
      'agent reads as "the event you were waiting for"');
});

// ── 4. A genuine delivery still works ─────────────────────────────────────

test('a CORRECTLY-signed POST is accepted (the fix did not just break the listener)', async () => {
  const res = await request('POST', {
    body: BODY,
    headers: { 'x-signature': sign(BODY, SECRET) },
  });
  assert.equal(res.status, 200,
    `a correctly signed POST was answered ${res.status}: ${redact(res.body)}\n` +
      `child stderr:\n${redact(stderrBuf)}`);
  assert.deepEqual(JSON.parse(res.body), { ok: true });
});

test('the accepted delivery — and ONLY it — reached the log, cursor and latest file', async () => {
  assert.ok(await waitForFile(LOG), `webhooks.jsonl was never written:\n${redact(stderrBuf)}`);
  assert.ok(await waitForFile(CURSOR), 'last-seen.txt was never written');
  assert.ok(await waitForFile(LATEST), 'webhook-latest.json was never written');

  const lines = readFileSync(LOG, 'utf8').trim().split('\n').filter(Boolean);
  assert.equal(lines.length, 1,
    `webhooks.jsonl holds ${lines.length} line(s); exactly ONE delivery was authentic. ` +
      'Any extra line is a rejected delivery that was logged anyway.');

  const entry = JSON.parse(lines[0]);
  assert.equal(entry.task_id, '868integration');
  assert.equal(entry.verified, true,
    `the logged entry says verified=${JSON.stringify(entry.verified)}; anything that ` +
      "reaches the log is verified now, so 'skipped' or false means an unauthenticated " +
      'delivery got through');

  const latest = JSON.parse(readFileSync(LATEST, 'utf8'));
  assert.equal(latest.task_id, '868integration');
  assert.ok(readFileSync(CURSOR, 'utf8').trim().length > 0, 'the cursor file is empty');
});

// ── 5. webhook-url.txt is a credential ────────────────────────────────────

test('🔴 webhook-url.txt is written 0600 — the URL IS the capability', async () => {
  const p = statePath('webhook-url.txt');
  assert.ok(await waitForFile(p), 'listen.mjs did not write webhook-url.txt at all');
  const mode = (statSync(p).mode & 0o777).toString(8);
  assert.equal(mode, '600',
    `webhook-url.txt is mode ${mode}. It holds https://webhook.site/<token>, and that ` +
      'token is a capability: whoever reads it can read this workspace\'s event stream ' +
      'and POST forged events into it. Written with no mode argument it lands 0644.');
});

// ── 6. The forwarder targets the address the receiver actually bound ──────

test('🔴 the forwarder is pointed at 127.0.0.1:<the bound port>, never localhost', () => {
  // Read back out of the array the process SPAWNS (listen.mjs prints that
  // element, not a recomputation), because `localhost` can resolve to ::1 and
  // the receiver binds 127.0.0.1 only: whcli would then be refused on every
  // delivery by a listener that reports perfectly healthy.
  const m = /\[whcli\] target (\S+)/.exec(stderrBuf);
  assert.ok(m, `listen.mjs never reported a forwarder target:\n${redact(stderrBuf)}`);
  assert.equal(m[1], `http://127.0.0.1:${bound.port}`,
    `whcli was pointed at ${m[1]} while the receiver bound 127.0.0.1:${bound.port}`);
});

// ── 7. CATCH-UP, end to end, against a loopback stub of webhook.site ──────
//
// The catch-up branch is gated on `SINCE && MODE === 'wait'`, so the server-mode
// child above can never reach it — which is exactly why two mutations that
// reinstate the pre-#438 defect (deliver whatever webhook.site stored) survived
// the entire suite. CLICKUP_WEBHOOK_SITE_API_BASE points the fetch at a stub
// here; it changes WHERE stored requests come from, never whether they are
// authenticated.

/** A webhook.site stored-requests API, on loopback, serving a canned page. */
async function stubWebhookSite(requests) {
  const seen = [];   // every request's headers, so the caller can assert on them
  const srv = http.createServer((req, res) => {
    seen.push(req.headers);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ data: requests }));
  });
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  return { srv, seen, base: `http://127.0.0.1:${srv.address().port}` };
}

/** One stored request, in the shape webhook.site's API returns (header ARRAYS). */
function storedRequest(content, signature, created_at) {
  const r = { uuid: `fixture-${created_at}`, content, created_at, headers: {} };
  if (signature !== undefined) r.headers['x-signature'] = [signature];
  return r;
}

/** A scratch state dir with one registered watcher — the host's state, reusable. */
function catchUpState() {
  const scratch = mkdtempSync(join(tmpdir(), 'clickup-catchup-'));
  const xdg = join(scratch, 'xdg');
  const state = join(xdg, 'clickup');
  mkdirSync(state, { recursive: true });
  writeFileSync(join(state, 'watchers.json'),
    JSON.stringify([{ webhookId: WEBHOOK_ID, secret: SECRET, kind: 'fixture' }], null, 2));
  return { scratch, xdg, state };
}

/**
 * Run listen.mjs in WAIT mode against the stub, to completion.
 *
 * `ctx` lets a caller run the SAME state dir twice — the only way to observe a
 * cursor that does not move, since `--since` defaults to last-seen.txt and a
 * wedge is by definition something that repeats. `since: null` omits the flag so
 * the cursor file is what decides, exactly as it does on a host.
 */
async function runCatchUp(requests, {
  timeout = 8, ctx = null, since = '2026-08-13 09:00:00', apiKey = '',
} = {}) {
  const own = !ctx;
  const { scratch, xdg, state } = ctx || catchUpState();

  const stub = await stubWebhookSite(requests);
  const proc = spawn(process.execPath, [
    LISTEN, '--mode', 'wait', '--port', '0',
    ...(since === null ? [] : ['--since', since]),
    '--timeout', String(timeout),
  ], {
    env: { ...process.env, HOME: scratch, XDG_STATE_HOME: xdg,
      CLICKUP_WEBHOOK_SITE_TOKEN: FIXTURE_TOKEN, WEBHOOK_SITE_API_KEY: apiKey,
      CLICKUP_WEBHOOK_SITE_API_BASE: stub.base },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let out = '';
  let err = '';
  proc.stdout.setEncoding('utf8');
  proc.stderr.setEncoding('utf8');
  proc.stdout.on('data', (d) => { out += d; });
  proc.stderr.on('data', (d) => { err += d; });

  const code = await new Promise((resolve) => {
    const kill = setTimeout(() => { try { proc.kill('SIGKILL'); } catch {} }, (timeout + 10) * 1000);
    proc.on('exit', (c) => { clearTimeout(kill); resolve(c); });
  });
  // closeAllConnections first: close() waits for live sockets, and a child
  // killed mid-request can leave one — which hangs the file, not the test.
  stub.srv.closeAllConnections();
  await new Promise((r) => stub.srv.close(r));

  const read = (n) => (existsSync(join(state, n)) ? readFileSync(join(state, n), 'utf8') : null);
  const result = { code, out, err, state, seen: stub.seen,
    log: read('webhooks.jsonl'), cursor: read('last-seen.txt') };
  if (own) rmSync(scratch, { recursive: true, force: true });
  return result;
}

test('🔴 CATCH-UP: forged and unverifiable stored events are skipped, the genuine one is delivered', async () => {
  const forged = JSON.stringify({ ...EVENT, task_id: '868forged' });
  const orphan = JSON.stringify({ ...EVENT, task_id: '868orphan', webhook_id: 'wh-not-in-watchers' });
  const r = await runCatchUp([
    // Unsigned — the shape anyone can POST to a webhook.site URL.
    storedRequest(forged, undefined, '2026-08-13 09:00:01'),
    // Signed with the wrong key.
    storedRequest(forged, sign(forged, 'the-attackers-guess'), '2026-08-13 09:00:02'),
    // Correctly signed, but by a webhook this skill does not own.
    storedRequest(orphan, sign(orphan, SECRET), '2026-08-13 09:00:03'),
    // The real one.
    storedRequest(BODY, sign(BODY, SECRET), '2026-08-13 09:00:04'),
  ]);

  assert.equal(r.code, 0, `catch-up did not deliver and exit 0:\n${redact(r.err)}`);
  assert.ok(r.out.includes('868integration'),
    `the delivered payload was not the authentic event:\n${redact(r.out)}`);
  assert.ok(!r.out.includes('868forged') && !r.out.includes('868orphan'),
    `catch-up delivered a payload it never authenticated — this is the pre-#438 defect ` +
      `in the catch-up path:\n${redact(r.out)}`);

  const lines = (r.log || '').trim().split('\n').filter(Boolean);
  assert.equal(lines.length, 1,
    `webhooks.jsonl holds ${lines.length} line(s); exactly ONE stored request was authentic. ` +
      `Any extra line is a rejected delivery that was logged anyway:\n${redact(r.log || '')}`);
  assert.equal(JSON.parse(lines[0]).task_id, '868integration');
  assert.equal((r.cursor || '').trim(), '2026-08-13 09:00:04',
    'the cursor does not sit on the delivered event');
});

test('🔴 CATCH-UP: a run of unverifiable stored events advances the cursor instead of wedging', async () => {
  // The lost-watchers.json case: every stored event is unknown-webhook. With a
  // 10-per-page cursor keyed on date_from, ten of these used to be a PERMANENT
  // blind spot — the same ten re-read on every run, forever.
  const reqs = [];
  for (let i = 0; i < 10; i++) {
    const body = JSON.stringify({ ...EVENT, task_id: `868gone${i}`, webhook_id: `wh-gone-${i}` });
    reqs.push(storedRequest(body, sign(body, SECRET), `2026-08-13 09:10:0${i}`));
  }
  const r = await runCatchUp(reqs, { timeout: 3 });

  assert.equal((r.cursor || '').trim(), '2026-08-13 09:10:09',
    `the cursor is ${JSON.stringify(r.cursor)} after ten permanently-unverifiable stored ` +
      'requests. It must sit past the last of them, or catch-up re-reads the same page on ' +
      'every run and can never reach a genuine event behind it.');
  assert.equal(r.log, null,
    `a rejected stored request was written to the log:\n${redact(r.log || '')}`);
  assert.ok(/\[catch-up:rejected:unknown-webhook\]/.test(r.err),
    `the skips were not reported on stderr:\n${redact(r.err)}`);
  assert.ok(/Listening on 127\.0\.0\.1:/.test(r.err),
    `catch-up did not fall through to the live listener:\n${redact(r.err)}`);
  assert.equal(r.code, 1, 'wait mode should time out with exit 1 having delivered nothing');
});

test('🔴 CATCH-UP: an UNPARSEABLE stored request is stepped over, not wedged on', async () => {
  const r = await runCatchUp([
    storedRequest('<html>not a webhook</html>', 'whatever', '2026-08-13 09:20:00'),
    storedRequest(BODY, sign(BODY, SECRET), '2026-08-13 09:20:01'),
  ], { timeout: 3 });

  assert.equal(r.code, 0,
    `catch-up did not reach the genuine event behind an unparseable one:\n${redact(r.err)}`);
  assert.ok(r.out.includes('868integration'), `nothing was delivered:\n${redact(r.out)}`);
  assert.equal((r.cursor || '').trim(), '2026-08-13 09:20:01',
    `the cursor is ${JSON.stringify(r.cursor)}. A stored body that is not JSON is as fixed as ` +
      'a bad signature — standing still on it re-reads the same page forever.');
  assert.ok(/\[catch-up:rejected:invalid-json\]/.test(r.err),
    `the skip was not reported on stderr:\n${redact(r.err)}`);
  const lines = (r.log || '').trim().split('\n').filter(Boolean);
  assert.equal(lines.length, 1, `a rejected body reached the log:\n${redact(r.log || '')}`);
});

test('🔴 CATCH-UP, TWO RUNS: a bare GET does not park the cursor forever', async () => {
  // 🔴 THE REGRESSION, end to end and in the shape it bites. webhook.site
  // records an EMPTY body for a bare GET — a crawler, a link preview, the user
  // opening their own webhook URL — and `--since` is omitted here, so SINCE
  // comes from last-seen.txt exactly as it does on a host. When that request
  // blocked the walk, the cursor never moved and run 2 was identical to run 1:
  // measured `exit=1 delivered=false cursor=09:00:00` on both.
  const ctx = catchUpState();
  try {
    writeFileSync(join(ctx.state, 'last-seen.txt'), '2026-08-13 09:00:00');
    const page = [
      { uuid: 'bare-get', content: '', created_at: '2026-08-13 09:00:01', headers: {} },
      storedRequest(BODY, sign(BODY, SECRET), '2026-08-13 09:00:02'),
    ];

    const first = await runCatchUp(page, { timeout: 3, ctx, since: null });
    assert.equal(first.code, 0,
      `run 1 exited ${first.code} instead of delivering. One request anybody can create — a ` +
        `GET to the public URL — is holding up every event behind it:\n${redact(first.err)}`);
    assert.ok(first.out.includes('868integration'),
      `run 1 delivered no genuine event:\n${redact(first.out)}`);
    assert.equal((first.cursor || '').trim(), '2026-08-13 09:00:02',
      `run 1 left the cursor at ${JSON.stringify(first.cursor)}; it must sit on the delivered ` +
        'event, or run 2 re-reads the same page and blocks identically, forever');

    // Run 2 sees only what is newer than the cursor — i.e. nothing.
    const second = await runCatchUp(
      page.filter((p) => p.created_at > (first.cursor || '').trim()), { timeout: 3, ctx, since: null });
    assert.ok(/No missed events/.test(second.err),
      `run 2 still had stored requests to examine, so run 1 made no progress:\n${redact(second.err)}`);
    assert.equal(second.code, 1, 'run 2 delivered something; there was nothing left to deliver');
  } finally {
    rmSync(ctx.scratch, { recursive: true, force: true });
  }
});

test('🔴 CATCH-UP: an UNDATABLE stored request is what stops the walk', async () => {
  // The genuinely undecidable case, and the only one: no created_at means
  // nothing to move the cursor TO. It cannot be produced by a request BODY —
  // webhook.site stamps what it stores — which is why it does not wedge in life.
  const r = await runCatchUp([
    { uuid: 'undatable', content: BODY, headers: {} },
    storedRequest(BODY, sign(BODY, SECRET), '2026-08-13 09:30:01'),
  ], { timeout: 3 });

  assert.equal(r.cursor, null,
    `the cursor advanced to ${JSON.stringify(r.cursor)} on a record with no timestamp — there ` +
      'is nothing to advance to, so that value was guessed');
  assert.ok(/\[catch-up:blocked:missing-signature\]/.test(r.err),
    `the blocked request was not reported:\n${redact(r.err)}`);
  assert.ok(/BLOCKED at an undatable request/.test(r.err),
    `the summary line hid the block. A wedged run must not end on a line that reads like a ` +
      `clean sweep:\n${redact(r.err)}`);
  assert.equal(r.log, null, 'something was logged despite nothing being authenticated');
  assert.equal(r.code, 1);
});

test('🔴 the webhook.site ACCOUNT API KEY is never sent to the loopback override', async () => {
  // The override's whole purpose is that the tests can point the fetch at a
  // stub. That must not mean handing the stub an account credential — so the
  // key is set here, deliberately, and must not appear on the wire.
  const r = await runCatchUp([storedRequest(BODY, sign(BODY, SECRET), '2026-08-13 09:40:00')],
    { timeout: 3, apiKey: 'fixture-account-api-key-not-real' });

  assert.ok(r.seen.length >= 1,
    `the stub received ${r.seen.length} request(s) — a header assertion over nothing is not ` +
      `evidence:\n${redact(r.err)}`);
  for (const headers of r.seen) {
    const keys = Object.keys(headers).map((k) => k.toLowerCase());
    assert.ok(!keys.includes('api-key'),
      `the Api-Key header was sent to the loopback base. Whatever CLICKUP_WEBHOOK_SITE_API_BASE ` +
        'names then receives a webhook.site ACCOUNT credential, not just this workspace\'s token.');
    for (const [k, v] of Object.entries(headers)) {
      assert.ok(!String(v).includes('fixture-account-api-key-not-real'),
        `the API key travelled in the ${k} header instead`);
    }
  }
});

test('🔴 an oversized stored-requests response is discarded, not accumulated', async () => {
  // `data += chunk` with no ceiling is an allocation the BASE HOST controls.
  // The cap is only a real cap if something exceeds it, so this streams past it.
  const ctx = catchUpState();
  const big = 'x'.repeat(64 * 1024);
  let sent = 0;
  const flood = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    const pump = () => {
      while (sent < 6 * 1024 * 1024) {
        sent += big.length;
        if (!res.write(big)) { res.once('drain', pump); return; }
      }
      res.end();
    };
    pump();
  });
  flood.on('clientError', () => {});
  await new Promise((r) => flood.listen(0, '127.0.0.1', r));
  try {
    const proc = spawn(process.execPath, [
      LISTEN, '--mode', 'wait', '--port', '0', '--since', '2026-08-13 09:00:00', '--timeout', '2',
    ], {
      env: { ...process.env, HOME: ctx.scratch, XDG_STATE_HOME: ctx.xdg,
        CLICKUP_WEBHOOK_SITE_TOKEN: FIXTURE_TOKEN, WEBHOOK_SITE_API_KEY: '',
        CLICKUP_WEBHOOK_SITE_API_BASE: `http://127.0.0.1:${flood.address().port}` },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    proc.stderr.setEncoding('utf8');
    let err = '';
    proc.stderr.on('data', (d) => { err += d; });
    const code = await new Promise((resolve) => {
      const kill = setTimeout(() => { try { proc.kill('SIGKILL'); } catch {} }, 40000);
      proc.on('exit', (c) => { clearTimeout(kill); resolve(c); });
    });

    assert.ok(sent > 5 * 1024 * 1024,
      `the flood server only sent ${sent} bytes — under the cap, so this measures nothing`);
    assert.ok(/response exceeded/.test(err),
      `an oversized response was accepted instead of discarded:\n${redact(err)}`);
    assert.ok(/Listening on 127\.0\.0\.1:/.test(err),
      `the listener did not fall through to the live path after discarding it:\n${redact(err)}`);
    assert.equal(code, 1, 'wait mode should time out with exit 1 having delivered nothing');
  } finally {
    flood.closeAllConnections();
    await new Promise((r) => flood.close(r));
    rmSync(ctx.scratch, { recursive: true, force: true });
  }
});

test('🔴 a hostile CLICKUP_WEBHOOK_SITE_API_BASE is refused by the real process', async () => {
  // The gate, exercised through the script rather than only through
  // resolveApiBase(): a base that is neither loopback nor webhook.site must not
  // be contacted at all — the token is in the path and the API key is a header.
  const ctx = catchUpState();
  let hits = 0;
  const hostile = http.createServer((req, res) => {
    hits += 1;
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ data: [] }));
  });
  await new Promise((r) => hostile.listen(0, '127.0.0.1', r));
  try {
    const port = hostile.address().port;

    const runWithBase = async (base) => {
      const proc = spawn(process.execPath, [
        LISTEN, '--mode', 'wait', '--port', '0', '--since', '2026-08-13 09:00:00', '--timeout', '2',
      ], {
        env: { ...process.env, HOME: ctx.scratch, XDG_STATE_HOME: ctx.xdg,
          CLICKUP_WEBHOOK_SITE_TOKEN: FIXTURE_TOKEN, WEBHOOK_SITE_API_KEY: 'fixture-api-key',
          CLICKUP_WEBHOOK_SITE_API_BASE: base },
        stdio: ['ignore', 'ignore', 'pipe'],
      });
      proc.stderr.setEncoding('utf8');
      let err = '';
      proc.stderr.on('data', (d) => { err += d; });
      await new Promise((resolve) => {
        const kill = setTimeout(() => { try { proc.kill('SIGKILL'); } catch {} }, 30000);
        proc.on('exit', () => { clearTimeout(kill); resolve(); });
      });
      return err;
    };

    // POSITIVE CONTROL first: the same server, named as loopback, IS contacted.
    // Without this, "0 requests" is indistinguishable from a server nothing
    // could have reached — and the refusal below would prove nothing.
    const okErr = await runWithBase(`http://127.0.0.1:${port}`);
    assert.equal(hits, 1,
      `the loopback base produced ${hits} request(s) — the stub is not reachable, so a zero ` +
        `from the refused base measures nothing:\n${redact(okErr)}`);

    // The same host, spelled as a name that is neither loopback nor
    // webhook.site: refused on the NAME, before any connection.
    const err = await runWithBase(`http://webhook.site.localtest.me:${port}`);
    assert.equal(hits, 1,
      `listen.mjs sent a request to a base that is neither loopback nor webhook.site (hits ` +
        `went 1 -> ${hits}). The webhook.site token is in the request PATH and the account ` +
        'API key rides as a header, so that host was handed both.');
    assert.ok(/Refusing it/.test(err),
      `the refusal was not printed — a gate nobody can see is a mystery:\n${redact(err)}`);
  } finally {
    // 🔴 In the FINALLY, not after the assertions. `close()` waits for open
    // connections and node's global agent keeps them alive, so a server left
    // open hangs the whole test FILE after its last test — and a FAILING
    // assertion is exactly when that happens. Found by mutating this gate: the
    // run stopped reporting instead of reporting a failure.
    hostile.closeAllConnections();
    await new Promise((r) => hostile.close(r));
    rmSync(ctx.scratch, { recursive: true, force: true });
  }
});

test('a pre-existing 0644 webhook-url.txt is REPAIRED, not left as found', async () => {
  // The mode argument to writeFileSync only applies on CREATE. A host that ran
  // the old listener already has a 0644 copy, and it must not survive.
  const scratch2 = mkdtempSync(join(tmpdir(), 'clickup-listen-mode-'));
  try {
    const xdg2 = join(scratch2, 'xdg');
    const state2 = join(xdg2, 'clickup');
    mkdirSync(state2, { recursive: true });
    writeFileSync(join(state2, 'webhook-url.txt'), 'https://webhook.site/stale\n', { mode: 0o644 });
    assert.equal((statSync(join(state2, 'webhook-url.txt')).mode & 0o777).toString(8), '644',
      'fixture precondition: the stale file must start 0644');

    const proc = spawn(process.execPath, [LISTEN, '--mode', 'server', '--port', '0'], {
      env: { ...process.env, HOME: scratch2, XDG_STATE_HOME: xdg2,
        CLICKUP_WEBHOOK_SITE_TOKEN: FIXTURE_TOKEN, WEBHOOK_SITE_API_KEY: '' },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    proc.stderr.setEncoding('utf8');
    let buf = '';
    proc.stderr.on('data', (d) => { buf += d; });
    try {
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline && !/Listening on /.test(buf)) await sleep(50);
      assert.ok(/Listening on /.test(buf), `second listener never started:\n${redact(buf)}`);
      assert.equal((statSync(join(state2, 'webhook-url.txt')).mode & 0o777).toString(8), '600',
        'a pre-existing 0644 webhook-url.txt was left group/world readable');
    } finally {
      proc.kill('SIGKILL');
    }
  } finally {
    rmSync(scratch2, { recursive: true, force: true });
  }
});
