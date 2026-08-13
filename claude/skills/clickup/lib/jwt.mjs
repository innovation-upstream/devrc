#!/usr/bin/env node
/**
 * ClickUp JWT Management
 *
 * Automates browser login to extract session JWTs for internal ClickUp APIs.
 * Uses the browser-automation skill to handle Playwright sessions.
 *
 * Usage:
 *   node jwt.mjs              # Get JWT (from cache or fresh login)
 *   node jwt.mjs --refresh    # Force fresh login
 *   node jwt.mjs --status     # Check cached JWT status
 *   node jwt.mjs --headless   # Use headless browser
 *   node jwt.mjs --account x  # Use specific account
 *
 * Credentials are read from accounts.json (email, password fields).
 */

import { execSync } from 'child_process';
import { readFileSync, writeFileSync, existsSync, mkdirSync, chmodSync } from 'fs';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import { getAccountCredentials, updateAccountField, getDefaultAccount } from './accounts.mjs';
import { SKILL_DIR, cacheDir, ensureStateDir } from './paths.mjs';

const BROWSER_CLI = resolve(process.env.HOME || process.env.USERPROFILE, '.claude/skills/browser-automation/cli.mjs');

// Refresh 1 hour before expiry to avoid edge cases
const EXPIRY_BUFFER_MS = 60 * 60 * 1000;

// ============================================================================
// Per-account cache
// ============================================================================

function getCacheFile(accountId) {
  return resolve(cacheDir(), `jwt-cache-${accountId}.json`);
}

function readCache(accountId) {
  ensureStateDir();
  const file = getCacheFile(accountId);
  if (!existsSync(file)) return null;
  try {
    return JSON.parse(readFileSync(file, 'utf-8'));
  } catch {
    return null;
  }
}

function writeCache(accountId, data) {
  ensureStateDir();
  const dir = cacheDir();
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
    try { chmodSync(dir, 0o700); } catch { /* best effort */ }
  }
  const file = getCacheFile(accountId);
  writeFileSync(file, JSON.stringify(data, null, 2));
  // The cache holds a live session JWT — keep it owner-only.
  try { chmodSync(file, 0o600); } catch { /* Windows may not support */ }
}

// ============================================================================
// JWT utilities
// ============================================================================

function decodeJwtPayload(jwt) {
  const parts = jwt.split('.');
  if (parts.length !== 3) return null;
  try {
    return JSON.parse(Buffer.from(parts[1], 'base64').toString());
  } catch {
    return null;
  }
}

function isJwtValid(jwt) {
  const payload = decodeJwtPayload(jwt);
  if (!payload?.exp) return false;
  const expiresAt = payload.exp * 1000;
  return Date.now() < (expiresAt - EXPIRY_BUFFER_MS);
}

function getJwtStatus(jwt) {
  const payload = decodeJwtPayload(jwt);
  if (!payload) return { valid: false, error: 'Invalid JWT' };

  const expiresAt = payload.exp * 1000;
  const now = Date.now();
  const remainingMs = expiresAt - now;
  const remainingHours = Math.round(remainingMs / (60 * 60 * 1000) * 10) / 10;

  return {
    valid: now < (expiresAt - EXPIRY_BUFFER_MS),
    user: payload.user,
    issuedAt: new Date(payload.iat * 1000).toISOString(),
    expiresAt: new Date(expiresAt).toISOString(),
    remainingHours,
    sessionToken: payload.session_token,
  };
}

// ============================================================================
// Browser automation
// ============================================================================

function browser(args, timeoutMs = 30000) {
  const cmd = `node "${BROWSER_CLI}" ${args}`;
  try {
    const result = execSync(cmd, {
      timeout: timeoutMs,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, CLAUDE_PROJECT_DIR: resolve(SKILL_DIR, '../..', '..') },
    });
    return result.trim();
  } catch (err) {
    throw new Error(`Browser command failed: ${err.stderr || err.message}`);
  }
}

function browserJson(args, timeoutMs = 30000) {
  const result = browser(`${args} --json`, timeoutMs);
  try {
    return JSON.parse(result);
  } catch {
    return { raw: result };
  }
}

