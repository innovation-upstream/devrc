/**
 * The webhook receiver's AUTHENTICATION and REQUEST HANDLING.
 *
 * Split out of listen.mjs so it can be exercised without a webhook.site token,
 * without credentials, and without ClickUp: listen.mjs is a script that reads
 * config and `process.exit(1)`s at module load when no token is configured, so
 * nothing in it was reachable from a test. It had ZERO coverage, which is how
 * all four of these defects survived a review:
 *
 *   1. the signature was COMPUTED and stored as a FIELD (`verified`) that no
 *      branch ever read, so a forged or unsigned event was 200-ack'd, written
 *      to the log, advanced the cursor, and was printed as the payload an agent
 *      then acts on. A field in a DTO is not a guard; only a branch on it is.
 *   2. `server.listen(PORT)` binds 0.0.0.0 — reachable from the LAN and from
 *      nebula. It is a loopback sidecar; it binds loopback.
 *   3. an unauthenticated GET returned `webhook_url`, which embeds the
 *      webhook.site capability token: anyone who can reach the port learns the
 *      URL that receives (and can be used to forge) this workspace's events.
 *   4. the HMAC comparison used `===`.
 *
 * 🔴 FAIL CLOSED. `authenticateDelivery()` returns accepted:false for EVERY
 * case it cannot positively verify — no signature header, no `webhook_id`, no
 * registered secret for that id, or a mismatching digest. There is deliberately
 * no "unverifiable, so allow it" path and no override flag: an event this
 * process cannot authenticate is indistinguishable from one an attacker wrote,
 * and the consumer is an agent that acts on the payload. Every webhook the
 * skill creates stores its `secret` in watchers.json at creation time
 * (api/webhooks.mjs → addWatcherEntry), so "no registered secret" means the
 * event is not from a webhook this skill owns.
 *
 * This module is deliberately STATELESS and has no filesystem dependency — it
 * takes a `lookupSecret` function and delivery callbacks. That keeps it out of
 * the state-path seam entirely (it never needs lib/paths.mjs).
 */

import crypto from 'crypto';
import http from 'http';

/** The only address this server may bind. */
export const LOOPBACK_HOST = '127.0.0.1';

/**
 * Constant-time comparison of two ASCII digests.
 *
 * `crypto.timingSafeEqual` THROWS on unequal lengths, which would turn a
 * short forged signature into a 500 (and, before the try/catch below existed,
 * into an unhandled exception). Length is not secret — it is visible in the
 * header the caller sent — so an early length check leaks nothing.
 */
export function safeEqualHex(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const ba = Buffer.from(a, 'utf8');
  const bb = Buffer.from(b, 'utf8');
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}

/** The HMAC ClickUp signs a delivery body with. */
export function signBody(rawBody, secret) {
  return crypto.createHmac('sha256', secret).update(rawBody).digest('hex');
}

/**
 * Normalise a header value that may arrive as a string (node's http) or as an
 * array (webhook.site's stored-request API returns headers as arrays).
 */
export function headerValue(v) {
  if (Array.isArray(v)) return v.length ? String(v[0]) : undefined;
  if (typeof v === 'string') return v;
  return undefined;
}

/**
 * Read one header CASE-INSENSITIVELY, array-aware.
 *
 * node's http lowercases what it receives, so the live path never needed this.
 * webhook.site's stored-request API returns whatever case the sender used, and
 * an exact-case bracket lookup there is a silent fail-closed bug: every stored
 * event rejects as `missing-signature` and catch-up delivers nothing, which
 * looks exactly like "no missed events". Case-insensitivity is also what makes
 * `headers['X-Signature']` a MUTANT rather than a spelling variant — the
 * catch-up tests feed both spellings, so a bracket access in either case dies.
 */
export function headerLookup(headers, name) {
  if (!headers || typeof headers !== 'object') return undefined;
  const want = String(name).toLowerCase();
  for (const key of Object.keys(headers)) {
    if (key.toLowerCase() === want) return headerValue(headers[key]);
  }
  return undefined;
}

/**
 * Decide whether a delivery may be acted on. THE one predicate — the live POST
 * path and the webhook.site catch-up path both call it, because an event read
 * back from webhook.site is exactly as forgeable as one POSTed here (anyone can
 * POST to a webhook.site URL; the catch-up path then reads it back).
 *
 * @param {string} rawBody   the bytes the signature is over
 * @param {string|string[]|undefined} signature  the X-Signature header
 * @param {(webhookId: string) => (string|null|undefined)} lookupSecret
 * @returns {{accepted: boolean, reason: string, status: number, event?: object}}
 */
