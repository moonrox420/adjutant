"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type Brand,
  type Channel,
  type Me,
  type Plan,
  type Workspace,
  type Status,
  type Approval,
} from "../lib/api";
import { AccountAccess } from "../components/account";
import { Jobs } from "../components/jobs";
import { AuditExport } from "../components/audit-export";
import { BrandForm, PlanForm } from "../components/forms";
import { GenerateForm, Research } from "../components/research";
import { DeploymentPreflight } from "../components/preflight";
import {
  Badge,
  channelName,
  Dialog,
  Empty,
  Field,
  human,
  money,
  when,
} from "../components/ui";

const tabs = [
  "Overview",
  "Brand intelligence",
  "Campaign plans",
  "Approvals",
  "Activity",
  "Channels",
  "Guardrails",
] as const;
type Tab = (typeof tabs)[number];
const marks = ["◫", "◎", "▤", "✓", "≋", "↗", "⊞"];

export default function Console() {
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [selected, setSelected] = useState("");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");
  const [modal, setModal] = useState<
    "brand" | "plan" | "stop" | "generate" | null
  >(null);
  const [editing, setEditing] = useState<Plan | undefined>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const workspaceRequest = useRef(0);

  const fail = useCallback((error: unknown) => {
    setError(
      error instanceof Error
        ? error.message
        : "The request could not be completed.",
    );
    if (error instanceof ApiError && error.status === 401) {
      setMe(null);
      setWorkspace(null);
    }
  }, []);
  const refresh = useCallback(async () => {
    const [identity, list, registry, health] = await Promise.all([
      api<Me>("/me"),
      api<Brand[]>("/brands"),
      api<Channel[]>("/channels"),
      api<Status>("/status"),
    ]);
    setMe(identity);
    setBrands(list);
    setChannels(registry);
    setStatus(health);
    setSelected((current) =>
      list.some((b) => b.id === current) ? current : list[0]?.id || "",
    );
  }, []);
  const refreshWorkspace = useCallback(async (brandId: string) => {
    const request = ++workspaceRequest.current;
    const data = await api<Workspace>(`/brands/${brandId}/workspace`);
    if (request === workspaceRequest.current) setWorkspace(data);
  }, []);
  useEffect(() => {
    refresh()
      .catch((e) => {
        if (!(e instanceof ApiError && e.status === 401)) fail(e);
      })
      .finally(() => setBooting(false));
  }, [refresh, fail]);
  useEffect(() => {
    setWorkspace(null);
    if (selected && me) refreshWorkspace(selected).catch(fail);
    return () => {
      workspaceRequest.current += 1;
    };
  }, [selected, me?.id, refreshWorkspace, fail]);

  useEffect(() => {
    const listener = (event: StorageEvent) => {
      if (event.key === "adjutant.logout") window.location.replace("/account");
    };
    window.addEventListener("storage", listener);
    return () => window.removeEventListener("storage", listener);
  }, []);
  async function signOut(everywhere: boolean) {
    setBusy(true);
    try {
      const result = await api<{ cancellation_verified: boolean }>(
        everywhere ? "/auth/logout-all" : "/auth/logout",
        "POST",
      );
      try {
        localStorage.setItem("adjutant.logout", String(Date.now()));
      } finally {
        window.location.replace(
          result.cancellation_verified
            ? "/account#signed-out"
            : "/account#stop-pending",
        );
      }
    } catch (e) {
      fail(e);
      setBusy(false);
    }
  }

  async function mutate(
    path: string,
    data?: unknown,
    method = "POST",
    message = "Changes saved.",
  ) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(path, method, data);
      await refresh();
      if (selected) await refreshWorkspace(selected);
      setNotice(message);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function formDone() {
    setModal(null);
    setEditing(undefined);
    setError("");
    try {
      await refresh();
      if (selected) await refreshWorkspace(selected);
      setNotice("Saved to your workspace.");
    } catch (e) {
      fail(e);
    }
  }
  const pending =
    workspace?.approvals.filter(
      (a) =>
        ["pending_internal", "pending_client"].includes(a.state) &&
        !a.is_expired,
    ) || [];
  const userRoles =
    me?.seats
      .filter(
        (s) =>
          s.account_id === workspace?.brand.account_id &&
          (!s.brand_id || s.brand_id === selected),
      )
      .map((s) => s.role) || [];
  const canEdit = userRoles.some((r) =>
    ["owner", "admin", "buyer"].includes(r),
  );
  const brand = brands.find((b) => b.id === selected);

  if (booting)
    return (
      <main className="boot">
        <span className="wordmark">
          adjutant<span>↗</span>
        </span>
        <p>Opening your workspace…</p>
      </main>
    );
  if (!me)
    return <AccountAccess signedIn={() => window.location.replace("/")} />;

  return (
    <div className="app-shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <a className="wordmark" href="/">
          adjutant<span>↗</span>
        </a>
        <div className="workspace-label">
          <span className="avatar">
            {me.accounts[0]?.display_name.slice(0, 1) || "A"}
          </span>
          <div>
            <strong>{me.accounts[0]?.display_name}</strong>
            <small>
              {human(me.accounts[0]?.account_type || "Workspace")} workspace
            </small>
          </div>
        </div>
        <p className="nav-label">OPERATIONS</p>
        <nav aria-label="Main navigation">
          {tabs.map((name, index) => (
            <button
              key={name}
              className={`nav-item ${tab === name ? "active" : ""}`}
              onClick={() => setTab(name)}
              aria-current={tab === name ? "page" : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                {marks[index]}
              </span>
              {name}
              {name === "Approvals" && pending.length > 0 && (
                <span className="nav-count">{pending.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="system-note">
            <span className="status-dot" />
            Review before launch
            <small>Every spend decision has an owner.</small>
          </div>
          <button className="user-button" onClick={() => signOut(false)}>
            <span className="avatar small">{me.email[0].toUpperCase()}</span>
            <span>
              {me.email}
              <small>Sign out</small>
            </span>
          </button>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            Workspace <span>/</span> {tab}
          </div>
          <div className="topbar-right">
            <details className="account-menu">
              <summary>Account</summary>
              <div>
                <p>{me.email}</p>
                <button
                  className="button compact"
                  disabled={busy}
                  onClick={() => signOut(false)}
                >
                  Sign out
                </button>
                <button
                  className="button compact"
                  disabled={busy}
                  onClick={() => signOut(true)}
                >
                  Sign out everywhere
                </button>
                <a href="/account">Account recovery</a>
              </div>
            </details>
            <label className="sr-only" htmlFor="brand-select">
              Current brand
            </label>
            <select
              id="brand-select"
              className="brand-select"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              {!brands.length && <option value="">No brands yet</option>}
              {brands.map((b) => (
                <option value={b.id} key={b.id}>
                  {b.display_name}
                </option>
              ))}
            </select>
            <button
              className="button compact"
              onClick={() => setModal("brand")}
            >
              + Add brand
            </button>
          </div>
        </header>
        <main id="main" className="main-content">
          {tab === "Activity" && <Jobs />}
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {brand?.display_name || "YOUR WORKSPACE"} / CAMPAIGN OPERATIONS
              </span>
              <h1>
                {tab === "Overview"
                  ? "A clear view. A considered next move."
                  : tab}
              </h1>
              <p>
                {
                  {
                    Overview:
                      "Your brand, your plans, and the decisions that move them forward.",
                    "Brand intelligence":
                      "The source of truth behind every campaign.",
                    "Campaign plans":
                      "A hypothesis, an audience, and a budget you can account for.",
                    Approvals:
                      "Review the full picture before giving a plan your approval.",
                    Activity:
                      "A permanent record of what changed, who acted, and why.",
                    Channels: "Platform capabilities and connection readiness.",
                    Guardrails:
                      "Set the boundaries that every campaign must respect.",
                  }[tab]
                }
              </p>
            </div>
            {tab === "Campaign plans" && canEdit && (
              <div className="card-actions">
                <button
                  className="button"
                  disabled={
                    !workspace?.brand.brand_graph_confirmed_at ||
                    !!workspace?.stop
                  }
                  onClick={() => setModal("generate")}
                >
                  Generate draft ↗
                </button>
                <button
                  className="button primary"
                  disabled={
                    !workspace?.brand.brand_graph_confirmed_at ||
                    !!workspace?.stop
                  }
                  onClick={() => {
                    setEditing(undefined);
                    setModal("plan");
                  }}
                >
                  + New campaign plan
                </button>
              </div>
            )}
          </div>
          {error && (
            <div role="alert" className="message error">
              {error}
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                ×
              </button>
            </div>
          )}
          {notice && (
            <div role="status" className="message success">
              {notice}
              <button aria-label="Dismiss notice" onClick={() => setNotice("")}>
                ×
              </button>
            </div>
          )}
          {workspace?.stop && (
            <div className="message error">
              <strong>Local operations stopped.</strong> {workspace.stop.reason}{" "}
              Platform pause verification is unavailable.
            </div>
          )}
          {!brands.length ? (
            <section className="panel">
              <Empty
                title="Every good campaign starts with a known brand."
                action={
                  <button
                    className="button primary"
                    onClick={() => setModal("brand")}
                  >
                    Add your first brand →
                  </button>
                }
              >
                Add your business, establish budget ceilings, and build a
                sourced brand profile.
              </Empty>
            </section>
          ) : !workspace ? (
            <p role="status">Loading brand records…</p>
          ) : (
            <>
              {tab === "Overview" && (
                <>
                  <div className="stats-grid">
                    <Stat
                      label="AWAITING A DECISION"
                      value={String(pending.length)}
                      detail="Campaign plans ready for review"
                      accent
                    />
                    <Stat
                      label="MONTHLY CEILING"
                      value={money(brand?.monthly_ceiling)}
                      detail="Your maximum planned commitment"
                    />
                    <Stat
                      label="CAMPAIGN PLANS"
                      value={String(workspace.plans.length)}
                      detail={`${workspace.plans.filter((p) => p.state === "approved").length} approved · ${workspace.plans.filter((p) => p.state === "draft").length} in draft`}
                    />
                    <Stat
                      label="VERIFIED LIVE CAMPAIGNS"
                      value={String(brand?.active_objects || 0)}
                      detail="No ad accounts connected"
                    />
                  </div>
                  <div className="overview-grid">
                    <section className="panel next-step">
                      <span className="eyebrow">THE NEXT CONSIDERED MOVE</span>
                      <h2>
                        {!workspace.brand.brand_graph_confirmed_at
                          ? "Get the brand story right."
                          : pending.length
                            ? "Your judgment is needed."
                            : "Turn understanding into a plan."}
                      </h2>
                      <p>
                        {!workspace.brand.brand_graph_confirmed_at
                          ? "Add the facts that make this business distinct, attach their sources, and confirm the profile before campaign creation."
                          : pending.length
                            ? `${pending.length} campaign ${pending.length === 1 ? "plan is" : "plans are"} waiting. Review the audience, strategy, and budgets together.`
                            : "Start with a clear hypothesis and a channel budget. Every draft stays under your control until review."}
                      </p>
                      <button
                        className="button primary"
                        onClick={() =>
                          setTab(
                            !workspace.brand.brand_graph_confirmed_at
                              ? "Brand intelligence"
                              : pending.length
                                ? "Approvals"
                                : "Campaign plans",
                          )
                        }
                      >
                        {!workspace.brand.brand_graph_confirmed_at
                          ? "Build brand intelligence"
                          : pending.length
                            ? "Open approval queue"
                            : "View campaign plans"}{" "}
                        →
                      </button>
                      <div className="step-line">
                        <span
                          className={
                            workspace.brand.brand_graph_confirmed_at
                              ? "complete"
                              : "current"
                          }
                        >
                          01 <b>Understand</b>
                        </span>
                        <span
                          className={workspace.plans.length ? "complete" : ""}
                        >
                          02 <b>Plan</b>
                        </span>
                        <span className={pending.length ? "current" : ""}>
                          03 <b>Review</b>
                        </span>
                        <span>
                          04 <b>Launch</b>
                        </span>
                      </div>
                    </section>
                    <section className="panel">
                      <div className="panel-title">
                        <h2>Operational readiness</h2>
                        <Badge tone="green">Connected</Badge>
                      </div>
                      <ul className="readiness">
                        <li>
                          <span>Brand profile</span>
                          <Badge
                            tone={
                              workspace.brand.brand_graph_confirmed_at
                                ? "green"
                                : "amber"
                            }
                          >
                            {workspace.brand.brand_graph_confirmed_at
                              ? "Confirmed"
                              : "Needs confirmation"}
                          </Badge>
                        </li>
                        <li>
                          <span>Budget boundaries</span>
                          <Badge tone="green">Set</Badge>
                        </li>
                        <li>
                          <span>Approval authority</span>
                          <Badge tone="green">Active</Badge>
                        </li>
                        <li>
                          <span>Ad platform connections</span>
                          <Badge>Not connected</Badge>
                        </li>
                      </ul>
                      <p className="footnote">
                        Approval records a plan decision. Launch also requires
                        approved creative, platform access, and deployment
                        checks.
                      </p>
                    </section>
                  </div>
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Recent decisions & changes</h2>
                      <button
                        className="text-button"
                        onClick={() => setTab("Activity")}
                      >
                        View all activity ↗
                      </button>
                    </div>
                    <Activity items={workspace.audit.slice(0, 5)} />
                  </section>
                </>
              )}
              {tab === "Brand intelligence" && (
                <>
                  <Research
                    key={selected}
                    brandId={selected}
                    canEdit={canEdit}
                  />
                  <div className="two-column">
                    <section className="panel">
                      <div className="panel-title">
                        <h2>Brand facts</h2>
                        <Badge
                          tone={
                            workspace.brand.brand_graph_confirmed_at
                              ? "green"
                              : "amber"
                          }
                        >
                          {workspace.brand.brand_graph_confirmed_at
                            ? "Confirmed"
                            : "Unconfirmed"}
                        </Badge>
                      </div>
                      {!workspace.assertions.length ? (
                        <Empty title="Build from evidence.">
                          Add your offers, audience, voice, and differentiators
                          with a source for each fact.
                        </Empty>
                      ) : (
                        <div className="facts">
                          {workspace.assertions.map((f) => (
                            <article key={f.id}>
                              <div className="fact-label">
                                {human(f.field_path)}
                                <Badge
                                  tone={
                                    f.human_confirmed_at ? "green" : "amber"
                                  }
                                >
                                  {f.human_confirmed_at
                                    ? "Confirmed"
                                    : "Review"}
                                </Badge>
                              </div>
                              <p>{f.value}</p>
                              <a
                                href={f.provenance_uri}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {f.provenance_uri} ↗
                              </a>
                            </article>
                          ))}
                        </div>
                      )}
                      {canEdit &&
                        workspace.assertions.length > 0 &&
                        !workspace.brand.brand_graph_confirmed_at && (
                          <button
                            className="button primary"
                            disabled={busy}
                            onClick={() =>
                              mutate(
                                `/brands/${selected}/confirm`,
                                undefined,
                                "POST",
                                "Brand facts confirmed. Campaign planning is available.",
                              )
                            }
                          >
                            Confirm these brand facts ✓
                          </button>
                        )}
                    </section>
                    <section className="panel">
                      <h2>Add a sourced fact</h2>
                      <p className="muted">
                        Updating a fact reopens brand confirmation and voids
                        outstanding approvals.
                      </p>
                      <form
                        className="form-stack"
                        onSubmit={(e) => {
                          e.preventDefault();
                          const form = e.currentTarget;
                          const data = Object.fromEntries(new FormData(form));
                          void mutate(`/brands/${selected}/assertions`, data);
                        }}
                      >
                        <Field label="Fact category">
                          <select name="field_path">
                            <option value="offerings">
                              Products & services
                            </option>
                            <option value="audience">Audience</option>
                            <option value="brand_voice">Brand voice</option>
                            <option value="differentiators">
                              Differentiators
                            </option>
                            <option value="offers">Current offers</option>
                          </select>
                        </Field>
                        <Field label="What we know">
                          <textarea
                            name="value"
                            required
                            rows={4}
                            maxLength={4000}
                          />
                        </Field>
                        <Field label="Source URL">
                          <input name="provenance_uri" type="url" required />
                        </Field>
                        <button
                          className="button primary"
                          disabled={busy || !canEdit}
                        >
                          Save sourced fact →
                        </button>
                      </form>
                    </section>
                  </div>
                </>
              )}
              {tab === "Campaign plans" && (
                <section className="panel">
                  {!workspace.brand.brand_graph_confirmed_at && (
                    <div className="message warning">
                      Confirm brand intelligence before creating a campaign
                      plan.
                    </div>
                  )}
                  {!workspace.plans.length ? (
                    <Empty title="Your next campaign starts here.">
                      Once the brand is confirmed, create a plan with a testable
                      hypothesis and explicit channel budgets.
                    </Empty>
                  ) : (
                    <div className="plan-list">
                      {workspace.plans.map((p) => (
                        <article className="plan-card" key={p.id}>
                          <div className="plan-card-top">
                            <div>
                              <span className="eyebrow">
                                {human(p.objective)} / REVISION{" "}
                                {p.plan_document.revision || 1}
                              </span>
                              <h2>{p.name}</h2>
                            </div>
                            <Badge
                              tone={
                                p.state === "approved"
                                  ? "green"
                                  : p.state === "pending_approval"
                                    ? "amber"
                                    : "neutral"
                              }
                            >
                              {human(p.state)}
                            </Badge>
                          </div>
                          <p>{p.rationale}</p>
                          <div className="plan-meta">
                            <strong>
                              {money(p.monthly_budget_usd)}{" "}
                              <small>/ month</small>
                            </strong>
                            <span>
                              {p.plan_document.allocations
                                .map((a) => channelName[a.channel])
                                .join(" · ")}
                            </span>
                          </div>
                          <details>
                            <summary>Audience, hypothesis & allocation</summary>
                            <PlanDetail document={p.plan_document} />
                          </details>
                          {canEdit && p.state === "approved" && (
                            <DeploymentPreflight
                              key={p.plan_hash}
                              brandId={selected}
                              planId={p.id}
                            />
                          )}
                          {canEdit && (
                            <div className="card-actions">
                              <button
                                className="button"
                                onClick={() => {
                                  setEditing(p);
                                  setModal("plan");
                                }}
                              >
                                Edit revision
                              </button>
                              {p.state === "draft" && (
                                <button
                                  className="button primary"
                                  disabled={busy}
                                  onClick={() =>
                                    mutate(
                                      `/brands/${selected}/plans/${p.id}/submit`,
                                      { expected_hash: p.plan_hash },
                                      "POST",
                                      "Campaign plan sent to review.",
                                    )
                                  }
                                >
                                  Send for review →
                                </button>
                              )}
                            </div>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}
              {tab === "Approvals" && (
                <>
                  <div className="queue-heading">
                    <Badge tone="amber">
                      {pending.length} awaiting decision
                    </Badge>
                    <span>
                      Every decision binds to the exact revision you review.
                    </span>
                  </div>
                  {pending.length === 0 ? (
                    <section className="panel">
                      <Empty title="Nothing waiting on your judgment.">
                        Campaign plans appear here when they are submitted for
                        review.
                      </Empty>
                    </section>
                  ) : (
                    pending.map((a) => (
                      <ApprovalCard
                        key={a.id}
                        approval={a}
                        busy={busy}
                        canApprove={userRoles.some((r) =>
                          (a.state === "pending_client"
                            ? ["client_approver"]
                            : ["owner", "admin", "buyer"]
                          ).includes(r),
                        )}
                        decide={(decision, reason) =>
                          mutate(
                            `/brands/${selected}/approvals/${a.id}/decide`,
                            { decision, reason, expected_hash: a.subject_hash },
                            "POST",
                            decision === "approved"
                              ? "Plan approved. No campaign has been launched."
                              : "Decision recorded with your feedback.",
                          )
                        }
                      />
                    ))
                  )}
                  {workspace.approvals.some((a) => !pending.includes(a)) && (
                    <section className="panel">
                      <h2>Decision history</h2>
                      <div className="history-list">
                        {workspace.approvals
                          .filter((a) => !pending.includes(a))
                          .map((a) => (
                            <div key={a.id}>
                              <span>
                                {a.plan_name}
                                {a.rejection_detail && (
                                  <small>{a.rejection_detail}</small>
                                )}
                              </span>
                              <Badge>
                                {a.is_expired && a.state.startsWith("pending")
                                  ? "Expired"
                                  : human(a.state)}
                              </Badge>
                            </div>
                          ))}
                      </div>
                    </section>
                  )}
                </>
              )}
              {tab === "Activity" && (
                <section className="panel">
                  <div className="panel-title">
                    <h2>Activity ledger</h2>
                    <Badge>Append only</Badge>
                  </div>
                  <Activity items={workspace.audit} expanded />
                  <AuditExport brandId={selected} onError={fail} />
                </section>
              )}
              {tab === "Channels" && (
                <>
                  <div className="message warning">
                    Channel capabilities are loaded from the supplied registry.
                    Live adapters and account connections are not enabled in
                    this build.
                  </div>
                  <div className="channel-grid">
                    {channels.map((c) => (
                      <section className="panel channel-card" key={c.channel}>
                        <div className="channel-monogram">
                          {channelName[c.channel].slice(0, 1)}
                        </div>
                        <h2>{channelName[c.channel]}</h2>
                        <Badge>Not connected</Badge>
                        <p>{c.objectives.map(human).join(" · ")}</p>
                        <details>
                          <summary>Connection prerequisites</summary>
                          <ul>
                            {c.prerequisites.map((p) => (
                              <li key={p}>{human(p)}</li>
                            ))}
                          </ul>
                        </details>
                        <small>Registry {c.registry_version}</small>
                      </section>
                    ))}
                  </div>
                </>
              )}
              {tab === "Guardrails" && (
                <div className="two-column">
                  <section className="panel">
                    <h2>Spend boundaries</h2>
                    <p className="muted">
                      Limits are checked when a plan is submitted and again when
                      it is approved.
                    </p>
                    <form
                      className="form-stack"
                      key={selected}
                      onSubmit={(e) => {
                        e.preventDefault();
                        void mutate(
                          `/brands/${selected}/ceiling`,
                          Object.fromEntries(new FormData(e.currentTarget)),
                          "PUT",
                        );
                      }}
                    >
                      <Field label="Monthly ceiling (USD)">
                        <input
                          type="number"
                          name="monthly_ceiling"
                          min="0.01"
                          step="0.01"
                          required
                          defaultValue={
                            workspace.ceilings.find(
                              (c) => c.scope_kind === "brand",
                            )?.monthly_usd_max
                          }
                        />
                      </Field>
                      <Field label="Daily ceiling (USD)">
                        <input
                          type="number"
                          name="daily_ceiling"
                          min="0.01"
                          step="0.01"
                          required
                          defaultValue={
                            workspace.ceilings.find(
                              (c) => c.scope_kind === "brand",
                            )?.daily_usd_max
                          }
                        />
                      </Field>
                      <button
                        className="button primary"
                        disabled={
                          busy ||
                          !userRoles.some((r) => ["owner", "admin"].includes(r))
                        }
                      >
                        Save boundaries
                      </button>
                    </form>
                  </section>
                  <section className="panel danger-panel">
                    <span className="eyebrow">OPERATIONAL CONTROL</span>
                    <h2>Stop local operations.</h2>
                    <p>
                      Block new plan approvals and invalidate outstanding spend
                      tokens for this brand.
                    </p>
                    <p className="footnote">
                      This build cannot pause ads in platform accounts. Manage
                      any existing live ads directly in the platform.
                    </p>
                    <button
                      className="button danger"
                      disabled={!canEdit || !!workspace.stop}
                      onClick={() => setModal("stop")}
                    >
                      Stop brand operations
                    </button>
                  </section>
                </div>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>ADJUTANT · Every decision accounted for.</span>
            <span>
              {status?.database === "connected"
                ? "Database connected"
                : "Checking database"}{" "}
              <span className="status-dot" />
            </span>
          </footer>
        </main>
      </div>
      {modal && (
        <Dialog
          title={
            modal === "brand"
              ? "Introduce a brand."
              : modal === "stop"
                ? "Stop brand operations"
                : modal === "generate"
                  ? "Give the strategist a brief."
                  : editing
                    ? "Revise the campaign plan."
                    : "Shape the next campaign."
          }
          close={() => {
            setModal(null);
            setEditing(undefined);
          }}
        >
          {error && (
            <p className="message error" role="alert">
              {error}
            </p>
          )}
          {modal === "brand" && (
            <BrandForm accounts={me.accounts} done={formDone} fail={fail} />
          )}
          {modal === "plan" && (
            <PlanForm
              brandId={selected}
              channels={channels}
              existing={editing}
              done={formDone}
              fail={fail}
            />
          )}
          {modal === "generate" && (
            <GenerateForm brandId={selected} done={formDone} />
          )}
          {modal === "stop" && (
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                const data = Object.fromEntries(new FormData(e.currentTarget));
                setModal(null);
                void mutate(
                  `/brands/${selected}/kill`,
                  data,
                  "POST",
                  "Local operations stopped. Review platform accounts separately.",
                );
              }}
            >
              <p>
                This voids outstanding tokens and blocks planning for{" "}
                {brand?.display_name}.
              </p>
              <Field label="Reason for stopping">
                <textarea
                  name="reason"
                  minLength={10}
                  maxLength={1000}
                  required
                  rows={3}
                />
              </Field>
              <button className="button danger" disabled={busy}>
                Confirm stop
              </button>
            </form>
          )}
        </Dialog>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  detail,
  accent = false,
}: {
  label: string;
  value: string;
  detail: string;
  accent?: boolean;
}) {
  return (
    <section className={`stat ${accent ? "accent" : ""}`}>
      <span className="eyebrow">{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </section>
  );
}

function PlanDetail({ document }: { document: Plan["plan_document"] }) {
  return (
    <div className="plan-detail">
      <h3>Audience</h3>
      <p>{document.audience}</p>
      <h3>Hypothesis</h3>
      <p>{document.hypothesis}</p>
      <h3>{human(document.goal_kind)}</h3>
      <p>{document.goal_value}</p>
      <table>
        <thead>
          <tr>
            <th>Channel</th>
            <th>Daily budget</th>
            <th>Monthly budget</th>
          </tr>
        </thead>
        <tbody>
          {document.allocations.map((a) => (
            <tr key={a.channel}>
              <td>{channelName[a.channel]}</td>
              <td>{money(a.daily_budget_usd)}</td>
              <td>{money(a.monthly_budget_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ApprovalCard({
  approval: a,
  busy,
  canApprove,
  decide,
}: {
  approval: Approval;
  busy: boolean;
  canApprove: boolean;
  decide: (decision: string, reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  return (
    <article className="panel approval-card">
      <div className="plan-card-top">
        <div>
          <span className="eyebrow">
            {a.state === "pending_client" ? "CLIENT REVIEW" : "INTERNAL REVIEW"}{" "}
            / CAMPAIGN PLAN
          </span>
          <h2>{a.plan_name}</h2>
        </div>
        <Badge tone="amber">Needs a decision</Badge>
      </div>
      <p>{a.plan_document.rationale}</p>
      <div className="approval-budget">
        <div>
          <span>Daily authority</span>
          <strong>{money(a.requested_daily_usd)}</strong>
        </div>
        <div>
          <span>Total authority</span>
          <strong>{money(a.requested_total_usd)}</strong>
        </div>
        <div>
          <span>Review expires</span>
          <strong className="expiry">{when(a.expires_at)}</strong>
        </div>
      </div>
      <PlanDetail document={a.plan_document} />
      <p className="footnote">
        You are approving this plan revision. Creative review and channel
        deployment are separate steps; this decision does not launch ads.
      </p>
      <Field
        label="Decision feedback"
        hint="Required for rejection or requested changes; at least 10 characters."
      >
        <textarea
          rows={2}
          maxLength={4000}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field>
      <div className="card-actions">
        <button
          className="button"
          disabled={busy || !canApprove || reason.trim().length < 10}
          onClick={() => decide("rejected", reason)}
        >
          Reject
        </button>
        <button
          className="button"
          disabled={busy || !canApprove || reason.trim().length < 10}
          onClick={() => decide("changes_requested", reason)}
        >
          Request changes
        </button>
        <button
          className="button primary"
          disabled={busy || !canApprove}
          onClick={() => decide("approved", reason)}
        >
          Approve plan ✓
        </button>
      </div>
    </article>
  );
}

function Activity({
  items,
  expanded = false,
}: {
  items: Workspace["audit"];
  expanded?: boolean;
}) {
  if (!items.length)
    return (
      <Empty title="A clean slate.">
        Brand and campaign decisions will build a permanent history here.
      </Empty>
    );
  return (
    <ol className="activity-list">
      {items.map((item) => (
        <li key={item.id}>
          <span className="activity-dot" />
          <div>
            <strong>{human(item.action_type)}</strong>
            <p>
              {item.rationale ||
                `${human(item.target_kind)} record updated by ${item.actor_kind}.`}
            </p>
            {expanded && (
              <details>
                <summary>View recorded change</summary>
                <pre>{JSON.stringify(item.diff, null, 2)}</pre>
              </details>
            )}
          </div>
          <time dateTime={item.executed_at}>{when(item.executed_at)}</time>
        </li>
      ))}
    </ol>
  );
}
