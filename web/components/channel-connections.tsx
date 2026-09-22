"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type Channel } from "../lib/api";
import { channelName, human, when } from "./ui";

type RemoteAccount = { id: string; name: string };
type Connection = {
  external_ad_account_id: string;
  external_account_name: string;
  health: string;
  health_detail: string | null;
  verified_at: string | null;
  selected: boolean;
  token_expires_at: string | null;
};
type ChannelState = {
  channel: string;
  documentation: string;
  required_fields: string[];
  application_saved: boolean;
  token_saved: boolean;
  redirect_uri: string;
  authorization: {
    authorized_at: string | null;
    discovered_at: string | null;
    accounts: RemoteAccount[];
    last_error: string | null;
    disconnected_at: string | null;
  } | null;
  connections: Connection[];
};

function ChannelConnection({
  brandId,
  capability,
  state,
  canManage,
  refresh,
}: {
  brandId: string;
  capability: Channel;
  state: ChannelState;
  canManage: boolean;
  refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [revocationUrl, setRevocationUrl] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("");
  const prefix = `/brands/${brandId}/channels/${state.channel}`;
  const selected = state.connections.find((connection) => connection.selected);
  const verified =
    selected?.health === "healthy" &&
    selected.verified_at &&
    (!selected.token_expires_at ||
      new Date(selected.token_expires_at).getTime() > Date.now());

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await operation();
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Channel request failed.",
      );
    } finally {
      await refresh().catch((error: Error) => setMessage(error.message));
      setBusy(false);
    }
  }
  async function saveApplication(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    if (!values.client_secret) delete values.client_secret;
    if (!values.developer_token) delete values.developer_token;
    await act(async () => {
      await api(`${prefix}/application`, "PUT", values);
      form.reset();
      setMessage(
        "Application credentials saved encrypted. Authorize access to discover accounts.",
      );
    });
  }
  return (
    <section className="panel channel-card">
      <div className="channel-monogram">
        {channelName[state.channel]?.slice(0, 1)}
      </div>
      <h2>{channelName[state.channel]}</h2>
      <p>
        {verified
          ? `Account access verified ${when(selected.verified_at!)}`
          : state.token_saved
            ? "Authorization received; verify account access"
            : "No account authorization"}
      </p>
      {selected && (
        <p>
          Selected account: {selected.external_account_name} (
          {selected.external_ad_account_id})
        </p>
      )}
      <p className="channel-objectives">
        {capability.objectives.map(human).join(" · ")}
      </p>
      <details>
        <summary>Connection prerequisites</summary>
        <ul>
          {capability.prerequisites.map((item) => (
            <li key={item}>{human(item)}</li>
          ))}
        </ul>
        <p>
          <a href={state.documentation} target="_blank" rel="noreferrer">
            Platform application setup
          </a>
        </p>
        <p>Register this OAuth callback URL:</p>
        <code style={{ overflowWrap: "anywhere" }}>{state.redirect_uri}</code>
      </details>
      {canManage && (
        <>
          <details>
            <summary>
              {state.application_saved
                ? "Update developer application"
                : "Set up developer application"}
            </summary>
            <form className="form-stack" onSubmit={saveApplication}>
              <label>
                Client / application ID
                <input
                  name="client_id"
                  required
                  maxLength={300}
                  disabled={busy}
                />
              </label>
              <label>
                Client secret
                <input
                  name="client_secret"
                  type="password"
                  autoComplete="new-password"
                  required={!state.application_saved}
                  disabled={busy}
                />
              </label>
              {state.required_fields.includes("developer_token") && (
                <label>
                  Developer token
                  <input
                    name="developer_token"
                    type="password"
                    autoComplete="new-password"
                    required={!state.application_saved}
                    disabled={busy}
                  />
                </label>
              )}
              {state.required_fields.includes("region") && (
                <label>
                  Advertising region
                  <select name="region" disabled={busy}>
                    <option value="NA">North America</option>
                    <option value="EU">Europe</option>
                    <option value="FE">Far East</option>
                  </select>
                </label>
              )}
              <button className="button" disabled={busy}>
                Save application
              </button>
            </form>
          </details>
          {state.application_saved && (
            <button
              className="button"
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  const result = await api<{ authorization_url: string }>(
                    `${prefix}/authorize`,
                    "POST",
                  );
                  window.location.assign(result.authorization_url);
                })
              }
            >
              Authorize {channelName[state.channel]}
            </button>
          )}
          {state.token_saved && (
            <>
              <button
                className="button ghost"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await api<{ accounts: RemoteAccount[] }>(
                      `${prefix}/discover`,
                      "POST",
                    );
                    setMessage(
                      `Verified ${result.accounts.length} accessible accounts.`,
                    );
                  })
                }
              >
                Discover accounts
              </button>
              {(state.authorization?.accounts.length ?? 0) > 0 && (
                <form
                  className="form-stack"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act(async () => {
                      await api(`${prefix}/select`, "POST", {
                        account_id: accountId,
                      });
                      setMessage(
                        "Selected account access verified against the platform.",
                      );
                    });
                  }}
                >
                  <label>
                    Advertising account
                    <select
                      value={accountId}
                      onChange={(event) => setAccountId(event.target.value)}
                      required
                      disabled={busy}
                    >
                      <option value="">Select an account</option>
                      {state.authorization?.accounts.map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.name} ({account.id})
                        </option>
                      ))}
                    </select>
                  </label>
                  <button className="button" disabled={busy}>
                    Verify and select account
                  </button>
                </form>
              )}
              <button
                className="button danger"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await api<{
                      message: string;
                      revocation_url: string | null;
                    }>(`${prefix}/authorization`, "DELETE");
                    setMessage(result.message);
                    setRevocationUrl(result.revocation_url);
                  })
                }
              >
                Disconnect from Adjutant
              </button>
            </>
          )}
        </>
      )}
      {state.authorization?.last_error && (
        <p role="alert">{state.authorization.last_error}</p>
      )}
      <p role="status">{message}</p>
      {revocationUrl && (
        <a href={revocationUrl} target="_blank" rel="noreferrer">
          Remove application consent on the platform
        </a>
      )}
      <small>Registry {capability.registry_version}</small>
    </section>
  );
}

export function ChannelConnections({
  brandId,
  channels,
  canManage,
}: {
  brandId: string;
  channels: Channel[];
  canManage: boolean;
}) {
  const [states, setStates] = useState<ChannelState[]>([]);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    const data = await api<ChannelState[]>(`/brands/${brandId}/channels`);
    setStates(data);
    setError("");
  }, [brandId]);
  useEffect(() => {
    const abort = new AbortController();
    api<ChannelState[]>(
      `/brands/${brandId}/channels`,
      "GET",
      undefined,
      abort.signal,
    )
      .then(setStates)
      .catch((error: Error) => {
        if (!abort.signal.aborted) setError(error.message);
      });
    return () => abort.abort();
  }, [brandId]);
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!states.length && !error && <p>Loading account authorization state…</p>}
      <div className="channel-grid">
        {states.map((state) => {
          const capability = channels.find(
            (item) => item.channel === state.channel,
          );
          return capability ? (
            <ChannelConnection
              key={`${brandId}:${state.channel}`}
              brandId={brandId}
              capability={capability}
              state={state}
              canManage={canManage}
              refresh={refresh}
            />
          ) : null;
        })}
      </div>
    </>
  );
}
