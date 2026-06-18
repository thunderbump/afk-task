from __future__ import annotations

import json
import shlex
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_simple_spike.cli import compile_validation_metadata
from automation_simple_spike.validation_metadata import (
    worker_validation_command,
    worker_validation_transport,
)


class ValidationMetadataTests(unittest.TestCase):
    def test_remote_worker_metadata_compiles_adapter_command_with_same_request(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            issue = {
                "id": "central-remote.worker",
                "metadata": {
                    "target_repo": "local/test",
                    "target_repo_path": str(tmp_path / "target"),
                    "target_base_branch": "main",
                    "branch_policy": "independent",
                    "validation_mode": "worker",
                    "validation_worker": {
                        "transport": "remote",
                        "remote_command": "ssh validator run-worker",
                        "profile": "safe",
                        "timeout_seconds": 12,
                    },
                },
            }

            command = compile_validation_metadata(
                issue=issue,
                state_dir=tmp_path / "state",
                review_branch="agent/central-remote.worker",
                target_checkout_path=tmp_path / "target",
                target_source_checkout=tmp_path / "target",
                target_worktree_checkout=None,
            )

            request_path = tmp_path / "state" / "validation-requests" / "central-remote.worker.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            self.assertEqual(request["profile"], "safe")
            self.assertEqual(request["timeout_seconds"], 12)
            self.assertEqual(request["review_branch"], "agent/central-remote.worker")
            self.assertIn("automation_simple_spike.validation_worker_adapter", command)
            self.assertIn("--transport remote", command)
            self.assertIn("--command 'ssh validator run-worker'", command)
            self.assertIn(f"--request {shlex.quote(str(request_path))}", command)
            self.assertEqual(worker_validation_transport(issue["metadata"]), "remote")
            self.assertEqual(worker_validation_command(issue["metadata"]), "ssh validator run-worker")

    def test_flat_local_worker_metadata_compiles_adapter_command(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            issue = {
                "id": "central-local.worker",
                "metadata": {
                    "target_repo": "local/test",
                    "target_repo_path": str(tmp_path / "target"),
                    "target_base_branch": "main",
                    "branch_policy": "independent",
                    "validation_mode": "worker",
                    "validation_worker_transport": "local",
                    "validation_worker_local_command": "python3 worker.py",
                },
            }

            command = compile_validation_metadata(
                issue=issue,
                state_dir=tmp_path / "state",
                review_branch="agent/central-local.worker",
                target_checkout_path=tmp_path / "target",
                target_source_checkout=tmp_path / "target",
                target_worktree_checkout=None,
            )

            self.assertIn("--transport local", command)
            self.assertIn("--command 'python3 worker.py'", command)
            self.assertEqual(worker_validation_command(issue["metadata"]), "python3 worker.py")


if __name__ == "__main__":
    unittest.main()
