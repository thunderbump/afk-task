from __future__ import annotations

import os
import subprocess
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ApplyCasePatchesScriptTest(unittest.TestCase):
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
