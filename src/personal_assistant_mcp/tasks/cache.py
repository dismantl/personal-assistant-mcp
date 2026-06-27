"""Task cache persistence and pure filters for cache-backed task reads."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from obsidian_livesync_mcp.client import ObsidianVaultClient

from . import planner
from .crud import TaskRef, read_tasks
from .model import Task
from .paths import VAULT_TIMEZONE

CACHE_PATH = ".tasks/cache.json"
CACHE_VERSION = 1

_PAYLOAD_KEYS = {"version", "computed_at", "spec_path", "spec_hash", "tasks"}
_TASK_KEYS = {
    "source_path",
    "body",
    "status",
    "indent",
    "bullet",
    "priority",
    "due",
    "scheduled",
    "start",
    "created",
    "done",
    "cancelled_date",
    "recurrence",
    "line_number",
}


async def compute_cache(vault: ObsidianVaultClient, *, spec_path: str) -> dict[str, Any]:
    """Rebuild the task cache from the planner source-selection spec."""
    spec = await planner.load_planner_spec(vault, spec_path)
    tasks_by_path = await planner.collect_source_tasks(vault, spec)
    payload = {
        "version": CACHE_VERSION,
        "computed_at": _now_iso(),
        "spec_path": spec_path,
        "spec_hash": spec_hash(spec),
        "tasks": [
            task_to_dict(task, path)
            for path in sorted(tasks_by_path)
            for task in tasks_by_path[path]
        ],
    }
    await write_cache(vault, payload)
    return payload


async def read_cache(vault: ObsidianVaultClient) -> dict[str, Any] | None:
    """Read the cache payload, returning ``None`` for any unusable cache state."""
    try:
        note = await vault.read_note(CACHE_PATH)
    except ValueError:
        return None
    if note is None:
        return None

    try:
        payload = json.loads(note.content)
        _validate_payload(payload)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None
    return payload


async def write_cache(vault: ObsidianVaultClient, payload: dict[str, Any]) -> None:
    """Write ``payload`` to the vault cache path using stable pretty JSON."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    await vault.write_note(CACHE_PATH, serialized)


async def patch_cache_for_path(
    vault: ObsidianVaultClient,
    file_path: str,
    *,
    spec_path: str,
) -> None:
    """Patch one file's task entries inside an existing cache payload."""
    payload = await read_cache(vault)
    if payload is None:
        return

    old_tasks = payload["tasks"]
    payload["tasks"] = [task for task in old_tasks if task["source_path"] != file_path]

    spec = await planner.load_planner_spec(vault, spec_path)
    in_scope = planner._matches_source(file_path, spec) and not any(  # noqa: SLF001
        frag in file_path for frag in spec.exclude_paths_containing
    )
    if in_scope and not await planner._is_tag_excluded(vault, file_path, spec.exclude_tags):  # noqa: SLF001
        payload["tasks"].extend(
            task_to_dict(task, file_path) for task in await read_tasks(vault, file_path)
        )

    if payload["tasks"] == old_tasks:
        return
    payload["computed_at"] = _now_iso()
    await write_cache(vault, payload)


async def cached_refs(
    vault: ObsidianVaultClient,
    *,
    spec_path: str,
) -> tuple[list[TaskRef], dict[str, Any] | None]:
    """Return cached refs and cache metadata, or scoped live refs with ``None`` meta."""
    payload = await read_cache(vault)
    if payload is not None and payload["spec_path"] == spec_path:
        return [dict_to_ref(task) for task in payload["tasks"]], _meta(payload)

    spec = await planner.load_planner_spec(vault, spec_path)
    tasks_by_path = await planner.collect_source_tasks(vault, spec)
    return [
        TaskRef(path, task) for path in sorted(tasks_by_path) for task in tasks_by_path[path]
    ], None


def filter_list(
    refs: list[TaskRef],
    *,
    folder: str | None = None,
    priority_bucket: str | None = None,
    statuses: tuple[str, ...] = (" ", "/"),
    due_before: date | None = None,
) -> list[TaskRef]:
    """Filter task refs for ``tasks_list`` without touching the vault."""
    out: list[TaskRef] = []
    for ref in refs:
        if folder is not None and not _path_in_folder(ref.file_path, folder):
            continue
        if ref.task.status not in statuses:
            continue
        if priority_bucket is not None and ref.task.priority_bucket != priority_bucket:
            continue
        if due_before is not None and (ref.task.due is None or ref.task.due >= due_before):
            continue
        out.append(ref)
    return out


