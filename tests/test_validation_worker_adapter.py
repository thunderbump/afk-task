from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from automation_simple_spike.validation_worker_adapter import main


class ValidationWorkerAdapterTests(unittest.TestCase):
    def test_local_worker_writes_result_with_secret_stripped_env(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            request_path = self.write_request(tmp_path)
            record_path = tmp_path / "record.json"
            worker = tmp_path / "worker.py"
            worker.write_text(
                "\n".join(
                    [
                        "import json, os, sys",
                        "from pathlib import Path",
                        "request = Path(sys.argv[sys.argv.index('--request') + 1])",
                        "payload = json.loads(request.read_text(encoding='utf-8'))",
                        "Path(payload['evidence_dir'], 'result.json').write_text(json.dumps({'status': 'passed'}) + '\\n', encoding='utf-8')",
                        "Path(sys.argv[sys.argv.index('--record') + 1]).write_text(json.dumps({'env': {k: os.environ.get(k) for k in ['BEADS_DIR', 'OPENAI_API_KEY', 'PI_CODING_AGENT_DIR', 'VALIDATION_WORKER_REQUEST']}}), encoding='utf-8')",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            old_env = os.environ.copy()
            try:
                os.environ.update(
                    {
                        "BEADS_DIR": "secret-beads",
                        "OPENAI_API_KEY": "secret-openai",
                        "PI_CODING_AGENT_DIR": "secret-pi",
                    }
                )
                result = main(
                    [
                        "--transport",
                        "local",
                        "--command",
                        f"{sys.executable} {worker} --record {record_path}",
                        "--request",
                        str(request_path),
                    ]
                )
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertEqual(result, 0)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertIsNone(record["env"]["BEADS_DIR"])
            self.assertIsNone(record["env"]["OPENAI_API_KEY"])
            self.assertIsNone(record["env"]["PI_CODING_AGENT_DIR"])
            self.assertEqual(record["env"]["VALIDATION_WORKER_REQUEST"], str(request_path))

    def test_remote_transport_reports_dispatch_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            request_path = self.write_request(tmp_path)
            result = main(
                [
                    "--transport",
                    "remote",
                    "--command",
                    "definitely-missing-validation-dispatcher",
                    "--request",
                    str(request_path),
                ]
            )
            self.assertEqual(result, 127)
            evidence = json.loads((tmp_path / "evidence" / "result.json").read_text())
            self.assertEqual(evidence["failure_category"], "remote_dispatch_failure")

    def test_timeout_is_reported_distinctly(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            request_path = self.write_request(tmp_path)
            result = main(
                [
                    "--command",
                    f"{sys.executable} -c 'import time; time.sleep(2)'",
                    "--request",
                    str(request_path),
                    "--timeout-seconds",
                    "0.1",
                ]
            )
            self.assertEqual(result, 124)
            evidence = json.loads((tmp_path / "evidence" / "result.json").read_text())
            self.assertEqual(evidence["failure_category"], "timeout")

    def test_missing_evidence_is_distinct_from_worker_nonzero(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            request_path = self.write_request(tmp_path)
            missing_result = main(
                [
                    "--command",
                    f"{sys.executable} -c 'pass'",
                    "--request",
                    str(request_path),
                ]
            )
            self.assertEqual(missing_result, 1)
            evidence = json.loads((tmp_path / "evidence" / "result.json").read_text())
            self.assertEqual(evidence["failure_category"], "missing_evidence")

            request_path = self.write_request(tmp_path, evidence_name="evidence-nonzero")
            nonzero_result = main(
                [
                    "--command",
                    f"{sys.executable} -c 'import sys; sys.exit(7)'",
                    "--request",
                    str(request_path),
                ]
            )
            self.assertEqual(nonzero_result, 7)
            evidence = json.loads(
                (tmp_path / "evidence-nonzero" / "result.json").read_text()
            )
            self.assertEqual(evidence["failure_category"], "worker_nonzero")

    def write_request(self, tmp_path: Path, *, evidence_name: str = "evidence") -> Path:
        evidence_dir = tmp_path / evidence_name
        evidence_dir.mkdir()
        request_path = tmp_path / f"{evidence_name}.request.json"
        request_path.write_text(
            json.dumps(
                {
                    "bead_id": "central-test.worker",
                    "evidence_dir": str(evidence_dir),
                    "timeout_seconds": 30,
                }
            ),
            encoding="utf-8",
        )
        return request_path


if __name__ == "__main__":
    unittest.main()
