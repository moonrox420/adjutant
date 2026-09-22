"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Plan } from "../lib/api";
import { channelName, Field, money, when } from "./ui";

type Guardrails = {
  version: number;
  monthly_spend_cap_usd: string;
  daily_spend_cap_usd: string;
  max_daily_spend_increase_pct: string;
  max_new_campaigns_per_day: number;
  max_new_ads_per_day: number;
  per_channel_cap_pct: string;
  min_channel_floor_pct: string;
  blocked_claims: string[];
  requires_approval_above_usd: string | null;
};
type LaunchScope = {
  plan_hash: string;
  review_hash: string;
  review: {
    creatives: {
      id: string;
      copy: Record<string, unknown>;
      studio_renditions: { id: string; aspect_ratio: string }[];
      renditions: { id: string; channel: string; asset_id: string | null }[];
    }[];
  };
  guardrails: Guardrails;
  channels: {
    channel: string;
    connection_id: string | null;
    external_account_name: string | null;
    external_ad_account_id: string | null;
    authorized_at: string | null;
    health: string | null;
    verified_at: string | null;
  }[];
};

const fields = [
  ["monthly_spend_cap_usd", "Monthly spend cap (USD)", "0.01", "0.01"],
  ["daily_spend_cap_usd", "Daily spend cap (USD)", "0.01", "0.01"],
  ["max_daily_spend_increase_pct", "Maximum daily increase (%)", "0", "0.01"],
  ["max_new_campaigns_per_day", "New campaigns per day", "1", "1"],
  ["max_new_ads_per_day", "New ads per day", "1", "1"],
  [
    "per_channel_cap_pct",
    "Maximum monthly share per channel (%)",
    "0.01",
    "0.01",
  ],
  [
    "min_channel_floor_pct",
    "Minimum monthly share per channel (%)",
    "0",
    "0.01",
  ],
] as const;

export function GuardrailEditor({
  brandId,
  canManage,
}: {
  brandId: string;
  canManage: boolean;
}) {
  const [limits, setLimits] = useState<Guardrails | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<Guardrails>(
      `/brands/${brandId}/guardrails`,
      "GET",
      undefined,
      controller.signal,
    )
      .then(setLimits)
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(String(cause.message || cause));
      });
    return () => controller.abort();
  }, [brandId, reload]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!limits) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      setLimits(
        await api<Guardrails>(`/brands/${brandId}/guardrails`, "PUT", {
          ...Object.fromEntries(fields.map(([key]) => [key, form.get(key)])),
          expected_version: limits.version,
          blocked_claims: String(form.get("blocked_claims") || "")
            .split("\n")
            .map((v) => v.trim())
            .filter(Boolean),
          requires_approval_above_usd:
            form.get("requires_approval_above_usd") || null,
        }),
      );
      setSaved(true);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Limits could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Autonomy guardrails</h2>
      <p className="muted">
        First-launch review uses these limits. Changes require a new review only
        for an approval that has not yet been consumed.
      </p>
      {error && (
        <p className="message error" role="alert">
          {error}{" "}
          <button
            className="button"
            onClick={() => {
              setError("");
              setReload((n) => n + 1);
            }}
          >
            Reload limits
          </button>
        </p>
      )}
      {!limits ? (
        <p>Loading limits…</p>
      ) : (
        <form key={limits.version} className="form-stack" onSubmit={save}>
          {fields.map(([key, label, min, step]) => (
            <Field key={key} label={label}>
              <input
                name={key}
                type="number"
                min={min}
                step={step}
                required
                defaultValue={limits[key]}
                disabled={!canManage || busy}
              />
            </Field>
          ))}
          <Field
            label="Escalate a single budget change above (USD)"
            hint="Leave empty to use the other guardrails without an additional dollar threshold."
          >
            <input
              name="requires_approval_above_usd"
              type="number"
              min="0.01"
              step="0.01"
              defaultValue={limits.requires_approval_above_usd ?? ""}
              disabled={!canManage || busy}
            />
          </Field>
          <Field
            label="Blocked claims"
            hint="One phrase per line. Enforced during generation, editing, and rendering."
          >
            <textarea
              name="blocked_claims"
              rows={5}
              defaultValue={limits.blocked_claims.join("\n")}
              disabled={!canManage || busy}
            />
          </Field>
          <button className="button primary" disabled={!canManage || busy}>
            {busy ? "Saving…" : "Save guardrails"}
          </button>
          {saved && (
            <p role="status">Guardrails saved as version {limits.version}.</p>
          )}
        </form>
      )}
    </section>
  );
}

