/**
 * ClickUp task API methods
 */

import { apiRequest, apiRequestV3, fetchAllPages } from './client.mjs';
import { getList } from './lists.mjs';
import { agentIdentity, buildMarker, stampDescription } from '../lib/agent-marker.mjs';

// Get task details
export async function getTask(taskId, includeSubtasks = false) {
  const params = includeSubtasks ? '?include_subtasks=true' : '';
  const task = await apiRequest(`/task/${taskId}${params}`);
  return task;
}

// Get tasks in a list
export async function getTasksInList(listId, assigneeId = null) {
  let endpoint = `/list/${listId}/task`;
  if (assigneeId) {
    endpoint += `?assignees[]=${assigneeId}`;
  }
  return fetchAllPages(endpoint, 'tasks');
}

// Update a task
export async function updateTask(taskId, updates) {
  const response = await apiRequest(`/task/${taskId}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
  return response;
}

// Get available statuses for a task's list
export async function getAvailableStatuses(taskId) {
  const task = await getTask(taskId);
  const listId = task.list?.id;
  if (!listId) {
    throw new Error('Could not determine list ID from task');
  }
  const list = await getList(listId);
  return list.statuses || [];
}

// Find matching status (case-insensitive, partial match)
export function findMatchingStatus(statuses, input) {
  const inputLower = input.toLowerCase().trim();

  // Exact match first
  const exact = statuses.find(s => s.status.toLowerCase() === inputLower);
  if (exact) return exact;

  // Partial match
  const partial = statuses.find(s => s.status.toLowerCase().includes(inputLower));
  if (partial) return partial;

  return null;
}

// Update task status with validation
export async function updateTaskStatus(taskId, statusInput) {
  const statuses = await getAvailableStatuses(taskId);
  const match = findMatchingStatus(statuses, statusInput);

  if (!match) {
    const available = statuses.map(s => `"${s.status}"`).join(', ');
    throw new Error(`Invalid status "${statusInput}". Available: ${available}`);
  }

  const response = await updateTask(taskId, { status: match.status });
  return { task: response, matchedStatus: match };
}

// ── Agent-object hygiene (Phase 0) ──────────────────────────────────────────
//
// 🔴 THE STAMP LIVES HERE, AT THE ONE CHOKE POINT, and that placement is the
// point. Every ClickUp task this skill creates goes through createTask() —
// `query.mjs create`, `query.mjs create-subtask`, and lib/batch-create.mjs's
// whole fan-out. Stamping at the CALL SITES instead would regenerate the same
// omission at each one, and a new caller would arrive unstamped by default.
//
// Two things get stamped, and they answer different questions:
//   * the TAG `agent/<producer>` — "which objects did an agent create?",
//     answerable through the API with no body parsing at all;
//   * the MARKER in the description — "what closes this one?".
//
// On ClickUp the tag is the load-bearing half for a reason specific to this
// platform: the API token resolves to a HUMAN identity, so creator-based
// attribution is structurally impossible here. Without a tag there is no
// queryable signal that an agent filed the task at all.
//
// ⚠️ TAGGING IS BEST-EFFORT AND CAN LEGITIMATELY FAIL. ClickUp requires a tag to
// exist at the SPACE level before it can be attached to a task; creating one is
// a workspace-level mutation this must not perform silently. So a missing space
// tag warns on stderr and the task is still created, with the marker intact.
// Create the `agent/<producer>` space tag once, per space, to turn the tag half
// on. (lib/batch-create.mjs already treats its own tag failures this way.)
//
// Opt out with CLICKUP_AGENT_STAMP=0 or `{ agentStamp: false }` — for the case
// where a human is driving the CLI by hand and the object genuinely is theirs.
function agentStampEnabled(options) {
  if (options.agentStamp === false) return false;
  return process.env.CLICKUP_AGENT_STAMP !== '0';
}

// Split the caller's options into (a) the body ClickUp receives, marker folded
// into the description, and (b) the tag to attach afterwards.
//
// EXPORTED for test/agent-marker.test.mjs. It is the entire stamping decision,
// and it is pure — so the suite can assert what ClickUp would actually receive
// without a network call or a module mock. createTask/createSubtask do nothing
// with the result but spread it into the POST body and attach the tag.
export function applyAgentStamp(options) {
  const { agentStamp: _drop, agentFp, agentCond, ...rest } = options;
  if (!agentStampEnabled(options)) return { body: rest, tag: null };
  const id = agentIdentity();
  const marker = buildMarker({
    srcProducer: id.producer,
    srcRunId: id.runId,
    // A caller with a real machine-checkable condition passes one; a session
    // filing a one-off gets `manual`, which is an honest "no machine closes
    // this" rather than a fabricated condition nobody will evaluate.
    cond: agentCond ?? id.cond,
    // 🔴 fp is OMITTED unless the caller supplies one. A session-filed task has
    // no stable claim tuple, and a fingerprint that changes every run defeats
    // the dedupe it exists to provide — see lib/agent-marker.mjs.
    fp: agentFp ?? null,
    init: id.init,
  });
  // ClickUp renders `markdown_description` when present and ignores
  // `description`; stamping only one of them would put the marker in the field
  // the UI is not showing, or drop it entirely on a task that set neither.
  const body = { ...rest };
  if (typeof body.markdown_description === 'string' || typeof body.description !== 'string') {
    body.markdown_description = stampDescription(body.markdown_description, marker);
  } else {
    body.description = stampDescription(body.description, marker);
  }
  return { body, tag: id.label };
}

async function attachAgentTag(taskId, tag) {
  if (!tag || !taskId) return;
  try {
    await addTag(taskId, tag);
  } catch (e) {
    // Never fatal: the marker is already in the body, so the object is still
    // identifiable. Loud on stderr because a silently untagged fleet is exactly
    // the state Phase 0 exists to end.
    process.stderr.write(
      `Warning: could not attach agent tag "${tag}" to ${taskId}: ${e.message}\n` +
      `  (ClickUp requires the tag to exist at the SPACE level first.)\n`,
    );
  }
}

// Create a new task in a list
export async function createTask(listId, name, options = {}) {
  const { body: stamped, tag } = applyAgentStamp(options);
  const body = { name, ...stamped };
  const response = await apiRequest(`/list/${listId}/task`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  await attachAgentTag(response?.id, tag);
  return response;
}

// Create a subtask
export async function createSubtask(parentTaskId, name, options = {}) {
  // Get parent task to find its list
  const parent = await getTask(parentTaskId);
  const listId = parent.list?.id;
  if (!listId) {
    throw new Error('Could not determine list ID from parent task');
  }

  const { body: stamped, tag } = applyAgentStamp(options);
  const body = { name, parent: parentTaskId, ...stamped };
  const response = await apiRequest(`/list/${listId}/task`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  await attachAgentTag(response?.id, tag);
  return response;
}

// Default look-back window for search when the caller doesn't specify one.
// Bounds date_updated_gt so search only pulls the recent working set instead of
// the entire workspace history. Widen per-call with --since / --all-time.
// Measured on this workspace (~high task volume): 30d ≈ 2k tasks ≈ ~15s;
// 6m timed out (>115s). --me / --assignee narrows server-side to ~instant.
export const DEFAULT_SEARCH_SINCE = '30d';

// Search tasks across team
// ClickUp v2 doesn't support direct text search, so we fetch with API-supported
// filters and apply client-side text filtering for the query parameter.
export async function searchTasks(teamId, query, options = {}) {
  const params = new URLSearchParams();

  // Space scoping. `search` has NO server-side assignee filter, so sweeping all
  // spaces means pulling the entire workspace task history before client-side
  // text matching — prohibitively slow across 14+ archived spaces. So:
  //   - explicit options.space_ids  → use them
  //   - options.allSpaces           → active + archived (the full, slow sweep)
  //   - default                     → let the endpoint scan active spaces only
  // This keeps interactive search fast while still allowing the archived sweep
  // on demand (getMyTasks can afford the full scan because its assignee filter
  // runs server-side).
  let spaceIds = options.space_ids;
  if ((!spaceIds || !Array.isArray(spaceIds) || spaceIds.length === 0) && options.allSpaces) {
    ({ ids: spaceIds } = await getAllSpaceIds(teamId));
  }

  // Recency bound (server-side). Since v2 has no text search, we fetch then
  // filter client-side — so the ONLY way to keep this fast is to shrink the
  // fetched set server-side. date_updated_gt is indexed, so bounding to a
  // look-back window (default: the active working set) is the big lever.
  //   - options.since    → explicit window ("90d","6m","1y") or date
  //   - options.allTime  → no bound (the full, slow scan) — explicit opt-in
  //   - default          → DEFAULT_SEARCH_SINCE
  let sinceMs = null;
  if (!options.allTime && !options.date_updated_gt) {
    const since = options.since || DEFAULT_SEARCH_SINCE;
    sinceMs = parseSinceInput(since).getTime();
    params.append('date_updated_gt', String(sinceMs));
  }

  // Assignee filter (server-side — a big speedup when scoping to a person)
  if (options.assigneeId) {
    params.append('assignees[]', options.assigneeId);
  }

  // Status filters
  if (options.statuses && Array.isArray(options.statuses)) {
    for (const status of options.statuses) {
      params.append('statuses[]', status);
    }
  }

  // Include closed tasks
  if (options.include_closed) {
    params.append('include_closed', 'true');
  }

  // Include subtasks
  if (options.subtasks) {
    params.append('subtasks', 'true');
  }

  // Space filters (only when scoped; default leaves it to the endpoint)
  if (spaceIds && spaceIds.length) {
    for (const id of spaceIds) {
      params.append('space_ids[]', id);
    }
  }

  // Project (folder) filters
  if (options.project_ids && Array.isArray(options.project_ids)) {
    for (const id of options.project_ids) {
      params.append('project_ids[]', id);
    }
  }

  // List filters
  if (options.list_ids && Array.isArray(options.list_ids)) {
    for (const id of options.list_ids) {
      params.append('list_ids[]', id);
    }
  }

  // Date filters (timestamps in milliseconds)
  const dateFilters = [
    'date_created_gt', 'date_created_lt',
    'date_updated_gt', 'date_updated_lt',
    'due_date_gt', 'due_date_lt',
  ];
  for (const filter of dateFilters) {
    if (options[filter]) {
      params.append(filter, options[filter]);
    }
  }

  const paramStr = params.toString();
  const endpoint = `/team/${teamId}/task${paramStr ? '?' + paramStr : ''}`;
  const tasks = await fetchAllPages(endpoint, 'tasks');

  // Surface the applied scope so a narrow default doesn't silently hide results.
  const scope = [];
  scope.push(options.allTime ? 'all time' : `updated since ${new Date(sinceMs).toISOString().slice(0, 10)}`);
  scope.push(spaceIds && spaceIds.length ? `${spaceIds.length} spaces` : 'active spaces');
  if (options.assigneeId) scope.push('assignee-scoped');
  console.error(`Search scanned ${tasks.length} task(s) [${scope.join(', ')}]. Widen with --since <win> / --all-time / --all-spaces.`);

  // Client-side text filter (API doesn't support text search)
  if (query) {
    const queryLower = query.toLowerCase();
    return tasks.filter(t =>
      t.name.toLowerCase().includes(queryLower) ||
      (t.description && t.description.toLowerCase().includes(queryLower))
    );
  }

  return tasks;
}

// List all space IDs in a team, including archived spaces.
// The Filtered Team Tasks endpoint only scans NON-archived spaces when called
// without space_ids[], so tasks living in an archived space (a common pattern
// where an old space is archived but still holds live, assigned tasks) are
// silently omitted. Enumerating both states and passing them explicitly closes
// that gap. Returns { ids, archivedCount }.
export async function getAllSpaceIds(teamId) {
  const [active, archived] = await Promise.all([
    apiRequest(`/team/${teamId}/space`),
    apiRequest(`/team/${teamId}/space?archived=true`),
  ]);
  const activeIds = (active.spaces || []).map(s => s.id);
  const archivedIds = (archived.spaces || []).map(s => s.id);
  const ids = [...new Set([...activeIds, ...archivedIds])];
  return { ids, archivedCount: archivedIds.length };
}

// Get all tasks assigned to a user across the team.
// Explicitly scopes to every space (active + archived) so tasks in archived
// spaces are not dropped. Writes a one-line scan summary to stderr so the
// coverage is visible rather than silent.
export async function getMyTasks(teamId, userId) {
  const { ids: spaceIds, archivedCount } = await getAllSpaceIds(teamId);

  const params = new URLSearchParams();
  params.append('assignees[]', userId);
  params.append('subtasks', 'true');
  for (const id of spaceIds) {
    params.append('space_ids[]', id);
  }

  const endpoint = `/team/${teamId}/task?${params.toString()}`;
  const tasks = await fetchAllPages(endpoint, 'tasks');

  console.error(
    `Scanned ${spaceIds.length} space(s) (${archivedCount} archived).`
  );
  return tasks;
}

// Update task assignees
export async function assignTask(taskId, assigneeIds, options = {}) {
  const body = {};

  if (options.remove) {
    body.assignees = { rem: assigneeIds };
  } else {
    // Default to add format - ClickUp API requires { add: [...] } for updates
    body.assignees = { add: assigneeIds };
  }

  const response = await updateTask(taskId, body);
  return response;
}

// Update task due date
export async function setDueDate(taskId, dueDate) {
  // Convert to timestamp if needed
  let timestamp = null;
  if (dueDate) {
    const parsed = parseDateInput(dueDate);
    timestamp = parsed.getTime();
  }

  const response = await updateTask(taskId, { due_date: timestamp, due_date_time: true });
  return response;
}

// Update task start date
export async function setStartDate(taskId, startDate) {
  // Convert to timestamp if needed
  let timestamp = null;
  if (startDate) {
    const parsed = parseDateInput(startDate);
    timestamp = parsed.getTime();
  }

  const response = await updateTask(taskId, { start_date: timestamp, start_date_time: true });
  return response;
}

// Update task dates (start and/or due)
export async function setDates(taskId, options = {}) {
  const updates = {};
  if (options.start) {
    const parsed = parseDateInput(options.start);
    updates.start_date = parsed.getTime();
    updates.start_date_time = true;
  }
  if (options.due) {
    const parsed = parseDateInput(options.due);
    updates.due_date = parsed.getTime();
    updates.due_date_time = true;
  }
  const response = await updateTask(taskId, updates);
  return response;
}

// Parse natural language date input
export function parseDateInput(input) {
  const now = new Date();
  const inputLower = input.toLowerCase().trim();

  // Today
  if (inputLower === 'today') {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
  }

  // Tomorrow
  if (inputLower === 'tomorrow') {
    const tomorrow = new Date(now);
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(23, 59, 59);
    return tomorrow;
  }

  // Next week
  if (inputLower === 'next week') {
    const nextWeek = new Date(now);
    nextWeek.setDate(nextWeek.getDate() + 7);
    nextWeek.setHours(23, 59, 59);
    return nextWeek;
  }

  // Day names (next monday, next friday, etc.)
  const days = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  for (let i = 0; i < days.length; i++) {
    if (inputLower.includes(days[i])) {
      const target = new Date(now);
      const currentDay = target.getDay();
      let daysToAdd = i - currentDay;
      if (daysToAdd <= 0) daysToAdd += 7; // Next week if today or past
      target.setDate(target.getDate() + daysToAdd);
      target.setHours(23, 59, 59);
      return target;
    }
  }

  // +N days format
  const plusDaysMatch = inputLower.match(/^\+(\d+)\s*(d|days?)?$/);
  if (plusDaysMatch) {
    const target = new Date(now);
    target.setDate(target.getDate() + parseInt(plusDaysMatch[1], 10));
    target.setHours(23, 59, 59);
    return target;
  }

  // Try parsing as date string
  const parsed = new Date(input);
  if (!isNaN(parsed.getTime())) {
    return parsed;
  }

  throw new Error(`Could not parse date: "${input}"`);
}

// Parse a "look-back window" into a past Date. Accepts:
//   - relative windows: "90d", "6w", "3m", "1y" (days/weeks/months/years ago)
//   - "Nd"/"N days" style with optional spaces
//   - any Date-parseable string ("2026-01-01") → that absolute date
// Used for date_updated_gt bounds on search. Distinct from parseDateInput,
// which resolves FUTURE dates (due/start dates).
export function parseSinceInput(input) {
  const s = String(input).toLowerCase().trim();
  const rel = s.match(/^(\d+)\s*(d|day|days|w|week|weeks|m|month|months|y|year|years)$/);
  if (rel) {
    const n = parseInt(rel[1], 10);
    const unit = rel[2][0]; // d | w | m | y
    const t = new Date();
    if (unit === 'd') t.setDate(t.getDate() - n);
    else if (unit === 'w') t.setDate(t.getDate() - n * 7);
    else if (unit === 'm') t.setMonth(t.getMonth() - n);
    else if (unit === 'y') t.setFullYear(t.getFullYear() - n);
    return t;
  }
  const parsed = new Date(input);
  if (!isNaN(parsed.getTime())) return parsed;
  throw new Error(`Could not parse look-back window: "${input}" (try e.g. "90d", "6m", "1y", or a date)`);
}

// Update task priority
export async function setPriority(taskId, priorityInput) {
  const priorities = {
    'urgent': 1,
    '1': 1,
    'high': 2,
    '2': 2,
    'normal': 3,
    '3': 3,
    'low': 4,
    '4': 4,
    'none': null,
    'clear': null,
  };

  const inputLower = priorityInput.toLowerCase().trim();
  if (!(inputLower in priorities)) {
    throw new Error(`Invalid priority "${priorityInput}". Use: urgent, high, normal, low, or none`);
  }

  const priority = priorities[inputLower];
  const response = await updateTask(taskId, { priority });
  return { task: response, priority: priorityInput };
}

// Move task to a different list (v3 endpoint)
export async function moveTask(taskId, targetListId, workspaceId) {
  const response = await apiRequestV3(
    `/workspaces/${workspaceId}/tasks/${taskId}/home_list/${targetListId}`,
    { method: 'PUT' }
  );
  return response;
}

// Archive a task (removes from active views, retrievable later)
export async function archiveTask(taskId) {
  const response = await updateTask(taskId, { archived: true });
  return response;
}

// Unarchive a task (restore from archive)
export async function unarchiveTask(taskId) {
  const response = await updateTask(taskId, { archived: false });
  return response;
}

// Add a watcher/follower to a task via UpdateTask
// Note: ClickUp UI calls these "followers" but the API field is "watchers"
export async function addWatcher(taskId, userId) {
  return updateTask(taskId, { watchers: { add: [parseInt(userId, 10)] } });
}

// Remove a watcher/follower from a task
export async function removeWatcher(taskId, userId) {
  return updateTask(taskId, { watchers: { rem: [parseInt(userId, 10)] } });
}

// Add a tag to a task
export async function addTag(taskId, tagName) {
  // Tag names in URL must be URL-encoded
  const encodedTag = encodeURIComponent(tagName);
  const response = await apiRequest(`/task/${taskId}/tag/${encodedTag}`, {
    method: 'POST',
  });
  return response;
}

// Remove a tag from a task
export async function removeTag(taskId, tagName) {
  const encodedTag = encodeURIComponent(tagName);
  const response = await apiRequest(`/task/${taskId}/tag/${encodedTag}`, {
    method: 'DELETE',
  });
  return response;
}

// Add a dependency (task waits on another task)
// depends_on: the task that must complete first
// dependency_of: the task that is blocked
// Only one of depends_on or dependency_of should be set
export async function addDependency(taskId, options = {}) {
  const body = {};
  if (options.depends_on) {
    body.depends_on = options.depends_on;
  } else if (options.dependency_of) {
    body.dependency_of = options.dependency_of;
  } else {
    throw new Error('Must specify either depends_on or dependency_of');
  }
  const response = await apiRequest(`/task/${taskId}/dependency`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return response;
}

// Remove a dependency
export async function removeDependency(taskId, options = {}) {
  const params = new URLSearchParams();
  if (options.depends_on) {
    params.append('depends_on', options.depends_on);
  } else if (options.dependency_of) {
    params.append('dependency_of', options.dependency_of);
  } else {
    throw new Error('Must specify either depends_on or dependency_of');
  }
  const response = await apiRequest(`/task/${taskId}/dependency?${params.toString()}`, {
    method: 'DELETE',
  });
  return response;
}

// Add a task link (bidirectional link between tasks)
export async function addTaskLink(taskId, linksToTaskId) {
  const response = await apiRequest(`/task/${taskId}/link/${linksToTaskId}`, {
    method: 'POST',
  });
  return response;
}

// Remove a task link
export async function removeTaskLink(taskId, linksToTaskId) {
  const response = await apiRequest(`/task/${taskId}/link/${linksToTaskId}`, {
    method: 'DELETE',
  });
  return response;
}

// Get members with explicit access to a task
export async function getTaskMembers(taskId) {
  const response = await apiRequest(`/task/${taskId}/member`);
  return response.members || response;
}

// Set a custom field value on a task
export async function setCustomField(taskId, fieldId, value) {
  return apiRequest(`/task/${taskId}/field/${fieldId}`, {
    method: 'POST',
    body: JSON.stringify({ value }),
  });
}

// Find a custom field on a task by name (case-insensitive partial match)
export async function findCustomFieldByName(taskId, fieldName) {
  const task = await getTask(taskId);
  const fields = task.custom_fields || [];
  const nameLower = fieldName.toLowerCase();

  // Exact match first
  const exact = fields.find(f => f.name.toLowerCase() === nameLower);
  if (exact) return exact;

  // Partial match
  const partial = fields.find(f => f.name.toLowerCase().includes(nameLower));
  return partial || null;
}
