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
} from "../lib/api";
import { AccountAccess } from "../components/account";
import { AccountSettings } from "../components/account-settings";
import { Jobs } from "../components/jobs";
import { AuditExport } from "../components/audit-export";
import { ChannelConnections } from "../components/channel-connections";
import { AdStudio } from "../components/ad-studio";
import { BrandUnderstandingEditor } from "../components/studio-settings";
import { BrandForm, PlanForm } from "../components/forms";
import { GenerateForm, Research } from "../components/research";
import { DeploymentPreflight } from "../components/preflight";
import { GuardrailEditor, LaunchReview } from "../components/autonomy";
import { RemoteStopReport } from "../components/remote-stop";
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
  "Launch review",
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
  const callbackBrand = useRef<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("channels")) {
      callbackBrand.current = params.get("brand");
      setTab("Channels");
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);
  const [modal, setModal] = useState<
    "brand" | "plan" | "stop" | "generate" | null
  >(null);
  const [editing, setEditing] = useState<Plan | undefined>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopRevision, setStopRevision] = useState(0);
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
    const requested = callbackBrand.current;
    callbackBrand.current = null;
    setSelected((current) => {
      if (requested && list.some((brand) => brand.id === requested))
        return requested;
      return list.some((brand) => brand.id === current)
        ? current
        : list[0]?.id || "";
    });
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
      if (path.endsWith("/kill")) setStopRevision((value) => value + 1);
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
                {me.accounts
                  .filter((account) =>
                    me.seats.some(
                      (seat) =>
                        seat.account_id === account.id &&
                        seat.brand_id === null &&
                        ["owner", "admin"].includes(seat.role),
                    ),
                  )
                  .map((account) => (
                    <AccountSettings
                      key={account.id}
                      account={account}
                      saved={refresh}
                    />
                  ))}
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
            {selected && (
              <button
                className="button danger compact"
                disabled={!canEdit || busy}
                onClick={() => setModal("stop")}
              >
                Pause everything
              </button>
            )}
          </div>
        </header>
        <main id="main" className="main-content">
          {workspace?.stop && (
            <RemoteStopReport brandId={selected} refreshToken={stopRevision} />
          )}
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
                    "Launch review":
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
                  onClick={() =>
                    document.getElementById("studio-input")?.focus()
                  }
                >
                  Generate draft ↗
                </button>
                <button
                  className="button primary"
                  onClick={() => {
                    setEditing(undefined);
                    setModal("plan");
                  }}
                >
                  + New campaign plan
                </button>
                <button
                  className="button quiet"
                  onClick={() => setModal("generate")}
                >
                  Generate strategy plan
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
              Review the remote pause report for each platform’s confirmed state
              or failure.
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
                      label="SELECTED AD ACCOUNTS"
                      value={String(
                        workspace.connections.filter((c) => c.selected).length,
                      )}
                      detail="Accounts selected in Channels"
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
                      detail="Remote objects recorded as active"
                    />
                  </div>
                  <div className="overview-grid">
                    <section className="panel next-step">
                      <span className="eyebrow">THE NEXT CONSIDERED MOVE</span>
                      <h2>Turn your business into your next ad.</h2>
                      <p>
                        Paste a URL or describe your offer in Ad Studio. Get
                        channel copy and a generated image, then edit every word
                        in place.
                      </p>
                      <button
                        className="button primary"
                        onClick={() => setTab("Campaign plans")}
                      >
                        Open Ad Studio →
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
                        <span
                          className={workspace?.plans.length ? "current" : ""}
                        >
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
                        <Badge
                          tone={
                            status?.approval_signing === "ready" &&
                            status.live_channel_writes
                              ? "green"
                              : "amber"
                          }
                        >
                          {status?.approval_signing === "ready" &&
                          status.live_channel_writes
                            ? "Ready"
                            : "Setup incomplete"}
                        </Badge>
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
                              ? "Context available"
                              : "Context not yet generated"}
                          </Badge>
                        </li>
                        <li>
                          <span>Budget boundaries</span>
                          <Badge
                            tone={
                              workspace.ceilings.some(
                                (limit) =>
                                  limit.scope_kind === "brand" &&
                                  Number(limit.monthly_usd_max) > 0 &&
                                  Number(limit.daily_usd_max) > 0,
                              )
                                ? "green"
                                : "amber"
                            }
                          >
                            {workspace.ceilings.some(
                              (limit) =>
                                limit.scope_kind === "brand" &&
                                Number(limit.monthly_usd_max) > 0 &&
                                Number(limit.daily_usd_max) > 0,
                            )
                              ? "Limits saved"
                              : "Limits required"}
                          </Badge>
                        </li>
                        <li>
                          <span>Approval authority</span>
                          <Badge
                            tone={
                              status?.approval_signing === "ready"
                                ? "green"
                                : "amber"
                            }
                          >
                            {status?.approval_signing === "ready"
                              ? "Ready"
                              : "Unavailable"}
                          </Badge>
                        </li>
                        <li>
                          <span>Ad platform connections</span>
                          <Badge>
                            {
                              (workspace.connections ?? []).filter(
                                (connection) =>
                                  connection.selected &&
                                  connection.health === "healthy" &&
                                  connection.verified_at &&
                                  (!connection.token_expires_at ||
                                    new Date(
                                      connection.token_expires_at,
                                    ).getTime() > Date.now()),
                              ).length
                            }{" "}
                            accounts verified
                          </Badge>
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
                  <BrandUnderstandingEditor
                    key={`understanding-${selected}`}
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
                            ? "Context available"
                            : "No generated context"}
                        </Badge>
                      </div>
                      {!workspace.assertions.length ? (
                        <Empty title="Build from evidence.">
                          Add your offers, audience, voice, and differentiators
                          in your own words. Sources are optional.
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
                                  {f.human_confirmed_at ? "Confirmed" : "Added"}
                                </Badge>
                              </div>
                              <p>{f.value}</p>
                              {f.provenance_uri && (
                                <a
                                  href={f.provenance_uri}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  {f.provenance_uri} ↗
                                </a>
                              )}
                            </article>
                          ))}
                        </div>
                      )}
                    </section>
                    <section className="panel">
                      <h2>Add a brand fact</h2>
                      <p className="muted">
                        Describe your offers and audience in your own words.
                        These details guide future copy generation.
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
                        <button
                          className="button primary"
                          disabled={busy || !canEdit}
                        >
                          Save brand fact →
                        </button>
                      </form>
                    </section>
                  </div>
                </>
              )}
              {tab === "Campaign plans" && (
                <section className="panel">
                  <AdStudio
                    key={workspace.brand.id}
                    brand={workspace.brand}
                    canEdit={canEdit}
                    canManage={userRoles.some((role) =>
                      ["owner", "admin"].includes(role),
                    )}
                    plans={workspace.plans}
                    fail={fail}
                  />
                  {!workspace.plans.length ? (
                    <Empty title="Your next campaign starts here.">
                      Create ads above, or add a campaign plan with a testable
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
                          {canEdit && (
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
                              <button
                                className="button"
                                onClick={() => setTab("Launch review")}
                              >
                                Review first-launch scope
                              </button>
                            </div>
                          )}
                        </article>
                      ))}
                    </div>
                  )}
                </section>
              )}
              {tab === "Launch review" && (
                <section className="panel">
                  <h2>First launch by channel account</h2>
                  {workspace.plans.length === 0 ? (
                    <Empty title="Create a campaign plan first.">
                      Review its audience, budget, and finished creative before
                      authorizing first launch.
                    </Empty>
                  ) : (
                    workspace.plans.map((plan) => (
                      <article className="plan-card" key={plan.id}>
                        <h2>{plan.name}</h2>
                        <PlanDetail document={plan.plan_document} />
                        <LaunchReview
                          brandId={selected}
                          plan={plan}
                          canApprove={userRoles.some((role) =>
                            ["owner", "admin", "client_approver"].includes(
                              role,
                            ),
                          )}
                        />
                      </article>
                    ))
                  )}
                </section>
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
                <ChannelConnections
                  key={selected}
                  brandId={selected}
                  channels={channels}
                  canManage={userRoles.some((role) =>
                    ["owner", "admin"].includes(role),
                  )}
                />
              )}
              {tab === "Guardrails" && (
                <div className="two-column">
                  <GuardrailEditor
                    key={selected}
                    brandId={selected}
                    canManage={userRoles.some((role) =>
                      ["owner", "admin"].includes(role),
                    )}
                  />
                  <section className="panel danger-panel">
                    <span className="eyebrow">OPERATIONAL CONTROL</span>
                    <h2>Pause managed campaigns.</h2>
                    <p>
                      Stop local work, invalidate outstanding spend tokens, and
                      request verified pauses from each managed campaign's
                      platform.
                    </p>
                    <p className="footnote">
                      A failed platform request remains unverified in the pause
                      report. Releasing the local stop does not resume remote
                      campaigns.
                    </p>
                    <button
                      className="button danger"
                      disabled={
                        busy ||
                        (workspace.stop
                          ? !userRoles.some((role) =>
                              ["owner", "admin"].includes(role),
                            )
                          : !canEdit)
                      }
                      onClick={() =>
                        workspace.stop
                          ? mutate(
                              `/brands/${selected}/resume`,
                              {
                                reason:
                                  "Owner resumed local brand operations from the console.",
                              },
                              "POST",
                              "Local operations resumed.",
                            )
                          : setModal("stop")
                      }
                    >
                      {workspace.stop
                        ? "Resume local operations"
                        : "Stop brand operations"}
                    </button>
                    {workspace.stop && (
                      <button
                        className="button danger"
                        disabled={!canEdit || busy}
                        onClick={() => setModal("stop")}
                      >
                        Retry platform pauses
                      </button>
                    )}
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
                  "Stop requested. Review the per-campaign pause report.",
                );
              }}
            >
              <p>
                Stop local work for {brand?.display_name} and pause its managed
                campaigns across connected platforms. The report shows each
                verified result and every unconfirmed pause.
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
