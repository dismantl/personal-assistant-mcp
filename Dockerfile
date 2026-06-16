FROM python:3.12-slim

# git: needed by uv to resolve the `obsidian-livesync-mcp @ git+https://...`
# dependency declared in pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.11.21 /uv /uvx /bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["python", "-m", "personal_assistant_mcp.server"]
