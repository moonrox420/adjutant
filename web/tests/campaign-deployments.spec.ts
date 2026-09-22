import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("campaign setup queues and cancels a durable build through actual APIs", async ({
  page,
  context,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-concepts-user.json"), "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(page.locator(".account-menu")).toBeVisible();
  const prefix = `/api/brands/${user.brand_id}`;
  const headers = { "X-Adjutant-Client": "console" };
  const response = await context.request.post(`${prefix}/plans`, {
    headers,
    data: {
      name: "Browser paused deployment",
      objective: "traffic",
      goal_kind: "efficient_spend",
      goal_value: "20.00",
      monthly_budget_usd: "3000.00",
      rationale: "Verify a paused deployment through the browser form.",
      audience: "Local homeowners",
      hypothesis: "Helpful repair information produces more visits.",
      allocations: [
        {
          channel: "meta",
          monthly_budget_usd: "3000.00",
          daily_budget_usd: "100.00",
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const plan = await response.json();
  const latest = await (
    await context.request.get(`${prefix}/studio/latest`)
  ).json();
  const attached = await context.request.post(
    `${prefix}/studio/${latest.id}/attach`,
    { headers, data: { plan_id: plan.id, expected_revision: latest.revision } },
  );
  expect(attached.ok(), await attached.text()).toBeTruthy();
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  const card = page
    .locator("article.plan-card")
    .filter({
      has: page.getByRole("heading", {
        name: "Browser paused deployment",
        exact: true,
      }),
    });
  await card
    .getByText("Build and verify paused campaigns", { exact: true })
    .click();
  await card.getByLabel("Page Id", { exact: true }).fill("5678");
  await card
    .getByLabel("Destination Url", { exact: true })
    .fill("https://example.com/repairs");
  await card
    .getByLabel("Countries (comma separated)", { exact: true })
    .fill("US");
  const endTime = new Date(Date.now() + 7 * 86400000)
    .toISOString()
    .slice(0, 16);
  await card.getByLabel("End Time", { exact: true }).fill(endTime);
  await card
    .getByRole("button", { name: "Create paused campaign", exact: true })
    .click();
  await expect(
    card.getByRole("heading", { name: "Meta: Queued", exact: true }),
  ).toBeVisible();
  await card
    .getByRole("button", { name: "Cancel remaining work", exact: true })
    .click();
  await expect(
    card.getByRole("heading", { name: "Meta: Cancelled", exact: true }),
  ).toBeVisible();
  const jobs = await (
    await context.request.get(`${prefix}/plans/${plan.id}/deployments`)
  ).json();
  expect(jobs).toHaveLength(1);
  expect(jobs[0].state).toBe("cancelled");
  expect(jobs[0].steps).toEqual([]);
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await card
    .getByText("Build and verify paused campaigns", { exact: true })
    .click();
  await expect(
    card.getByRole("heading", { name: "Meta: Cancelled", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