async function extractJwtViaBrowser(accountId, headless = false) {
  const creds = getAccountCredentials(accountId);
  const email = creds.email;
  const password = creds.password;

  if (!email || !password) {
    throw new Error(
      `Account "${accountId}" needs email and password for JWT extraction.\n` +
      'Add them to accounts.json:\n' +
      `  "email": "your@email.com",\n` +
      `  "password": "yourpassword"`
    );
  }

  const sessionName = 'clickup-jwt-' + Date.now();
  const headlessFlag = headless ? ' --headless' : '';

  try {
    // 1. Create session and navigate to login
    console.error('Opening ClickUp login page...');
    browser(`session "https://app.clickup.com/login" --name ${sessionName}${headlessFlag}`, 30000);

    // 2. Fill credentials
    console.error('Filling credentials...');
    browser(`fill-form "Work email=${email}" "Password=${password}" -s ${sessionName}`, 15000);

    // 3. Click login
    console.error('Submitting login...');
    browser(`click "button:has-text('Log In')" -s ${sessionName}`, 15000);

    // 4. Wait for dashboard to load
    console.error('Waiting for dashboard...');
    browser(`run "await page.waitForTimeout(4000);" --label "Wait for login redirect" -s ${sessionName}`, 15000);

    // 5. Extract JWT from cookies
    console.error('Extracting JWT...');
    const cookieCode = `
      const cookies = await page.context().cookies();
      const cuJwt = cookies.find(c => c.name === 'cu_jwt');
      const cuFormJwt = cookies.find(c => c.name === 'cu_form_jwt');
      return JSON.stringify({
        cu_jwt: cuJwt?.value || null,
        cu_form_jwt: cuFormJwt?.value || null,
      });
    `.replace(/\n/g, ' ').trim();

    const result = browserJson(`run "${cookieCode.replace(/"/g, '\\"')}" --label "Extract JWT" -s ${sessionName}`, 15000);

    let tokens;
    try {
      tokens = JSON.parse(result.returnValue);
    } catch {
      throw new Error('Failed to parse JWT from browser cookies');
    }

    if (!tokens.cu_jwt) {
      // Check if we're still on login page (wrong credentials?)
      const status = browserJson(`status -s ${sessionName}`, 10000);
      const currentUrl = status.url || '';
      if (currentUrl.includes('/login')) {
        throw new Error('Login failed - still on login page. Check credentials.');
      }
      throw new Error('JWT cookie not found after login');
    }

    // 6. Capture frontdoor URL from network traffic
    let frontdoorUrl = null;
    console.error('Checking network logs for internal API URL...');
    try {
      const netResult = browserJson(`network -s ${sessionName}`, 10000);
      const logs = netResult.logs || [];
      for (const log of logs) {
        const url = log.url || '';
        const match = url.match(/^(https:\/\/frontdoor[^/]+\.clickup\.com)/);
        if (match) {
          frontdoorUrl = match[1];
          break;
        }
      }
    } catch {
      // Non-fatal - we have a default in accounts.json
    }

    // 7. Save auth profile
    console.error('Saving auth profile...');
    try {
      browser(`save-auth clickup-${accountId} "ClickUp ${accountId} JWT session" -s ${sessionName}`, 10000);
    } catch {
      // Non-fatal - profile save is a convenience
    }

    return { ...tokens, frontdoorUrl };
  } finally {
    // Always close the session
    try {
      browser(`close ${sessionName}`, 10000);
    } catch {
      // Ignore close errors
    }
  }
}

// ============================================================================
// Public API
// ============================================================================

export async function getJwt({ refresh = false, headless = false, accountId } = {}) {
  const resolvedId = accountId || getDefaultAccount() || 'default';

  // Check cache first (unless forcing refresh)
  if (!refresh) {
    const cache = readCache(resolvedId);
    if (cache?.cu_jwt && isJwtValid(cache.cu_jwt)) {
      return cache.cu_jwt;
    }
  }

  // Need fresh JWT
  const tokens = await extractJwtViaBrowser(resolvedId, headless);

  // Cache it
  const payload = decodeJwtPayload(tokens.cu_jwt);
  writeCache(resolvedId, {
    cu_jwt: tokens.cu_jwt,
    cu_form_jwt: tokens.cu_form_jwt,
    user: payload?.user,
    extractedAt: new Date().toISOString(),
    expiresAt: payload?.exp ? new Date(payload.exp * 1000).toISOString() : null,
    frontdoorUrl: tokens.frontdoorUrl,
  });

  // Persist JWT to accounts.json
  updateAccountField(resolvedId, 'jwt', tokens.cu_jwt);

  // Update frontdoor URL if captured and different
  if (tokens.frontdoorUrl) {
    updateAccountField(resolvedId, 'internalApiUrl', tokens.frontdoorUrl);
  }

  return tokens.cu_jwt;
}

export function getCachedJwt(accountId) {
  const resolvedId = accountId || getDefaultAccount() || 'default';
  const cache = readCache(resolvedId);
  if (!cache?.cu_jwt) return null;
  if (!isJwtValid(cache.cu_jwt)) return null;
  return cache.cu_jwt;
}

export function getJwtInfo(accountId) {
  const resolvedId = accountId || getDefaultAccount() || 'default';
  const cache = readCache(resolvedId);
  if (!cache?.cu_jwt) return { cached: false };
  return {
    cached: true,
    ...getJwtStatus(cache.cu_jwt),
    extractedAt: cache.extractedAt,
  };
}

// ============================================================================
// CLI
// ============================================================================

async function main() {
  const args = process.argv.slice(2);
  const flags = new Set(args);

  // Parse --account flag
  let accountId = null;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--account' && args[i + 1]) {
      accountId = args[i + 1];
      break;
    }
  }

  if (flags.has('--status')) {
    const info = getJwtInfo(accountId);
    if (!info.cached) {
      console.log(`No cached JWT found${accountId ? ` for account "${accountId}"` : ''}`);
    } else {
      if (accountId) console.log(`Account: ${accountId}`);
      console.log(`User: ${info.user}`);
      console.log(`Valid: ${info.valid}`);
      console.log(`Issued: ${info.issuedAt}`);
      console.log(`Expires: ${info.expiresAt}`);
      console.log(`Remaining: ${info.remainingHours} hours`);
      console.log(`Extracted: ${info.extractedAt}`);
    }
    return;
  }

  const refresh = flags.has('--refresh');
  const headless = flags.has('--headless');

  try {
    const jwt = await getJwt({ refresh, headless, accountId });
    // Output just the JWT to stdout for piping
    console.log(jwt);
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}

// Run CLI if called directly
const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (isMain) {
  main();
}
