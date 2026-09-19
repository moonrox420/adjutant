"use client";

import { useState } from "react";
import { api } from "../lib/api";
import { channelName, when } from "./ui";

type PreflightResult = {
  ready: boolean;
  checked_at: string;
  checks: {
    key: string;
    passed: boolean;
    message: string;
    channel: string | null;
  }[];
};

export function DeploymentPreflight({
  brandId,
  planId,
}: {
  brandId: string;
  planId: string;
}) {
  const [result, setResult] = useState<PreflightResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function check() {
    setBusy(true);
    setResult(null);
    setError("");
    try {
      setResult(
        await api<PreflightResult>(
          `/brands/${brandId}/plans/${planId}/preflight`,
          "POST",
        ),
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Deployment checks failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="form-stack">
      <button className="button" disabled={busy} onClick={check}>
        {busy
          ? "Checking deployment requirements…"
          : "Check deployment readiness"}
      </button>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <div role="status">
          <p>
            <strong>
              {result.ready
                ? "Ready for deployment review"
                : "Deployment requirements remain"}
            </strong>
          </p>
          <ul>
            {result.checks.map((item) => (
              <li key={`${item.channel ?? "plan"}:${item.key}`}>
                <strong>{item.passed ? "Passed" : "Required"}</strong>
                {item.channel
                  ? ` · ${channelName[item.channel] || item.channel}`
                  : ""}
                : {item.message}
              </li>
            ))}
          </ul>
          <p className="footnote">
            Checked {when(result.checked_at)}. This check does not reserve spend
            or launch ads.
          </p>
        </div>
      )}
    </div>
  );
}
