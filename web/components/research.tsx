"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../lib/api";
import { Field, when } from "./ui";

type Evidence = {
  id: string;
  source_url: string;
  title: string;
  text_content: string;
  fetched_at: string;
  content_hash: string;
};

export function Research({
  brandId,
  canEdit,
}: {
  brandId: string;
  canEdit: boolean;
}) {
  const [items, setItems] = useState<Evidence[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(
    async () => setItems(await api<Evidence[]>(`/brands/${brandId}/evidence`)),
    [brandId],
  );
  useEffect(() => {
    let active = true;
    api<Evidence[]>(`/brands/${brandId}/evidence`)
      .then((data) => {
        if (active) setItems(data);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [brandId]);
  async function research() {
    setBusy(true);
    setError("");
    try {
      await api(`/brands/${brandId}/research`, "POST");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Website import failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Website evidence</h2>
        <button
          className="button compact"
          disabled={busy || !canEdit}
          onClick={research}
        >
          {busy ? "Reading website…" : "Import website ↗"}
        </button>
      </div>
      <p className="muted">
        Read your public homepage, then turn its evidence into sourced facts.
        Imported text remains unconfirmed.
      </p>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {!items.length && (
        <p className="footnote">No website evidence imported yet.</p>
      )}
      {items.map((item) => (
        <details className="evidence" key={item.id}>
          <summary>
            {item.title || item.source_url} · {when(item.fetched_at)}
          </summary>
          <a href={item.source_url} target="_blank" rel="noreferrer">
            Source page ↗
          </a>
          <pre>{item.text_content}</pre>
          <small>Source SHA-256: {item.content_hash}</small>
        </details>
      ))}
    </section>
  );
}

export function GenerateForm({
  brandId,
  done,
}: {
  brandId: string;
  done: () => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [provider, setProvider] = useState<"local" | "cloud" | "">("");
  const [loadingModels, setLoadingModels] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<{ default_provider: "local" | "cloud" }>("/model-providers")
      .then((data) => {
        if (active) setProvider(data.default_provider);
      })
      .catch((e) => {
        if (active) {
          setError(e.message);
          setLoadingModels(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!provider) return;
    let active = true;
    api<{ models: string[]; configured_model: string }>(
      `/models?provider=${provider}`,
    )
      .then((data) => {
        if (active) {
          setModels(data.models);
          setSelectedModel(
            data.models.includes(data.configured_model)
              ? data.configured_model
              : data.models[0] || "",
          );
          if (!data.models.length)
            setError("No generation models are available from this provider.");
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoadingModels(false);
      });
    return () => {
      active = false;
    };
  }, [provider]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(
        `/brands/${brandId}/generate-plan`,
        "POST",
        Object.fromEntries(new FormData(event.currentTarget)),
      );
      done();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form-stack" onSubmit={submit}>
      <p className="muted">
        Your selected model uses confirmed brand facts and current budget
        ceilings to propose a draft for your review.
      </p>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      <Field label="Inference provider">
        <select
          name="provider"
          required
          value={provider}
          disabled={busy || !provider}
          onChange={(event) => {
            setModels([]);
            setSelectedModel("");
            setError("");
            setLoadingModels(true);
            setProvider(event.target.value as "local" | "cloud");
          }}
        >
          {!provider && <option value="">Loading configuration…</option>}
          <option value="local">Local Ollama · this computer</option>
          <option value="cloud">Ollama Cloud · remote inference</option>
        </select>
      </Field>
      {provider === "cloud" && (
        <p className="footnote">
          The brief and confirmed brand facts will be sent to Ollama Cloud when
          you generate.
        </p>
      )}
      <Field
        label={provider === "cloud" ? "Cloud model" : "Installed local model"}
      >
        <select
          name="model"
          required
          disabled={busy || loadingModels || !models.length}
          value={selectedModel}
          onChange={(event) => setSelectedModel(event.target.value)}
        >
          {!models.length && (
            <option value="">
              {loadingModels ? "Loading models…" : "No models available"}
            </option>
          )}
          {models.map((model) => (
            <option key={model}>{model}</option>
          ))}
        </select>
      </Field>
      <Field
        label="Campaign brief"
        hint="Describe the offer, goal, audience, and channels you want to consider."
      >
        <textarea
          name="brief"
          required
          minLength={10}
          maxLength={3000}
          rows={4}
          disabled={busy}
        />
      </Field>
      <button
        className="button primary"
        disabled={busy || loadingModels || !models.length}
      >
        {busy ? "Generating a considered draft…" : "Generate campaign draft →"}
      </button>
      {busy && (
        <button
          className="button"
          type="button"
          onClick={async () => {
            try {
              const jobs =
                await api<
                  { id: string; brand_id: string; finished_at: string | null }[]
                >("/jobs");
              const active = jobs.find(
                (job) => job.brand_id === brandId && !job.finished_at,
              );
              if (active) {
                const result = await api<{ cancellation_verified: boolean }>(
                  `/jobs/${active.id}/cancel`,
                  "POST",
                );
                setError(
                  result.cancellation_verified
                    ? "Generation stopped. Worker process exit verified."
                    : "Stop requested. Check Activity for exit verification.",
                );
              } else {
                setError(
                  "No active job was found. Check Activity for its latest status.",
                );
              }
            } catch (e) {
              setError(
                e instanceof Error ? e.message : "Unable to stop generation.",
              );
            }
          }}
        >
          Stop generation
        </button>
      )}
      <p className="footnote">
        Generation may take several minutes. The result is a draft and cannot
        launch a campaign.
      </p>
    </form>
  );
}
