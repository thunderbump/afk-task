# central-5ik.6 Current Workflow Proof

This proof uses central-3gj-style fixtures: a parent PRD-like bead plus child
beads that vary AFK readiness.

The current runner behavior is:

- Parent/PRD-like records are not runnable because they do not satisfy the AFK
  task contract, including `ready-for-agent` and required AFK metadata.
- Child records without `afk_enabled`/`afk_runner`, with `active_run_id`, or with
  open blocking dependencies stop during eligibility and do not write Case task
  state or invoke Case.
- A runnable child with complete AFK metadata reaches the native Case dry-run
  command shape:
  `src/index.ts run --task <task-json> --mode unattended --dry-run`.
- Direct checkout mode currently rejects a dirty target checkout before Case
  handoff with `target repo has uncommitted changes`. Worktree mode is the AFK
  path for running from a clean review checkout while leaving a dirty source
  checkout alone.

Regression coverage lives in `tests/test_current_workflow_proof.py`.
