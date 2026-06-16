from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Iterator
import unittest
from uuid import uuid4

from automation_simple_spike.beads_lifecycle import (
    BeadsLifecycleClient,
    LifecycleRun,
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


class BeadsLifecycleTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def init_target_repo(self, path: Path) -> str:
        path.mkdir()
        self.git(path, "init")
        self.git(path, "config", "user.email", "test@example.com")
        self.git(path, "config", "user.name", "Test User")
        (path / "README.md").write_text("fixture target\n", encoding="utf-8")
        self.git(path, "add", "README.md")
        self.git(path, "commit", "-m", "Initial target commit")
        self.git(path, "branch", "-M", "main")
        return self.git(path, "rev-parse", "main").stdout.strip()

    def write_eligible_bead(self, path: Path, target_repo: Path, bead_id: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "id": bead_id,
                    "title": "Lifecycle AFK run",
                    "description": "Record lifecycle metadata.",
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

    def fake_bd(self, path: Path) -> Path:
        fake_bd = path / "fake-bd"
        fake_bd.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import json, os, sys",
                    "from pathlib import Path",
                    "transcript_path = Path(os.environ['FAKE_BD_TRANSCRIPT'])",
                    "transcript = json.loads(transcript_path.read_text(encoding='utf-8')) if transcript_path.exists() else []",
                    "stdin = sys.stdin.read()",
                    "transcript.append({",
                    "  'argv': sys.argv[1:],",
                    "  'cwd': os.getcwd(),",
                    "  'stdin': stdin,",
                    "  'beads_password_present': bool(os.environ.get('BEADS_DOLT_PASSWORD')),",
                    "})",
                    "transcript_path.write_text(json.dumps(transcript, indent=2) + '\\n', encoding='utf-8')",
                ]
            ),
            encoding="utf-8",
        )
        fake_bd.chmod(0o755)
        return fake_bd

    def write_fake_case(self, path: Path, exit_code: int = 0) -> Path:
        fake_case = path / "fake-case"
        fake_case.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env python3",
                    "import os, sys",
                    "print(f'beads password in case: {os.environ.get(\"BEADS_DOLT_PASSWORD\")}')",
                    f"raise SystemExit({exit_code})",
                ]
            ),
            encoding="utf-8",
        )
        fake_case.chmod(0o755)
        return fake_case

    def client(self, tmp_path: Path) -> tuple[BeadsLifecycleClient, Path, str]:
        beads_workspace = tmp_path / "beads"
        beads_workspace.mkdir()
        password_file = tmp_path / "beads-password.txt"
        secret_value = f"generated-{uuid4()}"
        password_file.write_text(secret_value + "\n", encoding="utf-8")
        transcript = tmp_path / "bd-transcript.json"
        fake_bd = self.fake_bd(tmp_path)
        return (
            BeadsLifecycleClient(
                bd_command=str(fake_bd),
                beads_workspace=beads_workspace,
                beads_password_file=password_file,
            ),
            transcript,
            secret_value,
        )

    def run_state(self, tmp_path: Path) -> LifecycleRun:
        return LifecycleRun(
            bead_id="central-5ik.4",
            run_id="run-bead-central-5ik.4-20260616T120000000000Z",
            review_branch="agent/central-5ik.4",
            target_checkout_mode="worktree",
            target_checkout_path=tmp_path / "worktree",
            target_source_checkout=tmp_path / "source",
            target_worktree_checkout=tmp_path / "worktree",
            archive_path=tmp_path / ".automation-simple" / "runs" / "run-123",
        )

    def metadata_from_update(self, argv: list[str]) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for index, value in enumerate(argv):
            if value != "--set-metadata":
                continue
            key, metadata_value = argv[index + 1].split("=", 1)
            metadata[key] = metadata_value
        return metadata

    def unset_metadata_from_update(self, argv: list[str]) -> list[str]:
        return [
            value
            for index, value in enumerate(argv)
            if argv[index - 1 : index] == ["--unset-metadata"]
        ]

    def assert_secret_not_in_tree(self, root: Path, secret_value: str) -> None:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            self.assertNotIn(secret_value, path.read_text(encoding="utf-8"))

    @contextmanager
    def transcript_env(self, transcript_path: Path) -> Iterator[None]:
        old_transcript = os.environ.get("FAKE_BD_TRANSCRIPT")
        os.environ["FAKE_BD_TRANSCRIPT"] = str(transcript_path)
        try:
            yield
        finally:
            if old_transcript is None:
                os.environ.pop("FAKE_BD_TRANSCRIPT", None)
            else:
                os.environ["FAKE_BD_TRANSCRIPT"] = old_transcript

    def test_run_start_writes_active_run_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client, transcript_path, secret_value = self.client(tmp_path)
            run = self.run_state(tmp_path)

            with self.transcript_env(transcript_path):
                client.record_start(run)

            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(
                transcript,
                [
                    {
                        "argv": [
                            "update",
                            "central-5ik.4",
                            "--set-metadata",
                            f"active_run_id={run.run_id}",
                            "--set-metadata",
                            "active_run_branch=agent/central-5ik.4",
                            "--set-metadata",
                            "active_run_checkout_mode=worktree",
                            "--set-metadata",
                            f"active_run_archive_path={run.archive_path}",
                            "--set-metadata",
                            f"active_run_target_checkout={run.target_checkout_path}",
                            "--set-metadata",
                            f"active_run_source_checkout={run.target_source_checkout}",
                            "--set-metadata",
                            f"active_run_worktree_checkout={run.target_worktree_checkout}",
                        ],
                        "cwd": str(tmp_path / "beads"),
                        "stdin": "",
                        "beads_password_present": True,
                    }
                ],
            )
            self.assertNotIn(
                secret_value,
                transcript_path.read_text(encoding="utf-8"),
            )

    def test_run_success_records_result_metadata_and_can_close_bead(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client, transcript_path, secret_value = self.client(tmp_path)
            run = self.run_state(tmp_path)

            with self.transcript_env(transcript_path):
                client.record_success(
                    run,
                    commit_sha="abc123def456",
                    interpreted_exit_code=0,
                    close_bead=True,
                )

            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(
                transcript,
                [
                    {
                        "argv": [
                            "update",
                            "central-5ik.4",
                            "--set-metadata",
                            f"last_afk_run_id={run.run_id}",
                            "--set-metadata",
                            "last_afk_run_result=success",
                            "--set-metadata",
                            "last_afk_run_exit_code=0",
                            "--set-metadata",
                            "last_afk_run_commit=abc123def456",
                            "--set-metadata",
                            "last_afk_run_branch=agent/central-5ik.4",
                            "--set-metadata",
                            f"last_afk_run_archive_path={run.archive_path}",
                            "--unset-metadata",
                            "active_run_id",
                            "--unset-metadata",
                            "active_run_branch",
                            "--unset-metadata",
                            "active_run_checkout_mode",
                            "--unset-metadata",
                            "active_run_archive_path",
                            "--unset-metadata",
                            "active_run_target_checkout",
                            "--unset-metadata",
                            "active_run_source_checkout",
                            "--unset-metadata",
                            "active_run_worktree_checkout",
                        ],
                        "cwd": str(tmp_path / "beads"),
                        "stdin": "",
                        "beads_password_present": True,
                    },
                    {
                        "argv": [
                            "close",
                            "central-5ik.4",
                            "--reason",
                            (
                                "AFK run "
                                f"{run.run_id} succeeded at commit abc123def456"
                            ),
                        ],
                        "cwd": str(tmp_path / "beads"),
                        "stdin": "",
                        "beads_password_present": True,
                    },
                ],
            )
            self.assertNotIn(
                secret_value,
                transcript_path.read_text(encoding="utf-8"),
            )

    def test_run_failure_comments_and_clears_active_state(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client, transcript_path, secret_value = self.client(tmp_path)
            run = self.run_state(tmp_path)

            with self.transcript_env(transcript_path):
                client.record_failure(
                    run,
                    interpreted_exit_code=42,
                    failure_summary="Case command exited 42",
                )

            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(len(transcript), 2)
            self.assertEqual(
                transcript[0]["argv"],
                ["comment", "central-5ik.4", "--stdin"],
            )
            self.assertEqual(transcript[0]["cwd"], str(tmp_path / "beads"))
            self.assertTrue(transcript[0]["beads_password_present"])
            self.assertIn("AFK run failed", transcript[0]["stdin"])
            self.assertIn(run.run_id, transcript[0]["stdin"])
            self.assertIn("Case command exited 42", transcript[0]["stdin"])
            self.assertIn(f"Archive: {run.archive_path}", transcript[0]["stdin"])
            self.assertIn("Next action:", transcript[0]["stdin"])
            self.assertEqual(
                transcript[1],
                {
                    "argv": [
                        "update",
                        "central-5ik.4",
                        "--set-metadata",
                        f"last_afk_run_id={run.run_id}",
                        "--set-metadata",
                        "last_afk_run_result=failure",
                        "--set-metadata",
                        "last_afk_run_exit_code=42",
                        "--set-metadata",
                        "last_afk_run_branch=agent/central-5ik.4",
                        "--set-metadata",
                        f"last_afk_run_archive_path={run.archive_path}",
                        "--unset-metadata",
                        "active_run_id",
                        "--unset-metadata",
                        "active_run_branch",
                        "--unset-metadata",
                        "active_run_checkout_mode",
                        "--unset-metadata",
                        "active_run_archive_path",
                        "--unset-metadata",
                        "active_run_target_checkout",
                        "--unset-metadata",
                        "active_run_source_checkout",
                        "--unset-metadata",
                        "active_run_worktree_checkout",
                    ],
                    "cwd": str(tmp_path / "beads"),
                    "stdin": "",
                    "beads_password_present": True,
                },
            )
            transcript_text = transcript_path.read_text(encoding="utf-8")
            self.assertNotIn(secret_value, transcript_text)

    def test_cli_lifecycle_success_records_metadata_and_closes(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            commit_sha = self.init_target_repo(target_repo)
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = self.write_fake_case(tmp_path)
            bead_json = tmp_path / "bead.json"
            self.write_eligible_bead(bead_json, target_repo, "central-life.1")
            client, transcript_path, secret_value = self.client(tmp_path)

            result = run_cli(
                "run",
                "--bead",
                "central-life.1",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--bd-command",
                client.bd_command,
                "--beads-workspace",
                str(client.beads_workspace),
                "--beads-password-file",
                str(client.beads_password_file),
                "--beads-lifecycle",
                "--close-bead-on-success",
                env={"FAKE_BD_TRANSCRIPT": str(transcript_path)},
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(len(transcript), 3)
            self.assertEqual(transcript[0]["argv"][:2], ["update", "central-life.1"])
            start_metadata = self.metadata_from_update(transcript[0]["argv"])
            run_id = start_metadata["active_run_id"]
            self.assertTrue(run_id.startswith("run-bead-central-life.1-"))
            self.assertEqual(
                start_metadata,
                {
                    "active_run_id": run_id,
                    "active_run_branch": "agent/central-life.1",
                    "active_run_checkout_mode": "direct",
                    "active_run_archive_path": str(state_dir / "runs" / run_id),
                    "active_run_target_checkout": str(target_repo),
                    "active_run_source_checkout": str(target_repo),
                },
            )
            success_metadata = self.metadata_from_update(transcript[1]["argv"])
            self.assertEqual(
                success_metadata,
                {
                    "last_afk_run_id": run_id,
                    "last_afk_run_result": "success",
                    "last_afk_run_exit_code": "0",
                    "last_afk_run_commit": commit_sha,
                    "last_afk_run_branch": "agent/central-life.1",
                    "last_afk_run_archive_path": str(state_dir / "runs" / run_id),
                },
            )
            self.assertIn(
                "active_run_id",
                self.unset_metadata_from_update(transcript[1]["argv"]),
            )
            self.assertEqual(
                transcript[2]["argv"],
                [
                    "close",
                    "central-life.1",
                    "--reason",
                    f"AFK run {run_id} succeeded at commit {commit_sha}",
                ],
            )
            self.assertNotIn(secret_value, transcript_path.read_text(encoding="utf-8"))
            self.assert_secret_not_in_tree(state_dir, secret_value)

    def test_cli_lifecycle_failure_comments_and_clears_after_case_nonzero(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "target"
            self.init_target_repo(target_repo)
            state_dir = tmp_path / ".automation-simple"
            case_checkout = tmp_path / "workos-case"
            case_checkout.mkdir()
            fake_case = self.write_fake_case(tmp_path, exit_code=42)
            bead_json = tmp_path / "bead.json"
            self.write_eligible_bead(bead_json, target_repo, "central-life.2")
            client, transcript_path, secret_value = self.client(tmp_path)

            result = run_cli(
                "run",
                "--bead",
                "central-life.2",
                "--bead-json",
                str(bead_json),
                "--state-dir",
                str(state_dir),
                "--case-checkout",
                str(case_checkout),
                "--case-command",
                str(fake_case),
                "--bd-command",
                client.bd_command,
                "--beads-workspace",
                str(client.beads_workspace),
                "--beads-password-file",
                str(client.beads_password_file),
                "--beads-lifecycle",
                env={"FAKE_BD_TRANSCRIPT": str(transcript_path)},
            )

            self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
            self.assertEqual(len(transcript), 3)
            start_metadata = self.metadata_from_update(transcript[0]["argv"])
            run_id = start_metadata["active_run_id"]
            self.assertEqual(
                transcript[1]["argv"],
                ["comment", "central-life.2", "--stdin"],
            )
            self.assertIn("AFK run failed", transcript[1]["stdin"])
            self.assertIn(run_id, transcript[1]["stdin"])
            self.assertIn("Case command exited 42", transcript[1]["stdin"])
            self.assertIn(str(state_dir / "runs" / run_id), transcript[1]["stdin"])
            failure_metadata = self.metadata_from_update(transcript[2]["argv"])
            self.assertEqual(
                failure_metadata,
                {
                    "last_afk_run_id": run_id,
                    "last_afk_run_result": "failure",
                    "last_afk_run_exit_code": "42",
                    "last_afk_run_branch": "agent/central-life.2",
                    "last_afk_run_archive_path": str(state_dir / "runs" / run_id),
                },
            )
            self.assertIn(
                "active_run_id",
                self.unset_metadata_from_update(transcript[2]["argv"]),
            )
            self.assertNotIn(secret_value, transcript_path.read_text(encoding="utf-8"))
            self.assert_secret_not_in_tree(state_dir, secret_value)
