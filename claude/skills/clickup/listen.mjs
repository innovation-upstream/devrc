#!/usr/bin/env node

/**
 * ClickUp Event Listener (webhook.site + local receiver)
 *
 * Receives ClickUp webhook events via webhook.site forwarding.
 * Designed to be run as a background task so the agent gets notified
 * when a watched event arrives (task status change, comment, etc.).
 *
 * KEY FEATURE: On startup, checks webhook.site API for events that
 * arrived since last seen timestamp. Prevents gaps between restarts.
 *
 * 🔴 EVERY delivery must carry a valid HMAC signature from a webhook this
 * skill registered (watchers.json). Unsigned, unknown-webhook and
 * wrongly-signed deliveries are REJECTED (401): not acked, not logged, the
 * cursor does not move, and nothing is printed for the agent to act on. The
 * predicate lives in lib/webhook-server.mjs and BOTH paths — the live POST and
 * the webhook.site catch-up (lib/catchup.mjs) — go through it, because an event
 * read back from webhook.site is exactly as forgeable as one POSTed here.
 *
 * That used to be asserted HERE, in prose, and by nothing else: the catch-up
 * branch was unreachable from the tests, so two mutations reinstating the
 * original defect passed the whole suite. Both halves now have their own file
 * and their own four-case table (test/{webhook-server,catchup}.test.mjs) plus
 * an end-to-end run of this script against each (test/listen-integration).
 *
 * The local receiver binds 127.0.0.1 only.
 *
 * Modes:
 *   1. "wait" (default) — Check for missed events, then listen for ONE
 *      new webhook, print it, and exit. Agent gets background task notification.
 *
 *   2. "server" — Stay running and log all events.
 *
 * Usage:
 *   node listen.mjs [--since <ISO timestamp>] [--port 3458] [--timeout 600] [--mode wait|server]
 *
 * Configuration (accounts.json > webhookSiteToken):
 *   Or environment: CLICKUP_WEBHOOK_SITE_TOKEN
 *
 * Exit codes:
 *   0 — webhook received (wait mode)
 *   1 — timeout or error
 */

import { spawn } from 'child_process';
import http from 'http';
import https from 'https';
import fs from 'fs';
import {
  accountsPath,
  watchersFile,
  webhookUrlFile,
  webhookLogFile,
  webhookLatestFile,
  lastSeenFile,
  ensureStateDir,
} from './lib/paths.mjs';
import {
  createWebhookServer,
  listenLoopback,
  forwarderArgs,
} from './lib/webhook-server.mjs';
import { catchUp } from './lib/catchup.mjs';

// All of these are STATE, so they resolve under $XDG_STATE_HOME/clickup
// (fallback ~/.local/state/clickup) — the skill dir deploys read-only.
ensureStateDir();
const LOG_FILE = webhookLogFile();
const LATEST_FILE = webhookLatestFile();
const WEBHOOK_URL_FILE = webhookUrlFile();
const LAST_SEEN_FILE = lastSeenFile();
const WATCHERS_FILE = watchersFile();

// ── Load config ───────────────────────────────────────────────────────────

// Try accounts.json first, then env
function loadConfig() {
  const accountsFile = accountsPath();
  if (fs.existsSync(accountsFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(accountsFile, 'utf-8'));
      return {
        webhookSiteToken: data.webhookSiteToken || process.env.CLICKUP_WEBHOOK_SITE_TOKEN,
        webhookSiteApiKey: data.webhookSiteApiKey || process.env.WEBHOOK_SITE_API_KEY,
      };
    } catch {}
  }
  return {
    webhookSiteToken: process.env.CLICKUP_WEBHOOK_SITE_TOKEN,
    webhookSiteApiKey: process.env.WEBHOOK_SITE_API_KEY,
  };
}

const config = loadConfig();
const WH_TOKEN = config.webhookSiteToken;
const WH_API_KEY = config.webhookSiteApiKey;

// ── Parse CLI args ────────────────────────────────────────────────────────

