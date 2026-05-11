"""Task dataclass and content-hash identity for cross-tool addressing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date

_BUCKETS: dict[str, frozenset[str]] = {
    "high": frozenset({"\U0001f53a", "⏫"}),  # 🔺 ⏫
    "medium": frozenset({"\U0001f53c", "⏬"}),  # 🔼 ⏬
    "low": frozenset({"\U0001f53d"}),  # 🔽
}

_TAG_RE = re.compile(r"#[\w/-]+")


@dataclass(frozen=True)
class Task:
    """A parsed Obsidian Tasks-plugin task line."""

    body: str
    status: str = " "
    indent: int = 0
    bullet: str = "-"

    priority: str | None = None
    due: date | None = None
    scheduled: date | None = None
    start: date | None = None
    created: date | None = None
    done: date | None = None
    cancelled_date: date | None = None
    recurrence: str | None = None

    line_number: int | None = None

    @property
    def priority_bucket(self) -> str | None:
        if self.priority is None:
            return None
        for name, markers in _BUCKETS.items():
            if self.priority in markers:
                return name
        return None

    @property
    def is_complete(self) -> bool:
        return self.status.lower() == "x"

    @property
    def is_cancelled(self) -> bool:
        return self.status == "-"

    @property
    def tags(self) -> tuple[str, ...]:
        """All ``#tag`` tokens found in the body, in order, deduplicated."""
        seen: set[str] = set()
        out: list[str] = []
        for m in _TAG_RE.finditer(self.body):
            tag = m.group(0)
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
        return tuple(out)

    def content_hash(self, file_path: str) -> str:
        """Stable content-hash identity used by mutation tools.

        Hashes ``file_path + ":" + render(task)``. Two tasks that render
        identically in the same file produce the same hash — by design,
        they are operationally indistinguishable.
        """
        from .render import render_task

        payload = f"{file_path}:{render_task(self).strip()}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
