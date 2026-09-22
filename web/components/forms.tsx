"use client";

import { useState, type FormEvent } from "react";
import { api, type Account, type Channel, type Plan } from "../lib/api";
import { channelName, Field } from "./ui";

type SaveProps = { done: () => void; fail: (error: unknown) => void };

export function BrandForm({
  accounts,
  done,
  fail,
}: SaveProps & { accounts: Account[] }) {
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      await api("/brands", "POST", Object.fromEntries(data));
      done();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="form-stack">
      <p className="muted">
        Start with the business and its spending limits. Ad Studio reads your
        website automatically when you generate your first ads.
      </p>
      <Field label="Workspace">
        <select name="account_id" required>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name} · {a.account_type}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Brand name">
        <input
          name="display_name"
          required
          minLength={2}
          maxLength={120}
          autoFocus
        />
      </Field>
      <Field label="Business website">
        <input name="website_url" type="url" required />
      </Field>
      <Field label="Industry">
        <select name="vertical" defaultValue="home_services">
          {Object.entries({
            home_services: "Home services",
            retail: "Retail",
            ecommerce: "Ecommerce",
            hospitality: "Hospitality",
            professional_services: "Professional services",
            education: "Education",
            other: "Other",
            political: "Political / electoral",
            pharmaceutical: "Pharmaceutical",
            gambling: "Gambling",
            crypto: "Cryptocurrency",
            financial_income_claims: "Financial income claims",
          }).map(([value, label]) => (
            <option value={value} key={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <div className="form-grid">
        <Field label="Monthly ceiling (USD)">
          <input
            name="monthly_ceiling"
            type="number"
            min="0.01"
            max="99999999"
            step="0.01"
            required
          />
        </Field>
        <Field label="Daily ceiling (USD)">
          <input
            name="daily_ceiling"
            type="number"
            min="0.01"
            max="99999999"
            step="0.01"
            required
          />
        </Field>
      </div>
      <button className="button primary" disabled={busy}>
        {busy ? "Creating brand…" : "Create brand →"}
      </button>
    </form>
  );
}

export function PlanForm({
  brandId,
  channels,
  existing,
  done,
  fail,
}: SaveProps & {
  brandId: string;
  channels: Channel[];
  existing?: Plan;
}) {
  const doc = existing?.plan_document;
  const [busy, setBusy] = useState(false);
  const [allocations, setAllocations] = useState(
    doc?.allocations || [
      { channel: "meta", monthly_budget_usd: "", daily_budget_usd: "" },
    ],
  );
  function update(index: number, key: string, value: string) {
    setAllocations((current) =>
      current.map((a, i) => (i === index ? { ...a, [key]: value } : a)),
    );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const fields = Object.fromEntries(new FormData(event.currentTarget));
    const cents = allocations.reduce(
      (sum, a) => sum + Math.round(Number(a.monthly_budget_usd) * 100),
      0,
    );
    const body = {
      ...fields,
      allocations,
      monthly_budget_usd: (cents / 100).toFixed(2),
      ...(existing ? { expected_hash: existing.plan_hash } : {}),
    };
    try {
      await api(
        `/brands/${brandId}/plans${existing ? `/${existing.id}` : ""}`,
        existing ? "PUT" : "POST",
        body,
      );
      done();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form-stack" onSubmit={submit}>
      <p className="muted">
        Define the hypothesis, audience, and channel budgets. Saving creates a
        draft. Review first-launch scope when the plan and creative are ready.
      </p>
      <Field label="Campaign name">
        <input
          name="name"
          required
          minLength={3}
          maxLength={140}
          defaultValue={doc?.name}
          autoFocus
        />
      </Field>
      <div className="form-grid">
        <Field label="Objective">
          <select name="objective" defaultValue={doc?.objective || "leads"}>
            {[
              "leads",
              "sales",
              "traffic",
              "awareness",
              "engagement",
              "app_promotion",
              "video_views",
            ].map((v) => (
              <option key={v} value={v}>
                {v.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Goal">
          <select
            name="goal_kind"
            defaultValue={doc?.goal_kind || "target_cpa"}
          >
            <option value="target_cpa">Target CPA</option>
            <option value="target_roas">Target ROAS</option>
            <option value="lead_volume">Lead volume</option>
            <option value="efficient_spend">Efficient spend</option>
          </select>
        </Field>
      </div>
      <Field label="Goal value">
        <input
          name="goal_value"
          type="number"
          min="0.01"
          step="0.01"
          required
          defaultValue={doc?.goal_value}
        />
      </Field>
      <Field label="Audience">
        <textarea
          name="audience"
          required
          minLength={5}
          maxLength={3000}
          defaultValue={doc?.audience}
          rows={2}
        />
      </Field>
      <Field
        label="Creative hypothesis"
        hint="What do you expect this campaign to demonstrate?"
      >
        <textarea
          name="hypothesis"
          required
          minLength={10}
          maxLength={3000}
          defaultValue={doc?.hypothesis}
          rows={2}
        />
      </Field>
      <Field label="Strategy and rationale">
        <textarea
          name="rationale"
          required
          minLength={20}
          maxLength={8000}
          defaultValue={doc?.rationale}
          rows={3}
        />
      </Field>
      <fieldset>
        <legend>Channel allocation</legend>
        {allocations.map((allocation, index) => (
          <div key={index} className="allocation-row">
            <Field label="Channel">
              <select
                aria-label={`Channel ${index + 1}`}
                value={allocation.channel}
                onChange={(e) => update(index, "channel", e.target.value)}
              >
                {channels.map((c) => (
                  <option key={c.channel} value={c.channel}>
                    {channelName[c.channel]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Monthly USD">
              <input
                aria-label={`Monthly budget ${index + 1}`}
                type="number"
                min="0.01"
                step="0.01"
                required
                value={allocation.monthly_budget_usd}
                onChange={(e) =>
                  update(index, "monthly_budget_usd", e.target.value)
                }
              />
            </Field>
            <Field label="Daily USD">
              <input
                aria-label={`Daily budget ${index + 1}`}
                type="number"
                min="0.01"
                step="0.01"
                required
                value={allocation.daily_budget_usd}
                onChange={(e) =>
                  update(index, "daily_budget_usd", e.target.value)
                }
              />
            </Field>
            {allocations.length > 1 && (
              <button
                className="icon-button"
                type="button"
                aria-label={`Remove channel ${index + 1}`}
                onClick={() =>
                  setAllocations((a) => a.filter((_, i) => i !== index))
                }
              >
                ×
              </button>
            )}
          </div>
        ))}
      </fieldset>
      {allocations.length < channels.length && (
        <button
          type="button"
          className="button quiet"
          onClick={() => {
            const next = channels.find(
              (c) => !allocations.some((a) => a.channel === c.channel),
            );
            if (next)
              setAllocations([
                ...allocations,
                {
                  channel: next.channel,
                  monthly_budget_usd: "",
                  daily_budget_usd: "",
                },
              ]);
          }}
        >
          + Add channel
        </button>
      )}
      <button className="button primary" disabled={busy}>
        {busy
          ? "Saving draft…"
          : existing
            ? "Save new revision →"
            : "Save campaign draft →"}
      </button>
    </form>
  );
}
