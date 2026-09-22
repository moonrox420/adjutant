"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { human, when } from "./ui";

type Job = {
  id: string;
  brand_id: string;
  kind: "generation" | "studio" | "campaign_build";
  state: string;
  model_id: string;
  finished_at: string | null;
  error_code: string | null;
  cancel_requested_at: string | null;
  worker_pid: number | null;
  worker_exit_verified_at: string | null;
};
type Activity = {
  mail_transport: "file" | "smtp";
  receipts: { event_id: string; event_type: string; processed_at: string }[];
  processes: {
    instance_id: string;
    healthy: boolean;
    exit_code: number | null;
    exit_verified_at: string | null;
  }[];
};

export function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const refresh = useCallback(async () => {
    const [runs, events] = await Promise.all([
      api<Job[]>("/jobs"),
      api<Activity>("/consumer-activity"),
    ]);
    setJobs(runs);
    setActivity(events);
  }, []);
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        if (active) await refresh();
      } catch (e) {
        if (active)
          setError(
            e instanceof Error ? e.message : "Unable to read job status.",
          );
      }
    }
    void poll();
    const timer = setInterval(poll, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [refresh]);
  async function cancel(job: Job) {
    setBusy(job.id);
    setError("");
    try {
      if (job.kind === "studio") {
        await api(`/brands/${job.brand_id}/studio/jobs/${job.id}`, "DELETE");
      } else if (job.kind === "campaign_build") {
        await api(
          `/brands/${job.brand_id}/deployments/${job.id}/cancel`,
          "POST",
        );
      } else {
        const report = await api<{ cancellation_verified: boolean }>(
          `/jobs/${job.id}/cancel`,
          "POST",
        );
        if (!report.cancellation_verified)
          setError(
            "Cancellation requested. Process exit is still awaiting verification.",
          );
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to cancel generation.");
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Jobs & delivery</h2>
      </div>
      <p className="muted">
        Generation cancellation stops its dedicated worker. Campaign
        cancellation stops remaining construction and retains the record of
        provider operations.
      </p>
      {error && (
        <p role="alert" className="message error">
          {error}
        </p>
      )}
      {!jobs.length && <p className="footnote">You have no jobs yet.</p>}
      {jobs.map((job) => (
        <div className="job-row" key={job.id}>
          <div>
            <strong>
              {job.kind === "studio"
                ? "Ad Studio · "
                : job.kind === "campaign_build"
                  ? "Campaign · "
                  : ""}
              {job.model_id}
            </strong>
            <p>
              {job.cancel_requested_at && !job.finished_at
                ? "Cancellation requested"
                : job.error_code
                  ? human(job.error_code)
                  : human(job.state)}
              {job.kind !== "generation"
                ? ""
                : job.worker_exit_verified_at
                  ? ` · Process exit verified ${when(job.worker_exit_verified_at)}`
                  : job.worker_pid
                    ? " · Process exit not yet verified"
                    : " · Worker not started"}
            </p>
          </div>
          {!job.finished_at && (
            <button
              className="button compact"
              disabled={busy === job.id}
              onClick={() => cancel(job)}
            >
              {busy === job.id
                ? "Requesting stop…"
                : job.kind === "campaign_build"
                  ? "Cancel remaining work"
                  : "Stop generation"}
            </button>
          )}
        </div>
      ))}
      <h3>Background consumer</h3>
      <p>
        {activity?.processes.some((process) => process.healthy)
          ? `Running · activity processing · ${activity.mail_transport === "smtp" ? "SMTP delivery selected" : "email saved locally; external delivery is not selected"}`
          : "No healthy consumer heartbeat. Delivery may be delayed."}
      </p>
      {activity?.processes
        .filter((process) => process.exit_verified_at)
        .slice(0, 2)
        .map((process) => (
          <p className="footnote" key={process.instance_id}>
            Previous consumer exit verified · code {process.exit_code} ·{" "}
            {when(process.exit_verified_at!)}
          </p>
        ))}
      <p className="footnote">
        {activity?.receipts.length || 0} recent activity receipts visible in
        your workspace. Receipts survive consumer restarts.
      </p>
    </section>
  );
}
