import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("persisted concepts load, edit, render, and survive reload through real APIs", async ({
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
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  const concepts = page.getByRole("group", { name: "Creative concepts" });
  await expect(concepts.getByRole("button")).toHaveCount(5);
  await concepts
    .getByRole("button", { name: "2. The outcome", exact: true })
    .click();
  await expect(page.getByLabel("Meta headline", { exact: true })).toHaveValue(
    "The outcome",
  );
  await page
    .getByLabel("Meta headline", { exact: true })
    .fill("A repaired kitchen tap");
  await expect(concepts.getByRole("button").first()).toBeDisabled();
  await page
    .getByRole("button", { name: "Save copy edits", exact: true })
    .click();
  await expect(page.locator(".ad-studio > [role=status]")).toHaveText(
    "Edits saved.",
  );
  await page.reload();
  await page
    .getByRole("button", { name: "Campaign plans", exact: true })
    .click();
  await concepts
    .getByRole("button", { name: "2. The outcome", exact: true })
    .click();
  await expect(page.getByLabel("Meta headline", { exact: true })).toHaveValue(
    "A repaired kitchen tap",
  );
  const latest = await context.request.get(
    `/api/brands/${user.brand_id}/studio/latest`,
  );
  expect(latest.ok()).toBeTruthy();
  const result = await latest.json();
  const draft = result.concepts[1];
  expect(draft.revision).toBe(2);
  for (const aspect_ratio of ["1:1", "4:5", "9:16", "16:9"]) {
    const render = await context.request.post(
      `/api/brands/${user.brand_id}/studio/${draft.id}/render`,
      {
        headers: { "X-Adjutant-Client": "console" },
        data: { expected_revision: 2, aspect_ratio },
      },
    );
    expect(render.ok(), await render.text()).toBeTruthy();
    const body = await render.json();
    const image = await context.request.get(body.png_url);
    expect(image.ok()).toBeTruthy();
    expect((await image.body()).subarray(0, 8).toString("hex")).toBe(
      "89504e470d0a1a0a",
    );
    const svg = await context.request.get(body.svg_url);
    expect(await svg.text()).toContain("A repaired kitchen tap");
  }
  expect(errors).toEqual([]);
});
