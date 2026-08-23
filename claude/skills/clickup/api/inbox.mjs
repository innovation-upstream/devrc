/**
 * ClickUp Inbox API (internal)
 *
 * Uses the internal inbox/v3 endpoints to fetch, clear, and mark-read
 * notification bundles. Requires JWT authentication.
 *
 * Endpoints:
 *   POST .../notifications/bundles/search     - Fetch notifications
 *   POST .../notifications/bundles/stats/fetch - Get badge counts
 *   PUT  .../notifications/bundles/{id}/clear  - Clear/dismiss a bundle
 *   PUT  .../notifications/bundles/read        - Mark a bundle as read
 */

import { internalApiRequest, getWorkspaceId } from './internal-client.mjs';

const INBOX_BASE = '/inbox/v3';

/**
 * Fetch inbox notifications
 * @param {object} options
 * @param {'messages'|'activity'} options.bundleType - Tab to fetch (default: 'messages')
 * @param {'uncleared'|'cleared'} options.status - Filter by status (default: 'uncleared')
 * @param {boolean} options.assignedToMe - Filter to assigned notifications
 * @param {boolean} options.mentioned - Filter to @mentions
 * @param {boolean} options.unread - Filter to unread only
 * @param {boolean} options.reminders - Filter to reminders
 * @param {boolean} options.saved - Filter to saved/bookmarked
 * @param {string} options.dateStart - ISO date string, only fetch after this date
 * @param {number} options.limit - Items per page (default: 20)
 * @param {string} options.cursor - Pagination cursor (default: '')
 * @returns {Promise<object>} - { resources, notificationBundleGroups, users }
 */
export async function fetchInboxNotifications(options = {}) {
  const workspaceId = getWorkspaceId();
  const {
    bundleType = 'messages',
    status = 'uncleared',
    assignedToMe = false,
    mentioned = false,
    unread = false,
    reminders = false,
    saved = false,
    dateStart,
    limit = 20,
    cursor = '',
  } = options;

  const body = {
    filteredBy: {
      bundleType,
      status,
      assignedToMe,
      mentioned,
      unread,
      reminders,
      saved,
    },
    pagination: { nextCursor: cursor, limit },
    sortedBy: { direction: 'descending' },
    needsMemberMap: false,
  };

  if (dateStart) {
    body.filteredBy.dateRange = { start: dateStart };
  }

  return internalApiRequest(
    `${INBOX_BASE}/workspaces/${workspaceId}/notifications/bundles/search`,
    { method: 'POST', body: JSON.stringify(body) }
  );
}

/**
 * The pagination cursor for the NEXT page of an inbox search, or null when the
 * response says there is none.
 *
 * ⚠️ The request nests the cursor under `pagination.nextCursor`, and the
 * response is assumed to answer in the same shape (or flat). That assumption is
 * UNVERIFIED against the live API: every `inbox-*` command needs a JWT this
 * host's accounts.json does not have, so no response body could be observed
 * while writing this. It is written to read several plausible locations and to
 * STOP rather than loop when it finds none — a missed cursor costs a page, a
 * wrong-shaped guess that never terminates costs the command.
 */
export function nextInboxCursor(response) {
  const candidates = [
    response?.pagination?.nextCursor,
    response?.nextCursor,
    response?.data?.pagination?.nextCursor,
    response?.data?.nextCursor,
  ];
  for (const c of candidates) {
    if (c !== undefined && c !== null && c !== '') return String(c);
  }
  return null;
}

/** Bundles carried by one inbox search response. */
export function bundlesOf(response) {
  return response?.notificationBundleGroups?.flatMap((g) => g.notificationBundles || []) || [];
}

/**
 * Merge consecutive inbox search pages into one response-shaped object.
 * Resources are de-duplicated by `entityResourceName` (the key the formatter
 * looks them up by); groups are concatenated in page order.
 */
export function mergeInboxPages(pages) {
  const resources = [];
  const seenResources = new Set();
  const groups = [];
  const users = {};

  for (const page of pages) {
    for (const r of page?.resources || []) {
      const key = r?.entityResourceName ?? JSON.stringify(r);
      if (seenResources.has(key)) continue;
      seenResources.add(key);
      resources.push(r);
    }
    for (const g of page?.notificationBundleGroups || []) groups.push(g);
    Object.assign(users, page?.users || {});
  }

  return {
    resources,
    notificationBundleGroups: groups,
    users,
    pagesFetched: pages.length,
  };
}

