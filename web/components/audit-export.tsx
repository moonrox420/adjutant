"use client";

import { useState } from "react";
import { ApiError } from "../lib/api";
import { Field } from "./ui";

export function AuditExport({
  brandId,
  onError,
}: {
  brandId: string;
  onError: (error: unknown) => void;
}) {
  const [start, setStart] = useState(() =>
    new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10),
  );
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  async function download(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(`/api/brands/${brandId}/audit-export`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-Adjutant-Client": "console",
        },
        body: JSON.stringify({
          start: new Date(`${start}T00:00:00Z`).toISOString(),
          end: new Date(
            Date.parse(`${end}T00:00:00Z`) + 86_400_000,
          ).toISOString(),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new ApiError(
          response.status,
          body?.error?.message || "Audit export could not complete.",
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `adjutant-audit-${brandId}-${start}-${end}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(
        "Signed audit export downloaded. Keep it with your trusted verification keys.",
      );
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={download} aria-label="Export signed audit records">
      <div className="form-grid">
        <Field label="Audit start date (UTC)">
          <input
            type="date"
            value={start}
            max={end}
            onChange={(event) => setStart(event.target.value)}
            required
          />
        </Field>
        <Field label="Audit end date (UTC)">
          <input
            type="date"
            value={end}
            min={start}
            onChange={(event) => setEnd(event.target.value)}
            required
          />
        </Field>
      </div>
      <button className="button" disabled={busy}>
        {busy ? "Preparing signed export…" : "Download signed audit"}
      </button>
      {notice && <p role="status">{notice}</p>}
    </form>
  );
}