export function LaunchReview({
  brandId,
  plan,
  canApprove,
}: {
  brandId: string;
  plan: Plan;
  canApprove: boolean;
}) {
  const [scope, setScope] = useState<LaunchScope | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const requestKey = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    requestKey.current = null;
    api<LaunchScope>(
      `/brands/${brandId}/plans/${plan.id}/launch-scope`,
      "GET",
      undefined,
      controller.signal,
    )
      .then(setScope)
      .catch((cause) => {
        if (!controller.signal.aborted)
          setError(String(cause.message || cause));
      });
    return () => controller.abort();
  }, [brandId, plan.id, plan.plan_hash, reload]);
  async function approve() {
    if (!scope) return;
    requestKey.current ??= crypto.randomUUID();
    setBusy(true);
    setError("");
    try {
      setScope(
        await api<LaunchScope>(
          `/brands/${brandId}/plans/${plan.id}/authorize-launch`,
          "POST",
          {
            request_key: requestKey.current,
            expected_hash: plan.plan_hash,
            expected_review_hash: scope.review_hash,
            expected_guardrail_version: scope.guardrails.version,
          },
        ),
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Authorization failed.",
      );
    } finally {
      setBusy(false);
    }
  }
  const needsApproval = scope?.channels.some((c) => !c.authorized_at);
  const stalePlan = Boolean(scope && scope.plan_hash !== plan.plan_hash);
  return (
    <section className="form-stack">
      <h3>First-launch authorization</h3>
      <p>
        Approve each selected channel account once. Later campaigns and changes
        use the brand guardrails. Adding another channel account requires its
        own first-launch review.
      </p>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {stalePlan && (
        <p className="message error" role="alert">
          This plan changed after the page loaded.{" "}
          <button className="button" onClick={() => window.location.reload()}>
            Reload the current plan
          </button>
        </p>
      )}
      {!scope ? (
        <p>Loading launch scope…</p>
      ) : (
        <>
          <p>
            Brand limits: {money(scope.guardrails.monthly_spend_cap_usd)} /
            month; {money(scope.guardrails.daily_spend_cap_usd)} / day.
            Guardrail version {scope.guardrails.version}.
          </p>
          <dl>
            {fields.slice(2).map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{scope.guardrails[key]}</dd>
              </div>
            ))}
            <dt>Single-change escalation threshold</dt>
            <dd>
              {scope.guardrails.requires_approval_above_usd
                ? money(scope.guardrails.requires_approval_above_usd)
                : "No additional dollar threshold"}
            </dd>
            <dt>Blocked claims</dt>
            <dd>
              {scope.guardrails.blocked_claims.join("; ") || "None specified"}
            </dd>
          </dl>
          {scope.review.creatives.length === 0 ? (
            <p>No finished creative is attached to this plan.</p>
          ) : (
            scope.review.creatives.map((creative) => (
              <article key={creative.id} className="plan-card">
                <h4>Attached ad creative</h4>
                <ReviewCopy value={creative.copy} />
                <div className="studio-grid">
                  {creative.studio_renditions.map((rendition) => (
                    <figure key={rendition.id}>
                      <img
                        src={`/api/brands/${brandId}/renditions/${rendition.id}.png`}
                        alt={`Finished ad in ${rendition.aspect_ratio} format`}
                        style={{
                          maxWidth: "100%",
                          maxHeight: 480,
                          objectFit: "contain",
                        }}
                      />
                      <figcaption>{rendition.aspect_ratio}</figcaption>
                    </figure>
                  ))}
                  {creative.renditions
                    .filter((r) => r.asset_id)
                    .map((rendition) => (
                      <figure key={rendition.id}>
                        <img
                          src={`/api/brands/${brandId}/assets/${rendition.asset_id}/preview`}
                          alt={`Ad for ${channelName[rendition.channel] || rendition.channel}`}
                          style={{
                            maxWidth: "100%",
                            maxHeight: 480,
                            objectFit: "contain",
                          }}
                        />
                        <figcaption>
                          {channelName[rendition.channel] || rendition.channel}
                        </figcaption>
                      </figure>
                    ))}
                </div>
              </article>
            ))
          )}
          <ul>
            {scope.channels.map((c) => (
              <li key={c.channel}>
                {channelName[c.channel] || c.channel} ·{" "}
                {c.external_account_name ||
                  c.external_ad_account_id ||
                  "Select an account in Channels"}{" "}
                ·{" "}
                {c.authorized_at
                  ? `First launch authorized ${when(c.authorized_at)}`
                  : "First launch awaits authorization"}
              </li>
            ))}
          </ul>
          {needsApproval ? (
            <button
              className="button primary"
              onClick={approve}
              disabled={busy || !canApprove || stalePlan}
            >
              {busy
                ? "Verifying authorization…"
                : "Authorize first launch within these guardrails"}
            </button>
          ) : (
            <p role="status">
              The selected accounts already have first-launch authorization.
            </p>
          )}
        </>
      )}
      <button
        className="button"
        disabled={busy}
        onClick={() => {
          setError("");
          setReload((n) => n + 1);
        }}
      >
        Reload launch scope
      </button>
    </section>
  );
}

function ReviewCopy({ value }: { value: unknown }) {
  if (typeof value === "string") return <p>{value}</p>;
  if (Array.isArray(value))
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <ReviewCopy value={item} />
          </li>
        ))}
      </ul>
    );
  if (value && typeof value === "object")
    return (
      <dl>
        {Object.entries(value)
          .filter(
            ([key]) =>
              !["image_prompt", "image_url", "understanding"].includes(key),
          )
          .map(([key, item]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd>
                <ReviewCopy value={item} />
              </dd>
            </div>
          ))}
      </dl>
    );
  return null;
}
