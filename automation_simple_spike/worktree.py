from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeProvisioningError(ValueError):
    pass


@dataclass(frozen=True)
class ProvisionedTargetWorktree:
    source_checkout: Path
    worktree_checkout: Path
    base_branch: str
    review_branch: str
    start_ref: str
    start_commit: str


@dataclass(frozen=True)
class ResolvedStartRef:
    ref: str
    commit: str


def provision_target_worktree(
    *,
    source_checkout: Path,
    worktree_root: Path,
    base_branch: str,
    review_branch: str,
    start_ref: str | None = None,
) -> ProvisionedTargetWorktree:
    source_checkout = source_checkout.resolve()
    worktree_root = worktree_root.resolve()
    source_checkout = require_git_toplevel(source_checkout)
    require_local_branch(source_checkout, base_branch)
    if start_ref is None:
        resolved_start_ref = ResolvedStartRef(
            ref=base_branch,
            commit=require_commit_ref(
                source_checkout,
                base_branch,
                label="target base branch",
            ),
        )
    else:
        resolved_start_ref = resolve_start_ref(
            source_checkout,
            start_ref,
            label="workstream seed ref",
        )

    worktree_checkout = worktree_path_for(
        source_checkout=source_checkout,
        worktree_root=worktree_root,
        review_branch=review_branch,
    )
    create_or_reuse_worktree(
        source_checkout=source_checkout,
        worktree_checkout=worktree_checkout,
        start_ref=resolved_start_ref.ref,
        review_branch=review_branch,
    )

    return ProvisionedTargetWorktree(
        source_checkout=source_checkout,
        worktree_checkout=worktree_checkout,
        base_branch=base_branch,
        review_branch=review_branch,
        start_ref=resolved_start_ref.ref,
        start_commit=resolved_start_ref.commit,
    )


def require_git_toplevel(checkout: Path) -> Path:
    result = run_git(checkout, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise WorktreeProvisioningError(f"{checkout} is not a git repository")
    return Path(result.stdout.strip()).resolve()


def require_local_branch(checkout: Path, branch: str) -> None:
    result = run_git(
        checkout,
        "rev-parse",
        "--verify",
        f"refs/heads/{branch}^{{commit}}",
    )
    if result.returncode != 0:
        raise WorktreeProvisioningError(
            f"target base branch does not exist locally: {branch}"
        )


def require_commit_ref(checkout: Path, ref: str, *, label: str) -> str:
    result = run_git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        raise WorktreeProvisioningError(
            f"{label} does not resolve to a commit: {ref}"
        )
    return result.stdout.strip()


def resolve_start_ref(checkout: Path, ref: str, *, label: str) -> ResolvedStartRef:
    pr_number = github_pr_number(ref)
    if pr_number is not None:
        fetched_ref = f"refs/automation-simple/workstream-seeds/pr-{pr_number}"
        result = run_git(
            checkout,
            "fetch",
            "origin",
            f"+refs/pull/{pr_number}/head:{fetched_ref}",
        )
        if result.returncode != 0:
            raise WorktreeProvisioningError(
                f"could not fetch {label} PR {pr_number} from origin: "
                f"{git_error_message(result)}"
            )
        return ResolvedStartRef(
            ref=fetched_ref,
            commit=require_commit_ref(checkout, fetched_ref, label=label),
        )

    return ResolvedStartRef(
        ref=ref,
        commit=require_commit_ref(checkout, ref, label=label),
    )


def github_pr_number(ref: str) -> str | None:
    match = re.match(
        r"^https://github\.com/[^/]+/[^/]+/pull/([0-9]+)(?:[/?#].*)?$",
        ref,
    )
    if match is None:
        return None
    return match.group(1)


def worktree_path_for(
    *,
    source_checkout: Path,
    worktree_root: Path,
    review_branch: str,
) -> Path:
    repo_hash = hashlib.sha256(str(source_checkout).encode("utf-8")).hexdigest()[:12]
    repo_name = sanitize_path_segment(source_checkout.name) or "repo"
    branch_name = sanitize_path_segment(review_branch) or "review"
    return worktree_root / f"{repo_name}-{repo_hash}" / branch_name


def sanitize_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")


def create_or_reuse_worktree(
    *,
    source_checkout: Path,
    worktree_checkout: Path,
    start_ref: str,
    review_branch: str,
) -> None:
    if worktree_checkout.exists() and any(worktree_checkout.iterdir()):
        reset_existing_worktree(
            source_checkout=source_checkout,
            worktree_checkout=worktree_checkout,
            start_ref=start_ref,
            review_branch=review_branch,
        )
        return

    worktree_checkout.parent.mkdir(parents=True, exist_ok=True)
    result = run_git(
        source_checkout,
        "worktree",
        "add",
        "-B",
        review_branch,
        str(worktree_checkout),
        start_ref,
    )
    if result.returncode != 0:
        raise WorktreeProvisioningError(
            f"could not create target worktree {worktree_checkout}: "
            f"{git_error_message(result)}"
        )


def reset_existing_worktree(
    *,
    source_checkout: Path,
    worktree_checkout: Path,
    start_ref: str,
    review_branch: str,
) -> None:
    if not worktree_checkout.is_dir():
        raise WorktreeProvisioningError(
            f"target worktree path exists but is not a directory: {worktree_checkout}"
        )

    existing_toplevel = require_git_toplevel(worktree_checkout)
    if existing_toplevel != worktree_checkout:
        raise WorktreeProvisioningError(
            f"target worktree path is inside a different checkout: {worktree_checkout}"
        )
    if git_common_dir(source_checkout) != git_common_dir(worktree_checkout):
        raise WorktreeProvisioningError(
            f"target worktree does not belong to source checkout: {worktree_checkout}"
        )
    require_clean_worktree(worktree_checkout)

    checkout = run_git(
        worktree_checkout,
        "checkout",
        "-B",
        review_branch,
        start_ref,
    )
    if checkout.returncode != 0:
        raise WorktreeProvisioningError(
            f"could not reset review branch {review_branch} to {start_ref}: "
            f"{git_error_message(checkout)}"
        )
    require_clean_worktree(worktree_checkout)


def git_common_dir(checkout: Path) -> Path:
    result = run_git(checkout, "rev-parse", "--git-common-dir")
    if result.returncode != 0:
        raise WorktreeProvisioningError(f"{checkout} is not a git repository")
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = checkout / common_dir
    return common_dir.resolve()


def require_clean_worktree(checkout: Path) -> None:
    status = run_git(checkout, "status", "--porcelain")
    if status.returncode != 0:
        raise WorktreeProvisioningError(
            f"could not inspect git status for {checkout}: {git_error_message(status)}"
        )
    if status.stdout.strip():
        raise WorktreeProvisioningError(
            "target worktree has uncommitted changes; commit, stash, or clean them first"
        )


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


def git_error_message(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout).strip() or f"git exited {result.returncode}"
