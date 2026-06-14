import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const IMPLEMENTED_FILES = [
  "dashboard.py",
  "dashboard.html",
  "tests/test_dashboard_render.py",
];

export function createCaseRuntime() {
  let aborted = false;

  return {
    async spawn(options) {
      if (aborted) {
        return spawnResult(
          options.agentName,
          "failed",
          `${options.agentName} skipped because runtime was aborted`,
          { error: "runtime aborted" },
        );
      }

      const started = Date.now();
      appendRuntimeLog(options.cwd, options.agentName, "started");

      let result;
      try {
        if (options.agentName === "implementer") {
          result = implementDashboard(options.cwd);
        } else if (options.agentName === "verifier") {
          result = verifyDashboard(options.cwd);
        } else {
          result = completedResult(
            options.agentName,
            `${options.agentName} completed by local fixture runtime`,
          );
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        result = failedResult(options.agentName, message);
      }

      appendRuntimeLog(options.cwd, options.agentName, result.status);
      return {
        raw: agentResultBlock(result),
        result,
        durationMs: Date.now() - started,
      };
    },

    createTools() {
      return [];
    },

    abort() {
      aborted = true;
    },
  };
}

function implementDashboard(cwd) {
  mkdirSync(join(cwd, "tests"), { recursive: true });

  writeNewOrSame(join(cwd, "dashboard.py"), dashboardPython());
  writeNewOrSame(join(cwd, "dashboard.html"), renderDashboardHtml());
  writeNewOrSame(join(cwd, "tests", "test_dashboard_render.py"), dashboardTestPython());

  return completedResult("implementer", "Added static host monitor dashboard render path", {
    filesChanged: IMPLEMENTED_FILES,
    testsPassed: null,
  });
}

function verifyDashboard(cwd) {
  const command = process.env.CASE_RUNTIME_VALIDATION_COMMAND ?? "python3 -m unittest discover -s tests";
  const result = spawnSync(command, {
    cwd,
    shell: true,
    encoding: "utf-8",
    timeout: Number(process.env.CASE_RUNTIME_VALIDATION_TIMEOUT_MS ?? "30000"),
  });

  if (result.status !== 0) {
    const output = [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
    return failedResult(
      "verifier",
      `Validation failed: ${command}${output ? `\n${output}` : ""}`,
      { testsPassed: false },
    );
  }

  return completedResult("verifier", `Validation passed: ${command}`, {
    filesChanged: [],
    testsPassed: true,
    evidenceMarkers: ["python3 -m unittest discover -s tests"],
  });
}

function completedResult(agentName, summary, artifactOverrides = {}) {
  return {
    status: "completed",
    summary,
    artifacts: {
      commit: null,
      filesChanged: [],
      testsPassed: null,
      screenshotUrls: [],
      evidenceMarkers: [],
      prUrl: null,
      prNumber: null,
      ...artifactOverrides,
    },
    findings: agentName === "scout" ? scoutFindings() : undefined,
    error: null,
  };
}

function failedResult(agentName, error, artifactOverrides = {}) {
  return {
    status: "failed",
    summary: `${agentName} failed`,
    artifacts: {
      commit: null,
      filesChanged: [],
      testsPassed: false,
      screenshotUrls: [],
      evidenceMarkers: [],
      prUrl: null,
      prNumber: null,
      ...artifactOverrides,
    },
    error,
  };
}

function spawnResult(agentName, status, summary, overrides = {}) {
  const result = {
    status,
    summary,
    artifacts: {
      commit: null,
      filesChanged: [],
      testsPassed: null,
      screenshotUrls: [],
      evidenceMarkers: [],
      prUrl: null,
      prNumber: null,
      ...(overrides.artifacts ?? {}),
    },
    error: overrides.error ?? null,
  };
  return {
    raw: agentResultBlock(result),
    result,
    durationMs: 0,
  };
}

function scoutFindings() {
  return {
    relevantFiles: [
      {
        path: "README.md",
        reason: "Declares the fixture purpose and expected dashboard sections.",
      },
    ],
    patterns: [
      {
        name: "local-static-render",
        file: "dashboard.py",
        description: "A small Python render function emits static sample dashboard HTML.",
      },
    ],
    constraints: [
      "No external CDN, network dependency, or secrets.",
      "Validation is python3 -m unittest discover -s tests.",
    ],
  };
}

function agentResultBlock(result) {
  return `<<<AGENT_RESULT\n${JSON.stringify({ result, scoutFindings: result.findings })}\nAGENT_RESULT>>>`;
}

function appendRuntimeLog(cwd, agentName, status) {
  const caseDir = join(cwd, ".case");
  mkdirSync(caseDir, { recursive: true });
  const entry = {
    at: new Date().toISOString(),
    agentName,
    status,
  };
  writeFileSync(
    join(caseDir, "runtime-module-spawns.log"),
    `${JSON.stringify(entry)}\n`,
    { flag: "a", encoding: "utf-8" },
  );
}

function writeNewOrSame(path, content) {
  if (existsSync(path)) {
    const existing = readFileSync(path, "utf-8");
    if (existing !== content) {
      throw new Error(`Refusing to overwrite existing file with different content: ${path}`);
    }
    return;
  }
  writeFileSync(path, content, "utf-8");
}

function dashboardPython() {
  return `from __future__ import annotations

from html import escape


SAMPLE_DATA = {
    "overview": {
        "cpu": "18%",
        "memory": "42%",
        "disk": "61%",
        "uptime": "3 days",
    },
    "containers": [
        {"name": "api", "image": "host-monitor/api:sample", "state": "running"},
        {"name": "worker", "image": "host-monitor/worker:sample", "state": "stopped"},
    ],
    "services": [
        {"name": "ssh", "port": 22, "state": "listening"},
        {"name": "dashboard", "port": 8080, "state": "listening"},
    ],
}


def render_dashboard(data: dict | None = None) -> str:
    data = data or SAMPLE_DATA
    overview = data["overview"]
    containers = data["containers"]
    services = data["services"]

    overview_items = "".join(
        f"<li><strong>{escape(label.title())}</strong>: {escape(value)}</li>"
        for label, value in overview.items()
    )
    container_rows = "".join(
        "<tr>"
        f"<td>{escape(container['name'])}</td>"
        f"<td>{escape(container['image'])}</td>"
        f"<td>{escape(container['state'])}</td>"
        "</tr>"
        for container in containers
    )
    service_rows = "".join(
        "<tr>"
        f"<td>{escape(service['name'])}</td>"
        f"<td>{escape(str(service['port']))}</td>"
        f"<td>{escape(service['state'])}</td>"
        "</tr>"
        for service in services
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Host Monitor Dashboard</title>
</head>
<body>
  <main>
    <h1>Host Monitor Dashboard</h1>
    <section aria-labelledby="overview-heading">
      <h2 id="overview-heading">Overview</h2>
      <ul>{overview_items}</ul>
    </section>
    <section aria-labelledby="containers-heading">
      <h2 id="containers-heading">Containers</h2>
      <table>
        <thead><tr><th>Name</th><th>Image</th><th>State</th></tr></thead>
        <tbody>{container_rows}</tbody>
      </table>
    </section>
    <section aria-labelledby="services-heading">
      <h2 id="services-heading">Services</h2>
      <p>Sample listening services rendered from local fixture data.</p>
      <table>
        <thead><tr><th>Name</th><th>Port</th><th>State</th></tr></thead>
        <tbody>{service_rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    print(render_dashboard())
`;
}

function renderDashboardHtml() {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Host Monitor Dashboard</title>
</head>
<body>
  <main>
    <h1>Host Monitor Dashboard</h1>
    <section aria-labelledby="overview-heading">
      <h2 id="overview-heading">Overview</h2>
      <ul>
        <li><strong>CPU</strong>: 18%</li>
        <li><strong>Memory</strong>: 42%</li>
        <li><strong>Disk</strong>: 61%</li>
        <li><strong>Uptime</strong>: 3 days</li>
      </ul>
    </section>
    <section aria-labelledby="containers-heading">
      <h2 id="containers-heading">Containers</h2>
      <table>
        <thead><tr><th>Name</th><th>Image</th><th>State</th></tr></thead>
        <tbody>
          <tr><td>api</td><td>host-monitor/api:sample</td><td>running</td></tr>
          <tr><td>worker</td><td>host-monitor/worker:sample</td><td>stopped</td></tr>
        </tbody>
      </table>
    </section>
    <section aria-labelledby="services-heading">
      <h2 id="services-heading">Services</h2>
      <p>Sample listening services rendered from local fixture data.</p>
      <table>
        <thead><tr><th>Name</th><th>Port</th><th>State</th></tr></thead>
        <tbody>
          <tr><td>ssh</td><td>22</td><td>listening</td></tr>
          <tr><td>dashboard</td><td>8080</td><td>listening</td></tr>
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
`;
}

function dashboardTestPython() {
  return `from __future__ import annotations

import unittest

from dashboard import render_dashboard


class DashboardRenderTest(unittest.TestCase):
    def test_dashboard_sections_render(self) -> None:
        html = render_dashboard()

        self.assertIn("Overview", html)
        self.assertIn("Containers", html)
        self.assertIn("Services", html)
        self.assertIn("listening services", html)


if __name__ == "__main__":
    unittest.main()
`;
}
