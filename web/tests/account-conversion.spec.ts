import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("account conversion preserves the workspace and can be reversed", async ({
  page,
  context,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-concepts-user.json"), "utf8"),
  );
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await page.locator(".account-menu > summary").click();
  await page
    .getByRole("button", { name: "Change to business", exact: true })
    .click();
  await expect(page.locator(".workspace-label")).toContainText(
    "Business workspace",
  );
  await expect(page.locator("#brand-select")).toHaveValue(user.brand_id);
  await page.reload();
  await expect(page.locator(".workspace-label")).toContainText(
    "Business workspace",
  );
  await page.locator(".account-menu > summary").click();
  await page
    .getByRole("button", { name: "Change to agency", exact: true })
    .click();
  await expect(page.locator(".workspace-label")).toContainText(
    "Agency workspace",
  );
  await expect(page.locator("#brand-select")).toHaveValue(user.brand_id);
  const history = await context.request.get(
    `/api/accounts/${user.account_id}/type-history`,
  );
  expect(history.ok()).toBeTruthy();
  expect(await history.json()).toHaveLength(2);
});
