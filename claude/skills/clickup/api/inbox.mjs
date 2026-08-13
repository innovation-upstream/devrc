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
 * @returns {Promise<{ total: number, cleared: number, failed: number }>}
 */
export async function clearAllInboxBundles(bundleType = 'messages') {
  const data = await fetchInboxNotifications({ bundleType, status: 'uncleared', limit: 140 });
  const bundles = data.notificationBundleGroups?.flatMap(g => g.notificationBundles) || [];

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

  return { total: bundles.length, cleared, failed };
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
  const bundles = data.notificationBundleGroups?.flatMap(g => g.notificationBundles) || [];
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

  lines.push(`Total: ${bundles.length} notification bundle(s)`);
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
