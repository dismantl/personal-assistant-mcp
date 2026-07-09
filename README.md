# personal-assistant-mcp

MCP server for personal-assistant tasks: Obsidian tasks/notes/digests,
FreshRSS, and CalDAV.

Companion to [obsidian-livesync-mcp](https://github.com/dismantl/obsidian-livesync-mcp): this server imports `obsidian-livesync-mcp`'s vault client to handle Obsidian operations, and adds higher-level tools for daily-note management, task routing, weekly reviews, RSS/release digests, and calendars.

## Status

Alpha. The server includes MCP tools across tasks, daily/weekly notes, digests,
FreshRSS, CalDAV, and release-state tracking.
It can run over stdio for local MCP clients or authenticated streamable HTTP
for hosted use.

Generic email search, replies, folder moves, read/unread state, and attachment
workflows are out of scope for this server. Use a dedicated email MCP server
for mail operations.

## Exposed tools

### Core

| Tool | Purpose |
|---|---|
| `health` | Return server health and transport status. |

### Tasks

| Tool | Purpose |
|---|---|
| `tasks_compute` | Rebuild the task cache from the `TODO.md` planner source-selection spec. |
| `tasks_list` | List cached or live-fallback tasks from `TODO.md` sources, with optional folder, priority, status, and due-date filters. |
| `tasks_search` | Search cached or live-fallback open tasks from `TODO.md` sources by case-insensitive substring, optionally within a folder. |
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

### HTTP routes

| Route | Purpose |
|---|---|
| `POST /inbox` | Append a captured task to today's daily-note `## Inbox` section. Accepts `text/plain` or JSON `{"text": "..."}` with optional task metadata. Requires `Authorization: Bearer <MCP_API_KEY>`. |

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
| `calendar_events_range` | Fetch events within a timezone-aware ISO datetime range. |
| `calendar_create_event` | Create an event in a calendar slug from timezone-aware ISO start/end datetimes, with optional minutes-before reminders. |
| `calendar_import_ics` | Import a raw iCalendar object into a calendar slug while preserving scheduling properties. |
| `calendar_update_event` | Replace an event, or one recurring instance when `recurrence_id` is supplied, from timezone-aware ISO datetimes; reminders are preserved unless overridden. |
| `calendar_delete_event` | Delete an event, or one recurring instance when `recurrence_id` is supplied. |
| `calendar_rsvp` | Update attendee status on an existing invitation while preserving CalDAV scheduling context. |

`calendar_today`, `calendar_week`, and `calendar_events_range` include each
event's iCalendar `uid` and `calendar_slug`, plus `recurrence_id` for recurring
instances, so listed events can be passed directly to the update/delete tools.
Each event also includes a `reminders` field when it has alarms: a list of
whole minutes before start (e.g. `[15, 60]`), with any non-standard alarm
surfaced as its raw iCalendar trigger string.

Calendar mutation tools use the `slug` returned by `calendar_list` as
`calendar_slug`. `calendar_create_event` generates a UID when omitted and sends
`If-None-Match: *` to avoid overwriting existing events. When a UID is supplied,
`calendar_create_event` first checks whether that iCalendar UID already exists.
`calendar_update_event` performs a full resource replacement for the supplied
iCalendar UID, which does not need to match the CalDAV resource filename. To
mutate a single recurring instance instead of the whole series, pass both `uid`
and that listed instance's `recurrence_id`; updates write an iCalendar override
and deletes add an exception date.
`calendar_import_ics` upserts a raw iCalendar object by UID and preserves invite
scheduling fields such as `ORGANIZER`, `ATTENDEE`, and `VTIMEZONE`.
`calendar_create_event` and `calendar_update_event` accept a `reminders` list
of minutes before start, each written as a `DISPLAY` alarm (e.g. `[15, 60]` ->
popups 15 minutes and 1 hour before). On update, omitting `reminders` keeps the
event's existing alarms, `[]` clears them, and a list replaces them; note this
differs from `description`/`location`, which are dropped when omitted.

`calendar_rsvp` preserves the existing invitation resource and updates only the
selected attendee `PARTSTAT`, so CalDAV scheduling can send the organizer a real
invitation response.

## Quick start

```bash
uv sync

# Stdio transport (for MCP Inspector / Claude Code local)
uv run python -m personal_assistant_mcp.server

# Streamable HTTP (production)
MCP_TRANSPORT=streamable-http \
    MCP_API_KEY=$(openssl rand -hex 32) \
    uv run python -m personal_assistant_mcp.server
```

## Development

```bash
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
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

Subsequent phases add env vars for CouchDB (vault access), FreshRSS, CalDAV,
and related integrations.

## License

MIT
