import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3001",
    viewport: { width: 1440, height: 1050 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "..\\.venv\\Scripts\\python.exe ..\\scripts\\browser_server.py",
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
