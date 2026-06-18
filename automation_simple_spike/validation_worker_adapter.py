from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

SANITIZED_ENV_KEYS = (
    "BEADS_DIR",
    "BEADS_DOLT_PASSWORD",
    "AUTOMATION_BEADS_WORKSPACE",
    "OPENAI_API_KEY",
    "PI_CODING_AGENT_DIR",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validation-worker-adapter")
    parser.add_argument("--request", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--transport", choices=("local", "remote"), default="local")
    parser.add_argument("--timeout-seconds", type=float)
    args = parser.parse_args(argv)

    request_path = Path(args.request)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"validation worker adapter could not read request: {error}", file=sys.stderr)
        return 2

    evidence_dir = Path(str(request.get("evidence_dir") or ""))
    if not evidence_dir:
        print("validation worker request is missing evidence_dir", file=sys.stderr)
        return 2
    evidence_dir.mkdir(parents=True, exist_ok=True)

    command = command_argv(args.command, request_path)
    env = sanitized_worker_env(request_path)
    timeout = args.timeout_seconds or timeout_from_request(request)

    try:
        result = subprocess.run(
            command,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as error:
        category = (
            "local_command_missing"
            if args.transport == "local"
            else "remote_dispatch_failure"
        )
        write_result(
            evidence_dir,
            status="failed",
            failure_category=category,
            message=str(error),
        )
        print(f"validation worker adapter failed: {error}", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired as error:
        write_result(
            evidence_dir,
            status="failed",
            failure_category="timeout",
            message=f"worker timed out after {timeout} seconds",
        )
        if error.stdout:
            print(error.stdout, end="")
        if error.stderr:
            print(error.stderr, end="", file=sys.stderr)
        return 124

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    result_path = evidence_dir / "result.json"
    if result.returncode != 0:
        category = (
            "remote_dispatch_failure"
            if args.transport == "remote"
            else "worker_nonzero"
        )
        if not result_path.is_file():
            write_result(
                evidence_dir,
                status="failed",
                failure_category=category,
                message=f"worker command exited {result.returncode}",
            )
        return result.returncode or 1

    if not result_path.is_file():
        write_result(
            evidence_dir,
            status="failed",
            failure_category="missing_evidence",
            message="worker command completed without writing result.json",
        )
        return 1

    return 0


def command_argv(command: str, request_path: Path) -> list[str]:
    if "{request}" in command:
        return shlex.split(command.format(request=str(request_path)))
    return [*shlex.split(command), "--request", str(request_path)]


def sanitized_worker_env(request_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in SANITIZED_ENV_KEYS:
        env.pop(key, None)
    env["VALIDATION_WORKER_REQUEST"] = str(request_path)
    return env


def timeout_from_request(request: dict[str, Any]) -> float | None:
    timeout = request.get("timeout_seconds")
    if timeout in (None, ""):
        return None
    try:
        return float(timeout)
    except (TypeError, ValueError):
        return None


def write_result(
    evidence_dir: Path,
    *,
    status: str,
    failure_category: str,
    message: str,
) -> None:
    payload = {
        "status": status,
        "failure_category": failure_category,
        "message": message,
    }
    (evidence_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
