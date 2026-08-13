/**
 * WHERE stored requests are fetched from, and WHAT CREDENTIALS may travel there.
 *
 * 🔴 `CLICKUP_WEBHOOK_SITE_API_BASE` exists so the catch-up path can be
 * exercised end-to-end against a loopback stub — it had no test at all, which is
 * how two mutations reinstating the original defect survived the whole suite.
 * Ungated, it is also a CREDENTIAL-EGRESS and PLAINTEXT-DOWNGRADE knob, and the
 * comment that shipped with it cleared it of a risk it never had (delivery
 * integrity — every fetched request is still authenticated) while saying nothing
 * about the risk it does have:
 *
 *   * the webhook.site token is IN THE URL PATH (`/token/<token>/requests`), so
 *     whatever host the variable names is handed a capability that can read this
 *     workspace's entire event stream and POST forged events into it;
 *   * `WEBHOOK_SITE_API_KEY` rides along as an `Api-Key` header — an account
 *     credential, not a per-workspace one;
 *   * the client is chosen by `url.protocol`, so `http://` sends both in
 *     cleartext.
 *
 * So the override is gated to the two origins that have a reason to exist, and
 * the API key is attached ONLY to webhook.site itself:
 *
 *   loopback (127.x, ::1, localhost)  allowed, http or https, and NO Api-Key —
 *                                     a test stub has no use for one, and this
 *                                     is what makes "the tests can point it
 *                                     somewhere" cost nothing in credentials.
 *   webhook.site over https           allowed, Api-Key attached. This is the
 *                                     default and the only real destination.
 *   anything else                     REFUSED — the default base is used
 *                                     instead and the refusal is printed. That
 *                                     includes http://webhook.site, which is a
 *                                     downgrade of the real destination.
 *
 * Refusing by falling back (rather than exiting) keeps a mis-set variable from
 * bricking the listener, and the fallback is the SAFE endpoint, never the named
 * one.
 */

export const DEFAULT_API_BASE = 'https://webhook.site';

/**
 * A cap on the stored-requests response. `data += chunk` with no ceiling is an
 * unbounded allocation driven by whatever the base host chooses to send.
 */
export const MAX_RESPONSE_BYTES = 5 * 1024 * 1024;

/** Hosts that are this machine, in the spellings a URL can carry them. */
function isLoopbackHost(hostname) {
  const h = String(hostname).toLowerCase().replace(/^\[|\]$/g, '');
  if (h === 'localhost' || h === '::1' || h === '0:0:0:0:0:0:0:1') return true;
  return /^127(\.\d{1,3}){3}$/.test(h);
}

/** webhook.site itself, or a subdomain of it — never a lookalike suffix. */
function isWebhookSiteHost(hostname) {
  const h = String(hostname).toLowerCase();
  return h === 'webhook.site' || h.endsWith('.webhook.site');
}

/**
 * Decide the stored-request API base and whether the API key may be sent.
 *
 * Pure: no env read, no IO, no process.exit — so both branches are testable and
 * listen.mjs holds no copy of the policy.
 *
 * @param {string|undefined|null} raw  the value of CLICKUP_WEBHOOK_SITE_API_BASE
 * @param {{defaultBase?: string}} [opts]
 * @returns {{base: string, sendApiKey: boolean, warning: string|null}}
 */
export function resolveApiBase(raw, { defaultBase = DEFAULT_API_BASE } = {}) {
  const fallback = (warning) => ({ base: defaultBase, sendApiKey: true, warning });
  if (raw === undefined || raw === null || String(raw).trim() === '') {
    return { base: defaultBase, sendApiKey: true, warning: null };
  }

  let url;
  try {
    url = new URL(String(raw).trim());
  } catch {
    // 🔴 Parsed HERE and not inside the fetch: `new URL()` throwing inside a
    // promise executor rejects a promise nothing awaits, which on this script
    // (main() is called, not awaited) is an unhandled rejection rather than a
    // message.
    return fallback(`CLICKUP_WEBHOOK_SITE_API_BASE is not a URL — ignoring it and using ${defaultBase}`);
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return fallback(
      `CLICKUP_WEBHOOK_SITE_API_BASE uses ${url.protocol} — ignoring it and using ${defaultBase}`);
  }

  const base = url.origin + url.pathname.replace(/\/+$/, '');

  if (isLoopbackHost(url.hostname)) {
    // No Api-Key: a loopback stub has no account to authenticate against, and
    // not sending it is what keeps this override out of the credential path.
    return { base, sendApiKey: false, warning: null };
  }

  if (isWebhookSiteHost(url.hostname) && url.protocol === 'https:') {
    return { base, sendApiKey: true, warning: null };
  }

  return fallback(
    `CLICKUP_WEBHOOK_SITE_API_BASE points at ${url.protocol}//${url.host}. The webhook.site ` +
      'token is in the request PATH and the API key rides as a header, so that host would ' +
      `receive both. Refusing it — using ${defaultBase}. Only loopback (a test stub) or ` +
      'https://webhook.site are accepted.');
}
