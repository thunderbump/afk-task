from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApplyCasePatchesScriptTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def init_patch_fixture(self, root: Path) -> tuple[Path, Path]:
        case_repo = root / "case-source"
        patch_dir = root / "patches"
        case_repo.mkdir()
        patch_dir.mkdir()
        self.git(case_repo, "init")
        self.git(case_repo, "config", "user.email", "test@example.com")
        self.git(case_repo, "config", "user.name", "Test User")
        (case_repo / "case.txt").write_text("before\n", encoding="utf-8")
        self.git(case_repo, "add", "case.txt")
        self.git(case_repo, "commit", "-m", "Initial Case fixture")
        (case_repo / "case.txt").write_text("after\n", encoding="utf-8")
        self.git(case_repo, "add", "case.txt")
        self.git(case_repo, "commit", "-m", "Update Case fixture")
        patch = subprocess.run(
            ["git", "format-patch", "-1", "--stdout"],
            cwd=case_repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        (patch_dir / "0001-update-case-fixture.patch").write_text(
            patch.stdout,
            encoding="utf-8",
        )
        self.git(case_repo, "reset", "--hard", "HEAD~1")
        return case_repo, patch_dir

    def test_requires_explicit_case_checkout_configuration(self) -> None:
        env = os.environ.copy()
        env.pop("CASE_CHECKOUT", None)

        result = subprocess.run(
            [str(REPO_ROOT / "scripts" / "apply-case-patches.sh")],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("CASE_CHECKOUT", result.stderr)
        self.assertIn("--case-checkout", result.stderr)
        self.assertNotIn("/home/bump/Projects/automation", result.stderr)

    def test_apply_case_patches_is_idempotent(self) -> None:
        with TemporaryDirectory() as tmp:
            case_repo, patch_dir = self.init_patch_fixture(Path(tmp))
            patch_file = patch_dir / "0001-update-case-fixture.patch"
            patch_file.write_text(
                patch_file.read_text(encoding="utf-8").replace(
                    "Subject: [PATCH] Update Case fixture",
                    "Subject: [PATCH 1/2] Update Case fixture",
                ),
                encoding="utf-8",
            )

            first = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "apply-case-patches.sh"),
                    "--case-checkout",
                    str(case_repo),
                    "--patch-dir",
                    str(patch_dir),
                ],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual((case_repo / "case.txt").read_text(), "after\n")
            patch_file.write_text(
                patch_file.read_text(encoding="utf-8").replace(
                    "before",
                    "context-that-no-longer-matches",
                ),
                encoding="utf-8",
            )

            second = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "apply-case-patches.sh"),
                    "--case-checkout",
                    str(case_repo),
                    "--patch-dir",
                    str(patch_dir),
                ],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn(
                "already applied by subject: 0001-update-case-fixture.patch",
                second.stdout,
            )

    def test_setup_case_checkout_clones_and_prepares_checkout(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            case_source, patch_dir = self.init_patch_fixture(tmp_path)
            checkout = tmp_path / "prepared-case"
            bun_log = tmp_path / "bun.log"
            fake_bin = tmp_path / "bin"
            fake_bin.mkdir()
            fake_bun = fake_bin / "bun"
            fake_bun.write_text(
                "#!/usr/bin/env sh\n"
                "printf '%s:%s\\n' \"$PWD\" \"$*\" >> \"$BUN_LOG\"\n",
                encoding="utf-8",
            )
            fake_bun.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "BUN_LOG": str(bun_log),
            }

            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "setup-case-checkout.sh"),
                    "--case-checkout",
                    str(checkout),
                    "--case-repo",
                    str(case_source),
                    "--patch-dir",
                    str(patch_dir),
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((checkout / "case.txt").read_text(), "after\n")
            self.assertEqual(
                self.git(checkout, "config", "user.email").stdout.strip(),
                "agent@example.invalid",
            )
            self.assertEqual(
                self.git(checkout, "config", "user.name").stdout.strip(),
                "Automation Workflow",
            )
            self.assertIn(f"export CASE_CHECKOUT='{checkout}'", result.stdout)
            bun_calls = bun_log.read_text(encoding="utf-8")
            self.assertIn(f"{checkout}:install", bun_calls)
            self.assertIn(f"{checkout}:run generate:assets", bun_calls)

    def test_actual_case_patches_remove_phase_status_self_transitions(self) -> None:
        case_source = REPO_ROOT / ".external" / "workos-case"
        if not case_source.is_dir():
            self.skipTest("external workos/case checkout is not available")

        first_patch = subprocess.run(
            [
                "git",
                "rev-list",
                "--max-count=1",
                "--grep=feat(run): add runtime module injection",
                "HEAD",
            ],
            cwd=case_source,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if first_patch.returncode != 0 or not first_patch.stdout.strip():
            self.skipTest("external workos/case checkout does not include local patch commits")

        with TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "case-checkout"
            clone = subprocess.run(
                ["git", "clone", "--quiet", str(case_source), str(checkout)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(clone.returncode, 0, clone.stdout + clone.stderr)
            self.git(checkout, "reset", "--hard", f"{first_patch.stdout.strip()}^")
            self.git(checkout, "config", "user.email", "test@example.com")
            self.git(checkout, "config", "user.name", "Test User")

            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "apply-case-patches.sh"),
                    "--case-checkout",
                    str(checkout),
                ],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            implementer_prompt = (checkout / "agents" / "implementer.md").read_text(
                encoding="utf-8"
            )
            verifier_prompt = (checkout / "agents" / "verifier.md").read_text(
                encoding="utf-8"
            )
            reviewer_prompt = (checkout / "agents" / "reviewer.md").read_text(
                encoding="utf-8"
            )
            closer_prompt = (checkout / "agents" / "closer.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(
                "ca status <task.json> status implementing", implementer_prompt
            )
            self.assertNotIn("ca status <task.json> status verifying", verifier_prompt)
            self.assertNotIn("ca status <task.json> status reviewing", reviewer_prompt)
            self.assertNotIn("ca status <task.json> status closing", closer_prompt)
