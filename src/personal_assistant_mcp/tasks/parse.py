"""Parser for Obsidian Tasks-plugin markdown task lines.

Task line shape::

    {indent}{bullet} [{status}] {body}? {metadata...}

Metadata emoji recognized:

- Priority: 🔺 (highest), ⏫ (high), 🔼 (medium-up), 🔽 (low), ⏬ (lowest)
- Dates: 📅 due, ⏳ scheduled, 🛫 start, ➕ created, ✅ done, ❌ cancelled
- Recurrence: 🔁 followed by a free-form rule (e.g. ``every 2 weeks on Monday``)

Tags (``#word``) are not extracted out of the body — they remain inline and
are surfaced via the ``Task.tags`` property.
"""

from __future__ import annotations

import re
from datetime import date

from .model import Task

PRIORITY_EMOJI_STR = "\U0001f53a⏫\U0001f53c\U0001f53d⏬"  # 🔺⏫🔼🔽⏬
PRIORITY_EMOJI: frozenset[str] = frozenset(PRIORITY_EMOJI_STR)

DATE_EMOJI_STR = "\U0001f4c5⏳\U0001f6eb➕✅❌"  # 📅⏳🛫➕✅❌
DATE_EMOJI_FIELDS: dict[str, str] = {
    "\U0001f4c5": "due",
    "⏳": "scheduled",
    "\U0001f6eb": "start",
    "➕": "created",
    "✅": "done",
    "❌": "cancelled_date",
}

RECURRENCE_EMOJI = "\U0001f501"  # 🔁

_ALL_META_EMOJI = PRIORITY_EMOJI_STR + DATE_EMOJI_STR + RECURRENCE_EMOJI

_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+]) \[(?P<status>.)\] (?P<body>.*)$")
_PRIORITY_RE = re.compile(f"[{PRIORITY_EMOJI_STR}]")
_DATE_RE = re.compile(rf"(?P<emoji>[{DATE_EMOJI_STR}])\s*(?P<date>\d{{4}}-\d{{2}}-\d{{2}})")
_RECUR_RE = re.compile(
    rf"{RECURRENCE_EMOJI}\s*(?P<recur>[^{_ALL_META_EMOJI}]+?)(?=\s*[{_ALL_META_EMOJI}]|\s*$)"
)


def parse_task(line: str, line_number: int | None = None) -> Task | None:
    """Parse a single line. Returns ``None`` if the line is not a task."""
    line = line.rstrip("\r\n")

    m = _LINE_RE.match(line)
    if not m:
        return None

    indent = len(m["indent"])
    bullet = m["bullet"]
    status = m["status"]
    rest = m["body"]

    priority: str | None = None
    due: date | None = None
    scheduled: date | None = None
    start: date | None = None
    created: date | None = None
    done: date | None = None
    cancelled_date: date | None = None
    recurrence: str | None = None
    spans: list[tuple[int, int]] = []

    pri = _PRIORITY_RE.search(rest)
    if pri:
        priority = pri.group()
        spans.append((pri.start(), pri.end()))

    for dm in _DATE_RE.finditer(rest):
        parsed = date.fromisoformat(dm["date"])
        field = DATE_EMOJI_FIELDS[dm["emoji"]]
        if field == "due":
            due = parsed
        elif field == "scheduled":
            scheduled = parsed
        elif field == "start":
            start = parsed
        elif field == "created":
            created = parsed
        elif field == "done":
            done = parsed
        elif field == "cancelled_date":
            cancelled_date = parsed
        spans.append((dm.start(), dm.end()))

    for rm in _RECUR_RE.finditer(rest):
        recurrence = rm["recur"].strip()
        spans.append((rm.start(), rm.end()))

    body = " ".join(_strip_spans(rest, spans).split())

    return Task(
        body=body,
        status=status,
        indent=indent,
        bullet=bullet,
        priority=priority,
        due=due,
        scheduled=scheduled,
        start=start,
        created=created,
        done=done,
        cancelled_date=cancelled_date,
        recurrence=recurrence,
        line_number=line_number,
    )


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    pos = 0
    for start_idx, end_idx in sorted(spans):
        parts.append(text[pos:start_idx])
        pos = end_idx
    parts.append(text[pos:])
    return "".join(parts)
