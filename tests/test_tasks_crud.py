"""Unit tests for vault-touching task CRUD operations."""

from __future__ import annotations

from datetime import date

import pytest

from personal_assistant_mcp.daily.note import DAILY_TEMPLATE_PATH
from personal_assistant_mcp.tasks import Task
from personal_assistant_mcp.tasks.crud import (
    MoveResult,
    MutationResult,
    TaskMoveConflict,
    TaskRef,
    add_task,
    complete_task,
    delete_task,
    list_tasks,
    move_task,
    read_tasks,
    search_tasks,
    uncomplete_task,
    update_task,
)
from tests.conftest import FakeVaultClient

_TODAY = date(2026, 5, 11)
_TEMPLATE_BODY = "## Priorities\n\n\n## Schedule\n\n\n## Inbox\n\n\n## Reflection\n\n\n## Log\n"


# -----------------------------------------------------------------------------
# read_tasks
# -----------------------------------------------------------------------------


async def test_read_tasks_returns_empty_for_missing_file(
    fake_vault: FakeVaultClient,
) -> None:
    tasks = await read_tasks(fake_vault, "0 Logs/missing.md")
    assert tasks == []


async def test_read_tasks_returns_empty_for_file_with_no_tasks(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "## Heading\n\njust prose\n"
    tasks = await read_tasks(fake_vault, "x.md")
    assert tasks == []


async def test_read_tasks_extracts_tasks_with_line_numbers(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = (
        "# Title\n"  # line 0
        "\n"  # line 1
        "- [ ] alpha\n"  # line 2
        "- [x] beta\n"  # line 3
        "\n"  # line 4
        "## Section\n"  # line 5
        "- [/] gamma\n"  # line 6
    )
    tasks = await read_tasks(fake_vault, "x.md")
    assert len(tasks) == 3
    assert tasks[0].body == "alpha"
    assert tasks[0].line_number == 2
    assert tasks[1].body == "beta"
    assert tasks[1].line_number == 3
    assert tasks[2].body == "gamma"
    assert tasks[2].line_number == 6


# -----------------------------------------------------------------------------
# list_tasks
# -----------------------------------------------------------------------------


async def test_list_tasks_returns_open_and_in_progress_by_default(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = (
        "- [ ] open\n- [/] in_progress\n- [x] done\n- [-] cancelled\n"
    )
    refs = await list_tasks(fake_vault)
    bodies = sorted(r.task.body for r in refs)
    assert bodies == ["in_progress", "open"]


async def test_list_tasks_filters_by_folder(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] daily-task\n"
    fake_vault.notes["1 Projects/x/todo.md"] = "- [ ] project-task\n"
    refs = await list_tasks(fake_vault, folder="0 Logs")
    assert [r.task.body for r in refs] == ["daily-task"]


async def test_list_tasks_filters_by_priority_bucket(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["a.md"] = "- [ ] no-priority\n- [ ] high \U0001f53a\n- [ ] low \U0001f53d\n"
    high = await list_tasks(fake_vault, priority_bucket="high")
    low = await list_tasks(fake_vault, priority_bucket="low")
    assert [r.task.body for r in high] == ["high"]
    assert [r.task.body for r in low] == ["low"]


async def test_list_tasks_filters_by_due_before(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["a.md"] = (
        "- [ ] tomorrow \U0001f4c5 2026-05-12\n"
        "- [ ] today \U0001f4c5 2026-05-11\n"
        "- [ ] next-week \U0001f4c5 2026-05-18\n"
        "- [ ] undated\n"
    )
    refs = await list_tasks(fake_vault, due_before=date(2026, 5, 12))
    assert sorted(r.task.body for r in refs) == ["today"]


async def test_list_tasks_skips_archives_and_attachments(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] live\n"
    fake_vault.notes["4 Archives/2026/note.md"] = "- [ ] archived\n"
    fake_vault.notes["0 Logs/attachments/note.md"] = "- [ ] attachment\n"
    refs = await list_tasks(fake_vault)
    assert [r.task.body for r in refs] == ["live"]


async def test_list_tasks_paginates_through_large_vault(
    fake_vault: FakeVaultClient,
) -> None:
    # Force pagination — _enumerate_notes uses page size 100
    for i in range(250):
        fake_vault.notes[f"1 Projects/p{i:03d}/todo.md"] = f"- [ ] task-{i}\n"
    refs = await list_tasks(fake_vault, folder="1 Projects")
    assert len(refs) == 250


async def test_list_tasks_skips_unreadable_note(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-06-03.md"] = "- [ ] visible task\n"
    fake_vault.notes["0 Logs/2026-06-04.md"] = "- [ ] hidden task\n"
    fake_vault.read_errors["0 Logs/2026-06-04.md"] = ValueError(
        "Missing 1 chunk(s) for 0 Logs/2026-06-04.md after 4 attempt(s): ['h:bad']"
    )

    refs = await list_tasks(fake_vault)

    assert [r.task.body for r in refs] == ["visible task"]


# -----------------------------------------------------------------------------
# search_tasks
# -----------------------------------------------------------------------------


async def test_search_finds_substring_case_insensitive(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["a.md"] = "- [ ] Buy MILK at store\n- [ ] eggs\n- [ ] write code\n"
    refs = await search_tasks(fake_vault, "milk")
    assert [r.task.body for r in refs] == ["Buy MILK at store"]


async def test_search_empty_query_returns_nothing(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["a.md"] = "- [ ] anything\n"
    assert await search_tasks(fake_vault, "") == []
    assert await search_tasks(fake_vault, "   ") == []


async def test_search_respects_folder_filter(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] read book\n"
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] read paper\n"
    refs = await search_tasks(fake_vault, "read", folder="0 Logs")
    assert [r.task.body for r in refs] == ["read book"]


# -----------------------------------------------------------------------------
# add_task
# -----------------------------------------------------------------------------


async def test_add_task_creates_file_when_missing(
    fake_vault: FakeVaultClient,
) -> None:
    ref = await add_task(fake_vault, "1 Projects/new/todo.md", "first task")
    assert isinstance(ref, TaskRef)
    assert ref.file_path == "1 Projects/new/todo.md"
    assert ref.task.body == "first task"
    assert fake_vault.notes["1 Projects/new/todo.md"] == "- [ ] first task\n"


async def test_add_task_appends_to_existing_file(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "## Inbox\n\n- [ ] alpha\n"
    await add_task(fake_vault, "x.md", "beta")
    assert fake_vault.notes["x.md"] == "## Inbox\n\n- [ ] alpha\n- [ ] beta\n"


async def test_add_task_with_priority_bucket(fake_vault: FakeVaultClient) -> None:
    await add_task(fake_vault, "x.md", "important", priority="high")
    assert "⏫" in fake_vault.notes["x.md"]  # ⏫


async def test_add_task_with_priority_emoji_passthrough(
    fake_vault: FakeVaultClient,
) -> None:
    await add_task(fake_vault, "x.md", "very", priority="\U0001f53a")  # 🔺
    assert "\U0001f53a" in fake_vault.notes["x.md"]


async def test_add_task_with_full_metadata(fake_vault: FakeVaultClient) -> None:
    ref = await add_task(
        fake_vault,
        "x.md",
        "spec work",
        priority="medium",
        due=date(2026, 5, 20),
        scheduled=date(2026, 5, 15),
        start=date(2026, 5, 12),
        recurrence="every Monday",
    )
    assert ref.task.priority_bucket == "medium"
    assert ref.task.due == date(2026, 5, 20)
    assert ref.task.scheduled == date(2026, 5, 15)
    assert ref.task.start == date(2026, 5, 12)
    assert ref.task.recurrence == "every Monday"


async def test_add_task_rejects_empty_text(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(ValueError, match="empty"):
        await add_task(fake_vault, "x.md", "")
    with pytest.raises(ValueError, match="empty"):
        await add_task(fake_vault, "x.md", "   ")


async def test_add_task_rejects_newlines(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(ValueError, match="newline"):
        await add_task(fake_vault, "x.md", "line1\nline2")


async def test_add_task_rejects_unknown_priority(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(ValueError, match="Unknown priority"):
        await add_task(fake_vault, "x.md", "task", priority="urgent")


async def test_add_task_to_daily_note_lands_in_inbox_and_creates_from_template(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY

    ref = await add_task(fake_vault, "0 Logs/2026-05-11.md", "captured thought")

    assert ref.file_path == "0 Logs/2026-05-11.md"
    assert ref.task.body == "captured thought"
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "## Inbox\n- [ ] captured thought\n\n\n## Reflection" in body
    assert body.endswith("## Log\n")


# -----------------------------------------------------------------------------
# complete_task
# -----------------------------------------------------------------------------


async def test_complete_task_by_body(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] take meds\n- [ ] something else\n"
    result = await complete_task(fake_vault, "x.md", body="take meds", today=_TODAY)
    assert isinstance(result, MutationResult)
    assert result.ref.task.is_complete
    assert result.ref.task.done == _TODAY
    assert fake_vault.notes["x.md"] == ("- [x] take meds ✅ 2026-05-11\n- [ ] something else\n")


async def test_complete_task_by_id(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] alpha\n"
    [ref] = await list_tasks(fake_vault)
    result = await complete_task(fake_vault, "x.md", task_id=ref.id, today=_TODAY)
    assert result.ref.task.is_complete


async def test_complete_task_preserves_existing_done_date(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [ ] task ✅ 2026-01-01\n"
    result = await complete_task(fake_vault, "x.md", body="task", today=_TODAY)
    assert result.ref.task.done == date(2026, 1, 1)


async def test_complete_task_raises_on_missing_file(
    fake_vault: FakeVaultClient,
) -> None:
    with pytest.raises(FileNotFoundError):
        await complete_task(fake_vault, "ghost.md", body="x")


async def test_complete_task_raises_on_unknown_body(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [ ] alpha\n"
    with pytest.raises(LookupError, match="No task matching"):
        await complete_task(fake_vault, "x.md", body="beta")


async def test_complete_task_surfaces_multiple_matches(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [ ] duplicate\n- [ ] duplicate\n"
    result = await complete_task(fake_vault, "x.md", body="duplicate", today=_TODAY)
    assert result.multiple_matches_in_file is True
    # First match was completed; second remains open
    assert fake_vault.notes["x.md"] == ("- [x] duplicate ✅ 2026-05-11\n- [ ] duplicate\n")


async def test_complete_task_requires_identity(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] alpha\n"
    with pytest.raises(ValueError, match="task_id or body"):
        await complete_task(fake_vault, "x.md")


# -----------------------------------------------------------------------------
# uncomplete_task
# -----------------------------------------------------------------------------


async def test_uncomplete_task_clears_status_and_done_date(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [x] task ✅ 2026-04-30\n"
    result = await uncomplete_task(fake_vault, "x.md", body="task")
    assert not result.ref.task.is_complete
    assert result.ref.task.done is None
    assert fake_vault.notes["x.md"] == "- [ ] task\n"


# -----------------------------------------------------------------------------
# delete_task
# -----------------------------------------------------------------------------


async def test_delete_task_removes_line(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] keep\n- [ ] go\n- [ ] keep2\n"
    result = await delete_task(fake_vault, "x.md", body="go")
    assert result.ref.task.body == "go"
    assert fake_vault.notes["x.md"] == "- [ ] keep\n- [ ] keep2\n"


async def test_delete_task_preserves_non_task_lines(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "## Inbox\n\n- [ ] alpha\n\n## Notes\nblah blah\n"
    await delete_task(fake_vault, "x.md", body="alpha")
    assert fake_vault.notes["x.md"] == "## Inbox\n\n\n## Notes\nblah blah\n"


async def test_delete_task_raises_on_missing(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] alpha\n"
    with pytest.raises(LookupError):
        await delete_task(fake_vault, "x.md", body="missing")


# -----------------------------------------------------------------------------
# update_task
# -----------------------------------------------------------------------------


async def test_update_task_changes_body_only(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] old body \U0001f53c \U0001f4c5 2026-05-15\n"
    result = await update_task(fake_vault, "x.md", body="old body", new_body="new body")
    assert result.ref.task.body == "new body"
    assert result.ref.task.priority_bucket == "medium"
    assert result.ref.task.due == date(2026, 5, 15)


async def test_update_task_changes_priority(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] task\n"
    result = await update_task(fake_vault, "x.md", body="task", new_priority="high")
    assert result.ref.task.priority_bucket == "high"


async def test_update_task_changes_due(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["x.md"] = "- [ ] task\n"
    result = await update_task(fake_vault, "x.md", body="task", new_due=date(2026, 6, 1))
    assert result.ref.task.due == date(2026, 6, 1)


async def test_update_task_leaves_unchanged_fields(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = (
        "- [ ] task \U0001f53c \U0001f6eb 2026-05-12 \U0001f4c5 2026-05-15 "
        "\U0001f501 every Monday\n"
    )
    result = await update_task(fake_vault, "x.md", body="task", new_priority="low")
    # priority changed
    assert result.ref.task.priority_bucket == "low"
    # rest preserved
    assert result.ref.task.start == date(2026, 5, 12)
    assert result.ref.task.due == date(2026, 5, 15)
    assert result.ref.task.recurrence == "every Monday"


async def test_update_task_rejects_empty_new_body(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [ ] task\n"
    with pytest.raises(ValueError, match="empty"):
        await update_task(fake_vault, "x.md", body="task", new_body="")
    with pytest.raises(ValueError, match="empty"):
        await update_task(fake_vault, "x.md", body="task", new_body="   ")


async def test_update_task_rejects_newlines_in_new_body(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["x.md"] = "- [ ] task\n"
    with pytest.raises(ValueError, match="newline"):
        await update_task(fake_vault, "x.md", body="task", new_body="a\nb")


# -----------------------------------------------------------------------------
# Identity round-trip: list -> id -> mutate by id -> verify
# -----------------------------------------------------------------------------


async def test_id_roundtrip_through_list_and_complete(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] alpha\n- [ ] beta \U0001f53c\n- [ ] gamma\n"
    refs = sorted(await list_tasks(fake_vault), key=lambda r: r.task.body)
    beta = next(r for r in refs if r.task.body == "beta")
    result = await complete_task(fake_vault, beta.file_path, task_id=beta.id, today=_TODAY)
    assert result.ref.task.body == "beta"
    assert result.ref.task.is_complete


# -----------------------------------------------------------------------------
# Direct TaskRef.id property
# -----------------------------------------------------------------------------


def test_taskref_id_is_content_hash() -> None:
    task = Task(body="hello")
    ref = TaskRef("0 Logs/today.md", task)
    assert ref.id == task.content_hash("0 Logs/today.md")


# -----------------------------------------------------------------------------
# move_task
# -----------------------------------------------------------------------------


async def test_move_task_happy_path(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] buy milk \U0001f53c\n"
    fake_vault.notes["1 Projects/grocery/todo.md"] = ""
    result = await move_task(
        fake_vault,
        "0 Logs/2026-05-11.md",
        "1 Projects/grocery/todo.md",
        body="buy milk",
    )
    assert isinstance(result, MoveResult)
    assert result.appended_to_dest is True
    assert result.removed_from_source is True
    assert result.multiple_matches_in_source is False
    assert result.ref.file_path == "1 Projects/grocery/todo.md"
    assert result.ref.task.body == "buy milk"
    assert result.ref.task.priority_bucket == "medium"
    # Source: line removed; dest: line appended (priority preserved)
    assert fake_vault.notes["0 Logs/2026-05-11.md"] == ""
    assert fake_vault.notes["1 Projects/grocery/todo.md"] == "- [ ] buy milk \U0001f53c\n"


async def test_move_task_creates_destination_file_if_missing(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] task\n"
    result = await move_task(
        fake_vault,
        "0 Logs/2026-05-11.md",
        "1 Projects/new/todo.md",
        body="task",
    )
    assert result.appended_to_dest is True
    assert fake_vault.notes["1 Projects/new/todo.md"] == "- [ ] task\n"


async def test_move_task_idempotent_when_already_in_dest(
    fake_vault: FakeVaultClient,
) -> None:
    """Same task body already in dest: no-op append, but still remove from source."""
    fake_vault.notes["src.md"] = "- [ ] alpha\n- [ ] beta\n"
    fake_vault.notes["dst.md"] = "- [ ] alpha\n"
    result = await move_task(fake_vault, "src.md", "dst.md", body="alpha")
    assert result.appended_to_dest is False
    assert result.removed_from_source is True
    assert fake_vault.notes["src.md"] == "- [ ] beta\n"
    assert fake_vault.notes["dst.md"] == "- [ ] alpha\n"


async def test_move_task_retry_safety(fake_vault: FakeVaultClient) -> None:
    """Simulates a crash between append-to-dest and remove-from-source.

    The first call's dest-write succeeded, source-write didn't happen. A retry
    sees the task already in dest, skips the append, and proceeds with the
    source removal. End-state: task in dest, not in source — never duplicated.
    """
    # Simulated post-crash state
    fake_vault.notes["src.md"] = "- [ ] alpha\n"
    fake_vault.notes["dst.md"] = "- [ ] alpha\n"  # append already happened pre-crash
    result = await move_task(fake_vault, "src.md", "dst.md", body="alpha")
    assert result.appended_to_dest is False  # dedup detected
    assert result.removed_from_source is True
    assert fake_vault.notes["src.md"] == ""
    assert fake_vault.notes["dst.md"] == "- [ ] alpha\n"


async def test_move_task_multiple_matches_in_source(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["src.md"] = "- [ ] dup\n- [ ] dup\n"
    fake_vault.notes["dst.md"] = ""
    result = await move_task(fake_vault, "src.md", "dst.md", body="dup")
    assert result.multiple_matches_in_source is True
    # First occurrence moved; second remains in source
    assert fake_vault.notes["src.md"] == "- [ ] dup\n"
    assert fake_vault.notes["dst.md"] == "- [ ] dup\n"


async def test_move_task_preserves_all_metadata(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["src.md"] = (
        "- [ ] write spec \U0001f53c \U0001f6eb 2026-05-12 \U0001f4c5 2026-05-15 "
        "\U0001f501 every Monday #work\n"
    )
    fake_vault.notes["dst.md"] = ""
    result = await move_task(fake_vault, "src.md", "dst.md", body="write spec #work")
    assert result.ref.task.priority_bucket == "medium"
    assert result.ref.task.start == date(2026, 5, 12)
    assert result.ref.task.due == date(2026, 5, 15)
    assert result.ref.task.recurrence == "every Monday"
    assert "#work" in fake_vault.notes["dst.md"]


async def test_move_task_raises_when_source_missing(fake_vault: FakeVaultClient) -> None:
    with pytest.raises(FileNotFoundError):
        await move_task(fake_vault, "ghost.md", "dst.md", body="x")


async def test_move_task_raises_when_task_not_in_source(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes["src.md"] = "- [ ] something else\n"
    fake_vault.notes["dst.md"] = ""
    with pytest.raises(LookupError):
        await move_task(fake_vault, "src.md", "dst.md", body="not there")


async def test_move_task_by_task_id(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["0 Logs/2026-05-11.md"] = "- [ ] alpha\n- [ ] beta \U0001f53c\n"
    fake_vault.notes["1 Projects/x/todo.md"] = ""
    refs = await list_tasks(fake_vault)
    beta = next(r for r in refs if r.task.body == "beta")
    result = await move_task(
        fake_vault,
        beta.file_path,
        "1 Projects/x/todo.md",
        task_id=beta.id,
    )
    assert result.ref.task.body == "beta"
    assert "beta" in fake_vault.notes["1 Projects/x/todo.md"]


async def test_move_task_detects_concurrent_edit_and_rolls_back(
    fake_vault: FakeVaultClient,
) -> None:
    """If source content changes between read and write, the move aborts and rollback runs."""
    fake_vault.notes["src.md"] = "- [ ] move me\n"
    fake_vault.notes["dst.md"] = ""

    # Patch read_note to mutate source on the second call (the re-read step)
    original_read = fake_vault.read_note
    call_count = {"n": 0}

    async def racing_read(path):
        if path == "src.md":
            call_count["n"] += 1
            if call_count["n"] == 2:
                # Simulate a concurrent edit between dest-append and source re-read
                fake_vault.notes["src.md"] = "- [ ] move me (edited)\n"
        return await original_read(path)

    fake_vault.read_note = racing_read  # type: ignore[method-assign]

    with pytest.raises(TaskMoveConflict) as excinfo:
        await move_task(fake_vault, "src.md", "dst.md", body="move me")

    assert excinfo.value.rollback_succeeded is True
    # Dest is rolled back to pre-move state (empty)
    assert fake_vault.notes["dst.md"] == ""
    # Source retains the (concurrently-edited) new content
    assert fake_vault.notes["src.md"] == "- [ ] move me (edited)\n"


async def test_move_task_no_rollback_when_dest_already_had_task(
    fake_vault: FakeVaultClient,
) -> None:
    """When append was a no-op (dedup), a concurrent-edit conflict has nothing to roll back."""
    fake_vault.notes["src.md"] = "- [ ] alpha\n"
    fake_vault.notes["dst.md"] = "- [ ] alpha\n"

    original_read = fake_vault.read_note
    call_count = {"n": 0}

    async def racing_read(path):
        if path == "src.md":
            call_count["n"] += 1
            if call_count["n"] == 2:
                fake_vault.notes["src.md"] = "- [ ] alpha edited\n"
        return await original_read(path)

    fake_vault.read_note = racing_read  # type: ignore[method-assign]

    with pytest.raises(TaskMoveConflict) as excinfo:
        await move_task(fake_vault, "src.md", "dst.md", body="alpha")

    assert excinfo.value.rollback_succeeded is True
    # Dest unchanged (no-op append meant nothing to roll back)
    assert fake_vault.notes["dst.md"] == "- [ ] alpha\n"


async def test_move_task_requires_identity(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes["src.md"] = "- [ ] alpha\n"
    with pytest.raises(ValueError, match="task_id or body"):
        await move_task(fake_vault, "src.md", "dst.md")


async def test_move_task_rejects_same_source_and_dest(fake_vault: FakeVaultClient) -> None:
    """Source==dest is a degenerate case; without a guard the task would be silently deleted."""
    fake_vault.notes["x.md"] = "- [ ] task\n"
    with pytest.raises(ValueError, match="must differ"):
        await move_task(fake_vault, "x.md", "x.md", body="task")
    assert fake_vault.notes["x.md"] == "- [ ] task\n"


async def test_move_task_rollback_deletes_dest_when_it_did_not_exist(
    fake_vault: FakeVaultClient,
) -> None:
    """Rollback after a concurrent edit must not leave a phantom empty dest file behind."""
    fake_vault.notes["src.md"] = "- [ ] move me\n"
    # Note: no "1 Projects/x/todo.md" entry — destination does not yet exist.

    original_read = fake_vault.read_note
    call_count = {"n": 0}

    async def racing_read(path):
        if path == "src.md":
            call_count["n"] += 1
            if call_count["n"] == 2:
                fake_vault.notes["src.md"] = "- [ ] move me (edited)\n"
        return await original_read(path)

    fake_vault.read_note = racing_read  # type: ignore[method-assign]

    with pytest.raises(TaskMoveConflict) as excinfo:
        await move_task(fake_vault, "src.md", "1 Projects/x/todo.md", body="move me")

    assert excinfo.value.rollback_succeeded is True
    # Destination must NOT exist — rollback deleted the file we created in step 3.
    assert "1 Projects/x/todo.md" not in fake_vault.notes


async def test_move_task_rollback_failure_surfaces_in_exception(
    fake_vault: FakeVaultClient,
) -> None:
    """If the rollback write itself fails, TaskMoveConflict.rollback_succeeded is False."""
    fake_vault.notes["src.md"] = "- [ ] move me\n"
    fake_vault.notes["dst.md"] = "- [ ] existing\n"

    original_read = fake_vault.read_note
    original_write = fake_vault.write_note
    read_calls = {"n": 0}
    write_calls = {"n": 0}

    async def racing_read(path):
        if path == "src.md":
            read_calls["n"] += 1
            if read_calls["n"] == 2:
                fake_vault.notes["src.md"] = "- [ ] move me (edited)\n"
        return await original_read(path)

    async def failing_write(path, content):
        write_calls["n"] += 1
        # First write = dest append (step 3) — let it succeed.
        # Second write = rollback restore — fail it.
        if write_calls["n"] == 1:
            return await original_write(path, content)
        raise RuntimeError("simulated rollback failure")

    fake_vault.read_note = racing_read  # type: ignore[method-assign]
    fake_vault.write_note = failing_write  # type: ignore[method-assign]

    with pytest.raises(TaskMoveConflict) as excinfo:
        await move_task(fake_vault, "src.md", "dst.md", body="move me")

    assert excinfo.value.rollback_succeeded is False


async def test_move_task_to_daily_note_lands_in_inbox(fake_vault: FakeVaultClient) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY
    fake_vault.notes["src.md"] = "- [ ] captured move\n"

    result = await move_task(fake_vault, "src.md", "0 Logs/2026-05-11.md", body="captured move")

    assert result.appended_to_dest is True
    assert result.removed_from_source is True
    assert fake_vault.notes["src.md"] == ""
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "## Inbox\n- [ ] captured move\n\n\n## Reflection" in body


async def test_move_task_daily_note_rollback_preserves_concurrent_append(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.notes[DAILY_TEMPLATE_PATH] = _TEMPLATE_BODY
    fake_vault.notes["src.md"] = "- [ ] move me\n"
    fake_vault.notes["0 Logs/2026-05-11.md"] = _TEMPLATE_BODY

    original_read = fake_vault.read_note
    source_reads = {"n": 0}

    async def racing_read(path):
        if path == "src.md":
            source_reads["n"] += 1
            if source_reads["n"] == 2:
                fake_vault.notes["0 Logs/2026-05-11.md"] = fake_vault.notes[
                    "0 Logs/2026-05-11.md"
                ].replace("## Log\n", "## Log\n- 10:31 — WARD: captured\n")
                fake_vault.notes["src.md"] = "- [ ] move me edited\n"
        return await original_read(path)

    fake_vault.read_note = racing_read  # type: ignore[method-assign]

    with pytest.raises(TaskMoveConflict) as excinfo:
        await move_task(fake_vault, "src.md", "0 Logs/2026-05-11.md", body="move me")

    assert excinfo.value.rollback_succeeded is True
    body = fake_vault.notes["0 Logs/2026-05-11.md"]
    assert "- [ ] move me" not in body
    assert "- 10:31 — WARD: captured" in body
