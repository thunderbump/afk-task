from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .cli import (
    eligibility_rejections,
    review_branch_for,
    run_bead,
)
from .workstream_context import (
    ENVIRONMENT_GATE_METADATA_KEYS,
    LIKELY_FILE_METADATA_KEYS,
)
from .workstream_selection import (
    WorkstreamSelectionError,
    deduplicated_issue_records,
    issue_matches_scope,
    issue_with_resolved_dependency_statuses,
    load_parent_workstream_issues,
    load_workstream_issues,
    select_runnable_workstream_beads,
)


SAFE_METADATA_KEYS = {
    "afk_enabled",
    "afk_runner",
    "target_repo",
    "target_repo_path",
    "target_base_branch",
    "branch_policy",
    "validation_command",
    "light_verification_command",
    "workstream_id",
    *LIKELY_FILE_METADATA_KEYS,
    *ENVIRONMENT_GATE_METADATA_KEYS,
}

SANITIZED_ENV_KEYS = (
    "BEADS_DIR",
    "BEADS_DOLT_PASSWORD",
    "AUTOMATION_BEADS_WORKSPACE",
    "OPENAI_API_KEY",
    "PI_CODING_AGENT_DIR",
)


def run_workstream_command(
    *,
    parent_id: str | None,
    workstream_id: str | None,
    workstream_json: Path | None,
    workstream_seed_ref: str | None,
    state_dir: Path,
    case_checkout: Path | None,
    case_data_dir: Path | None,
    case_command: str,
    case_dry_run: bool,
    case_runtime_module: Path | None,
    target_checkout_mode: str,
    target_worktree_root: Path | None,
    case_codex_session: bool,
    codex_auth_file: Path,
    codex_model: str,
    case_codex_scout_only: bool,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
    beads_lifecycle: bool,
    close_bead_on_success: bool,
    skip_final_validation: bool,
) -> int:
    try:
        issues = load_workstream_issue_records(
            parent_id=parent_id,
            workstream_id=workstream_id,
            workstream_json=workstream_json,
            bd_command=bd_command,
            beads_workspace=beads_workspace,
            beads_password_file=beads_password_file,
        )
    except (OSError, ValueError, WorkstreamSelectionError) as error:
        print(f"run-workstream failed to load workstream: {error}", file=sys.stderr)
        return 1

    return run_workstream_issues(
        issues,
        parent_id=parent_id,
        workstream_id=workstream_id,
        workstream_seed_ref=workstream_seed_ref,
        state_dir=state_dir,
        case_checkout=case_checkout,
        case_data_dir=case_data_dir,
        case_command=case_command,
        case_dry_run=case_dry_run,
        case_runtime_module=case_runtime_module,
        target_checkout_mode=target_checkout_mode,
        target_worktree_root=target_worktree_root,
        case_codex_session=case_codex_session,
        codex_auth_file=codex_auth_file,
        codex_model=codex_model,
        case_codex_scout_only=case_codex_scout_only,
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
        beads_lifecycle=beads_lifecycle,
        close_bead_on_success=close_bead_on_success,
        skip_final_validation=skip_final_validation,
    )


