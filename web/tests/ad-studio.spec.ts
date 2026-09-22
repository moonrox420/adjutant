import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("unconfirmed brand opens editable ad previews, preserves edits, and reports image errors", async ({
  page,
  context,
}) => {
  const user = JSON.parse(
    readFileSync(resolve("../.local/browser-studio-user.json"), "utf8"),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByLabel("Email address").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(page.locator(".account-menu")).toBeVisible();
  const result = await context.request.post("/api/brands", {
    headers: {
      "X-Adjutant-Client": "console",
      Origin: "http://127.0.0.1:3001",
    },
    data: {
      account_id: user.account_id,
      display_name: "Studio Plumbing",
      website_url: "https://example.com",
      vertical: "home_services",
      monthly_ceiling: "3000.00",
      daily_ceiling: "100.00",
    },
  });
  expect(result.status()).toBe(201);
  const brand = (await result.json()).id;
  const image =
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBv0AAAAASUVORK5CYII=";
  const fixture = {
    id: "f81a96f9-6076-4dab-92bc-bc5b5f094be5",
    brand_id: brand,
    revision: 1,
    brand_name: "Studio Plumbing",
    destination_url: "https://example.com",
    meta: {
      headline: "Plumbing help for your home",
      primary_text: "Talk with our local repair team.",
      description: "Residential plumbing repairs.",
      cta: "Contact us",
      image_prompt: "A plumber repairing a kitchen tap.",
      image_url: image,
    },
    google: {
      headlines: [
        "Home Plumbing Repairs",
        "Talk With Our Team",
        "Local Plumbing Help",
      ],
      descriptions: [
        "Get help with residential plumbing repairs.",
        "Contact our team about your home repairs.",
      ],
      destination_path: "plumbing/repairs",
    },
    tiktok: {
      hook: "That drip isn't fixing itself.",
      visual_script: "A dripping tap followed by a plumber inspecting it.",
      cta: "Talk to the repair team",
    },
  };
  let saved: typeof fixture | null = null;
  await page.route(`**/api/brands/${brand}/studio/latest`, (route) =>
    route.fulfill({ json: saved }),
  );
  await page.route("**/api/studio/jobs", async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      brand_id: brand,
      url_or_prompt: "https://example.com/services",
    });
    saved = fixture;
    await route.fulfill({
      status: 202,
      json: { id: fixture.id, state: "queued" },
    });
  });
  await page.route(
    `**/api/brands/${brand}/studio/jobs/${fixture.id}`,
    (route) =>
      route.fulfill({
        json: { id: fixture.id, state: "completed", result: fixture },
      }),
  );
  await page.route(
    `**/api/brands/${brand}/studio/${fixture.id}`,
    async (route) => {
      const body = route.request().postDataJSON();
      saved = {
        ...fixture,
        ...body,
        revision: 2,
        meta: { ...body.meta, image_url: image },
      };
      await route.fulfill({ json: saved });
    },
  );
  await page.reload();
  const registryResponse = await context.request.get("/api/channels");
  expect(registryResponse.ok()).toBeTruthy();
  const registry = await registryResponse.json();
  expect(registry.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Channels", exact: true }).click();
  await expect(page.locator(".channel-card")).toHaveCount(registry.length);
  for (const [index, channel] of registry.entries()) {
    expect(Array.isArray(channel.objectives)).toBeTruthy();
    const card = page.locator(".channel-card").nth(index);
    await expect(card.locator(".channel-objectives")).not.toBeEmpty();
    await card.getByText("Connection prerequisites", { exact: true }).click();
    await expect(card.locator("li")).toHaveCount(channel.prerequisites.length);
  }
  expect(errors).toEqual([]);
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "+ New campaign plan", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Generate draft ↗", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText(
      "Confirm brand intelligence before creating a campaign plan.",
    ),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Generate draft ↗", exact: true })
    .click();
  await expect(
    page.getByLabel("Business URL or campaign prompt"),
  ).toBeFocused();
  await page
    .getByLabel("Business URL or campaign prompt")
    .fill("https://example.com/services");
  await page.getByRole("button", { name: "Generate →", exact: true }).click();
  const generated = page.getByAltText(
    "Generated campaign image for Studio Plumbing",
  );
  await expect(generated).toHaveAttribute("src", image);
  await expect
    .poll(() =>
      generated.evaluate(
        (element) => (element as HTMLImageElement).naturalWidth,
      ),
    )
    .toBeGreaterThan(0);
  await page
    .getByLabel("Meta headline", { exact: true })
    .fill("Repairs made simpler");
  await page
    .getByLabel("Google headline 1", { exact: true })
    .fill("Your Local Repair Team");
  await page.getByLabel("TikTok three-second hook").fill("Hear that drip?");
  await page.getByRole("button", { name: "Save copy edits" }).click();
  await expect(page.locator(".ad-studio > [role=status]")).toHaveText(
    "Edits saved.",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await expect(page.getByLabel("Meta headline", { exact: true })).toHaveValue(
    "Repairs made simpler",
  );
  await expect(
    page.getByLabel("Google headline 1", { exact: true }),
  ).toHaveValue("Your Local Repair Team");
  await expect(page.getByLabel("TikTok three-second hook")).toHaveValue(
    "Hear that drip?",
  );
  await page.screenshot({
    path: "../.local/ad-studio-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "../.local/ad-studio-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.unroute("**/api/studio/jobs");
  await page.route("**/api/studio/jobs", (route) =>
    route.fulfill({
      status: 422,
      json: { error: { message: "Google returned no image." } },
    }),
  );
  await page.getByRole("button", { name: "Generate →", exact: true }).click();
  await expect(page.locator(".ad-studio [role=alert]")).toHaveText(
    "Google returned no image.",
  );
  await expect(page.locator('img[src*="placehold"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});
