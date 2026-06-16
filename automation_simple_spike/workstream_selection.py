from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Iterable

from .beads_env import beads_subprocess_env
from .cli import eligibility_rejections


class WorkstreamSelectionError(RuntimeError):
    pass


def select_runnable_workstream_beads(
    payload: Any,
    *,
    parent_id: str | None = None,
    workstream_id: str | None = None,
) -> list[dict[str, Any]]:
    if parent_id is None and workstream_id is None:
        raise ValueError("parent_id or workstream_id is required")

    issues = deduplicated_issue_records(payload)
    issues_by_id = {
        str(issue["id"]): issue
        for issue in issues
        if isinstance(issue.get("id"), str) and issue["id"]
    }
    selected: list[dict[str, Any]] = []

    for issue in issues:
        if not issue_matches_scope(
            issue,
            parent_id=parent_id,
            workstream_id=workstream_id,
        ):
            continue
        normalized_issue = issue_with_resolved_dependency_statuses(issue, issues_by_id)
        if not eligibility_rejections(normalized_issue):
            selected.append(normalized_issue)

    return dependency_ordered_issues(selected)


def load_parent_workstream_issues(
    *,
    parent_id: str,
    bd_command: str,
    beads_workspace: Any,
    beads_password_file: Any,
) -> list[dict[str, Any]]:
    parent_payload = run_bd_json(
        ["show", parent_id, "--json"],
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )
    children_payload = run_bd_json(
        ["children", parent_id, "--json"],
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )
    return [
        *issue_records_from_beads_payload(parent_payload),
        *issue_records_from_beads_payload(children_payload),
    ]


def load_workstream_issues(
    *,
    workstream_id: str,
    bd_command: str,
    beads_workspace: Any,
    beads_password_file: Any,
) -> list[dict[str, Any]]:
    payload = run_bd_json(
        [
            "list",
            "--metadata-field",
            f"workstream_id={workstream_id}",
            "--json",
            "--all",
            "--limit",
            "0",
        ],
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )
    return list(issue_records_from_beads_payload(payload))


def run_bd_json(
    args: list[str],
    *,
    bd_command: str,
    beads_workspace: Any,
    beads_password_file: Any,
) -> Any:
    result = subprocess.run(
        [bd_command, *args],
        cwd=beads_workspace,
        env=beads_subprocess_env(beads_password_file),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"bd {' '.join(args)} failed"
        raise WorkstreamSelectionError(message)
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise WorkstreamSelectionError(
            f"bd {' '.join(args)} returned invalid JSON"
        ) from error


def deduplicated_issue_records(payload: Any) -> list[dict[str, Any]]:
    issues_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for issue in issue_records_from_beads_payload(payload):
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id:
            continue
        if issue_id not in issues_by_id:
            order.append(issue_id)
            issues_by_id[issue_id] = dict(issue)
            continue
        merged = dict(issues_by_id[issue_id])
        merged.update(issue)
        issues_by_id[issue_id] = merged

    return [issues_by_id[issue_id] for issue_id in order]


def issue_records_from_beads_payload(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        raw_issues = payload
    else:
        raw_issues = [payload]

    for raw_issue in raw_issues:
        if not isinstance(raw_issue, dict):
            continue
        issue = dict(raw_issue)
        yield issue

        dependents = raw_issue.get("dependents") or []
        if not isinstance(dependents, list):
            continue
        for raw_dependent in dependents:
            if not isinstance(raw_dependent, dict):
                continue
            dependent = dict(raw_dependent)
            if (
                dependent.get("dependency_type") == "parent-child"
                and "parent" not in dependent
                and isinstance(issue.get("id"), str)
            ):
                dependent["parent"] = issue["id"]
            yield dependent


def issue_matches_scope(
    issue: dict[str, Any],
    *,
    parent_id: str | None,
    workstream_id: str | None,
) -> bool:
    issue_id = issue.get("id")
    if parent_id is not None:
        if issue_id == parent_id:
            return False
        if issue.get("parent") != parent_id:
            return False

    if workstream_id is not None:
        metadata = issue.get("metadata") or {}
        if metadata.get("workstream_id") != workstream_id:
            return False

    return True


def issue_with_resolved_dependency_statuses(
    issue: dict[str, Any],
    issues_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    dependencies = issue.get("dependencies")
    if not isinstance(dependencies, list):
        return dict(issue)

    normalized = dict(issue)
    normalized_dependencies: list[dict[str, Any]] = []
    for raw_dependency in dependencies:
        if not isinstance(raw_dependency, dict):
            continue
        dependency = dict(raw_dependency)
        if dependency_kind(dependency) == "blocks" and "status" not in dependency:
            blocker_id = dependency_blocker_id(dependency)
            if blocker_id is not None and blocker_id in issues_by_id:
                dependency["status"] = issues_by_id[blocker_id].get("status")
        normalized_dependencies.append(dependency)
    normalized["dependencies"] = normalized_dependencies
    return normalized


def dependency_kind(dependency: dict[str, Any]) -> Any:
    return dependency.get("dependency_type") or dependency.get("type")


def dependency_blocker_id(dependency: dict[str, Any]) -> str | None:
    value = dependency.get("id") or dependency.get("depends_on_id")
    return value if isinstance(value, str) and value else None


def dependency_ordered_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues_by_id = {
        issue["id"]: issue
        for issue in issues
        if isinstance(issue.get("id"), str) and issue["id"]
    }
    incoming = {issue_id: set[str]() for issue_id in issues_by_id}
    outgoing = {issue_id: set[str]() for issue_id in issues_by_id}

    for issue_id, issue in issues_by_id.items():
        dependencies = issue.get("dependencies") or []
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            if dependency_kind(dependency) != "blocks":
                continue
            blocker_id = dependency_blocker_id(dependency)
            if blocker_id not in issues_by_id:
                continue
            incoming[issue_id].add(blocker_id)
            outgoing[blocker_id].add(issue_id)

    ready = sorted(
        [issue_id for issue_id, blockers in incoming.items() if not blockers],
        key=issue_id_value_sort_key,
    )
    ordered: list[dict[str, Any]] = []
    while ready:
        issue_id = ready.pop(0)
        ordered.append(issues_by_id[issue_id])
        for dependent_id in sorted(outgoing[issue_id], key=issue_id_value_sort_key):
            incoming[dependent_id].discard(issue_id)
            if not incoming[dependent_id] and dependent_id not in ready:
                ready.append(dependent_id)
                ready.sort(key=issue_id_value_sort_key)

    if len(ordered) != len(issues_by_id):
        return sorted(issues, key=issue_id_sort_key)
    return ordered


def issue_id_sort_key(issue: dict[str, Any]) -> tuple[Any, ...]:
    return issue_id_value_sort_key(str(issue.get("id", "")))


def issue_id_value_sort_key(issue_id: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.split(r"(\d+)", issue_id):
        if part.isdigit():
            parts.append(int(part))
        elif part:
            parts.append(part)
    return tuple(parts)
