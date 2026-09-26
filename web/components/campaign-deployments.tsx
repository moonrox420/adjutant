"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import {
  parseDeploymentOptions,
  parseDeployments,
  type DeploymentOptions,
  type CampaignBuild,
} from "../lib/deployments";
import { Field, channelName, human, when } from "./ui";

export function CampaignDeployments({
  brandId,
  planId,
}: {
  brandId: string;
  planId: string;
}) {
  const [options, setOptions] = useState<DeploymentOptions | null>(null);
  const [builds, setBuilds] = useState<CampaignBuild[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const prefix = `/brands/${brandId}/plans/${planId}`;
  const pending = builds.some((build) =>
    ["queued", "running"].includes(build.state),
  );

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api<unknown>(
        `${prefix}/deployment-options`,
        "GET",
        undefined,
        controller.signal,
      ),
      api<unknown>(
        `${prefix}/deployments`,
        "GET",
        undefined,
        controller.signal,
      ),
    ])
      .then(([configuration, jobs]) => {
        setOptions(parseDeploymentOptions(configuration));
        setBuilds(parseDeployments(jobs));
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Campaign data could not be loaded.",
          );
      });
    return () => controller.abort();
  }, [prefix]);

  useEffect(() => {
    if (!pending) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const data = await api<unknown>(
          `${prefix}/deployments`,
          "GET",
          undefined,
          controller.signal,
        );
        setBuilds(parseDeployments(data));
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Campaign status could not be loaded.",
          );
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 2000);
      }
    }
    timer = setTimeout(poll, 1000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [prefix, pending]);

  async function refresh() {
    setBuilds(parseDeployments(await api<unknown>(`${prefix}/deployments`)));
  }
  async function create(
    event: FormEvent<HTMLFormElement>,
    option: DeploymentOptions["channels"][number],
  ) {
    event.preventDefault();
    if (!options || !option.fields || !option.connection_id) return;
    const form = new FormData(event.currentTarget);
    const settings: Record<string, unknown> = {};
    for (const field of option.fields) {
      const value = String(form.get(field.name) ?? "").trim();
      if (!value && !field.required) continue;
      settings[field.name] =
        field.type === "array"
          ? value.split(",").map((item) => item.trim())
          : field.type === "integer"
            ? Number(value)
            : field.format === "date-time"
              ? new Date(value).toISOString()
              : value;
    }
    setBusy(true);
    setError("");
    try {
      await api(`${prefix}/deployments`, "POST", {
        request_key: crypto.randomUUID(),
        connection_id: option.connection_id,
        expected_plan_hash: options.plan_hash,
        expected_guardrail_version: options.guardrail_version,
        settings,
      });
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Campaign creation failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function operate(id: string, operation: "cancel" | "retry") {
    setBusy(true);
    setError("");
    try {
      await api(`/brands/${brandId}/deployments/${id}/${operation}`, "POST");
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Campaign operation failed.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="campaign-deployments">
      <summary>Build and verify paused campaigns</summary>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {options?.channels.map((option) => (
        <div key={option.channel}>
          <h3>{channelName[option.channel] || option.channel}</h3>
          {!option.connection_id ? (
            <p>Connect and select an advertising account in Channels.</p>
          ) : !option.fields ? (
            <p>Campaign construction for this channel is unavailable.</p>
          ) : (
            <form
              className="form-grid"
              onSubmit={(event) => void create(event, option)}
            >
              <p className="span-2">
                Account: {option.external_account_name}. The provider campaign
                will be created paused.
              </p>
              {option.fields.map((field) => (
                <Field
                  key={field.name}
                  label={
                    field.label +
                    (field.type === "array" ? " (comma separated)" : "")
                  }
                >
                  <input
                    name={field.name}
                    required={field.required}
                    min={field.minimum}
                    max={field.maximum}
                    pattern={field.pattern ?? undefined}
                    defaultValue={field.default ?? undefined}
                    type={
                      field.type === "integer"
                        ? "number"
                        : field.format === "date-time"
                          ? "datetime-local"
                          : field.format === "uri"
                            ? "url"
                            : "text"
                    }
                  />
                </Field>
              ))}
              <button className="button primary span-2" disabled={busy}>
                {busy ? "Submitting…" : "Create paused campaign"}
              </button>
            </form>
          )}
        </div>
      ))}
      {builds.map((build) => (
        <article className="plan-card" key={build.id}>
          <h3>
            {channelName[build.channel] || build.channel}: {human(build.state)}
          </h3>
          {build.verified_at && (
            <p>Remote paused state verified {when(build.verified_at)}.</p>
          )}
          {build.error_message && (
            <p className="message error">{build.error_message}</p>
          )}
          {build.provider_errors.length > 0 && (
            <details>
              <summary>Provider rejection history</summary>
              <ul>
                {build.provider_errors.map((error, index) => (
                  <li key={`${error.occurred_at}:${index}`}>
                    {when(error.occurred_at)} · {error.raw_code}:{" "}
                    {error.raw_message}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <ul>
            {build.objects.map((object) => (
              <li key={object.id}>
                {human(object.level)} {object.native_id}: {human(object.state)}
              </li>
            ))}
          </ul>
          <p>
            {build.steps.filter((step) => step.verified_at).length} of{" "}
            {build.steps.length} recorded provider operations verified.
          </p>
          {build.state === "failed" && (
            <button
              className="button"
              disabled={busy}
              onClick={() => void operate(build.id, "retry")}
            >
              Reconcile and retry
            </button>
          )}
          {["queued", "running"].includes(build.state) && (
            <button
              className="button"
              disabled={busy}
              onClick={() => void operate(build.id, "cancel")}
            >
              Cancel remaining work
            </button>
          )}
        </article>
      ))}
    </details>
  );
}
