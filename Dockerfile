FROM python:3.12-slim

# git: needed by pip to resolve the `obsidian-livesync-mcp @ git+https://...`
# dependency declared in pyproject.toml.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["python", "-m", "personal_assistant_mcp.server"]
