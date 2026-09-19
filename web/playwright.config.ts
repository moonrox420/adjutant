import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const virtualenvPython =
  process.platform === "win32"
    ? "../.venv/Scripts/python.exe"
    : "../.venv/bin/python";
const python = existsSync(virtualenvPython) ? virtualenvPython : "python";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3001",
    viewport: { width: 1440, height: 1050 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `"${python}" ../scripts/browser_server.py`,
      url: "http://127.0.0.1:8001/healthz",
      timeout: 30_000,
      reuseExistingServer: false,
    },
    {
      command:
        "node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3001",
      url: "http://127.0.0.1:3001",
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        ADJUTANT_API_URL: "http://127.0.0.1:8001",
        ADJUTANT_NEXT_DIST_DIR: ".next-e2e",
      },
    },
  ],
});
