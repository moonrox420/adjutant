import test from "node:test";
import assert from "node:assert/strict";
import { violations } from "./check-channel-parity.mjs";

test("rejects literal comparisons, aliases, membership and switch cases", () => {
  for (const source of [
    'if (channel === "meta") run();',
    'const GOOGLE = "google_ads"; if (channel !== GOOGLE) run();',
    'if (["youtube", "tiktok"].includes(channel)) run();',
    'switch (channel) { case "reddit": run(); }',
  ])
    assert.ok(violations(source, "service.ts").length > 0, source);
});

test("allows registry-driven selection and display data", () => {
  assert.deepEqual(
    violations(
      'const labels = { meta: "Meta" }; const value = registry[channel];',
      "ui.ts",
    ),
    [],
  );
  assert.deepEqual(
    violations("if (row.channel === channel) render(row);", "ui.tsx"),
    [],
  );
});
