"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";

type Provider = {
  credentials_saved: boolean;
  model: string;
  sdk_supported: boolean;
};

export function StudioSettings({
  brandId,
  canManage,
}: {
  brandId: string;
  canManage: boolean;
}) {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [key, setKey] = useState("");
  const [model, setModel] = useState("gemini-3.1-flash-image");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<Provider>(
      `/brands/${brandId}/visual-provider`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((result) => {
        setProvider(result);
        if (result.sdk_supported) setModel(result.model);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) setMessage(error.message);
      });
    return () => controller.abort();
  }, [brandId]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const result = await api<Provider>(
        `/brands/${brandId}/visual-provider`,
        "PUT",
        { model, ...(key ? { api_key: key } : {}) },
      );
      setProvider(result);
      setKey("");
      setMessage(
        "Configuration saved securely. Generation uses it immediately.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Configuration could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <details
      className="panel"
      open={
        provider !== null &&
        (!provider.credentials_saved || !provider.sdk_supported)
      }
    >
      <summary>
        Image generation settings ·{" "}
        {provider?.credentials_saved && provider.sdk_supported
          ? "Credentials saved; generation unverified"
          : "Setup required"}
      </summary>
      <p>
        Use your Google project’s image model and API key. The key is encrypted
        for this brand and is never returned to the browser.
      </p>
      {canManage ? (
        <form className="form-stack" onSubmit={save}>
          <label>
            Gemini API key
            <input
              type="password"
              autoComplete="new-password"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              required={!provider?.credentials_saved}
              minLength={10}
              maxLength={512}
            />
          </label>
          <label>
            Google image model
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              required
              maxLength={120}
            />
          </label>
          <button className="button" disabled={busy}>
            {busy ? "Saving…" : "Save image settings"}
          </button>
        </form>
      ) : (
        <p>A workspace owner or administrator can configure the provider.</p>
      )}
      <p role="status">{message}</p>
    </details>
  );
}

type Understanding = {
  version: number;
  document: {
    offers: string[];
    audience: string;
    voice: string;
    proof_points: string[];
  };
};

export function BrandUnderstandingEditor({
  brandId,
  canEdit,
}: {
  brandId: string;
  canEdit: boolean;
}) {
  const [record, setRecord] = useState<Understanding | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<Understanding | null>(
      `/brands/${brandId}/understanding`,
      "GET",
      undefined,
      controller.signal,
    )
      .then(setRecord)
      .catch((error: Error) => {
        if (!controller.signal.aborted) setMessage(error.message);
      });
    return () => controller.abort();
  }, [brandId]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record) return;
    setBusy(true);
    try {
      setRecord(
        await api<Understanding>(`/brands/${brandId}/understanding`, "PUT", {
          expected_version: record.version,
          document: {
            ...record.document,
            offers: record.document.offers
              .map((value) => value.trim())
              .filter(Boolean),
            proof_points: record.document.proof_points
              .map((value) => value.trim())
              .filter(Boolean),
          },
        }),
      );
      setMessage(
        "Brand understanding saved. Future generation uses these edits.",
      );
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Could not save brand understanding.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Brand understanding{record ? ` · Version ${record.version}` : ""}</h2>
      {record ? (
        <form className="form-stack" onSubmit={save}>
          {(["offers", "audience", "voice", "proof_points"] as const).map(
            (field) => {
              const value = record.document[field];
              return (
                <label key={field}>
                  {field.replaceAll("_", " ")}
                  <textarea
                    aria-label={`Brand understanding ${field}`}
                    rows={3}
                    readOnly={!canEdit || busy}
                    value={Array.isArray(value) ? value.join("\n") : value}
                    onChange={(event) =>
                      setRecord({
                        ...record,
                        document: {
                          ...record.document,
                          [field]: Array.isArray(value)
                            ? event.target.value.split("\n")
                            : event.target.value,
                        },
                      })
                    }
                  />
                </label>
              );
            },
          )}
          {canEdit && (
            <button className="button" disabled={busy}>
              {busy ? "Saving…" : "Save brand understanding"}
            </button>
          )}
        </form>
      ) : (
        <p>
          Generate ads from a URL or prompt to extract the brand’s offers,
          audience, voice, and proof points.
        </p>
      )}
      <p role="status">{message}</p>
    </section>
  );
}
