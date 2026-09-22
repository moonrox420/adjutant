import { ApiError } from "./api";

export type MetaCopy = {
  headline: string;
  primary_text: string;
  description: string;
  cta: string;
  image_prompt: string;
  image_url: string;
};

export type AdBundle = {
  id: string;
  brand_id: string;
  revision: number;
  brand_name: string;
  destination_url: string;
  meta: MetaCopy;
  google: {
    headlines: string[];
    descriptions: string[];
    destination_path: string;
  };
  tiktok: { hook: string; visual_script: string; cta: string };
  concepts: {
    id: string;
    name: string | null;
    headline: string;
    revision: number;
    concept_index: number;
  }[];
};

export type StudioJob = {
  id: string;
  state: "queued" | "running" | "completed" | "failed" | "cancelled";
  cancel_requested_at: string | null;
  error_message: string | null;
  result?: AdBundle;
};

function invalid(field: string): never {
  throw new ApiError(
    502,
    `The Studio response has an invalid ${field}. Reload or retry the request.`,
  );
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    invalid(field);
  return value as Record<string, unknown>;
}

function text(value: unknown, field: string): string {
  if (typeof value !== "string") invalid(field);
  return value;
}

function integer(value: unknown, field: string, minimum = 1): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < minimum
  )
    invalid(field);
  return value;
}

function strings(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string"))
    invalid(field);
  return value as string[];
}

export function parseAdBundle(value: unknown): AdBundle {
  const row = record(value, "ad bundle");
  const meta = record(row.meta, "Meta copy");
  const google = record(row.google, "Google copy");
  const tiktok = record(row.tiktok, "TikTok copy");
  const concepts = row.concepts ?? [];
  if (!Array.isArray(concepts)) invalid("concept list");
  const image = text(meta.image_url, "image URL");
  if (!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(image))
    invalid("image URL");
  return {
    id: text(row.id, "draft ID"),
    brand_id: text(row.brand_id, "brand ID"),
    revision: integer(row.revision, "revision"),
    brand_name: text(row.brand_name, "brand name"),
    destination_url: text(row.destination_url, "destination URL"),
    meta: {
      headline: text(meta.headline, "headline"),
      primary_text: text(meta.primary_text, "primary copy"),
      description: text(meta.description, "description"),
      cta: text(meta.cta, "call to action"),
      image_prompt: text(meta.image_prompt, "image prompt"),
      image_url: image,
    },
    google: {
      headlines: strings(google.headlines, "Google headlines"),
      descriptions: strings(google.descriptions, "Google descriptions"),
      destination_path: text(google.destination_path, "destination path"),
    },
    tiktok: {
      hook: text(tiktok.hook, "hook"),
      visual_script: text(tiktok.visual_script, "visual script"),
      cta: text(tiktok.cta, "TikTok call to action"),
    },
    concepts: concepts.map((item) => {
      const concept = record(item, "concept");
      return {
        id: text(concept.id, "concept ID"),
        name: concept.name === null ? null : text(concept.name, "concept name"),
        headline: text(concept.headline, "concept headline"),
        revision: integer(concept.revision, "concept revision"),
        concept_index: integer(concept.concept_index, "concept position", 0),
      };
    }),
  };
}

export function parseStudioJob(value: unknown): StudioJob {
  const row = record(value, "generation job");
  const state = text(row.state, "job state");
  if (
    !["queued", "running", "completed", "failed", "cancelled"].includes(state)
  )
    invalid("job state");
  return {
    id: text(row.id, "job ID"),
    state: state as StudioJob["state"],
    cancel_requested_at:
      row.cancel_requested_at == null
        ? null
        : text(row.cancel_requested_at, "cancellation time"),
    error_message:
      row.error_message == null
        ? null
        : text(row.error_message, "error message"),
    ...(row.result ? { result: parseAdBundle(row.result) } : {}),
  };
}
