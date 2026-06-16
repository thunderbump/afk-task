from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


REQUIRED_RUNNABLE_METADATA = {
    "afk_enabled",
    "afk_runner",
    "target_repo",
    "target_repo_path",
    "target_base_branch",
    "branch_policy",
    "validation_command",
}

LIKELY_FILE_METADATA_KEYS = (
    "likely_files",
    "likely_file_paths",
    "target_files",
    "changed_paths",
    "files",
)

ENVIRONMENT_GATE_METADATA_KEYS = (
    "environment_gates",
    "stop_conditions",
    "human_gates",
    "gates",
)

SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"[a-z0-9_.-]*(?:api[_-]?key|access[_-]?key|private[_-]?key|token|"
    r"secret|password|credential)[a-z0-9_.-]*"
    r")(\s*[:=]\s*)([^\s,;`]+)"
)


@dataclass(frozen=True)
class WorkstreamContext:
    current_issue: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    parent_issue: dict[str, Any] | None


def build_workstream_context(
    *,
    current_issue: dict[str, Any],
    issues: Iterable[dict[str, Any]],
) -> WorkstreamContext:
    records = deduplicated_issue_records([*issues, current_issue])
    records_by_id = {
        issue_id: issue
        for issue in records
        if (issue_id := issue_id_from(issue)) is not None
    }

    current_id = issue_id_from(current_issue)
    parent_id = parent_id_for(current_issue)
    parent_issue = records_by_id.get(parent_id) if parent_id is not None else None
    if parent_issue is None:
        parent_issue = parent_from_dependencies(current_issue)
    if parent_issue is None and current_id is not None:
        for issue in records:
            if issue_id_from(issue) == current_id:
                continue
            if issue.get("issue_type") == "feature" and "prd" in set(
                issue.get("labels") or []
            ):
                parent_issue = issue
                break

    return WorkstreamContext(
        current_issue=dict(current_issue),
        issues=tuple(records),
        parent_issue=dict(parent_issue) if parent_issue is not None else None,
    )


def render_workstream_context_markdown(context: WorkstreamContext) -> list[str]:
    current = context.current_issue
    metadata = safe_metadata(current)
    lines = ["## Workstream Context", ""]

    if context.parent_issue is not None:
        parent_id = issue_id_from(context.parent_issue) or "<unknown>"
        parent_title = one_line(context.parent_issue.get("title") or "Untitled parent")
        lines.append(f"- Parent: {parent_id} - {redact_sensitive_text(parent_title)}")
        parent_summary = description_summary(context.parent_issue)
        if parent_summary:
            lines.append(f"- Parent summary: {parent_summary}")
    else:
        lines.append("- Parent: not provided in available context")

    workstream_id = metadata.get("workstream_id") or workstream_id_from(context.issues)
    if workstream_id:
        lines.append(f"- Workstream: {redact_sensitive_text(str(workstream_id))}")
    branch_policy = metadata.get("branch_policy")
    if branch_policy:
        lines.append(f"- Branch policy: {redact_sensitive_text(str(branch_policy))}")
    current_id = issue_id_from(current) or "<unknown>"
    current_title = one_line(current.get("title") or "Untitled bead")
    lines.append(f"- Current bead: {current_id} - {redact_sensitive_text(current_title)}")
    lines.append("")

    lines.extend(dependency_chain_lines(context))
    lines.append("")
    lines.extend(sibling_readiness_lines(context))
    lines.append("")
    lines.extend(likely_file_lines(current))
    lines.append("")
    lines.extend(validation_command_lines(current))
    lines.append("")
    lines.extend(environment_gate_lines(current))
    return lines


def dependency_chain_lines(context: WorkstreamContext) -> list[str]:
    paths = dependency_paths(context)
    lines = ["### Dependency Chain"]
    if not paths:
        lines.append("- No blocking dependencies were provided for this bead.")
        return lines

    records_by_id = issues_by_id(context.issues)
    current_id = issue_id_from(context.current_issue)
    for path in paths:
        rendered = " -> ".join(
            dependency_node_label(
                issue_id,
                records_by_id.get(issue_id),
                is_current=(issue_id == current_id),
            )
            for issue_id in path
        )
        lines.append(f"- {rendered}")
    return lines


def sibling_readiness_lines(context: WorkstreamContext) -> list[str]:
    lines = ["### Sibling Readiness"]
    siblings = sibling_issues(context)
    if not siblings:
        lines.append("- No sibling workstream records were provided.")
        return lines

    current_id = issue_id_from(context.current_issue)
    records_by_id = issues_by_id(context.issues)
    for issue in sorted(siblings, key=issue_sort_key):
        issue_id = issue_id_from(issue) or "<unknown>"
        title = one_line(issue.get("title") or "Untitled bead")
        readiness = readiness_label(
            issue,
            records_by_id=records_by_id,
            current_id=current_id,
        )
        lines.append(
            f"- {issue_id}: {readiness} - {redact_sensitive_text(title)}"
        )
    return lines