const args = process.argv.slice(2);

function getFlag(name) {
  const idx = args.indexOf(name);
  if (idx === -1 || idx + 1 >= args.length) return undefined;
  return args[idx + 1];
}

const PORT = parseInt(getFlag('--port') || '3458', 10);
const TIMEOUT = parseInt(getFlag('--timeout') || '600', 10) * 1000;
const MODE = getFlag('--mode') || 'wait';

// Filters — only deliver events matching these criteria
const FILTER_STATUS = getFlag('--filter-status');    // e.g. "qa review" — only deliver status changes TO this status
const FILTER_EVENT = getFlag('--filter-event');       // e.g. "taskCommentPosted" — only deliver this event type
const FILTER_TASK = getFlag('--filter-task');          // e.g. "868abc123" — only deliver events for this task

let SINCE = getFlag('--since');
if (!SINCE && fs.existsSync(LAST_SEEN_FILE)) {
  SINCE = fs.readFileSync(LAST_SEEN_FILE, 'utf8').trim();
}

if (!WH_TOKEN) {
  console.error('Error: No webhook.site token configured.');
  console.error(`Add "webhookSiteToken" to ${accountsPath()} or set CLICKUP_WEBHOOK_SITE_TOKEN env var.`);
  console.error('Visit https://webhook.site to get a token (the UUID in the URL).');
  process.exit(1);
}

const WEBHOOK_URL = `https://webhook.site/${WH_TOKEN}`;

// Write the webhook URL so other commands can read it.
// 🔴 0600, and `webhook-url.txt` is in SECRET_FILES: the URL embeds the
// webhook.site capability token, so anyone who can read this file can read
// (and forge posts to) this workspace's event stream. The mode argument only
// applies when the file is CREATED, so chmod unconditionally — a pre-existing
// 0644 copy from the version that omitted the mode must be repaired.
fs.writeFileSync(WEBHOOK_URL_FILE, WEBHOOK_URL, { mode: 0o600 });
try { fs.chmodSync(WEBHOOK_URL_FILE, 0o600); } catch { /* best effort */ }

// ── Watcher secrets for signature verification ────────────────────────────

/**
 * The signing secret for one webhook, read FRESH on every delivery.
 *
 * Deliberately not a snapshot taken at startup: `clickup watch …` registering a
 * new webhook while a listener runs would otherwise make every event from it
 * unverifiable, and the fail-closed policy would drop them all with no way to
 * tell that from an attack. Deliveries are rare; a file read per delivery is
 * cheaper than that failure mode.
 */
function lookupWatcherSecret(webhookId) {
  if (!webhookId || !fs.existsSync(WATCHERS_FILE)) return null;
  try {
    const watchers = JSON.parse(fs.readFileSync(WATCHERS_FILE, 'utf-8'));
    if (!Array.isArray(watchers)) return null;
    const entry = watchers.find((w) => w && w.webhookId === webhookId && w.secret);
    return entry ? entry.secret : null;
  } catch {
    return null;
  }
}

function countWatcherSecrets() {
  if (!fs.existsSync(WATCHERS_FILE)) return 0;
  try {
    const watchers = JSON.parse(fs.readFileSync(WATCHERS_FILE, 'utf-8'));
    return Array.isArray(watchers) ? watchers.filter((w) => w && w.webhookId && w.secret).length : 0;
  } catch {
    return 0;
  }
}

// ── Event Filtering ───────────────────────────────────────────────────────

function matchesFilters(event) {
  // Filter by event type
  if (FILTER_EVENT) {
    const allowed = FILTER_EVENT.split(',').map(s => s.trim().toLowerCase());
    const eventType = (event.event || '').toLowerCase();
    if (!allowed.includes(eventType)) return false;
  }

  // Filter by task ID
  if (FILTER_TASK) {
    const allowed = FILTER_TASK.split(',').map(s => s.trim());
    if (!allowed.includes(event.task_id)) return false;
  }

  // Filter by target status (only deliver taskStatusUpdated where after.status matches)
  if (FILTER_STATUS) {
    const allowed = FILTER_STATUS.split(',').map(s => s.trim().toLowerCase());
    const items = event.history_items || [];
    // For status updates, check the "after" status
    const hasMatch = items.some(item => {
      const afterStatus = (item.after?.status || '').toLowerCase();
      return allowed.includes(afterStatus);
    });
    // For non-status events, FILTER_STATUS doesn't apply — let them through
    if (event.event === 'taskStatusUpdated' && !hasMatch) return false;
  }

  return true;
}

