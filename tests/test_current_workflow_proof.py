from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest


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


class CurrentWorkflowProofTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def init_target_repo(self, path: Path) -> None:
        path.mkdir()
        self.git(path, "init")
        self.git(path, "config", "user.email", "proof@example.com")
        self.git(path, "config", "user.name", "Proof Fixture")
        (path / "README.md").write_text("central-3gj style target\n", encoding="utf-8")
        self.git(path, "add", "README.md")
        self.git(path, "commit", "-m", "Initial proof target")
        self.git(path, "branch", "-M", "main")

    def write_bead(self, path: Path, bead: dict[str, Any]) -> None:
        path.write_text(json.dumps(bead), encoding="utf-8")

    def write_fake_case(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, sys",
                    "from pathlib import Path",
                    "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                    "  'argv': sys.argv[1:],",
                    "  'cwd': os.getcwd(),",
                    "  'case_data_dir': os.environ.get('CASE_DATA_DIR'),",
                    "}) + '\\n', encoding='utf-8')",
                ]
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)

    def afk_metadata(self, target_repo: Path) -> dict[str, Any]:
        return {
            "afk_enabled": True,
            "afk_runner": "codex",
            "target_repo": "local/central-3gj-proof",
            "target_repo_path": str(target_repo),
            "target_base_branch": "main",
            "branch_policy": "independent",
            "validation_command": "python3 -m unittest discover -s tests",
        }

    def central_3gj_style_records(self, target_repo: Path) -> dict[str, dict[str, Any]]:
        runnable_metadata = self.afk_metadata(target_repo)
        child_without_afk = {
            key: value
            for key, value in runnable_metadata.items()
            if key not in {"afk_enabled", "afk_runner"}
        }

        return {
            "parent_prd": {
                "id": "central-3gj",
                "title": "PRD: prove the current AFK workflow",
                "description": "Parent planning record; not executable agent work.",
                "status": "open",
                "labels": ["project:automation", "prd"],
                "metadata": {"kind": "prd"},
            },
            "child_without_afk": {
                "id": "central-3gj.1",
                "title": "Child without AFK metadata",
                "description": "Ready-looking child missing the AFK runner contract.",
                "status": "open",
                "labels": ["project:automation", "ready-for-agent"],
                "metadata": child_without_afk,
            },
            "child_with_active_run": {
                "id": "central-3gj.2",
                "title": "Child already claimed by a runner",
                "description": "Complete AFK metadata but not currently selectable.",
                "status": "open",
                "labels": ["project:automation", "ready-for-agent"],
                "metadata": {
                    **runnable_metadata,
                    "active_run_id": "run-central-3gj.2",
                },
            },
            "runnable_child": {
                "id": "central-3gj.3",
                "title": "Runnable child reaches Case dry-run",
                "description": "Complete AFK metadata and no workflow blockers.",
                "status": "open",
                "labels": ["project:automation", "ready-for-agent"],
                "metadata": runnable_metadata,
            },
            "child_blocked_by_parent": {
                "id": "central-3gj.4",
                "title": "Child blocked by an open dependency",
                "description": "Complete AFK metadata but blocked by the parent PRD.",
                "status": "open",
                "labels": ["project:automation", "ready-for-agent"],
                "metadata": runnable_metadata,
                "dependencies": [
                    {
                        "id": "central-3gj",
                        "dependency_type": "blocks",
                        "status": "open",
                    }
                ],
            },
        }

    def test_central_3gj_style_fixture_only_hands_off_runnable_child(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = tmp_path / "fake-case"
            self.write_fake_case(fake_case)
            records = self.central_3gj_style_records(target_repo)

            non_runnable_cases = [
                ("parent_prd", "central-3gj", "missing label ready-for-agent"),
                (
                    "child_without_afk",
                    "central-3gj.1",
                    "missing metadata afk_enabled",
                ),
                (
                    "child_with_active_run",
                    "central-3gj.2",
                    "conflicting run active_run_id=run-central-3gj.2",
                ),
                (
                    "child_blocked_by_parent",
                    "central-3gj.4",
                    "open blocking dependency central-3gj",
                ),
            ]

            for fixture_name, bead_id, expected_reason in non_runnable_cases:
                with self.subTest(bead_id=bead_id):
                    bead_json = tmp_path / f"{fixture_name}.json"
                    self.write_bead(bead_json, records[fixture_name])
                    state_dir = tmp_path / f".automation-simple-{fixture_name}"

                    result = run_cli(
                        "run",
                        "--bead",
                        bead_id,
                        "--bead-json",
                        str(bead_json),
                        "--state-dir",
                        str(state_dir),
                        "--case-checkout",
                        str(case_checkout),
                        "--case-command",
                        str(fake_case),
                        "--case-dry-run",
                    )

                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(expected_reason, result.stderr)
                    self.assertFalse(fake_case.with_suffix(".json").exists())
                    self.assertFalse(
                        (
                            target_repo
                            / ".case"
                            / "tasks"
                            / "active"
                            / f"{bead_id}.task.json"
                        ).exists()
                    )

            runnable_bead_json = tmp_path / "runnable-child.json"
            self.write_bead(runnable_bead_json, records["runnable_child"])
            state_dir = tmp_path / ".automation-simple-runnable"

            result = run_cli(
                "run",
                "--bead",
                "central-3gj.3",
                "--bead-json",
                str(runnable_bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--case-dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("run-bead handed off: central-3gj.3", result.stdout)
            case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            task_json = (
                target_repo / ".case" / "tasks" / "active" / "central-3gj.3.task.json"
            )
            self.assertEqual(
                case_record["argv"],
                [
                    "src/index.ts",
                    "run",
                    "--task",
                    str(task_json),
                    "--mode",
                    "unattended",
                    "--dry-run",
                ],
            )
            self.assertEqual(case_record["cwd"], str(case_checkout))
            self.assertEqual(
                case_record["case_data_dir"], str(state_dir / "case-data")
            )
            self.assertEqual(
                sorted(
                    path.name
                    for path in (target_repo / ".case" / "tasks" / "active").glob(
                        "*.task.json"
                    )
                ),
                ["central-3gj.3.task.json"],
            )
            generated_task = json.loads(task_json.read_text(encoding="utf-8"))
            self.assertEqual(generated_task["id"], "central-3gj.3")
            self.assertEqual(generated_task["branch"], "agent/central-3gj.3")
            self.assertEqual(
                generated_task["checkCommand"],
                "python3 -m unittest discover -s tests",
            )
            execution_request = json.loads(
                next(state_dir.glob("runs/*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(execution_request["bead_id"], "central-3gj.3")
            self.assertTrue(execution_request["case_dry_run"])

    def test_dirty_checkout_is_currently_rejected_before_case_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            (target_repo / "dirty.txt").write_text("uncommitted work\n", encoding="utf-8")
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = tmp_path / "fake-case"
            self.write_fake_case(fake_case)
            bead_json = tmp_path / "runnable-child.json"
            self.write_bead(
                bead_json,
                self.central_3gj_style_records(target_repo)["runnable_child"],
            )

            result = run_cli(
                "run",
                "--bead",
                "central-3gj.3",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--case-dry-run",
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("target repo has uncommitted changes", result.stderr)
            self.assertFalse(fake_case.with_suffix(".json").exists())
            self.assertFalse((target_repo / ".case").exists())
