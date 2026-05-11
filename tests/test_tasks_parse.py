"""Unit tests for task parsing, rendering, round-tripping, and content-hash identity."""

from __future__ import annotations

from datetime import date

import pytest

from personal_assistant_mcp.tasks import Task, parse_task, render_task

# -----------------------------------------------------------------------------
# Non-task lines return None
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "",
        "# heading",
        "## another heading",
        "just plain text",
        "- not a task (no checkbox)",
        "-[ ] missing space after bullet",
        "- [] missing space inside checkbox",
        "  not a list item at all",
        "1. ordered list, not a task",
    ],
)
def test_parse_non_task_returns_none(line: str) -> None:
    assert parse_task(line) is None


# -----------------------------------------------------------------------------
# Status, bullet, and indent shapes
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected_status",
    [
        ("- [ ] open", " "),
        ("- [x] done", "x"),
        ("- [X] done uppercase", "X"),
        ("- [/] in progress", "/"),
        ("- [-] cancelled", "-"),
        ("- [>] forwarded", ">"),
    ],
)
def test_parse_status_variants(line: str, expected_status: str) -> None:
    task = parse_task(line)
    assert task is not None
    assert task.status == expected_status


@pytest.mark.parametrize("bullet", ["-", "*", "+"])
def test_parse_supports_all_bullet_markers(bullet: str) -> None:
    task = parse_task(f"{bullet} [ ] hello")
    assert task is not None
    assert task.bullet == bullet
    assert task.body == "hello"


@pytest.mark.parametrize("indent,raw", [(0, ""), (2, "  "), (4, "    ")])
def test_parse_captures_indent(indent: int, raw: str) -> None:
    task = parse_task(f"{raw}- [ ] sub-task")
    assert task is not None
    assert task.indent == indent
    assert task.body == "sub-task"


def test_parse_strips_trailing_newline() -> None:
    task = parse_task("- [ ] task\n")
    assert task is not None
    assert task.body == "task"


# -----------------------------------------------------------------------------
# Body extraction
# -----------------------------------------------------------------------------


def test_parse_empty_body() -> None:
    task = parse_task("- [ ] ")
    assert task is not None
    assert task.body == ""


def test_parse_body_only() -> None:
    task = parse_task("- [ ] Buy milk at the store")
    assert task is not None
    assert task.body == "Buy milk at the store"
    assert task.priority is None
    assert task.due is None
    assert task.tags == ()


# -----------------------------------------------------------------------------
# Priority emoji
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "emoji,bucket",
    [
        ("🔺", "high"),
        ("⏫", "high"),
        ("🔼", "medium"),
        ("⏬", "medium"),
        ("🔽", "low"),
    ],
)
def test_parse_priority_emoji(emoji: str, bucket: str) -> None:
    task = parse_task(f"- [ ] task {emoji}")
    assert task is not None
    assert task.priority == emoji
    assert task.priority_bucket == bucket
    assert task.body == "task"


def test_no_priority_has_no_bucket() -> None:
    task = parse_task("- [ ] task")
    assert task is not None
    assert task.priority is None
    assert task.priority_bucket is None


# -----------------------------------------------------------------------------
# Date emoji
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "emoji,field",
    [
        ("📅", "due"),
        ("⏳", "scheduled"),
        ("🛫", "start"),
        ("➕", "created"),
        ("✅", "done"),
        ("❌", "cancelled_date"),
    ],
)
def test_parse_date_emoji(emoji: str, field: str) -> None:
    task = parse_task(f"- [ ] task {emoji} 2026-05-15")
    assert task is not None
    assert getattr(task, field) == date(2026, 5, 15)
    assert task.body == "task"


def test_parse_multiple_dates() -> None:
    task = parse_task("- [ ] task 🛫 2026-01-01 ⏳ 2026-02-01 📅 2026-03-01")
    assert task is not None
    assert task.start == date(2026, 1, 1)
    assert task.scheduled == date(2026, 2, 1)
    assert task.due == date(2026, 3, 1)
    assert task.body == "task"


def test_completed_task_with_done_date() -> None:
    task = parse_task("- [x] finish report ✅ 2026-04-30")
    assert task is not None
    assert task.is_complete
    assert task.done == date(2026, 4, 30)


# -----------------------------------------------------------------------------
# Recurrence
# -----------------------------------------------------------------------------


def test_parse_simple_recurrence() -> None:
    task = parse_task("- [ ] take meds 🔁 every day")
    assert task is not None
    assert task.recurrence == "every day"
    assert task.body == "take meds"


def test_parse_complex_recurrence() -> None:
    task = parse_task("- [ ] standup 🔁 every weekday")
    assert task is not None
    assert task.recurrence == "every weekday"


def test_recurrence_with_dates_after() -> None:
    """Recurrence rule must terminate at the next metadata emoji."""
    task = parse_task("- [ ] task 🔁 every 2 weeks 📅 2026-06-01")
    assert task is not None
    assert task.recurrence == "every 2 weeks"
    assert task.due == date(2026, 6, 1)


def test_recurrence_with_priority_after() -> None:
    task = parse_task("- [ ] task 🔁 every Monday 🔼")
    assert task is not None
    assert task.recurrence == "every Monday"
    assert task.priority == "🔼"


# -----------------------------------------------------------------------------
# Tags (stay in body, surfaced via property)
# -----------------------------------------------------------------------------


def test_parse_single_tag() -> None:
    task = parse_task("- [ ] Buy #milk at the store")
    assert task is not None
    assert task.body == "Buy #milk at the store"
    assert task.tags == ("#milk",)


def test_parse_multiple_tags() -> None:
    task = parse_task("- [ ] errand #shopping #weekend")
    assert task is not None
    assert task.tags == ("#shopping", "#weekend")


