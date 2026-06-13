from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "automation_simple_spike", *args],
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class RunBeadCliTest(unittest.TestCase):
    def test_ineligible_bead_stops_before_case_state_or_execution(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            bead_json = tmp_path / "bead.json"
            bead_json.write_text(
                json.dumps(
                    {
                        "id": "central-run.1",
                        "title": "Wire simple runner",
                        "status": "open",
                        "labels": ["project:automation", "ready-for-agent"],
                        "metadata": {
                            "afk_enabled": True,
                            "afk_runner": "codex",
                            "target_repo": "local/test",
                            "target_repo_path": str(target_repo),
                            "target_base_branch": "main",
                            "branch_policy": "independent",
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_case_log = tmp_path / "fake-case.jsonl"

            result = run_cli(
                "run",
                "--bead",
                "central-run.1",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(tmp_path / ".automation-simple"),
                "--case-command",
                str(tmp_path / "fake-case"),
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(
                "missing metadata validation_command",
                result.stderr,
            )
            self.assertFalse((target_repo / ".case").exists())
            self.assertFalse(fake_case_log.exists())

    def test_eligible_bead_writes_case_state_and_invokes_case_command(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
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
                        "  'case_data_dir': os.environ.get('CASE_DATA_DIR'),",
                        "  'home': os.environ.get('HOME'),",
                        "  'beads_password': os.environ.get('BEADS_DOLT_PASSWORD'),",
                        "}) + '\\n', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bead_json = tmp_path / "bead.json"
            bead_json.write_text(
                json.dumps(
                    {
                        "id": "central-run.1",
                        "title": "Wire simple runner",
                        "description": "Generate Case state and hand off.",
                        "status": "open",
                        "labels": ["project:automation", "ready-for-agent"],
                        "metadata": {
                            "afk_enabled": True,
                            "afk_runner": "codex",
                            "target_repo": "local/test",
                            "target_repo_path": str(target_repo),
                            "target_base_branch": "main",
                            "branch_policy": "independent",
                            "validation_command": "python3 -m unittest discover -s tests",
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "run",
                "--bead",
                "central-run.1",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                env={"BEADS_DOLT_PASSWORD": "must-not-reach-case"},
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("run-bead handed off: central-run.1", result.stdout)
            task_json_path = (
                target_repo / ".case" / "tasks" / "active" / "central-run.1.task.json"
            )
            task_md_path = (
                target_repo / ".case" / "tasks" / "active" / "central-run.1.md"
            )
            self.assertTrue(task_json_path.is_file())
            self.assertTrue(task_md_path.is_file())
            task_json = json.loads(task_json_path.read_text(encoding="utf-8"))
            self.assertEqual(task_json["id"], "central-run.1")
            self.assertEqual(task_json["repo"], "local/test")
            self.assertEqual(task_json["branch"], "agent/central-run.1")
            self.assertEqual(task_json["mode"], "unattended")
            self.assertEqual(
                task_json["checkCommand"], "python3 -m unittest discover -s tests"
            )
            self.assertIn("Generate Case state and hand off.", task_md_path.read_text())

            project_manifest = json.loads(
                (state_dir / "case-data" / "projects.json").read_text(encoding="utf-8")
            )
            self.assertEqual(project_manifest["repos"][0]["name"], "local/test")
            self.assertEqual(project_manifest["repos"][0]["path"], str(target_repo))
            case_config = json.loads(
                (state_dir / "case-data" / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(case_config["projects"], "./projects.json")

            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(fake_case_record["cwd"], str(case_checkout))
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
            self.assertEqual(
                fake_case_record["case_data_dir"], str(state_dir / "case-data")
            )
            self.assertIsNone(fake_case_record["beads_password"])

            requests = list((state_dir / "runs").glob("*/execution-request.json"))
            self.assertEqual(len(requests), 1)
            execution_request = json.loads(requests[0].read_text(encoding="utf-8"))
            self.assertEqual(execution_request["bead_id"], "central-run.1")
            self.assertEqual(
                execution_request["sandcastle_runtime_adapter"]["status"],
                "scaffolded",
            )

    def test_can_load_bead_through_fake_bd_without_leaking_password_to_case(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            beads_workspace = tmp_path / "beads"
            beads_workspace.mkdir()
            secrets_dir = beads_workspace / "secrets"
            secrets_dir.mkdir()
            password_file = secrets_dir / "dolt_beads_password.txt"
            password_file.write_text("dummy-password\n", encoding="utf-8")

            payload_path = tmp_path / "bd-payload.json"
            payload_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "central-run.2",
                            "title": "Load from Beads",
                            "description": "Use bd show.",
                            "status": "open",
                            "labels": ["project:automation", "ready-for-agent"],
                            "metadata": {
                                "afk_enabled": True,
                                "afk_runner": "codex",
                                "target_repo": "local/test",
                                "target_repo_path": str(target_repo),
                                "target_base_branch": "main",
                                "branch_policy": "independent",
                                "validation_command": "true",
                            },
                        }
                    ]
                ),
                encoding="utf-8",
            )

            fake_bd = tmp_path / "fake-bd"
            fake_bd.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "Path(os.environ['FAKE_BD_LOG']).write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'cwd': os.getcwd(),",
                        "  'password': os.environ.get('BEADS_DOLT_PASSWORD'),",
                        "}) + '\\n', encoding='utf-8')",
                        "print(Path(os.environ['FAKE_BD_PAYLOAD']).read_text("
                        "encoding='utf-8'))",
                    ]
                ),
                encoding="utf-8",
            )
            fake_bd.chmod(0o755)

            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'beads_password': os.environ.get('BEADS_DOLT_PASSWORD'),",
                        "}) + '\\n', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bd_log = tmp_path / "bd-log.json"

            result = run_cli(
                "run",
                "--bead",
                "central-run.2",
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--bd-command",
                str(fake_bd),
                "--beads-workspace",
                str(beads_workspace),
                "--beads-password-file",
                str(password_file),
                env={
                    "FAKE_BD_LOG": str(bd_log),
                    "FAKE_BD_PAYLOAD": str(payload_path),
                },
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            bd_record = json.loads(bd_log.read_text(encoding="utf-8"))
            self.assertEqual(bd_record["argv"], ["show", "central-run.2", "--json"])
            self.assertEqual(bd_record["cwd"], str(beads_workspace))
            self.assertEqual(bd_record["password"], "dummy-password")
            case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertIsNone(case_record["beads_password"])

    def test_relative_state_dir_is_resolved_before_case_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            state_dir = f".automation-simple/test-{uuid4().hex}"

            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os",
                        "from pathlib import Path",
                        "Path(__file__).with_suffix('.json').write_text(json.dumps({",
                        "  'case_data_dir': os.environ.get('CASE_DATA_DIR'),",
                        "  'home': os.environ.get('HOME'),",
                        "}) + '\\n', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)

            bead_json = tmp_path / "bead.json"
            bead_json.write_text(
                json.dumps(
                    {
                        "id": "central-run.3",
                        "title": "Resolve state dir",
                        "description": "Use relative state dir.",
                        "status": "open",
                        "labels": ["project:automation", "ready-for-agent"],
                        "metadata": {
                            "afk_enabled": True,
                            "afk_runner": "codex",
                            "target_repo": "local/test",
                            "target_repo_path": str(target_repo),
                            "target_base_branch": "main",
                            "branch_policy": "independent",
                            "validation_command": "true",
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "run",
                "--bead",
                "central-run.3",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                state_dir,
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            expected_case_data_dir = str((REPO_ROOT / state_dir / "case-data").resolve())
            self.assertEqual(fake_case_record["case_data_dir"], expected_case_data_dir)
            self.assertEqual(
                fake_case_record["home"], str(Path(expected_case_data_dir) / "home")
            )