// ── Helpers ───────────────────────────────────────────────────────────────

function saveLastSeen(timestamp) {
  fs.writeFileSync(LAST_SEEN_FILE, timestamp);
}

// `verified` is now an INVARIANT of anything that reaches here, not a report:
// processEvent() is only ever called for a delivery authenticateDelivery()
// accepted. It stays in the log entry so an existing log stays readable.
function processEvent(event, source = 'live', { writeLatest = true } = {}) {
  const entry = {
    received_at: new Date().toISOString(),
    source,
    verified: true,
    ...event,
  };

  fs.appendFileSync(LOG_FILE, JSON.stringify(entry) + '\n');
  // The live path defers this until AFTER the filter check, exactly as before —
  // webhook-latest.json is the "what you were waiting for" file.
  if (writeLatest) fs.writeFileSync(LATEST_FILE, JSON.stringify(entry, null, 2));

  if (entry._wh_created_at) {
    saveLastSeen(entry._wh_created_at);
  } else {
    saveLastSeen(entry.received_at);
  }

  return entry;
}

function formatEventSummary(event) {
  const type = event.event || 'unknown';
  const taskId = event.task_id || '?';
  const webhookId = event.webhook_id || '?';
  const items = event.history_items || [];
  let detail = '';

  if (type === 'taskStatusUpdated' && items.length > 0) {
    const item = items[0];
    detail = ` | ${item.before?.status || '?'} → ${item.after?.status || '?'}`;
  } else if (type === 'taskCommentPosted' && items.length > 0) {
    const user = items[0]?.user?.username || '?';
    detail = ` | by ${user}`;
  } else if (items.length > 0) {
    const user = items[0]?.user?.username;
    if (user) detail = ` | by ${user}`;
  }

  return `${type} | task:${taskId} | wh:${webhookId}${detail}`;
}

// ── Catch-up: query webhook.site for missed events ────────────────────────

/**
 * Where the stored-request API lives.
 *
 * 🔴 The override exists so the catch-up path can be exercised end-to-end
 * against a loopback stub — it had NO test at all, which is why two mutations
 * reinstating the original defect survived the whole suite. It changes WHERE
 * stored requests come from, never WHETHER they are authenticated: every
 * request from it still goes through `authenticateDelivery()` against a secret
 * in watchers.json, so pointing this at a hostile server yields rejections, not
 * forged deliveries.
 */
const WH_API_BASE = process.env.CLICKUP_WEBHOOK_SITE_API_BASE || 'https://webhook.site';

function fetchMissedEvents(since) {
  return new Promise((resolve, reject) => {
    const sinceUtc = since.includes('Z') || since.includes('+') ? since : since.replace(' ', 'T') + 'Z';
    const d = new Date(sinceUtc);
    if (d.getMilliseconds() > 0) {
      d.setSeconds(d.getSeconds() + 1);
      d.setMilliseconds(0);
    }
    d.setSeconds(d.getSeconds() + 1);
    const dateFrom = d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, '');

    const url = new URL(`${WH_API_BASE}/token/${WH_TOKEN}/requests`);
    url.searchParams.set('date_from', dateFrom);
    url.searchParams.set('sorting', 'oldest');
    url.searchParams.set('per_page', '10');

    const headers = {};
    if (WH_API_KEY) headers['Api-Key'] = WH_API_KEY;

    const client = url.protocol === 'http:' ? http : https;
    client.get(url.toString(), { headers }, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve(json);
        } catch {
          resolve({ data: [] });
        }
      });
    }).on('error', (err) => {
      process.stderr.write(`[catch-up] Failed to query webhook.site: ${err.message}\n`);
      resolve({ data: [] });
    });
  });
}

