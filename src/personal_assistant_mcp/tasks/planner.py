"""Render the TODO planner view from a frontmatter-defined spec.

Ports the behaviour of ``obsidian_todo_render.js`` / the ``dataviewjs`` block
in Holden's ``TODO.md``. The spec lives in the frontmatter of a vault note
(default: ``TODO.md`` at the vault root). The renderer walks notes matching
the ``sourceSelection`` rules, parses tasks, and groups them across the
configured ``sections``.

Spec schema (verbatim from Holden's TODO.md frontmatter)::

    type: todo-planner-spec
    version: 1
    sourceSelection:
      include:
        roots: [ ... ]
        basenamesCaseInsensitive: [ ... ]   # e.g. ["todo"] matches todo.md / TODO.md
      exclude:
        pathsContaining: [ ... ]
        tags: [ ... ]                       # e.g. ["#notasks"]
    priorities:
      buckets:
        high:   [ "🔺", "⏫" ]
        medium: [ "🔼", "⏬" ]
        low:    [ "🔽" ]
      noPriorityMeansNoMarkerFromAnyBucket: true
    tasks:
      includeStatuses: [ " ", "/" ]
    sections:
      - kind: static
        id: ...
        title: ...
        pageMatch:
          excludePathsContaining: [ ... ]
        taskMatch:
          priority: none | { bucket: <name> }
      - kind: folderChildren
        id: ...
        parent: "1 Projects"
        titleFrom: folderName
        taskMatch:
          priority: ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from ..vault import iter_all_notes
from .crud import TaskRef, read_tasks
from .model import Task
from .render import render_task

DEFAULT_SPEC_PATH = "TODO.md"

_INLINE_TAG_RE = re.compile(r"(?<!\w)(#[\w/-]+)(?!\w)")


# -----------------------------------------------------------------------------
# Spec dataclasses
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _PageMatch:
    exclude_paths_containing: tuple[str, ...] = ()


@dataclass(frozen=True)
class _TaskMatch:
    # priority_kind: None (no filter) | "none" (no priority emoji) | "bucket"
    priority_kind: str | None = None
    priority_bucket: str | None = None


@dataclass(frozen=True)
class _SectionConfig:
    kind: str  # "static" | "folderChildren"
    section_id: str
    title: str | None = None
    parent: str | None = None
    title_from: str | None = None
    page_match: _PageMatch = field(default_factory=_PageMatch)
    task_match: _TaskMatch = field(default_factory=_TaskMatch)


@dataclass(frozen=True)
class PlannerSpec:
    """Parsed and validated planner configuration."""

    roots: tuple[str, ...]
    basename_matches_ci: tuple[str, ...]
    exclude_paths_containing: tuple[str, ...]
    exclude_tags: frozenset[str]
    priority_buckets: dict[str, frozenset[str]]
    include_statuses: tuple[str, ...]
    sections: tuple[_SectionConfig, ...]


# -----------------------------------------------------------------------------
# Output dataclasses
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerSection:
    """One rendered section: title + ordered list of task references."""

    title: str
    refs: tuple[TaskRef, ...]


@dataclass(frozen=True)
class PlannerOutput:
    sections: tuple[PlannerSection, ...]

    def to_markdown(self) -> str:
        """Render as a Markdown document. Empty sections are omitted."""
        out_lines: list[str] = []
        for section in self.sections:
            if not section.refs:
                continue
            if out_lines:
                out_lines.append("")
            out_lines.append(f"## {section.title}")
            for ref in section.refs:
                out_lines.append(render_task(ref.task))
        if not out_lines:
            return ""
        return "\n".join(out_lines) + "\n"


# -----------------------------------------------------------------------------
# Spec parsing
# -----------------------------------------------------------------------------


def parse_spec(fm: dict[str, Any]) -> PlannerSpec:
    """Validate and parse a raw frontmatter dict into a ``PlannerSpec``."""
    expected_type = "todo-planner-spec"
    if fm.get("type") != expected_type:
        raise ValueError(f"Expected frontmatter type={expected_type!r}, got {fm.get('type')!r}")
    if fm.get("version") != 1:
        raise ValueError(f"Unsupported planner spec version: {fm.get('version')!r}")

    src = fm.get("sourceSelection") or {}
    include = src.get("include") or {}
    exclude = src.get("exclude") or {}

    priorities = fm.get("priorities") or {}
    buckets_raw = priorities.get("buckets") or {}
    priority_buckets = {
        str(name): frozenset(str(e) for e in (emojis or [])) for name, emojis in buckets_raw.items()
    }

    tasks_cfg = fm.get("tasks") or {}
    include_statuses = tuple(tasks_cfg.get("includeStatuses") or (" ", "/"))

    sections_raw = fm.get("sections") or []
    sections = tuple(_parse_section(s) for s in sections_raw)

    return PlannerSpec(
        roots=tuple(str(r) for r in (include.get("roots") or ())),
        basename_matches_ci=tuple(
            str(b).lower() for b in (include.get("basenamesCaseInsensitive") or ())
        ),
        exclude_paths_containing=tuple(str(p) for p in (exclude.get("pathsContaining") or ())),
        exclude_tags=frozenset(str(t) for t in (exclude.get("tags") or ())),
        priority_buckets=priority_buckets,
        include_statuses=include_statuses,
        sections=sections,
    )


def _parse_section(raw: dict[str, Any]) -> _SectionConfig:
    kind = raw.get("kind")
    if kind not in ("static", "folderChildren"):
        raise ValueError(f"Unsupported section kind: {kind!r}")

    page_match_raw = raw.get("pageMatch") or {}
    page_match = _PageMatch(
        exclude_paths_containing=tuple(
            str(p) for p in (page_match_raw.get("excludePathsContaining") or ())
        ),
    )

    task_match_raw = raw.get("taskMatch") or {}
    priority_kind: str | None = None
    priority_bucket: str | None = None
    priority_match = task_match_raw.get("priority")
    if priority_match == "none":
        priority_kind = "none"
    elif isinstance(priority_match, dict) and "bucket" in priority_match:
        priority_kind = "bucket"
        priority_bucket = str(priority_match["bucket"])

    return _SectionConfig(
        kind=str(kind),
        section_id=str(raw.get("id", "")),
        title=str(raw["title"]) if "title" in raw else None,
        parent=str(raw["parent"]) if "parent" in raw else None,
        title_from=str(raw["titleFrom"]) if "titleFrom" in raw else None,
        page_match=page_match,
        task_match=_TaskMatch(
            priority_kind=priority_kind,
            priority_bucket=priority_bucket,
        ),
    )


# -----------------------------------------------------------------------------
# Source enumeration and task collection
# -----------------------------------------------------------------------------


async def _enumerate_source_notes(vault: ObsidianVaultClient, spec: PlannerSpec) -> list[str]:
    """Return vault-relative paths of notes matching the spec's source rules."""
    metas = await _all_notes(vault)
    out: list[str] = []
    for meta in metas:
        path = meta.path
        if not _matches_source(path, spec):
            continue
        if any(frag in path for frag in spec.exclude_paths_containing):
            continue
        out.append(path)
    return out


