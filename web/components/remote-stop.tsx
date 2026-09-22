"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { channelName, human, when } from "./ui";

type StopReport = {
  run: {
    id: string;
    state: string;
    created_at: string;
    error_code: string | null;
  };
  remote_pause_verified: boolean;
  items: {
    id: string;
    channel: string;
    native_id: string;
    account_id: string;
    covered_object_ids: string[];
    state: string;
    observed_state: string | null;
    provider_status: string | null;
    verified_at: string | null;
    error_message: string | null;
  }[];
};

export function RemoteStopReport({
  brandId,
  refreshToken,
}: {
  brandId: string;
  refreshToken: number;
}) {
  const [report, setReport] = useState<StopReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setReport(null);
    setError("");
    async function refresh() {
      try {
        const latest = await api<StopReport | null>(
          `/brands/${brandId}/remote-stop`,
          "GET",
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setReport(latest);
        if (latest && ["queued", "running"].includes(latest.run.state))
          timer = setTimeout(refresh, 1500);
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            e instanceof Error
              ? e.message
              : "The pause report could not be loaded.",
          );
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [brandId, refreshToken]);
  if (error)
    return (
      <p role="alert" className="message error">
        {error}
      </p>
    );
  if (!report) return null;
  return (
    <section className="panel" aria-label="Remote pause report">
      <span className="eyebrow">
        PAUSE REPORT · {when(report.run.created_at)}
      </span>
      <h2>
        {report.remote_pause_verified
          ? "Managed campaigns verified stopped."
          : report.items.length
            ? "Remote pause needs verification."
            : "No managed campaigns recorded."}
      </h2>
      <p>
        Local operations are stopped. This report covers campaigns recorded in
        Adjutant and their managed descendants. Campaigns outside that inventory
        require checking in the platform.
      </p>
      {report.run.error_code && (
        <p role="alert">{human(report.run.error_code)}</p>
      )}
      <div className="card-grid">
        {report.items.map((item) => (
          <article className="panel" key={item.id}>
            <h3>{channelName[item.channel] || human(item.channel)}</h3>
            <p>
              Account {item.account_id} · Campaign {item.native_id}
            </p>
            <strong>
              {item.state === "verified"
                ? `Verified ${human(item.observed_state || "")}`
                : item.state === "failed"
                  ? "Pause unverified"
                  : "Awaiting platform verification"}
            </strong>
            {item.verified_at && (
              <p>
                {when(item.verified_at)} · Provider status:{" "}
                {item.provider_status}
              </p>
            )}
            <p>
              {item.covered_object_ids.length} managed objects covered by this
              campaign.
            </p>
            {item.error_message && (
              <p role="alert" className="message error">
                {item.error_message}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
