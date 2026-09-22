import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("all ten channel setup forms save encrypted configuration without claiming authorization", async ({
  page,
  context,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-studio-user.json"), "utf8"),
  );
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(page.locator(".account-menu")).toBeVisible();
  const created = await context.request.post("/api/brands", {
    headers: {
      "X-Adjutant-Client": "console",
      Origin: "http://127.0.0.1:3001",
    },
    data: {
      account_id: user.account_id,
      display_name: "Account authorization testing",
      website_url: "https://example.com",
      vertical: "home_services",
      monthly_ceiling: "3000.00",
      daily_ceiling: "100.00",
    },
  });
  expect(created.status()).toBe(201);
  const brand = (await created.json()).id;
  await page.reload();
  await page.getByRole("button", { name: "Channels", exact: true }).click();
  await expect(page.locator(".channel-card")).toHaveCount(10);
  for (const card of await page.locator(".channel-card").all()) {
    await expect(
      card.getByText("No account authorization", { exact: true }),
    ).toBeVisible();
    await card
      .getByText("Set up developer application", { exact: true })
      .click();
    await card
      .getByLabel("Client / application ID", { exact: true })
      .fill("browser-test-client");
    await card
      .getByLabel("Client secret", { exact: true })
      .fill("browser-test-secret");
    const developer = card.getByLabel("Developer token", { exact: true });
    if (await developer.count())
      await developer.fill("browser-developer-token");
    await card
      .getByRole("button", { name: "Save application", exact: true })
      .click();
    await expect(card.getByRole("status")).toContainText(
      "credentials saved encrypted",
    );
    await expect(
      card.getByRole("button", { name: /^Authorize / }),
    ).toBeVisible();
    await expect(
      card.getByText("No account authorization", { exact: true }),
    ).toBeVisible();
  }
  const status = await context.request.get(`/api/brands/${brand}/channels`);
  expect(status.ok()).toBeTruthy();
  const payload = await status.json();
  expect(payload).toHaveLength(10);
  expect(
    payload.every(
      (item: { application_saved: boolean; token_saved: boolean }) =>
        item.application_saved && !item.token_saved,
    ),
  ).toBeTruthy();
  expect(JSON.stringify(payload)).not.toContain("browser-test-secret");
  await page.reload();
  await page.getByRole("button", { name: "Channels", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Authorize / })).toHaveCount(
    10,
  );
  const other = await context.request.post("/api/brands", {
    headers: {
      "X-Adjutant-Client": "console",
      Origin: "http://127.0.0.1:3001",
    },
    data: {
      account_id: user.account_id,
      display_name: "Another account brand",
      website_url: "https://example.com",
      vertical: "home_services",
      monthly_ceiling: "3000.00",
      daily_ceiling: "100.00",
    },
  });
  expect(other.status()).toBe(201);
  await page.goto(`/?channels=1&brand=${brand}`);
  await expect(page.getByRole("button", { name: /^Authorize / })).toHaveCount(
    10,
  );
  await expect(page.locator(".channel-card")).toHaveCount(10);
  expect(new URL(page.url()).search).toBe("");
});