def _matches_source(path: str, spec: PlannerSpec) -> bool:
    # Match by root prefix
    for root in spec.roots:
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return True
    # Match by case-insensitive basename (without extension)
    basename = path.rsplit("/", 1)[-1]
    name, _, _ = basename.rpartition(".")
    name = name or basename  # if no extension, use whole basename
    if name.lower() in spec.basename_matches_ci:
        return True
    return False


async def _all_notes(vault: ObsidianVaultClient) -> list[Any]:
    return await iter_all_notes(vault, folder=None)


async def _is_tag_excluded(vault: ObsidianVaultClient, path: str, excluded: frozenset[str]) -> bool:
    if not excluded:
        return False
    note = await vault.read_note(path)
    if note is None:
        return False
    found_tags = set(_INLINE_TAG_RE.findall(note.content))
    return bool(found_tags & excluded)


# -----------------------------------------------------------------------------
# Task filtering and section assembly
# -----------------------------------------------------------------------------


def _task_passes_filter(task: Task, spec: PlannerSpec, match: _TaskMatch) -> bool:
    if task.status not in spec.include_statuses:
        return False
    if match.priority_kind == "none":
        return _no_priority_marker(task, spec)
    if match.priority_kind == "bucket":
        markers = spec.priority_buckets.get(match.priority_bucket or "", frozenset())
        return task.priority in markers
    # No priority filter
    return True


