"""Render a ``Task`` back into a markdown line.

Metadata is emitted in canonical order regardless of the order it appeared
in the source, so two tasks with the same parsed structure produce the same
rendered line. That property is what makes content-hash identity stable.
"""

from __future__ import annotations

from .model import Task


def render_task(task: Task) -> str:
    """Return a single-line markdown rendering of ``task``."""
    indent = " " * task.indent
    parts: list[str] = []

    if task.body:
        _require_single_line("body", task.body)
        parts.append(task.body)

    if task.priority:
        _require_single_line("priority", task.priority)
        parts.append(task.priority)
    if task.recurrence:
        _require_single_line("recurrence", task.recurrence)
        parts.append(f"\U0001f501 {task.recurrence}")  # 🔁
    if task.start:
        parts.append(f"\U0001f6eb {task.start.isoformat()}")  # 🛫
    if task.scheduled:
        parts.append(f"⏳ {task.scheduled.isoformat()}")
    if task.due:
        parts.append(f"\U0001f4c5 {task.due.isoformat()}")  # 📅
    if task.created:
        parts.append(f"➕ {task.created.isoformat()}")
    if task.done:
        parts.append(f"✅ {task.done.isoformat()}")
    if task.cancelled_date:
        parts.append(f"❌ {task.cancelled_date.isoformat()}")

    line = f"{indent}{task.bullet} [{task.status}]"
    middle = " ".join(parts)
    if middle:
        line += f" {middle}"
    return line


def _require_single_line(field_name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must not contain newlines")
