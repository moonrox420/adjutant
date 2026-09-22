import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

function deliveredLink(email: string, purpose: "verify" | "reset") {
  const directory = resolve("../.local/browser-mail");
  let files: string[];
  try {
    files = readdirSync(directory);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "";
    throw error;
  }
  for (const file of files.filter((file) => file.endsWith(".eml"))) {
    const mail = readFileSync(resolve(directory, file), "utf8")
      .replace(/=\r?\n/g, "")
      .replace(/=([0-9A-F]{2})/g, (_, value: string) =>
        String.fromCharCode(parseInt(value, 16)),
      );
    if (mail.includes(`To: ${email}`)) {
      const match = mail.match(
        new RegExp(
          `http://127\\.0\\.0\\.1:3001/account#${purpose}=[A-Za-z0-9_-]+`,
        ),
      );
      if (match) return match[0];
    }
  }
  return "";
}

test("consumer signup, delivered verification, mobile logout, recovery, and cross-tab revocation", async ({
  page,
  context,
}) => {
  const email = `browser-${randomUUID()}@example.com`;
  const password = " exact consumer passphrase ";
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page
    .getByRole("button", { name: "Create an account", exact: true })
    .click();
  await page.getByLabel("Full name", { exact: true }).fill("Consumer Owner");
  await page.getByLabel("Workspace name").fill("Consumer workspace");
  await expect(
    page.getByRole("radio", { name: /^Business/ }),
  ).not.toBeChecked();
  await expect(page.getByRole("radio", { name: /^Agency/ })).not.toBeChecked();
  await page.getByRole("radio", { name: /^Agency/ }).check();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByLabel("Confirm password")
    .fill("this does not match the password");
  await page.getByRole("button", { name: "Create account →" }).click();
  await expect(page.locator(".login-form").getByRole("alert")).toContainText(
    "Passwords must match",
  );
  await page.getByLabel("Confirm password").fill(password);
  await page.screenshot({
    path: "../.local/account-signup-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Create account →" }).click();
  await expect(page.getByRole("status")).toContainText("queued");
  await expect
    .poll(() => deliveredLink(email, "verify"), { timeout: 15_000 })
    .not.toBe("");
  await page.goto(deliveredLink(email, "verify"));
  await page.getByRole("button", { name: "Verify email →" }).click();
  await expect(page.getByRole("status")).toContainText("Email verified");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(
    page.getByText("Every good campaign starts with a known brand."),
  ).toBeVisible();
  const second = await context.newPage();
  await second.goto("/");
  await expect(
    second.getByText("Every good campaign starts with a known brand."),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".account-menu summary").click();
  await expect(
    page
      .locator(".account-menu")
      .getByRole("button", { name: "Sign out", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../.local/account-menu-mobile.png",
    fullPage: true,
  });
  await page
    .locator(".account-menu")
    .getByRole("button", { name: "Sign out", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Sign in →", exact: true }),
  ).toBeVisible();
  await expect(
    second.getByRole("button", { name: "Sign in →", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Forgot password?" }).click();
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send recovery link →" }).click();
  await expect
    .poll(() => deliveredLink(email, "reset"), { timeout: 15_000 })
    .not.toBe("");
  await page.goto(deliveredLink(email, "reset"));
  const replacement = "my replacement consumer passphrase";
  await page.getByLabel("Password", { exact: true }).fill(replacement);
  await page.getByLabel("Confirm password").fill(replacement);
  await page.getByRole("button", { name: "Save new password →" }).click();
  await expect(page.getByRole("status")).toContainText("Password changed");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(replacement);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(
    page.getByText("Every good campaign starts with a known brand."),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("Every good campaign starts with a known brand."),
  ).toBeVisible();
  await page.locator(".account-menu summary").click();
  await page.getByRole("button", { name: "Sign out everywhere" }).click();
  await expect(
    page.getByRole("button", { name: "Sign in →", exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
