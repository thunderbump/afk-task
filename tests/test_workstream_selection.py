from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from automation_simple_spike.workstream_selection import (
    select_runnable_workstream_beads,
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


def runnable_metadata() -> dict[str, object]:
    return {
        "afk_enabled": True,
        "afk_runner": "codex",
        "target_repo": "local/test",
        "target_repo_path": "/tmp/target",
        "target_base_branch": "main",
        "branch_policy": "shared-sequential",
        "validation_command": "python3 -m unittest discover -s tests",
        "workstream_id": "workstream-validation",
    }


class WorkstreamSelectionTest(unittest.TestCase):
    def test_selects_afk_ready_children_and_excludes_parent_records(self) -> None:
        issues = [
            {
                "id": "central-3gj",
                "title": "PRD: Separate validation and gameplay flows",
                "status": "open",
                "issue_type": "feature",
                "labels": ["project:bump-eqemu", "ready-for-agent", "type:prd"],
            },
            {
                "id": "central-3gj.1",
                "title": "Finished prerequisite",
                "status": "closed",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(),
                "parent": "central-3gj",
            },
            {
                "id": "central-3gj.2",
                "title": "Ready child",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": runnable_metadata(),
                "parent": "central-3gj",
                "dependencies": [
                    {
                        "issue_id": "central-3gj.2",
                        "depends_on_id": "central-3gj.1",
                        "type": "blocks",
                    }
                ],
            },
            {
                "id": "central-3gj.3",
                "title": "Looks ready by label only",
                "status": "open",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "parent": "central-3gj",
            },
        ]

        selected = select_runnable_workstream_beads(issues, parent_id="central-3gj")

        self.assertEqual([issue["id"] for issue in selected], ["central-3gj.2"])

    def test_selects_claimed_afk_children_without_active_run(self) -> None:
        metadata = runnable_metadata()
        issues = [
            {
                "id": "central-3gj.2",
                "title": "Claimed child",
                "status": "in_progress",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": metadata,
                "parent": "central-3gj",
            },
            {
                "id": "central-3gj.3",
                "title": "Already running child",
                "status": "in_progress",
                "labels": ["project:bump-eqemu", "ready-for-agent"],
                "metadata": {**metadata, "active_run_id": "run-bead-central-3gj.3"},
                "parent": "central-3gj",
            },
        ]

        selected = select_runnable_workstream_beads(issues, parent_id="central-3gj")

        self.assertEqual([issue["id"] for issue in selected], ["central-3gj.2"])

    def test_command_lists_runnable_parent_children_from_fake_bd(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            beads_workspace = tmp_path / "beads"
            beads_workspace.mkdir()
            password_file = beads_workspace / "dolt_beads_password.txt"
            password_file.write_text("dummy-password\n", encoding="utf-8")

            show_payload = [
                {
                    "id": "central-3gj",
                    "title": "PRD: Separate validation and gameplay flows",
                    "status": "open",
                    "issue_type": "feature",
                    "labels": [
                        "project:bump-eqemu",
                        "ready-for-agent",
                        "type:prd",
                    ],
                    "dependents": [
                        {
                            "id": "central-3gj.1",
                            "title": "Finished prerequisite",
                            "status": "closed",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "metadata": runnable_metadata(),
                            "dependency_type": "parent-child",
                        },
                        {
                            "id": "central-3gj.2",
                            "title": "Ready child",
                            "status": "open",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "metadata": runnable_metadata(),
                            "dependency_type": "parent-child",
                        },
                        {
                            "id": "central-3gj.3",
                            "title": "Label-only child",
                            "status": "open",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "dependency_type": "parent-child",
                        },
                        {
                            "id": "central-3gj.4",
                            "title": "Blocked child",
                            "status": "open",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "metadata": runnable_metadata(),
                            "dependency_type": "parent-child",
                        },
                    ],
                }
            ]
            children_payload = [
                {
                    "id": "central-3gj.1",
                    "title": "Finished prerequisite",
                    "status": "closed",
                    "labels": ["project:bump-eqemu", "ready-for-agent"],
                    "metadata": runnable_metadata(),
                    "parent": "central-3gj",
                    "dependencies": [
                        {
                            "issue_id": "central-3gj.1",
                            "depends_on_id": "central-3gj",
                            "type": "parent-child",
                        }
                    ],
                },
                {
                    "id": "central-3gj.2",
                    "title": "Ready child",
                    "status": "open",
                    "labels": ["project:bump-eqemu", "ready-for-agent"],
                    "metadata": runnable_metadata(),
                    "parent": "central-3gj",
                    "dependencies": [
                        {
                            "issue_id": "central-3gj.2",
                            "depends_on_id": "central-3gj.1",
                            "type": "blocks",
                        }
                    ],
                },
                {
                    "id": "central-3gj.3",
                    "title": "Label-only child",
                    "status": "open",
                    "labels": ["project:bump-eqemu", "ready-for-agent"],
                    "parent": "central-3gj",
                },
                {
                    "id": "central-3gj.4",
                    "title": "Blocked child",
                    "status": "open",
                    "labels": ["project:bump-eqemu", "ready-for-agent"],
                    "metadata": runnable_metadata(),
                    "parent": "central-3gj",
                    "dependencies": [
                        {
                            "issue_id": "central-3gj.4",
                            "depends_on_id": "central-3gj.2",
                            "type": "blocks",
                        }
                    ],
                },
            ]
            payloads_path = tmp_path / "bd-payloads.json"
            payloads_path.write_text(
                json.dumps({"show": show_payload, "children": children_payload}),
                encoding="utf-8",
            )
            calls_path = tmp_path / "bd-calls.json"
            fake_bd = tmp_path / "fake-bd"
            fake_bd.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "calls_path = Path(os.environ['FAKE_BD_CALLS'])",
                        "calls = json.loads(calls_path.read_text(encoding='utf-8')) if calls_path.exists() else []",
                        "calls.append(sys.argv[1:])",
                        "calls_path.write_text(json.dumps(calls) + '\\n', encoding='utf-8')",
                        "payloads = json.loads(Path(os.environ['FAKE_BD_PAYLOADS']).read_text(encoding='utf-8'))",
                        "if sys.argv[1:] == ['show', 'central-3gj', '--json']:",
                        "    print(json.dumps(payloads['show']))",
                        "elif sys.argv[1:] == ['children', 'central-3gj', '--json']:",
                        "    print(json.dumps(payloads['children']))",
                        "else:",
                        "    print(f'unexpected bd args: {sys.argv[1:]}', file=sys.stderr)",
                        "    raise SystemExit(2)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_bd.chmod(0o755)

            result = run_cli(
                "select-workstream",
                "--parent",
                "central-3gj",
                "--json",
                "--bd-command",
                str(fake_bd),
                "--beads-workspace",
                str(beads_workspace),
                "--beads-password-file",
                str(password_file),
                env={
                    "FAKE_BD_CALLS": str(calls_path),
                    "FAKE_BD_PAYLOADS": str(payloads_path),
                },
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            selected = json.loads(result.stdout)
            self.assertEqual([issue["id"] for issue in selected], ["central-3gj.2"])
            self.assertEqual(
                json.loads(calls_path.read_text(encoding="utf-8")),
                [
                    ["show", "central-3gj", "--json"],
                    ["children", "central-3gj", "--json"],
                ],
            )

    def test_command_lists_runnable_beads_by_workstream_id(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            beads_workspace = tmp_path / "beads"
            beads_workspace.mkdir()
            password_file = beads_workspace / "dolt_beads_password.txt"
            password_file.write_text("dummy-password\n", encoding="utf-8")
            payload_path = tmp_path / "bd-payload.json"
            payload_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "central-3gj.2",
                            "title": "Ready child",
                            "status": "open",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "metadata": runnable_metadata(),
                        },
                        {
                            "id": "central-3gj.3",
                            "title": "Metadata only",
                            "status": "open",
                            "labels": ["project:bump-eqemu"],
                            "metadata": runnable_metadata(),
                        },
                        {
                            "id": "central-other.1",
                            "title": "Different workstream",
                            "status": "open",
                            "labels": ["project:bump-eqemu", "ready-for-agent"],
                            "metadata": {
                                **runnable_metadata(),
                                "workstream_id": "workstream-other",
                            },
                        },
                    ]
                ),
                encoding="utf-8",
            )
            calls_path = tmp_path / "bd-calls.json"
            fake_bd = tmp_path / "fake-bd"
            fake_bd.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "calls_path = Path(os.environ['FAKE_BD_CALLS'])",
                        "calls = json.loads(calls_path.read_text(encoding='utf-8')) if calls_path.exists() else []",
                        "calls.append(sys.argv[1:])",
                        "calls_path.write_text(json.dumps(calls) + '\\n', encoding='utf-8')",
                        "expected = ['list', '--metadata-field', 'workstream_id=workstream-validation', '--json', '--all', '--limit', '0']",
                        "if sys.argv[1:] != expected:",
                        "    print(f'unexpected bd args: {sys.argv[1:]}', file=sys.stderr)",
                        "    raise SystemExit(2)",
                        "print(Path(os.environ['FAKE_BD_PAYLOAD']).read_text(encoding='utf-8'))",
                    ]
                ),
                encoding="utf-8",
            )
            fake_bd.chmod(0o755)

            result = run_cli(
                "select-workstream",
                "--workstream-id",
                "workstream-validation",
                "--bd-command",
                str(fake_bd),
                "--beads-workspace",
                str(beads_workspace),
                "--beads-password-file",
                str(password_file),
                env={
                    "FAKE_BD_CALLS": str(calls_path),
                    "FAKE_BD_PAYLOAD": str(payload_path),
                },
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "central-3gj.2\n")
            self.assertEqual(
                json.loads(calls_path.read_text(encoding="utf-8")),
                [
                    [
                        "list",
                        "--metadata-field",
                        "workstream_id=workstream-validation",
                        "--json",
                        "--all",
                        "--limit",
                        "0",
                    ]
                ],
            )
