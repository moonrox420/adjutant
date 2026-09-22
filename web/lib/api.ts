export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Adjutant-Client": "console",
    },
    ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      response.status,
      body?.error?.message || `Request failed (${response.status}).`,
    );
  return body as T;
}

export type Account = {
  id: string;
  display_name: string;
  account_type: string;
};
export type Seat = {
  account_id: string;
  brand_id: string | null;
  role: string;
};
export type Me = {
  id: string;
  email: string;
  accounts: Account[];
  seats: Seat[];
};
export type Brand = {
  id: string;
  account_id: string;
  display_name: string;
  website_url: string;
  vertical: string;
  brand_graph_confirmed_at: string | null;
  campaigns_enabled: boolean;
  monthly_ceiling: string;
  spend_last_30d: string;
  pending_approvals: number;
  active_objects: number;
  stopped: boolean;
};
export type Allocation = {
  channel: string;
  monthly_budget_usd: string;
  daily_budget_usd: string;
};
export type PlanDocument = {
  name: string;
  objective: string;
  goal_kind: string;
  goal_value: string;
  monthly_budget_usd: string;
  rationale: string;
  audience: string;
  hypothesis: string;
  allocations: Allocation[];
  revision?: number;
};
export type Plan = {
  id: string;
  name: string;
  state: string;
  plan_hash: string;
  plan_document: PlanDocument;
  objective: string;
  monthly_budget_usd: string;
  rationale: string;
  created_at: string;
};
export type Approval = {
  id: string;
  subject_id: string;
  subject_hash: string;
  state: string;
  plan_name: string;
  plan_document: PlanDocument;
  requested_daily_usd: string;
  requested_total_usd: string;
  expires_at: string;
  is_expired: boolean;
  rejection_detail: string | null;
};
export type Assertion = {
  id: string;
  field_path: string;
  value: string;
  provenance_uri: string | null;
  human_confirmed_at: string | null;
};
export type Audit = {
  id: string;
  action_type: string;
  rationale: string | null;
  executed_at: string;
  target_kind: string;
  actor_kind: string;
  diff: unknown;
};
export type Workspace = {
  connections: {
    channel: string;
    external_account_name: string;
    verified_at: string | null;
    health: string;
    selected: boolean;
    token_expires_at: string | null;
  }[];
  brand: Brand;
  assertions: Assertion[];
  plans: Plan[];
  approvals: Approval[];
  audit: Audit[];
  ceilings: {
    scope_kind: string;
    monthly_usd_max: string;
    daily_usd_max: string;
  }[];
  stop: { reason: string } | null;
};
export type Channel = {
  channel: string;
  registry_version: string;
  objectives: string[];
  prerequisites: string[];
};
export type Status = {
  database: string;
  approval_signing: string;
  live_channel_writes: boolean;
  outbox_pending: number;
};
