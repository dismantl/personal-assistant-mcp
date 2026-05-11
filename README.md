# personal-assistant-mcp

MCP server for personal-assistant tasks: Obsidian tasks/notes/digests, FreshRSS, CalDAV, Proton email.

Companion to [obsidian-livesync-mcp](https://github.com/dismantl/obsidian-livesync-mcp): this server imports `obsidian-livesync-mcp`'s vault client to handle Obsidian operations, and adds higher-level tools for daily-note management, task routing, weekly reviews, RSS/release digests, and Proton mail.

## Status

Phase 1 — scaffold. Only the `health` tool is implemented. The full ~28-tool surface is added phase by phase per the spec.

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
| `MCP_API_KEY` | (empty) | Bearer token; required for HTTP mode |
| `MCP_RESOURCE_URL` | `http://localhost:$MCP_PORT` | Auth issuer/resource URL |
| `LOG_LEVEL` | `INFO` | Python logging level |

Subsequent phases add env vars for CouchDB (vault access), FreshRSS, CalDAV, and Proton IMAP/SMTP.

## License

MIT
