# Automation Simple Spike

Throwaway prototype for a simpler unattended automation shape:

```text
Beads selects and gates work -> Case owns workflow/review/PR -> Sandcastle sandboxes Case agent runs
```

This is not production code. It is a runnable spike to test whether the existing
automation coordinator can shrink by leaning on normal Case and Sandcastle
boundaries.

## Command

```sh
python3 -m automation_simple_spike run --bead <bead-id>
```

For a bounded native Case plumbing test that does not spawn model-backed phase
agents, pass:

```sh
python3 -m automation_simple_spike run --bead <bead-id> --case-dry-run
```

To pass a Case runtime adapter module through to native Case, pass:

```sh
python3 -m automation_simple_spike run --bead <bead-id> --case-runtime-module <path>
```

To prepare native Case's default Pi transport to use a local Codex ChatGPT
session token, pass:

```sh
python3 -m automation_simple_spike run \
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
python3 -m automation_simple_spike run \
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
python3 -m automation_simple_spike run \
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
scripts/apply-case-patches.sh
```

By default the script applies `patches/workos-case/*.patch` to
`/home/bump/Projects/automation/.automation/cache/workos-case`. Set
`CASE_CHECKOUT=/path/to/case` to apply it elsewhere.

By default the command reads central Beads with:

```sh
bd show <bead-id> --json
```

from `/home/bump/Projects/beads`, loading `BEADS_DOLT_PASSWORD` only into the
`bd` subprocess environment from
`/home/bump/Projects/beads/secrets/dolt_beads_password.txt`. The password is not
written into spike state, task files, logs, or Case environment.

For tests and smoke runs, pass `--bead-json <path>` or fake `--bd-command`.

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
5. Write Case project state under the spike state dir:
   `.automation-simple/case-data/projects.json`.
6. Write a run request under:
   `.automation-simple/runs/<run-id>/execution-request.json`.
7. Invoke native Case through:

```sh
bun src/index.ts run --task <task-json> --mode unattended
```

with `CASE_DATA_DIR`, `XDG_CONFIG_HOME`, and `HOME` pointed at the spike-owned
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
during dry-run, the spike archives that native copy in the run directory as
`native-dry-run-task.json` and restores the generated task JSON in the target
repo.

## Cron Shape

The target cron entry is one bead per tick, with an external lock if needed:

```cron
*/15 * * * * cd /home/bump/Projects/automation-simple-spike && /usr/bin/python3 -m automation_simple_spike run --bead central-abc.1 >> .automation-simple/logs/run-$(date +\%F).log 2>&1
```

For a real overnight runner, add task selection ahead of this command or have
cron pass a bead id selected by Beads. This spike intentionally starts with a
single explicit bead id.

## Why This Is Simpler

The current automation repo owns Beads selection, review worktree creation,
run archives, Sandcastle backend invocation, host validation, review gate state,
and later Case handoff. This spike removes most of that coordinator surface:

- Beads remains the source of task readiness and policy metadata.
- Case receives a native task file and owns scout, implement, verify, review,
  close, retrospective, event logs, and PR fields.
- Sandcastle becomes a Case runtime adapter, not a separate coordinator backend.
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
the same interface and call Sandcastle from each Case phase.

The local Case patches add `run --runtime-module <path>`, which dynamically
imports a module exporting `createCaseRuntime()`, a default runtime factory, or
a default runtime object. They also keep task status at `closing` when the close
phase completes without a PR URL, and preserve detailed `tested`/`reviewed`
evidence marker files when event projections notice a completed verify or
review phase. Pi remains the default runtime when no module is supplied.

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

The existing automation repo already uses this shape successfully. The narrow
plug-in point is therefore a Case runtime adapter that converts each
`SpawnAgentOptions` prompt into one Sandcastle `run()` invocation and parses the
agent stdout back into Case's `SpawnAgentResult`.

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
smaller than the current coordinator, but replacement depends on proving a real
Case `CaseAgentRuntime` backed by Sandcastle and confirming Case can safely own
branch, validation, PR, and comment creation for these Beads-driven tasks.

## Remaining Gaps

- Implement and compile the real Case `SandcastleRuntimeAdapter`.
- Decide whether to upstream Case `--runtime-module` or keep carrying the local
  patch process in `patches/workos-case/`.
- Confirm Case can create/use the intended review branch for both branch
  policies.
- Map Case final status, PR URL, PR number, and comments back to Beads metadata.
- Add host-side locking or Beads `active_run_id` mutation around cron execution.
- Add task selection for `ready-for-agent` beads instead of only `--bead`.
- Run a real Sandcastle-backed Case run.
