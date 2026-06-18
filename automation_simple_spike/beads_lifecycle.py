from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .beads_env import beads_subprocess_env


ACTIVE_RUN_METADATA_KEYS = [
    "active_run_id",
    "active_run_branch",
    "active_run_checkout_mode",
    "active_run_archive_path",
    "active_run_target_checkout",
    "active_run_source_checkout",
    "active_run_worktree_checkout",
]


class BeadsLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True)
class LifecycleRun:
    bead_id: str
    run_id: str
    review_branch: str
    target_checkout_mode: str
    target_checkout_path: Path
    target_source_checkout: Path
    target_worktree_checkout: Path | None
    archive_path: Path


@dataclass(frozen=True)
class BeadsLifecycleClient:
    bd_command: str
    beads_workspace: Path
    beads_password_file: Path

    def record_start(self, run: LifecycleRun) -> None:
        metadata = [
            ("active_run_id", run.run_id),
            ("active_run_branch", run.review_branch),
            ("active_run_checkout_mode", run.target_checkout_mode),
            ("active_run_archive_path", str(run.archive_path)),
            ("active_run_target_checkout", str(run.target_checkout_path)),
            ("active_run_source_checkout", str(run.target_source_checkout)),
        ]
        if run.target_worktree_checkout is not None:
            metadata.append(
                ("active_run_worktree_checkout", str(run.target_worktree_checkout))
            )

        args = ["update", run.bead_id]
        for key, value in metadata:
            args.extend(["--set-metadata", f"{key}={value}"])
        self.run_bd(args)

    def record_success(
        self,
        run: LifecycleRun,
        *,
        commit_sha: str,
        interpreted_exit_code: int,
        close_bead: bool,
    ) -> None:
        args = ["update", run.bead_id]
        for key, value in [
            ("last_afk_run_id", run.run_id),
            ("last_afk_run_result", "success"),
            ("last_afk_run_exit_code", str(interpreted_exit_code)),
            ("last_afk_run_commit", commit_sha),
            ("last_afk_run_branch", run.review_branch),
            ("last_afk_run_archive_path", str(run.archive_path)),
        ]:
            args.extend(["--set-metadata", f"{key}={value}"])
        for key in ACTIVE_RUN_METADATA_KEYS:
            args.extend(["--unset-metadata", key])
        self.run_bd(args)

        if close_bead:
            self.run_bd(
                [
                    "close",
                    run.bead_id,
                    "--reason",
                    f"AFK run {run.run_id} succeeded at commit {commit_sha}",
                ]
            )

    def record_failure(
        self,
        run: LifecycleRun,
        *,
        interpreted_exit_code: int,
        failure_summary: str,
        next_action: str | None = None,
    ) -> None:
        if next_action is None:
            next_action = (
                "inspect case-stdout.txt and case-stderr.txt in the archive, fix "
                "the failure, then rerun when ready."
            )
        comment = "\n".join(
            [
                "AFK run failed.",
                "",
                f"Run: {run.run_id}",
                f"Branch: {run.review_branch}",
                f"Archive: {run.archive_path}",
                f"Exit code: {interpreted_exit_code}",
                f"Failure: {failure_summary}",
                "",
                f"Next action: {next_action}",
                "",
            ]
        )
        self.run_bd(["comment", run.bead_id, "--stdin"], input_text=comment)

        args = ["update", run.bead_id]
        for key, value in [
            ("last_afk_run_id", run.run_id),
            ("last_afk_run_result", "failure"),
            ("last_afk_run_exit_code", str(interpreted_exit_code)),
            ("last_afk_run_branch", run.review_branch),
            ("last_afk_run_archive_path", str(run.archive_path)),
        ]:
            args.extend(["--set-metadata", f"{key}={value}"])
        for key in ACTIVE_RUN_METADATA_KEYS:
            args.extend(["--unset-metadata", key])
        self.run_bd(args)

    def run_bd(self, args: list[str], *, input_text: str | None = None) -> None:
        try:
            result = subprocess.run(
                [self.bd_command, *args],
                cwd=self.beads_workspace,
                env=beads_subprocess_env(self.beads_password_file),
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as error:
            raise BeadsLifecycleError(f"missing bd command: {self.bd_command}") from error
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            if not message:
                message = f"bd {' '.join(args)} exited {result.returncode}"
            raise BeadsLifecycleError(message)
