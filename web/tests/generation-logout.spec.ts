import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

type Job = {
  id: string;
  brand_id: string;
  worker_pid: number | null;
  worker_exit_code: number | null;
  worker_exit_verified_at: string | null;
  finished_at: string | null;
  error_code: string | null;
};

function inferenceReceipt(): { brief?: string; disconnected?: boolean } {
  return JSON.parse(
    readFileSync(resolve("../.local/browser-inference.json"), "utf8"),
  );
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
}

test("signing out during generation verifies worker exit and revokes the old session", async ({
  page,
  context,
  playwright,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-generation-user.json"), "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  async function signIn() {
    await page.getByLabel("Email address").fill(user.email);
    await page.getByLabel("Password", { exact: true }).fill(user.password);
    await page.getByRole("button", { name: "Sign in →", exact: true }).click();
    await expect(page.locator(".account-menu")).toBeVisible();
  }
  await page.goto("/");
  await signIn();
  const headers = {
    "X-Adjutant-Client": "console",
    Origin: "http://127.0.0.1:3001",
  };
  const created = await context.request.post("/api/brands", {
    headers,
    data: {
      account_id: user.account_id,
      display_name: "Generation lifecycle plumbing",
      website_url: "https://example.com",
      vertical: "home_services",
      monthly_ceiling: "3000.00",
      daily_ceiling: "100.00",
    },
  });
  expect(created.status()).toBe(201);
  const brand = (await created.json()).id as string;
  const assertion = await context.request.post(
    `/api/brands/${brand}/assertions`,
    {
      headers,
      data: {
        field_path: "offerings",
        value: "Residential plumbing repairs.",
        provenance_uri: "https://example.com/services",
      },
    },
  );
  expect(assertion.status()).toBe(201);
  expect(
    (
      await context.request.post(`/api/brands/${brand}/confirm`, { headers })
    ).ok(),
  ).toBe(true);
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await page.getByRole("button", { name: "Generate draft ↗" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Installed local model")).toHaveValue(
    "browser-lifecycle-model",
  );
  const brief = `Create a plumbing campaign for logout verification ${randomUUID()}`;
  await dialog.getByLabel("Campaign brief").fill(brief);
  await dialog
    .getByRole("button", { name: "Generate campaign draft →" })
    .click();
  await expect
    .poll(() => inferenceReceipt().brief, { timeout: 15_000 })
    .toBe(brief);
  await dialog
    .getByRole("button", { name: "Close dialog", exact: true })
    .click();
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  const jobRow = page
    .locator(".job-row")
    .filter({ hasText: "browser-lifecycle-model" });
  await expect(jobRow).toContainText("Running");
  await expect(jobRow).toContainText("Process exit not yet verified");
  const jobsResponse = await context.request.get("/api/jobs");
  expect(jobsResponse.ok()).toBe(true);
  const running = ((await jobsResponse.json()) as Job[]).find(
    (job) => job.brand_id === brand,
  )!;
  expect(running.worker_pid).not.toBeNull();
  expect(running.worker_exit_verified_at).toBeNull();
  expect(processExists(running.worker_pid!)).toBe(true);
  const session = (await context.cookies()).find(
    (cookie) => cookie.name === "adjutant_session",
  )!;
  const revokedClient = await playwright.request.newContext({
    baseURL: "http://127.0.0.1:3001",
    extraHTTPHeaders: { Cookie: `adjutant_session=${session.value}` },
  });
  try {
    await page.locator(".account-menu summary").click();
    let report: { cancellation_verified: boolean; jobs: Job[] } | undefined;
    await page.route("**/api/auth/logout", async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBe(true);
      // Capture the real API acknowledgement before sign-out navigates away.
      report = await response.json();
      await route.fulfill({ response });
    });
    await page
      .locator(".account-menu")
      .getByRole("button", { name: "Sign out", exact: true })
      .click();
    await expect(page).toHaveURL(/\/account(?:#.*)?$/);
    expect(report).toBeDefined();
    if (!report) throw new Error("Logout acknowledgement was not captured");
    expect(report.cancellation_verified).toBe(true);
    const acknowledged = report.jobs.find((job) => job.id === running.id)!;
    expect(acknowledged.worker_exit_code).not.toBeNull();
    expect(acknowledged.worker_exit_verified_at).not.toBeNull();
    expect(acknowledged.finished_at).not.toBeNull();
    await expect(page.getByRole("status")).toContainText(
      "worker exits were verified",
    );
    await expect.poll(() => processExists(running.worker_pid!)).toBe(false);
    await expect.poll(() => inferenceReceipt().disconnected).toBe(true);
    expect((await revokedClient.get("/api/me")).status()).toBe(401);
    expect(
      (await revokedClient.get(`/api/brands/${brand}/workspace`)).status(),
    ).toBe(401);
    await signIn();
    const persistedResponse = await context.request.get("/api/jobs");
    expect(persistedResponse.ok()).toBe(true);
    const persisted = ((await persistedResponse.json()) as Job[]).find(
      (job) => job.id === running.id,
    )!;
    expect(persisted.worker_pid).toBe(running.worker_pid);
    expect(persisted.worker_exit_code).toBe(acknowledged.worker_exit_code);
    expect(persisted.worker_exit_verified_at).toBe(
      acknowledged.worker_exit_verified_at,
    );
    expect(persisted.error_code).toBe("GenerationCancelled");
    const workspace = await context.request.get(
      `/api/brands/${brand}/workspace`,
    );
    expect(workspace.ok()).toBe(true);
    expect((await workspace.json()).plans).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await revokedClient.dispose();
  }
});