def test_tags_are_deduplicated() -> None:
    task = parse_task("- [ ] task #foo #foo #bar")
    assert task is not None
    assert task.tags == ("#foo", "#bar")


def test_nested_tag_with_slash() -> None:
    task = parse_task("- [ ] research #project/auth-rewrite")
    assert task is not None
    assert task.tags == ("#project/auth-rewrite",)


# -----------------------------------------------------------------------------
# Combined / full task
# -----------------------------------------------------------------------------


def test_parse_full_task() -> None:
    task = parse_task(
        "- [ ] write the spec 🔼 🔁 every Monday 🛫 2026-01-01 "
        "⏳ 2026-02-01 📅 2026-03-01 ➕ 2025-12-01 #work"
    )
    assert task is not None
    assert task.body == "write the spec #work"
    assert task.status == " "
    assert task.priority == "🔼"
    assert task.recurrence == "every Monday"
    assert task.start == date(2026, 1, 1)
    assert task.scheduled == date(2026, 2, 1)
    assert task.due == date(2026, 3, 1)
    assert task.created == date(2025, 12, 1)
    assert task.tags == ("#work",)


def test_metadata_order_does_not_matter() -> None:
    """Same metadata in different source order parses to equivalent Task."""
    a = parse_task("- [ ] task 🔼 📅 2026-05-15")
    b = parse_task("- [ ] task 📅 2026-05-15 🔼")
    assert a is not None and b is not None
    assert a.priority == b.priority
    assert a.due == b.due
    assert a.body == b.body


# -----------------------------------------------------------------------------
# Render
# -----------------------------------------------------------------------------


def test_render_minimal_open_task() -> None:
    line = render_task(Task(body="hello"))
    assert line == "- [ ] hello"


def test_render_done_task() -> None:
    line = render_task(Task(body="done thing", status="x"))
    assert line == "- [x] done thing"


def test_render_preserves_indent_and_bullet() -> None:
    line = render_task(Task(body="sub", indent=2, bullet="*"))
    assert line == "  * [ ] sub"


def test_render_canonical_metadata_order() -> None:
    """Metadata emits in a fixed canonical order regardless of construction order."""
    task = Task(
        body="x",
        priority="🔼",
        due=date(2026, 3, 1),
        scheduled=date(2026, 2, 1),
        start=date(2026, 1, 1),
        recurrence="every week",
        created=date(2025, 12, 1),
    )
    line = render_task(task)
    assert line == (
        "- [ ] x 🔼 🔁 every week 🛫 2026-01-01 ⏳ 2026-02-01 📅 2026-03-01 ➕ 2025-12-01"
    )


def test_render_empty_body_with_metadata() -> None:
    line = render_task(Task(body="", priority="🔼"))
    assert line == "- [ ] 🔼"


def test_render_completely_empty() -> None:
    line = render_task(Task(body=""))
    assert line == "- [ ]"


# -----------------------------------------------------------------------------
# Round-trip: parse → render → parse → same Task
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "- [ ] simple task",
        "- [x] done",
        "- [/] in progress",
        "- [ ] task 🔼",
        "- [ ] task 📅 2026-05-15",
        "- [ ] task 🛫 2026-01-01 ⏳ 2026-02-01 📅 2026-03-01",
        "- [ ] take meds 🔁 every day",
        "- [ ] standup 🔁 every weekday 📅 2026-06-01",
        "- [ ] Buy #milk",
        "  - [ ] indented sub-task 🔽",
        "* [ ] star bullet",
        "+ [ ] plus bullet",
        "- [ ] complex 🔺 🔁 every 2 weeks 🛫 2026-01-01 📅 2026-03-01 #work",
    ],
)
def test_parse_render_roundtrip(line: str) -> None:
    """Parsing then rendering then re-parsing returns an equivalent Task."""
    first = parse_task(line)
    assert first is not None
    rendered = render_task(first)
    second = parse_task(rendered)
    assert second is not None
    # Compare with line_number reset so it doesn't interfere
    assert first == second


# -----------------------------------------------------------------------------
# Content-hash identity
# -----------------------------------------------------------------------------


def test_content_hash_is_stable_across_calls() -> None:
    task = parse_task("- [ ] hello 🔼")
    assert task is not None
    assert task.content_hash("0 Logs/today.md") == task.content_hash("0 Logs/today.md")


def test_content_hash_includes_file_path() -> None:
    task = parse_task("- [ ] hello")
    assert task is not None
    a = task.content_hash("0 Logs/today.md")
    b = task.content_hash("1 Projects/x/todo.md")
    assert a != b


def test_content_hash_changes_with_metadata() -> None:
    bare = parse_task("- [ ] hello")
    prioritized = parse_task("- [ ] hello 🔼")
    assert bare is not None and prioritized is not None
    assert bare.content_hash("f.md") != prioritized.content_hash("f.md")


def test_content_hash_invariant_under_metadata_order() -> None:
    """Two tasks parsed from differently-ordered source produce the same hash."""
    a = parse_task("- [ ] task 🔼 📅 2026-05-15")
    b = parse_task("- [ ] task 📅 2026-05-15 🔼")
    assert a is not None and b is not None
    assert a.content_hash("f.md") == b.content_hash("f.md")


def test_content_hash_length() -> None:
    task = parse_task("- [ ] hello")
    assert task is not None
    h = task.content_hash("f.md")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


# -----------------------------------------------------------------------------
# line_number passthrough
# -----------------------------------------------------------------------------


def test_line_number_passthrough() -> None:
    task = parse_task("- [ ] task", line_number=42)
    assert task is not None
    assert task.line_number == 42


def test_line_number_default_none() -> None:
    task = parse_task("- [ ] task")
    assert task is not None
    assert task.line_number is None
