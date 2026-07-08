"""Tests for task cache compute/read paths and cache-backed task tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from personal_assistant_mcp.tasks import cache, tools
from personal_assistant_mcp.tasks.cache import CACHE_PATH, CACHE_VERSION
from personal_assistant_mcp.tasks.crud import TaskRef
from personal_assistant_mcp.tasks.model import Task
from personal_assistant_mcp.tasks.planner import parse_spec
from tests.conftest import FakeVaultClient


class ToolRegistry:
    """Tiny stand-in for FastMCP that captures decorated task tools."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[func.__name__] = func
            return func

        return decorator


class CacheWriteFailureVault(FakeVaultClient):
    """Fake vault that can fail only cache writes after setup has completed."""

    fail_cache_writes: bool = False

    async def write_note(self, path: str, content: str) -> bool:
        if self.fail_cache_writes and path == CACHE_PATH:
            raise RuntimeError("cache write failed")
        return await super().write_note(path, content)


def _register_task_tools(fake_vault: FakeVaultClient) -> dict[str, Callable[..., Any]]:
    registry = ToolRegistry()
    tools.register(registry, lambda: fake_vault)
    return registry.tools


def _planner_spec_fm() -> dict[str, Any]:
    return {
        "type": "todo-planner-spec",
        "version": 1,
        "sourceSelection": {
            "include": {
                "roots": ["0 Logs"],
                "basenamesCaseInsensitive": ["todo"],
            },
            "exclude": {
                "pathsContaining": ["4 Archives"],
                "tags": ["#notasks"],
            },
        },
        "priorities": {
            "buckets": {
                "high": ["\U0001f53a", "⏫"],
                "medium": ["\U0001f53c", "⏬"],
                "low": ["\U0001f53d"],
            },
            "noPriorityMeansNoMarkerFromAnyBucket": True,
        },
        "tasks": {"includeStatuses": [" ", "/"]},
        "sections": [
            {
                "kind": "static",
                "id": "inbox",
                "title": "Inbox",
                "pageMatch": {"excludePathsContaining": ["1 Projects", "2 Areas"]},
                "taskMatch": {"priority": "none"},
            },
            {
                "kind": "static",
                "id": "high-priority",
                "title": "High Priority",
                "taskMatch": {"priority": {"bucket": "high"}},
            },
            {
                "kind": "folderChildren",
                "id": "projects",
                "parent": "1 Projects",
                "titleFrom": "folderName",
                "taskMatch": {"priority": "none"},
            },
        ],
    }


def _root_only_spec_fm() -> dict[str, Any]:
    spec = _planner_spec_fm()
    spec["sourceSelection"]["include"] = {
        "roots": ["1 Projects"],
        "basenamesCaseInsensitive": [],
    }
    spec["sections"] = [
        {
            "kind": "folderChildren",
            "id": "projects",
            "parent": "1 Projects",
            "titleFrom": "folderName",
            "taskMatch": {"priority": "none"},
        }
    ]
    return spec


def _all_tasks_spec_fm(root: str) -> dict[str, Any]:
    spec = _planner_spec_fm()
    spec["sourceSelection"]["include"] = {
        "roots": [root],
        "basenamesCaseInsensitive": [],
    }
    spec["sections"] = [
        {
            "kind": "static",
            "id": "all",
            "title": "All",
        }
    ]
    return spec


def _seed_default_spec(fake_vault: FakeVaultClient) -> None:
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()


async def _compute(fake_vault: FakeVaultClient) -> dict[str, Any]:
    _seed_default_spec(fake_vault)
    return await cache.compute_cache(fake_vault, spec_path="TODO.md")


