# Automation Simple Workflow

Workflow under development for unattended automation:

```text
Beads selects and gates work -> Case owns workflow/review/PR -> Sandcastle sandboxes Case agent runs
```

This repo develops the unattended workflow that lets the existing automation
coordinator shrink by leaning on normal Case and Sandcastle boundaries.

## What This Pulls Together

- Beads is the task source. The wrapper reads one ready bead from the central
  Beads workspace or a fixture JSON file, validates AFK metadata, and keeps
  Beads credentials out of Case.
- Case is the workflow engine. This repo generates normal Case task and project
  state, then invokes an external patched `workos/case` checkout to run the
  unattended pipeline.
- The current runtime direction is to containerize the whole Case run so Case
  keeps ownership of inference, phase orchestration, review, and close. The
  checked-in Sandcastle scaffold remains an exploratory per-phase sandbox option;
  the raw Sandcastle repo and its dependencies are not vendored here.
- Codex/Pi adapter state is generated locally only when
  `--case-codex-session` is enabled. The wrapper writes non-secret model
  selection under `.automation-simple/` and passes the access token only to the
  child Case process.
- The stitch point is intentionally small: Beads chooses the work, this wrapper
  writes Case-compatible state, and Case owns the workflow inside the selected
  runner boundary.

## Setup

This repo should be push-ready for `git@github.com:thunderbump/afk-task.git`
without raw upstream checkouts, Sandcastle sources, `node_modules`, or local run
state.

Prepare the external Case checkout with the setup script:

```sh
scripts/setup-case-checkout.sh
export CASE_CHECKOUT="$(pwd)/.external/workos-case"
```

By default, the script clones `https://github.com/workos/case.git` into
`.external/workos-case`, which is ignored by Git. It installs Case dependencies,
sets local-only Git identity for patch application if needed, applies
`patches/workos-case/*.patch` idempotently, and refreshes Case's generated
package asset list.

To keep Case somewhere else, pass the checkout path explicitly:

```sh
scripts/setup-case-checkout.sh --case-checkout ../workos-case
export CASE_CHECKOUT="$(cd ../workos-case && pwd)"
```

The runner itself still requires `CASE_CHECKOUT` or `--case-checkout` so it does
not depend on one machine's local directory layout. The setup script is only the
repeatable way to create and refresh that external checkout.

For Sandcastle work, keep dependencies in the external Case checkout or in a
separate adapter package. The scaffold at
`scaffolds/case-sandcastle-runtime-adapter.ts` imports `@ai-hero/sandcastle`
and `@ai-hero/sandcastle/sandboxes/podman`; do not copy the raw Sandcastle repo
or its `node_modules` into this repo.

Generated local state is ignored by Git: `.automation-simple/`, `.case/`,
`node_modules/`, `workos-case/`, `sandcastle/`, and `.external/` should remain
local-only.

## Command

```sh
python3 -m automation_simple_workflow run --bead <bead-id>
```

The runner needs a patched Case checkout. Either export it once:

```sh
export CASE_CHECKOUT=/path/to/workos-case
python3 -m automation_simple_workflow run --bead <bead-id>
```

or pass it per invocation:

```sh
python3 -m automation_simple_workflow run \
  --bead <bead-id> \
  --case-checkout /path/to/workos-case
```

For a bounded native Case plumbing test that does not spawn model-backed phase
agents, pass:

```sh
python3 -m automation_simple_workflow run --bead <bead-id> --case-dry-run
```

To pass a Case runtime adapter module through to native Case, pass:

```sh
python3 -m automation_simple_workflow run --bead <bead-id> --case-runtime-module <path>
```

To prepare native Case's default Pi transport to use a local Codex ChatGPT
session token, pass:

```sh
python3 -m automation_simple_workflow run \
  --bead <bead-id> \
  --case-codex-session
```

By default this reads `~/.codex/auth.json` at runtime, validates that it is a
non-expired ChatGPT Codex session token, writes a non-secret Pi model alias under
`.automation-simple/pi-codex/models.json`, writes Case model selection under
`.automation-simple/case-data/config.json`, and passes the access token only to
the child Case process as `OPENAI_API_KEY`. The auth file is not copied, and no
token value is written by this wrapper.

Useful wrapper options:

