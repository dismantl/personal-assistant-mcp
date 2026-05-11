"""Unit tests for the wire-shape serializers in tasks/tools.py.

These functions sit between the crud layer (typed dataclasses) and the MCP
wire (JSON-compatible dicts). They're called once per tool invocation in
production, so a regression that drops a field or renames a key would slip
through if the only coverage was the underlying crud tests.
"""

from __future__ import annotations

from datetime import date

from personal_assistant_mcp.tasks import Task
from personal_assistant_mcp.tasks.crud import MoveResult, MutationResult, TaskRef
from personal_assistant_mcp.tasks.tools import (
    _serialize_move,
    _serialize_mutation,
    _serialize_task_ref,
)


def _ref() -> TaskRef:
    task = Task(
        body="Buy milk #shopping",
        status=" ",
        priority="\U0001f53c",  # 🔼
        due=date(2026, 5, 15),
        scheduled=date(2026, 5, 10),
        recurrence="every week",
    )
    return TaskRef("1 Projects/grocery/todo.md", task)


# -----------------------------------------------------------------------------
# _serialize_task_ref
# -----------------------------------------------------------------------------


def test_serialize_task_ref_returns_expected_keyset() -> None:
    """Pin the wire shape — adding/removing a key here is a breaking change."""
    result = _serialize_task_ref(_ref())
    assert set(result.keys()) == {
        "id",
        "file_path",
        "body",
        "status",
        "priority",
        "priority_bucket",
        "due",
        "scheduled",
        "start",
        "created",
        "done",
        "cancelled_date",
        "recurrence",
        "tags",
        "is_complete",
        "is_cancelled",
    }


def test_serialize_task_ref_drops_line_number() -> None:
    """line_number is intentionally not part of the wire shape (review finding)."""
    assert "line_number" not in _serialize_task_ref(_ref())


def test_serialize_task_ref_values() -> None:
    ref = _ref()
    result = _serialize_task_ref(ref)
    assert result["id"] == ref.id
    assert result["file_path"] == "1 Projects/grocery/todo.md"
    assert result["body"] == "Buy milk #shopping"
    assert result["priority"] == "\U0001f53c"
    assert result["priority_bucket"] == "medium"
    assert result["due"] == "2026-05-15"
    assert result["scheduled"] == "2026-05-10"
    assert result["recurrence"] == "every week"
    assert result["tags"] == ["#shopping"]
    assert result["is_complete"] is False


def test_serialize_task_ref_handles_none_dates() -> None:
    ref = TaskRef("x.md", Task(body="bare"))
    result = _serialize_task_ref(ref)
    for key in ("due", "scheduled", "start", "created", "done", "cancelled_date"):
        assert result[key] is None


def test_serialize_task_ref_done_task() -> None:
    ref = TaskRef("x.md", Task(body="done", status="x", done=date(2026, 4, 30)))
    result = _serialize_task_ref(ref)
    assert result["is_complete"] is True
    assert result["done"] == "2026-04-30"


# -----------------------------------------------------------------------------
# _serialize_mutation
# -----------------------------------------------------------------------------


def test_serialize_mutation_adds_multiple_matches_flag() -> None:
    result = _serialize_mutation(MutationResult(ref=_ref(), multiple_matches_in_file=True))
    assert result["multiple_matches_in_file"] is True
    # Everything else still comes from _serialize_task_ref
    assert result["body"] == "Buy milk #shopping"


def test_serialize_mutation_default_flag_is_false() -> None:
    result = _serialize_mutation(MutationResult(ref=_ref()))
    assert result["multiple_matches_in_file"] is False


# -----------------------------------------------------------------------------
# _serialize_move
# -----------------------------------------------------------------------------


def test_serialize_move_includes_move_specific_fields() -> None:
    result = _serialize_move(
        MoveResult(
            ref=_ref(),
            source_path="0 Logs/2026-05-11.md",
            dest_path="1 Projects/grocery/todo.md",
            appended_to_dest=True,
            removed_from_source=True,
            multiple_matches_in_source=False,
        )
    )
    assert result["source_path"] == "0 Logs/2026-05-11.md"
    assert result["dest_path"] == "1 Projects/grocery/todo.md"
    assert result["appended_to_dest"] is True
    assert result["removed_from_source"] is True
    assert result["multiple_matches_in_source"] is False
    # Plus the full TaskRef wire shape
    assert result["body"] == "Buy milk #shopping"
    assert result["id"]


def test_serialize_move_idempotent_no_op_case() -> None:
    """When dest already had the task, appended_to_dest=False; rest still serializes."""
    result = _serialize_move(
        MoveResult(
            ref=_ref(),
            source_path="src.md",
            dest_path="dst.md",
            appended_to_dest=False,
            removed_from_source=True,
            multiple_matches_in_source=True,
        )
    )
    assert result["appended_to_dest"] is False
    assert result["multiple_matches_in_source"] is True
