"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  activateLicense,
  deactivateLicense,
  fetchLicenseStatus,
  type LicenseStatus,
} from "../lib/api";
import { Badge, Field, when } from "./ui";

export function LicenseCard({ onUpdated }: { onUpdated?: () => Promise<void> | void }) {
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [keyInput, setKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);

  async function loadStatus() {
    try {
      const data = await fetchLicenseStatus();
      setLicense(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load license status.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStatus();
  }, []);

  async function handleActivate(e: FormEvent) {
    e.preventDefault();
    if (!keyInput.trim()) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const updated = await activateLicense(keyInput.trim());
      setLicense(updated);
      setKeyInput("");
      setSuccess("License activated successfully! Machine lease recorded.");
      if (onUpdated) await onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Activation failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDeactivate() {
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const res = await deactivateLicense();
      setSuccess(res.message);
      setConfirmDeactivate(false);
      await loadStatus();
      if (onUpdated) await onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Deactivation failed.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="license-card loading">
        <p className="muted">Verifying machine license lease…</p>
      </div>
    );
  }

  const isPro = license?.tier === "pro" || license?.status === "active";
  const badgeTone =
    license?.status === "active"
      ? "positive"
      : license?.status === "grace_period"
        ? "attention"
        : license?.dev_mode
          ? "accent"
          : "critical";

  return (
    <div className="license-card">
      <div className="license-header">
        <div>
          <span className="eyebrow">APPLIANCE LICENSING</span>
          <h3>{license?.tier_display || "Adjutant Commercial Subscription"}</h3>
        </div>
        <Badge tone={badgeTone}>
          {license?.dev_mode && license.status === "unlicensed"
            ? "DEV MODE (BYPASSED)"
            : license?.status?.toUpperCase() || "UNLICENSED"}
        </Badge>
      </div>

      {license?.in_grace_period && (
        <div className="message attention" role="alert" style={{ marginTop: "12px" }}>
          <strong>Offline Grace Period Active:</strong> Adjutant is operating offline or lease
          needs verification. Autonomous mutations will pause in {license.days_remaining} day(s)
          on {license.grace_period_end ? when(license.grace_period_end) : "expiry"} unless refreshed.
        </div>
      )}

      {license?.status === "expired" && (
        <div className="message error" role="alert" style={{ marginTop: "12px" }}>
          <strong>License Expired:</strong> Autonomous loop adjustments are suspended. Enter a valid
          subscription key below to reactivate continuous ad execution.
        </div>
      )}

      {license?.status === "unlicensed" && !license.dev_mode && (
        <div className="message attention" role="alert" style={{ marginTop: "12px" }}>
          <strong>Activation Required:</strong> Adjutant requires an active commercial license for
          autonomous campaign optimization and execution.
        </div>
      )}

      {error && (
        <div className="message error" role="alert" style={{ marginTop: "12px" }}>
          {error}
        </div>
      )}

      {success && (
        <div className="message success" role="status" style={{ marginTop: "12px" }}>
          {success}
        </div>
      )}

      <div className="license-grid" style={{ margin: "18px 0" }}>
        <div className="license-detail">
          <small className="muted">Subscriber</small>
          <strong>{license?.customer_name || "Unregistered"}</strong>
          {license?.customer_email && (
            <span className="muted" style={{ fontSize: "12px" }}>
              {license.customer_email}
            </span>
          )}
        </div>
        <div className="license-detail">
          <small className="muted">Hardware Lease Binding</small>
          <strong style={{ fontFamily: "monospace", fontSize: "12px" }}>
            {license?.instance_id || "Unbound local machine"}
          </strong>
          <span className="muted" style={{ fontSize: "12px" }}>
            {license?.status === "active" ? "14-day offline cache armed" : "Not bound"}
          </span>
        </div>
        <div className="license-detail">
          <small className="muted">Renewal / Expiration</small>
          <strong>
            {license?.expires_at ? when(license.expires_at) : "No active expiration"}
          </strong>
          {license?.status === "active" && (
            <span className="muted" style={{ fontSize: "12px" }}>
              {license.days_remaining} day(s) remaining in period
            </span>
          )}
        </div>
        {license?.license_key_masked && (
          <div className="license-detail">
            <small className="muted">License Key</small>
            <strong style={{ fontFamily: "monospace" }}>{license.license_key_masked}</strong>
          </div>
        )}
      </div>

      <div className="license-actions" style={{ borderTop: "1px solid var(--line)", paddingTop: "18px" }}>
        {license?.status !== "active" ? (
          <form onSubmit={handleActivate} className="form-stack">
            <Field
              label="Enter Commercial License Key"
              hint="Paste the license key provided in your LemonSqueezy subscription receipt."
            >
              <div style={{ display: "flex", gap: "8px" }}>
                <input
                  type="text"
                  placeholder="e.g. 8B2F-93A1-47C0-D29E"
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  style={{ flex: 1, fontFamily: "monospace" }}
                  disabled={busy}
                  required
                />
                <button type="submit" className="button primary" disabled={busy || !keyInput.trim()}>
                  {busy ? "Activating…" : "Activate License →"}
                </button>
              </div>
            </Field>
          </form>
        ) : (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="muted" style={{ fontSize: "13px" }}>
              License verified. This appliance is running autonomously on your hardware.
            </span>
            {!confirmDeactivate ? (
              <button
                type="button"
                className="button quiet danger compact"
                disabled={busy}
                onClick={() => setConfirmDeactivate(true)}
              >
                Deactivate License
              </button>
            ) : (
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <span style={{ fontSize: "12px", color: "var(--red)" }}>
                  Unlink this machine?
                </span>
                <button
                  type="button"
                  className="button danger compact"
                  disabled={busy}
                  onClick={handleDeactivate}
                >
                  {busy ? "Unlinking…" : "Yes, Deactivate"}
                </button>
                <button
                  type="button"
                  className="button compact"
                  disabled={busy}
                  onClick={() => setConfirmDeactivate(false)}
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
