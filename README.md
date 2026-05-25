# personal-assistant-mcp

MCP server for personal-assistant tasks: Obsidian tasks/notes/digests, FreshRSS, CalDAV, Proton email.

Companion to [obsidian-livesync-mcp](https://github.com/dismantl/obsidian-livesync-mcp): this server imports `obsidian-livesync-mcp`'s vault client to handle Obsidian operations, and adds higher-level tools for daily-note management, task routing, weekly reviews, RSS/release digests, and Proton mail.

## Status

Alpha. The server includes MCP tools across tasks, daily/weekly notes, digests,
FreshRSS, CalDAV, release-state tracking, and Proton Mail. It can run over stdio
for local MCP clients or authenticated streamable HTTP for hosted use.

## Exposed tools

### Core

| Tool | Purpose |
|---|---|
| `health` | Return server health and transport status. |

### Tasks

| Tool | Purpose |
|---|---|
| `tasks_list` | List open tasks across the vault, with optional folder, priority, and due-date filters. |
| `tasks_search` | Search open tasks by case-insensitive substring, optionally within a folder. |
| `tasks_add` | Add a task to a vault file, creating the file when needed. |
| `tasks_complete` | Mark a task complete by task ID or exact body. |
| `tasks_uncomplete` | Reopen a completed task and clear its done date. |
| `tasks_update` | Update task body, priority, due date, or scheduled date. |
| `tasks_delete` | Remove a task line from a file. |
| `tasks_move` | Move a task between files with idempotent content matching. |
| `tasks_render_planner` | Render the TODO planner view from its frontmatter spec. |

### Daily notes

| Tool | Purpose |
|---|---|
| `daily_create_today` | Create today's daily note from the template if it is absent. |
| `daily_template` | Return the daily-note template body. |
| `daily_read_today` | Read today's daily note, or return `None` if it does not exist. |
| `daily_read` | Read a specific daily note by ISO date. |
| `daily_read_recent` | Return the most recent daily notes, newest first. |
| `daily_write_today` | Overwrite today's daily note content. |
| `daily_append_log` | Append a timestamped entry to today's `## Log` section. |
| `daily_append_inbox` | Add a task to today's daily-note `## Inbox` section. |
| `daily_archive_old` | Move old daily notes outside the current month into the archive. |

### Weekly reviews

| Tool | Purpose |
|---|---|
| `weekly_latest` | Return the most recent weekly review note. |
| `weekly_read` | Read a specific weekly review by ISO date. |
| `weekly_write_current` | Write or overwrite this week's weekly review. |

### Digests

| Tool | Purpose |
|---|---|
| `digest_read` | Read an RSS or releases digest for a date. |
| `digest_write` | Write or overwrite an RSS or releases digest for a date. |

### Release state

| Tool | Purpose |
|---|---|
| `release_state_read` | Return the current release-tracker state. |
| `release_state_update` | Merge release-tracker entries into persisted state. |

### FreshRSS

| Tool | Purpose |
|---|---|
| `freshrss_unread` | List unread FreshRSS item IDs from the last seven days. |
| `freshrss_contents` | Fetch full content payloads for currently unread FreshRSS items. |

### Calendar

| Tool | Purpose |
|---|---|
| `calendar_list` | List active CalDAV calendars. |
| `calendar_today` | Fetch events for the next 24 hours in the configured vault timezone. |
| `calendar_week` | Fetch events for the next seven days in the configured vault timezone. |

### Proton mail

| Tool | Purpose |
|---|---|
| `email_primary_unread` | List unread messages in the primary inbox. |
| `email_primary_recent` | List recent messages in the primary inbox. |
| `email_primary_folders` | List IMAP folders for the primary account. |
| `email_primary_read` | Read a primary-mailbox message by Message-ID, including body. |
| `email_ai_unread` | List unread messages in the AI inbox. |
| `email_ai_recent` | List recent messages in the AI inbox. |
| `email_ai_folders` | List IMAP folders for the AI account. |
| `email_ai_read` | Read an AI-mailbox message by Message-ID, including body. |
| `email_ai_send` | Send an email through the AI mailbox via Proton Bridge SMTP. |
| `email_ai_archive` | Move an AI-mailbox message to Archive. |
| `email_ai_delete` | Move an AI-mailbox message to Trash. |
| `email_unsubscribe_check` | Inspect List-Unsubscribe headers on a primary-mailbox message. |
| `email_unsubscribe_url` | Return the HTTP unsubscribe URL from a primary-mailbox message, when present. |

## Quick start

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Stdio transport (for MCP Inspector / Claude Code local)
python -m personal_assistant_mcp.server

# Streamable HTTP (production)
MCP_TRANSPORT=streamable-http \
    MCP_API_KEY=$(openssl rand -hex 32) \
    python -m personal_assistant_mcp.server
```

## Development

```bash
pytest tests/ -v
ruff check src/ tests/
ruff format --check src/ tests/
```

## Environment

| Var | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_HOST` | `0.0.0.0` | HTTP bind (streamable-http only) |
| `MCP_PORT` | `8080` | HTTP port (streamable-http only) |
| `MCP_API_KEY` | (none) | Bearer token; **required** for HTTP mode — server refuses to start without it |
| `MCP_RESOURCE_URL` | `http://localhost:$MCP_PORT` | Auth issuer/resource URL |
| `LOG_LEVEL` | `INFO` | Python logging level |

Subsequent phases add env vars for CouchDB (vault access), FreshRSS, CalDAV, and Proton IMAP/SMTP.

## License

MIT
