/**
 * ClickUp Internal API client
 *
 * Uses JWT authentication (extracted via browser login) to access
 * internal ClickUp endpoints that aren't available through the public API.
 *
 * Currently used for:
 * - Document comments (comment-service v3)
 *
 * JWT management is handled by lib/jwt.mjs (auto-refresh via browser automation).
 */

import { getActiveCredentials } from './client.mjs';

const REQUEST_TIMEOUT_MS = 30000;
const MAX_RETRIES = 1;

/**
 * Get the JWT from active credentials, with validation
 * @returns {{ jwt: string, baseUrl: string, workspaceId: string }}
 */
function getInternalConfig() {
  const creds = getActiveCredentials();

  const jwt = creds.jwt;
  const baseUrl = creds.internalApiUrl;
  const workspaceId = creds.teamId;

  if (!jwt) {
    throw new Error(
      'JWT not configured for this account. Run: node lib/jwt.mjs --refresh\n' +
      'This requires email and password in accounts.json'
    );
  }

  if (!baseUrl) {
    throw new Error(
      'internalApiUrl not configured for this account.\n' +
      'Add to accounts.json or run: node lib/jwt.mjs --refresh'
    );
  }

  // Check JWT expiry
  try {
    const payload = JSON.parse(Buffer.from(jwt.split('.')[1], 'base64').toString());
    const expiresAt = payload.exp * 1000;
    if (Date.now() >= expiresAt) {
      throw new Error(
        'JWT has expired. Run: node lib/jwt.mjs --refresh'
      );
    }
  } catch (e) {
    if (e.message.includes('expired') || e.message.includes('JWT')) throw e;
    throw new Error('JWT is malformed. Run: node lib/jwt.mjs --refresh');
  }

  return { jwt, baseUrl, workspaceId };
}

/**
 * Make a request to the internal ClickUp API
 * @param {string} endpoint - Path after the base URL (e.g., /comment-service/v3/...)
 * @param {object} options - Fetch options (method, body, headers)
 * @returns {Promise<object>} - Parsed JSON response
 */
export async function internalApiRequest(endpoint, options = {}) {
  const { jwt, baseUrl, workspaceId } = getInternalConfig();
  const url = `${baseUrl}${endpoint}`;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'Authorization': `Bearer ${jwt}`,
        'Content-Type': 'application/json',
        'x-workspace-id': workspaceId,
        'x-csrf': '1',
        'accept': 'application/json',
        ...options.headers,
      },
    });

    if (response.status === 401) {
      throw new Error(
        'JWT authentication failed (401). Token may be expired.\n' +
        'Run: node lib/jwt.mjs --refresh'
      );
    }

    if (response.status === 429 && MAX_RETRIES > 0) {
      const waitMs = 5000;
      console.error(`Rate limited. Waiting ${waitMs / 1000}s before retry...`);
      await new Promise(r => setTimeout(r, waitMs));
      return internalApiRequest(endpoint, options);
    }

    if (!response.ok) {
      const text = await response.text();
      const err = new Error(`Internal API error: ${response.status} - ${text.slice(0, 200)}`);
      err.status = response.status;
      throw err;
    }

    const text = await response.text();
    if (!text) return {};
    return JSON.parse(text);
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error(`Internal API request timed out after ${REQUEST_TIMEOUT_MS / 1000}s`);
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Get the workspace ID from active credentials
 */
export function getWorkspaceId() {
  const creds = getActiveCredentials();
  return creds.teamId;
}
