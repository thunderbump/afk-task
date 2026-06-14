from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULE = REPO_ROOT / "runtime_modules" / "host-monitor-dashboard-runtime.mjs"


class HostMonitorRuntimeModuleTest(unittest.TestCase):
    def test_runtime_implements_and_verifies_dashboard_fixture(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "host-monitor-dashboard"
            tests_dir = target_repo / "tests"
            tests_dir.mkdir(parents=True)
            (target_repo / "README.md").write_text(
                "# Host Monitor Dashboard\n\n"
                "Safe target repo for agent-workflow smoke tests.\n",
                encoding="utf-8",
            )
            (tests_dir / "test_smoke.py").write_text(
                "\n".join(
                    [
                        "import unittest",
                        "from pathlib import Path",
                        "",
                        "",
                        "class RepositorySmokeTest(unittest.TestCase):",
                        "    def test_readme_declares_fixture_purpose(self):",
                        "        readme = Path(__file__).resolve().parents[1] / 'README.md'",
                        "        text = readme.read_text(encoding='utf-8')",
                        "        self.assertIn('Host Monitor Dashboard', text)",
                        "        self.assertIn('agent-workflow smoke tests', text)",
                        "",
                        "",
                        "if __name__ == '__main__':",
                        "    unittest.main()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            driver = tmp_path / "invoke-runtime.mjs"
            driver.write_text(
                "\n".join(
                    [
                        "import { readFileSync } from 'node:fs';",
                        "import { pathToFileURL } from 'node:url';",
                        "",
                        "const runtimeModule = await import(pathToFileURL(process.argv[2]).href);",
                        "const cwd = process.argv[3];",
                        "const runtime = await runtimeModule.createCaseRuntime();",
                        "const implementer = await runtime.spawn({",
                        "  agentName: 'implementer',",
                        "  cwd,",
                        "  dataDir: cwd,",
                        "  prompt: 'Implement bead central-hmd.5',",
                        "  packageRoot: process.cwd(),",
                        "});",
                        "const verifier = await runtime.spawn({",
                        "  agentName: 'verifier',",
                        "  cwd,",
                        "  dataDir: cwd,",
                        "  prompt: 'Run validation',",
                        "  packageRoot: process.cwd(),",
                        "});",
                        "const scout = await runtime.spawn({",
                        "  agentName: 'scout',",
                        "  cwd,",
                        "  dataDir: cwd,",
                        "  prompt: 'Scout fixture',",
                        "  packageRoot: process.cwd(),",
                        "});",
                        "runtime.abort();",
                        "console.log(JSON.stringify({",
                        "  implementer: implementer.result,",
                        "  verifier: verifier.result,",
                        "  scout: scout.result,",
                        "  dashboardHtml: readFileSync(`${cwd}/dashboard.html`, 'utf-8'),",
                        "  runtimeLog: readFileSync(`${cwd}/.case/runtime-module-spawns.log`, 'utf-8'),",
                        "}));",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(driver), str(RUNTIME_MODULE), str(target_repo)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["implementer"]["status"], "completed")
            self.assertEqual(payload["verifier"]["status"], "completed")
            self.assertEqual(payload["scout"]["status"], "completed")
            self.assertIn("Overview", payload["dashboardHtml"])
            self.assertIn("Containers", payload["dashboardHtml"])
            self.assertIn("Services", payload["dashboardHtml"])
            self.assertIn("listening services", payload["dashboardHtml"])
            self.assertIn("implementer", payload["runtimeLog"])
            self.assertIn("verifier", payload["runtimeLog"])
            self.assertIn("scout", payload["runtimeLog"])

    def test_verifier_returns_failed_result_when_validation_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target_repo = tmp_path / "host-monitor-dashboard"
            tests_dir = target_repo / "tests"
            tests_dir.mkdir(parents=True)
            (tests_dir / "test_failure.py").write_text(
                "\n".join(
                    [
                        "import unittest",
                        "",
                        "",
                        "class FailingValidationTest(unittest.TestCase):",
                        "    def test_failure(self):",
                        "        self.fail('fixture failure')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            driver = tmp_path / "invoke-verifier.mjs"
            driver.write_text(
                "\n".join(
                    [
                        "import { pathToFileURL } from 'node:url';",
                        "",
                        "const runtimeModule = await import(pathToFileURL(process.argv[2]).href);",
                        "const cwd = process.argv[3];",
                        "const runtime = await runtimeModule.createCaseRuntime();",
                        "const verifier = await runtime.spawn({",
                        "  agentName: 'verifier',",
                        "  cwd,",
                        "  dataDir: cwd,",
                        "  prompt: 'Run validation',",
                        "  packageRoot: process.cwd(),",
                        "});",
                        "console.log(JSON.stringify(verifier.result));",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                ["node", str(driver), str(RUNTIME_MODULE), str(target_repo)],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(payload["artifacts"]["testsPassed"])
            self.assertIn("Validation failed", payload["error"])
            self.assertIn("fixture failure", payload["error"])


if __name__ == "__main__":
    unittest.main()
