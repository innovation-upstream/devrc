#!/usr/bin/env node

/**
 * ClickUp - Task and Document interaction skill
 *
 * The command reference is showUsage() below, printed by `node query.mjs`
 * with no arguments. It is the SINGLE source of truth and is pinned by
 * test/help-coverage.test.mjs, which fails if any dispatchable command is missing
 * from it (or any printed command cannot dispatch).
 *
 * Do NOT restate the command list here or in SKILL.md. Both were hand-kept
 * copies that drifted: this header lagged by ~15 commands and SKILL.md's
 * tables documented 56 of 68, hiding a whole command group.
 */

import { readFileSync, existsSync, unlinkSync, writeFileSync, mkdirSync } from 'fs';
import { resolve, join } from 'path';
import { tmpdir } from 'os';

// API imports
import { initClient, getActiveCredentials, getActiveAccountId } from './api/client.mjs';
import { updateAccountField, listAccounts as listAccountsFn, setDefaultAccount, addAccount as addAccountFn, removeAccount as removeAccountFn } from './lib/accounts.mjs';
import { getCurrentUser, getUserId, getTeamId, findUser } from './api/user.mjs';
import {
  getTask,
  getTasksInList,
  getAvailableStatuses,
  updateTaskStatus,
  updateTask,
  createTask,
  createSubtask,
  searchTasks,
  getMyTasks,
  assignTask,
  setDueDate,
  setStartDate,
  setDates,
  setPriority,
  moveTask,
  parseDateInput,
  parseSinceInput,
  addWatcher,
  addTag,
  addDependency,
  removeDependency,
  addTaskLink,
  removeTaskLink,
  archiveTask,
  unarchiveTask,
  removeWatcher,
  removeTag,
  setCustomField,
  findCustomFieldByName,
} from './api/tasks.mjs';
import { getComments, postComment, updateComment, deleteComment, getThreadedComments, replyToComment } from './api/comments.mjs';
import { addChecklistItemToTask, getChecklists, editChecklistItem, deleteChecklistItem } from './api/checklists.mjs';
import { addExternalLink } from './api/links.mjs';
import { getList, createList, deleteList, updateList, getLists, getFolderlessLists, getListMembers } from './api/lists.mjs';
import {
  searchDocs,
  getDoc,
  createDoc,
  getDocPageListing,
  getPage,
  createPage,
  editPage,
} from './api/docs.mjs';
import {
  getDocPageComments,
  postDocPageComment,
  replyToDocComment,
  formatDocComments,
} from './api/doc-comments.mjs';
import {
  fetchAllInboxNotifications,
  fetchInboxStats,
  clearInboxBundle,
  markInboxBundleRead,
  clearAllInboxBundles,
  formatInboxNotifications,
  formatInboxStats,
} from './api/inbox.mjs';
import { uploadAttachment, uploadAttachments } from './api/attachments.mjs';

// Lib imports
import { executeBatchCreate } from './lib/batch-create.mjs';
import { validateCond, COND_KINDS } from './lib/agent-marker.mjs';
import { splitIds, isBulk, bulkExecute, formatBulkResults } from './lib/bulk.mjs';
import { parseTaskId, parseListId, parseDocId, parsePageId, parseSpaceId } from './lib/parse.mjs';
import {
  formatTask,
  formatTaskList,
  formatComments,
  formatThread,
  formatDoc,
  formatDocList,
  formatPage,
  formatPageList,
  formatList,
} from './lib/format.mjs';
import { stateBaseDir } from './lib/paths.mjs';
import { findAwaiting, formatAwaiting, AWAITING_DEFAULTS } from './lib/awaiting.mjs';

// Parse arguments
const args = process.argv.slice(2);
let command = null;
let targetInput = null;
let arg2 = null;
let arg3 = null;
let jsonOutput = false;
let includeSubtasks = false;
let filterMe = false;
let assigneeArg = null;
let dueArg = null;
let descriptionArg = null;
let condArg = null;
let contentArg = null;
let nameArg = null;
let parentArg = null;
let spaceArg = null;
let fileArg = null;
let cleanupFlag = false;
let archiveFlag = false;
let pageArg = null;
let accountArg = null;
let threadsFlag = false;
let attachArgs = [];
let allSpacesFlag = false;
let sinceArg = null;
let allTimeFlag = false;
let daysArg = null;
let maxArg = null;
let mentionedFlag = false;
let unreadFlag = false;
let clearedFlag = false;
let cursorArg = null;

for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '--json') {
    jsonOutput = true;
  } else if (arg === '--subtasks') {
    includeSubtasks = true;
  } else if (arg === '--me') {
    filterMe = true;
  } else if (arg === '--assignee' || arg === '-a') {
    assigneeArg = args[++i];
  } else if (arg === '--due' || arg === '-d') {
    dueArg = args[++i];
  } else if (arg === '--description' || arg === '--desc') {
    descriptionArg = args[++i];
  } else if (arg === '--cond') {
    condArg = args[++i];
  } else if (arg === '--content' || arg === '-c') {
    contentArg = args[++i];
  } else if (arg === '--file' || arg === '-f') {
    fileArg = args[++i];
  } else if (arg === '--cleanup') {
    cleanupFlag = true;
  } else if (arg === '--archive') {
    archiveFlag = true;
  } else if (arg === '--name' || arg === '-n') {
    nameArg = args[++i];
  } else if (arg === '--parent' || arg === '-p') {
    parentArg = args[++i];
  } else if (arg === '--space' || arg === '-s') {
    spaceArg = args[++i];
  } else if (arg === '--page') {
    pageArg = args[++i];
  } else if (arg === '--threads' || arg === '-t') {
    threadsFlag = true;
  } else if (arg === '--attach') {
    attachArgs.push(args[++i]);
  } else if (arg === '--account') {
    accountArg = args[++i];
  } else if (arg === '--all-spaces') {
    allSpacesFlag = true;
  } else if (arg === '--since') {
    sinceArg = args[++i];
  } else if (arg === '--all-time') {
    allTimeFlag = true;
  } else if (arg === '--days') {
    daysArg = args[++i];
  } else if (arg === '--max') {
    maxArg = args[++i];
  } else if (arg === '--mentioned') {
    mentionedFlag = true;
  } else if (arg === '--unread') {
    unreadFlag = true;
  } else if (arg === '--cleared') {
    clearedFlag = true;
  } else if (arg === '--cursor') {
    cursorArg = args[++i];
  } else if (!command) {
    command = arg;
  } else if (!targetInput) {
    targetInput = arg;
  } else if (!arg2) {
    arg2 = arg;
  } else if (!arg3) {
    arg3 = arg;
  }
}

// Unescape literal \n and \t in text args (CLI passes them as-is)
function unescapeText(str) {
  return str ? str.replace(/\\n/g, '\n').replace(/\\t/g, '\t') : str;
}
contentArg = unescapeText(contentArg);
descriptionArg = unescapeText(descriptionArg);
arg2 = unescapeText(arg2);

// --file overrides --content: read file contents as the content/text argument
if (fileArg) {
  const filePath = resolve(fileArg);
  if (!existsSync(filePath)) {
    console.error(`Error: File not found: ${filePath}`);
    process.exit(1);
  }
  const fileContent = readFileSync(filePath, 'utf-8');
  contentArg = fileContent;
  // Also set arg2 so commands that use positional text (comment, description) pick it up
  if (!arg2) arg2 = fileContent;
}

// Backward compatibility (commentText = arg2, which --file may have populated)
const commentText = arg2;

