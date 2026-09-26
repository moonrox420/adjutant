import { ApiError } from "./api";

type RecordValue = Record<string, unknown>;
function record(value: unknown): RecordValue {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new ApiError(502, "The campaign response is not a valid object.");
  return value as RecordValue;
}
function text(value: unknown): string {
  if (typeof value !== "string")
    throw new ApiError(502, "The campaign response contains invalid text.");
  return value;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value))
    throw new ApiError(502, "The campaign response is not a valid list.");
  return value;
}
function nullableText(value: unknown) {
  return value === null ? null : text(value);
}
function optionalNumber(value: unknown): number | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value))
    throw new ApiError(
      502,
      "The campaign response contains an invalid number.",
    );
  return value;
}

export function parseDeploymentOptions(value: unknown) {
  const row = record(value);
  const version = optionalNumber(row.guardrail_version);
  if (!version || !Number.isSafeInteger(version))
    throw new ApiError(502, "The guardrail version is invalid.");
  return {
    plan_hash: text(row.plan_hash),
    guardrail_version: version,
    channels: list(row.channels).map((value) => {
      const item = record(value);
      return {
        channel: text(item.channel),
        connection_id: nullableText(item.connection_id),
        external_account_name: nullableText(item.external_account_name),
        fields:
          item.configuration === null
            ? null
            : list(record(item.configuration).fields).map((value) => {
                const field = record(value);
                if (typeof field.required !== "boolean")
                  throw new ApiError(502, "Invalid campaign setup field.");
                const kind = text(field.type);
                if (!["string", "integer", "array"].includes(kind))
                  throw new ApiError(502, "Unsupported campaign setup field.");
                const defaultValue = field.default;
                if (
                  defaultValue !== null &&
                  typeof defaultValue !== "number" &&
                  typeof defaultValue !== "string"
                )
                  throw new ApiError(502, "Invalid campaign setup default.");
                return {
                  name: text(field.name),
                  label: text(field.label),
                  type: kind,
                  format: nullableText(field.format),
                  required: field.required,
                  default: defaultValue,
                  minimum: optionalNumber(field.minimum),
                  maximum: optionalNumber(field.maximum),
                  pattern: nullableText(field.pattern),
                };
              }),
      };
    }),
  };
}
export type DeploymentOptions = ReturnType<typeof parseDeploymentOptions>;

export function parseDeployments(value: unknown) {
  return list(value).map((value) => {
    const row = record(value);
    const state = text(row.state);
    if (!["queued", "running", "paused", "failed", "cancelled"].includes(state))
      throw new ApiError(502, "The campaign execution state is invalid.");
    return {
      id: text(row.id),
      channel: text(row.channel),
      state,
      error_message: nullableText(row.error_message),
      verified_at: nullableText(row.verified_at),
      provider_errors: list(row.provider_errors).map((value) => {
        const error = record(value);
        return {
          raw_code: nullableText(error.raw_code),
          raw_message: text(error.raw_message),
          occurred_at: text(error.occurred_at),
        };
      }),
      objects: list(row.objects).map((value) => {
        const object = record(value);
        return {
          id: text(object.id),
          level: text(object.level),
          native_id: text(object.native_id),
          state: text(object.state),
        };
      }),
      steps: list(row.steps).map((value) => {
        const step = record(value);
        return {
          step_key: text(step.step_key),
          native_id: nullableText(step.native_id),
          verified_at: nullableText(step.verified_at),
        };
      }),
    };
  });
}
export type CampaignBuild = ReturnType<typeof parseDeployments>[number];
