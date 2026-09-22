"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Brand, type Plan } from "../lib/api";
import { StudioSettings } from "./studio-settings";

import {
  parseAdBundle,
  parseStudioJob,
  type AdBundle,
  type StudioJob,
} from "../lib/studio";

function Editable({
  label,
  value,
  change,
  limit,
  multiline = false,
  readOnly = false,
}: {
  label: string;
  value: string;
  change: (value: string) => void;
  limit: number;
  multiline?: boolean;
  readOnly?: boolean;
}) {
  const props = {
    "aria-label": label,
    value,
    maxLength: limit,
    readOnly,
    onChange: (
      event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
    ) => change(event.target.value),
    className: "studio-editable",
  };
  return multiline ? <textarea {...props} rows={3} /> : <input {...props} />;
}

export function AdStudio({
  brand,
  canEdit,
  canManage = false,
  plans = [],
  fail,
}: {
  brand: Brand;
  canEdit: boolean;
  canManage?: boolean;
  plans?: Plan[];
  fail: (error: unknown) => void;
}) {
  const [brief, setBrief] = useState(brand.website_url || "");
  const [bundle, setBundle] = useState<AdBundle | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<StudioJob | null>(null);
  const [failedJob, setFailedJob] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [imageError, setImageError] = useState(false);
  const [ratio, setRatio] = useState("1:1");
  const [planId, setPlanId] = useState("");
  const [asset, setAsset] = useState<{
    png_url: string;
    svg_url: string;
  } | null>(null);
  const request = useRef<AbortController | null>(null);
  const loading = useRef(true);

  useEffect(() => {
    const controller = new AbortController();
    api<unknown>(
      `/brands/${brand.id}/studio/jobs/latest`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((raw) => {
        const result = raw === null ? null : parseStudioJob(raw);
        if (result && ["queued", "running"].includes(result.state)) {
          setJob(result);
          setBusy(true);
          loading.current = false;
        } else if (result?.state === "failed") {
          setFailedJob(result.id);
          setError(result.error_message || "Generation failed.");
        }
      })
      .catch((issue: unknown) => {
        if (!controller.signal.aborted) fail(issue);
      });
    api<unknown>(
      `/brands/${brand.id}/studio/latest`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((result) => {
        if (loading.current)
          setBundle(result === null ? null : parseAdBundle(result));
      })
      .catch((issue: unknown) => {
        if (!controller.signal.aborted) fail(issue);
      });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [brand.id, fail]);

  useEffect(() => {
    if (!job) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const current = parseStudioJob(
          await api<unknown>(
            `/brands/${brand.id}/studio/jobs/${job.id}`,
            "GET",
            undefined,
            controller.signal,
          ),
        );
        if (controller.signal.aborted) return;
        if (current.state === "completed" && current.result) {
          setBundle(current.result);
          setDirty(false);
          setImageError(false);
          setAsset(null);
          setBusy(false);
          setJob(null);
          setMessage("Concepts saved. Choose a concept and edit its copy.");
          return;
        }
        if (current.state === "failed" || current.state === "cancelled") {
          setBusy(false);
          setJob(null);
          setError(current.error_message || "Generation stopped.");
          setMessage("");
          setFailedJob(current.state === "failed" ? current.id : null);
          return;
        }
        setMessage(
          current.cancel_requested_at
            ? "Stopping generation…"
            : `Generation ${current.state}. You can leave this view and return.`,
        );
      } catch (issue) {
        if (!controller.signal.aborted)
          setError(
            issue instanceof Error
              ? issue.message
              : "Could not check generation progress.",
          );
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 700);
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [brand.id, job]);

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || saving) return;
    loading.current = false;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    setMessage(
      "Reading the business, writing channel copy, and generating the image…",
    );
    try {
      const result = parseStudioJob(
        await api<unknown>(
          "/studio/jobs",
          "POST",
          {
            brand_id: brand.id,
            url_or_prompt: brief,
            request_key: crypto.randomUUID(),
            concept_count: 5,
          },
          controller.signal,
        ),
      );
      if (!controller.signal.aborted) {
        setJob(result);
      }
    } catch (issue) {
      if (!controller.signal.aborted) {
        setError(
          issue instanceof Error ? issue.message : "Ad generation failed.",
        );
        setMessage("");
        fail(issue);
        setBusy(false);
      }
    }
  }

  function edit(change: (current: AdBundle) => AdBundle) {
    setAsset(null);
    setBundle((current) => (current ? change(current) : null));
    setDirty(true);
    setMessage("Unsaved edits");
  }

  async function selectConcept(id: string) {
    if (busy || saving || dirty) return;
    setSaving(true);
    setError("");
    setFailedJob(null);
    try {
      const value = await api<unknown>(`/brands/${brand.id}/studio/${id}`);
      setBundle(parseAdBundle(value));
      setAsset(null);
      setImageError(false);
      setMessage("");
    } catch (issue) {
      setError(
        issue instanceof Error ? issue.message : "Could not load this concept.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function finishCreative(operation: "render" | "attach") {
    if (!bundle || dirty || busy || saving) return;
    setSaving(true);
    setError("");
    try {
      const result = await api<{ png_url: string; svg_url: string }>(
        `/brands/${brand.id}/studio/${bundle.id}/${operation}`,
        "POST",
        {
          expected_revision: bundle.revision,
          ...(operation === "render"
            ? { aspect_ratio: ratio }
            : { plan_id: planId }),
        },
      );
      if (operation === "render") setAsset(result);
      setMessage(
        operation === "render"
          ? "Finished ad rendered. Download PNG or editable SVG below."
          : "Creative and four rendered formats attached to the campaign plan.",
      );
    } catch (issue) {
      setError(
        issue instanceof Error ? issue.message : "Creative operation failed.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function save() {
    if (!bundle || saving || busy) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setError("");
    try {
      const { image_url: omittedImage, ...meta } = bundle.meta;
      void omittedImage;
      const result = parseAdBundle(
        await api<unknown>(
          `/brands/${brand.id}/studio/${bundle.id}`,
          "PUT",
          {
            expected_revision: bundle.revision,
            brand_name: bundle.brand_name,
            destination_url: bundle.destination_url,
            meta,
            google: bundle.google,
            tiktok: bundle.tiktok,
          },
          controller.signal,
        ),
      );
      if (!controller.signal.aborted) {
        setBundle(result);
        setDirty(false);
        setMessage("Edits saved.");
      }
    } catch (issue) {
      if (!controller.signal.aborted) {
        setError(
          issue instanceof Error ? issue.message : "Edits could not be saved.",
        );
        fail(issue);
      }
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  return (
    <section className="ad-studio" aria-labelledby="studio-title">
      <div className="studio-heading">
        <div>
          <span className="eyebrow">FROM BUSINESS TO CREATIVE</span>
          <h2 id="studio-title">Ad Studio</h2>
        </div>
        <span className="studio-format">STATIC CREATIVE / THREE CHANNELS</span>
      </div>
      <StudioSettings brandId={brand.id} canManage={canManage} />
      {bundle && bundle.concepts.length > 1 && (
        <div
          className="studio-concepts"
          role="group"
          aria-label="Creative concepts"
        >
          {bundle.concepts.map((concept) => (
            <button
              key={concept.id}
              className="button"
              type="button"
              aria-pressed={bundle.id === concept.id}
              disabled={busy || saving || dirty}
              onClick={() => void selectConcept(concept.id)}
            >
              {concept.concept_index + 1}. {concept.name || concept.headline}
            </button>
          ))}
          {dirty && <p>Save your edits before changing concepts.</p>}
        </div>
      )}
      {canEdit && (
        <form onSubmit={generate} className="studio-brief">
          <label htmlFor="studio-input">Business URL or campaign prompt</label>
          <div className="studio-input-row">
            <textarea
              id="studio-input"
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              required
              minLength={3}
              maxLength={10000}
              rows={2}
            />
            <button className="button primary" aria-busy={busy} type="submit">
              {busy ? "Generating…" : "Generate →"}
            </button>
          </div>

          <p className="muted">
            Paste a website or describe the offer. Brand context is read
            automatically; your ads stay editable.
          </p>
        </form>
      )}
      {canEdit && job && (
        <button
          type="button"
          className="button danger"
          onClick={() => {
            void api<StudioJob>(
              `/brands/${brand.id}/studio/jobs/${job.id}`,
              "DELETE",
            )
              .then(() =>
                setMessage(
                  "Cancellation requested; waiting for the generation process to stop…",
                ),
              )
              .catch((issue: Error) => setError(issue.message));
          }}
        >
          Cancel generation
        </button>
      )}
      {canEdit && failedJob && !busy && (
        <button
          type="button"
          className="button"
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const result = await api<unknown>(
                `/brands/${brand.id}/studio/jobs/${failedJob}/retry`,
                "POST",
              );
              setJob(parseStudioJob(result));
              setFailedJob(null);
            } catch (issue) {
              setBusy(false);
              setError(
                issue instanceof Error
                  ? issue.message
                  : "Could not retry generation.",
              );
            }
          }}
        >
          Retry unfinished concepts
        </button>
      )}
      <p className="studio-status" role="status" aria-live="polite">
        {message}
      </p>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {bundle && (
        <>
          <div className="studio-toolbar">
            <span>
              Creative revision {bundle.revision}
              {dirty ? " · Unsaved edits" : " · Saved"}
            </span>
            {canEdit && (
              <button
                className="button"
                onClick={save}
                disabled={saving || busy || !dirty}
              >
                {saving ? "Saving…" : "Save copy edits"}
              </button>
            )}
          </div>
          {canEdit && (
            <div className="panel form-stack">
              <h3>Finish and use this creative</h3>
              <label>
                Rendition format
                <select
                  value={ratio}
                  onChange={(event) => {
                    setRatio(event.target.value);
                    setAsset(null);
                  }}
                >
                  {["1:1", "4:5", "9:16", "16:9"].map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="button"
                disabled={dirty || saving || busy}
                onClick={() => finishCreative("render")}
              >
                Render finished ad
              </button>
              {asset && (
                <div>
                  <img
                    src={asset.png_url}
                    alt={`Finished ${ratio} advertisement for ${bundle.brand_name}`}
                    style={{
                      maxWidth: "100%",
                      maxHeight: 480,
                      objectFit: "contain",
                    }}
                  />
                  <p>
                    <a href={asset.png_url} download>
                      Download PNG
                    </a>
                    {" · "}
                    <a href={asset.svg_url} download>
                      Download editable SVG
                    </a>
                  </p>
                </div>
              )}
              <label>
                Campaign plan
                <select
                  value={planId}
                  onChange={(event) => setPlanId(event.target.value)}
                >
                  <option value="">Select a draft plan</option>
                  {plans
                    .filter((plan) => plan.state === "draft")
                    .map((plan) => (
                      <option key={plan.id} value={plan.id}>
                        {plan.name}
                      </option>
                    ))}
                </select>
              </label>
              <button
                type="button"
                className="button"
                disabled={!planId || dirty || saving || busy}
                onClick={() => finishCreative("attach")}
              >
                Attach creative to plan
              </button>
              {dirty && (
                <p>
                  Save your copy edits before rendering or attaching this
                  revision.
                </p>
              )}
            </div>
          )}
          <div className="studio-cards">
            <article
              className="studio-card meta-preview"
              aria-label="Meta ad preview"
            >
              <div className="studio-channel">01 / META</div>
              <div className="meta-identity">
                <span className="studio-avatar" aria-hidden="true">
                  {bundle.brand_name.slice(0, 1)}
                </span>
                <div>
                  <Editable
                    label="Meta brand name"
                    value={bundle.brand_name}
                    limit={120}
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({ ...current, brand_name: value }))
                    }
                  />
                  <small>Sponsored</small>
                </div>
              </div>
              <div className="meta-body">
                <Editable
                  label="Meta primary copy"
                  value={bundle.meta.primary_text}
                  limit={2000}
                  multiline
                  readOnly={!canEdit || saving}
                  change={(value) =>
                    edit((current) => ({
                      ...current,
                      meta: { ...current.meta, primary_text: value },
                    }))
                  }
                />
              </div>
              {imageError ? (
                <p className="message error">
                  The generated image could not be displayed. Reload this draft.
                </p>
              ) : (
                <img
                  className="meta-image"
                  src={bundle.meta.image_url}
                  alt={`Generated campaign image for ${bundle.brand_name}`}
                  width={1024}
                  height={1024}
                  onError={() => setImageError(true)}
                />
              )}
              <div className="meta-caption">
                <Editable
                  label="Meta headline"
                  value={bundle.meta.headline}
                  limit={40}
                  readOnly={!canEdit || saving}
                  change={(value) =>
                    edit((current) => ({
                      ...current,
                      meta: { ...current.meta, headline: value },
                    }))
                  }
                />
                <Editable
                  label="Meta description"
                  value={bundle.meta.description}
                  limit={100}
                  readOnly={!canEdit || saving}
                  change={(value) =>
                    edit((current) => ({
                      ...current,
                      meta: { ...current.meta, description: value },
                    }))
                  }
                />
                <div className="meta-cta">
                  <Editable
                    label="Meta CTA"
                    value={bundle.meta.cta}
                    limit={40}
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        meta: { ...current.meta, cta: value },
                      }))
                    }
                  />
                </div>
              </div>
            </article>
            <article
              className="studio-card google-preview"
              aria-label="Google ad preview"
            >
              <div className="studio-channel">02 / GOOGLE SEARCH</div>
              <div className="google-copy">
                <strong>Sponsored</strong>
                <label className="studio-field-label">
                  Destination URL
                  <Editable
                    label="Google destination URL"
                    value={bundle.destination_url}
                    limit={2048}
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        destination_url: value,
                      }))
                    }
                  />
                </label>
                <Editable
                  label="Google destination path"
                  value={bundle.google.destination_path}
                  limit={31}
                  readOnly={!canEdit || saving}
                  change={(value) =>
                    edit((current) => ({
                      ...current,
                      google: { ...current.google, destination_path: value },
                    }))
                  }
                />
                <div className="google-headlines">
                  {bundle.google.headlines.map((headline, index) => (
                    <Editable
                      key={index}
                      label={`Google headline ${index + 1}`}
                      value={headline}
                      limit={30}
                      readOnly={!canEdit || saving}
                      change={(value) =>
                        edit((current) => ({
                          ...current,
                          google: {
                            ...current.google,
                            headlines: current.google.headlines.map(
                              (text, i) => (i === index ? value : text),
                            ),
                          },
                        }))
                      }
                    />
                  ))}
                </div>
                {bundle.google.descriptions.map((description, index) => (
                  <Editable
                    key={index}
                    label={`Google description ${index + 1}`}
                    value={description}
                    limit={90}
                    multiline
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        google: {
                          ...current.google,
                          descriptions: current.google.descriptions.map(
                            (text, i) => (i === index ? value : text),
                          ),
                        },
                      }))
                    }
                  />
                ))}
              </div>
            </article>
            <article
              className="studio-card tiktok-preview"
              aria-label="TikTok concept preview"
            >
              <div className="studio-channel">03 / TIKTOK CONCEPT</div>
              <div className="tiktok-copy">
                <span className="tiktok-time">00:00 — 00:03</span>
                <label className="studio-field-label">
                  Opening hook
                  <Editable
                    label="TikTok three-second hook"
                    value={bundle.tiktok.hook}
                    limit={150}
                    multiline
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        tiktok: { ...current.tiktok, hook: value },
                      }))
                    }
                  />
                </label>
                <label className="studio-field-label">
                  Visual script
                  <Editable
                    label="TikTok visual script"
                    value={bundle.tiktok.visual_script}
                    limit={2000}
                    multiline
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        tiktok: { ...current.tiktok, visual_script: value },
                      }))
                    }
                  />
                </label>
                <label className="studio-field-label">
                  Call to action
                  <Editable
                    label="TikTok CTA"
                    value={bundle.tiktok.cta}
                    limit={60}
                    readOnly={!canEdit || saving}
                    change={(value) =>
                      edit((current) => ({
                        ...current,
                        tiktok: { ...current.tiktok, cta: value },
                      }))
                    }
                  />
                </label>
                <small>Script concept · No video generated</small>
              </div>
            </article>
          </div>
        </>
      )}
    </section>
  );
}
