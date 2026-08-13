/**
 * ClickUp Document Comments API (internal/undocumented)
 *
 * The official ClickUp API has no support for document comments.
 * This module uses the internal comment-service endpoints that the web UI uses.
 *
 * Requires: CLICKUP_JWT (session token from browser login)
 *
 * Comment types:
 *   14 = top-level doc page comment
 *    2 = reply to a comment thread
 */

import { internalApiRequest, getWorkspaceId } from './internal-client.mjs';
import { clickUpToMarkdown } from '../lib/markdown.mjs';
import { textToCommentArray } from '../lib/mentions.mjs';

const COMMENT_SERVICE = '/comment-service/v3';

// Standard fields to request for comment threads
const THREAD_FIELDS = [
  'thread_comment_count',
  'thread_latest_comment_date',
  'thread_user_ids',
];

/**
 * Get comments on a doc page
 * @param {string} pageId - The page ID (e.g., "abc12-37231")
 * @param {object} options
 * @param {boolean} options.includeReplies - Include reply threads (default: true)
 * @param {boolean} options.resolvedOnly - Only resolved comments
 * @returns {Promise<{ comments: object[], threads: object[] }>}
 */
export async function getDocPageComments(pageId, options = {}) {
  const workspaceId = getWorkspaceId();
  const includeReplies = options.includeReplies !== false;

  const fields = THREAD_FIELDS.map(f => `fields=${f}`).join('&');
  const endpoint = `${COMMENT_SERVICE}/workspaces/${workspaceId}/comments/search?${fields}`;

  const body = {
    filters: {
      parent: {
        id: pageId,
        include_replies: includeReplies,
        type: 'doc',
      },
    },
  };

  if (options.resolvedOnly) {
    body.filters.resolved = true;
  }

  const response = await internalApiRequest(endpoint, {
    method: 'POST',
    body: JSON.stringify(body),
  });

  return {
    comments: response.comments || [],
    threads: response.comment_threads || [],
  };
}

/**
 * Reply to an existing doc comment thread
 * @param {string} commentId - The comment to reply to
 * @param {string} text - Reply text (markdown)
 * @param {object} options
 * @param {string} options.pageId - The root page ID (required)
 * @returns {Promise<object>} - Created reply
 */
export async function replyToDocComment(commentId, text, options = {}) {
  const workspaceId = getWorkspaceId();
  const pageId = options.pageId;

  if (!pageId) {
    throw new Error('pageId is required to reply to a doc comment');
  }

  const commentArray = await textToCommentArray(text);
  const pendingId = `PENDING_${Date.now().toString(36)}`;
  const now = Date.now().toString();

  const body = {
    comment: commentArray,
    id: pendingId,
    date: now,
    isPending: true,
    root_parent_id: pageId,
    root_parent_type: 14,
    parent: commentId,
    type: 2,
    attachment_ids: [],
    key: `2_${commentId}`,
    reactions: [],
    comment_date: Date.now(),
  };

  const endpoint = `${COMMENT_SERVICE}/workspaces/${workspaceId}/comments/comment/${commentId}`;
  const response = await internalApiRequest(endpoint, {
    method: 'POST',
    headers: { 'idempotency-key': pendingId },
    body: JSON.stringify(body),
  });

  return response;
}

/**
 * Post a new top-level comment on a doc page
 * @param {string} pageId - The page ID
 * @param {string} text - Comment text (markdown)
 * @returns {Promise<object>} - Created comment
 */
export async function postDocPageComment(pageId, text) {
  const workspaceId = getWorkspaceId();

  const commentArray = await textToCommentArray(text);
  const pendingId = `PENDING_${Date.now().toString(36)}`;
  const now = Date.now().toString();

  const body = {
    comment: commentArray,
    id: pendingId,
    date: now,
    isPending: true,
    root_parent_id: pageId,
    root_parent_type: 14,
    parent: pageId,
    type: 14,
    attachment_ids: [],
    key: `14_${pageId}`,
    reactions: [],
    comment_date: Date.now(),
  };

  const endpoint = `${COMMENT_SERVICE}/workspaces/${workspaceId}/comments/doc/${pageId}`;
  const response = await internalApiRequest(endpoint, {
    method: 'POST',
    headers: { 'idempotency-key': pendingId },
    body: JSON.stringify(body),
  });

  return response;
}

/**
 * Format doc comments for display
 * @param {object[]} comments - Raw comment objects from the API
 * @param {object} options
 * @param {object} options.userMap - Map of userId -> username for display
 * @returns {string} - Formatted output
 */
export function formatDocComments(comments, options = {}) {
  if (!comments || comments.length === 0) return 'No comments found.';

  const userMap = options.userMap || {};

  // Separate top-level and replies
  const topLevel = comments.filter(c => c.type === 14);
  const replies = comments.filter(c => c.type === 2);

  // Group replies by parent
  const replyMap = {};
  for (const r of replies) {
    if (!replyMap[r.parent]) replyMap[r.parent] = [];
    replyMap[r.parent].push(r);
  }

  const lines = [];

  for (const c of topLevel) {
    const date = new Date(parseInt(c.date));
    const dateStr = date.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    });
    const user = userMap[c.userid] || `User ${c.userid}`;
    const resolved = c.resolved ? ' [RESOLVED]' : '';
    const section = c.referenced_content ? ` (on "${c.referenced_content}")` : '';

    // Convert comment array to readable text
    const text = c.text_content || clickUpToMarkdown(c.comment) || '(empty)';

    lines.push(`[${dateStr}] ${user}${resolved}${section}:`);
    // Indent the comment text
    for (const line of text.trim().split('\n')) {
      lines.push(`  ${line}`);
    }
    lines.push(`  ID: ${c.id}`);

    // Show replies
    const threadReplies = replyMap[c.id] || [];
    if (threadReplies.length > 0) {
      // Sort by date
      threadReplies.sort((a, b) => parseInt(a.date) - parseInt(b.date));
      for (const r of threadReplies) {
        const rDate = new Date(parseInt(r.date));
        const rDateStr = rDate.toLocaleDateString('en-US', {
          month: 'short', day: 'numeric', year: 'numeric',
          hour: 'numeric', minute: '2-digit',
        });
        const rUser = userMap[r.userid] || `User ${r.userid}`;
        const rText = r.text_content || clickUpToMarkdown(r.comment) || '(empty)';

        lines.push(`    [${rDateStr}] ${rUser}:`);
        for (const line of rText.trim().split('\n')) {
          lines.push(`      ${line}`);
        }
        lines.push(`      ID: ${r.id}`);
      }
    }

    lines.push('');
  }

  lines.push(`Total: ${topLevel.length} comment(s), ${replies.length} reply(ies)`);
  return lines.join('\n');
}