def likely_file_lines(issue: dict[str, Any]) -> list[str]:
    lines = ["### Likely Files"]
    files = likely_files_from_metadata(safe_metadata(issue))
    if not files:
        lines.append(
            "- No explicit likely files metadata. Start with `rg` against the bead "
            "title/description, then edit only the relevant source and tests."
        )
        return lines
    for file_path in files:
        lines.append(f"- `{redact_sensitive_text(file_path)}`")
    return lines


def validation_command_lines(issue: dict[str, Any]) -> list[str]:
    metadata = safe_metadata(issue)
    lines = ["### Validation Commands"]
    light_command = metadata.get("light_verification_command")
    if light_command:
        lines.append(f"- Light: `{redact_sensitive_text(str(light_command))}`")
    validation_command = metadata.get("validation_command")
    if validation_command:
        lines.append(f"- Final: `{redact_sensitive_text(str(validation_command))}`")
    lines.append("- Sanity: `git diff --check` before handing off changes.")
    return lines


def environment_gate_lines(issue: dict[str, Any]) -> list[str]:
    lines = ["## Environment Gates"]
    gates = [
        "Stop if the target checkout has uncommitted changes unless this run uses "
        "an isolated worktree.",
        "Stop if any blocking dependency is open or missing from the provided "
        "workstream context.",
        "Stop if Case checkout, target repo, or validation tools are unavailable.",
        "Stop before using secrets or credentials that are not already configured "
        "outside git; never paste them into task files or logs.",
        "Stop on failed light verification or final validation.",
    ]
    gates.extend(metadata_gates(safe_metadata(issue)))
    for gate in gates:
        lines.append(f"- {redact_sensitive_text(gate)}")
    return lines


