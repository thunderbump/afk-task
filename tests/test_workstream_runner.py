from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
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


class WorkstreamRunnerTest(unittest.TestCase):
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
        self.git(path, "config", "user.email", "test@example.com")
        self.git(path, "config", "user.name", "Test User")
        (path / "README.md").write_text("fixture target\n", encoding="utf-8")
        self.git(path, "add", "README.md")
        self.git(path, "commit", "-m", "Initial target commit")
        self.git(path, "branch", "-M", "main")

    def runnable_metadata(
        self,
        target_repo: Path,
        *,
        light_command: str | None,
        validation_command: str,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "afk_enabled": True,
            "afk_runner": "codex",
            "target_repo": "local/workstream-target",
            "target_repo_path": str(target_repo),
            "target_base_branch": "main",
            "branch_policy": "shared-sequential",
            "validation_command": validation_command,
            "workstream_id": "central-3gj-fixture",
        }
        if light_command is not None:
            metadata["light_verification_command"] = light_command
        return metadata

    def test_runs_central_3gj_like_fixture_in_dependency_order_with_verification(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            state_dir = tmp_path / ".automation-simple"
            events_path = tmp_path / "events.jsonl"
            light_command = (
                f"{sys.executable} -c "
                "\"import json, os, pathlib; "
                "pathlib.Path(os.environ['EVENTS_PATH']).open('a').write("
                "json.dumps({'event':'light','cwd':os.getcwd()})+'\\n')\""
            )
            validation_command = (
                f"{sys.executable} -c "
                "\"import json, os, pathlib; "
                "assert pathlib.Path('central-3gj.2.txt').is_file(); "
                "assert pathlib.Path('central-3gj.3.txt').is_file(); "
                "pathlib.Path(os.environ['EVENTS_PATH']).open('a').write("
                "json.dumps({'event':'final','cwd':os.getcwd()})+'\\n')\""
            )
            metadata = self.runnable_metadata(
                target_repo,
                light_command=light_command,
                validation_command=validation_command,
            )
            workstream = [
                {
                    "id": "central-3gj",
                    "title": "PRD: Separate validation and gameplay flows",
                    "description": "Parent planning record.",
                    "status": "open",
                    "labels": ["project:automation", "type:prd"],
                },
                {
                    "id": "central-3gj.1",
                    "title": "Finished prerequisite",
                    "status": "closed",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": metadata,
                    "parent": "central-3gj",
                },
                {
                    "id": "central-3gj.2",
                    "title": "First runnable child",
                    "description": "Create first workstream output.",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
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
                },
                {
                    "id": "central-3gj.3",
                    "title": "Second runnable child after first closes",
                    "description": "Create second workstream output.",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": metadata,
                    "parent": "central-3gj",
                    "dependencies": [
                        {
                            "issue_id": "central-3gj.3",
                            "depends_on_id": "central-3gj.2",
                            "type": "blocks",
                        }
                    ],
                },
            ]
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(json.dumps(workstream), encoding="utf-8")
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, subprocess, sys",
                        "from pathlib import Path",
                        "task = Path(sys.argv[sys.argv.index('--task') + 1])",
                        "target = task.parents[3]",
                        "payload = json.loads(task.read_text(encoding='utf-8'))",
                        "bead_id = payload['id']",
                        (
                            "Path(os.environ['EVENTS_PATH']).open('a').write("
                            "json.dumps({'event':'case','bead':bead_id,"
                            "'cwd':str(target)}) + '\\n')"
                        ),
                        "output = target / f'{bead_id}.txt'",
                        "output.write_text(f'{bead_id}\\n', encoding='utf-8')",
                        (
                            "subprocess.run(['git', 'add', output.name], "
                            "cwd=target, check=True)"
                        ),
                        (
                            "subprocess.run(['git', 'commit', '-m', "
                            "f'Implement {bead_id}'], cwd=target, check=True, "
                            "stdout=subprocess.PIPE, stderr=subprocess.PIPE, "
                            "text=True)"
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_case.chmod(0o755)

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                env={"EVENTS_PATH": str(events_path)},
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("run-workstream completed: 2 bead(s)", result.stdout)
            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["event"] for event in events],
                ["case", "light", "case", "light", "final"],
            )
            self.assertEqual(
                [event.get("bead") for event in events if event["event"] == "case"],
                ["central-3gj.2", "central-3gj.3"],
            )
            worktree_paths = {
                event["cwd"]
                for event in events
                if event["event"] in {"case", "light", "final"}
            }
            self.assertEqual(len(worktree_paths), 1)
            worktree = Path(next(iter(worktree_paths)))
            self.assertNotEqual(worktree, target_repo)
            self.assertTrue((worktree / "central-3gj.2.txt").is_file())
            self.assertTrue((worktree / "central-3gj.3.txt").is_file())
            self.assertFalse((target_repo / "central-3gj.2.txt").exists())
            requests = sorted(state_dir.glob("runs/*/execution-request.json"))
            self.assertEqual(len(requests), 2)
            review_branches = {
                json.loads(path.read_text(encoding="utf-8"))["review_branch"]
                for path in requests
            }
            self.assertEqual(review_branches, {"agent/central-3gj-fixture"})

    def test_stops_on_case_failure_before_light_or_final_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            events_path = tmp_path / "events.jsonl"
            light_command = (
                f"{sys.executable} -c "
                "\"import os, pathlib; "
                "pathlib.Path(os.environ['EVENTS_PATH']).write_text('light\\n')\""
            )
            validation_command = (
                f"{sys.executable} -c "
                "\"import os, pathlib; "
                "pathlib.Path(os.environ['EVENTS_PATH']).write_text('final\\n')\""
            )
            workstream = [
                {
                    "id": "central-3gj.2",
                    "title": "Failing child",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": self.runnable_metadata(
                        target_repo,
                        light_command=light_command,
                        validation_command=validation_command,
                    ),
                    "parent": "central-3gj",
                }
            ]
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(json.dumps(workstream), encoding="utf-8")
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os",
                        "from pathlib import Path",
                        (
                            "Path(os.environ['EVENTS_PATH']).write_text("
                            "json.dumps({'event':'case'}) + '\\n', "
                            "encoding='utf-8')"
                        ),
                        "raise SystemExit(7)",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_case.chmod(0o755)

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                env={"EVENTS_PATH": str(events_path)},
            )

            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertIn("run-workstream stopped after central-3gj.2", result.stderr)
            self.assertEqual(
                [
                    json.loads(line)["event"]
                    for line in events_path.read_text().splitlines()
                ],
                ["case"],
            )

    def test_stops_on_light_verification_failure_before_final_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            final_marker = tmp_path / "final-ran"
            workstream = [
                {
                    "id": "central-3gj.2",
                    "title": "Light check fails",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": self.runnable_metadata(
                        target_repo,
                        light_command=f"{sys.executable} -c 'raise SystemExit(9)'",
                        validation_command=f"touch {final_marker}",
                    ),
                    "parent": "central-3gj",
                }
            ]
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(json.dumps(workstream), encoding="utf-8")
            fake_case = tmp_path / "fake-case"
            fake_case.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            fake_case.chmod(0o755)

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
            )

            self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
            self.assertIn(
                "run-workstream light verification failed for central-3gj.2",
                result.stderr,
            )
            self.assertFalse(final_marker.exists())

    def test_final_validation_runs_once_and_failure_stops_batch(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            final_marker = tmp_path / "final-ran"
            validation_command = (
                f"{sys.executable} -c "
                "\"import pathlib; "
                f"pathlib.Path({str(final_marker)!r}).write_text('ran\\n', encoding='utf-8'); "
                "raise SystemExit(5)\""
            )
            workstream = [
                {
                    "id": "central-3gj.2",
                    "title": "Final validation fails",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": self.runnable_metadata(
                        target_repo,
                        light_command=None,
                        validation_command=validation_command,
                    ),
                    "parent": "central-3gj",
                }
            ]
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(json.dumps(workstream), encoding="utf-8")
            fake_case = tmp_path / "fake-case"
            fake_case.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            fake_case.chmod(0o755)

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
            )

            self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
            self.assertIn("run-workstream final validation failed", result.stderr)
            self.assertTrue(final_marker.is_file())

    def test_stops_on_dependency_blockage_before_case_or_final_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            marker_path = tmp_path / "should-not-run"
            workstream = [
                {
                    "id": "central-3gj.2",
                    "title": "Blocked child",
                    "status": "open",
                    "labels": ["project:automation", "ready-for-agent"],
                    "metadata": self.runnable_metadata(
                        target_repo,
                        light_command=None,
                        validation_command=f"touch {marker_path}",
                    ),
                    "parent": "central-3gj",
                    "dependencies": [
                        {
                            "issue_id": "central-3gj.2",
                            "depends_on_id": "central-3gj.99",
                            "type": "blocks",
                        }
                    ],
                }
            ]
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(json.dumps(workstream), encoding="utf-8")

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                "false",
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "run-workstream blocked by dependencies: central-3gj.2",
                result.stderr,
            )
            self.assertFalse(marker_path.exists())

    def test_legacy_gate_metadata_does_not_block_afk_run_or_record_approval(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            state_dir = tmp_path / ".automation-simple"
            case_ran_path = tmp_path / "case-ran"
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "from pathlib import Path",
                        (
                            f"Path({str(case_ran_path)!r}).write_text("
                            "'ran\\n', encoding='utf-8')"
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            metadata = {
                **self.runnable_metadata(
                    target_repo,
                    light_command=None,
                    validation_command="true",
                ),
                "human_gates": ["Wait for maintainer approval before live Case."],
                "environment_gates": ["Requires live model access."],
                "stop_conditions": ["Stop if fixture asks for approval."],
                "gates": ["Legacy generic gate."],
                "gate_approval_id": "approval-central-3gj.2",
                "gate_approved_by": "bump",
                "gate_approved_at": "2026-06-16T04:30:00Z",
                "gate_approved_for": "A different gate.",
            }
            workstream_json = tmp_path / "workstream.json"
            workstream_json.write_text(
                json.dumps(
                    [
                        {
                            "id": "central-3gj.2",
                            "title": "Human gated child",
                            "status": "open",
                            "labels": ["project:automation", "ready-for-agent"],
                            "metadata": metadata,
                            "parent": "central-3gj",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "run-workstream",
                "--parent",
                "central-3gj",
                "--workstream-json",
                str(workstream_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(case_ran_path.exists())
            self.assertNotIn("run-workstream gated before", result.stderr)
            request = json.loads(
                next(state_dir.glob("runs/*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("gate_approval", request)