export function authenticateDelivery(rawBody, signature, lookupSecret) {
  let event;
  try {
    event = JSON.parse(rawBody);
  } catch {
    return { accepted: false, reason: 'invalid-json', status: 400 };
  }
  if (!event || typeof event !== 'object') {
    return { accepted: false, reason: 'invalid-json', status: 400 };
  }

  const sig = headerValue(signature);
  if (!sig) {
    return { accepted: false, reason: 'missing-signature', status: 401, event };
  }
  const webhookId = event.webhook_id;
  if (!webhookId) {
    return { accepted: false, reason: 'no-webhook-id', status: 401, event };
  }
  const secret = lookupSecret(webhookId);
  if (!secret) {
    return { accepted: false, reason: 'unknown-webhook', status: 401, event };
  }
  if (!safeEqualHex(signBody(rawBody, secret), sig)) {
    return { accepted: false, reason: 'bad-signature', status: 401, event };
  }
  return { accepted: true, reason: 'verified', status: 200, event };
}

/**
 * The HTTP request handler.
 *
 * @param {object} deps
 * @param {(webhookId: string) => (string|null|undefined)} deps.lookupSecret
 * @param {(event: object, rawBody: string) => void} deps.onAccepted
 *        called ONLY for a positively verified delivery. Logging, the cursor
 *        and delivery to the agent all hang off this — a rejected delivery
 *        must leave no trace beyond a stderr note.
 * @param {(result: object, rawBody: string) => void} [deps.onRejected]
 * @param {number} [deps.maxBodyBytes]
 */
export function createRequestHandler({
  lookupSecret,
  onAccepted,
  onRejected = () => {},
  maxBodyBytes = 5 * 1024 * 1024,
}) {
  return function handle(req, res) {
    if (req.method === 'GET') {
      // 🔴 NO webhook_url here. It embeds the webhook.site capability token;
      // this endpoint is unauthenticated by construction.
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'listening' }));
      return;
    }

    if (req.method !== 'POST') {
      res.writeHead(404);
      res.end('Not found');
      return;
    }

    let rawBody = '';
    let aborted = false;
    req.on('data', (chunk) => {
      if (aborted) return;
      rawBody += chunk;
      if (rawBody.length > maxBodyBytes) {
        aborted = true;
        res.writeHead(413, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'body-too-large' }));
        req.destroy();
      }
    });
    req.on('end', () => {
      if (aborted) return;
      // Same extraction helper as the catch-up path — one spelling of the
      // header name, in one place, for both callers of the one predicate.
      const result = authenticateDelivery(
        rawBody, headerLookup(req.headers, 'x-signature'), lookupSecret);
      if (!result.accepted) {
        res.writeHead(result.status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: result.reason }));
        onRejected(result, rawBody);
        return;
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true }));
      onAccepted(result.event, rawBody);
    });
  };
}

/** An http.Server wired to {@link createRequestHandler}. */
export function createWebhookServer(deps) {
  return http.createServer(createRequestHandler(deps));
}

/**
 * Bind LOOPBACK ONLY. `server.listen(port)` with no host binds every
 * interface, which on these hosts means the LAN address and the nebula
 * address as well — a webhook sidecar has no business being reachable there.
 *
 * @returns {Promise<import('net').AddressInfo>} the address actually bound
 */
export function listenLoopback(server, port) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, LOOPBACK_HOST, () => resolve(server.address()));
  });
}

/**
 * The argv for `whcli forward`, targeting the address this module BINDS.
 *
 * 🔴 The target is `LOOPBACK_HOST` — the same constant `listenLoopback()` binds
 * — and not `localhost`. `localhost` resolves through the host's name lookup and
 * can yield `::1` first; the receiver binds 127.0.0.1 ONLY, so a `::1` target is
 * ECONNREFUSED on every delivery, i.e. a listener that reports healthy and
 * silently receives nothing. Deriving both from one constant is what makes that
 * a relationship the tests can pin rather than two literals that agree today.
 *
 * @param {{token: string, port: number, apiKey?: string}} opts
 * @returns {string[]}
 */
export function forwarderArgs({ token, port, apiKey }) {
  const args = [
    'forward',
    `--token=${token}`,
    `--target=${forwarderTarget(port)}`,
    '--listen-timeout=5',
  ];
  if (apiKey) args.push(`--api-key=${apiKey}`);
  return args;
}

/** The URL whcli forwards to. Loggable — it carries no token. */
export function forwarderTarget(port) {
  return `http://${LOOPBACK_HOST}:${port}`;
}