def _no_priority_marker(task: Task, spec: PlannerSpec) -> bool:
    if task.priority is None:
        return True
    all_markers: frozenset[str] = frozenset()
    for emojis in spec.priority_buckets.values():
        all_markers = all_markers | emojis
    return task.priority not in all_markers


def _page_allowed(path: str, match: _PageMatch) -> bool:
    return not any(frag in path for frag in match.exclude_paths_containing)


def _folder_children(notes: list[str], parent: str) -> list[tuple[str, str]]:
    """Return ``(folder_name, folder_path)`` pairs immediately under ``parent``."""
    prefix = parent.rstrip("/") + "/"
    seen: dict[str, str] = {}
    for path in notes:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix) :]
        if "/" not in rest:
            continue  # file directly under parent, no child folder
        child = rest.split("/", 1)[0]
        folder_path = prefix + child
        seen.setdefault(child, folder_path)
    return sorted(seen.items())


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


async def load_planner_spec(
    vault: ObsidianVaultClient, spec_path: str = DEFAULT_SPEC_PATH
) -> PlannerSpec:
    """Read the planner spec from a vault note's frontmatter."""
    fm = await vault.read_frontmatter(spec_path)
    if fm is None:
        raise ValueError(f"Planner spec not found at {spec_path!r}")
    return parse_spec(fm)


async def render_planner(
    vault: ObsidianVaultClient,
    spec_path: str = DEFAULT_SPEC_PATH,
) -> PlannerOutput:
    """Render the planner view by walking source files and bucketing tasks per spec."""
    spec = await load_planner_spec(vault, spec_path)
    candidates = await _enumerate_source_notes(vault, spec)

    # Resolve tag exclusion lazily — parse content once per candidate
    tasks_by_path: dict[str, list[Task]] = {}
    for path in candidates:
        if await _is_tag_excluded(vault, path, spec.exclude_tags):
            continue
        tasks_by_path[path] = [
            t for t in await read_tasks(vault, path) if t.status in spec.include_statuses
        ]

    out_sections: list[PlannerSection] = []
    for section_cfg in spec.sections:
        if section_cfg.kind == "static":
            refs = _collect_for_section(section_cfg, spec, tasks_by_path)
            out_sections.append(PlannerSection(title=section_cfg.title or "", refs=tuple(refs)))
        elif section_cfg.kind == "folderChildren":
            for child_name, folder_path in _folder_children(
                list(tasks_by_path), section_cfg.parent or ""
            ):
                refs = _collect_for_section(
                    section_cfg, spec, tasks_by_path, folder_prefix=folder_path
                )
                out_sections.append(PlannerSection(title=child_name, refs=tuple(refs)))

    return PlannerOutput(sections=tuple(out_sections))


def _collect_for_section(
    section: _SectionConfig,
    spec: PlannerSpec,
    tasks_by_path: dict[str, list[Task]],
    *,
    folder_prefix: str | None = None,
) -> list[TaskRef]:
    """Return tasks matching one section's page + task filters."""
    refs: list[TaskRef] = []
    for path in sorted(tasks_by_path):
        if folder_prefix is not None and not path.startswith(folder_prefix + "/"):
            continue
        if not _page_allowed(path, section.page_match):
            continue
        for task in tasks_by_path[path]:
            if _task_passes_filter(task, spec, section.task_match):
                refs.append(TaskRef(path, task))
    return refs


__all__ = [
    "DEFAULT_SPEC_PATH",
    "PlannerOutput",
    "PlannerSection",
    "PlannerSpec",
    "load_planner_spec",
    "parse_spec",
    "render_planner",
]