```sh
python3 -m automation_simple_workflow run \
  --bead <bead-id> \
  --case-codex-session \
  --codex-auth-file /path/to/auth.json \
  --codex-model gpt-5.5 \
  --case-codex-scout-only
```

`--case-codex-scout-only` is a bounded proof mode: it configures Case's `scout`
model as `openai/<model>` and intentionally sets the default model to an invalid
sentinel so later phases cannot silently run. Live runs with
`--case-codex-session` are human-gated because they use local ChatGPT/Codex
session credentials and may call the model. The test suite covers this path only
with fake auth fixtures and fake Case commands.

This repo includes one local proof module:

```sh
python3 -m automation_simple_workflow run \
  --bead central-hmd.5 \
  --case-runtime-module runtime_modules/host-monitor-dashboard-runtime.mjs
```

`runtime_modules/host-monitor-dashboard-runtime.mjs` exports
`createCaseRuntime()` for native Case's `--runtime-module` seam. Its
implementer phase writes a tiny Python/static HTML dashboard fixture into the
target repo cwd, its verifier phase runs
`python3 -m unittest discover -s tests`, and every spawn appends evidence to
`<target_repo>/.case/runtime-module-spawns.log`.

The Case side of this proof currently lives as a local patch series, not a fork:

```sh
scripts/apply-case-patches.sh --case-checkout /path/to/workos-case
```

The script applies `patches/workos-case/*.patch` to the external checkout passed
with `--case-checkout` or `CASE_CHECKOUT`. It intentionally has no default so a
fresh clone does not depend on one machine's local checkout.

By default the command reads central Beads with:

```sh
bd show <bead-id> --json
```

from `/home/bump/Projects/beads`, loading `BEADS_DOLT_PASSWORD` only into the
`bd` subprocess environment from
`/home/bump/Projects/beads/secrets/dolt_beads_password.txt`. The password is not
written into wrapper state, task files, logs, or Case environment.

For tests and smoke runs, pass `--bead-json <path>` or fake `--bd-command`.

## Containerized Runner

The simplest isolation path is to run the whole workflow inside a container and
let Case keep its normal Pi/Codex inference path. This avoids making Sandcastle
and Case both own agent spawning for every phase.

Build and run the local runner image with a fixture bead:

```sh
scripts/run-containerized-workflow.sh --build --mount /path/to/target-repo --mount /path/to/fixture-dir -- \
  run \
  --bead <bead-id> \
  --bead-json /path/to/fixture-dir/bead.json \
  --case-checkout "$(pwd)/.external/workos-case"
```

The script mounts this workflow checkout at the same absolute path inside the
container. Use `--mount /path/to/target-repo` for any target repo or fixture path
referenced by the bead metadata. By default the container entrypoint prepares
`.external/workos-case` if it is missing. Pass `--setup-case` to refresh that
checkout on every run, or `--skip-case-setup` for fake-Case smoke tests. Use
`--mount-ro /path/to/secret-dir` for read-only credential mounts. On SELinux
hosts that require relabeling, use `--volume-suffix :Z` or `--volume-suffix :z`
for writable mounts and `--ro-volume-suffix :ro,Z` or `--ro-volume-suffix :ro,z`
for read-only mounts.

The container image does not currently install or configure `bd`, so central
Beads runs need either a fixture JSON via `--bead-json` or a later explicit
Beads workspace/CLI mount. Live Codex-session runs likewise need an explicit
read-only auth mount plus `--case-codex-session --codex-auth-file <mounted-path>`.

For a no-network synthetic proof, mount a temp directory containing the target
repo, bead JSON, fake Case command, and fake Case checkout directory:

```sh
scripts/run-containerized-workflow.sh --build --skip-case-setup --mount "$tmp" -- \
  run \
  --bead central-smoke.1 \
  --bead-json "$tmp/bead.json" \
  --state-dir "$tmp/.automation-simple" \
  --case-checkout "$tmp/workos-case" \
  --case-command "$tmp/fake-case"
```

This proves the container boundary and workflow handoff without Beads secrets,
Codex/Pi credentials, GitHub credentials, or a live Case process.

The same synthetic path is available as:

```sh
scripts/container-smoke.sh
```

## Fresh Clone Smoke