// Show usage
function showUsage() {
  console.error(`Usage: node query.mjs <command> [options]

Task Commands:
  get <url|id>                  Get task details
  comments <url|id>             List task comments (--threads to expand)
  thread <comment_id>           View threaded replies on a comment
  reply <comment_id> "msg"      Reply to a comment thread
  comment <url|id> "msg"        Post a comment
  status <url|id> "status"      Update task status
  tasks <list_id>               List tasks in a list
  me                            Show current user info
  create [list_id] "title"      Create a new task (list_id optional if default set)
  my-tasks                      List all tasks assigned to me
  awaiting                      My tasks whose NEWEST comment is someone else's
                                (--days N quiet-for, --assignee, --max fan-out cap)
  search "query"                Search tasks across workspace
  assign <task> <user>          Assign task to user
  due <task> "date"             Set due date
  start <task> "date"           Set start date
  schedule <task> "start" "due" Set both start and due dates
  rename <task> "new name"      Rename a task
  priority <task> <level>       Set priority (urgent/high/normal/low)
  subtask <task> "title"        Create a subtask
  move <task> <list_id>         Move task to different list
  link <task> <url> ["desc"]    Add external link reference
  checklist <task> "item"       Add checklist item
  update-comment <comment_id> "text"  Update a comment's text
  resolve-comment <comment_id>  Resolve/close a comment
  delete-comment <comment_id>   Delete a comment
  watch <task> <user>           Add a watcher to task
  unwatch <task> <user>         Remove a watcher from task
  tag <task> "tag_name"         Add a tag to task
  remove-tag <task> "tag_name"  Remove a tag from task
  description <task> "text"     Update task description (markdown supported)
  depends <task> <other_task>   Set task as waiting on another task
  blocks <task> <other_task>    Set task as blocking another task
  task-link <task> <other>      Create bidirectional link between tasks
  archive <task>                Archive a task (remove from active views)
  unarchive <task>              Restore an archived task
  claim <task>                  Link your session to this task (for resumability)

List Commands:
  list <list_id>                Get list details
  lists <folder_id>             List all lists in a folder
  space-lists <space_id>        List folderless lists in a space
  create-list <space> "name"    Create a new list in a space (--content for description)
  update-list <list_id>         Update list (--name "new name", --content "description")
  delete-list <list_id>         Delete a list

Document Commands:
  docs ["query"]                Search/list docs in workspace
  doc <doc_id>                  Get doc details and page listing
  create-doc "title"            Create a new doc (--content, --space)
  page <doc_id> <page_id>       Get page content
  create-page <doc_id> "title"  Add a new page to a doc (--content, --parent)
  edit-page <doc_id> <page_id>  Edit a page's content (--content and/or --name)
  doc-comments <page_id>        List comments on a doc page (requires JWT)
  doc-comment <page_id> "text"  Post a new comment on a doc page (requires JWT)
  doc-reply <comment_id> "text" Reply to a doc comment thread (requires --page)

Inbox Commands (requires JWT):
  inbox [messages|activity]     View inbox notifications (default: messages; follows
                                the cursor across pages)
                                Filters: --me --mentioned --unread --cleared
                                         --since <window> --cursor <cursor>
  inbox-stats                   Get unread badge counts
  inbox-clear <bundle_id>       Dismiss a notification bundle
  inbox-read <bundle_id>        Mark a notification bundle as read
  inbox-clear-all [type]        Clear all notifications (default: messages)

Attachment Commands:
  attach <url|id>               Upload file(s) to a task (--attach path, repeatable)
  fetch-image <url>             Download attachment to local temp file (--output path)
  fetch-attachment <url>        Alias for fetch-image

Project Setup:
  batch-create --file plan.json  Create multiple tasks from JSON (with deps, subtasks, assignments)
  batch-create --file plan.json --dry-run  Preview without creating

Account Commands:
  accounts                      List configured accounts
  switch-account <name>         Change the default account
  add-account <name>            Add a new account (--token required)
  remove-account <name>         Remove an account

Options:
  --account  <name>  Use a specific account for this command
  --json       Output raw JSON
  --subtasks   Include subtasks (for get command)
  --me         Filter to tasks assigned to me (for tasks command)
  --all-spaces Search across ALL spaces incl. archived (for search; slow — pulls full history)
  --since      Search look-back window by date_updated (e.g. "90d","6m","1y",date). Default 30d
               (tip: --me / --assignee narrows server-side and is near-instant)
               Also bounds inbox to notifications after that date
  --all-time   Search with no recency bound (slowest; scans full history)
  --mentioned  inbox: only bundles that @mention me
  --unread     inbox: only unread bundles
  --cleared    inbox: read the CLEARED bundles instead of the uncleared ones
  --cursor     inbox: start from a specific pagination cursor
  --days       awaiting: only tasks quiet for at least N days (default 0)
  --max        awaiting: cap the comment fan-out at N tasks (default ${AWAITING_DEFAULTS.maxTasks};
               one request per task, paced under the 100 req/min token limit)
  --content    Inline content (markdown). For short text only.
  --file       Read content from a file path (preferred for long content)
  --cleanup    Delete the --file after successful execution
  --name       New name for edit-page
  --parent     Parent page ID for create-page (creates as subpage)
  --attach     File path to upload as attachment (repeatable, for attach/comment)
  --page       Page ID for doc-reply (identifies which page the thread is on)
  --space      Space ID for create-doc (places doc in that space)
  --cond       Close-condition for a task an agent files, from the enumerated
               allowlist (gh_pr_merged:<owner>/<repo>#<n>, alert_cleared:<name>,
               cmd_exit_zero:<id>, metric_below:<id>, manual). Recorded in the
               task body so a later reconciler can close it. Defaults to
               `manual`; anything off-allowlist is REJECTED, not stored.

Bulk Operations (comma-separated IDs):
  node query.mjs get id1,id2,id3              Fetch multiple tasks at once
  node query.mjs status id1,id2 "complete"    Update status on multiple tasks
  node query.mjs due id1,id2 "friday"         Set due date on multiple tasks
  node query.mjs inbox-clear bid1,bid2,bid3   Clear multiple notifications

Examples:
  node query.mjs get 86a1b2c3d --subtasks
  node query.mjs comment 86a1b2c3d "Starting work on this"
  node query.mjs status 86a1b2c3d "in progress"
  node query.mjs tasks 900000000001 --me
  node query.mjs create 900000000001 "New feature: dark mode"
  node query.mjs create "Quick task" (uses CLICKUP_DEFAULT_LIST_ID)
  node query.mjs my-tasks
  node query.mjs awaiting                    # someone commented last, and it wasn't me
  node query.mjs awaiting --days 2 --max 40  # quiet 2+ days, scan at most 40 tasks
  node query.mjs search "dark mode"
  node query.mjs assign 86a1b2c3d alex
  node query.mjs due 86a1b2c3d "tomorrow"
  node query.mjs priority 86a1b2c3d high
  node query.mjs subtask 86a1b2c3d "Write unit tests"
  node query.mjs move 86a1b2c3d 900000000002
  node query.mjs link 86a1b2c3d "https://github.com/..." "PR #123"
  node query.mjs checklist 86a1b2c3d "Review code"
  node query.mjs delete-comment 90000000000001
  node query.mjs watch 86a1b2c3d alex
  node query.mjs tag 86a1b2c3d "DevOps"
  node query.mjs description 86a1b2c3d "## Summary\\nThis is **bold** text"

List Examples:
  node query.mjs list 900000000001                        # Get list details
  node query.mjs create-list 90000010 "New List"          # Create list in space
  node query.mjs create-list "https://app.clickup.com/.../v/s/90000010" "New List"
  node query.mjs delete-list 900000000001                 # Delete a list

Document Examples:
  node query.mjs docs                                     # List all docs
  node query.mjs docs "API"                               # Search docs
  node query.mjs doc abc123                               # Get doc details
  node query.mjs create-doc "Project Notes"               # Create empty doc
  node query.mjs create-doc "Guide" --content "# Guide\\nContent here"  # Short inline content
  node query.mjs page abc123 page456                      # Get page content
  node query.mjs create-page abc123 "New Section"         # Add additional page
  node query.mjs edit-page abc123 page456 --content "Updated content" --name "Renamed"

File-based Content (recommended for anything longer than a sentence):
  node query.mjs create-doc "Spec" --file ./spec.md --space 90000000010
  node query.mjs edit-page abc123 p456 --file ./updated-content.md
  node query.mjs comment 86a1b2c3d --file ./review-notes.md
  node query.mjs description 86a1b2c3d --file ./task-description.md
  node query.mjs create-doc "Temp" --file ./draft.md --cleanup  # Deletes draft.md after

Inbox Examples:
  node query.mjs inbox                                         # Messages tab
  node query.mjs inbox activity                                # Activity tab
  node query.mjs inbox --me                                    # Assigned to me
  node query.mjs inbox --mentioned --unread                    # Unread @mentions
  node query.mjs inbox --since 7d                              # Only the last 7 days
  node query.mjs inbox --cleared                               # Already-cleared bundles
  node query.mjs inbox-stats                                   # Badge counts
  node query.mjs inbox-clear "<bundle_id>"                     # Dismiss notification
  node query.mjs inbox-read "<bundle_id>"                      # Mark as read
  node query.mjs inbox-clear-all                               # Clear all messages
  node query.mjs inbox-clear-all activity                      # Clear all activity`);
  process.exit(1);
}