/**
 * Fetch EVERY page of inbox notifications, following the cursor.
 *
 * 🔴 A single-page read is the bug `reference/raw-api.md` is about: it returns a
 * number that looks like an answer. `inbox` read one page of 20 and
 * `inbox-clear-all` one page of 140, so a deeper queue was silently truncated
 * and "cleared all" cleared a prefix.
 *
 * @param {object} options - as fetchInboxNotifications, plus:
 * @param {number} options.maxPages - safety bound on the loop (default 25)
 * @param {function} options.fetchPage - injected transport. Every inbox
 *        endpoint needs a JWT this host does not have, so the cursor loop is
 *        only testable through a seam; this is it.
 * @returns {Promise<object>} merged response, plus `pagesFetched` / `truncated`
 */
export async function fetchAllInboxNotifications(options = {}) {
  const { maxPages = 25, fetchPage = fetchInboxNotifications, ...rest } = options;
  const pages = [];
  const seenCursors = new Set();
  let cursor = rest.cursor || '';
  let truncated = false;

  for (let page = 0; page < maxPages; page++) {
    const response = await fetchPage({ ...rest, cursor });
    pages.push(response);

    const next = nextInboxCursor(response);
    // No cursor, an empty page, or a cursor the server already handed us: stop.
    // The repeat check is what keeps a misread cursor shape from spinning.
    if (!next || bundlesOf(response).length === 0 || seenCursors.has(next)) break;
    seenCursors.add(next);
    cursor = next;

    if (page === maxPages - 1) truncated = true;
  }

  return { ...mergeInboxPages(pages), truncated, maxPages };
}

/**
 * Get inbox badge counts
 * @returns {Promise<{ messagesCount, activityCount, reminderCount, savedCount, lastUpdatedAt }>}
 */
export async function fetchInboxStats() {
  const workspaceId = getWorkspaceId();

  return internalApiRequest(
    `${INBOX_BASE}/workspaces/${workspaceId}/notifications/bundles/stats/fetch`,
    {
      method: 'POST',
      body: JSON.stringify({
        forceRefresh: true,
        includeUnreadFromAllWorkspaces: true,
        fields: ['messagesCount', 'activityCount', 'reminderCount', 'savedCount'],
      }),
    }
  );
}

/**
 * Clear (dismiss) a single notification bundle
 * @param {string} bundleId - The bundle ID from the search response
 * @returns {Promise<void>}
 */
export async function clearInboxBundle(bundleId) {
  const workspaceId = getWorkspaceId();
  const encodedId = encodeURIComponent(bundleId);

  return internalApiRequest(
    `${INBOX_BASE}/workspaces/${workspaceId}/notifications/bundles/${encodedId}/clear`,
    { method: 'PUT', body: '{}' }
  );
}

/**
 * Mark a notification bundle as read (keeps in inbox, reduces unread count)
 * @param {string} bundleId - The bundle ID from the search response
 * @returns {Promise<void>}
 */
export async function markInboxBundleRead(bundleId) {
  const workspaceId = getWorkspaceId();

  return internalApiRequest(
    `${INBOX_BASE}/workspaces/${workspaceId}/notifications/bundles/read`,
    { method: 'PUT', body: JSON.stringify({ bundleSnapshotId: bundleId }) }
  );
}

/**
 * Clear all uncleared bundles for a given tab
 * @param {'messages'|'activity'} bundleType
 * @returns {Promise<{ total: number, cleared: number, failed: number,
 *                     pagesFetched: number, truncated: boolean }>}
 *          `total` is what was FOUND across every page read; `truncated` says
 *          the page bound stopped the read, so bundles remain uncleared.
 */
export async function clearAllInboxBundles(bundleType = 'messages') {
  // Cursor-following: a single 140-item page made "clear all" mean "clear the
  // first 140", with no sign that anything was left behind.
  const data = await fetchAllInboxNotifications({ bundleType, status: 'uncleared', limit: 140 });
  const bundles = bundlesOf(data);

  let cleared = 0;
  let failed = 0;
  for (const bundle of bundles) {
    try {
      await clearInboxBundle(bundle.id);
      cleared++;
    } catch {
      failed++;
    }
  }

  return {
    total: bundles.length,
    cleared,
    failed,
    pagesFetched: data.pagesFetched,
    truncated: data.truncated,
  };
}

// ============================================================================
// Formatting helpers
// ============================================================================

/** Parse Quill delta JSON into plain text (used for @mentions in doc pages) */
function parseQuillDelta(content) {
  try {
    const delta = JSON.parse(content);
    if (!delta?.ops) return content;
    return delta.ops.map(op => {
      if (typeof op.insert === 'string') return op.insert;
      if (op.insert?.user_mention) return `@${op.insert.user_mention.name}`;
      return '';
    }).join('').trim();
  } catch {
    return content;
  }
}

/** Extract entity type and ID from a resource name */
function parseEntityId(resourceName) {
  const parts = resourceName.split(':');
  if (parts.length >= 4) {
    return { type: parts[1], id: parts[3] };
  }
  return { type: 'unknown', id: resourceName };
}