async def test_compute_cache_writes_schema_and_round_trips_every_task_field(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/2026-06-27.md"] = (
        "- [ ] open \U0001f53a \U0001f6eb 2026-06-01 \u23f3 2026-06-02 "
        "\U0001f4c5 2026-06-03 \u2795 2026-05-01 \U0001f501 every Monday #work\n"
        "* [x] done item ✅ 2026-06-04\n"
        "+ [-] cancelled item ❌ 2026-06-05\n"
    )
    fake_vault.notes["4 Archives/old/todo.md"] = "- [ ] archived\n"
    fake_vault.notes["0 Logs/tagged.md"] = "- [ ] hidden #notasks\n"

    payload = await cache.compute_cache(fake_vault, spec_path="TODO.md")

    written = json.loads(fake_vault.notes[CACHE_PATH])
    assert written == payload
    assert payload["version"] == CACHE_VERSION
    assert payload["spec_path"] == "TODO.md"
    assert len(payload["spec_hash"]) == 40
    assert datetime.fromisoformat(payload["computed_at"]).tzinfo is not None
    assert [task["status"] for task in payload["tasks"]] == [" ", "x", "-"]
    assert {task["source_path"] for task in payload["tasks"]} == {"0 Logs/2026-06-27.md"}

    open_task = payload["tasks"][0]
    assert open_task["line_number"] == 0
    assert open_task["priority"] == "\U0001f53a"
    assert open_task["start"] == "2026-06-01"
    assert open_task["scheduled"] == "2026-06-02"
    assert open_task["due"] == "2026-06-03"
    assert open_task["created"] == "2026-05-01"
    assert open_task["recurrence"] == "every Monday"

    star_ref = cache.dict_to_ref(payload["tasks"][1])
    assert star_ref.file_path == "0 Logs/2026-06-27.md"
    assert star_ref.task.bullet == "*"
    assert star_ref.task.done == date(2026, 6, 4)
    assert star_ref.id == Task(
        body="done item", status="x", bullet="*", done=date(2026, 6, 4)
    ).content_hash("0 Logs/2026-06-27.md")


async def test_read_cache_returns_none_for_missing_corrupt_empty_or_unknown_version(
    fake_vault: FakeVaultClient,
) -> None:
    assert await cache.read_cache(fake_vault) is None

    fake_vault.notes[CACHE_PATH] = "{not json"
    assert await cache.read_cache(fake_vault) is None

    fake_vault.notes[CACHE_PATH] = "{}"
    assert await cache.read_cache(fake_vault) is None

    fake_vault.notes[CACHE_PATH] = json.dumps(
        {
            "version": CACHE_VERSION + 1,
            "computed_at": "2026-06-27T10:00:00-04:00",
            "spec_path": "TODO.md",
            "spec_hash": "a" * 40,
            "tasks": [],
        }
    )
    assert await cache.read_cache(fake_vault) is None


def test_spec_hash_is_canonical_for_frontmatter_key_order() -> None:
    first = parse_spec(_planner_spec_fm())
    reordered = _planner_spec_fm()
    reordered["sourceSelection"] = {
        "exclude": reordered["sourceSelection"]["exclude"],
        "include": reordered["sourceSelection"]["include"],
    }
    second = parse_spec(reordered)

    assert cache.spec_hash(first) == cache.spec_hash(second)


def test_filter_list_and_search_match_task_predicates() -> None:
    refs = [
        TaskRef("0 Logs/today.md", Task(body="Buy MILK", due=date(2026, 6, 10))),
        TaskRef("0 Logs/today.md", Task(body="done", status="x")),
        TaskRef("1 Projects/a/todo.md", Task(body="high", priority="⏫")),
        TaskRef("1 Projects/a/todo.md", Task(body="late", due=date(2026, 6, 11))),
        TaskRef("1 Projects/a/todo.md", Task(body="progress", status="/")),
    ]

    assert [ref.task.body for ref in cache.filter_list(refs)] == [
        "Buy MILK",
        "high",
        "late",
        "progress",
    ]
    assert [ref.task.body for ref in cache.filter_list(refs, folder="0 Logs")] == ["Buy MILK"]
    assert [ref.task.body for ref in cache.filter_list(refs, priority_bucket="high")] == ["high"]
    assert [ref.task.body for ref in cache.filter_list(refs, due_before=date(2026, 6, 11))] == [
        "Buy MILK"
    ]
    assert [ref.task.body for ref in cache.filter_list(refs, statuses=("x",))] == ["done"]

    assert [ref.task.body for ref in cache.filter_search(refs, "milk")] == ["Buy MILK"]
    assert cache.filter_search(refs, "   ") == []
    assert [ref.task.body for ref in cache.filter_search(refs, "i", folder="1 Projects")] == [
        "high",
    ]
    assert [ref.task.body for ref in cache.filter_search(refs, "done", statuses=("x",))] == [
        "done",
    ]