async function main() {
  // Account management commands (run before initClient since they manage the config)
  if (command === 'accounts') {
    const accounts = listAccountsFn();
    if (accounts.length === 0) {
      console.log(`No accounts configured. Create accounts.json in ${stateBaseDir()}`);
    } else {
      for (const a of accounts) {
        const marker = a.isDefault ? ' (default)' : '';
        const token = a.hasApiToken ? 'token' : 'no-token';
        const jwt = a.hasJwt ? ', jwt' : '';
        console.log(`  ${a.id}${marker} [${token}${jwt}]${a.email ? ` - ${a.email}` : ''}`);
      }
    }
    return;
  }

  if (command === 'switch-account') {
    if (!targetInput) {
      console.error('Error: Account name required');
      console.error('Usage: node query.mjs switch-account <name>');
      process.exit(1);
    }
    setDefaultAccount(targetInput);
    console.log(`Default account set to: ${targetInput}`);
    return;
  }

  if (command === 'add-account') {
    if (!targetInput) {
      console.error('Error: Account name required');
      console.error('Usage: node query.mjs add-account <name> --token pk_...');
      process.exit(1);
    }
    // Look for --token in remaining args
    let token = null;
    for (let i = 0; i < args.length; i++) {
      if (args[i] === '--token' && args[i + 1]) {
        token = args[i + 1];
        break;
      }
    }
    if (!token) {
      console.error('Error: --token is required');
      console.error('Usage: node query.mjs add-account <name> --token pk_...');
      process.exit(1);
    }
    addAccountFn(targetInput, { apiToken: token });
    console.log(`Account "${targetInput}" added.`);
    return;
  }

  if (command === 'remove-account') {
    if (!targetInput) {
      console.error('Error: Account name required');
      console.error('Usage: node query.mjs remove-account <name>');
      process.exit(1);
    }
    removeAccountFn(targetInput);
    console.log(`Account "${targetInput}" removed.`);
    return;
  }

  // Initialize client with selected account
  initClient(accountArg);

  if (!command) {
    showUsage();
  }

  // Handle commands that don't require target input
  if (command === 'me') {
    try {
      const user = await getCurrentUser();
      if (jsonOutput) {
        console.log(JSON.stringify(user, null, 2));
      } else {
        console.log(`User: ${user.username}`);
        console.log(`Email: ${user.email}`);
        console.log(`ID: ${user.id}`);
        console.log(`Timezone: ${user.timezone || 'Not set'}`);
      }

      // Cache user ID if not already cached
      const creds = getActiveCredentials();
      if (!creds.userId) {
        updateAccountField(getActiveAccountId(), 'userId', user.id.toString());
        console.error('\nCached user ID to accounts.json');
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'my-tasks') {
    try {
      const teamId = await getTeamId();
      const userId = await getUserId();
      const tasks = await getMyTasks(teamId, userId);
      if (jsonOutput) {
        console.log(JSON.stringify(tasks, null, 2));
      } else {
        console.log(formatTaskList(tasks));
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'awaiting') {
    try {
      // Numeric flags are validated up front: a typo'd --max would otherwise
      // reach capFanOut() as NaN and be reported as a scan, not as a mistake.
      let minAgeDays = AWAITING_DEFAULTS.minAgeDays;
      if (daysArg !== null) {
        minAgeDays = Number(daysArg);
        if (!Number.isFinite(minAgeDays) || minAgeDays < 0) {
          console.error(`Error: --days must be a non-negative number, got "${daysArg}"`);
          process.exit(1);
        }
      }
      let maxTasks = AWAITING_DEFAULTS.maxTasks;
      if (maxArg !== null) {
        maxTasks = Number(maxArg);
        if (!Number.isInteger(maxTasks) || maxTasks < 1) {
          console.error(`Error: --max must be a positive integer, got "${maxArg}"`);
          process.exit(1);
        }
      }

      const teamId = await getTeamId();
      // The AUTHOR test is always against the token owner — that is the only
      // identity the API reports for anything this token writes. --assignee
      // only changes WHOSE tasks are scanned, never who counts as "answered".
      const ownerUserId = await getUserId();
      let scanUserId = ownerUserId;
      let scopeLabel = 'you';
      if (assigneeArg && assigneeArg.toLowerCase() !== 'me') {
        const user = await findUser(teamId, assigneeArg);
        if (!user) {
          console.error(`Error: User "${assigneeArg}" not found in team`);
          process.exit(1);
        }
        scanUserId = user.id.toString();
        scopeLabel = `${user.username || assigneeArg} (author test still: not the token owner)`;
      }

      const tasks = await getMyTasks(teamId, scanUserId);
      const result = await findAwaiting({
        tasks,
        ownerUserId,
        fetchComments: (taskId) => getComments(taskId),
        maxTasks,
        minAgeDays,
        onProgress: ({ done, total }) => {
          if (!jsonOutput) console.error(`  …read comments on ${done}/${total} task(s)`);
        },
      });

      if (jsonOutput) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        console.log(formatAwaiting(result, { scopeLabel }));
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'search') {
    if (!targetInput) {
      console.error('Error: Search query required');
      console.error('Usage: node query.mjs search "query"');
      process.exit(1);
    }
    try {
      const teamId = await getTeamId();
      // Resolve an optional server-side assignee narrowing (--me / --assignee).
      let assigneeId = null;
      if (filterMe) {
        assigneeId = await getUserId();
      } else if (assigneeArg) {
        if (assigneeArg.toLowerCase() === 'me') {
          assigneeId = await getUserId();
        } else {
          const user = await findUser(teamId, assigneeArg);
          if (!user) {
            console.error(`Error: User "${assigneeArg}" not found in team`);
            process.exit(1);
          }
          assigneeId = user.id;
        }
      }
      const tasks = await searchTasks(teamId, targetInput, {
        allSpaces: allSpacesFlag,
        since: sinceArg,
        allTime: allTimeFlag,
        assigneeId,
      });
      if (jsonOutput) {
        console.log(JSON.stringify(tasks, null, 2));
      } else {
        if (tasks.length === 0) {
          console.log(`No tasks found matching "${targetInput}"`);
        } else {
          console.log(formatTaskList(tasks));
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  // Document commands that may not require a target
  if (command === 'docs') {
    try {
      const workspaceId = await getTeamId();
      const options = targetInput ? { query: targetInput } : {};
      const result = await searchDocs(workspaceId, options);
      const docs = result.docs || [];
      if (jsonOutput) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        if (docs.length === 0) {
          console.log(targetInput ? `No docs found matching "${targetInput}"` : 'No docs found.');
        } else {
          console.log(formatDocList(docs));
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'create-doc') {
    if (!targetInput) {
      console.error('Error: Doc title required');
      console.error('Usage: node query.mjs create-doc "Doc Title" [--space <space_id>] [--content "content"]');
      process.exit(1);
    }
    try {
      const workspaceId = await getTeamId();
      const options = {};
      if (contentArg) {
        options.content = contentArg;
      }
      if (spaceArg) {
        const spaceId = parseSpaceId(spaceArg);
        options.parent = { id: spaceId, type: 4 };
      }
      const doc = await createDoc(workspaceId, targetInput, options);
      if (jsonOutput) {
        console.log(JSON.stringify(doc, null, 2));
      } else {
        console.log(`Doc created: ${doc.name}`);
        console.log(`ID: ${doc.id}`);
        if (spaceArg) {
          console.log(`Space: ${spaceArg}`);
        }
        if (doc.firstPageId) {
          console.log(`First page populated with content`);
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'lists') {
    if (!targetInput) {
      console.error('Error: Folder ID required');
      console.error('Usage: node query.mjs lists <folder_id>');
      process.exit(1);
    }
    try {
      const folderId = targetInput;
      const lists = await getLists(folderId);
      if (jsonOutput) {
        console.log(JSON.stringify(lists, null, 2));
      } else {
        if (lists.length === 0) {
          console.log('No lists found in this folder.');
        } else {
          for (const l of lists) {
            console.log(`  ${l.name} (ID: ${l.id})`);
          }
          console.log(`\nTotal: ${lists.length} list(s)`);
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  if (command === 'space-lists') {
    if (!targetInput) {
      console.error('Error: Space ID required');
      console.error('Usage: node query.mjs space-lists <space_id>');
      process.exit(1);
    }
    try {
      const spaceId = parseSpaceId(targetInput);
      const lists = await getFolderlessLists(spaceId);
      if (jsonOutput) {
        console.log(JSON.stringify(lists, null, 2));
      } else {
        if (lists.length === 0) {
          console.log('No folderless lists found in this space.');
        } else {
          for (const l of lists) {
            console.log(`  ${l.name} (ID: ${l.id})`);
          }
          console.log(`\nTotal: ${lists.length} list(s)`);
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  // Inbox commands (some don't require targetInput)
  if (command === 'inbox' || command === 'inbox-stats' || command === 'inbox-clear-all') {
    try {
      if (command === 'inbox') {
        const bundleType = targetInput || 'messages';
        // api/inbox.mjs has always accepted mentioned / unread / dateStart /
        // cursor; only --me was reachable from here, so the other filters
        // existed and could not be used.
        const opts = { bundleType, status: 'uncleared' };
        if (filterMe) opts.assignedToMe = true;
        if (mentionedFlag) opts.mentioned = true;
        if (unreadFlag) opts.unread = true;
        if (clearedFlag) opts.status = 'cleared';
        if (sinceArg) opts.dateStart = parseSinceInput(sinceArg).toISOString();
        if (cursorArg) opts.cursor = cursorArg;
        const result = await fetchAllInboxNotifications(opts);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(formatInboxNotifications(result));
        }
      } else if (command === 'inbox-stats') {
        const stats = await fetchInboxStats();
        if (jsonOutput) {
          console.log(JSON.stringify(stats, null, 2));
        } else {
          console.log(formatInboxStats(stats));
        }
      } else if (command === 'inbox-clear-all') {
        const bundleType = targetInput || 'messages';
        const result = await clearAllInboxBundles(bundleType);
        console.log(
          `Cleared ${result.cleared}/${result.total} ${bundleType} notification(s) ` +
            `found over ${result.pagesFetched} page(s).`
        );
        if (result.failed > 0) {
          console.log(`Failed: ${result.failed}`);
        }
        if (result.truncated) {
          console.log('🔴 TRUNCATED: hit the page safety bound — notifications remain uncleared.');
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  // Batch create from JSON file
  if (command === 'batch-create') {
    if (!fileArg && !contentArg) {
      console.error('Error: --file or --content required with a JSON plan');
      console.error('Usage: node query.mjs batch-create --file plan.json');
      console.error('       node query.mjs batch-create --content \'{"listId":"...","tasks":[...]}\'');
      process.exit(1);
    }
    try {
      const planText = contentArg || readFileSync(resolve(fileArg), 'utf-8');
      const plan = JSON.parse(planText);
      const dryRun = args.includes('--dry-run');
      const verbose = !jsonOutput; // verbose by default unless --json
      const result = await executeBatchCreate(plan, { verbose, dryRun });
      if (jsonOutput) {
        console.log(JSON.stringify(result, null, 2));
      } else {
        console.log(`\n${result.message}`);
        if (result.errors.length > 0) {
          console.log('\nErrors:');
          for (const e of result.errors) {
            console.log(`  ${e.ref || e.name || '?'}: ${e.error}`);
          }
        }
      }
    } catch (err) {
      console.error('Error:', err.message);
      process.exit(1);
    }
    return;
  }

  // All other commands require a target
  if (!targetInput) {
    showUsage();
  }

  try {
    switch (command) {
      case 'get': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await getTask(ids[0], includeSubtasks);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(formatTask(task));
          }
        } else {
          const results = await bulkExecute(ids, id => getTask(id, includeSubtasks));
          if (jsonOutput) {
            console.log(JSON.stringify(results.succeeded.map(s => s.result), null, 2));
          } else {
            for (const s of results.succeeded) {
              console.log(formatTask(s.result));
              console.log('');
            }
            if (results.failed.length > 0) {
              console.log(formatBulkResults({ ...results, succeeded: [] }));
            }
          }
        }
        break;
      }

      case 'comments': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        const comments = await getComments(taskId);

        // If --threads flag, auto-expand all threads with replies
        let threadMap = {};
        if (threadsFlag) {
          const threaded = comments.filter(c => parseInt(c.reply_count, 10) > 0);
          if (threaded.length > 0) {
            const threadResults = await Promise.all(
              threaded.map(c => getThreadedComments(c.id).then(replies => [c.id, replies]))
            );
            for (const [id, replies] of threadResults) {
              threadMap[id] = replies;
            }
          }
        }

        if (jsonOutput) {
          if (threadsFlag) {
            // Embed replies into each comment for JSON output
            const enriched = comments.map(c => ({
              ...c,
              replies: threadMap[c.id] || [],
            }));
            console.log(JSON.stringify(enriched, null, 2));
          } else {
            console.log(JSON.stringify(comments, null, 2));
          }
        } else {
          console.log(formatComments(comments, threadMap));
        }
        break;
      }

      case 'thread': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs thread <comment_id>');
          process.exit(1);
        }
        const replies = await getThreadedComments(commentId);
        if (jsonOutput) {
          console.log(JSON.stringify(replies, null, 2));
        } else {
          console.log(formatThread(null, replies));
        }
        break;
      }

      case 'reply': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs reply <comment_id> "reply text"');
          process.exit(1);
        }
        if (!commentText) {
          console.error('Error: Reply text required');
          console.error('Usage: node query.mjs reply <comment_id> "reply text"');
          process.exit(1);
        }
        const result = await replyToComment(commentId, commentText);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Reply posted successfully (ID: ${result.id || 'ok'})`);
        }
        break;
      }

      case 'comment': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!commentText) {
          console.error('Error: Comment text required');
          console.error('Usage: node query.mjs comment <url|id[,id,...]> "Your comment" [--attach ./file.png]');
          process.exit(1);
        }
        // Resolve --attach file paths for comment attachments
        const commentAttachFiles = attachArgs.map(f => resolve(f));
        if (commentAttachFiles.length > 0) {
          for (const fp of commentAttachFiles) {
            if (!existsSync(fp)) {
              console.error(`Error: Attachment file not found: ${fp}`);
              process.exit(1);
            }
          }
        }
        if (ids.length === 1) {
          const result = await postComment(ids[0], commentText);
          if (jsonOutput) {
            const output = { comment: result };
            if (commentAttachFiles.length > 0) {
              output.attachments = await uploadAttachments(ids[0], commentAttachFiles);
            }
            console.log(JSON.stringify(output, null, 2));
          } else {
            console.log(`Comment posted successfully (ID: ${result.id})`);
            if (commentAttachFiles.length > 0) {
              const attResults = await uploadAttachments(ids[0], commentAttachFiles);
              for (const r of attResults) {
                const att = r.attachment || r;
                console.log(`  Attached: ${att.title || att.name || 'file'}`);
              }
            }
          }
        } else {
          const results = await bulkExecute(ids, async (id) => {
            const comment = await postComment(id, commentText);
            let attachments;
            if (commentAttachFiles.length > 0) {
              attachments = await uploadAttachments(id, commentAttachFiles);
            }
            return { comment, attachments };
          });
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: 'Comment posted on' }));
            if (commentAttachFiles.length > 0) {
              console.log(`  (${commentAttachFiles.length} file(s) attached to each task)`);
            }
          }
        }
        break;
      }

      case 'status': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!commentText) {
          // No status provided - show available statuses (single task only)
          const statuses = await getAvailableStatuses(ids[0]);
          console.log('Available statuses:');
          for (const s of statuses) {
            console.log(`  - "${s.status}" (${s.type})`);
          }
          break;
        }
        if (ids.length === 1) {
          const { task, matchedStatus } = await updateTaskStatus(ids[0], commentText);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(`Status updated to "${matchedStatus.status}"`);
          }
        } else {
          const results = await bulkExecute(ids, id => updateTaskStatus(id, commentText));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, {
              action: 'Updated',
              formatItem: s => `${s.id}: status → "${s.result.matchedStatus.status}"`,
            }));
          }
        }
        break;
      }

      case 'tasks': {
        const listId = parseListId(targetInput);
        if (!listId) {
          console.error('Error: Could not parse list ID from input');
          process.exit(1);
        }

        let assigneeId = null;
        if (filterMe) {
          assigneeId = await getUserId();
        }

        const tasks = await getTasksInList(listId, assigneeId);
        if (jsonOutput) {
          console.log(JSON.stringify(tasks, null, 2));
        } else {
          console.log(formatTaskList(tasks));
        }
        break;
      }

      case 'create': {
        // Support: create <list_id> "title" [options] OR create "title" [options] (uses default list)
        // Options: --assignee/-a <user>, --due/-d <date>, --description/--desc <text>
        let listId = parseListId(targetInput);
        let title = arg2;

        // If targetInput doesn't parse as a list ID, treat it as the title
        if (!listId) {
          title = targetInput;
          listId = getActiveCredentials().defaultListId;
          if (!listId) {
            console.error('Error: No list ID provided and no defaultListId set for this account');
            console.error('Usage: node query.mjs create <list_id> "Task title" [options]');
            console.error('   Or: Set defaultListId in accounts.json to use: node query.mjs create "Task title"');
            console.error('Options: --assignee/-a <user>, --due/-d <date>, --description/--desc <text>');
            process.exit(1);
          }
        }

        if (!title) {
          console.error('Error: Task title required');
          console.error('Usage: node query.mjs create <list_id> "Task title" [options]');
          console.error('Options: --assignee/-a <user>, --due/-d <date>, --description/--desc <text>');
          process.exit(1);
        }

        // Build options from flags
        const options = {};
        if (descriptionArg) {
          // Use markdown_description for proper markdown rendering in ClickUp
          // Note: Task descriptions use markdown_description (ClickUp parses),
          // while comments use JSON array format (we parse via markdownToClickUp)
          options.markdown_description = descriptionArg;
        }
        // Agent-object hygiene: an explicit close-condition for the marker
        // createTask() stamps. Validated HERE so an off-allowlist value fails
        // loudly at the CLI instead of silently degrading to `manual` deep
        // inside the stamp — a wrong condition that reads as accepted is how an
        // object ends up believed-tracked and actually immortal.
        if (condArg) {
          try {
            options.agentCond = validateCond(condArg);
          } catch (e) {
            console.error(`Error: ${e.message}`);
            console.error(`Allowed --cond kinds: ${[...COND_KINDS].sort().join(', ')}`);
            process.exit(1);
          }
        }
        if (dueArg) {
          const dueDate = parseDateInput(dueArg);
          options.due_date = dueDate.getTime();
        }
        if (assigneeArg) {
          let assigneeId;
          if (assigneeArg.toLowerCase() === 'me') {
            // Special case: "me" means the current authenticated user
            assigneeId = await getUserId();
          } else {
            const teamId = await getTeamId();
            const user = await findUser(teamId, assigneeArg);
            if (!user) {
              console.error(`Error: User "${assigneeArg}" not found in team`);
              process.exit(1);
            }
            assigneeId = user.id;
          }
          options.assignees = [assigneeId];
        }

        const task = await createTask(listId, title, options);
        if (jsonOutput) {
          console.log(JSON.stringify(task, null, 2));
        } else {
          console.log(`Task created: ${task.name}`);
          console.log(`ID: ${task.id}`);
          if (task.assignees?.length) {
            console.log(`Assignees: ${task.assignees.map(a => a.username).join(', ')}`);
          }
          if (task.due_date) {
            console.log(`Due: ${new Date(parseInt(task.due_date, 10)).toLocaleDateString()}`);
          }
          console.log(`URL: ${task.url}`);
        }
        break;
      }

      case 'assign': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: User required');
          console.error('Usage: node query.mjs assign <task[,task,...]> <user>');
          process.exit(1);
        }
        const teamId = await getTeamId();
        const user = await findUser(teamId, arg2);
        if (!user) {
          console.error(`Error: User "${arg2}" not found in team`);
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await assignTask(ids[0], [user.id]);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(`Task assigned to ${user.username || user.email}`);
          }
        } else {
          const results = await bulkExecute(ids, id => assignTask(id, [user.id]));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: `Assigned to ${user.username || user.email}:` }));
          }
        }
        break;
      }

      case 'due': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Due date required');
          console.error('Usage: node query.mjs due <task[,task,...]> "date"');
          console.error('Examples: "tomorrow", "next friday", "2024-01-15", "+3d"');
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await setDueDate(ids[0], arg2);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            const dueDate = task.due_date ? new Date(parseInt(task.due_date, 10)).toLocaleDateString() : 'cleared';
            console.log(`Due date set to: ${dueDate}`);
          }
        } else {
          const results = await bulkExecute(ids, id => setDueDate(id, arg2));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: 'Due date set on' }));
          }
        }
        break;
      }

      case 'start': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Start date required');
          console.error('Usage: node query.mjs start <task[,task,...]> "date"');
          console.error('Examples: "today", "tomorrow", "2024-01-15", "+3d"');
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await setStartDate(ids[0], arg2);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            const startDate = task.start_date ? new Date(parseInt(task.start_date, 10)).toLocaleDateString() : 'cleared';
            console.log(`Start date set to: ${startDate}`);
          }
        } else {
          const results = await bulkExecute(ids, id => setStartDate(id, arg2));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: 'Start date set on' }));
          }
        }
        break;
      }

      case 'schedule': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2 || !arg3) {
          console.error('Error: Both start and due dates required');
          console.error('Usage: node query.mjs schedule <task> "start_date" "due_date"');
          console.error('Examples: node query.mjs schedule abc123 "today" "friday"');
          process.exit(1);
        }
        const task = await setDates(taskId, { start: arg2, due: arg3 });
        if (jsonOutput) {
          console.log(JSON.stringify(task, null, 2));
        } else {
          const startDate = task.start_date ? new Date(parseInt(task.start_date, 10)).toLocaleDateString() : 'not set';
          const dueDate = task.due_date ? new Date(parseInt(task.due_date, 10)).toLocaleDateString() : 'not set';
          console.log(`Scheduled: ${startDate} → ${dueDate}`);
        }
        break;
      }

      case 'rename': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: New name required');
          console.error('Usage: node query.mjs rename <task> "new name"');
          process.exit(1);
        }
        const task = await updateTask(taskId, { name: arg2 });
        if (jsonOutput) {
          console.log(JSON.stringify(task, null, 2));
        } else {
          console.log(`Task renamed to: ${task.name}`);
        }
        break;
      }

      case 'priority': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Priority level required');
          console.error('Usage: node query.mjs priority <task[,task,...]> <level>');
          console.error('Levels: urgent, high, normal, low, none');
          process.exit(1);
        }
        if (ids.length === 1) {
          const { task, priority } = await setPriority(ids[0], arg2);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(`Priority set to: ${priority}`);
          }
        } else {
          const results = await bulkExecute(ids, id => setPriority(id, arg2));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, {
              action: 'Priority set on',
              formatItem: s => `${s.id}: priority → ${s.result.priority}`,
            }));
          }
        }
        break;
      }

      case 'subtask': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Subtask title required');
          console.error('Usage: node query.mjs subtask <task> "Subtask title"');
          process.exit(1);
        }
        const subtask = await createSubtask(taskId, arg2);
        if (jsonOutput) {
          console.log(JSON.stringify(subtask, null, 2));
        } else {
          console.log(`Subtask created: ${subtask.name}`);
          console.log(`ID: ${subtask.id}`);
          console.log(`URL: ${subtask.url}`);
        }
        break;
      }

      case 'move': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Target list ID required');
          console.error('Usage: node query.mjs move <task> <list_id>');
          process.exit(1);
        }
        const listId = parseListId(arg2);
        const workspaceId = await getTeamId();
        const task = await moveTask(taskId, listId, workspaceId);
        if (jsonOutput) {
          console.log(JSON.stringify(task, null, 2));
        } else {
          console.log(`Task moved successfully`);
          if (task.url) console.log(`URL: ${task.url}`);
        }
        break;
      }

      case 'link': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: URL required');
          console.error('Usage: node query.mjs link <task> <url> ["description"]');
          process.exit(1);
        }
        const result = await addExternalLink(taskId, arg2, arg3);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Link added as comment (ID: ${result.id})`);
        }
        break;
      }

      case 'checklist': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Checklist item required');
          console.error('Usage: node query.mjs checklist <task> "Item text"');
          process.exit(1);
        }
        const { checklist, item } = await addChecklistItemToTask(taskId, arg2);
        if (jsonOutput) {
          console.log(JSON.stringify({ checklist, item }, null, 2));
        } else {
          console.log(`Added to checklist "${checklist.name}"`);
        }
        break;
      }

      case 'update-comment': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs update-comment <comment_id> "new text"');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Comment text required');
          console.error('Usage: node query.mjs update-comment <comment_id> "new text"');
          process.exit(1);
        }
        const result = await updateComment(commentId, { comment_text: arg2 });
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Comment ${commentId} updated`);
        }
        break;
      }

      case 'resolve-comment': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs resolve-comment <comment_id>');
          process.exit(1);
        }
        const result = await updateComment(commentId, { resolved: true });
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Comment ${commentId} resolved`);
        }
        break;
      }

      case 'delete-comment': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs delete-comment <comment_id>');
          process.exit(1);
        }
        await deleteComment(commentId);
        if (jsonOutput) {
          console.log(JSON.stringify({ deleted: true, commentId }, null, 2));
        } else {
          console.log(`Comment ${commentId} deleted`);
        }
        break;
      }

      case 'watch': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: User required');
          console.error('Usage: node query.mjs watch <task> <user>');
          process.exit(1);
        }
        const teamId = await getTeamId();
        const user = await findUser(teamId, arg2);
        if (!user) {
          console.error(`Error: User "${arg2}" not found in team`);
          process.exit(1);
        }
        const result = await addWatcher(taskId, user.id);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Added ${user.username || user.email} as watcher`);
        }
        break;
      }

      case 'unwatch': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: User required');
          console.error('Usage: node query.mjs unwatch <task> <user>');
          process.exit(1);
        }
        const teamId = await getTeamId();
        const user = await findUser(teamId, arg2);
        if (!user) {
          console.error(`Error: User "${arg2}" not found in team`);
          process.exit(1);
        }
        const result = await removeWatcher(taskId, user.id);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Removed ${user.username || user.email} as watcher`);
        }
        break;
      }

      case 'tag': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Tag name required');
          console.error('Usage: node query.mjs tag <task[,task,...]> "tag_name"');
          process.exit(1);
        }
        if (ids.length === 1) {
          await addTag(ids[0], arg2);
          if (jsonOutput) {
            console.log(JSON.stringify({ tagged: true, taskId: ids[0], tag: arg2 }, null, 2));
          } else {
            console.log(`Tag "${arg2}" added to task`);
          }
        } else {
          const results = await bulkExecute(ids, id => addTag(id, arg2));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: `Tagged "${arg2}" on` }));
          }
        }
        break;
      }

      case 'remove-tag': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Tag name required');
          console.error('Usage: node query.mjs remove-tag <task[,task,...]> "tag_name"');
          process.exit(1);
        }
        if (ids.length === 1) {
          await removeTag(ids[0], arg2);
          if (jsonOutput) {
            console.log(JSON.stringify({ removed: true, taskId: ids[0], tag: arg2 }, null, 2));
          } else {
            console.log(`Tag "${arg2}" removed from task`);
          }
        } else {
          const results = await bulkExecute(ids, id => removeTag(id, arg2));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: `Removed tag "${arg2}" from` }));
          }
        }
        break;
      }

      case 'description': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Description text required');
          console.error('Usage: node query.mjs description <task> "description text"');
          console.error('Markdown formatting is supported.');
          process.exit(1);
        }
        // Use markdown_description for proper markdown rendering in ClickUp
        // Note: Unlike comments (which use JSON array format via markdownToClickUp),
        // task descriptions use ClickUp's native markdown_description field
        const task = await updateTask(taskId, { markdown_description: arg2 });
        if (jsonOutput) {
          console.log(JSON.stringify(task, null, 2));
        } else {
          console.log('Task description updated');
          console.log(`URL: ${task.url}`);
        }
        break;
      }

      case 'depends': {
        // Set task as waiting on another task
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Other task ID required');
          console.error('Usage: node query.mjs depends <task> <waits_on_task>');
          console.error('This sets the first task as waiting on/blocked by the second task.');
          process.exit(1);
        }
        const dependsOnId = parseTaskId(arg2);
        if (!dependsOnId) {
          console.error('Error: Could not parse dependency task ID');
          process.exit(1);
        }
        const result = await addDependency(taskId, { depends_on: dependsOnId });
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Task ${taskId} now waits on task ${dependsOnId}`);
        }
        break;
      }

      case 'blocks': {
        // Set task as blocking another task
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Other task ID required');
          console.error('Usage: node query.mjs blocks <task> <blocked_task>');
          console.error('This sets the first task as blocking the second task.');
          process.exit(1);
        }
        const blocksId = parseTaskId(arg2);
        if (!blocksId) {
          console.error('Error: Could not parse blocked task ID');
          process.exit(1);
        }
        const result = await addDependency(taskId, { dependency_of: blocksId });
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Task ${taskId} now blocks task ${blocksId}`);
        }
        break;
      }

      case 'task-link': {
        // Create bidirectional link between tasks
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Other task ID required');
          console.error('Usage: node query.mjs task-link <task> <other_task>');
          console.error('This creates a bidirectional link between tasks.');
          process.exit(1);
        }
        const linksToId = parseTaskId(arg2);
        if (!linksToId) {
          console.error('Error: Could not parse target task ID');
          process.exit(1);
        }
        const result = await addTaskLink(taskId, linksToId);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Tasks ${taskId} and ${linksToId} are now linked`);
        }
        break;
      }

      case 'archive': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await archiveTask(ids[0]);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(`Task archived: ${task.name || ids[0]}`);
            if (task.url) console.log(`URL: ${task.url}`);
          }
        } else {
          const results = await bulkExecute(ids, id => archiveTask(id));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: 'Archived' }));
          }
        }
        break;
      }

      case 'unarchive': {
        const ids = splitIds(targetInput).map(parseTaskId).filter(Boolean);
        if (ids.length === 0) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        if (ids.length === 1) {
          const task = await unarchiveTask(ids[0]);
          if (jsonOutput) {
            console.log(JSON.stringify(task, null, 2));
          } else {
            console.log(`Task restored: ${task.name || ids[0]}`);
            if (task.url) console.log(`URL: ${task.url}`);
          }
        } else {
          const results = await bulkExecute(ids, id => unarchiveTask(id));
          if (jsonOutput) {
            console.log(JSON.stringify(results, null, 2));
          } else {
            console.log(formatBulkResults(results, { action: 'Restored' }));
          }
        }
        break;
      }

      case 'claim': {
        const sessionId = process.env.CLAUDE_SESSION_ID;
        if (!sessionId) {
          console.error('Error: No session ID available (CLAUDE_SESSION_ID not set)');
          console.error('This command must be run from within a Claude Code session.');
          process.exit(1);
        }
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Could not parse task ID from input');
          process.exit(1);
        }
        const field = await findCustomFieldByName(taskId, 'session');
        if (!field) {
          console.error('Error: No "Session ID" custom field found on this task.');
          console.error('Add a text custom field named "Session ID" to the task\'s list in ClickUp.');
          process.exit(1);
        }
        await setCustomField(taskId, field.id, sessionId);
        if (jsonOutput) {
          console.log(JSON.stringify({ taskId, fieldId: field.id, fieldName: field.name, sessionId }, null, 2));
        } else {
          console.log(`Session linked to task ${taskId}`);
          console.log(`Field: ${field.name}`);
          console.log(`Session: ${sessionId}`);
        }
        break;
      }

      // List commands
      case 'list': {
        const listId = parseListId(targetInput);
        if (!listId) {
          console.error('Error: Could not parse list ID from input');
          process.exit(1);
        }
        const list = await getList(listId);
        if (jsonOutput) {
          console.log(JSON.stringify(list, null, 2));
        } else {
          console.log(formatList(list));
        }
        break;
      }

      case 'create-list': {
        const spaceId = parseSpaceId(targetInput);
        if (!spaceId) {
          console.error('Error: Could not parse space ID from input');
          console.error('Usage: node query.mjs create-list <space_id|space_url> "List Name"');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: List name required');
          console.error('Usage: node query.mjs create-list <space_id|space_url> "List Name"');
          process.exit(1);
        }
        const options = {};
        if (contentArg) {
          options.content = contentArg;
        }
        const list = await createList(spaceId, arg2, options);
        if (jsonOutput) {
          console.log(JSON.stringify(list, null, 2));
        } else {
          console.log(`List created: ${list.name}`);
          console.log(`ID: ${list.id}`);
        }
        break;
      }

      case 'update-list': {
        const listId = parseListId(targetInput);
        if (!listId) {
          console.error('Error: Could not parse list ID from input');
          console.error('Usage: node query.mjs update-list <list_id> --name "new name" --content "description"');
          process.exit(1);
        }
        if (!nameArg && !contentArg) {
          console.error('Error: At least --name or --content is required');
          console.error('Usage: node query.mjs update-list <list_id> --name "new name" --content "description"');
          process.exit(1);
        }
        const updates = {};
        if (nameArg) {
          updates.name = nameArg;
        }
        if (contentArg) {
          updates.content = contentArg;
        }
        const list = await updateList(listId, updates);
        if (jsonOutput) {
          console.log(JSON.stringify(list, null, 2));
        } else {
          console.log(`List updated: ${list.name || listId}`);
          if (list.id) console.log(`ID: ${list.id}`);
        }
        break;
      }

      case 'delete-list': {
        const listId = parseListId(targetInput);
        if (!listId) {
          console.error('Error: Could not parse list ID from input');
          console.error('Usage: node query.mjs delete-list <list_id>');
          process.exit(1);
        }
        await deleteList(listId);
        if (jsonOutput) {
          console.log(JSON.stringify({ deleted: true, listId }, null, 2));
        } else {
          console.log(`List ${listId} deleted`);
        }
        break;
      }

      // Document commands
      case 'doc': {
        const docId = parseDocId(targetInput);
        if (!docId) {
          console.error('Error: Could not parse doc ID from input');
          process.exit(1);
        }
        const workspaceId = await getTeamId();
        const doc = await getDoc(workspaceId, docId);
        const pages = await getDocPageListing(workspaceId, docId);
        if (jsonOutput) {
          console.log(JSON.stringify({ doc, pages }, null, 2));
        } else {
          console.log(formatDoc(doc));
          console.log('');
          console.log('Pages:');
          console.log(formatPageList(pages));
        }
        break;
      }

      case 'page': {
        const docId = parseDocId(targetInput);
        if (!docId) {
          console.error('Error: Could not parse doc ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Page ID required');
          console.error('Usage: node query.mjs page <doc_id> <page_id>');
          process.exit(1);
        }
        const pageId = parsePageId(arg2);
        const workspaceId = await getTeamId();
        const page = await getPage(workspaceId, docId, pageId);
        if (jsonOutput) {
          console.log(JSON.stringify(page, null, 2));
        } else {
          console.log(formatPage(page));
        }
        break;
      }

      case 'create-page': {
        const docId = parseDocId(targetInput);
        if (!docId) {
          console.error('Error: Could not parse doc ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Page title required');
          console.error('Usage: node query.mjs create-page <doc_id> "Page Title" [--content "content"]');
          process.exit(1);
        }
        const workspaceId = await getTeamId();
        const options = {};
        if (contentArg) {
          options.content = contentArg;
        }
        if (parentArg) {
          options.parentPageId = parentArg;
        }
        const page = await createPage(workspaceId, docId, arg2, options);
        if (jsonOutput) {
          console.log(JSON.stringify(page, null, 2));
        } else {
          console.log(`Page created: ${page.name}`);
          console.log(`ID: ${page.id}`);
          if (parentArg) {
            console.log(`Parent: ${parentArg}`);
          }
        }
        break;
      }

      case 'edit-page': {
        const docId = parseDocId(targetInput);
        if (!docId) {
          console.error('Error: Could not parse doc ID from input');
          process.exit(1);
        }
        if (!arg2) {
          console.error('Error: Page ID required');
          console.error('Usage: node query.mjs edit-page <doc_id> <page_id> [--content "content"] [--name "name"]');
          process.exit(1);
        }
        if (!contentArg && !nameArg && !archiveFlag) {
          console.error('Error: At least --content, --name, or --archive is required');
          console.error('Usage: node query.mjs edit-page <doc_id> <page_id> [--content "content"] [--name "name"] [--archive]');
          process.exit(1);
        }
        const pageId = parsePageId(arg2);
        const workspaceId = await getTeamId();
        const updates = {};
        if (contentArg) {
          updates.content = contentArg;
        }
        if (nameArg) {
          updates.name = nameArg;
        }
        if (archiveFlag) {
          updates.archived = true;
        }
        const page = await editPage(workspaceId, docId, pageId, updates);
        if (jsonOutput) {
          console.log(JSON.stringify(page, null, 2));
        } else {
          if (archiveFlag) {
            console.log('Page archived successfully');
          } else {
            console.log('Page updated successfully');
          }
          if (page.id) {
            console.log(`ID: ${page.id}`);
          }
        }
        break;
      }

      // Doc comment commands (internal API, requires JWT)
      case 'doc-comments': {
        const pageId = targetInput;
        if (!pageId) {
          console.error('Error: Page ID required');
          console.error('Usage: node query.mjs doc-comments <page_id>');
          process.exit(1);
        }
        const { comments } = await getDocPageComments(pageId);
        if (jsonOutput) {
          console.log(JSON.stringify(comments, null, 2));
        } else {
          console.log(formatDocComments(comments));
        }
        break;
      }

      case 'doc-comment': {
        const pageId = targetInput;
        if (!pageId) {
          console.error('Error: Page ID required');
          console.error('Usage: node query.mjs doc-comment <page_id> "comment text"');
          process.exit(1);
        }
        if (!commentText) {
          console.error('Error: Comment text required');
          console.error('Usage: node query.mjs doc-comment <page_id> "comment text"');
          process.exit(1);
        }
        const result = await postDocPageComment(pageId, commentText);
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Doc comment posted on page ${pageId}`);
          if (result.id) console.log(`ID: ${result.id}`);
        }
        break;
      }

      case 'doc-reply': {
        const commentId = targetInput;
        if (!commentId) {
          console.error('Error: Comment ID required');
          console.error('Usage: node query.mjs doc-reply <comment_id> "reply text" --page <page_id>');
          process.exit(1);
        }
        if (!commentText) {
          console.error('Error: Reply text required');
          console.error('Usage: node query.mjs doc-reply <comment_id> "reply text" --page <page_id>');
          process.exit(1);
        }
        if (!pageArg) {
          console.error('Error: --page <page_id> is required for doc-reply');
          console.error('Usage: node query.mjs doc-reply <comment_id> "reply text" --page <page_id>');
          process.exit(1);
        }
        const result = await replyToDocComment(commentId, commentText, { pageId: pageArg });
        if (jsonOutput) {
          console.log(JSON.stringify(result, null, 2));
        } else {
          console.log(`Reply posted to comment ${commentId}`);
          if (result.id) console.log(`ID: ${result.id}`);
        }
        break;
      }

      // ==================== Inbox Commands ====================

      case 'inbox-clear': {
        if (!targetInput) {
          console.error('Error: Bundle ID required');
          console.error('Usage: node query.mjs inbox-clear <bundle_id[,bundle_id,...]>');
          console.error('Get bundle IDs from: node query.mjs inbox --json');
          process.exit(1);
        }
        const ids = splitIds(targetInput);
        if (ids.length === 1) {
          await clearInboxBundle(ids[0]);
          console.log('Notification cleared.');
        } else {
          const results = await bulkExecute(ids, id => clearInboxBundle(id));
          console.log(formatBulkResults(results, { action: 'Cleared' }));
        }
        break;
      }

      case 'inbox-read': {
        if (!targetInput) {
          console.error('Error: Bundle ID required');
          console.error('Usage: node query.mjs inbox-read <bundle_id[,bundle_id,...]>');
          process.exit(1);
        }
        const ids = splitIds(targetInput);
        if (ids.length === 1) {
          await markInboxBundleRead(ids[0]);
          console.log('Notification marked as read.');
        } else {
          const results = await bulkExecute(ids, id => markInboxBundleRead(id));
          console.log(formatBulkResults(results, { action: 'Marked as read:' }));
        }
        break;
      }

      case 'attach': {
        const taskId = parseTaskId(targetInput);
        if (!taskId) {
          console.error('Error: Task ID or URL required');
          console.error('Usage: node query.mjs attach <url|id> --attach ./file1.png --attach ./file2.pdf');
          console.error('');
          console.error('You can also use --file for a single file:');
          console.error('  node query.mjs attach <url|id> --file ./screenshot.png');
          process.exit(1);
        }
        // Collect files from --attach args and --file arg
        const filesToUpload = [...attachArgs.map(f => resolve(f))];
        if (fileArg && !filesToUpload.includes(resolve(fileArg))) {
          filesToUpload.push(resolve(fileArg));
        }
        if (filesToUpload.length === 0) {
          console.error('Error: At least one file required');
          console.error('Usage: node query.mjs attach <url|id> --attach ./file1.png [--attach ./file2.pdf]');
          process.exit(1);
        }
        // Validate all files exist before uploading
        for (const fp of filesToUpload) {
          if (!existsSync(fp)) {
            console.error(`Error: File not found: ${fp}`);
            process.exit(1);
          }
        }
        const attachResults = await uploadAttachments(taskId, filesToUpload);
        if (jsonOutput) {
          console.log(JSON.stringify(attachResults, null, 2));
        } else {
          for (const result of attachResults) {
            const att = result.attachment || result;
            const title = att.title || att.name || 'file';
            const url = att.url || att.url_w_query || '';
            console.log(`Attached: ${title}${url ? ` (${url})` : ''}`);
          }
          console.log(`\n${filesToUpload.length} file(s) attached to task ${taskId}`);
        }
        break;
      }

      case 'fetch-image':
      case 'fetch-attachment': {
        const url = targetInput;
        if (!url) {
          console.error('Error: Attachment URL required');
          console.error('Usage: node query.mjs fetch-image <url> [--output path]');
          console.error('');
          console.error('Downloads a ClickUp attachment to a local temp file.');
          console.error('The URL is shown in comment output as [Attachment: name](url)');
          process.exit(1);
        }

        // Determine output path
        const outputFlag = args.indexOf('--output');
        let outputPath;
        if (outputFlag !== -1 && args[outputFlag + 1]) {
          outputPath = resolve(args[outputFlag + 1]);
        } else {
          // Extract filename from URL or use a default
          const urlPath = new URL(url).pathname;
          const filename = urlPath.split('/').pop() || 'attachment';
          const clickupTmp = join(tmpdir(), 'clickup-attachments');
          mkdirSync(clickupTmp, { recursive: true });
          outputPath = join(clickupTmp, filename);
        }

        // Fetch the attachment
        const creds = getActiveCredentials();
        const resp = await fetch(url, {
          headers: { Authorization: creds.apiToken },
        });
        if (!resp.ok) {
          // Retry without auth (CDN URLs may not need it)
          const resp2 = await fetch(url);
          if (!resp2.ok) {
            console.error(`Error: Failed to fetch attachment (${resp2.status})`);
            process.exit(1);
          }
          const buffer = Buffer.from(await resp2.arrayBuffer());
          writeFileSync(outputPath, buffer);
        } else {
          const buffer = Buffer.from(await resp.arrayBuffer());
          writeFileSync(outputPath, buffer);
        }

        if (jsonOutput) {
          console.log(JSON.stringify({ path: outputPath, url }, null, 2));
        } else {
          console.log(`Downloaded: ${outputPath}`);
        }
        break;
      }

      default:
        console.error(`Unknown command: ${command}`);
        showUsage();
    }
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
}

main().then(() => {
  // --cleanup: delete the source file after successful execution
  if (cleanupFlag && fileArg) {
    const filePath = resolve(fileArg);
    try {
      unlinkSync(filePath);
    } catch {
      // Silently ignore cleanup failures
    }
  }
});
