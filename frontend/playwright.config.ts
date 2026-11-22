import { defineConfig, devices } from "@playwright/test";

// Browser smoke tests against the REAL stack: API on a throwaway database (test/e2e_server.py)
// behind the PRODUCTION nginx template (e2e/serve-nginx.sh) serving the production build.
// Run `npm run build` first; `source ../scripts/env.sh` provides the database settings.
const password = process.env.E2E_PASSWORD ?? "e2e-password-not-secret";
process.env.E2E_PASSWORD = password;

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:4790", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "python -m test.e2e_server --port 8790",
      cwd: "..",
      url: "http://127.0.0.1:8790/api/health/ready",
      reuseExistingServer: false,
      timeout: 60_000,
      env: { E2E_PASSWORD: password },
    },
    {
      command: "bash e2e/serve-nginx.sh",
      url: "http://127.0.0.1:4790/",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
