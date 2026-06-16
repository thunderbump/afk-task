from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from automation_simple_spike.cli import write_case_task, write_execution_request
from automation_simple_spike.workstream_context import build_workstream_context


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


def runnable_metadata(target_repo: Path) -> dict[str, object]:
    return {
        "afk_enabled": True,
        "afk_runner": "codex",
        "target_repo": "local/central-3gj-context",
        "target_repo_path": str(target_repo),
        "target_base_branch": "main",
        "branch_policy": "shared-sequential",
        "validation_command": (
            "python3 -m unittest discover -s tests && scripts/smoke.sh"
        ),
        "light_verification_command": (
            "python3 -m unittest tests/test_workstream_context.py"
        ),
        "workstream_id": "workstream-validation",
    }


class WorkstreamContextTest(unittest.TestCase):
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
        self.git(path, "config", "user.email", "context@example.com")
        self.git(path, "config", "user.name", "Context Fixture")
        (path / "README.md").write_text("fixture target\n", encoding="utf-8")
        self.git(path, "add", "README.md")
        self.git(path, "commit", "-m", "Initial context target")
        self.git(path, "branch", "-M", "main")

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
                    "}) + '\\n', encoding='utf-8')",
                ]
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)

    def test_case_task_markdown_includes_central_3gj_style_workstream_context(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            target_repo = Path(tmp)
            metadata = {
                **runnable_metadata(target_repo),
                "likely_files": [
                    "automation_simple_spike/cli.py",
                    "tests/test_workstream_context.py",
                ],
                "environment_gates": [
                    "Stop before live Case/Codex runs without a human gate."
                ],
            }
            parent = {
                "id": "central-3gj",
                "title": "PRD: Separate validation and gameplay flows",
                "description": "Parent planning record; not executable agent work.",
                "status": "open",
                "labels": ["project:bump-eqemu", "type:prd"],
                "metadata": {"kind": "prd"},
            }
            prerequisite = {
                "id": "central-3gj.1",
                "title": "Finished prerequisite",
                "status": "closed",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(target_repo),
                "parent": "central-3gj",
            }
            current = {
                "id": "central-3gj.2",
                "title": "Ready child",
                "description": "Implement the next runnable child.",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": metadata,
                "parent": "central-3gj",
                "dependencies": [
                    {
                        "issue_id": "central-3gj.2",
                        "depends_on_id": "central-3gj.1",
                        "type": "blocks",
                        "status": "closed",
                    }
                ],
            }
            blocked_sibling = {
                "id": "central-3gj.3",
                "title": "Next child",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(target_repo),
                "parent": "central-3gj",
                "dependencies": [
                    {
                        "issue_id": "central-3gj.3",
                        "depends_on_id": "central-3gj.2",
                        "type": "blocks",
                        "status": "open",
                    }
                ],
            }
            metadata_gap = {
                "id": "central-3gj.4",
                "title": "Label-only child",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "parent": "central-3gj",
            }

            context = build_workstream_context(
                current_issue=current,
                issues=[parent, prerequisite, current, blocked_sibling, metadata_gap],
            )

            task_md, _task_json = write_case_task(
                issue=current,
                target_repo=target_repo,
                review_branch="agent/workstream-validation",
                workstream_context=context,
            )

            markdown = task_md.read_text(encoding="utf-8")
            self.assertIn("## Workstream Context", markdown)
            self.assertIn(
                "Parent: central-3gj - PRD: Separate validation and gameplay flows",
                markdown,
            )
            self.assertIn("Workstream: workstream-validation", markdown)
            self.assertIn("central-3gj.1 (closed) -> central-3gj.2 (current)", markdown)
            self.assertIn("central-3gj.3: blocked by central-3gj.2", markdown)
            self.assertIn("central-3gj.4: not ready", markdown)
            self.assertIn("automation_simple_spike/cli.py", markdown)
            self.assertIn("tests/test_workstream_context.py", markdown)
            self.assertIn(
                "Light: `python3 -m unittest tests/test_workstream_context.py`",
                markdown,
            )
            self.assertIn(
                "Final: `python3 -m unittest discover -s tests && scripts/smoke.sh`",
                markdown,
            )
            self.assertIn("## Environment Gates", markdown)
            self.assertIn(
                "Stop if the target checkout has uncommitted changes",
                markdown,
            )
            self.assertIn(
                "Stop before live Case/Codex runs without a human gate.",
                markdown,
            )

    def test_secret_shaped_values_are_not_written_to_task_or_request_artifacts(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            state_dir = tmp_path / ".automation-simple"
            secret_value = "fixture-value-must-be-redacted"
            metadata = {
                **runnable_metadata(target_repo),
                "validation_command": f"python3 -m unittest token={secret_value}",
                "environment_gates": [
                    f"Stop if password={secret_value} would be required."
                ],
                "api_token": secret_value,
            }
            parent = {
                "id": "central-3gj",
                "title": "PRD: Separate validation and gameplay flows",
                "description": f"Parent summary token: {secret_value}",
                "status": "open",
                "labels": ["project:bump-eqemu", "type:prd"],
            }
            current = {
                "id": "central-3gj.2",
                "title": "Ready child",
                "description": f"Implement without persisting api_token={secret_value}.",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": metadata,
                "parent": "central-3gj",
            }
            context = build_workstream_context(
                current_issue=current,
                issues=[parent, current],
            )

            task_md, task_json = write_case_task(
                issue=current,
                target_repo=target_repo,
                review_branch="agent/workstream-validation",
                workstream_context=context,
            )
            request_path = write_execution_request(
                state_dir=state_dir,
                issue=current,
                task_md=task_md,
                task_json=task_json,
                case_checkout=tmp_path / "workos-case",
                case_data_dir=state_dir / "case-data",
                case_cli_shim=state_dir / "case-bin" / "ca",
                review_branch="agent/workstream-validation",
                case_dry_run=True,
                case_runtime_module=None,
            )

            task_markdown = task_md.read_text(encoding="utf-8")
            generated_artifacts = "\n".join(
                [
                    task_markdown,
                    task_json.read_text(encoding="utf-8"),
                    request_path.read_text(encoding="utf-8"),
                ]
            )
            self.assertNotIn(secret_value, generated_artifacts)
            self.assertIn("[REDACTED]", task_markdown)

    def test_run_command_can_project_workstream_context_from_fixture_json(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = tmp_path / "fake-case"
            self.write_fake_case(fake_case)
            metadata = {
                **runnable_metadata(target_repo),
                "likely_files": ["automation_simple_spike/cli.py"],
            }
            parent = {
                "id": "central-3gj",
                "title": "PRD: Separate validation and gameplay flows",
                "description": "Parent planning record.",
                "status": "open",
                "labels": ["project:bump-eqemu", "type:prd"],
            }
            prerequisite = {
                "id": "central-3gj.1",
                "title": "Finished prerequisite",
                "status": "closed",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(target_repo),
                "parent": "central-3gj",
            }
            current = {
                "id": "central-3gj.2",
                "title": "Ready child",
                "description": "Implement the next runnable child.",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": metadata,
                "parent": "central-3gj",
                "dependencies": [
                    {
                        "issue_id": "central-3gj.2",
                        "depends_on_id": "central-3gj.1",
                        "type": "blocks",
                        "status": "closed",
                    }
                ],
            }
            blocked_sibling = {
                "id": "central-3gj.3",
                "title": "Blocked sibling",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(target_repo),
                "parent": "central-3gj",
                "dependencies": [
                    {
                        "issue_id": "central-3gj.3",
                        "depends_on_id": "central-3gj.2",
                        "type": "blocks",
                        "status": "open",
                    }
                ],
            }
            bead_json = tmp_path / "bead.json"
            bead_json.write_text(json.dumps(current), encoding="utf-8")
            context_json = tmp_path / "workstream-context.json"
            context_json.write_text(
                json.dumps([parent, prerequisite, current, blocked_sibling]),
                encoding="utf-8",
            )

            result = run_cli(
                "run",
                "--bead",
                "central-3gj.2",
                "--bead-json",
                str(bead_json),
                "--workstream-context-json",
                str(context_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--case-dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            task_markdown = (
                target_repo / ".case" / "tasks" / "active" / "central-3gj.2.md"
            ).read_text(encoding="utf-8")
            self.assertIn("## Workstream Context", task_markdown)
            self.assertIn(
                "Parent: central-3gj - PRD: Separate validation and gameplay flows",
                task_markdown,
            )
            self.assertIn(
                "central-3gj.1 (closed) -> central-3gj.2 (current)",
                task_markdown,
            )
            self.assertIn("central-3gj.3: blocked by central-3gj.2", task_markdown)