// The catch-up walk — authentication, the decidable/undecidable split and the
// cursor policy — lives in lib/catchup.mjs so it can be exercised. See its
// header: this code was correct by reading and reachable by nothing.

// ── Local HTTP server ─────────────────────────────────────────────────────

let serverResolve;

const server = createWebhookServer({
  lookupSecret: lookupWatcherSecret,

  // 🔴 Reached ONLY by a delivery whose HMAC verified against a registered
  // watcher secret. Everything with a side effect — the log, the cursor, the
  // latest-event file, the payload printed for the agent — hangs off here, so a
  // rejected delivery leaves no trace at all.
  onAccepted(event) {
    const entry = processEvent(event, 'live', { writeLatest: false });

    // Check filters — only deliver to agent if event matches
    if (!matchesFilters(event)) {
      process.stderr.write(`[filtered] ${formatEventSummary(event)}\n`);
      return;
    }

    fs.writeFileSync(LATEST_FILE, JSON.stringify(entry, null, 2));

    if (MODE === 'server') {
      process.stderr.write(`[${entry.received_at}] ${formatEventSummary(event)}\n`);
    } else {
      console.log(JSON.stringify(entry, null, 2));
      if (serverResolve) serverResolve();
    }
  },

  // Reason and size ONLY. A rejected body is attacker-controlled: it does not
  // go in the log file and it is not echoed back into this process's stderr.
  onRejected(result, rawBody) {
    process.stderr.write(
      `[rejected:${result.reason}] ${Buffer.byteLength(rawBody)} bytes — not logged, ` +
        'cursor unchanged\n'
    );
  },
});

// ── whcli forward process ─────────────────────────────────────────────────

// `port` is the port actually BOUND, not the requested one (`--port 0` asks the
// OS to choose). The target is built by lib/webhook-server.mjs from the SAME
// constant it binds — `localhost` can resolve to ::1 first, and the receiver
// binds 127.0.0.1 only, so a `localhost` target is a listener that reports
// healthy and receives nothing. The target is printed (it carries no token, and
// the args do) so the integration test reads back the value actually spawned
// rather than a second copy of it.
function startForwarder(port) {
  const whArgs = forwarderArgs({ token: WH_TOKEN, port, apiKey: WH_API_KEY });
  // Read back out of the array being spawned, not recomputed: a second call to
  // forwarderTarget() would print 127.0.0.1 even if the arg said localhost.
  // 🔴 This one element only — `--token=` is in there too.
  const target = (whArgs.find((a) => a.startsWith('--target=')) || '--target=<none>').slice(9);
  process.stderr.write(`[whcli] target ${target}\n`);

  const proc = spawn('whcli', whArgs, {
    stdio: ['pipe', 'pipe', 'pipe'],
    shell: true,
  });

  proc.stdout.on('data', (d) => process.stderr.write(`[whcli] ${d}`));
  proc.stderr.on('data', (d) => process.stderr.write(`[whcli:err] ${d}`));
  proc.on('error', (err) => process.stderr.write(`[whcli] Error: ${err.message}\n`));

  return proc;
}

// ── Main ──────────────────────────────────────────────────────────────────

