import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("global pause persists a named failure for every unauthorized platform", async ({
  page,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-stop-user.json"), "utf8"),
  );
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(page.locator(".account-menu")).toBeVisible();
  await page.getByRole("button", { name: "Channels", exact: true }).click();
  await page
    .getByRole("button", { name: "Pause everything", exact: true })
    .click();
  await page
    .getByLabel("Reason for stopping")
    .fill("Verify missing authorization is reported for every platform");
  await page.getByRole("button", { name: "Confirm stop", exact: true }).click();
  const report = page.getByRole("region", { name: "Remote pause report" });
  await expect(
    report.getByText("Pause unverified", { exact: true }),
  ).toHaveCount(10);
  await expect(
    report.getByText(
      "Save the developer application and authorize account access first.",
      { exact: true },
    ),
  ).toHaveCount(10);
  await page.reload();
  await expect(
    report.getByText("Pause unverified", { exact: true }),
  ).toHaveCount(10);
  await page.getByRole("button", { name: "Guardrails", exact: true }).click();
  await page
    .getByRole("button", { name: "Resume local operations", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText(
    "Local operations resumed.",
  );
  await expect(report).toHaveCount(0);
});