def filter_search(
    refs: list[TaskRef],
    query: str,
    *,
    folder: str | None = None,
    statuses: tuple[str, ...] = (" ", "/"),
) -> list[TaskRef]:
    """Filter task refs for ``tasks_search`` without touching the vault."""
    q = query.strip().lower()
    if not q:
        return []

    out: list[TaskRef] = []
    for ref in refs:
        if folder is not None and not _path_in_folder(ref.file_path, folder):
            continue
        if ref.task.status not in statuses:
            continue
        if q in ref.task.body.lower():
            out.append(ref)
    return out


def task_to_dict(task: Task, source_path: str) -> dict[str, Any]:
    """Serialize every identity-relevant task field for cache storage."""
    return {
        "source_path": source_path,
        "body": task.body,
        "status": task.status,
        "indent": task.indent,
        "bullet": task.bullet,
        "priority": task.priority,
        "due": _date_to_str(task.due),
        "scheduled": _date_to_str(task.scheduled),
        "start": _date_to_str(task.start),
        "created": _date_to_str(task.created),
        "done": _date_to_str(task.done),
        "cancelled_date": _date_to_str(task.cancelled_date),
        "recurrence": task.recurrence,
        "line_number": task.line_number,
    }


def dict_to_ref(data: dict[str, Any]) -> TaskRef:
    """Deserialize a cached task dict into a ``TaskRef``."""
    return TaskRef(
        file_path=str(data["source_path"]),
        task=Task(
            body=str(data["body"]),
            status=str(data["status"]),
            indent=int(data["indent"]),
            bullet=str(data["bullet"]),
            priority=_optional_str(data["priority"]),
            due=_date_from_str(data["due"]),
            scheduled=_date_from_str(data["scheduled"]),
            start=_date_from_str(data["start"]),
            created=_date_from_str(data["created"]),
            done=_date_from_str(data["done"]),
            cancelled_date=_date_from_str(data["cancelled_date"]),
            recurrence=_optional_str(data["recurrence"]),
            line_number=_optional_int(data["line_number"]),
        ),
    )


def spec_hash(spec: planner.PlannerSpec) -> str:
    """Return a stable hash of the parsed planner spec's canonical form."""
    canonical = {
        "roots": sorted(spec.roots),
        "basename_matches_ci": sorted(spec.basename_matches_ci),
        "exclude_paths_containing": sorted(spec.exclude_paths_containing),
        "exclude_tags": sorted(spec.exclude_tags),
        "priority_buckets": {
            name: sorted(markers) for name, markers in sorted(spec.priority_buckets.items())
        },
        "include_statuses": sorted(spec.include_statuses),
        "sections": [
            {
                "kind": section.kind,
                "section_id": section.section_id,
                "title": section.title,
                "parent": section.parent,
                "title_from": section.title_from,
                "page_exclude_paths_containing": sorted(
                    section.page_match.exclude_paths_containing
                ),
                "task_priority_kind": section.task_match.priority_kind,
                "task_priority_bucket": section.task_match.priority_bucket,
            }
            for section in spec.sections
        ],
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _validate_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("cache payload must be an object")
    if not _PAYLOAD_KEYS.issubset(payload):
        raise ValueError("cache payload missing required keys")
    if payload["version"] != CACHE_VERSION:
        raise ValueError("unsupported cache version")
    if not isinstance(payload["tasks"], list):
        raise ValueError("cache tasks must be a list")
    for item in payload["tasks"]:
        if not isinstance(item, dict) or not _TASK_KEYS.issubset(item):
            raise ValueError("cache task missing required keys")
        dict_to_ref(item)


def _meta(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "computed_at": payload["computed_at"],
        "spec_path": payload["spec_path"],
        "spec_hash": payload["spec_hash"],
    }


def _path_in_folder(path: str, folder: str) -> bool:
    prefix = folder.rstrip("/") + "/"
    return path == folder or path.startswith(prefix)


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _date_from_str(value: Any) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(str(value))


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _now_iso() -> str:
    return datetime.now(VAULT_TIMEZONE).isoformat()


__all__ = [
    "CACHE_PATH",
    "CACHE_VERSION",
    "cached_refs",
    "compute_cache",
    "dict_to_ref",
    "filter_list",
    "filter_search",
    "patch_cache_for_path",
    "read_cache",
    "spec_hash",
    "task_to_dict",
    "write_cache",
]
