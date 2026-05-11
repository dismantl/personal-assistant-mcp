"""Unit tests for vault-touching task CRUD operations."""

from __future__ import annotations

from datetime import date

import pytest

from personal_assistant_mcp.tasks import Task
from personal_assistant_mcp.tasks.crud import (
    MutationResult,
    TaskRef,
    add_task,
    complete_task,
    delete_task,
    list_tasks,
    read_tasks,
    search_tasks,
    uncomplete_task,
    update_task,
)
from tests.conftest import FakeVaultClient

_TODAY = date(2026, 5, 11)


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
