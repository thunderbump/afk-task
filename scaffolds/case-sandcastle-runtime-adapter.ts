/*
 * PROTOTYPE SCAFFOLD ONLY.
 *
 * Intended home: Case, next to src/agent/adapters/pi-adapter.ts.
 * Purpose: make Sandcastle the implementation of CaseAgentRuntime while
 * preserving Case's normal pipeline, task store, review, close, and PR flow.
 */

import { codex, run } from "@ai-hero/sandcastle";
import { podman } from "@ai-hero/sandcastle/sandboxes/podman";
import type { CaseAgentRuntime, WorkspacePolicy } from "../src/agent/runtime.js";
import type { SpawnAgentOptions, SpawnAgentResult } from "../src/types.js";
import { parseAgentResult } from "../src/util/parse-agent-result.js";

export class SandcastleRuntimeAdapter implements CaseAgentRuntime {
  private abortController: AbortController | null = null;

  async spawn(options: SpawnAgentOptions): Promise<SpawnAgentResult> {
    const started = Date.now();
    this.abortController = new AbortController();

    const model = process.env.CASE_SANDCASTLE_CODEX_MODEL ?? "gpt-5.5";
    const provider = process.env.CASE_SANDCASTLE_PROVIDER ?? "podman";
    if (provider !== "podman") {
      throw new Error(`Prototype adapter only sketches podman, got ${provider}`);
    }

    const result = await run({
      agent: codex(model, { captureSessions: false }),
      sandbox: podman({
        imageName: process.env.CASE_SANDCASTLE_IMAGE ?? "sandcastle-codex:podman",
        mounts: [
          {
            hostPath:
              process.env.CASE_CODEX_AUTH_PATH ?? "/home/bump/.codex/auth.json",
            sandboxPath: "/home/agent/.codex/auth.json",
            readonly: true,
          },
        ],
        env: {},
      }),
      cwd: options.cwd,
      branchStrategy: { type: "head" },
      prompt: options.prompt,
      logging: {
        type: "file",
        path: `${options.dataDir}/.case/${options.phase ?? options.agentName}-sandcastle.log`,
      },
      maxIterations: Number(process.env.CASE_SANDCASTLE_MAX_ITERATIONS ?? "1"),
      completionSignal:
        process.env.CASE_SANDCASTLE_COMPLETION_SIGNAL ??
        "<promise>COMPLETE</promise>",
    });

    const raw = result.stdout ?? "";
    return {
      raw,
      result: parseAgentResult(raw),
      durationMs: Date.now() - started,
    };
  }

  createTools(_agentName: string, _cwd: string, _policy?: WorkspacePolicy): unknown[] {
    // Sandcastle runs the coding agent and its tools inside the sandbox.
    // Case should not expose host write/edit/bash tools for this adapter.
    return [];
  }

  abort(): void {
    this.abortController?.abort();
    this.abortController = null;
  }
}
