/**
 * ClickUp list API methods
 */

import { apiRequest } from './client.mjs';

// Get list details (including statuses)
export async function getList(listId) {
  const response = await apiRequest(`/list/${listId}`);
  return response;
}

/**
 * Create a new list in a space (folderless list)
 * @param {string} spaceId - The space ID
 * @param {string} name - The list name
 * @param {object} options - Additional options
 * @param {string} options.content - List description
 * @param {string} options.due_date - Due date timestamp
 * @param {number} options.priority - Priority (1=urgent, 2=high, 3=normal, 4=low)
 * @param {string} options.assignee - Assignee user ID
 * @param {string} options.status - Default status for tasks
 * @returns {Promise<object>} - Created list
 */
export async function createList(spaceId, name, options = {}) {
  const body = { name };

  if (options.content) {
    body.content = options.content;
  }
  if (options.due_date) {
    body.due_date = options.due_date;
  }
  if (options.due_date_time !== undefined) {
    body.due_date_time = options.due_date_time;
  }
  if (options.priority) {
    body.priority = options.priority;
  }
  if (options.assignee) {
    body.assignee = options.assignee;
  }
  if (options.status) {
    body.status = options.status;
  }

  const response = await apiRequest(`/space/${spaceId}/list`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return response;
}

/**
 * Create a new list in a folder
 * @param {string} folderId - The folder ID
 * @param {string} name - The list name
 * @param {object} options - Additional options (same as createList)
 * @returns {Promise<object>} - Created list
 */
export async function createListInFolder(folderId, name, options = {}) {
  const body = { name };

  if (options.content) {
    body.content = options.content;
  }
  if (options.due_date) {
    body.due_date = options.due_date;
  }
  if (options.due_date_time !== undefined) {
    body.due_date_time = options.due_date_time;
  }
  if (options.priority) {
    body.priority = options.priority;
  }
  if (options.assignee) {
    body.assignee = options.assignee;
  }
  if (options.status) {
    body.status = options.status;
  }

  const response = await apiRequest(`/folder/${folderId}/list`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return response;
}

// Update a list
export async function updateList(listId, updates = {}) {
  const body = {};
  const fields = ['name', 'content', 'due_date', 'due_date_time', 'priority', 'assignee', 'status', 'unset_status', 'color'];
  for (const field of fields) {
    if (updates[field] !== undefined) body[field] = updates[field];
  }
  const response = await apiRequest(`/list/${listId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
  return response;
}

// Get lists in a folder
export async function getLists(folderId, options = {}) {
  const params = new URLSearchParams();
  if (options.archived) params.append('archived', 'true');
  const queryString = params.toString();
  const response = await apiRequest(`/folder/${folderId}/list${queryString ? `?${queryString}` : ''}`);
  return response.lists || [];
}

// Get folderless lists in a space
export async function getFolderlessLists(spaceId, options = {}) {
  const params = new URLSearchParams();
  if (options.archived) params.append('archived', 'true');
  const queryString = params.toString();
  const response = await apiRequest(`/space/${spaceId}/list${queryString ? `?${queryString}` : ''}`);
  return response.lists || [];
}

// Get members with explicit access to a list
export async function getListMembers(listId) {
  const response = await apiRequest(`/list/${listId}/member`);
  return response.members || response;
}

/**
 * Delete a list
 * @param {string} listId - The list ID
 * @returns {Promise<object>} - Empty object on success
 */
export async function deleteList(listId) {
  const response = await apiRequest(`/list/${listId}`, {
    method: 'DELETE',
  });
  return response;
}
