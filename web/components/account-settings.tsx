"use client";

import { useState } from "react";
import { api, type Account } from "../lib/api";

export function AccountSettings({
  account,
  saved,
}: {
  account: Account;
  saved: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const target = account.account_type === "business" ? "agency" : "business";

  async function convert() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await api<unknown>(`/accounts/${account.id}/type`, "PUT", {
        expected_type: account.account_type,
        account_type: target,
      });
      if (
        !result ||
        typeof result !== "object" ||
        !("account_type" in result) ||
        result.account_type !== target
      ) {
        throw new Error(
          "The server did not confirm the account type. Reload before retrying.",
        );
      }
      await saved();
      setMessage(
        `Changed to ${target}. Existing brands, seats and saved settings were preserved.`,
      );
    } catch (issue) {
      setError(
        issue instanceof Error ? issue.message : "Account conversion failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={`${account.display_name} account settings`}>
      <p>
        {account.display_name} · {account.account_type}
      </p>
      <button
        className="button compact"
        disabled={busy}
        onClick={() => void convert()}
      >
        {busy ? "Saving account type…" : `Change to ${target}`}
      </button>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