/** Extract user ID from author string */
function parseAuthor(author) {
  const match = author?.match(/clickup:user::(\d+)/);
  return match ? match[1] : author || 'unknown';
}

/**
 * Format inbox notifications for display
 * @param {object} data - Raw response from fetchInboxNotifications
 * @param {object} userMap - Optional userId -> name map
 * @returns {string} - Formatted output
 */
export function formatInboxNotifications(data, userMap = {}) {
  const bundles = bundlesOf(data);
  const resourceMap = new Map((data.resources || []).map(r => [r.entityResourceName, r]));
  // Also map by root entity resource name for name lookups
  const rootMap = new Map();
  for (const r of data.resources || []) {
    const key = `clickup:${r.type}:${getWorkspaceId()}:${r.id}`;
    rootMap.set(key, r);
  }

  if (bundles.length === 0) return 'Inbox is empty.';

  const lines = [];

  for (const bundle of bundles) {
    const entity = parseEntityId(bundle.rootEntityResourceName);
    const rootResource = rootMap.get(bundle.rootEntityResourceName);
    const name = rootResource?.name || `${entity.type} ${entity.id}`;
    const types = Object.entries(bundle.countByNotificationType || {}).map(([t, c]) => `${c} ${t}`).join(', ');
    const time = new Date(bundle.mostRecentNotificationTime).toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
    });

    const flags = [];
    if (bundle.hasMention) flags.push('@mention');
    if (bundle.hasAssignment) flags.push('assigned');
    const flagStr = flags.length ? ` [${flags.join(', ')}]` : '';

    lines.push(`[${entity.type}] ${name}${flagStr}`);
    lines.push(`  Unread: ${bundle.unreadCount} (${types})`);
    lines.push(`  Last activity: ${time}`);

    // Extract preview
    const recent = bundle.mostRecentCommentNotification;
    const preview = bundle.previewNotification;

    if (recent?.historyItem?.entityResourceName) {
      const resource = resourceMap.get(recent.historyItem.entityResourceName);
      if (resource?.commentPreview?.length) {
        const text = resource.commentPreview.map(p => p.text).join('');
        const author = userMap[parseAuthor(resource.author)] || `User ${parseAuthor(resource.author)}`;
        const truncated = text.length > 200 ? text.slice(0, 200) + '...' : text;
        lines.push(`  ${author}: ${truncated}`);
      }
    } else if (preview?.historyItem?.type === 'description_tag' && preview.historyItem.content) {
      const text = parseQuillDelta(preview.historyItem.content);
      const author = userMap[parseAuthor(preview.historyItem.actorId)] || `User ${parseAuthor(preview.historyItem.actorId)}`;
      const truncated = text.length > 200 ? text.slice(0, 200) + '...' : text;
      lines.push(`  ${author}: ${truncated}`);
    } else if (preview?.historyItem?.type === 'status') {
      const h = preview.historyItem;
      const author = userMap[parseAuthor(h.actorId)] || `User ${parseAuthor(h.actorId)}`;
      lines.push(`  ${author}: Status changed: ${h.priorStatus?.status || '?'} → ${h.status?.status || '?'}`);
    } else if (preview?.historyItem?.type === 'shared_with_me') {
      const author = userMap[parseAuthor(preview.historyItem.actorId)] || `User ${parseAuthor(preview.historyItem.actorId)}`;
      lines.push(`  ${author}: Shared with you`);
    }

    lines.push(`  Bundle ID: ${bundle.id}`);
    lines.push('');
  }

  // Say how much was READ, not just how much matched: a total from a
  // single-page read is a number that looks like an answer.
  const pages = data.pagesFetched ? ` over ${data.pagesFetched} page(s)` : '';
  lines.push(`Total: ${bundles.length} notification bundle(s)${pages}`);
  if (data.truncated) {
    lines.push(
      `🔴 TRUNCATED: stopped at the ${data.maxPages}-page safety bound — there are more ` +
        `notifications than this listing shows.`
    );
  }
  return lines.join('\n');
}

/**
 * Format inbox stats for display
 * @param {object} stats - Raw response from fetchInboxStats
 * @returns {string}
 */
export function formatInboxStats(stats) {
  const lines = [
    `Messages: ${stats.messagesCount || 0}`,
    `Activity: ${stats.activityCount || 0}`,
    `Reminders: ${stats.reminderCount || 0}`,
    `Saved: ${stats.savedCount || 0}`,
  ];
  const total = Object.values(stats.allWorkspaces || {}).reduce((a, b) => a + b, 0);
  lines.push(`Total: ${total}`);
  lines.push(`Last updated: ${stats.lastUpdatedAt || 'unknown'}`);
  return lines.join('\n');
}