def deduplicated_issue_records(issues: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for issue in flatten_issue_records(issues):
        issue_id = issue_id_from(issue)
        if issue_id is None:
            continue
        if issue_id not in records_by_id:
            order.append(issue_id)
            records_by_id[issue_id] = dict(issue)
            continue
        merged = dict(records_by_id[issue_id])
        merged.update(issue)
        records_by_id[issue_id] = merged

    return [records_by_id[issue_id] for issue_id in order]


def flatten_issue_records(issues: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        yield dict(issue)

        for dependent in issue.get("dependents") or []:
            if not isinstance(dependent, dict):
                continue
            normalized = dict(dependent)
            if (
                normalized.get("dependency_type") == "parent-child"
                and "parent" not in normalized
                and isinstance(issue.get("id"), str)
            ):
                normalized["parent"] = issue["id"]
            yield normalized


def parent_from_dependencies(issue: dict[str, Any]) -> dict[str, Any] | None:
    for dependency in issue.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        if dependency_kind(dependency) != "parent-child":
            continue
        if issue_id_from(dependency) is not None:
            return dependency
    return None


def dependency_paths(context: WorkstreamContext) -> list[list[str]]:
    current_id = issue_id_from(context.current_issue)
    if current_id is None:
        return []
    records_by_id = issues_by_id(context.issues)
    records_by_id[current_id] = context.current_issue
    return dependency_paths_to(current_id, records_by_id, seen=set())


def dependency_paths_to(
    issue_id: str,
    records_by_id: dict[str, dict[str, Any]],
    *,
    seen: set[str],
) -> list[list[str]]:
    if issue_id in seen:
        return [[issue_id]]
    issue = records_by_id.get(issue_id)
    if issue is None:
        return [[issue_id]]

    blocker_ids = [
        blocker_id
        for dependency in issue.get("dependencies") or []
        if isinstance(dependency, dict)
        and dependency_kind(dependency) == "blocks"
        and (blocker_id := dependency_blocker_id(dependency)) is not None
    ]
    if not blocker_ids:
        return []

    paths: list[list[str]] = []
    for blocker_id in blocker_ids:
        blocker_paths = dependency_paths_to(
            blocker_id,
            records_by_id,
            seen={*seen, issue_id},
        )
        if blocker_paths:
            paths.extend([*path, issue_id] for path in blocker_paths)
        else:
            paths.append([blocker_id, issue_id])
    return paths


def sibling_issues(context: WorkstreamContext) -> list[dict[str, Any]]:
    current = context.current_issue
    current_id = issue_id_from(current)
    parent_id = parent_id_for(current)
    workstream_id = safe_metadata(current).get("workstream_id")
    parent_record_id = (
        issue_id_from(context.parent_issue) if context.parent_issue is not None else None
    )
    siblings: list[dict[str, Any]] = []

    for issue in context.issues:
        issue_id = issue_id_from(issue)
        if issue_id is None or issue_id == parent_record_id:
            continue
        if issue_id == current_id:
            siblings.append(current)
            continue
        if parent_id is not None and issue.get("parent") == parent_id:
            siblings.append(issue)
            continue
        if workstream_id and safe_metadata(issue).get("workstream_id") == workstream_id:
            siblings.append(issue)

    if current_id is not None and all(
        issue_id_from(issue) != current_id for issue in siblings
    ):
        siblings.append(current)
    return deduplicated_issue_records(siblings)


def readiness_label(
    issue: dict[str, Any],
    *,
    records_by_id: dict[str, dict[str, Any]],
    current_id: str | None,
) -> str:
    issue_id = issue_id_from(issue)
    if issue_id == current_id:
        return "current"
    if issue.get("status") == "closed":
        return "closed"

    blockers = open_blockers(issue, records_by_id)
    if blockers:
        return f"blocked by {', '.join(blockers)}"

    metadata = safe_metadata(issue)
    labels = set(issue.get("labels") or [])
    if issue.get("status") != "open":
        return f"not ready: status {issue.get('status') or '<missing>'}"
    if "ready-for-agent" not in labels:
        return "not ready: missing ready-for-agent"
    missing = [
        field
        for field in sorted(REQUIRED_RUNNABLE_METADATA)
        if field not in metadata or metadata[field] in ("", None)
    ]
    if missing:
        return f"not ready: missing metadata {', '.join(missing)}"
    active_run_id = metadata.get("active_run_id")
    if active_run_id:
        return "not ready: active run"
    return "ready"


def open_blockers(
    issue: dict[str, Any],
    records_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    for dependency in issue.get("dependencies") or []:
        if not isinstance(dependency, dict) or dependency_kind(dependency) != "blocks":
            continue
        blocker_id = dependency_blocker_id(dependency)
        if blocker_id is None:
            continue
        status = dependency.get("status")
        if status is None and blocker_id in records_by_id:
            status = records_by_id[blocker_id].get("status")
        if status != "closed":
            blockers.append(blocker_id)
    return blockers


def likely_files_from_metadata(metadata: dict[str, Any]) -> list[str]:
    for key in LIKELY_FILE_METADATA_KEYS:
        values = string_list(metadata.get(key))
        if values:
            return values
    return []


def metadata_gates(metadata: dict[str, Any]) -> list[str]:
    gates: list[str] = []
    for key in ENVIRONMENT_GATE_METADATA_KEYS:
        gates.extend(string_list(metadata.get(key)))
    return gates


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def description_summary(issue: dict[str, Any]) -> str:
    description = redact_sensitive_text(str(issue.get("description") or "")).strip()
    if not description:
        return ""
    summary = one_line(description)
    if len(summary) <= 240:
        return summary
    return f"{summary[:237].rstrip()}..."


def dependency_node_label(
    issue_id: str,
    issue: dict[str, Any] | None,
    *,
    is_current: bool,
) -> str:
    if is_current:
        return f"{issue_id} (current)"
    status = issue.get("status") if issue is not None else None
    return f"{issue_id} ({status or 'unknown'})"


def issue_id_from(issue: dict[str, Any] | None) -> str | None:
    if not isinstance(issue, dict):
        return None
    issue_id = issue.get("id") or issue.get("depends_on_id")
    return issue_id if isinstance(issue_id, str) and issue_id else None


def parent_id_for(issue: dict[str, Any]) -> str | None:
    parent_id = issue.get("parent")
    if isinstance(parent_id, str) and parent_id:
        return parent_id
    for dependency in issue.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        if dependency_kind(dependency) != "parent-child":
            continue
        parent_id = dependency_blocker_id(dependency)
        if parent_id is not None:
            return parent_id
    return None


def workstream_id_from(issues: Iterable[dict[str, Any]]) -> str | None:
    for issue in issues:
        workstream_id = safe_metadata(issue).get("workstream_id")
        if isinstance(workstream_id, str) and workstream_id:
            return workstream_id
    return None


def issues_by_id(issues: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        issue_id: issue
        for issue in issues
        if (issue_id := issue_id_from(issue)) is not None
    }


def dependency_kind(dependency: dict[str, Any]) -> Any:
    return dependency.get("dependency_type") or dependency.get("type")


def dependency_blocker_id(dependency: dict[str, Any]) -> str | None:
    value = dependency.get("id") or dependency.get("depends_on_id")
    return value if isinstance(value, str) and value else None


def safe_metadata(issue: dict[str, Any]) -> dict[str, Any]:
    metadata = issue.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def issue_sort_key(issue: dict[str, Any]) -> tuple[Any, ...]:
    return issue_id_sort_key(str(issue.get("id", "")))


def issue_id_sort_key(issue_id: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.split(r"(\d+)", issue_id):
        if part.isdigit():
            parts.append(int(part))
        elif part:
            parts.append(part)
    return tuple(parts)


def one_line(value: Any) -> str:
    return " ".join(str(value).strip().split())


def redact_sensitive_text(value: str) -> str:
    return SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", value)
