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
