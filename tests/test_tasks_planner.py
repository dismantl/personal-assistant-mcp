"""Unit tests for the TODO planner spec parser and renderer."""

from __future__ import annotations

import httpx
import pytest

from personal_assistant_mcp.tasks.planner import (
    PlannerOutput,
    PlannerSection,
    PlannerSpec,
    TransientPlannerRenderError,
    load_planner_spec,
    parse_spec,
    render_planner,
)
from tests.conftest import FakeVaultClient


def _planner_spec_fm() -> dict:
    """Representative TODO.md frontmatter for planner rendering tests."""
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
                "high": ["\U0001f53a", "⏫"],  # 🔺 ⏫
                "medium": ["\U0001f53c", "⏬"],  # 🔼 ⏬
                "low": ["\U0001f53d"],  # 🔽
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
                "id": "project-folders",
                "parent": "1 Projects",
                "titleFrom": "folderName",
                "taskMatch": {"priority": "none"},
            },
            {
                "kind": "folderChildren",
                "id": "area-folders",
                "parent": "2 Areas",
                "titleFrom": "folderName",
                "taskMatch": {"priority": "none"},
            },
            {
                "kind": "static",
                "id": "low-priority",
                "title": "Low Priority",
                "taskMatch": {"priority": {"bucket": "low"}},
            },
        ],
    }


class FlakyListNotesVault(FakeVaultClient):
    """Fake vault that can raise transient list errors before succeeding."""

    def __init__(self, *, failures_before_success: int | None) -> None:
        super().__init__()
        self.failures_before_success = failures_before_success
        self.list_notes_calls = 0

    async def list_notes(self, folder: str | None = None, limit: int = 50, skip: int = 0):
        self.list_notes_calls += 1
        if (
            self.failures_before_success is None
            or self.list_notes_calls <= self.failures_before_success
        ):
            raise httpx.ReadError("planner stream reset")
        return await super().list_notes(folder=folder, limit=limit, skip=skip)


class RecordingListNotesVault(FakeVaultClient):
    """Fake vault that records folder filters used by planner enumeration."""

    def __init__(self) -> None:
        super().__init__()
        self.list_note_folders: list[str | None] = []

    async def list_notes(self, folder: str | None = None, limit: int = 50, skip: int = 0):
        self.list_note_folders.append(folder)
        return await super().list_notes(folder=folder, limit=limit, skip=skip)


# -----------------------------------------------------------------------------
# parse_spec
# -----------------------------------------------------------------------------


def test_parse_spec_full() -> None:
    spec = parse_spec(_planner_spec_fm())
    assert isinstance(spec, PlannerSpec)
    assert spec.roots == ("0 Logs",)
    assert spec.basename_matches_ci == ("todo",)
    assert spec.exclude_paths_containing == ("4 Archives",)
    assert spec.exclude_tags == frozenset({"#notasks"})
    assert spec.include_statuses == (" ", "/")
    assert spec.priority_buckets["high"] == frozenset({"\U0001f53a", "⏫"})
    assert spec.priority_buckets["medium"] == frozenset({"\U0001f53c", "⏬"})
    assert spec.priority_buckets["low"] == frozenset({"\U0001f53d"})
    assert len(spec.sections) == 5
    assert spec.sections[0].kind == "static"
    assert spec.sections[0].title == "Inbox"
    assert spec.sections[2].kind == "folderChildren"
    assert spec.sections[2].parent == "1 Projects"


def test_parse_spec_rejects_wrong_type() -> None:
    with pytest.raises(ValueError, match="todo-planner-spec"):
        parse_spec({"type": "not-a-planner", "version": 1})


def test_parse_spec_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        parse_spec({"type": "todo-planner-spec", "version": 99})


def test_parse_spec_rejects_unknown_section_kind() -> None:
    with pytest.raises(ValueError, match="section kind"):
        parse_spec(
            {
                "type": "todo-planner-spec",
                "version": 1,
                "sections": [{"kind": "magic", "id": "x"}],
            }
        )


