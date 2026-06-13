from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REQUIRED_METADATA = [
    "afk_enabled",
    "afk_runner",
    "target_repo",
    "target_repo_path",
    "target_base_branch",
    "branch_policy",
    "validation_command",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="automation-simple-spike")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--bead", required=True)
    run.add_argument("--bead-json")
    run.add_argument("--state-dir", default=".automation-simple")
    run.add_argument(
        "--case-checkout",
        default="/home/bump/Projects/automation/.automation/cache/workos-case",
    )
    run.add_argument("--case-data-dir")
    run.add_argument("--case-command", default="bun")
    run.add_argument("--bd-command", default="bd")
    run.add_argument("--beads-workspace", default="/home/bump/Projects/beads")
    run.add_argument(
        "--beads-password-file",
        default="/home/bump/Projects/beads/secrets/dolt_beads_password.txt",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return run_bead(
            bead_id=args.bead,
            bead_json=Path(args.bead_json) if args.bead_json else None,
            state_dir=Path(args.state_dir),
            case_checkout=Path(args.case_checkout),
            case_data_dir=Path(args.case_data_dir) if args.case_data_dir else None,
            case_command=args.case_command,
            bd_command=args.bd_command,
            beads_workspace=Path(args.beads_workspace),
            beads_password_file=Path(args.beads_password_file),
        )
    parser.error(f"unknown command: {args.command}")
    return 2


def run_bead(
    *,
    bead_id: str,
    bead_json: Path | None,
    state_dir: Path,
    case_checkout: Path,
    case_data_dir: Path | None,
    case_command: str,
    bd_command: str,
    beads_workspace: Path,
    beads_password_file: Path,
) -> int:
    state_dir = state_dir.resolve()
    case_checkout = case_checkout.resolve()
    if case_data_dir is not None:
        case_data_dir = case_data_dir.resolve()
    issue = load_issue(
        bead_id=bead_id,
        bead_json=bead_json,
        bd_command=bd_command,
        beads_workspace=beads_workspace,
        beads_password_file=beads_password_file,
    )
    reasons = eligibility_rejections(issue)
    if reasons:
        print(f"run-bead ineligible: {bead_id}: {'; '.join(reasons)}", file=sys.stderr)
        return 1

    metadata = issue["metadata"]
    target_repo = Path(str(metadata["target_repo_path"])).resolve()
    case_data = case_data_dir or state_dir / "case-data"
    review_branch = review_branch_for(bead_id=bead_id, metadata=metadata)
    task_md, task_json = write_case_task(
        issue=issue,
        target_repo=target_repo,
        review_branch=review_branch,
    )
    write_case_projects_manifest(
        case_data_dir=case_data,
        repo_name=str(metadata["target_repo"]),
        repo_path=target_repo,
        validation_command=str(metadata["validation_command"]),
    )
    request_path = write_execution_request(
        state_dir=state_dir,
        issue=issue,
        task_md=task_md,
        task_json=task_json,
        case_checkout=case_checkout,
        case_data_dir=case_data,
        review_branch=review_branch,
    )
    result = run_case_command(
        case_command=case_command,
        case_checkout=case_checkout,
        case_data_dir=case_data,
        task_json=task_json,
    )
    write_case_command_result(request_path.parent, result)
    if result.returncode != 0:
        print(
            f"run-bead failed: Case command exited {result.returncode}",
            file=sys.stderr,
        )
        return result.returncode

    print(f"run-bead handed off: {bead_id}")
    print(f"Case task JSON: {task_json}")
    print(f"Execution request: {request_path}")
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


def beads_subprocess_env(password_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("BEADS_DOLT_PASSWORD"):
        return env
    try:
        password = password_file.read_text(encoding="utf-8").rstrip("\n")
    except FileNotFoundError:
        return env
    if password:
        env["BEADS_DOLT_PASSWORD"] = password
    return env


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


def write_case_task(
    *,
    issue: dict[str, Any],
    target_repo: Path,
    review_branch: str,
) -> tuple[Path, Path]:
    metadata = issue["metadata"]
    bead_id = str(issue["id"])
    task_dir = target_repo / ".case" / "tasks" / "active"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_md = task_dir / f"{bead_id}.md"
    task_json = task_dir / f"{bead_id}.task.json"

    task_md.write_text(
        "\n".join(
            [
                f"# {issue.get('title') or bead_id}",
                "",
                f"- Bead: {bead_id}",
                f"- Target repo: {metadata['target_repo']}",
                f"- Target base branch: {metadata['target_base_branch']}",
                f"- Review branch: {review_branch}",
                f"- Validation command: {metadata['validation_command']}",
                "",
                "## Bead Description",
                "",
                str(issue.get("description") or "").strip()
                or "(No description provided.)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    task_json.write_text(
        json.dumps(
            {
                "agents": {},
                "branch": review_branch,
                "checkBaseline": None,
                "checkCommand": metadata["validation_command"],
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
) -> Path:
    case_data_dir.mkdir(parents=True, exist_ok=True)
    (case_data_dir / "home").mkdir(parents=True, exist_ok=True)
    (case_data_dir / "config-home").mkdir(parents=True, exist_ok=True)
    projects_path = case_data_dir / "projects.json"
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
    (case_data_dir / "config.json").write_text(
        json.dumps({"projects": "./projects.json"}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return projects_path


def write_execution_request(
    *,
    state_dir: Path,
    issue: dict[str, Any],
    task_md: Path,
    task_json: Path,
    case_checkout: Path,
    case_data_dir: Path,
    review_branch: str,
) -> Path:
    metadata = issue["metadata"]
    run_dir = state_dir / "runs" / make_run_id(str(issue["id"]))
    run_dir.mkdir(parents=True, exist_ok=True)
    request_path = run_dir / "execution-request.json"
    request_path.write_text(
        json.dumps(
            {
                "bead_id": issue["id"],
                "case_checkout": str(case_checkout),
                "case_data_dir": str(case_data_dir),
                "case_task_json": str(task_json),
                "case_task_markdown": str(task_md),
                "target_repo": metadata["target_repo"],
                "target_repo_path": metadata["target_repo_path"],
                "target_base_branch": metadata["target_base_branch"],
                "review_branch": review_branch,
                "validation_command": metadata["validation_command"],
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


def run_case_command(
    *,
    case_command: str,
    case_checkout: Path,
    case_data_dir: Path,
    task_json: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in ("BEADS_DIR", "BEADS_DOLT_PASSWORD", "AUTOMATION_BEADS_WORKSPACE"):
        env.pop(key, None)
    env["CASE_DATA_DIR"] = str(case_data_dir)
    env["XDG_CONFIG_HOME"] = str(case_data_dir / "config-home")
    env["HOME"] = str(case_data_dir / "home")
    return subprocess.run(
        [
            case_command,
            "src/index.ts",
            "run",
            "--task",
            str(task_json),
            "--mode",
            "unattended",
        ],
        cwd=case_checkout,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_case_command_result(
    run_dir: Path, result: subprocess.CompletedProcess[str]
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "case-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (run_dir / "case-stderr.txt").write_text(result.stderr, encoding="utf-8")
    (run_dir / "case-result.json").write_text(
        json.dumps({"exit_code": result.returncode}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_run_id(bead_id: str) -> str:
    safe_bead_id = bead_id.replace("/", "-")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run-bead-{safe_bead_id}-{timestamp}"
