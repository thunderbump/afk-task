from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ContainerizedRunnerScriptTest(unittest.TestCase):
    def test_fake_engine_receives_build_and_run_arguments(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_engine = tmp_path / "fake-engine"
            log_path = tmp_path / "engine-calls.jsonl"
            mount_path = tmp_path / "target"
            readonly_path = tmp_path / "codex-auth"
            mount_path.mkdir()
            readonly_path.mkdir()
            fake_engine.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "import json, os, sys",
                        "with open(os.environ['ENGINE_LOG'], 'a', encoding='utf-8') as f:",
                        "    f.write(json.dumps(sys.argv[1:]) + '\\n')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            fake_engine.chmod(0o755)

            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "run-containerized-workflow.sh"),
                    "--engine",
                    str(fake_engine),
                    "--image",
                    "test-runner:local",
                    "--build",
                    "--skip-case-setup",
                    "--volume-suffix",
                    ":Z",
                    "--ro-volume-suffix",
                    ":ro,Z",
                    "--mount",
                    str(mount_path),
                    "--mount-ro",
                    str(readonly_path),
                    "--",
                    "run",
                    "--bead",
                    "central-test.1",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "ENGINE_LOG": str(log_path)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(calls[0][0], "build")
            self.assertIn("containers/case-runner/Containerfile", " ".join(calls[0]))
            self.assertEqual(calls[1][0:2], ["run", "--rm"])
            self.assertIn(f"{REPO_ROOT}:{REPO_ROOT}:Z", calls[1])
            self.assertIn(f"{mount_path}:{mount_path}:Z", calls[1])
            self.assertIn(f"{readonly_path}:{readonly_path}:ro,Z", calls[1])
            self.assertIn("WORKFLOW_SETUP_CASE=0", calls[1])
            image_index = calls[1].index("test-runner:local")
            self.assertEqual(
                calls[1][image_index:],
                ["test-runner:local", "run", "--bead", "central-test.1"],
            )

    def test_missing_container_engine_fails_clearly(self) -> None:
        with TemporaryDirectory() as tmp:
            fake_bin = Path(tmp)
            (fake_bin / "bash").symlink_to("/usr/bin/bash")
            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "run-containerized-workflow.sh"),
                    "--",
                    "run",
                    "--bead",
                    "central-test.1",
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "PATH": str(fake_bin)},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("no container engine found", result.stderr)