# -----------------------------------------------------------------------------
# load_planner_spec
# -----------------------------------------------------------------------------


async def test_load_planner_spec_reads_frontmatter(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    spec = await load_planner_spec(fake_vault)
    assert spec.roots == ("0 Logs",)


async def test_load_planner_spec_missing_raises(
    fake_vault: FakeVaultClient,
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await load_planner_spec(fake_vault, spec_path="missing.md")


# -----------------------------------------------------------------------------
# render_planner - end-to-end with representative planner spec
# -----------------------------------------------------------------------------


def _seed_vault_with_realistic_content(fake_vault: FakeVaultClient) -> None:
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()

    fake_vault.notes["0 Logs/2026-05-11.md"] = (
        "## Inbox\n"
        "- [ ] write the spec\n"  # inbox (no priority, in 0 Logs)
        "- [ ] urgent thing \U0001f53a\n"  # high (🔺) -> high-priority section
        "- [ ] less important \U0001f53d\n"  # low (🔽) -> low-priority section
        "- [x] done thing\n"  # excluded (status x)
    )
    fake_vault.notes["0 Logs/2026-05-10.md"] = "- [/] in progress task\n"

    fake_vault.notes["1 Projects/example-project/todo.md"] = (
        "- [ ] project task one\n- [ ] project task two\n"
    )
    fake_vault.notes["1 Projects/personal-assistant-mcp/todo.md"] = "- [ ] PA-MCP task A\n"
    fake_vault.notes["2 Areas/health/todo.md"] = "- [ ] go for run\n"

    # Excluded by 4 Archives folder
    fake_vault.notes["4 Archives/old.md"] = "- [ ] should not appear\n"

    # Excluded because tagged #notasks
    fake_vault.notes["0 Logs/random.md"] = "- [ ] hidden #notasks\n"


async def test_render_planner_full_pipeline(fake_vault: FakeVaultClient) -> None:
    _seed_vault_with_realistic_content(fake_vault)
    output = await render_planner(fake_vault)

    assert isinstance(output, PlannerOutput)
    titles = [s.title for s in output.sections]
    # Order: Inbox, High Priority, then per-project folders, per-area folders, Low Priority
    assert titles == [
        "Inbox",
        "High Priority",
        "example-project",
        "personal-assistant-mcp",
        "health",
        "Low Priority",
    ]

    by_title = {s.title: s for s in output.sections}
    assert {r.task.body for r in by_title["Inbox"].refs} == {
        "write the spec",
        "in progress task",
    }
    assert {r.task.body for r in by_title["High Priority"].refs} == {"urgent thing"}
    assert {r.task.body for r in by_title["Low Priority"].refs} == {"less important"}
    assert {r.task.body for r in by_title["example-project"].refs} == {
        "project task one",
        "project task two",
    }
    assert {r.task.body for r in by_title["personal-assistant-mcp"].refs} == {"PA-MCP task A"}
    assert {r.task.body for r in by_title["health"].refs} == {"go for run"}


async def test_render_planner_excludes_archives_and_notasks_tag(
    fake_vault: FakeVaultClient,
) -> None:
    _seed_vault_with_realistic_content(fake_vault)
    output = await render_planner(fake_vault)
    all_bodies = {r.task.body for s in output.sections for r in s.refs}
    assert "should not appear" not in all_bodies
    assert "hidden" not in all_bodies


async def test_render_planner_excludes_done_and_cancelled(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["0 Logs/today.md"] = (
        "- [ ] open\n- [x] done\n- [-] cancelled\n- [/] in_progress\n"
    )
    output = await render_planner(fake_vault)
    all_bodies = {r.task.body for s in output.sections for r in s.refs}
    assert all_bodies == {"open", "in_progress"}


async def test_render_planner_omits_empty_folder_children(
    fake_vault: FakeVaultClient,
) -> None:
    """folderChildren sections only show folders that have at least one task."""
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["1 Projects/has-tasks/todo.md"] = "- [ ] one\n"
    fake_vault.notes["1 Projects/empty/todo.md"] = ""
    output = await render_planner(fake_vault)
    by_title = {s.title: s for s in output.sections}
    assert by_title["has-tasks"].refs
    if "empty" in by_title:
        assert by_title["empty"].refs == ()


async def test_render_planner_skips_unreadable_note(
    fake_vault: FakeVaultClient,
) -> None:
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["0 Logs/2026-06-03.md"] = "- [ ] visible task\n"
    fake_vault.notes["0 Logs/2026-06-04.md"] = "- [ ] hidden task\n"
    fake_vault.read_errors["0 Logs/2026-06-04.md"] = ValueError(
        "Missing 1 chunk(s) for 0 Logs/2026-06-04.md after 4 attempt(s): ['h:bad']"
    )

    output = await render_planner(fake_vault)

    all_bodies = [r.task.body for s in output.sections for r in s.refs]
    assert all_bodies == ["visible task"]


async def test_render_planner_retries_transient_list_notes_failure() -> None:
    fake_vault = FlakyListNotesVault(failures_before_success=1)
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["0 Logs/2026-06-17.md"] = "- [ ] survives retry\n"

    output = await render_planner(fake_vault, retry_delay_seconds=0)

    all_bodies = [r.task.body for s in output.sections for r in s.refs]
    assert all_bodies == ["survives retry"]
    assert fake_vault.list_notes_calls == 2


async def test_render_planner_exhausted_transient_errors_preserve_existing_cache_hint() -> None:
    fake_vault = FlakyListNotesVault(failures_before_success=None)
    fake_vault.frontmatters["TODO.md"] = _planner_spec_fm()
    fake_vault.notes["0 Logs/2026-06-17.md"] = "- [ ] hidden by failures\n"

    with pytest.raises(TransientPlannerRenderError, match="existing planner cache"):
        await render_planner(fake_vault, retry_delay_seconds=0)

    assert fake_vault.list_notes_calls == 3


async def test_render_planner_uses_folder_filters_for_root_only_specs() -> None:
    fake_vault = RecordingListNotesVault()
    spec = _planner_spec_fm()
    spec["sourceSelection"]["include"] = {
        "roots": ["1 Projects", "2 Areas"],
        "basenamesCaseInsensitive": [],
    }
    fake_vault.frontmatters["TODO.md"] = spec
    fake_vault.notes["1 Projects/example/todo.md"] = "- [ ] project task\n"
    fake_vault.notes["2 Areas/health/todo.md"] = "- [ ] area task\n"
    fake_vault.notes["9 Elsewhere/todo.md"] = "- [ ] should not be scanned\n"

    output = await render_planner(fake_vault, retry_delay_seconds=0)

    all_bodies = {r.task.body for s in output.sections for r in s.refs}
    assert all_bodies == {"project task", "area task"}
    assert fake_vault.list_note_folders == ["1 Projects", "2 Areas"]


# -----------------------------------------------------------------------------
# PlannerOutput.to_markdown
# -----------------------------------------------------------------------------


def test_to_markdown_renders_canonical_form() -> None:
    from personal_assistant_mcp.tasks import Task
    from personal_assistant_mcp.tasks.crud import TaskRef

    output = PlannerOutput(
        sections=(
            PlannerSection(
                title="Inbox",
                refs=(
                    TaskRef("0 Logs/2026-05-11.md", Task(body="alpha")),
                    TaskRef("0 Logs/2026-05-11.md", Task(body="beta")),
                ),
            ),
            PlannerSection(title="Empty", refs=()),
            PlannerSection(
                title="Low Priority",
                refs=(
                    TaskRef(
                        "1 Projects/x/todo.md",
                        Task(body="not urgent", priority="\U0001f53d"),
                    ),
                ),
            ),
        )
    )
    md = output.to_markdown()
    assert md == (
        "## Inbox\n- [ ] alpha\n- [ ] beta\n\n## Low Priority\n- [ ] not urgent \U0001f53d\n"
    )


def test_to_markdown_empty_returns_empty_string() -> None:
    output = PlannerOutput(sections=(PlannerSection(title="x", refs=()),))
    assert output.to_markdown() == ""
