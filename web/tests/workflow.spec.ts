import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createHash, createPublicKey, verify } from "node:crypto";
import { resolve } from "node:path";

test("brand evidence to approved plan persists across reloads", async ({
  page,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-user.json"), "utf-8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(
    page.getByText("Every good campaign starts with a known brand."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Add your first brand →" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Brand name").fill("Northline Plumbing");
  await dialog.getByLabel("Business website").fill("https://example.com");
  await dialog.getByLabel("Monthly ceiling (USD)").fill("5000");
  await dialog.getByLabel("Daily ceiling (USD)").fill("200");
  await dialog.getByRole("button", { name: "Create brand →" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("button", { name: "Brand intelligence", exact: true })
    .click();
  await page
    .getByLabel("What we know")
    .fill("Residential plumbing repairs for local homeowners.");
  await page.getByLabel("Source URL").fill("https://example.com/services");
  await page.getByRole("button", { name: "Save sourced fact →" }).click();
  await page
    .getByRole("button", { name: "Confirm these brand facts ✓" })
    .click();
  await expect(
    page.getByText("Brand facts confirmed. Campaign planning is available."),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await page.getByRole("button", { name: "Generate draft ↗" }).click();
  await expect(dialog.getByLabel("Inference provider")).toHaveValue("local");
  await dialog.getByLabel("Inference provider").selectOption("cloud");
  await expect(dialog.getByRole("alert")).toContainText(
    "ADJUTANT_OLLAMA_CLOUD_API_KEY",
  );
  await expect(
    dialog.getByRole("button", { name: "Generate campaign draft →" }),
  ).toBeDisabled();
  await dialog.getByLabel("Inference provider").selectOption("local");
  await expect(dialog.getByLabel("Installed local model")).toBeEnabled();
  await page.screenshot({
    path: "../.local/ollama-provider-selection.png",
    fullPage: true,
  });
  await dialog
    .getByRole("button", { name: "Close dialog", exact: true })
    .click();
  await page
    .getByRole("button", { name: "+ New campaign plan", exact: true })
    .click();
  await dialog.getByLabel("Campaign name").fill("Homeowner repair inquiries");
  await dialog.getByLabel("Goal value", { exact: true }).fill("50");
  await dialog
    .getByLabel("Audience", { exact: true })
    .fill("Local homeowners seeking plumbing repairs");
  await dialog
    .getByLabel("Creative hypothesis")
    .fill("Clear repair service messaging increases qualified inquiries.");
  await dialog
    .getByLabel("Strategy and rationale")
    .fill(
      "Test an offer focused on residential repairs with a controlled daily budget.",
    );
  await dialog.getByLabel("Monthly budget 1").fill("3000");
  await dialog.getByLabel("Daily budget 1").fill("100");
  await dialog.getByRole("button", { name: "Save campaign draft →" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: "Send for review →" }).click();
  await page.getByRole("button", { name: /^✓Approvals|^Approvals/ }).click();
  await expect(
    page.getByRole("heading", { name: "Homeowner repair inquiries" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.local/approval-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Approve plan ✓" }).click();
  await expect(
    page.getByText("Plan approved. No campaign has been launched."),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Check deployment readiness" })
    .click();
  await expect(
    page.getByText("Deployment requirements remain", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Connect an authorized advertising account for this channel.",
      { exact: false },
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  const downloaded = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download signed audit", exact: true })
    .click();
  const download = await downloaded;
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  const bundle = JSON.parse(readFileSync(downloadPath!, "utf8"));
  expect(bundle.manifest.entry_count).toBeGreaterThan(0);
  expect(
    createHash("sha256").update(JSON.stringify(bundle.document)).digest("hex"),
  ).toBe(bundle.manifest.sha256);
  const trustedKeys = JSON.parse(
    readFileSync(resolve("../.local/approval-public-keys.json"), "utf8"),
  );
  const publicKey = createPublicKey({
    key: Buffer.concat([
      Buffer.from("302a300506032b6570032100", "hex"),
      Buffer.from(trustedKeys[bundle.manifest.signing_key_id], "base64"),
    ]),
    format: "der",
    type: "spki",
  });
  expect(
    verify(
      null,
      Buffer.from(JSON.stringify(bundle.manifest)),
      publicKey,
      Buffer.from(bundle.signature, "base64"),
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.screenshot({
    path: "../.local/overview-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.local/overview-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
