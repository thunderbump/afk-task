from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from automation_simple_spike.worktree import (
    WorktreeProvisioningError,
    provision_target_worktree,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "automation_simple_workflow", *args],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class WorktreeProvisioningTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def init_source_repo(self, path: Path) -> str:
        path.mkdir()
        self.git(path, "init")
        self.git(path, "config", "user.email", "test@example.com")
        self.git(path, "config", "user.name", "Test User")
        (path / "README.md").write_text("fixture target\n", encoding="utf-8")
        self.git(path, "add", "README.md")
        self.git(path, "commit", "-m", "Initial target commit")
        self.git(path, "branch", "-M", "main")
        return self.git(path, "rev-parse", "main").stdout.strip()

    def write_eligible_bead(
        self,
        path: Path,
        target_repo: Path,
        bead_id: str,
        target_base_branch: str = "main",
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "id": bead_id,
                    "title": "Provision worktree",
                    "description": "Use an isolated target worktree.",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": {
                        "afk_enabled": True,
                        "afk_runner": "codex",
                        "target_repo": "local/test",
                        "target_repo_path": str(target_repo),
                        "target_base_branch": target_base_branch,
                        "branch_policy": "independent",
                        "validation_command": "true",
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_dirty_source_checkout_does_not_block_clean_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_checkout = tmp_path / "source"
            base_sha = self.init_source_repo(source_checkout)
            (source_checkout / "dirty.txt").write_text(
                "unsaved work\n", encoding="utf-8"
            )

            target = provision_target_worktree(
                source_checkout=source_checkout,
                worktree_root=tmp_path / "worktrees",
                base_branch="main",
                review_branch="agent/central-dirty",
            )

            self.assertEqual(target.source_checkout, source_checkout)
            self.assertTrue(target.worktree_checkout.is_dir())
            self.assertEqual(target.base_branch, "main")
            self.assertEqual(target.review_branch, "agent/central-dirty")
            self.assertEqual(
                self.git(
                    target.worktree_checkout, "branch", "--show-current"
                ).stdout.strip(),
                "agent/central-dirty",
            )
            self.assertEqual(
                self.git(target.worktree_checkout, "rev-parse", "HEAD").stdout.strip(),
                base_sha,
            )
            self.assertEqual(
                self.git(target.worktree_checkout, "status", "--porcelain").stdout,
                "",
            )
            self.assertTrue((source_checkout / "dirty.txt").is_file())

    def test_existing_clean_worktree_is_reused_and_reset_to_base(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_checkout = tmp_path / "source"
            base_sha = self.init_source_repo(source_checkout)
            worktree_root = tmp_path / "worktrees"
            first_target = provision_target_worktree(
                source_checkout=source_checkout,
                worktree_root=worktree_root,
                base_branch="main",
                review_branch="agent/central-reuse",
            )
            (first_target.worktree_checkout / "stale.txt").write_text(
                "old run\n", encoding="utf-8"
            )
            self.git(first_target.worktree_checkout, "add", "stale.txt")
            self.git(first_target.worktree_checkout, "commit", "-m", "Stale run")
            stale_sha = self.git(
                first_target.worktree_checkout, "rev-parse", "HEAD"
            ).stdout.strip()

            second_target = provision_target_worktree(
                source_checkout=source_checkout,
                worktree_root=worktree_root,
                base_branch="main",
                review_branch="agent/central-reuse",
            )

            self.assertEqual(
                second_target.worktree_checkout, first_target.worktree_checkout
            )
            self.assertEqual(
                self.git(
                    second_target.worktree_checkout, "branch", "--show-current"
                ).stdout.strip(),
                "agent/central-reuse",
            )
            self.assertEqual(
                self.git(
                    second_target.worktree_checkout, "rev-parse", "HEAD"
                ).stdout.strip(),
                base_sha,
            )
            self.assertNotIn(
                stale_sha,
                self.git(
                    second_target.worktree_checkout,
                    "log",
                    "--format=%H",
                    "main..agent/central-reuse",
                )
                .stdout
                .splitlines(),
            )

    def test_missing_base_branch_fails_before_creating_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_checkout = tmp_path / "source"
            self.init_source_repo(source_checkout)
            worktree_root = tmp_path / "worktrees"

            with self.assertRaisesRegex(
                WorktreeProvisioningError,
                "target base branch does not exist locally: missing",
            ):
                provision_target_worktree(
                    source_checkout=source_checkout,
                    worktree_root=worktree_root,
                    base_branch="missing",
                    review_branch="agent/central-missing-base",
                )

            self.assertFalse(worktree_root.exists())

    def test_cli_worktree_mode_uses_worktree_and_records_checkout_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_checkout = tmp_path / "source"
            self.init_source_repo(source_checkout)
            (source_checkout / "dirty.txt").write_text(
                "unsaved work\n", encoding="utf-8"
            )
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'cwd': os.getcwd(),",
                        "}) + '\\n', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bead_json = tmp_path / "bead.json"
            self.write_eligible_bead(bead_json, source_checkout, "central-worktree")

            result = run_cli(
                "run",
                "--bead",
                "central-worktree",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--target-checkout-mode",
                "worktree",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            execution_request = json.loads(
                next((state_dir / "runs").glob("*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            worktree_checkout = Path(execution_request["target_worktree_checkout"])
            task_json_path = (
                worktree_checkout
                / ".case"
                / "tasks"
                / "active"
                / "central-worktree.task.json"
            )
            self.assertEqual(execution_request["target_checkout_mode"], "worktree")
            self.assertEqual(
                execution_request["target_source_checkout"], str(source_checkout)
            )
            self.assertEqual(
                execution_request["target_checkout_path"], str(worktree_checkout)
            )
            self.assertEqual(execution_request["target_base_branch"], "main")
            self.assertEqual(execution_request["review_branch"], "agent/central-worktree")
            self.assertTrue(worktree_checkout.is_dir())
            self.assertNotEqual(worktree_checkout, source_checkout)
            self.assertTrue(task_json_path.is_file())
            self.assertFalse((source_checkout / ".case").exists())

            project_manifest = json.loads(
                (state_dir / "case-data" / "projects.json").read_text(encoding="utf-8")
            )
            self.assertEqual(project_manifest["repos"][0]["path"], str(worktree_checkout))
            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                fake_case_record["argv"],
                [
                    "src/index.ts",
                    "run",
                    "--task",
                    str(task_json_path),
                    "--mode",
                    "unattended",
                ],
            )
            self.assertTrue((source_checkout / "dirty.txt").is_file())