async function main() {
  process.stderr.write(`ClickUp Event Listener\n`);
  process.stderr.write(`Webhook URL: ${WEBHOOK_URL}\n`);
  process.stderr.write(`Mode: ${MODE} | Port: ${PORT} | Timeout: ${TIMEOUT / 1000}s\n`);
  process.stderr.write(`Since: ${SINCE || 'none (first run)'}\n`);
  process.stderr.write(`Tracked webhooks: ${countWatcherSecrets()}\n`);
  if (FILTER_STATUS) process.stderr.write(`Filter status: ${FILTER_STATUS}\n`);
  if (FILTER_EVENT) process.stderr.write(`Filter event: ${FILTER_EVENT}\n`);
  if (FILTER_TASK) process.stderr.write(`Filter task: ${FILTER_TASK}\n`);
  process.stderr.write('\n');

  // ── Step 1: Check for missed events ─────────────────────────────────────
  if (SINCE && MODE === 'wait') {
    process.stderr.write(`Checking webhook.site for events since ${SINCE}...\n`);
    const result = await fetchMissedEvents(SINCE);
    const requests = result.data || [];

    if (requests.length > 0) {
      const outcome = catchUp(requests, {
        lookupSecret: lookupWatcherSecret,
        matches: matchesFilters,
        // Records it: log line + cursor. Only reached for a delivery that
        // authenticated, exactly as on the live path.
        onAccepted: (event) => processEvent(event, 'catch-up'),
        onDeliver: (entry) => {
          process.stderr.write(
            `[catch-up] Found matching event (of ${requests.length} missed). Delivering.\n`);
          console.log(JSON.stringify(entry, null, 2));
          process.exit(0);
        },
        onFiltered: (event) => {
          process.stderr.write(`[catch-up:filtered] ${event.event || 'unknown'} — skipped\n`);
        },
        // A PERMANENTLY unverifiable stored request. Nothing about it is
        // logged — the body is attacker-controlled — but the cursor moves past
        // it, or ten of these wedge catch-up forever (see lib/catchup.mjs).
        onSkipped: ({ reason, createdAt }) => {
          saveLastSeen(createdAt);
          process.stderr.write(
            `[catch-up:rejected:${reason}] a stored webhook.site request did not verify ` +
              `— not logged, cursor advanced past it (${createdAt})\n`
          );
        },
        // We could not decide what this was. The cursor stays put.
        onBlocked: ({ reason, createdAt }) => {
          process.stderr.write(
            `[catch-up:blocked:${reason}] a stored webhook.site request could not be ` +
              `parsed or dated (${createdAt || 'no timestamp'}) — catch-up stops here and the ` +
              'cursor is unchanged, so nothing behind it is skipped silently. Pass an ' +
              'explicit --since to step over it.\n'
          );
        },
      });

      process.stderr.write(
        `[catch-up] ${outcome.considered} of ${requests.length} stored request(s) examined, ` +
          `${outcome.skipped} unverifiable, ${outcome.filtered} filtered. Starting live listener.\n`);
    } else {
      process.stderr.write(`[catch-up] No missed events. Starting live listener.\n`);
    }
  }

  // ── Step 2: Start live listener ─────────────────────────────────────────
  // Loopback only — see lib/webhook-server.mjs. The address printed is the one
  // `server.address()` REPORTS, not the one we asked for: that is the only
  // form of this line that is evidence rather than a claim, and the
  // listen-integration test reads it back.
  const addr = await listenLoopback(server, PORT);
  process.stderr.write(`Listening on ${addr.address}:${addr.port}\n\n`);

  const forwarder = startForwarder(addr.port);

  if (MODE === 'wait') {
    const gotWebhook = new Promise((resolve) => { serverResolve = resolve; });
    const timedOut = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('timeout')), TIMEOUT)
    );

    try {
      await Promise.race([gotWebhook, timedOut]);
      forwarder.kill();
      server.close(() => process.exit(0));
    } catch {
      console.log(JSON.stringify({
        error: 'timeout',
        message: `No ClickUp event received within ${TIMEOUT / 1000}s`,
        webhook_url: WEBHOOK_URL,
      }));
      forwarder.kill();
      server.close(() => process.exit(1));
    }
  } else {
    process.stderr.write('Running in server mode. Press Ctrl+C to stop.\n\n');

    function cleanup() {
      process.stderr.write('\nShutting down...\n');
      forwarder.kill();
      server.close(() => process.exit(0));
    }

    process.on('SIGINT', cleanup);
    process.on('SIGTERM', cleanup);
  }
}

main();
