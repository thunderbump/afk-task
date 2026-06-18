#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

target_repo="$tmp/target"
case_checkout="$tmp/workos-case"
state_dir="$tmp/.automation-simple"
mkdir -p "$target_repo" "$case_checkout"

git -C "$target_repo" init --initial-branch=main
git -C "$target_repo" config user.email "smoke@example.com"
git -C "$target_repo" config user.name "Smoke Test"
printf 'smoke target\n' >"$target_repo/README.md"
git -C "$target_repo" add README.md
git -C "$target_repo" commit -m "Initial smoke target"

fake_case="$tmp/fake-case"
cat >"$fake_case" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(sys.argv[0]).with_suffix(".json").write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "cwd": os.getcwd(),
            "case_data_dir": os.environ.get("CASE_DATA_DIR"),
            "beads_password": os.environ.get("BEADS_DOLT_PASSWORD"),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
chmod +x "$fake_case"

bead_json="$tmp/bead.json"
python3 - "$target_repo" "$bead_json" <<'PY'
import json
import sys
from pathlib import Path

target_repo = Path(sys.argv[1])
bead_json = Path(sys.argv[2])
bead_json.write_text(
    json.dumps(
        {
            "id": "central-smoke.1",
            "title": "Smoke simple automation workflow",
            "description": "Exercise the fake Case handoff path.",
            "status": "open",
            "labels": ["project:automation", "ready-for-agent"],
            "metadata": {
                "afk_enabled": True,
                "afk_runner": "codex",
                "target_repo": "local/smoke",
                "target_repo_path": str(target_repo),
                "target_base_branch": "main",
                "branch_policy": "independent",
                "validation_command": "true",
            },
        }
    ),
    encoding="utf-8",
)
PY

(
  cd "$repo_root"
  BEADS_DOLT_PASSWORD="should-not-reach-case" \
    python3 -m automation_simple_workflow run \
      --bead central-smoke.1 \
      --bead-json "$bead_json" \
      --state-dir "$state_dir" \
      --case-checkout "$case_checkout" \
      --case-command "$fake_case"
)

python3 - "$target_repo" "$state_dir" "$fake_case" <<'PY'
import json
import sys
from pathlib import Path

target_repo = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
fake_case = Path(sys.argv[3])

task = target_repo / ".case/tasks/active/central-smoke.1.task.json"
request = next((state_dir / "runs").glob("*/execution-request.json"))
archive_task = request.parent / "case-artifacts/.case/tasks/active/central-smoke.1.task.json"
record = json.loads(fake_case.with_suffix(".json").read_text(encoding="utf-8"))

assert task.is_file(), task
assert request.is_file(), request
assert archive_task.is_file(), archive_task
assert record["beads_password"] is None
print("smoke passed")
print(f"task={task}")
print(f"request={request}")
print(f"archive_task={archive_task}")
PY
