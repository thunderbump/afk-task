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
- Decide how to inject the adapter into native Case without maintaining a fork.
- Confirm Case can create/use the intended review branch for both branch
  policies.
- Map Case final status, PR URL, PR number, and comments back to Beads metadata.
- Add host-side locking or Beads `active_run_id` mutation around cron execution.
- Add task selection for `ready-for-agent` beads instead of only `--bead`.
- Run a bounded real Case dry-run, then a real Sandcastle-backed Case run.
