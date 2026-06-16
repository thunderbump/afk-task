from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .beads_env import beads_subprocess_env
from .workstream_context import (
    WorkstreamContext,
    build_workstream_context,
    redact_sensitive_text,
    render_workstream_context_markdown,
)
from .beads_lifecycle import BeadsLifecycleClient, BeadsLifecycleError, LifecycleRun
from .worktree import WorktreeProvisioningError, provision_target_worktree


REQUIRED_METADATA = [
    "afk_enabled",
    "afk_runner",
    "target_repo",
    "target_repo_path",
    "target_base_branch",
    "branch_policy",
    "validation_command",
]

CODEX_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_TOKEN_EXPIRY_MARGIN_SECONDS = 300


class TargetRepoPreparationError(ValueError):
    pass


@dataclass(frozen=True)
class CaseCodexSession:
    access_token: str
    auth_source_path: Path
    model: str
    pi_config_dir: Path
    scout_only: bool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="automation-simple-workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--bead", required=True)
    run.add_argument("--bead-json")
    run.add_argument(
        "--workstream-context-json",
        help=(
            "Optional fixture JSON with parent/workstream issue records to project "
            "into the generated Case task."
        ),
    )
    run.add_argument("--state-dir", default=".automation-simple")
    run.add_argument(
        "--case-checkout",
        help="Path to the patched workos/case checkout. Defaults to CASE_CHECKOUT.",
    )
    run.add_argument("--case-data-dir")
    run.add_argument("--case-command", default="bun")
    run.add_argument("--case-dry-run", action="store_true")
    run.add_argument("--case-runtime-module")
    run.add_argument(
        "--target-checkout-mode",
        choices=("direct", "worktree"),
        default="direct",
        help="Use the target checkout directly or provision an isolated worktree.",
    )
    run.add_argument(
        "--target-worktree-root",
        help=(
            "Directory for provisioned target worktrees. Defaults to "
            "<state-dir>/target-worktrees."
        ),
    )
    run.add_argument("--case-codex-session", action="store_true")
    run.add_argument(
        "--codex-auth-file",
        default=str(Path.home() / ".codex" / "auth.json"),
    )
    run.add_argument("--codex-model", default="gpt-5.5")
    run.add_argument("--case-codex-scout-only", action="store_true")
    run.add_argument("--bd-command", default="bd")
    run.add_argument("--beads-workspace", default="/home/bump/Projects/beads")
    run.add_argument(
        "--beads-password-file",
        default="/home/bump/Projects/beads/secrets/dolt_beads_password.txt",
    )
    run.add_argument("--beads-lifecycle", action="store_true")
    run.add_argument("--close-bead-on-success", action="store_true")

    select = subparsers.add_parser("select-workstream")
    select_scope = select.add_mutually_exclusive_group(required=True)
    select_scope.add_argument("--parent")
    select_scope.add_argument("--workstream-id")
    select.add_argument("--json", action="store_true")
    select.add_argument("--bd-command", default="bd")
    select.add_argument("--beads-workspace", default="/home/bump/Projects/beads")
    select.add_argument(
        "--beads-password-file",
        default="/home/bump/Projects/beads/secrets/dolt_beads_password.txt",
    )

    run_workstream_parser = subparsers.add_parser("run-workstream")
    run_workstream_scope = run_workstream_parser.add_mutually_exclusive_group(
        required=True
    )
    run_workstream_scope.add_argument("--parent")
    run_workstream_scope.add_argument("--workstream-id")
    run_workstream_parser.add_argument(
        "--workstream-json",
        help="Optional fixture JSON with parent/workstream issue records.",
    )
    run_workstream_parser.add_argument("--state-dir", default=".automation-simple")
    run_workstream_parser.add_argument(
        "--case-checkout",
        help="Path to the patched workos/case checkout. Defaults to CASE_CHECKOUT.",
    )
    run_workstream_parser.add_argument("--case-data-dir")
    run_workstream_parser.add_argument("--case-command", default="bun")
    run_workstream_parser.add_argument("--case-dry-run", action="store_true")
    run_workstream_parser.add_argument("--case-runtime-module")
    run_workstream_parser.add_argument(
        "--target-checkout-mode",
        choices=("direct", "worktree"),
        default="worktree",
        help="Use the target checkout directly or provision an isolated worktree.",
    )
    run_workstream_parser.add_argument(
        "--target-worktree-root",
        help=(
            "Directory for provisioned target worktrees. Defaults to "
            "<state-dir>/target-worktrees."
        ),
    )
    run_workstream_parser.add_argument("--case-codex-session", action="store_true")
    run_workstream_parser.add_argument(
        "--codex-auth-file",
        default=str(Path.home() / ".codex" / "auth.json"),
    )
    run_workstream_parser.add_argument("--codex-model", default="gpt-5.5")
    run_workstream_parser.add_argument("--case-codex-scout-only", action="store_true")
    run_workstream_parser.add_argument("--bd-command", default="bd")
    run_workstream_parser.add_argument(
        "--beads-workspace",
        default="/home/bump/Projects/beads",
    )
    run_workstream_parser.add_argument(
        "--beads-password-file",
        default="/home/bump/Projects/beads/secrets/dolt_beads_password.txt",
    )
    run_workstream_parser.add_argument("--beads-lifecycle", action="store_true")
    run_workstream_parser.add_argument("--close-bead-on-success", action="store_true")
    run_workstream_parser.add_argument(
        "--skip-final-validation",
        action="store_true",
        help="Run child beads and light checks only; do not run final validation.",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_bead(
            bead_id=args.bead,
            bead_json=Path(args.bead_json) if args.bead_json else None,
            workstream_context_json=(
                Path(args.workstream_context_json)
                if args.workstream_context_json
                else None
            ),
            state_dir=Path(args.state_dir),
            case_checkout=configured_case_checkout(args.case_checkout),
            case_data_dir=Path(args.case_data_dir) if args.case_data_dir else None,
            case_command=args.case_command,
            case_dry_run=args.case_dry_run,
            case_runtime_module=(
                Path(args.case_runtime_module) if args.case_runtime_module else None
            ),
            target_checkout_mode=args.target_checkout_mode,
            target_worktree_root=(
                Path(args.target_worktree_root) if args.target_worktree_root else None
            ),
            case_codex_session=args.case_codex_session,
            codex_auth_file=Path(args.codex_auth_file),
            codex_model=args.codex_model,
            case_codex_scout_only=args.case_codex_scout_only,
            bd_command=args.bd_command,
            beads_workspace=Path(args.beads_workspace),
            beads_password_file=Path(args.beads_password_file),
            beads_lifecycle=args.beads_lifecycle,
            close_bead_on_success=args.close_bead_on_success,
        )
    if args.command == "select-workstream":
        return select_workstream(
            parent_id=args.parent,
            workstream_id=args.workstream_id,
            json_output=args.json,
            bd_command=args.bd_command,
            beads_workspace=Path(args.beads_workspace),
            beads_password_file=Path(args.beads_password_file),
        )
    if args.command == "run-workstream":
        from .workstream_runner import run_workstream_command

        return run_workstream_command(
            parent_id=args.parent,
            workstream_id=args.workstream_id,
            workstream_json=(
                Path(args.workstream_json) if args.workstream_json else None
            ),
            state_dir=Path(args.state_dir),
            case_checkout=configured_case_checkout(args.case_checkout),
            case_data_dir=Path(args.case_data_dir) if args.case_data_dir else None,
            case_command=args.case_command,
            case_dry_run=args.case_dry_run,
            case_runtime_module=(
                Path(args.case_runtime_module) if args.case_runtime_module else None
            ),
            target_checkout_mode=args.target_checkout_mode,
            target_worktree_root=(
                Path(args.target_worktree_root)
                if args.target_worktree_root
                else None
            ),
            case_codex_session=args.case_codex_session,
            codex_auth_file=Path(args.codex_auth_file),
            codex_model=args.codex_model,
            case_codex_scout_only=args.case_codex_scout_only,
            bd_command=args.bd_command,
            beads_workspace=Path(args.beads_workspace),
            beads_password_file=Path(args.beads_password_file),
            beads_lifecycle=args.beads_lifecycle,
            close_bead_on_success=args.close_bead_on_success,
            skip_final_validation=args.skip_final_validation,
        )
    parser.error(f"unknown command: {args.command}")
    return 2


def configured_case_checkout(raw_value: str | None) -> Path | None:
    raw_checkout = raw_value or os.environ.get("CASE_CHECKOUT")
    if raw_checkout is None or not raw_checkout.strip():
        return None
    return Path(raw_checkout).expanduser()


def run_bead(
    *,
    bead_id: str,
    bead_json: Path | None,
    workstream_context_json: Path | None,
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
    skip_target_preparation: bool = False,
) -> int:
    if close_bead_on_success and not beads_lifecycle:
        print(
            "run-bead invalid lifecycle flags: --close-bead-on-success requires "
            "--beads-lifecycle",
            file=sys.stderr,
        )
        return 1

    state_dir = state_dir.resolve()
    if case_data_dir is not None:
        case_data_dir = case_data_dir.resolve()
    if case_runtime_module is not None:
        case_runtime_module = case_runtime_module.resolve()
    if target_worktree_root is not None:
        target_worktree_root = target_worktree_root.resolve()
    codex_auth_file = codex_auth_file.resolve()
    issue = load_issue(
        bead_id=bead_id,
        bead_json=bead_json,
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )
    lifecycle_client = (
        BeadsLifecycleClient(
            bd_command=bd_command,
            beads_workspace=beads_workspace,
            beads_password_file=beads_password_file,
        )
        if beads_lifecycle
        else None
    )
    try:
        workstream_context = load_workstream_context(
            current_issue=issue,
            workstream_context_json=workstream_context_json,
        )
    except ValueError as error:
        print(f"run-bead workstream context invalid: {error}", file=sys.stderr)
        return 1
    reasons = eligibility_rejections(issue)
    if reasons:
        print(f"run-bead ineligible: {bead_id}: {'; '.join(reasons)}", file=sys.stderr)
        return 1

    codex_session = None
    if case_codex_session:
        try:
            codex_session = load_case_codex_session(
                auth_file=codex_auth_file,
                model=codex_model,
                pi_config_dir=state_dir / "pi-codex",
                scout_only=case_codex_scout_only,
            )
        except ValueError as error:
            print(f"run-bead codex session invalid: {error}", file=sys.stderr)
            return 1

    if case_checkout is None:
        print(
            "run-bead missing Case checkout: pass --case-checkout "
            "/path/to/workos-case or set CASE_CHECKOUT=/path/to/workos-case",
            file=sys.stderr,
        )
        return 1
    case_checkout = case_checkout.resolve()
    if not case_checkout.is_dir():
        print(
            f"run-bead missing Case checkout: {case_checkout} is not a directory",
            file=sys.stderr,
        )
        return 1

    metadata = issue["metadata"]
    target_source_checkout = Path(str(metadata["target_repo_path"])).resolve()
    target_repo = target_source_checkout
    target_worktree_checkout = None
    case_data = case_data_dir or state_dir / "case-data"
    review_branch = review_branch_for(bead_id=bead_id, metadata=metadata)
    try:
        if skip_target_preparation:
            git_dir = run_target_git(target_repo, "rev-parse", "--git-dir")
            if git_dir.returncode != 0:
                raise TargetRepoPreparationError(
                    f"{target_repo} is not a git repository"
                )
        elif target_checkout_mode == "worktree":
            provisioned = provision_target_worktree(
                source_checkout=target_source_checkout,
                worktree_root=target_worktree_root
                or state_dir / "target-worktrees",
                base_branch=str(metadata["target_base_branch"]),
                review_branch=review_branch,
            )
            target_source_checkout = provisioned.source_checkout
            target_repo = provisioned.worktree_checkout
            target_worktree_checkout = provisioned.worktree_checkout
        else:
            prepare_target_review_branch(
                target_repo=target_repo,
                base_branch=str(metadata["target_base_branch"]),
                review_branch=review_branch,
            )
    except (TargetRepoPreparationError, WorktreeProvisioningError) as error:
        print(f"run-bead target repo invalid: {error}", file=sys.stderr)
        return 1
    task_md, task_json = write_case_task(
        issue=issue,
        target_repo=target_repo,
        review_branch=review_branch,
        workstream_context=workstream_context,
    )
    write_case_projects_manifest(
        case_data_dir=case_data,
        repo_name=str(metadata["target_repo"]),
        repo_path=target_repo,
        validation_command=str(metadata["validation_command"]),
        codex_session=codex_session,
    )
    if codex_session is not None:
        write_pi_codex_models_config(codex_session)
    case_cli_shim = write_case_cli_shim(
        state_dir=state_dir,
        case_checkout=case_checkout,
    )
    request_path = write_execution_request(
        state_dir=state_dir,
        issue=issue,
        task_md=task_md,
        task_json=task_json,
        case_checkout=case_checkout,
        case_data_dir=case_data,
        case_cli_shim=case_cli_shim,
        review_branch=review_branch,
        case_dry_run=case_dry_run,
        case_runtime_module=case_runtime_module,
        target_checkout_mode=target_checkout_mode,
        target_checkout_path=target_repo,
        target_source_checkout=target_source_checkout,
        target_worktree_checkout=target_worktree_checkout,
        codex_session=codex_session,
    )
    lifecycle_run = None
    if lifecycle_client is not None:
        lifecycle_run = LifecycleRun(
            bead_id=bead_id,
            run_id=request_path.parent.name,
            review_branch=review_branch,
            target_checkout_mode=target_checkout_mode,
            target_checkout_path=target_repo,
            target_source_checkout=target_source_checkout,
            target_worktree_checkout=target_worktree_checkout,
            archive_path=request_path.parent,
        )
        try:
            lifecycle_client.record_start(lifecycle_run)
        except BeadsLifecycleError as error:
            print(f"run-bead lifecycle start failed: {error}", file=sys.stderr)
            return 1
    generated_task_json = task_json.read_text(encoding="utf-8") if case_dry_run else None
    result = run_case_command(
        case_command=case_command,
        case_checkout=case_checkout,
        case_data_dir=case_data,
        case_cli_shim=case_cli_shim,
        task_json=task_json,
        case_dry_run=case_dry_run,
        case_runtime_module=case_runtime_module,
        codex_session=codex_session,
    )
    if case_dry_run and generated_task_json is not None:
        preserve_native_dry_run_task(
            run_dir=request_path.parent,
            task_json=task_json,
            generated_task_json=generated_task_json,
        )
    interpreted_returncode = interpreted_case_returncode(result)
    write_case_command_result(
        request_path.parent,
        result,
        interpreted_returncode,
        redactions=([codex_session.access_token] if codex_session is not None else None),
    )
    if interpreted_returncode != 0:
        failure_summary = case_failure_summary(result)
        if lifecycle_client is not None and lifecycle_run is not None:
            try:
                lifecycle_client.record_failure(
                    lifecycle_run,
                    interpreted_exit_code=interpreted_returncode,
                    failure_summary=failure_summary,
                )
            except BeadsLifecycleError as error:
                print(
                    f"run-bead lifecycle failure update failed: {error}",
                    file=sys.stderr,
                )
        print(f"run-bead failed: {failure_summary}", file=sys.stderr)
        return interpreted_returncode

    if lifecycle_client is not None and lifecycle_run is not None:
        try:
            lifecycle_client.record_success(
                lifecycle_run,
                commit_sha=target_head_commit(target_repo),
                interpreted_exit_code=interpreted_returncode,
                close_bead=close_bead_on_success,
            )
        except (BeadsLifecycleError, TargetRepoPreparationError) as error:
            print(f"run-bead lifecycle success update failed: {error}", file=sys.stderr)
            return 1

    print(f"run-bead handed off: {bead_id}")
    print(f"Case task JSON: {task_json}")
    print(f"Execution request: {request_path}")
    return 0


def select_workstream(
    *,
    parent_id: str | None,
    workstream_id: str | None,
    json_output: bool,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
) -> int:
    from .workstream_selection import (
        WorkstreamSelectionError,
        load_parent_workstream_issues,
        load_workstream_issues,
        select_runnable_workstream_beads,
    )

    try:
        if parent_id is not None:
            issues = load_parent_workstream_issues(
                parent_id=parent_id,
                bd_command=bd_command,
                beads_workspace=beads_workspace,
                beads_password_file=beads_password_file,
            )
        else:
            assert workstream_id is not None
            issues = load_workstream_issues(
                workstream_id=workstream_id,
                bd_command=bd_command,
                beads_workspace=beads_workspace,
                beads_password_file=beads_password_file,
            )
        selected = select_runnable_workstream_beads(
            issues,
            parent_id=parent_id,
            workstream_id=workstream_id,
        )
    except WorkstreamSelectionError as error:
        print(f"select-workstream failed: {error}", file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps(selected, indent=2))
    else:
        for issue in selected:
            print(issue["id"])
    return 0


def load_issue(
    *,
    bead_id: str,
    bead_json: Path | None,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
) -> dict[str, Any]:
    if bead_json is None:
        result = subprocess.run(
            [bd_command, "show", bead_id, "--json"],
            cwd=beads_workspace,
            env=beads_subprocess_env(beads_password_file),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f"bd show failed for {bead_id}"
            raise SystemExit(message)
        payload = json.loads(result.stdout)
    else:
        payload = json.loads(bead_json.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise SystemExit(f"bead fixture returned no issue for {bead_id}")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise SystemExit("bead fixture must contain a JSON object or one-item list")
    return payload


def load_workstream_context(
    *,
    current_issue: dict[str, Any],
    workstream_context_json: Path | None,
) -> WorkstreamContext | None:
    if workstream_context_json is None:
        return None
    try:
        payload = json.loads(workstream_context_json.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"{workstream_context_json} does not exist") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{workstream_context_json} is not valid JSON") from error

    if isinstance(payload, list):
        issues = payload
    elif isinstance(payload, dict):
        raw_issues = payload.get("issues") or payload.get("workstream_issues")
        if isinstance(raw_issues, list):
            issues = raw_issues
        else:
            issues = [payload]
    else:
        raise ValueError("fixture must be a JSON object or list")

    return build_workstream_context(
        current_issue=current_issue,
        issues=[issue for issue in issues if isinstance(issue, dict)],
    )


def load_case_codex_session(
    *,
    auth_file: Path,
    model: str,
    pi_config_dir: Path,
    scout_only: bool,
) -> CaseCodexSession:
    try:
        raw_auth = auth_file.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(
            f"missing Codex auth file at {auth_file}; run Codex login first"
        ) from error

    try:
        auth = json.loads(raw_auth)
    except json.JSONDecodeError as error:
        raise ValueError(f"Codex auth file at {auth_file} is not valid JSON") from error

    if not isinstance(auth, dict):
        raise ValueError("Codex auth file must contain a JSON object")
    if auth.get("auth_mode") != "chatgpt":
        raise ValueError("Codex auth_mode must be chatgpt")

    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError("Codex auth file is missing tokens")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Codex auth file is missing tokens.access_token")

    payload = decode_jwt_payload(access_token)
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise ValueError("Codex access token is missing exp")
    if exp <= time.time() + CODEX_TOKEN_EXPIRY_MARGIN_SECONDS:
        raise ValueError("Codex access token is expired or too close to expiry")
    if not has_chatgpt_account_claim(payload):
        raise ValueError("Codex access token is missing ChatGPT account claim")

    return CaseCodexSession(
        access_token=access_token,
        auth_source_path=auth_file,
        model=model,
        pi_config_dir=pi_config_dir,
        scout_only=scout_only,
    )


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Codex access token is not a JWT")
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Codex access token has a malformed JWT payload") from error
    if not isinstance(parsed, dict):
        raise ValueError("Codex access token JWT payload must be an object")
    return parsed


def has_chatgpt_account_claim(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if (
                key == "https://api.openai.com/auth.chatgpt_account_id"
                or key == "chatgpt_account_id"
            ) and isinstance(nested, str) and nested:
                return True
            if has_chatgpt_account_claim(nested):
                return True
    elif isinstance(value, list):
        return any(has_chatgpt_account_claim(item) for item in value)
    return False


def eligibility_rejections(issue: dict[str, Any]) -> list[str]:
    metadata = issue.get("metadata") or {}
    labels = set(issue.get("labels") or [])
    reasons: list[str] = []

    if issue.get("status") != "open":
        reasons.append("expected open status")

    for field in REQUIRED_METADATA:
        if field not in metadata or metadata[field] in ("", None):
            reasons.append(f"missing metadata {field}")

    if "afk_enabled" in metadata and metadata.get("afk_enabled") is not True:
        reasons.append("invalid metadata afk_enabled: expected true")
    if "afk_runner" in metadata and metadata.get("afk_runner") != "codex":
        reasons.append("invalid metadata afk_runner: expected codex")
    if metadata.get("target_repo_path") and not Path(
        str(metadata["target_repo_path"])
    ).is_absolute():
        reasons.append("invalid metadata target_repo_path: expected absolute path")
    branch_policy = metadata.get("branch_policy")
    if branch_policy and branch_policy not in {"independent", "shared-sequential"}:
        reasons.append(
            "invalid metadata branch_policy: expected independent or shared-sequential"
        )
    if branch_policy == "shared-sequential" and not metadata.get("workstream_id"):
        reasons.append("missing metadata workstream_id")

    if "ready-for-agent" not in labels:
        reasons.append("missing label ready-for-agent")
    if not any(label.startswith("project:") for label in labels):
        reasons.append("missing label project:<slug>")

    active_run_id = metadata.get("active_run_id")
    if active_run_id:
        reasons.append(f"conflicting run active_run_id={active_run_id}")

    for dependency in issue.get("dependencies") or []:
        dependency_type = dependency.get("dependency_type") or dependency.get("type")
        if dependency_type != "blocks":
            continue
        if dependency.get("status") == "closed":
            continue
        blocker = dependency.get("id") or dependency.get("depends_on_id") or "<unknown>"
        reasons.append(f"open blocking dependency {blocker}")

    return reasons


def review_branch_for(*, bead_id: str, metadata: dict[str, Any]) -> str:
    branch_policy = metadata["branch_policy"]
    if branch_policy == "independent":
        return f"agent/{bead_id}"
    return f"agent/{metadata['workstream_id']}"


def prepare_target_review_branch(
    *,
    target_repo: Path,
    base_branch: str,
    review_branch: str,
) -> None:
    git_dir = run_target_git(target_repo, "rev-parse", "--git-dir")
    if git_dir.returncode != 0:
        raise TargetRepoPreparationError(f"{target_repo} is not a git repository")

    status = run_target_git(target_repo, "status", "--porcelain")
    if status.returncode != 0:
        raise TargetRepoPreparationError(
            f"could not inspect git status for {target_repo}: "
            f"{git_error_message(status)}"
        )
    if status.stdout.strip():
        raise TargetRepoPreparationError(
            "target repo has uncommitted changes; commit, stash, or clean them first"
        )

    base = run_target_git(
        target_repo,
        "rev-parse",
        "--verify",
        f"refs/heads/{base_branch}^{{commit}}",
    )
    if base.returncode != 0:
        raise TargetRepoPreparationError(
            f"target base branch does not exist locally: {base_branch}"
        )

    checkout = run_target_git(target_repo, "checkout", "-B", review_branch, base_branch)
    if checkout.returncode != 0:
        raise TargetRepoPreparationError(
            f"could not reset review branch {review_branch} to {base_branch}: "
            f"{git_error_message(checkout)}"
        )


def run_target_git(target_repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=target_repo,
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
            f"missing path or git executable: {target_repo}",
        )


def git_error_message(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip() or f"git exited {result.returncode}"


def target_head_commit(target_repo: Path) -> str:
    result = run_target_git(target_repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise TargetRepoPreparationError(
            f"could not inspect target commit for {target_repo}: "
            f"{git_error_message(result)}"
        )
    commit = result.stdout.strip()
    if not commit:
        raise TargetRepoPreparationError(
            f"could not inspect target commit for {target_repo}: empty HEAD"
        )
    return commit


def case_failure_summary(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode == 0:
        return "Case pipeline reported failure"
    return f"Case command exited {result.returncode}"


def write_case_task(
    *,
    issue: dict[str, Any],
    target_repo: Path,
    review_branch: str,
    workstream_context: WorkstreamContext | None = None,
) -> tuple[Path, Path]:
    metadata = issue["metadata"]
    bead_id = str(issue["id"])
    task_dir = target_repo / ".case" / "tasks" / "active"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_md = task_dir / f"{bead_id}.md"
    task_json = task_dir / f"{bead_id}.task.json"
    validation_command = redact_sensitive_text(str(metadata["validation_command"]))

    description = redact_sensitive_text(str(issue.get("description") or "").strip())
    task_lines = [
        f"# {redact_sensitive_text(str(issue.get('title') or bead_id))}",
        "",
        f"- Bead: {bead_id}",
        f"- Target repo: {metadata['target_repo']}",
        f"- Target base branch: {metadata['target_base_branch']}",
        f"- Review branch: {review_branch}",
        f"- Validation command: {validation_command}",
        "",
        "## Bead Description",
        "",
        description or "(No description provided.)",
        "",
    ]
    if workstream_context is not None:
        task_lines.extend(render_workstream_context_markdown(workstream_context))
        task_lines.append("")
    task_lines.extend(
        [
            "## Evidence Expectations",
            "",
            (
                "Use test-output evidence for this task. Run "
                f"`{validation_command}` and include the command "
                "output or a concise result summary in verifier evidence."
            ),
            (
                "Confirm the validation covers the changed paths, or note any "
                "changed paths that were not covered by the command."
            ),
            (
                "No screenshot or video evidence is required unless the bead "
                "description or implementation task explicitly asks for UI evidence."
            ),
            "",
        ]
    )
    task_md.write_text("\n".join(task_lines), encoding="utf-8")

    task_json.write_text(
        json.dumps(
            {
                "agents": {},
                "branch": review_branch,
                "checkBaseline": None,
                "checkCommand": validation_command,
                "checkTarget": None,
                "created": datetime.now(UTC).isoformat(),
                "id": bead_id,
                "issue": bead_id,
                "issueType": "freeform",
                "manualTested": False,
                "mode": "unattended",
                "prNumber": None,
                "prUrl": None,
                "profile": "standard",
                "repo": metadata["target_repo"],
                "status": "active",
                "tested": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return task_md, task_json


def write_case_projects_manifest(
    *,
    case_data_dir: Path,
    repo_name: str,
    repo_path: Path,
    validation_command: str,
    codex_session: CaseCodexSession | None = None,
) -> Path:
    case_data_dir.mkdir(parents=True, exist_ok=True)
    (case_data_dir / "home").mkdir(parents=True, exist_ok=True)
    (case_data_dir / "config-home").mkdir(parents=True, exist_ok=True)
    projects_path = case_data_dir / "projects.json"
    validation_command = redact_sensitive_text(validation_command)
    manifest: dict[str, Any]
    if projects_path.is_file():
        try:
            manifest = json.loads(projects_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {"repos": []}
    else:
        manifest = {"repos": []}

    repos = manifest.get("repos")
    if not isinstance(repos, list):
        repos = []
    repos = [
        repo
        for repo in repos
        if not isinstance(repo, dict) or repo.get("name") != repo_name
    ]
    repos.append(
        {
            "name": repo_name,
            "evidenceStrategy": "test-output",
            "path": str(repo_path),
            "remote": repo_name,
            "description": "Target repo selected by Beads metadata.",
            "language": "unknown",
            "packageManager": "unknown",
            "commands": {
                "test": validation_command,
                "check": validation_command,
            },
        }
    )
    manifest["repos"] = repos
    projects_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    case_config: dict[str, Any] = {"projects": "./projects.json"}
    if codex_session is not None:
        model_config = {"provider": "openai", "model": codex_session.model}
        if codex_session.scout_only:
            case_config["models"] = {
                "default": {"provider": "invalid", "model": "invalid-scout-only"},
                "scout": model_config,
            }
        else:
            case_config["models"] = {"default": model_config}
    (case_data_dir / "config.json").write_text(
        json.dumps(case_config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return projects_path


def write_case_cli_shim(*, state_dir: Path, case_checkout: Path) -> Path:
    shim_dir = state_dir / "case-bin"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "ca"
    shim_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env sh",
                "set -eu",
                f"CASE_CHECKOUT={shlex.quote(str(case_checkout))}",
                'exec bun "$CASE_CHECKOUT/src/index.ts" "$@"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    shim_path.chmod(0o755)
    return shim_path


def write_pi_codex_models_config(codex_session: CaseCodexSession) -> Path:
    codex_session.pi_config_dir.mkdir(parents=True, exist_ok=True)
    models_path = codex_session.pi_config_dir / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "models": [
                            {
                                "api": "openai-codex-responses",
                                "baseUrl": CODEX_CHATGPT_BASE_URL,
                                "contextWindow": 272000,
                                "cost": {
                                    "cacheRead": 0,
                                    "cacheWrite": 0,
                                    "input": 0,
                                    "output": 0,
                                },
                                "id": codex_session.model,
                                "input": ["text", "image"],
                                "maxTokens": 100000,
                                "name": (
                                    f"{codex_session.model} via ChatGPT Codex"
                                ),
                                "reasoning": True,
                            }
                        ]
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return models_path


def write_execution_request(
    *,
    state_dir: Path,
    issue: dict[str, Any],
    task_md: Path,
    task_json: Path,
    case_checkout: Path,
    case_data_dir: Path,
    case_cli_shim: Path,
    review_branch: str,
    case_dry_run: bool,
    case_runtime_module: Path | None,
    target_checkout_mode: str = "direct",
    target_checkout_path: Path | None = None,
    target_source_checkout: Path | None = None,
    target_worktree_checkout: Path | None = None,
    codex_session: CaseCodexSession | None = None,
) -> Path:
    metadata = issue["metadata"]
    validation_command = redact_sensitive_text(str(metadata["validation_command"]))
    source_checkout = target_source_checkout or Path(
        str(metadata["target_repo_path"])
    ).resolve()
    checkout_path = target_checkout_path or source_checkout
    run_dir = state_dir / "runs" / make_run_id(str(issue["id"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "execution-request.json"
    request_path.write_text(
        json.dumps(
            {
                "bead_id": issue["id"],
                "case_checkout": str(case_checkout),
                "case_cli_shim": str(case_cli_shim),
                "case_data_dir": str(case_data_dir),
                "case_dry_run": case_dry_run,
                "case_runtime_module": (
                    str(case_runtime_module) if case_runtime_module is not None else None
                ),
                "case_codex_session": case_codex_session_metadata(codex_session),
                "case_task_json": str(task_json),
                "case_task_markdown": str(task_md),
                "target_checkout_mode": target_checkout_mode,
                "target_checkout_path": str(checkout_path),
                "target_repo": metadata["target_repo"],
                "target_repo_path": metadata["target_repo_path"],
                "target_source_checkout": str(source_checkout),
                "target_worktree_checkout": (
                    str(target_worktree_checkout)
                    if target_worktree_checkout is not None
                    else None
                ),
                "target_base_branch": metadata["target_base_branch"],
                "review_branch": review_branch,
                "validation_command": validation_command,
                "sandcastle_runtime_adapter": {
                    "status": "scaffolded",
                    "interface": "CaseAgentRuntime",
                    "normal_sandcastle_entrypoint": (
                        "run({ agent, sandbox, cwd, branchStrategy, "
                        "prompt, logging })"
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return request_path


def case_codex_session_metadata(
    codex_session: CaseCodexSession | None,
) -> dict[str, Any]:
    if codex_session is None:
        return {"enabled": False}
    return {
        "auth_source_path": str(codex_session.auth_source_path),
        "enabled": True,
        "model": codex_session.model,
        "pi_config_dir": str(codex_session.pi_config_dir),
        "scout_only": codex_session.scout_only,
    }


def run_case_command(
    *,
    case_command: str,
    case_checkout: Path,
    case_data_dir: Path,
    case_cli_shim: Path,
    task_json: Path,
    case_dry_run: bool,
    case_runtime_module: Path | None,
    codex_session: CaseCodexSession | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        "BEADS_DIR",
        "BEADS_DOLT_PASSWORD",
        "AUTOMATION_BEADS_WORKSPACE",
        "OPENAI_API_KEY",
        "PI_CODING_AGENT_DIR",
    ):
        env.pop(key, None)
    env["CASE_DATA_DIR"] = str(case_data_dir)
    env["XDG_CONFIG_HOME"] = str(case_data_dir / "config-home")
    env["HOME"] = str(case_data_dir / "home")
    inherited_path = env.get("PATH")
    env["PATH"] = (
        f"{case_cli_shim.parent}{os.pathsep}{inherited_path}"
        if inherited_path
        else str(case_cli_shim.parent)
    )
    if codex_session is not None:
        env["OPENAI_API_KEY"] = codex_session.access_token
        env["PI_CODING_AGENT_DIR"] = str(codex_session.pi_config_dir)
    command = [
        case_command,
        "src/index.ts",
        "run",
        "--task",
        str(task_json),
        "--mode",
        "unattended",
    ]
    if case_runtime_module is not None:
        command.extend(["--runtime-module", str(case_runtime_module)])
    if case_dry_run:
        command.append("--dry-run")
    return subprocess.run(
        command,
        cwd=case_checkout,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_case_command_result(
    run_dir: Path,
    result: subprocess.CompletedProcess[str],
    interpreted_returncode: int | None = None,
    redactions: list[str] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "case-stdout.txt").write_text(
        redact_text(result.stdout, redactions),
        encoding="utf-8",
    )
    (run_dir / "case-stderr.txt").write_text(
        redact_text(result.stderr, redactions),
        encoding="utf-8",
    )
    (run_dir / "case-result.json").write_text(
        json.dumps(
            {
                "exit_code": result.returncode,
                "interpreted_exit_code": (
                    result.returncode
                    if interpreted_returncode is None
                    else interpreted_returncode
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def redact_text(text: str, redactions: list[str] | None) -> str:
    if not redactions:
        return text
    redacted = text
    for secret in redactions:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def preserve_native_dry_run_task(
    *,
    run_dir: Path,
    task_json: Path,
    generated_task_json: str,
) -> None:
    native_task_json = task_json.read_text(encoding="utf-8")
    if native_task_json != generated_task_json:
        (run_dir / "native-dry-run-task.json").write_text(
            native_task_json,
            encoding="utf-8",
        )
        task_json.write_text(generated_task_json, encoding="utf-8")


def interpreted_case_returncode(result: subprocess.CompletedProcess[str]) -> int:
    if result.returncode != 0:
        return result.returncode
    output = f"{result.stdout}\n{result.stderr}"
    if "Pipeline failed at " in output:
        return 1
    if '"msg":"pipeline finished"' in output and '"outcome":"failed"' in output:
        return 1
    return 0


def make_run_id(bead_id: str) -> str:
    safe_bead_id = bead_id.replace("/", "-")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run-bead-{safe_bead_id}-{timestamp}"
