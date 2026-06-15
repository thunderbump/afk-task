from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from base64 import urlsafe_b64encode
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
    def write_eligible_bead(self, path: Path, target_repo: Path, bead_id: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "id": bead_id,
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

    def fake_jwt(self, payload: dict[str, object]) -> str:
        def encode(part: dict[str, object] | bytes) -> str:
            raw = (
                json.dumps(part, separators=(",", ":")).encode("utf-8")
                if isinstance(part, dict)
                else part
            )
            return urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        return ".".join([encode({"alg": "none", "typ": "JWT"}), encode(payload), "sig"])

    def write_codex_auth(self, path: Path, payload: dict[str, object]) -> str:
        token = self.fake_jwt(payload)
        path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "access_token": token,
                        "refresh_token": "fixture-refresh-token",
                    },
                }
            ),
            encoding="utf-8",
        )
        return token

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
                        "import json, os, shutil, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'cwd': os.getcwd(),",
                        "  'case_data_dir': os.environ.get('CASE_DATA_DIR'),",
                        "  'home': os.environ.get('HOME'),",
                        "  'path': os.environ.get('PATH'),",
                        "  'ca_path': shutil.which('ca'),",
                        "  'beads_password': os.environ.get('BEADS_DOLT_PASSWORD'),",
                        "  'openai_api_key': os.environ.get('OPENAI_API_KEY'),",
                        "  'pi_coding_agent_dir': os.environ.get('PI_CODING_AGENT_DIR'),",
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
                env={
                    "BEADS_DOLT_PASSWORD": "must-not-reach-case",
                    "OPENAI_API_KEY": "ambient-openai-key",
                    "PI_CODING_AGENT_DIR": "ambient-pi-dir",
                },
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
            task_markdown = task_md_path.read_text(encoding="utf-8")
            self.assertIn("## Evidence Expectations", task_markdown)
            self.assertIn(
                "Run `python3 -m unittest discover -s tests`", task_markdown
            )
            self.assertIn("test-output evidence", task_markdown)
            self.assertIn("changed paths", task_markdown)
            self.assertIn("No screenshot or video evidence is required", task_markdown)

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
            case_cli_shim = state_dir / "case-bin" / "ca"
            self.assertEqual(fake_case_record["ca_path"], str(case_cli_shim))
            self.assertEqual(
                fake_case_record["path"].split(os.pathsep)[0],
                str(case_cli_shim.parent),
            )
            self.assertTrue(os.access(case_cli_shim, os.X_OK))
            shim_text = case_cli_shim.read_text(encoding="utf-8")
            self.assertIn("CASE_CHECKOUT=", shim_text)
            self.assertIn(str(case_checkout), shim_text)
            self.assertIn('exec bun "$CASE_CHECKOUT/src/index.ts" "$@"', shim_text)
            self.assertIsNone(fake_case_record["beads_password"])
            self.assertIsNone(fake_case_record["openai_api_key"])
            self.assertIsNone(fake_case_record["pi_coding_agent_dir"])

            requests = list((state_dir / "runs").glob("*/execution-request.json"))
            self.assertEqual(len(requests), 1)
            execution_request = json.loads(requests[0].read_text(encoding="utf-8"))
            self.assertEqual(execution_request["bead_id"], "central-run.1")
            self.assertEqual(
                execution_request["sandcastle_runtime_adapter"]["status"],
                "scaffolded",
            )
            self.assertEqual(execution_request["case_cli_shim"], str(case_cli_shim))
            self.assertNotIn("ambient-openai-key", json.dumps(execution_request))
            self.assertNotIn("must-not-reach-case", json.dumps(execution_request))

    def test_case_cli_shim_preserves_agent_cwd_and_uses_absolute_case_entrypoint(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_bun = fake_bin / "bun"
            fake_bun.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "Path(os.environ['FAKE_BUN_RECORD']).write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'cwd': os.getcwd(),",
                        "}) + '\\n', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_bun.chmod(0o755)
            record_path = tmp_path / "bun-record.json"
            bead_json = tmp_path / "bead.json"
            self.write_eligible_bead(bead_json, target_repo, "central-run.9")

            result = run_cli(
                "run",
                "--bead",
                "central-run.9",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                "true",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            case_cli_shim = state_dir / "case-bin" / "ca"
            shim_result = subprocess.run(
                [
                    str(case_cli_shim),
                    "status",
                    ".case/tasks/active/central-run.9.task.json",
                ],
                cwd=target_repo,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
                    "FAKE_BUN_RECORD": str(record_path),
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(
                shim_result.returncode, 0, shim_result.stdout + shim_result.stderr
            )
            bun_record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(bun_record["cwd"], str(target_repo))
            self.assertEqual(
                bun_record["argv"],
                [
                    str(case_checkout / "src" / "index.ts"),
                    "status",
                    ".case/tasks/active/central-run.9.task.json",
                ],
            )

    def test_case_codex_session_writes_wrapper_config_and_injects_child_env_only(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            auth_file = tmp_path / "auth.json"
            token = self.write_codex_auth(
                auth_file,
                {
                    "exp": int(time.time()) + 3600,
                    "https://api.openai.com/auth.chatgpt_account_id": "acct_fixture",
                },
            )
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
                        "  'pi_coding_agent_dir': os.environ.get('PI_CODING_AGENT_DIR'),",
                        "  'openai_api_key': os.environ.get('OPENAI_API_KEY'),",
                        "  'beads_password': os.environ.get('BEADS_DOLT_PASSWORD'),",
                        "}) + '\\n', encoding='utf-8')",
                        "print(os.environ.get('OPENAI_API_KEY'))",
                        "print(os.environ.get('OPENAI_API_KEY'), file=sys.stderr)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bead_json = tmp_path / "bead.json"
            self.write_eligible_bead(bead_json, target_repo, "central-run.8")

            result = run_cli(
                "run",
                "--bead",
                "central-run.8",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--case-codex-session",
                "--codex-auth-file",
                str(auth_file),
                "--case-codex-scout-only",
                env={"BEADS_DOLT_PASSWORD": "must-not-reach-case"},
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(fake_case_record["openai_api_key"], token)
            self.assertEqual(
                fake_case_record["pi_coding_agent_dir"],
                str(state_dir / "pi-codex"),
            )
            self.assertIsNone(fake_case_record["beads_password"])

            pi_models = json.loads(
                (state_dir / "pi-codex" / "models.json").read_text(encoding="utf-8")
            )
            openai_model = pi_models["providers"]["openai"]["models"][0]
            self.assertEqual(openai_model["id"], "gpt-5.5")
            self.assertEqual(openai_model["api"], "openai-codex-responses")
            self.assertEqual(openai_model["baseUrl"], "https://chatgpt.com/backend-api")
            self.assertNotIn(token, json.dumps(pi_models))

            case_config = json.loads(
                (state_dir / "case-data" / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                case_config["models"]["scout"],
                {"provider": "openai", "model": "gpt-5.5"},
            )
            self.assertEqual(
                case_config["models"]["default"],
                {"provider": "invalid", "model": "invalid-scout-only"},
            )

            execution_request = json.loads(
                next((state_dir / "runs").glob("*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                execution_request["case_codex_session"],
                {
                    "enabled": True,
                    "auth_source_path": str(auth_file),
                    "model": "gpt-5.5",
                    "pi_config_dir": str(state_dir / "pi-codex"),
                    "scout_only": True,
                },
            )
            self.assertNotIn(token, json.dumps(execution_request))
            run_dir = next((state_dir / "runs").glob("*"))
            self.assertNotIn(
                token, (run_dir / "case-stdout.txt").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                token, (run_dir / "case-stderr.txt").read_text(encoding="utf-8")
            )
            self.assertNotIn(token, result.stdout + result.stderr)

    def test_case_codex_session_auth_failures_stop_before_case(self) -> None:
        cases: list[tuple[str, str | None, str]] = [
            (
                "missing-file",
                None,
                "missing Codex auth file",
            ),
            (
                "expired-token",
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "access_token": self.fake_jwt(
                                {
                                    "exp": int(time.time()) - 60,
                                    "https://api.openai.com/auth.chatgpt_account_id": (
                                        "acct_fixture"
                                    ),
                                }
                            )
                        },
                    }
                ),
                "expired or too close to expiry",
            ),
            (
                "malformed-token",
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {"access_token": "not-a-jwt"},
                    }
                ),
                "not a JWT",
            ),
            (
                "missing-chatgpt-claim",
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "access_token": self.fake_jwt(
                                {"exp": int(time.time()) + 3600}
                            )
                        },
                    }
                ),
                "missing ChatGPT account claim",
            ),
        ]
        for name, auth_json, expected_error in cases:
            with self.subTest(name=name), TemporaryDirectory() as tmp:
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
                            "from pathlib import Path",
                            "Path(__file__).with_suffix('.json').write_text('ran\\n', encoding='utf-8')",
                        ]
                    ),
                    encoding="utf-8",
                )
                fake_case.chmod(0o755)
                bead_json = tmp_path / "bead.json"
                self.write_eligible_bead(bead_json, target_repo, f"central-{name}")
                auth_file = tmp_path / "auth.json"
                if auth_json is not None:
                    auth_file.write_text(auth_json, encoding="utf-8")

                result = run_cli(
                    "run",
                    "--bead",
                    f"central-{name}",
                    "--bead-json",
                    str(bead_json),
                    "--state-dir",
                    str(state_dir),
                    "--case-checkout",
                    str(case_checkout),
                    "--case-command",
                    str(fake_case),
                    "--case-codex-session",
                    "--codex-auth-file",
                    str(auth_file),
                )

                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected_error, result.stderr)
                self.assertFalse(fake_case.with_suffix(".json").exists())
                self.assertFalse((state_dir / "case-data").exists())

    def test_case_dry_run_flag_is_passed_to_case_command(self) -> None:
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
                        "import json, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
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
                        "id": "central-run.5",
                        "title": "Dry-run Case handoff",
                        "description": "Use native Case dry-run.",
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
                "central-run.5",
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

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                fake_case_record["argv"],
                [
                    "src/index.ts",
                    "run",
                    "--task",
                    str(
                        target_repo
                        / ".case"
                        / "tasks"
                        / "active"
                        / "central-run.5.task.json"
                    ),
                    "--mode",
                    "unattended",
                    "--dry-run",
                ],
            )
            execution_request = json.loads(
                next(state_dir.glob("runs/*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(execution_request["case_dry_run"])

    def test_case_runtime_module_is_passed_to_case_and_recorded(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            target_repo.mkdir()
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            runtime_module = tmp_path / "case-runtime.js"
            runtime_module.write_text("export default {};\n", encoding="utf-8")
            fake_case = tmp_path / "fake-case"
            fake_case.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, sys",
                        "from pathlib import Path",
                        "Path(sys.argv[0]).with_suffix('.json').write_text(json.dumps({",
                        "  'argv': sys.argv[1:],",
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
                        "id": "central-run.7",
                        "title": "Runtime module handoff",
                        "description": "Use native Case runtime module.",
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
                "central-run.7",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--case-runtime-module",
                str(runtime_module),
                "--case-dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            fake_case_record = json.loads(
                fake_case.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                fake_case_record["argv"],
                [
                    "src/index.ts",
                    "run",
                    "--task",
                    str(
                        target_repo
                        / ".case"
                        / "tasks"
                        / "active"
                        / "central-run.7.task.json"
                    ),
                    "--mode",
                    "unattended",
                    "--runtime-module",
                    str(runtime_module),
                    "--dry-run",
                ],
            )
            execution_request = json.loads(
                next(state_dir.glob("runs/*/execution-request.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                execution_request["case_runtime_module"], str(runtime_module)
            )
            self.assertTrue(execution_request["case_dry_run"])

    def test_case_dry_run_archives_native_task_mutation_and_restores_generated_task(
        self,
    ) -> None:
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
                        "import json, sys",
                        "from pathlib import Path",
                        "task = Path(sys.argv[sys.argv.index('--task') + 1])",
                        "payload = json.loads(task.read_text(encoding='utf-8'))",
                        "payload['status'] = 'pr-opened'",
                        "payload['tested'] = True",
                        "payload['prUrl'] = None",
                        "task.write_text(json.dumps(payload) + '\\n', encoding='utf-8')",
                        "print('Pipeline completed successfully.')",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bead_json = tmp_path / "bead.json"
            bead_json.write_text(
                json.dumps(
                    {
                        "id": "central-run.6",
                        "title": "Restore dry-run task",
                        "description": "Dry-run should not leave lifecycle state.",
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
                "central-run.6",
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

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            task_json_path = (
                target_repo / ".case" / "tasks" / "active" / "central-run.6.task.json"
            )
            restored_task = json.loads(task_json_path.read_text(encoding="utf-8"))
            self.assertEqual(restored_task["status"], "active")
            self.assertFalse(restored_task["tested"])
            self.assertIsNone(restored_task["prUrl"])

            native_task_paths = list(state_dir.glob("runs/*/native-dry-run-task.json"))
            self.assertEqual(len(native_task_paths), 1)
            native_task = json.loads(native_task_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(native_task["status"], "pr-opened")
            self.assertTrue(native_task["tested"])

    def test_case_pipeline_failure_output_makes_run_fail_even_with_zero_exit(
        self,
    ) -> None:
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
                        "print('Pipeline failed at implementer phase.')",
                        "raise SystemExit(0)",
                    ]
                ),
                encoding="utf-8",
            )
            fake_case.chmod(0o755)
            bead_json = tmp_path / "bead.json"
            bead_json.write_text(
                json.dumps(
                    {
                        "id": "central-run.4",
                        "title": "Surface Case failure",
                        "description": "Treat failed Case pipelines as failed runs.",
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
                "central-run.4",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
            )

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Case pipeline reported failure", result.stderr)
            run_results = list(state_dir.glob("runs/*/case-result.json"))
            self.assertEqual(len(run_results), 1)
            case_result = json.loads(run_results[0].read_text(encoding="utf-8"))
            self.assertEqual(case_result["exit_code"], 0)
            self.assertEqual(case_result["interpreted_exit_code"], 1)

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