async def test_cached_refs_falls_back_to_scoped_live_scan(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/in-scope/todo.md"] = "- [ ] visible\n"
    fake_vault.notes["9 Elsewhere/note.md"] = "- [ ] hidden\n"

    refs, meta = await cache.cached_refs(fake_vault, spec_path="TODO.md")

    assert meta is None
    assert [(ref.file_path, ref.task.body) for ref in refs] == [
        ("1 Projects/in-scope/todo.md", "visible")
    ]

    fake_vault.notes[CACHE_PATH] = "{corrupt"
    refs, meta = await cache.cached_refs(fake_vault, spec_path="TODO.md")
    assert meta is None
    assert [ref.task.body for ref in refs] == ["visible"]


async def test_tasks_compute_tool_returns_summary_and_creates_cache(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] visible\n"

    result = await task_tools["tasks_compute"]()

    assert result["spec_path"] == "TODO.md"
    assert len(result["spec_hash"]) == 40
    assert result["task_count"] == 1
    assert result["computed_at"] == json.loads(fake_vault.notes[CACHE_PATH])["computed_at"]


async def test_tasks_list_and_search_serve_cache_with_freshness(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    await _compute(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] changed after compute\n"

    listed = await task_tools["tasks_list"]()
    searched = await task_tools["tasks_search"]("open")

    assert listed["source"] == "cache"
    assert listed["stale"] is False
    assert listed["computed_at"] is not None
    assert listed["spec_hash"] is not None
    assert [task["body"] for task in listed["tasks"]] == []

    fake_vault.notes["0 Logs/today.md"] = "- [ ] open from cache source\n"
    await task_tools["tasks_compute"]()
    fake_vault.notes["0 Logs/today.md"] = "- [ ] changed after compute\n"
    searched = await task_tools["tasks_search"]("OPEN")
    assert searched["source"] == "cache"
    assert [task["body"] for task in searched["tasks"]] == ["open from cache source"]


async def test_tasks_list_and_search_default_open_tasks_and_can_include_all(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    spec = _all_tasks_spec_fm("0 Logs")
    spec["tasks"]["includeStatuses"] = [" ", "/", "x", "-"]
    fake_vault.frontmatters["TODO.md"] = spec
    fake_vault.notes["0 Logs/today.md"] = (
        "- [ ] follow up open\n"
        "- [/] follow up active\n"
        "- [x] follow up done ✅ 2026-06-28\n"
        "- [-] follow up cancelled ❌ 2026-06-28\n"
    )
    await task_tools["tasks_compute"]()

    listed = await task_tools["tasks_list"]()
    searched = await task_tools["tasks_search"]("follow up")

    assert [task["body"] for task in listed["tasks"]] == [
        "follow up open",
        "follow up active",
    ]
    assert [task["body"] for task in searched["tasks"]] == [
        "follow up open",
        "follow up active",
    ]

    all_listed = await task_tools["tasks_list"](include_closed=True)
    all_searched = await task_tools["tasks_search"]("follow up", include_closed=True)
    assert [task["body"] for task in all_listed["tasks"]] == [
        "follow up open",
        "follow up active",
        "follow up done",
        "follow up cancelled",
    ]
    assert [task["body"] for task in all_searched["tasks"]] == [
        "follow up open",
        "follow up active",
        "follow up done",
        "follow up cancelled",
    ]

    closed_search = await task_tools["tasks_search"]("done", statuses="x")
    assert [task["body"] for task in closed_search["tasks"]] == ["follow up done"]


async def test_tasks_list_uses_scoped_live_fallback_when_cache_is_corrupt(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/in-scope/todo.md"] = "- [ ] visible\n"
    fake_vault.notes["9 Elsewhere/note.md"] = "- [ ] hidden\n"
    fake_vault.notes[CACHE_PATH] = "{corrupt"

    listed = await task_tools["tasks_list"]()

    assert listed["source"] == "live"
    assert listed["computed_at"] is None
    assert listed["spec_hash"] is None
    assert listed["stale"] is None
    assert [task["body"] for task in listed["tasks"]] == ["visible"]


async def test_spec_hash_drift_marks_cache_reads_stale(fake_vault: FakeVaultClient) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] old scoped task\n"
    await task_tools["tasks_compute"]()

    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    listed = await task_tools["tasks_list"]()

    assert listed["source"] == "cache"
    assert listed["stale"] is True
    assert [task["body"] for task in listed["tasks"]] == ["old scoped task"]


async def test_tasks_render_planner_uses_cache_and_adds_freshness(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] old planner task\n"
    await task_tools["tasks_compute"]()
    fake_vault.notes["0 Logs/today.md"] = "- [ ] changed after compute\n"

    rendered = await task_tools["tasks_render_planner"]()

    assert rendered["source"] == "cache"
    assert rendered["stale"] is False
    assert "old planner task" in rendered["markdown"]
    assert "changed after compute" not in rendered["markdown"]
    assert rendered["sections"][0]["tasks"][0]["body"] == "old planner task"


async def test_tasks_render_planner_writes_markdown_to_default_path(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] rendered planner task\n"

    rendered = await task_tools["tasks_render_planner"]()

    assert rendered["write_to"] == "TODO-rendered.md"
    assert fake_vault.notes["TODO-rendered.md"] == rendered["markdown"]
    assert "rendered planner task" in fake_vault.notes["TODO-rendered.md"]


async def test_tasks_render_planner_can_compute_cache_before_rendering(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] old cached task\n"
    await task_tools["tasks_compute"]()
    fake_vault.notes["0 Logs/today.md"] = "- [ ] freshly computed task\n"

    rendered = await task_tools["tasks_render_planner"](
        compute=True,
        write_to="Planning/TODO-rendered.md",
    )

    cached_payload = json.loads(fake_vault.notes[CACHE_PATH])
    assert rendered["source"] == "cache"
    assert rendered["stale"] is False
    assert rendered["write_to"] == "Planning/TODO-rendered.md"
    assert [task["body"] for task in cached_payload["tasks"]] == ["freshly computed task"]
    assert "freshly computed task" in rendered["markdown"]
    assert "old cached task" not in rendered["markdown"]
    assert fake_vault.notes["Planning/TODO-rendered.md"] == rendered["markdown"]


async def test_tasks_render_planner_rejects_write_to_spec_path(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] visible\n"

    with pytest.raises(ToolError, match="write_to.*spec_path"):
        await task_tools["tasks_render_planner"](write_to="TODO.md")

    assert "TODO.md" not in fake_vault.notes


@pytest.mark.parametrize(
    "write_to",
    [
        "0 Logs/TODO-rendered.md",
        "Planning/TODO.md",
    ],
)
async def test_tasks_render_planner_rejects_write_to_selected_source_path(
    fake_vault: FakeVaultClient,
    write_to: str,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] visible\n"

    with pytest.raises(ToolError, match="source selection"):
        await task_tools["tasks_render_planner"](write_to=write_to)

    assert write_to not in fake_vault.notes


async def test_tasks_render_planner_falls_back_live_when_cache_is_invalid(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    _seed_default_spec(fake_vault)
    fake_vault.notes["0 Logs/today.md"] = "- [ ] live planner task\n"
    fake_vault.notes[CACHE_PATH] = "{corrupt"

    rendered = await task_tools["tasks_render_planner"]()

    assert rendered["source"] == "live"
    assert rendered["stale"] is None
    assert "live planner task" in rendered["markdown"]


async def test_task_mutations_patch_existing_cache(fake_vault: FakeVaultClient) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] original\n"
    await task_tools["tasks_compute"]()

    added = await task_tools["tasks_add"]("new cached task", "1 Projects/a/todo.md")
    listed = await task_tools["tasks_list"]()
    assert [task["body"] for task in listed["tasks"]] == ["original", "new cached task"]

    completed_result = await task_tools["tasks_complete"](
        "1 Projects/a/todo.md", task_id=added["id"]
    )
    completed = await task_tools["tasks_list"](statuses="x")
    assert [task["body"] for task in completed["tasks"]] == ["new cached task"]

    reopened = await task_tools["tasks_uncomplete"](
        "1 Projects/a/todo.md", task_id=completed_result["id"]
    )
    open_tasks = await task_tools["tasks_list"]()
    assert [task["body"] for task in open_tasks["tasks"]] == ["original", "new cached task"]

    await task_tools["tasks_update"](
        "1 Projects/a/todo.md",
        task_id=reopened["id"],
        new_body="updated cached task",
    )
    updated = await task_tools["tasks_list"]()
    assert [task["body"] for task in updated["tasks"]] == ["original", "updated cached task"]

    await task_tools["tasks_delete"]("1 Projects/a/todo.md", body="original")
    listed = await task_tools["tasks_list"](statuses=" /x")
    assert [task["body"] for task in listed["tasks"]] == ["updated cached task"]


async def test_mutations_patch_cache_using_cached_spec_path(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _all_tasks_spec_fm("0 Logs")
    fake_vault.frontmatters["ALT.md"] = _all_tasks_spec_fm("1 Projects")
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] alt original\n"
    await task_tools["tasks_compute"](spec_path="ALT.md")

    await task_tools["tasks_add"]("default-only task", "0 Logs/default.md")
    cached_payload = json.loads(fake_vault.notes[CACHE_PATH])
    assert cached_payload["spec_path"] == "ALT.md"
    assert [(task["source_path"], task["body"]) for task in cached_payload["tasks"]] == [
        ("1 Projects/a/todo.md", "alt original")
    ]

    await task_tools["tasks_add"]("alt new task", "1 Projects/a/todo.md")
    rendered = await task_tools["tasks_render_planner"](spec_path="ALT.md")

    assert rendered["source"] == "cache"
    assert rendered["stale"] is False
    assert [task["body"] for section in rendered["sections"] for task in section["tasks"]] == [
        "alt original",
        "alt new task",
    ]


async def test_out_of_scope_mutation_removes_stale_cache_entries(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] in scope\n"
    fake_vault.notes["9 Elsewhere/todo.md"] = "- [ ] stale old\n"
    payload = await task_tools["tasks_compute"]()
    cache_payload = json.loads(fake_vault.notes[CACHE_PATH])
    cache_payload["tasks"].append(cache.task_to_dict(Task(body="stale old"), "9 Elsewhere/todo.md"))
    await cache.write_cache(fake_vault, cache_payload)

    await task_tools["tasks_add"]("still out", "9 Elsewhere/todo.md")
    listed = await task_tools["tasks_list"]()

    assert payload["task_count"] == 1
    assert [task["body"] for task in listed["tasks"]] == ["in scope"]


async def test_delete_last_task_leaves_no_stale_cache_entries(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] only task\n"
    await task_tools["tasks_compute"]()

    await task_tools["tasks_delete"]("1 Projects/a/todo.md", body="only task")
    listed = await task_tools["tasks_list"]()

    assert listed["source"] == "cache"
    assert listed["tasks"] == []


async def test_move_patches_source_and_destination_cache_entries(
    fake_vault: FakeVaultClient,
) -> None:
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()
    fake_vault.notes["1 Projects/a/todo.md"] = "- [ ] move me\n"
    fake_vault.notes["1 Projects/b/todo.md"] = ""
    await task_tools["tasks_compute"]()

    await task_tools["tasks_move"]("1 Projects/a/todo.md", "1 Projects/b/todo.md", body="move me")
    listed = await task_tools["tasks_list"]()

    assert [(task["file_path"], task["body"]) for task in listed["tasks"]] == [
        ("1 Projects/b/todo.md", "move me")
    ]


async def test_patch_noops_when_cache_absent_and_patch_failure_does_not_fail_mutation() -> None:
    fake_vault = CacheWriteFailureVault()
    task_tools = _register_task_tools(fake_vault)
    fake_vault.frontmatters["TODO.md"] = _root_only_spec_fm()

    await task_tools["tasks_add"]("no cache yet", "1 Projects/a/todo.md")
    assert CACHE_PATH not in fake_vault.notes

    await task_tools["tasks_compute"]()
    fake_vault.fail_cache_writes = True

    result = await task_tools["tasks_add"]("mutation still succeeds", "1 Projects/a/todo.md")

    assert result["body"] == "mutation still succeeds"
    assert "mutation still succeeds" in fake_vault.notes["1 Projects/a/todo.md"]