def load_workstream_issue_records(
    *,
    parent_id: str | None,
    workstream_id: str | None,
    workstream_json: Path | None,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
) -> list[dict[str, Any]]:
    if workstream_json is not None:
        payload = json.loads(workstream_json.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = (
                payload.get("issues")
                or payload.get("workstream_issues")
                or payload
            )
        return deduplicated_issue_records(payload)
    if parent_id is not None:
        return load_parent_workstream_issues(
            parent_id=parent_id,
            bd_command=bd_command,
            beads_workspace=beads_workspace,
            beads_password_file=beads_password_file,
        )
    if workstream_id is None:
        raise ValueError("parent_id or workstream_id is required")
    return load_workstream_issues(
        workstream_id=workstream_id,
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )


def run_workstream_issues(
    issues: list[dict[str, Any]],
    *,
    parent_id: str | None,
    workstream_id: str | None,
    workstream_seed_ref: str | None,
    state_dir: Path,
    case_checkout: Path | None,
    case_data_dir: Path | None,
    case_command: str,
    case_dry_run: bool,
    case_runtime_module: Path | None,
    target_checkout_mode: str,
    target_worktree_root: Path | None,
    case_codex_session: bool,
    codex_auth_file: Path,
    codex_model: str,
    case_codex_scout_only: bool,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
    beads_lifecycle: bool,
    close_bead_on_success: bool,
    skip_final_validation: bool,
) -> int:
    state_dir = state_dir.resolve()
    working_issues = [deepcopy(issue) for issue in issues]
    completed_ids: list[str] = []
    shared_checkout: Path | None = None
    final_validation_command: str | None = None
    final_validation_cwd: Path | None = None

    with TemporaryDirectory(prefix="run-workstream-") as tmp:
        fixture_dir = Path(tmp)
        while True:
            runnable = [
                issue
                for issue in select_runnable_workstream_beads(
                    working_issues,
                    parent_id=parent_id,
                    workstream_id=workstream_id,
                )
                if issue.get("id") not in completed_ids
            ]
            if not runnable:
                break

            issue = runnable[0]
            issue_id = str(issue["id"])
            workstream_seed_ref_for_run = (
                workstream_seed_ref
                if shared_checkout is None and uses_shared_sequential_branch(issue)
                else None
            )
            issue_for_run = issue_with_shared_checkout(
                issue,
                shared_checkout=shared_checkout,
            )
            per_bead_checkout_mode = target_checkout_mode
            skip_target_preparation = False
            if shared_checkout is not None and uses_shared_sequential_branch(issue):
                per_bead_checkout_mode = "direct"
                checkout_error = shared_checkout_error(
                    checkout=shared_checkout,
                    review_branch=review_branch_for(
                        bead_id=issue_id,
                        metadata=issue_for_run["metadata"],
                    ),
                )
                if checkout_error is not None:
                    print(
                        f"run-workstream target repo invalid: {checkout_error}",
                        file=sys.stderr,
                    )
                    return 1
                skip_target_preparation = True

            bead_json = fixture_dir / f"{safe_file_name(issue_id)}.json"
            context_json = fixture_dir / f"{safe_file_name(issue_id)}-context.json"
            bead_json.write_text(
                json.dumps(safe_issue_fixture(issue_for_run), indent=2) + "\n",
                encoding="utf-8",
            )
            context_json.write_text(
                json.dumps(
                    safe_workstream_context_fixture(working_issues, issue_for_run),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_bead(
                bead_id=issue_id,
                bead_json=bead_json,
                workstream_context_json=context_json,
                state_dir=state_dir,
                case_checkout=case_checkout,
                case_data_dir=case_data_dir,
                case_command=case_command,
                case_dry_run=case_dry_run,
                case_runtime_module=case_runtime_module,
                target_checkout_mode=per_bead_checkout_mode,
                target_worktree_root=target_worktree_root,
                case_codex_session=case_codex_session,
                codex_auth_file=codex_auth_file,
                codex_model=codex_model,
                case_codex_scout_only=case_codex_scout_only,
                bd_command=bd_command,
                beads_workspace=beads_workspace,
                beads_password_file=beads_password_file,
                beads_lifecycle=beads_lifecycle,
                close_bead_on_success=close_bead_on_success,
                skip_target_preparation=skip_target_preparation,
                workstream_seed_ref=workstream_seed_ref_for_run,
            )
            if result != 0:
                print(
                    f"run-workstream stopped after {issue_id}: run exited {result}",
                    file=sys.stderr,
                )
                return result

            request = latest_execution_request(state_dir=state_dir, bead_id=issue_id)
            target_checkout = target_checkout_from_request(request, issue_for_run)
            if uses_shared_sequential_branch(issue):
                shared_checkout = target_checkout
            final_validation_cwd = target_checkout
            final_validation_command = str(issue["metadata"]["validation_command"])

            light_result = run_light_verification(
                issue=issue,
                cwd=target_checkout,
            )
            if light_result != 0:
                return light_result

            mark_issue_closed(working_issues, issue_id)
            completed_ids.append(issue_id)

    blocked = blocked_dependency_issues(
        working_issues,
        parent_id=parent_id,
        workstream_id=workstream_id,
    )
    if blocked:
        print(
            "run-workstream blocked by dependencies: "
            + ", ".join(str(issue["id"]) for issue in blocked),
            file=sys.stderr,
        )
        return 1

    if not completed_ids:
        print("run-workstream completed: 0 bead(s)")
        return 0

    if (
        not skip_final_validation
        and final_validation_command is not None
        and final_validation_cwd is not None
    ):
        result = run_shell_command(final_validation_command, cwd=final_validation_cwd)
        if result.returncode != 0:
            print(
                "run-workstream final validation failed: "
                f"{final_validation_command} exited {result.returncode}",
                file=sys.stderr,
            )
            return result.returncode or 1
        print("run-workstream final validation passed")

    print(f"run-workstream completed: {len(completed_ids)} bead(s)")
    return 0


def issue_with_shared_checkout(
    issue: dict[str, Any],
    *,
    shared_checkout: Path | None,
) -> dict[str, Any]:
    if shared_checkout is None or not uses_shared_sequential_branch(issue):
        return deepcopy(issue)

    adjusted = deepcopy(issue)
    metadata = dict(adjusted["metadata"])
    review_branch = review_branch_for(bead_id=str(adjusted["id"]), metadata=metadata)
    metadata["target_repo_path"] = str(shared_checkout)
    metadata["target_base_branch"] = review_branch
    adjusted["metadata"] = metadata
    return adjusted


def uses_shared_sequential_branch(issue: dict[str, Any]) -> bool:
    metadata = issue.get("metadata") or {}
    return metadata.get("branch_policy") == "shared-sequential"


def run_light_verification(*, issue: dict[str, Any], cwd: Path) -> int:
    metadata = issue.get("metadata") or {}
    command = metadata.get("light_verification_command")
    if not command:
        return 0
    command_text = str(command)
    result = run_shell_command(command_text, cwd=cwd)
    issue_id = str(issue.get("id") or "<unknown>")
    if result.returncode != 0:
        print(
            "run-workstream light verification failed for "
            f"{issue_id}: {command_text} exited {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode or 1
    print(f"run-workstream light verification passed: {issue_id}")
    return 0


def run_shell_command(command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in SANITIZED_ENV_KEYS:
        env.pop(key, None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def shared_checkout_error(*, checkout: Path, review_branch: str) -> str | None:
    git_dir = run_git(checkout, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        return f"{checkout} is not a git repository"
    current_branch = run_git(checkout, "branch", "--show-current")
    if current_branch.returncode != 0:
        return f"could not inspect shared checkout branch: {git_error(current_branch)}"
    if current_branch.stdout.strip() != review_branch:
        return (
            "shared checkout is on "
            f"{current_branch.stdout.strip() or '<detached>'}, expected {review_branch}"
        )
    status = run_git(checkout, "status", "--porcelain")
    if status.returncode != 0:
        return f"could not inspect shared checkout status: {git_error(status)}"
    dirty_paths = []
    for line in status.stdout.splitlines():
        path = porcelain_path(line)
        if path and not path.startswith(".case/"):
            dirty_paths.append(path)
    if dirty_paths:
        return (
            "shared checkout has uncommitted changes outside .case/: "
            + ", ".join(dirty_paths)
        )
    return None


def run_git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=checkout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["git", *args],
            128,
            "",
            f"missing path or git executable: {checkout}",
        )


def git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip() or f"git exited {result.returncode}"


def porcelain_path(line: str) -> str:
    if " -> " in line:
        return line.split(" -> ", 1)[1].strip()
    return line[3:].strip()


def latest_execution_request(*, state_dir: Path, bead_id: str) -> Path | None:
    safe_bead_id = bead_id.replace("/", "-")
    runs_dir = state_dir / "runs"
    candidates = list(
        runs_dir.glob(f"run-bead-{safe_bead_id}-*/execution-request.json")
    )
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def target_checkout_from_request(
    request: Path | None,
    issue: dict[str, Any],
) -> Path:
    if request is not None:
        payload = json.loads(request.read_text(encoding="utf-8"))
        checkout = payload.get("target_checkout_path")
        if isinstance(checkout, str) and checkout:
            return Path(checkout)
    return Path(str(issue["metadata"]["target_repo_path"])).resolve()


def mark_issue_closed(issues: list[dict[str, Any]], issue_id: str) -> None:
    for issue in issues:
        if issue.get("id") == issue_id:
            issue["status"] = "closed"
        for dependent in issue.get("dependents") or []:
            if isinstance(dependent, dict) and dependent.get("id") == issue_id:
                dependent["status"] = "closed"


def blocked_dependency_issues(
    issues: list[dict[str, Any]],
    *,
    parent_id: str | None,
    workstream_id: str | None,
) -> list[dict[str, Any]]:
    issues_by_id = {
        str(issue["id"]): issue
        for issue in deduplicated_issue_records(issues)
        if isinstance(issue.get("id"), str) and issue["id"]
    }
    blocked: list[dict[str, Any]] = []
    for issue in issues_by_id.values():
        if not issue_matches_scope(
            issue,
            parent_id=parent_id,
            workstream_id=workstream_id,
        ):
            continue
        normalized = issue_with_resolved_dependency_statuses(issue, issues_by_id)
        reasons = eligibility_rejections(normalized)
        if reasons and all(
            reason.startswith("open blocking dependency ") for reason in reasons
        ):
            blocked.append(normalized)
    return blocked


def safe_workstream_context_fixture(
    issues: list[dict[str, Any]],
    current_issue: dict[str, Any],
) -> list[dict[str, Any]]:
    current_id = current_issue.get("id")
    fixture = []
    for issue in issues:
        if issue.get("id") == current_id:
            fixture.append(safe_issue_fixture(current_issue))
        else:
            fixture.append(safe_issue_fixture(issue))
    return fixture


def safe_issue_fixture(issue: dict[str, Any]) -> dict[str, Any]:
    fixture: dict[str, Any] = {}
    for key in (
        "id",
        "title",
        "description",
        "status",
        "issue_type",
        "labels",
        "parent",
        "dependencies",
    ):
        if key in issue:
            fixture[key] = deepcopy(issue[key])

    metadata = issue.get("metadata")
    if isinstance(metadata, dict):
        fixture["metadata"] = {
            key: deepcopy(value)
            for key, value in metadata.items()
            if key in SAFE_METADATA_KEYS
        }
    return fixture


def safe_file_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