After cloning this repo and preparing Case, run the local validation commands:

```sh
python3 -m unittest discover -s tests
scripts/smoke.sh
```

`scripts/smoke.sh` uses a synthetic bead, a temporary target git repo, and a
fake Case command. It does not read Beads secrets and does not call Codex, Pi, or
GitHub.

To prove the native Case dry-run path from a fresh clone without live model
calls, create a temporary target repo and fixture bead:

```sh
tmp="$(mktemp -d)"
target="$tmp/target"
mkdir -p "$target"
git -C "$target" init
git -C "$target" config user.email "agent@example.invalid"
git -C "$target" config user.name "Automation Workflow"
printf 'native dry-run target\n' > "$target/README.md"
git -C "$target" add README.md
git -C "$target" commit -m "Initial target"
git -C "$target" branch -M main

cat > "$tmp/bead.json" <<EOF
{
  "id": "central-audit.1",
  "title": "Audit native dry-run handoff",
  "description": "Exercise native Case dry-run from a fresh workflow clone.",
  "status": "open",
  "labels": ["project:automation", "ready-for-agent"],
  "metadata": {
    "afk_enabled": true,
    "afk_runner": "codex",
    "target_repo": "local/audit",
    "target_repo_path": "$target",
    "target_base_branch": "main",
    "branch_policy": "independent",
    "validation_command": "true"
  }
}
EOF

python3 -m automation_simple_workflow run \
  --bead central-audit.1 \
  --bead-json "$tmp/bead.json" \
  --state-dir "$tmp/.automation-simple" \
  --case-checkout "$CASE_CHECKOUT" \
  --case-dry-run
```

That command invokes native Case with `--dry-run`, writes task state under the
temporary target repo, and should finish without live Codex/Pi credentials.

## Current Flow

1. Load one bead by id.
2. Validate the current runnable Agent Task contract:
   `afk_enabled`, `afk_runner`, `target_repo`, `target_repo_path`,
   `target_base_branch`, `branch_policy`, `validation_command`, open status,
   `ready-for-agent`, a `project:<slug>` label, no open blocking dependencies,
   and no `active_run_id`.
3. Derive the review branch from metadata:
   `agent/<bead-id>` for `independent`, or `agent/<workstream_id>` for
   `shared-sequential`.
4. Write normal repo-local Case task files:
   `<target_repo_path>/.case/tasks/active/<bead-id>.md`
   and `<bead-id>.task.json`.
5. Write Case project state under the wrapper state dir:
   `.automation-simple/case-data/projects.json`.
6. Write a run request under:
   `.automation-simple/runs/<run-id>/execution-request.json`.
7. Invoke native Case through:

```sh
bun src/index.ts run --task <task-json> --mode unattended
```

with `CASE_DATA_DIR`, `XDG_CONFIG_HOME`, and `HOME` pointed at the wrapper-owned
Case data dir. Beads environment variables are removed before invoking Case.
Ambient `OPENAI_API_KEY` and `PI_CODING_AGENT_DIR` are also removed unless
`--case-codex-session` is enabled. In wrapper mode, `PI_CODING_AGENT_DIR` points
to `.automation-simple/pi-codex` and `OPENAI_API_KEY` is populated only in the
child environment from the validated Codex auth file.
The wrapper also writes a non-secret `ca` shim under
`.automation-simple/case-bin/ca`, records it in `execution-request.json`, and
prepends that directory to Case's child `PATH` so phase agents can call the same
local Case checkout without a global `ca` install. The shim preserves the
agent's current working directory and invokes the Case entrypoint by absolute
path, so relative task paths and repo-local `.case/active` markers resolve in
the target repo.
When `--case-runtime-module <path>` is passed, the command appends Case's native
`--runtime-module <path>` flag and records the resolved path in
`execution-request.json`. When `--case-dry-run` is passed, the command appends
Case's native `--dry-run` flag. If native Case mutates the generated task JSON
during dry-run, the wrapper archives that native copy in the run directory as
`native-dry-run-task.json` and restores the generated task JSON in the target
repo.

## Cron Shape

The target cron entry is one bead per tick, with an external lock if needed:

```cron
*/15 * * * * cd /path/to/automation-simple-workflow && /usr/bin/python3 -m automation_simple_workflow run --bead central-abc.1 >> .automation-simple/logs/run-$(date +\%F).log 2>&1
```

For a real overnight runner, add task selection ahead of this command or have
cron pass a bead id selected by Beads. The current runner starts with a single
explicit bead id.

## Why This Is Simpler

The current automation repo owns Beads selection, review worktree creation,
run archives, Sandcastle backend invocation, host validation, review gate state,
and later Case handoff. This workflow removes most of that coordinator surface:

- Beads remains the source of task readiness and policy metadata.
- Case receives a native task file and owns scout, implement, verify, review,
  close, retrospective, event logs, and PR fields.
- The container runner can become the first sandbox boundary for the whole Case
  run. Sandcastle remains available for a later per-phase adapter if the extra
  isolation is worth the additional ownership overlap.
- Local state is limited to generated Case config plus a small execution request
  proving what was handed off.

## Case Findings

Local Case normal usage is task-file driven:

```sh
bun src/index.ts run --task <task-json> --mode unattended
```

Case stores mutable task/event state under the target repo's `.case/` directory
and resolves repo metadata from `projects.json` under `CASE_DATA_DIR`.

The useful extension point is `CaseAgentRuntime`:

```ts
interface CaseAgentRuntime {
  spawn(options: SpawnAgentOptions): Promise<SpawnAgentResult>;
  createTools(agentName: string, cwd: string, policy?: WorkspacePolicy): unknown[];
  abort(): void;
}
```

Case currently defaults to `PiRuntimeAdapter`. A Sandcastle adapter can satisfy
the same interface and call Sandcastle from each Case phase, but that makes both
Case and Sandcastle participate in agent spawning and inference ownership. The
containerized runner avoids that overlap by moving the boundary outside the
whole Case process.

The local Case patches add `run --runtime-module <path>`, which dynamically
imports a module exporting `createCaseRuntime()`, a default runtime factory, or
a default runtime object. They also keep task status at `closing` when the close
phase completes without a PR URL, preserve detailed `tested`/`reviewed`
evidence marker files when event projections notice a completed verify or
review phase, and merge task JSON projections without regressing externally
recorded evidence flags or completed agent phases. Pi remains the default
runtime when no module is supplied.

## Sandcastle Findings

Sandcastle's normal API is one `run()` call:

```ts
await run({
  agent: codex(model),
  sandbox: podman({ imageName, mounts }),
  cwd: options.cwd,
  branchStrategy: { type: "head" },
  prompt: options.prompt,
  logging: { type: "file", path: logPath },
});
```

The existing automation repo already uses this shape successfully. If per-phase
isolation becomes necessary, the narrow plug-in point is a Case runtime adapter
that converts each `SpawnAgentOptions` prompt into one Sandcastle `run()`
invocation and parses the agent stdout back into Case's `SpawnAgentResult`.
That path remains more complex than containerizing the whole Case run because it
splits inference ownership across two abstractions.

See `scaffolds/case-sandcastle-runtime-adapter.ts` for the intended seam.

## Run Tests

```sh
python3 -m unittest discover -s tests
```

## Smoke

The smoke path uses a temp target repo, temp bead JSON, and fake Case command.
It does not touch real Beads, secrets, Case, Sandcastle, or any target repo:

```sh
scripts/smoke.sh
```

## Recommendation

This should influence the next automation design, but it should not replace
`/home/bump/Projects/automation` yet. The bead intake and Case handoff are much
smaller than the current coordinator, and the next runtime proof should focus on
the containerized whole-Case runner before investing further in a Sandcastle
per-phase adapter.

## Remaining Gaps

- Prove the containerized whole-Case runner with native Case dry-run and then a
  real dashboard bead.
- Decide whether to upstream Case `--runtime-module` or keep carrying the local
  patch process in `patches/workos-case/`.
- Confirm Case can create/use the intended review branch for both branch
  policies.
- Map Case final status, PR URL, PR number, and comments back to Beads metadata.
- Revisit a real Sandcastle-backed Case run only if per-phase sandbox isolation
  is worth the extra adapter complexity.
- Add host-side locking or Beads `active_run_id` mutation around cron execution.
- Add task selection for `ready-for-agent` beads instead of only `--bead`.
